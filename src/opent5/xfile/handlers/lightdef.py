"""lightdef (20): GfxLightDef, 16 bytes. docs/research/structs-map.md section 5.

+0 name, +4 attenuation.image (image ref), +8 samplerState, +0xc
lmapLookupStart. Loaders: Ptr 0x24a6d0, struct 0x24a5e8.
"""

from __future__ import annotations

from opent5.xfile.constants import AssetType, Block
from opent5.xfile.handlers.base import Handler, asset_ref, register
from opent5.xfile.stream import Chunk, XStream


def read_lightdef(st: XStream, h: Chunk) -> dict:
    st.push(Block.VIRTUAL)
    name = st.string(h, 0)
    image = asset_ref(st, h, 4, AssetType.IMAGE)
    st.pop()
    return {
        "name": name,
        "image": image,
        "sampler_state": h.u8(8),
        "lmap_lookup_start": h.s32(0xC),
    }


@register
class LightDefHandler(Handler):
    asset_type = AssetType.LIGHTDEF
    header_size = 0x10

    def read(self, st: XStream, header: Chunk) -> dict:
        return read_lightdef(st, header)
