# The map editor

The editor edits a PS3 map zone's placed objects in the 3D view and saves the result to a
new file. It moves, rotates, adds, deletes and duplicates static-model props, spawn points,
objective entities and placed lights, and edits the sun and fog. It does not touch world
geometry or baked lighting, and it never writes over a stock zone.

This guide covers the common path: open a zone, select a prop, move it, rotate it, add and
delete objects, save, and stage the result for a device test.

## Open a zone

Open the `.ff` for the map you want to edit, the same way you open any zone in the app, and
switch to the 3D view. The view draws the map with its own textures and lighting, static
models included. An edited-since-save marker shows once you make a change.

A zone must carry a map entity string (in its `col_map` or on its own) to be editable; an
entity-only or synthetic zone with no clipMap cannot take prop-collision edits.

## Select an object

Left-click an object in the 3D view. Props are picked against the drawn static models;
spawns, objectives, lights and the sun are picked against their markers. The nearest hit
wins, and the selection is shown with its properties in the panel. Only objects that carry
an origin show a marker.

## Move

Drag the selected object's translate gizmo along an axis, or type an exact origin in the
property panel. A drag counts as one edit for undo. The prop's model always moves to the new
spot, and the 3D view rebuilds so you see it move without reopening.

What happens to the prop's collision depends on how it was built. A prop whose collision is a
clip-brush cluster (a few stock props, and every prop you add here) moves that cluster with
it, so it stays solid at the new position and the old position is left clear. Most stock props
have no clip cluster: their collision is baked triangles that cannot be relocated without a
full recompile, so moving one moves only its model and warns that its baked collision stays at
the old spot.

Moving or deleting a prop warns you that its baked lightmap shadow stays where the prop was
baked. The editor cannot relight the map, so the shadow does not follow the prop. This is
expected.

## Rotate

Drag the rotate gizmo, or type exact angles in the panel. The prop renders rotated. Its
collision clip is an axis-aligned box sized to the prop's footprint, so a prop turned off
the world axes keeps an axis-aligned solid volume.

## Add and duplicate

Add prop opens a picker of the zone's placeable models and drops the chosen one at the view
target. The picker lists only models the zone can draw a copy of, meaning those with an
existing GfxWorld draw instance to clone; a model with no instance cannot be cloned, so it is
left out rather than added as an invisible prop. B is a quick add of the zone's default
crate-like model. A newly added prop gets a solid clip sized to its footprint, so it draws and
blocks. Add (without "prop") still adds a placed entity by classname. Duplicate copies the
selected object with a small offset. The editor adds only models and classes the zone already
holds, not brand-new assets.

## Delete

Delete removes the selected object. Deleting a prop hides its model (the 3D view rebuilds so it
disappears without reopening) and removes the clip cluster under its footprint, so it is no
longer solid. A prop whose collision is baked triangles rather than a clip cluster has its
model hidden and warns that the baked collision stays, since baked collision clears only with a
full recompile. The baked-shadow warning applies here too.

## Save to a new file

Save writes a new file, `<name>.edited.ff` by default. Before writing, the editor rebuilds
what the edits changed, runs verify (the reparse-exact and emulated-loader checks) and the
gametype entity rules. If a check fails, or an edit would break a gametype that worked
before, nothing is written and the failing check is reported. The source zone and any stock
zone or pak are never written.

## Stage for a device test

A stale disc cache can make RPCS3 serve an old map and look like your edit did nothing, so
stage a build through the fixed protocol:

1. Close RPCS3 and clear (or rename) the BLES01031 FIOS disc cache. RPCS3 keeps a decrypted
   copy of the disc and will serve it if the new file is not newer than the cache.
2. Copy your edited `.ff` into place, then set its modified time to now. A plain copy keeps
   the old time and never invalidates the cache.
3. Put a visible marker in the map (a moved prop at a known spot) so a stale load is obvious.
4. Run `tools/verify_staged_map.py` to confirm the staged `.ff` is the map you meant to ship
   before you load it.

Remember that an edited zone keeps the original console signature, which no longer matches
after an edit, so it loads only on a client with the signature check patched out.
