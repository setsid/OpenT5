# R2a: map asset structs (T5 PS3, BLES01031)

Scope: the asset types that make up a multiplayer map zone and are not content types:
clipMap (col_map_mp 14, col_map_sp 13), ComWorld (com_map 15), GameWorldMp (game_map_mp 17),
MapEnts (map_ents 18, owned by clipMap), GfxWorld (gfx_map 19), GfxLightDef (lightdef 20),
XModel (5), PhysPreset (1), PhysConstraints (2), DestructibleDef (3). Material, image and
techset layouts are R2b's (`structs-content.md` sections 7 to 9); they are referenced here
where map assets embed them. The asset type numbers, header and block list are R1's
(`xfile.md`).

Status: the serialisation order, sizes, alignment and blocks below are CONFIRMED for every map
asset in mp_nuked and mp_firingrange, because they were taken from the game's own loader
executed over those zones, which consumed each zone to the last byte (section 1); and a
table-driven walker written from this document reproduces the file offset and all seven block
positions of every map-type asset in all nine inflated zones (section 11). Field names
are mostly INFERRED (from the OpenAssetTools PC T5 headers, `src/Common/Game/T5/T5_Assets.h`
and `src/ZoneCode/Game/T5/XAssets/*.txt`, matched by offset, element size and count); a name is
only CONFIRMED where the loader's own count arithmetic pins it.

Conventions: hex offsets; "zone offset" = offset in the inflated `.zone` stream (36-byte prefix
included). "LS n" = `Load_Stream(1, p, n)`, i.e. n bytes are copied from the stream. Align is
given in bytes (the loader passes `align - 1` to `DB_AllocStreamPos`). ELF addresses are VAs in
`t5mp.elf`. "-1" and "-2" are the inline markers 0xffffffff and 0xfffffffe.

## Contents

1. Method and proof
2. Loader rules that matter for a parser and a writer
3. Order of the map assets in a map zone
4. ComWorld (com_map 15)
5. GfxLightDef (lightdef 20)
6. GameWorldMp (game_map_mp 17) and PathData
7. clipMap_t (col_map_mp 14 / col_map_sp 13) and MapEnts (18)
8. GfxWorld (gfx_map 19)
9. XModel (5), XSurface and collision
10. PhysPreset (1), PhysConstraints (2), DestructibleDef (3)
11. Coverage
12. Open items
13. Appendix: generated per-path load tables

## 1. Method and proof

The PS3 map structs differ from PC (GfxWorld is 0x454 bytes, XModel 0xf8, XSurface 0x5c,
GfxImage 0x70, Material 0x80), so the layouts were not derived from PC headers. Instead the
game's own loader was run:

1. `t5mp.elf` was disassembled with `powerpc64-linux-gnu-objdump` (text section 0x10230 ..
   0x8c37ec). The per-type dispatcher `Load_XAssetHeader` is at 0x256678 (switch on the type,
   one tail call per type; R1's `xfile.md` section 5 has the full table).
2. A scratch PowerPC interpreter (Python; not part of OpenT5) loads the ELF's two LOAD
   segments, gives each of the seven XFile blocks its own buffer sized from the zone header,
   and executes the game's code: the stream initialiser 0x26aac0, the script-string list
   loader 0x2389f0, then `Load_XAsset` 0x256e60 once per entry of the asset list (exactly as
   the loop at 0x233ba4 .. 0x233cd8 does), then the deferred-read flush 0x26ae38 (called at
   0x233ce0). Only the file readers are replaced (0x233370, 0x233558, 0x2335d0 copy from the
   `.zone`), plus engine calls outside the DB code that do not touch the stream: asset
   registration (`DB_Add*` wrappers that call 0x25ee38), the name lookup 0x2601a0, the
   script-string interner 0x5b99c8, critical sections 0x4d7ed8 / 0x4d7ea0, and any other
   call outside 0x230000 .. 0x270000 (state-bits / RSX converters such as 0x7454a0,
   0x7145a0). The texture size function 0x736ec0 .. 0x737510 runs natively.
3. Every stream read is logged with its zone offset, size, block, block offset, the
   allocation alignment, and a back-trace. The pointer slot that received each allocation is
   recorded at the moment the loader stores it (watching `stw` of the `DB_AllocStreamPos`
   result), which gives the parent struct and field offset of every sub-array. Offset-pointer
   conversions (0x26add8) and alias conversions (0x26ada0) are logged with their slot.

Result (scratch `r2a/run.py`, logs `r2a/nuked.pkl`, `r2a/fr.pkl`):

| Zone | Stream length | Consumed by the loader | Final block positions (RUNTIME, LARGE_RUNTIME, PHYSICAL_RUNTIME, VIRTUAL, LARGE, PHYSICAL) | Header blockSize[1..6] |
|---|---|---|---|---|
| mp_nuked | 0x41a8b49 | 0x41a8b49 | 0x4a8580, 0, 0x115d900, 0x1eb81c1, 0, 0x11d90c8 | identical |
| mp_firingrange | 0x3ddf348 | 0x3ddf348 | 0xe4c98, 0, 0x864980, 0x2229121, 0, 0x13972c8 | identical |

So every byte of both zones is accounted for by the loader, in the order documented below,
and every block ends exactly at its declared size. R2b's independent run of its own
interpreter over nine zones reached the same end offsets (`structs-content.md` section 1).

The tables in sections 4 to 10 are distilled from those logs; section 13 has the raw
per-path tables they were read from.

## 2. Loader rules that matter for a parser and a writer

These are the same primitives R2b documents; the facts below are the ones a map-asset walker
needs. Addresses are the primitives' entry points.

| Primitive | VA | Effect on the stream |
|---|---|---|
| `Load_Stream(atStart, ptr, size)` | 0x26aeb8 | if `atStart` and size > 0: if the current block is RUNTIME (1): memset 0, no bytes read; if LARGE_RUNTIME (2) or PHYSICAL_RUNTIME (3): the (ptr, size) pair is queued and read later (deferred); otherwise `size` bytes are read now. The block position advances by `size` in every case. Calls with `atStart` = 0 read nothing. |
| `DB_PushStreamPos(block)` / `DB_PopStreamPos` | 0x26ab78 / 0x26ac08 | switch the current block; no stream effect |
| `DB_AllocStreamPos(mask)` | 0x26aca0 | `pos = (pos + mask) & ~mask` in the current block; no stream bytes (the file is packed) |
| `DB_InsertPointer` | 0x26acd8 | reserves a 4-byte alias slot in VIRTUAL (for "-2"); no stream bytes |
| offset pointer | 0x26add8 | `e = v - 1; block = e >> 29; ptr = blockBase[block] + (e & 0x1fffffff)` |
| alias pointer (asset refs) | 0x26ada0 | same decode, then one more dereference (the target is an alias slot) |
| string | 0x26ae08 | reads a NUL-terminated string (length includes the NUL), align 1 |
| deferred flush | 0x26ae38 | after the last asset: performs the queued reads in queue order |

Consequences:

- The file has no padding. Alignment only moves the in-memory block position. A parser can
  ignore alignment entirely; a writer must track it to compute offset-pointer values.
- RUNTIME pointers carry a -1 marker in the parent struct but have no bytes in the file.
  ClipMap and GfxWorld have several (listed in their sections).
- PHYSICAL_RUNTIME data (image pixels with the "deferred" flag) is not where its owner is:
  it is appended after the last asset, in the order the owners were loaded. In mp_nuked the
  tail starts at 0x304b249 and is 0x115d900 bytes (= blockSize[3]); in mp_firingrange it
  starts at 0x357a9c8, length 0x864980.
- Offset pointer evidence: the GfxLightDef in mp_nuked (zone 0xe85475) has name =
  0x808b6339 -> VIRTUAL + 0x8b6338, which is exactly where the walker placed the string
  "white_light" loaded by the ComWorld light at zone 0xe85469. GameWorldMp, clipMap and
  GfxWorld names are 0x808b4e81 -> VIRTUAL + 0x8b4e80, the block offset of the ComWorld name
  "maps/mp/mp_nuked.d3dbsp" (zone 0xe83fb1).
- Inline marker tests: some fields test `== -1` (shareable, other non-zero values are offset
  pointers), others test `!= 0` (any non-zero means inline). A writer that writes -1 for every
  inline sub-array and reproduces the original value for every offset/alias pointer is
  correct under both tests. The fields where offset or alias pointers occur in these two
  zones are listed per type.
- Asset references (material, image, xmodel, fx, physpreset, physconstraints) use the
  `Load_<X>Ptr` pattern: push TEMP; -1/-2 means the whole asset is loaded inline right there
  (header in TEMP, its data in VIRTUAL); other non-zero values are alias pointers.

## 3. Order of the map assets in a map zone

mp_nuked asset list (indices into the 529-entry list; types from `xfile.md`):

| # | Type | Zone offset | Next asset at | Bytes |
|---|---|---|---|---|
| 367 | com_map | 0xe83f71 | 0xe85475 | 0x1504 |
| 368 | lightdef | 0xe85475 | 0xe85501 | 0x8c |
| 369 .. 404 | techset x36 (R2b) | 0xe85501 | 0xfcd0bc | |
| 405 | gfx_map | 0xfcd0bc | 0x1e6b6e3 | 0xe9e627 |
| 406 | game_map_mp | 0x1e6b6e3 | 0x1e82252 | 0x16b6f |
| 407 | col_map_mp (with inline map_ents) | 0x1e82252 | 0x20d8a61 | 0x25680f |

XModels (289), physpresets (7) and destructibledefs (13) come earlier in the list, interleaved
with materials, techsets and fx. mp_firingrange has the same order (com_map #338, lightdef
#339, gfx_map #408, game_map_mp #409, col_map_mp #410). There is no separate map_ents asset:
the clipMap carries its MapEnts inline (section 7).

## 4. ComWorld (com_map 15)

Loaders: Ptr 0x2457a0, struct 0x2452d0, register 0x267208. Header 64 bytes, TEMP,
align 4; sub-data in VIRTUAL.

| Off | Size | Field (names INFERRED, OAT `ComWorld`) | Notes |
|---|---|---|---|
| 0x0 | 4 | name | string, -1 |
| 0x4 | 4 | isInUse | |
| 0x8 | 4 | primaryLightCount | count for +0xc (CONFIRMED: `lwz 8`, `mulli 220` at 0x245390) |
| 0xc | 4 | primaryLights | [nz] align 4, LS 220 x primaryLightCount |
| 0x10 | 16 | waterHeader (INFERRED) | four ints; mp_nuked: 7fffffff 7fffffff 80000000 80000000 |
| 0x20 | 4 | numWaterCells (INFERRED) | count for +0x24 |
| 0x24 | 4 | waterCells | [nz] align 4, LS 8 x count (0x245570) |
| 0x28 | 16 | burnableHeader (INFERRED) | |
| 0x38 | 4 | numBurnableCells | count for +0x3c |
| 0x3c | 4 | burnableCells | [nz] align 4, LS 12 x count; then per cell: +8 [nz] align 1, LS 32 (0x245640) |

ComPrimaryLight is 220 (0xdc) bytes on PS3; its only pointer is `defName` at +0xd8 (string,
`== -1`; offset pointers occur: 25 in the two zones).

Load order: LS 64 (TEMP) -> push VIRTUAL -> name -> primaryLights array -> for each light in
order: defName -> waterCells -> burnableCells array -> for each cell: its 32-byte data -> pop.

Worked example, mp_nuked asset #367, zone 0xe83f71 .. 0xe85475:

```
00e83f71: ffffffff 00000001 00000018 ffffffff   name -1, isInUse 1, 24 lights, lights -1
00e83f81: 7fffffff 7fffffff 80000000 80000000   +0x10 waterHeader
00e83f91: 00000000 00000000 00000000 00000000   +0x20 numWaterCells 0, waterCells 0, ...
00e83fa1: 00000000 00000000 00000000 00000000   +0x38 numBurnableCells 0, burnableCells 0
00e83fb1: "maps/mp/mp_nuked.d3dbsp\0"              name, 0x18 bytes, ends 0xe83fc9
00e83fc9: 24 x ComPrimaryLight (0x14a0 bytes)    light[0] is all zero; ends 0xe85469
          light[2].defName at 0xe83fc9+2*0xdc+0xd8 = -1, the others are 0 or offsets
00e85469: "white_light\0"                        light[2].defName, ends 0xe85475 = next asset
```

## 5. GfxLightDef (lightdef 20)

Loaders: Ptr 0x24a6d0, struct 0x24a5e8. Header 16 bytes, TEMP, align 4.

| Off | Size | Field (OAT `GfxLightDef`) | Notes |
|---|---|---|---|
| 0x0 | 4 | name | string; offset pointers seen (mp_nuked: shares the ComWorld defName string) |
| 0x4 | 4 | attenuation.image | image asset ptr (R2b section 7): header 0x70 in TEMP, name at image+0x68 |
| 0x8 | 1 | attenuation.samplerState | |
| 0xc | 4 | lmapLookupStart | |

Worked example, mp_nuked asset #368, zone 0xe85475 .. 0xe85501:

```
00e85475: 808b6339 fffffffe 72000000 00000000   name = offset (VIRTUAL+0x8b6338, "white_light"), image -2, sampler 0x72
00e85485: 0x70-byte GfxImage header (TEMP)       ends 0xe854f5; its +0x68 name = -1
00e854f5: "whitesquare\0"                        ends 0xe85501 = next asset
          the image's 0x80 pixel bytes are deferred: tail offset 0x3297bc9
```

## 6. GameWorldMp (game_map_mp 17) and PathData

Loaders: Ptr 0x248998 (struct inline, LS 44 at 0x248a50); PathData sub-loaders in 0x23b1f0 ..
0x23b800. Header 44 bytes, TEMP, align 4. On PS3 MP the GameWorldMp carries a full PathData,
laid out exactly like OAT's `PathData` inside `GameWorldSp` (OAT's PC `GameWorldMp` has only a
name).

| Off | Size | Field | Notes |
|---|---|---|---|
| 0x0 | 4 | name | string (offset pointer to the ComWorld name in both zones) |
| 0x4 | 4 | path.nodeCount | |
| 0x8 | 4 | path.nodes | align 4, LS 0x80 x (nodeCount + 128) (`addi r25,r9,128` at 0x23b26c; CONFIRMED by data: 316 + 128 = 444, 0xde00 bytes) |
| 0xc | 4 | path.basenodes | RUNTIME: push RUNTIME, align 16, 16 x (nodeCount + 128); no bytes (mp_nuked: 0x1bc0) |
| 0x10 | 4 | path.chainNodeCount | |
| 0x14 | 4 | path.chainNodeForNode | align 2, LS 2 x nodeCount |
| 0x18 | 4 | path.nodeForChainNode | align 2, LS 2 x nodeCount |
| 0x1c | 4 | path.visBytes | |
| 0x20 | 4 | path.pathVis | align 1, LS visBytes |
| 0x24 | 4 | path.nodeTreeCount | |
| 0x28 | 4 | path.nodeTree | align 4, LS 0x10 x nodeTreeCount; then per tree node with axis (s32 +0x0) < 0 (a leaf, OAT `pathnode_tree_t`): +0xc nodes, align 2, LS 2 x (u32 at +0x8); interior nodes hold offset pointers to their children |

pathnode_t (0x80): for each of the nodeCount + 128 nodes, after the node array: +0x40 links, align 4, LS 12 x (u16 at
+0x3e) (several unrolled copies of the loop: load sites 0x23b2ac, 0x23b308, 0x23b334 ...). The
count field is INFERRED from data (constant quotient over 778 instances); OAT calls the array
`constant.Links` with the u16 `totalLinkCount` just before it.

Load order: header -> nodes array -> per node: links -> chainNodeForNode -> nodeForChainNode ->
pathVis -> nodeTree array -> per tree node: its u16 list.

Worked example, mp_nuked asset #406, zone 0x1e6b6e3 .. 0x1e82252:

```
01e6b6e3: 808b4e81 0000013c ffffffff ffffffff   name offset, nodeCount 316, nodes -1, basenodes -1 (RUNTIME)
01e6b6f3: 00000000 ffffffff ffffffff 0000309b   chainNodeCount 0, chainNodeForNode -1, nodeForChainNode -1, visBytes 0x309b
01e6b703: ffffffff 000000e3 ffffffff             pathVis -1, nodeTreeCount 227, nodeTree -1
01e6b70f .. 01e7950f  444 x pathnode_t (0xde00)
01e7950f .. 01e7dc1f  per-node link arrays (316 reads)
01e7dc1f .. 01e7de97  chainNodeForNode (0x278 = 2 x 316)
01e7de97 .. 01e7e10f  nodeForChainNode (0x278)
01e7e10f .. 01e811aa  pathVis (0x309b)
01e811aa .. 01e81fda  nodeTree (227 x 0x10)
01e81fda .. 01e82252  per-tree-node u16 lists (114 reads); 0x1e82252 = next asset
```

## 7. clipMap_t (col_map_mp 14 / col_map_sp 13) and MapEnts (18)

Loaders: Ptr 0x250588 (shared by 13 and 14), struct 0x24e848, register 0x2663d8. Header 0x14c
bytes, TEMP, align 4; sub-data in VIRTUAL. The PS3 field order and offsets up to +0xb0 are the
same as OAT's PC `clipMap_t`; the counts below are CONFIRMED by the loader (`lwz` of the count
before the size computation) and by data in both zones (same element size, count field equal
to size / element).

| Off | Size | Field (OAT names) | Load (in order of the loader) |
|---|---|---|---|
| 0x0 | 4 | name | string; offset pointer to the ComWorld name in both zones |
| 0x4 | 4 | isInUse | |
| 0x8 | 4 | planeCount | |
| 0xc | 4 | planes | shareable; in both zones an offset pointer into the GfxWorld plane array (GfxWorld +0x158), so no bytes here |
| 0x10 | 4 | numStaticModels | |
| 0x14 | 4 | staticModelList | 1: align 4, LS 0x50 x numStaticModels; each +0x4 is an XModel alias pointer |
| 0x18 | 4 | numMaterials | |
| 0x1c | 4 | materials | 2: align 4, LS 0x48 x numMaterials (dmaterial_t: name[64], surfaceFlags, contentFlags) |
| 0x20 | 4 | numBrushSides | |
| 0x24 | 4 | brushsides | 3: align 4, LS 0xc x n; +0x0 plane is an offset pointer (39237 seen) |
| 0x28 | 4 | numNodes | |
| 0x2c | 4 | nodes | 4: align 4, LS 8 x n; +0x0 plane offset pointer |
| 0x30 | 4 | numLeafs | |
| 0x34 | 4 | leafs | 5: align 4, LS 0x2c x n |
| 0x38 | 4 | leafbrushNodesCount | |
| 0x3c | 4 | leafbrushNodes | 7: align 4, LS 0x14 x n; then per node with s16 leafBrushCount (+0x2) > 0: +0x8 brushes, shareable, align 2, LS 2 x leafBrushCount (offset pointers common: 12777 seen) |
| 0x40 | 4 | numLeafBrushes | |
| 0x44 | 4 | leafbrushes | 6: align 2, LS 2 x n (loaded before leafbrushNodes: the PC "reorder" also holds on PS3) |
| 0x48 | 4 | numLeafSurfaces | |
| 0x4c | 4 | leafsurfaces | 0 in both zones (OAT: 4 x n) |
| 0x50 | 4 | vertCount | |
| 0x54 | 4 | verts | 8: align 4, LS 12 x vertCount |
| 0x58 | 4 | numBrushVerts | |
| 0x5c | 4 | brushVerts | 9: align 4, LS 12 x n |
| 0x60 | 4 | nuinds | |
| 0x64 | 4 | uinds | 10: align 2, LS 2 x nuinds |
| 0x68 | 4 | triCount | |
| 0x6c | 4 | triIndices | 11: align 2, LS 6 x triCount |
| 0x70 | 4 | triEdgeIsWalkable | 12: align 1, LS ((3 x triCount + 31) / 32) x 4 (`srawi 5` at 0x24f440; mp_nuked 7492 tris -> 0xafc) |
| 0x74 | 4 | borderCount | |
| 0x78 | 4 | borders | 13: align 4, LS 0x1c x n |
| 0x7c | 4 | partitionCount | |
| 0x80 | 4 | partitions | 14: align 4, LS 0x14 x n; +0x10 borders offset pointer |
| 0x84 | 4 | aabbTreeCount | |
| 0x88 | 4 | aabbTrees | 15: align 16, LS 0x20 x n |
| 0x8c | 4 | numSubModels | |
| 0x90 | 4 | cmodels | 16: align 4, LS 0x48 x n (PS3 cmodel_t is 0x48) |
| 0x94 | 2 | numBrushes (u16) | |
| 0x98 | 4 | brushes | 17: align 16, LS 0x60 x numBrushes; +0x0 sides offset pointer (PS3 cbrush_t is 0x60) |
| 0x9c | 4 | numClusters | |
| 0xa0 | 4 | clusterBytes | |
| 0xa4 | 4 | visibility | 18: align 1, LS numClusters x clusterBytes |
| 0xa8 | 4 | vised | |
| 0xac | 4 | mapEnts | 19: MapEnts asset pointer (-2 in both zones), loaded inline here, see below |
| 0xb0 | 4 | box_brush | 20: align 16, LS 0x60 (one cbrush_t; its own -1 fields are not followed) |
| 0xb4 | 0x48 | box_model | cmodel_t inline; +0xd4 holds a -1 that the loader does not follow (RUNTIME / no bytes) |
| 0xfc | 2 | originalDynEntCount | |
| 0xfe | 2x4 | dynEntCount[4] | |
| 0x108 | 4 | dynEntDefList[0] | 21: align 4, LS 0x54 x dynEntCount[0]; per def: +0x20, +0x24, +0x2c asset alias pointers (xmodel / fx), +0x38 physPreset asset pointer (inline PhysPreset 0x54 or alias) |
| 0x10c | 4 | dynEntDefList[1] | 0 in both zones (OAT: x dynEntCount[1]) |
| 0x110 .. 0x134 | | dynEntPoseList[2], dynEntClientList[2], dynEntServerList[2], dynEntCollList[4] | RUNTIME, align 4, no bytes: pose[k] 0x20 x dynEntCount[k]; client[k] 0x14 x dynEntCount[k]; server[k] 8 x dynEntCount[2 + k]; coll[k] 0x20 x dynEntCount[k] (k = 0..3). mp_nuked: 0x3f20, 0x2774, 0x7c8, 0x3f20, 0x1f20 (load sites 0x24fd34 .. 0x24ffac) |
| 0x138 | 4 | num_constraints | |
| 0x13c | 4 | constraints | 22: align 4, LS 0xa8 x n (PhysConstraint, section 10); per constraint: +0x8c material asset pointer (inline Material 0x80 or alias) |
| 0x140 | 4 | max_ropes | |
| 0x144 | 4 | ropes | RUNTIME, align 4, 0xc74 x max_ropes (mp_nuked 32 ropes = 0x18e80, 0x250218); no bytes |
| 0x148 | 4 | checksum | |

The number in the last column is the load order. MapEnts (header 12 bytes, TEMP):

| Off | Size | Field | Notes |
|---|---|---|---|
| 0x0 | 4 | name | string; offset pointer (clipMap/ComWorld name) |
| 0x4 | 4 | entityString | align 1, LS numEntityChars (includes the trailing NUL) |
| 0x8 | 4 | numEntityChars | mp_nuked 0x1e711, mp_firingrange 0x1da57 |

Loaders: MapEnts Ptr 0x243f18, struct 0x243e20.

Worked example, mp_nuked asset #407, zone 0x1e82252 .. 0x20d8a61:

```
01e82252: 808b4e81 00000000 00002775 80a07571   name offset, isInUse 0, planeCount 10101, planes = offset VIRTUAL+0xa07570
01e82262: 00000569 ffffffff 0000018a ffffffff   1385 static models -1, 394 materials -1
01e82272: 000058a3 ffffffff 00000adc ffffffff   22691 brushsides -1, 2780 nodes -1
01e82282: 00000ba9 ffffffff 00001f85 ffffffff   2985 leafs -1, 8069 leafbrushNodes -1
01e82292: 00004bb4 ffffffff 00000000 00000000   19380 leafbrushes -1, 0 leafsurfaces
01e822a2: 00001224 ffffffff 0000be6b ffffffff   4644 verts -1, 48747 brushVerts -1
01e822b2: 000029a9 ffffffff 00001d44 ffffffff   10665 uinds -1, 7492 tris, triIndices -1
01e822c2: ffffffff 00000494 ffffffff 00000725   triEdgeIsWalkable -1, 1172 borders -1, 1829 partitions
01e822d2: ffffffff 00001342 ffffffff 000000cc   partitions -1, 4930 aabbTrees -1, 204 cmodels
01e822e2: ffffffff 17020000 ffffffff 00000001   cmodels -1, numBrushes 0x1702, brushes -1, numClusters 1
01e822f2: 000000b0 ffffffff 00000000 fffffffe   clusterBytes 0xb0, visibility -1, vised 0, mapEnts -2
01e82302: ffffffff ...                           box_brush -1, box_model (0x48) ...
01e8234e: 00001f84 00000000 00f901f9 000000f9   (+0xfc) originalDynEntCount 0x1f9 ... dynEntCount[0] = 0x1f9 (505)
01e8235e: 00000000 ffffffff 00000000 ffffffff   dynEntDefList[0] -1, [1] 0, pose/client/... -1 (RUNTIME)
01e8238a: 00000006 ffffffff 00000020 ffffffff   num_constraints 6, constraints -1, max_ropes 32, ropes -1 (RUNTIME)
01e8239a: 00000000                               checksum; header ends 0x1e8239e
```

Sub-arrays, in stream order (start .. end):

```
staticModelList 0x1e8239e .. 0x1e9d46e   materials   0x1e9d46e .. 0x1ea433e
brushsides      0x1ea433e .. 0x1ee6ae2   nodes       0x1ee6ae2 .. 0x1eec1c2
leafs           0x1eec1c2 .. 0x1f0c2ce   leafbrushes 0x1f0c2ce .. 0x1f15a36
leafbrushNodes  0x1f15a36 .. 0x1f3d22e   (array 0x27664 + 202 per-node brush lists)
verts           0x1f3d22e .. 0x1f4abde   brushVerts  0x1f4abde .. 0x1fd98e2
uinds           0x1fd98e2 .. 0x1fdec34   triIndices  0x1fdec34 .. 0x1fe9bcc
triEdgeIsWalkable 0x1fe9bcc .. 0x1fea6c8 borders     0x1fea6c8 .. 0x1ff26f8
partitions      0x1ff26f8 .. 0x1ffb5dc   aabbTrees   0x1ffb5dc .. 0x2021e1c
cmodels         0x2021e1c .. 0x202577c   brushes     0x202577c .. 0x20af83c
visibility      0x20af83c .. 0x20af8ec   MapEnts     0x20af8ec .. 0x20ce009 (12-byte header, then 0x1e711 entity string)
box_brush       0x20ce009 .. 0x20ce069   dynEntDefList[0] 0x20ce069 .. 0x20d8671 (505 x 0x54, then one inline PhysPreset header whose name is an offset pointer)
constraints     0x20d8671 .. 0x20d8a61 = next asset (6 x 0xa8)
```

## 8. GfxWorld (gfx_map 19)

Loaders: Ptr 0x2521f8, struct 0x250f88, register 0x266648. Header 0x454 bytes, TEMP, align 4; sub-data in VIRTUAL unless stated. Sub-loaders: plane/node arrays 0x237258,
cells 0x239f88 (called 8 times per unrolled loop step), light grid 0x236e20, draw / vertex data
0x2376d8 .. 0x2378a0, sun / materials 0x24afb0, 0x251f8c.

The PS3 field order follows OAT's PC `GfxWorld` member order closely; offsets differ. Names
below are INFERRED by that order plus the count/element evidence in the "Load" column (which is
CONFIRMED). "RT" = RUNTIME pointer: the header holds -1 but no bytes are in the file.

| Off | Field (INFERRED name) | Load (order number: rule) |
|---|---|---|
| 0x0 | name | string; offset pointer to the ComWorld name in both zones |
| 0x4 | baseName | 1: string (-1) |
| 0x8 | planeCount | count for +0x158 |
| 0xc | nodeCount | count for +0x15c |
| 0x10 | surfaceCount | count for +0x3b4 |
| 0x14 | streamInfo.aabbTreeCount | |
| 0x18 | streamInfo.aabbTrees | 2: align 4, LS 0x20 x [+0x14] (0x23685c) |
| 0x1c | streamInfo.leafRefCount | |
| 0x20 | streamInfo.leafRefs | 3: align 4, LS 4 x [+0x1c] |
| 0x24 .. 0x37 | (PS3 streamInfo extra) | data |
| 0x38 | skySurfCount | |
| 0x3c | skyStartSurfs | 4: align 4, LS 4 x [+0x38] |
| 0x40 | skyImage | 5: image asset pointer (-2 in both zones; inline GfxImage 0x70 + name) |
| 0x44 | skySamplerState | |
| 0x48 | skyBoxModel | 6: string |
| 0x4c .. 0xff | sunParse and other data | |
| 0x100 | sunLight | 7: align 16, LS 0x170 (PS3 GfxLight); then the light's +0x160 GfxLightDef asset pointer (0x252170 -> 0x24a6d0) |
| 0x104 .. 0x113 | sunColorFromBsp, sunPrimaryLightIndex | |
| 0x114 | primaryLightCount | count for +0x354, +0x358 |
| 0x118 | cullGroupCount | |
| 0x11c | coronaCount | |
| 0x120 | coronas | 8: align 4, LS 0x20 x [+0x11c] |
| 0x124 / 0x128 | shadowMapVolumeCount / shadowMapVolumes | align 4, LS 0x10 x count (0x25114c) |
| 0x12c / 0x130 | shadowMapVolumePlaneCount / shadowMapVolumePlanes | align 4, LS 0x10 x count |
| 0x134 / 0x138 | exposureVolumeCount / exposureVolumes | align 4, LS 0x18 x count (zombie_theater: 5 volumes, 120 bytes) |
| 0x13c / 0x140 | exposureVolumePlaneCount / exposureVolumePlanes | align 4, LS 0x10 x count |
| 0x144 .. 0x14f | | data |
| 0x150 | skyDynIntensity (INFERRED) | |
| 0x154 | dpvsPlanes.cellCount | count for +0x168 |
| 0x158 | dpvsPlanes.planes | 9: align 4, LS 0x14 x planeCount (shared: the clipMap points here) |
| 0x15c | dpvsPlanes.nodes | 10: align 2, LS 2 x nodeCount |
| 0x160 | dpvsPlanes.sceneEntCellBits | RT: align 4, 4 x 0x200 x cellCount (0x237344) |
| 0x164 | cellBitsCount | |
| 0x168 | cells | 11: align 4, LS 0x38 x cellCount, then per cell (below) |
| 0x16c | draw.reflectionProbeCount | |
| 0x170 | draw.reflectionProbes | 12: align 4, LS 0x18 x count; per probe: +0xc image asset pointer (inline) |
| 0x174 | draw.reflectionProbeTextures | RT: align 4, 0x18 x reflectionProbeCount (0x24b240) |
| 0x178 | draw.lightmapCount | |
| 0x17c | draw.lightmaps | 13: align 4, LS 0xc x count; each 12-byte entry is three image asset pointers, loaded in order |
| 0x180, 0x184, 0x188 | lightmapPrimary/Secondary/SecondaryB textures | RT: each align 4, 0x18 x lightmapCount (0x24b46c, 0x24b4c4, 0x24b51c) |
| 0x18c .. 0x207 | 31 image asset pointers (INFERRED: draw images) | each a GfxImage asset pointer, loaded in address order; 0 or alias in the MP zones, some inline in zombie_theater |
| 0x208 | draw.vertexCount | |
| 0x20c | draw.vd.vertices | 14: align 16, LS 0x10 x vertexCount (0x237838) |
| 0x210 | (vd buffer handle) | no bytes |
| 0x214 | draw.vertexLayerDataSize | |
| 0x218 | draw.vld.data | 15: PHYSICAL, align 1, LS vertexLayerDataSize (0x237758) |
| 0x21c .. 0x223 | | |
| 0x224 | draw.indexCount | |
| 0x228 | draw.indices | 16: align 2, LS 2 x indexCount |
| 0x230 | lightGrid (0x38 bytes, loader 0x236e20) | |
| 0x24c | lightGrid.rowDataStart | 17: align 2, LS 2 x (maxs[rowAxis] - mins[rowAxis] + 1); rowAxis = u32 at +0x244, mins u16[3] at +0x238, maxs u16[3] at +0x23e (CONFIRMED from 0x236e78 .. 0x236e9c) |
| 0x250 | lightGrid.rawRowDataSize | |
| 0x254 | lightGrid.rawRowData | 18: align 4, LS rawRowDataSize |
| 0x258 | lightGrid.entryCount | |
| 0x25c | lightGrid.entries | 19: align 4, LS 4 x entryCount |
| 0x260 | lightGrid.colorCount | |
| 0x264 | lightGrid.colors | 20: align 4, LS 0xa8 x colorCount (both zones: 32001 colours, 0x5208a8) |
| 0x268 | modelCount | |
| 0x26c | models | 21: align 4, LS 0x3c x modelCount |
| 0x270 .. 0x28b | mins, maxs, checksum | |
| 0x28c | materialMemoryCount | |
| 0x290 | materialMemory | 22: align 4, LS 8 x count; per entry: +0x0 Material asset pointer (inline Material, R2b section 8) |
| 0x294 | sun (sunflare_t) | |
| 0x298 | sun.spriteMaterial | 23: Material asset pointer (-2) |
| 0x29c | sun.flareMaterial | alias pointer in both zones |
| 0x2a0 .. 0x333 | sun data, outdoorLookupMatrix | |
| 0x334 | outdoorImage | 24: image asset pointer (-2) |
| 0x338 .. 0x350 | cellCasterBits, sceneDynModel, sceneDynBrush, primaryLightEntityShadowVis, primaryLightDynEntShadowVis[2], nonSunPrimaryLightForModelDynEnt | RT, in this order: 4 x cellCount x ((cellCount + 31) / 32); 6 x dynEntClientCount[0] (+0x3d4); sceneDynBrush (0 in all zones; size INFERRED); 4 x 0x2000 x (primaryLightCount - sunPrimaryLightIndex - 1), sunPrimaryLightIndex = +0x110; 4 x dynEntClientCount[k] x (same); 1 x dynEntClientCount[0] (align 1). All align 4 except the last. mp_nuked: 0x5c, 0xbd6, 0xb0000, 0xad98, 0x1f9 |
| 0x354 | shadowGeom | 25: align 4, LS 0xc x primaryLightCount; per entry (loader 0x2373b0): +0x4 sortedSurfIndex, align 2, LS 2 x (u16 +0x0 surfaceCount); then +0x8 smodelIndex, align 2, LS 2 x (u16 +0x2 smodelCount) |
| 0x358 | lightRegion | 26: align 4, LS 8 x primaryLightCount; per entry: +0x4 hulls, align 4, LS 0x50 x (u32 +0x0); per hull: +0x4c axis, align 4, LS 0x14 x (u32 hull+0x48) |
| 0x35c | dpvs.smodelCount | count for +0x3b0, +0x3bc |
| 0x360 .. 0x387 | dpvs counts | +0x364 = count for +0x3ac |
| 0x380 / 0x384 | dpvs.smodelVisDataCount / surfaceVisDataCount | |
| 0x388 .. 0x3a8 | smodelVisData[3], surfaceVisData[3], smodelVisDataCameraSaved, surfaceVisDataCameraSaved, lodData | RT, align 128, in this order: 4 x [+0x380] (x3), 4 x [+0x384] (x3), 4 x [+0x380], 4 x [+0x384], 8 x [+0x380] (0x250724 .. 0x2509a4) |
| 0x3ac | dpvs.sortedSurfIndex | 27: align 2, LS 2 x [+0x364] |
| 0x3b0 | dpvs.smodelInsts | 28: align 4, LS 0x28 x smodelCount |
| 0x3b4 | dpvs.surfaces | 29: align 16, LS 0x60 x surfaceCount; each +0x40 is a Material alias pointer (6319 seen) |
| 0x3b8 | dpvs.cullGroups | 0 in both zones |
| 0x3bc | dpvs.smodelDrawInsts | 30: align 4, LS 0x2c x smodelCount; each +0x20 is an XModel alias pointer (9002 seen) |
| 0x3c0, 0x3c4 | surfaceMaterials, surfaceCastsSunShadow | RT: align 4, 8 x [+0x364]; align 128, 4 x [+0x384] (0x250f08, 0x250f58) |
| 0x3c8 .. 0x3d7 | dpvsDyn counts | |
| 0x3cc / 0x3d0 | dpvsDyn.dynEntClientWordCount[2] | |
| 0x3d4 / 0x3d8 | dpvsDyn.dynEntClientCount[2] | |
| 0x3dc, 0x3e0 | dynEntCellBits[2] | RT, align 4, 4 x wordCount[k] x cellCount (0x236a28) |
| 0x3e4 .. 0x3f8 | dynEntVisData[2][3] | RT, align 16, 32 x wordCount[k]; order [0][0], [1][0], [0][1], [1][1], [0][2], [1][2] (0x236ad8 .. 0x236c18) |
| 0x3f0 .. 0x427 | worldLod*, waterDirection, waterBuffers | 0 / data in both zones |
| 0x428, 0x42c, 0x430 | waterMaterial, coronaMaterial, ropeMaterial | Material asset pointers, loaded in that order (0x251f8c .. 0x251fb4); 0 / alias in both zones |
| 0x434 | numOccluders | |
| 0x438 | occluders | 31: align 4, LS 0x44 x numOccluders (mp_nuked only: 5) |
| 0x43c .. 0x453 | numOutdoorBounds, outdoorBounds, heroLightCount, heroLightTreeCount, heroLights, heroLightTree | 0 in both zones |

GfxCell (0x38 bytes, loader 0x239f88), per cell in order:

| Cell off | Load |
|---|---|
| +0x1c | aabbTree: align 4, LS 0x28 x (u32 +0x18); per tree node: +0x20 smodelIndexes, shareable, align 2, LS 2 x (u16 node+0x1e) (offset pointers common: 3941 seen) |
| +0x24 | portals: align 4, LS 0x44 x (u32 +0x20); per portal: +0x24 vertices, align 4, LS 0xc x (u8 portal+0x28); +0x20 cell is an offset pointer |
| +0x34 | reflectionProbes: align 1, LS (u8 +0x30) |

Worked example, mp_nuked asset #405, zone 0xfcd0bc .. 0x1e6b6e3 (header 0xfcd0bc .. 0xfcd510;
first 0x60 bytes shown, the rest is in the per-field sub-array list):

```
00fcd0bc: 808b4e81 ffffffff 00002775 00001afe   name offset, baseName -1, planeCount 10101, nodeCount 6910
00fcd0cc: 00000da6 00000270 ffffffff 00001de4   surfaceCount 3494, 624 aabbTrees -1, 7652 leafRefs
00fcd0dc: ffffffff 00000000 ...                 leafRefs -1
00fcd0f4: 00000006 ffffffff fffffffe ea000000   skySurfCount 6, skyStartSurfs -1, skyImage -2, sampler 0xea
00fcd104: ffffffff                               skyBoxModel -1
```

Sub-arrays in stream order:

```
baseName            0xfcd510 .. 0xfcd519 ("mp_nuked\0")
aabbTrees           0xfcd519 .. 0xfd2319     leafRefs        0xfd2319 .. 0xfd9aa9
skyStartSurfs       0xfd9aa9 .. 0xfd9ac1     skyImage        0xfd9ac1 .. 0xfd9b38 (0x70 + name)
skyBoxModel         0xfd9b38 .. 0xfd9b48     sunLight        0xfd9b48 .. 0xfd9cb8
coronas             0xfd9cb8 .. 0xfd9e38     planes          0xfd9e38 .. 0x100b35c
nodes               0x100b35c .. 0x100e958   cells (+ per cell data) 0x100e958 .. 0x102e229
reflectionProbes    0x102e229 .. 0x102e35f   lightmaps (3 images) 0x102e35f .. 0x102e4f9
vertices            0x102e4f9 .. 0x12df279   vertexLayerData (PHYSICAL) 0x12df279 .. 0x17aac2d
indices             0x17aac2d .. 0x185669b   rowDataStart    0x185669b .. 0x1857685
rawRowData          0x1857685 .. 0x185e529   entries         0x185e529 .. 0x18827b5
colors              0x18827b5 .. 0x1da305d   models          0x1da305d .. 0x1da602d
materialMemory (+ 182 materials) 0x1da602d .. 0x1dbdda9
sun.spriteMaterial  0x1dbdda9 .. 0x1dbdec2   outdoorImage    0x1dbdec2 .. 0x1dbdf3b
shadowGeom          0x1dbdf3b .. 0x1dc0603   lightRegion     0x1dc0603 .. 0x1dc187f
sortedSurfIndex     0x1dc187f .. 0x1dc323b   smodelInsts     0x1dc323b .. 0x1dec3e3
surfaces            0x1dec3e3 .. 0x1e3e223   smodelDrawInsts 0x1e3e223 .. 0x1e6b58f
occluders           0x1e6b58f .. 0x1e6b6e3 = next asset
deferred tail: 23 image pixel blocks owned by this asset, 0xc37f00 bytes in total, first at 0x3297c49
```

## 9. XModel (5), XSurface and collision

Loaders: Ptr 0x24c998, struct 0x24bc88, register 0x266168. Header 0xf8 bytes, TEMP, align 4; data in VIRTUAL except vertex streams (PHYSICAL).

| Off | Size | Field (OAT `XModel` order) | Load (order: rule) |
|---|---|---|---|
| 0x0 | 4 | name | 1: string (offset pointers occur) |
| 0x4 | 1 | numBones | |
| 0x5 | 1 | numRootBones | |
| 0x6 | 1 | numsurfs | |
| 0x7 | 1 | lodRampType | |
| 0x8 | 4 | boneNames | 2: align 2, LS 2 x numBones (script-string indices) |
| 0xc | 4 | parentList | 3: align 1, LS (numBones - numRootBones) |
| 0x10 | 4 | quats | 4: align 2, LS 8 x (numBones - numRootBones) |
| 0x14 | 4 | trans | 5: align 4, LS 16 x (numBones - numRootBones) |
| 0x18 | 4 | partClassification | 6: align 1, LS numBones |
| 0x1c | 4 | baseMat | 7: align 4, LS 0x20 x numBones |
| 0x20 | 4 | surfs | 8: align 4, LS 0x5c x numsurfs, then per surface (below) |
| 0x24 | 4 | materialHandles | 9: align 4, LS 4 x numsurfs; then each entry is a Material asset pointer (inline Material / alias) |
| 0x28 .. 0x9f | | lodInfo[4] etc. | data |
| 0xa0 | 4 | collSurfs | 10: align 4, LS 0x24 x (u32 +0xa4) (the load site 0x24c1ec computes the size with adds; element size and count field read from data, constant over 407 instances, and confirmed by the walker in nine zones) |
| 0xa4 | 4 | numCollSurfs | |
| 0xa8 | 4 | contents | |
| 0xac | 4 | boneInfo | 11: align 4, LS 0x2c x numBones |
| 0xb0 .. 0xcf | | radius, mins, maxs, numLods, collLod | |
| 0xd0 | 4 | streamInfo.highMipBounds | 12: align 4, LS 0x10 x numsurfs |
| 0xd4 .. 0xe7 | | memUsage, flags, bad | |
| 0xe8 | 4 | physPreset | 13: PhysPreset asset pointer (inline 0x54 + name, or alias) |
| 0xec | 1 | numCollmaps | |
| 0xf0 | 4 | collmaps | 14: align 4, LS 4 x numCollmaps; then per collmap (below) |
| 0xf4 | 4 | physConstraints | 15: PhysConstraints asset pointer (not seen in the two zones) |

The counts for 0xc, 0x10, 0x14 are `numBones - numRootBones` (`lbz 4`, `lbz 5`, `subf` at 0x24c8cc, 0x24c90c,
0x24c950); the data agree (100 models in the two zones have them).

XSurface (0x5c bytes, loader 0x238db8). Fields: +0x0 tileMode u8, +0x1 vertListCount u8, +0x2
flags u16, +0x4 vertCount u16, +0x6 triCount u16, +0x8 triIndices, +0xc vertInfo (16 bytes:
vertCount[4] s16 then vertsBlend, tensionData), +0x1c verts0, +0x20 (no bytes; RSX handle),
+0x24 vertex stream, +0x28 (no bytes), +0x2c vertList, +0x30 (no bytes; passed to the RSX
offset converter 0x7145a0). Per surface, in order:

| Step | Field | Rule (all CONFIRMED from 0x238db8 .. 0x239488 and helpers) |
|---|---|---|
| 1 | vertInfo.vertsBlend (+0x14) | align 2, LS 2 x (v0 + 3 v1 + 5 v2 + 7 v3), v = vertInfo.vertCount[] (0x2371b0) |
| 2 | vertInfo.tensionData (+0x18) | align 4, LS 48 x (v0 + v1 + v2 + v3) (0x237130) |
| 3 | verts0 (+0x1c) | if (flags & 1) == 0: align 16, LS 16 x vertCount; if (flags & 3) == 1: align 8, LS 8 x vertCount; if (flags & 3) == 3: align 16, LS 16 x vertCount. Shareable (offsets seen). |
| 4 | vertex stream (+0x24), fmt = flags & 7 | fmt 0 or 1: push PHYSICAL, align 16, LS 16 x vertCount; fmt 2: align 16, LS 16 x vertCount in the current block (VIRTUAL); fmt 3: PHYSICAL, align 8, LS 8 x vertCount; fmt 5: PHYSICAL, align 4, LS 12 x vertCount; fmt 7: PHYSICAL, align 4, LS 4 x vertCount; fmt 4, 6: nothing (0x236468, 0x2360e0, 0x2361c8, 0x2362b0) |
| 5 | vertList (+0x2c) | align 4, LS 12 x vertListCount; per XRigidVertList: +0x8 collisionTree: align 4, LS 0x28, then nodes (+0x1c) align 16, LS 16 x (u32 +0x18), then leafs (+0x24) align 2, LS 2 x (u32 +0x20) (0x235f48) |
| 6 | triIndices (+0x8) | align 16, LS 6 x triCount (shareable) |

All surfaces' 0x5c structs are read as one array first; then steps 1 to 6 run surface by
surface.

Collmap (4 bytes) -> PhysGeomList (+0x0: align 4, LS 0xc; loader 0x23acbc) -> geoms (+0x4:
align 16, LS 0x44 x (u32 +0x0)) -> per PhysGeomInfo: +0x0 brush (BrushWrapper, align 16, LS
0x60; 0x23a788), per brush: +0x20 sides, align 4, LS 0xc x (u32 +0x1c), per side +0x0 plane
(align 4, LS 0x14; cplane_s inline per side, in unrolled loops 0x23a9e4 .. 0x23ac68); then
then +0x58 (verts, INFERRED), align 4, LS 0xc x (u32 brush+0x54). Order per geom: brush header,
sides array, each side's plane, the +0x58 array; then the next geom. (Order and sizes CONFIRMED by the
trace; the field names INFERRED from OAT `BrushWrapper`.)

Worked example, mp_nuked asset #13 (first xmodel), zone 0x5795d .. 0x5b9b8:

```
0005795d: ffffffff 01010400 ffffffff 00000000   name -1, 1 bone, 1 root bone, 4 surfs, lodRamp 0, boneNames -1, parentList 0
0005796d: 00000000 00000000 ffffffff ffffffff   quats 0, trans 0, partClassification -1, baseMat -1
0005797d: ffffffff ffffffff 447a0000 ...         surfs -1, materialHandles -1, lodInfo[0].dist 1000.0 ...
000579fd: ffffffff 00000001 00000001 ffffffff   collSurfs -1, numCollSurfs 1, contents 1, boneInfo -1
00057a2d: ffffffff 00000000 ...                  highMipBounds -1 ... physPreset 0, numCollmaps 0, collmaps 0, physConstraints 0
00057a55: name (0xf), 00057a64: boneNames (2), 00057a66: partClassification (1), 00057a67: baseMat (0x20)
00057a87: 4 x XSurface (0x170), then per-surface data .. 0x5b60b
0005b60b: materialHandles (0x10) then 4 materials inline .. 0x5b928
0005b928: collSurfs (0x24)   0005b94c: boneInfo (0x2c)   0005b978: highMipBounds (0x40) .. 0x5b9b8 = next asset
```

## 10. PhysPreset (1), PhysConstraints (2), DestructibleDef (3)

PhysPreset: Ptr 0x24baa0 (struct inline). 0x54 bytes, TEMP, align 4. Push VIRTUAL; +0x0 name
(string); +0x1c sndAliasPrefix (string; the second string test at 0x24bbb4); pop. In these zones
sndAliasPrefix is always 0. Example mp_nuked #209,
zone 0x96adf3: header 0x54 bytes then the name, ends 0x96ae58 = next asset. PhysPresets also
occur inline inside clipMap dynEnt defs and XModels.

PhysConstraints: Ptr 0x24a498, struct 0x24a2c8. 0xa88 bytes, TEMP, align 4: +0x0 name
(string), +0x4 count, +0x8 data[16] (PhysConstraint, 0xa8 each = 16 x 0xa8 + 8 = 0xa88). Per
PhysConstraint the strings are +0x14 (target_bone1) and +0x24 (target_bone2), shareable
(offset pointers common); +0x8c is a Material asset pointer (seen inline in clipMap
constraints). Load order: push VIRTUAL; name; then for each of the 16 elements in order:
+0x14 string, +0x24 string, +0x8c material; pop. The only instances in these zones are inline inside DestructibleDef pieces
(27); the standalone asset occurs in patch_mp and code_post_gfx_mp (R2b's zones).

DestructibleDef: Ptr 0x254628, struct 0x254360. 0x18 bytes, TEMP, align 4.

| Off | Field (OAT) | Load |
|---|---|---|
| 0x0 | name | 1: string |
| 0x4 | model | XModel asset pointer (alias values in both zones) |
| 0x8 | pristineModel | XModel asset pointer |
| 0xc | numPieces | |
| 0x10 | pieces | 2: align 4, LS 0x138 x numPieces, then per piece (below) |
| 0x14 | clientOnly | |

DestructiblePiece (0x138): stages[5] at +0x0 (DestructibleStage 0x30 each), then fields to
0x138. Per piece, in order (loaders 0x253f40 per stage, 0x254170 for the piece tail):

- for each of the 5 stages s (base +0x30 s), in this order: +0x10 breakEffect (fx asset
  pointer), +0x14 breakSound, +0x18 breakNotify, +0x1c loopSound (strings), +0x20, +0x24, +0x28
  spawnModel[3] (XModel asset pointers), +0x2c physPreset (asset pointer);
- +0x10c physConstraints (asset pointer: inline 0xa88 header + strings, or alias);
- +0x114 damageSound (string), +0x118 burnEffect (fx asset pointer), +0x11c burnSound (string).

All asset pointers follow the `Load_<X>Ptr` rule (section 2): -1/-2 = the asset is inline at
that point, other non-zero = alias.

Worked example, mp_nuked #282, zone 0xa427a5 .. 0xa42974:

```
00a427a5: ffffffff 80003605 00000000 00000001   name -1, model = alias (slot VIRTUAL+0x3604), pristine 0, 1 piece
00a427b5: ffffffff 00000001                      pieces -1, clientOnly 1
00a427bd: name (0x19)   00a427d6: 1 x piece (0x138)
00a4290e: stage[0].breakSound (0x23)   00a42931: stage[1].breakSound (0x23)   00a42954: damageSound (0x20) .. 0xa42974 = next asset
```

## 11. Coverage

Bytes of each type that the loader trace accounts for, from each asset's first byte to the
next asset's first byte, and the deferred tail bytes those assets own (scratch
`r2a/coverage.py`). "Discontinuities" counts places where one read does not start where the
previous ended (0 = the reads tile the span exactly).

| Zone | Type | Assets | Span | Read | Coverage | Discontinuities | Deferred tail owned |
|---|---|---|---|---|---|---|---|
| mp_nuked | physpreset | 7 | 0x2d1 | 0x2d1 | 100% | 0 | 0 |
| mp_nuked | destructibledef | 13 | 0xee72 | 0xee72 | 100% | 0 | 0 |
| mp_nuked | xmodel | 289 | 0x164a4df | 0x164a4df | 100% | 0 | 0x2de100 |
| mp_nuked | com_map | 1 | 0x1504 | 0x1504 | 100% | 0 | 0 |
| mp_nuked | lightdef | 1 | 0x8c | 0x8c | 100% | 0 | 0x80 |
| mp_nuked | gfx_map | 1 | 0xe9e627 | 0xe9e627 | 100% | 0 | 0xc37f00 |
| mp_nuked | game_map_mp | 1 | 0x16b6f | 0x16b6f | 100% | 0 | 0 |
| mp_nuked | col_map_mp | 1 | 0x25680f | 0x25680f | 100% | 0 | 0 |
| mp_firingrange | physpreset | 5 | 0x211 | 0x211 | 100% | 0 | 0 |
| mp_firingrange | destructibledef | 9 | 0xc20e | 0xc20e | 100% | 0 | 0 |
| mp_firingrange | xmodel | 268 | 0x17bf85c | 0x17bf85c | 100% | 0 | 0x158180 |
| mp_firingrange | com_map | 1 | 0x754 | 0x754 | 100% | 0 | 0 |
| mp_firingrange | lightdef | 2 | 0x10c | 0x10c | 100% | 0 | 0x15600 |
| mp_firingrange | gfx_map | 1 | 0xe35129 | 0xe35129 | 100% | 0 | 0x694800 |
| mp_firingrange | game_map_mp | 1 | 0x22e87 | 0x22e87 | 100% | 0 | 0 |
| mp_firingrange | col_map_mp | 1 | 0x33f4ad | 0x33f4ad | 100% | 0 | 0xd700 |

"Read" is what the game's loader consumed for these assets; it equals the span by
construction, and the whole-zone totals (section 1) show nothing is left over between assets.
Independent check: a table-driven walker written from this document (scratch
`r2a/walker_map.py`, layered on R2b's content walker `r2b/walker.py` for materials, images,
techsets and fx) starts every asset at the file offset and the seven block positions recorded
from the game's loader and must end at the recorded file offset with all seven block positions
equal (TEMP, RUNTIME, LARGE_RUNTIME, PHYSICAL_RUNTIME, VIRTUAL, LARGE, PHYSICAL; i.e. sizes,
order, alignment and blocks all right, including the RUNTIME-only and deferred allocations).
Result, every asset of every type in all nine inflated zones:

| Zone | Map-type assets walked (types 1, 2, 3, 5, 13 .. 20) | Failures |
|---|---|---|
| mp_nuked | 7 physpreset, 13 destructibledef, 289 xmodel, col_map_mp, com_map, game_map_mp, gfx_map, lightdef | 0 |
| mp_firingrange | 5, 9, 268 xmodel, col_map_mp, com_map, game_map_mp, gfx_map, 2 lightdef | 0 |
| zombie_theater | physpreset, 330 xmodel, col_map_sp, com_map, game_map_sp, gfx_map, lightdef | 0 |
| common_mp, code_post_gfx_mp, patch_mp, ui_mp, patch, patch_ui_mp | all xmodels (including the 7 inline in fx/weapons), physpresets, physconstraints, lightdefs | 0 |

With R2b's content loaders the combined walker covers every asset of all nine zones. For the
assets R2b's harness traced event by event (`r2b/ev_<zone>.pkl`), the walker's sequence of
Load_Stream sizes and blocks, allocations with masks, strings and alias inserts is identical
(push/pop pairs with nothing between them ignored): mp_nuked 65 map-type assets, zombie_theater
46, common_mp 41, code_post_gfx_mp 8, patch_mp 34, all equal (scratch `r2a/diffall.py`).

## 12. Open items

- Field names other than counts and pointers are INFERRED from OAT's PC order; the PS3-only
  ranges (ComWorld +0x10/+0x28 headers, GfxWorld +0x24 .. +0x37, +0x4c .. +0xff, +0x18c ..
  +0x207, XModel +0x28 .. +0x9f) are unnamed.
- The pathnode link count (u16 at node+0x3e), the nodeTree leaf test (axis < 0) and the GfxCell /
  lightRegion nested counts were inferred from data and OAT, then confirmed only by the walker
  passing every asset in nine zones (not by reading each count instruction).
- Pointer fields that were 0 in every zone (clipMap leafsurfaces, dynEntDefList[1];
  GfxWorld shadowMapVolumes / planes, cullGroups, sceneDynBrush, worldLod*, heroLights,
  outdoorBounds; XModel physConstraints; XSurface +0x20/+0x28) have element sizes from OAT or
  the loader only; the walker raises an error if it meets a non-zero one it does not implement
  (GfxWorld +0x3b8, +0x3fc .. +0x424, +0x43c .. +0x450).
- col_map_sp / game_map_sp: walked in zombie_theater with the same tables as the MP types
  (13 and 14 dispatch to 0x250588; GameWorldSp is also 0x2c with the same PathData).
- XModelPieces (0) has no instance in any of the nine inflated zones.

## 13. Appendix: generated per-path load tables

Generated from the two traces by scratch `r2a/paths.py` + `r2a/mdtab.py`. A path is
`root/<field>/<field>...`, each field offset relative to the start of its parent element;
`$` marks a string. Rows are in load order merged over all assets of the type; where an asset lacks a field the
merge can misplace it (for example shadowGeom `+0x8` is listed before `+0x4`, but the loader
loads `+0x4` first): the tables in sections 4 to 10 and `walker_map.py` are authoritative. "Reads" is
the number of stream reads at that path over both zones; "Marker in parent" counts the inline
markers seen in the parent field; "Offset/alias seen" counts conversions of non-inline values
at that field. Asset references show the embedded asset's own paths (Material `0x80`, image
`0x70`, PhysPreset `0x54`) beneath the referencing field. Size rules are the expression the
loader computes into `r5` at the load site (`lwz N(rX) x E` = u32 count at offset N of the
struct in rX times E); expressions shown as `add`/`subf` are multiplications the compiler
strength-reduced.

### 13.1 ComWorld (com_map)

| Path | Reads (both zones) | Size per read | Size rule at load site | Block | Align | Marker in parent | Offset/alias seen | Load site (return addr) |
|---|---|---|---|---|---|---|---|---|
| `root` | 2 | 0x40 | fixed 0x40 | TEMP | 4 |  |  | 0x245314 |
| `root/0x0$` | 2 | 0x18..0x1e | string | VIRTUAL | 1 | {-1: 2} |  | 0x24579c |
| `root/0xc` (array) | 2 | 0x6e0..0x14a0 | lwz 8(r8) x 0xdc | VIRTUAL | 4 | {-1: 2} |  | 0x24539c |
| `root/0xc/0xd8$` | 3 | 0xa..0xc | string | VIRTUAL | 1 | {-1: 3} | {off: 25} | 0x24542c,0x245514 |


### 13.2 GfxLightDef (lightdef)

| Path | Reads (both zones) | Size per read | Size rule at load site | Block | Align | Marker in parent | Offset/alias seen | Load site (return addr) |
|---|---|---|---|---|---|---|---|---|
| `root` | 3 | 0x10 | fixed 0x10 | TEMP | 4 |  |  | 0x24a614 |
| `root/0x4` | 3 | 0x70 | fixed 0x70 | TEMP | 4 | {-2: 3} |  | 0x248bfc |
| `root/0x4/0x68$` | 2 | 0xc | string | VIRTUAL | 1 | {-1: 2} | {off: 1} | 0x248cc8 |
| `?unlinked` | 3 | 0x80..0x15580 | unknown | PHYSICAL_RUNTIME DEFERRED | - |  |  | 0xdead0000 |

Pointer fields seen only as offset or alias pointers (never inline in these zones):

- `root/0x0`: {off: 3}


### 13.3 GameWorldMp (game_map_mp)

| Path | Reads (both zones) | Size per read | Size rule at load site | Block | Align | Marker in parent | Offset/alias seen | Load site (return addr) |
|---|---|---|---|---|---|---|---|---|
| `root` | 2 | 0x2c | fixed 0x2c | TEMP | 4 |  |  | 0x248a54 |
| `root/0x8` (array) | 2 | 0xde00..0x12700 | expr addi r25,r9,128 x 0x80 | VIRTUAL | 4 | {-1: 2} |  | 0x23b278 |
| `root/0x8/0x40` | 778 | 0xc..0x90 | stw r5,-16232(r26); unknown-after-call | VIRTUAL | 4 | {-1: 778} |  | 0x23b2ac,0x23b308,0x23b334.. |
| `root/0x14` | 2 | 0x278..0x39c | lwz 0(r29) x 0x2 | VIRTUAL | 2 | {-1: 2} |  | 0x23b4b4 |
| `root/0x18` | 2 | 0x278..0x39c | lwz 0(r6) x 0x2 | VIRTUAL | 2 | {-1: 2} |  | 0x23b4fc |
| `root/0x20` | 2 | 0x309b..0x67ff | lwz 24(r10) x 0x1 | VIRTUAL | 1 | {-1: 2} |  | 0x23b540 |
| `root/0x28` (array) | 2 | 0xe30..0x1550 | lwz 32(r25) x 0x10 | VIRTUAL | 4 | {-1: 2} |  | 0x23b588 |
| `root/0x28/0xc` | 285 | 0x2..0xc | lwz 0(r10) x 0x2; lwz 0(r8) x 0x2 | VIRTUAL | 2 | {-1: 285} | {off: 283} | 0x23b748,0x23b7d8 |

Pointer fields seen only as offset or alias pointers (never inline in these zones):

- `root/0x0`: {off: 2}
- `root/0x28/0x8`: {off: 283}


### 13.4 clipMap_t (col_map_mp)

| Path | Reads (both zones) | Size per read | Size rule at load site | Block | Align | Marker in parent | Offset/alias seen | Load site (return addr) |
|---|---|---|---|---|---|---|---|---|
| `root` | 2 | 0x14c | fixed 0x14c | TEMP | 4 |  |  | 0x24e890 |
| `root/0x14` | 2 | 0x1b0d0..0x294a0 | add r28,r29,r31 | VIRTUAL | 4 | {-1: 2} |  | 0x24e948 |
| `root/0x1c` | 2 | 0x6ed0..0xce28 | add r5,r9,r6 | VIRTUAL | 4 | {-1: 2} |  | 0x24eb5c |
| `root/0x24` | 2 | 0x30798..0x427a4 | subf r30,r25,r24 | VIRTUAL | 4 | {-1: 2} |  | 0x24ebac |
| `root/0x2c` | 2 | 0x4640..0x56e0 | lwz 40(r11) x 0x8 | VIRTUAL | 4 | {-1: 2} |  | 0x24ee0c |
| `root/0x34` | 2 | 0x1925c..0x2010c | lwz 48(r24) x 0x2c | VIRTUAL | 4 | {-1: 2} |  | 0x24f070 |
| `root/0x44` | 2 | 0x9630..0x9768 | lwz 64(r29) x 0x2 | VIRTUAL | 2 | {-1: 2} |  | 0x24f0b8 |
| `root/0x3c` (array) | 2 | 0x27664..0x2f670 | add r30,r11,r0 | VIRTUAL | 4 | {-1: 2} |  | 0x24f108 |
| `root/0x3c/0x8` | 356 | 0x2..0xa | expr extsh r0,r9 x 0x2; expr extsh r11,r0 x 0x2 | VIRTUAL | 2 | {-1: 356} | {off: 12777} | 0x24f274,0x2502b4 |
| `root/0x54` | 2 | 0xd9b0..0x28fec | subf r6,r8,r7 | VIRTUAL | 4 | {-1: 2} |  | 0x24f314 |
| `root/0x5c` | 2 | 0x8ed04..0xb2cbc | subf r5,r0,r9 | VIRTUAL | 4 | {-1: 2} |  | 0x24f364 |
| `root/0x64` | 2 | 0x5352..0xf6aa | lwz 96(r7) x 0x2 | VIRTUAL | 2 | {-1: 2} |  | 0x24f3ac |
| `root/0x6c` | 2 | 0xaf98..0x20b38 | expr add r12,r31,r29 x 0x2 | VIRTUAL | 2 | {-1: 2} |  | 0x24f3fc |
| `root/0x70` | 2 | 0xafc..0x20b4 | expr srawi r24,r25,5 x 0x4 | VIRTUAL | 1 | {-1: 2} |  | 0x24f454 |
| `root/0x78` | 2 | 0x8030..0xfd88 | subf r10,r31,r12 | VIRTUAL | 4 | {-1: 2} |  | 0x24f4a4 |
| `root/0x80` | 2 | 0x8ee4..0x19690 | add r30,r22,r0 | VIRTUAL | 4 | {-1: 2} |  | 0x24f4f4 |
| `root/0x88` | 2 | 0x26840..0x66d80 | lwz 132(r31) x 0x20 | VIRTUAL | 16 | {-1: 2} |  | 0x24f68c |
| `root/0x90` | 2 | 0x19e0..0x3960 | add r11,r9,r22 | VIRTUAL | 4 | {-1: 2} |  | 0x24f6dc |
| `root/0x98` | 2 | 0x8a0c0..0xb0520 | subf r30,r7,r6 | VIRTUAL | 16 | {-1: 2} |  | 0x24f72c |
| `root/0xa4` | 2 | 0xa0..0xb0 | mullw r27,r10,r12 | VIRTUAL | 1 | {-1: 2} |  | 0x24f908 |
| `root/0xac` | 2 | 0xc | fixed 0xc | TEMP | 4 | {-2: 2} |  | 0x243e4c |
| `root/0xac/0x4` | 2 | 0x1da57..0x1e711 | lwz 8(r10) x 0x1 | VIRTUAL | 1 | {-1: 2} |  | 0x243ed4 |
| `root/0xb0` | 2 | 0x60 | fixed 0x60 | VIRTUAL | 16 | {-1: 2} |  | 0x238880 |
| `root/0x108` (array) | 2 | 0xa5b4..0x11694 | lhz 254(r7) x 0x54 | VIRTUAL | 4 | {-1: 2} |  | 0x24f990 |
| `root/0x108/0x38` | 4 | 0x54 | fixed 0x54 | TEMP | 4 | {-1: 4} | {alias: 721} | 0x24bb5c |
| `root/0x13c` (array) | 2 | 0x3f0..0x2958 | lwz 312(r29) x 0xa8 | VIRTUAL | 4 | {-1: 2} |  | 0x250040 |
| `root/0x13c/0x8c` | 2 | 0x80 | fixed 0x80 | TEMP | 4 | {-1: 2} | {alias: 1} | 0x248f60 |
| `root/0x13c/0x8c/0x0$` | 2 | 0x14..0x1c | string | VIRTUAL | 1 | {-1: 2} |  | 0x249264 |
| `root/0x13c/0x8c/0x74` (array) | 2 | 0x30 | lbz 103(r24) x 0x10 | VIRTUAL | 4 | {-1: 2} |  | 0x24929c |
| `root/0x13c/0x8c/0x74/0xc` | 6 | 0x70 | fixed 0x70 | TEMP | 4 | {-1: 6} |  | 0x248bfc |
| `root/0x13c/0x8c/0x74/0xc/0x68$` | 6 | 0x14..0x27 | string | VIRTUAL | 1 | {-1: 6} |  | 0x248cc8 |
| `root/0x13c/0x8c/0x78` | 2 | 0x20 | lbz 104(r30) x 0x20 | VIRTUAL | 16 | {-1: 2} |  | 0x249494 |
| `root/0x13c/0x8c/0x7c` | 2 | 0x18 | lbz 105(r4) x 0x4 | VIRTUAL | 4 | {-1: 2} |  | 0x2490b4 |
| `?unlinked` | 6 | 0x1580..0x2b00 | unknown | PHYSICAL_RUNTIME DEFERRED | - |  |  | 0xdead0000 |

Pointer fields seen only as offset or alias pointers (never inline in these zones):

- `root/0x0`: {off: 2}
- `root/0xc`: {off: 2}
- `root/0x14/0x4`: {alias: 3499}
- `root/0x24/0x0`: {off: 39237}
- `root/0x2c/0x0`: {off: 5028}
- `root/0x80/0x10`: {off: 7033}
- `root/0x98/0x0`: {off: 23177}
- `root/0xac/0x0`: {off: 2}
- `root/0x108/0x20`: {alias: 842}
- `root/0x108/0x24`: {alias: 226}
- `root/0x108/0x2c`: {alias: 252}
- `root/0x108/0x38/0x0`: {off: 4}
- `root/0x13c/0x8c/0x70`: {alias: 2}
- `root/0x13c/0x8c/0x7c/0x0`: {alias: 12}


### 13.5 GfxWorld (gfx_map)

| Path | Reads (both zones) | Size per read | Size rule at load site | Block | Align | Marker in parent | Offset/alias seen | Load site (return addr) |
|---|---|---|---|---|---|---|---|---|
| `root` | 2 | 0x454 | fixed 0x454 | TEMP | 4 |  |  | 0x250fcc |
| `root/0x4$` | 2 | 0x9..0xf | string | VIRTUAL | 1 | {-1: 2} |  | 0x2521c8 |
| `root/0x18` | 2 | 0x4e00..0x4fc0 | lwz 0(r11) x 0x20 | VIRTUAL | 4 | {-1: 2} |  | 0x23685c |
| `root/0x20` | 2 | 0x76d4..0x7790 | unknown-after-call | VIRTUAL | 4 | {-1: 2} |  | 0x251068 |
| `root/0x3c` | 2 | 0x14..0x18 | lwz 56(r11) x 0x4 | VIRTUAL | 4 | {-1: 2} |  | 0x2510c4 |
| `root/0x40` | 2 | 0x70 | fixed 0x70 | TEMP | 4 | {-2: 2} |  | 0x248bfc |
| `root/0x40/0x68$` | 2 | 0x7 | string | VIRTUAL | 1 | {-1: 2} |  | 0x248cc8 |
| `root/0x48$` | 2 | 0x10..0x16 | string | VIRTUAL | 1 | {-1: 2} |  | 0x2521f4 |
| `root/0x100` | 2 | 0x170 | fixed 0x170 | VIRTUAL | 16 | {-1: 2} |  | 0x252160 |
| `root/0x120` | 2 | 0x60..0x180 | lwz 284(r7) x 0x20 | VIRTUAL | 4 | {-1: 2} |  | 0x25118c |
| `root/0x158` | 2 | 0x2222c..0x31524 | add r5,r6,r7 | VIRTUAL | 4 | {-1: 2} |  | 0x2373a0 |
| `root/0x15c` | 2 | 0x2cde..0x35fc | lwz 12(r7) x 0x2 | VIRTUAL | 2 | {-1: 2} |  | 0x2372f8 |
| `root/0x168` (array) | 2 | 0x2d8..0x508 | subf r30,r0,r9 | VIRTUAL | 4 | {-1: 2} |  | 0x251318 |
| `root/0x168/0x4` (array) | 108 | 0x1..0x98d0 | add r27,r29,r30; add r5,r6,r7; unknown-after-call | VIRTUAL | 1/4 | {-1: 108} |  | 0x23a010,0x23a1b0,0x251330.. |
| `root/0x168/0x4/0x20` | 414 | 0x2..0x10de | lhz 30(r12) x 0x2; lhz 30(r4) x 0x2; lhz 30(r5) x 0x2 | VIRTUAL | 2 | {-1: 414} | {off: 3941} | 0x23a148,0x23a418,0x23a478 |
| `root/0x168/0x4/0x0` | 310 | 0x24..0x54 | stw r5,-15644(r28); unknown-after-call | VIRTUAL | 4 | {-1: 310} | {off: 310} | 0x23a1c8,0x23a22c,0x23a240.. |
| `root/0x170` (array) | 2 | 0x30 | subf r5,r7,r6 | VIRTUAL | 4 | {-1: 2} |  | 0x24b044 |
| `root/0x170/0xc` | 4 | 0x70 | fixed 0x70 | TEMP | 4 | {-1: 4} |  | 0x248bfc |
| `root/0x170/0xc/0x68$` | 4 | 0x13 | string | VIRTUAL | 1 | {-1: 4} |  | 0x248cc8 |
| `root/0x17c` (array) | 2 | 0xc | subf r29,r12,r11 | VIRTUAL | 4 | {-1: 2} |  | 0x24b290 |
| `root/0x17c/0x0` | 6 | 0x70 | fixed 0x70 | TEMP | 4 | {-1: 6} |  | 0x248bfc |
| `root/0x17c/0x0/0x68$` | 6 | 0x13..0x16 | string | VIRTUAL | 1 | {-1: 6} |  | 0x248cc8 |
| `root/0x20c` | 2 | 0x264440..0x2b0d80 | lwz 156(r10) x 0x10 | VIRTUAL | 16 | {-1: 2} |  | 0x237838 |
| `root/0x218` | 2 | 0x4659dc..0x4cb9b4 | lwz 168(r10) x 0x1 | PHYSICAL | 1 | {-1: 2} |  | 0x237758 |
| `root/0x228` | 2 | 0x9e1e4..0xaba6e | lwz 184(r12) x 0x2 | VIRTUAL | 2 | {-1: 2} |  | 0x24b680 |
| `root/0x24c` | 2 | 0x2f6..0xfea | expr addi r6,r7,1 x 0x2 | VIRTUAL | 2 | {-1: 2} |  | 0x236ea8 |
| `root/0x254` | 2 | 0x3338..0x6ea4 | lwz 32(r11) x 0x1 | VIRTUAL | 4 | {-1: 2} |  | 0x236eec |
| `root/0x25c` | 2 | 0x2428c..0x73c78 | lwz 40(r4) x 0x4 | VIRTUAL | 4 | {-1: 2} |  | 0x236f34 |
| `root/0x264` | 2 | 0x5208a8 | unknown-after-call | VIRTUAL | 4 | {-1: 2} |  | 0x2514d4 |
| `root/0x26c` | 2 | 0x1590..0x2fd0 | subf r31,r11,r5 | VIRTUAL | 4 | {-1: 2} |  | 0x251524 |
| `root/0x290` (array) | 2 | 0x5b0..0xcb8 | lwz 652(r7) x 0x8 | VIRTUAL | 4 | {-1: 2} |  | 0x251570 |
| `root/0x290/0x0` | 589 | 0x80 | fixed 0x80 | TEMP | 4 | {-1: 589} |  | 0x248f60 |
| `root/0x290/0x0/0x0$` | 589 | 0xb..0x7d | string | VIRTUAL | 1 | {-1: 589} |  | 0x249264 |
| `root/0x290/0x0/0x74` (array) | 589 | 0x10..0x90 | lbz 103(r24) x 0x10 | VIRTUAL | 4 | {-1: 589} |  | 0x24929c |
| `root/0x290/0x0/0x74/0xc` | 567 | 0x70 | fixed 0x70 | TEMP | 4 | {-1: 567} | {alias: 2033} | 0x248bfc |
| `root/0x290/0x0/0x74/0xc/0x68$` | 567 | 0x8..0x27 | string | VIRTUAL | 1 | {-1: 567} |  | 0x248cc8 |
| `root/0x290/0x0/0x78` | 570 | 0x20..0x160 | lbz 104(r30) x 0x20 | VIRTUAL | 16 | {-1: 570} |  | 0x249494 |
| `root/0x290/0x0/0x7c` (array) | 589 | 0x4..0x1c | lbz 105(r4) x 0x4 | VIRTUAL | 4 | {-1: 589} |  | 0x2490b4 |
| `root/0x290/0x0/0x7c/0x0` | 25 | 0x8 | fixed 0x8 | TEMP | 4 | {-1: 25} | {alias: 3934} | 0x236784 |
| `root/0x298` | 2 | 0x80 | fixed 0x80 | TEMP | 4 | {-2: 2} |  | 0x248f60 |
| `root/0x298/0x0$` | 2 | 0xa | string | VIRTUAL | 1 | {-1: 2} |  | 0x249264 |
| `root/0x298/0x74` | 2 | 0x10 | lbz 103(r24) x 0x10 | VIRTUAL | 4 | {-1: 2} |  | 0x24929c |
| `root/0x298/0x74/0xc` | 2 | 0x70 | fixed 0x70 | TEMP | 4 | {-1: 2} |  | 0x248bfc |
| `root/0x298/0x74/0xc/0x68$` | 2 | 0xb | string | VIRTUAL | 1 | {-1: 2} |  | 0x248cc8 |
| `root/0x298/0x7c` | 2 | 0x4 | lbz 105(r4) x 0x4 | VIRTUAL | 4 | {-1: 2} |  | 0x2490b4 |
| `root/0x334` | 2 | 0x70 | fixed 0x70 | TEMP | 4 | {-2: 2} |  | 0x248bfc |
| `root/0x298/0x68$` | 1 | 0x9 | string | VIRTUAL | 1 | {other: 1} |  | 0x248cc8 |
| `root/0x334/0x68$` | 1 | 0x9 | string | VIRTUAL | 1 | {-1: 1} |  | 0x248cc8 |
| `root/0x354` (array) | 2 | 0x60..0x120 | subf r30,r12,r10 | VIRTUAL | 4 | {-1: 2} |  | 0x251a30 |
| `root/0x354/0x0` | 60 | 0x2..0x2388 | lhz 0(r11) x 0x2; stw r5,-15584(r28); unknown-after-call | VIRTUAL | 2 | {-1: 60} |  | 0x237418,0x251a48,0x251aac.. |
| `root/0x358` (array) | 2 | 0x40..0xc0 | lwz 276(r5) x 0x8 | VIRTUAL | 4 | {-1: 2} |  | 0x251c00 |
| `root/0x358/0x4` | 28 | 0x50 | add r5,r6,r7 | VIRTUAL | 4 | {-1: 28} |  | 0x2380d8 |
| `root/0x358/0x4/0x4c` | 28 | 0x50..0xa0 | add r12,r31,r0 | VIRTUAL | 4 | {-1: 28} |  | 0x238164 |
| `root/0x3ac` | 2 | 0x1480..0x19bc | lwz 8(r11) x 0x2 | VIRTUAL | 2 | {-1: 2} |  | 0x2509f0 |
| `root/0x3b0` | 2 | 0x291a8..0x2ece8 | add r25,r26,r27 | VIRTUAL | 4 | {-1: 2} |  | 0x250a40 |
| `root/0x3b4` | 2 | 0x42360..0x51e40 | subf r29,r7,r6 | VIRTUAL | 16 | {-1: 2} |  | 0x250a98 |
| `root/0x3bc` | 2 | 0x2d36c..0x337cc | lwz 0(r6) x 0x2c | VIRTUAL | 4 | {-1: 2} |  | 0x250cf4 |
| `root/0x438` | 1 | 0x154 | add r26,r31,r6 | VIRTUAL | 4 | {-1: 1} |  | 0x252008 |
| `?unlinked` | 41 | 0x300..0x400000 | unknown | PHYSICAL_RUNTIME DEFERRED | - |  |  | 0xdead0000 |

Pointer fields seen only as offset or alias pointers (never inline in these zones):

- `root/0x0`: {off: 2}
- `root/0x290/0x0/0x70`: {alias: 589}
- `root/0x298/0x70`: {alias: 2}
- `root/0x298/0x7c/0x0`: {alias: 2}
- `root/0x29c`: {alias: 2}
- `root/0x3b4/0x40`: {alias: 6319}
- `root/0x3bc/0x20`: {alias: 9002}


### 13.6 XModel

| Path | Reads (both zones) | Size per read | Size rule at load site | Block | Align | Marker in parent | Offset/alias seen | Load site (return addr) |
|---|---|---|---|---|---|---|---|---|
| `root` | 557 | 0xf8 | fixed 0xf8 | TEMP | 4 |  |  | 0x24bcd0 |
| `root/0x0$` | 450 | 0xb..0x33 | string | VIRTUAL | 1 | {-1: 450} | {off: 107} | 0x24c708 |
| `root/0x8` | 523 | 0x2..0xd2 | lbz 4(r7) x 0x2 | VIRTUAL | 2 | {-1: 523} | {off: 34} | 0x24c740 |
| `root/0xc` | 100 | 0x1..0x68 | subf r10,r23,r12 | VIRTUAL | 1 | {-1: 100} | {off: 6} | 0x24c8d8 |
| `root/0x10` | 100 | 0x8..0x340 | expr subf r30,r0,r31 x 0x8 | VIRTUAL | 2 | {-1: 100} | {off: 6} | 0x24c91c |
| `root/0x14` | 100 | 0x10..0x680 | expr subf r8,r12,r10 x 0x10 | VIRTUAL | 4 | {-1: 100} | {off: 6} | 0x24c960 |
| `root/0x18` | 523 | 0x1..0x69 | lbz 4(r27) x 0x1 | VIRTUAL | 1 | {-1: 523} | {off: 34} | 0x24c994 |
| `root/0x1c` | 523 | 0x20..0xd20 | lbz 4(r11) x 0x20 | VIRTUAL | 4 | {-1: 523} | {off: 34} | 0x24c6e0 |
| `root/0x20` (array) | 557 | 0x5c..0x4e58 | lbz 6(r12) x 0x5c | VIRTUAL | 4 | {-1: 557} |  | 0x24be48 |
| `root/0x20/0x14` | 538 | 0x5c..0xb3cc | expr add r6,r8,r9 x 0x2 | VIRTUAL | 2 | {-1: 538} | {off: 5} | 0x237220 |
| `root/0x20/0x1c` | 3145 | 0x18..0x152c0 | lhz 4(r10) x 0x10; lhz 4(r4) x 0x8 | VIRTUAL | 16/8 | {-1: 3145} | {off: 520} | 0x239330,0x2393d8,0x239414 |
| `root/0x20/0x24` | 3167 | 0x24..0x20b80 | lhz 4(r10) x 0x10; lhz 4(r10) x 0x4; lhz 4(r10) x 0x8; subf r6,r8,r7 | PHYSICAL | 16/4/8 | {-1: 3167} | {off: 498} | 0x2361b8,0x236294,0x23637c.. |
| `root/0x20/0x2c` (array) | 2961 | 0xc..0x84 | subf r26,r30,r27 | VIRTUAL | 4 | {-1: 2961} | {off: 161} | 0x2390c0 |
| `root/0x20/0x2c/0x8` | 3147 | 0x28 | fixed 0x28 | VIRTUAL | 4 | {-1: 3147} |  | 0x235f6c |
| `root/0x20/0x2c/0x8/0x1c` | 3147 | 0x10..0x3510 | lwz 24(r11) x 0x10 | VIRTUAL | 16 | {-1: 3147} |  | 0x235fb4 |
| `root/0x20/0x2c/0x8/0x24` | 3147 | 0x2..0x27ac | stw r5,-16512(r23); unknown-after-call | VIRTUAL | 2 | {-1: 3147} |  | 0x2392f8,0x239354,0x239378.. |
| `root/0x20/0x8` | 2777 | 0xc..0xc534 | expr add r7,r8,r0 x 0x2 | VIRTUAL | 16 | {-1: 2777} | {off: 888} | 0x23907c |
| `root/0x24` (array) | 557 | 0x4..0x368 | lbz 6(r26) x 0x4 | VIRTUAL | 4 | {-1: 557} |  | 0x24c014 |
| `root/0x24/0x0` | 643 | 0x80 | fixed 0x80 | TEMP | 4 | {-1: 643} | {alias: 3022} | 0x248f60 |
| `root/0x24/0x0/0x0$` | 643 | 0xd..0x2f | string | VIRTUAL | 1 | {-1: 643} |  | 0x249264 |
| `root/0x24/0x0/0x74` (array) | 625 | 0x10..0x70 | lbz 103(r24) x 0x10 | VIRTUAL | 4 | {-1: 625} |  | 0x24929c |
| `root/0x24/0x0/0x74/0xc` | 1591 | 0x70 | fixed 0x70 | TEMP | 4 | {-1: 1591} | {alias: 382} | 0x248bfc |
| `root/0x24/0x0/0x74/0xc/0x68$` | 1591 | 0x5..0x28 | string | VIRTUAL | 1 | {-1: 1591} |  | 0x248cc8 |
| `root/0x24/0x0/0x78` | 620 | 0x20..0x100 | lbz 104(r30) x 0x20 | VIRTUAL | 16 | {-1: 620} |  | 0x249494 |
| `root/0x24/0x0/0x7c` (array) | 625 | 0x8..0x18 | lbz 105(r4) x 0x4 | VIRTUAL | 4 | {-1: 625} |  | 0x2490b4 |
| `root/0x24/0x0/0x7c/0x0` | 82 | 0x8 | fixed 0x8 | TEMP | 4 | {-1: 82} | {alias: 3371} | 0x236784 |
| `root/0xa0` | 407 | 0x24..0x774 | add r28,r29,r30 | VIRTUAL | 4 | {-1: 407} |  | 0x24c1ec |
| `root/0xac` | 557 | 0x2c..0x120c | lbz 4(r7) x 0x2c | VIRTUAL | 4 | {-1: 557} |  | 0x24c43c |
| `root/0xd0` | 557 | 0x10..0xda0 | lbz 6(r24) x 0x10 | VIRTUAL | 4 | {-1: 557} |  | 0x24c4a0 |
| `blk6+?` | 1 | 0x2b00 | unknown | PHYSICAL_RUNTIME DEFERRED | - |  |  | 0xdead0000 |
| `?unlinked` | 86 | 0x100..0xc0000 | unknown | PHYSICAL_RUNTIME DEFERRED | - |  |  | 0xdead0000 |
| `root/0xe8` | 26 | 0x54 | fixed 0x54 | TEMP | 4 | {-2: 26} | {alias: 71} | 0x24bb5c |
| `root/0xe8/0x0$` | 26 | 0x5..0x17 | string | VIRTUAL | 1 | {-1: 26} |  | 0x24bc48 |
| `root/0xf0` (array) | 118 | 0x4..0x48 | lbz 236(r31) x 0x4 | VIRTUAL | 4 | {-1: 118} |  | 0x24c4f8 |
| `root/0xf0/0x0` | 167 | 0xc | fixed 0xc | VIRTUAL | 4 | {-1: 167} |  | 0x23acbc |
| `root/0xf0/0x0/0x4` (array) | 167 | 0x44..0x3b8 | add r5,r6,r7 | VIRTUAL | 16 | {-1: 167} |  | 0x23ad08 |
| `root/0xf0/0x0/0x4/0x44` | 7 | 0x60 | fixed 0x60 | VIRTUAL | 16 | {-1: 7} |  | 0x23a788 |
| `root/0xf0/0x0/0x4/0x44/0x58` | 7 | 0x60 | subf r12,r24,r23 | VIRTUAL | 4 | {-1: 7} |  | 0x23aba0 |
| `root/0xf0/0x0/0x4/0x0` | 208 | 0x60 | fixed 0x60 | VIRTUAL | 16 | {-1: 208} |  | 0x23a788 |
| `root/0xf0/0x0/0x4/0x0/0x58` | 208 | 0x60..0x9fc | subf r12,r24,r23 | VIRTUAL | 4 | {-1: 208} |  | 0x23aba0 |
| `root/0xf0/0x0/0x4/0x0/0x20` (array) | 128 | 0xc..0x150 | subf r5,r7,r6 | VIRTUAL | 4 | {-1: 128} |  | 0x23a7d4 |
| `root/0xf0/0x0/0x4/0x0/0x20/0x0` | 1209 | 0x14 | fixed 0x14 | VIRTUAL | 4 | {-1: 1209} |  | 0x23a9e4,0x23aa94,0x23aac0.. |
| `blk4+?` | 3 | 0xb00..0x2b00 | unknown | PHYSICAL_RUNTIME DEFERRED | - |  |  | 0xdead0000 |

Pointer fields seen only as offset or alias pointers (never inline in these zones):

- `root/0x24/0x0/0x70`: {alias: 625}
- `root/0xf0/0x0/0x4/0x0/0x5c`: {off: 128}


### 13.7 PhysPreset

| Path | Reads (both zones) | Size per read | Size rule at load site | Block | Align | Marker in parent | Offset/alias seen | Load site (return addr) |
|---|---|---|---|---|---|---|---|---|
| `root` | 12 | 0x54 | fixed 0x54 | TEMP | 4 |  |  | 0x24bb5c |
| `root/0x0$` | 12 | 0x9..0x1d | string | VIRTUAL | 1 | {-1: 12} |  | 0x24bc48 |


### 13.8 DestructibleDef

| Path | Reads (both zones) | Size per read | Size rule at load site | Block | Align | Marker in parent | Offset/alias seen | Load site (return addr) |
|---|---|---|---|---|---|---|---|---|
| `root` | 22 | 0x18 | fixed 0x18 | TEMP | 4 |  |  | 0x254398 |
| `root/0x0$` | 8 | 0xf..0x21 | string | VIRTUAL | 1 | {-1: 8} | {off: 14} | 0x254620 |
| `root/0x10` (array) | 22 | 0x138..0x1c08 | lwz 12(r26) x 0x138 | VIRTUAL | 4 | {-1: 22} |  | 0x25444c |
| `root/0x10/0x48$` | 3 | 0x8..0x16 | string | VIRTUAL | 1 | {-1: 3} | {off: 2} | 0x254130 |
| `root/0x10/0x4c$` | 1 | 0x10 | string | VIRTUAL | 1 | {-1: 1} |  | 0x254160 |
| `root/0x10/0x14$` | 45 | 0x5..0x2c | string | VIRTUAL | 1 | {-1: 45} | {off: 49} | 0x254104 |
| `root/0x10/0x18$` | 5 | 0x9..0x19 | string | VIRTUAL | 1 | {-1: 5} | {off: 6} | 0x254130 |
| `root/0x10/0x1c$` | 2 | 0xf..0x14 | string | VIRTUAL | 1 | {-1: 2} |  | 0x254160 |
| `root/0x10/0x44$` | 23 | 0x13..0x2a | string | VIRTUAL | 1 | {-1: 23} | {off: 53} | 0x254104 |
| `root/0x10/0x74$` | 13 | 0xe..0x24 | string | VIRTUAL | 1 | {-1: 13} | {off: 10} | 0x254104 |
| `root/0x10/0x78$` | 3 | 0x6..0x1b | string | VIRTUAL | 1 | {-1: 3} | {off: 2} | 0x254130 |
| `root/0x10/0x7c$` | 2 | 0xd | string | VIRTUAL | 1 | {-1: 2} | {off: 2} | 0x254160 |
| `root/0x10/0xa4$` | 8 | 0x16..0x1c | string | VIRTUAL | 1 | {-1: 8} | {off: 8} | 0x254104 |
| `root/0x10/0x114$` | 50 | 0x16..0x28 | string | VIRTUAL | 1 | {-1: 50} | {off: 28} | 0x254348 |
| `root/0x10/0x10c` | 27 | 0xa88 | fixed 0xa88 | TEMP | 4 | {-1: 27} | {alias: 10} | 0x24a2f4 |
| `root/0x10/0x10c/0x0$` | 27 | 0x17..0x1e | string | VIRTUAL | 1 | {-1: 27} |  | 0x24a494 |
| `root/0x10/0x10c/0xc4$` | 4 | 0x12..0x1a | string | VIRTUAL | 1 | {-1: 4} | {off: 23} | 0x24a2b8 |
| `root/0x10/0x10c/0xd4$` | 1 | 0x7 | string | VIRTUAL | 1 | {-1: 1} | {off: 26} | 0x24a264 |
| `root/0x10/0x10c/0x1c$` | 4 | 0x1..0x12 | string | VIRTUAL | 1 | {-1: 4} | {off: 23} | 0x24a2b8 |
| `root/0x10/0x10c/0x2c$` | 1 | 0x7 | string | VIRTUAL | 1 | {-1: 1} | {off: 26} | 0x24a264 |
| `root/0x10/0x10c/0x16c$` | 1 | 0x1 | string | VIRTUAL | 1 | {-1: 1} | {off: 26} | 0x24a2b8 |

Pointer fields seen only as offset or alias pointers (never inline in these zones):

- `root/0x4`: {alias: 22}
- `root/0x10/0x10`: {alias: 31}
- `root/0x10/0x2c`: {alias: 1}
- `root/0x10/0x10c/0x17c`: {off: 27}
- `root/0x10/0x10c/0x214`: {off: 27}
- `root/0x10/0x10c/0x224`: {off: 27}
- `root/0x10/0x10c/0x2bc`: {off: 27}
- `root/0x10/0x10c/0x2cc`: {off: 27}
- `root/0x10/0x10c/0x364`: {off: 27}
- `root/0x10/0x10c/0x374`: {off: 27}
- `root/0x10/0x10c/0x40c`: {off: 27}
- `root/0x10/0x10c/0x41c`: {off: 27}
- `root/0x10/0x10c/0x4b4`: {off: 27}
- `root/0x10/0x10c/0x4c4`: {off: 27}
- `root/0x10/0x10c/0x55c`: {off: 27}
- `root/0x10/0x10c/0x56c`: {off: 27}
- `root/0x10/0x10c/0x604`: {off: 27}
- `root/0x10/0x10c/0x614`: {off: 27}
- `root/0x10/0x10c/0x6ac`: {off: 27}
- `root/0x10/0x10c/0x6bc`: {off: 27}
- `root/0x10/0x10c/0x754`: {off: 27}
- `root/0x10/0x10c/0x764`: {off: 27}
- `root/0x10/0x10c/0x7fc`: {off: 27}
- `root/0x10/0x10c/0x80c`: {off: 27}
- `root/0x10/0x10c/0x8a4`: {off: 27}
- `root/0x10/0x10c/0x8b4`: {off: 27}
- `root/0x10/0x10c/0x94c`: {off: 27}
- `root/0x10/0x10c/0x95c`: {off: 27}
- `root/0x10/0x10c/0x9f4`: {off: 27}
- `root/0x10/0x10c/0xa04`: {off: 27}
- `root/0x10/0x70`: {alias: 4}
- `root/0x10/0x5c`: {alias: 36}
- `root/0x10/0x40`: {alias: 28}
- `root/0x10/0x20`: {alias: 15}

