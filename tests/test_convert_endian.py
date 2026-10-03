"""The xfile layer with a byte order and a platform type map, on synthetic zones.

The same zone is built big-endian with PS3 type numbers and little-endian with other
type numbers; the little-endian one is parsed, written and re-laid out through a
``Platform`` and must behave exactly as the big-endian one does with the default (PS3).
"""

import struct
import zlib

import pytest

from opent5.convert import pc as pcmod
from opent5.xfile import AssetType, parse, write
from opent5.xfile.handlers.base import PS3, REGISTRY
from opent5.xfile.remap import Rewrite, compare
from opent5.xfile.stream import Chunk, ChunkLE, Platform

INLINE = 0xFFFFFFFF
#: A made-up numbering for the little-endian zone: stored -> registered (PS3) type.
LE_TYPES = {7: int(AssetType.LOCALIZE), 9: int(AssetType.RAWFILE)}
LE = Platform("test-le", "<", REGISTRY, LE_TYPES)


def make_zone(endian: str, assets: list[tuple[int, bytes]], platform=None) -> bytes:
    body = struct.pack(endian + "IIII", 0, 0, len(assets), INLINE)
    body += b"".join(struct.pack(endian + "II", t, INLINE) for t, _ in assets)
    body += b"".join(data for _, data in assets)
    content = bytes(36) + body
    x = parse(content, platform=platform)
    sizes = list(x.final_cursors)
    sizes[0] = x.temp_high_water + 16
    return struct.pack(endian + "9I", len(content) - 36, 0, *sizes) + body


def ptr(endian: str, block: int, offset: int) -> bytes:
    return struct.pack(endian + "I", ((block << 29) | offset) + 1)


def localize(endian: str, t: int, value: bytes, name: bytes, value_ptr: bytes | None = None):
    inline = struct.pack(endian + "I", INLINE)
    data = (value_ptr or inline) + inline
    if value_ptr is None:
        data += value + b"\0"
    return t, data + name + b"\0"


def rawfile(endian: str, t: int, name: bytes, buffer: bytes):
    head = struct.pack(endian + "IiI", INLINE, len(buffer) - 1, INLINE)
    return t, head + name + b"\0" + buffer


def zone(endian: str) -> bytes:
    """Four assets: a localize, a rawfile, and two localizes whose values are offset
    pointers (to the first value and into the rawfile buffer)."""
    loc, raw = (25, 38) if endian == ">" else (7, 9)
    platform = None if endian == ">" else LE
    return make_zone(
        endian,
        [
            localize(endian, loc, b"abc", b"A"),
            rawfile(endian, raw, b"r", b"hello world\0"),
            localize(endian, loc, b"", b"B", ptr(endian, 4, 0x24)),
            localize(endian, loc, b"", b"C", ptr(endian, 4, 0x30 + 2)),
        ],
        platform,
    )


def summary(x):
    return [(a.type, a.name, a.file_end - a.file_start, a.cursors_after) for a in x.assets]


def test_chunk_byte_orders():
    data = memoryview(bytes.fromhex("0102030405060708"))
    be, le = Chunk(data, 0, 4, 0), ChunkLE(data, 0, 4, 0)
    assert be.u16(0) == 0x0102 and le.u16(0) == 0x0201
    assert be.u32(4) == 0x05060708 and le.u32(4) == 0x08070605
    assert be.s16(0) == 0x0102 and le.s32(0) == 0x04030201
    assert le.sub(4, 4).u32(0) == 0x08070605  # sub keeps the byte order
    assert isinstance(le.sub(0, 2), ChunkLE)


def test_platform_rejects_unknown_endian():
    with pytest.raises(ValueError, match="endian"):
        Platform("x", "=", REGISTRY)


def test_little_endian_zone_parses_like_the_big_endian_one():
    be, le = zone(">"), zone("<")
    xb, xl = parse(be), parse(le, platform=LE)
    assert xb.problems() == [] and xl.problems() == []
    assert xb.platform is PS3 and xl.platform is LE
    assert summary(xb) == summary(xl)
    values = [a.data.get("value") for a in xl.assets if a.type == AssetType.LOCALIZE]
    assert values == ["abc", "A", None]  # the third points into the rawfile buffer
    assert values == [a.data.get("value") for a in xb.assets if a.type == AssetType.LOCALIZE]
    assert xl.header.block_sizes == xb.header.block_sizes


def test_little_endian_zone_writes_back_identically():
    le = zone("<")
    assert write(parse(le, platform=LE)).content == le
    assert Rewrite(le, platform=LE).build(check=True).content == le


def test_little_endian_rewrite_moves_pointers_and_stays_little_endian():
    le = zone("<")
    rw = Rewrite(le, platform=LE)
    rw.xfile.assets[0].data["name"] = "Abcdefgh"  # inline string grows by 7
    out = rw.build(check=True).content
    before, after = parse(le, platform=LE), parse(out, platform=LE)
    assert compare(before, after) == []
    assert after.assets[2].data["value"] == "Abcdefgh"  # still points at that string
    assert struct.unpack_from("<I", out, 0)[0] == len(out) - 36


def test_default_parse_of_a_little_endian_zone_fails_cleanly():
    with pytest.raises(Exception, match="Load_Stream of 536870912 bytes"):
        parse(zone("<"))  # the asset count read big-endian


def test_pc_type_numbers():
    assert [pcmod.pc_to_ps3_type(t) for t in (0, 6, 7, 12, 13, 17, 36, 41, 42)] == [
        0,
        6,
        9,
        14,
        15,
        19,
        38,
        43,
        45,
    ]
    with pytest.raises(pcmod.PCZoneError):
        pcmod.pc_to_ps3_type(43)


def test_pc_container():
    content = b"zone bytes" * 10
    data = b"IWffu100" + struct.pack("<I", 473) + zlib.compress(content)
    assert pcmod.read_pc_fastfile(data) == content
    with pytest.raises(pcmod.PCZoneError, match="magic"):
        pcmod.read_pc_fastfile(b"IWff0100" + data[8:])
    with pytest.raises(pcmod.PCZoneError, match="version"):
        pcmod.read_pc_fastfile(data[:8] + struct.pack("<I", 1) + data[12:])
    with pytest.raises(pcmod.PCZoneError, match="ends early"):
        pcmod.read_pc_fastfile(data[:-6])
