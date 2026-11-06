"""Internal signing client.

Never registered as a tool. The model does not get a generic sign-this-blob
tool, a raw publish tool, or a parameter that accepts a signed kind 4707
request. A later change will read PARANOY_BOT_SECRET_HEX through
``agent.secret_scope.get_secret`` inside these functions and will not return
or log it. This module does not read the secret, the environment, ``.env``,
or ``auth.json``, and it does not import coincurve or httpx yet.
"""

from __future__ import annotations

import json


def describe() -> str:
    """Status label. The client does not sign yet."""
    return "not implemented"


def sign_event(kind: int, content: dict | None = None, **kwargs) -> str:
    """Sign one Nostr event. Stub: no signature is produced.

    ``content`` and ``kwargs`` are ignored so a secret or a pre-signed blob
    cannot leak into the result.
    """
    del content, kwargs
    kind_value = kind if isinstance(kind, int) else None
    return json.dumps(
        {
            "error": "not implemented",
            "component": "signing",
            "operation": "sign_event",
            "kind": kind_value,
            "detail": (
                "The signing client is not implemented. No event was signed "
                "and no secret was read."
            ),
        }
    )


def sign_request(tool: str, args: dict | None = None, **kwargs) -> str:
    """Sign one kind 4707 tool request. Stub, and not a model-facing tool.

    The model cannot supply this request. ``args`` is ignored.
    """
    del args, kwargs
    return json.dumps(
        {
            "error": "not implemented",
            "component": "signing",
            "operation": "sign_request",
            "tool": str(tool),
            "detail": (
                "Kind 4707 request signing is not implemented. "
                "The model cannot supply this request."
            ),
        }
    )
