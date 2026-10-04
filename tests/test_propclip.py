"""Axis-aligned clip cbrushes for the map editor (opent5.convert.propclip).

Fast tests (no zones): the pure cbrush / vert / node builders, a cbrush byte for
byte against a recorded cod2map clip box, and add / move / remove / footprint /
cluster logic on a hand-built synthetic clipMap node.

Zone tests (marked ``zones``): on a real PS3 clipMap, a clip box added through
the node rewrite reparses exactly and is reachable from the leaf(s) it was
attached to, an added cbrush is byte-identical in shape to a cod2map axial
brush, and a stock clip cluster moves and is disabled with the zone still exact.
The emulated loader (oracle) is a slow, ELF-gated check.
"""

from __future__ import annotations

import os
import struct
from pathlib import Path

import pytest

from opent5 import env
from opent5.container.zone import Zone
from opent5.convert import propclip as pc
from opent5.xfile import AssetType, parse
from opent5.xfile.remap import Rewrite

# -- fast: pure builders ---------------------------------------------------------------------

#: n_box_all2 brush 0 (a device-proven v0.2.0 clip box), its first 0x54 bytes:
#: mins (266,-458,0), contents 0x8030200, maxs (334,-438,26), numsides 0, sides 0,
#: six axial cflags 0x8030200, six axial sflags 0x440A0. The verts tail (0x54..0x60)
#: is left out: cod2map points it at eight box-corner verts, an added brush does not.
COD2MAP_BRUSH0_HEAD = bytes.fromhex(
    "43850000c3e5000000000000"  # mins
    "08030200"  # contents
    "43a70000c3db000041d00000"  # maxs
    "00000000"  # numsides
    "00000000"  # sides
    "080302000803020008030200080302000803020008030200"  # axial cflags[6]
    "000440a0000440a0000440a0000440a0000440a0000440a0"  # axial sflags[6]
)


def test_clip_cbrush_matches_cod2map_box():
    out = pc.clip_cbrush((266.0, -458.0, 0.0), (334.0, -438.0, 26.0))
    assert len(out) == pc.CBRUSH_SIZE
    assert out[:0x54] == COD2MAP_BRUSH0_HEAD
    # an added brush carries no verts (NULL run)
    assert struct.unpack_from(">I", out, pc._B_NUMVERTS)[0] == 0
    assert struct.unpack_from(">I", out, pc._B_VERTS)[0] == 0


def test_clip_cbrush_fields_round_trip():
    out = pc.clip_cbrush((1.0, 2.0, 3.0), (4.0, 5.0, 6.0), contents=0x1, surface_flags=0x44)
    assert struct.unpack_from(">3f", out, pc._B_MINS) == (1.0, 2.0, 3.0)
    assert struct.unpack_from(">3f", out, pc._B_MAXS) == (4.0, 5.0, 6.0)
    assert struct.unpack_from(">i", out, pc._B_CONTENTS)[0] == 0x1
    assert struct.unpack_from(">I", out, pc._B_NUMSIDES)[0] == 0
    assert struct.unpack_from(">I", out, pc._B_SIDES)[0] == 0
    for k in range(6):
        assert struct.unpack_from(">i", out, pc._B_AXIAL_CFLAGS + 4 * k)[0] == 0x1
        assert struct.unpack_from(">i", out, pc._B_AXIAL_SFLAGS + 4 * k)[0] == 0x44


def test_clip_cbrush_rejects_inverted_bounds():
    with pytest.raises(pc.ClipError):
        pc.clip_cbrush((0.0, 0.0, 0.0), (10.0, -1.0, 10.0))


def test_box_corner_verts():
    v = pc.box_corner_verts((0.0, 0.0, 0.0), (2.0, 4.0, 8.0))
    corners = [struct.unpack_from(">3f", v, 12 * i) for i in range(8)]
    assert len(corners) == 8
    assert set(corners) == {
        (x, y, z) for x in (0.0, 2.0) for y in (0.0, 4.0) for z in (0.0, 8.0)
    }


def test_boxes_overlap():
    assert pc.boxes_overlap((0, 0, 0), (1, 1, 1), (1, 1, 1), (2, 2, 2))  # touching
    assert not pc.boxes_overlap((0, 0, 0), (1, 1, 1), (2, 2, 2), (3, 3, 3))
    assert not pc.boxes_overlap((0, 0, 0), (1, 1, 1), (0, 0, 5), (1, 1, 6))  # z apart


def test_inline_leaf_node():
    n = pc.inline_leaf_node(0x8030200, [3, 7])
    assert struct.unpack_from(">h", n["raw"], pc._N_COUNT)[0] == 2
    assert struct.unpack_from(">I", n["raw"], pc._N_DATA)[0] == 0xFFFFFFFF
    assert n["brushes"] == struct.pack(">HH", 3, 7)
    with pytest.raises(pc.ClipError):
        pc.inline_leaf_node(0, [])


# -- fast: a synthetic clipMap node ----------------------------------------------------------


def synthetic_clipmap(leaf_boxes):
    """A minimal clipMap node: one empty kd-tree node, the given leaf boxes all
    rooted at it, and no brushes."""
    header = bytearray(0x14C)
    struct.pack_into(">I", header, 0x30, len(leaf_boxes))  # numLeafs
    struct.pack_into(">I", header, 0x38, 1)  # leafbrushNodesCount
    leafs = bytearray()
    for lo, hi in leaf_boxes:
        rec = bytearray(pc.CLEAF_SIZE)
        struct.pack_into(">3f", rec, pc._L_MINS, *lo)
        struct.pack_into(">3f", rec, pc._L_MAXS, *hi)
        struct.pack_into(">i", rec, pc._L_LEAFBRUSHNODE, 0)
        leafs += rec
    empty = {"_t": "cLeafBrushNode_s", "raw": bytes(pc.CLEAFBRUSHNODE_SIZE), "brushes": None}
    return {
        "_t": "clipMap_t",
        "header": bytes(header),
        "brushes": b"",
        "leafs": bytes(leafs),
        "leafbrush_nodes": [empty],
        "leafbrushes": b"",
        "brush_verts": b"",
    }


def test_synthetic_add_attaches_and_is_reachable():
    cm = pc.ClipMap(synthetic_clipmap([((-100, -100, -100), (100, 100, 100)),
                                       ((200, 200, 200), (300, 300, 300))]))
    idx = pc.add_clip(cm, (0.0, 0.0, 0.0), (10.0, 10.0, 10.0))
    assert idx == 0
    assert cm.num_brushes == 1
    assert cm.reachable_brushes(0) == {0}  # overlaps leaf 0
    assert cm.reachable_brushes(1) == set()  # not leaf 1


def test_synthetic_second_add_extends_the_same_leaf():
    cm = pc.ClipMap(synthetic_clipmap([((-100, -100, -100), (100, 100, 100))]))
    pc.add_clip(cm, (0.0, 0.0, 0.0), (10.0, 10.0, 10.0))
    pc.add_clip(cm, (1.0, 1.0, 1.0), (5.0, 5.0, 5.0))
    assert cm.num_brushes == 2
    assert cm.reachable_brushes(0) == {0, 1}
    assert cm.lbn_count == 2  # one new inline node, shared by both brushes


def test_synthetic_move_reattaches_across_leaves():
    cm = pc.ClipMap(synthetic_clipmap([((-100, -100, -100), (100, 100, 100)),
                                       ((200, 200, 200), (300, 300, 300))]))
    idx = pc.add_clip(cm, (0.0, 0.0, 0.0), (10.0, 10.0, 10.0))
    pc.move_clip(cm, idx, (250.0, 250.0, 250.0), (260.0, 260.0, 260.0))
    assert cm.brush(idx).mins == (250.0, 250.0, 250.0)
    assert cm.reachable_brushes(0) == set()  # detached from leaf 0
    assert cm.reachable_brushes(1) == {idx}  # attached to leaf 1


def test_synthetic_remove_renumbers():
    cm = pc.ClipMap(synthetic_clipmap([((-100, -100, -100), (100, 100, 100))]))
    pc.add_clip(cm, (0.0, 0.0, 0.0), (10.0, 10.0, 10.0))  # brush 0
    pc.add_clip(cm, (1.0, 1.0, 1.0), (5.0, 5.0, 5.0))  # brush 1
    pc.remove_clip(cm, 0)
    assert cm.num_brushes == 1
    # the surviving brush (was 1) is renumbered to 0 and still reachable
    assert cm.reachable_brushes(0) == {0}
    assert cm.brush(0).mins == (1.0, 1.0, 1.0)


def test_synthetic_footprint_and_cluster():
    cm = pc.ClipMap(synthetic_clipmap([((-100, -100, -100), (100, 100, 100))]))
    a = pc.add_clip(cm, (0.0, 0.0, 0.0), (10.0, 10.0, 10.0))
    b = pc.add_clip(cm, (2.0, 2.0, 2.0), (6.0, 6.0, 6.0))
    pc.add_clip(cm, (50.0, 50.0, 0.0), (60.0, 60.0, 10.0))  # outside the footprint
    inside = pc.clips_in_footprint(cm, (-1.0, -1.0, -1.0), (11.0, 11.0, 11.0), within=True)
    assert set(inside) == {a, b}
    pc.move_cluster(cm, [a, b], (0.0, 0.0, 20.0))
    assert cm.brush(a).mins == (0.0, 0.0, 20.0)
    assert cm.brush(b).mins == (2.0, 2.0, 22.0)


def test_synthetic_disable_zeroes_contents():
    cm = pc.ClipMap(synthetic_clipmap([((-100, -100, -100), (100, 100, 100))]))
    idx = pc.add_clip(cm, (0.0, 0.0, 0.0), (10.0, 10.0, 10.0))
    pc.disable_clip(cm, idx)
    assert cm.brush(idx).contents == 0
    assert cm.reachable_brushes(0) == {idx}  # still referenced, but non-solid


# -- zones -----------------------------------------------------------------------------------


def find_zone(name: str) -> Path | None:
    for key in ("OPENT5_ZONES", "OPENT5_PATCH_ZONES"):
        folder = env.path_of(key)
        if folder and (folder / f"{name}.ff").is_file():
            return folder / f"{name}.ff"
    return None


def clipmap_node(xf) -> dict:
    assets = [a for a in xf.assets if a.type in (AssetType.COL_MAP_MP, AssetType.COL_MAP_SP)]
    return assets[0].data


def open_clip(name: str):
    path = find_zone(name)
    if path is None:
        pytest.skip(f"{name}.ff is not on this machine")
    return bytes(Zone.open(path).content)


@pytest.mark.zones
@pytest.mark.slow
def test_stock_add_clip_round_trips_and_is_reachable():
    """The required stock test: add a clip box to a stock clipMap, rewrite, and
    the zone reparses exactly with the brush reachable from the leaves it was
    attached to."""
    content = open_clip("mp_nuked")
    rw = Rewrite(content)
    cm = pc.ClipMap(clipmap_node(rw.xfile))
    before = cm.num_brushes
    idx = pc.add_clip(cm, (-20.0, -20.0, 40.0), (20.0, 20.0, 120.0))
    result = rw.build(check=True)
    back = parse(result.content)
    assert back.problems() == []
    cmb = pc.ClipMap(clipmap_node(back))
    assert cmb.num_brushes == before + 1
    reaching = [L for L in range(cmb.num_leafs) if idx in cmb.reachable_brushes(L)]
    assert reaching, "added clip is reachable from no leaf"


@pytest.mark.zones
@pytest.mark.slow
def test_stock_added_cbrush_is_byte_identical_to_an_axial_brush():
    content = open_clip("mp_nuked")
    cm = pc.ClipMap(clipmap_node(parse(content)))
    axial = next(i for i in range(cm.num_brushes) if cm.brush(i).numsides == 0)
    b = cm.brush(axial)
    built = pc.clip_cbrush(b.mins, b.maxs, b.contents, pc._surface_of(cm, axial))
    orig = cm.node["brushes"][axial * pc.CBRUSH_SIZE : axial * pc.CBRUSH_SIZE + 0x54]
    assert built[:0x54] == orig


@pytest.mark.zones
@pytest.mark.slow
def test_stock_move_and_disable_cluster_round_trip():
    content = open_clip("mp_nuked")
    rw = Rewrite(content)
    cm = pc.ClipMap(clipmap_node(rw.xfile))
    axial = [i for i in range(min(cm.num_brushes, 300)) if cm.brush(i).numsides == 0][:3]
    before = {i: cm.brush(i).mins for i in axial}
    pc.move_cluster(cm, axial, (0.0, 0.0, 64.0))
    pc.disable_clip(cm, axial[0])
    result = rw.build(check=True)
    back = parse(result.content)
    assert back.problems() == []
    cmb = pc.ClipMap(clipmap_node(back))
    assert cmb.brush(axial[1]).mins == pc._add3(before[axial[1]], (0.0, 0.0, 64.0))
    assert cmb.brush(axial[0]).contents == 0


@pytest.mark.zones
@pytest.mark.slow
@pytest.mark.skipif(not os.environ.get("OPENT5_ELF") and not env.path_of("OPENT5_ELF"),
                    reason="the oracle needs t5mp.elf (OPENT5_ELF)")
def test_stock_add_clip_passes_the_oracle(tmp_path):
    """The emulated game loader accepts a stock zone with a clip added: consumes
    it exactly, every block ends at the header, and every offset / alias pointer
    the product parser reads is converted to the same value, each inside its block."""
    import sys

    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root / "tools"))
    sys.path.insert(0, str(root / "tools" / "loader_emu"))
    from opent5.xfile.events import EventKind, PtrKind
    from remap_oracle import TracingEmu

    content = open_clip("mp_nuked")
    rw = Rewrite(content)
    cm = pc.ClipMap(clipmap_node(rw.xfile))
    pc.add_clip(cm, (-20.0, -20.0, 40.0), (20.0, 20.0, 120.0))
    edited = rw.build(check=True).content

    tr = TracingEmu(edited)
    consumed = tr.run()
    assert consumed == len(edited)
    header = struct.unpack_from(">9I", edited, 0)
    final = tr.final_positions()
    blocks = list(header[2:9])
    assert all(final[b] == blocks[b] for b in range(1, 7)) and final[0] == 0
    x = parse(edited, log=True)
    product = {}
    for row in x.log.of_kind(EventKind.POINTER).tolist():
        _, at, raw, kind, _, _ = row
        if kind in (PtrKind.OFFSET, PtrKind.ALIAS_REF):
            product[at] = raw
    loader, outside = {}, 0
    for _fn, _addr, raw, fo in tr.convs:
        loader[fo] = raw
        v = (raw - 1) & 0xFFFFFFFF
        b, off = v >> 29, v & 0x1FFFFFFF
        if b > 6 or off >= max(blocks[b], 1):
            outside += 1
    assert set(product) == set(loader)
    assert all(loader[k] == product[k] for k in product)
    assert outside == 0
