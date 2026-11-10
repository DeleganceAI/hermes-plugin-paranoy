"""In-process Nostr signing for Paranoy. Never registered as a tool.

The secret is read from Hermes and stays inside this module. Event results
contain the public key, the id, and the signature, never the secret.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import time

from . import schnorr

_CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"
_GENERATORS = [0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3]


class SecretError(Exception):
    """The stored secret is missing or not a usable key. The value is not included."""


def _polymod(values: list[int]) -> int:
    checksum = 1
    for value in values:
        top = checksum >> 25
        checksum = ((checksum & 0x1FFFFFF) << 5) ^ value
        for index in range(5):
            if (top >> index) & 1:
                checksum ^= _GENERATORS[index]
    return checksum


def _hrp_expand(hrp: str) -> list[int]:
    return [ord(char) >> 5 for char in hrp] + [0] + [ord(char) & 31 for char in hrp]


def _convertbits(data: list[int], from_bits: int, to_bits: int, pad: bool) -> list[int] | None:
    accumulator = 0
    bits = 0
    result: list[int] = []
    limit = (1 << to_bits) - 1
    for value in data:
        if value < 0 or value >> from_bits:
            return None
        accumulator = (accumulator << from_bits) | value
        bits += from_bits
        while bits >= to_bits:
            bits -= to_bits
            result.append((accumulator >> bits) & limit)
    if pad:
        if bits:
            result.append((accumulator << (to_bits - bits)) & limit)
    elif bits >= from_bits or ((accumulator << (to_bits - bits)) & limit):
        return None
    return result


def _bech32_encode(hrp: str, data: bytes) -> str:
    words = _convertbits(list(data), 8, 5, True)
    if words is None:
        raise SecretError("Could not encode a public identity.")
    polymod = _polymod(_hrp_expand(hrp) + words + [0, 0, 0, 0, 0, 0]) ^ 1
    checksum = [(polymod >> 5 * (5 - index)) & 31 for index in range(6)]
    return hrp + "1" + "".join(_CHARSET[word] for word in words + checksum)


def _bech32_decode(text: str) -> tuple[str, bytes]:
    if not text or any(ord(char) < 33 or ord(char) > 126 for char in text):
        raise SecretError("The stored secret is not a usable key.")
    if text != text.lower() and text != text.upper():
        raise SecretError("The stored secret is not a usable key.")
    text = text.lower()
    separator = text.rfind("1")
    if separator < 1 or separator + 7 > len(text):
        raise SecretError("The stored secret is not a usable key.")
    hrp = text[:separator]
    try:
        words = [_CHARSET.index(char) for char in text[separator + 1 :]]
    except ValueError as exc:
        raise SecretError("The stored secret is not a usable key.") from exc
    if _polymod(_hrp_expand(hrp) + words) != 1:
        raise SecretError("The stored secret is not a usable key.")
    decoded = _convertbits(words[:-6], 5, 8, False)
    if decoded is None:
        raise SecretError("The stored secret is not a usable key.")
    return hrp, bytes(decoded)


def encode_npub(pubkey: bytes) -> str:
    return _bech32_encode("npub", pubkey)


def secret_to_hex(stored: str) -> str:
    """Accept 64-character hex or an nsec. The input is not returned."""
    text = str(stored or "").strip()
    if len(text) == 64 and all(char in "0123456789abcdefABCDEF" for char in text):
        raw = bytes.fromhex(text)
    elif text.lower().startswith("nsec1"):
        hrp, raw = _bech32_decode(text)
        if hrp != "nsec" or len(raw) != 32:
            raise SecretError("The stored secret is not a usable key.")
    else:
        raise SecretError("The stored secret is not a usable key.")
    if int.from_bytes(raw, "big") == 0:
        raise SecretError("The stored secret is not a usable key.")
    return raw.hex()


def load_secret_hex() -> str:
    """Read PARANOY_BOT_SECRET_HEX from Hermes. Callers must not log the result."""
    from . import schemas

    try:
        from agent.secret_scope import get_secret
    except Exception as exc:
        raise SecretError("Hermes secret storage is not available.") from exc
    try:
        raw = get_secret(schemas.SECRET_ENV)
    except Exception as exc:
        raise SecretError("Hermes secret storage is not available.") from exc
    if raw is None or not str(raw).strip():
        raise SecretError("No agent key is stored in Hermes yet.")
    return secret_to_hex(str(raw))


def secret_configured() -> bool:
    try:
        load_secret_hex()
    except SecretError:
        return False
    return True


class Signer:
    """Holds a key only to sign. ``repr`` does not include it."""

    def __init__(self, secret_hex: str, *, clock=None, aux_rand=None):
        self._clock = clock or time.time
        self._aux_rand = aux_rand or (lambda: secrets.token_bytes(32))
        self._secret = bytes.fromhex(secret_hex)
        self.pubkey = schnorr.pubkey_xonly(self._secret).hex()
        self.npub = encode_npub(bytes.fromhex(self.pubkey))

    def __repr__(self) -> str:
        return f"Signer(npub={self.npub!r})"

    def sign_event(self, kind: int, content: dict, tags: list | None = None, created_at: int | None = None) -> dict:
        tag_list = list(tags or [])
        content_text = json.dumps(content, separators=(",", ":"), ensure_ascii=False)
        stamp = int(self._clock()) if created_at is None else int(created_at)
        payload = json.dumps(
            [0, self.pubkey, stamp, int(kind), tag_list, content_text],
            separators=(",", ":"),
            ensure_ascii=False,
        )
        event_id = hashlib.sha256(payload.encode()).hexdigest()
        signature = schnorr.sign(self._secret, bytes.fromhex(event_id), self._aux_rand()).hex()
        return {
            "id": event_id,
            "pubkey": self.pubkey,
            "created_at": stamp,
            "kind": int(kind),
            "tags": tag_list,
            "content": content_text,
            "sig": signature,
        }

    def sign_request(self, tool: str, args: dict, *, nonce: str | None = None, created_at: int | None = None) -> dict:
        body = {
            "tool": tool,
            "args": args,
            "nonce": nonce if nonce is not None else secrets.token_hex(8),
        }
        return self.sign_event(4707, body, created_at=created_at)


def generate_and_store(save) -> str:
    """Create a key, hand the hex to Hermes' saver, and return only the npub."""
    from . import schemas

    secret_hex = secrets.token_bytes(32).hex()
    npub = Signer(secret_hex).npub
    try:
        save(schemas.SECRET_ENV, secret_hex)
    except Exception:
        return (
            "A key was generated but Hermes could not store it. "
            "Nothing was saved and no secret was printed. Run keygen again inside Hermes."
        )
    return f"Stored a new agent key with Hermes as {schemas.SECRET_ENV}. npub: {npub}"


def verify_event(event: dict) -> bool:
    """True when the public signature matches the id. Used by tests."""
    content = event["content"]
    payload = json.dumps(
        [0, event["pubkey"], event["created_at"], event["kind"], event["tags"], content],
        separators=(",", ":"),
        ensure_ascii=False,
    )
    if hashlib.sha256(payload.encode()).hexdigest() != event["id"]:
        return False
    return schnorr.verify(bytes.fromhex(event["pubkey"]), bytes.fromhex(event["id"]), bytes.fromhex(event["sig"]))
