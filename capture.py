"""Conversation capture: chat messages become standalone ledger records.

Two paths. Human messages are gated and filed at ``pre_gateway_dispatch``,
off the turn's critical path. The assistant's final answer is handled inside
``llm_execution`` middleware: contestable world-claims are extracted and
published, and the answer is rewritten once against claims retrieved from the
collection.

Capture-first: the plugin publishes standalone evidence and claims. It does
not create links or contradictions, and retrieval reads claims (with the
evidence quotes already linked to them) only. The ledger being unreachable
never breaks a turn: the answer the user sees carries a visible warning line
instead.

See ``CAPTURE.md`` in the task artifacts for the settled design.
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import threading

from . import gates, runtime, state
from .client import PluginError
from .runtime import now_iso

logger = logging.getLogger(__name__)

WARNING = "_(paranoy ledger unreachable — this answer was not checked against the collection.)_"

_CAPTURE_KEYS = (
    "capture_enabled",
    "capture_extract",
    "capture_rewrite",
    "capture_platforms",
    "capture_collection",
    "capture_permalink_base",
    "capture_max_claims",
    "capture_retrieval_limit",
    "capture_dupe_distance",
    "capture_terminal_source",
)

_DEFAULT_PLATFORMS = "slack,terminal"
_SEEN_CAP = 4000
_GATE_TEXT_CAP = 20000

_CONFIG_CACHE: dict = {"at": 0.0, "value": {}}
_CONFIG_TTL = 15.0


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def _hermes_plugin_entry() -> dict:
    """``plugins.entries.paranoy`` from Hermes config.yaml, briefly cached."""
    import time

    now = time.time()
    if _CONFIG_CACHE["value"] and now - _CONFIG_CACHE["at"] < _CONFIG_TTL:
        return _CONFIG_CACHE["value"]
    entry: dict = {}
    try:
        from hermes_cli.config import load_config

        config = load_config() or {}
        plugins = config.get("plugins") or {}
        entries = plugins.get("entries") or {}
        found = entries.get("paranoy")
        if isinstance(found, dict):
            entry = found
    except Exception:
        entry = {}
    _CONFIG_CACHE["at"] = now
    _CONFIG_CACHE["value"] = entry
    return entry


def _raw_setting(ctx, key: str, default):
    """Plugin-local override, then ctx.get_config (Hermes >= 0.21), then
    ``plugins.entries.paranoy`` in config.yaml, then the default."""
    overrides = state.load_state().get("capture_settings") or {}
    if key in overrides:
        return overrides[key]
    if ctx is not None:
        try:
            get = getattr(ctx, "get_config", None)
            if callable(get):
                value = get(key, default=default)
                if value is not None:
                    return value
        except Exception:
            pass
    entry = _hermes_plugin_entry()
    if key in entry:
        return entry[key]
    return default


_BOOL_KEYS = ("capture_enabled", "capture_extract", "capture_rewrite")
_INT_KEYS = ("capture_max_claims", "capture_retrieval_limit")
_FLOAT_KEYS = ("capture_dupe_distance",)


def validate_capture_setting(key: str, value) -> object:
    """Coerce and check one capture setting. Raises PluginError on bad input."""
    if key not in _CAPTURE_KEYS:
        raise PluginError(
            "Unknown capture setting. Keys: " + ", ".join(_CAPTURE_KEYS)
        )
    if key in _BOOL_KEYS:
        if isinstance(value, bool):
            return value
        lowered = str(value).strip().lower()
        if lowered in {"on", "true", "1"}:
            return True
        if lowered in {"off", "false", "0", ""}:
            return False
        raise PluginError(f"{key} takes on or off.")
    if key in _INT_KEYS:
        try:
            return max(1, int(value))
        except (TypeError, ValueError):
            raise PluginError(f"{key} takes a number.")
    if key in _FLOAT_KEYS:
        try:
            number = float(value)
        except (TypeError, ValueError):
            raise PluginError(f"{key} takes a number between 0 and 1.")
        if not 0.0 <= number <= 1.0:
            raise PluginError(f"{key} takes a number between 0 and 1.")
        return number
    return str(value).strip()


def set_capture_setting(key: str, value) -> object:
    """``/paranoy capture`` and the agent tool persist here. Returns the stored value."""
    stored_value = validate_capture_setting(key, value)
    stored = state.load_state()
    overrides = dict(stored.get("capture_settings") or {})
    overrides[key] = stored_value
    state.save_state({**stored, "capture_settings": overrides})
    _CONFIG_CACHE["at"] = 0.0
    return stored_value


def read_capture_config(ctx) -> dict:
    get = lambda key, default: _raw_setting(ctx, key, default)  # noqa: E731
    platforms_raw = str(get("capture_platforms", _DEFAULT_PLATFORMS) or _DEFAULT_PLATFORMS)
    platforms = {part.strip().lower() for part in platforms_raw.split(",") if part.strip()}
    try:
        max_claims = max(1, int(get("capture_max_claims", 3)))
    except (TypeError, ValueError):
        max_claims = 3
    try:
        retrieval_limit = max(1, min(50, int(get("capture_retrieval_limit", 8))))
    except (TypeError, ValueError):
        retrieval_limit = 8
    try:
        dupe_distance = float(get("capture_dupe_distance", 0.15))
    except (TypeError, ValueError):
        dupe_distance = 0.15
    dupe_distance = min(max(dupe_distance, 0.0), 1.0)
    return {
        "enabled": gates.as_bool(get("capture_enabled", False), False),
        "extract": gates.as_bool(get("capture_extract", True), True),
        "rewrite": gates.as_bool(get("capture_rewrite", True), True),
        "platforms": platforms,
        "collection": str(get("capture_collection", "") or "").strip(),
        "permalink_base": str(get("capture_permalink_base", "") or "").strip().rstrip("/"),
        "max_claims": max_claims,
        "retrieval_limit": retrieval_limit,
        "dupe_distance": dupe_distance,
        "terminal_source": str(get("capture_terminal_source", "") or "").strip(),
    }


def capture_status_text(ctx, settings: dict) -> str:
    cfg = read_capture_config(ctx)
    collection = cfg["collection"] or settings.get("collection") or ""
    platforms = ", ".join(sorted(cfg["platforms"])) or "(none)"
    mode = settings.get("mode") or "reader"
    ready = [
        f"capture: {'on' if cfg['enabled'] else 'off'}",
        f"collection: {collection or '(unset — capture will not run)'}",
        f"mode: {mode}{' (capture needs contributor or ledger)' if mode not in ('contributor', 'ledger') else ''}",
        f"platforms: {platforms}",
        f"extract: {'on' if cfg['extract'] else 'off'}  rewrite: {'on' if cfg['rewrite'] else 'off'}",
        f"dupe distance: {cfg['dupe_distance']} (vector near-duplicate threshold)",
        f"permalink base: {cfg['permalink_base'] or '(no evidence url)'}",
        "usage: /paranoy capture <on|off|key value> — keys: " + ", ".join(_CAPTURE_KEYS),
    ]
    return "\n".join(ready)


def platform_allowed(cfg: dict, platform: str) -> bool:
    """``all`` matches everything. Empty (no platform), ``cli``, and
    ``terminal`` all count as the terminal surface."""
    lowered = str(platform or "").strip().lower()
    if "all" in cfg["platforms"]:
        return True
    if lowered in {"", "cli", "terminal"}:
        lowered = "terminal"
    return lowered in cfg["platforms"]


def resolve_capture_collection(cfg: dict, settings: dict) -> str:
    return cfg["collection"] or str(settings.get("collection") or "").strip()


def capture_eligible(ctx, settings: dict) -> tuple[bool, dict, str]:
    """Shared eligibility: enabled, key, mode, and collection resolution."""
    cfg = read_capture_config(ctx)
    if not cfg["enabled"]:
        return False, cfg, "capture is off"
    if not settings.get("key_configured"):
        return False, cfg, "no agent key"
    if settings.get("mode") not in ("contributor", "ledger"):
        return False, cfg, f"mode is {settings.get('mode')}, capture needs contributor or ledger"
    collection = resolve_capture_collection(cfg, settings)
    if not collection:
        return False, cfg, "no collection"
    return True, cfg, collection


# ---------------------------------------------------------------------------
# LLM helpers
# ---------------------------------------------------------------------------


def _text_block(text: str):
    try:
        from agent.plugin_llm import PluginLlmTextInput

        return PluginLlmTextInput(text=text)
    except Exception:
        return {"type": "text", "text": text}


def _llm(ctx):
    try:
        return ctx.llm
    except Exception:
        return None


def _structured(llm, instructions: str, text: str, schema: dict) -> dict:
    result = llm.complete_structured(
        instructions=instructions,
        input=[_text_block(text)],
        json_schema=schema,
        json_mode=True,
        purpose="paranoy-capture",
    )
    parsed = getattr(result, "parsed", None)
    if isinstance(parsed, dict):
        return parsed
    raw = str(getattr(result, "text", "") or "")
    try:
        loaded = json.loads(raw)
        return loaded if isinstance(loaded, dict) else {}
    except ValueError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end > start:
            try:
                loaded = json.loads(raw[start : end + 1])
                return loaded if isinstance(loaded, dict) else {}
            except ValueError:
                return {}
    return {}


_CLAIMS_SCHEMA = {
    "type": "object",
    "properties": {
        "has_content": {"type": "boolean"},
        "observation": {"type": "boolean"},
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "rebuttal": {"type": "string"},
                },
                "required": ["text", "rebuttal"],
            },
        },
    },
    "required": ["has_content", "observation", "claims"],
}

_HUMAN_GATE = """You classify one message from a human chatting with an AI assistant, for a private evidence ledger.

Classify the message:
- "has_content": true only if the message contains concrete, observational information about the world: a report of something that happened (first- or second-hand), a state of the world, a technical observation or bug, or a stated belief about the world. False for small talk, acknowledgments ("ok", "yes do that", "lol"), pure questions, and decisions or intentions about future actions.
- "observation": true if the message reports something the person did, saw, heard, or read — including second-hand reports ("I talked to X and they said Y") and technical observations ("app v1.4 isn't compiling for me").
- "claims": contestable world-statements worth recording as standalone claims. For each, rewrite the content into a strong, checkable form (subject, relation, direction, scope, time window) and add "rebuttal": one sentence describing evidence that would count against it. If a message states a belief about the world, include the belief's content as a claim. Drop single data points with no contestable general content, decisions, intentions, and questions. Drop any candidate with no possible rebuttal.

Claim text is written in the third person about the world. Never quote or attribute the speaker in the claim text."""

_AGENT_GATE = """You read an AI assistant's draft answer and extract claims about the world worth recording in a private evidence ledger.

- Extract only statements about the world that evidence could rebut: facts, measurements, causes, outcomes.
- Not plans, not summaries of what the assistant was doing, not its reasoning steps, not questions.
- Rewrite each into a strong, checkable form (subject, relation, direction, scope, time window) and add "rebuttal": one sentence describing evidence that would count against it. Drop any candidate with no possible rebuttal.
- Most answers contain no such claims. Return an empty list then.

Set "has_content" to true when at least one claim is extracted, "observation" to false, and never treat the answer itself as an observation."""


def _classify(llm, text: str, instructions: str) -> dict:
    classification = {
        "has_content": False,
        "observation": False,
        "claims": [],
    }
    if llm is None or not str(text or "").strip():
        return classification
    parsed = _structured(llm, instructions, str(text)[:_GATE_TEXT_CAP], _CLAIMS_SCHEMA)
    claims = []
    for item in parsed.get("claims") or []:
        if not isinstance(item, dict):
            continue
        claim_text = str(item.get("text") or "").strip()
        rebuttal = str(item.get("rebuttal") or "").strip()
        if claim_text and rebuttal:
            claims.append({"text": claim_text, "rebuttal": rebuttal})
    classification["has_content"] = bool(parsed.get("has_content")) or bool(claims)
    classification["observation"] = bool(parsed.get("observation"))
    classification["claims"] = claims
    return classification


def classify_human_message(llm, text: str) -> dict:
    return _classify(llm, text, _HUMAN_GATE)


def classify_agent_answer(llm, text: str) -> dict:
    return _classify(llm, text, _AGENT_GATE)


# ---------------------------------------------------------------------------
# Publishing
# ---------------------------------------------------------------------------


def semantic_duplicate(service, collection: str, text: str, threshold: float):
    """Vector near-duplicate check against existing claims.

    Returns ``{match, nearest, distance, error}``. ``match`` is the closest
    claim at or below ``threshold`` (cosine distance, lower = closer), or
    None. ``nearest`` is that same claim when matched, otherwise the closest
    claim seen regardless of threshold — the audit trail for borderline
    keeps. On a search failure ``error`` is set and the check says nothing:
    the caller publishes rather than dropping a real claim (fail-open).
    """
    result = {"match": None, "nearest": None, "distance": None, "error": None}
    try:
        found = service.call(
            "search_claims",
            {"collection": collection, "text": text, "method": "vector", "limit": 5},
        )
    except Exception as exc:
        result["error"] = str(exc)
        return result
    items = found.get("items") if isinstance(found, dict) else None
    if not isinstance(items, list):
        result["error"] = "unreadable search result"
        return result
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            distance = float(item.get("distance"))
        except (TypeError, ValueError):
            continue
        if result["nearest"] is None or distance < result["distance"]:
            result["nearest"] = item
            result["distance"] = distance
    if result["nearest"] is not None and result["distance"] <= threshold:
        result["match"] = result["nearest"]
    return result


def claim_events(
    service,
    settings: dict,
    collection: str,
    claims: list[dict],
    *,
    max_claims: int,
    dupe_distance: float = 0.15,
) -> list[dict]:
    """Search before adding; skip near-duplicates; sign kind 4702. No links.

    A candidate is skipped when an existing claim matches its text exactly
    (keyword search) or sits within ``dupe_distance`` cosine distance
    (vector search). Every decision is journaled to ``capture.jsonl`` with the
    matched id and distance, so the threshold can be tuned from real data.
    """
    events: list[dict] = []
    for claim in claims[:max_claims]:
        text = str(claim.get("text") or "").strip()
        if not text:
            continue
        try:
            exact_match = runtime.find_duplicate_claim(service, collection, text)
        except Exception as exc:
            exact_match = None
            state.append_jsonl(
                "capture.jsonl",
                {"kind": "dedupe-unavailable", "via": "exact", "error": str(exc), "at": now_iso()},
            )
        if exact_match is not None:
            state.append_jsonl(
                "capture.jsonl",
                {"kind": "claim-duplicate", "via": "exact", "text": text, "at": now_iso()},
            )
            continue
        check = semantic_duplicate(service, collection, text, dupe_distance)
        if check["error"] is not None:
            state.append_jsonl(
                "capture.jsonl",
                {"kind": "dedupe-unavailable", "error": check["error"], "at": now_iso()},
            )
        elif check["match"] is not None:
            state.append_jsonl(
                "capture.jsonl",
                {
                    "kind": "claim-duplicate",
                    "via": "semantic",
                    "matched": check["match"].get("id"),
                    "distance": check["distance"],
                    "text": text,
                    "at": now_iso(),
                },
            )
            continue
        else:
            nearest = check["nearest"] or {}
            state.append_jsonl(
                "capture.jsonl",
                {
                    "kind": "claim-kept",
                    "text": text,
                    "nearest": nearest.get("id"),
                    "nearest_text": str(nearest.get("text") or "")[:120],
                    "best_distance": check["distance"],
                    "at": now_iso(),
                },
            )
        try:
            runtime.claim_gate(text, claim.get("rebuttal"))
        except PluginError as exc:
            state.append_jsonl(
                "capture.jsonl",
                {"kind": "claim-rejected", "text": text, "reason": str(exc), "at": now_iso()},
            )
            continue
        events.append(service.sign(4702, {"collection": collection, "text": text}))
    return events


def publish_records(service, settings: dict, events: list[dict]) -> dict:
    if not events:
        return {"accepted": [], "skipped": []}
    return runtime.publish(service, settings, events)


# ---------------------------------------------------------------------------
# Path A — human messages (pre_gateway_dispatch, background thread)
# ---------------------------------------------------------------------------

# Sessions spawned by delegate/subagent runs: their "user messages" are
# synthetic prompts, not human speech. pre_llm_call capture refuses them.
_SUBAGENT_SESSIONS: set[str] = set()


def note_subagent_started(child_session_id) -> None:
    try:
        if child_session_id:
            _SUBAGENT_SESSIONS.add(str(child_session_id))
    except Exception:
        pass


def note_subagent_stopped(child_session_id) -> None:
    try:
        _SUBAGENT_SESSIONS.discard(str(child_session_id or ""))
    except Exception:
        pass


def terminal_payload(kwargs, ctx, settings_factory) -> dict | None:
    """Terminal-mode capture, from the once-per-turn pre_llm_call hook.

    Only the terminal surface (no platform name, ``cli``, ``local``) —
    messaging platforms are handled by ``pre_gateway_dispatch``, and firing
    both would capture a Slack message twice. Skips slash commands, cron
    runs, and subagent sessions, whose user messages are prompts rather
    than human speech.
    """
    try:
        platform = str(kwargs.get("platform") or "").strip().lower()
        if platform not in ("", "cli", "local", "terminal"):
            return None
        session_id = str(kwargs.get("session_id") or "")
        if session_id and session_id in _SUBAGENT_SESSIONS:
            return None
        if gates.in_unattended_cron(ctx):
            return None
        message = kwargs.get("user_message")
        if not isinstance(message, str):
            return None
        text = message.strip()
        if len(text) < 3 or text.startswith("/"):
            return None
        settings = settings_factory()
        eligible, cfg, _reason = capture_eligible(ctx, settings)
        if not eligible:
            return None
        if "terminal" not in cfg["platforms"] and "all" not in cfg["platforms"]:
            return None
        collection = resolve_capture_collection(cfg, settings)
        source = cfg["terminal_source"] or str(settings.get("owner") or "").strip() or "terminal"
        return {
            "platform": "terminal",
            "chat_id": session_id,
            "message_id": "",
            "user_name": source,
            "text": text,
            "collection": collection,
            "permalink": "",
            "max_claims": cfg["max_claims"],
            "dupe_distance": cfg["dupe_distance"],
        }
    except Exception:
        return None


def _platform_value(source) -> str:
    platform = getattr(source, "platform", None)
    if platform is None:
        return ""
    value = getattr(platform, "value", None)
    return str(value if value is not None else platform)


def _authorized(gateway, source) -> bool:
    """pre_gateway_dispatch fires before auth; re-check before filing anything."""
    check = getattr(gateway, "_is_user_authorized", None)
    if not callable(check):
        return True
    try:
        return bool(check(source))
    except Exception:
        return True


def build_permalink(cfg: dict, platform: str, chat_id: str, message_id: str) -> str:
    if platform != "slack" or not cfg["permalink_base"]:
        return ""
    ts = str(message_id or "").strip()
    if not ts or not chat_id:
        return ""
    return f"{cfg['permalink_base']}/archives/{chat_id}/p{ts.replace('.', '')}"


def inbound_payload(event, gateway, ctx, settings_factory) -> dict | None:
    """Eligibility for one inbound message. Returns the job payload or None."""
    try:
        if event is None or getattr(event, "internal", False):
            return None
        if event.is_command():
            return None
        text = str(event.text or "").strip()
        if len(text) < 3:
            return None
        source = event.source
        if source is None or getattr(source, "is_bot", False):
            return None
        if not _authorized(gateway, source):
            return None
        settings = settings_factory()
        eligible, cfg, _reason = capture_eligible(ctx, settings)
        if not eligible:
            return None
        platform = _platform_value(source)
        if not platform_allowed(cfg, platform):
            return None
        collection = resolve_capture_collection(cfg, settings)
        return {
            "platform": platform,
            "chat_id": str(getattr(source, "chat_id", "") or ""),
            "message_id": str(getattr(event, "message_id", "") or ""),
            "user_name": str(getattr(source, "user_name", "") or ""),
            "text": text,
            "collection": collection,
            "permalink": build_permalink(
                cfg, platform, str(getattr(source, "chat_id", "") or ""), str(getattr(event, "message_id", "") or "")
            ),
            "max_claims": cfg["max_claims"],
            "dupe_distance": cfg["dupe_distance"],
        }
    except Exception as exc:
        logger.warning("paranoy capture: inbound eligibility failed: %s", exc)
        return None


def _message_key(payload: dict) -> str:
    if payload.get("message_id"):
        return f"{payload.get('platform')}:{payload.get('chat_id')}:{payload.get('message_id')}"
    digest = hashlib.sha256(str(payload.get("text") or "").encode("utf-8")).hexdigest()[:32]
    return f"{payload.get('platform')}:{payload.get('chat_id')}:{digest}"


def _seen(key: str) -> bool:
    return key in (state.load_state().get("capture_seen") or {})


def _mark_seen(key: str) -> None:
    stored = state.load_state()
    seen = dict(stored.get("capture_seen") or {})
    seen[key] = now_iso()
    if len(seen) > _SEEN_CAP:
        newest = sorted(seen.items(), key=lambda item: item[1], reverse=True)[: _SEEN_CAP // 2]
        seen = dict(newest)
    state.save_state({**stored, "capture_seen": seen})


def run_inbound(payload: dict, ctx, settings_factory, service_factory) -> None:
    """Background job: gate, classify, publish. Never raises."""
    key = _message_key(payload)
    try:
        if _seen(key):
            state.append_jsonl("capture.jsonl", {"kind": "inbound-duplicate", "key": key, "at": now_iso()})
            return
        _mark_seen(key)
        settings = settings_factory()
        service = service_factory(settings)
        llm = _llm(ctx)
        if llm is None:
            state.append_jsonl(
                "capture.jsonl",
                {"kind": "inbound-skipped", "reason": "no host LLM available", "at": now_iso()},
            )
            return
        classification = classify_human_message(llm, payload["text"])
        if not classification["has_content"] and not classification["observation"]:
            state.append_jsonl(
                "capture.jsonl",
                {"kind": "inbound-smalltalk", "platform": payload["platform"], "at": now_iso()},
            )
            return
        collection = payload["collection"]
        events = []
        if classification["observation"]:
            content = {"collection": collection, "text": payload["text"]}
            if payload.get("user_name"):
                content["source"] = payload["user_name"]
            content["retrieved_via"] = payload["platform"]
            if payload.get("permalink"):
                content["url"] = payload["permalink"]
            events.append(service.sign(4701, content))
        events.extend(
            claim_events(
                service,
                settings,
                collection,
                classification["claims"],
                max_claims=payload["max_claims"],
                dupe_distance=payload["dupe_distance"],
            )
        )
        result = publish_records(service, settings, events)
        state.append_jsonl(
            "capture.jsonl",
            {
                "kind": "inbound-published",
                "platform": payload["platform"],
                "user": payload.get("user_name"),
                "evidence": classification["observation"],
                "claims": len(result.get("accepted") or []),
                "at": now_iso(),
            },
        )
    except Exception as exc:
        logger.warning("paranoy capture: inbound job failed: %s", exc)
        state.append_jsonl(
            "capture.jsonl",
            {
                "kind": "inbound-error",
                "key": key,
                "error": str(exc),
                "at": now_iso(),
            },
        )


def start_inbound_thread(payload: dict, ctx, settings_factory, service_factory) -> None:
    thread = threading.Thread(
        target=run_inbound,
        args=(payload, ctx, settings_factory, service_factory),
        daemon=True,
        name="paranoy-capture",
    )
    thread.start()


# ---------------------------------------------------------------------------
# Path B — agent answers (llm_execution middleware)
# ---------------------------------------------------------------------------


def _final_response_text(response, api_mode: str) -> str:
    """The draft, only when this is the turn's final provider response."""
    if str(api_mode or "").lower() not in ("", "chat_completions", "openai"):
        return ""
    try:
        choices = getattr(response, "choices", None)
        if not choices:
            return ""
        choice = choices[0]
        if str(getattr(choice, "finish_reason", "") or "").lower() != "stop":
            return ""
        message = getattr(choice, "message", None)
        if message is None:
            return ""
        if getattr(message, "tool_calls", None):
            return ""
        content = getattr(message, "content", None)
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts = [
                str(block.get("text") or "")
                for block in content
                if isinstance(block, dict) and block.get("type") == "text"
            ]
            return "\n".join(part for part in parts if part).strip()
    except Exception:
        return ""
    return ""


def _set_response_text(response, text: str, *, append: bool = False) -> bool:
    try:
        message = response.choices[0].message
        current = getattr(message, "content", None)
        if append:
            if isinstance(current, str):
                message.content = f"{current}\n\n{text}"
                return True
            if isinstance(current, list):
                current.append({"type": "text", "text": text})
                return True
            message.content = text
            return True
        message.content = text
        return True
    except Exception:
        return False


def _message_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            str(block.get("text") or "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        return "\n".join(part for part in parts if part)
    return ""


def _last_user_text(messages) -> str:
    for message in reversed(list(messages or [])):
        if isinstance(message, dict) and message.get("role") == "user":
            return _message_text(message.get("content"))
    return ""


def retrieve_claims(service, collection: str, query: str, limit: int) -> list[dict]:
    """Claims only, with their linked evidence quotes inlined by the server."""
    for method in ("vector", "keyword"):
        try:
            found = service.call(
                "search_claims",
                {"collection": collection, "text": query, "method": method, "limit": limit},
            )
        except PluginError:
            if method == "keyword":
                raise
            continue
        items = found.get("items") if isinstance(found, dict) else None
        if isinstance(items, list):
            return [item for item in items if isinstance(item, dict)]
        return []
    return []


def render_claims(items: list[dict], limit: int) -> str:
    lines: list[str] = []
    for item in items[:limit]:
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        lines.append(f"- {text}")
        for link in (item.get("links") or [])[:2]:
            if not isinstance(link, dict):
                continue
            relation = str(link.get("relation") or "").upper()
            quote = str(link.get("quote") or "").strip()[:300]
            reason = str(link.get("reason") or "").strip()[:200]
            if not quote:
                continue
            line = f"    {relation}: \"{quote}\""
            if reason:
                line += f" — {reason}"
            lines.append(line)
    rendered = "\n".join(lines)
    if len(rendered) > 6000:
        rendered = rendered[:6000] + "\n    …"
    return rendered


_REWRITE_INSTRUCTION = """You are revising an AI assistant's draft answer in an ongoing conversation.

Below are records from a private evidence ledger relevant to this conversation: claims, and under each the evidence quotes linked to it (with the relation and reason).

Revise the draft answer so it is consistent with and informed by the ledger:
- Where a ledger record bears on the answer, work it in and cite it plainly (for example "the ledger records …", quoting when useful).
- Where the draft contradicts the ledger, correct the draft.
- Otherwise keep the draft's substance, tone, format, and language.
- Never mention this revision process. Output only the final answer text.

Ledger records:

{ledger}

Draft answer:

{draft}"""


def rewrite_answer(llm, messages, draft: str, ledger: str) -> str:
    try:
        history = copy.deepcopy(list(messages or []))
    except Exception:
        history = list(messages or [])
    instruction = _REWRITE_INSTRUCTION.format(ledger=ledger or "(no records)", draft=draft)
    history.append({"role": "user", "content": instruction})
    result = llm.complete(messages=history, purpose="paranoy-capture-rewrite", timeout=240.0)
    return str(getattr(result, "text", "") or "").strip()


def llm_execution(request, next_call, *, ctx, settings_factory, service_factory, **context):
    """Middleware body. Never raises; always returns a response the chain accepts."""
    api_mode = context.get("api_mode") or ""
    try:
        settings = settings_factory()
        eligible, cfg, collection = capture_eligible(ctx, settings)
    except Exception:
        settings = None
        eligible, cfg, collection = False, {}, ""
    if (
        settings is None
        or not eligible
        or (not cfg["extract"] and not cfg["rewrite"])
        or not platform_allowed(cfg, str(context.get("platform") or ""))
    ):
        return next_call(request)

    v1 = next_call(request)
    draft = _final_response_text(v1, api_mode)
    if not draft:
        return v1

    llm = _llm(ctx)
    ledger_error = ""
    service = None
    try:
        service = service_factory(settings)
    except Exception as exc:
        ledger_error = str(exc)

    # Claim extraction from the answer (agent output is never evidence).
    if cfg["extract"] and llm is not None and service is not None:
        try:
            classification = classify_agent_answer(llm, draft)
            events = claim_events(
                service,
                settings,
                collection,
                classification["claims"],
                max_claims=cfg["max_claims"],
                dupe_distance=cfg["dupe_distance"],
            )
            result = publish_records(service, settings, events)
            state.append_jsonl(
                "capture.jsonl",
                {
                    "kind": "answer-claims",
                    "claims": len(result.get("accepted") or []),
                    "at": now_iso(),
                },
            )
        except PluginError as exc:
            ledger_error = ledger_error or str(exc)
        except Exception as exc:
            logger.warning("paranoy capture: answer claim extraction failed: %s", exc)

    # Retrieval + rewrite against the collection's claims.
    if ledger_error or not cfg["rewrite"] or llm is None or service is None:
        if ledger_error:
            _set_response_text(v1, WARNING, append=True)
        return v1
    try:
        query = _last_user_text(request.get("messages")) or draft[:500]
        items = retrieve_claims(service, collection, query, cfg["retrieval_limit"])
    except PluginError as exc:
        _set_response_text(v1, WARNING, append=True)
        state.append_jsonl(
            "capture.jsonl", {"kind": "retrieval-error", "error": str(exc), "at": now_iso()}
        )
        return v1
    except Exception as exc:
        logger.warning("paranoy capture: retrieval failed: %s", exc)
        return v1
    if not items:
        return v1
    try:
        revised = rewrite_answer(llm, request.get("messages"), draft, render_claims(items, cfg["retrieval_limit"]))
    except Exception as exc:
        logger.warning("paranoy capture: rewrite failed: %s", exc)
        state.append_jsonl("capture.jsonl", {"kind": "rewrite-error", "error": str(exc), "at": now_iso()})
        return v1
    if revised and _set_response_text(v1, revised):
        state.append_jsonl(
            "capture.jsonl", {"kind": "rewritten", "claims": len(items), "at": now_iso()}
        )
        return v1
    return v1
