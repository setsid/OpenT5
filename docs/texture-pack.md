# Texture packs: batch-replacing a zone's images from a folder

A texture pack is a folder of PNG (and/or DDS) files named after the images they replace.
`opent5.texpack` matches each file to an image asset in a zone, replaces the matching ones in
one pass with the single-image replace of `opent5.edit` (docs/edit-api.md), and saves a new
zone and, when streamed images changed, its `.pak` beside it. Nothing is ever written into
the game folders named in `.env`; the edited zone keeps the original console signature, so it
loads only on a client with the signature check patched out.

## Naming: how a file name maps to an image

The linker decorates stored image names two ways (docs/research/textures.md 2.1, 5):

- a leading marker of one or two tildes and a `-g` tag on names the material system
  generates: `~-g...`, `~~-g...` (names with no marker exist too, such as `whitesquare`);
- a trailing semantic suffix: `_c` colour, `_n` normal, `_s` specular.

So a modder names a PNG after the thing it is and need not type the marker. Matching works on
a normalised key: the name is lower-cased, the leading marker removed, and the `_c` / `_n` /
`_s` suffix split off. Real mp_nuked examples:

| File name (stem) | Matches stored image | How |
|---|---|---|
| `mp_nuked_sign` | `~-gmp_nuked_sign_c` | cleaned (marker and `_c` added back) |
| `mp_nuked_townsign_c` | `~-gmp_nuked_townsign_c` | cleaned |
| `us_art_streetsigns_c` | `~-gus_art_streetsigns_c` | cleaned |
| `~-gus_art_color_white_c` | `~-gus_art_color_white_c` | raw stored name (always accepted) |
| `whitesquare` | `whitesquare` | raw stored name (no marker) |

The suffix matters when one core has several maps. mp_nuked has
`~-gus_art_color_white_c`, `~-gus_art_color_white_n` and `~-gus_art_color_white_s`: a file
`us_art_color_white` is **ambiguous** and left alone (the report names the three candidates),
and `us_art_color_white_c` picks the colour map. A file that matches nothing is **skipped**
with the cleaned name it looked for.

### The map file (exact control)

A JSON or CSV map file overrides the heuristic, keyed by file name or stem:

```json
{ "my_custom_name.png": "~-gus_art_color_white_c", "sign": "~-gmp_nuked_sign_c" }
```

```csv
file,image
my_custom_name.png,~-gus_art_color_white_c
sign,~-gmp_nuked_sign_c
```

`opent5 texpack list ZONE --template map.json` writes a template mapping a suggested file
name to every replaceable image, so you know what to call your PNGs.

## Size and format

By default a file must match the image's own width, height and format (the format is the
image's; a PNG is encoded to it, a DDS must already be in it). A wrong size is reported per
file. A **streamed** image (one whose pixels live in a `.pak`) may take another power-of-two
size with `resize=True` / `--resize` (docs/research/pak.md 9.1); a zone-held image cannot
change size. Cube maps, volume textures and images defined in another zone are reported as
not replaceable.

### Shared paks

A streamed image's mip tail usually lives in a shared pak (`images_low.pak`), and some images
have every part in `common.pak` or `ui_mp.pak`. By default only parts in the level's own
`<zone>.pak` are written; parts in a shared pak are left alone (the per-file report says so).
An image with **no** part in the level pak is reported failed unless `allow_shared=True` /
`--allow-shared`, which writes the shared pak too, under its own name beside the zone, and
changes that image in every zone that uses the pak.

## API

```python
from opent5.texpack import apply_pack, preview_pack, list_images, write_template

result = preview_pack("mp_nuked.ff", "my_pack/")            # dry run: what would change
result = apply_pack("mp_nuked.ff", "my_pack/", "out/",      # write out/mp_nuked.ff (+ .pak)
                    map_file=None, resize=False, allow_shared=False)

images = list_images("mp_nuked.ff", replaceable_only=True)  # names to put in a map file
write_template("mp_nuked.ff", "map.json")                   # a map template to fill in
```

`apply_pack(zone_ff, pack_dir, out_dir, map_file=None, resize=False, allow_shared=False,
dry_run=False)` opens the zone once, matches every `.png` / `.dds` in `pack_dir`, replaces the
matching images and (unless `dry_run`) saves `out_dir/<zone>.ff`, verified. `preview_pack` is
`apply_pack` with `dry_run=True` and no output.

`PackResult`: `zone_name`, `pack_dir`, `output` (the saved `.ff`, `None` on a dry run),
`files` (a `FileResult` each), `report` (the save report as a dict: `verified`, `sha1`,
`identical`, `signature_note`, and `paks` when a pak was written), and the counts `replaced`,
`skipped`, `failed`. `FileResult`: `file`, `status` (`replaced`, `resized`, `skipped`,
`failed`, or `would-replace` / `would-resize` on a dry run), `image` (the matched stored
name), `how` (`stored-name`, `cleaned`, `map`), `reason` (why it was skipped or failed), the
matched image's `format` / `width` / `height`, and the input `input_width` / `input_height`.
`to_dict()` on both gives the `--json` shape. Bad inputs (a missing pack folder, a malformed
map file, an output inside a game folder) raise `TexpackError`.

## Command line

```sh
opent5 texpack list mp_nuked                         # every image name
opent5 texpack list mp_nuked --replaceable --template map.json
opent5 texpack apply mp_nuked my_pack -o out --dry-run     # check the mapping first
opent5 texpack apply mp_nuked my_pack -o out              # write out/mp_nuked.ff (+ .pak)
opent5 texpack apply mp_nuked my_pack -o out --map map.json --resize --allow-shared
```

Every command takes `--json`. Per-file failures (a wrong size, a name that matched nothing)
are reported in the output and do not fail the command, as with `extract`; a failed save
verification does. Copy the written `<zone>.pak` into the same game folder as the zone (the
game opens `<zone>.pak` beside `<zone>.ff`, pak.md 5).
