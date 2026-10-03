"""Zone-level parsing and the tier-1 write side over a small synthetic zone whose
block sizes were worked out by hand."""

import struct

import pytest

from opent5.xfile import REGISTRY, AssetError, AssetType, Writer, parse
from opent5.xfile.constants import PTR_INLINE, encode_offset_pointer
from opent5.xfile.handlers.localize import LocalizeEntry
from opent5.xfile.handlers.rawfile import RawFile
from opent5.xfile.handlers.stringtable import StringTable, StringTableCell, string_hash


def u32s(*values):
    return b"".join(struct.pack(">I", v & 0xFFFFFFFF) for v in values)


RAWFILE = RawFile("a.txt", PTR_INLINE, 3, PTR_INLINE, b"abc\0")
# The name points at "a.txt", which the rawfile put at VIRTUAL + 40.
LOCALIZE = LocalizeEntry("Hi", PTR_INLINE, "a.txt", encode_offset_pointer(4, 40))
TABLE = StringTable(
    name="t.csv",
    name_ptr=PTR_INLINE,
    column_count=2,
    row_count=1,
    values_ptr=PTR_INLINE,
    cell_index_ptr=PTR_INLINE,
    cells=[
        StringTableCell("x", PTR_INLINE, string_hash("x")),
        StringTableCell("y", PTR_INLINE, string_hash("y")),
    ],
    cell_index=[0, 1],
)
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
        writer = Writer()
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
        assert raw == RAWFILE
        assert raw.contents() == b"abc"
        assert loc.value == "Hi" and loc.name == "a.txt"
        assert table.cell(0, 1) == "y"
        assert table.cells[0].hash == string_hash("x")

    def test_round_trip(self):
        data = build_zone()
        for asset in parse(data).assets:
            writer = Writer()
            REGISTRY[asset.type].write(asset.data, writer)
            assert writer.getvalue() == data[asset.file_start : asset.file_end]

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

    def test_only_tier_one_types_write(self):
        writable = {t for t, h in REGISTRY.items() if h.writable}
        assert writable == {AssetType.RAWFILE, AssetType.STRINGTABLE, AssetType.LOCALIZE}

    def test_read_only_handlers_say_so(self):
        with pytest.raises(NotImplementedError, match="material.*read-only"):
            REGISTRY[AssetType.MATERIAL].write({}, Writer())
