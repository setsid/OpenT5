"""Field schemas for every struct the handlers load, and the node kinds that use them.

``KINDS[kind][key]`` is the schema of node[key] for a node whose "_t" is
`kind`: a ``Struct`` (one struct over the bytes) or an ``ArrayOf`` (a run of
structs or scalars). Handlers tag every node they fill with its kind.

Two sources of names, stated per struct:

* ``pc(...)``: the PC T5 layout (opent5.xfile.layouts_pc, generated from
  OpenAssetTools src/Common/Game/T5/T5_Assets.h), used where the PS3 struct has
  the PC size and every pointer the PS3 loader follows sits at the PC offset
  (structs-content.md and structs-map.md list which; the sanity report in
  docs/research/fields.md checks the types on data);
* ``S(...)``: PS3-specific layouts from structs-*.md, walk-all.md and the ELF,
  where the PC layout does not apply. Unnamed bytes become unk_0x.. fields.

Pointer fields carry role "ptr" and count fields role "count" with the node
key they size; both are read-only through a view.
"""

from __future__ import annotations

import struct as _struct

from opent5.xfile.layouts_pc import PC_LAYOUTS
from opent5.xfile.schema import ArrayOf, Dynamic, Field, Struct


def F(
    off: int,
    name: str,
    type_: str,
    role: str | None = None,
    counts: str | None = None,
    names: dict | None = None,
) -> Field:
    return Field(name, off, type_, role, counts, names)


def P(off: int, name: str, n: int = 1) -> Field:
    """A pointer (n pointers)."""
    return Field(name, off, "ptr" if n == 1 else f"ptr[{n}]", "ptr")


def C(off: int, name: str, type_: str, counts: str) -> Field:
    """A count sizing node[counts]."""
    return Field(name, off, type_, "count", counts)


def S(name: str, size: int, *fields: Field, source: str = "") -> Struct:
    return Struct(name, size, list(fields), source)


def pc(
    name: str,
    *,
    alias: str | None = None,
    size: int | None = None,
    upto: int | None = None,
    counts: dict | None = None,
    roles: dict | None = None,
    types: dict | None = None,
    drop: tuple = (),
    extra: tuple = (),
    since: int | None = None,
    shift: int = 0,
    source: str = "",
) -> Struct:
    """A struct from the PC layout `name`. size: the PS3 size when it differs;
    upto / since: keep only PC fields below / from this PC offset; shift: added to
    every kept offset at or past `since`; counts / roles / types: per field
    overrides; drop: PC fields to leave unnamed; extra: more Fields."""
    pc_size, layout = PC_LAYOUTS[name]
    counts = counts or {}
    roles = roles or {}
    types = types or {}
    fields = []
    for fname, off, ftype in layout:
        if upto is not None and off >= upto:
            continue
        if fname in drop:
            continue
        if since is not None:
            if off < since:
                continue
            off += shift
        ftype = types.get(fname, ftype)
        role = roles.get(fname)
        if role is None and ftype.startswith("ptr"):
            role = "ptr"
        if fname in counts:
            role = "count"
        fields.append(Field(fname, off, ftype, role, counts.get(fname)))
    fields.extend(extra)
    note = source or f"PC layout {name} (OpenAssetTools T5_Assets.h)"
    return Struct(alias or name, size or pc_size, fields, note)


def pc_fields(
    name: str,
    prefix: str = "",
    base: int = 0,
    since: int = 0,
    until: int | None = None,
    shift: int = 0,
) -> tuple:
    """PC fields of `name` from PC offset `since` up to `until`, renamed with
    `prefix` and moved to base + offset + shift."""
    out = []
    for fname, off, ftype in PC_LAYOUTS[name][1]:
        if off < since or (until is not None and off >= until):
            continue
        role = "ptr" if ftype.startswith("ptr") else None
        out.append(Field(prefix + fname, base + off + shift, ftype, role))
    return tuple(out)


def A(element) -> ArrayOf:
    return ArrayOf(element)


# --------------------------------------------------------------------------------------------
# rawfile, stringtable, localize (structs-content.md 3 to 5)

RawFile = pc("RawFile", counts={"len": "buffer"})
StringTable = pc("StringTable", counts={"columnCount": "cells", "rowCount": "cells"})
StringTableCell = pc("StringTableCell")
LocalizeEntry = pc("LocalizeEntry")

# --------------------------------------------------------------------------------------------
# image (structs-content.md 7, textures.md 2)

GfxImage = S(
    "GfxImage",
    0x70,
    F(0x0, "texture.format", "u8"),
    F(0x1, "texture.mipmap", "u8"),
    F(0x2, "texture.dimension", "u8"),
    F(0x3, "texture.cubemap", "u8"),
    F(0x4, "texture.remap", "u32"),
    F(0x8, "texture.width", "u16"),
    F(0xA, "texture.height", "u16"),
    F(0xC, "texture.depth", "u16"),
    F(0xE, "texture.location", "u8"),
    F(0xF, "texture.pad", "u8"),
    F(0x10, "texture.pitch", "u32"),
    F(0x14, "texture.offset", "u32"),
    F(0x18, "mapType", "u8"),
    F(0x19, "semantic", "u8"),
    F(0x1A, "category", "u8"),
    F(0x1B, "delayLoadPixels", "u8"),
    C(0x1C, "size", "u32", "pixels"),
    F(0x20, "loadedSize", "u16[3]"),
    F(0x26, "streamed", "u8"),
    F(0x27, "streamingMode", "u8"),
    F(0x28, "streamedUnitsLE", "u32"),
    P(0x2C, "pixels"),
    F(0x30, "streamedBytesLE", "u32"),
    F(0x34, "parts", "u32[12]"),
    F(0x64, "partCount", "u8"),
    F(0x65, "partPad", "bytes[3]"),
    P(0x68, "name"),
    F(0x6C, "hash", "u32"),
    source="structs-content.md 7; textures.md 2 (CellGcmTexture, streaming records)",
)

# --------------------------------------------------------------------------------------------
# material, techset, shaders (structs-content.md 8 and 9)

Material = S(
    "Material",
    0x80,
    P(0x0, "info.name"),
    F(0x4, "info.gameFlags", "u32"),
    F(0x8, "info.pad", "u8"),
    F(0x9, "info.sortKey", "u8"),
    F(0xA, "info.textureAtlasRowCount", "u8"),
    F(0xB, "info.textureAtlasColumnCount", "u8"),
    F(0x10, "info.drawSurf", "u64"),
    F(0x18, "info.surfaceTypeBits", "u32"),
    F(0x1C, "info.layeredSurfaceTypes", "u32"),
    F(0x20, "stateBitsEntry", "u8[71]"),
    C(0x67, "textureCount", "u8", "textures"),
    C(0x68, "constantCount", "u8", "constants"),
    C(0x69, "stateBitsCount", "u8", "state_bits"),
    F(0x6A, "stateFlags", "u8"),
    F(0x6B, "cameraRegion", "u8"),
    P(0x70, "techniqueSet"),
    P(0x74, "textureTable"),
    P(0x78, "constantTable"),
    P(0x7C, "stateBitsTable"),
    source="structs-content.md 8 (PS3, 128 bytes)",
)
MaterialTextureDef = pc("MaterialTextureDef", types={"nameStart": "u8", "nameEnd": "u8"})
MaterialConstantDef = S(
    "MaterialConstantDef",
    32,
    F(0x0, "nameHash", "u32"),
    F(0x4, "name", "char[12]"),
    F(0x10, "literal", "vec4"),
    source="OpenAssetTools T5_Assets.h MaterialConstantDef (same size on PS3)",
)
water_t = S(
    "water_t",
    72,
    F(0x0, "floatTime", "f32"),
    P(0x4, "H0"),
    P(0x8, "wTerm"),
    P(0xC, "wTermSum"),
    F(0x10, "M", "s32", "count", "H0"),
    F(0x14, "N", "s32", "count", "H0"),
    P(0x44, "image"),
    source="structs-content.md 8 (ELF only; no instance in any zone)",
)
StateBitsRef = S("MaterialStateBitsRef", 4, P(0x0, "stateBits"), source="structs-content.md 8")
GfxStateBits = pc("GfxStateBits")
MaterialTechniqueSet = S(
    "MaterialTechniqueSet",
    292,
    P(0x0, "name"),
    F(0x4, "worldVertFormat", "u8"),
    F(0x5, "unused", "u8"),
    F(0x6, "techsetFlags", "u16"),
    P(0x8, "techniques", 71),
    source="structs-content.md 9 (PS3: 71 techniques; +0x4 names INFERRED)",
)
MaterialTechnique = S(
    "MaterialTechnique",
    8,
    P(0x0, "name"),
    F(0x4, "flags", "u16"),
    C(0x6, "passCount", "u16", "passes"),
    source="structs-content.md 9",
)
MaterialPass = S(
    "MaterialPass",
    24,
    P(0x0, "vertexDecl"),
    P(0x4, "vertexShader"),
    P(0x8, "pixelShader"),
    C(0xC, "perPrimArgCount", "u8", "args"),
    C(0xD, "perObjArgCount", "u8", "args"),
    C(0xE, "stableArgCount", "u8", "args"),
    F(0xF, "customSamplerFlags", "u8"),
    P(0x14, "args"),
    source="structs-content.md 9 (PS3 24 bytes; +0x10 unnamed)",
)
MaterialVertexDeclaration = S(
    "MaterialVertexDeclaration",
    34,
    F(0x0, "streamCount", "u8"),
    F(0x1, "hasOptionalSource", "u8"),
    F(0x2, "routing", "u8[32]"),
    source="PS3 34 bytes; first fields as PC MaterialVertexDeclaration (INFERRED)",
)
MaterialShaderArgument = S(
    "MaterialShaderArgument",
    8,
    F(0x0, "type", "u16"),
    F(0x2, "dest", "u16"),
    F(0x4, "u", "u32"),
    source="PC MaterialShaderArgument; +4 is a pointer only for literal types 1 and 7",
)
Vec4 = S("vec4", 16, F(0x0, "value", "vec4"))
MaterialVertexShader = S(
    "MaterialVertexShader",
    16,
    P(0x0, "name"),
    P(0x4, "program"),
    C(0xE, "programWords", "u16", "program"),
    source="structs-content.md 9 (+8..+0xd unnamed)",
)
MaterialPixelShader = S(
    "MaterialPixelShader",
    12,
    P(0x0, "name"),
    P(0x4, "program"),
    F(0x8, "unk_u16_0x8", "u16"),
    C(0xA, "programWords", "u16", "program"),
    source="structs-content.md 9",
)

# --------------------------------------------------------------------------------------------
# xanim (structs-content.md 10)

XAnimParts = pc(
    "XAnimParts",
    counts={
        "dataByteCount": "data_byte",
        "dataShortCount": "data_short",
        "dataIntCount": "data_int",
        "randomDataByteCount": "random_data_byte",
        "randomDataIntCount": "random_data_int",
        "randomDataShortCount": "random_data_short",
    },
    roles={"indexCount": "count"},
    drop=("boneCount", "notifyCount", "assetType", "isDefault"),
    extra=(
        F(0x18, "boneCount", "u8[11]"),
        C(0x23, "nameCount", "u8", "names"),
        C(0x24, "notifyCount", "u8", "notify"),
        F(0x25, "assetType", "u8"),
        F(0x26, "isDefault", "u8"),
    ),
    source="PC XAnimParts; PS3 boneCount has 12 classes, the 12th (+0x23) counts names",
)
XAnimNotifyInfo = pc("XAnimNotifyInfo", roles={"name": "scrstr"})
XAnimDeltaPart = pc("XAnimDeltaPart")
XAnimTransHead = S(
    "XAnimPartTransHead",
    4,
    F(0x0, "size", "u16"),
    F(0x2, "smallTrans", "u8"),
    source="structs-content.md 10",
)
XAnimTransFramesHead = S(
    "XAnimPartTransFrames",
    28,
    F(0x0, "mins", "vec3"),
    F(0xC, "size", "vec3"),
    P(0x18, "frames"),
    source="structs-content.md 10",
)
XAnimQuatHead = S(
    "XAnimDeltaPartQuatHead", 4, F(0x0, "size", "u16"), source="structs-content.md 10"
)
XAnimQuatFramesHead = S(
    "XAnimDeltaPartQuatFrames", 4, P(0x0, "frames"), source="structs-content.md 10"
)
Vec3 = S("vec3", 12, F(0x0, "value", "vec3"))

# --------------------------------------------------------------------------------------------
# fx, impactfx (structs-content.md 11: PC layouts)

FxEffectDef = pc(
    "FxEffectDef",
    counts={
        "elemDefCountLooping": "elem_defs",
        "elemDefCountOneShot": "elem_defs",
        "elemDefCountEmission": "elem_defs",
    },
)
FxElemDef = pc(
    "FxElemDef",
    types={
        "elemType": "u8",
        "visualCount": "u8",
        "velIntervalCount": "u8",
        "visStateIntervalCount": "u8",
    },
    counts={
        "velIntervalCount": "vel_samples",
        "visStateIntervalCount": "vis_samples",
        "visualCount": "visuals",
    },
)
FxElemVelStateSample = pc("FxElemVelStateSample")
FxElemVisStateSample = pc(
    "FxElemVisStateSample", types={"base.color": "u8[4]", "amplitude.color": "u8[4]"}
)
FxElemMarkVisuals = pc("FxElemMarkVisuals")
FxElemVisual = S("FxElemVisuals", 4, P(0x0, "visual"), source="PC FxElemVisuals union")
FxTrailDef = pc("FxTrailDef", counts={"vertCount": "verts", "indCount": "inds"})
FxTrailVertex = pc("FxTrailVertex")
FxImpactTable = pc("FxImpactTable")

# --------------------------------------------------------------------------------------------
# sound (structs-content.md 12)

SndBank = pc(
    "SndBank",
    counts={"aliasCount": "alias_lists", "radverbCount": "radverbs", "snapshotCount": "snapshots"},
)
snd_alias_list_t = pc("snd_alias_list_t", counts={"count": "aliases"})
snd_alias_t = pc("snd_alias_t")
SoundFile = pc("SoundFile", types={"type": "u8", "exists": "u8"})
LoadedSound = pc(
    "LoadedSound", counts={"sound.seek_table_count": "seek_table", "sound.data_size": "data"}
)
StreamedSound = S(
    "StreamedSound",
    24,
    P(0x0, "filename"),
    P(0x4, "primeSnd"),
    F(0x8, "unk_u32_0x8", "u32"),
    F(0xC, "unk_u32_0xc", "u32"),
    F(0x10, "unk_u32_0x10", "u32"),
    F(0x14, "unk_u32_0x14", "u32"),
    source="structs-content.md 12 (PS3 24 bytes; +8..+0x17 INFERRED size/offset/pack/rate)",
)
PrimedSound = pc("PrimedSound", counts={"size": "buffer"})
SndPatch = pc("SndPatch", counts={"elementCount": "elements", "fileCount": "files"})
SndDriverGlobals = pc(
    "SndDriverGlobals",
    counts={
        "groupCount": "groups",
        "curveCount": "curves",
        "panCount": "pans",
        "snapshotGroupCount": "snapshot_groups",
        "contextCount": "contexts",
        "masterCount": "masters",
    },
)

# --------------------------------------------------------------------------------------------
# weapon (structs-content.md 13: PC layouts)

WeaponVariantDef = pc("WeaponVariantDef")
WeaponDef = pc("WeaponDef")
FlameTable = pc("FlameTable")

# --------------------------------------------------------------------------------------------
# font, ddl, emblemset, glasses, packindex, xGlobals, texturelist (structs-content.md 14)

Font_s = pc("Font_s", counts={"glyphCount": "glyphs"})
Glyph = pc("Glyph")
ddlRoot_t = pc("ddlRoot_t")
ddlDef_t = pc("ddlDef_t", counts={"structCount": "structs", "enumCount": "enums"})
ddlStructDef_t = pc("ddlStructDef_t", counts={"memberCount": "members"})
ddlMemberDef_t = pc("ddlMemberDef_t")
ddlEnumDef_t = pc("ddlEnumDef_t", counts={"memberCount": "member_ptrs"})
EmblemSet = pc(
    "EmblemSet",
    counts={
        "layerCount": "layers",
        "categoryCount": "categories",
        "iconCount": "icons",
        "backgroundCount": "backgrounds",
        "backgroundLookupCount": "background_lookup",
    },
)
EmblemLayer = pc("EmblemLayer")
EmblemCategory = pc("EmblemCategory")
EmblemIcon = pc("EmblemIcon")
EmblemBackground = pc("EmblemBackground")
Glasses = pc("Glasses", counts={"numGlasses": "glasses"})
Glass = pc("Glass", types={"numOutlineVerts": "u8"}, counts={"numOutlineVerts": "outline"})
GlassDef = pc("GlassDef")
PackIndex = S(
    "PackIndex",
    12,
    P(0x0, "name"),
    F(0x4, "unk_u32_0x4", "u32"),
    F(0x8, "packId", "u32"),
    source="structs-content.md 14 (PS3 12 bytes; +8 pack id INFERRED)",
)
XGlobals = pc("XGlobals")
TextureList = S(
    "TextureList",
    8,
    C(0x0, "count", "u32", "entries"),
    P(0x4, "entries"),
    source="structs-content.md 14 (PS3 only)",
)

# --------------------------------------------------------------------------------------------
# menus (structs-content.md 6: PS3 layouts)

#: windowDef_t on PS3: the PC layout with dynamicFlags[4] (one per local client),
#: which moves everything after it by 12 (foreColor at +0x68, background at +0xac).
_WINDOW = (
    pc_fields("windowDef_t", "window.", until=0x54)
    + (F(0x54, "window.dynamicFlags", "s32[4]"),)
    + pc_fields("windowDef_t", "window.", since=0x58, shift=12)
)


def _expr(off: int, name: str) -> tuple:
    return (
        P(off, f"{name}.filename"),
        F(off + 4, f"{name}.line", "s32"),
        F(off + 8, f"{name}.numRpn", "s32"),
        P(off + 12, f"{name}.rpn"),
    )


def _rect(off: int, name: str) -> tuple:
    return tuple(Field(f"{name}.{f}", off + o, t) for f, o, t in PC_LAYOUTS["rectDef_s"][1])


menuDef_t = S(
    "menuDef_t",
    424,
    *_WINDOW,
    P(0xB0, "font"),
    F(0xB4, "fullScreen", "s32"),
    F(0xB8, "ui3dWindowId", "s32"),
    C(0xBC, "itemCount", "s32", "item_ptrs"),
    F(0xC0, "fontIndex", "s32"),
    F(0xC4, "cursorItem", "s32[4]"),
    F(0xD4, "fadeCycle", "s32"),
    F(0xD8, "priority", "s32"),
    F(0xDC, "fadeClamp", "f32"),
    F(0xE0, "fadeAmount", "f32"),
    F(0xE4, "fadeInAmount", "f32"),
    F(0xE8, "blurRadius", "f32"),
    F(0xEC, "openSlideSpeed", "s32"),
    F(0xF0, "closeSlideSpeed", "s32"),
    F(0xF4, "openSlideDirection", "s32"),
    F(0xF8, "closeSlideDirection", "s32"),
    *_rect(0xFC, "initialRectInfo"),
    F(0x114, "openFadingTime", "s32"),
    F(0x118, "closeFadingTime", "s32"),
    F(0x11C, "fadeTimeCounter", "s32"),
    F(0x120, "slideTimeCounter", "s32"),
    P(0x124, "onEvent"),
    P(0x128, "onKey"),
    *_expr(0x12C, "visibleExp"),
    F(0x140, "showBits", "u64"),
    F(0x148, "hideBits", "u64"),
    P(0x150, "allowedBinding"),
    P(0x154, "soundName"),
    F(0x158, "imageTrack", "s32"),
    F(0x15C, "control", "s32"),
    F(0x160, "focusColor", "vec4"),
    F(0x170, "disableColor", "vec4"),
    *_expr(0x180, "rectXExp"),
    *_expr(0x190, "rectYExp"),
    P(0x1A0, "items"),
    source="structs-content.md 6.3; PC menuDef_t member order with cursorItem[4] "
    "(the offsets this gives match every loaded field)",
)
ITEM_TYPES = {
    0: "DEFAULT",
    1: "TEXT",
    2: "IMAGE",
    3: "BUTTON",
    4: "LISTBOX",
    5: "EDITFIELD",
    6: "OWNERDRAW",
    7: "NUMERICFIELD",
    8: "SLIDER",
    9: "YESNO",
    10: "MULTI",
    11: "DVARENUM",
    12: "BIND",
    13: "VALIDFILEFIELD",
    14: "UPREDITFIELD",
    15: "GAME_MESSAGE_WINDOW",
    16: "BIND2",
    17: "HIGHLIGHT",
    18: "OWNERDRAW_TEXT",
    19: "OD_BUTTON",
    20: "OD_TEXT_BUTTON",
    21: "BUTTON_NO_TEXT",
    22: "ALPHANUMERICFIELD",
    25: "RADIOBUTTON",
    26: "MODEL",
    27: "CHECKBOX",
    28: "COMBO",
    30: "DECIMALFIELD",
}
itemDef_s = S(
    "itemDef_s",
    280,
    *_WINDOW,
    F(0xB0, "type", "s32", "enum", names=ITEM_TYPES),
    F(0xB4, "dataType", "s32"),
    F(0xB8, "imageTrack", "s32"),
    P(0xBC, "dvar"),
    P(0xC0, "dvarTest"),
    P(0xC4, "enableDvar"),
    F(0xC8, "dvarFlags", "s32"),
    P(0xCC, "typeData"),
    P(0xD0, "parent"),
    P(0xD4, "rectExpData"),
    *_expr(0xD8, "visibleExp"),
    F(0xE8, "showBits", "u64"),
    F(0xF0, "hideBits", "u64"),
    *_expr(0xF8, "forecolorAExp"),
    F(0x108, "ui3dWindowId", "s32"),
    P(0x10C, "onEvent"),
    P(0x110, "animInfo"),
    source="structs-content.md 6.4; PC itemDef_s member order (ItemDefType names from "
    "OpenAssetTools)",
)
ExpressionStatement = S(
    "ExpressionStatement", 16, *_expr(0, "exp"), source="structs-content.md 6.6"
)
expressionRpn = S(
    "expressionRpn",
    12,
    F(0x0, "type", "s32"),
    F(0x4, "dataType", "s32"),
    F(0x8, "value", "u32"),
    source="structs-content.md 6.6 (value is a string pointer for type 0 / dataType 2)",
)
ScriptCondition = S(
    "ScriptCondition",
    16,
    F(0x0, "fireOnTrue", "s32"),
    F(0x4, "constructID", "s32"),
    F(0x8, "blockID", "s32"),
    P(0xC, "next"),
    source="PC ScriptCondition (same size)",
)
GenericEventScript = S(
    "GenericEventScript",
    44,
    P(0x0, "prerequisites"),
    *_expr(0x4, "condition"),
    F(0x14, "type", "s32"),
    F(0x18, "fireOnTrue", "s32"),
    P(0x1C, "action"),
    F(0x20, "blockID", "s32"),
    F(0x24, "constructID", "s32"),
    P(0x28, "next"),
    source="structs-content.md 6.6; PC GenericEventScript names",
)
GenericEventHandler = S(
    "GenericEventHandler",
    12,
    P(0x0, "name"),
    P(0x4, "eventScript"),
    P(0x8, "next"),
    source="structs-content.md 6.6",
)
ItemKeyHandler = S(
    "ItemKeyHandler",
    12,
    F(0x0, "key", "s32"),
    P(0x4, "keyScript"),
    P(0x8, "next"),
    source="structs-content.md 6.6",
)
textDef_s = S(
    "textDef_s",
    140,
    *(f for k in range(4) for f in _rect(24 * k, f"textRect[{k}]")),
    *pc_fields("textDef_s", since=0x18, shift=0x48),
    source="structs-content.md 6.5; PC textDef_s with textRect[4] (text lands on +0x80 "
    "as loaded)",
)
focusItemDef_s = S(
    "focusItemDef_s",
    8,
    P(0x0, "onKey"),
    P(0x4, "focusTypeData"),
    source="structs-content.md 6.5",
)
listBoxDef_s = pc(
    "listBoxDef_s",
    size=700,
    since=0x1C,
    shift=0x20,
    counts={"maxRows": "rows"},
    types={"numColumns": "s32"},
    source="structs-content.md 6.5: PS3 700 bytes; the PC layout from numColumns on, moved "
    "by 0x20 (selectIcon, rows and maxRows land on the loaded offsets); +0..+0x3b unnamed",
)
MenuRow = S(
    "MenuRow",
    24,
    P(0x0, "cells"),
    P(0x4, "eventName"),
    P(0x8, "onFocusEventName"),
    F(0xC, "disableArg", "s32"),
    F(0x10, "status", "s32"),
    F(0x14, "name", "s32"),
    source="structs-content.md 6.5; PC MenuRow tail",
)
MenuCell = S(
    "MenuCell",
    12,
    F(0x0, "type", "s32"),
    C(0x4, "maxChars", "s32", "string_value"),
    P(0x8, "stringValue"),
    source="structs-content.md 6.5; PC MenuCell",
)
multiDef_s = S(
    "multiDef_s",
    396,
    P(0x0, "dvarList", 32),
    P(0x80, "dvarStr", 32),
    F(0x100, "dvarValue", "f32[32]"),
    F(0x180, "count", "s32"),
    F(0x184, "actionOnEnterPressOnly", "s32"),
    F(0x188, "strDef", "s32"),
    source="structs-content.md 6.5 (same as PC)",
)
editFieldDef_s = S(
    "editFieldDef_s",
    48,
    F(0x0, "cursorPos", "s32[4]"),
    *pc_fields("editFieldDef_s", since=0x4, shift=12),
    source="PC editFieldDef_s with cursorPos[4] (PS3 48 = PC 36 + 12; INFERRED)",
)
enumDvarDef_s = S("enumDvarDef_s", 4, P(0x0, "enumDvarName"), source="structs-content.md 6.5")
gameMsgDef_s = S(
    "gameMsgDef_s",
    8,
    F(0x0, "gameMsgWindowIndex", "s32"),
    F(0x4, "gameMsgWindowMode", "s32"),
    source="PC gameMsgDef_s",
)
UIAnimInfo = pc("UIAnimInfo", counts={"animStateCount": "anim_state_ptrs"})
animParamsDef_t = pc("animParamsDef_t")
rectData_s = S(
    "rectData_s",
    64,
    *_expr(0, "rectXExp"),
    *_expr(16, "rectYExp"),
    *_expr(32, "rectWExp"),
    *_expr(48, "rectHExp"),
    source="PC rectData_s",
)
MenuList = pc("MenuList", counts={"menuCount": "menu_ptrs"})

# --------------------------------------------------------------------------------------------
# physics, destructibles (structs-map.md 10: PC layouts)

PhysPreset = pc("PhysPreset")
PhysConstraints = pc("PhysConstraints")
PhysConstraint = pc("PhysConstraint")
DestructibleDef = pc("DestructibleDef", counts={"numPieces": "pieces"})
DestructiblePiece = pc("DestructiblePiece")

# --------------------------------------------------------------------------------------------
# xmodel (structs-map.md 9)

XModel = pc(
    "XModel",
    size=0xF8,
    upto=0x28,
    counts={"numBones": "base_mat", "numsurfs": "surfs"},
    roles={"numRootBones": "count"},
    extra=tuple(
        f
        for i in range(4)
        for f in (
            F(0x28 + 28 * i, f"lodInfo[{i}].dist", "f32"),
            F(0x2C + 28 * i, f"lodInfo[{i}].numsurfs", "u16"),
            F(0x2E + 28 * i, f"lodInfo[{i}].surfIndex", "u16"),
            F(0x30 + 28 * i, f"lodInfo[{i}].partBits", "s32[4]"),
        )
    )
    + (
        P(0xA0, "collSurfs"),
        C(0xA4, "numCollSurfs", "s32", "coll_surfs"),
        F(0xA8, "contents", "s32"),
        P(0xAC, "boneInfo"),
        F(0xB0, "radius", "f32"),
        F(0xB4, "mins", "vec3"),
        F(0xC0, "maxs", "vec3"),
        F(0xCC, "numLods", "u16"),
        F(0xCE, "collLod", "s16"),
        P(0xD0, "streamInfo.highMipBounds"),
        F(0xD4, "memUsage", "s32"),
        F(0xD8, "flags", "u32"),
        P(0xE8, "physPreset"),
        C(0xEC, "numCollmaps", "u8", "collmaps"),
        P(0xF0, "collmaps"),
        P(0xF4, "physConstraints"),
    ),
    source="structs-map.md 9 (PS3 0xf8; PC names; lodInfo 28 bytes with partBits[4] INFERRED)",
)
XSurface = S(
    "XSurface",
    0x5C,
    F(0x0, "tileMode", "u8"),
    C(0x1, "vertListCount", "u8", "vert_lists"),
    F(0x2, "flags", "u16"),
    F(0x4, "vertCount", "u16"),
    C(0x6, "triCount", "u16", "tri_indices"),
    P(0x8, "triIndices"),
    F(0xC, "vertInfo.vertCount", "s16[4]"),
    P(0x14, "vertInfo.vertsBlend"),
    P(0x18, "vertInfo.tensionData"),
    P(0x1C, "verts0"),
    F(0x20, "verts0Handle", "u32"),
    P(0x24, "vertexStream"),
    F(0x28, "vertexStreamHandle", "u32"),
    P(0x2C, "vertList"),
    F(0x30, "indexBufferHandle", "u32"),
    source="structs-map.md 9 (PS3 0x5c); +0x34 partBits as PC (INFERRED); +0x48 position "
    "offset and +0x55 scale exponents of packed positions: docs/extract.md 3.2",
)
XRigidVertList = pc("XRigidVertList")
XSurfaceCollisionTree = pc(
    "XSurfaceCollisionTree", counts={"nodeCount": "nodes", "leafCount": "leafs"}
)
XSurfaceCollisionNode = S(
    "XSurfaceCollisionNode",
    16,
    F(0x0, "aabb.mins", "u16[3]"),
    F(0x6, "aabb.maxs", "u16[3]"),
    F(0xC, "childBeginIndex", "u16"),
    F(0xE, "childCount", "u16"),
    source="OpenAssetTools T5_Assets.h XSurfaceCollisionNode",
)
XModelCollSurf = S(
    "XModelCollSurf_s",
    0x24,
    F(0x0, "mins", "vec3"),
    F(0xC, "maxs", "vec3"),
    F(0x18, "boneIdx", "s32"),
    F(0x1C, "contents", "s32"),
    F(0x20, "surfFlags", "s32"),
    source="PC XModelCollSurf_s without collTris / numCollTris (PS3 0x24; INFERRED)",
)
XBoneInfo = pc("XBoneInfo")
XModelHighMipBounds = pc("XModelHighMipBounds")
DObjAnimMat = pc("DObjAnimMat")
PhysGeomList = pc("PhysGeomList", counts={"count": "geoms"})
PhysGeomInfo = pc("PhysGeomInfo")
BrushWrapper = S(
    "BrushWrapper",
    0x60,
    F(0x0, "mins", "vec3"),
    F(0xC, "contents", "s32"),
    F(0x10, "maxs", "vec3"),
    C(0x1C, "numsides", "u32", "sides"),
    P(0x20, "sides"),
    F(0x24, "axial_cflags", "s32[6]"),
    F(0x3C, "axial_sflags", "s32[6]"),
    C(0x54, "numverts", "u32", "verts"),
    P(0x58, "verts"),
    P(0x5C, "planes"),
    source="OpenAssetTools T5_Assets.h BrushWrapper (PS3 0x60)",
)
cbrushside_t = pc("cbrushside_t")
cplane_s = pc("cplane_s", types={"type": "u8", "signbits": "u8", "pad": "u8[2]"})
XModelPieces = pc("XModelPieces", counts={"numpieces": "pieces"})
XModelPiece = pc("XModelPiece")

# --------------------------------------------------------------------------------------------
# com_map, lightdef, map_ents, game_map (structs-map.md 4 to 6)

ComWorld = pc(
    "ComWorld",
    counts={
        "primaryLightCount": "primary_lights",
        "numWaterCells": "water_cells",
        "numBurnableCells": "burnable_cells",
    },
)
ComPrimaryLight = pc(
    "ComPrimaryLight",
    types={"type": "u8", "canUseShadowMap": "u8", "exponent": "u8", "priority": "u8"},
)
ComBurnableCell = pc("ComBurnableCell")
GfxLightDef = pc("GfxLightDef", types={"attenuation.samplerState": "u8"})
MapEnts = pc("MapEnts", counts={"numEntityChars": "entity_string"})
GameWorld = pc(
    "GameWorldSp",
    alias="GameWorld",
    roles={"path.nodeCount": "count"},
    counts={
        "path.visBytes": "path_vis",
        "path.nodeTreeCount": "node_tree",
    },
    source="PC GameWorldSp (name + PathData); the PS3 MP world has the same layout",
)
pathnode_t = pc("pathnode_t")
pathlink_s = pc("pathlink_s", types={"ubBadPlaceCount": "u8[4]"})
pathnode_tree_t = pc("pathnode_tree_t")

# --------------------------------------------------------------------------------------------
# clipMap (structs-map.md 7)

clipMap_t = pc(
    "clipMap_t",
    counts={
        "planeCount": "planes",
        "numStaticModels": "static_model_list",
        "numMaterials": "materials",
        "numBrushSides": "brushsides",
        "numNodes": "nodes",
        "numLeafs": "leafs",
        "leafbrushNodesCount": "leafbrush_nodes",
        "numLeafBrushes": "leafbrushes",
        "numLeafSurfaces": "leafsurfaces",
        "vertCount": "verts",
        "numBrushVerts": "brush_verts",
        "nuinds": "uinds",
        "triCount": "tri_indices",
        "borderCount": "borders",
        "partitionCount": "partitions",
        "aabbTreeCount": "aabb_trees",
        "numSubModels": "cmodels",
        "numBrushes": "brushes",
        "numClusters": "visibility",
        "clusterBytes": "visibility",
        "num_constraints": "constraints",
    },
    source="PC clipMap_t (structs-map.md 7: same offsets on PS3)",
)
cStaticModel = pc("cStaticModel_s")
dmaterial_t = pc("dmaterial_t")
cNode_t = pc("cNode_t")
cLeaf_s = pc("cLeaf_s")
cLeafBrushNode = pc(
    "cLeafBrushNode_s",
    types={"axis": "u8"},
    counts={"leafBrushCount": "brushes"},
    extra=(F(0xC, "data.children.range", "f32"), F(0x10, "data.children.childOffset", "u16[2]")),
    source="PC cLeafBrushNode_s; data is a union: leaf (brushes) or children (dist at +8, "
    "range, childOffset)",
)
CollisionBorder = pc("CollisionBorder")
CollisionPartition = pc("CollisionPartition", types={"triCount": "u8", "borderCount": "u8"})
CollisionAabbTree = S(
    "CollisionAabbTree",
    32,
    F(0x0, "origin", "vec3"),
    F(0xC, "materialIndex", "u16"),
    F(0xE, "childCount", "u16"),
    F(0x10, "halfSize", "vec3"),
    F(0x1C, "u", "s32"),
    source="OpenAssetTools T5_Assets.h CollisionAabbTree",
)
cmodel_t = pc("cmodel_t")
cbrush_t = S(
    "cbrush_t",
    0x60,
    F(0x0, "mins", "vec3"),
    F(0xC, "contents", "s32"),
    F(0x10, "maxs", "vec3"),
    F(0x1C, "numsides", "u32"),
    P(0x20, "sides"),
    F(0x24, "axial_cflags", "s32[6]"),
    F(0x3C, "axial_sflags", "s32[6]"),
    F(0x54, "numverts", "u32"),
    P(0x58, "verts"),
    source="OpenAssetTools T5_Assets.h cbrush_t (PS3 0x60)",
)
DynEntityDef = pc("DynEntityDef")

# --------------------------------------------------------------------------------------------
# gfx_map (structs-map.md 8, walk-all.md 3)

GfxWorld = S(
    "GfxWorld",
    0x454,
    P(0x0, "name"),
    P(0x4, "baseName"),
    C(0x8, "planeCount", "u32", "planes"),
    C(0xC, "nodeCount", "u32", "nodes"),
    C(0x10, "surfaceCount", "u32", "surfaces"),
    C(0x14, "streamInfo.aabbTreeCount", "u32", "aabb_trees"),
    P(0x18, "streamInfo.aabbTrees"),
    C(0x1C, "streamInfo.leafRefCount", "u32", "leaf_refs"),
    P(0x20, "streamInfo.leafRefs"),
    C(0x38, "skySurfCount", "u32", "sky_start_surfs"),
    P(0x3C, "skyStartSurfs"),
    P(0x40, "skyImage"),
    F(0x44, "skySamplerState", "u8"),
    P(0x48, "skyBoxModel"),
    P(0x100, "sunLight"),
    F(0x104, "sunColorFromBsp", "vec3"),
    F(0x110, "sunPrimaryLightIndex", "u32"),
    C(0x114, "primaryLightCount", "u32", "shadow_geom"),
    C(0x118, "cullGroupCount", "u32", "cull_groups"),
    C(0x11C, "coronaCount", "u32", "coronas"),
    P(0x120, "coronas"),
    C(0x124, "shadowMapVolumeCount", "u32", "shadow_map_volumes"),
    P(0x128, "shadowMapVolumes"),
    C(0x12C, "shadowMapVolumePlaneCount", "u32", "shadow_map_volume_planes"),
    P(0x130, "shadowMapVolumePlanes"),
    C(0x134, "exposureVolumeCount", "u32", "exposure_volumes"),
    P(0x138, "exposureVolumes"),
    C(0x13C, "exposureVolumePlaneCount", "u32", "exposure_volume_planes"),
    P(0x140, "exposureVolumePlanes"),
    C(0x154, "dpvsPlanes.cellCount", "u32", "cells"),
    P(0x158, "dpvsPlanes.planes"),
    P(0x15C, "dpvsPlanes.nodes"),
    P(0x160, "dpvsPlanes.sceneEntCellBits"),
    F(0x164, "cellBitsCount", "u32"),
    P(0x168, "cells"),
    C(0x16C, "draw.reflectionProbeCount", "u32", "reflection_probes"),
    P(0x170, "draw.reflectionProbes"),
    P(0x174, "draw.reflectionProbeTextures"),
    C(0x178, "draw.lightmapCount", "u32", "lightmaps"),
    P(0x17C, "draw.lightmaps"),
    P(0x180, "draw.lightmapPrimaryTextures"),
    P(0x184, "draw.lightmapSecondaryTextures"),
    P(0x188, "draw.lightmapSecondaryTexturesB"),
    P(0x18C, "draw.images", 31),
    C(0x208, "draw.vertexCount", "u32", "vertices"),
    P(0x20C, "draw.vd.vertices"),
    F(0x210, "draw.vd.handle", "u32"),
    C(0x214, "draw.vertexLayerDataSize", "u32", "vertex_layer_data"),
    P(0x218, "draw.vld.data"),
    C(0x224, "draw.indexCount", "u32", "indices"),
    P(0x228, "draw.indices"),
    F(0x230, "lightGrid.hasLightRegions", "u8"),
    F(0x234, "lightGrid.sunPrimaryLightIndex", "u32"),
    F(0x238, "lightGrid.mins", "u16[3]"),
    F(0x23E, "lightGrid.maxs", "u16[3]"),
    F(0x244, "lightGrid.rowAxis", "u32"),
    F(0x248, "lightGrid.colAxis", "u32"),
    P(0x24C, "lightGrid.rowDataStart"),
    C(0x250, "lightGrid.rawRowDataSize", "u32", "raw_row_data"),
    P(0x254, "lightGrid.rawRowData"),
    C(0x258, "lightGrid.entryCount", "u32", "entries"),
    P(0x25C, "lightGrid.entries"),
    C(0x260, "lightGrid.colorCount", "u32", "colors"),
    P(0x264, "lightGrid.colors"),
    C(0x268, "modelCount", "u32", "models"),
    P(0x26C, "models"),
    F(0x270, "mins", "vec3"),
    F(0x27C, "maxs", "vec3"),
    F(0x288, "checksum", "u32"),
    C(0x28C, "materialMemoryCount", "u32", "material_memory"),
    P(0x290, "materialMemory"),
    P(0x298, "sun.spriteMaterial"),
    P(0x29C, "sun.flareMaterial"),
    P(0x334, "outdoorImage"),
    P(0x338, "cellCasterBits"),
    P(0x33C, "sceneDynModel"),
    P(0x340, "sceneDynBrush"),
    P(0x344, "primaryLightEntityShadowVis"),
    P(0x348, "primaryLightDynEntShadowVis", 2),
    P(0x350, "nonSunPrimaryLightForModelDynEnt"),
    P(0x354, "shadowGeom"),
    P(0x358, "lightRegion"),
    C(0x35C, "dpvs.smodelCount", "u32", "smodel_insts"),
    C(0x364, "dpvs.staticSurfaceCount", "u32", "sorted_surf_index"),
    F(0x380, "dpvs.smodelVisDataCount", "u32"),
    F(0x384, "dpvs.surfaceVisDataCount", "u32"),
    P(0x388, "dpvs.smodelVisData", 3),
    P(0x394, "dpvs.surfaceVisData", 3),
    P(0x3A0, "dpvs.smodelVisDataCameraSaved"),
    P(0x3A4, "dpvs.surfaceVisDataCameraSaved"),
    P(0x3A8, "dpvs.lodData"),
    P(0x3AC, "dpvs.sortedSurfIndex"),
    P(0x3B0, "dpvs.smodelInsts"),
    P(0x3B4, "dpvs.surfaces"),
    P(0x3B8, "dpvs.cullGroups"),
    P(0x3BC, "dpvs.smodelDrawInsts"),
    P(0x3C0, "dpvs.surfaceMaterials"),
    P(0x3C4, "dpvs.surfaceCastsSunShadow"),
    F(0x3CC, "dpvsDyn.dynEntClientWordCount", "u32[2]"),
    F(0x3D4, "dpvsDyn.dynEntClientCount", "u32[2]"),
    P(0x3DC, "dpvsDyn.dynEntCellBits", 2),
    P(0x3E4, "dpvsDyn.dynEntVisData", 6),
    C(0x3FC, "worldLodChainCount", "u32", "world_lod_chains"),
    P(0x400, "worldLodChains"),
    C(0x404, "worldLodInfoCount", "u32", "world_lod_infos"),
    P(0x408, "worldLodInfos"),
    C(0x40C, "worldLodSurfaceCount", "u32", "world_lod_surfaces"),
    P(0x410, "worldLodSurfaces"),
    F(0x414, "waterDirection", "f32"),
    C(0x418, "waterBuffers[0].bufferSize", "u32", "water_buffer0"),
    P(0x41C, "waterBuffers[0].buffer"),
    C(0x420, "waterBuffers[1].bufferSize", "u32", "water_buffer1"),
    P(0x424, "waterBuffers[1].buffer"),
    P(0x428, "waterMaterial"),
    P(0x42C, "coronaMaterial"),
    P(0x430, "ropeMaterial"),
    C(0x434, "numOccluders", "u32", "occluders"),
    P(0x438, "occluders"),
    C(0x43C, "numOutdoorBounds", "u32", "outdoor_bounds"),
    P(0x440, "outdoorBounds"),
    C(0x444, "heroLightCount", "u32", "hero_lights"),
    C(0x448, "heroLightTreeCount", "u32", "hero_light_tree"),
    P(0x44C, "heroLights"),
    P(0x450, "heroLightTree"),
    source="structs-map.md 8 and walk-all.md 3 (PS3 0x454; names INFERRED from the PC order)",
)
GfxStreamingAabbTree = pc("GfxStreamingAabbTree")
GfxLight = S(
    "GfxLight",
    0x170,
    F(0x0, "type", "u8"),
    F(0x1, "canUseShadowMap", "u8"),
    F(0x2, "cullDist", "s16"),
    F(0x4, "color", "vec3"),
    F(0x10, "dir", "vec3"),
    F(0x1C, "origin", "vec3"),
    F(0x28, "radius", "f32"),
    F(0x2C, "cosHalfFovOuter", "f32"),
    F(0x30, "cosHalfFovInner", "f32"),
    F(0x34, "exponent", "s32"),
    F(0x38, "spotShadowIndex", "u32"),
    F(0x3C, "angles", "vec3"),
    F(0x48, "spotShadowHiDistance", "f32"),
    F(0x4C, "diffuseColor", "vec4"),
    F(0x5C, "specularColor", "vec4"),
    F(0x6C, "shadowColor", "vec4"),
    F(0x7C, "falloff", "vec4"),
    F(0x8C, "attenuation", "vec4"),
    F(0x9C, "aAbB", "vec4"),
    F(0xAC, "cookieControl0", "vec4"),
    F(0xBC, "cookieControl1", "vec4"),
    F(0xCC, "cookieControl2", "vec4"),
    F(0xDC, "viewMatrix", "mat4"),
    F(0x11C, "projMatrix", "mat4"),
    P(0x160, "def"),
    source="OpenAssetTools T5_Assets.h GfxLight (PS3 0x170, def at +0x160 confirmed)",
)
GfxLightCorona = pc("GfxLightCorona")
GfxVolume16 = S(
    "GfxShadowMapVolume",
    16,
    F(0x0, "control", "u32"),
    F(0x4, "unk", "u32[3]"),
    source="PC GfxShadowMapVolume (no instance with data)",
)
GfxVolumePlane = S("GfxVolumePlane", 16, F(0x0, "plane", "vec4"), source="PC GfxVolumePlane")
GfxExposureVolume = pc("GfxExposureVolume")
DpvsPlane = pc("DpvsPlane", types={"side": "u8[3]", "pad": "u8"})
GfxCell = pc(
    "GfxCell",
    types={"reflectionProbeCount": "u8"},
    counts={
        "aabbTreeCount": "aabb_tree",
        "portalCount": "portals",
        "reflectionProbeCount": "reflection_probes",
    },
)
GfxAabbTree = pc("GfxAabbTree", counts={"smodelIndexCount": "smodel_indexes"})
GfxPortal = pc(
    "GfxPortal",
    types={"vertexCount": "u8", "plane.side": "u8[3]", "plane.pad": "u8"},
    counts={"vertexCount": "vertices"},
)
GfxReflectionProbe = pc("GfxReflectionProbe", counts={"probeVolumeCount": "probe_volumes"})
GfxReflectionProbeVolume = S(
    "GfxReflectionProbeVolumeData",
    96,
    F(0x0, "volumePlanes", "vec4[6]"),
    source="PC GfxReflectionProbeVolumeData",
)
GfxLightmapArray = pc("GfxLightmapArray")
GfxLightGridEntry = S(
    "GfxLightGridEntry",
    4,
    F(0x0, "colorsIndex", "u16"),
    F(0x2, "primaryLightIndex", "u8"),
    F(0x3, "needsTrace", "u8"),
    source="OpenAssetTools T5_Assets.h GfxLightGridEntry",
)
GfxLightGridColors = S(
    "GfxLightGridColors",
    0xA8,
    F(0x0, "rgb", "u8[168]"),
    source="OpenAssetTools GfxCompressedLightGridColors (56 x rgb)",
)
GfxBrushModel = pc("GfxBrushModel")
MaterialMemory = pc("MaterialMemory")
GfxShadowGeometry = pc(
    "GfxShadowGeometry", counts={"surfaceCount": "sorted_surf_index", "smodelCount": "smodel_index"}
)
GfxLightRegion = pc("GfxLightRegion", counts={"hullCount": "hulls"})
GfxLightRegionHull = pc("GfxLightRegionHull", counts={"axisCount": "axis"})
GfxLightRegionAxis = pc("GfxLightRegionAxis")
GfxStaticModelInst = pc("GfxStaticModelInst")
GfxSurface = S(
    "GfxSurface",
    0x60,
    F(0x0, "tris.mins", "vec3"),
    F(0xC, "tris.vertexLayerData", "s32"),
    F(0x10, "tris.maxs", "vec3"),
    F(0x1C, "tris.firstVertex", "s32"),
    F(0x24, "tris.vertexCount", "u16"),
    F(0x26, "tris.triCount", "u16"),
    F(0x28, "tris.baseIndex", "s32"),
    F(0x2C, "tris.himipRadiusSq", "f32"),
    F(0x30, "tris.stream2ByteOffset", "s32"),
    P(0x40, "material"),
    F(0x44, "lightmapIndex", "u8"),
    F(0x45, "reflectionProbeIndex", "u8"),
    F(0x46, "primaryLightIndex", "u8"),
    F(0x47, "flags", "u8"),
    F(0x48, "bounds", "vec3[2]"),
    source="PS3 0x60 (material at +0x40 confirmed): PC srfTriangles_t order with one extra "
    "word at +0x20 (a vertex offset relative to +0x1c, INFERRED) and 12 bytes at +0x34",
)
GfxCullGroup = pc("GfxCullGroup")
GfxStaticModelDrawInst = S(
    "GfxStaticModelDrawInst",
    0x2C,
    F(0x0, "cullDist", "f32"),
    F(0x4, "placement.origin", "vec3"),
    F(0x10, "placement.axis", "cmp[3]"),
    F(0x1C, "placement.scale", "f32"),
    P(0x20, "model"),
    F(0x24, "flags", "s32"),
    source="docs/extract.md 3.3: PS3 0x2c, origin, three CMP axes (forward, left, up), "
    "scale, model, flags; +0x28 unnamed",
)
GfxWorldLodChain = pc("GfxWorldLodChain")
GfxWorldLodInfo = pc("GfxWorldLodInfo")
Occluder = pc("Occluder")
GfxOutdoorBounds = pc("GfxOutdoorBounds")
GfxHeroLight = pc("GfxHeroLight", types={"type": "u8", "unused": "u8[3]"})
GfxHeroLightTree = pc("GfxHeroLightTree")
GfxWorldVertex = S(
    "GfxWorldVertex",
    16,
    F(0x0, "xyz", "vec3"),
    F(0xC, "binormalSign", "f32"),
    source="PS3 16-byte position stream (PC 44-byte vertex split; INFERRED, checked on data)",
)

# --------------------------------------------------------------------------------------------
# Vertex formats (docs/extract.md 3): chosen per surface from its flags.

XVertexFloat = S(
    "XVertexFloat",
    16,
    F(0x0, "xyz", "vec3"),
    F(0xC, "binormalSign", "f32"),
    source="docs/extract.md 3.2 (flags & 1 == 0)",
)
XVertexPacked = S(
    "XVertexPacked",
    8,
    F(0x0, "pos", "s16[3]"),
    F(0x6, "binormalSign", "s16"),
    source="docs/extract.md 3.2: position = posOffset + s16 * 2**posScaleExponent / 32768",
)
XVertexPackedNT = S(
    "XVertexPackedNT",
    16,
    F(0x0, "pos", "s16[3]"),
    F(0x6, "binormalSign", "s16"),
    F(0x8, "normal", "cmp"),
    F(0xC, "tangent", "cmp"),
    source="docs/extract.md 3.2 (formats 3, 7)",
)
XStreamNTUVC = S(
    "XStreamNTUVC",
    16,
    F(0x0, "normal", "cmp"),
    F(0x4, "tangent", "cmp"),
    F(0x8, "uv", "f16[2]"),
    F(0xC, "color", "u8[4]"),
    source="docs/extract.md 3.2 (0, 1, 2)",
)
XStreamNTUV = S(
    "XStreamNTUV",
    12,
    F(0x0, "normal", "cmp"),
    F(0x4, "tangent", "cmp"),
    F(0x8, "uv", "f16[2]"),
    source="docs/extract.md 3.2 (format 5)",
)
XStreamUVC = S(
    "XStreamUVC",
    8,
    F(0x0, "uv", "f16[2]"),
    F(0x4, "color", "u8[4]"),
    source="docs/extract.md 3.2 (format 3)",
)
XStreamUV = S("XStreamUV", 4, F(0x0, "uv", "f16[2]"), source="docs/extract.md 3.2 (format 7)")


def _flags(node: dict) -> int:
    return _struct.unpack_from(">H", node["raw"], 2)[0]


def _verts0(node: dict):
    flags = _flags(node)
    if flags & 1 == 0:
        return ArrayOf(XVertexFloat)
    return ArrayOf(XVertexPacked if flags & 3 == 1 else XVertexPackedNT)


_STREAMS = {
    0: XStreamNTUVC,
    1: XStreamNTUVC,
    2: XStreamNTUVC,
    5: XStreamNTUV,
    3: XStreamUVC,
    7: XStreamUV,
}


def _vertex_stream(node: dict):
    stream = _STREAMS.get(_flags(node) & 7)
    return ArrayOf(stream) if stream else None


# --------------------------------------------------------------------------------------------
# Node kinds: for a node with "_t" == kind, the schema of each byte-valued key.

U8, S8, U16, S16, U32, S32, F32 = (A(t) for t in ("u8", "s8", "u16", "s16", "u32", "s32", "f32"))

KINDS: dict[str, dict] = {
    "RawFile": {"header": RawFile, "buffer": U8},
    "StringTable": {"header": StringTable, "cell_index": S16},
    "StringTableCell": {"raw": StringTableCell},
    "LocalizeEntry": {"header": LocalizeEntry},
    "GfxImage": {"header": GfxImage, "pixels": U8},
    "Material": {"header": Material, "constants": A(MaterialConstantDef)},
    "MaterialTextureDef": {"raw": MaterialTextureDef},
    "water_t": {"raw": water_t, "H0": F32, "wTerm": F32, "wTermSum": F32},
    "MaterialStateBitsRef": {"raw": StateBitsRef, "bits": GfxStateBits},
    "MaterialTechniqueSet": {"header": MaterialTechniqueSet},
    "MaterialTechnique": {"head": MaterialTechnique},
    "MaterialPass": {"raw": MaterialPass, "vertex_decl": MaterialVertexDeclaration},
    "MaterialShaderArgument": {"raw": MaterialShaderArgument, "literal": Vec4},
    "MaterialVertexShader": {"header": MaterialVertexShader, "program": U32},
    "MaterialPixelShader": {"header": MaterialPixelShader, "program": U32},
    "XAnimParts": {
        "header": XAnimParts,
        "names": U16,
        "notify": A(XAnimNotifyInfo),
        "data_byte": U8,
        "data_short": S16,
        "data_int": S32,
        "random_data_short": S16,
        "random_data_byte": U8,
        "random_data_int": S32,
        "indices": U8,
    },
    "XAnimDeltaPart": {"raw": XAnimDeltaPart},
    "XAnimPartTrans": {
        "head": XAnimTransHead,
        "frame0": Vec3,
        "frames_head": XAnimTransFramesHead,
        "indices": U8,
        "frames": U8,
    },
    "XAnimDeltaPartQuat": {
        "head": XAnimQuatHead,
        "frame0": A("s16[2]"),
        "frames_head": XAnimQuatFramesHead,
        "indices": U8,
        "frames": A("s16[2]"),
    },
    "FxEffectDef": {"header": FxEffectDef},
    "FxElemDef": {
        "raw": FxElemDef,
        "vel_samples": A(FxElemVelStateSample),
        "vis_samples": A(FxElemVisStateSample),
    },
    "FxElemMarkVisuals": {"raw": FxElemMarkVisuals},
    "FxElemVisuals": {"raw": FxElemVisual},
    "FxTrailDef": {"raw": FxTrailDef, "verts": A(FxTrailVertex), "inds": U16},
    "FxImpactTable": {"header": FxImpactTable, "table_raw": A("ptr")},
    "SndBank": {
        "header": SndBank,
        "alias_index": A("u16[2]"),
        "radverbs": A(pc("snd_radverb")),
        "snapshots": A(pc("snd_snapshot")),
    },
    "snd_alias_list_t": {"raw": snd_alias_list_t},
    "snd_alias_t": {"raw": snd_alias_t},
    "SoundFile": {"raw": SoundFile},
    "LoadedSound": {"raw": LoadedSound, "seek_table": U32, "data": U8},
    "StreamedSound": {"raw": StreamedSound},
    "PrimedSound": {"raw": PrimedSound, "buffer": U8},
    "SndPatch": {"header": SndPatch, "elements": U32},
    "SndDriverGlobals": {
        "header": SndDriverGlobals,
        "groups": A(pc("snd_group")),
        "curves": A(pc("snd_curve")),
        "pans": A(pc("snd_pan")),
        "snapshot_groups": A(pc("snd_snapshot_group")),
        "contexts": A(pc("snd_context")),
        "masters": A(pc("snd_master")),
    },
    "WeaponVariantDef": {"header": WeaponVariantDef, "szXAnims_ptrs": A("ptr"), "hideTags": U16},
    "WeaponDef": {
        "raw": WeaponDef,
        "gunXModel_ptrs": A("ptr"),
        "worldModel_ptrs": A("ptr"),
        "notetrackSoundMapKeys": U16,
        "notetrackSoundMapValues": U16,
        "bounceSound_ptrs": A("ptr"),
        "parallelBounce": F32,
        "perpendicularBounce": F32,
        "aiVsAiAccuracyGraphKnots": A("vec2"),
        "originalAiVsAiAccuracyGraphKnots": A("vec2"),
        "aiVsPlayerAccuracyGraphKnots": A("vec2"),
        "originalAiVsPlayerAccuracyGraphKnots": A("vec2"),
        "locationDamageMultipliers": F32,
    },
    "FlameTable": {"raw": FlameTable},
    "Font_s": {"header": Font_s, "glyphs": A(Glyph)},
    "ddlRoot_t": {"header": ddlRoot_t},
    "ddlDef_t": {"raw": ddlDef_t},
    "ddlStructDef_t": {"raw": ddlStructDef_t},
    "ddlMemberDef_t": {"raw": ddlMemberDef_t},
    "ddlEnumDef_t": {"raw": ddlEnumDef_t, "member_ptrs": A("ptr")},
    "EmblemSet": {"header": EmblemSet, "layers": A(EmblemLayer), "background_lookup": S16},
    "EmblemCategory": {"raw": EmblemCategory},
    "EmblemIcon": {"raw": EmblemIcon},
    "EmblemBackground": {"raw": EmblemBackground},
    "Glasses": {"header": Glasses},
    "Glass": {"raw": Glass, "outline": A("vec2")},
    "GlassDef": {"raw": GlassDef},
    "PackIndex": {"header": PackIndex},
    "XGlobals": {"header": XGlobals},
    "TextureList": {"header": TextureList, "entries": U32},
    "MenuList": {"header": MenuList, "menu_ptrs": A("ptr")},
    "menuDef_t": {"header": menuDef_t, "item_ptrs": A("ptr")},
    "itemDef_s": {"raw": itemDef_s},
    "ExpressionStatement": {"raw": ExpressionStatement},
    "expressionRpn": {"raw": expressionRpn},
    "ScriptCondition": {"raw": ScriptCondition},
    "GenericEventScript": {"raw": GenericEventScript},
    "GenericEventHandler": {"raw": GenericEventHandler},
    "ItemKeyHandler": {"raw": ItemKeyHandler},
    "textDef_s": {"raw": textDef_s, "game_msg": gameMsgDef_s},
    "focusItemDef_s": {"raw": focusItemDef_s},
    "MenuTypeData": {"edit_field": editFieldDef_s},
    "listBoxDef_s": {"raw": listBoxDef_s},
    "MenuRow": {"raw": MenuRow, "event_name": A("char[32]"), "on_focus_event_name": A("char[32]")},
    "MenuCell": {"raw": MenuCell, "string_value": A("text")},
    "multiDef_s": {"raw": multiDef_s},
    "enumDvarDef_s": {"raw": enumDvarDef_s},
    "UIAnimInfo": {"raw": UIAnimInfo, "anim_state_ptrs": A("ptr")},
    "animParamsDef_t": {"raw": animParamsDef_t},
    "rectData_s": {"raw": rectData_s},
    "PhysPreset": {"header": PhysPreset},
    "PhysConstraints": {"header": PhysConstraints},
    "PhysConstraint": {"raw": PhysConstraint},
    "DestructibleDef": {"header": DestructibleDef},
    "DestructiblePiece": {"raw": DestructiblePiece},
    "XModel": {
        "header": XModel,
        "bone_names": U16,
        "parent_list": U8,
        "quats": A("s16[4]"),
        "trans": A("vec4"),
        "part_classification": U8,
        "base_mat": A(DObjAnimMat),
        "coll_surfs": A(XModelCollSurf),
        "bone_info": A(XBoneInfo),
        "high_mip_bounds": A(XModelHighMipBounds),
    },
    "XSurface": {
        "raw": XSurface,
        "verts_blend": U16,
        "tri_indices": A("u16[3]"),
        "verts0": Dynamic(_verts0, "by flags & 3"),
        "vertex_stream": Dynamic(_vertex_stream, "by flags & 7"),
        "tension_data": F32,
    },
    "XRigidVertList": {"raw": XRigidVertList},
    "XSurfaceCollisionTree": {
        "raw": XSurfaceCollisionTree,
        "nodes": A(XSurfaceCollisionNode),
        "leafs": U16,
    },
    "XModelMaterial": {"raw": S("MaterialHandle", 4, P(0, "material"))},
    "Collmap": {"raw": S("Collmap", 4, P(0, "geomList")), "geom_list": PhysGeomList},
    "PhysGeomInfo": {"raw": PhysGeomInfo},
    "BrushWrapper": {"raw": BrushWrapper, "verts": A("vec3")},
    "cbrushside_t": {"raw": cbrushside_t, "plane": cplane_s},
    "XModelPieces": {"header": XModelPieces},
    "XModelPiece": {"raw": XModelPiece},
    "ComWorld": {"header": ComWorld, "water_cells": A("s32[2]")},
    "ComPrimaryLight": {"raw": ComPrimaryLight},
    "ComBurnableCell": {"raw": ComBurnableCell, "data": U8},
    "GfxLightDef": {"header": GfxLightDef},
    "MapEnts": {"header": MapEnts, "entity_string": A("text")},
    "GameWorld": {
        "header": GameWorld,
        "chain_node_for_node": U16,
        "node_for_chain_node": U16,
        "path_vis": U8,
    },
    "pathnode_t": {"raw": pathnode_t, "links": A(pathlink_s)},
    "pathnode_tree_t": {"raw": pathnode_tree_t, "nodes": U16},
    "clipMap_t": {
        "header": clipMap_t,
        "planes": A(cplane_s),
        "static_model_list": A(cStaticModel),
        "materials": A(dmaterial_t),
        "brushsides": A(cbrushside_t),
        "nodes": A(cNode_t),
        "leafs": A(cLeaf_s),
        "leafbrushes": U16,
        "leafsurfaces": U32,
        "verts": A("vec3"),
        "brush_verts": A("vec3"),
        "uinds": U16,
        "tri_indices": A("u16[3]"),
        "tri_edge_is_walkable": U8,
        "borders": A(CollisionBorder),
        "partitions": A(CollisionPartition),
        "aabb_trees": A(CollisionAabbTree),
        "cmodels": A(cmodel_t),
        "brushes": A(cbrush_t),
        "visibility": U8,
        "box_brush": cbrush_t,
    },
    "cLeafBrushNode_s": {"raw": cLeafBrushNode, "brushes": U16},
    "DynEntityDef": {"raw": DynEntityDef},
    "GfxWorld": {
        "header": GfxWorld,
        "aabb_trees": A(GfxStreamingAabbTree),
        "leaf_refs": S32,
        "sky_start_surfs": S32,
        "coronas": A(GfxLightCorona),
        "shadow_map_volumes": A(GfxVolume16),
        "shadow_map_volume_planes": A(GfxVolumePlane),
        "exposure_volumes": A(GfxExposureVolume),
        "exposure_volume_planes": A(GfxVolumePlane),
        "planes": A(cplane_s),
        "nodes": U16,
        "vertices": A(GfxWorldVertex),
        "vertex_layer_data": U8,
        "indices": U16,
        "models": A(GfxBrushModel),
        "sorted_surf_index": U16,
        "smodel_insts": A(GfxStaticModelInst),
        "cull_groups": A(GfxCullGroup),
        "world_lod_chains": A(GfxWorldLodChain),
        "world_lod_infos": A(GfxWorldLodInfo),
        "world_lod_surfaces": U32,
        "water_buffer0": A("vec4"),
        "water_buffer1": A("vec4"),
        "occluders": A(Occluder),
        "outdoor_bounds": A(GfxOutdoorBounds),
        "hero_lights": A(GfxHeroLight),
        "hero_light_tree": A(GfxHeroLightTree),
    },
    "GfxLight": {"raw": GfxLight},
    "GfxLightGrid": {
        "row_data_start": U16,
        "raw_row_data": U8,
        "entries": A(GfxLightGridEntry),
        "colors": A(GfxLightGridColors),
    },
    "GfxCell": {"raw": GfxCell, "reflection_probes": U8},
    "GfxAabbTree": {"raw": GfxAabbTree, "smodel_indexes": U16},
    "GfxPortal": {"raw": GfxPortal, "vertices": A("vec3")},
    "GfxReflectionProbe": {"raw": GfxReflectionProbe, "probe_volumes": A(GfxReflectionProbeVolume)},
    "GfxLightmapArray": {"raw": GfxLightmapArray},
    "MaterialMemory": {"raw": MaterialMemory},
    "GfxShadowGeometry": {"raw": GfxShadowGeometry, "sorted_surf_index": U16, "smodel_index": U16},
    "GfxLightRegion": {"raw": GfxLightRegion},
    "GfxLightRegionHull": {"raw": GfxLightRegionHull, "axis": A(GfxLightRegionAxis)},
    "GfxSurface": {"raw": GfxSurface},
    "GfxStaticModelDrawInst": {"raw": GfxStaticModelDrawInst},
}
