# OpenT5

By setsid.

A standalone toolkit for Call of Duty: Black Ops (engine T5) PS3 fastfiles:
open, browse, edit and rebuild zones, and in time build custom maps.

## Signatures

Every retail zone carries an RSA-2048 signature at 0x3c. It cannot be
regenerated without the publisher's private key. A modified zone keeps the
original signature bytes, which no longer match, so it loads only on a client
with the signature check patched out. OpenT5 says so whenever it saves.

## Setup

    cp .env.example .env      # point it at your own dump; nothing is ever written there
    npm run setup
    npm run test
    npm run lint
    npm run build

Game files (`.ff`, `.self`, `.elf`, `.wad`, extracted assets) are never
committed. Tests that need real zones find them through `.env` and skip when
they are absent.

## Running

    .venv/bin/opent5 info mp_nuked          # command line; see docs/cli.md
    npm run gui                             # desktop app; see docs/gui.md

## Windows build

`packaging\build.bat`, run on Windows from a copy of the source at a short path
(for example `C:\o5\src`), creates a short-path venv at `C:\o5\v` with the
python.org Python 3.12 (not the Store build, which breaks the 260-character path
limit) and writes `dist\OpenT5.exe`. Put a `.env` next to the exe to list your zone
folders on the start page.

Then smoke-test the built exe itself (not the source tree): it opens the zones
offscreen, shows one asset of every type in every view, and fails on any view that
could not load:

    .venv/bin/python tools/exe_smoke.py 'C:\o5\src\dist\OpenT5.exe' \
        'C:\...\english\mp_nuked.ff' 'C:\...\english\zombietron.ff'

`OpenT5.exe --screenshots DIR` (with `QT_QPA_PLATFORM=offscreen`) renders the review
screenshots from the exe.

## Layout

    src/opent5/container   the signed, encrypted, compressed fastfile container
    src/opent5/xfile       the zone stream: parser, writer, field schemas, references, remap
    src/opent5/edit        the editing API shared by the CLI and the GUI (docs/edit-api.md)
    src/opent5/export      extraction to PNG, OBJ, JSON, CSV and text
    src/opent5/gui         the desktop app
    src/opent5/cli.py      the command line
    docs/research          format research; every claim carries its evidence

## Licence

OpenT5 is by setsid and is free software under the GNU General Public License, version 3 or
later; see LICENSE. Some code is adapted from OpenAssetTools (also GPLv3);
docs/provenance.md records what was taken from where.
