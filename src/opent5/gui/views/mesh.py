"""Wireframe view of world geometry, collision and models.

No OpenGL: every frame is projected with numpy and the edges are rasterised
into a float buffer (points sampled along each edge, one per pixel of length,
summed with ``np.bincount``), then tone-mapped into a QImage with the theme's
``wire`` / ``wire_far`` / ``base`` tokens. That runs the same offscreen and on
any GPU. Edge density shades itself: busy areas read brighter, as in a
wireframe drawn with additive blending. While the mouse drags, a fixed random
subset of the edges is drawn; the full set comes back on release.

Mouse: left drag orbits, middle drag pans, the wheel zooms to the cursor. Hold
the right button to fly (mouse-look, WASD to move, Q/E down/up, Shift faster, the
wheel sets the fly speed). Double click a surface to focus it, F frames the
focus, Home frames the whole scene.
"""

from __future__ import annotations

import math
import time

import numpy as np
from PySide6.QtCore import QObject, QPointF, QRunnable, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QImage, QKeySequence, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
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
KIND_OF_TYPE = {T.GFX_MAP: "world", T.COL_MAP_MP: "collision", T.COL_MAP_SP: "collision",
                T.XMODEL: "model"}  # fmt: skip
KIND_TITLES = {
    "world": "World geometry",
    "world_models": "World geometry",
    "collision": "Collision",
    "model": "Model",
}
COLLISION_MODES = ("Brushes and triangles", "Brushes", "Triangles")
HELP_LINES = (
    "LMB orbit   MMB pan   wheel zoom to cursor",
    "hold RMB to fly: WASD move, Q/E down/up, Shift faster, wheel sets speed",
    "double click to focus   F frame focus   Home frame all   H hide help",
)
#: Prepended to the hint when the view can be edited but Edit mode is off.
EDIT_OFF_HINT = ("Edit is OFF: click Edit (top right) to select and move props",)
#: Prepended to the hint when Edit mode is on.
EDIT_ON_HELP = (
    "EDIT ON: click a prop to select, then drag a gizmo axis to move",
    "G move   R rotate   B add crate   Del remove   Ctrl+D duplicate   Esc deselect",
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


class Camera:
    """Orbit camera, Z up: yaw around Z, pitch above the horizon, distance from target."""

    def __init__(self):
        self.target = np.zeros(3)
        self.yaw = math.radians(35.0)
        self.pitch = math.radians(30.0)
        self.distance = 512.0

    def basis(self):
        cp, sp = math.cos(self.pitch), math.sin(self.pitch)
        cy, sy = math.cos(self.yaw), math.sin(self.yaw)
        forward = np.array([-cp * cy, -cp * sy, -sp])
        right = np.array([-sy, cy, 0.0])
        up = np.cross(right, forward)
        eye = self.target - forward * self.distance
        return eye, right, up, forward

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
    f = np.float32(focal(h))
    z = cs[:, 2]
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
    """World-space ray (origin at the eye, unit direction) through a screen pixel.

    The pixel is in the same coordinates the render uses; the direction does not
    depend on the device-pixel ratio, so logical widget coordinates are fine."""
    eye, right, up, forward = cam.basis()
    f = focal(h)
    dx = (px - w / 2.0) / f
    dy = (h / 2.0 - py) / f
    d = right * dx + up * dy + forward
    return eye, d / np.linalg.norm(d)


def dolly_to_ray(eye, forward, distance: float, ray_dir, factor: float, min_dist: float):
    """Zoom towards (or away from) the point under the cursor.

    ``factor`` is the new pivot distance as a fraction of the old (``< 1`` zooms
    in). The eye slides along ``ray_dir`` so the world point under the cursor
    keeps its place on screen. The pivot distance is floored at ``min_dist``: once
    there, zooming in keeps advancing the eye, so the pivot is pushed forward along
    the view direction and the camera passes through rather than stalling.
    Returns ``(new_target, new_distance)``."""
    cosang = float(ray_dir @ forward)
    if cosang <= 1e-4:  # cursor ray almost perpendicular to the view: dolly straight ahead
        ray_dir, cosang = forward, 1.0
    t = distance / cosang
    new_eye = eye + ray_dir * (t * (1.0 - factor))
    new_distance = max(distance * factor, min_dist)
    return new_eye + forward * new_distance, new_distance


def pan_delta(right, up, distance: float, focal_px: float, dx: float, dy: float):
    """World-space pivot shift for a drag of ``(dx, dy)`` pixels. Scaled by the
    pivot depth and the focal length, so the same drag moves the pivot the same
    apparent amount at any distance."""
    scale = distance / max(focal_px, 1.0)
    return -right * (dx * scale) + up * (dy * scale)


def frame_distance(radius: float, fov_deg: float, aspect: float) -> float:
    """Pivot distance that fits a sphere of ``radius`` comfortably in the view."""
    half = math.radians(fov_deg) / 2.0
    if aspect < 1.0:  # a tall, narrow viewport is limited by its width
        half = math.atan(math.tan(half) * aspect)
    return max(radius, 1.0) / math.sin(half) * 0.85


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
        return result

    # selection and projection
    def markers(self) -> list:
        return self.session.markers()

    def marker_screen(self, cam: Camera, w: int, h: int):
        from opent5.edit import gizmo as gz

        eye, right, up, forward = cam.basis()
        f = focal(h)
        ids, pts, depths = [], [], []
        for o in self.markers():
            sx, sy, depth, front = gz.project_point(o.origin, eye, right, up, forward, f, w, h)
            ids.append(o.id)
            pts.append((sx, sy) if front else (np.nan, np.nan))
            depths.append(depth if front else np.inf)
        return ids, np.array(pts, np.float64).reshape(-1, 2), np.array(depths, np.float64)

    def prop_screen(self, cam: Camera, w: int, h: int):
        """Static-model prop origins projected to the screen, for picking and markers:
        ``(indices, (n, 2) positions, depths)``."""
        from opent5.edit import gizmo as gz

        eye, right, up, forward = cam.basis()
        f = focal(h)
        idxs, pts, depths = [], [], []
        for p in self.props():
            sx, sy, depth, front = gz.project_point(p.origin, eye, right, up, forward, f, w, h)
            idxs.append(p.index)
            pts.append((sx, sy) if front else (np.nan, np.nan))
            depths.append(depth if front else np.inf)
        return idxs, np.array(pts, np.float64).reshape(-1, 2), np.array(depths, np.float64)

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
        if prop_i is not None and prop_t <= ent_dist:
            self.selected_prop = prop_i
            self.selected_id = None
            return None
        self.selected_prop = None
        self.selected_id = ent_id
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
        csx, csy, cdepth, cfront = gz.project_point(centre, eye, right, up, forward, f, w, h)
        if not cfront:
            return None, []
        world_len = max(cdepth, 1.0) * length_px / f
        handles = []
        for i, axis in enumerate(EDIT_AXES):
            sx, sy, _d, front = gz.project_point(
                centre + axis * world_len, eye, right, up, forward, f, w, h
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
                self.move_selected_prop(tuple(float(v) for v in preview[1]))
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
        return self.selected_id

    def duplicate(self) -> int | None:
        if self.selected_id is None:
            return None
        self.selected_id = self.session.duplicate_object(self.selected_id)
        return self.selected_id

    def delete(self) -> None:
        if self.selected_id is not None:
            self.session.delete_object(self.selected_id)
            self.selected_id = None

    def set_property(self, key: str, value: str | None) -> None:
        if self.selected_id is not None:
            self.session.set_property(self.selected_id, key, value)

    def undo(self) -> None:
        self.session.undo()
        self._props_cache = None
        self._prop_rev += 1  # an undo may restore a prop's placement; refresh the mesh
        if self.selected() is None:
            self.selected_id = None

    def redo(self) -> None:
        self.session.redo()
        self._props_cache = None
        self._prop_rev += 1


class Renderer:
    """Holds a mesh's edges and renders frames into QImages."""

    def __init__(self):
        self.points = np.zeros((0, 3), np.float32)
        self.edges = np.zeros((0, 2), np.int64)
        self.tris = np.zeros((0, 3), np.int64)  # kept for cursor picking
        self.drag_edges = self.edges
        self.grid_points = np.zeros((0, 3), np.float32)
        self.grid_edges = np.zeros((0, 2), np.int64)
        self.depth_cue = True
        self.grid = True
        self.bounds = (np.zeros(3), np.zeros(3))
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
    #: An entity was picked (its id, or None); the view updates the property panel.
    selection_changed = Signal(object)
    #: An edit went through the session (move, rotate, add, delete, ...).
    edited = Signal()
    #: A short message for the status bar (e.g. why an add could not be placed).
    status = Signal(str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
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
        self._focus = None  # last point picked by a double click, or None
        self._settle = QTimer(self)
        self._settle.setSingleShot(True)
        self._settle.setInterval(120)
        self._settle.timeout.connect(self._end_drag)
        # fly mode: held while the right button is down
        self._flying = False
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

    def set_shaded(self, on: bool) -> None:
        self.shaded = on
        self.invalidate()

    def gl_available(self) -> bool:
        if self._gl_failed:
            return False
        if self._gl is None:
            from opent5.gui.glrender import ShadedRenderer

            self._gl = ShadedRenderer()
        if not self._gl.available():
            self._gl_failed = True
            return False
        return True

    def set_scene(self, scene, tex_count: int) -> None:
        """A prepared shaded scene (vertices, index runs and decoded textures)."""
        self._scene = scene
        self._scene_uploaded = False
        self._tex_count = tex_count
        self.invalidate()

    def invalidate(self) -> None:
        self._key = None
        self.update()

    def _scene_diag(self) -> float:
        lo, hi = self.renderer.bounds
        return float(np.linalg.norm(hi - lo))

    def frame(self, lo, hi) -> None:
        """Reposition the camera so the bounds ``(lo, hi)`` fill the view, without
        changing the view angles."""
        lo, hi = np.asarray(lo, np.float64), np.asarray(hi, np.float64)
        radius = float(np.linalg.norm(hi - lo)) / 2.0
        aspect = max(self.width(), 1) / max(self.height(), 1)
        self.camera.target = (lo + hi) / 2.0
        self.camera.distance = frame_distance(radius, FOV_DEG, aspect)
        self.invalidate()
        self.camera_changed.emit()

    def frame_focus(self) -> None:
        """Frame the focused point (set by a double click) or, failing that, the
        dense core of the mesh."""
        if self._focus is not None:
            half = max(self._scene_diag() * 0.06, 8.0)
            self.frame(self._focus - half, self._focus + half)
        else:
            self.frame(*self.renderer.focus_bounds)

    def frame_scene(self) -> None:
        """Frame the whole mesh."""
        self.frame(*self.renderer.bounds)

    def frame_all(self, focus: bool = False) -> None:
        lo, hi = self.renderer.focus_bounds if focus else self.renderer.frame_bounds
        self.frame(lo, hi)

    def reset_camera(self) -> None:
        self.camera.yaw = math.radians(35.0)
        self.camera.pitch = math.radians(30.0)
        self._focus = None
        self.frame_all(focus=True)

    def _shaded_ready(self) -> bool:
        return self.shaded and self._scene is not None and self.gl_available()

    def render_image(self, fast: bool = False) -> QImage:
        ratio = self.devicePixelRatioF()
        w, h = max(1, int(self.width() * ratio)), max(1, int(self.height() * ratio))
        c = self.camera
        shaded = self._shaded_ready()
        key = (w, h, fast, c.yaw, c.pitch, c.distance, tuple(c.target), self.renderer.depth_cue,
               self.renderer.grid, theme.current().name, shaded)  # fmt: skip
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
        return self._gl.render(eye, self.camera.target, up, w, h, bg, near, far)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        t = theme.current()
        if not len(self.renderer.edges):
            p.fillRect(self.rect(), QColor(t.base))
            p.end()
            return
        p.drawImage(0, 0, self.render_image(self._dragging))
        p.setFont(theme.mono_font(8))
        p.setPen(QColor(t.text_dim))
        verts, tris, edges = self.counts
        shaded = self._shaded_ready()
        if shaded:
            third = f"shaded  {self._tex_count:,} textures"
        else:
            third = f"{self.renderer.last_edges:,} edges drawn  {self.renderer.last_ms:.0f} ms" + (
                "  (preview)" if self._dragging else ""
            )
        lines = [
            self.title,
            f"{verts:,} vertices  {tris:,} triangles  {edges:,} edges",
            third,
        ]
        fm = p.fontMetrics()
        back = QColor(t.base)
        back.setAlpha(210)
        width = max(fm.horizontalAdvance(line) for line in lines) + 12
        p.fillRect(2, 2, width, fm.height() * len(lines) + 8, back)
        y = 6 + fm.ascent()
        for line in lines:
            p.drawText(8, y, line)
            y += fm.height()
        help_top = self._paint_help(p) if self.show_help else self.height()
        if self.renderer.grid and not shaded:
            step = self.renderer.grid_step
            p.setFont(theme.mono_font(8))
            p.setPen(QColor(t.text_dim))
            p.drawText(8, help_top - 6, f"grid {step:g} units")
        self._paint_gizmo(p)
        if self.controller is not None:
            self._paint_edit(p)
        p.end()

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

    def _paint_gizmo(self, p: QPainter) -> None:
        t = theme.current()
        _eye, right, up, _fwd = self.camera.basis()
        cx, cy, r = self.width() - 34, self.height() - 34, 20
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setFont(theme.mono_font(7))
        axes = (("X", np.array([1.0, 0, 0])), ("Y", np.array([0, 1.0, 0])),
                ("Z", np.array([0, 0, 1.0])))  # fmt: skip
        for name, v in axes:
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
            ids, pts, _depths = ctl.marker_screen(self.camera, w, h)
            for obj in ctl.markers():
                try:
                    i = ids.index(obj.id)
                except ValueError:
                    continue
                sx, sy = pts[i]
                if np.isnan(sx):
                    continue
                selected = obj.id == ctl.selected_id
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
        self._paint_gizmo_handles(p)
        self._paint_edit_readout(p)

    def _paint_prop_markers(self, p: QPainter) -> None:
        """Static-model props as small diamonds; the selected one brighter."""
        ctl = self.controller
        # Props are drawn (and clickable) only when the filter includes them and they are on
        # screen; the selected prop is always drawn so it stays visible under any filter.
        if not ctl.props_pickable and ctl.selected_prop is None:
            return
        t = theme.current()
        from opent5.edit import gizmo as gz

        eye, right, up, forward = self.camera.basis()
        f = focal(self.height())
        for prop in ctl.props():
            selected = prop.index == ctl.selected_prop
            if not selected and not ctl.props_pickable:
                continue
            origin = prop.origin
            if selected:
                centre = ctl.gizmo_centre()  # follows a live translate preview
                if centre is not None:
                    origin = centre
            sx, sy, _d, front = gz.project_point(
                origin, eye, right, up, forward, f, self.width(), self.height()
            )
            if not front:
                continue
            # props stand out from the entity dots: a brighter, larger diamond, not a square.
            colour = QColor("#c88bd8" if selected else "#8e7fa0")
            p.setPen(QPen(QColor(t.text) if selected else colour, 1.5 if selected else 1.2))
            p.setBrush(colour)
            r = 6 if selected else 4
            # QPainter.drawPolygon takes a single QPolygonF, not loose points: passing four
            # QPointF args raises inside paintEvent, which unwinds through Qt's C++ paint
            # callback and crashes the windowed app on the first repaint with props drawn.
            p.drawPolygon(
                QPolygonF(
                    [
                        QPointF(sx, sy - r),
                        QPointF(sx + r, sy),
                        QPointF(sx, sy + r),
                        QPointF(sx - r, sy),
                    ]
                )
            )

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
            scr = [gz.project_point(c, eye, right, up, forward, f, w, h) for c in corners]
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

    def _paint_edit_readout(self, p: QPainter) -> None:
        ctl = self.controller
        t = theme.current()
        obj = ctl.selected()
        bits = [f"edit: {ctl.mode}"]
        if obj is not None:
            bits.append(f"{obj.label}  {obj.keys.get('origin', '')}")
        if ctl.session.dirty:
            bits.append("edited")
        text = "    ".join(bits)
        p.setFont(theme.mono_font(8))
        p.setPen(QColor(t.text))
        fm = p.fontMetrics()
        x = self.width() - fm.horizontalAdvance(text) - 10
        p.drawText(max(x, 8), 16, text)

    # interaction
    def mousePressEvent(self, e) -> None:
        self._press_pos = e.position()
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
            self._start_fly()

    def mouseMoveEvent(self, e) -> None:
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
            return
        last, _buttons = self._drag
        d = e.position() - last
        self._drag = (e.position(), e.buttons())
        c = self.camera
        buttons = e.buttons()
        if buttons & Qt.MouseButton.LeftButton:
            c.yaw -= d.x() * 0.008
            c.pitch = max(-1.55, min(1.55, c.pitch + d.y() * 0.008))
        elif buttons & Qt.MouseButton.RightButton:  # fly: look around in place
            eye, _r, _u, _f = c.basis()
            c.yaw -= d.x() * 0.006
            c.pitch = max(-1.55, min(1.55, c.pitch + d.y() * 0.006))
            _e2, _r2, _u2, forward = c.basis()
            c.target = eye + forward * c.distance
        elif buttons & Qt.MouseButton.MiddleButton:  # pan
            _eye, right, up, _f = c.basis()
            c.target = c.target + pan_delta(
                right, up, c.distance, focal(max(self.height(), 1)), d.x(), d.y()
            )
        else:
            return
        self._dragging = True
        self._settle.start()
        self.invalidate()
        self.camera_changed.emit()

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
            self._stop_fly()
        self._drag = None
        self._settle.start(10)

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

    def _start_fly(self) -> None:
        self._flying = True
        self._fly_last = time.perf_counter()
        self._fly_timer.start()

    def _stop_fly(self) -> None:
        self._flying = False
        self._fly_timer.stop()
        self._fly_keys.clear()
        self._dragging = False
        self.invalidate()

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
        if self._flying:  # the wheel sets the fly speed, not the zoom
            self.fly_speed = max(1.0, self.fly_speed * (1.25**steps))
            self.camera_changed.emit()
            return
        eye, d = cursor_ray(self.camera, e.position().x(), e.position().y(),
                            self.width(), self.height())  # fmt: skip
        _e2, _r, _u, forward = self.camera.basis()
        min_dist = max(self._scene_diag() * 5e-4, 1.0)
        target, dist = dolly_to_ray(eye, forward, self.camera.distance, d, 0.8**steps, min_dist)
        self.camera.target, self.camera.distance = target, dist
        self._dragging = True
        self._settle.start()
        self.invalidate()
        self.camera_changed.emit()

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
        elif k in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace) and ctl.selected_prop is not None:
            ctl.delete_selected_prop_clip()  # remove the prop's clip cluster (collision)
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
        self.mode = QComboBox(bar)
        self.mode.addItems(COLLISION_MODES)
        self.mode.setToolTip("Which collision geometry to draw")
        self.mode.currentIndexChanged.connect(self._apply_mode)
        self.frame_button = QToolButton(bar)
        self.frame_button.setText("Frame all")
        self.frame_button.setToolTip("Frame the whole scene (Home)")
        self.frame_button.clicked.connect(lambda: self.canvas.frame_scene())
        self.info = QLabel("", bar)
        self.info.setObjectName("AssetMeta")
        for w in (self.shaded_button, self.depth_button, self.grid_button,
                  self.models_button, self.edit_button, self.mode):  # fmt: skip
            row.addWidget(w)
        row.addStretch(1)
        row.addWidget(self.info)
        row.addWidget(self.frame_button)
        self.bar = bar

        self.canvas = MeshCanvas(self)
        self.canvas.camera_changed.connect(self._camera_status)
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

    def _on_pick(self, obj_id) -> None:
        self.panel.show_object(obj_id)

    def _on_edited(self) -> None:
        if self._edit_controller is not None:
            self.panel.show_object(self._edit_controller.selected_id)
        self._maybe_refresh_models()
        self.canvas.invalidate()

    def _on_panel_changed(self) -> None:
        self._maybe_refresh_models()
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
        self.canvas.edit_available = self._can_edit()
        self._sync = True
        try:
            mesh = doc.mesh(kind, ref)
        except (EditError, ValueError, KeyError, IndexError, TypeError) as exc:
            self._failed(self._generation, str(exc))
            self._sync = False
            return
        self._done(self._generation, mesh)
        if self.shaded_button.isChecked() and self.mesh is not None:
            self._build_scene_sync()
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
        if self.shaded_button.isChecked() and not self._sync:
            self._build_scene()
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
