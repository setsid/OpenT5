"""The event log: every stream operation of a parse, in order, compact.

A remap (re-laying out a zone after an asset changes size) needs to know, for
every byte the loader read, where it came from in the file and where it went
in block memory, and for every pointer field, what it pointed at. The parse
records exactly that, one fixed-width record per event, in a single
``array('I')``: six unsigned 32-bit words per event (24 bytes), so even
mp_nuked's few million events stay small. ``EventLog.table()`` views it as an
(n, 6) numpy array without copying.

Record layouts (word 0 is the kind; NONE = 0xffffffff marks "not applicable"):

=========  =============  ========  ==========  ===========  ==============
kind       word 1         word 2    word 3      word 4       word 5
=========  =============  ========  ==========  ===========  ==============
READ       file offset    size      block       mem offset   align mask
STRING     file offset    size      block       mem offset   align mask
DEFER      tail index     size      block       mem offset   align mask
TAIL       file offset    size      block       mem offset   tail index
PUSH       block          previous  saved pos   0            0
POP        restored       left      TEMP pos    0            0
ALLOC      block          mask      before      after        0
POINTER    field offset   raw       PtrKind     target block target offset
INSERT     slot offset    0         0           0            0
ASSET      asset index    type      file offset 0            0
=========  =============  ========  ==========  ===========  ==============

READ covers every Load_Stream into TEMP, VIRTUAL, LARGE and PHYSICAL (bytes
copied from the file) and into RUNTIME (file offset NONE: zero-filled, no file
bytes). Its align mask is that of the DB_AllocStreamPos call immediately
before it in the same block, or NONE when the read was not aligned first.
STRING is a NUL-terminated string read (size includes the NUL). DEFER is a
Load_Stream into LARGE_RUNTIME or PHYSICAL_RUNTIME: block memory is reserved
now, the bytes are read after the last asset, by the TAIL record with the same
tail index. POP's "TEMP pos" is the TEMP position restored when the popped
block was TEMP, else NONE. INSERT is DB_InsertPointer reserving a 4-byte alias
slot in VIRTUAL at the given offset.

POINTER records every pointer field the loader looks at, at the moment it
looks: the file offset of the field (NONE when its struct lives in RUNTIME),
the raw value, its kind (``PtrKind``) and the decoded target. For an offset
pointer the target is the decoded (block, offset); for inline data it is the
(block, aligned position) where the data was placed, filled in by the
allocation that follows; NONE when there is no target (null).
"""

from __future__ import annotations

from array import array
from enum import IntEnum

import numpy as np

NONE = 0xFFFFFFFF
WIDTH = 6


class EventKind(IntEnum):
    READ = 0
    STRING = 1
    DEFER = 2
    TAIL = 3
    PUSH = 4
    POP = 5
    ALLOC = 6
    POINTER = 7
    INSERT = 8
    ASSET = 9


class PtrKind(IntEnum):
    """What a pointer field's value meant to the loader."""

    NULL = 0
    #: -1: the data follows inline.
    INLINE = 1
    #: -2: inline, and an alias slot is reserved in VIRTUAL for it.
    INLINE_ALIAS = 2
    #: An offset pointer ((block << 29) | offset) + 1 to data loaded earlier.
    OFFSET = 3
    #: A PS3 "non-zero means inline" field holding a value other than -1.
    NONZERO_INLINE = 4
    #: An asset reference given as an offset pointer to an alias slot.
    ALIAS_REF = 5


class EventLog:
    """Append-only, fixed-width event records."""

    __slots__ = ("words",)

    def __init__(self) -> None:
        self.words = array("I")

    def __len__(self) -> int:
        return len(self.words) // WIDTH

    def append(self, kind: int, a: int = 0, b: int = 0, c: int = 0, d: int = 0, e: int = 0):
        self.words.extend((kind, a, b, c, d, e))

    def record(self, index: int) -> tuple[int, ...]:
        start = index * WIDTH
        return tuple(self.words[start : start + WIDTH])

    def set_word(self, index: int, word: int, value: int) -> None:
        self.words[index * WIDTH + word] = value

    def table(self) -> np.ndarray:
        """All records as an (n, 6) uint32 array (a view, no copy)."""
        return np.frombuffer(self.words, dtype=np.uint32).reshape(-1, WIDTH)

    def of_kind(self, kind: int) -> np.ndarray:
        rows = self.table()
        return rows[rows[:, 0] == kind]

    def counts(self) -> dict[str, int]:
        rows = self.table()
        values, counts = np.unique(rows[:, 0], return_counts=True)
        return {EventKind(int(v)).name: int(n) for v, n in zip(values, counts, strict=True)}

    @property
    def nbytes(self) -> int:
        return self.words.itemsize * len(self.words)
