"""The converter on real data: the PC box map (built with the PC Mod Tools,
docs/research/box-map.md 1) into the disc's mp_nuked, the converted zone read back and PC
mp_nuked's world against PS3 mp_nuked's (slow: each conversion takes about 10 s; the quick
real-data smoke tests are in test_convert_full.py).
Skipped when the files are not on this machine. Neither file enters the repository; the
PC box is looked for at $OPENT5_PC_BOX, then in the default build locations."""

import os
from pathlib import Path

import pytest

from opent5 import env
from opent5.convert import pc as pcmod
from opent5.xfile import AssetType, write
from opent5.xfile.remap import Rewrite

STEAM = Path("/mnt/c/Program Files (x86)/Steam/steamapps/common/Call of Duty Black Ops")
PC_BOX = (
    Path(os.environ.get("OPENT5_PC_BOX", "/nonexistent")),
    Path("/mnt/c/o5/p6/mp_opent5box.ff"),
    STEAM / "zone" / "English" / "mp_opent5box.ff",
)
PC_NUKED = STEAM / "zone" / "Common" / "mp_nuked.ff"


def _first(paths):
    return next((p for p in paths if p.is_file()), None)


def _ps3(name: str) -> Path:
    folder = env.path_of("OPENT5_ZONES")
    path = folder / f"{name}.ff" if folder else None
    if path is None or not path.is_file():
        pytest.skip(f"{name}.ff is not configured")
    return path


@pytest.fixture(scope="module")
def box():
    path = _first(PC_BOX)
    if path is None:
        pytest.skip("the PC box map (mp_opent5box.ff) is not on this machine")
    return path.read_bytes()


@pytest.fixture(scope="module")
def converted(box):
    from opent5.convert.mapzone import convert_map

    return convert_map(box, _ps3("mp_nuked"))


@pytest.mark.zones
def test_pc_box_walks_exactly_and_writes_back(box):
    content = pcmod.read_pc_fastfile(box)
    x = pcmod.parse_pc(content)
    assert x.problems() == []
    assert [a.type_name for a in x.assets] == [
        "rawfile",
        "com_map",
        "techset",
        "techset",
        "gfx_map",
        "game_map_mp",
        "col_map_mp",
        "rawfile",
    ]
    assert write(x, log=False).content == content
    assert Rewrite(content, platform=pcmod.PC).build().content == content


@pytest.mark.zones
@pytest.mark.slow
def test_box_converts_into_mp_nuked(converted):
    r = converted.report
    assert r["checks"]["reparse_exact"] and r["checks"]["write_identical"]
    assert r["checks"]["unresolved"] == 0
    assert r["checks"]["assets"] == 530  # 529 stock + the compass material
    assert r["zone"]["name"] == "mp_nuked"
    for g in ("tdm", "dm"):
        assert r["gametypes"][g] == "ready"
    assert set(r["scripts"]["changed"]) == {
        "maps/mp/mp_nuked.gsc",
        "maps/mp/createfx/mp_nuked_fx.gsc",
        "clientscripts/mp/createfx/mp_nuked_fx.csc",
        "maps/mp/createart/mp_nuked_art.gsc",
    }
    assert "maps\\mp\\_load::main();" in r["scripts"]["main_kept"]
    assert r["assets"]["glasses"]["action"].startswith("numGlasses 62 -> 0")
    assert r["pointers"]["via_pc"] > 0
    light = r["lighting"]
    assert light["mode"] == "baked"
    assert (light["lightmaps"], light["reflection_probes"], light["outdoor_image"]) == (1, 1, True)
    assert [(i["name"], i["format"], i["bytes"]) for i in light["images"]] == [
        ("*lightmap0_primary", "DXT1", 524288),
        ("*lightmap0_secondary", "R5G6B5", 1048576),
        ("*lightmap0_secondaryb", "Y16_X16", 1048576),
        ("*reflection_probe0", "DXT1", 768),
        ("$outdoor", "B8", 262144),
    ]
    assert light["light_grid"] == "1 colours; last 15009d x 56 -> 40059d x 56"
    assert r["zone"]["header"][5] == "0x9dd700"  # PHYSICAL_RUNTIME: the box's own lightmaps
    compass = r["compass"]
    assert compass["material"] == "compass_map_mp_nuked" and compass["asset_index"] == 529
    assert compass["image"]["bytes"] == 262144 and compass["image"]["drawn_pixels"] > 150000
    corners = r["entities"]["minimap_corners"]
    assert (corners["north_west"], corners["south_east"]) == ([576.0, 576.0], [-576.0, -576.0])


@pytest.mark.zones
@pytest.mark.slow
def test_box_flat_lighting_still_converts(box):
    from opent5.convert.mapzone import convert_map

    r = convert_map(box, _ps3("mp_nuked"), lighting="flat").report
    assert r["checks"]["unresolved"] == 0 and r["checks"]["reparse_exact"]
    assert r["lighting"]["mode"] == "flat" and len(r["lighting"]["donors"]) == 3
    assert "images" not in r["lighting"]


@pytest.mark.zones
@pytest.mark.slow
def test_converted_zone_reads_back(converted, tmp_path):
    from opent5.edit import Document

    out = tmp_path / "mp_nuked.ff"
    out.write_bytes(converted.fastfile)
    doc = Document.open(out)
    world = doc.mesh("world")
    assert world.positions.shape == (24, 3) and world.triangles.shape == (12, 3)
    assert world.positions.min(axis=0).tolist() == [-512, -512, 0]
    assert world.positions.max(axis=0).tolist() == [512, 512, 256]
    collision = doc.mesh("collision")
    assert collision.positions.min(axis=0).tolist() == [-528, -528, -16]
    gfx = next(a for a in doc.xfile.assets if a.type == AssetType.GFX_MAP).data
    names = sorted(s["material"].name for s in gfx["surfaces"])
    assert names == [
        "wc/jun_art_concrete_base02",
        "wc/pent_art_wall_creampaint02",
        "wc/us_art_wall_vinylsiding_white",
    ]
    glasses = next(a for a in doc.xfile.assets if a.type == AssetType.GLASSES).data
    assert glasses["glasses"] is None
    ents = doc.text(doc.find("maps/mp/mp_nuked.d3dbsp", "col_map_mp").index)
    assert '"targetname" "endgame_camera_start"' in ents


@pytest.mark.zones
@pytest.mark.slow
def test_pc_nuked_world_matches_ps3_nuked():
    """The converter's world rules against the same map built for PS3 (numbers in
    docs/convert.md section 4)."""
    import sys

    if not PC_NUKED.is_file():
        pytest.skip("PC mp_nuked.ff is not on this machine")
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
    from convert_map import pc_world_range
    from opent5.container.zone import Zone
    from opent5.convert.compare import compare_world

    pc = pcmod.open_pc_zone(PC_NUKED)
    ps3 = bytes(Zone.open(_ps3("mp_nuked")).content)
    r = compare_world(pc, *pc_world_range(pc), ps3)
    assert r["vertices"]["positions_identical"]
    assert r["indices"]["identical"]
    assert set(r["surfaces"]["differing_fields"]) == {"mins", "maxs", "himipRadiusSq"}
    layer = r["layer_data"]
    assert layer["vertices_differing"]["colour"] == 0
    assert layer["vertices_differing"]["uv"] == 0
    assert layer["vertices_differing"]["lmap_uv"] == 0
    assert layer["normal_tangent_max_angle_degrees"] < 0.5
    assert layer["groups_with_extras_identical"] == 76
    data = {a["array"]: a.get("differing_non_pointer_words") for a in r["arrays"]}
    for name in ("com_map.primary_lights", "game_map_mp.nodes", "col_map_mp.brushes"):
        assert data[name] == 0
    assert len(r["gfx_header_differing_words"]) == 6


PC_BOX_GRID = (
    Path(os.environ.get("OPENT5_PC_BOX_GRID", "/nonexistent")),
    STEAM / "zone" / "English" / "mp_opent5box_grid.ff",
)


@pytest.mark.zones
@pytest.mark.slow
def test_box_with_a_light_grid_converts():
    """The box rebuilt with a lightgrid_volume brush (docs/demo-box-lit.md): cod2map writes
    the grid points, cod2rad a real light grid, the converter keeps it."""
    from opent5.convert.mapzone import convert_map

    path = _first(PC_BOX_GRID)
    if path is None:
        pytest.skip("mp_opent5box_grid.ff is not on this machine")
    r = convert_map(path.read_bytes(), _ps3("mp_nuked")).report
    assert r["checks"]["unresolved"] == 0 and r["checks"]["reparse_exact"]
    assert r["lighting"]["light_grid"] == "2164 colours; last 15009d x 56 -> 40059d x 56"
