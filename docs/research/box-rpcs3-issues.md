# Box map: first console runs of the converted lighting and gametype zones

Status: diagnosis. The RPCS3 runs of `out/demo/g_box_lit/mp_nuked.ff` (box-lighting.md,
demo-box-lit.md) and `out/demo/i_box_modes/mp_nuked.ff` (demo-box-modes.md) on a
signature-patched splitscreen client showed four faults. This file gives the cause of each
with evidence, a confidence, the exact converter fix, and what an offline check proved versus
what still needs a device run. READ-ONLY diagnosis: no `src/` change was made.

Conventions as box-lighting.md. "Box lit" is `out/demo/g_box_lit/mp_nuked.ff`; "box modes" is
`out/demo/i_box_modes/mp_nuked.ff`; "PS3 nuked" is the retail disc `mp_nuked.ff`. ELF
addresses are into the decrypted `t5mp.elf`. Offsets are into the inflated zone stream.
Scratch work is under `<scratch>/diag/` (`A/`, `B/`, `CD/`).

Observed (local splitscreen, patched client):

- **A.** Box lit, Team Deathmatch: floor, walls and the ceiling hotspot are lit from the box's
  own lightmaps, but the viewmodel is near-black (teal before, with Nuketown's grid), and the
  ceiling reads dull grey, not cream.
- **B.** Box lit minimap: the grey room square and the player arrow draw, but the placeholder
  letter grid (A2, B3, C4 ...) still draws over them; in one run the square looked rotated
  about 45 degrees.
- **C.** Box modes, Search and Destroy: the round starts ("DESTROY TARGET A OR B", timer runs)
  but there are no A/B icons on HUD or compass, no bomb site models and no bomb, well into the
  round.
- **D.** Box modes, Domination: hangs on the load screen at "Awaiting challenge...0"; the local
  server never finishes starting the map.

## 1. Summary

| # | Cause | Confidence | Fix sits in | Proven offline |
|---|---|---|---|---|
| A1 | The box's light grid holds almost no light (room light is not a primary light) | medium-high | `tools/testmap.py` + `convert/world.py` | grid placement and luminance read from the zone |
| A2 | Base art script's `r_lightGrid*` tweaks are tuned for Nuketown | low-medium | `convert/scripts.py` | script lines read from the converted zone |
| A3 | Ceiling grey: cream surface under a white/bluish box light, not a warm sun | medium | PC map side (`_color`/`_ambientcolor`) | lightmap and light colours read |
| B1 | The letter grid is drawn by engine code on a dvar defaulting on, not by the image | medium-high | no converter change; map script `setdvar` | ELF draw path and dvar defaults; image decode |
| B2 | 45 degrees is the compass rotating with the player (`compassRotation` default 1) | medium | no converter change | ELF; corners and absence of `northyaw` read |
| C/D | The box's objective entities are structurally correct and consumed exactly; the fault is at engine/runtime level (leading: PS3 per-map configstring tables, or a runtime error in the gametype thread) | low, unconfirmed | likely `convert/` name/registration, or `testmap.py` | entity chains replayed through the stock filter and setup |

## 2. A: viewmodel near-black, ceiling grey

### 2.1 The light grid is correctly placed but nearly empty (A1, medium-high)

The grid is not misplaced or mis-encoded. cod2rad.exe at 0x4333f0 maps a grid index to a world
position as `x = (i - 0x1000) * 32`, `y` likewise, `z = (i - 0x800) * 64`. The box's
GfxLightGrid header (PS3 +0x238, box lit zone) reads mins `0ff1 0ff1 0801`, maxs
`100f 100f 0803`, rowAxis 0, colAxis 1: samples from -480 to 480 on x and y and z 64 to 192, so
the grid does cover a player and the viewmodel. All 31 rows read `0ff1 001f 0801 0003 <first>
1f030000` (`<scratch>/diag/A/grid.py`).

What is wrong is the content. Reading each 24-bit entry colour as a little-endian word, bits
0..4 and 6..10 behave as two 5-bit chroma values centred near 16 and bits 12..22 as an 11-bit
luminance (INFERRED, from bit statistics; see 2.3). The box's luminance percentiles
(1/10/50/90/99) are 209/216/230/242/251; PS3 nuked's are 322/785/1151/1275/1391. The viewmodel
point (0, 0, 60) lands on entry 1440, colour 190, mean 254: the whole box sits below Nuketown's
darkest one per cent. The same reading explains the earlier teal, since the out-of-grid default
`40059d` decodes to chroma (0, 21), strongly off-neutral, luminance 464.

Why so dark: the box's room light is a plain `light` with no spawnflags (`tools/testmap.py`
413..417). The PC tools' `bin/codbo.def` (line 42) make a primary light only for PRIMARY_OMNI
or PRIMARY_SPOT. So the box ComWorld carries 2 primary lights (an empty slot and the sun) and
every one of the 2883 grid entries has primaryLightIndex 0, where PS3 nuked has 24 primary
lights, 22 of them `white_light` spots, and 1119 entries referencing them. cod2rad baked the
grid from an ambient term alone.

Fix, ordered:

1. `tools/testmap.py`: give the room light `spawnflags 1` (PRIMARY_OMNI) and rebuild the PC
   zone. The converter must then carry primaryLightCount 3 and the extra RUNTIME shadow array,
   and the light's def must resolve in the base zone (INFERRED; confirm by reparse and the
   oracle after rebuild).
2. `convert/lighting.py`: report the grid's luminance percentiles and warn when the median
   falls below PS3 nuked's floor (about 322).

### 2.2 The base art script fights the box grid (A2, low-medium)

The kept `createart/mp_nuked_art.gsc` (lines 43..45 of the converted zone) still sets
`r_lightGridEnableTweaks 1`, `r_lightGridIntensity 1.25`, `r_lightGridContrast 0.18`. These are
tuned for Nuketown and act on the box's much darker grid.

Fix: `convert/scripts.py`, when lighting is baked, drop the three `r_lightGrid*` SetDvar lines
from the base's art script.

Device check: set `r_lightGridEnableTweaks 0` at the console. If the viewmodel brightens, A2 is
a contributor; if it stays dark, the A1 primary-light rebuild is the real fix.

### 2.3 Colour decode is still inferred

The PPU function at t5mp.elf 0x749668 only reads the `r_lightGrid*` dvars and hands the grid
lookup off to an SPU job (call to 0x4c0bf8 at 0x749a78). There is no SPU disassembler in this
environment, so the 4-field / luminance split above is INFERRED from bit statistics over the
nuked and box colour arrays, not read from the sampler. A device run of the rebuilt grid, or an
SPU disassembly, would confirm it.

### 2.4 Ceiling grey (A3, medium)

The ceiling reuses the stock PS3 material `*15n_10(wc/pent_art_wall_creampaint02 ...)` with
colorTint 1,1,1 (`<scratch>/diag/A/mats.py`), and its secondaryB lightmap (1228/1264) is about
as bright as the floor's (1370/1175), so brightness is not the problem. The difference is the
light colour: the box has a white light `1 1 1` and a bluish ambient `0.80 0.82 0.99` at 0.1,
where Nuketown has a warm sun `0.996 0.976 0.886`. A cream surface under white/bluish light
renders neutral grey. This is a PC map property, not a conversion fault: fix it on the PC side
with a warmer `_color` / `_ambientcolor`, or accept grey.

## 3. B: the placeholder letter grid and the rotation

### 3.1 The letters are drawn by engine code, not the image or a menu (B1, medium-high)

The stock compass image has no letters: a decode of `compass_map_mp_nuked`
(`p6l/compass_map_mp_nuked.png`) shows only the plan and alpha. The grid is drawn by the
function at t5mp.elf 0xe0570, which loops over `compassGridRows` x `compassGridCols` and forms
the labels as `'A' + row` (0xe0934 `addi r21,r18,65`) and `'1' + col` (0xe09e8
`addi r6,r31,49`). The dvars are registered at 0xdbfc4..0xdc018: `compassGridEnabled` is a bool
defaulting to 1, `compassGridRows`/`compassGridCols` default 5 (range 1..9). Both ownerdraw
paths (0x125458 and 0x126338) call 0xe0570 only when the bool lookup (0x4c7bf0) returns true.
No stock zone or script sets the dvar: the string "compassGrid" is absent from all nine
inflated zones and every extracted script.

So this grid should draw over stock Nuketown too (INFERRED; a fade gate at 0xe0640..0xe0684 was
not fully decoded). The box's own grey image did replace code_post_gfx_mp's
`compass_map_mp_nuked` (asset 529 in `convert.json`), so there is no second minimap drawing
underneath: the letters sit on top of the correct room square.

Fix: no converter change is needed for correctness. To hide the letters a level script would
call `setdvar("compassGridEnabled", 0)`. `h_box_named_lit` has its own script and can do this.
`g_box_lit` cannot: patch_mp's `mp_nuked.gsc` (rank 20) replaces the zone's script, and editing
patch_mp would break the custom-map rule. A device test of `setdvar` from a local game is
needed (INFERRED that a server-side setdvar reaches this client dvar in splitscreen).

### 3.2 The 45-degree look is the compass turning with the player (B2, medium)

`compassRotation` is registered at 0xdb8dc with default 1 and stored at 0xc1a904. At 0xe0798 the
code builds a yaw rotation (call to 0x4b9928), so the whole map turns with the player's view; a
square seen while facing diagonally looks rotated 45 degrees. The corners are not at fault: the
converted entity string holds the two `minimap_corner` script_origins at offset 0x10473d0
(576 576 0) and 0x1047423 (-576 -576 0), there is no `northyaw` key in the box or in stock
mp_nuked, and `_compass.gsc` `setupMiniMap` (lines 10..36) picks north-west = (576, 576)
whichever entity is first, matching `convert/compass.py`. No corner-order or north bug.

Device check: turning in place should rotate the square while the arrow stays pointing up;
`^1Error: There are not exactly two "minimap_corner"` should not appear.

## 4. C and D: Search and Destroy objectives, Domination hang

### 4.1 The box's objective entities are structurally correct (proven offline)

The entity string is not the fault. `numEntityChars` in the box-modes zone is 0x37b1 = 14257,
the exact string length, so it is not truncated (`<scratch>/diag/CD/dump.py`).
`<scratch>/diag/CD/sim.py` replays three stock functions over the box entities:

- the `_gameobjects.gsc` filter (`main` 4..33, `entity_is_allowed` 34..57) keeps the `bombzone`
  and `sd` tagged entities;
- `sd.gsc` `bombs()` (430..517) resolves both sites `_a`/`_b`, their `trigger_use_touch`
  brush models `*1`/`*3`, the `p_glo_bomb_stack` visuals, the pickup trigger and the defuse
  trigger;
- `dom.gsc` `domFlags()`/`flagSetup()` (214..311, 741..830) find 3 flags, 3 descriptors and
  every spawn class, with no "Map errors" and no AbortLevel.

The box chains match stock Nuketown's (`box.ents` vs `nuked.ents`); stock carries a few extra
keys (`script_exploder`, `script_bombmode_original`, a `model` key on the flag triggers) that
the scripts do not require. Every model the scripts use (`prop_suitcase_bomb` sd.gsc 447,
`p_glo_bomb_stack`, `mp_flag_allies_2`, `mp_flag_axis_1`) is present in the box zone or
common_mp, and the brush triggers' cmodels 1..14 have sane bounds and the stock contents word
`08000001`. So the converter produced correct, fully consumed objective data.

### 4.2 The fault is at engine/runtime level (unconfirmed, needs a device run)

Because the entities are correct, the symptoms point past the zone data. Ranked hypotheses,
all INFERRED:

1. **PS3 per-map, per-gametype configstring tables (leading).** The box keeps the name
   mp_nuked, so the client loads `configStrings_ps3_mp_nuked_sd.csv` /
   `..._dom.csv` (path string at t5mp.elf 0x90d710) built for Nuketown. SD precaches 19 HUD and
   compass shaders and `prop_suitcase_bomb` (sd.gsc 50..69, 447), each needing a configstring; a
   server/client index mismatch raises EXE_CONFIGSTRINGMISMATCH (code_post_gfx_mp 0x32067c),
   which would leave Domination's client stuck at "Awaiting challenge" (D) and, where
   non-fatal, leave SD icons and models unregistered on the client so they never draw (C). The
   CD fork found the box's models are listed in all twelve tables, so if this is the cause it is
   the shaders or the index order, not the models. A converter fix would regenerate or validate
   the configstring tables for the converted map/gametype set.
2. **A runtime error in the gametype thread (C especially).** A script error kills only the
   running thread, not the level. SD's round message and timer come from the globallogic
   thread, so an error in `bombs()` setup (an engine builtin behaving differently on the box
   entities, e.g. `getentarray` or `set2DIcon`) would give exactly C: round runs, no
   objectives. For D, dom's flag setup runs during the load phase, so the same class of error
   there would stall the start. The ELF watchdog "potential infinite loop in script"
   (file offset 0x91ede0) trips only on tight loops without a `wait`, so a wait-on-entity loop
   would hang silently rather than error.
3. **A true wait-forever in dom load.** Not seen in `dom.gsc` (the only load-phase loops around
   214..311 are bounded), so lower than the above.

No converter fix is proposed for C/D until a device run narrows it.

## 5. What a device run should capture (per issue)

Retail TTY prints little, so capture the full log.

- **A:** whether the viewmodel brightens with `r_lightGridEnableTweaks 0` (A2) or stays dark
  (A1). No specific log line.
- **B:** on retail Nuketown, are the A1..E5 letters also visible (would confirm stock
  behaviour)? Does `compassGridEnabled 0` remove them? Does turning rotate the square while the
  arrow stays up? Absence of the `minimap_corner` error line.
- **C/D:** the TTY/log lines `EXE_CONFIGSTRINGMISMATCH` / "An error occured while connecting",
  `G_FindConfigstringIndex: overflow`, "Aborting level - gametype is not supported", "Not
  enough domination flags", "Map errors", "No sd_bomb_pickup_trig trigger found in map.", "No
  sd_bomb script_model found", "getent used with more than one entity", "exitlevel already
  called", "potential infinite loop in script". Tests that narrow it: in SD, can each
  splitscreen player see the other (if yes, drawing is fine and the objects were never spawned)?
  Do Sabotage, Capture the Flag and Headquarters show any objective? Does Domination throw an
  error box or fall back to Free-for-All after a while?

## Sources

Zone bytes and commands above; scratch `<scratch>/diag/A/` (`grid.py`, `mats.py`,
`f749668.txt`), `<scratch>/diag/B/dis.txt`, `<scratch>/diag/CD/` (`dump.py`, `sim.py`,
`box.ents`, `nuked.ents`, `cs/`); docs/research/box-lighting.md, docs/research/box-map.md,
docs/convert.md, docs/demo-box-lit.md, docs/demo-box-modes.md; t5mp.elf at 0x749668, 0x749a78,
0xe0570, 0xdbfc4..0xdc018, 0xdb8dc, 0x90d710, 0x4c7bf0; cod2rad.exe 0x4333f0; the PC tools'
`bin/codbo.def`; the extracted scripts `maps/mp/gametypes/{sd,dom,_gameobjects,_hud}.gsc` and
`maps/mp/_compass.gsc`; OpenAssetTools src/Common/Game/T5/T5_Assets.h (GfxLightGrid and
neighbours).
