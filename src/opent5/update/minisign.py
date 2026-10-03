"""The minisign file formats (public key, encrypted secret key, signature), stdlib only.

Format reference: the minisign project's documentation (its README, "Signature
format" and "Key format"). Summary:

  public key   base64( "Ed" | key id (8) | Ed25519 public key (32) )
  secret key   base64( "Ed" | "Sc" | "B2" | salt (32) | opslimit (8, LE) | memlimit (8, LE)
                       | ( key id (8) | seed+pk (64) | checksum (32) ) XOR scrypt(passphrase) )
               checksum = BLAKE2b-256( "Ed" | key id | seed+pk )
  signature    untrusted comment: ...
               base64( "ED" | key id (8) | Ed25519 sig over BLAKE2b-512(file) )
               trusted comment: TEXT
               base64( Ed25519 sig over ( first signature | TEXT ) )

"Ed" (legacy) signatures sign the file itself rather than its BLAKE2b-512 hash; both are
accepted. The trusted comment is covered by the second ("global") signature, so it is
safe to put the release version in it.

scrypt parameters are stored as libsodium's opslimit/memlimit and turned into (N, r, p)
the way libsodium's crypto_pwhash_scryptsalsa208sha256 does (_pick_params).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import struct
from dataclasses import dataclass

from opent5.update import ed25519

UNTRUSTED = "untrusted comment: "
TRUSTED = "trusted comment: "
#: minisign's defaults for a new key ("sensitive" limits: about 1 GiB of memory).
OPSLIMIT = 33554432
MEMLIMIT = 1073741824
#: Longest signature file accepted (they are about 300 bytes).
MAX_SIG_BYTES = 4096


class MinisignError(ValueError):
    """A key or signature that is malformed, or does not verify."""


@dataclass(frozen=True)
class PublicKey:
    key_id: bytes
    key: bytes

    @classmethod
    def parse(cls, text: str) -> PublicKey:
        """A public key from its base64 line, or from a .pub file (comment + line)."""
        lines = [x.strip() for x in text.strip().splitlines() if x.strip()]
        lines = [x for x in lines if not x.startswith(UNTRUSTED)]
        if len(lines) != 1:
            raise MinisignError(f"expected one public key line, found {len(lines)}")
        raw = _b64(lines[0], "public key")
        if len(raw) != 42 or raw[:2] != b"Ed":
            raise MinisignError(
                f"public key: expected 42 bytes starting 'Ed', found {len(raw)} bytes "
                f"starting {raw[:2]!r}"
            )
        return cls(raw[2:10], raw[10:42])

    def line(self) -> str:
        return base64.b64encode(b"Ed" + self.key_id + self.key).decode()

    def file_text(self) -> str:
        return f"{UNTRUSTED}minisign public key {self.key_id[::-1].hex().upper()}\n{self.line()}\n"


@dataclass(frozen=True)
class SecretKey:
    key_id: bytes
    seed: bytes

    @classmethod
    def generate(cls) -> SecretKey:
        return cls(os.urandom(8), os.urandom(32))

    @property
    def public(self) -> PublicKey:
        return PublicKey(self.key_id, ed25519.public_key(self.seed))

    def encrypt(self, passphrase: bytes, opslimit: int = OPSLIMIT, memlimit: int = MEMLIMIT) -> str:
        """The encrypted secret key file text (minisign format, scrypt + XOR + BLAKE2b)."""
        if not passphrase:
            raise MinisignError("a passphrase is required")
        salt = os.urandom(32)
        sk = self.seed + self.public.key
        plain = self.key_id + sk + _checksum(self.key_id, sk)
        stream = _kdf(passphrase, salt, opslimit, memlimit, len(plain))
        blob = (
            b"EdScB2"
            + salt
            + struct.pack("<QQ", opslimit, memlimit)
            + bytes(x ^ y for x, y in zip(plain, stream, strict=True))
        )
        return f"{UNTRUSTED}minisign encrypted secret key\n{base64.b64encode(blob).decode()}\n"

    @classmethod
    def decrypt(cls, text: str, passphrase: bytes) -> SecretKey:
        lines = [x.strip() for x in text.strip().splitlines() if x.strip()]
        lines = [x for x in lines if not x.startswith(UNTRUSTED)]
        if len(lines) != 1:
            raise MinisignError("secret key: expected one base64 line")
        raw = _b64(lines[0], "secret key")
        if len(raw) != 158:
            raise MinisignError(f"secret key: expected 158 bytes, found {len(raw)}")
        if raw[:6] != b"EdScB2":
            raise MinisignError(f"secret key: expected header b'EdScB2', found {raw[:6]!r}")
        salt = raw[6:38]
        opslimit, memlimit = struct.unpack("<QQ", raw[38:54])
        stream = _kdf(passphrase, salt, opslimit, memlimit, 104)
        plain = bytes(x ^ y for x, y in zip(raw[54:], stream, strict=True))
        key_id, sk, check = plain[:8], plain[8:72], plain[72:104]
        if not hmac.compare_digest(check, _checksum(key_id, sk)):
            raise MinisignError("wrong passphrase, or the secret key file is damaged")
        key = cls(key_id, sk[:32])
        if key.public.key != sk[32:]:
            raise MinisignError("secret key: the stored public half does not match")
        return key

    def sign(self, message: bytes, trusted_comment: str, untrusted_comment: str = "") -> str:
        """A minisign signature file for MESSAGE (prehashed "ED" form)."""
        if "\n" in trusted_comment or "\r" in trusted_comment:
            raise MinisignError("the trusted comment must be one line")
        digest = hashlib.blake2b(message, digest_size=64).digest()
        sig = ed25519.sign(self.seed, digest)
        glob = ed25519.sign(self.seed, sig + trusted_comment.encode())
        return (
            f"{UNTRUSTED}{untrusted_comment or 'signature from minisign secret key'}\n"
            f"{base64.b64encode(b'ED' + self.key_id + sig).decode()}\n"
            f"{TRUSTED}{trusted_comment}\n"
            f"{base64.b64encode(glob).decode()}\n"
        )


def verify(public: PublicKey, message: bytes, signature_text: str | bytes) -> str:
    """Check a minisign signature of MESSAGE; return its trusted comment.

    Raises MinisignError, saying what was expected and what was found, on any problem.
    """
    if isinstance(signature_text, bytes):
        if len(signature_text) > MAX_SIG_BYTES:
            raise MinisignError(f"signature: at most {MAX_SIG_BYTES} bytes, found more")
        try:
            signature_text = signature_text.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise MinisignError("signature: not UTF-8 text") from exc
    lines = signature_text.replace("\r\n", "\n").rstrip("\n").split("\n")
    if len(lines) != 4:
        raise MinisignError(f"signature: expected 4 lines, found {len(lines)}")
    if not lines[0].startswith(UNTRUSTED):
        raise MinisignError("signature: line 1 must be the untrusted comment")
    if not lines[2].startswith(TRUSTED):
        raise MinisignError("signature: line 3 must be the trusted comment")
    raw = _b64(lines[1], "signature")
    if len(raw) != 74:
        raise MinisignError(f"signature: expected 74 bytes, found {len(raw)}")
    alg, key_id, sig = raw[:2], raw[2:10], raw[10:]
    if key_id != public.key_id:
        raise MinisignError(
            f"signature: made by key {key_id[::-1].hex().upper()}, expected "
            f"{public.key_id[::-1].hex().upper()}"
        )
    if alg == b"ED":
        signed = hashlib.blake2b(message, digest_size=64).digest()
    elif alg == b"Ed":
        signed = message
    else:
        raise MinisignError(f"signature: unknown algorithm {alg!r}")
    if not ed25519.verify(public.key, signed, sig):
        raise MinisignError("signature: does not match the signed file")
    comment = lines[2][len(TRUSTED) :]
    glob = _b64(lines[3], "global signature")
    if not ed25519.verify(public.key, sig + comment.encode(), glob):
        raise MinisignError("signature: the trusted comment has been altered")
    return comment


def _b64(text: str, what: str) -> bytes:
    try:
        return base64.b64decode(text.strip(), validate=True)
    except ValueError as exc:
        raise MinisignError(f"{what}: not valid base64") from exc


def _checksum(key_id: bytes, sk: bytes) -> bytes:
    return hashlib.blake2b(b"Ed" + key_id + sk, digest_size=32).digest()


def _pick_params(opslimit: int, memlimit: int) -> tuple[int, int, int]:
    """(log2 N, r, p) from libsodium opslimit/memlimit, as libsodium's pickparams()."""
    opslimit = max(opslimit, 32768)
    r = 8
    if opslimit < memlimit // 32:
        p = 1
        max_n = opslimit // (r * 4)
    else:
        max_n = memlimit // (r * 128)
    n_log2 = 1
    while n_log2 < 63 and (1 << n_log2) <= max_n // 2:
        n_log2 += 1
    if opslimit >= memlimit // 32:
        max_rp = min((opslimit // 4) // (1 << n_log2), 0x3FFFFFFF)
        p = max_rp // r
    return n_log2, r, p


def _kdf(passphrase: bytes, salt: bytes, opslimit: int, memlimit: int, size: int) -> bytes:
    n_log2, r, p = _pick_params(opslimit, memlimit)
    if n_log2 > 22 or p < 1 or p > 16:
        raise MinisignError(f"secret key: scrypt parameters out of range (N=2^{n_log2}, p={p})")
    need = 128 * r * (1 << n_log2) + 128 * r * p + 1024 * 1024
    return hashlib.scrypt(passphrase, salt=salt, n=1 << n_log2, r=r, p=p, maxmem=need, dklen=size)
