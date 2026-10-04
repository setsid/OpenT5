"""col_map_sp (13) and col_map_mp (14): clipMap_t, 0x14c bytes, one loader for
both. docs/research/structs-map.md section 7 (the table's last column is the
load order followed here). Loaders: Ptr 0x250588, struct 0x24e848.
"""

from __future__ import annotations

from opent5.xfile.constants import AssetType, Block
from opent5.xfile.handlers.base import Handler, array, asset_ref, items, register, runtime
from opent5.xfile.handlers.physics import PHYS_CONSTRAINT_SIZE, phys_constraint
from opent5.xfile.stream import Chunk, XStream

DYN_ENT_DEF_SIZE = 0x54
#: sizeof(rope_t) as the loader strides the RUNTIME max_ropes pool (clipMap_t.rope at 0x144).
#: Stock mp_nuked and the PC box both carry 32 ropes and reserve exactly at 0xC74.
ROPE_SIZE = 0xC74

#: (field, pointer offset, align mask, element size, count offset) for the plain
#: arrays loaded before leafbrushNodes, in load order.
_ARRAYS_1 = (
    ("planes", 0xC, 3, 0x14, 0x8),
    ("static_model_list", 0x14, 3, 0x50, 0x10),
    ("materials", 0x1C, 3, 0x48, 0x18),
    ("brushsides", 0x24, 3, 0xC, 0x20),
    ("nodes", 0x2C, 3, 8, 0x28),
    ("leafs", 0x34, 3, 0x2C, 0x30),
    ("leafbrushes", 0x44, 1, 2, 0x40),
)
#: The arrays after leafbrushNodes up to triIndices.
_ARRAYS_2 = (
    ("leafsurfaces", 0x4C, 3, 4, 0x48),
    ("verts", 0x54, 3, 12, 0x50),
    ("brush_verts", 0x5C, 3, 12, 0x58),
    ("uinds", 0x64, 1, 2, 0x60),
    ("tri_indices", 0x6C, 1, 6, 0x68),
)
#: The arrays after triEdgeIsWalkable.
_ARRAYS_3 = (
    ("borders", 0x78, 3, 0x1C, 0x74),
    ("partitions", 0x80, 3, 0x14, 0x7C),
    ("aabb_trees", 0x88, 15, 0x20, 0x84),
    ("cmodels", 0x90, 3, 0x48, 0x8C),
)


#: Pointer fields inside array elements that the loader converts but never loads:
#: (element offset, alias). From the game's loader run over mp_nuked and
#: mp_firingrange (docs/research/structs-map.md section 13.4, docs/research/walk-all.md 4.2).
CONVERTED = {
    "static_model_list": ((0x4, True),),  # XModel alias
    "brushsides": ((0x0, False),),  # plane
    "nodes": ((0x0, False),),  # plane
    "partitions": ((0x10, False),),  # borders
    "brushes": ((0x20, False), (0x58, False)),  # sides, verts
}


def _plain(
    io: XStream, h: Chunk, node: dict, key: str, off: int, mask: int, size: int, count: int
) -> None:
    """A plain array; then its converted-only pointer fields, element by element."""
    table = array(io, h, off, mask, size * count, node, key)
    fields = CONVERTED.get(key)
    if table is not None and fields:
        for i in range(count):
            for field_off, alias in fields:
                io.convert(table, size * i + field_off, alias)


def dyn_ent_def(io: XStream, d: Chunk, node: dict) -> None:
    asset_ref(io, d, 0x20, AssetType.XMODEL, node, "xmodel")
    asset_ref(io, d, 0x24, AssetType.XMODEL, node, "destroyed_xmodel")
    asset_ref(io, d, 0x2C, AssetType.FX, node, "destroy_fx")
    asset_ref(io, d, 0x38, AssetType.PHYSPRESET, node, "phys_preset")


def clipmap_body(io: XStream, h: Chunk, node: dict) -> None:
    io.push(Block.VIRTUAL)
    io.string(h, 0, node, "name")
    for key, off, mask, size, count_at in _ARRAYS_1:
        _plain(io, h, node, key, off, mask, size, h.u32(count_at))
    count = h.u32(0x38)
    for n, element in (
        items(io, h, 0x3C, 3, 0x14, count, node, "leafbrush_nodes", kind="cLeafBrushNode_s") or ()
    ):
        brushes = n.s16(2)
        if brushes > 0:
            array(io, n, 8, 1, 2 * brushes, element, "brushes")
    for key, off, mask, size, count_at in _ARRAYS_2:
        _plain(io, h, node, key, off, mask, size, h.u32(count_at))
    tri_count = h.u32(0x68)
    array(io, h, 0x70, 0, ((3 * tri_count + 31) // 32) * 4, node, "tri_edge_is_walkable")
    for key, off, mask, size, count_at in _ARRAYS_3:
        _plain(io, h, node, key, off, mask, size, h.u32(count_at))
    _plain(io, h, node, "brushes", 0x98, 15, 0x60, h.u16(0x94))
    array(io, h, 0xA4, 0, h.u32(0x9C) * h.u32(0xA0), node, "visibility")
    asset_ref(io, h, 0xAC, AssetType.MAP_ENTS, node, "map_ents")
    array(io, h, 0xB0, 15, 0x60, node, "box_brush")
    dyn_counts = [h.u16(0xFE + 2 * k) for k in range(4)]
    for k in range(2):
        key = f"dyn_ent_def_list{k}"
        defs = items(
            io, h, 0x108 + 4 * k, 3, DYN_ENT_DEF_SIZE, dyn_counts[k], node, key, kind="DynEntityDef"
        )
        for d, element in defs or ():
            dyn_ent_def(io, d, element)
    # RUNTIME lists: pose[2], client[2], server[2], coll[4].
    runtime(io, h, 0x110, 3, 0x20 * dyn_counts[0])
    runtime(io, h, 0x114, 3, 0x20 * dyn_counts[1])
    runtime(io, h, 0x118, 3, 0x14 * dyn_counts[0])
    runtime(io, h, 0x11C, 3, 0x14 * dyn_counts[1])
    runtime(io, h, 0x120, 3, 8 * dyn_counts[2])
    runtime(io, h, 0x124, 3, 8 * dyn_counts[3])
    for k in range(4):
        runtime(io, h, 0x128 + 4 * k, 3, 0x20 * dyn_counts[k])
    constraints = items(
        io,
        h,
        0x13C,
        3,
        PHYS_CONSTRAINT_SIZE,
        h.u32(0x138),
        node,
        "constraints",
        kind="PhysConstraint",
    )
    for c, element in constraints or ():
        phys_constraint(io, c, element)
    runtime(io, h, 0x144, 3, ROPE_SIZE * h.u32(0x140))
    io.pop()


@register
class ClipMapSpHandler(Handler):
    kind = "clipMap_t"
    asset_type = AssetType.COL_MAP_SP
    header_size = 0x14C

    def body(self, io: XStream, header: Chunk, node: dict) -> None:
        clipmap_body(io, header, node)


@register
class ClipMapMpHandler(Handler):
    kind = "clipMap_t"
    asset_type = AssetType.COL_MAP_MP
    header_size = 0x14C

    def body(self, io: XStream, header: Chunk, node: dict) -> None:
        clipmap_body(io, header, node)
