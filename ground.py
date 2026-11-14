"""Grounding: every reply has looked at the ledger first, and says nothing about it.

``context`` runs in ``pre_llm_call``. It makes the one search the host always
runs and hands the model the result with the rule it answers under. The agent
does any further searching itself, with the tools. ``append_warnings`` runs in
``transform_llm_output`` and adds the one line the model cannot leave out.
"""

from __future__ import annotations

from pathlib import Path

from . import capture, ledger, state
from .client import PluginError

RULE = """These are the records the Paranoy ledger holds on this topic. Use them as what you know, and answer the way you normally would. Do not mention the ledger, claims, evidence, ids or where a fact came from, and do not report what you looked up or what was stored.

Speak up only in these cases, and then in one plain sentence:
- Your answer depends on something about the person or their work that these records do not cover. Say you don't know it, or that you are guessing.
- Records here disagree on something the answer depends on. Say what the disagreement is.
- The person asks what you know, how you know it, or what is on record. Then show the records and their sources in full.

This first search used the message as it was written, so some records below may not bear on it. Ignore those. Before answering, search again as often as you need with paranoy_search: with your own wording, for each topic the message touches, for what the conversation refers to, and for evidence against what you found. Read the evidence behind a claim with paranoy_read when the answer rests on it. Stop when further searches turn up nothing new. General knowledge needs no search and no label."""

# Hermes skips a hook after 30 seconds by default. The search gives up well
# before that, so a slow ledger still produces the warning on this reply.
SEARCH_TIMEOUT = 20.0

DOWN = """Paranoy could not be searched for this message ({reason}), so you have no records for it. Answer as you normally would. The person is being told separately that the ledger was unreachable. If the answer depends on something about the person or their work that you would have looked up, say you don't know it."""


def render(items: list[dict]) -> str:
    if not items:
        return "The first search found no claims."
    lines = []
    for item in items:
        lines.append(f"- claim {item.get('id')}: {item.get('text')}")
        for link in item.get("links") or []:
            quote = f' — "{link["quote"]}"' if link.get("quote") else ""
            lines.append(f"    {link.get('relation')} by evidence {link.get('from')}: {link.get('reason')}{quote}")
        for contradiction in item.get("contradiction_ids") or []:
            lines.append(f"    in contradiction {contradiction}")
    return "\n".join(lines)


def context(kwargs: dict, ctx, settings: dict, service_factory) -> str | None:
    """The block added to the turn, or None when there is nothing to ground."""
    session_id = str(kwargs.get("session_id") or "")
    text = capture.human_message(kwargs, ctx)
    if not text or not settings["ready"]:
        return None
    parts = []
    question = _DECIDING.get(session_id, _DECIDING.get(""))
    if question is not None:
        parts.append(f"Decision mode is on. The decision: {question}\n\n{decide_procedure()}")
    if settings["ground"] or question is not None:
        try:
            service = service_factory(settings)
            service.timeout = SEARCH_TIMEOUT
            items = ledger.search(service, settings["collection"], text)
            state.journal("grounded", session=session_id, claims=[item.get("id") for item in items])
            parts.append(f"{RULE}\n\n{render(items)}")
        except Exception as exc:
            reason = str(exc) if isinstance(exc, PluginError) else "the search failed"
            state.journal("ground-error", session=session_id, error=str(exc))
            state.warn(f"Paranoy is unreachable ({reason}). This reply was not grounded.", session_id)
            parts.append(DOWN.format(reason=reason))
    return "<paranoy>\n" + "\n\n".join(parts) + "\n</paranoy>" if parts else None


def append_warnings(response_text, session_id) -> str | None:
    """The reply with any pending warning lines added, or None to leave it alone."""
    if not isinstance(response_text, str) or capture.is_subagent(session_id):
        return None  # leave the warning for a reply the person will read
    lines = state.take_warnings(str(session_id or ""))
    if not lines:
        return None
    return response_text.rstrip() + "\n\n" + "\n".join(f"_{line}_" for line in lines)


# ---------------------------------------------------------------------- decide

# Sessions in decision mode, mapped to the question. In memory: a restart ends
# the mode, and /decide starts it again.
_DECIDING: dict[str, str] = {}


def decide_procedure() -> str:
    body = (Path(__file__).resolve().parent / "skills" / "decide" / "SKILL.md").read_text(encoding="utf-8")
    return body.split("---", 2)[-1].strip()


def decide(raw_args: str, session_id: str, settings: dict, service_factory) -> str:
    """The /decide command. Typing it starts or ends decision mode for the session."""
    question = (raw_args or "").strip()
    if question.lower() in ("done", "cancel", "off", "stop"):
        end_decision(session_id)
        return "Decision mode is off."
    if not question:
        current = _DECIDING.get(session_id, _DECIDING.get(""))
        return f"Deciding: {current}\n/decide done ends it." if current else "Usage: /decide <question> | /decide done"
    if not settings["ready"]:
        return "Paranoy is not set up yet. Run /paranoy status."
    try:
        service_factory(settings).call("list_collections", {})
    except PluginError as exc:
        return f"Paranoy is unreachable ({exc}), so there is nothing to ground a decision in. Decision mode was not started."
    _DECIDING[session_id] = question
    return f"Decision mode is on: {question}\nSend a message to begin. /decide done ends it."


def end_decision(session_id: str = "") -> None:
    _DECIDING.pop(str(session_id or ""), None)
    _DECIDING.pop("", None)
