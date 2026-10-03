"""Byte writer for the handlers' write side.

A handler's ``write`` emits one asset's stream bytes in the loader's order:
the header struct, then everything it owns. Alignment never reaches the file
(the stream is packed), so the writer only appends bytes; tracking block
positions for a re-layout is the remap's job, not this one's.
"""

from __future__ import annotations

import struct


class Writer:
    def __init__(self) -> None:
        self.out = bytearray()

    def __len__(self) -> int:
        return len(self.out)

    def getvalue(self) -> bytes:
        return bytes(self.out)

    def bytes(self, data: bytes | bytearray | memoryview) -> None:
        self.out += data

    def u8(self, value: int) -> None:
        self.out.append(value & 0xFF)

    def u16(self, value: int) -> None:
        self.out += struct.pack(">H", value & 0xFFFF)

    def s16(self, value: int) -> None:
        self.out += struct.pack(">h", value)

    def u32(self, value: int) -> None:
        self.out += struct.pack(">I", value & 0xFFFFFFFF)

    def s32(self, value: int) -> None:
        self.out += struct.pack(">i", value)

    def string(self, text: str) -> None:
        """A NUL-terminated string; text is Latin-1, as the parser decodes it."""
        self.out += text.encode("latin-1") + b"\0"
