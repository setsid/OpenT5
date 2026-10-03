# R5: The PC route for custom maps (Black Ops Mod Tools output to a PS3 zone)

Status: first pass complete. Sections 1, 2 and the asset-list comparison in section 4 are
CONFIRMED from files on this machine. Per-struct conversion rules (section 3) are partly
INFERRED and must be closed against the PS3 struct layouts from R2 (structs-*.md, not yet
written at the time of this pass).

Conventions: "PC zone" means the inflated payload of a PC `.ff` (everything after the 12-byte
file header). "PS3 zone" means the inflated XFile stream as produced by
`src/opent5/container/fastfile.py`. Offsets are into those streams unless stated. Asset type
numbers on PS3 use the enum in xfile.md section 4.

## 0. What is on this machine

| Item | Path | Notes |
|---|---|---|
| Black Ops PC (Steam app 42700, MP 42710) | `/mnt/c/Program Files (x86)/Steam/steamapps/common/Call of Duty Black Ops/` | `zone/Common/*.ff` (all stock maps incl. mp_nuked, mp_firingrange, mp_radiation), `zone/English/*.ff`, `main/iw_00..25.iwd` |
| Black Ops Mod Tools (BETA), Steam app 42740 | `.../common/Call of Duty Black Ops 42740/` | `appmanifest_42740.acf`: name "Call of Duty Black Ops - Mod Tools (BETA)". `bin/`: `linker_pc.exe`, `converter.exe`, `asset_manager.exe`, `AssetViewer.exe`, `effectsed3.exe`, `Launcher.exe`. No `cod2map.exe` / `cod2rad.exe` / Radiant. |
| Map compiler setup in the game dir | `.../Call of Duty Black Ops/bin/` | `CoDBORadiant.exe`, `cod2map.exe`, `cod2rad.exe` plus `cod2map.dll`, `cod2rad.dll`, `radiant_mod.dll`, `linker_pc.dll`, `game_mod.dll` - the layout of the LinkerMod project (the compile executables come from the World at War mod tools; see LinkerMod project wiki, page "LinkerMod"). `linker_pc.exe` is byte-identical to the mod tools copy (md5 f28cc9dcd44d3df2adb74ab6efd2e297). |
| A compiled user map | `.../Call of Duty Black Ops/usermaps/zombie_test_room/zombie_test_room.ff` (9 495 520 bytes, identical copy under `mods/usermaps/`) | Built from `map_source/zombie_test_room.map`, `raw/maps/zombie_test_room.d3dbsp`, `zone_source/zombie_test_room.csv`. A zombies (SP-world) map, not MP. |
| Stock `.map` sources | `.../Call of Duty Black Ops/map_source/` and `map_source/mp/` (mp_array.map, mp_cairo.map, mp_firingrange.map, ...) | Shipped Radiant sources for the stock maps. |

Nothing was copied into the repo. Scratch inflations are in the R5 scratch directory.

## 1. PC T5 zone format vs PS3

### 1.1 File header - CONFIRMED

PC `.ff`, first 16 bytes (identical pattern in mp_nuked.ff, mp_firingrange.ff, common_mp.ff,
patch_mp.ff, usermaps zombie_test_room.ff):

```
00000000: 4957 6666 7531 3030 d901 0000 7801 ecbd  IWffu100....x...
```

| Off | PC | PS3 |
|---|---|---|
| 0x00 | magic `IWffu100` ("u" = unsigned) | `IWff0100` |
| 0x08 | version u32 **LE** 0x000001d9 = 473 | u32 BE 473 |
| 0x0c | zlib stream (header `78 01`) runs to near EOF | auth header `PHEEBs71`, name, RSA-PSS signature, Salsa20-encrypted raw-deflate chunks from 0x13c |

So PC zones are: no signature, no encryption, a single zlib stream (with zlib header and
Adler-32, not raw deflate). Python `zlib.decompressobj().decompress(data[12:])` inflates them
completely: mp_nuked 37 475 776 -> 78 846 117 bytes (eof true, 30 trailing bytes unused);
mp_firingrange 37 999 616 -> 82 360 651; zombie_test_room 9 495 520 -> 20 870 862.
OpenAssetTools agrees: src/ZoneCommon/Game/T5/ZoneConstantsT5.h (`MAGIC_UNSIGNED = "IWffu100"`,
`ZONE_VERSION = 473`) and src/ZoneLoading/Game/T5/ZoneLoaderFactoryT5.cpp (PC, little-endian,
32-bit, unsigned, unencrypted; inflate processor). OpenAssetTools supports only PC T5.

`linker_pc.exe` contains all three magics as strings: `IWffs100` (file off 0x121bc8),
`IWffu100` (0x121cdc), `IWff0100` (0x121ce8). INFERRED: `s` = signed PC zone (retail/patch),
`u` = unsigned (all stock PC zones on this machine are `u`), `0` = console form.

### 1.2 XFile header - CONFIRMED

Same 36-byte layout (size, externalSize, 7 block sizes), little-endian on PC:

| Field | PC mp_nuked | PS3 mp_nuked |
|---|---|---|
| size (= stream - 36) | 0x04b31881 | 0x041a8b25 |
| externalSize | 0x10c373ce | 0 |
| TEMP | 0x0040048c | 0x00000ab0 |
| RUNTIME | 0x000fd93c | 0x004a8580 |
| LARGE_RUNTIME | 0 | 0 |
| PHYSICAL_RUNTIME | 0 | 0x0115d900 |
| VIRTUAL | 0x03ba2391 | 0x01eb81c1 |
| LARGE | 0 | 0 |
| PHYSICAL | 0x0056a000 | 0x011d90c8 |

PC bytes 0x00..0x23: `8118b304 ce73c310 8c044000 3cd90f00 00000000 00000000 9123ba03 00000000 00a05600`.
Other PC maps follow the pattern (mp_firingrange TEMP 0x0020048c, PHYSICAL_RUNTIME 0;
mp_radiation TEMP 0x0020048c; zombie_test_room TEMP 0x0010048c). Every PC zone has
PHYSICAL_RUNTIME = 0 and a non-zero externalSize; every PS3 zone has externalSize = 0 and a
large PHYSICAL_RUNTIME/PHYSICAL (RSX-side data). Block sizes therefore cannot be copied; they
must be recomputed by whatever writes the PS3 stream. Meaning of PC externalSize: INFERRED
(likely the size of data streamed from outside the zone; confirm in OpenAssetTools
StepLoadZoneSizes and the PC loader).

Pointer size is 32-bit on both; the -1 (inline follows) marker is the same (`ffffffff`).

### 1.3 Script strings and asset list - CONFIRMED identical modulo enum

For the three stock MP maps compared, the PC and PS3 zones share the script string table byte
for byte (52 .. start of asset array; mp_nuked 516 strings, asset array at 0x2d74 in both;
mp_firingrange 609 strings, 0x339b in both; mp_radiation 734 strings, 0x41ae in both). Both
streams are packed (no alignment before the asset array; matches xfile.md section 2).

Asset type numbering differs: PS3 inserts pixelshader (7) and vertexshader (8) after material,
and texturelist (44) before emblemset. Mapping PC -> PS3: types 0..6 unchanged; PC 7..41 -> +2;
PC 42 (emblemset) -> 45.

After remapping, the PC asset array is a subsequence of the PS3 one. Differences (Python
`difflib` over the remapped type sequences):

| Map | PC assets | PS3 assets | PS3-only entries |
|---|---|---|---|
| mp_nuked | 515 | 529 | 12 x stringtable (39) at index 0..11; 1 extra sound (11) at index 443; 1 texturelist (44) at 526 |
| mp_firingrange | 518 | 532 | same three insertions (indices 0..11, 420, 529) |
| mp_radiation | 734 | 748 | same three insertions (indices 0..11, 664, 745) |

The 12 stringtables are per-gametype configstring tables, names read from PS3 mp_nuked:
`mp/configstrings/configstrings_ps3_mp_nuked_{dm,sab,sd,tdm,ctf,koth,dom,dem,hlnd,oic,gun,shrp}.csv`
(first at 0x3e10). The PC zone_source has the matching mechanism: `include,configStrings`
in a map CSV, and the linker emitted `sp/configstrings/configstrings_pc_zombie_test_room_zom.csv`
(zone_source/english/assetinfo/zombie_test_room.csv, row 3). So the PS3 build ran the same
linker with a ps3 platform, and the asset order of a PC map zone is a good template for the
PS3 one. Identity of the extra sound: INFERRED (likely a platform sound bank or patch; confirm
with R1's walker by reading the name of PS3 asset 443 in mp_nuked).

### 1.4 Example asset: rawfile - CONFIRMED

`maps/mp/mp_nuked.gsc`:

```
PC  0392b05f: 0000 0000 ffff ffff 3e0b 0000 ffff ffff   name=-1, len=0xb3e (LE), buffer=-1
    0392b06f: "maps/mp/mp_nuked.gsc\0"  0a250000 360b0000 789c...
PS3 021015e5: 0000 0000 ffff ffff 0000 07a4 ffff ffff   name=-1, len=0x7a4 (BE), buffer=-1
    021015f5: "maps/mp/mp_nuked.gsc\0"  00001a6e 0000079c 789c...
```

Same struct (name ptr, len, buffer ptr), buffer = {u32 inflated size, u32 compressed size,
zlib}. Only the integers swap. The content differs: PC inflates to 9482 bytes with comments,
tabs and CRLF; PS3 inflates to 6766 bytes with comments and indentation stripped and LF line
ends. Scripts compiled for PC therefore need no binary conversion, but should be minified
(optional, saves memory) and must only call script functions that exist in the PS3 build.

## 2. Mod tools pipeline

1. Radiant (`CoDBORadiant.exe`) saves a text `.map`. `map_source/zombie_test_room.map` starts
   `iwmap 4`, layer lines, then entity 0 `"classname" "worldspawn"` with sun/ambient keys and
   brushes as plane triples with material names (e.g. `caulk`, `lightmap_gray`); 209
   `classname` lines in this map.
2. `cod2map` compiles the `.map` into `raw/maps/<name>.d3dbsp` (BSP, collision, portals) and
   `.d3dprt`; `cod2rad` lights it (lightmaps, light grid; `.grid_auto`, `.radtrans`).
   `zombie_test_room.d3dbsp` header: `49425350 2d000000 1d000000` = "IBSP", version 45,
   29 lumps, then a (lump id, size) table. The mod tools' own `raw/maps/effectsEd_box.d3dbsp`
   is "IBSP" version 44 with 37 lumps (`49425350 2c000000 25000000`), so the borrowed compiler
   writes a different BSP version from the one Treyarch shipped (INFERRED that the linker
   accepts both; it evidently linked zombie_test_room).
3. `zone_source/<name>.csv` lists the zone contents as `type,name` lines. The map line is
   `col_map_sp,maps/zombie_test_room.d3dbsp` (MP maps use `col_map_mp`); plus `include,`,
   `ignore,` (zones assumed loaded already, e.g. `ignore,code_post_gfx`, `ignore,common_zombie`),
   `rawfile,`, `fx,`, `material,`, `xmodel,`, `sound,` etc. 491 CSVs ship in zone_source.
   The linker also understands platform-conditional lines: `include_pc`, `include_ps3`,
   `include_xenon`, `include_wii`, `file_pc`, `file_ps3` (strings at 0x11c05c..0x11d3bc in
   linker_pc.exe).
4. `linker_pc.exe` reads the d3dbsp and expands it into the world assets (col_map, com_map,
   game_map, gfx_map, map_ents), pulls every referenced material, techset, image, model, fx,
   and writes `zone_source/english/assetlist/<name>.csv`, `assetinfo/<name>.csv` (with usage
   in KB and parent stack) and the `.ff` into `usermaps/<name>/` (copied to
   `mods/usermaps/<name>/`). For zombie_test_room assetinfo lists: 1 col_map_sp, 1 com_map,
   1 game_map_sp, 1 gfx_map, 1 map_ents, 55 techset, 215+5 material, 140+13 image,
   26 xmodel, 115 xanim, 118 fx, 24 rawfile, 2 sound, 13 weapon, 1 stringtable.
5. `converter.exe` converts raw source data (GDTs, textures, models) to the game formats the
   linker consumes; images are written as `.iwi` (version 13: an iwi from iw_05.iwd starts
   `49 57 69 0d`, "IWi" v13).

The linker contains only PC savers for the rendering and sound types. Its embedded source
paths (`codsrc_mods\src\database\...`) name `r_bsp_save_pc_db.h`, `r_image_save_pc_db.h`,
`r_material_save_pc_db.h`, `r_xsurface_save_pc_db.h`, `xmodel_save_pc_db.h`,
`snd_bank_save_pc_db.h`, `map_ents_save_pc_db.h`, ... and `_save_xenon_db.h` variants for
clipmap (`cm_brush_related`, `cm_local`), `com_bsp`, `g_bsp`, `pathnode`, `fx_effect`,
`rawfile`, `bg_weapons`, `ui_shared`, `r_font`, `r_water`, `xglobals`. No `_ps3` saver.
So the shipped linker cannot write a PS3 zone, even though it knows the PS3 magic. INFERRED
(weak): types whose saver is the `_xenon` one are laid out the same on PC and console, apart
from endianness; rawfile (above) is consistent with this.

## 3. Asset-by-asset conversion PC -> PS3

Classes: (a) endian swap and type renumbering only; (b) remap by name onto existing PS3 assets;
(c) re-encode payload; (d) cannot convert, must be replaced.

| Asset (PS3 id) | Class | Notes |
|---|---|---|
| rawfile (38) | a | CONFIRMED in 1.4. Swap the two u32 size words. Optionally minify. |
| stringtable (39) | a + generate | Configstrings: emit 12 `configstrings_ps3_<map>_<gt>.csv` tables (PS3 wants one per gametype; PC MP zone has none). Content INFERRED to be the precache list; copy the structure from a stock PS3 map. |
| map_ents (18) | a | Entity string is text. In MP map zones it is not top level (referenced from the clipmap). INFERRED layout identical. |
| col_map_mp (14) / col_map_sp (13) | a (INFERRED) | Planes, brushes, leafs, nodes, cmodels, PVS: floats and ints, swapped field by field. Saver is `_xenon` on PC. Confirm against R2. |
| com_map (15) | a (INFERRED) | Primary lights. `com_bsp` saver is `_xenon`. |
| game_map_mp (17) | a (INFERRED) | Path nodes (`g_bsp`, `pathnode` savers `_xenon`). |
| gfx_map (19) | a + c | The hardest. Struct layout via `r_bsp_save_pc`, i.e. PC-specific. Vertex/index buffers (D3D9, LE) must move to the PS3 vertex layout and the PHYSICAL blocks; lightmaps, reflection probes and outdoor image are images (see image). Surface material pointers stay by name. INFERRED; needs R2's gfx_map layout. |
| lightdef (20) | a + c | Small struct plus an attenuation image. |
| material (6) | a + b | Struct differs (`r_material_save_pc`); constants and state bits likely portable, technique set pointer must name a PS3 techset; texture table entries point at PS3 images. INFERRED. |
| techset (9) | b | PC techsets embed D3D9 HLSL bytecode (2112 "vs_3_0/ps_3_0" strings and "Microsoft (R) HLSL Shader Compiler 9.22.949.2248" in PC mp_nuked). PS3 techsets reference RSX programs (pixelshader 7 / vertexshader 8, PS3 only). Do not convert: substitute the PS3 techset of the same name. Names match: PC and PS3 mp_nuked both list 104 techsets in the same order. Of the 55 techsets the zombie_test_room linker pulled, 52 names occur verbatim in the nine inflated PS3 zones; the 3 missing (`2d`, `effect_falloff_add_eyeoffset`, `effect_falloff_add_nofog_eyeoffset`) are generic and INFERRED to be in code_pre_gfx / common zones not yet inflated. |
| pixelshader (7), vertexshader (8) | d | PC has no equivalent; carried inside PS3 techsets copied from stock zones. |
| image (10) | c | PC: GfxImage in the zone with the full data streamed from `.iwi` in `main/*.iwd` (988 files in iw_01.iwd, e.g. `images/$white.iwi`). PS3: images in per-map `.pak` (`mp_nuked.pak` starts `70616b32` "pak2") plus a texturelist asset. DXT blocks are the same data but D3D9 format codes become GCM formats, and the pitch, mip order and tiling/swizzle (non-DXT formats) must follow R3 (textures.md). |
| texturelist (44) | generate | PS3 only; one per map zone. Contents INFERRED to index the .pak; R3/R1 to confirm. |
| xmodel (5) | a + c | XSurface vertex/index data and the `_pc` saver mean layout work like gfx_map. A box map needs none (use models already in common_mp). |
| xanim (4) | a (INFERRED) | `_pc` saver; not needed for a box map. |
| fx (30), impactfx (31) | a (INFERRED) | `_xenon` saver. Referenced materials must exist on PS3. |
| sound (11), sound_patch (12) | c or d | PC banks (`snd_bank_save_pc`) hold PC-encoded audio; PS3 codec INFERRED to differ. For a first map, reuse an existing PS3 bank or ship an empty one. |
| physpreset, destructibledef, glasses (43) | a (INFERRED) | Small structs; glasses needed (empty) per stock map. |
| weapon, menus, localize, fonts | out of scope for maps; `_xenon` savers suggest (a). |

## 4. Minimum viable box map

### 4.1 What a PS3 MP map zone contains

Smallest MP map on disc by `.ff` size is mp_radiation.ff (35 083 424 bytes). Its PS3 asset
counts: physpreset 5, destructibledef 24, xmodel 437, material 2, techset 131, sound 2,
col_map_mp 1, com_map 1, game_map_mp 1, gfx_map 1, lightdef 1, fx 104, rawfile 24,
stringtable 12, glasses 1, texturelist 1 (748). mp_nuked has the same type set
(xfile.md section 4). Map_ents and images do not appear top level; they are inline under
the world assets and materials.

Minimal set INFERRED for a box map (to be proven by loading it):

- 12 configstring stringtables (39), named `configstrings_ps3_<map>_<gametype>.csv`;
- techsets (9) for the box's surfaces, copied from stock PS3 zones by name;
- materials (6) inline or top level, pointing at those techsets and at images;
- col_map_mp (14) with map_ents inline: one brush model, spawn entities
  (`mp_tdm_spawn`, `mp_dm_spawn`, `mp_global_intermission` etc.), worldspawn;
- com_map (15) with at least the sun light;
- game_map_mp (17) with no path nodes;
- gfx_map (19);
- lightdef (20) if the map uses one;
- rawfiles (38): `maps/mp/<map>.gsc` calling `maps\mp\_load::main()`, its `_fx` and
  `clientscripts/mp/<map>.csc`;
- a sound bank (11) or reference to an existing one;
- glasses (43), texturelist (44);
- a `<map>.pak` if any map-specific image is used (avoidable by using only images already in
  common_mp: R3 to confirm whether materials in a map zone may point at images resident in
  common_mp.pak).

Lowest-risk naming: reuse an existing map name (e.g. ship the box as `mp_radiation.ff`) so
the map tables, loadscreen and menus need no changes; requires the signature-patched client.

### 4.2 What the PC tools supply for a box map

A Radiant box (six caulk/textured brushes, spawns, a light) compiled with cod2map/cod2rad and
linked with `col_map_mp,maps/mp/mp_box.d3dbsp` produces a PC zone with col_map_mp, com_map,
game_map_mp, gfx_map, map_ents, the techsets/materials/images of the textures used, a PC
configstrings table and the listed rawfiles - i.e. every asset in 4.1 except the PS3-only
texturelist and the per-gametype configstrings, already in the correct order and with the
same script strings. Converting it needs: type renumbering, endian swap of every field
(needs per-struct layouts, R2), gfx_map vertex/index re-layout, techset substitution, image
re-encoding (or avoidance), and recomputed block sizes.

## 5. Alternative: synthesise the world assets without the mod tools

Feasible but larger. A box needs a hand-built clipMap_t (planes, 6 brushes, a 1-leaf BSP,
cmodel 0, map_ents text), GfxWorld (cells, one portal-less cell, surfaces with vertex/index
buffers, a lightmap or vertex lighting, sun, light grid, dpvs data sized to the surface
count), ComWorld (sun) and GameWorldMp (empty). Every counter cross-references others (dpvs
sizes, cell/surface indices, lightgrid), so this is only practical with complete PS3 struct
layouts (R2) and a stock PS3 map as a template whose arrays are cut down. Advantages: no
Windows tools, no PC-only structs, output is native PS3 from the start. Risk: the BSP,
collision and lighting must be internally consistent, which the PC compiler otherwise does.

## 6. Recommendation

1. Build the PS3 struct writer first (needed by either route), proven by round-tripping stock
   PS3 map zones.
2. For the box map, use a hybrid: compile the box with the PC tools on this machine, parse the
   PC zone (LE, unsigned, plain zlib - trivial to read), and convert the world assets
   field by field into PS3 structs; substitute techsets by name from stock PS3 zones; use only
   textures already present on PS3 so no image re-encoding or `.pak` is needed at first;
   copy configstrings, sound, glasses and texturelist from the replaced stock map.
   OpenT5 is GPL-3.0-or-later, so OpenAssetTools (GPLv3) code may be reused with its headers
   intact and an entry in docs/provenance.md. Its T5 support is PC-only, which is exactly the
   reading side of this route: the T5 struct definitions (src/Common/Game/T5/T5_Assets.h), the
   zone loader (src/ZoneLoading/Game/T5/) and the per-asset dumpers can supply the PC field
   lists for every type in section 3, leaving OpenT5 to supply the PS3 differences (enum shift,
   endianness, PS3-only types, gfx_map/xmodel buffer layout) and the PS3 writer. The local
   checkout at ~/oat is not built; either port the needed structs to Python or build its
   Unlinker to dump a PC map zone to raw assets as a reference.
3. Keep the "synthesise from a description" route as a later tool once gfx_map and clipmap
   layouts are fully known; the PC output of the same box is then a reference to test against.

## Sources

- Local files listed in section 0 (offsets and bytes quoted inline).
- OpenAssetTools: src/ZoneCommon/Game/T5/ZoneConstantsT5.h; src/ZoneLoading/Game/T5/ZoneLoaderFactoryT5.cpp; src/Common/Game/T5/T5.h (XAssetType); src/Common/Game/T5/T5_Assets.h (XFileBlock).
- Not yet checked: DLC map zones (will appear under RPCS3 dev_hdd0 later); repeat the
  section 1.3 comparison on them when available.
- LinkerMod project wiki, page "LinkerMod" (cod2map/cod2rad/Radiant taken from World at War mod tools).
- Mod tools `ReadMe.txt` in the app 42740 install (Launcher, Converter).
- https://wiki.modme.co/wiki/Game-Support-_-Black-Ops-1.html (custom maps via Game_Mod / LinkerMod).
