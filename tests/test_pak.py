"""opent5.container.pak on synthetic paks: read, byte-identical write, entry edits with and
without relayout, the non-ascending table img_patch.pak has, and the errors. No game files."""

from __future__ import annotations

import struct

import pytest

from opent5.container.pak import (
    MAGIC,
    SECTOR,
    Pak,
    PakError,
    compare,
    header_bytes,
    sha1_of,
)


def build_pak(
    entries: list[bytes], order: list[int] | None = None, field08: int = 1, stamp: int = 0x4CA3E2EE
) -> bytes:
    """A pak as the game's are laid out: header, start table, then entries in ``order``
    (file order; default index order), each zero-padded to the sector."""
    order = list(range(len(entries))) if order is None else order
    head_size = header_bytes(len(entries))
    starts = [0] * len(entries)
    body = bytearray()
    for i in order:
        starts[i] = (head_size + len(body)) // SECTOR
        body += entries[i] + bytes(-len(entries[i]) % SECTOR)
    head = struct.pack(">4sIIIIII", MAGIC, stamp, field08, len(entries), SECTOR, head_size, 0)
    head += struct.pack(f">{len(entries)}I", *starts)
    return head + bytes(head_size - len(head)) + bytes(body)


ENTRIES = [bytes([i + 1]) * n for i, n in enumerate([0x800, 0x1000, 0x10, 0x2345, 0x800])]


def test_header_size_rule():
    assert header_bytes(0) == 0x800
    assert header_bytes(0x805) == 0x2800  # mp_nuked.pak
    assert header_bytes(0x2FBC) == 0xC000  # images_low.pak
    assert header_bytes(0x100) == 0x800  # img_patch.pak


@pytest.mark.parametrize("order", [None, [3, 0, 4, 1, 2]])
def test_round_trip_is_byte_identical(order):
    data = build_pak(ENTRIES, order)
    pak = Pak.from_bytes(data)
    assert (pak.count, pak.field08, pak.sector, pak.header_size) == (5, 1, SECTOR, 0x800)
    assert pak.to_bytes() == data
    for i, e in enumerate(ENTRIES):
        assert pak.read(i, len(e)) == e
        assert pak.entry(i).capacity == len(e) + (-len(e) % SECTOR)


def test_round_trip_from_a_file(tmp_path):
    data = build_pak(ENTRIES, [2, 1, 0, 4, 3], field08=0x11)
    src = tmp_path / "img_patch.pak"
    src.write_bytes(data)
    with Pak.open(src) as pak:
        assert pak.field08 == 0x11
        assert pak.order == [2, 1, 0, 4, 3]
        size, sha1 = pak.write(tmp_path / "out" / "img_patch.pak")
        with pytest.raises(PakError, match="source pak itself"):
            pak.write(src)
    assert size == len(data)
    assert sha1 == sha1_of(src) == sha1_of(tmp_path / "out" / "img_patch.pak")


def test_empty_pak():
    data = build_pak([])
    pak = Pak.from_bytes(data)
    assert pak.count == 0 and pak.to_bytes() == data


def test_replace_same_sectors_keeps_the_layout():
    data = build_pak(ENTRIES, [3, 0, 4, 1, 2])
    pak = Pak.from_bytes(data)
    pak.replace(1, b"\xaa" * 0xFF0)  # 0x1000 bytes before, still two sectors
    out = Pak.from_bytes(pak.to_bytes())
    assert out.starts == pak.starts
    assert len(pak.to_bytes()) == len(data)
    assert out.read(1, 0xFF0) == b"\xaa" * 0xFF0
    assert out.read(1)[0xFF0:] == bytes(0x10)
    assert compare(pak, out, {1: b"\xaa" * 0xFF0}) == []


@pytest.mark.parametrize("new_size", [0x3001, 0x10])
def test_replace_other_size_relays_out_in_file_order(new_size):
    order = [3, 0, 4, 1, 2]
    pak = Pak.from_bytes(build_pak(ENTRIES, order))
    new = b"\x5a" * new_size
    pak.replace(0, new)
    out = Pak.from_bytes(pak.to_bytes())
    assert out.order == order  # file order kept
    assert out.read(0, new_size) == new
    for i, e in enumerate(ENTRIES):
        if i != 0:
            assert out.read(i, len(e)) == e
    shift = (new_size + (-new_size % SECTOR)) - 0x800
    assert out.starts[4] - pak.starts[4] == shift // SECTOR  # after entry 0 in the file
    assert out.starts[3] == pak.starts[3]  # before it
    assert compare(pak, out, {0: new}) == []


def test_compare_finds_a_changed_entry():
    pak = Pak.from_bytes(build_pak(ENTRIES))
    bad = bytearray(build_pak(ENTRIES))
    bad[pak.offset(2) + 3] ^= 1
    problems = compare(pak, Pak.from_bytes(bytes(bad)))
    assert problems == ["pak entry 2: bytes differ from the source"]


def test_compare_finds_nonzero_padding_after_new_bytes():
    pak = Pak.from_bytes(build_pak(ENTRIES))
    pak.replace(2, b"\x01" * 0x10)
    out = bytearray(pak.to_bytes())
    out[pak.offset(2) + 0x20] = 7
    assert "padding" in compare(pak, Pak.from_bytes(bytes(out)), {2: b"\x01" * 0x10})[0]


def test_shared_starts_round_trip_and_refuse_edits():
    data = bytearray(build_pak(ENTRIES[:3]))
    struct.pack_into(">I", data, 0x1C + 4 * 2, struct.unpack_from(">I", data, 0x1C + 4)[0])
    data = bytes(data[: 0x800 + 0x800 + 0x1000])  # entry 2 now aliases entry 1
    pak = Pak.from_bytes(data)
    assert pak.alias == {2: 1}
    assert pak.to_bytes() == data
    with pytest.raises(PakError, match="shares its start"):
        pak.replace(1, b"x")


def test_errors_name_what_was_expected():
    data = build_pak(ENTRIES)
    with pytest.raises(PakError, match=r"expected magic b'pak2' at 0x0, found b'pak3'"):
        Pak.from_bytes(b"pak3" + data[4:])
    cut = data[:0x1800]
    with pytest.raises(PakError, match="past the end of the file"):
        Pak.from_bytes(cut)
    pak = Pak.from_bytes(data)
    with pytest.raises(PakError, match="expected an entry index below 5, found 5"):
        pak.read(5)
    with pytest.raises(PakError, match="holds 2048"):
        pak.read(0, 0x801)
    bad_head = bytearray(data)
    struct.pack_into(">I", bad_head, 0x14, 0x7FF)
    with pytest.raises(PakError, match="multiple of the sector size"):
        Pak.from_bytes(bytes(bad_head))


@pytest.mark.parametrize("count", [len(ENTRIES), (SECTOR - 0x1C) // 4])
def test_append_adds_entries_after_the_last(count):
    """An appended entry goes last in the table and the file; at 505 entries the table
    fills the first sector, so one more grows the header and moves every entry."""
    entries = (ENTRIES * (count // len(ENTRIES) + 1))[:count]
    src = Pak.from_bytes(build_pak(entries))
    added = [b"\xaa" * 0x10, b"\xbb" * 0x1801]
    assert [src.append(a) for a in added] == [count, count + 1]
    out = Pak.from_bytes(src.to_bytes())
    assert out.count == count + 2
    assert out.header_size == header_bytes(count + 2)
    assert out.header_size == (2 * SECTOR if count == (SECTOR - 0x1C) // 4 else src.header_size)
    assert out.read(count, 0x10) == added[0] and out.read(count + 1) == added[1] + bytes(0x7FF)
    assert [out.read(i) for i in range(count)] == [src.read(i) for i in range(count)]
    assert compare(src, out, {count: added[0], count + 1: added[1]}) == []
    assert compare(src, out, {}) != []  # the extra entries must be named
