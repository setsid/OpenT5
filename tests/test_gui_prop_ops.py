"""v0.4.0 editor: static-model (prop) move / add / delete made to work for ANY prop, and the
OpenT5 3D viewer made to reflect saved edits.

These drive the real MapEditController and MeshView offscreen against the pinned retail
mp_nuked (never the live .env zone), so they exercise the same calls the GUI makes:

- a move of an ARBITRARY prop (one with no clip cluster) still moves its render, adds a fresh
  solid clip at the new footprint, warns that the old baked collision stays, and the viewer
  (geometry.with_static_models placements) shows it at the new spot after save and reopen;
- Add places a CHOSEN placeable model (not just the default crate) and it draws and is solid;
- Delete hides the render (the viewer no longer places it) and removes the clip;
- the view rebuilds the placed-model mesh after an edit, without reopening.

Marked zones + slow: they need a real PS3 clipMap and GfxWorld (the BSP, leafBrushes pool and
the draw instances), which a synthetic zone has none of.
"""

from __future__ import annotations

import os
import struct

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from opent5.edit.document import Document  # noqa: E402
from opent5.edit.mapedit import _DRAW_SCALE, EditSession  # noqa: E402
from opent5.gui import geometry  # noqa: E402
from opent5.gui.backend import ZoneDoc  # noqa: E402
from opent5.gui.views import mesh as mv  # noqa: E402
from opent5.xfile import AssetType, parse  # noqa: E402
from retail_fixtures import retail_zone  # noqa: E402

APP = QApplication.instance() or QApplication(["opent5"])


def _session() -> EditSession:
    return EditSession(Document.open(retail_zone("mp_nuked").read_bytes(), name="mp_nuked.ff"))


def _clipless_prop(s: EditSession):
    """An arbitrary prop that draws (has a GfxWorld draw instance at its origin) but carries
    no clip cluster: the common case the old editor refused to move."""
    for p in s.static_models():
        if (
            s._draw_inst_at_origin(p.origin) is not None
            and not s.clip_cluster_in_footprint(*p.footprint)
        ):
            return p
    pytest.skip("no clip-less drawing prop in this mp_nuked build")


def _zone_content(path) -> bytes:
    from opent5.container.zone import Zone

    return Zone.open(path).content


def _placements(zone_bytes: bytes):
    back = parse(zone_bytes)
    assert back.problems() == []
    gfx = next(a for a in back.assets if a.type == AssetType.GFX_MAP)
    return back, geometry._helper(back).static_models(gfx.data)


# -- move: any prop moves its render, the viewer reflects it ----------------------------------


@pytest.mark.zones
@pytest.mark.slow
def test_move_clipless_prop_moves_render_and_viewer_reflects(tmp_path):
    s = _session()
    prop = _clipless_prop(s)
    j = s._draw_inst_at_origin(prop.origin)
    assert j is not None
    old = prop.origin
    ctl = mv.MapEditController(s)
    ctl.selected_prop = prop.index

    result = ctl.move_selected_prop((512.0, 0.0, 0.0))

    # a clip-less prop has no cluster to move, but its render moves, and the bundle move adds a
    # fresh clip at the new footprint so it is solid there (when the new spot is inside the
    # BSP); either way it warns that the old baked-triangle collision stays behind.
    assert result["found"] is False and result.get("render_moved") is True
    assert any("baked" in w.lower() and "old position" in w.lower() for w in result["warnings"])
    new = (old[0] + 512.0, old[1], old[2])
    assert s._draw_inst_at_origin(old) is None  # the draw instance left the old spot
    assert s._draw_inst_at_origin(new) is not None  # and is at the new one

    out = tmp_path / "moved" / "mp_nuked.edited.ff"
    report = s.save(out)
    assert report.verified, report.problems
    _back, placements = _placements(bytes(_zone_content(out)))
    # the viewer places this prop from its draw instance: it now reads the new origin
    assert placements[j]["origin"][0] == pytest.approx(new[0], abs=0.1)

    assert s.undo() is not None
    assert s._draw_inst_at_origin(old) is not None  # exact undo


# -- add: a chosen placeable model, drawn and solid ------------------------------------------


@pytest.mark.zones
@pytest.mark.slow
def test_add_chosen_model_draws_and_is_solid(tmp_path):
    s = _session()
    ctl = mv.MapEditController(s)
    models = ctl.placeable_models()
    assert len(models) > 1
    default = ctl.default_crate_model()
    chosen = next(m for m in models if m != default)  # not the default: any choice places
    n_props = len(ctl.props())
    n_draws = len(s._draw_insts())

    result = ctl.add_prop(chosen, (204.0, -47.0, 60.0), (30.0, 30.0, 60.0))

    assert ctl.selected_prop == result["prop"]
    assert not result["warnings"]  # a placeable model clones a draw instance, so it draws
    assert len(ctl.props()) == n_props + 1
    assert len(s._draw_insts()) == n_draws + 1
    added = ctl.selected_prop_obj()
    assert s.clip_cluster_in_footprint(*added.footprint)  # solid: a clip under its footprint

    out = tmp_path / "added" / "mp_nuked.edited.ff"
    report = s.save(out)
    assert report.verified, report.problems
    _back, placements = _placements(bytes(_zone_content(out)))
    # the viewer places the chosen model at the new spot
    assert any(
        p["model"] == chosen and abs(p["origin"][0] - 204.0) < 40 and p["scale"]
        for p in placements
    )


# -- delete: the render is hidden, the viewer drops it, the clip is removed -------------------


@pytest.mark.zones
@pytest.mark.slow
def test_delete_prop_hides_render_and_viewer_drops_it(tmp_path):
    s = _session()
    prop = _clipless_prop(s)
    j = s._draw_inst_at_origin(prop.origin)
    assert j is not None
    ctl = mv.MapEditController(s)
    ctl.selected_prop = prop.index

    result = ctl.delete_selected_prop_clip()

    assert result.get("render_hidden") is True
    assert struct.unpack_from(">f", s._draw_raw(j), _DRAW_SCALE)[0] == 0.0  # degenerate

    out = tmp_path / "deleted" / "mp_nuked.edited.ff"
    report = s.save(out)
    assert report.verified, report.problems
    _back, placements = _placements(bytes(_zone_content(out)))
    assert placements[j]["scale"] == 0  # the viewer skips a scale-0 placement
    # the viewer path (with_static_models) really drops it: one fewer model is placed
    back = parse(bytes(_zone_content(out)))
    gfx = next(a for a in back.assets if a.type == AssetType.GFX_MAP)
    world = geometry.world_mesh(back, gfx)
    placed = geometry.with_static_models(back, gfx, world)
    drawn = sum(1 for p in placements if p["scale"])
    assert any(f"{drawn} static models" == note for note in placed.notes)

    assert s.undo() is not None
    assert struct.unpack_from(">f", s._draw_raw(j), _DRAW_SCALE)[0] != 0.0  # restored


@pytest.mark.zones
@pytest.mark.slow
def test_delete_clustered_prop_removes_the_clip():
    s = _session()
    prop = next(
        (p for p in s.static_models() if s.clip_cluster_in_footprint(*p.footprint)), None
    )
    if prop is None:
        pytest.skip("no clustered prop in this build")
    cluster = s.clip_cluster_in_footprint(*prop.footprint)
    cm, _loc = s._clip_engine()
    ctl = mv.MapEditController(s)
    ctl.selected_prop = prop.index

    result = ctl.delete_selected_prop_clip()

    assert result["found"] and sorted(result["removed"]) == sorted(cluster)
    assert all(cm.brush(i).contents == 0 for i in cluster)  # clip disabled
    assert parse(s.build()).problems() == []
    assert s.undo() is not None
    assert all(cm.brush(i).contents != 0 for i in cluster)  # restored


# -- live refresh: the view rebuilds the placed-model mesh after an edit ----------------------


@pytest.mark.zones
@pytest.mark.slow
def test_view_rebuilds_models_mesh_after_a_prop_move():
    import numpy as np

    doc = ZoneDoc.open(retail_zone("mp_nuked"))
    assert doc.backend == "edit"
    view = mv.MeshView()
    view.load_sync(doc, "world_models")
    assert view.mesh is not None and view.mesh_kind == "world_models"

    session = view._ensure_session()
    assert session is not None
    ctl = mv.MapEditController(session)
    view._edit_controller = ctl
    view._last_prop_rev = ctl._prop_rev
    view.canvas.set_controller(ctl)

    prop = _clipless_prop(session)
    far_x = prop.origin[0] + 100000.0  # well past every bit of the map's geometry

    def near_far_spot(mesh) -> int:
        px = mesh.positions
        return int(np.count_nonzero(np.abs(px[:, 0] - far_x) < 300.0))

    assert near_far_spot(view.mesh) == 0  # nothing out there before the move

    ctl.selected_prop = prop.index
    ctl.move_selected_prop((100000.0, 0.0, 0.0))  # move the prop's model out to the empty spot
    view.canvas.edited.emit()  # the edit signal drives the live refresh

    # the rebuilt mesh now carries the moved model's vertices at the new, far-off spot
    assert near_far_spot(view.mesh) > 0


# -- performance: a move refreshes in place, not a whole-mesh rebuild ------------------------


def _drawing_prop(s: EditSession):
    for p in s.static_models():
        if s._draw_inst_at_origin(p.origin) is not None:
            return p
    pytest.skip("no drawing prop in this mp_nuked build")


@pytest.mark.zones
@pytest.mark.slow
def test_prop_move_restages_in_place_matching_a_full_rebuild():
    """A prop move keeps the mesh topology (same props, same triangles), so the view restages
    only the moved prop's vertices rather than rebuilding the whole world+models mesh. The
    result must be identical to a full rebuild, and must reuse the existing triangle array
    (proof no rebuild happened)."""
    import time

    import numpy as np

    doc = ZoneDoc.open(retail_zone("mp_nuked"))
    view = mv.MeshView()
    view.load_sync(doc, "world_models")  # records the restage plan
    session = view._ensure_session()
    ctl = mv.MapEditController(session)
    prop = _drawing_prop(session)
    ctl.selected_prop = prop.index

    before_tris = view.mesh.triangles
    before_pos = view.mesh.positions.copy()
    ctl.move_selected_prop((777.0, -321.0, 44.0))

    s = time.perf_counter()
    inc = geometry.restage_static_models(doc)
    restage_ms = (time.perf_counter() - s) * 1000.0
    assert inc is not None  # a move takes the fast, in-place path
    assert inc.triangles is before_tris  # the triangle array is reused: no mesh rebuild
    assert not np.array_equal(inc.positions, before_pos)  # but the moved prop's verts shifted

    # identical to a full rebuild from the edited placements
    world = geometry.mesh(doc, "world", None)
    asset = geometry._pick(doc.xfile, geometry.WORLD_TYPES, None, "world")
    s = time.perf_counter()
    full = geometry.with_static_models(doc.xfile, asset, world)
    full_ms = (time.perf_counter() - s) * 1000.0
    assert np.array_equal(inc.positions, full.positions)
    assert np.array_equal(inc.normals, full.normals)
    assert np.array_equal(inc.triangles, full.triangles)
    assert np.array_equal(inc.groups, full.groups)
    assert np.array_equal(inc.uvs, full.uvs)
    assert inc.materials == full.materials
    assert restage_ms < full_ms  # and it is cheaper than the whole-mesh rebuild it replaces


@pytest.mark.zones
@pytest.mark.slow
def test_restage_changed_ranges_cover_exactly_the_moved_prop():
    """restage records the vertex rows it rewrote so the shaded view can re-upload only those
    to the GPU. The ranges must cover exactly the vertices that actually moved, and patching a
    fresh full-build interleaved buffer over those rows must reproduce the restaged vertices (so
    a partial GPU upload lands the same geometry a full re-upload would)."""
    import numpy as np

    from opent5.gui import glrender

    doc = ZoneDoc.open(retail_zone("mp_nuked"))
    view = mv.MeshView()
    view.load_sync(doc, "world_models")
    before_pos = view.mesh.positions.copy()
    session = view._ensure_session()
    ctl = mv.MapEditController(session)
    prop = _drawing_prop(session)
    ctl.selected_prop = prop.index

    asset = geometry._pick(doc.xfile, geometry.WORLD_TYPES, None, "world")
    ctl.move_selected_prop((777.0, -321.0, 44.0))
    inc = geometry.restage_static_models(doc)
    assert inc is not None
    ranges = geometry.restage_changed_ranges(doc)
    assert ranges  # the move was recorded

    # The ranges cover exactly the rows whose vertices changed, nothing more, nothing less.
    covered = np.zeros(len(inc.positions), bool)
    for start, count in ranges:
        covered[start : start + count] = True
    moved = np.any(inc.positions != before_pos, axis=1)
    assert np.array_equal(covered, moved)

    # a partial GPU upload rebuilds the same interleaved buffer a full rebuild would.
    world = geometry.mesh(doc, "world", None)
    full = geometry.with_static_models(doc.xfile, asset, world)
    scene_before = glrender.build_scene(
        view.mesh.positions, view.mesh.triangles, view.mesh.normals, view.mesh.uvs, None, []
    )
    patched = scene_before.interleaved.copy()
    for start, count in ranges:
        patched[start : start + count, 0:3] = inc.positions[start : start + count]
        patched[start : start + count, 3:6] = inc.normals[start : start + count]
    full_scene = glrender.build_scene(
        full.positions, full.triangles, full.normals, full.uvs, None, []
    )
    assert np.array_equal(patched[:, 0:3], full_scene.interleaved[:, 0:3])
    assert np.array_equal(patched[:, 3:6], full_scene.interleaved[:, 3:6])


@pytest.mark.zones
@pytest.mark.slow
def test_update_scene_vertices_falls_back_without_uploaded_scene():
    """With no uploaded shaded scene (the headless case, and before the first shaded frame),
    the incremental GPU patch declines so the caller does the full rebuild instead."""
    doc = ZoneDoc.open(retail_zone("mp_nuked"))
    view = mv.MeshView()
    view.load_sync(doc, "world_models")
    assert view.canvas.update_scene_vertices(view.mesh, [(0, 10)]) is False


@pytest.mark.zones
@pytest.mark.slow
def test_prop_add_falls_back_to_full_rebuild():
    """Adding a prop changes which models draw (the mesh topology), so the in-place restage
    declines and the view does the full rebuild."""
    doc = ZoneDoc.open(retail_zone("mp_nuked"))
    view = mv.MeshView()
    view.load_sync(doc, "world_models")
    session = view._ensure_session()
    ctl = mv.MapEditController(session)
    models = ctl.placeable_models()
    if not models:
        pytest.skip("no placeable model in this build")

    ctl.add_prop(models[0], (0.0, 0.0, 5000.0), (16.0, 16.0, 16.0))
    assert geometry.restage_static_models(doc) is None  # topology changed: full rebuild needed


@pytest.mark.zones
@pytest.mark.slow
def test_marker_projection_culls_offscreen_and_caches():
    """The overlay projects every prop and marker in one pass, culls the ones behind the
    camera, and caches the result while the camera and the scene hold, so repainting on every
    mouse move does not reproject the whole scene."""
    import numpy as np

    doc = ZoneDoc.open(retail_zone("mp_nuked"))
    view = mv.MeshView()
    view.load_sync(doc, "world_models")
    session = view._ensure_session()
    ctl = mv.MapEditController(session)
    cam = view.canvas.camera
    view.canvas.frame_scene()
    w, h = 1200, 800

    # a point behind the camera projects to NaN (culled); the target in front projects
    eye, _right, _up, forward = cam.basis()
    behind = eye - forward * 1000.0
    pts, _depths = ctl._project_all(np.array([behind, cam.target], np.float64), cam, w, h)
    assert np.isnan(pts[0, 0])  # behind the camera: dropped
    assert not np.isnan(pts[1, 0])  # in front: kept

    # the projection is cached while the camera holds, and reprojected when it moves
    a = ctl.prop_screen(cam, w, h)
    assert ctl.prop_screen(cam, w, h) is a  # reused, not reprojected
    cam.yaw += 0.5
    assert ctl.prop_screen(cam, w, h) is not a  # camera moved: fresh projection

    # an edit invalidates the cache so the moved prop reprojects
    before = ctl.prop_screen(cam, w, h)
    ctl.selected_prop = _drawing_prop(session).index
    ctl.move_selected_prop((10.0, 0.0, 0.0))
    assert ctl.prop_screen(cam, w, h) is not before
