# How multiplayer finds, offers and loads a map

Status: answers the question "can a converted map have its own name, and can the game offer
it without changing a stock file?". Short answer: the map's own zone works under its own name
(every name the game derives from the map name is found, section 3), and the server loads a
map name it does not know without complaint (section 2.3); but the menus offer only the maps
listed in `mp/mapstable.csv`, which lives in stock zones, and no route was found that issues
`map <name>` or lists a map without changing a stock file (section 4). The minimum stock
change is one table row in patch_mp.ff (section 5), which OpenT5 writes only on request.

Conventions. ELF addresses are virtual addresses in `t5mp.elf` (file offset = address -
0x10000); the disassembly is `powerpc64-linux-gnu-objdump` output. "G" is the UI global at
0x1874af8. Zone offsets are into the inflated zone. Disc folder `PS3_GAME/USRDIR/english`,
update folder `dev_hdd0/game/BLES01031/USRDIR/english`, DLC folders `dlc1..dlc5/english`.

## 1. The map table, `mp/mapstable.csv`

Present in code_post_gfx_mp (disc, string at zone 0x37ae53) and in patch_mp (update, asset
44, 28 rows x 16 columns). patch_mp's copy is the one in use: it lists the DLC maps, the disc
copy does not (code_post_gfx_mp 0x37b688..0x37b8fa: 14 `menu_*_map_select_final` names).

Rows: a header row (`a0 .. k10`), `maxnum_map,26`, then one row per map. Columns and their
readers:

| Col | mp_nuked | Meaning | Read by |
|---|---|---|---|
| 0 | `mp_nuked` | map name (the key) | 0x4166f0 copies it, 24-byte field (`li r5,24` at 0x416ae4); `dvar_defaults.cfg`; `_teams.gsc` |
| 1, 2 | `urbanspecops` | allies / axis team set | 0xf9888 (UI, by `ui_mapname`); common_mp `maps/mp/gametypes/_teams.gsc` lines 299, 309 |
| 3 | `MPUI_NUKED` | localize key of the name; `<key>_CAPS` is built from it | 0x416af4 (copy, 32 bytes), 0x416b44 (`%s%s` with `_CAPS`); 0x436650, 0x43b298 (translated); ui_mp / patch_ui_mp menus (`GetMapIndexByName(map_selection, tableLookup(..., 3))`) |
| 4 | `menu_mp_nuked_map_select_final` | map select image | INFERRED: menus (0x43b2b8 reads column 4) |
| 5 | `9` | index 0..maxnum-1 | 0x416a9c (entry i is the row whose column 5 is `"%i"` of i); patch_mp `dvar_defaults.cfg` (clears `ui_mapname` when empty); 0x287328 (telemetry `mapID`) |
| 6 | `MPUI_DESC_MAP_NUKED` | description key | INFERRED: menus |
| 7 | `compass_overlay_map_nuked` | combat record heat map | 0x439748 (by index, `heatmap_default` when missing) |
| 8, 9 | `SMALL`, `NO` | size, unknown flag | no reader found (INFERRED unused by code) |
| 10 | `YES` | offered in splitscreen | 0x416b6c (compared with `YES`); ui_mp menu (`ui_is_splitscreen_map NO` resets the map to mp_nuked) |
| 11 | `0` | map pack: 0 base game, 2..4 DLC | 0x416aa4 (stored at entry +108); patch_ui_mp menus |
| 12..15 | `MPUI_SPECOPS_SHORT`, `MPUI_RUSSIAN_SHORT`, `ops`, `spets` | team short names, faction | 0x6610f0, 0x66112c, 0x6611c8 (by index) |

The UI map list (0x4166f0, called from 0x43177c at UI start and 0x55b678 after content is
mounted): `memset(G+19392, 0, 0x3800)` (128 entries of 112 bytes), `maxnum_map` from the
table (0x416798), then per entry name (+32, 24 bytes), key (+0, 32), caps key (+56, 32),
splitscreen flag (+88), pack (+108); `ui_mapCount` = maxnum (0x416ba4). The count of
playable entries skips a pack whose content `dlc1..dlc5` is not mounted (0x416978..0x416a5c).
The feeder (0x40dd80) lists, with `ui_showDLCMaps` off, the entries of pack 0, with it on the
DLC entries whose pack is mounted. `mappack_count` (0x4167c0) is absent from patch_mp's
table, so that loop does nothing.

Other tables: `mp/mappackofferids.csv` (store offer ids per language) and
`mp/storeimagefileidsmapping.csv` (store images) concern buying packs, not the map list.

## 2. Loading a map by name

### 2.1 The level table in the executable

0xb420e0: 80 entries of 16 bytes {name, mode mask, content mask, presence string} plus a
terminator: the 14 disc MP maps (mode 1, content 2), SP and zombie maps, and every DLC map and
its `_load` zone (content 4, 8, 0x10, 0x20, 0x40 for dlc1..dlc5). Lookups: 0x68b410 (index by
name, -1 when absent), 0x68b140 (content mask by index; **2 for -1**, 0x68b154..0x68b15c).

### 2.2 Zones and folders

- Com_LoadLevelFastFiles 0x3a4c00: loads `<map>_load` first only when 0x68bd60 says the map's
  content mask is not 1 or 2 (DLC maps; 0x3a4c88, `%s_load` at 0x3a4f98); then `<map>`
  (0x3a4e04 stores the name, DB_LoadXAssets 0x2615c0). Disc maps have no `_load` zone (disc
  folder listing); DLC `_load` zones hold only pack indices and load screen materials
  (`dlc2/english/mp_kowloon_load.ff`: packindex img_dlc2, snd_dlc2, five `loadscreen_*`
  materials, rawfile `mp_kowloon_load`).
- Folder: 0x55bdf8 strips `_load` / `_patch` (0x55be10, 0x55be54), looks the name up in the
  level table, and returns `""` (0x92a948) when the name is absent or its content mask is 2
  (0x55be88, 0x55be98), else `/dlc/<pack>`. The zone path is `%s/%s%s%s` (folder, base, name,
  `.ff`, 0x261014..0x261068); the pak `<name>.pak` the same way (0x260e70). So an unknown map
  name resolves exactly like mp_nuked: the same folder, the disc `english` folder in this
  setup.
- A missing zone that is not `_load`, `_patch` or `default*` raises `EXE_CANNOT_FIND_ZONE`
  (0x2610f4..0x261104).

### 2.3 The server accepts an unknown map

SV_SpawnServer path 0x1a6a7c: index (0x68b410) -> content mask (0x68b140; 2 for an unknown
name) -> 0x55ab98, which returns 1 at once for mask 2 (0x55ab98..0x55ac20); only a 0 result
raises `PLATFORM_MISSINGMAP` (0x1a6c70). Then `maps/mp/%s.d3dbsp` (0x1a6ad4 -> 0x4b5368) and
Com_LoadLevelFastFiles (0x1a6c90). Nothing in this path consults the map table.

## 3. Every place the map name appears in a map zone

Stock mp_nuked (529 assets): 292 occurrences of `mp_nuked` in the inflated zone. Renamed by
the converter (the game finds them by the map name):

| What | Stock name (asset index) | Who finds it |
|---|---|---|
| Container header zone name (0x1c, 32 bytes) | `mp_nuked` | the file name the loader asks for; the nonce is derived from it |
| com_map, gfx_map, game_map_mp, col_map_mp, map_ents | `maps/mp/mp_nuked.d3dbsp` (367, 405, 406, 407; com_map owns the string, the others point at it: fields 0xfcd0bc, 0x1e6b6e3, 0x1e82252, 0x20af8ec) | 0x4b5370 `maps/mp/%s.d3dbsp` |
| gfx_map base name | `mp_nuked` (405; rawfile 528 `mp_nuked` is named by a pointer to it, field 0x304b23c) | exposure lookup strips to the base name (0x1860f8) |
| level script and its includes | `maps/mp/mp_nuked.gsc`, `_amb`, `_fx`, `_platform`, `createfx/..._fx`, `createart/..._art` (427..432) | 0x334d9c: `maps/mp/%s` with the `mapname` dvar |
| client scripts | `clientscripts/mp/mp_nuked.csc`, `_amb`, `_amb_platform`, `_fx`, `createfx/..._fx` (433..437) | client script start by map name (INFERRED, same pattern) |
| configstring tables | `mp/configstrings/configstrings_ps3_mp_nuked_<gt>.csv` x 12 (0..11) | 0x476708: `%s/configStrings/configStrings_ps3_%s_%s.csv` with `mp`, map, gametype |
| sun and exposure | `sun/mp_nuked.sun` (438), `exposure/mp_nuked.xpo` (439) | 0x186190 `exposure/%s.xpo` (`exposure/default.xpo` when missing, 0x186208); sun file INFERRED |
| script path references inside the scripts | `maps\mp\mp_nuked_fx::main()` etc. | the script compiler resolves them by file name |

Kept (they name assets that keep their names): xmodels `mp_nuked_*` and `dest_mp_nuked_*`,
materials and images `mc/mtl_mp_nuked_*`, effects `maps/mp_maps/fx_mp_nuked_*`, destructible
definitions, sound file paths in `mpl_nuked.all`, the sky model `skybox_mp_nuked`, the
configstring cells listing them, the entity string's model names, the script strings,
`setupMiniMap("compass_map_mp_nuked")` (material in code_post_gfx_mp) and
`VisionSetNaked("mp_nuked")` in the art script (`vision/mp_nuked.vision` is in common_mp).

Found outside the zone, by map name, with a fallback: `loadscreen_<map>` (0x448de0;
Material_IsDefault 0x448d9c -> no load screen image, no error), `vision/<map>.vision`
(0x184c98; `vision/default.vision` at 0x18511c; common_mp has it). The stats DDL enum
`maps_e` (patch_mp `ddl_mp/stats.ddl`, zone 0x7bbdb) lists map names, but no script or
executable string uses `mapstats` (searched in every GSC of patch_mp and common_mp and the
ELF strings): INFERRED harmless.

Name limits: 23 characters (map table 24-byte field, 0x416ae4); zone header 31; not ending in
`_load` / `_patch` (stripped by 0x55bdf8); lower case (`level.script = toLower(mapname)`,
patch_mp `_globallogic.gsc` line 29); not a name in the level table or the map table.

## 4. Routes that would need no stock change: none found

| Route | Finding |
|---|---|
| (a) DLC-style registration | The map list has one source, the `mp/mapsTable.csv` asset (0x416708); DLC zones carry no table (the `_load` zone contents above) and every DLC map already has its row in patch_mp's table (pack 2..4) and its entry in the executable's level table. DLC content is recognised by fixed folder names `dlc1..dlc5` (0x416978..0x416a34) and `%s.edat` licences (0x55b3dc). No other table source was found. |
| (b) Search paths / mods | `fs_game` is only read into the server info string (0x3ee180); no mod or user-map folder string exists in the ELF; zone folders come from 0x55bdf8 (fixed table) and the runtime base set at 0x55e2c8. INFERRED: none. |
| (c) Console or cfg | `toggleconsole` is bound in patch_mp's `default_mp.cfg`, but the executable holds no `toggleconsole` string (no such command) and no keyboard driver strings. Startup execs `default_mp.cfg`, `language.cfg`, then a config named by an argument that is 0 at every call (0x3a227c..0x3a22c4), then `safemode_mp.cfg` (0x3a11d8): all rawfiles in stock zones. The FFOTD zone `ffotd_tu13_mp` is loaded only after an online fetch (0x6bc420 -> 0x6b1138; load at 0x6bd170), so not offline. |
| (d) `map <name>` directly | Works for an unknown name (2.3), but nothing issues it: the lobby starts `map %s 1 %i` with `ui_mapname` (0x1fd1e4, 0x219eb4), and `dvar_defaults.cfg` (patch_mp, exec'd by the private match menu) clears `ui_mapname` when the name has no table row. |

Conclusion: the map's own files (`<name>.ff`, `<name>.pak`) need no stock change to load;
offering the map in a menu needs the map table row, which only a stock zone can carry.

## 5. The minimum stock change

One row in patch_mp's `mp/mapstable.csv` and `maxnum_map` + 1: name, team sets, index =
old maxnum, pack 0, splitscreen `YES` (OpenT5 copies the base map's row). Optional: the shown
name. A zone cannot gain a localize asset through the editing layer, so OpenT5 puts the title
into one of four localize entries patch_mp carries for cut maps and no row or menu uses
(`MPUI_WARMUSEUM`, `MPUI_SNOWMINE`, `MPUI_SALVAGE`, `MPUI_FIREBASE`, each with `_CAPS` and
`MPUI_DESC_MAP_*`; the same keys exist in patch.ff, and which copy wins is INFERRED).
Several custom maps stack in the same patch_mp: give `--patch-mp` the already edited file;
a name already in the table is updated in place. `opent5 convert --name ... --register`
writes that copy; the default writes nothing but the map's own files.

What would confirm the open points: a console run of the registered map (the map appears in
the private match and splitscreen list, the title shows), and of the unregistered map started
any other way.
