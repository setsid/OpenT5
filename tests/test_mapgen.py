"""Procedural blocky map generator: noise, terrain, greedy meshing, textures, the .map."""

import numpy as np
import pytest

from opent5.formats import texture as tx
from opent5.mapgen import greedy, mapwriter, noise, preview, terrain, textures

# -- noise ---------------------------------------------------------------------------------


def test_noise_range_and_determinism():
    xs = np.linspace(0, 10, 50)[:, None]
    ys = np.linspace(0, 10, 50)[None, :]
    a = noise.fbm2(xs, ys, seed=3)
    b = noise.fbm2(xs, ys, seed=3)
    assert np.array_equal(a, b)
    assert a.min() >= 0.0 and a.max() <= 1.0
    assert not np.array_equal(a, noise.fbm2(xs, ys, seed=4))


def test_noise3_range():
    g = np.mgrid[0:8, 0:8, 0:8].astype(float)
    v = noise.fbm3(g[0], g[1], g[2], seed=1)
    assert v.shape == (8, 8, 8)
    assert v.min() >= 0.0 and v.max() <= 1.0


# -- terrain -------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def small():
    return terrain.generate(nx=24, ny=24, nz=24, block=64, seed=2)


def test_terrain_deterministic():
    a = terrain.generate(nx=20, ny=20, nz=20, seed=5)
    b = terrain.generate(nx=20, ny=20, nz=20, seed=5)
    assert np.array_equal(a.grid, b.grid)
    assert not np.array_equal(a.grid, terrain.generate(nx=20, ny=20, nz=20, seed=6).grid)


def test_terrain_has_every_feature(small):
    present = set(np.unique(small.grid))
    for m in (terrain.GRASS, terrain.DIRT, terrain.STONE, terrain.WATER, terrain.PLANK):
        assert m in present, terrain.MATERIAL_NAMES.get(m)
    assert small.counts["huts"] >= 1
    assert small.counts["water_cells"] > 0


def test_terrain_floor_is_sealed(small):
    # k=0 is solid in every column, so the map base is closed (mapwriter relies on this).
    assert bool((small.grid[:, :, 0] != terrain.AIR).all())


def test_village_is_flat(small):
    vx0, vy0, vx1, vy1 = small.village
    h = small.heights[vx0:vx1, vy0:vy1]
    assert h.max() - h.min() <= 1


# -- greedy meshing ------------------------------------------------------------------------


def test_mesh_covers_every_solid_cell_once(small):
    boxes, counts = greedy.mesh(small)
    meshable = int(np.isin(small.grid, list(terrain.OPAQUE | {terrain.WATER})).sum())
    covered = 0
    seen = np.zeros(small.shape, bool)
    b = small.block
    ox, oy, oz = small.origin
    for box in boxes:
        i0 = (box.lo[0] - ox) // b
        j0 = (box.lo[1] - oy) // b
        k0 = (box.lo[2] - oz) // b
        i1 = (box.hi[0] - ox) // b
        j1 = (box.hi[1] - oy) // b
        k1 = (box.hi[2] - oz) // b
        assert not seen[i0:i1, j0:j1, k0:k1].any()  # no overlap
        seen[i0:i1, j0:j1, k0:k1] = True
        covered += (i1 - i0) * (j1 - j0) * (k1 - k0)
    assert covered == meshable
    assert counts["brushes"] == len(boxes)


def test_mesh_counts_under_engine_limits(small):
    _, counts = greedy.mesh(small)
    assert counts["brushes"] < 65535  # clipMap_t.numBrushes is uint16_t (OAT T5_Assets.h)
    assert counts["surfaces"] < 65535  # GfxWorldDpvsStatic surfaceCount is uint16_t
    assert counts["caulk_faces"] > 0  # hidden faces are nodraw/caulk


def test_mesh_merges_below_cube_count(small):
    boxes, _ = greedy.mesh(small)
    cubes = int(np.isin(small.grid, list(terrain.OPAQUE | {terrain.WATER})).sum())
    assert len(boxes) < cubes  # greedy merge actually reduces the brush count


def test_grass_box_faces():
    # Grass: top=grass_top, sides=grass_side, bottom=dirt; log: z faces=log_top, sides=log_side.
    assert greedy.face_tile(terrain.GRASS, "top") == "grass_top"
    assert greedy.face_tile(terrain.GRASS, "bottom") == "dirt"
    assert greedy.face_tile(terrain.GRASS, "xmin") == "grass_side"
    assert greedy.face_tile(terrain.LOG, "top") == "log_top"
    assert greedy.face_tile(terrain.LOG, "xmax") == "log_side"


# -- textures ------------------------------------------------------------------------------


def test_every_tile_renders_and_is_deterministic():
    for name in textures.TILE_NAMES:
        a = textures.render(name, 64, seed=1)
        assert a.shape == (64, 64, 4)
        assert np.array_equal(a, textures.render(name, 64, seed=1))
    assert set(textures.TILE_NAMES) == set(textures._DRAW)


def test_upscale_is_nearest_neighbour():
    base = textures.base_tile("stone", 1)
    up = textures.upscale(base, 4)
    assert up.shape == (64, 64, 4)
    # every 4x4 block equals one source pixel (crisp, no interpolation)
    assert np.array_equal(up[0:4, 0:4], np.broadcast_to(base[0, 0], (4, 4, 4)))


def test_texture_dxt_roundtrip():
    for name in textures.TILE_NAMES:
        rgba = textures.render(name, 64, 1)
        fmt, stored = textures.encode_ps3(rgba)
        dec = tx.decode(stored, fmt, 64, 64, tx.full_levels(64, 64))
        err = np.abs(dec[..., :3].astype(int) - rgba[..., :3].astype(int)).mean()
        assert err < 8, (name, err)
    assert textures.has_alpha(textures.render("water", 64))
    assert not textures.has_alpha(textures.render("grass_top", 64))


def test_bad_tile_name():
    with pytest.raises(KeyError):
        textures.base_tile("obsidian")


# -- map writer ----------------------------------------------------------------------------


@pytest.fixture(scope="module")
def built(small):
    boxes, _ = greedy.mesh(small)
    return small, boxes, mapwriter.map_text(small, boxes)


def test_map_braces_balanced(built):
    _, _, text = built
    assert text.startswith("iwmap 4")
    assert text.count("{") == text.count("}")


def test_map_has_worldspawn_and_spawns(built):
    _, _, text = built
    assert '"classname" "worldspawn"' in text
    for cls in (
        "info_player_start",
        "mp_global_intermission",
        "mp_tdm_spawn_allies_start",
        "mp_tdm_spawn_axis_start",
        "mp_tdm_spawn",
        "mp_dm_spawn",
        "minimap_corner",
    ):
        assert cls in text, cls


def test_map_has_own_materials_and_lighting(built):
    _, _, text = built
    assert "blockout_test_fabric01" in text  # grass_top
    assert "blockout_test_wood" in text  # grass_side
    assert mapwriter.LIGHT_GRID in text
    assert '"classname" "light"' in text  # primary lights (no sky techset, so no sun)
    assert "caulk" in text  # hidden faces and the sealing shell


def test_material_and_colormap_names():
    assert mapwriter.material_name("grass_top") == "blockout_test_fabric01"
    assert mapwriter.material_name("caulk") == "caulk"
    assert mapwriter.colormap_name("dirt") == "~-gblockout_concrete_med_test_c"
    # distinct material and colour map per tile (so each gets its own art override)
    tiles = mapwriter.STOCK_MATERIAL
    assert len(set(tiles.values())) == len(tiles)
    assert len(set(mapwriter.STOCK_COLORMAP.values())) == len(mapwriter.STOCK_COLORMAP)


def test_spawn_classes_present_for_tdm_and_ffa(built):
    t, _, _ = built
    classes = {e["classname"] for e in mapwriter.spawn_entities(t)}
    need = {"mp_tdm_spawn_allies_start", "mp_tdm_spawn_axis_start", "mp_tdm_spawn", "mp_dm_spawn"}
    assert need <= classes


# -- preview (smoke) -----------------------------------------------------------------------


def test_previews_render(small):
    top = preview.top_view(small, px=4)
    ang = preview.angle_view(small, px=5)
    assert top.ndim == 3 and top.shape[2] == 4
    assert ang.ndim == 3 and ang.shape[2] == 4
    assert preview.write(top)[:8] == b"\x89PNG\r\n\x1a\n"
