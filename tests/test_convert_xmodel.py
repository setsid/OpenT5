"""PC XModels and static models to PS3 (opent5.convert.xmodel, opent5.convert.smodels).

The synthetic tests pin the layout rules; the real-data tests (skipped when the files are
not on this machine; none enters the repository) repeat the mp_nuked proof of docs/convert.md
section 9 and the props box of docs/demo-box-models.md."""

import struct
from pathlib import Path

import numpy as np
import pytest

from opent5 import env
from opent5.convert import smodels
from opent5.convert import xmodel as xm
from opent5.convert.world import ConvertError

STEAM = Path("/mnt/c/Program Files (x86)/Steam/steamapps/common/Call of Duty Black Ops")
PC_NUKED = STEAM / "zone" / "Common" / "mp_nuked.ff"
PC_PROPS = STEAM / "zone" / "English" / "mp_opent5box_props.ff"


def _vertices(xyz, colour=(255, 255, 255, 255), sign=1.0):
    v = np.zeros(len(xyz), xm.PC_PACKED_VERTEX)
    v["xyz"] = xyz
    v["binormal_sign"] = sign
    v["colour"] = colour
    v["uv"] = 0xB51C3000
    v["normal"] = (127, 254, 127, 63)  # PackedUnitVec (0, 1, 0)
    v["tangent"] = (127, 127, 254, 63)  # (0, 0, 1)
    return v


def test_quantise_offset_exponent_and_round_trip():
    xyz = np.array([[-33, -1, 0], [33, 1, 64], [10.25, 0.5, 31]], np.float32)
    offset, exp, q = xm.quantise(xyz)
    assert offset.tolist() == [0.0, 0.0, 32.0]
    assert exp == 6  # half extent 33 -> 2^6
    back = offset + q.astype(np.float64) * 2.0**exp / 32768
    assert np.abs(back - xyz).max() <= 2.0**exp / 32768 / 2
    # a half extent a hair above a power of two keeps it (p_us_table_art02 surface 3)
    _, exp, _ = xm.quantise(np.array([[0, 0, 0], [32.000004, 1, 1]], np.float32))
    assert exp == 4
    # small surfaces: the exponent is never negative
    _, exp, _ = xm.quantise(np.array([[0, 0, 0], [0.5, 0.2, 0.1]], np.float32))
    assert exp == 0


def test_surface_flags_formats_and_colours():
    xyz = [[0, 0, 0], [1, 0, 0], [0, 1, 0]]
    flags, v0, stream, tail, word = xm.surface_streams(_vertices(xyz, (0xBF, 0xBF, 0xBF, 0xFF)), 0)
    assert flags == 5 and len(v0) == 3 * 8 and len(stream) == 3 * 12
    assert word == 0xFFBFBFBF and tail[0x10:0x14] == bytes.fromhex("ffbfbfbf")
    assert tail[0xC:0x10] == bytes([0x4B, 0, 0, 0])
    assert stream[:12] == bytes.fromhex("001ff800 7fc00000 b51c3000".replace(" ", ""))
    v = _vertices(xyz)
    v["colour"][1] = (255, 255, 0, 146)  # B, G, R, A
    flags, v0, stream, tail, word = xm.surface_streams(v, 0)
    assert flags == 1 and word == 0 and len(stream) == 3 * 16
    assert stream[16 + 12 : 16 + 16] == bytes([0, 255, 255, 146])  # R, G, B, A
    flags, v0, stream, _, _ = xm.surface_streams(_vertices(xyz, sign=-1.0), 2)  # skinned
    assert flags == 7 and len(v0) == 3 * 16 and len(stream) == 3 * 4
    assert struct.unpack_from(">h", v0, 6)[0] == -32768
    flags, v0, stream, _, _ = xm.surface_streams(_vertices(xyz), 0, quantise_positions=False)
    assert flags == 0 and len(stream) == 3 * 16
    assert struct.unpack_from(">4f", v0, 16) == (1.0, 0.0, 0.0, 1.0)


def test_header_layout():
    pc = bytearray(xm.PC_XMODEL)
    struct.pack_into("<I4B", pc, 0, 0xFFFFFFFF, 1, 1, 4, 0)
    for i in range(4):
        struct.pack_into(
            "<fHH5i4b", pc, 0x28 + 32 * i, 1000.0 * (i + 1), 1, i, -(2**31), 0, 0, 0, 0, i, 4, 9, 0
        )
    tail = (0xFFFFFFFF, 1, 1, 0xFFFFFFFF, 46.5, -33, -1, 0, 33, 1, 64, 4, 3, 0xFFFFFFFF)
    struct.pack_into("<IiiIf3f3fHhIiIBxxxIBxxxII", pc, 0xAC, *tail, 19671, 0x80000, 0, 0, 0, 0, 0)
    ps3 = xm.xmodel_header(bytes(pc))
    assert len(ps3) == xm.PS3_XMODEL
    assert struct.unpack_from(">fHHI", ps3, 0x28 + 28 * 3) == (4000.0, 1, 3, 0x80000000)
    assert struct.unpack_from(">IiiIf", ps3, 0xA0) == (0xFFFFFFFF, 1, 1, 0xFFFFFFFF, 46.5)
    assert struct.unpack_from(">HhI", ps3, 0xCC) == (4, 3, 0xFFFFFFFF)
    assert ps3[0xD4:0xE0] == bytes(12)
    assert struct.unpack_from(">II", ps3, 0xE0) == (19671, 0x80000)


def test_mem_usage_rule():
    surf = {"raw": struct.pack(">BBHHH", 2, 1, 5, 592, 300) + bytes(0x54)}
    assert xm.ps3_mem_usage(19671, [{"verts0": b"x"}], [surf]) == 19671 - 4 + 24 + 592 * (20 - 32)
    assert xm.ps3_mem_usage(296, [{"verts0": None}], [surf]) == 292
    assert xm.ps3_mem_usage(0, None, None) == 0


def test_draw_inst_layout():
    yaw = np.radians(30)
    axes = [np.cos(yaw), np.sin(yaw), 0, -np.sin(yaw), np.cos(yaw), 0, 0, 0, 1]
    pc = struct.pack(
        "<4f9ffIi4HHbb", 8000, 240, 320, 0, *axes, 1.0, 0xDEADBEEF, 0, 1, 2, 3, 4, 7, 1, 1
    )
    out = smodels.ps3_draw_inst(pc, bytes.fromhex("80003045"))
    assert len(out) == smodels.PS3_DRAW_INST
    assert struct.unpack_from(">4f", out, 0) == (8000.0, 240.0, 320.0, 0.0)
    assert struct.unpack_from(">I", out, 0x18)[0] == 0x7FC00000  # up (0, 0, 1)
    assert out[0x1C:0x28] == struct.pack(">f", 1.0) + bytes.fromhex("80003045") + bytes(4)
    assert out[0x28:0x2C] == bytes.fromhex("00070101")


def test_static_models_need_base_models():
    taken = smodels.Taken(bytes(40), [{"raw": bytes(76), "model": {"name": "p_missing"}}])
    with pytest.raises(ConvertError, match="p_missing"):
        smodels.convert_static_models(taken, {})
    res = smodels.convert_static_models(taken, {"p_missing": (b"\x80\0\0\x05", {})})
    assert res.report == {"count": 1, "models": {"p_missing": 1}}
    assert res.draw_insts[0]["raw"][0x20:0x24] == b"\x80\0\0\x05"


# -- real data -------------------------------------------------------------------------------


def _ps3_zone(name: str):
    from opent5.container.zone import Zone
    from opent5.xfile.model import parse

    folder = env.path_of("OPENT5_ZONES")
    path = folder / f"{name}.ff" if folder else None
    if path is None or not path.is_file():
        pytest.skip(f"{name}.ff is not configured")
    return parse(bytes(Zone.open(path).content))


def _models(xfile, kind):
    from opent5.export.nodes import walk

    out = {}
    for a in xfile.assets:
        for n in walk(a.data):
            if isinstance(n, dict) and n.get("_t") == kind and n.get("name"):
                out.setdefault(n["name"], n)
    return out


@pytest.mark.zones
@pytest.mark.slow
def test_pc_nuked_models_and_static_models_against_ps3():
    from opent5.convert.pc import open_pc_zone, parse_pc

    if not PC_NUKED.is_file():
        pytest.skip("PC mp_nuked.ff is not on this machine")
    pc = parse_pc(open_pc_zone(PC_NUKED))
    # walks to the end; RUNTIME ends 0x6600 short (docs/convert.md 12, open)
    assert [p for p in pc.problems() if "RUNTIME" not in p] == []
    ps3 = _ps3_zone("mp_nuked")
    r = xm.compare_xmodels(pc, ps3, _models(pc, "PC.XModel"), _models(ps3, "XModel"))
    assert (r["models"], r["surfaces"], r["vertices"], r["triangles"]) == (
        293,
        1820,
        819455,
        821442,
    )
    assert r["position_error_steps"][">2"] == 0
    assert r["worst"]["normal_deg"] < 0.5 and r["worst"]["tangent_deg"] < 0.5
    exact = ("triangle indices", "uv", "colour", "binormal sign", "bone names", "material names")
    exact += ("base_mat", "bone_info", "coll_surfs", "collmaps", "rigid vert list", "verts_blend")
    assert not set(exact) & set(r["differences"])
    assert r["differences"]["surface flags"] == 1 and r["differences"]["header memUsage"] == 1
    gfx = next(a for a in pc.assets if a.type_name == "gfx_map").data
    stock = next(a for a in ps3.assets if a.type_name == "gfx_map").data
    s = smodels.compare_static_models(gfx, stock, smodels.model_words(ps3), pc)
    assert s["draw_insts"] == 4209
    assert {k: v for k, v in s["identical"].items() if k != "cullDist"} == {
        "origin": 4209,
        "axes (CMP)": 4209,
        "scale": 4209,
        "model alias": 4209,
        "flags": 4209,
        "lightingHandle, probe, primary light": 4209,
    }
    assert s["insts"]["groundLighting identical"] == 4209


@pytest.mark.zones
@pytest.mark.slow
def test_props_box_static_models():
    from opent5.convert.pc import open_pc_zone, parse_pc

    if not PC_PROPS.is_file():
        pytest.skip("the props box (mp_opent5box_props.ff) is not on this machine")
    pc = parse_pc(open_pc_zone(PC_PROPS))
    assert pc.problems() == []
    gfx = next(a for a in pc.assets if a.type_name == "gfx_map").data
    ps3 = _ps3_zone("mp_nuked")
    words = smodels.model_words(ps3)
    res = smodels.convert_static_models(smodels.take(gfx), words, pc)
    assert res.report["count"] == 8 and len(res.insts) == 8 * smodels.INST
    assert sorted(res.report["models"]) == [
        "mp_nuked_fence",
        "p_dest_trashcan_metal",
        "p_glo_barricade_wood_barb",
        "p_glo_cardboardbox_4",
        "p_glo_potted_plant_01",
        "p_glo_sandbag",
        "p_jun_wood_stack",
        "p_us_mailbox",
    ]
    assert gfx["smodel_draw_insts"] is None
