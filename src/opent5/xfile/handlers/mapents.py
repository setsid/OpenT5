"""map_ents (18): MapEnts, 12 bytes. docs/research/structs-map.md section 7.

+0 name, +4 entityString (align 1, LS numEntityChars, NUL included), +8
numEntityChars. Usually loaded inline by the clipMap. Loaders: Ptr 0x243f18,
struct 0x243e20.
"""

from __future__ import annotations

from opent5.xfile.constants import AssetType, Block
from opent5.xfile.handlers.base import Handler, array, register
from opent5.xfile.stream import Chunk, XStream


@register
class MapEntsHandler(Handler):
    asset_type = AssetType.MAP_ENTS
    header_size = 0xC

    def read(self, st: XStream, h: Chunk) -> dict:
        st.push(Block.VIRTUAL)
        name = st.string(h, 0)
        text = array(st, h, 4, 0, h.u32(8))
        st.pop()
        return {
            "name": name,
            "num_entity_chars": h.u32(8),
            "entity_string": None if text is None else text.bytes(),
        }
