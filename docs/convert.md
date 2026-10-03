# Converting a PC Mod Tools map to a PS3 map zone

`opent5 convert` turns a map built with the PC Mod Tools (cod2map, cod2rad, linker_pc; how
the box map was built is in docs/research/box-map.md section 1) into a PS3 map zone that a
signature-patched PS3 client can load. First target: a sealed box (`mp_opent5box`), shipped
inside `mp_nuked.ff`. The user-facing test procedure is docs/demo-box.md.

    opent5 convert PC_MAP.ff --base mp_nuked -o OUTDIR [--lighting flat|sunlit|keep] [--json]

`PC_MAP.ff` is the PC zone (`zone/English/<map>.ff` of the PC tools, `IWffu100`). `--base` is
a PS3 map zone (a bare name is looked up in the .env folders) whose world is replaced; the
output is `OUTDIR/<base>.ff` plus `OUTDIR/convert.json` (every decision below, with numbers).
Nothing is written into the game folders or over a source. The run takes about 10 s.

Supporting tool (not product code): `tools/convert_map.py convert | compare | oracle`
(section 4 and 5).

Conventions: "PC zone" is the inflated PC stream, "PS3 zone" the inflated PS3 XFile stream;
offsets are into those streams. Type numbers are PS3 numbers.

## 1. Design

Strategy (box-map.md 4.2): keep a stock PS3 map zone, which already holds everything the PC
tools cannot make for PS3 (RSX techsets, PS3 materials and images with their `.pak`, the twelve
`configstrings_ps3_*` tables, the two sound banks, the texture list), and replace its world:
com_map, gfx_map, game_map_mp and col_map_mp with its map_ents, under the base's names. Every
asset is then written by the product writer and every offset pointer remapped.

| Module | What it does |
|---|---|
| `xfile/stream.py`, `model.py`, `handlers/base.py`, `remap.py` | Byte order and handler set are a `Platform` (endian, handler registry, stored-type to registered-type map). `Chunk` reads big-endian, `ChunkLE` little-endian; `XStream` / `XWriter` take their chunk type, pointer reader and writer from the platform; `parse`, `write`, `write_asset`, `traced_parse`, `traced_write` and `Rewrite` take `platform=` (default `PS3`); `XFile.platform` records it; `load_asset` and the asset walk use the stream's registry. PS3 behaviour is unchanged (section 6) |
| `convert/pc.py` | PC container (`IWffu100`, u32 LE 473, one zlib stream), the `PC` platform (PC types 0..6 as PS3, 7..41 +2, 42 -> 45; pc-route.md 1.3), PC handlers for the structs that differ: techset (528, D3D9 programs, reusable -1/-2 pointers), material (192), image (0x34 + loadDef in TEMP), rawfile (buffer align 1), GfxWorld (0x43c). Every other type uses the PS3 handler. `walk_range` walks a stock PC map from its com_map |
| `convert/swap.py` | Field-by-field byte swap of same-layout structs from the PC layouts (`xfile/layouts_pc.py`) plus corrections (cLeaf_t, cbrush_t, the cLeafBrushNode_s union, CollisionAabbTree, GfxLightGridEntry, GfxLightRegionAxis) |
| `convert/world.py` | clipMap_t + MapEnts, ComWorld, GameWorldMp: swap in place. GfxWorld: header re-layout, vertex regrouping, layer data, surfaces, light grid rows (section 3) |
| `convert/splice.py` | `Splice`: a `Rewrite` whose nodes come from two parses (section 2) |
| `convert/scripts.py` | Minimal map scripts, entity parsing, per-gametype readiness, the base map's compatibility entities |
| `convert/lighting.py` | Lightmap coordinates from the base map's own lightmap (section 3.5) |
| `convert/mapzone.py` | `convert_map`: the whole conversion and the offline checks |
| `convert/compare.py` | The converter against a map that exists on both platforms (section 4) |

### 1.1 Per-asset status (box into mp_nuked, `out/demo/e_box/convert.json`)

| Asset (index in mp_nuked) | Action |
|---|---|
| com_map (367) | replaced by the swapped PC ComWorld: 2 primary lights (0 empty, 1 the sun); name `maps/mp/mp_nuked.d3dbsp` |
| lightdef `white_light` (368) | kept; its name was an offset pointer into the stock ComWorld's light names, now stored inline (the stock ComWorld is gone) |
| techsets, materials, images, xmodels, fx, physpresets, destructibledefs, xanim | kept unchanged (the stock xmodels and fx stay loaded but are not placed) |
| gfx_map (405) | replaced by the converted PC GfxWorld (3 surfaces, 24 vertices, 36 indices, 1 cell, 6 planes); stock sky, sun light, reflection probes, lightmaps, draw images, material memory, sun flare and outdoor image kept under it (section 3) |
| game_map_mp (406) | replaced by the swapped PC GameWorldMp: 0 path nodes (the 128 spare slots stored) |
| col_map_mp (407) + map_ents | replaced by the swapped PC clipMap_t (6 brushes, 7 nodes, 9 leafs, 1 cmodel, 256 empty dynamic entity definitions) and the PC entity string plus 7 compatibility entities (section 3.6) |
| `maps/mp/mp_nuked.gsc` (427) | minimal main() (section 3.6) |
| `maps/mp/createfx/mp_nuked_fx.gsc` (430), `clientscripts/mp/createfx/mp_nuked_fx.csc` (437) | `main()` with an empty body |
| other map scripts (428, 429, 431 .. 436) | kept; read and checked (section 3.6) |
| configstrings tables (0..11), sound banks (442, 443), texturelist (526) | kept |
| glasses (527) | numGlasses 62 -> 0, glasses pointer 0 |

`opent5 verify out/demo/e_box/mp_nuked.ff --against mp_nuked`: 414 assets identical, 106
identical apart from remapped offset pointers (17 983 pointers checked), and exactly the nine
assets above changed (367, 368, 405, 406, 407, 427, 430, 437, 527).

## 2. Writing nodes from two parses

`Rewrite` (remap.md section 8 and the node rewrite) names every allocation by what was loaded
there: (`"L"`, node, key) for a Load_Stream into node[key], (`"S"`, node, key) for an inline
string, (`"P"`, asset node) for an alias slot; elements of an array of structs are followed by
their own node identity. `Splice(base, foreign)` extends that to nodes from two traced parses
(the PS3 base, PS3 platform; the PC map, PC platform):

1. The converted PC nodes are put into the base XFile in place of the stock world assets. They
   keep their identity: the PC clipMap's planes pointer (an offset pointer into the PC
   GfxWorld's planes, `809f9ba1` in the converted zone) still names ("L", PC GfxWorld, "planes"),
   and the GfxWorld node is converted in place so that identity is written.
2. The zone is written by the PS3 handlers with tracing.
3. For every OFFSET / ALIAS_REF pointer in the written stream, the node holding the field says
   which parse its value comes from: a node the base parse loaded, a node the PC parse loaded,
   or a node the converter built and registered (`origin`), with byte-range overrides for a
   header that mixes both (`origin_ranges`; the converted GfxWorld header keeps six stock ranges,
   section 3.1). The value is resolved in that parse's layout and trace, renamed when stock
   data now hangs under a converted node (`moved`: the stock GfxWorld's images and materials;
   the stock world names, since the last rawfile `mp_nuked` is named by the stock GfxWorld's
   base-name string), and looked up in the written layout.
4. A PC value naming the script strings, the asset array or a RUNTIME reservation, or any value
   whose target was not written, is refused with the field's offset.

Box into mp_nuked: 53 166 offset / alias pointers, 53 141 resolved through the base and 25
through the PC zone, 17 476 rewritten.

## 3. GfxWorld, field by field

### 3.1 Header

PS3 = PC[0x0, 0x24) + 20 PS3-only bytes + PC[0x24, 0x218) + 4 PS3-only bytes + PC[0x218,
0x43c), every field swapped (box-map.md 3.4). Kept from the stock header: 0x24..0x37
(PS3-only, zero in mp_nuked), 0x40..0x4b (sky image, sky sampler state, sky box model),
0x100 (sun light), 0x16c..0x207 (reflection probes, lightmaps, draw images), 0x22c (PS3-only,
zero), 0x28c..0x337 (material memory, sun sprite and flare, sun data, outdoor image). Set by the
converter: 0x208 vertex count, 0x20c / 0x218 / 0x228 data pointers, 0x210 0, 0x214 layer data
size, 0x224 index count.

### 3.2 Vertices, groups and surfaces

A PC GfxSurface's (firstVertex, vertexCount) names a vertex group several surfaces share,
and its indices are relative to the group's first vertex. Reading PS3 mp_nuked against PC
mp_nuked (section 4) gives the PS3 rule exactly:

- vertex groups are stored in the order the surfaces first use them, each group's vertices
  in PC order;
- the index buffer is each surface's index run in surface order, values unchanged;
- a PS3 surface stores +0xc the group's layer-data offset, +0x1c the group's first vertex,
  +0x20 the lowest index the surface uses and +0x24 the count up to its highest (the PS3 value
  INFERRED as "a vertex offset relative to +0x1c" in `xfile/structs.py` is that lowest index:
  it equals the minimum index of the surface in all 3494 mp_nuked surfaces), +0x28 the running
  base index;
- mins / maxs (+0x0, +0x10): PC values; where the PC bounds are empty (131072 / -131072) PS3
  has the triangles' bounds widened by 1 in 179 of 230 surfaces and keeps the empty bounds in
  the other 51 (rule for the choice not found; the converter widens all 230).

Positions: 16 bytes per vertex (x, y, z, binormal sign), f32 BE. Layer data per group: colour
RGBA (the PC D3DCOLOR is BGRA: proven on all 243 groups), uv, lightmap uv (f32 BE), normal and
tangent as CMP 11:11:10, then the PC per-layer extras. The PC normal is a PackedUnitVec
(b - 127) x (b3 + 192) / 32385; PS3 stores it normalised (PC `a0047f3f` has length 1.0028, the
PS3 word has y = -988, the normalised value), rounded. The extras: pairs of f32 (one uv per
extra layer, swapped), then a byte word (a colour, unchanged) when four bytes remain. PC layer
data holds only the extras, one run per group at the group's PC vertexLayerData; groups without
extras have offset 0, so the run at offset 0 belongs to the one group whose vertex count divides
it into a valid stride (the box's 4-byte layer data has no owner and is dropped).

### 3.3 Other arrays

Planes, nodes, streaming aabb trees and leaf refs, cells (with their aabb trees and portals),
brush models, sorted surface indices, shadow geometry, light regions: swapped. Light grid:
rowDataStart and entries swapped; rawRowData holds rows at 4 x rowDataStart[i] (0xffff: none),
each u16 colStart, colCount, zStart, zCount, u32 firstEntry, then lookup bytes, so those five
fields are swapped and the bytes kept (swapping the whole array as u16, box-map.md 3.4, leaves
5310 of 7081 words wrong; this rule leaves none); colours are bytes. Refused when they hold
data: static models (`smodel_insts`, `smodel_draw_insts`), hero lights, water buffers, cell cull
groups.

### 3.4 Materials

A converted surface's material is the alias pointer of a stock surface drawn with the same
material name (box: `wc/us_art_wall_vinylsiding_white` walls, `wc/jun_art_concrete_base02`
floor, `wc/pent_art_wall_creampaint02` ceiling; all three are stock mp_nuked world materials
with stride-28 groups). A PC material the base does not use stops the conversion with its
name.

### 3.5 Lighting (decision and evidence)

The PC lightmap is a PC image and is not converted; the base keeps its own lightmap
(mp_nuked: one, `*lightmap0_primary` DXT1 2048 x 2048, `*lightmap0_secondary` R5G6B5 1024 x
2048, `*lightmap0_secondaryb` Y16_X16 1024 x 1024, pixels deferred in the zone). The PC
lightmap coordinates would sample it at arbitrary places, so the default (`flat`) gives every
vertex of a converted surface one coordinate: among the stock surfaces drawn with the same
material, the vertex whose secondary texel is brightest inside an even 5 x 5 patch (luminance
standard deviation at most 6 of 255, 3 texels or more from the image border). The surface also
takes that donor's lightmap and reflection probe indices. Read from the decoded images:

| Material | Donor (stock surface, vertex) | Lightmap uv | Secondary luminance | Primary green |
|---|---|---|---|---|
| wc/us_art_wall_vinylsiding_white | 380, 13910 | 0.565063, 0.536682 | 127.5 | 0 |
| wc/jun_art_concrete_base02 | 832, 69998 | 0.57666, 0.104301 | 110.6 | 0 |
| wc/pent_art_wall_creampaint02 | 1014, 77759 | 0.928223, 0.164551 | 131.4 | 0 |

The primary is a mask: red and blue are 0 everywhere, green is 0 on 89% of the texels and 240
or more on 9% (INFERRED: sun visibility). Even sunlit patches of the same materials have
secondary luminance 27.3 (`--lighting sunlit`, which picks among them; the creampaint ceiling
has no sunlit even patch and keeps its shaded donor), so the shaded donors carry the most
non-sun light: a closed box is in the sun's shadow (INFERRED), and the flat choice keeps the
walls lit by the secondary whatever the sun does. INFERRED until seen on a console: how the
renderer combines the three images; that the result is not black.

### 3.6 Scripts, entities, glass and dynamic entities

Scripts. Stock `maps/mp/mp_nuked.gsc` starts four threads that use entities without checking
them: the doomsday clock (`GetEnt("clock_min_hand")`, `RotatePitch`), the population sign
(`counter_tens`, `counter_ones`, `RotateRoll`), the end-game bomb (`GetStruct("endgame_camera_
start")`, `.target`, `GetEnt("nuked_bomb")`) and the mannequins (safe: an empty array returns).
The minimal main() keeps only `maps\mp\mp_nuked_fx::main()`, `maps\mp\_load::main()`,
`maps\mp\mp_nuked_amb::main()`, the compass (`setupMiniMap("compass_map_mp_nuked")`), the
teamset (`_teamset_urbanspecops::level_init()`), dvars, callsign strings and
`level_use_unified_spawning(true)`; the createfx files (3.4 KB each of one-shot effects at
Nuketown positions) become empty. Read and kept: `mp_nuked_fx.gsc` (loadfx of assets in the
zone, the dust devil xanim, calls createfx and createart main), `mp_nuked_amb.gsc` (empty),
`mp_nuked_platform.gsc` (checks `IsDefined` on its trigger), `createart/mp_nuked_art.gsc` (fog
and vision dvars), the client scripts (`GetEntArray` results loop over nothing). Stock
`maps/mp/_load.gsc` (common_mp) only reads optional entity arrays; `_spawnlogic.gsc` aborts the
level when a gametype's spawn class is missing and asserts one `mp_global_intermission`.

Finding: the installed update's `patch_mp.ff` (sha1 499ce654..., identical to the user's
`patch_mp.ff.retail.bak`) carries its own `maps/mp/mp_nuked.gsc` (asset 584, 7337 bytes:
the stock script plus a spawn fix, `move_spawn_point("mp_dom_spawn", ...)` and two
`spawncollision` walls at Nuketown positions) and `clientscripts/mp/mp_nuked_fx.csc` (652).
The fix only works if the patch's copy is the one the game runs, so with the update installed
the minimal script is INFERRED not to run. The converter therefore also adds the entities the
stock script needs, from the stock entity string, placed for the box: the two clock hands
(`mp_nuked_doomsday_clock_min_hand`, `_sec_hand`) and two counters
(`mp_nuked_townsign_counter`) as script_models 256 units above the box's roof, the bomb as a
`tag_origin` script_model 3956 units above the roof (it drops 3700 at match end and stops above
it; the stock one is brush model `*40`, which the box does not have), and the two end-game
camera structs inside the box. With either script the level starts without a script error
(INFERRED until run). The patch's two collision walls stand at (769, 329), outside the box.

Gametypes (from common_mp / patch_mp `maps/mp/gametypes/*.gsc`; `convert.json` "gametypes"):

| Gametype | Needs | Box |
|---|---|---|
| tdm | mp_tdm_spawn_allies_start, mp_tdm_spawn_axis_start, mp_tdm_spawn | ready (3, 3, 6) |
| dm, oic, gun, shrp, hlnd | mp_dm_spawn (wager modes also take optional mp_wager_spawn) | ready (6) |
| koth | mp_tdm_spawn and `hq_hardpoint` radios | no radios (map errors, AbortLevel) |
| dom | mp_dom_spawn_*_start, mp_dom_spawn, `flag_primary` flags | missing (AbortLevel) |
| sd | mp_sd_spawn_attacker / _defender, `sd_bomb_pickup_trig`, `sd_bomb`, `bombzone` | missing |
| dem | mp_dem_spawn_* (start, attacker, defender), bombzones | missing |
| ctf | mp_ctf_spawn_* (start and team), `ctf_flag_pickup_trig`, `ctf_flag_zone_trig` | missing |
| sab | mp_sab_spawn_*, `sab_bomb_pickup_trig`, `sab_bomb`, `sab_bomb_allies` / `_axis` | missing |

The converter refuses a map lacking tdm or dm spawns (the gametypes the demo uses); the rest
need objective entities (triggers, script_brushmodels) that a later map would carry.

Glass. The glasses asset holds the panes themselves (62 in mp_nuked, at Nuketown positions);
the converter sets its count to 0 and its pointer to 0 (written through the node rewrite). The
62 `glass` entities of the stock entity string are gone with it.

Dynamic entities. The box's clipMap holds 256 empty DynEntityDefs (PC linker output) and the
GfxWorld 256 dynamic-entity client slots. Stock PS3 mp_nuked has 505 = 249 real + 256 with no
model, so the empty slots are the platform's own convention: harmless.

Path nodes. The box has none (game_map_mp nodeCount 0). Dogs and Combat Training bots navigate
by path nodes (INFERRED: they stand still or are not spawned).

## 4. Proof of the world rules: PC mp_nuked against PS3 mp_nuked

`tools/convert_map.py compare <Steam>/zone/Common/mp_nuked.ff <disc>/mp_nuked.ff` walks the PC
zone from its com_map (0x183bae2, assets 355..395; the PC XModel / FX / sound handlers are not
written, so the walk starts there, box-map.md 2.3) and converts with the converter's own code
(`compare.compare_world`; also `tests/test_convert_box.py`, 3 s):

| Array | Result |
|---|---|
| ComWorld, GameWorldMp, clipMap_t, MapEnts (32 arrays) | 0 differing non-pointer words in 31; differing words are pointer fields only (primary light def names 21, node tree children 226, brush sides 22 691, nodes 2780, partitions 1829, brushes 11 021, leaf-brush lists 6027, dynamic entity asset pointers 566) |
| clipMap staticModelList | 7106 non-pointer words: the absmin / absmax floats in their low bits (box-map.md 3.1, INFERRED recomputed from PS3 model bounds); the box has no static models |
| GfxWorld header | 6 words differ: name pointer, streamInfo aabbTreeCount (0x217 vs 0x270) and leafRefCount (0x1cfe vs 0x1de4), sky sampler 0xeb vs 0xea, vertexLayerDataSize (PC extras only vs PS3 full layer), sun flare alias |
| vertices | 176 344 = 176 344, positions identical byte for byte after regrouping |
| indices | 351 543 = 351 543, identical |
| surfaces (3494) | every field identical except: mins / maxs in the 51 surfaces above, himipRadiusSq in 514 (230 with PC 0; 284 where PS3 = PC / 16, INFERRED from PS3 texture sizes) |
| layer data (5 028 276 bytes, 243 groups, strides 28 x 165, 36 x 28, 40 x 25, 44 x 8, 48 x 10, 52 x 6, 56 x 1) | colour, uv, lightmap uv identical in all 176 344 vertices; normal / tangent words differ in 110 451 / 115 927 vertices, largest angle 0.44 degrees (PC normals are 8-bit); extras identical in 76 of 78 groups: the two others are stride-52 groups whose 24 bytes are two uv pairs and two byte words, not three uv pairs, which the size cannot tell (the converter refuses that case by default) |
| planes | 114 of 10 101 differ in byte +0x11 (0 on PC, 8 on PS3; meaning INFERRED) |
| nodes, brush models, sorted surface index, shadow geometry, light grid rowDataStart, rawRowData, entries | identical |
| light grid colours | 42 words of 1 344 042 at the end differ |
| streaming aabb trees, leaf refs, cell aabb tree counts (3 cells) | rebuilt per platform (535 vs 624 trees); the converter keeps the PC ones |
| static model instances | float differences; not converted |

## 5. Validation of the box (offline)

| Check | Result |
|---|---|
| PC box zone (`mp_opent5box.ff`, sha1 20e4cffb...) | parses exactly with the product parser and the `PC` platform; written back identically; `Rewrite(platform=PC).build()` identical |
| Converted content | reparses exactly; `write(parse(content))` identical (the rewrite_all check); 53 166 offset / alias pointers, 0 unresolved |
| Game loader (t5mp.elf's XFile loader in the local PowerPC interpreter of .oracle/r2b, `tools/convert_map.py oracle`, 17 s) | consumed 0x30bad10 of 0x30bad10 bytes; final block positions RUNTIME 0x3d0e80, PHYSICAL_RUNTIME 0x115d900, VIRTUAL 0x1295d71, PHYSICAL 0xd0e0c8 = the header (TEMP rewound to 0); the loader converted 53 166 pointers, the same fields with the same values as the product parser reads, none outside its block. Control, stock mp_nuked: 109 495 = 109 495, all equal, 24 s |
| `opent5 verify --against mp_nuked` | ok; container 1043 chunks, 4 terminators; parse exact; rewrites identically; 9 assets changed as listed in 1.1 |
| GUI self-test (`--selftest`, with code_post_gfx_mp for the localize view) | ok, 99 checks (52 on the converted zone), 0 failed |
| Geometry views | world: 24 vertices, 12 triangles, bounds (-512, -512, 0)..(512, 512, 256), normals inward (walls -X, -Y, +Y, +X, floor +Z, ceiling -Z); collision: 6 brushes, bounds (-528, -528, -16)..(528, 528, 272). Screenshots `out/demo/e_box/screenshots/` |
| Determinism | two runs give the same sha1 |

Not checked offline: everything after the loader (asset registration, script compilation and
run, rendering). That is the device test, docs/demo-box.md.

## 6. The byte-order change and the 178-zone checks

Benchmark (best of 3, same machine): parse with log / write / Rewrite build of common_mp
1.101 / 0.602 / 8.37 s before, 1.088 / 0.636 / 7.77 s after; mp_nuked 0.429 / 0.288 / 3.20 s
before, 0.349 / 0.274 / 3.18 s after. Round trips byte-identical in all. After the change,
over every zone in .env: `tools/walk_all.py` 178 of 178 zones parse exactly (67 s, 4 jobs);
`tools/rewrite_all.py` 178 zones, 125 327 of 125 327 assets written back identically, content,
event log and `.ff` identical in all 178 (151 s).

## 7. Limits and next steps

- Materials and techsets must exist in the base zone's world surfaces; new textures need
  images converted to PS3 GfxImage and written into the `.pak` (textures.md, pak.md).
- Lightmaps, reflection probes and the light grid colours are not converted (3.5).
- Static models (GfxStaticModelDrawInst and XModel conversion), layered materials with
  ambiguous extras, path nodes, PS3-rebuilt streaming trees: not done.
- One map at a time under a stock name; a new map name needs the menu tables and
  configstrings.
- The PC tools need the LinkerMod `cod2map.dll` / `linker_pc.dll` (box-map.md 1.2).

## Sources

docs/research/box-map.md, pc-route.md, structs-map.md (with its errata), walk-all.md,
remap.md section 8; OpenAssetTools src/Common/Game/T5/T5_Assets.h (PC structs, through
`xfile/layouts_pc.py`) and src/ZoneCode/Game/T5/XAssets/*.txt (PC load rules); the stock
scripts read from common_mp.ff and patch_mp.ff; the zones and files named above.
