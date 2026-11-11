---
name: Paranoy collection admin
description: >-
  Use when an agent administers one or more Paranoy collections this key owns
  or administers — members, topic and visibility, admins when this key is the
  owner, and creating a collection. No content writes.
---
# Paranoy collection admin

> **If the agent is set up wrong**, for example no tools are visible, a call says the key has not signed up or is not a member, or the person asks about the npub or profile, use the Paranoy agent setup skill (`setup`) first.

Administer collections. Do not add evidence, claims, links, or contradictions. Content writes belong to a contributor, or to ledger mode when the owner asks.

## Placeholders

| Placeholder | Meaning |
|---|---|
| `<OWNER>` | Person this agent works for |
| `<SERVER_URL>` | Ledger base URL (docs at `<SERVER_URL>/docs`) |
| `<COLLECTION>` | Collection slug for this action, when one is named |
| `<KEY_PATH>` | This agent's Nostr secret path — never print, log, or paste the nsec |
| `<STATE_DIR>` | Local checkpoint dir (not in the ledger) |
| `<REPORT_TO>` | Who gets short summaries |
| `<ROLES>` | Not used in admin mode. Contributor roles, including the single-collection Ledger role, belong to the contributor skill |

`<COLLECTION>` must be a collection this key owns or administers. When the managed set is non-empty, stay inside it. An empty managed set means every collection this key administers.

## Standing rules

1. Read `<SERVER_URL>/docs` before the first governance change each session. Follow live docs over memory.
2. The plugin signs. Do not ask for the nsec, do not pass a signed kind 4707 request, and do not call a raw publish tool or a generic sign-this-blob tool.
3. No content writes. Do not add evidence, claims, links, or contradictions in this mode.
4. Work only in collections this key owns or administers. Never change a collection outside that set.
5. Append-only: a new members, topic, or admins event supersedes the older one for that purpose. It does not edit the old event.
6. Report briefly to `<REPORT_TO>`. Stay quiet when nothing changed. Never include key material.

## What you may change

- **Members (kind 4705).** `paranoy_add_members` and `paranoy_remove_members` name who to add or remove. The plugin fetches the roster, previews the change, and publishes the complete list. Do not hand it a replacement list. The owner cannot be removed. Wiping everyone except the owner needs `confirm_owner_only`.
- **Topic and visibility (kind 4709).** `paranoy_set_topic` sets the one-line topic (at most 140 characters) and whether the collection is public. A public collection is listed at `GET /api/public/collections`, which `paranoy_list_public_collections` reads. A key becomes a member by signing kind 4715, not by this flag alone. An owner or admin can still block that key; the server then skips the collection.
- **Admins (kind 4710), and only when this key is the owner.** `paranoy_set_admins` adds or removes admins. The plugin fetches the current list and leaves the owner's pubkey out. An admin cannot change the admin list.
- **Create a collection.** `paranoy_create_collection` opens a collection this key owns. Pass `owner` only when this key is already a steward and the new collection should belong to that grantor (kind 4712).

Kind 4705 and kind 4710 always require approval, even when other writes would not.

## What you must not change

- Kind 4711 steward grants stay out of v1. The human signs those. Do not invent a tool call for them.
- Do not change which key is the account Ledger.
- Account agent lists (kind 4713) and per-collection agent assignments (kind 4714) belong to ledger mode, not admin mode.
- The contributor role named Ledger is a single-collection maintainer. It is not this mode and not ledger mode.

## Pass summary shape

Collections touched, changes previewed, whether a person approved, blockers, and anything that belongs to a contributor or to ledger mode.
