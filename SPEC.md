# Paranoy-grounded agent: spec

Status: proposal, 2026-11-14. Part 1 describes the behaviour we want from any
agent, as if no plugin existed. Part 2 shows one way to build it in Hermes and
what that means for the current plugin.

## The idea

An assistant with a Paranoy collection behind it should do two things without
being asked, and three things when asked.

Without being asked:

1. **Capture.** When someone says something that is evidence, it is stored
   as evidence, with the speaker as the source.
2. **Ground.** Before the agent replies, it sees what the ledger holds on the
   topic and answers from it. It does this silently. The owner hears about
   the ledger only when something is missing, uncertain or in dispute, or
   when they ask.

When asked, or when the agent judges it useful:

3. **Ingest.** Turn a message, web page or document into evidence plus
   high-quality claims. Linking a claim to the passage behind it is optional.
4. **Store.** Add a piece of evidence, a claim or a link at any time.
5. **Decide.** Run a decision process that searches the ledger as much as it
   needs to, lays out what supports and opposes each option, and records the
   decision.

There are no roles, no modes, and one collection. Everything the agent may do
is available all the time.

## What Paranoy gives us to work with

Four facts about the ledger shape the design. All are from
[paranoy.org/docs](https://paranoy.org/docs).

| Fact | Consequence |
|---|---|
| Evidence has fixed fields: `collection`, `text`, `url`, `title`, `source`, `archive_url`, `retrieved_via`. Unknown fields are rejected. | There is no metadata field to hold `type: utterance`. We use `retrieved_via` for it (see [Capture](#1-capture)). |
| The only search is `search_claims`. Evidence text is not searchable. Evidence is reached through the links on a claim. | Grounding finds claims. It reaches a piece of evidence only where a link connects the two. Unlinked evidence is still stored and can be listed and read. |
| A link connects evidence to a claim with a relation and a reason. Its `quote` is optional. A batch is accepted or rejected whole. | Links carry a quote only when it helps (see [Quotes](#quotes)). |
| Records are append-only. Nothing is edited or deleted. A free account holds 10,000 records. | Paranoy expects evidence to be noisy and keeps it all. Storing something wrong or low-value is fine. The capture gate only keeps out what is not evidence at all. |

Paranoy has no confidence scores and no verdicts. Search order and link counts
help find material and do not say what is true. The grounding step passes that
on to the agent unchanged: it shows the records and leaves the judgement to
the agent and the owner.

## Part 1: behaviour

### 1. Capture

Runs on every message from a person, in the background, so it never delays
the reply.

**Gate.** One cheap model call classifies the message. It passes only if the
message states something about the world that could later be checked or
relied on:

- something the owner did, saw, measured, read or was told ("I talked to Ana
  and she said the renewal moved to March")
- a state of the world or a technical observation ("v1.4 doesn't compile on
  my machine")
- a decision already made ("we went with Postgres")
- a durable fact or preference about the owner or their work ("I never ship
  on Fridays")

It rejects small talk, acknowledgements, questions, instructions to the agent,
and intentions that have not been acted on.

**The gate filters for kind, not for truth.** Paranoy assumes most evidence
is noisy to some degree, and a stored message is a record that the owner
said it, not a finding that it is correct. So the gate does not ask whether
a statement is accurate, well-sourced or important. It asks only whether
the message says something about the world. When it is unsure, it stores.
A mistaken or low-value capture costs one record. Later evidence can rebut
it, and the judgement stays with the reader.

The gate exists for two practical reasons: chit-chat stored as evidence
would crowd the grounding results, and a free account holds 10,000 records.

**What is stored.** One batch:

- Evidence: the message text, verbatim and whole.
  - `source`: the speaker's display name
  - `retrieved_via`: `utterance`
  - `title`: `<name>, <date>, <platform>`
  - `url`: a permalink to the message, when the platform has one
- Claims: every claim the message supports (see
  [Claim quality](#claim-quality)).

If a claim already exists in the collection, no new claim is made, and the
message is linked to the existing claim as `SUPPORTS` (see
[Linking](#linking)). Capture makes no other links: it runs in the
background with no agent to judge how the message bears on other claims. If
the gate passes but no claim survives extraction, the evidence is still
stored.

**Who is captured.** Everyone the host has admitted to the conversation. In
a group chat that means each person who speaks, with their own display name
as `source`, so the record shows who said what. In a terminal session the
speaker is the owner. Agent prompts, scheduled runs, subagent traffic and
other bots are not speech and are never captured.

**The agent's own answers are never captured**, as evidence or as claims. If
the agent's statements entered the ledger, the grounding step would later
show the agent its own guesses as recorded claims. The ledger only takes in
what came from the owner or from a document.

**The record.** Every gate decision is written to a local journal: stored or
skipped, and why. The owner can read it to see what was captured and to tune
the gate.

### 2. Ground

Runs before the model answers each message, whoever sent it. It has two parts:
a first search the host always runs, and as much further searching as the
agent judges the message needs. The reply waits for both. There is no limit
on the number of searches.

1. **First search, by the host.** Search claims by meaning, using the
   owner's message as the query. This is the guarantee: every reply has
   looked at the ledger, whatever the model chooses to do.
2. Put the results in the model's context as a block. Each entry has the
   claim text, its id, the evidence that supports, rebuts or qualifies it
   (with a quote where one exists), and a note if it belongs to a
   contradiction.
3. Add the rule the agent answers under:

   > These are the records the ledger holds on this topic. Use them as what
   > you know, and answer the way you normally would. Do not mention the
   > ledger, claims, evidence, ids or where a fact came from, and do not
   > report what you looked up or what was stored.
   >
   > Speak up only in these cases, and then in one plain sentence:
   > - Your answer depends on something about the owner or their work that
   >   these records do not cover. Say you don't know it, or that you are
   >   guessing.
   > - Records here disagree on something the answer depends on. Say what
   >   the disagreement is.
   > - The owner asks what you know, how you know it, or what is on record.
   >   Then show the records and their sources in full.
   >
   > This first search used the owner's message as it was written. Before
   > answering, search again as often as you need: with your own wording,
   > for each topic the message touches, for what the conversation refers
   > to, and for evidence against what you found. Read the evidence behind
   > a claim when the answer rests on it. Stop when further searches turn
   > up nothing new.

4. **Further searching, by the agent.** The agent runs its own searches with
   the tools, choosing the queries, the method and how many results to ask
   for. It follows links and reads evidence in full where that matters.
   This is the same searching `/decide` does, scaled by the agent to what
   the message needs: none for a regex, several for a question about the
   owner's work.

The first search alone is weak. A message like "what about the second one?"
is a poor query, and one query cannot cover a message that touches three
topics. The agent knows the conversation and can ask better questions, so
the depth of grounding is its call.

**The reply reads like any other assistant's.** Grounding changes what the
agent says and leaves how it says it alone. A grounded answer carries no
citations, no "according to the ledger", and no summary of what was
retrieved. The same holds for capture: the agent does not announce that it
stored something.

**A gap is the exception worth a sentence.** When the search returns nothing
relevant, the block tells the agent so. The agent mentions it only if the
answer needs the missing fact. "I don't have anything on when the renewal
is" is right when the owner asks about the renewal. It is noise when the
owner asks for a regex.

General knowledge is used freely and without a label. The rule covers the
owner, their work, and facts the records dispute. An agent that flagged
everything the ledger does not hold would flag nearly everything.

**Sources on request.** "How do you know that?", "what do we have on X?" and
`/decide` are the owner asking. The agent then gives claims, quotes and
sources. The records behind each reply are also written to the local
journal, so the owner can check what a reply rested on without the agent
saying it in chat.

The first search runs on every message a person sends, small talk
included, because deciding what counts as small talk would cost a model call
before every reply. The block tells the agent that some of what the search
returned may not bear on the message, and to ignore it.

### When Paranoy is unreachable

The agent keeps working and the owner is always told. A broken ledger does
not take down the rest of the setup, and it is never silent.

- If the grounding search fails, the reply goes ahead
  ungrounded, and the host appends one warning line to it:
  "Paranoy is unreachable (<reason>). This reply was not grounded."
- The host adds the line, not the model, so it cannot be left out. It is the
  one exception to the rule that replies do not mention the ledger.
- It appears on every reply while the ledger is down, and stops when the
  ledger is back.
- A capture that fails in the background is reported the same way on the
  next reply: "Paranoy is unreachable (<reason>). An earlier message was not
  recorded."
- A tool call that fails returns the error to the agent, which tells the
  owner what it could not do.
- `/decide` does not start without the ledger. It says so and stops, since
  a decision process with nothing to search is not the thing asked for.
- Every failure is journaled with its cause.

Messages missed during an outage are not stored later. The warning says so,
and the owner can ask for them to be stored once the ledger is back. A retry
queue can be added if outages turn out to be common.

### 3. Tools

Six tools, always available. The agent never signs anything itself and never
sees the key. Signing happens in the host.

| Tool | Does |
|---|---|
| `search(query, method?, limit?)` | Finds claims by meaning or by words. Each result carries its links, quotes, evidence titles and contradiction ids. |
| `read(id, offset?)` | Returns one record. For evidence, returns the text a window at a time. |
| `ingest(url \| text + source \| evidence_id)` | Stores the document as evidence if it is new, extracts claims, and publishes the batch. Returns the evidence id, the claims stored, and the existing claims its searches turned up, so the agent can consider linking to them. |
| `store_evidence(text, source, title?, url?)` | Stores one document and nothing else. |
| `check_proof(proof)` | Checks the structure of an argument: every step cites a record or an earlier step, or is marked as a gap. Used by `/decide`. |
| `store_claim(text, link?)` | Stores one claim. With `link` (`evidence_id`, `relation`, `reason`, `quote?`) it also links it. With `claim_id` in place of `text`, it links evidence to an existing claim, including as `REBUTS` or `QUALIFIES`. |

`ingest` is the main write path. Capture is `ingest` with a gate in front of
it, run automatically on what people say.

#### Linking

Links are optional. Storing evidence and claims with no links between them
is a complete, valid write.

**The one direction that is searched: new evidence to existing claims.**
When a piece of evidence is being added, the agent has it in hand and can
search claims for ones it bears on. Claim search is what the server is
built for and stays fast however large the collection gets.

- **Automatic, same claim only.** `ingest` and capture search for each
  extracted claim to avoid duplicates. When an extracted claim is the same
  as an existing one, the new evidence is linked to the existing claim as
  `SUPPORTS`. "The same" means the text is identical, or a model reading
  both confirms they say the same thing. The relation is safe because the
  claim was extracted from this evidence, so the evidence states it. No
  extra search is run.
- After `ingest`, the agent may run claim searches on what the evidence
  says, and link the evidence to existing claims, giving a reason for each.
  This is where a new document gets connected to a claim it contradicts.
- **For these, the agent reads both and picks the relation.** `SUPPORTS`, `REBUTS` or
  `QUALIFIES` is a judgement about what the evidence says and what the
  claim says. Search closeness does not give it: "the new pump used less
  power" and "the new pump used more power" sit close together in a
  meaning search and are opposites. So apart from the same-claim case above, no link is
  made without the agent's judgement.
- The agent decides how far to take it. It is never required.

**The direction that is never searched: claims to evidence.** The agent does
not search or list evidence to find something to link a claim to. A
collection is assumed to hold millions of evidence records and evidence is
not searchable by meaning. A claim is linked to evidence only when that
evidence is already in the current conversation, because the agent stored
or read it a moment ago and still has its id.

**Writes need no approval.** If the agent's key has permission to write to
the collection, the agent can write at any time. Membership is the
permission, and there is no second check in the plugin. Every write is
journaled.

Not in the first version: recording contradictions, managing members, and
anything about more than one collection. The agent reads contradictions
through `search` and reports them.

#### Quotes

A link says this evidence bears on this claim, and why. That is the whole
link. A quote is an optional pointer to where in the document the passage
is. It earns its place when the evidence is large, such as one paragraph in
a long PDF. For a chat message or a short page it adds nothing.

- The agent never has to produce a quote. No write is held back or dropped
  for lack of one.
- The agent may include one when it has the passage in front of it and the
  document is long enough for a pointer to help.
- Grounding and `/decide` show a quote when a link has one and do not treat
  a link without one as weaker.

**Server support.** Paranoy is dropping the quote requirement: `quote` is
optional on kind 4703 and is checked against the evidence only when present.
The plugin is built for that behaviour and has no workaround for the old
one.

#### Claim quality

`ingest` and capture use the same standard. A claim is published only if it is:

- **One statement.** Split compound sentences.
- **Readable alone.** Names in place of pronouns, with the date, version or
  condition that the source gives.
- **No stronger than its source.** "In the two-hour test the new pump used less
  power" and not "the new pump uses less power".
- **Checkable.** Someone could describe evidence that would count against it.
  If nobody could, it is dropped.
- **About the world, in the third person.** The evidence record says who
  said it. The claim says what was said.
- **Faithful to the source.** It says what the document says.
- **Not a duplicate.** Search first. A claim with the same text is reused.
  A close match is reused only when the model reading both confirms they
  say the same thing. Closeness in the search is not enough, since a claim
  and its opposite are close.

A message or document produces as many claims as it supports. There is no
cap.

### 4. Decide

The one command. `/decide <question>` starts it, and it ends when the decision
is recorded or the owner cancels.

While it is active, the agent has no limit on ledger calls and follows this
procedure:

1. **Frame.** State the decision, the options, and what would make one better
   than another. Ask the owner if any of that is unclear.
2. **Search widely.** For each option and each criterion, search by meaning
   and by words, follow links to the evidence, read the passages in full, and
   look for rebuttals and contradictions as hard as for support. Keep
   searching while new queries turn up new records.
3. **Fill gaps, if the owner wants.** Find outside sources and `ingest` them,
   so the decision rests on records.
4. **Lay it out.** For each option: what supports it, what opposes it, what is
   disputed, each with the claim and evidence it comes from. Then a list of what
   is unknown: the questions the ledger could not answer.
5. **Check the argument.** Write the reasoning as a Paranoy thinking proof
   and pass it to `check_proof`. This confirms every step cites a record or is
   marked as a gap. It does not confirm the reasoning is right.
6. **Decide.** The agent gives a recommendation if asked. The owner makes the
   decision.
7. **Record it.** Once the owner confirms, store a decision record as
   evidence (`source`: the owner, `retrieved_via`: `decision`) holding the
   question, the options, the choice, the reasons, the ids relied on, and the
   unknowns. Add a claim, "On <date>, <owner> decided <choice> for
   <question>", linked to it. Later grounding will then surface the decision
   whenever the topic comes up.

The decision record is also the thing to export to other systems. The earlier
note mentioned sending it to Cognee as a short note. That is outside this
spec, and the record is the hook for it.

### Setup and settings

Setup is a key and a collection: generate the agent's key, register it, name
the collection. The current one-command registration already does this.

| Setting | Default | Meaning |
|---|---|---|
| `collection` | none | The one collection the agent reads and writes |
| `owner_name` | none | Recorded as `source` in a terminal session, where the host gives no display name |
| `capture` | on | Automatic capture of what people say |
| `ground` | on | Automatic grounding before each reply |

The spec sets no numeric limits. The agent chooses how many results each
search asks for, and how many searches to run.

### What this leaves out, on purpose

- Roles, modes and per-role tool visibility
- Scheduled jobs
- Collection administration: members, admins, stewards, agents
- Per-write approval prompts
- Capturing claims from the agent's answers
- Rewriting the agent's answer after the fact

## Part 2: building it in Hermes

This part describes the plugin as built. Three things differ from Part 1
because of how Hermes works:

- **`/decide` cannot start a model turn.** A Hermes slash command returns
  text and does not run the model. So `/decide <question>` turns decision
  mode on and the next message begins the work.
- **The warning line is added in `transform_llm_output`**, the hook that
  may replace the final reply text.
- **Re-ingesting a URL stores a second copy.** Checking whether a document
  is already stored would mean scanning evidence, which is ruled out.

| Behaviour | Hermes surface | Module |
|---|---|---|
| Capture, messaging platforms | `post_gateway_admission` hook, background thread | `capture.py` |
| Capture, terminal | `pre_llm_call` hook, background thread | `capture.py` |
| Ground, first search | `pre_llm_call` returns `{"context": ...}`, which Hermes adds to the user message. It fires once per turn and the turn waits for it. | `ground.py` |
| Ground, further searching | Ordinary tool calls by the agent | `tools.py` |
| Warning line | `transform_llm_output` | `ground.py` |
| Tools | `ctx.register_tool`, shown once a key and a collection are set | `tools.py` |
| `/decide` | A slash command that sets a flag for the session. `pre_llm_call` adds the procedure from the `decide` skill while the flag is set. Storing the decision record clears it. | `ground.py`, `skills/decide` |
| Extraction and same-claim checks | `ctx.llm.complete_structured` | `ledger.py` |
| Signing, transport, registration, journal | Unchanged from the earlier plugin | `signing.py`, `schnorr.py`, `client.py`, `state.py` |

Injection replaced the earlier plugin's answer rewrite: it costs no extra
model call, the agent sees the records while it can still search further,
and the owner reads the agent's own answer and not a second model's
revision of it.

Removed: modes and roles, cadence jobs, collection governance, capture of
the agent's answers, the answer rewrite, and local quote matching (quotes
are optional and the server checks one when it is given). Roughly 9,300
lines became about 2,400, tests included.

### Still to verify against a live setup

- That the live server accepts a link with no quote.
- Whether Hermes caps tool calls per turn. Its plugin docs do not say. If
  it does, a long `/decide` could be cut short.
- That `pre_llm_call` holds the turn for the search on every platform.
  Hermes gives a hook 30 seconds by default (`plugins.hook_callback_timeout`)
  and skips it after that, which would leave a reply ungrounded with no
  warning.
- That the `/decide` handler is passed a `session_id`. Without one, decision
  mode applies to every session until it ends.

## Settled

- **`utterance` goes in `retrieved_via`.** Today the plugin puts the
  platform there (`slack`, `terminal`) and the sender's display name in
  `source`, and nothing marks a record as an utterance. The spec moves the
  platform into `title`.
- **Writes need no approval.** Permission to write is the only gate.
- **Group chats are captured**, each speaker under their own name.
- **Decisions are stored as evidence** with the owner as `source`. The
  record is signed by the agent's key, so a reader can see who wrote it.

## Known limit

Grounding searches claims, and only claims are embedded on the server. A
claim leads back to evidence only where a link was made, and evidence with
no claim and no link is not surfaced by grounding. This is accepted. If it
becomes a problem, the fix is on the server (word search or embeddings over
evidence).
