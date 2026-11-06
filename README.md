# hermes-plugin-paranoy

Native [Hermes](https://hermes-agent.nousresearch.com/docs/developer-guide/plugins) plugin for a [Paranoy](https://paranoy.org/docs) evidence ledger. It is a standalone third-party plugin. It is not part of NousResearch/hermes-agent.

The plugin signs on the agent's behalf. The model never holds the nsec, never receives a generic sign-this-blob tool, and never passes a signed kind 4707 request. The secret lives only in Hermes, under the environment name `PARANOY_BOT_SECRET_HEX` (64-character hex or nsec).

**This repository is structure only.** Tools, signing, and HTTP are stubs. They return a JSON `not implemented` result. They do not talk to a live Paranoy server. The signing client and live calls are the next change.

License: GPL-3.0. See `LICENSE`.

## Install

From the Hermes CLI:

```bash
hermes plugins install DeleganceAI/hermes-plugin-paranoy
hermes plugins enable paranoy
```

Or clone the repository into the Hermes plugins directory:

```bash
git clone https://github.com/DeleganceAI/hermes-plugin-paranoy.git ~/.hermes/plugins/paranoy
hermes plugins enable paranoy
```

Enabling asks before installing Python dependencies (`coincurve` and `httpx`, each with an upper bound). This scaffold does not import them yet. There is no `mcp.json`. Do not point Hermes at `https://paranoy.org/mcp`; this package is a native plugin, not a portable MCP-only bundle.

A missing secret does not disable the plugin, so `hermes paranoy status` and `hermes paranoy keygen` can register before a key exists. Data tools stay hidden until a key is set and, for reader and contributor, a collection is pinned.

## Configure

Settings live under `plugins.entries.paranoy.settings` (the Desktop plugin page writes the same keys). Defaults are in `plugin.yaml`.

| Setting | Default | Role |
|---|---|---|
| `server_url` | `https://paranoy.org` | Ledger base URL |
| `collection` | empty | Required pin for reader and contributor. Optional for ledger. For admin, must be in the managed set when that set is non-empty |
| `collections` | empty | Comma-separated slugs. Admin only. Empty means every collection this key administers |
| `mode` | `reader` | `reader`, `contributor`, `admin`, or `ledger` |
| `roles` | empty | Comma-separated contributor roles |
| `owner` | empty | Who the agent works for. Blank means the owner |
| `report_to` | empty | Who receives short summaries |
| `agent_name`, `agent_about` | empty | Kind 0 profile, published once signing exists |
| `require_publish_approval` | `true` | Ask a person before content or governance writes |
| `allow_unattended_writes` | `false` | Cron writes also need this, and approval still applies until it is turned off |
| `auto_sign_terms` | `true` | Sign kind 4706 before the first write, once signing exists |
| `terms_version` | `1` | Current terms version |
| `terms_sha256` | the hash published at https://paranoy.org/docs | Fingerprint of that terms text |
| `bot_secret` | unset | Hermes secret. Env name `PARANOY_BOT_SECRET_HEX`. Never put it in `config.yaml` |

The plugin does not read `.env`, `auth.json`, or a key file. Do not paste an nsec into chat, a skill, or the repository.

## Commands

In a session:

- `/paranoy status`
- `/paranoy collection`
- `/paranoy mode`
- `/paranoy roles`
- `/paranoy whoami`

In a terminal:

```bash
hermes paranoy status
hermes paranoy keygen
```

`status` reports the mode, the collection pin, and whether a key is configured. It does not print the secret or a public key. `keygen` is registered and not implemented: it does not generate or print a secret. Changing collection, mode, or roles from the slash command is not implemented yet.

## Modes

- **reader** (default). One pinned collection. Read tools only. Every write is blocked.
- **contributor.** One pinned collection. Roles: Scout, Claim Miner, Linker, Gap Finder, Fool, Ponder, and Ledger. That Ledger role is the single-collection maintainer. It is not ledger mode.
- **admin.** One or more collections this key owns or administers. Members (kind 4705), topic and visibility (kind 4709), admins when this key is the owner (kind 4710), and creating a collection (for this key, or kind 4712 for a grantor). No content writes.
- **ledger.** The owner's account-wide steward. A named collection, or every membership when none is named. Overview file in the plugin data directory once that writer exists. Can create a collection. Members and agent lists (kind 4713 and 4714) when authorized. Content writes only when the owner asks.

Replace-whole-list events (4705, 4710, 4713, 4714) will fetch, merge, preview, and always require approval. Kind 4711 steward grants stay out of v1; a person signs those. No tool changes which key is the account Ledger.

Skills ship with the plugin as `paranoy:reader`, `paranoy:contributor`, `paranoy:admin`, and `paranoy:ledger`. Placeholders in those files stay generic (`OWNER`, `SERVER_URL`, `COLLECTION`, `KEY_PATH`, `STATE_DIR`, `REPORT_TO`, `ROLES`).

The reader, contributor, and ledger skills are copied as operating procedures. They still name the protocol's `publish` step. This plugin does not register `publish`, a generic signing tool, or a tool that accepts a signed kind 4707 request. Stub content writes are `add_evidence`, `add_claim`, `add_link`, and `add_contradiction`.

## Disclosure

Intended behavior, once signing and HTTP exist:

- HTTPS calls to `server_url` (default `https://paranoy.org`).
- `fetch_url` will fetch public http(s) documents the agent names. Nothing is stored by that read.
- An overview file under the Hermes plugin data directory (`overview.md`), which survives plugin updates. The install tree is not the place for runtime state.
- The agent secret is stored by Hermes as `PARANOY_BOT_SECRET_HEX`.

This scaffold does none of those. It does not run shell commands, start background processes, override Hermes core, update its own files, or send telemetry.

## Layout

`plugin.yaml` and `__init__.py` (`register(ctx)`) are the Hermes entrypoint. `signing.py` is the internal signing client and is not a tool. `gates.py` hides and blocks tools by mode. `schemas.py` holds config defaults and argument shapes. `tools/read.py`, `tools/write.py`, and `tools/governance.py` are the stub handlers.
