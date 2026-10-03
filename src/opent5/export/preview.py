"""A small software rasteriser for checking exported geometry by eye (numpy only).

``render(positions, triangles, view)`` draws flat-shaded triangles with a
z-buffer and returns an RGB image; ``view`` is "top" (looking down -Z) or
"angle" (from above one corner). Colour is a fixed light direction against the
face normal, optionally tinted per triangle.
"""

from __future__ import annotations

import zlib

import numpy as np


def _view_matrix(view: str, yaw_deg: float = 35.0, pitch_deg: float = 55.0) -> np.ndarray:
    if view == "top":
        return np.eye(3)
    # Yaw about Z, then tilt so that world up keeps pointing up on screen: the
    # camera sits above and to the south of the scene.
    yaw, pitch = np.radians(yaw_deg), -np.radians(pitch_deg)
    rz = np.array([[np.cos(yaw), -np.sin(yaw), 0], [np.sin(yaw), np.cos(yaw), 0], [0, 0, 1]])
    rx = np.array(
        [[1, 0, 0], [0, np.cos(pitch), -np.sin(pitch)], [0, np.sin(pitch), np.cos(pitch)]]
    )
    return rx @ rz


def render(
    positions: np.ndarray,
    triangles: np.ndarray,
    view: str = "top",
    size: int = 1024,
    tints: np.ndarray | None = None,
    bounds: tuple[np.ndarray, np.ndarray] | None = None,
    yaw: float = 35.0,
    pitch: float = 55.0,
) -> np.ndarray:
    """Orthographic flat-shaded render -> uint8 (size, size, 3)."""
    p = np.asarray(positions, np.float64)
    t = np.asarray(triangles, np.int64)
    if bounds is not None:
        lo, hi = bounds
        inside = np.all((p[t] >= lo) & (p[t] <= hi), axis=(1, 2))
        t = t[inside]
        if tints is not None:
            tints = tints[inside]
    img = np.full((size, size, 3), 24, np.uint8)
    if len(t) == 0:
        return img
    m = _view_matrix(view, yaw, pitch)
    q = p @ m.T  # x right, y up (screen), z towards the viewer
    used = q[np.unique(t)]
    lo, hi = used.min(0), used.max(0)
    span = max(hi[0] - lo[0], hi[1] - lo[1]) or 1.0
    scale = (size - 8) / span
    sx = (q[:, 0] - (lo[0] + hi[0]) / 2) * scale + size / 2
    sy = size / 2 - (q[:, 1] - (lo[1] + hi[1]) / 2) * scale
    sz = q[:, 2]
    a, b, c = p[t[:, 0]], p[t[:, 1]], p[t[:, 2]]
    n = np.cross(b - a, c - a)
    n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)
    light = np.array([0.35, 0.25, 0.9])
    light /= np.linalg.norm(light)
    shade = 0.25 + 0.75 * np.abs(n @ light)
    base = np.full((len(t), 3), 200.0) if tints is None else np.asarray(tints, np.float64)
    colour = np.clip(base * shade[:, None], 0, 255).astype(np.uint8)
    zbuf = np.full((size, size), -np.inf)
    x0, y0, z0 = sx[t[:, 0]], sy[t[:, 0]], sz[t[:, 0]]
    x1, y1, z1 = sx[t[:, 1]], sy[t[:, 1]], sz[t[:, 1]]
    x2, y2, z2 = sx[t[:, 2]], sy[t[:, 2]], sz[t[:, 2]]
    minx = np.clip(np.floor(np.minimum(np.minimum(x0, x1), x2)), 0, size - 1).astype(int)
    maxx = np.clip(np.ceil(np.maximum(np.maximum(x0, x1), x2)), 0, size - 1).astype(int)
    miny = np.clip(np.floor(np.minimum(np.minimum(y0, y1), y2)), 0, size - 1).astype(int)
    maxy = np.clip(np.ceil(np.maximum(np.maximum(y0, y1), y2)), 0, size - 1).astype(int)
    area = (x1 - x0) * (y2 - y0) - (x2 - x0) * (y1 - y0)
    for i in np.nonzero(np.abs(area) > 1e-9)[0]:
        xs = np.arange(minx[i], maxx[i] + 1) + 0.5
        ys = np.arange(miny[i], maxy[i] + 1) + 0.5
        if len(xs) == 0 or len(ys) == 0:
            continue
        gx, gy = np.meshgrid(xs, ys)
        w0 = ((x1[i] - gx) * (y2[i] - gy) - (x2[i] - gx) * (y1[i] - gy)) / area[i]
        w1 = ((x2[i] - gx) * (y0[i] - gy) - (x0[i] - gx) * (y2[i] - gy)) / area[i]
        w2 = 1.0 - w0 - w1
        inside = (w0 >= -1e-6) & (w1 >= -1e-6) & (w2 >= -1e-6)
        if not inside.any():
            # Sub-pixel triangle: plot its first vertex.
            px, py = int(np.clip(x0[i], 0, size - 1)), int(np.clip(y0[i], 0, size - 1))
            if z0[i] > zbuf[py, px]:
                zbuf[py, px] = z0[i]
                img[py, px] = colour[i]
            continue
        z = w0 * z0[i] + w1 * z1[i] + w2 * z2[i]
        ys_i = (gy[inside] - 0.5).astype(int)
        xs_i = (gx[inside] - 0.5).astype(int)
        zi = z[inside]
        closer = zi > zbuf[ys_i, xs_i]
        ys_i, xs_i, zi = ys_i[closer], xs_i[closer], zi[closer]
        zbuf[ys_i, xs_i] = zi
        img[ys_i, xs_i] = colour[i]
    return img


def tint_by_key(keys: list[str] | np.ndarray) -> np.ndarray:
    """A stable pastel colour per distinct key."""
    out = np.empty((len(keys), 3))
    cache: dict = {}
    for i, k in enumerate(keys):
        if k not in cache:
            h = zlib.crc32(str(k).encode()) & 0xFFFFFF
            cache[k] = np.array(
                [120 + (h & 0x7F), 120 + ((h >> 8) & 0x7F), 120 + ((h >> 16) & 0x7F)]
            )
        out[i] = cache[k]
    return out
