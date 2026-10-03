"""Ed25519 (RFC 8032, section 5.1) in pure Python: verification, and signing for the
release tools.

This follows the reference code in RFC 8032 section 6, with three extra checks on
verification that libsodium also makes: the public key and R must be canonical point
encodings, S must be below the group order L, and the public key must not be of small
order. Verification only handles public data, so it does not need to run in constant
time. Signing does handle the private key and is NOT constant time; it runs only on the
release machine, with the release author's own key (see docs/updates.md).

Checked against every Ed25519 test vector in RFC 8032 section 7.1
(tests/test_update_crypto.py).
"""

from __future__ import annotations

import hashlib

P = 2**255 - 19
L = 2**252 + 27742317777372353535851937790883648493
D = -121665 * pow(121666, P - 2, P) % P
D2 = 2 * D % P
SQRT_M1 = pow(2, (P - 1) // 4, P)

_Point = tuple[int, int, int, int]  # extended coordinates (X, Y, Z, T), x = X/Z, y = Y/Z
IDENTITY: _Point = (0, 1, 1, 0)


def _sha512(data: bytes) -> bytes:
    return hashlib.sha512(data).digest()


def _sha512_mod_l(data: bytes) -> int:
    return int.from_bytes(_sha512(data), "little") % L


def _add(a: _Point, b: _Point) -> _Point:
    x1, y1, z1, t1 = a
    x2, y2, z2, t2 = b
    aa = (y1 - x1) * (y2 - x2) % P
    bb = (y1 + x1) * (y2 + x2) % P
    cc = t1 * D2 * t2 % P
    dd = z1 * 2 * z2 % P
    e, f, g, h = bb - aa, dd - cc, dd + cc, bb + aa
    return (e * f % P, g * h % P, f * g % P, e * h % P)


def _mul(s: int, point: _Point) -> _Point:
    q = IDENTITY
    while s > 0:
        if s & 1:
            q = _add(q, point)
        point = _add(point, point)
        s >>= 1
    return q


def _equal(a: _Point, b: _Point) -> bool:
    return (a[0] * b[2] - b[0] * a[2]) % P == 0 and (a[1] * b[2] - b[1] * a[2]) % P == 0


def _recover_x(y: int, sign: int) -> int | None:
    if y >= P:
        return None  # non-canonical encoding
    x2 = (y * y - 1) * pow(D * y * y + 1, P - 2, P) % P
    if x2 == 0:
        return None if sign else 0
    x = pow(x2, (P + 3) // 8, P)
    if (x * x - x2) % P != 0:
        x = x * SQRT_M1 % P
    if (x * x - x2) % P != 0:
        return None
    if (x & 1) != sign:
        x = P - x
    return x


_GY = 4 * pow(5, P - 2, P) % P
_GX = _recover_x(_GY, 0)
assert _GX is not None
BASE: _Point = (_GX, _GY, 1, _GX * _GY % P)


def _compress(point: _Point) -> bytes:
    zinv = pow(point[2], P - 2, P)
    x = point[0] * zinv % P
    y = point[1] * zinv % P
    return (y | ((x & 1) << 255)).to_bytes(32, "little")


def _decompress(data: bytes) -> _Point | None:
    if len(data) != 32:
        return None
    y = int.from_bytes(data, "little")
    sign = y >> 255
    y &= (1 << 255) - 1
    x = _recover_x(y, sign)
    if x is None:
        return None
    return (x, y, 1, x * y % P)


def _expand(seed: bytes) -> tuple[int, bytes]:
    if len(seed) != 32:
        raise ValueError(f"an Ed25519 seed is 32 bytes, found {len(seed)}")
    h = _sha512(seed)
    a = int.from_bytes(h[:32], "little")
    a &= (1 << 254) - 8
    a |= 1 << 254
    return a, h[32:]


def public_key(seed: bytes) -> bytes:
    """The 32-byte public key for a 32-byte secret seed."""
    a, _ = _expand(seed)
    return _compress(_mul(a, BASE))


def sign(seed: bytes, message: bytes) -> bytes:
    """A 64-byte signature of MESSAGE. Not constant time: release machine only."""
    a, prefix = _expand(seed)
    pub = _compress(_mul(a, BASE))
    r = _sha512_mod_l(prefix + message)
    rs = _compress(_mul(r, BASE))
    h = _sha512_mod_l(rs + pub + message)
    s = (r + h * a) % L
    return rs + s.to_bytes(32, "little")


def verify(public: bytes, message: bytes, signature: bytes) -> bool:
    """True only when SIGNATURE is a valid Ed25519 signature of MESSAGE by PUBLIC."""
    if len(public) != 32 or len(signature) != 64:
        return False
    a = _decompress(public)
    if a is None or _equal(_mul(8, a), IDENTITY):
        return False  # not a point, or a small-order key that "verifies" anything
    rs = signature[:32]
    r = _decompress(rs)
    if r is None:
        return False
    s = int.from_bytes(signature[32:], "little")
    if s >= L:
        return False  # non-canonical S (signature malleability)
    h = _sha512_mod_l(rs + public + message)
    return _equal(_mul(s, BASE), _add(r, _mul(h, a)))
