"""Gametype entity rules (opent5.convert.entities) and the test map generator
(tools/testmap.py): every MP gametype ready on the generated box, each rule failing when its
entity is taken away, and (with the files present) the compiled PC box and the converted
zone carrying the entities and brush models."""

import copy
import os
import sys
from pathlib import Path

import pytest

from opent5.convert import entities as en
from opent5.convert import scripts

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import testmap  # noqa: E402

STEAM = Path("/mnt/c/Program Files (x86)/Steam/steamapps/common/Call of Duty Black Ops")
PC_MODES = (
    Path(os.environ.get("OPENT5_PC_BOX_MODES", "/nonexistent")),
    STEAM / "zone" / "English" / "mp_opent5box_modes.ff",
)


@pytest.fixture(scope="module")
def box():
    return testmap.compiled_entities()


def _without(ents, **match):
    return [e for e in ents if any(e.get(k) != v for k, v in match.items())]


def _missing(ents, cmodels, gametype):
    return en.check(ents, gametype, cmodels).missing


def test_every_gametype_ready_on_the_box(box):
    ents, cmodels = box
    rep = en.report(ents, cmodels=cmodels, xmodels={"p_glo_bomb_stack", "prop_suitcase_bomb"})
    assert set(rep) == set(en.GAMETYPES) and len(rep) == 12
    assert {g: r["ready"] for g, r in rep.items()} == dict.fromkeys(en.GAMETYPES, True)
    assert rep["koth"]["warnings"] == [
        "script_model 'mp_supplydrop_hq': not in the zone or " "the known common_mp models"
    ]
    assert rep["sd"]["have"]["bombzone"] == 2 and rep["dom"]["have"]["flag_primary"] == 3
    # the presence table in scripts agrees
    assert all(not m for m in scripts.gametype_report(ents, list(en.GAMETYPES)).values())


def test_gameobject_filter(box):
    ents, _ = box
    tdm = en.filtered(ents, "tdm")
    assert not [e for e in tdm if "script_gameobjectname" in e]
    sd = {e.get("targetname") for e in en.filtered(ents, "sd")}
    assert {"bombzone", "sd_bomb", "sd_bomb_pickup_trig"} <= sd
    assert not sd & {"sab_bomb", "flag_primary", "hq_hardpoint", "ctf_flag_pickup_trig"}
    dem = {e.get("targetname") for e in en.filtered(ents, "dem")}
    assert "bombzone" in dem and "sd_bomb" in dem  # dem.gsc deletes sd_bomb itself
    koth = {e.get("script_gameobjectname") for e in en.filtered(ents, "koth")}
    assert koth == {None, "hq"}
    assert en.allowed("koth", "mp_kowloon") == ("hq", "koth")
    assert en.survives({"script_gameobjectname": "[all_modes]"}, ("tdm",))
    assert en.survives({"script_gameobjectname": "twar dom hq sd dem"}, ("hq",))


def test_spawn_rules(box):
    ents, cm = box
    m = _missing(_without(ents, classname="mp_sd_spawn_defender"), cm, "sd")
    assert m == [
        "mp_sd_spawn_defender: none (patch_mp sd.gsc onStartGameType 184: "
        "placeSpawnPoints aborts the level)"
    ]
    assert (
        "mp_global_intermission"
        in _missing(_without(ents, classname="mp_global_intermission"), cm, "dm")[0]
    )
    no_dm = _without(ents, classname="mp_dm_spawn")
    assert _missing(no_dm, cm, "oic")
    wager = [*no_dm, {"classname": "mp_wager_spawn", "origin": "0 0 8"}]
    assert not _missing(wager, cm, "oic")
    assert en.check(wager, "gun", cm).have["mp_wager_spawn"] == 1


def test_sd_and_dem_rules(box):
    ents, cm = box
    two = [*ents, {"classname": "script_model", "targetname": "sd_bomb", "model": "x"}]
    assert any("getEnt needs exactly one" in t for t in _missing(two, cm, "sd"))
    no_defuse = _without(ents, targetname="bombzone_a_defuse")
    assert any("defuse trigger" in t for t in _missing(no_defuse, cm, "sd"))
    assert any("defuse trigger" in t for t in _missing(no_defuse, cm, "dem"))
    walk_in = copy.deepcopy(ents)
    for e in walk_in:
        if e.get("targetname") == "bombzone":
            e["classname"] = "trigger_multiple"
    assert any("walk-in" in t for t in _missing(walk_in, cm, "sd"))
    for e in walk_in:
        if e.get("targetname") == "bombzone":
            e["classname"], e["script_label"] = "trigger_use_touch", "_c"
    assert any("script_label '_c'" in t for t in _missing(walk_in, cm, "dem"))
    assert any(
        "no bomb sites" in t for t in _missing(_without(ents, targetname="bombzone"), cm, "sd")
    )


def test_dom_ctf_sab_koth_rules(box):
    ents, cm = box
    one_flag = [
        e
        for e in ents
        if not (e.get("targetname") == "flag_primary" and e.get("script_label") != "_a")
    ]
    assert "at least 2" in _missing(one_flag, cm, "dom")[0]
    bad_link = copy.deepcopy(ents)
    for e in bad_link:
        if e.get("script_linkname") == "flag_a":
            e["script_linkto"] = "flag_z"
    assert any("flag_z" in t for t in _missing(bad_link, cm, "dom"))
    assert any(
        "flag_descriptor: none" in t
        for t in _missing(_without(ents, targetname="flag_descriptor"), cm, "dom")
    )
    one = [
        e
        for e in ents
        if not (e.get("targetname") == "ctf_flag_zone_trig" and e.get("script_team") == "axis")
    ]
    assert any("ctf_flag_zone_trig: 1, exactly 2" in t for t in _missing(one, cm, "ctf"))
    assert any(
        "sab_bomb_axis" in t
        for t in _missing(_without(ents, targetname="sab_bomb_axis"), cm, "sab")
    )
    moved = copy.deepcopy(ents)
    for e in moved:
        if e.get("targetname") == "hq_hardpoint":
            e["origin"] = "0 0 16"
            break
    assert any("inside no radiotrigger" in t for t in _missing(moved, cm, "koth"))
    assert en.check(moved, "koth").warnings  # bounds unknown without the brush models
    assert any("brush model *13, the clipMap has 13" in t for t in _missing(ents, cm[:13], "koth"))


def test_map_text_and_layout():
    text = testmap.map_text()
    assert text.startswith("iwmap 4\n")
    assert text.count(" trigger 64 64 ") == 6 * 14  # 14 trigger brushes, six faces each
    assert text.count('"classname" "light"') == 1
    plain = testmap.map_text(objectives=False)
    assert " trigger " not in plain
    ents, cm = testmap.compiled_entities(half=1024, objectives=False)
    assert len(cm) == 1 and cm[0] == ((-1041, -1041, -17), (1041, 1041, 273))
    rep = en.report(ents, cmodels=cm)
    assert [g for g, r in rep.items() if r["ready"]] == ["dm", "tdm", "oic", "gun", "shrp", "hlnd"]
    assert "sd_bomb" in str(rep["sd"]["missing"])
    for e in testmap.compiled_entities()[0]:
        x, y, z = (float(v) for v in e.get("origin", "0 0 0").split())
        assert -512 < x < 512 and -512 < y < 512 and 0 <= z < 256


@pytest.mark.zones
def test_compiled_box_matches_the_generator():
    path = next((p for p in PC_MODES if p.is_file()), None)
    if path is None:
        pytest.skip("the compiled PC box with objectives is not on this machine")
    from opent5.convert import pc as pcmod
    from opent5.edit.content import mapents_text

    x = pcmod.parse_pc(pcmod.read_pc_fastfile(path.read_bytes()))
    assert x.problems() == []
    clip = next(a for a in x.assets if a.type_name == "col_map_mp").data
    real = scripts.parse_entities(mapents_text(clip["map_ents"]))
    real = [{k: v for k, v in e.items() if k != "gndLt"} for e in real]
    ents, cm = testmap.compiled_entities()
    assert real == ents
    assert en.cmodel_bounds(clip, "<") == [
        (tuple(map(float, lo)), tuple(map(float, hi))) for lo, hi in cm
    ]
