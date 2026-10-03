"""clipMap (col_map_mp / col_map_sp) -> collision geometry.

Brushes are convex volumes: the six axial planes given by the brush's mins and
maxs plus its extra sides (cbrush_t +0x1c count, +0x20 offset pointer to its
cbrushside_t run; each side's +0x0 is an offset pointer to a 20-byte cplane_s:
normal, dist). Each face is built by clipping a large square on its plane by
every other plane. Collision triangles are the clipMap's verts (12 bytes each)
indexed by triIndices (three u16 each). Layouts: structs-map.md section 7 and
the errata block (sides at +0x20).
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

import numpy as np

BRUSH_SIZE = 0x60
SIDE_SIZE = 0xC
PLANE_SIZE = 0x14
EPS = 0.01
WELD = 0.01
MIN_AREA = 0.01
BIG = 1.0e6


@dataclass
class Brush:
    mins: np.ndarray
    maxs: np.ndarray
    contents: int
    planes: list[tuple[np.ndarray, float]]  # extra sides only
    side_flags: list[tuple[int, int]]


def parse_brushes(brushes: bytes, read_pointer) -> list[Brush]:
    """``read_pointer(raw, size)`` resolves an offset pointer to bytes (MemoryMap.read)."""
    out = []
    for at in range(0, len(brushes) - BRUSH_SIZE + 1, BRUSH_SIZE):
        mins = np.array(struct.unpack_from(">3f", brushes, at), np.float64)
        (contents,) = struct.unpack_from(">i", brushes, at + 0xC)
        maxs = np.array(struct.unpack_from(">3f", brushes, at + 0x10), np.float64)
        count, sides_raw = struct.unpack_from(">II", brushes, at + 0x1C)
        planes, flags = [], []
        if count and sides_raw:
            sides = read_pointer(sides_raw, SIDE_SIZE * count)
            if sides is None:
                raise ValueError(
                    f"brush at {at:#x}: side pointer {sides_raw:#010x} does not resolve"
                )
            for k in range(count):
                plane_raw, cflags, sflags = struct.unpack_from(">Iii", sides, SIDE_SIZE * k)
                plane = read_pointer(plane_raw, PLANE_SIZE)
                if plane is None:
                    raise ValueError(
                        f"brush at {at:#x} side {k}: plane pointer {plane_raw:#010x} does not "
                        "resolve"
                    )
                nx, ny, nz, dist = struct.unpack_from(">4f", plane, 0)
                planes.append((np.array([nx, ny, nz]), dist))
                flags.append((cflags, sflags))
        out.append(Brush(mins, maxs, contents, planes, flags))
    return out


def _clip(poly: np.ndarray, normal: np.ndarray, dist: float) -> np.ndarray:
    """Keep the part of a convex polygon with normal . p <= dist."""
    if len(poly) == 0:
        return poly
    d = poly @ normal - dist
    inside = d <= EPS
    if inside.all():
        return poly
    if not inside.any():
        return poly[:0]
    out = []
    n = len(poly)
    for i in range(n):
        a, b = poly[i], poly[(i + 1) % n]
        da, db = d[i], d[(i + 1) % n]
        if da <= EPS:
            out.append(a)
        if (da <= EPS) != (db <= EPS):
            t = da / (da - db)
            out.append(a + t * (b - a))
    return np.array(out).reshape(-1, 3)


def _base_square(normal: np.ndarray, dist: float, size: float) -> np.ndarray:
    n = normal / np.linalg.norm(normal)
    up = np.array([0.0, 0.0, 1.0]) if abs(n[2]) < 0.9 else np.array([1.0, 0.0, 0.0])
    u = np.cross(up, n)
    u /= np.linalg.norm(u)
    v = np.cross(n, u)
    c = n * dist
    # Counter-clockwise seen from outside (along +n).
    return np.array(
        [
            c - u * size - v * size,
            c + u * size - v * size,
            c + u * size + v * size,
            c - u * size + v * size,
        ]
    )


def brush_planes(brush: Brush) -> list[tuple[np.ndarray, float]]:
    axial = []
    for k in range(3):
        n = np.zeros(3)
        n[k] = 1.0
        axial.append((n, float(brush.maxs[k])))
        axial.append((-n, -float(brush.mins[k])))
    return axial + [(n, float(d)) for n, d in brush.planes]


def brush_faces(brush: Brush) -> list[np.ndarray]:
    """Convex polygons (k, 3) of the brush's faces, counter-clockwise from outside."""
    planes = brush_planes(brush)
    size = float(np.max(np.abs(np.concatenate([brush.mins, brush.maxs])))) * 2 + 64.0
    size = min(size, BIG)
    faces = []
    for i, (n, d) in enumerate(planes):
        if not np.any(n):
            continue
        poly = _base_square(n, d / np.linalg.norm(n) if np.linalg.norm(n) else d, size)
        for j, (m, e) in enumerate(planes):
            if i == j:
                continue
            poly = _clip(poly, m, e)
            if len(poly) < 3:
                break
        poly = _dedupe(poly)
        if len(poly) >= 3 and _area(poly) > MIN_AREA:
            faces.append(poly)
    return faces


def _dedupe(poly: np.ndarray) -> np.ndarray:
    if len(poly) < 2:
        return poly
    keep = np.linalg.norm(poly - np.roll(poly, -1, axis=0), axis=1) > WELD
    return poly[keep]


def _area(poly: np.ndarray) -> float:
    a = poly[1:-1] - poly[0]
    b = poly[2:] - poly[0]
    return float(0.5 * np.linalg.norm(np.cross(a, b), axis=1).sum())


def triangulate(faces: list[np.ndarray]) -> tuple[np.ndarray, np.ndarray]:
    """Fan-triangulate polygons -> (positions, triangles)."""
    pos, tris, at = [], [], 0
    for f in faces:
        pos.append(f)
        k = len(f)
        tris.extend([(at, at + i, at + i + 1) for i in range(1, k - 1)])
        at += k
    if not pos:
        return np.zeros((0, 3)), np.zeros((0, 3), np.int64)
    return np.concatenate(pos), np.array(tris, np.int64).reshape(-1, 3)


def collision_triangles(verts: bytes | None, tri_indices: bytes | None):
    if not verts or not tri_indices:
        return np.zeros((0, 3), np.float32), np.zeros((0, 3), np.int64)
    p = np.frombuffer(bytes(verts), ">f4").reshape(-1, 3).astype(np.float32)
    t = np.frombuffer(bytes(tri_indices), ">u2").reshape(-1, 3).astype(np.int64)
    return p, t
