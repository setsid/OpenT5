"""The compass pieces and the lighting helpers of world.py on synthetic data."""

import struct

import numpy as np

from opent5.convert import compass, world
from opent5.formats import texture as tx


def test_corners_are_a_square_around_the_bounds():
    nw, se = compass.corners((-512, -256, 0), (512, 256, 256))
    assert nw == (576.0, 576.0) and se == (-576.0, -576.0)
    ents = compass.corner_entities(nw, se, 0)
    assert [e["targetname"] for e in ents] == ["minimap_corner"] * 2
    assert ents[0]["origin"] == "576 576 0"


def test_render_puts_north_at_the_top_and_west_at_the_left():
    # a floor square in the north-west quarter (x > 0 is north, y > 0 is west)
    p = np.array([[100, 100, 0], [500, 100, 0], [500, 500, 0], [100, 500, 0]], float)
    t = np.array([[0, 1, 2], [0, 2, 3]])
    img = compass.render(p, t, (512, 512), (-512, -512), size=64)
    drawn = img[:, :, 3] > 0
    rows, cols = np.nonzero(drawn)
    assert rows.max() < 32 and cols.max() < 32
    assert not drawn[40:, :].any() and not drawn[:, 40:].any()


def test_walls_are_outlined_and_ceilings_left_out():
    wall = np.array([[0, -100, 0], [0, 100, 0], [0, 100, 50]], float)
    ceiling = np.array([[-100, -100, 50], [100, 100, 50], [100, -100, 50]], float)
    img = compass.render(
        np.vstack([wall, ceiling]), [[0, 1, 2], [3, 4, 5]], (128, 128), (-128, -128), size=64
    )
    assert (img[:, :, 3] == 255).sum() > 0  # the wall line
    assert (img[:, :, 3] == 200).sum() == 0  # no filled face: the ceiling faces down


def test_compass_image_is_dxt23_one_level():
    rgba = np.zeros((512, 512, 4), np.uint8)
    node = compass.compass_image("compass_map_mp_x", rgba)
    h = node["header"]
    assert h[0] == tx.DXT23 and h[1] == 1 and struct.unpack_from(">HH", h, 8) == (512, 512)
    assert h[0x18:0x1C] == bytes((3, 0, 3, 1)) and len(node["pixels"]) == 262144


def test_compass_material_is_inline_and_keeps_the_stock_fields():
    header = bytearray(0x80)
    header[0x67], header[0x69] = 1, 1
    header[0x10] = 0x2B
    node = compass.compass_material(
        "compass_map_mp_x",
        (bytes(header), bytes(16), bytes(8)),
        bytes.fromhex("800039e5"),
        None,
        {"_t": "GfxImage"},
    )
    h = node["header"]
    assert h[0:4] == b"\xff" * 4 and h[0x70:0x74] == bytes.fromhex("800039e5")
    assert h[0x74:0x78] == b"\xff" * 4 and h[0x7C:0x80] == b"\xff" * 4 and h[0x10] == 0x2B
    assert node["textures"][0]["raw"][12:16] == b"\xff" * 4


def test_grid_default_colour_is_replaced_only_when_it_is_the_cod2rad_one():
    grid = {"colors": bytes(168) + world.PC_GRID_DEFAULT}
    assert world.grid_default(grid).startswith("2 colours")
    assert grid["colors"][-168:] == world.PS3_GRID_DEFAULT
    other = {"colors": bytes(168)}
    assert "kept" in world.grid_default(other) and other["colors"] == bytes(168)


def test_sun_light_words_are_swapped_but_the_first():
    pc = {"raw": bytes((1, 2, 3, 4)) + struct.pack("<91I", *range(91))}
    stock = {"raw": bytes(0x160) + b"\x12\x34\x56\x78" + bytes(12)}
    world.convert_sun(pc, stock)
    out = stock["raw"]
    assert out[0:4] == bytes((1, 2, 3, 4))
    assert struct.unpack_from(">I", out, 4)[0] == 0
    assert struct.unpack_from(">I", out, 8)[0] == 1
    assert out[0x160:0x164] == b"\x12\x34\x56\x78"
