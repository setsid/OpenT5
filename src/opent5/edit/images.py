"""GfxImage pixels: what the editor shows, and new pixels for an image (docs/research/
textures.md, docs/research/pak.md, structs-content.md section 7).

Pixels live in one of three places: inline after the image's name (PHYSICAL), in the
deferred tail at the end of the zone (PHYSICAL_RUNTIME; same bytes, read after the last
asset), or in .pak files (streamed; the zone holds part records only). All three can be
replaced with pixels of the same width, height, format and mip count: re-encoded from RGBA
(mips rebuilt with a box filter) or taken as stored blocks from a DDS.

Streamed images are split into parts by mip range (pak.md 3): part 0 is the mip tail (in
mode 1 usually in the shared images_low.pak), each later part one larger level (usually in
the level's own pak). New bytes for each part are kept as pending pak edits keyed by
(slot, entry); the zone itself does not change. ``save_paks`` writes the edited paks next
to the saved zone and checks them. ``resize_streamed`` gives a streamed image another
power-of-two size (pak.md 9.1): the mip tail keeps its entry, the larger levels are laid
out again in the level pak (appending entries when needed) and the GfxImage header changes.
"""

from __future__ import annotations

import copy
import dataclasses
import struct
from pathlib import Path

import numpy as np

from opent5.container.pak import Pak, PakError
from opent5.container.pak import compare as compare_pak
from opent5.edit.types import EditError, ImageData
from opent5.export.images import (
    IDENTITY_REMAP,
    PAK_SLOTS,
    SEMANTIC_NORMAL,
    ImageError,
    PakSet,
    StreamPart,
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
        out["parts"] = [
            dict(dataclasses.asdict(p), pak=pak_file_name(zone_name, p.slot), shared=p.slot != 0)
            for p in stream_parts(f)
        ]
    return out


def why_not_replaceable(node: dict, zone_name: str) -> str | None:
    place = where(node)
    if place == "pak":
        reason = _why_not_streamed(node)
        if reason:
            return reason
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


def read(
    node: dict,
    zone_name: str,
    pak_dirs: list[Path],
    pending: dict[tuple[int, int], bytes | None] | None = None,
) -> ImageData:
    """The image as it is now: ``pending`` holds new bytes for pak entries not yet saved."""
    meta = info(node, zone_name)
    paks = EditedPaks(zone_name, pak_dirs, pending)
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
    size = view(node).fields["size"]
    stored = _encode_full(node, data, what)
    if len(stored) > size:
        raise EditError(
            f"{what}: the encoded pixels take {len(stored)} bytes, the image holds {size}"
        )
    return stored + bytes(size - len(stored))


def _encode_full(node: dict, data, what: str, dims: tuple[int, int, int] | None = None) -> bytes:
    """New pixels as one stored image: every face, every mip level, padded to 128. ``dims``
    (width, height, levels) replaces the image's own for a resize."""
    f = view(node).fields
    fmt, w, h = f["texture.format"], f["texture.width"], f["texture.height"]
    levels = max(1, f["texture.mipmap"])
    if dims is not None:
        w, h, levels = dims
    faces = 6 if f["texture.cubemap"] else 1
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
    return stored


def stored_value(node: dict, stored: bytes):
    """The value to put in node["pixels"] for new stored bytes (kept deferred if it was)."""
    pixels = node.get("pixels")
    if isinstance(pixels, DeferredData):
        return dataclasses.replace(pixels, data=memoryview(stored))
    return stored


# -- streamed images (.pak) ------------------------------------------------------------------

#: The level's own pak (``<zone>.pak``); every other slot names a pak several zones share.
LEVEL_SLOT = 0

SHARED_NOTE = (
    "{pak} is shared: every zone that streams from it reads it, so a changed copy changes "
    "this image wherever it is used (each entry belongs to one image)"
)


def pak_file_name(zone_name: str, slot: int) -> str | None:
    """The file a pak slot names: slot 0 is ``<zone>.pak`` (pak.md 4)."""
    return f"{zone_name}.pak" if slot == LEVEL_SLOT else PAK_SLOTS.get(slot)


def _why_not_streamed(node: dict) -> str | None:
    f = view(node).fields
    parts = stream_parts(f)
    if f["texture.cubemap"] or f["texture.dimension"] == 3:
        return "a streamed cube or volume image: its part layout is not established"
    for p in parts:
        if p.slot != LEVEL_SLOT and p.slot not in PAK_SLOTS:
            return f"part in pak slot {p.slot}, whose file is not known (pak.md 4)"
    model = _part_sizes(f, parts)
    if model is None:
        return "the part records do not follow the mip-range layout of pak.md 3"
    return None


def _part_sizes(f, parts: list[StreamPart]) -> list[tuple[int, int, int]] | None:
    """(first level, level count, bytes) of each part, checked against its record: part k
    holds the levels from its own width down to twice the previous part's, padded to 128."""
    fmt, levels = f["texture.format"], max(1, f["texture.mipmap"])
    out, prev_c, prev_m = [], 0, 0
    for p in parts:
        n = p.mips - prev_m
        if n < 1 or p.mips > levels:
            return None
        size = tx.face_size(fmt, p.width, p.height, n)
        if size != p.cumulative - prev_c:
            return None
        out.append((levels - p.mips, n, size))
        prev_c, prev_m = p.cumulative, p.mips
    if not parts or prev_m != levels or parts[-1].width != f["texture.width"]:
        return None
    return out


def encode_parts(node: dict, data, what: str) -> list[tuple[StreamPart, bytes]]:
    """New pixels for a streamed image, as (part, stored bytes) for every part record:
    the full chain is encoded once and split into each part's mip range."""
    f = view(node).fields
    reason = _why_not_streamed(node)
    if reason:
        raise EditError(f"{what}: cannot replace: {reason}")
    fmt, w, h = f["texture.format"], f["texture.width"], f["texture.height"]
    levels = max(1, f["texture.mipmap"])
    parts = stream_parts(f)
    stored = _encode_full(node, data, what)
    per_level = tx.split(stored, fmt, w, h, levels)[0]
    out = []
    for p, (first, n, size) in zip(parts, _part_sizes(f, parts) or [], strict=True):
        part = tx.assemble(fmt, [per_level[first : first + n]])
        if len(part) != size:
            raise EditError(
                f"{what}: part in slot {p.slot} entry {p.entry}: expected {size} bytes, "
                f"encoded {len(part)}"
            )
        out.append((p, part))
    return out


def input_size(data) -> tuple[int, int] | None:
    """(width, height) of new pixels: an RGBA array, six of them, or DDS bytes."""
    if isinstance(data, bytes | bytearray | memoryview):
        try:
            dds = tx.read_dds(bytes(data))
        except tx.TextureError:
            return None
        return dds.width, dds.height
    if isinstance(data, list | tuple) and data:
        data = data[0]
    shape = np.shape(data)
    return (shape[1], shape[0]) if len(shape) >= 2 else None


def _power_of_two(n: int) -> bool:
    return n > 0 and n & (n - 1) == 0


#: Largest side accepted for a new size (the RSX samples textures up to 4096 a side).
MAX_SIDE = 4096


def linker_layout(w: int, h: int, levels: int) -> list[tuple[int, int, int]] | None:
    """(width, height, mips) of each part, smallest first, as the linker lays out a mode-1
    image (pak.md 6.1): every level whose smaller side is at least 64 is its own part,
    the three largest at most; the rest is the tail, part 0. None for a single part."""
    dims = [(max(1, w >> k), max(1, h >> k)) for k in range(levels)]
    own = min(3, sum(1 for d in dims if min(d) >= 64))
    if own == 0 or own >= levels:
        return None
    return [(*dims[own], levels - own)] + [(*dims[k], levels - k) for k in reversed(range(own))]


def why_not_resizable(node: dict) -> str | None:
    """Why a streamed image cannot change size, or None."""
    reason = _why_not_streamed(node)
    if reason:
        return reason
    f = view(node).fields
    parts = stream_parts(f)
    w, h, levels = f["texture.width"], f["texture.height"], max(1, f["texture.mipmap"])
    if f["streamingMode"] != 1 or len(parts) < 2:
        return "a single-part image: resizing keeps the mip tail (part 0), which here is the image"
    if tx.base_format(f["texture.format"]) not in tx.BLOCK_BYTES:
        return f"format {tx.format_name(f['texture.format'])}: only DXT images change size"
    if not (_power_of_two(w) and _power_of_two(h)) or levels != tx.full_levels(w, h):
        return "the image is not a power-of-two size with a full mip chain"
    tail = parts[0]
    if tail.mips != tx.full_levels(tail.width, tail.height):
        return "its mip tail does not run down to 1x1"
    for p in parts[1:]:
        if p.slot != LEVEL_SLOT:
            return (
                f"a level above the mip tail ({p.width}x{p.height}) streams from "
                f"{pak_file_name('', p.slot)}, a shared pak; resizing changes its size"
            )
    return None


@dataclasses.dataclass
class Resize:
    """A streamed image at a new size: the new GfxImage header, and the stored bytes of
    each new part with its record (slot and entry included)."""

    header: bytes
    parts: list[tuple[StreamPart, bytes]]
    width: int
    height: int
    levels: int
    appended: list[int]  # level-pak entries added after the pak's last one
    unused: list[int]  # level-pak entries the image no longer reads
    linker_layout: bool  # the part split is the one the linker makes at this size


def resize_streamed(node: dict, data, what: str, next_entry: int) -> Resize:
    """New pixels for a streamed image at another power-of-two size (pak.md 9.1). The mip
    tail (part 0) keeps its size, place and record; every larger level becomes one part
    in the level pak, reusing the image's level-pak entries smallest first and adding
    entries from ``next_entry`` when it needs more."""
    reason = why_not_resizable(node)
    if reason:
        raise EditError(f"{what}: cannot change size: {reason}")
    f = view(node).fields
    fmt = f["texture.format"]
    old = stream_parts(f)
    size = input_size(data)
    if size is None:
        raise EditError(f"{what}: expected RGBA pixels or DDS bytes")
    w, h = size
    if not (_power_of_two(w) and _power_of_two(h)) or max(w, h) > MAX_SIDE:
        raise EditError(
            f"{what}: expected a power-of-two size up to {MAX_SIDE}x{MAX_SIDE}, found {w}x{h}"
        )
    levels = tx.full_levels(w, h)
    tail = old[0]
    above = levels - tail.mips
    if (max(1, w >> above), max(1, h >> above)) != (tail.width, tail.height) or above < 1:
        raise EditError(
            f"{what}: the mip tail ({tail.width}x{tail.height}, {tail.mips} levels, "
            f"{pak_file_name('', tail.slot) or 'slot ' + str(tail.slot)} entry {tail.entry}) is "
            f"kept as it is, so the new size must have it as a level: expected "
            f"{_sizes_keeping(tail, f['texture.width'], f['texture.height'])}, found {w}x{h}"
        )
    if above > 3:
        raise EditError(
            f"{what}: {w}x{h} has {above} levels above the {tail.width}x{tail.height} mip "
            f"tail; a GfxImage holds four part records, so at most 3. Expected "
            f"{_sizes_keeping(tail, f['texture.width'], f['texture.height'])}"
        )
    stored = _encode_full(node, data, what, (w, h, levels))
    per_level = tx.split(stored, fmt, w, h, levels)[0]
    entries = [p.entry for p in old[1:]]
    if tail.slot == LEVEL_SLOT:
        entries = [e for e in entries if e != tail.entry]
    appended: list[int] = []
    parts: list[tuple[StreamPart, bytes]] = []
    total, prev_mips = 0, 0
    for k in range(above, -1, -1):
        mips = levels - k
        n = mips - prev_mips
        pw, ph = max(1, w >> k), max(1, h >> k)
        first = levels - mips
        part = tx.assemble(fmt, [per_level[first : first + n]])
        if len(part) != tx.face_size(fmt, pw, ph, n):
            raise EditError(f"{what}: part {pw}x{ph}: encoded {len(part)} bytes, expected "
                            f"{tx.face_size(fmt, pw, ph, n)}")  # fmt: skip
        total += len(part)
        if k == above:
            slot, entry = tail.slot, tail.entry
        elif entries:
            slot, entry = LEVEL_SLOT, entries.pop(0)
        else:
            slot, entry = LEVEL_SLOT, next_entry + len(appended)
            appended.append(entry)
        parts.append((StreamPart(total, mips, pw, ph, slot, entry), part))
        prev_mips = mips
    header = bytearray(node["header"])
    header[0x1] = levels
    struct.pack_into(">HH", header, 0x8, w, h)
    struct.pack_into("<I", header, 0x28, total // (4 * len(parts)))
    struct.pack_into("<I", header, 0x30, total)
    words = b"".join(
        struct.pack(">IHHI", (p.cumulative // 16) << 8 | p.mips, p.width, p.height,
                    p.slot << 24 | p.entry)
        for p, _ in parts
    )  # fmt: skip
    header[0x34:0x64] = words.ljust(48, b"\0")
    header[0x64] = len(parts)
    return Resize(
        bytes(header),
        parts,
        w,
        h,
        levels,
        appended,
        entries,
        linker_layout(w, h, levels) == [(p.width, p.height, p.mips) for p, _ in parts],
    )


def _sizes_keeping(tail: StreamPart, w: int, h: int) -> str:
    """The sizes of the image's aspect that keep its tail, with 1 to 3 levels above it."""
    out = []
    for above in (1, 2, 3):
        nw, nh = tail.width << above, tail.height << above
        if max(nw, nh) <= MAX_SIDE and nw * h == nh * w:
            out.append(f"{nw}x{nh}")
    return " or ".join(out) or "none"


class EditedPaks(PakSet):
    """PakSet that answers pending (unsaved) entry edits before reading the files."""

    def __init__(
        self,
        zone_name: str,
        folders: list[Path],
        pending: dict[tuple[int, int], bytes | None] | None = None,
    ):
        super().__init__(zone_name, folders)
        self.pending = {k: v for k, v in (pending or {}).items() if v is not None}

    def pak(self, slot: int):
        """The pak, counting entries a pending edit appends after its last one."""
        pak = super().pak(slot)
        added = [e for (s, e) in self.pending if s == slot]
        if pak is not None and added and max(added) >= pak.count:
            pak = copy.copy(pak)
            pak.count = max(added) + 1
        return pak

    def read(self, slot: int, entry: int, size: int) -> bytes:
        data = self.pending.get((slot, entry))
        if data is not None:
            if len(data) < size:
                raise tx.TextureError(
                    f"pak slot {slot} entry {entry}: expected {size} bytes, the edit has "
                    f"{len(data)}"
                )
            return data[:size]
        return super().read(slot, entry, size)


def find_pak(zone_name: str, folders: list[Path], slot: int) -> Path | None:
    name = pak_file_name(zone_name, slot)
    if name is None:
        return None
    for folder in folders:
        path = Path(folder) / name
        if path.is_file():
            return path
    return None


def pak_targets(
    zone_name: str,
    folders: list[Path],
    pending: dict[tuple[int, int], bytes | None],
    out_dir: Path,
    out_stem: str,
) -> list[tuple[int, Path, Path, dict[int, bytes]]]:
    """(slot, source pak, output path, {entry: bytes}) for every pak with edits. The level
    pak is written as ``<out_stem>.pak`` (the game opens ``<zone>.pak`` beside the zone),
    a shared pak under its own name."""
    by_slot: dict[int, dict[int, bytes]] = {}
    for (slot, entry), data in sorted(pending.items()):
        if data is not None:
            by_slot.setdefault(slot, {})[entry] = data
    out = []
    for slot, entries in sorted(by_slot.items()):
        source = find_pak(zone_name, folders, slot)
        if source is None:
            raise EditError(
                f"pak slot {slot}: expected {pak_file_name(zone_name, slot)} in "
                f"{', '.join(str(f) for f in folders) or 'no folder'}, found none"
            )
        name = f"{out_stem}.pak" if slot == LEVEL_SLOT else source.name
        out.append((slot, source, Path(out_dir) / name, entries))
    return out


def save_paks(
    targets: list[tuple[int, Path, Path, dict[int, bytes]]], verify: bool = True
) -> tuple[list[dict], list[str]]:
    """Write each edited pak (source untouched) and, with verify, re-read it: header and
    every entry not edited byte-identical, every edited entry holding the new bytes."""
    infos, problems = [], []
    for slot, source, target, entries in targets:
        try:
            with Pak.open(source) as pak:
                for entry, data in sorted(entries.items()):
                    if entry < pak.count:
                        pak.replace(entry, data)
                    elif pak.append(data) != entry:
                        raise PakError(
                            f"{source.name}: expected appended entries from {pak.count} on, "
                            f"found entry {entry}"
                        )
                size, sha1 = pak.write(target)
                info = {
                    "slot": slot,
                    "source": str(source),
                    "path": str(target),
                    "bytes": size,
                    "sha1": sha1,
                    "entries": pak.new_count,
                    "edited_entries": sorted(entries),
                    "appended_entries": sorted(e for e in entries if e >= pak.count),
                    "shared": slot != LEVEL_SLOT,
                }
                if slot != LEVEL_SLOT:
                    info["note"] = SHARED_NOTE.format(pak=source.name)
                if verify:
                    with Pak.open(target) as written:
                        found = compare_pak(pak, written, entries)
                    info["entries_checked"] = pak.new_count
                    info["entries_identical"] = pak.new_count - len(entries)
                    info["verified"] = not found
                    problems += found
        except PakError as exc:
            problems.append(f"{target.name}: {exc}")
            continue
        infos.append(info)
    return infos, problems


def check_streamed(
    node: dict,
    zone_name: str,
    source_dirs: list[Path],
    pending: dict,
    out_dir: Path,
    out_stem: str,
) -> str | None:
    """None when the image decodes from the written paks (``<out_stem>.pak`` and any shared
    pak in ``out_dir``, the rest from the source folders) to exactly what the pending edits
    decode to; otherwise what differs."""
    want_paks = EditedPaks(zone_name, source_dirs, pending)
    got_paks = PakSet(out_stem, [Path(out_dir), *source_dirs])
    try:
        want = decode_image(node, want_paks)
        got = decode_image(node, got_paks)
    except ImageError as exc:
        return f"image {node.get('name')}: does not decode: {exc}"
    finally:
        want_paks.close()
        got_paks.close()
    if got.source != want.source or not np.array_equal(got.layers[0][1], want.layers[0][1]):
        return (
            f"image {node.get('name')}: decodes from {got.source} to pixels that differ from "
            "the new ones"
        )
    return None
