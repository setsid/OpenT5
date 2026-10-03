# Box map demo: a PC Mod Tools map in PS3 Nuketown

The box map built with the PC Mod Tools (`mp_opent5box`, docs/research/box-map.md section 1:
a sealed room, floor, four walls and a ceiling) converted into the disc's `mp_nuked.ff` by
`opent5 convert` (docs/convert.md). Nuketown's world is replaced; its name, menus, load
screen, textures, sounds and gametype tables stay. Checked offline only (docs/convert.md
section 5); this is the first run on a console client.

## Files

| File | sha1 | Bytes | What |
|---|---|---|---|
| `~/opent5/out/demo/e_box/mp_nuked.ff` | `d5a37f699023d9aaa06b0d1cc536ddcc39cd5bea` | 27 579 008 | the demo (lighting `flat`) |
| `~/opent5/out/demo/e_box_sunlit/mp_nuked.ff` | `b4865068fcb5e3954ca59494168ff905b23d2c1e` | 27 579 008 | optional second try, only if the first is dark (lighting `sunlit`) |
| disc `mp_nuked.ff` (retail) | `6d5a4a0e5c031c0d8fcd555413730b014e9c0c5d` | 36 349 760 | the file to back up and restore |
| disc `mp_nuked.pak` | `5930c6c683d9339d0357a7266a8d37819b95e6cf` | 172 005 376 | stays as it is |

Each folder also holds `convert.json` (every conversion decision) and `oracle.json` (the
emulated loader check: consumed exactly, 53 166 pointers as the product reads them);
`e_box` also holds `verify.json`, `selftest.json` and
`screenshots/` (the world and collision in OpenT5's geometry view).

Made with:

    opent5 convert "C:\o5\p6\mp_opent5box.ff" --base mp_nuked -o out/demo/e_box/
    opent5 convert "C:\o5\p6\mp_opent5box.ff" --base mp_nuked -o out/demo/e_box_sunlit/ --lighting sunlit

(from WSL the PC file is `/mnt/c/o5/p6/mp_opent5box.ff`, sha1 `20e4cffb...`).

## Install

1. Back up the retail file first: copy
   `<OPENT5_ZONES>\mp_nuked.ff`
   to `mp_nuked.ff.retail.bak` in the same folder (or anywhere safe). Check its sha1 is the
   retail one above.
2. Copy `out/demo/e_box/mp_nuked.ff` over that `mp_nuked.ff` (same folder, same name).
3. Leave `mp_nuked.pak` in that folder as it is. The converted zone keeps every image record
   of the stock zone (no image asset changed: `verify.json` lists the nine changed assets,
   none an image), and the game opens `<zone>.pak` from the folder of `<zone>.ff`, so the stock
   pak is the right one.
4. Leave the update's `patch_mp.ff` (dev_hdd0) as it is (retail, sha1 `499ce654...`). It
   carries its own copy of the Nuketown script; the converted zone adds the entities that
   script needs, so it runs in the box too (docs/convert.md 3.6).
5. Use the signature-patched multiplayer client, as for the earlier remap tests. The console
   signature of the converted file no longer matches; an unpatched client refuses it.

To restore: copy the backup back over `mp_nuked.ff` and check the sha1.

## Run

Multiplayer, offline: Combat Training, Team Deathmatch, map Nuketown, with as few bots as the
menu allows. Free-for-all works too. If Combat Training is not available offline in this setup,
use a Splitscreen, System Link or Private Match game of Team Deathmatch or Free-for-all on
Nuketown with one player. Only these two modes (and the wager modes One in the Chamber, Gun
Game, Sharpshooter, Sticks and Stones, which use the free-for-all spawns) have their spawns in
the box; Search and Destroy, Domination, Capture the Flag, Demolition, Sabotage and
Headquarters need objective entities the box does not have and abort or start without
objectives.

## What you should see

- The Nuketown load screen and map name (unchanged), then you spawn standing on the floor of a
  closed room, 1024 x 1024 units (about 26 m square) and 256 units (about 6.5 m) high, not in
  Nuketown.
- Floor: grey concrete (`jun_art_concrete_base02`). Four walls: white vinyl siding
  (`us_art_wall_vinylsiding_white`), 4 x 1 repeats of the 256-unit texture per wall. Ceiling:
  cream paint (`pent_art_wall_creampaint02`).
- Even lighting: each surface lit uniformly, taken from a brightly lit patch of the same
  material in Nuketown's own lightmap. No baked shadows, no sky (the room is sealed).
- You can walk, sprint, jump and crouch anywhere in the room; walls and ceiling stop you; you do
  not fall through the floor. Bullets leave impacts on walls and floor.
- Team Deathmatch: you spawn at one end of the room facing the middle (three start points per
  team at x = -384 and x = +384); bots, if any, spawn in the room too and probably stand still
  (the box has no path nodes for them to walk on; INFERRED).
- No houses, cars, glass, mannequins or street props. The compass shows no map or Nuketown's
  map image; it is not meaningful here. Nuketown's ambient sound may still play.
- At the end of the match the end-game camera sits inside the room looking along +Y; with the
  update's Nuketown script the nuke sound plays and its explosion effect goes off above the roof
  (you may hear it but should not see it).

## What a failure looks like, and what it would mean

| You see | Most likely meaning |
|---|---|
| The game refuses the map or returns to the menu at once, maybe with a "fastfile" / signature message | the client is not the signature-patched one, or the file was not copied completely (check the sha1) |
| An error box while loading naming an asset, e.g. "Could not load ..." / "missing asset" | an asset the converted zone refers to is not found: note the name; the zone's structure itself was consumed exactly by the game's own loader offline |
| A script error naming `maps/mp/mp_nuked.gsc` (compile error or "undefined is not an entity") | a script path not covered: note the file, line and function; with the update's script it would point at an entity the box still lacks |
| "No mp_tdm_spawn spawnpoints" or the level ending right after loading | a gametype other than Team Deathmatch / Free-for-all was chosen |
| Crash or freeze at the first frame after loading (RPCS3 RSX error in its log) | the converted draw data (vertex groups, layer data, surfaces) is not what the PS3 renderer expects; the layout was proven against PS3 Nuketown (docs/convert.md 4), so note the RPCS3 log lines |
| Black screen with the HUD and weapon visible | the room is drawn black: the lightmap combination is not what the flat lighting assumed; try `e_box_sunlit`; if impacts show on black walls, it is lighting, not geometry |
| Walls stop you but you see through them (sky, void or smeared images) | collision is right, the drawn surfaces are not: surfaces or materials |
| Walls drawn with wrong or garbled textures | the material alias or the uv in the layer data is wrong |
| You fall through the floor or spawn falling | the collision (clipMap) or the spawn heights are wrong |
| Floating glass, cars or houses | stock world data survived somewhere (not expected: those assets were replaced or emptied) |
| Dark but visible walls | the flat lighting works but the patch chosen is dim in-game; `e_box_sunlit` is the other choice |

Please report which row matches (or none), the mode used, a screenshot from inside the room,
and any RPCS3 log lines that mention fastfile, script, DB or RSX errors.
