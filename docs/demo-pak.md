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
