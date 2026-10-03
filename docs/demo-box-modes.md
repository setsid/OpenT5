# Box map demo: every gametype in one converted zone

The box room of docs/demo-box.md, built again with `tools/testmap.py` so that it also carries
the objectives of every MP gametype: bomb sites, bombs, flags, Domination points and
Headquarters radios. Converted into the disc's `mp_nuked.ff` (Nuketown's world replaced, its
name, menus and gametype tables kept). Checked offline only (docs/convert.md 9.5); this is
the first run on a console client. What each gametype needs and why: docs/convert.md 9.

## Files

| File | sha1 | Bytes | What |
|---|---|---|---|
| `~/opent5/out/demo/i_box_modes/mp_nuked.ff` | `146312a3dda18b3707aa1a986a471d19590b1be8` | 23 830 624 | the demo (default lighting) |
| disc `mp_nuked.ff` (retail) | `6d5a4a0e5c031c0d8fcd555413730b014e9c0c5d` | 36 349 760 | the file to back up and restore |

The folder also holds `convert.json` (every conversion decision), `oracle.json` (emulated
loader: consumed exactly, 53 195 pointers as the product reads them), `verify.json` and
`gametypes.json` (`entities.report` on the converted zone: all twelve gametypes ready).

Made with (PC zone sha1 `d8d34f5175be6be5eabd4b685b6171b735cd200b`):

    .venv/bin/python tools/testmap.py build mp_opent5box_modes --game "<PC game folder>" \
        --work "C:\o5\modes" -o <scratch>/mp_opent5box_modes.ff
    opent5 convert "<PC game folder>/zone/English/mp_opent5box_modes.ff" --base mp_nuked \
        -o out/demo/i_box_modes/

## Install

As docs/demo-box.md: back up the retail `mp_nuked.ff` (check its sha1), copy
`out/demo/i_box_modes/mp_nuked.ff` over it, leave `mp_nuked.pak` and the update's
`patch_mp.ff` as they are, use the signature-patched multiplayer client. To restore, copy
the backup back and check the sha1.

## The room

1024 x 1024 units, 256 high. Allies (and the attackers in Search and Destroy, Demolition)
start on the west side, axis (and the defenders) on the east side, facing each other. In
every mode only that mode's objects are in the room: the game deletes the others at the
start (each object is tagged with its mode). Positions, x to the east and y to the north,
room centre (0, 0), walls at -512 and 512:

| Where (x, y) | What, by mode |
|---|---|
| (288, -320), (288, 320) | bomb sites A (south-east) and B (north-east): Search and Destroy, Demolition |
| (-288, 0) | the bomb: Search and Destroy; flag A: Domination; allies' target: Sabotage |
| (0, 0) | flag B: Domination; the bomb: Sabotage |
| (288, 0) | flag C: Domination; axis' target: Sabotage |
| (-448, 0), (448, 0) | allies' and axis' flags: Capture the Flag |
| (0, -320), (0, 320) | the two Headquarters places |
| x = -384 and 384 | start points (west allies / attackers, east axis / defenders) |
| x = -448 and 448, near the corners | team respawns |

## Per mode: what to start, what you should see

Use a Splitscreen, System Link or Private Match game on Nuketown (Combat Training offers
only some modes). Bots have no path nodes in the box and will probably stand still
(INFERRED); a second splitscreen player is the better opponent.

| Mode (menu name) | What you should see |
|---|---|
| Team Deathmatch | spawns at the west (allies) or east (axis) wall; no objectives in the room |
| Free-for-All, One in the Chamber, Gun Game, Sharpshooter, Sticks and Stones | spawns at the six neutral points (corners of a square around the centre and the middle of the north and south walls); no objectives |
| Search and Destroy | attackers start west, the bomb (a suitcase) lies on the floor at the west-centre (-288, 0) with a bomb icon for the attackers; walk over it to pick it up. Two bomb stacks against the east side, A at the south-east (288, -320), B at the north-east (288, 320), marked A and B on the HUD and compass. Hold use at a site with the bomb to plant (the planting bar runs); defenders hold use on a planted bomb to defuse. One life per round |
| Demolition | as Search and Destroy without the suitcase: any attacker can plant at A or B; sites with A / B markers; respawns on |
| Domination | three flags in a line across the middle, A west (-288, 0), B centre, C east (288, 0), neutral flag models with A / B / C markers; stand inside a flag's circle (radius 96) to capture it, the capture bar runs and the flag turns to your team |
| Capture the Flag | a flag on a base just in front of each team's wall: allies at (-448, 0), axis at (448, 0); walk into the enemy flag to take it, carry it to your own base while your flag is home to score |
| Sabotage | one bomb in the centre of the room (0, 0); each team's target (a bomb stack) on its own side: allies west (-288, 0), axis east (288, 0). Pick up the bomb, plant it at the enemy's target by holding use; defuse by holding use |
| Headquarters | about five seconds after the start "HQ revealed": a crate with a radio appears at one of two places, (0, -320) south or (0, 320) north, with a marker; it can be captured when the "HQ available in" timer ends (45 s by default); stand next to it to capture; when it is destroyed or times out it moves |

## What a failure looks like, and what it would mean

The rows of docs/demo-box.md still apply to loading, drawing and collision. For the modes:

| You see | Most likely meaning |
|---|---|
| The match ends at once and the game switches to Free-for-All, or returns to the lobby, in one mode only | that mode aborted the level (AbortLevel: a spawn class or its objectives were not found); note the mode. The converted zone's entity string lists them (`gametypes.json`), so the likely cause is an entity the game did not spawn (a classname or key it does not accept) |
| Search and Destroy without a bomb or without sites; Capture the Flag without flags; Sabotage without the bomb | the gametype printed "No ... found in map" and runs on without its objective: the trigger or model named was not spawned. A missing brush trigger (bomb site, flag, pickup) points at its brush model (`model "*N"`), see the next row |
| An error box mentioning a model number, an inline or brush model, or a crash when the level loads | the brush triggers' collision models (clipMap cmodels 1..14) are not what the game expects. They were converted like every other clipMap array (identical on Nuketown, docs/convert.md 4) and the loader consumed them exactly, so note the exact text |
| Sites, flags or the HQ are where described but planting, capturing or picking up never starts | the trigger is there but you are not touching it: its bounds (cmodel mins and maxs) are wrong or its origin is; note how close you were |
| A script error naming `maps/mp/gametypes/<mode>.gsc` (for example "undefined is not an entity", "getent used with more than one entity") | an objective chain is not as the script expects; note the file, line and function |
| Bomb stacks, suitcase, flags or crates invisible but their markers visible | the models are not loaded: they come from the base zone (mp_nuked) and common_mp, not from the PC zone; note which ones |
| Objects of another mode in the room (for example flags in Search and Destroy) | the gametype filter did not delete them: the `script_gameobjectname` keys were not read |
| Domination aborts with "Map errors" in the log | the flag descriptors (three script_origins 64 above the flags, linked A-B-C) were not matched to the flags |
| Headquarters aborts with "Map errors" | a radio is not inside exactly one radio trigger in the game's own test (`istouching`); note it |

Please report, per mode: started or not, which row matches (or none), a screenshot showing an
objective, and any RPCS3 log lines that mention script, DB or entity errors.

## Files created outside scratch

PC game folder (new files only, all named `mp_opent5box_modes`; delete them to remove):

```
raw/maps/mp/mp_opent5box_modes.d3dbsp
raw/maps/mp/mp_opent5box_modes.d3dpoly
raw/maps/mp/mp_opent5box_modes.d3dprt
raw/maps/mp/mp_opent5box_modes.radtrans
zone/English/mp_opent5box_modes.ff
zone_source/mp_opent5box_modes.csv
zone_source/english/assetinfo/mp_opent5box_modes.csv
zone_source/english/assetinfo/mp_opent5box_modes_dep.txt
zone_source/english/assetinfo/mp_opent5box_modes_xmodel.csv
zone_source/english/assetlist/mp_opent5box_modes.csv
```

Work folder `C:\o5\modes\`: `mp_opent5box_modes.map` and the three `.bat` files
(`_cod2map`, `_cod2rad`, `_linker`).

## Result in RPCS3 (2026-10-03, local splitscreen)

- Search and Destroy: the round starts ("DESTROY TARGET A OR B", the round timer runs) but no
  A/B icons appear on the HUD or minimap, and there are no bomb site models and no bomb.
- Domination: hangs on the load screen at "Awaiting challenge...0"; the local server never
  finishes starting the map.
