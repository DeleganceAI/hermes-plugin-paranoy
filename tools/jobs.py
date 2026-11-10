"""paranoy_schedule_job: build, list, and remove cadence jobs. See ``jobs``."""

from __future__ import annotations

from .. import jobs, schemas
from ..client import PluginError, dumps
from ..commands import MembershipError, membership_rows


def schedule_job(service, settings, args) -> dict:
    action = str(args.get("action") or "").strip().lower()
    if action == "list":
        return jobs.list_jobs()
    if action == "remove":
        return jobs.remove_job(args)
    if action == "create":
        jobs.validate(args)
        rows = membership_rows(service.call("list_collections", {}))
        try:
            return jobs.create_job(settings, args, rows, service.pubkey, strict=True)
        except MembershipError as exc:
            return {"error": str(exc), "available": exc.available}
    raise PluginError("action must be create, list, or remove.")


def build(service_factory, settings_factory):
    def handler(args: dict, **kwargs) -> str:
        del kwargs
        try:
            settings = settings_factory()
            service = service_factory(settings)
            return dumps(schedule_job(service, settings, args or {}))
        except PluginError as exc:
            return dumps({"error": str(exc)})
        except Exception:
            return dumps({"error": "The Paranoy call failed."})

    handler.__name__ = "schedule_job"
    return [
        {
            "name": "paranoy_schedule_job",
            "schema": schemas.SCHEMAS["paranoy_schedule_job"],
            "handler": handler,
            "group": "jobs",
        }
    ]
