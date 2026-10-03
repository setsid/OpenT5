"""The converter's pieces on synthetic data: struct swap, GfxWorld header re-layout,
normals, the PS3 vertex regrouping, light grid rows, map scripts and entities."""

import struct

import numpy as np
import pytest

from opent5.convert import lighting, scripts, world
from opent5.convert.swap import swap_struct
from opent5.xfile.layouts_pc import PC_LAYOUTS
from opent5.xfile.schema import unpack_cmp_array


def test_swap_struct_reverses_every_field():
    # cplane_s: normal (3 f32), dist f32, type u8, signbits u8, pad u8[2]
    pc = struct.pack("<4fBB2x", 0.0, 0.5, -1.0, 128.0, 2, 3)
    assert swap_struct("cplane_s", pc) == struct.pack(">4fBB2x", 0.0, 0.5, -1.0, 128.0, 2, 3)
    assert swap_struct("u16", struct.pack("<3H", 1, 2, 3)) == struct.pack(">3H", 1, 2, 3)
    with pytest.raises(ValueError, match="whole number"):
        swap_struct("cplane_s", pc + b"x")


def test_gfx_header_relayout():
    pc = struct.pack("<271I", *range(271))  # 0x43c bytes, word n holds n
    stock = bytes(range(256)) * 5
    ps3 = world.gfx_header(pc, stock[: world.PS3_GFX_HEADER])
    assert len(ps3) == world.PS3_GFX_HEADER
    words = [o for _, o, t in PC_LAYOUTS["GfxWorld"][1] if t in ("u32", "s32", "ptr")]
    assert len(words) > 100
    for pc_off in words:
        at = world.ps3_offset(pc_off)
        assert struct.unpack_from(">I", ps3, at)[0] == pc_off // 4
    assert ps3[0x24:0x38] == stock[0x24:0x38]  # PS3-only words from the stock header
    assert ps3[0x22C:0x230] == stock[0x22C:0x230]


def test_pc_unit_vectors_and_cmp():
    packed = np.frombuffer(bytes.fromhex("7f7ffe3f" "fe7f7f3f" "a0047f3f"), np.uint8).reshape(3, 4)
    v = world.pc_unit_vectors(packed)
    assert np.allclose(v[0], (0, 0, 1), atol=0.01)
    assert np.allclose(v[1], (1, 0, 0), atol=0.01)
    words = world.cmp_pack(v)
    back = unpack_cmp_array(words)
    assert np.allclose(back[0], (0, 0, 1), atol=0.003)
    # PC a0047f3f has length 1.0028; the PS3 word is the normalised one (y = -988)
    assert (int(words[2]) >> 11) & 0x7FF == (-988) & 0x7FF


def _surface(first, count, tris, base, layer=0):
    raw = struct.pack("<3fi3fiHHifi", 0, 0, 0, layer, 1, 1, 1, first, count, tris, base, 0.0, -1)
    raw += struct.pack("<I", 0x80000001) + bytes([0, 1, 0, 1])  # material, light bytes
    raw += struct.pack("<6f", 0, 0, 0, 1, 1, 1)
    return raw


def _vertices(n):
    out = b""
    for i in range(n):
        out += struct.pack("<4f", i, 2 * i, 3 * i, 1.0)  # position, binormal sign
        out += bytes((10, 20, 30, 255))  # BGRA
        out += struct.pack("<4f", 0.5, 0.25, 0.1, 0.2)
        out += bytes.fromhex("7f7ffe3f") + bytes.fromhex("fe7f7f3f")
    return out


def test_regroup_in_first_use_order():
    """PC groups at vertex 6 (4 vertices) and 0 (6); surfaces use 6, 0, 6. PS3: groups
    in first-use order, each surface's own vertex range, indices in surface order."""
    idx = struct.pack("<18H", 2, 3, 4, 3, 4, 5, 0, 1, 2, 0, 2, 3, 1, 2, 3, 0, 0, 0)
    surfaces = [
        world.PCSurface.parse(0, _surface(6, 4, 2, 6)),
        world.PCSurface.parse(1, _surface(0, 6, 2, 0)),
        world.PCSurface.parse(2, _surface(6, 4, 1, 12)),
    ]
    groups = world.plan_groups(surfaces, 0)
    assert [(g.pc_first, g.first, g.layer) for g in groups] == [(6, 0, 0), (0, 4, 4 * 28)]
    positions, layer, notes = world.convert_vertices(_vertices(10), b"", groups)
    pos = np.frombuffer(positions, ">f4").reshape(-1, 4)
    assert pos[:, 0].tolist() == [6, 7, 8, 9, 0, 1, 2, 3, 4, 5]
    first = np.frombuffer(layer, np.uint8, 28).tolist()
    assert first[0:4] == [30, 20, 10, 255]  # BGRA -> RGBA
    words = [b"\x80\0\0\x01"] * 3
    records, indices = world.convert_surfaces(surfaces, idx, groups, words)
    fields = [struct.unpack_from(">iiHHi", r, 0x1C) for r in records]
    assert fields == [(0, 0, 4, 2, 0), (4, 2, 4, 2, 6), (0, 1, 3, 1, 12)]
    assert [struct.unpack_from(">i", r, 0xC)[0] for r in records] == [0, 112, 0]
    assert struct.unpack(">15H", indices) == (0, 1, 2, 0, 2, 3, 2, 3, 4, 3, 4, 5, 1, 2, 3)
    assert all(r[0x40:0x44] == b"\x80\0\0\x01" for r in records)


def test_layer_extras_are_swapped_as_floats():
    surfaces = [world.PCSurface.parse(0, _surface(0, 2, 0, 0, layer=0))]
    groups = world.plan_groups(surfaces, 16)
    assert groups[0].extra == 8 and groups[0].stride == 36
    extras = struct.pack("<4f", 1.0, 2.0, 3.0, 4.0)
    _, layer, _ = world.convert_vertices(_vertices(2), extras, groups)
    run = np.frombuffer(layer, np.uint8).reshape(2, 36)
    assert run[0, 28:].tobytes() == struct.pack(">2f", 1.0, 2.0)
    assert world.extra_layout(12) == ["f32", "f32", "bytes"]
    assert world.extra_layout(24) is None


def test_ambiguous_layer_data_is_refused():
    surfaces = [
        world.PCSurface.parse(0, _surface(0, 2, 0, 0)),
        world.PCSurface.parse(1, _surface(2, 4, 0, 0)),
    ]
    with pytest.raises(world.ConvertError, match="exactly one group"):
        world.plan_groups(surfaces, 32)  # 16 x 2 or 8 x 4


def test_light_grid_rows():
    row = struct.pack("<4HI", 1, 2, 3, 4, 5) + bytes((2, 2, 0, 0))
    out = world.light_grid_rows(row * 2, struct.pack("<3H", 0, 0xFFFF, 4))
    assert out == (struct.pack(">4HI", 1, 2, 3, 4, 5) + bytes((2, 2, 0, 0))) * 2


STOCK_MAIN = """#include maps\\mp\\_utility;
main()
{
maps\\mp\\mp_test_fx::main();
maps\\mp\\_load::main();
maps\\mp\\_compass::setupMiniMap("compass_map_mp_test");
setdvar("compassmaxrange","2100");
if ( level.x )
{
level thread something();
}
level thread clock_think();
}
clock_think()
{
hand = GetEnt( "clock", "targetname" );
hand RotatePitch( 10, 1 );
}
"""


def test_minimal_main():
    text, kept, dropped = scripts.minimal_main(STOCK_MAIN, "mp_test")
    assert "clock_think" not in text and text.startswith("#include maps\\mp\\_utility;\nmain()")
    assert kept[1] == "maps\\mp\\_load::main();"
    assert "level thread clock_think();" in dropped
    with pytest.raises(world.ConvertError, match="_load"):
        scripts.minimal_main("main()\n{\nfoo();\n}\n", "mp_test")


def test_plan_scripts_refuses_a_dropped_function():
    texts = {
        "maps/mp/mp_test.gsc": STOCK_MAIN,
        "maps/mp/createfx/mp_test_fx.gsc": "main()\n{\nlots();\n}\n",
        "maps/mp/mp_test_amb.gsc": "main()\n{\n}\n",
    }
    plan = scripts.plan_scripts("mp_test", texts)
    assert plan.texts["maps/mp/createfx/mp_test_fx.gsc"] == scripts.EMPTY_MAIN
    texts["maps/mp/other.gsc"] = "f()\n{\nmaps\\mp\\mp_test::clock_think();\n}\n"
    with pytest.raises(world.ConvertError, match="clock_think"):
        scripts.plan_scripts("mp_test", texts)


def test_entities_and_gametypes():
    text = '{\n"classname" "worldspawn"\n}\n{\n"classname" "mp_dm_spawn"\n}\n'
    text += '{\n"classname" "mp_global_intermission"\n"origin" "0 0 0"\n}\n'
    ents = scripts.parse_entities(text)
    assert [e["classname"] for e in ents] == ["worldspawn", "mp_dm_spawn", "mp_global_intermission"]
    report = scripts.gametype_report(ents, ["dm", "tdm", "sd"])
    assert report["dm"] == []
    assert report["tdm"] == ["mp_tdm_spawn_allies_start", "mp_tdm_spawn_axis_start", "mp_tdm_spawn"]
    assert "targetname=sd_bomb" in report["sd"]
    added = scripts.compat_entities("mp_nuked", (-512, -512, 0), (512, 512, 256))
    by_name = {e["targetname"]: e for e in added}
    assert by_name["clock_min_hand"]["origin"] == "-256 0 512"
    assert by_name["nuked_bomb"]["origin"] == "0 0 4212"
    assert by_name["endgame_camera_start"]["target"] == "endgame_camera_end"
    again = scripts.parse_entities(scripts.entity_text(added))
    assert again == added
    assert scripts.compat_entities("mp_other", (0, 0, 0), (1, 1, 1)) == []


def test_box5_mean():
    a = np.arange(49, dtype=float).reshape(7, 7)
    m = lighting._box5(a)
    p = np.pad(a, 2, mode="edge")
    assert np.isclose(m[3, 3], p[3:8, 3:8].mean())
    assert np.isclose(m[0, 0], p[0:5, 0:5].mean())
