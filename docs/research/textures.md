# R3: PS3 textures as T5 stores them, and conversion to and from DDS / PNG

Status: findings below are from zone and .pak bytes, checked by decoding to PNG and looking at
the result. Prototype code: `src/opent5/formats/texture.py`, tests `tests/test_texture.py`.
Anything not confirmed is marked INFERRED with what would confirm it.

Conventions: "zone offset" is an offset into the inflated XFile stream (the `.zone` plaintext,
36-byte prefix included, see xfile.md). Disc folder = `PS3_GAME/USRDIR/english`, update folder =
`dev_hdd0/game/BLES01031/USRDIR/english`. Zones examined: mp_nuked, mp_firingrange,
zombie_theater, common_mp, code_post_gfx_mp, ui_mp, patch, patch_mp, patch_ui_mp.

The GfxImage struct as a whole belongs to R2b (structs-content.md, not yet written when this was
done); section 2 documents only the fields needed to find pixels. Where the two disagree,
R2b's ELF-backed layout wins.

## 1. Summary

| Question | Answer | Confidence |
|---|---|---|
| Where are pixels? | Three places, chosen per image by two bytes of the GfxImage: (a) inline in the zone straight after the image name, (b) deferred to the zone's last `blockSize[3]` bytes (PHYSICAL_RUNTIME), (c) streamed from .pak files, one pak entry per mip part | CONFIRMED (sizes match exactly; images decode) |
| Texture header | A 24-byte CellGcmTexture at GfxImage+0x00, big-endian | CONFIRMED |
| Formats in use | DXT45, DXT1, DXT23, G8B8, D8R8G8B8, B8, A8R8G8B8, R5G6B5, Y16_X16, X32_FLOAT (counts in section 3) | CONFIRMED |
| DXT byte order | Same as a PC DDS: little-endian 565 colours and indices, no swap | CONFIRMED (visual, and DDS copy is byte-identical) |
| DXT swizzle | None; plain row-major 4x4 blocks even though the LN flag is clear | CONFIRMED |
| Uncompressed layout | Swizzled (Morton, x bit first), texels big-endian (A8R8G8B8 = bytes A,R,G,B) | CONFIRMED |
| Volume layout | 3D Morton (x, y, z bits interleaved) | CONFIRMED on a 32^3 identity LUT |
| Mip chain | Levels packed with no per-level padding; each face padded to 128 bytes | CONFIRMED (size fields) |
| Cube maps | Six faces back to back, each with its mips, each 128-aligned | CONFIRMED; face order INFERRED |
| Linear (LN, pitch) textures | None in any examined zone (pitch = 0 everywhere) | — |
| Encoder needed? | Yes for PNG input; carry DXT blocks straight from DDS when given one | Recommendation |

## 2. Where pixels live

### 2.1 The GfxImage fields that matter (0x70 bytes)

Located by searching for the CellGcmTexture pattern and checking that the stored-size field
equals the size computed from format/width/height/mips (section 4). The name pointer at +0x68
is followed by the name string when it is -1.

| Off | Size | Field | Notes |
|---|---|---|---|
| 0x00 | 24 | CellGcmTexture | format u8, mipmap u8, dimension u8 (2 = 2D, 3 = 3D), cubemap u8, remap u32, width u16, height u16, depth u16, location u8, pad u8, pitch u32, offset u32 |
| 0x18 | 1 | mapType | 3 = 2D, 4 = 3D, 5 = cube (PC T5 MapType enum) |
| 0x19 | 1 | semantic | 2 colour, 5 normal, 8 specular, ... (PC TextureSemantic) |
| 0x1a | 1 | category | 3 = load-from-file in nearly all; 1 = auto-generated |
| 0x1b | 1 | delayLoadPixels | 1 = pixels deferred to PHYSICAL_RUNTIME, 0 = inline after name (2.3) |
| 0x1c | u32 BE | stored size | bytes of zone-held pixels; 0 for streamed images |
| 0x20 | 3 x u16 | width, height, depth | copy of the GCM size; 1,1,1 for streamed images (INFERRED: current loaded size, filled at run time) |
| 0x26 | u8 | 1 when streamed, 0 otherwise | |
| 0x27 | u8 | streaming mode | 0 none, 1 multi-part, 2 single part (2.4) |
| 0x28 | u32 LE | unknown | streamed only. Read little-endian it equals total/16 for DXT and total/4 for 32/16-bit formats; meaning unknown |
| 0x2c | u32 | pixels pointer | 0xffffffff when the zone holds pixels; 0 when streamed |
| 0x30 | u32 LE | total streamed bytes | streamed only, little-endian (sic): e.g. `00ab0200` = 0x2ab00 |
| 0x34 | 4 x 12 | part records | streamed only (2.4) |
| 0x64 | u8 | part count | 1..4; three pad bytes follow |
| 0x68 | u32 | name pointer | -1 = string follows the struct |
| 0x6c | u32 | hash | unknown function; not needed to find pixels |

Example, mp_nuked zone offset 0x5b80d, `~-gus_art_wall_vinylsiding_white_c` (streamed DXT1 512x512,
10 mips):

```
0005b80d: 860a 0200 0001 aae4 0200 0200 0001 0000   DXT1, 10 mips, 2D, remap 0x1aae4, 512x512x1
0005b81d: 0000 0000 0000 0000 0302 0301 0000 0000   2D, colour, file, delay=1, size 0
0005b82d: 0001 0001 0001 0101 b02a 0000 0000 0000   1,1,1, streamed=1 mode=1, +0x28, ptr 0
0005b83d: 00ab 0200 | 0000b007 0040 0040 0100262e   total 0x2ab00 LE | part 0
0005b84d: 0002b008 0080 0080 00000002 | 000ab009    part 1 | part 2 ...
0005b85d: 0100 0100 00000001 | 002ab00a 0200 0200   ... part 2 | part 3 ...
0005b86d: 00000000 | 04 000000 | ffffffff 520799c2  ... part 3 | count 4 | name -1 | hash
```

### 2.2 Inline pixels (delayLoadPixels = 0)

The stored-size bytes follow the NUL of the name. Example: common_mp 0x4b6197c,
`weapon_reticle_arrows01`, D8R8G8B8 32x32 6 mips, size 0x1580; pixel bytes start at 0x4b61a04
with `ff00 0000 ff00 0000 ...` (opaque black texels). 308 of 474 inline images in common_mp pass an
automatic check that mip 1 equals a 2x2 box-filter of mip 0 when read from that position; the rest
are 1-mip or mostly flat/transparent (for example `hud_explosives`). Used by common_mp, ui_mp,
code_post_gfx_mp (all with blockSize[3] = 0).

### 2.3 Deferred pixels (delayLoadPixels = 1): the PHYSICAL_RUNTIME block

When `+0x1b` is 1, nothing follows the name (mp_nuked 0x688d2 `~-gcardboard_box_decal_c`: the
name is followed directly by a material constant `b4de4d82 "dynamicFolia"`). Instead every such
image's bytes sit at the end of the zone, in the order the GfxImage structs appear in the stream,
each `size` bytes (a multiple of 128). The sum of their sizes equals XFile `blockSize[3]`
(PHYSICAL_RUNTIME) exactly in every zone that has deferred images:

| Zone | deferred images | sum of +0x1c sizes | blockSize[3] |
|---|---|---|---|
| mp_nuked | 101 | 0x115d900 | 0x0115d900 |
| mp_firingrange | 69 | 0x864980 | 0x00864980 |
| zombie_theater | 196 | 0x1987100 | 0x01987100 |
| patch | 4 | 0x4880 | 0x00004880 |
| patch_mp | 17 | 0x72580 | 0x00072580 |
| patch_ui_mp | 82 | 0x650000 | 0x00650000 |

So the last `blockSize[3]` bytes of the stream are the PHYSICAL_RUNTIME block, and it holds only
image pixels. The last image in mp_nuked, `c_gen_eye_rim_mask_c` (DXT1 64x64, 7 mips, 0xab8
bytes padded to 0xb00), occupies 0x41a8049..0x41a8b01 followed by 0x48 zero bytes to the end of
the file (0x41a8b49). Two of mp_nuked's 101 deferred images have a name pointer that is not -1
(zone 0x27268ef and 0x27269fb, DXT45 128x128 1 mip); a name-string search misses them, which
shifts every earlier image by 0x8000, so locate images by the struct, not by the name.

INFERRED: that the loader reads the block in this order (consistent with all 6 zones; would be
confirmed by the XFile loader in the ELF, R1's section 3).

### 2.4 Streamed pixels: .pak files and part records

When `+0x27` is non-zero, each of the `+0x64` part records (12 bytes, big-endian) names one pak
entry:

| Off | Field |
|---|---|
| 0 | u32: bits 31..8 = cumulative byte size of the image up to and including this part, divided by 16; bits 7..0 = mip count of the image at this resolution |
| 4 | u16 width, u16 height of the largest level in this part |
| 8 | u32: bits 31..24 = pak slot, bits 23..0 = entry index in that pak |

A part's own byte size is its cumulative size minus the previous part's. Parts go smallest
first. In mode 1 the first part is the whole mip tail from 64x64 (or 32x32) down, and each later
part is exactly one larger level: for the example above, part 0 (64x64, 7 levels, 0xb00 bytes)
is images_low.pak entry 0x262e, then 128x128 (0x2000) entry 2, 256x256 (0x8000) entry 1,
512x512 (0x20000) entry 0 of mp_nuked.pak. In mode 2 there is one part holding the whole image
(for example `scope_overlay_nsp3a_512`, A8R8G8B8 512x512 1 mip, record `01000001 0200 0200
010003df` at common_mp 0x2a6df70).

Pak slots, established by checking that every part's size, rounded up to 0x800, equals the
entry's length in the named pak (100% match for every part in every zone examined):

| Slot | File | Parts matched |
|---|---|---|
| 0 | the level's own pak (`mp_nuked.pak`, ...) | mp_nuked 2053/2053 (= every entry), mp_firingrange 2627/2627, zombie_theater 2974/2974 |
| 1 | `images_low.pak` (mip tails, always resident) | mp_nuked 908 (= one per streamed image), common_mp 669, code_post_gfx_mp 750 |
| 3 | `common.pak` | common_mp 1943, mp_nuked 12, patch_mp 12 |
| 4 | `ui_mp.pak` | mp_nuked 514, mp_firingrange 500 |
| 9 | `img_patch.pak` (update folder) | patch_mp 25/25 |

Slot 2 is not used by any examined zone (INFERRED: frontend.pak or another always-loaded pak).
The slot numbers do not equal the pak header field at 0x08 (below). The mapping from slot to
file is INFERRED fixed; it would be confirmed in the ELF near the strings `images_low.pak`
(0x91aa98), `common.pak` (0x91aae8), `img_patch` (0x8e4ed0) and `%s/%s/%s.pak` (0x91a9f8, the
DLC path with `.edat`).

Note: there is no common_mp.pak or ui pak for every zone; common_mp streams from common.pak and
images_low.pak.

### 2.5 The .pak format (`pak2`)

Read from mp_nuked, mp_firingrange, common, frontend, ui_mp, images_low, img_patch and
snd.all (sound packs use the same container). All big-endian:

| Off | Field | mp_nuked | common | images_low | img_patch |
|---|---|---|---|---|---|
| 0x00 | magic `pak2` | | | | |
| 0x04 | build time (unix) | 0x4ca3e2ee (Sep 2010) | 0x4ca3e97a | 0x4ca3e97a | 0x4e2e1cd6 (Jul 2011) |
| 0x08 | unknown | 1 | 7 | 0 | 0x11 |
| 0x0c | entry count | 0x805 | 0x89e | 0x2fbc | 0x100 |
| 0x10 | sector size | 0x800 | 0x800 | 0x800 | 0x800 |
| 0x14 | header bytes (= starts[0] x 0x800) | 0x2800 | 0x2800 | 0xc000 | 0x800 |
| 0x18 | 0 | | | | |
| 0x1c | u32[count] start sector of each entry | 5, 0x45, 0x55, ... | 5, 0xd, ... | 0x18, ... | 0xd90, 0xf90, ... |

```
mp_nuked.pak 00000000: 7061 6b32 4ca3 e2ee 0000 0001 0000 0805  pak2L...........
             00000010: 0000 0800 0000 2800 0000 0000 0000 0005  ......(.........
             00000020: 0000 0045 0000 0055 0000 0059 0000 00d9
```

The table is zero-padded to the header size; data follow, sector-aligned, with no per-entry
header (mp_nuked.pak entry 0 begins `fbde 34a5 55a0 0000`, a DXT1 block). Sizes are not
stored: the gap to the next start is the sector-rounded size in ascending tables, but
img_patch.pak's table is not ascending (the update appends; entry 0xff starts at 0xd8d, below
entry 0's 0xd90), so the GfxImage part record is the authority for size. The last entry runs to
end of file (mp_nuked: 83986 x 0x800 + 0x800 = file size 0xa409800).

## 3. Formats found

CellGcmTexture format codes (libgcm `CELL_GCM_TEXTURE_*`; values as in RPCS3,
rpcs3/Emu/RSX/gcm_enums.h): flags LN = 0x20 (linear), UN = 0x40 (unnormalised) OR-ed in.

Counts of GfxImage structs over the nine zones (duplicated images in different zones counted
per zone):

| Code | Format | 2D | cube | 3D | Where seen |
|---|---|---|---|---|---|
| 0x88 | COMPRESSED_DXT45 | 3482 | | | everywhere; colour+alpha, specular, and 1072 of the 1073 normal maps (semantic 5; the other is A8R8G8B8) |
| 0x86 | COMPRESSED_DXT1 | 1716 | 9 | | everywhere; skyboxes are DXT1 cubes |
| 0x87 | COMPRESSED_DXT23 | 632 | | | fx and ui |
| 0x8b | G8B8 | 517 | | | 489 are emblems (code_post_gfx_mp, streamed); remap 0x1aafe |
| 0x9e | D8R8G8B8 | 54 | 3 | | fx sprites, reticles, `day_ft` reflection cube |
| 0x81 | B8 | 32 | | | light masks, `$outdoor`; remap 0x1a9ff |
| 0x85 | A8R8G8B8 | 27 | | 7 | ui; the 3D ones are 32x32x32 colour-grading LUTs |
| 0x84 | R5G6B5 | 4 | | | `*lightmap0_secondary` |
| 0x95 | Y16_X16 | 4 | | | `*lightmap0_secondaryb` |
| 0x9c | X32_FLOAT | 1 | | | code_post_gfx_mp |

No format had the LN flag set and every pitch field is 0. No UN flag was seen.

Remap words seen: 0x0001aae4 (identity, order XXXY), 0x0001a9ff (B8: R=G=B=B, A=1),
0x0001aafe (G8B8: A from G, R=G=B from B). `apply_remap` implements bits [15:8] as the four
output ops (B,G,R,A: 0 zero, 1 one, 2 remap) and [7:0] as the four sources (B,G,R,A: 0=A, 1=R,
2=G, 3=B); this reproduces the B8 images correctly. G8B8 channel naming (first byte = G) is
INFERRED from `black_box_faded`, whose first byte is a faded-box alpha and second byte constant
255.

Normal maps (semantic 5): DXT45 with X in alpha and Y in green (red and blue repeat Y). From
`cardboard_box_n` (mp_nuked.pak entry 0x15, 256x256): reconstructing Z = sqrt(1 - X^2 - Y^2)
from (A, G) gives a clean tangent-space map with edges in both directions.

## 4. Layout rules

Confirmed against the GfxImage size field for every non-streamed image in the nine zones (the
scanner accepts a struct only when the size field equals this computation, and the accepted
deferred images sum to blockSize[3] exactly).

- Level size: DXT = ceil(w/4) x ceil(h/4) x (8 or 16); uncompressed = w x h x bytes-per-texel.
- Mip levels follow each other with no padding; the whole face is padded to 128 bytes.
  Examples: DXT1 128x128 8 levels = 10936 -> 0x2b00; DXT23 16x16 5 levels = 368 -> 0x180
  (per-level padding would give 0x280).
- Cube: six faces, each as above. DXT1 512 1-level cube = 0xc0000; D8R8G8B8 64 cube = 0x18000.
  Face order +X, -X, +Y, -Y, +Z, -Z is INFERRED (the libgcm/D3D order); the decoded
  `skybox_mp_cosmodrome_ft` shows two faces with a vertical horizon, two with a horizontal one,
  one with the sun, one cloud-only, consistent with a Z-up engine.
- Volume: depth slices interleaved by 3D Morton order, x bit first, then y, then z. The
  code_post_gfx_mp `identity` LUT (A8R8G8B8 32x32x32, inline at 0x15ce71, size 0x20000)
  decodes with zero error to R = x, G = y, B = z under 3D Morton; slice-by-slice 2D swizzle and
  linear both give errors over 200. Mipped volumes: none seen (INFERRED).
- Swizzle (uncompressed, LN clear): texel (x, y) is at the index formed by interleaving the
  bits of x and y, x first, while both have bits; the larger dimension's remaining bits follow.
  Confirmed on square (`fxt_light_glow`, `$outdoor`, `led_number_4x4_radiant`) and non-square
  (`progress_bar_frame_sel` 128x32) images.
- DXT blocks: not swizzled, not byte-swapped. They are exactly PC/DDS blocks.

## 5. Images decoded and inspected

All written to the scratch directory as PNG and viewed. Each is a recognisable image.

| Image | Zone / source | Format | Result |
|---|---|---|---|
| `~-gus_art_wall_vinylsiding_white_c` | mp_nuked.pak entry 0 (512) and images_low entry 0x262e (64-tail) | DXT1 | white clapboard siding |
| `~-gpent_dec_signs_assrtd_c` | mp_nuked deferred | DXT1 512 | sign sheet: "RESTROOM", "FALLOUT SHELTER", "West Corridor", ... |
| `~-gus_art_streetsigns_c` | mp_nuked deferred | DXT1 128x64 | "Trinity Ave.", "Latchkey Rd." |
| `~-glights_hang_lamp_on_c` | mp_nuked deferred | DXT45 512 | lamp atlas: bronze trim, hooks, glass shades |
| `~~-gus_art_wall_vinylsiding_s...` | mp_nuked.pak entry 6 | DXT45 512 | siding specular |
| `cardboard_box_n` | mp_nuked.pak entry 0x15 | DXT45 256 normal map | box flap edges in X and Y |
| `scope_overlay_nsp3a_512` | images_low.pak entry 0x3df (slot 1, mode 2) | A8R8G8B8 512, swizzled | round scope vignette with reticle |
| `led_number_4x4_radiant` | mp_nuked deferred | D8R8G8B8 128 | red LED digits "847.01" |
| `fxt_light_glow` | common_mp inline | D8R8G8B8 128 | soft round glow |
| `progress_bar_frame_sel` | common_mp inline | A8R8G8B8 128x32 | green-framed bar |
| `$outdoor` | mp_nuked deferred | B8 512 | top-down outdoor mask of the map |
| `skybox_mp_cosmodrome_ft` | mp_nuked deferred, data at 0x40df2c9 | DXT1 512 cube | six coherent sky faces |
| `day_ft` | mp_nuked deferred | D8R8G8B8 64 cube | six sky/cloud faces |
| `loadscreen_mp_discovery2` | img_patch.pak entry 110 (slot 9) | DXT1 1024 | Discovery load screen |
| `identity` | code_post_gfx_mp inline | A8R8G8B8 32^3 volume | exact identity LUT |

## 6. Converting back: PNG / DDS to PS3

What a writer must produce for one image: the 24-byte CellGcmTexture, the GfxImage fields of
section 2.1, and pixel bytes in one of the three places.

- Simplest target: inline or deferred, not streamed. Inline (delay 0) needs no pak edits and no
  block-size bookkeeping beyond the zone's own stream; deferred needs blockSize[3] updated.
  Streamed images need new pak entries; pak headers carry no signature, but whether the game
  validates pak contents is unknown (INFERRED none).
- DXT: needed for any PNG input, because 90% of images (5839 of 6478) are DXT and memory budgets assume it.
  Recommendation: accept DDS (DXT1/3/5) and copy the blocks unchanged (the stored layout is
  byte-identical to DDS; `read_dds(...).to_stored()` round-trips exactly), and keep the pure-numpy
  encoder in `texture.py` for PNG input. That encoder (principal-axis range fit, DXT5 alpha
  min/max) re-encodes game textures at 38-45 dB PSNR in about 0.2 s for 512x512 with mips; good
  enough for a prototype, below a dedicated tool such as NVTT or Compressonator, so a user who
  cares about quality should supply a DDS.
- Normal maps: write DXT45 with X in alpha and Y in G (copy Y to R and B).
- Mips: box filter down to 1x1 (the game images all carry a full chain except single-level
  ones such as skyboxes and load screens); then pad the face to 128.
- Swizzle: only for uncompressed formats (Morton as above). DXT is never swizzled.
- Cube maps: six faces in the order above (face order INFERRED); volume: 3D Morton.

## 7. Open points

- Meaning of GfxImage +0x28 (little-endian, size/16 or size/4) and +0x6c (hash function).
- Slot-to-pak table in the ELF; slot 2.
- Cube face order: confirm by rendering or from the ELF's cube upload path.
- Whether DLC zones use slot numbers above 9 for their own paks (DLC paks are not yet extracted).
