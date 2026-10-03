"""com_map (15): ComWorld, 64 bytes. docs/research/structs-map.md section 4.

+0 name, +4 isInUse, +8 primaryLightCount, +0xc primaryLights [nz] (align 4,
LS 220 each; +0xd8 defName string), +0x20/+0x24 waterCells [nz] (8 each),
+0x38/+0x3c burnableCells [nz] (12 each; per cell +8 [nz] align 1, LS 32).
Loaders: Ptr 0x2457a0, struct 0x2452d0.
"""

from __future__ import annotations

from opent5.xfile.constants import AssetType, Block
from opent5.xfile.handlers.base import Handler, register
from opent5.xfile.stream import Chunk, XStream

PRIMARY_LIGHT_SIZE = 0xDC


@register
class ComWorldHandler(Handler):
    asset_type = AssetType.COM_MAP
    header_size = 0x40

    def read(self, st: XStream, h: Chunk) -> dict:
        st.push(Block.VIRTUAL)
        name = st.string(h, 0)
        count = h.u32(8)
        lights = None
        if st.follows(h, 0xC, owned=True):
            st.alloc(3)
            table = st.load(PRIMARY_LIGHT_SIZE * count)
            lights = [
                {"def_name": st.string(light, 0xD8), "raw": light.bytes()}
                for light in table.items(PRIMARY_LIGHT_SIZE, count)
            ]
        water = None
        if st.follows(h, 0x24, owned=True):
            st.alloc(3)
            water = st.load(8 * h.u32(0x20)).bytes()
        burnable = None
        if st.follows(h, 0x3C, owned=True):
            st.alloc(3)
            n = h.u32(0x38)
            cells = st.load(12 * n)
            burnable = []
            for cell in cells.items(12, n):
                data = None
                if st.follows(cell, 8, owned=True):
                    st.alloc(0)
                    data = st.load(32).bytes()
                burnable.append({"raw": cell.bytes(), "data": data})
        st.pop()
        return {
            "name": name,
            "is_in_use": h.u32(4),
            "primary_light_count": count,
            "primary_lights": lights,
            "water_cells": water,
            "burnable_cells": burnable,
            "header": h.bytes(),
        }
