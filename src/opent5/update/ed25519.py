"""Ed25519 through libsodium (PyNaCl): verification, and signing for the release tools.

Rejection is the verifier's whole job, so it is left to libsodium's
crypto_sign_verify_detached, which refuses non-canonical S (S >= L), non-canonical or
small-order public keys and small-order R, as well as plain mismatches
(tests/test_update_crypto.py checks each case against this module).
"""

from __future__ import annotations

from nacl.exceptions import BadSignatureError
from nacl.signing import SigningKey, VerifyKey

#: The order of the Ed25519 base point.
L = 2**252 + 27742317777372353535851937790883648493
KEY_SIZE = 32
SIGNATURE_SIZE = 64


def public_key(seed: bytes) -> bytes:
    return bytes(SigningKey(bytes(seed)).verify_key)


def sign(seed: bytes, message: bytes) -> bytes:
    return SigningKey(bytes(seed)).sign(bytes(message)).signature


def verify(public: bytes, message: bytes, signature: bytes) -> bool:
    """True only when libsodium accepts the signature; any malformed input is False."""
    if len(public) != KEY_SIZE or len(signature) != SIGNATURE_SIZE:
        return False
    try:
        VerifyKey(bytes(public)).verify(bytes(message), bytes(signature))
    except (BadSignatureError, ValueError, TypeError):
        return False
    return True
