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
from opent5.xfile.handlers.base import Handler, asset_ref, register
from opent5.xfile.stream import Chunk, XStream

PIECE_SIZE = 16


@register
class XModelPiecesHandler(Handler):
    asset_type = AssetType.XMODELPIECES
    header_size = 12

    def load_ptr(self, st: XStream, raw: int) -> Any:
        if raw == PTR_NULL:
            return None
        if raw != PTR_INLINE:
            return {"offset_pointer": raw}
        st.trail.append(self.name)
        st.alloc(3)
        data = self.read(st, st.load(self.header_size))
        st.trail.pop()
        return data

    def read(self, st: XStream, h: Chunk) -> dict:
        name = st.string(h, 0)
        count = h.s32(4)
        pieces = None
        if st.follows(h, 8, owned=True):
            st.alloc(3)
            table = st.load(PIECE_SIZE * count)
            pieces = [
                {
                    "model": asset_ref(st, p, 0, AssetType.XMODEL),
                    "offset": (p.f32(4), p.f32(8), p.f32(12)),
                }
                for p in table.items(PIECE_SIZE, count)
            ]
        return {"name": name, "num_pieces": count, "pieces": pieces}
