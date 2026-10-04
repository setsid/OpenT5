"""The 3D-view edit controller (opent5.gui.views.mesh.MapEditController) driving a real
EditSession offscreen: marker projection and picking, axis-constrained translate and rotate
drags, and add / delete / duplicate. No GPU; the interactive gizmo feel is a device check."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QKeyEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from opent5.edit.document import Document  # noqa: E402
from opent5.edit.mapedit import EditSession  # noqa: E402
from opent5.gui.views import mesh as mv  # noqa: E402
from test_mapedit import zone_bytes  # noqa: E402

APP = QApplication.instance() or QApplication(["opent5"])


def _session(tmp_path):
    path = tmp_path / "mp_box.ff"
    path.write_bytes(zone_bytes())
    return EditSession(Document.open(path))


def _spawn(session, classname="mp_tdm_spawn"):
    for o in session.objects:
        if o.classname == classname:
            return o
    raise AssertionError(classname)


def _aim(cam, origin):
    cam.target = np.asarray(origin, np.float64)
    cam.distance = 512.0


def test_pick_selects_marker_under_cursor(tmp_path):
    session = _session(tmp_path)
    ctl = mv.MapEditController(session)
    spawn = _spawn(session)
    cam = mv.Camera()
    _aim(cam, spawn.origin)
    w, h = 640, 480
    # the aimed marker projects to the screen centre
    picked = ctl.pick(cam, w / 2, h / 2, w, h)
    assert picked == spawn.id
    assert ctl.selected().classname == "mp_tdm_spawn"


def test_pick_empty_space_deselects(tmp_path):
    session = _session(tmp_path)
    ctl = mv.MapEditController(session)
    cam = mv.Camera()
    _aim(cam, _spawn(session).origin)
    assert ctl.pick(cam, 5, 5, 640, 480) is None


def test_translate_drag_moves_only_along_axis(tmp_path):
    session = _session(tmp_path)
    ctl = mv.MapEditController(session)
    spawn = _spawn(session)
    cam = mv.Camera()
    _aim(cam, spawn.origin)
    w, h = 640, 480
    ctl.pick(cam, w / 2, h / 2, w, h)
    before = np.asarray(ctl.selected().origin, float)
    ctl.begin_drag(0, cam, w / 2, h / 2, w, h)  # X axis
    ctl.update_drag(cam, w / 2 + 120, h / 2, w, h)
    after = np.asarray(ctl.selected().origin, float)
    ctl.end_drag()
    assert not np.allclose(after[0], before[0])  # moved along X
    assert np.allclose(after[1:], before[1:])  # Y and Z unchanged
    assert len(session.history()) == 1  # the drag is one undo entry


def test_two_drags_do_not_coalesce(tmp_path):
    session = _session(tmp_path)
    ctl = mv.MapEditController(session)
    spawn = _spawn(session)
    cam = mv.Camera()
    _aim(cam, spawn.origin)
    w, h = 640, 480
    ctl.pick(cam, w / 2, h / 2, w, h)
    for _ in range(2):
        ctl.begin_drag(0, cam, w / 2, h / 2, w, h)
        ctl.update_drag(cam, w / 2 + 120, h / 2, w, h)
        ctl.end_drag()
    assert len(session.history()) == 2


def test_rotate_drag_changes_angles(tmp_path):
    session = _session(tmp_path)
    ctl = mv.MapEditController(session)
    ctl.mode = "rotate"
    spawn = _spawn(session)
    cam = mv.Camera()
    _aim(cam, spawn.origin)
    w, h = 640, 480
    ctl.pick(cam, w / 2, h / 2, w, h)
    before = ctl.selected().keys.get("angles")
    ctl.begin_drag(2, cam, w / 2 + 80, h / 2, w, h)  # about Z
    ctl.update_drag(cam, w / 2, h / 2 + 80, w, h)
    after = ctl.selected().keys.get("angles")
    ctl.end_drag()
    assert after != before


def test_add_delete_duplicate(tmp_path):
    session = _session(tmp_path)
    ctl = mv.MapEditController(session)
    n = len(session.objects)
    new_id = ctl.add({"classname": "mp_dm_spawn", "origin": "1 2 3"})
    assert len(session.objects) == n + 1
    assert ctl.selected_id == new_id
    dup = ctl.duplicate()
    assert len(session.objects) == n + 2
    assert dup != new_id
    ctl.delete()
    assert len(session.objects) == n + 1
    assert ctl.selected_id is None


def test_controller_undo(tmp_path):
    session = _session(tmp_path)
    ctl = mv.MapEditController(session)
    base = session.build()
    ctl.add({"classname": "mp_dm_spawn", "origin": "1 2 3"})
    assert session.build() != base
    ctl.undo()
    assert session.build() == base
    assert ctl.selected_id is None


# -- the view, panel and canvas glue ---------------------------------------------------------


def test_edit_button_hidden_by_default():
    view = mv.MeshView()
    assert not view.edit_button.isVisible()
    assert not view.panel.isVisible()


def test_panel_shows_and_edits_fields(tmp_path):
    session = _session(tmp_path)
    ctl = mv.MapEditController(session)
    view = mv.MeshView()
    view.canvas.set_controller(ctl)
    view.panel.set_controller(ctl, on_save=lambda p: None)
    spawn = _spawn(session)
    ctl.selected_id = spawn.id
    view.panel.show_object(spawn.id)
    assert "mp_tdm_spawn" in view.panel.selected_label.text()
    edit = view.panel._field_edits["origin"]
    edit.setText("9 9 9")
    view.panel._commit("origin", edit)
    assert session.object(spawn.id).keys["origin"] == "9 9 9"


def test_canvas_delete_and_duplicate_keys(tmp_path):
    session = _session(tmp_path)
    ctl = mv.MapEditController(session)
    view = mv.MeshView()
    view.canvas.set_controller(ctl)
    view.panel.set_controller(ctl, on_save=lambda p: None)
    ctl.selected_id = _spawn(session, "mp_dm_spawn").id
    n = len(session.objects)
    # Ctrl+D duplicates
    view.canvas.keyPressEvent(
        QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_D, Qt.KeyboardModifier.ControlModifier)
    )
    assert len(session.objects) == n + 1
    # Delete removes the selection
    view.canvas.keyPressEvent(
        QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Delete, Qt.KeyboardModifier.NoModifier)
    )
    assert len(session.objects) == n
    assert ctl.selected_id is None


def test_view_save_edited_reports(tmp_path):
    session = _session(tmp_path)
    ctl = mv.MapEditController(session)
    view = mv.MeshView()
    view._session = session
    view.canvas.set_controller(ctl)
    messages: list[str] = []
    view.status.connect(messages.append)
    session.move_object(_spawn(session).id, (5, 6, 7))
    out = tmp_path / "out.ff"
    view._save_edited(out)
    assert out.exists()
    assert any("verified" in m for m in messages)


def test_view_save_refused_reports(tmp_path):
    session = _session(tmp_path)
    view = mv.MeshView()
    view._session = session
    messages: list[str] = []
    view.status.connect(messages.append)
    # break tdm by deleting its only mp_tdm_spawn
    session.delete_object(_spawn(session).id)
    out = tmp_path / "broken.ff"
    view._save_edited(out)
    assert not out.exists()
    assert any("tdm" in m for m in messages)
