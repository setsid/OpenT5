"""The small content types: font (22), ddl (42), emblemset (45), glasses (43),
packindex (40), xGlobals (41), texturelist (44). docs/research/structs-content.md
section 14.
"""

from __future__ import annotations

from opent5.xfile.constants import AssetType, Block
from opent5.xfile.handlers.base import Handler, asset_ref, blob, register
from opent5.xfile.stream import Chunk, XStream


@register
class FontHandler(Handler):
    """Font_s (24): +0 fontName, +4 pixelHeight, +8 glyphCount, +0xc material,
    +0x10 glowMaterial, +0x14 glyphs [-1] (align 4, LS 24 x glyphCount). Loader 0x249590."""

    asset_type = AssetType.FONT
    header_size = 24

    def read(self, st: XStream, h: Chunk) -> dict:
        st.push(Block.VIRTUAL)
        name = st.string(h, 0)
        material = asset_ref(st, h, 12, AssetType.MATERIAL)
        glow = asset_ref(st, h, 16, AssetType.MATERIAL)
        glyphs = None
        if st.follows(h, 20):
            st.alloc(3)
            glyphs = st.load(24 * h.s32(8)).bytes()
        st.pop()
        return {
            "name": name,
            "pixel_height": h.s32(4),
            "glyph_count": h.s32(8),
            "material": material,
            "glow_material": glow,
            "glyphs": glyphs,
        }


def read_ddl_def(st: XStream) -> dict:
    """ddlDef_t (28): structList [nz] (16 each), enumList [nz] (12 each), next [nz]."""
    d = st.load(28)
    out: dict = {"raw": d.bytes(), "structs": None, "enums": None, "next": None}
    if st.follows(d, 8, owned=True):
        n = d.s32(12)
        st.alloc(3)
        structs = []
        for sd in st.load(16 * n).items(16, n):
            entry: dict = {"name": st.string(sd, 0), "size": sd.s32(4), "members": None}
            if st.follows(sd, 12, owned=True):
                m = sd.s32(8)
                st.alloc(3)
                members = st.load(48 * m)
                entry["members"] = [
                    {"name": st.string(mb, 0), "raw": mb.bytes()} for mb in members.items(48, m)
                ]
            structs.append(entry)
        out["structs"] = structs
    if st.follows(d, 16, owned=True):
        n = d.s32(20)
        st.alloc(3)
        enums = []
        for en in st.load(12 * n).items(12, n):
            entry = {"name": st.string(en, 0), "members": None}
            if st.follows(en, 8, owned=True):
                m = en.s32(4)
                st.alloc(3)
                names = st.load(4 * m)
                entry["members"] = [st.string(names, 4 * k) for k in range(m)]
            enums.append(entry)
        out["enums"] = enums
    if st.follows(d, 24, owned=True):
        st.alloc(3)
        out["next"] = read_ddl_def(st)
    return out


@register
class DdlHandler(Handler):
    """ddlRoot_t (8): +0 name, +4 ddlDef [nz]. Loaders 0x247480 / 0x247008 / 0x2458b0."""

    asset_type = AssetType.DDL
    header_size = 8

    def read(self, st: XStream, h: Chunk) -> dict:
        st.push(Block.VIRTUAL)
        name = st.string(h, 0)
        ddl_def = None
        if st.follows(h, 4, owned=True):
            st.alloc(3)
            ddl_def = read_ddl_def(st)
        st.pop()
        return {"name": name, "ddl_def": ddl_def}


@register
class EmblemSetHandler(Handler):
    """EmblemSet (44, no name field; the asset is named "emblemset"). Loader 0x24a7f8."""

    asset_type = AssetType.EMBLEMSET
    header_size = 44

    def read(self, st: XStream, h: Chunk) -> dict:
        st.push(Block.VIRTUAL)
        out: dict = {"name": "emblemset", "header": h.bytes()}
        out["layers"] = None
        if st.follows(h, 8, owned=True):
            st.alloc(3)
            out["layers"] = st.load(12 * h.s32(4)).bytes()
        out["categories"] = None
        if st.follows(h, 16, owned=True):
            n = h.s32(12)
            st.alloc(3)
            table = st.load(8 * n)
            out["categories"] = [(st.string(c, 0), st.string(c, 4)) for c in table.items(8, n)]
        out["icons"] = None
        if st.follows(h, 24, owned=True):
            n = h.s32(20)
            st.alloc(3)
            table = st.load(40 * n)
            out["icons"] = [
                {
                    "image": asset_ref(st, ic, 0, AssetType.IMAGE),
                    "description": st.string(ic, 4),
                    "raw": ic.bytes(),
                }
                for ic in table.items(40, n)
            ]
        out["backgrounds"] = None
        if st.follows(h, 32, owned=True):
            n = h.s32(28)
            st.alloc(3)
            table = st.load(24 * n)
            out["backgrounds"] = [
                {
                    "material": asset_ref(st, bg, 0, AssetType.MATERIAL),
                    "description": st.string(bg, 4),
                    "raw": bg.bytes(),
                }
                for bg in table.items(24, n)
            ]
        out["background_lookup"] = None
        if st.follows(h, 40, owned=True):
            st.alloc(1)
            out["background_lookup"] = st.load(2 * h.s32(36)).bytes()
        st.pop()
        return out


def read_glass_def(st: XStream, g: Chunk) -> dict:
    return {
        "name": st.string(g, 0),
        "materials": [asset_ref(st, g, off, AssetType.MATERIAL) for off in (0x1C, 0x20, 0x24)],
        "strings": [st.string(g, off) for off in (0x28, 0x2C, 0x30)],
        "effects": [asset_ref(st, g, off, AssetType.FX) for off in (0x34, 0x38)],
        "raw": g.bytes(),
    }


@register
class GlassesHandler(Handler):
    """Glasses (56): +0 name, +4 numGlasses, +8 glasses [nz] (124 each), +0xc
    workMemory [nz] (RUNTIME, align 32, +0x10 bytes). Loader 0x24db80."""

    asset_type = AssetType.GLASSES
    header_size = 56

    def read(self, st: XStream, h: Chunk) -> dict:
        st.push(Block.VIRTUAL)
        name = st.string(h, 0)
        glasses = None
        if st.follows(h, 8, owned=True):
            n = h.u32(4)
            st.alloc(3)
            table = st.load(124 * n)
            glasses = []
            for g in table.items(124, n):
                entry: dict = {"glass_def": None, "outline": None, "raw": g.bytes()}
                if st.follows(g, 0):
                    st.alloc(3)
                    entry["glass_def"] = read_glass_def(st, st.load(60))
                if st.follows(g, 0x40, owned=True):
                    st.alloc(3)
                    entry["outline"] = st.load(8 * g.u8(0x3D)).bytes()
                glasses.append(entry)
        work_memory = h.u32(16)
        if st.follows(h, 12, owned=True):
            st.push(Block.RUNTIME)
            st.alloc(31)
            st.reserve(work_memory)
            st.pop()
        st.pop()
        return {
            "name": name,
            "glasses": glasses,
            "work_memory_size": work_memory,
            "header": h.bytes(),
        }


@register
class PackIndexHandler(Handler):
    """PackIndex (12 on PS3): +0 name, +4 u32, +8 u32 (INFERRED pack id)."""

    asset_type = AssetType.PACKINDEX
    header_size = 12

    def read(self, st: XStream, h: Chunk) -> dict:
        st.push(Block.VIRTUAL)
        name = st.string(h, 0)
        st.pop()
        return {"name": name, "value_4": h.u32(4), "pack_id": h.u32(8)}


@register
class XGlobalsHandler(Handler):
    """XGlobals (40): +0 name, the rest plain data (layout from the ELF only)."""

    asset_type = AssetType.XGLOBALS
    header_size = 40

    def read(self, st: XStream, h: Chunk) -> dict:
        st.push(Block.VIRTUAL)
        name = st.string(h, 0)
        st.pop()
        return {"name": name, "header": h.bytes()}


@register
class TextureListHandler(Handler):
    """TextureList (8, PS3 only, no name; named "texturelist"): +0 count, +4
    entries [nz] (align 4, LS 4 x count). Loader 0x235c40."""

    asset_type = AssetType.TEXTURELIST
    header_size = 8

    def read(self, st: XStream, h: Chunk) -> dict:
        st.push(Block.VIRTUAL)
        entries = None
        if st.follows(h, 4, owned=True):
            st.alloc(3)
            entries = blob(st.load(4 * h.u32(0)))
        st.pop()
        return {"name": "texturelist", "count": h.u32(0), "entries": entries}
