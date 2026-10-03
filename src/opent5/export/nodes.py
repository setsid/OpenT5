"""Finding every asset in a parsed zone, and resolving the pointers the parse keeps raw.

A zone lists only its top-level assets; most images, materials, techsets,
shaders and models are loaded inline inside other assets (a material inside a
GfxWorld, an image inside a material). ``AssetIndex`` walks the parsed data and
collects every asset node of the types the exporter writes, by name.

Two kinds of reference stay unresolved after a parse:

- Asset references given as alias pointers (``AssetLink``). The pointer names
  a memory location that holds a pointer to the asset: either an alias slot
  reserved by a ``-2`` load, or the pointer field of the struct that loaded the
  asset inline (a GfxWorld surface's material points at the ``materialMemory``
  entry that loaded it). ``AliasResolver`` rebuilds that map from the event log.
- Offset pointers to data loaded earlier (an XSurface that shares another
  surface's vertices, a brush side's plane). ``MemoryMap`` maps a block
  position back to the file bytes that were loaded there.
"""

from __future__ import annotations

import dataclasses
import struct
from bisect import bisect_right
from collections.abc import Iterator
from typing import Any

import numpy as np

from opent5.xfile.constants import (
    OFFSET_BLOCK_SHIFT,
    OFFSET_MASK,
    PTR_INLINE,
    AssetType,
    Block,
    type_name,
)
from opent5.xfile.events import NONE, EventKind, PtrKind
from opent5.xfile.stream import AssetLink, DeferredData

#: Header size of each inline asset -> offset of its name pointer (default 0).
NAME_OFFSET_BY_HEADER = {0x70: 0x68, 8: 4}


def _header_size(node: dict) -> int | None:
    h = node.get("header")
    return len(h) if isinstance(h, bytes | bytearray | memoryview) else None


def classify(node: Any) -> int | None:
    """The asset type of a handler's output node, from its keys and header size;
    None for anything that is not an asset node."""
    if not isinstance(node, dict):
        return None
    keys = node.keys()
    n = _header_size(node)
    if n is None:
        return None
    if n == 0x70 and "pixels" in keys:
        return AssetType.IMAGE
    if n == 0x80 and "technique_set" in keys:
        return AssetType.MATERIAL
    if n == 0xF8 and "surfs" in keys:
        return AssetType.XMODEL
    if n == 292 and "techniques" in keys:
        return AssetType.TECHSET
    if "program" in keys and n in (16, 12):
        return AssetType.VERTEXSHADER if n == 16 else AssetType.PIXELSHADER
    if n == 0x10 and "image" in keys:
        return AssetType.LIGHTDEF
    if n == 0x54 and "snd_alias_prefix" in keys:
        return AssetType.PHYSPRESET
    if n == 60 and "elem_defs" in keys:
        return AssetType.FX
    if n == 0xA88:
        return AssetType.PHYSCONSTRAINTS
    return None


def children(obj: Any) -> Iterator[Any]:
    if isinstance(obj, dict):
        yield from obj.values()
    elif isinstance(obj, list | tuple):
        yield from obj
    elif isinstance(obj, AssetLink):
        if obj.target is not None:
            yield obj.target
    elif dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        for f in dataclasses.fields(obj):
            yield getattr(obj, f.name)


def walk(root: Any) -> Iterator[Any]:
    """Every node under root, depth first, each container once."""
    seen: set[int] = set()
    stack = [root]
    while stack:
        obj = stack.pop()
        if isinstance(obj, bytes | bytearray | memoryview | str | int | float) or obj is None:
            continue
        if id(obj) in seen:
            continue
        seen.add(id(obj))
        yield obj
        kids = list(children(obj))
        kids.reverse()
        stack.extend(kids)


class AssetIndex:
    """Every asset node in a parsed zone, top level and inline, by type and name.

    ``top`` keeps the zone's own asset list order; ``by_type[t]`` maps a name to
    its first node (a name loaded twice is the same asset)."""

    def __init__(self, xfile):
        self.xfile = xfile
        self.by_type: dict[int, dict[str, Any]] = {}
        self.unnamed: dict[int, int] = {}
        for asset in xfile.assets:
            if asset.data is None or isinstance(asset.data, AssetLink):
                continue
            self._add(asset.type, asset.data)
            for node in walk(asset.data):
                t = classify(node)
                if t is not None and node is not asset.data:
                    self._add(t, node)

    def _add(self, asset_type: int, node: Any) -> None:
        name = node.get("name") if isinstance(node, dict) else getattr(node, "name", None)
        if name is None:
            self.unnamed[asset_type] = self.unnamed.get(asset_type, 0) + 1
            return
        self.by_type.setdefault(asset_type, {}).setdefault(name, node)

    def of(self, asset_type: int) -> dict[str, Any]:
        return self.by_type.get(asset_type, {})

    def get(self, asset_type: int, name: str | None) -> Any:
        if name is None:
            return None
        return self.by_type.get(asset_type, {}).get(name)

    def counts(self) -> dict[str, int]:
        return {type_name(t): len(v) for t, v in sorted(self.by_type.items())}


def _mem_key(block: int, offset: int) -> int:
    return (block << OFFSET_BLOCK_SHIFT) | offset


class MemoryMap:
    """Block memory -> file bytes, from the READ, STRING and TAIL events of a parse.

    TEMP is left out: it rewinds after every asset, so its positions repeat."""

    def __init__(self, xfile, content: bytes):
        if xfile.log is None:
            raise ValueError("MemoryMap needs a parse with log=True")
        self.content = content
        t = np.asarray(xfile.log.table()).astype(np.int64)
        kinds = t[:, 0]
        loaded = ((kinds == EventKind.READ) | (kinds == EventKind.STRING)) & (t[:, 1] != NONE)
        rows = t[loaded][:, [3, 4, 2, 1]]  # block, mem, size, file
        tail = t[kinds == EventKind.TAIL][:, [3, 4, 2, 1]]
        rows = np.concatenate([rows, tail])
        rows = rows[(rows[:, 0] != Block.TEMP) & (rows[:, 2] > 0)]
        keys = (rows[:, 0] << OFFSET_BLOCK_SHIFT) | rows[:, 1]
        order = np.argsort(keys, kind="stable")
        self.keys = keys[order].tolist()
        self.rows = rows[order]
        self.table = t

    def file_offset(self, block: int, offset: int) -> int | None:
        key = _mem_key(block, offset)
        i = bisect_right(self.keys, key) - 1
        if i < 0:
            return None
        blk, mem, size, file = (int(v) for v in self.rows[i])
        if blk != block or offset >= mem + size:
            return None
        return file + offset - mem

    def pointer_offset(self, raw: int) -> int | None:
        """File offset of the data an offset pointer ((block << 29 | offset) + 1) names."""
        value = (raw - 1) & 0xFFFFFFFF
        return self.file_offset(value >> OFFSET_BLOCK_SHIFT, value & OFFSET_MASK)

    def read(self, raw: int, size: int) -> bytes | None:
        at = self.pointer_offset(raw)
        if at is None or at + size > len(self.content):
            return None
        return self.content[at : at + size]


class AliasResolver:
    """Alias pointers -> (header size, asset name), rebuilt from the event log.

    For every asset loaded inline through an asset reference (-1 or -2) the log
    holds: the POINTER event of the reference field, PUSH TEMP, (for -2) the
    INSERT of its alias slot, and the READ of its header in TEMP. The name is the
    first STRING after the header when the header's name field is -1, else an
    offset pointer to a string loaded earlier. Both the field's own memory and
    the alias slot then hold a pointer to that asset. Alias reference fields
    (DB_ConvertOffsetToAlias) are recorded too, since a later alias may name
    them in turn.
    """

    def __init__(self, memory: MemoryMap):
        self.memory = memory
        content = memory.content
        t = memory.table
        n = len(t)
        kinds = t[:, 0]
        strings: dict[int, str] = {}
        for row in t[(kinds == EventKind.STRING) & (t[:, 1] != NONE)]:
            if row[3] != Block.TEMP:
                text = content[row[1] : row[1] + row[2] - 1].decode("latin-1")
                strings[_mem_key(int(row[3]), int(row[4]))] = text
        self.slots: dict[int, tuple[int, str | None]] = {}
        self.aliases: dict[int, int] = {}
        string_rows = np.nonzero(kinds == EventKind.STRING)[0]
        for i in np.nonzero(kinds == EventKind.POINTER)[0]:
            field_at, _raw, kind = int(t[i, 1]), int(t[i, 2]), int(t[i, 3])
            if kind == PtrKind.ALIAS_REF:
                key = self._field_key(field_at)
                if key is not None:
                    self.aliases[key] = _mem_key(int(t[i, 4]), int(t[i, 5]))
                continue
            if kind not in (PtrKind.INLINE, PtrKind.INLINE_ALIAS):
                continue
            if i + 1 >= n or t[i + 1, 0] != EventKind.PUSH or t[i + 1, 1] != Block.TEMP:
                continue  # sub-data, not an asset reference
            j, slot = i + 1, None
            while j < n and not (t[j, 0] == EventKind.READ and t[j, 3] == Block.TEMP):
                if t[j, 0] == EventKind.INSERT:
                    slot = _mem_key(Block.VIRTUAL, int(t[j, 1]))
                j += 1
            if j >= n:
                continue
            header_at, header_size = int(t[j, 1]), int(t[j, 2])
            name_off = NAME_OFFSET_BY_HEADER.get(header_size, 0)
            name = None
            if name_off + 4 <= header_size:
                name_raw = struct.unpack_from(">I", content, header_at + name_off)[0]
                if name_raw == PTR_INLINE:
                    k = int(np.searchsorted(string_rows, j))
                    if k < len(string_rows):
                        s = t[string_rows[k]]
                        name = content[s[1] : s[1] + s[2] - 1].decode("latin-1")
                elif name_raw:
                    name = strings.get((name_raw - 1) & 0xFFFFFFFF)
            value = (header_size, name)
            key = self._field_key(field_at)
            if key is not None:
                self.slots[key] = value
            if slot is not None:
                self.slots[slot] = value

    def _field_key(self, field_at: int) -> int | None:
        if field_at == NONE:
            return None
        return self._by_file(field_at)

    _file_rows: np.ndarray | None = None

    def _by_file(self, at: int) -> int | None:
        if self._file_rows is None:
            rows = self.memory.rows
            order = np.argsort(rows[:, 3], kind="stable")
            self._file_rows = rows[order]
            self._file_starts = self._file_rows[:, 3].tolist()
        i = bisect_right(self._file_starts, at) - 1
        if i < 0:
            return None
        blk, mem, size, file = (int(v) for v in self._file_rows[i])
        if at >= file + size:
            return None
        return _mem_key(blk, mem + at - file)

    def name_of(self, link: AssetLink) -> str | None:
        key = _mem_key(link.block, link.offset)
        for _ in range(16):
            if key in self.slots:
                return self.slots[key][1]
            if key not in self.aliases:
                return None
            key = self.aliases[key]
        return None


class Resolver:
    """Names and nodes behind asset references, inline or by alias."""

    def __init__(self, index: AssetIndex, aliases: AliasResolver | None):
        self.index = index
        self.aliases = aliases

    def name(self, ref: Any) -> str | None:
        if ref is None:
            return None
        if isinstance(ref, AssetLink):
            if ref.target is not None:
                return ref.name
            return self.aliases.name_of(ref) if self.aliases else None
        if isinstance(ref, dict):
            return ref.get("name")
        return getattr(ref, "name", None)

    def node(self, ref: Any, asset_type: int | None = None) -> Any:
        if ref is None:
            return None
        if isinstance(ref, AssetLink):
            if ref.target is not None:
                return ref.target
            return self.index.get(asset_type or ref.asset_type, self.name(ref))
        return ref


def pixel_bytes(pixels: Any) -> bytes | None:
    """An image's zone-held pixels: inline bytes or the deferred tail slice."""
    if pixels is None:
        return None
    if isinstance(pixels, DeferredData):
        return None if pixels.data is None else bytes(pixels.data)
    return bytes(pixels)
