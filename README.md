<div align="center">

<img src="src/opent5/gui/resources/opent5.svg" width="72" alt="">

# OpenT5

**Open, edit and rebuild Call of Duty: Black Ops fastfiles for the PS3.**

By setsid.

[![Licence: GPL-3.0-or-later](https://img.shields.io/badge/licence-GPL--3.0--or--later-c4a062)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-3776ab?logo=python&logoColor=white)](pyproject.toml)
[![PySide6 6.8](https://img.shields.io/badge/PySide6-6.8-41cd52?logo=qt&logoColor=white)](requirements-gui.txt)
[![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20Linux-555)](#getting-it)
[![Game](https://img.shields.io/badge/game-Black%20Ops%20(T5)%20PS3-333)](#what-it-works-with)
[![Style: ruff](https://img.shields.io/badge/style-ruff-d7ff64)](pyproject.toml)

</div>

---

A Black Ops zone (`.ff`) isn't an archive. It's a signed, encrypted, compressed
snapshot of the game's memory, full of pointers into itself. OpenT5 reads that
snapshot down to the last byte, lets you change what's inside, and writes it back
in the form the game expects.

I wanted a tool that treats the PS3 build properly: no hex-patching blind, no
"same length only" edits, and nothing claimed that hasn't been checked against the
game's own loader.

## What it does

- **Opens every zone** on the disc, the title update and all five DLC packs
  (178 zones), and rebuilds each one byte for byte.
- **Parses every asset type** into named, typed fields: scripts, string tables,
  localised text, menus, images, materials, shaders, models, animations, effects,
  sounds, weapons, collision and world geometry.
- **Edits with resizing.** Make a script or a string longer and every pointer
  that moves is remapped, the block sizes and headers are recomputed, and the
  zone is re-chunked and re-encrypted.
- **Images in and out**, including the ones streamed from `.pak` files: DXT and
  the uncompressed GCM formats, swizzled and unswizzled.
- **Exports** whole zones to PNG, OBJ, JSON, CSV and plain text.
- **Converts maps** built with the PC Mod Tools into a PS3 map zone. A sealed
  test room built this way already loads and plays in RPCS3.

There's a desktop app and a command line, and both use the same editing core.

## Getting it

### The app (Windows)

Download `OpenT5.exe` from the [releases](../../releases) page and run it. It's a
single file with no installer. Put a `.env` next to it listing your zone folders
(see [`.env.example`](.env.example)) and they show up on the start page.

New versions are checked for on start, verified against a signature, and offered
as a restart. You can turn that off in the Help menu. That check is the only
network request the app ever makes.

### From source

```sh
git clone https://github.com/setsid/OpenT5.git
cd OpenT5
cp .env.example .env      # point it at your own dump
npm run setup             # creates .venv and installs the pinned dependencies
npm run gui               # desktop app
.venv/bin/opent5 --help   # command line
```

You'll need Python 3.10 or newer and Node (only for the `npm run` shortcuts).

## Using it

```sh
opent5 info mp_nuked                         # what's in a zone
opent5 list mp_nuked --type stringtable      # list assets
opent5 extract mp_nuked out/nuked            # dump everything to open formats
opent5 replace patch_mp default_mp.cfg my.cfg -o out/patch_mp.ff
opent5 verify out/patch_mp.ff --against patch_mp
opent5 rebuild mp_nuked -o out/mp_nuked.ff   # byte-identical when nothing changed
```

Every command takes `--json`. A bare zone name like `mp_nuked` is looked up in
the folders named in `.env`. More in [docs/cli.md](docs/cli.md) and
[docs/gui.md](docs/gui.md).

## Read this before you edit anything

Every retail zone carries an RSA-2048 signature, and nobody outside the
publisher can make a new one. OpenT5 keeps the original signature bytes, so an
**edited zone loads only on a client with the signature check patched out**.
The app tells you this every time you save.

OpenT5 never writes over your game files. Saving always produces a new file,
and it reopens and checks every asset before it says the save worked.

## What it works with

| | |
|---|---|
| Game | Call of Duty: Black Ops, PS3 (BLES01031), multiplayer, zombies and campaign zones |
| Zones | disc, title update, DLC 1 to 5, English and French |
| Tested on | RPCS3 with a signature-patched client |

Other regions and the Xbox 360 build are untested. Zones from other games
(World at War, Black Ops II) use different layouts and won't open.

## How it was worked out

Nothing public described the PS3 structures past the container, so they were
worked out from the zones and the game's own executable. Every claim in
[`docs/research/`](docs/research/README.md) carries its evidence: an offset and
the bytes, or an address in the executable. The parser is checked against the
game's real loader, run in a small PowerPC interpreter
([`tools/loader_emu`](tools/loader_emu)).

## Development

```sh
npm run test      # fast suite, about 50 s
npm run lint
npm run build
```

Tests that need real zones find them through `.env` and skip when they aren't
there. Game files (`.ff`, `.pak`, `.elf`, extracted assets) are never committed.
For the Windows build, the exe smoke test and releases, see
[`packaging/build.bat`](packaging/build.bat) and [docs/updates.md](docs/updates.md).

| Folder | What's in it |
|---|---|
| `src/opent5/container` | the signed, encrypted, compressed container, and `.pak`/`.wad` |
| `src/opent5/xfile` | the zone stream: parser, writer, field schemas, references, remap |
| `src/opent5/edit` | the editing core shared by the app and the command line |
| `src/opent5/export` | export to PNG, OBJ, JSON, CSV and text |
| `src/opent5/convert` | PC Mod Tools map to PS3 zone |
| `src/opent5/gui` | the desktop app |
| `docs/research` | the format research |

## Licence

OpenT5 is free software under the GNU General Public License, version 3 or later
([LICENSE](LICENSE)). Some struct and field names come from OpenAssetTools, also
GPLv3; [docs/provenance.md](docs/provenance.md) lists what came from where.

Call of Duty and Black Ops are trademarks of Activision. OpenT5 isn't affiliated
with or endorsed by Activision or Treyarch, and it doesn't include any game
files. You need your own copy of the game.
