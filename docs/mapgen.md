# Procedural blocky map generator (`opent5.mapgen`, `tools/blockmap.py`)

A reproducible, seeded generator for a blocky (voxel) multiplayer map: rolling hills, caves,
trees, a water plane and a small village of hut shells, greedy-meshed into brushes and written
as a Radiant `.map` (iwmap 4) with its own pixel-art textures. The map is built headless with
the PC Mod Tools (cod2map, cod2rad, linker_pc; command lines in docs/research/box-map.md 1.2)
and converted to a PS3 map zone with the committed converter (docs/convert.md).

The only resemblance to any other game is the "blocky voxel terrain" style. Every material,
texture and shape here is this project's own code and art; no external asset is read, copied,
traced or named.

Package layout (owned by this track):

| Module | Role |
|---|---|
| `mapgen/noise.py` | Own value-noise (numpy), seeded; `fbm2`/`fbm3` fractal octaves |
| `mapgen/terrain.py` | Seeded voxel grid: hills, caves, trees, water, village; deterministic |
| `mapgen/greedy.py` | Greedy box meshing of same-material cells; per-face texture or caulk |
| `mapgen/textures.py` | Original 16x16 pixel-art tiles, nearest-neighbour upscaled, DXT encoded |
| `mapgen/mapwriter.py` | Radiant `.map` from brushes + spawns + sun + light grid + skybox |
| `mapgen/preview.py` | Top-down and isometric PNG previews (inspection only) |
| `tools/blockmap.py` | CLI: `gen`, `textures`, `build`, `convert` |

## 1. Generator design

Deterministic from a seed throughout: the noise is a value lattice addressed by integer world
coordinates hashed with the seed (SplitMix-style avalanche in masked uint64, so no result
depends on the sampled grid size), interpolated with a quintic smoothstep; `fbm2`/`fbm3` sum
octaves (frequency x2, amplitude x0.5) and normalise to [0, 1]. Two runs with the same seed give
byte-identical grids (`test_mapgen.py`).

Terrain (`generate(nx, ny, nz, block, seed)`, default 40x40x32 cubes of 64 units, centred on the
origin):

1. Heightmap: `0.78 x broad fbm + 0.22 x fine fbm`, rescaled to a cell band. Columns fill stone,
   then three dirt, then a grass top (sand near and below the sea).
2. Water: empty cells at or below the sea level become water; underwater ground becomes sand.
3. Village: a flat central area is levelled to its median height, laid with a cobble top, and
   given two plank hut shells (walls, floor, flat roof, a door opening); spawns keep clear of it.
4. Caves: a 3D fractal field carves air pockets in a band two or more cells below the surface
   (threshold 0.62), giving one or two connected hollows.
5. Trees: on grass tops above the sea, away from the village, where a placement field peaks: a
   log trunk (3 to 5 cells) and a trimmed 5x5x3 leaf canopy.

Materials (cell ids): stone, dirt, grass, sand, water, log, leaves, plank, cobble. A grass cell
textures its top face as grass, its sides as grass-side and its bottom as dirt; a log cell
textures its z faces as log-top (rings) and its sides as bark.

## 2. Greedy meshing, brush and collision counts

`greedy.mesh` merges same-material cells into the largest axis-aligned boxes (grow along x, then
y, then z), one brush per box. Each box face is textured by `(material, direction)` when any
neighbour across it is air or water, and `caulk` (nodraw, collision kept) when it is fully hidden
behind opaque cells. Every meshable cell lands in exactly one box, with no overlap
(`test_mesh_covers_every_solid_cell_once`).

Engine limits found, with evidence (OpenAssetTools `src/Common/Game/T5/T5_Assets.h`, the structs
the PS3 loader reads; cod2map/linker_pc would be the compile-time gate but those PC tools are not
installed on this machine, so the data-type caps are the evidence used):

| Limit | Cap | Evidence |
|---|---|---|
| collision brushes | 65535 | `clipMap_t.numBrushes` is `uint16_t` |
| gfx surfaces | 65535 | `GfxWorldDpvsStatic surfaceCount` is `uint16_t` |
| per-group vertices | 65535 | `GfxWorld ... indices` is `uint16_t*`; a surface group is u16-addressable |

The default map (seed 1, 40x40x32) produces, well under every cap:

| Count | Value | Headroom |
|---|---|---|
| collision brushes | 2778 | 62757 |
| gfx surfaces (non-caulk quads) | 6207 | 59328 |
| caulk (hidden) faces | 10461 | nodraw |
| vertices (pre-weld, 4 per surface) | 24828 | 40707 |
| indices (6 per surface) | 37242 | |

Greedy meshing reduces 19607 solid/water cubes to 2778 brushes (`test_mesh_merges_below_cube_count`).
cod2map welds and splits vertices by group, so the final zone counts can only fall; the figures
above are the generator's own upper bounds.

## 3. Textures

Each tile is drawn as 16x16 RGBA pixel art (a per-tile seeded speckle over a base colour, plus a
few deliberate features) and upscaled with nearest-neighbour to the engine size (default 64x64),
so the pixels stay crisp. DXT1 (opaque) or DXT45 (water, which has alpha) with a full mip chain,
through `opent5.formats.texture`. All were previewed to PNG and looked at; each reads as what it
is:

| Tile | Looks like |
|---|---|
| grass_top | green turf with brighter blades and darker tufts |
| grass_side | a green lip with a jagged fringe dripping onto dirt below |
| dirt | brown soil with darker clumps |
| stone | grey rock with a few darker cracks |
| sand | pale yellow grains with lighter speckles |
| plank | four horizontal wood planks, dark seams, faint vertical grain |
| log_side | vertical bark streaks with a lighter rim top and bottom |
| log_top | concentric growth rings around a heart knot |
| leaves | mottled dark green foliage, lighter and darker clusters |
| water | blue with horizontal wave highlights, semi-transparent |
| cobble | irregular grey stones on a darker mortar grid |

DXT round-trips at a mean RGB error of about 2 of 255 (`test_texture_dxt_roundtrip`). Own art is
handed to the converter as `overrides` keyed by the colour-map name `~-g<material>_c`
(docs/convert.md 11.4, demo-box-textures.md 4), so no PC image converter is needed.

## 4. Building the `.map` with the PC tools

`tools/blockmap.py build NAME --game GAME --work WORK -o OUT.ff` writes the `.map` into `--work`
and runs the exact three command lines of tools/testmap.py / box-map.md 1.2 through
`launcher_ldr.exe` (cod2map, cod2rad `-fast`, linker_pc), creating only new `<NAME>.*` files in
the game folder (see that list in box-map.md 7). The map name is `mp_opent5blocks`, so every
game-folder file is `mp_opent5*`.

Status: the PC Mod Tools (`bin/launcher_ldr.exe`, `cod2map.dll`, `linker_pc.dll`) are NOT present
on this machine, so no `.map` was compiled and no `.ff` was produced here. `build` reports this
and stops; the `.map`, textures and previews from `gen` are the offline deliverables. For the PC
build each `mp_opent5blocks_*` material needs a stub material definition and a colour-map image
registered in the tools' `raw/materials` and `raw/images` (all named `mp_opent5*`), as the stock
`blockout_test_*` materials are (demo-box-textures.md); the converter then replaces the
colour-map pixels with the generated art. This is the gap for the lead (section 6).

Lighting: worldspawn carries the sun (`sundirection`, `sunlight`, `suncolor`, ambient); a
`lightgrid_volume` brush fills the playable band so cod2rad bakes a real light grid
(docs/convert.md 10.3); a skybox of `sky` faces encloses the world so it is sealed and lit
outdoors (the terrain's k=0 layer is solid in every column, sealing the base,
`test_terrain_floor_is_sealed`). Spawns: `info_player_start`, `mp_global_intermission`, four
`mp_tdm_spawn_*_start` per team and a spread of `mp_tdm_spawn` (TDM) and `mp_dm_spawn` (FFA) on
flat surface cells; two `minimap_corner` origins for the compass.

## 5. Conversion and offline validation

`tools/blockmap.py convert PC_MAP.ff -o OUTDIR [--base mp_nuked]` drives the committed converter
`opent5.convert.mapzone.convert_map`, passing the block textures as `overrides`. The converter is
ready for this map: its signature already accepts `overrides`, `name`, `image_roots`,
`force_materials` and baked lighting (docs/convert.md 11, 13); new materials (none of the block
materials exist in mp_nuked) go through `materials.py` with the techset remapped by name. The one
thing it cannot do without the PC build is read the PC material/techset structure, which lives in
`PC_MAP.ff`. So the conversion is blocked only by the absent PC tools, not by the converter.

Offline validation done here (no console, no emulator beyond the committed loader path, no
network):

| Check | Result |
|---|---|
| Determinism | same seed gives byte-identical grid and `.map` sha1 (`gen` twice) |
| Mesh coverage | every solid/water cell in exactly one brush, no overlaps |
| Counts vs limits | 2778 / 65535 brushes, 6207 / 65535 surfaces (section 2) |
| Texture DXT round-trip | mean RGB error about 2 of 255; water has alpha |
| `.map` well-formed | brace-balanced, worldspawn + all spawn classes + light grid + skybox |
| Tests | `tests/test_mapgen.py`, 20 passing; `test_no_network` clean; ruff clean |

Not validated here (needs the PC build first, then a device run): the compiled PC `.ff`, the PS3
conversion, the emulated loader walk, and rendering.

## 6. Previews

`gen` writes `preview_top.png` (plan, north up, height-shaded) and `preview_angle.png` (isometric,
back to front). Looked at for seed 1: the plan shows green hills, blue ponds with sandy shores,
dark-green tree canopies and the grey cobble village with two brown hut roofs; the isometric view
shows the same as blocky 3D terrain with trees standing above the hills and the village on a flat
pad. Hills, trees, water and village are all clearly visible.

## 7. Files created and open items

In the game folder: none (the PC tools are absent, so `build` created nothing). When run where the
tools exist, `build` creates only `mp_opent5blocks.*` under `raw/maps/mp`, `zone/English`,
`zone_source` and `zone_source/english/assetinfo|assetlist` (box-map.md 7).

Open items / gaps for the lead:

- PC Mod Tools are not installed on this machine: the `.map` cannot be compiled and no PC `.ff`
  can be produced, which also blocks the conversion and every device-side check.
- The `mp_opent5blocks_*` materials and their `sky` material need stub material definitions and
  colour-map images in the tools' `raw/materials` / `raw/images` (named `mp_opent5*`) before
  cod2map/linker will build the map; the generated art then overrides the colour-map pixels.
- Texture scale in the `.map` (one tile per block) is set to the block size and is INFERRED; the
  exact UV wants a device render to confirm the blocky tiling.
- Water is a plain textured slab with a water-looking material; a true engine water surface
  (reflection, fog volume) is not modelled.
