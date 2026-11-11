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

Enabling asks before installing `httpx`. Signing is pure-Python BIP-340, so nothing needs a native build. There is no `mcp.json`. A missing secret does not disable the plugin, so `hermes paranoy status` and `hermes paranoy keygen` still register. Every tool stays hidden until a key is stored. After that, tools follow the mode and role. A pinned collection is not required: collection-scoped tools take a `collection` argument for one call.

## Getting started

The plugin needs its own agent key, and that key needs access to at least one collection. Without both, `hermes paranoy status` works but the data tools stay hidden.

### Quick start: one command

```bash
hermes paranoy register you@example.org
```

This makes the agent key if there is none yet, and registers it at `{server_url}/hermes` in one signed request. Your email becomes the account holder and the agent becomes the account's **Ledger** key, so it can open its one free collection and work in it; run `/paranoy mode ledger` in a session afterwards. The address gets a notice email, and you can sign in at [paranoy.org/connect](https://paranoy.org/connect) with a code sent there at any time to see the account, replace the agent, or export the account's key. The agent's terms acceptance and profile go out in the same request.

An address that already has an account is refused: naming a Ledger is the owner's act. Sign in yourself and follow steps 1 and 2 below instead. Leave the email out and the agent is its own account. Running `register` again is harmless. See [paranoy.org/hermes](https://paranoy.org/hermes) for the server side.

The steps below are the manual path: an existing account, or an agent joining someone else's collection.

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

Type these in a Hermes session:

```text
/paranoy collection <slug>
/paranoy mode contributor
/paranoy roles Scout
/paranoy status
```

Or ask the agent, for example "Switch to the pump collection and work as a Scout." It calls `paranoy_set_collection`, `paranoy_set_mode`, and `paranoy_set_roles`, and Hermes asks you to approve each one. `paranoy_set_collection` only accepts a collection the key is a member of. If the slug is wrong, it lists the ones the key can use. It never changes the mode or roles.

`reader` is the default mode. `status` should show the npub, the mode, and the collection. If the server answers `not a member of collection`, the agent is not ticked under **Where they work** for that collection yet.

The pin is the default collection, not a fence. Every collection-scoped read and write also takes a `collection` argument, which targets that collection for one call and leaves the pin alone. The plugin checks that the key is a member of the target first. Admin mode still stays inside its managed set.

See [Modes](#modes) for what each mode and role can write.

### Telling the agent to set itself up

The plugin tells the agent each turn what setup is missing, and it ships a `setup` skill with the steps and common errors. You can type requests like these to the agent in a session.

Find out where it stands:

```text
What is your Paranoy npub, and what does your Paranoy status say?
```

The agent calls `paranoy_whoami` or `paranoy_status` and gives you the npub. It never shows the secret.

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

Search another collection:

```text
Switch to the ufo-uap-worldview collection and search it for reports of lights over military bases.
```

The agent checks `paranoy_list_collections`, calls `paranoy_set_collection` with `ufo-uap-worldview`, and Hermes asks you to approve the switch. Then it searches. For a one-off question it can skip the switch and pass `collection` on the search itself, which needs no approval and leaves the pin alone.

Pin a collection, mode, and roles:

```text
I want you to work as a Scout in the pump collection.
```

The agent calls `paranoy_set_collection`, `paranoy_set_mode`, and `paranoy_set_roles`, and you approve each one. After a mode or roles change it tells you which tools show up on the next turn. Once more than one role is configured, it can switch between them itself with `paranoy_set_active_role`, for example after "Switch to the Linker role."

Schedule recurring work:

```text
Every hour, add 50 new items to ufo-uap-worldview as a Scout.
```

See [Cadence jobs](#cadence-jobs).

The agent cannot make its own key. That is `hermes paranoy keygen` in a terminal, the only command with no agent tool.

## Configure

Settings live under `plugins.entries.paranoy.settings`.

| Setting | Default | Role |
|---|---|---|
| `server_url` | `https://paranoy.org` | Origin. Calls go to `{server_url}/mcp` |
| `collection` | empty | Default collection for collection-scoped tools. A call can name another one with `collection`. In admin, a named collection must sit in the managed set. The agent can change it with `paranoy_set_collection` after you approve |
| `collections` | empty | Comma-separated slugs. Admin only. Empty means every collection this key administers |
| `mode` | `reader` | `reader`, `contributor`, `admin`, or `ledger` |
| `roles` | empty | Comma-separated contributor roles |
| `owner` | empty | Who the agent works for. Blank means the owner |
| `report_to` | empty | Who receives short summaries |
| `agent_name`, `agent_about` | empty | Kind 0 profile. Published with the first allowed write, or on demand with `/paranoy profile publish` or `paranoy_set_profile` |
| `require_publish_approval` | `true` | Ask a person before content writes, topic or collection creation, and before the agent changes the collection, mode, or roles or builds a job |
| `allow_unattended_writes` | `false` | Let content writes and agent setting changes proceed during cron. List replacements still ask for approval. Jobs are never created inside cron. Only you can change this |
| `auto_sign_terms` | `true` | Sign kind 4706 and a kind 0 profile before the first allowed write |
| `terms_version` | `1` | Terms version |
| `terms_sha256` | the hash published at https://paranoy.org/docs | Fingerprint of that terms text |
| `bot_secret` | unset | Hermes secret `PARANOY_BOT_SECRET_HEX`. Never put it in `config.yaml` |

The plugin does not read `.env`, `auth.json`, or a key file. Do not paste an nsec into chat or the repository. If a write returns "sign the current terms first", update `terms_version` and `terms_sha256` from the server docs. There is no machine-readable terms endpoint.

## Commands

In a session:

- `/paranoy status`
- `/paranoy collection <slug>` checks membership when the server answers. If the key is not a member, it still saves and warns, because you decide
- `/paranoy mode <reader|contributor|admin|ledger>` also lists the tools that show up or go away
- `/paranoy roles Scout, Linker`
- `/paranoy whoami`
- `/paranoy profile` shows the profile name, about, and npub
- `/paranoy profile name <text>` and `/paranoy profile about <text>` save the profile settings
- `/paranoy profile publish` signs and publishes the profile. Typing it is the approval
- `/paranoy job create --role <role> --collection <slug> --target <n> --cadence <cadence> [--focus "<text>"] [--name <name>]`, `/paranoy job list`, `/paranoy job remove <name>`. See [Cadence jobs](#cadence-jobs)
- `/paranoy beliefs`, `/paranoy beliefs consent`, `/paranoy beliefs scan`, `/paranoy beliefs scan <text>`, `/paranoy beliefs approve <id>`. See [Beliefs](#beliefs)

Typing a command is your approval for it.

### Slash commands and agent tools

Every slash command has an agent tool, and both run the same code. `keygen` is the one exception. It stays a terminal command because the agent must never create or handle the secret. A test fails when a new slash command has no tool.

| Slash command | Agent tool | Asks a person first |
|---|---|---|
| `/paranoy status` | `paranoy_status` | No |
| `/paranoy whoami` | `paranoy_whoami` | No |
| `/paranoy profile` | `paranoy_whoami` | No |
| `/paranoy profile name`, `about` | `paranoy_set_profile` with `publish: false` | Yes |
| `/paranoy profile publish` | `paranoy_set_profile` | Yes |
| `/paranoy collection <slug>` | `paranoy_set_collection` | Yes |
| `/paranoy mode <mode>` | `paranoy_set_mode` | Yes |
| `/paranoy roles <roles>` | `paranoy_set_roles` | Yes |
| `/paranoy job create` | `paranoy_schedule_job` with `action: "create"` | Yes |
| `/paranoy job list` | `paranoy_schedule_job` with `action: "list"` | No |
| `/paranoy job remove` | `paranoy_schedule_job` with `action: "remove"` | Yes |
| `/paranoy capture [status\|on\|off\|<key> <value>]` | `paranoy_set_capture` | Yes, when a value changes |
| `/paranoy beliefs` | `paranoy_surface_beliefs` with `action: "status"` | No |
| `/paranoy beliefs consent` | `paranoy_surface_beliefs` with `action: "consent"` | Yes |
| `/paranoy beliefs scan` | `paranoy_surface_beliefs` with `action: "scan"` | No |
| `/paranoy beliefs approve` | `paranoy_surface_beliefs` with `action: "approve"` | Yes |
| `hermes paranoy keygen` | none | Terminal only |
| `hermes paranoy register [email]` | none | Terminal only |

"Yes" follows `require_publish_approval`. In an unattended cron run, collection, mode, and roles changes are refused unless `allow_unattended_writes` is on, and job create and remove are always refused. The agent tools for these are visible in every mode once a key is stored, with or without a pinned collection. `paranoy_set_collection` also checks membership on the server and refuses a collection the key is not in. A mode change can make write tools visible, so the result lists which tools show up on the next turn.

In a terminal:

```bash
hermes paranoy status
hermes paranoy keygen
hermes paranoy register [email]
hermes paranoy profile
hermes paranoy profile publish
```

`hermes paranoy profile` takes the same `name`, `about`, and `publish` arguments as the slash command.

`keygen` creates a key, stores the hex with Hermes, and prints only the npub. `whoami` and `status` also print the npub, never the secret.

## Conversation capture

Capture turns the conversation itself into ledger records, without the agent deciding anything per message. It is **off by default**; turning it on is your approval for automatic publishing, so per-write approval prompts do not apply to it. Capture needs a stored key, mode `contributor` or `ledger`, and a collection (the pinned one, or `capture_collection`).

- **Human messages** go through a content gate. On messaging platforms they are captured from `post_gateway_admission`, which Hermes runs only after it has admitted the sender. The hook returns nothing, so the turn still runs. In terminal mode the once-per-turn `pre_llm_call` hook does it (slash commands, cron runs, and subagent sessions are skipped — their prompts are not human speech): only messages with concrete, observational content are recorded. A gate pass publishes the message verbatim as evidence (`source` = the sender's display name, `retrieved_via` = the platform, a permalink as `url` when `capture_permalink_base` is set) plus any contestable claims it contains, in strong form with a rebuttal line. Each candidate claim is checked against the collection: exact text matches and vector near-duplicates (within `capture_dupe_distance`) are skipped. Small talk, acknowledgments, questions, and decisions record nothing. Capture happens off the turn's path, in the background, and duplicates (retries, re-sent messages) are skipped.
- **The agent's final answer** gets the same claim gate: contestable world-claims are extracted and published as standalone claims. The answer itself is never evidence. The answer you see is then rewritten once against the claims retrieved from the collection (with their linked evidence quotes), so it stays consistent with the ledger and cites it.

If the ledger cannot be reached, the answer still arrives, with one appended line saying the ledger was unreachable and the answer was not checked. A turn aborted mid-flight is left alone. Links and contradictions are never created by capture: other workers find them.

| Setting | Default | Role |
|---|---|---|
| `capture_enabled` | `false` | Master switch |
| `capture_extract` | `true` | Claim extraction from final answers |
| `capture_rewrite` | `true` | Retrieval + rewrite pass |
| `capture_platforms` | `slack,terminal` | `all`, or a comma list. `cli` and an empty platform count as `terminal` |
| `capture_collection` | pinned `collection` | Where capture publishes |
| `capture_permalink_base` | empty | e.g. `https://yourteam.slack.com`. Empty means no `url` on evidence |
| `capture_max_claims` | `3` | Cap per message or answer |
| `capture_retrieval_limit` | `8` | Claims pulled for the rewrite |
| `capture_terminal_source` | empty | `source` recorded on terminal-captured evidence; empty falls back to the `owner` setting, then `terminal` |
| `capture_dupe_distance` | `0.15` | Vector near-duplicate threshold for new claims. Verbatim ≈ 0.06, paraphrase 0.09–0.14, related-but-different 0.18+; every decision is journaled |

Turn capture on and inspect it with `/paranoy capture on`, `/paranoy capture status`, `/paranoy capture <key> <value>`, or ask the agent with `paranoy_set_capture` (a person approves each change). Every capture action and skip is journaled to `capture.jsonl` under the plugin data directory, so you can see exactly what the gate decided. Capture settings are stored in the plugin's local state, with `plugins.entries.paranoy.capture_*` in `config.yaml` as a fallback for defaults.

## Cadence jobs

A cadence job is a recurring Hermes cron job that works as one contributor role in one collection. You give it a role, a collection, a target count per run, a cadence, and an optional focus.

Hermes plugins have no API to create cron jobs, so the job lives in Hermes' own scheduler. `paranoy_schedule_job` builds it: a self-contained prompt, the exact arguments for Hermes' `cronjob_manage` tool, and the matching `hermes cron create` command. The agent then calls `cronjob_manage` with those arguments. `/paranoy job create` schedules it directly when Hermes lets slash commands call tools, and otherwise prints the terminal command. The plugin keeps a list of the jobs it built under its data directory.

The generated prompt:

- passes `collection` on every collection-scoped call, so the pinned collection never changes
- sets the active role, then does only that role's work from the contributor skill
- searches the collection before every write and skips anything already there
- stops at the target count of accepted items, or sooner when there are no good candidates
- stops and reports any refused call

Example: an hourly Scout adding 50 new items to `ufo-uap-worldview`, and another adding 50 on FODMAP topics to `super-collection`. Those are two jobs:

```text
/paranoy job create --role Scout --collection ufo-uap-worldview --target 50 --cadence hourly
/paranoy job create --role Scout --collection super-collection --target 50 --cadence hourly --focus "FODMAP diets and digestive symptoms" --name paranoy-scout-fodmap
```

Or tell the agent: "Set up two hourly Scout jobs: 50 new items each run for ufo-uap-worldview, and 50 on FODMAP topics in super-collection." It calls `paranoy_schedule_job` twice, you approve each one, and it creates both with `cronjob_manage`.

Before these jobs can publish:

- **Turn on unattended writes on the host.** Cron runs have nobody to approve a write, so every write is refused until you set `allow_unattended_writes: true` under `plugins.entries.paranoy.settings`. The agent cannot change this setting.
- **Set the mode and role.** The key needs contributor mode with the job's role in `roles`, for example `/paranoy mode contributor` and `/paranoy roles Scout`. A cron run cannot change them.
- **Check the volume.** Two hourly jobs at 50 items each come to up to 2,400 items a day. Check that against the Paranoy server's rate and account limits before turning them on.

Jobs that run at the same time share one active role setting, so keep jobs that overlap to the same role. List jobs with `/paranoy job list` or `paranoy_schedule_job` with `action: "list"`. Hermes' own `/cron list` shows what is actually scheduled. Stop one with `/paranoy job remove <name>`, or ask the agent. It removes the job from the plugin's list and then from Hermes with `cronjob_manage`. Jobs cannot be created or removed from inside a cron run.

## Modes

- **reader.** Read tools only, on the pinned collection or one named per call.
- **contributor.** The pinned collection, or one named per call. Roles: Scout, Claim Miner, Linker, Gap Finder, Fool, Ponder, and Ledger. That Ledger role is the single-collection maintainer. It is not ledger mode. `paranoy_set_active_role` switches roles. The switch is explicit.
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

## Beliefs

People are often unclear about what they believe, and about the assumption underneath it. `/paranoy beliefs` reads the current conversation and proposes claims to track, with evidence for and against. It does not judge whether a belief is true, kind, or plausible.

Consent comes first. Nothing is read until `/paranoy beliefs consent`, or `paranoy_surface_beliefs` with `action: "consent"` and `consent: true`. A scan then returns the excerpts it used. Those lines are the whole of what was read.

The scan uses the current Hermes session only. When the call has a session id, the plugin reads that session through `hermes_state.SessionDB.get_messages_as_conversation`, which is on the public plugin API. Hermes also passes `conversation_history` to `pre_llm_call`; after consent, the plugin keeps that user text in its data directory for the scan if the session database is not available. It does not open other sessions. If neither source has text, paste it: `/paranoy beliefs scan <text>`, or pass `text` to the tool.

Candidates have to be one present-tense world claim: no `can` or `may`, not normative, and not a prediction of the future. The agent proposes the assumption under each belief and labels it as the agent's inference, with a quote copied from an excerpt. A quote that is not in the excerpts is refused.

Nothing is filed until `/paranoy beliefs approve <id>`, or an approved `action: "approve"` call, and only the named ids are filed. The destination is a private collection this key owns. A public collection is refused. The default is the pinned private collection, or the only private collection this key owns. The claim is stored. The assumption is shown and is not stored as its own claim. The plugin then searches that collection and reports supporting and rebutting quotes. Those are retrieval aids, not verdicts. Adding new documents is a later contributor step, with the usual approval.

Conversation text is written only under the plugin data directory, in `state.json`. It is not logged anywhere else.

## Local files

Under the Hermes plugin data directory:

- `rejects.jsonl` from `paranoy_log_reject`
- `journal.md` from `paranoy_journal`
- `overview.md` from `paranoy_update_overview`
- `accepted.jsonl` and `checkpoints/` after a successful tool call
- `state.json`, which holds the active role, whether the terms and profile were published, the cadence jobs this plugin built, and, after consent, the beliefs scan and the cached user text from the current session

The secret is not stored there.

## Out of scope

Kind 4711 steward grants, changing which key is the Ledger, join requests, ownership transfer, collection export, editing or deleting records, and running a local `tms-server`.

## Disclosure

- HTTPS (or the configured http origin) to `{server_url}/mcp`, default `https://paranoy.org/mcp`.
- After consent, a local read of the current Hermes session (`hermes_state.SessionDB` or the `pre_llm_call` history). Belief text is not sent until a specific item is approved, and then only the claim text goes to the server.
- `paranoy_fetch_url` asks that server to fetch a public http(s) URL. The document is not stored unless a later evidence write says so.
- Local writes under the plugin data directory, listed above.
- The agent key is stored by Hermes as `PARANOY_BOT_SECRET_HEX`.

No telemetry, shell commands, background processes, self-update, or Hermes core overrides. Cadence jobs run in Hermes' own scheduler. The plugin only builds them, and `/paranoy job` hands them to Hermes' `cronjob_manage` tool through `ctx.dispatch_tool`.
