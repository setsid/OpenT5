"""v0.4.0 editor: static-model (prop) move / add / delete made to work for ANY prop, and the
OpenT5 3D viewer made to reflect saved edits.

These drive the real MapEditController and MeshView offscreen against the pinned retail
mp_nuked (never the live .env zone), so they exercise the same calls the GUI makes:

- a move of an ARBITRARY prop (one with no clip cluster) still moves its render, warns that
  the baked collision stays, and the viewer (geometry.with_static_models placements) shows it
  at the new spot after save and reopen;
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

    # a clip-less prop has no cluster to move, but its render moves and it warns
    assert result["found"] is False and result.get("render_moved") is True
    assert any("collision stays" in w.lower() for w in result["warnings"])
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
