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
