"""Seeded procedural voxel terrain for a blocky map.

A grid of cubes (``block`` units each, default 64) carrying a material per cell: rolling
hills from 2D fractal value-noise, one or two caves carved from 3D noise, scattered trees
(log trunk, leaf cuboid), a water plane at a sea level and a small flat village of plank hut
shells. Everything is deterministic from the seed. The terrain is the input to greedy
meshing (``greedy.py``) and the map writer (``mapwriter.py``).

Style note: "blocky voxel terrain" is the only resemblance to any other game; every material,
texture and shape here is this project's own. See docs/mapgen.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from . import noise

# Material ids (cell values). AIR is empty; WATER is the sea; the rest are opaque solids.
AIR = 0
STONE = 1
DIRT = 2
GRASS = 3
SAND = 4
WATER = 5
LOG = 6
LEAVES = 7
PLANK = 8
COBBLE = 9

MATERIAL_NAMES = {
    STONE: "stone",
    DIRT: "dirt",
    GRASS: "grass",
    SAND: "sand",
    WATER: "water",
    LOG: "log",
    LEAVES: "leaves",
    PLANK: "plank",
    COBBLE: "cobble",
}
#: Opaque solids: a face between two of these is hidden.
OPAQUE = frozenset({STONE, DIRT, GRASS, SAND, LOG, LEAVES, PLANK, COBBLE})


@dataclass
class Terrain:
    grid: np.ndarray  # (nx, ny, nz) uint8, indexed [x, y, z], z up
    block: int
    origin: tuple[int, int, int]  # world coords of grid corner (x, y, z) at (0, 0, 0)
    sea_level: int  # highest water cell index (k)
    heights: np.ndarray  # (nx, ny) int: solid surface height in cells per column
    village: tuple[int, int, int, int]  # x0, y0, x1, y1 in cells (flat hut area)
    seed: int = 0
    counts: dict = field(default_factory=dict)

    @property
    def shape(self) -> tuple[int, int, int]:
        return tuple(int(n) for n in self.grid.shape)

    def world_bounds(self) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
        nx, ny, nz = self.shape
        ox, oy, oz = self.origin
        return (ox, oy, oz), (ox + nx * self.block, oy + ny * self.block, oz + nz * self.block)

    def cell_centre_world(self, i: int, j: int, k: int) -> tuple[float, float, float]:
        ox, oy, oz = self.origin
        b = self.block
        return (ox + (i + 0.5) * b, oy + (j + 0.5) * b, oz + (k + 0.5) * b)


def _heightmap(nx: int, ny: int, seed: int, floor_k: int, ceil_k: int) -> np.ndarray:
    xs = np.arange(nx)[:, None].astype(np.float64)
    ys = np.arange(ny)[None, :].astype(np.float64)
    # Two scales of hills: broad rolling shape plus finer bumps.
    broad = noise.fbm2(xs / 18.0, ys / 18.0, seed, octaves=4, persistence=0.55)
    fine = noise.fbm2(xs / 6.0, ys / 6.0, seed + 7, octaves=3, persistence=0.5)
    h = 0.78 * broad + 0.22 * fine
    h = (h - h.min()) / max(1e-9, (h.max() - h.min()))
    return np.rint(floor_k + h * (ceil_k - floor_k)).astype(np.int32)


def _carve_caves(grid: np.ndarray, heights: np.ndarray, seed: int) -> int:
    """Carve air pockets from 3D noise in a band below the surface. Returns cells removed."""
    nx, ny, nz = grid.shape
    xs = np.arange(nx)[:, None, None].astype(np.float64)
    ys = np.arange(ny)[None, :, None].astype(np.float64)
    zs = np.arange(nz)[None, None, :].astype(np.float64)
    field3 = noise.fbm3(xs / 9.0, ys / 9.0, zs / 7.0, seed + 31, octaves=3, persistence=0.55)
    k = np.arange(nz)[None, None, :]
    surf = heights[:, :, None]
    # Below the surface by 2+ cells and above the map floor: a thin high-density band is a cave.
    band = (k < surf - 2) & (k >= 2)
    cave = band & (field3 > 0.62) & (grid != AIR) & (grid != WATER)
    grid[cave] = AIR
    return int(cave.sum())


def _add_tree(grid: np.ndarray, i: int, j: int, top: int, height: int) -> None:
    nx, ny, nz = grid.shape
    trunk_top = min(nz - 1, top + height)
    grid[i, j, top:trunk_top] = LOG
    cz = trunk_top
    # Leaf cuboid: a 5x5x3 canopy, corners trimmed, centred on the trunk top.
    for dz in range(-1, 2):
        z = cz + dz
        if not 0 <= z < nz:
            continue
        r = 2 if dz < 1 else 1
        for dx in range(-r, r + 1):
            for dy in range(-r, r + 1):
                if abs(dx) == r and abs(dy) == r:
                    continue  # trim corners
                x, y = i + dx, j + dy
                if 0 <= x < nx and 0 <= y < ny and grid[x, y, z] in (AIR,):
                    grid[x, y, z] = LEAVES
    if 0 <= cz + 1 < nz and grid[i, j, cz + 1] == AIR:
        grid[i, j, cz + 1] = LEAVES


def _place_trees(grid: np.ndarray, heights: np.ndarray, seed: int, village, sea: int) -> int:
    nx, ny, nz = grid.shape
    vx0, vy0, vx1, vy1 = village
    # Deterministic candidate density from noise; accept the strongest local peaks.
    xs = np.arange(nx)[:, None].astype(np.float64)
    ys = np.arange(ny)[None, :].astype(np.float64)
    density = noise.fbm2(xs / 3.5, ys / 3.5, seed + 99, octaves=2)
    count = 0
    for i in range(2, nx - 2):
        for j in range(2, ny - 2):
            if vx0 - 1 <= i <= vx1 and vy0 - 1 <= j <= vy1:
                continue  # keep the village clear
            top = int(heights[i, j])
            if top <= sea or top + 1 >= nz:
                continue
            if grid[i, j, top - 1] != GRASS:
                continue
            if density[i, j] < 0.80:
                continue
            # thin to roughly one tree per 3x3 by a coordinate hash
            if noise.value3(np.array(i), np.array(j), np.array(0.0), seed + 5) > 0.5:
                h = 3 + int(noise.value3(np.array(i), np.array(j), np.array(1.0), seed + 6) * 3)
                _add_tree(grid, i, j, top, h)
                count += 1
    return count


def _build_village(grid: np.ndarray, heights: np.ndarray, seed: int, village, floor_k: int) -> int:
    """Flatten the village area and raise a few plank hut shells on a cobble pad."""
    vx0, vy0, vx1, vy1 = village
    nx, ny, nz = grid.shape
    pad = int(np.median(heights[vx0:vx1, vy0:vy1])) if vx1 > vx0 else floor_k
    pad = max(floor_k + 1, pad)
    # Flat ground: solid up to pad-1 (grass top), air above; cobble pad square.
    for i in range(vx0, vx1):
        for j in range(vy0, vy1):
            grid[i, j, :pad] = np.where(np.arange(pad) >= pad - 3, DIRT, STONE)
            grid[i, j, pad - 1] = GRASS
            grid[i, j, pad:] = AIR
            heights[i, j] = pad
            grid[i, j, pad - 1] = COBBLE  # village path/pad is cobble on top
    huts = 0
    # Two hut shells inside the pad.
    plan = [
        (vx0 + 2, vy0 + 2, 6, 5),
        (vx0 + 10, vy0 + 3, 5, 6),
    ]
    for hx, hy, w, d in plan:
        if hx + w >= vx1 or hy + d >= vy1:
            continue
        wall_h = 4
        for i in range(hx, hx + w):
            for j in range(hy, hy + d):
                edge = i in (hx, hx + w - 1) or j in (hy, hy + d - 1)
                if edge:
                    for k in range(pad, pad + wall_h):
                        grid[i, j, k] = PLANK
                # plank floor and flat plank roof
                grid[i, j, pad - 1] = PLANK
                grid[i, j, pad + wall_h] = PLANK
        # a door opening on the -x wall, middle, two cells high
        dj = hy + d // 2
        grid[hx, dj, pad : pad + 2] = AIR
        huts += 1
    return huts


def generate(
    nx: int = 40,
    ny: int = 40,
    nz: int = 32,
    block: int = 64,
    seed: int = 1,
) -> Terrain:
    """A deterministic :class:`Terrain` for the given seed and grid size."""
    grid = np.zeros((nx, ny, nz), np.uint8)
    floor_k = 4
    ceil_k = nz - 8
    sea = floor_k + max(2, (ceil_k - floor_k) // 4)
    heights = _heightmap(nx, ny, seed, floor_k, ceil_k)
    k = np.arange(nz)[None, None, :]
    surf = heights[:, :, None]
    # Columns: stone below, 3 dirt, grass (or sand near water) on top.
    solid = k < surf
    grid[solid.repeat(1, 0) & (k < surf - 3)] = STONE
    band = solid & (k >= surf - 3)
    grid[band] = DIRT
    top_cell = k == surf - 1
    grid[top_cell] = GRASS
    # Beaches: top cells at or just above the sea become sand.
    beach = top_cell & (surf - 1 <= sea + 1)
    grid[beach] = SAND
    # Sand floor under the sea.
    underwater_top = top_cell & (surf - 1 < sea)
    grid[underwater_top] = SAND
    # Water fills empty cells up to the sea level.
    water = (grid == AIR) & (k <= sea)
    grid[water] = WATER

    village = (nx // 2 - 9, ny // 2 - 7, nx // 2 + 9, ny // 2 + 7)
    huts = _build_village(grid, heights, seed, village, floor_k)
    caves = _carve_caves(grid, heights, seed)
    trees = _place_trees(grid, heights, seed, village, sea)

    counts = {
        "caves_cells": caves,
        "trees": trees,
        "huts": huts,
        "solid_cells": int(np.isin(grid, list(OPAQUE)).sum()),
        "water_cells": int((grid == WATER).sum()),
    }
    return Terrain(
        grid=grid,
        block=block,
        origin=(-nx * block // 2, -ny * block // 2, 0),
        sea_level=sea,
        heights=heights,
        village=village,
        seed=seed,
        counts=counts,
    )
