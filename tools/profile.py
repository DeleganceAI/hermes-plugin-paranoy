"""Profile tool. Saves agent_name and agent_about, then publishes a kind 0."""

from __future__ import annotations

from ..client import PluginError, dumps
from ..gates import profile_value
from ..runtime import profile_content, profile_refusal, publish_profile

SAVED_NOTE = "The name and about were saved and will publish on the next try."


def set_profile(service, settings, args, save_setting) -> dict:
    changes = {}
    for field, key in (("name", "agent_name"), ("about", "agent_about")):
        if field in args and args[field] is not None:
            changes[key] = profile_value(field, args[field])
    for key, value in changes.items():
        save_setting(key, value)
    merged = {**settings, **changes}
    try:
        return publish_profile(service, merged)
    except PluginError as exc:
        return {
            "error": profile_refusal(str(exc), service.npub, merged, saved_note=SAVED_NOTE),
            "saved": profile_content(merged),
            "npub": service.npub,
        }


def build(service_factory, settings_factory, save_setting):
    def handler(args: dict, **kwargs) -> str:
        del kwargs
        try:
            settings = settings_factory()
            service = service_factory(settings)
            return dumps(set_profile(service, settings, args or {}, save_setting))
        except PluginError as exc:
            return dumps({"error": str(exc)})
        except Exception:
            return dumps({"error": "The Paranoy call failed."})

    handler.__name__ = "set_profile"
    from .. import schemas

    return [
        {
            "name": "paranoy_set_profile",
            "schema": schemas.SCHEMAS["paranoy_set_profile"],
            "handler": handler,
            "group": "profile",
        }
    ]
