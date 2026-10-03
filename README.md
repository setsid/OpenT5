# OpenT5

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

## Layout

    src/opent5/container   the signed, encrypted, compressed fastfile container
    docs/research          format research; every claim carries its evidence

## Licence

OpenT5 is free software under the GNU General Public License, version 3 or
later; see LICENSE. Some code is adapted from OpenAssetTools (also GPLv3);
docs/provenance.md records what was taken from where.
