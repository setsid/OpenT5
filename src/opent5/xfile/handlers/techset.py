"""techset (9): MaterialTechniqueSet, 292 bytes. docs/research/structs-content.md section 9.

+0 name, +4 flags, +8 techniques[71] (each [-1]: align 4, MaterialTechnique).
MaterialTechnique: LS 8 (+0 name, +4 flags, +6 passCount), LS 24 x passCount,
each pass's pointers, then the technique name (loaded last).
MaterialPass (24): +0 vertexDecl [-1] (align 4, LS 34), +4 vertex shader ref,
+8 pixel shader ref, +0xc three arg counts, +0x14 args [nz] (align 4, LS 8 x
args; literal args of type 1 or 7 with +4 == -1: align 4, LS 16).
Loaders: Ptr 0x248878, struct 0x2486d8, technique 0x2483c8, pass 0x2480c0.

Node: "header", "name", "techniques" (71 entries: a technique node or None).
Technique: "head" (8 bytes), "passes" (elements), "name". Pass element: "raw",
"vertex_decl", "vertex_shader", "pixel_shader", "args" (elements: "raw",
"literal").
"""

from __future__ import annotations

from opent5.xfile.constants import AssetType, Block
from opent5.xfile.handlers.base import Handler, array, asset_ref, items, register
from opent5.xfile.stream import Chunk, XStream

TECHNIQUE_COUNT = 71
#: MaterialShaderArgument types whose +4 is a pointer to a 16-byte literal.
LITERAL_ARG_TYPES = (1, 7)


def pass_body(io: XStream, p: Chunk, node: dict) -> None:
    array(io, p, 0, 3, 34, node, "vertex_decl")
    asset_ref(io, p, 4, AssetType.VERTEXSHADER, node, "vertex_shader")
    asset_ref(io, p, 8, AssetType.PIXELSHADER, node, "pixel_shader")
    count = p.u8(12) + p.u8(13) + p.u8(14)
    for a, arg in items(io, p, 20, 3, 8, count, node, "args", owned=True) or ():
        if a.u16(0) in LITERAL_ARG_TYPES:
            array(io, a, 4, 3, 16, arg, "literal")


def technique_body(io: XStream, node: dict) -> None:
    head = io.load(8, node, "head")
    for p, element in io.items(24, head.u16(6), node, "passes"):
        pass_body(io, p, element)
    io.string(head, 0, node, "name")


def techset_body(io: XStream, h: Chunk, node: dict) -> None:
    io.push(Block.VIRTUAL)
    io.string(h, 0, node, "name")
    techniques = io.children(node, "techniques")
    for i in range(TECHNIQUE_COUNT):
        if io.reading:
            techniques.append(None)
        if io.follows(h, 8 + 4 * i):
            io.alloc(3)
            if io.reading:
                techniques[i] = {}
            technique_body(io, techniques[i])
    io.pop()


@register
class TechsetHandler(Handler):
    asset_type = AssetType.TECHSET
    header_size = 292

    def body(self, io: XStream, header: Chunk, node: dict) -> None:
        techset_body(io, header, node)
