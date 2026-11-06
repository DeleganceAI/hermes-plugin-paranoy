"""Admin and ledger writes. Replace-whole-list kinds are fetched, merged, then signed."""

from __future__ import annotations

from .. import schemas, state
from ..client import PluginError, dumps
from ..gates import as_bool, resolve_collection
from ..merge import (
    MergeError,
    merge_account_agents,
    merge_admins,
    merge_collection_agents,
    merge_members,
)
from ..runtime import assert_membership, publish, reject_replacement_list


def _collection(settings, tool_name, args) -> str:
    return resolve_collection(settings, tool_name, args)


def _members_row(service, collection: str) -> dict:
    roster = service.call("list_members", {"collection": collection})
    if not isinstance(roster, dict) or not isinstance(roster.get("members"), list):
        raise PluginError("The current member list could not be fetched, so nothing was published.")
    return roster


def _change_members(service, settings, args, *, remove: bool) -> dict:
    reject_replacement_list(args)
    collection = _collection(settings, "paranoy_add_members", args)
    if settings.get("mode") == "admin":
        assert_membership(service, collection, administer=True)
    roster = _members_row(service, collection)
    if settings.get("mode") == "ledger" and not roster.get("can_manage_members"):
        raise PluginError("This key cannot change members of that collection.")
    try:
        planned = merge_members(
            roster.get("members"),
            add=None if remove else args.get("add"),
            remove=args.get("remove") if remove else None,
            confirm_owner_only=as_bool(args.get("confirm_owner_only"), False),
        )
    except MergeError as exc:
        raise PluginError(str(exc)) from exc
    event = service.sign(
        4705,
        {"collection": collection, "members": planned["members"]},
    )
    published = dict(publish(service, settings, [event]))
    published["preview"] = {
        "added": planned["added"],
        "removed": planned["removed"],
        "members": planned["members"],
    }
    return published


def add_members(service, settings, args) -> dict:
    return _change_members(service, settings, args, remove=False)


def remove_members(service, settings, args) -> dict:
    return _change_members(service, settings, args, remove=True)


def set_topic(service, settings, args) -> dict:
    collection = _collection(settings, "paranoy_set_topic", args)
    topic = str(args.get("topic") or "").strip()
    if "public" not in args:
        raise PluginError("set_topic needs a public flag.")
    if not topic:
        raise PluginError("A topic is required.")
    if len(topic) > 140:
        raise PluginError("A topic is at most 140 characters.")
    if settings.get("mode") == "admin":
        assert_membership(service, collection, administer=True)
    else:
        roster = _members_row(service, collection)
        if not roster.get("can_manage_members") and roster.get("role") not in {"owner", "admin"}:
            raise PluginError("This key cannot change the topic of that collection.")
    event = service.sign(
        4709,
        {"collection": collection, "topic": topic, "public": as_bool(args.get("public"), False)},
    )
    published = dict(publish(service, settings, [event]))
    published["stored_kind"] = 4709
    return published


def set_admins(service, settings, args) -> dict:
    reject_replacement_list(args)
    collection = _collection(settings, "paranoy_set_admins", args)
    row = assert_membership(service, collection, administer=True)
    roster = _members_row(service, collection)
    if not roster.get("can_manage_admins") and roster.get("role") != "owner":
        raise PluginError("Only the owner changes the admin list.")
    owner = row.get("owner") or next(
        (member.get("pubkey") for member in roster.get("members") or [] if member.get("role") == "owner"),
        None,
    )
    try:
        planned = merge_admins(
            row.get("admins"),
            str(owner or ""),
            add=args.get("add"),
            remove=args.get("remove"),
        )
    except MergeError as exc:
        raise PluginError(str(exc)) from exc
    event = service.sign(4710, {"collection": collection, "admins": planned["admins"]})
    published = dict(publish(service, settings, [event]))
    published["preview"] = {
        "added": planned["added"],
        "removed": planned["removed"],
        "admins": planned["admins"],
        "owner_excluded": planned["owner_excluded"],
    }
    return published


def create_collection(service, settings, args) -> dict:
    slug = str(args.get("collection") or "").strip()
    if not slug:
        raise PluginError("create_collection needs a slug.")
    topic = str(args.get("topic") or slug).strip()
    if len(topic) > 140:
        raise PluginError("A topic is at most 140 characters.")
    public = as_bool(args.get("public"), False)
    owner = str(args.get("owner") or "").strip()
    initial = [str(item).strip() for item in (args.get("members") or []) if str(item).strip()]
    events = []
    if owner:
        grants = service.call("list_delegations", {})
        authorized = grants.get("for") if isinstance(grants, dict) else None
        if not isinstance(authorized, list) or owner not in authorized:
            raise PluginError(
                "This key can only open a collection for a grantor listed in list_delegations."
            )
        events.append(service.sign(4712, {"collection": slug, "owner": owner}))
    events.append(service.sign(4709, {"collection": slug, "topic": topic, "public": public}))
    if initial:
        events.append(service.sign(4705, {"collection": slug, "members": initial}))
    published = dict(publish(service, settings, events))
    published["created"] = slug
    published["stored_kinds"] = [event["kind"] for event in events]
    return published


def update_overview(service, settings, args) -> dict:
    del service, settings
    text = str(args.get("text") or "")
    if not text.strip():
        raise PluginError("Overview text is empty.")
    path = state.write_overview(text if text.endswith("\n") else text + "\n", replace=as_bool(args.get("replace"), False))
    return {"overview": path.name, "ledger": False}


def set_collection_agents(service, settings, args) -> dict:
    reject_replacement_list(args)
    collection = _collection(settings, "paranoy_set_collection_agents", args)
    current = _fetch_collection_agents(service, collection)
    try:
        planned = merge_collection_agents(current, add=args.get("add"), remove=args.get("remove"))
    except MergeError as exc:
        raise PluginError(str(exc)) from exc
    event = service.sign(4714, {"collection": collection, "agents": planned["agents"]})
    published = dict(publish(service, settings, [event]))
    published["preview"] = planned
    return published


def set_account_agents(service, settings, args) -> dict:
    reject_replacement_list(args)
    if "ledger" in args:
        raise PluginError("This tool cannot change which key is the Ledger.")
    current = _fetch_account(service)
    try:
        planned = merge_account_agents(current, add=args.get("add"), remove=args.get("remove"))
    except MergeError as exc:
        raise PluginError(str(exc)) from exc
    event = service.sign(4713, {"ledger": planned["ledger"], "agents": planned["agents"]})
    published = dict(publish(service, settings, [event]))
    published["preview"] = {
        "added": planned["added"],
        "removed": planned["removed"],
        "agents": planned["agents"],
        "ledger_unchanged": True,
    }
    return published


def _fetch_account(service):
    """The public tool list has no account-agent read. Refuse rather than invent one."""
    fetcher = getattr(service, "fetch_account", None)
    if callable(fetcher):
        return fetcher()
    return None


def _fetch_collection_agents(service, collection: str):
    fetcher = getattr(service, "fetch_collection_agents", None)
    if callable(fetcher):
        return fetcher(collection)
    return None


GOVERNANCE_FUNCTIONS = {
    "paranoy_set_topic": set_topic,
    "paranoy_add_members": add_members,
    "paranoy_remove_members": remove_members,
    "paranoy_set_admins": set_admins,
    "paranoy_create_collection": create_collection,
    "paranoy_update_overview": update_overview,
    "paranoy_set_collection_agents": set_collection_agents,
    "paranoy_set_account_agents": set_account_agents,
}


def build(service_factory, settings_factory):
    tools = []
    for name, function in GOVERNANCE_FUNCTIONS.items():
        local = name == "paranoy_update_overview"
        tools.append(
            {
                "name": name,
                "schema": schemas.SCHEMAS[name],
                "handler": _wrap(function, service_factory, settings_factory, local=local),
                "group": "governance",
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
