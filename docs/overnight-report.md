# Overnight report (2026-10-03 into 2026-10-04)

Everything is committed locally only. Nothing was pushed, tagged or released.
Game folders were read-only (only new test-map files named mp_opent5* were written
into the Steam tools folder). No commit carries AI attribution.

The version is bumped to 0.2.0 locally. Review, then push and release yourself.

## Invariants

- Fast suite: 794 passed, 272 deselected, 48 s.
- Full suite (slow included): see the line at the end of this file.
- All 178 zones round-trip byte-identically (`tools/rewrite_all.py`, 178/178).
- Both exe smoke tests pass on the final 0.2.0 exe: 109 view checks 0 failed, and
  the update check accepts a good release and rejects a tampered one.

## What landed (commits, oldest first)

| Area | Commit | What |
|---|---|---|
| Converter | 87af53d | Textures, static models and objective checks wired into `convert`; static models in the world view |
| Diagnosis | effe727 | C/D investigation: config-string theory withdrawn with evidence |
| Search | ce8bf2e | Disk-cached global search index across all 178 zones |
| Map gen | 8b16d30 | Seeded blocky-terrain generator with original pixel-art textures |
| Mod patch | c7d32b2, 8ea5e21 | Share only the changes; verify the source; reproduce the edited zone. CLI wired |
| GUI | 593c7d5 | Textured GPU model/world viewer, image thumbnails, per-type tree icons |
| GUI | 0d575cd | Panels for cross-zone search and mod patches |
| Map gen | 1389817 | Register blocky-map materials/images and wire its conversion |
| Version | 24257c3 | Bump to 0.2.0 |
| Diagnosis | 37a9ef6 | fixC radius-trigger and fixD path-node test-map options |
| Exe | fa6f23a | Index builds serially in the frozen exe; bundle the OpenGL modules |
| GUI | 5026281 | Polish: structured error dialogs, empty states, Quit shortcut, theme checks |
| Textures | b11cc90, 5fb995a | Texture-pack workflow: batch-replace a zone's textures from a folder. CLI wired |

Final 0.2.0 exe sha1 3b329952 (opent5-hwtest/OpenT5-0.2.0-preview/); both smoke
tests pass on it.

## App features (section 3)

- **Mod patch format** (`opent5 patch create/apply/info`, GUI File menu, format in
  docs/patch-format.md). Share a mod as the difference from a stock zone, so no
  game files are redistributed. A cfg edit is a 309-byte patch that reproduces the
  edited zone byte-identically; apply refuses a source whose hash does not match.
- **Global search** (`opent5 search`, GUI "All Zones" tab, Ctrl+Shift+G). Search
  names and text across all 178 zones from a SQLite cache. Cold build about 47 s,
  then instant; queries 55 to 360 ms. In the exe it builds serially (a frozen exe
  cannot spawn a process pool); verified working in the 0.2.0 exe.
- **Textured viewer** (Shaded/Wireframe toggle, GPU path). The model and world
  viewer draws with the assets' own textures and lighting; the tree shows image
  thumbnails and per-type icons. See "needs you" for the exe caveat.
- **Texture pack** (`opent5 texpack list/apply`). Batch-replace a zone's textures
  from a folder of PNGs named after the images (a dry run previews the mapping,
  and a map file gives exact control). mp_nuked has 1004 replaceable images.
- **GUI polish.** Error dialogs now show expected, found and the offset in a clear
  grid; empty states and a first-run view; a complete keyboard list; both themes
  checked.

## Test files on your desktop

Run them in this order; all need the signature-patched client. Back up the stock
file first and restore after each (retail mp_nuked.ff sha1 6d5a4a0e, mp_nuked.pak
sha1 5930c6c6).

opent5-hwtest/nuked/

| Folder | File (sha1) | Start | What to look for |
|---|---|---|---|
| k_box_full | mp_nuked.ff (17d6a7e9) | TDM, Nuketown | Lit floor/walls/ceiling, custom grey wall texture, 8 props, A/B fixes. Viewmodel: see note |
| l_box_full_named | mp_opent5box.ff (154cf6c6) + .pak | needs the opt-in map-table row to be startable; offline-proven | Same, as its own named map, with compass dvars off |

opent5-hwtest/OpenT5-0.2.0-preview/ holds the 0.2.0 exe and its screenshots for
review. Your installed 0.1.0 at Desktop\OpenT5\ is untouched.

Not built tonight (PC compile blocked, see Parked): k_box_fixC, k_box_fixD, and
the blocky-map demos. Commands are staged for the morning.

## Proven offline vs needs a device run

Proven offline (exact re-parse, emulated game loader, verify, GUI self-test):
every converted zone's structure; the mod patch round-trip; the search index; the
blocky-map generator counts and textures; the fixC/fixD entity changes.

Needs a device run:
- The lit box's viewmodel brightness. The A fix makes the light grid reference a
  primary light, but cod2rad bakes it darker than Nuketown (grid p50 174 vs 1151).
  So the viewmodel may be dim rather than correct. The k_box_full run tells "dim
  but working" from "still wrong".
- C and D (Search and Destroy objectives, Domination hang). Best offline
  candidates: C is the bomb/site brush-model triggers not spawning; D is the
  Domination flag influencers needing path-node connectivity. fixC (radius
  triggers) and fixD (path nodes) are coded and will isolate each once compiled.
  Capture with Debug Console Mode on and the PPU callstack when it hangs; the
  exact discriminator lines are in docs/research/box-objectives-cd.md.
- Whether the named-map compass dvars reach the client.

## Parked (needs you)

- **PC Mod Tools loader wedged.** Partway through the night the LinkerMod loader
  (launcher_ldr) began returning "Access is denied" (exit 5) for every map
  compile, while cod2map.exe alone still runs but writes the old BSP version. A
  protected launcher-x64 process (PID 12280, session 0) could not be killed by the
  WSL user. It is not in the task list now, so clearing it or restarting should
  free it. This blocks only new PC compiles: the fixC/fixD and blocky-map demos.
  Morning: clear the process or restart, then run the staged commands.
  - fixC/fixD: scratchpad integ/fixCD.sh, or the commands in docs/demo-box-full.md
    section 7.
  - blocky map: `blockmap build mp_opent5blocks` then `blockmap convert`
    (docs/mapgen.md).

## Needs you (decisions / action)

- **Loader process** (above): clear it, then the three map demos build and I can
  finish them.
- **Shaded viewer in the exe.** The textured viewer needs a working OpenGL driver.
  It falls back to wireframe when none is present (so the exe never breaks), but it
  could not be screenshot-tested in the headless build. Please open a model in the
  0.2.0 exe and click Shaded to confirm it renders on your machine; if it only
  shows wireframe, the fix is bundling the Qt OpenGL plugins, which I can do.
- **0.2.0 release** is drafted (docs/releases/0.2.0.md) but not tagged. Review the
  notes and the preview exe, then release yourself.

## Decisions I made (full list in docs/decisions.md)

- No AI attribution on commits, despite a harness reminder (your rules forbid it).
- Hardened the test stand-in with pinned retail copies so overnight tests did not
  depend on the live disc you were testing against.
- Verified first-hand that the earlier tracks did not fabricate: the PC tools are
  installed and the compiled PC zones are real (one track had looked in the wrong
  place).
- Batched the exe smoke tests at release prep rather than after every commit.
- Built the search index serially in the frozen exe (a PyInstaller exe cannot
  spawn a process pool) rather than adding a dependency.

## Full suite result

(appended at the close of the session)
