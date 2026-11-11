"""Surface the user's beliefs from the current conversation. Local until approved.

History is read only after explicit consent. The sources are the current
Hermes session, not every past session:

- ``hermes_state.SessionDB.get_messages_as_conversation(session_id)`` when the
  tool or slash command is given a session id. ``hermes_state`` is on the
  public plugin API list.
- The ``conversation_history`` Hermes passes to ``pre_llm_call``, cached in
  the plugin data directory after consent, when the session database is
  unavailable.
- Text the user pastes into the command or tool.

Excerpts are returned with the scan so the user can see exactly what was
read. Nothing is published until a specific proposal is approved, and the
destination is a private collection this key owns. Conversation text is
written only under the plugin data directory.
"""

from __future__ import annotations

import contextvars
import logging
import re

from . import state
from .client import PluginError, redact
from .runtime import claim_gate, now_iso, publish, row_role

logger = logging.getLogger(__name__)

_session_id = contextvars.ContextVar("paranoy_beliefs_session", default="")
_READER = None

USER_MESSAGE_CAP = 30
EXCERPT_CHARS = 1500
MAX_CANDIDATES = 12
MAX_ASSUMPTIONS = 3
ASSUMPTION_CHARS = 400

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")
_LEAD = re.compile(
    r"^(?:i think|i believe|i feel|i suspect|in my opinion|imo)\s+(?:that\s+)?(?P<body>.+)$",
    re.IGNORECASE,
)
_CHAT = re.compile(
    r"^(?:hi|hello|hey|thanks|thank you|ok|okay|yes|no|please|sure|cool|great)[.!]?$",
    re.IGNORECASE,
)
_CAN_MAY = re.compile(r"\b(?:can|may)\b", re.IGNORECASE)
_NORMATIVE = re.compile(
    r"\b(?:should|shouldn't|shouldnt|ought|must|mustn't|mustnt)\b",
    re.IGNORECASE,
)
_FUTURE = re.compile(
    r"\b(?:will|won't|wont|shall|gonna|tomorrow)\b|\bgoing to\b|\bnext (?:week|month|year)\b",
    re.IGNORECASE,
)
_PAST = re.compile(
    r"\b(?:was|were|did|had|used|went|made|said|thought|believed|felt)\b",
    re.IGNORECASE,
)
_INTENTION = re.compile(r"^(?:i|we)\s+(?:want|wish|hope|plan|decide|decided|need)\b", re.IGNORECASE)
_USER_LINE = re.compile(r"^(?:user|human|me)\s*:\s*(.*)$", re.IGNORECASE)
_OTHER_LINE = re.compile(r"^(?:assistant|agent|hermes|paranoy)\s*:\s*(.*)$", re.IGNORECASE)


def bind_session(session_id: str | None):
    return _session_id.set(str(session_id or ""))


def reset_session(token) -> None:
    _session_id.reset(token)


def use_history_reader(reader) -> None:
    """Tests replace the session-database read. None restores the default."""
    global _READER
    _READER = reader


def consented() -> bool:
    return bool(state.load_state().get("beliefs_consent"))


def record_consent(explicit: bool) -> dict:
    if not explicit:
        raise PluginError(
            "Consent is required before a beliefs scan. "
            "Pass consent true, or type /paranoy beliefs consent."
        )
    stamp = now_iso()
    state.update_state(beliefs_consent=True, beliefs_consent_at=stamp)
    return {
        "consent": True,
        "at": stamp,
        "note": "Conversation text stays in the plugin data directory until you approve one item.",
    }


def status() -> dict:
    saved = state.load_state()
    proposals = _proposals()
    pending = [item["id"] for item in proposals if item.get("status") == "pending"]
    return {
        "consent": bool(saved.get("beliefs_consent")),
        "consent_at": saved.get("beliefs_consent_at"),
        "pending": pending,
        "note": (
            "Type /paranoy beliefs consent before the first scan. "
            "A scan reads the current session, or text you paste. Nothing is filed until you approve an item."
        ),
    }


def remember_turn(kwargs: dict) -> None:
    """Cache this turn's user text after consent. Never logs the text."""
    if not consented():
        return
    if not isinstance(kwargs, dict):
        return
    messages = _user_messages(kwargs.get("conversation_history"))
    current = _message_text(kwargs.get("user_message"))
    if current:
        messages.append(current)
    messages = [redact(text) for text in _cap_messages(messages)]
    if not messages:
        return
    state.update_state(
        beliefs_history={
            "session_id": str(kwargs.get("session_id") or ""),
            "messages": messages,
        }
    )


def claim_problems(text: str) -> list[str]:
    """Form rules only. Truth, tone, and plausibility are not checked."""
    sentence = " ".join(str(text or "").split())
    problems = []
    if not sentence:
        problems.append("empty")
        return problems
    words = sentence.replace(".", "").split()
    if len(words) < 4:
        problems.append("too short to be one scoped claim")
    if "?" in sentence:
        problems.append("a question is not a claim")
    if ";" in sentence or " but " in f" {sentence.lower()} ":
        problems.append("more than one claim")
    if _CAN_MAY.search(sentence):
        problems.append("uses can or may")
    if _NORMATIVE.search(sentence):
        problems.append("normative")
    if _FUTURE.search(sentence):
        problems.append("a prediction of the future")
    if _PAST.search(sentence):
        problems.append("not present tense")
    if _INTENTION.search(sentence):
        problems.append("an intention, not a world proposition")
    if _CHAT.fullmatch(sentence) or re.match(r"^(?:hi|hello|hey|thanks|thank you)\b", sentence, re.IGNORECASE):
        problems.append("not a claim")
    return problems


def scan(text: str | None = None, session_id: str | None = None) -> dict:
    """Read local history and list claim-shaped statements. Sends nothing."""
    if not consented():
        raise PluginError(
            "Consent is required before a beliefs scan. "
            "Type /paranoy beliefs consent, or call paranoy_surface_beliefs with action consent and consent true."
        )
    excerpts = _collect_excerpts(text, session_id)
    if not excerpts:
        raise PluginError(
            "No conversation text was available. Paste it with /paranoy beliefs scan <text>, "
            "or pass text to paranoy_surface_beliefs. "
            "The scan reads the current Hermes session only, after consent."
        )
    candidates = _candidates(excerpts)
    payload = {
        "excerpts": excerpts,
        "candidates": candidates,
        "filed": False,
        "sent": False,
        "note": (
            "Excerpts are exactly the text that was read. "
            "Candidates are statements that already fit the claim rules. "
            "Truth, tone, and plausibility are not judged. "
            "An assumption under a belief is the agent's inference. "
            "Propose it before approval. Nothing was sent."
        ),
    }
    state.update_state(
        beliefs_scan={
            "excerpts": excerpts,
            "candidates": candidates,
            "proposals": [],
        }
    )
    return payload


def propose(items) -> dict:
    """Attach the agent's inferred assumptions. Still local. Still unfiled."""
    if not isinstance(items, list) or not items:
        raise PluginError("propose needs a list of candidates.")
    excerpts = _excerpts()
    if not excerpts:
        raise PluginError("Scan first. There are no excerpts to quote.")
    proposals = []
    errors = []
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            errors.append({"index": index, "error": "A candidate must be an object."})
            continue
        try:
            proposals.append(_one_proposal(item, excerpts, len(proposals) + 1))
        except PluginError as exc:
            errors.append({"index": index, "error": str(exc)})
    state.update_state(
        beliefs_scan={
            "excerpts": excerpts,
            "candidates": _scan().get("candidates") or [],
            "proposals": proposals,
        }
    )
    return {
        "proposals": proposals,
        "errors": errors,
        "excerpts": excerpts,
        "filed": False,
        "sent": False,
        "note": "Each assumption is the agent's inference, not a statement the user made. Nothing was sent.",
    }


def approve(service, settings: dict, ids, collection: str | None = None) -> dict:
    """File the approved beliefs only, then search for evidence either way."""
    from .runtime import find_duplicate_claim

    wanted = _id_list(ids)
    if not wanted:
        raise PluginError("approve needs the proposal ids to file.")
    proposals = _proposals()
    chosen = [item for item in proposals if item.get("id") in wanted and item.get("status") == "pending"]
    missing = [item for item in wanted if item not in {row.get("id") for row in chosen}]
    if missing:
        raise PluginError("Nothing was filed. Unknown or already filed ids: " + ", ".join(missing) + ".")
    destination = private_collection(service, settings, collection)
    filed = []
    for item in chosen:
        try:
            one = _file_one(service, settings, destination, item, find_duplicate_claim)
        except PluginError as exc:
            filed.append({"id": item.get("id"), "claim": item.get("claim"), "error": str(exc)})
            continue
        item["status"] = "filed"
        item["claim_id"] = one.get("claim_id")
        filed.append(one)
    _save_proposals(proposals)
    return {
        "collection": destination,
        "private": True,
        "filed": filed,
        "excerpts": _excerpts_for(filed),
        "sent": True,
        "note": (
            "Only the approved claims were filed. Assumptions stay labelled as the agent's inference "
            "and were not filed as claims. Support and rebuttal counts are retrieval aids, not verdicts."
        ),
    }


def private_collection(service, settings: dict, requested) -> str:
    """A collection this key owns that is not public."""
    from .commands import check_slug

    listed = service.call("list_collections", {})
    rows = listed.get("result") if isinstance(listed, dict) else None
    if not isinstance(rows, list):
        raise PluginError("Collections could not be listed, so nothing was filed.")
    pubkey = getattr(service, "pubkey", None)

    def owned_private(row: dict) -> bool:
        return row_role(row, pubkey) == "owner" and row.get("public") is not True

    if requested:
        slug = check_slug(requested)
        row = next((item for item in rows if isinstance(item, dict) and item.get("slug") == slug), None)
        if row is None or row_role(row, pubkey) != "owner":
            raise PluginError(f"{slug} is not a collection this key owns, so nothing was filed.")
        if row.get("public") is True:
            raise PluginError(
                f"{slug} is public. Beliefs are filed only in a private collection this key owns."
            )
        return slug
    owned = [item for item in rows if isinstance(item, dict) and owned_private(item)]
    pinned = str(settings.get("collection") or "")
    match = next((item for item in owned if item.get("slug") == pinned), None)
    if match:
        return match["slug"]
    if len(owned) == 1:
        return owned[0]["slug"]
    names = ", ".join(sorted(item["slug"] for item in owned if item.get("slug"))) or "(none)"
    raise PluginError(
        "Name a private collection this key owns. Public collections are refused. "
        f"Private collections this key owns: {names}."
    )


def format_status(payload: dict) -> str:
    lines = [
        f"consent: {'yes' if payload.get('consent') else 'no'}",
        "pending: " + (", ".join(payload.get("pending") or []) or "(none)"),
        payload.get("note") or "",
    ]
    return "\n".join(lines)


def format_scan(payload: dict) -> str:
    lines = ["Excerpts read:"]
    for excerpt in payload.get("excerpts") or []:
        lines.append(f"[{excerpt.get('id')}] ({excerpt.get('source')}) {excerpt.get('text')}")
    lines.append("Candidates:")
    candidates = payload.get("candidates") or []
    if not candidates:
        lines.append("  (none that already fit the claim rules)")
    for item in candidates:
        lines.append(f"  {item.get('id')}: {item.get('claim')}")
        lines.append(f"    quote: {item.get('quote')}")
    lines.append(payload.get("note") or "")
    return "\n".join(lines)


def format_approve(payload: dict) -> str:
    lines = [f"Filed in private collection {payload.get('collection')}:"]
    for item in payload.get("filed") or []:
        evidence = item.get("evidence") or {}
        lines.append(
            f"  {item.get('claim_id')}: {item.get('claim')} "
            f"(supports {len(evidence.get('supports') or [])}, "
            f"rebuts {len(evidence.get('rebuts') or [])})"
        )
        for assumption in item.get("assumptions") or []:
            lines.append(f"    assumption (agent inference): {assumption.get('text')}")
    lines.append("Excerpts used:")
    for excerpt in payload.get("excerpts") or []:
        lines.append(f"[{excerpt.get('id')}] {excerpt.get('text')}")
    lines.append(payload.get("note") or "")
    return "\n".join(lines)


def _file_one(service, settings, collection: str, item: dict, find_duplicate) -> dict:
    text = str(item.get("claim") or "").strip()
    claim_gate(text, str(item.get("would_be_rebutted_by") or ""))
    duplicate = find_duplicate(service, collection, text)
    if duplicate:
        claim_id = duplicate.get("id")
        evidence = _search_evidence(service, collection, text)
        return _filed_row(item, collection, claim_id, evidence, skipped=True)
    event = service.sign(4702, {"collection": collection, "text": text})
    result = publish(service, settings, [event])
    evidence = _search_evidence(service, collection, text)
    claim_id = event.get("id")
    if isinstance(result, dict) and not claim_id:
        accepted = result.get("accepted") or []
        claim_id = accepted[0] if accepted else None
    return _filed_row(item, collection, claim_id, evidence, skipped=False)


def _filed_row(item, collection, claim_id, evidence, *, skipped: bool) -> dict:
    return {
        "id": item.get("id"),
        "claim": item.get("claim"),
        "claim_id": claim_id,
        "collection": collection,
        "skipped": skipped,
        "quote": item.get("quote"),
        "excerpt_id": item.get("excerpt_id"),
        "assumptions": item.get("assumptions") or [],
        "evidence": evidence,
    }


def _search_evidence(service, collection: str, text: str) -> dict:
    empty = {"supports": [], "rebuts": [], "qualifies": [], "error": None}
    try:
        found = service.call(
            "search_claims",
            {"collection": collection, "text": text, "method": "keyword", "limit": 10},
        )
    except PluginError as exc:
        empty["error"] = str(exc)
        return empty
    supports, rebuts, qualifies = [], [], []
    items = []
    if isinstance(found, dict):
        if isinstance(found.get("items"), list):
            items = found["items"]
        elif isinstance(found.get("result"), list):
            items = found["result"]
        elif isinstance(found.get("result"), dict) and isinstance(found["result"].get("items"), list):
            items = found["result"]["items"]
    for claim in items:
        if not isinstance(claim, dict):
            continue
        links = claim.get("links") if isinstance(claim.get("links"), list) else []
        for link in links:
            if not isinstance(link, dict):
                continue
            relation = str(link.get("relation") or "").upper()
            row = {
                "quote": link.get("quote"),
                "reason": link.get("reason"),
                "from": link.get("from"),
            }
            if relation == "SUPPORTS" and len(supports) < 5:
                supports.append(row)
            elif relation == "REBUTS" and len(rebuts) < 5:
                rebuts.append(row)
            elif relation == "QUALIFIES" and len(qualifies) < 5:
                qualifies.append(row)
    return {"supports": supports, "rebuts": rebuts, "qualifies": qualifies, "error": None}


def _one_proposal(item: dict, excerpts: list[dict], number: int) -> dict:
    claim = " ".join(str(item.get("claim") or "").split())
    problems = claim_problems(claim)
    if problems:
        raise PluginError("The claim was not kept: " + "; ".join(problems) + ".")
    quote = str(item.get("quote") or "").strip()
    excerpt = _excerpt_for_quote(quote, excerpts)
    if excerpt is None:
        raise PluginError("The quote is not in the excerpts that were read, so nothing was kept.")
    assumptions = item.get("assumptions")
    if isinstance(assumptions, str):
        assumptions = [assumptions]
    if not isinstance(assumptions, list):
        assumptions = []
    cleaned = []
    for assumption in assumptions:
        text = " ".join(str(assumption or "").split())
        if not text:
            continue
        if len(text) > ASSUMPTION_CHARS:
            raise PluginError("An assumption is longer than the limit, so nothing was kept.")
        cleaned.append(
            {
                "text": text,
                "inference": True,
                "label": "agent inference, not something the user stated",
            }
        )
        if len(cleaned) >= MAX_ASSUMPTIONS:
            break
    if not cleaned:
        raise PluginError("Each belief needs at least one underlying assumption, labelled as the agent's inference.")
    rebuttal = str(item.get("would_be_rebutted_by") or "").strip()
    claim_gate(claim, rebuttal)
    return {
        "id": f"p{number}",
        "claim": claim if claim.endswith(".") else claim + ".",
        "quote": quote,
        "excerpt_id": excerpt["id"],
        "assumptions": cleaned,
        "would_be_rebutted_by": rebuttal,
        "status": "pending",
    }


def _candidates(excerpts: list[dict]) -> list[dict]:
    found = []
    for excerpt in excerpts:
        for sentence in _sentences(excerpt["text"]):
            if len(found) >= MAX_CANDIDATES:
                return found
            quote = sentence.strip()
            claim = _world_claim(quote)
            if claim is None:
                continue
            found.append(
                {
                    "id": f"c{len(found) + 1}",
                    "claim": claim,
                    "quote": quote,
                    "excerpt_id": excerpt["id"],
                    "assumptions": [],
                    "fileable": False,
                }
            )
    return found


def _world_claim(sentence: str) -> str | None:
    match = _LEAD.match(sentence.rstrip("."))
    body = match.group("body") if match else sentence
    body = body.strip().rstrip(".")
    if not body:
        return None
    claim = body[0].upper() + body[1:]
    if claim_problems(claim):
        return None
    return claim if claim.endswith(".") else claim + "."


def _sentences(text: str) -> list[str]:
    parts = _SENTENCE_SPLIT.split(str(text or "").strip())
    return [part.strip() for part in parts if part.strip()]


def _collect_excerpts(text: str | None, session_id: str | None) -> list[dict]:
    pasted = redact(str(text or "").strip())
    if pasted:
        rows = [{"text": redact(part)[:EXCERPT_CHARS], "source": "pasted"} for part in _pasted_user_text(pasted)]
        return _number_excerpts(rows)
    sid = _session_id.get() if session_id is None else str(session_id or "")
    rows = _session_rows(sid)
    source = "session"
    if not rows:
        rows = _hook_rows()
        source = "hook"
    excerpts = []
    for text_value in rows:
        cleaned = redact(text_value)[:EXCERPT_CHARS]
        if cleaned:
            excerpts.append({"text": cleaned, "source": source})
    return _number_excerpts(excerpts)


def _number_excerpts(rows: list[dict]) -> list[dict]:
    excerpts = []
    for row in rows[:USER_MESSAGE_CAP]:
        excerpts.append({"id": f"e{len(excerpts) + 1}", "source": row["source"], "text": row["text"]})
    return excerpts


def _pasted_user_text(text: str) -> list[str]:
    labelled = False
    chunks: list[str] = []
    current: list[str] = []
    role = ""
    for line in text.splitlines():
        user = _USER_LINE.match(line)
        other = _OTHER_LINE.match(line)
        if user or other:
            labelled = True
            if role == "user" and current:
                chunks.append("\n".join(current).strip())
            role = "user" if user else "other"
            current = [(user or other).group(1)]
            continue
        if role == "user":
            current.append(line)
    if role == "user" and current:
        chunks.append("\n".join(current).strip())
    if not labelled:
        return [text] if text else []
    return [chunk for chunk in chunks if chunk]


def _session_rows(session_id: str) -> list[str]:
    if not session_id:
        return []
    reader = _READER or _session_db_reader
    try:
        rows = reader(session_id) or []
    except Exception:
        logger.debug("beliefs session read failed")
        return []
    texts = []
    for row in rows:
        if isinstance(row, str):
            texts.append(row)
        elif isinstance(row, dict) and (row.get("role") in (None, "user")):
            texts.append(_message_text(row.get("text") if "text" in row else row.get("content")))
    return _cap_messages([text for text in texts if text])


def _session_db_reader(session_id: str) -> list[dict]:
    try:
        from hermes_state import SessionDB
    except Exception:
        return []
    try:
        db = SessionDB()
        rows = db.get_messages_as_conversation(session_id) or []
    except Exception:
        return []
    return rows if isinstance(rows, list) else []


def _hook_rows() -> list[str]:
    saved = state.load_state().get("beliefs_history")
    if not isinstance(saved, dict):
        return []
    messages = saved.get("messages")
    if not isinstance(messages, list):
        return []
    return [str(item) for item in messages if str(item).strip()]


def _user_messages(history) -> list[str]:
    if not isinstance(history, list):
        return []
    texts = []
    for item in history:
        if isinstance(item, str):
            texts.append(item)
            continue
        if not isinstance(item, dict) or item.get("role") not in (None, "user"):
            continue
        text = _message_text(item.get("content"))
        if text:
            texts.append(text)
    return texts


def _message_text(content) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(block["text"])
        return "\n".join(part for part in parts if part).strip()
    return ""


def _cap_messages(messages: list[str]) -> list[str]:
    capped = []
    for text in messages[-USER_MESSAGE_CAP:]:
        cleaned = " ".join(str(text).split())
        if cleaned:
            capped.append(cleaned[:EXCERPT_CHARS])
    return capped


def _excerpt_for_quote(quote: str, excerpts: list[dict]) -> dict | None:
    if len(quote) < 12:
        return None
    for excerpt in excerpts:
        if quote in str(excerpt.get("text") or ""):
            return excerpt
    return None


def _scan() -> dict:
    saved = state.load_state().get("beliefs_scan")
    return saved if isinstance(saved, dict) else {}


def _excerpts() -> list[dict]:
    rows = _scan().get("excerpts")
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _proposals() -> list[dict]:
    rows = _scan().get("proposals")
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _save_proposals(proposals: list[dict]) -> None:
    saved = _scan()
    state.update_state(
        beliefs_scan={
            "excerpts": saved.get("excerpts") or [],
            "candidates": saved.get("candidates") or [],
            "proposals": proposals,
        }
    )


def _excerpts_for(filed: list[dict]) -> list[dict]:
    wanted = {item.get("excerpt_id") for item in filed}
    return [excerpt for excerpt in _excerpts() if excerpt.get("id") in wanted]


def _id_list(ids) -> list[str]:
    if isinstance(ids, str):
        return [part.strip() for part in ids.split(",") if part.strip()]
    if isinstance(ids, list):
        return [str(item).strip() for item in ids if str(item).strip()]
    return []
