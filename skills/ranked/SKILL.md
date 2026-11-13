---
name: Paranoy ranked answer
description: >-
  Use when someone asks which option ranks highest or lowest, including
  "what is the worst seed oil and why", or asks for an answer from a Paranoy
  collection with the evidence for and against each option.
---
# Paranoy ranked answer

> **If the agent is set up wrong**, for example no tools are visible, a call says the key has not signed up or is not a member, or the person asks about the npub or profile, use the Paranoy agent setup skill (`setup`) first.

Answer from the ledger. Call `paranoy_ranked_answer` with the person's question, or they can type `/paranoy ask <question>`. Show the returned `card` as the answer. Do not add a verdict, a tone, or a claim that is not in the card.

The tool picks the collection by word overlap with `paranoy_list_collections` (slug and topic). For the demo question `what is the worst seed oil and why`, that is `seed-oils-and-sat-fat` when that collection is in the list. If it is not listed, say so. Do not invent a collection or a claim.

The rank is supporting links minus rebutting links. Qualifying links are shown and do not change the net. Say that. Counts are retrieval aids. If the card says evidence is missing, one-sided, or thin, repeat that. If it says the collection has no claim that answers the question, stop. Do not write a claim to fill the gap.

The last lines are the collection link. Leave them in the answer.
