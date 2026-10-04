"""Line-of-sight path-link regeneration for the v0.3.0 map editor.

``opent5.convert.pathlinks`` bakes path-node links by distance so an unlinked
converted map loads (the engine reads baked links, it does not connect at load).
It links every node pair within a widening radius and refuses a graph that is not
one connected component. It has no notion of what is *between* two nodes, so it
will happily link two nodes through a wall or a prop.

The editor needs more: once a prop has moved, a link may no longer be walkable.
This module relinks the nodes by distance the same way ``pathlinks.generate``
does, but rejects any candidate link whose straight segment passes through solid
collision in the *edited* clipMap (the line of sight). Links are kept only when
the nodes are within range AND the segment is clear, and are written back in the
exact stock PathData bytes ``pathlinks`` produces (bidirectional, inline, true
distance), so the baking is not regressed.

How the segment-vs-clipMap test works
--------------------------------------

The whole of mp_nuked's collision is axis-aligned boxes (cod2map emits axial
``cbrush_t`` boxes; 4205 of 4205 brushes are axial), so a segment-vs-box test is
exact and cheap: the slab method clips the segment's parameter range against each
box's three axis slabs (``_segment_hits_box``). A link is blocked when its segment
crosses any box whose ``contents`` meet the block mask (solid world plus the
device-proven player-clip bits).

Two refinements earn their place:

- The test line is raised to body height (``LOS_HEIGHT``) above the node floor.
  Path nodes sit on the ground, on top of floor and step brushes; a line at node
  height lies in those boxes' top face and a naive slab test reads the whole floor
  as blocking, fragmenting the graph. Raised to body height the line clears the
  floor tops yet still crosses any wall or prop. On retail mp_nuked this keeps
  1903 of the 1904 baked edges and the graph stays one connected component; at
  node height 272 edges are wrongly rejected into 7 components.
- Grazing a face does not count as a crossing. The slab test is strict: a segment
  that merely lies in a box's plane (zero penetration) is clear, so a link that
  runs flush along a wall's face is kept.

A ``BspLocator`` (when supplied) point-samples the segment and gathers only the
brushes reachable from the leaves it passes through, the way the engine's trace
finds a brush; on mp_nuked that is about two candidate boxes per segment, so a
whole-map relink is a second or two. Without a locator the test falls back to
every blocking brush (correct, used by the synthetic tests).

The single-component gate
-------------------------

After relinking (widening the radius as ``pathlinks`` does, but every candidate
still LOS-constrained), the graph must be one connected component or the engine
drops the map. ``regenerate_or_raise`` reads the links back from the written bytes
and raises ``PathRegenError`` when it is not, so a build or save fails loudly: a
moved prop that walls off a region cannot be saved as a broken map.
"""

from __future__ import annotations

import math
import struct

from opent5.convert import pathlinks
from opent5.convert.propclip import PLAYER_CLIP_CONTENTS
from opent5.xfile.constants import PTR_INLINE

Vec3 = tuple[float, float, float]

#: CoD contents bit for solid world geometry.
CONTENTS_SOLID = 0x1

#: A candidate link is blocked when a crossed brush's contents meet this mask:
#: solid world geometry or the device-proven player-clip bits (so a link may not
#: cross a wall, a static-model clip or a prop's player-clip box). The player-clip
#: bits are needed in their own right: a player-clip brush carries no CONTENTS_SOLID
#: bit (its contents are ``0x8030200``), so solid alone would miss it.
DEFAULT_BLOCK_MASK = CONTENTS_SOLID | PLAYER_CLIP_CONTENTS

#: The LOS line is tested this far above the node floor. Nodes sit on top of floor
#: and step brushes; a line at node height grazes their top faces and reads as
#: blocked. 40 units clears Nuketown's step height while still crossing any wall or
#: prop, and keeps retail's baked graph one connected component (see the module note).
LOS_HEIGHT = 40.0

#: Relink radius schedule, matching ``pathlinks.generate``. mp_nuked's node grid is
#: spaced about 144 units, so 160 links the grid neighbours.
LINK_START_DIST = 160.0
LINK_STEP = 40.0
LINK_MAX_DIST = 600.0

#: Spacing at which a segment is point-sampled to gather candidate brushes through
#: the BSP. Finer than the thinnest wall so no blocking leaf is stepped over.
SAMPLE_STEP = 16.0

_ZERO3: Vec3 = (0.0, 0.0, 0.0)


class PathRegenError(Exception):
    """The path graph could not be regenerated as one connected component."""


def _nodes_of(game) -> list:
    """The path-node element list, whether ``game`` is the parsed GameWorldMp node
    (a dict with ``"nodes"``) or the node list itself."""
    return game if isinstance(game, list) else (game.get("nodes") or [])


# -- the segment-vs-clipMap line-of-sight test -----------------------------------------------


def _segment_hits_box(p0: Vec3, p1: Vec3, bmin: Vec3, bmax: Vec3, radius: Vec3 = _ZERO3) -> bool:
    """Whether the segment ``p0``..``p1`` crosses the axis-aligned box, optionally
    grown by ``radius`` on each axis (a player hull swept along the line). The slab
    method, strict at the faces: a segment that only lies in a face plane (zero
    penetration) is not a crossing, so a link flush along a wall is kept."""
    tmin, tmax = 0.0, 1.0
    for k in range(3):
        lo = bmin[k] - radius[k]
        hi = bmax[k] + radius[k]
        d = p1[k] - p0[k]
        if abs(d) < 1e-9:
            # The segment does not move on this axis: it must be strictly inside the
            # slab, so a coplanar graze does not count.
            if p0[k] <= lo or p0[k] >= hi:
                return False
        else:
            inv = 1.0 / d
            t1 = (lo - p0[k]) * inv
            t2 = (hi - p0[k]) * inv
            if t1 > t2:
                t1, t2 = t2, t1
            if t1 > tmin:
                tmin = t1
            if t2 < tmax:
                tmax = t2
            if tmin > tmax:
                return False
    return tmax > tmin  # positive penetration length, not a tangent touch


class LosTester:
    """A reusable line-of-sight tester over one clipMap. Caches the blocking boxes
    so a whole-map relink (thousands of candidate links) stays cheap.

    With a ``BspLocator`` it gathers, per segment, only the brushes reachable from
    the leaves the segment passes through (the engine's own way of finding a brush);
    without one it tests every blocking brush."""

    def __init__(
        self,
        cm,
        locator=None,
        block_mask: int = DEFAULT_BLOCK_MASK,
        radius: Vec3 = _ZERO3,
        sample_step: float = SAMPLE_STEP,
    ):
        self.cm = cm
        self.locator = locator
        self.block_mask = block_mask
        self.radius = tuple(float(v) for v in radius)
        self.sample_step = sample_step
        self._leaf_blocking: dict[int, set[int]] = {}
        self._all_blocking: set[int] | None = None

    def _blocking_in_leaf(self, leaf: int) -> set[int]:
        cached = self._leaf_blocking.get(leaf)
        if cached is None:
            cached = {
                b
                for b in self.cm.reachable_brushes(leaf)
                if self.cm.brush(b).contents & self.block_mask
            }
            self._leaf_blocking[leaf] = cached
        return cached

    def _all_blocking_brushes(self) -> set[int]:
        if self._all_blocking is None:
            self._all_blocking = {
                b
                for b in range(self.cm.num_brushes)
                if self.cm.brush(b).contents & self.block_mask
            }
        return self._all_blocking

    def _candidates(self, p0: Vec3, p1: Vec3) -> set[int]:
        if self.locator is None:
            return self._all_blocking_brushes()
        n = max(2, int(math.dist(p0, p1) / self.sample_step) + 1)
        cand: set[int] = set()
        for s in range(n + 1):
            t = s / n
            p = (
                p0[0] + (p1[0] - p0[0]) * t,
                p0[1] + (p1[1] - p0[1]) * t,
                p0[2] + (p1[2] - p0[2]) * t,
            )
            leaf = self.locator.locate(p)
            if leaf is not None:
                cand |= self._blocking_in_leaf(leaf)
        return cand

    def blocking_brush(self, p0: Vec3, p1: Vec3, only=None) -> int | None:
        """The index of the first brush the segment crosses, or ``None`` when the
        segment is clear. ``only`` restricts the test to a given set of brush
        indices (e.g. one prop's cluster) and still applies the block mask."""
        if only is not None:
            candidates: set[int] = set(only)
        else:
            candidates = self._candidates(p0, p1)
        for b in candidates:
            br = self.cm.brush(b)
            if (br.contents & self.block_mask) and _segment_hits_box(
                p0, p1, br.mins, br.maxs, self.radius
            ):
                return b
        return None

    def blocked(self, p0: Vec3, p1: Vec3, only=None) -> bool:
        return self.blocking_brush(p0, p1, only=only) is not None


def segment_blocked(
    cm,
    p0: Vec3,
    p1: Vec3,
    *,
    locator=None,
    block_mask: int = DEFAULT_BLOCK_MASK,
    radius: Vec3 = _ZERO3,
    only=None,
    sample_step: float = SAMPLE_STEP,
) -> bool:
    """Whether the straight segment ``p0``..``p1`` passes through blocking collision
    in ``cm`` (a ``propclip.ClipMap``). A convenience wrapper around ``LosTester``;
    for many segments build one ``LosTester`` and reuse it."""
    return LosTester(cm, locator, block_mask, radius, sample_step).blocked(p0, p1, only=only)


def segment_blocking_brush(
    cm,
    p0: Vec3,
    p1: Vec3,
    *,
    locator=None,
    block_mask: int = DEFAULT_BLOCK_MASK,
    radius: Vec3 = _ZERO3,
    only=None,
    sample_step: float = SAMPLE_STEP,
) -> int | None:
    """The index of the first brush the segment crosses, or ``None`` (diagnostics)."""
    return LosTester(cm, locator, block_mask, radius, sample_step).blocking_brush(
        p0, p1, only=only
    )


# -- connectivity (reading the written links back) -------------------------------------------


def connected_components(game) -> list[set[int]]:
    """The connected components of the path graph as currently baked into the node
    bytes (read back from each node's link array), so the gate checks what was
    actually written, not an in-memory adjacency."""
    nodes = _nodes_of(game)
    real = pathlinks._real_nodes(nodes)
    present = {i for i, _, _ in real}
    adj: dict[int, set[int]] = {i: set() for i in present}
    for i, el, _ in real:
        raw = bytes(el["raw"])
        count = struct.unpack_from(">H", raw, pathlinks.TOTAL_LINK_COUNT_OFF)[0]
        links = bytes(el.get("links") or b"")
        for o in range(count):
            target = struct.unpack_from(">H", links, o * pathlinks.PATHLINK_SIZE + 4)[0]
            if target in present:
                adj[i].add(target)
                adj[target].add(i)
    return pathlinks._components(adj)


def component_report(game) -> dict:
    """A summary of the baked path graph: the component count, the component sizes
    (largest first), the components themselves, and the ``offending`` nodes (every
    node outside the largest component), for diagnosing a split."""
    comps = sorted(connected_components(game), key=len, reverse=True)
    offending = sorted(n for c in comps[1:] for n in c)
    return {
        "count": len(comps),
        "sizes": [len(c) for c in comps],
        "components": comps,
        "offending": offending,
    }


# -- relinking --------------------------------------------------------------------------------


def _write_links(real: list, adj: dict[int, set[int]], dist: dict) -> int:
    """Write the adjacency back into the node bytes in the exact stock PathData
    format ``pathlinks.generate`` produces: an inline ``pathlink_s`` array per node
    (fDist, nodeNum, then zeros), ``totalLinkCount`` and the inline Links marker."""
    total = 0
    for i, el, _ in real:
        targets = sorted(adj[i])
        blob = bytearray()
        for t in targets:
            blob += struct.pack(">fHbb", dist[(i, t)], t, 0, 0) + bytes(4)
        el["links"] = bytes(blob)
        raw = bytearray(el["raw"])
        struct.pack_into(">H", raw, pathlinks.TOTAL_LINK_COUNT_OFF, len(targets))
        struct.pack_into(">I", raw, pathlinks.LINKS_PTR_OFF, PTR_INLINE)
        el["raw"] = bytes(raw)
        total += len(targets)
    return total


def regenerate_links(
    game,
    clipmap=None,
    locator=None,
    *,
    block_mask: int = DEFAULT_BLOCK_MASK,
    radius: Vec3 = _ZERO3,
    height: float = LOS_HEIGHT,
    start_dist: float = LINK_START_DIST,
    step: float = LINK_STEP,
    max_dist: float = LINK_MAX_DIST,
    sample_step: float = SAMPLE_STEP,
) -> dict:
    """Relink the path nodes of ``game`` (the parsed GameWorldMp node, or its node
    list) by distance as ``pathlinks.generate`` does, but reject any candidate link
    whose segment passes through blocking collision in ``clipmap`` (a
    ``propclip.ClipMap``; ``None`` means no collision, i.e. pure distance baking,
    identical to ``pathlinks.generate``). ``locator`` is the clipMap's
    ``BspLocator`` and makes the LOS test fast and engine-faithful.

    The links are written back in the stock format (bidirectional, inline, true
    distance). The radius widens from ``start_dist`` towards ``max_dist`` until the
    graph connects, but every candidate stays LOS-constrained, so a region the
    collision walls off does not connect however far the radius widens; the single-
    component gate (``regenerate_or_raise``) is what catches that. Returns a report;
    it does not raise on a disconnected graph."""
    nodes = _nodes_of(game)
    real = pathlinks._real_nodes(nodes)
    if not real:
        return {"nodes": 0, "links": 0, "components": 0, "rejected": 0, "linked": False}

    tester = (
        LosTester(clipmap, locator, block_mask, radius, sample_step)
        if clipmap is not None
        else None
    )
    #: Memoised LOS verdict per node pair, reused across widening passes.
    los: dict[tuple[int, int], bool] = {}

    def clear(ia: int, pa: Vec3, ib: int, pb: Vec3) -> bool:
        if tester is None:
            return True
        key = (ia, ib)
        verdict = los.get(key)
        if verdict is None:
            la = (pa[0], pa[1], pa[2] + height)
            lb = (pb[0], pb[1], pb[2] + height)
            verdict = tester.blocked(la, lb)
            los[key] = verdict
        return not verdict

    def build(link_dist: float):
        adj: dict[int, set[int]] = {i: set() for i, _, _ in real}
        dist: dict[tuple[int, int], float] = {}
        rejected = 0
        for a in range(len(real)):
            ia, _, pa = real[a]
            for b in range(a + 1, len(real)):
                ib, _, pb = real[b]
                d = math.dist(pa, pb)
                if d > link_dist:
                    continue
                if not clear(ia, pa, ib, pb):
                    rejected += 1
                    continue
                adj[ia].add(ib)
                adj[ib].add(ia)
                dist[(ia, ib)] = dist[(ib, ia)] = d
        return adj, dist, rejected

    link_dist = start_dist
    adj, dist, rejected = build(link_dist)
    while len(pathlinks._components(adj)) > 1 and link_dist < max_dist:
        link_dist = min(link_dist + step, max_dist)
        adj, dist, rejected = build(link_dist)

    comps = pathlinks._components(adj)
    total = _write_links(real, adj, dist)
    return {
        "nodes": len(real),
        "links": total // 2,
        "components": len(comps),
        "rejected": rejected,
        "link_dist": link_dist,
        "linked": len(comps) == 1,
    }


def regenerate_or_raise(game, clipmap=None, locator=None, **kwargs) -> dict:
    """Relink with ``regenerate_links`` and then gate: the baked graph must be one
    connected component, or raise ``PathRegenError``. Use this on a build or save so
    a prop move that walls off a region fails loudly rather than shipping a map the
    engine drops at load."""
    report = regenerate_links(game, clipmap, locator, **kwargs)
    comps = sorted(connected_components(game), key=len, reverse=True)
    if len(comps) != 1:
        sizes = [len(c) for c in comps]
        offending = sorted(n for c in comps[1:] for n in c)
        shown = offending[:12]
        tail = " and more" if len(offending) > len(shown) else ""
        raise PathRegenError(
            f"path links do not form one connected component: {len(comps)} components "
            f"with sizes {sizes}. A moved or added prop has walled off a region so no "
            f"clear link bridges it. Offending path nodes: {shown}{tail}."
        )
    report["components"] = 1
    report["linked"] = True
    return report


# -- edit-relative (local) regeneration ------------------------------------------------------
#
# The whole-map ``regenerate_or_raise`` gate above is wrong for the editor's save path.
# Pristine retail mp_nuked ships a sparse 296-node graph that our strict segment-LOS model
# reads as 4 components (and fragments to 42 under a whole-map relink), yet the engine loads
# and plays it: the engine's path connectivity is looser than ours (see
# docs/research/pathlink-los-recipe.md). A whole-map gate would therefore reject maps the
# engine is happy with on every save. So the save gate is edit-relative: keep the map's
# existing links untouched, re-test only the links whose segment crosses an edited clip
# volume, and fail only when the edit itself splits a pair of nodes that were connected
# before. Pre-existing fragmentation under our model is a warning, never a failure.


def _node_link_records(el) -> list[tuple[int, bytes]]:
    """The ``(target, record)`` pairs of a node's inline pathlink array, each record the
    exact stock 12 bytes as stored (so a kept link is preserved byte-for-byte, including any
    retail flag bytes ``_write_links`` would zero)."""
    raw = bytes(el["raw"])
    count = struct.unpack_from(">H", raw, pathlinks.TOTAL_LINK_COUNT_OFF)[0]
    links = bytes(el.get("links") or b"")
    out = []
    for o in range(count):
        rec = links[o * pathlinks.PATHLINK_SIZE : (o + 1) * pathlinks.PATHLINK_SIZE]
        out.append((struct.unpack_from(">H", rec, 4)[0], rec))
    return out


def _drop_links(el, drop_targets: set[int]) -> None:
    """Remove the records naming any target in ``drop_targets`` from a node's inline link
    array, keeping the other records unchanged, and update ``totalLinkCount``."""
    kept = [rec for target, rec in _node_link_records(el) if target not in drop_targets]
    el["links"] = b"".join(kept)
    raw = bytearray(el["raw"])
    struct.pack_into(">H", raw, pathlinks.TOTAL_LINK_COUNT_OFF, len(kept))
    el["raw"] = bytes(raw)


def regenerate_local(
    game,
    clipmap,
    locator,
    edited_boxes,
    *,
    block_mask: int = DEFAULT_BLOCK_MASK,
    radius: Vec3 = _ZERO3,
    height: float = LOS_HEIGHT,
    sample_step: float = SAMPLE_STEP,
) -> dict:
    """Edit-relative path-link regeneration for the editor's save path.

    Keeps the map's existing links. For each existing link whose body-height segment crosses
    ANY box in ``edited_boxes`` (the old and new footprints of moved, added, rotated or
    deleted prop clips, each an ``(mins, maxs)`` AABB), re-test line of sight against
    ``clipmap`` and drop the link when it is now blocked. A link that crosses no edited box is
    left untouched, so the whole-map graph is never re-derived (that is the "local" part).

    Returns a report ``{removed, before_components, after_components, new_split,
    offending_nodes, preexisting_fragmentation, nodes}``. It does not raise;
    ``regenerate_local_or_raise`` is the gate. A new split is a pair of nodes that shared a
    component before the drops but no longer do (the edit walled them off). Pre-existing
    fragmentation (the graph already had more than one component before any drop, under our
    stricter-than-engine model) is recorded as a warning, never a split."""
    nodes = _nodes_of(game)
    real = pathlinks._real_nodes(nodes)
    empty = {
        "removed": 0,
        "before_components": 0,
        "after_components": 0,
        "new_split": False,
        "offending_nodes": [],
        "preexisting_fragmentation": False,
        "nodes": len(real),
    }
    if not real or not edited_boxes:
        return empty

    pos = {i: p for i, _, p in real}
    present = set(pos)
    by_index = {i: el for i, el, _ in real}

    # Baseline connectivity from the CURRENT links, before any drop.
    baseline = connected_components(game)
    boxes = [(tuple(mn), tuple(mx)) for mn, mx in edited_boxes]
    tester = LosTester(clipmap, locator, block_mask, radius, sample_step)

    # The undirected edges whose segment crosses an edited box and are now LOS-blocked.
    dropped: set[tuple[int, int]] = set()
    for i, el, _ in real:
        pa = pos[i]
        la = (pa[0], pa[1], pa[2] + height)
        for target, _rec in _node_link_records(el):
            if target <= i or target not in present:
                continue  # dedupe the bidirectional pair; skip links to spare nodes
            pb = pos[target]
            lb = (pb[0], pb[1], pb[2] + height)
            if not any(_segment_hits_box(la, lb, mn, mx, radius) for mn, mx in boxes):
                continue  # not near the edit: leave this link exactly as it is
            if tester.blocked(la, lb):
                dropped.add((i, target))

    for i, j in dropped:
        _drop_links(by_index[i], {j})
        _drop_links(by_index[j], {i})

    # Recompute connectivity from the written bytes and look for a pair the edit split.
    after = connected_components(game)
    comp_id: dict[int, int] = {n: cid for cid, comp in enumerate(after) for n in comp}
    offending: set[int] = set()
    for comp in baseline:
        fragments: dict[int, set[int]] = {}
        for n in comp:
            fragments.setdefault(comp_id[n], set()).add(n)
        if len(fragments) > 1:
            # the edit split this once-joined component; every node outside its largest
            # surviving fragment is now cut off.
            largest = max(fragments.values(), key=len)
            offending |= {n for n in comp if n not in largest}

    return {
        "removed": len(dropped),
        "before_components": len(baseline),
        "after_components": len(after),
        "new_split": bool(offending),
        "offending_nodes": sorted(offending),
        "preexisting_fragmentation": len(baseline) > 1,
        "nodes": len(real),
    }


def regenerate_local_or_raise(game, clipmap, locator, edited_boxes, **kwargs) -> dict:
    """Run ``regenerate_local`` and gate on a NEW split only: raise ``PathRegenError`` when
    the edit has cut a pair of nodes that were connected before apart, naming the offending
    nodes. Pre-existing fragmentation is left in the report as a warning and never raised, so
    a save fails only when the edit itself broke connectivity."""
    report = regenerate_local(game, clipmap, locator, edited_boxes, **kwargs)
    if report["new_split"]:
        offending = report["offending_nodes"]
        shown = offending[:12]
        tail = " and more" if len(offending) > len(shown) else ""
        raise PathRegenError(
            "the edit has walled off part of the path graph: "
            f"{report['before_components']} connected region(s) before the edit became "
            f"{report['after_components']} after, so path nodes that could reach each other "
            f"no longer can. A moved, added or deleted prop blocks the only link(s) that "
            f"bridged them. Offending path nodes: {shown}{tail}."
        )
    return report
