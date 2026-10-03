"""Zone-level parsing and the tier-1 write side over a small synthetic zone whose
block sizes were worked out by hand."""

import struct

import pytest

from opent5.xfile import REGISTRY, AssetError, AssetType, XWriter, parse, write, write_asset
from opent5.xfile.constants import PTR_INLINE, encode_offset_pointer
from opent5.xfile.handlers.localize import LocalizeEntry
from opent5.xfile.handlers.rawfile import RawFile
from opent5.xfile.handlers.stringtable import StringTable, string_hash


def u32s(*values):
    return b"".join(struct.pack(">I", v & 0xFFFFFFFF) for v in values)


RAWFILE = RawFile.build("a.txt", b"abc")
# The name points at "a.txt", which the rawfile put at VIRTUAL + 40.
LOCALIZE = LocalizeEntry.build("Hi", "a.txt")
LOCALIZE["header"] = u32s(PTR_INLINE, encode_offset_pointer(4, 40))
TABLE = StringTable.build("t.csv", [["x", "y"]])
ASSETS = (
    (AssetType.RAWFILE, RAWFILE),
    (AssetType.LOCALIZE, LOCALIZE),
    (AssetType.STRINGTABLE, TABLE),
)

# VIRTUAL, by hand: script string pointers 0..8, "hello\0" 8..14, align 4 -> 16,
# asset array 16..40; rawfile name 40..46, align 16 -> 48, buffer 48..52;
# localize value 52..55 (its name is shared); stringtable name 55..61, align 4 -> 64,
# cells 64..80, "x\0" "y\0" 80..84, cellIndex 84..88.
VIRTUAL_SIZE = 88
# TEMP: the largest header (stringtable, 20) plus the 16-byte slack.
TEMP_SIZE = 20 + 16


def asset_bytes():
    out = []
    for asset_type, data in ASSETS:
        writer = XWriter()
        REGISTRY[asset_type].write(data, writer)
        out.append(writer.getvalue())
    return out


def build_zone(temp=TEMP_SIZE, virtual=VIRTUAL_SIZE, extra=b""):
    body = u32s(2, PTR_INLINE, len(ASSETS), PTR_INLINE)
    body += u32s(0, PTR_INLINE) + b"hello\0"
    body += b"".join(u32s(t, PTR_INLINE) for t, _ in ASSETS)
    body += b"".join(asset_bytes()) + extra
    blocks = [temp, 0, 0, 0, virtual, 0, 0]
    head = struct.pack(">9I", len(body), 0, *blocks)
    return head + body


class TestSyntheticZone:
    def test_parses_exactly(self):
        xfile = parse(build_zone())
        assert xfile.problems() == []
        assert xfile.script_strings == [None, "hello"]
        assert [a.type for a in xfile.assets] == [t for t, _ in ASSETS]
        assert [a.name for a in xfile.assets] == ["a.txt", "a.txt", "t.csv"]
        assert xfile.final_cursors[4] == VIRTUAL_SIZE
        assert xfile.temp_high_water == 20

    def test_assets_tile_the_stream(self):
        data = build_zone()
        xfile = parse(data)
        spans = [(a.file_start, a.file_end) for a in xfile.assets]
        for (_, end), (start, _) in zip(spans, spans[1:], strict=False):
            assert end == start
        assert spans[-1][1] == xfile.tail_offset == len(data)
        assert [data[s:e] for s, e in spans] == asset_bytes()

    def test_structured_data(self):
        xfile = parse(build_zone())
        raw, loc, table = (a.data for a in xfile.assets)
        assert isinstance(raw, RawFile) and raw.length == 3
        assert raw.contents() == b"abc"
        assert loc.value == "Hi" and loc.name == "a.txt"
        assert table.cell(0, 1) == "y"
        assert table.hashes()[0] == string_hash("x")
        assert sorted(table.index()) == [0, 1]

    def test_round_trip_per_asset(self):
        data = build_zone()
        for asset in parse(data).assets:
            assert write_asset(asset) == data[asset.file_start : asset.file_end]

    def test_round_trip_whole_zone(self):
        data = build_zone()
        xfile = parse(data)
        written = write(xfile)
        assert written.content == data
        assert written.header == xfile.header
        assert (written.log.table() == xfile.log.table()).all()

    def test_an_edit_resizes_the_zone_and_its_header(self):
        xfile = parse(build_zone())
        raw = xfile.assets[0].data
        raw["buffer"] = b"abcdefgh\0"
        raw["header"] = raw["header"][:4] + struct.pack(">i", 8) + raw["header"][8:]
        written = write(xfile)
        assert len(written.content) == len(build_zone()) + 5
        assert written.header.size == len(written.content) - 36
        # The buffer still starts 16-aligned; everything after it moved in VIRTUAL.
        reparsed = parse(written.content)
        assert reparsed.problems() == []
        assert reparsed.assets[0].data.contents() == b"abcdefgh"

    def test_pointer_override_by_ordinal(self):
        data = build_zone()
        xfile = parse(data)
        pointers = xfile.log.of_kind(7)
        # Ordinal of the localize name pointer: the field at its header + 4.
        loc = xfile.assets[1]
        ordinal = next(i for i, p in enumerate(pointers) if p[1] == loc.file_start + 4)
        written = write(xfile, pointer_values={ordinal: encode_offset_pointer(4, 8)})
        assert written.content[loc.file_start + 4 : loc.file_start + 8] == u32s(0x80000009)
        assert parse(written.content).assets[1].data.name == "hello"

    def test_wrong_block_size_is_a_problem(self):
        problems = parse(build_zone(virtual=VIRTUAL_SIZE + 4)).problems()
        assert problems == ["block VIRTUAL: walk ends at 0x58, header blockSize[4] at 0x18 is 0x5c"]

    def test_trailing_bytes_are_a_problem(self):
        problems = parse(build_zone(extra=b"\0\0")).problems()
        assert len(problems) == 1 and "walk ended at" in problems[0]

    def test_truncated_asset_raises_with_the_asset(self):
        data = build_zone()[:-3]
        data = struct.pack(">I", len(data) - 36) + data[4:]
        with pytest.raises(AssetError) as err:
            parse(data)
        assert err.value.index == 2
        assert err.value.asset_type == AssetType.STRINGTABLE
        assert err.value.strings[0] == "t.csv"
        assert "expected at most" in str(err.value)

    def test_events_cover_every_byte_read(self):
        data = build_zone()
        xfile = parse(data)
        table = xfile.log.table()
        read = table[(table[:, 0] == 0) | (table[:, 0] == 1)]
        spans = sorted((int(r[1]), int(r[1] + r[2])) for r in read)
        assert spans[0][0] == 0x34
        for (_, end), (start, _) in zip(spans, spans[1:], strict=False):
            assert end == start
        assert spans[-1][1] == len(data)


class TestRegistry:
    def test_every_loaded_type_has_a_handler(self):
        no_loader = {21, 27, 28, 32, 33, 34, 35, 36, 37}
        assert set(REGISTRY) == set(range(46)) - no_loader

    def test_writing_a_node_without_its_data_names_the_key(self):
        node = RawFile.build("a.txt", b"abc")
        del node["buffer"]
        with pytest.raises(Exception, match="buffer"):
            REGISTRY[AssetType.RAWFILE].write(node, XWriter())

    def test_writing_a_short_array_names_the_sizes(self):
        node = RawFile.build("a.txt", b"abc")
        node["buffer"] = b"ab"
        with pytest.raises(Exception, match="expected 4 bytes.*found 2"):
            REGISTRY[AssetType.RAWFILE].write(node, XWriter())
