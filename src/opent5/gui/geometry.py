"""Meshes for the geometry view: world surfaces, collision, models.

    from opent5.gui import geometry
    m = geometry.mesh(doc, "world", None)      # the zone's first GfxWorld
    m = geometry.mesh(doc, "collision", 1234)   # a clipMap by asset index
    m = geometry.mesh(doc, "model", 56)         # an XModel's LOD0
    m = geometry.mesh(doc, "world_models", None)  # the world with its static models placed

``doc`` is anything with an ``xfile`` (the backend adapter or a ZoneDoc). The
decoding is the exporter's (``opent5.export``): GfxWorld surfaces through
``vertex.world_mesh``, clipMap brushes as convex faces plus the collision
triangles, XModel LOD0 surfaces through the XSurface vertex formats. Results
are cached per parsed zone.

``MeshData.groups`` holds one id per triangle: the surface index (world,
model), the brush index (collision brushes) or -1 (collision triangles). For
``world_models`` the world surfaces keep their indices and each placed static
model's surfaces take the next ids after them, so every id (world or placed) is
an index into ``MeshData.materials`` for the shaded, textured renderer.
"""

from __future__ import annotations

import os
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
#: Per (id(xfile), gfx asset index): what a live prop edit needs to restage only the moved,
#: rotated or rescaled static models in place, instead of rebuilding the whole world+models
#: mesh (see ``restage_static_models``). Dropped with the world_models mesh it describes.
_models_state: dict[tuple[int, int], dict] = {}
#: Decoded, downscaled colour maps, keyed by (id(xfile), material name). None = none usable.
_texcache: dict[tuple[int, str], np.ndarray | None] = {}
#: Largest colour-map side kept for the shaded preview (GL mip-maps the rest down).
TEXTURE_MAX = 256


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
    _texcache.clear()
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
    if index is not None and kind not in ("world", "world_models", "collision", "model"):
        kind = kind_for_type(xfile.assets[index].type) or kind
    key = (id(xfile), kind, index)
    if key in _cache:
        return _cache[key]
    if kind == "world":
        out = world_mesh(xfile, _pick(xfile, WORLD_TYPES, index, "world geometry (gfx_map)"))
    elif kind == "world_models":
        asset = _pick(xfile, WORLD_TYPES, index, "world geometry (gfx_map)")
        out = with_static_models(xfile, asset, mesh(doc, "world", asset.index))
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


def drop_static_models(doc) -> None:
    """Forget the cached world+models mesh for this zone, so a rebuild re-reads the edited
    static-model placements (a moved, added or deleted prop). The plain world mesh is kept, as
    only the placed models change."""
    xfile = _xfile(doc)
    for key in [k for k in _cache if k[0] == id(xfile) and k[1] == "world_models"]:
        del _cache[key]
    for key in [k for k in _models_state if k[0] == id(xfile)]:
        del _models_state[key]


def model_mesh(zdoc, node, name: str) -> MeshData:
    """LOD0 of an XModel node loaded inside another asset."""
    xfile = _xfile(zdoc)
    key = (id(xfile), "model", ("inline", name))
    if key not in _cache:
        _cache[key] = _model(xfile, node, name)
    return _cache[key]


# -- textures for the shaded renderer --------------------------------------------------------


def _colormap_nodes(h) -> dict[str, Any]:
    """Material name -> its colorMap image node (or None), from the material's textures."""
    from opent5.export.zone import SAMPLER_BY_HASH
    from opent5.xfile.schema import view

    out: dict[str, Any] = {}
    for name, node in h.index.of(T.MATERIAL).items():
        chosen = None
        for t in node.get("textures") or []:
            if SAMPLER_BY_HASH.get(view(t).fields.nameHash) == "colorMap":
                chosen = h.resolver.node(t.get("image"))
                break
        out[name] = chosen
    return out


def _downscale(rgba: np.ndarray, maxdim: int = TEXTURE_MAX) -> np.ndarray:
    h, w = rgba.shape[:2]
    factor = max(1, int(max(h, w) // maxdim))
    if factor > 1:
        rgba = rgba[::factor, ::factor]
    return np.ascontiguousarray(rgba, np.uint8)


#: How many threads decode colour maps at once. Decode is numpy (GIL dropped) mixed with
#: reading one shared disk, so a few workers win most of the speedup and more only add
#: contention; measured best around four on an eight-core WSL box reading off a 9p mount.
_TEXTURE_WORKERS = min(8, max(1, os.cpu_count() or 1))


def _decode_colormaps_parallel(doc, named_nodes: list) -> dict:
    """Decode ``[(name, colormap_node), ...]`` across a thread pool, each worker using its own
    ``PakSet`` (one open file handle per slot, so no shared seek), and return ``{name: rgba or
    None}``. Falls back to a single-threaded decode (one shared PakSet) for one item or when a
    pool cannot be made, so the path is always safe."""
    from opent5.export.images import PakSet
    from opent5.gui.backend import pak_dirs

    folders = pak_dirs(doc.path)
    zone = doc.zone_name
    if len(named_nodes) <= 1 or _TEXTURE_WORKERS <= 1:
        paks = PakSet(zone, folders)
        try:
            return {name: _decode_one_colormap(node, paks) for name, node in named_nodes}
        finally:
            paks.close()

    import threading
    from concurrent.futures import ThreadPoolExecutor

    local = threading.local()
    opened: list = []
    opened_lock = threading.Lock()

    def worker_paks() -> PakSet:
        paks = getattr(local, "paks", None)
        if paks is None:
            paks = local.paks = PakSet(zone, folders)
            with opened_lock:
                opened.append(paks)
        return paks

    def decode(item):
        name, node = item
        return name, _decode_one_colormap(node, worker_paks())

    try:
        with ThreadPoolExecutor(max_workers=_TEXTURE_WORKERS) as pool:
            return dict(pool.map(decode, named_nodes))
    finally:
        for paks in opened:
            paks.close()


def _decode_one_colormap(node, paks) -> np.ndarray | None:
    """Decode one material's colour map to a downscaled RGBA array, or None when it has no
    colour map or cannot be decoded. Pure and uncached: it touches only its arguments, so it is
    safe to run on a worker thread given that worker's own ``PakSet``."""
    if node is None:
        return None
    from opent5.export.images import ImageError, decode_image

    try:
        decoded = decode_image(node, paks)
        if decoded.layers:
            return _downscale(decoded.layers[0][1])
    except (ImageError, ValueError, KeyError, IndexError, TypeError):
        return None
    return None


def _decode_colormap(xfile, name: str, node, paks) -> np.ndarray | None:
    key = (id(xfile), name)
    if key in _texcache:
        return _texcache[key]
    rgba = _decode_one_colormap(node, paks)
    _texcache[key] = rgba
    return rgba


def textures(doc, mesh: MeshData):
    """Decode each material's colour map and map it to the mesh's triangles.

    Returns ``(tri_tex, texture_list)``: ``tri_tex[i]`` is the index into
    ``texture_list`` for triangle ``i`` (its surface's material's colour map), or
    -1 where there is none (no material, an undecodable colour map, or a streamed
    texture whose .pak is absent). Textures that cannot be decoded fall back to a
    flat shade this way. Pure CPU, safe to call on a worker thread."""
    xfile = _xfile(doc)
    mats = mesh.materials
    if not mats or mesh.groups is None:
        return np.full(len(mesh.triangles), -1, np.int32), []
    h = _helper(xfile)
    cmaps = _colormap_nodes(h)
    names = list(dict.fromkeys(m for m in mats if m))
    # A map has hundreds of distinct colour maps; decoding each is independent, pure numpy
    # (which drops the GIL) and bound partly on reading the .pak, so they decode across a small
    # thread pool. Only the uncached ones, and each worker reads through its own PakSet because
    # a PakSet keeps one file handle per slot and a shared seek+read is not thread-safe.
    rgbas: dict[str, np.ndarray | None] = {}
    todo = [n for n in names if (id(xfile), n) not in _texcache]
    for n in names:
        if (id(xfile), n) in _texcache:
            rgbas[n] = _texcache[(id(xfile), n)]
    if todo:
        decoded = _decode_colormaps_parallel(doc, [(n, cmaps.get(n)) for n in todo])
        for n, rgba in decoded.items():
            _texcache[(id(xfile), n)] = rgba
            rgbas[n] = rgba
    slot_of_name: dict[str, int] = {}
    texture_list: list[np.ndarray] = []
    for name in names:  # names order, so the slots match a serial decode exactly
        rgba = rgbas.get(name)
        if rgba is not None:
            slot_of_name[name] = len(texture_list)
            texture_list.append(rgba)
    group_slot = np.full(len(mats), -1, np.int32)
    for i, name in enumerate(mats):
        group_slot[i] = slot_of_name.get(name, -1) if name else -1
    groups = np.asarray(mesh.groups)
    valid = (groups >= 0) & (groups < len(mats))
    tri_tex = np.full(len(groups), -1, np.int32)
    tri_tex[valid] = group_slot[groups[valid]]
    return tri_tex, texture_list


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
    resolver = _helper(xfile).resolver
    materials = [resolver.name(s.get("material")) for s in g["surfaces"]]
    return MeshData(
        m.positions.astype(np.float32),
        m.triangles.astype(np.int32),
        m.normals,
        m.uvs,
        groups,
        materials=materials,
        label=g.get("name") or asset.name or "world",
        notes=[f"{len(surfs)} surfaces"],
    )


def _parts_for(h, model: str):
    """LOD0 surfaces of a placement's model, or ``None`` when it has none usable. The same
    decode the full build and the live restage both go through, so they place a model alike."""
    node = h.index.get(T.XMODEL, model)
    if node is None:
        return None
    try:
        return h.model_lod0(node) or None
    except (ValueError, KeyError, TypeError):
        return None


def _place_parts(parts, scale, axes, origin):
    """World-space positions and normals of one placement's LOD0 parts, concatenated in the
    build's part order: ``origin + scale * (position @ axes)``. Exactly what the loop below
    writes, factored out so a live restage recomputes one moved prop the same way."""
    axes, origin = np.array(axes), np.array(origin)
    pos, norm = [], []
    for _mat, m in parts:
        pos.append((origin + scale * (m.positions @ axes)).astype(np.float32))
        if m.normals is not None:
            norm.append((m.normals @ axes).astype(np.float32))
        else:
            norm.append(np.zeros((len(m.positions), 3), np.float32))
    return np.concatenate(pos), np.concatenate(norm)


def _normalise_rows(v: np.ndarray) -> np.ndarray:
    """``vx.normalise`` over a stack of row vectors at once: (n, 3) -> (n, 3) float32, the same
    guard and float32 cast per row, so a batched orthonormal matches the per-instance one."""
    n = np.linalg.norm(v, axis=-1, keepdims=True)
    return np.where(n > 1e-6, v / np.maximum(n, 1e-6), v).astype(np.float32)


def _orthonormal_rows(cmp: np.ndarray) -> np.ndarray:
    """``zone.orthonormal`` over every instance's three CMP axes at once: (n, 3, 3) -> (n, 3, 3).
    Same steps as the scalar version (normalise forward, Gram-Schmidt the up, cross for left),
    so each row matches ``orthonormal`` field for field; batched so the thousands of tiny 3x3
    numpy calls that dominated the decode collapse into a handful of array ops."""
    a0, a2 = cmp[:, 0, :], cmp[:, 2, :]
    f = _normalise_rows(a0)
    # dot(a2, f) per row; a 3-term sum, so the einsum matches the scalar np.dot bit for bit.
    d = np.einsum("ni,ni->n", a2, f)
    up = _normalise_rows(a2 - f * d[:, None])
    left = np.cross(up, f)
    return np.stack([f, left, up], axis=1).astype(np.float64)


#: Structured view over a GfxStaticModelDrawInst's 0x2c raw bytes: origin (vec3), the three
#: packed CMP axes and the scale, at their struct offsets, read for every instance in one pass.
_DRAW_INST_DTYPE = np.dtype(
    {"names": ["origin", "axis", "scale"],
     "formats": [(">f4", 3), (">u4", 3), ">f4"],
     "offsets": [0x4, 0x10, 0x1C], "itemsize": 0x2C}
)  # fmt: skip


def _decode_placements(h, g) -> list[dict]:
    """The static-model placements the world+models build needs (index, model, origin, axes,
    scale), decoded for every draw instance at once.

    Field for field the same as ``ZoneExporter.static_models`` for these keys (same CMP unpack,
    same orthonormalisation, same rounding), but the struct read, the axis unpack and the
    orthonormal frame are vectorised over all instances rather than looped one tiny numpy call
    at a time, which is where the per-instance decode spent its time. The exporter's own
    ``static_models`` is left as is, so export output is untouched."""
    from opent5.export.vertex import unpack_cmp

    insts = g["smodel_draw_insts"] or []
    if not insts:
        return []
    raw = b"".join(bytes(d["raw"]) for d in insts)
    arr = np.frombuffer(raw, _DRAW_INST_DTYPE, len(insts))
    origins = arr["origin"].astype(np.float64)
    scales = arr["scale"].astype(np.float64)
    axes = _orthonormal_rows(unpack_cmp(np.ascontiguousarray(arr["axis"])).astype(np.float64))
    names = [h.resolver.name(d["model"]) for d in insts]
    # The rounding stays in Python (round(), not np.round) so the values are bit-identical to
    # the scalar path: np.round scales by a power of ten and can land on the other side of a
    # decimal boundary, Python's round is correctly rounded.
    out = []
    for i in range(len(insts)):
        out.append(
            {
                "index": i,
                "model": names[i],
                "origin": [round(float(v), 4) for v in origins[i]],
                "axes": [[round(float(v), 5) for v in row] for row in axes[i]],
                "scale": round(float(scales[i]), 5),
            }
        )
    return out


def _placement_sigs(h, g) -> list:
    """A cheap raw signature per static-model draw instance, for diffing an edit without the
    costly per-instance axis decode: ``(model_ref, scale, axis_words, origin)``. The model ref
    and whether the scale is zero decide topology (which props draw and with how many
    vertices); the axis and origin decide only where a drawn prop sits."""
    from opent5.xfile.schema import view

    out = []
    for d in g["smodel_draw_insts"] or []:
        f = view(d).fields
        model = d["model"]
        ref = getattr(model, "raw", model)  # an AssetLink's raw ref; detects a model swap
        out.append(
            (ref, float(f["placement.scale"]),
             tuple(int(w) for w in f["placement.axis"]),
             tuple(float(v) for v in f["placement.origin"]))
        )  # fmt: skip
    return out


def with_static_models(xfile, asset, world: MeshData) -> MeshData:
    """``world`` plus every static model's LOD0 placed as the exporter places it
    (``ZoneExporter.static_models``: origin + scale x position @ axes).

    The placement transform is batched per model: every instance of a model shares the same
    LOD0 vertices, so one ``numpy.matmul`` of those vertices against all the instances' axes at
    once (BLAS, multi-core) replaces a Python matmul per prop, and the triangle, group, uv and
    material arrays are written into preallocated buffers in placement order rather than
    concatenated from thousands of per-prop pieces. The output (vertex order, triangles,
    groups, materials) is byte-identical to the per-prop loop in ``_with_static_models_loop``;
    the matmul is per matrix, so grouping by model only changes the order work happens in, not
    the result each prop lands at."""
    h = _helper(xfile)
    placements = _decode_placements(h, asset.data)
    n_world = len(world.positions)
    world_normals = (
        world.normals if world.normals is not None else np.zeros((n_world, 3), np.float32)
    )
    world_uvs = world.uvs if world.uvs is not None else np.zeros((n_world, 2), np.float32)
    materials = list(world.materials or [])

    # First pass: pick the drawn placements, fetch each model's parts once, and lay out every
    # placement's vertex, triangle and group ranges in placement order, so the second pass can
    # scatter each instance's batch result into exactly the slot the loop would have appended.
    plans: list[dict] = []  # per drawn placement, with its destination ranges
    by_model: dict[str, list[int]] = {}  # model -> indices into plans sharing those parts
    drawn: list[tuple[int, int, int, str]] = []
    at, tri_at, gid, missing = n_world, len(world.triangles), len(materials), 0
    for p in placements:
        if not p["scale"]:  # a deleted/hidden prop: a degenerate scale-0 placement draws nothing
            continue
        parts = _parts_for(h, p["model"])
        if not parts:
            missing += 1
            continue
        vstart, tstart, gid0 = at, tri_at, gid
        for _mat, m in parts:
            materials.append(_mat)
            at += len(m.positions)
            tri_at += len(m.triangles)
            gid += 1
        plans.append(
            {"parts": parts, "axes": np.array(p["axes"]), "scale": p["scale"],
             "origin": np.array(p["origin"]), "vstart": vstart, "tstart": tstart, "gid0": gid0}
        )  # fmt: skip
        by_model.setdefault(p["model"], []).append(len(plans) - 1)
        drawn.append((p["index"], vstart, at - vstart, p["model"]))

    total_v, total_t = at, tri_at
    positions = np.empty((total_v, 3), np.float32)
    normals = np.empty((total_v, 3), np.float32)
    uvs = np.empty((total_v, 2), np.float32)
    tris = np.empty((total_t, 3), np.int32)
    groups = np.empty(total_t, np.int32)
    positions[:n_world] = world.positions
    normals[:n_world] = world_normals.astype(np.float32)
    uvs[:n_world] = world_uvs.astype(np.float32)
    tris[: len(world.triangles)] = world.triangles
    groups[: len(world.groups)] = world.groups

    # Second pass, one batch per model: build that model's shared vertices once, transform them
    # against all its instances' axes in a single matmul, and write each instance into place.
    for idxs in by_model.values():
        parts = plans[idxs[0]]["parts"]
        pm = np.concatenate([m.positions for _mat, m in parts]).astype(np.float32)
        nm = np.concatenate(
            [m.normals if m.normals is not None else np.zeros((len(m.positions), 3), np.float32)
             for _mat, m in parts]
        ).astype(np.float32)  # fmt: skip
        um = np.concatenate(
            [m.uvs if m.uvs is not None else np.zeros((len(m.positions), 2), np.float32)
             for _mat, m in parts]
        ).astype(np.float32)  # fmt: skip
        # Triangle indices and group ids relative to the instance's own vertex/group base.
        tri_rel, grp_rel, voff = [], [], 0
        for j, (_mat, m) in enumerate(parts):
            tri_rel.append(m.triangles + voff)
            grp_rel.append(np.full(len(m.triangles), j, np.int32))
            voff += len(m.positions)
        tri_rel = np.concatenate(tri_rel)
        grp_rel = np.concatenate(grp_rel)
        axes = np.stack([plans[i]["axes"] for i in idxs])  # (k, 3, 3) f64
        origins = np.stack([plans[i]["origin"] for i in idxs])  # (k, 3) f64
        scales = np.array([plans[i]["scale"] for i in idxs], np.float64)  # (k,)
        # Per-matrix matmul, so each instance equals pm @ axes exactly; origin + scale * (...)
        # in that order to add the same way the loop did.
        posk = (origins[:, None, :] + scales[:, None, None] * np.matmul(pm[None], axes)).astype(
            np.float32
        )
        normk = np.matmul(nm[None], axes).astype(np.float32)
        for slot, i in enumerate(idxs):
            pl = plans[i]
            vs, ts, n = pl["vstart"], pl["tstart"], len(pm)
            positions[vs : vs + n] = posk[slot]
            normals[vs : vs + n] = normk[slot]
            uvs[vs : vs + n] = um
            tris[ts : ts + len(tri_rel)] = (tri_rel + vs).astype(np.int32)
            groups[ts : ts + len(grp_rel)] = grp_rel + pl["gid0"]

    placed = len(drawn)
    notes = [*world.notes, f"{placed} static models"]
    if missing:
        notes.append(f"{missing} without a usable model")
    out = MeshData(
        positions, tris, normals=normals, uvs=uvs, groups=groups,
        materials=materials, label=world.label, notes=notes,
    )  # fmt: skip
    _models_state[(id(xfile), asset.index)] = {
        "mesh": out,
        "n_world": n_world,
        "drawn": drawn,
        "sigs": _placement_sigs(h, asset.data),
    }
    return out


def _with_static_models_loop(xfile, asset, world: MeshData) -> MeshData:
    """The original per-prop build the vectorised ``with_static_models`` replaced: the scalar
    ``static_models`` decode plus a Python loop that places one prop at a time. Kept verbatim as
    the reference the byte-identity and benchmark tests check the batched build against; not
    used on the live path."""
    h = _helper(xfile)
    placements = h.static_models(asset.data)
    n_world = len(world.positions)
    positions, tris, groups = [world.positions], [world.triangles], [world.groups]
    zeros_uv = np.zeros((n_world, 2), np.float32)
    zeros_n = np.zeros((n_world, 3), np.float32)
    world_uvs = world.uvs if world.uvs is not None else zeros_uv
    world_normals = world.normals if world.normals is not None else zeros_n
    normals, uvs = [world_normals.astype(np.float32)], [world_uvs.astype(np.float32)]
    materials = list(world.materials or [])
    group_id = len(materials)
    at, placed, missing = n_world, 0, 0
    #: each drawn placement's instance index and its vertex slice [start, start+count) in the
    #: combined mesh, so a live edit can rewrite just the moved prop's vertices.
    drawn: list[tuple[int, int, int, str]] = []
    for p in placements:
        if not p["scale"]:  # a deleted/hidden prop: a degenerate scale-0 placement draws nothing
            continue
        parts = _parts_for(h, p["model"])
        if not parts:
            missing += 1
            continue
        start = at
        axes, origin = np.array(p["axes"]), np.array(p["origin"])
        for mat, m in parts:
            positions.append((origin + p["scale"] * (m.positions @ axes)).astype(np.float32))
            tris.append((m.triangles + at).astype(np.int32))
            groups.append(np.full(len(m.triangles), group_id, np.int32))
            n = len(m.positions)
            if m.normals is not None:
                normals.append((m.normals @ axes).astype(np.float32))
            else:
                normals.append(np.zeros((n, 3), np.float32))
            muv = m.uvs.astype(np.float32) if m.uvs is not None else np.zeros((n, 2), np.float32)
            uvs.append(muv)
            materials.append(mat)
            group_id += 1
            at += n
        drawn.append((p["index"], start, at - start, p["model"]))
        placed += 1
    notes = [*world.notes, f"{placed} static models"]
    if missing:
        notes.append(f"{missing} without a usable model")
    out = MeshData(
        np.concatenate(positions),
        np.concatenate(tris),
        normals=np.concatenate(normals),
        uvs=np.concatenate(uvs),
        groups=np.concatenate(groups),
        materials=materials,
        label=world.label,
        notes=notes,
    )
    _models_state[(id(xfile), asset.index)] = {
        "mesh": out,
        "n_world": n_world,
        "drawn": drawn,
        "sigs": _placement_sigs(h, asset.data),
    }
    return out


def restage_static_models(doc) -> MeshData | None:
    """Rebuild the world+models mesh after a prop edit by rewriting only the vertices of the
    props that actually moved, rotated or rescaled, reusing the cached world surfaces, the
    unmoved props and the shared triangle, group and material arrays.

    Returns the updated mesh (its triangles are unchanged, so the view can refresh the display
    without recomputing wireframe edges or bounds), or ``None`` when the edit changed the set
    of drawn props (an add, a delete, or a model swap), which changes the mesh topology and
    needs the full ``with_static_models`` rebuild."""
    xfile = _xfile(doc)
    asset = _pick(xfile, WORLD_TYPES, None, "world geometry (gfx_map)")
    state = _models_state.get((id(xfile), asset.index))
    if state is None or state.get("mesh") is None:
        return None
    h = _helper(xfile)
    new_sigs = _placement_sigs(h, asset.data)
    old_sigs = state["sigs"]
    if len(new_sigs) != len(old_sigs):
        return None  # an instance was added or removed: topology changed
    # Topology holds only while every instance keeps its model and its drawn/hidden state (a
    # zero scale draws nothing). A model swap or a show/hide needs the full rebuild.
    changed_insts = set()
    for i, (new, old) in enumerate(zip(new_sigs, old_sigs, strict=True)):
        if new[0] != old[0] or (new[1] == 0.0) != (old[1] == 0.0):
            return None
        if new != old:
            changed_insts.add(i)
    old_mesh = state["mesh"]
    if not changed_insts:  # nothing a placed model draws from changed (e.g. an entity-only edit)
        return old_mesh
    positions = old_mesh.positions.copy()
    normals = old_mesh.normals.copy()
    # Decode and re-place only the changed props, exactly as the full build does for them. The
    # vertex slices they touch are recorded so the shaded view can re-upload just those rows of
    # the GPU buffer instead of the whole mesh.
    changed_ranges: list[tuple[int, int]] = []
    for inst_index, start, count, model in state["drawn"]:
        if inst_index not in changed_insts:
            continue
        parts = _parts_for(h, model)
        if not parts:
            return None  # unexpected: a drawn prop lost its model; fall back to a full rebuild
        p = _decoded_placement(h, asset.data, inst_index)
        pos, norm = _place_parts(parts, p["scale"], p["axes"], p["origin"])
        if len(pos) != count:
            return None  # vertex count drifted; rebuild to stay correct
        positions[start : start + count] = pos
        normals[start : start + count] = norm
        changed_ranges.append((start, count))
    out = MeshData(
        positions, old_mesh.triangles, normals=normals, uvs=old_mesh.uvs,
        groups=old_mesh.groups, materials=old_mesh.materials,
        label=old_mesh.label, notes=old_mesh.notes,
    )  # fmt: skip
    state["mesh"] = out
    state["sigs"] = new_sigs
    state["changed_ranges"] = changed_ranges
    for key in ((id(xfile), "world_models", None), (id(xfile), "world_models", asset.index)):
        if key in _cache:
            _cache[key] = out
    return out


def restage_changed_ranges(doc) -> list[tuple[int, int]]:
    """The ``(vertex_start, count)`` slices the last ``restage_static_models`` rewrote, so the
    shaded view can re-upload only those rows of the GPU vertex buffer. Empty when no restage
    has run or nothing moved."""
    xfile = _xfile(doc)
    try:
        asset = _pick(xfile, WORLD_TYPES, None, "world geometry (gfx_map)")
    except EditError:
        return []
    state = _models_state.get((id(xfile), asset.index))
    return list(state.get("changed_ranges", [])) if state else []


def _decoded_placement(h, g, inst_index: int) -> dict:
    """One static-model placement decoded exactly as ``ZoneExporter.static_models`` decodes it
    (same rounding), so a restage places a moved prop identically to a full rebuild. Decodes
    the single changed instance rather than the whole list, which the full path re-decodes."""
    from opent5.export.vertex import unpack_cmp
    from opent5.export.zone import orthonormal
    from opent5.xfile.schema import view

    d = (g["smodel_draw_insts"] or [])[inst_index]
    f = view(d).fields
    axes = orthonormal(unpack_cmp(np.array(f["placement.axis"], np.uint32)).astype(np.float64))
    return {
        "model": h.resolver.name(d["model"]),
        "origin": [round(v, 4) for v in f["placement.origin"]],
        "axes": [[round(float(v), 5) for v in row] for row in axes],
        "scale": round(f["placement.scale"], 5),
    }


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


def collision_overlay_mesh(doc, families=("clip",)) -> MeshData | None:
    """A mesh of the clipMap's clip brushes whose contents family is in ``families``
    (``opent5.convert.propclip.contents_family``: 'clip', 'solid', 'other'), triangulated for
    the collision overlay to draw as translucent volumes over the edit view. ``groups`` holds
    each triangle's clipMap brush index, so a picked or highlighted triangle maps back to the
    brush the editor can remove with ``remove_clips``. Cached per (zone, families); ``None``
    when the zone carries no clipMap.

    The baked-triangle collision (the clipMap's tri_indices/verts) is deliberately left out:
    it is separate collision that cannot be removed without a recompile, so the overlay shows
    only the removable clip walls. The full 'collision' view still shows the baked triangles."""
    xfile = _xfile(doc)
    try:
        asset = _pick(xfile, COLLISION_TYPES, None, "collision (col_map)")
    except EditError:
        return None
    fam = tuple(families)
    key = (id(xfile), "collision_overlay", fam)
    if key in _cache:
        return _cache[key]
    from opent5.convert import propclip as pc
    from opent5.export import collision as col
    from opent5.xfile.schema import view

    c = asset.data
    if c.get("brushes") is None:
        return None
    h = _helper(xfile)
    try:
        brushes = col.parse_brushes(view(c).array("brushes"), h.brush_sides)
    except ValueError:
        return None
    want = set(fam)
    positions, tris, groups, at = [], [], [], 0
    for i, b in enumerate(brushes):
        if pc.contents_family(b.contents) not in want:
            continue
        faces = brush_faces(b)
        if not faces:
            continue
        p, t = col.triangulate(faces)
        positions.append(np.asarray(p, np.float32))
        tris.append(t + at)
        groups.append(np.full(len(t), i, np.int32))
        at += len(p)
    if positions:
        out = MeshData(
            np.concatenate(positions),
            np.concatenate(tris).astype(np.int32),
            groups=np.concatenate(groups),
            label="collision",
            notes=[f"{at} clip verts in {len(groups)} brushes"],
        )
    else:
        out = MeshData(
            np.zeros((0, 3), np.float32), np.zeros((0, 3), np.int32),
            groups=np.zeros(0, np.int32), label="collision", notes=["0 clip brushes"],
        )  # fmt: skip
    _cache[key] = out
    return out


def drop_collision_overlay(doc) -> None:
    """Forget the cached collision-overlay meshes for this zone, so a rebuild re-reads the
    edited clip brushes after a clip is added or removed (a removed clip's contents go to 0, so
    it drops out of the clip family and the overlay no longer shows it)."""
    xfile = _xfile(doc)
    for key in [k for k in _cache if k[0] == id(xfile) and k[1] == "collision_overlay"]:
        del _cache[key]


def _model(xfile, node, name: str) -> MeshData:
    h = _helper(xfile)
    try:
        parts = h.model_lod0(node)
    except (ValueError, KeyError, TypeError) as exc:
        raise EditError(f"model {name}: LOD0 does not decode ({exc})") from exc
    if not parts:
        raise EditError(f"model {name}: no surfaces in LOD0")
    positions, tris, groups, normals, uvs, materials, at = [], [], [], [], [], [], 0
    have_normals = all(m.normals is not None for _mat, m in parts)
    have_uvs = all(m.uvs is not None for _mat, m in parts)
    for i, (mat, m) in enumerate(parts):
        positions.append(m.positions.astype(np.float32))
        tris.append(m.triangles + at)
        groups.append(np.full(len(m.triangles), i, np.int32))
        if have_normals:
            normals.append(m.normals.astype(np.float32))
        if have_uvs:
            uvs.append(m.uvs.astype(np.float32))
        materials.append(mat)
        at += len(m.positions)
    return MeshData(
        np.concatenate(positions),
        np.concatenate(tris).astype(np.int32),
        normals=np.concatenate(normals) if have_normals else None,
        uvs=np.concatenate(uvs) if have_uvs else None,
        groups=np.concatenate(groups),
        materials=materials,
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
