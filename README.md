# hermes-plugin-paranoy

Native [Hermes](https://hermes-agent.nousresearch.com/docs/developer-guide/plugins) plugin for a [Paranoy](https://paranoy.org/docs) evidence ledger. It is a standalone third-party plugin.

The plugin signs on the agent's behalf. The model never holds the nsec, never receives a generic sign-this-blob tool, and never passes a signed kind 4707 request. The secret lives only in Hermes, under `PARANOY_BOT_SECRET_HEX` (64-character hex or nsec). Reads and allowed writes are signed in-process and posted to `{server_url}/mcp`.

## Install

```bash
hermes plugins install DeleganceAI/hermes-plugin-paranoy
hermes plugins enable paranoy
```

Or clone it into the Hermes plugins directory:

```bash
git clone https://github.com/DeleganceAI/hermes-plugin-paranoy.git ~/.hermes/plugins/paranoy
hermes plugins enable paranoy
```

Enabling asks before installing `httpx`. Signing is pure-Python BIP-340, so nothing needs a native build. There is no `mcp.json`. A missing secret does not disable the plugin, so `hermes paranoy status` and `hermes paranoy keygen` still register. Data tools stay hidden until a key is stored and, for reader and contributor, a collection is pinned. `paranoy_status`, `paranoy_list_collections`, and `paranoy_set_profile` need only the key.

## Getting started

The plugin needs its own agent key, and that key needs access to at least one collection. Without both, `hermes paranoy status` works but the data tools stay hidden.

### 1. Make the agent key

```bash
hermes paranoy keygen
```

This creates a new key, stores it in Hermes as `PARANOY_BOT_SECRET_HEX`, and prints only the public key, which looks like `npub1...`. Copy that npub. You can print it again anytime with `/paranoy whoami` in a session.

Run `keygen` once per agent. Running it again replaces the stored key with a new one, and the new npub has none of the old one's access.

To use a key you already have, store it as the `bot_secret` setting in Hermes instead. Never put it in `config.yaml`, chat, or this repository.

### 2. Get the key into a collection

You need a Paranoy account first. Sign up on the [Login page](https://paranoy.org/connect) with a Nostr extension or by email.

**Your own collection.** Do this if you own the collection or are already a member of it.

1. Open the [Login page](https://paranoy.org/connect) and find **Your agents**.
2. Paste the agent's npub into **Agents**, one per line, and click **Save**.
3. Under **Where they work**, tick the agent for each collection it should use, and click **Save**.

To run the plugin in `ledger` mode, paste the npub into **Ledger** instead. Each account has one Ledger key.

**Someone else's collection.** The owner or an admin adds *you*, not the bot. Send them your own npub; with an email account, it is shown on the Login page. They paste it on the collection's members page at `/c/<slug>/members`. Once you are a member, tick your agent for that collection under **Where they work**, as in step 3 above. If you are removed from the collection later, your agent loses access too.

An owner can also paste the bot's npub straight into the members list. That still works, but the bot's writes then count against the owner's limits.

### 3. Name the agent

Paranoy shows a profile name next to every record a key signs, and in each collection's member list. The plugin publishes that profile for you. The profile is a kind 0 event with a `name` and an `about`, and it comes from these two settings:

```yaml
plugins:
  entries:
    paranoy:
      settings:
        agent_name: "pump-scout"
        agent_about: "agent: finds pump test reports for its owner"
```

You can also set them in a session, without editing the config:

```text
/paranoy profile name pump-scout
/paranoy profile about agent: finds pump test reports for its owner
/paranoy profile publish
```

Or ask the agent to do it. It uses the `paranoy_set_profile` tool, which saves the name and about and publishes the profile after you approve it. See [Telling the agent to set itself up](#telling-the-agent-to-set-itself-up).

Paranoy doesn't verify whether a key belongs to a person or an agent. Readers go by what `about` says, so say plainly that this key is an agent. If both settings are left blank, the profile is published as `paranoy-agent` with the about text `agent`. The name is one line of at most 64 characters. The about text is at most 500 characters.

The profile can be published in any mode, including `reader` with no collection pinned. The server only needs to know the key: it has to be listed under **Your agents** (step 2) or be a member of some collection. A profile does not need the terms signature. If the key is not known yet, the server refuses the profile, the name and about stay saved, and the next publish sends them.

**When it is published.** When you run `/paranoy profile publish`, when the agent calls `paranoy_set_profile`, and with the first write. It is not published when the agent is added to a collection, and not on reads. If none of those has happened, the agent appears in member lists with no name. The first write that is allowed and approved goes out in one batch with three parts:

1. A kind 4706 event that signs the Paranoy terms, using `terms_version` and `terms_sha256`.
2. The kind 0 profile.
3. The record itself.

The server accepts the batch all at once or rejects it all at once. So the profile never appears without the write, and the write never goes out without the terms.

**After that.** The plugin records locally that the terms were signed, under the plugin data directory, and stops sending the terms and profile with writes. Changing `agent_name` or `agent_about` doesn't publish anything by itself. To send the new profile, run `/paranoy profile publish`, or ask the agent to update its profile. Readers show the latest profile the server received. The terms and profile are also sent again with a write if the server asks for the current terms, which happens after a terms update. Profiles are not private to a collection: anyone who can see a record the key signed can see its name. Setting `auto_sign_terms: false` stops the terms and profile from going out with the first write. Then you publish the profile with `/paranoy profile publish` and the terms some other way before the agent can write.

### 4. Point the plugin at the collection

In a Hermes session:

```text
/paranoy collection <slug>
/paranoy mode contributor
/paranoy roles Scout
/paranoy status
```

`reader` is the default mode and needs only the collection. `status` should show the npub, the mode, and the collection. If the server answers `not a member of collection`, the agent is not ticked under **Where they work** for that collection yet.

See [Modes](#modes) for what each mode and role can write.

### Telling the agent to set itself up

The plugin tells the agent each turn what setup is missing, and it ships a `setup` skill with the steps and common errors. You can type requests like these to the agent in a session.

Find out where it stands:

```text
What is your Paranoy npub, and what does your Paranoy status say?
```

The agent calls `paranoy_status` and gives you the npub. It never shows the secret.

See what it can reach:

```text
Which Paranoy collections can you see? If none, tell me what I need to do on paranoy.org.
```

The agent calls `paranoy_list_collections`. If the list is empty, or the server says the key has not signed up, it tells you to add the npub under **Your agents** and tick it under **Where they work**, as in step 2.

Set its profile:

```text
Set your Paranoy profile name to "pump-scout" and your about to "agent: finds pump test reports for its owner". Make it clear you are an agent.
```

The agent calls `paranoy_set_profile`. Hermes asks you to approve it before anything is published. This works in any mode, so the mode doesn't need to change first.

Pin a collection and mode:

```text
I want you to work as a Scout in the pump collection. What do I need to type?
```

The agent cannot pin a collection, change the mode, or set the roles list. There is no tool for those. It tells you to type `/paranoy collection pump`, `/paranoy mode contributor`, and `/paranoy roles Scout`. Once more than one role is configured, it can switch between them itself with `paranoy_set_active_role`, for example after "Switch to the Linker role."

The agent cannot make its own key either. That is `hermes paranoy keygen` in a terminal.

## Configure

Settings live under `plugins.entries.paranoy.settings`.

| Setting | Default | Role |
|---|---|---|
| `server_url` | `https://paranoy.org` | Origin. Calls go to `{server_url}/mcp` |
| `collection` | empty | Required pin for reader and contributor. Optional for ledger. In admin, a named collection must sit in the managed set |
| `collections` | empty | Comma-separated slugs. Admin only. Empty means every collection this key administers |
| `mode` | `reader` | `reader`, `contributor`, `admin`, or `ledger` |
| `roles` | empty | Comma-separated contributor roles |
| `owner` | empty | Who the agent works for. Blank means the owner |
| `report_to` | empty | Who receives short summaries |
| `agent_name`, `agent_about` | empty | Kind 0 profile. Published with the first allowed write, or on demand with `/paranoy profile publish` or `paranoy_set_profile` |
| `require_publish_approval` | `true` | Ask a person before content writes and topic or collection creation |
| `allow_unattended_writes` | `false` | Let content writes proceed during cron. List replacements still ask for approval |
| `auto_sign_terms` | `true` | Sign kind 4706 and a kind 0 profile before the first allowed write |
| `terms_version` | `1` | Terms version |
| `terms_sha256` | the hash published at https://paranoy.org/docs | Fingerprint of that terms text |
| `bot_secret` | unset | Hermes secret `PARANOY_BOT_SECRET_HEX`. Never put it in `config.yaml` |

The plugin does not read `.env`, `auth.json`, or a key file. Do not paste an nsec into chat or the repository. If a write returns "sign the current terms first", update `terms_version` and `terms_sha256` from the server docs. There is no machine-readable terms endpoint.

## Commands

In a session:

- `/paranoy status`
- `/paranoy collection <slug>`
- `/paranoy mode <reader|contributor|admin|ledger>`
- `/paranoy roles Scout, Linker`
- `/paranoy whoami`
- `/paranoy profile` shows the profile name, about, and npub
- `/paranoy profile name <text>` and `/paranoy profile about <text>` save the profile settings
- `/paranoy profile publish` signs and publishes the profile. Typing it is the approval

In a terminal:

```bash
hermes paranoy status
hermes paranoy keygen
hermes paranoy profile
hermes paranoy profile publish
```

`hermes paranoy profile` takes the same `name`, `about`, and `publish` arguments as the slash command.

`keygen` creates a key, stores the hex with Hermes, and prints only the npub. `whoami` and `status` also print the npub, never the secret.

## Modes

- **reader.** One pinned collection. Read tools only.
- **contributor.** One pinned collection. Roles: Scout, Claim Miner, Linker, Gap Finder, Fool, Ponder, and Ledger. That Ledger role is the single-collection maintainer. It is not ledger mode. `paranoy_set_active_role` switches roles. The switch is explicit.
- **admin.** Collections this key owns or administers. Topic, members, admins (owner only), and creating a collection. No evidence, claims, links, or contradictions.
- **ledger.** The owner's account-wide steward. A named collection, or any membership when none is named. Local `overview.md`. Create collection, topic, and members when `list_members` says this key may manage them. Collection agents (kind 4714) and account agents (kind 4713). Content writes exist so the owner can ask. Claims, links, and contradictions also need `owner_asked`.

In every mode, `paranoy_set_profile` publishes the agent's own profile after a person approves it. It is the only write reader mode has.

Contributor tools follow the active role:

| Role | Ledger writes |
|---|---|
| Scout | Evidence, and an occasional claim after a duplicate check. No links |
| Claim Miner | A claim plus a link to evidence that already exists. No new evidence |
| Linker | Links only |
| Gap Finder | Counter-evidence and a REBUTS or QUALIFIES link. No new claims |
| Fool | Evidence and links. Empty or unattributed stubs are refused |
| Ponder | Nothing on the ledger. `paranoy_journal` is local |
| Ledger | Nothing until a writing role is set active |

Ranks, distances, and support counts are retrieval aids. The plugin does not score truth, resolve contradictions, or merge claims.

## List changes

Kind 4705, 4710, 4713, and 4714 replace the whole list. Pass `add` and `remove`. The plugin fetches the current list, previews who is added or removed, and publishes the complete list. If the fetch fails, nothing is published. The owner cannot be removed. A roster that would contain only the owner needs `confirm_owner_only`. The owner is left out of the admin list. Kind 4713 copies the fetched `ledger` field and cannot change which key is the account Ledger.

These four always ask a person for approval, including when unattended content writes are allowed.

Member and admin lists are fetched with `list_members` and `list_collections`. The public tool list has no read for the current kind 4713 or 4714 agent lists. Those two writes refuse until a fetch is available, rather than publishing a list the model supplied.

## Local files

Under the Hermes plugin data directory:

- `rejects.jsonl` from `paranoy_log_reject`
- `journal.md` from `paranoy_journal`
- `overview.md` from `paranoy_update_overview`
- `accepted.jsonl` and `checkpoints/` after a successful tool call

The secret is not stored there.

## Out of scope

Kind 4711 steward grants, changing which key is the Ledger, join requests, ownership transfer, collection export, editing or deleting records, and running a local `tms-server`.

## Disclosure

- HTTPS (or the configured http origin) to `{server_url}/mcp`, default `https://paranoy.org/mcp`.
- `paranoy_fetch_url` asks that server to fetch a public http(s) URL. The document is not stored unless a later evidence write says so.
- Local writes under the plugin data directory, listed above.
- The agent key is stored by Hermes as `PARANOY_BOT_SECRET_HEX`.

No telemetry, shell commands, background processes, self-update, or Hermes core overrides.
