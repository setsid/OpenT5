"""Byte-order swap of structs whose PC and PS3 layouts are the same.

Field types come from the PC layouts (``opent5.xfile.layouts_pc``, generated from
OpenAssetTools src/Common/Game/T5/T5_Assets.h) plus the definitions and corrections in
``EXTRA``. Every field wider than a byte is reversed in place; byte fields, char arrays
and bitfield runs stay as they are. Evidence that this is the whole conversion for
clipMap_t, ComWorld, GameWorldMp and MapEnts: PC and PS3 mp_nuked compared array by array
after the swap differ only in pointer words (docs/research/box-map.md 3.1; the test
``tests/test_convert_pc.py`` repeats the comparison when the zones are present).
"""

from __future__ import annotations

import re

import numpy as np

from opent5.xfile.layouts_pc import PC_LAYOUTS

Layout = tuple[int, tuple[tuple[str, int, str], ...]]

#: Structs missing from (or wrong in) the flattened PC layouts.
EXTRA: dict[str, Layout] = {
    # OpenAssetTools T5_Assets.h cLeaf_t (44), as cmodel_t.leaf.
    "cLeaf_t": (
        44,
        (
            ("firstCollAabbIndex", 0, "u16"),
            ("collAabbCount", 2, "u16"),
            ("brushContents", 4, "s32"),
            ("terrainContents", 8, "s32"),
            ("mins", 12, "vec3"),
            ("maxs", 24, "vec3"),
            ("leafBrushNode", 36, "s32"),
            ("cluster", 40, "s16"),
        ),
    ),
    # OpenAssetTools cbrush_t (96): sides +0x20, verts +0x58.
    "cbrush_t": (
        96,
        (
            ("mins", 0, "vec3"),
            ("contents", 12, "s32"),
            ("maxs", 16, "vec3"),
            ("numsides", 28, "u32"),
            ("sides", 32, "ptr"),
            ("axial_cflags", 36, "s32[6]"),
            ("axial_sflags", 60, "s32[6]"),
            ("numverts", 84, "u32"),
            ("verts", 88, "ptr"),
        ),
    ),
    # The flattened layout keeps the union's first member (data.brushes); the other is
    # {f32 dist; f32 range; u16 childOffset[2]}. Evidence: PS3 mp_nuked leafbrushNodes
    # +0xc 3e164000 / PC 0040163e, +0x10 0017002c / PC 17002c00 (box-map.md 3.1).
    "cLeafBrushNode_s": (
        20,
        (
            ("axis", 0, "s8"),
            ("leafBrushCount", 2, "s16"),
            ("contents", 4, "s32"),
            ("data.brushes_or_dist", 8, "u32"),
            ("data.range", 12, "f32"),
            ("data.childOffset", 16, "u16[2]"),
        ),
    ),
    "CollisionAabbTree": (
        32,
        (
            ("origin", 0, "vec3"),
            ("materialIndex", 12, "u16"),
            ("childCount", 14, "u16"),
            ("halfSize", 16, "vec3"),
            ("u", 28, "s32"),
        ),
    ),
    "GfxLightGridEntry": (
        4,
        (("colorsIndex", 0, "u16"), ("primaryLightIndex", 2, "u8"), ("needsTrace", 3, "u8")),
    ),
    "GfxLightRegionAxis": (
        20,
        (("dir", 0, "vec3"), ("midPoint", 12, "f32"), ("halfSize", 16, "f32")),
    ),
    "u16": (2, (("v", 0, "u16"),)),
    "u32": (4, (("v", 0, "u32"),)),
    "vec3": (12, (("v", 0, "vec3"),)),
    "bytes": (1, ()),
}

_WIDTH = {
    "u8": (1, 1),
    "s8": (1, 1),
    "char": (1, 1),
    "u16": (2, 1),
    "s16": (2, 1),
    "u32": (4, 1),
    "s32": (4, 1),
    "f32": (4, 1),
    "ptr": (4, 1),
    "u64": (8, 1),
    "vec2": (4, 2),
    "vec3": (4, 3),
    "vec4": (4, 4),
    "mat3": (4, 9),
    "mat4": (4, 16),
}


def layout(name: str) -> Layout:
    found = EXTRA.get(name) or PC_LAYOUTS.get(name)
    if found is None:
        raise KeyError(f"struct {name!r}: no PC layout")
    return found


_PLANS: dict[str, tuple[int, np.ndarray]] = {}


def _plan(name: str) -> tuple[int, np.ndarray]:
    """(struct size, permutation of one struct's byte indices that swaps every field)."""
    plan = _PLANS.get(name)
    if plan is not None:
        return plan
    size, fields = layout(name)
    perm = np.arange(size)
    for _, off, kind in fields:
        m = re.fullmatch(r"(\w+?)(?:\[(\d+)\])?", kind)
        base, n = m.group(1), int(m.group(2) or 1)
        if base.startswith("bits_"):
            continue
        width, per = _WIDTH.get(base, (1, 1))
        if width == 1:
            continue
        for k in range(n * per):
            at = off + k * width
            perm[at : at + width] = perm[at : at + width][::-1].copy()
    _PLANS[name] = plan = (size, perm)
    return plan


def swap_struct(name: str, data: bytes | bytearray | memoryview, count: int | None = None) -> bytes:
    """Swap every field of ``count`` consecutive ``name`` structs (all of ``data`` when
    count is None)."""
    data = bytes(data)
    if name == "bytes":
        return data
    size, perm = _plan(name)
    if count is None:
        count, rest = divmod(len(data), size)
        if rest:
            raise ValueError(f"{name}: {len(data)} bytes is not a whole number of {size}")
    elif count * size != len(data):
        raise ValueError(
            f"{name}: expected {count} x {size} = {count * size} bytes, found {len(data)}"
        )
    if not count:
        return data
    a = np.frombuffer(data, np.uint8).reshape(count, size)
    return a[:, perm].tobytes()


#: node key -> struct of its bytes (or of each element's "raw"), per asset kind.
CLIP_KEYS = {
    "header": "clipMap_t",
    "planes": "cplane_s",
    "static_model_list": "cStaticModel_s",
    "materials": "dmaterial_t",
    "brushsides": "cbrushside_t",
    "nodes": "cNode_t",
    "leafs": "cLeaf_t",
    "leafbrushes": "u16",
    "leafbrush_nodes": "cLeafBrushNode_s",
    "leafsurfaces": "u32",
    "verts": "vec3",
    "brush_verts": "vec3",
    "uinds": "u16",
    "tri_indices": "u16",
    "tri_edge_is_walkable": "bytes",
    "borders": "CollisionBorder",
    "partitions": "CollisionPartition",
    "aabb_trees": "CollisionAabbTree",
    "cmodels": "cmodel_t",
    "brushes": "cbrush_t",
    "visibility": "bytes",
    "box_brush": "cbrush_t",
    "dyn_ent_def_list0": "DynEntityDef",
    "dyn_ent_def_list1": "DynEntityDef",
    "constraints": "PhysConstraint",
}
COM_KEYS = {
    "header": "ComWorld",
    "primary_lights": "ComPrimaryLight",
    "water_cells": "bytes",
    "burnable_cells": "ComBurnableCell",
}
GAME_KEYS = {
    "header": "GameWorldMp",
    "nodes": "pathnode_t",
    "chain_node_for_node": "u16",
    "node_for_chain_node": "u16",
    "path_vis": "bytes",
    "node_tree": "pathnode_tree_t",
}
MAPENTS_KEYS = {"header": "MapEnts", "entity_string": "bytes"}
#: (element struct, child key) -> struct of the child's bytes.
SUB_KEYS = {
    ("cLeafBrushNode_s", "brushes"): "u16",
    ("pathnode_t", "links"): "pathlink_s",
    ("pathnode_tree_t", "nodes"): "u16",
    ("ComBurnableCell", "data"): "bytes",
}


def swap_node(node: dict, keys: dict[str, str], skip: tuple[str, ...] = ()) -> dict:
    """Swap a parsed PC node in place: each known key's bytes, or each element's "raw"
    and its known children. Keys not in ``keys`` are left alone."""
    for key, value in list(node.items()):
        name = keys.get(key)
        if name is None or key in skip:
            continue
        if isinstance(value, bytes | bytearray | memoryview):
            node[key] = swap_struct(name, value)
        elif isinstance(value, list):
            for element in value:
                if isinstance(element, dict) and "raw" in element:
                    element["raw"] = swap_struct(name, element["raw"])
                    for child, child_value in list(element.items()):
                        sub = SUB_KEYS.get((name, child))
                        if sub and isinstance(child_value, bytes | bytearray | memoryview):
                            element[child] = swap_struct(sub, child_value)
    return node
