"""The wireframe view: edge extraction, rasterising, and (with .env) mp_nuked's geometry."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

import numpy as np
import pytest
from PySide6.QtWidgets import QApplication

from opent5 import env
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
    assert view.grab().width() == 480


def nuked() -> Path | None:
    folder = env.path_of("OPENT5_ZONES")
    path = folder / "mp_nuked.ff" if folder else None
    return path if path and path.is_file() else None


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
    view.load_sync(doc, "world")
    assert view.canvas.counts[1] == 117181


def _gl_ok() -> bool:
    from opent5.gui.glrender import ShadedRenderer

    return ShadedRenderer().available()


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


def test_world_view_offers_static_models_only_for_worlds():
    view = mv.MeshView()
    assert view.models_button.isHidden()
    view._done(view._generation, MeshData(CUBE_POS, CUBE_TRIS, label="cube"))
    assert view._world_kind("world") == "world"
    view.models_button.blockSignals(True)
    view.models_button.setChecked(True)
    view.models_button.blockSignals(False)
    assert view._world_kind("world") == "world_models"
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
