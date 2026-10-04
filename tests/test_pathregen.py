"""Line-of-sight path-link regeneration for the map editor (opent5.edit.pathregen).

Fast tests (no zones): the segment-vs-box slab primitive, the LOS test on a
hand-built clipMap, byte-for-byte parity with ``pathlinks.generate`` when there is
no collision, the connectivity read-back, the single-component gate raising on a
corridor a wall splits, and a moved prop whose clip blocks the new link and frees
the old one.

Zone tests (marked ``zones``): on retail mp_nuked the LOS rejects a segment through
a stock solid brush, passes one in open air, and a whole-map relink stays one
connected component keeping almost every baked edge. On the editor bus-move build a
candidate link through the bus's NEW clip is rejected while the bus's OLD footprint
is now clear of the bus clip; the whole-map gate's outcome on that sparse converted
map is asserted and documented.
"""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from opent5 import env
from opent5.container.zone import Zone
from opent5.convert import pathlinks
from opent5.convert import propclip as pc
from opent5.edit import pathregen as pr
from opent5.xfile import AssetType, parse

# -- builders --------------------------------------------------------------------------------


PATHNODE_SIZE = 0x80  # the stock pathnode_t record


def node(x: float, y: float, z: float, ntype: int = 1) -> dict:
    raw = bytearray(PATHNODE_SIZE)
    struct.pack_into(">I", raw, pathlinks.TYPE_OFF, ntype)
    struct.pack_into(">3f", raw, pathlinks.ORIGIN_OFF, x, y, z)
    return {"_t": "pathnode_t", "raw": bytes(raw), "links": None}


def game_of(points: list[tuple[float, float, float]]) -> dict:
    """A GameWorldMp-shaped dict: the given standard nodes plus the 128 spare
    type-0 nodes a real PathData carries."""
    nodes = [node(*p) for p in points]
    nodes += [node(0, 0, 0, ntype=0) for _ in range(128)]
    return {"header": b"", "nodes": nodes}


def clipmap_of(boxes: list[tuple]) -> pc.ClipMap:
    """A minimal clipMap view holding just a brush array (mins, maxs, contents),
    enough for the brute-force (locator-less) LOS test."""
    header = bytearray(0x14C)
    struct.pack_into(">H", header, pc._H_NUM_BRUSHES, len(boxes))
    brushes = b"".join(pc.clip_cbrush(mn, mx, contents=ct) for mn, mx, ct in boxes)
    return pc.ClipMap({"_t": "clipMap_t", "header": bytes(header), "brushes": brushes})


# -- the slab primitive ----------------------------------------------------------------------


def test_segment_hits_box_crossing_grazing_miss():
    box_min, box_max = (0.0, 0.0, 0.0), (10.0, 10.0, 10.0)
    # straight through the middle
    assert pr._segment_hits_box((-5, 5, 5), (15, 5, 5), box_min, box_max)
    # ends outside, short of the box
    assert not pr._segment_hits_box((-5, 5, 5), (-1, 5, 5), box_min, box_max)
    # grazing the top face (coplanar, zero penetration) is not a crossing
    assert not pr._segment_hits_box((-5, 5, 10), (15, 5, 10), box_min, box_max)
    # parallel and clear of the box
    assert not pr._segment_hits_box((-5, 20, 5), (15, 20, 5), box_min, box_max)
    # a segment that starts inside is a hit
    assert pr._segment_hits_box((5, 5, 5), (50, 5, 5), box_min, box_max)


def test_segment_hits_box_radius_inflates():
    box_min, box_max = (0.0, 0.0, 0.0), (10.0, 10.0, 10.0)
    # passes 5 units clear of the box in Y
    assert not pr._segment_hits_box((-5, 15, 5), (15, 15, 5), box_min, box_max)
    # with a 6-unit hull it now clips the box
    assert pr._segment_hits_box((-5, 15, 5), (15, 15, 5), box_min, box_max, radius=(6, 6, 6))


# -- LOS on a hand-built clipMap -------------------------------------------------------------


def test_segment_blocked_by_solid_wall_and_clear_otherwise():
    cm = clipmap_of([((70.0, -50.0, -50.0), (80.0, 200.0, 100.0), pr.CONTENTS_SOLID)])
    # a line that crosses the wall's slab
    assert pr.segment_blocked(cm, (0, 0, 40), (150, 0, 40))
    # a line on the near side, clear of the wall
    assert not pr.segment_blocked(cm, (0, 0, 40), (0, 120, 40))


def test_block_mask_catches_player_clip_without_solid_bit():
    # a player-clip brush carries no CONTENTS_SOLID bit, yet the default mask blocks it.
    cm = clipmap_of([((70.0, -50.0, -50.0), (80.0, 200.0, 100.0), pc.PLAYER_CLIP_CONTENTS)])
    assert pc.PLAYER_CLIP_CONTENTS & pr.CONTENTS_SOLID == 0
    assert pr.segment_blocked(cm, (0, 0, 40), (150, 0, 40))
    # a narrower mask of just the solid bit misses it
    assert not pr.segment_blocked(cm, (0, 0, 40), (150, 0, 40), block_mask=pr.CONTENTS_SOLID)


def test_segment_blocking_brush_reports_index_and_only_filter():
    cm = clipmap_of([
        ((70.0, -50.0, -50.0), (80.0, 200.0, 100.0), pr.CONTENTS_SOLID),   # brush 0
        ((500.0, -50.0, -50.0), (510.0, 200.0, 100.0), pr.CONTENTS_SOLID),  # brush 1, elsewhere
    ])
    assert pr.segment_blocking_brush(cm, (0, 0, 40), (150, 0, 40)) == 0
    # restricting to brush 1 only, the same line is clear
    assert pr.segment_blocking_brush(cm, (0, 0, 40), (150, 0, 40), only={1}) is None


# -- parity with pathlinks (no regression of the baking) -------------------------------------


def test_regenerate_without_collision_matches_pathlinks_bytes():
    pts = [(c * 116.0, r * 116.0, 0.0) for r in range(5) for c in range(5)]
    a = game_of(pts)
    b = game_of(pts)
    ref = pathlinks.generate(a)
    out = pr.regenerate_links(b, clipmap=None)
    assert out["linked"] and out["components"] == 1
    assert out["nodes"] == ref["nodes"] and out["links"] == ref["links"]
    # byte-for-byte identical node records and link arrays
    for ea, eb in zip(a["nodes"], b["nodes"], strict=True):
        assert bytes(ea["raw"]) == bytes(eb["raw"])
        assert bytes(ea.get("links") or b"") == bytes(eb.get("links") or b"")


def test_regenerate_with_empty_clipmap_is_like_no_collision():
    pts = [(c * 116.0, r * 116.0, 0.0) for r in range(4) for c in range(4)]
    a = game_of(pts)
    b = game_of(pts)
    pr.regenerate_links(a, clipmap=None)
    pr.regenerate_links(b, clipmap=clipmap_of([]))  # a clipMap with no brushes
    for ea, eb in zip(a["nodes"], b["nodes"], strict=True):
        assert bytes(ea["raw"]) == bytes(eb["raw"])


# -- connectivity and the gate ---------------------------------------------------------------


def test_connected_components_and_report_read_back():
    g = game_of([(0, 0, 0), (116, 0, 0), (1000, 0, 0)])  # third node is far off
    pr.regenerate_links(g, clipmap=None, max_dist=200.0)
    report = pr.component_report(g)
    assert report["count"] == 2
    assert report["sizes"] == [2, 1]
    # the lone far node is the offending one
    assert len(report["offending"]) == 1


def test_wall_across_corridor_splits_graph_and_gate_raises():
    # two nodes each side of a wall; the only cross links cross it.
    pts = [(0.0, 0.0, 0.0), (0.0, 120.0, 0.0), (150.0, 0.0, 0.0), (150.0, 120.0, 0.0)]
    wall = clipmap_of([((70.0, -80.0, -50.0), (80.0, 260.0, 100.0), pr.CONTENTS_SOLID)])

    g = game_of(pts)
    with pytest.raises(pr.PathRegenError) as err:
        pr.regenerate_or_raise(g, wall)
    assert "connected component" in str(err.value)
    assert "walled off" in str(err.value)
    assert pr.component_report(g)["count"] == 2

    # the same nodes with no wall connect into one component.
    g2 = game_of(pts)
    out = pr.regenerate_or_raise(g2, clipmap=None)
    assert out["components"] == 1


def test_moved_prop_blocks_new_link_and_frees_old():
    # a prop clip at A; a link that crosses A is blocked, a link across B is clear.
    old_pos = clipmap_of([((70.0, -50.0, -50.0), (80.0, 60.0, 100.0), pc.PLAYER_CLIP_CONTENTS)])
    new_pos = clipmap_of([((200.0, -50.0, -50.0), (210.0, 60.0, 100.0), pc.PLAYER_CLIP_CONTENTS)])
    link_through_a = ((0, 0, 40), (150, 0, 40))
    link_through_b = ((170, 0, 40), (240, 0, 40))
    # with the prop at A
    assert pr.segment_blocked(old_pos, *link_through_a)
    assert not pr.segment_blocked(old_pos, *link_through_b)
    # after the move to B, A is free and B is blocked
    assert not pr.segment_blocked(new_pos, *link_through_a)
    assert pr.segment_blocked(new_pos, *link_through_b)


# -- zones -----------------------------------------------------------------------------------


BUSMOVE_BUILD = Path("/mnt/c/Users/bolst/Desktop/opent5-hwtest/nuked/q_editor_busmove/mp_nuked.ff")
BASE_BUILD = Path("/mnt/c/Users/bolst/Desktop/opent5-hwtest/nuked/d_pak/mp_nuked.ff")


def find_zone(name: str) -> Path | None:
    for key in ("OPENT5_ZONES", "OPENT5_PATCH_ZONES"):
        folder = env.path_of(key)
        if folder and (folder / f"{name}.ff").is_file():
            return folder / f"{name}.ff"
    return None


def load_map(path: Path):
    """Return (xfile, game_node, clipmap, locator) for a zone or a raw .ff."""
    data = path.read_bytes()
    content = bytes(Zone.open(data).content) if data[:8] == b"IWff0100" else data
    x = parse(content)
    game = next((a.data for a in x.assets if a.type == AssetType.GAME_MAP_MP), None)
    clip = next(
        (a.data for a in x.assets if a.type in (AssetType.COL_MAP_MP, AssetType.COL_MAP_SP)),
        None,
    )
    loc = pc.BspLocator.from_xfile(x, clip) if clip is not None else None
    return x, game, pc.ClipMap(clip) if clip is not None else None, loc


def baked_edges(game) -> set[tuple[int, int]]:
    real = pathlinks._real_nodes(game["nodes"])
    present = {i for i, _, _ in real}
    edges: set[tuple[int, int]] = set()
    for i, el, _ in real:
        count = struct.unpack_from(">H", bytes(el["raw"]), pathlinks.TOTAL_LINK_COUNT_OFF)[0]
        links = bytes(el.get("links") or b"")
        for o in range(count):
            t = struct.unpack_from(">H", links, o * pathlinks.PATHLINK_SIZE + 4)[0]
            if t in present:
                edges.add((min(i, t), max(i, t)))
    return edges


@pytest.mark.zones
@pytest.mark.slow
def test_retail_los_rejects_solid_and_keeps_single_component():
    """Retail mp_nuked: the LOS rejects a segment that crosses a stock solid brush
    and passes one in open air, and a whole-map relink stays one connected component
    (retail ships a baked, connected graph, and LOS keeps almost all of it)."""
    path = find_zone("mp_nuked")
    if path is None:
        pytest.skip("mp_nuked.ff is not on this machine")
    x, game, cm, loc = load_map(path)
    assert game is not None and cm is not None

    # a segment straight through a mid-sized stock solid brush is blocked.
    solid = next(
        i
        for i in range(cm.num_brushes)
        if cm.brush(i).numsides == 0
        and cm.brush(i).contents & pr.CONTENTS_SOLID
        and all(25.0 < cm.brush(i).maxs[k] - cm.brush(i).mins[k] < 200.0 for k in range(3))
    )
    b = cm.brush(solid)
    c = tuple((b.mins[k] + b.maxs[k]) / 2 for k in range(3))
    half_x = (b.maxs[0] - b.mins[0]) / 2
    p0 = (c[0] - half_x - 20, c[1], c[2])
    p1 = (c[0] + half_x + 20, c[1], c[2])
    assert pr.segment_blocked(cm, p0, p1)
    # high in open air, nothing to cross.
    assert not pr.segment_blocked(cm, (0.0, 0.0, 2000.0), (300.0, 0.0, 2000.0), locator=loc)

    before = baked_edges(game)
    report = pr.regenerate_or_raise(game, cm, loc)
    assert report["components"] == 1 and report["linked"]
    assert report["rejected"] >= 1  # LOS turned at least one baked-through link away
    after = baked_edges(game)
    # LOS keeps almost every baked edge (it does not invent a disconnected graph).
    assert len(after & before) >= len(before) - 5


@pytest.mark.zones
@pytest.mark.slow
@pytest.mark.skipif(
    not (BUSMOVE_BUILD.is_file() and BASE_BUILD.is_file()),
    reason="the editor bus-move build and its base are not on this machine",
)
def test_busmove_los_rejects_new_bus_and_frees_old_footprint():
    """The editor bus-move build: a candidate link through the bus's NEW clip
    position is rejected (blocked by a bus-cluster brush), and the bus's OLD
    footprint is clear of the bus clip (the cluster moved away, so no bus brush
    blocks a link there).

    The bus cluster is the brushes that moved +300 X between the base and the move.

    Whole-map gate: this converted base map's path nodes are sparse (links span
    hundreds of units and cross interior walls), so a LOS relink cannot hold one
    connected component and ``regenerate_or_raise`` raises. That is a property of the
    sparse converted map, not of the bus; the bus's own effect is the segment test
    above. Either outcome is valid per the task, and this build's is a documented
    split."""
    _, _, base_cm, _ = load_map(BASE_BUILD)
    x, game, cm, loc = load_map(BUSMOVE_BUILD)
    assert cm is not None and base_cm is not None

    # the bus cluster: brushes whose mins moved by exactly +300 in X, 0 in Y and Z.
    cluster = [
        i
        for i in range(min(base_cm.num_brushes, cm.num_brushes))
        if abs(cm.brush(i).mins[0] - base_cm.brush(i).mins[0] - 300.0) < 0.5
        and abs(cm.brush(i).mins[1] - base_cm.brush(i).mins[1]) < 0.5
        and abs(cm.brush(i).mins[2] - base_cm.brush(i).mins[2]) < 0.5
    ]
    assert cluster, "no bus cluster found (no brushes moved +300 X)"

    def bounds(view, idxs):
        mn = tuple(min(view.brush(i).mins[k] for i in idxs) for k in range(3))
        mx = tuple(max(view.brush(i).maxs[k] for i in idxs) for k in range(3))
        return mn, mx

    old_mn, old_mx = bounds(base_cm, cluster)  # where the bus used to be
    new_mn, new_mx = bounds(cm, cluster)  # where it is now
    z = (new_mn[2] + new_mx[2]) / 2

    def across(mn, mx):
        cx = (mn[0] + mx[0]) / 2
        return (cx, mn[1] - 60.0, z), (cx, mx[1] + 60.0, z)

    new_p0, new_p1 = across(new_mn, new_mx)
    old_p0, old_p1 = across(old_mn, old_mx)

    # a link through the bus's new position is blocked, by a bus-cluster brush.
    blocker = pr.segment_blocking_brush(cm, new_p0, new_p1, only=set(cluster))
    assert blocker in cluster
    # and the full LOS test (through the BSP) agrees the new link is blocked.
    assert pr.segment_blocked(cm, new_p0, new_p1, locator=loc)
    # the old footprint is clear of the bus clip: no bus-cluster brush blocks it.
    assert pr.segment_blocking_brush(cm, old_p0, old_p1, only=set(cluster)) is None

    # the documented whole-map outcome on this sparse converted build.
    with pytest.raises(pr.PathRegenError):
        pr.regenerate_or_raise(game, cm, loc)
