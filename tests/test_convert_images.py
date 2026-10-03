"""PC GfxImage -> PS3 GfxImage: synthetic cases, and PC mp_nuked's cod2rad images against
PS3 mp_nuked byte for byte (docs/research/box-lighting.md 2.3, 2.4; skipped when the PC
zone is not on this machine)."""

import struct
import sys
from pathlib import Path

import numpy as np
import pytest

from opent5.convert import images
from opent5.formats import texture as tx


def _pc_node(fmt_code: bytes, width: int, height: int, pixels: bytes, map_type=3, levels=1):
    header = bytearray(0x34)
    header[4:8] = bytes((map_type, 1, 2, 0))
    struct.pack_into("<3H", header, 0x14, width, height, 1)
    struct.pack_into("<I", header, 0x30, 0xCE2F698B)  # PC bytes 8b692fce
    load_def = bytes((levels, 2, 0, 0)) + fmt_code + struct.pack("<I", len(pixels))
    return {"name": "x", "header": bytes(header), "load_def": load_def, "pixels": pixels}


def test_r5g6b5_is_swapped_and_swizzled():
    texels = np.arange(16, dtype="<u2").reshape(4, 4)
    node = images.convert_image(_pc_node(struct.pack("<I", 23), 4, 4, texels.tobytes()))
    h = node["header"]
    assert h[0] == tx.R5G6B5 and h[1] == 1
    assert struct.unpack_from(">I", h, 4)[0] == 0x0001AAE4
    assert h[0x18:0x1C] == bytes((3, 1, 2, 1))
    assert struct.unpack_from(">I", h, 0x6C)[0] == 0xCE2F698B  # PS3 bytes ce2f698b
    back = tx.unswizzle(np.frombuffer(node["pixels"][:32], ">u2"), 4, 4)
    assert (back == texels).all()
    assert len(node["pixels"]) == 128  # padded


def test_g16r16_keeps_word_order():
    pc = struct.pack("<2H", 0x1234, 0xABCD) * 4
    node = images.convert_image(_pc_node(struct.pack("<I", 34), 2, 2, pc))
    assert node["header"][0] == tx.Y16_X16
    assert struct.unpack_from(">I", node["header"], 4)[0] == 0x0000AAE4
    assert node["pixels"][:4] == bytes.fromhex("1234abcd")


def test_cube_faces_are_padded_and_full_chain():
    face = bytes(range(8)) * 3  # DXT1 4x4, 2x2, 1x1: three 8-byte blocks
    node = images.convert_image(_pc_node(b"DXT1", 4, 4, face * 6, map_type=5, levels=0))
    h = node["header"]
    assert h[0] == tx.DXT1 and h[1] == 3 and h[3] == 1
    assert len(node["pixels"]) == 6 * 128
    assert node["pixels"][128 : 128 + 24] == face


def test_refuses_unknown_format_and_short_pixels():
    with pytest.raises(images.ImageError, match="loadDef format"):
        images.convert_image(_pc_node(struct.pack("<I", 99), 4, 4, b"\0" * 32))
    with pytest.raises(images.ImageError, match="needs 8 bytes"):
        images.convert_image(_pc_node(b"DXT1", 4, 4, b"\0" * 4))


def test_name_hash_matches_stock_images():
    assert images.name_hash("*lightmap0_primary") == 0xCE2F698B
    assert images.name_hash("faction_128_specops") == 0x8BA3E54A


@pytest.mark.zones
@pytest.mark.slow
def test_pc_nuked_images_equal_ps3_nuked():
    from opent5.container.zone import Zone
    from opent5.convert import pc as pcmod
    from opent5.export.nodes import pixel_bytes
    from opent5.xfile import AssetType, parse
    from test_convert_box import PC_NUKED, _ps3

    if not PC_NUKED.is_file():
        pytest.skip("PC mp_nuked.ff is not on this machine")
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
    from convert_map import pc_world_range

    pc = pcmod.open_pc_zone(PC_NUKED)
    walked = pcmod.walk_range(pc, *pc_world_range(pc))
    g = next(a.data for a in walked if a.type == AssetType.GFX_MAP)
    ps3 = parse(bytes(Zone.open(_ps3("mp_nuked")).content), log=False)
    q = next(a.data for a in ps3.assets if a.type == AssetType.GFX_MAP)
    pairs = [(g["lightmaps"][0][k], q["lightmaps"][0][k]) for k in range(3)]
    pairs += [
        (a["image"], b["image"])
        for a, b in zip(g["reflection_probes"], q["reflection_probes"], strict=True)
    ]
    pairs.append((g["outdoor_image"], q["outdoor_image"]))
    assert len(pairs) == 6
    for a, b in pairs:
        node = images.convert_image(a)
        assert node["name"] == b["name"]
        assert node["header"] == bytes(b["header"]), node["name"]
        assert node["pixels"] == pixel_bytes(b["pixels"]), node["name"]
