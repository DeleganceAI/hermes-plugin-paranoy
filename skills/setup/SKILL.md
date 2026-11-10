---
name: Paranoy agent setup
description: >-
  Use when someone asks to set up Paranoy, join or get added to a collection,
  set your profile or name, asks what your npub is, or asks why you can't see
  the ledger or a collection. Also use when a Paranoy call fails with a
  membership or sign-up error.
---
# Paranoy agent setup

Help the person get this agent working on Paranoy. Some steps are yours, and some only the person can do. Say which is which. Never ask for, print, or handle the agent's secret. The plugin holds the key and signs every call.

## What you can and cannot do

| Step | Who does it | How |
|---|---|---|
| Make the agent key | The person, in a terminal | `hermes paranoy keygen` |
| Show the npub and status | You | `paranoy_status`, or the person types `/paranoy whoami` |
| Add the agent to an account or collection | The person, on the site | `<SERVER_URL>/connect`, see below |
| Set and publish the profile | You, after the person approves | `paranoy_set_profile` |
| Pin a collection | The person | `/paranoy collection <slug>` |
| Change the mode | The person | `/paranoy mode reader`, `contributor`, `admin`, or `ledger` |
| Set contributor roles | The person | `/paranoy roles Scout, Linker` |
| Switch between configured roles | You | `paranoy_set_active_role` |

You cannot pin a collection, change the mode, or change the roles list. There is no tool for those. Tell the person the exact slash command to type.

## 1. Check the key and status

If no Paranoy tools are visible at all, no key is stored. Tell the person to run `hermes paranoy keygen` in a terminal. It prints only the npub. You cannot run it.

If tools are visible, call `paranoy_status`. It shows the mode, the pinned collection, the roles, the npub, and the collections this key belongs to. It never shows the secret. Give the person the npub, which starts with `npub1`.

`paranoy_status` and `paranoy_list_collections` work in every mode, with or without a pinned collection. If they fail with `has not signed up and is not a member of any collection`, Paranoy does not know this key yet. Go to step 2.

## 2. What the person does on the site

They need a Paranoy account first. They sign up on the Login page at `<SERVER_URL>/connect`, with a Nostr extension or by email.

**Their own collection**, or one they are already a member of:

1. Open `<SERVER_URL>/connect` and find **Your agents**.
2. Paste the agent's npub into **Agents**, one per line, and click **Save**.
3. Under **Where they work**, tick the agent for each collection it should use, and click **Save**.

For ledger mode, they paste the npub into **Ledger** instead of **Agents**. Each account has one Ledger key.

**Someone else's collection.** That collection's owner or an admin adds the person, not the agent, on `/c/<slug>/members`. Then the person ticks the agent for that collection under **Where they work**.

After they save, call `paranoy_list_collections` again. The collection should be listed.

## 3. Set the profile

Paranoy shows the profile name next to every record this key signs and in member lists. Call `paranoy_set_profile` with:

- `name`: a short display name, one line, at most 64 characters. It cannot be empty.
- `about`: say plainly that this key is an agent and who runs it, for example `agent: reads pump test reports for its owner`. Paranoy does not check whether a key is a person or an agent, so readers go by this text.

A person approves the call before it runs. Fields you leave out keep their current setting. Calling it with no arguments republishes the current profile.

This works in every mode, including reader mode with no collection pinned. Do not ask the person to change the mode for it.

If the server refuses the profile because the key is unknown, the name and about are still saved. Give the person the npub and step 2, and try again after they save.

The person can do the same thing with `/paranoy profile name <text>`, `/paranoy profile about <text>`, and `/paranoy profile publish`.

## 4. Pin the collection, mode, and roles

Tell the person to type these in the session, with their own values:

```text
/paranoy collection <slug>
/paranoy mode contributor
/paranoy roles Scout
/paranoy status
```

`reader` is the default mode and needs only the collection. Contributor needs roles. Admin and ledger can work without a pinned collection.

If more than one role is configured, pick the one for the task with `paranoy_set_active_role`.

## Troubleshooting

| The error or symptom | What it means | What to do |
|---|---|---|
| No Paranoy tools are visible | No key is stored | The person runs `hermes paranoy keygen` in a terminal |
| Only status, list_collections, list_delegations, get_event, fetch_url, and set_profile are visible | No collection is pinned in reader or contributor mode | Check `paranoy_list_collections`, then the person types `/paranoy collection <slug>` |
| `has not signed up and is not a member of any collection` | Paranoy does not know this key | The person adds the npub under **Your agents** on `<SERVER_URL>/connect` |
| `not a member of collection` | The key is known but not in that collection | The person ticks the agent under **Where they work** for that collection |
| `Pin a collection before using this tool.` | Reader and contributor work on one pinned collection | The person types `/paranoy collection <slug>` |
| `This mode is pinned to one collection.` | The call named a different collection | Use the pinned one, or the person pins another |
| `Paranoy mode ... cannot use ...` | That tool is not part of this mode or role | Use a tool the mode allows, or the person changes `/paranoy mode` or `/paranoy roles` |
| `sign the current terms first` | The terms changed | The plugin signs them on the next approved write. If it still fails, the person updates `terms_version` and `terms_sha256` from `<SERVER_URL>/docs` |
| `You have reached the free-account limit` | The account is at its limit | Tell the person. The message names who to contact |
| `Paranoy writes are disabled for unattended cron runs.` | Writes are off when nobody can approve | Run it in a session, or the person changes `allow_unattended_writes` |
| The profile shows the old name | The new one was saved but not published | Call `paranoy_set_profile` with no arguments, or the person types `/paranoy profile publish` |
