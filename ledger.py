"""Reading and writing the ledger: publish, claim extraction, and ingest.

``ingest`` is the one write path that thinks: it stores a document, extracts
claims from it, and reuses claims the collection already has. Capture is
``ingest`` with a gate in front of it. See SPEC.md.
"""

from __future__ import annotations

import json

from . import settings as config
from . import state
from .client import PluginError

RELATIONS = ("SUPPORTS", "REBUTS", "QUALIFIES")
SAME_CLAIM_REASON = "The evidence states this claim."
_CHUNK = 20000  # characters of a document handed to the model per extraction call
_PAGE = 20000  # the server's default get_event text window


# ------------------------------------------------------------------ publishing


def profile_content(settings: dict) -> dict:
    return {
        "name": str(settings.get("agent_name") or "").strip() or "paranoy-agent",
        "about": str(settings.get("agent_about") or "").strip() or "agent",
    }


def _first_write_events(service, settings: dict) -> list[dict]:
    """Terms and profile, sent once ahead of this key's first write."""
    terms = {
        "version": str(settings.get("terms_version") or config.TERMS_VERSION),
        "sha256": str(settings.get("terms_sha256") or config.TERMS_SHA256),
    }
    return [service.sign(4706, terms), service.sign(0, profile_content(settings))]


def publish(service, settings: dict, events: list[dict]) -> dict:
    """Publish a batch, signing the terms first when the server still needs them."""
    if not events:
        return {"accepted": [], "skipped": []}
    with_terms = not state.load_state().get("terms_published")
    try:
        result = service.publish(_first_write_events(service, settings) + events if with_terms else events)
    except PluginError as exc:
        if with_terms or "sign the current terms first" not in str(exc).lower():
            raise
        result = service.publish(_first_write_events(service, settings) + events)
        with_terms = True
    if with_terms:
        state.update_state(terms_published=True)
    state.journal("published", ids=[event["id"] for event in events], kinds=[event["kind"] for event in events])
    return result


def register_account(service, settings: dict, email: str | None) -> str:
    """Register this key at /hermes with its terms and profile. Returns a line for the owner."""
    email = str(email or "").strip() or None
    result = service.register(email, _first_write_events(service, settings))
    state.update_state(terms_published=True)
    server = str(settings.get("server_url") or config.DEFAULTS["server_url"]).rstrip("/")
    lines = [("Registered" if result.get("registered") else "Already registered") + f" on Paranoy as {service.npub}."]
    if email:
        lines.append(
            f"{email} holds the account and this agent is its Ledger. A notice was emailed there; "
            f"sign in at {server}/connect with that address to see and change everything."
        )
    else:
        lines.append("This agent is its own account, with one collection on the free plan.")
    lines.append("Next: /paranoy collection <slug> in a Hermes session.")
    return "\n".join(lines)


# --------------------------------------------------------------------- reading


def search(service, collection: str, text: str, method: str = "vector", limit: int | None = None) -> list[dict]:
    args = {"collection": collection, "text": text, "method": method}
    if limit:
        args["limit"] = int(limit)
    found = service.call("search_claims", args)
    items = found.get("items") if isinstance(found, dict) else None
    return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []


def evidence_text(service, evidence_id: str) -> str:
    """The whole text of one evidence record, read a window at a time."""
    pieces: list[str] = []
    offset = 0
    while True:
        page = service.call("get_event", {"id": evidence_id, "offset": offset, "max_chars": _PAGE})
        result = page.get("result") if isinstance(page, dict) else None
        if not isinstance(result, dict) or result.get("kind") != 4701:
            raise PluginError("That id is not an evidence record this key can read.")
        text = str((result.get("content") or {}).get("text") or "")
        pieces.append(text)
        offset += len(text)
        if not text or offset >= int(result.get("chars") or 0):
            return "".join(pieces)


# ------------------------------------------------------------------ extraction

_GATE = """You read one message a person sent in a chat with an AI assistant, for a private evidence ledger.

Set "evidence" to true when the message says something about the world:
- something the speaker did, saw, measured, read or was told
- a state of the world or a technical observation
- a decision already made
- a durable fact or preference about the speaker or their work

Set it to false for small talk, acknowledgements, questions, instructions to the assistant, and intentions not yet acted on.
Do not judge whether the statement is accurate, well-sourced or important. When unsure, set it to true.

"""

_CLAIMS = """Extract the claims this text makes. Return every claim it supports, and an empty list when it makes none.

Each claim must be:
- One statement. Split compound sentences.
- Readable alone. Use names in place of pronouns, and keep the date, version or condition the text gives.
- No stronger than the text. Keep its scope and its hedges.
- Checkable. Someone could describe evidence that would count against it. Drop it otherwise.
- About the world, in the third person. Do not write "the speaker said" or "the document says".
- Faithful. It says what the text says and adds nothing.

Attribution belongs in the evidence record, not in the claim. "Don Muir wrote that buyers' questions have shifted toward data ownership" is wrong. "Over the year before July 2026, institutional AI buyers' questions shifted toward where their data goes" is right, and the date carries the scope the source gave. The exception is a statement that is itself the fact. Then name the speaker: "Anthropic has said there will be no winner-take-all in AI models" records a company's public position, which is an event in the world."""

_EXTRACT_SCHEMA = {
    "type": "object",
    "properties": {
        "evidence": {"type": "boolean"},
        "claims": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["evidence", "claims"],
}

_SAME = """Each entry has a new claim and candidate claims already on record.
For each entry, decide whether one candidate says the same thing as the new claim: the same subject, the same direction, the same scope.
A candidate that says the opposite, or something broader, narrower or merely related, is not the same.
Return a match only for an entry that has one, giving the entry's "claim" number and the candidate's "id"."""

_SAME_SCHEMA = {
    "type": "object",
    "properties": {
        "matches": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"claim": {"type": "integer"}, "id": {"type": "string"}},
                "required": ["claim", "id"],
            },
        }
    },
    "required": ["matches"],
}


def _ask(llm, instructions: str, text: str, schema: dict) -> dict:
    """One structured call to the host model. Returns {} when the reply is not JSON."""
    try:
        from agent.plugin_llm import PluginLlmTextInput

        block = PluginLlmTextInput(text=text)
    except Exception:
        block = {"type": "text", "text": text}
    result = llm.complete_structured(
        instructions=instructions, input=[block], json_schema=schema, json_mode=True, purpose="paranoy"
    )
    parsed = getattr(result, "parsed", None)
    if isinstance(parsed, dict):
        return parsed
    raw = str(getattr(result, "text", "") or "")
    start, end = raw.find("{"), raw.rfind("}")
    try:
        loaded = json.loads(raw[start : end + 1]) if 0 <= start < end else None
    except ValueError:
        loaded = None
    return loaded if isinstance(loaded, dict) else {}


def extract(llm, text: str, *, message: bool) -> tuple[bool, list[str]]:
    """(is this evidence, its claims). A document is always evidence; a message is gated."""
    is_evidence = not message
    claims: list[str] = []
    instructions = (_GATE if message else "") + _CLAIMS
    for start in range(0, len(text), _CHUNK):
        parsed = _ask(llm, instructions, text[start : start + _CHUNK], _EXTRACT_SCHEMA)
        is_evidence = is_evidence or bool(parsed.get("evidence"))
        found = parsed.get("claims")
        for claim in found if isinstance(found, list) else []:
            claim = claim.strip() if isinstance(claim, str) else ""
            if claim and claim not in claims:
                claims.append(claim)
    return is_evidence or bool(claims), claims


def match_existing(service, llm, collection: str, claims: list[str]) -> tuple[list[str], list[dict], list[dict]]:
    """Split extracted claims into (new, same as an existing claim, related existing claims).

    "Same" is identical text, or a model reading both and confirming they say
    the same thing. Search closeness alone is never enough: a claim and its
    opposite sit close together. A failed search means "no match": the claim
    is stored, and a real outage surfaces at publish.
    """
    same: dict[str, dict] = {}
    related: dict[str, dict] = {}
    undecided: list[tuple[str, list[dict]]] = []
    new: list[str] = []
    for claim in claims:
        try:
            exact = next(
                (item for item in search(service, collection, claim, "keyword", 20)
                 if str(item.get("text") or "").strip() == claim),
                None,
            )
            candidates = [] if exact else search(service, collection, claim, "vector", 5)
        except PluginError:
            exact, candidates = None, []
        if exact:
            same[exact["id"]] = exact
        elif candidates:
            undecided.append((claim, candidates))
        else:
            new.append(claim)
    if undecided:
        entries = [
            {"claim": index, "text": claim, "candidates": [{"id": c["id"], "text": c.get("text")} for c in candidates]}
            for index, (claim, candidates) in enumerate(undecided)
        ]
        matches = _ask(llm, _SAME, json.dumps(entries, ensure_ascii=False), _SAME_SCHEMA).get("matches") or []
        chosen = {
            m["claim"]: m["id"]
            for m in (matches if isinstance(matches, list) else [])
            if isinstance(m, dict) and isinstance(m.get("claim"), int) and isinstance(m.get("id"), str)
        }
        for index, (claim, candidates) in enumerate(undecided):
            by_id = {c["id"]: c for c in candidates}
            match = by_id.pop(chosen.get(index), None)
            if match:
                same[match["id"]] = match
            else:
                new.append(claim)
            related.update(by_id)
    for claim_id in same:
        related.pop(claim_id, None)
    brief = lambda items: [{"id": item["id"], "text": item.get("text")} for item in items]
    return new, brief(same.values()), brief(related.values())


# ---------------------------------------------------------------------- ingest


def evidence_content(collection: str, text: str, meta: dict) -> dict:
    content = {"collection": collection, "text": text}
    for key in ("url", "title", "source", "archive_url", "retrieved_via"):
        value = str(meta.get(key) or "").strip()
        if value:
            content[key] = value
    return content


def ingest(service, llm, settings: dict, *, text: str, meta: dict | None = None,
           evidence_id: str | None = None, message: bool = False) -> dict:
    """Store a document (unless ``evidence_id`` says it is stored), with its claims.

    One batch: the evidence, each new claim, and a SUPPORTS link from the
    evidence to each existing claim it restates. That link is safe because the
    claim was extracted from this evidence. No other link is made here.
    """
    collection = settings["collection"]
    text = str(text or "").strip()
    if not text:
        raise PluginError("There is no text to store.")
    is_evidence, claims = extract(llm, text, message=message) if llm is not None else (not message, [])
    if not is_evidence:
        return {"stored": False, "reason": "not evidence"}
    events: list[dict] = []
    if evidence_id is None:
        events.append(service.sign(4701, evidence_content(collection, text, meta or {})))
        evidence_id = events[0]["id"]
    new, same, related = match_existing(service, llm, collection, claims) if claims else ([], [], [])
    stored_claims = []
    for claim in new:
        event = service.sign(4702, {"collection": collection, "text": claim})
        events.append(event)
        stored_claims.append({"id": event["id"], "text": claim})
    for existing in same:
        link = {"collection": collection, "from": evidence_id, "to": existing["id"],
                "relation": "SUPPORTS", "reason": SAME_CLAIM_REASON}
        events.append(service.sign(4703, link))
    publish(service, settings, events)
    result = {
        "stored": True,
        "evidence_id": evidence_id,
        "claims": stored_claims,
        "linked_existing_claims": same,
        "related_claims": related,
    }
    if llm is None:
        result["note"] = "No host model was available, so no claims were extracted."
    return result
