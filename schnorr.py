"""Pure-Python BIP-340 Schnorr signatures over secp256k1.

No native build, so the plugin installs on any Python Hermes uses. Signing
volume here is a few events per tool call, so speed is not a concern. This
code is not constant-time; the key never leaves the local process.
"""

from __future__ import annotations

import hashlib

P = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEFFFFFC2F
N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
G = (
    0x79BE667EF9DCBBAC55A06295CE870B07029BFCDB2DCE28D959F2815B16F81798,
    0x483ADA7726A3C4655DA4FBFC0E1108A8FD17B448A68554199C47D08FFB10D4B8,
)

Point = tuple[int, int] | None


def _tagged_hash(tag: str, data: bytes) -> bytes:
    tag_hash = hashlib.sha256(tag.encode()).digest()
    return hashlib.sha256(tag_hash + tag_hash + data).digest()


def _add(p1: Point, p2: Point) -> Point:
    if p1 is None:
        return p2
    if p2 is None:
        return p1
    if p1[0] == p2[0] and p1[1] != p2[1]:
        return None
    if p1 == p2:
        lam = (3 * p1[0] * p1[0] * pow(2 * p1[1], P - 2, P)) % P
    else:
        lam = ((p2[1] - p1[1]) * pow(p2[0] - p1[0], P - 2, P)) % P
    x3 = (lam * lam - p1[0] - p2[0]) % P
    return (x3, (lam * (p1[0] - x3) - p1[1]) % P)


def _mul(point: Point, scalar: int) -> Point:
    result: Point = None
    for bit in range(256):
        if (scalar >> bit) & 1:
            result = _add(result, point)
        point = _add(point, point)
    return result


def _lift_x(x: int) -> Point:
    if x >= P:
        return None
    y_sq = (pow(x, 3, P) + 7) % P
    y = pow(y_sq, (P + 1) // 4, P)
    if pow(y, 2, P) != y_sq:
        return None
    return (x, y if y % 2 == 0 else P - y)


def _int(data: bytes) -> int:
    return int.from_bytes(data, "big")


def _bytes(value: int) -> bytes:
    return value.to_bytes(32, "big")


def pubkey_xonly(secret: bytes) -> bytes:
    d = _int(secret)
    if not 1 <= d < N:
        raise ValueError("secret out of range")
    point = _mul(G, d)
    assert point is not None
    return _bytes(point[0])


def sign(secret: bytes, msg: bytes, aux_rand: bytes) -> bytes:
    if len(msg) != 32 or len(aux_rand) != 32:
        raise ValueError("msg and aux_rand must be 32 bytes")
    d0 = _int(secret)
    if not 1 <= d0 < N:
        raise ValueError("secret out of range")
    point = _mul(G, d0)
    assert point is not None
    d = d0 if point[1] % 2 == 0 else N - d0
    t = _bytes(d ^ _int(_tagged_hash("BIP0340/aux", aux_rand)))
    k0 = _int(_tagged_hash("BIP0340/nonce", t + _bytes(point[0]) + msg)) % N
    if k0 == 0:
        raise ValueError("nonce is zero")
    r_point = _mul(G, k0)
    assert r_point is not None
    k = k0 if r_point[1] % 2 == 0 else N - k0
    e = _int(_tagged_hash("BIP0340/challenge", _bytes(r_point[0]) + _bytes(point[0]) + msg)) % N
    sig = _bytes(r_point[0]) + _bytes((k + e * d) % N)
    if not verify(_bytes(point[0]), msg, sig):
        raise ValueError("signature self-check failed")
    return sig


def verify(pubkey: bytes, msg: bytes, sig: bytes) -> bool:
    if len(pubkey) != 32 or len(msg) != 32 or len(sig) != 64:
        return False
    point = _lift_x(_int(pubkey))
    r = _int(sig[:32])
    s = _int(sig[32:])
    if point is None or r >= P or s >= N:
        return False
    e = _int(_tagged_hash("BIP0340/challenge", sig[:32] + pubkey + msg)) % N
    r_point = _add(_mul(G, s), _mul(point, N - e))
    return r_point is not None and r_point[1] % 2 == 0 and r_point[0] == r
