"""Axis-aligned clip cbrushes for the map editor (opent5.convert.propclip).

Fast tests (no zones): the pure cbrush / vert / node builders, a cbrush byte for
byte against a recorded cod2map clip box, the kd-tree walker and the footprint
finder on a hand-built synthetic clipMap, and the world-leaf reference check
(which catches the inline-under-world-leaf structure that crashed the first
device build, p_propclip).

Zone tests (marked ``zones``): on a real PS3 clipMap, a clip added through the
node rewrite reparses exactly, is referenced through the flat leafBrushes pool
(as cod2map references world-leaf brushes, never inline), and passes the check;
move and remove likewise; and an added cbrush is byte-identical in shape to a
cod2map axial brush. The emulated loader (oracle) is a slow, ELF-gated check that
now also runs the world-leaf reference check.
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

#: n_box_all2 brush 0 (a device-proven v0.2.0 clip box), its first 0x54 bytes.
COD2MAP_BRUSH0_HEAD = bytes.fromhex(
    "43850000c3e5000000000000"  # mins (266, -458, 0)
    "08030200"  # contents
    "43a70000c3db000041d00000"  # maxs (334, -438, 26)
    "00000000"  # numsides
    "00000000"  # sides
    "080302000803020008030200080302000803020008030200"  # axial cflags[6]
    "000440a0000440a0000440a0000440a0000440a0000440a0"  # axial sflags[6]
)


def test_clip_cbrush_matches_cod2map_box():
    out = pc.clip_cbrush((266.0, -458.0, 0.0), (334.0, -438.0, 26.0))
    assert len(out) == pc.CBRUSH_SIZE
    assert out[:0x54] == COD2MAP_BRUSH0_HEAD


def test_clip_cbrush_fields():
    out = pc.clip_cbrush((1.0, 2.0, 3.0), (4.0, 5.0, 6.0), contents=0x1, surface_flags=0x44,
                         verts_ptr=0x1234, numverts=8)
    assert struct.unpack_from(">3f", out, pc._B_MINS) == (1.0, 2.0, 3.0)
    assert struct.unpack_from(">3f", out, pc._B_MAXS) == (4.0, 5.0, 6.0)
    assert struct.unpack_from(">i", out, pc._B_CONTENTS)[0] == 0x1
    assert struct.unpack_from(">I", out, pc._B_NUMSIDES)[0] == 0
    assert struct.unpack_from(">I", out, pc._B_SIDES)[0] == 0
    assert struct.unpack_from(">I", out, pc._B_NUMVERTS)[0] == 8
    assert struct.unpack_from(">I", out, pc._B_VERTS)[0] == 0x1234
    for k in range(6):
        assert struct.unpack_from(">i", out, pc._B_AXIAL_CFLAGS + 4 * k)[0] == 0x1
        assert struct.unpack_from(">i", out, pc._B_AXIAL_SFLAGS + 4 * k)[0] == 0x44


def test_clip_cbrush_rejects_inverted_bounds():
    with pytest.raises(pc.ClipError):
        pc.clip_cbrush((0.0, 0.0, 0.0), (10.0, -1.0, 10.0))


def test_box_corner_verts():
    v = pc.box_corner_verts((0.0, 0.0, 0.0), (2.0, 4.0, 8.0))
    corners = [struct.unpack_from(">3f", v, 12 * i) for i in range(8)]
    assert set(corners) == {(x, y, z) for x in (0.0, 2.0) for y in (0.0, 4.0) for z in (0.0, 8.0)}


def test_boxes_overlap():
    assert pc.boxes_overlap((0, 0, 0), (1, 1, 1), (1, 1, 1), (2, 2, 2))  # touching
    assert not pc.boxes_overlap((0, 0, 0), (1, 1, 1), (2, 2, 2), (3, 3, 3))
    assert not pc.boxes_overlap((0, 0, 0), (1, 1, 1), (0, 0, 5), (1, 1, 6))


def test_flat_leaf_node():
    n = pc.flat_leaf_node(0x8030200, 0x80001001, 2)
    assert struct.unpack_from(">h", n["raw"], pc._N_COUNT)[0] == 2
    assert struct.unpack_from(">I", n["raw"], pc._N_DATA)[0] == 0x80001001
    assert n["brushes"] is None
    with pytest.raises(pc.ClipError):
        pc.flat_leaf_node(0, 0, 0)


# -- fast: a synthetic clipMap (flat pool, no Rewrite) ---------------------------------------

#: A VIRTUAL-block base for the synthetic flat pool's pointers.
_POOL_MEM = 0x1000
_POOL_PTR = ((4 << pc._OFFSET_BLOCK_SHIFT) | _POOL_MEM) + 1


def synthetic(leaf_boxes, flat, nodes, roots):
    """Build a clipMap node by hand. ``flat`` is the leafBrushes pool (list of
    brush indices). ``nodes`` are (count, contents, data_or_inline) where
    data_or_inline is a pool byte-offset for a flat leaf, 'inline:[idx...]' for an
    inline leaf, or ('split', child0, child1) for a split. ``roots`` gives each
    leaf's root node index. Enough to exercise the walker, footprint and check."""
    inline_max = max((max(s[2]) for s in nodes if s[0] != "split" and isinstance(s[2], list)),
                     default=-1)
    nbrush = max(max(flat, default=-1), inline_max) + 1
    header = bytearray(0x14C)
    struct.pack_into(">H", header, pc._H_NUM_BRUSHES, nbrush)
    struct.pack_into(">I", header, pc._H_NUM_LEAFS, len(leaf_boxes))
    struct.pack_into(">I", header, pc._H_LBN_COUNT, len(nodes))
    struct.pack_into(">I", header, pc._H_NUM_LEAFBRUSHES, len(flat))
    leafs = bytearray()
    for (lo, hi), root in zip(leaf_boxes, roots, strict=True):
        rec = bytearray(pc.CLEAF_SIZE)
        struct.pack_into(">3f", rec, pc._L_MINS, *lo)
        struct.pack_into(">3f", rec, pc._L_MAXS, *hi)
        struct.pack_into(">i", rec, pc._L_LEAFBRUSHNODE, root)
        leafs += rec
    node_dicts = []
    for spec in nodes:
        r = bytearray(pc.CLEAFBRUSHNODE_SIZE)
        brushes = None
        if spec[0] == "split":
            _, c0, c1 = spec
            struct.pack_into(">h", r, pc._N_COUNT, 0)
            struct.pack_into(">H", r, pc._N_CHILD0, c0)
            struct.pack_into(">H", r, pc._N_CHILD1, c1)
        else:
            count, contents, data = spec
            struct.pack_into(">h", r, pc._N_COUNT, count)
            struct.pack_into(">i", r, pc._N_CONTENTS, contents)
            if isinstance(data, list):  # inline
                struct.pack_into(">I", r, pc._N_DATA, pc._PTR_INLINE)
                brushes = b"".join(struct.pack(">H", b) for b in data)
            else:  # pool byte offset
                ptr = ((4 << pc._OFFSET_BLOCK_SHIFT) | (_POOL_MEM + data)) + 1
                struct.pack_into(">I", r, pc._N_DATA, ptr)
        node_dicts.append({"_t": "cLeafBrushNode_s", "raw": bytes(r), "brushes": brushes})
    return {
        "_t": "clipMap_t",
        "header": bytes(header),
        "brushes": pc.clip_cbrush((0.0, 0.0, 0.0), (1.0, 1.0, 1.0)) * nbrush,
        "leafs": bytes(leafs),
        "leafbrush_nodes": node_dicts,
        "leafbrushes": b"".join(struct.pack(">H", b) for b in flat),
        "brush_verts": None,
    }


def test_walker_flat_split_inline():
    # leaf 0 -> split -> two flat leaves; leaf 1 -> one flat leaf; leaf 2 -> inline.
    flat = [5, 7, 9]  # pool entries at byte 0, 2, 4
    nodes = [
        ("split", 1, 2),          # 0
        (1, 0x1, 0),              # 1: flat, pool offset 0 -> brush 5
        (2, 0x1, 2),              # 2: flat, pool offset 2 -> brushes 7, 9
        (1, 0x1, 4),              # 3: flat, pool offset 4 -> brush 9
        (1, 0x1, [11]),           # 4: inline -> brush 11
    ]
    cm = pc.ClipMap(synthetic(
        [((0, 0, 0), (1, 1, 1)), ((0, 0, 0), (1, 1, 1)), ((0, 0, 0), (1, 1, 1))],
        flat, nodes, roots=[0, 3, 4],
    ))
    assert cm.reachable_brushes(0) == {5, 7, 9}
    assert cm.reachable_brushes(1) == {9}
    assert cm.reachable_brushes(2) == {11}


def test_check_flags_inline_under_world_leaf():
    # A single flat leaf (fine) vs an inline leaf under a world leaf (the bug).
    good = pc.ClipMap(synthetic([((0, 0, 0), (1, 1, 1))], [3], [(1, 0x1, 0)], roots=[0]))
    assert pc.check_world_leaf_refs(good) == []
    bad = pc.ClipMap(synthetic([((0, 0, 0), (1, 1, 1))], [3], [(1, 0x1, [3])], roots=[0]))
    problems = pc.check_world_leaf_refs(bad)
    assert problems and "inline" in problems[0]


def test_check_flags_out_of_pool_run():
    # A flat leaf node whose run runs off the end of the pool.
    bad = pc.ClipMap(synthetic([((0, 0, 0), (1, 1, 1))], [3], [(2, 0x1, 2)], roots=[0]))
    problems = pc.check_world_leaf_refs(bad)
    assert problems and "pool" in problems[0]


def _set_leaf_brush_contents(node: dict, leaf: int, contents: int) -> None:
    leafs = bytearray(node["leafs"])
    struct.pack_into(">i", leafs, leaf * pc.CLEAF_SIZE + pc._L_BRUSH_CONTENTS, contents)
    node["leafs"] = bytes(leafs)


def test_contents_mask_check_flags_a_non_subset_brush():
    # One flat leaf lists brush 0 (default contents 0x08030200). With the leaf and
    # node contents not covering those bits, a trace early-outs: flagged. With them
    # OR'd in, clean.
    node = synthetic([((0, 0, 0), (1, 1, 1))], [0], [(1, 0x1, 0)], roots=[0])
    bad = pc.ClipMap(node)
    problems = pc.check_leaf_contents_masks(bad)
    assert problems and "subset" in problems[0]
    # now make both masks cover the brush's contents
    _set_leaf_brush_contents(node, 0, pc.PLAYER_CLIP_CONTENTS)
    r = bytearray(node["leafbrush_nodes"][0]["raw"])
    struct.pack_into(">i", r, pc._N_CONTENTS, pc.PLAYER_CLIP_CONTENTS)
    node["leafbrush_nodes"][0]["raw"] = bytes(r)
    assert pc.check_leaf_contents_masks(pc.ClipMap(node)) == []


def test_clips_in_footprint():
    # three clip brushes; footprint contains the first two.
    node = synthetic([((0, 0, 0), (1, 1, 1))], [], [], roots=[0])
    node["brushes"] = (
        pc.clip_cbrush((0.0, 0.0, 0.0), (10.0, 10.0, 10.0))
        + pc.clip_cbrush((2.0, 2.0, 2.0), (6.0, 6.0, 6.0))
        + pc.clip_cbrush((50.0, 50.0, 0.0), (60.0, 60.0, 10.0))
    )
    h = bytearray(node["header"])
    struct.pack_into(">H", h, pc._H_NUM_BRUSHES, 3)
    node["header"] = bytes(h)
    cm = pc.ClipMap(node)
    assert cm.num_brushes == 3
    inside = pc.clips_in_footprint(cm, (-1.0, -1.0, -1.0), (11.0, 11.0, 11.0), within=True)
    assert set(inside) == {0, 1}
    overlap = pc.clips_in_footprint(cm, (-1.0, -1.0, -1.0), (11.0, 11.0, 11.0), within=False)
    assert set(overlap) == {0, 1}


# -- zones -----------------------------------------------------------------------------------


def find_zone(name: str) -> Path | None:
    for key in ("OPENT5_ZONES", "OPENT5_PATCH_ZONES"):
        folder = env.path_of(key)
        if folder and (folder / f"{name}.ff").is_file():
            return folder / f"{name}.ff"
    return None


def clipmap_node(xf) -> dict:
    return [a for a in xf.assets if a.type in (AssetType.COL_MAP_MP, AssetType.COL_MAP_SP)][0].data


def open_clip(name: str) -> bytes:
    path = find_zone(name)
    if path is None:
        pytest.skip(f"{name}.ff is not on this machine")
    return bytes(Zone.open(path).content)


@pytest.mark.zones
@pytest.mark.slow
def test_stock_add_clip_round_trips_pool_backed_and_checks_clean():
    """Add a clip to a stock clipMap, rewrite, and the zone reparses exactly with
    the brush referenced through the flat pool (not inline) and reachable from the
    leaves it was attached to; the world-leaf reference check is clean."""
    content = open_clip("mp_nuked")
    rw = Rewrite(content)
    cm = pc.ClipMap(clipmap_node(rw.xfile), rewrite=rw)
    before = cm.num_brushes
    idx = pc.add_clip(cm, (-20.0, -20.0, 40.0), (20.0, 20.0, 120.0))
    back = parse(rw.build(check=True).content)
    assert back.problems() == []
    cmb = pc.ClipMap(clipmap_node(back))
    assert cmb.num_brushes == before + 1
    assert pc.check_world_leaf_refs(cmb) == []
    reaching = [L for L in range(cmb.num_leafs) if idx in cmb.reachable_brushes(L)]
    assert reaching, "added clip is reachable from no leaf"
    # the added brush owns its verts, pointing into the brushVerts pool
    assert cmb.brush(idx).numverts == 8
    assert back.resolve(cmb.brush(idx).verts_ptr) is not None


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
def test_stock_move_and_remove_round_trip():
    content = open_clip("mp_nuked")
    rw = Rewrite(content)
    cm = pc.ClipMap(clipmap_node(rw.xfile), rewrite=rw)
    idx = pc.add_clip(cm, (-20.0, -20.0, 40.0), (20.0, 20.0, 120.0))
    pc.move_clip(cm, idx, (100.0, 100.0, 40.0), (140.0, 140.0, 120.0))
    other = pc.add_clip(cm, (-200.0, -200.0, 40.0), (-160.0, -160.0, 120.0))
    pc.remove_clip(cm, other)
    back = parse(rw.build(check=True).content)
    assert back.problems() == []
    cmb = pc.ClipMap(clipmap_node(back))
    assert pc.check_world_leaf_refs(cmb) == []
    assert cmb.brush(idx).mins == (100.0, 100.0, 40.0)
    assert cmb.brush(other).contents == 0  # removed = disabled in place


@pytest.mark.zones
@pytest.mark.slow
def test_stock_add_clip_bsp_is_reachable_from_its_centre_leaf():
    """A clip added with ``add_clip_bsp`` is referenced from the exact BSP leaf a
    trace descends to at its centre, so a trace there hits it. The leaf- and
    node-contents masks cover the clip's contents (``check_leaf_contents_masks``
    is clean), and the structure round-trips. ``add_clip`` (empty-leaf attachment)
    does not make the centre leaf reach the brush; the BSP variant does. This is
    the fix for the clip that was structurally valid on device yet never hit."""
    content = open_clip("mp_nuked")
    rw = Rewrite(content)
    node = clipmap_node(rw.xfile)
    cm = pc.ClipMap(node, rewrite=rw)
    loc = pc.BspLocator.from_xfile(rw.xfile, node)
    mins, maxs = (174.0, -77.0, 0.0), (234.0, -17.0, 120.0)
    centre = tuple((mins[k] + maxs[k]) / 2 for k in range(3))
    centre_leaf = loc.locate(centre)
    # the old empty-leaf attachment leaves the centre leaf not reaching the brush
    old_idx = pc.add_clip(cm, mins, maxs)
    assert old_idx not in cm.reachable_brushes(centre_leaf)
    # the BSP attachment does; add_clip_bsp also asserts this internally
    idx = pc.add_clip_bsp(cm, loc, mins, maxs)
    assert idx in cm.reachable_brushes(centre_leaf)
    back = parse(rw.build(check=True).content)
    assert back.problems() == []
    cmb = pc.ClipMap(clipmap_node(back))
    assert pc.check_world_leaf_refs(cmb) == []
    assert pc.check_leaf_contents_masks(cmb) == []
    assert idx in cmb.reachable_brushes(centre_leaf)


@pytest.mark.zones
@pytest.mark.slow
def test_stock_cluster_footprint_and_move():
    content = open_clip("mp_nuked")
    rw = Rewrite(content)
    cm = pc.ClipMap(clipmap_node(rw.xfile), rewrite=rw)
    a = pc.add_clip(cm, (0.0, 0.0, 40.0), (20.0, 20.0, 60.0))
    b = pc.add_clip(cm, (5.0, 5.0, 45.0), (15.0, 15.0, 55.0))
    cluster = pc.clips_in_footprint(cm, (-1.0, -1.0, 39.0), (21.0, 21.0, 61.0), within=True)
    assert a in cluster and b in cluster
    pc.move_cluster(cm, [a, b], (0.0, 0.0, 100.0))
    back = parse(rw.build(check=True).content)
    assert back.problems() == []
    cmb = pc.ClipMap(clipmap_node(back))
    assert pc.check_world_leaf_refs(cmb) == []
    assert cmb.brush(a).mins == (0.0, 0.0, 140.0)
    assert cmb.brush(b).mins == (5.0, 5.0, 145.0)


@pytest.mark.zones
@pytest.mark.slow
@pytest.mark.skipif(not os.environ.get("OPENT5_ELF") and not env.path_of("OPENT5_ELF"),
                    reason="the oracle needs t5mp.elf (OPENT5_ELF)")
def test_stock_add_clip_passes_the_oracle():
    """The emulated game loader accepts a stock zone with a clip added and the
    world-leaf reference check is clean. The check catches the inline-under-world-
    leaf structure that crashed p_propclip even though the loader accepts it."""
    import sys

    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root / "tools"))
    sys.path.insert(0, str(root / "tools" / "loader_emu"))
    from opent5.xfile.events import EventKind, PtrKind
    from remap_oracle import TracingEmu

    content = open_clip("mp_nuked")
    rw = Rewrite(content)
    cm = pc.ClipMap(clipmap_node(rw.xfile), rewrite=rw)
    pc.add_clip(cm, (-20.0, -20.0, 40.0), (20.0, 20.0, 120.0))
    edited = rw.build(check=True).content

    tr = TracingEmu(edited)
    assert tr.run() == len(edited)
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
    assert pc.check_world_leaf_refs(pc.ClipMap(clipmap_node(x))) == []
