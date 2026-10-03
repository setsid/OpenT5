# Mod patch format (`.o5patch`)

A mod patch lets someone share a zone edit without redistributing any game files. It captures
only the difference between a stock zone and an edited one; applying it to the recipient's own
stock copy reproduces the edited zone exactly, after checking that their copy really is the
right source.

The format and its code live in `src/opent5/patch/`. The command line is
`opent5 patch create|apply|info`; the same functions back a GUI panel.

## Why this matters

Retail zones are copyrighted game data. A mod that ships an edited `patch_mp.ff` ships the
whole of Treyarch's zone. A `.o5patch` ships only the modder's own change (the new text, the
new table, the new image), so it is legal to pass around and, for a text edit, tiny. The
recipient supplies the stock zone from their own copy of the game.

## What a patch contains

A `.o5patch` file is one compressed file with two parts:

1. a **manifest** describing what the patch was built from and what it produces, and
2. a **payload** of one record per changed asset, each holding the new content of that asset
   and nothing else.

### Manifest

| Field | Meaning |
|---|---|
| `format_version` | the format version (1); a reader refuses a newer file |
| `generator`, `tool_version` | the OpenT5 build that wrote it |
| `created` | the date it was written (ISO `YYYY-MM-DD`) |
| `source_zone` | the zone name from the stock header (for example `patch_mp`) |
| `source_ff_sha1` | sha1 of the stock `.ff` file |
| `source_content_sha1` | sha1 of the stock zone's **decompressed content** |
| `result_content_sha1` | sha1 of the edited zone's decompressed content |
| `signed` | whether the stock header carries a console signature |
| `assets_changed` | how many assets the patch changes |

Two hashes are carried because they answer two different questions. `source_content_sha1` is
the authoritative one: the decompressed content is what the game loads and what the edit
replay runs against, so matching it is what guarantees the result is reproduced byte for byte.
`source_ff_sha1` identifies the exact retail file; it is reported to the user but is not the
gate, because a `.ff` that was repacked (different chunk boundaries, identical content) still
reproduces the edit.

### Payload

One record per changed asset: the asset's index, its type and name (checked on apply), an
`op`, and the new content as a blob. The blob is exactly what the matching editor reads:

| `op` | asset types | blob |
|---|---|---|
| `localize` | localize | the new value (Latin-1) |
| `table` | stringtable | CSV of the new rows (Latin-1) |
| `text` | rawfile, map_ents, col_map | the new file / entity string (Latin-1; a `.gsc`/`.csc` is its inflated source) |
| `image` | image (pixels in the zone) | a PNG of the new level-0 pixels |

The payload holds no stock bytes of any kind: only the new content of assets that actually
changed. A localize patch is a few hundred bytes; a stringtable-cell patch is a few hundred
too. A rawfile patch is the size of the new file (compressed), because replacing a rawfile is
a whole-file replacement, not a within-file diff.

## Why per-asset new content, not a byte delta

The payload could instead be a delta of the decompressed zone content (a copy/insert delta, or
`zlib`/`difflib`). We chose per-asset new content, and reject the byte delta, for two reasons.

**Size.** A length-changing edit (a longer localize value, an extra stringtable row, a longer
script) does not change one place in the zone: the zone linker re-lays the whole content out
and every offset pointer after the edit takes a new value. In `patch_mp`, growing one localize
value by sixteen bytes rewrites the offsets of about 1,300 later assets. A byte-level delta of
the two decompressed streams would therefore have to encode all of those changed pointer bytes,
scattered across megabytes, even though the modder changed one string. The per-asset form
stores only the new string and regenerates the re-layout on the recipient's machine, so the
patch stays the size of the change. Measured on real `patch_mp` (3,715,811 bytes of content):

| Edit | Patch size |
|---|---|
| one localize value | 325 bytes |
| one stringtable cell | 481 bytes |
| a localize value, a cell and a whole script | 10,597 bytes (the 86 KB of new script text, compressed) |

**Correctness with no new code.** Applying a patch replays the recorded edits through
`opent5.edit.Document`, the same editing core the `replace` command uses. The re-layout, the
pointer remapping, the container repacking and the verification are the code that is already
proven against the game's own loader; the patch format adds no new zone-rewriting logic that
could be subtly wrong. A hand-written copy/insert delta would be new, security-sensitive code
with none of that backing.

The one thing the per-asset form cannot do on its own is tell, from a diff alone, that an edit
it cannot express has happened. That is handled at build time (below), so it is never a
correctness risk, only a reason a particular patch may be refused.

## Correctness is proven when the patch is built

`create`/`export` does not trust its own diff. After capturing the per-asset changes it opens a
fresh copy of the stock zone, replays exactly those changes, rebuilds the content, and requires
the result to equal the edited content **byte for byte**. If anything is left over (an asset
type the format does not carry, or a shared-string edit made with `--share all`, which only
reproduces as a stored-string edit, not as per-field copies), the rebuild differs and the patch
is **refused**, naming the asset and content offset that still differ. So a `.o5patch` that
would not reproduce its edited zone is never written.

On apply, the recipient's stock content sha1 must equal `source_content_sha1` or the apply is
refused with expected-vs-found. Because the stock content is then identical to the content the
patch was built against, replaying the same edits yields the same bytes; as a final check,
apply confirms the rebuilt content sha1 equals `result_content_sha1`.

## File layout

```
offset 0   8 bytes   MAGIC                b"O5PATCH\x00"
offset 8   1 byte    format version       (reader refuses a higher value)
offset 9   ...       zlib stream of:

  u32  manifest_json_len
       manifest_json                      (UTF-8 JSON object)
  u32  change_count
  change_count times:
       u32  meta_json_len
            meta_json                      {"index","type","name","op"} (UTF-8)
       u32  blob_len
            blob                           the new content
```

All integers are big-endian, to match the fastfile the patch is built from. The whole file
after the version byte is a single `zlib` stream, so the patch is one compressed file.

## Reversing a patch (`unapply`)

Not supported, by design. A patch deliberately carries no stock bytes, so it cannot rebuild the
stock zone from itself. The stock zone the recipient needs to go back to is the very file they
already hold (their unmodified copy, or a fresh copy from the game). To revert an applied
patch, keep or restore that stock `.ff`; there is nothing the tool can reconstruct that the
user does not already have. This is a consequence of the no-stock-data property, not a gap.

## Signatures

Every retail zone is RSA-signed and the signature cannot be regenerated. An applied patch
changes the zone's content, so the rebuilt zone carries the original signature bytes and loads
only on a client with the signature check patched out. The result object's `signature_note`
says so whenever the source is signed, the same wording `replace` uses. A patch whose only
effect, once applied, leaves the content unchanged would keep its signature, but by definition
such a patch has no changes.

## Limitations (format version 1)

- Adding or removing a top-level asset is not supported; the asset lists must match.
- Image edits are carried for images whose pixels live in the zone (inline or deferred). A
  streamed `.pak` image edit is not carried (it would also need the `.pak` shipped); such a
  patch is refused at build time with the asset named.
- A `--share all` edit across several fields is captured per field and so may not reproduce
  byte for byte; when it does not, the build is refused rather than a wrong patch produced.

## API

```python
from opent5.patch import export, create, apply, info, PatchError

patch_bytes = export(stock_ff, edited_ff)          # the .o5patch bytes
result      = create(stock_ff, edited_ff)           # CreateResult (patch + summary)
applied     = apply(patch_bytes, user_stock_ff, out_ff)   # ApplyResult; out_ff optional
described   = info(patch_bytes)                     # PatchInfo
```

`stock`/`edited`/`user_stock` are a `.ff` path, raw `.ff` bytes, or a bare zone name resolved
through `.env` by the CLI. `PatchError` carries an expected-vs-found message.

The result objects suit both CLI JSON (`.to_dict()`) and a GUI dialog (attribute access):

- `CreateResult`: `patch` (bytes), `patch_bytes`, `source_zone`, `source_ff_sha1`,
  `source_content_sha1`, `result_content_sha1`, `signed`, `changes` (list of `ChangeInfo`:
  `index`, `type_name`, `name`, `op`, `size`), `source_bytes`, `edited_bytes`,
  `signature_note`.
- `ApplyResult`: `source_zone`, `expected_ff_sha1`/`expected_content_sha1`,
  `found_ff_sha1`/`found_content_sha1`, `verified` (bool), `reproduces_target` (bool),
  `changes`, `output`, `output_bytes`, `output_sha1`, `signature_note`, `notes`, `problems`.
- `PatchInfo`: `manifest` (a `Manifest`) and `changes`.

## Command line

```sh
opent5 patch create STOCK EDITED -o FILE      # build a patch from stock -> edited
opent5 patch apply  PATCH STOCK  -o OUT       # apply a patch to your own stock zone
opent5 patch info   PATCH                     # describe a patch without applying it
```

Every sub-command takes `--json`. `STOCK` and `EDITED` may be a path or a bare zone name from
`.env`. No command writes into a configured game folder. `apply` refuses a stock zone whose
content sha1 does not match the patch, with expected-vs-found, and verifies the written zone
unless `--no-verify`. Exit codes follow the rest of the CLI: 0 done, 1 failed, 2 usage.

```sh
$ opent5 patch create patch_mp patch_mp_edited.ff -o accuracy.o5patch
patch accuracy.o5patch (325 bytes)
  source   patch_mp  (content 3715811 -> 3715827 bytes)
  ...
  changes  1 asset(s)
        95  localize      localize        15  CGAME_SB_ACCURACY

$ opent5 patch apply accuracy.o5patch patch_mp -o out/patch_mp.ff
applied to patch_mp -> out/patch_mp.ff (1196064 bytes, sha1 ...)
  source   verified: content sha1 ... matches the patch
  changes  1 asset(s)
        95  localize      localize        15  CGAME_SB_ACCURACY
  reproduces the edited zone: yes; verified: yes
  note: The console signature at 0x3c ... loads only on a client with the check patched out.
```

## What the lead wires in

### `src/opent5/cli.py`

Two additions, both safe (the `opent5.patch` package does not import `opent5.cli` at module
load, so there is no import cycle):

1. At the top, with the other imports:

   ```python
   from opent5.patch import cmd_patch, register as register_patch, text_patch
   ```

2. In `COMMANDS`, one more entry:

   ```python
   "patch": (cmd_patch, text_patch),
   ```

3. In `build_parser()`, just before `return parser`:

   ```python
   register_patch(sub)
   ```

`cmd_patch` dispatches on its own sub-command (`create`/`apply`/`info`), resolves bare zone
names through `.env`, guards output paths against the game folders, and returns the dict the
command table renders. A missing sub-command exits 2 through argparse, as the other commands
do. Optionally add `"patch"` to the small set in `main()` that renders `Failure.data` as text
on a verification problem; it is not required.

### `docs/cli.md`

Add a `## patch` section (place it after `rebuild`). Suggested lines:

```
## patch

    opent5 patch create STOCK EDITED -o FILE [--json]
    opent5 patch apply  PATCH STOCK  -o OUT [--no-verify] [--json]
    opent5 patch info   PATCH [--json]

Share a zone edit as its difference from the stock zone, without redistributing game files
(docs/patch-format.md). `create` captures the change from a stock zone to an edited one into a
`.o5patch` file that holds only the new content of the changed assets. `apply` reproduces the
edited zone on the recipient's own stock copy, refusing a stock zone whose decompressed-content
sha1 does not match the patch (expected vs found), and verifying the written zone unless
`--no-verify`. `info` describes a patch without applying it.

STOCK and EDITED may be a path or a bare zone name from .env. An applied patch changes the
content, so the rebuilt zone loads only on a signature-patched client; the output says so.
```

### GUI

A panel can call `opent5.patch.create`, `apply` and `info` and show the result objects
directly: `source_zone`, the `changes` list, `verified`/`reproduces_target`, the sizes and
`signature_note`. `apply` surfaces `notes` (for example when the user's `.ff` was repacked but
its content matches) and `problems` (empty when all is well).
```
