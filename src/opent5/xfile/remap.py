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
from opent5.xfile.stream import XFileError

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
