"""PC T5 zones (the PC Mod Tools output): container, platform, and the handlers whose
structs differ from PS3.

A PC ``.ff`` is ``IWffu100``, u32 LE 473, then one zlib stream (docs/research/pc-route.md
1.1); the inflated stream is the same XFile memory image as on PS3 with every scalar
little-endian and asset types numbered without the PS3-only types (pixelshader 7,
vertexshader 8, texturelist 44): PC 0..6 as PS3, PC 7..41 are PS3 + 2, PC 42 is PS3 45
(pc-route.md 1.3). ``PC`` is the ``Platform`` that parses one with the product walker:
the PS3 handlers for every struct the two platforms share (clipMap_t, ComWorld,
GameWorldMp, MapEnts, ...; docs/research/box-map.md 2.1 and 3.1) and the handlers below
for the ones that differ. Layouts: OpenAssetTools src/Common/Game/T5/T5_Assets.h (through
``opent5.xfile.layouts_pc``); load rules: OpenAssetTools src/ZoneCode/Game/T5/XAssets/*.txt.

Covered: everything a map built by cod2map / cod2rad / linker_pc contains (rawfile,
com_map, techset with its shaders, material, image, xmodel, gfx_map, game_map_mp,
col_map_mp with its map_ents), plus the xanim and glasses differences met in stock PC
mp_nuked, which now walks from its first asset to its last (515 assets; every block but
RUNTIME ends at the header size, RUNTIME 0x6600 short: open, docs/convert.md 12). FX, sound
and destructibledef parse with the PS3 handlers there.
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

from opent5.xfile.constants import AssetType, Block
from opent5.xfile.handlers.base import REGISTRY, Handler, array, asset_ref, items, runtime
from opent5.xfile.handlers.misc import glass_def_body
from opent5.xfile.handlers.xanim import _data_arrays, _part, delta_part_body
from opent5.xfile.model import XFile, _entry, parse
from opent5.xfile.stream import Chunk, Platform, XFileError, XStream

PC_MAGIC = b"IWffu100"
PC_VERSION = 473


class PCZoneError(XFileError):
    """A PC fastfile that cannot be opened; the message names the offset and the values."""


def pc_to_ps3_type(pc_type: int) -> int:
    """PC asset type number -> PS3 number (pc-route.md 1.3)."""
    if pc_type <= 6:
        return pc_type
    if pc_type <= 41:
        return pc_type + 2
    if pc_type == 42:
        return int(AssetType.EMBLEMSET)
    raise PCZoneError(f"PC asset type {pc_type}: expected 0..42")


def read_pc_fastfile(data: bytes) -> bytes:
    """The inflated XFile stream of a PC ``.ff`` (``IWffu100``)."""
    if data[:8] != PC_MAGIC:
        raise PCZoneError(
            f"PC fastfile at 0x0: expected magic {PC_MAGIC!r}, found {bytes(data[:8])!r}"
        )
    (version,) = struct.unpack_from("<I", data, 8)
    if version != PC_VERSION:
        raise PCZoneError(f"PC fastfile at 0x8: expected version {PC_VERSION}, found {version}")
    inflater = zlib.decompressobj()
    try:
        content = inflater.decompress(bytes(data[12:]))
    except zlib.error as exc:
        raise PCZoneError(f"PC fastfile at 0xc: zlib stream does not inflate ({exc})") from None
    if not inflater.eof:
        raise PCZoneError(f"PC fastfile: zlib stream ends early ({len(data)} bytes read)")
    return content


def open_pc_zone(path: str | Path) -> bytes:
    return read_pc_fastfile(Path(path).read_bytes())


# -- handlers ---------------------------------------------------------------------------------

PC_REGISTRY: dict[int, Handler] = {}


def _pc(cls: type[Handler]) -> type[Handler]:
    PC_REGISTRY[cls.asset_type] = cls()
    return cls


def reusable(io: XStream, chunk: Chunk, off: int) -> bool:
    """A reusable pointer (OpenAssetTools ``set reusable``): -1 or -2 means the data
    follows (-2 also reserves an alias slot in VIRTUAL); any other non-zero value is an
    offset pointer to data loaded earlier. Evidence: box zone 0x5944c, the
    ``*reflection_probe0`` GfxImage texture field is ``feffffff`` and its loadDef follows
    the name (box-map.md 2.1)."""
    if io._u32(chunk.data, off)[0] == 0xFFFFFFFE:
        io.follows(chunk, off, owned=True)
        io.insert()
        return True
    return io.follows(chunk, off, owned=False)


def _shader(io: XStream, chunk: Chunk, off: int, node: dict, key: str) -> None:
    """MaterialVertexShader / MaterialPixelShader (16, reusable): name, then the
    program (u32 x programSize at +0xc, not reusable)."""
    if reusable(io, chunk, off):
        io.alloc(3)
        sub = node.setdefault(key, {})
        s = io.load(16, sub, "raw")
        io.string(s, 0, sub, "name")
        array(io, s, 8, 3, 4 * s.u16(0xC), sub, "program", owned=True)
    elif io.reading:
        node[key] = None


def _technique(io: XStream, node: dict) -> None:
    head = io.load(8, node, "header")
    count = head.u16(6)
    passes = io.items(20, count, node, "passes") if count else []
    for p, pn in passes:
        if reusable(io, p, 0):  # vertexDecl (reusable)
            io.alloc(3)
            io.load(108, pn, "vertex_decl")
        _shader(io, p, 4, pn, "vs")
        _shader(io, p, 8, pn, "ps")
        args = p.u8(0xC) + p.u8(0xD) + p.u8(0xE)
        table = array(io, p, 0x10, 3, 8 * args, pn, "args", owned=True)
        if table is not None:
            for a in range(args):
                # a literal vertex / pixel constant (type 1 / 7), reusable
                if table.u16(8 * a) in (1, 7) and reusable(io, table, 8 * a + 4):
                    io.alloc(3)
                    io.load(16, pn, ("literal", a))
    io.string(head, 0, node, "name")


@_pc
class PCTechsetHandler(Handler):
    """MaterialTechniqueSet (528): name, then 130 reusable technique pointers, each
    a 8-byte header, passes (20 each) with D3D9 shader programs inline."""

    kind = "PC.MaterialTechniqueSet"
    asset_type = AssetType.TECHSET
    header_size = 528

    def body(self, io: XStream, h: Chunk, node: dict) -> None:
        io.push(Block.VIRTUAL)
        io.string(h, 0, node, "name")
        techniques = node.setdefault("techniques", {})
        for i in range(130):
            if reusable(io, h, 8 + 4 * i):
                io.alloc(3)
                if io.reading:
                    techniques[i] = {}
                _technique(io, techniques[i])
        io.pop()


@_pc
class PCImageHandler(Handler):
    """GfxImage (0x34): +0 texture.loadDef (reusable; TEMP: 12 bytes + resourceSize at
    +8), +0x2c name."""

    kind = "PC.GfxImage"
    asset_type = AssetType.IMAGE
    header_size = 0x34

    def body(self, io: XStream, h: Chunk, node: dict) -> None:
        io.push(Block.VIRTUAL)
        io.string(h, 0x2C, node, "name")
        if reusable(io, h, 0):
            io.push(Block.TEMP)
            io.alloc(3)
            load_def = io.load(12, node, "load_def")
            io.load(load_def.s32(8), node, "pixels")
            io.pop()
        io.pop()


@_pc
class PCMaterialHandler(Handler):
    """Material (192): +0 name, +0xb0 techset, +0xb4 textures (16 each, count u8 +0xaa),
    +0xb8 constants (32 x u8 +0xab), +0xbc state bits (8 x u8 +0xac)."""

    kind = "PC.Material"
    asset_type = AssetType.MATERIAL
    header_size = 192

    def body(self, io: XStream, h: Chunk, node: dict) -> None:
        io.push(Block.VIRTUAL)
        io.string(h, 0, node, "name")
        asset_ref(io, h, 0xB0, AssetType.TECHSET, node, "techset")
        textures = items(io, h, 0xB4, 3, 16, h.u8(0xAA), node, "textures", kind="PC.TextureDef")
        for t, element in textures or ():
            if t.u8(7) == 0xB:  # TS_WATER_MAP: a water struct, not an image
                if reusable(io, t, 0xC):
                    io.alloc(3)
                    w = io.load(68, element, "water")
                    cells = w.s32(0xC) * w.s32(0x10)
                    array(io, w, 4, 3, 8 * cells, element, "H0", owned=True)
                    array(io, w, 8, 3, 4 * cells, element, "wTerm", owned=True)
                    asset_ref(io, w, 0x40, AssetType.IMAGE, element, "image")
            else:
                asset_ref(io, t, 0xC, AssetType.IMAGE, element, "image")
        array(io, h, 0xB8, 15, 32 * h.u8(0xAB), node, "constants")
        array(io, h, 0xBC, 3, 8 * h.u8(0xAC), node, "state_bits")
        io.pop()


@_pc
class PCRawFileHandler(Handler):
    """RawFile (12): name, len, buffer (len + 1). PC buffer alignment is 1 (PS3 16):
    with 16 the box walk puts the com_map name at VIRTUAL+0x255, the gfx_map name
    pointer ``51020080`` says +0x250 (box-map.md 2.1)."""

    kind = "PC.RawFile"
    asset_type = AssetType.RAWFILE
    header_size = 12

    def body(self, io: XStream, h: Chunk, node: dict) -> None:
        io.push(Block.VIRTUAL)
        io.string(h, 0, node, "name")
        array(io, h, 8, 0, h.s32(4) + 1, node, "buffer", owned=True)
        io.pop()


def _pc_cell(io: XStream, c: Chunk, node: dict) -> None:
    for t, tree in items(io, c, 0x1C, 3, 40, c.s32(0x18), node, "aabb_tree") or ():
        array(io, t, 0x20, 1, 2 * t.u16(0x1E), tree, "smodel_indexes")
    for p, portal in items(io, c, 0x24, 3, 68, c.s32(0x20), node, "portals", owned=True) or ():
        io.convert(p, 0x20)  # cell
        array(io, p, 0x24, 3, 12 * p.s8(0x28), portal, "vertices", owned=True)
    array(io, c, 0x2C, 3, 4 * c.s32(0x28), node, "cull_groups", owned=True)
    array(io, c, 0x34, 0, c.u8(0x30), node, "reflection_probes", owned=True)


#: The PC GfxWorld keys (header offsets), for the converter.
PC_GFX_HEADER_SIZE = 0x43C


@_pc
class PCGfxWorldHandler(Handler):
    """GfxWorld (0x43c on PC; 0x454 on PS3). Member order and counts: OpenAssetTools
    T5 GfxWorld and src/ZoneCode/Game/T5/XAssets/GfxWorld.txt. The RUNTIME sizes and
    alignments are one choice that ends RUNTIME exactly at the box header's 0x1fd30
    (box-map.md 2.1; the choice is not unique, INFERRED)."""

    kind = "PC.GfxWorld"
    asset_type = AssetType.GFX_MAP
    header_size = PC_GFX_HEADER_SIZE

    def body(self, io: XStream, h: Chunk, node: dict) -> None:  # noqa: PLR0915
        io.push(Block.VIRTUAL)
        io.string(h, 0, node, "name")
        io.string(h, 4, node, "base_name")
        planes, nodes, surfaces = h.s32(8), h.s32(0xC), h.s32(0x10)
        array(io, h, 0x18, 3, 32 * h.s32(0x14), node, "aabb_trees", owned=True)
        array(io, h, 0x20, 3, 4 * h.s32(0x1C), node, "leaf_refs", owned=True)
        array(io, h, 0x28, 3, 4 * h.s32(0x24), node, "sky_start_surfs", owned=True)
        asset_ref(io, h, 0x2C, AssetType.IMAGE, node, "sky_image")
        io.string(h, 0x34, node, "sky_box_model")
        sun = node.setdefault("sun_light", {})
        raw = array(io, h, 0xEC, 15, 0x170, sun, "raw")
        if raw is not None:
            asset_ref(io, raw, 0x160, AssetType.LIGHTDEF, sun, "def")
        array(io, h, 0x10C, 3, 32 * h.u32(0x108), node, "coronas", owned=True)
        array(io, h, 0x114, 3, 16 * h.u32(0x110), node, "shadow_map_volumes", owned=True)
        array(io, h, 0x11C, 3, 16 * h.u32(0x118), node, "shadow_map_volume_planes", owned=True)
        array(io, h, 0x124, 3, 24 * h.u32(0x120), node, "exposure_volumes", owned=True)
        array(io, h, 0x12C, 3, 16 * h.u32(0x128), node, "exposure_volume_planes", owned=True)
        cells = h.s32(0x140)
        array(io, h, 0x144, 3, 20 * planes, node, "planes")
        array(io, h, 0x148, 1, 2 * nodes, node, "nodes", owned=True)
        runtime(io, h, 0x14C, 3, 4 * 0x200 * cells)
        for c, element in items(io, h, 0x154, 3, 56, cells, node, "cells", owned=True) or ():
            _pc_cell(io, c, element)
        probes = h.u32(0x158)
        found = items(io, h, 0x15C, 3, 24, probes, node, "reflection_probes", owned=True)
        for p, element in found or ():
            asset_ref(io, p, 0xC, AssetType.IMAGE, element, "image")
            array(io, p, 0x10, 3, 96 * p.u32(0x14), element, "probe_volumes", owned=True)
        runtime(io, h, 0x160, 3, 4 * probes)
        lightmaps = h.s32(0x164)
        for m, element in (
            items(io, h, 0x168, 3, 12, lightmaps, node, "lightmaps", owned=True) or ()
        ):
            for k in range(3):
                asset_ref(io, m, 4 * k, AssetType.IMAGE, element, k)
        for off in (0x16C, 0x170, 0x174):
            runtime(io, h, off, 3, 4 * lightmaps)
        images = node.setdefault("draw_images", {})
        for off in range(0x178, 0x1F4, 4):
            asset_ref(io, h, off, AssetType.IMAGE, images, off)
        array(io, h, 0x1F8, 3, 44 * h.u32(0x1F4), node, "vertices", owned=True)
        array(io, h, 0x204, 0, h.u32(0x200), node, "vertex_layer_data", owned=True)
        array(io, h, 0x214, 1, 2 * h.s32(0x210), node, "indices", owned=True)
        row_axis = h.u32(0x22C)
        lo, hi = h.u16(0x220 + 2 * row_axis), h.u16(0x226 + 2 * row_axis)
        grid = node.setdefault("light_grid", {})
        array(io, h, 0x234, 1, 2 * (hi - lo + 1), grid, "row_data_start", owned=True)
        array(io, h, 0x23C, 3, h.u32(0x238), grid, "raw_row_data", owned=True)
        array(io, h, 0x244, 3, 4 * h.u32(0x240), grid, "entries", owned=True)
        array(io, h, 0x24C, 3, 168 * h.u32(0x248), grid, "colors", owned=True)
        array(io, h, 0x254, 3, 60 * h.s32(0x250), node, "models", owned=True)
        memory = items(io, h, 0x278, 3, 8, h.s32(0x274), node, "material_memory", owned=True)
        for m, element in memory or ():
            asset_ref(io, m, 0, AssetType.MATERIAL, element, "material")
        asset_ref(io, h, 0x280, AssetType.MATERIAL, node, "sun_sprite_material")
        asset_ref(io, h, 0x284, AssetType.MATERIAL, node, "sun_flare_material")
        asset_ref(io, h, 0x31C, AssetType.IMAGE, node, "outdoor_image")
        sun_index, lights = h.u32(0xFC), h.u32(0x100)
        dyn = (h.u32(0x3BC), h.u32(0x3C0))
        shadow = lights - sun_index - 1
        runtime(io, h, 0x320, 3, 4 * cells * ((cells + 31) // 32))
        runtime(io, h, 0x324, 3, 6 * dyn[0])
        runtime(io, h, 0x328, 3, 4 * dyn[1])
        runtime(io, h, 0x32C, 3, 4 * 0x2000 * shadow)
        runtime(io, h, 0x330, 3, 4 * dyn[0] * shadow)
        runtime(io, h, 0x334, 3, 4 * dyn[1] * shadow)
        runtime(io, h, 0x338, 0, dyn[0])
        for e, element in items(io, h, 0x33C, 3, 12, lights, node, "shadow_geom", owned=True) or ():
            array(io, e, 4, 1, 2 * e.u16(0), element, "sorted_surf_index", owned=True)
            array(io, e, 8, 1, 2 * e.u16(2), element, "smodel_index", owned=True)
        regions = items(io, h, 0x340, 3, 8, lights, node, "light_region", owned=True)
        for e, element in regions or ():
            for hull, hel in items(io, e, 4, 3, 80, e.u32(0), element, "hulls", owned=True) or ():
                array(io, hull, 0x4C, 3, 20 * hull.u32(0x48), hel, "axis", owned=True)
        smodels, static_surfaces = h.u32(0x344), h.u32(0x34C)
        smodel_vis, surface_vis = h.u32(0x368), h.u32(0x36C)
        # dpvs-static visibility arrays. The smodel vis arrays stay keyed to smodel_vis (0 on
        # terrain, which has no static models). The surface vis arrays hold no RUNTIME bytes
        # here: on PC the surface visibility reservation is made once, 128-aligned, as
        # surfaceCastsSunShadow (0x3AC) below. Calibrated against 11 built PC zones spanning 1 to
        # 9 shadow lights and 1 to 670 static surfaces (all parse exactly; see docs/mapgen.md).
        for k in range(3):
            runtime(io, h, 0x370 + 4 * k, 0, smodel_vis)  # smodelVisData[3]
        for k in range(3):
            runtime(io, h, 0x37C + 4 * k, 0, 0)  # surfaceVisData[3]
        runtime(io, h, 0x388, 0, smodel_vis)  # smodelVisDataCameraSaved
        runtime(io, h, 0x38C, 0, 0)  # surfaceVisDataCameraSaved
        # lodData: align 4 (with 128 the box with 8 static models, mp_opent5box_props,
        # ends RUNTIME 0x80 past its header size; the box without models cannot tell).
        runtime(io, h, 0x390, 3, 32 * smodel_vis)
        array(io, h, 0x394, 1, 2 * static_surfaces, node, "sorted_surf_index", owned=True)
        array(io, h, 0x398, 3, 40 * smodels, node, "smodel_insts", owned=True)
        for s, element in items(io, h, 0x39C, 15, 80, surfaces, node, "surfaces", owned=True) or ():
            asset_ref(io, s, 0x30, AssetType.MATERIAL, element, "material")
        array(io, h, 0x3A0, 3, 32 * h.s32(0x104), node, "cull_groups", owned=True)
        draws = items(io, h, 0x3A4, 3, 76, smodels, node, "smodel_draw_insts", owned=True)
        for d, element in draws or ():
            asset_ref(io, d, 0x38, AssetType.XMODEL, element, "model")
        # Per-static-surface RUNTIME: surfaceMaterials plus the per-surface draw/sun-shadow words
        # that the PC loader lays down contiguously here, 12 bytes per static surface (align 4).
        runtime(io, h, 0x3A8, 3, 12 * static_surfaces)
        # surfaceCastsSunShadow: the surface visibility reservation, 4 * surface_vis bytes,
        # 128-aligned (as on PS3, src/opent5/xfile/handlers/gfxworld.py 0x3C4). This single
        # 128-aligned block is where PC accounts for surface_vis, not the per-array reserves
        # at 0x37C..0x38C above; earlier builds used 4 * static_surfaces, which only matched
        # maps where surface_vis tracked static_surfaces (box, one-light terrain).
        runtime(io, h, 0x3AC, 127, 4 * surface_vis)
        words = (h.u32(0x3B4), h.u32(0x3B8))
        runtime(io, h, 0x3C4, 3, 4 * words[0] * cells)
        runtime(io, h, 0x3C8, 3, 4 * words[1] * cells)
        for j in range(2):  # dynEntVisData[2][3], row-major
            for k in range(3):
                runtime(io, h, 0x3CC + 12 * j + 4 * k, 15, 32 * words[j])
        array(io, h, 0x3E8, 3, 24 * h.u32(0x3E4), node, "world_lod_chains", owned=True)
        array(io, h, 0x3F0, 3, 12 * h.u32(0x3EC), node, "world_lod_infos", owned=True)
        array(io, h, 0x3F8, 3, 4 * h.u32(0x3F4), node, "world_lod_surfaces", owned=True)
        array(io, h, 0x404, 15, h.u32(0x400) & ~15, node, "water_buffer0", owned=True)
        array(io, h, 0x40C, 15, h.u32(0x408) & ~15, node, "water_buffer1", owned=True)
        asset_ref(io, h, 0x410, AssetType.MATERIAL, node, "water_material")
        asset_ref(io, h, 0x414, AssetType.MATERIAL, node, "corona_material")
        asset_ref(io, h, 0x418, AssetType.MATERIAL, node, "rope_material")
        array(io, h, 0x420, 3, 68 * h.u32(0x41C), node, "occluders", owned=True)
        array(io, h, 0x428, 3, 24 * h.u32(0x424), node, "outdoor_bounds", owned=True)
        array(io, h, 0x434, 3, 56 * h.u32(0x42C), node, "hero_lights", owned=True)
        array(io, h, 0x438, 3, 24 * h.u32(0x430), node, "hero_light_tree", owned=True)
        io.pop()


def _pc_collision_tree(io: XStream, node: dict) -> None:
    ct = io.load(0x28, node, "raw")
    array(io, ct, 0x1C, 15, 16 * ct.u32(0x18), node, "nodes", owned=True)
    array(io, ct, 0x24, 1, 2 * ct.u32(0x20), node, "leafs", owned=True)


def _pc_xsurface(io: XStream, s: Chunk, node: dict) -> None:
    """PC XSurface (0x44): vertInfo, verts0 (GfxPackedVertex, 32 bytes), vertList,
    triIndices (OpenAssetTools XModel.txt reorder); verts0, vertList, collision trees and
    triIndices are reusable."""
    v = [s.s16(0x10 + 2 * k) for k in range(4)]
    array(io, s, 0x18, 1, 2 * (v[0] + 3 * v[1] + 5 * v[2] + 7 * v[3]), node, "verts_blend")
    array(io, s, 0x1C, 3, 48 * (v[0] + v[1] + v[2] + v[3]), node, "tension_data")
    if reusable(io, s, 0x20):
        io.alloc(15)
        io.load(32 * s.u16(4), node, "verts0")
    elif io.reading:
        node["verts0"] = None
    lists = None
    if reusable(io, s, 0x28):
        io.alloc(3)
        lists = io.items(12, s.u8(1), node, "vert_lists", "PC.XRigidVertList")
    elif io.reading:
        node["vert_lists"] = None
    for vl, element in lists or ():
        if reusable(io, vl, 8):
            io.alloc(3)
            _pc_collision_tree(
                io, element.setdefault("collision_tree", {"_t": "PC.XSurfaceCollisionTree"})
            )
    if reusable(io, s, 0xC):
        io.alloc(15)
        io.load(6 * s.u16(6), node, "tri_indices")
    elif io.reading:
        node["tri_indices"] = None


def _pc_brush(io: XStream, node: dict) -> None:
    b = io.load(0x60, node, "raw")
    sides = items(io, b, 0x20, 3, 0xC, b.u32(0x1C), node, "sides", kind="PC.cbrushside_t")
    for side, element in sides or ():
        if reusable(io, side, 0):
            io.alloc(3)
            io.load(0x14, element, "plane")
    if reusable(io, b, 0x58):
        io.alloc(3)
        io.load(0xC * b.u32(0x54), node, "verts")
    if reusable(io, b, 0x5C):
        io.alloc(3)
        io.load(0x14 * b.u32(0x1C), node, "planes")


def _pc_collmap(io: XStream, c: Chunk, node: dict) -> None:
    geom_list = array(io, c, 0, 3, 0xC, node, "geom_list")
    if geom_list is None:
        return
    geoms = items(
        io, geom_list, 4, 15, 0x44, geom_list.u32(0), node, "geoms", kind="PC.PhysGeomInfo"
    )
    for g, geom in geoms or ():
        if reusable(io, g, 0):
            io.alloc(15)
            _pc_brush(io, geom.setdefault("brush", {"_t": "PC.BrushWrapper"}))


@_pc
class PCXModelHandler(Handler):
    """XModel (0xfc on PC; 0xf8 on PS3): OpenAssetTools T5 XModel and
    src/ZoneCode/Game/T5/XAssets/XModel.txt. lodInfo is 4 x 32 bytes from +0x28 (PS3 4 x
    28), collSurfs +0xac (0x2c each with collTris, 48 x numCollTris), boneInfo +0xb8,
    highMipBounds +0xdc, physPreset +0xec, collmaps +0xf4 (count u8 +0xf0),
    physConstraints +0xf8."""

    kind = "PC.XModel"
    asset_type = AssetType.XMODEL
    header_size = 0xFC

    def body(self, io: XStream, h: Chunk, node: dict) -> None:
        io.push(Block.VIRTUAL)
        io.string(h, 0, node, "name")
        bones, roots, surf_count = h.u8(4), h.u8(5), h.u8(6)
        for off, mask, size, key in (
            (0x8, 1, 2 * bones, "bone_names"),
            (0xC, 0, bones - roots, "parent_list"),
            (0x10, 1, 8 * (bones - roots), "quats"),
            (0x14, 3, 16 * (bones - roots), "trans"),
            (0x18, 0, bones, "part_classification"),
            (0x1C, 3, 0x20 * bones, "base_mat"),
        ):
            if reusable(io, h, off):
                io.alloc(mask)
                io.load(size, node, key)
            elif io.reading:
                node[key] = None
        surfs = items(io, h, 0x20, 3, 0x44, surf_count, node, "surfs", True, "PC.XSurface")
        for s, surface in surfs or ():
            _pc_xsurface(io, s, surface)
        materials = items(io, h, 0x24, 3, 4, surf_count, node, "materials", True, "XModelMaterial")
        for m, element in materials or ():
            asset_ref(io, m, 0, AssetType.MATERIAL, element, "material")
        coll = items(io, h, 0xAC, 3, 0x2C, h.s32(0xB0), node, "coll_surfs", True, "PC.CollSurf")
        for c, element in coll or ():
            array(io, c, 0, 3, 48 * c.s32(4), element, "coll_tris", owned=True)
        array(io, h, 0xB8, 3, 0x2C * bones, node, "bone_info", owned=True)
        array(io, h, 0xDC, 3, 0x10 * surf_count, node, "high_mip_bounds", owned=True)
        asset_ref(io, h, 0xEC, AssetType.PHYSPRESET, node, "phys_preset")
        collmaps = items(io, h, 0xF4, 3, 4, h.u8(0xF0), node, "collmaps", True, "PC.Collmap")
        for c, element in collmaps or ():
            _pc_collmap(io, c, element)
        asset_ref(io, h, 0xF8, AssetType.PHYSCONSTRAINTS, node, "phys_constraints")
        io.pop()


@_pc
class PCGlassesHandler(Handler):
    """Glasses (56): as PS3 but workMemory (+0xc) is never loaded on PC (OpenAssetTools
    src/ZoneCode/Game/T5/XAssets/Glasses.txt ``set condition workMemory never``); the
    PS3 RUNTIME reservation runs PC mp_nuked's asset 513 past the stream end."""

    kind = "Glasses"
    asset_type = AssetType.GLASSES
    header_size = 56

    def body(self, io: XStream, h: Chunk, node: dict) -> None:
        io.push(Block.VIRTUAL)
        io.string(h, 0, node, "name")
        for g, glass in items(io, h, 8, 3, 124, h.u32(4), node, "glasses", True, "Glass") or ():
            if reusable(io, g, 0):
                io.alloc(3)
                if io.reading:
                    glass["glass_def"] = {"_t": "GlassDef"}
                glass_def = glass["glass_def"]
                glass_def_body(io, io.load(60, glass_def, "raw"), glass_def)
            elif io.reading:
                glass["glass_def"] = None
            array(io, g, 0x40, 3, 8 * g.u8(0x3D), glass, "outline", owned=True)
        io.pop()


@_pc
class PCXAnimHandler(Handler):
    """XAnimParts (104): as PS3 but boneCount has 10 entries (PS3 12), so the bone
    names count is boneCount[9] at u8 +0x21 (OpenAssetTools XAnimParts.txt ``set count
    names boneCount[9]``) and notifyCount is u8 +0x22 (PS3 +0x23 and +0x24). PC mp_nuked
    asset 512 (0x4b2e60c): +0x21 = 1 name (``6701``), +0x22 = 1 notify (``0302 0000
    0000803f``: name 0x203, time 1.0); with the PS3 offsets the asset ends 2 bytes late
    and the glasses header (``ffffffff 3e000000 ffffffff``) at 0x4b2edc6 is missed."""

    kind = "XAnimParts"
    asset_type = AssetType.XANIM
    header_size = 104

    def body(self, io: XStream, h: Chunk, node: dict) -> None:
        io.push(Block.VIRTUAL)
        io.string(h, 0, node, "name")
        numframes = h.u16(14)
        array(io, h, 64, 1, 2 * h.u8(33), node, "names", owned=True)
        array(io, h, 96, 3, 8 * h.u8(34), node, "notify", owned=True)
        delta = _part(io, h, 100, node, "delta_part", "XAnimDeltaPart")
        if delta is not None:
            delta_part_body(io, numframes, delta)
        for key, off, count, mask, size in _data_arrays(h):
            array(io, h, off, mask, size * count, node, key, owned=True)
        if numframes <= 255:
            array(io, h, 92, 0, h.u32(44), node, "indices", owned=True)
        else:
            array(io, h, 92, 1, 2 * h.u32(44), node, "indices", owned=True)
        io.pop()


#: The PC platform: little-endian, the PS3 handlers where the structs agree, the PC ones
#: above where they differ, PC asset type numbers mapped to PS3 ones.
PC = Platform(
    "pc",
    "<",
    {**REGISTRY, **PC_REGISTRY},
    {pc: pc_to_ps3_type(pc) for pc in range(43)},
)


def parse_pc(content: bytes, log: bool = True) -> XFile:
    """Parse an inflated PC zone (``read_pc_fastfile``)."""
    return parse(content, log=log, platform=PC)


def walk_range(content: bytes, start: int, first: int, last: int, virtual: int = 0) -> list:
    """Walk assets first..last (inclusive) of a PC zone from file offset ``start`` with
    VIRTUAL at ``virtual``, without the assets before them: for stock PC map zones,
    whose XModel / FX / sound assets the PC handlers do not cover (the world range of PC
    mp_nuked, com_map at 0x183bae2 to col_map_mp, walks this way; box-map.md 2.3).
    Returns the Assets. Offset pointers into earlier assets do not resolve."""
    data = bytes(content)
    io = XStream(data, log=False, platform=PC)
    count = struct.unpack_from("<I", data, 0x2C)[0]
    at = _asset_array_offset(data)
    entries = PC.chunk_type(memoryview(data)[at : at + 8 * count], at, Block.VIRTUAL, 0)
    io.fp = start
    io.pos[Block.VIRTUAL] = virtual
    io.push(Block.VIRTUAL)
    out = [_entry(io, entries, i, None) for i in range(first, last + 1)]
    io.pop()
    return out


def _asset_array_offset(data: bytes) -> int:
    """File offset of the asset array: after the script string pointers and the inline
    strings (-1) they name."""
    strings, strings_ptr = struct.unpack_from("<II", data, 0x24)
    pos = 0x34
    if strings_ptr:
        pointers = struct.unpack_from(f"<{strings}I", data, pos)
        pos += 4 * strings
        for p in pointers:
            if p == 0xFFFFFFFF:
                pos = data.index(b"\0", pos) + 1
    return pos
