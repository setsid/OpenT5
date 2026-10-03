"""GfxImage pixels: what the editor shows, and new pixels for an image whose pixels are in
the zone (docs/research/textures.md, structs-content.md section 7).

Pixels live in one of three places: inline after the image's name (PHYSICAL), in the
deferred tail at the end of the zone (PHYSICAL_RUNTIME; same bytes, read after the last
asset), or in a .pak beside the .ff (streamed; the zone holds part records only). The first
two can be replaced here with pixels of the same width, height, format and mip count:
re-encoded from RGBA (mips rebuilt with a box filter) or taken as stored blocks from a DDS.
Streamed images are reported as not replaceable: writing a .pak is not implemented.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import numpy as np

from opent5.edit.types import EditError, ImageData
from opent5.export.images import (
    IDENTITY_REMAP,
    SEMANTIC_NORMAL,
    ImageError,
    PakSet,
    decode_image,
    stream_parts,
)
from opent5.export.nodes import pixel_bytes
from opent5.formats import texture as tx
from opent5.xfile.schema import view
from opent5.xfile.stream import DeferredData

#: Formats new pixels can be encoded to.
ENCODABLE = {tx.DXT1, tx.DXT23, tx.DXT45, tx.A8R8G8B8, tx.D8R8G8B8, tx.B8, tx.G8B8}


def where(node: dict) -> str:
    pixels = node.get("pixels")
    if isinstance(pixels, DeferredData):
        return "deferred"
    if pixels is not None:
        return "inline"
    f = view(node).fields
    if stream_parts(f):
        return "pak"
    return "elsewhere"


def info(node: dict, zone_name: str) -> dict:
    f = view(node).fields
    fmt = f["texture.format"]
    cube, dimension = bool(f["texture.cubemap"]), f["texture.dimension"]
    kind = "cube" if cube else "volume" if dimension == 3 else "2d"
    place = where(node)
    out = {
        "name": node.get("name"),
        "format": tx.format_name(fmt),
        "format_code": fmt,
        "width": f["texture.width"],
        "height": f["texture.height"],
        "depth": f["texture.depth"],
        "mips": f["texture.mipmap"],
        "kind": kind,
        "pixels": place,
        "size": f["size"],
        "semantic": f["semantic"],
        "remap": f["texture.remap"],
    }
    reason = why_not_replaceable(node, zone_name)
    out["replaceable"] = reason is None
    if reason:
        out["not_replaceable"] = reason
    if place == "pak":
        out["parts"] = [dataclasses.asdict(p) for p in stream_parts(f)]
    return out


def why_not_replaceable(node: dict, zone_name: str) -> str | None:
    place = where(node)
    if place == "pak":
        return (
            f"the pixels stream from {zone_name}.pak (or a shared .pak); writing .pak files "
            "is not implemented, so only images whose pixels are in the zone can be replaced"
        )
    if place == "elsewhere":
        return "the zone holds no pixels for this image (defined in another zone)"
    f = view(node).fields
    fmt = tx.base_format(f["texture.format"])
    if fmt not in ENCODABLE:
        return f"format {tx.format_name(f['texture.format'])} cannot be encoded yet"
    if f["texture.dimension"] == 3:
        return "volume textures cannot be replaced yet"
    if fmt not in tx.BLOCK_BYTES and (f["texture.remap"] & 0xFFFF) != IDENTITY_REMAP:
        return f"channel remap {f['texture.remap']:#06x} is not the identity"
    if tx.is_linear(f["texture.format"]) and f["texture.pitch"] not in (
        0,
        f["texture.width"] * tx.TEXEL_BYTES.get(fmt, 0),
    ):
        return "a linear texture with row padding cannot be replaced yet"
    return None


def read(node: dict, zone_name: str, pak_dirs: list[Path]) -> ImageData:
    meta = info(node, zone_name)
    paks = PakSet(zone_name, pak_dirs)
    try:
        decoded = decode_image(node, paks)
    except ImageError as exc:
        return ImageData(meta, None, str(exc))
    finally:
        paks.close()
    meta["source"] = decoded.source
    if decoded.notes:
        meta["notes"] = decoded.notes
    if not decoded.layers:
        return ImageData(meta, None, "no pixels decoded")
    return ImageData(meta, decoded.layers[0][1], None)


def stored_pixels(node: dict) -> bytes | None:
    return pixel_bytes(node.get("pixels"))


def _to_rgba(data, w: int, h: int, what: str) -> np.ndarray:
    a = np.asarray(data)
    if a.dtype != np.uint8:
        raise EditError(f"{what}: expected uint8 pixels, found {a.dtype}")
    if a.ndim == 3 and a.shape[2] == 3:
        a = np.concatenate([a, np.full(a.shape[:2] + (1,), 255, np.uint8)], 2)
    if a.shape != (h, w, 4):
        raise EditError(f"{what}: expected pixels of shape ({h}, {w}, 4), found {a.shape}")
    return np.ascontiguousarray(a)


def encode(node: dict, data, what: str) -> bytes:
    """The stored bytes for new pixels: RGBA (an array, or a list of six for a cube), or
    DDS file bytes. Same width, height and format as the image; size checked."""
    f = view(node).fields
    fmt, w, h = f["texture.format"], f["texture.width"], f["texture.height"]
    levels = max(1, f["texture.mipmap"])
    faces = 6 if f["texture.cubemap"] else 1
    size = f["size"]
    if isinstance(data, bytes | bytearray | memoryview):
        try:
            dds = tx.read_dds(bytes(data))
        except tx.TextureError as exc:
            raise EditError(f"{what}: {exc}") from None
        if tx.base_format(dds.fmt) != tx.base_format(fmt):
            raise EditError(
                f"{what}: expected a {tx.format_name(fmt)} DDS, found {tx.format_name(dds.fmt)}"
            )
        if (dds.width, dds.height) != (w, h):
            raise EditError(f"{what}: expected {w}x{h}, found {dds.width}x{dds.height} in the DDS")
        if len(dds.faces) != faces:
            raise EditError(f"{what}: expected {faces} face(s), found {len(dds.faces)}")
        if dds.levels < levels:
            raise EditError(f"{what}: expected at least {levels} mip levels, found {dds.levels}")
        if tx.base_format(fmt) in tx.BLOCK_BYTES:
            stored = tx.assemble(fmt, [row[:levels] for row in dds.faces])
        else:
            # read_dds gives levels in the GCM layout of the format it read; re-encode
            # from RGBA so swizzle and channel order follow this image's format.
            images = [tx.decode_plain(row[0], dds.fmt, w, h) if row else None for row in dds.faces]
            stored = tx.encode(images if faces > 1 else images[0], fmt, levels)
    else:
        if faces > 1:
            if not isinstance(data, list | tuple) or len(data) != 6:
                raise EditError(f"{what}: a cube map needs six faces (+X -X +Y -Y +Z -Z)")
            rgba = [_to_rgba(d, w, h, what) for d in data]
        else:
            rgba = _to_rgba(data, w, h, what)
            if f["semantic"] == SEMANTIC_NORMAL and tx.base_format(fmt) in (tx.DXT45, tx.DXT23):
                # The game keeps X in alpha and Y in green (the exporter rebuilds Z).
                stored_rgba = np.empty_like(rgba)
                stored_rgba[..., 0] = 255
                stored_rgba[..., 1] = rgba[..., 1]
                stored_rgba[..., 2] = 255
                stored_rgba[..., 3] = rgba[..., 0]
                rgba = stored_rgba
        try:
            stored = tx.encode(rgba, fmt, levels)
        except tx.TextureError as exc:
            raise EditError(f"{what}: {exc}") from None
    if len(stored) > size:
        raise EditError(
            f"{what}: the encoded pixels take {len(stored)} bytes, the image holds {size}"
        )
    return stored + bytes(size - len(stored))


def stored_value(node: dict, stored: bytes):
    """The value to put in node["pixels"] for new stored bytes (kept deferred if it was)."""
    pixels = node.get("pixels")
    if isinstance(pixels, DeferredData):
        return dataclasses.replace(pixels, data=memoryview(stored))
    return stored
