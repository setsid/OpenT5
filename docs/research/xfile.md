# R1: XFile layout, blocks, pointer markers and asset types (T5 PS3, BLES01031)

> **Erratum (from the 178-zone walk, `walk-all.md`).** The header's TEMP block size is the TEMP
> high-water mark **plus 16** in all 178 zones, not equal to it. The reason is INFERRED (likely
> the 16-byte XAssetList counted in TEMP).

Status: complete for the stream framing (header, script strings, asset list, markers, blocks,
enum, loader addresses). Per-asset struct contents belong to R2 (structs.md); section 5 gives
R2 the entry points.

Conventions: every integer in the stream is big-endian, every pointer is 4 bytes. "Zone offset"
is an offset into the decompressed XFile stream (the `.zone` plaintext produced by inflating a
`.ff`), with the 36-byte prefix at offset 0. ELF addresses are virtual addresses in
`t5mp.elf` (file offset = VA - 0x10000 for every PROGBITS section). The TOC (r2) is 0x00b576e8
(from the entry OPD at 0xb461e0: `00010230 00b576e8`). The ELF has no symbols; function names
below are descriptive, chosen by their behaviour, and marked as such.

## 1. Stream header (XFile) - CONFIRMED (zone bytes + ELF)

The first 36 bytes are nine u32 BE:

| Off | Field | mp_nuked | patch_mp | ui_mp |
|---|---|---|---|---|
| 0x00 | size (= stream length - 36) | 0x041a8b25 | 0x0038b2bf | 0x023b111d |
| 0x04 | externalSize | 0 | 0 | 0 |
| 0x08 | blockSize[0] TEMP | 0x00000ab0 | 0x00000a98 | 0x000002b8 |
| 0x0c | blockSize[1] RUNTIME | 0x004a8580 | 0 | 0 |
| 0x10 | blockSize[2] LARGE_RUNTIME | 0 | 0 | 0 |
| 0x14 | blockSize[3] PHYSICAL_RUNTIME | 0x0115d900 | 0x00072580 | 0 |
| 0x18 | blockSize[4] VIRTUAL | 0x01eb81c1 | 0x002c46d1 | 0x00c9d201 |
| 0x1c | blockSize[5] LARGE | 0 | 0 | 0 |
| 0x20 | blockSize[6] PHYSICAL | 0x011d90c8 | 0x0000b7b4 | 0x016e9b80 |

Hex (mp_nuked.zone, length 68848457 = 0x041a8b49; minus 36 = 0x041a8b25):

```
00000000: 041a 8b25 0000 0000 0000 0ab0 004a 8580
00000010: 0000 0000 0115 d900 01eb 81c1 0000 0000
00000020: 011d 90c8 | 0000 0204 ffff ffff 0000 0211
00000030: ffff ffff | 0000 0000 ffff ffff ffff ffff ...
```

ELF evidence:
- Block name table at VA 0x00b31df8: seven pointers then 0 -> "temp", "runtime",
  "large_runtime", "physical_runtime", "virtual", "large", "physical" (strings at
  0x8f48a0..0x8f48e8). It is used by the zone memory allocator at 0x256f30
  (`addi r25,r5,7672` with r5 = 0xb30000 at 0x256fa8), next to the error string at 0x8f4848
  "Could not allocate %.2f MB of type '%s' for zone '%s' ...".
- The zone loader (descriptively DB_LoadXFileInternal, the code around 0x233a84..0x233ce0)
  reads exactly 36 bytes (`li r29,36` at 0x233a84) into a stack buffer at r1+160, then calls
  the allocator 0x256f30 with r3 = r1+168, i.e. header+8 = the seven block sizes.

So T5 PS3 has seven blocks, numbered and named as on PC T5 (OpenAssetTools,
src/Common/Game/T5/T5_Assets.h `XFileBlock`). There is no extra vertex/index/callback block on
PS3.

| # | Name | Read from the file? (see section 3) |
|---|---|---|
| 0 | TEMP | yes, in place |
| 1 | RUNTIME | no: zero-filled (memset), occupies no file bytes |
| 2 | LARGE_RUNTIME | yes, but deferred to the end of the stream |
| 3 | PHYSICAL_RUNTIME | yes, but deferred to the end of the stream |
| 4 | VIRTUAL | yes, in place |
| 5 | LARGE | yes, in place |
| 6 | PHYSICAL | yes, in place |

## 2. XAssetList, script strings, asset array - CONFIRMED (zone bytes + ELF)

At zone offset 0x24 (16 bytes):

| Off | Field | mp_nuked | patch_mp | ui_mp |
|---|---|---|---|---|
| 0x24 | scriptStringCount | 0x204 (516) | 0xd4 (212) | 0 |
| 0x28 | scriptStrings (ptr) | 0xffffffff | 0xffffffff | 0 (null) |
| 0x2c | assetCount | 0x211 (529) | 0x539 (1337) | 0x29e (670) |
| 0x30 | assets (ptr) | 0xffffffff | 0xffffffff | 0xffffffff |

The loader reads these 16 bytes raw (0x233b04 loop, 16 bytes into the global list at
0xd9be1c), then:

1. Push VIRTUAL (`li r3,4; bl 0x26ab78` at 0x233b30), load the script string list
   (0x2389f0), pop. The list loader: if scriptStrings != 0, align 4 and read count x u32.
   Each entry: 0 = null, -1 = inline NUL-terminated string follows, anything else = offset
   pointer (0x238ab8..0x238ad0). In every zone entry 0 is 0 and all others are -1; the
   strings follow the pointer array in order.
2. Push VIRTUAL (0x233b4c), and if assets != 0, align 4 (0x235bb0 = `li r3,3; b AllocStreamPos`)
   and read assetCount x 8 bytes of `XAsset { u32 type; u32 header; }` (0x233b94).
3. For each asset, Load_XAsset (0x256e60) -> Load_XAssetHeader (0x256670, the type switch,
   section 5) with atStreamStart = 0, because the header pointer was already read with the
   array. In every zone checked every header pointer is 0xffffffff.
4. Pop, then flush the deferred LARGE_RUNTIME / PHYSICAL_RUNTIME reads (0x233ce0,
   `bl 0x26ae38`).

Zone evidence (mp_nuked): strings end and the asset array starts at 0x2d74; the first asset
data starts at 0x3dfc = 0x2d74 + 529 x 8. The first entry is type 0x27 (stringtable), inline:

```
00002d70: 656e 6400 | 0000 0027 ffff ffff 0000 0027 ...      "end\0" | {39, -1} ...
00003df0: ffff ffff 0000 0026 ffff ffff | ffff ffff            last entry {38,-1} | asset data
00003e00: 0000 0002 0000 04dc ffff ffff ffff ffff            StringTable (20 bytes, see s5)
00003e10: 6d70 2f63 6f6e 6669 6773 7472 696e 6773            "mp/configstrings..." (name, inline)
```

## 3. Stream mechanics: blocks, alignment, push/pop - CONFIRMED (ELF), alignment also from zones

Stream primitives (descriptive names; globals live at 0x118a88c..0x11928bc):

| VA | Behaviour | Descriptive name |
|---|---|---|
| 0x26aeb8 | `(atStreamStart, ptr, size)`: if atStreamStart and size: block in {0,4,5,6} -> read size bytes from the file (0x233558) and advance; block 1 -> memset 0 (0x49494) and advance; block 2 or 3 -> append (ptr,size) to a deferred list (count at 0x118a8ac, entries at 0x118a8b0) and advance | Load_Stream |
| 0x26ae38 | read every deferred (ptr,size) from the file, in order | flush deferred reads |
| 0x26ab78 | push: save (pos, block) on the stack at 0x11928bc, switch current block (0x118a8a8) and position (0x11928b4) | DB_PushStreamPos |
| 0x26ac08 | pop: restore; when returning to TEMP the TEMP position is rewound | DB_PopStreamPos |
| 0x26aca0 | `pos = (pos + mask) & ~mask; return pos` (r3 = alignment - 1) | DB_AllocStreamPos |
| 0x26acc0 | `pos += size` | DB_IncStreamPos |
| 0x26acd8 | push VIRTUAL (`cmpwi r8,4`), align 4, reserve 4 bytes, pop; returns the slot | DB_InsertPointer |
| 0x26add8 | `*p = blocks[(v-1)>>29].data + ((v-1) & 0x1fffffff)` | DB_ConvertOffsetToPointer |
| 0x26ada0 | `*p = *(u32*)(blocks[(v-1)>>29].data + ((v-1) & 0x1fffffff))` | DB_ConvertOffsetToAlias |
| 0x26ae08 | read a NUL-terminated string at *p (0x2335d0) and advance by its length | Load_XStringCustom |
| 0x233558 | `(dst, size)`: copy size bytes from the decompressed stream | DB_LoadXFileData |

Block table: 0x11928b0 points to an array of 8-byte `XBlock { u32 data; u32 size; }` (the
convert functions index it with `rlwinm rX,v-1,6,26,28` = block x 8).

Alignment: DB_AllocStreamPos only moves the in-memory position; it never reads or skips file
bytes. The file stream is therefore packed. Zone proof: in common_mp the strings end and the
asset array begins at 0x5c05, in code_post_gfx_mp at 0x7b6 (both unaligned); aligning to 4
misreads every entry (all pointers come out as 0xff000000 / 0xffff0000). Alignment matters
only to a parser that wants to resolve offset pointers: it must track each block's memory
position, applying `(pos + mask) & ~mask` exactly where the loader does. Masks seen: 3 for
asset headers and most arrays, 7 for menuDef_t (0x2561e0), 15 for vertex shader programs
(0x236918), 127 for image pixel data (0x236648), 0 for strings (no alignment, 0x24ba58).

Block choice: each loader pushes a block explicitly. Every asset Ptr loader pushes TEMP (0)
before reading the header pointer, and every asset struct loader pushes VIRTUAL (4) for its
contents (table in section 5: all 35 loaders do this). Image pixel data (0x2365e8) picks
LARGE (5) or PHYSICAL (6) by a byte at GfxImage+14, or LARGE_RUNTIME (2) /
PHYSICAL_RUNTIME (3) when the byte at +27 is non-zero.

Deferred data at the end of the stream (PS3-specific as far as known): because blocks 2/3
are queued and only read after the last asset, the final blockSize[2] + blockSize[3] bytes of
the stream are that deferred data (image pixels). CONFIRMED in four zones - the last asset
ends exactly at `len - blockSize[3]`, and DXT-looking texel data starts there:

| Zone | len | blockSize[3] | boundary | bytes before / after |
|---|---|---|---|---|
| patch_ui_mp | 0xcfb1eb | 0x650000 | 0x6ab1eb | `...ffffffff 70617463685f75695f6d70 0000` ("patch_ui_mp") / `dece7bc6 aaaaaf55 ...` |
| patch_mp | 0x38b2e3 | 0x72580 | 0x318d63 | `...ffffffff 7061746368 5f6d70 0000` ("patch_mp") / `ff372d27 ff1a3638 ...` |
| patch | 0xa9cb7 | 0x4880 | 0xa5437 | `...00000000 ffffffff 00` / `ff372d27 ff1a3638 ...` |
| zombie_theater | 0x6115538 | 0x1987100 | 0x478e438 | rawfile text `...zombie_theater.csv'\n\0` / `cb5a694a 80a0e87a ...` |

(mp_nuked's boundary 0x304b249 is preceded by `...00 ffffffff 00` and followed by
`00000000 00000000 ffff2068`, consistent but not conclusive on its own.)

INFERRED: this works because the deferred chunks' sizes are multiples of their alignment, so
blockSize[3] equals the bytes on disc; it would break if a deferred chunk needed padding.
Confirm by a full struct walk (Phase 2) ending exactly at `len - blockSize[2] - blockSize[3]`.
RUNTIME (block 1) data has no bytes in the file at all (mp_nuked declares 0x4a8580 of it).

## 4. Pointer encoding - CONFIRMED (ELF), offset form also seen in zone bytes

A 32-bit pointer field read from the stream means:

| Value | Meaning | ELF evidence |
|---|---|---|
| 0x00000000 | null, nothing follows | every Ptr loader, e.g. 0x2451fc `cmpwi r31,0` |
| 0xffffffff (-1) | data follows inline: align, set pointer to the current block position, load the struct | `addi r0,r31,2; cmplwi r0,1` (i.e. -1 or -2) at 0x245204 |
| 0xfffffffe (-2) | as -1, and also reserve an alias slot in VIRTUAL (DB_InsertPointer) and store the final header pointer into it | `cmpwi r31,-2` at 0x245250 -> 0x24528c |
| anything else | `((block << 29) | offset) + 1`: an offset into an already-loaded block | 0x26add8 / 0x26ada0 |

For asset-header pointers (XAsset.header and pointers to other assets) the "anything else"
case goes through DB_ConvertOffsetToAlias (pointer to an alias slot holding the real header);
for ordinary sub-struct pointers it is DB_ConvertOffsetToPointer. Sub-struct pointers in the
loaders observed only test 0 and -1 (e.g. 0x248c24..0x248c3c), -2 is used for asset pointers.

Zone evidence of the offset form: in patch_mp, scanning words that follow a 0xffffffff, 1892
values decode to block 4 (VIRTUAL) with an offset below blockSize[4], none to other blocks:
e.g. at 0x54ac `ffffffff 800043ed 800043ed` (two pointers to VIRTUAL offset 0x43ec). Real -2
occurrences in zones are not yet located (a raw byte search finds only unaligned noise);
locating one needs the struct walker (Phase 2).

PS3 vs PC: the PC T5 constants (OpenAssetTools, src/ZoneCommon/Game/T5/ZoneConstantsT5.h:
`OFFSET_BLOCK_BIT_COUNT = 3`, `INSERT_BLOCK = XFILE_BLOCK_VIRTUAL`) match what the PS3 code
does (3-bit block in the top bits, alias slots in VIRTUAL).

## 5. Asset type enum and per-type loaders - CONFIRMED (ELF + zones)

Sources in t5mp.elf, all 46 entries long and mutually consistent:
- Type name table at VA 0x00b5bccc (46 pointers to strings 0x8f43c0..0x8f4608), used by the
  name getter at 0x22f498 (`lis r4,0xb6; addi r9,r4,-17204; lwzx r3,r9,r3`).
- Pool size table at VA 0x008f4900 (46 x s32), used next to "Exceeded limit of %d '%s'
  assets." at 0x25ee0c.
- Struct size handler table at VA 0x00b31bd0 (46 OPD pointers; each target is `li r3,N; blr`
  at 0x22f4b0..0x22f5c8). NULL entries exactly at 21, 27, 28, 32..37.
- Name getter handler table at VA 0x00b31d40 and name setter table at VA 0x00b31c88 (same
  NULLs; the setter also lacks 12, 43, 44). The getter gives the name field offset.
- The switch in Load_XAssetHeader at VA 0x256670 (compare chain on the type read from
  *(0xd9c390)); types without a case fall through to a plain return.
- t5.elf (SP) has the identical 46-name table at VA 0xa586a0.

Zone cross-check: every type value in nine inflated zones lies in 0..45 and only types with a
loader occur; content fits (localize in ui zones, game_map_mp in MP maps, game_map_sp and
col_map_sp in zombie_theater, weapon 26 in common_mp).

Columns: Ptr = the per-type pointer loader called from the switch (handles 0/-1/-2/offset,
pushes TEMP); Load = the struct loader (or "inline" when compiled into the Ptr function, with
the address of its first instruction); sizeof = the size of the first Load_Stream (equal to
the size handler table value); Name = name field offset (getter table); Add = the function
called after loading (descriptively the per-type DB_AddXAsset wrapper); Pool = pool size.
Descriptive names; addresses are what R2 should disassemble.

| Val | ELF name | PC T5 (OAT T5.h) | Ptr | Load | sizeof | Name | Add | Pool |
|---|---|---|---|---|---|---|---|---|
| 0 | xmodelpieces | XMODELPIECES 0 | inline in switch 0x2567d0 (no -2 path) | 0x24e430 | 12 | +0 | - | 64 |
| 1 | physpreset | PHYSPRESET 1 | 0x24baa0 | inline 0x24bb48 | 84 (0x54) | +0 | 0x266098 | 64 |
| 2 | physconstraints | PHYSCONSTRAINTS 2 | 0x24a498 | 0x24a2c8 | 2696 (0xa88) | +0 | 0x266238 | 64 |
| 3 | destructibledef | DESTRUCTIBLEDEF 3 | 0x254628 | 0x254360 | 24 (0x18) | +0 | 0x2661d0 | 64 |
| 4 | xanim | XANIMPARTS 4 | 0x244db8 | 0x244728 | 104 (0x68) | +0 | 0x266168 | 5100 |
| 5 | xmodel | XMODEL 5 | 0x24c998 | 0x24bc88 | 248 (0xf8) | +0 | 0x266100 | 1000 |
| 6 | material | MATERIAL 6 | 0x249498 | 0x248f20 | 128 (0x80) | +0 | 0x260138 | 4096 |
| 7 | pixelshader | none (PS3 only) | 0x244178 | 0x244038 | 12 (0xc) | +0 | 0x266c00 | 4192 |
| 8 | vertexshader | none (PS3 only) | 0x247f28 | inline 0x247fd0 (program: 0x2368e0) | 16 (0x10) | +0 | 0x266f50 | 1800 |
| 9 | techset | TECHNIQUE_SET 7 | 0x248878 | 0x2486d8 | 292 (0x124) | +0 | 0x266578 | 512 |
| 10 | image | IMAGE 8 | 0x248b40 | inline 0x248be8 (pixels: 0x2365e8) | 112 (0x70) | +0x68 | 0x2670f0 | 4096 |
| 11 | sound | SOUND 9 | 0x2466a0 | 0x246310 | 40 (0x28) | +0 | 0x266718 | 32 |
| 12 | sound_patch | SOUND_PATCH 10 | 0x246ad0 | 0x2467b0 | 20 (0x14) | +0 | 0x266da0 | 16 |
| 13 | col_map_sp | CLIPMAP 11 | 0x250588 (shared with 14) | 0x24e848 | 332 (0x14c) | +0 | 0x2663d8 | 1 |
| 14 | col_map_mp | CLIPMAP_PVS 12 | 0x250588 (shared with 13) | 0x24e848 | 332 (0x14c) | +0 | 0x2663d8 | 1 |
| 15 | com_map | COMWORLD 13 | 0x2457a0 | 0x2452d0 | 64 (0x40) | +0 | 0x267208 | 1 |
| 16 | game_map_sp | GAMEWORLD_SP 14 | 0x24b8f8 | inline (glass data: 0x23b1f0) | 44 (0x2c) | +0 | 0x266ae8 | 1 |
| 17 | game_map_mp | GAMEWORLD_MP 15 | 0x248998 | inline (glass data: 0x23b1f0) | 44 (0x2c) | +0 | 0x266e80 | 1 |
| 18 | map_ents | MAP_ENTS 16 | 0x243f18 | 0x243e20 | 12 (0xc) | +0 | 0x2664a8 | 2 |
| 19 | gfx_map | GFXWORLD 17 | 0x2521f8 | 0x250f88 | 1108 (0x454) | +0 | 0x266648 | 1 |
| 20 | lightdef | LIGHT_DEF 18 | 0x24a6d0 | 0x24a5e8 | 16 (0x10) | +0 | 0x266cd0 | 32 |
| 21 | ui_map | UI_MAP 19 | no loader | - | - | - | - | 0 |
| 22 | font | FONT 20 | 0x2496f8 | 0x249590 | 24 (0x18) | +0 | 0x266308 | 16 |
| 23 | menufile | MENULIST 21 | 0x256578 | 0x2562e0 | 12 (0xc) | +0 | 0x267088 | 164 |
| 24 | menu | MENU 22 | 0x2561e0 (align 8) | 0x255cb0 | 424 (0x1a8) | +0 | 0x266b50 | 970 |
| 25 | localize | LOCALIZE_ENTRY 23 | 0x24b700 | inline | 8 | +4 | 0x266ee8 | 9216 |
| 26 | weapon | WEAPON 24 | 0x253e30 | 0x253790 | 228 (0xe4) | +0 | 0x266510 | 2048 |
| 27 | weapondef | WEAPONDEF 25 | no loader | - | - | - | - | 0 |
| 28 | weaponvariant | WEAPON_VARIANT 26 | no loader | - | - | - | - | 0 |
| 29 | snddriverglobals | SNDDRIVER_GLOBALS 27 | 0x244620 | 0x2443a0 | 52 (0x34) | +0 | 0x266fb8 | 1 |
| 30 | fx | FX 28 | 0x24d748 | 0x24d498 | 60 (0x3c) | +0 | 0x2666b0 | 450 |
| 31 | impactfx | IMPACT_FX 29 | 0x24e2f8 | 0x24e140 | 8 | +0 | 0x266d38 | 4 |
| 32 | aitype | AITYPE 30 | no loader | - | - | - | - | 0 |
| 33 | mptype | MPTYPE 31 | no loader | - | - | - | - | 0 |
| 34 | mpbody | MPBODY 32 | no loader | - | - | - | - | 0 |
| 35 | mphead | MPHEAD 33 | no loader | - | - | - | - | 0 |
| 36 | character | CHARACTER 34 | no loader | - | - | - | - | 0 |
| 37 | xmodelalias | XMODELALIAS 35 | no loader | - | - | - | - | 0 |
| 38 | rawfile | RAWFILE 36 | 0x243d08 | 0x243c08 | 12 (0xc) | +0 | 0x266370 | 1024 |
| 39 | stringtable | STRINGTABLE 37 | 0x2451c8 | 0x244ec0 | 20 (0x14) | +0 | 0x266e10 | 80 |
| 40 | packindex | PACK_INDEX 38 | 0x247da0 | inline | 12 (0xc) | +0 | 0x266788 | 16 |
| 41 | xGlobals | XGLOBALS 39 | 0x247c08 | inline | 40 (0x28) | +0 | 0x266c68 | 1 |
| 42 | ddl | DDL 40 | 0x247570 | 0x247480 | 8 | +0 | 0x267020 | 24 |
| 43 | glasses | GLASSES 41 | 0x24de80 | 0x24db80 | 56 (0x38) | +0 | 0x266440 | 1 |
| 44 | texturelist | none (PS3 only) | 0x235cc8 | 0x235c40 | 8 | constant "texturelist" | 0x2665e0 | 1 |
| 45 | emblemset | EMBLEMSET 42 | 0x24ae98 | 0x24a7f8 | 44 (0x2c) | constant "emblemset" | 0x2662a0 | 4 |

ASSET_TYPE_COUNT = 46 (CONFIRMED: four parallel 46-entry tables, compare chain ends at 45).

PS3 differences from PC T5: three extra types (pixelshader 7, vertexshader 8, texturelist 44)
shift every PC value from techset onwards (PC techset 7 = PS3 9; PC emblemset 42 = PS3 45).
The ELF names col_map_sp / col_map_mp are what PC tooling calls clipmap / clipmap_pvs; both
share one loader. ui_map, weapondef, weaponvariant, aitype..xmodelalias exist as names but have
no loader in the MP (or, by the name table, SP) executable's switch: zones must not contain
them. Weapons are stored as type 26 "weapon".

Note on the struct loaders: each takes the atStreamStart flag; it first calls
Load_Stream(1, var, sizeof) and then pushes VIRTUAL (4) for its sub-data. Inline sub-struct
loaders are called with atStreamStart = 0 (data already read as part of the parent).

### Per-type asset counts (walker below)

| Zone | Counts (type name = n) | Total |
|---|---|---|
| mp_nuked | 1 physpreset 7, 3 destructibledef 13, 4 xanim 1, 5 xmodel 289, 6 material 2, 9 techset 104, 11 sound 2, 14 col_map_mp 1, 15 com_map 1, 17 game_map_mp 1, 19 gfx_map 1, 20 lightdef 1, 30 fx 66, 38 rawfile 26, 39 stringtable 12, 43 glasses 1, 44 texturelist 1 | 529 |
| mp_firingrange | 1 physpreset 5, 3 destructibledef 9, 5 xmodel 268, 6 material 2, 9 techset 131, 11 sound 2, 14 col_map_mp 1, 15 com_map 1, 17 game_map_mp 1, 19 gfx_map 1, 20 lightdef 2, 30 fx 71, 38 rawfile 24, 39 stringtable 12, 43 glasses 1, 44 texturelist 1 | 532 |
| common_mp | 4 xanim 3361, 5 xmodel 325, 6 material 480, 9 techset 113, 10 image 28, 11 sound 2, 20 lightdef 1, 22 font 2, 23 menufile 85, 25 localize 419, 26 weapon 1716, 30 fx 293, 31 impactfx 1, 38 rawfile 459, 39 stringtable 1 | 7286 |
| code_post_gfx_mp | 1 physpreset 1, 2 physconstraints 1, 4 xanim 2, 5 xmodel 5, 6 material 453, 9 techset 99, 10 image 7, 11 sound 2, 20 lightdef 1, 22 font 8, 23 menufile 2, 25 localize 7807, 29 snddriverglobals 1, 30 fx 1, 38 rawfile 138, 39 stringtable 41, 42 ddl 6, 45 emblemset 1 | 8576 |
| ui_mp | 6 material 359, 9 techset 5, 23 menufile 3, 25 localize 302, 38 rawfile 1 | 670 |
| patch | 1 physpreset 1, 2 physconstraints 1, 6 material 3, 9 techset 3, 10 image 1, 12 sound_patch 1, 23 menufile 10, 25 localize 391, 30 fx 1, 38 rawfile 8, 39 stringtable 4, 42 ddl 2 | 426 |
| patch_mp | 1 physpreset 1, 2 physconstraints 1, 4 xanim 328, 5 xmodel 32, 6 material 46, 9 techset 47, 10 image 1, 12 sound_patch 1, 23 menufile 26, 25 localize 445, 26 weapon 187, 30 fx 32, 38 rawfile 166, 39 stringtable 17, 40 packindex 1, 42 ddl 5, 45 emblemset 1 | 1337 |
| patch_ui_mp | 6 material 82, 9 techset 2, 23 menufile 41, 25 localize 4, 38 rawfile 1 | 130 |
| zombie_theater | 1 physpreset 1, 4 xanim 177, 5 xmodel 330, 6 material 5, 9 techset 198, 11 sound 2, 13 col_map_sp 1, 15 com_map 1, 16 game_map_sp 1, 19 gfx_map 1, 20 lightdef 1, 25 localize 6, 26 weapon 13, 30 fx 129, 38 rawfile 50, 39 stringtable 1 | 917 |

Script string counts: mp_nuked 516, mp_firingrange 609, common_mp 1145, code_post_gfx_mp 116,
patch 1, patch_mp 212, zombie_theater 790, ui_mp 0, patch_ui_mp 0 (ptr 0).

Observations: map zones hold few materials as assets (2 in mp_nuked) because materials are
mostly loaded inline through other assets' pointers; image assets are rare for the same
reason (pixel data sits in the deferred tail, section 3).

## 6. PS3-specific notes (summary)

- Big-endian; 4-byte pointers; struct sizes above are the PS3 sizes (from the ELF).
- Same seven blocks as PC T5; no extra blocks.
- Deferred LARGE_RUNTIME / PHYSICAL_RUNTIME reads put image pixel data at the end of the
  stream, after the last asset (section 3).
- Three extra asset types (7, 8, 44); PC numbering is shifted from techset on (section 5).
- The XAssetList is read raw (16 bytes) rather than through Load_Stream; it does not occupy
  stream block memory.

## 7. Reference walker (header, script strings, asset list)

Verified on all nine inflated zones (scratch copy used for the counts above). Stdlib only.

```python
import struct

ASSET_TYPE_NAMES = (
    "xmodelpieces physpreset physconstraints destructibledef xanim xmodel material "
    "pixelshader vertexshader techset image sound sound_patch col_map_sp col_map_mp com_map "
    "game_map_sp game_map_mp map_ents gfx_map lightdef ui_map font menufile menu localize "
    "weapon weapondef weaponvariant snddriverglobals fx impactfx aitype mptype mpbody mphead "
    "character xmodelalias rawfile stringtable packindex xGlobals ddl glasses texturelist "
    "emblemset").split()                      # index = XAssetType, 46 entries
BLOCK_NAMES = ("temp", "runtime", "large_runtime", "physical_runtime",
               "virtual", "large", "physical")
PTR_NULL, PTR_INLINE, PTR_INSERT = 0, 0xFFFFFFFF, 0xFFFFFFFE


def decode_offset_pointer(v):
    """((block << 29) | offset) + 1  ->  (block, offset)."""
    v -= 1
    return v >> 29, v & 0x1FFFFFFF


def parse_xfile_head(data):
    u32 = lambda o: struct.unpack_from(">I", data, o)[0]
    size, external = u32(0), u32(4)
    if size != len(data) - 36:
        raise ValueError(f"XFile.size: expected {len(data) - 36:#x}, found {size:#x} at 0x0")
    blocks = [u32(8 + 4 * i) for i in range(7)]
    ss_count, ss_ptr, a_count, a_ptr = (u32(0x24 + 4 * i) for i in range(4))
    pos = 0x34                                   # no alignment anywhere in the file
    strings = []
    if ss_ptr:                                   # loader tests != 0, zones use -1
        ptrs = [u32(pos + 4 * i) for i in range(ss_count)]
        pos += 4 * ss_count
        for p in ptrs:
            if p == PTR_INLINE:
                end = data.index(b"\0", pos)
                strings.append(data[pos:end].decode("latin-1"))
                pos = end + 1
            elif p == PTR_NULL:
                strings.append(None)
            else:
                strings.append(decode_offset_pointer(p))   # not seen in shipped zones
    assets = []
    if a_ptr:
        for i in range(a_count):
            t, h = u32(pos), u32(pos + 4)
            if t >= len(ASSET_TYPE_NAMES):
                raise ValueError(f"asset type: expected < 46, found {t} at {pos:#x}")
            assets.append((t, h))
            pos += 8
    # Asset data starts at `pos`. The last blocks[2] + blocks[3] bytes of `data` are the
    # deferred LARGE_RUNTIME / PHYSICAL_RUNTIME data, not asset structs.
    return dict(size=size, external_size=external, block_sizes=blocks,
                script_strings=strings, assets=assets, asset_data_offset=pos,
                deferred_tail_offset=len(data) - blocks[2] - blocks[3])
```

## Open items (what would confirm them)

- -2 (insert alias) markers have not been located in zone bytes yet; needs the Phase 2 struct
  walker. Semantics are confirmed from the ELF.
- Exact equality "deferred tail size == blockSize[2] + blockSize[3]" (INFERRED, confirmed only
  at the boundary in four zones); a full walk of mp_nuked would settle it.
- Offset-pointer resolution against reconstructed block memory (needs the walker to track
  per-block positions with the alignment masks in section 3).
