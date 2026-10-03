"""PS3 texture decode/encode, swizzle, DDS and PNG, plus .pak reading."""

import struct

import numpy as np
import pytest

from opent5 import env
from opent5.formats import texture as T


def _gradient(w, h, alpha=True):
    y, x = np.mgrid[0:h, 0:w]
    img = np.zeros((h, w, 4), np.uint8)
    img[..., 0] = x * 255 // max(1, w - 1)
    img[..., 1] = y * 255 // max(1, h - 1)
    img[..., 2] = 128
    img[..., 3] = ((x + y) * 255 // max(1, w + h - 2)) if alpha else 255
    return img


# -- sizes ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fmt,w,h,levels,faces,expected",
    [
        # values read from GfxImage size fields in mp_nuked / common_mp / code_post_gfx_mp
        (T.DXT1, 128, 128, 8, 1, 0x2B00),
        (T.DXT1, 32, 32, 6, 1, 0x300),
        (T.DXT45, 512, 512, 10, 1, 0x55580),
        (T.DXT45, 64, 128, 8, 1, 0x2B00),
        (T.DXT23, 16, 16, 5, 1, 0x180),
        (T.DXT1, 512, 512, 1, 6, 0xC0000),
        (T.D8R8G8B8, 64, 64, 1, 6, 0x18000),
        (T.D8R8G8B8, 128, 128, 8, 1, 0x15580),
        (T.B8, 512, 512, 1, 1, 0x40000),
        (T.B8, 128, 128, 8, 1, 0x5580),
        (T.A8R8G8B8, 128, 32, 1, 1, 0x4000),
    ],
)
def test_image_size_matches_zone_values(fmt, w, h, levels, faces, expected):
    assert T.image_size(fmt, w, h, levels, faces) == expected


def test_volume_size():
    assert T.image_size(T.A8R8G8B8, 32, 32, 1, depth=32) == 0x20000


# -- swizzle -------------------------------------------------------------------------------


def test_morton_square_order():
    idx = T.morton_indices(4, 4)
    assert idx[0].tolist() == [0, 1, 4, 5]
    assert idx[1].tolist() == [2, 3, 6, 7]
    assert idx[:, 0].tolist() == [0, 2, 8, 10]


def test_morton_non_square_is_a_permutation():
    for w, h in [(128, 32), (16, 128), (1, 8), (8, 1)]:
        idx = T.morton_indices(w, h)
        assert sorted(idx.ravel().tolist()) == list(range(w * h))


def test_morton_wide_tail_bits_follow_x():
    # 8x2: one interleaved pair (x0, y0), then x1, x2
    assert T.morton_indices(8, 2)[0].tolist() == [0, 1, 4, 5, 8, 9, 12, 13]


def test_swizzle_round_trip():
    img = _gradient(32, 8)
    assert np.array_equal(T.unswizzle(T.swizzle(img), 32, 8), img)


def test_morton_3d_first_octant():
    idx = T.morton_indices_3d(2, 2, 2)
    assert idx[0, 0, 1] == 1 and idx[0, 1, 0] == 2 and idx[1, 0, 0] == 4


def test_non_power_of_two_swizzle_rejected():
    with pytest.raises(T.TextureError):
        T.morton_indices(6, 4)


# -- DXT -----------------------------------------------------------------------------------


def test_dxt1_known_block():
    # c0 = red (0xf800), c1 = blue (0x001f), little-endian words; indices 0,1,2,3 on row 0
    block = struct.pack("<HHI", 0xF800, 0x001F, 0b11100100)
    img = T.decode_dxt(block, T.DXT1, 4, 4)
    assert img[0, 0].tolist() == [255, 0, 0, 255]
    assert img[0, 1].tolist() == [0, 0, 255, 255]
    assert img[0, 2].tolist() == [170, 0, 85, 255]
    assert img[1, 0].tolist() == [255, 0, 0, 255]


def test_dxt1_punch_through():
    block = struct.pack("<HHI", 0x001F, 0xF800, 0xFFFFFFFF)  # c0 <= c1, index 3 = transparent
    img = T.decode_dxt(block, T.DXT1, 4, 4)
    assert (img[..., 3] == 0).all()


def test_dxt5_alpha_endpoints():
    alpha = bytes([255, 0]) + bytes(6)  # all index 0 -> 255
    color = struct.pack("<HHI", 0xFFFF, 0, 0)
    img = T.decode_dxt(alpha + color, T.DXT45, 4, 4)
    assert (img[..., 3] == 255).all() and (img[..., :3] == 255).all()


@pytest.mark.parametrize("fmt", [T.DXT1, T.DXT23, T.DXT45])
def test_dxt_encode_decode_close(fmt):
    img = _gradient(64, 32, alpha=fmt != T.DXT1)
    data = T.encode(img, fmt, 1)
    assert len(data) == T.image_size(fmt, 64, 32, 1)
    out = T.decode(data, fmt, 64, 32)
    assert np.abs(out[..., :3].astype(int) - img[..., :3]).mean() < 6
    if fmt != T.DXT1:
        assert np.abs(out[..., 3].astype(int) - img[..., 3]).mean() < 10


def test_dxt_reencode_of_flat_block_is_exact():
    img = np.full((8, 8, 4), (16, 64, 200, 255), np.uint8)
    img[..., :3] = T.decode_dxt(T.encode_dxt(img, T.DXT1), T.DXT1, 8, 8)[..., :3]
    again = T.decode_dxt(T.encode_dxt(img, T.DXT1), T.DXT1, 8, 8)
    assert np.array_equal(again, img)


# -- uncompressed --------------------------------------------------------------------------


@pytest.mark.parametrize("fmt", [T.A8R8G8B8, T.A8R8G8B8 | T.LN])
def test_argb_round_trip(fmt):
    img = _gradient(16, 8)
    data = T.encode_level(img, fmt)
    assert np.array_equal(T.decode_level(data, fmt, 16, 8), img)


def test_argb_is_big_endian_argb():
    img = np.array([[[10, 20, 30, 40]]], np.uint8)  # R G B A
    assert T.encode_level(img, T.A8R8G8B8) == bytes([40, 10, 20, 30])


def test_b8_and_remap():
    img = _gradient(8, 8)
    data = T.encode_level(img, T.B8)
    out = T.decode(data, T.B8, 8, 8, remap=0x1A9FF)
    assert np.array_equal(out[..., 0], img[..., 0])
    assert (out[..., 1] == out[..., 0]).all() and (out[..., 3] == 255).all()


def test_identity_remap():
    img = _gradient(4, 4)
    assert np.array_equal(T.apply_remap(img, 0xAAE4), img)


def test_volume_round_trip():
    vol = np.stack([_gradient(4, 4)] * 4)
    vol[..., 2] = np.arange(4)[:, None, None] * 60
    data = T.encode_volume(vol, T.A8R8G8B8)
    assert np.array_equal(T.decode_volume(data, T.A8R8G8B8, 4, 4, 4), vol)


# -- whole images, mips, cube --------------------------------------------------------------


def test_mip_offsets_and_cube_faces():
    offs = T.level_offsets(T.DXT1, 16, 16, 5, faces=6)
    assert offs[0] == [0, 128, 160, 168, 176]
    assert offs[1][0] == 256  # 184 bytes padded to 128


def test_cube_encode_decode_faces():
    faces = [np.full((8, 8, 4), (i * 40, 0, 0, 255), np.uint8) for i in range(6)]
    data = T.encode(faces, T.A8R8G8B8, 1)
    for i in range(6):
        assert T.decode(data, T.A8R8G8B8, 8, 8, 1, face=i, faces=6)[0, 0, 0] == i * 40


def test_mip_chain_box_filter():
    img = np.zeros((4, 4, 4), np.uint8)
    img[:2, :2] = 200
    chain = T.mip_chain(img, 3)
    assert [m.shape[:2] for m in chain] == [(4, 4), (2, 2), (1, 1)]
    assert chain[1][0, 0, 0] == 200 and chain[2][0, 0, 0] == 50


# -- DDS and PNG ---------------------------------------------------------------------------


@pytest.mark.parametrize("fmt", [T.DXT1, T.DXT45, T.A8R8G8B8, T.B8])
def test_dds_round_trip(fmt):
    img = _gradient(16, 16)
    stored = T.encode(img, fmt)
    dds = T.stored_to_dds(stored, fmt, 16, 16, 5)
    back = T.read_dds(dds)
    assert (back.fmt, back.width, back.height, back.levels) == (fmt, 16, 16, 5)
    assert back.to_stored() == stored


def test_dds_cube_round_trip():
    faces = [_gradient(8, 8)] * 6
    stored = T.encode(faces, T.DXT1, 1)
    back = T.read_dds(T.stored_to_dds(stored, T.DXT1, 8, 8, 1, faces=6))
    assert len(back.faces) == 6 and back.to_stored() == stored


def test_dds_bad_magic():
    with pytest.raises(T.TextureError, match="DDS magic"):
        T.read_dds(b"XXXX" + bytes(124))


def test_png_round_trip():
    img = _gradient(13, 7)
    assert np.array_equal(T.read_png(T.write_png(img)), img)


# -- pak -----------------------------------------------------------------------------------


def test_pak_reader(tmp_path):
    body = b"A" * 0x800 + b"B" * 0x1000
    head = struct.pack(">4sIIIIII", b"pak2", 1, 0, 2, 0x800, 0x800, 0) + struct.pack(">2I", 1, 2)
    p = tmp_path / "x.pak"
    p.write_bytes(head + bytes(0x800 - len(head)) + body)
    pak = T.Pak(p)
    assert pak.count == 2 and pak.offset(1) == 0x1000
    assert pak.read(1, 0x10) == b"B" * 0x10


def test_gcm_texture_parse():
    raw = bytes.fromhex("8808020000 01aae4 0040 0080 0001 00 00 00000000 00000000".replace(" ", ""))
    tex = T.GcmTexture.parse(raw)
    assert (tex.format, tex.mipmap, tex.width, tex.height, tex.remap) == (0x88, 8, 64, 128, 0x1AAE4)
    assert tex.stored_size() == 0x2B00 and tex.pack() == raw


# -- real data (skipped without the game) --------------------------------------------------


def _zones():
    d = env.path_of("OPENT5_ZONES")
    if not d or not (d / "mp_nuked.pak").is_file():
        pytest.skip("OPENT5_ZONES with mp_nuked.pak not configured")
    return d


@pytest.mark.zones
def test_real_pak_header_and_dxt1_entry():
    pak = T.Pak(_zones() / "mp_nuked.pak")
    assert pak.count == 2053 and pak.starts[0] * 0x800 == pak.header_size == 0x2800
    # entry 0: ~-gus_art_wall_vinylsiding_white_c, 512x512 DXT1 level 0 (131072 bytes)
    data = pak.read(0, 0x20000)
    img = T.decode(data, T.DXT1, 512, 512)
    assert img[..., :3].mean() > 180  # white siding, not noise
    assert T.stored_to_dds(data, T.DXT1, 512, 512, 1)[128:] == data


@pytest.mark.zones
def test_real_images_low_mip_tail_is_consistent():
    pak = T.Pak(_zones() / "images_low.pak")
    # mip tail (64x64, 7 levels) of ~-gus_art_wall_vinylsiding_white_c, part slot 1 index 0x262e
    data = pak.read(0x262E, 0xB00)
    l0 = T.decode(data, T.DXT1, 64, 64, 7).astype(float)
    l1 = T.decode(data, T.DXT1, 64, 64, 7, level=1).astype(float)
    down = (l0[0::2, 0::2] + l0[1::2, 0::2] + l0[0::2, 1::2] + l0[1::2, 1::2]) / 4
    assert np.abs(down - l1).mean() < 6
