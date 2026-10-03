"""image (10): GfxImage, 0x70 bytes on PS3. docs/research/structs-content.md
section 7 and textures.md section 2.

Load order: header (TEMP), push VIRTUAL, name (+0x68), then the pixels: push
LARGE or PHYSICAL by the GCM location byte (+0xe == 1 -> LARGE), or their
deferred counterparts LARGE_RUNTIME / PHYSICAL_RUNTIME when the byte at +0x1b
is set; if +0x2c != 0: align 128, LS +0x1c bytes; pop, pop.
Loaders: Ptr 0x248b40 (struct inline at 0x248be8), pixels 0x2365e8.

Node: "header", "name", "pixels" (bytes when inline, DeferredData when in the
deferred tail, absent / None when streamed from a .pak); decoded header fields
are notes.
"""

from __future__ import annotations

from opent5.xfile.constants import AssetType, Block
from opent5.xfile.handlers.base import Handler, register
from opent5.xfile.stream import Chunk, XStream


def pixel_block(location: int, deferred: int) -> int:
    if deferred:
        return Block.LARGE_RUNTIME if location == 1 else Block.PHYSICAL_RUNTIME
    return Block.LARGE if location == 1 else Block.PHYSICAL


def image_body(io: XStream, h: Chunk, node: dict) -> None:
    io.push(Block.VIRTUAL)
    io.string(h, 0x68, node, "name")
    location, deferred = h.u8(0xE), h.u8(0x1B)
    io.push(pixel_block(location, deferred))
    if io.follows(h, 0x2C, owned=True):
        io.alloc(127)
        io.reserve(h.u32(0x1C), node, "pixels")
    else:
        io.note(node, "pixels", None)
    io.pop()
    io.pop()
    if io.reading:
        node.update(
            format=h.u8(0x0),
            mipmap=h.u8(0x1),
            dimension=h.u8(0x2),
            cubemap=h.u8(0x3),
            remap=h.u32(0x4),
            width=h.u16(0x8),
            height=h.u16(0xA),
            depth=h.u16(0xC),
            location=location,
            pitch=h.u32(0x10),
            offset=h.u32(0x14),
            map_type=h.u8(0x18),
            semantic=h.u8(0x19),
            category=h.u8(0x1A),
            delay_load_pixels=deferred,
            size=h.u32(0x1C),
            hash=h.u32(0x6C),
        )


@register
class ImageHandler(Handler):
    asset_type = AssetType.IMAGE
    header_size = 0x70

    def body(self, io: XStream, header: Chunk, node: dict) -> None:
        image_body(io, header, node)
