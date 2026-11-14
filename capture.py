"""Capture: what people say becomes evidence, in the background.

A message is gated and stored by ``ledger.ingest`` on a thread, so it never
delays the reply. Messaging platforms are captured from
``post_gateway_admission``, which Hermes fires only for an admitted sender.
The terminal is captured from the once-per-turn ``pre_llm_call`` hook. The
agent's own answers are never captured.
"""

from __future__ import annotations

import contextvars
import logging
import os
import threading
import time

from . import ledger, state
from .client import PluginError

logger = logging.getLogger(__name__)

_TERMINAL = ("", "cli", "local", "terminal")
_SEEN_CAP = 4000

# Sessions spawned by delegate/subagent runs: their "user messages" are prompts.
_SUBAGENT_SESSIONS: set[str] = set()


def note_subagent_started(child_session_id) -> None:
    if child_session_id:
        _SUBAGENT_SESSIONS.add(str(child_session_id))


def note_subagent_stopped(child_session_id) -> None:
    _SUBAGENT_SESSIONS.discard(str(child_session_id or ""))


def _in_cron(ctx) -> bool:
    current = getattr(ctx, "current_cron_execution", None)
    if callable(current):
        try:
            if current() is not None:
                return True
        except Exception:
            pass
    return os.environ.get("HERMES_CRON_SESSION") == "1"


def is_subagent(session_id) -> bool:
    return str(session_id or "") in _SUBAGENT_SESSIONS


def human_message(kwargs: dict, ctx) -> str:
    """The turn's message when a person wrote it, otherwise "".

    Scheduled runs, kanban workers and subagent sessions carry prompts, not
    speech. Grounding and terminal capture both skip them.
    """
    message = kwargs.get("user_message")
    if not isinstance(message, str):
        return ""
    text = message.strip()
    if not text or is_subagent(kwargs.get("session_id")):
        return ""
    if os.environ.get("HERMES_KANBAN_TASK") or _in_cron(ctx):
        return ""
    return text


def _field(source, name, default=""):
    if isinstance(source, dict):
        return source.get(name, default)
    return getattr(source, name, default)


def _platform(value) -> str:
    value = _field(value, "platform", value) if not isinstance(value, str) else value
    return str(getattr(value, "value", value) or "").strip().lower()


def terminal_payload(kwargs: dict, ctx, settings: dict) -> dict | None:
    """A terminal message, from ``pre_llm_call``. Messaging platforms use the gateway hook."""
    if _platform(kwargs.get("platform")) not in _TERMINAL:
        return None
    text = human_message(kwargs, ctx)
    if len(text) < 3 or text.startswith("/"):
        return None
    return {
        "platform": "terminal",
        "chat_id": str(kwargs.get("session_id") or ""),
        # 9. the turn id, so saying the same thing later is a new message
        "message_id": str(kwargs.get("turn_id") or ""),
        "speaker": settings.get("owner_name") or "terminal",
        "text": text,
        "url": "",
    }


def gateway_payload(kwargs: dict, settings: dict) -> dict | None:
    """An admitted message, from ``post_gateway_admission``. Bots are not speech."""
    text = str(kwargs.get("text") or "").strip()
    source = kwargs.get("source")
    if len(text) < 3 or text.startswith("/") or source is None or _field(source, "is_bot", False):
        return None
    platform = _platform(kwargs.get("platform")) or _platform(source)
    chat_id = str(_field(source, "chat_id") or "")
    message_id = str(kwargs.get("message_id") or _field(source, "message_id") or "")
    base = str(settings.get("permalink_base") or "").rstrip("/")
    url = ""
    if base and platform == "slack" and chat_id and message_id:
        url = f"{base}/archives/{chat_id}/p{message_id.replace('.', '')}"
    return {
        "platform": platform or "chat",
        "chat_id": chat_id,
        "message_id": message_id,
        "speaker": str(_field(source, "user_name") or _field(source, "user_id") or platform or "unknown"),
        "text": text,
        "url": url,
    }


def _first_sight(payload: dict) -> bool:
    """False for a retry or a re-sent message that was already handled.

    A message with no id (a terminal turn on a Hermes that passes no turn id)
    cannot be a retry, so it is always new.
    """
    if not payload["message_id"]:
        return True
    key = f"{payload['platform']}:{payload['chat_id']}:{payload['message_id']}"
    first = []

    def mark(stored: dict) -> None:
        seen = stored.setdefault("capture_seen", {})
        if key in seen:
            return
        first.append(True)
        seen[key] = time.time()
        if len(seen) > _SEEN_CAP:
            stored["capture_seen"] = dict(sorted(seen.items(), key=lambda item: item[1])[-_SEEN_CAP // 2 :])

    state.change_state(mark)
    return bool(first)


def run(payload: dict, ctx, settings: dict, service_factory) -> None:
    """Gate, extract, publish. Never raises: a failure becomes a warning on the next reply."""
    try:
        if not _first_sight(payload):
            state.journal("capture-skipped", reason="already handled", platform=payload["platform"])
            return
        llm = getattr(ctx, "llm", None)
        if llm is None:
            state.journal("capture-skipped", reason="no host model to gate the message")
            return
        meta = {
            "source": payload["speaker"],
            "retrieved_via": "utterance",
            "title": f"{payload['speaker']}, {time.strftime('%Y-%m-%d', time.gmtime())}, {payload['platform']}",
            "url": payload["url"],
        }
        result = ledger.ingest(service_factory(settings), llm, settings, text=payload["text"], meta=meta, message=True)
        if result["stored"]:
            state.journal(
                "captured",
                speaker=payload["speaker"],
                platform=payload["platform"],
                evidence=result["evidence_id"],
                claims=len(result["claims"]),
                linked=len(result["linked_existing_claims"]),
            )
        else:
            state.journal("capture-skipped", reason=result["reason"], platform=payload["platform"])
    except PluginError as exc:
        state.journal("capture-error", error=str(exc))
        state.warn(f"Paranoy is unreachable ({exc}). An earlier message was not recorded.")
    except Exception as exc:  # the host model or local state, not the ledger
        logger.warning("paranoy capture failed: %s", exc, exc_info=True)
        state.journal("capture-error", error=f"{type(exc).__name__}: {exc}")
        state.warn("Paranoy capture failed before reaching the ledger. An earlier message was not recorded.")


def start(payload: dict, ctx, settings: dict, service_factory) -> None:
    # Hermes binds the profile secret scope in a contextvar; a bare Thread starts with an
    # empty context, so get_secret raises under the multiplexing gateway. Carry it over.
    context = contextvars.copy_context()
    threading.Thread(
        target=context.run, args=(run, payload, ctx, settings, service_factory), daemon=True, name="paranoy-capture"
    ).start()
