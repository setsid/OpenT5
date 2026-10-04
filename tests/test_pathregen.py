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
from copy import deepcopy
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


# -- edit-relative (local) regeneration ------------------------------------------------------


def _bake(pts):
    """A game with baseline links baked by distance (no collision), the starting point for an
    edit-relative regen: the map already carries links the editor then perturbs."""
    g = game_of(pts)
    pr.regenerate_links(g, clipmap=None)
    return g


# left/right corridor: two nodes each side, joined across the middle; a wall box at x~150
# represents a newly placed prop clip that blocks every cross link.
CORRIDOR = [(0.0, 0.0, 0.0), (0.0, 120.0, 0.0), (300.0, 0.0, 0.0), (300.0, 120.0, 0.0)]
WALL_BOX = ((140.0, -80.0, -50.0), (160.0, 260.0, 100.0))


def test_regenerate_local_new_split_raises():
    """An edit whose clip blocks the only links bridging two sides splits a once-joined
    graph: ``regenerate_local_or_raise`` raises and names the cut-off nodes."""
    g = _bake(CORRIDOR)
    assert pr.component_report(g)["count"] == 1  # the baked baseline is one component
    wall = clipmap_of([(WALL_BOX[0], WALL_BOX[1], pr.CONTENTS_SOLID)])

    report = pr.regenerate_local(g, wall, None, [WALL_BOX])
    assert report["removed"] >= 1  # the cross links were dropped
    assert report["new_split"] is True
    assert report["before_components"] == 1 and report["after_components"] == 2
    assert report["preexisting_fragmentation"] is False
    assert report["offending_nodes"]  # the side cut off is named

    g2 = _bake(CORRIDOR)
    with pytest.raises(pr.PathRegenError) as err:
        pr.regenerate_local_or_raise(g2, wall, None, [WALL_BOX])
    assert "walled off" in str(err.value)


def test_regenerate_local_passes_when_another_path_keeps_sides_joined():
    """The same wall, but a node above it bridges the two sides by links that do not cross
    the edited box: the cross links drop, no new split, so the save is allowed."""
    pts = CORRIDOR + [(150.0, 400.0, 0.0)]  # a bridge node clear of the wall's Y span
    g = _bake(pts)
    assert pr.component_report(g)["count"] == 1
    wall = clipmap_of([(WALL_BOX[0], WALL_BOX[1], pr.CONTENTS_SOLID)])

    report = pr.regenerate_local_or_raise(g, wall, None, [WALL_BOX])
    assert report["removed"] >= 1  # the direct cross links still drop
    assert report["new_split"] is False
    assert report["after_components"] == 1  # the bridge node keeps it one component


def test_regenerate_local_preexisting_fragmentation_is_warned_not_raised():
    """A map already in two components before the edit (under our stricter-than-engine LOS
    model): an edit that re-tests a clear link changes nothing and must not raise, only flag
    the pre-existing fragmentation."""
    # cluster A near the origin, cluster B far away: no baseline link bridges them.
    pts = [(0.0, 0.0, 0.0), (0.0, 120.0, 0.0), (5000.0, 0.0, 0.0), (5000.0, 120.0, 0.0)]
    g = _bake(pts)
    assert pr.component_report(g)["count"] == 2  # already fragmented before any edit

    # an edit box over the A1-A2 link, but no collision there, so the link is re-tested clear.
    box = ((-20.0, 40.0, -50.0), (20.0, 80.0, 100.0))
    report = pr.regenerate_local_or_raise(g, clipmap_of([]), None, [box])
    assert report["removed"] == 0  # the re-tested link was clear, nothing dropped
    assert report["new_split"] is False
    assert report["preexisting_fragmentation"] is True
    assert report["before_components"] == 2 and report["after_components"] == 2


def test_regenerate_local_no_edit_or_no_nodes_does_nothing():
    g = _bake(CORRIDOR)
    empty = pr.regenerate_local(g, clipmap_of([]), None, [])  # no edited boxes
    assert empty["removed"] == 0 and empty["new_split"] is False
    none_nodes = pr.regenerate_local(game_of([]), clipmap_of([]), None, [WALL_BOX])
    assert none_nodes["nodes"] == 0 and none_nodes["new_split"] is False


# -- zones -----------------------------------------------------------------------------------


BUSMOVE_BUILD = Path("/mnt/c/Users/bolst/Desktop/opent5-hwtest/nuked/q_editor_busmove/mp_nuked.ff")
BASE_BUILD = Path("/mnt/c/Users/bolst/Desktop/opent5-hwtest/nuked/d_pak/mp_nuked.ff")


def find_zone(name: str) -> Path | None:
    # Pin to a sha1-checked retail backup, never the live .env mp_nuked (the RPCS3 file
    # overwritten by every device test, e.g. whatever build was last staged).
    from retail_fixtures import RETAIL_SHA1, retail_zone

    if name in RETAIL_SHA1:
        return retail_zone(name)
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


def _raised(pos, i, j):
    pa, pb = pos[i], pos[j]
    return (
        (pa[0], pa[1], pa[2] + pr.LOS_HEIGHT),
        (pb[0], pb[1], pb[2] + pr.LOS_HEIGHT),
    )


@pytest.mark.zones
@pytest.mark.slow
def test_retail_los_rejects_solid_and_fragmentation_is_preexisting():
    """Pristine retail mp_nuked (the sha1-pinned backup, 296 path nodes): the LOS rejects a
    segment crossing a stock solid brush and passes one in open air; and the whole-map LOS
    relink FRAGMENTS the graph (far more than one component), yet this map loads and plays on
    the device. That fragmentation is a property of the map under our stricter-than-engine LOS
    model, not something an edit caused, which is exactly why the save gate is edit-relative:
    ``regenerate_local`` reports the fragmentation as a warning and does not raise when the
    edit itself splits nothing."""
    path = find_zone("mp_nuked")
    if path is None:
        pytest.skip("mp_nuked.ff is not on this machine")
    x, game, cm, loc = load_map(path)
    assert game is not None and cm is not None

    # a segment straight through a mid-sized stock solid brush is blocked (any shape: the slab
    # test reads its axis-aligned bounds). Pristine retail has hundreds of such brushes.
    solid = next(
        i
        for i in range(cm.num_brushes)
        if cm.brush(i).contents & pr.CONTENTS_SOLID
        and all(16.0 < cm.brush(i).maxs[k] - cm.brush(i).mins[k] < 1000.0 for k in range(3))
    )
    b = cm.brush(solid)
    c = tuple((b.mins[k] + b.maxs[k]) / 2 for k in range(3))
    half_x = (b.maxs[0] - b.mins[0]) / 2
    p0 = (c[0] - half_x - 20, c[1], c[2])
    p1 = (c[0] + half_x + 20, c[1], c[2])
    assert pr.segment_blocked(cm, p0, p1)
    # high in open air, nothing to cross.
    assert not pr.segment_blocked(cm, (0.0, 0.0, 2000.0), (300.0, 0.0, 2000.0), locator=loc)

    # the pre-existing fragmentation: the baked graph is already more than one component, and a
    # whole-map LOS relink fragments it further. A whole-map single-component gate would reject
    # this engine-happy map, so it is the wrong model for the save path.
    baked = pr.connected_components(game)
    assert len(baked) > 1
    whole = pr.regenerate_links(deepcopy(game), cm, loc)
    assert whole["components"] > 1 and not whole["linked"]
    assert whole["rejected"] >= 100  # strict LOS turns many baked-through links away

    # the edit-relative gate on the same map, with an edit far out in empty air that crosses no
    # link: nothing is dropped, the pre-existing fragmentation is flagged, and it does not raise.
    far = ((9000.0, 9000.0, 9000.0), (9050.0, 9050.0, 9050.0))
    report = pr.regenerate_local_or_raise(deepcopy(game), cm, loc, [far])
    assert report["removed"] == 0
    assert report["new_split"] is False
    assert report["preexisting_fragmentation"] is True
    assert report["before_components"] == len(baked)


@pytest.mark.zones
@pytest.mark.slow
def test_retail_local_regen_drops_only_crossing_links_no_shatter():
    """Pristine retail: an edit whose box lies on a baked link that the stock collision blocks
    drops that link (and the few others crossing the box), but NOT the whole-map graph. Only
    links crossing the edited box are re-tested, so the drop count is a handful, far below the
    thousands a whole-map LOS relink rejects, and connectivity holds (no new split)."""
    path = find_zone("mp_nuked")
    if path is None:
        pytest.skip("mp_nuked.ff is not on this machine")
    x, game, cm, loc = load_map(path)
    assert game is not None and cm is not None

    real = pathlinks._real_nodes(game["nodes"])
    pos = {i: p for i, _, p in real}
    edges = baked_edges(game)
    whole_rejected = pr.regenerate_links(deepcopy(game), cm, loc)["rejected"]

    def box_of(i, j):
        la, lb = _raised(pos, i, j)
        return (
            tuple(min(la[k], lb[k]) - 8 for k in range(3)),
            tuple(max(la[k], lb[k]) + 8 for k in range(3)),
        )

    # a baked link the stock collision already blocks at body height, boxed tightly: the "edit"
    # is a clip arriving on that link. Take the first such link whose drop keeps the graph
    # connected (so this test isolates the locality claim, not the split path tested elsewhere).
    target = report = None
    for i, j in sorted(e for e in edges if e[0] in pos and e[1] in pos):
        if not pr.segment_blocked(cm, *_raised(pos, i, j), locator=loc):
            continue
        g = deepcopy(game)
        r = pr.regenerate_local(g, cm, loc, [box_of(i, j)])
        if (i, j) not in baked_edges(g) and not r["new_split"]:
            target, report = (i, j), r
            break
    assert target is not None, "no blocked baked link found whose drop keeps the graph connected"

    assert report["removed"] >= 1  # the targeted link (and any others crossing the box) dropped
    # locality: only a handful of links went, nowhere near the whole-map shatter.
    assert report["removed"] < whole_rejected / 10
    assert report["new_split"] is False  # the dense component stays connected


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
