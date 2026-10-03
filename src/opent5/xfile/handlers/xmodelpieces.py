"""xmodelpieces (0): XModelPieces, 12 bytes. Read from the ELF (t5mp.elf); no
instance in the nine sample zones.

Unlike every other type, the pointer is handled in the Load_XAssetHeader switch
itself (0x2567d0): no TEMP push and no -2 path. 0 -> nothing; -1 -> align 4 in
the current block (0x256900) and the struct loader 0x24e430 with
atStreamStart = 1; any other value -> DB_ConvertOffsetToPointer (0x26add8).

The struct loader pushes no block either: LS 12 (+0 name, +4 numpieces,
+8 pieces); name -1 -> align 1, string (0x24e6b8); pieces != 0 -> align 4, LS
16 x numpieces (0x24e4b4..0x24e4e8); then per XModelPiece (16: +0 model,
+4 offset[3]) the XModel Ptr loader 0x24c998 on +0 (loop unrolled by four,
0x24e4fc..0x24e688).
"""

from __future__ import annotations

from typing import Any

from opent5.xfile.constants import PTR_INLINE, PTR_NULL, AssetType
from opent5.xfile.handlers.base import Handler, asset_ref, items, register
from opent5.xfile.stream import Chunk, XStream

PIECE_SIZE = 16


@register
class XModelPiecesHandler(Handler):
    asset_type = AssetType.XMODELPIECES
    header_size = 12

    def load_ptr(self, io: XStream, raw: int, node: Any = None) -> Any:
        if raw == PTR_NULL:
            return None
        if raw != PTR_INLINE:
            return {"offset_pointer": raw} if io.reading else node
        if io.reading:
            node = {"_t": "XModelPieces"}
        io.trail.append(self.name)
        io.alloc(3)
        self.body(io, io.load(self.header_size, node, "header"), node)
        io.trail.pop()
        return node

    def body(self, io: XStream, h: Chunk, node: dict) -> None:
        io.string(h, 0, node, "name")
        for p, piece in (
            items(io, h, 8, 3, PIECE_SIZE, h.s32(4), node, "pieces", owned=True, kind="XModelPiece")
            or ()
        ):
            asset_ref(io, p, 0, AssetType.XMODEL, piece, "model")
