"""rawfile (38): a named byte buffer. docs/research/structs-content.md section 3.

RawFile (12): +0 name (string), +4 len (s32, without the final byte),
+8 buffer [nz] (align 16, LS len + 1). Loaders: Ptr 0x243d08, struct 0x243c08.
"""

from __future__ import annotations

import struct
import zlib

from opent5.xfile.constants import PTR_INLINE, AssetType, Block
from opent5.xfile.handlers.base import Handler, array, register
from opent5.xfile.stream import Chunk, XStream


class RawFile(dict):
    """Node: "header" (12 bytes), "name", "buffer" (len + 1 bytes or None)."""

    @property
    def name(self) -> str | None:
        return self.get("name")

    @property
    def length(self) -> int:
        return struct.unpack_from(">i", self["header"], 4)[0]

    @property
    def buffer(self) -> bytes | None:
        return self.get("buffer")

    @property
    def compressed(self) -> bool:
        """Scripts (.gsc / .csc) are stored as u32 size, u32 packed size, zlib."""
        b = self.buffer
        return b is not None and len(b) >= 10 and b[8:10] == b"\x78\x9c"

    def contents(self) -> bytes | None:
        """The file as the game sees it: inflated for scripts, without the final byte."""
        if self.buffer is None:
            return None
        if self.compressed:
            size, packed = struct.unpack_from(">II", self.buffer, 0)
            data = zlib.decompress(self.buffer[8 : 8 + packed])
            if len(data) != size:
                raise ValueError(
                    f"rawfile {self.name}: expected {size} inflated bytes, found {len(data)}"
                )
            return data
        return self.buffer[: self.length]

    @classmethod
    def build(cls, name: str, contents: bytes) -> RawFile:
        """A plain (uncompressed) rawfile with an inline name and buffer."""
        header = struct.pack(">IiI", PTR_INLINE, len(contents), PTR_INLINE)
        return cls(header=header, name=name, buffer=bytes(contents) + b"\0")


@register
class RawFileHandler(Handler):
    asset_type = AssetType.RAWFILE
    header_size = 12
    node_type = RawFile

    def body(self, io: XStream, h: Chunk, node: dict) -> None:
        io.push(Block.VIRTUAL)
        io.string(h, 0, node, "name")
        array(io, h, 8, 15, h.s32(4) + 1, node, "buffer", owned=True)
        io.pop()
