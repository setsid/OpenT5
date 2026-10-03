# R2b: content asset structs (T5 PS3, BLES01031)

Status: complete for every content type that occurs in the nine inflated zones. Every layout
below was checked by walking real zones and comparing, asset by asset, with the game's own
loader code run over the same bytes (section 1). Fields that are not confirmed that way are
marked INFERRED, with what would confirm them.

Owner: track R2b. Companion documents: `xfile.md` (R1: header, blocks, pointer encoding, type
enum, per-type loader table), `textures.md` (R3: pixel formats). R2a owns clipmap, comworld,
gfxworld, mapents, xmodel, physpreset, physconstraints, destructibledef, lightdef.

Conventions

- All integers are big-endian. Pointers are 4 bytes.
- "Zone offset" = offset into the inflated XFile stream (`.zone` plaintext, 36-byte header
  included), as in `xfile.md`.
- ELF addresses are virtual addresses in `t5mp.elf` (file offset = VA - 0x10000).
- Block numbers: 0 TEMP, 1 RUNTIME, 2 LARGE_RUNTIME, 3 PHYSICAL_RUNTIME, 4 VIRTUAL, 5 LARGE,
  6 PHYSICAL (`xfile.md` section 1).
- "align N" means the loader calls `DB_AllocStreamPos(N-1)` (VA 0x26aca0) before the data. It
  moves only the in-memory block position, never the file position (`xfile.md` section 3).
- "LS n" = `Load_Stream(1, ptr, n)` (VA 0x26aeb8): n bytes are read from the file (blocks
  0/4/5/6), zero-filled (block 1, no file bytes), or deferred to the end of the file (blocks
  2/3).
- "string" = `const char*` loaded by the Load_XString pattern: value -1 -> align 1 and a
  NUL-terminated string follows in the file (VA 0x26ae08, reader 0x2335d0); 0 -> null; any
  other value -> offset pointer to a string loaded earlier (`DB_ConvertOffsetToPointer`,
  0x26add8). Strings are de-duplicated across the whole zone this way.
- Struct and field names come from the PC T5 headers of OpenAssetTools
  (src/Common/Game/T5/T5_Assets.h) wherever the PS3 offsets and sizes agree with them; the
  agreement is stated per type. PC offsets were obtained by compiling that header as 32-bit
  with 8-byte alignment for 64-bit types and reading the offsets back with gdb.

## Contents

1. Method and evidence
2. Rules shared by all content loaders
3. rawfile (38)
4. stringtable (39)
5. localize (25)
6. menufile (23) and menu (24)
7. image (10) and where pixels live
8. material (6)
9. techset (9), vertexshader (8), pixelshader (7)
10. xanim (4)
11. fx (30), impactfx (31)
12. sound (11), sound_patch (12), snddriverglobals (29)
13. weapon (26)
14. font (22), ddl (42), emblemset (45), glasses (43), packindex (40), xGlobals (41),
    texturelist (44)
15. Types with no loader
16. Resizing rawfile, stringtable and localize
17. Walker and coverage
18. Open items

## 1. Method and evidence

1. Per-type loaders. The loader addresses are those of `xfile.md` section 5 (from the switch in
   Load_XAssetHeader at 0x256670). Each struct loader was disassembled with
   `powerpc64-linux-gnu-objdump`; sizes come from the `li r5,N` before Load_Stream, alignment
   from the `li r3,M` before `DB_AllocStreamPos`, blocks from the `li r3,B` before
   `DB_PushStreamPos` (0x26ab78), counts from the loads feeding `r5`.
2. The game's own loader, executed. A scratch harness (a small PowerPC interpreter in Python,
   not part of OpenT5) loads `t5mp.elf`, runs the real stream initialiser (0x26aac0), the real
   script-string loader (0x2389f0) and the real `Load_XAsset` (0x256e60) once per asset, then
   the real deferred flush (0x26ae38), exactly as `DB_LoadXFile` does (0x233ab4..0x233ce0).
   Only two functions are replaced: the file reader (0x233558) and the string reader
   (0x2335d0), which copy from the `.zone` file. Calls outside the loader code (asset
   registration such as 0x266370, the shader/state-bits converters 0x7454a0 / 0x745478, the
   RSX offset converters 0x7145a0 / 0x736af8, script string remapping 0x26b000) return
   immediately; none of them touches the stream. The texture size function 0x736ec0 runs
   natively. Result, all nine zones:

   | Zone | Stream length | Bytes consumed by the game's loader | Final block positions = header blockSize[0..6] |
   |---|---|---|---|
   | patch_mp | 0x38b2e3 | 0x38b2e3 | yes (TEMP rewound to 0, its high-water mark is blockSize[0]) |
   | ui_mp | 0x23b1141 | 0x23b1141 | yes |
   | patch_ui_mp | 0xcfb1eb | 0xcfb1eb | yes |
   | patch | 0xa9cb7 | 0xa9cb7 | yes |
   | code_post_gfx_mp | 0x9bb103 | 0x9bb103 | yes |
   | common_mp | 0x5b3d203 | 0x5b3d203 | yes |
   | mp_nuked | 0x41a8b49 | 0x41a8b49 | yes |
   | mp_firingrange | 0x3ddf348 | 0x3ddf348 | yes |
   | zombie_theater | 0x6115538 | 0x6115538 | yes |

   The harness logs every primitive call (Load_Stream size/block/file offset, alloc mask,
   push/pop, string reads, offset conversions) with the calling loader, and records the file
   offset and all seven block positions at every asset boundary.
3. An independent walker (scratch `walker.py`) implements every content type from the tables
   in this document. For each asset it starts from the recorded block positions, walks the
   asset, and must (a) end at the same file offset, (b) end with the same seven block
   positions, and (c) for the traced assets, produce the identical sequence of Load_Stream
   sizes/blocks, allocs with masks, pushes/pops and strings as the game's loader. Results in
   section 17: every asset of every content type passes, except 7 that contain an inline
   XModel (an R2a type the walker does not implement).

Confidence labels per type (details in each section):

| Type | Layout of pointer/count fields and load order | Non-pointer field names |
|---|---|---|
| rawfile, stringtable, localize | CONFIRMED (ELF + all assets in 9 zones) | CONFIRMED (sizes, counts used by loader); hash and index rules CONFIRMED from data |
| menufile, menu and all menu sub-structs | CONFIRMED | PS3 differs from PC; shifted fields named by offset, names INFERRED |
| image | CONFIRMED | GCM header CONFIRMED (with R3); fields 0x30..0x67 INFERRED (see R3) |
| material, techset, vertex/pixel shader | CONFIRMED | PS3-specific layouts; some names INFERRED |
| xanim | CONFIRMED | boneCount width INFERRED |
| fx, impactfx | CONFIRMED (inline XModel visuals: ELF only) | same as PC (sizes and all pointer offsets match) |
| sound, sound_patch, snddriverglobals | CONFIRMED | StreamedSound tail INFERRED |
| weapon | CONFIRMED | same as PC (sizes and all pointer offsets match) |
| font, ddl, emblemset, glasses, packindex, texturelist | CONFIRMED | PackIndex/TextureList contents INFERRED |
| xGlobals | ELF only (no instance in the nine zones) | INFERRED from PC |

## 2. Rules shared by all content loaders

### 2.1 Asset pointer (the per-type `Load_<X>Ptr`)

Used for the asset itself (from `Load_XAsset`) and for every reference from one asset to
another asset (material, image, techset, fx, xmodel, xanim, menu, vertex/pixel shader).

```
push(TEMP)
v = pointer
if v == 0:              nothing
elif v in (-1, -2):     align 4 (menus: align 8) in TEMP
                        if v == -2: DB_InsertPointer (reserve a 4-byte alias slot in VIRTUAL)
                        LS sizeof(header) in TEMP
                        struct loader (below)
                        register the asset (0x266xxx / 0x2670f0 ...), store into alias slot
else:                   offset pointer to an alias slot (DB_ConvertOffsetToAlias, 0x26ada0)
pop()                   (TEMP position rewinds: asset headers do not use TEMP space for long)
```

The struct loader then does `push(VIRTUAL)`, loads the sub-data, `pop()`. Asset headers
therefore live in TEMP and everything they own in VIRTUAL unless stated otherwise.

Example, a localize entry with an inline value and a shared name, patch_mp asset #367 at zone
0xa908a: `ffffffff 80053f4e 5a6f6f00` = value -1 ("Zoo" follows), name = offset
0x80053f4e -> block 4, offset 0x53f4d.

Names starting with `,` (for example `,mc_tools`, `,impacts/fx_dud_m203`) are placeholder
entries: the header holds only the name, every other pointer is 0 (patch_mp asset #705 at
0x1568b5: techset 292 bytes of zero except the name). INFERRED meaning: a reference to an asset
defined in another zone.

### 2.2 Sub-pointer tests: "-1" fields and "non-zero" fields

Two kinds of test occur, and a writer must respect the difference:

| Kind | Test | Meaning of other values | Used for |
|---|---|---|---|
| shareable | `== -1` inline; 0 null | offset pointer (`0x26add8`) to data loaded earlier | strings, and structs the PC tools call "reusable" (material texture/constant/state tables, techset techniques, vertex declarations, `WeaponDef`, weapon xanim/hideTag arrays, sound files) |
| owned | `!= 0` inline | none: any non-zero value is treated as -1 | most arrays and sub-structs (rawfile buffer, stringtable cells/index, menu items/handlers/expressions, xanim data, fx elems, image pixels, sound data, emblem tables, glasses, ddl, trail data) |

All values seen in owned fields in the nine zones are 0 or -1. Each field below is marked
`[-1]` or `[nz]`. Evidence: the compare sequence after the field load in each loader, e.g.
rawfile buffer 0x243c80 (`cmpwi r,0` only), stringtable name 0x244fec (`cmpwi r,-1`).

### 2.3 Script strings

Fields typed "scrstr" are u16 indices into the zone's script string list. They are read as part
of their struct and remapped by 0x26b000 after loading; they never cause stream reads.

### 2.4 Alignment masks seen

| Align | Where |
|---|---|
| 1 | strings, byte arrays, menu cell strings and event names |
| 2 | u16 arrays (stringtable cellIndex, xanim names/short data, weapon hideTags/notetracks, emblem lookup, fx trail indices) |
| 4 | asset headers, most arrays |
| 8 | menuDef_t header (TEMP), itemDef_s (VIRTUAL) |
| 16 | rawfile buffer, material constant table, shader programs |
| 32 | glasses work memory (RUNTIME block) |
| 128 | image pixels |
| 2048 | loaded-sound data, primed-sound buffers |

## 3. rawfile (38)

Loaders: Ptr 0x243d08, struct 0x243c08. Same layout as PC (OpenAssetTools RawFile).

| Off | Size | Type | Field | Notes |
|---|---|---|---|---|
| 0x0 | 4 | string [-1] | name | |
| 0x4 | 4 | s32 | len | buffer length without the final byte |
| 0x8 | 4 | ptr [nz] | buffer | align 16 in VIRTUAL, LS len+1 |

Load order: header (TEMP), push VIRTUAL, name, buffer, pop.

Example, patch_mp asset #13 at 0x1ac7d, `mp/playeranimtypes.txt`:

```
0001ac7d: ffff ffff 0000 00ce ffff ffff 6d70 2f70   name -1, len 0xce, buffer -1, "mp/p...
0001ac8d: 6c61 7965 7261 6e69 6d74 7970 6573 2e74   ...layeranimtypes.t
0001ac9d: 7874 00|6e 6f6e 650d 0a64 6566 6175 6c74  xt\0 | buffer: "none\r\ndefault..." (0xcf bytes)
```

Two buffer formats (all 9 zones, 873 rawfiles):

| Kind | Files | Buffer |
|---|---|---|
| plain | everything except .gsc/.csc (vision, cfg, rmb, txt, shock, atr, csv, ...) | text, `buffer[len]` = 0 in all 371 |
| zlib | all 502 .gsc and .csc | u32 BE uncompressed size, u32 BE compressed size, zlib stream (`78 9c`), then one more byte; `len` = 8 + compressed size |

For the zlib kind: the uncompressed data always ends with a NUL that is counted in the
uncompressed size; `zlib.compress(data, 6)` (Python, zlib level 6) reproduces all 502 stored
streams byte for byte; the byte after the stream (`buffer[len]`) equals `uncompressed[len]` when
len < uncompressed size (448 cases) and 0 otherwise (54 cases). Example, patch_mp asset #540
`maps/mp/animscripts/dog_combat.gsc` at 0xab83f: len 0xb9c, buffer at 0xab86e
`00002f8d 00000b94 789cc51a...` (12173 bytes unpacked, 2964 packed).

## 4. stringtable (39)

Loaders: Ptr 0x2451c8, struct 0x244ec0. Same layout as PC.

| Off | Size | Type | Field | Notes |
|---|---|---|---|---|
| 0x0 | 4 | string [-1] | name | |
| 0x4 | 4 | s32 | columnCount | |
| 0x8 | 4 | s32 | rowCount | |
| 0xc | 4 | ptr [nz] | values | align 4, LS 8 x columnCount x rowCount (StringTableCell) |
| 0x10 | 4 | ptr [nz] | cellIndex | align 2, LS 2 x columnCount x rowCount (s16) |

StringTableCell (8): `+0 string [-1] string`, `+4 s32 hash`.

Load order: name, the whole cell array, then each cell's string in cell order (row-major,
cell = row x columnCount + column), then cellIndex.

Rules (checked on all 72 tables in 9 zones):

- hash = 32-bit djb2 over the lower-cased string: `h = 5381; for c: h = h*33 + tolower(c)`
  (all 8 423 inline cell strings in patch_mp, code_post_gfx_mp, mp_nuked and common_mp; the empty string gives 5381).
- cellIndex lists all cell numbers sorted by hash as signed 32-bit. Adjacent entries always
  satisfy `(int32)(hash[a] - hash[b]) <= 0`, i.e. the order is what a qsort with an
  overflowing subtraction comparator produces; ties are in no fixed order. A writer that sorts
  by signed hash produces a valid index; keep the original index when cells are unchanged to
  stay byte-identical.

Example, patch_mp asset #52 at 0x736e6, `mp/freeofferids.csv` (1 x 1):

```
000736e6: ffff ffff 0000 0001 0000 0001 ffff ffff   name -1, 1 column, 1 row, values -1
000736f6: ffff ffff 6d70 2f66 7265 656f 6666 6572   cellIndex -1, "mp/freeoffer
00073706: 6964 732e 6373 7600|ffff ffff 6775 5b61   ids.csv\0" | cell 0: string -1, hash 0x67755b61
00073716: 4242 4630 3041 3100|0000                  "BBF00A1\0" | cellIndex[0] = 0
```

## 5. localize (25)

Loader: Ptr 0x24b700 with the struct loader inline. Same layout as PC (LocalizeEntry, 8 bytes).

| Off | Size | Type | Field |
|---|---|---|---|
| 0x0 | 4 | string [-1] | value |
| 0x4 | 4 | string [-1] | name |

Load order: header (TEMP), push VIRTUAL, value, name, pop. Example, patch_mp asset #95 at
0xa49b3: `ffffffff ffffffff "Accuracy\0" "CGAME_SB_ACCURACY\0"` (value before name).

## 6. menufile (23) and menu (24)

Loaders: MenuList 0x2562e0 (Ptr 0x256578); menuDef_t Ptr 0x2561e0, struct 0x255cb0; itemDef_s
0x2558e0; itemDefData 0x255598; textDef_s 0x255460; textDef union 0x254e78; focus union
0x249df0; listBoxDef_s 0x249b78; MenuRow 0x238530; multiDef_s 0x247690; ExpressionStatement
0x254768; rectData_s 0x254ad0; GenericEventHandler 0x255088; GenericEventScript 0x254b80;
ScriptCondition chain 0x23bda0; ItemKeyHandler 0x254d70; UIAnimInfo 0x255230.

PS3 menus differ from PC: four local clients (split screen) widen several per-client arrays.
windowDef_t is 176 bytes (PC 164), menuDef_t 424 (PC 400), itemDef_s 280 (PC 272), textDef_s
140 (PC 68), listBoxDef_s 700 (PC 668), editFieldDef_s 48 (PC 36), and focusItemDef_s is only
8 bytes (PC 24: the four mouse strings do not exist on PS3).

### 6.1 MenuList (12)

| Off | Type | Field | Notes |
|---|---|---|---|
| 0x0 | string [-1] | name | |
| 0x4 | s32 | menuCount | |
| 0x8 | ptr [nz] | menus | align 4, LS 4 x menuCount; each element is a menu asset pointer (2.1) with **align 8** for the 424-byte header in TEMP |

Menus are almost always inline in the menu list (type 24 assets do not occur in the nine zones).

### 6.2 windowDef_t (176, embedded at +0 of menuDef_t and itemDef_s)

| Off | Type | Field | Evidence |
|---|---|---|---|
| 0x0 | string [-1] | name | loaded first |
| 0x4 | rectDef_s (24) | rect | PC |
| 0x1c | rectDef_s (24) | rectClient | PC |
| 0x34 | string [-1] | group | loaded second (0x255d78 `addi r8,r10,52`) |
| 0x38 | 4 x u8 | style, border, modal, frameSides | PC |
| 0x3c..0x4f | | frameTexSize, frameSize, ownerDraw, ownerDrawFlags, borderSize | PC; borderSize = 1.0 at +0x4c in default_menu |
| 0x50 | s32 | staticFlags | PC |
| 0x54 | 4 x s32 | dynamicFlags[4] | INFERRED: the 12 extra bytes lie between borderSize and foreColor (foreColor = 1,1,1,1 at +0x68 in default_menu, PC +0x5c) |
| 0x64 | s32 | nextTime | |
| 0x68 | 4 x 4 x f32 | foreColor, backColor, borderColor, outlineColor | |
| 0xa8 | f32 | rotation | |
| 0xac | material asset ptr | background | loaded third (Material Ptr 0x249498) |

### 6.3 menuDef_t (424, TEMP, align 8)

Pointer fields in load order (offsets CONFIRMED from 0x255cb0):

| Off | Type | Field | Notes |
|---|---|---|---|
| 0x0 | windowDef_t | window | name, group, background as above |
| 0xb0 | string [-1] | font | |
| 0xb4, 0xb8 | s32 | fullScreen, ui3dWindowId | ui3dWindowId -1 in default_menu |
| 0xbc | s32 | itemCount | count for items |
| 0xc0..0x123 | | fontIndex, cursorItem[4], fade/slide/rect fields | INFERRED names (PC fields shifted by +24 after cursorItem) |
| 0x124 | ptr [nz] | onOpen (GenericEventHandler chain) | align 4 |
| 0x128 | ptr [nz] | onKey (ItemKeyHandler chain) | align 4 |
| 0x12c | ExpressionStatement (16) | visibleExp | |
| 0x140 | u64 x 2 | showBits, hideBits | INFERRED |
| 0x150 | string [-1] | allowedBinding | |
| 0x154 | string [-1] | soundName | |
| 0x180 | ExpressionStatement | rectXExp | INFERRED name |
| 0x190 | ExpressionStatement | rectYExp | INFERRED name |
| 0x1a0 | ptr [nz] | items | align 4, LS 4 x itemCount; each element [nz] -> align 8, LS 280 itemDef_s |

### 6.4 itemDef_s (280, VIRTUAL, align 8)

| Off | Type | Field | Notes |
|---|---|---|---|
| 0x0 | windowDef_t | window | |
| 0xb0 | s32 | type | selects typeData |
| 0xb4, 0xb8 | s32 | dataType, imageTrack | |
| 0xbc | string [-1] | dvar | |
| 0xc0 | string [-1] | dvarTest | |
| 0xc4 | string [-1] | enableDvar | |
| 0xc8 | s32 | dvarFlags | |
| 0xcc | ptr [nz] | typeData | see 6.5 |
| 0xd0 | ptr | parent | never loaded |
| 0xd4 | ptr [nz] | rectExpData | align 4, LS 64 = 4 ExpressionStatements |
| 0xd8 | ExpressionStatement | visibleExp | |
| 0xe8 | u64 x 2 | showBits, hideBits | |
| 0xf8 | ExpressionStatement | textAlignYExp | |
| 0x108 | s32 | ui3dWindowId | |
| 0x10c | ptr [nz] | onEvent | align 4, GenericEventHandler chain |
| 0x110 | ptr [nz] | animInfo | align 4, LS 236 UIAnimInfo |
| 0x114 | | padding to 280 | |

Load order: window, dvar, dvarTest, enableDvar, typeData, rectExpData, visibleExp,
textAlignYExp, onEvent, animInfo.

### 6.5 typeData by item type (0x255598)

| Item type | typeData points to |
|---|---|
| 1, 3, 4, 5, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 18, 20 | textDef_s (140) |
| 2, 6 | 16-byte ExpressionStatement (imageDef_s / ownerDrawDef_s) |
| 19, 21 | focusItemDef_s (8) |
| other | nothing |

All typeData targets: align 4, header read with LS.

textDef_s (140): `+0x0 textRect[4]` (96, INFERRED: four per-client rects), `+0x60..0x7f` PC
fields alignment..textStyle, `+0x80 string [-1] text`, `+0x84 ptr [nz] textExpData` (align 4,
LS 16 ExpressionStatement), `+0x88 ptr [nz] textTypeData`:

- item type in {3, 4, 5, 7, 8, 9, 10, 11, 12, 13, 14, 16, 20, 21, 30}: focusItemDef_s (8);
- item type 15: gameMsgDef_s (LS 8, no pointers);
- otherwise nothing.

focusItemDef_s (8, PS3): `+0x0 ptr [nz] onKey` (align 4, ItemKeyHandler chain),
`+0x4 ptr [nz] focusTypeData` by item type (0x249df0):

| Item type | focusTypeData | Size | Pointers |
|---|---|---|---|
| 4 | listBoxDef_s | 700 | below |
| 10 | multiDef_s | 396 | dvarList[32] then dvarStr[32]: 64 strings [-1] at +0..+0xfc (same as PC) |
| 5, 7, 8, 9, 12, 13, 14, 16, 30 | editFieldDef_s | 48 | none |
| 11 | enumDvarDef_s | 4 | `+0 string enumDvarName` (ELF only; no instance seen) |

listBoxDef_s (700): `+0x3c s32 numColumns` (INFERRED name, PC +0x1c), `+0x2a0`, `+0x2a4`,
`+0x2a8` material asset ptrs (selectIcon, backgroundItemListbox, highlightTexture),
`+0x2b0 ptr [nz] rows` (align 4, LS 24 x `+0x2b4` maxRows). MenuRow (24): `+0 ptr [nz] cells`
(align 4, LS 12 x numColumns), `+4 ptr [nz] eventName` (align 1, LS 32), `+8 ptr [nz]
onFocusEventName` (align 1, LS 32), order cells (each cell `+8 ptr [nz] stringValue`, align 1,
LS cell `+4` maxChars), eventName, onFocusEventName.

### 6.6 Shared menu sub-structs (same sizes and offsets as PC)

| Struct | Size | Pointers, in load order |
|---|---|---|
| ExpressionStatement | 16 | `+0 string filename`; `+0xc ptr [-1] rpn` align 4, LS 12 x `+8` numRpn; per rpn: if `+0 type == 0` (constant) and `+4 dataType == 2` (string): `+8 string` |
| GenericEventHandler | 12 | read with LS 12; `+0 string name`; `+4 ptr [nz] eventScript` (align 4); `+8 ptr [nz] next` (align 4, LS 12, loop) |
| GenericEventScript | 44 | read with LS 44; `+0 ptr [nz] prerequisites` (align 4, LS 16 ScriptCondition, then while `+0xc next != 0`: align 4, LS 16); `+4` ExpressionStatement condition; `+0x1c string action`; `+0x28 ptr [nz] next` (align 4, LS 44, loop) |
| ItemKeyHandler | 12 | read with LS 12; `+4 ptr [nz] keyScript` (align 4, GenericEventScript); `+8 ptr [nz] next` (align 4, LS 12, loop) |
| rectData_s | 64 | 4 ExpressionStatements |
| UIAnimInfo | 236 | `+4 ptr [nz] animStates` align 4, LS 4 x `+0` animStateCount; each element [nz]: align 4, LS 108 animParamsDef_t: `+0 string name`, `+0x68 ptr [nz] onEvent` (align 4, handler chain). The two embedded animParamsDef_t at +8 and +0x74 are not followed |

Example, patch_mp asset #735 at 0x237f5d, `ui_mp/scriptmenus/class_splitscreen.menu`: MenuList
`ffffffff 00000001 ffffffff` + name; menus array `ffffffff` at 0x237f92; menuDef_t (LS 424,
TEMP, align 8) at 0x237f96 `ffffffff 00000000 00000000 44200000 43f00000 ...`; name
`class_splitscreen`; onOpen handler LS 12 at 0x238150 `80154029 ffffffff ffffffff` (shared
name, inline script, inline next), its script LS 44 at 0x23815c, and so on.

## 7. image (10) and where pixels live

Loaders: Ptr 0x248b40 with the struct loader inline at 0x248be8; pixels 0x2365e8; pixel size
0x736ec0. GfxImage is 0x70 bytes on PS3 (PC 52; the PC layout does not apply). R3's
`textures.md` section 2 documents the same fields from the data side; the two agree.

| Off | Size | Field | Notes |
|---|---|---|---|
| 0x0 | 24 | CellGcmTexture | format u8, mipmap u8, dimension u8, cubemap u8, remap u32, width u16, height u16, depth u16, location u8 (+0xe), pad u8, pitch u32, offset u32 |
| 0x18 | 1 | mapType | |
| 0x19 | 1 | semantic | |
| 0x1a | 1 | category | |
| 0x1b | 1 | deferred flag (R3: delayLoadPixels) | selects a deferred block, below |
| 0x1c | 4 | pixel byte count | equals what 0x736ec0 computes; 0 for streamed images |
| 0x20..0x2b | | sizes and streaming bytes | see R3 |
| 0x2c | 4 | pixels [nz] | any non-zero value means "pixels follow" (0x2365e8 tests only `!= 0`) |
| 0x30..0x67 | | streaming records (streamed images only) | INFERRED; R3 section 2.4 |
| 0x68 | 4 | string [-1] name | |
| 0x6c | 4 | hash | function unknown (not djb2, FNV-1a, sdbm or x31 over the name) |

Load order: header LS 0x70 (TEMP), push VIRTUAL, name, then pixels:

```
blk = (location == 1 ? LARGE_RUNTIME : PHYSICAL_RUNTIME) if [+0x1b] else (location == 1 ? LARGE : PHYSICAL)
push(blk); if [+0x2c] != 0: align 128, LS size; pop       (0x236618..0x2366a4)
pop (VIRTUAL); register; pop (TEMP)
```

So pixels are in exactly one of three places (CONFIRMED by the walk, all images in 9 zones):

| [+0x2c] | [+0x1b] | Pixels |
|---|---|---|
| non-zero | 0 | inline in the zone, straight after the name, in PHYSICAL (location 0) or LARGE |
| non-zero | 1 | in the deferred tail at the end of the zone (PHYSICAL_RUNTIME / LARGE_RUNTIME), in the order the images were loaded (`xfile.md` section 3) |
| 0 | any | not in the zone: streamed from a `.pak` next to the `.ff` (R3 section 2.4) |

Counts of loaded GfxImage headers in traced assets: mp_nuked 50 deferred, 321 streamed, 15
empty; common_mp 224 inline, 136 streamed; ui_mp 376 inline; patch_mp 17 deferred, 29
streamed. Location 1 (LARGE) never occurs. Size: the walker uses `[+0x1c]` and matched every
image in 9 zones, so `[+0x1c]` = 0x736ec0(image) wherever pixels are present.

Example, patch_mp asset #5 at 0x488a, `$white` (1x1 A8R8G8B8, deferred):

```
0000488a: 8501 0200 0001 aae4 0001 0001 0001 0000   format 0x85, 1 mip, 2D, remap, 1 x 1 x 1, location 0
0000489a: 0000 0000 0000 0000 0300 0101 0000 0080   pitch 0, offset 0 | 2D, semantic 0, category 1, deferred 1 | size 0x80
000048aa: 0001 0001 0001 0000 0000 0000 ffff ffff   ... pixels -1
000048ea: ...       ...       ffff ffff 5762 9243   name -1, hash 0x57629243
000048fa: 2477 6869 7465 00                         "$white\0"; 128 pixel bytes go to the tail
```

Pack link (INFERRED): patch_mp contains a packindex asset `img_patch` with `+8 = 9`; its 25
streamed images carry 9 in the byte at +0x3c, while images in zones without a packindex
carry 1 there. That suggests +0x3c is a pack id; R3 owns the record layout.

## 8. material (6)

Loaders: Ptr 0x249498, struct 0x248f20, texture union 0x248e28, water 0x248ce8, state bits
0x2366d8. Material is 128 bytes on PS3 (PC 192).

| Off | Size | Field | Notes |
|---|---|---|---|
| 0x0 | 32 | MaterialInfo | read with `LS(0, 32)` at 0x248f74; `+0 string [-1] name`, `+4 gameFlags`, `+8 pad`, `+9 sortKey`, `+0xa`/`+0xb` atlas rows/columns, `+0x10` u64 drawSurf, `+0x18` surfaceTypeBits, `+0x1c` u32 (INFERRED: layeredSurfaceTypes) |
| 0x20 | 71 | stateBitsEntry[71] | one byte per technique slot (71 slots, section 9); 0xff = none |
| 0x67 | 1 | textureCount | (0x249294 `lbz r24,103`) |
| 0x68 | 1 | constantCount | |
| 0x69 | 1 | stateBitsCount | (0x2490a4 `lbz r26,105`) |
| 0x6a..0x6f | | stateFlags, cameraRegion, ... | INFERRED names |
| 0x70 | 4 | techniqueSet | techset asset ptr (2.1) |
| 0x74 | 4 | textureTable [-1] | align 4, LS 16 x textureCount |
| 0x78 | 4 | constantTable [-1] | align 16, LS 32 x constantCount |
| 0x7c | 4 | stateBitsTable [-1] | align 4, LS 4 x stateBitsCount (pointers) |

Load order: name, techniqueSet, textureTable (then each texture's image), constantTable,
stateBitsTable (then each state-bits object).

MaterialTextureDef (16, as PC): `+0 nameHash`, `+4 nameStart`, `+5 nameEnd`, `+6 samplerState`,
`+7 semantic`, `+8 isMatureContent`, `+0xc u`: if semantic == 11 (water): `ptr [-1]` water_t
(align 4, LS 72), else an image asset ptr. water_t (72, ELF only, no instance seen): `+4`,
`+8`, `+0xc` ptrs [nz] (each align 4, LS 4 x M x N with M at +0x10, N at +0x14), then
`+0x44` image asset ptr.

MaterialConstantDef (32, as PC): nameHash, name[12], literal vec4.

stateBitsTable elements are pointers: each element: push TEMP; if -1/-2: align 4 (TEMP),
[-2: insert], LS 8 (GfxStateBits); pop. The 8 bytes are handed to 0x7454a0 (INFERRED:
converted to a runtime state object), which is why they live in TEMP.

Example, patch_mp asset #4 at 0x4775, `$default`:

```
00004775: ffff ffff 0000 0000 0004 0101 0000 0000   name -1, gameFlags 0, pad 0, sortKey 4, atlas 1x1
00004795: ffff ffff 00ff ffff ffff ffff ...         stateBitsEntry[71] (slot 4 = 0)
000047d5: ffff ffff ffff ff01 0001 0003 0000 0000   ... textureCount 1, constantCount 0, stateBitsCount 1
000047e5: 8000 0f45 ffff ffff 0000 0000 ffff ffff   techset = offset (block 4, 0xf44), textures -1, constants 0, stateBits -1
000047f5: "$default\0"
000047fe: a0ab1041 63700102 00000000 ffffffff        texture def: hash, semantic 2, image -1
0000480e: 9e01 0200 ... (GfxImage 0x70, TEMP)        name = offset 0x800038fb, 1024 pixel bytes deferred
0000487e: ffffffff                                    stateBits[0] = -1
00004882: 18124812 e00e0002                           GfxStateBits (TEMP)
```

## 9. techset (9), vertexshader (8), pixelshader (7)

Loaders: techset Ptr 0x248878, struct 0x2486d8; MaterialTechnique 0x2483c8; MaterialPass
0x2480c0; argument union 0x237490; vertex shader Ptr 0x247f28, program 0x2368e0; pixel shader
Ptr 0x244178, struct 0x244038. All PS3-specific (PC techset 528 bytes with 130 techniques).

MaterialTechniqueSet (292):

| Off | Field | Notes |
|---|---|---|
| 0x0 | string [-1] name | |
| 0x4 | 4 bytes | INFERRED: worldVertFormat u8, unused u8, techsetFlags u16 |
| 0x8 | techniques[71] | each `[-1]`: align 4, then MaterialTechnique |

MaterialTechnique (variable, contiguous): LS 8 header `+0 string name`, `+4 u16 flags`,
`+6 u16 passCount`; then LS 24 x passCount passes; then each pass's pointers; then the name
string (name is loaded last).

MaterialPass (24):

| Off | Field | Notes |
|---|---|---|
| 0x0 | vertexDecl [-1] | align 4, LS 34 (MaterialVertexDeclaration, opaque here) |
| 0x4 | vertexShader | asset ptr (type 8): header 16 |
| 0x8 | pixelShader | asset ptr (type 7): header 12 |
| 0xc | u8 x 3 | perPrimArgCount, perObjArgCount, stableArgCount |
| 0xd..0x13 | | customSamplerFlags and 4 bytes, INFERRED |
| 0x14 | args [nz] | align 4, LS 8 x (sum of the three counts); per argument (`+0 u16 type`, `+2 u16 dest`, `+4 u`): type 1 or 7 (literal constants) with `u == -1`: align 4, LS 16 |

MaterialVertexShader (16): `+0 string name`, `+4 ptr [-1] program` (align 16, LS 4 x u16 at
+0xe), `+8..+0xd` INFERRED. MaterialPixelShader (12): `+0 string name`, `+4 ptr [-1] program`
(align 16, LS 4 x u16 at +0xa), `+8 u16` INFERRED. Program blobs start with `VS0u` / `PS0u` and
their own size at +4.

Example, patch_mp asset #10 at 0x5490, techset `effect_nofog`: techniques[4] = -1, [5] and [6]
= offset 0x800043ed (shared). Technique at 0x55c1 `ffffffff 00880001` (1 pass), pass at 0x55c9
`80003935 ffffffff ffffffff 01010100 02000000 ffffffff` (shared decl, inline VS, inline PS,
1+1+1 args); vertex shader header at 0x55e1 `ffffffff ffffffff 00000000 00310037` +
`pimp_shader_effectsimple_1bdcd991.hlsl`, program 220 bytes (0x37 x 4) at 0x5618 `56533075
000000dc ...`; pixel shader at 0x56f4 `ffffffff ffffffff 08000014` + name + 80-byte program;
3 args at 0x5777; technique name `pimp_technique_effectsimple_2b26d39a` last.

## 10. xanim (4)

Loaders: Ptr 0x244db8, struct 0x244728, delta part 0x23bfb0, translation frames 0x235dd0,
quaternion frames 0x23a588. XAnimParts is 104 bytes like PC and every pointer and data count
is at the PC offset, except the bone counts (below).

| Off | Field | Notes |
|---|---|---|
| 0x0 | string [-1] name | |
| 0x4 / 0x6 / 0x8 / 0xa / 0xc | u16 dataByteCount, dataShortCount, dataIntCount, randomDataByteCount, randomDataIntCount | |
| 0xe | u16 numframes | <= 255 selects byte indices |
| 0x10..0x13 | bLoop, bDelta, bLeftHandGripIK, bStreamable | |
| 0x14 | u32 streamedFileSize | |
| 0x18 | u8 boneCount[12] | PS3: names count is `boneCount[11]` at +0x23 (0x2447d8 `lbz r26,35`); PC has boneCount[10]. INFERRED names for the extra classes |
| 0x24 | u8 notifyCount | (0x24497c `lbz r27,36`) |
| 0x25, 0x26 | assetType, isDefault | INFERRED |
| 0x28 | u32 randomDataShortCount | |
| 0x2c | u32 indexCount | |
| 0x30..0x3f | framerate, frequency, primedLength, loopEntryTime | |
| 0x40 | names [nz] | align 2, LS 2 x boneCount[11] (scrstr) |
| 0x44 | dataByte [nz] | align 1, LS dataByteCount |
| 0x48 | dataShort [nz] | align 2, LS 2 x dataShortCount |
| 0x4c | dataInt [nz] | align 4, LS 4 x dataIntCount |
| 0x50 | randomDataShort [nz] | align 2, LS 2 x randomDataShortCount |
| 0x54 | randomDataByte [nz] | align 1, LS randomDataByteCount |
| 0x58 | randomDataInt [nz] | align 4, LS 4 x randomDataIntCount |
| 0x5c | indices [nz] | numframes <= 255: align 1, LS indexCount; else align 2, LS 2 x indexCount |
| 0x60 | notify [nz] | align 4, LS 8 x notifyCount (`u16 name scrstr`, `f32 time`) |
| 0x64 | deltaPart [nz] | align 4, below |

Load order: name, names, notify, deltaPart, dataByte, dataShort, dataInt, randomDataShort,
randomDataByte, randomDataInt, indices.

XAnimDeltaPart (LS 8): `+0 trans [nz]`, `+4 quat [nz]`, loaded trans then quat:

- trans: align 4, LS 4 header (`u16 size`, `u8 smallTrans`, pad). size == 0: LS 12 (frame0
  vec3). size > 0: LS 28 (mins[3], size[3], `+24 frames ptr`), then the inline index array LS
  (size+1) bytes (numframes <= 255) or 2 x (size+1); then frames [nz]: smallTrans ? (align 1,
  LS 3 x (size+1)) : (align 4, LS 6 x (size+1)).
- quat: align 4, LS 4 header (`u16 size`, pad). size == 0: LS 4 (frame0, 2 x s16). size > 0:
  LS 4 (`+0 frames ptr`), inline index array as above, then frames [nz]: align 4, LS 4 x
  (size+1).

The inline index arrays and the 0-size Load_Stream that precedes them are PS3 specifics.

Example, patch_mp asset #695 at 0x14a2de, `pb_briefcase_stand_turn_l90`:

```
0014a2de: ffff ffff 02c7 002b 005b 0114 00d0 0014   name -1; byte 711, short 43, int 91, rbyte 276, rint 208; 20 frames
0014a2ee: 0101 0100 0000 0000 0000 0e12 0222 0009   loop, delta, gripIK; streamed 0; boneCount[0..7]
0014a2fe: 0001 3a44 0402 0000 0000 0405 0000 0000   boneCount[8..11] (names = 0x44 = 68); notify 4; rshort 0x405; indexCount 0
0014a31e: ffff ffff ... ffff ffff 0000 0000         pointers +0x40..+0x58 -1, indices 0
0014a33e: ffff ffff ffff ffff "pb_briefcase_stand_turn_l90\0"   notify -1, deltaPart -1
```

then names LS 136 at 0x14a362, notify LS 32 at 0x14a3ea, delta part LS 8 `00000000 ffffffff`
(no trans), quat header `00100000` (size 16), frames pointer `ffffffff`, 17 index bytes, 68
frame bytes, then the six data arrays (711, 86, 364, 2058, 276, 832 bytes).

## 11. fx (30), impactfx (31)

Loaders: fx Ptr 0x24d748, FxEffectDef 0x24d498, FxElemDef 0x24d0a8, visuals 0x24cc90, single
visual 0x24ca90, trail 0x236fb0; impactfx Ptr 0x24e2f8, struct 0x24e140. FxEffectDef (60),
FxElemDef (292), FxTrailDef (28), FxElemVelStateSample (96), FxElemVisStateSample (48),
FxTrailVertex (20) have the PC sizes and every pointer is at the PC offset, so the PC field
layout applies (OpenAssetTools T5_Assets.h).

FxEffectDef (60): `+0 string name`, `+0x10/+0x14/+0x18 s32 elemDefCountLooping/OneShot/
Emission`, `+0x1c elemDefs [nz]` align 4, LS 292 x (sum of the three counts).

FxElemDef pointers, in load order:

| Off | Field | Load |
|---|---|---|
| 0xbc | velSamples [nz] | align 4, LS 96 x (u8 velIntervalCount at +0xba + 1) |
| 0xc0 | visSamples [nz] | align 4, LS 48 x (u8 visStateIntervalCount at +0xbb + 1) |
| 0xc4 | visuals | by u8 elemType (+0xb8) and u8 visualCount (+0xb9): type 11 (decal): `[nz]` align 4, LS 8 x visualCount, each 2 material ptrs; else visualCount > 1: `[nz]` align 4, LS 4 x visualCount, each one visual; else the field itself is one visual |
| 0xe0, 0xe4, 0xe8, 0xfc | effectOnImpact, effectOnDeath, effectEmitted, (fourth FxEffectDefRef) | strings (effects referenced by name) |
| 0x100 | trailDef [nz] | align 4, LS 28; `+0x10 verts [nz]` align 4, LS 20 x `+0xc`; `+0x18 inds [nz]` align 2, LS 2 x `+0x14` |
| 0x118 | spawnSound | string |

One visual, by elemType: 7 (model) xmodel asset ptr; 10 (sound) and 12 (runner) string;
8, 9 (lights) nothing; all others material asset ptr.

FxImpactTable (8): `+0 string name`, `+4 table [nz]` align 4, LS 21 x 140; each entry is 35
FxEffectDef asset ptrs (nonflesh[31], flesh[4]).

Example, patch_mp asset #683 at 0x13d445, `misc/fx_theater_mode_camera_head`:
`ffffffff 00ff0000 000002a1 7fffffff 00000001 00000000 00000000 ffffffff` (1 looping elem,
elemDefs -1), elem LS 292 at 0x13d4a2, then vel samples LS 192 (2 x 96), vis samples LS 96,
then a material reference.

## 12. sound (11), sound_patch (12), snddriverglobals (29)

Loaders: SndBank Ptr 0x2466a0, struct 0x246310; alias list 0x246030; alias 0x245df0;
SoundFile 0x245b70; snd_asset 0x236388; PrimedSound 0x244290; SndPatch 0x2467b0;
SndDriverGlobals 0x2443a0. SndBank (40), snd_alias_list_t (20), snd_alias_t (84), SoundFile
(8), LoadedSound (60), PrimedSound (12), SndPatch (20), SndDriverGlobals (52) and its element
types match PC; StreamedSound is 24 bytes on PS3 (PC 8).

SndBank (40), load order:

| Off | Field | Load |
|---|---|---|
| 0x0 | string name | e.g. `mpl_common.english` |
| 0x4 | aliasCount | |
| 0x8 | alias [nz] | align 4, LS 20 x aliasCount, then each list |
| 0xc | aliasIndex [nz] | align 4, LS 4 x aliasCount |
| 0x10 | packHash | equals the u32 at +0xc of the sound pak header (0x2e63 for `snd.english.pak` and the common_mp bank) |
| 0x14 | packLocation | 1 in common_mp |
| 0x18 / 0x1c | radverbCount / radverbs [nz] | align 4, LS 96 x count |
| 0x20 / 0x24 | snapshotCount / snapshots [nz] | align 4, LS 348 x count |

snd_alias_list_t (20): `+0 string name`, `+8 head [nz]` align 4, LS 84 x `+0xc count`, then
each alias. snd_alias_t (84): `+0 string name`, `+8 string subtitle`, `+0xc string
secondaryname`, `+0x10 soundFile [-1]` align 4, LS 8.

SoundFile (8): `+0 u`, `+4 u8 type`, `+5 u8 exists`.

- type 1 (loaded): `u [-1]`: align 4, LS 60 LoadedSound: `+0 string name`; `+0x30 seek_table
  [nz]` align 4, LS 4 x `+0x2c`; push LARGE, push PHYSICAL, `+0x38 data [nz]` align 2048, LS
  `+0x34` data_size (block PHYSICAL), pop, pop.
- otherwise (streamed): `u [-1]`: align 4, LS 24 StreamedSound: `+0 string filename`
  (e.g. `english\sound\vox\...\breathing_better.mp3`), `+4 primeSnd [-1]` align 4, LS 12:
  `+0 string name`, push PHYSICAL, `+4 buffer [nz]` align 2048, LS `+8`, pop. StreamedSound
  `+8..+0x17`: four u32, INFERRED (seen `00005c40 000170e4 01000a8a bb800100`: plausibly
  size, offset, pack id + entry, 48000 Hz + channels). The audio itself is not in the zone;
  `snd.all.pak` and `snd.english.pak` sit next to the zones (INFERRED container).

SndPatch (20): `+0 string name`, `+8 elements [nz]` align 4, LS 4 x `+4`, `+0x10 files [-1]`
align 4, LS 8 x `+0xc`, each a SoundFile as above.

SndDriverGlobals (52): `+0 string name`, then six arrays [nz], each align 4, in this order:
groups (+8, 80 x `+4`), curves (+0x10, 100 x `+0xc`), pans (+0x18, 60 x `+0x14`),
snapshotGroups (+0x20, 32 x `+0x1c`), contexts (+0x28, 40 x `+0x24`), masters (+0x30, 176 x
`+0x2c`). Example code_post_gfx_mp asset #203 at 0x2d0a9 (`singleton`, 36 groups = LS 2880).

## 13. weapon (26)

Loaders: Ptr 0x253e30, WeaponVariantDef 0x253790, WeaponDef 0x252308, string helper 0x236030,
FlameTable 0x249810. WeaponVariantDef (228), WeaponDef (2056) and FlameTable (476) have the PC
sizes and every pointer is at the PC offset, so the PC field layout applies. Zones use type 26
only (weapondef 27 and weaponvariant 28 have no loader).

WeaponVariantDef load order: `+0 string szInternalName`; `+8 weapDef [-1]` (align 4, LS 2056,
WeaponDef below); `+0xc string szDisplayName`; `+0x14 string szAltWeaponName`; `+0x10
szXAnims [-1]` (align 4, LS 264 = 66 string pointers, each a string naming an xanim);
`+0x18 hideTags [-1]` (align 2, LS 64 = 32 scrstr); `+0x40 string szAmmoName`; `+0x48 string
szClipName`; material asset ptrs `+0x8c`, `+0x90`, `+0x94`.

WeaponDef load order (offsets from 0x252308; names from PC):

| Offsets | Fields | Load |
|---|---|---|
| 0x0 | szOverlayName | string |
| 0x4 | gunXModel | [-1] align 4, LS 64, 16 xmodel ptrs |
| 0x8 | handXModel | xmodel ptr |
| 0xc | szModeName | string |
| 0x10, 0x14 | notetrackSoundMapKeys / Values | each [-1] align 2, LS 40 (20 scrstr) |
| 0x3c | parentWeaponName | string |
| 0x74, 0x78 | view/world flash effect | fx ptrs |
| 0x7c..0x170 | 62 sound names (pickupSound .. adsZoomSound) | strings |
| 0x174 | bounceSound | [-1] align 4, LS 124 (31 surface types), 31 strings |
| 0x178, 0x17c, 0x180 | stand/crouch/prone mounted weapdef | strings |
| 0x190..0x19c | 4 shell-eject effects | fx ptrs |
| 0x1a0, 0x1a4 | reticleCenter, reticleSide | material ptrs |
| 0x30c | worldModel | [-1] align 4, LS 64, 16 xmodel ptrs |
| 0x310..0x31c | worldClipModel, rocketModel, mountedModel, additionalMeleeModel | xmodel ptrs |
| 0x320, 0x330 | hudIcon, ammoCounterIcon | material ptrs |
| 0x34c | szSharedAmmoCapName | string |
| 0x374 | explosionTag | scrstr (no stream effect) |
| 0x394..0x3a8, 0x48c | spin/stop/stack sounds | 7 strings |
| 0x578, 0x328 | killIcon, then indicatorIcon | material ptrs |
| 0x58c, 0x590 | spawned-grenade / dual-wield weapon names | strings |
| 0x5e8 | xmodel ptr | |
| 0x5f0..0x618 | 6 projectile effects | fx ptrs |
| 0x61c..0x628 | 4 projectile sounds | strings |
| 0x650, 0x654 | parallelBounce, perpendicularBounce | each [-1] align 4, LS 124 (31 floats) |
| 0x658, 0x674 | fx ptrs | |
| 0x678 | projIgnitionSound | string |
| 0x714 | aiVsAiAccuracyGraphName | string |
| 0x71c, 0x724 | aiVsAi knots, original knots | each [-1] align 4, LS 8 x s32 at 0x72c |
| 0x718 | aiVsPlayerAccuracyGraphName | string |
| 0x720, 0x728 | aiVsPlayer knots, original knots | each [-1] align 4, LS 8 x s32 at 0x730 |
| 0x784, 0x788, 0x79c | use/drop hint, script | strings |
| 0x7bc | locationDamageMultipliers | [-1] align 4, LS 76 (19 floats) |
| 0x7c0, 0x7c4, 0x7c8 | rumbles | strings |
| 0x7e8, 0x7ec | flameTableFirstPerson / ThirdPerson | strings |
| 0x7f0, 0x7f4 | flame table pointers | each [-1] align 4, LS 476 FlameTable: `+0x1a8 string name`, materials +0x1ac..+0x1c8 (8), strings +0x1cc..+0x1d8 |
| 0x7f8, 0x7fc | fx ptrs | |

Example, patch_mp asset #1291 at 0x2d61a4, `famas_elbit_gl_dualclip_mp`: variant header
`ffffffff 00000000 8029bf99 8028c61d ffffffff 80291177 ffffffff 00000000 ...` (weapDef =
offset, display name = offset, szXAnims -1, alt name offset, hideTags -1), name, szXAnims LS 264
at 0x2d62a3, hideTags LS 64 at 0x2d63ab, three material references. patch_mp asset #805 at
0x28df15 carries an inline WeaponDef (4415 bytes in total).

## 14. font (22), ddl (42), emblemset (45), glasses (43), packindex (40), xGlobals (41), texturelist (44)

All except packindex and texturelist have the PC sizes and the PC pointer offsets.

Font_s (24, loader 0x249590): `+0 string fontName`, `+4 pixelHeight`, `+8 glyphCount`, `+0xc
material`, `+0x10 glowMaterial` (material ptrs), `+0x14 glyphs [-1]` align 4, LS 24 x
glyphCount. Example code_post_gfx_mp asset #476 at 0x30bfb7 `fonts/bigFont`: `ffffffff
00000039 000000c9 80051a75 80051ab1 ffffffff`, 201 glyphs (LS 4824).

ddlRoot_t (8, loaders 0x247480 / 0x247008 / 0x2458b0): `+0 string name`, `+4 ddlDef [nz]`
align 4, LS 28. ddlDef_t (28): `+8 structList [nz]` (align 4, LS 16 x `+0xc`), `+0x10 enumList
[nz]` (align 4, LS 12 x `+0x14`), `+0x18 next [nz]` (align 4, recursive), loaded in that order.
ddlStructDef_t (16): `+0 string name`, `+0xc members [nz]` align 4, LS 48 x `+8`, each member
`+0 string name`. ddlEnumDef_t (12): `+0 string name`, `+8 members [nz]` align 4, LS 4 x `+4`,
each a string. Example patch_mp asset #93 at 0x9fc06 `ddl_mp/file_share_public.ddl`.

EmblemSet (44, loader 0x24a7f8, no name; R1: name is constant "emblemset"), all [nz], align 4
unless stated: `+8 layers` 12 x `+4`; `+0x10 categories` 8 x `+0xc` (2 strings each);
`+0x18 icons` 40 x `+0x14` (image ptr, string); `+0x20 backgrounds` 24 x `+0x1c` (material
ptr, string); `+0x28 backgroundLookup` align 2, 2 x `+0x24`. Example patch_mp asset #1335 at
0x2f9d95 (126 904 bytes).

Glasses (56, loader 0x24db80): `+0 string name`, `+8 glasses [nz]` align 4, LS 124 x `+4`
numGlasses; per Glass: `+0 glassDef [-1]` align 4, LS 60 GlassDef (`+0 string name`, materials
+0x1c, +0x20, +0x24, strings +0x28, +0x2c, +0x30, fx +0x34, +0x38), `+0x40 outline [nz]`
align 4, LS 8 x u8 `+0x3d`; then `+0xc workMemory [nz]`: push RUNTIME, align 32, LS `+0x10`
bytes (no file bytes: block 1 is zero-filled), pop. PC tools treat workMemory as never loaded;
on PS3 it reserves RUNTIME memory. Example mp_nuked asset #527 at 0x304874a: `ffffffff 0000003e
ffffffff ffffffff 003b0f40 ...` (62 glasses, 3 870 528 bytes of RUNTIME work memory).

PackIndex (12 on PS3, PC 28; Ptr 0x247da0, struct inline): `+0 string name`, `+4 u32`, `+8 u32`;
no entries are loaded. Example patch_mp asset #0 at 0x3921: `ffffffff 00000000 00000009
"img_patch\0"`. INFERRED: +8 is the pack id used by streamed images (section 7).

XGlobals (40, Ptr 0x247c08, struct inline): `+0 string name`, rest plain data (PC field names
INFERRED). No instance in the nine zones; layout from the ELF only.

TextureList (8, PS3 only, loader 0x235c40, no name): `+0 u32 count`, `+4 entries [nz]` align 4,
LS 4 x count. Example mp_nuked asset #526 at 0x3046482: `000008b0 ffffffff` then 2224 u32
starting `00000001 00000002 ...`. INFERRED meaning (texture index list for the level).

## 15. Types with no loader

ui_map (21), weapondef (27), weaponvariant (28), aitype (32), mptype (33), mpbody (34),
mphead (35), character (36) and xmodelalias (37) have names but no case in Load_XAssetHeader
(`xfile.md` section 5); xmodelpieces (0) is converted as an offset only. None occurs in the
nine zones.

## 16. Resizing rawfile, stringtable and localize

Fields to recompute inside the asset:

| Type | Change | Recompute |
|---|---|---|
| rawfile, plain | new text | `len` = byte length; buffer = text + NUL (LS len+1) |
| rawfile, .gsc/.csc | new script | data = text + NUL; compressed = zlib level 6; buffer = u32 BE len(data), u32 BE len(compressed), compressed, one extra byte (0 is accepted; original files have `data[len]` there); `len` = 8 + len(compressed) |
| stringtable | cells, rows, columns | columnCount, rowCount; each cell hash (djb2 lower-case, section 4); cellIndex re-sorted by signed hash; cell count = rows x columns, max 32767 for the s16 index |
| localize | new text | nothing in the struct: both fields are plain NUL-terminated strings |

What makes resizing hard is outside the asset: every offset pointer in the zone (strings,
techsets, materials, images, fx, xmodels ...) encodes `((block << 29) | position) + 1` into a
block (`xfile.md` section 4), and the VIRTUAL position of everything loaded after the edited
asset moves by the size change (rounded by the alignments). A writer must therefore:

1. Walk the whole zone, tracking all seven block positions with the push/pop/alignment rules
   of section 2 and `xfile.md` section 3, and record the target position of every offset
   pointer and the position of every object.
2. Apply the edit, re-walk to compute the new positions, and rewrite every offset pointer whose
   target moved, including pointers in assets of other types and in the asset list.
3. Update the XFile header: `size` and `blockSize[4]` (VIRTUAL); `blockSize[0]` (TEMP) only if
   the asset header grows beyond the previous high-water mark.
4. Keep shared strings shared or emit them inline (-1): changing a string that other assets
   reference by offset changes it for all of them.

The scratch walker already tracks positions exactly (section 17), so step 1 is mechanical.

## 17. Walker and coverage

The scratch walker (`walker.py`, not in OpenT5) implements every content type of this document
and the asset-pointer, string and alignment rules. It runs per asset from the block positions
recorded by the loader harness and checks end offset, all seven block positions, and (for the
traced assets) the full event sequence. Results:

| Zone | Content assets walked exactly | Content-type bytes | + deferred bytes those assets queued | + header, script strings, asset list | Not walked |
|---|---|---|---|---|---|
| patch_mp | 1303 of 1303 | 83.45% | 95.72% | 96.11% | xmodel 32, physpreset 1, physconstraints 1 (R2a) |
| ui_mp | 670 of 670 | 99.99% | 99.99% | 100.00% | none |
| patch_ui_mp | 130 of 130 | 51.36% | 99.99% | 100.00% | none |
| patch | 424 of 424 | 96.43% | 99.10% | 99.60% | physpreset, physconstraints |
| code_post_gfx_mp | 8568 of 8568 | 98.98% | 98.98% | 99.67% | R2a types |
| common_mp | 6959 of 6960 | 69.30% | 69.30% | 69.39% | 325 xmodels; 1 weapon with an inline XModel |
| mp_nuked | 213 of 215 | 13.49% | 16.96% | 16.98% | map types (R2a); 2 fx with inline XModels |
| mp_firingrange | 244 of 244 | 19.51% | 20.05% | 20.08% | map types (R2a) |
| zombie_theater | 576 of 581 | 17.00% | 23.97% | 24.00% | map types (R2a); 3 weapons, 2 fx with inline XModels |

The seven failing assets all stop at the first inline XModel (R2a's type); with an XModel
loader they need nothing else from this document.

Branches exercised by real data (counts over the nine zones): 29 543 menu items, item data of
types 1, 2, 3, 4, 6, 8, 10, 15, 18, 21; focus data of types 4, 8, 10; 23 936 event scripts;
4 299 script-condition chains; 5 222 anim infos; 6 852 shader passes and techniques; 737
xanim delta parts; 8 133 sound files; 20 flame tables; 51 ddl definitions; 33 `-2` alias
insertions. Not exercised (layout from the ELF only, INFERRED until seen in data): water_t
materials, enumDvarDef_s, edit-field item types other than 8, and fx model visuals with an
inline XModel (code path known, XModel layout is R2a's).

The loader harness itself consumes all nine zones to the last byte (section 1), so it is a
complete reference walker for every type, including R2a's, and can be used to check any future
parser or writer event by event.

## 18. Open items

- GfxImage `+0x6c` hash function: not djb2, FNV-1a, sdbm or x31 of the name. Confirm by
  finding its consumer (image registration 0x2670f0 or the image hash table).
- StreamedSound `+8..+0x17` and SndBank packHash/packLocation semantics: confirm by locating
  an entry in `snd.english.pak` with the recorded values.
- PackIndex `+4`/`+8` and TextureList contents: confirm by finding their users in the ELF.
- menuDef_t `+0xc0..+0x123`, listBoxDef_s `+0x0..+0x3b`, textDef_s `+0x0..+0x5f`: exact field
  names of the split-screen widened arrays (offsets of all loaded fields are confirmed).
- xGlobals: no zone instance; any zone containing one would confirm the 40-byte layout.
- Field names here follow OpenAssetTools (src/Common/Game/T5/T5_Assets.h and
  src/ZoneCode/Game/T5/XAssets/*.txt); if OpenT5 code later copies those definitions, a
  provenance row is needed.
