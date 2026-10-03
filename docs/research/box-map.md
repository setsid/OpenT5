# P6: a box map from the PC tools to a PS3 map zone

Status: research and prototype. The PC tools built a box map headless; the PC zone walks
exactly; the PC world assets were converted to PS3 form and spliced into an in-memory copy of
the stock PS3 mp_nuked zone; the result parses exactly with the product parser and is consumed
exactly by the game's own loader (emulated). Nothing has been run on RPCS3 or hardware (not
allowed for this track). Prototype code is in the track scratch directory (`p6/`), not in
`src/`.

Conventions: "PC zone" is the zlib-inflated payload of a PC `.ff` (from byte 12). "PS3 zone"
is the inflated XFile stream. Offsets are into those streams. Type numbers are PS3 numbers
(xfile.md section 5) unless "PC type" is written. "Box" is the map built here,
`mp_opent5box`. Scratch is `/tmp/claude-1000/-home-cbolland-opent5/<session>/scratchpad/p6/`.

Contents

1. Building the box with the PC tools (commands and output)
2. The PC zone: format differences met, the box walk, the stock PC mp_nuked walk
3. PC to PS3: per-asset mapping, proven on mp_nuked
4. The PS3 target and the strategy
5. Prototype: splice into mp_nuked, validation
6. Plan for `src/opent5/convert/`, and risks
7. Files created outside scratch

## 1. Building the box with the PC tools

### 1.1 Inputs

- `C:\o5\p6\map_source\mp_opent5box.map` (written by `p6/genmap.py`): `iwmap 4`, the layer
  lines and brush syntax copied from the user's `map_source/test_room.map` (the stock
  `map_source/mp/*.map` files are 50-byte placeholders: "// Blank / Used By Launcher To
  Populate Map List"). Six brushes: floor, ceiling and four walls, 16 units thick, enclosing
  x, y in -512..512 and z in 0..256. Inner faces use `jun_art_concrete_base02` (floor),
  `us_art_wall_vinylsiding_white` (walls), `pent_art_wall_creampaint02` (ceiling); every other
  face is `caulk`. All three materials are used by world surfaces of PS3 mp_nuked (as
  `wc/...`, section 4.3). Worldspawn keys copied from the PC mp_nuked entity string (PC zone
  offset 0x38d6edd: `"sundirection" "-37 221 0"`, `"sunlight" "14"`, ...; the sky box key
  dropped). Entities: one `light` (origin 0 0 200, radius 1200, intensity 1.5),
  `info_player_start`, `mp_global_intermission`, three `mp_tdm_spawn_allies_start`, three
  `mp_tdm_spawn_axis_start`, six `mp_tdm_spawn`, six `mp_dm_spawn` (classnames as counted in
  the PC mp_nuked entity string: 34 mp_tdm_spawn, 30 mp_dm_spawn, 9 of each start class).
- `zone_source\mp_opent5box.csv` (the mp_nuked CSV minus the map's own assets):

```
ignore,code_post_gfx_mp
ignore,common_mp
include_console,mp_configStrings
,
col_map_mp,maps/mp/mp_opent5box.d3dbsp
gfx_map,maps/mp/mp_opent5box.d3dbsp
```

### 1.2 Command lines (all via `cmd.exe /c C:\o5\p6\<name>.bat` from WSL, cwd = game `bin`)

The command lines are the ones `bin/Launcher.exe` builds (its UTF-16 strings include
`-platform pc `, `-loadFrom "`, `launcher_ldr`, `cod2map.dll`, `-nopause -language `):

```
launcher_ldr.exe cod2map.dll cod2map.exe -platform pc -loadFrom "C:\o5\p6\map_source\mp_opent5box.map" "<game>\raw\maps\mp\mp_opent5box"
launcher_ldr.exe cod2rad.dll cod2rad.exe -platform pc -fast "<game>\raw\maps\mp\mp_opent5box"
launcher_ldr.exe linker_pc.dll linker_pc.exe -nopause -language english mp_opent5box
```

Results:

| Step | Exit | Console | Output |
|---|---|---|---|
| cod2map (loader) | 0 | none (the loader's child prints elsewhere) | `raw/maps/mp/mp_opent5box.d3dbsp` 8052 bytes, `IBSP` version 45 (`49425350 2d000000`), md5 afa3205c... (identical on two runs); `.d3dprt` 411 bytes; `.d3dpoly` 0 bytes |
| cod2rad (loader) | 0 | none | d3dbsp rewritten with lighting: 10 230 120 bytes, `IBSP` v45, 25 lumps (`2d000000 19000000`); `.radtrans` 1 457 193 bytes |
| linker_pc (loader) | 0 | none | `zone/English/mp_opent5box.ff` 127 296 bytes (md5 4cf32c2d18cb40f7a8e89c8eca6293b9, copied to `C:\o5\p6\`), plus `zone_source/english/assetinfo/mp_opent5box.csv`, `_dep.txt`, `_xmodel.csv`, `assetlist/mp_opent5box.csv` |

Without the loader (to see the output):

- `cod2map.exe` alone runs and logs normally (`p6` Windows log `cod2map_direct.log`: "finding
  triangle windings ... emitting cells and portals ... building curve/terrain collision ...
  removed 0 brush sides ... Writing ...mp_opent5box.d3dbsp"), but writes `IBSP` version 31
  (`4942 5350 1f00 0000 1b00 0000`), the World at War format. So `cod2map.dll` is what makes it
  write the Black Ops v45 BSP.
- `linker_pc.exe` alone fails: "UNRECOVERABLE ERROR: Com_LoadBsp: Not allowed at this time!"
  (exit 1, `linker_direct.log`); the `.ff` was left unchanged (same md5). So `linker_pc.dll`
  is required too.
- Both linker runs report "ERROR: Could not open
  '../zone_source/english/assetlist/code_post_gfx_mp.csv'" and the same for `common_mp.csv`:
  the `ignore,` lines need asset lists this install does not have, so nothing was excluded.
  The linker also stores this log in the zone, as the last rawfile, named after the zone
  (section 2.2, asset 7).

A first attempt with the output path under `C:\o5\p6` instead of the game's `raw\` failed:
cod2map derives its search path from the output path (`.../bin/"C:/o5/p6//raw`) and finds no
materials. Quoting from WSL straight into `cmd.exe /c` also breaks the arguments
(`file '"C:\o5\p6\map_source\mp_opent5box.map"' not found`); writing a `.bat` avoids it.

Everything ran headless; no window was needed. Linker asset info for the zone
(`assetinfo/mp_opent5box.csv`): com_map 0.5 KB, techset `wc_l_sm_r0c0n0s0` 185.8 KB and
`wc_l_sm_r0c0n0s0x0` 167.7 KB, three materials, 13 images (`*reflection_probe0`,
`*lightmap0_primary`, `*lightmap0_secondary`, `*lightmap0_secondaryb`, `$outdoor`, the
materials' colour/normal/spec maps), gfx_map 7.0 KB, game_map_mp 18.0 KB, map_ents 1.8 KB,
col_map 143.9 KB (listed as `col_map_sp`, the zone has PC type 12 = col_map_mp).

## 2. The PC zone

### 2.1 Reading it: what differs from PS3 (each met while walking)

The scratch walker (`p6/pcxfile.py`, `p6/pchandlers.py`) is the product walker with the
scalar readers switched to little-endian, the asset array's PC type numbers renumbered to PS3
ones (0..6 same, 7..41 +2, 42 -> 45; pc-route.md 1.3), and PC handlers registered over the PS3
ones where the structs differ. Differences found, with evidence:

| Item | PC | PS3 | Evidence |
|---|---|---|---|
| Container | `IWffu100`, u32 LE 473, one zlib stream from byte 12 | signed, Salsa20, chunks | box `.ff` 127 296 -> 3 295 929 bytes, eof true, 31 bytes unused |
| XFile header | nine u32 LE; externalSize non-zero | BE, externalSize 0 | box: size 0x324a95, external 0x2800c8, blocks TEMP 0x10048c, RUNTIME 0x1fd30, VIRTUAL 0x63be6, rest 0 |
| TEMP block size | high-water mark + 16 | same | the box walk ends TEMP exactly at 0x10048c with the product rule |
| RawFile buffer alignment | 1 | 16 (structs-content.md 3) | gfx_map name pointer `51020080` = VIRTUAL+0x250; with align 16 the walk puts the com_map name at 0x255, with align 1 at 0x250 |
| Reusable pointer = -2 | followed (with an alias slot), e.g. GfxImage texture | (asset pointers only seen) | image header at 0x5944c `feffffff 05010101 ...` (`*reflection_probe0`), its loadDef follows the name |
| GfxImage | 0x34 bytes; name, then `texture.loadDef` (TEMP, 12 bytes + resourceSize) | 0x70, pixels deferred / in `.pak` | 0x59492: `00 04 0000 'DXT1' 90000000` + 0x90 bytes |
| Techset | 528 bytes, 130 technique pointers; D3D9 shader programs inline | 0x124, RSX programs as types 7/8 | two box techsets: 190 597 and 172 071 bytes |
| Material | 192 bytes | 0x80 | OpenAssetTools `Material`; the box's three materials walk |
| GfxWorld | 0x43c | 0x454 | section 3.4 |
| GfxSurface | 0x50, material +0x30 | 0x60, material +0x40 | section 3.4 |
| GfxWorldVertex | 44 bytes, one stream | 16-byte positions + per-group layer data in PHYSICAL | section 3.4 |
| GfxWorld RUNTIME sizes | sceneDynModel 6 x n, visData 1 x n (align 1), lodData 32 x n, castsSunShadow 4 x n (align 128), dynEntVisData[2][3] 32 x words, row-major | see structs-map.md 8 | only these choices end RUNTIME at the box header's 0x1fd30; the set is not unique (20 combinations fit), INFERRED |
| clipMap_t, ComWorld, GameWorldMp, MapEnts, cbrush_t, DynEntityDef, PhysConstraint | same sizes | same | section 3.1 |

### 2.2 The box zone walk: exact

`p6/walkbox.py` over the box zone: stream consumed to 0x324ab9 (the end), all seven blocks end
at the header sizes, `problems() == []`.

| # | PC type | Asset | Offset | Bytes |
|---|---|---|---|---|
| 0 | 36 rawfile | `tahcep` (517 bytes of binary, INFERRED a tool watermark) | 0x78 | 536 |
| 1 | 13 com_map | `maps/mp/mp_opent5box.d3dbsp` | 0x290 | 532 |
| 2 | 7 techset | `wc_l_sm_r0c0n0s0` | 0x4a4 | 190 597 |
| 3 | 7 techset | `wc_l_sm_r0c0n0s0x0` | 0x2ed29 | 172 071 |
| 4 | 17 gfx_map | (name = offset to the com_map name) | 0x58d50 | 2 889 569 |
| 5 | 15 game_map_mp | | 0x31a4b1 | 16 428 |
| 6 | 12 col_map_mp | (map_ents inline, -2) | 0x31e4dd | 25 914 |
| 7 | 36 rawfile | `mp_opent5box` (the linker error log) | 0x324a17 | 162 |

There is one script string (pointer 0, null). Materials and images are inline under the
gfx_map (materialMemory, lightmaps, probe, outdoor image). Box content in numbers:

- com_map: 2 primary lights, light 0 all zero, light 1 the sun (type 1, colour 14, 13.72,
  12.46 = worldspawn `sunlight` x `suncolor`). The `light` entity is baked by cod2rad and is
  not a primary light.
- gfx_map: 6 planes, 19 nodes, 3 surfaces (walls 16 vertices / 8 triangles, floor 4 / 2,
  ceiling 4 / 2), 24 vertices, 36 indices, 1 cell, 0 portals, 1 reflection probe, 1
  lightmap, 1 brush model, light grid with 1 colour and no entries, bounds (-512, -512, 0) ..
  (512, 512, 256), sunPrimaryLightIndex 1, primaryLightCount 2, dynEntClientCount[0] 256.
- col_map_mp: 6 planes (an offset pointer into the gfx_map planes, `b18b0580`), 4 materials,
  0 brush sides (the six brushes are axial), 7 nodes, 9 leafs, 9 leaf-brush nodes, 7 leaf
  brushes, 48 brush vertices, 1 cmodel, 6 brushes, 1 cluster (8 bytes), 256 empty dynamic
  entity definitions (dynEntCount[0] = 256, originalDynEntCount 0), max_ropes 32.
- game_map_mp: 0 path nodes (the 128 spare node slots are still stored).
- map_ents: 1802 bytes of entity text, as written in the .map.

### 2.3 Stock PC mp_nuked

Present: `zone/Common/mp_nuked.ff` (and 13 more PC MP maps; smallest by file is mp_villa,
36 491 040 bytes). Full walk: not reached. The scratch walker stops at asset 37 (PC zone
0x6f4e) because asset 36, an XModel, was read with the PS3 XModel handler (PC XModel is 252
bytes, PS3 248; PC XSurface 68, PS3 92); PC handlers for XModel, FX, XAnim, sound,
destructibledef and glasses were not written. Partial walk: the world range, assets 355
(com_map, 0x183bae2) to 395 (col_map_mp, ends 0x38ffd46), started by hand at the com_map
header (found by its name string at 0x183bb22), walks cleanly through the lightdef, 36
techsets, gfx_map (26 419 263 bytes), game_map_mp (93 039) and col_map_mp (2 451 471). Those
are the assets section 3 compares with PS3.

## 3. PC to PS3, per asset, proven on mp_nuked

Method: convert the PC mp_nuked world assets with the field swap (`p6/swap.py`; field types
from `opent5.xfile.layouts_pc`, plus the corrections below) and compare every array with the
PS3 mp_nuked asset (`p6/validate_swap.py`, `p6/validate_gfx.py`). A difference in a pointer
word is expected (the two zones have different block layouts); anything else is a finding.

### 3.1 col_map_mp, com_map, game_map_mp, map_ents: endian swap only (CONFIRMED)

Same sizes on both platforms (PC zone bytes: com_map 5380, game_map_mp 93 039, col_map_mp
2 451 471, the PS3 figures exactly). After the swap:

| Array (struct) | Bytes | Non-pointer words that differ |
|---|---|---|
| ComWorld header | 64 | 0 |
| primaryLights (ComPrimaryLight 220) | 5280 | 0 (21 defName offset pointers differ) |
| GameWorldMp header, nodes (pathnode_t 128), chainNodeForNode, nodeForChainNode, pathVis | 44, 56 832, 632, 632, 12 443 | 0 (name pointer) |
| nodeTree (pathnode_tree_t) | 3632 | 0 (226 child offset pointers) |
| clipMap_t header | 332 | 0 (name and planes pointers) |
| materials, leafs, leafbrushes, verts, brushVerts, uinds, triIndices, triEdgeIsWalkable, borders, aabbTrees, cmodels, visibility, box_brush, constraints | 28 368 .. 584 964 each | 0 |
| brushsides, nodes, partitions, brushes, leafbrushNodes | | pointer words only (plane, borders, sides/verts, brushes) |
| staticModelList (cStaticModel_s 80) | 110 800 | xmodel alias pointers, and the absmin/absmax floats (+0x38..+0x4f) in low bits (e.g. `43af4ee8` vs `43af4ef0`); INFERRED recomputed from the PS3 model bounds at link time |
| dynEntDefList[0] (DynEntityDef 84) | 42 420 | asset pointers only (+0x20, +0x24, +0x2c, +0x38) |
| MapEnts header, entity string | 12, 124 689 | 0 (name pointer) |

Corrections to the flattened PC layouts that were needed:

- `cLeafBrushNode_s` +0x8: the union is flattened to `data.brushes`; the other member is
  `{f32 dist; f32 range; u16 childOffset[2]}`, so +0xc swaps as 4 bytes and +0x10 as two u16.
  Evidence: PS3 `3e164000` / PC `0040163e` at +0xc, PS3 `0017002c` / PC `17002c00` at +0x10.
  After the fix leafbrushNodes differ only in the 6027 brush-list pointers.
- `cLeaf_t` (44) and `cbrush_t` (96, sides +0x20, verts +0x58) are not in `layouts_pc`; their
  OpenAssetTools definitions were used (src/Common/Game/T5/T5_Assets.h).

Conversion rule: swap every field; keep the asset name (it must stay the target map's name);
re-point every offset pointer (section 5.2). In MP map zones the MapEnts is inline (-2) in the
clipMap on both platforms.

### 3.2 lightdef

Same 16-byte struct; its attenuation image is a GfxImage (PC 0x34 + loadDef, PS3 0x70 +
deferred pixels). The box has no lightdef; stock mp_nuked's `white_light` is kept (section 5).

### 3.3 Materials and techsets: by name (CONFIRMED names exist)

PC techsets carry D3D9 programs; PS3 ones RSX programs (pc-route.md 3). Not converted. The box
needs `wc_l_sm_r0c0n0s0` and `wc_l_sm_r0c0n0s0x0`; both are top-level techsets of PS3 mp_nuked
(assets 370 and 369, zone 0xe8f266 and 0xe85501), and the three box materials are all materials of PS3 mp_nuked world
surfaces (`wc/us_art_wall_vinylsiding_white` 372 surfaces, `wc/jun_art_concrete_base02`,
`wc/pent_art_wall_creampaint02` 140; product parse of PS3 mp_nuked). In the PS3 GfxWorld a
surface's material is an alias pointer into the inline `materialMemory` materials; the
converter reuses the alias value of a stock surface with the same material name.

### 3.4 gfx_map: re-layout (header CONFIRMED on mp_nuked, buffers INFERRED)

Header. PS3 = PC[0x0, 0x24) + 20 PS3-only bytes + PC[0x24, 0x218) + 4 PS3-only bytes +
PC[0x218, 0x43c), i.e. PC fields move by +0x14 from `skySurfCount` (PC 0x24 -> PS3 0x38) and by
+0x18 from `lightGrid` (PC 0x218 -> PS3 0x230). With that rule the swapped PC mp_nuked header
differs from the PS3 one in six words only:

| PS3 off | Field | PC | PS3 | Reason |
|---|---|---|---|---|
| 0x0 | name | `8180cda1` | `808b4e81` | offset pointer |
| 0x14 | streamInfo.aabbTreeCount | 0x217 | 0x270 | streaming tree built per platform |
| 0x1c | streamInfo.leafRefCount | 0x1cfe | 0x1de4 | same |
| 0x44 | skySamplerState | 0xeb | 0xea | INFERRED platform sampler bits |
| 0x214 | vertexLayerDataSize | 0x16214 | 0x4cb9b4 | different vertex layout |
| 0x29c | sun.flareMaterial | `82b138c9` | `813140bd` | alias pointer |

The PS3-only words are zero in stock mp_nuked (0x24..0x37 all zero; 0x22c zero).

Arrays (after swap) compared with PS3 mp_nuked: planes (cplane_s 20) equal apart from 114 of
10 101 planes whose byte +0x11 is 0 on PC and 8 on PS3 (`02000000` vs `02080000`; meaning
INFERRED); dpvs nodes, brush models (GfxBrushModel 60), sortedSurfIndex, light grid
rowDataStart and entries (GfxLightGridEntry u16, u8, u8), shadowGeom: identical; cells: 3 words
differ; light grid colours: 42 words at the end differ; light grid rawRowData must be swapped
as u16 (`5f0e0200` vs `0e5f0002`; its row structs are INFERRED); streaming aabbTrees and
leafRefs: different counts (rebuilt for PS3); staticModelInsts: float differences.

Vertices, indices, surfaces: the vertex count is equal (176 344) but the order differs (614 248
of 705 376 position words differ) and so do the indices: the PS3 linker regroups vertices.
Surface bounds (+0x48) agree in all 3494 surfaces and the srfTriangles mins/maxs in all but
230; the srfTriangles vertex/index fields (+0xc, +0x1c..+0x2c) do not. So the
PS3 buffers are not a re-encoding of the PC ones in place; a converter must produce a
self-consistent PS3 layout. Rules used for the box (from docs/extract.md 3.1):

- positions: 16 bytes per vertex, x, y, z, binormal sign (f32 BE);
- layer data: one group per surface, 28 bytes per vertex: colour R, G, B, A; u, v; lightmap
  u, v (f32 BE); normal and tangent as CMP 11:11:10 (x bits 0..10 x 1023, y 11..21 x 1023,
  z 22..31 x 511). The PC normal is a PackedUnitVec, (b - 127) x (b3 + 192) / 32385
  (box floor normal `7f7ffe3f` -> (0, 0, 1)); the product's `unpack_cmp` reads the converted
  normals back as the PC ones (box walls -X, -Y, +Y, +X, floor +Z, ceiling -Z). PC colour is
  assumed BGRA (D3DCOLOR) and reordered to RGBA: INFERRED, the box is all `ffffffff`. Stride
  28 is right for these materials: every stock mp_nuked group that uses them has stride 28.
  Layered materials (stride 36 to 56) would need the PC layer data merged in: not done.
- surface (0x60): mins; +0xc = 28 x firstVertex (the group's layer offset); maxs; +0x1c =
  firstVertex (group start); +0x20 = 0 (first vertex in the group); +0x24 vertexCount,
  triCount; +0x28 baseIndex; +0x2c himipRadiusSq; +0x30 stream2ByteOffset; +0x34..+0x3f zero
  (as in all 3494 stock surfaces); +0x40 material; +0x44 lightmap/probe/primary light/flags
  bytes; +0x48 bounds. Indices stay PC values (relative to the surface's first vertex, which
  is the group start). Winding: the box floor triangle (0, 1, 2) has (v1 - v0) x (v2 - v0)
  pointing down while its normal points up, the PS3 convention of extract.md 3.1, so no index
  swap is needed.
- GfxStaticModelDrawInst differs (PC 76, PS3 0x2c with CMP axes; extract.md 3.3); the box has
  no static models.
- Images (lightmaps, reflection probe, outdoor, sky): PC GfxImage + loadDef cannot be used as
  is; the prototype keeps the stock PS3 images (section 5).

### 3.5 Other assets of a map zone

| Asset | Status for the box |
|---|---|
| rawfile | same struct; PC buffer align 1, PS3 16; scripts must be PS3-legal (pc-route.md 1.4) |
| stringtable (12 configstrings_ps3_*) | PS3 only (the PC MP CSV has `include_console,mp_configStrings`); kept from the target map |
| sound | PS3 mp_nuked has two banks, `mpl_nuked.english` (asset 442) and `mpl_nuked.all` (443); PC mp_nuked one (PC type 9). The "extra sound" of pc-route.md 1.3 is the language split (INFERRED from the names). Kept from the target |
| texturelist | PS3 only, one per map zone (asset 526); kept |
| glasses | optional: PS3 mp_havoc has none; mp_nuked has 62 (asset 527) |
| xmodel, fx, destructibledef, physpreset, xanim | not needed by the box; kept as they are in the target |

## 4. The PS3 target and the strategy

### 4.1 What a PS3 MP map zone contains

Asset type counts (product parse, all exact): mp_villa (fewest assets on disc, 498): col_map_mp
1, com_map 1, destructibledef 4, fx 81, game_map_mp 1, gfx_map 1, glasses 1, lightdef 1,
material 2, physpreset 4, rawfile 24, sound 2, stringtable 12, techset 137, texturelist 1,
xmodel 225. mp_havoc (smallest content, 63 856 189 bytes, 516 assets): the same without glasses.
mp_nuked (529): adds xanim 1. Common to all three, in this order: 12 configstrings tables
first; `aitype/enemy_dog_mp.gsc`; com_map then lightdef `white_light`; techsets; gfx_map,
game_map_mp, col_map_mp together; the map's gsc/csc rawfiles; two sound banks; two
`faction_128_*` materials; mpbody/mphead gsc rawfiles; texturelist; (glasses); a last rawfile
named after the zone (1 byte in mp_nuked).

Minimal set for a box (INFERRED until it loads): the five world assets, `white_light`, the
techsets and materials the surfaces use, the map gsc/csc (stripped), the configstrings, the
sound banks, the mpbody/mphead/faction assets the gametypes precache, texturelist.

### 4.2 Decision: (b), replace the world assets of a stock map, ship under its name

Chosen target: mp_nuked. Reasons, with evidence:

1. The same map exists as a PC zone, so every conversion rule could be tested against the PS3
   original (section 3). Nothing like that is possible for a from-scratch zone.
2. The box's three materials and two techsets are already in it (3.3), with their images in
   `mp_nuked.pak`, so no material, techset, image or `.pak` has to be built for the first map.
3. Everything a PS3 map zone needs that the PC tools cannot make (configstrings_ps3 tables,
   the two sound banks, texturelist, the RSX techsets) is already there and valid, and menus,
   map tables, load screen and gametypes know the name `mp_nuked`.
4. The product remap already re-lays out a whole zone after count changes; the prototype only
   had to teach it pointers from a second (PC) layout (5.2). The world assets are replaced
   wholesale, not resized field by field.

Option (a), a zone from scratch, needs every one of those PS3-only assets copied in from
stock zones anyway, plus a correct asset order, a new map name in the menu tables and a new
`.pak`; it is the later step once custom textures are needed.

## 5. Prototype: splice into mp_nuked

### 5.1 What `p6/splice.py` does

1. Walk the PC box zone with the traced walker (`opent5.xfile.remap.Rewrite` with the PC
   handlers and little-endian readers) and record, for every PC offset pointer, the node,
   key and byte offset that hold it.
2. Swap the PC col_map, map_ents, com_map, game_map_mp nodes in place (3.1); build the PS3
   gfx_map header and buffers (3.4).
3. Parse stock PS3 mp_nuked (from the scratch copy of the disc zone; the `.ff` it came from
   was only read) with `Rewrite` and splice in place, keeping each stock node object and its
   name: clipMap (its planes pointer set to -1 and the planes stored inline, since the box
   planes would otherwise point into the replaced gfx_map), MapEnts (entity text), ComWorld,
   GameWorldMp, GfxWorld (box planes, nodes, cells, streaming trees, vertices, layer data,
   indices, light grid, brush model, shadowGeom, lightRegion, surfaces; stock name, base name,
   sky, sun light, reflection probes, lightmaps, draw images, materialMemory, sun flare,
   outdoor image kept). The stock lightdef's name pointed at a stock ComWorld light string, so
   it is made inline (`white_light`).
4. Write with the product writer and remap every offset pointer: a pointer held by a
   PC-origin field is resolved through the PC layout and trace, every other one through the
   stock layout (as `Rewrite.build` does), with node identities mapped from the PC nodes to the
   stock ones that received their data.

### 5.2 Results

| Spliced | Content | Pointers | Product parse | Game loader (emulated, `.oracle/r2b`) |
|---|---|---|---|---|
| col_map + map_ents, com_map, game_map_mp | 66 341 569 bytes (stock 68 848 457) | 20 via the PC layout, 62 946 via the stock layout, 23 011 rewritten | exact (`problems() == []`) | consumed 0x3f44ac1 of 0x3f44ac1, blocks equal the header (RUNTIME 0x49f920, VIRTUAL 0x1c54191, PHYSICAL 0x11d90c8, PHYSICAL_RUNTIME 0x115d900), 18 s |
| the above + gfx_map | 51 115 155 bytes | 20 PC, 53 225 stock, 17 540 rewritten | exact | consumed 0x30bf493 of 0x30bf493, blocks equal the header (RUNTIME 0x3d0e80, VIRTUAL 0x129a231, PHYSICAL 0xd0e0c8), 16 s |

Stock mp_nuked under the same harness: consumed exactly in 22 s (control run).

Checks beyond consumption:

- Two independent pointer matchings (by pointer ordinal within the asset, and by holding
  field) produced byte-identical output for the clip/com/game splice.
- Every offset pointer in the spliced clipMap resolves (product `XFile.resolve`) where the PC
  one did: brushes -> brushVerts (6), leafbrushNodes -> leafbrushes (7), nodes -> planes (7),
  names -> the ComWorld name (2); relative offsets equal the PC ones (brush verts 0, 96, ..., 480;
  node planes 0, 80, 20, 20, 40, 60, 100).
- The three box surfaces' material pointers resolve into the stock materialMemory.
- Packed for a later device test (not run): `p6/out/mp_nuked.ff`, 27 591 456 bytes, sha1
  2bedf994f879fe122c39d40690e8afc4131b715d, 681 chunks carried, 362 recompressed; reopening
  gives the spliced content back. The console signature no longer matches: signature-patched
  client only.

### 5.3 Where it breaks, or will

Nothing broke at the loader level. Expected problems at run time (not tested):

1. Scripts. `maps/mp/mp_nuked.gsc`, `_fx`, `_amb`, `createfx/*`, `clientscripts/mp/*` refer
   to nuked entities (mannequins, script_brushmodels, triggers) that the box has not;
   `getent` returning undefined can halt the script VM. Fix: replace them with minimal
   scripts (`maps\mp\_load::main()` and the art/fx calls) through `set_text`.
2. Lighting: box surfaces sample stock lightmap 0 with box lightmap coordinates; light grid has
   one colour. Expect wrong but present lighting.
3. Gametypes other than tdm/dm have no spawns; glasses (62) float outside the box; compass and
   minimap show nuked; dogs need path nodes (box has none).
4. Static models are gone from the GfxWorld but their xmodels remain in the zone (harmless).
5. 256 empty dynamic entity definitions (PC linker output, same on PC) are kept; INFERRED
   harmless.
6. PS3 streaming trees (aabbTrees, leafRefs) are the PC ones; plane byte +0x11 left at the PC
   value.

## 6. Plan for `src/opent5/convert/` (ordered), and risks

1. Byte order as a parameter of the xfile layer. `opent5.xfile.stream` reads through
   module-level big-endian unpackers (`_U16` .. `_F32`), `model.XFileHeader` and `XWriter._raw`
   use `>`; the prototype patches them per process. Make the order a property of
   `XStream`/`Chunk`/`XWriter` so PC and PS3 parses can coexist. Small, but touches the hot
   path; benchmark.
2. `convert/pc_zone.py`: open `IWffu100` (zlib), renumber PC types, PC handlers for the types
   that differ (rawfile align 1, techset, material, image with loadDef and -2 reusable,
   GfxWorld); later XModel/XSurface, FX, XAnim, sound, destructibledef, glasses so that a full
   PC map walks (the stock PC mp_nuked walk is the test).
3. `convert/swap.py`: per-struct swap tables from `layouts_pc` plus the corrections of 3.1.
   Test: PC mp_nuked -> PS3 mp_nuked must differ only in pointer words (and the documented
   float/plane exceptions). The PC zone cannot be a repo fixture (game data); record the
   expected per-array difference counts as numbers.
4. `convert/world.py`: clipMap / ComWorld / GameWorldMp / MapEnts (swap), GfxWorld (header
   re-layout, surfaces, vertex groups, CMP, light grid rawRowData as u16 rows), lightdef.
5. Generalise `remap.Rewrite`: accept nodes from another parse with that parse's layout and
   trace, and map their pointers by holding field (5.1 step 4). Keep the names of the replaced
   assets.
6. `convert/splice.py` (CLI `opent5 convert-map PC.ff --into mp_nuked.ff --out ...`): world
   assets wholesale; materials matched by name to the target's materialMemory, with a clear
   error listing missing materials; stripped map scripts; empty glasses; spawn check for the
   gametypes the user wants.
7. Verification in the product: exact reparse, every pointer of the replaced assets resolving
   into the expected arrays, and the emulated loader in the test suite where the harness is
   available.
8. Later: lightmaps and reflection probes converted (PC loadDef DXT/RGBA to PS3 GfxImage with
   deferred pixels, textures.md), which needs `.pak` writing for streamed images; materials and
   techsets not in the target copied from other PS3 zones; layered-material vertex strides;
   static models (XSurface and GfxStaticModelDrawInst conversion); path nodes; a new map
   name (menus, map table, configstrings) once more than one custom map is wanted.

Risks: the run-time items of 5.3 (scripts first); LinkerMod's `cod2map.dll`/`linker_pc.dll`
are required (stock tools write a v31 BSP or refuse the BSP), so the toolchain depends on that
project; the PC linker could not read `assetlist/common_mp.csv`, so PC zones carry assets the
PS3 side already has (harmless for a splice, wasteful for a from-scratch zone); PS3 vertex
regrouping and streaming trees are not reproduced; the `.ff` loads only on a
signature-patched client.

## 7. Files created outside scratch

In the game folder (new files only, all named `mp_opent5box`; delete to remove):

```
raw/maps/mp/mp_opent5box.d3dbsp
raw/maps/mp/mp_opent5box.d3dpoly
raw/maps/mp/mp_opent5box.d3dprt
raw/maps/mp/mp_opent5box.radtrans
zone_source/mp_opent5box.csv
zone_source/english/assetinfo/mp_opent5box.csv
zone_source/english/assetinfo/mp_opent5box_dep.txt
zone_source/english/assetinfo/mp_opent5box_xmodel.csv
zone_source/english/assetlist/mp_opent5box.csv
zone/English/mp_opent5box.ff
```

Under `C:\o5\p6\`: the `.map`, the four `.bat` files plus two direct-run variants, `logs\`,
a copy of `mp_opent5box.ff`, and two empty files from the failed first cod2map attempt
(`raw\maps\mp\mp_opent5box.grid_auto`, `.grid_not`).

## Sources

- Local files and commands above (offsets and bytes inline); scratch scripts `p6/genmap.py`,
  `pcinflate.py`, `pcxfile.py`, `pchandlers.py`, `walkbox.py`, `swap.py`, `validate_swap.py`,
  `validate_gfx.py`, `splice.py`, `check.py`, `ptrcheck.py`, `emurun.py`.
- OpenAssetTools: src/Common/Game/T5/T5_Assets.h (cbrush_t, cLeaf_t, GfxSurface, GfxLight,
  GfxWorldVertex, srfTriangles_t, MaterialTechniqueSet and pass structs, GfxImageLoadDef,
  MaterialConstantDef, CollisionAabbTree); src/ZoneCode/Game/T5/XAssets/*.txt (load order,
  reusable/count rules: GfxWorld.txt, clipMap_t.txt, MaterialTechniqueSet.txt, Material.txt,
  GfxImage.txt, RawFile.txt).
- docs/extract.md 3.1 (PS3 world vertex layer and CMP), structs-map.md, walk-all.md,
  remap.md, pc-route.md.
