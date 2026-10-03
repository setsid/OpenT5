# Box map with its own lighting and compass

The box (docs/demo-box.md) rebuilt with a light grid volume and converted with
`--lighting baked`: its own cod2rad lightmaps, reflection probe, outdoor image, sun and light
grid, plus a compass of its own. Two outputs: the box over Nuketown (`g_box_lit`) and the box
under its own name (`h_box_named_lit`). Checked offline only (docs/convert.md 10).

## Files

| File | sha1 | Bytes | What |
|---|---|---|---|
| `out/demo/g_box_lit/mp_nuked.ff` | `ade718f6ccf9ab6feb858de1a3e4bf24cea471a5` | 23 988 256 | the box in place of Nuketown |
| `out/demo/h_box_named_lit/mp_opent5box.ff` | `84b58ac3ee96559055ca0bcad107265b00e89bc9` | 23 987 776 | the box as its own map |
| `out/demo/h_box_named_lit/mp_opent5box.pak` | `5930c6c683d9339d0357a7266a8d37819b95e6cf` | 172 005 376 | its image pack: a byte copy of the disc `mp_nuked.pak` |
| disc `mp_nuked.ff` (retail, for the backup) | `6d5a4a0e5c031c0d8fcd555413730b014e9c0c5d` | 36 349 760 | |
| PC input `zone/English/mp_opent5box_grid.ff` | `27bf78b34e24177f3c5cc5646eb76bf15961c53b` | 295 968 | the box with a `lightgrid_volume` brush, built with the PC Mod Tools |

Each folder also holds `convert.json` (every decision: the converted images with their
sha1s, the grid, the corners, the compass), `cli.json`, `verify.json`, `oracle.json` and
`selftest.json`.

Made with (PC map built as box-map.md 1.2 with one more brush, docs/convert.md 10.3):

    opent5 convert "<PC game folder>/zone/English/mp_opent5box_grid.ff" --base mp_nuked -o out/demo/g_box_lit/
    opent5 convert "<PC game folder>/zone/English/mp_opent5box_grid.ff" --base mp_nuked --name mp_opent5box --copy-pak -o out/demo/h_box_named_lit/

## What is inside

| Item | Both outputs |
|---|---|
| Lightmaps | `*lightmap0_primary` DXT1 1024 x 1024, `_secondary` R5G6B5 512 x 1024, `_secondaryb` Y16_X16 512 x 512: the box's own, from cod2rad, stored in the zone |
| Reflection probe | `*reflection_probe0`, the 4 x 4 black cube the PC tools write |
| Outdoor image | `$outdoor` B8 512 x 512, the box's own, with the box's own outdoor matrix |
| Sun | the PC sun light, swapped |
| Light grid | 2883 entries, 2164 colours from cod2rad (was 0 entries, 1 colour) |
| Minimap corners | two `minimap_corner` script_origins at (576, 576, 0) and (-576, -576, 0) |
| Compass | DXT23 512 x 512, the room from above (grey floor, light wall outline, transparent around it), in the zone: `compass_map_mp_nuked` in `g_box_lit`, `compass_map_mp_opent5box` in `h_box_named_lit` |

## Checks (offline)

| Check | `g_box_lit` | `h_box_named_lit` |
|---|---|---|
| Reparse exact, rewrite identical, unresolved pointers | yes, yes, 0 of 53 167 | yes, yes, 0 of 53 167 |
| `opent5 verify --against mp_nuked` | ok (container 890 chunks, parse exact, rewrites identically); asset by asset not compared: 530 assets against 529 (the compass material is new) | the same |
| Emulated loader (`tools/convert_map.py oracle`) | consumed exactly, blocks end at the header, 53 167 conversions, same fields and values | the same |
| GUI self-test (with code_post_gfx_mp) | 99 checks, 0 failed | 99 checks, 0 failed |
| Decoded images | floor chart of `_secondaryb` brightest under the light (up to 2053), walls and ceiling lit; compass shows the room | the same |
| Determinism | two runs, same sha1 | |

## Install

Backups first. Use the signature-patched multiplayer client, as before.

`g_box_lit` (box over Nuketown):

1. Copy `<OPENT5_ZONES>/mp_nuked.ff` to `mp_nuked.ff.retail.bak` (check the retail sha1
   above) unless that backup already exists.
2. Copy `out/demo/g_box_lit/mp_nuked.ff` over `<OPENT5_ZONES>/mp_nuked.ff`.
3. Leave `mp_nuked.pak`, `code_post_gfx_mp.ff`, `images_low.pak` and the update's
   `patch_mp.ff` as they are: nothing in them is needed or changed.
4. Restore: copy the backup back and check its sha1.

`h_box_named_lit` (box as `mp_opent5box`):

1. Copy `mp_opent5box.ff` and `mp_opent5box.pak` into `<OPENT5_ZONES>` (new names; nothing
   is overwritten; if the files of docs/demo-box-named.md are there, these replace them, so
   keep those aside if wanted).
2. The map loads under its own name, but the menus list only the maps of `mp/mapstable.csv`
   in patch_mp.ff: to start it you still need the opt-in map-table row
   (docs/demo-box-named.md "Starting it", map-registration.md 5): a stock-file change that is
   your decision. Without it the map is installed but nothing offers it.
3. Remove: delete the two files (and restore `patch_mp.ff` from its backup if the row was
   installed).

Run: Team Deathmatch or Free-for-all, splitscreen or private match, as before.

## What you should see

- Floor lit: brightest in the middle under the room's light, darker towards the walls; not
  black.
- Ceiling: cream (`pent_art_wall_creampaint02`) lit by the same light, not brownish.
- Walls: white siding, lit.
- Viewmodel (weapon and hands): normal colours, not teal (the light grid now holds the box's
  own light).
- Minimap: the room as a grey square with a light outline, the player arrow inside it, north
  up; no placeholder grid and no garbled letters.

## Failures and what they would mean

| You see | Most likely meaning |
|---|---|
| Fastfile / signature refusal | not the patched client, or a file copied incompletely (check sha1s) |
| Error naming `compass_map_*` or a material, or a crash while loading | the appended compass material: report the text; the loader emulation consumed it exactly, so it would be a run-time lookup |
| Room black or all one flat colour | the lightmaps: note which surfaces; the images match the PC ones byte for byte after the proven transform, so it would be how the three images combine in the shader (box-lighting.md 2.5) |
| Floor and walls lit, viewmodel still teal | the light grid colour encoding or the out-of-grid default; note whether it changes when you move |
| Minimap still a placeholder grid in `g_box_lit` only | the update's Nuketown script draws `compass_map_mp_nuked` from code_post_gfx_mp, so the zone's material of that name did not replace it (docs/convert.md 10.4, the INFERRED point); `h_box_named_lit` uses its own name and should be unaffected |
| Minimap shows the room rotated or mirrored | north: the box has no `northyaw` key, so north is +X; report which way the spawn arrow points against the room |
| Minimap blank (no grid, no room) | the material was found but its image or state bits do not draw; report |

Please report which rows match, the output used, a screenshot inside the room with the
minimap visible, and any RPCS3 log lines about fastfile, DB, script or RSX errors.
