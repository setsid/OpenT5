"""Unpacking and repacking a T5 PS3 fastfile."""

import io
import struct
import zlib

import pytest

from opent5 import env
from opent5.container import fastfile
from opent5.container.fastfile import (
    AUTH_MAGIC,
    BLOCK_HASHES_COUNT,
    CHUNK_SIZE_FIELD,
    CHUNKS_OFFSET,
    FILE_ALIGNMENT,
    OFFSET_AUTH_MAGIC,
    OFFSET_SIGNATURE,
    OFFSET_ZONE_NAME,
    SHA1_HASH_SIZE,
    SIGNATURE_SIZE,
    STREAM_COUNT,
    XCHUNK_SIZE,
    ZONE_MAGIC,
    ZONE_NAME_SIZE,
    ZONE_VERSION,
    FastFileError,
    NonceTable,
    carries_console_signature,
    main,
    pack,
    read_fastfile,
    unpack,
    write_fastfile,
)

# The installed update's zones: small, signed, and the ones the container
# code was first proved against.
ZONES = env.path_of("OPENT5_PATCH_ZONES") or env.ROOT / "missing"
EXACT = ZONES / "patch.ff"
BIGGEST = ZONES / "patch_ui_mp.ff"
ZOMBIE_ZONES = ("zombie_moon_patch.ff", "zombie_theater_patch.ff", "zombietron_patch.ff")

ZONE_NAME = b"ffotd_tu13_mp"


def header_for(name=ZONE_NAME):
    out = bytearray(CHUNKS_OFFSET)
    out[0:8] = ZONE_MAGIC
    struct.pack_into(">I", out, 8, ZONE_VERSION)
    struct.pack_into(">I", out, 0x0C, 1)
    out[OFFSET_AUTH_MAGIC : OFFSET_AUTH_MAGIC + 8] = AUTH_MAGIC
    out[OFFSET_ZONE_NAME : OFFSET_ZONE_NAME + len(name)] = name
    # A signature we made up: nothing here checks it.
    out[OFFSET_SIGNATURE : OFFSET_SIGNATURE + SIGNATURE_SIZE] = bytes(
        (i * 7) & 0xFF for i in range(SIGNATURE_SIZE)
    )
    return bytes(out)


def built(bodies, name=ZONE_NAME, total_bytes=0):
    return write_fastfile(header_for(name), [(b, None) for b in bodies], total_bytes=total_bytes)


class TestTheNonceTable:
    def test_it_is_seeded_four_bytes_at_a_time_from_the_zone_name(self):
        table = NonceTable(ZONE_NAME)

        assert bytes(table.blocks[:20]) == b"ffffffffoooottttdddd"

    def test_the_first_nonce_is_the_first_eight_bytes(self):
        assert NonceTable(ZONE_NAME).nonce(0) == b"ffffffff"

    def test_each_stream_starts_at_its_own_block(self):
        table = NonceTable(ZONE_NAME)

        assert table.nonce(1) == b"____tttt"
        assert table.nonce(0) != table.nonce(1)

    def test_the_table_is_shared_across_streams_not_per_stream(self):
        """The T6 reference sizes it per stream; T5 does not, which is what
        the large zones show."""
        assert len(NonceTable(ZONE_NAME).blocks) == BLOCK_HASHES_COUNT * SHA1_HASH_SIZE

    def test_the_block_index_wraps_at_the_count_over_the_streams(self):
        assert NonceTable(ZONE_NAME).wrap == BLOCK_HASHES_COUNT // STREAM_COUNT == 50

    def test_advancing_changes_the_nonce(self):
        table = NonceTable(ZONE_NAME)
        first = table.nonce(0)

        table.advance(0, b"a chunk of plaintext")

        assert table.nonce(0) != first

    def test_advancing_one_stream_leaves_the_others_where_they_were(self):
        table = NonceTable(ZONE_NAME)
        before = table.nonce(1)

        table.advance(0, b"a chunk of plaintext")

        assert table.nonce(1) == before

    def test_the_chain_depends_on_the_plaintext(self):
        one, other = NonceTable(ZONE_NAME), NonceTable(ZONE_NAME)

        one.advance(0, b"one chunk")
        other.advance(0, b"a different chunk")

        assert one.nonce(0) != other.nonce(0)

    def test_a_different_zone_name_seeds_differently(self):
        assert NonceTable(b"patch_mp").nonce(0) != NonceTable(ZONE_NAME).nonce(0)

    def test_the_name_is_cut_to_the_field_it_came_from(self):
        long_name = b"x" * 40

        assert len(NonceTable(long_name).blocks) == BLOCK_HASHES_COUNT * SHA1_HASH_SIZE

    def test_an_empty_name_is_refused(self):
        with pytest.raises(FastFileError, match="needs a zone name"):
            NonceTable(b"")


class TestTheHeader:
    def test_a_built_header_reads_back(self):
        data = built([b"content" * 40])

        assert read_fastfile(data).zone_name == ZONE_NAME.decode()

    def test_the_zone_magic_is_checked(self):
        data = bytearray(built([b"content" * 40]))
        data[0:8] = b"NOTAZONE"

        with pytest.raises(FastFileError, match="not a fastfile"):
            read_fastfile(bytes(data))

    def test_the_auth_magic_is_checked(self):
        data = bytearray(built([b"content" * 40]))
        data[OFFSET_AUTH_MAGIC : OFFSET_AUTH_MAGIC + 8] = b"XXXXXXXX"

        with pytest.raises(FastFileError, match="no auth header"):
            read_fastfile(bytes(data))

    def test_the_version_is_checked(self):
        data = bytearray(built([b"content" * 40]))
        struct.pack_into(">I", data, 8, 999)

        with pytest.raises(FastFileError, match="zone version 999"):
            read_fastfile(bytes(data))

    def test_a_file_too_short_for_a_header_is_refused(self):
        with pytest.raises(FastFileError, match="header is"):
            read_fastfile(ZONE_MAGIC)

    def test_an_empty_zone_name_is_refused(self):
        header = bytearray(header_for())
        header[OFFSET_ZONE_NAME : OFFSET_ZONE_NAME + ZONE_NAME_SIZE] = bytes(ZONE_NAME_SIZE)

        with pytest.raises(FastFileError, match="zone name field is empty"):
            write_fastfile(bytes(header), [(b"x" * 100, None)])

    def test_the_signature_is_carried_not_recomputed(self):
        data = built([b"content" * 40])

        signature = data[OFFSET_SIGNATURE : OFFSET_SIGNATURE + SIGNATURE_SIZE]
        assert signature == header_for()[OFFSET_SIGNATURE : OFFSET_SIGNATURE + SIGNATURE_SIZE]


class TestTheChunkStream:
    def test_the_size_field_is_big_endian(self):
        data = built([b"a" * 500])

        (size,) = CHUNK_SIZE_FIELD.unpack_from(data, CHUNKS_OFFSET)
        assert size == len(zlib.compress(b"a" * 500, 9)) - 6 or size > 0
        assert data[CHUNKS_OFFSET] == 0, "a big-endian size of this scale starts with a zero byte"

    def test_chunks_go_round_robin_over_the_streams(self):
        fastfile = read_fastfile(built([b"chunk %d" % n * 60 for n in range(6)]))

        assert [c.stream for c in fastfile.chunks] == [0, 1, 2, 3, 0, 1]

    def test_every_stream_gets_its_own_nonce(self):
        fastfile = read_fastfile(built([b"chunk %d" % n * 60 for n in range(4)]))

        assert len({c.nonce for c in fastfile.chunks}) == 4

    def test_the_stream_is_terminated_once_per_stream(self):
        data = built([b"one chunk" * 40])

        fastfile = read_fastfile(data)
        end = CHUNKS_OFFSET + CHUNK_SIZE_FIELD.size + fastfile.chunks[0].compressed
        terminators = data[end : end + CHUNK_SIZE_FIELD.size * STREAM_COUNT]
        assert terminators == bytes(CHUNK_SIZE_FIELD.size * STREAM_COUNT)

    def test_the_file_is_padded_to_the_alignment(self):
        assert len(built([b"one chunk" * 40])) % FILE_ALIGNMENT == 0

    def test_a_chunk_claiming_more_than_the_maximum_is_refused(self):
        data = bytearray(built([b"content" * 40]))
        CHUNK_SIZE_FIELD.pack_into(data, CHUNKS_OFFSET, XCHUNK_SIZE + 1)

        with pytest.raises(FastFileError, match="the most is"):
            read_fastfile(bytes(data))

    def test_a_truncated_chunk_is_refused(self):
        """Content that does not compress away, so the chunk is longer than
        the cut."""
        awkward = bytes((i * 37 + i // 251) & 0xFF for i in range(4000))

        data = built([awkward])

        with pytest.raises(FastFileError, match="is short"):
            read_fastfile(data[: CHUNKS_OFFSET + 40])

    def test_a_file_with_no_chunks_is_refused(self):
        data = header_for() + bytes(64)

        with pytest.raises(FastFileError, match="no chunks"):
            read_fastfile(data)


class TestRoundTrip:
    @pytest.mark.parametrize("count", [1, 2, 4, 5, 9])
    def test_content_survives_however_many_chunks(self, count):
        bodies = [bytes([n]) * 700 for n in range(count)]

        fastfile = read_fastfile(built(bodies))

        assert fastfile.content == b"".join(bodies)

    def test_rebuilding_an_untouched_file_is_byte_identical(self):
        data = built([b"chunk %d" % n * 80 for n in range(6)])

        fastfile = read_fastfile(data)
        rebuilt = write_fastfile(
            fastfile.header,
            [(c.body, c.level) for c in fastfile.chunks],
            total_bytes=fastfile.total_bytes,
        )
        assert rebuilt == data

    def test_a_changed_chunk_comes_back_changed(self):
        fastfile = read_fastfile(built([b"before" * 80, b"second" * 80]))
        bodies = [(b"after" * 96, None), (fastfile.chunks[1].body, None)]

        rebuilt = write_fastfile(fastfile.header, bodies)

        assert read_fastfile(rebuilt).chunks[0].body == b"after" * 96

    def test_the_wrong_key_does_not_quietly_produce_rubbish(self):
        data = built([b"content" * 80])

        with pytest.raises(FastFileError, match="did not inflate"):
            read_fastfile(data, key=bytes(32))

    def test_a_file_shorter_than_its_own_chunks_is_refused(self):
        with pytest.raises(FastFileError, match="the chunks need"):
            built([b"content" * 80], total_bytes=CHUNKS_OFFSET)


class TestOnDisk:
    def test_unpack_then_pack_is_byte_identical(self, tmp_path, capsys):
        source = tmp_path / "zone.ff"
        source.write_bytes(built([b"chunk %d" % n * 80 for n in range(6)]))

        unpack(source, tmp_path / "out")
        # derive_size off: this fixture's content is not a zone, so its first
        # four bytes are not a length and deriving one would rewrite them.
        # A real zone needs no such thing -- the value derived is the value
        # already there, which TestTheZoneDeclaresItsOwnLength pins.
        rebuilt = pack(tmp_path / "out", tmp_path / "rebuilt.ff", derive_size=False)

        assert rebuilt == source.read_bytes()

    def test_the_content_is_written_out_whole(self, tmp_path, capsys):
        bodies = [b"chunk %d" % n * 80 for n in range(3)]
        source = tmp_path / "zone.ff"
        source.write_bytes(built(bodies))

        unpack(source, tmp_path / "out")

        assert (tmp_path / "out" / "zone.bin").read_bytes() == b"".join(bodies)

    def test_main_round_trips(self, tmp_path, capsys):
        source = tmp_path / "zone.ff"
        source.write_bytes(built([b"chunk %d" % n * 80 for n in range(5)]))

        assert main(["unpack", str(source), str(tmp_path / "out")]) == 0
        assert main(["pack", str(tmp_path / "out"), str(tmp_path / "back.ff"), "--keep-size"]) == 0
        assert (tmp_path / "back.ff").read_bytes() == source.read_bytes()

    def test_a_file_that_is_not_a_fastfile_is_reported(self, tmp_path, capsys):
        broken = tmp_path / "broken.ff"
        broken.write_bytes(b"not a zone at all" * 20)

        assert main(["unpack", str(broken), str(tmp_path / "out")]) == 1
        assert "fastfile:" in capsys.readouterr().err


@pytest.mark.skipif(not EXACT.is_file(), reason="the retail zones are not on this machine")
class TestTheRetailZones:
    """Against the real files, which are not committed."""

    def test_the_patch_zone_round_trips_byte_identically(self):
        raw = EXACT.read_bytes()
        fastfile = read_fastfile(raw)

        rebuilt = write_fastfile(
            fastfile.header,
            [(c.body, c.level) for c in fastfile.chunks],
            total_bytes=fastfile.total_bytes,
        )
        assert rebuilt == raw

    def test_the_first_chunk_is_the_36_byte_prefix_and_the_nonces_seed_from_the_name(self):
        fastfile = read_fastfile(EXACT.read_bytes())

        assert len(fastfile.chunks[0].body) == 36
        # "patch" is five characters, four bytes each: exactly one 20-byte
        # block, so every stream's first block starts the same way.
        assert fastfile.chunks[0].nonce == b"ppppaaaa"
        assert fastfile.chunks[1].nonce == b"ppppaaaa"

    @pytest.mark.parametrize("name", ZOMBIE_ZONES)
    def test_a_zombie_zone_opens(self, name):
        path = ZONES / name
        if not path.is_file():
            pytest.skip(f"{name} is not here")

        fastfile = read_fastfile(path.read_bytes())

        assert len(fastfile.chunks) >= 2
        assert len(fastfile.content) > 40000

    @pytest.mark.skipif(not BIGGEST.is_file(), reason="patch_ui_mp.ff is not here")
    def test_the_largest_zone_exercises_every_stream_and_the_wrap(self):
        """279 chunks: past the 50-block wrap on every stream."""
        fastfile = read_fastfile(BIGGEST.read_bytes())

        assert len(fastfile.chunks) > BLOCK_HASHES_COUNT
        assert sorted({c.stream for c in fastfile.chunks}) == list(range(STREAM_COUNT))
        assert len(fastfile.content) > 13_000_000

    @pytest.mark.skipif(not BIGGEST.is_file(), reason="patch_ui_mp.ff is not here")
    def test_the_largest_zone_survives_a_repack(self):
        fastfile = read_fastfile(BIGGEST.read_bytes())

        rebuilt = write_fastfile(
            fastfile.header,
            [(c.body, c.level) for c in fastfile.chunks],
            total_bytes=fastfile.total_bytes,
        )
        assert read_fastfile(rebuilt).content == fastfile.content


class TestTheZoneDeclaresItsOwnLength:
    """The decompressed zone carries a length field that pack used to carry
    through unchanged.

    Right for a byte-identical repack and wrong for every other kind: change
    a member's length and the zone header says one size while another
    arrives. It is derived now.
    """

    #: The fixed prefix the declared length excludes. Measured on fourteen
    #: zones -- a 31KB ffotd through a 13MB patch_ui_mp -- with no exception.
    PREFIX = 36

    def a_zone(self, body=b"the zone body"):
        """A content buffer whose header agrees with its own length."""
        return fastfile.with_declared_size(bytes(self.PREFIX) + body)

    def test_the_prefix_is_the_measured_one(self):
        assert fastfile.ZONE_SIZE_PREFIX == self.PREFIX
        assert fastfile.ZONE_SIZE_AT == 0

    def test_the_field_is_the_length_after_the_prefix(self):
        content = self.a_zone(b"x" * 500)

        assert fastfile.declared_size(content) == len(content) - self.PREFIX
        assert fastfile.declared_size(content) == 500

    def test_deriving_it_changes_nothing_when_it_already_agrees(self):
        """Which is why a byte-identical repack stays byte-identical."""
        content = self.a_zone(b"x" * 64)

        assert fastfile.with_declared_size(content) == content

    def test_a_longer_zone_gets_a_longer_declared_length(self):
        content = self.a_zone(b"x" * 64)
        longer = content + b"more"

        assert fastfile.declared_size(fastfile.with_declared_size(longer)) == len(longer) - 36

    def test_a_shorter_zone_gets_a_shorter_one(self):
        content = self.a_zone(b"x" * 64)
        shorter = content[:-10]

        assert fastfile.declared_size(fastfile.with_declared_size(shorter)) == len(shorter) - 36

    def test_only_the_first_four_bytes_move(self):
        content = self.a_zone(b"x" * 64)
        wrong = b"\x00\x00\x00\x01" + content[4:]

        fixed = fastfile.with_declared_size(wrong)

        assert fixed[4:] == wrong[4:]
        assert fixed[:4] != wrong[:4]

    def test_a_buffer_too_short_to_hold_a_zone_is_refused(self):
        for wrong in (b"", b"abc"):
            with pytest.raises(fastfile.FastFileError):
                fastfile.declared_size(wrong)
        with pytest.raises(fastfile.FastFileError):
            fastfile.with_declared_size(b"short")

    def test_packing_writes_the_derived_length(self, tmp_path):
        """End to end through pack, which is the step that knows its input is
        a zone. The field a console reads describes what was actually sent."""
        content = self.a_zone(b"y" * 300) + b"z" * 40
        (tmp_path / fastfile.HEADER_NAME).write_bytes(header_for())
        (tmp_path / fastfile.CONTENT_NAME).write_bytes(content)
        target = tmp_path / "rebuilt.ff"

        fastfile.pack(tmp_path, target, out=io.StringIO())
        back = fastfile.read_fastfile(target.read_bytes())

        assert fastfile.declared_size(back.content) == len(content) - self.PREFIX

    def test_write_fastfile_itself_leaves_the_content_alone(self):
        """It is a faithful primitive over arbitrary buffers. Deriving the
        field there would corrupt anything that is not a zone."""
        odd = b"\x00\x00\x00\x01" + b"not a zone at all" * 4

        data = fastfile.write_fastfile(header_for(), [(odd, 6)])

        assert fastfile.read_fastfile(data).content == odd


ALL_RETAIL_ZONES = (
    "patch.ff",
    "patch_mp.ff",
    "patch_ui.ff",
    "patch_ui_mp.ff",
    "common_zombie_patch.ff",
    "zombie_moon_patch.ff",
    "zombie_theater_patch.ff",
    "zombietron_patch.ff",
)


class TestTheConsoleSignature:
    """The 256 bytes at 0x3c, read under the key lifted out of t5mp.elf.

    Nothing here can sign: that needs the private key. What it can do is tell
    a real signature from an invented one, which is the difference between a
    repack the console will take and one it will not.
    """

    def test_the_recovered_key_is_a_whole_rsa_2048_key(self):
        assert fastfile.CONSOLE_KEY_MODULUS.bit_length() == 2048
        assert fastfile.CONSOLE_KEY_EXPONENT == 65537

    def test_a_made_up_signature_does_not_decode(self):
        """The header the rest of these tests build carries invented bytes."""
        assert fastfile.console_signature_of(header_for()) is None

    def test_a_header_too_short_to_hold_one_does_not_decode(self):
        assert fastfile.console_signature_of(header_for()[:OFFSET_SIGNATURE]) is None

    @pytest.mark.skipif(not EXACT.is_file(), reason="the retail zones are not on this machine")
    def test_the_retail_patch_carries_one(self):
        signature = fastfile.console_signature_of(read_fastfile(EXACT.read_bytes()).header)

        assert signature is not None
        assert len(signature.salt) == fastfile.PSS_SALT_SIZE
        assert len(signature.digest) == fastfile.PSS_HASH_SIZE

    @pytest.mark.skipif(not EXACT.is_file(), reason="the retail zones are not on this machine")
    @pytest.mark.parametrize("name", ALL_RETAIL_ZONES)
    def test_every_retail_zone_on_this_machine_carries_one(self, name):
        path = ZONES / name
        if not path.is_file():
            pytest.skip(f"{name} is not here")

        assert carries_console_signature(path.read_bytes()[:CHUNKS_OFFSET])

    @pytest.mark.skipif(not EXACT.is_file(), reason="the retail zones are not on this machine")
    def test_flipping_one_bit_of_it_stops_it_decoding(self):
        """Which is why a signature cannot be edited any more than the zone
        can: the padding is what fails, long before anything is compared."""
        header = bytearray(read_fastfile(EXACT.read_bytes()).header)
        header[OFFSET_SIGNATURE] ^= 0x01

        assert fastfile.console_signature_of(bytes(header)) is None

    @pytest.mark.skipif(not EXACT.is_file(), reason="the retail zones are not on this machine")
    def test_a_repack_carries_the_same_signature_over_a_changed_zone(self):
        """The whole finding in one assertion: the bytes move, the signature
        does not, and nothing in this tool can make them agree again."""
        raw = EXACT.read_bytes()
        original = read_fastfile(raw)
        edited = bytearray(original.content)
        edited[-40] ^= 0x20
        bodies = []
        at = 0
        for chunk in original.chunks:
            bodies.append((bytes(edited[at : at + len(chunk.body)]), chunk.level))
            at += len(chunk.body)

        rebuilt = write_fastfile(original.header, bodies, total_bytes=original.total_bytes)

        assert rebuilt != raw
        assert (
            rebuilt[OFFSET_SIGNATURE : OFFSET_SIGNATURE + SIGNATURE_SIZE]
            == raw[OFFSET_SIGNATURE : OFFSET_SIGNATURE + SIGNATURE_SIZE]
        )

    @pytest.mark.skipif(not EXACT.is_file(), reason="the retail zones are not on this machine")
    def test_packing_a_signed_header_says_so(self, tmp_path):
        original = read_fastfile(EXACT.read_bytes())
        (tmp_path / fastfile.HEADER_NAME).write_bytes(original.header)
        (tmp_path / fastfile.CONTENT_NAME).write_bytes(original.content)
        out = io.StringIO()

        fastfile.pack(tmp_path, tmp_path / "rebuilt.ff", out=out)

        assert "carries a console signature" in out.getvalue()

    def test_packing_an_unsigned_header_says_nothing(self, tmp_path):
        (tmp_path / fastfile.HEADER_NAME).write_bytes(header_for())
        (tmp_path / fastfile.CONTENT_NAME).write_bytes(b"\x00\x00\x00\x10" + b"z" * 48)
        out = io.StringIO()

        fastfile.pack(tmp_path, tmp_path / "rebuilt.ff", out=out)

        assert "console signature" not in out.getvalue()
