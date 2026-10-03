"""Greedy box meshing of a voxel :class:`~opent5.mapgen.terrain.Terrain`.

Same-material cells are merged into the largest axis-aligned boxes (3D run growth), so one
brush stands for many cubes and the collision-brush count stays far under the engine cap
(``clipMap_t.numBrushes`` is ``uint16_t``, OpenAssetTools src/Common/Game/T5/T5_Assets.h: at
most 65535 brushes; the gfx index buffer is ``uint16_t*``, so each surface group is
u16-addressable). Each box face is then textured by its material and direction, or caulked
when it is hidden against opaque neighbours (no gfx surface, collision kept). See docs/mapgen.md.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .terrain import AIR, GRASS, LOG, MATERIAL_NAMES, OPAQUE, WATER, Terrain

#: Face order and outward normal direction, matching tools/testmap.py FACES.
FACES = ("bottom", "top", "ymin", "xmax", "ymax", "xmin")
_NORMAL = {
    "bottom": (0, 0, -1),
    "top": (0, 0, 1),
    "ymin": (0, -1, 0),
    "xmax": (1, 0, 0),
    "ymax": (0, 1, 0),
    "xmin": (-1, 0, 0),
}

CAULK = "caulk"


@dataclass
class Box:
    lo: tuple[int, int, int]  # world coords
    hi: tuple[int, int, int]
    material: int
    faces: dict  # face name -> tile name (CAULK for hidden)


def face_tile(material: int, face: str) -> str:
    """The texture tile name for a material's face (grass and log are per-direction)."""
    if material == GRASS:
        if face == "top":
            return "grass_top"
        if face == "bottom":
            return "dirt"
        return "grass_side"
    if material == LOG:
        return "log_top" if face in ("top", "bottom") else "log_side"
    return MATERIAL_NAMES[material]


def _grow(grid: np.ndarray, done: np.ndarray, i: int, j: int, k: int, m: int):
    """Largest box of material ``m`` with corner (i, j, k), extending x, then y, then z."""
    nx, ny, nz = grid.shape

    def run_x(jj, kk, i0):
        w = 0
        while i0 + w < nx and grid[i0 + w, jj, kk] == m and not done[i0 + w, jj, kk]:
            w += 1
        return w

    w = run_x(j, k, i)
    # extend along y while each row matches the full width
    d = 1
    while j + d < ny:
        ok = all(grid[i + a, j + d, k] == m and not done[i + a, j + d, k] for a in range(w))
        if not ok:
            break
        d += 1
    # extend along z while each x-y slab matches
    h = 1
    while k + h < nz:
        ok = True
        for a in range(w):
            for b in range(d):
                if grid[i + a, j + b, k + h] != m or done[i + a, j + b, k + h]:
                    ok = False
                    break
            if not ok:
                break
        if not ok:
            break
        h += 1
    return w, d, h


def _see_through(grid: np.ndarray, x: int, y: int, z: int, material: int) -> bool:
    """Is the neighbour cell (x, y, z) a reason to draw the face? Out of bounds = sky = yes."""
    nx, ny, nz = grid.shape
    if not (0 <= x < nx and 0 <= y < ny and 0 <= z < nz):
        return True
    n = grid[x, y, z]
    if material == WATER:
        return n == AIR  # water is drawn only at the air boundary
    return n == AIR or n == WATER  # opaque solids show against air or water


def _face_exposed(grid, box_i0, box_j0, box_k0, w, d, h, face, material) -> bool:
    dx, dy, dz = _NORMAL[face]
    # the layer of neighbour cells just outside this face
    if dx:
        x = box_i0 + (w if dx > 0 else -1)
        return any(
            _see_through(grid, x, box_j0 + b, box_k0 + c, material)
            for b in range(d)
            for c in range(h)
        )
    if dy:
        y = box_j0 + (d if dy > 0 else -1)
        return any(
            _see_through(grid, box_i0 + a, y, box_k0 + c, material)
            for a in range(w)
            for c in range(h)
        )
    z = box_k0 + (h if dz > 0 else -1)
    return any(
        _see_through(grid, box_i0 + a, box_j0 + b, z, material) for a in range(w) for b in range(d)
    )


def mesh(terrain: Terrain) -> tuple[list[Box], dict]:
    """Greedy-mesh the terrain into textured, caulked brushes. Returns (boxes, counts)."""
    grid = terrain.grid
    nx, ny, nz = grid.shape
    b = terrain.block
    ox, oy, oz = terrain.origin
    meshable = set(OPAQUE) | {WATER}
    done = np.zeros_like(grid, dtype=bool)
    boxes: list[Box] = []
    surfaces = 0
    for k in range(nz):
        for j in range(ny):
            for i in range(nx):
                m = int(grid[i, j, k])
                if m not in meshable or done[i, j, k]:
                    continue
                w, d, h = _grow(grid, done, i, j, k, m)
                done[i : i + w, j : j + d, k : k + h] = True
                faces = {}
                for face in FACES:
                    if _face_exposed(grid, i, j, k, w, d, h, face, m):
                        faces[face] = face_tile(m, face)
                        surfaces += 1
                    else:
                        faces[face] = CAULK
                lo = (ox + i * b, oy + j * b, oz + k * b)
                hi = (ox + (i + w) * b, oy + (j + d) * b, oz + (k + h) * b)
                boxes.append(Box(lo, hi, m, faces))
    counts = {
        "brushes": len(boxes),
        "surfaces": surfaces,  # non-caulk quads = gfx surfaces (pre-weld)
        "vertices_est": surfaces * 4,
        "indices_est": surfaces * 6,
        "caulk_faces": len(boxes) * 6 - surfaces,
    }
    return boxes, counts
