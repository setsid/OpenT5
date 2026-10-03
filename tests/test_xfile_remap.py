"""The remap: resize stream data, re-lay out block memory, remap offset pointers.

Synthetic zones check the arithmetic; the zone tests reproduce the two hardware
test zones built and validated by the emulator oracle (tools/remap_oracle.py)
byte for byte, then resize random strings of three types in real zones and
reparse each result (exact walk, same pointer targets).
"""

import hashlib
import json
import random
import struct
from pathlib import Path

import pytest

from opent5 import env
from opent5.container.zone import Zone
from opent5.xfile import AssetType, parse
from opent5.xfile.events import EventKind
from opent5.xfile.remap import Edit, RemapError, compare, logical_targets, remap, string_edit

FIXTURES = Path(__file__).parent / "fixtures"
INLINE = b"\xff\xff\xff\xff"


# -- synthetic zones -----------------------------------------------------------------------


def make_zone(assets: list[tuple[int, bytes]]) -> bytes:
    """A zone with the given (type, stream bytes) assets and a correct header."""
    body = struct.pack(">IIII", 0, 0, len(assets), 0xFFFFFFFF)
    body += b"".join(struct.pack(">II", t, 0xFFFFFFFF) for t, _ in assets)
    body += b"".join(data for _, data in assets)
    content = bytes(36) + body
    x = parse(content)
    sizes = list(x.final_cursors)
    sizes[0] = x.temp_high_water + 16
    return struct.pack(">9I", len(content) - 36, 0, *sizes) + body


def offset_ptr(block: int, offset: int) -> bytes:
    return struct.pack(">I", ((block << 29) | offset) + 1)


def localize(value: bytes, name: bytes, value_ptr: bytes = INLINE) -> tuple[int, bytes]:
    data = value_ptr + INLINE
    if value_ptr == INLINE:
        data += value + b"\0"
    return AssetType.LOCALIZE, data + name + b"\0"


def rawfile(name: bytes, buffer: bytes) -> tuple[int, bytes]:
    head = INLINE + struct.pack(">i", len(buffer) - 1) + INLINE
    return AssetType.RAWFILE, head + name + b"\0" + buffer


def synthetic() -> bytes:
    # VIRTUAL: the asset array (4 x 8) @0, "abc\0" @0x20, "A\0" @0x24, rawfile name
    # "r\0" @0x26, buffer (align 16) @0x30, then a localize whose value points at "A"
    # and one whose value points 2 bytes into the buffer.
    return make_zone(
        [
            localize(b"abc", b"A"),
            rawfile(b"r", b"hello world\0"),
            localize(b"", b"B", offset_ptr(4, 0x24)),
            localize(b"", b"C", offset_ptr(4, 0x30 + 2)),
        ]
    )


def test_synthetic_zone_parses_exactly():
    x = parse(synthetic())
    assert x.problems() == []


@pytest.mark.parametrize("grow", [-3, -1, 1, 2, 9, 10, 11, 12, 16, 17, 100, 128])
def test_string_resize_moves_later_data_and_pointers(grow):
    content = synthetic()
    x = parse(content)
    value_at = content.index(b"abc\0")
    text = b"abc"[: max(0, 3 + grow)] if grow < 0 else b"abc" + b"x" * grow
    r = remap(x, content, [string_edit(content, value_at, text)], check=True)
    y = parse(r.content)
    assert y.problems() == []
    assert compare(x, y) == []
    # "A" moves by exactly the string's change; the buffer by that rounded to 16.
    a_new = 0x24 + grow
    buf_new = (a_new + 2 + 2 + 15) & ~15
    rows = y.log.of_kind(EventKind.POINTER)
    targets = {int(r[5]) for r in rows if r[3] == 3}
    assert targets == {a_new, buf_new + 2}
    assert r.header_after[2 + 4] == buf_new + 12 + 4  # buffer, then "B\0" "C\0"
    assert r.header_after[0] == len(r.content) - 36


def test_rawfile_buffer_resize_updates_len():
    content = synthetic()
    x = parse(content)
    at = content.index(b"hello world\0")
    r = remap(x, content, [Edit(at + 5, 6, b", wider world")], check=True)
    y = parse(r.content)
    raw = next(a for a in y.assets if a.type == AssetType.RAWFILE)
    assert raw.data.length == len(b"hello, wider world")
    assert raw.data.buffer == b"hello, wider world\0"
    assert r.fields and r.fields[0][1:] == (11, 18)


def test_rejected_edits():
    content = synthetic()
    x = parse(content)
    at = content.index(b"abc\0")
    with pytest.raises(RemapError):
        remap(x, content, [Edit(at + 1, 1, b"\0x")])  # a second NUL in a string
    with pytest.raises(RemapError):
        remap(x, content, [Edit(at, 6, b"q")])  # crosses into the next read
    with pytest.raises(RemapError):
        remap(x, content, [Edit(at, 1, b"q"), Edit(at, 2, b"zz")])  # overlap
    with pytest.raises(RemapError):
        remap(x, content, [Edit(0x10, 1, b"qq")])  # the header


# -- real zones ----------------------------------------------------------------------------


def find_zone(name: str) -> Path | None:
    for key in ("OPENT5_ZONES", "OPENT5_PATCH_ZONES"):
        folder = env.path_of(key)
        if folder and (folder / f"{name}.ff").is_file():
            return folder / f"{name}.ff"
    return None


def open_zone(name: str):
    path = find_zone(name)
    if path is None:
        pytest.skip(f"{name}.ff is not on this machine")
    zone = Zone.open(path)
    content = bytes(zone.content)
    return zone, content, parse(content)


@pytest.mark.zones
@pytest.mark.parametrize(
    ("fixture", "length", "sha1"),
    [
        ("remap_code_post_gfx_mp.json", 15, "43ac232529ae3f337e73a16987c163f1faa1e88d"),
        pytest.param(
            "remap_code_post_gfx_mp_b2.json",
            140,
            "7e00990c394ee225716b90ef41419245d1332155",
            marks=pytest.mark.slow,
        ),
    ],
)
def test_reproduces_the_oracle_test_zones(fixture, length, sha1):
    """Test zones (b) and (b2) were laid out from the game's own loader (emulated);
    the product remap must give the same pointers, relayout and file."""
    want = json.loads((FIXTURES / fixture).read_text())
    zone, content, x = open_zone("code_post_gfx_mp")
    if len(content) != want["content_old_length"]:
        pytest.skip("a different code_post_gfx_mp.ff")
    text = b"OPENT5 REMAP OK".ljust(length, b" ")
    r = remap(x, content, [string_edit(content, want["string_file_offset"], text)])
    assert sorted(list(p) for p in r.pointers) == sorted(want["pointers"])
    assert r.runs(4) == want["relayout_runs"]
    assert r.header_after[2 + 4] == want["virtual_new_size"]
    assert len(r.content) == want["content_new_length"]
    zone.content[:] = r.content
    assert hashlib.sha1(zone.build().data).hexdigest() == sha1


def strings_of(x, asset_type: int, skip_first: bool) -> list[tuple[int, int]]:
    """(file offset, length without NUL) of inline VIRTUAL strings in assets of a type;
    skip_first drops each asset's first string (its name)."""
    rows = x.log.table()
    starts = [a.event_start for a in x.assets] + [len(rows)]
    out = []
    for n, a in enumerate(x.assets):
        if a.type != asset_type:
            continue
        seg = rows[starts[n] : starts[n + 1]]
        found = seg[(seg[:, 0] == EventKind.STRING) & (seg[:, 3] == 4)]
        for k, row in enumerate(found):
            if skip_first and k == 0:
                continue
            out.append((int(row[1]), int(row[2]) - 1))
    return out


DELTAS = [-5, -2, -1, 1, 3, 4, 5, 8, 13, 16, 17, 127, 128, 129, 300]


def resized(content: bytes, at: int, length: int, delta: int) -> Edit:
    old = content[at : at + length]
    if delta < 0:
        return Edit(at, length, old[: max(0, length + delta)])
    return Edit(at, length, old + b"#" * delta)


_SLOW = pytest.mark.slow
CASES = [
    pytest.param("code_post_gfx_mp", AssetType.LOCALIZE, False, marks=_SLOW),
    pytest.param("code_post_gfx_mp", AssetType.RAWFILE, False, marks=_SLOW),
    pytest.param("code_post_gfx_mp", AssetType.STRINGTABLE, True, marks=_SLOW),
    ("patch_mp", AssetType.LOCALIZE, False),
    pytest.param("patch_mp", AssetType.RAWFILE, False, marks=_SLOW),
    pytest.param("patch_mp", AssetType.STRINGTABLE, True, marks=_SLOW),
]


@pytest.mark.zones
@pytest.mark.parametrize(("zone_name", "asset_type", "cells"), CASES)
def test_random_string_resizes_reparse_exactly(zone_name, asset_type, cells):
    _, content, x = open_zone(zone_name)
    pool = [s for s in strings_of(x, asset_type, cells) if s[1] >= 5]
    rng = random.Random(f"{zone_name}/{asset_type}")
    targets = logical_targets(x)
    for delta in rng.sample(DELTAS, 4):
        at, length = rng.choice(pool)
        r = remap(x, content, [resized(content, at, length, delta)])
        y = parse(r.content)
        assert compare(x, y, targets) == [], (zone_name, hex(at), delta)
        assert len(r.content) == len(content) + delta


@pytest.mark.zones
@pytest.mark.parametrize(
    "zone_name", [pytest.param("code_post_gfx_mp", marks=pytest.mark.slow), "patch_mp"]
)
def test_several_edits_at_once(zone_name):
    _, content, x = open_zone(zone_name)
    rng = random.Random(zone_name)
    picks = []
    for asset_type, cells in (
        (AssetType.LOCALIZE, False),
        (AssetType.RAWFILE, False),
        (AssetType.STRINGTABLE, True),
    ):
        pool = [s for s in strings_of(x, asset_type, cells) if s[1] >= 5]
        picks += rng.sample(pool, 2)
    edits = [resized(content, at, length, rng.choice(DELTAS)) for at, length in sorted(set(picks))]
    r = remap(x, content, edits)
    y = parse(r.content)
    assert compare(x, y) == []
    assert len(r.content) == len(content) + sum(e.delta for e in edits)
