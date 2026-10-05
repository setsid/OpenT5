"""Wireframe view of world geometry, collision and models.

No OpenGL: every frame is projected with numpy and the edges are rasterised
into a float buffer (points sampled along each edge, one per pixel of length,
summed with ``np.bincount``), then tone-mapped into a QImage with the theme's
``wire`` / ``wire_far`` / ``base`` tokens. That runs the same offscreen and on
any GPU. Edge density shades itself: busy areas read brighter, as in a
wireframe drawn with additive blending. While the mouse drags, a fixed random
subset of the edges is drawn; the full set comes back on release.

The camera is an orbit camera (Z up) with a perspective and an orthographic
projection. The Top / Front / Side presets are orthographic and axis aligned;
Perspective is the free 3D view. Mouse: left drag orbits, middle drag pans, the
wheel zooms towards the cursor (towards the surface under it when one is hit).
Fly mode is a toggle (Space, or the Fly button) as well as a hold of the right
button: WASD move, Q/E down/up, Shift sprints, a drag looks around and the wheel
sets the fly speed. Double click a surface to focus it, F frames the focus, Home
frames the whole map, 1-4 pick the preset views.
"""

from __future__ import annotations

import math
import time

import numpy as np
from PySide6.QtCore import QObject, QPoint, QPointF, QRunnable, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QImage, QKeySequence, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QScrollArea,
    QSplitter,
    QStackedLayout,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from opent5.gui import theme
from opent5.gui.backend import EditError, MeshData, Ref, ZoneDoc
from opent5.gui.views.base import AssetView, empty_state
from opent5.xfile.constants import AssetType as T

FOV_DEG = 50.0
DRAG_EDGES = 40_000
NEAR = 1.0
#: Orbit look sensitivity, radians of rotation per pixel of drag. Angular, so the apparent
#: speed is the same at any distance or zoom. The canvas multiplies it by ``sensitivity``.
ORBIT_SENS = 0.006
#: Fly look sensitivity, radians per pixel; a touch calmer than the orbit, as a fly look sweeps
#: the whole view rather than rolling a pivot in front of you.
FLY_LOOK_SENS = 0.005
#: Zoom factor applied per wheel notch (``< 1`` zooms in). Multiplicative, so one notch covers
#: the same fraction of the distance however near or far you are.
ZOOM_STEP = 0.85
#: Pitch is clamped just short of the poles so a drag past straight up or straight down cannot
#: flip the view or snap the yaw. The ortho Top preset sets the pole exactly (see ``preset``).
PITCH_LIMIT = math.radians(89.5)
KIND_OF_TYPE = {T.GFX_MAP: "world", T.COL_MAP_MP: "collision", T.COL_MAP_SP: "collision",
                T.XMODEL: "model"}  # fmt: skip
KIND_TITLES = {
    "world": "World geometry",
    "world_models": "World geometry",
    "collision": "Collision",
    "model": "Model",
}
COLLISION_MODES = ("Brushes and triangles", "Brushes", "Triangles")
#: Contents-family filter for the collision overlay: label -> families passed to the session.
COLLISION_FAMILIES = (
    ("Clip walls", ("clip",)),
    ("Solid", ("solid",)),
    ("Clip + solid", ("clip", "solid")),
    ("All", ("clip", "solid", "other")),
)
#: Colour the translucent clip-brush overlay is drawn in (a distinct cyan, additive).
CLIP_OVERLAY_COLOUR = "#38bdf8"
HELP_LINES = (
    "LMB orbit   MMB pan   wheel zoom to cursor   Space / RMB fly",
    "fly: WASD/QE move   Shift sprint   drag to look   wheel sets speed",
    "1 top  2 front  3 side  4 perspective   F focus   Home frame all   H hide",
)
#: Prepended to the hint when the view can be edited but Edit mode is off.
EDIT_OFF_HINT = ("Edit is OFF: click Edit (top right) to select and move props",)
#: Prepended to the hint when Edit mode is on.
EDIT_ON_HELP = (
    "EDIT ON: click a prop to select, then drag a gizmo axis to move",
    "G move   R rotate   B add crate   Del remove   Ctrl+D duplicate   Esc deselect",
    "right-click a prop for the bundle menu   Show collision to see/remove clip walls",
)
_FLY_KEYS = frozenset(
    (Qt.Key.Key_W, Qt.Key.Key_A, Qt.Key.Key_S, Qt.Key.Key_D, Qt.Key.Key_Q, Qt.Key.Key_E)
)


# -- maths -----------------------------------------------------------------------------------


def unique_edges(triangles: np.ndarray) -> np.ndarray:
    """(m, 3) triangles -> (k, 2) undirected edges, each once."""
    t = np.asarray(triangles, np.int64).reshape(-1, 3)
    if not len(t):
        return np.zeros((0, 2), np.int64)
    e = np.concatenate([t[:, [0, 1]], t[:, [1, 2]], t[:, [2, 0]]])
    e.sort(axis=1)
    e = e[e[:, 0] != e[:, 1]]
    n = int(e.max()) + 1
    codes = np.unique(e[:, 0] * n + e[:, 1])
    return np.stack([codes // n, codes % n], 1)


#: Preset view angles ``(yaw, pitch)`` in radians and whether the preset is orthographic.
#: Top looks straight down with +X right and +Y up; Front and Side are axis-aligned ortho
#: elevations; Perspective is the free 3D view.
PRESETS = {
    "top": (math.radians(-90.0), math.radians(90.0), True),
    "front": (math.radians(-90.0), 0.0, True),
    "side": (math.radians(180.0), 0.0, True),
    "perspective": (math.radians(35.0), math.radians(30.0), False),
}


class Camera:
    """Orbit camera, Z up: yaw around Z, pitch above the horizon, distance from target.

    Projection is perspective by default; ``ortho`` switches to a parallel projection whose
    scale is set by ``ortho_half_h`` (the world half-height the view shows). The eye still
    sits ``distance`` back along the view direction, so the near/far planes and picking stay
    sensible, but in ortho the distance does not change the on-screen size of anything."""

    def __init__(self):
        self.target = np.zeros(3)
        self.yaw = math.radians(35.0)
        self.pitch = math.radians(30.0)
        self.distance = 512.0
        self.ortho = False
        self.ortho_half_h = 512.0

    def basis(self):
        cp, sp = math.cos(self.pitch), math.sin(self.pitch)
        cy, sy = math.cos(self.yaw), math.sin(self.yaw)
        forward = np.array([-cp * cy, -cp * sy, -sp])
        # Right stays horizontal from the yaw alone, so straight up or down (cp -> 0) still has
        # a defined right and up and the view does not gimbal-snap at the pole.
        right = np.array([-sy, cy, 0.0])
        up = np.cross(right, forward)
        eye = self.target - forward * self.distance
        return eye, right, up, forward

    def set_pitch(self, pitch: float) -> None:
        """Set the pitch, clamped just short of the poles so a drag cannot flip the view."""
        self.pitch = max(-PITCH_LIMIT, min(PITCH_LIMIT, pitch))

    def ortho_scale(self, h: int) -> float:
        """Pixels per world unit under the orthographic projection, for a viewport ``h`` tall."""
        return (h / 2.0) / max(self.ortho_half_h, 1e-6)

    def look_from(self, eye, target) -> None:
        """Aim the camera at ``target`` from ``eye``, leaving the eye in place."""
        self.yaw, self.pitch, self.distance = look_angles(eye, target)
        self.target = np.asarray(target, np.float64)


def focal(height: int) -> float:
    return (height / 2.0) / math.tan(math.radians(FOV_DEG) / 2.0)


def project_edges(points, edges, cam: Camera, w: int, h: int):
    """Camera-space clip at the near plane, then screen positions:
    returns (x0, y0, x1, y1, depth) arrays for the edges that survive."""
    eye, right, up, forward = cam.basis()
    basis = np.stack([right, up, forward], 1).astype(np.float32)
    cs = (points - eye.astype(np.float32)) @ basis  # (n, 3): x right, y up, z depth
    z = cs[:, 2]
    if cam.ortho:
        return _project_edges_ortho(cs, z, edges, cam, w, h)
    f = np.float32(focal(h))
    front = z > NEAR
    inv = np.where(front, f / np.where(front, z, 1), 0).astype(np.float32)
    sx = np.float32(w / 2) + cs[:, 0] * inv
    sy = np.float32(h / 2) - cs[:, 1] * inv
    ia, ib = edges[:, 0], edges[:, 1]
    fa, fb = front[ia], front[ib]
    both = fa & fb
    e = edges[both]
    a, b = e[:, 0], e[:, 1]
    x0, y0, x1, y1 = sx[a], sy[a], sx[b], sy[b]
    depth = 0.5 * (z[a] + z[b])
    cross = fa ^ fb
    if cross.any():  # one endpoint behind the near plane: move it onto the plane
        c = edges[cross]
        pa, pb = cs[c[:, 0]], cs[c[:, 1]]
        behind_a = pa[:, 2] <= NEAR
        near_pt, far_pt = np.where(behind_a[:, None], pa, pb), np.where(behind_a[:, None], pb, pa)
        t = ((NEAR - near_pt[:, 2]) / (far_pt[:, 2] - near_pt[:, 2]))[:, None]
        q = near_pt + (far_pt - near_pt) * t
        qx = w / 2 + f * q[:, 0] / q[:, 2]
        qy = h / 2 - f * q[:, 1] / q[:, 2]
        rx = w / 2 + f * far_pt[:, 0] / far_pt[:, 2]
        ry = h / 2 - f * far_pt[:, 1] / far_pt[:, 2]
        x0, y0 = np.concatenate([x0, qx]), np.concatenate([y0, qy])
        x1, y1 = np.concatenate([x1, rx]), np.concatenate([y1, ry])
        depth = np.concatenate([depth, 0.5 * (q[:, 2] + far_pt[:, 2])])
    # drop edges wholly off one side of the screen
    off = (
        ((x0 < 0) & (x1 < 0)) | ((x0 >= w) & (x1 >= w)) | ((y0 < 0) & (y1 < 0))
        | ((y0 >= h) & (y1 >= h))
    )  # fmt: skip
    keep = ~off
    return x0[keep], y0[keep], x1[keep], y1[keep], depth[keep]


def _project_edges_ortho(cs, z, edges, cam: Camera, w: int, h: int):
    """Screen positions under the parallel projection. The eye is placed well outside the
    geometry by the framing, so nothing straddles the eye plane and the perspective path's
    near-plane clip is not needed: edges with either endpoint behind the eye are simply
    dropped. The scale is constant (``ortho_scale``), so depth does not change screen size."""
    scale = np.float32(cam.ortho_scale(h))
    sx = np.float32(w / 2) + cs[:, 0] * scale
    sy = np.float32(h / 2) - cs[:, 1] * scale
    front = z > 1e-3
    e = edges[front[edges[:, 0]] & front[edges[:, 1]]]
    a, b = e[:, 0], e[:, 1]
    x0, y0, x1, y1 = sx[a], sy[a], sx[b], sy[b]
    depth = 0.5 * (z[a] + z[b])
    off = (
        ((x0 < 0) & (x1 < 0)) | ((x0 >= w) & (x1 >= w)) | ((y0 < 0) & (y1 < 0))
        | ((y0 >= h) & (y1 >= h))
    )  # fmt: skip
    keep = ~off
    return x0[keep], y0[keep], x1[keep], y1[keep], depth[keep]


def clip_to_rect(x0, y0, x1, y1, w: int, h: int):
    """Liang-Barsky against [0, w) x [0, h): returns t0, t1 per edge and a keep mask."""
    dx, dy = x1 - x0, y1 - y0
    t0 = np.zeros_like(x0)
    t1 = np.ones_like(x0)
    keep = np.ones(len(x0), bool)
    for p, q in ((-dx, x0), (dx, w - 1 - x0), (-dy, y0), (dy, h - 1 - y0)):
        zero = p == 0
        keep &= ~(zero & (q < 0))
        with np.errstate(divide="ignore", invalid="ignore"):
            r = np.where(zero, 0, q / np.where(zero, 1, p))
        t0 = np.where(~zero & (p < 0), np.maximum(t0, r), t0)
        t1 = np.where(~zero & (p > 0), np.minimum(t1, r), t1)
    keep &= t0 <= t1
    return t0, t1, keep


def rasterise(x0, y0, x1, y1, weight, w: int, h: int, extra=None):
    """Sample each segment once per pixel of length and sum into (h*w,) buffers:
    hits (``weight`` is accepted for API symmetry; every sample counts 1) and,
    optionally, the per-segment ``extra`` value."""
    t0, t1, keep = clip_to_rect(x0, y0, x1, y1, w, h)
    if not keep.all():
        x0, y0, x1, y1, t0, t1 = (v[keep] for v in (x0, y0, x1, y1, t0, t1))
        if extra is not None:
            extra = extra[keep]
    dx, dy = (x1 - x0).astype(np.float32), (y1 - y0).astype(np.float32)
    t0, t1 = t0.astype(np.float32), t1.astype(np.float32)
    sx, sy = x0 + dx * t0 + 0.5, y0 + dy * t0 + 0.5
    lx, ly = dx * (t1 - t0), dy * (t1 - t0)
    n = np.ceil(np.maximum(np.abs(lx), np.abs(ly))).astype(np.int32) + 1
    np.minimum(n, 2 * max(w, h), out=n)
    total = int(n.sum(dtype=np.int64))
    if total == 0:
        z = np.zeros(w * h, np.float32)
        return z, (z if extra is not None else None)
    inv = (1.0 / np.maximum(n - 1, 1)).astype(np.float32)
    starts = np.cumsum(n, dtype=np.int64) - n
    k = np.arange(total, dtype=np.int32) - np.repeat(starts.astype(np.int32), n)
    k = k.astype(np.float32)
    px = np.repeat(sx.astype(np.float32), n) + np.repeat(lx * inv, n) * k
    py = np.repeat(sy.astype(np.float32), n) + np.repeat(ly * inv, n) * k
    ix = px.astype(np.int32)
    iy = py.astype(np.int32)
    np.clip(ix, 0, w - 1, out=ix)
    np.clip(iy, 0, h - 1, out=iy)
    flat = iy * np.int32(w) + ix
    acc = np.bincount(flat, minlength=w * h).astype(np.float32)
    acc2 = None
    if extra is not None:
        acc2 = np.bincount(flat, weights=np.repeat(extra, n), minlength=w * h)
        acc2 = acc2.astype(np.float32)
    return acc, acc2


def _rgb(colour: str) -> np.ndarray:
    c = QColor(colour)
    return np.array([c.red(), c.green(), c.blue()], np.float32)


def nice_step(extent: float) -> float:
    """A power-of-two grid step giving roughly 16 to 32 cells across ``extent``."""
    if extent <= 0:
        return 16.0
    return float(2 ** max(0, math.floor(math.log2(extent / 16.0))))


# -- camera maths (pure, so they can be unit tested without a window) -------------------------


def cursor_ray(cam: Camera, px: float, py: float, w: int, h: int):
    """World-space ray (origin, unit direction) through a screen pixel.

    Under perspective the ray starts at the eye and fans out through the pixel; under the
    parallel projection every ray runs along the view direction and the origin is the pixel's
    own point on the eye plane. Either way picking, dragging and zoom-to-cursor feed the ray
    straight in. The pixel is in render coordinates; the direction does not depend on the
    device-pixel ratio, so logical widget coordinates are fine."""
    eye, right, up, forward = cam.basis()
    if cam.ortho:
        scale = cam.ortho_scale(h)
        origin = eye + right * ((px - w / 2.0) / scale) + up * ((h / 2.0 - py) / scale)
        return origin, forward / np.linalg.norm(forward)
    f = focal(h)
    dx = (px - w / 2.0) / f
    dy = (h / 2.0 - py) / f
    d = right * dx + up * dy + forward
    return eye, d / np.linalg.norm(d)


def dolly_to_ray(
    eye, forward, distance: float, ray_dir, factor: float, min_dist: float, anchor_t=None
):
    """Zoom towards (or away from) the point under the cursor.

    ``factor`` is the new pivot distance as a fraction of the old (``< 1`` zooms
    in). The eye slides along ``ray_dir`` so the world point under the cursor
    keeps its place on screen. ``anchor_t`` is the distance along the ray to the
    point to hold under the cursor; passing the depth of the surface actually under
    the cursor (from a ray cast) makes the zoom track that surface rather than a
    point floating at the pivot plane, which is what sells zoom-to-cursor. Without
    it the pivot plane is used. The pivot distance is floored at ``min_dist``: once
    there, zooming in keeps advancing the eye, so the pivot is pushed forward along
    the view direction and the camera passes through rather than stalling.
    Returns ``(new_target, new_distance)``."""
    cosang = float(ray_dir @ forward)
    if cosang <= 1e-4:  # cursor ray almost perpendicular to the view: dolly straight ahead
        ray_dir, cosang = forward, 1.0
    t = anchor_t if anchor_t is not None else distance / cosang
    new_eye = eye + ray_dir * (t * (1.0 - factor))
    new_distance = max(distance * factor, min_dist)
    return new_eye + forward * new_distance, new_distance


def dolly_ortho(cam: Camera, px: float, py: float, w: int, h: int, factor: float, min_half: float):
    """Zoom the orthographic view towards the cursor: scale ``ortho_half_h`` by ``factor`` and
    shift the pivot so the world point under the cursor stays under it. Returns the new
    ``(target, ortho_half_h)``."""
    _eye, right, up, _forward = cam.basis()
    scale = cam.ortho_scale(h)
    wx = (px - w / 2.0) / scale
    wy = (h / 2.0 - py) / scale
    new_half = max(cam.ortho_half_h * factor, min_half)
    # the shift keeps the cursor's world point fixed as the scale changes
    shift = (1.0 - new_half / cam.ortho_half_h) if cam.ortho_half_h else 0.0
    target = cam.target + right * (wx * shift) + up * (wy * shift)
    return target, new_half


def pan_delta(right, up, distance: float, focal_px: float, dx: float, dy: float):
    """World-space pivot shift for a drag of ``(dx, dy)`` pixels. Scaled by the
    pivot depth and the focal length, so the same drag moves the pivot the same
    apparent amount at any distance."""
    scale = distance / max(focal_px, 1.0)
    return -right * (dx * scale) + up * (dy * scale)


def frame_distance(radius: float, fov_deg: float, aspect: float, fill: float = 0.85) -> float:
    """Pivot distance to frame a sphere of ``radius``. ``fill`` scales the distance: below 1 the
    sphere more than fills the view (the old default, which crops the far corners), at or above 1
    the whole sphere sits inside with that much margin. Framing the whole map passes ``fill`` a
    touch above 1 so every corner stays in view."""
    half = math.radians(fov_deg) / 2.0
    if aspect < 1.0:  # a tall, narrow viewport is limited by its width
        half = math.atan(math.tan(half) * aspect)
    return max(radius, 1.0) / math.sin(half) * fill


def ortho_half_for(radius: float, aspect: float) -> float:
    """World half-height for the parallel projection to fit a sphere of ``radius``, allowing
    for a viewport narrower than it is tall (the width then sets the limit) and a small
    margin so the geometry does not touch the edges."""
    half = max(radius, 1.0)
    if aspect < 1.0:  # a tall, narrow viewport fits less across than down
        half = half / max(aspect, 1e-6)
    return half * 1.1


def look_angles(eye, target):
    """``(yaw, pitch, distance)`` for an orbit camera at ``eye`` looking at ``target``."""
    v = np.asarray(target, np.float64) - np.asarray(eye, np.float64)
    distance = float(np.linalg.norm(v))
    if distance < 1e-6:
        return 0.0, 0.0, 1.0
    f = v / distance
    pitch = math.asin(max(-1.0, min(1.0, -float(f[2]))))
    yaw = math.atan2(-float(f[1]), -float(f[0]))
    return yaw, pitch, distance


def ray_mesh_hit(origin, direction, points, triangles):
    """Nearest point where a ray meets the mesh, or ``None``. Vectorised
    Moeller-Trumbore over every triangle at once."""
    if triangles is None or not len(triangles):
        return None
    p = np.asarray(points, np.float64)
    tri = np.asarray(triangles, np.int64).reshape(-1, 3)
    v0, v1, v2 = p[tri[:, 0]], p[tri[:, 1]], p[tri[:, 2]]
    e1, e2 = v1 - v0, v2 - v0
    o = np.asarray(origin, np.float64)
    d = np.asarray(direction, np.float64)
    pv = np.cross(d, e2)
    det = np.einsum("ij,ij->i", e1, pv)
    eps = 1e-7
    ok = np.abs(det) > eps
    inv = np.where(ok, 1.0 / np.where(ok, det, 1.0), 0.0)
    tv = o - v0
    u = inv * np.einsum("ij,ij->i", tv, pv)
    qv = np.cross(tv, e1)
    v = inv * (qv @ d)
    t = inv * np.einsum("ij,ij->i", e2, qv)
    hit = ok & (u >= -eps) & (v >= -eps) & (u + v <= 1.0 + eps) & (t > eps)
    if not hit.any():
        return None
    nearest = int(np.argmin(np.where(hit, t, np.inf)))
    return o + d * float(t[nearest])


def ray_aabb_distance(origin, direction, mins, maxs):
    """Distance along the ray (unit ``direction``) to where it first enters the world-space
    box ``(mins, maxs)``, or ``None`` when it misses. Zero when the eye is inside the box.
    The slab method; used to pick a static-model prop by its world bounds, the same bounds
    the renderer places and draws each instance within."""
    o = np.asarray(origin, np.float64)
    d = np.asarray(direction, np.float64)
    lo = np.asarray(mins, np.float64)
    hi = np.asarray(maxs, np.float64)
    tmin, tmax = 0.0, np.inf
    for k in range(3):
        if abs(d[k]) < 1e-12:  # ray parallel to this slab: must start between its planes
            if o[k] < lo[k] or o[k] > hi[k]:
                return None
            continue
        inv = 1.0 / d[k]
        t1 = (lo[k] - o[k]) * inv
        t2 = (hi[k] - o[k]) * inv
        if t1 > t2:
            t1, t2 = t2, t1
        tmin = max(tmin, t1)
        tmax = min(tmax, t2)
        if tmin > tmax:
            return None
    return float(tmin)


# -- in-place editing (v0.3.0) ---------------------------------------------------------------

#: World axes a translate or rotate drag is constrained to.
EDIT_AXES = (
    np.array([1.0, 0.0, 0.0]),
    np.array([0.0, 1.0, 0.0]),
    np.array([0.0, 0.0, 1.0]),
)
#: Marker colour per kind; falls back to the theme text colour.
MARKER_KINDS = {
    "spawn": "#49b36b",
    "objective": "#d8973c",
    "light": "#e8d24a",
    "worldspawn": "#6f8bd8",
    "entity": "#9aa0a6",
}


class MapEditController:
    """Drives one ``EditSession`` from the 3D view: selection by marker, a translate or
    rotate drag constrained to a world axis, and add / delete / duplicate. The maths are the
    pure functions in ``opent5.edit.gizmo``; this holds the per-drag state and the selection,
    so it can be exercised without a GPU."""

    #: pick filter values: everything, static-model props only, or placed entities only.
    PICK_ALL = "all"
    PICK_PROPS = "props"
    PICK_ENTITIES = "entities"

    def __init__(self, session):
        self.session = session
        self.selected_id: int | None = None
        #: index of the selected static-model prop (clipMap staticModelList), or None.
        self.selected_prop: int | None = None
        #: index of the selected clip brush in the collision view (an invisible wall), or None.
        self.selected_clip: int | None = None
        #: whether the collision overlay (clip brushes) is shown, pickable and deletable, and
        #: which contents families it covers. Set by the view's Show collision toggle/filter.
        self.show_collision = False
        self.collision_families: tuple = ("clip",)
        #: bumped on every clip-brush add/remove so the view rebuilds the collision overlay.
        self._collision_rev = 0
        #: the right-click menu's move scope: which bundle pieces a prop move drag acts on.
        self.move_model = True
        self.move_collision = True
        #: what a click selects, and which markers the view draws: PICK_ALL / PICK_PROPS /
        #: PICK_ENTITIES. Defaults to everything; the view switches it to PICK_PROPS when Edit
        #: turns on, so the map is not buried under its hundreds of entity dots and the props
        #: are reachable.
        self.pick_filter = self.PICK_ALL
        #: whether the static models are drawn; props pick only when they are (you cannot
        #: click a prop that is not on screen). Set by the view from its Static models toggle.
        self.props_shown = True
        self.mode = "translate"  # or "rotate"
        self.grid = 0.0  # translate snap, world units (0: off)
        self.angle = 0.0  # rotate snap, degrees (0: off)
        self._drag = None
        self._drag_token = 0
        #: a prop drag in progress: its axis index, mode, centre and the drag's start param.
        self._prop_drag: dict | None = None
        #: live preview of a prop drag, applied only to the drawn highlight until release:
        #: ``("translate", np.ndarray delta)`` or ``("rotate", float degrees)``, or None.
        self._prop_preview: tuple | None = None
        #: non-blocking notices from the last clip edit (baked shadow, collision stays).
        self.warnings: list = []
        self._props_cache: list | None = None
        #: bumped on every prop structural edit (add / move / rotate / delete) and on undo /
        #: redo, so the view knows to rebuild the placed-model mesh for a live refresh.
        self._prop_rev = 0
        #: bumped on any edit that could move a marker or prop, so the per-paint projection
        #: cache below is dropped. The view repaints on every mouse move for the cursor
        #: readout, so projecting every prop and entity each time was the overlay's main cost;
        #: the cache reuses the last projection while the camera and the scene are unchanged.
        self._scene_epoch = 0
        self._markers_cache: list | None = None
        self._proj_cache: dict = {}

    # static-model props and their clip collision
    def props(self) -> list:
        """The zone's static-model props (clipMap staticModelList); cached per session."""
        if self._props_cache is None:
            self._props_cache = self.session.static_models() if self.can_edit_clips else []
        return self._props_cache

    @property
    def can_edit_clips(self) -> bool:
        return getattr(self.session, "can_edit_clips", False)

    def selected_prop_obj(self):
        if self.selected_prop is None:
            return None
        for p in self.props():
            if p.index == self.selected_prop:
                return p
        return None

    def selected_cluster(self) -> list:
        """The clip brush indices of the selected prop's cluster (under its footprint),
        for the editor to highlight before a move or delete. Empty when nothing is
        selected, the prop has no clip, or the zone has no clipMap."""
        prop = self.selected_prop_obj()
        if prop is None:
            return []
        return self.session.clip_cluster_in_footprint(*prop.footprint)

    def selected_cluster_boxes(self) -> list:
        """(mins, maxs) of each brush in the selected prop's cluster, for drawing the
        highlight."""
        return self.session.cluster_boxes(self.selected_cluster())

    def move_selected_prop(self, delta) -> dict:
        """Move the selected prop's clip cluster by ``delta`` (the whole cluster, so the
        old spot clears). Records warnings; returns the result dict."""
        prop = self.selected_prop_obj()
        if prop is None:
            self.warnings = []
            return {"moved": [], "found": False, "warnings": []}
        result = self.session.move_prop_clip(prop.index, delta, footprint=prop.footprint)
        self.warnings = result["warnings"]
        self._props_cache = None
        if result.get("found") or result.get("render_moved"):
            self._prop_rev += 1
        return result

    def rotate_selected_prop(self, degrees: float) -> dict:
        """Rotate the selected prop about Z by ``degrees`` (render and clip in step).
        Records warnings; returns the result dict."""
        prop = self.selected_prop_obj()
        if prop is None:
            self.warnings = []
            return {"rotated": [], "found": False, "warnings": []}
        result = self.session.rotate_prop_clip(prop.index, degrees, footprint=prop.footprint)
        self.warnings = result["warnings"]
        self._props_cache = None
        if result.get("found"):
            self._prop_rev += 1
        return result

    def add_prop(self, model: str, origin, half_extent) -> dict:
        """Place a solid crate prop centred at ``origin`` with the given half-extents (render
        static model + clip). Selects the new prop. Returns the session result dict."""
        origin = np.asarray(origin, np.float64)
        half = np.asarray(half_extent, np.float64)
        result = self.session.add_prop(model, origin - half, origin + half)
        self.warnings = result["warnings"]
        self._props_cache = None
        self.selected_prop = result["prop"]
        self.selected_id = None
        self._prop_rev += 1
        return result

    def prop_models(self) -> list[str]:
        """Distinct model names of the zone's static-model props, for the add palette."""
        seen: list[str] = []
        for p in self.props():
            if p.model and p.model not in seen:
                seen.append(p.model)
        return seen

    def placeable_models(self) -> list[str]:
        """Distinct prop models the zone can place a drawing copy of: those with an existing
        GfxWorld draw instance to clone. The Add picker offers these, so a chosen model draws."""
        seen: list[str] = []
        for p in self.props():
            if p.model and p.model not in seen and self.session.has_draw_inst(p.model):
                seen.append(p.model)
        return seen

    def default_crate_model(self) -> str | None:
        """A crate-like model the zone can place, for the quick add (B): the first placeable
        model whose name reads like a box/crate, else the first placeable model of all."""
        models = self.placeable_models() or self.prop_models()
        for m in models:
            low = m.lower()
            if any(k in low for k in ("crate", "cardboardbox", "cargo", "container", "_box")):
                return m
        return models[0] if models else None

    def delete_selected_prop_clip(self) -> dict:
        prop = self.selected_prop_obj()
        if prop is None:
            self.warnings = []
            return {"removed": [], "found": False, "warnings": []}
        result = self.session.remove_prop_clip(prop.index, footprint=prop.footprint)
        self.warnings = result["warnings"]
        self._props_cache = None
        if result.get("found") or result.get("render_hidden"):
            self._prop_rev += 1
        if result.get("found"):
            self._collision_rev += 1
        return result

    # -- bundle pieces (the right-click menu drives these) -----------------------------------
    def move_selected_model_only(self, delta) -> dict:
        """Move only the selected prop's render (the model), not its collision."""
        prop = self.selected_prop_obj()
        if prop is None:
            self.warnings = []
            return {"render_moved": False, "warnings": []}
        result = self.session.move_prop_render(prop.index, delta)
        self.warnings = result.get("warnings", [])
        self._props_cache = None
        if result.get("render_moved"):
            self._prop_rev += 1
        return result

    def move_selected_collision_only(self, delta) -> dict:
        """Move only the selected prop's clip cluster (the collision), not the model."""
        prop = self.selected_prop_obj()
        if prop is None:
            self.warnings = []
            return {"moved": [], "found": False, "warnings": []}
        result = self.session.move_prop_cluster(prop.index, delta, footprint=prop.footprint)
        self.warnings = result.get("warnings", [])
        self._props_cache = None
        if result.get("found"):
            self._prop_rev += 1
            self._collision_rev += 1
        return result

    def delete_selected_model_only(self) -> dict:
        prop = self.selected_prop_obj()
        if prop is None:
            self.warnings = []
            return {"render_hidden": False, "warnings": []}
        result = self.session.remove_prop_render(prop.index)
        self.warnings = result.get("warnings", [])
        self._props_cache = None
        if result.get("render_hidden"):
            self._prop_rev += 1
        return result

    def delete_selected_collision_only(self) -> dict:
        prop = self.selected_prop_obj()
        if prop is None:
            self.warnings = []
            return {"removed": [], "warnings": []}
        cluster = self.session.clip_cluster_in_footprint(*prop.footprint)
        result = self.session.remove_clips(cluster, key=prop.index)
        self.warnings = result.get("warnings", [])
        if result.get("removed"):
            self._collision_rev += 1
        return result

    def footprint_pieces(self) -> dict:
        """The individual pieces under the selected prop's footprint, for the context menu: the
        clip brush indices and any other static-model props whose footprint overlaps."""
        prop = self.selected_prop_obj()
        if prop is None:
            return {"clips": [], "props": []}
        from opent5.convert.propclip import boxes_overlap

        clips = self.session.clip_cluster_in_footprint(*prop.footprint)
        props = [
            p.index
            for p in self.props()
            if p.index != prop.index and boxes_overlap(prop.absmin, prop.absmax, p.absmin, p.absmax)
        ]
        return {"clips": clips, "props": props}

    # -- the collision overlay (clip brushes) ------------------------------------------------
    def collision_boxes(self) -> list:
        """(index, mins, maxs, family) of the clip brushes the collision view shows, for
        picking and highlighting. Empty when the overlay is off or the zone has no clipMap."""
        if not self.show_collision or not self.can_edit_clips:
            return []
        return [
            (i, mn, mx, fam)
            for (i, mn, mx, _c, fam) in self.session.collision_brushes(self.collision_families)
        ]

    def pick_clip_ray(self, cam: Camera, px: float, py: float, w: int, h: int):
        """Nearest clip brush the cursor ray enters, as ``(brush_index, distance)``, by its
        axis-aligned bounds (the same box the overlay draws it as)."""
        eye, d = cursor_ray(cam, px, py, w, h)
        best_i, best_t = None, np.inf
        for i, mn, mx, _fam in self.collision_boxes():
            t = ray_aabb_distance(eye, d, mn, mx)
            if t is not None and t < best_t:
                best_i, best_t = i, t
        return best_i, best_t

    def selected_clip_box(self):
        """(mins, maxs) of the selected clip brush, or None."""
        if self.selected_clip is None:
            return None
        for i, mn, mx, _fam in self.collision_boxes():
            if i == self.selected_clip:
                return (mn, mx)
        return None

    def delete_selected_clip(self) -> dict:
        """Remove the selected clip brush (an invisible wall) with ``disable_clip``."""
        if self.selected_clip is None:
            self.warnings = []
            return {"removed": [], "warnings": []}
        result = self.session.remove_clips([self.selected_clip])
        self.warnings = result.get("warnings", [])
        if result.get("removed"):
            self._collision_rev += 1
        self.selected_clip = None
        return result

    def delete_clip_brushes(self, indices) -> dict:
        result = self.session.remove_clips(list(indices))
        self.warnings = result.get("warnings", [])
        if result.get("removed"):
            self._collision_rev += 1
        return result

    # selection and projection
    def invalidate_projection(self) -> None:
        """Drop the cached marker/prop projections (an edit moved something, or the markers
        list changed). The next paint reprojects."""
        self._scene_epoch += 1
        self._markers_cache = None
        self._proj_cache.clear()

    def markers(self) -> list:
        if self._markers_cache is None:
            self._markers_cache = self.session.markers()
        return self._markers_cache

    @staticmethod
    def _cam_sig(cam: Camera, w: int, h: int) -> tuple:
        return (round(cam.yaw, 6), round(cam.pitch, 6), round(cam.distance, 4),
                float(cam.target[0]), float(cam.target[1]), float(cam.target[2]),
                cam.ortho, round(cam.ortho_half_h, 4), w, h)  # fmt: skip

    def _project_all(self, origins: np.ndarray, cam: Camera, w: int, h: int):
        """Project an (n, 3) array of world points to the screen in one vectorised pass:
        ``((n, 2) positions with NaN behind the camera, (n,) depths)``. One matrix multiply for
        every marker rather than a Python call each, which is what made the overlay slow."""
        eye, right, up, forward = cam.basis()
        if not len(origins):
            return np.zeros((0, 2), np.float64), np.zeros(0, np.float64)
        rel = np.asarray(origins, np.float64) - eye
        x = rel @ right
        y = rel @ up
        z = rel @ forward
        front = z > 1e-9
        if cam.ortho:
            oscale = cam.ortho_scale(h)
            sx = w / 2.0 + oscale * x
            sy = h / 2.0 - oscale * y
        else:
            f = focal(h)
            zz = np.where(front, z, 1.0)
            sx = w / 2.0 + f * x / zz
            sy = h / 2.0 - f * y / zz
        pts = np.stack([np.where(front, sx, np.nan), np.where(front, sy, np.nan)], 1)
        depths = np.where(front, z, np.inf)
        return pts, depths

    def marker_screen(self, cam: Camera, w: int, h: int):
        cached = self._proj_cache.get("markers")
        sig = (self._cam_sig(cam, w, h), self._scene_epoch)
        if cached is not None and cached[0] == sig:
            return cached[1]
        markers = self.markers()
        ids = [o.id for o in markers]
        origins = np.array([o.origin for o in markers], np.float64).reshape(-1, 3)
        pts, depths = self._project_all(origins, cam, w, h)
        result = (ids, pts, depths)
        self._proj_cache["markers"] = (sig, result)
        return result

    def prop_screen(self, cam: Camera, w: int, h: int):
        """Static-model prop origins projected to the screen, for picking and markers:
        ``(indices, (n, 2) positions, depths)``."""
        cached = self._proj_cache.get("props")
        sig = (self._cam_sig(cam, w, h), self._prop_rev)
        if cached is not None and cached[0] == sig:
            return cached[1]
        props = self.props()
        idxs = [p.index for p in props]
        origins = np.array([p.origin for p in props], np.float64).reshape(-1, 3)
        pts, depths = self._project_all(origins, cam, w, h)
        result = (idxs, pts, depths)
        self._proj_cache["props"] = (sig, result)
        return result

    @property
    def props_pickable(self) -> bool:
        """Props can be picked only when the filter allows them, they are drawn, and the
        zone carries an editable clipMap with static models."""
        return (
            self.pick_filter in (self.PICK_ALL, self.PICK_PROPS)
            and self.props_shown
            and self.can_edit_clips
        )

    @property
    def entities_pickable(self) -> bool:
        return self.pick_filter in (self.PICK_ALL, self.PICK_ENTITIES)

    def pick_prop_ray(self, cam: Camera, px: float, py: float, w: int, h: int):
        """Nearest static-model prop the cursor ray enters, as ``(prop_index, distance)``, or
        ``(None, inf)``. The ray is tested against each prop's world-space bounds (the
        clipMap cStaticModel absmin/absmax, the same bounds the renderer places and draws the
        instance within), so a click lands on the model's body, not on a screen point at its
        origin. Bounds level only: the per-instance triangles are not cleanly available in
        this transform here, so a prop is a box to the picker."""
        eye, d = cursor_ray(cam, px, py, w, h)
        best_i, best_t = None, np.inf
        for p in self.props():
            t = ray_aabb_distance(eye, d, p.absmin, p.absmax)
            if t is not None and t < best_t:
                best_i, best_t = p.index, t
        return best_i, best_t

    def pick(self, cam: Camera, px: float, py: float, w: int, h: int, radius: float = 12.0):
        """Select what the cursor is over, honouring the pick filter. A prop is hit by ray
        against its world bounds; an entity marker by screen proximity. When both are under
        the cursor the one nearer the camera wins (compared by world-space distance from the
        eye, not screen distance). Picking a prop sets ``selected_prop`` and clears the entity
        selection; picking an entity does the reverse."""
        from opent5.edit import gizmo as gz

        self.warnings = []
        eye, _d = cursor_ray(cam, px, py, w, h)
        prop_i, prop_t = (None, np.inf)
        if self.props_pickable:
            prop_i, prop_t = self.pick_prop_ray(cam, px, py, w, h)
        ent_id, ent_dist = None, np.inf
        if self.entities_pickable:
            ids, pts, depths = self.marker_screen(cam, w, h)
            ent_i = gz.nearest_marker(pts, (px, py), radius, depths) if ids else None
            if ent_i is not None:
                ent_id = ids[ent_i]
                origin = np.asarray(self._object_origin(ent_id), np.float64)
                ent_dist = float(np.linalg.norm(origin - eye))
        clip_i, clip_t = (None, np.inf)
        if self.show_collision and self.can_edit_clips:
            clip_i, clip_t = self.pick_clip_ray(cam, px, py, w, h)
        # Nearest hit wins. On a tie, a prop beats a clip brush beats an entity (so clicking a
        # prop selects the prop, not the clip wall it sits inside), matching the old prop/entity
        # order; the collision overlay off means no clip ever wins.
        best = None
        for kind, val, t in (
            ("prop", prop_i, prop_t),
            ("clip", clip_i, clip_t),
            ("entity", ent_id, ent_dist),
        ):
            if val is None:
                continue
            if best is None or t < best[2]:
                best = (kind, val, t)
        self.selected_prop = best[1] if best and best[0] == "prop" else None
        self.selected_clip = best[1] if best and best[0] == "clip" else None
        self.selected_id = best[1] if best and best[0] == "entity" else None
        return self.selected_id

    def _object_origin(self, obj_id):
        for o in self.markers():
            if o.id == obj_id:
                return o.origin
        return (0.0, 0.0, 0.0)

    def selected(self):
        if self.selected_id is None:
            return None
        from opent5.edit import EditError

        try:
            return self.session.object(self.selected_id)
        except EditError:
            self.selected_id = None
            return None

    def gizmo_centre(self):
        """World centre the gizmo sits on: the selected prop's origin (shifted by any live
        translate preview) or the selected entity's origin, else None."""
        prop = self.selected_prop_obj()
        if prop is not None:
            centre = np.asarray(prop.origin, np.float64)
            if self._prop_preview is not None and self._prop_preview[0] == "translate":
                centre = centre + self._prop_preview[1]
            return centre
        obj = self.selected()
        return None if obj is None else np.asarray(obj.origin, np.float64)

    def axis_handles(self, cam: Camera, w: int, h: int, length_px: float = 60.0):
        """The selected object's gizmo: ``(centre_screen, [(axis_index, (sx, sy), front)])``,
        the three world-axis handle ends sized to about ``length_px`` on screen. Empty when
        nothing is selected or it is behind the camera. Works for a selected entity or a
        selected static-model prop."""
        from opent5.edit import gizmo as gz

        centre = self.gizmo_centre()
        if centre is None:
            return None, []
        eye, right, up, forward = cam.basis()
        f = focal(h)
        oscale = cam.ortho_scale(h) if cam.ortho else None
        csx, csy, cdepth, cfront = gz.project_point(
            centre, eye, right, up, forward, f, w, h, oscale
        )
        if not cfront:
            return None, []
        # the handle keeps about ``length_px`` on screen: the scale is constant in ortho, so the
        # world length is fixed; in perspective it grows with depth.
        world_len = (length_px / oscale) if oscale is not None else max(cdepth, 1.0) * length_px / f
        handles = []
        for i, axis in enumerate(EDIT_AXES):
            sx, sy, _d, front = gz.project_point(
                centre + axis * world_len, eye, right, up, forward, f, w, h, oscale
            )
            handles.append((i, (sx, sy), front))
        return (csx, csy), handles

    def hit_axis(self, cam: Camera, px: float, py: float, w: int, h: int, radius: float = 10.0):
        """The axis whose handle end the cursor is within ``radius`` pixels of, or None."""
        _centre, handles = self.axis_handles(cam, w, h)
        best, best_d = None, radius * radius
        for i, (sx, sy), front in handles:
            if not front:
                continue
            d = (sx - px) ** 2 + (sy - py) ** 2
            if d <= best_d:
                best, best_d = i, d
        return best

    # dragging
    def begin_drag(self, axis_index: int, cam: Camera, px: float, py: float, w: int, h: int):
        from opent5.edit import gizmo as gz

        eye, d = cursor_ray(cam, px, py, w, h)
        axis = EDIT_AXES[axis_index]
        if self.selected_prop is not None:
            return self._begin_prop_drag(axis_index, axis, eye, d)
        obj = self.selected()
        if obj is None:
            return False
        centre = np.asarray(obj.origin, np.float64)
        self._drag_token += 1
        if self.mode == "rotate":
            self._drag = {
                "axis": axis_index,
                "centre": centre,
                "hit0": gz.ray_plane(eye, d, centre, axis),
                "angles": np.asarray(obj.angles, np.float64),
            }
        else:
            self._drag = {
                "axis": axis_index,
                "start": centre,
                "s0": gz.axis_param(eye, d, centre, axis),
            }
        return True

    def _begin_prop_drag(self, axis_index, axis, eye, d) -> bool:
        from opent5.edit import gizmo as gz

        prop = self.selected_prop_obj()
        if prop is None:
            return False
        centre = np.asarray(prop.origin, np.float64)
        self._prop_preview = None
        if self.mode == "rotate":
            self._prop_drag = {
                "mode": "rotate",
                "axis": axis_index,
                "centre": centre,
                "hit0": gz.ray_plane(eye, d, centre, axis),
            }
        else:
            self._prop_drag = {
                "mode": "translate",
                "axis": axis_index,
                "start": centre,
                "s0": gz.axis_param(eye, d, centre, axis),
            }
        return True

    def update_drag(self, cam: Camera, px: float, py: float, w: int, h: int) -> None:
        from opent5.edit import gizmo as gz

        eye, d = cursor_ray(cam, px, py, w, h)
        if self._prop_drag is not None:
            self._update_prop_drag(gz, eye, d)
            return
        if self._drag is None:
            return
        obj = self.selected()
        if obj is None:
            return
        axis_index = self._drag["axis"]
        axis = EDIT_AXES[axis_index]
        if self.mode == "rotate":
            hit0 = self._drag["hit0"]
            hit1 = gz.ray_plane(eye, d, self._drag["centre"], axis)
            if hit0 is None or hit1 is None:
                return
            delta = math.degrees(gz.rotation_delta(self._drag["centre"], axis, hit0, hit1))
            delta = gz.snap(delta, self.angle) if self.angle else delta
            angles = gz.rotate_angles(self._drag["angles"], axis_index, delta)
            self.session.rotate_object(obj.id, angles, coalesce=True, group=self._drag_token)
        else:
            s1 = gz.axis_param(eye, d, self._drag["start"], axis)
            origin = gz.translate_on_axis(self._drag["start"], axis, self._drag["s0"], s1)
            origin = gz.snap_vec(origin, self.grid) if self.grid else origin
            self.session.move_object(obj.id, origin, coalesce=True, group=self._drag_token)
        self.invalidate_projection()  # the dragged entity moved; reproject the markers

    def _update_prop_drag(self, gz, eye, d) -> None:
        """A prop drag updates only the live preview; the heavy clip edit lands on release."""
        drag = self._prop_drag
        axis = EDIT_AXES[drag["axis"]]
        if drag["mode"] == "rotate":
            hit1 = gz.ray_plane(eye, d, drag["centre"], axis)
            if drag["hit0"] is None or hit1 is None:
                return
            deg = math.degrees(gz.rotation_delta(drag["centre"], axis, drag["hit0"], hit1))
            deg = gz.snap(deg, self.angle) if self.angle else deg
            self._prop_preview = ("rotate", float(deg))
        else:
            s1 = gz.axis_param(eye, d, drag["start"], axis)
            origin = gz.translate_on_axis(drag["start"], axis, drag["s0"], s1)
            origin = gz.snap_vec(origin, self.grid) if self.grid else origin
            self._prop_preview = ("translate", np.asarray(origin, np.float64) - drag["start"])

    def end_drag(self) -> None:
        """Finish a drag. An entity drag already applied live; a prop drag commits its
        previewed translate or rotate now, as one reversible edit."""
        if self._prop_drag is not None:
            preview, self._prop_preview = self._prop_preview, None
            self._prop_drag = None
            if preview is None:
                return
            if preview[0] == "rotate" and abs(preview[1]) > 1e-6:
                self.rotate_selected_prop(preview[1])
            elif preview[0] == "translate" and float(np.linalg.norm(preview[1])) > 1e-6:
                delta = tuple(float(v) for v in preview[1])
                # the right-click menu's move scope decides which bundle pieces follow the gizmo.
                if self.move_model and self.move_collision:
                    self.move_selected_prop(delta)
                elif self.move_model:
                    self.move_selected_model_only(delta)
                elif self.move_collision:
                    self.move_selected_collision_only(delta)
            return
        self._drag = None

    @property
    def dragging(self) -> bool:
        return self._drag is not None or self._prop_drag is not None

    @property
    def previewing(self) -> bool:
        """True while a prop drag is showing a preview but has not yet committed, so the
        canvas repaints without a per-move session edit."""
        return self._prop_drag is not None

    def preview_cluster_boxes(self) -> list:
        """The selected prop's cluster boxes, transformed by any live drag preview, for the
        highlight to follow the cursor before the edit commits."""
        boxes = self.selected_cluster_boxes()
        if self._prop_preview is None or not boxes:
            return boxes
        from opent5.edit import gizmo as gz

        kind, value = self._prop_preview
        if kind == "translate":
            d = value

            def shift(v):
                return (v[0] + d[0], v[1] + d[1], v[2] + d[2])

            return [(shift(mn), shift(mx)) for mn, mx in boxes]
        centre = [sum(mn[k] + mx[k] for mn, mx in boxes) / (2 * len(boxes)) for k in range(3)]
        return [gz.rotated_box_aabb_z(mn, mx, centre, value) for mn, mx in boxes]

    # structural edits
    def add(self, keys: dict) -> int:
        self.selected_id = self.session.add_object(keys)
        self.invalidate_projection()  # the markers list grew
        return self.selected_id

    def duplicate(self) -> int | None:
        if self.selected_id is None:
            return None
        self.selected_id = self.session.duplicate_object(self.selected_id)
        self.invalidate_projection()
        return self.selected_id

    def delete(self) -> None:
        if self.selected_id is not None:
            self.session.delete_object(self.selected_id)
            self.selected_id = None
            self.invalidate_projection()

    def set_property(self, key: str, value: str | None) -> None:
        if self.selected_id is not None:
            self.session.set_property(self.selected_id, key, value)
            self.invalidate_projection()  # a property edit may move the marker (origin)

    def undo(self) -> None:
        self.session.undo()
        self._props_cache = None
        self._prop_rev += 1  # an undo may restore a prop's placement; refresh the mesh
        self.invalidate_projection()
        if self.selected() is None:
            self.selected_id = None

    def redo(self) -> None:
        self.session.redo()
        self._props_cache = None
        self._prop_rev += 1
        self.invalidate_projection()


class Renderer:
    """Holds a mesh's edges and renders frames into QImages."""

    def __init__(self):
        self.points = np.zeros((0, 3), np.float32)
        self.edges = np.zeros((0, 2), np.int64)
        self.tris = np.zeros((0, 3), np.int64)  # kept for cursor picking
        self.drag_edges = self.edges
        self.grid_points = np.zeros((0, 3), np.float32)
        self.grid_edges = np.zeros((0, 2), np.int64)
        #: the collision overlay (clip-brush volumes), drawn translucent over the wireframe when
        #: ``show_collision`` is on. Separate from the world mesh, so toggling it or editing a
        #: clip never rebuilds the world+models mesh.
        self.collision_points = np.zeros((0, 3), np.float32)
        self.collision_edges = np.zeros((0, 2), np.int64)
        self.show_collision = False
        #: bumped whenever the overlay mesh changes, so the frame cache knows to repaint.
        self.collision_rev = 0
        self.depth_cue = True
        self.grid = True
        self.bounds = (np.zeros(3), np.zeros(3))
        # framing bounds and grid step exist before any mesh, so the cursor readout and HUD are
        # safe if the mouse moves over an empty view.
        self.frame_bounds = (np.zeros(3), np.zeros(3))
        self.focus_bounds = (np.zeros(3), np.zeros(3))
        self.grid_step = 16.0
        self.last_ms = 0.0
        self.last_edges = 0

    def set_mesh(self, positions: np.ndarray, triangles: np.ndarray) -> None:
        self.points = np.ascontiguousarray(positions, np.float32)
        self.tris = np.asarray(triangles, np.int64).reshape(-1, 3)
        self.edges = unique_edges(triangles)
        if len(self.edges) > DRAG_EDGES:
            rng = np.random.default_rng(5)
            self.drag_edges = self.edges[rng.choice(len(self.edges), DRAG_EDGES, replace=False)]
        else:
            self.drag_edges = self.edges
        used = self.points[np.unique(self.edges)] if len(self.edges) else self.points
        if len(used):
            self.bounds = (used.min(0).astype(np.float64), used.max(0).astype(np.float64))
            # framing ignores far-flung outliers (skybox pieces, stray verts)
            lo, hi = np.percentile(used, [2, 98], axis=0).astype(np.float64)
            self.frame_bounds = (lo, hi)
            # a map is a dense core inside a wide terrain skirt: the first view
            # frames the core (the middle 80% of vertices, widened by half)
            clo, chi = np.percentile(used, [10, 90], axis=0).astype(np.float64)
            centre, half = (clo + chi) / 2, (chi - clo) * 0.75
            if np.linalg.norm(2 * half) < 0.25 * np.linalg.norm(hi - lo):
                self.focus_bounds = (centre - half, centre + half)
            else:
                self.focus_bounds = self.frame_bounds
        else:
            self.bounds = self.frame_bounds = self.focus_bounds = (np.zeros(3), np.zeros(3))
        self._build_grid()

    def set_collision_mesh(self, positions: np.ndarray, triangles: np.ndarray) -> None:
        """Set the collision overlay geometry (clip-brush volumes). Its edges are derived once;
        projection and culling happen per frame in ``render`` through the same numpy pipeline as
        the world wireframe, so it stays cheap even with a thousand brushes."""
        self.collision_points = np.ascontiguousarray(positions, np.float32)
        has = triangles is not None and len(triangles)
        self.collision_edges = unique_edges(triangles) if has else np.zeros((0, 2), np.int64)
        self.collision_rev += 1

    def clear_collision_mesh(self) -> None:
        self.collision_points = np.zeros((0, 3), np.float32)
        self.collision_edges = np.zeros((0, 2), np.int64)
        self.collision_rev += 1

    def update_points(self, positions: np.ndarray) -> None:
        """Replace the vertex positions without touching the edge, triangle or grid topology.
        For a live prop move or rotate, the triangles and their wireframe edges are unchanged
        and only some vertices shift, so this skips the costly edge rebuild (``unique_edges``)
        and robust framing (percentiles) that ``set_mesh`` does. The full min/max bounds are
        refreshed cheaply for the near/far planes and fly speed; the trimmed framing bounds
        stay as they were, which a single moved prop leaves effectively unchanged anyway."""
        self.points = np.ascontiguousarray(positions, np.float32)
        if len(self.points):
            self.bounds = (self.points.min(0).astype(np.float64),
                           self.points.max(0).astype(np.float64))  # fmt: skip

    def _build_grid(self) -> None:
        lo, hi = self.frame_bounds
        size = hi - lo
        step = nice_step(float(max(size[0], size[1])))
        z = float(lo[2])
        x0, x1 = math.floor(lo[0] / step) * step, math.ceil(hi[0] / step) * step
        y0, y1 = math.floor(lo[1] / step) * step, math.ceil(hi[1] / step) * step
        pts, edges = [], []
        sub = 8  # split each line so the near-plane clip stays local

        def line(a, b):
            base = len(pts)
            for i in range(sub + 1):
                t = i / sub
                pts.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, z))
            edges.extend((base + i, base + i + 1) for i in range(sub))

        x = x0
        while x <= x1 + 1e-6 and len(pts) < 20000:
            line((x, y0), (x, y1))
            x += step
        y = y0
        while y <= y1 + 1e-6 and len(pts) < 20000:
            line((x0, y), (x1, y))
            y += step
        self.grid_points = np.array(pts, np.float32).reshape(-1, 3)
        self.grid_edges = np.array(edges, np.int64).reshape(-1, 2)
        self.grid_step = step

    def render(self, cam: Camera, w: int, h: int, fast: bool = False) -> QImage:
        started = time.perf_counter()
        t = theme.current()
        bg, wire, far, grid = _rgb(t.base), _rgb(t.wire), _rgb(t.wire_far), _rgb(t.grid)
        out = np.empty((h * w, 3), np.uint8)
        out[:] = bg.astype(np.uint8)
        grid_cov = None
        if self.grid and len(self.grid_edges):
            gx0, gy0, gx1, gy1, _d = project_edges(self.grid_points, self.grid_edges, cam, w, h)
            if len(gx0):
                g, _ = rasterise(gx0, gy0, gx1, gy1, np.ones(len(gx0), np.float32), w, h)
                hit = np.flatnonzero(g)
                grid_cov = (hit, np.minimum(g[hit], 1.0)[:, None])
                out[hit] = (bg + (grid - bg) * grid_cov[1]).astype(np.uint8)
        edges = self.drag_edges if fast else self.edges
        count = 0
        if len(edges):
            x0, y0, x1, y1, depth = project_edges(self.points, edges, cam, w, h)
            count = len(x0)
            if count:
                if self.depth_cue:
                    lo, hi = np.percentile(depth, 2), np.percentile(depth, 98)
                    near = 1.0 - np.clip((depth - lo) / max(hi - lo, 1e-3), 0, 1)
                    near = near.astype(np.float32)
                else:
                    near = np.ones(count, np.float32)
                ones = np.ones(count, np.float32)
                acc, accn = rasterise(x0, y0, x1, y1, ones, w, h, extra=near)
                hit = np.flatnonzero(acc)
                a = acc[hit]
                mix = (accn[hit] / a)[:, None]
                colour = far + (wire - far) * mix
                gain = 0.55 if fast else 0.8
                cov = (1.0 - np.exp(-gain * a))[:, None]
                under = out[hit].astype(np.float32)
                out[hit] = np.clip(under + (colour - under) * cov, 0, 255).astype(np.uint8)
        if self.show_collision and len(self.collision_edges):
            cx0, cy0, cx1, cy1, _cd = project_edges(
                self.collision_points, self.collision_edges, cam, w, h
            )
            if len(cx0):
                cc, _ = rasterise(cx0, cy0, cx1, cy1, np.ones(len(cx0), np.float32), w, h)
                chit = np.flatnonzero(cc)
                if len(chit):
                    clip_rgb = _rgb(CLIP_OVERLAY_COLOUR)
                    # additive translucency: a face stack reads brighter, so the clip volumes
                    # look like translucent solids over the wireframe rather than flat lines.
                    ccov = (1.0 - np.exp(-0.5 * cc[chit]))[:, None]
                    cunder = out[chit].astype(np.float32)
                    out[chit] = np.clip(cunder + (clip_rgb - cunder) * ccov, 0, 255).astype(
                        np.uint8
                    )
        img8 = out.reshape(h, w, 3)
        image = QImage(img8.data, w, h, 3 * w, QImage.Format.Format_RGB888).copy()
        self.last_ms = (time.perf_counter() - started) * 1000.0
        self.last_edges = count
        return image


# -- loading in the background ---------------------------------------------------------------


class _Signals(QObject):
    done = Signal(int, object)
    failed = Signal(int, str)


class _MeshJob(QRunnable):
    def __init__(self, generation: int, doc: ZoneDoc, kind: str, ref: Ref | None):
        super().__init__()
        self.generation, self.doc, self.kind, self.ref = generation, doc, kind, ref
        self.signals = _Signals()

    def run(self) -> None:
        try:
            mesh = self.doc.mesh(self.kind, self.ref)
        except (EditError, ValueError, KeyError, IndexError, TypeError) as exc:
            self.signals.failed.emit(self.generation, str(exc))
            return
        self.signals.done.emit(self.generation, mesh)


class _SceneJob(QRunnable):
    """Decodes the mesh's colour maps and prepares a shaded scene, off the GUI thread.
    The GL upload and drawing stay on the GUI thread."""

    def __init__(self, generation: int, doc: ZoneDoc, mesh: MeshData):
        super().__init__()
        self.generation, self.doc, self.mesh = generation, doc, mesh
        self.signals = _Signals()

    def run(self) -> None:
        try:
            from opent5.gui import geometry, glrender

            tri_tex, textures = geometry.textures(self.doc, self.mesh)
            scene = glrender.build_scene(
                self.mesh.positions, self.mesh.triangles, self.mesh.normals,
                self.mesh.uvs, tri_tex, textures,
            )  # fmt: skip
        except (EditError, ValueError, KeyError, IndexError, TypeError) as exc:
            self.signals.failed.emit(self.generation, str(exc))
            return
        self.signals.done.emit(self.generation, (scene, len(textures)))


# -- widgets ---------------------------------------------------------------------------------


class MeshCanvas(QWidget):
    """The drawing surface: owns the camera and the renderer."""

    camera_changed = Signal()
    #: Fly mode turned on or off (so the view bar's Fly button can follow it).
    fly_changed = Signal(bool)
    #: An entity was picked (its id, or None); the view updates the property panel.
    selection_changed = Signal(object)
    #: An edit went through the session (move, rotate, add, delete, ...).
    edited = Signal()
    #: A short message for the status bar (e.g. why an add could not be placed).
    status = Signal(str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        # the right-click context menu is built by hand in mouseReleaseEvent (a right DRAG is
        # fly-look), so suppress Qt's automatic one.
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.PreventContextMenu)
        self.controller = None  # a MapEditController while editing, else None
        #: True when this zone can be edited, so the hint prompts to turn on Edit even
        #: before the controller exists.
        self.edit_available = False
        self._edit_drag = False
        self._press_pos = None
        self.setMinimumSize(120, 90)
        self.renderer = Renderer()
        self.camera = Camera()
        self.counts = (0, 0, 0)
        self.title = ""
        self.shaded = False
        self._gl = None  # a glrender.ShadedRenderer, made on first shaded frame
        self._gl_failed = False
        self._scene = None  # a glrender.Scene awaiting upload, or already uploaded
        self._scene_uploaded = False
        self._tex_count = 0
        self._image: QImage | None = None
        self._key = None
        self._drag = None
        self._dragging = False
        self.show_help = True
        #: look/zoom speed multiplier; 1.0 is the tuned default. The orbit and zoom respond to
        #: it, so a user who wants it calmer or quicker has one dial.
        self.sensitivity = 1.0
        self._focus = None  # last point picked by a double click, or None
        #: world point under the cursor (on the ground plane), for the on-screen readout, and
        #: the last whole-unit coordinate shown, so an idle move repaints only when it changes.
        self._cursor_world: np.ndarray | None = None
        self._cursor_shown: tuple | None = None
        self.setMouseTracking(True)  # so the readout tracks the cursor without a button held
        self._settle = QTimer(self)
        self._settle.setSingleShot(True)
        self._settle.setInterval(120)
        self._settle.timeout.connect(self._end_drag)
        # Fly mode. It is active (``_flying``) either while the right button is held (a quick
        # look-and-go) or while toggled on (``_fly_toggle``, via Space or the Fly button), so
        # WASD works hands-free. A fly button toggle notifies the view bar.
        self._flying = False
        self._fly_toggle = False
        self._fly_hold = False
        self.fly_speed = 512.0
        self._fly_keys: set = set()
        self._fly_last = 0.0
        self._fly_timer = QTimer(self)
        self._fly_timer.setInterval(16)
        self._fly_timer.timeout.connect(self._fly_step)

    def set_controller(self, controller) -> None:
        """Attach (or clear with None) the in-place edit controller. While set, the canvas
        draws entity markers and the selection gizmo and routes left-button events to it."""
        self.controller = controller
        self._edit_drag = False
        self.invalidate()

    def set_mesh(self, positions, triangles, title: str) -> None:
        self.renderer.set_mesh(positions, triangles)
        self.counts = (len(positions), len(triangles), len(self.renderer.edges))
        self.fly_speed = max(self._scene_diag() * 0.4, 16.0)
        self.title = title
        self._scene = None  # the shaded scene is rebuilt for the new mesh
        self._scene_uploaded = False
        self._tex_count = 0
        self.invalidate()

    def update_mesh_positions(self, mesh) -> None:
        """Refresh only the vertex positions of the current mesh (a live prop move or rotate,
        where the triangles are unchanged). Keeps the wireframe edges, so the view updates
        without the full ``set_mesh`` rebuild. A shaded scene, which carries its own vertex
        buffer, is rebuilt separately by the view."""
        self.renderer.update_points(mesh.positions)
        self.counts = (len(mesh.positions), len(mesh.triangles), len(self.renderer.edges))
        self.invalidate()

    def set_shaded(self, on: bool) -> None:
        self.shaded = on
        self.invalidate()

    def set_show_collision(self, on: bool) -> None:
        """Turn the translucent clip-brush collision overlay on or off. The overlay geometry is
        supplied separately by the view through ``renderer.set_collision_mesh``."""
        self.renderer.show_collision = on
        if self.controller is not None:
            self.controller.show_collision = on
        self.invalidate()

    def gl_available(self) -> bool:
        if self._gl_failed:
            return False
        from opent5.gui.glrender import ShadedRenderer, can_draw

        # The decision uses the process-wide probe (can_draw), so defaulting to the GPU on every
        # map load does not create a GL context per view. Only when GL can actually draw do we
        # make this canvas its own renderer for the frames.
        if not can_draw():
            self._gl_failed = True
            return False
        if self._gl is None:
            self._gl = ShadedRenderer()
        return True

    def set_scene(self, scene, tex_count: int) -> None:
        """A prepared shaded scene (vertices, index runs and decoded textures)."""
        self._scene = scene
        self._scene_uploaded = False
        self._tex_count = tex_count
        self.invalidate()

    def update_scene_vertices(self, mesh, ranges) -> bool:
        """Refresh only the moved props' vertices in the uploaded shaded scene, both the
        CPU-side interleaved copy and the GPU buffer, for a live prop move where the triangles,
        groups and textures are unchanged. Returns False (so the caller falls back to a full
        scene rebuild) when there is no uploaded scene to patch, which is the case headless and
        before the first shaded frame."""
        if (
            self._scene is None
            or not self._scene_uploaded
            or self._gl is None
            or not ranges
            or mesh.normals is None
        ):
            return False
        inter = self._scene.interleaved
        for start, count in ranges:
            inter[start : start + count, 0:3] = mesh.positions[start : start + count]
            inter[start : start + count, 3:6] = mesh.normals[start : start + count]
        if not self._gl.update_vertices(inter, ranges):
            return False
        self.renderer.update_points(mesh.positions)  # keep the wireframe fallback in step
        self.invalidate()
        return True

    def invalidate(self) -> None:
        self._key = None
        self.update()

    def _scene_diag(self) -> float:
        lo, hi = self.renderer.bounds
        return float(np.linalg.norm(hi - lo))

    def frame(self, lo, hi) -> None:
        """Reposition the camera so the bounds ``(lo, hi)`` fill the view, without
        changing the view angles. Sets both the perspective distance and the ortho scale, so a
        preset frames the same whichever projection it uses."""
        lo, hi = np.asarray(lo, np.float64), np.asarray(hi, np.float64)
        radius = float(np.linalg.norm(hi - lo)) / 2.0
        aspect = max(self.width(), 1) / max(self.height(), 1)
        self.camera.target = (lo + hi) / 2.0
        # fill just over 1 so the whole of the bounds stays in view rather than cropping the
        # corners; the ortho scale fits with the same small margin.
        self.camera.distance = frame_distance(radius, FOV_DEG, aspect, fill=1.1)
        self.camera.ortho_half_h = ortho_half_for(radius, aspect)
        self.invalidate()
        self.camera_changed.emit()

    def set_preset(self, name: str) -> None:
        """Snap to a preset view (``top`` / ``front`` / ``side`` / ``perspective``) and frame
        the whole map. Top, Front and Side are orthographic and axis aligned; Perspective is the
        free 3D view. A fly toggle is dropped, as the presets are a stationary look."""
        angles = PRESETS.get(name)
        if angles is None:
            return
        self.set_fly(False)
        self.camera.yaw, pitch, self.camera.ortho = angles[0], angles[1], angles[2]
        self.camera.pitch = pitch  # presets may sit exactly at the pole; the drag clamp does not
        self._focus = None
        # frame the robust whole-map bounds (outliers trimmed), not the dense core, so a preset
        # shows the entire map rather than diving into the middle of it.
        self.frame(*self.renderer.frame_bounds)

    def mode_name(self) -> str:
        """The current interaction mode, for the on-screen readout."""
        if self.controller is not None:
            return f"edit: {self.controller.mode}"
        if self._flying:
            return "fly"
        return "orbit"

    def _world_per_pixel(self) -> float:
        """World units per screen pixel at the pivot, for pans and the cursor readout."""
        if self.camera.ortho:
            return 1.0 / max(self.camera.ortho_scale(max(self.height(), 1)), 1e-9)
        return self.camera.distance / max(focal(max(self.height(), 1)), 1.0)

    def frame_focus(self) -> None:
        """Frame the focused point (set by a double click) or, failing that, the
        dense core of the mesh."""
        if self._focus is not None:
            half = max(self._scene_diag() * 0.06, 8.0)
            self.frame(self._focus - half, self._focus + half)
        else:
            self.frame(*self.renderer.focus_bounds)

    def frame_scene(self) -> None:
        """Frame the whole map. Uses the robust bounds (far-flung skybox and stray verts
        trimmed), so Home shows the map rather than flying out to an outlier."""
        self.frame(*self.renderer.frame_bounds)

    def frame_all(self, focus: bool = False) -> None:
        lo, hi = self.renderer.focus_bounds if focus else self.renderer.frame_bounds
        self.frame(lo, hi)

    def reset_camera(self) -> None:
        """The view a map opens on: a perspective three-quarter angle framing the whole map."""
        self.set_fly(False)
        self.camera.ortho = False
        self.camera.yaw = math.radians(35.0)
        self.camera.pitch = math.radians(30.0)
        self._focus = None
        self.frame_all(focus=False)

    def _shaded_ready(self) -> bool:
        return self.shaded and self._scene is not None and self.gl_available()

    def render_image(self, fast: bool = False) -> QImage:
        ratio = self.devicePixelRatioF()
        w, h = max(1, int(self.width() * ratio)), max(1, int(self.height() * ratio))
        c = self.camera
        shaded = self._shaded_ready()
        key = (w, h, fast, c.yaw, c.pitch, c.distance, tuple(c.target), c.ortho, c.ortho_half_h,
               self.renderer.depth_cue, self.renderer.grid, theme.current().name,
               shaded, self.renderer.show_collision, self.renderer.collision_rev)  # fmt: skip
        if key != self._key or self._image is None:
            self._image = self._gl_image(w, h) if shaded else None
            if self._image is None:
                self._image = self.renderer.render(self.camera, w, h, fast)
            self._image.setDevicePixelRatio(ratio)
            self._key = key
        return self._image

    def _gl_image(self, w: int, h: int) -> QImage | None:
        if not self._scene_uploaded:
            if not self._gl.set_scene(self._scene):
                self._gl_failed = True
                return None
            self._scene_uploaded = True
        eye, _right, up, _fwd = self.camera.basis()
        lo, hi = self.renderer.bounds
        diag = float(np.linalg.norm(hi - lo)) or 1024.0
        far = self.camera.distance + diag * 1.5 + 16.0
        near = max(self.camera.distance * 0.002, 0.5)
        bg = _rgb(theme.current().base)
        ortho_half = self.camera.ortho_half_h if self.camera.ortho else None
        return self._gl.render(eye, self.camera.target, up, w, h, bg, near, far, ortho_half)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        t = theme.current()
        if not len(self.renderer.edges):
            p.fillRect(self.rect(), QColor(t.base))
            p.end()
            return
        p.drawImage(0, 0, self.render_image(self._dragging))
        self._paint_hud(p)
        if self.show_help:
            self._paint_help(p)
        self._paint_gizmo(p)
        if self.controller is not None:
            self._paint_edit(p)
        p.end()

    def _hud_lines(self) -> list[str]:
        """The one always-on readout, top-left: what the map is, the interaction mode, what is
        selected, and the grid, projection and cursor coordinate. Kept to a few short lines so
        it informs without crowding the view."""
        verts, tris, _edges = self.counts
        count = f"{verts:,} verts  {tris:,} tris"
        if self._dragging and not self._shaded_ready():
            count += f"   {self.renderer.last_ms:.0f} ms"
        lines = [self.title, count]
        mode = f"fly  {self.fly_speed:,.0f} u/s" if self._flying else self.mode_name()
        sel = self._selection_summary()
        if sel:
            mode += f"   {sel}"
        if self.controller is not None and self.controller.session.dirty:
            mode += "   edited"
        lines.append(mode)
        info = ["ortho" if self.camera.ortho else "persp"]
        if self.renderer.grid and not self._shaded_ready():
            info.append(f"grid {self.renderer.grid_step:g} u")
        if self._cursor_world is not None:
            cx, cy, cz = (float(v) for v in self._cursor_world)
            info.append(f"cursor {cx:.0f} {cy:.0f} {cz:.0f}")
        lines.append("   ".join(info))
        return lines

    def _selection_summary(self) -> str:
        ctl = self.controller
        if ctl is None:
            return ""
        if ctl.selected_clip is not None:
            return f"clip brush #{ctl.selected_clip}"
        prop = ctl.selected_prop_obj()
        if prop is not None:
            return f"{prop.model or 'prop'} #{prop.index}"
        obj = ctl.selected()
        if obj is not None:
            return f"{obj.label} ({obj.id})"
        return "nothing selected"

    def _paint_hud(self, p: QPainter) -> None:
        t = theme.current()
        p.setFont(theme.mono_font(8))
        p.setPen(QColor(t.text_dim))
        lines = self._hud_lines()
        fm = p.fontMetrics()
        back = QColor(t.base)
        back.setAlpha(210)
        width = max(fm.horizontalAdvance(line) for line in lines) + 12
        p.fillRect(2, 2, width, fm.height() * len(lines) + 8, back)
        y = 6 + fm.ascent()
        for i, line in enumerate(lines):
            # the mode line (index 2) in the text colour so it reads as the live state
            p.setPen(QColor(t.text if i == 2 else t.text_dim))
            p.drawText(8, y, line)
            y += fm.height()

    def _help_lines(self) -> tuple[str, ...]:
        """Camera controls, plus edit controls when Edit is on, or a prompt to turn it on."""
        if self.controller is not None:
            return EDIT_ON_HELP + HELP_LINES
        if self.edit_available:
            return EDIT_OFF_HINT + HELP_LINES
        return HELP_LINES

    def _paint_help(self, p: QPainter) -> int:
        """Draw the controls hint in the bottom-left corner. Returns its top y."""
        t = theme.current()
        p.setFont(theme.mono_font(7))
        fm = p.fontMetrics()
        lines = self._help_lines()
        width = max(fm.horizontalAdvance(line) for line in lines) + 12
        block = fm.height() * len(lines) + 8
        top = self.height() - block - 6
        back = QColor(t.base)
        back.setAlpha(170)
        p.fillRect(6, top, width, block, back)
        # the first line is the edit cue; draw it in the accent so it stands out
        edit_cue = self.controller is not None or self.edit_available
        y = top + 4 + fm.ascent()
        for i, line in enumerate(lines):
            p.setPen(QColor(t.accent if edit_cue and i == 0 else t.text_dim))
            p.drawText(12, y, line)
            y += fm.height()
        return top

    #: orientation gizmo axis -> (label, world direction, preset snapped to by a click).
    _GIZMO_AXES = (
        ("X", np.array([1.0, 0.0, 0.0]), "side"),
        ("Y", np.array([0.0, 1.0, 0.0]), "front"),
        ("Z", np.array([0.0, 0.0, 1.0]), "top"),
    )
    _GIZMO_R = 20

    def _gizmo_hub(self) -> tuple[int, int]:
        """Screen centre of the orientation gizmo (bottom-right corner)."""
        return self.width() - 38, self.height() - 38

    def _gizmo_preset_at(self, px: float, py: float):
        """The preset a click on the orientation gizmo selects, or None. An axis label picks
        that elevation; the hub picks the perspective view."""
        cx, cy = self._gizmo_hub()
        _eye, right, up, _fwd = self.camera.basis()
        r = self._GIZMO_R
        for _name, v, preset in self._GIZMO_AXES:
            lx = cx + float(v @ right) * (r + 7)
            ly = cy - float(v @ up) * (r + 7)
            if (px - lx) ** 2 + (py - ly) ** 2 <= 100:  # within ~10px of the label
                return preset
        if (px - cx) ** 2 + (py - cy) ** 2 <= 49:  # the hub, within ~7px
            return "perspective"
        return None

    def _paint_gizmo(self, p: QPainter) -> None:
        """A small clickable orientation gizmo: click an axis to snap to that elevation, or the
        hub for the perspective view."""
        t = theme.current()
        _eye, right, up, _fwd = self.camera.basis()
        cx, cy = self._gizmo_hub()
        r = self._GIZMO_R
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setFont(theme.mono_font(7))
        p.setBrush(QColor(t.text_dim))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QPointF(cx, cy), 2.5, 2.5)  # the hub (perspective)
        for name, v, _preset in self._GIZMO_AXES:
            sx, sy = float(v @ right), -float(v @ up)
            end = QPointF(cx + sx * r, cy + sy * r)
            p.setPen(QPen(QColor(t.text_dim), 1.0))
            p.drawLine(QPointF(cx, cy), end)
            p.setPen(QColor(t.text))
            p.drawText(QPointF(cx + sx * (r + 7) - 3, cy + sy * (r + 7) + 4), name)

    def _paint_edit(self, p: QPainter) -> None:
        """Draw entity markers (billboarded) and the selection gizmo."""
        ctl = self.controller
        t = theme.current()
        w, h = self.width(), self.height()
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        # Entity markers (spawns, triggers, path-relevant entities) are hundreds of dots on a
        # real map, so draw them only when the pick filter includes entities; the default
        # Props filter hides them so the props are visible and reachable.
        if ctl.entities_pickable:
            # marker_screen projects every marker in one pass and returns rows aligned with
            # markers(); iterate by position (no per-marker id lookup) and skip anything behind
            # the camera or off screen, so only the visible dots cost a draw call.
            markers = ctl.markers()
            _ids, pts, _depths = ctl.marker_screen(self.camera, w, h)
            sel = ctl.selected_id
            for obj, (sx, sy) in zip(markers, pts, strict=True):
                if np.isnan(sx) or sx < -8 or sy < -8 or sx > w + 8 or sy > h + 8:
                    continue
                selected = obj.id == sel
                colour = QColor(MARKER_KINDS.get(obj.kind, t.text))
                r = 5 if selected else 3
                if selected:
                    p.setPen(QPen(QColor(t.text), 1.5))
                else:
                    p.setPen(QPen(colour, 1.0))
                p.setBrush(colour)
                p.drawRect(int(sx - r), int(sy - r), 2 * r, 2 * r)
        self._paint_prop_markers(p)
        self._paint_cluster(p)
        self._paint_selected_clip(p)
        self._paint_gizmo_handles(p)

    def _paint_box(self, p: QPainter, mins, maxs) -> None:
        """Draw one axis-aligned box as a wireframe, projecting its eight corners."""
        from opent5.edit import gizmo as gz

        eye, right, up, forward = self.camera.basis()
        f = focal(self.height())
        w, h = self.width(), self.height()
        oscale = self.camera.ortho_scale(h) if self.camera.ortho else None
        corners = [
            (mins[0], mins[1], mins[2]), (maxs[0], mins[1], mins[2]),
            (maxs[0], maxs[1], mins[2]), (mins[0], maxs[1], mins[2]),
            (mins[0], mins[1], maxs[2]), (maxs[0], mins[1], maxs[2]),
            (maxs[0], maxs[1], maxs[2]), (mins[0], maxs[1], maxs[2]),
        ]  # fmt: skip
        edges = (
            (0, 1), (1, 2), (2, 3), (3, 0), (4, 5), (5, 6), (6, 7), (7, 4),
            (0, 4), (1, 5), (2, 6), (3, 7),
        )  # fmt: skip
        scr = [gz.project_point(c, eye, right, up, forward, f, w, h, oscale) for c in corners]
        for a, b in edges:
            if scr[a][3] and scr[b][3]:
                p.drawLine(QPointF(scr[a][0], scr[a][1]), QPointF(scr[b][0], scr[b][1]))

    def _paint_selected_clip(self, p: QPainter) -> None:
        """Highlight the selected clip brush (an invisible wall) so it reads clearly against the
        overlay, in the same clip colour but brighter."""
        ctl = self.controller
        box = ctl.selected_clip_box() if ctl is not None and ctl.selected_clip is not None else None
        if box is None:
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        p.setPen(QPen(QColor(CLIP_OVERLAY_COLOUR), 2.0))
        self._paint_box(p, box[0], box[1])

    def _diamond(self, p: QPainter, sx: float, sy: float, r: float) -> None:
        # QPainter.drawPolygon takes a single QPolygonF, not loose points: passing four
        # QPointF args raises inside paintEvent, which unwinds through Qt's C++ paint
        # callback and crashes the windowed app on the first repaint with props drawn.
        p.drawPolygon(
            QPolygonF(
                [QPointF(sx, sy - r), QPointF(sx + r, sy), QPointF(sx, sy + r), QPointF(sx - r, sy)]
            )  # fmt: skip
        )

    def _paint_prop_markers(self, p: QPainter) -> None:
        """Static-model props as small diamonds; the selected one brighter."""
        ctl = self.controller
        # Props are drawn (and clickable) only when the filter includes them and they are on
        # screen; the selected prop is always drawn so it stays visible under any filter.
        if not ctl.props_pickable and ctl.selected_prop is None:
            return
        t = theme.current()
        w, h = self.width(), self.height()
        base = QColor("#8e7fa0")
        if ctl.props_pickable:
            # One cached projection of every prop origin (reused across repaints while the
            # camera holds), then draw only the on-screen ones. Projecting each prop with a
            # Python call every paint was the overlay's cost on a prop-heavy map.
            _idxs, pts, _depths = ctl.prop_screen(self.camera, w, h)
            selected_idx = ctl.selected_prop
            p.setPen(QPen(base, 1.2))
            p.setBrush(base)
            for (sx, sy), idx in zip(pts, _idxs, strict=True):
                if idx == selected_idx:
                    continue  # drawn below from the live gizmo centre
                if np.isnan(sx) or sx < -8 or sy < -8 or sx > w + 8 or sy > h + 8:
                    continue
                self._diamond(p, sx, sy, 4)
        if ctl.selected_prop is not None:
            from opent5.edit import gizmo as gz

            eye, right, up, forward = self.camera.basis()
            f = focal(h)
            oscale = self.camera.ortho_scale(h) if self.camera.ortho else None
            centre = ctl.gizmo_centre()  # follows a live translate preview
            if centre is not None:
                sx, sy, _d, front = gz.project_point(centre, eye, right, up, forward, f, w, h,
                                                     oscale)  # fmt: skip
                if front:
                    colour = QColor("#c88bd8")
                    p.setPen(QPen(QColor(t.text), 1.5))
                    p.setBrush(colour)
                    self._diamond(p, sx, sy, 6)

    def _paint_cluster(self, p: QPainter) -> None:
        """Highlight the selected prop's clip cluster (the collision a move or delete
        affects) as wireframe boxes, so the user sees exactly what will change."""
        ctl = self.controller
        boxes = ctl.preview_cluster_boxes() if ctl.selected_prop is not None else []
        if not boxes:
            return
        from opent5.edit import gizmo as gz

        eye, right, up, forward = self.camera.basis()
        f = focal(self.height())
        w, h = self.width(), self.height()
        oscale = self.camera.ortho_scale(h) if self.camera.ortho else None
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        p.setPen(QPen(QColor("#e0a0ff"), 1.2))
        edges = (
            (0, 1), (1, 2), (2, 3), (3, 0), (4, 5), (5, 6), (6, 7), (7, 4),
            (0, 4), (1, 5), (2, 6), (3, 7),
        )  # fmt: skip
        for mins, maxs in boxes:
            corners = [
                (mins[0], mins[1], mins[2]), (maxs[0], mins[1], mins[2]),
                (maxs[0], maxs[1], mins[2]), (mins[0], maxs[1], mins[2]),
                (mins[0], mins[1], maxs[2]), (maxs[0], mins[1], maxs[2]),
                (maxs[0], maxs[1], maxs[2]), (mins[0], maxs[1], maxs[2]),
            ]  # fmt: skip
            scr = [gz.project_point(c, eye, right, up, forward, f, w, h, oscale) for c in corners]
            for a, b in edges:
                if scr[a][3] and scr[b][3]:
                    p.drawLine(QPointF(scr[a][0], scr[a][1]), QPointF(scr[b][0], scr[b][1]))

    def _paint_gizmo_handles(self, p: QPainter) -> None:
        ctl = self.controller
        centre, handles = ctl.axis_handles(self.camera, self.width(), self.height())
        if centre is None:
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        names = ("X", "Y", "Z")
        colours = ("#d85c5c", "#49b36b", "#5c7cd8")
        for i, (sx, sy), front in handles:
            if not front:
                continue
            pen = QPen(QColor(colours[i]), 2.0)
            p.setPen(pen)
            p.drawLine(QPointF(*centre), QPointF(sx, sy))
            p.setBrush(QColor(colours[i]))
            p.drawEllipse(QPointF(sx, sy), 4, 4)
            p.drawText(QPointF(sx + 4, sy - 4), names[i])

    # interaction
    def mousePressEvent(self, e) -> None:
        self._press_pos = e.position()
        if e.button() == Qt.MouseButton.LeftButton:
            preset = self._gizmo_preset_at(e.position().x(), e.position().y())
            if preset is not None:  # clicked the orientation gizmo: snap to that view
                self.set_preset(preset)
                self.setFocus()
                return
        if self.controller is not None and e.button() == Qt.MouseButton.LeftButton:
            axis = self.controller.hit_axis(
                self.camera, e.position().x(), e.position().y(), self.width(), self.height()
            )
            has_sel = (
                self.controller.selected() is not None or self.controller.selected_prop is not None
            )
            if axis is not None and has_sel:
                self.controller.begin_drag(
                    axis, self.camera, e.position().x(), e.position().y(),
                    self.width(), self.height(),
                )  # fmt: skip
                self._edit_drag = True
                self._drag = None
                self.setFocus()
                return
        self._drag = (e.position(), e.buttons())
        self.setFocus()
        if e.button() == Qt.MouseButton.RightButton:
            self._begin_fly_hold()

    def mouseMoveEvent(self, e) -> None:
        moved = self._track_cursor(e.position().x(), e.position().y())
        if self._edit_drag and self.controller is not None:
            self.controller.update_drag(
                self.camera, e.position().x(), e.position().y(), self.width(), self.height()
            )
            self._dragging = True
            self.invalidate()
            # a prop drag only previews until release, so no session edit has happened yet.
            if not self.controller.previewing:
                self.edited.emit()
            return
        if self._drag is None:
            # Redraw for the cursor readout, but only when the shown coordinate actually
            # changes: the HUD prints whole units, so a sub-unit wiggle need not repaint the
            # whole overlay on every mouse-move event.
            if moved:
                self.update()
            return
        last, _buttons = self._drag
        d = e.position() - last
        self._drag = (e.position(), e.buttons())
        c = self.camera
        buttons = e.buttons()
        look = buttons & (Qt.MouseButton.LeftButton | Qt.MouseButton.RightButton)
        if self._flying and look:  # fly: mouse-look in place, eye fixed
            eye, _r, _u, _f = c.basis()
            c.yaw -= d.x() * FLY_LOOK_SENS
            c.set_pitch(c.pitch + d.y() * FLY_LOOK_SENS)
            _e2, _r2, _u2, forward = c.basis()
            c.target = eye + forward * c.distance
        elif buttons & Qt.MouseButton.LeftButton:  # orbit
            c.yaw -= d.x() * ORBIT_SENS * self.sensitivity
            c.set_pitch(c.pitch + d.y() * ORBIT_SENS * self.sensitivity)
        elif buttons & Qt.MouseButton.MiddleButton:  # pan, same apparent speed in either mode
            _eye, right, up, _f = c.basis()
            wpp = self._world_per_pixel()
            c.target = c.target - right * (d.x() * wpp) + up * (d.y() * wpp)
        else:
            return
        self._dragging = True
        self._settle.start()
        self.invalidate()
        self.camera_changed.emit()

    def _track_cursor(self, px: float, py: float) -> bool:
        """Record the world point under the cursor (where its ray meets the ground plane) for
        the on-screen readout. The ground plane is the grid's Z, so the coordinate reads true
        for prop placement in the Top view. Returns whether the shown (whole-unit) coordinate
        changed, so an idle mouse-move can skip a repaint when the readout would not change."""
        eye, d = cursor_ray(self.camera, px, py, self.width(), self.height())
        lo, _hi = self.renderer.frame_bounds
        z = float(lo[2])
        dz = float(d[2])
        if abs(dz) < 1e-9:
            self._cursor_world = None
        else:
            t = (z - float(eye[2])) / dz
            self._cursor_world = eye + d * t if t > 0 else None
        shown = (None if self._cursor_world is None
                 else tuple(round(float(v)) for v in self._cursor_world))  # fmt: skip
        if shown == self._cursor_shown:
            return False
        self._cursor_shown = shown
        return True

    def mouseReleaseEvent(self, e) -> None:
        if self._edit_drag and e.button() == Qt.MouseButton.LeftButton:
            self.controller.end_drag()
            self._edit_drag = False
            self._dragging = False
            self.invalidate()
            self.edited.emit()
            return
        if (
            self.controller is not None
            and e.button() == Qt.MouseButton.LeftButton
            and self._press_pos is not None
            and (e.position() - self._press_pos).manhattanLength() < 4
        ):
            picked = self.controller.pick(
                self.camera, e.position().x(), e.position().y(), self.width(), self.height()
            )
            self.selection_changed.emit(picked)
            self.invalidate()
        if e.button() == Qt.MouseButton.RightButton:
            self._end_fly_hold()
            # a right CLICK (not a fly-look drag) over the edit view opens the context menu.
            if (
                self.controller is not None
                and self._press_pos is not None
                and (e.position() - self._press_pos).manhattanLength() < 4
            ):
                self._show_context_menu(e.position())
        self._drag = None
        self._settle.start(10)

    def _show_context_menu(self, pos) -> None:
        """Right-click menu on a selected prop or clip brush. The default acts on the whole
        bundle; the other items act on a single piece, and the move-scope toggles set which
        pieces a later move drag follows. Baked-triangle collision is listed but disabled."""
        ctl = self.controller
        if ctl is None:
            return
        px, py = pos.x(), pos.y()
        # if nothing relevant is selected, pick what was right-clicked first
        if ctl.selected_prop is None and ctl.selected_clip is None:
            ctl.pick(self.camera, px, py, self.width(), self.height())
            self.selection_changed.emit(ctl.selected_id)
        menu = QMenu(self)
        if ctl.selected_prop is not None:
            self._build_prop_menu(menu, ctl)
        elif ctl.selected_clip is not None:
            self._build_clip_menu(menu, ctl)
        else:
            return
        menu.exec(self.mapToGlobal(QPoint(int(px), int(py))))
        self.invalidate()

    def _build_prop_menu(self, menu: QMenu, ctl) -> None:
        has_cluster = bool(ctl.selected_cluster())
        menu.addAction(
            "Delete everything (model + collision)",
            lambda: self._menu_delete(ctl.delete_selected_prop_clip),
        )
        menu.addAction(
            "Delete model only", lambda: self._menu_delete(ctl.delete_selected_model_only)
        )
        coll = menu.addAction(
            "Delete collision only", lambda: self._menu_delete(ctl.delete_selected_collision_only)
        )
        coll.setEnabled(has_cluster)
        menu.addSeparator()
        am = menu.addAction("Move drag moves the model")
        am.setCheckable(True)
        am.setChecked(ctl.move_model)
        am.toggled.connect(lambda v: setattr(ctl, "move_model", v))
        ac = menu.addAction("Move drag moves the collision")
        ac.setCheckable(True)
        ac.setChecked(ctl.move_collision)
        ac.toggled.connect(lambda v: setattr(ctl, "move_collision", v))
        pieces = ctl.footprint_pieces()
        if pieces["clips"]:
            sub = menu.addMenu("Delete one clip brush")
            for i in pieces["clips"]:
                sub.addAction(
                    f"clip brush #{i}",
                    lambda i=i: self._menu_delete(lambda: ctl.delete_clip_brushes([i])),
                )
        if pieces["props"]:
            sub2 = menu.addMenu("Overlapping props")
            for j in pieces["props"]:
                sub2.addAction(
                    f"delete prop #{j} (everything)",
                    lambda j=j: self._menu_delete_other_prop(j),
                )
        menu.addSeparator()
        baked = menu.addAction("Baked-triangle collision: cannot remove (needs recompile)")
        baked.setEnabled(False)

    def _build_clip_menu(self, menu: QMenu, ctl) -> None:
        menu.addAction(
            "Delete this clip brush (invisible wall)",
            lambda: self._menu_delete(ctl.delete_selected_clip),
        )
        baked = menu.addAction("Baked-triangle collision: cannot remove (needs recompile)")
        baked.setEnabled(False)

    def _menu_delete(self, action) -> None:
        action()
        self.controller.selected_prop = None
        self.selection_changed.emit(None)
        self.edited.emit()
        self.invalidate()

    def _menu_delete_other_prop(self, prop_index: int) -> None:
        ctl = self.controller
        ctl.selected_prop = prop_index
        ctl.delete_selected_prop_clip()
        ctl.selected_prop = None
        self.selection_changed.emit(None)
        self.edited.emit()
        self.invalidate()

    def mouseDoubleClickEvent(self, e) -> None:
        if e.button() != Qt.MouseButton.LeftButton:
            return
        eye, d = cursor_ray(self.camera, e.position().x(), e.position().y(),
                            self.width(), self.height())  # fmt: skip
        hit = ray_mesh_hit(eye, d, self.renderer.points, self.renderer.tris)
        if hit is None:
            return
        self._focus = np.asarray(hit, np.float64)
        self.camera.look_from(eye, hit)
        self.invalidate()
        self.camera_changed.emit()

    def set_fly(self, on: bool) -> None:
        """Turn the persistent fly toggle on or off (the Fly button and Space call this)."""
        if self._fly_toggle == on:
            return
        self._fly_toggle = on
        self._update_fly_state()
        self.fly_changed.emit(self._fly_toggle)

    def toggle_fly(self) -> None:
        self.set_fly(not self._fly_toggle)

    def _begin_fly_hold(self) -> None:
        self._fly_hold = True
        self._update_fly_state()

    def _end_fly_hold(self) -> None:
        self._fly_hold = False
        self._update_fly_state()

    def _update_fly_state(self) -> None:
        """Fly is active while held with the right button or while toggled on; keep the move
        timer and the flag in step with those two inputs."""
        flying = self._fly_toggle or self._fly_hold
        if flying == self._flying:
            return
        self._flying = flying
        if flying:
            self._fly_last = time.perf_counter()
            self._fly_timer.start()
        else:
            self._fly_timer.stop()
            self._fly_keys.clear()
            self._dragging = False
        self.invalidate()
        self.camera_changed.emit()

    def _fly_step(self) -> None:
        now = time.perf_counter()
        dt = min(now - self._fly_last, 0.1)
        self._fly_last = now
        keys = self._fly_keys
        fb = (Qt.Key.Key_W in keys) - (Qt.Key.Key_S in keys)
        lr = (Qt.Key.Key_D in keys) - (Qt.Key.Key_A in keys)
        ud = (Qt.Key.Key_E in keys) - (Qt.Key.Key_Q in keys)
        if not (fb or lr or ud):
            return
        from PySide6.QtWidgets import QApplication

        _eye, right, _up, forward = self.camera.basis()
        move = forward * fb + right * lr + np.array([0.0, 0.0, 1.0]) * ud
        n = float(np.linalg.norm(move))
        if n < 1e-9:
            return
        fast = bool(QApplication.keyboardModifiers() & Qt.KeyboardModifier.ShiftModifier)
        step = self.fly_speed * (3.0 if fast else 1.0) * dt
        self.camera.target = self.camera.target + move / n * step
        self._dragging = True
        self.invalidate()
        self.camera_changed.emit()

    def _end_drag(self) -> None:
        if self._flying:
            return
        if self._drag is None or not self._drag[1]:
            self._dragging = False
            self.invalidate()

    def wheelEvent(self, e) -> None:
        steps = e.angleDelta().y() / 120.0
        px, py = e.position().x(), e.position().y()
        if self._flying:  # the wheel sets the fly speed, not the zoom
            self.fly_speed = max(1.0, self.fly_speed * (1.25**steps))
            self.camera_changed.emit()
            self.update()
            return
        factor = ZOOM_STEP ** (steps * self.sensitivity)
        if self.camera.ortho:
            min_half = max(self._scene_diag() * 5e-4, 1.0)
            self.camera.target, self.camera.ortho_half_h = dolly_ortho(
                self.camera, px, py, self.width(), self.height(), factor, min_half
            )
        else:
            eye, d = cursor_ray(self.camera, px, py, self.width(), self.height())
            _e2, _r, _u, forward = self.camera.basis()
            min_dist = max(self._scene_diag() * 5e-4, 1.0)
            # zoom towards the surface actually under the cursor when the ray hits one, so the
            # point stays put; otherwise towards the pivot plane.
            hit = ray_mesh_hit(eye, d, self.renderer.points, self.renderer.tris)
            anchor_t = float(np.dot(hit - eye, d)) if hit is not None else None
            target, dist = dolly_to_ray(
                eye, forward, self.camera.distance, d, factor, min_dist, anchor_t
            )
            self.camera.target, self.camera.distance = target, dist
        self._dragging = True
        self._settle.start()
        self.invalidate()
        self.camera_changed.emit()

    #: number keys to preset views.
    _PRESET_KEYS = {
        Qt.Key.Key_1: "top", Qt.Key.Key_2: "front",
        Qt.Key.Key_3: "side", Qt.Key.Key_4: "perspective",
    }  # fmt: skip

    def keyPressEvent(self, e) -> None:
        k = e.key()
        if self.controller is not None and self._edit_key(e, k):
            return
        if k == Qt.Key.Key_F:
            self.frame_focus()
        elif k == Qt.Key.Key_Home:
            self.frame_scene()
        elif k == Qt.Key.Key_H:
            self.show_help = not self.show_help
            self.update()
        elif k == Qt.Key.Key_Space:
            self.toggle_fly()
        elif k in self._PRESET_KEYS:
            self.set_preset(self._PRESET_KEYS[k])
        elif k in _FLY_KEYS and self._flying:
            if not e.isAutoRepeat():
                self._fly_keys.add(k)
        else:
            super().keyPressEvent(e)

    def _edit_key(self, e, k) -> bool:
        """Editing keys: G translate, R rotate, B add a crate, Delete delete, Ctrl+D
        duplicate, Esc deselect. Returns True when the key was an editing key."""
        ctl = self.controller
        ctrl = bool(e.modifiers() & Qt.KeyboardModifier.ControlModifier)
        if k == Qt.Key.Key_G:
            ctl.mode = "translate"
        elif k == Qt.Key.Key_R and not ctrl:
            ctl.mode = "rotate"
        elif k == Qt.Key.Key_B and not ctrl:
            self._add_crate()
        elif k in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace) and ctl.selected_clip is not None:
            ctl.delete_selected_clip()  # remove the picked clip brush (an invisible wall)
            self.selection_changed.emit(None)
            self.edited.emit()
        elif k in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace) and ctl.selected_prop is not None:
            ctl.delete_selected_prop_clip()  # remove the whole bundle (model + collision)
            ctl.selected_prop = None
            self.selection_changed.emit(None)
            self.edited.emit()
        elif k in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace) and ctl.selected_id is not None:
            ctl.delete()
            self.selection_changed.emit(None)
            self.edited.emit()
        elif k == Qt.Key.Key_D and ctrl and ctl.selected_id is not None:
            ctl.duplicate()
            self.selection_changed.emit(ctl.selected_id)
            self.edited.emit()
        elif k == Qt.Key.Key_Escape and ctl.selected_id is not None:
            ctl.selected_id = None
            self.selection_changed.emit(None)
        else:
            return False
        self.invalidate()
        return True

    def _add_crate(self) -> None:
        """Quick add (B): place the zone's default crate-like prop at the view target."""
        ctl = self.controller
        if ctl is None or not ctl.can_edit_clips:
            self.status.emit("This zone has no editable clipMap, so a prop cannot be added.")
            return
        model = ctl.default_crate_model()
        if model is None:
            self.status.emit("This zone carries no placeable static-model prop to place.")
            return
        self.add_prop_chosen(model)

    def add_prop_chosen(self, model: str) -> None:
        """Place the chosen static-model prop at the current view target (render static model +
        clip), then select it. Reports why if the spot cannot take one."""
        ctl = self.controller
        if ctl is None or not ctl.can_edit_clips:
            self.status.emit("This zone has no editable clipMap, so a prop cannot be added.")
            return
        target = np.asarray(self.camera.target, np.float64)
        try:
            result = ctl.add_prop(model, target, (24.0, 24.0, 24.0))
        except EditError as exc:
            self.status.emit(f"Could not add {model} here: {exc}")
            return
        note = f"  ({result['warnings'][0]})" if result.get("warnings") else ""
        self.status.emit(f"Added {model}{note}")
        self.selection_changed.emit(None)
        self.edited.emit()
        self.invalidate()

    def keyReleaseEvent(self, e) -> None:
        if not e.isAutoRepeat():
            self._fly_keys.discard(e.key())
        super().keyReleaseEvent(e)

    def resizeEvent(self, e) -> None:
        self.invalidate()
        super().resizeEvent(e)


class MapEditPanel(QWidget):
    """Property panel for the in-place editor: the selected entity's keys, the gizmo mode and
    snap, add / duplicate / delete, undo / redo and save to a new file."""

    changed = Signal()  # an edit happened; the view refreshes markers
    status = Signal(str)
    #: the Add prop button was pressed; the view opens the model picker (it owns the camera).
    add_prop_requested = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("MapEditPanel")
        self.controller = None
        self._on_save = None  # callback(path) set by the view
        self._field_edits: dict[str, QLineEdit] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(6)
        title = QLabel("Map editor")
        f = title.font()
        f.setBold(True)
        title.setFont(f)
        root.addWidget(title)

        how = QLabel(
            "Click a prop in the view to select it, then drag a gizmo axis to move it.\n"
            "G move   R rotate   B add crate   Del remove   Ctrl+D duplicate   Esc deselect",
            self,
        )
        how.setObjectName("AssetMeta")
        how.setWordWrap(True)
        root.addWidget(how)

        filter_row = QHBoxLayout()
        self.pick_filter = QComboBox(self)
        self.pick_filter.addItems(["Props", "Entities", "All"])
        self.pick_filter.setToolTip(
            "What a click selects, and which markers are shown. Props hides the entity dots so "
            "the models are reachable."
        )
        self.pick_filter.currentIndexChanged.connect(self._set_pick_filter)
        filter_row.addWidget(QLabel("Pick"))
        filter_row.addWidget(self.pick_filter, 1)
        root.addLayout(filter_row)

        mode_row = QHBoxLayout()
        self.mode = QComboBox(self)
        self.mode.addItems(["Translate (G)", "Rotate (R)"])
        self.mode.currentIndexChanged.connect(self._set_mode)
        mode_row.addWidget(QLabel("Gizmo"))
        mode_row.addWidget(self.mode, 1)
        root.addLayout(mode_row)

        snap_row = QHBoxLayout()
        self.grid = QLineEdit("0", self)
        self.grid.setToolTip("Translate snap, world units (0 off)")
        self.grid.editingFinished.connect(self._set_snap)
        self.angle = QLineEdit("0", self)
        self.angle.setToolTip("Rotate snap, degrees (0 off)")
        self.angle.editingFinished.connect(self._set_snap)
        snap_row.addWidget(QLabel("Snap"))
        snap_row.addWidget(self.grid)
        snap_row.addWidget(QLabel("deg"))
        snap_row.addWidget(self.angle)
        root.addLayout(snap_row)

        act_row = QHBoxLayout()
        self.add_button = QPushButton("Add", self)
        self.add_button.setToolTip("Add a placed entity (asks for a classname)")
        self.add_button.clicked.connect(self._add)
        self.add_prop_button = QPushButton("Add prop", self)
        self.add_prop_button.setToolTip("Place a static-model prop: pick from the zone's models")
        self.add_prop_button.clicked.connect(self.add_prop_requested)
        self.dup_button = QPushButton("Duplicate", self)
        self.dup_button.clicked.connect(self._duplicate)
        self.del_button = QPushButton("Delete", self)
        self.del_button.clicked.connect(self._delete)
        for b in (self.add_button, self.add_prop_button, self.dup_button, self.del_button):
            act_row.addWidget(b)
        root.addLayout(act_row)

        hist_row = QHBoxLayout()
        self.undo_button = QPushButton("Undo", self)
        self.undo_button.clicked.connect(self._undo)
        self.redo_button = QPushButton("Redo", self)
        self.redo_button.clicked.connect(self._redo)
        hist_row.addWidget(self.undo_button)
        hist_row.addWidget(self.redo_button)
        root.addLayout(hist_row)

        self.selected_label = QLabel("Nothing selected", self)
        self.selected_label.setWordWrap(True)
        root.addWidget(self.selected_label)

        # non-blocking notices from the last clip edit (baked shadow, collision stays)
        self.notice = QLabel("", self)
        self.notice.setWordWrap(True)
        self.notice.setObjectName("EditNotice")
        self.notice.setStyleSheet("color: #d8973c;")
        self.notice.setVisible(False)
        root.addWidget(self.notice)

        self.form_host = QWidget(self)
        self.form = QFormLayout(self.form_host)
        self.form.setContentsMargins(0, 0, 0, 0)
        self.form.setSpacing(3)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.form_host)
        root.addWidget(scroll, 1)

        key_row = QHBoxLayout()
        self.new_key = QLineEdit(self)
        self.new_key.setPlaceholderText("new key")
        self.new_value = QLineEdit(self)
        self.new_value.setPlaceholderText("value")
        add_key = QPushButton("Set", self)
        add_key.clicked.connect(self._add_key)
        key_row.addWidget(self.new_key)
        key_row.addWidget(self.new_value)
        key_row.addWidget(add_key)
        root.addLayout(key_row)

        self.save_button = QPushButton("Save edited map ...", self)
        self.save_button.clicked.connect(self._save)
        root.addWidget(self.save_button)

    # wiring
    def set_controller(self, controller, on_save=None) -> None:
        self.controller = controller
        self._on_save = on_save
        self.show_object(controller.selected_id if controller else None)

    def set_mode(self, mode: str) -> None:
        self.mode.blockSignals(True)
        self.mode.setCurrentIndex(1 if mode == "rotate" else 0)
        self.mode.blockSignals(False)

    def _set_mode(self, index: int) -> None:
        if self.controller is not None:
            self.controller.mode = "rotate" if index == 1 else "translate"

    #: combo index -> controller pick-filter value.
    _PICK_VALUES = (MapEditController.PICK_PROPS, MapEditController.PICK_ENTITIES,
                    MapEditController.PICK_ALL)  # fmt: skip

    def set_pick_filter(self, value: str) -> None:
        """Reflect the controller's pick filter in the combo without re-firing the change."""
        index = self._PICK_VALUES.index(value) if value in self._PICK_VALUES else 0
        self.pick_filter.blockSignals(True)
        self.pick_filter.setCurrentIndex(index)
        self.pick_filter.blockSignals(False)

    def _set_pick_filter(self, index: int) -> None:
        if self.controller is None:
            return
        self.controller.pick_filter = self._PICK_VALUES[index]
        # a selection the new filter excludes is dropped, so the gizmo does not hang on a
        # hidden object, then the view redraws with the right markers.
        if self.controller.pick_filter == MapEditController.PICK_PROPS:
            self.controller.selected_id = None
        elif self.controller.pick_filter == MapEditController.PICK_ENTITIES:
            self.controller.selected_prop = None
        self.show_object(self.controller.selected_id)
        self.changed.emit()

    def _set_snap(self) -> None:
        if self.controller is None:
            return
        self.controller.grid = _as_float(self.grid.text())
        self.controller.angle = _as_float(self.angle.text())

    def _show_notice(self) -> None:
        warnings = list(getattr(self.controller, "warnings", []) or []) if self.controller else []
        self.notice.setText("\n".join(warnings))
        self.notice.setVisible(bool(warnings))

    def show_object(self, obj_id) -> None:
        while self.form.rowCount():
            self.form.removeRow(0)
        self._field_edits.clear()
        self._show_notice()
        if self.controller is not None:
            self.undo_button.setEnabled(self.controller.session.can_undo)
            self.redo_button.setEnabled(self.controller.session.can_redo)
        # a clip brush selected in the collision overlay: show it, offer removal
        clip = self.controller.selected_clip if self.controller else None
        if clip is not None:
            self.dup_button.setEnabled(False)
            self.del_button.setEnabled(True)
            self.selected_label.setText(
                f"clip brush #{clip} (an invisible collision wall)\n"
                "Delete removes it. Baked-triangle collision cannot be removed here."
            )
            return
        # a static-model prop selected: show its clip cluster, not entity fields
        prop = self.controller.selected_prop_obj() if self.controller else None
        if prop is not None:
            cluster = self.controller.selected_cluster()
            self.dup_button.setEnabled(False)
            self.del_button.setEnabled(bool(cluster))
            name = prop.model or "(unnamed static model)"
            if cluster:
                self.selected_label.setText(
                    f"prop: {name}\nindex {prop.index}, {len(cluster)} clip brush(es) "
                    "(Delete removes them, shown highlighted)"
                )
            else:
                self.selected_label.setText(
                    f"prop: {name}\nindex {prop.index}, no clip collision at its footprint"
                )
            return
        obj = None
        if self.controller is not None and obj_id is not None:
            obj = self.controller.selected()
        has = obj is not None
        self.dup_button.setEnabled(has)
        self.del_button.setEnabled(has)
        if obj is None:
            self.selected_label.setText("Nothing selected: click a prop in the view to select it.")
            return
        self.selected_label.setText(f"{obj.kind}: {obj.label}  (id {obj.id})")
        for key, value in obj.keys.items():
            edit = QLineEdit(value, self)
            edit.setReadOnly(key == "classname")
            edit.editingFinished.connect(lambda k=key, e=edit: self._commit(k, e))
            self.form.addRow(key, edit)
            self._field_edits[key] = edit

    # actions
    def _commit(self, key: str, edit: QLineEdit) -> None:
        if self.controller is None:
            return
        self._guard(lambda: self.controller.set_property(key, edit.text()))

    def _add_key(self) -> None:
        if self.controller is None or self.controller.selected_id is None:
            return
        key = self.new_key.text().strip()
        if not key:
            return
        self._guard(lambda: self.controller.set_property(key, self.new_value.text()))
        self.new_key.clear()
        self.new_value.clear()
        self.show_object(self.controller.selected_id)

    def _add(self) -> None:
        if self.controller is None:
            return
        name, ok = QInputDialog.getText(self, "Add entity", "classname:")
        if not ok or not name.strip():
            return
        self._guard(lambda: self.controller.add({"classname": name.strip(), "origin": "0 0 0"}))
        self.show_object(self.controller.selected_id)

    def _duplicate(self) -> None:
        if self.controller is not None:
            self._guard(self.controller.duplicate)
            self.show_object(self.controller.selected_id)

    def _delete(self) -> None:
        if self.controller is None:
            return
        if self.controller.selected_clip is not None:
            self._guard(self.controller.delete_selected_clip)
            self.show_object(None)
            return
        if self.controller.selected_prop is not None:
            self._guard(self.controller.delete_selected_prop_clip)
            self.controller.selected_prop = None
            self.show_object(None)
            return
        self._guard(self.controller.delete)
        self.show_object(None)

    def _undo(self) -> None:
        if self.controller is not None:
            self.controller.undo()
            self.changed.emit()
            self.show_object(self.controller.selected_id)

    def _redo(self) -> None:
        if self.controller is not None:
            self.controller.redo()
            self.changed.emit()
            self.show_object(self.controller.selected_id)

    def _save(self) -> None:
        if self.controller is None or self._on_save is None:
            return
        session = self.controller.session
        default = session.default_save_path()
        path, _ = QFileDialog.getSaveFileName(
            self, "Save edited map", str(default or ""), "Fastfiles (*.ff)"
        )
        if path:
            self._on_save(path)

    def _guard(self, action) -> None:
        from opent5.edit import EditError

        try:
            action()
        except EditError as exc:
            self.status.emit(str(exc))
            return
        self.changed.emit()
        if self.controller is not None:
            self.show_object(self.controller.selected_id)


def _as_float(text: str) -> float:
    try:
        return float(text)
    except ValueError:
        return 0.0


class MeshView(AssetView):
    kind = "geometry"
    title = "Geometry"

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.mesh: MeshData | None = None
        self.mesh_kind: str | None = None
        self._generation = 0
        self._sync = False
        self._jobs: set = set()
        #: set once the user toggles Shaded by hand, so their choice sticks for the session and
        #: a later map load does not override it with the GPU-or-not default.
        self._shaded_user_set = False

        bar = QWidget(self)
        bar.setObjectName("ViewBar")
        row = QHBoxLayout(bar)
        row.setContentsMargins(4, 0, 4, 0)
        row.setSpacing(2)
        self.shaded_button = self._toggle("Shaded", False, self._set_shaded)
        self.shaded_button.setToolTip("Shaded, textured (GPU) instead of wireframe")
        self.depth_button = self._toggle("Depth cue", True, self._set_depth)
        self.grid_button = self._toggle("Grid", True, self._set_grid)
        self.models_button = self._toggle("Static models", True, self._set_models)
        self.models_button.setToolTip("Place the world's static models (props)")
        self.models_button.setVisible(False)
        self.edit_button = self._toggle("Edit", False, self._set_edit)
        self.edit_button.setToolTip(
            "Edit placed entities and static-model props in place; click a marker to select"
        )
        self.edit_button.setVisible(False)
        self.fly_button = self._toggle("Fly", False, self._set_fly)
        self.fly_button.setAutoRaise(True)
        self.fly_button.setToolTip("Fly through the map: WASD move, Q/E down/up, Shift sprint "
                                   "(Space, or hold the right button)")  # fmt: skip
        self.collision_button = self._toggle("Show collision", False, self._set_show_collision)
        self.collision_button.setToolTip(
            "Overlay the clip-brush collision (the invisible walls) as translucent volumes; "
            "click one to select it, Delete removes it"
        )
        self.collision_button.setVisible(False)
        self.collision_family = QComboBox(bar)
        for label, _fam in COLLISION_FAMILIES:
            self.collision_family.addItem(label)
        self.collision_family.setToolTip("Which collision to show in the overlay")
        self.collision_family.currentIndexChanged.connect(self._set_collision_family)
        self.collision_family.setVisible(False)
        self.mode = QComboBox(bar)
        self.mode.addItems(COLLISION_MODES)
        self.mode.setToolTip("Which collision geometry to draw")
        self.mode.currentIndexChanged.connect(self._apply_mode)
        # Preset views: Top / Front / Side are orthographic and axis aligned, Perspective is
        # the free 3D view. Each snaps the camera and frames the whole map.
        self.view_buttons = []
        for text, preset, tip in (
            ("Top", "top", "Top-down orthographic view (1), for placing props"),
            ("Front", "front", "Front orthographic elevation (2)"),
            ("Side", "side", "Side orthographic elevation (3)"),
            ("Persp", "perspective", "Free perspective view (4)"),
        ):
            b = QToolButton(bar)
            b.setText(text)
            b.setToolTip(tip)
            b.setAutoRaise(True)
            b.clicked.connect(lambda _c=False, name=preset: self._pick_preset(name))
            self.view_buttons.append(b)
        self.frame_button = QToolButton(bar)
        self.frame_button.setText("Frame all")
        self.frame_button.setToolTip("Frame the whole map (Home)")
        self.frame_button.clicked.connect(lambda: self.canvas.frame_scene())
        self.info = QLabel("", bar)
        self.info.setObjectName("AssetMeta")
        for w in (self.shaded_button, self.depth_button, self.grid_button,
                  self.models_button, self.edit_button, self.fly_button,
                  self.collision_button, self.collision_family, self.mode):  # fmt: skip
            row.addWidget(w)
        row.addSpacing(8)
        for b in self.view_buttons:
            row.addWidget(b)
        row.addStretch(1)
        row.addWidget(self.info)
        row.addWidget(self.frame_button)
        self.bar = bar

        self.canvas = MeshCanvas(self)
        self.canvas.camera_changed.connect(self._camera_status)
        self.canvas.fly_changed.connect(self._on_fly_changed)
        self.canvas.selection_changed.connect(self._on_pick)
        self.canvas.edited.connect(self._on_edited)
        self.canvas.status.connect(self.status)
        self.placeholder = empty_state("", self)
        self.stack = QStackedLayout()
        holder = QWidget(self)
        holder.setLayout(self.stack)
        self.stack.addWidget(self.placeholder)
        self.stack.addWidget(self.canvas)

        self._session = None
        self._edit_controller = None
        #: set while Edit is being turned on but the static models still need loading; _done
        #: re-enters Edit once the world_models mesh is ready.
        self._edit_after_models = False
        self.panel = MapEditPanel(self)
        self.panel.changed.connect(self._on_panel_changed)
        self.panel.status.connect(self.status)
        self.panel.add_prop_requested.connect(self._add_prop_dialog)
        self.panel.setVisible(False)
        #: last prop-edit revision the models mesh was rebuilt for, so a prop edit refreshes
        #: the view live but an entity-only edit does not rebuild the whole mesh.
        self._last_prop_rev = 0
        #: last clip-edit revision the collision overlay was rebuilt for, so a clip add/remove
        #: refreshes the overlay without rebuilding the world mesh.
        self._last_collision_rev = 0
        self.split = QSplitter(Qt.Orientation.Horizontal, self)
        self.split.addWidget(holder)
        self.split.addWidget(self.panel)
        self.split.setStretchFactor(0, 1)
        self.split.setStretchFactor(1, 0)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(bar)
        lay.addWidget(self.split, 1)

        self._frame_action = QAction("Frame focus", self)
        self._frame_action.setShortcut(QKeySequence("F"))
        self._frame_action.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self._frame_action.triggered.connect(lambda: self.canvas.frame_focus())
        self._reset_action = QAction("Frame all", self)
        self._reset_action.setShortcut(QKeySequence("Home"))
        self._reset_action.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self._reset_action.triggered.connect(lambda: self.canvas.frame_scene())
        self.addAction(self._frame_action)
        self.addAction(self._reset_action)
        self._modes = False
        self.mode.setVisible(False)
        self._show_message("Select a gfx_map, col_map or xmodel to see its geometry.")
        theme.on_change(lambda _t: self.canvas.invalidate(), self)

    def _toggle(self, text: str, on: bool, slot) -> QToolButton:
        b = QToolButton(self)
        b.setText(text)
        b.setCheckable(True)
        b.setChecked(on)
        b.toggled.connect(slot)
        return b

    def _set_shaded(self, on: bool) -> None:
        self._shaded_user_set = True  # a hand toggle; keep it over the GPU-or-not default
        if on and not self.canvas.gl_available():
            self.shaded_button.blockSignals(True)
            self.shaded_button.setChecked(False)
            self.shaded_button.blockSignals(False)
            self.status.emit("Shaded view needs OpenGL, which is not available here")
            return
        self.depth_button.setEnabled(not on)
        self.grid_button.setEnabled(not on)
        if on and self.mesh is not None and self.canvas._scene is None:
            self._build_scene()
        self.canvas.set_shaded(on)

    def _apply_shaded_default(self, sync: bool = False) -> None:
        """On a fresh mesh, default to the GPU shaded view when OpenGL can draw here and to the
        software wireframe when it cannot, so the viewer uses the GPU by default without the
        user having to ask. A hand toggle (``_shaded_user_set``) is respected instead. Keyed off
        ``canvas.gl_available()``, which is false on a headless machine, so this is unit-testable
        without a GPU and the software path stays the default there and in screenshots."""
        if self._shaded_user_set:
            want = self.shaded_button.isChecked()
        else:
            want = self.canvas.gl_available()
        self.shaded_button.blockSignals(True)
        self.shaded_button.setChecked(want)
        self.shaded_button.blockSignals(False)
        self.depth_button.setEnabled(not want)
        self.grid_button.setEnabled(not want)
        if want and self.mesh is not None and self.canvas._scene is None:
            self._build_scene_sync() if sync else self._build_scene()
        self.canvas.set_shaded(want)

    def _build_scene(self) -> None:
        if self.doc is None or self.mesh is None:
            return
        self.status.emit("Decoding textures ...")
        job = _SceneJob(self._generation, self.doc, self.mesh)
        job.signals.done.connect(self._scene_done)
        job.signals.failed.connect(self._scene_failed)
        self._jobs.add(job.signals)
        QThreadPool.globalInstance().start(job)

    def _scene_done(self, generation: int, payload) -> None:
        if generation != self._generation:
            return
        scene, tex_count = payload
        self.canvas.set_scene(scene, tex_count)
        self.status.emit(f"Shaded: {tex_count:,} textures")

    def _scene_failed(self, generation: int, message: str) -> None:
        if generation != self._generation:
            return
        self.shaded_button.blockSignals(True)
        self.shaded_button.setChecked(False)
        self.shaded_button.blockSignals(False)
        self.canvas.set_shaded(False)
        self.status.emit(f"Shaded view unavailable: {message}")

    def _build_scene_sync(self) -> None:
        if self.doc is None or self.mesh is None:
            return
        from opent5.gui import geometry, glrender

        tri_tex, textures = geometry.textures(self.doc, self.mesh)
        scene = glrender.build_scene(
            self.mesh.positions, self.mesh.triangles, self.mesh.normals,
            self.mesh.uvs, tri_tex, textures,
        )  # fmt: skip
        self.canvas.set_scene(scene, len(textures))

    def _set_depth(self, on: bool) -> None:
        self.canvas.renderer.depth_cue = on
        self.canvas.invalidate()

    def _set_grid(self, on: bool) -> None:
        self.canvas.renderer.grid = on
        self.canvas.invalidate()

    def _set_models(self, on: bool) -> None:
        if self.doc is None or self.mesh_kind not in ("world", "world_models"):
            return
        self._start(self.doc, "world_models" if on else "world", self.ref)

    def _pick_preset(self, name: str) -> None:
        self.canvas.set_preset(name)
        self.canvas.setFocus()  # so 1-4 and WASD reach the canvas, not the button just clicked

    def _set_fly(self, on: bool) -> None:
        self.canvas.set_fly(on)
        self.canvas.setFocus()  # so WASD reaches the canvas, not the button just clicked

    def _on_fly_changed(self, on: bool) -> None:
        """Keep the Fly button in step when fly is toggled from the canvas (Space, or a
        right-button hold ending, or a preset clearing it)."""
        if self.fly_button.isChecked() != on:
            self.fly_button.blockSignals(True)
            self.fly_button.setChecked(on)
            self.fly_button.blockSignals(False)

    # -- collision overlay (the clip-brush invisible walls) ----------------------------------

    def _collision_families(self) -> tuple:
        return COLLISION_FAMILIES[self.collision_family.currentIndex()][1]

    def _set_show_collision(self, on: bool) -> None:
        self.collision_family.setVisible(on)
        self.canvas.set_show_collision(on)
        if on:
            self._refresh_collision_overlay()
        else:
            self.canvas.renderer.clear_collision_mesh()
        self.canvas.invalidate()
        self.canvas.setFocus()

    def _set_collision_family(self, _index: int) -> None:
        if self._edit_controller is not None:
            self._edit_controller.collision_families = self._collision_families()
        if self.collision_button.isChecked():
            self._refresh_collision_overlay()

    def _refresh_collision_overlay(self, drop: bool = False) -> None:
        """Build (or rebuild) the clip-brush overlay mesh and hand it to the renderer. ``drop``
        clears the cached mesh first, for after a clip edit; a plain toggle reuses the cache.
        This never touches the world+models mesh, so showing or editing collision does not
        trigger a world-mesh rebuild."""
        if self.doc is None:
            return
        from opent5.gui import geometry

        fam = self._collision_families()
        if self._edit_controller is not None:
            self._edit_controller.collision_families = fam
        if drop:
            geometry.drop_collision_overlay(self.doc)
        try:
            mesh = geometry.collision_overlay_mesh(self.doc, fam)
        except (EditError, ValueError, KeyError, IndexError, TypeError) as exc:
            self.status.emit(f"Could not build the collision overlay: {exc}")
            return
        if mesh is None:
            self.canvas.renderer.clear_collision_mesh()
            self.status.emit("This zone has no clipMap collision to show.")
            return
        self.canvas.renderer.set_collision_mesh(mesh.positions, mesh.triangles)
        self.canvas.invalidate()

    def _maybe_refresh_collision(self) -> None:
        ctl = self._edit_controller
        if ctl is None or not self.collision_button.isChecked():
            return
        rev = getattr(ctl, "_collision_rev", 0)
        if rev == self._last_collision_rev:
            return
        self._last_collision_rev = rev
        self._refresh_collision_overlay(drop=True)

    # -- in-place editing --------------------------------------------------------------------

    def _can_edit(self) -> bool:
        return (
            self.doc is not None
            and getattr(self.doc, "backend", None) == "edit"
            and self.mesh_kind in ("world", "world_models", "collision")
        )

    def _ensure_session(self):
        if self._session is not None:
            return self._session
        from opent5.edit import EditError
        from opent5.edit.mapedit import EditSession

        try:
            self._session = EditSession(self.doc.impl)
        except EditError as exc:
            self.status.emit(f"Cannot edit this map: {exc}")
            return None
        return self._session

    def _set_edit(self, on: bool) -> None:
        if on and not self._can_edit():
            self.edit_button.blockSignals(True)
            self.edit_button.setChecked(False)
            self.edit_button.blockSignals(False)
            self.status.emit("This zone cannot be edited in place (needs the edit backend).")
            return
        if on:
            # Props are only pickable when drawn, so bring the static models on with Edit.
            # The toggle reloads the mesh (world -> world_models) off the GUI thread, so defer
            # entering Edit until that mesh is ready (handled in _done).
            if self.models_button.isVisible() and not self.models_button.isChecked():
                self._edit_after_models = True
                self.models_button.setChecked(True)
                return
            session = self._ensure_session()
            if session is None:
                self.edit_button.blockSignals(True)
                self.edit_button.setChecked(False)
                self.edit_button.blockSignals(False)
                return
            self._edit_controller = MapEditController(session)
            self._last_prop_rev = self._edit_controller._prop_rev
            self._last_collision_rev = self._edit_controller._collision_rev
            self._edit_controller.show_collision = self.collision_button.isChecked()
            self._edit_controller.collision_families = self._collision_families()
            self._edit_controller.props_shown = self.models_button.isChecked()
            # start on Props so the view is not buried under the entity dots; the panel's Pick
            # control switches to Entities or All.
            self._edit_controller.pick_filter = MapEditController.PICK_PROPS
            self.canvas.set_controller(self._edit_controller)
            self.panel.set_controller(self._edit_controller, on_save=self._save_edited)
            self.panel.set_pick_filter(MapEditController.PICK_PROPS)
            self.panel.setVisible(True)
            self.status.emit(
                f"Editing: {len(session.static_models())} props, {len(session.markers())} "
                "entities. Click a prop; G move, R rotate, B add a crate, Delete remove. "
                "Use Pick to show entities."
            )
        else:
            self.canvas.set_controller(None)
            self.panel.setVisible(False)
        self.canvas.invalidate()

    def _teardown_edit(self) -> None:
        if self.edit_button.isChecked():
            self.edit_button.blockSignals(True)
            self.edit_button.setChecked(False)
            self.edit_button.blockSignals(False)
        self.canvas.set_controller(None)
        self.panel.set_controller(None)
        self.panel.setVisible(False)
        self._edit_controller = None
        self._session = None
        self._last_prop_rev = 0
        self._last_collision_rev = 0
        if self.collision_button.isChecked():
            self.collision_button.blockSignals(True)
            self.collision_button.setChecked(False)
            self.collision_button.blockSignals(False)
        self.collision_family.setVisible(False)
        self.canvas.renderer.clear_collision_mesh()
        self.canvas.renderer.show_collision = False

    def _on_pick(self, obj_id) -> None:
        self.panel.show_object(obj_id)

    def _on_edited(self) -> None:
        if self._edit_controller is not None:
            self.panel.show_object(self._edit_controller.selected_id)
        self._maybe_refresh_models()
        self._maybe_refresh_collision()
        self.canvas.invalidate()

    def _on_panel_changed(self) -> None:
        self._maybe_refresh_models()
        self._maybe_refresh_collision()
        self.canvas.invalidate()

    def _maybe_refresh_models(self) -> None:
        """Rebuild the placed-model mesh after a prop edit (not an entity-only edit), so the
        view reflects a moved, added or deleted prop without reopening."""
        ctl = self._edit_controller
        if ctl is None:
            return
        rev = getattr(ctl, "_prop_rev", 0)
        if rev == self._last_prop_rev:
            return
        self._last_prop_rev = rev
        self._refresh_models()

    def _refresh_models(self) -> None:
        """Rebuild the world+models mesh from the edited static-model placements, keeping the
        camera and the edit session. Only the placed models change, so the plain world mesh is
        reused from the cache."""
        if self.doc is None or self.mesh_kind not in ("world", "world_models"):
            return
        if not self.models_button.isChecked():
            return
        from opent5.gui import geometry

        # A move, rotate or rescale leaves the mesh topology intact (the same props, the same
        # triangles), so restage rewrites only the moved props' vertices and the view refreshes
        # without rebuilding the whole world+models mesh or its wireframe edges. An add, delete
        # or model swap changes which props draw, so it falls back to the full rebuild.
        incremental = geometry.restage_static_models(self.doc)
        if incremental is not None:
            self.mesh = incremental
            self.canvas.update_mesh_positions(self.mesh)
            if self.shaded_button.isChecked() and not self._sync:
                # Only the moved props' vertices changed, so patch those rows of the uploaded GPU
                # buffer rather than re-decoding and re-uploading the whole scene. Falls back to
                # the full rebuild when there is no uploaded scene yet to patch.
                ranges = geometry.restage_changed_ranges(self.doc)
                if not self.canvas.update_scene_vertices(self.mesh, ranges):
                    self.canvas._scene = None
                    self._build_scene()
        else:
            geometry.drop_static_models(self.doc)
            try:
                self.mesh = self.doc.mesh("world_models", self.ref)
            except (EditError, ValueError, KeyError, IndexError, TypeError) as exc:
                self.status.emit(f"Could not refresh the models view: {exc}")
                return
            self._apply_mode()
            if self.shaded_button.isChecked() and not self._sync:
                self.canvas._scene = None
                self._build_scene()

    def _add_prop_dialog(self) -> None:
        """Pick a placeable model and place it at the view target. Offers only models the zone
        can draw a copy of (those with a draw instance to clone)."""
        ctl = self._edit_controller
        if ctl is None or not ctl.can_edit_clips:
            self.status.emit("This zone has no editable clipMap, so a prop cannot be added.")
            return
        models = ctl.placeable_models()
        if not models:
            self.status.emit("This zone carries no placeable static-model prop to copy.")
            return
        model, ok = QInputDialog.getItem(self, "Add prop", "Model to place:", models, 0, False)
        if ok and model:
            self.canvas.add_prop_chosen(model)

    def _save_edited(self, path) -> None:
        if self._session is None:
            return
        from opent5.edit import EditError

        try:
            report = self._session.save(path)
        except EditError as exc:
            self.status.emit(f"Not saved: {exc}")
            return
        if report.verified:
            self.status.emit(f"Saved {report.path.name} (verified, {report.bytes:,} bytes)")
        else:
            self.status.emit(f"Saved {report.path.name} but verify failed: {report.problems[:1]}")

    def _world_kind(self, kind: str | None) -> str | None:
        if kind == "world" and self.models_button.isChecked():
            return "world_models"
        return kind

    def _show_message(self, text: str) -> None:
        self.placeholder.setText(text)
        self.stack.setCurrentWidget(self.placeholder)

    def view_actions(self) -> list[QAction]:
        return [self._frame_action, self._reset_action]

    # loading
    @staticmethod
    def kind_of(ref: Ref) -> str | None:
        return KIND_OF_TYPE.get(int(ref.type))

    def load(self, doc: ZoneDoc, ref: Ref) -> None:
        super().load(doc, ref)
        kind = self.kind_of(ref)
        if kind is None:
            self._show_message(f"{ref.type_name} assets have no geometry.")
            return
        self._start(doc, self._world_kind(kind), ref)

    def load_kind(self, doc: ZoneDoc, kind: str) -> None:
        """World geometry or collision of the zone, without a selected asset."""
        self.doc, self.ref = doc, None
        self._start(doc, self._world_kind(kind), None)

    def _start(self, doc: ZoneDoc, kind: str, ref: Ref | None) -> None:
        self._generation += 1
        self.mesh_kind = kind
        self.models_button.setVisible(kind in ("world", "world_models"))
        self._teardown_edit()
        self.edit_button.setVisible(self._can_edit())
        self.collision_button.setVisible(self._can_edit())
        self.canvas.edit_available = self._can_edit()
        label = ref.label if ref is not None else KIND_TITLES.get(kind, kind)
        self._show_message(f"Building mesh for {label} ...")
        job = _MeshJob(self._generation, doc, kind, ref)
        job.signals.done.connect(self._done)
        job.signals.failed.connect(self._failed)
        self._jobs.add(job.signals)
        QThreadPool.globalInstance().start(job)

    def load_sync(self, doc: ZoneDoc, what) -> None:
        """Build the mesh in this thread (tests, screenshots). ``what`` is a Ref or a kind."""
        self._generation += 1
        if isinstance(what, Ref):
            self.doc, self.ref = doc, what
            kind = self.kind_of(what)
            ref = what
        else:
            self.doc, self.ref = doc, None
            kind, ref = what, None
        kind = self._world_kind(kind)
        self.mesh_kind = kind
        self.models_button.setVisible(kind in ("world", "world_models"))
        self._teardown_edit()
        self.edit_button.setVisible(self._can_edit())
        self.collision_button.setVisible(self._can_edit())
        self.canvas.edit_available = self._can_edit()
        self._sync = True
        try:
            mesh = doc.mesh(kind, ref)
        except (EditError, ValueError, KeyError, IndexError, TypeError) as exc:
            self._failed(self._generation, str(exc))
            self._sync = False
            return
        self._done(self._generation, mesh)
        self._sync = False

    def _done(self, generation: int, mesh: MeshData) -> None:
        if generation != self._generation:
            return
        self.mesh = mesh
        self.mode.blockSignals(True)
        self.mode.setCurrentIndex(0)
        self.mode.blockSignals(False)
        self._modes = self.mesh_kind == "collision" and mesh.groups is not None
        self.mode.setVisible(self._modes)
        self.info.setText("  ".join(mesh.notes))
        self._apply_mode()
        self.stack.setCurrentWidget(self.canvas)
        self.canvas.reset_camera()
        self._apply_shaded_default(sync=self._sync)
        if self._edit_after_models:
            # Edit was requested before the static models were loaded; now that they are,
            # turn Edit on (static models are drawn, so props are pickable).
            self._edit_after_models = False
            if self._can_edit() and not self.edit_button.isChecked():
                self.edit_button.setChecked(True)  # fires _set_edit(True), models now on

    def _failed(self, generation: int, message: str) -> None:
        if generation != self._generation:
            return
        self.mesh = None
        self._show_message(f"No geometry: {message}")

    def _apply_mode(self, *_args) -> None:
        m = self.mesh
        if m is None:
            return
        tris = m.triangles
        if self._modes and m.groups is not None:
            choice = self.mode.currentIndex()
            if choice == 1:
                tris = tris[m.groups >= 0]
            elif choice == 2:
                tris = tris[m.groups < 0]
        title = m.label or KIND_TITLES.get(self.mesh_kind or "", "")
        self.canvas.set_mesh(m.positions, tris, title)

    def _camera_status(self) -> None:
        c = self.canvas.camera
        tx, ty, tz = (float(v) for v in c.target)
        self.status.emit(
            f"yaw {math.degrees(c.yaw) % 360:.0f}  pitch {math.degrees(c.pitch):.0f}  "
            f"distance {c.distance:,.0f}  target {tx:.0f} {ty:.0f} {tz:.0f}"
        )
