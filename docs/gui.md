# The desktop GUI

    npm run gui                                  # or: .venv/bin/python -m opent5.gui
    .venv/bin/python -m opent5.gui path/to/mp_nuked.ff path/to/patch_mp.ff
    .venv/bin/python -m opent5.gui --theme light

PySide6-Essentials 6.8.1 (`requirements-gui.txt`, installed by `npm run setup`). The code is
`src/opent5/gui/`; it reads and edits zones only through `opent5.edit.Document`
(`docs/edit-api.md`), wrapped by `gui/backend.py` (`ZoneDoc`). If `opent5.edit` is missing,
`ZoneDoc` falls back to a read adapter over `opent5.xfile` / `opent5.export` that keeps edits
in memory and cannot save. The status bar says which one is in use ("edit API" or "read
adapter").

Zones are only read. Saving always writes a new file: Save As refuses the source file and
any file inside the zone folders configured in `.env`.

## Layout

- **Zone tabs.** Each open zone is a tab. While it has unsaved edits its text starts with
  `* ` and it carries a dot in the accent colour, and the window title reads
  `* zone - OpenT5`.
- **Unsaved strip.** Across the top of a zone with edits: "Unsaved changes: N assets
  edited (M edits). Save As to write a new file.", with **Save As...** and **Discard**.
  Discard undoes every edit after asking; Redo brings them back while the zone is open. The
  strip has a 3px accent bar on its left edge and the panel background, in both themes.
- **Asset tree** (left). The assets are grouped by type, with a count per type. It lists the
  zone's own asset list and also the assets loaded inside other assets (images inside
  materials, models, the clipMap's entity string), marked `inline`. Above the tree is a
  filter box: every word you type must match. Edited assets and their type rows are
  marked `*` in the accent colour. Each type and asset carries a monochrome line icon for its
  kind (image, model, world, material, sound, text, table, localize, font, fx, or a generic
  one). Image assets show a small decoded thumbnail in place of the icon: they are decoded
  lazily on a worker the first time a row is seen and cached per asset, so opening a zone
  stays fast; an image whose pixels are not in this zone keeps the image icon.
- **Asset header and view bar** (right). These show the asset's name, type, list index,
  size and zone offset, then one button for each view the asset offers.
- **Bottom panel** (Ctrl+J), with two tabs: **Search** and **Changes**.
- **Status bar.** It shows the cursor or selection in the current view, then the zone
  name, the asset counts, whether the zone is signed and how many changes it has, and the
  backend.
- **Start page.** It lists recent files and every zone in the configured folders (disc,
  update, dlc). The folders are read on a worker thread.

Opening a zone runs on a worker thread. Its tab appears at once and shows a centred panel
with the file name, the stage, a bar and the percentage; the status bar shows the same. The
bar is driven by the work itself: chunks decrypted and inflated (0 to 25%), assets parsed
(25 to 85%), then the shared-string and inline-asset indexes. Closing the tab while it loads
drops the result. On mp_nuked (35 MB file, 69 MB inflated) opening takes about 3 s, and the
window never stops responding for more than about 60 ms.

Saving shows a progress strip across the top of the zone page (and the status bar): assets
written, pointers remapped, chunks compressed and encrypted, the file written, then the
verification's chunks, parse and asset comparison, each stage filling its share of the bar
by its own counts.

## Views

| View | For | What it does |
|---|---|---|
| Text | rawfile (GSC, CSC, cfg, txt, script), clipMap / map_ents entity string | GSC and CSC scripts are stored without indentation; with View > Formatted GSC (on by default, remembered) they are shown indented by `opent5.edit.gscformat` and saved back in the stored form: an unedited script saves byte for byte, an edited line is stored without its indentation (docs/edit-api.md, GSC formatting). The status bar says "formatted". Line numbers and a current-line band. Highlighting for GSC-like code (keywords, built-ins, `path::func` references, strings, comments, numbers, `#include`) and for cfg (commands, strings, comments). The monospace font is the first installed of Cascadia Mono, Consolas, JetBrains Mono and DejaVu Sans Mono. Find (Ctrl+F) and replace (Ctrl+H) can use regular expressions and match case, and every match is marked. Edits go to the backend after a 400 ms pause, and at once before undo, save or switching assets. CRLF files keep CRLF. |
| Table | stringtable | Editable grid. Columns can be resized. Insert row (Ctrl+Shift+Enter) and Remove row (Ctrl+Shift+Delete) use `add_row` / `remove_row`. Edited cells are drawn in the accent colour. A cell whose string is shared carries a link marker; its tooltip and the line under the grid say what it is shared with. |
| Localize | localize | Every entry of the zone as Key / Value / Shared with, with a filter over keys and values. Values can be edited. A value the zone stores once for several keys carries a link marker, the third column names the other keys, and the line under the table repeats it for the selected entry. The selected entry is scrolled into view. |
| Image | image (top-level and inline) | Fit, 1:1 and wheel zoom around the cursor; drag to pan. Channel buttons for RGB, R, G, B and A, and alpha blend over a checkerboard. The info panel shows format, size, mips, cube, semantic, where the pixels live (inline, deferred, pak slot N entry M, or not in this zone, with the reason) and notes. Export PNG. Import PNG/DDS runs `replace_image`, also for pixels streamed from a .pak (the save writes the .pak beside the zone). A file of another size is refused, except for a streamed image: the view then asks whether to change the image's size (from W x H to the file's), and on Yes runs `replace_image(..., resize=True)` (docs/edit-api.md) and shows the image at its new size. For images the API cannot replace (formats it cannot encode yet, pixels in another zone) the panel shows the API's reason instead of the button. |
| Geometry | gfx_map (world), col_map (brushes and collision triangles), xmodel (LOD0, top-level and inline) | Two renderers behind a **Shaded / Wireframe** toggle, both with the same orbit camera (left drag orbits, right or middle drag pans, the wheel zooms, F frames everything, Home resets). **Wireframe** is rasterised with numpy (no OpenGL, so it works offscreen and on any GPU), with depth cue, ground grid and an axis gizmo; while dragging it draws a 40k-edge subset, and on Nuketown it draws about 240k edges in roughly 100 ms per full-quality frame and 30 to 45 ms while dragging. **Shaded** draws the models and the world with their materials' colour-map textures (decoded through the export image path, mip-mapped GL textures, UVs from the parsed vertices) and a two-sided lambert light, on the GPU. It renders each frame into an offscreen framebuffer and blits the result, so it runs the same interactively and in a headless screenshot; it is not a `QOpenGLWidget`, which does not present reliably offscreen. The colour maps are decoded on a worker so the view stays responsive, and a texture that cannot be decoded (streamed from a `.pak` that is not present, a format not yet decodable, pixels in another zone) falls back to a flat shade. On a world the static models are placed with their own textures; collision has a mode box (brushes, triangles or both). If OpenGL is unavailable the Shaded toggle refuses and the view stays on wireframe. |
| Fields | every asset | The schema fields (`fields()`, the schema's `to_dict`) as a tree that builds branches as you expand them. Integers are shown in decimal and hex. There is a filter over the loaded branches. Scalar fields of the asset's struct and its sub-structs are editable (double-click or F2) through `set_field`, and so are those of every node nested in the asset (a material inside a model, a pass inside a technique, a cell of a table), through the `"child/"` and `"child[i]/"` path steps: integers in decimal or 0x hex, range checked for the field's type; floats; vectors and small arrays as space-separated values; enums by name or number. Pointers and counts carry a lock; their tooltip says why they stay read-only (`field_info`). Refused input is shown under the tree with the reason; nothing changes. |
| Hex | every asset with a byte span (inline ones too, through the edit API) | Offset (zone offset, or relative), 16 bytes in two groups and ASCII. Only the visible rows are painted, so a 15 MB GfxWorld span opens instantly. Click, drag or shift-click to select; Ctrl+G goes to an offset; Ctrl+C copies the selection as hex. The status bar inspects the byte at the cursor (u8, u16, u32, f32, big-endian). |

## Editing, undo, changes, saving

- Every edit goes through the backend as one operation. Ctrl+Z and Ctrl+Shift+Z (or Ctrl+Y)
  undo and redo through the backend in every view, and jump to the asset they touched.
- **Shared strings.** Editing a value that other keys or cells share always asks first, in a
  small dialog: **Edit all sharing keys** (A) changes the stored string, so every key that
  reads it changes; **Split this key** (S) gives this key its own copy. It starts on the last
  choice (Enter takes it, Esc cancels and changes nothing). The Changes panel says which
  happened (`share=all ... (also MENU_PLAYER_MATCH_CAPS)` or `share=split ...`). After an
  "all" edit every key it changed is marked edited in the tree (and counted in the unsaved
  strip) from the Change's `also`, whether or not the localize view was ever opened, and the
  localize view colours them when it is opened later.
- **Changes** (Ctrl+Shift+D) lists each edit with its asset, type and a summary (`text +3
  -1`, the cell, a field path, or the API's detail, for example how a shared string was
  edited). The
  right side shows the selected change as a unified diff with added and removed lines
  shaded. Double-click a change to open its asset.
- **Save As** (Ctrl+Shift+S) writes a new file through `Document.save(path, verify=True)` on
  a worker thread. Save (Ctrl+S) on a zone that has not yet been saved to a new file opens
  Save As; after that it writes to that same new file. The report dialog shows the path,
  size, assets checked and changed, and whether verification passed, with any problems.
  For a signed zone that is no longer byte-identical it also says: "The console signature
  no longer matches this file. It will load only on a client with the signature check
  patched out."
- Closing a zone or the window with unsaved edits asks first.

## Keyboard

The command palette (Ctrl+Shift+P) lists every action with its shortcut, plus the actions
of the current view, and filters them with a fuzzy match. Quick open (Ctrl+P) does the same
for asset names. Help > Keyboard Shortcuts (F1) shows the full list.

| Keys | Action |
|---|---|
| Ctrl+O, Ctrl+W, Ctrl+Q | Open, close zone, quit |
| Ctrl+S, Ctrl+Shift+S | Save (to the new file), Save As |
| Ctrl+Z, Ctrl+Shift+Z / Ctrl+Y | Undo, redo |
| Ctrl+P, Ctrl+Shift+P | Quick open asset, command palette |
| Ctrl+Shift+F | Search in zone (names, rawfile text, stringtable cells, localize values) |
| Ctrl+Shift+D, Ctrl+J | Changes panel, toggle the bottom panel |
| Ctrl+1, Ctrl+L | Focus the asset tree, the asset filter |
| Ctrl+Tab, Ctrl+Shift+Tab | Next, previous zone |
| Alt+1 ... Alt+7 | Text, Table, Localize, Image, Geometry, Fields, Hex view |
| Ctrl+F, Ctrl+H, F3, Shift+F3, Esc | Find, replace, next, previous, close the find bar |
| Ctrl+G, Ctrl+C | Hex: go to offset, copy as hex |
| F, Home | Geometry: frame all, reset camera |
| F1 | Keyboard shortcuts |

## Look

There is a dark theme (the default) and a light theme, under View > Theme; the choice is
remembered. All colours are named tokens in `gui/theme.py` (`Theme`), turned into a
QPalette plus one stylesheet. Custom-painted widgets (gutter, hex, image, mesh) read the
same tokens. There is one accent colour, a desaturated amber, used for selection and focus
only. The surfaces are flat with 1px dividers and 1px splitter handles. The toolbar, view and
per-asset-type icons are monochrome line drawings made with QPainter (`gui/icons.py`);
`icons.type_icon(type_name)` maps each asset type to one, with a generic fallback.

The application mark is a square with a bevelled corner and a geometric "T5" cut out of it:
`gui/resources/opent5.svg`, plus `opent5.ico` at 16, 24, 32, 48, 64 and 256 px, rendered
with Qt. `icons.write_app_icon()` regenerates both.

## Screenshots

    QT_QPA_PLATFORM=offscreen .venv/bin/python tools/screenshots.py [--only NAME ...]

This renders every view offscreen against mp_nuked, patch_mp and ui_mp (read through `.env`)
into `out/screenshots/`, which git ignores. It takes about 35 s:

| File | Shows |
|---|---|
| `00_start_dark` | start page |
| `01_main_dark`, `01_main_light` | main window, tree, zone summary |
| `02_code_gsc_find_dark`, `02_code_gsc_replace_light` | `_globallogic.gsc` with the find bar, and with replace |
| `03_code_cfg_dark` | a cfg file |
| `04_stringtable_dark`, `04_stringtable_light` | `mp/mapstable.csv` |
| `05_localize_dark`, `05_localize_ui_dark` | localize entries (patch_mp, ui_mp) |
| `06_image_dxt_dark`, `06_image_normal_dark`, `06_image_alpha_dark`, `06_image_blend_light` | DXT1 texture, normal map, alpha channel, alpha over the checkerboard |
| `07_world_wire_dark`, `07_world_wire_light`, `07_collision_dark`, `07_xmodel_dark`, `07_map_ents_dark` | Nuketown world, collision, a model, the entity string (wireframe) |
| `07_world_shaded_dark`, `07_xmodel_shaded_dark` | the shaded, textured GPU renderer: Nuketown with its static models, and a model, both with colour maps and lambert lighting (rendered offscreen into a framebuffer) |
| `08_hex_dark`, `08_hex_light` | hex view |
| `09_fields_dark` | fields of a weapon |
| `10_search_dark` | search results |
| `11_palette_dark`, `11_quick_open_dark` | command palette, quick open |
| `12_changes_diff_dark`, `12_changes_diff_light` | Changes panel after three edits |
| `13_save_report_dark` | report of a real, verified Save As (written to a temporary folder and deleted) |
| `14_about_dark`, `14_about_light`, `15_shortcuts_dark` | About (with the GPL notice), shortcuts |
| `16_shared_localize_dark`, `16_share_choice_dark`, `16_share_choice_light`, `16_shared_changes_dark` | code_post_gfx_mp: `MPUI_PLAYER_MATCH_CAPS` with its shared marker and note, the edit choice, and the Changes panel after an "all" and a "split" edit |
| `17_loading_dark` | code_post_gfx_mp captured while it loads (loading panel and status bar) |
| `18_unsaved_dark`, `18_unsaved_light`, `19_saving_dark` | unsaved strip, tab and title; a Save As in progress |
| `20_fields_edit_dark`, `20_fields_edit_light` | a material's fields after one edit, with the locked counts and pointers |
| `20_fields_nested_dark` | a model's fields with a field of its first material (`materials[0]/material/info.sortKey`) edited, and the Changes panel |
| `21_image_resize_dark`, `21_image_resize_changes_dark` | mp_nuked's male mannequin head (streamed, 128 x 256) imported at 256 x 512, and the Changes panel with the new size, the parts and the entry added (nothing is saved) |
| `22_shared_tree_dark` | code_post_gfx_mp after a share="all" edit of `MPUI_PLAYER_MATCH_CAPS` made from the Fields view: both keys marked in the tree |
| `23_thumbnails_dark`, `23_thumbnails_light` | the asset tree's image thumbnails and per-type icons, at mp_nuked's streamed colour maps |

## Tests

`tests/test_gui_core.py` covers the theme tokens, the icon and ICO, the tree model, the
filter and the edit markers, search, the changes and undo mapping, the editor's find and
replace, CRLF round trips, the highlighter, the palette's fuzzy ranking and the diff lines.
It runs against a fake backend, plus one `zones` test on patch_mp.
`tests/test_gui_edit.py` covers the shared-string markers and the choice (all, split,
cancel, the default), the unsaved title, tab and strip and Discard, the staged progress,
the loading page, formatted GSC (shown indented, stored minified, off again) and field
editing (input parsing by type, locked counts, refused input, fields of nested nodes), and the
tree marking every key of a share="all" edit without the localize view.
`tests/test_gui_hex.py`, `tests/test_gui_image.py` and `tests/test_gui_mesh.py` cover the
custom views. `test_gui_mesh.py` also covers the shaded renderer: the texture-sorted scene
build, a textured quad rendered through OpenGL (skipped where no GL context is available),
the wireframe fallback when GL is missing, and, slow-marked, the shaded Nuketown world.
`test_gui_core.py` covers the per-type icons and the tree's thumbnail scaling and lazy
requests. They are plain pytest with an offscreen QApplication (pytest-qt is not used); the
fast ones take a few seconds in total.

## Open items

- The menu views show no menu source: T5 menus are compiled, so there is none to show.
- The shaded renderer draws colour maps with a lambert light only: no normal or specular
  maps, no lightmaps and no transparency sorting, so glass and foliage are drawn opaque.
