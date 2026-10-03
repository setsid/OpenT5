# Command line

    .venv/bin/python -m opent5 COMMAND ...      (or `opent5 COMMAND ...` once installed)

Every command runs headless. Add `--json` to any command for one JSON object on stdout;
without it the output is plain text for people. Errors go to stderr (and, with `--json`, into
the object as `"ok": false, "error": "..."`).

| Exit code | Meaning |
|---|---|
| 0 | done |
| 1 | failed: the zone does not read, an edit is refused, verification found a problem, or an output path is not allowed. The message says what was expected and what was found |
| 2 | usage: unknown command, missing argument, `replace` without ASSET FILE pairs |

Where output may go. No command writes into the game folders named in `.env`
(`OPENT5_ZONES`, `OPENT5_PATCH_ZONES`, `OPENT5_DLC_ZONES`, and the folder of `OPENT5_ELF`), and
no command writes over the zone it reads. Such a path is refused before anything is written.

`opent5 --version` prints the version and the licence notice, with its line breaks.

Assets on the command line (`ASSET`): an index in the zone's asset list (`40`), a name
(`mp/gametypestable.csv`), or `type:name` when a name is used by more than one type
(`image:~-gus_art_streetsigns_c`). Names also find assets loaded inside other assets (an
image inside a material, a material inside the world), which have no index of their own.

A ZONE argument may be a path or a bare zone name such as `mp_nuked`, which is looked up in
the folders named in `.env` (base disc first, then the update, then the DLC).

## info

    opent5 info ZONE [--json]

What the zone is and what it holds.

    $ opent5 info patch_mp.ff
    patch_mp  (.../patch_mp.ff)
      file      1196128 bytes, sha1 499ce6547392fe44ae0a52186712591e770e19e2
      signed    yes (console signature at 0x3c)
      content   3715811 bytes in 77 chunks
      blocks    0xa98 0x0 0x0 0x72580 0x2c46d1 0x0 0xb7b4
      assets    1337 in the asset list, 1087 loaded inline
      parse     exact
      types
           445  localize
           ...

`signed` says the header carries a field that decodes under the console key. An edited zone
still carries the original field, which no longer matches its content.

```json
{
  "ok": true,
  "zone": "patch_mp",
  "path": ".../patch_mp.ff",
  "bytes": 1196128,
  "sha1": "499ce6547392fe44ae0a52186712591e770e19e2",
  "signed": true,
  "content_bytes": 3715811,
  "chunks": 77,
  "block_sizes": [2712, 0, 0, 468352, 2901713, 0, 47028],
  "assets": 1337,
  "inline_assets": 1087,
  "type_counts": {"localize": 445, "xanim": 328, "...": 0},
  "parse_exact": true,
  "parse_problems": [],
  "seconds": 0.9
}
```

## list

    opent5 list ZONE [--type TYPE] [--match GLOB] [--inline] [--json]

The asset list, optionally only one type, only names matching a glob (case-sensitive,
`fnmatch` rules), and with `--inline` also the assets loaded inside other assets.

    $ opent5 list patch_mp.ff --type stringtable
         8  stringtable             46  mp/defaultstringtable.csv
        40  stringtable           4423  mp/gametypestable.csv
        ...
    17 asset(s)

Columns: index, type, size in content bytes, name.

```json
{
  "ok": true,
  "zone": "patch_mp",
  "count": 1,
  "assets": [
    {"index": 40, "type": "stringtable", "name": "mp/gametypestable.csv", "size": 4423,
     "offset": 324206, "editable": ["table", "fields", "hex"]}
  ]
}
```

An inline asset has `"index": ["inline", <type number>, <name>]` and `"parent"`, the index of
the top-level asset that loads it. `editable` names the editors that apply (docs/edit-api.md).

## extract

    opent5 extract ZONE OUTDIR [--type TYPE] [--name GLOB] [--previews] [--json]

Without `--type` / `--name`: the full export of `opent5.export` (docs/extract.md): images as
PNG, rawfiles, stringtables as CSV, localize as JSON, world and models as OBJ, map entities,
and every other asset as JSON, with `manifest.json`. `--previews` also renders previews.

With a filter: only the selected assets, one file each:

| Type | File |
|---|---|
| rawfile | `rawfiles/<name as stored>`, the bytes as the game reads them (scripts inflated, without their final NUL) |
| stringtable | `stringtables/<name>.csv` |
| localize | `localize/<NAME>.txt`, the value |
| image | `images/<name>.png`, level 0 (from the zone, or from the `.pak` beside the zone) |
| map_ents, col_map | `map_ents/<name>.ents`, the entity string |
| anything else | `<type>/<name>.json`, the asset's typed fields |

These are the files `replace` takes back.

    $ opent5 extract mp_nuked.ff out/nuked --type col_map_mp
    $ opent5 extract mp_nuked.ff out/nuked --type image --name '*streetsigns*'

```json
{
  "ok": true, "zone": "mp_nuked", "outdir": "out/nuked", "mode": "selected", "assets": 1,
  "files": ["out/nuked/map_ents/mp_nuked.d3dbsp.ents"],
  "failures": []
}
```

Full export: `{"ok", "zone", "outdir", "mode": "full export", "files", "failures", "manifest"}`
(`files` and `failures` are counts; the manifest lists them).

## replace

    opent5 replace ZONE ASSET FILE [ASSET FILE ...] -o OUT [--no-verify] [--share split|all] [--json]

Replaces assets with the content of files and saves a new zone. Every length is allowed:
the zone is re-laid out and every offset pointer remapped (docs/edit-api.md, Saving).

| Asset type | FILE |
|---|---|
| rawfile | the new file (any bytes; a .gsc / .csc is compressed the way the game stores scripts) |
| stringtable | CSV with the same number of columns; rows may be added or removed at the end, and every changed cell is rehashed and the index re-sorted |
| localize | the new value (one final newline is dropped) |
| image | PNG of the same width and height, or a DDS of the same format, size and at least the same mip count; only for images whose pixels are in the zone (inline or deferred). Streamed images (`.pak`) are refused: writing `.pak` files is not implemented |
| map_ents, col_map | the new entity string |

Shared strings. The zone linker stores identical strings once, so several localize keys (or
stringtable cells, or other string fields) can read the same stored text; for example
`MENU_PLAYER_MATCH_CAPS` and `MPUI_PLAYER_MATCH_CAPS` share "PLAYER MATCH" in
code_post_gfx_mp. `--share split` (the default) changes only the named asset: the other
fields keep the old text as their own copy. `--share all` changes the stored string, so
every field that shares it changes too (fields that read only the end of it, or that are an
asset's name, keep the old text). Each change's detail says which happened (`share=split` or
`share=all`). It applies to localize values and stringtable cells.

    $ opent5 replace code_post_gfx_mp MPUI_PLAYER_MATCH_CAPS new.txt --share all -o out/cpg.ff
      change: asset 4162 localize share=all: the stored string changed for 2 field(s) (also MENU_PLAYER_MATCH_CAPS)

After saving, the file is verified (unless `--no-verify`): reopened, parsed exactly, every
asset not edited identical to the source (or identical apart from remapped pointers that
each name the same thing), every edited asset read back as edited.

    $ opent5 replace mp_nuked.ff col_map_mp:maps/mp/mp_nuked.d3dbsp moved.ents -o out/mp_nuked.ff
    mp_nuked -> out/mp_nuked.ff (36349760 bytes, sha1 3740413f440542d7c02eb85395aeb753eaa8d94e)
      replaced col_map_mp maps/mp/mp_nuked.d3dbsp from moved.ents (text)
      change: asset 407 text
      verified: 529 assets; 455 identical, 73 identical apart from 16512 remapped pointers, 1 edited and read back
      note: The console signature at 0x3c is the original one and no longer matches the content: ...

```json
{
  "ok": true,
  "zone": "mp_nuked",
  "output": "out/mp_nuked.ff",
  "bytes": 36349760,
  "sha1": "3740413f440542d7c02eb85395aeb753eaa8d94e",
  "source_sha1": "6d5a4a0e5c031c0d8fcd555413730b014e9c0c5d",
  "replaced": [
    {"asset": {"index": 407, "type": "col_map_mp", "name": "maps/mp/mp_nuked.d3dbsp", "...": ""},
     "file": "moved.ents", "as": "text"}
  ],
  "changes": [{"index": 407, "kind": "text", "detail": ""}],
  "verified": true,
  "problems": [],
  "verification": {
    "assets_checked": 529, "identical": 455, "identical_after_pointer_remap": 73,
    "pointers_checked": 16512, "edited": 1, "edited_read_back": 1,
    "shared_string_copies_in": []
  },
  "signature_note": "The console signature at 0x3c is the original one and no longer matches ..."
}
```

`changes[].detail` says when a string another asset shared was given its own copy (the
zone's linker shares identical strings; an edit changes only the asset it names).
`shared_string_copies_in` lists the assets that received such a copy.

## unpack, pack

    opent5 unpack ZONE DIR [--json]
    opent5 pack DIR -o OUT [--keep-size] [--json]

Container level only: `unpack` writes the header, the decompressed content and a chunk
manifest to DIR; `pack` builds a fastfile from such a folder (the zone length field is
derived from the content unless `--keep-size`). An unmodified folder packs to the original
file byte for byte. JSON: `{"ok", "zone", "directory", "chunks", "content_bytes", "log"}` and
`{"ok", "directory", "output", "bytes", "sha1", "log"}`.

## verify

    opent5 verify ZONE [--against SOURCE] [--json]

Checks a zone: the container (every chunk decrypts and inflates, four terminators, the
length field), an exact parse, and that the parse writes back to the same content. With
`--against`, compares it asset by asset with its source and lists what changed. A zone
that passes exits 0 whether or not it differs from the source.

    $ opent5 verify out/mp_nuked.ff --against mp_nuked.ff
    mp_nuked: OK (sha1 3740413f440542d7c02eb85395aeb753eaa8d94e)
      container: 1405 chunks, 68848473 content bytes, 4 terminators
      parse: exact
      rewrite from the parse: identical
      against mp_nuked.ff: 455 identical, 73 identical apart from remapped pointers, 1 changed
        changed: 407 col_map_mp maps/mp/mp_nuked.d3dbsp: expected 2451471 bytes as in the source, found 2451487 at 0x1e82252

```json
{
  "ok": true,
  "zone_path": "out/mp_nuked.ff",
  "sha1": "...",
  "checks": {
    "container": {"chunks": 1405, "content_bytes": 68848473, "terminators": 4,
                  "padded_as_original_writer": true},
    "parse_exact": true,
    "rewrites_identically": true
  },
  "zone": "mp_nuked",
  "signed": true,
  "against": {
    "source": "mp_nuked.ff", "source_sha1": "...", "comparable": true,
    "identical": 455, "identical_after_pointer_remap": 73, "pointers_checked": 16512,
    "changed": [{"index": 407, "type": "col_map_mp", "name": "maps/mp/mp_nuked.d3dbsp",
                 "reason": "expected 2451471 bytes as in the source, found 2451487 at 0x1e82252"}]
  },
  "problems": []
}
```

## rebuild

    opent5 rebuild ZONE -o OUT [--recompress] [--json]

Parses the zone, writes every asset back from its parsed form, and packs the container. For
an unmodified zone the result is byte-identical to the source, and the command says so (and
exits 1 if it is not). Chunks whose content is unchanged are carried as stored;
`--recompress` deflates every chunk afresh instead, which also gives the identical file.

    $ opent5 rebuild mp_nuked.ff -o out/mp_nuked.ff
    mp_nuked: 529 assets parsed and written back
      content  identical
      file     byte-identical (1404 chunks carried, 0 deflated afresh)
      source   6d5a4a0e5c031c0d8fcd555413730b014e9c0c5d  mp_nuked.ff
      output   6d5a4a0e5c031c0d8fcd555413730b014e9c0c5d  out/mp_nuked.ff

```json
{
  "ok": true, "zone": "mp_nuked", "source": "mp_nuked.ff",
  "source_sha1": "6d5a4a0e5c031c0d8fcd555413730b014e9c0c5d",
  "output": "out/mp_nuked.ff", "sha1": "6d5a4a0e5c031c0d8fcd555413730b014e9c0c5d",
  "bytes": 36349760, "assets": 529, "content_identical": true, "file_identical": true,
  "chunks_carried": 1404, "chunks_deflated": 0, "seconds": 4.4
}
```

## Signatures

Every retail zone is signed at 0x3c and the signature cannot be regenerated. A zone whose
content changed loads only on a client with the signature check patched out; `replace` says
so in `signature_note` every time.

## convert

Convert a map built with the PC Mod Tools into a PS3 zone: the base zone's world assets
(gfx_map, col_map_mp and its entities, com_map, game_map_mp) are replaced with the PC
map's, everything else is kept. The base is only read; the result goes to OUTDIR.
Details, supported gametypes and limits: docs/convert.md.

    opent5 convert PC_MAP.ff --base mp_nuked -o OUTDIR [--lighting flat|sunlit|keep]
        [--name mp_NAME [--copy-pak]] [--register --patch-mp PATCH_MP.ff
        [--title TEXT] [--description TEXT] [--ui-slot N]] [--json]

`--name` gives the map its own zone name (`mp_NAME.ff`, every internal occurrence renamed;
docs/research/map-registration.md lists them); `--copy-pak` writes `mp_NAME.pak` beside it.
`--register` is opt-in and writes a NEW copy of patch_mp.ff with one map-table row added: the
game offers maps only from that table, so a custom map cannot be started without it (see
docs/convert.md, the own-files rule, and docs/demo-box-named.md). The source patch_mp.ff is
only read.
