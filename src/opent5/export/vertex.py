"""PS3 vertex formats of T5 world geometry and models, unpacked to numpy arrays.

Evidence and the derivation of each layout: docs/extract.md section 3.

World (GfxWorld):

- ``draw.vd.vertices`` (+0x20c), 16 bytes each: float x, y, z, then a float
  of +-1 (binormal sign).
- ``draw.vld.data`` (+0x218, PHYSICAL), one run per vertex group, each vertex
  ``stride`` bytes: u32 colour (R, G, B, A bytes), float u, v, float lightmap
  u, v, u32 normal and u32 tangent packed as CMP (below), then 0 to 28 bytes of
  extra per-layer data. A surface's ``+0xc`` is the byte offset of its group's
  run and ``+0x1c`` the group's first vertex; the stride is the run length
  divided by the group's vertex count.

Models (XSurface, ``flags & 7`` = stream format):

- ``verts0`` (+0x1c): 16 bytes of float x, y, z, binormal sign when
  ``flags & 1 == 0``; otherwise four s16 (x, y, z, binormal sign) quantised
  against the surface's offset (floats at +0x48) and per-axis scale exponents
  (bytes +0x55..+0x57: position = offset + s16 * 2**e / 32768), followed when
  ``flags & 3 == 3`` by u32 normal and u32 tangent (CMP).
- the vertex stream (+0x24): format 0, 1, 2: normal, tangent, half u, v, u32
  colour (16 bytes); 5: normal, tangent, half u, v (12); 3: half u, v, colour
  (8); 7: half u, v (4); 4 and 6: none.

CMP is the RSX's packed 11:11:10 signed normalised vector: x in bits 0..10,
y in 11..21, z in 22..31.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

import numpy as np

WORLD_VERTEX_SIZE = 16
WORLD_LAYER_BASE = 28
#: XSurface byte offsets used for packed positions.
XSURF_POS_OFFSET = 0x48
XSURF_POS_SHIFT = 0x55


def unpack_cmp(words: np.ndarray) -> np.ndarray:
    """u32 CMP words (any shape) -> float32 vectors (..., 3), not renormalised."""
    w = np.asarray(words, dtype=np.uint32).astype(np.int64)
    x = w & 0x7FF
    y = (w >> 11) & 0x7FF
    z = (w >> 22) & 0x3FF
    x = np.where(x >= 0x400, x - 0x800, x) / 1023.0
    y = np.where(y >= 0x400, y - 0x800, y) / 1023.0
    z = np.where(z >= 0x200, z - 0x400, z) / 511.0
    return np.stack([x, y, z], -1).astype(np.float32)


def pack_cmp(vectors: np.ndarray) -> np.ndarray:
    """float vectors (..., 3) in [-1, 1] -> u32 CMP words (the inverse of unpack_cmp)."""
    v = np.clip(np.asarray(vectors, np.float64), -1.0, 1.0)
    x = np.rint(v[..., 0] * 1023).astype(np.int64) & 0x7FF
    y = np.rint(v[..., 1] * 1023).astype(np.int64) & 0x7FF
    z = np.rint(v[..., 2] * 511).astype(np.int64) & 0x3FF
    return (x | (y << 11) | (z << 22)).astype(np.uint32)


def normalise(v: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(v, axis=-1, keepdims=True)
    return np.where(n > 1e-6, v / np.maximum(n, 1e-6), v).astype(np.float32)


def halves(data: bytes, count: int, stride: int, offset: int) -> np.ndarray:
    """Two big-endian half floats at ``offset`` of each ``stride``-byte record."""
    raw = np.frombuffer(data, np.uint8, count * stride).reshape(count, stride)
    return raw[:, offset : offset + 4].copy().view(">f2").astype(np.float32).reshape(count, 2)


def words(data: bytes, count: int, stride: int, offset: int) -> np.ndarray:
    raw = np.frombuffer(data, np.uint8, count * stride).reshape(count, stride)
    return raw[:, offset : offset + 4].copy().view(">u4").reshape(count)


def floats(data: bytes, count: int, stride: int, offset: int, n: int) -> np.ndarray:
    raw = np.frombuffer(data, np.uint8, count * stride).reshape(count, stride)
    return raw[:, offset : offset + 4 * n].copy().view(">f4").astype(np.float32).reshape(count, n)


def colours(data: bytes, count: int, stride: int, offset: int) -> np.ndarray:
    raw = np.frombuffer(data, np.uint8, count * stride).reshape(count, stride)
    return raw[:, offset : offset + 4].copy()


@dataclass
class Mesh:
    """One unpacked vertex set and its triangles (indices into it)."""

    positions: np.ndarray  # (n, 3) float32
    triangles: np.ndarray  # (m, 3) int64
    normals: np.ndarray | None = None  # (n, 3)
    uvs: np.ndarray | None = None  # (n, 2)
    colours: np.ndarray | None = None  # (n, 4) uint8 RGBA


# -- world ---------------------------------------------------------------------------------


def world_positions(vertices: bytes) -> np.ndarray:
    n = len(vertices) // WORLD_VERTEX_SIZE
    return np.frombuffer(vertices, ">f4", n * 4).reshape(n, 4)[:, :3].astype(np.float32)


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
    return {
        "colour": colours(run, count, stride, 0),
        "uv": floats(run, count, stride, 4, 2),
        "lmap_uv": floats(run, count, stride, 12, 2),
        "normal": normalise(unpack_cmp(words(run, count, stride, 20))),
        "tangent": normalise(unpack_cmp(words(run, count, stride, 24))),
    }


@dataclass
class WorldSurface:
    index: int
    first_vertex: int  # +0x1c: the group's first vertex
    layer_offset: int  # +0xc: the group's byte offset in the layer data
    vertex_count: int  # +0x24
    tri_count: int  # +0x26
    base_index: int  # +0x28
    mins: tuple[float, float, float]
    maxs: tuple[float, float, float]
    lightmap_index: int  # +0x44
    flags: int  # +0x47

    @classmethod
    def parse(cls, index: int, raw: bytes) -> WorldSurface:
        mins = struct.unpack_from(">3f", raw, 0)
        (layer,) = struct.unpack_from(">I", raw, 0xC)
        maxs = struct.unpack_from(">3f", raw, 0x10)
        (first,) = struct.unpack_from(">I", raw, 0x1C)
        vc, tc = struct.unpack_from(">HH", raw, 0x24)
        (base,) = struct.unpack_from(">I", raw, 0x28)
        return cls(index, first, layer, vc, tc, base, mins, maxs, raw[0x44], raw[0x47])


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


def world_mesh(vertices: bytes, layer: bytes, indices: bytes, surface_raws: list[bytes]):
    """The whole GfxWorld as one Mesh plus per-surface triangle ranges.

    Returns (mesh, surfaces, tri_ranges) where tri_ranges[i] = (start, count) in
    mesh.triangles for surface i."""
    positions = world_positions(vertices)
    n = len(positions)
    surfaces = [WorldSurface.parse(i, r) for i, r in enumerate(surface_raws)]
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
    idx = np.frombuffer(indices, ">u2").astype(np.int64)
    tris = []
    ranges = []
    at = 0
    for s in surfaces:
        t = idx[s.base_index : s.base_index + 3 * s.tri_count].reshape(-1, 3) + s.first_vertex
        tris.append(t)
        ranges.append((at, len(t)))
        at += len(t)
    triangles = np.concatenate(tris) if tris else np.zeros((0, 3), np.int64)
    return Mesh(positions, triangles, normals, uvs, cols), surfaces, ranges


# -- models --------------------------------------------------------------------------------


def packed_positions(verts0: bytes, count: int, stride: int, surf_raw: bytes) -> np.ndarray:
    """s16 x, y, z quantised positions -> float32 (count, 3)."""
    q = np.frombuffer(verts0, np.uint8, count * stride).reshape(count, stride)
    q = q[:, :6].copy().view(">i2").astype(np.float64).reshape(count, 3)
    offset = np.array(struct.unpack_from(">3f", surf_raw, XSURF_POS_OFFSET))
    shifts = np.array(list(surf_raw[XSURF_POS_SHIFT : XSURF_POS_SHIFT + 3]), np.float64)
    return (offset + q * (2.0**shifts) / 32768.0).astype(np.float32)


#: Stream format -> (stride, normal offset or None, tangent offset, uv offset, colour offset)
STREAM_LAYOUT = {
    0: (16, 0, 4, 8, 12),
    1: (16, 0, 4, 8, 12),
    2: (16, 0, 4, 8, 12),
    5: (12, 0, 4, 8, None),
    3: (8, None, None, 0, 4),
    7: (4, None, None, 0, None),
}


def xsurface_mesh(
    flags: int,
    count: int,
    tri_indices: bytes,
    tri_count: int,
    verts0: bytes,
    stream: bytes | None,
    surf_raw: bytes,
) -> Mesh:
    """One XSurface -> Mesh. ``verts0`` and ``stream`` are the resolved buffers
    (inline or shared)."""
    if flags & 1 == 0:
        pos = floats(verts0, count, 16, 0, 3)
        stride0 = 16
    else:
        stride0 = 8 if flags & 3 == 1 else 16
        pos = packed_positions(verts0, count, stride0, surf_raw)
    normals = uvs = cols = None
    if flags & 3 == 3 and stride0 == 16:
        normals = normalise(unpack_cmp(words(verts0, count, 16, 8)))
    fmt = flags & 7
    layout = STREAM_LAYOUT.get(fmt)
    if stream is not None and layout is not None:
        stride, n_off, _t_off, uv_off, c_off = layout
        if len(stream) >= count * stride:
            if n_off is not None:
                normals = normalise(unpack_cmp(words(stream, count, stride, n_off)))
            uvs = halves(stream, count, stride, uv_off)
            if c_off is not None:
                cols = colours(stream, count, stride, c_off)
    tris = np.frombuffer(tri_indices, ">u2", 3 * tri_count).astype(np.int64).reshape(-1, 3)
    return Mesh(pos, tris, normals, uvs, cols)


def xsurface_buffer_sizes(flags: int, count: int) -> tuple[int, int]:
    """(verts0 bytes, stream bytes) the loader reads for a surface (structs-map.md 9)."""
    v0 = 8 * count if flags & 3 == 1 else 16 * count
    fmt = flags & 7
    stream = {0: 16, 1: 16, 2: 16, 3: 8, 5: 12, 7: 4}.get(fmt, 0) * count
    return v0, stream
