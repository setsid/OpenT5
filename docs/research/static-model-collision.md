# Spike: how a PS3 map gives a static-model prop collision

Status: spike for v0.3.0 (the in-place map editor), before any editor code touches the clipMap.
Question: a moved or added prop must stay solid, so how is a solid misc_model prop's collision
stored on PS3, and what must the editor maintain when a prop moves.

## What the data shows

Read from a pristine retail mp_nuked (sha1 6d5a4a0e) and the v0.2.0 box (n_box_all2), col_map_mp:

| clipMap count | stock mp_nuked | box (v0.2.0) |
|---|---|---|
| staticModels | 1385 | 8 |
| brushes / brushSides | 5890 / 22691 | 32 / 0 |
| tris / partitions | 7492 / 1829 | 0 / 0 |
| aabbTrees | 4930 | 0 |
| leafs / leafBrushes | 2985 / 19380 | 27 / 33 |

- **cStaticModel_s** (staticModelList, 0x50 each) carries, per prop: the XModel, origin, inverse
  scaled axis, and absmin/absmax world bounds, plus a writable `nextModelInWorldSector` the
  engine links at load. So the list holds per-prop collision bounds.
- **The CollisionAabbTree is the triangle-collision index, not static models.** Its leaf `u`
  union reaches 1828, which matches partitionCount 1829 (the CollisionPartition triangle groups),
  not numStaticModels 1385. A per-cmodel leaf points into it (box_model.leaf.collAabbCount). It
  is the terrain / surface mesh collision BVH.
- **Brush collision** (cbrushes in the BSP leafs via leafBrushes) is what makes the box floor,
  walls and the v0.2.0 clip boxes solid: the box has 0 tris and 0 aabb trees yet the floor is
  solid, so brush collision needs none of the triangle infrastructure.

## The key result

On device, the v0.2.0 box (k_box_full) carried its 8 props in the staticModelList with bounds,
but players walked through them: **the staticModelList alone did not give collision**, in a map
that had no triangle-collision infrastructure (0 tris, 0 partitions, 0 aabb trees). Stock props
are solid, in a map that has all of it. So on PS3 either static-model collision is only queried
once that infrastructure is initialised, or prop collision is carried by brushes. Either way, the
one prop-collision mechanism proven to work on PS3 from our own build is **axis-aligned clip
cbrushes in the BSP leafs** (the v0.2.0 clips; n_box_all2 collision confirmed on device).

## What this means for the editor

Two routes for keeping a moved or added prop solid:

- **Route A (proven, heavier): clip cbrushes.** Each editable prop owns an axis-aligned clip
  cbrush sized to its collision bounds. A move moves the cbrush; an add inserts one; a delete
  removes it. The editor must keep the cbrush referenced from the BSP leaf(s) it falls in
  (leafBrushes / cLeaf), so a trace finds it, and keep numBrushes / numLeafBrushes consistent.
  Axis-aligned cbrushes need no brushSides (6 implicit axial planes), as the box shows, which
  keeps this tractable. This is the recommended default.

- **Route B (simpler, unproven): static-model collision.** Update the prop's cStaticModel_s
  (origin, axis, absmin/absmax) on a move and let the engine's world-sector list find it. This is
  far simpler, but the box showed it does not work without the collision infrastructure. Before
  relying on it, a device test must convert a map that HAS that infrastructure (a real Mod Tools
  map with terrain tris, since both the box and the blocky map are brush-only and carry none) and
  confirm its static-model props are solid; and that moving one by editing only cStaticModel_s
  keeps it solid.

Recommendation: build the editor on Route A (clip cbrushes), because it is the mechanism our own
builds have shown solid on device. Run the Route B device test early; if it passes, offer it as a
lighter path, but do not block the editor on it. Either route needs the BSP/leaf bookkeeping to
stay consistent, checked on save by the emulated loader (targets_outside_their_block 0, blocks end
at header) and verify.

## Follow-ups this opens

- The box's static models not colliding is consistent with "needs the collision infrastructure";
  it is not proof the infrastructure is sufficient. The Route B device test settles it.
- The aabb tree being triangle collision means a terrain map built from tris (not brushes) would
  need the aabb tree generated for its terrain to be solid; our generated maps use brushes, so
  this does not block them, but a future tri-based generator would need it.
