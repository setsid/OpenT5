# Overnight report (2026-10-03 into 2026-10-04)

Everything below is committed locally only. Nothing was pushed, tagged or
released. Game folders were read-only (new test-map files named mp_opent5*
excepted). All commits carry no AI attribution.

## Invariants held all night

- Full test suite green after each landed change.
- All 178 zones round-trip byte-identically (`tools/rewrite_all.py`, 178/178
  after the converter integration).
- Both exe smoke tests: confirmed at the final rebuild (see Release prep).

## What landed

### Converter integration (commit 87af53d)

Textures, static models and the per-gametype objective check are wired into
`opent5 convert`. Same-name stock materials are reused; materials the base lacks
are built into the map's own zone; static models place the base zone's XModels by
name; `entities.check` reports what each gametype needs. All five earlier
conversion modes still produce byte-identical output (no regression). The GUI
world view gained a "Static models" toggle. Fast suite 712 passed in 40 s; full
suite 982 passed in 20 m.

### RPCS3 box fixes A and B (in the converter integration)

- A (viewmodel light grid): the test-map room light is now a primary light, so
  the light grid references it (2883 of 2919 entries, was 0). Honest caveat:
  cod2rad still bakes it darker than Nuketown (grid p50 174 vs 1151), so the
  viewmodel may be dim rather than correct. Needs the morning RPCS3 run to tell
  "dim but working" from "still wrong".
- B (minimap letter grid and rotation): both are engine dvars
  (compassGridEnabled, compassRotation). Named maps now set them off in their own
  script. The Nuketown-replacement mode cannot, because the update's script runs
  instead; documented.

## Test files on the desktop (opent5-hwtest/nuked/)

Order to run them, newest work first:

| Folder | File | sha1 | What it proves |
|---|---|---|---|
| k_box_full | mp_nuked.ff | 17d6a7e9 | Full box: own lighting, own wall texture, 8 props, all 12 gametypes' objectives, A and B fixes (Nuketown-replacement) |
| l_box_full_named | mp_opent5box.ff + .pak | 154cf6c6 | The same as its own named map (compass dvars off) |

(Earlier test files g_box_lit, i_box_modes, j_pak_resize already reported; j_pak_resize passed on device.)

## Still inferred / needs a device run

- A: whether the primary-light grid is bright enough (dim vs wrong).
- Named-map compass dvars reaching the client.
- C and D (Search and Destroy objectives, Domination hang): see the C/D section
  once that investigation lands.

## Decisions I made (see docs/decisions.md)

- Kept commits free of AI attribution despite a harness reminder (work order and
  global rules forbid it).
- Hardened the test stand-in (`retail-english`) with pinned retail copies of
  mp_nuked.ff and .pak so overnight tests do not depend on the live disc.

## Parked

(to be filled as tracks report)

## Needs you

(to be filled)
