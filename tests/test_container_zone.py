"""Editing a zone and saving it: untouched chunks verbatim, edited ones
re-chunked, and whatever is saved reads back as exactly what was edited.

The property tests use `random` with fixed seeds, so a failure repeats.
"""

import random
import struct

import pytest

from opent5 import env
from opent5.container.fastfile import (
    AUTH_MAGIC,
    CHUNK_SIZE_FIELD,
    CHUNKS_OFFSET,
    FILE_ALIGNMENT,
    OFFSET_AUTH_MAGIC,
    OFFSET_SIGNATURE,
    OFFSET_ZONE_NAME,
    RING_BUFFER_SIZE,
    SIGNATURE_SIZE,
    STREAM_COUNT,
    TRAILER_SLACK,
    XCHUNK_WRITE_SIZE,
    ZONE_MAGIC,
    ZONE_SIZE_PREFIX,
    ZONE_VERSION,
    FastFileError,
    assemble,
    deflate,
    padded_length,
    read_fastfile,
    ring_gap,
    split_content,
    with_declared_size,
    write_fastfile,
)
from opent5.container.zone import Zone, first_difference, verify

NAME = b"mp_testzone"


def header_for(name=NAME):
    out = bytearray(CHUNKS_OFFSET)
    out[0:8] = ZONE_MAGIC
    struct.pack_into(">I", out, 8, ZONE_VERSION)
    out[OFFSET_AUTH_MAGIC : OFFSET_AUTH_MAGIC + 8] = AUTH_MAGIC
    out[OFFSET_ZONE_NAME : OFFSET_ZONE_NAME + len(name)] = name
    out[OFFSET_SIGNATURE : OFFSET_SIGNATURE + SIGNATURE_SIZE] = bytes(
        (i * 13) & 0xFF for i in range(SIGNATURE_SIZE)
    )
    return bytes(out)


def zone_content(rng, size):
    """Content that compresses like a zone: runs, repeats and some noise."""
    out = bytearray()
    while len(out) < size:
        kind = rng.random()
        if kind < 0.4:
            out += bytes([rng.randrange(256)]) * rng.randrange(1, 300)
        elif kind < 0.7 and out:
            start = rng.randrange(len(out))
            out += out[start : start + rng.randrange(1, 200)]
        else:
            out += rng.randbytes(rng.randrange(1, 120))
    return with_declared_size(bytes(ZONE_SIZE_PREFIX) + bytes(out[:size]))


def zone_file(content, piece=5000):
    """A zone split like the original writer does, with smaller pieces so
    small content still has many chunk boundaries."""
    return write_fastfile(header_for(), [(p, None) for p in split_content(content, piece)])


@pytest.fixture(scope="module")
def small():
    content = zone_content(random.Random(1), 60_000)
    return content, zone_file(content)


class TestTheFramingRules:
    def test_no_gap_away_from_the_ring_end(self):
        assert ring_gap(0x13C) == 0
        assert ring_gap(RING_BUFFER_SIZE - 4) == 0
        assert ring_gap(RING_BUFFER_SIZE) == 0

    @pytest.mark.parametrize("left", [1, 2, 3])
    def test_a_field_that_would_straddle_the_ring_end_skips_to_it(self, left):
        assert ring_gap(RING_BUFFER_SIZE * 31 - left) == left

    def test_padding_follows_the_original_writer(self):
        # patch.ff: terminators end at 0x14dcb, the file is 0x14e00 long.
        assert padded_length(0x14DCB) == 0x14E00
        # common_mp.ff: the smallest padding seen, 48 bytes.
        assert padded_length(0x34A21F0) == 0x34A2220
        for end in range(1000, 1100):
            length = padded_length(end)
            assert length % FILE_ALIGNMENT == 0
            assert TRAILER_SLACK <= length - end < TRAILER_SLACK + FILE_ALIGNMENT

    def test_the_prefix_is_a_chunk_of_its_own(self):
        content = zone_content(random.Random(2), 120_000)

        pieces = split_content(content)

        assert len(pieces[0]) == ZONE_SIZE_PREFIX
        assert all(len(p) == XCHUNK_WRITE_SIZE for p in pieces[1:-1])
        assert b"".join(pieces) == content

    def test_four_terminators_end_the_file(self, small):
        _, data = small
        fastfile = read_fastfile(data)

        assert fastfile.terminators == STREAM_COUNT
        assert data[fastfile.end - 16 : fastfile.end] == bytes(16)
        assert len(data) == padded_length(fastfile.end)


class TestAnUntouchedZone:
    def test_saves_byte_identical(self, small):
        content, data = small

        result = Zone.open(data).build()

        assert result.identical
        assert result.data == data
        assert result.deflated == 0

    def test_saves_byte_identical_when_recompressed(self, small):
        _, data = small

        assert Zone.open(data).build(recompress=True).data == data

    def test_is_not_modified(self, small):
        assert not Zone.open(small[1]).modified

    def test_saves_to_disk(self, small, tmp_path):
        target = tmp_path / "out.ff"

        result = Zone.open(small[1]).save(target)

        assert target.read_bytes() == small[1] == result.data


def edit(rng, content, boundaries):
    """One random edit: overwrite, insert, delete or replace, often placed on
    or across a chunk boundary."""
    content = bytearray(content)
    if rng.random() < 0.5 and boundaries:
        at = rng.choice(boundaries) + rng.randrange(-3, 4)
    else:
        at = rng.randrange(ZONE_SIZE_PREFIX, len(content))
    at = max(ZONE_SIZE_PREFIX, min(at, len(content)))
    kind = rng.choice(["overwrite", "insert", "delete", "replace"])
    span = rng.choice([1, 2, 7, 100, 4999, 5000, 5001, 12_000])
    if kind == "overwrite":
        piece = rng.randbytes(min(span, len(content) - at))
        content[at : at + len(piece)] = piece
    elif kind == "insert":
        content[at:at] = rng.randbytes(span)
    elif kind == "delete":
        del content[at : at + span]
    else:
        content[at : at + span] = rng.randbytes(rng.choice([1, 50, 5000, 9000]))
    return kind, bytes(content)


class TestEditedZonesReadBackAsEdited:
    @pytest.mark.parametrize("seed", range(8))
    def test_random_edits(self, small, seed):
        original, data = small
        rng = random.Random(1000 + seed)
        zone = Zone.open(data)
        boundaries = []
        at = 0
        for size in zone.chunk_sizes:
            at += size
            boundaries.append(at)
        for _ in range(25):
            kind, edited = edit(rng, original, boundaries)
            zone = Zone.open(data)
            zone.content[:] = edited

            result = zone.build()

            want = with_declared_size(edited)
            assert read_fastfile(result.data).content == want, kind
            verify(result.data, expected=want)
            assert (
                result.data[OFFSET_SIGNATURE : OFFSET_SIGNATURE + SIGNATURE_SIZE]
                == (data[OFFSET_SIGNATURE : OFFSET_SIGNATURE + SIGNATURE_SIZE])
            )

    def test_chunks_before_an_edit_are_carried_verbatim(self, small):
        content, data = small
        zone = Zone.open(data)
        sizes = zone.chunk_sizes
        at = sum(sizes[:6]) + 10
        zone.content[at] ^= 0xFF

        result = zone.build()

        # Same length: only the one chunk is deflated again.
        assert result.deflated == 1
        assert result.kept == len(sizes) - 1
        # Everything before it is byte-identical on disk too.
        first = read_fastfile(data).chunks[6].offset
        assert result.data[:first] == data[:first]

    def test_chunks_after_an_insertion_are_carried_verbatim(self, small):
        _, data = small
        zone = Zone.open(data)
        at = sum(zone.chunk_sizes[:3]) + 100
        zone.content[at:at] = b"inserted" * 50

        result = zone.build()

        # The prefix (its length field moved) and the split middle chunk.
        assert result.kept == len(zone.chunk_sizes) - 2
        verify(result.data, expected=with_declared_size(bytes(zone.content)))

    def test_the_declared_length_follows_the_content(self, small):
        _, data = small
        zone = Zone.open(data)
        del zone.content[-1000:]

        back = read_fastfile(zone.build().data).content

        assert struct.unpack_from(">I", back)[0] == len(back) - ZONE_SIZE_PREFIX

    def test_a_long_insertion_is_split_like_the_original_writer(self, small):
        _, data = small
        zone = Zone.open(data)
        zone.content[100:100] = random.Random(5).randbytes(3 * XCHUNK_WRITE_SIZE)

        back = read_fastfile(zone.build().data)

        sizes = [len(c.body) for c in back.chunks]
        assert sizes.count(XCHUNK_WRITE_SIZE) >= 3
        assert max(sizes) <= XCHUNK_WRITE_SIZE

    def test_editing_the_header_name_rechains_every_nonce(self, small):
        _, data = small
        zone = Zone.open(data)
        zone.header[OFFSET_ZONE_NAME : OFFSET_ZONE_NAME + 4] = b"zz_t"

        result = zone.build()

        assert read_fastfile(result.data).content == bytes(zone.content)
        assert result.data[CHUNKS_OFFSET:] != data[CHUNKS_OFFSET:]

    def test_content_shorter_than_the_prefix_is_refused(self, small):
        zone = Zone.open(small[1])
        del zone.content[10:]

        with pytest.raises(FastFileError, match="at least 36"):
            zone.build()


def file_with_a_gap(left, fill=b"\x60\x61\x62"):
    """A zone whose size field after the first long chunk would straddle the
    ring end by `left` bytes, with `fill` in the gap as the original writer
    left there. Built by sizing an incompressible chunk until it ends at
    RING_BUFFER_SIZE - left."""
    rng = random.Random(left)
    target = RING_BUFFER_SIZE - left
    prefix_body = rng.randbytes(500)
    bodies = []
    end = CHUNKS_OFFSET
    head = bytes(ZONE_SIZE_PREFIX)
    # Stored (level 0), so its length does not depend on the length field
    # filled in at the end.
    deflated = [deflate(head, 0)]
    end += 4 + len(deflated[0])
    while target - end > 70_000:
        body = rng.randbytes(40_000)
        bodies.append(body)
        deflated.append(deflate(body))
        end += 4 + len(deflated[-1])
    size = target - end - 4 - 20
    while True:
        body = rng.randbytes(size)
        stream = deflate(body)
        over = end + 4 + len(stream) - target
        if over == 0:
            break
        size -= over
    bodies.append(body)
    deflated.append(stream)
    tail = rng.randbytes(3000) + prefix_body
    bodies.append(tail)
    deflated.append(deflate(tail))
    content = head + b"".join(bodies)
    content = with_declared_size(content)
    deflated[0] = deflate(content[:ZONE_SIZE_PREFIX], 0)
    gaps = {target: fill[:left]}
    return content, assemble(header_for(), deflated, gaps=gaps)


class TestTheRingGap:
    @pytest.mark.parametrize("left", [1, 2, 3])
    def test_a_gap_is_read_past(self, left):
        content, data = file_with_a_gap(left)

        fastfile = read_fastfile(data)

        assert fastfile.content == content
        gapped = [c for c in fastfile.chunks if c.skipped]
        assert len(gapped) == 1
        assert gapped[0].offset % RING_BUFFER_SIZE == 0
        assert gapped[0].skipped == b"\x60\x61\x62"[:left]

    @pytest.mark.parametrize("left", [1, 2, 3])
    def test_the_gap_bytes_survive_an_untouched_save(self, left):
        _, data = file_with_a_gap(left)

        assert Zone.open(data).build().identical

    def test_the_gap_bytes_survive_an_edit_after_them(self):
        content, data = file_with_a_gap(1)
        zone = Zone.open(data)
        zone.content[-5] ^= 1

        result = zone.build()

        assert result.data[RING_BUFFER_SIZE - 1] == 0x60
        verify(result.data, expected=bytes(zone.content))

    @pytest.mark.parametrize("shift", range(-6, 7))
    def test_edits_that_move_fields_across_the_ring_end_read_back(self, shift):
        content, data = file_with_a_gap(2)
        zone = Zone.open(data)
        if shift >= 0:
            zone.content[200:200] = bytes(shift)
        else:
            del zone.content[200 : 200 - shift]

        result = zone.build()

        verify(result.data, expected=with_declared_size(bytes(zone.content)))


class TestVerify:
    def test_reports_where_content_differs(self, small):
        content, data = small
        wrong = bytearray(content)
        wrong[5000] ^= 1

        with pytest.raises(FastFileError, match="differs at 0x1388"):
            verify(data, expected=bytes(wrong))

    def test_refuses_a_file_without_four_terminators(self, small):
        _, data = small
        end = read_fastfile(data).end

        with pytest.raises(FastFileError, match="terminators"):
            verify(data[: end - CHUNK_SIZE_FIELD.size])

    def test_first_difference(self):
        assert first_difference(b"abc", b"abd") == 2
        assert first_difference(b"ab", b"abc") == 2
        assert first_difference(bytes(200_000), bytes(199_999) + b"x") == 199_999


# Against the real zones, which are not committed.

PATCH = (env.path_of("OPENT5_PATCH_ZONES") or env.ROOT / "missing") / "patch.ff"
DISC = env.path_of("OPENT5_ZONES") or env.ROOT / "missing"
CREEK = DISC / "creek_1.ff"


@pytest.mark.zones
@pytest.mark.skipif(not PATCH.is_file(), reason="the retail zones are not on this machine")
class TestARealZone:
    def test_patch_saves_byte_identical_both_ways(self):
        data = PATCH.read_bytes()
        zone = Zone.open(data)

        assert zone.build().identical
        assert zone.build(recompress=True).identical

    @pytest.mark.parametrize("seed", range(3))
    def test_random_edits_read_back(self, seed):
        data = PATCH.read_bytes()
        rng = random.Random(seed)
        original = Zone.open(data)
        boundaries = []
        at = 0
        for size in original.chunk_sizes:
            at += size
            boundaries.append(at)
        for _ in range(6):
            _, edited = edit(rng, bytes(original.content), boundaries)
            zone = Zone.open(data)
            zone.content[:] = edited

            verify(zone.build().data, expected=with_declared_size(edited))


@pytest.mark.zones
@pytest.mark.slow
@pytest.mark.skipif(not CREEK.is_file(), reason="creek_1.ff is not on this machine")
def test_creek_1_keeps_the_byte_in_its_ring_gap():
    """The one ring gap in the retail zones: 0x60 at 0xb9ffff."""
    data = CREEK.read_bytes()

    result = Zone.open(data).build()

    assert data[0xB9FFFF] == 0x60
    assert result.identical


ALL_ZONES = env.all_zones()


@pytest.mark.zones
@pytest.mark.slow
@pytest.mark.parametrize("path", ALL_ZONES, ids=[f"{p.parent.name}/{p.name}" for p in ALL_ZONES])
def test_every_zone_round_trips_byte_identically(path):
    data = path.read_bytes()
    zone = Zone.open(data)

    assert zone.build().identical
    assert zone.build(recompress=True).identical
