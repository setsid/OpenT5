# Writing every zone back (Phase 2, M3)

Status: every asset of all 178 zones re-serialises from its parsed node to its exact bytes, every
zone's content is rebuilt byte for byte (header included, derived from the written stream), and
every rebuilt content goes back through the container to the original `.ff`. mp_nuked is in
section 3.

Code: `src/opent5/xfile/` (`stream.py` XStream / XWriter, `model.py` parse / write /
write_asset, `handlers/`), `tools/rewrite_all.py` (this run). Companion: `walk-all.md` (the
parse side over the same 178 zones).

## 1. Design: one description, two directions

Each handler's `body(io, header, node)` is the type's struct loader written once against the
stream primitives. `io` is either an `XStream` (reading) or an `XWriter` (writing); the two
share push / pop / alloc / insert / RUNTIME reservation / pointer logging and differ only in
where loaded bytes come from:

| Primitive | Reading (`XStream`) | Writing (`XWriter`) |
|---|---|---|
| `load(size, node, key)` | read `size` file bytes, store `node[key]` | emit `node[key]` (its length must equal `size`, computed from bytes already written) |
| `items(size, n, node, key)` | one read, split into element nodes `{"raw": ...}` | emit the elements' `raw`, concatenated |
| `string(chunk, off, node, key)` | -1: read the NUL-terminated string into `node[key]` | -1: emit `node[key]` + NUL |
| `reserve(size, node, key)` | RUNTIME: no bytes; deferred: queue a `DeferredData` | RUNTIME: no bytes; deferred: queue `node[key]`'s bytes for the tail |
| `follows`, `ref`, `convert` | test and log the pointer field | the same, on the bytes being written (after any override) |
| `flush_deferred` | read the queued tail | emit the queued tail |

Every decision a body makes (counts, union tags, pointer tests) is computed from the chunk it
has just loaded or emitted, so the writer takes exactly the reader's path. Both keep the same
event log; for an unedited zone the writer's log equals the parser's record for record (checked
on all 178 zones, section 3), which also proves the block positions, alignments and pointer
fields agree.

Nodes are dicts: the raw bytes of every struct and array the loader reads (`"header"`, `"raw"`
per element, named keys for arrays and blobs), strings as Latin-1 text, child assets as child
nodes, alias references as `AssetLink`. Scalars live in those bytes; the few decoded values a
handler adds for convenience (image width, format...) are not written. Tier-1 nodes have typed
accessors and constructors (`RawFile.build`, `StringTable.build`, `LocalizeEntry.build`).

What a remap or an editor can drive:

- edit a node (bytes or strings); `write` re-lays the stream and derives the header (size and
  all seven block sizes, TEMP as high-water mark + 16), so a resized asset yields a consistent
  zone apart from offset pointers that pointed past it;
- `write(xfile, pointer_values={ordinal: value})` overrides pointer fields by ordinal (the n-th
  pointer field met, the same numbering as the parse log's POINTER records); the writer's own
  log (`Written.log`) gives every field's new output offset and every object's new block
  position for a second pass.

## 2. Per-type status

Every registered type (37: all 46 types except the nine with no loader) reads and writes
through the same body. Exercised by the 178 zones: all except xmodelpieces (no instance
anywhere; ELF only) and the GfxWorld fields no zone uses (waterBuffers, cullGroups); pixel
shaders, vertex shaders, map_ents and menus are exercised inline (inside techsets, clipMaps and
menu lists). Opaque data (shader programs, vertex and index buffers, pixels, sound data, path
nodes, collision trees) is carried as bytes.

## 3. Results

mp_nuked (disc, 68 848 457-byte stream, 529 assets): all 529 assets identical; content
identical with the header derived from the written stream; writer event log identical to the
parser's (340 198 records); the `.ff` rebuilt through `Zone.build` is byte-identical to the
disc file (1 404 chunks carried), and so is the build with every chunk deflated afresh
(`--recompress`). Parse 0.4 s, write 0.8 s, container 6.7 s.

<!-- REWRITE START -->
178 of 178 zones pass every check; 125327 of 125327 assets write back identically; 178 contents, 178 event logs and 178 .ff files identical; 7358 MB of stream; wall 158.7 s (parse 66.4 s and write 120.4 s summed over workers).

| Group | Zone | Assets | Assets identical | Content | Header | Event log | .ff | Parse s | Write s |
|---|---|---|---|---|---|---|---|---|---|
| disc | code_post_gfx | 6899 | 6899 | yes | yes | yes | yes | 0.24 | 0.21 |
| disc | code_post_gfx_mp | 8576 | 8576 | yes | yes | yes | yes | 0.34 | 0.31 |
| disc | common | 1689 | 1689 | yes | yes | yes | yes | 0.12 | 0.15 |
| disc | common_mp | 7286 | 7286 | yes | yes | yes | yes | 1.08 | 1.14 |
| disc | common_zombie | 3357 | 3357 | yes | yes | yes | yes | 0.43 | 0.51 |
| disc | creek_1 | 2998 | 2998 | yes | yes | yes | yes | 0.89 | 1.0 |
| disc | cuba | 2435 | 2435 | yes | yes | yes | yes | 1.34 | 1.4 |
| disc | dev | 70 | 70 | yes | yes | yes | yes | 0.0 | 0.0 |
| disc | dev_mp | 52 | 52 | yes | yes | yes | yes | 0.0 | 0.0 |
| disc | flashpoint | 2539 | 2539 | yes | yes | yes | yes | 1.16 | 1.01 |
| disc | frontend | 1320 | 1320 | yes | yes | yes | yes | 0.42 | 0.53 |
| disc | fullahead | 2145 | 2145 | yes | yes | yes | yes | 0.82 | 1.15 |
| disc | hue_city | 2321 | 2321 | yes | yes | yes | yes | 1.36 | 1.54 |
| disc | int_escape | 1104 | 1104 | yes | yes | yes | yes | 0.63 | 0.73 |
| disc | khe_sanh | 2397 | 2397 | yes | yes | yes | yes | 0.87 | 0.97 |
| disc | kowloon | 2433 | 2433 | yes | yes | yes | yes | 1.02 | 1.11 |
| disc | mp_array | 766 | 766 | yes | yes | yes | yes | 0.63 | 0.71 |
| disc | mp_cairo | 655 | 655 | yes | yes | yes | yes | 0.73 | 0.75 |
| disc | mp_cosmodrome | 765 | 765 | yes | yes | yes | yes | 0.77 | 0.82 |
| disc | mp_cracked | 758 | 758 | yes | yes | yes | yes | 0.9 | 0.78 |
| disc | mp_crisis | 642 | 642 | yes | yes | yes | yes | 0.73 | 0.63 |
| disc | mp_duga | 728 | 728 | yes | yes | yes | yes | 0.62 | 0.68 |
| disc | mp_firingrange | 532 | 532 | yes | yes | yes | yes | 0.56 | 0.6 |
| disc | mp_hanoi | 682 | 682 | yes | yes | yes | yes | 0.79 | 0.75 |
| disc | mp_havoc | 516 | 516 | yes | yes | yes | yes | 0.5 | 0.55 |
| disc | mp_mountain | 630 | 630 | yes | yes | yes | yes | 0.57 | 0.61 |
| disc | mp_nuked | 529 | 529 | yes | yes | yes | yes | 0.47 | 0.5 |
| disc | mp_radiation | 748 | 748 | yes | yes | yes | yes | 0.61 | 0.66 |
| disc | mp_russianbase | 662 | 662 | yes | yes | yes | yes | 0.55 | 0.59 |
| disc | mp_villa | 498 | 498 | yes | yes | yes | yes | 0.62 | 0.67 |
| disc | outro | 189 | 189 | yes | yes | yes | yes | 0.1 | 0.1 |
| disc | pentagon | 1027 | 1027 | yes | yes | yes | yes | 0.6 | 0.75 |
| disc | pow | 2599 | 2599 | yes | yes | yes | yes | 0.88 | 1.08 |
| disc | rebirth | 2355 | 2355 | yes | yes | yes | yes | 1.24 | 2.11 |
| disc | river | 2559 | 2559 | yes | yes | yes | yes | 1.59 | 1.24 |
| disc | so_narrative1_frontend | 33 | 33 | yes | yes | yes | yes | 0.02 | 0.01 |
| disc | so_narrative2_frontend | 32 | 32 | yes | yes | yes | yes | 0.01 | 0.01 |
| disc | so_narrative3_frontend | 35 | 35 | yes | yes | yes | yes | 0.01 | 0.01 |
| disc | so_narrative4_frontend | 33 | 33 | yes | yes | yes | yes | 0.01 | 0.01 |
| disc | so_narrative5_frontend | 32 | 32 | yes | yes | yes | yes | 0.01 | 0.01 |
| disc | terminal | 294 | 294 | yes | yes | yes | yes | 0.04 | 0.03 |
| disc | ui_mp | 670 | 670 | yes | yes | yes | yes | 1.72 | 1.4 |
| disc | ui_viewer_mp | 194 | 194 | yes | yes | yes | yes | 0.1 | 0.15 |
| disc | underwaterbase | 2379 | 2379 | yes | yes | yes | yes | 1.11 | 1.24 |
| disc | vorkuta | 2452 | 2452 | yes | yes | yes | yes | 1.31 | 1.37 |
| disc | wmd | 2877 | 2877 | yes | yes | yes | yes | 1.12 | 1.29 |
| disc | wmd_sr71 | 2022 | 2022 | yes | yes | yes | yes | 0.8 | 1.18 |
| disc | zombie_pentagon | 1057 | 1057 | yes | yes | yes | yes | 0.78 | 0.85 |
| disc | zombie_theater | 917 | 917 | yes | yes | yes | yes | 0.81 | 0.85 |
| disc | zombietron | 2790 | 2790 | yes | yes | yes | yes | 0.82 | 1.04 |
| update | common_zombie_patch | 137 | 137 | yes | yes | yes | yes | 0.03 | 0.01 |
| update | patch | 426 | 426 | yes | yes | yes | yes | 0.07 | 0.07 |
| update | patch_mp | 1337 | 1337 | yes | yes | yes | yes | 0.33 | 0.25 |
| update | patch_ui | 31 | 31 | yes | yes | yes | yes | 0.25 | 0.25 |
| update | patch_ui_mp | 130 | 130 | yes | yes | yes | yes | 0.89 | 0.7 |
| update | zombie_coast_patch | 103 | 103 | yes | yes | yes | yes | 0.01 | 0.01 |
| update | zombie_cod5_asylum_patch | 21 | 21 | yes | yes | yes | yes | 0.01 | 0.02 |
| update | zombie_cod5_factory_patch | 21 | 21 | yes | yes | yes | yes | 0.01 | 0.01 |
| update | zombie_cod5_prototype_patch | 21 | 21 | yes | yes | yes | yes | 0.01 | 0.01 |
| update | zombie_cod5_sumpf_patch | 25 | 25 | yes | yes | yes | yes | 0.01 | 0.01 |
| update | zombie_cosmodrome_patch | 131 | 131 | yes | yes | yes | yes | 0.02 | 0.01 |
| update | zombie_moon_patch | 16 | 16 | yes | yes | yes | yes | 0.0 | 0.0 |
| update | zombie_pentagon_patch | 98 | 98 | yes | yes | yes | yes | 0.01 | 0.01 |
| update | zombie_temple_patch | 101 | 101 | yes | yes | yes | yes | 0.01 | 0.01 |
| update | zombie_theater_patch | 146 | 146 | yes | yes | yes | yes | 0.02 | 0.01 |
| update | zombietron_patch | 12 | 12 | yes | yes | yes | yes | 0.0 | 0.0 |
| dlc1-english | zombie_cod5_asylum | 1028 | 1028 | yes | yes | yes | yes | 0.55 | 0.7 |
| dlc1-english | zombie_cod5_asylum_load | 8 | 8 | yes | yes | yes | yes | 0.0 | 0.0 |
| dlc1-english | zombie_cod5_factory | 1064 | 1064 | yes | yes | yes | yes | 0.68 | 0.76 |
| dlc1-english | zombie_cod5_factory_load | 8 | 8 | yes | yes | yes | yes | 0.0 | 0.0 |
| dlc1-english | zombie_cod5_prototype | 903 | 903 | yes | yes | yes | yes | 0.39 | 0.43 |
| dlc1-english | zombie_cod5_prototype_load | 8 | 8 | yes | yes | yes | yes | 0.0 | 0.0 |
| dlc1-english | zombie_cod5_sumpf | 991 | 991 | yes | yes | yes | yes | 0.71 | 0.74 |
| dlc1-english | zombie_cod5_sumpf_load | 8 | 8 | yes | yes | yes | yes | 0.0 | 0.0 |
| dlc1-french | zombie_cod5_asylum | 1028 | 1028 | yes | yes | yes | yes | 0.63 | 0.66 |
| dlc1-french | zombie_cod5_asylum_load | 8 | 8 | yes | yes | yes | yes | 0.0 | 0.0 |
| dlc1-french | zombie_cod5_factory | 1064 | 1064 | yes | yes | yes | yes | 0.73 | 0.75 |
| dlc1-french | zombie_cod5_factory_load | 8 | 8 | yes | yes | yes | yes | 0.0 | 0.0 |
| dlc1-french | zombie_cod5_prototype | 903 | 903 | yes | yes | yes | yes | 0.39 | 0.46 |
| dlc1-french | zombie_cod5_prototype_load | 8 | 8 | yes | yes | yes | yes | 0.0 | 0.0 |
| dlc1-french | zombie_cod5_sumpf | 991 | 991 | yes | yes | yes | yes | 0.64 | 0.73 |
| dlc1-french | zombie_cod5_sumpf_load | 8 | 8 | yes | yes | yes | yes | 0.0 | 0.0 |
| dlc2-english | mp_berlinwall2 | 658 | 658 | yes | yes | yes | yes | 0.89 | 0.99 |
| dlc2-english | mp_berlinwall2_load | 9 | 9 | yes | yes | yes | yes | 0.0 | 0.0 |
| dlc2-english | mp_discovery | 680 | 680 | yes | yes | yes | yes | 0.71 | 0.8 |
| dlc2-english | mp_discovery_load | 9 | 9 | yes | yes | yes | yes | 0.0 | 0.0 |
| dlc2-english | mp_kowloon | 753 | 753 | yes | yes | yes | yes | 0.88 | 0.81 |
| dlc2-english | mp_kowloon_load | 9 | 9 | yes | yes | yes | yes | 0.0 | 0.0 |
| dlc2-english | mp_stadium | 818 | 818 | yes | yes | yes | yes | 0.74 | 0.85 |
| dlc2-english | mp_stadium_load | 9 | 9 | yes | yes | yes | yes | 0.0 | 0.0 |
| dlc2-english | zombie_cosmodrome | 1162 | 1162 | yes | yes | yes | yes | 0.89 | 0.96 |
| dlc2-english | zombie_cosmodrome_load | 9 | 9 | yes | yes | yes | yes | 0.0 | 0.0 |
| dlc2-french | mp_berlinwall2 | 658 | 658 | yes | yes | yes | yes | 0.81 | 0.88 |
| dlc2-french | mp_berlinwall2_load | 9 | 9 | yes | yes | yes | yes | 0.0 | 0.0 |
| dlc2-french | mp_discovery | 680 | 680 | yes | yes | yes | yes | 0.68 | 0.71 |
| dlc2-french | mp_discovery_load | 9 | 9 | yes | yes | yes | yes | 0.0 | 0.0 |
| dlc2-french | mp_kowloon | 753 | 753 | yes | yes | yes | yes | 0.72 | 0.71 |
| dlc2-french | mp_kowloon_load | 9 | 9 | yes | yes | yes | yes | 0.0 | 0.0 |
| dlc2-french | mp_stadium | 818 | 818 | yes | yes | yes | yes | 0.74 | 0.78 |
| dlc2-french | mp_stadium_load | 9 | 9 | yes | yes | yes | yes | 0.0 | 0.0 |
| dlc2-french | zombie_cosmodrome | 1162 | 1162 | yes | yes | yes | yes | 0.85 | 0.91 |
| dlc2-french | zombie_cosmodrome_load | 9 | 9 | yes | yes | yes | yes | 0.0 | 0.0 |
| dlc3-english | mp_gridlock | 716 | 716 | yes | yes | yes | yes | 0.75 | 0.84 |
| dlc3-english | mp_gridlock_load | 9 | 9 | yes | yes | yes | yes | 0.0 | 0.0 |
| dlc3-english | mp_hotel | 766 | 766 | yes | yes | yes | yes | 0.77 | 0.77 |
| dlc3-english | mp_hotel_load | 9 | 9 | yes | yes | yes | yes | 0.0 | 0.0 |
| dlc3-english | mp_outskirts | 701 | 701 | yes | yes | yes | yes | 0.73 | 0.75 |
| dlc3-english | mp_outskirts_load | 9 | 9 | yes | yes | yes | yes | 0.0 | 0.0 |
| dlc3-english | mp_zoo | 679 | 679 | yes | yes | yes | yes | 0.72 | 0.8 |
| dlc3-english | mp_zoo_load | 9 | 9 | yes | yes | yes | yes | 0.0 | 0.0 |
| dlc3-english | zombie_coast | 1187 | 1187 | yes | yes | yes | yes | 0.71 | 0.79 |
| dlc3-english | zombie_coast_load | 9 | 9 | yes | yes | yes | yes | 0.0 | 0.0 |
| dlc3-french | mp_gridlock | 716 | 716 | yes | yes | yes | yes | 0.8 | 0.82 |
| dlc3-french | mp_gridlock_load | 9 | 9 | yes | yes | yes | yes | 0.0 | 0.0 |
| dlc3-french | mp_hotel | 766 | 766 | yes | yes | yes | yes | 0.74 | 0.74 |
| dlc3-french | mp_hotel_load | 9 | 9 | yes | yes | yes | yes | 0.0 | 0.0 |
| dlc3-french | mp_outskirts | 701 | 701 | yes | yes | yes | yes | 0.76 | 0.78 |
| dlc3-french | mp_outskirts_load | 9 | 9 | yes | yes | yes | yes | 0.0 | 0.0 |
| dlc3-french | mp_zoo | 679 | 679 | yes | yes | yes | yes | 0.77 | 0.74 |
| dlc3-french | mp_zoo_load | 9 | 9 | yes | yes | yes | yes | 0.0 | 0.0 |
| dlc3-french | zombie_coast | 1187 | 1187 | yes | yes | yes | yes | 0.67 | 0.78 |
| dlc3-french | zombie_coast_load | 9 | 9 | yes | yes | yes | yes | 0.0 | 0.0 |
| dlc4-english | mp_area51 | 803 | 803 | yes | yes | yes | yes | 0.79 | 0.82 |
| dlc4-english | mp_area51_load | 9 | 9 | yes | yes | yes | yes | 0.0 | 0.0 |
| dlc4-english | mp_drivein | 738 | 738 | yes | yes | yes | yes | 0.75 | 0.78 |
| dlc4-english | mp_drivein_load | 9 | 9 | yes | yes | yes | yes | 0.0 | 0.0 |
| dlc4-english | mp_golfcourse | 711 | 711 | yes | yes | yes | yes | 0.65 | 0.64 |
| dlc4-english | mp_golfcourse_load | 9 | 9 | yes | yes | yes | yes | 0.0 | 0.0 |
| dlc4-english | mp_silo | 748 | 748 | yes | yes | yes | yes | 0.67 | 0.69 |
| dlc4-english | mp_silo_load | 9 | 9 | yes | yes | yes | yes | 0.0 | 0.0 |
| dlc4-english | zombie_temple | 1355 | 1355 | yes | yes | yes | yes | 0.68 | 0.77 |
| dlc4-english | zombie_temple_load | 9 | 9 | yes | yes | yes | yes | 0.0 | 0.0 |
| dlc4-french | mp_area51 | 803 | 803 | yes | yes | yes | yes | 0.73 | 0.79 |
| dlc4-french | mp_area51_load | 9 | 9 | yes | yes | yes | yes | 0.0 | 0.0 |
| dlc4-french | mp_drivein | 738 | 738 | yes | yes | yes | yes | 0.7 | 0.71 |
| dlc4-french | mp_drivein_load | 9 | 9 | yes | yes | yes | yes | 0.0 | 0.0 |
| dlc4-french | mp_golfcourse | 711 | 711 | yes | yes | yes | yes | 0.67 | 0.68 |
| dlc4-french | mp_golfcourse_load | 9 | 9 | yes | yes | yes | yes | 0.0 | 0.0 |
| dlc4-french | mp_silo | 748 | 748 | yes | yes | yes | yes | 0.7 | 0.69 |
| dlc4-french | mp_silo_load | 9 | 9 | yes | yes | yes | yes | 0.0 | 0.0 |
| dlc4-french | zombie_temple | 1355 | 1355 | yes | yes | yes | yes | 0.69 | 0.75 |
| dlc4-french | zombie_temple_load | 9 | 9 | yes | yes | yes | yes | 0.0 | 0.0 |
| dlc5-english | zombie_moon | 1258 | 1258 | yes | yes | yes | yes | 0.75 | 1.04 |
| dlc5-english | zombie_moon_load | 5 | 5 | yes | yes | yes | yes | 0.0 | 0.0 |
| dlc5-french | zombie_moon | 1258 | 1258 | yes | yes | yes | yes | 0.9 | 0.95 |
| dlc5-french | zombie_moon_load | 5 | 5 | yes | yes | yes | yes | 0.0 | 0.0 |
| hdd-english | common_zombie_patch | 137 | 137 | yes | yes | yes | yes | 0.01 | 0.01 |
| hdd-english | patch | 426 | 426 | yes | yes | yes | yes | 0.09 | 0.07 |
| hdd-english | patch_mp | 1337 | 1337 | yes | yes | yes | yes | 0.32 | 0.26 |
| hdd-english | patch_ui | 31 | 31 | yes | yes | yes | yes | 0.27 | 0.26 |
| hdd-english | patch_ui_mp | 130 | 130 | yes | yes | yes | yes | 0.83 | 0.73 |
| hdd-english | zombie_coast_patch | 103 | 103 | yes | yes | yes | yes | 0.01 | 0.01 |
| hdd-english | zombie_cod5_asylum_patch | 21 | 21 | yes | yes | yes | yes | 0.01 | 0.01 |
| hdd-english | zombie_cod5_factory_patch | 21 | 21 | yes | yes | yes | yes | 0.01 | 0.01 |
| hdd-english | zombie_cod5_prototype_patch | 21 | 21 | yes | yes | yes | yes | 0.01 | 0.01 |
| hdd-english | zombie_cod5_sumpf_patch | 25 | 25 | yes | yes | yes | yes | 0.01 | 0.01 |
| hdd-english | zombie_cosmodrome_patch | 131 | 131 | yes | yes | yes | yes | 0.02 | 0.02 |
| hdd-english | zombie_moon_patch | 16 | 16 | yes | yes | yes | yes | 0.0 | 0.0 |
| hdd-english | zombie_pentagon_patch | 98 | 98 | yes | yes | yes | yes | 0.01 | 0.01 |
| hdd-english | zombie_temple_patch | 101 | 101 | yes | yes | yes | yes | 0.01 | 0.01 |
| hdd-english | zombie_theater_patch | 146 | 146 | yes | yes | yes | yes | 0.02 | 0.01 |
| hdd-english | zombietron_patch | 12 | 12 | yes | yes | yes | yes | 0.0 | 0.0 |
| hdd-french | common_zombie_patch | 137 | 137 | yes | yes | yes | yes | 0.01 | 0.02 |
| hdd-french | patch | 426 | 426 | yes | yes | yes | yes | 0.07 | 0.07 |
| hdd-french | patch_mp | 1337 | 1337 | yes | yes | yes | yes | 0.27 | 0.24 |
| hdd-french | patch_ui | 31 | 31 | yes | yes | yes | yes | 0.27 | 0.26 |
| hdd-french | patch_ui_mp | 130 | 130 | yes | yes | yes | yes | 0.79 | 0.59 |
| hdd-french | zombie_coast_patch | 103 | 103 | yes | yes | yes | yes | 0.01 | 0.01 |
| hdd-french | zombie_cod5_asylum_patch | 21 | 21 | yes | yes | yes | yes | 0.01 | 0.01 |
| hdd-french | zombie_cod5_factory_patch | 21 | 21 | yes | yes | yes | yes | 0.01 | 0.01 |
| hdd-french | zombie_cod5_prototype_patch | 21 | 21 | yes | yes | yes | yes | 0.01 | 0.01 |
| hdd-french | zombie_cod5_sumpf_patch | 25 | 25 | yes | yes | yes | yes | 0.01 | 0.01 |
| hdd-french | zombie_cosmodrome_patch | 131 | 131 | yes | yes | yes | yes | 0.02 | 0.02 |
| hdd-french | zombie_moon_patch | 15 | 15 | yes | yes | yes | yes | 0.0 | 0.0 |
| hdd-french | zombie_pentagon_patch | 98 | 98 | yes | yes | yes | yes | 0.01 | 0.01 |
| hdd-french | zombie_temple_patch | 101 | 101 | yes | yes | yes | yes | 0.01 | 0.01 |
| hdd-french | zombie_theater_patch | 146 | 146 | yes | yes | yes | yes | 0.01 | 0.02 |
| hdd-french | zombietron_patch | 12 | 12 | yes | yes | yes | yes | 0.0 | 0.0 |
<!-- REWRITE END -->

## 4. Open items

- xmodelpieces, waterBuffers and cullGroups are written by the same code as they are read, but
  no zone contains them, so neither direction is proven on data.
- Offset pointers are written as parsed. After an edit that moves data, pointers into the moved
  range are stale until the remap (src/opent5/xfile/remap.py) rewrites them.
- The `.ff` rebuild of an edited zone deflates the changed chunks afresh; only unchanged
  content is carried. The console signature at 0x3c is copied and is void for edited content.
