"""A converted map under its own name (docs/research/map-registration.md): the name rules,
the renaming of the base zone's map-named assets and script paths, and the map table row
in patch_mp. The unit tests need no game files; the patch_mp test needs the update's
patch_mp.ff (zones), the full conversion the PC box map as well (slow)."""

from pathlib import Path

import pytest

from opent5 import env
from opent5.convert import mapname, register
from opent5.convert.world import ConvertError
from opent5.xfile import AssetType

R, T = AssetType.RAWFILE, AssetType.STRINGTABLE


# -- name rules ------------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["mp_opent5box", "mp_a", "mp_box_2", "mp_" + "x" * 20])
def test_good_names_pass(name):
    assert mapname.validate(name, "mp_nuked") == name


@pytest.mark.parametrize(
    ("name", "why"),
    [
        ("MP_BOX", "lower case"),
        ("opent5box", "'mp_'"),
        ("mp_", "'mp_'"),
        ("mp_box-1", "lower case"),
        ("mp_" + "x" * 21, "at most 23"),
        ("mp_box_load", "_load"),
        ("mp_box_patch", "_patch"),
        ("mp_nuked", "does not already use"),
        ("mp_kowloon", "does not already use"),
        ("mp_background", "does not already use"),
    ],
)
def test_bad_names_are_refused_with_the_rule(name, why):
    with pytest.raises(ConvertError, match=why):
        mapname.validate(name, "mp_nuked")


# -- renaming --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kind", "old", "new"),
    [
        (R, "maps/mp/mp_nuked.gsc", "maps/mp/mp_box.gsc"),
        (R, "maps/mp/mp_nuked_fx.gsc", "maps/mp/mp_box_fx.gsc"),
        (R, "maps/mp/mp_nuked_platform.gsc", "maps/mp/mp_box_platform.gsc"),
        (R, "maps/mp/createfx/mp_nuked_fx.gsc", "maps/mp/createfx/mp_box_fx.gsc"),
        (R, "maps/mp/createart/mp_nuked_art.gsc", "maps/mp/createart/mp_box_art.gsc"),
        (R, "clientscripts/mp/mp_nuked.csc", "clientscripts/mp/mp_box.csc"),
        (
            R,
            "clientscripts/mp/mp_nuked_amb_platform.csc",
            "clientscripts/mp/mp_box_amb_platform.csc",
        ),
        (R, "clientscripts/mp/createfx/mp_nuked_fx.csc", "clientscripts/mp/createfx/mp_box_fx.csc"),
        (R, "sun/mp_nuked.sun", "sun/mp_box.sun"),
        (R, "exposure/mp_nuked.xpo", "exposure/mp_box.xpo"),
        (
            T,
            "mp/configstrings/configstrings_ps3_mp_nuked_tdm.csv",
            "mp/configstrings/configstrings_ps3_mp_box_tdm.csv",
        ),
    ],
)
def test_map_named_assets_are_renamed(kind, old, new):
    assert mapname.asset_rename(kind, old, "mp_nuked", "mp_box") == new


@pytest.mark.parametrize(
    ("kind", "old"),
    [
        (R, "maps/mp/mp_nuked2.gsc"),  # another map whose name starts the same way
        (R, "vision/mp_nuked.vision"),  # in common_mp; the art script still sets it
        (R, "maps/mp/_load.gsc"),
        (R, "mpbody/camo_mp.gsc"),
        (R, "mp_nuked"),  # named through the gfx_map base name, which is renamed
        (T, "mp/mapstable.csv"),
        (AssetType.FX, "maps/mp_maps/fx_mp_nuked_glint"),
        (AssetType.XMODEL, "mp_nuked_fence"),
    ],
)
def test_other_names_stay(kind, old):
    assert mapname.asset_rename(kind, old, "mp_nuked", "mp_box") is None


def test_script_paths_are_renamed_and_nothing_else():
    text = (
        "#include maps\\mp\\_utility;\n"
        "#include maps\\mp\\mp_nuked_fx;\n"
        "main()\n{\n"
        "maps\\mp\\mp_nuked_fx::main();\n"
        "maps\\mp\\createart\\mp_nuked_art::main();\n"
        "maps\\mp\\createfx\\mp_nuked_fx::main();\n"
        "thread clientscripts\\mp\\mp_nuked_amb::main();\n"
        "maps\\mp\\mp_nuked2::main();\n"
        'maps\\mp\\_compass::setupMiniMap("compass_map_mp_nuked");\n'
        'VisionSetNaked( "mp_nuked", 1 );\n'
        'level._effect["x"] = loadfx("maps/mp_maps/fx_mp_nuked_glint");\n'
        "}\n"
    )
    out, count = mapname.script_text(text, "mp_nuked", "mp_box")
    assert count == 5
    assert "maps\\mp\\mp_box_fx;" in out
    assert "maps\\mp\\createart\\mp_box_art::main();" in out
    assert "maps\\mp\\createfx\\mp_box_fx::main();" in out
    assert "clientscripts\\mp\\mp_box_amb::main();" in out
    for kept in ("mp_nuked2::", "compass_map_mp_nuked", '"mp_nuked"', "fx_mp_nuked_glint"):
        assert kept in out


# -- the map table ---------------------------------------------------------------------------

HEAD = ["a0", "b1", "c2", "d3", "e4", "f5", "g6", "h7", "i8", "j9", "k10"] + [""] * 5


def _row(name, index, key, pack="0"):
    return [
        name,
        "urbanspecops",
        "urbanspecops",
        key,
        f"menu_{name}_map_select_final",
        str(index),
        f"MPUI_DESC_MAP_{key[5:]}",
        f"compass_overlay_map_{name[3:]}",
        "SMALL",
        "NO",
        "YES",
        pack,
        "MPUI_SPECOPS_SHORT",
        "MPUI_RUSSIAN_SHORT",
        "ops",
        "spets",
    ]


def _table(count=3):
    rows = [HEAD, ["maxnum_map", str(count)] + [""] * 14]
    rows.append(_row("mp_array", 0, "MPUI_array"))
    rows.append(_row("mp_nuked", 1, "MPUI_NUKED"))
    rows.append(_row("mp_kowloon", 2, "MPUI_KOWLOON", pack="2"))
    return rows


def test_new_row_copies_the_base_and_takes_the_next_index():
    plan = register.plan_row(_table(), "mp_box", "mp_nuked", "warmuseum")
    assert plan.row is None and plan.maxnum == 4 and plan.maxnum_row == 1
    base = _row("mp_nuked", 1, "MPUI_NUKED")
    want = list(base)
    want[0], want[3], want[5], want[6], want[11] = (
        "mp_box",
        "MPUI_WARMUSEUM",
        "3",
        "MPUI_DESC_MAP_WARMUSEUM",
        "0",
    )
    assert plan.values == want
    assert plan.keys == ("MPUI_WARMUSEUM", "MPUI_WARMUSEUM_CAPS", "MPUI_DESC_MAP_WARMUSEUM")


def test_the_map_list_offers_the_new_map_with_the_base_maps():
    rows = _table()
    plan = register.plan_row(rows, "mp_box", "mp_nuked")
    rows = rows + [plan.values]
    rows[1][1] = str(plan.maxnum)
    assert register.offered(rows) == ["mp_array", "mp_nuked", "mp_box"]
    assert register.offered(rows, show_dlc=True) == ["mp_kowloon"]
    entry = register.entries(rows)[3]
    assert (entry.index, entry.name, entry.key, entry.splitscreen, entry.pack) == (
        3,
        "mp_box",
        "MPUI_WARMUSEUM",
        True,
        0,
    )


def test_rows_beyond_maxnum_are_not_listed():
    rows = _table(count=2)
    assert register.offered(rows) == ["mp_array", "mp_nuked"]


def test_an_existing_row_is_updated_in_place():
    rows = _table()
    plan = register.plan_row(rows, "mp_box", "mp_nuked")
    rows = rows + [plan.values]
    rows[1][1] = "4"
    again = register.plan_row(rows, "mp_box", "mp_nuked", "warmuseum")
    assert again.row == 5 and again.maxnum is None and again.values[5] == "3"


@pytest.mark.parametrize(
    ("change", "why"),
    [
        (lambda rows: rows.pop(3), "base map"),
        (lambda rows: rows.pop(1), "maxnum_map"),
        (lambda rows: rows[1].__setitem__(1, "128"), "128 maps at most"),
        (lambda rows: rows[2].__setitem__(3, "MPUI_WARMUSEUM"), "already used"),
        (lambda rows: rows[2].__setitem__(5, "3"), "index 3"),
        (lambda rows: rows.__setitem__(0, HEAD[:15]), "16 columns"),
    ],
)
def test_bad_tables_are_refused(change, why):
    rows = _table()
    change(rows)
    with pytest.raises(ConvertError, match=why):
        register.plan_row(rows, "mp_box", "mp_nuked")


def test_unknown_slot_and_bad_name_are_refused():
    with pytest.raises(ConvertError, match="ui slot"):
        register.plan_row(_table(), "mp_box", "mp_nuked", "nowhere")
    with pytest.raises(ConvertError, match="lower case"):
        register.plan_row(_table(), "MP_BOX", "mp_nuked")


def test_default_title():
    assert register.default_title("mp_opent5box") == "Opent5box"
    assert register.default_title("mp_two_words") == "Two words"


def test_cli_takes_the_name_options():
    from opent5.cli import build_parser

    args = build_parser().parse_args(
        ["convert", "PC.ff", "--base", "mp_nuked", "--name", "mp_box", "-o", "out", "--copy-pak"]
    )
    assert (args.name, args.ui_slot, args.register, args.copy_pak) == (
        "mp_box",
        "warmuseum",
        False,
        True,
    )


# -- real files ------------------------------------------------------------------------------


def _patch_mp() -> Path:
    folder = env.path_of("OPENT5_PATCH_ZONES")
    path = folder / "patch_mp.ff" if folder else None
    if path is None or not path.is_file():
        pytest.skip("the update's patch_mp.ff is not configured")
    return path


@pytest.mark.zones
def test_patch_mp_registers_the_map(tmp_path):
    from opent5.edit import Document

    out = tmp_path / "patch_mp.ff"
    report = register.register_map(
        _patch_mp(), out, "mp_opent5box", "mp_nuked", "OpenT5 Box", "A box.", "warmuseum"
    )
    assert report["verified"] and report["problems"] == []
    assert report["assets_changed"] == 4  # the table and the three localize entries
    assert report["entry"] == {
        "index": 26,
        "name": "mp_opent5box",
        "key": "MPUI_WARMUSEUM",
        "splitscreen": True,
        "pack": 0,
    }
    doc = Document.open(out)
    rows = doc.table(doc.find(register.TABLE, "stringtable").index)
    assert rows[1][:2] == ["maxnum_map", "27"]
    assert register.offered(rows)[-1] == "mp_opent5box"
    assert len(register.offered(rows)) == 15  # the 14 disc maps and the new one
    texts = {k: doc.localize(doc.find(k, "localize").index)[1] for k in register.SLOTS["warmuseum"]}
    assert list(texts.values()) == ["OpenT5 Box", "OPENT5 BOX", "A box."]


@pytest.mark.zones
@pytest.mark.slow
def test_the_box_converts_under_its_own_name():
    from opent5.convert.mapzone import convert_map
    from opent5.edit.content import rawfile_text
    from opent5.xfile import parse
    from test_convert_box import PC_BOX, _first, _ps3

    pc = _first(PC_BOX)
    if pc is None:
        pytest.skip("the PC box map (mp_opent5box.ff) is not on this machine")
    result = convert_map(pc.read_bytes(), _ps3("mp_nuked"), name="mp_opent5box")
    assert result.zone_name == "mp_opent5box"
    checks = result.report["checks"]
    assert checks["reparse_exact"] and checks["write_identical"] and checks["unresolved"] == 0
    assert result.report["entities"]["added_for_the_stock_script"] == []
    x = parse(result.content)
    world = [a for a in x.assets if a.type in (13, 16, 17, 18) or a.type_name.endswith("_map_mp")]
    assert {a.name for a in world} == {"maps/mp/mp_opent5box.d3dbsp"}
    names = {a.name for a in x.assets if a.type in (R, T)}
    assert not any(
        n.startswith(("maps/mp/mp_nuked", "clientscripts/mp/mp_nuked", "sun/", "exposure/mp_n"))
        for n in names
        if "mp_nuked" in n
    )
    assert "mp_opent5box" in names
    assert "maps/mp/mp_opent5box.gsc" in names
    for a in x.assets:
        if a.type == R and a.name.endswith((".gsc", ".csc")):
            text = rawfile_text(a.data)
            assert "maps\\mp\\mp_nuked" not in text and "clientscripts\\mp\\mp_nuked" not in text
