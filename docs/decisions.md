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
