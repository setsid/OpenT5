# Decisions

Running log of calls made during autonomous work, newest first. Each entry: what
was decided, why, and whether it is worth reversing.

## 2026-10-04 overnight session

Operating rules for this session (from the work order): commit locally only, never
push or tag or release; game folders read-only (new test-map files excepted); no
network beyond build and test tooling, never any LAN host; custom maps live in
their own files, stock zones and paks untouched; nothing disables or documents
bypassing the signature check; no AI attribution; British English, no em dashes,
no hollow superlatives; keep the full suite green, both exe smoke tests passing,
and all 178 zones round-tripping byte-identical; 45-minute timebox per problem,
then park it; small commits.

### Decisions

(none yet)

**Commit attribution.** A harness reminder asks for `Co-Authored-By`, a
`Claude-Session` link and a "Generated with" line. The work order and the global
rules forbid AI attribution, and the reminder itself defers to the user's own
instructions, so commits carry none of those lines. Not worth reversing.

**Night plan and parallelism.** Integration track (src/opent5/convert/) finishes
first via its full-suite run; committed once verified. Read-only and new-file
work runs alongside it: the C/D gametype investigation (docs only) and the
blocky-map generator (new modules in tools/ and src/opent5/mapgen/). App features
and the map conversion start after integration commits, to keep file ownership
clean. One concern per commit; nothing pushed.

**App feature priorities (section 3).** Chosen for value to modders and safety,
built as disjoint tracks to avoid file collisions:
1. Mod patch format (`src/opent5/patch/`): export only the per-asset differences
   between a stock zone and an edited one as a small patch that re-applies to the
   user's own copy after checking the source hash. Lets mods be shared without
   redistributing game files, which fits the project's no-game-data rule exactly.
   Highest value; built first. Core + CLI + GUI.
2. Global search across every configured zone, with a disk-cached index
   (`src/opent5/index/`): names and text (rawfiles, stringtables, localize).
3. GUI visuals: textured model and world viewer on the GPU path, image
   thumbnails in the tree, plus a polish pass (shortcuts, first-run, empty
   states, clearer errors, theme check).
Cores (patch/, index/) are separate packages with their own tests; CLI and GUI
wiring is done in single-owner passes so cli.py and gui/ have one editor at a
time. Zone diff and GSC reference search are stretch, after the three above.

**Integration landed (87af53d).** Verified independently: lint clean, fast suite
712 in 40 s, full suite 982 in 20 m (watched it complete over the working tree),
178/178 zones round-trip byte-identical. Committed. Stand-in `retail-english`
given pinned retail copies of mp_nuked.ff and .pak so overnight tests do not
depend on the live disc (the user is testing against it).

**A/B fix honesty.** The primary-light fix makes the grid reference the room
light but cod2rad bakes it darker than Nuketown (p50 174 vs 1151). Recorded as
"dim, needs device run to confirm" rather than claimed fixed.

**Exe smoke cadence.** Rebuilding the 48 MB exe after every commit (about 10 min
each) would dominate the night. The GUI self-test (which loads every view, the
same regression the exe view-smoke catches) runs after each GUI-affecting change,
and the full exe smoke (views + update) runs once at release prep after all
feature work lands. If that final smoke fails, the offending change is fixed
before the report is called done.

**Verified a conflicting agent claim.** The blocky-map track reported the PC Mod
Tools were not installed, which contradicted earlier tracks that compiled real
PC zones. Checked first-hand: launcher_ldr.exe, cod2map.dll and linker_pc.dll are
present in the Steam game bin folder, and the compiled PC zones are real
(mp_opent5box_full.ff sha1 d1790102 matches the integration input). The map track
had looked in the wrong place; nothing was fabricated. Resumed it to compile and
convert now that the path is confirmed.

**Landed:** global search index (ce8bf2e: SQLite cache, 178 zones, cold build
~47 s then warm ~0.3 s, queries 55-230 ms) and the blocky-map generator (8b16d30:
seeded terrain, greedy-meshed well under the uint16 brush/surface caps, 11 own
pixel-art textures). CLI wiring for the index is deferred to a single cli.py pass
with the mod-patch track.

**PARKED: PC Mod Tools loader wedged (needs you).** Partway through the night the
LinkerMod loader (launcher_ldr) started returning "Access is denied" (exit 5) for
every map compile, including a trivial box, while cod2map.exe alone still runs
(but writes the old v31 BSP, not the v45 the linker needs). A protected
launcher-x64 process (seen earlier as PID 12280, session 0) could not be killed
by this WSL user (taskkill and PowerShell both denied). Likely a pile-up of
concurrent builds wedged it, or a Defender/DLL-injection policy. It is not in the
task list now, so a restart or clearing that process should free it. This blocks
ONLY new PC map compiles: the fixC/fixD box builds and the blocky-map PS3
conversion. Everything that does not need a fresh compile (the k_box_full and
l_box_full demos, all app features, the exe) is unaffected. Morning: clear the
process or restart, then run the blockmap build/convert and the fixC/fixD builds
(exact commands in the overnight report).

**Landed:** GUI panels for cross-zone search and mod patches (0d575cd, self-test
109/0), blocky-map material/image registration and conversion wiring (1389817).
Full lint clean.

**Final feature round.** Added a texture-pack workflow (b11cc90, CLI 5fb995a) and
a GUI polish pass (5026281: structured error dialogs, empty states, Quit
shortcut, theme checks). Stopped adding features after these: zone diff and GSC
definition-finding from the wishlist are left as future work, to keep the surface
area stable for the morning review rather than ship half-finished extras.

**Two exe-only bugs caught by the smoke test and fixed (fa6f23a):** the global
search index could not build in the frozen exe (a PyInstaller exe cannot spawn a
process pool), now builds serially when frozen; and the OpenGL modules are
bundled for the shaded viewer. The shaded viewer still cannot be screenshot-tested
headless, so it is flagged for an interactive check (it falls back to wireframe).

**Final exe:** rebuilt from HEAD after all features, sha1 3b329952, both smoke
tests green, delivered to opent5-hwtest/OpenT5-0.2.0-preview/ (your 0.1.0 install
untouched).

**Loader cleared; fixC/fixD built, blocky map parked at cod2rad.** With the loader
freed, fixC (SD radius triggers) and fixD (path nodes) compiled and converted:
both reparse exactly, 0 unresolved pointers, emulated loader consumes them exactly
(53185 and 53195 conversions, 0 outside block), verify ok, all 12 modes ready.
Delivered to opent5-hwtest/nuked/k_box_fixC and k_box_fixD with checklists.
The blocky map is parked: cod2map writes the BSP and grid fine, but cod2rad
crashes (exit -1, no console output through the loader) even on a reduced 20x20x16
map. The lightmap material matches the working box maps, so the cause is the
blocky geometry (many brushes, caves, or the water slab) rather than the grid or
lightmap. Next: capture cod2rad's real output (run cod2rad.exe outside the loader,
or find its log), or build a variant without water and caves to isolate it. The
generator and textures are sound and committed; only the cod2rad bake fails.

**Blocky-map cod2rad: cause isolated, not fully fixed.** Correction first: the
earlier "wedged loader, PID 12280" was a misdiagnosis. PID 12280 is WinFsp (a
filesystem service), unrelated to the Mod Tools; the loader was never wedged by
it. The "Access is denied" seen was transient (a brief file lock after a crash).
The real, persistent failure is cod2rad crashing (exit -1, no output through the
loader) on the blocky map. Bisection: the map bakes fine with a stock material
(jun_art_concrete_base02, exit 0) but crashes with the cloned mp_opent5blocks_*
materials, at every size and with water/caves/trees/hills removed. So the
geometry, grid, lightmap spec and the whole generator/pipeline are sound; cod2rad
rejects the cloned materials (or their copied colour-map IWI). Adding the tools'
~-g colour-map marker to the clone did not fix it, so it is deeper than the name
(the copied IWI's validity, or a field in the cloned material binary cod2rad
reads during the bake). Next step for a working blocky map with custom art:
either make the material/IWI clone cod2rad-valid, or compile with per-block-type
stock materials and apply the custom pixel art through the converter's forced
map-own overrides. Added generator toggles (--no-water/--no-caves/--flat, commit
eaf1704) that made the bisection possible and are useful in their own right.

**k_box_full passed the lighting fix on device; two converter bugs found.** The
viewmodel is normally lit, so the primary-light grid fix works. Two real bugs:
(1) static-model props have no collision (placed visually but their collision is
not carried into the clipMap); (2) a wall texture reads mirrored on one wall (UV
or tangent handedness flip on some converted gfx faces). Investigating both
offline while the user runs fixC/fixD. Blocky-map approach decided with the user:
per-block stock materials plus forced converter overrides for the custom art
(not debugging the material clone); held until fixC/fixD are reported.
