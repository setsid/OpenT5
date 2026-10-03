"""Meshes for the geometry view: world surfaces, collision, models.

    from opent5.gui import geometry
    m = geometry.mesh(doc, "world", None)      # the zone's first GfxWorld
    m = geometry.mesh(doc, "collision", 1234)   # a clipMap by asset index
    m = geometry.mesh(doc, "model", 56)         # an XModel's LOD0

``doc`` is anything with an ``xfile`` (the backend adapter or a ZoneDoc). The
decoding is the exporter's (``opent5.export``): GfxWorld surfaces through
``vertex.world_mesh``, clipMap brushes as convex faces plus the collision
triangles, XModel LOD0 surfaces through the XSurface vertex formats. Results
are cached per parsed zone.

``MeshData.groups`` holds one id per triangle: the surface index (world,
model), the brush index (collision brushes) or -1 (collision triangles).
"""

from __future__ import annotations

from typing import Any

import numpy as np

from opent5.gui.backend import EditError, MeshData
from opent5.xfile.constants import AssetType as T

WORLD_TYPES = (T.GFX_MAP,)
COLLISION_TYPES = (T.COL_MAP_MP, T.COL_MAP_SP)
KIND_OF_TYPE = {T.GFX_MAP: "world", T.COL_MAP_MP: "collision", T.COL_MAP_SP: "collision",
                T.XMODEL: "model"}  # fmt: skip

#: Per parsed zone: an exporter shell (index, resolver, model cache) and the meshes.
_helpers: dict[int, tuple[Any, Any]] = {}
_cache: dict[tuple[int, str, Any], MeshData] = {}


def kind_for_type(asset_type: int) -> str | None:
    return KIND_OF_TYPE.get(int(asset_type))


def _helper(xfile):
    """A ZoneExporter without an output folder: only its decoding methods are used."""
    found = _helpers.get(id(xfile))
    if found is not None and found[0] is xfile:
        return found[1]
    from opent5.export.nodes import AssetIndex, Resolver
    from opent5.export.zone import ZoneExporter

    h = ZoneExporter.__new__(ZoneExporter)
    h.xfile = xfile
    h.index = AssetIndex(xfile)
    h.resolver = Resolver(h.index, xfile)
    h.model_meshes = {}
    h.script_strings = xfile.script_strings
    _helpers.clear()  # one zone's helper at a time; it holds the parse alive
    _helpers[id(xfile)] = (xfile, h)
    return h


def _xfile(doc):
    xfile = getattr(doc, "xfile", None)
    if xfile is None:
        raise EditError("geometry needs the parsed zone, which this document does not expose")
    return xfile


def _pick(xfile, types, index: int | None, what: str):
    if index is not None:
        a = xfile.assets[index]
        if a.type not in types:
            raise EditError(f"asset {index} is a {a.type_name}; expected {what}")
        return a
    for a in xfile.assets:
        if a.type in types and a.data is not None:
            return a
    raise EditError(f"this zone has no {what}")


def mesh(doc, kind: str, index: int | None = None) -> MeshData:
    xfile = _xfile(doc)
    if index is not None and kind not in ("world", "collision", "model"):
        kind = kind_for_type(xfile.assets[index].type) or kind
    key = (id(xfile), kind, index)
    if key in _cache:
        return _cache[key]
    if kind == "world":
        out = world_mesh(xfile, _pick(xfile, WORLD_TYPES, index, "world geometry (gfx_map)"))
    elif kind == "collision":
        out = collision_mesh(xfile, _pick(xfile, COLLISION_TYPES, index, "collision (col_map)"))
    elif kind == "model":
        if index is None:
            raise EditError("a model mesh needs the model's asset index")
        a = _pick(xfile, (T.XMODEL,), index, "an xmodel")
        out = _model(xfile, a.data, a.name or f"xmodel {index}")
    else:
        raise EditError(f"geometry kind {kind!r}: expected world, collision or model")
    _cache[key] = out
    return out


def model_mesh(zdoc, node, name: str) -> MeshData:
    """LOD0 of an XModel node loaded inside another asset."""
    xfile = _xfile(zdoc)
    key = (id(xfile), "model", ("inline", name))
    if key not in _cache:
        _cache[key] = _model(xfile, node, name)
    return _cache[key]


def world_mesh(xfile, asset) -> MeshData:
    from opent5.export import vertex as vx
    from opent5.xfile.schema import view

    g = asset.data
    v = view(g)
    surfs = [vx.WorldSurface.from_fields(i, view(s).fields) for i, s in enumerate(g["surfaces"])]
    try:
        m, ranges = vx.world_mesh(
            v.array("vertices")["xyz"], bytes(g["vertex_layer_data"]), v.array("indices"), surfs
        )
    except ValueError as exc:
        raise EditError(f"world mesh of {g.get('name')}: {exc}") from exc
    groups = np.repeat(np.arange(len(ranges), dtype=np.int32), [n for _a, n in ranges])
    return MeshData(
        m.positions.astype(np.float32),
        m.triangles.astype(np.int32),
        m.normals,
        m.uvs,
        groups,
        label=g.get("name") or asset.name or "world",
        notes=[f"{len(surfs)} surfaces"],
    )


def collision_mesh(xfile, asset) -> MeshData:
    from opent5.export import collision as col
    from opent5.xfile.schema import view

    h = _helper(xfile)
    c = asset.data
    v = view(c)
    notes = []
    try:
        brushes = (
            col.parse_brushes(v.array("brushes"), h.brush_sides)
            if c.get("brushes") is not None
            else []
        )
    except ValueError as exc:
        notes.append(f"brushes: {exc}")
        brushes = []
    positions, tris, groups, at = [], [], [], 0
    for i, b in enumerate(brushes):
        faces = brush_faces(b)
        if not faces:
            continue
        p, t = col.triangulate(faces)
        positions.append(np.asarray(p, np.float32))
        tris.append(t + at)
        groups.append(np.full(len(t), i, np.int32))
        at += len(p)
    tp, tt = col.collision_triangles(
        v.array("verts") if c.get("verts") is not None else None,
        v.array("tri_indices") if c.get("tri_indices") is not None else None,
    )
    if len(tt):
        positions.append(np.asarray(tp, np.float32))
        tris.append(tt + at)
        groups.append(np.full(len(tt), -1, np.int32))
    if not positions:
        raise EditError(f"collision {c.get('name')}: no brushes and no triangles")
    notes.insert(0, f"{len(brushes)} brushes, {len(tt)} collision triangles")
    return MeshData(
        np.concatenate(positions),
        np.concatenate(tris).astype(np.int32),
        groups=np.concatenate(groups),
        label=c.get("name") or asset.name or "collision",
        notes=notes,
    )


def _model(xfile, node, name: str) -> MeshData:
    h = _helper(xfile)
    try:
        parts = h.model_lod0(node)
    except (ValueError, KeyError, TypeError) as exc:
        raise EditError(f"model {name}: LOD0 does not decode ({exc})") from exc
    if not parts:
        raise EditError(f"model {name}: no surfaces in LOD0")
    positions, tris, groups, at = [], [], [], 0
    for i, (_mat, m) in enumerate(parts):
        positions.append(m.positions.astype(np.float32))
        tris.append(m.triangles + at)
        groups.append(np.full(len(m.triangles), i, np.int32))
        at += len(m.positions)
    return MeshData(
        np.concatenate(positions),
        np.concatenate(tris).astype(np.int32),
        groups=np.concatenate(groups),
        label=name,
        notes=[f"LOD0, {len(parts)} surfaces"],
    )


# -- brushes, fast ---------------------------------------------------------------------------
# The same construction as opent5.export.collision.brush_faces (each plane's big square
# clipped by every other plane), on plain floats: the brushes are small polygons, and
# per-call numpy overhead made a whole map take ten seconds or more.

_EPS = 0.01
_WELD2 = 0.01 * 0.01
_MIN_AREA = 0.01


def _clip_poly(poly, nx, ny, nz, dist):
    ds = [px * nx + py * ny + pz * nz - dist for px, py, pz in poly]
    if all(d <= _EPS for d in ds):
        return poly
    if not any(d <= _EPS for d in ds):
        return []
    out = []
    n = len(poly)
    for i in range(n):
        a, b = poly[i], poly[(i + 1) % n]
        da, db = ds[i], ds[(i + 1) % n]
        if da <= _EPS:
            out.append(a)
        if (da <= _EPS) != (db <= _EPS):
            t = da / (da - db)
            out.append((a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1]),
                        a[2] + t * (b[2] - a[2])))  # fmt: skip
    return out


def _square(nx, ny, nz, dist, size):
    ln = (nx * nx + ny * ny + nz * nz) ** 0.5
    nx, ny, nz = nx / ln, ny / ln, nz / ln
    dist = dist / ln
    upx, upy, upz = (0.0, 0.0, 1.0) if abs(nz) < 0.9 else (1.0, 0.0, 0.0)
    ux, uy, uz = upy * nz - upz * ny, upz * nx - upx * nz, upx * ny - upy * nx
    lu = (ux * ux + uy * uy + uz * uz) ** 0.5
    ux, uy, uz = ux / lu, uy / lu, uz / lu
    vx, vy, vz = ny * uz - nz * uy, nz * ux - nx * uz, nx * uy - ny * ux
    cx, cy, cz = nx * dist, ny * dist, nz * dist
    return [
        (cx + (su * ux + sv * vx) * size, cy + (su * uy + sv * vy) * size,
         cz + (su * uz + sv * vz) * size)
        for su, sv in ((-1, -1), (1, -1), (1, 1), (-1, 1))
    ]  # fmt: skip


def _area2(poly) -> float:
    ax, ay, az = poly[0]
    total = 0.0
    for i in range(1, len(poly) - 1):
        bx, by, bz = poly[i][0] - ax, poly[i][1] - ay, poly[i][2] - az
        cx, cy, cz = poly[i + 1][0] - ax, poly[i + 1][1] - ay, poly[i + 1][2] - az
        x, y, z = by * cz - bz * cy, bz * cx - bx * cz, bx * cy - by * cx
        total += (x * x + y * y + z * z) ** 0.5
    return 0.5 * total


def brush_faces(brush) -> list[np.ndarray]:
    """Convex faces of a brush (as opent5.export.collision.brush_faces)."""
    mins = [float(v) for v in brush.mins]
    maxs = [float(v) for v in brush.maxs]
    planes = []
    for k in range(3):
        n = [0.0, 0.0, 0.0]
        n[k] = 1.0
        planes.append((n[0], n[1], n[2], maxs[k]))
        planes.append((-n[0], -n[1], -n[2], -mins[k]))
    for n, d in brush.planes:
        planes.append((float(n[0]), float(n[1]), float(n[2]), float(d)))
    if len(planes) == 6:  # a plain box
        (x0, y0, z0), (x1, y1, z1) = mins, maxs
        if x1 - x0 <= 0 or y1 - y0 <= 0 or z1 - z0 <= 0:
            return []
        c = [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
             (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]  # fmt: skip
        quads = ((1, 2, 6, 5), (0, 4, 7, 3), (2, 3, 7, 6), (0, 1, 5, 4), (4, 5, 6, 7), (0, 3, 2, 1))
        return [np.array([c[i] for i in q]) for q in quads]
    size = min(max(abs(v) for v in mins + maxs) * 2 + 64.0, 1.0e6)
    faces = []
    for i, (nx, ny, nz, d) in enumerate(planes):
        if nx == 0.0 and ny == 0.0 and nz == 0.0:
            continue
        poly = _square(nx, ny, nz, d, size)
        for j, (mx, my, mz, e) in enumerate(planes):
            if i != j:
                poly = _clip_poly(poly, mx, my, mz, e)
                if len(poly) < 3:
                    break
        if len(poly) >= 3:
            kept = []
            for k, a in enumerate(poly):
                b = poly[(k + 1) % len(poly)]
                if (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2 > _WELD2:
                    kept.append(a)
            if len(kept) >= 3 and _area2(kept) > _MIN_AREA:
                faces.append(np.array(kept))
    return faces
