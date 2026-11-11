"""Read tools. Each one signs a kind 4707 request inside the client."""

from __future__ import annotations

from .. import schemas
from ..client import PluginError, dumps
from ..gates import is_override, resolve_collection
from ..runtime import assert_membership, page_event


def _scoped(service, settings, tool_name, args) -> str:
    collection = resolve_collection(settings, tool_name, args)
    if settings.get("mode") == "admin":
        assert_membership(service, collection, administer=True)
    elif is_override(settings, tool_name, args) or not settings.get("collection"):
        # A per-call collection, or no pin: check membership in the target.
        assert_membership(service, collection, administer=False)
    return collection


def _pass(args: dict, keys: tuple[str, ...], collection: str | None = None) -> dict:
    payload = {}
    if collection is not None:
        payload["collection"] = collection
    for key in keys:
        if key not in args or args[key] in (None, ""):
            continue
        payload[key] = args[key]
    return payload


def list_collections(service, settings, args) -> dict:
    del settings, args
    return service.call("list_collections", {})


def list_public_collections(service, settings, args) -> dict:
    del args
    from ..public import list_for_agent

    return list_for_agent(service, settings)


def list_members(service, settings, args) -> dict:
    collection = _scoped(service, settings, "paranoy_list_members", args)
    return service.call("list_members", {"collection": collection})


def list_delegations(service, settings, args) -> dict:
    del settings, args
    return service.call("list_delegations", {})


def search_claims(service, settings, args) -> dict:
    collection = _scoped(service, settings, "paranoy_search_claims", args)
    payload = _pass(args, ("text", "method", "limit"), collection)
    if not str(payload.get("text") or "").strip():
        raise PluginError("search_claims needs text.")
    result = service.call("search_claims", payload)
    if isinstance(result, dict):
        result = {**result, "note": schemas.RETRIEVAL_NOTE}
    return result


def list_claims(service, settings, args) -> dict:
    collection = _scoped(service, settings, "paranoy_list_claims", args)
    result = service.call("list_claims", _pass(args, ("sort", "limit", "offset"), collection))
    if isinstance(result, dict):
        result = {**result, "note": schemas.RETRIEVAL_NOTE}
    return result


def list_evidence(service, settings, args) -> dict:
    collection = _scoped(service, settings, "paranoy_list_evidence", args)
    return service.call(
        "list_evidence",
        _pass(args, ("ungrounded_only", "source", "limit", "offset"), collection),
    )


def get_event(service, settings, args) -> dict:
    del settings
    event_id = str(args.get("id") or "").strip()
    if not event_id:
        raise PluginError("get_event needs an id.")
    return page_event(service, event_id)


def get_links(service, settings, args) -> dict:
    collection = _scoped(service, settings, "paranoy_get_links", args)
    return service.call(
        "get_links",
        _pass(args, ("to_id", "from_id", "relation", "limit", "offset"), collection),
    )


def search_contradictions(service, settings, args) -> dict:
    collection = _scoped(service, settings, "paranoy_search_contradictions", args)
    result = service.call(
        "search_contradictions",
        _pass(args, ("claim", "text", "limit"), collection),
    )
    if isinstance(result, dict):
        result = {**result, "note": schemas.RETRIEVAL_NOTE}
    return result


def check_proof(service, settings, args) -> dict:
    collection = _scoped(service, settings, "paranoy_check_proof", args)
    proof = args.get("proof")
    if not isinstance(proof, dict):
        raise PluginError("check_proof needs a proof object.")
    return service.call("check_proof", {"collection": collection, "proof": proof})


def fetch_url(service, settings, args) -> dict:
    del settings
    url = str(args.get("url") or "").strip()
    if not url:
        raise PluginError("fetch_url needs a url.")
    return service.call("fetch_url", {"url": url})


def status(service, settings, args) -> dict:
    del args
    listed = service.call("list_collections", {})
    grants = service.call("list_delegations", {})
    return {
        "mode": settings.get("mode"),
        "collection": settings.get("collection") or None,
        "collections": settings.get("collections") or None,
        "roles": settings.get("roles_list") or [],
        "active_role": settings.get("active_role") or None,
        "npub": service.npub,
        "membership": listed.get("result") if isinstance(listed, dict) else listed,
        "terms_version": settings.get("terms_version"),
        "terms_sha256": settings.get("terms_sha256"),
        "steward_grants": grants,
        "public_collections": _cached_public(),
        "register_public_collections": settings.get("register_public_collections") or "all",
        "note": schemas.RETRIEVAL_NOTE,
    }


def _cached_public():
    from ..public import cached_snapshot

    return cached_snapshot()


READ_FUNCTIONS = {
    "paranoy_list_collections": list_collections,
    "paranoy_list_public_collections": list_public_collections,
    "paranoy_list_members": list_members,
    "paranoy_list_delegations": list_delegations,
    "paranoy_search_claims": search_claims,
    "paranoy_list_claims": list_claims,
    "paranoy_list_evidence": list_evidence,
    "paranoy_get_event": get_event,
    "paranoy_get_links": get_links,
    "paranoy_search_contradictions": search_contradictions,
    "paranoy_check_proof": check_proof,
    "paranoy_fetch_url": fetch_url,
    "paranoy_status": status,
}


def build(service_factory, settings_factory):
    tools = []
    for name, function in READ_FUNCTIONS.items():
        tools.append(
            {
                "name": name,
                "schema": schemas.SCHEMAS[name],
                "handler": _wrap(function, service_factory, settings_factory),
                "group": "read",
            }
        )
    return tools


def _wrap(function, service_factory, settings_factory):
    def handler(args: dict, **kwargs) -> str:
        del kwargs
        try:
            settings = settings_factory()
            service = service_factory(settings)
            return dumps(function(service, settings, args or {}))
        except PluginError as exc:
            return dumps({"error": str(exc)})
        except Exception:
            return dumps({"error": "The Paranoy call failed."})

    handler.__name__ = function.__name__
    return handler
