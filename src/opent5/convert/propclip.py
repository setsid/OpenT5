"""Axis-aligned clip cbrushes for the v0.3.0 map editor (Route A of the spike,
docs/research/static-model-collision.md; the device test confirmed Route A and
rejected static-model collision).

A moved or added static-model prop stays solid the way cod2map makes the v0.2.0
box props solid: an axis-aligned clip ``cbrush_t`` in the clipMap, referenced
from the BSP leaf(s) it falls in so a trace finds it. A stock prop's collision is
a *cluster* of such cbrushes (a bus is a bus-sized brush plus thinner shell
brushes at its footprint), identifiable by their axis-aligned bounds. This module
adds, moves and removes these on a parsed ``col_map_mp`` / ``col_map_sp`` node,
keeping every count and reference consistent, and finds the cluster under a
footprint so a whole prop's clips move or go together.

How a cod2map clip is shaped (confirmed from out/demo/n_box_all2 and retail
mp_nuked):

- ``cbrush_t`` (0x60): mins (+0x0), contents (+0xC), maxs (+0x10), numsides=0
  (+0x1C), sides=NULL (+0x20), the six axial side-flag words axial_cflags[6]
  (+0x24) and axial_sflags[6] (+0x3C), numverts (+0x54) and a verts pointer
  (+0x58). An axis-aligned brush has no brushSides: its six faces are the axial
  planes taken from mins/maxs (opent5.export.collision.brush_planes). cod2map
  also writes the eight box-corner verts and points +0x58 at them.

- A trace reaches a brush through the BSP: a ``cNode_t`` tree to a ``cLeaf_s``,
  then the leaf's ``leafBrushNode`` index into the ``cLeafBrushNode_s`` kd-tree.
  A kd-tree node with leafBrushCount==0 is a split (inline dist/range and two
  forward, relative child offsets at +0x8); one with leafBrushCount>0 is a leaf
  listing that many brush indices. **A world leaf's brush list is always read
  through the flat ``leafBrushes`` pool** (the leaf node's +0x8 points into it);
  cod2map never gives a world leaf an *inline* brush list (+0x8 == -1). Inline
  lists are used only by submodel (cmodel) leaf nodes. The engine's world trace
  treats a world leaf node's +0x8 as a pointer into the pool, so an inline list
  under a world leaf makes it index the pool out of bounds and dereference float
  data as a pointer. T5's ``cLeaf_s`` has no firstLeafBrush/numLeafBrushes field;
  brushes are reached only through this kd-tree.

So an added brush is referenced the cod2map way: its index is appended to the
flat ``leafBrushes`` pool and each attachable world leaf is given a flat-backed
leaf node (+0x8 pointing into the pool). The editor drives this through
``opent5.xfile.remap.Rewrite``: parse for editing, pass the ``Rewrite`` to
``ClipMap`` so appends to the pool and the ``brushVerts`` tail resolve (the
Rewrite's ``append_identities``), call the operations, then ``Rewrite.build``.

Which leaves to reference the brush from is the whole game. The first device
clip was byte-valid, loader-clean and oracle-clean yet no trace ever hit it: it
was attached only to *empty* leaves (``add_clip`` / ``_attach_targets``), but a
trace at a prop descends the cNode BSP to the populated cod2map leaf that spans
that spot, and that leaf never listed the brush. So the clip must be referenced
from the exact world leaf a trace reaches. ``BspLocator`` walks the cNode tree
and the shared plane pool (resolved off the first node's plane pointer) to point-
locate the box's centre and corners, and ``add_clip_bsp`` / ``move_clip_bsp``
merge the brush into those leaves (``merge_brush_into_leaf``), OR-ing the clip's
contents into each leaf's ``brushContents`` and the listing node's ``contents``
so the trace does not early-out, then assert the brush is reachable from its
centre leaf. Use the ``*_bsp`` operations for a free-standing prop clip.

``add_clip`` / ``move_clip`` (empty-leaf attachment) are kept only for the
synthetic-clipMap tests and as a fallback when no BSP is available; on a real map
they place a clip no trace reaches, so the editor uses the ``*_bsp`` path.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

#: ``cbrush_t`` big-endian struct offsets.
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
_N_CHILD0 = 0x10
_N_CHILD1 = 0x12

CLEAF_SIZE = 0x2C
_L_BRUSH_CONTENTS = 0x04
_L_MINS = 0x0C
_L_MAXS = 0x18
_L_LEAFBRUSHNODE = 0x24

#: ``cNode_t`` (0x8) offsets: a plane pointer and two signed child indices
#: (negative child ``c`` is leaf ``-1 - c``; non-negative is a node index).
CNODE_SIZE = 0x08
_CN_PLANE = 0x00
_CN_CHILDREN = 0x04

#: ``cplane_s`` (0x14): normal (vec3) then dist (f32).
CPLANE_SIZE = 0x14

#: ``clipMap_t`` header count fields.
_H_NUM_LEAFS = 0x30
_H_LBN_COUNT = 0x38
_H_NUM_LEAFBRUSHES = 0x40
_H_NUM_BRUSHVERTS = 0x58
_H_NUM_BRUSHES = 0x94

#: Device-proven player-clip flags: the v0.2.0 box clip boxes carry contents
#: 0x8030200 and the surface word 0x440A0 on all six axial faces, solid on PS3.
PLAYER_CLIP_CONTENTS = 0x8030200
PLAYER_CLIP_SURFACE = 0x000440A0

_PTR_NULL = 0
_PTR_INLINE = 0xFFFFFFFF
_OFFSET_BLOCK_SHIFT = 29
_OFFSET_MASK = 0x1FFFFFFF

Vec3 = tuple[float, float, float]


class ClipError(Exception):
    """A clip edit the clipMap structure cannot take safely."""


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
    (the six axial planes come from mins/maxs), the contents on every axial cflag
    and ``surface_flags`` on every axial sflag. Matches the shape of a cod2map
    clip box of the same bounds."""
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


def flat_leaf_node(contents: int, data_ptr: int, count: int) -> dict:
    """A ``cLeafBrushNode_s`` leaf element node whose brush list is read through
    the flat ``leafBrushes`` pool (data pointer into the pool), as the handler
    stores a parsed one (no inline ``brushes``). This is cod2map's world-leaf
    shape."""
    if count <= 0:
        raise ClipError("flat_leaf_node: expected a positive brush count")
    r = bytearray(CLEAFBRUSHNODE_SIZE)
    struct.pack_into(">b", r, _N_AXIS, 0)
    struct.pack_into(">h", r, _N_COUNT, count)
    struct.pack_into(">i", r, _N_CONTENTS, contents)
    struct.pack_into(">I", r, _N_DATA, data_ptr)
    return {"_t": "cLeafBrushNode_s", "raw": bytes(r), "brushes": None}


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
    """A typed view over a parsed clipMap node (the handler's dict). Reads and
    writes the header counts and the brush / leaf / kd-tree / pool arrays in
    place.

    ``rewrite`` is the ``opent5.xfile.remap.Rewrite`` the node belongs to; it is
    required for ``add_clip`` / ``move_clip`` because appending to the flat
    ``leafBrushes`` pool and the ``brushVerts`` tail needs the Rewrite to resolve
    the appended-tail pointers (``append_identities``). The read-only views
    (``brush``, ``reachable_brushes``, ``clips_in_footprint``, the checker) work
    without it."""

    def __init__(self, node: dict, rewrite=None):
        self.node = node
        self.rewrite = rewrite
        #: Indices of the leaf nodes this view created, so a later attach extends
        #: its own node rather than treating it as a cod2map one.
        self._mine: set[int] = set()
        #: Pools already given their one-element boundary pad (see ``_append_pool``).
        self._padded: set[str] = set()

    # header counts ------------------------------------------------------------------------

    def _hget(self, off: int, u16: bool = False) -> int:
        return struct.unpack_from(">H" if u16 else ">I", self.node["header"], off)[0]

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

    # appending to tail-resolvable pools ---------------------------------------------------

    def _orig_alloc(self, key: str) -> tuple[int, int]:
        """The (block, memory offset) the original parse loaded ``node[key]`` at,
        from the Rewrite's event log (the original layout, not the edited node)."""
        if self.rewrite is None:
            raise ClipError(f"appending to {key!r} needs a Rewrite (pass rewrite= to ClipMap)")
        event = self.rewrite.event_of(("L", id(self.node), key))
        if event is None:
            raise ClipError(f"the clipMap has no {key!r} array to append to")
        rows = self.rewrite.xfile.log.table()
        return int(rows[event, 3]), int(rows[event, 4])

    def _append_pool(self, key: str, data: bytes, element_size: int) -> int:
        """Append ``data`` to ``node[key]`` and return the offset-pointer value of
        the appended bytes' start, which ``Rewrite.build`` maps to its new place
        (the Rewrite is told this allocation grows at its tail). The first append
        to a pool leaves one unused element at the original end, so no pointer of
        ours lands on the boundary with the allocation that follows the pool (see
        the remap's ``append_identities``)."""
        block, mem = self._orig_alloc(key)
        if key not in self._padded:
            self.node[key] = self.node[key] + bytes(element_size)
            self._padded.add(key)
            self.rewrite.append_identities.add(("L", id(self.node), key))
        inner = len(self.node[key])  # byte offset of the appended data in the pool
        self.node[key] = self.node[key] + data
        return ((block << _OFFSET_BLOCK_SHIFT) | ((mem + inner) & _OFFSET_MASK)) + 1

    def append_flat_run(self, brush_indices: list[int]) -> int:
        """Append a run of u16 brush indices to the flat ``leafBrushes`` pool;
        returns the pointer a flat leaf node uses to read them."""
        data = b"".join(struct.pack(">H", i) for i in brush_indices)
        ptr = self._append_pool("leafbrushes", data, 2)
        self._hset(_H_NUM_LEAFBRUSHES, len(self.node["leafbrushes"]) // 2)
        return ptr

    def append_verts(self, mins: Vec3, maxs: Vec3) -> int:
        """Append a brush's eight corner verts to the ``brushVerts`` pool; returns
        the pointer for the brush's +0x58 field. Returns 0 when the clipMap has no
        brushVerts pool (no brushes carry verts).

        PADDED append (the parked path): leaves one boundary element so the returned
        pointer resolves to the appended tail. This breaks brushVerts contiguity, so
        it is kept only for the synthetic tests; the device fix uses
        ``append_verts_contiguous`` (see its note)."""
        if self.node.get("brush_verts") is None:
            return 0
        ptr = self._append_pool("brush_verts", box_corner_verts(mins, maxs), 12)
        self._hset(_H_NUM_BRUSHVERTS, len(self.node["brush_verts"]) // 12)
        return ptr

    def append_verts_contiguous(self, mins: Vec3, maxs: Vec3) -> int:
        """Append a brush's eight corner verts to ``brushVerts`` with NO boundary
        pad, keeping the pool strictly contiguous in brush-index order.

        cod2map lays ``brushVerts`` out so that each brush's verts sit at the running
        sum of the earlier brushes' vert counts (verified on retail mp_nuked: 0
        mismatches across 5890 brushes), and the engine addresses a brush's collision
        verts by that running count, not by the stored ``verts`` pointer. The padded
        append (``append_verts`` / ``_append_pool``) inserts one unused vec3 at the
        pool's old end to keep the returned pointer off the allocation boundary; that
        one element shifts the appended brush off its running-count position, so the
        engine reads the pad (zeros) instead of the brush's corners and the brush
        collides with nothing. This is the cause of the p_clip_a walk-through on
        device (p_clip_a had a 12-byte gap at the appended brush; p_clip_move, which
        rewrites verts in place, had none and was solid).

        So the verts go at the pool's old end with no gap, and the returned pointer is
        the running-count position. Because an immediately following allocation
        (``uinds``) begins at that boundary, the Rewrite resolves this pointer to that
        neighbour rather than to the appended bytes; that is harmless because the
        engine ignores the stored pointer for collision verts (it uses the running
        count), and the appended bytes themselves are contiguous and correct."""
        if self.node.get("brush_verts") is None:
            return 0
        block, mem = self._orig_alloc("brush_verts")
        inner = len(self.node["brush_verts"])  # old end = the new brush's running-count position
        self.node["brush_verts"] = self.node["brush_verts"] + box_corner_verts(mins, maxs)
        self._hset(_H_NUM_BRUSHVERTS, len(self.node["brush_verts"]) // 12)
        return ((block << _OFFSET_BLOCK_SHIFT) | ((mem + inner) & _OFFSET_MASK)) + 1

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

    def _brush_verts_base(self) -> int | None:
        best = None
        for i in range(self.num_brushes):
            br = self.brush(i)
            if br.numverts and br.verts_ptr:
                off = (br.verts_ptr - 1) & _OFFSET_MASK
                best = off if best is None else min(best, off)
        return best

    def set_brush_verts(self, i: int, verts: bytes) -> None:
        """Rewrite a brush's corner verts in the pool, in place (same size),
        leaving its pointer valid. Only for a brush that already owns verts."""
        br = self.brush(i)
        if not (br.numverts and br.verts_ptr):
            raise ClipError(f"brush {i}: owns no verts to rewrite")
        base = self._brush_verts_base()
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

    def _node(self, i: int) -> bytes:
        return self.nodes[i]["raw"]

    def node_count(self, i: int) -> int:
        return struct.unpack_from(">h", self._node(i), _N_COUNT)[0]

    def node_data(self, i: int) -> int:
        return struct.unpack_from(">I", self._node(i), _N_DATA)[0]

    def is_inline(self, i: int) -> bool:
        """A leaf node whose brush list is inline (data == -1), not pool-backed."""
        return self.node_count(i) > 0 and self.node_data(i) == _PTR_INLINE

    def _flat_base(self) -> int | None:
        offs = []
        for e in self.nodes:
            raw = e["raw"]
            count = struct.unpack_from(">h", raw, _N_COUNT)[0]
            data = struct.unpack_from(">I", raw, _N_DATA)[0]
            if count > 0 and data not in (_PTR_NULL, _PTR_INLINE):
                offs.append((data - 1) & _OFFSET_MASK)
        return min(offs) if offs else None

    def node_brush_list(self, i: int) -> list[int]:
        """Every brush index a kd-tree leaf node lists, inline or via the pool."""
        count = self.node_count(i)
        if count <= 0:
            return []
        if self.node_data(i) == _PTR_INLINE:
            extra = self.nodes[i].get("brushes")
            if extra is None:
                return []
            return [struct.unpack_from(">H", extra, 2 * k)[0] for k in range(count)]
        base = self._flat_base()
        if base is None:
            return []
        start = (((self.node_data(i) - 1) & _OFFSET_MASK) - base) // 2
        fb = self.node["leafbrushes"]
        return [struct.unpack_from(">H", fb, 2 * (start + k))[0] for k in range(count)]

    def reachable_brushes(self, leaf_index: int) -> set[int]:
        """Every brush index reachable from a BSP leaf, walking its leafBrushNode
        kd-tree and visiting both children of every split."""
        return self._reach(self.leaf_root(leaf_index), set())

    def _reach(self, node_index: int, seen: set[int]) -> set[int]:
        if node_index in seen or node_index < 0 or node_index >= len(self.nodes):
            return set()
        seen.add(node_index)
        if self.node_count(node_index) > 0:
            return set(self.node_brush_list(node_index))
        raw = self._node(node_index)
        out: set[int] = set()
        for child_off in (_N_CHILD0, _N_CHILD1):
            rel = struct.unpack_from(">H", raw, child_off)[0]
            if rel:
                out |= self._reach(node_index + rel, seen)
        return out

    def append_flat_node(self, contents: int, brush_indices: list[int]) -> int:
        """Append a flat-backed leaf node (its run appended to the pool) and
        return its index; records it as one of ours."""
        ptr = self.append_flat_run(brush_indices)
        index = len(self.nodes)
        self.nodes.append(flat_leaf_node(contents, ptr, len(brush_indices)))
        self._hset(_H_LBN_COUNT, index + 1)
        self._mine.add(index)
        return index

    def set_flat_node(self, i: int, contents: int, brush_indices: list[int]) -> None:
        """Repoint one of our leaf nodes at a fresh run of ``brush_indices``."""
        ptr = self.append_flat_run(brush_indices)
        raw = bytearray(CLEAFBRUSHNODE_SIZE)
        struct.pack_into(">h", raw, _N_COUNT, len(brush_indices))
        struct.pack_into(">i", raw, _N_CONTENTS, contents)
        struct.pack_into(">I", raw, _N_DATA, ptr)
        self.nodes[i]["raw"] = bytes(raw)
        self.nodes[i]["brushes"] = None


# -- footprint ------------------------------------------------------------------------------


def clips_in_footprint(
    cm: ClipMap,
    mins: Vec3,
    maxs: Vec3,
    within: bool = True,
    contents: int | None = None,
) -> list[int]:
    """The axis-aligned clip brushes under a footprint, by AABB. ``within`` keeps
    only brushes whose box lies inside [mins, maxs] (the cluster at a prop's
    footprint); ``within=False`` keeps any brush whose box overlaps. ``contents``
    filters to one contents value. Non-axial brushes (numsides>0) are skipped."""
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


# -- attachment -----------------------------------------------------------------------------


def _is_attachable(cm: ClipMap, leaf: int) -> bool:
    """A leaf is attachable when it holds no cod2map brushes: it reaches nothing,
    or its root is a leaf node this view created."""
    root = cm.leaf_root(leaf)
    if root <= 0 or not cm.reachable_brushes(leaf):
        return True
    return root in cm._mine


def _attach_targets(cm: ClipMap, mins: Vec3, maxs: Vec3) -> list[int]:
    """The world leaves to reference a clip box from: every attachable leaf whose
    box overlaps it, else the attachable zero-volume catch-all leaves (a map whose
    leaves are tight around existing collision routes open space there)."""
    overlapping, catch_all = [], []
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
    """Reference ``brush_index`` from a world leaf through the flat pool: extend
    our leaf node there, or give the leaf a fresh flat-backed node."""
    root = cm.leaf_root(leaf_index)
    if root in cm._mine:
        have = cm.node_brush_list(root)
        if brush_index not in have:
            was = struct.unpack_from(">i", cm.nodes[root]["raw"], _N_CONTENTS)[0]
            cm.set_flat_node(root, was | contents, have + [brush_index])
    else:
        node_index = cm.append_flat_node(contents, [brush_index])
        cm.set_leaf_root(leaf_index, node_index)
    cm.or_leaf_contents(leaf_index, contents)


def _detach(cm: ClipMap, brush_index: int, keep: set[int]) -> None:
    """Drop ``brush_index`` from our leaf nodes on leaves not in ``keep``."""
    for leaf_index in range(cm.num_leafs):
        if leaf_index in keep:
            continue
        root = cm.leaf_root(leaf_index)
        if root not in cm._mine:
            continue
        have = cm.node_brush_list(root)
        if brush_index not in have:
            continue
        rest = [b for b in have if b != brush_index]
        if rest:
            was = struct.unpack_from(">i", cm.nodes[root]["raw"], _N_CONTENTS)[0]
            cm.set_flat_node(root, was, rest)
        else:
            cm.set_leaf_root(leaf_index, 0)


def _collapse_leaf(cm: ClipMap, leaf: int, rest: list[int]) -> None:
    """Make a world leaf reach exactly ``rest`` (and nothing else), the device-proven
    way: a single flat pool-backed leaf node listing ``rest``, the leaf root repointed
    at it. An empty ``rest`` points the leaf at node 0 (the all-zero node stock maps
    use for an empty leaf). The node's contents are the union of the kept brushes'
    contents, so the trace does not early-out on any of them."""
    root = cm.leaf_root(leaf)
    if not rest:
        cm.set_leaf_root(leaf, 0)
        return
    contents = 0
    for b in rest:
        if 0 <= b < cm.num_brushes:
            contents |= cm.brush(b).contents
    if root > 0 and cm.node_count(root) > 0:
        cm.set_flat_node(root, contents, rest)
        cm._mine.add(root)
    else:
        cm.set_leaf_root(leaf, cm.append_flat_node(contents, rest))


def detach_everywhere(cm: ClipMap, brush_index: int, keep: set[int]) -> None:
    """Drop ``brush_index`` from EVERY world leaf that reaches it (except those in
    ``keep``), including stock cod2map leaves the editor did not create. Each such
    leaf's reachable set minus the brush is collapsed into a fresh flat pool-backed
    node (as ``merge_brush_into_leaf`` does, which is device-proven solid). ``_detach``
    only touches our own leaf nodes, so it cannot clear a stock brush's original
    references; moving a stock prop's clip needs this, or the old spot keeps an
    invisible wall."""
    for leaf in range(cm.num_leafs):
        if leaf in keep:
            continue
        reachable = cm.reachable_brushes(leaf)
        if brush_index not in reachable:
            continue
        _collapse_leaf(cm, leaf, sorted(reachable - {brush_index}))


# -- operations -----------------------------------------------------------------------------


def add_clip(
    cm: ClipMap,
    mins: Vec3,
    maxs: Vec3,
    contents: int = PLAYER_CLIP_CONTENTS,
    surface_flags: int = PLAYER_CLIP_SURFACE,
) -> int:
    """Add an axis-aligned clip brush (with its eight corner verts) and reference
    it the cod2map way from the attachable world leaf(s) for its footprint.
    Returns the new brush index. Raises ``ClipError`` when the clipMap has no
    attachable leaf."""
    targets = _attach_targets(cm, mins, maxs)
    if not targets:
        raise ClipError("clipMap has no empty BSP leaf to reference the clip from")
    verts_ptr = cm.append_verts(mins, maxs)
    numverts = 8 if verts_ptr else 0
    brush = clip_cbrush(mins, maxs, contents, surface_flags, verts_ptr, numverts)
    brush_index = cm.append_brush(brush)
    for leaf_index in targets:
        _attach(cm, leaf_index, brush_index, contents)
    return brush_index


def move_clip(cm: ClipMap, brush_index: int, mins: Vec3, maxs: Vec3) -> None:
    """Move a clip brush to new bounds: rewrite the ``cbrush_t`` (its six axial
    planes follow mins/maxs) and its corner verts in place, then re-evaluate the
    world leaves it is referenced from. Raises ``ClipError`` when the new box
    reaches no attachable leaf."""
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
    _detach(cm, brush_index, set(targets))
    for leaf_index in targets:
        _attach(cm, leaf_index, brush_index, br.contents)


def _surface_of(cm: ClipMap, brush_index: int) -> int:
    off = brush_index * CBRUSH_SIZE + _B_AXIAL_SFLAGS
    return struct.unpack_from(">i", cm.node["brushes"], off)[0]


def disable_clip(cm: ClipMap, brush_index: int) -> None:
    """Make a clip brush non-solid in place: zero its contents and the six axial
    contents flags, leaving the record and its references untouched. This is how a
    clip is removed: the shared leafBrushes pool can only grow at its tail, so the
    record and its pool entries stay (harmless) and the brush simply collides with
    nothing."""
    b = bytearray(cm.node["brushes"])
    o = brush_index * CBRUSH_SIZE
    struct.pack_into(">i", b, o + _B_CONTENTS, 0)
    for k in range(6):
        struct.pack_into(">i", b, o + _B_AXIAL_CFLAGS + 4 * k, 0)
    cm.node["brushes"] = bytes(b)


def remove_clip(cm: ClipMap, brush_index: int) -> None:
    """Remove a clip's collision: disable the brush in place (see ``disable_clip``)
    and drop it from the leaf nodes this view attached it to."""
    if not 0 <= brush_index < cm.num_brushes:
        raise ClipError(f"brush {brush_index}: out of range 0..{cm.num_brushes - 1}")
    disable_clip(cm, brush_index)
    _detach(cm, brush_index, set())


# -- clusters -------------------------------------------------------------------------------


def _add3(a: Vec3, d: Vec3) -> Vec3:
    return (a[0] + d[0], a[1] + d[1], a[2] + d[2])


def translate_clip(cm: ClipMap, brush_index: int, delta: Vec3) -> None:
    """Move one clip brush by a translation, keeping its size."""
    br = cm.brush(brush_index)
    move_clip(cm, brush_index, _add3(br.mins, delta), _add3(br.maxs, delta))


def move_cluster(cm: ClipMap, brush_indices: list[int], delta: Vec3) -> None:
    """Move a whole clip cluster (a prop's brushes) by the same translation,
    keeping the cluster's shape."""
    for i in brush_indices:
        translate_clip(cm, i, delta)


def remove_cluster(cm: ClipMap, brush_indices: list[int]) -> None:
    """Remove a whole clip cluster's collision (each brush disabled in place)."""
    for i in sorted(set(brush_indices)):
        remove_clip(cm, i)


def cluster_bounds(cm: ClipMap, brush_indices: list[int]) -> tuple[Vec3, Vec3] | None:
    """The axis-aligned bounds enclosing a cluster of clip brushes, or ``None`` when
    the cluster is empty. For showing which collision a move or delete will affect."""
    boxes = [cm.brush(i) for i in brush_indices]
    if not boxes:
        return None
    mins = tuple(min(b.mins[k] for b in boxes) for k in range(3))
    maxs = tuple(max(b.maxs[k] for b in boxes) for k in range(3))
    return mins, maxs


def translate_clip_bsp(cm: ClipMap, locator: BspLocator, brush_index: int, delta: Vec3) -> None:
    """Move one clip brush by a translation, keeping its size, and re-reference it
    from the world leaf(s) the new box reaches (the BSP path). Use this, not
    ``translate_clip``, on a real map so a trace finds the brush at its new place."""
    br = cm.brush(brush_index)
    move_clip_bsp(cm, locator, brush_index, _add3(br.mins, delta), _add3(br.maxs, delta))


def move_cluster_bsp(
    cm: ClipMap, locator: BspLocator, brush_indices: list[int], delta: Vec3
) -> None:
    """Move a whole clip cluster (a stock prop's brushes) by the same translation,
    the BSP way: the whole cluster is first detached from every world leaf that
    reaches any of its brushes (one leaf pass, so the OLD spot goes clear), each
    brush's record and verts are moved by ``delta``, and each brush is then re-leafed
    into the world leaves a trace at its new place reaches. ``numBrushes`` is
    unchanged.

    This is the move an editor applies to a stock prop (e.g. a bus): moving the
    existing cluster, not adding a new clip at the destination, so no leftover wall
    stays behind at the old position."""
    idxset = set(brush_indices)
    for i in idxset:
        if cm.brush(i).numsides:
            raise ClipError(f"brush {i} is not axis-aligned (has {cm.brush(i).numsides} sides)")
    # one leaf pass: strip the whole cluster from every leaf that reaches any of it.
    for leaf in range(cm.num_leafs):
        reachable = cm.reachable_brushes(leaf)
        if not (reachable & idxset):
            continue
        _collapse_leaf(cm, leaf, sorted(reachable - idxset))
    # move each brush's record and verts.
    d = tuple(float(v) for v in delta)
    for i in brush_indices:
        br = cm.brush(i)
        mn, mx = _add3(br.mins, d), _add3(br.maxs, d)
        cm.set_brush_bytes(
            i, clip_cbrush(mn, mx, br.contents, _surface_of(cm, i),
                           verts_ptr=br.verts_ptr, numverts=br.numverts),
        )
        if br.numverts and br.verts_ptr:
            cm.set_brush_verts(i, box_corner_verts(mn, mx))
    # re-leaf each brush into the world leaves its new box reaches.
    for i in brush_indices:
        br = cm.brush(i)
        leaves = locator.leaves_for_box(br.mins, br.maxs)
        if not leaves:
            raise ClipError(f"moved cluster brush {i} locates to no world leaf (outside the BSP)")
        for leaf in leaves:
            merge_brush_into_leaf(cm, leaf, i, br.contents)
        _assert_reachable(cm, locator, i, br.mins, br.maxs)


# -- offline check ---------------------------------------------------------------------------


def check_world_leaf_refs(cm: ClipMap) -> list[str]:
    """Problems that would crash the engine's world collision though the loader
    and the pointer oracle pass. A world leaf's brush list is read through the
    flat ``leafBrushes`` pool, so for every brush reachable from a ``cLeaf``:

    - no leaf node under a world leaf may list its brushes *inline* (data == -1);
      cod2map only does that for submodels, and an inline list under a world leaf
      makes the engine index the pool out of bounds (this is what crashed
      p_propclip: an added clip referenced by inline world-leaf nodes);
    - every flat-backed leaf node's pool offset and count must stay inside the
      pool and name brushes that exist.

    Empty when the clipMap is safe."""
    problems: list[str] = []
    nbrush = cm.num_brushes
    nflat = len(cm.node.get("leafbrushes") or b"") // 2
    base = cm._flat_base()
    for leaf in range(cm.num_leafs):
        root = cm.leaf_root(leaf)
        for node_index in _walk_nodes(cm, root):
            if cm.node_count(node_index) <= 0:
                continue
            if cm.node_data(node_index) == _PTR_INLINE:
                problems.append(
                    f"leaf {leaf}: leaf node {node_index} lists its brushes inline "
                    "(data == -1); world leaves must reference the flat leafBrushes pool"
                )
                continue
            if base is None:
                problems.append(f"leaf {leaf}: leaf node {node_index} is pool-backed but no pool")
                continue
            off = ((cm.node_data(node_index) - 1) & _OFFSET_MASK) - base
            start, count = off // 2, cm.node_count(node_index)
            if off < 0 or off % 2 or start + count > nflat:
                problems.append(
                    f"leaf {leaf}: leaf node {node_index} run {start}..{start + count} "
                    f"is outside the {nflat}-entry leafBrushes pool"
                )
                continue
            for b in cm.node_brush_list(node_index):
                if not 0 <= b < nbrush:
                    problems.append(
                        f"leaf {leaf}: leaf node {node_index} names brush {b}, out of "
                        f"0..{nbrush - 1}"
                    )
    return problems


def _walk_nodes(cm: ClipMap, root: int, seen: set[int] | None = None) -> list[int]:
    if seen is None:
        seen = set()
    if root in seen or root < 0 or root >= len(cm.nodes):
        return []
    seen.add(root)
    out = [root]
    if cm.node_count(root) <= 0:
        raw = cm._node(root)
        for child_off in (_N_CHILD0, _N_CHILD1):
            rel = struct.unpack_from(">H", raw, child_off)[0]
            if rel:
                out += _walk_nodes(cm, root + rel, seen)
    return out


def check_leaf_contents_masks(cm: ClipMap) -> list[str]:
    """Trace-skip problems: the engine's world trace early-outs on a leaf, and on
    a leaf-brush node, when the collision mask it is tracing for shares no bit with
    the leaf's ``brushContents`` / the node's ``contents``. So for a brush to be
    hit through a leaf, the brush's own contents bits must be a subset of both the
    ``cLeaf.brushContents`` of every leaf that reaches it and the ``contents`` of
    the leaf-brush node that lists it. cod2map keeps this true for every stock
    brush (verified on mp_nuked: 0 violations across 14730 reachable pairs); an
    edit that references a clip from a leaf/node without OR-ing the clip's contents
    in leaves the brush present but never hit. Empty when the clipMap is safe."""
    problems: list[str] = []
    for leaf in range(cm.num_leafs):
        lbc = struct.unpack_from(
            ">i", cm.node["leafs"], leaf * CLEAF_SIZE + _L_BRUSH_CONTENTS
        )[0]
        for node_index in _walk_nodes(cm, cm.leaf_root(leaf)):
            if cm.node_count(node_index) <= 0:
                continue
            ncont = struct.unpack_from(">i", cm._node(node_index), _N_CONTENTS)[0]
            for b in cm.node_brush_list(node_index):
                if not 0 <= b < cm.num_brushes:
                    continue
                bc = cm.brush(b).contents
                if bc & ~lbc:
                    problems.append(
                        f"leaf {leaf}: brush {b} contents {bc:#010x} is not a subset of "
                        f"the leaf's brushContents {lbc & 0xFFFFFFFF:#010x}; the trace "
                        "early-outs and never tests it"
                    )
                if bc & ~ncont:
                    problems.append(
                        f"leaf {leaf}: brush {b} contents {bc:#010x} is not a subset of "
                        f"leaf node {node_index} contents {ncont & 0xFFFFFFFF:#010x}"
                    )
    return problems


# -- BSP point location and leaf-correct attachment ------------------------------------------


class BspLocator:
    """Point-locates the clipMap's ``cNode_t`` BSP so a clip is referenced from the
    *exact* world leaf a trace descends to, not merely from an empty leaf whose box
    overlaps (which is what ``add_clip`` does, and why the first device clip was
    structurally valid yet never hit: the leaf a trace reaches at a prop is a
    populated cod2map leaf, and ``add_clip`` skips those). Needs the ``cNode_t``
    array (``node['nodes']``) and the shared plane pool, which lives in a separate
    allocation; ``from_xfile`` resolves it off the first node's plane pointer."""

    def __init__(self, nodes_bytes: bytes, planes_bytes: bytes, plane_offset_of):
        self._nodes = nodes_bytes
        self._planes = planes_bytes
        self._plane_offset_of = plane_offset_of
        self._count = len(nodes_bytes) // CNODE_SIZE

    @classmethod
    def from_xfile(cls, xf, node: dict) -> "BspLocator":
        nodes_bytes = node["nodes"]
        if not nodes_bytes:
            raise ClipError("clipMap has no cNode_t BSP to locate against")
        first_ptr = struct.unpack_from(">I", nodes_bytes, _CN_PLANE)[0]
        target = xf.resolve(first_ptr)
        if target is None or target.node is None:
            raise ClipError("cannot resolve the clipMap plane pool for BSP location")
        planes_bytes = target.node[target.key]

        def plane_offset_of(ptr: int) -> int:
            t = xf.resolve(ptr)
            if t is None:
                raise ClipError(f"unresolved plane pointer {ptr:#010x}")
            return t.within

        return cls(nodes_bytes, planes_bytes, plane_offset_of)

    def _plane(self, ptr: int) -> tuple[Vec3, float]:
        o = self._plane_offset_of(ptr)
        normal = struct.unpack_from(">3f", self._planes, o)
        dist = struct.unpack_from(">f", self._planes, o + 12)[0]
        return normal, dist

    def locate(self, point: Vec3) -> int | None:
        """The world-leaf index the point falls in, or ``None`` if the walk runs
        away (a malformed tree)."""
        ni = 0
        for _ in range(4 * self._count + 8):
            ptr = struct.unpack_from(">I", self._nodes, ni * CNODE_SIZE + _CN_PLANE)[0]
            c0, c1 = struct.unpack_from(">hh", self._nodes, ni * CNODE_SIZE + _CN_CHILDREN)
            normal, dist = self._plane(ptr)
            on_front = (
                normal[0] * point[0] + normal[1] * point[1] + normal[2] * point[2] - dist
            ) >= 0
            child = c0 if on_front else c1
            if child < 0:
                return -1 - child
            ni = child
        return None

    def leaves_for_box(self, mins: Vec3, maxs: Vec3) -> list[int]:
        """Every world leaf the box's centre and eight corners land in (deduped):
        the leaves a trace through the box can reach. Corners are pulled a hair
        inward so a face exactly on a splitting plane resolves to the inside
        leaf."""
        eps = 0.125
        lo = tuple(mins[k] + eps for k in range(3))
        hi = tuple(maxs[k] - eps for k in range(3))
        pts = [
            ((lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, (lo[2] + hi[2]) / 2),
            (lo[0], lo[1], lo[2]), (lo[0], lo[1], hi[2]), (lo[0], hi[1], lo[2]),
            (lo[0], hi[1], hi[2]), (hi[0], lo[1], lo[2]), (hi[0], lo[1], hi[2]),
            (hi[0], hi[1], lo[2]), (hi[0], hi[1], hi[2]),
        ]
        out: list[int] = []
        for p in pts:
            leaf = self.locate(p)
            if leaf is not None and leaf not in out:
                out.append(leaf)
        return out


def merge_brush_into_leaf(cm: ClipMap, leaf: int, brush_index: int, contents: int) -> None:
    """Add ``brush_index`` to the brushes a trace tests in ``leaf``, the cod2map
    way (through the flat ``leafBrushes`` pool), merging into a leaf that already
    holds cod2map brushes. The leaf's whole reachable set is collapsed into one
    fresh flat leaf node listing that set plus the new brush, and the leaf's root
    is repointed at it. Collapsing a kd sub-tree to a flat list is safe: every
    brush still gates on its own bounds, the test just visits them all. The leaf's
    ``brushContents`` and the node's contents are OR'd with ``contents`` so the
    trace does not early-out (see ``check_leaf_contents_masks``)."""
    reachable = cm.reachable_brushes(leaf)
    union = sorted(reachable | {brush_index})
    root = cm.leaf_root(leaf)
    node_contents = contents
    if root > 0:
        node_contents |= struct.unpack_from(">i", cm._node(root), _N_CONTENTS)[0]
    if root > 0 and cm.node_count(root) > 0:
        cm.set_flat_node(root, node_contents, union)
        cm._mine.add(root)
    else:
        node_index = cm.append_flat_node(node_contents, union)
        cm.set_leaf_root(leaf, node_index)
    cm.or_leaf_contents(leaf, contents)


def add_clip_bsp(
    cm: ClipMap,
    locator: BspLocator,
    mins: Vec3,
    maxs: Vec3,
    contents: int = PLAYER_CLIP_CONTENTS,
    surface_flags: int = PLAYER_CLIP_SURFACE,
) -> int:
    """Add an axis-aligned clip brush and reference it from the exact world
    leaf(s) a trace through its box reaches (``BspLocator``), so it is actually
    hit, then assert the brush is reachable from the box centre's leaf. Use this,
    not ``add_clip``, for a free-standing prop clip; raises ``ClipError`` if the
    box reaches no leaf or the post-attach reachability check fails.

    The verts are appended contiguously (``append_verts_contiguous``): an appended
    brush's verts must sit at the running-count position cod2map uses, or the engine
    reads the wrong bytes and the brush does not collide (the p_clip_a cause)."""
    leaves = locator.leaves_for_box(mins, maxs)
    if not leaves:
        raise ClipError("clip box locates to no world leaf (outside the BSP)")
    verts_ptr = cm.append_verts_contiguous(mins, maxs)
    numverts = 8 if verts_ptr else 0
    brush_index = cm.append_brush(
        clip_cbrush(mins, maxs, contents, surface_flags, verts_ptr, numverts)
    )
    for leaf in leaves:
        merge_brush_into_leaf(cm, leaf, brush_index, contents)
    _assert_reachable(cm, locator, brush_index, mins, maxs)
    return brush_index


def move_clip_bsp(cm: ClipMap, locator: BspLocator, brush_index: int, mins: Vec3, maxs: Vec3) -> None:
    """Move a clip brush to new bounds and re-reference it from the world leaf(s)
    the new box reaches. Drops it from EVERY leaf that currently reaches it (stock
    leaves included, so the old spot goes clear), rewrites the ``cbrush_t`` and its
    verts, then merges it into the new leaves and asserts reachability. The brush
    index and ``numBrushes`` do not change."""
    br = cm.brush(brush_index)
    if br.numsides:
        raise ClipError(f"brush {brush_index} is not axis-aligned (has {br.numsides} sides)")
    leaves = locator.leaves_for_box(mins, maxs)
    if not leaves:
        raise ClipError("moved clip box locates to no world leaf (outside the BSP)")
    # detach from the destination leaves too, then re-add cleanly below, so a brush that
    # barely moves (new leaves overlap old) still ends listed exactly once per leaf.
    detach_everywhere(cm, brush_index, set())
    cm.set_brush_bytes(
        brush_index,
        clip_cbrush(mins, maxs, br.contents, _surface_of(cm, brush_index),
                    verts_ptr=br.verts_ptr, numverts=br.numverts),
    )
    if br.numverts and br.verts_ptr:
        cm.set_brush_verts(brush_index, box_corner_verts(mins, maxs))
    for leaf in leaves:
        merge_brush_into_leaf(cm, leaf, brush_index, br.contents)
    _assert_reachable(cm, locator, brush_index, mins, maxs)


def _assert_reachable(cm: ClipMap, locator: BspLocator, brush_index: int, mins: Vec3, maxs: Vec3) -> None:
    """Fail loudly if the just-attached brush is not reachable from the leaf its
    centre locates to: the editor places clips in open space, so the centre leaf is
    the trace leaf, and a miss here is the bug that made the first device clip
    invisible to traces."""
    centre = tuple((mins[k] + maxs[k]) / 2 for k in range(3))
    leaf = locator.locate(centre)
    if leaf is None:
        raise ClipError(f"clip {brush_index} centre {centre} locates to no leaf")
    if brush_index not in cm.reachable_brushes(leaf):
        raise ClipError(
            f"clip {brush_index} is not reachable from its centre leaf {leaf}; "
            "a trace there would not hit it"
        )
