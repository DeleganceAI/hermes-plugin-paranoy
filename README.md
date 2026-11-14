# hermes-plugin-paranoy

A [Hermes](https://hermes-agent.nousresearch.com/docs/developer-guide/plugins) plugin that grounds an agent in a [Paranoy](https://paranoy.org/docs) evidence ledger.

With it installed, the agent chats as it always has. In the background:

- **Capture.** When someone says something that is evidence, it is stored in your collection, with the speaker as the source.
- **Ground.** Before every reply, the ledger is searched and the agent answers from what it finds. It mentions the ledger only when something it needs is missing, when records disagree, or when you ask.

And when you want it:

- **Tools.** The agent can search the ledger, read records, ingest a page or document, and store evidence and claims at any time.
- **`/decide <question>`.** The agent works a decision from what is on record and stores the outcome.

There are no roles or modes, and one collection. [SPEC.md](SPEC.md) describes the behaviour in full.

## Install

```bash
hermes plugins install DeleganceAI/hermes-plugin-paranoy
```

```bash
hermes plugins enable paranoy
```

Enabling asks before installing `httpx`. Signing is pure-Python BIP-340, so nothing needs a native build.

## Set up

The agent needs its own key, an account, and a collection.

```bash
hermes paranoy register you@example.org
```

This makes the agent key if there is none, stores it in Hermes as `PARANOY_BOT_SECRET_HEX`, and registers it at `{server_url}/hermes`. Your email becomes the account holder and the agent acts for it. You can sign in at [paranoy.org/connect](https://paranoy.org/connect) with a code sent to that address to see the account or replace the agent. An address that already has an account is refused: sign in and add the agent's npub under **Your agents** instead. Leave the email out and the agent is its own account.

Then, in a Hermes session:

```text
/paranoy collection <slug>
/paranoy status
```

`status` shows the agent's npub, the collection, and whether capture and grounding are on. A slug that does not exist yet is created on the agent's first write, if its account may create collections. The tools appear once a key is stored and a collection is set.

The model never holds the key and has no tool that signs arbitrary data. Every call is signed in-process and posted to `{server_url}/mcp`.

## Capture

Runs on every message a person sends, on a background thread, so it never delays the reply.

A small model call decides whether the message says something about the world: something the speaker did, saw or was told, a state of the world, a decision already made, or a durable fact or preference. Small talk, questions, instructions to the agent and unacted intentions are skipped. The gate does not judge whether a statement is accurate, and it stores when unsure. Paranoy expects evidence to be noisy.

A message that passes is stored whole as evidence:

| Field | Value |
|---|---|
| `source` | The speaker's display name. In a terminal, the `owner_name` setting |
| `retrieved_via` | `utterance` |
| `title` | `<name>, <date>, <platform>` |
| `url` | A Slack permalink, when `permalink_base` is set |

Its claims are extracted and stored too. A claim the collection already has is not stored again; the new message is linked to the existing claim as `SUPPORTS`. A claim counts as the same only when its text is identical or a model reading both confirms they say the same thing.

Everyone Hermes admits to a conversation is captured, each under their own name. Bots, slash commands, scheduled runs and subagent sessions are not. The agent's own answers are never stored.

## Grounding

Before each reply the plugin runs one search of the collection's claims, using the message as the query, and adds the results to the turn with a rule: answer normally, do not mention the ledger, and speak up only for a gap the answer depends on, a disagreement in the records, or a direct question about what is on record.

That first search is the guarantee that every reply has looked. The agent then searches further on its own, as often as the message needs, choosing its own queries and result counts.

## When Paranoy is unreachable

The agent keeps working. If the grounding search fails, the reply goes out with one line added by the plugin:

> _Paranoy is unreachable (Could not reach the Paranoy server.). This reply was not grounded._

A capture that fails is reported the same way on the next reply. The line repeats on each reply until the ledger is back. `/decide` does not start without the ledger.

## Tools

| Tool | Does |
|---|---|
| `paranoy_search` | Finds claims by meaning or by words, with their links and contradictions |
| `paranoy_read` | Reads one record. Evidence text comes back a window at a time |
| `paranoy_ingest` | Stores a URL, a text or an existing evidence record's claims: the document as evidence, plus the claims extracted from it |
| `paranoy_store_evidence` | Stores one document and nothing else |
| `paranoy_store_claim` | Stores one claim, optionally linked to evidence already in the conversation. Also links evidence to an existing claim |
| `paranoy_check_proof` | Checks that each step of an argument cites a record or is marked as a gap |

Writes need no approval. If the agent's key can write to the collection, the agent can write at any time.

Links are optional, and so is the quote on a link. The agent links new evidence to existing claims when it judges that worth doing. It never searches evidence to find something to link a claim to.

## /decide

```text
/decide Should we move the queue to Postgres or keep Redis?
```

This turns on decision mode for the session. Send a message to begin. The agent then frames the options, searches the ledger as widely as it needs, lays out what supports and opposes each option and what is unknown, and checks its argument with `paranoy_check_proof`. You make the decision. Once you confirm it, the agent stores a decision record as evidence (`retrieved_via: decision`) with a claim linked to it, and decision mode ends. `/decide done` ends it without recording anything.

## Settings

Settings live under `plugins.entries.paranoy.settings`.

| Setting | Default | Meaning |
|---|---|---|
| `server_url` | `https://paranoy.org` | Calls go to `{server_url}/mcp` |
| `collection` | empty | The one collection the agent reads and writes |
| `owner_name` | empty | Source recorded for messages captured in a terminal |
| `capture` | `true` | Automatic capture. `/paranoy capture on\|off` |
| `ground` | `true` | Automatic grounding. `/paranoy ground on\|off` |
| `permalink_base` | empty | For example `https://yourteam.slack.com` |
| `agent_name`, `agent_about` | empty | The agent's profile, published with its first write. Blank publishes `paranoy-agent` / `agent` |
| `terms_version`, `terms_sha256` | current terms | The Paranoy terms the agent signs with its first write |

## The journal

Every capture decision, grounding search, write and failure is appended to `journal.jsonl` in the plugin's data directory (`~/.hermes/plugin-data/paranoy/` by default). Read it to see what was captured, what a reply was grounded on, and why something was skipped.

## Development

```bash
python -m unittest discover -s tests -t .
```

No telemetry, shell commands, self-update, or Hermes core overrides.
