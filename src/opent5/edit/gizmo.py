"""Pure gizmo and transform maths for the in-place map editor (no Qt, no GPU).

The 3D editor drags an object along an axis or rotates it about an axis. The maths that
turn a cursor ray into a translation or a rotation live here, as plain numpy functions, so
they can be unit tested without a window, the way the camera maths in
``opent5.gui.views.mesh`` are. The interactive widget feeds them the camera basis it already
has and keeps the small amount of per-drag state (the starting axis parameter or the
starting hit point).

Coordinates match the viewer: Z is up, lengths are world units, and an entity's ``angles``
string is "pitch yaw roll" in degrees (pitch about Y, yaw about Z, roll about X), as the
game stores it.
"""

from __future__ import annotations

import math

import numpy as np

EPS = 1e-9

#: angles component index driven by a rotation about each world axis.
#: X -> roll (index 2), Y -> pitch (index 0), Z -> yaw (index 1).
AXIS_TO_ANGLE = (2, 0, 1)


def _unit(v: np.ndarray) -> np.ndarray:
    v = np.asarray(v, np.float64)
    n = float(np.linalg.norm(v))
    return v / n if n > EPS else v


def ray_plane(origin, direction, plane_point, plane_normal):
    """World point where a ray meets a plane, or ``None`` when it is parallel or the plane
    lies behind the ray's origin."""
    o = np.asarray(origin, np.float64)
    d = np.asarray(direction, np.float64)
    p = np.asarray(plane_point, np.float64)
    n = np.asarray(plane_normal, np.float64)
    denom = float(d @ n)
    if abs(denom) < EPS:
        return None
    t = float((p - o) @ n) / denom
    if t <= 0:
        return None
    return o + d * t


def axis_param(ray_origin, ray_dir, axis_point, axis_dir) -> float:
    """The scalar ``s`` along ``axis_dir`` (from ``axis_point``) of the point on the axis
    line closest to the cursor ray. A translate drag records ``s`` at the press and moves the
    object by ``axis_dir * (s_now - s_start)``."""
    ro = np.asarray(ray_origin, np.float64)
    rd = _unit(ray_dir)
    ao = np.asarray(axis_point, np.float64)
    ad = _unit(axis_dir)
    w0 = ro - ao
    b = float(rd @ ad)
    d = float(rd @ w0)
    e = float(ad @ w0)
    denom = 1.0 - b * b  # rd and ad are unit, so a = c = 1
    if denom < EPS:  # ray parallel to the axis: project the origin onto it
        return e
    return (e - b * d) / denom


def translate_on_axis(start_origin, axis_dir, s_start: float, s_now: float) -> np.ndarray:
    """New origin for a translate drag along one axis."""
    return np.asarray(start_origin, np.float64) + _unit(axis_dir) * (s_now - s_start)


def plane_project(v, normal) -> np.ndarray:
    """``v`` with its component along ``normal`` removed."""
    v = np.asarray(v, np.float64)
    n = _unit(normal)
    return v - n * float(v @ n)


def signed_angle(a, b, normal) -> float:
    """Signed angle (radians) from ``a`` to ``b`` measured about ``normal`` (right handed).
    Both vectors are projected onto the plane of ``normal`` first."""
    n = _unit(normal)
    a = plane_project(a, n)
    b = plane_project(b, n)
    if float(np.linalg.norm(a)) < EPS or float(np.linalg.norm(b)) < EPS:
        return 0.0
    return float(np.arctan2(float(n @ np.cross(a, b)), float(a @ b)))


def rotation_delta(centre, axis_dir, hit_start, hit_now) -> float:
    """Signed rotation (radians) about ``axis_dir`` from one hit point to another, both in
    the plane through ``centre``. A rotate drag records ``hit_start`` (``ray_plane`` of the
    press against the plane normal ``axis_dir``) and asks for the delta as the cursor moves."""
    return signed_angle(
        np.asarray(hit_start, np.float64) - np.asarray(centre, np.float64),
        np.asarray(hit_now, np.float64) - np.asarray(centre, np.float64),
        axis_dir,
    )


def rotate_angles(angles_deg, axis_index: int, delta_deg: float) -> tuple[float, float, float]:
    """Add ``delta_deg`` to the ``angles`` component a rotation about world axis
    ``axis_index`` (0=X, 1=Y, 2=Z) drives, keeping each component in [0, 360)."""
    a = list(float(v) for v in angles_deg)
    while len(a) < 3:
        a.append(0.0)
    i = AXIS_TO_ANGLE[axis_index]
    a[i] = (a[i] + float(delta_deg)) % 360.0
    return (a[0], a[1], a[2])


def snap(value: float, step: float) -> float:
    """``value`` rounded to the nearest multiple of ``step`` (no snapping when step <= 0)."""
    if step <= 0:
        return float(value)
    return round(float(value) / step) * step


def snap_vec(vec, step: float) -> np.ndarray:
    v = np.asarray(vec, np.float64)
    if step <= 0:
        return v
    return np.round(v / step) * step


def duplicate_offset(origin, offset) -> np.ndarray:
    """Origin of a duplicate: the source origin shifted by ``offset``."""
    return np.asarray(origin, np.float64) + np.asarray(offset, np.float64)


# -- prop rotation about Z (clip footprint and render axes) -----------------------------------


def yaw_matrix(degrees: float) -> np.ndarray:
    """Rotation about world Z (yaw) by ``degrees``, as a 3x3 applied to a column vector
    on the right (``v @ R`` rotates row vectors the same way the render's axis rows do)."""
    a = math.radians(float(degrees))
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, s, 0.0], [-s, c, 0.0], [0.0, 0.0, 1.0]], np.float64)


def is_axial_turn(degrees: float, tol: float = 0.5) -> bool:
    """Whether ``degrees`` is (within ``tol``) a multiple of 90, so an axis-aligned box
    rotated by it is still an exact axis-aligned box. A non-axial angle cannot be
    represented by an axial clip, so the editor keeps the clip as the rotated bounding box
    and says so."""
    r = abs(float(degrees)) % 90.0
    return r <= tol or r >= 90.0 - tol


def rotated_box_aabb_z(mins, maxs, centre, degrees: float) -> tuple[tuple, tuple]:
    """The axis-aligned bounding box of ``(mins, maxs)`` after rotating it about the world
    Z axis through ``centre`` by ``degrees``. For a quarter turn this is the box with its X
    and Y extents swapped (lossless); for any other angle it is the enclosing axis-aligned
    box (an over-approximation, since an axial clip cannot slant). Z is unchanged."""
    mins = [float(v) for v in mins]
    maxs = [float(v) for v in maxs]
    cx, cy = float(centre[0]), float(centre[1])
    a = math.radians(float(degrees))
    c, s = math.cos(a), math.sin(a)
    xs, ys = [], []
    for x in (mins[0], maxs[0]):
        for y in (mins[1], maxs[1]):
            dx, dy = x - cx, y - cy
            xs.append(cx + dx * c - dy * s)
            ys.append(cy + dx * s + dy * c)
    return (
        (min(xs), min(ys), mins[2]),
        (max(xs), max(ys), maxs[2]),
    )


def project_point(point, eye, right, up, forward, focal: float, w: int, h: int):
    """Screen position of a world point for the viewer's camera: ``(sx, sy, depth,
    in_front)``. The basis vectors and focal length are the camera's own (see
    ``mesh.Camera.basis`` and ``mesh.focal``), so a billboarded marker lands exactly where
    the wireframe would draw the point."""
    p = np.asarray(point, np.float64)
    e = np.asarray(eye, np.float64)
    rel = p - e
    x = float(rel @ np.asarray(right, np.float64))
    y = float(rel @ np.asarray(up, np.float64))
    z = float(rel @ np.asarray(forward, np.float64))
    if z <= EPS:
        return 0.0, 0.0, z, False
    sx = w / 2.0 + focal * x / z
    sy = h / 2.0 - focal * y / z
    return sx, sy, z, True


def nearest_marker(points2d, cursor, max_px: float, depths=None):
    """Index of the screen marker nearest the cursor within ``max_px`` pixels, or ``None``.
    ``points2d`` is an (n, 2) array of marker screen positions; ``depths`` (optional) breaks a
    tie towards the nearer marker. Markers behind the camera are passed as NaN and ignored."""
    pts = np.asarray(points2d, np.float64).reshape(-1, 2)
    if not len(pts):
        return None
    c = np.asarray(cursor, np.float64)
    d2 = np.sum((pts - c) ** 2, axis=1)
    within = d2 <= max_px * max_px
    within &= ~np.isnan(d2)
    if not within.any():
        return None
    if depths is not None:
        order = np.where(within, np.asarray(depths, np.float64), np.inf)
        return int(np.argmin(order))
    return int(np.argmin(np.where(within, d2, np.inf)))
