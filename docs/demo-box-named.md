# Box map under its own name: `mp_opent5box`

The same converted box as docs/demo-box.md, but as its own map, `mp_opent5box`, beside the
stock maps instead of over Nuketown. Stock `mp_nuked.ff` stays untouched. Checked offline only.

## Files

| File | sha1 | Bytes | What |
|---|---|---|---|
| `~/opent5/out/demo/f_box_named/mp_opent5box.ff` | `56cf51e42fbfe408609cf3254a184efbb0cfe449` | 27 579 104 | the map's zone (lighting `flat`) |
| `~/opent5/out/demo/f_box_named/mp_opent5box.pak` | `5930c6c683d9339d0357a7266a8d37819b95e6cf` | 172 005 376 | its image pack: a byte copy of the disc `mp_nuked.pak` under the map's name |

The folder also holds `convert.json`, `verify.json`, `oracle.json` and `selftest.json`.
Made with:

    opent5 convert "C:\o5\p6\mp_opent5box.ff" --base mp_nuked --name mp_opent5box --copy-pak -o out/demo/f_box_named/

## What is and is not possible without changing a stock file

The game loads a map name it does not know without complaint, from the same folder as the
disc maps (docs/research/map-registration.md 2). But the multiplayer menus list only the maps
in `mp/mapstable.csv`, a table inside the stock patch_mp.ff, and no console, config file or
other route was found that starts a map by name without changing a stock file (same
document, section 4). So with only the map's own files installed, the map is on the console
but nothing offers or starts it. That is the honest state; the test below needs your
decision.

## Install (the map's own files)

1. Copy `mp_opent5box.ff` and `mp_opent5box.pak` into
   `C:\Users\bolst\Desktop\rpcs3\BLES01031\PS3_GAME\USRDIR\english` (beside `mp_nuked.ff`;
   new names, nothing is overwritten). Leave `mp_nuked.ff` and `mp_nuked.pak` as they are
   (retail sha1s in docs/demo-box.md).
2. Use the signature-patched multiplayer client.

To remove: delete the two files.

## Starting it: needs one stock change (your decision)

The minimum change is one row in patch_mp's map table (docs/research/map-registration.md 5).
OpenT5 writes it only when asked:

    opent5 convert "C:\o5\p6\mp_opent5box.ff" --base mp_nuked --name mp_opent5box --register --title "OpenT5 Box" -o OUTDIR

This also writes `OUTDIR/patch_mp.ff` (from the update's patch_mp.ff, read only): row 28,
index 26, `maxnum_map` 27, pack 0, splitscreen `YES`, team sets as Nuketown, and the title
under `MPUI_WARMUSEUM` (a cut map's name no menu uses). Checked offline in an earlier run:
`opent5 verify --against` the update's patch_mp: 4 assets changed (the table and three
localize entries), the rest identical; the emulated loader consumed it exactly (48 231
pointers as the product reads them). If you choose this: back up the update's
`patch_mp.ff` first (it is already saved as `patch_mp.ff.retail.bak`, sha1 `499ce654...`),
copy `OUTDIR/patch_mp.ff` over `dev_hdd0\game\BLES01031\USRDIR\english\patch_mp.ff`, and
restore the backup to undo. A second custom map is added to the same file with
`--patch-mp OUTDIR/patch_mp.ff`.

Then: Multiplayer, Splitscreen (or Private Match), Team Deathmatch or Free-for-all, open the
map list (base maps, not DLC): `OpenT5 Box` is the last entry.

## What you should see (with the row installed)

- The map list shows `OpenT5 Box`; its image is Nuketown's (the row copies the base's
  image name); the lobby background may be blank or a placeholder (no
  `menu_mp_opent5box_map_select_big` material exists; INFERRED).
- Loading: no load screen image (no `loadscreen_mp_opent5box`; the game shows none, by
  design of 0x448d20), then the box as in docs/demo-box.md.
- Unlike the Nuketown-named demo, the update's Nuketown script does not run: the zone's own
  minimal `maps/mp/mp_opent5box.gsc` does. No nuke at match end, no Nuketown entities added.
- The look may differ from the first demo: `vision/mp_opent5box.vision` does not exist, so
  the game uses `vision/default.vision` after the art script's Nuketown vision (lighting is
  another track's work).

## Failures and their meaning

| You see | Most likely meaning |
|---|---|
| `OpenT5 Box` is not in the list | the edited patch_mp.ff is not the one in use (check its sha1 in dev_hdd0), or the DLC map list is shown |
| The list shows `War Museum` | another zone (INFERRED patch.ff) supplies the same localize key last; the map is still the right one |
| "Cannot find zone" (`EXE_CANNOT_FIND_ZONE`) | `mp_opent5box.ff` is not in the disc `english` folder or misnamed |
| Box with smeared or missing textures | `mp_opent5box.pak` missing or not beside the zone |
| Script error in `maps/mp/mp_opent5box*.gsc` | a renamed script reference the converter missed: note file and line |
| Signature / fastfile refusal | not the signature-patched client, or a file copied incompletely (check sha1s) |
