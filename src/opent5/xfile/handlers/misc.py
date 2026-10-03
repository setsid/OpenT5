"""The small content types: font (22), ddl (42), emblemset (45), glasses (43),
packindex (40), xGlobals (41), texturelist (44). docs/research/structs-content.md
section 14.
"""

from __future__ import annotations

from opent5.xfile.constants import AssetType, Block
from opent5.xfile.handlers.base import Handler, array, asset_ref, items, register
from opent5.xfile.stream import Chunk, XStream


@register
class FontHandler(Handler):
    """Font_s (24): +0 fontName, +4 pixelHeight, +8 glyphCount, +0xc material,
    +0x10 glowMaterial, +0x14 glyphs [-1] (align 4, LS 24 x glyphCount). Loader 0x249590."""

    kind = "Font_s"
    asset_type = AssetType.FONT
    header_size = 24

    def body(self, io: XStream, h: Chunk, node: dict) -> None:
        io.push(Block.VIRTUAL)
        io.string(h, 0, node, "name")
        asset_ref(io, h, 12, AssetType.MATERIAL, node, "material")
        asset_ref(io, h, 16, AssetType.MATERIAL, node, "glow_material")
        array(io, h, 20, 3, 24 * h.s32(8), node, "glyphs")
        io.pop()


def ddl_def_body(io: XStream, node: dict) -> None:
    """ddlDef_t (28): structList [nz] (16 each), enumList [nz] (12 each), next [nz]."""
    d = io.load(28, node, "raw")
    for sd, struct_def in (
        items(io, d, 8, 3, 16, d.s32(12), node, "structs", owned=True, kind="ddlStructDef_t") or ()
    ):
        io.string(sd, 0, struct_def, "name")
        members = items(
            io, sd, 12, 3, 48, sd.s32(8), struct_def, "members", owned=True, kind="ddlMemberDef_t"
        )
        for mb, member in members or ():
            io.string(mb, 0, member, "name")
    for en, enum_def in (
        items(io, d, 16, 3, 12, d.s32(20), node, "enums", owned=True, kind="ddlEnumDef_t") or ()
    ):
        io.string(en, 0, enum_def, "name")
        names = array(io, en, 8, 3, 4 * en.s32(4), enum_def, "member_ptrs", owned=True)
        if names is not None:
            members = enum_def.setdefault("members", {})
            for k in range(en.s32(4)):
                io.string(names, 4 * k, members, k)
    if io.follows(d, 24, owned=True):
        io.alloc(3)
        if io.reading:
            node["next"] = {"_t": "ddlDef_t"}
        ddl_def_body(io, node["next"])
    elif io.reading:
        node["next"] = None


@register
class DdlHandler(Handler):
    """ddlRoot_t (8): +0 name, +4 ddlDef [nz]. Loaders 0x247480 / 0x247008 / 0x2458b0."""

    kind = "ddlRoot_t"
    asset_type = AssetType.DDL
    header_size = 8

    def body(self, io: XStream, h: Chunk, node: dict) -> None:
        io.push(Block.VIRTUAL)
        io.string(h, 0, node, "name")
        if io.follows(h, 4, owned=True):
            io.alloc(3)
            if io.reading:
                node["ddl_def"] = {"_t": "ddlDef_t"}
            ddl_def_body(io, node["ddl_def"])
        elif io.reading:
            node["ddl_def"] = None
        io.pop()


@register
class EmblemSetHandler(Handler):
    """EmblemSet (44, no name field; the asset is named "emblemset"). Loader 0x24a7f8."""

    kind = "EmblemSet"
    asset_type = AssetType.EMBLEMSET
    header_size = 44

    def body(self, io: XStream, h: Chunk, node: dict) -> None:
        io.push(Block.VIRTUAL)
        io.note(node, "name", "emblemset")
        array(io, h, 8, 3, 12 * h.s32(4), node, "layers", owned=True)
        for c, category in (
            items(io, h, 16, 3, 8, h.s32(12), node, "categories", owned=True, kind="EmblemCategory")
            or ()
        ):
            io.string(c, 0, category, "name")
            io.string(c, 4, category, "description")
        for ic, icon in (
            items(io, h, 24, 3, 40, h.s32(20), node, "icons", owned=True, kind="EmblemIcon") or ()
        ):
            asset_ref(io, ic, 0, AssetType.IMAGE, icon, "image")
            io.string(ic, 4, icon, "description")
        for bg, back in (
            items(
                io,
                h,
                32,
                3,
                24,
                h.s32(28),
                node,
                "backgrounds",
                owned=True,
                kind="EmblemBackground",
            )
            or ()
        ):
            asset_ref(io, bg, 0, AssetType.MATERIAL, back, "material")
            io.string(bg, 4, back, "description")
        array(io, h, 40, 1, 2 * h.s32(36), node, "background_lookup", owned=True)
        io.pop()

    def name_of(self, node) -> str:
        return "emblemset"


def glass_def_body(io: XStream, g: Chunk, node: dict) -> None:
    io.string(g, 0, node, "name")
    for off in (0x1C, 0x20, 0x24):
        asset_ref(io, g, off, AssetType.MATERIAL, node, f"material_{off:#x}")
    for off in (0x28, 0x2C, 0x30):
        io.string(g, off, node, f"string_{off:#x}")
    for off in (0x34, 0x38):
        asset_ref(io, g, off, AssetType.FX, node, f"effect_{off:#x}")


@register
class GlassesHandler(Handler):
    """Glasses (56): +0 name, +4 numGlasses, +8 glasses [nz] (124 each), +0xc
    workMemory [nz] (RUNTIME, align 32, +0x10 bytes). Loader 0x24db80."""

    kind = "Glasses"
    asset_type = AssetType.GLASSES
    header_size = 56

    def body(self, io: XStream, h: Chunk, node: dict) -> None:
        io.push(Block.VIRTUAL)
        io.string(h, 0, node, "name")
        for g, glass in (
            items(io, h, 8, 3, 124, h.u32(4), node, "glasses", owned=True, kind="Glass") or ()
        ):
            if io.follows(g, 0):
                io.alloc(3)
                if io.reading:
                    glass["glass_def"] = {"_t": "GlassDef"}
                glass_def = glass["glass_def"]
                glass_def_body(io, io.load(60, glass_def, "raw"), glass_def)
            elif io.reading:
                glass["glass_def"] = None
            array(io, g, 0x40, 3, 8 * g.u8(0x3D), glass, "outline", owned=True)
        if io.follows(h, 12, owned=True):
            io.push(Block.RUNTIME)
            io.alloc(31)
            io.reserve(h.u32(16))
            io.pop()
        io.pop()


@register
class PackIndexHandler(Handler):
    """PackIndex (12 on PS3): +0 name, +4 u32, +8 u32 (INFERRED pack id)."""

    kind = "PackIndex"
    asset_type = AssetType.PACKINDEX
    header_size = 12

    def body(self, io: XStream, h: Chunk, node: dict) -> None:
        io.push(Block.VIRTUAL)
        io.string(h, 0, node, "name")
        io.pop()


@register
class XGlobalsHandler(Handler):
    """XGlobals (40): +0 name, the rest plain data (layout from the ELF; 14 zones agree)."""

    kind = "XGlobals"
    asset_type = AssetType.XGLOBALS
    header_size = 40

    def body(self, io: XStream, h: Chunk, node: dict) -> None:
        io.push(Block.VIRTUAL)
        io.string(h, 0, node, "name")
        io.pop()


@register
class TextureListHandler(Handler):
    """TextureList (8, PS3 only, no name; named "texturelist"): +0 count, +4
    entries [nz] (align 4, LS 4 x count). Loader 0x235c40."""

    kind = "TextureList"
    asset_type = AssetType.TEXTURELIST
    header_size = 8

    def body(self, io: XStream, h: Chunk, node: dict) -> None:
        io.push(Block.VIRTUAL)
        io.note(node, "name", "texturelist")
        array(io, h, 4, 3, 4 * h.u32(0), node, "entries", owned=True)
        io.pop()

    def name_of(self, node) -> str:
        return "texturelist"
