"""Build, smoke-test, sign and publish a release.   npm run release [-- --dry-run]

    npm run version -- 0.2.0         set the version (src/opent5/__init__.py is the source;
                                     pyproject.toml and package.json follow it)
    npm run release -- --dry-run     everything except creating the GitHub release
    npm run release                  the same, then `gh release create`

Steps: refuse on a dirty tree or an existing tag; unlock the signing key (path from
OPENT5_RELEASE_KEY in .env, passphrase asked for); copy the tracked source to a fresh
build folder and run packaging\\build.bat there (through cmd.exe from WSL); run
tools/exe_smoke.py on the built exe (views, and --update: find, download, verify) which
must pass; write OpenT5.manifest.json (version, file, size, SHA-256) and sign it;
verify the signature with the app's own code and baked-in key; then `gh release create
vX.Y.Z OpenT5.exe OpenT5.manifest.json OpenT5.manifest.json.minisig --notes-file ...`.
See docs/updates.md.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

import opent5  # noqa: E402
from keygen import inside_repo, read_passphrase  # noqa: E402
from opent5 import update  # noqa: E402
from opent5.update import minisign  # noqa: E402
from opent5.update import version as semver  # noqa: E402

INIT = ROOT / "src" / "opent5" / "__init__.py"
#: The build folder (Windows path), kept apart from the working copy in C:\o5\src.
BUILD_DIR = r"C:\o5\rel"
EXE_NAME = f"{opent5.APP_NAME}.exe"


class Refused(Exception):
    pass


def git(*args: str, check: bool = True) -> str:
    r = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=60)
    if check and r.returncode != 0:
        raise Refused(f"git {' '.join(args)} failed: {r.stderr.strip()}")
    return r.stdout


def read_env() -> dict[str, str]:
    env = {}
    path = ROOT / ".env"
    if path.is_file():
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def current_version() -> str:
    m = re.search(r'^__version__ = "([^"]+)"$', INIT.read_text(), re.M)
    if not m:
        raise Refused(f"no __version__ line in {INIT}")
    return m.group(1)


#: Every file that holds the version, and the pattern that reads it. They must all agree, or a
#: release would ship mismatched versions (npm showed 0.2.0 while the zone read 0.3.0 once).
VERSION_FILES = (
    (INIT, r'^__version__ = "([^"]+)"$'),
    (ROOT / "pyproject.toml", r'^version = "([^"]+)"$'),
    (ROOT / "package.json", r'^  "version": "([^"]+)",$'),
)


def all_versions() -> dict[str, str]:
    out: dict[str, str] = {}
    for path, pattern in VERSION_FILES:
        m = re.search(pattern, path.read_text(), re.M)
        out[path.name] = m.group(1) if m else "(no version line)"
    return out


def check_versions(version: str) -> None:
    """Refuse unless every version-holding file matches ``version`` (the __init__ source)."""
    versions = all_versions()
    if any(v != version for v in versions.values()):
        detail = ", ".join(f"{name}={v}" for name, v in versions.items())
        raise Refused(
            f"version mismatch across files ({detail}); run: npm run version -- {version}"
        )


# -- npm run version -- X.Y.Z ------------------------------------------------------------


def set_version(new: str) -> int:
    if semver.parse(new) is None:
        print(f"version: expected MAJOR.MINOR.PATCH (no pre-release), found {new!r}")
        return 2
    old = current_version()
    edits = [
        (INIT, r'^__version__ = "[^"]+"$', f'__version__ = "{new}"'),
        (ROOT / "pyproject.toml", r'^version = "[^"]+"$', f'version = "{new}"'),
        (ROOT / "package.json", r'^  "version": "[^"]+",$', f'  "version": "{new}",'),
    ]
    for path, pattern, repl in edits:
        text = path.read_text()
        text2, n = re.subn(pattern, repl, text, count=1, flags=re.M)
        if n != 1:
            print(f"version: no version line found in {path.name}")
            return 1
        path.write_text(text2)
    print(f"version: {old} -> {new} (src/opent5/__init__.py, pyproject.toml, package.json)")
    print("Commit that, then: npm run release -- --dry-run")
    return 0


# -- paths between WSL and Windows -------------------------------------------------------


def on_wsl() -> bool:
    return os.name != "nt" and Path("/mnt/c").is_dir() and shutil.which("cmd.exe") is not None


def local(win_path: str) -> Path:
    if os.name == "nt":
        return Path(win_path)
    return Path("/mnt") / win_path[0].lower() / win_path[3:].replace("\\", "/")


def windows(path: Path) -> str:
    s = str(path)
    if os.name == "nt":
        return s
    m = re.match(r"^/mnt/([a-z])/(.*)$", s)
    if not m:
        raise Refused(f"{path} is not on a Windows drive")
    return f"{m.group(1).upper()}:\\" + m.group(2).replace("/", "\\")


# -- steps -------------------------------------------------------------------------------


def preflight(version: str, dry_run: bool, allow_dirty: bool) -> list[str]:
    """Refusals and warnings before anything slow. Returns warnings."""
    warnings = []
    if semver.parse(version) is None:
        raise Refused(f"__version__ {version!r} is not MAJOR.MINOR.PATCH")
    dirty = git("status", "--porcelain").rstrip("\n")
    if dirty.strip():
        if not (dry_run and allow_dirty):
            raise Refused(
                "the working tree is dirty; commit or stash first:\n"
                + "\n".join("  " + x for x in dirty.splitlines()[:20])
            )
        warnings.append("working tree is dirty (allowed for this dry run only)")
    check_versions(version)  # after the dirty check, so a dirty tree reports that first
    tag = f"v{version}"
    if git("tag", "--list", tag).strip():
        raise Refused(f"tag {tag} already exists; bump the version (npm run version -- X.Y.Z)")
    for what, value in (("REPO", update.REPO), ("PUBLIC_KEY", update.PUBLIC_KEY)):
        if not value:
            msg = f"opent5.update.{what} is empty: this build could never check for updates"
            if not dry_run:
                raise Refused(msg)
            warnings.append(msg)
    if not dry_run:
        r = subprocess.run(
            ["gh", "release", "view", tag, "--repo", update.REPO],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if r.returncode == 0:
            raise Refused(f"release {tag} already exists on GitHub")
        remote = git("ls-remote", "--tags", "origin", tag, check=False).strip()
        if remote:
            raise Refused(f"tag {tag} already exists on origin")
        if not git("branch", "-r", "--contains", "HEAD").strip():
            raise Refused("HEAD is not on any remote branch; push it first")
    return warnings


def unlock_key(env: dict[str, str], key_arg: str | None) -> minisign.SecretKey:
    raw = key_arg or env.get("OPENT5_RELEASE_KEY") or os.environ.get("OPENT5_RELEASE_KEY")
    if not raw:
        raise Refused("no signing key: set OPENT5_RELEASE_KEY in .env (npm run keygen makes one)")
    path = Path(raw).expanduser()
    if inside_repo(path):
        raise Refused(f"the signing key must live outside the repository: {path}")
    if not path.is_file():
        raise Refused(f"no signing key at {path}")
    passphrase = read_passphrase(f"Passphrase for {path}: ")
    try:
        return minisign.SecretKey.decrypt(path.read_text(), passphrase)
    except minisign.MinisignError as exc:
        raise Refused(f"could not unlock the signing key: {exc}") from None


def build(build_dir: str) -> Path:
    """Copy the tracked source to BUILD_DIR\\src and run packaging\\build.bat there."""
    src = local(build_dir) / "src"
    if src.exists():
        shutil.rmtree(src)
    src.mkdir(parents=True)
    files = git("ls-files", "-co", "--exclude-standard", "-z").split("\0")
    for name in filter(None, files):
        s = ROOT / name
        if s.is_file():
            d = src / name
            d.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(s, d)
    work = build_dir + r"\w"
    print(f"release: building in {build_dir}\\src (this takes a few minutes)")
    if os.name == "nt":
        env = dict(os.environ, O5_WORK=work)
        r = subprocess.run(["cmd", "/c", r"packaging\build.bat"], cwd=src, env=env)
    else:
        command = f"cd /d {build_dir}\\src && set O5_WORK={work}&& packaging\\build.bat"
        r = subprocess.run(["cmd.exe", "/c", command], cwd="/mnt/c")
    exe = src / "dist" / EXE_NAME
    if r.returncode != 0 or not exe.is_file():
        raise Refused(f"the build failed (exit code {r.returncode}); no {exe}")
    return exe


def smoke(exe: Path, zones: list[str], build_dir: str) -> None:
    cmd = [sys.executable, str(ROOT / "tools" / "exe_smoke.py"), windows(exe), *zones]
    cmd += ["--report", build_dir + r"\selftest.json", "--update"]
    cmd += ["--update-report", build_dir + r"\update-selftest.json"]
    print("release: smoke-testing the built exe")
    if subprocess.run(cmd, cwd=ROOT).returncode != 0:
        raise Refused("tools/exe_smoke.py failed on the built exe; not releasing")


def default_zones(env: dict[str, str]) -> list[str]:
    base = env.get("OPENT5_ZONES", "")
    names = ("mp_nuked.ff", "zombietron.ff")
    if not base or not all((Path(base) / n).is_file() for n in names):
        raise Refused("set OPENT5_ZONES in .env (or pass --zones) so the smoke test has zones")
    return [windows(Path(base) / n) for n in names]


def write_release(exe: Path, version: str, key: minisign.SecretKey, out: Path) -> list[Path]:
    out.mkdir(parents=True, exist_ok=True)
    final = out / EXE_NAME
    shutil.copyfile(exe, final)
    data = final.read_bytes()
    manifest = {
        "name": opent5.APP_NAME,
        "version": version,
        "file": EXE_NAME,
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }
    raw = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
    (out / update.MANIFEST_ASSET).write_bytes(raw)
    comment = f"{opent5.APP_NAME} manifest version:{version} file:{EXE_NAME}"
    sig = key.sign(raw, comment, f"{opent5.APP_NAME} {version} release manifest")
    (out / update.SIGNATURE_ASSET).write_text(sig)
    return [final, out / update.MANIFEST_ASSET, out / update.SIGNATURE_ASSET]


def check_release(files: list[Path], version: str, public: minisign.PublicKey) -> None:
    """Verify the files exactly as an installed copy will."""
    from opent5.update import check

    exe, manifest_path, sig_path = files
    raw = manifest_path.read_bytes()
    comment = minisign.verify(public, raw, sig_path.read_bytes())
    m = check.read_manifest(raw, version)
    if f"version:{version}" not in comment.split():
        raise Refused(f"trusted comment {comment!r} does not name {version}")
    if check.sha256_file(exe) != (m["size"], m["sha256"]):
        raise Refused("the exe does not match its manifest")


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(line_buffering=True)  # interleave in order with the build's output
    if argv is None:
        argv = sys.argv[1:]
    if argv[:1] == ["version"]:
        if len(argv) != 2:
            print("usage: npm run version -- X.Y.Z")
            return 2
        return set_version(argv[1])
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true", help="do everything but publish")
    ap.add_argument("--allow-dirty", action="store_true", help="dry run only: allow edits")
    ap.add_argument("--notes", type=Path, help="release notes (default docs/releases/X.Y.Z.md)")
    ap.add_argument("--key", help="signing key path (default: OPENT5_RELEASE_KEY from .env)")
    ap.add_argument("--zones", nargs="+", help="zones for the exe smoke test (Windows paths)")
    ap.add_argument("--build-dir", default=BUILD_DIR, help="Windows build folder")
    ap.add_argument("--exe", type=Path, help="dry run only: use this built exe, skip the build")
    ap.add_argument("--out", type=Path, help="where to write the release files")
    args = ap.parse_args(argv)
    if args.allow_dirty and not args.dry_run:
        print("release: --allow-dirty is for dry runs only")
        return 2
    if args.exe and not args.dry_run:
        print("release: --exe is for dry runs only; a release always builds afresh")
        return 2

    version = current_version()
    tag = f"v{version}"
    env = read_env()
    try:
        for w in preflight(version, args.dry_run, args.allow_dirty):
            print(f"release: warning: {w}")
        notes = args.notes or ROOT / "docs" / "releases" / f"{version}.md"
        if not notes.is_file():
            if not args.dry_run:
                raise Refused(f"no release notes at {notes}")
            print(f"release: warning: no release notes at {notes}")
        key = unlock_key(env, args.key)
        public = minisign.PublicKey.parse(update.PUBLIC_KEY) if update.PUBLIC_KEY else None
        if public is None:
            print("release: warning: verifying with the signing key's own public half")
            public = key.public
        elif public != key.public:
            raise Refused(
                "the signing key does not match opent5.update.PUBLIC_KEY; installed copies "
                "would reject this release"
            )
        if args.exe:
            exe = args.exe
        else:
            if not (on_wsl() or os.name == "nt"):
                raise Refused("building needs Windows (or WSL with cmd.exe)")
            exe = build(args.build_dir)
        smoke(exe, args.zones or default_zones(env), args.build_dir)
        out = args.out or ROOT / "dist" / f"release-{version}"
        files = write_release(exe, version, key, out)
        check_release(files, version, public)
    except Refused as exc:
        print(f"release: refused: {exc}")
        return 1
    print(f"release: {tag} ready in {out}:")
    for f in files:
        print(f"  {f.name}  {f.stat().st_size} bytes")
    head = git("rev-parse", "HEAD").strip()
    cmd = ["gh", "release", "create", tag, *map(str, files)]
    cmd += ["--title", f"{opent5.APP_NAME} {version}", "--notes-file", str(notes)]
    cmd += ["--target", head, "--repo", update.REPO or "<REPO>"]
    if args.dry_run:
        print("release: dry run; would now run:\n  " + " ".join(cmd))
        return 0
    r = subprocess.run(cmd, cwd=ROOT)
    if r.returncode != 0:
        print("release: gh release create failed")
        return 1
    print(f"release: published {tag}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
