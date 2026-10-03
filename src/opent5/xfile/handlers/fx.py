"""fx (30) and impactfx (31). docs/research/structs-content.md section 11.

FxEffectDef (60): +0 name, +0x10/+0x14/+0x18 elem counts (looping, one-shot,
emission), +0x1c elemDefs [nz] (align 4, LS 292 x total). FxElemDef pointers in
load order: velSamples, visSamples, visuals (by elemType and visualCount),
four effect refs by name, trailDef, spawnSound. FxImpactTable (8): +0 name,
+4 table [nz] (align 4, LS 21 x 140; 35 effect refs per entry).
Loaders: fx Ptr 0x24d748, FxEffectDef 0x24d498, FxElemDef 0x24d0a8, visuals
0x24cc90, single visual 0x24ca90, trail 0x236fb0; impactfx Ptr 0x24e2f8,
struct 0x24e140.

Node: "header", "name", "elem_defs" (elements: "raw", "vel_samples",
"vis_samples", "visual" or "visuals", effect names, "trail_def", "spawn_sound").
"""

from __future__ import annotations

from typing import Any

from opent5.xfile.constants import AssetType, Block
from opent5.xfile.handlers.base import Handler, array, asset_ref, items, register
from opent5.xfile.stream import Chunk, XStream

FX_ELEM_DEF_SIZE = 292
ELEM_TYPE_MODEL = 7
ELEM_TYPE_OMNI_LIGHT = 8
ELEM_TYPE_SPOT_LIGHT = 9
ELEM_TYPE_SOUND = 10
ELEM_TYPE_DECAL = 11
ELEM_TYPE_RUNNER = 12


def visual(io: XStream, chunk: Chunk, off: int, elem_type: int, node: Any, key: Any) -> None:
    """One FxElemVisuals union member, by element type."""
    if elem_type == ELEM_TYPE_MODEL:
        asset_ref(io, chunk, off, AssetType.XMODEL, node, key)
    elif elem_type in (ELEM_TYPE_SOUND, ELEM_TYPE_RUNNER):
        io.string(chunk, off, node, key)
    elif elem_type not in (ELEM_TYPE_OMNI_LIGHT, ELEM_TYPE_SPOT_LIGHT):
        asset_ref(io, chunk, off, AssetType.MATERIAL, node, key)


def elem_def_body(io: XStream, e: Chunk, node: dict) -> None:
    elem_type, visual_count = e.u8(184), e.u8(185)
    array(io, e, 188, 3, 96 * (e.u8(186) + 1), node, "vel_samples", owned=True)
    array(io, e, 192, 3, 48 * (e.u8(187) + 1), node, "vis_samples", owned=True)
    if elem_type == ELEM_TYPE_DECAL:
        for d, decal in (
            items(
                io,
                e,
                196,
                3,
                8,
                visual_count,
                node,
                "visuals",
                owned=True,
                kind="FxElemMarkVisuals",
            )
            or ()
        ):
            asset_ref(io, d, 0, AssetType.MATERIAL, decal, "material0")
            asset_ref(io, d, 4, AssetType.MATERIAL, decal, "material1")
    elif visual_count > 1:
        for v, element in (
            items(io, e, 196, 3, 4, visual_count, node, "visuals", owned=True, kind="FxElemVisuals")
            or ()
        ):
            visual(io, v, 0, elem_type, element, "visual")
    else:
        visual(io, e, 196, elem_type, node, "visual")
    io.string(e, 224, node, "effect_on_impact")
    io.string(e, 228, node, "effect_on_death")
    io.string(e, 232, node, "effect_emitted")
    io.string(e, 252, node, "effect_ref_252")
    trail = None
    if io.follows(e, 256, owned=True):
        io.alloc(3)
        trail = {"_t": "FxTrailDef"} if io.reading else node["trail_def"]
        t = io.load(28, trail, "raw")
        array(io, t, 16, 3, 20 * t.s32(12), trail, "verts", owned=True)
        array(io, t, 24, 1, 2 * t.s32(20), trail, "inds", owned=True)
    io.note(node, "trail_def", trail)
    io.string(e, 280, node, "spawn_sound")


def fx_body(io: XStream, h: Chunk, node: dict) -> None:
    io.push(Block.VIRTUAL)
    io.string(h, 0, node, "name")
    count = h.s32(16) + h.s32(20) + h.s32(24)
    elems = items(
        io, h, 0x1C, 3, FX_ELEM_DEF_SIZE, count, node, "elem_defs", owned=True, kind="FxElemDef"
    )
    for e, element in elems or ():
        elem_def_body(io, e, element)
    io.pop()


@register
class FxHandler(Handler):
    kind = "FxEffectDef"
    asset_type = AssetType.FX
    header_size = 60

    def body(self, io: XStream, header: Chunk, node: dict) -> None:
        fx_body(io, header, node)


IMPACT_ROWS = 21
IMPACT_EFFECTS_PER_ROW = 35
IMPACT_EFFECTS = IMPACT_ROWS * IMPACT_EFFECTS_PER_ROW


@register
class ImpactFxHandler(Handler):
    """FxImpactTable; node "table" holds 735 effect refs (21 rows of 35)."""

    kind = "FxImpactTable"
    asset_type = AssetType.IMPACTFX
    header_size = 8

    def body(self, io: XStream, h: Chunk, node: dict) -> None:
        io.push(Block.VIRTUAL)
        io.string(h, 0, node, "name")
        table = array(io, h, 4, 3, 4 * IMPACT_EFFECTS, node, "table_raw", owned=True)
        if table is not None:
            effects = io.children(node, "table")
            for i in range(IMPACT_EFFECTS):
                if io.reading:
                    effects.append(None)
                asset_ref(io, table, 4 * i, AssetType.FX, effects, i)
        io.pop()
