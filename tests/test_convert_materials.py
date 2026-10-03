"""PC materials and images into a converted map's own zone (opent5.convert.materials):
synthetic cases, and (slow, skipped when the files are not on this machine) the PC box with
its walls in a texture no PS3 zone has, against the disc's mp_nuked
(docs/demo-box-textures.md)."""

import os
import struct
import zipfile
from pathlib import Path

import numpy as np
import pytest

from opent5 import env
from opent5.convert import materials as mats
from opent5.convert.world import ConvertError
from opent5.export.zone import r_hash
from opent5.formats import texture as tx

COLOR, NORMAL, SPECULAR = r_hash("colorMap"), r_hash("normalMap"), r_hash("specularMap")


def _iwi(fmt: int, flags: int, width: int, height: int, levels: list[bytes]) -> bytes:
    """IWI v13 with ``levels`` given largest first (the file stores smallest first)."""
    head = b"IWi\x0d" + bytes((fmt, flags)) + struct.pack("<3Hf8I", width, height, 1, 2.0, *[0] * 8)
    return head + b"".join(reversed(levels))


def test_read_iwi_returns_levels_largest_first():
    levels = [
        bytes([k]) * tx.level_size(tx.DXT1, w, h) for k, (w, h) in enumerate(tx.mip_dims(8, 8, 4))
    ]
    iwi = mats.read_iwi(_iwi(0x0B, 0x10, 8, 8, levels))
    assert (iwi.width, iwi.height, iwi.levels, iwi.load_format) == (8, 8, 4, b"DXT1")
    assert iwi.pixels == b"".join(levels)


def test_read_iwi_single_level_and_errors():
    one = bytes(range(16)) * 4  # 4 x 4 RGBA
    iwi = mats.read_iwi(_iwi(0x01, 0x02, 4, 4, [one]))
    assert iwi.levels == 1 and iwi.pixels == one and iwi.load_format == struct.pack("<I", 21)
    with pytest.raises(ConvertError, match="expected 'IWi'"):
        mats.read_iwi(b"DDS " + bytes(60))
    with pytest.raises(ConvertError, match="format 0x13"):
        mats.read_iwi(_iwi(0x13, 0x02, 4, 4, [one]))
    with pytest.raises(ConvertError, match="needs"):
        mats.read_iwi(_iwi(0x01, 0x02, 4, 4, [one[:10]]))


def test_image_files_raw_then_iwd(tmp_path):
    (tmp_path / "raw" / "images").mkdir(parents=True)
    (tmp_path / "main").mkdir()
    (tmp_path / "raw" / "images" / "a_c.iwi").write_bytes(b"raw")
    with zipfile.ZipFile(tmp_path / "main" / "iw_00.iwd", "w") as z:
        z.writestr("images/a_c.iwi", b"zip")
        z.writestr("images/B_n.iwi", b"zipb")
    files = mats.ImageFiles([tmp_path])
    assert files.find("a_c")[0] == b"raw"
    assert files.find("b_n")[0] == b"zipb"
    assert files.find("missing") is None


def _pc_material_header() -> bytes:
    h = bytearray(0xC0)
    struct.pack_into("<I", h, 0, 0xFFFFFFFF)
    struct.pack_into("<I", h, 4, 0x52)
    h[8:12] = bytes((0, 4, 1, 1))
    h[0x10:0x18] = bytes.fromhex("0000100001000010")
    struct.pack_into("<2I", h, 0x18, 0x00100000, 0x40000015)
    h[0xAA:0xB0] = bytes((2, 1, 7, 0x39, 0, 0))
    return bytes(h)


def test_ps3_material_header_takes_pc_fields_and_template_state_bits():
    template = bytearray(0x80)
    template[0x20:0x67] = bytes(range(71))
    template[0x69] = 6
    template[0x70:0x74] = bytes.fromhex("8000396d")
    h = mats.ps3_material_header(_pc_material_header(), bytes(template))
    assert h[0:4] == b"\xff\xff\xff\xff"
    assert struct.unpack_from(">I", h, 4)[0] == 0x52
    assert h[8:12] == bytes((0, 4, 1, 1))
    assert h[0x10:0x18] == bytes.fromhex("0000100001000010")  # drawSurf bytes copied
    assert struct.unpack_from(">2I", h, 0x18) == (0x00100000, 0x40000015)
    assert h[0x20:0x67] == bytes(range(71))
    assert tuple(h[0x67:0x70]) == (2, 1, 6, 0x39, 0, 0, 0, 0, 0)
    assert h[0x70:0x74] == bytes.fromhex("8000396d")
    assert h[0x74:0x80] == b"\xff" * 12
    with pytest.raises(ConvertError, match="0xc0"):
        mats.ps3_material_header(bytes(0x80), bytes(template))


def test_constants_swap_hash_and_floats():
    pc = struct.pack("<I", 0xB4DE4D82) + b"dynamicFolia" + struct.pack("<4f", 0, 1, 0, 1)
    ps3 = mats._swap_constants(pc)
    assert ps3.hex() == "b4de4d8264796e616d6963466f6c6961000000003f800000000000003f800000"


def _techset(samplers, constants=()) -> dict:
    args = [{"raw": struct.pack(">HHI", 2, 0, h)} for h in samplers]
    args += [{"raw": struct.pack(">HHI", 6, 0, h)} for h in constants]
    args.append({"raw": struct.pack(">HHI", 3, 0, 7)})
    return {"techniques": [None, {"passes": [{"args": args}]}]}


def test_remap_techset_same_name_closest_and_refusal():
    base = {
        "wc_l_sm_r0c0": _techset([COLOR]),
        "wc_l_sm_r0c0n0s0": _techset([COLOR, NORMAL, SPECULAR]),
        "wc_l_sm_r0c0s0": _techset([COLOR, SPECULAR]),
        "l_sm_r0c0n0s0": _techset([COLOR, NORMAL, SPECULAR]),
        ",wc_l_sm_b0c0n0s0": {"techniques": []},
    }
    c = mats.remap_techset("wc_l_sm_r0c0", {COLOR, NORMAL}, set(), base)
    assert (c.name, c.how, c.samplers) == ("wc_l_sm_r0c0", "same name", ["colorMap"])
    c = mats.remap_techset("wc_l_sm_r0c0n0s0x0", {COLOR, NORMAL, SPECULAR}, set(), base)
    assert (c.name, c.how) == ("wc_l_sm_r0c0n0s0", "closest")
    c = mats.remap_techset("wc_l_sm_r0c0s0x0", {COLOR, SPECULAR}, set(), base)
    assert c.name == "wc_l_sm_r0c0s0"
    with pytest.raises(ConvertError, match="no 'wc_unlit_replace' techset"):
        mats.remap_techset("wc_unlit_replace", {COLOR}, set(), base)
    assert mats._techset_shape("l_sm_r0c0n0s0_m1c1") == ("l_sm", "r0c0n0s0_m1c1")


def test_placeholder_and_override_images():
    p = mats.placeholder_image("$identitynormalmap")
    assert p["name"] == ",$identitynormalmap" and p["pixels"] is None
    assert p["header"] == bytes(0x68) + b"\xff\xff\xff\xff" + bytes(4)
    pc = {
        "name": "x_c",
        "header": bytes(4) + bytes((3, 2, 3, 1)) + bytes(0x28) + bytes.fromhex("2a01ff7f"),
    }
    rgba = np.zeros((8, 8, 4), np.uint8)
    rgba[..., 0], rgba[..., 3] = 200, 255
    node = mats.override_image(pc, rgba)
    h = node["header"]
    assert (h[0], h[1], struct.unpack_from(">2H", h, 8)) == (tx.DXT1, 4, (8, 8))
    assert h[0x18:0x1C] == bytes((3, 2, 3, 1))
    assert h[0x6C:0x70] == bytes.fromhex("7fff012a")
    back = tx.decode(node["pixels"], tx.DXT1, 8, 8, 4)
    assert abs(int(back[..., 0].mean()) - 200) <= 8  # 5-bit red
    rgba[0, 0, 3] = 0
    assert mats.override_image(pc, rgba)["header"][0] == tx.DXT45
    with pytest.raises(ConvertError, match="power of two"):
        mats.override_image(pc, np.zeros((6, 8, 4), np.uint8))


class _Splice:
    def __init__(self):
        self.calls = []

    def origin(self, node, source):
        self.calls.append(("origin", id(node), source))

    def origin_ranges(self, node, key, ranges, source):
        self.calls.append(("ranges", id(node), key, tuple(ranges), source))


def test_attach_appends_elements_and_attributes_pointers():
    gfx = {"header": bytes(0x454), "material_memory": [{"raw": b"a"}]}
    new = {"raw": b"b"}
    mat = {"header": bytes(0x80)}
    texdef = {"raw": bytes(16)}
    r = mats.MaterialsResult(
        words={"old": b"1111", "new": b"2222"},
        sources={"old": "base", "new": "foreign"},
        elements=[new],
        base_nodes=[texdef],
        base_ranges=[(mat, "header", [(0x70, 0x74)])],
    )
    surfaces = [{"raw": bytes(0x60)}, {"raw": bytes(0x60)}]
    s = _Splice()
    mats.attach(r, gfx, ["old", "new"], surfaces, s)
    assert gfx["material_memory"][-1] is new and len(gfx["material_memory"]) == 2
    assert struct.unpack_from(">I", gfx["header"], 0x28C)[0] == 2
    assert ("origin", id(texdef), "base") in s.calls
    assert ("ranges", id(mat), "header", ((0x70, 0x74),), "base") in s.calls
    assert ("ranges", id(surfaces[1]), "raw", ((0x40, 0x44),), "foreign") in s.calls
    assert not any(c[1] == id(surfaces[0]) for c in s.calls)


STEAM = Path("/mnt/c/Program Files (x86)/Steam/steamapps/common/Call of Duty Black Ops")
PC_BOX_TEX = (
    Path(os.environ.get("OPENT5_PC_BOX_TEX", "/nonexistent")),
    STEAM / "zone" / "English" / "mp_opent5box_tex.ff",
)


@pytest.mark.zones
@pytest.mark.slow
def test_box_walls_in_a_texture_no_ps3_zone_has():
    from opent5.container.zone import Zone
    from opent5.convert import pc as pcmod
    from opent5.xfile import AssetType
    from opent5.xfile.remap import Rewrite

    path = next((p for p in PC_BOX_TEX if p.is_file()), None)
    folder = env.path_of("OPENT5_ZONES")
    if path is None or folder is None or not (folder / "mp_nuked.ff").is_file():
        pytest.skip("the PC textured box or the disc's mp_nuked is not on this machine")
    pc = Rewrite(pcmod.read_pc_fastfile(path.read_bytes()), platform=pcmod.PC)
    base = Rewrite(bytes(Zone.open(folder / "mp_nuked.ff").content))
    pgfx = next(a.data for a in pc.xfile.assets if a.type == AssetType.GFX_MAP)
    stock = next(a.data for a in base.xfile.assets if a.type == AssetType.GFX_MAP)
    r = mats.convert_materials(pgfx, stock, base, files=mats.ImageFiles([STEAM]))
    wood = r.report["materials"]["wc/blockout_test_wood"]
    assert wood["techset"] == "wc_l_sm_r0c0" and wood["techset_how"] == "same name"
    assert wood["memory"] == 44 * 16 + 6 * 8 + 160
    assert r.report["images"]["~-gblockout_wood_test_c"]["bytes"] == 0x2AB00
    assert r.report["images"]["$identitynormalmap"]["action"].startswith("reused")
    assert r.sources == {
        "wc/blockout_test_wood": "foreign",
        "wc/jun_art_concrete_base02": "base",
        "wc/pent_art_wall_creampaint02": "base",
    }
