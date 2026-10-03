"""techset (9): MaterialTechniqueSet, 292 bytes. docs/research/structs-content.md section 9.

+0 name, +4 flags, +8 techniques[71] (each [-1]: align 4, MaterialTechnique).
MaterialTechnique: LS 8 (+0 name, +4 flags, +6 passCount), LS 24 x passCount,
each pass's pointers, then the technique name (loaded last).
MaterialPass (24): +0 vertexDecl [-1] (align 4, LS 34), +4 vertex shader ref,
+8 pixel shader ref, +0xc three arg counts, +0x14 args [nz] (align 4, LS 8 x
args; literal args of type 1 or 7 with +4 == -1: align 4, LS 16).
Loaders: Ptr 0x248878, struct 0x2486d8, technique 0x2483c8, pass 0x2480c0.
"""

from __future__ import annotations

from opent5.xfile.constants import AssetType, Block
from opent5.xfile.handlers.base import Handler, asset_ref, register
from opent5.xfile.stream import Chunk, XStream

TECHNIQUE_COUNT = 71
#: MaterialShaderArgument types whose +4 is a pointer to a 16-byte literal.
LITERAL_ARG_TYPES = (1, 7)


def read_pass(st: XStream, p: Chunk) -> dict:
    decl = None
    if st.follows(p, 0):
        st.alloc(3)
        decl = st.load(34).bytes()
    vertex_shader = asset_ref(st, p, 4, AssetType.VERTEXSHADER)
    pixel_shader = asset_ref(st, p, 8, AssetType.PIXELSHADER)
    counts = (p.u8(12), p.u8(13), p.u8(14))
    args = None
    if st.follows(p, 20, owned=True):
        n = sum(counts)
        st.alloc(3)
        table = st.load(8 * n)
        args = []
        for a in table.items(8, n):
            arg = {"type": a.u16(0), "dest": a.u16(2), "u": a.u32(4)}
            if arg["type"] in LITERAL_ARG_TYPES and st.follows(a, 4):
                st.alloc(3)
                literal = st.load(16)
                arg["literal"] = tuple(literal.f32(4 * k) for k in range(4))
            args.append(arg)
    return {
        "vertex_decl": decl,
        "vertex_shader": vertex_shader,
        "pixel_shader": pixel_shader,
        "per_prim_arg_count": counts[0],
        "per_obj_arg_count": counts[1],
        "stable_arg_count": counts[2],
        "args": args,
        "raw": p.bytes(),
    }


def read_technique(st: XStream) -> dict:
    head = st.load(8)
    count = head.u16(6)
    passes = st.load(24 * count)
    pass_list = [read_pass(st, p) for p in passes.items(24, count)]
    name = st.string(head, 0)
    return {"name": name, "flags": head.u16(4), "passes": pass_list}


def read_techset(st: XStream, h: Chunk) -> dict:
    st.push(Block.VIRTUAL)
    name = st.string(h, 0)
    techniques: list = []
    for i in range(TECHNIQUE_COUNT):
        off = 8 + 4 * i
        if st.follows(h, off):
            st.alloc(3)
            techniques.append(read_technique(st))
        else:
            raw = h.u32(off)
            techniques.append(raw or None)
    st.pop()
    return {
        "name": name,
        "world_vert_format": h.u8(4),
        "flags": h.data[4:8].tobytes(),
        "techniques": techniques,
    }


@register
class TechsetHandler(Handler):
    asset_type = AssetType.TECHSET
    header_size = 292

    def read(self, st: XStream, header: Chunk) -> dict:
        return read_techset(st, header)
