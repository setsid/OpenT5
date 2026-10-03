"""image (10): GfxImage, 0x70 bytes on PS3. docs/research/structs-content.md
section 7 and textures.md section 2.

Load order: header (TEMP), push VIRTUAL, name (+0x68), then the pixels: push
LARGE or PHYSICAL by the GCM location byte (+0xe == 1 -> LARGE), or their
deferred counterparts LARGE_RUNTIME / PHYSICAL_RUNTIME when the byte at +0x1b
is set; if +0x2c != 0: align 128, LS +0x1c bytes; pop, pop.
Loaders: Ptr 0x248b40 (struct inline at 0x248be8), pixels 0x2365e8.
"""

from __future__ import annotations

from opent5.xfile.constants import AssetType, Block
from opent5.xfile.handlers.base import Handler, register
from opent5.xfile.stream import Chunk, DeferredData, XStream


def pixel_block(location: int, deferred: int) -> int:
    if deferred:
        return Block.LARGE_RUNTIME if location == 1 else Block.PHYSICAL_RUNTIME
    return Block.LARGE if location == 1 else Block.PHYSICAL


def read_image(st: XStream, h: Chunk) -> dict:
    st.push(Block.VIRTUAL)
    name = st.string(h, 0x68)
    location, deferred = h.u8(0xE), h.u8(0x1B)
    st.push(pixel_block(location, deferred))
    pixels: bytes | DeferredData | None = None
    if st.follows(h, 0x2C, owned=True):
        st.alloc(127)
        got = st.reserve(h.u32(0x1C))
        pixels = got.bytes() if isinstance(got, Chunk) else got
    st.pop()
    st.pop()
    return {
        "name": name,
        "format": h.u8(0x0),
        "mipmap": h.u8(0x1),
        "dimension": h.u8(0x2),
        "cubemap": h.u8(0x3),
        "remap": h.u32(0x4),
        "width": h.u16(0x8),
        "height": h.u16(0xA),
        "depth": h.u16(0xC),
        "location": location,
        "pitch": h.u32(0x10),
        "offset": h.u32(0x14),
        "map_type": h.u8(0x18),
        "semantic": h.u8(0x19),
        "category": h.u8(0x1A),
        "delay_load_pixels": deferred,
        "size": h.u32(0x1C),
        "hash": h.u32(0x6C),
        "header": h.bytes(),
        #: bytes (inline), DeferredData (in the tail) or None (streamed from a .pak).
        "pixels": pixels,
    }


@register
class ImageHandler(Handler):
    asset_type = AssetType.IMAGE
    header_size = 0x70

    def read(self, st: XStream, header: Chunk) -> dict:
        return read_image(st, header)
