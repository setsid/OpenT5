"""The update check: find, download and verify a newer release. No Qt; runs in a worker.

    outcome = check(failed_versions, download_dir)

Steps, any of which ends the check with an Outcome:
  1. configuration: REPO and PUBLIC_KEY set (or the 127.0.0.1 test override);
  2. GET /repos/{REPO}/releases; drop drafts, pre-releases and tags that are not a
     final semver; keep versions above this build's and not remembered as failed; take
     the highest;
  3. download the manifest and its minisign signature; verify the signature with the
     baked-in key, and that the manifest's version is the release's version;
  4. download the exe the manifest names (streamed, bounded by the manifest's size) and
     check its size and SHA-256 against the manifest.
A verification failure (3 or 4) discards anything downloaded and reports
"failed-verification"; the caller remembers that version and never tries it again.
Network trouble reports "error" and the next start simply tries again.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import opent5
from opent5 import update
from opent5.update import minisign, net
from opent5.update import version as semver

log = logging.getLogger("opent5.update")

#: Per-operation timeouts (connect, each read) and whole-transfer limits, in seconds.
LIST_TIMEOUT, LIST_TOTAL = 15.0, 60.0
SMALL_TIMEOUT, SMALL_TOTAL = 20.0, 120.0
EXE_TIMEOUT, EXE_TOTAL = 30.0, 1800.0

_FILE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}\.exe$")


@dataclass
class Outcome:
    """What a check found. status is one of: disabled, error, up-to-date, ready,
    failed-verification."""

    status: str
    message: str
    version: str = ""
    notes: str = ""
    path: Path | None = None
    manifest: dict = field(default_factory=dict)
    error_kind: str = ""
    test_mode: bool = False


@dataclass
class Release:
    version: tuple[int, int, int]
    tag: str
    notes: str
    assets: dict[str, dict]  # name -> {"url": ..., "size": ...}

    @property
    def text(self) -> str:
        return semver.text(self.version)


@dataclass
class Config:
    repo: str
    base: str
    public_key: minisign.PublicKey
    test_port: int | None = None

    @property
    def test_mode(self) -> bool:
        return self.test_port is not None


class VerificationError(Exception):
    pass


def config() -> Config | str:
    """The check's configuration, or a string saying why checking is disabled."""
    try:
        override = net.test_override()
    except net.NetError as exc:
        return f"update check disabled: {exc}"
    if override is not None:
        base, port = override
        key_text = os.environ.get(update.TEST_KEY_ENV, "")
        if not key_text:
            return f"update check disabled: {update.TEST_URL_ENV} needs {update.TEST_KEY_ENV}"
        try:
            key = minisign.PublicKey.parse(key_text)
        except minisign.MinisignError as exc:
            return f"update check disabled: {update.TEST_KEY_ENV}: {exc}"
        return Config(update.REPO or "opent5-test/opent5", base, key, port)
    if not update.REPO:
        return "update check disabled: no repository set (opent5.update.REPO is empty)"
    if not update.PUBLIC_KEY:
        return "update check disabled: no release key set (opent5.update.PUBLIC_KEY is empty)"
    try:
        key = minisign.PublicKey.parse(update.PUBLIC_KEY)
    except minisign.MinisignError as exc:
        return f"update check disabled: opent5.update.PUBLIC_KEY: {exc}"
    return Config(update.REPO, net.API_BASE, key)


def default_download_dir() -> Path:
    return Path(tempfile.gettempdir()) / f"{opent5.APP_NAME}-update"


def parse_releases(body: bytes) -> list[Release]:
    """Eligible releases from the GitHub release list: published, final, semver tags."""
    try:
        data = json.loads(body)
    except ValueError as exc:
        raise net.NetError("http", f"the release list is not JSON: {exc}") from None
    if not isinstance(data, list):
        raise net.NetError("http", f"expected a JSON list of releases, found {type(data).__name__}")
    out = []
    for item in data:
        if not isinstance(item, dict) or item.get("draft") or item.get("prerelease"):
            continue
        tag = item.get("tag_name")
        if not isinstance(tag, str):
            continue
        v = semver.parse(tag)
        if v is None:
            continue
        assets = {}
        for a in item.get("assets") or []:
            if isinstance(a, dict) and isinstance(a.get("name"), str) and a.get("url"):
                assets[a["name"]] = {"url": str(a["url"]), "size": a.get("size")}
        body_text = item.get("body")
        out.append(Release(v, tag, body_text if isinstance(body_text, str) else "", assets))
    return out


def pick(
    releases: list[Release], current: str, failed: set[str] | frozenset[str] = frozenset()
) -> Release | None:
    """The highest release above CURRENT that has not failed before. Never a downgrade."""
    now = semver.parse(current)
    if now is None:
        raise ValueError(f"this build's version {current!r} is not MAJOR.MINOR.PATCH")
    eligible = [r for r in releases if r.version > now and r.text not in failed]
    return max(eligible, key=lambda r: r.version, default=None)


def check(
    failed: set[str] | frozenset[str] = frozenset(),
    download_dir: Path | None = None,
    current: str | None = None,
    cfg: Config | str | None = None,
) -> Outcome:
    """Run one update check. Never raises for network or verification trouble."""
    current = current or opent5.__version__
    cfg = config() if cfg is None else cfg
    if isinstance(cfg, str):
        log.info(cfg)
        return Outcome("disabled", cfg)
    download_dir = download_dir or default_download_dir()
    try:
        body = net.fetch(
            f"{cfg.base}/repos/{cfg.repo}/releases?per_page=30",
            accept="application/vnd.github+json",
            limit=update.MAX_LIST_BYTES,
            test_port=cfg.test_port,
            timeout=LIST_TIMEOUT,
            total_timeout=LIST_TOTAL,
        )
        release = pick(parse_releases(body), current, failed)
        if release is None:
            msg = f"{opent5.APP_NAME} {current} is up to date"
            log.info(msg)
            return Outcome("up-to-date", msg, test_mode=cfg.test_mode)
        log.info("update check: %s is available", release.text)
        path, manifest = _download_verified(release, cfg, download_dir)
    except net.NetError as exc:
        msg = f"update check failed ({exc.kind}): {exc}"
        log.info(msg)
        return Outcome("error", msg, error_kind=exc.kind, test_mode=cfg.test_mode)
    except VerificationError as exc:
        msg = f"The update to {release.text} failed verification and was discarded"
        log.warning("%s: %s", msg, exc)
        return Outcome(
            "failed-verification", f"{msg}: {exc}", release.text, test_mode=cfg.test_mode
        )
    except OSError as exc:  # the temp folder: full disc, permissions
        msg = f"update check failed (disk): {exc}"
        log.info(msg)
        return Outcome("error", msg, error_kind="disk", test_mode=cfg.test_mode)
    msg = f"Version {release.text} is ready. Restart to update."
    return Outcome(
        "ready", msg, release.text, release.notes, path, manifest, test_mode=cfg.test_mode
    )


def read_manifest(raw: bytes, expected_version: str) -> dict:
    """The manifest's fields, checked; raise VerificationError on anything off."""
    try:
        m = json.loads(raw)
    except ValueError as exc:
        raise VerificationError(f"the manifest is not JSON: {exc}") from None
    if not isinstance(m, dict):
        raise VerificationError("the manifest is not a JSON object")
    if m.get("version") != expected_version:
        raise VerificationError(
            f"manifest version {m.get('version')!r} does not match release {expected_version!r}"
        )
    name, size, sha = m.get("file"), m.get("size"), m.get("sha256")
    if not isinstance(name, str) or not _FILE_NAME.match(name):
        raise VerificationError(f"manifest file name {name!r} is not a plain .exe name")
    if not isinstance(size, int) or isinstance(size, bool) or not 0 < size <= update.MAX_EXE_BYTES:
        raise VerificationError(f"manifest size {size!r} is out of range")
    if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{64}", sha):
        raise VerificationError(f"manifest sha256 {sha!r} is not 64 lower-case hex digits")
    return m


def sha256_file(path: Path) -> tuple[int, str]:
    h = hashlib.sha256()
    size = 0
    with open(path, "rb") as f:
        while chunk := f.read(1024 * 1024):
            h.update(chunk)
            size += len(chunk)
    return size, h.hexdigest()


def _asset(release: Release, name: str) -> dict:
    a = release.assets.get(name)
    if a is None:
        # Not a verification failure: the assets may still be uploading. Try next time.
        raise net.NetError("incomplete", f"release {release.tag} has no asset named {name!r}")
    return a


def _download_verified(release: Release, cfg: Config, download_dir: Path) -> tuple[Path, dict]:
    def get(name: str, limit: int) -> bytes:
        return net.fetch(
            _asset(release, name)["url"],
            accept="application/octet-stream",
            limit=limit,
            test_port=cfg.test_port,
            timeout=SMALL_TIMEOUT,
            total_timeout=SMALL_TOTAL,
        )

    raw_manifest = get(update.MANIFEST_ASSET, update.MAX_MANIFEST_BYTES)
    raw_sig = get(update.SIGNATURE_ASSET, update.MAX_SIGNATURE_BYTES)
    try:
        comment = minisign.verify(cfg.public_key, raw_manifest, raw_sig)
    except minisign.MinisignError as exc:
        raise VerificationError(str(exc)) from None
    manifest = read_manifest(raw_manifest, release.text)
    if f"version:{release.text}" not in comment.split():
        raise VerificationError(
            f"the signed comment {comment!r} does not name version {release.text}"
        )
    asset = _asset(release, manifest["file"])
    if isinstance(asset.get("size"), int) and asset["size"] != manifest["size"]:
        raise VerificationError(
            f"GitHub lists {manifest['file']} as {asset['size']} bytes, "
            f"the manifest says {manifest['size']}"
        )

    download_dir.mkdir(parents=True, exist_ok=True)
    final = download_dir / f"{opent5.APP_NAME}-{release.text}.exe"
    if final.is_file() and sha256_file(final) == (manifest["size"], manifest["sha256"]):
        log.info("update %s already downloaded and verified at %s", release.text, final)
        return final, manifest
    part = final.with_suffix(".part")
    h = hashlib.sha256()
    try:
        with open(part, "wb") as f:

            def sink(chunk: bytes) -> None:
                h.update(chunk)
                f.write(chunk)

            net.fetch(
                asset["url"],
                accept="application/octet-stream",
                limit=manifest["size"],
                test_port=cfg.test_port,
                timeout=EXE_TIMEOUT,
                total_timeout=EXE_TOTAL,
                sink=sink,
            )
        size = part.stat().st_size
        if size != manifest["size"]:
            raise VerificationError(
                f"downloaded {size} bytes, the manifest says {manifest['size']}"
            )
        if h.hexdigest() != manifest["sha256"]:
            raise VerificationError(
                f"SHA-256 of the download is {h.hexdigest()}, the manifest says "
                f"{manifest['sha256']}"
            )
        os.replace(part, final)
    except net.NetError as exc:
        with contextlib.suppress(OSError):
            part.unlink()
        if exc.kind == "too-large":
            raise VerificationError(f"the exe is larger than the manifest says: {exc}") from None
        raise
    except BaseException:
        with contextlib.suppress(OSError):
            part.unlink()
        raise
    log.info("update %s downloaded and verified: %s", release.text, final)
    return final, manifest
