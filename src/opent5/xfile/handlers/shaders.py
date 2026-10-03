"""pixelshader (7) and vertexshader (8), PS3 only. docs/research/structs-content.md section 9.

MaterialVertexShader (16): +0 name, +4 program [-1] (align 16, LS 4 x u16 at
+0xe). MaterialPixelShader (12): +0 name, +4 program [-1] (align 16, LS 4 x
u16 at +0xa). Programs start with VS0u / PS0u. Loaders: vertex Ptr 0x247f28
(program 0x2368e0), pixel Ptr 0x244178 (struct 0x244038).
"""

from __future__ import annotations

from opent5.xfile.constants import AssetType, Block
from opent5.xfile.handlers.base import Handler, register
from opent5.xfile.stream import Chunk, XStream


def read_shader(st: XStream, h: Chunk, size_at: int) -> dict:
    st.push(Block.VIRTUAL)
    name = st.string(h, 0)
    words = h.u16(size_at)
    program = None
    if st.follows(h, 4):
        st.alloc(15)
        program = st.load(4 * words).bytes()
    st.pop()
    return {"name": name, "program_words": words, "program": program, "header": h.bytes()}


@register
class PixelShaderHandler(Handler):
    asset_type = AssetType.PIXELSHADER
    header_size = 12

    def read(self, st: XStream, header: Chunk) -> dict:
        return read_shader(st, header, 10)


@register
class VertexShaderHandler(Handler):
    asset_type = AssetType.VERTEXSHADER
    header_size = 16

    def read(self, st: XStream, header: Chunk) -> dict:
        return read_shader(st, header, 14)
