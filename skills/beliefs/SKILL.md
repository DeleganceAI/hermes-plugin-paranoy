---
name: Paranoy beliefs
description: >-
  Use when someone asks what they believe, what a belief rests on, or to track
  evidence for and against a belief from this conversation. Also use when they
  type /paranoy beliefs or ask to file a surfaced belief.
---
# Paranoy beliefs

> **If the agent is set up wrong**, for example no tools are visible, a call says the key has not signed up or is not a member, or the person asks about the npub or profile, use the Paranoy agent setup skill (`setup`) first.

Help the person see a belief they stated, and the assumption underneath it, then track evidence for and against that belief. Do not judge whether the belief is true, polite, or plausible. Relevance is the only filter: it is a world claim they are treating as so.

## Consent and privacy

Do not scan until the person has consented. Ask them to type `/paranoy beliefs consent`, or call `paranoy_surface_beliefs` with `action: "consent"` and `consent: true`. A person approves that call. Consent is local. It does not send the conversation anywhere.

The scan reads the current Hermes session only: the session database when this call has a session id, otherwise the history Hermes passed to the plugin on this conversation. It does not read other sessions. If that history is missing, ask the person to paste the text and pass it as `text`. Show them the excerpts the tool returns. Those are exactly the lines that were read.

Nothing is filed, and nothing is sent to Paranoy, until the person approves specific items.

## How a turn goes

1. Call `paranoy_surface_beliefs` with `action: "scan"`. If they pasted text, pass it as `text`.
2. Show the excerpts and the candidates. Do not add praise, criticism, or a verdict.
3. For each candidate worth tracking, call `action: "propose"`. Each item needs:
   - `claim`: one present-tense world proposition. No `can` or `may`. Not normative (`should`, `ought`, `must`). Not a prediction (`will`, `going to`). Not two claims joined by `but`.
   - `quote`: a span copied from an excerpt. If it is not in the excerpts, the tool refuses it.
   - `assumptions`: one or more short sentences. These are your inference about what the belief rests on. Say that. Do not present them as something the person said.
   - `would_be_rebutted_by`: what would show the claim is false. This is a gate. It is not stored on the claim.
4. Show the proposals, including each assumption labelled as your inference, and ask which ids to file. Do not approve them yourself.
5. After they approve, call `action: "approve"` with those `ids` only. Leave `collection` empty unless they named a private collection this key owns. A public collection is refused. The default is the pinned private collection, or the only private collection this key owns.
6. Show the compact result: the claim id, the assumptions, and the supporting and rebutting quotes the collection already has. Counts and quotes are retrieval aids, not verdicts. Say so when the collection has nothing either way.
7. New evidence is a later step, through the contributor roles, and only if they ask. Do not invent a document.

The person can do the consent, scan, and approve steps themselves with `/paranoy beliefs consent`, `/paranoy beliefs scan`, and `/paranoy beliefs approve <id>`.
