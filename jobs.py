"""Cadence jobs: recurring Paranoy work as one contributor role.

Hermes plugins have no API to create cron jobs. Jobs belong to Hermes'
own scheduler, which the agent drives with its ``cronjob_manage`` tool and
the user drives with ``/cron add`` or ``hermes cron create``. So this module
builds the job: a self-contained prompt, the exact ``cronjob_manage``
arguments, and the matching terminal command. It also keeps a local list of
the jobs it built, under the plugin data directory, so they can be listed
and removed by name.

``paranoy_schedule_job`` and ``/paranoy job`` both call these functions.
"""

from __future__ import annotations

import re
import shlex

from . import schemas, state
from .client import PluginError

TARGET_MAX = 500
FOCUS_MAX = 500
PLUGIN_SKILL = "paranoy:contributor"
ACTIONS = ("create", "list", "remove")
# Actions a person approves, and that never run inside a cron job.
APPROVAL_ACTIONS = frozenset({"create", "remove"})
_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,79}$")
_INTERVAL = re.compile(r"^every\s+([1-9][0-9]*)\s*(m|min|mins|minutes?|h|hr|hrs|hours?|d|days?)$")
_CRON_FIELD = re.compile(r"^[0-9*/,\-]+$")
_ALIASES = {"hourly": "every 1h", "daily": "every 1d", "every hour": "every 1h", "every day": "every 1d"}
WRITE_ROLES = frozenset({"Scout", "Claim Miner", "Linker", "Gap Finder", "Fool"})

ROLE_WORK = {
    "Scout": (
        "new evidence documents",
        "Find real sources for and against the collection's hypotheses. Store each as full "
        "verbatim evidence with paranoy_add_evidence (fetch true with a url when the page is "
        "public). Add a claim with paranoy_add_claim only when the source states one the "
        "collection lacks.",
    ),
    "Claim Miner": (
        "new claims",
        "Go through evidence one document at a time. Add distinct falsifiable claims the "
        "collection lacks with paranoy_add_claim, each grounded in an exact quote. Never add "
        "evidence.",
    ),
    "Linker": (
        "new links",
        "Connect existing claims to existing evidence with paranoy_add_link: SUPPORTS, REBUTS, "
        "or QUALIFIES, with an exact quote and a reason. Add no evidence or claims.",
    ),
    "Gap Finder": (
        "new evidence documents or links",
        "Look for claims with one-sided or missing evidence. Add evidence and REBUTS or "
        "QUALIFIES links that fill those gaps.",
    ),
    "Fool": (
        "new evidence documents or links",
        "Argue the other side. Find the strongest real sources against well-supported claims "
        "and add them as evidence and links.",
    ),
    "Ponder": (
        "journal notes",
        "Read the collection and write reflections with paranoy_journal. Nothing goes to the "
        "ledger.",
    ),
    "Ledger": (
        "records reviewed",
        "Review collection health: duplicates, summary-only evidence, stale areas, one-sided "
        "claims, open contradictions. Write findings with paranoy_update_overview. Do not add "
        "claims, links, or contradictions.",
    ),
}


def check_role(value) -> str:
    name = schemas.ROLE_INDEX.get(str(value or "").strip().casefold())
    if not name:
        raise PluginError(
            "role must be one of Scout, Claim Miner, Linker, Gap Finder, Fool, Ponder, or Ledger."
        )
    return name


def check_target(value) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise PluginError("target must be a whole number of items per run.") from None
    if isinstance(value, float) and value != number:
        raise PluginError("target must be a whole number of items per run.")
    if number < 1 or number > TARGET_MAX:
        raise PluginError(f"target must be from 1 to {TARGET_MAX}.")
    return number


def check_cadence(value) -> str:
    """Return a Hermes schedule string: an interval or a 5-field cron expression."""
    text = " ".join(str(value or "").strip().lower().split())
    text = _ALIASES.get(text, text)
    if _INTERVAL.fullmatch(text):
        return text
    fields = text.split(" ")
    if len(fields) == 5 and all(_CRON_FIELD.fullmatch(field) for field in fields):
        return text
    raise PluginError(
        "cadence must be hourly, daily, an interval like 'every 2h' or 'every 30m', "
        "or a 5-field cron expression like '0 * * * *'."
    )


def check_focus(value) -> str:
    text = " ".join(str(value or "").split())
    if "nsec1" in text:
        raise PluginError("That focus was not saved.")
    if len(text) > FOCUS_MAX:
        raise PluginError(f"focus is at most {FOCUS_MAX} characters.")
    return text


def check_name(value) -> str:
    name = str(value or "").strip().lower()
    if not _NAME.fullmatch(name):
        raise PluginError("Job names use lowercase letters, digits, hyphens, or underscores.")
    return name


def default_name(role: str, collection: str) -> str:
    return check_name(f"paranoy-{role.lower().replace(' ', '-')}-{collection}"[:80])


def validate(args: dict) -> dict:
    """Check create arguments without touching anything. Raises PluginError."""
    from .commands import check_slug

    role = check_role(args.get("role"))
    collection = check_slug(args.get("collection"))
    spec = {
        "role": role,
        "collection": collection,
        "target": check_target(args.get("target")),
        "cadence": check_cadence(args.get("cadence")),
        "focus": check_focus(args.get("focus")),
    }
    spec["name"] = check_name(args["name"]) if args.get("name") else default_name(role, collection)
    return spec


def build_prompt(spec: dict) -> str:
    role, slug, target = spec["role"], spec["collection"], spec["target"]
    unit, work = ROLE_WORK[role]
    lines = [
        f'Paranoy cadence job "{spec["name"]}". Work as the {role} role in the Paranoy '
        f"collection {slug}, following the Paranoy contributor skill ({PLUGIN_SKILL}) for "
        f"{role} only.",
        "",
        "Rules:",
        f'1. Pass collection "{slug}" on every collection-scoped Paranoy call. Do not call '
        "paranoy_set_collection, paranoy_set_mode, paranoy_set_roles, or paranoy_schedule_job. "
        "This job does not change settings.",
        f'2. First call paranoy_set_active_role with role "{role}".',
        f"3. Do only {role} work. {work}",
        "4. Dedupe before every write. Search the collection first (paranoy_search_claims, "
        "paranoy_list_evidence with source or url, paranoy_get_links) and skip anything "
        "already there. Skip a candidate when in doubt and log it with paranoy_log_reject.",
        f"5. Stop after {target} {unit} in this run. Count only items the server accepted. "
        "Stop sooner when there are no more good candidates. Never pad the count.",
    ]
    if spec.get("focus"):
        lines.append(f"6. Focus: {spec['focus']}")
    lines += [
        f"{7 if spec.get('focus') else 6}. If a Paranoy call is refused (not a member, mode "
        "cannot use, writes disabled for unattended cron runs, rate limit), stop and report "
        "the exact error. Do not work around it.",
        "",
        f"Final response: how many {unit} were added, their ids, how many candidates were "
        "skipped and why, and any errors.",
    ]
    return "\n".join(lines)


def cron_arguments(spec: dict) -> dict:
    """Arguments for Hermes' cronjob_manage tool."""
    return {
        "action": "create",
        "name": spec["name"],
        "schedule": spec["cadence"],
        "prompt": build_prompt(spec),
        "skills": [PLUGIN_SKILL],
        "continuity": True,
    }


def cli_command(spec: dict) -> str:
    parts = [
        "hermes",
        "cron",
        "create",
        spec["cadence"],
        build_prompt(spec),
        "--name",
        spec["name"],
        "--skill",
        PLUGIN_SKILL,
    ]
    return " ".join(shlex.quote(part) for part in parts)


def runs_per_day(cadence: str) -> float | None:
    match = _INTERVAL.fullmatch(cadence)
    if match:
        count, unit = int(match.group(1)), match.group(2)[0]
        minutes = count * {"m": 1, "h": 60, "d": 1440}[unit]
        return 1440 / minutes
    fields = cadence.split(" ")
    if len(fields) == 5 and fields[0].isdigit() and fields[1:] == ["*", "*", "*", "*"]:
        return 24.0
    return None


def prerequisites(settings: dict, spec: dict) -> list[str]:
    """What must be true on the host before the job can do its work."""
    notes = []
    role = spec["role"]
    if settings.get("mode") != "contributor":
        notes.append(
            f"Mode is {settings.get('mode')}. {role} tools are visible only in contributor mode. "
            "Change it with paranoy_set_mode (a person approves) or /paranoy mode contributor."
        )
    if role not in (settings.get("roles_list") or []):
        notes.append(
            f"{role} is not in the configured roles. Add it with paranoy_set_roles (a person "
            "approves) or /paranoy roles."
        )
    if role in WRITE_ROLES and not settings.get("allow_unattended_writes"):
        notes.append(
            "Unattended publishing is off, so every write in this job will be refused. Only the "
            "user can turn it on, on the host: set allow_unattended_writes to true in the "
            "paranoy plugin settings (plugins.entries.paranoy.settings). The agent cannot change it."
        )
    return notes


def _saved() -> dict:
    jobs = state.load_state().get("jobs")
    return dict(jobs) if isinstance(jobs, dict) else {}


def list_jobs() -> dict:
    jobs = _saved()
    return {
        "jobs": [jobs[name] for name in sorted(jobs)],
        "note": "These are the jobs this plugin built. Hermes' own list (cronjob_manage "
        "action list, /cron list, or hermes cron list) shows what is actually scheduled.",
    }


def create_job(settings: dict, args: dict, rows: list[dict] | None, pubkey: str | None, *, strict: bool) -> dict:
    from .commands import MembershipError, is_member

    spec = validate(args)
    warning = None
    if rows is None:
        if strict:
            raise PluginError("Collections could not be listed, so the job was not built.")
        warning = "Membership was not checked, because collections could not be listed."
    else:
        members = [row for row in rows if is_member(row, pubkey)]
        if not any(row.get("slug") == spec["collection"] for row in members):
            available = sorted({row["slug"] for row in members})
            if strict:
                raise MembershipError(spec["collection"], available)
            warning = f"This key is not listed as a member of {spec['collection']}."
    jobs = _saved()
    jobs[spec["name"]] = spec
    state.update_state(jobs=jobs)
    result = {
        "job": spec,
        "cronjob_manage": cron_arguments(spec),
        "cli_command": cli_command(spec),
        "prerequisites": prerequisites(settings, spec),
        "next_step": "Create the job in Hermes: call cronjob_manage with the cronjob_manage "
        "arguments exactly, or the user runs cli_command in a terminal. This plugin cannot "
        "schedule it itself.",
    }
    per_day = runs_per_day(spec["cadence"])
    if per_day:
        result["volume"] = {
            "runs_per_day": per_day,
            "max_items_per_day": int(per_day * spec["target"]),
            "note": "Check this against the Paranoy server's rate and account limits.",
        }
    if warning:
        result["warning"] = warning
    return result


def remove_job(args: dict) -> dict:
    name = check_name(args.get("name"))
    jobs = _saved()
    spec = jobs.pop(name, None)
    if spec is None:
        raise PluginError(f"No Paranoy job named {name}. Known jobs: {', '.join(sorted(jobs)) or '(none)'}.")
    state.update_state(jobs=jobs)
    job_id = spec.get("hermes_job_id")
    return {
        "removed": spec,
        "cronjob_manage": {"action": "remove", "job_id": job_id} if job_id else None,
        "next_step": (
            "Remove it from Hermes too: call cronjob_manage with action remove and the job_id "
            f"of the job named {name} (cronjob_manage action list shows it), or the user runs "
            "hermes cron remove <job_id>."
        ),
    }


def record_hermes_id(name: str, job_id: str) -> None:
    jobs = _saved()
    if name in jobs and job_id:
        jobs[name] = {**jobs[name], "hermes_job_id": str(job_id)}
        state.update_state(jobs=jobs)
