"""Wireframe view of world geometry, collision and models.

No OpenGL: every frame is projected with numpy and the edges are rasterised
into a float buffer (points sampled along each edge, one per pixel of length,
summed with ``np.bincount``), then tone-mapped into a QImage with the theme's
``wire`` / ``wire_far`` / ``base`` tokens. That runs the same offscreen and on
any GPU. Edge density shades itself: busy areas read brighter, as in a
wireframe drawn with additive blending. While the mouse drags, a fixed random
subset of the edges is drawn; the full set comes back on release.

Mouse: left drag orbits, right or middle drag pans, the wheel zooms. F frames
everything, Home resets the camera.
"""

from __future__ import annotations

import math
import time

import numpy as np
from PySide6.QtCore import QObject, QPointF, QRunnable, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QImage, QKeySequence, QPainter, QPen
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
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


class Renderer:
    """Holds a mesh's edges and renders frames into QImages."""

    def __init__(self):
        self.points = np.zeros((0, 3), np.float32)
        self.edges = np.zeros((0, 2), np.int64)
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

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
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
        self._settle = QTimer(self)
        self._settle.setSingleShot(True)
        self._settle.setInterval(120)
        self._settle.timeout.connect(self._end_drag)

    def set_mesh(self, positions, triangles, title: str) -> None:
        self.renderer.set_mesh(positions, triangles)
        self.counts = (len(positions), len(triangles), len(self.renderer.edges))
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

    def frame_all(self, focus: bool = False) -> None:
        r = self.renderer
        lo, hi = r.focus_bounds if focus else r.frame_bounds
        centre = (lo + hi) / 2.0
        radius = max(float(np.linalg.norm(hi - lo)) / 2.0, 1.0)
        self.camera.target = centre
        aspect = max(self.width(), 1) / max(self.height(), 1)
        half = math.radians(FOV_DEG) / 2.0
        if aspect < 1.0:
            half = math.atan(math.tan(half) * aspect)
        self.camera.distance = radius / math.sin(half) * 0.85
        self.invalidate()
        self.camera_changed.emit()

    def reset_camera(self) -> None:
        self.camera.yaw = math.radians(35.0)
        self.camera.pitch = math.radians(30.0)
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
        if self.renderer.grid and not shaded:
            step = self.renderer.grid_step
            p.drawText(8, self.height() - 8, f"grid {step:g} units")
        self._paint_gizmo(p)
        p.end()

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

    # interaction
    def mousePressEvent(self, e) -> None:
        self._drag = (e.position(), e.buttons())
        self.setFocus()

    def mouseMoveEvent(self, e) -> None:
        if self._drag is None:
            return
        last, _buttons = self._drag
        d = e.position() - last
        self._drag = (e.position(), e.buttons())
        c = self.camera
        if e.buttons() & Qt.MouseButton.LeftButton:
            c.yaw -= d.x() * 0.008
            c.pitch = max(-1.55, min(1.55, c.pitch + d.y() * 0.008))
        elif e.buttons() & (Qt.MouseButton.RightButton | Qt.MouseButton.MiddleButton):
            _eye, right, up, _f = c.basis()
            scale = c.distance / focal(max(self.height(), 1))
            c.target = c.target - right * d.x() * scale + up * d.y() * scale
        else:
            return
        self._dragging = True
        self._settle.start()
        self.invalidate()
        self.camera_changed.emit()

    def mouseReleaseEvent(self, _e) -> None:
        self._drag = None
        self._settle.start(10)

    def _end_drag(self) -> None:
        if self._drag is None or not self._drag[1]:
            self._dragging = False
            self.invalidate()

    def wheelEvent(self, e) -> None:
        steps = e.angleDelta().y() / 120.0
        self.camera.distance = max(1.0, self.camera.distance * (0.85**steps))
        self._dragging = True
        self._settle.start()
        self.invalidate()
        self.camera_changed.emit()

    def keyPressEvent(self, e) -> None:
        if e.key() == Qt.Key.Key_F:
            self.frame_all()
        elif e.key() == Qt.Key.Key_Home:
            self.reset_camera()
        else:
            super().keyPressEvent(e)

    def resizeEvent(self, e) -> None:
        self.invalidate()
        super().resizeEvent(e)


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
        self.models_button = self._toggle("Static models", False, self._set_models)
        self.models_button.setToolTip("Place the world's static models (props)")
        self.models_button.setVisible(False)
        self.mode = QComboBox(bar)
        self.mode.addItems(COLLISION_MODES)
        self.mode.setToolTip("Which collision geometry to draw")
        self.mode.currentIndexChanged.connect(self._apply_mode)
        self.frame_button = QToolButton(bar)
        self.frame_button.setText("Frame all")
        self.frame_button.setToolTip("Frame all (F)")
        self.frame_button.clicked.connect(lambda: self.canvas.frame_all())
        self.info = QLabel("", bar)
        self.info.setObjectName("AssetMeta")
        for w in (self.shaded_button, self.depth_button, self.grid_button,
                  self.models_button, self.mode):  # fmt: skip
            row.addWidget(w)
        row.addStretch(1)
        row.addWidget(self.info)
        row.addWidget(self.frame_button)
        self.bar = bar

        self.canvas = MeshCanvas(self)
        self.canvas.camera_changed.connect(self._camera_status)
        self.placeholder = empty_state("", self)
        self.stack = QStackedLayout()
        holder = QWidget(self)
        holder.setLayout(self.stack)
        self.stack.addWidget(self.placeholder)
        self.stack.addWidget(self.canvas)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(bar)
        lay.addWidget(holder, 1)

        self._frame_action = QAction("Frame all", self)
        self._frame_action.setShortcut(QKeySequence("F"))
        self._frame_action.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self._frame_action.triggered.connect(lambda: self.canvas.frame_all())
        self._reset_action = QAction("Reset camera", self)
        self._reset_action.setShortcut(QKeySequence("Home"))
        self._reset_action.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self._reset_action.triggered.connect(lambda: self.canvas.reset_camera())
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
