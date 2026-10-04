"""opent5.edit.mapedit.EditSession on a synthetic map zone: the edit model's reversibility,
undo/redo and drag coalescing, saving to a new file with verify, and the gametype entity
rules refusing a save that breaks a mode. No game files needed."""

from __future__ import annotations

import struct

import pytest

from opent5.container.fastfile import split_content, write_fastfile
from opent5.edit import EditError
from opent5.edit.document import Document
from opent5.edit.mapedit import EditSession
from opent5.xfile import AssetType, parse
from test_edit_document import INLINE, header_for, make_content

# A map_ents entity string that makes tdm and dm ready: the common intermission, the three
# tdm spawn classes and a dm spawn, plus a worldspawn to hold sun and fog.
ENTS = (
    '{\n"classname" "worldspawn"\n"sundirection" "0 45 0"\n}\n'
    '{\n"classname" "mp_global_intermission"\n"origin" "0 0 64"\n}\n'
    '{\n"classname" "mp_tdm_spawn_allies_start"\n"origin" "100 0 0"\n}\n'
    '{\n"classname" "mp_tdm_spawn_axis_start"\n"origin" "-100 0 0"\n}\n'
    '{\n"classname" "mp_tdm_spawn"\n"origin" "0 100 0"\n"angles" "0 90 0"\n}\n'
    '{\n"classname" "mp_dm_spawn"\n"origin" "0 -100 0"\n}\n'
)


def mapents_asset(name: bytes, text: bytes):
    head = INLINE + INLINE + struct.pack(">I", len(text) + 1)
    return AssetType.MAP_ENTS, head + name + b"\0" + text + b"\0"


def zone_bytes(text: str = ENTS) -> bytes:
    content = make_content([mapents_asset(b"maps/mp/mp_box.d3dbsp", text.encode("latin-1"))])
    return write_fastfile(header_for(b"mp_box"), [(p, None) for p in split_content(content, 400)])


@pytest.fixture
def ff(tmp_path):
    path = tmp_path / "in" / "mp_box.ff"
    path.parent.mkdir()
    path.write_bytes(zone_bytes())
    return path


@pytest.fixture
def session(ff):
    return EditSession(Document.open(ff))


def _ent_id(session, classname):
    for o in session.objects:
        if o.classname == classname:
            return o.id
    raise AssertionError(f"no {classname} in the session")


def test_parses_and_lists_objects(session):
    classes = [o.classname for o in session.objects]
    assert classes[0] == "worldspawn"
    assert "mp_tdm_spawn" in classes
    # markers are the entities with an origin (worldspawn here has none)
    assert all("origin" in o.keys for o in session.markers())


@pytest.mark.parametrize("op", ["move", "rotate", "property", "add", "delete", "duplicate",
                                "sun", "fog"])  # fmt: skip
def test_each_edit_round_trips_byte_identical(session, op):
    base = session.build()
    spawn = _ent_id(session, "mp_tdm_spawn")
    if op == "move":
        session.move_object(spawn, (10, 20, 30))
    elif op == "rotate":
        session.rotate_object(spawn, (0, 180, 0))
    elif op == "property":
        session.set_property(spawn, "script_label", "_a")
    elif op == "add":
        session.add_object({"classname": "mp_dm_spawn", "origin": "5 5 5"})
    elif op == "delete":
        session.delete_object(_ent_id(session, "mp_dm_spawn"))
    elif op == "duplicate":
        session.duplicate_object(spawn)
    elif op == "sun":
        session.set_sun(direction=(0, 90, 0), color=(0.9, 0.8, 0.7), intensity=1.5)
    elif op == "fog":
        session.set_fog(fogcolor="0.3 0.3 0.4", fogstart="128")
    assert session.build() != base  # the edit changed the zone
    assert session.undo() is not None
    assert session.build() == base  # revert is byte-exact


def test_undo_redo(session):
    base = session.build()
    spawn = _ent_id(session, "mp_tdm_spawn")
    session.move_object(spawn, (1, 2, 3))
    moved = session.build()
    session.undo()
    assert session.build() == base
    session.redo()
    assert session.build() == moved
    assert session.object(spawn).keys["origin"] == "1 2 3"


def test_drag_coalesces_to_one_entry(session):
    base = session.build()
    spawn = _ent_id(session, "mp_tdm_spawn")
    for x in range(1, 6):
        session.move_object(spawn, (x, 0, 0), coalesce=True)
    assert len(session.history()) == 1  # one undo entry for the whole drag
    assert session.object(spawn).keys["origin"] == "5 0 0"
    session.undo()
    assert session.build() == base  # one undo restores the drag's start


def test_set_property_delete_key(session):
    spawn = _ent_id(session, "mp_tdm_spawn")
    assert session.object(spawn).keys.get("angles") == "0 90 0"
    session.set_property(spawn, "angles", None)
    assert "angles" not in session.object(spawn).keys


def test_save_round_trips_and_verifies(session, tmp_path):
    spawn = _ent_id(session, "mp_tdm_spawn")
    session.move_object(spawn, (12, 34, 56))
    session.add_object({"classname": "mp_dm_spawn", "origin": "7 8 9"})
    out = tmp_path / "out" / "mp_box.edited.ff"
    report = session.save(out)
    assert report.verified, report.problems
    assert report.problems == []
    # the saved file reparses exactly and carries the edit
    saved = Document.open(out)
    assert saved.parse_problems == []
    text = saved.text(EditSession(saved)._ent_key)
    assert '"origin" "12 34 56"' in text


def test_default_save_path(session):
    assert session.default_save_path().name == "mp_box.edited.ff"


def test_save_refused_when_delete_breaks_a_mode(session, tmp_path):
    # tdm is ready before; deleting its only mp_tdm_spawn breaks addSpawnPoints for tdm
    session.delete_object(_ent_id(session, "mp_tdm_spawn"))
    out = tmp_path / "out" / "broken.ff"
    with pytest.raises(EditError) as exc:
        session.save(out)
    assert "tdm" in str(exc.value)
    assert not out.exists()  # nothing written


def test_gametype_report_tracks_edits(session):
    assert session.gametype_report()["tdm"]["ready"]
    session.delete_object(_ent_id(session, "mp_dm_spawn"))
    assert not session.gametype_report()["dm"]["ready"]
    assert session.gametype_problems() == [] or any(
        "dm" in p for p in session.gametype_problems()
    )


def test_save_refused_over_source(session):
    with pytest.raises(EditError):
        session.save(session.doc.path)


def test_add_requires_classname(session):
    with pytest.raises(EditError):
        session.add_object({"origin": "0 0 0"})


def test_edited_zone_reparses_with_parse(session):
    spawn = _ent_id(session, "mp_tdm_spawn")
    session.move_object(spawn, (1, 1, 1))
    assert parse(session.build()).problems() == []
