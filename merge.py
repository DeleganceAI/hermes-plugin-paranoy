"""Fetch-merge-preview for replace-whole-list events.

The model supplies additions and removals. The current list comes from the
server. A missing fetch refuses the write. Kind 4711 is not handled here,
and kind 4713 never changes the fetched ``ledger`` field.
"""

from __future__ import annotations


class MergeError(Exception):
    """A list write that must not be published."""


def _clean(values) -> list[str]:
    cleaned = []
    for value in values or []:
        text = str(value or "").strip()
        if text and text not in cleaned:
            cleaned.append(text)
    return cleaned


def merge_members(current, *, add=None, remove=None, confirm_owner_only: bool = False) -> dict:
    """Merge a roster. ``current`` is a list of ``{pubkey, role}`` or None."""
    if current is None:
        raise MergeError(
            "The current member list could not be fetched, so nothing was published."
        )
    ordered: list[str] = []
    owner = None
    for row in current:
        if not isinstance(row, dict):
            continue
        pubkey = str(row.get("pubkey") or "").strip()
        if not pubkey:
            continue
        if pubkey not in ordered:
            ordered.append(pubkey)
        if row.get("role") == "owner":
            owner = pubkey
    if owner is None:
        raise MergeError("The current roster has no owner, so nothing was published.")
    removed_request = set(_clean(remove))
    if owner in removed_request:
        raise MergeError("The owner cannot be removed.")
    added = []
    for pubkey in _clean(add):
        if pubkey == owner or pubkey in ordered:
            continue
        ordered.append(pubkey)
        added.append(pubkey)
    removed = []
    intended = []
    for pubkey in ordered:
        if pubkey in removed_request and pubkey != owner:
            removed.append(pubkey)
            continue
        intended.append(pubkey)
    others = [pubkey for pubkey in intended if pubkey != owner]
    if not others and not confirm_owner_only:
        raise MergeError(
            "Refusing to wipe the roster down to the owner alone. "
            "Pass confirm_owner_only to do that on purpose."
        )
    return {
        "members": intended,
        "added": added,
        "removed": removed,
        "owner": owner,
    }


def merge_admins(current, owner: str, *, add=None, remove=None) -> dict:
    """Merge admin pubkeys. The owner is never written into the admin list."""
    if current is None or not str(owner or "").strip():
        raise MergeError(
            "The current admin list could not be fetched, so nothing was published."
        )
    owner = str(owner).strip()
    existing = [pubkey for pubkey in _clean(current) if pubkey != owner]
    removed_request = set(_clean(remove))
    kept = [pubkey for pubkey in existing if pubkey not in removed_request]
    added = []
    for pubkey in _clean(add):
        if pubkey == owner or pubkey in kept:
            continue
        kept.append(pubkey)
        added.append(pubkey)
    removed = [pubkey for pubkey in existing if pubkey not in kept]
    return {
        "admins": kept,
        "added": added,
        "removed": removed,
        "owner_excluded": owner,
    }


def merge_account_agents(current, *, add=None, remove=None) -> dict:
    """Merge kind 4713 agents. ``current['ledger']`` is copied unchanged.

    A ledger value supplied by the caller is not an argument of this function.
    """
    if not isinstance(current, dict):
        raise MergeError(
            "The current account agent list could not be fetched, so nothing was published."
        )
    existing = _clean(current.get("agents") or [])
    removed_request = set(_clean(remove))
    kept = [pubkey for pubkey in existing if pubkey not in removed_request]
    added = []
    for pubkey in _clean(add):
        if pubkey not in kept:
            kept.append(pubkey)
            added.append(pubkey)
    return {
        "ledger": current.get("ledger"),
        "agents": kept,
        "added": added,
        "removed": [pubkey for pubkey in existing if pubkey not in kept],
    }


def merge_collection_agents(current, *, add=None, remove=None) -> dict:
    """Merge a kind 4714 agent list. ``current`` is the fetched pubkey list or None."""
    if current is None:
        raise MergeError(
            "The current collection agent list could not be fetched, so nothing was published."
        )
    merged = merge_account_agents(
        {"ledger": None, "agents": list(current)}, add=add, remove=remove
    )
    return {
        "agents": merged["agents"],
        "added": merged["added"],
        "removed": merged["removed"],
    }
