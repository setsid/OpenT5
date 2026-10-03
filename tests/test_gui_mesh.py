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
    view.resize(320, 240)
    view._done(view._generation, MeshData(CUBE_POS, CUBE_TRIS, label="cube"))
    image = view.canvas.render_image()
    assert image.width() > 0
    bg = theme.current().color("base").rgb()
    lit = sum(
        image.pixel(x, y) != bg for x in range(0, image.width(), 4)
        for y in range(0, image.height(), 4)
    )  # fmt: skip
    assert lit > 20
    assert view.grab().width() == 320


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
