# mp_nuked acceptance demos

Three edits of Nuketown (`mp_nuked.ff`, disc, sha1 `6d5a4a0e5c031c0d8fcd555413730b014e9c0c5d`,
36 349 760 bytes), each made with the command line (docs/cli.md), verified offline, and checked
with the emulated loader. Outputs are under `~/opent5/out/demo/` (git-ignored; game data never
enters the repository).

| Demo | File | sha1 | Bytes |
|---|---|---|---|
| a, rebuilt unmodified | `out/demo/a_rebuilt/mp_nuked.ff` | `6d5a4a0e5c031c0d8fcd555413730b014e9c0c5d` (= disc) | 36 349 760 |
| b, entity moved | `out/demo/b_entity_move/mp_nuked.ff` | `3740413f440542d7c02eb85395aeb753eaa8d94e` | 36 349 920 |
| c, street-sign texture | `out/demo/c_texture/mp_nuked.ff` | `6ba233da331569bc45de5112939b7f808148995b` | 36 346 560 |
| c (extra), sky texture | `out/demo/c_sky/mp_nuked.ff` | `6f8edf7ee5f6bd9e1cad0e4c5f7575d4cfe6e43c` | 35 891 072 |

Each folder also holds the command's JSON report (`rebuild.json` / `replace.json`), the input
file given to `replace`, and for b and c the emulated-loader report (`oracle.json`).

To try one in RPCS3: copy it over `PS3_GAME/USRDIR/english/mp_nuked.ff` of the game (keep the
retail file; its sha1 is above), with the signature-patched multiplayer client used for the
earlier remap tests. b and c have a console signature that no longer matches, so an unpatched
client refuses them. `mp_nuked.pak` stays as it is: none of the demos touches streamed images.
Put the retail file back afterwards. Only one demo can be installed at a time.

## a. Rebuild

    opent5 rebuild mp_nuked.ff -o out/demo/a_rebuilt/mp_nuked.ff

All 529 assets parsed and written back from their parsed form; the content is identical and
the file is byte-identical to the disc file (sha1 above; 1 404 chunks). With `--recompress`
(every chunk deflated afresh rather than carried) the result is the same sha1, in 8.5 s.

In RPCS3 this file is the retail file, so Nuketown loads as normal.

## b. Entity moved (entity string resized)

What changed: the entity string (MapEnts, loaded inside the clipMap
`maps/mp/mp_nuked.d3dbsp`, asset 407). One entity, the green Tiara car that is a destructible
`script_model` (`destructibledef veh_tiara_destructible_green_mp`, model `t5_veh_civ_tiara`),
moved up by 250 units (about 6.4 m):

    "origin" "-59.5 804 -66"   ->   "origin" "-59.500000 804.000000 184.000"

Why this entity: it is visible from much of the map, it is in every game mode (it has no
`script_gameobjectname`), and its model is drawn at its entity origin, so the move cannot be
mistaken for anything else. Static props compiled into the world (misc_model) are not
entities in the zone, and spawn points cannot be seen. Why this text: the value is 16 bytes
longer, a multiple of the largest alignment met after it, so the shift is not absorbed by
padding and reaches the end of block memory: the zone is re-laid out, not patched in place.

How it was made:

    opent5 extract mp_nuked.ff WORK --type col_map_mp     # WORK/map_ents/mp_nuked.d3dbsp.ents
    (edit line 1305 as above: out/demo/b_entity_move/mp_nuked_moved.ents)
    opent5 replace mp_nuked.ff col_map_mp:maps/mp/mp_nuked.d3dbsp mp_nuked_moved.ents \
        -o out/demo/b_entity_move/mp_nuked.ff

Offline verification (`replace.json`): reparsed exactly; 529 assets checked: 455 identical,
73 identical apart from remapped offset pointers (16 512 pointers, each naming the same string
or the same allocation as before), 1 edited and read back with the new text. 15 680 pointer
fields rewritten in all; VIRTUAL block size 0x1eb81c1 -> 0x1eb81d1 (+16); content +16 bytes;
`opent5 verify --against` reports the clipMap as the only changed asset.

Emulated loader (`oracle.json`; t5mp.elf's own XFile loader run in the local PowerPC
interpreter of `tools/remap_oracle.py`, `check_remap`, 55 s): the loader consumed the whole
edited content; its final block positions equal the new header (VIRTUAL 32 211 409 =
0x1eb81d1); 109 227 offset pointers converted, the same count as for the original, and 0
values different from the mapping; the loaded VIRTUAL image equals the
original once each allocation is moved back (28 727 pointer words mapped back, 0 unexplained
bytes); TEMP, RUNTIME, PHYSICAL_RUNTIME and PHYSICAL images identical; all 109 227 pointed-to
windows equal after mapping (0 bad). The one grown allocation is the entity string.

What you should see in RPCS3: load Nuketown (any mode, e.g. a private match or Combat
Training, Team Deathmatch). Go to the middle of the street at the end with the open moving
truck and its furniture (map position about x -60, y 800). The green Tiara car that is
normally parked on the road there hangs in the air above its usual spot, its underside about
level with the upstairs windows of the houses, facing the same way. The other two green Tiaras (one in front of a house
at about x -709, y 21, and one that exists only in Headquarters at about x 557, y 629) stay on
the ground, as does everything else. The floating car should still take damage and explode when shot, as
the destructible script reads its position at run time (INFERRED, not tested).

What failure would look like:

- the game stops on the loading screen, returns to the menu, or reports a fastfile or
  memory error: the loader did not accept the zone (the emulated loader says it does, so
  this would point at post-load work the emulator skips);
- the car is on the ground in its normal place: the retail file is still installed (check the
  sha1) or the entity string was not read from this file;
- the map loads but other things are wrong (missing or swapped models and textures, broken
  geometry, objects in the wrong places, spawn points in walls): a pointer was remapped to the
  wrong target. The offline checks and the emulator say none was.

## c. Texture replaced

What changed: the image `~-gus_art_streetsigns_c` (DXT1, 128 x 64, 8 mips), the colour map of
the material `wc/us_art_streetsigns`: the green street-name signs "Trinity Ave." and
"Latchkey Rd.". Its pixels are in the zone (in the deferred tail at the end of the stream,
PHYSICAL_RUNTIME), so the zone alone carries the change and the `.pak` stays untouched; images
streamed from `mp_nuked.pak` are reported as not replaceable, because writing a `.pak` is not
implemented. The new pixels are a test pattern of the same size and format
(`streetsigns_test_pattern.png`): the top half bright yellow with "OPENT5" in black, the bottom
half an 8-pixel magenta and black checkerboard. Mips are rebuilt from it with a box filter
and re-encoded to DXT1.

    opent5 replace mp_nuked.ff 'image:~-gus_art_streetsigns_c' streetsigns_test_pattern.png \
        -o out/demo/c_texture/mp_nuked.ff

Offline verification: reparsed exactly; 528 assets identical, 1 edited (the GfxWorld
`maps/mp/mp_nuked.d3dbsp`, asset 405, which loads the image inline) and read back with exactly
the new stored pixels; extracting the image from the new file gives the test pattern. Emulated
loader (`oracle.json`): whole content consumed, final positions equal the header, 109 227
pointers converted with 0 mismatches, every block image identical except PHYSICAL_RUNTIME,
where 5 198 bytes differ: the new pixels.

What you should see in RPCS3: the street sign stands on a pole at the opposite end of the
street from the moving truck, a little east of the middle (map position about x 250, y -570),
with the signs about 3 m up. Instead of the green "Trinity Ave." / "Latchkey Rd." signs it
shows yellow panels with black "OPENT5" lettering over a magenta and black checkerboard (the
two signs use different halves of the texture, so one may show mostly the lettering and the
other mostly the checkerboard). Nothing else changes. From a distance the smaller mips show
blended colours; up close the pattern is sharp.

What failure would look like: the signs still show street names (retail file installed, or
the game took the image from elsewhere); the signs are black, white or garbled noise (the
pixel encoding is wrong for the GPU); or the map fails to load (see b).

### Extra: the sky

`out/demo/c_sky/mp_nuked.ff` replaces the cube map `skybox_mp_cosmodrome_ft` (DXT1, 512 x 512,
6 faces, 1 mip, deferred), loaded by the sky model `skybox_mp_nuked` (asset 441), from
`sky_test_pattern.dds`: each face a 64-pixel checkerboard with "OPENT5" across the middle, in
face order +X -X +Y -Y +Z -Z: red, green and blue on white, then yellow, magenta and cyan on
black (`sky_test_pattern_face0.png` is the +X face). Same checks: 528 assets
identical, the image read back exactly; the emulated loader consumed the zone, 0 pointer
mismatches, only PHYSICAL_RUNTIME differs (773 159 bytes, the six faces).

What you should see: wherever you look up, the sky is a large coloured checkerboard box with
"OPENT5" on its sides instead of clouds. The face orientation in-game (which colour is where,
and whether the letters are rotated or mirrored) is not established; any of those means the
replacement worked. Failure would look like the normal sky, a black sky, or a load failure.
