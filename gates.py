"""Mode, role, and approval gates.

``check_fn`` hides tools the active mode and role must not see. ``pre_tool_call``
is the second fence: it blocks the same cases, blocks key material, and
escalates ledger writes to a person. Roster, admin-list, and agent-list
writes always ask for approval. Collection, mode, and roles changes from the
agent ask the same way, though they publish nothing.

Kind 4705, 4710, 4713, and 4714 replace the whole list. The handlers fetch,
merge, and preview. Kind 4711 steward grants are not signed. No tool changes
which key is the account Ledger.
"""

from __future__ import annotations

import re

from . import schemas, state
from .client import PluginError

_BLOCKED_ARG_KEYS = frozenset(
    {
        "request",
        "sig",
        "signature",
        "nsec",
        "secret",
        "secret_hex",
        "private_key",
        "events",
        "bot_secret",
        "paranoy_bot_secret_hex",
    }
)
_SLUG = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


def _get(ctx, key: str, default):
    """One setting: ctx config when Hermes has the surface (>= 0.21),
    otherwise the plugin-local overlay in plugin state (Hermes 0.17 has
    no ``ctx.get_config``), otherwise the default."""
    get = getattr(ctx, "get_config", None)
    if callable(get):
        try:
            value = get(key, default=default)
        except Exception:
            value = None
        return default if value is None else value
    overrides = state.load_state().get("plugin_settings") or {}
    if key in overrides:
        return overrides[key]
    return default


def save_setting(ctx, key: str, value) -> None:
    """Write one setting. ctx.set_config is authoritative when it exists;
    otherwise the write lands in plugin-local state."""
    set_config = getattr(ctx, "set_config", None)
    if callable(set_config):
        set_config(key, value)
        return
    stored = state.load_state()
    overrides = dict(stored.get("plugin_settings") or {})
    overrides[key] = value
    state.save_state({**stored, "plugin_settings": overrides})


def as_bool(value, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off", ""}:
            return False
    return default


def parse_csv(value: str) -> list[str]:
    return [part.strip() for part in str(value or "").split(",") if part.strip()]


def canonical_roles(value: str) -> list[str]:
    found = []
    for part in parse_csv(value):
        name = schemas.ROLE_INDEX.get(part.casefold())
        if name and name not in found:
            found.append(name)
    return found


def read_settings(ctx) -> dict:
    """User settings plus the active role. The secret is only a boolean here."""
    defaults = schemas.DEFAULTS
    mode = str(_get(ctx, "mode", defaults["mode"]) or defaults["mode"]).strip().lower()
    if mode not in schemas.MODES:
        mode = "reader"
    roles = canonical_roles(str(_get(ctx, "roles", "") or ""))
    stored = state.load_state().get("active_role") or ""
    active = schemas.ROLE_INDEX.get(str(stored).casefold(), "")
    if active not in roles:
        active = roles[0] if len(roles) == 1 else ""
    try:
        from .signing import secret_configured
    except Exception:
        secret_configured = lambda: False  # noqa: E731
    return {
        "server_url": str(_get(ctx, "server_url", defaults["server_url"]) or defaults["server_url"]),
        "collection": str(_get(ctx, "collection", "") or "").strip(),
        "collections": str(_get(ctx, "collections", "") or "").strip(),
        "managed": parse_csv(str(_get(ctx, "collections", "") or "")),
        "mode": mode,
        "roles": ", ".join(roles),
        "roles_list": roles,
        "active_role": active,
        "owner": str(_get(ctx, "owner", "") or "").strip(),
        "report_to": str(_get(ctx, "report_to", "") or "").strip(),
        "agent_name": str(_get(ctx, "agent_name", "") or "").strip(),
        "agent_about": str(_get(ctx, "agent_about", "") or "").strip(),
        "require_publish_approval": as_bool(_get(ctx, "require_publish_approval", True), True),
        "allow_unattended_writes": as_bool(_get(ctx, "allow_unattended_writes", False), False),
        "auto_sign_terms": as_bool(_get(ctx, "auto_sign_terms", True), True),
        "terms_version": str(_get(ctx, "terms_version", schemas.TERMS_VERSION) or schemas.TERMS_VERSION),
        "terms_sha256": str(_get(ctx, "terms_sha256", schemas.TERMS_SHA256) or schemas.TERMS_SHA256),
        "key_configured": secret_configured(),
    }


def tool_visible(settings: dict, tool_name: str) -> bool:
    """False hides the tool from the model."""
    mode = settings.get("mode") or "reader"
    has_key = bool(settings.get("key_configured"))
    if (
        tool_name in schemas.PROFILE_TOOLS
        or tool_name in schemas.CONFIG_TOOLS
        or tool_name in schemas.JOB_TOOLS
        or tool_name in schemas.BELIEF_TOOLS
    ):
        # Any mode, with or without a pinned collection. Only a key is needed.
        return has_key
    if tool_name in schemas.READ_TOOLS:
        # Collection-scoped reads take a per-call collection, so no pin is needed.
        return has_key
    if tool_name in schemas.LOCAL_TOOLS:
        return mode == "contributor"
    if tool_name in schemas.CONTENT_TOOLS:
        if not has_key:
            return False
        if mode == "ledger":
            return True
        if mode != "contributor":
            return False
        allowed = schemas.ROLE_CONTENT.get(settings.get("active_role") or "", set())
        return tool_name in allowed
    if tool_name == "paranoy_set_admins":
        return mode == "admin" and has_key
    if tool_name in schemas.LEDGER_TOOLS:
        return mode == "ledger" and has_key
    if tool_name in schemas.SHARED_GOVERNANCE:
        return mode in {"admin", "ledger"} and has_key
    return False


def resolve_collection(settings: dict, tool_name: str, args: dict) -> str | None:
    """Pick the collection for one call. list_collections is not pinned.

    A ``collection`` argument targets that collection for this call only. The
    pinned setting does not change. The handlers check membership in the
    target before they read or write.
    """
    if tool_name in schemas.COLLECTION_FREE:
        asked = str((args or {}).get("collection") or "").strip()
        return asked or None
    asked = str((args or {}).get("collection") or "").strip()
    pinned = str(settings.get("collection") or "").strip()
    mode = settings.get("mode")
    if asked and not _SLUG.fullmatch(asked):
        raise PluginError(
            "Collection slugs start with a lowercase letter or digit and "
            "then use lowercase letters, digits, hyphens, or underscores."
        )
    if mode in {"reader", "contributor", "ledger"}:
        chosen = asked or pinned
        if not chosen:
            raise PluginError(
                "No collection is pinned. Pass collection for this call, or pin one with "
                "paranoy_set_collection."
            )
        return chosen
    if mode == "admin":
        chosen = asked or pinned
        if not chosen:
            raise PluginError("Name a collection.")
        managed = settings.get("managed") or []
        if managed and chosen not in managed:
            raise PluginError("That collection is outside the managed set.")
        return chosen
    raise PluginError("The Paranoy mode is not recognized.")


def is_override(settings: dict, tool_name: str, args: dict) -> bool:
    """True when the call targets a collection other than the pinned one."""
    if tool_name in schemas.COLLECTION_FREE:
        return False
    asked = str((args or {}).get("collection") or "").strip()
    return bool(asked) and asked != str(settings.get("collection") or "").strip()


def _looks_sensitive(value) -> bool:
    if isinstance(value, str):
        return value.strip().startswith("nsec1")
    if isinstance(value, dict):
        return args_look_sensitive(value)
    if isinstance(value, list):
        return any(_looks_sensitive(item) for item in value)
    return False


def args_look_sensitive(args: dict) -> bool:
    for key, value in (args or {}).items():
        if str(key).lower() in _BLOCKED_ARG_KEYS:
            return True
        if _looks_sensitive(value):
            return True
    return False


def in_unattended_cron(ctx) -> bool:
    current = getattr(ctx, "current_cron_execution", None)
    if callable(current):
        try:
            if current() is not None:
                return True
        except Exception:
            pass
    # Hermes marks scheduler sessions with this env var "so the approval
    # system can apply cron_mode" (cron/scheduler.py). Hermes versions without
    # ctx.current_cron_execution rely on it entirely.
    import os

    try:
        return os.environ.get("HERMES_CRON_SESSION") == "1"
    except Exception:
        return False


def bare_tool_name(tool_name: str) -> str:
    name = str(tool_name or "")
    if "__" in name:
        name = name.rsplit("__", 1)[-1]
    for separator in ("/", ":"):
        if separator in name:
            name = name.rsplit(separator, 1)[-1]
    return name


def _config_arg_block(settings: dict, tool_name: str, args: dict) -> str | None:
    """Local checks for config tools, so a bad value is refused before approval."""
    from .commands import check_managed, check_mode, check_roles, check_slug

    try:
        if tool_name == "paranoy_set_collection":
            check_managed(settings, check_slug(args.get("slug")))
        elif tool_name == "paranoy_set_mode":
            check_mode(args.get("mode"))
        elif tool_name == "paranoy_set_roles":
            check_roles(args.get("roles"))
        elif tool_name == "paranoy_set_capture":
            key = str(args.get("key") or "").strip()
            value = args.get("value")
            if key and value is not None:
                from . import capture

                capture.validate_capture_setting(key, value)
    except PluginError as exc:
        return str(exc)
    return None


def _job_action(args: dict) -> str:
    return str((args or {}).get("action") or "").strip().lower()


def _job_arg_block(args: dict) -> str | None:
    from . import jobs

    action = _job_action(args)
    try:
        if action == "create":
            jobs.validate(args)
        elif action == "remove":
            jobs.check_name(args.get("name"))
        elif action != "list":
            return "action must be create, list, or remove."
    except PluginError as exc:
        return str(exc)
    return None


def _config_message(settings: dict, tool_name: str, args: dict) -> str:
    if tool_name == "paranoy_set_collection":
        old = settings.get("collection") or "(unset)"
        return (
            f"Approve switching the Paranoy collection from {old} to {args.get('slug')}. "
            "Mode and roles stay the same, and nothing is published."
        )
    if tool_name == "paranoy_set_mode":
        return (
            f"Approve changing the Paranoy mode from {settings.get('mode')} to "
            f"{str(args.get('mode') or '').strip().lower()}. This changes which tools the agent "
            "can use, including writes. Nothing is published."
        )
    if tool_name == "paranoy_set_capture":
        key = str(args.get("key") or "").strip()
        if not key or args.get("value") is None:
            return "Approve reading the Paranoy capture settings. Nothing is published."
        return (
            f"Approve changing the Paranoy capture setting {key} to {args.get('value')}. "
            "Capture publishes messages and answers to the ledger automatically without "
            "per-write approval. Nothing is published by this call itself."
        )
    return (
        "Approve changing the Paranoy contributor roles. This changes which write tools the "
        "agent can use. Nothing is published."
    )


def _arg_block(settings: dict, tool_name: str, args: dict) -> str | None:
    if tool_name in schemas.CONFIG_APPROVAL:
        return _config_arg_block(settings, tool_name, args)
    if tool_name in schemas.JOB_TOOLS:
        return _job_arg_block(args)
    if settings.get("mode") == "ledger" and tool_name in schemas.EXPLICIT_OWNER_TOOLS:
        if not as_bool(args.get("owner_asked"), False):
            return (
                "In ledger mode, claims, links, and contradictions need "
                "owner_asked, because the owner has to ask for that write."
            )
    if settings.get("active_role") == "Gap Finder" and tool_name == "paranoy_add_link":
        relation = str(args.get("relation") or "").strip().upper()
        if relation and relation not in {"REBUTS", "QUALIFIES"}:
            return "Gap Finder links are REBUTS or QUALIFIES."
    return None


def pre_tool_directive(ctx, tool_name, args) -> dict | None:
    name = bare_tool_name(tool_name)
    if name not in schemas.ALL_TOOL_NAMES:
        return None
    payload = args if isinstance(args, dict) else {}
    if args_look_sensitive(payload):
        return {
            "action": "block",
            "message": "Paranoy tools do not accept key material or a pre-signed request.",
        }
    settings = read_settings(ctx)
    if not tool_visible(settings, name):
        return {
            "action": "block",
            "message": f"Paranoy mode {settings['mode']} cannot use {name}.",
        }
    try:
        resolve_collection(settings, name, payload)
    except PluginError as exc:
        return {"action": "block", "message": str(exc)}
    extra = _arg_block(settings, name, payload)
    if extra:
        return {"action": "block", "message": extra}
    if name in schemas.ALWAYS_APPROVE:
        return {
            "action": "approve",
            "message": "This replaces a whole list. Approve the add and remove before it is published.",
        }
    if name in schemas.JOB_TOOLS:
        from .jobs import APPROVAL_ACTIONS

        if _job_action(payload) not in APPROVAL_ACTIONS:
            return None
        if in_unattended_cron(ctx):
            return {
                "action": "block",
                "message": "Paranoy jobs are not created or removed from inside a cron run.",
            }
        if settings["require_publish_approval"]:
            return {
                "action": "approve",
                "message": f"Approve this Paranoy job {_job_action(payload)}: "
                f"{payload.get('name') or (str(payload.get('role') or '') + ' in ' + str(payload.get('collection') or ''))}. "
                "It builds a recurring Hermes job. Nothing is published now.",
            }
        return None
    if name in schemas.BELIEF_TOOLS:
        return _belief_directive(ctx, settings, payload)
    if name in schemas.CONFIG_APPROVAL:
        return _approval(
            ctx,
            settings,
            "Paranoy setting changes are disabled for unattended cron runs.",
            _config_message(settings, name, payload),
        )
    if name not in schemas.APPROVAL_WRITES:
        return None
    return _approval(
        ctx,
        settings,
        "Paranoy writes are disabled for unattended cron runs.",
        "Paranoy write needs a person's approval before it can run.",
    )


def _belief_directive(ctx, settings: dict, args: dict) -> dict | None:
    action = str((args or {}).get("action") or "status").strip().lower()
    if action not in {"status", "scan", "propose", "approve"}:
        return {"action": "block", "message": "action must be status, scan, propose, or approve."}
    if action == "status":
        return None
    if in_unattended_cron(ctx):
        return {"action": "block", "message": "Paranoy belief scans are not run from inside a cron job."}
    if action == "approve":
        ids = args.get("ids")
        shown = ids if isinstance(ids, str) else ", ".join(str(item) for item in (ids or []))
        return _approval(
            ctx,
            settings,
            "Paranoy belief scans are not run from inside a cron job.",
            "Approve filing these beliefs as claims in a private collection: "
            + (shown or "(none)")
            + ". Other proposals stay unfiled. A public collection is refused.",
        )
    return None


def _approval(ctx, settings: dict, cron_message: str, approve_message: str) -> dict | None:
    """Shared by writes and setting changes: cron refusal, then person approval."""
    if in_unattended_cron(ctx):
        if not settings["allow_unattended_writes"]:
            return {"action": "block", "message": cron_message}
        return None
    if settings["require_publish_approval"]:
        return {"action": "approve", "message": approve_message}
    return None


def _npub() -> str | None:
    try:
        from .signing import Signer, load_secret_hex

        return Signer(load_secret_hex()).npub
    except Exception:
        return None


def status_text(settings: dict) -> str:
    """Local status. The secret is not included."""
    npub = _npub()
    lines = [
        "Paranoy",
        f"mode: {settings['mode']}",
        f"server_url: {settings['server_url']}",
        f"collection: {settings['collection'] or '(unset)'}",
        f"collections: {settings['collections'] or '(unset)'}",
        f"roles: {settings['roles'] or '(unset)'}",
        f"active_role: {settings['active_role'] or '(unset)'}",
        f"owner: {settings['owner'] or 'the owner'}",
        f"report_to: {settings['report_to'] or '(unset)'}",
        f"npub: {npub or '(no key)'}",
        f"terms_version: {settings['terms_version']}",
        f"terms_sha256: {settings['terms_sha256']}",
    ]
    return "\n".join(lines)


PROFILE_ANY_MODE = (
    "The profile can be set in any mode, with or without a collection, by calling "
    "paranoy_set_profile. The mode does not need to change for it."
)


def setup_hints(settings: dict, npub: str | None, saved: dict | None = None) -> list[str]:
    """Short setup lines for the model. Empty once nothing obvious is missing.

    Only local facts are used: whether a key is stored, the pinned
    collection, the mode and roles, and whether this plugin has published a
    profile. Membership is a server fact, so it is phrased as what to check.
    """
    saved = saved if saved is not None else state.load_state()
    mode = settings.get("mode") or "reader"
    server = str(settings.get("server_url") or schemas.DEFAULTS["server_url"]).rstrip("/")
    if not settings.get("key_configured") or not npub:
        return [
            "Setup: no agent key is stored, so every Paranoy tool is hidden. "
            "The user has to run `hermes paranoy keygen` in a terminal. You cannot do it. "
            "Never ask for or handle a secret. See the setup skill."
        ]
    lines: list[str] = []
    if not settings.get("collection") and mode in {"reader", "contributor"}:
        lines.append(
            f"Setup: no collection is pinned. Agent npub: {npub}. Call paranoy_list_collections. "
            f"If it is empty, the owner adds this npub on {server}/connect under Your agents, "
            "then ticks it under Where they work, or a collection owner adds the owner first. "
            "Then pin one: you may call paranoy_set_collection with a slug from "
            "paranoy_list_collections, and a person approves it, or the user types "
            "`/paranoy collection <slug>`. If the mode should change, call paranoy_set_mode "
            "the same way, with approval. Mode and roles change only with the user's approval. "
            "Collection-scoped tools also take a collection argument for one call without a pin."
        )
    if mode == "contributor" and not settings.get("roles_list"):
        lines.append(
            "Setup: contributor mode has no roles. Call paranoy_set_roles with the roles the "
            "user asked for, and a person approves it, or the user types `/paranoy roles <roles>`."
        )
    elif mode == "contributor" and not settings.get("active_role"):
        lines.append("Setup: more than one role is set. Pick one with paranoy_set_active_role.")
    if not saved.get("profile_published_at") and not saved.get("terms_published"):
        lines.append(
            "Setup: no profile has been published from this plugin. Offer paranoy_set_profile "
            "with a name and an about that says this key is an agent. A person approves it. "
            + PROFILE_ANY_MODE
        )
    if lines:
        lines.append("For the full steps, use the setup skill.")
    return lines


def prompt_context(settings: dict) -> str:
    """User-message context. It does not include the secret."""
    owner = settings["owner"] or "the owner"
    state_path = ""
    try:
        state_path = str(state.data_dir())
    except Exception:
        state_path = "(plugin data directory unavailable)"
    roles = settings["roles"] or "(none)"
    active = settings["active_role"] or "(none)"
    managed = settings["collections"] or "(every collection this key administers, in admin mode)"
    try:
        hints = setup_hints(settings, _npub() if settings.get("key_configured") else None)
    except Exception:
        hints = []
    if not hints:
        hints = [
            "Setup looks complete. For a not-a-member error, or a setup or profile question, "
            "use the setup skill. paranoy_set_profile works in any mode. To work in another "
            "collection, call paranoy_set_collection with a slug from paranoy_list_collections; "
            "a person approves it. Mode and roles change only with the user's approval."
        ]
    return "\n".join(
        [
            "Paranoy plugin context.",
            f"mode: {settings['mode']}",
            f"server: {settings['server_url']}",
            f"collection: {settings['collection'] or '(unset)'}",
            f"collections: {managed}",
            f"roles: {roles}",
            f"active role: {active}",
            f"owner: {owner}",
            f"report_to: {settings['report_to'] or '(unset)'}",
            f"state dir: {state_path}",
            "The plugin holds the agent key and signs every call. Do not ask for it, print it, or pass a signed request.",
            "Use the paranoy_* tools. Do not call a raw publish tool.",
            schemas.RETRIEVAL_NOTE,
            "Placeholders in the skills mean: "
            f"OWNER={owner}; SERVER_URL={settings['server_url']}; "
            f"COLLECTION={settings['collection'] or '(unset)'}; "
            f"ROLES={roles}; REPORT_TO={settings['report_to'] or '(unset)'}; "
            f"STATE_DIR={state_path}. KEY_PATH is not a file. The plugin holds the key.",
            *hints,
        ]
    )


def _reject_secret_text(value: str) -> bool:
    return value.strip().startswith("nsec1") or str(value).lower() in _BLOCKED_ARG_KEYS


def profile_value(field: str, value) -> str:
    """Check one profile field before it is saved. Raises PluginError."""
    text = str(value if value is not None else "").strip()
    if _reject_secret_text(text) or "nsec1" in text:
        raise PluginError("That value was not saved.")
    if field == "name":
        if not text:
            raise PluginError("The profile name cannot be empty.")
        if "\n" in text or "\r" in text:
            raise PluginError("The profile name has to be one line.")
        if len(text) > schemas.PROFILE_NAME_MAX:
            raise PluginError(f"The profile name is longer than {schemas.PROFILE_NAME_MAX} characters.")
        return text
    if field == "about":
        if not text:
            raise PluginError("The about text cannot be empty.")
        if len(text) > schemas.PROFILE_ABOUT_MAX:
            raise PluginError(f"The about text is longer than {schemas.PROFILE_ABOUT_MAX} characters.")
        return text
    raise PluginError("Profile fields are name and about.")


def profile_text(settings: dict) -> str:
    from .runtime import profile_content

    content = profile_content(settings)
    name_note = "" if settings.get("agent_name") else " (default, agent_name is unset)"
    about_note = "" if settings.get("agent_about") else " (default, agent_about is unset)"
    return "\n".join(
        [
            f"name: {content['name']}{name_note}",
            f"about: {content['about']}{about_note}",
            f"npub: {_npub() or '(no key)'}",
            "Change with /paranoy profile name <text> or /paranoy profile about <text>.",
            "Send it with /paranoy profile publish.",
        ]
    )


PROFILE_USAGE = "Usage: /paranoy profile [name <text>|about <text>|publish]"
USAGE = (
    "Usage: /paranoy status|collection|mode|roles|whoami|profile|job|beliefs\n"
    "       /paranoy capture [status|on|off|<key> <value>]"
)
JOB_USAGE = (
    "Usage: /paranoy job create --role <role> --collection <slug> --target <n> "
    '--cadence <hourly|"every 2h"|"0 * * * *"> [--focus "<text>"] [--name <name>]\n'
    "       /paranoy job list\n"
    "       /paranoy job remove <name>"
)


def _profile_command(ctx, settings: dict, parts: list[str], service_factory) -> str:
    from .commands import save_profile
    from .runtime import profile_content, publish_profile

    sub = parts[1].lower() if len(parts) > 1 else ""
    value = " ".join(parts[2:]).strip()
    if not sub:
        return profile_text(settings)
    if sub in {"name", "about"}:
        if not value:
            return f"{sub}: {profile_content(settings)[sub]}"
        try:
            merged = save_profile(settings, {sub: value}, lambda key, value: save_setting(ctx, key, value))
        except PluginError as exc:
            return str(exc)
        cleaned = profile_content(merged)[sub]
        return f"{sub}: {cleaned}\nSaved. Run /paranoy profile publish to send it."
    if sub == "publish":
        if value:
            return PROFILE_USAGE
        if not settings.get("key_configured") or service_factory is None:
            return "No agent key is stored in Hermes yet. Run hermes paranoy keygen in a terminal."
        from .runtime import profile_refusal

        try:
            service = service_factory(settings)
            result = publish_profile(service, settings)
        except PluginError as exc:
            return profile_refusal(
                str(exc),
                _npub() or "(no key)",
                settings,
                saved_note="The saved name and about go out on the next /paranoy profile publish.",
            )
        profile = result.get("profile") or {}
        return "\n".join(
            [
                f"Published profile {result.get('event_id') or ''}".rstrip() + ".",
                f"name: {profile.get('name')}",
                f"about: {profile.get('about')}",
            ]
        )
    return PROFILE_USAGE


def _listing(settings: dict, service_factory):
    """list_collections rows and the pubkey, or (None, None) when unavailable."""
    from .commands import membership_rows

    if not settings.get("key_configured") or service_factory is None:
        return None, None
    try:
        service = service_factory(settings)
        return membership_rows(service.call("list_collections", {})), service.pubkey
    except Exception:
        return None, None


def _slash_status(ctx, settings, argument, parts, service_factory):
    if argument:
        return USAGE
    return status_text(settings)


def _slash_collection(ctx, settings, argument, parts, service_factory):
    from . import commands

    if not argument:
        return f"collection: {settings['collection'] or '(unset)'}"
    try:
        commands.check_slug(argument)
        commands.check_managed(settings, argument)
    except PluginError as exc:
        return str(exc)
    rows, pubkey = _listing(settings, service_factory)
    try:
        result = commands.set_collection(settings, argument, lambda key, value: save_setting(ctx, key, value), rows, pubkey, strict=False)
    except PluginError as exc:
        return str(exc)
    lines = [f"collection: {result['collection']}"]
    if result.get("warning"):
        lines.append(result["warning"])
    return "\n".join(lines)


def _slash_mode(ctx, settings, argument, parts, service_factory):
    from . import commands

    if not argument:
        return f"mode: {settings['mode']}"
    try:
        result = commands.set_mode(settings, argument, lambda key, value: save_setting(ctx, key, value))
    except PluginError as exc:
        return str(exc)
    return "\n".join([f"mode: {result['mode']}", *commands.describe_visibility(result)])


def _slash_roles(ctx, settings, argument, parts, service_factory):
    from . import commands

    if not argument:
        return f"roles: {settings['roles'] or '(unset)'}"
    try:
        result = commands.set_roles(settings, argument, lambda key, value: save_setting(ctx, key, value))
    except PluginError as exc:
        return str(exc)
    return "\n".join([f"roles: {', '.join(result['roles'])}", *commands.describe_visibility(result)])


def _slash_whoami(ctx, settings, argument, parts, service_factory):
    from .commands import whoami

    npub = whoami(settings, _npub())["npub"]
    if not npub:
        return "No agent key is stored in Hermes yet. This command does not print a secret."
    return f"npub: {npub}"


def _slash_profile(ctx, settings, argument, parts, service_factory):
    return _profile_command(ctx, settings, parts, service_factory)


def _job_flags(tokens: list[str]) -> dict:
    allowed = {"role", "collection", "target", "cadence", "focus", "name"}
    args: dict = {}
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if not token.startswith("--") or token[2:] not in allowed or index + 1 >= len(tokens):
            raise PluginError(JOB_USAGE)
        args[token[2:]] = tokens[index + 1]
        index += 2
    return args


def _dispatch_cron(ctx, arguments: dict):
    """Create the Hermes job through ctx.dispatch_tool when this Hermes has it."""
    dispatch = getattr(ctx, "dispatch_tool", None)
    if not callable(dispatch):
        return None
    try:
        raw = dispatch("cronjob_manage", arguments)
    except Exception:
        return None
    if isinstance(raw, str):
        import json

        try:
            raw = json.loads(raw)
        except ValueError:
            return None
    return raw if isinstance(raw, dict) else None


def _slash_job(ctx, settings, argument, parts, service_factory):
    import shlex

    from . import jobs

    try:
        tokens = shlex.split(argument)
    except ValueError:
        return JOB_USAGE
    sub = tokens[0].lower() if tokens else ""
    try:
        if sub == "list" and len(tokens) == 1:
            listed = jobs.list_jobs()["jobs"]
            if not listed:
                return "No Paranoy jobs. Hermes' own list: /cron list"
            return "\n".join(
                f"{job['name']}: {job['role']} in {job['collection']}, {job['target']} per run, "
                f"{job['cadence']}" + (f", job id {job['hermes_job_id']}" if job.get("hermes_job_id") else "")
                for job in listed
            )
        if sub == "remove" and len(tokens) == 2:
            removed = jobs.remove_job({"name": tokens[1]})
            job_id = removed["removed"].get("hermes_job_id")
            if job_id and _dispatch_cron(ctx, {"action": "remove", "job_id": job_id}) is not None:
                return f"Removed {tokens[1]} and Hermes job {job_id}."
            return f"Removed {tokens[1]} from the plugin list. " + removed["next_step"]
        if sub == "create":
            args = _job_flags(tokens[1:])
            jobs.validate(args)
            rows, pubkey = _listing(settings, service_factory)
            result = jobs.create_job(settings, args, rows, pubkey, strict=False)
        else:
            return JOB_USAGE
    except PluginError as exc:
        return str(exc)
    spec = result["job"]
    lines = [f"Built {spec['name']}: {spec['role']} in {spec['collection']}, {spec['target']} per run, {spec['cadence']}."]
    created = _dispatch_cron(ctx, result["cronjob_manage"])
    job_id = (created or {}).get("job_id") if (created or {}).get("success") else None
    if job_id:
        jobs.record_hermes_id(spec["name"], job_id)
        lines.append(f"Scheduled in Hermes as job {job_id}.")
    else:
        lines.append("Schedule it in Hermes with this terminal command:")
        lines.append(result["cli_command"])
    if result.get("volume"):
        volume = result["volume"]
        lines.append(f"Up to {volume['max_items_per_day']} items a day. {volume['note']}")
    for note in [result.get("warning"), *result["prerequisites"]]:
        if note:
            lines.append("Before it can work: " + note)
    return "\n".join(lines)


def _slash_beliefs(ctx, settings, argument, parts, service_factory):
    del ctx
    from . import beliefs

    sub = parts[1].lower() if len(parts) > 1 else "status"
    rest = " ".join(parts[2:]).strip()
    try:
        if sub == "status":
            return beliefs.format_status(beliefs.status())
        if sub == "scan":
            return beliefs.format_scan(beliefs.scan(rest or None))
        if sub == "approve":
            if not settings.get("key_configured") or service_factory is None:
                return "No agent key is stored in Hermes yet. Run hermes paranoy keygen in a terminal."
            ids = rest.split()
            result = beliefs.approve(service_factory(settings), settings, ids, None)
            return beliefs.format_approve(result)
    except PluginError as exc:
        return str(exc)
    return (
        "Usage: /paranoy beliefs [status|scan [text]|approve <id>]\n"
        "Nothing is filed until approve. The destination is a private collection you own."
    )


def _slash_keygen(ctx, settings, argument, parts, service_factory):
    return "Keygen is a terminal command: hermes paranoy keygen"


# Every /paranoy subcommand. schemas.SLASH_TOOLS maps each one, except the
# terminal-only keygen, to the agent tool that does the same thing.
def _slash_capture(ctx, settings, argument, parts, service_factory):
    from . import capture

    tokens = [part for part in parts[1:] if part]
    if not tokens or tokens[0].lower() == "status":
        return capture.capture_status_text(ctx, settings)
    if len(tokens) == 1 and tokens[0].lower() in {"on", "off"}:
        capture.set_capture_setting("capture_enabled", tokens[0].lower())
        return capture.capture_status_text(ctx, settings)
    if len(tokens) != 2:
        return "Usage: /paranoy capture [status|on|off|<key> <value>]"
    try:
        capture.set_capture_setting(tokens[0].lower(), tokens[1])
    except PluginError as exc:
        return str(exc)
    return capture.capture_status_text(ctx, settings)


SLASH_COMMANDS = {
    "status": _slash_status,
    "collection": _slash_collection,
    "mode": _slash_mode,
    "roles": _slash_roles,
    "whoami": _slash_whoami,
    "profile": _slash_profile,
    "job": _slash_job,
    "beliefs": _slash_beliefs,
    "keygen": _slash_keygen,
    "capture": _slash_capture,
}
PROFILE_SUBCOMMANDS = ("", "name", "about", "publish")
JOB_SUBCOMMANDS = ("", "create", "list", "remove")
BELIEF_SUBCOMMANDS = ("", "status", "scan", "approve")


def slash_result(ctx, raw_args: str, service_factory=None, session_id: str | None = None) -> str:
    """``/paranoy`` subcommands. Secret-looking arguments are not saved or echoed.

    ``service_factory`` is used by ``profile publish`` and to check membership
    for ``collection``. Typing a command is the person's approval for it.
    """
    settings = read_settings(ctx)
    parts = (raw_args or "").split()
    command = parts[0].lower() if parts else "status"
    argument = " ".join(parts[1:]).strip()
    if argument and _reject_secret_text(argument):
        return "That value was not saved."
    handler = SLASH_COMMANDS.get(command)
    if handler is None:
        return USAGE
    from .beliefs import bind_session, reset_session

    token = bind_session(session_id)
    try:
        return handler(ctx, settings, argument, parts, service_factory)
    finally:
        reset_session(token)
