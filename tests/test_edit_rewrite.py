"""The node rewrite (opent5.xfile.remap.Rewrite) under the editing layer, and the editing
layer on real zones.

Synthetic: an unedited rewrite is the identity; a resize gives exactly the splice remap's
result; element arrays can shrink and grow with pointers into them following their
elements; a pointer to a removed element is refused.

Zones: rows added to and removed from every stringtable of several zones, each result
reparsed exactly with every unchanged asset identical (or identical apart from remapped
pointers, each naming the same thing); the oracle test zone (b) reproduced byte for byte
through the rewrite; every rawfile recompressing to its stored bytes; the mp_nuked demo
edits (an entity moved by a +16-byte entity string, a texture replaced) verified.
"""

from __future__ import annotations

import hashlib
import json
import random
from pathlib import Path

import pytest

from opent5 import env
from opent5.container.zone import Zone
from opent5.edit import Document
from opent5.edit import content as ct
from opent5.edit.verify import verify_content
from opent5.xfile import AssetType, parse
from opent5.xfile.remap import RemapError, Rewrite, compare, remap, string_edit
from test_edit_document import ROWS, synthetic_content

FIXTURES = Path(__file__).parent / "fixtures"


# -- synthetic -------------------------------------------------------------------------------


def test_unedited_rewrite_is_the_identity():
    content = synthetic_content()
    rw = Rewrite(content)
    result = rw.build(check=True)
    assert result.content == content
    assert result.pointers == []


@pytest.mark.parametrize("grow", [-4, -1, 1, 3, 15, 16, 17, 129])
def test_resize_matches_the_splice_remap(grow):
    content = synthetic_content()
    rw = Rewrite(content)
    node = rw.xfile.assets[0].data  # "Hello", the target of asset 1's pointer
    event = rw.event_of(("S", id(node), "value"))
    at = int(rw.xfile.log.table()[event, 1])
    text = "Hello"[: 5 + grow] if grow < 0 else "Hello" + "#" * grow
    node["value"] = text
    built = rw.build(check=True)
    spliced = remap(parse(content), content, [string_edit(content, at, text)])
    assert built.content == spliced.content
    assert sorted(built.pointers) == sorted(
        (at_ + (grow if at_ > at else 0), was, now) for at_, was, now in spliced.pointers
    )
    assert compare(parse(content), parse(built.content)) == []


def test_removed_elements_and_pointers_into_arrays():
    content = synthetic_content()
    rw = Rewrite(content)
    table = rw.xfile.assets[4].data
    cells = table["cells"]
    # Cell 4 points at cell 0's string; removing row 1 (cells 2, 3) keeps both.
    table["cells"] = cells[:2] + cells[4:]
    table["header"] = ct.table_header(table, 2)
    table["cell_index"] = ct.resort_index(table, keep_order=False)
    y = parse(rw.build(check=True).content)
    assert ct.table_rows(y.assets[4].data) == [["alpha", "1"], ["alpha", "3"]]


def test_a_pointer_to_a_removed_string_is_refused():
    content = synthetic_content()
    rw = Rewrite(content)
    table = rw.xfile.assets[4].data
    table["cells"] = table["cells"][2:]  # row 0 goes; cell 4 still points at its string
    table["header"] = ct.table_header(table, 2)
    table["cell_index"] = ct.resort_index(table, keep_order=False)
    with pytest.raises(RemapError, match="which the edit removed"):
        rw.build()


def test_the_document_copies_a_string_before_removing_it(tmp_path):
    from test_edit_document import synthetic_ff

    path = tmp_path / "z.ff"
    path.write_bytes(synthetic_ff())
    doc = Document.open(path)
    doc.remove_row(4, 0)
    assert "own copy" in doc.changes()[0].detail
    assert Document.open(doc.save(tmp_path / "out.ff").path).table(4) == [
        [c.decode() for c in r] for r in ROWS[1:]
    ]


# -- zones -----------------------------------------------------------------------------------


def find_zone(name: str) -> Path | None:
    for key in ("OPENT5_ZONES", "OPENT5_PATCH_ZONES"):
        folder = env.path_of(key)
        if folder and (folder / f"{name}.ff").is_file():
            return folder / f"{name}.ff"
    return None


def open_doc(name: str) -> Document:
    path = find_zone(name)
    if path is None:
        pytest.skip(f"{name}.ff is not on this machine")
    return Document.open(path)


def edit_every_table(doc: Document, seed: str) -> int:
    rng = random.Random(seed)
    n = 0
    for a in doc.assets:
        if a.type != AssetType.STRINGTABLE or not isinstance(doc.xfile.assets[a.index].data, dict):
            continue
        rows = doc.table(a.index)
        if not rows:
            continue
        doc.remove_row(a.index, rng.randrange(len(rows)))
        width = len(rows[0])
        doc.add_row(a.index, [f"opent5 {k}" for k in range(width)], rng.randrange(len(rows)))
        doc.set_cell(a.index, 0, rng.randrange(width), "x" * rng.randrange(1, 40))
        n += 1
    return n


_SLOW = pytest.mark.slow


@pytest.mark.zones
@pytest.mark.parametrize(
    "zone",
    [
        "patch_mp",
        pytest.param("code_post_gfx_mp", marks=_SLOW),
        pytest.param("mp_nuked", marks=_SLOW),
    ],
)
def test_rows_added_and_removed_in_every_table(zone):
    doc = open_doc(zone)
    tables = edit_every_table(doc, zone)
    assert tables > 0
    problems, details = verify_content(doc, doc.build())
    assert problems == []
    assert details["identical"] + details["identical_after_pointer_remap"] + len(
        doc._touched()
    ) == len(doc.assets)
    assert details["edited_read_back"] == tables


@pytest.mark.zones
@pytest.mark.parametrize("zone", ["patch_mp", pytest.param("code_post_gfx_mp", marks=_SLOW)])
def test_every_rawfile_recompresses_to_its_bytes(zone):
    doc = open_doc(zone)
    seen = 0
    for a in doc.xfile.assets:
        if a.type == AssetType.RAWFILE and isinstance(a.data, dict) and a.data.get("buffer"):
            text = ct.rawfile_text(a.data)
            assert ct.rawfile_bytes(a.data, text) == (
                bytes(a.data["header"]),
                bytes(a.data["buffer"]),
            )
            seen += 1
    assert seen > 100


@pytest.mark.zones
@pytest.mark.slow
def test_reproduces_oracle_zone_b_through_the_rewrite():
    """The hardware-tested zone (b), with the string shared as the original remap kept it."""
    want = json.loads((FIXTURES / "remap_code_post_gfx_mp.json").read_text())
    path = find_zone("code_post_gfx_mp")
    if path is None:
        pytest.skip("code_post_gfx_mp.ff is not on this machine")
    zone = Zone.open(path)
    rw = Rewrite(bytes(zone.content))
    if len(rw.content) != want["content_old_length"]:
        pytest.skip("a different code_post_gfx_mp.ff")
    rw.xfile.assets[4162].data["value"] = "OPENT5 REMAP OK"
    zone.content[:] = rw.build().content
    assert hashlib.sha1(zone.build().data).hexdigest() == "43ac232529ae3f337e73a16987c163f1faa1e88d"


@pytest.mark.zones
@pytest.mark.slow
def test_patch_mp_mixed_edits_save_and_verify(tmp_path):
    doc = open_doc("patch_mp")
    loc = [a.index for a in doc.assets if a.type == AssetType.LOCALIZE]
    raw = [a.index for a in doc.assets if a.type == AssetType.RAWFILE and a.name.endswith(".gsc")]
    doc.set_localize(loc[10], "OPENT5 " * 20)
    doc.set_text(raw[0], doc.text(raw[0]) + "\n// OpenT5\n")
    report = doc.save(tmp_path / "patch_mp.ff")
    assert report.problems == [] and report.verified
    back = Document.open(report.path)
    assert back.localize(loc[10])[1] == "OPENT5 " * 20
    assert back.text(raw[0]).endswith("// OpenT5\n")


@pytest.mark.zones
@pytest.mark.slow
def test_mp_nuked_demo_edits_verify(tmp_path):
    """The acceptance demos (docs/demo-mp_nuked.md): an entity moved through a +16-byte
    entity string edit, and the street-sign texture replaced."""
    import numpy as np

    doc = open_doc("mp_nuked")
    clip = doc.find("maps/mp/mp_nuked.d3dbsp", "col_map_mp").index
    old = '"model" "t5_veh_civ_tiara"\n"origin" "-59.5 804 -66"\n'
    new = '"model" "t5_veh_civ_tiara"\n"origin" "-59.500000 804.000000 184.000"\n'
    text = doc.text(clip)
    assert text.count(old) == 1
    doc.set_text(clip, text.replace(old, new))
    sign = doc.find("~-gus_art_streetsigns_c", "image").index
    pattern = np.zeros((64, 128, 4), np.uint8)
    pattern[:32] = (255, 255, 0, 255)
    pattern[32:, ::2] = (255, 0, 255, 255)
    doc.replace_image(sign, pattern)
    content = doc.build()
    header_before = doc.xfile.header.block_sizes
    problems, details = verify_content(doc, content)
    assert problems == []
    assert details["identical_after_pointer_remap"] > 0
    after = parse(content, log=False).header.block_sizes
    assert after[4] == header_before[4] + 16  # the VIRTUAL block grew with the string
