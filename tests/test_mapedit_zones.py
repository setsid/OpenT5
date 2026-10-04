"""EditSession against a real PS3 map zone (needs the zones named in .env): the GfxWorld sun
GfxLight update, the col_map map_ents path, and a full edit-move-save that verifies and
reparses exactly. Marked ``zones`` so it is left out of the fast suite."""

from __future__ import annotations

import pytest

from opent5 import env
from opent5.edit.document import Document
from opent5.edit.mapedit import EditSession
from opent5.xfile import AssetType, parse

pytestmark = pytest.mark.zones

#: Small stock MP maps to try, in order; the first one found is used.
CANDIDATES = ("mp_firingrange", "mp_nuked", "mp_cracked", "mp_array", "mp_cairo")


def _find_map_zone():
    for folder in env.zone_dirs():
        for name in CANDIDATES:
            path = folder / f"{name}.ff"
            if path.is_file():
                return path
    return None


@pytest.fixture(scope="module")
def map_zone():
    path = _find_map_zone()
    if path is None:
        pytest.skip("no stock MP map zone found in .env zone folders")
    return path


def test_real_map_edits_and_saves(map_zone, tmp_path):
    session = EditSession(Document.open(map_zone))
    assert session.markers(), "a real map carries placed entities"
    ready = {g for g, r in session.gametype_report().items() if r["ready"]}
    assert ready, "a stock map is ready for at least one gametype"

    # move a spawn
    spawn = next(
        (o for o in session.objects if "_spawn" in o.classname and "origin" in o.keys), None
    )
    assert spawn is not None
    ox = spawn.origin
    session.move_object(spawn.id, (ox[0] + 16, ox[1], ox[2]))

    # the GfxWorld sun light, when the zone carries one
    if session.sun_state()["has_gfx_sun"]:
        session.set_sun(color=(0.8, 0.7, 0.6))
        assert session.sun_state()["gfx_color"] == (0.8, 0.7, 0.6)

    out = tmp_path / "edited.ff"
    report = session.save(out)
    assert report.verified, report.problems
    assert report.problems == []
    assert parse(Document.open(out).content).problems() == []


def test_real_map_delete_breaks_mode_refused(map_zone, tmp_path):
    session = EditSession(Document.open(map_zone))
    report = session.gametype_report()
    # find a spawn class that a ready mode needs and that has exactly one instance
    from opent5.convert.entities import SPAWNS

    target = None
    for g, r in report.items():
        if not r["ready"]:
            continue
        for _how, cls, _where in SPAWNS.get(g, ()):
            if r["have"].get(cls) == 1:
                target = (g, cls)
                break
        if target:
            break
    if target is None:
        pytest.skip("no single-instance required spawn to break")
    _g, cls = target
    victim = next(o for o in session.objects if o.classname == cls)
    session.delete_object(victim.id)
    from opent5.edit import EditError

    out = tmp_path / "broken.ff"
    with pytest.raises(EditError):
        session.save(out)
    assert not out.exists()


def test_map_ents_lives_under_col_map(map_zone):
    session = EditSession(Document.open(map_zone))
    # phase 1: the entity string edited is the one under the col_map_mp asset
    a = session.doc.xfile.assets[session._ent_key]
    assert a.type in (AssetType.COL_MAP_MP, AssetType.COL_MAP_SP, AssetType.MAP_ENTS)
