"""Admin and ledger stubs.

Kind 4711 steward grants are not represented. replace_account_agents cannot
change which key is the Ledger. Replace-whole-list kinds are 4705, 4710,
4713, and 4714; fetch-merge-preview is a later change.
"""

from __future__ import annotations

from .. import schemas, signing

# Reads go out as kind 4707 requests. Mutations name the event kind they
# will sign later. create_collection uses 4712 when an owner pubkey is set;
# creating for this key is the same stub until the client exists.
_REQUESTS = {"list_members", "list_delegations"}
_KINDS = {
    "replace_members": 4705,
    "set_topic": 4709,
    "replace_admins": 4710,
    "create_collection": 4712,
    "replace_account_agents": 4713,
    "replace_collection_agents": 4714,
}


def _handler_for(tool: str):
    def handler(args: dict, **kwargs) -> str:
        del kwargs
        try:
            payload = args if isinstance(args, dict) else {}
            if tool in _REQUESTS:
                return signing.sign_request(tool, payload)
            if tool == "create_collection" and not str(payload.get("owner") or "").strip():
                # A collection this key owns is not a kind 4712 found event.
                # Kind 4712 is only the steward path, when owner is set.
                return schemas.not_implemented(tool)
            return signing.sign_event(_KINDS[tool], payload)
        except Exception:
            return schemas.not_implemented(tool)

    handler.__name__ = tool
    return handler


TOOLS = [
    {
        "name": schema["name"],
        "schema": schema,
        "handler": _handler_for(schema["name"]),
        "group": "governance",
    }
    for schema in schemas.GOVERNANCE_SCHEMAS
]
