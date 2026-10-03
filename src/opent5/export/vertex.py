"""World and model vertices unpacked to meshes, from the parser's typed vertex arrays.

The vertex layouts are named in ``opent5.xfile.structs`` (GfxWorldVertex, the
XSurface verts0 / stream formats chosen by flags); evidence in docs/extract.md
section 3. Two things the schema leaves opaque are decoded here:

- GfxWorld ``draw.vld.data`` (+0x218): one run per vertex group, each vertex
  ``stride`` bytes: u32 colour (R, G, B, A bytes), float u, v, float lightmap
  u, v, CMP normal, CMP tangent, then 0 to 28 bytes of per-layer extras. A
  surface's ``tris.vertexLayerData`` is its group's byte offset and
  ``tris.firstVertex`` the group's first vertex; the stride is the run length
  divided by the group's vertex count.
- Packed model positions (XVertexPacked*): position = offset + s16 * 2**e /
  32768, with the offset XSurface ``posOffset`` (+0x48) and e ``posScaleExp``
  (+0x55..+0x57).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from opent5.xfile.schema import unpack_cmp_array
from opent5.xfile.structs import XSurface

WORLD_LAYER_BASE = 28


def unpack_cmp(words: np.ndarray) -> np.ndarray:
    """u32 CMP words -> float32 vectors (..., 3), not renormalised."""
    return unpack_cmp_array(words).astype(np.float32)


def normalise(v: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(v, axis=-1, keepdims=True)
    return np.where(n > 1e-6, v / np.maximum(n, 1e-6), v).astype(np.float32)


@dataclass
class Mesh:
    """One unpacked vertex set and its triangles (indices into it)."""

    positions: np.ndarray  # (n, 3) float32
    triangles: np.ndarray  # (m, 3) int64
    normals: np.ndarray | None = None  # (n, 3)
    uvs: np.ndarray | None = None  # (n, 2)
    colours: np.ndarray | None = None  # (n, 4) uint8 RGBA


# -- world ---------------------------------------------------------------------------------


def world_layer(layer: bytes, offset: int, count: int, stride: int) -> dict[str, np.ndarray]:
    """``count`` vertices of the base layer at ``offset`` with ``stride``: colour,
    uv, lightmap uv, normal, tangent."""
    if stride < WORLD_LAYER_BASE:
        raise ValueError(f"world layer stride: expected >= {WORLD_LAYER_BASE}, found {stride}")
    run = layer[offset : offset + count * stride]
    if len(run) < count * stride:
        raise ValueError(
            f"world layer at {offset:#x}: expected {count * stride} bytes, found {len(run)}"
        )
    dtype = np.dtype(
        {
            "names": ["colour", "uv", "lmap_uv", "normal", "tangent"],
            "formats": [("u1", 4), (">f4", 2), (">f4", 2), ">u4", ">u4"],
            "offsets": [0, 4, 12, 20, 24],
            "itemsize": stride,
        }
    )
    a = np.frombuffer(run, dtype, count)
    return {
        "colour": a["colour"].copy(),
        "uv": a["uv"].astype(np.float32),
        "lmap_uv": a["lmap_uv"].astype(np.float32),
        "normal": normalise(unpack_cmp(a["normal"])),
        "tangent": normalise(unpack_cmp(a["tangent"])),
    }


@dataclass
class WorldSurface:
    """The GfxSurface fields the mesh needs (names as in the schema's GfxSurface)."""

    index: int
    first_vertex: int  # tris.firstVertex: the group's first vertex
    layer_offset: int  # tris.vertexLayerData: the group's byte offset in the layer data
    vertex_count: int
    tri_count: int
    base_index: int
    lightmap_index: int = 0
    flags: int = 0

    @classmethod
    def from_fields(cls, index: int, f) -> WorldSurface:
        return cls(
            index,
            f["tris.firstVertex"],
            f["tris.vertexLayerData"],
            f["tris.vertexCount"],
            f["tris.triCount"],
            f["tris.baseIndex"],
            f["lightmapIndex"],
            f["flags"],
        )


def world_group_strides(surfaces: list[WorldSurface], vertex_count: int, layer_size: int):
    """(first_vertex, layer_offset) -> stride, for every vertex group."""
    groups = sorted({(s.first_vertex, s.layer_offset) for s in surfaces})
    out: dict[tuple[int, int], int] = {}
    for i, (first, off) in enumerate(groups):
        nxt_first, nxt_off = groups[i + 1] if i + 1 < len(groups) else (vertex_count, layer_size)
        count = nxt_first - first
        if count <= 0:
            raise ValueError(f"vertex group at {first}: next group starts at {nxt_first}")
        stride, rem = divmod(nxt_off - off, count)
        if rem:
            raise ValueError(
                f"vertex group at {first} (layer {off:#x}): {nxt_off - off} bytes for {count} "
                "vertices is not a whole stride"
            )
        out[(first, off)] = stride
    return out


def world_mesh(positions: np.ndarray, layer: bytes, indices: np.ndarray, surfaces):
    """The whole GfxWorld as one Mesh plus per-surface triangle ranges.

    ``positions`` (n, 3) from the vertex array's ``xyz``; ``indices`` the u16
    index array; ``surfaces`` WorldSurface list. Returns (mesh, tri_ranges) where
    tri_ranges[i] = (start, count) in mesh.triangles for surface i."""
    positions = np.asarray(positions, np.float32)
    n = len(positions)
    strides = world_group_strides(surfaces, n, len(layer))
    normals = np.zeros((n, 3), np.float32)
    uvs = np.zeros((n, 2), np.float32)
    cols = np.full((n, 4), 255, np.uint8)
    groups = sorted(strides)
    for i, (first, off) in enumerate(groups):
        end = groups[i + 1][0] if i + 1 < len(groups) else n
        base = world_layer(layer, off, end - first, strides[(first, off)])
        normals[first:end] = base["normal"]
        uvs[first:end] = base["uv"]
        cols[first:end] = base["colour"]
    idx = np.asarray(indices).astype(np.int64)
    tris = []
    ranges = []
    at = 0
    for s in surfaces:
        t = idx[s.base_index : s.base_index + 3 * s.tri_count].reshape(-1, 3) + s.first_vertex
        tris.append(t)
        ranges.append((at, len(t)))
        at += len(t)
    triangles = np.concatenate(tris) if tris else np.zeros((0, 3), np.int64)
    return Mesh(positions, triangles, normals, uvs, cols), ranges


# -- models --------------------------------------------------------------------------------


def packed_positions(pos: np.ndarray, surf_raw: bytes) -> np.ndarray:
    """s16 (x, y, z) quantised positions (the schema's ``pos``) -> float32 (count, 3)."""
    offset = np.array(XSurface.field("posOffset").decode(surf_raw), np.float64)
    shifts = np.array(XSurface.field("posScaleExp").decode(surf_raw), np.float64)
    return (offset + pos.astype(np.float64) * (2.0**shifts) / 32768.0).astype(np.float32)


def xsurface_mesh(
    verts0: np.ndarray, stream: np.ndarray | None, triangles: np.ndarray, surf_raw: bytes
) -> Mesh:
    """One XSurface -> Mesh, from its typed verts0 and stream arrays (any of the
    schema's XVertex* / XStream* formats) and its (m, 3) triangle array."""
    names = verts0.dtype.names
    if "xyz" in names:
        pos = verts0["xyz"].astype(np.float32)
    else:
        pos = packed_positions(verts0["pos"], surf_raw)
    normals = uvs = cols = None
    if "normal" in names:
        normals = normalise(unpack_cmp(verts0["normal"]))
    if stream is not None and len(stream) >= len(pos):
        sn = stream.dtype.names
        if "normal" in sn:
            normals = normalise(unpack_cmp(stream["normal"]))
        uvs = stream["uv"].astype(np.float32)
        if "color" in sn:
            cols = stream["color"].copy()
    tris = np.asarray(triangles).astype(np.int64).reshape(-1, 3)
    return Mesh(pos, tris, normals, uvs, cols)
