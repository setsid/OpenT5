"""rawfile (38): a named byte buffer. docs/research/structs-content.md section 3.

RawFile (12): +0 name (string), +4 len (s32, without the final byte),
+8 buffer [nz] (align 16, LS len + 1). Loaders: Ptr 0x243d08, struct 0x243c08.
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass

from opent5.xfile.constants import PTR_INLINE, AssetType, Block
from opent5.xfile.handlers.base import Handler, register
from opent5.xfile.stream import Chunk, XStream
from opent5.xfile.writer import Writer


@dataclass
class RawFile:
    name: str | None
    #: Raw pointer values as stored (-1 inline, 0 null, else an offset pointer).
    name_ptr: int
    #: The len field: the buffer holds len + 1 bytes.
    length: int
    buffer_ptr: int
    #: len + 1 bytes when the buffer is inline, else None.
    buffer: bytes | None

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


@register
class RawFileHandler(Handler):
    asset_type = AssetType.RAWFILE
    header_size = 12
    writable = True

    def read(self, st: XStream, header: Chunk) -> RawFile:
        st.push(Block.VIRTUAL)
        name = st.string(header, 0)
        length = header.s32(4)
        buffer = None
        if st.follows(header, 8, owned=True):
            st.alloc(15)
            buffer = st.load(length + 1).bytes()
        st.pop()
        return RawFile(name, header.u32(0), length, header.u32(8), buffer)

    def write(self, data: RawFile, writer: Writer) -> None:
        writer.u32(data.name_ptr)
        writer.s32(data.length)
        writer.u32(data.buffer_ptr)
        if data.name_ptr == PTR_INLINE:
            writer.string(data.name or "")
        if data.buffer_ptr != 0:
            if data.buffer is None or len(data.buffer) != data.length + 1:
                found = None if data.buffer is None else len(data.buffer)
                raise ValueError(
                    f"rawfile {data.name}: buffer expected {data.length + 1} bytes, found {found}"
                )
            writer.bytes(data.buffer)
