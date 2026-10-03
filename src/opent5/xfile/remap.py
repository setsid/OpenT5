"""Re-layout a zone after stream data changes length, remapping every offset pointer.

A zone is a memory image (docs/research/xfile.md): offset pointers hold
((block << 29) | position) + 1, a position in block memory, not in the file. When
a string or blob grows or shrinks, everything loaded after it in the same block
moves, and every later DB_AllocStreamPos re-aligns, so the shift is piecewise and
can be absorbed (or changed) by padding. This module redoes that arithmetic from
the parse's event log alone (no emulator):

1. Replay. Every event of the original parse is replayed with the loader's
   rules (push / pop with TEMP rewind, alloc with its mask, Load_Stream,
   strings, alias slots, deferred reads), once with the original sizes (each
   logged position is checked) and once with the edited sizes. Each event that
   takes block memory becomes an allocation (block, old start, old size, new
   start, new size).
2. Map. Each OFFSET / ALIAS_REF pointer target (block, offset) is mapped through
   the allocation that contained it when the pointer was read, keeping the
   offset inside that allocation. Non-TEMP blocks only grow, so a bisect on the
   start positions finds it; TEMP is rewound after every asset, so a TEMP target
   is looked up among the TEMP allocations made before the pointer.
3. Write. Changed pointer fields and the size fields declared for the edited
   data (see ``SIZE_FIELDS``) are rewritten at their original file offsets, then
   the edits are spliced in, then the header: blockSize[b] moves by the change in
   that block's final position (TEMP: by the change in its high-water mark), and
   size = len - 36. The container layer (``Zone.save``) does the rest.

Supported edits: a byte range inside data the loader reads from the file now
(READ or STRING events in TEMP, VIRTUAL, LARGE or PHYSICAL), changed to bytes of
any length; same-length edits anywhere in read data (deferred tail included).
An edit must not change anything the loader reads to decide what to read next
(counts, pointer markers, sizes) unless a size-field rule declares the field; a
string edit must keep exactly one NUL, at the end. ``check`` reparses the result
and compares it with the original at the pointer level.

Proof against the game's loader: tests/test_xfile_remap.py reproduces the two
hardware test zones that tools/remap_oracle.py built and validated with the
emulated loader, byte for byte (docs/research/remap.md).
"""

from __future__ import annotations

import bisect
import struct
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from opent5.xfile.constants import (
    BLOCK_COUNT,
    HEADER_SIZE,
    OFFSET_BLOCK_SHIFT,
    OFFSET_MASK,
    AssetType,
    Block,
)
from opent5.xfile.events import NONE, EventKind, PtrKind
from opent5.xfile.model import XFile, parse
from opent5.xfile.stream import Platform, XFileError, XStream, XWriter

READ, STRING, DEFER, TAIL = EventKind.READ, EventKind.STRING, EventKind.DEFER, EventKind.TAIL
PUSH, POP, ALLOC, POINTER = EventKind.PUSH, EventKind.POP, EventKind.ALLOC, EventKind.POINTER
INSERT, ASSET = EventKind.INSERT, EventKind.ASSET
TEMP, VIRTUAL = Block.TEMP, Block.VIRTUAL


class RemapError(XFileError):
    """An edit the remap cannot apply; the message names the offset and the values."""


@dataclass(frozen=True)
class Edit:
    """Replace ``old_length`` bytes at content offset ``offset`` with ``data``."""

    offset: int
    old_length: int
    data: bytes

    @property
    def delta(self) -> int:
        return len(self.data) - self.old_length

    @property
    def end(self) -> int:
        return self.offset + self.old_length


def string_edit(content: bytes, offset: int, text: bytes | str) -> Edit:
    """Replace the NUL-terminated string starting at ``offset`` (the NUL stays)."""
    if isinstance(text, str):
        text = text.encode("latin-1")
    end = content.find(b"\0", offset)
    if end < 0:
        raise RemapError(f"string at {offset:#x}: expected a NUL before the stream end")
    if b"\0" in text:
        raise RemapError(f"string at {offset:#x}: the new text contains a NUL")
    return Edit(offset, end - offset, bytes(text))


@dataclass(frozen=True)
class FieldPatch:
    """A size/count field to rewrite because an edit changed the data it describes."""

    offset: int
    fmt: str  # struct format, big-endian, e.g. ">i"
    value: int


@dataclass(frozen=True)
class EditContext:
    """What a size-field rule is told about one resized read."""

    xfile: XFile
    content: bytes
    asset_index: int
    #: Event index of the edited READ / STRING, and of the asset's ASSET record.
    event: int
    asset_event: int
    delta: int


#: Size-field rules by asset type: given an edited read inside an asset of that
#: type, return the fields that must change with it. A type's write side can
#: register its own with ``size_rule``. Strings need none (they are
#: NUL-terminated); rawfile buffers carry ``len``.
SIZE_FIELDS: dict[int, Callable[[EditContext], list[FieldPatch]]] = {}


def size_rule(asset_type: int):
    def wrap(fn: Callable[[EditContext], list[FieldPatch]]):
        SIZE_FIELDS[int(asset_type)] = fn
        return fn

    return wrap


@size_rule(AssetType.RAWFILE)
def _rawfile_len(ctx: EditContext) -> list[FieldPatch]:
    """RawFile: +4 len, the buffer read (align 16) is len + 1 bytes
    (structs-content.md section 3). The header is the asset's first READ."""
    rows = ctx.xfile.log.table()
    kind, fp, size = (int(x) for x in rows[ctx.event, :3])
    if kind != READ:
        return []  # the name string: nothing to declare
    header = ctx.asset_event + 1
    while int(rows[header, 0]) != READ:
        header += 1
    hfp = int(rows[header, 1])
    (length,) = struct.unpack_from(">i", ctx.content, hfp + 4)
    if header == ctx.event or size != length + 1:
        raise RemapError(
            f"rawfile asset {ctx.asset_index}: the edited read at {fp:#x} ({size} bytes) is "
            f"not the buffer (len {length} at {hfp + 4:#x} gives {length + 1})"
        )
    return [FieldPatch(hfp + 4, ">i", length + ctx.delta)]


@dataclass
class Allocation:
    block: int
    old_start: int
    old_size: int
    new_start: int
    new_size: int
    event: int


@dataclass
class RemapResult:
    content: bytes
    #: (field file offset in the original, old value, new value), changed ones only.
    pointers: list[tuple[int, int, int]]
    #: Size fields rewritten (original file offset, old value, new value).
    fields: list[tuple[int, int, int]]
    header_before: tuple[int, ...]
    header_after: tuple[int, ...]
    #: Per block: the allocations, in load order (non-zero sizes only).
    allocations: dict[int, list[Allocation]] = field(repr=False, default_factory=dict)

    def runs(self, block: int) -> list[list[int]]:
        """[old start, shift] breakpoints for a block: the shift holds from there on."""
        out: list[list[int]] = []
        for a in self.allocations.get(block, []):
            shift = a.new_start - a.old_start
            if not out or out[-1][1] != shift:
                out.append([a.old_start, shift])
        return out


class _Layout:
    """Allocations of one replay, with lookup by (block, offset) at a moment."""

    def __init__(self) -> None:
        self.by_block: dict[int, list[Allocation]] = {b: [] for b in range(BLOCK_COUNT)}
        self._starts: dict[int, list[int]] = {}

    def add(self, a: Allocation) -> None:
        if a.old_size or a.new_size:
            self.by_block[a.block].append(a)

    def freeze(self) -> None:
        self._starts = {b: [a.old_start for a in v] for b, v in self.by_block.items()}
        self._events = {b: [a.event for a in v] for b, v in self.by_block.items()}

    def find(self, block: int, offset: int, before_event: int) -> Allocation:
        allocs = self.by_block[block]
        if block == TEMP:
            # TEMP memory is reused: the latest allocation covering it so far.
            last = bisect.bisect_left(self._events[TEMP], before_event)
            for a in reversed(allocs[:last]):
                if a.old_start <= offset < a.old_start + a.old_size:
                    return a
            raise RemapError(f"TEMP target {offset:#x}: no live allocation holds it")
        i = bisect.bisect_right(self._starts[block], offset) - 1
        if i >= 0:
            a = allocs[i]
            if offset < a.old_start + a.old_size:
                return a
            if offset == a.old_start + a.old_size:
                return a  # one past the end (an empty tail); keeps its distance
        raise RemapError(f"block {block} target {offset:#x} lies in no allocation (padding)")

    def map(self, block: int, offset: int, before_event: int) -> int:
        a = self.find(block, offset, before_event)
        inner = offset - a.old_start
        if inner > a.new_size:
            raise RemapError(
                f"block {block} target {offset:#x} is {inner} bytes into an allocation that "
                f"shrank to {a.new_size}"
            )
        return a.new_start + inner


def _replay(rows, sizes: dict[int, int] | None) -> tuple[_Layout, list[int], int]:
    """Replay the event log; ``sizes`` overrides the size of some events. With
    sizes=None every logged position is checked against the replay."""
    check = sizes is None
    sizes = sizes or {}
    pos = [0] * BLOCK_COUNT
    cur = TEMP
    stack: list[tuple[int, int]] = []
    high = 0
    lay = _Layout()

    def drift(i: int, what: str, logged: int, have: int) -> RemapError:
        return RemapError(f"event {i}: {what} logged {logged:#x}, replay has {have:#x}")

    for i, row in enumerate(rows.tolist()):
        kind = row[0]
        if kind in (READ, STRING, DEFER):
            block, mem = row[3], row[4]
            if check and pos[block] != mem:
                raise drift(i, f"block {block} position", mem, pos[block])
            size = sizes.get(i, row[2])
            start = pos[block]
            lay.add(Allocation(block, mem, row[2], start, size, i))
            pos[block] = start + size
            if block == TEMP and kind != DEFER and start + size > high:
                high = start + size
        elif kind == ALLOC:
            block, mask = row[1], row[2]
            if check and (pos[block] != row[3] or block != cur):
                raise drift(i, f"alloc in block {block}", row[3], pos[block])
            pos[block] = (pos[block] + mask) & ~mask
        elif kind == PUSH:
            stack.append((pos[row[1]], cur))
            cur = row[1]
        elif kind == POP:
            saved, previous = stack.pop()
            if cur == TEMP:
                pos[TEMP] = saved
            cur = previous
        elif kind == INSERT:
            slot = (pos[VIRTUAL] + 3) & ~3
            if check and slot != row[1]:
                raise drift(i, "alias slot", row[1], slot)
            lay.add(Allocation(VIRTUAL, row[1], 4, slot, 4, i))
            pos[VIRTUAL] = slot + 4
        # TAIL, POINTER, ASSET: no block memory
    lay.freeze()
    return lay, pos, high


def _owner(asset_events: Sequence[int], event: int) -> int:
    """Index (into asset_events) of the asset whose events contain ``event``; -1 before."""
    return bisect.bisect_right(asset_events, event) - 1


def remap(xfile: XFile, content: bytes, edits: Iterable[Edit], check: bool = False) -> RemapResult:
    """Apply ``edits`` to ``content`` (the stream ``xfile`` was parsed from, with
    its event log) and return the re-laid-out content."""
    if xfile.log is None:
        raise RemapError("remap needs a parse with its event log (parse(content, log=True))")
    content = bytes(content)
    rows = xfile.log.table()
    edits = sorted(edits, key=lambda e: e.offset)
    for a, b in zip(edits, edits[1:], strict=False):
        if b.offset < a.end:
            raise RemapError(f"edits at {a.offset:#x} and {b.offset:#x} overlap")

    # Which read each edit falls in.
    kinds = rows[:, 0]
    reads = np.nonzero(
        ((kinds == READ) | (kinds == STRING) | (kinds == TAIL))
        & (rows[:, 1] != NONE)
        & (rows[:, 2] != 0)
    )[0].tolist()
    read_fp = rows[reads, 1].tolist()
    sizes: dict[int, int] = {}
    for e in edits:
        k = bisect.bisect_right(read_fp, e.offset) - 1
        if k < 0:
            raise RemapError(f"edit at {e.offset:#x}: before the first read (header / list)")
        i = reads[k]
        fp, size = int(rows[i, 1]), int(rows[i, 2])
        if e.end > fp + size:
            raise RemapError(
                f"edit {e.offset:#x}..{e.end:#x} crosses the end of the read at "
                f"{fp:#x}..{fp + size:#x}; split it per read"
            )
        if e.delta and rows[i, 0] == TAIL:
            raise RemapError(f"edit at {e.offset:#x}: deferred data cannot change length")
        if e.delta:
            sizes[i] = sizes.get(i, size) + e.delta
    # Strings must stay one string each.
    for i in sizes:
        if rows[i, 0] == STRING:
            fp, size = int(rows[i, 1]), int(rows[i, 2])
            inside = [e for e in edits if fp <= e.offset < fp + size]
            body = _apply(content[fp : fp + size], inside, fp)
            if body.find(b"\0") != len(body) - 1:
                raise RemapError(f"string at {fp:#x}: the edit must keep one NUL, at the end")

    _, old_pos, old_high = _replay(rows, None)  # checks every logged position
    new, new_pos, new_high = _replay(rows, sizes)

    # Size fields declared for the resized reads.
    asset_events = [a.event_start for a in xfile.assets]
    patches: list[FieldPatch] = []
    for i, size in sizes.items():
        owner = _owner(asset_events, i)
        if owner < 0:
            continue
        asset = xfile.assets[owner]
        rule = SIZE_FIELDS.get(asset.type)
        if rule is not None:
            ctx = EditContext(xfile, content, owner, i, asset.event_start, size - int(rows[i, 2]))
            patches.extend(rule(ctx))

    # Pointers.
    out = bytearray(content)
    pointers: list[tuple[int, int, int]] = []
    for i in _movable_pointers(rows):
        _, at, raw, _, block, offset = rows[i].tolist()
        # The edited replay's allocations carry both the logged (old) and new starts.
        target = new.map(block, offset, i)
        value = ((block << OFFSET_BLOCK_SHIFT) | (target & OFFSET_MASK)) + 1
        if value != raw:
            if at == NONE:
                raise RemapError(f"pointer event {i}: field has no file bytes (RUNTIME)")
            pointers.append((at, raw, value))
            struct.pack_into(">I", out, at, value)
    for e in edits:
        for at, _, _ in pointers:
            if at < e.end and e.offset < at + 4:
                raise RemapError(f"edit at {e.offset:#x} overlaps the pointer field at {at:#x}")
    fields: list[tuple[int, int, int]] = []
    for p in patches:
        (was,) = struct.unpack_from(p.fmt, content, p.offset)
        struct.pack_into(p.fmt, out, p.offset, p.value)
        fields.append((p.offset, was, p.value))

    result = _apply(bytes(out), edits, 0)
    before = struct.unpack_from(">9I", content, 0)
    blocks = list(before[2:9])
    for b in range(BLOCK_COUNT):
        if b == TEMP:
            blocks[b] += new_high - old_high
        else:
            blocks[b] += new_pos[b] - old_pos[b]
    after = (len(result) - HEADER_SIZE, before[1], *blocks)
    result = struct.pack(">9I", *after) + result[HEADER_SIZE:]
    res = RemapResult(
        content=result,
        pointers=pointers,
        fields=fields,
        header_before=before,
        header_after=after,
        allocations=new.by_block,
    )
    if check:
        problems = compare(xfile, parse(result))
        if problems:
            raise RemapError("remap check failed: " + "; ".join(problems[:5]))
    return res


def _apply(data: bytes, edits: Sequence[Edit], base: int) -> bytes:
    """Splice sorted, non-overlapping edits (offsets relative to ``base``) into data."""
    parts, at = [], 0
    for e in edits:
        start = e.offset - base
        parts.append(data[at:start])
        parts.append(e.data)
        at = start + e.old_length
    parts.append(data[at:])
    return b"".join(parts)


def _movable_pointers(rows) -> list[int]:
    """Event indices of the OFFSET / ALIAS_REF pointer records."""
    kinds = rows[:, 3]
    mask = (rows[:, 0] == POINTER) & ((kinds == PtrKind.OFFSET) | (kinds == PtrKind.ALIAS_REF))
    return np.nonzero(mask)[0].tolist()


def logical_targets(xfile: XFile) -> list[tuple[int, int, int, int]]:
    """Every OFFSET / ALIAS_REF pointer as (kind, allocation ordinal, offset inside
    it): what it points at, independent of where things sit in memory."""
    rows = xfile.log.table()
    lay, _, _ = _replay(rows, None)
    ordinal = {}
    for b, allocs in lay.by_block.items():
        for n, a in enumerate(allocs):
            ordinal[(b, a.event)] = n
    out = []
    for i in _movable_pointers(rows):
        _, _, _, kind, block, offset = rows[i].tolist()
        a = lay.find(block, offset, i)
        out.append((kind, block, ordinal[(block, a.event)], offset - a.old_start))
    return out


def compare(before: XFile, after: XFile, before_targets: list | None = None) -> list[str]:
    """Parse-level check of a remap: the new parse is exact and every pointer has the
    same kind and the same logical target. Empty when it holds. ``before_targets``
    (logical_targets(before)) can be passed in when checking many edits of one zone."""
    problems = list(after.problems())
    kb = [int(x) for x in before.log.of_kind(POINTER)[:, 3]]
    ka = [int(x) for x in after.log.of_kind(POINTER)[:, 3]]
    if kb != ka:
        problems.append(f"pointer kinds differ ({len(kb)} before, {len(ka)} after)")
        return problems
    tb = before_targets if before_targets is not None else logical_targets(before)
    ta = logical_targets(after)
    for n, (x, y) in enumerate(zip(tb, ta, strict=True)):
        if x != y:
            problems.append(f"offset pointer {n}: target {x} before, {y} after")
            if len(problems) > 20:
                break
    return problems


# -- Rewriting from edited nodes: counts and element arrays may change -----------------------
#
# The splice remap above changes the length of reads the loader already makes. An edit that
# changes counts (a stringtable row added or removed), turns a shared string into an inline
# one, or otherwise changes which reads happen, cannot be a byte splice: the type's write side
# has to emit the asset again. ``Rewrite`` does that for the whole zone and then remaps the
# offset pointers the same way the splice remap does, with one difference in how it matches
# old allocations to new ones: by what was loaded rather than by position in the event log.
#
# 1. Trace. The zone is parsed with a stream that records, for every event that takes block
#    memory, the identity of what was loaded: ("L", node, key) for a Load_Stream into
#    node[key] (an array of structs also records its element nodes, in order), ("S", node,
#    key) for an inline string, ("P", asset node) for a DB_InsertPointer alias slot, ("R",
#    asset, n) for the n-th RUNTIME reservation of an asset, and ("pre", event) for the
#    script strings and the asset array (which no edit changes). Nodes are named by their
#    Python identity; the trace keeps a reference to each, so an identity is never reused.
# 2. Edit. The caller changes the parsed nodes in place: bytes, strings, element lists
#    (removing an element dict, inserting a new one). Anything kept keeps its identity.
# 3. Write. The edited zone is written by the same handlers, with the same tracing, so every
#    allocation in the new stream has an identity too, and the header is derived from the
#    written stream (size, every blockSize; TEMP as high-water mark + 16).
# 4. Map. Every OFFSET / ALIAS_REF pointer the writer emitted still holds its old value. Its
#    old target is found in the old layout (the allocation containing it), named by identity,
#    and looked up in the new layout, keeping the offset inside the allocation; inside an array
#    of structs the element is followed by identity, so a pointer to element 7 of 9 follows
#    that element when an earlier one is removed. A pointer whose target was removed raises
#    ``RemapError`` naming the field. Pointers into TEMP do not occur in any zone walked
#    (every converted pointer targets VIRTUAL or PHYSICAL) and are refused.
#
# For an edit that only resizes reads this gives exactly the splice remap's result
# (tests/test_xfile_remap.py checks both on the same edits); for an unedited zone it gives
# the original content.


class _Trace:
    """What a traced parse or write loaded, keyed by event index."""

    def __init__(self) -> None:
        #: allocation event (READ / STRING / DEFER / INSERT) -> identity
        self.ident: dict[int, tuple] = {}
        #: event of a Load_Stream of an array of structs -> element ids, in order
        self.elements: dict[int, list[int]] = {}
        #: the same, the element nodes themselves (the list as it was loaded)
        self.lists: dict[int, list] = {}
        #: READ / DEFER event -> (node, key) that holds its bytes
        self.holders: dict[int, tuple[Any, Any]] = {}
        #: file offset of an offset-pointer string field -> (node, key) holding its text
        self.string_fields: dict[int, tuple[Any, Any]] = {}
        #: id(asset node) -> (file start, file end) of everything its pointer loaded
        self.spans: dict[int, tuple[int, int]] = {}
        self.keep: list = []


class _Tracing:
    """Mixin over XStream / XWriter: records ``_Trace`` while walking."""

    def _setup_trace(self) -> None:
        self.trace = _Trace()
        self._insert_event = -1
        self._headers: list[tuple[int, Any, int]] = []
        self._runtime_asset = -2
        self._runtime_count = 0
        self._loose = 0

    def _last_alloc(self, n0: int) -> int:
        words = self.log.words
        for i in range(len(words) // 6 - 1, n0 - 1, -1):
            if words[i * 6] in (READ, STRING, DEFER, INSERT):
                return i
        return -1

    def _mark(self, n0: int, ident: tuple, holder: tuple | None = None) -> int:
        i = self._last_alloc(n0)
        if i < 0 or i in self.trace.ident:
            return -1
        self.trace.ident[i] = ident if self.asset_index >= 0 else ("pre", i)
        if holder is not None:
            self.trace.holders[i] = holder
        return i

    def load(self, size: int, node: Any = None, key: Any = None):
        n0 = len(self.log)
        chunk = super().load(size, node, key)
        if node is not None:
            self._mark(n0, ("L", id(node), key), (node, key))
            self.trace.keep.append(node)
            if key == "header" and isinstance(node, dict):
                if self._insert_event >= 0:
                    self.trace.ident[self._insert_event] = ("P", id(node))
                    self._insert_event = -1
                self._headers.append((len(self.stack), node, chunk.at))
        else:
            self._loose += 1
            self._mark(n0, ("N", self.asset_index, self._loose))
        return chunk

    def items(self, size: int, count: int, node: Any, key: Any, kind: str | None = None):
        n0 = len(self.log)
        pairs = super().items(size, count, node, key, kind)
        i = self._mark(n0, ("L", id(node), key), (node, key))
        self.trace.keep.append(node)
        if i >= 0:
            elements = node[key]
            self.trace.elements[i] = [id(e) for e in elements]
            self.trace.lists[i] = list(elements)
        return pairs

    def reserve(self, size: int, node: Any = None, key: Any = None):
        n0 = len(self.log)
        item = super().reserve(size, node, key)
        if node is not None:
            self._mark(n0, ("L", id(node), key), (node, key))
            self.trace.keep.append(node)
        else:
            if self._runtime_asset != self.asset_index:
                self._runtime_asset, self._runtime_count = self.asset_index, 0
            self._runtime_count += 1
            self._mark(n0, ("R", self.asset_index, self._runtime_count))
        return item

    def string(self, chunk, off: int, node: Any = None, key: Any = None):
        n0 = len(self.log)
        text = super().string(chunk, off, node, key)
        if self._last_alloc(n0) >= 0:
            self._mark(n0, ("S", id(node), key))
            self.trace.keep.append(node)
        elif node is not None and chunk.at != NONE:
            self.trace.string_fields[chunk.at + off] = (node, key)
            self.trace.keep.append(node)
        return text

    def insert(self) -> int:
        slot = super().insert()
        self._insert_event = len(self.log) - 1
        return slot

    def pop(self) -> None:
        super().pop()
        while self._headers and len(self.stack) < self._headers[-1][0]:
            _, node, start = self._headers.pop()
            self.trace.spans[id(node)] = (start, self.fp)


class _TracingStream(_Tracing, XStream):
    def __init__(self, data: bytes, platform: Platform | None = None):
        XStream.__init__(self, data, log=True, platform=platform)
        self._setup_trace()


class _TracingWriter(_Tracing, XWriter):
    def __init__(self, platform: Platform | None = None):
        XWriter.__init__(self, log=True, platform=platform)
        self._setup_trace()


def traced_parse(
    content: bytes | bytearray | memoryview, progress=None, platform: Platform | None = None
) -> tuple[XFile, _Trace]:
    """``parse(content)`` (with its event log), plus the trace ``Rewrite`` needs.
    ``progress(stage, done, total)`` is optional (asset counts)."""
    from opent5.xfile.constants import ASSET_LIST_OFFSET, ASSET_LIST_SIZE
    from opent5.xfile.model import PS3, XFileHeader, _walk

    platform = platform or PS3
    data = bytes(content)
    header = XFileHeader.parse(data, platform.endian)
    if len(data) < ASSET_LIST_OFFSET + ASSET_LIST_SIZE:
        raise XFileError(
            f"XAssetList at {ASSET_LIST_OFFSET:#x}: expected {ASSET_LIST_SIZE} bytes, "
            f"stream is {len(data)}"
        )
    st = _TracingStream(data, platform)
    st.progress = progress
    list_bytes = data[ASSET_LIST_OFFSET : ASSET_LIST_OFFSET + ASSET_LIST_SIZE]
    st.fp = ASSET_LIST_OFFSET + ASSET_LIST_SIZE
    parts: dict = {}
    walked = _walk(st, list_bytes, ASSET_LIST_OFFSET, parts, None)
    xfile = XFile(
        header=header,
        script_strings=parts.get("script_strings") or [],
        assets=walked.assets,
        script_strings_offset=walked.strings_at,
        asset_array_offset=walked.array_at,
        tail_offset=walked.tail,
        end_offset=st.fp,
        length=len(data),
        deferred=st.deferred,
        final_cursors=st.cursors(),
        temp_high_water=st.temp_high,
        log=st.log,
        asset_list=list_bytes,
        script_string_ptrs=parts.get("script_string_ptrs"),
        asset_entries=parts.get("asset_entries"),
        refs=st.refs,
        platform=platform,
    )
    return xfile, st.trace


def traced_write(xfile: XFile, progress=None):
    """``write(xfile)`` with the header derived from the stream, plus its trace."""
    from opent5.xfile.model import TEMP_SLACK, Written, XFileHeader, _walk

    endian = xfile.platform.endian
    writer = _TracingWriter(xfile.platform)
    writer.progress = progress
    writer.raw(xfile.header.pack(endian))
    list_at = writer.raw(xfile.asset_list)
    parts = {
        "script_string_ptrs": xfile.script_string_ptrs,
        "script_strings": xfile.script_strings,
        "asset_entries": xfile.asset_entries,
    }
    walked = _walk(writer, xfile.asset_list, list_at, parts, xfile)
    content = writer.out
    sizes = list(writer.pos)
    sizes[TEMP] = writer.temp_high + TEMP_SLACK
    header = XFileHeader(len(content) - HEADER_SIZE, xfile.header.external_size, tuple(sizes))
    content[0:HEADER_SIZE] = header.pack(endian)
    written = Written(
        content=bytes(content),
        header=header,
        assets=walked.assets,
        tail_offset=walked.tail,
        final_cursors=writer.cursors(),
        temp_high_water=writer.temp_high,
        log=writer.log,
    )
    return written, writer.trace


def describe_identity(ident: tuple | None) -> str:
    if ident is None:
        return "an untraced allocation"
    tag = ident[0]
    if tag == "S":
        return f"the string {ident[2]!r} of node {ident[1]:#x}"
    if tag == "L":
        return f"the data {ident[2]!r} of node {ident[1]:#x}"
    if tag == "P":
        return f"the alias slot of asset node {ident[1]:#x}"
    if tag == "R":
        return f"RUNTIME reservation {ident[2]} of asset {ident[1]}"
    return f"{ident!r}"


class Rewrite:
    """A zone parsed for editing: change ``xfile``'s nodes in place, then ``build``.

        rw = Rewrite(content)
        table = rw.xfile.assets[52].data
        del table["cells"][0:2]               # e.g. drop a row of a two-column table
        ...                                   # header counts, cellIndex
        result = rw.build(check=True)         # RemapResult; result.content is the new zone

    ``build`` may be called again after further edits; the reference is always the content
    the Rewrite was made from."""

    def __init__(
        self,
        content: bytes | bytearray | memoryview,
        progress=None,
        platform: Platform | None = None,
    ):
        self.content = bytes(content)
        self.xfile, self.trace = traced_parse(self.content, progress, platform)
        self._layout: _Layout | None = None
        self._by_ident: dict[tuple, int] | None = None
        self._reads: tuple[list[int], list[int]] | None = None

    # -- the original layout ---------------------------------------------------------------

    def layout(self) -> _Layout:
        if self._layout is None:
            self._layout, _, _ = _replay(self.xfile.log.table(), None)
        return self._layout

    def event_of(self, ident: tuple) -> int | None:
        """The original allocation event of an identity, e.g. ("S", id(node), "value")."""
        if self._by_ident is None:
            self._by_ident = {v: k for k, v in self.trace.ident.items()}
        return self._by_ident.get(ident)

    def owner(self, event: int) -> int:
        """Index of the top-level asset whose load logged ``event`` (-1 before the first)."""
        return _owner([a.event_start for a in self.xfile.assets], event)

    def sharers(self, node: Any, key: Any, string: bool = True) -> list[int]:
        """POINTER events (OFFSET / ALIAS_REF) of the original zone that point into what was
        loaded as node[key] (an inline string when ``string``, else a Load_Stream)."""
        event = self.event_of(("S" if string else "L", id(node), key))
        if event is None:
            return []
        rows = self.xfile.log.table()
        _, _, size, block, mem, _ = (int(v) for v in rows[event])
        movable = _movable_pointers(rows)
        if not movable:
            return []
        p = rows[movable]
        hit = (p[:, 4] == block) & (p[:, 5] >= mem) & (p[:, 5] < mem + max(size, 1))
        return [movable[i] for i in np.nonzero(hit)[0].tolist()]

    def field_holder(self, file_offset: int) -> tuple[Any, Any, int]:
        """The node, key and byte offset holding the original file bytes at
        ``file_offset`` (a struct field): for an array of structs, the element node and
        "raw". Raises RemapError when no traced read holds it."""
        if self._reads is None:
            rows = self.xfile.log.table()
            reads = np.nonzero((rows[:, 0] == READ) & (rows[:, 1] != NONE) & (rows[:, 2] != 0))[0]
            self._reads = (rows[reads, 1].tolist(), reads.tolist())
        starts, events = self._reads
        k = bisect.bisect_right(starts, file_offset) - 1
        if k >= 0:
            event = events[k]
            rows = self.xfile.log.table()
            fp, size = int(rows[event, 1]), int(rows[event, 2])
            holder = self.trace.holders.get(event)
            if file_offset < fp + size and holder is not None:
                node, key = holder
                inner = file_offset - fp
                elements = self.trace.lists.get(event)
                if elements:
                    k2, sub = divmod(inner, size // len(elements))
                    return elements[k2], "raw", sub
                return node, key, inner
        raise RemapError(f"field at {file_offset:#x}: expected a traced read to hold it, none does")

    # -- writing ---------------------------------------------------------------------------

    def build(self, check: bool = False, progress=None) -> RemapResult:
        """Write the (edited) nodes and remap every offset pointer; see the section notes.
        ``progress(stage, done, total)`` is optional (assets written, pointers remapped)."""
        written, new_trace = traced_write(self.xfile, progress)
        new_rows = written.log.table()
        old = self.layout()
        new, _, _ = _replay(new_rows, None)  # also checks the writer's own positions
        by_ident: dict[tuple, Allocation] = {}
        duplicate: set[tuple] = set()
        for allocs in new.by_block.values():
            for a in allocs:
                ident = new_trace.ident.get(a.event)
                if ident is None:
                    continue
                if ident in by_ident:
                    duplicate.add(ident)
                by_ident[ident] = a
        positions: dict[int, dict[int, int]] = {}
        endian = self.xfile.platform.endian
        out = bytearray(written.content)
        pointers: list[tuple[int, int, int]] = []
        movable = _movable_pointers(new_rows)
        for n, j in enumerate(movable):
            if progress is not None and n % 4096 == 0:
                progress("Remapping pointers", n, len(movable))
            _, at, raw, _, block, offset = new_rows[j].tolist()
            if block == TEMP:
                raise RemapError(
                    f"pointer field at {at:#x} (value {raw:#010x}): expected a target outside "
                    "TEMP, found one in TEMP"
                )
            a_old = old.find(block, offset, NONE)
            ident = self.trace.ident.get(a_old.event)
            a_new = by_ident.get(ident) if ident is not None else None
            if a_new is None or ident in duplicate:
                why = "which the edit removed" if a_new is None else "which was written twice"
                raise RemapError(
                    f"pointer field at {at:#x} (value {raw:#010x}) names block {block} "
                    f"+{offset:#x}, inside {describe_identity(ident)}, {why}"
                )
            inner = offset - a_old.old_start
            old_ids = self.trace.elements.get(a_old.event)
            if old_ids:
                esz = a_old.old_size // len(old_ids)
                k, sub = divmod(inner, esz)
                new_ids = new_trace.elements.get(a_new.event, [])
                if k >= len(old_ids):
                    nk = len(new_ids)
                else:
                    where = positions.get(a_new.event)
                    if where is None:
                        where = positions[a_new.event] = {e: n for n, e in enumerate(new_ids)}
                    nk = where.get(old_ids[k])
                    if nk is None:
                        raise RemapError(
                            f"pointer field at {at:#x} (value {raw:#010x}) names element {k} "
                            f"of {describe_identity(ident)}, which the edit removed"
                        )
                inner = nk * esz + sub
            if inner > a_new.old_size:
                raise RemapError(
                    f"pointer field at {at:#x}: target {inner} bytes into "
                    f"{describe_identity(ident)}, which is now {a_new.old_size} bytes"
                )
            value = ((block << OFFSET_BLOCK_SHIFT) | ((a_new.old_start + inner) & OFFSET_MASK)) + 1
            if value != raw:
                if at == NONE:
                    raise RemapError(f"pointer event {j}: field has no file bytes (RUNTIME)")
                struct.pack_into(endian + "I", out, at, value)
                pointers.append((at, raw, value))
        moved: dict[int, list[Allocation]] = {b: [] for b in range(BLOCK_COUNT)}
        for b, allocs in old.by_block.items():
            for a in allocs:
                n = by_ident.get(self.trace.ident.get(a.event, ("?",)))
                if n is not None:
                    moved[b].append(
                        Allocation(b, a.old_start, a.old_size, n.old_start, n.old_size, a.event)
                    )
        result = RemapResult(
            content=bytes(out),
            pointers=pointers,
            fields=[],
            header_before=struct.unpack_from(endian + "9I", self.content, 0),
            header_after=struct.unpack_from(endian + "9I", out, 0),
            allocations=moved,
        )
        if check:
            problems = list(
                parse(result.content, log=False, platform=self.xfile.platform).problems()
            )
            if problems:
                raise RemapError("rewrite check failed: " + "; ".join(problems[:5]))
        return result
