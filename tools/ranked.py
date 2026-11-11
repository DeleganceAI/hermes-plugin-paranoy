"""Ranked evidence answer. Reads the ledger and publishes nothing."""

from __future__ import annotations

from .. import ranked, schemas
from ..client import PluginError, dumps


def build(service_factory, settings_factory):
    name = "paranoy_ranked_answer"

    def handler(args: dict, **kwargs) -> str:
        del kwargs
        try:
            settings = settings_factory()
            service = service_factory(settings)
            return dumps(ranked.answer(service, settings, args or {}))
        except PluginError as exc:
            return dumps({"error": str(exc)})
        except Exception:
            return dumps({"error": "The Paranoy call failed."})

    handler.__name__ = "ranked_answer"
    return [{"name": name, "schema": schemas.SCHEMAS[name], "handler": handler, "group": "read"}]
