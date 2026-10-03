"""col_map_sp (13) and col_map_mp (14): clipMap_t, 0x14c bytes, one loader for
both. docs/research/structs-map.md section 7 (the table's last column is the
load order followed here). Loaders: Ptr 0x250588, struct 0x24e848.
"""

from __future__ import annotations

from opent5.xfile.constants import AssetType, Block
from opent5.xfile.handlers.base import Handler, array, asset_ref, blob, register, runtime
from opent5.xfile.handlers.physics import PHYS_CONSTRAINT_SIZE, read_phys_constraint
from opent5.xfile.stream import Chunk, XStream

DYN_ENT_DEF_SIZE = 0x54
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
#: mp_firingrange (docs/research/structs-map.md section 13.4).
CONVERTED = {
    "static_model_list": ((0x4, True),),  # XModel alias
    "brushsides": ((0x0, False),),  # plane
    "nodes": ((0x0, False),),  # plane
    "partitions": ((0x10, False),),  # borders
    "brushes": ((0x20, False), (0x58, False)),  # sides, verts
}


def _convert_all(st: XStream, table: Chunk | None, size: int, count: int, key: str) -> None:
    fields = CONVERTED.get(key)
    if table is None or not fields:
        return
    for i in range(count):
        for off, alias in fields:
            st.convert(table, size * i + off, alias)


def _plain(st: XStream, h: Chunk, key: str, off: int, mask: int, size: int, count: int):
    table = array(st, h, off, mask, size * count)
    _convert_all(st, table, size, count, key)
    return blob(table)


def read_dyn_ent_def(st: XStream, d: Chunk) -> dict:
    return {
        "xmodel": asset_ref(st, d, 0x20, AssetType.XMODEL),
        "destroyed_xmodel": asset_ref(st, d, 0x24, AssetType.XMODEL),
        "destroy_fx": asset_ref(st, d, 0x2C, AssetType.FX),
        "phys_preset": asset_ref(st, d, 0x38, AssetType.PHYSPRESET),
        "raw": d.bytes(),
    }


def read_clipmap(st: XStream, h: Chunk) -> dict:
    st.push(Block.VIRTUAL)
    out: dict = {"name": st.string(h, 0), "is_in_use": h.u32(4)}
    for key, off, mask, size, count_at in _ARRAYS_1:
        out[key] = _plain(st, h, key, off, mask, size, h.u32(count_at))
    count = h.u32(0x38)
    nodes = array(st, h, 0x3C, 3, 0x14 * count)
    out["leafbrush_nodes"] = blob(nodes)
    out["leafbrush_node_brushes"] = None
    if nodes is not None:
        brushes = []
        for node in nodes.items(0x14, count):
            n = node.s16(2)
            brushes.append(blob(array(st, node, 8, 1, 2 * n)) if n > 0 else None)
        out["leafbrush_node_brushes"] = brushes
    for key, off, mask, size, count_at in _ARRAYS_2:
        out[key] = _plain(st, h, key, off, mask, size, h.u32(count_at))
    tri_count = h.u32(0x68)
    out["tri_edge_is_walkable"] = blob(array(st, h, 0x70, 0, ((3 * tri_count + 31) // 32) * 4))
    for key, off, mask, size, count_at in _ARRAYS_3:
        out[key] = _plain(st, h, key, off, mask, size, h.u32(count_at))
    out["brushes"] = _plain(st, h, "brushes", 0x98, 15, 0x60, h.u16(0x94))
    out["visibility"] = blob(array(st, h, 0xA4, 0, h.u32(0x9C) * h.u32(0xA0)))
    out["map_ents"] = asset_ref(st, h, 0xAC, AssetType.MAP_ENTS)
    out["box_brush"] = blob(array(st, h, 0xB0, 15, 0x60))
    dyn_counts = [h.u16(0xFE + 2 * k) for k in range(4)]
    out["dyn_ent_count"] = dyn_counts
    defs_out = []
    for k in range(2):
        defs = array(st, h, 0x108 + 4 * k, 3, DYN_ENT_DEF_SIZE * dyn_counts[k])
        if defs is None:
            defs_out.append(None)
        else:
            defs_out.append(
                [read_dyn_ent_def(st, d) for d in defs.items(DYN_ENT_DEF_SIZE, dyn_counts[k])]
            )
    out["dyn_ent_def_list"] = defs_out
    # RUNTIME lists: pose[2], client[2], server[2], coll[4].
    runtime(st, h, 0x110, 3, 0x20 * dyn_counts[0])
    runtime(st, h, 0x114, 3, 0x20 * dyn_counts[1])
    runtime(st, h, 0x118, 3, 0x14 * dyn_counts[0])
    runtime(st, h, 0x11C, 3, 0x14 * dyn_counts[1])
    runtime(st, h, 0x120, 3, 8 * dyn_counts[2])
    runtime(st, h, 0x124, 3, 8 * dyn_counts[3])
    for k in range(4):
        runtime(st, h, 0x128 + 4 * k, 3, 0x20 * dyn_counts[k])
    constraint_count = h.u32(0x138)
    constraints = array(st, h, 0x13C, 3, PHYS_CONSTRAINT_SIZE * constraint_count)
    out["constraints"] = None
    if constraints is not None:
        out["constraints"] = [
            read_phys_constraint(st, c)
            for c in constraints.items(PHYS_CONSTRAINT_SIZE, constraint_count)
        ]
    runtime(st, h, 0x144, 3, ROPE_SIZE * h.u32(0x140))
    out["max_ropes"] = h.u32(0x140)
    out["checksum"] = h.u32(0x148)
    out["header"] = h.bytes()
    st.pop()
    return out


@register
class ClipMapSpHandler(Handler):
    asset_type = AssetType.COL_MAP_SP
    header_size = 0x14C

    def read(self, st: XStream, header: Chunk) -> dict:
        return read_clipmap(st, header)


@register
class ClipMapMpHandler(Handler):
    asset_type = AssetType.COL_MAP_MP
    header_size = 0x14C

    def read(self, st: XStream, header: Chunk) -> dict:
        return read_clipmap(st, header)
