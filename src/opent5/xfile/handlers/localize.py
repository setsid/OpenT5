"""localize (25): one localised string. docs/research/structs-content.md section 5.

LocalizeEntry (8): +0 value (string), +4 name (string); value is loaded
first. Loader: Ptr 0x24b700 with the struct loader inline.
"""

from __future__ import annotations

from dataclasses import dataclass

from opent5.xfile.constants import PTR_INLINE, AssetType, Block
from opent5.xfile.handlers.base import Handler, register
from opent5.xfile.stream import Chunk, XStream
from opent5.xfile.writer import Writer


@dataclass
class LocalizeEntry:
    value: str | None
    value_ptr: int
    name: str | None
    name_ptr: int


@register
class LocalizeHandler(Handler):
    asset_type = AssetType.LOCALIZE
    header_size = 8
    writable = True

    def read(self, st: XStream, header: Chunk) -> LocalizeEntry:
        st.push(Block.VIRTUAL)
        value = st.string(header, 0)
        name = st.string(header, 4)
        st.pop()
        return LocalizeEntry(value, header.u32(0), name, header.u32(4))

    def write(self, data: LocalizeEntry, writer: Writer) -> None:
        writer.u32(data.value_ptr)
        writer.u32(data.name_ptr)
        if data.value_ptr == PTR_INLINE:
            writer.string(data.value or "")
        if data.name_ptr == PTR_INLINE:
            writer.string(data.name or "")
