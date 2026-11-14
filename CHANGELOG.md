# Changelog

## 0.5.0

The plugin is rebuilt around [SPEC.md](SPEC.md). This is a breaking change.

- **Capture** stores what people say as evidence, in the background. It is on by default and covers every platform, with each speaker under their own name and `retrieved_via: utterance`. The gate asks only whether a message says something about the world, and stores when unsure.
- **Grounding** searches the ledger before every reply and gives the agent what it finds, with a rule to answer normally and mention the ledger only for a gap, a disagreement, or when asked. The agent searches further with the tools as much as it needs.
- **Six tools**: `paranoy_search`, `paranoy_read`, `paranoy_ingest`, `paranoy_store_evidence`, `paranoy_store_claim`, `paranoy_check_proof`. Writes need no approval.
- **`/decide <question>`** turns on decision mode: unlimited ledger searching, a laid-out case for each option, and a decision record stored as evidence.
- When Paranoy is unreachable the agent still replies, and the plugin appends a warning line to the reply.
- Links are optional and a quote on a link is optional. `paranoy_ingest` and capture link new evidence to an existing claim as `SUPPORTS` only when the claim is the same: identical text, or a model confirms it.

Removed: modes, roles and their skills, cadence jobs, collection governance tools, per-write approval, capture of the agent's own answers, and the rewrite of the finished answer. Settings from those features are ignored. `/paranoy` keeps `status`, `collection`, `capture` and `ground`.

## 0.4.1

Messaging capture listens on `post_gateway_admission` instead of `pre_gateway_dispatch`. Hermes fires that hook only after the sender has been admitted, and passes `source`, `text`, and `message_id`. The hook returns nothing, so the turn still runs.

The call to the gateway's private `_is_user_authorized`, which treated a missing or failing check as allowed, is gone. An unpaired sender never reaches capture, so it is not signed or published.
