"""pixelshader (7) and vertexshader (8), PS3 only. docs/research/structs-content.md section 9.

MaterialVertexShader (16): +0 name, +4 program [-1] (align 16, LS 4 x u16 at
+0xe). MaterialPixelShader (12): +0 name, +4 program [-1] (align 16, LS 4 x
u16 at +0xa). Programs start with VS0u / PS0u. Loaders: vertex Ptr 0x247f28
(program 0x2368e0), pixel Ptr 0x244178 (struct 0x244038).

Node: "header", "name", "program" (bytes).
"""

from __future__ import annotations

from opent5.xfile.constants import AssetType, Block
from opent5.xfile.handlers.base import Handler, array, register
from opent5.xfile.stream import Chunk, XStream


def shader_body(io: XStream, h: Chunk, node: dict, size_at: int) -> None:
    io.push(Block.VIRTUAL)
    io.string(h, 0, node, "name")
    array(io, h, 4, 15, 4 * h.u16(size_at), node, "program")
    io.pop()


@register
class PixelShaderHandler(Handler):
    asset_type = AssetType.PIXELSHADER
    header_size = 12

    def body(self, io: XStream, header: Chunk, node: dict) -> None:
        shader_body(io, header, node, 10)


@register
class VertexShaderHandler(Handler):
    asset_type = AssetType.VERTEXSHADER
    header_size = 16

    def body(self, io: XStream, header: Chunk, node: dict) -> None:
        shader_body(io, header, node, 14)
