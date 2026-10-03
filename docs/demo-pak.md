# mp_nuked streamed-texture demo (d_pak)

One texture whose pixels stream from `mp_nuked.pak` replaced with a test pattern, made with the
command line, verified offline. Format and evidence: docs/research/pak.md. Outputs are under
`~/opent5/out/demo/` (git-ignored; game data never enters the repository).

## What changed

The image `~-gmp_nuked_townsign_c` (DXT1, 512 x 512, 10 mips), the colour map of the material
`mc/mtl_mp_nuked_townsign` on the static model `mp_nuked_townsign`: the red "Welcome to
NUKETOWN Population" board, and on the same texture sheet the fallout-shelter panels, the
radiation symbols and the grey concrete trim of the sign's base. It is streamed in four parts:

| Part | Levels | Pak entry | Written |
|---|---|---|---|
| 0 | 64 x 64 down to 1 x 1 | images_low.pak entry 9845 | no (shared pak; see below) |
| 1 | 128 x 128 | mp_nuked.pak entry 704 | yes |
| 2 | 256 x 256 | mp_nuked.pak entry 703 | yes |
| 3 | 512 x 512 | mp_nuked.pak entry 702 | yes |

The new pixels (`townsign_test_pattern.png`, same size and format): four yellow bands with
"OPENT5 PAK" in black capitals over a magenta and black checkerboard of 32-pixel squares.
Mips are rebuilt with a box filter and encoded to DXT1.

    opent5 replace mp_nuked.ff 'image:~-gmp_nuked_townsign_c' townsign_test_pattern.png \
        -o out/demo/d_pak/mp_nuked.ff

## Files

| File | sha1 | Bytes |
|---|---|---|
| disc `english/mp_nuked.ff` (source) | `6d5a4a0e5c031c0d8fcd555413730b014e9c0c5d` | 36 349 760 |
| disc `english/mp_nuked.pak` (source) | `5930c6c683d9339d0357a7266a8d37819b95e6cf` | 172 005 376 |
| `out/demo/d_pak/mp_nuked.ff` | `6d5a4a0e5c031c0d8fcd555413730b014e9c0c5d` (= disc) | 36 349 760 |
| `out/demo/d_pak/mp_nuked.pak` | `cb4ac63f73e768a8a3b2031b5d4b89525217ee48` | 172 005 376 |
| `out/demo/d_pak/townsign_test_pattern.png` | `b71978fa09dd6bc5403fb4129ae8ee37d04e1abb` | 9 362 |

Also in the folder: `replace.json` (the command's report), `townsign_original.png` and
`townsign_extracted.png` (the image extracted before and after).

The zone does not change: a same-size streamed image is described in the zone only by its
part records, which stay the same. So the `.ff` written is byte-identical to the disc file and
its console signature still matches; only the `.pak` carries the edit.

Optional variant, `out/demo/d_pak_shared/` (made with the API, `allow_shared=True`): the same
`mp_nuked.ff` and `mp_nuked.pak`, plus `images_low.pak` (sha1
`49a69567eea7f06567ef37e8bb438bd3eef7b09e`, 145 166 336 bytes; disc sha1
`49d0a9618a0ca142e603db6b07a2e285c7deefde`) with entry 9845 (the 64 x 64 tail) replaced too, so
the sign shows the pattern at every distance. images_low.pak is shared by every zone; entry
9845 belongs to this one image and, of the nine zones examined, only mp_nuked uses it.

## Offline verification

From `replace.json` and an independent check (a script comparing every entry span of the
written pak with the disc pak and decoding the three new entries):

- Zone: reparsed exactly; 529 of 529 assets identical; file byte-identical to the source.
- Pak: same size and header; 2 050 of 2 053 entries byte-identical over their whole spans;
  entries 702, 703, 704 hold the new parts, padding after them zero. Nothing outside those
  three spans differs.
- Pixels: entry 702 decodes to exactly the pattern (512 x 512, no error, the colours are
  aligned to DXT blocks) and entry 703 to exactly its 256 x 256 mip; entry 704 (128 x 128)
  matches the box-filtered mip at 19.8 dB PSNR, as expected when DXT1 encodes blended text.
- The image extracted from the output with `opent5 extract out/demo/d_pak/mp_nuked.ff WORK
  --type image --name '*townsign_c'` (the extractor looks for `mp_nuked.pak` beside the
  `.ff` first) is `townsign_extracted.png`, byte-identical to the input PNG, and shows the
  pattern when opened.
- With `allow_shared=True` (d_pak_shared) the same checks pass for both paks: 12 219 of
  12 220 images_low.pak entries byte-identical, entry 9845 the new tail.

## Installing in RPCS3

Back up first: copy `PS3_GAME/USRDIR/english/mp_nuked.pak` of the game somewhere safe (its
sha1 is in the table above; keep `mp_nuked.ff` too if a demo from docs/demo-mp_nuked.md is
installed). Then:

1. Copy `out/demo/d_pak/mp_nuked.pak` over `PS3_GAME/USRDIR/english/mp_nuked.pak`.
2. Copy `out/demo/d_pak/mp_nuked.ff` over `PS3_GAME/USRDIR/english/mp_nuked.ff`. It is the
   retail file, so this step only matters if another demo's `.ff` is installed; the game
   reads `<zone>.pak` from the same folder as `<zone>.ff`, so both must be there and must be
   a pair.
3. For the optional variant, also back up `PS3_GAME/USRDIR/english/images_low.pak` and copy
   `out/demo/d_pak_shared/images_low.pak` over it. This file is used by every map, so put the
   retail one back afterwards.

Because the `.ff` keeps its signature, no signature-patched client is needed for this demo,
unless the game checks pak contents in a way not found in the executable (pak.md: no
checksum, no magic check). Put the retail files back afterwards.

## What to look for

Load Nuketown (any mode, e.g. a private match or Combat Training). The "Welcome to NUKETOWN
Population" sign stands in the middle of the map at about x -235, y 522, just off the street
near the end with the moving truck (about 8 m from the spot where the green Tiara car is
parked on the road, x -60, y 804), with its digit counter in front of it (x -209, y 541). From
the spawns on that side of the map (about x -750 to -1000, y 500) it is 13 to 20 m away.

With the demo installed, the board shows yellow bands with black "OPENT5 PAK" lettering over a
magenta and black checkerboard instead of the red sign with "Welcome to NUKETOWN Population";
any other part of the sign model mapped to this texture sheet (the fallout-shelter panels,
the trim) shows the same pattern. The digit counter is a separate model and keeps its own look.

At a long distance (the 64 x 64 mip and smaller) the sign shows the original red board again
with d_pak, because the mip tail lives in images_low.pak and was left as it was; with the
optional images_low.pak it stays patterned at every distance. Walking up to it, the pattern
should appear as the high mips stream in (a second or so after loading).

## What failure looks like

- The sign is red and unchanged up close, even after waiting: the game is not reading this
  `mp_nuked.pak` (check its sha1 in the game folder), or it reads the pak from a folder other
  than the `.ff`'s (pak.md 5: INFERRED same folder). Check that no other `mp_nuked.pak`
  exists in the update folder (`dev_hdd0/game/BLES01031/USRDIR/english`).
- The sign shows the pattern only when far away and red up close: the opposite of the
  expected mip split, meaning the part-to-entry mapping is wrong (offline it is not).
- Garbage blocks, wrong colours or a different picture on the sign: an entry offset or
  size is wrong. Offline, all three entries decode to the pattern and nothing else differs.
- Other textures in the map wrong or missing: another entry moved. Offline, 2 050 entries are
  byte-identical at the same offsets and the table is unchanged.
- The game refuses the map or crashes while loading: it checks the pak in a way not found in
  the executable; put the retail files back and report what is shown.

# j_pak_resize: a streamed texture at twice its size

One streamed texture of mp_nuked given twice its width and height, with the command line
(docs/research/pak.md 9.1). Outputs are in `~/opent5/out/demo/j_pak_resize/`.

## What changed

The image `~-gmp_nuked_manneq_head_male_01_c`, the face of the male mannequin head
`p_phys_nuked_manneq_head_male_01` (material `mc/mtl_mp_nuked_manneq_head_male_01`): DXT1,
128 x 256 with 9 mips before, 256 x 512 with 10 mips after. The new pixels
(`manneq_head_256x512.png`) are three yellow bands reading "OPENT5", "256x512" and "2X" in
black over a magenta and black checkerboard of 16-pixel squares, with a 1-pixel white grid
every 8 pixels (detail the old size could not hold).

    opent5 replace mp_nuked.ff 'image:~-gmp_nuked_manneq_head_male_01_c' \
        manneq_head_256x512.png -o out/demo/j_pak_resize/mp_nuked.ff --resize

| Part | Levels | Before | After |
|---|---|---|---|
| 0 | 32 x 64 down to 1 x 1 | images_low.pak entry 9900 | the same, not written (shared pak) |
| 1 | 64 x 128 | mp_nuked.pak entry 1282 | entry 1282, new pixels |
| 2 | 128 x 256 | mp_nuked.pak entry 1281 | entry 1281, new pixels |
| 3 | 256 x 512 | (none) | mp_nuked.pak entry 2053, added after the last entry |

In the zone, 15 bytes of the image's GfxImage header change (mips, width, height, +0x28,
+0x30, the fourth part record and the part count); nothing else. So the `.ff` is no longer
the retail file and its console signature no longer matches: this demo needs the
signature-patched client.

## Files

| File | sha1 | Bytes |
|---|---|---|
| disc `english/mp_nuked.ff` (source) | `6d5a4a0e5c031c0d8fcd555413730b014e9c0c5d` | 36 349 760 |
| disc `english/mp_nuked.pak` (source) | `5930c6c683d9339d0357a7266a8d37819b95e6cf` | 172 005 376 |
| `out/demo/j_pak_resize/mp_nuked.ff` | `4538f47b6e3ba49cfc0e5339f969ebd7d55394da` | 36 349 760 |
| `out/demo/j_pak_resize/mp_nuked.pak` | `cd2d037bc5a57af2b4dbe073c1249f83b7084296` | 172 070 912 |
| `out/demo/j_pak_resize/manneq_head_256x512.png` | `9feac09f67ccf4ee59b3e7d34666c98f842d8ca8` | 8 405 |

Also in the folder: `replace.json` and `replace.txt` (the command's report),
`manneq_head_original.png` (the image before, 128 x 256) and `manneq_head_extracted.png`
(the image extracted from the output, 256 x 512). Running the command again gives the same
two sha1s.

## Offline verification

- Zone: the 68 848 457 bytes of content differ from the disc zone in 15 bytes, all inside
  the image's header (zone 0x9dbc84..0x9dbce7); it re-parses exactly; 528 of 529 assets are
  byte-identical and the one that loads the image reads back with the new header.
- Loader: the game's XFile loader, run in the local PowerPC interpreter
  (`tools/convert_map.py oracle out/demo/j_pak_resize/mp_nuked.ff`), consumes the content
  exactly, ends every block at the header's size and converts the same 109 495 pointers to
  the same values as the product parser.
- Pak: 2 054 entries (one more), header still 0x2800 bytes, no entry moved; 2 051 entries
  byte-identical over their whole spans; 1281, 1282 and 2053 hold the new parts with zero
  padding.
- Pixels: decoded from the written files, the image is 256 x 512 and equal to the DXT1
  encoding of the PNG (level 0, every pixel). `opent5 extract out/demo/j_pak_resize/mp_nuked.ff
  WORK --type image --name '*manneq_head_male_01_c'` writes `manneq_head_extracted.png`
  again (same sha1), and it shows the pattern.

## Installing in RPCS3

Back up `PS3_GAME/USRDIR/english/mp_nuked.ff` and `mp_nuked.pak` first (sha1s above), and
use the signature-patched client.

1. Copy `out/demo/j_pak_resize/mp_nuked.pak` over `PS3_GAME/USRDIR/english/mp_nuked.pak`.
2. Copy `out/demo/j_pak_resize/mp_nuked.ff` over `PS3_GAME/USRDIR/english/mp_nuked.ff`. The
   two are a pair: the zone's new part record names entry 2053, which only this pak has.

Put both retail files back afterwards.

## What to look for

Load Nuketown. The mannequins stand in and around both houses. On the male mannequins whose
head is `p_phys_nuked_manneq_head_male_01`, the face shows yellow bands with black "OPENT5", "256x512" and "2X" over the magenta
checkerboard. Up close the 1-pixel white grid lines should be sharp: that is the 256 x 512
level, which only exists in the new entry 2053. The other mannequin heads (male_02, the
female ones) keep their own faces. From far away (the 32 x 64 mip and smaller, in
images_low.pak) the head shows the old face again, as in d_pak.

## What failure looks like

- The map does not load or the game stops while loading: the signature check is not patched,
  or the loader rejects the new header; put the retail files back and note the message.
- The heads show the pattern but blurred (no sharp 1-pixel grid even up close): the game
  streams the 128 x 256 part and never the new 256 x 512 one; the fourth record is read but
  entry 2053 is not, or the streamer caps the size it allocates for this image.
- Garbage blocks on the head, or a pattern squashed into one half: the size in the header
  and the part records disagree in what the game uses (offline they agree).
- Other textures wrong: another entry moved (offline none did).

## Result in RPCS3 for j_pak_resize (2026-10-03)

Local Team Deathmatch on Nuketown with the signature-patched client, `j_pak_resize`'s
`mp_nuked.ff` and `mp_nuked.pak` in place of the retail files (restored afterwards):

- The male mannequin face shows the OPENT5 / 256x512 pattern on magenta, mapped correctly on
  the head, with no garbage blocks.
- The rest of the map is normal.

This confirms on RPCS3 that the game reads `<zone>.pak` from beside `<zone>.ff` (the INFERRED
lookup in docs/research/pak.md) and that a resized streamed image with an appended pak entry
and rewritten part records loads.
