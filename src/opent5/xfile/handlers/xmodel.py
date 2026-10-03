"""xmodel (5): XModel (0xf8), XSurface (0x5c) and collision.
docs/research/structs-map.md section 9.

Vertex streams of most formats go to PHYSICAL. Loaders: Ptr 0x24c998, struct
0x24bc88, XSurface 0x238db8, collision tree 0x235f48, PhysGeomList 0x23acbc,
BrushWrapper 0x23a788.
"""

from __future__ import annotations

from opent5.xfile.constants import AssetType, Block
from opent5.xfile.handlers.base import Handler, array, asset_ref, blob, register
from opent5.xfile.stream import Chunk, XStream

XSURFACE_SIZE = 0x5C


def read_collision_tree(st: XStream) -> dict:
    ct = st.load(0x28)
    nodes = array(st, ct, 0x1C, 15, 16 * ct.u32(0x18))
    leafs = array(st, ct, 0x24, 1, 2 * ct.u32(0x20))
    return {"raw": ct.bytes(), "nodes": blob(nodes), "leafs": blob(leafs)}


def read_xsurface(st: XStream, s: Chunk) -> dict:
    flags, vert_count, tri_count = s.u16(2), s.u16(4), s.u16(6)
    v = [s.s16(0xC + 2 * k) for k in range(4)]
    out: dict = {
        "tile_mode": s.u8(0),
        "vert_list_count": s.u8(1),
        "flags": flags,
        "vert_count": vert_count,
        "tri_count": tri_count,
        "vert_info_counts": v,
    }
    out["verts_blend"] = blob(array(st, s, 0x14, 1, 2 * (v[0] + 3 * v[1] + 5 * v[2] + 7 * v[3])))
    out["tension_data"] = blob(array(st, s, 0x18, 3, 48 * (v[0] + v[1] + v[2] + v[3])))
    if flags & 1 == 0:
        verts0 = array(st, s, 0x1C, 15, 16 * vert_count)
    elif flags & 3 == 1:
        verts0 = array(st, s, 0x1C, 7, 8 * vert_count)
    else:
        verts0 = array(st, s, 0x1C, 15, 16 * vert_count)
    out["verts0"] = blob(verts0)
    fmt = flags & 7
    stream = None
    if fmt in (0, 1):
        st.push(Block.PHYSICAL)
        stream = array(st, s, 0x24, 15, 16 * vert_count)
        st.pop()
    elif fmt == 2:
        stream = array(st, s, 0x24, 15, 16 * vert_count)
    elif fmt == 5:
        st.push(Block.PHYSICAL)
        stream = array(st, s, 0x24, 3, 12 * vert_count)
        st.pop()
    elif fmt == 3:
        st.push(Block.PHYSICAL)
        stream = array(st, s, 0x24, 7, 8 * vert_count)
        st.pop()
    elif fmt == 7:
        st.push(Block.PHYSICAL)
        stream = array(st, s, 0x24, 3, 4 * vert_count)
        st.pop()
    out["vertex_stream"] = blob(stream)
    lists_count = s.u8(1)
    vert_lists = array(st, s, 0x2C, 3, 12 * lists_count)
    out["vert_lists"] = None
    if vert_lists is not None:
        lists = []
        for vl in vert_lists.items(12, lists_count):
            tree = None
            if st.follows(vl, 8):
                st.alloc(3)
                tree = read_collision_tree(st)
            lists.append({"raw": vl.bytes(), "collision_tree": tree})
        out["vert_lists"] = lists
    out["tri_indices"] = blob(array(st, s, 0x8, 15, 6 * tri_count))
    out["raw"] = s.bytes()
    return out


def read_brush_wrapper(st: XStream) -> dict:
    b = st.load(0x60)
    count = b.u32(0x1C)
    sides = array(st, b, 0x20, 3, 0xC * count)
    planes = None
    if sides is not None:
        planes = [blob(array(st, side, 0, 3, 0x14)) for side in sides.items(0xC, count)]
    verts = array(st, b, 0x58, 3, 0xC * b.u32(0x54))
    st.convert(b, 0x5C)  # converted only (offset pointers seen; never loaded)
    return {"raw": b.bytes(), "sides": blob(sides), "side_planes": planes, "verts": blob(verts)}


def read_collmap(st: XStream, chunk: Chunk, off: int) -> dict | None:
    geom_list = array(st, chunk, off, 3, 0xC)
    if geom_list is None:
        return None
    count = geom_list.u32(0)
    geoms = array(st, geom_list, 4, 15, 0x44 * count)
    out: dict = {"raw": geom_list.bytes(), "geoms": None}
    if geoms is not None:
        infos = []
        for g in geoms.items(0x44, count):
            brush = None
            if st.follows(g, 0):
                st.alloc(15)
                brush = read_brush_wrapper(st)
            infos.append({"raw": g.bytes(), "brush": brush})
        out["geoms"] = infos
    return out


def read_xmodel(st: XStream, h: Chunk) -> dict:
    st.push(Block.VIRTUAL)
    out: dict = {"name": st.string(h, 0)}
    bones, roots, surf_count = h.u8(4), h.u8(5), h.u8(6)
    out.update(num_bones=bones, num_root_bones=roots, numsurfs=surf_count)
    names = array(st, h, 0x8, 1, 2 * bones)
    out["bone_names"] = None if names is None else [names.u16(2 * i) for i in range(bones)]
    out["parent_list"] = blob(array(st, h, 0xC, 0, bones - roots))
    out["quats"] = blob(array(st, h, 0x10, 1, 8 * (bones - roots)))
    out["trans"] = blob(array(st, h, 0x14, 3, 16 * (bones - roots)))
    out["part_classification"] = blob(array(st, h, 0x18, 0, bones))
    out["base_mat"] = blob(array(st, h, 0x1C, 3, 0x20 * bones))
    surfs = array(st, h, 0x20, 3, XSURFACE_SIZE * surf_count)
    out["surfs"] = None
    if surfs is not None:
        out["surfs"] = [read_xsurface(st, s) for s in surfs.items(XSURFACE_SIZE, surf_count)]
    handles = array(st, h, 0x24, 3, 4 * surf_count)
    out["materials"] = None
    if handles is not None:
        out["materials"] = [
            asset_ref(st, handles, 4 * i, AssetType.MATERIAL) for i in range(surf_count)
        ]
    out["coll_surfs"] = blob(array(st, h, 0xA0, 3, 0x24 * h.u32(0xA4)))
    out["bone_info"] = blob(array(st, h, 0xAC, 3, 0x2C * bones))
    out["high_mip_bounds"] = blob(array(st, h, 0xD0, 3, 0x10 * surf_count))
    out["phys_preset"] = asset_ref(st, h, 0xE8, AssetType.PHYSPRESET)
    collmap_count = h.u8(0xEC)
    collmaps = array(st, h, 0xF0, 3, 4 * collmap_count)
    out["collmaps"] = None
    if collmaps is not None:
        out["collmaps"] = [read_collmap(st, collmaps, 4 * i) for i in range(collmap_count)]
    out["phys_constraints"] = asset_ref(st, h, 0xF4, AssetType.PHYSCONSTRAINTS)
    out["header"] = h.bytes()
    st.pop()
    return out


@register
class XModelHandler(Handler):
    asset_type = AssetType.XMODEL
    header_size = 0xF8

    def read(self, st: XStream, header: Chunk) -> dict:
        return read_xmodel(st, header)
