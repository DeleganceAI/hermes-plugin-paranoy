"""Mode, role, and approval gates.

``check_fn`` hides tools the active mode and role must not see. ``pre_tool_call``
is the second fence: it blocks the same cases, blocks key material, and
escalates ledger writes to a person. Roster, admin-list, and agent-list
writes always ask for approval.

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
    try:
        value = ctx.get_config(key, default=default)
    except Exception:
        return default
    if value is None:
        return default
    return value


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
    if tool_name in schemas.READ_TOOLS:
        if not has_key:
            return False
        if tool_name not in schemas.COLLECTION_FREE and mode in {"reader", "contributor"}:
            return bool(settings.get("collection"))
        return True
    if tool_name in schemas.LOCAL_TOOLS:
        return mode == "contributor"
    if tool_name in schemas.CONTENT_TOOLS:
        if not has_key:
            return False
        if mode == "ledger":
            return True
        if mode != "contributor" or not settings.get("collection"):
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
    """Default and reject collection arguments. list_collections is not pinned."""
    if tool_name in schemas.COLLECTION_FREE:
        asked = str((args or {}).get("collection") or "").strip()
        return asked or None
    asked = str((args or {}).get("collection") or "").strip()
    pinned = str(settings.get("collection") or "").strip()
    mode = settings.get("mode")
    if mode in {"reader", "contributor"}:
        if not pinned:
            raise PluginError("Pin a collection before using this tool.")
        if asked and asked != pinned:
            raise PluginError("This mode is pinned to one collection.")
        return pinned
    if mode == "ledger":
        if pinned:
            if asked and asked != pinned:
                raise PluginError("Ledger mode is using the named collection.")
            return pinned
        if not asked:
            raise PluginError("Name a collection, or pin one in settings.")
        return asked
    if mode == "admin":
        chosen = asked or pinned
        if not chosen:
            raise PluginError("Name a collection.")
        managed = settings.get("managed") or []
        if managed and chosen not in managed:
            raise PluginError("That collection is outside the managed set.")
        return chosen
    raise PluginError("The Paranoy mode is not recognized.")


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
    if not callable(current):
        return False
    try:
        return current() is not None
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


def _arg_block(settings: dict, tool_name: str, args: dict) -> str | None:
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
    if name not in schemas.APPROVAL_WRITES:
        return None
    if in_unattended_cron(ctx) and not settings["allow_unattended_writes"]:
        return {"action": "block", "message": "Paranoy writes are disabled for unattended cron runs."}
    if in_unattended_cron(ctx) and settings["allow_unattended_writes"]:
        return None
    if settings["require_publish_approval"]:
        return {
            "action": "approve",
            "message": "Paranoy write needs a person's approval before it can run.",
        }
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
        ]
    )


def _reject_secret_text(value: str) -> bool:
    return value.strip().startswith("nsec1") or str(value).lower() in _BLOCKED_ARG_KEYS


def slash_result(ctx, raw_args: str) -> str:
    """``/paranoy`` subcommands. Secret-looking arguments are not saved or echoed."""
    settings = read_settings(ctx)
    parts = (raw_args or "").split()
    command = parts[0].lower() if parts else "status"
    argument = " ".join(parts[1:]).strip()
    if argument and _reject_secret_text(argument):
        return "That value was not saved."
    if command == "status" and not argument:
        return status_text(settings)
    if command == "collection":
        if not argument:
            return f"collection: {settings['collection'] or '(unset)'}"
        if not _SLUG.fullmatch(argument):
            return (
                "Collection slugs start with a lowercase letter or digit and "
                "then use lowercase letters, digits, hyphens, or underscores."
            )
        ctx.set_config("collection", argument)
        return f"collection: {argument}"
    if command == "mode":
        if not argument:
            return f"mode: {settings['mode']}"
        mode = argument.lower()
        if mode not in schemas.MODES:
            return "Mode must be reader, contributor, admin, or ledger."
        ctx.set_config("mode", mode)
        return f"mode: {mode}"
    if command == "roles":
        if not argument:
            return f"roles: {settings['roles'] or '(unset)'}"
        names = canonical_roles(argument)
        unknown = [
            part
            for part in parse_csv(argument)
            if schemas.ROLE_INDEX.get(part.casefold()) is None
        ]
        if unknown or not names:
            return "Roles must be chosen from Scout, Claim Miner, Linker, Gap Finder, Fool, Ponder, and Ledger."
        ctx.set_config("roles", ", ".join(names))
        return f"roles: {', '.join(names)}"
    if command == "whoami":
        npub = _npub()
        if not npub:
            return "No agent key is stored in Hermes yet. This command does not print a secret."
        return f"npub: {npub}"
    if command == "keygen":
        return "Keygen is a terminal command: hermes paranoy keygen"
    return "Usage: /paranoy status|collection|mode|roles|whoami"
