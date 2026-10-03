"""stringtable (39): a CSV table. docs/research/structs-content.md section 4.

StringTable (20): +0 name, +4 columnCount, +8 rowCount, +0xc values [nz]
(align 4, LS 8 x cells: StringTableCell +0 string, +4 hash), +0x10 cellIndex
[nz] (align 2, LS 2 x cells, s16). Load order: name, the cell array, each
cell's string in cell order, cellIndex. Loaders: Ptr 0x2451c8, struct 0x244ec0.
"""

from __future__ import annotations

import struct

from opent5.xfile.constants import PTR_INLINE, AssetType, Block
from opent5.xfile.handlers.base import Handler, array, items, register
from opent5.xfile.stream import Chunk, XStream


def string_hash(text: str) -> int:
    """The cell hash: 32-bit djb2 over the lower-cased string."""
    h = 5381
    for c in text.lower().encode("latin-1"):
        h = (h * 33 + c) & 0xFFFFFFFF
    return h


class StringTable(dict):
    """Node: "header" (20 bytes), "name", "cells" (elements {"raw": 8 bytes,
    "string"}), "cell_index" (2 bytes per cell)."""

    @property
    def name(self) -> str | None:
        return self.get("name")

    @property
    def column_count(self) -> int:
        return struct.unpack_from(">i", self["header"], 4)[0]

    @property
    def row_count(self) -> int:
        return struct.unpack_from(">i", self["header"], 8)[0]

    def cell(self, row: int, column: int) -> str | None:
        cells = self.get("cells")
        if cells is None:
            return None
        return cells[row * self.column_count + column]["string"]

    def hashes(self) -> list[int]:
        return [struct.unpack_from(">I", c["raw"], 4)[0] for c in self.get("cells") or []]

    def index(self) -> list[int]:
        data = self.get("cell_index") or b""
        return [v for (v,) in struct.iter_unpack(">h", data)]

    @classmethod
    def build(cls, name: str, rows: list[list[str]]) -> StringTable:
        """A table with inline strings, hashes and a cellIndex sorted by signed hash."""
        columns = len(rows[0]) if rows else 0
        texts = [text for row in rows for text in row]
        if any(len(row) != columns for row in rows):
            raise ValueError(f"stringtable {name}: rows must all have {columns} columns")
        hashes = [string_hash(t) for t in texts]
        cells = [
            {"raw": struct.pack(">II", PTR_INLINE, h), "string": t}
            for t, h in zip(texts, hashes, strict=True)
        ]
        signed = [h - (1 << 32) if h & 0x80000000 else h for h in hashes]
        order = sorted(range(len(texts)), key=lambda i: signed[i])
        header = struct.pack(">IiiII", PTR_INLINE, columns, len(rows), PTR_INLINE, PTR_INLINE)
        return cls(
            header=header,
            name=name,
            cells=cells,
            cell_index=b"".join(struct.pack(">h", i) for i in order),
        )


@register
class StringTableHandler(Handler):
    asset_type = AssetType.STRINGTABLE
    header_size = 20
    node_type = StringTable

    def body(self, io: XStream, h: Chunk, node: dict) -> None:
        io.push(Block.VIRTUAL)
        io.string(h, 0, node, "name")
        count = h.s32(4) * h.s32(8)
        cells = items(io, h, 12, 3, 8, count, node, "cells", owned=True)
        for cell, element in cells or ():
            io.string(cell, 0, element, "string")
        array(io, h, 16, 1, 2 * count, node, "cell_index", owned=True)
        io.pop()
