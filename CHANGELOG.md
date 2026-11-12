# Changelog

## 0.4.1

Messaging capture listens on `post_gateway_admission` instead of `pre_gateway_dispatch`. Hermes fires that hook only after the sender has been admitted, and passes `source`, `text`, and `message_id`. The hook returns nothing, so the turn still runs.

A gateway authorization check is no longer required on that path. If a gateway is supplied anyway, `_is_user_authorized` must exist and return true. A missing method or any error skips the message, so an unpaired sender is not signed or published.
