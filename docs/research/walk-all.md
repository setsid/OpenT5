# Walking every zone (Phase 2, M2)

Status: all 178 zones parse exactly (disc 50, update 16, HDD copies of the update 16 + 16
French, DLC 1 to 5 in English and French 72). Every break found outside the nine-zone sample is
fixed from the loader in `t5mp.elf`; each fix was confirmed by running the game's own loader
code over the failing asset and comparing every stream read.

Owner: Phase 2 core track. Code: `src/opent5/xfile/` (parser and handlers), `tools/walk_all.py`
(this walk). Companion documents: `xfile.md` (stream framing), `structs-content.md`,
`structs-map.md` (per-type layouts).

## 1. What "parses" means

`tools/walk_all.py` inflates each zone (`opent5.container.zone.Zone`), parses it with
`opent5.xfile.parse`, and counts the zone as passing only when all of these hold:

1. every asset parses through its type's handler (no read past the stream end, no unknown
   type, no unexpected inline marker in a converted-only pointer field);
2. the walk consumes the stream exactly: the asset data ends where the deferred tail starts,
   and the deferred LARGE_RUNTIME / PHYSICAL_RUNTIME reads end at `len(content)`;
3. every block ends at the size the XFile header declares: RUNTIME, LARGE_RUNTIME,
   PHYSICAL_RUNTIME, VIRTUAL, LARGE and PHYSICAL at their final positions, TEMP at its
   high-water mark plus 16 (section 4.1).

Each zone runs in its own process with a wall-clock timeout (default 900 s; none came near it).
Failures report the zone, asset index, type, the first strings read in the asset (its name is
usually the first), the asset's file offset, the handler path that broke, and the message with
expected and found values.

    .venv/bin/python tools/walk_all.py --jobs 4 --json walk.json --doc docs/research/walk-all.md

## 2. Method for each break

1. Inflate the failing zone to scratch and parse it; the AssetError gives the asset, its file
   offset and the seven block positions at its start.
2. Run the game's loader for that one asset: a scratch PowerPC interpreter (the one described
   in `structs-content.md` section 1, local CPU emulation only) loads `t5mp.elf`, sets the
   seven block positions and the file position to the values our parser had at the asset's
   start, pushes VIRTUAL and calls `Load_XAsset` (0x256e60) as the loop at 0x233ba4 does.
3. Compare the game's Load_Stream / string reads (block, size, file offset, return address)
   with our handler's READ / STRING / DEFER events and stop at the first difference; the
   return address names the load site, whose disassembly gives the field, count and alignment.
4. Fix the handler, re-run the comparison until the reads are identical, then re-walk all
   zones.

## 3. Breaks found outside the nine-zone sample

First full walk: 144 of 178 passed. All 34 failures were in GfxWorld (gfx_map, 19), in fields
that are zero or absent in mp_nuked, mp_firingrange and zombie_theater. Second walk, after rows 1 to
4 and 7: 159 of 178. Third walk, after rows 5 and 6: 178 of 178.

| # | Field (GfxWorld offset) | Symptom | Cause | Fix and ELF evidence | Zones that use it |
|---|---|---|---|---|---|
| 1 | worldLodChains +0x400 (count +0x3fc), worldLodInfos +0x408 (+0x404), worldLodSurfaces +0x410 (+0x40c) | stopped by the "layout unknown" guard: +0x3fc = 1 | three [nz] arrays not loaded | align 4, LS 24 / 12 / 4 x count, in that order, after dpvsDyn (0x251dc4 .. 0x251e58: `lwz r25,1024`, size `(n<<5)-(n<<3)`; `lwz 1032`, `(n<<4)-(n<<2)`; `lwz 1040`, `n<<2`). mp_cosmodrome #577 at 0x1289395, +0x3fc at 0x1289791: `00000001 ffffffff 00000002 ffffffff 00000008 ffffffff` | creek_1, mp_cosmodrome, pentagon, rebirth, zombie_temple (en, fr) |
| 2 | waterBuffers[2] at +0x418, +0x420 (`{u32 bufferSize; ptr buffer}`) | (none in data) | not loaded | each [nz]: align 4, LS `bufferSize & ~15` (0x251edc .. 0x251f78, `rlwinm r26,r31,0,0,27`) | no zone |
| 3 | outdoorBounds +0x440 (count +0x43c) | guard: +0x43c = 1 | [nz] array not loaded | align 4, LS 24 x count, after the occluders (0x252010 .. 0x252054). mp_mountain #495, +0x43c at 0x1165c9b: `00000001 ffffffff` | mp_mountain, mp_radiation, mp_russianbase, mp_discovery, mp_kowloon, mp_outskirts, mp_area51, mp_drivein, mp_silo (15 zones with French) |
| 4 | heroLights +0x44c (count +0x444), heroLightTree +0x450 (count +0x448) | guard: +0x444 = 3 | two [nz] arrays not loaded | align 4, LS 56 x +0x444 (0x25205c .. 0x2520a4, `(n<<6)-(n<<3)`), then align 4, LS 24 x +0x448 (0x2520ac .. 0x2520f4). cuba #1040, +0x43c at 0x2963f5d: `00000000 00000000 00000003 00000003 ffffffff ffffffff` | cuba, fullahead, hue_city, rebirth, vorkuta |
| 5 | GfxReflectionProbe.probeVolumes (+0x10 of each 24-byte probe, count +0x14) | an image header read from the wrong place: "Load_Stream of 0x441b8000 bytes" inside gfx_map/image | [nz] array per probe not loaded | after each probe's image ref: align 4, LS 96 x count (0x24b0a8 .. 0x24b0e4, `(n<<7)-(n<<5)`; loop 0x24b070 .. 0x24b168). mp_outskirts (dlc3) #596: first difference at read 989, game LS 0x1e0 in VIRTUAL at return address 0x24b168; probe 2 = `43ba8000 42ae0000 43918000 ffffffff ffffffff 00000005` (5 volumes = 480 bytes) | cuba, flashpoint, int_escape, pentagon, pow, rebirth, vorkuta, zombie_pentagon, zombie_cod5_prototype (dlc1 en, fr), mp_outskirts (dlc3 en, fr), zombie_temple (dlc4 en, fr) |
| 6 | sceneDynBrush +0x340 (RUNTIME) | walk ends with RUNTIME 0x80 past blockSize[1] | size was INFERRED as 8 x dynEntClientCount[1] | 4 x dynEntClientCount[1] (+0x3d8): 0x251830 .. 0x25186c, `lwz r9,984` then `rlwinm r6,r9,2,0,29`. mp_duga #583: game reads 8 bytes, we read 16 (dynEntClientCount[1] = 2 at 0x122eb80); the difference crosses a later 128-byte alignment | with 8 x: mp_duga, river, zombietron, pow, cuba, mp_area51 (en, fr) end RUNTIME 0x80 long; fullahead, hue_city, kowloon, rebirth, underwaterbase, wmd, mp_stadium (en, fr) also have a non-zero count but a later 128-byte RUNTIME alignment absorbs the error, so only the ELF settles them |
| 7 | cullGroups +0x3b8 (count +0x118, cullGroupCount) | (none in data) | the walker refused a non-zero value | [nz]: align 4, LS 32 x +0x118, between dpvs.surfaces and smodelDrawInsts (0x250c64 .. 0x250ca8) | no zone |

After the fixes, the game's loader and the handler produce identical read sequences for the
gfx_map assets of mp_cosmodrome (3 774 reads), mp_outskirts (5 011) and mp_duga (3 232), with
equal final file offsets and block positions. Rows 2 and 7 are taken from the ELF alone; no zone
exercises them (INFERRED until one does).

The SP zones (common, code_post_gfx, frontend, the campaign levels) and the zombie zones are
loaded on the console by `t5.elf`, not `t5mp.elf`. All of them walk exactly with the MP loader's layouts
(stream end and all seven block sizes), which is strong evidence that the two executables share
every struct layout these zones use. The SP loader itself was not traced (open item).

## 4. Other findings

### 4.1 TEMP block size

blockSize[0] (TEMP) is exactly the TEMP high-water mark plus 16 in all 178 zones (the game's
loader, emulated over the nine sample zones, reaches the same high-water mark as the parser:
0xa88 in patch_mp, header 0xa98). TEMP rewinds after every asset, so its final position is 0.
INFERRED: the zone writer counts the 16-byte XAssetList in TEMP, while the PS3 loader reads it
into a global (0x233b04) instead. `xfile.md` section 3 said "its high-water mark is
blockSize[0]"; that is 16 short.

### 4.2 Pointer fields the loader converts without loading

The event log records every pointer field the loader looks at. Comparing its offset and alias
pointers with every DB_ConvertOffsetToPointer / DB_ConvertOffsetToAlias call the game made, per
asset, over the six zones traced event by event (mp_nuked, zombie_theater, common_mp,
code_post_gfx_mp, patch_mp, ui_mp; 3 063 assets) found conversions in fields that never cause
a stream read, so a stream-only walker misses them. They are now logged (``XStream.convert``),
and the multisets of converted values now match for every traced asset:

| Struct | Field | Kind | Count in mp_nuked |
|---|---|---|---|
| clipMap staticModelList (0x50) | +0x4 XModel | alias | 1 385 |
| clipMap brushsides (0xc) | +0x0 plane | offset | 22 691 |
| clipMap nodes (8) | +0x0 plane | offset | 2 779 |
| clipMap partitions (0x14) | +0x10 borders | offset | 1 829 |
| clipMap brushes (0x60) | +0x20 sides, +0x58 verts | offset | 5 131, 5 890 |
| PathData nodeTree (0x10), interior nodes | +0x8, +0xc children | offset | 113, 113 |
| GfxCell portals (0x44) | +0x20 cell | offset | 144 |
| BrushWrapper (XModel collmaps, 0x60) | +0x5c | offset | 1 |

A -1 in any of these stops the parse (layout of inline data unknown); none of the 178 zones has
one.

### 4.3 Tier-1 write side on every zone

Parse, then write back with the handler's ``write``: all 32 760 rawfile, stringtable and
localize assets of the 178 zones come out byte-identical to their file spans (scratch check;
the test suite does the same on the nine sample zones).

### 4.4 Types that never occur as a top-level asset

Over all 178 zones, xmodelpieces (0), pixelshader (7), vertexshader (8), map_ents (18) and menu
(24) never appear in an asset list. Shaders, map_ents and menus occur inline (inside techsets,
clipMaps and menu lists) and are exercised there; xmodelpieces occurs nowhere, so its handler
(written from the ELF: the switch case at 0x2567d0 has no TEMP push and no -2 path, struct
loader 0x24e430) is unexercised. xGlobals (14 instances) and texturelist (46) now have data; their
layouts hold.

## 5. Results

<!-- WALK START -->
178 of 178 zones pass; 125327 assets, 7358 MB of stream; wall 97.1 s (inflate 297.6 s and parse 80.5 s summed over workers).

| Group | Zones | Pass | Assets | Stream MB | Parse s |
|---|---|---|---|---|---|
| disc | 50 | 50 | 81278 | 4142 | 40.4 |
| update | 16 | 16 | 2756 | 31 | 1.3 |
| dlc1-english | 8 | 8 | 4018 | 327 | 2.7 |
| dlc1-french | 8 | 8 | 4018 | 327 | 3.0 |
| dlc2-english | 10 | 10 | 4116 | 372 | 5.0 |
| dlc2-french | 10 | 10 | 4116 | 372 | 5.4 |
| dlc3-english | 10 | 10 | 4094 | 383 | 5.3 |
| dlc3-french | 10 | 10 | 4094 | 382 | 4.1 |
| dlc4-english | 10 | 10 | 4400 | 384 | 4.3 |
| dlc4-french | 10 | 10 | 4400 | 383 | 4.2 |
| dlc5-english | 2 | 2 | 1263 | 96 | 0.9 |
| dlc5-french | 2 | 2 | 1263 | 96 | 0.9 |
| hdd-english | 16 | 16 | 2756 | 31 | 1.5 |
| hdd-french | 16 | 16 | 2755 | 31 | 1.5 |

| Type | Assets (all zones) |
|---|---|
| xmodel | 30488 |
| xanim | 27801 |
| localize | 24955 |
| fx | 12924 |
| techset | 12589 |
| rawfile | 7168 |
| weapon | 3493 |
| material | 2962 |
| stringtable | 637 |
| destructibledef | 551 |
| menufile | 402 |
| physpreset | 299 |
| sound | 158 |
| image | 149 |
| lightdef | 137 |
| packindex | 110 |
| com_map | 76 |
| gfx_map | 76 |
| texturelist | 46 |
| glasses | 45 |
| game_map_mp | 39 |
| col_map_mp | 39 |
| game_map_sp | 37 |
| col_map_sp | 37 |
| ddl | 30 |
| impactfx | 26 |
| font | 19 |
| xGlobals | 14 |
| physconstraints | 8 |
| sound_patch | 6 |
| emblemset | 4 |
| snddriverglobals | 2 |

| Group | Zone | Result | Stream bytes | Assets | Script strings | Tail bytes | Events | Parse s |
|---|---|---|---|---|---|---|---|---|
| disc | code_post_gfx | pass | 8923153 | 6899 | 154 | 0 | 202232 | 0.43 |
| disc | code_post_gfx_mp | pass | 10203395 | 8576 | 116 | 0 | 281066 | 0.64 |
| disc | common | pass | 31263986 | 1689 | 274 | 0 | 112583 | 0.19 |
| disc | common_mp | pass | 95670787 | 7286 | 1145 | 0 | 850506 | 1.33 |
| disc | common_zombie | pass | 84619615 | 3357 | 858 | 0 | 348972 | 0.57 |
| disc | creek_1 | pass | 148259845 | 2998 | 2020 | 20453760 | 627909 | 1.14 |
| disc | cuba | pass | 164436029 | 2435 | 2314 | 21626880 | 860910 | 2.33 |
| disc | dev | pass | 432481 | 70 | 4 | 0 | 2751 | 0.01 |
| disc | dev_mp | pass | 115307 | 52 | 0 | 0 | 882 | 0.0 |
| disc | flashpoint | pass | 154606636 | 2539 | 1959 | 20621696 | 658832 | 1.32 |
| disc | frontend | pass | 63420557 | 1320 | 323 | 19316736 | 257552 | 0.63 |
| disc | fullahead | pass | 147140736 | 2145 | 1578 | 17293184 | 609181 | 1.12 |
| disc | hue_city | pass | 153563925 | 2321 | 2338 | 21736832 | 817747 | 1.44 |
| disc | int_escape | pass | 140020517 | 1104 | 913 | 17250560 | 328400 | 0.69 |
| disc | khe_sanh | pass | 155714887 | 2397 | 1498 | 21158784 | 626262 | 1.25 |
| disc | kowloon | pass | 156996283 | 2433 | 1742 | 35476736 | 692729 | 1.62 |
| disc | mp_array | pass | 70788664 | 766 | 806 | 8750976 | 433648 | 0.94 |
| disc | mp_cairo | pass | 70354671 | 655 | 897 | 8089600 | 494898 | 1.0 |
| disc | mp_cosmodrome | pass | 71379022 | 765 | 775 | 8812800 | 455295 | 1.04 |
| disc | mp_cracked | pass | 72984741 | 758 | 739 | 8646656 | 497982 | 1.03 |
| disc | mp_crisis | pass | 70532391 | 642 | 621 | 8121728 | 426133 | 0.91 |
| disc | mp_duga | pass | 68809151 | 728 | 676 | 8310400 | 426399 | 0.83 |
| disc | mp_firingrange | pass | 64877384 | 532 | 609 | 8800640 | 394582 | 0.78 |
| disc | mp_hanoi | pass | 71240645 | 682 | 669 | 8130688 | 495831 | 0.94 |
| disc | mp_havoc | pass | 63856189 | 516 | 504 | 8957568 | 342848 | 0.66 |
| disc | mp_mountain | pass | 67431037 | 630 | 775 | 11621504 | 379669 | 0.78 |
| disc | mp_nuked | pass | 68848457 | 529 | 516 | 18209024 | 340198 | 0.65 |
| disc | mp_radiation | pass | 69662122 | 748 | 734 | 10923776 | 424549 | 0.84 |
| disc | mp_russianbase | pass | 69780069 | 662 | 715 | 9858560 | 383455 | 0.83 |
| disc | mp_villa | pass | 68969993 | 498 | 534 | 10796288 | 446345 | 0.86 |
| disc | outro | pass | 10367865 | 189 | 1 | 3408640 | 66475 | 0.14 |
| disc | pentagon | pass | 152174983 | 1027 | 769 | 34227712 | 395621 | 0.88 |
| disc | pow | pass | 153372498 | 2599 | 1589 | 18785792 | 598942 | 1.05 |
| disc | rebirth | pass | 159063451 | 2355 | 1864 | 24815232 | 811330 | 1.45 |
| disc | river | pass | 157552077 | 2559 | 1801 | 19050624 | 610389 | 1.21 |
| disc | so_narrative1_frontend | pass | 2315971 | 33 | 161 | 8320 | 2813 | 0.01 |
| disc | so_narrative2_frontend | pass | 3144177 | 32 | 161 | 8320 | 2799 | 0.01 |
| disc | so_narrative3_frontend | pass | 4542837 | 35 | 161 | 8320 | 2841 | 0.01 |
| disc | so_narrative4_frontend | pass | 2295243 | 33 | 161 | 8320 | 2813 | 0.01 |
| disc | so_narrative5_frontend | pass | 1516749 | 32 | 161 | 8320 | 2799 | 0.01 |
| disc | terminal | pass | 4316737 | 294 | 0 | 3973120 | 16159 | 0.03 |
| disc | ui_mp | pass | 37425473 | 670 | 0 | 0 | 848040 | 1.66 |
| disc | ui_viewer_mp | pass | 40249871 | 194 | 160 | 3276416 | 68346 | 0.13 |
| disc | underwaterbase | pass | 152216037 | 2379 | 1996 | 17734400 | 674051 | 1.22 |
| disc | vorkuta | pass | 160398181 | 2452 | 1836 | 27011200 | 708123 | 1.31 |
| disc | wmd | pass | 152013660 | 2877 | 2132 | 19504256 | 664947 | 1.2 |
| disc | wmd_sr71 | pass | 132832502 | 2022 | 1466 | 29676032 | 480322 | 0.89 |
| disc | zombie_pentagon | pass | 90524512 | 1057 | 861 | 18168704 | 452126 | 0.67 |
| disc | zombie_theater | pass | 101799224 | 917 | 790 | 26767616 | 437558 | 0.82 |
| disc | zombietron | pass | 139307622 | 2790 | 1241 | 29232896 | 522222 | 0.93 |
| update | common_zombie_patch | pass | 777344 | 137 | 163 | 0 | 5338 | 0.01 |
| update | patch | pass | 695479 | 426 | 1 | 18560 | 48603 | 0.07 |
| update | patch_mp | pass | 3715811 | 1337 | 212 | 468352 | 173415 | 0.24 |
| update | patch_ui | pass | 4513012 | 31 | 0 | 2622592 | 142739 | 0.2 |
| update | patch_ui_mp | pass | 13611499 | 130 | 0 | 6619136 | 413377 | 0.68 |
| update | zombie_coast_patch | pass | 858910 | 103 | 35 | 526336 | 8215 | 0.01 |
| update | zombie_cod5_asylum_patch | pass | 857642 | 21 | 19 | 526464 | 5016 | 0.01 |
| update | zombie_cod5_factory_patch | pass | 853697 | 21 | 19 | 526464 | 5016 | 0.01 |
| update | zombie_cod5_prototype_patch | pass | 853968 | 21 | 19 | 526464 | 5016 | 0.01 |
| update | zombie_cod5_sumpf_patch | pass | 861430 | 25 | 19 | 526464 | 5072 | 0.01 |
| update | zombie_cosmodrome_patch | pass | 919212 | 131 | 35 | 526464 | 10385 | 0.02 |
| update | zombie_moon_patch | pass | 46183 | 16 | 0 | 0 | 232 | 0.0 |
| update | zombie_pentagon_patch | pass | 843849 | 98 | 35 | 526336 | 8145 | 0.01 |
| update | zombie_temple_patch | pass | 856764 | 101 | 35 | 526336 | 8187 | 0.01 |
| update | zombie_theater_patch | pass | 847582 | 146 | 35 | 526336 | 10272 | 0.01 |
| update | zombietron_patch | pass | 87077 | 12 | 0 | 0 | 176 | 0.0 |
| dlc1-english | zombie_cod5_asylum | pass | 74926081 | 1028 | 584 | 17378304 | 334604 | 0.57 |
| dlc1-english | zombie_cod5_asylum_load | pass | 1765 | 8 | 0 | 0 | 276 | 0.0 |
| dlc1-english | zombie_cod5_factory | pass | 99092427 | 1064 | 594 | 16728064 | 408818 | 0.76 |
| dlc1-english | zombie_cod5_factory_load | pass | 1766 | 8 | 0 | 0 | 276 | 0.0 |
| dlc1-english | zombie_cod5_prototype | pass | 58274233 | 903 | 570 | 12063488 | 248756 | 0.42 |
| dlc1-english | zombie_cod5_prototype_load | pass | 1768 | 8 | 0 | 0 | 276 | 0.0 |
| dlc1-english | zombie_cod5_sumpf | pass | 94279903 | 991 | 562 | 12810368 | 374230 | 0.93 |
| dlc1-english | zombie_cod5_sumpf_load | pass | 1764 | 8 | 0 | 0 | 276 | 0.0 |
| dlc1-french | zombie_cod5_asylum | pass | 74994651 | 1028 | 584 | 17378304 | 333859 | 0.73 |
| dlc1-french | zombie_cod5_asylum_load | pass | 1765 | 8 | 0 | 0 | 276 | 0.0 |
| dlc1-french | zombie_cod5_factory | pass | 99283601 | 1064 | 594 | 16728064 | 408057 | 0.86 |
| dlc1-french | zombie_cod5_factory_load | pass | 1766 | 8 | 0 | 0 | 276 | 0.0 |
| dlc1-french | zombie_cod5_prototype | pass | 58259287 | 903 | 570 | 12063488 | 241391 | 0.52 |
| dlc1-french | zombie_cod5_prototype_load | pass | 1768 | 8 | 0 | 0 | 276 | 0.0 |
| dlc1-french | zombie_cod5_sumpf | pass | 94284200 | 991 | 562 | 12810368 | 366865 | 0.86 |
| dlc1-french | zombie_cod5_sumpf_load | pass | 1764 | 8 | 0 | 0 | 276 | 0.0 |
| dlc2-english | mp_berlinwall2 | pass | 69729167 | 658 | 649 | 8221312 | 525978 | 1.04 |
| dlc2-english | mp_berlinwall2_load | pass | 2507 | 9 | 0 | 0 | 345 | 0.0 |
| dlc2-english | mp_discovery | pass | 70146611 | 680 | 934 | 8218624 | 432763 | 0.91 |
| dlc2-english | mp_discovery_load | pass | 2505 | 9 | 0 | 0 | 345 | 0.0 |
| dlc2-english | mp_kowloon | pass | 67714603 | 753 | 790 | 10549888 | 442326 | 0.93 |
| dlc2-english | mp_kowloon_load | pass | 2503 | 9 | 0 | 0 | 345 | 0.0 |
| dlc2-english | mp_stadium | pass | 69412176 | 818 | 612 | 12517760 | 461725 | 0.96 |
| dlc2-english | mp_stadium_load | pass | 2503 | 9 | 0 | 0 | 345 | 0.0 |
| dlc2-english | zombie_cosmodrome | pass | 95166328 | 1162 | 775 | 12728448 | 515899 | 1.19 |
| dlc2-english | zombie_cosmodrome_load | pass | 2510 | 9 | 0 | 0 | 345 | 0.0 |
| dlc2-french | mp_berlinwall2 | pass | 69625663 | 658 | 649 | 8221312 | 517786 | 1.12 |
| dlc2-french | mp_berlinwall2_load | pass | 2507 | 9 | 0 | 0 | 345 | 0.0 |
| dlc2-french | mp_discovery | pass | 70043107 | 680 | 934 | 8218624 | 424571 | 0.92 |
| dlc2-french | mp_discovery_load | pass | 2505 | 9 | 0 | 0 | 345 | 0.0 |
| dlc2-french | mp_kowloon | pass | 67611099 | 753 | 790 | 10549888 | 434134 | 0.98 |
| dlc2-french | mp_kowloon_load | pass | 2503 | 9 | 0 | 0 | 345 | 0.0 |
| dlc2-french | mp_stadium | pass | 69308672 | 818 | 612 | 12517760 | 453533 | 1.06 |
| dlc2-french | mp_stadium_load | pass | 2503 | 9 | 0 | 0 | 345 | 0.0 |
| dlc2-french | zombie_cosmodrome | pass | 95157576 | 1162 | 775 | 12728448 | 515291 | 1.34 |
| dlc2-french | zombie_cosmodrome_load | pass | 2510 | 9 | 0 | 0 | 345 | 0.0 |
| dlc3-english | mp_gridlock | pass | 71408718 | 716 | 708 | 10408832 | 504423 | 1.15 |
| dlc3-english | mp_gridlock_load | pass | 2466 | 9 | 0 | 0 | 343 | 0.0 |
| dlc3-english | mp_hotel | pass | 70929445 | 766 | 745 | 9577472 | 474520 | 1.1 |
| dlc3-english | mp_hotel_load | pass | 2463 | 9 | 0 | 0 | 343 | 0.0 |
| dlc3-english | mp_outskirts | pass | 71612596 | 701 | 816 | 9481088 | 470921 | 1.13 |
| dlc3-english | mp_outskirts_load | pass | 2467 | 9 | 0 | 0 | 343 | 0.0 |
| dlc3-english | mp_zoo | pass | 73466035 | 679 | 727 | 11773696 | 472359 | 1.05 |
| dlc3-english | mp_zoo_load | pass | 2461 | 9 | 0 | 0 | 343 | 0.0 |
| dlc3-english | zombie_coast | pass | 95236212 | 1187 | 732 | 19293312 | 482543 | 0.86 |
| dlc3-english | zombie_coast_load | pass | 1919 | 9 | 0 | 0 | 305 | 0.0 |
| dlc3-french | mp_gridlock | pass | 71305214 | 716 | 708 | 10408832 | 496231 | 0.96 |
| dlc3-french | mp_gridlock_load | pass | 2466 | 9 | 0 | 0 | 343 | 0.0 |
| dlc3-french | mp_hotel | pass | 70823931 | 766 | 745 | 9577472 | 466229 | 0.86 |
| dlc3-french | mp_hotel_load | pass | 2463 | 9 | 0 | 0 | 343 | 0.0 |
| dlc3-french | mp_outskirts | pass | 71509092 | 701 | 816 | 9481088 | 462729 | 0.82 |
| dlc3-french | mp_outskirts_load | pass | 2467 | 9 | 0 | 0 | 343 | 0.0 |
| dlc3-french | mp_zoo | pass | 73362531 | 679 | 727 | 11773696 | 464167 | 0.78 |
| dlc3-french | mp_zoo_load | pass | 2461 | 9 | 0 | 0 | 343 | 0.0 |
| dlc3-french | zombie_coast | pass | 95210071 | 1187 | 732 | 19293312 | 480567 | 0.71 |
| dlc3-french | zombie_coast_load | pass | 1919 | 9 | 0 | 0 | 305 | 0.0 |
| dlc4-english | mp_area51 | pass | 71002540 | 803 | 891 | 8764928 | 491550 | 0.83 |
| dlc4-english | mp_area51_load | pass | 2458 | 9 | 0 | 0 | 341 | 0.0 |
| dlc4-english | mp_drivein | pass | 70466225 | 738 | 724 | 11199232 | 463611 | 0.79 |
| dlc4-english | mp_drivein_load | pass | 2459 | 9 | 0 | 0 | 341 | 0.0 |
| dlc4-english | mp_golfcourse | pass | 71375376 | 711 | 550 | 10860544 | 439982 | 0.97 |
| dlc4-english | mp_golfcourse_load | pass | 2462 | 9 | 0 | 0 | 341 | 0.0 |
| dlc4-english | mp_silo | pass | 72819251 | 748 | 651 | 8448640 | 457681 | 0.84 |
| dlc4-english | mp_silo_load | pass | 2456 | 9 | 0 | 0 | 341 | 0.0 |
| dlc4-english | zombie_temple | pass | 98565853 | 1355 | 723 | 17109632 | 484874 | 0.89 |
| dlc4-english | zombie_temple_load | pass | 2462 | 9 | 0 | 0 | 341 | 0.0 |
| dlc4-french | mp_area51 | pass | 70899036 | 803 | 891 | 8764928 | 483358 | 0.92 |
| dlc4-french | mp_area51_load | pass | 2458 | 9 | 0 | 0 | 341 | 0.0 |
| dlc4-french | mp_drivein | pass | 70362721 | 738 | 724 | 11199232 | 455419 | 0.85 |
| dlc4-french | mp_drivein_load | pass | 2459 | 9 | 0 | 0 | 341 | 0.0 |
| dlc4-french | mp_golfcourse | pass | 71269862 | 711 | 550 | 10860544 | 431691 | 0.74 |
| dlc4-french | mp_golfcourse_load | pass | 2462 | 9 | 0 | 0 | 341 | 0.0 |
| dlc4-french | mp_silo | pass | 72715747 | 748 | 651 | 8448640 | 449489 | 0.83 |
| dlc4-french | mp_silo_load | pass | 2456 | 9 | 0 | 0 | 341 | 0.01 |
| dlc4-french | zombie_temple | pass | 98163925 | 1355 | 723 | 17109632 | 484038 | 0.86 |
| dlc4-french | zombie_temple_load | pass | 2462 | 9 | 0 | 0 | 341 | 0.0 |
| dlc5-english | zombie_moon | pass | 95989814 | 1258 | 699 | 16988160 | 533163 | 0.86 |
| dlc5-english | zombie_moon_load | pass | 1299 | 5 | 0 | 0 | 201 | 0.0 |
| dlc5-french | zombie_moon | pass | 95966628 | 1258 | 699 | 16988160 | 532037 | 0.87 |
| dlc5-french | zombie_moon_load | pass | 1299 | 5 | 0 | 0 | 201 | 0.0 |
| hdd-english | common_zombie_patch | pass | 777344 | 137 | 163 | 0 | 5338 | 0.01 |
| hdd-english | patch | pass | 695479 | 426 | 1 | 18560 | 48603 | 0.08 |
| hdd-english | patch_mp | pass | 3715811 | 1337 | 212 | 468352 | 173415 | 0.28 |
| hdd-english | patch_ui | pass | 4513012 | 31 | 0 | 2622592 | 142739 | 0.25 |
| hdd-english | patch_ui_mp | pass | 13611499 | 130 | 0 | 6619136 | 413377 | 0.74 |
| hdd-english | zombie_coast_patch | pass | 858910 | 103 | 35 | 526336 | 8215 | 0.01 |
| hdd-english | zombie_cod5_asylum_patch | pass | 857642 | 21 | 19 | 526464 | 5016 | 0.01 |
| hdd-english | zombie_cod5_factory_patch | pass | 853697 | 21 | 19 | 526464 | 5016 | 0.01 |
| hdd-english | zombie_cod5_prototype_patch | pass | 853968 | 21 | 19 | 526464 | 5016 | 0.01 |
| hdd-english | zombie_cod5_sumpf_patch | pass | 861430 | 25 | 19 | 526464 | 5072 | 0.01 |
| hdd-english | zombie_cosmodrome_patch | pass | 919212 | 131 | 35 | 526464 | 10385 | 0.02 |
| hdd-english | zombie_moon_patch | pass | 46183 | 16 | 0 | 0 | 232 | 0.0 |
| hdd-english | zombie_pentagon_patch | pass | 843849 | 98 | 35 | 526336 | 8145 | 0.01 |
| hdd-english | zombie_temple_patch | pass | 856764 | 101 | 35 | 526336 | 8187 | 0.01 |
| hdd-english | zombie_theater_patch | pass | 847582 | 146 | 35 | 526336 | 10272 | 0.01 |
| hdd-english | zombietron_patch | pass | 87077 | 12 | 0 | 0 | 176 | 0.0 |
| hdd-french | common_zombie_patch | pass | 777590 | 137 | 163 | 0 | 5338 | 0.01 |
| hdd-french | patch | pass | 697330 | 426 | 1 | 18560 | 48603 | 0.08 |
| hdd-french | patch_mp | pass | 3717638 | 1337 | 212 | 468352 | 173415 | 0.29 |
| hdd-french | patch_ui | pass | 4513012 | 31 | 0 | 2622592 | 142739 | 0.25 |
| hdd-french | patch_ui_mp | pass | 13611506 | 130 | 0 | 6619136 | 413377 | 0.75 |
| hdd-french | zombie_coast_patch | pass | 858910 | 103 | 35 | 526336 | 8215 | 0.02 |
| hdd-french | zombie_cod5_asylum_patch | pass | 857642 | 21 | 19 | 526464 | 5016 | 0.01 |
| hdd-french | zombie_cod5_factory_patch | pass | 853697 | 21 | 19 | 526464 | 5016 | 0.01 |
| hdd-french | zombie_cod5_prototype_patch | pass | 853968 | 21 | 19 | 526464 | 5016 | 0.01 |
| hdd-french | zombie_cod5_sumpf_patch | pass | 861430 | 25 | 19 | 526464 | 5072 | 0.01 |
| hdd-french | zombie_cosmodrome_patch | pass | 919212 | 131 | 35 | 526464 | 10385 | 0.02 |
| hdd-french | zombie_moon_patch | pass | 46178 | 15 | 0 | 0 | 218 | 0.0 |
| hdd-french | zombie_pentagon_patch | pass | 843849 | 98 | 35 | 526336 | 8145 | 0.01 |
| hdd-french | zombie_temple_patch | pass | 856764 | 101 | 35 | 526336 | 8187 | 0.01 |
| hdd-french | zombie_theater_patch | pass | 847582 | 146 | 35 | 526336 | 10272 | 0.02 |
| hdd-french | zombietron_patch | pass | 87077 | 12 | 0 | 0 | 176 | 0.0 |
<!-- WALK END -->

## 6. Open items

- xmodelpieces (0): no instance in any zone; the handler follows the ELF only. A zone holding
  one would confirm it.
- waterBuffers and cullGroups (GfxWorld, rows 2 and 7): from the ELF only, no zone uses them.
- SP executable: the SP and zombie zones were checked by exact walks with the MP loader's
  layouts, not by tracing `t5.elf`. Tracing one SP gfx_map and one SP weapon in `t5.elf` would
  close it.
- TEMP +16 (section 4.1): the reason is INFERRED. Reading the zone writer's block accounting
  (not available) or finding where the loader sizes TEMP would confirm it.
- Field names in the new GfxWorld rows follow the PC member order (OpenAssetTools,
  src/Common/Game/T5/T5_Assets.h `GfxWorld`, `GfxWaterBuffer`, `GfxReflectionProbe`); element
  sizes and load order are from the PS3 ELF and the data.
