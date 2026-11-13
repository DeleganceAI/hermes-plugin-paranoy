"""Shared read and publish helpers used by the tool handlers."""

from __future__ import annotations

import time

from . import schemas, state
from .client import PluginError
from .quotes import quote_in_evidence

_OPTIONAL_EVIDENCE = ("url", "title", "source", "archive_url", "retrieved_via")


def profile_content(settings: dict) -> dict:
    """Kind 0 content from settings. The defaults live here and nowhere else."""
    return {
        "name": str(settings.get("agent_name") or "").strip() or schemas.DEFAULT_AGENT_NAME,
        "about": str(settings.get("agent_about") or "").strip() or schemas.DEFAULT_AGENT_ABOUT,
    }


def profile_event(service, settings: dict) -> dict:
    return service.sign(0, profile_content(settings))


def terms_event(service, settings: dict) -> dict:
    return service.sign(
        4706,
        {
            "version": str(settings.get("terms_version") or schemas.TERMS_VERSION),
            "sha256": str(settings.get("terms_sha256") or schemas.TERMS_SHA256),
        },
    )


def not_admitted(message: str) -> bool:
    lowered = str(message or "").lower()
    return "has not signed up" in lowered or "not a member of any collection" in lowered


def profile_refusal(message: str, npub: str, settings: dict, *, saved_note: str) -> str:
    """Plain explanation of a refused kind 0. ``saved_note`` says what was kept."""
    if not_admitted(message):
        server = str(settings.get("server_url") or schemas.DEFAULTS["server_url"]).rstrip("/")
        return (
            "Paranoy does not know this key yet, so it refused the profile. "
            f"The owner has to add this npub under Your agents on {server}/connect first: {npub}. "
            + saved_note
        )
    return f"The server refused the profile: {message}. " + saved_note


def publish_profile(service, settings: dict) -> dict:
    """Sign and publish one kind 0 with the current settings.

    The server accepts a profile from any key that has an account or belongs
    to a collection. It does not need a terms signature. The latest profile
    by received_at is the one readers show.
    """
    event = profile_event(service, settings)
    result = service.publish([event])
    state.update_state(profile_published_at=now_iso())
    payload = dict(result) if isinstance(result, dict) else {"result": result}
    payload["profile"] = profile_content(settings)
    payload["event_id"] = event.get("id")
    payload["stored_kind"] = 0
    return payload


def preface(service, settings: dict, events: list[dict]) -> list[dict]:
    """Sign terms and a kind 0 profile once, ahead of an allowed write."""
    if not settings.get("auto_sign_terms"):
        return events
    saved = state.load_state()
    if saved.get("terms_published"):
        return events
    return [terms_event(service, settings), profile_event(service, settings), *events]


def publish(service, settings: dict, events: list[dict]) -> dict:
    batch = preface(service, settings, events)
    try:
        result = service.publish(batch)
    except PluginError as exc:
        message = str(exc).lower()
        included_terms = any(event.get("kind") == 4706 for event in batch)
        if "sign the current terms first" in message and not included_terms:
            state.update_state(terms_published=False)
            batch = preface(service, {**settings, "auto_sign_terms": True}, events)
            if not any(event.get("kind") == 4706 for event in batch):
                batch = [terms_event(service, settings), profile_event(service, settings), *events]
            result = service.publish(batch)
        else:
            raise
    if any(event.get("kind") == 4706 for event in batch):
        state.update_state(terms_published=True)
    return result


def register_account(service, settings: dict, email: str | None) -> str:
    """Register this key at /hermes with its terms and profile. Returns a line for the owner."""
    email = str(email or "").strip() or None
    result = service.register(email, [terms_event(service, settings), profile_event(service, settings)])
    state.update_state(terms_published=True, profile_published_at=now_iso())
    server = str(settings.get("server_url") or schemas.DEFAULTS["server_url"]).rstrip("/")
    lines = [("Registered" if result.get("registered") else "Already registered") + f" on Paranoy as {service.npub}."]
    if email:
        lines.append(
            f"{email} holds the account and this agent is its Ledger. A notice was emailed there; "
            f"sign in at {server}/connect with that address to see and change everything."
        )
        lines.append("Next: /paranoy mode ledger in a Hermes session.")
    else:
        lines.append("This agent is its own account, with one collection on the free plan.")
    return "\n".join(lines)


def page_event(service, event_id: str) -> dict:
    """Read one event, following evidence text windows up to the cap."""
    offset = 0
    pieces: list[str] = []
    meta = None
    total = None
    while True:
        page = service.call(
            "get_event",
            {"id": event_id, "offset": offset, "max_chars": schemas.EVIDENCE_PAGE_CHARS},
        )
        result = page.get("result") if isinstance(page, dict) else None
        if result is None:
            return {"result": None}
        meta = result
        content = result.get("content") if isinstance(result.get("content"), dict) else {}
        if "chars" not in result:
            return page
        text = str(content.get("text") or "")
        pieces.append(text)
        total = int(result.get("chars") or 0)
        offset += len(text)
        if not text or offset >= total or offset >= schemas.EVIDENCE_CHAR_CAP:
            break
    assembled = dict(meta)
    content = dict(assembled.get("content") or {})
    content["text"] = "".join(pieces)
    assembled["content"] = content
    assembled["truncated"] = bool(total is not None and offset < total)
    return {"result": assembled}


def quote_matches_event(service, evidence_id: str, quote: str) -> None:
    loaded = page_event(service, evidence_id)
    result = loaded.get("result") if isinstance(loaded, dict) else None
    if not isinstance(result, dict):
        raise PluginError("That evidence event was not found in a collection this key can read.")
    content = result.get("content") if isinstance(result.get("content"), dict) else {}
    text = str(content.get("text") or "")
    if quote_in_evidence(quote, text):
        return
    if result.get("truncated"):
        raise PluginError(
            "The quote was not in the evidence text that could be read, so nothing was published."
        )
    raise PluginError(
        "The quote does not match the evidence under the server's normalization, so nothing was published."
    )


def list_all_evidence(service, collection: str) -> list[dict]:
    rows: list[dict] = []
    offset = 0
    limit = 500
    for _ in range(5):
        page = service.call(
            "list_evidence",
            {"collection": collection, "limit": limit, "offset": offset},
        )
        batch = page.get("result") if isinstance(page, dict) else None
        if not isinstance(batch, list) or not batch:
            break
        rows.extend(item for item in batch if isinstance(item, dict))
        if len(batch) < limit:
            break
        offset += limit
    return rows


def find_duplicate_evidence(service, collection: str, text: str, url: str) -> dict | None:
    target_url = url.strip()
    target_text = text.strip()
    checked = 0
    for row in list_all_evidence(service, collection):
        row_url = str(row.get("url") or "").strip()
        if target_url and row_url == target_url:
            return row
        if not target_text:
            continue
        if int(row.get("chars") or -1) != len(target_text):
            continue
        if checked >= 10:
            continue
        checked += 1
        event_id = str(row.get("id") or "")
        if not event_id:
            continue
        loaded = page_event(service, event_id)
        result = loaded.get("result") if isinstance(loaded, dict) else None
        content = result.get("content") if isinstance(result, dict) else {}
        if isinstance(content, dict) and str(content.get("text") or "").strip() == target_text:
            return row
    return None


def find_duplicate_claim(service, collection: str, text: str) -> dict | None:
    found = service.call(
        "search_claims",
        {"collection": collection, "text": text, "method": "keyword", "limit": 20},
    )
    items = found.get("items") if isinstance(found, dict) else None
    if not isinstance(items, list):
        return None
    wanted = text.strip()
    for item in items:
        if isinstance(item, dict) and str(item.get("text") or "").strip() == wanted:
            return item
    return None


def claim_gate(text: str, rebuttal: str) -> None:
    statement = str(text or "").strip()
    counter = str(rebuttal or "").strip()
    if not statement:
        raise PluginError("Claim text is empty.")
    if not counter:
        raise PluginError("would_be_rebutted_by is required and is not stored on the claim.")
    if counter.casefold() == statement.casefold():
        raise PluginError("would_be_rebutted_by has to be different from the claim text.")


def evidence_content(collection: str, args: dict, text: str) -> dict:
    content = {"collection": collection, "text": text}
    for key in _OPTIONAL_EVIDENCE:
        value = str(args.get(key) or "").strip()
        if value:
            content[key] = value
    return content


def require_relation(value: str, *, gap_finder: bool = False) -> str:
    relation = str(value or "").strip().upper()
    allowed = {"REBUTS", "QUALIFIES"} if gap_finder else {"SUPPORTS", "REBUTS", "QUALIFIES"}
    if relation not in allowed:
        if gap_finder:
            raise PluginError("Gap Finder links are REBUTS or QUALIFIES.")
        raise PluginError("relation must be SUPPORTS, REBUTS, or QUALIFIES.")
    return relation


MEMBER_ROLES = frozenset({"owner", "admin", "member"})


def row_role(row: dict, pubkey: str | None) -> str | None:
    """This key's role in one list_collections row.

    The server's ``role`` wins. Some responses carry ``role: null`` for keys
    that are on the lists, so owner, admins, and members are checked next.
    """
    role = row.get("role")
    if role in MEMBER_ROLES:
        return role
    if not pubkey:
        return None
    if row.get("owner") == pubkey:
        return "owner"
    for field, name in (("admins", "admin"), ("members", "member")):
        values = row.get(field)
        if isinstance(values, list) and pubkey in values:
            return name
    return None


def assert_membership(service, collection: str, *, administer: bool) -> dict:
    """Return the list_collections row or raise. ``administer`` requires owner or admin."""
    listed = service.call("list_collections", {})
    rows = listed.get("result") if isinstance(listed, dict) else None
    if not isinstance(rows, list):
        raise PluginError("Collections could not be listed, so nothing was done.")
    pubkey = getattr(service, "pubkey", None)
    for row in rows:
        if isinstance(row, dict) and row.get("slug") == collection:
            role = row_role(row, pubkey)
            if administer and role not in {"owner", "admin"}:
                raise PluginError(f"This key does not administer {collection}.")
            if role not in MEMBER_ROLES:
                raise PluginError(f"This key is not a member of {collection}.")
            return row
    raise PluginError(f"This key is not a member of {collection}.")


def reject_replacement_list(args: dict) -> None:
    for key in ("members", "admins", "agents", "events", "request"):
        if key in (args or {}):
            raise PluginError(
                "Pass add and remove. A whole list from the model is not published as-is."
            )


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
