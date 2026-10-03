"""GfxImage -> PNG: zone pixels (inline or deferred) and streamed pixels from .pak files.

docs/research/textures.md has the evidence for every rule used here; the
decoders are ``opent5.formats.texture``. Level 0 (the largest level present) is
exported. Cube maps become six faces (+X -X +Y -Y +Z -Z, order INFERRED in
textures.md 4); volumes become one image with the depth slices stacked
vertically. Normal maps (semantic 5, X in alpha and Y in green) are written as
ordinary RGB tangent-space normal maps with Z rebuilt.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from opent5.export.nodes import pixel_bytes
from opent5.formats import texture as tx

#: Pak slot -> file name; slot 0 is the zone's own pak. textures.md 2.4 (slot 2 unknown).
PAK_SLOTS = {1: "images_low.pak", 3: "common.pak", 4: "ui_mp.pak", 9: "img_patch.pak"}
SEMANTIC_NORMAL = 5
IDENTITY_REMAP = 0xAAE4


@dataclass
class StreamPart:
    cumulative: int  # bytes up to and including this part
    mips: int
    width: int
    height: int
    slot: int
    entry: int


def stream_parts(header: bytes) -> list[StreamPart]:
    """The part records of a streamed image (GfxImage +0x34, count at +0x64)."""
    if len(header) < 0x68 or not header[0x27]:
        return []
    count = header[0x64]
    parts = []
    for k in range(min(count, 4)):
        word, w, h, loc = struct.unpack_from(">IHHI", header, 0x34 + 12 * k)
        parts.append(StreamPart((word >> 8) * 16, word & 0xFF, w, h, loc >> 24, loc & 0xFFFFFF))
    return parts


class PakSet:
    """Opens .pak files by slot, searching the given folders in order (read-only)."""

    def __init__(self, zone_name: str, folders: list[Path]):
        self.zone_name = zone_name
        self.folders = [Path(f) for f in folders if f]
        self._open: dict[int, tx.Pak | None] = {}
        self._files: dict[int, object] = {}

    def file_name(self, slot: int) -> str | None:
        if slot == 0:
            return f"{self.zone_name}.pak"
        return PAK_SLOTS.get(slot)

    def pak(self, slot: int) -> tx.Pak | None:
        if slot not in self._open:
            name = self.file_name(slot)
            found = None
            if name:
                for folder in self.folders:
                    path = folder / name
                    if path.is_file():
                        found = tx.Pak(path)
                        break
            self._open[slot] = found
        return self._open[slot]

    def read(self, slot: int, entry: int, size: int) -> bytes:
        """One entry's bytes, through a handle kept open per pak (reads over a network or
        9p mount are much faster without a reopen per image)."""
        pak = self.pak(slot)
        if pak is None:
            raise tx.TextureError(f"pak slot {slot}: no file")
        f = self._files.get(slot)
        if f is None:
            f = self._files[slot] = open(pak.path, "rb")  # noqa: SIM115 (closed in close())
        at = pak.offset(entry)
        f.seek(at)
        data = f.read(size)
        if len(data) != size:
            raise tx.TextureError(
                f"pak slot {slot} entry {entry}: expected {size} bytes at {at:#x}, "
                f"found {len(data)}"
            )
        return data

    def close(self) -> None:
        for f in self._files.values():
            f.close()
        self._files.clear()


@dataclass
class DecodedImage:
    """What an image decodes to: one or more named RGBA arrays plus notes."""

    layers: list[tuple[str, np.ndarray]] = field(default_factory=list)  # (suffix, rgba)
    source: str = ""
    width: int = 0
    height: int = 0
    notes: list[str] = field(default_factory=list)


class ImageError(Exception):
    pass


def _decode_x32_float(data: bytes, w: int, h: int) -> np.ndarray:
    tex = np.frombuffer(data, ">f4", w * h).reshape(-1, 1)
    vals = tx.unswizzle(tex, w, h)[..., 0]
    g = np.clip(np.nan_to_num(vals) * 255.0, 0, 255).astype(np.uint8)
    return np.dstack([g, g, g, np.full_like(g, 255)])


def _decode_level0(
    data: bytes, fmt: int, w: int, h: int, levels: int, face: int, faces: int, remap: int
) -> np.ndarray:
    if tx.base_format(fmt) == tx.X32_FLOAT:
        off = tx.level_offsets(fmt, w, h, levels, faces)[face][0] if faces > 1 else 0
        return _decode_x32_float(data[off:], w, h)
    rgba = tx.decode(data, fmt, w, h, max(1, levels), face=face, faces=faces)
    if (remap & 0xFFFF) != IDENTITY_REMAP and tx.base_format(fmt) not in tx.BLOCK_BYTES:
        rgba = tx.apply_remap(rgba, remap)
    return rgba


def rebuild_normal(rgba: np.ndarray) -> np.ndarray:
    """X in alpha, Y in green -> RGB normal map (Z = sqrt(1 - x^2 - y^2)), opaque."""
    x = rgba[..., 3].astype(np.float32) / 127.5 - 1.0
    y = rgba[..., 1].astype(np.float32) / 127.5 - 1.0
    z = np.sqrt(np.clip(1.0 - x * x - y * y, 0.0, 1.0))
    out = np.empty_like(rgba)
    out[..., 0] = np.clip((x + 1) * 127.5, 0, 255)
    out[..., 1] = np.clip((y + 1) * 127.5, 0, 255)
    out[..., 2] = np.clip((z + 1) * 127.5, 0, 255)
    out[..., 3] = 255
    return out


def decode_image(node: dict, paks: PakSet | None) -> DecodedImage:
    """Decode one GfxImage node. Raises ImageError naming why it cannot be decoded."""
    header = bytes(node["header"])
    gcm = tx.GcmTexture.parse(header)
    fmt, w, h, depth, levels, remap = (
        gcm.format,
        gcm.width,
        gcm.height,
        gcm.depth,
        gcm.mipmap,
        gcm.remap,
    )
    cube = bool(gcm.cubemap)
    dimension, semantic, category, delayed = header[2], header[0x19], header[0x1A], header[0x1B]
    out = DecodedImage(width=w, height=h)
    data = pixel_bytes(node["pixels"])
    if data is not None:
        out.source = "deferred" if delayed else "inline"
    else:
        parts = stream_parts(header)
        if not parts and (node.get("name") or "").startswith(","):
            raise ImageError(
                "a reference to an image defined in another zone (the name starts with ','; "
                "the struct here carries no pixels)"
            )
        if not parts:
            raise ImageError(
                f"no pixels: not in the zone (pointer +0x2c = 0) and not streamed "
                f"(+0x27 = 0); format 0x{fmt:02x}, category {category}"
            )
        if paks is None:
            raise ImageError("streamed from .pak files, and no pak folder was given")
        if cube or dimension == 3:
            raise ImageError("streamed cube or volume image: part layout not established")
        prev, chosen, missing = 0, None, []
        sized = []
        for p in parts:
            sized.append((p, prev, p.cumulative - prev))
            prev = p.cumulative
        for p, _start, size in reversed(sized):  # largest part first
            pak = paks.pak(p.slot)
            if pak is None:
                missing.append(f"slot {p.slot} ({paks.file_name(p.slot) or 'unknown file'})")
                continue
            if p.entry >= pak.count:
                missing.append(f"slot {p.slot} entry {p.entry} (pak has {pak.count})")
                continue
            data = paks.read(p.slot, p.entry, size)
            chosen = p
            break
        if chosen is None:
            raise ImageError("streamed; no part readable: " + ", ".join(missing))
        w, h, levels = chosen.width, chosen.height, chosen.mips
        out.width, out.height = w, h
        out.source = f"pak slot {chosen.slot} entry {chosen.entry}"
        if missing:
            out.notes.append(
                f"largest part(s) unavailable ({', '.join(missing)}); exported {w}x{h}"
            )
    base = tx.base_format(fmt)
    if base not in tx.FORMAT_NAMES:
        raise ImageError(f"format 0x{fmt:02x} is not a known libgcm texture format")
    try:
        if dimension == 3 and depth > 1:
            vol = tx.decode_volume(data, fmt, w, h, depth)
            out.layers.append(("", vol.reshape(depth * h, w, 4)))
            out.notes.append(f"volume {w}x{h}x{depth}: slices stacked vertically")
        elif cube:
            for face, suffix in enumerate(("_px", "_nx", "_py", "_ny", "_pz", "_nz")):
                out.layers.append((suffix, _decode_level0(data, fmt, w, h, levels, face, 6, remap)))
        else:
            rgba = _decode_level0(data, fmt, w, h, levels, 0, 1, remap)
            if semantic == SEMANTIC_NORMAL and base in (tx.DXT45, tx.DXT23):
                rgba = rebuild_normal(rgba)
                out.notes.append("normal map: X from alpha, Y from green, Z rebuilt")
            out.layers.append(("", rgba))
    except (tx.TextureError, ValueError, IndexError) as exc:
        raise ImageError(f"{tx.format_name(fmt)} {w}x{h}: {exc}") from exc
    return out
