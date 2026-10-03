"""Ed25519 against RFC 8032, and the minisign key and signature formats."""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from opent5.update import ed25519, minisign
from opent5.update.minisign import MinisignError, PublicKey, SecretKey

VECTORS = json.loads((Path(__file__).parent / "fixtures" / "rfc8032_ed25519.json").read_text())

#: Small scrypt limits so the tests stay fast (the format stores them in the key file).
FAST = {"opslimit": 32768, "memlimit": 16777216}


@pytest.mark.parametrize("v", VECTORS["vectors"], ids=lambda v: v["name"])
def test_rfc8032_vectors(v):
    seed, msg = bytes.fromhex(v["secret"]), bytes.fromhex(v["message"])
    pub, sig = bytes.fromhex(v["public"]), bytes.fromhex(v["signature"])
    assert ed25519.public_key(seed) == pub
    assert ed25519.sign(seed, msg) == sig
    assert ed25519.verify(pub, msg, sig)
    assert not ed25519.verify(pub, msg + b"x", sig)
    for i in (0, 31, 32, 63):
        bad = bytearray(sig)
        bad[i] ^= 0x01
        assert not ed25519.verify(pub, msg, bytes(bad))


def test_rfc8032_vector_count():
    assert [v["name"] for v in VECTORS["vectors"]] == [
        "TEST 1", "TEST 2", "TEST 3", "TEST 1024", "TEST SHA(abc)"
    ]  # fmt: skip


def test_rejects_non_canonical_s_and_small_order_keys():
    v = VECTORS["vectors"][1]
    pub, msg, sig = (bytes.fromhex(v[k]) for k in ("public", "message", "signature"))
    s = int.from_bytes(sig[32:], "little") + ed25519.L  # same value mod L, not canonical
    assert not ed25519.verify(pub, msg, sig[:32] + s.to_bytes(32, "little"))
    identity = (1).to_bytes(32, "little")  # the neutral point, order 1
    assert not ed25519.verify(identity, msg, identity + bytes(32))
    assert not ed25519.verify(pub[:31], msg, sig)
    assert not ed25519.verify(pub, msg, sig[:63])


#: Every small-order point of edwards25519 in its 32-byte encoding (orders 1, 2, 4 and 8),
#: including the encodings with the sign bit set, as listed in libsodium's blocklist.
SMALL_ORDER = [
    "0100000000000000000000000000000000000000000000000000000000000000",
    "ecffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff7f",
    "0000000000000000000000000000000000000000000000000000000000000000",
    "0000000000000000000000000000000000000000000000000000000000000080",
    "c7176a703d4dd84fba3c0b760d10670f2a2053fa2c39ccc64ec7fd7792ac037a",
    "c7176a703d4dd84fba3c0b760d10670f2a2053fa2c39ccc64ec7fd7792ac03fa",
    "26e8958fc2b227b045c3f489f2ef98f0d5dfac05d3c63339b13802886d53fc05",
    "26e8958fc2b227b045c3f489f2ef98f0d5dfac05d3c63339b13802886d53fc85",
]


def _vector(i=1):
    v = VECTORS["vectors"][i]
    return tuple(bytes.fromhex(v[k]) for k in ("secret", "public", "message", "signature"))


@pytest.mark.parametrize("encoding", SMALL_ORDER)
def test_small_order_public_keys_are_refused(encoding):
    """With a small-order key, R = a small-order point and S = 0 satisfies the plain
    verification equation for many messages; only a verifier that rejects such keys is
    safe against it."""
    key = bytes.fromhex(encoding)
    for message in (b"", b"manifest", b"x" * 100):
        for r in SMALL_ORDER:
            assert not ed25519.verify(key, message, bytes.fromhex(r) + bytes(32))


@pytest.mark.parametrize("encoding", SMALL_ORDER)
def test_small_order_r_is_refused_under_a_real_key(encoding):
    _, pub, msg, sig = _vector()
    assert not ed25519.verify(pub, msg, bytes.fromhex(encoding) + sig[32:])
    assert not ed25519.verify(pub, msg, bytes.fromhex(encoding) + bytes(32))


@pytest.mark.parametrize("extra", [1, 2, 15])
def test_non_canonical_s_is_refused(extra):
    """S + k*L verifies under the plain equation (malleability); it must not here."""
    _, pub, msg, sig = _vector()
    s = int.from_bytes(sig[32:], "little")
    assert ed25519.verify(pub, msg, sig)
    forged = s + extra * ed25519.L
    if forged < 2**256:
        assert not ed25519.verify(pub, msg, sig[:32] + forged.to_bytes(32, "little"))


def test_s_at_or_above_the_order_is_refused():
    _, pub, msg, sig = _vector()
    for s in (ed25519.L, ed25519.L + 1, 2**253, 2**256 - 1):
        assert not ed25519.verify(pub, msg, sig[:32] + s.to_bytes(32, "little"))


def test_non_canonical_public_key_encodings_are_refused():
    """y >= p encodings of a point: the same point, a different byte string."""
    _, pub, msg, sig = _vector()
    p = 2**255 - 19
    for y in (p, p + 1, 2**255 - 1):
        for sign in (0, 1):
            key = (y | (sign << 255)).to_bytes(32, "little")
            assert not ed25519.verify(key, msg, sig)


def test_verification_is_libsodium():
    import nacl.bindings

    _, pub, msg, sig = _vector()
    assert ed25519.verify(pub, msg, sig)
    nacl.bindings.crypto_sign_open(sig + msg, pub)  # libsodium accepts the same input


def test_scrypt_parameters_match_libsodium():
    # (opslimit, memlimit) -> (log2 N, r, p), as libsodium 1.0.18 computes them (checked
    # against PyNaCl 1.5.0's crypto_pwhash_scryptsalsa208sha256 output, see docs/updates.md).
    assert minisign._pick_params(33554432, 1073741824) == (20, 8, 1)  # minisign default
    assert minisign._pick_params(32768, 16777216) == (10, 8, 1)
    assert minisign._pick_params(524288, 16777216) == (14, 8, 1)
    assert minisign._pick_params(1 << 20, 1 << 24) == (14, 8, 2)


def test_secret_key_round_trip_and_wrong_passphrase():
    key = SecretKey.generate()
    text = key.encrypt(b"correct horse", **FAST)
    assert text.startswith("untrusted comment: ")
    again = SecretKey.decrypt(text, b"correct horse")
    assert again == key
    with pytest.raises(MinisignError, match="wrong passphrase"):
        SecretKey.decrypt(text, b"wrong")
    with pytest.raises(MinisignError, match="passphrase is required"):
        key.encrypt(b"", **FAST)


def test_public_key_line_round_trip():
    key = SecretKey.generate()
    line = key.public.line()
    assert PublicKey.parse(line) == key.public
    assert PublicKey.parse(key.public.file_text()) == key.public
    with pytest.raises(MinisignError):
        PublicKey.parse(base64.b64encode(b"Xx" + bytes(40)).decode())


def test_signature_valid_and_trusted_comment_returned():
    key = SecretKey.generate()
    sig = key.sign(b"manifest", "OpenT5 manifest version:1.2.3")
    assert minisign.verify(key.public, b"manifest", sig) == "OpenT5 manifest version:1.2.3"
    assert minisign.verify(key.public, b"manifest", sig.encode()) == (
        "OpenT5 manifest version:1.2.3"
    )


def test_signature_invalid_cases():
    key, other = SecretKey.generate(), SecretKey.generate()
    sig = key.sign(b"manifest", "version:1.2.3")
    with pytest.raises(MinisignError, match="does not match"):
        minisign.verify(key.public, b"manifesT", sig)
    with pytest.raises(MinisignError, match="made by key"):
        minisign.verify(other.public, b"manifest", sig)
    # Same key id, different key: the signature itself fails.
    impostor = PublicKey(key.key_id, other.public.key)
    with pytest.raises(MinisignError, match="does not match"):
        minisign.verify(impostor, b"manifest", sig)
    with pytest.raises(MinisignError, match="trusted comment has been altered"):
        minisign.verify(key.public, b"manifest", sig.replace("1.2.3", "9.9.9"))
    with pytest.raises(MinisignError, match="4 lines"):
        minisign.verify(key.public, b"manifest", "garbage")
    with pytest.raises(MinisignError):
        minisign.verify(key.public, b"manifest", b"\xff" * 10)


def test_legacy_unhashed_signature_accepted():
    key = SecretKey.generate()
    sig = ed25519.sign(key.seed, b"data")
    glob = ed25519.sign(key.seed, sig + b"c")
    text = (
        "untrusted comment: x\n"
        + base64.b64encode(b"Ed" + key.key_id + sig).decode()
        + "\ntrusted comment: c\n"
        + base64.b64encode(glob).decode()
        + "\n"
    )
    assert minisign.verify(key.public, b"data", text) == "c"
