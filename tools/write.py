"""Content-write stubs.

These are not a raw publish tool. They do not accept a signed event batch
or a kind 4707 request. A later change will sign kind 4701-4704 here.
"""

from __future__ import annotations

from .. import schemas, signing

_KINDS = {
    "add_evidence": 4701,
    "add_claim": 4702,
    "add_link": 4703,
    "add_contradiction": 4704,
}


def _writer(tool: str, kind: int):
    def handler(args: dict, **kwargs) -> str:
        del kwargs
        try:
            payload = args if isinstance(args, dict) else {}
            return signing.sign_event(kind, payload)
        except Exception:
            return schemas.not_implemented(tool)

    handler.__name__ = tool
    return handler


TOOLS = [
    {
        "name": schema["name"],
        "schema": schema,
        "handler": _writer(schema["name"], _KINDS[schema["name"]]),
        "group": "write",
    }
    for schema in schemas.WRITE_SCHEMAS
]
