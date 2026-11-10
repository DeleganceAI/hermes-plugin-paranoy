"""Agent versions of the /paranoy config commands: collection, mode, roles, whoami.

Each handler calls the same function in ``commands`` as the slash command.
The pre_tool_call hook asks a person before collection, mode, and roles
changes. Nothing here publishes to the server.
"""

from __future__ import annotations

from .. import commands, schemas
from ..client import PluginError, dumps


def set_collection(service, settings, args, save_setting) -> dict:
    slug = commands.check_slug(args.get("slug"))
    commands.check_managed(settings, slug)
    rows = commands.membership_rows(service.call("list_collections", {}))
    try:
        return commands.set_collection(settings, slug, save_setting, rows, service.pubkey, strict=True)
    except commands.MembershipError as exc:
        return {
            "error": str(exc),
            "collection": settings.get("collection") or None,
            "available": exc.available,
        }


def set_mode(service, settings, args, save_setting) -> dict:
    del service
    return commands.set_mode(settings, args.get("mode"), save_setting)


def set_roles(service, settings, args, save_setting) -> dict:
    del service
    return commands.set_roles(settings, args.get("roles"), save_setting)


def whoami(service, settings, args, save_setting) -> dict:
    del args, save_setting
    return commands.whoami(settings, service.npub)


FUNCTIONS = {
    "paranoy_set_collection": set_collection,
    "paranoy_set_mode": set_mode,
    "paranoy_set_roles": set_roles,
    "paranoy_whoami": whoami,
}


def build(service_factory, settings_factory, save_setting):
    tools = []
    for name in schemas.CONFIG_TOOLS:
        tools.append(
            {
                "name": name,
                "schema": schemas.SCHEMAS[name],
                "handler": _wrap(FUNCTIONS[name], service_factory, settings_factory, save_setting),
                "group": "config",
            }
        )
    return tools


def _wrap(function, service_factory, settings_factory, save_setting):
    def handler(args: dict, **kwargs) -> str:
        del kwargs
        try:
            settings = settings_factory()
            service = service_factory(settings)
            return dumps(function(service, settings, args or {}, save_setting))
        except PluginError as exc:
            return dumps({"error": str(exc)})
        except Exception:
            return dumps({"error": "The Paranoy call failed."})

    handler.__name__ = function.__name__
    return handler
