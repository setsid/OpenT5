"""localize (25): one localised string. docs/research/structs-content.md section 5.

LocalizeEntry (8): +0 value (string), +4 name (string); value is loaded
first. Loader: Ptr 0x24b700 with the struct loader inline.
"""

from __future__ import annotations

import struct

from opent5.xfile.constants import PTR_INLINE, AssetType, Block
from opent5.xfile.handlers.base import Handler, register
from opent5.xfile.stream import Chunk, XStream


class LocalizeEntry(dict):
    """Node: "header" (8 bytes), "value", "name"."""

    @property
    def name(self) -> str | None:
        return self.get("name")

    @property
    def value(self) -> str | None:
        return self.get("value")

    @classmethod
    def build(cls, value: str, name: str) -> LocalizeEntry:
        return cls(header=struct.pack(">II", PTR_INLINE, PTR_INLINE), value=value, name=name)


@register
class LocalizeHandler(Handler):
    kind = "LocalizeEntry"
    asset_type = AssetType.LOCALIZE
    header_size = 8
    node_type = LocalizeEntry

    def body(self, io: XStream, h: Chunk, node: dict) -> None:
        io.push(Block.VIRTUAL)
        io.string(h, 0, node, "value")
        io.string(h, 4, node, "name")
        io.pop()
