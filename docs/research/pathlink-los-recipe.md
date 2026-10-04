# Recipe: line-of-sight path-link regeneration

Status: module landed. `opent5.edit.pathregen` relinks a map's path nodes with a line-of-sight
test against the edited collision and gates the result on a single connected component. It is a
standalone module (`src/opent5/edit/pathregen.py`) with its own tests (`tests/test_pathregen.py`),
not yet wired into the editor's save path; that hook is Phase 2.5's job (see
`docs/v0.3.0-map-editor.md` section 6 and 7).

## The problem

`opent5.convert.pathlinks` bakes path-node links by distance so an unlinked converted map loads
(the retail engine reads baked links, it does not connect at load). It links every node pair
within a widening radius and refuses a graph that is not one connected component. It has no notion
of what sits *between* two nodes, so it will link two nodes straight through a wall or a prop. Once
the editor moves a prop, a link may no longer be walkable, and a moved prop must not leave a link
running through where it now stands.

## The segment-vs-clipMap test

The whole of a stock mp_nuked clipMap is axis-aligned boxes (cod2map emits axial `cbrush_t` boxes;
4205 of 4205 brushes on retail mp_nuked are axial), so a segment-vs-box test is exact and cheap.
`_segment_hits_box` is the slab method: it clips the segment's parameter range `[0, 1]` against each
box's three axis slabs and reports a crossing when a positive-length interval survives. A brush
counts as blocking when its `contents` meet the block mask, which defaults to `CONTENTS_SOLID`
(solid world) plus the device-proven player-clip bits `0x8030200` (a player-clip brush carries no
solid bit, so solid alone would miss it).

Two refinements earn their place, both measured on retail mp_nuked (1021 path nodes on a ~144-unit
grid, 1904 baked edges):

- **The test line is raised to body height** (`LOS_HEIGHT`, 40 units) above the node floor. Path
  nodes sit on the ground, on top of floor and step brushes. A line at node height lies in those
  boxes' top faces, and a slab test reads the whole floor as blocking: 272 of 1904 baked edges are
  wrongly rejected and the graph falls into 7 components. Raised 40 units the line clears the floor
  tops while still crossing any wall or prop, and the graph stays one component, keeping 1903 of the
  1904 baked edges (the one dropped edge is a real crossing retail happened to link anyway).
- **Grazing a face does not count.** The slab test is strict: a segment that only lies in a box's
  plane (zero penetration) is clear, so a link that runs flush along a wall is kept.

A `BspLocator` (when supplied) point-samples the segment and gathers only the brushes reachable
from the leaves it passes through, the way the engine's trace finds a brush. On retail mp_nuked that
is about two candidate boxes per segment, so a whole-map relink is a second or two. Without a locator
the test falls back to every blocking brush (correct, used by the synthetic tests).

## The single-component gate

After relinking (widening the radius as `pathlinks` does, but every candidate still
LOS-constrained), the graph must be one connected component or the engine drops the map.
`regenerate_or_raise` reads the links back from the written bytes and raises `PathRegenError` in
British English when it is not, naming the component sizes and the offending nodes, so a build or
save fails loudly rather than shipping a map the engine refuses.

## API

- `regenerate_links(game, clipmap=None, locator=None, ...)`: relink by distance with LOS rejection,
  widening the radius until connected; write the links back in the exact stock `pathlinks` bytes
  (bidirectional, inline, true distance). `clipmap=None` gives pure distance baking, byte-for-byte
  identical to `pathlinks.generate`. Returns a report; does not raise.
- `regenerate_or_raise(...)`: the same, then gate on one connected component or raise.
- `connected_components(game)` / `component_report(game)`: the components of the baked graph, read
  back from the node bytes (so the gate checks what was written).
- `segment_blocked(cm, p0, p1, ...)` / `segment_blocking_brush(...)` / `LosTester`: the LOS test on
  its own, for diagnostics and the per-prop check.

## What the device-made bus move shows

On the editor bus-move build (`q_editor_busmove/mp_nuked.ff`, the bus moved +300 X onto the central
road), the LOS rejects a candidate link straight through the bus's new clip position (blocked by a
bus-cluster brush) and finds the bus's old footprint clear of the bus clip (the cluster moved away,
so no bus brush blocks a link there). This is the enforcement of "a moved prop must not leave links
passing through it".

A whole-map relink of that build does *not* hold one connected component, so a whole-map gate would
raise. That is a property of the base map, not of the bus. Measured (big-endian, body-height LOS):

| zone | real path nodes | components as shipped | under whole-map LOS |
|---|---|---|---|
| pristine retail mp_nuked (`d_pak`, sha1 6d5a4a0e) | 296 | 4 | 42 (5415 links rejected) |
| `q_editor_busmove` (d_pak + bus +300 X) | 296 | 4 | 42 (5421 rejected) |
| the live `.env` mp_nuked (a deployed build, e.g. o_blocks5) | 1021 | 1 | 1 (1 rejected) |

Two conclusions, both important:

1. **The bus move does not split the graph.** `d_pak` and `q_editor_busmove` are component-identical
   (4 as shipped, 42 under whole-map LOS). The move rejects six more links locally (5415 -> 5421) with
   no change in component count. Any earlier claim that "retail is dense and one component" was reading
   the live `.env` file, which is whatever build was last staged (1021 nodes), not pristine retail.

2. **Our connectivity model is not the engine's.** Pristine retail mp_nuked ships with path links that
   our reader sees as **4 components**, yet the map loads and plays correctly on the device. So the
   engine's path connectivity is looser than a strict segment-LOS graph (it connects paths at load by
   its own rules). A whole-map single-component gate built on our model would therefore reject maps the
   engine is happy with. This is exactly why the save gate is **edit-relative**: keep the map's existing
   links, re-test only links crossing an edited clip volume, and fail only when the edit itself turns a
   pair that was connected before into one that is not. Pre-existing fragmentation under our model is a
   warning, never a failure. The bus's own effect is the local segment test above, which is
   deterministic and independent of the whole-map outcome.
