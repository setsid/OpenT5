"""Check the converter against a map that exists on both platforms.

``compare_world(pc_content, pc_start, pc_first, pc_last, ps3_content)`` converts the PC
world assets (walked from the PC com_map, ``pc.walk_range``) with the converter's own
code and compares the result with the PS3 zone's assets field by field: every array
of the same-layout assets (clipMap_t, ComWorld, GameWorldMp, MapEnts) with pointer
fields masked, the GfxWorld header word by word, the GfxWorld arrays, the vertex
positions and layer data, the index buffer and every surface record. Used by
``tools/convert_map.py compare`` and the slow test; the numbers are in docs/convert.md.
"""

from __future__ import annotations

import re
import struct
from collections import Counter

import numpy as np

from opent5.convert import pc as pcmod
from opent5.convert import world
from opent5.convert.swap import CLIP_KEYS, COM_KEYS, GAME_KEYS, MAPENTS_KEYS, layout, swap_struct
from opent5.xfile import AssetType, parse
from opent5.xfile.schema import unpack_cmp_array


def _pointer_mask(struct_name: str, count: int) -> np.ndarray:
    """True on the bytes of pointer fields of ``count`` structs."""
    if struct_name == "bytes":
        return np.zeros(count, bool)
    size, fields = layout(struct_name)
    one = np.zeros(size, bool)
    for _, off, kind in fields:
        m = re.fullmatch(r"(\w+?)(?:\[(\d+)\])?", kind)
        if m.group(1) == "ptr":
            n = int(m.group(2) or 1)
            one[off : off + 4 * n] = True
    if struct_name == "cLeafBrushNode_s":
        one[8:12] = True  # the union: data.brushes, a pointer in every leaf node
    return np.tile(one, count)


def _diff_words(a: bytes, b: bytes, mask: np.ndarray | None = None) -> tuple[int, int]:
    """(differing 4-byte words, of which outside pointer fields)."""
    n = min(len(a), len(b)) // 4 * 4
    x = np.frombuffer(a[:n], np.uint8).reshape(-1, 4)
    y = np.frombuffer(b[:n], np.uint8).reshape(-1, 4)
    diff = (x != y).any(axis=1)
    if mask is None:
        return int(diff.sum()), int(diff.sum())
    m = mask[:n].reshape(-1, 4).any(axis=1)
    return int(diff.sum()), int((diff & ~m).sum())


def _cmp_node(pc_node: dict, ps3_node: dict, keys: dict[str, str], label: str, out: list) -> None:
    for key, struct_name in keys.items():
        a, b = pc_node.get(key), ps3_node.get(key)
        if a is None and b is None:
            continue
        if isinstance(a, list) or isinstance(b, list):
            a = b"".join(e["raw"] for e in a or [])
            b = b"".join(e["raw"] for e in b or [])
        a, b = bytes(a or b""), bytes(b or b"")
        size = layout(struct_name)[0] if struct_name != "bytes" else 1
        count = len(a) // size if size else 0
        mask = _pointer_mask(struct_name, count) if struct_name != "bytes" else None
        words, data = _diff_words(a, b, mask)
        out.append(
            {
                "array": f"{label}.{key}",
                "struct": struct_name,
                "pc_bytes": len(a),
                "ps3_bytes": len(b),
                "differing_words": words,
                "differing_non_pointer_words": data,
            }
        )


def compare_world(
    pc_content: bytes, pc_start: int, pc_first: int, pc_last: int, ps3_content: bytes
) -> dict:
    """See the module docstring. Returns a JSON-able report."""
    assets = pcmod.walk_range(pc_content, pc_start, pc_first, pc_last)
    P = {a.type: a.data for a in assets}
    x = parse(ps3_content, log=False)
    Q = {a.type: a.data for a in x.assets}
    report: dict = {"arrays": []}

    # Same-layout assets.
    pclip, pcom, pgame = P[AssetType.COL_MAP_MP], P[AssetType.COM_MAP], P[AssetType.GAME_MAP_MP]
    world.convert_com(pcom)
    world.convert_game(pgame)
    pents = pclip.get("map_ents")
    world.convert_clip(pclip)
    for node, ref, keys, label in (
        (pcom, Q[AssetType.COM_MAP], COM_KEYS, "com_map"),
        (pgame, Q[AssetType.GAME_MAP_MP], GAME_KEYS, "game_map_mp"),
        (pclip, Q[AssetType.COL_MAP_MP], CLIP_KEYS, "col_map_mp"),
        (pents, Q[AssetType.COL_MAP_MP]["map_ents"], MAPENTS_KEYS, "map_ents"),
    ):
        _cmp_node(node, ref, keys, label, report["arrays"])

    # GfxWorld.
    pg, qg = P[AssetType.GFX_MAP], Q[AssetType.GFX_MAP]
    pc_header = bytes(pg["header"])
    mine = world.gfx_header(pc_header, bytes(qg["header"]))
    theirs = bytes(qg["header"])
    report["gfx_header_differing_words"] = [
        {"offset": i, "converted": mine[i : i + 4].hex(), "ps3": theirs[i : i + 4].hex()}
        for i in range(0, len(mine), 4)
        if mine[i : i + 4] != theirs[i : i + 4]
    ]
    for key, struct_name in (
        ("planes", "cplane_s"),
        ("nodes", "u16"),
        ("aabb_trees", "GfxStreamingAabbTree"),
        ("leaf_refs", "u32"),
        ("models", "GfxBrushModel"),
        ("sorted_surf_index", "u16"),
        ("smodel_insts", "GfxStaticModelInst"),
    ):
        a = swap_struct(struct_name, pg[key]) if pg.get(key) is not None else b""
        b = bytes(qg.get(key) or b"")
        words, _ = _diff_words(a, b)
        report["arrays"].append(
            {
                "array": f"gfx_map.{key}",
                "struct": struct_name,
                "pc_bytes": len(a),
                "ps3_bytes": len(b),
                "differing_words": words,
            }
        )
    cells_a = b"".join(swap_struct("GfxCell", c["raw"]) for c in pg["cells"])
    cells_b = b"".join(c["raw"] for c in qg["cells"])
    words, data = _diff_words(cells_a, cells_b, _pointer_mask("GfxCell", len(pg["cells"])))
    report["arrays"].append(
        {
            "array": "gfx_map.cells",
            "struct": "GfxCell",
            "pc_bytes": len(cells_a),
            "ps3_bytes": len(cells_b),
            "differing_words": words,
            "differing_non_pointer_words": data,
        }
    )
    for key, struct_name in (
        ("row_data_start", "u16"),
        ("raw_row_data", "rows"),
        ("entries", "GfxLightGridEntry"),
        ("colors", "bytes"),
    ):
        if struct_name == "rows":
            a = world.light_grid_rows(pg["light_grid"][key], pg["light_grid"]["row_data_start"])
        else:
            a = swap_struct(struct_name, pg["light_grid"][key])
        b = bytes(qg["light_grid"][key])
        words, _ = _diff_words(a, b)
        report["arrays"].append(
            {
                "array": f"gfx_map.light_grid.{key}",
                "struct": struct_name,
                "pc_bytes": len(a),
                "ps3_bytes": len(b),
                "differing_words": words,
            }
        )
    sg_a = b"".join(swap_struct("GfxShadowGeometry", e["raw"]) for e in pg["shadow_geom"])
    sg_b = b"".join(e["raw"] for e in qg["shadow_geom"])
    words, data = _diff_words(
        sg_a, sg_b, _pointer_mask("GfxShadowGeometry", len(pg["shadow_geom"]))
    )
    report["arrays"].append(
        {
            "array": "gfx_map.shadow_geom",
            "struct": "GfxShadowGeometry",
            "pc_bytes": len(sg_a),
            "ps3_bytes": len(sg_b),
            "differing_words": words,
            "differing_non_pointer_words": data,
        }
    )

    # Vertices, groups, surfaces, indices.
    surfaces = [world.PCSurface.parse(i, e["raw"]) for i, e in enumerate(pg["surfaces"])]
    layer_pc = bytes(pg["vertex_layer_data"])
    groups = world.plan_groups(surfaces, len(layer_pc))
    positions, layer, notes = world.convert_vertices(bytes(pg["vertices"]), layer_pc, groups, False)
    qs = qg["surfaces"]
    words = [bytes(s["raw"][0x40:0x44]) for s in qs]
    records, indices = world.convert_surfaces(
        surfaces, bytes(pg["indices"]), groups, words, None, bytes(pg["vertices"])
    )
    per_field = Counter()
    names = {
        0x0: "mins",
        0x4: "mins",
        0x8: "mins",
        0xC: "vertexLayerData",
        0x10: "maxs",
        0x14: "maxs",
        0x18: "maxs",
        0x1C: "firstVertex",
        0x20: "vertexOffset",
        0x24: "vertexCount/triCount",
        0x28: "baseIndex",
        0x2C: "himipRadiusSq",
        0x30: "stream2ByteOffset",
        0x34: "zero",
        0x38: "zero",
        0x3C: "zero",
        0x40: "material",
        0x44: "lightmap/probe/light/flags",
    }
    for a, b in zip(records, qs, strict=False):
        b = bytes(b["raw"])
        for off in range(0, 0x60, 4):
            if a[off : off + 4] != b[off : off + 4]:
                per_field[names.get(off, "bounds")] += 1
    ps3_layer = bytes(qg["vertex_layer_data"])
    report["surfaces"] = {
        "pc": len(surfaces),
        "ps3": len(qs),
        "differing_fields": dict(per_field),
    }
    report["vertex_groups"] = len(groups)
    report["vertices"] = {
        "pc": len(pg["vertices"]) // 44,
        "ps3": len(qg["vertices"]) // 16,
        "positions_identical": positions == bytes(qg["vertices"]),
    }
    report["indices"] = {
        "pc": len(pg["indices"]) // 2,
        "ps3": len(qg["indices"]) // 2,
        "identical": indices == bytes(qg["indices"]),
    }
    report["layer_data"] = _layer_report(groups, layer, ps3_layer, notes)
    return report


def _layer_report(groups, mine: bytes, theirs: bytes, notes: list[str]) -> dict:
    out = {
        "bytes": len(mine),
        "ps3_bytes": len(theirs),
        "identical": mine == theirs,
        "groups": len(groups),
        "notes": notes,
    }
    if len(mine) != len(theirs):
        return out
    fields = Counter()
    extras_equal = extras_total = 0
    angles = []
    vertices = 0
    for g in groups:
        a = np.frombuffer(mine, np.uint8, g.count * g.stride, g.layer).reshape(g.count, g.stride)
        b = np.frombuffer(theirs, np.uint8, g.count * g.stride, g.layer).reshape(g.count, g.stride)
        vertices += g.count
        for name, lo, hi in (("colour", 0, 4), ("uv", 4, 12), ("lmap_uv", 12, 20)):
            fields[name] += int((a[:, lo:hi] != b[:, lo:hi]).any(axis=1).sum())
        for name, lo in (("normal", 20), ("tangent", 24)):
            wa = a[:, lo : lo + 4].copy().view(">u4").ravel()
            wb = b[:, lo : lo + 4].copy().view(">u4").ravel()
            fields[name + "_words_differing"] += int((wa != wb).sum())
            va = unpack_cmp_array(wa).astype(np.float64)
            vb = unpack_cmp_array(wb).astype(np.float64)
            na = va / np.maximum(np.linalg.norm(va, axis=1, keepdims=True), 1e-9)
            nb = vb / np.maximum(np.linalg.norm(vb, axis=1, keepdims=True), 1e-9)
            ang = np.degrees(np.arccos(np.clip((na * nb).sum(axis=1), -1, 1)))
            angles.append(float(ang.max()) if len(ang) else 0.0)
        if g.extra:
            extras_total += 1
            extras_equal += bool(np.array_equal(a[:, 28:], b[:, 28:]))
    out.update(
        {
            "vertices": vertices,
            "vertices_differing": dict(fields),
            "normal_tangent_max_angle_degrees": max(angles) if angles else 0.0,
            "groups_with_extras": extras_total,
            "groups_with_extras_identical": extras_equal,
            "strides": dict(Counter(g.stride for g in groups)),
        }
    )
    return out


def first_word(data: bytes, off: int) -> int:
    return struct.unpack_from(">I", data, off)[0]
