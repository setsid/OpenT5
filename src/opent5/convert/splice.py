"""Write a zone whose nodes come from two parses, remapping every offset pointer.

``Rewrite`` (opent5.xfile.remap) re-lays out one parsed zone after its nodes were edited:
every offset pointer is resolved in the original layout, named by what was loaded there
(its identity), and looked up again in the written stream. ``Splice`` does the same when
some nodes come from a second parse (the converted PC world assets): each pointer field
in the written stream is first attributed to the parse its value came from, then
resolved in that parse's layout and trace.

Attribution is by the node that holds the field in the written stream:

- a node that the base parse loaded: the base zone;
- a node that the foreign parse loaded: the foreign zone;
- otherwise (a node the converter built) the source registered with ``origin``;
- ``origin_ranges`` overrides it for byte ranges of one node's bytes (a header that
  mixes words of both, such as the converted GfxWorld header).

Identities of the two parses never collide: nodes are named by their Python identity and
both traces keep their nodes alive. A foreign pointer that names the script strings,
the asset array or a RUNTIME reservation (identities by position, not by node) is
refused, as is any pointer whose target was not written.
"""

from __future__ import annotations

import bisect
import struct
from dataclasses import dataclass, field

import numpy as np

from opent5.xfile.constants import OFFSET_BLOCK_SHIFT, OFFSET_MASK, Block
from opent5.xfile.events import NONE, EventKind
from opent5.xfile.model import parse
from opent5.xfile.remap import (
    Allocation,
    RemapError,
    Rewrite,
    _movable_pointers,
    _replay,
    describe_identity,
    traced_write,
)

READ = EventKind.READ
BASE, FOREIGN = "base", "foreign"


def _loaded_nodes(rw: Rewrite) -> set[int]:
    """ids of every node (and element node) a traced parse stored bytes in."""
    out: set[int] = set()
    for node, _ in rw.trace.holders.values():
        out.add(id(node))
    for elements in rw.trace.lists.values():
        out.update(id(e) for e in elements)
    for ident in rw.trace.ident.values():
        if ident[0] in ("L", "S", "P"):
            out.add(ident[1])
    return out


@dataclass
class SpliceResult:
    content: bytes
    #: Pointer fields resolved through each parse, and how many changed value.
    via: dict[str, int] = field(default_factory=dict)
    rewritten: int = 0
    header: tuple[int, ...] = ()


class Splice:
    """``base`` (a PS3 Rewrite) receives nodes from ``foreign`` (a Rewrite of another
    zone, any platform); edit ``base.xfile`` in place, then ``build``."""

    def __init__(self, base: Rewrite, foreign: Rewrite):
        if base.xfile.platform.endian != ">":
            raise RemapError("splice: the base zone must be a PS3 (big-endian) parse")
        self.base = base
        self.foreign = foreign
        self._sources = {BASE: base, FOREIGN: foreign}
        self._loaded = {BASE: _loaded_nodes(base), FOREIGN: _loaded_nodes(foreign)}
        self._origin: dict[int, str] = {}
        self._ranges: dict[tuple[int, object], list[tuple[int, int, str]]] = {}
        self._moved: dict[tuple[str, int], tuple[int, set]] = {}
        self._keep: list = []

    def moved(self, source: str, old_node: dict, new_node: dict, keys) -> None:
        """Data the ``source`` parse loaded as old_node[key] is written as new_node[key]
        (a stock GfxWorld's images and materials kept under the converted node)."""
        self._moved[(source, id(old_node))] = (id(new_node), set(keys))
        self._keep += [old_node, new_node]

    def _rename(self, source: str, ident: tuple | None) -> tuple | None:
        if ident is None or ident[0] not in ("L", "S"):
            return ident
        found = self._moved.get((source, ident[1]))
        if found is not None and ident[2] in found[1]:
            return (ident[0], found[0], ident[2])
        return ident

    def origin(self, node: dict, source: str) -> None:
        """Pointer fields in ``node`` (one the converter built) hold ``source`` values."""
        self._origin[id(node)] = source
        self._keep.append(node)

    def origin_ranges(self, node: dict, key, ranges: list[tuple[int, int]], source: str) -> None:
        """Bytes [lo, hi) of node[key] hold ``source`` pointer values."""
        self._ranges.setdefault((id(node), key), []).extend((lo, hi, source) for lo, hi in ranges)
        self._keep.append(node)

    def _source(self, node, key, inner: int) -> str:
        for lo, hi, source in self._ranges.get((id(node), key), ()):
            if lo <= inner < hi:
                return source
        found = self._origin.get(id(node))
        if found is not None:
            return found
        in_base = id(node) in self._loaded[BASE]
        in_foreign = id(node) in self._loaded[FOREIGN]
        if in_base and not in_foreign:
            return BASE
        if in_foreign and not in_base:
            return FOREIGN
        raise RemapError(
            f"pointer in {key!r} of node {id(node):#x} (+{inner:#x}): cannot tell which zone "
            "its value comes from; register the node with origin()"
        )

    def build(self, check: bool = True, progress=None) -> SpliceResult:
        written, new_trace = traced_write(self.base.xfile, progress)
        rows = written.log.table()
        new, _, _ = _replay(rows, None)
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
        reads = np.nonzero((rows[:, 0] == READ) & (rows[:, 1] != NONE) & (rows[:, 2] != 0))[0]
        read_at = rows[reads, 1].tolist()
        reads = reads.tolist()

        def holder(at: int):
            k = bisect.bisect_right(read_at, at) - 1
            event = reads[k]
            fp, size = int(rows[event, 1]), int(rows[event, 2])
            if not fp <= at < fp + size or event not in new_trace.holders:
                raise RemapError(f"pointer field at {at:#x}: no traced read holds it")
            node, key = new_trace.holders[event]
            inner = at - fp
            elements = new_trace.lists.get(event)
            if elements:
                k2, sub = divmod(inner, size // len(elements))
                return elements[k2], "raw", sub
            return node, key, inner

        out = bytearray(written.content)
        via = {BASE: 0, FOREIGN: 0}
        positions: dict[int, dict[int, int]] = {}
        rewritten = 0
        layouts = {name: rw.layout() for name, rw in self._sources.items()}
        movable = _movable_pointers(rows)
        for n, j in enumerate(movable):
            if progress is not None and n % 4096 == 0:
                progress("Remapping pointers", n, len(movable))
            _, at, raw, _, block, offset = (int(v) for v in rows[j])
            if block == Block.TEMP:
                raise RemapError(f"pointer field at {at:#x} ({raw:#010x}): target in TEMP")
            node, key, inner0 = holder(at)
            source = self._source(node, key, inner0)
            src = self._sources[source]
            a_old = layouts[source].find(block, offset, NONE)
            ident = src.trace.ident.get(a_old.event)
            if source == FOREIGN and (ident is None or ident[0] not in ("L", "S", "P")):
                raise RemapError(
                    f"pointer field at {at:#x} ({raw:#010x}) in {key!r}: a converted value "
                    f"naming {describe_identity(ident)}, which has no counterpart here"
                )
            ident = self._rename(source, ident)
            a_new = by_ident.get(ident) if ident is not None else None
            if a_new is None or ident in duplicate:
                why = "which was not written" if a_new is None else "which was written twice"
                raise RemapError(
                    f"pointer field at {at:#x} ({raw:#010x}) in {key!r} ({source} value) names "
                    f"{describe_identity(ident)}, {why}"
                )
            inner = offset - a_old.old_start
            old_ids = src.trace.elements.get(a_old.event)
            if old_ids:
                esz = a_old.old_size // len(old_ids)
                k, sub = divmod(inner, esz)
                new_ids = new_trace.elements.get(a_new.event, [])
                if k >= len(old_ids):
                    nk = len(new_ids)
                else:
                    where = positions.get(a_new.event)
                    if where is None:
                        where = positions[a_new.event] = {e: i for i, e in enumerate(new_ids)}
                    nk = where.get(old_ids[k])
                    if nk is None:
                        raise RemapError(
                            f"pointer field at {at:#x} ({raw:#010x}) names element {k} of "
                            f"{describe_identity(ident)}, which was removed"
                        )
                inner = nk * esz + sub
            if inner > a_new.old_size:
                raise RemapError(
                    f"pointer field at {at:#x}: target {inner} bytes into "
                    f"{describe_identity(ident)}, which is now {a_new.old_size} bytes"
                )
            value = (
                (a_new.block << OFFSET_BLOCK_SHIFT) | ((a_new.old_start + inner) & OFFSET_MASK)
            ) + 1
            via[source] += 1
            if value != raw:
                struct.pack_into(">I", out, at, value)
                rewritten += 1
        content = bytes(out)
        if check:
            problems = parse(content, log=False).problems()
            if problems:
                raise RemapError("splice check failed: " + "; ".join(problems[:5]))
        return SpliceResult(content, via, rewritten, struct.unpack_from(">9I", content, 0))
