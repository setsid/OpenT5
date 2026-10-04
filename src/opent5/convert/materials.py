"""PC materials and their images for a converted map: reuse the base zone's PS3 material of
the same name, or build a new PS3 material whose images live in the map's own zone.

API (other converter modules call these; docs/convert.md section 11):

- ``convert_materials(pc_gfx, stock_gfx, base, ...) -> MaterialsResult``: before
  ``world.convert_gfx``. For every material the PC surfaces use: a stock PS3 material of
  the same name in the base zone's world is reused (the current behaviour) unless forced;
  otherwise the PC material node is turned into a PS3 one in place (techset remapped by
  name onto a PS3 techset of the base zone, state bits from a stock material with that
  techset, texture definitions and constants byte-swapped, images reused by name,
  referenced as a placeholder when an always-loaded zone has them, or converted into the
  map's own zone with their pixels in the end-of-zone block).
- ``attach(result, pc_gfx, surface_nodes, splice)``: after ``world.convert_gfx`` and
  before ``Splice.build``: appends the new MaterialMemory elements and tells the splice
  which parse each new pointer value comes from.
- ``read_iwi(data)``, ``ImageFiles(roots)``: the PC ``.iwi`` images (``raw/images`` of a
  game folder, or ``main/*.iwd``) that the PC zone names but does not carry.
- ``remap_techset(...)``, ``techset_samplers(...)``, ``shared_image_names(...)``.

Rules and their evidence (docs/demo-box-textures.md section 2):

- PC Material (192, OpenAssetTools T5 ``Material``) -> PS3 Material (0x80,
  structs-content.md 8): name, gameFlags, sortKey and atlas bytes, drawSurf (8 bytes copied
  unswapped), surfaceTypeBits, layeredSurfaceTypes, textureCount, constantCount,
  stateFlags, cameraRegion, maxStreamedMips from the PC header; PC mp_nuked against PS3
  mp_nuked, 182 world materials: every one of those fields agrees, drawSurf byte for byte
  in 181 (one materialSortedIndex differs).
- stateBitsEntry (PC 130 techniques, PS3 71), stateBitsCount and the state bits table
  differ per platform (20 of 182 tables agree after a swap), so they come from a stock
  PS3 material with the same techset; that choice reproduces the real PS3 state bits for
  156 of 165 nuked materials that have another material with their techset.
- MaterialTextureDef and MaterialConstantDef: same layout, the hash (and the floats)
  swapped; texture definitions agree in all single-layer nuked materials.
- MaterialMemory.memory = 44 x vertices + 6 x triangles + 160 x surfaces of the PS3
  surfaces drawn with the material (exact for all 182 nuked materials; PC: 128 per
  surface).
"""

from __future__ import annotations

import difflib
import re
import struct
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from opent5.convert import images as imgconv
from opent5.convert.world import ConvertError, PCSurface, surface_material_name
from opent5.export.zone import SAMPLER_BY_HASH
from opent5.formats import texture as tx
from opent5.xfile.constants import PTR_INLINE, PTR_NULL, AssetType, encode_offset_pointer
from opent5.xfile.stream import AssetLink

PS3_MATERIAL_HEADER = 0x80
PC_MATERIAL_HEADER = 0xC0
#: PS3 GfxWorld header: materialMemoryCount (structs-map.md; xfile/handlers/gfxworld.py).
GFX_MATERIAL_MEMORY_COUNT = 0x28C
#: MaterialShaderArgument types (PS3 techset pass args, +0 u16): 2 names a material
#: sampler by hash, 6 a material constant by hash (wc_l_sm_r0c0n0s0: colorMap, normalMap,
#: specularMap as type 2; wc_l_sm_r0c0: colorMap only).
ARG_MATERIAL_SAMPLER = 2
ARG_MATERIAL_CONSTANT = 6
#: Zones every MP map is loaded with; an image they hold is referenced from a map zone by
#: a placeholder named ",<name>" (PS3 mp_nuked ``,$identitynormalmap``: 0x70 zero bytes,
#: name -1; its pixels are code_post_gfx_mp's).
SHARED_ZONES = ("code_post_gfx_mp", "common_mp")

# IWI version 13 (OpenAssetTools src/ObjImage/Image/IwiTypes.h, namespace iwi13).
IWI_MAGIC = b"IWi"
IWI_VERSION = 13
IWI_HEADER = 48
IWI_FLAG_NOMIPMAPS = 0x2
IWI_FLAG_CUBEMAP = 0x4
IWI_FLAG_VOLMAP = 0x8
#: iwi13 format -> PC loadDef format (as the PC zone stores it) and bytes per texel (0 =
#: block format).
IWI_FORMATS = {
    0x0B: (b"DXT1", 0),
    0x0C: (b"DXT3", 0),
    0x0D: (b"DXT5", 0),
    0x01: (struct.pack("<I", 21), 4),  # BITMAP_RGBA: B, G, R, A bytes (D3DFMT_A8R8G8B8)
    0x04: (struct.pack("<I", 50), 1),  # BITMAP_LUMINANCE (D3DFMT_L8)
}


@dataclass
class Iwi:
    format: int
    flags: int
    width: int
    height: int
    depth: int
    #: Pixels in PC loadDef order: per face, levels largest first.
    pixels: bytes
    levels: int
    load_format: bytes


def read_iwi(data: bytes, name: str = "") -> Iwi:
    """An IWI v13 file: 48-byte header, then mip levels smallest first, each holding all
    faces (OpenAssetTools ``LoadIwi13``). Returned in PC loadDef order."""
    if data[:3] != IWI_MAGIC or len(data) < IWI_HEADER:
        raise ConvertError(f"iwi {name!r} at 0x0: expected 'IWi', found {bytes(data[:3])!r}")
    if data[3] != IWI_VERSION:
        raise ConvertError(f"iwi {name!r} at 0x3: expected version 13, found {data[3]}")
    fmt, flags = data[4], data[5]
    width, height, depth = struct.unpack_from("<3H", data, 6)
    if fmt not in IWI_FORMATS:
        raise ConvertError(
            f"iwi {name!r} at 0x4: format {fmt:#x} is not one of "
            f"{sorted(hex(f) for f in IWI_FORMATS)}"
        )
    if flags & IWI_FLAG_VOLMAP or depth != 1:
        raise ConvertError(f"iwi {name!r}: volume image (flags {flags:#x}, depth {depth})")
    load_format, bpp = IWI_FORMATS[fmt]
    levels = 1 if flags & IWI_FLAG_NOMIPMAPS else tx.full_levels(width, height)
    faces = 6 if flags & IWI_FLAG_CUBEMAP else 1
    dims = list(tx.mip_dims(width, height, levels))

    def size(w: int, h: int) -> int:
        return w * h * bpp if bpp else tx.level_size(_BLOCK[load_format], w, h)

    at = IWI_HEADER
    per_level: dict[int, list[bytes]] = {}
    for k in range(levels - 1, -1, -1):
        n = size(*dims[k])
        chunk = data[at : at + n * faces]
        if len(chunk) != n * faces:
            raise ConvertError(
                f"iwi {name!r}: level {dims[k][0]}x{dims[k][1]} needs {n * faces} bytes at "
                f"{at:#x}, the file has {len(data) - at}"
            )
        per_level[k] = [chunk[f * n : (f + 1) * n] for f in range(faces)]
        at += n * faces
    if at != len(data):
        raise ConvertError(f"iwi {name!r}: {len(data)} bytes, the levels use {at}")
    pixels = b"".join(per_level[k][f] for f in range(faces) for k in range(levels))
    return Iwi(fmt, flags, width, height, depth, pixels, levels, load_format)


_BLOCK = {b"DXT1": tx.DXT1, b"DXT3": tx.DXT23, b"DXT5": tx.DXT45}


class ImageFiles:
    """Finds ``<name>.iwi`` under game folders: ``raw/images/`` first (what the PC linker
    read), then ``images/`` inside ``main/*.iwd`` (zip archives; read only)."""

    def __init__(self, roots):
        self.roots = [Path(r) for r in roots]
        self._iwd: dict[str, tuple[Path, str]] | None = None

    def _index(self) -> dict[str, tuple[Path, str]]:
        if self._iwd is None:
            self._iwd = {}
            for root in self.roots:
                for iwd in sorted((root / "main").glob("*.iwd")):
                    try:
                        with zipfile.ZipFile(iwd) as z:
                            for n in z.namelist():
                                low = n.lower()
                                if low.startswith("images/") and low.endswith(".iwi"):
                                    self._iwd[low[7:-4]] = (iwd, n)
                    except zipfile.BadZipFile:
                        continue
        return self._iwd

    def find(self, name: str) -> tuple[bytes, str] | None:
        """(file bytes, where) or None."""
        for root in self.roots:
            p = root / "raw" / "images" / f"{name}.iwi"
            if p.is_file():
                return p.read_bytes(), str(p)
        hit = self._index().get(name.lower())
        if hit is not None:
            with zipfile.ZipFile(hit[0]) as z:
                return z.read(hit[1]), f"{hit[0]}:{hit[1]}"
        return None


# -- techsets ----------------------------------------------------------------------------


def techset_samplers(techset: dict) -> tuple[frozenset[int], frozenset[int]]:
    """(sampler name hashes, constant name hashes) a PS3 techset's passes read from the
    material."""
    samplers: set[int] = set()
    constants: set[int] = set()
    for t in techset.get("techniques") or ():
        if not isinstance(t, dict):
            continue
        for p in t.get("passes") or ():
            for a in p.get("args") or ():
                kind, _, value = struct.unpack(">HHI", bytes(a["raw"]))
                if kind == ARG_MATERIAL_SAMPLER:
                    samplers.add(value)
                elif kind == ARG_MATERIAL_CONSTANT:
                    constants.add(value)
    return frozenset(samplers), frozenset(constants)


_LAYER_CODE = re.compile(r"(?:[a-z]\d)+")


def _techset_shape(name: str) -> tuple[str, str]:
    """(family, layer codes): ``wc_l_sm_r0c0n0s0x0`` -> ("wc_l_sm", "r0c0n0s0x0")."""
    parts = name.split("_")
    for i, p in enumerate(parts):
        if _LAYER_CODE.fullmatch(p):
            return "_".join(parts[:i]), "_".join(parts[i:])
    return name, ""


def _ops(code: str) -> tuple[str, ...]:
    """The first letter of each layer code (``r0c0n0s0_m1c1`` -> ("r", "m")): how the
    layer is drawn (INFERRED: r replace, b blend, m multiply, t alpha test)."""
    return tuple(t[0] for t in code.split("_") if _LAYER_CODE.fullmatch(t))


def _extras(code: str) -> tuple[str, ...]:
    """Words after the layer codes (``r0c0_seethru`` -> ("seethru",))."""
    return tuple(t for t in code.split("_") if t and not _LAYER_CODE.fullmatch(t))


@dataclass
class TechsetChoice:
    pc_name: str
    name: str
    how: str  # "same name", "same name, placeholder", "closest"
    samplers: list[str] = field(default_factory=list)
    candidates: int = 0


def remap_techset(
    pc_name: str,
    textures: set[int],
    constants: set[int],
    base_techsets: dict[str, dict],
) -> TechsetChoice:
    """The PS3 techset of the base zone a PC material is drawn with: the same name when
    the base zone has it (or a placeholder of that name); otherwise the techset of the same
    family (``wc_l_sm`` ...) whose sampler and constant sets the material can feed, with
    the same layer kinds and suffixes first, then the most samplers in common and the
    closest name. PS3 RSX programs cannot be made from PC D3D9 ones (pc-route.md 3)."""
    real = {n: t for n, t in base_techsets.items() if not n.startswith(",")}
    if pc_name in real:
        s, _ = techset_samplers(real[pc_name])
        return TechsetChoice(pc_name, pc_name, "same name", _names(s), 1)
    if "," + pc_name in base_techsets:
        # A placeholder: the techset itself is in an always-loaded zone (43 of PS3
        # mp_nuked's 104 techsets are such, e.g. ",wc_l_sm_b0c0n0s0").
        return TechsetChoice(pc_name, "," + pc_name, "same name, placeholder", [], 1)
    family, code = _techset_shape(pc_name)
    best = None
    count = 0
    for name, ts in real.items():
        fam, other = _techset_shape(name)
        if fam != family:
            continue
        s, c = techset_samplers(ts)
        if not s or not s <= textures or not c <= constants:
            continue
        count += 1
        score = (
            _ops(other) == _ops(code),
            _extras(other) == _extras(code),
            len(s),
            difflib.SequenceMatcher(None, code, other).ratio(),
            -len(name),
        )
        if best is None or score > best[0]:
            best = (score, name, s)
    if best is None:
        raise ConvertError(
            f"techset {pc_name!r}: not in the base zone and no {family!r} techset there reads "
            f"only the material's samplers {_names(textures)}"
        )
    return TechsetChoice(pc_name, best[1], "closest", _names(best[2]), count)


def _names(hashes) -> list[str]:
    return sorted(SAMPLER_BY_HASH.get(h, f"{h:#010x}") for h in hashes)


# -- the base zone ---------------------------------------------------------------------


@dataclass
class _Base:
    techsets: dict[str, dict]
    #: techset name -> stock materials (dicts) drawn with it, in zone order
    by_techset: dict[str, list[dict]]
    #: image name -> a BASE pointer value naming it
    images: dict[str, int]
    #: the base XFile (state bits behind offset pointers)
    xfile: Any = None

    def bits(self, element: dict, depth: int = 0) -> bytes | None:
        """The 8 GfxStateBits bytes a state-bits table element names (through chains of
        offset pointers to earlier elements)."""
        if element.get("bits") is not None:
            return bytes(element["bits"])
        if depth > 64 or self.xfile is None:
            return None
        t = self.xfile.resolve(struct.unpack(">I", bytes(element["raw"]))[0])
        if t is None or not isinstance(t.element, dict):
            return None
        return self.bits(t.element, depth + 1)


def _walk(o: Any, fn) -> None:
    stack = [o]
    seen: set[int] = set()
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            if id(cur) in seen:
                continue
            seen.add(id(cur))
            fn(cur)
            stack.extend(v for v in cur.values() if isinstance(v, dict | list))
        elif isinstance(cur, list):
            stack.extend(v for v in cur if isinstance(v, dict | list))


def _scan_base(base) -> _Base:
    """Techsets, materials and image pointer values of the base parse (a Rewrite)."""
    x = base.xfile
    techsets = {a.name: a.data for a in x.assets if a.type == AssetType.TECHSET and a.name}
    by_techset: dict[str, list[dict]] = {}
    images: dict[str, int] = {}
    rows = x.log.table()

    def visit(node: dict) -> None:
        if node.get("_t") != "Material" or "header" not in node:
            return
        ts = node.get("technique_set")
        name = ts.get("name") if isinstance(ts, dict) else getattr(ts, "name", None)
        if name:
            by_techset.setdefault(name, []).append(node)
        for k, t in enumerate(node.get("textures") or ()):
            im = t.get("image")
            if isinstance(im, AssetLink) and im.name and im.name not in images:
                images[im.name] = im.raw
            elif isinstance(im, dict) and im.get("name") and im["name"] not in images:
                # Loaded here (-1): later references name this field (+0xc of the def).
                event = base.event_of(("L", id(node), "textures"))
                if event is not None:
                    block, mem = int(rows[event][3]), int(rows[event][4])
                    images[im["name"]] = encode_offset_pointer(block, mem + 16 * k + 0xC)

    for a in x.assets:
        _walk(a.data, visit)
    return _Base(techsets, by_techset, images, x)


def shared_image_names(zone_paths) -> set[str]:
    """Names of the images (not placeholders) held by the given PS3 zones."""
    from opent5.container.zone import Zone
    from opent5.xfile import parse

    names: set[str] = set()
    for p in zone_paths:
        x = parse(bytes(Zone.open(p).content), log=False)

        def visit(node: dict) -> None:
            name = node.get("name")
            if node.get("_t") == "GfxImage" and name and not name.startswith(","):
                names.add(name)

        for a in x.assets:
            _walk(a.data, visit)
    return names


# -- conversion ------------------------------------------------------------------------


@dataclass
class MaterialsResult:
    #: material name -> the surface +0x40 word (BE bytes)
    words: dict[str, bytes] = field(default_factory=dict)
    #: material name -> "base" (stock alias value) or "foreign" (PC value)
    sources: dict[str, str] = field(default_factory=dict)
    #: new MaterialMemory element nodes (the PC ones, converted in place), in PC order
    elements: list[dict] = field(default_factory=list)
    #: nodes whose pointer fields hold base-zone values, and (node, key, ranges)
    base_nodes: list[dict] = field(default_factory=list)
    base_ranges: list[tuple[dict, str, list[tuple[int, int]]]] = field(default_factory=list)
    report: dict = field(default_factory=dict)

    @property
    def new_pixel_bytes(self) -> int:
        done = self.report.get("images", {}).values()
        return sum(i["bytes"] for i in done if i["action"] == "converted")


def _mm_material(element: dict) -> dict | None:
    m = element.get("material")
    return m if isinstance(m, dict) else getattr(m, "target", None)


def _swap_constants(pc: bytes) -> bytes:
    out = bytearray()
    for i in range(0, len(pc), 32):
        c = pc[i : i + 32]
        out += c[0:4][::-1] + c[4:16] + struct.pack(">4f", *struct.unpack_from("<4f", c, 16))
    return bytes(out)


def _template(choices: list[dict], pc_header: bytes, pc_bits: bytes, scan: _Base) -> dict:
    """The stock material whose state bits a new one takes: same stateFlags and
    cameraRegion, then the most PC state bits words (swapped) found among its own, then
    the first in zone order."""
    want = {pc_bits[i : i + 8] for i in range(0, len(pc_bits), 8)}

    def score(m: dict) -> tuple:
        h = bytes(m["header"])
        own = {scan.bits(e) for e in m.get("state_bits") or ()}
        swapped = {struct.pack(">II", *struct.unpack("<II", w)) for w in want}
        return (h[0x6A] == pc_header[0xAD], h[0x6B] == pc_header[0xAE], len(own & swapped))

    usable = [m for m in choices if struct.unpack_from(">I", m["header"], 0x70)[0] != PTR_INLINE]
    if not usable:
        raise ConvertError("no stock material with an alias to the techset to copy state bits from")
    return max(usable, key=score)


def ps3_material_header(pc: bytes, template: bytes) -> bytes:
    """The PS3 Material (0x80) from a PC one (0xc0) and the stock template's state bits
    and techset pointer (see the module docstring)."""
    if len(pc) != PC_MATERIAL_HEADER or len(template) != PS3_MATERIAL_HEADER:
        raise ConvertError(
            f"material header: expected 0xc0 (PC) and 0x80 (PS3) bytes, found "
            f"{len(pc):#x} and {len(template):#x}"
        )
    h = bytearray(PS3_MATERIAL_HEADER)
    struct.pack_into(">I", h, 0, PTR_INLINE)
    struct.pack_into(">I", h, 4, struct.unpack_from("<I", pc, 4)[0])
    h[8:0x18] = pc[8:0x18]
    struct.pack_into(">2I", h, 0x18, *struct.unpack_from("<2I", pc, 0x18))
    h[0x20:0x67] = template[0x20:0x67]
    h[0x67], h[0x68] = pc[0xAA], pc[0xAB]
    h[0x69] = template[0x69]
    h[0x6A:0x6D] = pc[0xAD:0xB0]
    h[0x70:0x74] = template[0x70:0x74]
    struct.pack_into(">I", h, 0x74, PTR_INLINE if pc[0xAA] else PTR_NULL)
    struct.pack_into(">I", h, 0x78, PTR_INLINE if pc[0xAB] else PTR_NULL)
    struct.pack_into(">I", h, 0x7C, PTR_INLINE if template[0x69] else PTR_NULL)
    return bytes(h)


def placeholder_image(name: str) -> dict:
    """A reference to an image of another zone: 0x70 zero bytes, name -1, ",<name>"."""
    h = bytearray(imgconv.PS3_IMAGE_HEADER)
    struct.pack_into(">I", h, 0x68, PTR_INLINE)
    return {"_t": "GfxImage", "header": bytes(h), "name": "," + name, "pixels": None}


def override_image(pc_node: dict, rgba: np.ndarray) -> dict:
    """A PS3 image named as the PC one, keeping its mapType, semantic, category and hash,
    with pixels encoded from ``rgba`` (full mip chain; DXT1, or DXT5 when any alpha is
    below 255; textures.md 6). Width and height must be powers of two."""
    rgba = np.ascontiguousarray(rgba, dtype=np.uint8)
    if rgba.ndim != 3 or rgba.shape[2] != 4:
        raise ConvertError(f"image override {pc_node.get('name')!r}: expected (h, w, 4) RGBA")
    height, width = rgba.shape[:2]
    if width & (width - 1) or height & (height - 1):
        raise ConvertError(
            f"image override {pc_node.get('name')!r}: {width}x{height} is not a power of two"
        )
    h = bytes(pc_node["header"])
    fmt = tx.DXT1 if (rgba[..., 3] == 255).all() else tx.DXT45
    levels = [tx.encode_level(m, fmt) for m in tx.mip_chain(rgba, tx.full_levels(width, height))]
    return imgconv.new_image(
        pc_node.get("name"),
        fmt,
        [levels],
        width,
        height,
        map_type=h[4],
        semantic=h[5],
        category=h[6],
        hash_=struct.unpack_from("<I", h, 0x30)[0],
    )


def _pc_pixels(node: dict, files: ImageFiles | None) -> tuple[dict, str]:
    """A PC image node with its pixels (from the zone, or from its .iwi)."""
    ld = bytes(node.get("load_def") or b"")
    if len(ld) == 12 and struct.unpack_from("<I", ld, 8)[0]:
        return node, "PC zone"
    name = node.get("name")
    found = files.find(name) if files is not None else None
    if found is None:
        raise ConvertError(
            f"image {name!r}: the PC zone does not carry its pixels and no {name}.iwi was "
            "found (raw/images or main/*.iwd of the PC game folder)"
        )
    iwi = read_iwi(found[0], name)
    h = bytes(node["header"])
    width, height = struct.unpack_from("<2H", h, 0x14)
    if (width, height) != (iwi.width, iwi.height):
        raise ConvertError(
            f"image {name!r}: the PC zone says {width}x{height}, {found[1]} holds "
            f"{iwi.width}x{iwi.height}"
        )
    load_def = bytes((iwi.levels, 0, 0, 0)) + iwi.load_format + struct.pack("<I", len(iwi.pixels))
    return {"name": name, "header": h, "load_def": load_def, "pixels": iwi.pixels}, found[1]


def convert_materials(
    pc_gfx: dict,
    stock_gfx: dict,
    base,
    files: ImageFiles | None = None,
    shared_images: set[str] | frozenset[str] = frozenset(),
    force: set[str] | frozenset[str] | bool = frozenset(),
    overrides: dict[str, np.ndarray] | None = None,
) -> MaterialsResult:
    """See the module docstring. ``pc_gfx``: the parsed PC GfxWorld (before
    ``world.convert_gfx``); ``stock_gfx``: the base zone's GfxWorld; ``base``: the base
    ``Rewrite``; ``files``: where the PC ``.iwi`` images are; ``shared_images``: image
    names of the always-loaded zones (``shared_image_names``); ``force``: material names
    (or True for all) to build anew even when the base has them; ``overrides``: image name
    -> RGBA (h, w, 4) uint8 drawn instead of the PC pixels (the map's own art without the
    PC converter; ``override_image``)."""
    result = MaterialsResult()
    report: dict = {"materials": {}, "techsets": {}, "images": {}}
    result.report = report
    stock: dict[str, bytes] = {}
    for s in stock_gfx.get("surfaces") or ():
        n = surface_material_name(s)
        if n and n not in stock:
            stock[n] = bytes(s["raw"][0x40:0x44])
    surfaces = pc_gfx.get("surfaces") or []
    counts: dict[str, list[int]] = {}
    pc_words: dict[str, bytes] = {}
    for i, e in enumerate(surfaces):
        n = surface_material_name(e)
        s = PCSurface.parse(i, bytes(e["raw"]))
        c = counts.setdefault(n, [0, 0, 0])
        c[0] += s.vertex_count
        c[1] += s.tri_count
        c[2] += 1
        pc_words.setdefault(n, struct.pack(">I", struct.unpack_from("<I", e["raw"], 0x30)[0]))
    wanted = [n for n in counts]
    new = [
        n for n in wanted if n not in stock or force is True or (force is not True and n in force)
    ]
    for n in wanted:
        if n not in new:
            result.words[n] = stock[n]
            result.sources[n] = "base"
            report["materials"][n] = {"action": "reused: the base zone's PS3 material"}
    if not new:
        return result

    scan = _scan_base(base)
    by_name = {}
    for e in pc_gfx.get("material_memory") or ():
        m = _mm_material(e)
        if isinstance(m, dict) and m.get("name") in new and m["name"] not in by_name:
            by_name[m["name"]] = (e, m)
    converted: dict[int, dict] = {}
    for e in pc_gfx.get("material_memory") or ():
        m = _mm_material(e)
        if not isinstance(m, dict) or m.get("name") not in new or by_name[m["name"]][0] is not e:
            continue
        name = m["name"]
        if not isinstance(e.get("material"), dict):
            raise ConvertError(
                f"material {name!r}: its PC MaterialMemory entry is an alias, not the material"
            )
        result.report["materials"][name] = _convert_one(
            name, e, m, counts[name], scan, files, shared_images, converted, result, overrides or {}
        )
        result.words[name] = pc_words[name]
        result.sources[name] = "foreign"
        result.elements.append(e)
    missing = [n for n in new if n not in result.words]
    if missing:
        raise ConvertError(f"materials {missing}: not found inline in the PC MaterialMemory")
    return result


def _convert_one(
    name, element, m, count, scan, files, shared, converted, result, overrides
) -> dict:
    pc_header = bytes(m["header"])
    ts = m.get("techset")
    pc_ts = ts.get("name") if isinstance(ts, dict) else getattr(ts, "name", None)
    if not pc_ts:
        raise ConvertError(f"material {name!r}: its PC techset has no name")
    textures = m.get("textures") or []
    hashes = {struct.unpack_from("<I", t["raw"], 0)[0] for t in textures}
    pc_consts = bytes(m.get("constants") or b"")
    const_hashes = {struct.unpack_from("<I", pc_consts, i)[0] for i in range(0, len(pc_consts), 32)}
    choice = remap_techset(pc_ts, hashes, const_hashes, scan.techsets)
    result.report["techsets"][pc_ts] = {
        "ps3": choice.name,
        "how": choice.how,
        "samplers": choice.samplers,
    }
    stock = scan.by_techset.get(choice.name) or []
    if not stock:
        raise ConvertError(
            f"material {name!r}: no stock material of the base zone uses techset "
            f"{choice.name!r} (its state bits and techset pointer are copied from one)"
        )
    template = _template(stock, pc_header, bytes(m.get("state_bits") or b""), scan)
    header = ps3_material_header(pc_header, bytes(template["header"]))

    images = {}
    for t in textures:
        raw = bytearray(t["raw"])
        raw[0:4] = raw[0:4][::-1]
        im = t.get("image")
        target = im if isinstance(im, dict) else getattr(im, "target", None)
        iname = target.get("name") if isinstance(target, dict) else getattr(im, "name", None)
        stock_name = iname if iname in scan.images else "," + str(iname)
        if isinstance(im, dict) and iname in overrides:
            # The map's own art wins, even when an image of this name also exists in the base
            # zone (plank/water/cobble borrow mp_nuked colour maps; without this they would be
            # reused from the base at line below and show its texture). Written inline here.
            new = override_image(target, overrides[iname])
            struct.pack_into(">I", raw, 0xC, PTR_INLINE)
            target.clear()
            target.update(new)
            converted[id(target)] = target
            h = new["header"]
            images[iname] = {
                "action": "converted",
                "from": "override (RGBA)",
                "format": f"{h[0]:#x}",
                "levels": h[1],
                "size": list(struct.unpack_from(">2H", h, 8)),
                "bytes": len(new["pixels"]),
            }
        elif stock_name in scan.images:
            struct.pack_into(">I", raw, 0xC, scan.images[stock_name])
            result.base_nodes.append(t)
            images[iname] = {"action": f"reused: the base zone's {stock_name!r}"}
        elif isinstance(target, dict) and id(target) in converted:
            if isinstance(im, dict):
                raise ConvertError(f"image {iname!r}: loaded inline twice in the PC zone")
            struct.pack_into(">I", raw, 0xC, im.raw)
            images[iname] = {"action": "alias to its first converted use"}
        elif not isinstance(im, dict):
            raise ConvertError(
                f"material {name!r}: image {iname!r} is an alias to an image the converter does "
                "not write (loaded by a material that is reused from the base); use force for it"
            )
        elif iname in shared:
            struct.pack_into(">I", raw, 0xC, PTR_INLINE)
            target.clear()
            target.update(placeholder_image(iname))
            converted[id(target)] = target
            images[iname] = {"action": "placeholder: held by an always-loaded zone"}
        else:
            node, where = _pc_pixels(target, files)
            new = imgconv.convert_image(node)
            struct.pack_into(">I", raw, 0xC, PTR_INLINE)
            target.clear()
            target.update(new)
            converted[id(target)] = target
            h = new["header"]
            images[iname] = {
                "action": "converted",
                "from": where.replace("\\", "/").rsplit("/", 1)[-1],
                "format": f"{h[0]:#x}",
                "levels": h[1],
                "size": list(struct.unpack_from(">2H", h, 8)),
                "bytes": len(new["pixels"]),
            }
        t["raw"] = bytes(raw)
        t["_t"] = "MaterialTextureDef"
    for k, v in images.items():
        result.report["images"].setdefault(k, v)

    bits = []
    for e in template.get("state_bits") or ():
        el = {"_t": "MaterialStateBitsRef", "raw": bytes(e["raw"])}
        if e.get("bits") is not None:
            # A copy: no alias slot of its own (-2 would reserve one).
            el["raw"] = struct.pack(">I", PTR_INLINE)
            el["bits"] = bytes(e["bits"])
        bits.append(el)
        result.base_nodes.append(el)
    old = dict(m)
    m.clear()
    m["_t"] = "Material"
    m["header"] = header
    m["name"] = name
    m["technique_set"] = template.get("technique_set")
    m["textures"] = textures or None
    m["constants"] = _swap_constants(bytes(old.get("constants") or b"")) or None
    m["state_bits"] = bits or None
    result.base_ranges.append((m, "header", [(0x70, 0x74)]))
    vertices, triangles, surfaces = count
    memory = 44 * vertices + 6 * triangles + 160 * surfaces
    element["_t"] = "MaterialMemory"
    element["raw"] = struct.pack(">iI", -1, memory)
    return {
        "action": "new: PS3 material built from the PC one",
        "techset": choice.name,
        "techset_how": choice.how,
        "state_bits_from": template.get("name"),
        "memory": memory,
        "textures": [_image_name(t["image"]) for t in textures],
    }


def _image_name(im) -> str | None:
    return im.get("name") if isinstance(im, dict) else getattr(im, "name", None)


def attach(
    result: MaterialsResult, gfx: dict, pc_names: list[str], surface_nodes: list, splice
) -> None:
    """After ``world.convert_gfx(pc_gfx, ...)`` (``gfx`` is that node, converted in place)
    and before ``splice.build``: the MaterialMemory list becomes the stock elements plus
    the new ones (count at header +0x28c), and the splice learns which parse each new
    pointer value belongs to. ``pc_names[i]`` is the material of converted surface i."""
    from opent5.convert.splice import BASE, FOREIGN

    if not result.elements:
        return
    stock = list(gfx.get("material_memory") or [])
    gfx["material_memory"] = stock + result.elements
    header = bytearray(gfx["header"])
    struct.pack_into(">I", header, GFX_MATERIAL_MEMORY_COUNT, len(gfx["material_memory"]))
    gfx["header"] = bytes(header)
    for node in result.base_nodes:
        splice.origin(node, BASE)
    for node, key, ranges in result.base_ranges:
        splice.origin_ranges(node, key, ranges, BASE)
    for element, name in zip(surface_nodes, pc_names, strict=True):
        if result.sources.get(name) == "foreign":
            splice.origin_ranges(element, "raw", [(0x40, 0x44)], FOREIGN)
