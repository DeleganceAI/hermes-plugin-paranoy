---
name: Paranoy collection reader
description: >-
  Use when any agent needs to read, answer from, or skillfully consume a Paranoy
  collection without writing to it — questions, briefings, proof checks, or
  navigating evidence/claims/links.
---
# Paranoy collection reader

> **If the agent is set up wrong**, for example no tools are visible, a call says the key has not signed up or is not a member, or the person asks about the npub or profile, use the Paranoy agent setup skill (`setup`) first.

Be a careful consumer of one Paranoy collection. Read only. Do not publish, edit, or dispute records unless a separate contributor skill (or the owner) authorizes writes.

## Before anything else

1. Read the live protocol at `<SERVER_URL>/docs` (default `https://paranoy.org/docs`), especially evidence, claims, links, contradictions, and what ranks/counts mean.
2. Confirm you can access the named collection (member or owner) with `paranoy_list_collections`. Public collections are also listed by `paranoy_list_public_collections`. You can read a public one when this key is a member, which kind 4715 does immediately unless that collection was skipped. If it is not a member, stop and say so. To read a collection other than the pinned one, pass `collection` on each call. It applies to that call only, and the pin stays. To change the pin, call `paranoy_set_collection`, which a person approves.
3. Never treat support counts, distances, ranks, or "contested" sorts as confidence or verdicts. They are retrieval aids only. The reader (usually the human owner) decides what is true.

## What the records mean

| Record | Kind | What it is |
|---|---|---|
| Evidence | 4701 | Full verbatim source text. Storing it says it was read, not that it is correct. |
| Claim | 4702 | One checkable statement. May be a hypothesis with no links yet. |
| Link | 4703 | Evidence → target with an exact quote + reason: `SUPPORTS`, `REBUTS`, or `QUALIFIES`. |
| Contradiction | 4704 | Names claims that cannot all hold, with a reason. The ledger does not resolve it. |

Append-only: older records stay. A correction is a new claim or a new link that REBUTS/QUALIFIES an earlier one — never an edit.

## How to answer a question from the collection

1. **Search first.** `search_claims` (and `list_claims` / `search_evidence` / `search_contradictions` as needed). Do not invent what is in the collection.
2. **Open the trail.** For each relevant claim, `get_links` and pull the cited evidence with `get_event` (page the text with character `offset` until you have the quote).
3. **Check the quote.** Confirm the link's quote appears in the evidence. The server checks substring presence; you still judge whether the reason overclaims the passage.
4. **Cite, don't summarize away.** Answer with claim ids, short quotes from evidence, and the link relation. Name who signed each record when authorship matters.
5. **Say what is thin or missing.** Unsupported claims, one-sided support (only vendor self-report, only secondary press), open contradictions, and topics with no evidence yet.
6. **Optional structure.** For a high-stakes answer, use `check_proof` so the argument is inspectable — still not a verdict.

## How to read skillfully

- Prefer primary evidence over secondary reporting of the same finding.
- Ten SUPPORTS links that all cite one article are one source, not ten.
- A hypothesis (claim, no links) is a question the collection has not answered yet — not a fact.
- Contradictions are invitations to inspect scope (same test? same date? same population?), not automatic cancellations.
- Fringe or unpopular evidence still belongs if it is real; weigh it, don't erase it from the answer.
- Your own analysis stays out of the ledger when you are only reading. If you form a new belief about what the records show, state it as *your reading*, not as collection content.

## Output shape (default)

Lead with the answer in plain words, then:
- Claims you relied on (id + one-line text)
- Key evidence quotes (with evidence id)
- Open contradictions or gaps
- What remains unknown

Stay quiet on writes. Hand contributor work to a maintainer agent or load the contributor skill when the owner asks to change the collection.
