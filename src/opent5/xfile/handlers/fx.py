"""fx (30) and impactfx (31). docs/research/structs-content.md section 11.

FxEffectDef (60): +0 name, +0x10/+0x14/+0x18 elem counts (looping, one-shot,
emission), +0x1c elemDefs [nz] (align 4, LS 292 x total). FxElemDef pointers in
load order: velSamples, visSamples, visuals (by elemType and visualCount),
four effect refs by name, trailDef, spawnSound. FxImpactTable (8): +0 name,
+4 table [nz] (align 4, LS 21 x 140; 35 effect refs per entry).
Loaders: fx Ptr 0x24d748, FxEffectDef 0x24d498, FxElemDef 0x24d0a8, visuals
0x24cc90, single visual 0x24ca90, trail 0x236fb0; impactfx Ptr 0x24e2f8,
struct 0x24e140.
"""

from __future__ import annotations

from typing import Any

from opent5.xfile.constants import AssetType, Block
from opent5.xfile.handlers.base import Handler, asset_ref, register
from opent5.xfile.stream import Chunk, XStream

FX_ELEM_DEF_SIZE = 292
ELEM_TYPE_MODEL = 7
ELEM_TYPE_OMNI_LIGHT = 8
ELEM_TYPE_SPOT_LIGHT = 9
ELEM_TYPE_SOUND = 10
ELEM_TYPE_DECAL = 11
ELEM_TYPE_RUNNER = 12


def read_visual(st: XStream, chunk: Chunk, off: int, elem_type: int) -> Any:
    """One FxElemVisuals union member, by element type."""
    if elem_type == ELEM_TYPE_MODEL:
        return asset_ref(st, chunk, off, AssetType.XMODEL)
    if elem_type in (ELEM_TYPE_SOUND, ELEM_TYPE_RUNNER):
        return st.string(chunk, off)
    if elem_type in (ELEM_TYPE_OMNI_LIGHT, ELEM_TYPE_SPOT_LIGHT):
        return None
    return asset_ref(st, chunk, off, AssetType.MATERIAL)


def read_elem_def(st: XStream, e: Chunk) -> dict:
    elem_type, visual_count = e.u8(184), e.u8(185)
    out: dict = {"elem_type": elem_type, "visual_count": visual_count}
    out["vel_samples"] = None
    if st.follows(e, 188, owned=True):
        st.alloc(3)
        out["vel_samples"] = st.load(96 * (e.u8(186) + 1)).bytes()
    out["vis_samples"] = None
    if st.follows(e, 192, owned=True):
        st.alloc(3)
        out["vis_samples"] = st.load(48 * (e.u8(187) + 1)).bytes()
    visuals: Any = None
    if elem_type == ELEM_TYPE_DECAL:
        if st.follows(e, 196, owned=True):
            st.alloc(3)
            table = st.load(8 * visual_count)
            visuals = [
                (
                    asset_ref(st, table, 8 * i, AssetType.MATERIAL),
                    asset_ref(st, table, 8 * i + 4, AssetType.MATERIAL),
                )
                for i in range(visual_count)
            ]
    elif visual_count > 1:
        if st.follows(e, 196, owned=True):
            st.alloc(3)
            table = st.load(4 * visual_count)
            visuals = [read_visual(st, table, 4 * i, elem_type) for i in range(visual_count)]
    else:
        visuals = read_visual(st, e, 196, elem_type)
    out["visuals"] = visuals
    out["effect_on_impact"] = st.string(e, 224)
    out["effect_on_death"] = st.string(e, 228)
    out["effect_emitted"] = st.string(e, 232)
    out["effect_ref_252"] = st.string(e, 252)
    out["trail_def"] = None
    if st.follows(e, 256, owned=True):
        st.alloc(3)
        t = st.load(28)
        trail: dict = {"raw": t.bytes(), "verts": None, "inds": None}
        if st.follows(t, 16, owned=True):
            st.alloc(3)
            trail["verts"] = st.load(20 * t.s32(12)).bytes()
        if st.follows(t, 24, owned=True):
            st.alloc(1)
            trail["inds"] = st.load(2 * t.s32(20)).bytes()
        out["trail_def"] = trail
    out["spawn_sound"] = st.string(e, 280)
    out["raw"] = e.bytes()
    return out


def read_fx(st: XStream, h: Chunk) -> dict:
    st.push(Block.VIRTUAL)
    name = st.string(h, 0)
    counts = (h.s32(16), h.s32(20), h.s32(24))
    elems = None
    if st.follows(h, 0x1C, owned=True):
        n = sum(counts)
        st.alloc(3)
        table = st.load(FX_ELEM_DEF_SIZE * n)
        elems = [read_elem_def(st, e) for e in table.items(FX_ELEM_DEF_SIZE, n)]
    st.pop()
    return {
        "name": name,
        "elem_def_count_looping": counts[0],
        "elem_def_count_one_shot": counts[1],
        "elem_def_count_emission": counts[2],
        "elem_defs": elems,
        "header": h.bytes(),
    }


@register
class FxHandler(Handler):
    asset_type = AssetType.FX
    header_size = 60

    def read(self, st: XStream, header: Chunk) -> dict:
        return read_fx(st, header)


IMPACT_ROWS = 21
IMPACT_EFFECTS_PER_ROW = 35


@register
class ImpactFxHandler(Handler):
    asset_type = AssetType.IMPACTFX
    header_size = 8

    def read(self, st: XStream, h: Chunk) -> dict:
        st.push(Block.VIRTUAL)
        name = st.string(h, 0)
        table = None
        if st.follows(h, 4, owned=True):
            st.alloc(3)
            t = st.load(IMPACT_ROWS * IMPACT_EFFECTS_PER_ROW * 4)
            table = [
                [
                    asset_ref(st, t, 4 * (row * IMPACT_EFFECTS_PER_ROW + k), AssetType.FX)
                    for k in range(IMPACT_EFFECTS_PER_ROW)
                ]
                for row in range(IMPACT_ROWS)
            ]
        st.pop()
        return {"name": name, "table": table}
