# Editing API (contract shared by the CLI and the GUI)

`opent5.edit` is the one layer both front ends use. Neither front end touches
`opent5.xfile` or `opent5.container` directly for anything that changes a zone.
This file is the contract; the implementation lives in `src/opent5/edit/`.

## Document

    from opent5.edit import Document

    doc = Document.open(path)            # .ff path (or bytes + name)
    doc.path, doc.zone_name              # source path, zone name from the header
    doc.signed                           # True when the header carries a console signature
    doc.assets                           # list[AssetRef]
    doc.type_counts()                    # {type name: count}
    doc.asset(index) -> AssetRef
    doc.find(name, type=None) -> AssetRef | None
    doc.search(text, names=True, contents=True) -> list[SearchHit]
                                          # contents = rawfile text, stringtable cells, localize values

`AssetRef`: `index`, `type` (enum value), `type_name`, `name`, `size` (file bytes),
`editable` (which editors apply: "text", "table", "localize", "image", "entities",
"geometry", "hex").

## Reading

    doc.text(index) -> str               # rawfile (GSC/CSC decompressed), menu source if any,
                                          # map_ents entity string
    doc.table(index) -> list[list[str]]  # stringtable rows
    doc.localize(index) -> (key, value)
    doc.image(index) -> ImageData        # info (format, width, height, mips, kind, where the
                                          # pixels live) and rgba (numpy uint8 h x w x 4,
                                          # first face / slice / mip 0); None pixels with a
                                          # reason when the pixels are in another zone
    doc.mesh(kind, index=None) -> Mesh   # kind "world" | "collision" | "model"; positions
                                          # (n,3) float32, triangles (m,3), optional normals/uvs
    doc.raw(index) -> bytes              # the asset's file span, for the hex view
    doc.fields(index) -> dict            # to_dict() of the asset's schema view

## Editing

Every edit is an operation recorded on the document; nothing is written until
`save`.

    doc.set_text(index, text)            # any length; GSC/CSC recompressed exactly as the
                                          # game stores them; sizes and pointers remapped
    doc.set_cell(index, row, col, text)  # any length; cell hash recomputed, index re-sorted
    doc.set_localize(index, value)       # any length
    doc.replace_image(index, rgba | dds_bytes)  # same width/height/format; re-encoded
    doc.set_field(index, path, value)    # schema field by dotted path (non-count, non-pointer)

    doc.dirty -> bool
    doc.changes() -> list[Change]        # asset index, kind, before, after (for diff views)
    doc.undo(); doc.redo(); doc.can_undo; doc.can_redo

## Saving

    report = doc.save(new_path, verify=True)

- Refuses to write over the source file, or over any file in the configured game
  folders.
- Builds content from the edits (remap for any length change), packs the
  container (unchanged chunks carried, IV chain rebuilt, signature bytes copied).
- `verify=True` reopens the written file, parses every asset exactly, checks
  every unchanged asset is byte-identical to the source and every edited asset
  reads back as edited.
- `SaveReport`: `path`, `bytes`, `assets_checked`, `assets_changed`, `verified`,
  `problems`, and `signature_note`, which always says when the console signature
  no longer matches: the file loads only on a client with the signature check
  patched out.

## Errors

`EditError(message)`: what was expected, what was found, and the asset index
and offset where relevant.

## Additions (implementation, 2026-10-03)

Everything above is implemented as written in `src/opent5/edit/`. These are additions; no
signature above changed.

Inline assets. Most images, materials, models and the entity string of a map are loaded
inside other assets and have no index in the asset list. `doc.inline_assets` lists them as
`AssetRef`s whose `index` is the key `("inline", type, name)` (the same key the GUI uses).
Every reader and editor takes either an int index or such a key, so an image inside a
material is read with `doc.image(key)` and replaced with `doc.replace_image(key, ...)`.
`AssetRef` also has `file_start` (content offset of the span) and `parent` (for an inline
asset, the index of the top-level asset that loads it); `ref.inline` and `ref.key`.

    doc.inline_assets -> list[AssetRef]
    doc.xfile                              # the live parse (nodes reflect the edits)
    doc.add_row(index, values=None, at=None) -> int   # stringtable; returns the row number
    doc.remove_row(index, row) -> list[str]           # stringtable; returns the removed cells
    doc.build() -> bytes                   # the edited content, re-laid out (no file)
    doc.pak_dirs() -> list[Path]           # where streamed pixels are looked up

`add_row` / `remove_row` change the cell count: the stringtable is written again by its
handler, everything after it re-laid out and every offset pointer remapped (the node rewrite,
`opent5.xfile.remap.Rewrite`). Both are undoable like every other edit.

Text: a col_map asset's `text` / `set_text` is its entity string (the MapEnts it loads), so
the clipMap is the natural handle for "the map's entities".

`set_field(index, path, value)`: `path` is a field of the asset's own struct
(`"lodInfo[0].dist"`), or of a sub-struct as `"key:field"` / `"key[i]:field"`.

Shared strings. The zone linker stores identical strings once and points later fields at
them: in code_post_gfx_mp, `MENU_PLAYER_MATCH_CAPS` and `MPUI_PLAYER_MATCH_CAPS` read the
same stored "PLAYER MATCH" (1474 of the zone's 7807 localize values share their string with
another field). The menu button reads `MPUI_PLAYER_MATCH_CAPS`, so an edit of the other key
alone changes nothing on screen.

    doc.shared_with(index, row=None, col=None, field=None) -> list[SharedField]
    doc.set_localize(index, value, share="split")       # or share="all"
    doc.set_cell(index, row, col, text, share="split")  # or share="all"
    doc.index_shared_strings() -> int      # build the index now (else on first use)

`shared_with` lists the other fields holding the same stored string as a localize value, a
stringtable cell (`row`, `col`), or a string `field` of the asset's node. `SharedField` has
`index` (asset index or inline key), `type_name`, `name` (for localize, the key), `field`
("value", "row 2, column 1", or "StructType.key"), `owner` (the field that stores the
string; the others point at it), `suffix` (points into the middle of it), and `label`.

`share="split"` (the default, the behaviour before this addition): every other string field
pointing at the string gets its own inline copy of the old text, or, when the edited field
is the one pointing, it becomes inline; only the named key changes. `share="all"`: the stored
string itself takes the new text (any length; the zone is re-laid out and every pointer
remapped), so every field pointing at its start changes too; stringtable cells among them
are rehashed and their table's index re-sorted. Fields that point into the middle of the
string (a suffix) or that are an asset's name keep the old text as their own copy; when the
stored string is itself an asset's name, `share="all"` raises `EditError` (it would rename the
asset). Removing a row always splits. The `Change.detail` starts with `share=split` or
`share=all` whenever the string was shared and names the other fields, and verification
reads every key that changed back from the saved file. Undo and redo cover both. A
non-string pointer into edited data is refused with `EditError`.

Progress. `Document.open(path, progress=fn)` and `doc.save(path, progress=fn)` call
`fn(stage, done, total)` as real work is done: "Decrypting and inflating" (chunks),
"Parsing assets" (assets); on save "Writing assets", "Remapping pointers" (pointers),
"Compressing", "Encrypting" (chunks), "Writing the file", then "Verifying: decrypting and
inflating", "Verifying: parsing" and "Verifying assets". The same optional parameter exists
on `container.fastfile.read_fastfile` / `assemble`, `container.zone.Zone.open` / `build` /
`verify`, `xfile.parse`, and `xfile.remap.Rewrite` / `build`; without it nothing changes.

Fields. `doc.field_info(index, path)` describes what `set_field` would edit: `type` ("f32",
"vec3", "u16[4]"), `role` (None, "ptr", "count", "scrstr", "enum", "flags"), `names` (enum
values), `value`, `editable`, and `reason` when it is not (pointers and counts change layout;
fixed-size text and byte fields are not edited this way). An unknown path raises
`EditError`.

GSC formatting. `opent5.edit.gscformat` (pure functions, no Qt) shows a script indented and
gives back the stored form:

    format_script(text) -> str        # leading tabs from the brace structure, nothing else
    unformat_script(text) -> str      # leading spaces and tabs removed from every line
    round_trips(text) -> bool         # unformat_script(format_script(text)) == text
    check_consistent(text) -> list[str]   # unbalanced braces, misplaced closing braces
    is_script(name) -> bool           # .gsc / .csc

Evidence for the save policy: the scripts are stored with no indentation (patch_mp,
`maps/mp/animscripts/dog_combat.gsc` begins `main()\n{\ndebug_anim_print(`, every line at
column 0), but some lines hold only whitespace (patch_mp, `maps/mp/_burnplayer.gsc` and
`maps/mp/_tabun.gsc` each have a line that is a single tab before its CRLF). So the policy is:
the formatter changes only the leading whitespace of lines with code on them; lines inside a
`/* */` comment and lines holding only whitespace are kept as they are in both directions;
`unformat_script` removes leading spaces and tabs from every other line. An unedited
formatted script therefore saves byte-identically, and an edited or new line is stored
without indentation, the way the game's own files are. Proof (`tools/gsc_format_all.py`, all
178 zones from `opent5.env.all_zones()`, one process per zone with a timeout): 4758 GSC / CSC
rawfiles (2496 distinct, 1,630,738 lines), `unformat_script(format_script(t)) == t` exactly for
every one; braces balance in every script except code_post_gfx `maps/_menus.gsc`, which
ships with one more opening brace than closing ones (its last function, `menuResponse`,
is never closed); the formatter shows it and saves it exactly all the same.

`Change` has `index` (and `key`, the same), `kind` ("text", "cell", "row_added",
"row_removed", "localize", "image", "field"), `before`, `after`, `detail`. `undo()` /
`redo()` return the Change they undid or redid.

`SaveReport` additionally has `sha1`, `identical` (byte-identical to the source) and
`details` (counts: identical assets, assets identical apart from remapped pointers, pointers
checked, edited assets read back, assets that received shared-string copies).
`signature_note` is set whenever the source is signed: when the output is identical it says
the signature still matches, otherwise that the file loads only with the check patched out.

`ImageData.info` keys: name, format, format_code, width, height, depth, mips, kind ("2d",
"cube", "volume"), pixels ("inline", "deferred", "pak", "elsewhere"), size, semantic, remap,
replaceable, not_replaceable (why), parts (streamed), source and notes (after decoding).
`replace_image` takes an RGBA array (h, w, 4) or (h, w, 3), a list of six for a cube map
(+X -X +Y -Y +Z -Z), or DDS bytes; mips are rebuilt with a box filter from RGBA, or taken
from the DDS. Streamed (.pak) images are replaced too (below).

Streamed images (.pak; docs/research/pak.md). The signature gains one keyword argument, the
two-argument call is unchanged:

    doc.replace_image(index, rgba | dds_bytes, allow_shared=False)
    doc.pak_edits() -> {(slot, entry): bytes}   # pending pak entries (undone ones left out)

For a streamed image `replace_image` encodes the full mip chain at the image's own size,
format and mip count, splits it into the mip range of each part record, and keeps the new
part bytes as pending pak edits; the zone itself does not change (same width, height and
format only). Parts in the level's own pak (`<zone>.pak`, slot 0) are always written. Parts
in a shared pak (images_low.pak, common.pak, ui_mp.pak, img_patch.pak) change that image in
every zone that uses the pak, so by default they are left as they are (the `Change.detail`
names them; for a usual image that is the 64 x 64 mip tail in images_low.pak, which then
still shows the old picture at a distance), and an image with no part in the level pak is
refused with `EditError`. `allow_shared=True` writes them too. `ImageData.info["parts"]`
lists each part with `slot`, `entry`, `width`, `height`, `mips`, `cumulative`, `pak` (file
name) and `shared`; `doc.image(index)` shows pending edits before saving. Undo and redo
apply to the pak edits like any other.

`save(path)` then also writes each edited pak beside the zone, `<stem of path>.pak` for the
level pak (the game opens `<zone>.pak` from the folder of `<zone>.ff`, so the two must be
copied together) and a shared pak under its own name. It never writes over a source pak
(`EditError`) or into a game folder. With `verify=True` each written pak is re-opened: header
fields as in the source, every entry not edited byte-identical over its whole span, every
edited entry holding the new bytes with zero padding; and every edited streamed image is
decoded from the written files and must equal the new pixels exactly. `SaveReport.details`
gains `paks` (per pak: `slot`, `source`, `path`, `bytes`, `sha1`, `entries`,
`edited_entries`, `shared`, `note` for a shared pak, `entries_checked`,
`entries_identical`, `verified`), `streamed_images_decoded` and `pak_note`. A save whose only
edits are streamed images writes a `.ff` byte-identical to the source (its signature still
matches).

The pak container on its own: `opent5.container.pak.Pak` (`open`, `from_bytes`, `entries`,
`read`, `replace`, `write`, `chunks`) and `compare(original, written, edited)`.

`mesh(kind, index)` returns `opent5.edit.Mesh` (positions, triangles, normals, uvs, groups
per triangle, notes).
