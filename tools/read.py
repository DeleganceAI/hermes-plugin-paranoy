"""Read-tool stubs. They ask the signing client for a kind 4707 request and stop there."""

from __future__ import annotations

from .. import schemas, signing


def _reader(tool: str):
    def handler(args: dict, **kwargs) -> str:
        del kwargs
        try:
            payload = args if isinstance(args, dict) else {}
            return signing.sign_request(tool, payload)
        except Exception:
            return schemas.not_implemented(tool)

    handler.__name__ = tool
    return handler


TOOLS = [
    {
        "name": schema["name"],
        "schema": schema,
        "handler": _reader(schema["name"]),
        "group": "read",
    }
    for schema in schemas.READ_SCHEMAS
]
