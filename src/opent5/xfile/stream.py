"""The stream cursor: the loader's primitives, replayed over the zone bytes.

The game loads a zone by walking the decompressed XFile stream with a handful
of primitives (docs/research/xfile.md section 3). ``XStream`` implements the
same primitives with the same effects on the file position and on the seven
block positions, so a handler written as a sequence of these calls consumes
exactly what the game consumes:

=====================  ===========  ==============================================
loader primitive       VA           here
=====================  ===========  ==============================================
DB_PushStreamPos       0x26ab78     ``push(block)``
DB_PopStreamPos        0x26ac08     ``pop()`` (rewinds TEMP when leaving it)
DB_AllocStreamPos      0x26aca0     ``alloc(mask)``: aligns block memory only
DB_InsertPointer       0x26acd8     ``insert()``: a 4-byte alias slot in VIRTUAL
Load_Stream            0x26aeb8     ``load(size)``
Load_XString           0x26ae08     ``xstring()``
flush deferred reads   0x26ae38     ``flush_deferred()``
=====================  ===========  ==============================================

The file is packed: alignment never skips file bytes. RUNTIME reads have no
file bytes at all (the loader zero-fills them). LARGE_RUNTIME and
PHYSICAL_RUNTIME reads are queued and their bytes come after the last asset,
in queue order.

Pointer fields are read through ``follows``, ``string`` and ``ref``, which
apply the loader's test for that field (``== -1`` for shareable data, ``!= 0``
for PS3 owned data, -1/-2/alias for asset references) and log the field.

One description, two directions. Handlers are written once against these
primitives, with every loaded thing given a home in a node (a dict):
``load(size, node, key)``, ``items(size, count, node, key)``,
``string(chunk, off, node, key)``, ``reserve(size, node, key)``. ``XStream``
(reading) takes the bytes from the file and stores them in the node;
``XWriter`` (writing, below) takes them from the node and emits them, with the
same block, alignment, RUNTIME and deferred bookkeeping and the same event log.
Because every decision a handler makes (counts, pointer tests, union tags) is
computed from the bytes it has just loaded or emitted, the two directions cannot
drift: writing a parsed node reproduces the bytes it was parsed from.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Any

from opent5.xfile.constants import (
    BLOCK_COUNT,
    OFFSET_BLOCK_SHIFT,
    OFFSET_MASK,
    PTR_INLINE,
    PTR_INSERT,
    PTR_NULL,
    Block,
)
from opent5.xfile.events import NONE, EventKind, EventLog, PtrKind
from opent5.xfile.refs import Refs

_U16 = struct.Struct(">H").unpack_from
_S16 = struct.Struct(">h").unpack_from
_U32 = struct.Struct(">I").unpack_from
_S32 = struct.Struct(">i").unpack_from
_F32 = struct.Struct(">f").unpack_from

READ = EventKind.READ
STRING = EventKind.STRING
DEFER = EventKind.DEFER
TAIL = EventKind.TAIL
PUSH = EventKind.PUSH
POP = EventKind.POP
ALLOC = EventKind.ALLOC
POINTER = EventKind.POINTER
INSERT = EventKind.INSERT

TEMP = Block.TEMP
RUNTIME = Block.RUNTIME
VIRTUAL = Block.VIRTUAL
_FILE_BLOCKS = (True, False, False, False, True, True, True)
_DEFERRED_BLOCKS = (False, False, True, True, False, False, False)


class XFileError(Exception):
    """A stream that does not parse; the message names the offset and the values."""


class Chunk:
    """Bytes the loader read with one Load_Stream (or a slice of them), with
    where they came from in the file and where they went in block memory."""

    __slots__ = ("data", "at", "block", "mem")

    def __init__(self, data: memoryview, at: int, block: int, mem: int):
        self.data = data
        self.at = at
        self.block = block
        self.mem = mem

    def __len__(self) -> int:
        return len(self.data)

    def __repr__(self) -> str:
        return f"Chunk({len(self.data)} bytes at {self.at:#x}, block {self.block} +{self.mem:#x})"

    def u8(self, off: int) -> int:
        return self.data[off]

    def s8(self, off: int) -> int:
        v = self.data[off]
        return v - 256 if v > 127 else v

    def u16(self, off: int) -> int:
        return _U16(self.data, off)[0]

    def s16(self, off: int) -> int:
        return _S16(self.data, off)[0]

    def u32(self, off: int) -> int:
        return _U32(self.data, off)[0]

    def s32(self, off: int) -> int:
        return _S32(self.data, off)[0]

    def f32(self, off: int) -> float:
        return _F32(self.data, off)[0]

    def sub(self, off: int, size: int) -> Chunk:
        at = self.at + off if self.at != NONE else NONE
        return Chunk(self.data[off : off + size], at, self.block, self.mem + off)

    def items(self, size: int, count: int):
        """The chunk as `count` consecutive elements of `size` bytes."""
        return [self.sub(size * i, size) for i in range(count)]

    def bytes(self) -> bytes:
        return bytes(self.data)


@dataclass
class RuntimeData:
    """A Load_Stream into RUNTIME: block memory reserved, no file bytes."""

    block: int
    mem: int
    size: int


@dataclass
class DeferredData:
    """A Load_Stream into LARGE_RUNTIME / PHYSICAL_RUNTIME. Block memory is
    reserved where the owner is loaded; the bytes come from the deferred tail
    after the last asset (``file_offset`` and ``data`` are set by the flush)."""

    index: int
    block: int
    mem: int
    size: int
    asset: int
    file_offset: int | None = None
    data: memoryview | None = field(default=None, repr=False)


@dataclass
class AssetLink:
    """An asset reference given as an offset pointer to an alias slot."""

    asset_type: int
    raw: int
    block: int
    offset: int
    #: The asset the alias names (resolved through alias slots, the pointer fields
    #: that loaded assets inline, and chains of alias fields), when known.
    target: Any = field(default=None, repr=False)
    #: Index of the top-level asset whose load put the target there (-1: unknown).
    asset_index: int = -1

    @property
    def name(self) -> str | None:
        target = self.target
        if isinstance(target, dict):
            return target.get("name")
        return getattr(target, "name", None)


class XStream:
    """The loader's view of the stream: file position, seven block positions,
    the block stack, the deferred queue, and the event log. Reads."""

    reading = True

    def __init__(self, data: bytes, log: bool = True):
        self.data = data
        self.view = memoryview(data)
        self.size = len(data)
        self.fp = 0
        self.pos = [0] * BLOCK_COUNT
        self.cur = TEMP
        self.stack: list[tuple[int, int]] = []
        self.temp_high = 0
        self.deferred: list[DeferredData] = []
        self.log = EventLog() if log else None
        self._ev = self.log.words.extend if log else None
        self._mask = NONE
        self._pending = -1
        #: Strings by memory key ((block << 29) | offset), to resolve offset pointers.
        self.strings: dict[int, str] = {}
        #: Allocations, alias slots and alias chains, to resolve pointers (refs.py).
        self.refs = Refs()
        #: Index of the asset being loaded (-1 before the first).
        self.asset_index = -1
        #: Names of the structs being loaded, outermost first, for error messages.
        self.trail: list[str] = []
        #: The first few strings met in the current asset (its name is usually the
        #: first), so a failure can say which asset it was.
        self.asset_strings: list[str | None] = []
        #: Pointer fields met so far: the ordinal of the next one. Reading and
        #: writing meet pointer fields in the same order, so an ordinal from a
        #: parse's POINTER events names the same field in a write.
        self.pointer_index = 0

    # -- position ---------------------------------------------------------------------------

    def cursors(self) -> tuple[int, ...]:
        return tuple(self.pos)

    def where(self) -> str:
        trail = "/".join(self.trail) or "-"
        return f"file {self.fp:#x}, block {self.cur} +{self.pos[self.cur]:#x}, in {trail}"

    def fail(self, message: str) -> XFileError:
        return XFileError(f"{message} ({self.where()})")

    # -- primitives -------------------------------------------------------------------------

    def push(self, block: int) -> None:
        if self._ev is not None:
            self._ev((PUSH, block, self.cur, self.pos[block], 0, 0))
        self.stack.append((self.pos[block], self.cur))
        self.cur = block
        self._mask = NONE

    def pop(self) -> None:
        if not self.stack:
            raise self.fail("pop with an empty block stack")
        saved, previous = self.stack.pop()
        left = self.cur
        if left == TEMP:
            self.pos[TEMP] = saved
        if self._ev is not None:
            self._ev((POP, previous, left, saved if left == TEMP else NONE, 0, 0))
        self.cur = previous
        self._mask = NONE

    def alloc(self, mask: int) -> int:
        """DB_AllocStreamPos: align the current block position (memory only)."""
        block = self.cur
        before = self.pos[block]
        after = (before + mask) & ~mask
        self.pos[block] = after
        if self._ev is not None:
            self._ev((ALLOC, block, mask, before, after, 0))
            if self._pending >= 0:
                words = self.log.words
                base = self._pending * 6
                words[base + 4] = block
                words[base + 5] = after
        self._pending = -1
        self._mask = mask
        return after

    def insert(self) -> int:
        """DB_InsertPointer: reserve a 4-byte alias slot in VIRTUAL; returns its offset."""
        slot = (self.pos[VIRTUAL] + 3) & ~3
        self.pos[VIRTUAL] = slot + 4
        if self._ev is not None:
            self._ev((INSERT, slot, 0, 0, 0, 0))
        return slot

    def load(self, size: int, node: Any = None, key: Any = None) -> Chunk:
        """Load_Stream(1, p, size) in a block that reads now (TEMP, VIRTUAL, LARGE,
        PHYSICAL); the bytes are stored as node[key] when a node is given. Use
        ``reserve`` for RUNTIME and the deferred blocks."""
        chunk = self._read(size)
        if node is not None:
            node[key] = chunk.bytes()
        self.refs.record(chunk.block, chunk.mem, size, node, key, 0, self.asset_index)
        return chunk

    def items(
        self, size: int, count: int, node: Any, key: Any, kind: str | None = None
    ) -> list[tuple[Chunk, dict]]:
        """One Load_Stream of `count` structs of `size` bytes, stored as node[key] = a
        list of element nodes, each {"raw": its bytes} (and "_t": kind when given);
        returns (chunk, element) pairs."""
        chunk = self._read(size * count)
        data = chunk.data
        if kind is None:
            elements = [{"raw": data[size * i : size * (i + 1)].tobytes()} for i in range(count)]
        else:
            elements = [
                {"_t": kind, "raw": data[size * i : size * (i + 1)].tobytes()} for i in range(count)
            ]
        node[key] = elements
        self.refs.record(chunk.block, chunk.mem, size * count, node, key, size, self.asset_index)
        return list(zip(chunk.items(size, count), elements, strict=True))

    def note(self, node: Any, key: Any, value: Any) -> None:
        """Store a decoded value for the reader's convenience (ignored when writing;
        the bytes stay authoritative)."""
        node[key] = value

    def tag(self, node: dict, kind: str) -> dict:
        """Name the node's kind ("_t"), which keys its field schema
        (opent5.xfile.structs.KINDS). Reading only; returns the node."""
        node["_t"] = kind
        return node

    def children(self, node: Any, key: Any) -> list:
        """A list that holds child nodes: created when reading, fetched when writing."""
        out: list = []
        node[key] = out
        return out

    def child(self, items: list, index: int) -> dict:
        """Element `index` of a child list: appended when reading, fetched when writing."""
        element: dict = {}
        items.append(element)
        return element

    def _read(self, size: int) -> Chunk:
        block = self.cur
        if not _FILE_BLOCKS[block]:
            raise self.fail(f"Load_Stream of {size} bytes: block {block} has no file bytes here")
        if size < 0:
            raise self.fail(f"Load_Stream size: expected >= 0, found {size}")
        fp = self.fp
        mem = self.pos[block]
        if size == 0:
            return Chunk(self.view[fp:fp], fp, block, mem)
        end = fp + size
        if end > self.size:
            raise self.fail(
                f"Load_Stream of {size} bytes: expected at most {self.size - fp} left, "
                f"stream ends at {self.size:#x}"
            )
        self.pos[block] = mem + size
        if block == TEMP and mem + size > self.temp_high:
            self.temp_high = mem + size
        if self._ev is not None:
            self._ev((READ, fp, size, block, mem, self._mask))
        self._mask = NONE
        self.fp = end
        return Chunk(self.view[fp:end], fp, block, mem)

    def reserve(
        self, size: int, node: Any = None, key: Any = None
    ) -> RuntimeData | DeferredData | Chunk:
        """Load_Stream in whatever block is current: RUNTIME gives RuntimeData (no
        file bytes), LARGE_RUNTIME / PHYSICAL_RUNTIME give DeferredData (bytes later),
        the other blocks read now and give a Chunk. node[key] holds the bytes (or the
        DeferredData) when a node is given."""
        block = self.cur
        if _FILE_BLOCKS[block]:
            return self.load(size, node, key)
        if size < 0:
            raise self.fail(f"Load_Stream size: expected >= 0, found {size}")
        mem = self.pos[block]
        if _DEFERRED_BLOCKS[block]:
            item = DeferredData(len(self.deferred), block, mem, size, self.asset_index)
            if size:
                self.deferred.append(item)
                self.pos[block] = mem + size
                if self._ev is not None:
                    self._ev((DEFER, item.index, size, block, mem, self._mask))
            self._mask = NONE
            if node is not None:
                node[key] = item
            self.refs.record(block, mem, size, node, key, 0, self.asset_index)
            return item
        if size:
            self.refs.record(block, mem, size, None, None, 0, self.asset_index)
            self.pos[block] = mem + size
            if self._ev is not None:
                self._ev((READ, NONE, size, block, mem, self._mask))
        self._mask = NONE
        return RuntimeData(block, mem, size)

    def xstring(self) -> str:
        """Load_XString: a NUL-terminated string at the file position."""
        fp = self.fp
        end = self.data.find(b"\0", fp)
        if end < 0:
            raise self.fail(f"string at {fp:#x}: expected a NUL before the stream end")
        size = end + 1 - fp
        block = self.cur
        mem = self.pos[block]
        self.pos[block] = mem + size
        if block == TEMP and mem + size > self.temp_high:
            self.temp_high = mem + size
        if self._ev is not None:
            self._ev((STRING, fp, size, block, mem, self._mask))
        self._mask = NONE
        text = self.data[fp:end].decode("latin-1")
        self.strings[(block << OFFSET_BLOCK_SHIFT) | mem] = text
        self.fp = end + 1
        return text

    def flush_deferred(self) -> None:
        """Read every queued LARGE_RUNTIME / PHYSICAL_RUNTIME chunk, in order."""
        for item in self.deferred:
            fp = self.fp
            end = fp + item.size
            if end > self.size:
                raise XFileError(
                    f"deferred read {item.index} of {item.size:#x} bytes at {fp:#x}: "
                    f"expected at most {self.size - fp:#x} left"
                )
            item.file_offset = fp
            item.data = self.view[fp:end]
            if self._ev is not None:
                self._ev((TAIL, fp, item.size, item.block, item.mem, item.index))
            self.fp = end

    # -- pointer fields ---------------------------------------------------------------------

    def _pointer(self, chunk: Chunk, off: int, raw: int, kind: int) -> None:
        if self._ev is None:
            return
        at = chunk.at + off if chunk.at != NONE else NONE
        if kind == PtrKind.OFFSET or kind == PtrKind.ALIAS_REF:
            v = (raw - 1) & 0xFFFFFFFF
            self._ev((POINTER, at, raw, kind, v >> OFFSET_BLOCK_SHIFT, v & OFFSET_MASK))
        else:
            self._ev((POINTER, at, raw, kind, NONE, NONE))
            if kind != PtrKind.NULL:
                self._pending = len(self.log.words) // 6 - 1

    def _raw(self, chunk: Chunk, off: int) -> int:
        self.pointer_index += 1
        return _U32(chunk.data, off)[0]

    def follows(self, chunk: Chunk, off: int, owned: bool = False) -> bool:
        """A pointer to sub-data: True when the data follows inline.

        Shareable fields (owned=False) are inline only for -1; other non-zero
        values are offset pointers to data loaded earlier. PS3 owned fields
        (owned=True, the loader tests ``!= 0``) are inline for any non-zero value.
        """
        raw = self._raw(chunk, off)
        if raw == PTR_NULL:
            kind = PtrKind.NULL
        elif raw == PTR_INLINE:
            kind = PtrKind.INLINE
        elif owned:
            kind = PtrKind.NONZERO_INLINE
        else:
            kind = PtrKind.OFFSET
        self._pointer(chunk, off, raw, kind)
        return kind == PtrKind.INLINE or kind == PtrKind.NONZERO_INLINE

    def string(self, chunk: Chunk, off: int, node: Any = None, key: Any = None) -> str | None:
        """A ``const char*`` field: -1 -> the string follows (align 1); an offset
        pointer -> the string loaded earlier at that position; 0 -> None. Stored as
        node[key] when a node is given."""
        raw = self._raw(chunk, off)
        if raw == PTR_INLINE:
            self._pointer(chunk, off, raw, PtrKind.INLINE)
            self.alloc(0)
            block, mem = self.cur, self.pos[self.cur]
            text = self.xstring()
            self.refs.record(block, mem, len(text) + 1, node, key, 0, self.asset_index)
        elif raw == PTR_NULL:
            self._pointer(chunk, off, raw, PtrKind.NULL)
            text = None
        else:
            self._pointer(chunk, off, raw, PtrKind.OFFSET)
            text = self.strings.get((raw - 1) & 0xFFFFFFFF)
        if raw != PTR_NULL and len(self.asset_strings) < 3:
            self.asset_strings.append(text)
        if node is not None:
            node[key] = text
        return text

    def ref(self, chunk: Chunk, off: int) -> int:
        """An asset reference field: logs it and returns the raw value."""
        raw = self._raw(chunk, off)
        if raw == PTR_NULL:
            kind = PtrKind.NULL
        elif raw == PTR_INLINE:
            kind = PtrKind.INLINE
        elif raw == PTR_INSERT:
            kind = PtrKind.INLINE_ALIAS
        else:
            kind = PtrKind.ALIAS_REF
        self._pointer(chunk, off, raw, kind)
        return raw

    def convert(self, chunk: Chunk, off: int, alias: bool = False) -> int:
        """A pointer field the loader only converts (DB_ConvertOffsetToPointer, or
        DB_ConvertOffsetToAlias when alias=True) and never loads from the stream:
        logged; returns the raw value. -1 / -2 there would mean inline data whose
        layout is not known, so they stop the parse."""
        raw = self._raw(chunk, off)
        if raw in (PTR_INLINE, PTR_INSERT):
            raise self.fail(
                f"pointer at file {chunk.at + off:#x}: expected 0 or an offset pointer "
                f"(converted only), found {raw:#010x}"
            )
        kind = PtrKind.NULL if raw == PTR_NULL else PtrKind.ALIAS_REF if alias else PtrKind.OFFSET
        self._pointer(chunk, off, raw, kind)
        return raw

    def resolve_string(self, raw: int) -> str | None:
        return self.strings.get((raw - 1) & 0xFFFFFFFF)


class XWriter(XStream):
    """The same primitives, run the other way: every load takes its bytes from the
    node a parse filled and appends them to ``out``, with the block positions,
    alignment, RUNTIME reservations, deferred queue and event log kept exactly as
    the reader keeps them. File offsets in chunks and events are output offsets.

    ``pointer_values`` overrides pointer fields by ordinal (the n-th pointer field
    met, counting from 0 in the order a parse logs POINTER events): the value is
    written in place of the stored one before the field is tested, so it also
    steers what follows. An override that keeps a field's kind (offset to offset,
    inline to inline) leaves every later ordinal where it was; one that changes the
    kind changes what is written after it, and the node must then hold (or stop
    holding) the inline data to match.
    """

    reading = False

    def __init__(self, log: bool = True, pointer_values: dict[int, int] | None = None):
        super().__init__(b"", log=log)
        self.out = bytearray()
        self.pointer_values = pointer_values or {}
        self._queued: list[tuple[DeferredData, bytes]] = []

    def getvalue(self) -> bytes:
        return bytes(self.out)

    # -- emitting ---------------------------------------------------------------------------

    def _emit(self, data: bytes | bytearray | memoryview) -> Chunk:
        block = self.cur
        if not _FILE_BLOCKS[block]:
            raise self.fail(f"Load_Stream of {len(data)} bytes: block {block} has no file bytes")
        size = len(data)
        fp = len(self.out)
        mem = self.pos[block]
        copy = bytearray(data)
        if size:
            self.pos[block] = mem + size
            if block == TEMP and mem + size > self.temp_high:
                self.temp_high = mem + size
            if self._ev is not None:
                self._ev((READ, fp, size, block, mem, self._mask))
            self._mask = NONE
            self.out += copy
        self.fp = len(self.out)
        return Chunk(memoryview(copy), fp, block, mem)

    def _stored(self, node: Any, key: Any, size: int) -> bytes:
        if node is None:
            raise self.fail(f"Load_Stream of {size} bytes: nothing to write (no node)")
        try:
            data = node[key]
        except (KeyError, IndexError, TypeError):
            data = None
        if data is None:
            raise self.fail(f"Load_Stream of {size} bytes: node has no {key!r} to write")
        if len(data) != size:
            raise self.fail(
                f"{key!r}: expected {size} bytes (from the counts already written), "
                f"found {len(data)}"
            )
        return data

    def load(self, size: int, node: Any = None, key: Any = None) -> Chunk:
        if size < 0:
            raise self.fail(f"Load_Stream size: expected >= 0, found {size}")
        if size == 0:
            return self._emit(b"")
        return self._emit(self._stored(node, key, size))

    def items(
        self, size: int, count: int, node: Any, key: Any, kind: str | None = None
    ) -> list[tuple[Chunk, dict]]:
        elements = node.get(key) if isinstance(node, dict) else node[key]
        if elements is None or len(elements) != count:
            found = None if elements is None else len(elements)
            raise self.fail(f"{key!r}: expected {count} elements, found {found}")
        for i, element in enumerate(elements):
            if len(element["raw"]) != size:
                raise self.fail(f"{key!r}[{i}]: expected {size} bytes, found {len(element['raw'])}")
        chunk = self._emit(b"".join(e["raw"] for e in elements))
        return list(zip(chunk.items(size, count), elements, strict=True))

    def note(self, node: Any, key: Any, value: Any) -> None:
        pass

    def tag(self, node: dict, kind: str) -> dict:
        return node

    def children(self, node: Any, key: Any) -> list:
        items = node[key]
        if items is None:
            raise self.fail(f"{key!r}: expected a list of child nodes, found None")
        return items

    def child(self, items: list, index: int) -> dict:
        if index >= len(items):
            raise self.fail(f"child list: expected an element {index}, found {len(items)}")
        return items[index]

    def reserve(
        self, size: int, node: Any = None, key: Any = None
    ) -> RuntimeData | DeferredData | Chunk:
        block = self.cur
        if _FILE_BLOCKS[block]:
            return self.load(size, node, key)
        if not _DEFERRED_BLOCKS[block]:
            return super().reserve(size)
        item = super().reserve(size)
        if size:
            stored = node[key] if node is not None else None
            data = stored.data if isinstance(stored, DeferredData) else stored
            if data is None or len(data) != size:
                found = None if data is None else len(data)
                raise self.fail(f"deferred {key!r}: expected {size} bytes, found {found}")
            self._queued.append((item, bytes(data)))
        return item

    def xstring(self) -> str:
        raise self.fail("xstring: the writer emits strings through string()")

    def string(self, chunk: Chunk, off: int, node: Any = None, key: Any = None) -> str | None:
        raw = self._raw(chunk, off)
        if raw == PTR_INLINE:
            self._pointer(chunk, off, raw, PtrKind.INLINE)
            self.alloc(0)
            text = node[key] if node is not None else None
            if text is None:
                raise self.fail(f"string {key!r}: the pointer says inline, the node has none")
            data = text.encode("latin-1") + b"\0"
            block = self.cur
            mem = self.pos[block]
            fp = len(self.out)
            self.pos[block] = mem + len(data)
            if block == TEMP and mem + len(data) > self.temp_high:
                self.temp_high = mem + len(data)
            if self._ev is not None:
                self._ev((STRING, fp, len(data), block, mem, self._mask))
            self._mask = NONE
            self.out += data
            self.fp = len(self.out)
            self.strings[(block << OFFSET_BLOCK_SHIFT) | mem] = text
            return text
        self._pointer(chunk, off, raw, PtrKind.NULL if raw == PTR_NULL else PtrKind.OFFSET)
        return None if node is None else node[key]

    def flush_deferred(self) -> None:
        for item, data in self._queued:
            fp = len(self.out)
            item.file_offset = fp
            if self._ev is not None:
                self._ev((TAIL, fp, item.size, item.block, item.mem, item.index))
            self.out += data
        self.fp = len(self.out)

    def raw(self, data: bytes) -> int:
        """Bytes outside any block (the XFile header, the XAssetList); returns their offset."""
        at = len(self.out)
        self.out += data
        self.fp = len(self.out)
        return at

    def _raw(self, chunk: Chunk, off: int) -> int:
        index = self.pointer_index
        self.pointer_index = index + 1
        value = self.pointer_values.get(index)
        if value is not None:
            struct.pack_into(">I", chunk.data, off, value)
            if chunk.at != NONE:
                struct.pack_into(">I", self.out, chunk.at + off, value)
            return value
        return _U32(chunk.data, off)[0]
