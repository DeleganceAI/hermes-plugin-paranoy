"""Surface beliefs from the current conversation, and file only approved ones."""

from __future__ import annotations

from .. import beliefs, schemas
from ..client import PluginError, dumps
from ..gates import as_bool


def surface(service, settings, args, session_id: str = "") -> dict:
    action = str((args or {}).get("action") or "status").strip().lower()
    token = beliefs.bind_session(session_id)
    try:
        if action == "status":
            return beliefs.status()
        if action == "consent":
            return beliefs.record_consent(as_bool(args.get("consent"), False))
        if action == "scan":
            return beliefs.scan(args.get("text"), session_id or None)
        if action == "propose":
            return beliefs.propose(args.get("candidates"))
        if action == "approve":
            return beliefs.approve(service, settings, args.get("ids"), args.get("collection"))
    finally:
        beliefs.reset_session(token)
    raise PluginError("action must be status, consent, scan, propose, or approve.")


def build(service_factory, settings_factory):
    name = "paranoy_surface_beliefs"

    def handler(args: dict, **kwargs) -> str:
        try:
            settings = settings_factory()
            session_id = str(kwargs.get("session_id") or "")
            action = str((args or {}).get("action") or "status").strip().lower()
            service = None
            if action == "approve":
                service = service_factory(settings)
            return dumps(surface(service, settings, args or {}, session_id))
        except PluginError as exc:
            return dumps({"error": str(exc)})
        except Exception:
            return dumps({"error": "The Paranoy call failed."})

    handler.__name__ = "surface_beliefs"
    return [
        {
            "name": name,
            "schema": schemas.SCHEMAS[name],
            "handler": handler,
            "group": "beliefs",
        }
    ]
