# Typed fields over the parsed bytes (Phase 2, M4)

Status: every byte value a parse keeps has a schema; every struct the handlers load is covered
field by field (named fields plus explicit `unk_0x..` gaps); setting every field to its own
value leaves every asset of all 178 zones byte-identical; and the sanity checks below show the
types fit the data. The full tables are in section 4.

Code: `src/opent5/xfile/schema.py` (types, `Struct`, views, `to_dict`),
`src/opent5/xfile/structs.py` (every struct and node kind), `src/opent5/xfile/layouts_pc.py`
(generated PC layouts), `src/opent5/xfile/fieldcheck.py` (the checks), `tools/fields_all.py`
(this run). Companion: `rewrite-all.md` (raw bytes stay the canonical storage),
`structs-content.md`, `structs-map.md`, `walk-all.md`, `docs/extract.md` (vertex formats).

## 1. Model

Raw bytes stay canonical: a node keeps the exact bytes of every struct and array the loader
reads, and that is what writes back. The field layer puts names and types on those bytes in
place:

- Each node carries `"_t"`, its kind, set by the handler that filled it. `KINDS[kind][key]` is
  the schema of `node[key]`: a `Struct` (one struct), an `ArrayOf` (a run of structs or
  scalars, or `"text"`), or a `Dynamic` (chosen from the node's own bytes: XSurface vertex
  streams by `flags`).
- A `Struct` is a list of `Field`s (name, offset, type, role). Types: u8/s8/u16/s16/u32/s32/
  u64/s64, f16 (half), f32, f64, vec2/vec3/vec4, mat3/mat4, `cmp` (the RSX 11:11:10 packed
  vector), `char[n]`, `bytes[n]`, and arrays `T[n]`. Gaps nobody has named are filled with
  `unk_0x..` fields at construction, so a struct is always covered byte for byte and the
  unnamed share is visible.
- Roles: `ptr` (a pointer the loader follows or converts) and `count` (sizes an array, with the
  node key it sizes) are read-only through a view; setting one raises an error that says to
  change the arrays and re-lay the zone instead. `enum` fields carry their value names.

API:

    from opent5.xfile.schema import view
    v = view(asset.data)                    # NodeView
    v.fields.numsurfs                       # header field
    v.fields["lodInfo[0].dist"] = 512.0     # written in place, big-endian
    v.struct("box_brush").mins              # a struct-valued key
    v.array("planes")                       # numpy structured array over the bytes
    v.array("vertices", writable=True)["xyz"][0] = (0, 0, 0)
    v.child("surfs")[0].array("verts0")     # per-surface vertex format, by flags
    v.to_dict()                             # JSON-ready tree

`to_dict` decodes every struct into named fields, arrays of structs into lists, text values
into strings; long u8 blobs (pixels, layer data, sound data) appear as `{"bytes": n}`.

## 2. Where the names come from

- `pc(...)` structs: the PC T5 layout from OpenAssetTools' T5_Assets.h (compiled 32-bit, member
  offsets printed by gdb, flattened; docs/provenance.md). Used only where the PS3 struct has the
  PC size and every pointer the PS3 loader follows sits at the PC offset; in several cases a
  documented PS3 difference is applied (`since` / `shift`: listBoxDef_s +0x20, editFieldDef_s and
  windowDef_t per-client arrays; `upto` / `extra`: XModel, XAnimParts).
- `S(...)` structs: PS3 layouts from structs-content.md, structs-map.md, walk-all.md and
  docs/extract.md, with the PC member order where the loaded offsets confirm it (menuDef_t: the
  PC order with cursorItem[4] lands onOpen, allowedBinding, rectXExp and items exactly on the
  offsets the loader reads; textDef_s: textRect[4] lands text on +0x80).
- Vertex formats (XSurface verts0 and stream, GfxWorld positions, static model axes) from
  docs/extract.md section 3.

## 3. The checks (tools/fields_all.py)

Per zone, after parsing:

1. Coverage: for every struct instance, bytes under named fields and under `unk_` fields;
   byte values with no schema at all are listed separately (none remain).
2. Round trip: every field of every single struct and array element is set to its own value
   through the view (`StructView.__setitem__`, the same encode path an editor uses), and every
   asset must then write back to its exact file span. Long homogeneous arrays (vertices, planes,
   brushes, path nodes...) run the same conversions vectorised in numpy: floats through float64
   and back, `char[n]` values checked for bytes after the NUL that re-encoding would drop.
3. Sanity, to show the types are right: float fields finite, below 1e12 in magnitude (FLT_MAX
   sentinels allowed) and not denormal (a denormal is almost always an integer read as a float);
   unit-vector fields (`normal`, `dir`, every `cmp` vector) of length 1; positions (`xyz`,
   `origin`, `placement.origin`, `lightingOrigin`, path node origins) inside the GfxWorld bounds
   for map assets and inside the XModel bounds for model vertices; each count equal to the
   length of the array it sizes (or that length minus one, for arrays of count + 1); enum values
   among the known names.

## 4. Results

<!-- FIELDS START -->
178 zones, 125327 assets, wall 223 s. Struct bytes under a schema: 2974129630; named 2943906160 (98.98%), unk_ 30223470 (1.02%). Opaque blobs (pixels, vertex streams, programs, text): 3036934699 bytes. Byte values with no schema: 0 bytes. Field round-trip failures: 0; assets changed by setting every field to its own value: 0. Zones with errors: 0.

### Coverage by struct

| Struct | Instances | Bytes | Named % | unk_ % | Round-trip failures | char tails |
|---|---|---|---|---|---|---|
| GfxLightGridColors | 2877487 | 483417816 | 100.0 | 0.0 | 0 | 0 |
| XStreamNTUV | 38540351 | 462484212 | 100.0 | 0.0 | 0 | 0 |
| GfxWorldVertex | 21645754 | 346332064 | 100.0 | 0.0 | 0 | 0 |
| XVertexPacked | 42970413 | 343763304 | 100.0 | 0.0 | 0 | 0 |
| XVertexPackedNT | 17370572 | 277929152 | 100.0 | 0.0 | 0 | 0 |
| XStreamNTUVC | 4750810 | 76012960 | 100.0 | 0.0 | 0 | 0 |
| cbrush_t | 772645 | 74173920 | 95.8 | 4.2 | 0 | 0 |
| XSurfaceCollisionNode | 4080155 | 65282480 | 100.0 | 0.0 | 0 | 0 |
| XStreamUVC | 7000164 | 56001312 | 100.0 | 0.0 | 0 | 0 |
| CollisionAabbTree | 1735911 | 55549152 | 100.0 | 0.0 | 0 | 0 |
| GfxSurface | 574502 | 55152192 | 83.3 | 16.7 | 0 | 0 |
| GfxLightGridEntry | 12695224 | 50780896 | 100.0 | 0.0 | 0 | 0 |
| snd_alias_t | 583275 | 48995100 | 98.8 | 1.2 | 0 | 0 |
| MaterialShaderArgument | 5240211 | 41921688 | 100.0 | 0.0 | 0 | 0 |
| FxElemVelStateSample | 432760 | 41544960 | 100.0 | 0.0 | 0 | 0 |
| XStreamUV | 10232836 | 40931344 | 100.0 | 0.0 | 0 | 0 |
| FxElemVisStateSample | 605903 | 29083344 | 100.0 | 0.0 | 0 | 0 |
| cLeafBrushNode_s | 1237970 | 24759400 | 95.0 | 5.0 | 0 | 0 |
| cLeaf_s | 504383 | 22192852 | 95.5 | 4.5 | 0 | 0 |
| FxElemDef | 67701 | 19768692 | 100.0 | 0.0 | 0 | 0 |
| GfxStaticModelDrawInst | 422142 | 18574248 | 90.9 | 9.1 | 0 | 0 |
| cbrushside_t | 1527534 | 18330408 | 100.0 | 0.0 | 0 | 0 |
| cStaticModel_s | 222275 | 17782000 | 97.5 | 2.5 | 0 | 0 |
| cplane_s | 866507 | 17330140 | 100.0 | 0.0 | 0 | 0 |
| itemDef_s | 60661 | 16985080 | 98.6 | 1.4 | 0 | 0 |
| GfxStaticModelInst | 422142 | 16885680 | 100.0 | 0.0 | 0 | 0 |
| XSurface | 177607 | 16339844 | 56.5 | 43.5 | 0 | 0 |
| expressionRpn | 1165569 | 13986828 | 100.0 | 0.0 | 0 | 0 |
| CollisionPartition | 626340 | 12526800 | 90.0 | 10.0 | 0 | 0 |
| pathnode_t | 95713 | 12251264 | 100.0 | 0.0 | 0 | 0 |
| GfxImage | 107284 | 12015808 | 100.0 | 0.0 | 0 | 0 |
| CollisionBorder | 415303 | 11628484 | 100.0 | 0.0 | 0 | 0 |
| StringTableCell | 1447012 | 11576096 | 100.0 | 0.0 | 0 | 0 |
| Material | 85171 | 10901888 | 93.8 | 6.2 | 0 | 0 |
| XBoneInfo | 235472 | 10360768 | 93.2 | 6.8 | 0 | 0 |
| GfxAabbTree | 251034 | 10041360 | 100.0 | 0.0 | 0 | 0 |
| pathlink_s | 734728 | 8816736 | 100.0 | 0.0 | 0 | 0 |
| XModel | 30861 | 7653528 | 84.3 | 15.7 | 0 | 0 |
| MaterialConstantDef | 235000 | 7520000 | 100.0 | 0.0 | 0 | 0 |
| DObjAnimMat | 209304 | 6697728 | 100.0 | 0.0 | 0 | 0 |
| XSurfaceCollisionTree | 141816 | 5672640 | 100.0 | 0.0 | 0 | 0 |
| textDef_s | 34060 | 4768400 | 100.0 | 0.0 | 0 | 0 |
| MaterialTextureDef | 290813 | 4653008 | 100.0 | 0.0 | 0 | 0 |
| DynEntityDef | 53489 | 4493076 | 100.0 | 0.0 | 0 | 0 |
| WeaponDef | 2136 | 4391616 | 98.0 | 2.0 | 0 | 0 |
| dmaterial_t | 57012 | 4104864 | 100.0 | 0.0 | 0 | 0 |
| snd_alias_list_t | 204960 | 4099200 | 100.0 | 0.0 | 0 | 0 |
| cNode_t | 482771 | 3862168 | 100.0 | 0.0 | 0 | 0 |
| MaterialPass | 160183 | 3844392 | 83.3 | 16.7 | 0 | 0 |
| MaterialTechniqueSet | 12589 | 3675988 | 100.0 | 0.0 | 0 | 0 |
| animParamsDef_t | 33621 | 3631068 | 100.0 | 0.0 | 0 | 0 |
| LoadedSound | 57169 | 3430140 | 100.0 | 0.0 | 0 | 0 |
| StreamedSound | 135282 | 3246768 | 100.0 | 0.0 | 0 | 0 |
| XAnimParts | 27801 | 2891304 | 99.0 | 1.0 | 0 | 0 |
| XModelHighMipBounds | 177607 | 2841712 | 100.0 | 0.0 | 0 | 0 |
| GenericEventScript | 63902 | 2811688 | 100.0 | 0.0 | 0 | 0 |
| XVertexFloat | 167165 | 2674640 | 100.0 | 0.0 | 0 | 0 |
| GfxStreamingAabbTree | 83058 | 2657856 | 100.0 | 0.0 | 0 | 0 |
| UIAnimInfo | 8769 | 2069484 | 100.0 | 0.0 | 0 | 0 |
| MaterialPixelShader | 157122 | 1885464 | 100.0 | 0.0 | 0 | 0 |
| MaterialStateBitsRef | 429918 | 1719672 | 100.0 | 0.0 | 0 | 0 |
| XRigidVertList | 141816 | 1701792 | 100.0 | 0.0 | 0 | 0 |
| cmodel_t | 21550 | 1551600 | 97.2 | 2.8 | 0 | 0 |
| SoundFile | 192451 | 1539608 | 75.0 | 25.0 | 0 | 0 |
| GfxPortal | 21150 | 1438200 | 95.6 | 4.4 | 0 | 0 |
| GfxBrushModel | 21550 | 1293000 | 100.0 | 0.0 | 0 | 0 |
| MaterialTechnique | 160183 | 1281464 | 100.0 | 0.0 | 0 | 0 |
| menuDef_t | 2726 | 1155824 | 98.1 | 1.9 | 0 | 0 |
| XModelCollSurf_s | 31742 | 1142712 | 100.0 | 0.0 | 0 | 0 |
| PhysConstraint | 6334 | 1064112 | 96.4 | 3.6 | 0 | 0 |
| ComPrimaryLight | 4123 | 907060 | 100.0 | 0.0 | 0 | 0 |
| PhysConstraints | 304 | 819584 | 96.4 | 3.6 | 0 | 0 |
| pathnode_tree_t | 51008 | 816128 | 100.0 | 0.0 | 0 | 0 |
| WeaponVariantDef | 3493 | 796404 | 98.7 | 1.3 | 0 | 0 |
| FxEffectDef | 12924 | 775440 | 100.0 | 0.0 | 0 | 0 |
| MaterialHandle | 177607 | 710428 | 100.0 | 0.0 | 0 | 0 |
| XAnimNotifyInfo | 79607 | 636856 | 75.0 | 25.0 | 0 | 0 |
| BrushWrapper | 6242 | 599232 | 100.0 | 0.0 | 0 | 0 |
| ddlMemberDef_t | 11649 | 559152 | 100.0 | 0.0 | 0 | 0 |
| DestructiblePiece | 1744 | 544128 | 95.2 | 4.8 | 0 | 0 |
| ExpressionStatement | 33981 | 543696 | 100.0 | 0.0 | 0 | 0 |
| PhysGeomInfo | 7390 | 502520 | 100.0 | 0.0 | 0 | 0 |
| GfxLightRegionAxis | 24297 | 485940 | 100.0 | 0.0 | 0 | 0 |
| GenericEventHandler | 35466 | 425592 | 100.0 | 0.0 | 0 | 0 |
| ScriptCondition | 26223 | 419568 | 100.0 | 0.0 | 0 | 0 |
| listBoxDef_s | 558 | 390600 | 91.4 | 8.6 | 0 | 0 |
| MaterialVertexShader | 24121 | 385936 | 62.5 | 37.5 | 0 | 0 |
| rectData_s | 5162 | 330368 | 100.0 | 0.0 | 0 | 0 |
| GfxLightRegionHull | 3972 | 317760 | 100.0 | 0.0 | 0 | 0 |
| MaterialMemory | 36493 | 291944 | 100.0 | 0.0 | 0 | 0 |
| multiDef_s | 733 | 290268 | 100.0 | 0.0 | 0 | 0 |
| XAnimPartTransFrames | 8760 | 245280 | 100.0 | 0.0 | 0 | 0 |
| LocalizeEntry | 24955 | 199640 | 100.0 | 0.0 | 0 | 0 |
| GfxCell | 3484 | 195104 | 94.6 | 5.4 | 0 | 0 |
| snd_snapshot | 508 | 176784 | 100.0 | 0.0 | 0 | 0 |
| Glass | 1311 | 162564 | 98.4 | 1.6 | 0 | 0 |
| PhysPreset | 1685 | 141540 | 100.0 | 0.0 | 0 | 0 |
| XAnimDeltaPart | 13942 | 111536 | 100.0 | 0.0 | 0 | 0 |
| FxElemVisuals | 26319 | 105276 | 100.0 | 0.0 | 0 | 0 |
| FxTrailVertex | 4671 | 93420 | 100.0 | 0.0 | 0 | 0 |
| RawFile | 7168 | 86016 | 100.0 | 0.0 | 0 | 0 |
| GfxWorld | 76 | 84208 | 62.3 | 37.7 | 0 | 0 |
| Glyph | 3504 | 84096 | 95.8 | 4.2 | 0 | 0 |
| EmblemIcon | 1996 | 79840 | 100.0 | 0.0 | 0 | 0 |
| snd_radverb | 767 | 73632 | 100.0 | 0.0 | 0 | 0 |
| ItemKeyHandler | 5238 | 62856 | 100.0 | 0.0 | 0 | 0 |
| PhysGeomList | 4851 | 58212 | 100.0 | 0.0 | 0 | 0 |
| GfxShadowGeometry | 4123 | 49476 | 100.0 | 0.0 | 0 | 0 |
| MaterialVertexDeclaration | 1443 | 49062 | 100.0 | 0.0 | 0 | 0 |
| focusItemDef_s | 6083 | 48664 | 100.0 | 0.0 | 0 | 0 |
| GfxStateBits | 5936 | 47488 | 100.0 | 0.0 | 0 | 0 |
| GfxVolumePlane | 2948 | 47168 | 100.0 | 0.0 | 0 | 0 |
| XAnimPartTransHead | 10799 | 43196 | 75.0 | 25.0 | 0 | 0 |
| GfxLightCorona | 1222 | 39104 | 100.0 | 0.0 | 0 | 0 |
| GfxLightRegion | 4123 | 32984 | 100.0 | 0.0 | 0 | 0 |
| GfxLight | 76 | 27968 | 95.7 | 4.3 | 0 | 0 |
| XAnimDeltaPartQuatHead | 6341 | 25364 | 50.0 | 50.0 | 0 | 0 |
| clipMap_t | 76 | 25232 | 98.2 | 1.8 | 0 | 0 |
| vec3 | 2039 | 24468 | 100.0 | 0.0 | 0 | 0 |
| ddlStructDef_t | 1508 | 24128 | 100.0 | 0.0 | 0 | 0 |
| Occluder | 309 | 21012 | 100.0 | 0.0 | 0 | 0 |
| EmblemBackground | 840 | 20160 | 100.0 | 0.0 | 0 | 0 |
| Collmap | 4896 | 19584 | 100.0 | 0.0 | 0 | 0 |
| MenuCell | 1494 | 17928 | 100.0 | 0.0 | 0 | 0 |
| XAnimDeltaPartQuatFrames | 4286 | 17144 | 100.0 | 0.0 | 0 | 0 |
| FlameTable | 30 | 14280 | 100.0 | 0.0 | 0 | 0 |
| DestructibleDef | 551 | 13224 | 100.0 | 0.0 | 0 | 0 |
| StringTable | 637 | 12740 | 100.0 | 0.0 | 0 | 0 |
| FxTrailDef | 437 | 12236 | 100.0 | 0.0 | 0 | 0 |
| MenuRow | 378 | 9072 | 100.0 | 0.0 | 0 | 0 |
| ddlEnumDef_t | 645 | 7740 | 100.0 | 0.0 | 0 | 0 |
| SndBank | 158 | 6320 | 100.0 | 0.0 | 0 | 0 |
| GfxReflectionProbeVolumeData | 62 | 5952 | 100.0 | 0.0 | 0 | 0 |
| snd_group | 72 | 5760 | 100.0 | 0.0 | 0 | 0 |
| FxElemMarkVisuals | 703 | 5624 | 100.0 | 0.0 | 0 | 0 |
| GfxExposureVolume | 227 | 5448 | 100.0 | 0.0 | 0 | 0 |
| PrimedSound | 423 | 5076 | 100.0 | 0.0 | 0 | 0 |
| GfxReflectionProbe | 204 | 4896 | 100.0 | 0.0 | 0 | 0 |
| ComWorld | 76 | 4864 | 100.0 | 0.0 | 0 | 0 |
| MenuList | 402 | 4824 | 100.0 | 0.0 | 0 | 0 |
| snd_curve | 48 | 4800 | 100.0 | 0.0 | 0 | 0 |
| GlassDef | 75 | 4500 | 100.0 | 0.0 | 0 | 0 |
| snd_master | 24 | 4224 | 100.0 | 0.0 | 0 | 0 |
| editFieldDef_s | 87 | 4176 | 100.0 | 0.0 | 0 | 0 |
| snd_snapshot_group | 122 | 3904 | 100.0 | 0.0 | 0 | 0 |
| ddlDef_t | 129 | 3612 | 100.0 | 0.0 | 0 | 0 |
| GameWorld | 76 | 3344 | 100.0 | 0.0 | 0 | 0 |
| snd_pan | 50 | 3000 | 100.0 | 0.0 | 0 | 16 |
| Glasses | 45 | 2520 | 100.0 | 0.0 | 0 | 0 |
| GfxLightDef | 137 | 2192 | 81.2 | 18.8 | 0 | 0 |
| GfxShadowMapVolume | 114 | 1824 | 100.0 | 0.0 | 0 | 0 |
| PackIndex | 110 | 1320 | 100.0 | 0.0 | 0 | 0 |
| GfxLightmapArray | 106 | 1272 | 100.0 | 0.0 | 0 | 0 |
| GfxHeroLight | 18 | 1008 | 100.0 | 0.0 | 0 | 0 |
| MapEnts | 76 | 912 | 100.0 | 0.0 | 0 | 0 |
| EmblemLayer | 48 | 576 | 100.0 | 0.0 | 0 | 0 |
| XGlobals | 14 | 560 | 100.0 | 0.0 | 0 | 0 |
| GfxWorldLodChain | 20 | 480 | 91.7 | 8.3 | 0 | 0 |
| Font_s | 19 | 456 | 100.0 | 0.0 | 0 | 0 |
| EmblemCategory | 48 | 384 | 100.0 | 0.0 | 0 | 0 |
| GfxHeroLightTree | 16 | 384 | 100.0 | 0.0 | 0 | 0 |
| TextureList | 46 | 368 | 100.0 | 0.0 | 0 | 0 |
| GfxWorldLodInfo | 30 | 360 | 83.3 | 16.7 | 0 | 0 |
| GfxOutdoorBounds | 15 | 360 | 100.0 | 0.0 | 0 | 0 |
| snd_context | 8 | 320 | 100.0 | 0.0 | 0 | 0 |
| ddlRoot_t | 30 | 240 | 100.0 | 0.0 | 0 | 0 |
| FxImpactTable | 26 | 208 | 100.0 | 0.0 | 0 | 0 |
| EmblemSet | 4 | 176 | 100.0 | 0.0 | 0 | 0 |
| gameMsgDef_s | 18 | 144 | 100.0 | 0.0 | 0 | 0 |
| SndPatch | 6 | 120 | 100.0 | 0.0 | 0 | 0 |
| SndDriverGlobals | 2 | 104 | 100.0 | 0.0 | 0 | 0 |

### Sanity checks that fail

| Check | Values | Failing | Example | Reason |
|---|---|---|---|---|
| PhysConstraint.dir: unit length | 6021 | 6021 | 90.00000000000009 | length differs from 1 by > 0.01 |
| GfxHeroLight.dir: unit length | 18 | 4 | 6.248832154209245e-11 | length differs from 1 by more than 0.01 |
| GfxHeroLight.dir: float plausible | 54 | 4 | 7.2752725774693e-39 | non-finite, |x| > 1e12 (not FLT_MAX), or denormal |
| FxElemDef.u.topWidth: float plausible | 67701 | 2327 | 3.587324068671532e-43 | non-finite, |x| > 1e12 (not FLT_MAX), or denormal |
| FxElemDef.u.bottomWidth: float plausible | 67701 | 1754 | 7.174648137343064e-43 | non-finite, |x| > 1e12 (not FLT_MAX), or denormal |
| GfxLight.origin: inside the world / asset bounds | 76 | 1 | [0.0, 0.0, 0.0] | outside ((-64.0, -64.0, -256.0), (64.0, 64.0, -192.0)) |
| XSurfaceCollisionTree.scale: float plausible | 425448 | 5476 | inf | non-finite, |x| > 1e12 (not FLT_MAX), or denormal |
| GfxReflectionProbe.origin: inside the world / asset bounds | 204 | 2 | [0.0, 0.0, 0.0] | outside ((-64.0, -64.0, -256.0), (64.0, 64.0, -192.0)) |
| pathnode_t.constant.vOrigin: inside the world / asset bounds | 95713 | 128 | [0.0, 0.0, 0.0] | outside ((-64.0, -64.0, -256.0), (64.0, 64.0, -192.0)) |
| ComPrimaryLight.origin: inside the world / asset bounds | 4123 | 2 | [0.0, 0.0, 0.0] | outside ((-64.0, -64.0, -256.0), (64.0, 64.0, -192.0)) |
| XVertexFloat.xyz: inside the world / asset bounds | 167165 | 28 | [257.9833068847656, 226.18948364257812, 115.45625305175781] | outside mins (-244.46656799316406, -263.53375244140625, -35.202049255371094) / maxs (264.1085510253906, 258.4495849609375, 106.61158752441406) |
| cStaticModel_s.origin: inside the world / asset bounds | 222275 | 10 | [1055.699951171875, -3219.39990234375, -204.10000610351562] | outside mins (-6464.0, -5797.0, -188.0) / maxs (5824.0, 5787.0, 1472.0) |
| CollisionAabbTree.origin: inside the world / asset bounds | 1735911 | 54 | [-1050.919677734375, 2118.88330078125, -457.29644775390625] | outside mins (-1100800.0, -11752.0, -456.0) / maxs (11544.0, 1388800.0, 6936.0) |
| GfxStaticModelDrawInst.placement.origin: inside the world / asset bounds | 422142 | 12 | [13446.0, -14997.099609375, -7242.2998046875] | outside ((-32768.0, -24576.0, -7168.0), (28672.0, 20480.0, 12800.0)) |
| GfxStaticModelInst.lightingOrigin: inside the world / asset bounds | 422142 | 8 | [7484.10546875, -24766.3671875, 13843.9609375] | outside mins (-23744.0, -28080.0, -180.0) / maxs (17088.0, 12752.0, 12480.0) |
| cplane_s.normal: unit length | 866507 | 1 | 1.1114246864446248e-19 | length differs from 1 by > 0.01 |
| cplane_s.normal: float plausible | 2599521 | 1 | 3.905250664257546e-40 | non-finite, |x| > 1e12 (not FLT_MAX), or denormal |
| XStreamNTUV.uv: float plausible | 77080702 | 10 | nan/inf | non-finite, |x| > 1e12 (not FLT_MAX), or denormal |

1138 other checks pass on every value (381879626 values).
<!-- FIELDS END -->

## 5. What the failing checks mean

No failure points at a wrong type; each is a value the data really holds:

- `FxElemDef.u.topWidth` / `bottomWidth`: `u` is a union (billboard trim, or an index for other
  element types); the PC layout names its first member. The denormal values are the integer
  members of the other union cases: the type is right for billboard elements only.
- `XSurfaceCollisionTree.scale`: infinite on degenerate trees (a zero extent along an axis).
- `PhysConstraint.dir`: never a unit vector (lengths such as 90 and 160). The PC name suggests a
  direction; the data holds a scaled vector. Name kept, meaning open.
- `GfxHeroLight.dir`: four lights with a zero (or denormal) direction.
- Positions outside bounds: path nodes, primary lights, reflection probes and the sun light at
  exactly (0, 0, 0) in zones whose world lies elsewhere (the 128 spare path nodes of a PathData,
  unused light slots); a few static models, AABB tree origins and model vertices lie up to a few
  units past bounds that are themselves rounded.
- One clip plane with a zero normal and ten half-float UVs that are NaN.

`char tails` counts fixed-size strings with bytes after the NUL (snd_pan names); setting such a
string to its own value keeps them, setting a new string clears them.

## 6. Open items

- Unnamed bytes (1.02% of all struct bytes): the largest are GfxSurface +0x20 and
  +0x34..+0x3f, XSurface +0x54 and +0x58, the 4-byte tail of cbrush_t, GfxStaticModelDrawInst
  +0x28, CollisionPartition and cLeaf_s padding, the cLeafBrushNode_s union, XModel
  +0x98..+0x9f and +0xdc..+0xe7, XBoneInfo +0x29, Material +0xc and +0x6c (section 4 lists
  every struct). Most are alignment padding in the PC layouts; the rest need the ELF code that
  reads them.
- GfxWorld vertex layer data has a per-group stride (28 to 56 bytes); it stays an opaque blob
  here (docs/extract.md 3.1 decodes its first 28 bytes per vertex).
- XAnim frame data, shader programs, pixels and sound data are opaque by design.
