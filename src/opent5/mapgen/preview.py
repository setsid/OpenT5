"""Render a :class:`~opent5.mapgen.terrain.Terrain` to PNG for inspection (not engine output).

``top_view`` is a height-shaded plan; ``angle_view`` is a simple back-to-front isometric paint.
Both are numpy only and go through :func:`opent5.formats.texture.write_png`. See docs/mapgen.md.
"""

from __future__ import annotations

import numpy as np

from opent5.formats import texture as tx

from .terrain import (
    AIR,
    COBBLE,
    DIRT,
    GRASS,
    LEAVES,
    LOG,
    PLANK,
    SAND,
    STONE,
    WATER,
    Terrain,
)

_COLOUR = {
    GRASS: (86, 141, 54),
    DIRT: (123, 86, 57),
    STONE: (130, 130, 134),
    SAND: (221, 203, 140),
    WATER: (48, 92, 168),
    LOG: (106, 78, 48),
    LEAVES: (46, 96, 40),
    PLANK: (150, 110, 66),
    COBBLE: (120, 120, 124),
}


def _top_cell(t: Terrain, i: int, j: int) -> tuple[int, int]:
    """(material, k) of the highest non-air cell in a column, or (AIR, -1)."""
    col = t.grid[i, j]
    nz = col.shape[0]
    for k in range(nz - 1, -1, -1):
        if col[k] != AIR:
            return int(col[k]), k
    return AIR, -1


def top_view(t: Terrain, px: int = 10) -> np.ndarray:
    nx, ny, nz = t.shape
    img = np.full((ny * px, nx * px, 4), 20, np.uint8)
    img[..., 3] = 255
    for i in range(nx):
        for j in range(ny):
            m, k = _top_cell(t, i, j)
            if m == AIR:
                continue
            base = np.array(_COLOUR.get(m, (200, 0, 200)), np.float32)
            shade = 0.55 + 0.45 * (k / max(1, nz - 1))  # higher = brighter
            col = np.clip(base * shade, 0, 255).astype(np.uint8)
            y = (ny - 1 - j) * px  # north (+y) at the top of the image
            x = i * px
            img[y : y + px, x : x + px, :3] = col
    return img


def angle_view(t: Terrain, px: int = 7) -> np.ndarray:
    nx, ny, nz = t.shape
    half = px // 2
    W = (nx + ny) * half + px * 2
    H = (nx + ny) * (half // 2 + 1) + nz * half + px * 4
    img = np.full((H, W, 4), 24, np.uint8)
    img[..., 3] = 255
    ox = px
    oy = H - px * 2 - (nx + ny) * (half // 2)
    order = sorted(
        ((i, j, k) for i in range(nx) for j in range(ny) for k in range(nz)),
        key=lambda c: (c[0] + c[1], c[2]),
    )
    for i, j, k in order:
        m = int(t.grid[i, j, k])
        if m == AIR:
            continue
        # skip fully buried cells (all 3 "towards camera/up" neighbours solid) for speed
        up = t.grid[i, j, k + 1] if k + 1 < nz else AIR
        fi = t.grid[i + 1, j, k] if i + 1 < nx else AIR
        fj = t.grid[i, j + 1, k] if j + 1 < ny else AIR
        if up != AIR and fi != AIR and fj != AIR:
            continue
        sx = ox + (i - j) * half + (ny) * half
        sy = oy + (i + j) * (half // 2 + 1) - k * half
        base = np.array(_COLOUR.get(m, (200, 0, 200)), np.float32)
        light = 0.65 + 0.35 * (k / max(1, nz - 1))
        col = np.clip(base * light, 0, 255).astype(np.uint8)
        y0, x0 = int(sy), int(sx)
        if 0 <= y0 < H - px and 0 <= x0 < W - px:
            img[y0 : y0 + px, x0 : x0 + px, :3] = col
            img[y0 + px - 1 : y0 + px, x0 : x0 + px, :3] = (col * 0.7).astype(np.uint8)
            img[y0 : y0 + px, x0 + px - 1 : x0 + px, :3] = (col * 0.8).astype(np.uint8)
    return img


def write(img: np.ndarray) -> bytes:
    return tx.write_png(img)
