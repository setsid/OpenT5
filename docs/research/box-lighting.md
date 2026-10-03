# P6 lighting: cod2rad lightmaps, light grid, reflection probes and the compass for a converted map

Status: research and prototype. Every image and table cod2rad and the PC linker write for a
map has been compared with the PS3 build of the same map (mp_nuked, and the light grid on four
more maps); the transform PC to PS3 is byte-exact for the lightmaps, reflection probe cubes,
outdoor image, GfxImage headers, sun light and light grid (apart from one constant colour). A
prototype applied it to the box map (`mp_opent5box`) converted into mp_nuked; the result
reparses exactly and is consumed exactly by the game's own loader (emulated, local CPU only).
Nothing was run on RPCS3 or hardware. Prototype code was kept outside the repository
(`p6l/`), not in `src/`.

Conventions as in box-map.md: "PC zone" is the inflated PC stream, "PS3 zone" the inflated
PS3 XFile stream (36-byte prefix included); offsets are into those streams. "PC nuked" is
Steam `zone/Common/mp_nuked.ff` walked from its com_map (`pc.walk_range`, com_map at
0x183bae2); "PS3 nuked" is the disc `mp_nuked.ff` (scratch `zones/mp_nuked.zone`); "box" is
`/mnt/c/o5/p6/mp_opent5box.ff`. GfxWorld header offsets are PS3 offsets unless "PC +x".

Contents

1. Summary
2. Lightmaps
3. Light grid
4. Reflection probes
5. Sun, outdoor image and the rest of what cod2rad writes
6. The compass (minimap)
7. Why the first device run looked as it did
8. Prototype: the box with its own lighting
9. Implementation plan for `src/opent5/convert/`
10. Open items

## 1. Summary

| Item | PC (cod2rad + linker_pc) | PS3 | Transform | Proof |
|---|---|---|---|---|
| `*lightmap0_primary` | DXT1, 1 level, loadDef in the zone | GCM DXT1 (0x86), deferred | blocks unchanged | PC nuked = PS3 nuked, 2 097 152 bytes identical |
| `*lightmap0_secondary` | D3DFMT_R5G6B5 (23), linear, LE | GCM R5G6B5 (0x84), swizzled, BE | swap each u16, Morton swizzle | 4 194 304 bytes identical |
| `*lightmap0_secondaryb` | D3DFMT_G16R16 (34) | GCM Y16_X16 (0x95), remap 0xaae4 | swap each u16 (order kept), swizzle | 4 194 304 bytes identical |
| `*reflection_probeN` | DXT1 cube, faces packed | GCM DXT1 cube, each face padded to 128 | pad faces | probe0 768 and probe1 262 656 bytes identical |
| `$outdoor` | D3DFMT_L8 (50) | GCM B8 (0x81), remap 0x1a9ff | swizzle | 262 144 bytes identical |
| GfxImage header | 0x34 + loadDef | 0x70 | built from the PC fields (2.4) | all six 0x70 headers byte-identical |
| Light grid | rows, entries, colours | same bytes | swap scalars (done today); last colour `15009d` x 56 -> `40059d` x 56 | 5 maps: colours identical except the last |
| Sun light (GfxLight 0x170) | LE | BE | swap every word but +0 | PC nuked = PS3 nuked |
| Compass | none from the tools | material `compass_map_mp_nuked` (code_post_gfx_mp), DXT23 512 streamed from images_low.pak entry 57 | two `minimap_corner` entities + a generated image | script read; Nuketown image matches a top-down render |

## 2. Lightmaps

### 2.1 PC storage

A PC GfxImage is 0x34 bytes (OpenAssetTools src/Common/Game/T5/T5_Assets.h `GfxImage`):
+0 texture.loadDef (reusable pointer), +4 mapType, +5 semantic, +6 category, +7
delayLoadPixels, +8 picmip[2], +0xa noPicmip, +0xb track, +0xc cardMemory[2], +0x14 width,
+0x16 height, +0x18 depth, +0x1a levelCount, +0x1b streaming, +0x1c baseSize, +0x20 pixels,
+0x24 loadedSize, +0x28 skippedMipLevels, +0x2c name, +0x30 hash. The loadDef (12 bytes,
TEMP) is levelCount u8, flags u8, pad, format u32 (FOURCC or D3DFORMAT), resourceSize u32,
then the pixels. The lightmaps hang inline under the GfxWorld (`lightmaps` at PC +0x168, three
image pointers per GfxLightmapArray), each with -2 (`feffffff`) and the loadDef after the name.

Box (PC zone, sha1 20e4cffb...):

| Image | Header at | loadDef | Size |
|---|---|---|---|
| `*lightmap0_primary` | 0x5953b `feffffff 03010201 ... 0004 0004 0100 ...` | 0x59582 `01 02 0000 'DXT1' 00000800` | 1024 x 1024, 524 288 bytes |
| `*lightmap0_secondary` | 0xd958e | 0xd95d7 `01 02 0000 17000000 00001000` | 512 x 1024, 1 048 576 |
| `*lightmap0_secondaryb` | 0x1d95e3 | 0x1d962d `01 02 0000 22000000 00001000` | 512 x 512, 1 048 576 |

PC nuked: headers at 0x1e00de0, 0x2000e33, 0x2400e88; loadDefs `...'DXT1' 00002000`,
`...17000000 00004000`, `...22000000 00004000`: 2048 x 2048, 1024 x 2048, 1024 x 1024. The
secondary is always half the primary's width and the same height, the secondaryB half by half.

### 2.2 PS3 storage

PS3 nuked GfxWorld header at 0xfcd0bc; lightmapCount (+0x178) = 1; the three GfxImages
(0x70, structs-content.md 7) at 0x102e36b, 0x102e3ee, 0x102e473, each `delayLoadPixels` = 1,
pixels in the deferred tail (PHYSICAL_RUNTIME): 0x32f0149 (0x200000), 0x34f0149 (0x400000),
0x38f0149 (0x400000).

```
0102e36b: 86010200 0001aae4 08000800 00010000 00000000 00000000   DXT1, 1 level, 2048 x 2048
          03010201 00200000 08000800 00010000 00000000 ffffffff   2D, semantic 1, cat 2, delay 1, size, w h d, pixels -1
          (0x30..0x67 zero) ffffffff ce2f698b                     name -1, hash
0102e3ee: 84010200 0001aae4 04000800 ...                          R5G6B5 1024 x 2048, size 0x400000
0102e473: 95010200 0000aae4 04000400 ...                          Y16_X16 1024 x 1024, remap 0xaae4
```

### 2.3 Transform, proven on mp_nuked

Scratch `p6l/t4.py`, `t20.py`:

- primary: PC DXT1 blocks = PS3 bytes, identical (2 097 152 bytes). DXT is not swizzled
  (textures.md 4).
- secondary: reverse each 2-byte texel (LE -> BE), then Morton swizzle (`texture.swizzle`):
  0 of 4 194 304 bytes differ. PC starts `064b e54a c73b ...`, PS3 (swizzled) `4b06 4ae5 4b05 ...`.
- secondaryB: reverse each u16, keep the two u16 in order (PC low word = D3D red = PS3 first
  u16), swizzle: 0 bytes differ. Swapping the 4-byte texel as a whole leaves 3 063 848 wrong.

### 2.4 GfxImage header PC -> PS3 (all images)

Built from the PC header and loadDef alone; byte-identical to the PS3 header for all six
mp_nuked images of this document (3 lightmaps, 2 probes, `$outdoor`; `t20.py`):

| PS3 off | Value |
|---|---|
| 0x00 | GCM format: DXT1 0x86, DXT3 0x87, DXT5 0x88, R5G6B5 0x84, G16R16 -> Y16_X16 0x95, L8 -> B8 0x81 |
| 0x01 | levels: loadDef levelCount, or the full chain when it is 0 (the probes: PC 0, PS3 3 for 4 x 4, 9 for 256) |
| 0x02, 0x03 | dimension 2, cubemap 1 when mapType is 5 |
| 0x04 | remap 0x0001aae4; Y16_X16 0x0000aae4; B8 0x0001a9ff |
| 0x08 | width, height, depth 1 (u16 BE); location, pad, pitch, offset 0 |
| 0x18 | PC mapType, semantic, category, then 1 (deferred) |
| 0x1c | stored size (faces padded to 128) |
| 0x20 | width, height, depth again |
| 0x2c, 0x68 | -1 (pixels follow, name follows) |
| 0x6c | the PC hash (+0x30) byte-swapped: `8b692fce` -> `ce2f698b` |

### 2.5 Layout of the three images (what the texels hold)

Read from the box (`t24.py`, `t26.py`) and PS3 nuked (`t25.py`); the meaning is INFERRED
(the shader was not read).

- Chart layout: the lightmap uv addresses the primary and the secondaryB directly
  (u x width, v x height). The secondary is two images of the secondaryB's size stacked
  vertically with the same chart layout; uv addresses each half at (u x w, v x h / 2). Evidence:
  box floor uv (0.00195..0.0645, 0.0039..0.0664) covers secondaryB rows 2..34 and secondary rows
  2..34 and 514..546; box secondary green is non-zero on rows 2..107 of each half and nowhere
  else (uv v max 0.2109 x 512 = 108). Green is identical in both halves of the box; in PS3 nuked
  the halves' green correlates 0.92, red 0.48, blue 0.22.
- primary (DXT1): red and blue 0; green 0 on 89% of nuked texels and 240+ on 9% (INFERRED sun
  visibility); all zero in the box (sealed).
- secondary (R5G6B5): in the box red = 8/31 and blue = 7/31 constant, green a ramp 63 -> 0 across
  each chart (floor row 10: `63 63 61 58 49 ... 11 9 14 14 13 15 14 5`). Not an irradiance
  (a light above the floor centre would peak in the middle): INFERRED a direction or basis term.
- secondaryB (Y16_X16): smooth, peaks under the light (`p6l/conv_floor_preview.png`): box floor
  channel 0 933..2045 (mean 1370), channel 1 606..1733 (mean 1175); ceiling 1228 / 1264; walls
  856 / 810. PS3 nuked mean 649 / 1654, 99th percentile 2620 / 9794. INFERRED: this is the
  intensity the renderer scales by.

### 2.6 What the surfaces and vertices carry

A surface's lightmap index is byte +0x44 of the PS3 GfxSurface (PC +0x34; then reflection probe
index, primary light index, flags, all bytes and copied). The vertex lightmap uv is the PC
GfxWorldVertex `lmapCoord` (+0x1c, two f32) written as two f32 BE at +12 of the vertex's layer
data run; docs/convert.md 4 proves that on PC/PS3 nuked (lightmap uv identical in all 176 344
vertices). So with the PC lightmaps converted, the converter keeps the PC uv and the PC indices
(today's `--lighting keep` path); no new uv is needed. Box surfaces: `00000001` (lightmap 0,
probe 0, primary light 0, flags 1).

## 3. Light grid

Layout: GfxLightGrid at PS3 +0x230 (PC +0x218; 0x38 bytes; loader 0x236e20): hasLightRegions,
sunPrimaryLightIndex, mins[3], maxs[3], rowAxis, colAxis, rowDataStart, rawRowData, entries
(GfxLightGridEntry: u16 colorsIndex, u8 primaryLightIndex, u8 needsTrace), colours
(GfxCompressedLightGridColors, 0xa8 = 56 x 3 bytes). The converter already swaps the header,
rowDataStart, entries and the row fields (world.py `light_grid_rows`); docs/convert.md 4
shows them identical on nuked.

Colours (`t6.py`, `t10.py`): PC and PS3 colour arrays are byte-identical except the last colour.

| Map | Colours | PC last | PS3 last |
|---|---|---|---|
| mp_nuked | 32 001 | `15009d` x 56 (PC 0x357599a) | `40059d` x 56 (PS3 0x1da2fb5) |
| mp_firingrange, mp_havoc, mp_villa, mp_cracked | 32 001 each | `15009d` x 56 | `40059d` x 56 |

The last colour is an extra, unreferenced one: nuked has 37 027 entries, highest colorsIndex
31 999, none uses 32 000; cod2rad allocates (count + 1) x 0xa8 (cod2rad.exe 0x43682e ..
0x436836, `imul eax, eax, 0xa8` after `add eax, 1`); the worldspawn key `"maxlightgridcolors"
"32000"` gives the 32 000. INFERRED: it is the colour used outside the grid, and the PS3 linker
writes its own constant. Encoding: 24 bits per sample; read as LE 24-bit words, bits 5, 11 and 23
are 0 in every nuked colour and bits 12..17 are uniform, so INFERRED four 6-bit fields; the two
defaults differ only by swapping the two low fields (PC 21, 0, 16, 39; PS3 0, 21, 16, 39).

The box: one colour (the PC default, PC 0x2d9aa7) and no entries; rowDataStart `ffff`, mins =
maxs = 0. cod2rad wrote no grid because it had no sample points: cod2rad takes them from
`<map>.grid` / `.grid_auto` files ("Light grid sample point file '%s' not found."; loader at
cod2rad.exe 0x434010, which checks size % 6 == 0 at 0x4340a7 .. 0x4340ba and counts size / 6
records, so INFERRED 6 bytes per point); the Launcher's "Make New Grid" / "Collect Dots"
controls make the `.grid` (strings in bin/Launcher.exe), and cod2map wrote empty `.grid_auto` /
`.grid_not` for the box.

Rule: copy the colours; replace a last colour equal to `15009d` x 56 by `40059d` x 56. For a
map with no grid the PS3 default is the only colour models get (INFERRED).

## 4. Reflection probes

GfxReflectionProbe (24 bytes on both): origin f32[3], image, probeVolumes, probeVolumeCount
(OpenAssetTools `GfxReflectionProbe`; PS3 handler xfile/handlers/gfxworld.py). After the swap
the PC nuked probe records equal the PS3 ones (`0000b4c3 00000043 000050c1 ffffffff ...` vs
`c3b40000 43000000 c1500000 ffffffff ...`). Images (`t5.py`): PC loadDef `00 04 0000 'DXT1'`,
levelCount 0 (full chain), flags 4, mapType 5; six faces each with its full chain, packed (PC
nuked probe1: 6 x 43 704 = 262 224 bytes, header 0x1dc0d31); PS3 pads each face to 128 (6 x
43 776 = 262 656, header 0x102e2dc). Same face order: the padded PC bytes equal the PS3 bytes
for probe0 (768) and probe1 (262 656).

Cells: GfxCell +0x30 count, +0x34 u8 probe indices; surfaces byte +0x45. Both copied.

Box: one probe, `*reflection_probe0`, the 4 x 4 black DXT1 dummy (all 144 bytes zero),
byte-identical to nuked's probe0. Real probes need a `reflection_probe` entity in the .map
(cod2map strings "reflection_probe", "Reflection probe at ... is in solid") and cube faces made
by the game (`r_reflectionProbeGenerate`, a string in the LinkerMod linker_pc.dll); cod2rad does
not render them (INFERRED from its strings: only "LUMP_REFLECTION_PROBES").

## 5. Sun, outdoor image and the rest

- Sun light (GfxLight 0x170, PS3 +0x100, PC +0xec): every 32-bit word swapped except +0 (type
  and flag bytes); PC nuked swapped = PS3 nuked, 0 words differ (`t21.py`). The box's sun light
  equals PC nuked's (same worldspawn sun keys), so keeping the stock one changed nothing for the
  box; a general map needs the swap.
- Primary lights: ComWorld (swapped today); GfxWorld sunPrimaryLightIndex +0x110 = 1,
  primaryLightCount +0x114 = 2 for the box (stock 24), shadowGeom and lightRegion per primary
  light (swapped today). The RUNTIME shadow arrays size from (count - sun - 1) = 0 for the box.
  No shadow map images exist in either zone.
- `$outdoor` (PC 0x2da34a, nuked PS3 0x1dbdec2): L8 -> B8, swizzle; identical on nuked (`t5.py`).
  Its mapping is the outdoorLookupMatrix at PS3 +0x2f4..+0x333; today the converter keeps the
  stock range 0x28c..0x338, so the box would get its own $outdoor (once converted) with
  Nuketown's matrix (box `3a800000` = 1/1024 vs stock `37c4c4cd` at +0x2f4; `t32.py`). The same
  stock range also keeps Nuketown's sun flare data (+0x294 hasValidData 1, +0x2a0.. sizes and
  dots) where the box has none. Only the pointer words (+0x28c/0x290 material memory, +0x298,
  +0x29c, +0x334) need to stay stock.
- Static model lighting: the box has none (smodel arrays are refused when they hold data).
- Sky: the box has no sky image (PC +0x2c is 0); the stock sky stays.

## 6. The compass (minimap)

- The script: `maps/mp/_compass.gsc` (common_mp) `setupMiniMap(material)` takes the two
  entities with targetname `minimap_corner` (`getentarray`, exactly two or it prints "There are
  not exactly two "minimap_corner" entities" and returns without `setMiniMap`), orients them by
  `getnorthyaw()` (north = (cos, sin, 0)), widens to `scr_requiredMapAspectRatio`, and calls
  `setMiniMap(material, nw.x, nw.y, se.x, se.y)`. The stock map script calls
  `setupMiniMap("compass_map_mp_nuked")`; docs/convert.md 3.6 keeps that line.
- Stock corners (entity string): `script_origin` `minimap_corner` at `2546 2906 -72` and
  `-2574 -2214 -72`. The box has none, hence the placeholder compass on the device.
- The material `compass_map_mp_nuked` is asset 164 of code_post_gfx_mp (always loaded),
  techset `2d` (asset 94), one texture `compass_map_mp_nuked`: GCM DXT23 (0x87) 512 x 512, 1
  level, streamed, mode 2, one part `00400001 0200 0200 01000039` = images_low.pak (slot 1)
  entry 57, 0x40000 bytes. Decoded (`p6l/compass_map_mp_nuked.png`): the Nuketown plan with
  alpha around it. `mapstable.csv` column 7 (`compass_overlay_map_nuked`) is the menu overlay,
  not the in-game compass.
- Orientation, checked: a top-down render of PS3 nuked's world with row 0 = north edge (x = nw.x,
  world +X up) and column 0 = west edge (y = nw.y, world +Y left) between the two corners lines
  up with the stock image (`p6l/nuked_topdown_vs_compass.png`: houses, cul-de-sac and the shape
  of the plot in the same places).
- The PC tools do not produce a compass: the mapper places the two corners in Radiant and makes
  the image (INFERRED; no compass step in cod2map, cod2rad or linker strings).

Proposal for OpenT5: add two `minimap_corner` script_origins at a square around the world bounds
(box: (576, 576) and (-576, -576)); render a top-down image of the converted world with the
same mapping (floor triangles filled, an outline, alpha 0 outside) at 512 x 512; encode DXT23,
1 level (262 144 bytes, exactly the existing part), and write it as images_low.pak entry 57 with
the existing streamed-image path (`edit.images.encode_parts` accepts it:
`StreamPart(cumulative=262144, mips=1, width=512, height=512, slot=1, entry=57)`, `t31.py`).
images_low.pak is shared, but entry 57 belongs to this one image (pak.md). The alternative, a
material and image of its own in the map zone, needs the `2d` techset in that zone or an
override of a code_post_gfx_mp asset (INFERRED behaviour): later.

## 7. Why the first device run looked as it did

demo-box.md reports a nearly black floor, a brownish ceiling and walls that look right. The
`flat` donors were chosen by sampling the secondary at (u x 1024, v x 2048) and ranking its
RGB luminance; per 2.5 the renderer reads that image at v x 1024 in each half and the intensity
is in the secondaryB. At the donors' uv (`t28.py`):

| Donor | Secondary scored (R, G, B 5:6:5) | Read by the renderer (top, bottom) | SecondaryB (raw u16) |
|---|---|---|---|
| walls (0.565063, 0.536682) | (9, 43, 6) | (6, 30, 9), (6, 20, 9) | 364, 781 |
| floor (0.57666, 0.104301) | (6, 35, 11) | (7, 30, 10), (8, 32, 6) | 165, 66 |
| ceiling (0.928223, 0.164551) | (5, 46, 11) | (10, 26, 5), (9, 46, 6) | 626, 4630 |

The floor donor has the lowest secondaryB (nuked mean 649 / 1654) and the ceiling an unbalanced
pair, which fits what was seen (INFERRED: the shader was not read). The viewmodel tint fits the
light grid: the converted box grid held only the PC default `15009d`, which the PS3 build of
every map replaces by `40059d` (section 3); the probes are not teal (nuked probe1 faces mean
RGB about 80..94, 76..101, 62..97; probe0 black).

## 8. Prototype: the box with its own lighting

`p6l/convert_lit.py` runs the product `convert_map(..., lighting="keep")` with two functions
replaced in-process (src/ untouched): `world.convert_gfx` is wrapped to (1) put the converted
PC lightmap images into the base GfxWorld's lightmap image nodes (header and pixels;
`p6l/pcimg.py`, the 2.4 rule), (2) fill the base probe nodes from the PC probes (count 2 -> 1,
origin swapped, image converted), (3) convert `$outdoor`, (4) take +0x294 and +0x2a0..+0x333 (sun
flare data, outdoorLookupMatrix) from the PC header, (5) swap the sun light, (6) replace the
grid default colour; `scripts.compat_entities` is wrapped to add the two minimap corners.

| Check | Result |
|---|---|
| Images written | primary DXT1 1024 x 1024 (524 288), secondary R5G6B5 512 x 1024 (1 048 576), secondaryB Y16_X16 512 x 512 (1 048 576), `*reflection_probe0` cube 768, `$outdoor` B8 512 x 512 (262 144) |
| Light grid | 1 colour `40059d` x 56, 0 entries |
| Reparse | exact, `write(parse)` identical, 529 assets, 53 166 offset / alias pointers, 0 unresolved |
| Header | PHYSICAL_RUNTIME 0x115d900 -> 0x99d700 (the 0x7c0200 smaller lightmaps and probe1), other blocks as the flat build |
| Game loader (`tools/convert_map.py oracle`, 16.7 s) | consumed 0x28faaf9 of 0x28faaf9; all blocks end at the header; 53 166 pointer conversions, same fields and values as the product parser, 0 outside their block |
| Output (scratch only) | `p6l/out/mp_nuked.ff` 23 832 000 bytes, sha1 f1d33b195b2f006172984ffa4aca12ee3fe40f89; content sha1 d49a292f... |
| Floor texels (decoded from the converted zone, `t23.py`, `conv_floor_preview.png`) | primary 0 (no sun); secondary R 66, B 57..66, G 0..255 ramp; secondaryB 933..2045 / 606..1733, brightest under the light. Walls 856 / 810, ceiling 1228 / 1264: the floor is the most lit surface, not black |
| Compass | corners (576, 576, 0), (-576, -576, 0) in the entity string; `p6l/box_compass.png` DXT23 512 x 512 = 262 144 bytes, accepted by `encode_parts` for entry 57 (pak not written) |

Not checked: anything after the loader (rendering). The `.ff` was not installed anywhere.

## 9. Implementation plan for `src/opent5/convert/` (ordered)

1. `formats/texture.py` or a new `convert/images.py`: `pc_image_to_ps3(node) -> (header 0x70,
   stored)` for DXT1/3/5, R5G6B5, G16R16, L8 (and A8R8G8B8 / X8R8G8B8 when met), 2D and cube,
   with the 2.4 header; refuse other formats and volumes with the format code and offset. Test:
   the six nuked images byte-identical (PC zone not a fixture: record the sha1 of each result).
2. `convert/world.py`: replace `STOCK_HEADER_RANGES` (0x16c..0x208, 0x28c..0x338) by pointer-only
   words; take probe count, lightmap count, sun flare data and the outdoorLookupMatrix from the
   PC header; convert `reflection_probes` (records, probe volumes swapped as f32, images),
   `lightmaps`, `outdoor_image` and the sun light (word swap, +0 kept) instead of keeping the
   stock nodes; light grid default colour (section 3). The draw images (+0x18c..) and sky stay
   stock while they are empty on PC.
3. `convert/splice.py` / `mapzone.py`: new image nodes are wholly converter-built; give them an
   origin (they hold only inline pointers) and keep the stock image node identities where the
   count matches, so alias pointers elsewhere to stock images do not dangle; check that no other
   asset refers to a replaced image by alias (none in nuked: lightmaps, probes and $outdoor are
   loaded inline under the GfxWorld).
4. `convert/lighting.py`: make `cod2rad` the default (`keep` uv and indices, converted images);
   keep `flat` only as a fallback, and fix its sampling (secondary halves at v x h / 2, rank by
   secondaryB) if it is kept.
5. `convert/scripts.py`: add the two `minimap_corner` entities (square around the bounds, or the
   PC map's own corners when present), and refuse more than two.
6. Compass image: a `convert/compass.py` that renders the converted world top-down (the
   `export/preview.py` rasteriser with the section 6 mapping), encodes DXT23 and writes it
   through `edit.images` pending pak edits for the base's compass image (images_low.pak for the
   disc maps; slot 0 for maps whose compass is in their own pak); `opent5 convert` writes the
   pak next to the `.ff` and the install notes say which file to back up.
7. `mapzone.py` report: list every converted image (name, format, size, sha1), the grid colour
   count and default, the probe count and the corners.
8. PC side (docs/convert.md): a `reflection_probe` entity in the map and a `.grid` file (or the
   Launcher's grid step) so cod2rad writes a real light grid; until then the box has one colour.

## 10. Open items

- What the three lightmaps mean to the shader (2.5) and the light grid colour encoding: read
  the RSX fragment program of `wc_l_sm_r0c0n0s0` (mp_nuked asset 370) or the PC D3D9 one.
- That the last grid colour is the out-of-grid colour, and that the viewmodel tint came from it:
  a device run of `p6l/out/mp_nuked.ff`.
- The `.grid` record format (6 bytes INFERRED) and whether `.grid_auto` can be filled from the
  spawn points.
- Probe cube generation with the LinkerMod linker (`r_reflectionProbeGenerate`) for a map with a
  `reflection_probe` entity.
- Whether a map zone may carry its own `compass_map_*` material that overrides code_post_gfx_mp's.
- A base lightmap count different from the PC one (the prototype refuses it): the converter
  must then build new GfxLightmapArray elements and image nodes.

## Sources

Zone bytes and commands above; scratch scripts `p6l/load.py`, `t1.py` .. `t34.py`,
`pcimg.py`, `compass.py`, `convert_lit.py`; docs/research/box-map.md, docs/convert.md,
textures.md, pak.md, structs-map.md (with its errata), structs-content.md 7; OpenAssetTools
src/Common/Game/T5/T5_Assets.h (GfxImage, GfxImageLoadDef, GfxLightGrid, GfxLightGridEntry,
GfxReflectionProbe, GfxLightmapArray, GfxWorldVertex, sunflare_t); t5mp.elf loaders 0x236e20
(light grid), 0x24b070 .. 0x24b168 (probes); cod2rad.exe (pei-i386) 0x434010, 0x4340a7,
0x43682e; strings of cod2map.exe, cod2rad.exe, linker_pc.exe/.dll and Launcher.exe in the PC
tools' `bin`.
