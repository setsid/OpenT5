"""The parser against the game's own loader, on the nine sample zones.

tests/fixtures/xfile_oracle.json holds, for every asset of nine zones, the file
span and the seven block positions before and after it, recorded while the
game's loader code (t5mp.elf Load_XAsset, run in a PowerPC interpreter) loaded
the zone; numbers only. Here the parser must reproduce all of them, end every
block at the header's size, and write every rawfile, stringtable and localize
asset back to its exact bytes.

Skipped unless .env points at the zones.
"""

import json
from pathlib import Path

import pytest

from opent5 import env
from opent5.container.zone import Zone
from opent5.xfile import REGISTRY, AssetType, Writer, parse
from opent5.xfile.events import EventKind, PtrKind

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "xfile_oracle.json").read_text())
ZONES = sorted(FIXTURE["zones"])
TIER1 = (AssetType.RAWFILE, AssetType.STRINGTABLE, AssetType.LOCALIZE)


def candidates(name: str) -> list[Path]:
    out = []
    for key in ("OPENT5_PATCH_ZONES", "OPENT5_ZONES"):
        folder = env.path_of(key)
        if folder:
            out.append(folder / f"{name}.ff")
    dlc = env.path_of("OPENT5_DLC_ZONES")
    if dlc:
        out.append(dlc / "english" / f"{name}.ff")
    return [p for p in out if p.is_file()]


@pytest.fixture(scope="module", params=ZONES)
def sample(request):
    """(name, content, parsed) for a sample zone whose length matches the fixture.
    Module scope with a parameter: pytest runs every test on one zone before
    loading the next, so each zone is inflated and parsed once."""
    name = request.param
    want = FIXTURE["zones"][name]["length"]
    for path in candidates(name):
        content = bytes(Zone.open(path).content)
        if len(content) == want:
            return name, content, parse(content)
    pytest.skip(f"{name}.ff with a {want}-byte stream is not on this machine")


def expected(name: str):
    """Rebuild (type, start, end, before, after) per asset from the delta-coded fixture."""
    z = FIXTURE["zones"][name]
    start, cursors = z["asset_data_start"], list(z["cursors_start"])
    out = []
    for i, (asset_type, size) in enumerate(zip(z["types"], z["sizes"], strict=True)):
        after = [c + d for c, d in zip(cursors, z["deltas"][7 * i : 7 * i + 7], strict=True)]
        out.append((asset_type, start, start + size, tuple(cursors), tuple(after)))
        start, cursors = start + size, after
    return out


pytestmark = pytest.mark.zones


def test_every_asset_matches_the_game_loader(sample):
    name, _, xfile = sample
    want = expected(name)
    assert len(xfile.assets) == len(want)
    for asset, (asset_type, start, end, before, after) in zip(xfile.assets, want, strict=True):
        got = (asset.type, asset.file_start, asset.file_end, asset.cursors_before)
        assert got == (asset_type, start, end, before), f"asset {asset.index}"
        assert asset.cursors_after == after, f"asset {asset.index} {asset.name}"


def test_the_walk_ends_where_the_header_says(sample):
    name, content, xfile = sample
    z = FIXTURE["zones"][name]
    assert xfile.problems() == []
    assert xfile.tail_offset == z["tail_start"]
    assert xfile.end_offset == len(content)
    assert list(xfile.final_cursors) == z["final_cursors"]
    assert list(xfile.final_cursors[1:]) == list(xfile.header.block_sizes[1:])


def test_tier1_assets_write_back_identically(sample):
    name, content, xfile = sample
    count = 0
    for asset in xfile.assets:
        if asset.type not in TIER1:
            continue
        writer = Writer()
        REGISTRY[asset.type].write(asset.data, writer)
        assert writer.getvalue() == content[asset.file_start : asset.file_end], asset.name
        count += 1
    assert count == sum(1 for t in FIXTURE["zones"][name]["types"] if t in TIER1)


def test_reads_tile_the_file(sample):
    """Every file byte is read exactly once: READ, STRING and TAIL events, sorted by
    file offset, cover 0x34 .. end with no gap or overlap."""
    _, content, xfile = sample
    table = xfile.log.table()
    kinds = table[:, 0]
    rows = table[
        (kinds == EventKind.STRING)
        | (kinds == EventKind.TAIL)
        | ((kinds == EventKind.READ) & (table[:, 1] != 0xFFFFFFFF))
    ]
    starts = rows[:, 1].astype("int64")
    ends = starts + rows[:, 2].astype("int64")
    order = starts.argsort()
    starts, ends = starts[order], ends[order]
    assert starts[0] == 0x34
    assert (starts[1:] == ends[:-1]).all()
    assert ends[-1] == len(content)


def test_offset_pointers_decode_into_their_blocks(sample):
    _, _, xfile = sample
    pointers = xfile.log.of_kind(EventKind.POINTER)
    offsets = pointers[(pointers[:, 3] == PtrKind.OFFSET) | (pointers[:, 3] == PtrKind.ALIAS_REF)]
    assert len(offsets) > 0
    sizes = xfile.header.block_sizes
    for block in set(offsets[:, 4].tolist()):
        inside = offsets[offsets[:, 4] == block][:, 5]
        assert int(inside.max()) < sizes[block]
