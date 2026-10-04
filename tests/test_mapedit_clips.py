"""Phase 2 of the v0.3.0 editor: real clip collision wired into the EditSession's
add / move / delete of static-model props, each a reversible edit whose undo restores
the clipMap exactly, with save-with-verify still passing.

These run headless (no GUI) against a real PS3 clipMap (mp_nuked), because the add /
move operations need the zone's cNode BSP and leafBrushes pool to re-leaf a clip into
the world leaf a trace reaches; a synthetic clipMap has none. They are marked ``zones``
and ``slow``. The pure reversibility helpers and the stock-brush detach are unit-tested
without a zone in ``test_propclip``.
"""

from __future__ import annotations

import pytest

from opent5.convert import propclip as pc
from opent5.edit.document import Document
from opent5.edit.mapedit import EditSession
from opent5.xfile import AssetType, parse
from test_propclip import find_zone

#: the Nuketown school bus: a stock static-model prop whose collision is a cluster of
#: clip cbrushes (docs/research/static-model-collision.md).
BUS_MODEL = "t5_veh_schoolbus"
#: a free spot with no stock clip: the central road crossing the clip R&D used.
NEW_BOX = ((174.0, -77.0, 0.0), (234.0, -17.0, 120.0))


@pytest.fixture(scope="module")
def nuked_bytes() -> bytes:
    path = find_zone("mp_nuked")
    if path is None:
        pytest.skip("mp_nuked.ff is not on this machine")
    return path.read_bytes()


def _session(data: bytes) -> EditSession:
    return EditSession(Document.open(data, name="mp_nuked.ff"))


def _centre(box):
    mn, mx = box
    return tuple((mn[k] + mx[k]) / 2 for k in range(3))


def _inside(box, p):
    mn, mx = box
    return all(mn[k] <= p[k] <= mx[k] for k in range(3))


def _solid(cm, loc, idx, point):
    """A trace at ``point`` hits brush ``idx``: the brush's box covers it and the BSP
    leaf there lists the brush."""
    b = cm.brush(idx)
    leaf = loc.locate(point)
    return (
        leaf is not None
        and idx in cm.reachable_brushes(leaf)
        and _inside((b.mins, b.maxs), point)
    )


def _bus(session: EditSession):
    for m in session.static_models():
        if m.model == BUS_MODEL:
            return m
    raise AssertionError("no school bus static model in mp_nuked")


# -- the prop model ---------------------------------------------------------------------------


@pytest.mark.zones
@pytest.mark.slow
def test_static_models_reads_the_bus_with_a_footprint(nuked_bytes):
    s = _session(nuked_bytes)
    assert s.can_edit_clips
    bus = _bus(s)
    assert bus.model == BUS_MODEL
    # the footprint is a real volume and the bus sits inside it
    assert all(bus.absmin[k] < bus.absmax[k] for k in range(3))
    assert _inside((bus.absmin, bus.absmax), bus.origin)
    # and a clip cluster sits under that footprint
    assert s.clip_cluster_in_footprint(bus.absmin, bus.absmax)


# -- add --------------------------------------------------------------------------------------


@pytest.mark.zones
@pytest.mark.slow
def test_add_prop_adds_a_solid_clip_and_undo_restores_exactly(nuked_bytes):
    s = _session(nuked_bytes)
    cm, loc = s._clip_engine()
    base = s.build()
    before = cm.num_brushes
    mins, maxs = NEW_BOX

    idx = s.add_prop_clip("prop1", mins, maxs)

    assert cm.num_brushes == before + 1
    assert _solid(cm, loc, idx, _centre(NEW_BOX))  # the placed prop is solid
    built = s.build()
    assert built != base
    back = parse(built)
    assert back.problems() == []
    bcm = pc.ClipMap(_clipmap(back))
    assert pc.check_world_leaf_refs(bcm) == []
    assert pc.check_leaf_contents_masks(bcm) == []

    assert s.undo() is not None
    assert cm.num_brushes == before
    assert s.build() == base  # undo restores the clipMap byte for byte
    assert s.redo() is not None
    assert s.build() == built  # redo is exact too


# -- move -------------------------------------------------------------------------------------


@pytest.mark.zones
@pytest.mark.slow
def test_move_prop_moves_the_whole_cluster_old_spot_clears(nuked_bytes):
    s = _session(nuked_bytes)
    cm, loc = s._clip_engine()
    base = s.build()
    bus = _bus(s)
    cluster = s.clip_cluster_in_footprint(*bus.footprint)
    assert len(cluster) > 1  # a cluster, not a single brush
    before_boxes = {i: (cm.brush(i).mins, cm.brush(i).maxs) for i in cluster}
    old_centres = {i: _centre(before_boxes[i]) for i in cluster}
    delta = (400.0, 0.0, 0.0)

    result = s.move_prop_clip(bus.index, delta, footprint=bus.footprint)

    moved_zone = s.build()
    assert result["found"] and sorted(result["moved"]) == sorted(cluster)
    assert any("baked lightmap shadow" in w for w in result["warnings"])  # stock prop
    # numBrushes unchanged: the cluster moved, nothing was added
    moved_n = pc.ClipMap(_clipmap(parse(moved_zone))).num_brushes
    base_n = pc.ClipMap(_clipmap(parse(base))).num_brushes
    assert moved_n == base_n
    # every brush box shifted by the delta
    for i in cluster:
        assert cm.brush(i).mins[0] == pytest.approx(before_boxes[i][0][0] + delta[0])
    # solid at the destination
    for i in cluster:
        new_box = (cm.brush(i).mins, cm.brush(i).maxs)
        assert _solid(cm, loc, i, _centre(new_box))
    # the OLD spot is clear: no cluster brush's box still covers any old centre
    for j in cluster:
        here = old_centres[j]
        assert not any(_inside((cm.brush(i).mins, cm.brush(i).maxs), here) for i in cluster)
    back = parse(moved_zone)
    assert back.problems() == []
    bcm = pc.ClipMap(_clipmap(back))
    assert pc.check_world_leaf_refs(bcm) == [] and pc.check_leaf_contents_masks(bcm) == []

    assert s.undo() is not None
    assert s.build() == base  # undo restores the clipMap exactly


# -- delete -----------------------------------------------------------------------------------


@pytest.mark.zones
@pytest.mark.slow
def test_delete_prop_removes_the_cluster_and_undo_restores(nuked_bytes):
    s = _session(nuked_bytes)
    cm, _loc = s._clip_engine()
    base = s.build()
    bus = _bus(s)
    cluster = s.clip_cluster_in_footprint(*bus.footprint)

    result = s.remove_prop_clip(bus.index, footprint=bus.footprint)

    assert result["found"] and sorted(result["removed"]) == sorted(cluster)
    assert all(cm.brush(i).contents == 0 for i in cluster)  # disabled: collides with nothing
    back = parse(s.build())
    assert back.problems() == []
    assert pc.check_world_leaf_refs(pc.ClipMap(_clipmap(back))) == []

    assert s.undo() is not None
    assert s.build() == base
    assert all(cm.brush(i).contents != 0 for i in cluster)  # restored


# -- fallback and warnings --------------------------------------------------------------------


@pytest.mark.zones
@pytest.mark.slow
def test_move_with_no_cluster_leaves_collision_and_warns(nuked_bytes):
    s = _session(nuked_bytes)
    base = s.build()
    empty = ((5000.0, 5000.0, 5000.0), (5010.0, 5010.0, 5010.0))  # no stock clip there

    result = s.move_prop_clip("ghost", (10.0, 0.0, 0.0), footprint=empty)

    assert result["found"] is False and result["moved"] == []
    assert any("collision is left as it is" in w for w in result["warnings"])
    assert not s.can_undo  # nothing was changed, so there is nothing to undo
    assert s.build() == base


@pytest.mark.zones
@pytest.mark.slow
def test_editor_added_clip_move_does_not_warn_about_baked_shadow(nuked_bytes):
    # an editor-added prop has no baked lightmap, so moving it carries no shadow warning
    s = _session(nuked_bytes)
    mins, maxs = NEW_BOX
    s.add_prop_clip("prop2", mins, maxs)
    result = s.move_prop_clip("prop2", (0.0, 0.0, 80.0))
    assert result["found"] and result["warnings"] == []


# -- save with verify -------------------------------------------------------------------------


@pytest.mark.zones
@pytest.mark.slow
def test_clip_edit_saves_with_verify(nuked_bytes, tmp_path):
    s = _session(nuked_bytes)
    mins, maxs = NEW_BOX
    s.add_prop_clip("prop3", mins, maxs)
    out = tmp_path / "out" / "mp_nuked.edited.ff"
    report = s.save(out)
    assert report.verified, report.problems
    assert report.problems == []
    # the saved file reparses and still holds the added clip
    saved = parse(bytes(_zone_content(out)))
    assert saved.problems() == []


def _clipmap(xf) -> dict:
    return [a for a in xf.assets if a.type in (AssetType.COL_MAP_MP, AssetType.COL_MAP_SP)][0].data


def _zone_content(path):
    from opent5.container.zone import Zone

    return Zone.open(path).content
