"""xmodel (5): XModel (0xf8), XSurface (0x5c) and collision.
docs/research/structs-map.md section 9.

Vertex streams of most formats go to PHYSICAL. Loaders: Ptr 0x24c998, struct
0x24bc88, XSurface 0x238db8, collision tree 0x235f48, PhysGeomList 0x23acbc,
BrushWrapper 0x23a788.
"""

from __future__ import annotations

from opent5.xfile.constants import AssetType, Block
from opent5.xfile.handlers.base import Handler, array, asset_ref, items, register
from opent5.xfile.stream import Chunk, XStream

XSURFACE_SIZE = 0x5C


def collision_tree(io: XStream, node: dict) -> None:
    ct = io.load(0x28, node, "raw")
    array(io, ct, 0x1C, 15, 16 * ct.u32(0x18), node, "nodes")
    array(io, ct, 0x24, 1, 2 * ct.u32(0x20), node, "leafs")


def xsurface(io: XStream, s: Chunk, node: dict) -> None:
    flags, vert_count, tri_count = s.u16(2), s.u16(4), s.u16(6)
    v = [s.s16(0xC + 2 * k) for k in range(4)]
    array(io, s, 0x14, 1, 2 * (v[0] + 3 * v[1] + 5 * v[2] + 7 * v[3]), node, "verts_blend")
    array(io, s, 0x18, 3, 48 * (v[0] + v[1] + v[2] + v[3]), node, "tension_data")
    if flags & 1 == 0:
        array(io, s, 0x1C, 15, 16 * vert_count, node, "verts0")
    elif flags & 3 == 1:
        array(io, s, 0x1C, 7, 8 * vert_count, node, "verts0")
    else:
        array(io, s, 0x1C, 15, 16 * vert_count, node, "verts0")
    fmt = flags & 7
    # (block, align mask, bytes per vertex) of the vertex stream, by format; None: none.
    layout = {
        0: (Block.PHYSICAL, 15, 16),
        1: (Block.PHYSICAL, 15, 16),
        2: (None, 15, 16),
        3: (Block.PHYSICAL, 7, 8),
        5: (Block.PHYSICAL, 3, 12),
        7: (Block.PHYSICAL, 3, 4),
    }.get(fmt)
    if layout is not None:
        block, mask, size = layout
        if block is not None:
            io.push(block)
        array(io, s, 0x24, mask, size * vert_count, node, "vertex_stream")
        if block is not None:
            io.pop()
    lists = items(io, s, 0x2C, 3, 12, s.u8(1), node, "vert_lists")
    for vl, element in lists or ():
        if io.follows(vl, 8):
            io.alloc(3)
            collision_tree(io, element.setdefault("collision_tree", {}))
    array(io, s, 0x8, 15, 6 * tri_count, node, "tri_indices")


def brush_wrapper(io: XStream, node: dict) -> None:
    b = io.load(0x60, node, "raw")
    for side, element in items(io, b, 0x20, 3, 0xC, b.u32(0x1C), node, "sides") or ():
        array(io, side, 0, 3, 0x14, element, "plane")
    array(io, b, 0x58, 3, 0xC * b.u32(0x54), node, "verts")
    io.convert(b, 0x5C)  # converted only (offset pointers seen; never loaded)


def collmap(io: XStream, chunk: Chunk, off: int, node: dict) -> None:
    geom_list = array(io, chunk, off, 3, 0xC, node, "geom_list")
    if geom_list is None:
        return
    count = geom_list.u32(0)
    for g, geom in items(io, geom_list, 4, 15, 0x44, count, node, "geoms") or ():
        if io.follows(g, 0):
            io.alloc(15)
            brush_wrapper(io, geom.setdefault("brush", {}))


def xmodel_body(io: XStream, h: Chunk, node: dict) -> None:
    io.push(Block.VIRTUAL)
    io.string(h, 0, node, "name")
    bones, roots, surf_count = h.u8(4), h.u8(5), h.u8(6)
    array(io, h, 0x8, 1, 2 * bones, node, "bone_names")
    array(io, h, 0xC, 0, bones - roots, node, "parent_list")
    array(io, h, 0x10, 1, 8 * (bones - roots), node, "quats")
    array(io, h, 0x14, 3, 16 * (bones - roots), node, "trans")
    array(io, h, 0x18, 0, bones, node, "part_classification")
    array(io, h, 0x1C, 3, 0x20 * bones, node, "base_mat")
    for s, surface in items(io, h, 0x20, 3, XSURFACE_SIZE, surf_count, node, "surfs") or ():
        xsurface(io, s, surface)
    for m, element in items(io, h, 0x24, 3, 4, surf_count, node, "materials") or ():
        asset_ref(io, m, 0, AssetType.MATERIAL, element, "material")
    array(io, h, 0xA0, 3, 0x24 * h.u32(0xA4), node, "coll_surfs")
    array(io, h, 0xAC, 3, 0x2C * bones, node, "bone_info")
    array(io, h, 0xD0, 3, 0x10 * surf_count, node, "high_mip_bounds")
    asset_ref(io, h, 0xE8, AssetType.PHYSPRESET, node, "phys_preset")
    for c, element in items(io, h, 0xF0, 3, 4, h.u8(0xEC), node, "collmaps") or ():
        collmap(io, c, 0, element)
    asset_ref(io, h, 0xF4, AssetType.PHYSCONSTRAINTS, node, "phys_constraints")
    io.pop()


@register
class XModelHandler(Handler):
    asset_type = AssetType.XMODEL
    header_size = 0xF8

    def body(self, io: XStream, header: Chunk, node: dict) -> None:
        xmodel_body(io, header, node)
