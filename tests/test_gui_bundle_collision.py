"""v0.4.0 editor: reliable bundle move/delete for EVERY prop, and the clip-brush collision
overlay (the invisible walls) made visible, selectable and removable.

These drive the real MapEditController / MeshView / canvas offscreen against the pinned retail
mp_nuked (never the live .env zone):

- a move of several DIFFERENT props moves each one's OWN render every time (the robust
  prop->draw-instance link, which fixes the old origin-only match that silently linked a
  shared-origin prop to its neighbour's render);
- the bundle move of a clip-less prop makes it SOLID at the new footprint (a fresh clip);
- deleting a prop hides its render AND removes its clip cluster (the wall goes);
- Show collision renders the clip brushes, a clip brush is pickable and deletable, and the
  overlay reflects the removal;
- the right-click menu's "collision only" removes just the clip and "model only" hides just
  the render;
- showing collision and editing a clip never triggers a full world-mesh rebuild.

Marked zones + slow: they need a real PS3 clipMap and GfxWorld.
"""

from __future__ import annotations

import math
import os
import struct

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np  # noqa: E402
import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from opent5.edit import gizmo as gz  # noqa: E402
from opent5.edit.document import Document  # noqa: E402
from opent5.edit.mapedit import _DRAW_SCALE, EditSession  # noqa: E402
from opent5.gui import geometry  # noqa: E402
from opent5.gui.backend import ZoneDoc  # noqa: E402
from opent5.gui.views import mesh as mv  # noqa: E402
from retail_fixtures import retail_zone  # noqa: E402

APP = QApplication.instance() or QApplication(["opent5"])


def _session() -> EditSession:
    return EditSession(Document.open(retail_zone("mp_nuked").read_bytes(), name="mp_nuked.ff"))


def _clipless_prop(s: EditSession):
    for p in s.static_models():
        if (
            s._draw_inst_for(p.index) is not None
            and not s.clip_cluster_in_footprint(*p.footprint)
        ):
            return p
    pytest.skip("no clip-less drawing prop in this build")


def _clustered_prop(s: EditSession):
    for p in s.static_models():
        if s.clip_cluster_in_footprint(*p.footprint):
            return p
    pytest.skip("no clustered prop in this build")


# -- 1. robust linkage: moving several different props moves each one's own render -----------


@pytest.mark.zones
@pytest.mark.slow
def test_move_several_props_moves_each_own_render_every_time():
    s = _session()
    ctl = mv.MapEditController(s)
    # the link map, so each prop's own draw instance is known up front
    linked = [p for p in s.static_models() if s._draw_inst_for(p.index) is not None]
    assert len(linked) > 5
    # cover several distinct models, and specifically a prop that shares an origin with a
    # different-model neighbour (the case the old origin-only match got wrong)
    picks = []
    seen_models: set = set()
    shared = None
    origins: dict = {}
    for p in linked:
        key = tuple(round(v, 1) for v in p.origin)
        if key in origins and s.static_models()[origins[key]].model != p.model:
            shared = (origins[key], p.index)
        origins.setdefault(key, p.index)
        if p.model not in seen_models:
            seen_models.add(p.model)
            picks.append(p.index)
        if len(picks) >= 6:
            break
    if shared is not None:
        picks = [shared[0], shared[1], *picks][:6]

    delta = (250.0, -150.0, 30.0)
    for idx in picks:
        j = s._draw_inst_for(idx)
        before = s._draw_inst_origin(j)
        # a control prop that must not move
        control = next(p.index for p in linked if p.index != idx)
        cj = s._draw_inst_for(control)
        cbefore = s._draw_inst_origin(cj)

        ctl.selected_prop = idx
        result = ctl.move_selected_prop(delta)
        assert result.get("found") or result.get("render_moved")

        after = s._draw_inst_origin(j)
        assert tuple(round(after[k] - before[k], 1) for k in range(3)) == delta, idx
        # the control prop's render stayed exactly put (no cross-linking)
        if cj != j:
            assert s._draw_inst_origin(cj) == cbefore, (idx, control)
        s.undo()
        assert s._draw_inst_origin(j) == before  # exact undo


@pytest.mark.zones
@pytest.mark.slow
def test_shared_origin_prop_links_to_its_own_render():
    """Two props at the same spot but different models (the old origin-only match linked both
    to the first one's draw instance). Each must move only its own render."""
    s = _session()
    sm = {p.index: p for p in s.static_models()}
    # find a colliding pair: same rounded origin, different model, both linked
    grid: dict = {}
    pair = None
    for p in s.static_models():
        if s._draw_inst_for(p.index) is None:
            continue
        key = tuple(round(v, 1) for v in p.origin)
        if key in grid and sm[grid[key]].model != p.model:
            pair = (grid[key], p.index)
            break
        grid.setdefault(key, p.index)
    if pair is None:
        pytest.skip("no shared-origin, different-model prop pair in this build")
    a, b = pair
    ja, jb = s._draw_inst_for(a), s._draw_inst_for(b)
    assert ja != jb  # distinct draw instances, matched by model not just origin
    assert s._draw_inst_name(ja) == sm[a].model and s._draw_inst_name(jb) == sm[b].model
    ctl = mv.MapEditController(s)
    oa, ob = s._draw_inst_origin(ja), s._draw_inst_origin(jb)
    ctl.selected_prop = a
    ctl.move_selected_prop((400.0, 0.0, 0.0))
    assert s._draw_inst_origin(ja)[0] == pytest.approx(oa[0] + 400.0, abs=0.1)
    assert s._draw_inst_origin(jb) == ob  # the neighbour's render did not move


# -- 2. bundle move of a clip-less prop makes it solid at the new footprint -------------------


@pytest.mark.zones
@pytest.mark.slow
def test_move_clipless_prop_is_solid_at_new_footprint():
    s = _session()
    prop = _clipless_prop(s)
    j = s._draw_inst_for(prop.index)
    before = s._draw_inst_origin(j)
    ctl = mv.MapEditController(s)
    ctl.selected_prop = prop.index

    delta = (64.0, 0.0, 0.0)
    result = ctl.move_selected_prop(delta)

    assert result["render_moved"] is True  # the model follows the gizmo
    assert s._draw_inst_origin(j)[0] == pytest.approx(before[0] + 64.0, abs=0.1)
    assert result["added_clip"] is not None  # a fresh clip was added at the new spot
    new_fp = (
        tuple(prop.footprint[0][k] + delta[k] for k in range(3)),
        tuple(prop.footprint[1][k] + delta[k] for k in range(3)),
    )
    assert s.clip_cluster_in_footprint(*new_fp)  # solid at the new footprint
    assert any("old position" in w.lower() for w in result["warnings"])  # baked stays, honest
    assert parse_ok(s)
    s.undo()
    assert not s.clip_cluster_in_footprint(*new_fp)  # the added clip is gone again


def parse_ok(s: EditSession) -> bool:
    from opent5.xfile import parse

    return parse(s.build()).problems() == []


# -- 3. delete a prop: render hidden AND clip cluster gone ------------------------------------


@pytest.mark.zones
@pytest.mark.slow
def test_delete_prop_hides_render_and_removes_clip():
    s = _session()
    prop = _clustered_prop(s)
    cluster = s.clip_cluster_in_footprint(*prop.footprint)
    cm, _ = s._clip_engine()
    j = s._draw_inst_for(prop.index)
    ctl = mv.MapEditController(s)
    ctl.selected_prop = prop.index

    result = ctl.delete_selected_prop_clip()

    assert result["found"] and sorted(result["removed"]) == sorted(cluster)
    assert all(cm.brush(i).contents == 0 for i in cluster)  # the invisible wall is gone
    if j is not None:
        assert struct.unpack_from(">f", s._draw_raw(j), _DRAW_SCALE)[0] == 0.0  # render hidden
    assert parse_ok(s)
    s.undo()
    assert all(cm.brush(i).contents != 0 for i in cluster)  # restored


# -- 4. show collision: render clip brushes, pick and delete one ------------------------------


def _aim_pick_clip(ctl, cam, box):
    centre = np.array([(box[0][k] + box[1][k]) / 2 for k in range(3)], float)
    cam.target = centre
    cam.distance = 400.0
    cam.yaw = math.radians(200.0)
    cam.pitch = math.radians(35.0)
    eye, right, up, forward = cam.basis()
    sx, sy, _d, front = gz.project_point(centre, eye, right, up, forward, mv.focal(480), 640, 480)
    if not front:
        return False
    ctl.pick(cam, sx, sy, 640, 480)
    return True


@pytest.mark.zones
@pytest.mark.slow
def test_show_collision_pick_and_delete_clip_brush():
    doc = ZoneDoc.open(retail_zone("mp_nuked"))
    s = EditSession(doc.impl)
    ctl = mv.MapEditController(s)
    ctl.show_collision = True
    ctl.collision_families = ("clip",)

    boxes = ctl.collision_boxes()
    assert len(boxes) > 100  # the invisible clip walls are enumerated

    # the overlay mesh renders those brushes (groups map back to clipMap brush indices)
    overlay = geometry.collision_overlay_mesh(doc, ("clip",))
    assert overlay is not None and len(overlay.triangles) > 0
    brush_ids = set(int(g) for g in overlay.groups.tolist())
    assert any(i in brush_ids for i, *_ in boxes)

    # pick a clip brush by aiming at a large, open one and clicking through the controller:
    # the integrated pick selects the clip (not a prop or entity) because the ray enters it.
    cam = mv.Camera()
    big = max(boxes, key=lambda b: np.prod([b[2][k] - b[1][k] for k in range(3)]))
    assert _aim_pick_clip(ctl, cam, (big[1], big[2]))
    assert ctl.selected_clip is not None  # a clip brush was picked, independently of props
    assert ctl.selected_prop is None and ctl.selected_id is None
    picked = ctl.selected_clip

    cm, _ = s._clip_engine()
    assert cm.brush(picked).contents != 0
    result = ctl.delete_selected_clip()
    assert picked in result["removed"]
    assert cm.brush(picked).contents == 0  # disable_clip: the wall is removed

    # the collision view reflects it: the brush drops out of the clip family
    assert picked not in [i for i, *_ in ctl.collision_boxes()]
    geometry.drop_collision_overlay(doc)
    overlay2 = geometry.collision_overlay_mesh(doc, ("clip",))
    assert picked not in set(int(g) for g in overlay2.groups.tolist())
    assert parse_ok(s)


# -- 5. right-click menu pieces: collision only vs model only --------------------------------


@pytest.mark.zones
@pytest.mark.slow
def test_menu_collision_only_removes_clip_leaves_render():
    s = _session()
    prop = _clustered_prop(s)
    cluster = s.clip_cluster_in_footprint(*prop.footprint)
    cm, _ = s._clip_engine()
    j = s._draw_inst_for(prop.index)
    ctl = mv.MapEditController(s)
    ctl.selected_prop = prop.index

    ctl.delete_selected_collision_only()  # the menu's "collision only"

    assert all(cm.brush(i).contents == 0 for i in cluster)  # clip gone
    if j is not None:
        assert struct.unpack_from(">f", s._draw_raw(j), _DRAW_SCALE)[0] != 0.0  # render stays
    assert parse_ok(s)


@pytest.mark.zones
@pytest.mark.slow
def test_menu_model_only_hides_render_leaves_clip():
    s = _session()
    prop = _clustered_prop(s)
    cluster = s.clip_cluster_in_footprint(*prop.footprint)
    cm, _ = s._clip_engine()
    j = s._draw_inst_for(prop.index)
    if j is None:
        pytest.skip("clustered prop has no render to hide in this build")
    ctl = mv.MapEditController(s)
    ctl.selected_prop = prop.index

    ctl.delete_selected_model_only()  # the menu's "model only"

    assert struct.unpack_from(">f", s._draw_raw(j), _DRAW_SCALE)[0] == 0.0  # render hidden
    assert all(cm.brush(i).contents != 0 for i in cluster)  # clip stays solid
    assert parse_ok(s)


@pytest.mark.zones
@pytest.mark.slow
def test_context_menu_builds_on_a_selected_prop():
    """The canvas builds the right-click menu for a selected prop without raising, and its
    default action deletes the whole bundle."""
    doc = ZoneDoc.open(retail_zone("mp_nuked"))
    view = mv.MeshView()
    view.load_sync(doc, "world_models")
    session = view._ensure_session()
    ctl = mv.MapEditController(session)
    view._edit_controller = ctl
    view.canvas.set_controller(ctl)
    prop = _clustered_prop(session)
    cluster = session.clip_cluster_in_footprint(*prop.footprint)
    cm, _ = session._clip_engine()
    ctl.selected_prop = prop.index

    from PySide6.QtWidgets import QMenu

    menu = QMenu()
    view.canvas._build_prop_menu(menu, ctl)
    labels = [a.text() for a in menu.actions() if a.text()]
    assert any("Delete everything" in t for t in labels)
    assert any("model only" in t.lower() for t in labels)
    assert any("collision only" in t.lower() for t in labels)
    assert any("cannot remove" in t.lower() for t in labels)  # baked, disabled
    baked = next(a for a in menu.actions() if "cannot remove" in a.text().lower())
    assert not baked.isEnabled()

    # the default action deletes the bundle
    ctl.delete_selected_prop_clip()
    assert all(cm.brush(i).contents == 0 for i in cluster)


# -- 6. perf guard: showing collision / a clip edit does not rebuild the world mesh ----------


@pytest.mark.zones
@pytest.mark.slow
def test_show_collision_does_not_rebuild_world_mesh():
    doc = ZoneDoc.open(retail_zone("mp_nuked"))
    view = mv.MeshView()
    view.load_sync(doc, "world_models")  # records the restage plan
    world_tris = view.mesh.triangles
    assert geometry.restage_static_models(doc) is not None  # the plan is in place

    # turning the collision overlay on builds a SEPARATE mesh; the world+models plan survives
    view.edit_button.setChecked(True)
    view.collision_button.setChecked(True)
    assert len(view.canvas.renderer.collision_edges) > 0  # the overlay has geometry
    assert view.mesh.triangles is world_tris  # the world mesh was not rebuilt or replaced
    assert geometry.restage_static_models(doc) is not None  # restage plan still usable

    # deleting a clip brush refreshes only the overlay, not the world mesh
    ctl = view._edit_controller
    ctl.show_collision = True
    before_rev = view.canvas.renderer.collision_rev
    first = next(i for i, *_ in ctl.collision_boxes())
    ctl.selected_clip = first
    ctl.delete_selected_clip()
    view.canvas.edited.emit()  # drives _maybe_refresh_collision
    assert view.canvas.renderer.collision_rev > before_rev  # overlay rebuilt
    assert view.mesh.triangles is world_tris  # world mesh untouched by a clip edit
