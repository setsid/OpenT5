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
them. Before a string is changed (`set_localize`, `set_cell`, removing a row), every other
string field that points at it gets its own inline copy of the old text, so an edit changes
only the asset it names. The `Change.detail` says when that happened, and which other assets
received a copy. A non-string pointer into edited data is refused with `EditError`.

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
from the DDS. Streamed (.pak) images raise `EditError`: writing `.pak` files is not
implemented.

`mesh(kind, index)` returns `opent5.edit.Mesh` (positions, triangles, normals, uvs, groups
per triangle, notes).
