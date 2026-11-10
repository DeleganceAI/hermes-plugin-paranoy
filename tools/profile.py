"""Profile tool. Saves agent_name and agent_about, then publishes a kind 0."""

from __future__ import annotations

from ..client import PluginError, dumps
from ..commands import save_profile
from ..gates import as_bool
from ..runtime import profile_content, profile_refusal, publish_profile

SAVED_NOTE = "The name and about were saved and will publish on the next try."


def set_profile(service, settings, args, save_setting) -> dict:
    """Same logic as /paranoy profile name, about, and publish."""
    merged = save_profile(settings, args, save_setting)
    if not as_bool(args.get("publish"), True):
        return {
            "saved": profile_content(merged),
            "published": False,
            "note": "Saved. Call paranoy_set_profile with no arguments to publish it.",
        }
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
