"""The T5 PS3 ``.pak`` container ("pak2"): streamed image parts and sound banks.

docs/research/pak.md has the evidence for every field. All values are big-endian:

    0x00  'pak2'
    0x04  u32 build time (unix seconds)
    0x08  u32 build value (0 sound / images_low, 1 MP maps, 2 SP and zombie maps, 3 ui_mp,
          7 common, 0x11 img_patch); the loader copies it to a global and nothing reads it
    0x0c  u32 entry count
    0x10  u32 sector size (0x800 in every pak; the loader uses a fixed 0x800 shift)
    0x14  u32 header bytes = round_up(0x1c + 4 * count, sector)
    0x18  u32 0
    0x1c  u32[count] start sector of each entry; zero padding to the header size

Entries are raw bytes (texture parts exactly as the GPU reads them; sound data) with no
per-entry header, size or checksum. Each starts on a sector boundary and is zero-padded to
the next one. Sizes are not stored: the reader of an entry knows how many bytes it wants
(for images, the GfxImage part record). Here an entry's *capacity* is the span from its
start to the next entry in file order (or the end of the file), padding included, so a
read-then-write reproduces the file byte for byte even where the table is not in
ascending order (img_patch.pak).

``Pak.open(path)`` reads the header only; entry bytes are read on demand. ``replace``
records new bytes for an entry and ``append`` adds an entry after the last one;
``chunks`` / ``write`` produce the file with the edits, laying entries out again in their
original file order when a size changes (appended entries last).
"""

from __future__ import annotations

import hashlib
import struct
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

MAGIC = b"pak2"
HEADER_FIXED = 0x1C
SECTOR = 0x800
#: The loader forms an entry's file offset as (start << 11) in 32 bits (t5mp.elf 0x3c9074),
#: so a start sector must stay below 2**21.
MAX_START = 1 << 21
_COPY_BLOCK = 1 << 22


class PakError(ValueError):
    """A .pak that cannot be read or written; says what was expected, what was found, and
    the offset."""


def round_up(n: int, unit: int = SECTOR) -> int:
    return (n + unit - 1) // unit * unit


def header_bytes(count: int, sector: int = SECTOR) -> int:
    """The header size a pak with ``count`` entries has (every pak on the disc agrees)."""
    return round_up(HEADER_FIXED + 4 * count, sector)


@dataclass(frozen=True)
class PakEntry:
    index: int
    start: int  # sector
    offset: int  # bytes
    capacity: int  # bytes to the next entry in file order (or to the end of the file)


class Pak:
    """One .pak, read lazily from a file (``open``) or from bytes (``from_bytes``)."""

    def __init__(self, head: bytes, size: int, source: Path | bytes, name: str = "pak"):
        self.name = name
        self._source = source
        self._file: BinaryIO | None = None
        self.size = size
        if len(head) < HEADER_FIXED:
            raise PakError(
                f"{name}: expected at least {HEADER_FIXED} header bytes, found {len(head)}"
            )
        (
            magic,
            self.timestamp,
            self.field08,
            self.count,
            self.sector,
            self.header_size,
            self.reserved,
        ) = struct.unpack_from(">4sIIIIII", head)
        if magic != MAGIC:
            raise PakError(f"{name}: expected magic {MAGIC!r} at 0x0, found {magic!r}")
        if self.sector == 0 or self.header_size % self.sector:
            raise PakError(
                f"{name}: expected a header size (0x14) that is a multiple of the sector size "
                f"(0x10 = {self.sector:#x}), found {self.header_size:#x}"
            )
        table_end = HEADER_FIXED + 4 * self.count
        if table_end > self.header_size or self.header_size > size:
            raise PakError(
                f"{name}: expected {self.count} start words (to {table_end:#x}) inside the "
                f"header ({self.header_size:#x}) inside the file ({size:#x})"
            )
        if len(head) < self.header_size:
            head = self._read_at(0, self.header_size)
        self.starts: tuple[int, ...] = struct.unpack_from(f">{self.count}I", head, HEADER_FIXED)
        #: Bytes between the table and the end of the header (zero in every pak seen).
        self.header_tail = bytes(head[table_end : self.header_size])
        self._layout()
        self._edits: dict[int, bytes] = {}
        #: Entries added by ``append``: index count, count + 1, ...
        self._added: list[bytes] = []

    # -- opening -------------------------------------------------------------------------

    @classmethod
    def open(cls, path: str | Path) -> Pak:
        path = Path(path)
        size = path.stat().st_size
        with open(path, "rb") as f:
            head = f.read(SECTOR)
        return cls(head, size, path, path.name)

    @classmethod
    def from_bytes(cls, data: bytes, name: str = "pak") -> Pak:
        data = bytes(data)
        return cls(data[:SECTOR], len(data), data, name)

    def close(self) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None

    def __enter__(self) -> Pak:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    @property
    def path(self) -> Path | None:
        return self._source if isinstance(self._source, Path) else None

    def _read_at(self, offset: int, size: int) -> bytes:
        if isinstance(self._source, bytes):
            return self._source[offset : offset + size]
        if self._file is None:
            self._file = open(self._source, "rb")  # noqa: SIM115 (closed in close())
        self._file.seek(offset)
        return self._file.read(size)

    # -- layout --------------------------------------------------------------------------

    def _layout(self) -> None:
        """File order, capacities, and entries that share a start (none on the disc)."""
        sector = self.sector
        order = sorted(range(self.count), key=lambda i: (self.starts[i], i))
        self.order: list[int] = order
        self.alias: dict[int, int] = {}  # index -> the first index with the same start
        cap: dict[int, int] = {}
        first = self.starts[order[0]] * sector if order else self.size
        if order and first < self.header_size:
            raise PakError(
                f"{self.name}: entry {order[0]} starts at {first:#x}, inside the header "
                f"(which ends at {self.header_size:#x})"
            )
        #: Bytes between the header and the first entry (none in any pak seen).
        self.lead = first - self.header_size
        for k, i in enumerate(order):
            off = self.starts[i] * sector
            if off > self.size:
                raise PakError(
                    f"{self.name}: entry {i} starts at {off:#x}, past the end of the file "
                    f"({self.size:#x}); the table is at {HEADER_FIXED + 4 * i:#x}"
                )
            if k and self.starts[order[k - 1]] == self.starts[i]:
                self.alias[i] = self.alias.get(order[k - 1], order[k - 1])
                cap[i] = 0
                continue
            nxt = next(
                (self.starts[j] for j in order[k + 1 :] if self.starts[j] != self.starts[i]), None
            )
            end = nxt * sector if nxt is not None else self.size
            cap[i] = end - off
        self.capacities = cap
        if self.alias:
            for i, owner in self.alias.items():
                cap[i] = cap[owner]

    def entry(self, index: int) -> PakEntry:
        self._check_index(index)
        start = self.starts[index]
        return PakEntry(index, start, start * self.sector, self.capacities[index])

    def entries(self) -> list[PakEntry]:
        return [self.entry(i) for i in range(self.count)]

    def offset(self, index: int) -> int:
        self._check_index(index)
        return self.starts[index] * self.sector

    def _check_index(self, index: int) -> None:
        if not 0 <= index < self.count:
            raise PakError(
                f"{self.name}: expected an entry index below {self.count}, found {index}"
            )

    def read(self, index: int, size: int | None = None) -> bytes:
        """An entry's bytes as they are now (edits included): ``size`` bytes, or its whole
        capacity (padding included) when size is None."""
        self._check_index(index)
        index = self.alias.get(index, index)
        if index in self._edits:
            data = self._edits[index]
            if size is not None and size > len(data):
                raise PakError(
                    f"{self.name} entry {index}: expected {size} bytes, the new data has "
                    f"{len(data)}"
                )
            return data if size is None else data[:size]
        cap = self.capacities[index]
        want = cap if size is None else size
        if size is not None and size > cap:
            raise PakError(
                f"{self.name} entry {index}: expected {size} bytes at {self.offset(index):#x}, "
                f"the entry holds {cap} (to the next entry or the end of the file)"
            )
        data = self._read_at(self.offset(index), want)
        if len(data) != want:
            raise PakError(
                f"{self.name} entry {index}: expected {want} bytes at {self.offset(index):#x}, "
                f"found {len(data)}"
            )
        return data

    # -- editing -------------------------------------------------------------------------

    def replace(self, index: int, data: bytes) -> None:
        """New bytes for an entry (any length; zero-padded to the sector when written)."""
        self._check_index(index)
        if index in self.alias or index in self.alias.values():
            raise PakError(
                f"{self.name} entry {index}: shares its start with another entry; "
                "replacing it would change both"
            )
        self._edits[index] = bytes(data)

    def revert(self, index: int) -> None:
        self._edits.pop(index, None)

    def append(self, data: bytes) -> int:
        """Add an entry after the last one (in the table and in the file); returns its
        index. The table grows by one word, so the header grows by a sector whenever the
        table crosses a sector boundary (every entry then moves by one sector)."""
        self._added.append(bytes(data))
        return self.count + len(self._added) - 1

    @property
    def edits(self) -> dict[int, bytes]:
        out = dict(self._edits)
        out.update({self.count + k: d for k, d in enumerate(self._added)})
        return out

    @property
    def new_count(self) -> int:
        return self.count + len(self._added)

    def header_size_for(self, count: int) -> int:
        """Header bytes with ``count`` entries: the source's own while the table still
        fits it, else the rule every pak follows (round_up(0x1c + 4 * count, sector))."""
        return max(self.header_size, header_bytes(count, self.sector))

    def new_header_tail(self) -> bytes:
        """The bytes after the start table once entries are appended: the source's own,
        less the words the table now covers, then zeros."""
        count = self.new_count
        size = self.header_size_for(count) - (HEADER_FIXED + 4 * count)
        skip = 4 * len(self._added)
        return (self.header_tail + bytes(skip + size))[skip : skip + size]

    def _new_capacity(self, index: int) -> int:
        if index in self.alias:
            return 0
        if index not in self._edits:
            return self.capacities[index]
        need = round_up(len(self._edits[index]), self.sector)
        old = self.capacities[index]
        # Keep the old span when the new bytes take the same sectors (no relayout).
        return old if need == round_up(old, self.sector) else need

    def new_starts(self) -> list[int]:
        """Start sector of every entry after the edits (original file order kept)."""
        starts = list(self.starts) + [0] * len(self._added)
        cursor = self.header_size_for(self.new_count) + self.lead
        for i in self.order:
            if i in self.alias:
                continue
            if cursor % self.sector:
                raise PakError(
                    f"{self.name}: entry {i} would start at {cursor:#x}, not on a sector boundary"
                )
            starts[i] = cursor // self.sector
            if starts[i] >= MAX_START:
                raise PakError(
                    f"{self.name}: entry {i} would start at sector {starts[i]:#x}; the loader "
                    f"addresses at most {MAX_START:#x} sectors"
                )
            cursor += self._new_capacity(i)
        for i, owner in self.alias.items():
            starts[i] = starts[owner]
        for k, data in enumerate(self._added):
            starts[self.count + k] = cursor // self.sector
            if starts[self.count + k] >= MAX_START:
                raise PakError(
                    f"{self.name}: appended entry {self.count + k} would start at sector "
                    f"{starts[self.count + k]:#x}; the loader addresses at most {MAX_START:#x}"
                )
            cursor += round_up(len(data), self.sector)
        return starts

    def header(self, starts: list[int] | None = None) -> bytes:
        starts = self.new_starts() if starts is None else starts
        count = self.new_count
        size = self.header_size_for(count)
        head = struct.pack(
            ">4sIIIIII",
            MAGIC,
            self.timestamp,
            self.field08,
            count,
            self.sector,
            size,
            self.reserved,
        )
        head += struct.pack(f">{count}I", *starts) + self.new_header_tail()
        if len(head) != size:
            raise PakError(f"{self.name}: expected a {size:#x}-byte header, built {len(head):#x}")
        return head

    def chunks(self) -> Iterator[bytes]:
        """The file's bytes with the edits, in pieces (entries are copied, not held)."""
        yield self.header()
        if self.lead:
            yield self._read_at(self.header_size, self.lead)
        for i in self.order:
            if i in self.alias:
                continue
            cap = self._new_capacity(i)
            if i in self._edits:
                data = self._edits[i]
                yield data + bytes(cap - len(data))
                continue
            off, left = self.offset(i), cap
            while left:
                n = min(left, _COPY_BLOCK)
                piece = self._read_at(off, n)
                if len(piece) != n:
                    raise PakError(
                        f"{self.name} entry {i}: expected {n} bytes at {off:#x}, found {len(piece)}"
                    )
                yield piece
                off += n
                left -= n
        for data in self._added:
            yield data + bytes(round_up(len(data), self.sector) - len(data))

    def to_bytes(self) -> bytes:
        return b"".join(self.chunks())

    def write(self, path: str | Path) -> tuple[int, str]:
        """Write the file (with edits) to ``path``; returns (bytes, sha1). Refuses to write
        over the file it was read from."""
        path = Path(path)
        if self.path is not None and path.resolve() == self.path.resolve():
            raise PakError(f"{path}: expected a new path, found the source pak itself")
        sha = hashlib.sha1()
        n = 0
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as f:
            for piece in self.chunks():
                f.write(piece)
                sha.update(piece)
                n += len(piece)
        return n, sha.hexdigest()


def compare(original: Pak, written: Pak, edited: dict[int, bytes] | None = None) -> list[str]:
    """Problems found comparing a written pak with its source: header fields, entry count,
    every entry not edited byte-identical (its whole span), every edited entry holding the
    new bytes followed by zero padding. Indices from the source's count up in ``edited``
    are entries appended after the last one."""
    edited = edited or {}
    problems: list[str] = []
    added = sorted(i for i in edited if i >= original.count)
    if added != list(range(original.count, original.count + len(added))):
        return [
            f"{written.name}: appended entries {added} do not follow entry {original.count - 1}"
        ]
    count = original.count + len(added)
    want = {
        "timestamp": original.timestamp,
        "field08": original.field08,
        "count": count,
        "sector": original.sector,
        "header_size": original.header_size_for(count),
        "reserved": original.reserved,
    }
    for field, a in want.items():
        b = getattr(written, field)
        if a != b:
            problems.append(f"{written.name}: header {field}: expected {a:#x}, found {b:#x}")
    if problems:
        return problems
    tail = written.header_tail
    if added:
        expected_tail = (original.header_tail + bytes(len(tail) + 4 * len(added)))[
            4 * len(added) : 4 * len(added) + len(tail)
        ]
    else:
        expected_tail = original.header_tail
    if tail != expected_tail:
        problems.append(f"{written.name}: the bytes after the start table differ from the source")
    for i in range(count):
        if i in edited:
            data = edited[i]
            got = written.read(i)
            if got[: len(data)] != data:
                problems.append(f"{written.name} entry {i}: does not hold the new bytes")
            elif any(got[len(data) :]):
                problems.append(
                    f"{written.name} entry {i}: padding after the new bytes is not zero"
                )
            continue
        if original.capacities[i] != written.capacities[i]:
            problems.append(
                f"{written.name} entry {i}: expected {original.capacities[i]:#x} bytes as in the "
                f"source, found {written.capacities[i]:#x}"
            )
        elif original.read(i) != written.read(i):
            problems.append(f"{written.name} entry {i}: bytes differ from the source")
        if len(problems) >= 50:
            break
    return problems


def sha1_of(path: str | Path) -> str:
    sha = hashlib.sha1()
    with open(path, "rb") as f:
        while piece := f.read(_COPY_BLOCK):
            sha.update(piece)
    return sha.hexdigest()
