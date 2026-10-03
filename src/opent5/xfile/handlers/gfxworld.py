"""gfx_map (19): GfxWorld, 0x454 bytes on PS3. docs/research/structs-map.md section 8.

Field names are INFERRED from the PC member order (see the document); the
order of loads, sizes, alignments and blocks are CONFIRMED. "RT" fields are
RUNTIME: a -1 in the header, block memory reserved, no file bytes.
Loaders: Ptr 0x2521f8, struct 0x250f88; cells 0x239f88; light grid 0x236e20.
"""

from __future__ import annotations

from opent5.xfile.constants import AssetType, Block
from opent5.xfile.handlers.base import Handler, array, asset_ref, items, register, runtime
from opent5.xfile.stream import Chunk, XStream

CELL_SIZE = 0x38


def reflection_probe(io: XStream, p: Chunk, node: dict) -> None:
    """GfxReflectionProbe (24): +0xc image ref, +0x10 probeVolumes [nz] (align 4, 96 x
    u32 +0x14), loaded in that order (0x24b070 .. 0x24b168)."""
    asset_ref(io, p, 0xC, AssetType.IMAGE, node, "image")
    array(io, p, 0x10, 3, 96 * p.u32(0x14), node, "probe_volumes", owned=True)


def cell(io: XStream, c: Chunk, node: dict) -> None:
    for t, tree in items(io, c, 0x1C, 3, 0x28, c.u32(0x18), node, "aabb_tree") or ():
        array(io, t, 0x20, 1, 2 * t.u16(0x1E), tree, "smodel_indexes")
    for p, portal in items(io, c, 0x24, 3, 0x44, c.u32(0x20), node, "portals") or ():
        io.convert(p, 0x20)  # cell, converted only
        array(io, p, 0x24, 3, 0xC * p.u8(0x28), portal, "vertices")
    array(io, c, 0x34, 0, c.u8(0x30), node, "reflection_probes")


def gfxworld_body(io: XStream, h: Chunk, node: dict) -> None:
    io.push(Block.VIRTUAL)
    io.string(h, 0, node, "name")
    io.string(h, 4, node, "base_name")
    planes, nodes, surfaces = h.u32(8), h.u32(0xC), h.u32(0x10)
    array(io, h, 0x18, 3, 0x20 * h.u32(0x14), node, "aabb_trees")
    array(io, h, 0x20, 3, 4 * h.u32(0x1C), node, "leaf_refs")
    array(io, h, 0x3C, 3, 4 * h.u32(0x38), node, "sky_start_surfs")
    asset_ref(io, h, 0x40, AssetType.IMAGE, node, "sky_image")
    io.string(h, 0x48, node, "sky_box_model")
    sun = node.setdefault("sun_light", {})
    sun_raw = array(io, h, 0x100, 15, 0x170, sun, "raw")
    if sun_raw is not None:
        asset_ref(io, sun_raw, 0x160, AssetType.LIGHTDEF, sun, "def")  # GfxLight.def
    array(io, h, 0x120, 3, 0x20 * h.u32(0x11C), node, "coronas")
    array(io, h, 0x128, 3, 0x10 * h.u32(0x124), node, "shadow_map_volumes")
    array(io, h, 0x130, 3, 0x10 * h.u32(0x12C), node, "shadow_map_volume_planes")
    array(io, h, 0x138, 3, 0x18 * h.u32(0x134), node, "exposure_volumes")
    array(io, h, 0x140, 3, 0x10 * h.u32(0x13C), node, "exposure_volume_planes")
    cells = h.u32(0x154)
    array(io, h, 0x158, 3, 0x14 * planes, node, "planes")
    array(io, h, 0x15C, 1, 2 * nodes, node, "nodes")
    runtime(io, h, 0x160, 3, 4 * 0x200 * cells)  # sceneEntCellBits
    for c, element in items(io, h, 0x168, 3, CELL_SIZE, cells, node, "cells") or ():
        cell(io, c, element)
    # draw
    probe_count = h.u32(0x16C)
    for p, probe in items(io, h, 0x170, 3, 0x18, probe_count, node, "reflection_probes") or ():
        reflection_probe(io, p, probe)
    runtime(io, h, 0x174, 3, 0x18 * probe_count)  # reflectionProbeTextures
    lightmap_count = h.u32(0x178)
    for m, lightmap in items(io, h, 0x17C, 3, 0xC, lightmap_count, node, "lightmaps") or ():
        for k in range(3):
            asset_ref(io, m, 4 * k, AssetType.IMAGE, lightmap, k)
    for off in (0x180, 0x184, 0x188):  # lightmap textures
        runtime(io, h, off, 3, 0x18 * lightmap_count)
    draw_images = node.setdefault("draw_images", {})
    for off in range(0x18C, 0x208, 4):
        asset_ref(io, h, off, AssetType.IMAGE, draw_images, off)
    array(io, h, 0x20C, 15, 0x10 * h.u32(0x208), node, "vertices")
    io.push(Block.PHYSICAL)
    array(io, h, 0x218, 0, h.u32(0x214), node, "vertex_layer_data")
    io.pop()
    array(io, h, 0x228, 1, 2 * h.u32(0x224), node, "indices")
    # light grid at +0x230
    row_axis = h.u32(0x244)
    lo, hi = h.u16(0x238 + 2 * row_axis), h.u16(0x23E + 2 * row_axis)
    grid = node.setdefault("light_grid", {})
    array(io, h, 0x24C, 1, 2 * (hi - lo + 1), grid, "row_data_start")
    array(io, h, 0x254, 3, h.u32(0x250), grid, "raw_row_data")
    array(io, h, 0x25C, 3, 4 * h.u32(0x258), grid, "entries")
    array(io, h, 0x264, 3, 0xA8 * h.u32(0x260), grid, "colors")
    array(io, h, 0x26C, 3, 0x3C * h.u32(0x268), node, "models")
    for m, element in items(io, h, 0x290, 3, 8, h.u32(0x28C), node, "material_memory") or ():
        asset_ref(io, m, 0, AssetType.MATERIAL, element, "material")
    asset_ref(io, h, 0x298, AssetType.MATERIAL, node, "sun_sprite_material")
    asset_ref(io, h, 0x29C, AssetType.MATERIAL, node, "sun_flare_material")
    asset_ref(io, h, 0x334, AssetType.IMAGE, node, "outdoor_image")
    sun_index, light_count = h.u32(0x110), h.u32(0x114)
    dyn_client = (h.u32(0x3D4), h.u32(0x3D8))
    shadow_lights = light_count - sun_index - 1
    runtime(io, h, 0x338, 3, 4 * cells * ((cells + 31) // 32))  # cellCasterBits
    runtime(io, h, 0x33C, 3, 6 * dyn_client[0])  # sceneDynModel
    runtime(io, h, 0x340, 3, 4 * dyn_client[1])  # sceneDynBrush (0x251830)
    runtime(io, h, 0x344, 3, 4 * 0x2000 * shadow_lights)
    runtime(io, h, 0x348, 3, 4 * dyn_client[0] * shadow_lights)
    runtime(io, h, 0x34C, 3, 4 * dyn_client[1] * shadow_lights)
    runtime(io, h, 0x350, 0, dyn_client[0])
    for e, geom in items(io, h, 0x354, 3, 0xC, light_count, node, "shadow_geom") or ():
        array(io, e, 4, 1, 2 * e.u16(0), geom, "sorted_surf_index")
        array(io, e, 8, 1, 2 * e.u16(2), geom, "smodel_index")
    for e, region in items(io, h, 0x358, 3, 8, light_count, node, "light_region") or ():
        for hull, element in items(io, e, 4, 3, 0x50, e.u32(0), region, "hulls") or ():
            array(io, hull, 0x4C, 3, 0x14 * hull.u32(0x48), element, "axis")
    # dpvs static
    smodel_count, static_surface_count = h.u32(0x35C), h.u32(0x364)
    smodel_vis, surface_vis = h.u32(0x380), h.u32(0x384)
    for k in range(3):
        runtime(io, h, 0x388 + 4 * k, 127, 4 * smodel_vis)
    for k in range(3):
        runtime(io, h, 0x394 + 4 * k, 127, 4 * surface_vis)
    runtime(io, h, 0x3A0, 127, 4 * smodel_vis)
    runtime(io, h, 0x3A4, 127, 4 * surface_vis)
    runtime(io, h, 0x3A8, 127, 8 * smodel_vis)
    array(io, h, 0x3AC, 1, 2 * static_surface_count, node, "sorted_surf_index")
    array(io, h, 0x3B0, 3, 0x28 * smodel_count, node, "smodel_insts")
    for s, surface in items(io, h, 0x3B4, 15, 0x60, surfaces, node, "surfaces") or ():
        asset_ref(io, s, 0x40, AssetType.MATERIAL, surface, "material")
    # cullGroups: 32 x cullGroupCount (+0x118); [nz] (0x250c64)
    array(io, h, 0x3B8, 3, 0x20 * h.u32(0x118), node, "cull_groups", owned=True)
    for d, inst in items(io, h, 0x3BC, 3, 0x2C, smodel_count, node, "smodel_draw_insts") or ():
        asset_ref(io, d, 0x20, AssetType.XMODEL, inst, "model")
    runtime(io, h, 0x3C0, 3, 8 * static_surface_count)  # surfaceMaterials
    runtime(io, h, 0x3C4, 127, 4 * surface_vis)  # surfaceCastsSunShadow
    # dpvs dynamic
    words = (h.u32(0x3CC), h.u32(0x3D0))
    runtime(io, h, 0x3DC, 3, 4 * words[0] * cells)
    runtime(io, h, 0x3E0, 3, 4 * words[1] * cells)
    for k in range(3):
        runtime(io, h, 0x3E4 + 4 * k, 15, 32 * words[0])
        runtime(io, h, 0x3F0 + 4 * k, 15, 32 * words[1])
    # worldLod* and water buffers, all [nz] (0x251dc4 .. 0x251f78)
    array(io, h, 0x400, 3, 24 * h.u32(0x3FC), node, "world_lod_chains", owned=True)
    array(io, h, 0x408, 3, 12 * h.u32(0x404), node, "world_lod_infos", owned=True)
    array(io, h, 0x410, 3, 4 * h.u32(0x40C), node, "world_lod_surfaces", owned=True)
    array(io, h, 0x41C, 3, h.u32(0x418) & ~15, node, "water_buffer0", owned=True)
    array(io, h, 0x424, 3, h.u32(0x420) & ~15, node, "water_buffer1", owned=True)
    asset_ref(io, h, 0x428, AssetType.MATERIAL, node, "water_material")
    asset_ref(io, h, 0x42C, AssetType.MATERIAL, node, "corona_material")
    asset_ref(io, h, 0x430, AssetType.MATERIAL, node, "rope_material")
    array(io, h, 0x438, 3, 0x44 * h.u32(0x434), node, "occluders")
    # outdoorBounds 24 x +0x43c; heroLights 56 x +0x444; heroLightTree 24 x +0x448 (0x252010 ..)
    array(io, h, 0x440, 3, 24 * h.u32(0x43C), node, "outdoor_bounds", owned=True)
    array(io, h, 0x44C, 3, 56 * h.u32(0x444), node, "hero_lights", owned=True)
    array(io, h, 0x450, 3, 24 * h.u32(0x448), node, "hero_light_tree", owned=True)
    io.pop()


@register
class GfxWorldHandler(Handler):
    asset_type = AssetType.GFX_MAP
    header_size = 0x454

    def body(self, io: XStream, header: Chunk, node: dict) -> None:
        gfxworld_body(io, header, node)
