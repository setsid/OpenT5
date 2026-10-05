"""The wireframe view: edge extraction, rasterising, and (with .env) mp_nuked's geometry."""

import math
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

import numpy as np
import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent, QWheelEvent
from PySide6.QtWidgets import QApplication

from opent5.edit import gizmo as gz
from opent5.gui import theme
from opent5.gui.backend import MeshData
from opent5.gui.views import mesh as mv

APP = QApplication.instance() or QApplication([])
theme.apply("dark")

CUBE_POS = np.array([[x, y, z] for z in (0, 64) for y in (0, 64) for x in (0, 64)], np.float32)
CUBE_QUADS = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
CUBE_TRIS = np.array([t for a, b, c, d in CUBE_QUADS for t in ((a, b, c), (a, c, d))])


def test_unique_edges_cube():
    edges = mv.unique_edges(CUBE_TRIS)
    assert len(edges) == 12 + 6  # cube edges plus one diagonal per face
    assert len({tuple(e) for e in edges.tolist()}) == len(edges)
    assert (edges[:, 0] < edges[:, 1]).all()


def test_rasterise_horizontal_line():
    acc, _ = mv.rasterise(
        np.array([2.0]), np.array([5.0]), np.array([12.0]), np.array([5.0]),
        np.ones(1, np.float32), 20, 10,
    )  # fmt: skip
    img = acc.reshape(10, 20)
    assert img[5, 2:13].min() >= 1 and img.sum() == img[5].sum()


def test_render_cube_not_blank():
    view = mv.MeshView()
    view.resize(480, 240)
    view._done(view._generation, MeshData(CUBE_POS, CUBE_TRIS, label="cube"))
    image = view.canvas.render_image()
    assert image.width() > 0
    bg = theme.current().color("base").rgb()
    lit = sum(
        image.pixel(x, y) != bg for x in range(0, image.width(), 4)
        for y in range(0, image.height(), 4)
    )  # fmt: skip
    assert lit > 20
    # the view grabs and renders; its minimum width now follows the view bar's buttons (the
    # preset views were added), so it is at least the requested width rather than exactly it.
    assert view.grab().width() >= 480


def _cube_view(w=640, h=480):
    view = mv.MeshView()
    view.resize(w, h)
    view._done(view._generation, MeshData(CUBE_POS, CUBE_TRIS, label="cube"))
    view.canvas.resize(w, h)
    return view


def _corners_on_screen(canvas) -> bool:
    """Whether all eight cube corners project inside the canvas under its own projection."""
    cam = canvas.camera
    eye, right, up, forward = cam.basis()
    w, h = canvas.width(), canvas.height()
    oscale = cam.ortho_scale(h) if cam.ortho else None
    for x in (0, 64):
        for y in (0, 64):
            for z in (0, 64):
                sx, sy, _d, front = gz.project_point(
                    (x, y, z), eye, right, up, forward, mv.focal(h), w, h, oscale
                )
                if not front or not (0 <= sx < w and 0 <= sy < h):
                    return False
    return True


def test_preset_top_is_ortho_and_frames_the_map():
    view = _cube_view()
    view.canvas.set_preset("top")
    assert view.canvas.camera.ortho  # Top is orthographic
    assert np.allclose(view.canvas.camera.basis()[3], [0, 0, -1], atol=1e-6)  # straight down
    assert _corners_on_screen(view.canvas)  # the whole map is in view
    view.canvas.set_preset("perspective")
    assert not view.canvas.camera.ortho
    assert _corners_on_screen(view.canvas)


def test_default_framing_shows_the_whole_map():
    view = _cube_view()
    view.canvas.reset_camera()  # the view a map opens on
    assert not view.canvas.camera.ortho
    assert _corners_on_screen(view.canvas)


def test_fly_toggle_and_speed_readout():
    view = _cube_view()
    canvas = view.canvas
    canvas.set_fly(True)
    assert canvas._flying and view.fly_button.isChecked()  # the bar button follows the canvas
    lines = canvas._hud_lines()
    assert any("fly" in line and "u/s" in line for line in lines)
    before = canvas.fly_speed
    pos = QPointF(canvas.width() / 2, canvas.height() / 2)
    wheel = QWheelEvent(pos, pos, QPoint(0, 0), QPoint(0, 120),
                        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                        Qt.ScrollPhase.NoScrollPhase, False)  # fmt: skip
    canvas.wheelEvent(wheel)
    assert canvas.fly_speed > before  # a wheel notch in fly mode raises the speed
    assert f"{canvas.fly_speed:,.0f}" in "   ".join(canvas._hud_lines())
    canvas.set_fly(False)
    assert not canvas._flying


def test_view_cube_click_snaps_to_top():
    view = _cube_view()
    canvas = view.canvas
    cx, cy = canvas._gizmo_hub()
    _eye, right, up, _fwd = canvas.camera.basis()
    r = canvas._GIZMO_R + 7
    zx = cx + float(np.array([0, 0, 1.0]) @ right) * r
    zy = cy - float(np.array([0, 0, 1.0]) @ up) * r
    assert canvas._gizmo_preset_at(zx, zy) == "top"
    _send_click(canvas, zx, zy)
    assert canvas.camera.ortho  # clicking the gizmo's Z arm snapped to the Top view


def nuked() -> Path | None:
    # Pin to the sha1-checked retail backup, not the live .env mp_nuked (the RPCS3 file
    # overwritten by every device test): these assertions are retail geometry counts.
    from retail_fixtures import retail_zone

    return retail_zone("mp_nuked")


@pytest.mark.zones
@pytest.mark.slow
def test_nuked_geometry():
    path = nuked()
    if path is None:
        pytest.skip("mp_nuked.ff not configured in .env")
    from opent5.gui import geometry
    from opent5.gui.backend import ZoneDoc

    doc = ZoneDoc.open(path)
    world = geometry.mesh(doc, "world")
    assert world.triangles.shape == (117181, 3)
    coll = geometry.mesh(doc, "collision")
    assert (coll.groups < 0).sum() == 7492
    view = mv.MeshView()
    view.resize(640, 480)
    view.models_button.setChecked(False)  # the plain world, without its static models
    view.load_sync(doc, "world")
    assert view.canvas.counts[1] == 117181


def _gl_ok() -> bool:
    # usable(), not available(): a headless context can be created but render nothing (offscreen
    # WSL), so the shaded tests skip cleanly there and still run where GL actually draws.
    from opent5.gui.glrender import ShadedRenderer

    return ShadedRenderer().usable()


def test_build_scene_sorts_triangles_by_texture():
    from opent5.gui import glrender as gl

    pos = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [1, 1, 0]], np.float32)
    tris = np.array([[0, 1, 2], [1, 3, 2]], np.int64)
    uvs = np.zeros((4, 2), np.float32)
    # triangle 0 untextured (-1), triangle 1 uses texture 0: the run order is -1 then 0
    tri_tex = np.array([-1, 0], np.int64)
    tex = np.full((4, 4, 4), 200, np.uint8)
    scene = gl.build_scene(pos, tris, None, uvs, tri_tex, [tex])
    assert scene.interleaved.shape == (4, 8)
    assert [r[0] for r in scene.runs] == [-1, 0]
    assert sum(r[2] for r in scene.runs) == 6  # two triangles, six indices


def test_shaded_renderer_draws_textured_quad():
    if not _gl_ok():
        pytest.skip("no usable OpenGL context here")
    from opent5.gui import glrender as gl

    pos = np.array([[-1, -1, 0], [1, -1, 0], [1, 1, 0], [-1, 1, 0]], np.float32)
    tris = np.array([[0, 1, 2], [0, 2, 3]], np.int64)
    uv = np.array([[0, 0], [1, 0], [1, 1], [0, 1]], np.float32)
    nrm = np.tile([0, 0, 1], (4, 1)).astype(np.float32)
    yy, xx = np.mgrid[0:16, 0:16]
    tex = np.zeros((16, 16, 4), np.uint8)
    tex[..., 0] = (xx * 16).astype(np.uint8)
    tex[..., 1] = (yy * 16).astype(np.uint8)
    tex[..., 3] = 255
    r = gl.ShadedRenderer()
    assert r.set_scene(gl.build_scene(pos, tris, nrm, uv, np.array([0, 0]), [tex]))
    img = r.render((0, 0, 2.6), (0, 0, 0), (0, 1, 0), 80, 80, (20, 22, 24), 0.1, 10)
    assert img is not None and img.width() == 80
    colours = {img.pixel(x, y) for x in range(0, 80, 4) for y in range(0, 80, 4)}
    assert len(colours) > 8  # the gradient texture varies across the quad


def test_shaded_view_falls_back_without_gl(monkeypatch):
    view = mv.MeshView()
    monkeypatch.setattr(view.canvas, "gl_available", lambda: False)
    view.shaded_button.setChecked(True)
    assert not view.shaded_button.isChecked()  # refused, stays on wireframe
    assert not view.canvas.shaded


def test_viewer_defaults_to_gpu_when_gl_available(monkeypatch):
    """A fresh mesh defaults to the shaded GPU view when OpenGL can draw. Keyed off
    gl_available(), so it is checked with a stub and needs no real GPU."""
    view = mv.MeshView()
    monkeypatch.setattr(view.canvas, "gl_available", lambda: True)
    view._done(view._generation, MeshData(CUBE_POS, CUBE_TRIS, label="cube"))
    assert view.shaded_button.isChecked()
    assert view.canvas.shaded


def test_viewer_defaults_to_software_without_gl(monkeypatch):
    """With no usable GL the viewer stays on the software wireframe by default."""
    view = mv.MeshView()
    monkeypatch.setattr(view.canvas, "gl_available", lambda: False)
    view._done(view._generation, MeshData(CUBE_POS, CUBE_TRIS, label="cube"))
    assert not view.shaded_button.isChecked()
    assert not view.canvas.shaded


def test_manual_shaded_choice_sticks_across_loads(monkeypatch):
    """A hand toggle wins over the GPU-or-not default on later loads: turning Shaded off on a
    GL machine keeps it off when the next map loads."""
    view = mv.MeshView()
    monkeypatch.setattr(view.canvas, "gl_available", lambda: True)
    view._done(view._generation, MeshData(CUBE_POS, CUBE_TRIS, label="cube"))
    assert view.canvas.shaded  # defaulted on
    view.shaded_button.setChecked(False)  # user turns it off by hand
    assert not view.canvas.shaded
    view._done(view._generation, MeshData(CUBE_POS, CUBE_TRIS, label="cube2"))
    assert not view.shaded_button.isChecked()  # the hand choice stuck
    assert not view.canvas.shaded


def test_world_view_offers_static_models_only_for_worlds():
    view = mv.MeshView()
    assert view.models_button.isHidden()
    view._done(view._generation, MeshData(CUBE_POS, CUBE_TRIS, label="cube"))
    # static models default to on, so a world first shows with its props placed
    assert view.models_button.isChecked()
    assert view._world_kind("world") == "world_models"
    view.models_button.blockSignals(True)
    view.models_button.setChecked(False)
    view.models_button.blockSignals(False)
    assert view._world_kind("world") == "world"
    assert view._world_kind("collision") == "collision"


@pytest.mark.zones
@pytest.mark.slow
def test_nuked_world_with_static_models():
    path = nuked()
    if path is None:
        pytest.skip("mp_nuked.ff not configured in .env")
    from opent5.gui import geometry
    from opent5.gui.backend import ZoneDoc

    doc = ZoneDoc.open(path)
    world = geometry.mesh(doc, "world")
    both = geometry.mesh(doc, "world_models")
    placed = both.groups >= len(world.materials)
    assert (both.triangles[~placed] == world.triangles).all()
    assert placed.sum() > 0 and "4209 static models" in both.notes
    # every placed surface's group indexes into the extended materials list
    assert both.materials is not None and both.groups.max() < len(both.materials)


@pytest.mark.zones
@pytest.mark.slow
def test_nuked_shaded_world_not_blank():
    path = nuked()
    if path is None:
        pytest.skip("mp_nuked.ff not configured in .env")
    if not _gl_ok():
        pytest.skip("no usable OpenGL context here")
    from opent5.gui.backend import ZoneDoc

    doc = ZoneDoc.open(path)
    view = mv.MeshView()
    view.resize(640, 480)
    view.shaded_button.setChecked(True)
    view.load_sync(doc, "world")
    assert view.canvas._scene is not None and view.canvas._tex_count > 50
    image = view.canvas.render_image()
    bg = theme.current().color("base").rgb()
    colours = {
        image.pixel(x, y) for x in range(0, image.width(), 8) for y in range(0, image.height(), 8)
    }
    assert len(colours) > 40 and image.pixel(320, 300) != bg


# -- static-model prop picking (v0.3.0) ------------------------------------------------------


def test_ray_aabb_distance_hits_and_misses():
    o = np.array([0.0, 0.0, 0.0])
    # a box straight ahead on +X: entered at its near face
    assert mv.ray_aabb_distance(o, (1, 0, 0), (10, -1, -1), (12, 1, 1)) == pytest.approx(10.0)
    # a box off to the side is missed
    assert mv.ray_aabb_distance(o, (1, 0, 0), (10, 5, -1), (12, 7, 1)) is None
    # origin inside the box: distance zero
    assert mv.ray_aabb_distance(o, (1, 0, 0), (-1, -1, -1), (1, 1, 1)) == 0.0


class _FakeProp:
    def __init__(self, index, origin, absmin, absmax, model="crate"):
        self.index, self.model = index, model
        self.origin, self.absmin, self.absmax = origin, absmin, absmax


class _FakeMarker:
    def __init__(self, obj_id, origin):
        self.id, self.kind = obj_id, "entity"
        self.keys = {"origin": " ".join(str(v) for v in origin)}

    @property
    def origin(self):
        return tuple(float(v) for v in self.keys["origin"].split())


class _FakeSession:
    can_edit_clips = True

    def __init__(self, props, markers):
        self._props, self._markers = props, markers

    def static_models(self):
        return list(self._props)

    def markers(self):
        return list(self._markers)


def _axis_camera(distance=1000.0):
    """A camera at ``target + (distance, 0, 0)`` looking down -X, so a screen-centre ray runs
    straight along -X and a point on the X axis projects to the screen centre."""
    cam = mv.Camera()
    cam.yaw = 0.0
    cam.pitch = 0.0
    cam.target = np.zeros(3)
    cam.distance = distance
    return cam


def test_pick_nearest_camera_prop_in_front_of_entity_wins():
    # prop box around x=500, entity origin behind it at x=0: both project to the screen centre.
    prop = _FakeProp(7, (500, 0, 0), (480, -20, -20), (520, 20, 20))
    marker = _FakeMarker(99, (0, 0, 0))
    ctl = mv.MapEditController(_FakeSession([prop], [marker]))
    ctl.pick_filter = mv.MapEditController.PICK_ALL
    ctl.props_shown = True
    cam = _axis_camera(1000.0)
    w, h = 800, 600
    picked = ctl.pick(cam, w / 2, h / 2, w, h, radius=12.0)
    assert ctl.selected_prop == 7 and picked is None  # the nearer prop wins


def test_pick_nearest_camera_entity_in_front_of_prop_wins():
    # same geometry, but the entity now sits in front of the prop (x=900, eye at x=1000).
    prop = _FakeProp(7, (500, 0, 0), (480, -20, -20), (520, 20, 20))
    marker = _FakeMarker(99, (900, 0, 0))
    ctl = mv.MapEditController(_FakeSession([prop], [marker]))
    ctl.pick_filter = mv.MapEditController.PICK_ALL
    ctl.props_shown = True
    cam = _axis_camera(1000.0)
    w, h = 800, 600
    picked = ctl.pick(cam, w / 2, h / 2, w, h, radius=12.0)
    assert picked == 99 and ctl.selected_prop is None


def test_pick_filter_props_ignores_entities():
    prop = _FakeProp(7, (500, 0, 0), (480, -20, -20), (520, 20, 20))
    marker = _FakeMarker(99, (900, 0, 0))  # nearer the eye, but filtered out
    ctl = mv.MapEditController(_FakeSession([prop], [marker]))
    ctl.pick_filter = mv.MapEditController.PICK_PROPS
    ctl.props_shown = True
    cam = _axis_camera(1000.0)
    ctl.pick(cam, 400, 300, 800, 600)
    assert ctl.selected_prop == 7 and ctl.selected_id is None


def test_pick_prop_works_in_ortho_top_view():
    # the editor must still pick in the orthographic Top view (where props are placed): a prop
    # box around the origin, camera looking straight down, cursor at the screen centre.
    prop = _FakeProp(7, (0, 0, 0), (-20, -20, -20), (20, 20, 20))
    ctl = mv.MapEditController(_FakeSession([prop], []))
    ctl.pick_filter = mv.MapEditController.PICK_ALL
    ctl.props_shown = True
    cam = mv.Camera()
    cam.yaw, cam.pitch, cam.ortho = mv.PRESETS["top"]
    cam.target = np.zeros(3)
    cam.distance = 1000.0
    cam.ortho_half_h = 500.0
    w, h = 800, 600
    ctl.pick(cam, w / 2, h / 2, w, h)
    assert ctl.selected_prop == 7
    # the gizmo handles also project under the ortho scale, so the move handles are reachable
    _centre, handles = ctl.axis_handles(cam, w, h)
    assert any(front for _i, _p, front in handles)


def test_pick_props_need_static_models_shown():
    prop = _FakeProp(7, (500, 0, 0), (480, -20, -20), (520, 20, 20))
    ctl = mv.MapEditController(_FakeSession([prop], []))
    ctl.pick_filter = mv.MapEditController.PICK_ALL
    ctl.props_shown = False  # static models not drawn: an off-screen prop is not pickable
    cam = _axis_camera(1000.0)
    ctl.pick(cam, 400, 300, 800, 600)
    assert ctl.selected_prop is None


# -- real GUI picking on the pinned retail mp_nuked ------------------------------------------

#: mp_nuked's school bus is static-model index 503 (t5_veh_schoolbus).
_BUS_INDEX = 503


def _nuked_edit_view():
    """A geometry view editing retail mp_nuked with its static models placed, framed on the
    bus. Returns ``(view, controller, bus, canvas, w, h)`` or skips when the backup is absent."""
    path = nuked()
    if path is None:
        pytest.skip("mp_nuked.ff not available")
    from opent5.gui.backend import ZoneDoc

    doc = ZoneDoc.open(path)
    view = mv.MeshView()
    view.resize(900, 600)
    view.load_sync(doc, "world")  # static models default on -> world_models, Edit can turn on
    view.edit_button.setChecked(True)
    ctl = view.canvas.controller
    assert ctl is not None, "Edit did not turn on"
    canvas = view.canvas
    canvas.resize(820, 560)
    bus = next((p for p in ctl.props() if p.index == _BUS_INDEX), None)
    assert bus is not None and bus.model and "schoolbus" in bus.model
    return view, ctl, bus, canvas, canvas.width(), canvas.height()


def _bus_centre(bus):
    return np.array([(bus.absmin[k] + bus.absmax[k]) / 2 for k in range(3)], float)


def _frame_bus(canvas, bus):
    """Frame the bus from a side the moving truck beside it does not occlude, and return the
    bus centre's screen position under the view's own projection."""
    centre = _bus_centre(bus)
    canvas.camera.target = centre
    canvas.camera.distance = 500.0
    canvas.camera.yaw = math.radians(180.0)
    canvas.camera.pitch = math.radians(40.0)
    eye, right, up, forward = canvas.camera.basis()
    sx, sy, _d, front = gz.project_point(
        centre, eye, right, up, forward, mv.focal(canvas.height()), canvas.width(), canvas.height()
    )
    assert front
    return sx, sy


def _send_click(canvas, px, py):
    """Post a real left press+release at ``(px, py)`` through the widget's event handlers."""
    pos = QPointF(px, py)
    press = QMouseEvent(QEvent.Type.MouseButtonPress, pos, pos, Qt.MouseButton.LeftButton,
                        Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)  # fmt: skip
    release = QMouseEvent(QEvent.Type.MouseButtonRelease, pos, pos, Qt.MouseButton.LeftButton,
                          Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)  # fmt: skip
    APP.sendEvent(canvas, press)
    APP.sendEvent(canvas, release)


def _send_drag(canvas, start, end):
    """Post a left press, a few moves and a release, dragging from ``start`` to ``end``."""
    sx, sy = start
    ex, ey = end
    press = QMouseEvent(QEvent.Type.MouseButtonPress, QPointF(sx, sy), QPointF(sx, sy),
                        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                        Qt.KeyboardModifier.NoModifier)  # fmt: skip
    APP.sendEvent(canvas, press)
    for k in range(1, 6):
        t = k / 5.0
        mx, my = sx + (ex - sx) * t, sy + (ey - sy) * t
        move = QMouseEvent(QEvent.Type.MouseMove, QPointF(mx, my), QPointF(mx, my),
                           Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton,
                           Qt.KeyboardModifier.NoModifier)  # fmt: skip
        APP.sendEvent(canvas, move)
    release = QMouseEvent(QEvent.Type.MouseButtonRelease, QPointF(ex, ey), QPointF(ex, ey),
                          Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton,
                          Qt.KeyboardModifier.NoModifier)  # fmt: skip
    APP.sendEvent(canvas, release)


@pytest.mark.zones
@pytest.mark.slow
def test_nuked_click_selects_the_bus():
    view, ctl, bus, canvas, w, h = _nuked_edit_view()
    # Edit defaults to the Props filter, so the map is not buried under its entity dots.
    assert ctl.pick_filter == mv.MapEditController.PICK_PROPS
    assert view.panel.pick_filter.currentText() == "Props"
    sx, sy = _frame_bus(canvas, bus)
    _send_click(canvas, sx, sy)
    assert ctl.selected_prop == _BUS_INDEX
    # the panel shows the model name, the index and the clip-cluster brush count
    view.panel.show_object(None)
    label = view.panel.selected_label.text()
    assert "t5_veh_schoolbus" in label and "index 503" in label and "brush" in label
    # and the whole view paints with the prop selected (the old drawPolygon crash path)
    canvas.grab()


@pytest.mark.zones
@pytest.mark.slow
def test_nuked_drag_moves_the_bus():
    view, ctl, bus, canvas, w, h = _nuked_edit_view()
    sx, sy = _frame_bus(canvas, bus)
    _send_click(canvas, sx, sy)
    assert ctl.selected_prop == _BUS_INDEX
    before = ctl.selected_prop_obj().origin
    boxes_before = ctl.selected_cluster_boxes()
    # grab a gizmo axis handle that is on screen and drag along it
    _centre, handles = ctl.axis_handles(canvas.camera, w, h)
    handle = next((hh for hh in handles if hh[2]), None)
    assert handle is not None, "no gizmo axis handle on screen"
    hx, hy = handle[1]
    _send_drag(canvas, (hx, hy), (hx + 60, hy))
    after = ctl.selected_prop_obj().origin
    assert after != before, "the prop origin did not change"
    assert ctl.selected_cluster_boxes() != boxes_before, "the clip cluster did not move"
    assert "move prop clip" in view._session.history()  # move_prop_clip ran


@pytest.mark.zones
@pytest.mark.slow
def test_nuked_click_entity_marker_does_not_crash():
    view, ctl, bus, canvas, w, h = _nuked_edit_view()
    # show the entity dots and click one: it must select cleanly (or miss), never crash.
    view.panel.pick_filter.setCurrentText("Entities")
    assert ctl.pick_filter == mv.MapEditController.PICK_ENTITIES
    _frame_bus(canvas, bus)
    ids, pts, _d = ctl.marker_screen(canvas.camera, w, h)
    onscreen = [
        (i, p) for i, p in zip(ids, pts, strict=True)
        if not np.isnan(p[0]) and 0 <= p[0] < w and 0 <= p[1] < h
    ]  # fmt: skip
    assert onscreen, "no entity markers on screen to click"
    i, p = onscreen[0]
    _send_click(canvas, float(p[0]), float(p[1]))
    # a marker click selects an entity and never a prop under this filter; no crash on repaint
    assert ctl.selected_prop is None
    canvas.grab()
