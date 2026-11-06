"""Content writes and local contributor tools.

These sign events in-process. They do not accept a signed request or a
pre-signed batch from the model.
"""

from __future__ import annotations

from .. import schemas, state
from ..client import PluginError, dumps
from ..gates import as_bool, canonical_roles, resolve_collection
from ..runtime import (
    assert_membership,
    claim_gate,
    evidence_content,
    find_duplicate_claim,
    find_duplicate_evidence,
    now_iso,
    publish,
    quote_matches_event,
    require_relation,
)


def _collection(service, settings, tool_name, args) -> str:
    if settings.get("mode") in {"reader", "admin"}:
        raise PluginError("This mode does not write content.")
    collection = resolve_collection(settings, tool_name, args)
    assert_membership(service, collection, administer=False)
    return collection


def _owner_gate(settings, tool_name, args) -> None:
    if settings.get("mode") == "ledger" and tool_name in schemas.EXPLICIT_OWNER_TOOLS:
        if not as_bool(args.get("owner_asked"), False):
            raise PluginError(
                "In ledger mode, claims, links, and contradictions need owner_asked, "
                "because the owner has to ask for that write."
            )


def _role(settings) -> str:
    return settings.get("active_role") or ""


def add_evidence(service, settings, args) -> dict:
    collection = _collection(service, settings, "paranoy_add_evidence", args)
    fetch = as_bool(args.get("fetch"), False)
    text = str(args.get("text") or "")
    url = str(args.get("url") or "").strip()
    if fetch or (not text.strip() and url):
        if not url:
            raise PluginError("fetch needs a url.")
        fetched = service.call("fetch_url", {"url": url})
        text = str(fetched.get("text") or "")
        if not args.get("title") and fetched.get("title"):
            args = {**args, "title": fetched.get("title")}
    if not str(text).strip():
        raise PluginError("Evidence needs the full verbatim text.")
    if _role(settings) == "Fool":
        if len(str(text).strip()) < 200:
            raise PluginError("Fool will not publish an unreadable stub.")
        if not url and not str(args.get("source") or "").strip():
            raise PluginError("Fool will not publish a source with no attribution.")
    duplicate = find_duplicate_evidence(service, collection, str(text).strip(), url)
    if duplicate:
        return {
            "skipped": True,
            "reason": "duplicate evidence",
            "id": duplicate.get("id"),
            "url": duplicate.get("url"),
        }
    event = service.sign(4701, evidence_content(collection, args, text))
    result = publish(service, settings, [event])
    result = dict(result)
    result["stored_kind"] = 4701
    return result


def add_claim(service, settings, args) -> dict:
    _owner_gate(settings, "paranoy_add_claim", args)
    collection = _collection(service, settings, "paranoy_add_claim", args)
    text = str(args.get("text") or "")
    claim_gate(text, str(args.get("would_be_rebutted_by") or ""))
    link_keys = ("from", "quote", "relation", "reason")
    supplied = [key for key in link_keys if str(args.get(key) or "").strip()]
    if _role(settings) == "Claim Miner" and len(supplied) != len(link_keys):
        raise PluginError("Claim Miner publishes a claim together with a link to existing evidence.")
    if supplied and len(supplied) != len(link_keys):
        raise PluginError("A claim link needs from, quote, relation, and reason together.")
    duplicate = find_duplicate_claim(service, collection, text.strip())
    if duplicate:
        return {"skipped": True, "reason": "duplicate claim", "id": duplicate.get("id")}
    relation = None
    if supplied:
        relation = require_relation(args.get("relation"), gap_finder=False)
        quote_matches_event(service, str(args.get("from")).strip(), str(args.get("quote")))
    content = {"collection": collection, "text": text.strip()}
    claim = service.sign(4702, content)
    events = [claim]
    if supplied:
        link = service.sign(
            4703,
            {
                "collection": collection,
                "from": str(args.get("from")).strip(),
                "to": claim["id"],
                "relation": relation,
                "quote": str(args.get("quote")),
                "reason": str(args.get("reason")).strip(),
            },
        )
        events.append(link)
    result = dict(publish(service, settings, events))
    result["stored_kinds"] = [event["kind"] for event in events]
    result["claim_id"] = claim["id"]
    return result


def add_link(service, settings, args) -> dict:
    _owner_gate(settings, "paranoy_add_link", args)
    collection = _collection(service, settings, "paranoy_add_link", args)
    relation = require_relation(args.get("relation"), gap_finder=_role(settings) == "Gap Finder")
    evidence_id = str(args.get("from") or "").strip()
    target = str(args.get("to") or "").strip()
    quote = str(args.get("quote") or "")
    reason = str(args.get("reason") or "").strip()
    if not evidence_id or not target or not quote.strip() or not reason:
        raise PluginError("A link needs from, to, relation, quote, and reason.")
    quote_matches_event(service, evidence_id, quote)
    if relation == "QUALIFIES":
        loaded = service.call("get_event", {"id": target, "offset": 0, "max_chars": 1})
        result = loaded.get("result") if isinstance(loaded, dict) else None
        kind = result.get("kind") if isinstance(result, dict) else None
        if kind is not None and kind != 4702:
            raise PluginError("QUALIFIES may only target a claim, so nothing was published.")
    event = service.sign(
        4703,
        {
            "collection": collection,
            "from": evidence_id,
            "to": target,
            "relation": relation,
            "quote": quote,
            "reason": reason,
        },
    )
    published = dict(publish(service, settings, [event]))
    published["stored_kind"] = 4703
    return published


def add_contradiction(service, settings, args) -> dict:
    _owner_gate(settings, "paranoy_add_contradiction", args)
    collection = _collection(service, settings, "paranoy_add_contradiction", args)
    claims = [str(item).strip() for item in (args.get("claims") or []) if str(item).strip()]
    reason = str(args.get("reason") or "").strip()
    if len(claims) < 2 or len(set(claims)) < 2:
        raise PluginError("A contradiction needs two or more distinct claim ids.")
    if not reason:
        raise PluginError("A contradiction needs a reason.")
    event = service.sign(4704, {"collection": collection, "claims": claims, "reason": reason})
    published = dict(publish(service, settings, [event]))
    published["stored_kind"] = 4704
    return published


def log_reject(service, settings, args) -> dict:
    del service
    what = str(args.get("what") or "").strip()
    reason = str(args.get("reason") or "").strip()
    if not what or not reason:
        raise PluginError("A reject record needs what and reason.")
    record = {
        "time": now_iso(),
        "bot": settings.get("agent_name") or "paranoy-agent",
        "collection": str(args.get("collection") or settings.get("collection") or ""),
        "what": what,
        "reason": reason,
    }
    state.append_jsonl("rejects.jsonl", record)
    return {"logged": True, "ledger": False}


def journal(service, settings, args) -> dict:
    del service, settings
    note = str(args.get("note") or "").strip()
    if not note:
        raise PluginError("A journal note is empty.")
    path = state.append_journal(f"{now_iso()} {note}")
    return {"journal": str(path.name), "ledger": False}


def set_active_role(service, settings, args) -> dict:
    del service
    requested = str(args.get("role") or "").strip()
    name = schemas.ROLE_INDEX.get(requested.casefold())
    configured = settings.get("roles_list") or canonical_roles(settings.get("roles") or "")
    if name is None or name not in configured:
        raise PluginError("The active role must be one of the configured roles.")
    state.update_state(active_role=name)
    return {"active_role": name, "ledger": False}


WRITE_FUNCTIONS = {
    "paranoy_add_evidence": add_evidence,
    "paranoy_add_claim": add_claim,
    "paranoy_add_link": add_link,
    "paranoy_add_contradiction": add_contradiction,
    "paranoy_log_reject": log_reject,
    "paranoy_journal": journal,
    "paranoy_set_active_role": set_active_role,
}


def build(service_factory, settings_factory):
    tools = []
    for name, function in WRITE_FUNCTIONS.items():
        group = "local" if name in schemas.LOCAL_TOOLS else "content"
        tools.append(
            {
                "name": name,
                "schema": schemas.SCHEMAS[name],
                "handler": _wrap(function, service_factory, settings_factory, local=group == "local"),
                "group": group,
            }
        )
    return tools


def _wrap(function, service_factory, settings_factory, *, local: bool):
    def handler(args: dict, **kwargs) -> str:
        del kwargs
        try:
            settings = settings_factory()
            service = None if local else service_factory(settings)
            return dumps(function(service, settings, args or {}))
        except PluginError as exc:
            return dumps({"error": str(exc)})
        except Exception:
            return dumps({"error": "The Paranoy call failed."})

    handler.__name__ = function.__name__
    return handler
