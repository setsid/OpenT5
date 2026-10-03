"""The release tools: keygen, version bump, refusals, manifest signing (no build, no gh)."""

from __future__ import annotations

import io
import json
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import keygen  # noqa: E402
import release  # noqa: E402
from opent5 import update  # noqa: E402
from opent5.update import minisign  # noqa: E402


def _stdin(monkeypatch, text: str) -> None:
    monkeypatch.setattr(sys, "stdin", io.StringIO(text))


def test_keygen_writes_outside_the_repo_mode_600_and_never_overwrites(
    tmp_path, monkeypatch, capsys
):
    out = tmp_path / "keys" / "opent5.key"
    _stdin(monkeypatch, "a long passphrase\na long passphrase\n")
    assert keygen.main(["--out", str(out), "--weak-kdf-for-tests"]) == 0
    printed = capsys.readouterr().out
    assert (out.stat().st_mode & 0o777) == 0o600
    key = minisign.SecretKey.decrypt(out.read_text(), b"a long passphrase")
    assert f'PUBLIC_KEY = "{key.public.line()}"' in printed
    assert minisign.PublicKey.parse(out.with_suffix(".pub").read_text()) == key.public
    before = out.read_bytes()
    _stdin(monkeypatch, "another passphrase\nanother passphrase\n")
    assert keygen.main(["--out", str(out), "--weak-kdf-for-tests"]) == 1
    assert out.read_bytes() == before
    assert "already exists" in capsys.readouterr().out


def test_keygen_refusals(tmp_path, monkeypatch, capsys):
    inside = keygen.ROOT / "tmp-test-key.key"
    _stdin(monkeypatch, "a long passphrase\na long passphrase\n")
    assert keygen.main(["--out", str(inside), "--weak-kdf-for-tests"]) == 1
    assert not inside.exists()
    _stdin(monkeypatch, "a long passphrase\na different one!!\n")
    assert keygen.main(["--out", str(tmp_path / "k.key"), "--weak-kdf-for-tests"]) == 1
    _stdin(monkeypatch, "short\nshort\n")
    assert keygen.main(["--out", str(tmp_path / "k.key"), "--weak-kdf-for-tests"]) == 1
    assert not (tmp_path / "k.key").exists()


def test_set_version_updates_all_three(tmp_path, monkeypatch, capsys):
    for name in ("pyproject.toml", "package.json"):
        shutil.copy(release.ROOT / name, tmp_path / name)
    (tmp_path / "src" / "opent5").mkdir(parents=True)
    init = tmp_path / "src" / "opent5" / "__init__.py"
    shutil.copy(release.INIT, init)
    monkeypatch.setattr(release, "ROOT", tmp_path)
    monkeypatch.setattr(release, "INIT", init)
    assert release.main(["version", "3.4.5"]) == 0
    assert '__version__ = "3.4.5"' in init.read_text()
    assert 'version = "3.4.5"' in (tmp_path / "pyproject.toml").read_text()
    assert json.loads((tmp_path / "package.json").read_text())["version"] == "3.4.5"
    assert release.main(["version", "3.4.5-rc.1"]) == 2


def test_release_refuses_dirty_tree_and_existing_tag(monkeypatch):
    answers = {"status": " M src/x.py\n", "tag": ""}
    monkeypatch.setattr(release, "git", lambda *a, **k: answers.get(a[0], ""))
    with pytest.raises(release.Refused, match="dirty"):
        release.preflight("1.0.0", dry_run=True, allow_dirty=False)
    with pytest.raises(release.Refused, match="dirty"):
        release.preflight("1.0.0", dry_run=False, allow_dirty=True)
    answers.update(status="", tag="v1.0.0\n")
    with pytest.raises(release.Refused, match="already exists"):
        release.preflight("1.0.0", dry_run=True, allow_dirty=False)
    answers["tag"] = ""
    monkeypatch.setattr(update, "REPO", "")
    with pytest.raises(release.Refused, match="REPO is empty"):
        release.preflight("1.0.0", dry_run=False, allow_dirty=False)
    assert any("REPO" in w for w in release.preflight("1.0.0", True, False))


def test_release_cli_guards():
    assert release.main(["--allow-dirty"]) == 2
    assert release.main(["--exe", "x.exe"]) == 2


def test_manifest_is_signed_and_checks_like_a_client(tmp_path):
    key = minisign.SecretKey.generate()
    exe = tmp_path / "built.exe"
    exe.write_bytes(b"MZ" + bytes(5000))
    files = release.write_release(exe, "2.0.0", key, tmp_path / "out")
    assert [f.name for f in files] == ["OpenT5.exe", update.MANIFEST_ASSET, update.SIGNATURE_ASSET]
    release.check_release(files, "2.0.0", key.public)
    manifest = json.loads(files[1].read_text())
    assert manifest["version"] == "2.0.0" and manifest["size"] == 5002
    with pytest.raises(release.Refused):
        files[0].write_bytes(b"MZ" + bytes(5001))
        release.check_release(files, "2.0.0", key.public)
    with pytest.raises(minisign.MinisignError):
        release.check_release(files, "2.0.0", minisign.SecretKey.generate().public)


def test_windows_paths():
    assert release.windows(Path("/mnt/c/o5/rel/src")) == "C:\\o5\\rel\\src"
    assert release.local(r"C:\o5\rel") == Path("/mnt/c/o5/rel")
