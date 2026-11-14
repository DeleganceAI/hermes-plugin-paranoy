---
name: Paranoy-grounded decision
description: >-
  The procedure for making a decision grounded in the Paranoy ledger. Runs
  while decision mode is on, which the person starts with /decide <question>.
---
# Deciding from the ledger

The person asked to make this decision from what is on record. While decision mode is on, the rule about not mentioning the ledger does not apply: show records and sources. Make as many ledger calls as the decision needs. There is no limit.

1. **Frame.** State the decision, the options, and what would make one better than another. Ask the person if any of that is unclear.
2. **Search widely.** For each option and each criterion, run `paranoy_search` by meaning and by words. Follow links to the evidence and read the passages in full with `paranoy_read`. Look for rebuttals and contradictions as hard as you look for support. Keep searching while new queries turn up new records.
3. **Fill gaps, if the person wants.** Find outside sources and store them with `paranoy_ingest`, so the decision rests on records.
4. **Lay it out.** For each option: what supports it, what opposes it, and what is disputed, each with the claim and evidence it comes from. Then list what is unknown: the questions the ledger could not answer.
5. **Check the argument.** Write the reasoning as a proof and pass it to `paranoy_check_proof`. Fix every problem it reports. This confirms each step cites a record or is marked as a gap. It does not confirm the reasoning is right.
6. **Decide.** Give a recommendation if asked. The person makes the decision.
7. **Record it, once the person confirms.**
   - `paranoy_store_evidence` with `source` set to the person's name, `retrieved_via` set to `decision`, and a `text` holding the question, the options, the choice, the reasons, the ids relied on, and the unknowns.
   - `paranoy_store_claim` with the text "On <date>, <person> decided <choice> for <question>" and a `SUPPORTS` link to that evidence id.

Storing the decision record ends decision mode. The person can also end it with `/decide done`.
