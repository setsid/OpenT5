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

- **Zone tabs.** Each open zone is a tab, marked with `*` while it has unsaved edits.
- **Asset tree** (left). The assets are grouped by type, with a count per type. It lists the
  zone's own asset list and also the assets loaded inside other assets (images inside
  materials, models, the clipMap's entity string), marked `inline`. Above the tree is a
  filter box: every word you type must match. Edited assets and their type rows are
  marked `*` in the accent colour.
- **Asset header and view bar** (right). These show the asset's name, type, list index,
  size and zone offset, then one button for each view the asset offers.
- **Bottom panel** (Ctrl+J), with two tabs: **Search** and **Changes**.
- **Status bar.** It shows the cursor or selection in the current view, then the zone
  name, the asset counts, whether the zone is signed and how many changes it has, and the
  backend.
- **Start page.** It lists recent files and every zone in the configured folders (disc,
  update, dlc). The folders are read on a worker thread.

Opening a zone runs on a worker thread, with its progress in the status bar. On mp_nuked
(35 MB file, 69 MB inflated) it takes about 3 s, and the window never stops responding for
more than about 60 ms.

## Views

| View | For | What it does |
|---|---|---|
| Text | rawfile (GSC, CSC, cfg, txt, script), clipMap / map_ents entity string | Line numbers and a current-line band. Highlighting for GSC-like code (keywords, built-ins, `path::func` references, strings, comments, numbers, `#include`) and for cfg (commands, strings, comments). The monospace font is the first installed of Cascadia Mono, Consolas, JetBrains Mono and DejaVu Sans Mono. Find (Ctrl+F) and replace (Ctrl+H) can use regular expressions and match case, and every match is marked. Edits go to the backend after a 400 ms pause, and at once before undo, save or switching assets. CRLF files keep CRLF. |
| Table | stringtable | Editable grid. Columns can be resized. Insert row (Ctrl+Shift+Enter) and Remove row (Ctrl+Shift+Delete) use `add_row` / `remove_row`. Edited cells are drawn in the accent colour. |
| Localize | localize | Every entry of the zone as Key / Value, with a filter. Values can be edited. The selected entry is scrolled into view. |
| Image | image (top-level and inline) | Fit, 1:1 and wheel zoom around the cursor; drag to pan. Channel buttons for RGB, R, G, B and A, and alpha blend over a checkerboard. The info panel shows format, size, mips, cube, semantic, where the pixels live (inline, deferred, pak slot N entry M, or not in this zone, with the reason) and notes. Export PNG. Import PNG/DDS runs `replace_image`; for images the API cannot replace (pixels streamed from a .pak, formats it cannot encode yet) the panel shows the API's reason instead of the button. |
| Geometry | gfx_map (world), col_map (brushes and collision triangles), xmodel (LOD0, top-level and inline) | Wireframe rasterised with numpy (no OpenGL, so it works offscreen and on any GPU), with depth cue, ground grid and an axis gizmo. Left drag orbits, right or middle drag pans, the wheel zooms, F frames everything, Home resets the camera. While dragging it draws a 40k-edge subset. On Nuketown it draws about 240k edges in roughly 100 ms per full-quality frame and 30 to 45 ms while dragging. Collision has a mode box: brushes, triangles or both. |
| Fields | every asset | The schema fields (`fields()`, the schema's `to_dict`) as a tree that builds branches as you expand them. Integers are shown in decimal and hex. There is a filter over the loaded branches. Read-only for now. |
| Hex | every asset with a byte span (inline ones too, through the edit API) | Offset (zone offset, or relative), 16 bytes in two groups and ASCII. Only the visible rows are painted, so a 15 MB GfxWorld span opens instantly. Click, drag or shift-click to select; Ctrl+G goes to an offset; Ctrl+C copies the selection as hex. The status bar inspects the byte at the cursor (u8, u16, u32, f32, big-endian). |

## Editing, undo, changes, saving

- Every edit goes through the backend as one operation. Ctrl+Z and Ctrl+Shift+Z (or Ctrl+Y)
  undo and redo through the backend in every view, and jump to the asset they touched.
- **Changes** (Ctrl+Shift+D) lists each edit with its asset, type and a summary (`text +3
  -1`, the cell, or the API's detail, for example when a shared string was split). The
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
only. The surfaces are flat with 1px dividers and 1px splitter handles. The toolbar and
view icons are monochrome line drawings made with QPainter (`gui/icons.py`).

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
| `07_world_wire_dark`, `07_world_wire_light`, `07_collision_dark`, `07_xmodel_dark`, `07_map_ents_dark` | Nuketown world, collision, a model, the entity string |
| `08_hex_dark`, `08_hex_light` | hex view |
| `09_fields_dark` | fields of a weapon |
| `10_search_dark` | search results |
| `11_palette_dark`, `11_quick_open_dark` | command palette, quick open |
| `12_changes_diff_dark`, `12_changes_diff_light` | Changes panel after three edits |
| `13_save_report_dark` | report of a real, verified Save As (written to a temporary folder and deleted) |
| `14_about_dark`, `14_about_light`, `15_shortcuts_dark` | About (with the GPL notice), shortcuts |

## Tests

`tests/test_gui_core.py` covers the theme tokens, the icon and ICO, the tree model, the
filter and the edit markers, search, the changes and undo mapping, the editor's find and
replace, CRLF round trips, the highlighter, the palette's fuzzy ranking and the diff lines.
It runs against a fake backend, plus one `zones` test on patch_mp.
`tests/test_gui_hex.py`, `tests/test_gui_image.py` and `tests/test_gui_mesh.py` cover the
custom views. They are plain pytest with an offscreen QApplication (pytest-qt is not used)
and take about 3 s in total.

## Open items

- Field editing (`set_field`) is not offered in the Fields view yet; it is read-only.
- The menu views show no menu source: T5 menus are compiled, so there is none to show.
- Geometry is wireframe only. It does not draw the world's static models.
