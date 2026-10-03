"""PC world assets -> PS3 world assets, node by node.

clipMap_t (with its MapEnts), ComWorld and GameWorldMp have the same layout on both
platforms: their nodes are swapped in place (``swap``). GfxWorld is re-laid out:

- header: PS3 = PC[0x0, 0x24) + 20 PS3-only bytes + PC[0x24, 0x218) + 4 PS3-only bytes +
  PC[0x218, 0x43c) (PC fields move by +0x14 from skySurfCount and by +0x18 from the light
  grid). With this rule the swapped PC mp_nuked header differs from the PS3 one in six
  words only (docs/research/box-map.md 3.4, repeated by ``compare``).
- vertices: PC GfxWorldVertex (44 bytes, one stream) -> PS3 positions (16 bytes: x, y, z,
  binormal sign) plus PHYSICAL layer data, one run per vertex group: colour RGBA (PC
  D3DCOLOR is BGRA), uv, lightmap uv, normal and tangent as CMP 11:11:10, then the
  per-layer extras the PC keeps in its own layer data.
- groups and surfaces: a PC surface's (firstVertex, vertexCount) is its vertex group; the
  PS3 vertex buffer holds the groups in the order surfaces first use them, the index
  buffer holds each surface's triangles in surface order, and a PS3 surface records its
  own vertex range inside the group (+0x20 the lowest index it uses, +0x24 the count up
  to its highest). Proven on mp_nuked: converting the PC world with this rule gives the
  PS3 vertex positions, index buffer, surface groups, first vertices, base indices,
  vertex counts and layer offsets exactly (``compare``, docs/convert.md).
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from opent5.convert.swap import (
    CLIP_KEYS,
    COM_KEYS,
    GAME_KEYS,
    MAPENTS_KEYS,
    swap_node,
    swap_struct,
)
from opent5.xfile.constants import PTR_INLINE
from opent5.xfile.stream import XFileError

PS3_GFX_HEADER = 0x454
PC_GFX_HEADER = 0x43C
LAYER_BASE = 28


class ConvertError(XFileError):
    """A PC map the converter cannot turn into PS3 form; says what and where."""


# -- same-layout assets ---------------------------------------------------------------------


def convert_clip(node: dict) -> dict:
    """clipMap_t and its inline MapEnts, in place."""
    ents = node.get("map_ents")
    swap_node(node, CLIP_KEYS)
    if isinstance(ents, dict):
        swap_node(ents, MAPENTS_KEYS)
    return node


def convert_com(node: dict) -> dict:
    return swap_node(node, COM_KEYS)


def convert_game(node: dict) -> dict:
    return swap_node(node, GAME_KEYS)


# -- GfxWorld: header ---------------------------------------------------------------------


def gfx_header(pc_header_le: bytes, stock_header: bytes) -> bytearray:
    """The PS3 header from a PC one (swapped field by field), PS3-only words from the
    stock header."""
    ph = swap_struct("GfxWorld", pc_header_le)
    out = bytearray(
        ph[0:0x24] + stock_header[0x24:0x38] + ph[0x24:0x218] + stock_header[0x22C:0x230]
    )
    out += ph[0x218:PC_GFX_HEADER]
    if len(out) != PS3_GFX_HEADER:
        raise AssertionError(len(out))
    return out


def ps3_offset(pc_offset: int) -> int:
    """Where a PC GfxWorld header field lands in the PS3 header."""
    if pc_offset < 0x24:
        return pc_offset
    if pc_offset < 0x218:
        return pc_offset + 0x14
    return pc_offset + 0x18


# -- GfxWorld: vertices, groups, surfaces ------------------------------------------------------

PC_VERTEX = np.dtype(
    [
        ("xyz", "<f4", 3),
        ("binormal_sign", "<f4"),
        ("colour", "u1", 4),
        ("uv", "<f4", 2),
        ("lmap", "<f4", 2),
        ("normal", "u1", 4),
        ("tangent", "u1", 4),
    ]
)


def pc_unit_vectors(packed: np.ndarray) -> np.ndarray:
    """PC PackedUnitVec bytes (x, y, z, scale) -> float vectors:
    (b - 127) * (scale + 192) / 32385. Evidence: box floor normal 7f7ffe3f -> (0, 0, 1)
    (box-map.md 3.4)."""
    b = packed.astype(np.float64)
    scale = (b[:, 3:4] + 192.0) / 32385.0
    return (b[:, :3] - 127.0) * scale


def cmp_pack(vectors: np.ndarray) -> np.ndarray:
    """Vectors -> PS3 CMP words (x bits 0..10 and y 11..21 scaled by 1023, z 22..31 by
    511; docs/extract.md 3.1), normalised first: the PS3 words are unit length (the PC
    normal a0047f3f has length 1.0028; PS3 stores y -988, the normalised value)."""
    n = np.linalg.norm(vectors, axis=1, keepdims=True)
    v = np.where(n > 1e-9, vectors / np.maximum(n, 1e-9), vectors)
    v = np.clip(v, -1.0, 1.0)
    x = np.round(v[:, 0] * 1023).astype(np.int64) & 0x7FF
    y = np.round(v[:, 1] * 1023).astype(np.int64) & 0x7FF
    z = np.round(v[:, 2] * 511).astype(np.int64) & 0x3FF
    return (x | (y << 11) | (z << 22)).astype(">u4")


@dataclass
class Group:
    """One vertex group: PC first vertex and count, PC layer data offset and extra
    bytes per vertex, and where it goes in the PS3 buffers."""

    pc_first: int
    count: int
    pc_layer: int
    extra: int = 0
    first: int = 0
    layer: int = 0

    @property
    def stride(self) -> int:
        return LAYER_BASE + self.extra


@dataclass
class PCSurface:
    index: int
    raw: bytes  # PC GfxSurface, 0x50 bytes, little-endian
    mins: tuple
    pc_layer: int
    maxs: tuple
    pc_first: int
    vertex_count: int
    tri_count: int
    base_index: int
    himip: float
    stream2: int

    @classmethod
    def parse(cls, index: int, raw: bytes) -> PCSurface:
        if len(raw) != 0x50:
            raise ConvertError(f"PC GfxSurface {index}: expected 0x50 bytes, found {len(raw)}")
        f = struct.unpack_from("<3fi3fiHHifi", raw, 0)
        return cls(index, bytes(raw), f[0:3], f[3], f[4:7], f[7], f[8], f[9], f[10], f[11], f[12])


def plan_groups(surfaces: list[PCSurface], layer_size: int) -> list[Group]:
    """The PS3 vertex groups, in the order the surfaces first use them, with their
    extra layer bytes per vertex. PC layer data holds only the extras, one run per group
    at the group's ``vertexLayerData``; a group without extras has offset 0 (PC
    mp_nuked: all 165 such groups), so at offset 0 the run belongs to the one group whose
    vertex count divides it into a valid stride (a multiple of 4, at least 8 bytes).
    More than one such group is ambiguous and raises. Checked against PS3 mp_nuked:
    every group's PS3 stride is 28 plus the extra this gives (``compare``)."""
    groups: dict[int, Group] = {}
    order: list[Group] = []
    for s in surfaces:
        g = groups.get(s.pc_first)
        if g is None:
            g = groups[s.pc_first] = Group(s.pc_first, s.vertex_count, s.pc_layer)
            order.append(g)
        elif (g.count, g.pc_layer) != (s.vertex_count, s.pc_layer):
            raise ConvertError(
                f"PC surface {s.index}: group at vertex {s.pc_first} has {g.count} vertices "
                f"and layer offset {g.pc_layer:#x}, this surface says {s.vertex_count} and "
                f"{s.pc_layer:#x}"
            )
    by_offset: dict[int, list[Group]] = {}
    for g in groups.values():
        by_offset.setdefault(g.pc_layer, []).append(g)
    offsets = sorted(by_offset)
    for i, off in enumerate(offsets):
        end = offsets[i + 1] if i + 1 < len(offsets) else layer_size
        size = end - off
        if not size:
            continue
        owners = [
            g
            for g in by_offset[off]
            if g.count
            and size % g.count == 0
            and (size // g.count) % 4 == 0
            and size // g.count >= 8
        ]
        if not owners and size < 8:
            # The box's 4-byte layer data: no group has extras (all its materials are
            # 28-byte strides), the linker stores a minimal buffer.
            continue
        if len(owners) != 1:
            found = ", ".join(f"{g.count} at vertex {g.pc_first}" for g in by_offset[off][:8])
            raise ConvertError(
                f"PC layer data at {off:#x}: {size} bytes; expected exactly one group there "
                f"whose vertex count divides it into a stride, found {len(owners)} among "
                f"groups of {found}"
            )
        owners[0].extra = size // owners[0].count
    first = layer = 0
    for g in order:
        g.first, g.layer = first, layer
        first += g.count
        layer += g.count * g.stride
    return order


def extra_layout(extra: int) -> list[str]:
    """Word kinds of a group's extra layer bytes: pairs of floats (layer uv, swapped),
    then a colour word (bytes, as they are) when four bytes remain. Evidence: PS3 vs PC
    mp_nuked layer extras word by word (docs/convert.md): strides 36, 44 all floats;
    40 and 48 floats then a byte word; 56 six floats then a byte word. Stride 52 occurs
    both as six floats and as four floats and two byte words, which the size alone cannot
    tell apart: ``None``."""
    if extra % 4:
        raise ConvertError(f"layer extras of {extra} bytes per vertex: expected a multiple of 4")
    if extra == 24:
        return None
    words = extra // 4
    kinds = ["f32"] * (words - words % 2)
    if words % 2:
        kinds.append("bytes")
    return kinds


def convert_vertices(
    pc_vertices: bytes, pc_layer: bytes, groups: list[Group], strict: bool = True
) -> tuple[bytes, bytes, list[str]]:
    """PS3 positions and layer data, group by group. Returns (positions, layer data,
    notes); a group whose extras cannot be laid out raises with strict, else its extras
    are swapped as floats and a note says so."""
    pc = np.frombuffer(pc_vertices, PC_VERTEX)
    positions = bytearray()
    layer = bytearray()
    notes: list[str] = []
    for g in groups:
        v = pc[g.pc_first : g.pc_first + g.count]
        if len(v) != g.count:
            raise ConvertError(
                f"vertex group at {g.pc_first}: expected {g.count} vertices, the PC buffer "
                f"has {len(v)} from there"
            )
        pos = np.empty((g.count, 4), ">f4")
        pos[:, :3] = v["xyz"]
        pos[:, 3] = v["binormal_sign"]
        positions += pos.tobytes()
        run = np.zeros((g.count, g.stride), np.uint8)
        run[:, 0:4] = v["colour"][:, [2, 1, 0, 3]]
        run[:, 4:12] = v["uv"].astype(">f4").view(np.uint8).reshape(g.count, 8)
        run[:, 12:20] = v["lmap"].astype(">f4").view(np.uint8).reshape(g.count, 8)
        run[:, 20:24] = cmp_pack(pc_unit_vectors(v["normal"])).view(np.uint8).reshape(-1, 4)
        run[:, 24:28] = cmp_pack(pc_unit_vectors(v["tangent"])).view(np.uint8).reshape(-1, 4)
        if g.extra:
            raw = np.frombuffer(
                pc_layer[g.pc_layer : g.pc_layer + g.count * g.extra], np.uint8
            ).reshape(g.count, g.extra)
            kinds = extra_layout(g.extra)
            if kinds is None:
                if strict:
                    raise ConvertError(
                        f"vertex group at {g.pc_first}: {g.extra} bytes of layer extras per "
                        "vertex can be three uv pairs or two uv pairs and two colours; this "
                        "layered material is not supported"
                    )
                notes.append(f"group {g.pc_first}: {g.extra}-byte extras swapped as floats")
                kinds = ["f32"] * (g.extra // 4)
            out = raw.copy()
            for w, kind in enumerate(kinds):
                if kind == "f32":
                    out[:, 4 * w : 4 * w + 4] = raw[:, 4 * w : 4 * w + 4][:, ::-1]
            run[:, LAYER_BASE:] = out
        layer += run.tobytes()
    return bytes(positions), bytes(layer), notes


def convert_surfaces(
    surfaces: list[PCSurface],
    pc_indices: bytes,
    groups: list[Group],
    material_words: list[bytes],
    light_bytes: list[bytes] | None = None,
    pc_vertices: bytes | None = None,
) -> tuple[list[bytes], bytes]:
    """PS3 surface records (0x60, BE) and the index buffer. ``material_words`` gives each
    surface's +0x40 word (a Material alias pointer in the target zone);
    ``light_bytes`` optionally its +0x44 bytes (lightmap, reflection probe, primary
    light, flags), else the PC ones. ``pc_vertices`` fills empty PC bounds."""
    idx = np.frombuffer(pc_indices, "<u2")
    positions = None
    if pc_vertices is not None:
        positions = np.frombuffer(pc_vertices, PC_VERTEX)["xyz"].astype(np.float32)
    by_first = {g.pc_first: g for g in groups}
    records: list[bytes] = []
    out_idx: list[np.ndarray] = []
    base = 0
    for n, s in enumerate(surfaces):
        g = by_first[s.pc_first]
        tri = idx[s.base_index : s.base_index + 3 * s.tri_count]
        if len(tri) != 3 * s.tri_count:
            raise ConvertError(
                f"PC surface {s.index}: {s.tri_count} triangles from index {s.base_index}, the "
                f"buffer has {len(idx)} indices"
            )
        lo, hi = (int(tri.min()), int(tri.max())) if len(tri) else (0, -1)
        if hi >= g.count:
            raise ConvertError(
                f"PC surface {s.index}: index {hi} outside its group of {g.count} vertices"
            )
        mins, maxs = s.mins, s.maxs
        if (
            positions is not None
            and len(tri)
            and any(a > b for a, b in zip(mins, maxs, strict=False))
        ):
            # Empty PC bounds (131072 / -131072): PS3 stores the triangles' bounds
            # widened by 1 (PS3 mp_nuked: all 230 such surfaces).
            v = positions[s.pc_first + tri.astype(np.int64)]
            mins = tuple(float(x) - 1.0 for x in v.min(axis=0))
            maxs = tuple(float(x) + 1.0 for x in v.max(axis=0))
        rec = struct.pack(
            ">3fi3fiiHHifi",
            *mins,
            g.layer,
            *maxs,
            g.first,
            lo,
            hi - lo + 1,
            s.tri_count,
            base,
            s.himip,
            s.stream2,
        )
        light = s.raw[0x34:0x38] if light_bytes is None else light_bytes[n]
        rec += b"\0" * 12 + material_words[n] + light
        rec += struct.pack(">6f", *struct.unpack_from("<6f", s.raw, 0x38))
        if len(rec) != 0x60:
            raise AssertionError(len(rec))
        records.append(rec)
        out_idx.append(tri)
        base += len(tri)
    indices = np.concatenate(out_idx).astype(">u2").tobytes() if out_idx else b""
    return records, indices


def light_grid_rows(raw_le: bytes, row_start_le: bytes | None) -> bytes:
    """Light grid rawRowData: rows at 4 x rowDataStart[i] (0xffff: no row), each u16
    colStart, colCount, zStart, zCount, u32 firstEntry, then lookup bytes. Evidence: PS3
    mp_nuked row 0 ``0e5f 0002 082a 0002 00000000 0202 0000`` = PC ``5f0e 0200 2a08 0200
    00000000 0202 0000``; with this rule the whole array matches (``compare``)."""
    data = bytearray(raw_le)
    starts = sorted(
        {4 * v for v in np.frombuffer(bytes(row_start_le or b""), "<u2") if v != 0xFFFF}
    )
    for at in starts:
        if at + 12 > len(data):
            raise ConvertError(
                f"light grid row at {at:#x}: expected 12 bytes, rawRowData is {len(data)}"
            )
        u16s = struct.unpack_from("<4H", data, at)
        (first,) = struct.unpack_from("<I", data, at + 8)
        struct.pack_into(">4HI", data, at, *u16s, first)
    return bytes(data)


# -- GfxWorld: the whole node ---------------------------------------------------------------

#: PS3 header ranges whose words stay the target zone's in every mode: pointer words to
#: data kept from the target (and the counts that size it). Everything else in the header,
#: including the sun flare data (+0x294 hasValidData, +0x2a0..) and the outdoorLookupMatrix
#: (+0x2f4..+0x333), is the PC map's own (box-lighting.md 5: keeping the stock range gave the
#: box Nuketown's matrix, stock ``37c4c4cd`` at +0x2f4 where the box has ``3a800000``).
STOCK_HEADER_RANGES = (
    (0x24, 0x38, "PS3-only streaming words"),
    (0x40, 0x4C, "sky image, sky sampler state, sky box model"),
    (0x100, 0x104, "sun light pointer (the record is the PC one, swapped)"),
    (0x18C, 0x208, "draw images"),
    (0x22C, 0x230, "PS3-only word"),
    (0x28C, 0x294, "material memory count and pointer"),
    (0x298, 0x2A0, "sun sprite and sun flare materials"),
)
#: Kept from the target as well when the PC map's lightmaps and probes are not converted
#: (``baked`` off): the surfaces then index the target's lightmaps and probes.
STOCK_LIGHT_RANGES = ((0x16C, 0x18C, "reflection probes, lightmaps"),)
#: Kept when the PC map has no outdoor image.
STOCK_OUTDOOR_RANGES = ((0x334, 0x338, "outdoor image"),)
#: Node keys taken from the stock GfxWorld in every mode.
STOCK_KEYS = (
    "sky_image",
    "sky_box_model",
    "sun_light",
    "draw_images",
    "material_memory",
    "sun_sprite_material",
    "sun_flare_material",
)
LIGHT_KEYS = ("reflection_probes", "lightmaps")
#: The last light grid colour: cod2rad writes 15009d x 56, the PS3 build of every MP map
#: examined holds 40059d x 56 there (PC 0x357599a vs PS3 0x1da2fb5 in mp_nuked; the same in
#: mp_firingrange, mp_havoc, mp_villa, mp_cracked; box-lighting.md 3). INFERRED: the colour
#: used outside the grid.
PC_GRID_DEFAULT = bytes.fromhex("15009d") * 56
PS3_GRID_DEFAULT = bytes.fromhex("40059d") * 56
GRID_COLOUR = 0xA8
#: PC arrays that are swapped struct by struct when present (same layout on PS3).
_GFX_ARRAYS = {
    "aabb_trees": "GfxStreamingAabbTree",
    "leaf_refs": "u32",
    "sky_start_surfs": "u32",
    "coronas": "GfxLightCorona",
    "shadow_map_volumes": "GfxShadowMapVolume",
    "shadow_map_volume_planes": "GfxVolumePlane",
    "exposure_volumes": "GfxExposureVolume",
    "exposure_volume_planes": "GfxVolumePlane",
    "planes": "cplane_s",
    "nodes": "u16",
    "models": "GfxBrushModel",
    "sorted_surf_index": "u16",
    "cull_groups": "GfxCullGroup",
    "occluders": "Occluder",
    "outdoor_bounds": "GfxOutdoorBounds",
    "world_lod_chains": "GfxWorldLodChain",
    "world_lod_infos": "GfxWorldLodInfo",
    "world_lod_surfaces": "u32",
}
#: PC keys the converter refuses when they hold data (no PS3 conversion yet).
_UNSUPPORTED = (
    "smodel_insts",
    "smodel_draw_insts",
    "hero_lights",
    "hero_light_tree",
    "water_buffer0",
    "water_buffer1",
)


@dataclass
class GfxResult:
    node: dict
    groups: list[Group]
    #: (node, key) -> [(lo, hi)] PS3 header byte ranges whose pointers are the target's.
    stock_ranges: list[tuple[int, int]]
    #: Element nodes created here whose pointer fields hold target-zone values.
    stock_nodes: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    #: Node keys whose data is the target's (``Splice.moved`` renames them).
    stock_keys: tuple[str, ...] = STOCK_KEYS
    #: Nodes built here from PC data (converted images, probe and lightmap records).
    new_nodes: list[dict] = field(default_factory=list)
    #: What was converted: images (name, format, size, sha1), probes, grid, sun.
    lighting: dict = field(default_factory=dict)


def surface_material_name(element: dict) -> str | None:
    m = element.get("material")
    if isinstance(m, dict):
        return m.get("name")
    return getattr(m, "name", None)


def convert_gfx(
    pc: dict,
    stock: dict,
    material_word: dict[str, bytes],
    light_bytes: list[bytes] | None = None,
    strict: bool = True,
    baked: bool = False,
) -> GfxResult:
    """Turn the parsed PC GfxWorld ``pc`` into a PS3 one, in place (the dict keeps its
    identity so offset pointers into it, such as the clipMap's planes, still name it).
    ``stock`` is the target zone's GfxWorld, whose images, materials, probes and sun are
    kept. ``material_word[name]`` is the +0x40 word of a target surface using that
    material. ``baked``: convert the PC map's own cod2rad lightmaps, reflection probes and
    outdoor image (``lightmaps``) instead of keeping the target's."""
    for key in _UNSUPPORTED:
        if pc.get(key):
            raise ConvertError(f"PC GfxWorld {key}: holds data; not supported yet")
    pc_header = bytes(pc["header"])
    header = gfx_header(pc_header, bytes(stock["header"]))
    ranges = list(STOCK_HEADER_RANGES)
    stock_keys = list(STOCK_KEYS)
    if not baked:
        ranges += STOCK_LIGHT_RANGES
        stock_keys += LIGHT_KEYS
    if pc.get("outdoor_image") is None or not baked:
        ranges += STOCK_OUTDOOR_RANGES
        stock_keys.append("outdoor_image")
    for lo, hi, _ in ranges:
        header[lo:hi] = bytes(stock["header"])[lo:hi]

    surfaces = [PCSurface.parse(i, e["raw"]) for i, e in enumerate(pc.get("surfaces") or [])]
    layer_pc = bytes(pc.get("vertex_layer_data") or b"")
    groups = plan_groups(surfaces, len(layer_pc))
    positions, layer, notes = convert_vertices(
        bytes(pc.get("vertices") or b""), layer_pc, groups, strict
    )
    words = []
    for e, s in zip(pc.get("surfaces") or [], surfaces, strict=True):
        name = surface_material_name(e)
        if name not in material_word:
            raise ConvertError(
                f"PC surface {s.index}: material {name!r} is not used by any surface of the "
                "target map; pick a target that has it (docs/convert.md)"
            )
        words.append(material_word[name])
    records, indices = convert_surfaces(
        surfaces,
        bytes(pc.get("indices") or b""),
        groups,
        words,
        light_bytes,
        bytes(pc.get("vertices") or b""),
    )

    vertex_count = sum(g.count for g in groups)
    if vertex_count != struct.unpack_from(">I", header, 0x208)[0]:
        notes.append(
            f"vertex count {struct.unpack_from('>I', header, 0x208)[0]} -> {vertex_count} "
            "(vertices no surface uses are dropped)"
        )
    struct.pack_into(">I", header, 0x208, vertex_count)
    struct.pack_into(">I", header, 0x20C, PTR_INLINE if vertex_count else 0)
    struct.pack_into(">I", header, 0x210, 0)
    struct.pack_into(">I", header, 0x214, len(layer))
    struct.pack_into(">I", header, 0x218, PTR_INLINE if layer else 0)
    struct.pack_into(">I", header, 0x224, len(indices) // 2)
    struct.pack_into(">I", header, 0x228, PTR_INLINE if indices else 0)

    # Rebuild the node with the PS3 handler's keys, keeping the dict itself.
    old = dict(pc)
    pc.clear()
    pc["_t"] = "GfxWorld"
    pc["header"] = bytes(header)
    for key in ("name", "base_name"):
        pc[key] = old.get(key)
    for key, struct_name in _GFX_ARRAYS.items():
        value = old.get(key)
        pc[key] = None if value is None else swap_struct(struct_name, value)
    for key in stock_keys:
        pc[key] = stock.get(key)
    lighting: dict = {}
    new_nodes: list[dict] = []
    if baked:
        from opent5.convert import lightmaps

        lighting = lightmaps.convert_lighting(old, pc, new_nodes)
    lighting["sun_light"] = convert_sun(old.get("sun_light"), pc.get("sun_light"))
    cells = old.get("cells") or []
    for c in cells:
        if c.get("cull_groups"):
            raise ConvertError("PC GfxCell cullGroups: holds data; the PS3 cell has none")
        c.pop("cull_groups", None)
        c["_t"] = "GfxCell"
        c["raw"] = swap_struct("GfxCell", c["raw"])
        for t in c.get("aabb_tree") or ():
            t["_t"] = "GfxAabbTree"
            t["raw"] = swap_struct("GfxAabbTree", t["raw"])
            if t.get("smodel_indexes") is not None:
                t["smodel_indexes"] = swap_struct("u16", t["smodel_indexes"])
        for p in c.get("portals") or ():
            p["_t"] = "GfxPortal"
            p["raw"] = swap_struct("GfxPortal", p["raw"])
            p["vertices"] = swap_struct("vec3", p["vertices"])
    pc["cells"] = old.get("cells")
    pc["vertices"] = positions or None
    pc["vertex_layer_data"] = layer or None
    pc["indices"] = indices or None
    grid = old.get("light_grid") or {}
    grid["_t"] = "GfxLightGrid"
    if grid.get("raw_row_data") is not None:
        grid["raw_row_data"] = light_grid_rows(grid["raw_row_data"], grid.get("row_data_start"))
    for key, struct_name in (
        ("row_data_start", "u16"),
        ("entries", "GfxLightGridEntry"),
        ("colors", "bytes"),
    ):
        if grid.get(key) is not None:
            grid[key] = swap_struct(struct_name, bytes(grid[key]))
    lighting["light_grid"] = grid_default(grid)
    pc["light_grid"] = grid
    for e in old.get("shadow_geom") or ():
        e["_t"] = "GfxShadowGeometry"
        e["raw"] = swap_struct("GfxShadowGeometry", e["raw"])
        for key in ("sorted_surf_index", "smodel_index"):
            if e.get(key) is not None:
                e[key] = swap_struct("u16", e[key])
    pc["shadow_geom"] = old.get("shadow_geom")
    for e in old.get("light_region") or ():
        e["_t"] = "GfxLightRegion"
        e["raw"] = swap_struct("GfxLightRegion", e["raw"])
        for hull in e.get("hulls") or ():
            hull["_t"] = "GfxLightRegionHull"
            hull["raw"] = swap_struct("GfxLightRegionHull", hull["raw"])
            if hull.get("axis") is not None:
                hull["axis"] = swap_struct("GfxLightRegionAxis", hull["axis"])
    pc["light_region"] = old.get("light_region")
    pc["smodel_insts"] = None
    pc["smodel_draw_insts"] = None
    surface_nodes = [{"_t": "GfxSurface", "raw": rec, "material": None} for rec in records]
    pc["surfaces"] = surface_nodes or None
    for key in ("water_buffer0", "water_buffer1", "hero_lights", "hero_light_tree"):
        pc[key] = None
    for key in ("water_material", "corona_material", "rope_material"):
        pc[key] = old.get(key)
    return GfxResult(
        pc,
        groups,
        [(lo, hi) for lo, hi, _ in ranges],
        surface_nodes,
        notes,
        tuple(stock_keys),
        new_nodes,
        lighting,
    )


def convert_sun(pc_sun: dict | None, stock_sun: dict | None) -> str:
    """The sun GfxLight (0x170) from the PC map, into the target's node: every 32-bit word
    swapped except +0 (type and flag bytes); +0x160 (the lightdef pointer) stays the
    target's. PC mp_nuked converted this way equals PS3 mp_nuked, 0 words differ
    (box-lighting.md 5)."""
    if not isinstance(pc_sun, dict) or pc_sun.get("raw") is None:
        return "kept: the PC map has no sun light"
    if not isinstance(stock_sun, dict) or stock_sun.get("raw") is None:
        return "not converted: the target has no sun light record"
    raw = bytes(pc_sun["raw"])
    out = bytearray(np.frombuffer(raw, "<u4").astype(">u4").tobytes())
    out[0:4] = raw[0:4]
    old = bytes(stock_sun["raw"])
    out[0x160:0x164] = old[0x160:0x164]
    changed = sum(1 for i in range(0, len(out), 4) if out[i : i + 4] != old[i : i + 4])
    stock_sun["raw"] = bytes(out)
    return f"PC sun light, swapped ({changed} words differ from the target's)"


def grid_default(grid: dict) -> str:
    """Replace a last light grid colour equal to the cod2rad default by the PS3 one."""
    colours = grid.get("colors")
    if not colours or len(colours) < GRID_COLOUR:
        return "no colours"
    if bytes(colours[-GRID_COLOUR:]) != PC_GRID_DEFAULT:
        return "last colour is not the cod2rad default: kept"
    grid["colors"] = bytes(colours[:-GRID_COLOUR]) + PS3_GRID_DEFAULT
    return f"{len(colours) // GRID_COLOUR} colours; last 15009d x 56 -> 40059d x 56"


def stock_material_words(stock: dict) -> dict[str, tuple[bytes, Any]]:
    """Material name -> (+0x40 word, material link) of the first target surface using it."""
    out: dict[str, tuple[bytes, Any]] = {}
    for s in stock.get("surfaces") or ():
        name = surface_material_name(s)
        if name and name not in out:
            out[name] = (bytes(s["raw"][0x40:0x44]), s.get("material"))
    return out
