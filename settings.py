"""Plugin settings. The secret is only ever a boolean here."""

from __future__ import annotations

import re

from . import state

SECRET_ENV = "PARANOY_BOT_SECRET_HEX"
TERMS_VERSION = "1"
TERMS_SHA256 = "ef0c5a74783a3639172ab7dd1d7df764611bb70e29dccfb34823cc0a86154626"
SLUG = re.compile(r"^[a-z0-9][a-z0-9_-]*$")

DEFAULTS = {
    "server_url": "https://paranoy.org",
    "collection": "",
    "owner_name": "",
    "capture": True,
    "ground": True,
    "agent_name": "",
    "agent_about": "",
    "permalink_base": "",
    "terms_version": TERMS_VERSION,
    "terms_sha256": TERMS_SHA256,
}


def as_bool(value, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value if value is not None else "").strip().lower()
    if text in ("1", "true", "yes", "on"):
        return True
    if text in ("0", "false", "no", "off"):
        return False
    return default


def _get(ctx, key: str, default):
    """ctx config when Hermes has it, otherwise the plugin-local overlay."""
    get = getattr(ctx, "get_config", None)
    if callable(get):
        try:
            value = get(key, default=default)
        except Exception:
            value = None
        return default if value is None else value
    return (state.load_state().get("plugin_settings") or {}).get(key, default)


def save(ctx, key: str, value) -> None:
    set_config = getattr(ctx, "set_config", None)
    if callable(set_config):
        set_config(key, value)
        return
    state.change_state(lambda stored: stored.setdefault("plugin_settings", {}).update({key: value}))


def read(ctx) -> dict:
    from .signing import secret_configured

    values = {}
    for key, default in DEFAULTS.items():
        raw = _get(ctx, key, default)
        values[key] = as_bool(raw, default) if isinstance(default, bool) else str(raw or default).strip()
    values["key_configured"] = secret_configured()
    values["ready"] = bool(values["key_configured"] and values["collection"])
    return values
