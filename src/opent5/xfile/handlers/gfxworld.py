"""gfx_map (19): GfxWorld, 0x454 bytes on PS3. docs/research/structs-map.md section 8.

Field names are INFERRED from the PC member order (see the document); the
order of loads, sizes, alignments and blocks are CONFIRMED. "RT" fields are
RUNTIME: a -1 in the header, block memory reserved, no file bytes.
Loaders: Ptr 0x2521f8, struct 0x250f88; cells 0x239f88; light grid 0x236e20.
"""

from __future__ import annotations

from opent5.xfile.constants import AssetType, Block
from opent5.xfile.handlers.base import Handler, array, asset_ref, blob, register, runtime
from opent5.xfile.stream import Chunk, XStream

CELL_SIZE = 0x38


def read_reflection_probe(st: XStream, p: Chunk) -> dict:
    """GfxReflectionProbe (24): +0xc image ref, +0x10 probeVolumes [nz] (align 4, 96 x
    u32 +0x14), loaded in that order (0x24b070 .. 0x24b168)."""
    image = asset_ref(st, p, 0xC, AssetType.IMAGE)
    volumes = array(st, p, 0x10, 3, 96 * p.u32(0x14), owned=True)
    return {"raw": p.bytes(), "image": image, "probe_volumes": blob(volumes)}


def read_cell(st: XStream, c: Chunk) -> dict:
    tree_count = c.u32(0x18)
    tree = array(st, c, 0x1C, 3, 0x28 * tree_count)
    smodel_indexes = None
    if tree is not None:
        smodel_indexes = [
            blob(array(st, t, 0x20, 1, 2 * t.u16(0x1E))) for t in tree.items(0x28, tree_count)
        ]
    portal_count = c.u32(0x20)
    portals = array(st, c, 0x24, 3, 0x44 * portal_count)
    portal_vertices = None
    if portals is not None:
        portal_vertices = []
        for p in portals.items(0x44, portal_count):
            st.convert(p, 0x20)  # cell, converted only
            portal_vertices.append(blob(array(st, p, 0x24, 3, 0xC * p.u8(0x28))))
    probes = array(st, c, 0x34, 0, c.u8(0x30))
    return {
        "aabb_tree": blob(tree),
        "aabb_smodel_indexes": smodel_indexes,
        "portals": blob(portals),
        "portal_vertices": portal_vertices,
        "reflection_probes": blob(probes),
        "raw": c.bytes(),
    }


def read_gfxworld(st: XStream, h: Chunk) -> dict:
    st.push(Block.VIRTUAL)
    out: dict = {"name": st.string(h, 0), "base_name": st.string(h, 4)}
    planes, nodes, surfaces = h.u32(8), h.u32(0xC), h.u32(0x10)
    out.update(plane_count=planes, node_count=nodes, surface_count=surfaces)
    out["aabb_trees"] = blob(array(st, h, 0x18, 3, 0x20 * h.u32(0x14)))
    out["leaf_refs"] = blob(array(st, h, 0x20, 3, 4 * h.u32(0x1C)))
    out["sky_start_surfs"] = blob(array(st, h, 0x3C, 3, 4 * h.u32(0x38)))
    out["sky_image"] = asset_ref(st, h, 0x40, AssetType.IMAGE)
    out["sky_box_model"] = st.string(h, 0x48)
    sun = array(st, h, 0x100, 15, 0x170)
    out["sun_light"] = None
    if sun is not None:
        light_def = asset_ref(st, sun, 0x160, AssetType.LIGHTDEF)  # GfxLight.def
        out["sun_light"] = {"raw": sun.bytes(), "def": light_def}
    out["coronas"] = blob(array(st, h, 0x120, 3, 0x20 * h.u32(0x11C)))
    out["shadow_map_volumes"] = blob(array(st, h, 0x128, 3, 0x10 * h.u32(0x124)))
    out["shadow_map_volume_planes"] = blob(array(st, h, 0x130, 3, 0x10 * h.u32(0x12C)))
    out["exposure_volumes"] = blob(array(st, h, 0x138, 3, 0x18 * h.u32(0x134)))
    out["exposure_volume_planes"] = blob(array(st, h, 0x140, 3, 0x10 * h.u32(0x13C)))
    cells = h.u32(0x154)
    out["cell_count"] = cells
    out["planes"] = blob(array(st, h, 0x158, 3, 0x14 * planes))
    out["nodes"] = blob(array(st, h, 0x15C, 1, 2 * nodes))
    runtime(st, h, 0x160, 3, 4 * 0x200 * cells)  # sceneEntCellBits
    cell_table = array(st, h, 0x168, 3, CELL_SIZE * cells)
    out["cells"] = None
    if cell_table is not None:
        out["cells"] = [read_cell(st, c) for c in cell_table.items(CELL_SIZE, cells)]
    # draw
    probe_count = h.u32(0x16C)
    probes = array(st, h, 0x170, 3, 0x18 * probe_count)
    out["reflection_probes"] = None
    if probes is not None:
        out["reflection_probes"] = [
            read_reflection_probe(st, p) for p in probes.items(0x18, probe_count)
        ]
    runtime(st, h, 0x174, 3, 0x18 * probe_count)  # reflectionProbeTextures
    lightmap_count = h.u32(0x178)
    lightmaps = array(st, h, 0x17C, 3, 0xC * lightmap_count)
    out["lightmaps"] = None
    if lightmaps is not None:
        out["lightmaps"] = [
            asset_ref(st, lightmaps, 4 * i, AssetType.IMAGE) for i in range(3 * lightmap_count)
        ]
    for off in (0x180, 0x184, 0x188):  # lightmap textures
        runtime(st, h, off, 3, 0x18 * lightmap_count)
    out["draw_images"] = [asset_ref(st, h, off, AssetType.IMAGE) for off in range(0x18C, 0x208, 4)]
    out["vertices"] = blob(array(st, h, 0x20C, 15, 0x10 * h.u32(0x208)))
    st.push(Block.PHYSICAL)
    out["vertex_layer_data"] = blob(array(st, h, 0x218, 0, h.u32(0x214)))
    st.pop()
    out["indices"] = blob(array(st, h, 0x228, 1, 2 * h.u32(0x224)))
    # light grid at +0x230
    row_axis = h.u32(0x244)
    lo, hi = h.u16(0x238 + 2 * row_axis), h.u16(0x23E + 2 * row_axis)
    out["light_grid"] = {
        "raw": h.data[0x230:0x268].tobytes(),
        "row_data_start": blob(array(st, h, 0x24C, 1, 2 * (hi - lo + 1))),
        "raw_row_data": blob(array(st, h, 0x254, 3, h.u32(0x250))),
        "entries": blob(array(st, h, 0x25C, 3, 4 * h.u32(0x258))),
        "colors": blob(array(st, h, 0x264, 3, 0xA8 * h.u32(0x260))),
    }
    out["models"] = blob(array(st, h, 0x26C, 3, 0x3C * h.u32(0x268)))
    memory_count = h.u32(0x28C)
    memory = array(st, h, 0x290, 3, 8 * memory_count)
    out["material_memory"] = None
    if memory is not None:
        out["material_memory"] = [
            {"raw": m.bytes(), "material": asset_ref(st, m, 0, AssetType.MATERIAL)}
            for m in memory.items(8, memory_count)
        ]
    out["sun_sprite_material"] = asset_ref(st, h, 0x298, AssetType.MATERIAL)
    out["sun_flare_material"] = asset_ref(st, h, 0x29C, AssetType.MATERIAL)
    out["outdoor_image"] = asset_ref(st, h, 0x334, AssetType.IMAGE)
    sun_index, light_count = h.u32(0x110), h.u32(0x114)
    dyn_client = (h.u32(0x3D4), h.u32(0x3D8))
    shadow_lights = light_count - sun_index - 1
    runtime(st, h, 0x338, 3, 4 * cells * ((cells + 31) // 32))  # cellCasterBits
    runtime(st, h, 0x33C, 3, 6 * dyn_client[0])  # sceneDynModel
    runtime(st, h, 0x340, 3, 4 * dyn_client[1])  # sceneDynBrush (0x251830)
    runtime(st, h, 0x344, 3, 4 * 0x2000 * shadow_lights)
    runtime(st, h, 0x348, 3, 4 * dyn_client[0] * shadow_lights)
    runtime(st, h, 0x34C, 3, 4 * dyn_client[1] * shadow_lights)
    runtime(st, h, 0x350, 0, dyn_client[0])
    shadow_geom = array(st, h, 0x354, 3, 0xC * light_count)
    out["shadow_geom"] = None
    if shadow_geom is not None:
        out["shadow_geom"] = [
            {
                "raw": e.bytes(),
                "sorted_surf_index": blob(array(st, e, 4, 1, 2 * e.u16(0))),
                "smodel_index": blob(array(st, e, 8, 1, 2 * e.u16(2))),
            }
            for e in shadow_geom.items(0xC, light_count)
        ]
    regions = array(st, h, 0x358, 3, 8 * light_count)
    out["light_region"] = None
    if regions is not None:
        region_list = []
        for e in regions.items(8, light_count):
            hull_count = e.u32(0)
            hulls = array(st, e, 4, 3, 0x50 * hull_count)
            axes = None
            if hulls is not None:
                axes = [
                    blob(array(st, hull, 0x4C, 3, 0x14 * hull.u32(0x48)))
                    for hull in hulls.items(0x50, hull_count)
                ]
            region_list.append({"raw": e.bytes(), "hulls": blob(hulls), "hull_axes": axes})
        out["light_region"] = region_list
    # dpvs static
    smodel_count, static_surface_count = h.u32(0x35C), h.u32(0x364)
    smodel_vis, surface_vis = h.u32(0x380), h.u32(0x384)
    for k in range(3):
        runtime(st, h, 0x388 + 4 * k, 127, 4 * smodel_vis)
    for k in range(3):
        runtime(st, h, 0x394 + 4 * k, 127, 4 * surface_vis)
    runtime(st, h, 0x3A0, 127, 4 * smodel_vis)
    runtime(st, h, 0x3A4, 127, 4 * surface_vis)
    runtime(st, h, 0x3A8, 127, 8 * smodel_vis)
    out["sorted_surf_index"] = blob(array(st, h, 0x3AC, 1, 2 * static_surface_count))
    out["smodel_insts"] = blob(array(st, h, 0x3B0, 3, 0x28 * smodel_count))
    surface_table = array(st, h, 0x3B4, 15, 0x60 * surfaces)
    out["surfaces"] = None
    if surface_table is not None:
        out["surfaces"] = [
            {"raw": s.bytes(), "material": asset_ref(st, s, 0x40, AssetType.MATERIAL)}
            for s in surface_table.items(0x60, surfaces)
        ]
    # cullGroups: 32 x cullGroupCount (+0x118); [nz] (0x250c64)
    out["cull_groups"] = blob(array(st, h, 0x3B8, 3, 0x20 * h.u32(0x118), owned=True))
    draw_insts = array(st, h, 0x3BC, 3, 0x2C * smodel_count)
    out["smodel_draw_insts"] = None
    if draw_insts is not None:
        out["smodel_draw_insts"] = [
            {"raw": d.bytes(), "model": asset_ref(st, d, 0x20, AssetType.XMODEL)}
            for d in draw_insts.items(0x2C, smodel_count)
        ]
    runtime(st, h, 0x3C0, 3, 8 * static_surface_count)  # surfaceMaterials
    runtime(st, h, 0x3C4, 127, 4 * surface_vis)  # surfaceCastsSunShadow
    # dpvs dynamic
    words = (h.u32(0x3CC), h.u32(0x3D0))
    runtime(st, h, 0x3DC, 3, 4 * words[0] * cells)
    runtime(st, h, 0x3E0, 3, 4 * words[1] * cells)
    for k in range(3):
        runtime(st, h, 0x3E4 + 4 * k, 15, 32 * words[0])
        runtime(st, h, 0x3F0 + 4 * k, 15, 32 * words[1])
    # worldLod* and water buffers, all [nz] (0x251dc4 .. 0x251f78)
    out["world_lod_chains"] = blob(array(st, h, 0x400, 3, 24 * h.u32(0x3FC), owned=True))
    out["world_lod_infos"] = blob(array(st, h, 0x408, 3, 12 * h.u32(0x404), owned=True))
    out["world_lod_surfaces"] = blob(array(st, h, 0x410, 3, 4 * h.u32(0x40C), owned=True))
    out["water_direction"] = h.f32(0x414)
    out["water_buffers"] = [
        blob(array(st, h, off + 4, 3, h.u32(off) & ~15, owned=True)) for off in (0x418, 0x420)
    ]
    out["water_material"] = asset_ref(st, h, 0x428, AssetType.MATERIAL)
    out["corona_material"] = asset_ref(st, h, 0x42C, AssetType.MATERIAL)
    out["rope_material"] = asset_ref(st, h, 0x430, AssetType.MATERIAL)
    out["occluders"] = blob(array(st, h, 0x438, 3, 0x44 * h.u32(0x434)))
    # outdoorBounds 24 x +0x43c; heroLights 56 x +0x444; heroLightTree 24 x +0x448 (0x252010 ..)
    out["outdoor_bounds"] = blob(array(st, h, 0x440, 3, 24 * h.u32(0x43C), owned=True))
    out["hero_lights"] = blob(array(st, h, 0x44C, 3, 56 * h.u32(0x444), owned=True))
    out["hero_light_tree"] = blob(array(st, h, 0x450, 3, 24 * h.u32(0x448), owned=True))
    out["header"] = h.bytes()
    st.pop()
    return out


@register
class GfxWorldHandler(Handler):
    asset_type = AssetType.GFX_MAP
    header_size = 0x454

    def read(self, st: XStream, header: Chunk) -> dict:
        return read_gfxworld(st, header)
