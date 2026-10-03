"""lightdef (20): GfxLightDef, 16 bytes. docs/research/structs-map.md section 5.

+0 name, +4 attenuation.image (image ref), +8 samplerState, +0xc
lmapLookupStart. Loaders: Ptr 0x24a6d0, struct 0x24a5e8.
"""

from __future__ import annotations

from opent5.xfile.constants import AssetType, Block
from opent5.xfile.handlers.base import Handler, asset_ref, register
from opent5.xfile.stream import Chunk, XStream


@register
class LightDefHandler(Handler):
    asset_type = AssetType.LIGHTDEF
    header_size = 0x10

    def body(self, io: XStream, h: Chunk, node: dict) -> None:
        io.push(Block.VIRTUAL)
        io.string(h, 0, node, "name")
        asset_ref(io, h, 4, AssetType.IMAGE, node, "image")
        io.pop()
