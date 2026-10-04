# Recipe: what cod2map does to the clipMap when a clip brush is added

Status: RESOLVED. The root cause is the brushVerts contiguity invariant (see "Root
cause" below). Device confirmed p_clip_move (an existing clip re-leafed to the test
spot, numBrushes unchanged) is SOLID, and p_clip_a (an appended clip) walked through.
The fix is in `opent5.convert.propclip.add_clip_bsp` (contiguous brushVerts append) and
is staged as p_clip_add for device confirmation.

Goal (original): read off what the real compiler changes in the clipMap when a clip
brush is added, compare it to `add_clip_bsp`, and find why a converter-added clip was
not solid on hardware.

Build base: `fix/clip-trace-recipe` off `fix/clip-trace-miss`.

## Method (step 1, the compiler diff)

Two box maps were compiled with the real PC Mod Tools (cod2map, cod2rad, linker_pc; the
command lines of `tools/testmap.py` / box-map.md 1.2), identical except one carries extra
worldspawn `clip` boxes:

- `mp_clipdiff_a` (base): `testmap.map_text(objectives=False, path_nodes=False, props=False)`.
- `mp_ctrlprops`: the same plus `props=True`, which adds eight worldspawn `clip` boxes and
  eight `misc_model` props.

Both linked to a PC `.ff`. Their `col_map_mp` clipMaps were parsed (`opent5.convert.pc.parse_pc`,
PC little-endian) and diffed (`tools/clip_diff_parse.py`, `tools/clip_probe*.py`).

Note on the single-brush diff: cod2map **crashes (exit -1) on a minimal box map that gains a
single extra worldspawn brush of any material** (clip, caulk or a wall), unless companion
`misc_model` entities are present. This is a sealing/portal quirk of the trivial test map, not
of the clip brush. The eight-prop map compiles, so the per-brush recipe is read from it (eight
identical, independent clip boxes).

## The recipe: what cod2map changes per clip brush

Base -> props clipMap array deltas (eight clip boxes added):

| structure | base | props | delta | what |
|---|---|---|---|---|
| brushes (cbrush_t, 0x60) | 6 | 14 | +8 | one cbrush per clip |
| brush_verts (vec3) | 48 | 112 | +64 | eight box-corner verts per clip |
| leafbrushNodes (cLeafBrushNode_s, 0x14) | 9 | 16 | +7 | a kd-tree over the clip leaf |
| leafbrushes (u16 pool) | 7 | 15 | +8 | one pool entry per clip |
| planes (cplane_s) | 6 | 6 | 0 | axial brushes need no planes |
| brushsides | 0 | 0 | 0 | axial brushes have no sides |
| nodes (cNode_t BSP) | 7 | 7 | 0 | the world BSP is not touched |
| leafs (cLeaf_s) | 9 | 9 | 0 count | existing leaves are repointed, none added |
| aabbTrees / partitions / tris / borders / cmodels | - | - | 0 | not involved |

So, per clip brush, cod2map:

1. **Appends a `cbrush_t`** (contents `0x08030200`, `numsides` 0, `sides` NULL, the six axial
   `cflags` = contents, the six axial `sflags` = `0x000440A0`, `numverts` 8, a `verts` pointer).
   Bumps `numBrushes`.
2. **Appends the eight box-corner verts** to the `brushVerts` pool; bumps `numBrushVerts`.
3. **Appends the brush index** to the flat `leafBrushes` pool; bumps `numLeafBrushes`.
4. **Builds a leafBrushNode kd-tree** for the leaf the clips fall in. For the eight-clip box all
   eight went into one leaf (leaf 5, the open interior), as a kd-tree of three split nodes and
   four leaf nodes (two brushes each):
   - split node: `leafBrushCount` 0, `axis`, `dist` (f32 at +8), `range` (f32 at +0xC),
     `childOffset[2]` (u16 at +0x10/+0x12, relative node indices, confirmed `node + offset`);
   - leaf node: `leafBrushCount` > 0, `contents` = the clip contents, `data` = a pointer into
     the leafBrushes pool.
5. **Repoints the target `cLeaf.leafBrushNode`** to that kd-tree root, **OR-s the clip contents
   into `cLeaf.brushContents`**, and **updates `cLeaf.mins`/`maxs`** to bound the new brushes
   (base leaf 5 was a degenerate `(0,0,0)..(0,0,0)` leaf; it became `(-326,-468,0)..(334,474,40)`,
   the tight bounds of the clip volume).
6. Leaves `planes`, `brushsides`, `nodes` (the cNode BSP) and `numLeafs` untouched.

cod2map also **re-sorts the whole brush array and rebuilds the entire leafBrushNode forest**;
brush and node indices are not stable across a recompile.

## Compared to `add_clip_bsp` / `merge_brush_into_leaf`

What the converter does **the same** (verified byte for byte on p_clip_a's own serialised file,
brush 5890 vs stock axial player-clip brush 82):

- the `cbrush_t` record is byte-identical in shape: contents `0x08030200`, `numsides` 0,
  `sides` NULL, six axial cflags `0x08030200`, six axial sflags `0x000440A0`, `numverts` 8, a
  resolving `verts` pointer to eight correct box-corner verts;
- appends to the brushVerts and leafBrushes pools, bumps `numBrushes` / `numBrushVerts` /
  `numLeafBrushes`;
- repoints the target `cLeaf.leafBrushNode` and OR-s the clip contents into
  `cLeaf.brushContents` and the leaf node's `contents`.

Discrepancies (converter vs cod2map):

1. **Leaf bounds.** cod2map updates the target `cLeaf.mins`/`maxs` to include the new brush.
   `merge_brush_into_leaf` does not. For p_clip_a's target (leaf 596) the leaf box is already
   huge and contains the clip, so this does not explain the miss there; it would matter for an
   empty/degenerate target leaf.
2. **kd-tree shape.** cod2map builds split nodes; the converter collapses the leaf's reachable
   set into a single flat leaf node. Both are legal (stock leaves 6-8 in the box use a flat
   `count=1` root and are solid on device), so this is a shape difference, not a correctness one.
3. **Append vs rebuild.** cod2map re-sorts brushes and rebuilds the forest coherently; the
   converter appends past the original `numBrushes` and surgically repoints. This append is the
   one thing common to both failing device builds (see below).

Stock mp_nuked has **759 axial (`numsides` 0) brushes, 11 of them player-clips** (brush 82, 83,
95, 98, 100, 102, ...), solid on device, so `numsides` 0 is a supported, solid shape. The
converter's clip is indistinguishable from these.

## Step 2: the offline trace / structural oracle, and what it reports

The t5mp ELF is stripped of the CM_ collision symbols (only `CM_LoadMap: NULL name` survives),
so a faithful PPC CM_BoxTrace could not be lifted from it directly. Instead the engine's brush
reachability was modelled structurally (`BspLocator.locate` to point-locate the cNode BSP leaf,
then `ClipMap.reachable_brushes` over that leaf's leafBrushNode kd-tree, player-hull aware) and
checked against stock ground truth (`tools/clip_oracle.py validate`):

- stock axial player-clips (`numsides` 0, contents `0x08030200`) are reported solid at their own
  centre in **9 of 11** cases; a point high in the air is not solid;
- a broader 200-brush sample reproduces the same partial match: the centre's located leaf lists
  the brush in ~83% of cases.

So the oracle matches stock reality only approximately (~82-90%), not exactly: for roughly one
clip in six the centre's located leaf does not list the clip. This is itself a finding: the
converter's point-locate-plus-reachability model is not a faithful trace. (The big bus-volume
brush 5169, contents `0x08001040`, is a vehicle-style clip in a loose-box leaf, not in the
player-clip set; an early single-brush probe of it was a red herring.)

Run against p_clip_a at the clip (centre 204,-47,60, box `(174,-77,0)..(234,-17,120)`):

- the point locates to **leaf 596**, whose box contains it;
- the new clip **brush 5890 is reachable from leaf 596** in the serialised file, alongside the
  sibling stock player-clips 4887 and 5074 that cover that region;
- leaf 596's `brushContents` and the listing node's `contents` include the clip bits;
- the clip record and its eight verts are byte-correct and resolve.

So the structural oracle **reports p_clip_a's clip as a valid, reachable, solid player-clip**. It
does **not** reproduce the device walk-through. The device failure is therefore **not in the
clipMap brush / leaf / pool structures the converter edits**: those are correct and match stock.

**CORRECTION (device ground truth): leaf assignment is NOT ruled out.** Only **p_clip_a** was
tested on device (it walked through). **p_clip_c was never tested.** An earlier draft of this
section claimed both a and c walked through and concluded the cause was "independent of leaf
assignment" — that was wrong: the p_clip_c result was never observed. Do not build on that
conclusion. What device testing actually establishes so far is only that **p_clip_a (add_clip_bsp,
clip reachable from its trace leaf) walked through**. Leaf assignment, the brush-array append, and
the leafBrushes/leafBrushNode append all remain live suspects. `p_clip_move` (move an existing
clip, no `numBrushes` growth) is the next test and will separate the append hypothesis from the
rest; p_clip_c would separately retest the leaf-assignment hypothesis if staged.

## How the gap was closed

At the time of the first write-up the offline structural oracle reported p_clip_a solid while the
device showed walk-through, so the cause was not captured by the leaf/reachability checks. The
decisive step was p_clip_move: an existing stock clip re-leafed to the test spot (no `numBrushes`
growth). The device showed it SOLID, which isolated the cause to the brushes/brushVerts growth of
the append. The `brushVerts` contiguity analysis above then pinned it exactly: the one-element pad
the converter inserted into `brushVerts` broke the running-count layout the engine relies on. The
"Root cause", "The fix", and "p_clip_add" sections above record the resolution.

## p_clip_move (staged, the diagnostic build)

Staged at `/mnt/c/Users/bolst/Desktop/opent5-hwtest/nuked/p_clip_move/mp_nuked.ff`
(`tools/stage_clip_move.py`). This is the recommended experiment, NOT an appended-brush build.

- **Landmark:** the central road crossing between the two houses, slightly east of map
  centre, the exact p_clip_a spot (204,-47) so it is a direct controlled comparison (same
  location and shape; the only change from p_clip_a is moved-existing vs appended). The two
  team spawn clusters sit west (x around -700..-2000) and east (x around +790..+2100), so a
  player crossing the middle walks into it. The moved marker prop stands at the wall.
- **Clip moved:** stock axial player-clip **brush 82** (a small perimeter clip, contents
  0x08030200), from (788,555,86)..(799,564,146) to a wall across the road
  **(180,-147,-48)..(228,53,112)** (thin in X, the travel axis; wide in Y; tall in Z over the
  crouch/stand range). `numBrushes` stays 5890.
- **Marker prop moved:** clipMap `cStaticModel_s` index 28 (a crate-sized central collidable
  prop) and its matching GfxWorld `smodelDrawInst` 434 (plus the parallel `smodelInst` bounds)
  moved to (204,-47,-20). Render follows the origin (Route-B); its own baked collision does not,
  which is fine because collision here is the moved clip. `numStaticModels` stays 1385.
- **Gate (all pass):** reparse-exact (`blocks_end_at_header` true), full oracle clean
  (`consumed_exactly` true, 109496/109496 pointers identical, `targets_outside_their_block` 0,
  both clipMap checks 0), `check_world_leaf_refs` 0, `check_leaf_contents_masks` 0,
  `clip_oracle` reports the moved clip solid at (204,-47,*), `numBrushes` 5890 unchanged,
  `numStaticModels` 1385 unchanged, `numBrushVerts` 48747 unchanged.
- **File sha1:** `9488a3fee09dd9653497892d07985d6fdfa16b2e` (content sha1
  `34e9df4a4040ba369201b818dc1479898754293b`).

**Diagnostic reading on device** (p_clip_move moves an existing clip, so `numBrushes` does not
grow, unlike the appended p_clip_a):
- if p_clip_move is **solid** -> moving an existing clip works where appending one (p_clip_a) did
  not, implicating the cbrush-array append / `numBrushes` growth. The fix is to rebuild the clipMap
  the cod2map way: re-sort the whole brush array, rebuild the leafBrushNode forest and update the
  cLeaf mins/maxs, rather than appending past the original brush count;
- if it still **walks through** -> the `numBrushes` growth is not the (only) cause. The gap is then
  in the leafBrushes / leafBrushNode append or the engine's leaf descent, and leaf assignment
  itself is still unproven (p_clip_c was never device-tested). The next step needs a faithful
  CM_BoxTrace from a symbolised t5mp.

## Root cause (confirmed): the brushVerts contiguity invariant

Device result settled it: **p_clip_move (an existing stock clip, brush 82, re-leafed to
the test spot with `numBrushes` unchanged) is SOLID; p_clip_a (a brush appended past the
original `numBrushes`) walks through.** The two edits differ only in that the append grows
the `brushes` array (+1) and the `brushVerts` pool, while the move rewrites in place. The
discriminating structure is **`brushVerts`**.

Evidence (`tools/clip_verts_check.py`, PS3 big-endian):

- In retail mp_nuked, `brushVerts` is strictly contiguous in brush-index order: every
  brush's verts sit at the running sum of the earlier brushes' vert counts,
  `verts_ptr[i] == base + 12 * Sum(numverts[0..i-1])` — **0 mismatches across all 5890
  brushes**, and the running total equals `numBrushVerts` (48747) exactly. The engine
  addresses a brush's collision verts by this running count, not by the stored `verts`
  pointer.
- p_clip_move: contiguity perfect (0 mismatches) -> solid on device.
- p_clip_a: **1 mismatch, at the appended brush 5890** — its verts sit 12 bytes (one vec3)
  past the running-count position, because the converter's pool append
  (`ClipMap._append_pool`) inserts a one-element pad at the pool's old end so the appended
  pointer clears the allocation boundary (`brushVerts` is immediately followed by `uinds`).
  That pad shifts the appended brush off its running-count slot, so the engine reads the
  pad (zeros) instead of the clip's corners and the brush collides with nothing. This is
  the p_clip_a walk-through, reproduced and explained offline.

So p_clip_a had a correct `verts` pointer yet failed: proof the engine uses the running
count, not the pointer. The move preserved contiguity because it rewrites verts in place.

Secondary finding (not the failure, but cod2map does it and we did not): cod2map keeps
`box_model.leaf.leafBrushNode` pointing at the last `leafBrushNode` and renumbers it when
nodes are inserted (box diff: 8 -> 15). Appending nodes at the end (as the converter does)
leaves the box-hull node in place, so `box_model.leaf.leafBrushNode` stays valid without an
update; no fix needed for the append-at-end path.

## The fix (task 2): `add_clip_bsp` appends brushVerts contiguously

`ClipMap.append_verts_contiguous` appends the eight corner verts to `brushVerts` with NO
boundary pad, so the appended brush's verts sit exactly at the running-count position and
the pool stays contiguous. The stored `verts` pointer is set to that position; because the
following allocation (`uinds`) begins at the boundary, the Rewrite resolves the pointer to
that neighbour, which is harmless since the engine ignores the stored pointer for collision
verts. `add_clip_bsp` now uses this instead of the padded `append_verts`. The padded path is
parked (kept only for `add_clip`, the synthetic-test fallback).

A full re-sort/rebuild of the brush array (what the compiler does internally) is NOT needed:
appending the new brush at the end with contiguous verts satisfies the invariant (the new
brush's verts are the new tail), and the move proved re-leafing a brush into the trace leaf
works. This keeps every stock brush untouched.

## Offline verification (task 3)

- `test_propclip.py`: passes (the `add_clip_bsp` tests now exercise the contiguous path).
- `tools/convert_map.py oracle` on p_clip_add: reparse-exact (`blocks_end_at_header` true),
  `consumed_exactly` true, 109497/109497 pointers identical (`same_fields`/`same_values`),
  `targets_outside_their_block` 0, `clipmap_world_leaf_problems` 0,
  `clipmap_leaf_contents_problems` 0.
- brushVerts contiguity: 0 mismatches; running total 48755 == `numBrushVerts` 48755.
- `clip_oracle solid`: the new clip (brush 5890) is solid at (204,-47,*), leaf 596.
- Unchanged stock clips (brushes 82, 83, 102, 107): record byte-identical ignoring the
  re-encoded `verts_ptr`, and their verts read by running count are identical to pristine, so
  stock collision is undisturbed.
- Zone rebuild round-trip (`tests/test_cli.py::test_rebuild_is_byte_identical`): passes. The
  change is confined to `add_clip_bsp`, outside the parse/rebuild path.

## p_clip_add (staged, the proof build)

Staged at `/mnt/c/Users/bolst/Desktop/opent5-hwtest/nuked/p_clip_add/mp_nuked.ff`
(`tools/stage_clip_add.py`). A genuinely NEW clip brush (numBrushes grows), the cod2map way.

- **Landmark:** the central road crossing between the two houses, slightly east of map
  centre (204,-47) — the same spot as p_clip_move, so the only change from the solid
  p_clip_move is that the clip is NEW (appended) rather than an existing brush re-leafed. The
  west and east team spawn clusters sit either side, so a player crossing the middle walks
  into it; the marker prop stands at the wall.
- **New brush:** index **5890**, bounds **(180,-147,-48)..(228,53,112)** (thin in X, the
  travel axis; wide in Y; tall in Z), contents 0x08030200, numsides 0, 8 contiguous verts.
- **Marker prop:** central collidable prop 28 and its GfxWorld draw inst 434 moved to
  (204,-47,-20) (render-only; `numStaticModels` unchanged).
- **Counts:** numBrushes 5890 -> 5891; numBrushVerts 48747 -> 48755; numStaticModels 1385
  unchanged.
- **Second spot for the user to confirm nothing shifted:** the **bus in the centre of the
  map** — its invisible player-clip shell (stock brushes 83, 102, 104, 107, unchanged) must
  still block exactly as in stock. (The re-sort was not needed, so no stock brush moved; the
  bus is a clear reference the player passes.)
- **File sha1:** `fab14aecbaf1be9cad4429a8608bdfb2750a01d8`.

Device reading: if p_clip_add is solid, a NEW appended clip now works and the fix is proven;
the bus must still be solid (nothing else shifted).

## Tools (scratch, this branch)

- `tools/clip_diff_build.py` - builds the two box maps and runs the real toolchain.
- `tools/clip_diff_parse.py` - diffs two compiled PC clipMaps.
- `tools/clip_oracle.py` - the offline structural check (`validate` / `solid`).
- `tools/clip_verts_check.py` - checks the brushVerts contiguity invariant on a zone.
- `tools/stage_clip_move.py` - stages p_clip_move (moves clip 82 + marker prop 28).
- `tools/stage_clip_add.py` - stages p_clip_add (NEW brush via the fixed `add_clip_bsp` +
  marker prop), repacks the PS3 .ff, and verifies the whole gate.
