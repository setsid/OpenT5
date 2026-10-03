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

from dataclasses import dataclass

import numpy as np

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


def parse_brushes(brushes: np.ndarray, sides_of) -> list[Brush]:
    """``brushes``: the schema's cbrush_t array. ``sides_of(pointer, count)`` returns the
    brush's sides as (normal, dist, cflags, sflags) tuples, or None when the pointer
    does not resolve."""
    out = []
    for i, b in enumerate(brushes):
        count, sides_ptr = int(b["numsides"]), int(b["sides"])
        planes, flags = [], []
        if count and sides_ptr:
            sides = sides_of(sides_ptr, count)
            if sides is None:
                raise ValueError(f"brush {i}: side pointer {sides_ptr:#010x} does not resolve")
            for normal, dist, cflags, sflags in sides:
                planes.append((np.asarray(normal, np.float64), float(dist)))
                flags.append((int(cflags), int(sflags)))
        out.append(
            Brush(
                np.asarray(b["mins"], np.float64),
                np.asarray(b["maxs"], np.float64),
                int(b["contents"]),
                planes,
                flags,
            )
        )
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


def collision_triangles(verts: np.ndarray | None, tri_indices: np.ndarray | None):
    """The clipMap's verts (vec3) and triIndices (u16[3]) arrays."""
    if verts is None or tri_indices is None or not len(tri_indices):
        return np.zeros((0, 3), np.float32), np.zeros((0, 3), np.int64)
    return np.asarray(verts, np.float32).reshape(-1, 3), np.asarray(tri_indices).astype(
        np.int64
    ).reshape(-1, 3)
