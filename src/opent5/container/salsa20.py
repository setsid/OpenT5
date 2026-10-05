"""Salsa20: a pure-Python reference and a numpy path that is the one in use.

The stdlib has no Salsa20. The reference below is plain and slow (about a
megabyte a second); `crypt` computes every 64-byte block of a buffer at once
with numpy, which is what makes a 90 MB zone open in seconds rather than an
hour. Both are checked against the same published vector and against each
other in tests/test_salsa20.py.

The reference design: a 16-word state of four constants, the key, an 8-byte
nonce and a 64-bit block counter, put through twenty rounds and added to
itself to make 64 bytes of keystream. Every word is little-endian, including
on the big-endian console: the cipher is defined that way and the fastfiles
confirm it. The published vector is ECRYPT Set 6 for a 256-bit key.
"""

from __future__ import annotations

import struct

import numpy as np

BLOCK_SIZE = 64
KEY_SIZE = 32
SHORT_KEY_SIZE = 16
NONCE_SIZE = 8
DEFAULT_ROUNDS = 20

SIGMA = b"expand 32-byte k"
TAU = b"expand 16-byte k"

_MASK = 0xFFFFFFFF
_STATE = struct.Struct("<16I")
_FOUR = struct.Struct("<4I")
_TWO = struct.Struct("<2I")


class Salsa20Error(ValueError):
    pass


def _rotate(value: int, by: int) -> int:
    return ((value << by) | (value >> (32 - by))) & _MASK


def _quarter(x: list[int], a: int, b: int, c: int, d: int) -> None:
    x[b] ^= _rotate((x[a] + x[d]) & _MASK, 7)
    x[c] ^= _rotate((x[b] + x[a]) & _MASK, 9)
    x[d] ^= _rotate((x[c] + x[b]) & _MASK, 13)
    x[a] ^= _rotate((x[d] + x[c]) & _MASK, 18)


def core(state: list[int], rounds: int = DEFAULT_ROUNDS) -> bytes:
    """One 64-byte keystream block from a 16-word state."""
    x = list(state)
    for _ in range(rounds // 2):
        # Columns, then rows.
        _quarter(x, 0, 4, 8, 12)
        _quarter(x, 5, 9, 13, 1)
        _quarter(x, 10, 14, 2, 6)
        _quarter(x, 15, 3, 7, 11)
        _quarter(x, 0, 1, 2, 3)
        _quarter(x, 5, 6, 7, 4)
        _quarter(x, 10, 11, 8, 9)
        _quarter(x, 15, 12, 13, 14)
    return _STATE.pack(*[(a + b) & _MASK for a, b in zip(x, state, strict=True)])


def _state_for(key: bytes, nonce: bytes, index: int) -> list[int]:
    constants = SIGMA if len(key) == KEY_SIZE else TAU
    first = key[:SHORT_KEY_SIZE]
    second = key[SHORT_KEY_SIZE:] if len(key) == KEY_SIZE else key
    c = _FOUR.unpack(constants)
    return [
        c[0], *_FOUR.unpack(first), c[1],
        *_TWO.unpack(nonce), index & _MASK, (index >> 32) & _MASK,
        c[2], *_FOUR.unpack(second), c[3],
    ]  # fmt: skip


def keystream(
    key: bytes, nonce: bytes, length: int, rounds: int = DEFAULT_ROUNDS, counter: int = 0
) -> bytes:
    if len(key) not in (KEY_SIZE, SHORT_KEY_SIZE):
        raise Salsa20Error(f"key must be {SHORT_KEY_SIZE} or {KEY_SIZE} bytes, got {len(key)}")
    if len(nonce) != NONCE_SIZE:
        raise Salsa20Error(f"nonce must be {NONCE_SIZE} bytes, got {len(nonce)}")
    if length < 0:
        raise Salsa20Error(f"length must not be negative, got {length}")
    out = bytearray()
    index = counter
    while len(out) < length:
        out += core(_state_for(key, nonce, index), rounds)
        index += 1
    return bytes(out[:length])


def crypt_reference(
    data: bytes, key: bytes, nonce: bytes, rounds: int = DEFAULT_ROUNDS, counter: int = 0
) -> bytes:
    """Encrypt or decrypt with the pure-Python core. Kept as the oracle."""
    stream = keystream(key, nonce, len(data), rounds, counter)
    return bytes(a ^ b for a, b in zip(data, stream, strict=True))


def _rotl(v: np.ndarray, by: int) -> np.ndarray:
    return (v << np.uint32(by)) | (v >> np.uint32(32 - by))


# The four quarter-rounds of a column round (then a row round) act on disjoint words, so they
# are run as one array op over a four-wide axis rather than four separate numpy calls each. The
# index arrays gather the (a, b, c, d) word of all four quarters at once. Same arithmetic, same
# bytes out as the per-quarter reference; it just cuts the numpy dispatch count about fourfold.
_COL = (np.array([0, 5, 10, 15]), np.array([4, 9, 14, 3]),
        np.array([8, 13, 2, 7]), np.array([12, 1, 6, 11]))  # fmt: skip
_ROW = (np.array([0, 5, 10, 15]), np.array([1, 6, 11, 12]),
        np.array([2, 7, 8, 13]), np.array([3, 4, 9, 14]))  # fmt: skip


def keystream_fast(
    key: bytes, nonce: bytes, length: int, rounds: int = DEFAULT_ROUNDS, counter: int = 0
) -> bytes:
    """The same keystream as `keystream`, every block computed in parallel."""
    if len(key) not in (KEY_SIZE, SHORT_KEY_SIZE):
        raise Salsa20Error(f"key must be {SHORT_KEY_SIZE} or {KEY_SIZE} bytes, got {len(key)}")
    if len(nonce) != NONCE_SIZE:
        raise Salsa20Error(f"nonce must be {NONCE_SIZE} bytes, got {len(nonce)}")
    if length < 0:
        raise Salsa20Error(f"length must not be negative, got {length}")
    if length == 0:
        return b""
    blocks = (length + BLOCK_SIZE - 1) // BLOCK_SIZE
    base = _state_for(key, nonce, 0)
    index = np.arange(counter, counter + blocks, dtype=np.uint64)
    state = np.empty((16, blocks), np.uint32)
    for i, word in enumerate(base):
        state[i] = word
    state[8] = (index & np.uint64(_MASK)).astype(np.uint32)
    state[9] = (index >> np.uint64(32)).astype(np.uint32)
    x = state.copy()

    def grouped(ai, bi, ci, di) -> None:
        a, b, c, d = x[ai], x[bi], x[ci], x[di]
        b = b ^ _rotl(a + d, 7)
        c = c ^ _rotl(b + a, 9)
        d = d ^ _rotl(c + b, 13)
        a = a ^ _rotl(d + c, 18)
        x[ai], x[bi], x[ci], x[di] = a, b, c, d

    with np.errstate(over="ignore"):
        for _ in range(rounds // 2):
            grouped(*_COL)
            grouped(*_ROW)
        out = (x + state).T
    return np.ascontiguousarray(out).astype("<u4").tobytes()[:length]


def crypt(
    data: bytes, key: bytes, nonce: bytes, rounds: int = DEFAULT_ROUNDS, counter: int = 0
) -> bytes:
    """Encrypt or decrypt: the cipher is its own inverse."""
    stream = keystream_fast(key, nonce, len(data), rounds, counter)
    a = np.frombuffer(data, dtype=np.uint8)
    b = np.frombuffer(stream, dtype=np.uint8)
    return (a ^ b).tobytes()
