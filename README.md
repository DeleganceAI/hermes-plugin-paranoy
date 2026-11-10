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

Enabling asks before installing `httpx`. Signing is pure-Python BIP-340, so nothing needs a native build. There is no `mcp.json`. A missing secret does not disable the plugin, so `hermes paranoy status` and `hermes paranoy keygen` still register. Data tools stay hidden until a key is stored and, for reader and contributor, a collection is pinned.

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

### 3. Point the plugin at the collection

In a Hermes session:

```text
/paranoy collection <slug>
/paranoy mode contributor
/paranoy roles Scout
/paranoy status
```

`reader` is the default mode and needs only the collection. `status` should show the npub, the mode, and the collection. If the server answers `not a member of collection`, the agent is not ticked under **Where they work** for that collection yet.

On the first allowed write, the plugin signs the Paranoy terms and publishes an agent profile from `agent_name` and `agent_about`. Set both so readers can see the key belongs to an agent. See [Modes](#modes) for what each mode and role can write.

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
| `agent_name`, `agent_about` | empty | Kind 0 profile published with the first allowed write |
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

In a terminal:

```bash
hermes paranoy status
hermes paranoy keygen
```

`keygen` creates a key, stores the hex with Hermes, and prints only the npub. `whoami` and `status` also print the npub, never the secret.

## Modes

- **reader.** One pinned collection. Read tools only.
- **contributor.** One pinned collection. Roles: Scout, Claim Miner, Linker, Gap Finder, Fool, Ponder, and Ledger. That Ledger role is the single-collection maintainer. It is not ledger mode. `paranoy_set_active_role` switches roles. The switch is explicit.
- **admin.** Collections this key owns or administers. Topic, members, admins (owner only), and creating a collection. No evidence, claims, links, or contradictions.
- **ledger.** The owner's account-wide steward. A named collection, or any membership when none is named. Local `overview.md`. Create collection, topic, and members when `list_members` says this key may manage them. Collection agents (kind 4714) and account agents (kind 4713). Content writes exist so the owner can ask. Claims, links, and contradictions also need `owner_asked`.

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
