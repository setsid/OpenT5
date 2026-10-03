"""Unpack and repack a T5 PS3 fastfile: decrypt, decompress, and the reverse.

A signed PS3 zone is a 0x13c-byte header followed by a stream of XChunks:

    0x000  IWff0100                 zone magic
    0x008  u32 big-endian           zone version, 473 for T5
    0x00c  u32
    0x010  PHEEBs71                 auth header magic
    0x018  u32                      loading flags, always zero
    0x01c  char[32]                 the zone name, NUL-padded
    0x03c  byte[256]                RSA signature
    0x13c  the XChunks

    each chunk: u32 big-endian size, then that many bytes
    a size of zero ends a stream, one per stream; then zero padding (see
    TRAILER_SLACK), and no size field straddles a 0x60000 ring boundary

The 256 bytes at 0x3c are a real RSA-2048 signature, PSS-padded with MGF1
over SHA-256 and an 8-byte salt, and the console checks it. `pack` carries
that field over untouched because it cannot do anything else: signing needs
the private key. A repack whose bytes are identical to the file it came from
is therefore fine, and a repack of edited content is not. See
`console_signature_of` for what is measured rather than assumed.

Each chunk is Salsa20-encrypted and then raw-deflate compressed, so unpacking
decrypts first and inflates second. Chunks go round-robin over four streams,
each with its own Salsa20 nonce state and block index.

The nonce is not fixed. A table of 200 x streams x 20 bytes is filled from the
zone name -- four copies of each character in turn, cycling -- and a chunk's
nonce is the first eight bytes of its stream's current 20-byte block. After a
chunk is decrypted its plaintext is hashed with SHA-1, the block index
advances, and the hash is XORed into the block it lands on. So the nonces
chain: get one chunk wrong and everything after it on that stream is noise.

    opent5 unpack mp_nuked.ff out/
    opent5 pack out/ rebuilt.ff
"""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
import sys
import zlib
from dataclasses import dataclass
from pathlib import Path

from opent5.container.salsa20 import crypt

# The T5 PS3 zone key, from COD Engine Research's Black Ops page. Verified
# here: it decrypts ffotd_tu13_mp.ff and patch_mp.ff to streams that inflate.
SALSA20_KEY = bytes.fromhex("0c99b3ddb8d6d0845d1147e470f28a8bf2ae69a8a9f534767b54e9180ff55370")

#: The decompressed zone carries its own length, and it is not the same
#: number as any of the container's.
#:
#: The first big-endian u32 of the decompressed stream is the length of
#: everything after a fixed 36-byte prefix, so `content_length - 36`. That is
#: measured, not assumed: it holds exactly on all fourteen zones on this
#: machine, from a 31,453-byte ffotd to a 13,611,499-byte patch_ui_mp, with
#: no exceptions and no other constant that fits.
#:
#: `pack` used to carry this through from the unpacked content, which is
#: right for a byte-identical repack and wrong for every other kind. Change a
#: member's length and the zone says one size while another arrives.
ZONE_SIZE_AT = 0x00
ZONE_SIZE_PREFIX = 36

ZONE_MAGIC = b"IWff0100"
AUTH_MAGIC = b"PHEEBs71"
ZONE_VERSION = 473

OFFSET_VERSION = 0x08
OFFSET_AUTH_MAGIC = 0x10
OFFSET_ZONE_NAME = 0x1C
ZONE_NAME_SIZE = 32
OFFSET_SIGNATURE = 0x3C
SIGNATURE_SIZE = 256
CHUNKS_OFFSET = 0x13C

#: The console's zone-signing public key, recovered from t5mp.elf rather than
#: found anywhere: the function at VMA 0x22f8d0 builds a 270-byte PKCS#1
#: RSAPublicKey DER on its stack one `stb` at a time, hands it to
#: `rsa_import`, and then calls `rsa_verify_hash_ex` at VMA 0x230248. The DER
#: parses exactly -- SEQUENCE of 266, INTEGER of 257 with the usual leading
#: zero, INTEGER 65537, nothing left over -- which is the check that it was
#: reassembled correctly.
CONSOLE_KEY_MODULUS = int(
    "c9513aa85db84b36a02cecf736c66e76def3d3a898cbc97d4cf373e820435048"
    "173f82cdde1cc80ae5493dff7ddfe6d7cc1e8c71e3d204a93d146719e5de7da3"
    "3e5bd758806ef301614d05f03e03a477abe29f1b73fec97a85f68ef74a4c6965"
    "0af356d5278006449bf851f8a5d05a3562e7970effa15b8d2ba9197ce0401b58"
    "5b57904bcc6f7e29ca117a9f925599824edd8024f0d2b4a2a56fd2a2ffeaab69"
    "4d7eec78f8d6565007c50e1c1c1d0262eed38c5f6ec787050032c5e127a18836"
    "88e662c683eddd2ba36a9c3676477fb181c0f8b5ab49518f5cb4d616b48115ef"
    "70c2d49d2abb155638b9ffe771d271aba38a9d591ab82be44994c064fd4ece7f",
    16,
)
CONSOLE_KEY_EXPONENT = 65537

#: The arguments the console passes to `rsa_verify_hash_ex`, read off the
#: call site at VMA 0x230248: signature 256 bytes, padding 3 which is
#: LTC_PKCS_1_PSS, the hash index returned by `find_hash("sha256")`, and a
#: salt length of 8, which is what libtomcrypt's own `rsa_verify_hash`
#: passes. `stat` is required to come back 1 at VMA 0x230260.
PSS_SALT_SIZE = 8
PSS_HASH_SIZE = 32
PSS_TRAILER = 0xBC

#: The message the signature covers is a 4000-byte buffer, and what fills it
#: is *not* established -- see the finding. Nothing here depends on knowing,
#: because nothing here can sign.
CONSOLE_SIGNED_MESSAGE_SIZE = 4000

# From the reference implementation of the same scheme.
BLOCK_HASHES_COUNT = 200
SHA1_HASH_SIZE = 20
SALSA20_IV_SIZE = 8
STREAM_COUNT = 4

# The largest a stored chunk may be. Empirical: the largest stored chunk in
# all 82 zones to hand is 49098 bytes (code_post_gfx.ff, frontend.ff,
# river.ff); see docs/research/zones.md. The field is a u32, so nothing in
# the format itself bounds it.
XCHUNK_SIZE = 0x10000

#: What the original writer puts in one chunk: every chunk after the 36-byte
#: prefix inflates to exactly 0xBFC0 (49088) bytes except the last, in every
#: one of the 82 zones measured (docs/research/zones.md). A fresh split
#: mimics it.
XCHUNK_WRITE_SIZE = 0xC000 - 0x40
#: Kept under its old name for callers that split content themselves.
XCHUNK_MAX_WRITE = XCHUNK_WRITE_SIZE

# A chunk's size field.
CHUNK_SIZE_FIELD = struct.Struct(">I")

#: The file ends with the four terminators, then zeros: the length is the
#: end of the last terminator plus TRAILER_SLACK, rounded up to
#: FILE_ALIGNMENT. Measured on all 82 zones: the padding runs from 48
#: (common_mp.ff) to 79 bytes (mp_nuked.ff), every file length is a multiple
#: of 0x20, and every padding byte is zero.
FILE_ALIGNMENT = 0x20
TRAILER_SLACK = 0x30

#: The console reads a zone through a ring buffer of 0x60000 bytes indexed by
#: file offset: t5mp.elf at VMA 0x231ea8-0x231ec4 reduces the read position
#: modulo 0x60000 (multiply by 0xaaaaaaab, shift 18, subtract q<<19 - q<<17)
#: and adds the ring's base, and reads at most 0x30000 at a time (VMA
#: 0x231f98). A chunk size field never straddles the end of the ring: when
#: fewer than four bytes are left before a multiple of 0x60000, the writer
#: leaves them unused and the field starts on the boundary. The one case in
#: 82 zones is creek_1.ff, where chunk 414 ends at 0xb9ffff, the single byte
#: 0x60 there is skipped, and chunk 415's size field 00009160 sits at
#: 0xba0000 = 31 * 0x60000. Fifty other size fields lie within three bytes of
#: a 0x1000 boundary and are not skipped; 0x60000 from offset 0 is the
#: ring the ELF shows and is consistent with all 51.
RING_BUFFER_SIZE = 0x60000

#: The deflate settings that reproduce every original chunk byte for byte:
#: raw deflate, level 9, memLevel 9, default strategy, 32K window. Python's
#: zlib defaults to memLevel 8, which gives a different block split on some
#: chunks -- the cause of patch.ff's old repack mismatch at 0x66a6.
RAW_DEFLATE_WBITS = -15
DEFAULT_LEVEL = 9
DEFLATE_MEM_LEVEL = 9
LEVELS = range(10)

MANIFEST_NAME = "fastfile.json"
HEADER_NAME = "header.bin"
CONTENT_NAME = "zone.bin"


class FastFileError(ValueError):
    pass


@dataclass(frozen=True)
class ConsoleSignature:
    """What a PSS encoding gives up without the signed message.

    `digest` is the H field: SHA-256 over the eight zero bytes, the signed
    message and the salt. `salt` is the salt that came back out. Neither
    identifies the message, so neither lets anything here forge one; they are
    what proves the field is a signature at all rather than padding.
    """

    digest: bytes
    salt: bytes


def _mgf1(seed: bytes, length: int) -> bytes:
    out = bytearray()
    counter = 0
    while len(out) < length:
        out += hashlib.sha256(seed + struct.pack(">I", counter)).digest()
        counter += 1
    return bytes(out[:length])


def console_signature_of(header: bytes) -> ConsoleSignature | None:
    """Decode the header's signature field under the console's public key.

    None when the field is not a well-formed PSS encoding, which is what a
    made-up or tampered signature gives. A successful decode is not proof the
    signature matches this file -- that needs the signed message, which is not
    known -- but it does say the field came from whoever holds the private
    key, because the padding it recovers cannot be hit by chance.
    """
    if len(header) < OFFSET_SIGNATURE + SIGNATURE_SIZE:
        return None
    signature = header[OFFSET_SIGNATURE : OFFSET_SIGNATURE + SIGNATURE_SIZE]
    modulus = CONSOLE_KEY_MODULUS
    encoded_bits = modulus.bit_length() - 1
    encoded_size = (encoded_bits + 7) // 8
    raw = pow(int.from_bytes(signature, "big"), CONSOLE_KEY_EXPONENT, modulus)
    encoded = raw.to_bytes(encoded_size, "big")
    if encoded[-1] != PSS_TRAILER:
        return None
    split = encoded_size - PSS_HASH_SIZE - 1
    masked, digest = encoded[:split], encoded[split : encoded_size - 1]
    block = bytes(a ^ b for a, b in zip(masked, _mgf1(digest, split), strict=True))
    # The leading bits the encoding does not use have to be clear.
    spare = 8 * encoded_size - encoded_bits
    block = bytes([block[0] & (0xFF >> spare)]) + block[1:]
    at = block.find(b"\x01")
    if at < 0 or any(block[:at]):
        return None
    salt = block[at + 1 :]
    if len(salt) != PSS_SALT_SIZE:
        return None
    return ConsoleSignature(digest=digest, salt=salt)


def carries_console_signature(header: bytes) -> bool:
    """Whether the header's signature field decodes under the console's key."""
    return console_signature_of(header) is not None


@dataclass(frozen=True)
class Chunk:
    """One chunk as it was read.

    `deflated` is the chunk after decryption and before inflation: the exact
    bytes the original encoder produced. Carrying it is what lets an untouched
    chunk go back out byte for byte without trusting a recompression.
    `skipped` is whatever sat in the ring-buffer gap before this chunk's size
    field, usually nothing.
    """

    stream: int
    nonce: bytes
    compressed: int
    body: bytes
    level: int | None = None
    deflated: bytes = b""
    skipped: bytes = b""
    offset: int = 0


@dataclass(frozen=True)
class FastFile:
    header: bytes
    chunks: tuple[Chunk, ...]
    # How long the file was. The tail is zeros whose length follows from the
    # chunks (see TRAILER_SLACK); it is carried so that a file that broke the
    # rule would still round-trip.
    total_bytes: int = 0
    #: How many zero size fields ended the stream (four in every retail zone).
    terminators: int = 0
    #: Where the last terminator ends.
    end: int = 0
    #: Bytes skipped at the ring boundary before a terminator, by offset.
    trailing_skips: tuple[tuple[int, bytes], ...] = ()

    @property
    def zone_name(self) -> str:
        raw = self.header[OFFSET_ZONE_NAME : OFFSET_ZONE_NAME + ZONE_NAME_SIZE]
        return raw.split(b"\x00", 1)[0].decode("ascii", errors="replace")

    @property
    def content(self) -> bytes:
        return b"".join(chunk.body for chunk in self.chunks)


def zone_name_of(header: bytes) -> bytes:
    return header[OFFSET_ZONE_NAME : OFFSET_ZONE_NAME + ZONE_NAME_SIZE].split(b"\x00", 1)[0]


def check_header(header: bytes) -> None:
    if len(header) < CHUNKS_OFFSET:
        raise FastFileError(f"a fastfile header is {CHUNKS_OFFSET} bytes, got {len(header)}")
    if not header.startswith(ZONE_MAGIC):
        raise FastFileError(f"not a fastfile: magic is {header[:8]!r}, wanted {ZONE_MAGIC!r}")
    at = OFFSET_AUTH_MAGIC
    if header[at : at + len(AUTH_MAGIC)] != AUTH_MAGIC:
        raise FastFileError(
            f"no auth header: {header[at : at + 8]!r} at {at:#x}, wanted {AUTH_MAGIC!r}"
        )
    (version,) = struct.unpack_from(">I", header, OFFSET_VERSION)
    if version != ZONE_VERSION:
        raise FastFileError(f"zone version {version} at {OFFSET_VERSION:#x}, wanted {ZONE_VERSION}")
    if not zone_name_of(header):
        raise FastFileError("the zone name field is empty, so there is no nonce to derive")


class NonceTable:
    """The nonce state, seeded from the zone name and chained by the SHA-1 of
    each chunk's plaintext.

    The table is BLOCK_HASHES_COUNT blocks of SHA1_HASH_SIZE **in total**,
    shared by all four streams, and a stream's block index wraps at
    BLOCK_HASHES_COUNT // STREAM_COUNT. That differs from the T6 reference,
    which sizes the table per stream and wraps at BLOCK_HASHES_COUNT, and it
    is empirical: read the T6 way, patch_ui_mp.ff decrypts 200 chunks and
    then every stream fails at once, which is each stream reaching its 51st
    chunk. Read this way all 279 chunks inflate.
    """

    def __init__(self, zone_name: bytes, streams: int = STREAM_COUNT) -> None:
        if not zone_name:
            raise FastFileError("the nonce table needs a zone name")
        self.streams = streams
        size = BLOCK_HASHES_COUNT * SHA1_HASH_SIZE
        # The name is cut to 31 characters: the field it came from holds 32
        # with a NUL.
        usable = min(len(zone_name), ZONE_NAME_SIZE - 1)
        self.blocks = bytearray(size)
        index = 0
        for offset in range(0, size, 4):
            self.blocks[offset : offset + 4] = bytes([zone_name[index]]) * 4
            index = (index + 1) % usable
        self.indices = [0] * streams
        self.wrap = BLOCK_HASHES_COUNT // streams

    def _at(self, stream: int) -> int:
        return self.indices[stream] * self.streams * SHA1_HASH_SIZE + stream * SHA1_HASH_SIZE

    def nonce(self, stream: int) -> bytes:
        start = self._at(stream)
        return bytes(self.blocks[start : start + SALSA20_IV_SIZE])

    def advance(self, stream: int, plaintext: bytes) -> None:
        """Hash the chunk, step the index, and fold the hash into the block
        the stream lands on."""
        digest = hashlib.sha1(plaintext).digest()
        self.indices[stream] = (self.indices[stream] + 1) % self.wrap
        start = self._at(stream)
        for offset in range(SHA1_HASH_SIZE):
            self.blocks[start + offset] ^= digest[offset]


def deflate(body: bytes, level: int = DEFAULT_LEVEL, mem_level: int = DEFLATE_MEM_LEVEL) -> bytes:
    """Raw deflate with the settings the original encoder used."""
    compressor = zlib.compressobj(level, zlib.DEFLATED, RAW_DEFLATE_WBITS, mem_level)
    return compressor.compress(body) + compressor.flush()


def level_that_rebuilds(body: bytes, stored: bytes) -> int | None:
    """The deflate level that reproduces `stored` exactly at memLevel 9, if
    one does.

    Level 9 is tried first and matches every chunk of every retail zone, so
    this normally compresses once. Nothing on the read path calls it any
    more: an untouched chunk keeps its original deflate stream instead.
    """
    for level in (DEFAULT_LEVEL, *(n for n in LEVELS if n != DEFAULT_LEVEL)):
        if deflate(body, level) == stored:
            return level
    return None


def ring_gap(offset: int) -> int:
    """How many bytes must be skipped at `offset` before a size field, so it
    does not straddle the end of the console's read ring."""
    left = RING_BUFFER_SIZE - offset % RING_BUFFER_SIZE
    return left if left < CHUNK_SIZE_FIELD.size else 0


def padded_length(end: int) -> int:
    """The file length the original writer gives a zone whose terminators end
    at `end`."""
    want = end + TRAILER_SLACK
    return (want + FILE_ALIGNMENT - 1) // FILE_ALIGNMENT * FILE_ALIGNMENT


def read_fastfile(data: bytes, key: bytes = SALSA20_KEY) -> FastFile:
    check_header(data)
    header = bytes(data[:CHUNKS_OFFSET])
    table = NonceTable(zone_name_of(header))
    chunks: list[Chunk] = []
    offset = CHUNKS_OFFSET
    stream = 0
    terminators = 0
    trailing: list[tuple[int, bytes]] = []
    while offset + CHUNK_SIZE_FIELD.size <= len(data):
        gap = ring_gap(offset)
        skipped = bytes(data[offset : offset + gap])
        offset += gap
        if offset + CHUNK_SIZE_FIELD.size > len(data):
            break
        (size,) = CHUNK_SIZE_FIELD.unpack_from(data, offset)
        if size == 0:
            if gap:
                trailing.append((offset - gap, skipped))
            offset += CHUNK_SIZE_FIELD.size
            terminators += 1
            if terminators == STREAM_COUNT:
                break
            continue
        if terminators:
            raise FastFileError(
                f"a chunk of {size} bytes at {offset:#x} follows a terminator; "
                f"wanted {STREAM_COUNT} zero size fields in a row"
            )
        field_at = offset
        offset += CHUNK_SIZE_FIELD.size
        if size > XCHUNK_SIZE:
            raise FastFileError(
                f"chunk {len(chunks)} at {field_at:#x} claims {size} bytes, "
                f"the most is {XCHUNK_SIZE}"
            )
        stored = data[offset : offset + size]
        if len(stored) != size:
            raise FastFileError(
                f"chunk {len(chunks)} at {field_at:#x} is short: wanted {size}, got {len(stored)}"
            )
        offset += size

        nonce = table.nonce(stream)
        plaintext = crypt(stored, key, nonce)
        table.advance(stream, plaintext)
        try:
            body = zlib.decompress(plaintext, RAW_DEFLATE_WBITS)
        except zlib.error as exc:
            raise FastFileError(
                f"chunk {len(chunks)} on stream {stream} at {field_at:#x} did not inflate "
                f"({exc}); the key, the nonce chain or the chunk framing is wrong"
            ) from exc
        chunks.append(
            Chunk(
                stream=stream,
                nonce=nonce,
                compressed=size,
                body=body,
                deflated=plaintext,
                skipped=skipped,
                offset=field_at,
            )
        )
        stream = (stream + 1) % STREAM_COUNT
    if not chunks:
        raise FastFileError("no chunks: the stream ended before the first one")
    return FastFile(
        header=header,
        chunks=tuple(chunks),
        total_bytes=len(data),
        terminators=terminators,
        end=offset,
        trailing_skips=tuple(trailing),
    )


def declared_size(content: bytes) -> int:
    """What the zone's own header says its length is."""
    if len(content) < ZONE_SIZE_AT + 4:
        raise FastFileError(f"a zone is at least {ZONE_SIZE_AT + 4} bytes, got {len(content)}")
    return struct.unpack_from(">I", content, ZONE_SIZE_AT)[0]


def with_declared_size(content: bytes) -> bytes:
    """The content with its own length field made to agree with its length.

    Derived rather than carried. A byte-identical repack is unaffected --
    the number it computes is the number that was already there -- and a
    repack whose content changed length gets a header that matches what it
    actually sends instead of what the file it came from used to be.
    """
    want = len(content) - ZONE_SIZE_PREFIX
    if want < 0:
        raise FastFileError(f"a zone is at least {ZONE_SIZE_PREFIX} bytes, got {len(content)}")
    return struct.pack(">I", want) + bytes(content[ZONE_SIZE_AT + 4 :])


def assemble(
    header: bytes,
    deflated: list[bytes],
    key: bytes = SALSA20_KEY,
    total_bytes: int = 0,
    gaps: dict[int, bytes] | None = None,
) -> bytes:
    """Frame, encrypt and chain already-deflated chunks into a fastfile.

    The nonce of every chunk is recomputed from the zone name and the chunks
    before it on its stream, so a chunk that did not change and sits behind
    only chunks that did not change gets back its original nonce, and with it
    its original ciphertext.

    `gaps` gives the bytes to write into a ring-buffer gap at a given offset;
    a gap with no entry is zero-filled. `total_bytes` pads the result to that
    length; zero means the original writer's rule (padded_length).
    """
    check_header(header)
    gaps = gaps or {}
    table = NonceTable(zone_name_of(header))
    out = bytearray(header[:CHUNKS_OFFSET])

    def field(size: int) -> None:
        gap = ring_gap(len(out))
        if gap:
            fill = gaps.get(len(out), b"")
            out.extend(fill if len(fill) == gap else bytes(gap))
        out.extend(CHUNK_SIZE_FIELD.pack(size))

    stream = 0
    for index, plaintext in enumerate(deflated):
        if not plaintext:
            raise FastFileError(f"chunk {index} is empty, and a zero size ends the stream")
        if len(plaintext) > XCHUNK_SIZE:
            raise FastFileError(
                f"chunk {index} compresses to {len(plaintext)} bytes, the most stored "
                f"is {XCHUNK_SIZE}"
            )
        nonce = table.nonce(stream)
        field(len(plaintext))
        out += crypt(plaintext, key, nonce)
        table.advance(stream, plaintext)
        stream = (stream + 1) % STREAM_COUNT
    # A zero size ends one stream, so every stream gets one.
    for _ in range(STREAM_COUNT):
        field(0)
    want = total_bytes or padded_length(len(out))
    if want < len(out):
        raise FastFileError(f"the chunks need {len(out)} bytes, the file was {total_bytes}")
    out += bytes(want - len(out))
    return bytes(out)


def write_fastfile(
    header: bytes,
    bodies: list[tuple[bytes, int | None]],
    key: bytes = SALSA20_KEY,
    total_bytes: int = 0,
) -> bytes:
    """Rebuild a fastfile from its chunk bodies, compressing every one.

    Each body is deflated at its given level (level 9 when None) and memLevel
    9, which is what reproduces the retail chunks. `total_bytes` pads the
    result out to that length; without it the original writer's rule is
    used. For keeping untouched chunks verbatim, see opent5.container.zone.
    """
    deflated = [deflate(body, DEFAULT_LEVEL if level is None else level) for body, level in bodies]
    return assemble(header, deflated, key, total_bytes)


def split_content(content: bytes, size: int = XCHUNK_WRITE_SIZE) -> list[bytes]:
    """Split fresh content the way the original writer does: the 36-byte
    prefix alone, then XCHUNK_WRITE_SIZE pieces, the last one shorter."""
    if len(content) <= ZONE_SIZE_PREFIX:
        return [bytes(content)] if content else [b""]
    head = [bytes(content[:ZONE_SIZE_PREFIX])]
    rest = content[ZONE_SIZE_PREFIX:]
    return head + [bytes(rest[at : at + size]) for at in range(0, len(rest), size)]


def unpack(path: Path, directory: Path, key: bytes = SALSA20_KEY, out=None) -> FastFile:
    out = sys.stdout if out is None else out
    fastfile = read_fastfile(path.read_bytes(), key)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / HEADER_NAME).write_bytes(fastfile.header)
    (directory / CONTENT_NAME).write_bytes(fastfile.content)
    manifest = {
        "zone_name": fastfile.zone_name,
        "total_bytes": fastfile.total_bytes,
        "chunks": [
            {"stream": c.stream, "nonce": c.nonce.hex(), "bytes": len(c.body), "level": c.level}
            for c in fastfile.chunks
        ],
    }
    (directory / MANIFEST_NAME).write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"  zone {fastfile.zone_name}, {len(fastfile.chunks)} chunk(s)", file=out)
    for index, chunk in enumerate(fastfile.chunks):
        print(
            f"    chunk {index:3d} stream {chunk.stream} nonce {chunk.nonce.hex()} "
            f"{chunk.compressed:6d} bytes in -> {len(chunk.body):7d} out",
            file=out,
        )
    print(f"  {len(fastfile.content)} bytes written to {directory / CONTENT_NAME}", file=out)
    return fastfile


def pack(
    directory: Path,
    target: Path,
    key: bytes = SALSA20_KEY,
    out=None,
    derive_size: bool = True,
) -> bytes:
    """Rebuild a fastfile from an unpacked directory.

    derive_size recomputes the zone's own length field from the content being
    packed. On by default because that is what a real zone wants: carried
    over, it describes the file the content came from rather than the file
    going out. It is a no-op on a byte-identical repack, because the number
    it computes is the number already there.

    Turn it off for content that is not a zone. This is the one thing here
    that reads a meaning into the bytes rather than moving them, so anything
    whose first four bytes are not a length gets them rewritten.
    """
    out = sys.stdout if out is None else out
    header = (directory / HEADER_NAME).read_bytes()
    content = (directory / CONTENT_NAME).read_bytes()
    # The zone's own length field, derived from the content actually being
    # packed rather than carried over from the file it was unpacked from.
    # Done here and not in write_fastfile: that one is a faithful primitive
    # over arbitrary buffers, and this is the step that knows its input is a
    # zone. A byte-identical repack is unaffected, because the number this
    # computes is the number already there.
    fixed = with_declared_size(content) if derive_size else content
    if fixed != content:
        print(
            f"  declared zone size {declared_size(content)} -> {declared_size(fixed)}"
            f" to match {len(content)} bytes of content",
            file=out,
        )
        content = fixed
    manifest_path = directory / MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text()) if manifest_path.is_file() else {}
    listed = manifest.get("chunks")
    total_bytes = manifest.get("total_bytes", 0)

    if listed is None:
        print(f"  no {MANIFEST_NAME}: splitting at {XCHUNK_MAX_WRITE} bytes", file=out)
        bodies = [(piece, None) for piece in split_content(content)]
    else:
        bodies = []
        at = 0
        for entry in listed:
            size = entry["bytes"]
            bodies.append((content[at : at + size], entry.get("level")))
            at += size
        if at != len(content):
            raise FastFileError(
                f"the manifest accounts for {at} bytes, {CONTENT_NAME} holds {len(content)}"
            )
    data = write_fastfile(header, bodies, key, total_bytes)
    target.write_bytes(data)
    print(f"  {len(bodies)} chunk(s), {target} is {len(data)} bytes", file=out)
    if carries_console_signature(header):
        print(
            "  the header carries a console signature, copied over unchanged. "
            "If the content changed, the signature no longer matches and the "
            "zone loads only on a client with the signature check patched out",
            file=out,
        )
    return data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="action", required=True)

    out = sub.add_parser("unpack", help="decrypt and decompress a fastfile")
    out.add_argument("fastfile", type=Path)
    out.add_argument("directory", type=Path)

    into = sub.add_parser("pack", help="rebuild a fastfile from a directory")
    into.add_argument("directory", type=Path)
    into.add_argument("fastfile", type=Path)
    into.add_argument(
        "--keep-size",
        action="store_true",
        help="carry the zone's declared length over instead of deriving it "
        "from the content, for a buffer that is not a zone",
    )

    for which in (out, into):
        which.add_argument("--key", help="a 32-byte Salsa20 key as hex, for another title")

    args = parser.parse_args(argv)
    key = bytes.fromhex(args.key) if args.key else SALSA20_KEY
    try:
        if args.action == "unpack":
            unpack(args.fastfile, args.directory, key)
        else:
            pack(args.directory, args.fastfile, key, derive_size=not args.keep_size)
    except FastFileError as exc:
        print(f"fastfile: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
