"""Role and mode gates.

``check_fn`` hides tools the active mode must not see. ``pre_tool_call``
repeats those fences so a hidden tool still cannot run. Neither path logs
arguments or secrets.

Kind 4705 (members), 4710 (admins), 4713 (account agents), and 4714
(collection agents) replace the whole list. A later change will fetch the
current list, merge, preview, and always require approval before signing.
This scaffold does not call the server.

Kind 4711 steward grants stay out of v1. The human signs those. No tool may
change which key is the account Ledger.
"""

from __future__ import annotations

from . import schemas

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


def secret_configured() -> bool:
    """True when Hermes has a non-empty bot secret. The value is never returned."""
    try:
        from agent.secret_scope import get_secret
    except Exception:
        return False
    try:
        raw = get_secret(schemas.SECRET_ENV)
    except Exception:
        return False
    if raw is None:
        return False
    return bool(str(raw).strip())


def _get(ctx, key: str, default):
    try:
        value = ctx.get_config(key, default=default)
    except Exception:
        return default
    if value is None:
        return default
    return value


def _as_bool(value, default: bool) -> bool:
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


def read_settings(ctx) -> dict:
    """Plugin settings for gates and status. Secrets are a boolean only."""
    defaults = schemas.DEFAULTS
    mode = str(_get(ctx, "mode", defaults["mode"]) or defaults["mode"]).strip().lower()
    if mode not in schemas.MODES:
        mode = "reader"
    return {
        "server_url": str(_get(ctx, "server_url", defaults["server_url"]) or defaults["server_url"]),
        "collection": str(_get(ctx, "collection", "") or "").strip(),
        "collections": str(_get(ctx, "collections", "") or "").strip(),
        "mode": mode,
        "roles": str(_get(ctx, "roles", "") or "").strip(),
        "owner": str(_get(ctx, "owner", "") or "").strip(),
        "report_to": str(_get(ctx, "report_to", "") or "").strip(),
        "agent_name": str(_get(ctx, "agent_name", "") or "").strip(),
        "agent_about": str(_get(ctx, "agent_about", "") or "").strip(),
        "require_publish_approval": _as_bool(
            _get(ctx, "require_publish_approval", True), True
        ),
        "allow_unattended_writes": _as_bool(
            _get(ctx, "allow_unattended_writes", False), False
        ),
        "auto_sign_terms": _as_bool(_get(ctx, "auto_sign_terms", True), True),
        "terms_version": str(_get(ctx, "terms_version", schemas.TERMS_VERSION) or schemas.TERMS_VERSION),
        "terms_sha256": str(_get(ctx, "terms_sha256", schemas.TERMS_SHA256) or schemas.TERMS_SHA256),
        "key_configured": secret_configured(),
    }


def pin_outside_managed_set(settings: dict) -> bool:
    """Admin pin must be one of ``collections`` when that list is non-empty."""
    if settings.get("mode") != "admin":
        return False
    collection = (settings.get("collection") or "").strip()
    managed = parse_csv(settings.get("collections") or "")
    return bool(collection and managed and collection not in managed)


def collection_ready(settings: dict, tool_name: str) -> bool:
    """Reader and contributor need a pin. Ledger's pin is optional.

    Admin may omit the pin (every collection this key administers when
    ``collections`` is empty). A pin outside the managed set is refused.
    """
    if pin_outside_managed_set(settings):
        return tool_name in schemas.COLLECTION_FREE_TOOLS
    if tool_name in schemas.COLLECTION_FREE_TOOLS:
        return True
    mode = settings.get("mode")
    if mode in {"reader", "contributor"}:
        return bool((settings.get("collection") or "").strip())
    return True


def tool_visible(settings: dict, group: str, tool_name: str) -> bool:
    """False hides the tool from the model. Fail closed on a missing key."""
    if not settings.get("key_configured"):
        return False
    mode = settings.get("mode") or "reader"
    if group == "write" and mode in {"reader", "admin"}:
        return False
    if group == "governance" and mode not in {"admin", "ledger"}:
        return False
    if not collection_ready(settings, tool_name):
        return False
    return True


def bare_tool_name(tool_name: str) -> str:
    name = str(tool_name or "")
    for separator in ("/", ".", ":"):
        if separator in name:
            name = name.rsplit(separator, 1)[-1]
    if "__" in name:
        name = name.rsplit("__", 1)[-1]
    return name


def _string_looks_like_secret(value: str) -> bool:
    return value.strip().startswith("nsec1")


def args_look_sensitive(args: dict) -> bool:
    """True when the model passed key material or a pre-signed blob."""
    for key, value in args.items():
        if str(key).lower() in _BLOCKED_ARG_KEYS:
            return True
        if isinstance(value, str) and _string_looks_like_secret(value):
            return True
        if isinstance(value, dict) and args_look_sensitive(value):
            return True
    return False


def in_unattended_cron(ctx) -> bool:
    """True when this call is inside a Hermes cron run. Never raises."""
    current = getattr(ctx, "current_cron_execution", None)
    if not callable(current):
        return False
    try:
        return current() is not None
    except Exception:
        return False


def pre_tool_directive(ctx, tool_name, args) -> dict | None:
    """Block or escalate. ``approve`` asks a person; it does not auto-approve."""
    name = bare_tool_name(tool_name)
    if name not in schemas.ALL_TOOL_NAMES:
        return None
    payload = args if isinstance(args, dict) else {}
    if args_look_sensitive(payload):
        return {
            "action": "block",
            "message": (
                "Paranoy tools do not accept key material or a pre-signed request."
            ),
        }
    settings = read_settings(ctx)
    mode = settings["mode"]
    if not settings["key_configured"]:
        return {
            "action": "block",
            "message": (
                "Paranoy data tools stay hidden until Hermes has "
                f"{schemas.SECRET_ENV}. Status and keygen still run. "
                "The secret is not shown."
            ),
        }
    if name in schemas.WRITE_TOOL_NAMES and mode in {"reader", "admin"}:
        return {
            "action": "block",
            "message": f"Paranoy mode {mode} does not allow content writes.",
        }
    if name in schemas.GOVERNANCE_TOOL_NAMES and mode not in {"admin", "ledger"}:
        return {
            "action": "block",
            "message": f"Paranoy mode {mode} does not allow governance tools.",
        }
    if not collection_ready(settings, name):
        if pin_outside_managed_set(settings):
            message = "The pinned collection is not in this admin key's managed set."
        else:
            message = "This Paranoy mode needs a pinned collection before that tool can run."
        return {"action": "block", "message": message}
    mutation = name in schemas.WRITE_TOOL_NAMES or name in schemas.GOVERNANCE_MUTATION_NAMES
    if not mutation:
        return None
    if in_unattended_cron(ctx) and not settings["allow_unattended_writes"]:
        return {
            "action": "block",
            "message": "Paranoy writes are disabled for unattended cron runs.",
        }
    if settings["require_publish_approval"]:
        return {
            "action": "approve",
            "message": "Paranoy write needs a person's approval before it can run.",
        }
    return None


def status_text(settings: dict) -> str:
    """Status without secret material."""
    owner = settings["owner"] or "the owner"
    if pin_outside_managed_set(settings):
        pin = "outside managed set"
    elif settings["mode"] in {"reader", "contributor"} and not settings["collection"]:
        pin = "missing"
    else:
        pin = "ok"
    return "\n".join(
        [
            "Paranoy plugin scaffold",
            f"mode: {settings['mode']}",
            f"server_url: {settings['server_url']}",
            f"collection: {settings['collection'] or '(unset)'}",
            f"collections: {settings['collections'] or '(unset)'}",
            f"collection_pin: {pin}",
            f"roles: {settings['roles'] or '(unset)'}",
            f"owner: {owner}",
            f"report_to: {settings['report_to'] or '(unset)'}",
            f"key_configured: {'yes' if settings['key_configured'] else 'no'}",
            "signing: not implemented",
            "tools: not implemented",
        ]
    )


def slash_result(settings: dict, raw_args: str) -> str:
    """``/paranoy`` subcommands. Argument text is never echoed."""
    parts = (raw_args or "").split()
    command = parts[0].lower() if parts else "status"
    has_arg = len(parts) > 1
    if command == "status" and not has_arg:
        return status_text(settings)
    if command == "collection":
        if has_arg:
            return "Changing the collection is not implemented in this scaffold."
        return f"collection: {settings['collection'] or '(unset)'}"
    if command == "mode":
        if has_arg:
            return "Changing the mode is not implemented in this scaffold."
        return f"mode: {settings['mode']}"
    if command == "roles":
        if has_arg:
            return "Changing roles is not implemented in this scaffold."
        return f"roles: {settings['roles'] or '(unset)'}"
    if command == "whoami":
        configured = "yes" if settings["key_configured"] else "no"
        return (
            "Public key is not available yet. The signing client is not "
            "implemented, and this command does not print the secret. "
            f"Key configured: {configured}."
        )
    if command == "keygen":
        return (
            "Keygen is a terminal command, and it is not implemented: "
            "hermes paranoy keygen"
        )
    return "Usage: /paranoy status|collection|mode|roles|whoami"
