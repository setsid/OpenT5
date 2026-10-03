# Demo: the full test map (`mp_opent5box_full`)

One PC map with everything the converter now does, converted twice: over Nuketown (the
stock name) and as its own map `mp_opent5box`. It carries:

- the sealed room of docs/demo-box.md (1024 x 1024 x 256 units);
- the map's own cod2rad lighting: lightmaps, a light grid filled from a `lightgrid_volume`
  brush (docs/demo-box-lit.md), and the room light as a primary light (docs/convert.md 13);
- walls in `blockout_test_concrete`, a PC Mod Tools material whose texture is in no PS3
  zone, built into the map's own zone (docs/convert.md 11): a light and dark grey check with
  the printed values "117 (18%)" and "186 (50%)";
- eight stock Nuketown props (docs/demo-box-models.md), in two rows of four along the north
  and south walls;
- spawns and objectives for all twelve gametypes (docs/demo-box-modes.md);
- a minimap of its own (docs/convert.md 10.4).

Status: built and checked offline only (parse, the game's own loader emulated, verify, GUI
self-test, decoded images, previews). Not run on a console or an emulator.

## 1. Files

| File | sha1 | Bytes |
|---|---|---|
| PC zone `zone/English/mp_opent5box_full.ff` (PC Mod Tools) | `d1790102da5847276b5c676966e3dcef2bf69f56` | 1 349 376 |
| `out/demo/k_box_full/mp_nuked.ff` (over Nuketown) | `17d6a7e97424c6c6e42c07a12d055a250b8dde83` | 23 963 744 |
| `out/demo/l_box_full_named/mp_opent5box.ff` (its own map) | `154cf6c69c66a785edd1a823adf64d9c43dd65f6` | 23 962 464 |
| `out/demo/l_box_full_named/mp_opent5box.pak` (a byte copy of the disc `mp_nuked.pak`) | `5930c6c683d9339d0357a7266a8d37819b95e6cf` | 172 005 376 |
| disc `mp_nuked.ff`, retail (to back up and restore) | `6d5a4a0e5c031c0d8fcd555413730b014e9c0c5d` | 36 349 760 |

Each folder also holds `convert.json`, `oracle.json`, `verify.json`, `selftest.json`, the
decoded wall texture and compass image (PNG) and `previews/`.

Made with:

    .venv/bin/python tools/testmap.py build mp_opent5box_full --full \
        --game "C:\Program Files (x86)\Steam\steamapps\common\Call of Duty Black Ops" \
        --work "C:\o5\full" -o PC_OUT.ff
    opent5 convert "<game>/zone/English/mp_opent5box_full.ff" --base mp_nuked --modes all \
        -o out/demo/k_box_full
    opent5 convert "<game>/zone/English/mp_opent5box_full.ff" --base mp_nuked --modes all \
        --name mp_opent5box --copy-pak -o out/demo/l_box_full_named

The base was a verified retail `mp_nuked.ff` (sha1 above). The wall texture's `.iwi` was
read from the game folder the map was built in (`raw/images/~-gblockout_average_test_c.iwi`).

Files the build created in the Steam game folder (all new, all named
`mp_opent5box_full`; delete them to remove the test map):

```
raw/maps/mp/mp_opent5box_full.d3dbsp
raw/maps/mp/mp_opent5box_full.d3dpoly
raw/maps/mp/mp_opent5box_full.d3dprt
raw/maps/mp/mp_opent5box_full.grid_auto
raw/maps/mp/mp_opent5box_full.radtrans
zone/English/mp_opent5box_full.ff
zone_source/mp_opent5box_full.csv
zone_source/english/assetinfo/mp_opent5box_full.csv
zone_source/english/assetinfo/mp_opent5box_full_dep.txt
zone_source/english/assetinfo/mp_opent5box_full_xmodel.csv
zone_source/english/assetlist/mp_opent5box_full.csv
```

A search of the whole game folder for files changed during each build found these and
nothing else. Outside the game: `C:\o5\full\` (the `.map` and three `.bat` files).

## 2. Offline checks (both zones)

| Check | k_box_full (`mp_nuked`) | l_box_full_named (`mp_opent5box`) |
|---|---|---|
| reparse, write back | exact, identical | exact, identical |
| offset and alias pointers | 53 222, 0 unresolved | 53 222, 0 unresolved |
| emulated loader (`tools/convert_map.py oracle`) | consumed exactly, every block ends at its header size, 53 222 conversions with the product parser's fields and values, none outside its block | the same |
| `opent5 verify --against mp_nuked` | ok (container 892 chunks, parse exact, rewrites identically; 530 assets: the compass adds one) | ok |
| objectives (`convert.json`, `objectives`) | all twelve ready, no warnings | all twelve ready, no warnings |
| new material | `wc/blockout_test_concrete`: techset `wc_l_sm_r0c0` (same name), state bits from `wc/blockout_test_fabric01`; image `~-gblockout_average_test_c` DXT1 512 x 512, 10 levels, 174 848 bytes in the zone's end-of-zone block | the same |
| static models | 8 placed, 8 in the clipMap list (collision) | the same |
| light grid | 1767 colours; 2883 of 2919 entries name the room light (docs/convert.md 13) | the same |
| configstring tables | the twelve stock tables, every cell equal to retail mp_nuked's | the same, under the map's name |
| scripts | Nuketown's `r_lightGrid*` tweaks removed from the art script | the same, plus `compassGridEnabled 0` and `compassRotation 0` in the map's own main() |
| GUI self-test | 52 checks, 0 failed, 0 errors (it reports not ok only because a map zone has no localize asset to exercise) | the same |
| determinism | two conversions, same sha1 | the same |

Looked at: the wall texture decoded from each zone (the grey check with its printed values),
the compass image (a grey square with light outlines; props are not drawn on it), a render
of the world with the ceiling removed and the static models placed (`previews/props_*.png`:
the eight props on the floor in two rows), and the GUI world view with `Static models` on
(`previews/gui_world_models.png`).

## 3. Install

Back up first. Both modes use the signature-patched multiplayer client.

**Over Nuketown** (`k_box_full`):

1. Copy the disc `english/mp_nuked.ff` to `mp_nuked.ff.retail.bak` (same folder, or anywhere
   safe) and check its sha1 is the retail one above.
2. Copy `out/demo/k_box_full/mp_nuked.ff` over the disc `english/mp_nuked.ff`.
3. Leave `mp_nuked.pak` and the update's `patch_mp.ff` as they are.
4. To restore: copy the backup back and check the sha1.

**Its own map** (`l_box_full_named`): copy `mp_opent5box.ff` and `mp_opent5box.pak` into the
disc `english` folder beside `mp_nuked.ff` (new names, nothing overwritten). Starting it
needs the map-table row in patch_mp, which is a stock zone change you opt into
(docs/demo-box-named.md, "Starting it"). To remove: delete the two files (and restore
patch_mp if you changed it).

## 4. Which mode to start and what to see

Start with Team Deathmatch (the mode both earlier box demos ran). Then each other mode;
every one is checked ready offline.

- The room: floor `jun_art_concrete_base02`, ceiling `pent_art_wall_creampaint02` (both
  Nuketown materials), lit from the map's own lightmaps, the floor brightest under the
  ceiling light.
- Walls: the grey check of `blockout_test_concrete` with "117 (18%)" and "186 (50%)"
  printed on it, clearly different from the earlier boxes' white siding and wood.
- Props: sandbag, mailbox, fence, cardboard box (turned 30 degrees) along the north wall
  (y = +448); trash can, potted plant, wood stack (45 degrees), barricade (90 degrees) along
  the south wall (y = -448). You should not walk through them (they are in the clipMap
  list; INFERRED).
- Minimap: a grey square with light wall outlines. Named map: no letter grid and a
  non-rotating map (INFERRED until run). Over Nuketown: the letter grid still draws and the
  map turns with the player, as on stock Nuketown (patch_mp's script runs there, not the
  zone's; docs/convert.md 13).
- Viewmodel: lit by the room light (the grid entries now name it). This is the point of the
  primary light; INFERRED until run.
- Objectives per mode (allies and attackers start on the west side, axis and defenders on
  the east; layout table in docs/convert.md 9.5): sd and dem two bomb sites A and B on the
  east side (bomb stack models), sd's bomb on the west; sab a bomb in the middle and a
  target on each side; ctf a flag behind each team's start; dom three flags in a line
  A, B, C; Headquarters two radios at the north and south middle with crates; dm, oic, gun,
  shrp, hlnd spawns only.

## 5. Failures and what they would mean

| Seen | Likely meaning |
|---|---|
| refused at once, signature message | not the signature-patched client, or a partial copy (check the sha1) |
| walls black or a default texture, room fine | the new material's techset or state bits do not suit the RSX shader, or the image is not read from the end-of-zone block (docs/demo-box-textures.md 6) |
| props missing, room fine | culling data or a draw instance field (docs/demo-box-models.md 5) |
| props black | their lighting (lightingHandle, groundLighting, light grid) |
| viewmodel still near-black | the primary light alone is not enough; try `r_lightGridEnableTweaks 0` at the console and report (docs/research/box-rpcs3-issues.md 2) |
| letter grid still on the named map's minimap | the level script's setDvar does not reach the client compass in splitscreen |
| SD: no A/B icons, no bomb sites or bomb, round runs | open item C below, not fixed here |
| Domination hangs at "Awaiting challenge" | open item D below, not fixed here |
| crash on load | note where; the loader itself consumed the zone exactly offline |

## 6. Open items

- C and D of docs/research/box-rpcs3-issues.md (Search and Destroy objectives not drawn,
  Domination hanging on load) are engine-level and unconfirmed. The leading hypothesis is
  the PS3 per-map configstring tables (`configstrings_ps3_mp_nuked_<gametype>.csv`)
  mismatching the shaders the box registers (EXE_CONFIGSTRINGMISMATCH). This demo carries
  the twelve stock tables unchanged (cell for cell), so it will show the same faults if that
  is the cause. A device TTY log is needed before a converter change.
- Props are XModels of the base zone only (docs/convert.md 12.5).
- The compass image shows the room only, not the props.
- Ceiling colour: the light is white and the ambient bluish, so the cream ceiling reads grey
  (box-rpcs3-issues.md 2.4); a PC map property, not changed here.
