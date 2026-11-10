"""Shared logic for the ``/paranoy`` slash commands and their agent tools.

Each subcommand and its tool call the same function here, so the two cannot
drift. ``SLASH_TOOLS`` in ``schemas`` maps every subcommand to its tool, and a
test fails when a new subcommand has no tool. ``keygen`` is the one
exception: it stays a terminal command, because the agent never creates or
handles the secret.
"""

from __future__ import annotations

from . import schemas
from .client import PluginError

SLUG_RULE = (
    "Collection slugs start with a lowercase letter or digit and "
    "then use lowercase letters, digits, hyphens, or underscores."
)


# ---------------------------------------------------------------- validators


def check_slug(value) -> str:
    from .gates import _SLUG

    slug = str(value if value is not None else "").strip()
    if not slug or not _SLUG.fullmatch(slug):
        raise PluginError(SLUG_RULE)
    return slug


def check_managed(settings: dict, slug: str) -> None:
    """Admin mode with a non-empty managed set may only pin inside that set."""
    managed = settings.get("managed") or []
    if settings.get("mode") == "admin" and managed and slug not in managed:
        raise PluginError(
            f"{slug} is outside the managed set ({', '.join(managed)}). "
            "Only the user can change the managed set, in the collections setting."
        )


def check_mode(value) -> str:
    mode = str(value if value is not None else "").strip().lower()
    if mode not in schemas.MODES:
        raise PluginError("Mode must be reader, contributor, admin, or ledger.")
    return mode


def check_roles(value) -> list[str]:
    from .gates import canonical_roles, parse_csv

    if isinstance(value, (list, tuple)):
        parts = [str(item).strip() for item in value if str(item).strip()]
    else:
        parts = parse_csv(str(value if value is not None else ""))
    unknown = [part for part in parts if schemas.ROLE_INDEX.get(part.casefold()) is None]
    names = canonical_roles(", ".join(parts))
    if unknown or not names:
        raise PluginError(
            "Roles must be chosen from Scout, Claim Miner, Linker, Gap Finder, Fool, Ponder, and Ledger."
        )
    return names


# ---------------------------------------------------------------- visibility


def visible_tools(settings: dict) -> list[str]:
    from .gates import tool_visible

    return [name for name in schemas.ALL_TOOL_NAMES if tool_visible(settings, name)]


def _visibility_change(before: dict, after: dict) -> dict:
    old = visible_tools(before)
    new = visible_tools(after)
    return {
        "visible_next_turn": new,
        "newly_visible": [name for name in new if name not in old],
        "hidden_next_turn": [name for name in old if name not in new],
    }


def _with_roles(settings: dict, roles: list[str]) -> dict:
    from . import state

    stored = state.load_state().get("active_role") or ""
    active = schemas.ROLE_INDEX.get(str(stored).casefold(), "")
    if active not in roles:
        active = roles[0] if len(roles) == 1 else ""
    return {**settings, "roles": ", ".join(roles), "roles_list": roles, "active_role": active}


# ---------------------------------------------------------------- operations


def membership_rows(listed) -> list[dict] | None:
    rows = listed.get("result") if isinstance(listed, dict) else listed
    if not isinstance(rows, list):
        return None
    return [row for row in rows if isinstance(row, dict) and row.get("slug")]


def is_member(row: dict, pubkey: str | None) -> bool:
    """Same rule as runtime.assert_membership, so the pin and the calls agree."""
    from .runtime import row_role

    return row_role(row, pubkey) is not None


def set_collection(
    settings: dict,
    slug,
    save,
    rows: list[dict] | None,
    pubkey: str | None,
    *,
    strict: bool = True,
) -> dict:
    """Pin a collection. ``rows`` is the list_collections result.

    The agent tool is strict: it needs the listing and refuses a collection
    this key is not a member of. The user's slash command is not strict,
    because the user decides. It still saves, and warns, when the listing is
    missing or does not include the slug.
    """
    slug = check_slug(slug)
    check_managed(settings, slug)
    previous = settings.get("collection") or None
    row = None
    warning = None
    if rows is None:
        if strict:
            raise PluginError("Collections could not be listed, so the collection was not changed.")
        warning = "Membership was not checked, because collections could not be listed."
    else:
        members = [item for item in rows if is_member(item, pubkey)]
        available = sorted({item["slug"] for item in members})
        row = next((item for item in members if item["slug"] == slug), None)
        if row is None:
            if strict:
                raise MembershipError(slug, available)
            warning = (
                f"This key is not listed as a member of {slug}. Reads will fail until it is. "
                f"Collections this key can use: {', '.join(available) if available else '(none)'}."
            )
    save("collection", slug)
    result = {
        "previous": previous,
        "collection": slug,
        "changed": previous != slug,
        "mode": settings.get("mode"),
        "note": "Mode and roles did not change. Only the user, or an approved "
        "paranoy_set_mode or paranoy_set_roles call, changes them.",
    }
    if row is not None:
        result.update(
            {
                "topic": row.get("topic"),
                "public": row.get("public"),
                "counts": {
                    key: row.get(key)
                    for key in ("evidence", "claims", "links", "contradictions")
                    if key in row
                },
            }
        )
    if warning:
        result["warning"] = warning
    return result


class MembershipError(PluginError):
    def __init__(self, slug: str, available: list[str]):
        self.slug = slug
        self.available = available
        listed = ", ".join(available) if available else "(none)"
        super().__init__(
            f"This key is not a member of {slug}, so the collection was not changed. "
            f"Collections this key can use: {listed}."
        )


def set_mode(settings: dict, mode, save) -> dict:
    mode = check_mode(mode)
    previous = settings.get("mode")
    save("mode", mode)
    after = {**settings, "mode": mode}
    return {"previous": previous, "mode": mode, "changed": previous != mode, **_visibility_change(settings, after)}


def set_roles(settings: dict, roles, save) -> dict:
    names = check_roles(roles)
    previous = settings.get("roles_list") or []
    save("roles", ", ".join(names))
    after = _with_roles(settings, names)
    return {
        "previous": previous,
        "roles": names,
        "active_role": after["active_role"] or None,
        "mode": settings.get("mode"),
        **_visibility_change(settings, after),
    }


def whoami(settings: dict, npub: str | None) -> dict:
    from .runtime import profile_content

    return {
        "npub": npub,
        "profile": profile_content(settings),
        "profile_defaults": {
            "name": not settings.get("agent_name"),
            "about": not settings.get("agent_about"),
        },
    }


def describe_visibility(result: dict) -> list[str]:
    lines = []
    if result.get("newly_visible"):
        lines.append("Visible from the next turn: " + ", ".join(result["newly_visible"]))
    if result.get("hidden_next_turn"):
        lines.append("Hidden from the next turn: " + ", ".join(result["hidden_next_turn"]))
    if not lines:
        lines.append("The visible tools do not change.")
    return lines


def save_profile(settings: dict, args: dict, save) -> dict:
    """Check and save name and about. Returns the settings with the changes applied."""
    from .gates import profile_value

    changes = {}
    for field, key in (("name", "agent_name"), ("about", "agent_about")):
        if field in args and args[field] is not None:
            changes[key] = profile_value(field, args[field])
    for key, value in changes.items():
        save(key, value)
    return {**settings, **changes}
