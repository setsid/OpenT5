# Converting a PC Mod Tools map to a PS3 map zone

`opent5 convert` turns a map built with the PC Mod Tools (cod2map, cod2rad, linker_pc; how
the box map was built is in docs/research/box-map.md section 1) into a PS3 map zone that a
signature-patched PS3 client can load. First target: a sealed box (`mp_opent5box`), shipped
inside `mp_nuked.ff`. The test procedures are docs/demo-box.md and, with the box's own
lighting and compass, docs/demo-box-lit.md.

    opent5 convert PC_MAP.ff --base mp_nuked -o OUTDIR [--lighting baked|flat|sunlit|keep] [--json]
                   [--name mp_NAME [--copy-pak] [--register [--patch-mp FILE] [--title T]
                   [--description D] [--ui-slot warmuseum|snowmine|salvage|firebase]]]

`PC_MAP.ff` is the PC zone (`zone/English/<map>.ff` of the PC tools, `IWffu100`). `--base` is
a PS3 map zone (a bare name is looked up in the .env folders) whose world is replaced; the
output is `OUTDIR/<base>.ff` plus `OUTDIR/convert.json` (every decision below, with numbers).
Nothing is written into the game folders or over a source. The run takes about 10 s.

Supporting tool (not product code): `tools/convert_map.py convert | compare | oracle`
(section 4 and 5).

Conventions: "PC zone" is the inflated PC stream, "PS3 zone" the inflated PS3 XFile stream;
offsets are into those streams. Type numbers are PS3 numbers.


## Rule: a custom map lives in its own files

Everything a converted map needs lives in its own files: its zone (`mp_<name>.ff`), its own
`.pak` if it streams images, and images such as the compass stored inside its own zone in the
end-of-zone image block (docs/research/textures.md, deferred pixels). Stock zones and stock
paks (`patch_mp.ff`, `images_low.pak`, `common.pak` and the rest) are never modified: two
custom maps would otherwise collide, and stock files must stay stock.

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
| `convert/entities.py` | What each MP gametype needs from the entities (counts, links, gameobject filter, triggers, brush models), checked per gametype (section 9) |
| `convert/images.py` | PC GfxImage to PS3 GfxImage (header and pixels), new PS3 images; pixels stored in the zone (section 10.1) |
| `convert/lightmaps.py` | `--lighting baked`: the PC map's cod2rad lightmaps, reflection probes and outdoor image converted (section 10.2) |
| `convert/compass.py` | Minimap corners, the top-down compass image and the map's own compass material (section 10.4) |
| `convert/materials.py` | PC materials the base lacks: new PS3 materials (techset remapped by name, state bits from a stock material), their images reused, placeholders or converted into the zone; `.iwi` reader (section 11) |
| `convert/lighting.py` | `flat` / `sunlit`: lightmap coordinates from the base map's own lightmap (section 3.5) |
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
0x43c), every field swapped (box-map.md 3.4). Kept from the stock header, pointer words to
stock data and the counts that size them only (`world.STOCK_HEADER_RANGES`): 0x24..0x37
(PS3-only, zero in mp_nuked), 0x40..0x4b (sky image, sky sampler state, sky box model),
0x100 (sun light pointer; the record is the PC one, 10.2), 0x18c..0x207 (draw images), 0x22c
(PS3-only, zero), 0x28c..0x293 (material memory), 0x298..0x29f (sun sprite and flare
materials); with `flat`, `sunlit` and `keep` also 0x16c..0x18b (reflection probes,
lightmaps), and 0x334 (outdoor image) unless the PC outdoor image is converted. Everything
else is the PC map's, including the sun flare data (+0x294, +0x2a0..) and the
outdoorLookupMatrix (+0x2f4..+0x333): the earlier rule kept 0x28c..0x337 whole and gave the
box Nuketown's matrix (stock `37c4c4cd` at +0x2f4, the box `3a800000`; box-lighting.md 5).
Set by the converter: 0x208 vertex count, 0x20c / 0x218 / 0x228 data pointers, 0x210 0,
0x214 layer data size, 0x224 index count.

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
with stride-28 groups). A PC material the base does not use is built as a new PS3 material
in the map's own zone, with its images (section 11, `materials.py`); until that call is
wired into `mapzone.py` such a material stops the conversion with its name.

### 3.5 Lighting (decision and evidence)

This section describes `flat`, `sunlit` and `keep`, which keep the base's lighting; the
default is now `baked`, the PC map's own lightmaps converted (section 10.2). In these modes
the PC lightmap is not converted; the base keeps its own lightmap
(mp_nuked: one, `*lightmap0_primary` DXT1 2048 x 2048, `*lightmap0_secondary` R5G6B5 1024 x
2048, `*lightmap0_secondaryb` Y16_X16 1024 x 1024, pixels deferred in the zone). The PC
lightmap coordinates would sample it at arbitrary places, so `flat` gives every
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

Finding: the installed update's `patch_mp.ff` (sha1 499ce654..., identical to the installed
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

Gametypes: what each MP gametype needs from the entity string, the check
(`opent5.convert.entities`) and the box that carries every mode: section 9.

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
| Game loader (t5mp.elf's XFile loader in the local PowerPC interpreter in tools/loader_emu, `tools/convert_map.py oracle`, 17 s) | consumed 0x30bad10 of 0x30bad10 bytes; final block positions RUNTIME 0x3d0e80, PHYSICAL_RUNTIME 0x115d900, VIRTUAL 0x1295d71, PHYSICAL 0xd0e0c8 = the header (TEMP rewound to 0); the loader converted 53 166 pointers, the same fields with the same values as the product parser reads, none outside its block. Control, stock mp_nuked: 109 495 = 109 495, all equal, 24 s |
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

- Techsets must exist in the base zone (same name, a placeholder of that name, or the
  closest one with the same samplers); a material whose techset has no match stops the
  conversion. Materials and images of the map's own: section 11.
- Lightmaps, probes, outdoor image, sun and light grid are converted (section 10); probe
  cube faces come only from the game (the PC tools write a black 4 x 4 dummy); a PC map with
  more than one lightmap is converted but untested.
- Static models (GfxStaticModelDrawInst and XModel conversion), layered materials with
  ambiguous extras, path nodes, PS3-rebuilt streaming trees: not done.
- Own map names: section 8. Offering a named map in the menus needs a stock zone change.
- The PC tools need the LinkerMod `cod2map.dll` / `linker_pc.dll` (box-map.md 1.2).

## 8. Map names (`--name`)

`--name mp_opent5box` writes `OUTDIR/mp_opent5box.ff`, a map of its own: the zone header
name, the world (`maps/mp/mp_opent5box.d3dbsp` for com_map, gfx_map, game_map_mp, col_map_mp
and map_ents; gfx base name `mp_opent5box`, which also names the last rawfile), the 11 map
scripts, the sun and exposure files and the 12 configstring tables are renamed, and the script
paths inside the scripts follow (`maps\mp\mp_opent5box_fx::main()`). Every other `mp_nuked`
in the zone names an asset that keeps its name. Why each one, with ELF addresses:
docs/research/map-registration.md 3. `mapname.py` holds the rules and the renaming;
`register.py` the map table row.

Name rules (`mapname.validate`): `mp_` then lower case letters, digits and `_`; at most 23
characters (the UI map table's 24-byte field); not ending in `_load` or `_patch`; not a stock
or cut map name. Without `--name` the output is unchanged (box into mp_nuked: sha1
`d5a37f69...`, as in docs/demo-box.md).

With an own name the update's Nuketown script no longer applies, so the compatibility
entities of 3.6 are not added (`--name`: `compat` off by default).

`--copy-pak` writes `<name>.pak`, a byte copy of the base's pak: the game opens
`<zone>.pak` beside `<zone>.ff` (docs/research/pak.md 5). Images of the map's own (a future
compass image) belong inside the map's zone, never in a shared pak.

The game loads an unknown map name from the disc maps' folder without a missing-map error,
but the menus list only the rows of `mp/mapstable.csv` in patch_mp.ff, and no route that
avoids a stock change was found (map-registration.md 4). `--register` (opt in) writes an
edited copy of the update's patch_mp.ff with the map's row; the default writes only the
map's own files.

Box as `mp_opent5box` (`out/demo/f_box_named/`, docs/demo-box-named.md):

| Check | Result |
|---|---|
| Content | reparses exactly, written back identically, 53 166 pointers, 0 unresolved |
| `opent5 verify --against mp_nuked` | ok; 32 assets changed: the 9 of 1.1, 12 configstring tables, 10 more renamed rawfiles, rawfile 528 |
| Emulated loader | consumed exactly; 53 166 conversions, same fields and values as the product parser; blocks end at the header sizes |
| GUI self-test (with code_post_gfx_mp) | 99 checks, 0 failed |
| Determinism | two runs, same sha1 (`56cf51e4...`) |

## 9. Gametypes and objective entities

The MP gametypes are the twelve of `maps/mp/gametypes/_gametypes.txt` (code_post_gfx_mp,
rawfile 440: dm, dom, sd, sab, tdm, koth, ctf, dem, oic, hlnd, gun, shrp), the same twelve
as the `configstrings_ps3_mp_nuked_<gametype>.csv` tables of mp_nuked (assets 0..11). `koth`
is Headquarters (`MPUI_HEADQUARTERS`); there is no separate `hq` gametype. `twar` and `sur`
scripts exist in common_mp but are not in the list.

The scripts the game runs: the update's `patch_mp.ff` carries its own
`maps/mp/gametypes/{ctf,dem,dm,dom,gun,hlnd,koth,sab,sd,tdm}.gsc` and `_gameobjects.gsc`;
`oic.gsc`, `shrp.gsc`, `_spawnlogic.gsc` and `_callbacksetup.gsc` come from `common_mp.ff`.
Line numbers are into those rawfiles as `opent5 extract ZONE OUT --type rawfile` writes them.

### 9.1 Rules every gametype shares

| Rule | Evidence | Without it |
|---|---|---|
| Entities whose `script_gameobjectname` (space-separated tokens, `[all_modes]` keeps all) names none of the gametype's `allowed` list are deleted; untagged ones stay | patch_mp `_gameobjects.gsc` main 4-33, entity_is_allowed 34-57 | an untagged objective stays in every mode (inert, but visible) |
| `allowed`: sd `sd bombzone blocker`; dem `sd bombzone blocker dem` (`bombzone_dem` replaces `bombzone` when the map has one); koth `hq` (`koth` too only on mp_kowloon); the others their own name | sd.gsc 189-191, dem.gsc 213-221, koth.gsc 150-153, tdm 61, dm 55, dom 114, ctf 219, sab 168 | |
| placeSpawnPoints(class) needs one spawn of the class | common_mp `_spawnlogic.gsc` placeSpawnPoints 90-100 | AbortLevel |
| addSpawnPoints(team, class) needs the team to end with one spawn | `_spawnlogic.gsc` addSpawnPointsInternal 37-68 | AbortLevel |
| one `mp_global_intermission` | `_spawnlogic.gsc` getRandomIntermissionPoint 892-898 (assert) | assert / no intermission point |
| a name read with getEnt is on exactly one entity | t5mp.elf string "getent used with more than one entity" at file offset 0x8d7d20 | script error |
| a use object is hold-to-use when the trigger's classname contains `use` (`trigger_use`, `trigger_use_touch`, `trigger_radius_use`; all three in t5mp.elf's strings), walk-in otherwise | `_gameobjects.gsc` createCarryObject 141-148, createUseObject 673-681 | a bomb site planted by walking in |

AbortLevel (common_mp `_callbacksetup.gsc` 125-139) sets `g_gametype` to `dm` and exits the
level. `maps\mp\_utility::error` (common_mp `maps/mp/_utility.gsc` 21-25) only prints, so a
gametype that calls it and returns runs on without its objective.

### 9.2 Per gametype

| Gametype | Spawns (classname) | Objective entities (targetname, classname, links) | Evidence (patch_mp unless noted) | Without them |
|---|---|---|---|---|
| tdm | `mp_tdm_spawn_allies_start`, `_axis_start` (place), `mp_tdm_spawn` (add) | none | tdm.gsc onStartGameType 50-53 | AbortLevel |
| dm | `mp_dm_spawn` (add) | none | dm.gsc onStartGameType 47-48 | AbortLevel |
| oic, gun, shrp, hlnd | `mp_wager_spawn` when the map has any, else `mp_dm_spawn` | none | common_mp oic.gsc 80-89, shrp.gsc 70-79; gun.gsc 195-204, hlnd.gsc 124-133 | AbortLevel |
| sd | `mp_sd_spawn_attacker`, `mp_sd_spawn_defender` (place) | `sd_bomb_pickup_trig` (one trigger) and `sd_bomb` (one script_model); `bombzone` use triggers, each `target` naming its visual(s), the first visual's `target` naming one defuse trigger; `script_label` `_a` / `_b` (the only labelled icons precached, sd.gsc 50-75) | sd.gsc onStartGameType 183-184; bombs 435-444 (bomb), 466-502 (sites; `visuals[0]` used unchecked at 490, the defuse trigger asserted at 503) | spawns: AbortLevel; bomb: error() and no bomb or sites; no `bombzone`: no sites; a site without visual or defuse trigger: script error |
| dem | `mp_dem_spawn_defender_start`, `mp_dem_spawn_attacker_start` (place), `mp_dem_spawn_attacker`, `mp_dem_spawn_defender` (add); optional `_attacker_a/_b`, `_defender_a/_b` | `bombzone_dem`, else `bombzone`, as for sd; `sd_bomb` is deleted when present | dem.gsc onStartGameType 201-221; bombs 512-551 | as sd |
| dom | `mp_dom_spawn_allies_start`, `_axis_start` (place); `mp_dom_spawn` (add on every flag change); optional `mp_dom_spawn_flag<label>` | at least two `flag_primary` / `flag_secondary` triggers (radius triggers in stock maps; an optional `target` names one visual, else a model is spawned); `script_label` `_a`..`_e`; `flag_descriptor` entities with `script_linkname`, one nearest each flag, whose `script_linkto` names other flags' descriptors | dom.gsc domFlags 248-253 (count), 265-274 (visual), flagSetup 745-828, change_dom_spawns 923-949 | fewer than two flags: AbortLevel; descriptor errors: AbortLevel |
| ctf | `mp_ctf_spawn_allies_start`, `_axis_start` (place), `mp_ctf_spawn_allies`, `_axis` (add) | exactly two `ctf_flag_pickup_trig` and two `ctf_flag_zone_trig` triggers, `script_team` `allies` and `axis`; the pickup trigger's optional `target` names the flag model | ctf.gsc createFlag 306-317, createFlagZone 350-353, ctf 426-443 | error() and no flags |
| sab | `mp_sab_spawn_allies_start`, `_axis_start` (place), `mp_sab_spawn_allies`, `_axis` (add) | one each: `sab_bomb_pickup_trig` (trigger), `sab_bomb` (script_model), `sab_bomb_allies` and `sab_bomb_axis` (use triggers, `target` naming the visual) | sab.gsc sabotage 312-357, createBombZone 362-370 | error() and no bomb / targets; no visual: script error |
| koth (Headquarters) | `mp_tdm_spawn` (add, and checked) | at least two `hq_hardpoint` script_models with `target` (the crate), each touching exactly one `radiotrigger` trigger | koth.gsc onStartGameType 135-147, SetupRadios 523-579 | no `mp_tdm_spawn`: AbortLevel; radio errors: AbortLevel |

### 9.3 Brush triggers in a converted map

A brush trigger names its brush model in the entity string (`"model" "*N"`); cod2map writes
the brush entities as clipMap submodels 1..n in entity order, sets the entity's `origin` to
the brushes' centre and stores the bounds relative to it, widened by 1 (the box with
objectives: 14 triggers, cmodels 1..14, a 96-unit bomb site trigger `-49 .. 49`; the
GfxWorld holds 15 brush models). The converter carries them without special handling:
`col_map_mp` cmodels (`cmodel_t`, 0x48 bytes, with its `cLeaf_t`) and the GfxWorld's brush
models are swapped field by field, which section 4 proves on mp_nuked (cmodels and brush
models identical between PC and PS3). In the converted box the clipMap count (header +0x8c)
is 15, the GfxWorld brush model count (+0x268) 15, and the bounds read back are the PC ones
(the radios fall inside their radiotriggers by them). Radius triggers (`trigger_radius`,
`radius` and `height` keys) need no brush model.

The PC linker adds every model a script_model names to the PC zone (assetlist: 5 xmodels,
their materials, techsets and images); the PC parse walks them exactly and the converter
ignores them, since the base zone already holds them (mp_nuked 362 `prop_suitcase_bomb`,
364 `p_glo_bomb_stack`, 365 `mp_supplydrop_hq`; common_mp 5917 `mp_flag_neutral`, 5944
`t5_weapon_briefcase_bomb_world`).

### 9.4 The check (`entities.py`)

`entities.report(entities, gametypes, cmodels, xmodels, mapname)` applies 9.1 and 9.2 to the
entities that survive each gametype's filter and returns, per gametype, `ready`, `have`
(counts), `missing` (each with its script, function and lines) and `warnings` (a
script_model whose model is neither in the zone nor one of the known common_mp models; a
radio inside the bounds of two radiotriggers, which bounds cannot decide). `zone_report`
takes the converted clipMap node and the zone being written. Control: stock PS3 mp_nuked
gives all twelve ready (koth with that bounds warning for one radio).

### 9.5 The box with every mode (`tools/testmap.py`, docs/demo-box-modes.md)

`tools/testmap.py write OUT.map` writes the room of box-map.md 1.1 (parameterised by
`--half` and `--height`) with spawns for every class above and the objectives below;
`tools/testmap.py build NAME --game G --work W -o OUT.ff` compiles it with the PC tools
(box-map.md 1.2 command lines; about 20 s). Allies and attackers start on the -x side,
axis and defenders on the +x side. Objectives overlap only across modes that never run
together, because each is tagged with its mode:

| Mode (tag) | Entities (x, y) |
|---|---|
| sd, dem (`bombzone`) | sites A (288, -320) and B (288, 320): `trigger_use_touch` 96 x 96 x 96, `p_glo_bomb_stack`, defuse `trigger_use_touch` |
| sd (`sd`) | bomb (-288, 0): `trigger_multiple` 48 x 48 x 48, `prop_suitcase_bomb` |
| sab (`sab`) | bomb (0, 0); `sab_bomb_allies` (-288, 0), `sab_bomb_axis` (288, 0) with `p_glo_bomb_stack` |
| ctf (`ctf`) | allies flag (-448, 0), axis flag (448, 0): pickup trigger 64 x 64 x 80, zone trigger 96 x 96 x 80, `mp_flag_neutral` |
| dom (`dom`) | flags A (-288, 0), B (0, 0), C (288, 0): `trigger_radius` 96 x 128; descriptors 64 above, linked A-B-C |
| koth (`hq`) | radios (0, -320) and (0, 320): `radiotrigger` 160 x 160 x 128, `t5_weapon_briefcase_bomb_world`, crate `mp_supplydrop_hq` |

Spawns: four start points per side and class at x = -384 / 384, four team respawns per side
and class at x = -448 / 448, six neutral points (`mp_tdm_spawn`, `mp_dm_spawn`,
`mp_dom_spawn`) clear of every objective, one `mp_dom_spawn_flag_<a|b|c>` per flag.

Results (PC zone `mp_opent5box_modes.ff`, sha1 `d8d34f51...`, 868 448 bytes; converted into
mp_nuked with the default lighting, `out/demo/i_box_modes/`):

| Check | Result |
|---|---|
| PC zone | parses exactly (22 assets); entity string equal to `testmap.compiled_entities()` apart from cod2rad's `gndLt` key on two script_models; cmodel bounds equal |
| Converted content | reparses exactly, written back identically, 53 195 offset / alias pointers, 0 unresolved |
| Emulated loader (`tools/convert_map.py oracle`) | consumed exactly, blocks end at the header sizes, 53 195 conversions, same fields and values as the product parser, none outside its block |
| `opent5 verify --against mp_nuked` | ok (container, exact parse, identical rewrite) |
| `entities.report` on the converted zone | all twelve gametypes ready, no warnings |
| Determinism | two conversions, same sha1 |

The hook for the converter report (one line in `mapzone.convert_map`, after
`entities = sc.parse_entities(text)`):
`report["objectives"] = ent.zone_report(entities, pclip, bx, own or base_name)` with
`from opent5.convert import entities as ent`.

## 10. The map's own lighting and compass

### 10.1 Images (`images.py`)

`convert_image(pc_node)` returns a new PS3 GfxImage node: the 0x70 header built from the PC
header and loadDef, pixels transformed per format (DXT copied; R5G6B5 each u16 swapped and
Morton swizzled; G16R16 to Y16_X16, each u16 swapped in place and swizzled; L8 to B8
swizzled; cube faces padded to 128) and stored deferred (delayLoadPixels 1), which the writer
puts in the zone's own end-of-zone block (PHYSICAL_RUNTIME). No `.pak` is read or written.
Proof (`tests/test_convert_images.py`): converting PC mp_nuked's six cod2rad images
(`*lightmap0_primary`, `_secondary`, `_secondaryb`, `*reflection_probe0`, `1`, `$outdoor`)
gives the PS3 mp_nuked headers and pixels byte for byte. The image hash (+0x6c) of a new image
is `h = 33 h ^ (c | 0x20)` over the name (matches PS3 mp_nuked `faction_128_specops`
0x8ba3e54a, `faction_128_spetsnaz` 0x0102d637, `*lightmap0_primary` 0xce2f698b).

### 10.2 `--lighting baked` (the default)

The PC GfxWorld's lightmaps (one GfxLightmapArray per lightmap, three images each), reflection
probes (origin and volumes swapped, cube image) and `$outdoor` image become new nodes under
the converted GfxWorld (`lightmaps.py`); the header's probe and lightmap counts are the PC
ones. Surfaces keep their PC lightmap uv and their PC lightmap and probe indices (PC and PS3
mp_nuked agree on both, section 4). New nodes hold only inline pointers; the splice
attributes them to the PC parse (`Splice.origin`), and only the stock keys still kept are
renamed onto the converted node, so a base pointer to a dropped stock lightmap or probe would
stop the build with its offset (none in mp_nuked). The sun GfxLight is the PC one, every
32-bit word swapped except +0, +0x160 (lightdef pointer) the base's (PC mp_nuked so converted
equals PS3 mp_nuked). The light grid's last colour, the cod2rad default `15009d` x 56, becomes
`40059d` x 56 as in the PS3 build of every MP map examined (box-lighting.md 3; its meaning,
the colour outside the grid, is INFERRED).

Box (`mp_opent5box_grid`, 10.3) into mp_nuked: `*lightmap0_primary` DXT1 1024 x 1024
(524 288 bytes), `_secondary` R5G6B5 512 x 1024 (1 048 576), `_secondaryb` Y16_X16 512 x 512
(1 048 576), `*reflection_probe0` DXT1 cube 4 x 4 (768), `$outdoor` B8 512 x 512 (262 144);
PHYSICAL_RUNTIME 0x115d900 -> 0x9dd700. Decoded from the converted zone, the floor chart of
the secondaryB peaks under the light (channel 0 up to 2053) and is the brightest surface.

`flat`, `sunlit` and `keep` work as before (3.5) on the base's lightmaps and probes.

### 10.3 A real light grid from the PC tools

cod2rad builds the light grid only at sample points read from `<map>.grid` or `.grid_auto`
(cod2rad.exe strings "Light grid sample point file '%s' not found.", loader 0x434010, 6-byte
records checked at 0x4340a7). cod2map writes `.grid_auto` from brushes with the
`lightgrid_volume` material (cod2map.exe strings `lightgrid_volume`, `lightgrid_sky`,
`.grid_auto`; the material is in the tools' `raw/materials`). The box rebuilt with one such
brush filling the room (8 units inside each wall), headless with the same three commands as
box-map.md 1.2: cod2map wrote `raw/maps/mp/mp_opent5box_grid.grid_auto`, 17 298 bytes = 2883
points; cod2rad then wrote a grid of 2883 entries and 2164 colours (2163 used, indices 0 ..
2162, plus the default), all converted (`tests/test_convert_box.py`). Without such a brush
the grid holds only the default colour.

### 10.4 Compass (`compass.py`)

- How the game picks it: the level script calls `maps\mp\_compass::setupMiniMap("<material>")`;
  `_compass.gsc` (common_mp asset 1249) needs exactly two `minimap_corner` entities, orders
  them by `getnorthyaw()` and calls `setMiniMap(material, nw.x, nw.y, se.x, se.y)`. The
  material is chosen by that string only: no map table column (column 7 is the combat record
  overlay, map-registration.md 1) and no worldspawn key.
- Corners: two `script_origin` `minimap_corner` entities at a square around the world bounds
  plus 64 units (box: (576, 576) and (-576, -576)), unless the PC map has its own two; one or
  more than two are refused.
- Image: a top-down render of the converted world (row 0 = north edge x = nw.x, column 0 =
  west edge y = nw.y, no `northyaw` key), upward faces filled grey by height, ceilings left
  out, walls drawn as light outlines, alpha 0 elsewhere; DXT23 512 x 512, one level, as the
  stock compass images; stored inside the map's zone (deferred).
- Material: `compass_map_<map>`, cloned by name from code_post_gfx_mp's
  `compass_map_<base>` (header, texture def, state bits followed to their owner), with its
  name, image and state bits inline and the technique set pointing at the zone's own `,2d`
  reference (mp_nuked asset 404, through asset 444 `faction_128_specops`). It is appended as
  the last asset of the zone (asset list count + 1; emulated loader consumes it exactly).
- Named map (`--name`): `compass_map_mp_<name>` and the map's own script calls it
  (`setupMiniMap("compass_map_mp_opent5box")`). Everything is in the map's zone.
- Under the base's name (mp_nuked replacement): the update's patch_mp carries its own
  `maps/mp/mp_nuked.gsc`, which calls `setupMiniMap("compass_map_mp_nuked")`, and patch_mp's
  assets win over the map zone's (below), so the material keeps the stock name
  `compass_map_mp_nuked`, in the converted zone. Asset precedence, from t5mp.elf: when a zone
  loads an asset whose name is already loaded, DB_LinkXAssetEntry ranks the two zones by their
  load flag and the new one replaces the old when its rank is not lower (0x25f4b8 `cmpw`,
  `bge` 0x25f914, which swaps the entries and chains the old one, 0x25f924..0x25f958; the
  warning "Attempting to override asset '%s' from zone '%s' with zone '%s'" is printed at
  0x25f3dc). Ranks (switch 0x25f404..0x25fe7c): flag 0x4 -> 1, 0x40 -> 4, 0x8000 -> 10, 0x1 ->
  20. Startup zone table 0xb34cac: patch_mp 0x1, code_post_gfx_mp 0x4, common_mp 0x40; the
  level zone is loaded with 0x8000 (0x3a4e18). So the converted zone's `compass_map_mp_nuked`
  (rank 10) replaces code_post_gfx_mp's (rank 1) while the map is loaded, without changing
  code_post_gfx_mp or images_low.pak. This also confirms the earlier INFERRED point of 3.6:
  patch_mp's Nuketown script (rank 20) runs, not the converted zone's. INFERRED until a device
  run: that `setMiniMap` then draws the zone's material (a deferred branch at 0x25f7d0 was not
  decoded).

### 10.5 What stays in which file

Lightmaps, probe, outdoor image, compass material and compass image: inside the map's zone
(`<map>.ff`, end-of-zone deferred block). `--copy-pak` still copies the base's `.pak` for the
base's textures. No stock zone or pak is changed by any of this.

## 11. Materials and textures of the map's own (`materials.py`)

A converted map may use materials and textures that the base PS3 zone does not have. They
are stored in the map's own zone: the material inline in the GfxWorld's material memory, its
images inline after it with their pixels in the zone's end-of-zone (deferred,
PHYSICAL_RUNTIME) block. No pak is written for them and no stock file changes. Test map and
numbers: docs/demo-box-textures.md.

### 11.1 Where the PC zone keeps them

The PC linker puts every material a surface uses inline in the GfxWorld's material memory
(PC GfxWorld +0x278), each texture's GfxImage inline after it (0x34 bytes, loadDef in TEMP).
An image the game reads from a file (category 3: colour, normal, specular maps) carries no
pixels in the zone: its loadDef has resourceSize 0 (box zone, `~-gblockout_wood_test_c`:
loadDef `00000000 44585431 00000000`, 'DXT1', size 0); the pixels are `images/<name>.iwi`
(IWI version 13) in `main/*.iwd`, or in `raw/images/` of the game folder, where the linker
read them. Generated images (category 1, e.g. `$identitynormalmap`, loadDef `01020000
15000000 04000000`, a 1 x 1 A8R8G8B8) carry their pixels in the zone. IWI 13: 48-byte header
(format, flags, width, height, depth, gamma, eight file sizes), then the levels smallest first
(OpenAssetTools src/ObjImage/Image/IwiLoader.cpp `LoadIwi13`); formats 0x0b/0x0c/0x0d DXT1/3/5,
0x01 RGBA, 0x04 luminance. `~-gblockout_wood_test_c.iwi`: `4957690d 0b10 0002 0002 0100
00000040 e8aa0200 ...` (DXT1, streaming flag, 512 x 512, 174 824 bytes = 48 + the ten levels).

### 11.2 The PS3 material

Rules from PC mp_nuked against PS3 mp_nuked (182 world materials, all inline in both):

| PS3 field | From | Evidence |
|---|---|---|
| name, gameFlags, sortKey and atlas bytes, surfaceTypeBits, layeredSurfaceTypes | PC header, integers swapped | equal in 182 of 182 |
| drawSurf (+0x10, 8 bytes) | PC bytes copied unswapped | equal in 181 (one materialSortedIndex 0xad vs 0xb1) |
| textureCount, constantCount, stateFlags, cameraRegion, maxStreamedMips (+0x67, +0x68, +0x6a..+0x6c) | PC +0xaa, +0xab, +0xad..+0xaf | equal in 182 |
| stateBitsEntry[71], stateBitsCount, state bits table | a stock PS3 material of the base with the same techset | PC has 130 technique slots, PS3 71; only 20 of 182 tables agree after a swap; the stock-template choice reproduces the PS3 values in 156 of the 165 materials that share their techset with another |
| techset (+0x70) | the template's alias to the PS3 techset | section 11.3 |
| MaterialTextureDef (16) | PC, nameHash swapped | equal in every single-layer material; secondary layers of layered materials have samplerState 0x12 on PS3 where PC has 0x13 (kept from PC, INFERRED harmless) |
| MaterialConstantDef (32) | PC, hash and four floats swapped | equal in 182 |
| MaterialMemory.memory | 44 x vertices + 6 x triangles + 160 x surfaces drawn with it | exact for all 182 (PC: 128 per surface) |

### 11.3 Techsets: remap by name

PC techsets hold D3D9 programs, PS3 ones RSX programs (pc-route.md 3), so a PC techset is
never converted; the material takes a PS3 techset of the base zone by its PC name. Order:
the same name; a placeholder of that name (`,wc_l_sm_b0c0n0s0`: 43 of mp_nuked's 104
techsets are placeholders for techsets of the always-loaded zones); otherwise the closest
techset of the same family (`wc_l_sm`, `l_sm`, ...) whose pass arguments read only samplers
and constants the material has (argument type 2 = material sampler by name hash, type 6 =
material constant by hash: `wc_l_sm_r0c0n0s0` reads colorMap, normalMap, specularMap and
envMapParms 0x3d9994dc; `wc_l_sm_r0c0` colorMap only), preferring the same layer kinds and
suffixes (`r0c0` against `t0c0`, `_seethru`), then the most samplers, then the closest name.
A material with no candidate stops the conversion. The state bits come from a stock material
drawn with the chosen techset (11.2), so one must exist in the base zone.

| PC techset (box maps) | PS3 (mp_nuked) | How | Samplers read |
|---|---|---|---|
| wc_l_sm_r0c0 | wc_l_sm_r0c0 | same name (39 of 178 PS3 zones have it) | colorMap |
| wc_l_sm_r0c0n0s0 | wc_l_sm_r0c0n0s0 | same name (75 zones) | colorMap, normalMap, specularMap; envMapParms |
| wc_l_sm_r0c0n0s0x0 | wc_l_sm_r0c0n0s0x0 | same name (74 zones) | colorMap, normalMap, specularMap |
| wc_l_sm_r0c0 into mp_firingrange (which lacks it) | wc_l_sm_r0c0x0 | closest of 5 candidates | colorMap |

### 11.4 Images

Per texture of a new material, in this order: an image of that name in the base zone is
reused (its alias; `,name` placeholders included); an image held by an always-loaded zone
(code_post_gfx_mp, common_mp) becomes a placeholder `,name` (0x70 zero bytes, name -1, as
stock mp_nuked's `,$identitynormalmap`); otherwise the image is converted into the map's
zone by `images.py` (`convert_image`: DXT blocks copied, other formats swizzled; the header
keeps the PC mapType, semantic, category and hash), from the PC zone's pixels or the
`.iwi`. An RGBA override (`overrides={name: array}`) replaces the pixels with the map's own
art, encoded as DXT1 (DXT5 when any alpha is below 255) with a full mip chain.

Decision: inline, deferred to the end-of-zone block, not the map's own `.pak`. Evidence:

- The loader puts a non-streamed image's pixels (GfxImage +0x2c non-zero, +0x1b = 1) in the
  deferred PHYSICAL_RUNTIME block at the zone's end (structs-content.md 7); stock MP maps do
  this for their own images (mp_nuked: 101 images, 0x115d900 bytes, the largest of the 26
  MP map zones of the base game and the DLC; SP levels up to 0x21d5500, kowloon). The box converted into mp_nuked with baked
  lighting has 0xa08200 bytes there (the stock 2048 x 2048 lightmaps are replaced by the box's
  own), so a few 512 x 512 textures (0x2ab00 each in DXT1) stay well inside what the stock
  map loads.
- A streamed image needs pak part records; mode 1 keeps the mip tail in images_low.pak
  (slot 1, shared by every zone, pak.md 5), which a custom map must not change; a single-part
  (mode 2) image in a level's own pak (slot 0) is not seen in any stock zone. So the own-pak
  route has no stock precedent, the deferred one has.
- Streamed images are also limited by the pak header pool (240 KiB for all open paks, pak.md
  2.1); deferred images are not.

The own `.pak` (named mode, `mp_<name>.pak`) stays the place for streamed images should a
map ever exceed the deferred budget; not implemented.

### 11.5 Integration (to be wired into `mapzone.convert_map`)

```python
from opent5.convert import materials as mats

# after stock_words / pc_names, replacing the "missing materials" refusal:
mres = mats.convert_materials(
    pgfx, stock_gfx, base,
    files=mats.ImageFiles(image_roots),                     # PC game folder(s)
    shared_images=mats.shared_image_names(shared_zone_paths),  # code_post_gfx_mp, common_mp
    force=force_materials,                                   # names, or True
)
report["materials"] = mres.report
words = {name: w for name, (w, _) in stock_words.items()}
words.update(mres.words)
gres = world.convert_gfx(pgfx, stock_gfx, words, ...)
for element, name in zip(surfaces, pc_names, strict=True):
    element["material"] = stock_words[name][1] if name in stock_words else None
...
splice = Splice(base, foreign)
mats.attach(mres, pgfx, pc_names, surfaces, splice)          # before splice.build
```

A surface drawn with a new material keeps its PC material word, which `Splice` resolves
through the PC parse to the converted MaterialMemory element (`attach` marks bytes
+0x40..+0x44 of those surfaces as PC values); the material's techset word and the reused
images' and state bits' words are base values. CLI options proposed: `--pc-game DIR` (where
the `.iwi` are; default the folder of the PC zone's game), `--new-material NAME` (force).

## 12. Static models and XModels (`smodels.py`, `xmodel.py`)

Static models are the `misc_model` props of a Radiant map. cod2map / linker_pc write them as
three arrays (GfxWorld `smodelInsts`, `smodelDrawInsts`, clipMap `staticModelList`) whose
model fields name XModel assets. A map built from stock props reuses the base zone's XModels
by name (their materials, techsets and images are then already in the base, with its
`.pak`), as world surfaces reuse its materials (3.4). `xmodel.py` converts a PC XModel
itself, proven below on every model PC and PS3 mp_nuked share, for props the base does not
hold (open: adding one as a new asset, 12.5).

### 12.1 Static model arrays, PC -> PS3 (proven on mp_nuked, 4209 instances)

| Struct | PC | PS3 | Rule |
|---|---|---|---|
| GfxStaticModelInst | 0x28: mins, maxs, lightingOrigin, groundLighting | same | swapped |
| GfxStaticModelDrawInst | 0x4c: cullDist, origin, axis[3][3] f32, scale, model, flags, smodelCacheIndex[4], lightingHandle u16, reflectionProbeIndex, primaryLightIndex | 0x2c: cullDist, origin, axis rows as 3 CMP words, scale, model (alias), flags, lightingHandle, probe, primary light | axes normalised and packed as `world.cmp_pack`; cache indices dropped |
| cStaticModel_s (clipMap) | 0x50, XModel alias at +0x4 | same | swapped (`world.convert_clip`), +0x4 replaced by the base's alias word |
| GfxWorld dpvs counts (smodelCount, smodelVisDataCount, ...) | PC +0x344 .. | PS3 +0x35c .. | header words, moved by `world.gfx_header` (identical in mp_nuked: 4209, 1, 3294, ..., 132, 104) |
| cell aabb trees (smodelIndexes), shadow geometry (smodelIndex) | u16 lists | same | swapped by `world.convert_gfx` |

Evidence, first instance (PC GfxWorld asset 393 at 0x1d60989; PS3 zone 0x1e3e223):

```
PC   0000fa45 664618c4 000057c3 666676c2 | f250713f c4e8aabe 00000080 c4e8aa3e f250713f 00000000
     00000000 00000080 0000803f | 0000803f | e52f0080 | 00000000 | 00000000 00000000 | 00000101
PS3  45fa0000 c4184666 c3570000 c2766666 | 00355bc4 001e2155 7fc00000 | 3f800000 | 80003045 |
     00000000 | 00000101
```

`smodels.compare_static_models` (PC mp_nuked converted with PS3 mp_nuked's model words):

| Field | Identical (of 4209) |
|---|---|
| origin, axes (CMP), scale, flags, lightingHandle / probe / primary light | 4209 |
| model alias word (from the base zone's alias slots, `model_words`) | 4209 |
| cullDist | 2801 (the others within 1.1e-4 relative: PS3 linker recomputed from its own model bounds) |
| GfxStaticModelInst groundLighting | 4209 |
| GfxStaticModelInst mins / maxs, lightingOrigin | 46 / 334 (largest difference 0.65 / 0.58 inch, same cause) |
| clipMap staticModelList (1385): model words, bytes 0x0 .. 0x37 | 1385, 1385 (absmin / absmax low bits as box-map.md 3.1) |

Lighting the props get (INFERRED from the fields; the renderer was not read): cod2rad's
lightingOrigin and groundLighting colour per instance, and the lightingHandle, reflection
probe and primary light per draw instance, all carried from the PC map. With
`--lighting baked` the light grid they sample is the map's own (section 10).

### 12.2 Integration into `convert_map` (for the lead to wire in)

`world.convert_gfx` refuses a GfxWorld with static models (`_UNSUPPORTED`); taking the two
arrays out first leaves it unchanged:

```python
from opent5.convert import smodels
taken = smodels.take(pgfx)                     # before world.convert_gfx
gres = world.convert_gfx(...)                   # unchanged
world.convert_clip(pclip)                       # unchanged
words = smodels.model_words(bx)                 # base XModel name -> (alias word, node)
sres = smodels.convert_static_models(taken, words, foreign.xfile)
smodels.repoint_clip(pclip, words, foreign.xfile)
report["static_models"] = sres.report
...
splice = Splice(base, foreign)
...
smodels.place(pgfx, pclip, sres, splice)        # before splice.build
```

`place` sets `smodel_insts` / `smodel_draw_insts` on the converted GfxWorld and registers
the draw instance nodes and the clipMap list as holding base values (`Splice.origin`,
`origin_ranges`). A prop the base lacks stops the conversion with its name.

### 12.3 XModel, PC -> PS3

PC handlers (`pc.py`, OpenAssetTools XModel.txt): XModel 0xfc, XSurface 0x44, vertices
GfxPackedVertex (32 bytes: xyz, binormal sign, D3DCOLOR, packed half uv, normal, tangent),
reusable verts0 / vertList / collision trees / triIndices. With them (and two more PC
differences met on the way: XAnimParts boneCount has 10 entries, so names count u8 +0x21
and notifyCount u8 +0x22, asset 512 at 0x4b2e60c; Glasses workMemory is never loaded,
asset 513 at 0x4b2edc6) stock PC mp_nuked walks from its first asset to its last: 515
assets, every block but RUNTIME ends at its header size. RUNTIME ends 0x6600 short (open:
the PC GfxWorld RUNTIME sizes are INFERRED; no rule found that fits the box, the props box
and mp_nuked at once). GfxWorld lodData was changed to align 4: with 128 the props box
ends RUNTIME 0x80 past its header.

| Item | Rule |
|---|---|
| header | PC[0, 0x28) + 4 x lodInfo 28 bytes (PC 32: lod, smcIndexPlusOne, smcAllocBits, unused dropped) + lodDistAutoGenerated and 7 zero bytes + PC[0xac, 0xe0) + 12 zero bytes + memUsage, flags (PC 0xe0) + PC[0xec, 0xfc); PC `bad` dropped. Fence: PC 0x3fae, PS3 0x5795d |
| memUsage (+0xe0) | PC - 4 + per surface with its own vertices: 24 + vertCount x (PS3 bytes per vertex - 32); 288 of 289 exact (the other is the unquantised surface below) |
| XSurface | +0 tileMode, vertListCount, flags, vertCount, triCount; +0x8 triIndices; +0xc vertInfo; +0x1c verts0, +0x24 vertex stream, +0x2c vertList, +0x34 partBits[5] (PC +0x30); +0x48 posOffset, +0x54 0x4b, +0x55 exponents, +0x58 constant colour; PC baseTriIndex / baseVertIndex dropped |
| flags | PC flags + 1 (quantised) + 4 when every vertex has one colour; 1819 of 1820 surfaces (`mp_flag_american_vertical` surface 0 stays unquantised on PS3, flags 0; rule not found) |
| packed positions | offset = (min + max) x 0.5 in f32 (1732 of 1732 quantised surfaces); one exponent e >= 0, the smallest with 2^e >= the largest half extent (less 1e-6: `p_us_table_art02` 16.000002 -> 4); s16 = round((p - offset) x 32768 / 2^e); binormal sign -32768 / 32767 |
| colour | PC B, G, R, A -> PS3 stream R, G, B, A (67 surfaces); constant colour word 0xAARRGGBB (`p_pent_manila_folder_2`: PC `bfbfbfff` -> PS3 `ffbfbfbf`) |
| normal, tangent | PC PackedUnitVec -> CMP (`world.cmp_pack`) |
| uv | the PC packed half pair as one u32, swapped (fence: PC `00301cb5` -> PS3 `b51c3000`) |
| collSurfs | PS3 0x24 per surface: the PC collTris pointer and count dropped |
| bones, blend data, tension, rigid vert lists, collision trees, high mip bounds, collmaps, physPreset | same layouts, swapped |

### 12.4 Proof: PC mp_nuked's XModels against PS3 mp_nuked's

`xmodel.compare_xmodels` converts every PC model whose name PS3 mp_nuked also has (293 of
293) and compares field by field and vertex by vertex (1820 surfaces, 819 455 vertices,
821 442 triangles):

| Compared | Result |
|---|---|
| triangle indices, uv, colours, binormal signs, flags (1819), posOffset (1819), exponents (1819), constant colours, partBits | identical |
| bone names (by script string), parent lists, quats, trans, base matrices, bone info, collSurfs, part classification, material names per surface, physPresets, collmaps, rigid vert lists, blend weights, tension data | identical |
| positions | within 2 quantisation steps everywhere: 63 436 vertices exact, 593 962 one step off, 161 877 two |
| normals, tangents | 140 430 / 135 170 words identical; largest angle 0.44 degrees |
| header bounds, lod distances (68 models), high mip bounds (278), collision tree trans / scale and node bounds | float differences |
| memUsage | 288 of 289 |

The position, bound and normal differences have one cause: the PS3 linker quantised from
source data the PC zone no longer has (the PC floats are themselves rounded: PS3 bounds
match neither the PC floats nor the PS3 quantised vertices; the best rule over the PC
floats, floor((p - offset) x 32766.5 / 2^e), matches 72%). The converter quantises the PC
floats to the nearest step, the decode error the PS3 format allows.

Writing them: every top-level XModel of PS3 mp_nuked (289) replaced by its converted PC
model (`xmodel.adopt` keeps the stock dicts, material elements, physPreset and collmaps,
which other assets name by offset pointer) and the zone rewritten: parses exactly, and the
game's own loader (emulated, `tools/convert_map.py oracle`) consumes all 72 101 510 bytes,
every block ending at its header size, converting 108 303 pointers with the same fields and
values as the product parser.

### 12.5 Limits

- A prop must be an XModel of the base zone. Adding a converted model as a new asset (its
  materials and images from other zones, `images.py` / `materials.py`) is not done; a
  converted BrushWrapper's planes pointer names its own first side's plane, which a new
  node cannot express to `Rewrite` yet (`adopt` keeps the stock collmaps for that reason).
- The GUI world view does not draw static models; `tools/dump_zone.py` places them
  (`world/<map>_static_models.obj`, `previews/world_models_*.png`).
- PC GfxWorld RUNTIME sizes (12.3).

## Sources

docs/research/box-map.md, pc-route.md, structs-map.md (with its errata), walk-all.md,
remap.md section 8; OpenAssetTools src/Common/Game/T5/T5_Assets.h (PC structs, through
`xfile/layouts_pc.py`) and src/ZoneCode/Game/T5/XAssets/*.txt (PC load rules); the stock
scripts read from common_mp.ff and patch_mp.ff; the zones and files named above.
