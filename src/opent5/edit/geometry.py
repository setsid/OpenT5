"""Meshes for the viewers: world surfaces, collision, and model LOD0 (read only).

The unpacking is the exporter's (``opent5.export``); this only gathers it into arrays.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from opent5.edit.types import EditError
from opent5.export import collision as col
from opent5.export import vertex as vx
from opent5.export.nodes import AssetIndex, Resolver
from opent5.export.zone import ZoneExporter
from opent5.xfile.constants import AssetType as T
from opent5.xfile.schema import view


@dataclass
class Mesh:
    positions: np.ndarray  # (n, 3) float32
    triangles: np.ndarray  # (m, 3) int32
    normals: np.ndarray | None = None
    uvs: np.ndarray | None = None
    #: Per-triangle group number (surface, brush or model surface), when known.
    groups: np.ndarray | None = None
    notes: list[str] = field(default_factory=list)


def _exporter(xfile, zone_name: str) -> ZoneExporter:
    ex = ZoneExporter(b"", zone_name, ".", images=False, previews=False, log=lambda *_: None)
    ex.xfile = xfile
    ex.index = AssetIndex(xfile)
    ex.resolver = Resolver(ex.index, xfile)
    ex.script_strings = xfile.script_strings
    return ex


def _concat(parts: list[vx.Mesh]) -> Mesh:
    if not parts:
        return Mesh(np.zeros((0, 3), np.float32), np.zeros((0, 3), np.int32))
    positions, tris, groups, at = [], [], [], 0
    for n, m in enumerate(parts):
        positions.append(np.asarray(m.positions, np.float32))
        tris.append(np.asarray(m.triangles, np.int64) + at)
        groups.append(np.full(len(m.triangles), n, np.int32))
        at += len(m.positions)
    return Mesh(
        np.concatenate(positions),
        np.concatenate(tris).astype(np.int32),
        groups=np.concatenate(groups),
    )


def world(xfile, node: dict) -> Mesh:
    v = view(node)
    surfs = [vx.WorldSurface.from_fields(i, view(s).fields) for i, s in enumerate(node["surfaces"])]
    try:
        m, ranges = vx.world_mesh(
            v.array("vertices")["xyz"], bytes(node["vertex_layer_data"]), v.array("indices"), surfs
        )
    except ValueError as exc:
        raise EditError(f"world mesh of {node.get('name')}: {exc}") from exc
    groups = np.repeat(np.arange(len(ranges), dtype=np.int32), [n for _a, n in ranges])
    return Mesh(
        m.positions.astype(np.float32),
        m.triangles[:, [0, 2, 1]].astype(np.int32),
        m.normals,
        m.uvs,
        groups,
        [f"{len(surfs)} surfaces"],
    )


def collision(xfile, node: dict, zone_name: str) -> Mesh:
    ex = _exporter(xfile, zone_name)
    v = view(node)
    parts: list[vx.Mesh] = []
    notes = []
    if node.get("brushes") is not None:
        try:
            brushes = col.parse_brushes(v.array("brushes"), ex.brush_sides)
        except ValueError as exc:
            raise EditError(f"collision brushes of {node.get('name')}: {exc}") from exc
        for b in brushes:
            faces = col.brush_faces(b)
            if faces:
                p, t = col.triangulate(faces)
                parts.append(vx.Mesh(p, t))
        notes.append(f"{len(brushes)} brushes")
    tp, tt = col.collision_triangles(
        v.array("verts") if node.get("verts") is not None else None,
        v.array("tri_indices") if node.get("tri_indices") is not None else None,
    )
    if len(tt):
        parts.append(vx.Mesh(tp, tt))
        notes.append(f"{len(tt)} collision triangles")
    out = _concat(parts)
    out.notes = notes
    return out


def model(xfile, node: dict, zone_name: str) -> Mesh:
    ex = _exporter(xfile, zone_name)
    try:
        meshes = ex.model_lod0(node)
    except (ValueError, KeyError) as exc:
        raise EditError(f"model {node.get('name')}: {exc}") from exc
    out = _concat([m for _mat, m in meshes])
    out.notes = [f"LOD0, {len(meshes)} surfaces"]
    return out


def first_of(xfile, types: tuple[int, ...]) -> Any:
    for a in xfile.assets:
        if a.type in types and isinstance(a.data, dict):
            return a
    return None


WORLD = (T.GFX_MAP,)
COLLISION = (T.COL_MAP_MP, T.COL_MAP_SP)
