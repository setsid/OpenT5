"""material (6): Material, 128 bytes on PS3. docs/research/structs-content.md section 8.

Load order: name, techniqueSet (asset ref), textureTable [-1] (align 4, LS 16 x
textureCount; then per texture its image ref, or water_t for semantic 11),
constantTable [-1] (align 16, LS 32 x constantCount), stateBitsTable [-1]
(align 4, LS 4 x stateBitsCount; each element: push TEMP, -1/-2 -> align 4,
LS 8, pop). Loaders: Ptr 0x249498, struct 0x248f20, textures 0x248e28, water
0x248ce8, state bits 0x2366d8.

Node: "header", "name", "technique_set", "textures" (elements: "raw", then
"image" or "water"), "constants", "state_bits" (elements: "raw" = the 4-byte
pointer, "bits" = the 8-byte GfxStateBits when inline).
"""

from __future__ import annotations

from opent5.xfile.constants import PTR_INLINE, PTR_INSERT, AssetType, Block
from opent5.xfile.handlers.base import Handler, array, asset_ref, items, register
from opent5.xfile.stream import Chunk, XStream

TS_WATER_MAP = 11


def water_body(io: XStream, w: Chunk, node: dict) -> None:
    """water_t (72): three [nz] float arrays of M x N, then an image ref."""
    size = 4 * w.s32(16) * w.s32(20)
    for off, key in ((4, "H0"), (8, "wTerm"), (12, "wTermSum")):
        array(io, w, off, 3, size, node, key, owned=True)
    asset_ref(io, w, 68, AssetType.IMAGE, node, "image")


def material_body(io: XStream, h: Chunk, node: dict) -> None:
    io.push(Block.VIRTUAL)
    io.string(h, 0, node, "name")
    asset_ref(io, h, 0x70, AssetType.TECHSET, node, "technique_set")
    texture_count, constant_count, state_bits_count = h.u8(0x67), h.u8(0x68), h.u8(0x69)
    for d, texture in items(io, h, 0x74, 3, 16, texture_count, node, "textures") or ():
        if d.u8(7) == TS_WATER_MAP:
            water = None
            if io.follows(d, 12):
                io.alloc(3)
                water = texture.get("water") if not io.reading else {}
                c = io.load(72, water, "raw")
                water_body(io, c, water)
            io.note(texture, "water", water)
        else:
            asset_ref(io, d, 12, AssetType.IMAGE, texture, "image")
    array(io, h, 0x78, 15, 32 * constant_count, node, "constants")
    for e, element in items(io, h, 0x7C, 3, 4, state_bits_count, node, "state_bits") or ():
        raw = io.ref(e, 0)
        io.push(Block.TEMP)
        if raw in (PTR_INLINE, PTR_INSERT):
            io.alloc(3)
            if raw == PTR_INSERT:
                io.insert()
            io.load(8, element, "bits")
        io.pop()
    io.pop()


@register
class MaterialHandler(Handler):
    asset_type = AssetType.MATERIAL
    header_size = 0x80

    def body(self, io: XStream, header: Chunk, node: dict) -> None:
        material_body(io, header, node)
