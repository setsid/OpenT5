# Box map: Search and Destroy objectives (C) and the Domination load hang (D)

Status: diagnosis, offline. Continues docs/research/box-rpcs3-issues.md section 4 for the two
gametype faults seen on the signature-patched splitscreen client running
`out/demo/i_box_modes/mp_nuked.ff` (demo-box-modes.md):

- **C.** Search and Destroy: the round starts ("DESTROY TARGET A OR B", timer runs) but there
  are no A/B icons on HUD or compass, no bomb-site models and no bomb.
- **D.** Domination: hangs on the load screen at "Awaiting challenge...0"; the local server
  never finishes starting the map.

READ-ONLY diagnosis: no `src/` change was made. Scripts were extracted with
`opent5 extract {patch_mp,common_mp,code_post_gfx_mp,mp_nuked} OUT --type rawfile`; line numbers
are into those rawfiles. The game runs patch_mp's copies of the gametype scripts (convert.md 9,
the update's patch_mp wins by load rank). ELF addresses are vaddr into the decrypted t5mp.elf
(vaddr = ELF file offset + 0x10000). Scratch: `<scratch>/cd-track/`.

## 1. Summary

| # | Finding | Confidence | Fix sits in | Proven offline |
|---|---|---|---|---|
| CS | The stock per-map configstring tables are compatible with the box; the mismatch error is an ERR_DROP, not a hang, and did not fire (SD ran) | high | n/a (hypothesis withdrawn) | ELF; SD runs as control |
| C1 | SD's `bombs()` hits `error()` and returns because a brush-model objective trigger/model did not spawn -> no objects, no icons, round still runs | medium-high | `tools/testmap.py` (radius triggers) or `convert/` brush-model path | script path read; entity types read |
| C2 | Objects spawn but icons/models do not draw (client registration) | low-medium | `convert/` | downgraded by CS |
| D1 | DOM (and every teambased mode except SD) calls `updateAllSpawnPoints()` synchronously at load; SD does not. The stall is in the spawn / influencer subsystem exercised by that path on the box | medium | `tools/testmap.py` / `convert/world.py` (path nodes) | caller list read; SD is the control |
| D2 | A blocking engine builtin in DOM's synchronous flag setup (addsphereinfluencer / spawn scoring) | medium | as D1 | path read; needs callstack |

The honest position: C1 and D1/D2 are the best offline-derived candidates, but a device log or
PC sample is needed to confirm which, because both symptoms are produced by engine builtins whose
PS3 behaviour on the box's sparse world cannot be executed here.

## 2. The configstring-mismatch hypothesis is withdrawn (task 2)

The path string `%s/configStrings/configStrings_ps3_%s_%s.csv` is at vaddr 0x91d710. The table is
a `stringtable` asset per map and gametype (mp_nuked assets 0..11,
`mp/configstrings/configstrings_ps3_mp_nuked_<gt>.csv`). Extracted, it is an index,value snapshot
of the whole server configstring array: dvar names (index 23+), scene vectors, strings, and at
**fixed** indices the objective assets - models at 1181 (`mp_flag_neutral`), 1265/1266
(`mp_flag_allies_2`, `mp_flag_axis_1`), fx at 1598 (`misc/fx_ui_flagbase_gold_t5`), and the
HUD/compass shaders at 2081..2116 (`compass_waypoint_*`, `waypoint_*`). Both the dom and sd tables
have the same maximum index (2680); the array layout is fixed, the gametypes differ only in which
rows are populated (dom 1297 rows, sd 1282).

How the table is used (ELF): the loader at 0x4766d8 formats the path (snprintf 0x4db5b8),
parses the CSV (0x4bee30) into the global at 0x18d42b8 and stores its row count at 0x18d42bc
(reader 0x476628). The comparison block at 0x4767e8 does `cmpw` of a live value (from 0x3b0008)
against the table's stored count/entry and, on mismatch, branches to 0x3a2b38 with r3=1 and
r4 = one of EXE_CONFIGSTRINGMISMATCH / ...2 / ...3 (0x91d788 / 0x91d748 / 0x91d768). 0x3a2b38 is
Com_Error: it compares the error level against 4 (ERR_FATAL) and calls the Sys error / longjmp
path (0x6de720, 0x6deac0). So EXE_CONFIGSTRINGMISMATCH is `Com_Error(ERR_DROP=1, ...)` - a
connection drop back to the menu with a visible error, **not** a silent hang.

Consequence for the box: the box keeps the name mp_nuked, keeps the twelve stock configstring
tables unchanged (convert.md 1.1), keeps the stock xmodels/fx/materials/techsets (so every name
in the table still resolves), and runs the stock patch_mp gametype scripts (same precache set). So
the tables are compatible. The decisive evidence is the control: **Search and Destroy loaded and
ran its round**, which means SD passed this very machinery - if the box's configstrings were
mismatched, SD would have dropped with the error, not run. The prior leading hypothesis
(box-rpcs3-issues.md 4.2 item 1) is therefore withdrawn for both C and D. The `0x32067c` address
cited there is a float/dvar clamp routine, not the mismatch site.

The prior finding that the box's objective **entity string** is structurally correct and fully
consumed (box-rpcs3-issues.md 4.1) still stands and is assumed here.

## 3. C: Search and Destroy objectives do not appear

### 3.1 The round and the objectives come from different threads

The round message and timer come from the globallogic thread (`Callback_StartGameType` ->
`startGame`, _globallogic.gsc 1338, 1579). The objectives are built in `sd.gsc` `bombs()`, started
as `thread bombs()` from `onStartGameType` (sd.gsc 59). A failure in `bombs()` therefore removes
every objective while the round still runs - exactly symptom C.

### 3.2 The single script path that produces exactly C

`bombs()` (sd.gsc 430..517) begins:

```
435  trigger = getEnt( "sd_bomb_pickup_trig", "targetname" );
436  if ( !isDefined( trigger ) ) { error("No sd_bomb_pickup_trig trigger found in map."); return; }
441  visuals[0] = getEnt( "sd_bomb", "targetname" );
442  if ( !isDefined( visuals[0] ) ) { error("No sd_bomb script_model found in map."); return; }
...
466  bombZones = getEntArray( "bombzone", "targetname" );   // sites A/B
```

`maps\mp\_utility::error()` only prints (convert.md 9.1), so after an early `return` the round
continues with no bomb, no sites and no icons (the `set2DIcon`/`setCarryIcon` calls at 452..455,
and the per-site `createUseObject`+`set2DIcon` at 471+, are never reached). This is the one path
that yields C precisely.

For this to fire, `getEnt`/`getEntArray` must return undefined on the box although the entity
string carries the entities. The SD objective triggers are **brush** entities: in
`tools/testmap.py`, `sd_bomb_pickup_trig` and `sd_bomb` are `trigger_multiple` (lines 278, 292)
and the `bombzone` sites are `trigger_use_touch` (242, 266). cod2map writes each as a clipMap
submodel `"model" "*N"` (convert.md 9.3). If the box's brush-model triggers do not spawn as
entities on PS3 (a `"*N"` that the engine does not resolve at `G_SpawnEntitiesFromString`), those
names are absent and `bombs()` returns at 436/442. Domination's flags, by contrast, are
`trigger_radius` (testmap.py 388), which carry no brush model and spawn regardless - consistent
with DOM getting past flag discovery (domFlags finds its flags) and failing later.

C1 confidence medium-high: it is the only script path giving exactly C, and it depends on the one
entity class (brush-model triggers) that SD needs and DOM does not. It is unproven because the
brush triggers were shown to convert byte-identically to Nuketown and to be consumed by the
emulated loader (convert.md 4, 9.3); whether the running engine spawns them is the open point.

### 3.3 Alternative (C2, lower)

If the objects do spawn, the icons/models fail to draw on the client - a material/model
registration gap. This is downgraded because section 2 shows the box's configstring registration
is sound (SD passed it) and the box keeps the icon materials and the models
(`prop_suitcase_bomb`, `p_glo_bomb_stack` in mp_nuked; icon shaders in code_post_gfx_mp).

## 4. D: the Domination load hang

### 4.1 The hang is a server stall during SV_SpawnServer, before networking

"Awaiting challenge...N" is the loopback client waiting for the local server to answer
`getchallenge`; the single-process server cannot answer while it is still inside SV_SpawnServer
running the level script to its first yield. So the DOM gametype init must never return control to
the engine. The engine entry is `Callback_StartGameType` (_globallogic.gsc 1338), which calls
`[[level.onStartGameType]]()` synchronously (1579) and only then lets the frame complete.

### 4.2 dom.gsc's own control flow does not hang

`onStartGameType` (dom.gsc 80..132) runs `thread domFlags()` (129) and `level change_dom_spawns()`
(131) synchronously. `domFlags()` (214..311) and `flagSetup()` (741..830) contain no `wait`, so the
`thread` runs them to completion before yielding; every loop is bounded by the flag/descriptor
counts; the only exits are `AbortLevel` (fewer than two flags, or descriptor map errors), which
switches to dm rather than hanging. `createUseObject` (_gameobjects.gsc 673) only spawns think
threads whose `while(true){ self.trigger waittill("trigger") }` loops yield immediately (759, 899).
So no construct in dom.gsc blocks given the box's correct entities (replayed offline,
box-rpcs3-issues.md 4.1). The dev-only `level waittill("eternity")` at _globallogic.gsc 1467 is
gated on `r_reflectionProbeGenerate==1` and does not apply.

### 4.3 The discriminator: updateAllSpawnPoints at load

`change_dom_spawns()` (dom.gsc 920) ends with `updateAllSpawnPoints()` (dom.gsc 953), called
directly in `onStartGameType` (dom.gsc 131). `updateAllSpawnPoints` is called at load by **dm, tdm,
dom, dem, sab, ctf, koth, gun, hlnd** - every teambased MP mode **except SD** (grep of patch_mp
gametypes; SD's `onStartGameType` contains it 0 times). SD is the one mode that skips it, and SD is
the one mode that loaded while DOM hung. That is the sharpest offline discriminator between the
mode that works (C) and the mode that hangs (D).

DOM also registers the flag spawn influencers just before, `addsphereinfluencer` x9 with score
curves (dom.gsc 832..866, `createFlagSpawnInfluencers`), which the subsequent spawn scoring then
evaluates. The stall is therefore in the spawn / influencer subsystem reached by this path.
`updateAllSpawnPoints` itself (gather/clear/add/remove, _spawning.gsc 677..692) is bounded; the
blocking builtin is `addspawnpoints` / the influencer evaluation / `remove_unused_spawn_entities`,
or the C code behind `addsphereinfluencer`, operating on the box's world.

Caveat: Team Deathmatch (which also calls `updateAllSpawnPoints`) loaded on the *lighting* box
`g_box_lit` (box-rpcs3-issues.md A/B). That box has no flag influencers and a different spawn
layout, and TDM was not tested on the *modes* box `i_box_modes`. So the stall is most likely the
DOM-specific flag-influencer set rather than `updateAllSpawnPoints` generically (D2 over a generic
D1). The box has no path nodes (game_map_mp nodeCount 0, convert.md 3.6), and the spawn-influence
system may require map connectivity the box lacks; this is the most plausible engine reason and is
testable.

## 5. The most likely fix per issue (task 3)

Neither can be finalised offline; each is given as a converter change plus the device line that
confirms or refutes it.

### C (ranked)

1. **C1 - brush-model objective triggers do not spawn.** Converter-side test:
   `tools/testmap.py` build a modes box whose SD objective triggers are **radius** triggers
   (`trigger_radius_use` for the sites, a `trigger_radius` for the bomb pickup) instead of
   `trigger_use_touch`/`trigger_multiple`, so no `"*N"` brush model is required. The script path
   still works: hold-to-use is chosen when the classname contains `use` (convert.md 9.1), and
   `getEnt` resolves by targetname regardless of trigger shape. If SD then shows the bomb, sites
   and A/B icons, C1 is proven and the real converter fix is in the brush-model/cmodel spawn path
   (`convert/world.py` clipMap cmodels + GfxWorld brush models for trigger entities); if it still
   fails, move to C2. What disproves C1: the device log shows no
   "No sd_bomb_pickup_trig trigger found in map." / "No sd_bomb script_model found." line.
2. **C2 - client draw/registration.** Only if C1's radius-trigger map still shows no icons/models.
   Then compare the box's registered icon materials against the stock set; fix in `convert/`.

### D (two candidates, decided by one device line)

1. **D1/D2 - spawn-influencer / connectivity stall.** Converter-side test: build the modes box with
   **path nodes** (a minimal navmesh/path-node grid emitted into `game_map_mp`, currently 0 nodes,
   convert.md 3.6; `tools/testmap.py` + `convert/world.py`). If DOM then finishes loading, the
   spawn-influence system needed map connectivity the box lacked, and the converter fix is to emit
   that connectivity for any converted map. What confirms it on device: the RPCS3 log shows DOM
   stalling with no script error (pure engine hang) or with "potential infinite loop in script"
   (ELF 0x91ede0) naming a `_spawning`/`_gameobjects` function; and TDM on the **same**
   `i_box_modes` zone should be run to split generic (TDM also hangs -> `updateAllSpawnPoints`
   generic) from DOM-specific (TDM loads, DOM hangs -> flag influencers).
2. **D-alt - a flag-setup builtin on the radius triggers.** If path nodes do not fix it, strip the
   flag influencers' effect by placing the three DOM flags far apart with valid descriptors and
   re-test; a remaining hang points at `addsphereinfluencer` / the flag `createUseObject` on PS3,
   fixed by the same connectivity work.

The converter track should build two test zones: **fixC** (modes box, SD objectives as radius
triggers) and **fixD** (modes box with path nodes emitted into game_map_mp). Both keep the name
mp_nuked and the stock tables; neither touches patch_mp or the stock scripts.

## 6. What a device run must capture

- **C:** the TTY/log for "No sd_bomb_pickup_trig trigger found in map.",
  "No sd_bomb script_model found in map." (confirms C1), or their absence (points to C2). Whether
  Demolition (also brush `bombzone` sites) shows the same, and whether Sabotage (brush bomb) does.
- **D:** whether DOM stalls with no error (engine hang, needs a PC sample of the server thread) or
  prints a script error / "potential infinite loop in script" naming a `_spawning` function. Run
  **Team Deathmatch on `i_box_modes`**: TDM loading while DOM hangs confirms the DOM-specific
  flag-influencer path; TDM also hanging points at `updateAllSpawnPoints` generally.
- Neither is expected to print EXE_CONFIGSTRINGMISMATCH (section 2); if one does, it is an
  ERR_DROP with an error box, which contradicts the reported hang and would reopen section 2.

## Sources

Extracted scripts `maps/mp/gametypes/{sd,dom,dem,sab,koth}.gsc`, `_globallogic.gsc`,
`_gameobjects.gsc`, `_spawning.gsc` (patch_mp); `tools/testmap.py`; mp_nuked stringtable assets
0..11 (`configstrings_ps3_mp_nuked_*.csv`). t5mp.elf at 0x91d710, 0x91d748/68/88, 0x4766d8,
0x476628, 0x4767e8, 0x3a2b38, 0x18d42b8/bc, 0x91ede0. docs/research/box-rpcs3-issues.md,
docs/convert.md 9, docs/demo-box-modes.md.
