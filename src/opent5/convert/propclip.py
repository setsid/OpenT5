"""Axis-aligned clip cbrushes for the v0.3.0 map editor (Route A of the spike,
docs/research/static-model-collision.md).

A moved or added static-model prop stays solid the way cod2map makes the v0.2.0
box props solid: an axis-aligned clip ``cbrush_t`` in the clipMap, referenced
from the BSP leaf(s) it falls in so a trace finds it. This module adds, moves
and removes such a brush on a parsed ``col_map_mp`` / ``col_map_sp`` node,
keeping every count and reference consistent, so the editor can keep collision
in step without recompiling.

What a cod2map clip box looks like (confirmed from out/demo/n_box_all2, whose 8
props carry clip boxes, brushes 0..7):

- ``cbrush_t`` (0x60): mins (+0x0), contents (+0xC), maxs (+0x10), numsides=0
  (+0x1C), sides=NULL (+0x20), then the six axial side flag words
  axial_cflags[6] (+0x24) and axial_sflags[6] (+0x3C), numverts (+0x54) and a
  verts pointer (+0x58). An axis-aligned brush has no brushSides: its six faces
  are the implicit axial planes taken from mins/maxs (see
  opent5.export.collision.brush_planes), so the collision volume needs nothing
  but mins/maxs and the contents. cod2map also writes the eight box-corner verts
  and points +0x58 at them; those verts are brush-edge data the axial box trace
  does not need, and the shared ``brushVerts`` pool cannot take an appended run
  through the re-layout (it is followed immediately by ``cmodels``, so a
  one-past-the-end pointer is ambiguous), so an *added* brush is written with
  numverts=0 and a NULL verts pointer. A *moved* brush that already owns verts
  keeps them, rewritten in place to the new corners.

- A trace reaches a brush through the BSP: a ``cNode_t`` tree to a ``cLeaf_s``,
  then the leaf's ``leafBrushNode`` index into the ``cLeafBrushNode_s`` kd-tree.
  A kd-tree node with leafBrushCount==0 is a split (inline dist/range/childOffset
  at +0x8, childOffset relative and forward), one with leafBrushCount>0 is a leaf
  listing that many brush indices, inline when its +0x8 pointer is -1 or through
  the flat ``leafBrushes`` pool otherwise. T5's ``cLeaf_s`` has no
  firstLeafBrush/numLeafBrushes; brushes are reached only through this kd-tree.

A brush is added as an inline single-brush ``cLeafBrushNode_s`` (the shape of
n_box_all2's submodel nodes 33..51: axis 0, count 1, data pointer -1, one inline
u16 index), attached to every empty BSP leaf whose box overlaps the brush by
pointing that leaf's ``leafBrushNode`` at the new node. This needs no new plane
(the shared plane pool lives in another asset) and no new BSP ``cNode_t``.
Leaves that already hold cod2map brushes (a split or flat-backed kd-tree) are
not merged into: that is the one case this module refuses rather than guess, and
the device check still has the final say on solidity (collision is only
confirmable on device).

The editor drives this through ``opent5.xfile.remap.Rewrite``: parse for
editing, call these functions on the clipMap node, then ``Rewrite.build`` to
re-lay-out the zone and remap every offset pointer. Appends only grow arrays at
their tail, so the re-layout follows every pointer.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

#: ``cbrush_t`` big-endian struct offsets (see module docstring / structs.py).
CBRUSH_SIZE = 0x60
_B_MINS = 0x00
_B_CONTENTS = 0x0C
_B_MAXS = 0x10
_B_NUMSIDES = 0x1C
_B_SIDES = 0x20
_B_AXIAL_CFLAGS = 0x24
_B_AXIAL_SFLAGS = 0x3C
_B_NUMVERTS = 0x54
_B_VERTS = 0x58

#: ``cLeafBrushNode_s`` (0x14) and ``cLeaf_s`` (0x2C) offsets.
CLEAFBRUSHNODE_SIZE = 0x14
_N_AXIS = 0x00
_N_COUNT = 0x02
_N_CONTENTS = 0x04
_N_DATA = 0x08  # leaf: brushes pointer; split: dist (f32)
_N_RANGE = 0x0C
_N_CHILD0 = 0x10
_N_CHILD1 = 0x12

CLEAF_SIZE = 0x2C
_L_BRUSH_CONTENTS = 0x04
_L_MINS = 0x0C
_L_MAXS = 0x18
_L_LEAFBRUSHNODE = 0x24

#: ``clipMap_t`` header count fields.
_H_NUM_LEAFS = 0x30
_H_LBN_COUNT = 0x38
_H_NUM_LEAFBRUSHES = 0x40
_H_NUM_BRUSHVERTS = 0x58
_H_NUM_BRUSHES = 0x94

#: Device-proven player-clip flags: the v0.2.0 box clip boxes (brushes 0..7 of
#: n_box_all2) carry contents 0x8030200 and the surface word 0x440A0 on all six
#: axial faces, and were solid on PS3 (n_box_all2 collision confirmed on device).
PLAYER_CLIP_CONTENTS = 0x8030200
PLAYER_CLIP_SURFACE = 0x000440A0

_PTR_NULL = 0
_PTR_INLINE = 0xFFFFFFFF
_OFFSET_MASK = 0x1FFFFFFF


class ClipError(Exception):
    """A clip edit the clipMap structure cannot take safely."""


Vec3 = tuple[float, float, float]


# -- pure builders ---------------------------------------------------------------------------


def clip_cbrush(
    mins: Vec3,
    maxs: Vec3,
    contents: int = PLAYER_CLIP_CONTENTS,
    surface_flags: int = PLAYER_CLIP_SURFACE,
    verts_ptr: int = _PTR_NULL,
    numverts: int = 0,
) -> bytes:
    """The 0x60 ``cbrush_t`` bytes of an axis-aligned clip brush: no brushSides
    (the six axial planes come from mins/maxs), the contents on every axial
    cflag and ``surface_flags`` on every axial sflag. Matches the shape of a
    cod2map clip box of the same bounds. ``verts_ptr``/``numverts`` default to a
    NULL run (see the module docstring)."""
    for name, lo, hi in (("x", mins[0], maxs[0]), ("y", mins[1], maxs[1]), ("z", mins[2], maxs[2])):
        if hi < lo:
            raise ClipError(f"clip bounds {name}: maxs {hi} is below mins {lo}")
    b = bytearray(CBRUSH_SIZE)
    struct.pack_into(">3f", b, _B_MINS, *mins)
    struct.pack_into(">i", b, _B_CONTENTS, contents)
    struct.pack_into(">3f", b, _B_MAXS, *maxs)
    struct.pack_into(">I", b, _B_NUMSIDES, 0)
    struct.pack_into(">I", b, _B_SIDES, _PTR_NULL)
    for k in range(6):
        struct.pack_into(">i", b, _B_AXIAL_CFLAGS + 4 * k, contents)
        struct.pack_into(">i", b, _B_AXIAL_SFLAGS + 4 * k, surface_flags)
    struct.pack_into(">I", b, _B_NUMVERTS, numverts)
    struct.pack_into(">I", b, _B_VERTS, verts_ptr)
    return bytes(b)


def box_corner_verts(mins: Vec3, maxs: Vec3) -> bytes:
    """The eight box-corner verts (vec3, 96 bytes), in cod2map's order (the order
    of n_box_all2's brush verts): the four mins-x corners then the four maxs-x."""
    x0, y0, z0 = mins
    x1, y1, z1 = maxs
    order = (
        (x0, y0, z0), (x0, y1, z0), (x0, y1, z1), (x0, y0, z1),
        (x1, y0, z0), (x1, y0, z1), (x1, y1, z1), (x1, y1, z0),
    )
    out = bytearray()
    for v in order:
        out += struct.pack(">3f", *v)
    return bytes(out)


def inline_leaf_node(contents: int, brush_indices: list[int]) -> dict:
    """A ``cLeafBrushNode_s`` leaf element node holding its brush indices inline
    (data pointer -1), as the handler stores a parsed one: ``{"_t", "raw",
    "brushes"}``. The shape of n_box_all2's nodes 33..51."""
    if not brush_indices:
        raise ClipError("inline_leaf_node: expected at least one brush index")
    r = bytearray(CLEAFBRUSHNODE_SIZE)
    struct.pack_into(">b", r, _N_AXIS, 0)
    struct.pack_into(">h", r, _N_COUNT, len(brush_indices))
    struct.pack_into(">i", r, _N_CONTENTS, contents)
    struct.pack_into(">I", r, _N_DATA, _PTR_INLINE)
    brushes = b"".join(struct.pack(">H", i) for i in brush_indices)
    return {"_t": "cLeafBrushNode_s", "raw": bytes(r), "brushes": brushes}


def boxes_overlap(a_min: Vec3, a_max: Vec3, b_min: Vec3, b_max: Vec3) -> bool:
    """Whether two axis-aligned boxes share any volume (touching faces count)."""
    return all(a_min[k] <= b_max[k] and b_min[k] <= a_max[k] for k in range(3))


# -- the clipMap view ------------------------------------------------------------------------


@dataclass
class BrushInfo:
    mins: Vec3
    maxs: Vec3
    contents: int
    numsides: int
    numverts: int
    verts_ptr: int


class ClipMap:
    """A thin typed view over a parsed clipMap node (the handler's dict). Reads
    and writes the header counts and the brush / leaf / kd-tree arrays in place.
    Node-only: it never needs the zone layout."""

    def __init__(self, node: dict):
        self.node = node

    # header counts ------------------------------------------------------------------------

    def _hget(self, off: int, u16: bool = False) -> int:
        fmt = ">H" if u16 else ">I"
        return struct.unpack_from(fmt, self.node["header"], off)[0]

    def _hset(self, off: int, value: int, u16: bool = False) -> None:
        h = bytearray(self.node["header"])
        struct.pack_into(">H" if u16 else ">I", h, off, value)
        self.node["header"] = bytes(h)

    @property
    def num_brushes(self) -> int:
        return self._hget(_H_NUM_BRUSHES, u16=True)

    @property
    def num_leafs(self) -> int:
        return self._hget(_H_NUM_LEAFS)

    @property
    def lbn_count(self) -> int:
        return self._hget(_H_LBN_COUNT)

    # brushes ------------------------------------------------------------------------------

    def brush(self, i: int) -> BrushInfo:
        o = i * CBRUSH_SIZE
        b = self.node["brushes"]
        return BrushInfo(
            mins=struct.unpack_from(">3f", b, o + _B_MINS),
            maxs=struct.unpack_from(">3f", b, o + _B_MAXS),
            contents=struct.unpack_from(">i", b, o + _B_CONTENTS)[0],
            numsides=struct.unpack_from(">I", b, o + _B_NUMSIDES)[0],
            numverts=struct.unpack_from(">I", b, o + _B_NUMVERTS)[0],
            verts_ptr=struct.unpack_from(">I", b, o + _B_VERTS)[0],
        )

    def set_brush_bytes(self, i: int, data: bytes) -> None:
        if len(data) != CBRUSH_SIZE:
            raise ClipError(f"brush {i}: expected {CBRUSH_SIZE} bytes, got {len(data)}")
        b = bytearray(self.node["brushes"])
        b[i * CBRUSH_SIZE : (i + 1) * CBRUSH_SIZE] = data
        self.node["brushes"] = bytes(b)

    def append_brush(self, data: bytes) -> int:
        if len(data) != CBRUSH_SIZE:
            raise ClipError(f"append_brush: expected {CBRUSH_SIZE} bytes, got {len(data)}")
        index = self.num_brushes
        self.node["brushes"] = self.node["brushes"] + data
        self._hset(_H_NUM_BRUSHES, index + 1, u16=True)
        return index

    def drop_last_brush(self) -> None:
        n = self.num_brushes
        self.node["brushes"] = self.node["brushes"][: (n - 1) * CBRUSH_SIZE]
        self._hset(_H_NUM_BRUSHES, n - 1, u16=True)

    def brush_verts_base(self) -> int | None:
        """The block-memory offset of the ``brushVerts`` pool (the smallest brush
        verts pointer), or None when no brush owns verts."""
        best = None
        for i in range(self.num_brushes):
            br = self.brush(i)
            if br.numverts and br.verts_ptr:
                off = (br.verts_ptr - 1) & _OFFSET_MASK
                best = off if best is None else min(best, off)
        return best

    def set_brush_verts(self, i: int, verts: bytes) -> None:
        """Rewrite a brush's eight corner verts in the shared pool, in place (same
        size), leaving its pointer valid. Only for a brush that already owns verts."""
        br = self.brush(i)
        if not (br.numverts and br.verts_ptr):
            raise ClipError(f"brush {i}: owns no verts to rewrite")
        base = self.brush_verts_base()
        inner = ((br.verts_ptr - 1) & _OFFSET_MASK) - base
        bv = bytearray(self.node["brush_verts"])
        if inner < 0 or inner + len(verts) > len(bv):
            raise ClipError(f"brush {i}: verts run {inner}..{inner + len(verts)} outside the pool")
        bv[inner : inner + len(verts)] = verts
        self.node["brush_verts"] = bytes(bv)

    # leaves -------------------------------------------------------------------------------

    def leaf_box(self, i: int) -> tuple[Vec3, Vec3]:
        o = i * CLEAF_SIZE
        leafs = self.node["leafs"]
        return (
            struct.unpack_from(">3f", leafs, o + _L_MINS),
            struct.unpack_from(">3f", leafs, o + _L_MAXS),
        )

    def leaf_root(self, i: int) -> int:
        return struct.unpack_from(">i", self.node["leafs"], i * CLEAF_SIZE + _L_LEAFBRUSHNODE)[0]

    def set_leaf_root(self, i: int, node_index: int) -> None:
        leafs = bytearray(self.node["leafs"])
        struct.pack_into(">i", leafs, i * CLEAF_SIZE + _L_LEAFBRUSHNODE, node_index)
        self.node["leafs"] = bytes(leafs)

    def or_leaf_contents(self, i: int, contents: int) -> None:
        leafs = bytearray(self.node["leafs"])
        o = i * CLEAF_SIZE + _L_BRUSH_CONTENTS
        was = struct.unpack_from(">i", leafs, o)[0]
        struct.pack_into(">i", leafs, o, was | contents)
        self.node["leafs"] = bytes(leafs)

    # kd-tree nodes ------------------------------------------------------------------------

    @property
    def nodes(self) -> list[dict]:
        return self.node["leafbrush_nodes"]

    def _node_count(self, i: int) -> int:
        return struct.unpack_from(">h", self.nodes[i]["raw"], _N_COUNT)[0]

    def inline_brush_list(self, i: int) -> list[int] | None:
        """The brush indices a node lists inline (data pointer -1), or None when
        the node is a split or references the flat leafBrushes pool."""
        element = self.nodes[i]
        raw = element["raw"]
        count = struct.unpack_from(">h", raw, _N_COUNT)[0]
        data = struct.unpack_from(">I", raw, _N_DATA)[0]
        if count <= 0 or data != _PTR_INLINE:
            return None
        extra = element.get("brushes")
        if extra is None:
            return None
        return [struct.unpack_from(">H", extra, 2 * k)[0] for k in range(count)]

    def set_inline_brush_list(self, i: int, brush_indices: list[int], contents: int) -> None:
        element = self.nodes[i]
        raw = bytearray(element["raw"])
        struct.pack_into(">h", raw, _N_COUNT, len(brush_indices))
        struct.pack_into(">i", raw, _N_CONTENTS, contents)
        struct.pack_into(">I", raw, _N_DATA, _PTR_INLINE)
        element["raw"] = bytes(raw)
        element["brushes"] = b"".join(struct.pack(">H", b) for b in brush_indices)

    def flat_brushes(self) -> list[int]:
        fb = self.node["leafbrushes"]
        return [struct.unpack_from(">H", fb, 2 * k)[0] for k in range(len(fb) // 2)]

    def set_flat_brushes(self, indices: list[int]) -> None:
        self.node["leafbrushes"] = b"".join(struct.pack(">H", i) for i in indices)
        self._hset(_H_NUM_LEAFBRUSHES, len(indices))

    def _flat_base(self) -> int | None:
        """Block-memory offset of the flat leafBrushes pool: the smallest address
        any pool-backed leaf node points at (each points at the start of its own
        run). None when no node is pool-backed."""
        offs = []
        for element in self.nodes:
            raw = element["raw"]
            count = struct.unpack_from(">h", raw, _N_COUNT)[0]
            data = struct.unpack_from(">I", raw, _N_DATA)[0]
            if count > 0 and data not in (_PTR_NULL, _PTR_INLINE):
                offs.append((data - 1) & _OFFSET_MASK)
        return min(offs) if offs else None

    def node_brush_list(self, i: int) -> list[int]:
        """Every brush index a kd-tree leaf node lists, inline or via the pool."""
        raw = self.nodes[i]["raw"]
        count = struct.unpack_from(">h", raw, _N_COUNT)[0]
        if count <= 0:
            return []
        inline = self.inline_brush_list(i)
        if inline is not None:
            return inline
        data = struct.unpack_from(">I", raw, _N_DATA)[0]
        base = self._flat_base()
        if base is None:
            return []
        start = (((data - 1) & _OFFSET_MASK) - base) // 2
        flat = self.flat_brushes()
        return flat[start : start + count]

    def reachable_brushes(self, leaf_index: int) -> set[int]:
        """Every brush index reachable from a BSP leaf, walking its leafBrushNode
        kd-tree and visiting both children of every split. This is the structural
        test of 'is this brush referenced from this leaf'."""
        return self._reach(self.leaf_root(leaf_index), set())

    def _reach(self, node_index: int, seen: set[int]) -> set[int]:
        if node_index in seen or node_index < 0 or node_index >= len(self.nodes):
            return set()
        seen.add(node_index)
        raw = self.nodes[node_index]["raw"]
        count = struct.unpack_from(">h", raw, _N_COUNT)[0]
        if count > 0:
            return set(self.node_brush_list(node_index))
        out: set[int] = set()
        for child_off in (_N_CHILD0, _N_CHILD1):
            rel = struct.unpack_from(">H", raw, child_off)[0]
            if rel:
                out |= self._reach(node_index + rel, seen)
        return out

    def append_inline_node(self, contents: int, brush_indices: list[int]) -> int:
        index = len(self.nodes)
        self.nodes.append(inline_leaf_node(contents, brush_indices))
        self._hset(_H_LBN_COUNT, index + 1)
        return index


# -- high-level operations -------------------------------------------------------------------


def _is_attachable(cm: ClipMap, leaf: int) -> bool:
    """A leaf is attachable when it holds no cod2map brushes: it reaches nothing,
    or its root is an inline node this module created earlier."""
    root = cm.leaf_root(leaf)
    if root <= 0 or not cm.reachable_brushes(leaf):
        return True
    return cm.inline_brush_list(root) is not None


def _attach_targets(cm: ClipMap, mins: Vec3, maxs: Vec3) -> list[int]:
    """The leaves to reference a clip box from. A trace lands in the BSP leaf the
    cNode tree routes its region to; computing that exactly needs the shared plane
    pool (another asset) and the device-confirmed child convention, so this uses
    the leaf boxes the parse gives: every attachable leaf whose own box overlaps
    the clip. When none do (a map like n_box whose leaves are tight around
    existing collision, with open space routed to a zero-volume catch-all leaf),
    it falls back to the attachable zero-volume catch-all leaves. Over-inclusion
    is harmless: a brush's own bounds gate the hit."""
    overlapping = []
    catch_all = []
    for i in range(cm.num_leafs):
        if not _is_attachable(cm, i):
            continue
        lo, hi = cm.leaf_box(i)
        if lo == hi:
            catch_all.append(i)
        elif boxes_overlap(mins, maxs, lo, hi):
            overlapping.append(i)
    return overlapping or catch_all


def _attach(cm: ClipMap, leaf_index: int, brush_index: int, contents: int) -> None:
    """Reference ``brush_index`` from a leaf: extend its inline node when it has
    one, else give it a fresh inline single-brush node."""
    root = cm.leaf_root(leaf_index)
    inline = cm.inline_brush_list(root) if root > 0 else None
    if inline is not None:
        if brush_index not in inline:
            was = struct.unpack_from(">i", cm.nodes[root]["raw"], _N_CONTENTS)[0]
            cm.set_inline_brush_list(root, inline + [brush_index], was | contents)
    else:
        node_index = cm.append_inline_node(contents, [brush_index])
        cm.set_leaf_root(leaf_index, node_index)
    cm.or_leaf_contents(leaf_index, contents)


def add_clip(
    cm: ClipMap,
    mins: Vec3,
    maxs: Vec3,
    contents: int = PLAYER_CLIP_CONTENTS,
    surface_flags: int = PLAYER_CLIP_SURFACE,
) -> int:
    """Add an axis-aligned clip brush and reference it from the attachable BSP
    leaf(s) for its footprint (see ``_attach_targets``). Returns the new brush
    index. Raises ``ClipError`` when the clipMap has no attachable leaf at all."""
    targets = _attach_targets(cm, mins, maxs)
    if not targets:
        raise ClipError("clipMap has no empty BSP leaf to reference the clip from")
    brush_index = cm.append_brush(clip_cbrush(mins, maxs, contents, surface_flags))
    for leaf_index in targets:
        _attach(cm, leaf_index, brush_index, contents)
    return brush_index


def move_clip(cm: ClipMap, brush_index: int, mins: Vec3, maxs: Vec3) -> None:
    """Move a clip brush to new bounds: rewrite the ``cbrush_t`` (its six axial
    planes follow mins/maxs), rewrite its corner verts in place when it owns any,
    and re-evaluate the leaves that reference it. Raises ``ClipError`` when the
    new box reaches no empty leaf."""
    br = cm.brush(brush_index)
    if br.numsides:
        raise ClipError(f"brush {brush_index} is not axis-aligned (has {br.numsides} sides)")
    targets = _attach_targets(cm, mins, maxs)
    if not targets:
        raise ClipError("clipMap has no empty BSP leaf to reference the moved clip from")
    cm.set_brush_bytes(
        brush_index,
        clip_cbrush(mins, maxs, br.contents, _surface_of(cm, brush_index),
                    verts_ptr=br.verts_ptr, numverts=br.numverts),
    )
    if br.numverts and br.verts_ptr:
        cm.set_brush_verts(brush_index, box_corner_verts(mins, maxs))
    keep = set(targets)
    for leaf_index in range(cm.num_leafs):
        if leaf_index in keep:
            continue
        root = cm.leaf_root(leaf_index)
        inline = cm.inline_brush_list(root) if root > 0 else None
        if inline is None or brush_index not in inline:
            continue
        rest = [b for b in inline if b != brush_index]
        if rest:
            contents = struct.unpack_from(">i", cm.nodes[root]["raw"], _N_CONTENTS)[0]
            cm.set_inline_brush_list(root, rest, contents)
        else:
            cm.set_leaf_root(leaf_index, 0)
    for leaf_index in targets:
        _attach(cm, leaf_index, brush_index, br.contents)


def _surface_of(cm: ClipMap, brush_index: int) -> int:
    o = brush_index * CBRUSH_SIZE + _B_AXIAL_SFLAGS
    return struct.unpack_from(">i", cm.node["brushes"], o)[0]


def _empty_node(cm: ClipMap, node_index: int) -> None:
    """Turn a kd-tree node into an empty one (count 0, no children): it reaches
    no brushes. Leaves the element in place so no node index or split child
    offset moves."""
    raw = bytearray(CLEAFBRUSHNODE_SIZE)
    cm.nodes[node_index]["raw"] = bytes(raw)
    cm.nodes[node_index]["brushes"] = None


def remove_clip(cm: ClipMap, brush_index: int) -> None:
    """Remove a clip brush: drop every reference to it from the kd-tree leaf
    nodes and the flat pool, renumber the higher brush indices down by one, and
    drop the ``cbrush_t`` record. Orphaned verts and emptied kd-tree nodes are
    left in place (harmless, and renumbering node indices would move split child
    offsets)."""
    n = cm.num_brushes
    if not 0 <= brush_index < n:
        raise ClipError(f"brush {brush_index}: out of range 0..{n - 1}")
    if brush_index in cm.flat_brushes():
        raise ClipError(
            f"brush {brush_index} is referenced through the flat leafBrushes pool "
            "(cod2map-built); this module removes only brushes it added (inline-referenced)"
        )

    def renum(i: int) -> int:
        return i - 1 if i > brush_index else i

    for node_index in range(len(cm.nodes)):
        inline = cm.inline_brush_list(node_index)
        if inline is None:
            continue
        kept = [renum(b) for b in inline if b != brush_index]
        if kept:
            contents = struct.unpack_from(">i", cm.nodes[node_index]["raw"], _N_CONTENTS)[0]
            cm.set_inline_brush_list(node_index, kept, contents)
        else:
            _empty_node(cm, node_index)
            for leaf_index in range(cm.num_leafs):
                if cm.leaf_root(leaf_index) == node_index:
                    cm.set_leaf_root(leaf_index, 0)

    flat = cm.flat_brushes()
    if flat:
        cm.set_flat_brushes([renum(b) for b in flat])  # length kept (guarded above)

    b = bytearray(cm.node["brushes"])
    del b[brush_index * CBRUSH_SIZE : (brush_index + 1) * CBRUSH_SIZE]
    cm.node["brushes"] = bytes(b)
    cm._hset(_H_NUM_BRUSHES, n - 1, u16=True)


# -- footprint and clusters (a stock prop's collision is a cluster of clips) ------------------


def _add3(a: Vec3, d: Vec3) -> Vec3:
    return (a[0] + d[0], a[1] + d[1], a[2] + d[2])


def clips_in_footprint(
    cm: ClipMap,
    mins: Vec3,
    maxs: Vec3,
    within: bool = True,
    contents: int | None = None,
) -> list[int]:
    """The axis-aligned clip brushes under a footprint, by AABB. ``within`` keeps
    only brushes whose box lies inside [mins, maxs] (the cluster at a prop's
    footprint: a stock prop is a bus-sized brush plus thinner shell brushes, all
    inside its footprint); ``within=False`` keeps any brush whose box overlaps.
    ``contents`` filters to one contents value (e.g. ``PLAYER_CLIP_CONTENTS``).
    Non-axial brushes (numsides>0) are skipped."""
    out = []
    for i in range(cm.num_brushes):
        br = cm.brush(i)
        if br.numsides:
            continue
        if contents is not None and br.contents != contents:
            continue
        if within:
            if all(mins[k] <= br.mins[k] and br.maxs[k] <= maxs[k] for k in range(3)):
                out.append(i)
        elif boxes_overlap(mins, maxs, br.mins, br.maxs):
            out.append(i)
    return out


def translate_clip(cm: ClipMap, brush_index: int, delta: Vec3) -> None:
    """Move one clip brush by a translation, keeping its size (``move_clip`` to
    absolute bounds)."""
    br = cm.brush(brush_index)
    move_clip(cm, brush_index, _add3(br.mins, delta), _add3(br.maxs, delta))


def move_cluster(cm: ClipMap, brush_indices: list[int], delta: Vec3) -> None:
    """Move a whole clip cluster (a prop's set of brushes) by the same
    translation, keeping the cluster's shape."""
    for i in brush_indices:
        translate_clip(cm, i, delta)


def disable_clip(cm: ClipMap, brush_index: int) -> None:
    """Make a clip brush non-solid in place: zero its contents and its six axial
    contents flags, leaving the record and its BSP references untouched. This is
    the safe way to drop a stock (cod2map, flat-referenced) prop's collision,
    where removing the record would have to shift the shared leafBrushes pool.
    The brush stays in the array (harmless) but collides with nothing."""
    b = bytearray(cm.node["brushes"])
    o = brush_index * CBRUSH_SIZE
    struct.pack_into(">i", b, o + _B_CONTENTS, 0)
    for k in range(6):
        struct.pack_into(">i", b, o + _B_AXIAL_CFLAGS + 4 * k, 0)
    cm.node["brushes"] = bytes(b)


def remove_cluster(cm: ClipMap, brush_indices: list[int]) -> None:
    """Remove a whole clip cluster. Brushes this module added (inline-referenced)
    are removed outright (record dropped, indices renumbered); stock brushes
    (referenced through the shared leafBrushes pool) are disabled in place. Mixed
    clusters are handled: the inline ones are removed last, in descending index
    order, so renumbering stays valid."""
    indices = sorted(set(brush_indices), reverse=True)
    flat = set(cm.flat_brushes())
    for i in indices:
        if i in flat:
            disable_clip(cm, i)
    for i in indices:
        if i not in flat:
            remove_clip(cm, i)
