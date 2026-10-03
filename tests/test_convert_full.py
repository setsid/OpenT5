"""The full test map (tools/testmap.py --full, docs/demo-box-full.md): every gametype's
objectives, a light grid volume, a primary room light, walls in a material no PS3 zone has
and eight stock props. Quick real-data smoke tests of each converter area on it (one parse
of each zone, shared), and (slow) whole conversions under the base's name and its own.
Skipped when the files are not on this machine; none enters the repository. The PC zone is
looked for at $OPENT5_PC_BOX_FULL, then in the PC Mod Tools' zone folder."""

import os
import sys
from pathlib import Path

import pytest

from opent5 import env
from opent5.convert import entities as en
from opent5.convert import images, smodels
from opent5.convert import materials as mats
from opent5.convert import pc as pcmod
from opent5.convert import scripts as sc

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import testmap  # noqa: E402

STEAM = Path("/mnt/c/Program Files (x86)/Steam/steamapps/common/Call of Duty Black Ops")
PC_FULL = (
    Path(os.environ.get("OPENT5_PC_BOX_FULL", "/nonexistent")),
    STEAM / "zone" / "English" / "mp_opent5box_full.ff",
)
PROPS = sorted(m for m, _, _ in testmap.PROPS)


# -- no files needed --------------------------------------------------------------------------


def test_full_map_text():
    text = testmap.map_text(wall=testmap.FULL_WALL, light_grid=True, props=True)
    assert text.count("lightgrid_volume") == 6
    assert text.count(testmap.FULL_WALL) == 4
    assert text.count('"classname" "misc_model"') == 8
    light = text[text.index('"classname" "light"') :].split("}", 1)[0]
    assert '"spawnflags" "1"' in light and '"target" "room_light_target"' in light
    assert '"targetname" "room_light_target"' in text
    plain = testmap.map_text()
    assert "lightgrid_volume" not in plain and "misc_model" not in plain


def test_props_stand_clear_of_spawns_and_objectives():
    points = [
        tuple(float(v) for v in e["origin"].split()[:2])
        for e in testmap.entity_dicts()
        if "origin" in e and e.get("classname") != "mp_global_intermission"
    ]
    for _, (x, y), _ in testmap.PROPS:
        assert min(((x - a) ** 2 + (y - b) ** 2) ** 0.5 for a, b in points) >= 64


def test_scripts_drop_the_light_grid_tweaks_and_hide_the_compass_grid():
    main = "#include x;\nmain()\n{\nmaps\\mp\\_load::main();\n}\n"
    art = (
        'main()\n{\n\tSetDvar( "r_lightGridEnableTweaks", 1 );\n'
        '\tSetDvar( "r_lightGridIntensity", 1.25 );\n\tSetDvar( "r_lightTweakSunLight", 1 );\n}\n'
    )
    texts = {"maps/mp/mp_x.gsc": main, "maps/mp/createart/mp_x_art.gsc": art}
    own = sc.plan_scripts("mp_x", texts, own_map=True).texts
    assert "r_lightGrid" not in own["maps/mp/createart/mp_x_art.gsc"]
    assert "r_lightTweakSunLight" in own["maps/mp/createart/mp_x_art.gsc"]
    assert own["maps/mp/mp_x.gsc"].endswith(
        'setDvar("compassGridEnabled", 0);\nsetDvar("compassRotation", 0);\n}\n'
    )
    replaced = sc.plan_scripts("mp_x", texts).texts
    assert "compassGridEnabled" not in replaced["maps/mp/mp_x.gsc"]


def test_cli_modes_and_materials_options():
    from opent5.cli import Failure, _modes, build_parser

    args = build_parser().parse_args(
        ["convert", "P.ff", "--base", "mp_nuked", "-o", "o", "--modes", "sd,dom"]
        + ["--pc-game", "G", "--new-material", "wc/x"]
    )
    assert (args.modes, args.pc_game, args.new_material) == ("sd,dom", ["G"], ["wc/x"])
    assert _modes("sd, dom") == ("sd", "dom")
    assert _modes("all") == en.GAMETYPES
    with pytest.raises(Failure, match="hq"):
        _modes("hq")


# -- real files: one parse of each, shared ------------------------------------------------------


def _pc_full() -> Path:
    path = next((p for p in PC_FULL if p.is_file()), None)
    if path is None:
        pytest.skip("the full test map (mp_opent5box_full.ff) is not on this machine")
    return path


def _ps3(name: str) -> Path:
    folder = env.path_of("OPENT5_ZONES")
    path = folder / f"{name}.ff" if folder else None
    if path is None or not path.is_file():
        pytest.skip(f"{name}.ff is not configured")
    return path


@pytest.fixture(scope="module")
def pc():
    from opent5.xfile.remap import Rewrite

    return Rewrite(pcmod.read_pc_fastfile(_pc_full().read_bytes()), platform=pcmod.PC)


@pytest.fixture(scope="module")
def base():
    from opent5.container.zone import Zone
    from opent5.xfile.remap import Rewrite

    return Rewrite(bytes(Zone.open(_ps3("mp_nuked")).content))


def _asset(x, type_name):
    return next(a.data for a in x.assets if a.type_name == type_name)


@pytest.mark.zones
def test_pc_full_map_parses_with_every_part(pc):
    x = pc.xfile
    assert x.problems() == []
    assert sorted(a.name for a in x.assets if a.type_name == "xmodel") == sorted(
        set(PROPS)
        | {"mp_flag_neutral", "mp_supplydrop_hq", "p_glo_bomb_stack"}
        | {"prop_suitcase_bomb", "t5_weapon_briefcase_bomb_world"}
    )
    com = _asset(x, "com_map")
    assert len(com["primary_lights"]) == 3  # empty slot, sun, the room light
    grid = _asset(x, "gfx_map")["light_grid"]
    assert len(grid["colors"]) // 168 > 1000  # cod2rad's grid, not the default colour only


@pytest.mark.zones
def test_full_map_objectives_ready_in_every_mode(pc):
    from opent5.edit.content import mapents_text

    clip = _asset(pc.xfile, "col_map_mp")
    ents = sc.parse_entities(mapents_text(clip["map_ents"]))
    assert not any(e.get("classname") in ("misc_model", "info_null") for e in ents)
    rep = en.report(ents, cmodels=en.cmodel_bounds(clip, "<"))
    assert {g: r["ready"] for g, r in rep.items()} == dict.fromkeys(en.GAMETYPES, True)


@pytest.mark.zones
def test_full_map_wall_material_is_built(pc, base):
    pgfx = _asset(pc.xfile, "gfx_map")
    stock = _asset(base.xfile, "gfx_map")
    r = mats.convert_materials(pgfx, stock, base, files=mats.ImageFiles([STEAM]))
    wall = r.report["materials"]["wc/blockout_test_concrete"]
    assert (wall["techset"], wall["techset_how"]) == ("wc_l_sm_r0c0", "same name")
    image = r.report["images"]["~-gblockout_average_test_c"]
    assert (image["action"], image["size"], image["bytes"]) == ("converted", [512, 512], 0x2AB00)
    assert r.sources["wc/jun_art_concrete_base02"] == "base"


@pytest.mark.zones
def test_full_map_props_are_base_models(pc, base):
    pgfx = dict(_asset(pc.xfile, "gfx_map"))
    words = smodels.model_words(base.xfile)
    res = smodels.convert_static_models(smodels.take(pgfx), words, pc.xfile)
    assert res.report["count"] == 8 and sorted(res.report["models"]) == PROPS
    assert len(res.insts) == 8 * smodels.INST


@pytest.mark.zones
def test_full_map_lightmap_converts(pc):
    gfx = _asset(pc.xfile, "gfx_map")
    node = images.convert_image(gfx["lightmaps"][0][0])
    assert node["name"] == "*lightmap0_primary" and len(node["pixels"]) > 0


# -- whole conversions (slow) -----------------------------------------------------------------


@pytest.mark.zones
@pytest.mark.slow
@pytest.mark.parametrize("name", [None, "mp_opent5box"])
def test_full_map_converts(name):
    from opent5.convert.mapzone import convert_map
    from opent5.convert.world import surface_material_name
    from opent5.edit.content import rawfile_text
    from opent5.xfile import AssetType, parse

    r = convert_map(
        _pc_full().read_bytes(),
        _ps3("mp_nuked"),
        name=name,
        require=en.GAMETYPES,
        image_roots=[STEAM],
    )
    rep = r.report
    checks = rep["checks"]
    assert checks["reparse_exact"] and checks["write_identical"] and checks["unresolved"] == 0
    assert all(v == "ready" for v in rep["gametypes"].values()) and len(rep["gametypes"]) == 12
    assert rep["materials"]["materials"]["wc/blockout_test_concrete"]["action"].startswith("new")
    assert rep["static_models"]["count"] == 8 and rep["static_models"]["collision"] == 8
    assert rep["pointers"]["via_pc"] > 0
    x = parse(r.content, log=False)
    gfx = next(a.data for a in x.assets if a.type == AssetType.GFX_MAP)
    assert len(gfx["smodel_draw_insts"]) == 8
    walls = [s for s in gfx["surfaces"] if surface_material_name(s) == "wc/blockout_test_concrete"]
    assert len(walls) == 1
    scripts = {a.name: rawfile_text(a.data) for a in x.by_type(AssetType.RAWFILE)}
    own = name or "mp_nuked"
    assert "r_lightGrid" not in scripts[f"maps/mp/createart/{own}_art.gsc"]
    assert ("compassGridEnabled" in scripts[f"maps/mp/{own}.gsc"]) == (name is not None)
