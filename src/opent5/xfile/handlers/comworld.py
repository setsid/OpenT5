"""com_map (15): ComWorld, 64 bytes. docs/research/structs-map.md section 4.

+0 name, +4 isInUse, +8 primaryLightCount, +0xc primaryLights [nz] (align 4,
LS 220 each; +0xd8 defName string), +0x20/+0x24 waterCells [nz] (8 each),
+0x38/+0x3c burnableCells [nz] (12 each; per cell +8 [nz] align 1, LS 32).
Loaders: Ptr 0x2457a0, struct 0x2452d0.
"""

from __future__ import annotations

from opent5.xfile.constants import AssetType, Block
from opent5.xfile.handlers.base import Handler, array, items, register
from opent5.xfile.stream import Chunk, XStream

PRIMARY_LIGHT_SIZE = 0xDC


@register
class ComWorldHandler(Handler):
    asset_type = AssetType.COM_MAP
    header_size = 0x40

    def body(self, io: XStream, h: Chunk, node: dict) -> None:
        io.push(Block.VIRTUAL)
        io.string(h, 0, node, "name")
        lights = items(
            io, h, 0xC, 3, PRIMARY_LIGHT_SIZE, h.u32(8), node, "primary_lights", owned=True
        )
        for light, element in lights or ():
            io.string(light, 0xD8, element, "def_name")
        array(io, h, 0x24, 3, 8 * h.u32(0x20), node, "water_cells", owned=True)
        for cell, element in (
            items(io, h, 0x3C, 3, 12, h.u32(0x38), node, "burnable_cells", owned=True) or ()
        ):
            array(io, cell, 8, 0, 32, element, "data", owned=True)
        io.pop()
