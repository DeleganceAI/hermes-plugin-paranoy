# Changelog

## 0.5.0

Surface beliefs from the current conversation: `/paranoy beliefs scan` or `paranoy_surface_beliefs`. A scan shows the excerpts it read and the claim-shaped statements in them; the agent adds the assumptions it infers underneath, labelled as its inference. Nothing is filed until you approve specific items, and only into a private collection this key owns.

There is no separate consent step: asking for a scan is the go-ahead, since Hermes already holds the conversation and a scan sends nothing. The fallback copy of the latest turn's text is kept in memory only, never written to disk.

## 0.4.1

Messaging capture listens on `post_gateway_admission` instead of `pre_gateway_dispatch`. Hermes fires that hook only after the sender has been admitted, and passes `source`, `text`, and `message_id`. The hook returns nothing, so the turn still runs.

The call to the gateway's private `_is_user_authorized`, which treated a missing or failing check as allowed, is gone. An unpaired sender never reaches capture, so it is not signed or published.
