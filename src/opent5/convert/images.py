"""PC GfxImage -> PS3 GfxImage, and new PS3 images stored in the map's own zone.

API (stable; other converter modules call these):

- ``convert_image(pc_node) -> dict``: a PC image node (as ``convert.pc`` parses it:
  ``header`` 0x34 bytes LE, ``load_def`` 12 bytes, ``pixels``, ``name``) to a new PS3
  GfxImage node (``header`` 0x70 bytes BE, ``name``, ``pixels`` bytes). The pixels are
  deferred (delayLoadPixels 1): the writer puts them in the zone's own end-of-zone block
  (PHYSICAL_RUNTIME), never in a ``.pak``. Raises ``ImageError`` (format code, name) for
  a format or shape not listed in ``PC_FORMATS``.
- ``new_image(name, fmt, faces_levels, ...) -> dict``: a PS3 GfxImage node from stored
  per-face, per-level bytes (``formats.texture`` encoders), same storage.
- ``ps3_header(...)``: the 0x70 header on its own.

The transform, proven byte for byte on mp_nuked (docs/research/box-lighting.md 2.3, 2.4, 4,
5; ``tests/test_convert_images.py``):

| PC (loadDef format) | PS3 GCM | Pixels |
|---|---|---|
| DXT1 / DXT3 / DXT5 | 0x86 / 0x87 / 0x88 | blocks unchanged (DXT is not swizzled) |
| D3DFMT_R5G6B5 (23) | R5G6B5 0x84 | each u16 swapped, Morton swizzle |
| D3DFMT_G16R16 (34) | Y16_X16 0x95, remap 0xaae4 | each u16 swapped (order kept), swizzle |
| D3DFMT_L8 (50) | B8 0x81, remap 0x1a9ff | swizzle |
| D3DFMT_A8R8G8B8 (21), X8R8G8B8 (22) | 0x85, 0x9e | INFERRED: u32 swapped, swizzle |

Cube faces (mapType 5) keep their order and are each padded to 128 bytes; a loadDef
levelCount of 0 means the full mip chain (the probes).
"""

from __future__ import annotations

import struct

import numpy as np

from opent5.formats import texture as tx
from opent5.xfile.stream import XFileError

PS3_IMAGE_HEADER = 0x70
PC_IMAGE_HEADER = 0x34
MAP_2D, MAP_CUBE = 3, 5
REMAP_DEFAULT = 0x0001AAE4

#: loadDef format (FOURCC or D3DFORMAT, as 4 LE bytes) -> (GCM format, swap unit in bytes;
#: 0 for block formats).
PC_FORMATS = {
    b"DXT1": (tx.DXT1, 0),
    b"DXT3": (tx.DXT23, 0),
    b"DXT5": (tx.DXT45, 0),
    struct.pack("<I", 23): (tx.R5G6B5, 2),
    struct.pack("<I", 34): (tx.Y16_X16, 2),
    struct.pack("<I", 50): (tx.B8, 1),
    struct.pack("<I", 21): (tx.A8R8G8B8, 4),
    struct.pack("<I", 22): (tx.D8R8G8B8, 4),
}
#: PS3 remap words that differ from 0x0001aae4 (PS3 mp_nuked ``*lightmap0_secondaryb``
#: 0x0000aae4 at 0x102e477, ``$outdoor`` 0x0001a9ff).
REMAP = {tx.Y16_X16: 0x0000AAE4, tx.B8: 0x0001A9FF}


class ImageError(XFileError):
    """A PC image the converter cannot store as a PS3 image."""


def ps3_header(
    fmt: int,
    levels: int,
    width: int,
    height: int,
    stored: int,
    map_type: int = MAP_2D,
    semantic: int = 0,
    category: int = 0,
    hash_: int = 0,
    remap: int | None = None,
) -> bytes:
    """The 0x70 PS3 GfxImage for pixels that follow inline in the deferred block:
    CellGcmTexture (+0), mapType, semantic, category, delayLoadPixels 1 (+0x18), stored size
    (+0x1c), width, height, depth (+0x20), pixels -1 (+0x2c), name -1 (+0x68), hash (+0x6c).
    Matches all six PS3 mp_nuked images of box-lighting.md 2.4 byte for byte."""
    cube = map_type == MAP_CUBE
    gcm = tx.GcmTexture(
        fmt,
        levels,
        2,
        1 if cube else 0,
        REMAP.get(fmt, REMAP_DEFAULT) if remap is None else remap,
        width,
        height,
        1,
        0,
        0,
        0,
    ).pack()
    out = bytearray(PS3_IMAGE_HEADER)
    out[0 : len(gcm)] = gcm
    out[0x18:0x1C] = bytes((map_type, semantic, category, 1))
    struct.pack_into(">I", out, 0x1C, stored)
    struct.pack_into(">3H", out, 0x20, width, height, 1)
    struct.pack_into(">I", out, 0x2C, 0xFFFFFFFF)
    struct.pack_into(">I", out, 0x68, 0xFFFFFFFF)
    struct.pack_into(">I", out, 0x6C, hash_)
    return bytes(out)


def image_node(name: str, header: bytes, pixels: bytes) -> dict:
    """A PS3 GfxImage node the product writer stores inline, pixels deferred."""
    if len(header) != PS3_IMAGE_HEADER:
        raise ImageError(f"image {name!r}: expected a 0x70-byte header, found {len(header):#x}")
    (size,) = struct.unpack_from(">I", header, 0x1C)
    if size != len(pixels):
        raise ImageError(f"image {name!r}: header size {size} at +0x1c, pixels {len(pixels)}")
    return {"_t": "GfxImage", "header": bytes(header), "name": name, "pixels": bytes(pixels)}


def _level(data: bytes, fmt: int, unit: int, width: int, height: int) -> bytes:
    if not unit:
        return data
    bpp = tx.TEXEL_BYTES[fmt]
    t = np.frombuffer(data, np.uint8).reshape(height, width, bpp)
    if unit > 1:
        t = t.reshape(height, width, bpp // unit, unit)[..., ::-1].reshape(height, width, bpp)
    return tx.swizzle(np.ascontiguousarray(t)).tobytes()


def convert_pixels(pc_node: dict) -> tuple[bytes, bytes]:
    """(PS3 header, stored pixels) of a PC image node."""
    name = pc_node.get("name")
    h = bytes(pc_node.get("header") or b"")
    ld = bytes(pc_node.get("load_def") or b"")
    px = bytes(pc_node.get("pixels") or b"")
    if len(h) != PC_IMAGE_HEADER or len(ld) != 12:
        raise ImageError(
            f"PC image {name!r}: expected a 0x34 header and a 12-byte loadDef, found "
            f"{len(h):#x} and {len(ld)} (an image without its pixels in the zone?)"
        )
    map_type, semantic, category = h[4], h[5], h[6]
    width, height, depth = struct.unpack_from("<3H", h, 0x14)
    (hash_,) = struct.unpack_from("<I", h, 0x30)
    code = ld[4:8]
    if code not in PC_FORMATS:
        raise ImageError(
            f"PC image {name!r}: loadDef format {code.hex()} at +4 is not one of "
            f"{sorted(c.hex() for c in PC_FORMATS)}"
        )
    fmt, unit = PC_FORMATS[code]
    if depth != 1 or map_type not in (MAP_2D, MAP_CUBE):
        raise ImageError(
            f"PC image {name!r}: mapType {map_type} depth {depth}; expected a 2D (3) or cube "
            "(5) image of depth 1"
        )
    levels = ld[0] or tx.full_levels(width, height)
    faces = 6 if map_type == MAP_CUBE else 1
    out = bytearray()
    at = 0
    for _ in range(faces):
        face = bytearray()
        for lw, lh in tx.mip_dims(width, height, levels):
            n = tx.level_size(fmt, lw, lh)
            data = px[at : at + n]
            if len(data) != n:
                raise ImageError(
                    f"PC image {name!r}: level {lw}x{lh} needs {n} bytes at pixel offset {at}, "
                    f"the loadDef holds {len(px)}"
                )
            at += n
            face += _level(data, fmt, unit, lw, lh)
        face += bytes(-len(face) % tx.ALIGNMENT)
        out += face
    if at != len(px):
        raise ImageError(f"PC image {name!r}: {len(px)} pixel bytes, the levels use {at}")
    header = ps3_header(fmt, levels, width, height, len(out), map_type, semantic, category, hash_)
    return header, bytes(out)


def convert_image(pc_node: dict) -> dict:
    """A new PS3 GfxImage node from a PC one (see the module docstring)."""
    header, pixels = convert_pixels(pc_node)
    return image_node(pc_node.get("name"), header, pixels)


def new_image(
    name: str,
    fmt: int,
    faces_levels: list[list[bytes]],
    width: int,
    height: int,
    map_type: int = MAP_2D,
    semantic: int = 0,
    category: int = 3,
    hash_: int | None = None,
) -> dict:
    """A PS3 GfxImage node from stored levels (``texture.encode_level`` output, swizzled
    where the format is). ``category`` 3 and semantic 0 are what the stock compass images
    carry (code_post_gfx_mp ``compass_map_mp_nuked``: ``03 00 03 00`` at +0x18)."""
    pixels = tx.assemble(fmt, faces_levels)
    header = ps3_header(
        fmt,
        len(faces_levels[0]),
        width,
        height,
        len(pixels),
        map_type,
        semantic,
        category,
        name_hash(name) if hash_ is None else hash_,
    )
    return image_node(name, header, pixels)


def name_hash(name: str) -> int:
    """The GfxImage hash (+0x6c) of a name: h = 33 h ^ (c | 0x20) over the bytes. Matches
    PS3 mp_nuked ``faction_128_specops`` 0x8ba3e54a, ``faction_128_spetsnaz`` 0x0102d637 and
    ``*lightmap0_primary`` 0xce2f698b."""
    h = 0
    for c in name.encode("ascii"):
        h = ((33 * h) ^ (c | 0x20)) & 0xFFFFFFFF
    return h
