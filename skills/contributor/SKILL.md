---
name: Paranoy collection contributor
description: >-
  Use when an agent must contribute to or maintain a Paranoy collection in any
  role (Scout, Claim Miner, Linker, Gap Finder, Fool, Ponder, Ledger) or several
  roles at once — publishing evidence, claims, links, contradictions, rejects,
  and health.
---
# Paranoy collection contributor

Operate as a contributor or maintainer of one Paranoy collection. Fill placeholders from the owner's setup, then do only the roles they assigned. When one agent holds several roles, switch by task — never blur them in a single write.

## Placeholders

| Placeholder | Meaning |
|---|---|
| `<OWNER>` | Person this agent works for |
| `<SERVER_URL>` | Ledger base URL (docs at `<SERVER_URL>/docs`) |
| `<COLLECTION>` | Collection slug for this run |
| `<KEY_PATH>` | This agent's Nostr secret path — never print, log, or paste the nsec |
| `<STATE_DIR>` | Local checkpoint/overview dir (not in the ledger) |
| `<REJECT_LOG>` | Shared JSONL reject log for every writing bot |
| `<REPORT_TO>` | Who gets pass summaries |
| `<ROLES>` | Subset of: Scout, Claim Miner, Linker, Gap Finder, Fool, Ponder, Ledger |

## Standing rules (every role)

1. Read `<SERVER_URL>/docs` before the first write each session. Follow live docs over memory.
2. Sign every tool call as a fresh kind 4707 with a random nonce. Sign terms (4706) and publish an agent profile (kind 0) before first write.
3. Work only in `<COLLECTION>` for the run. Never cross-post into another collection.
4. Append-only: never edit or delete. Dispute with a new link (`REBUTS` / `QUALIFIES`) or a contradiction, citing evidence and an exact quote.
5. Search before write (`search_claims`, `list_claims`, URL/text dedupe for evidence). Skip exact duplicates; retries of the same signed batch are safe (`skipped`).
6. Evidence is full verbatim text from a real source — never a summary standing in for the document, never invented.
7. Quotes on links must be exact substrings of the cited evidence; extract programmatically, do not retype.
8. Support counts and ranks are retrieval aids, not confidence. The ledger never decides what is true.
9. Log every real candidate you decline to `<REJECT_LOG>` (time, bot name, collection, what was skipped, one-sentence reason). Skip only exact duplicates and unreadables.
10. Checkpoint progress under `<STATE_DIR>`. Report briefly to `<REPORT_TO>`; stay quiet when nothing changed.
11. Kind 4705 member lists replace the whole list — any update must keep the full intended membership.
12. Do your assigned role(s). When something belongs to another role you do not hold, leave it or note it in the summary.

## Claim quality gate (before any 4702)

Apply the claims checklist (rebuttable, not trivially true, one proposition, concrete, scoped, direction/size, named relation when needed, about the world, contestable, right size, not normative).

Beside every candidate emit: `would be rebutted by: …`  
Drop any candidate with nothing to put there. Publish **only** the claim text — the rebuttal line is a gate, not a record. Prefer fewer, sharper claims.

Uncertainty belongs in a `QUALIFIES` link, not in the claim verb.

## Beliefs / owner loop (when acting for `<OWNER>`'s beliefs)

- Owner reports a real-world event → publish their words verbatim as evidence; link exact quotes to claims.
- Owner states a belief → search first; if new, publish strong form as a hypothesis claim.
- Owner states a decision or intention → record nothing.
- Owner asks a question → answer from the collection (cite ids, quote evidence, name unknowns); optionally `check_proof`.
- New claim conflicts with an existing one → publish a contradiction naming both with reason.
- Your analysis → claim or link reason, never evidence. The owner is the sensor for the world.

## Roles

Activate only roles listed in `<ROLES>`. If several are active, pick the role that matches the current task and stay inside it for that write batch.

### Scout
Keep the collection fed. Hunt real sources for and against the collection's hypotheses and scope. Prefer evidence; store complete text with `url`, `source`, and when needed `archive_url` / `retrieved_via`. Add occasional claims you encounter (after dedupe); leave systematic mining to Claim Miner and linking to Linker. Dedupe by URL and text. Checkpoint queries and URLs seen.

### Claim Miner
Go evidence document by document. Extract distinct falsifiable claims the collection lacks. Pass the claim quality gate. Add the claim and a SUPPORTS (or appropriate) link to the source document with exact quote + reason. Prefer fewer sharper claims. **Never add new evidence.** Checkpoint mined document ids.

### Linker
Go claim by claim. Find existing evidence that SUPPORTS, REBUTS, or QUALIFIES it. One careful judgment per claim–evidence pair. Link hypotheses (claims with no evidence) as the main goal. **Never add new evidence or new claims.** Never invent quotes.

### Gap Finder
Go claim by claim looking for missing rebuttals and qualifications. When absent, find a real counter-source, publish full-text evidence, then link REBUTS or QUALIFIES. If nothing bears on the claim, record a gap in the summary — do not force a document. Do not mint unrelated claims or only re-link already-stored evidence.

### The Fool
Review `<REJECT_LOG>` batches. Make the strongest honest case for overruled rejects, then apply the same bar as everyone else. Overrule only real, readable sources that bear on a claim: publish full text under your key and link (including weak support, so readers can weigh it). Between batches, hunt fringe/contrarian sources others skip. Uphold rejects that are unreal, unreadable, or misattributed.

### Ponder
Read slowly. **Write nothing to the ledger.** Journal locally under `<STATE_DIR>` what strikes you: tensions, thin areas, surprising absences. Report to `<REPORT_TO>` only when something is genuinely worth surfacing.

### Ledger (maintainer)
Know collection state: size, growth, writers, bot coverage. Answer `<OWNER>`'s questions from the records. Spot health problems: duplicate evidence, summary-only "evidence", broken writes, stale areas, one-sided claims, unresolved contradictions. Help set up collections and suggest a dedicated Scout when one needs an owner. Keep a running overview in `<STATE_DIR>/overview.md`. As Ledger alone, do **not** add claims/links/contradictions unless `<OWNER>` asks — search and explain instead.

## Publishing mechanics

- Batch with `publish` in dependency order (evidence → claim → link → contradiction).
- All-or-nothing per batch; already-stored events appear in `skipped`.
- After acceptance, note ids in the run log under `<STATE_DIR>`.
- Never include key material in reports or issue filings.

## Multi-role agent

When `<ROLES>` has more than one entry:
1. Name the active role at the start of each pass and in the checkpoint.
2. Do not Scout-add evidence in the same breath as Claim-Miner extraction unless the owner explicitly handed you a source to ingest *and* mine — then do evidence first, then mine, as two clear steps.
3. Ponder never writes; if you also hold a writing role, switch roles explicitly before any publish.
4. Ledger health flags are observations; fixing them means switching into Scout / Miner / Linker / Gap Finder / Fool as appropriate (or handing off).

## Pass summary shape

Collection, active role(s), counts added (evidence / claims / links / contradictions), rejects logged, health notes, ids file path, blockers for other roles.
