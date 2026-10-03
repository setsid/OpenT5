"""material (6): Material, 128 bytes on PS3. docs/research/structs-content.md section 8.

Load order: name, techniqueSet (asset ref), textureTable [-1] (align 4, LS 16 x
textureCount; then per texture its image ref, or water_t for semantic 11),
constantTable [-1] (align 16, LS 32 x constantCount), stateBitsTable [-1]
(align 4, LS 4 x stateBitsCount; each element: push TEMP, -1/-2 -> align 4,
LS 8, pop). Loaders: Ptr 0x249498, struct 0x248f20, textures 0x248e28, water
0x248ce8, state bits 0x2366d8.
"""

from __future__ import annotations

from opent5.xfile.constants import PTR_INLINE, PTR_INSERT, AssetType, Block
from opent5.xfile.handlers.base import Handler, asset_ref, register
from opent5.xfile.stream import Chunk, XStream

TS_WATER_MAP = 11


def read_water(st: XStream, w: Chunk) -> dict:
    """water_t (72): three [nz] float arrays of M x N, then an image ref."""
    m, n = w.s32(16), w.s32(20)
    arrays = []
    for off in (4, 8, 12):
        if st.follows(w, off, owned=True):
            st.alloc(3)
            arrays.append(st.load(4 * m * n).bytes())
        else:
            arrays.append(None)
    image = asset_ref(st, w, 68, AssetType.IMAGE)
    return {
        "M": m,
        "N": n,
        "H0": arrays[0],
        "wTerm": arrays[1],
        "wTermSum": arrays[2],
        "image": image,
        "raw": w.bytes(),
    }


def read_material(st: XStream, h: Chunk) -> dict:
    st.push(Block.VIRTUAL)
    name = st.string(h, 0)
    techset = asset_ref(st, h, 0x70, AssetType.TECHSET)
    texture_count, constant_count, state_bits_count = h.u8(0x67), h.u8(0x68), h.u8(0x69)
    textures = None
    if st.follows(h, 0x74):
        st.alloc(3)
        table = st.load(16 * texture_count)
        textures = []
        for d in table.items(16, texture_count):
            entry = {
                "name_hash": d.u32(0),
                "name_start": d.u8(4),
                "name_end": d.u8(5),
                "sampler_state": d.u8(6),
                "semantic": d.u8(7),
                "is_mature_content": d.u8(8),
            }
            if entry["semantic"] == TS_WATER_MAP:
                water = None
                if st.follows(d, 12):
                    st.alloc(3)
                    water = read_water(st, st.load(72))
                entry["water"] = water
            else:
                entry["image"] = asset_ref(st, d, 12, AssetType.IMAGE)
            textures.append(entry)
    constants = None
    if st.follows(h, 0x78):
        st.alloc(15)
        table = st.load(32 * constant_count)
        constants = [
            {
                "name_hash": c.u32(0),
                "name": c.data[4:16].tobytes().split(b"\0", 1)[0].decode("latin-1"),
                "literal": tuple(c.f32(16 + 4 * k) for k in range(4)),
            }
            for c in table.items(32, constant_count)
        ]
    state_bits = None
    if st.follows(h, 0x7C):
        st.alloc(3)
        table = st.load(4 * state_bits_count)
        state_bits = []
        for i in range(state_bits_count):
            raw = st.ref(table, 4 * i)
            st.push(Block.TEMP)
            bits = None
            if raw in (PTR_INLINE, PTR_INSERT):
                st.alloc(3)
                if raw == PTR_INSERT:
                    st.insert()
                bits = st.load(8).bytes()
            st.pop()
            state_bits.append(bits if bits is not None else raw)
    st.pop()
    return {
        "name": name,
        "info": h.data[0:32].tobytes(),
        "game_flags": h.u8(4),
        "sort_key": h.u8(9),
        "state_bits_entry": h.data[0x20:0x67].tobytes(),
        "texture_count": texture_count,
        "constant_count": constant_count,
        "state_bits_count": state_bits_count,
        "technique_set": techset,
        "textures": textures,
        "constants": constants,
        "state_bits": state_bits,
        "header": h.bytes(),
    }


@register
class MaterialHandler(Handler):
    asset_type = AssetType.MATERIAL
    header_size = 0x80

    def read(self, st: XStream, header: Chunk) -> dict:
        return read_material(st, header)
