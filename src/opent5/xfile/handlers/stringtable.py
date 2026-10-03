"""stringtable (39): a CSV table. docs/research/structs-content.md section 4.

StringTable (20): +0 name, +4 columnCount, +8 rowCount, +0xc values [nz]
(align 4, LS 8 x cells: StringTableCell +0 string, +4 hash), +0x10 cellIndex
[nz] (align 2, LS 2 x cells, s16). Load order: name, the cell array, each
cell's string in cell order, cellIndex. Loaders: Ptr 0x2451c8, struct 0x244ec0.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from opent5.xfile.constants import PTR_INLINE, AssetType, Block
from opent5.xfile.handlers.base import Handler, register
from opent5.xfile.stream import Chunk, XStream
from opent5.xfile.writer import Writer


def string_hash(text: str) -> int:
    """The cell hash: 32-bit djb2 over the lower-cased string."""
    h = 5381
    for c in text.lower().encode("latin-1"):
        h = (h * 33 + c) & 0xFFFFFFFF
    return h


@dataclass
class StringTableCell:
    string: str | None
    string_ptr: int
    hash: int


@dataclass
class StringTable:
    name: str | None
    name_ptr: int
    column_count: int
    row_count: int
    values_ptr: int
    cell_index_ptr: int
    cells: list[StringTableCell] | None = None
    cell_index: list[int] | None = field(default=None)

    def cell(self, row: int, column: int) -> str | None:
        if self.cells is None:
            return None
        return self.cells[row * self.column_count + column].string


@register
class StringTableHandler(Handler):
    asset_type = AssetType.STRINGTABLE
    header_size = 20
    writable = True

    def read(self, st: XStream, header: Chunk) -> StringTable:
        st.push(Block.VIRTUAL)
        table = StringTable(
            name=st.string(header, 0),
            name_ptr=header.u32(0),
            column_count=header.s32(4),
            row_count=header.s32(8),
            values_ptr=header.u32(12),
            cell_index_ptr=header.u32(16),
        )
        count = table.column_count * table.row_count
        if st.follows(header, 12, owned=True):
            st.alloc(3)
            values = st.load(8 * count)
            cells = []
            for i in range(count):
                cell = values.sub(8 * i, 8)
                text = st.string(cell, 0)
                cells.append(StringTableCell(text, cell.u32(0), cell.u32(4)))
            table.cells = cells
        if st.follows(header, 16, owned=True):
            st.alloc(1)
            index = st.load(2 * count)
            table.cell_index = [index.s16(2 * i) for i in range(count)]
        st.pop()
        return table

    def write(self, data: StringTable, writer: Writer) -> None:
        count = data.column_count * data.row_count
        writer.u32(data.name_ptr)
        writer.s32(data.column_count)
        writer.s32(data.row_count)
        writer.u32(data.values_ptr)
        writer.u32(data.cell_index_ptr)
        if data.name_ptr == PTR_INLINE:
            writer.string(data.name or "")
        if data.values_ptr != 0:
            cells = data.cells or []
            if len(cells) != count:
                raise ValueError(
                    f"stringtable {data.name}: expected {count} cells, found {len(cells)}"
                )
            for cell in cells:
                writer.u32(cell.string_ptr)
                writer.u32(cell.hash)
            for cell in cells:
                if cell.string_ptr == PTR_INLINE:
                    writer.string(cell.string or "")
        if data.cell_index_ptr != 0:
            index = data.cell_index or []
            if len(index) != count:
                raise ValueError(
                    f"stringtable {data.name}: expected {count} index entries, found {len(index)}"
                )
            for value in index:
                writer.s16(value)
