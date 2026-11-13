# Changelog

## 0.4.1

Messaging capture listens on `post_gateway_admission` instead of `pre_gateway_dispatch`. Hermes fires that hook only after the sender has been admitted, and passes `source`, `text`, and `message_id`. The hook returns nothing, so the turn still runs.

The call to the gateway's private `_is_user_authorized`, which treated a missing or failing check as allowed, is gone. An unpaired sender never reaches capture, so it is not signed or published.
