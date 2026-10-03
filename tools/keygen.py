"""Create the release signing key, once.  npm run keygen [-- --out PATH]

Writes a minisign-format Ed25519 secret key, encrypted with a passphrase (scrypt, as
minisign does), to a path OUTSIDE the repository (default ~/.opent5-release/opent5.key,
mode 600), and the public key next to it (.pub). Refuses to overwrite an existing key.
Prints the public key line to paste into opent5.update.PUBLIC_KEY.

Back the secret key up offline (see docs/updates.md). If it is lost, installed copies
cannot verify any new release and must be reinstalled by hand with a new key.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from opent5.update import minisign  # noqa: E402

DEFAULT = Path.home() / ".opent5-release" / "opent5.key"
MIN_PASSPHRASE = 12


def read_passphrase(prompt: str) -> bytes:
    """From the terminal without echo; from stdin (one line) when not a terminal."""
    if sys.stdin.isatty():
        return getpass.getpass(prompt).encode()
    line = sys.stdin.readline()
    return line.rstrip("\r\n").encode()


def inside_repo(path: Path) -> bool:
    path = path.expanduser().resolve()
    return path == ROOT or ROOT in path.parents


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=DEFAULT, help=f"secret key path ({DEFAULT})")
    # Tests only: cheap scrypt limits instead of minisign's 1 GiB "sensitive" ones.
    ap.add_argument("--weak-kdf-for-tests", action="store_true", help=argparse.SUPPRESS)
    args = ap.parse_args(argv)

    out = args.out.expanduser().resolve()
    pub = out.with_suffix(".pub")
    if inside_repo(out):
        print(f"keygen: refusing to write the secret key inside the repository: {out}")
        return 1
    if out.exists() or pub.exists():
        print(f"keygen: a key already exists at {out if out.exists() else pub}; not overwriting")
        return 1
    first = read_passphrase("Passphrase for the new release key: ")
    second = read_passphrase("The same passphrase again: ")
    if first != second:
        print("keygen: the passphrases differ; nothing written")
        return 1
    if len(first) < MIN_PASSPHRASE:
        print(f"keygen: use a passphrase of at least {MIN_PASSPHRASE} characters")
        return 1

    key = minisign.SecretKey.generate()
    limits = {"opslimit": 32768, "memlimit": 16777216} if args.weak_kdf_for_tests else {}
    text = key.encrypt(first, **limits)
    out.parent.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        out.parent.chmod(0o700)
    fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    pub.write_text(key.public.file_text())
    line = key.public.line()
    print(f"secret key: {out} (mode 600, passphrase protected)")
    print(f"public key: {pub}")
    print()
    print("Paste this into src/opent5/update/__init__.py:")
    print(f'PUBLIC_KEY = "{line}"')
    print()
    print("Add to .env (never committed):")
    print(f"OPENT5_RELEASE_KEY={out}")
    print()
    print("Now back the secret key up offline (two copies, e.g. two USB sticks kept apart).")
    print("If it is lost, installed copies can never verify a new release.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
