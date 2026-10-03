"""Own value-noise, numpy only, deterministic from a seed.

A lattice of pseudo-random values is addressed by integer coordinates hashed with the
seed, so the field does not depend on the sampled grid's size or origin: the same world
coordinate gives the same value in any run with the same seed. ``fbm2``/``fbm3`` sum
octaves (fractal Brownian motion) for rolling hills and cave pockets.

Not Perlin gradient noise (which would need a gradient table); value noise with a smoothstep
is enough for blocky terrain and is simpler to keep deterministic. See docs/mapgen.md.
"""

from __future__ import annotations

import numpy as np

_M = np.uint64(0xFFFFFFFF)  # all arithmetic is 32-bit, carried in uint64 to avoid overflow


def _mix(h: np.ndarray) -> np.ndarray:
    """A SplitMix32-style avalanche: integer in, well-mixed 32-bit value out (as uint64)."""
    h = h.astype(np.uint64) & _M
    h = (h ^ (h >> np.uint64(16))) & _M
    h = (h * np.uint64(0x7FEB352D)) & _M
    h = (h ^ (h >> np.uint64(15))) & _M
    h = (h * np.uint64(0x846CA68B)) & _M
    h = (h ^ (h >> np.uint64(16))) & _M
    return h


def _lattice(ix: np.ndarray, iy: np.ndarray, iz: np.ndarray, seed: int) -> np.ndarray:
    """A value in [0, 1) at each integer lattice point, hashed from its coordinates."""
    ix = ix.astype(np.uint64) & _M
    iy = iy.astype(np.uint64) & _M
    iz = iz.astype(np.uint64) & _M
    h = _mix((ix * np.uint64(0x1B873593)) & _M)
    h = _mix(h ^ ((iy * np.uint64(0x19E3779B)) & _M))
    h = _mix(h ^ ((iz * np.uint64(0x85EBCA77)) & _M))
    h = _mix(h ^ np.uint64(seed & 0xFFFFFFFF))
    return h.astype(np.float64) / 4294967296.0


def _smooth(t: np.ndarray) -> np.ndarray:
    """Quintic smoothstep 6t^5 - 15t^4 + 10t^3 (zero first and second derivative at 0, 1)."""
    return t * t * t * (t * (t * 6 - 15) + 10)


def value3(x: np.ndarray, y: np.ndarray, z: np.ndarray, seed: int) -> np.ndarray:
    """Trilinearly interpolated value noise at float coordinates, in [0, 1)."""
    x = np.asarray(x, np.float64)
    y = np.asarray(y, np.float64)
    z = np.asarray(z, np.float64)
    x0, y0, z0 = np.floor(x), np.floor(y), np.floor(z)
    fx, fy, fz = _smooth(x - x0), _smooth(y - y0), _smooth(z - z0)
    ix, iy, iz = x0.astype(np.int64), y0.astype(np.int64), z0.astype(np.int64)
    out = np.zeros(np.broadcast(x, y, z).shape, np.float64)
    for dx in (0, 1):
        for dy in (0, 1):
            for dz in (0, 1):
                c = _lattice(ix + dx, iy + dy, iz + dz, seed)
                wx = fx if dx else 1 - fx
                wy = fy if dy else 1 - fy
                wz = fz if dz else 1 - fz
                out = out + c * wx * wy * wz
    return out


def fbm2(
    x: np.ndarray,
    y: np.ndarray,
    seed: int,
    octaves: int = 4,
    frequency: float = 1.0,
    persistence: float = 0.5,
    lacunarity: float = 2.0,
) -> np.ndarray:
    """Fractal value noise over a 2D field (z held at 0), normalised to [0, 1]."""
    total = np.zeros(np.broadcast(x, y).shape, np.float64)
    amp, freq, norm = 1.0, frequency, 0.0
    zero = np.zeros_like(np.asarray(x, np.float64))
    for o in range(octaves):
        total += amp * value3(np.asarray(x) * freq, np.asarray(y) * freq, zero + o * 0.37, seed + o)
        norm += amp
        amp *= persistence
        freq *= lacunarity
    return total / norm


def fbm3(
    x: np.ndarray,
    y: np.ndarray,
    z: np.ndarray,
    seed: int,
    octaves: int = 3,
    frequency: float = 1.0,
    persistence: float = 0.5,
    lacunarity: float = 2.0,
) -> np.ndarray:
    """Fractal value noise over a 3D field, normalised to [0, 1]."""
    total = np.zeros(np.broadcast(x, y, z).shape, np.float64)
    amp, freq, norm = 1.0, frequency, 0.0
    for o in range(octaves):
        total += amp * value3(
            np.asarray(x) * freq, np.asarray(y) * freq, np.asarray(z) * freq, seed + 101 * o
        )
        norm += amp
        amp *= persistence
        freq *= lacunarity
    return total / norm
