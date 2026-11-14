---
name: Paranoy agent setup
description: >-
  Use when someone asks to set up Paranoy, asks what your npub is, or asks why
  the ledger is not working. Also use when a Paranoy call fails with a
  membership or sign-up error.
---
# Paranoy agent setup

Help the person get this agent working on Paranoy. Never ask for, print, or handle the agent's secret. The plugin holds the key and signs every call.

| Step | Who | How |
|---|---|---|
| Make the key and the account | The person, in a terminal | `hermes paranoy register <email>` |
| See the npub and what is missing | The person | `/paranoy status` |
| Choose the collection | The person | `/paranoy collection <slug>` |
| Turn capture or grounding off or on | The person | `/paranoy capture off`, `/paranoy ground off` |

The Paranoy tools appear once a key is stored and a collection is set.

## Common errors

| Message contains | Meaning |
|---|---|
| `has not signed up and is not a member of any collection` | The key has no account. Run `hermes paranoy register <email>`, or add the npub under **Your agents** on `<server>/connect`. |
| `not a member of collection` | The key is not ticked for that collection under **Where they work** on `<server>/connect`. |
| `sign the current terms first` | The terms changed. Update `terms_version` and `terms_sha256` in the plugin settings to the values in the server docs. |
| `You have reached the free-account limit` | The account is at its record limit. The message says who to email. |
| `Could not reach the Paranoy server` | Network or server trouble. Replies carry a warning line until it is back. |
