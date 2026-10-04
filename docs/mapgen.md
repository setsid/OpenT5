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
| `mapgen/mapwriter.py` | Radiant `.map` from brushes + spawns + primary lights + light grid + caulk shell + path nodes |
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
handed to the converter as `overrides` keyed by each tile's stock colour-map image name
(`mapwriter.STOCK_COLORMAP`, e.g. `~-gblockout_rock_test_c`), so no PC image converter is needed.

## 4. Building the `.map` with the PC tools

`tools/blockmap.py build NAME --game GAME --work WORK -o OUT.ff` checks the stock block materials
are present (below), writes the `.map` into `--work`, and runs the exact three command lines of
tools/testmap.py / box-map.md 1.2 through `launcher_ldr.exe` (cod2map, cod2rad `-fast`, linker_pc).
It writes no new material or image into the game folder.

Materials: each block face uses a stock material (`mapwriter.STOCK_MATERIAL`) rather than a clone.
Eight are `blockout_test_*` placeholder materials and three are mp_nuked art materials (floor,
wall, ceiling), chosen so that all eleven have a colour-map IWI loose in `raw/images` and all bake
through cod2rad. `require_stock_assets` checks they are present. The clones used earlier (one
material per tile copied from `blockout_test_wood`, with a byte-copied IWI) crashed cod2rad, so
they were dropped. The converter force-builds each block material from the PC zone and overrides
its colour map with the generated art (`overrides`, section 5), so the block textures are the
map's own while the material structure stays stock.

Lighting and sealing: a caulk shell (ceiling, four walls, floor slab) encloses the world so it is
sealed (`seal_brushes`; the terrain's k=0 layer is solid in every column,
`test_terrain_floor_is_sealed`). A `lightgrid_volume` brush fills the playable band so cod2rad
bakes a light grid, and a 3x3 grid of primary spot lights (`primary_lights`, each with an
`info_null` target) lights the sealed world. There is no outdoor sun: this Mod Tools install has
no sky techset (the sky materials' techsets, e.g. `sky_cubemap_hdr`, are absent from
`raw/techsets`), so a sky material makes the linker assert `missing techset`. The shell is caulk
instead, and the primary lights stand in for the sun; lighting is even rather than directional.
Spawns: `info_player_start`, `mp_global_intermission`, four `mp_tdm_spawn_*_start` per team and a
spread of `mp_tdm_spawn` (TDM) and `mp_dm_spawn` (FFA) on flat surface cells; two `minimap_corner`
origins for the compass; and a `node_pathnode` grid on walkable cells by default, so Domination
and the other team-based modes do not stall at load.

Status (verified here): the PC build runs. cod2map, cod2rad and linker_pc all return 0 for the
generated map. `blockmap convert` does not yet parse the PC zone exactly: its gfx_map RUNTIME
block is 0x780 short of the header. This is a PC-parse gap in the gfx_map RUNTIME handler
(convert/pc.py), exposed by the blocky map's terrain and nine-light structure, not present on the
box (which parses exactly). It is unrelated to ropes: col_map and game_map both parse exactly, and
every gfx_map RUNTIME reserve size is captured, so the 0x780 is an alignment or sizing subtlety in
one gfx_map array for this map's counts. Parked until found; the earlier converted blocky zone was
produced with a wrong rope stride that happened to mask the gap and should not be trusted. The
generator side (map, materials, sealing, lighting, path nodes) is correct and compiles.

## 5. Conversion and offline validation

`tools/blockmap.py convert PC_MAP.ff -o OUTDIR [--base mp_nuked]` drives the committed converter
`opent5.convert.mapzone.convert_map`, passing the block textures as `overrides`. The converter is
ready for this map: its signature already accepts `overrides`, `name`, `image_roots`,
`force_materials` and baked lighting (docs/convert.md 11, 13); new materials (none of the block
materials exist in mp_nuked) go through `materials.py` with the techset remapped by name. The one
thing it cannot do without the PC build is read the PC material/techset structure, which lives in
`PC_MAP.ff`. So the conversion is blocked only by the PC build (the launcher loader, section 4),
not by the converter. `blockmap convert` wires both forms: the mp_nuked replacement
(`out/demo/m_blocks/`) and the own-name zone (`--name`, `--copy-pak`, its own compass,
`out/demo/m_blocks_named/`).

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

New files created in the Steam game folder (all `mp_opent5blocks*`): 11 materials
`raw/materials/mp_opent5blocks_<tile>`, 11 colour maps `raw/images/mp_opent5blocks_<tile>_c.iwi`,
`zone_source/mp_opent5blocks.csv`, and `raw/maps/mp/mp_opent5blocks.{d3dbsp,d3dprt,d3dpoly,grid_auto}`
(the BSP is currently the v31 from `cod2map.exe` alone; the launcher v45 BSP and the linked
`zone/English/mp_opent5blocks.ff` are not yet produced, section 4). Nothing else in the game
folder was changed.

Open items / gaps for the lead:

- The LinkerMod loader is wedged: `launcher_ldr.exe` + `cod2map.dll` fail with "Access is denied."
  / EXITCODE 5 for every map (a trivial box too), while `cod2map.exe` alone works. A protected
  `launcher-x64.exe` (PID 12280, session 0) cannot be killed by this user. Clear that process (or
  the security policy blocking DLL injection), then re-run `blockmap build` and `blockmap convert`;
  everything else is in place.
- (historic) The PC Mod Tools were first thought absent; they are present at
  `.../Call of Duty Black Ops/bin/`. The remaining blocker is the loader above, not the tools.
- The block materials, their colour-map IWIs and the sky material are registered (section 4);
  `cod2map.exe` resolves them all and seals the map, so this is done, pending the loader.
- Texture scale in the `.map` (one tile per block) is set to the block size and is INFERRED; the
  exact UV wants a device render to confirm the blocky tiling.
- Water is a plain textured slab with a water-looking material; a true engine water surface
  (reflection, fog volume) is not modelled.
