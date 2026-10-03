"""Swapping the exe for a verified update, with rollback at every step, and clean-up.

A fake "exe" in a temp folder stands in for OpenT5.exe. Windows refuses to overwrite a
running exe but allows renaming it; the swap only ever renames the running file, so the
same sequence runs here. The launched "new exe" is a shell script that leaves a marker.
"""

from __future__ import annotations

import hashlib
import os
import stat
import sys
import time
from pathlib import Path

import pytest

from opent5.update import swap

pytestmark = pytest.mark.skipif(os.name == "nt", reason="uses a shell script as the fake exe")


def _script(marker: Path, tag: str) -> bytes:
    return f"#!/bin/sh\necho {tag} > '{marker}'\n".encode()


@pytest.fixture
def setup(tmp_path):
    install = tmp_path / "install"
    install.mkdir()
    marker = tmp_path / "launched.txt"
    exe = install / "OpenT5.exe"
    exe.write_bytes(_script(marker, "old"))
    exe.chmod(0o755)
    download = tmp_path / "download" / "OpenT5-9.0.0.exe"
    download.parent.mkdir()
    new = _script(marker, "new")
    download.write_bytes(new)
    download.chmod(0o755)
    return exe, download, len(new), hashlib.sha256(new).hexdigest(), marker


class _ExecOps(swap.Ops):
    """Real file operations; launch runs the script and waits for it."""

    def launch(self, exe: Path) -> None:
        exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
        super().launch(exe)


def _wait(marker: Path) -> str:
    for _ in range(200):
        if marker.is_file() and marker.read_text().strip():
            return marker.read_text().strip()
        time.sleep(0.01)
    return ""


def test_swap_installs_and_relaunches(setup):
    exe, download, size, sha, marker = setup
    swap.apply(download, exe, size, sha, ops=_ExecOps())
    assert exe.read_bytes() == download.read_bytes()
    assert swap.old_path(exe).read_bytes().startswith(b"#!/bin/sh\necho old")
    assert not swap.new_path(exe).exists()
    assert _wait(marker) == "new"
    # Next start: the .old copy is removed.
    assert swap.cleanup_old(exe, tries=1)
    assert not swap.old_path(exe).exists()
    assert sorted(p.name for p in exe.parent.iterdir()) == ["OpenT5.exe"]


class _Fail(_ExecOps):
    def __init__(self, op: str, nth: int = 1):
        self.op, self.nth, self.seen = op, nth, 0

    def _maybe(self, op: str) -> None:
        if op == self.op:
            self.seen += 1
            if self.seen == self.nth:
                raise PermissionError(f"injected failure in {op} #{self.nth}")

    def copy(self, src, dst):
        self._maybe("copy")
        super().copy(src, dst)

    def rename(self, src, dst):
        self._maybe("rename")
        super().rename(src, dst)

    def launch(self, exe):
        self._maybe("launch")
        super().launch(exe)


@pytest.mark.parametrize(
    "op,nth,step",
    [
        ("copy", 1, "staging the new version"),
        ("rename", 1, "moving the running exe aside"),
        ("rename", 2, "moving the new version into place"),
        ("launch", 1, "starting the new version"),
    ],
)
def test_failure_at_each_step_rolls_back(setup, op, nth, step):
    exe, download, size, sha, marker = setup
    original = exe.read_bytes()
    with pytest.raises(swap.SwapError) as e:
        swap.apply(download, exe, size, sha, ops=_Fail(op, nth))
    assert step in str(e.value) and "still the previous version" in str(e.value)
    assert exe.read_bytes() == original  # the old exe is back under its own name
    assert sorted(p.name for p in exe.parent.iterdir()) == ["OpenT5.exe"]
    assert not marker.exists()  # nothing was started


def test_staged_copy_checked_against_manifest(setup):
    exe, download, size, sha, _marker = setup
    original = exe.read_bytes()
    with pytest.raises(swap.SwapError, match="staging"):
        swap.apply(download, exe, size, "0" * 64, ops=_ExecOps())
    assert exe.read_bytes() == original
    assert sorted(p.name for p in exe.parent.iterdir()) == ["OpenT5.exe"]


def test_failed_rollback_says_so(setup):
    exe, download, size, sha, _marker = setup

    class _Worse(_Fail):
        def rename(self, src, dst):
            if Path(src).name.endswith(".old"):
                raise PermissionError("cannot restore")
            super().rename(src, dst)

    with pytest.raises(swap.SwapError, match="putting the old version back also failed"):
        swap.apply(download, exe, size, sha, ops=_Worse("launch"))


def test_stale_old_and_new_removed_on_start(tmp_path):
    exe = tmp_path / "OpenT5.exe"
    exe.write_bytes(b"x")
    swap.old_path(exe).write_bytes(b"old")
    swap.new_path(exe).write_bytes(b"new")
    assert swap.cleanup_old(exe, tries=1)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["OpenT5.exe"]


def test_cleanup_retries_then_gives_up(tmp_path, monkeypatch):
    exe = tmp_path / "OpenT5.exe"
    exe.write_bytes(b"x")
    swap.old_path(exe).write_bytes(b"old")
    calls = []

    def busy(self, missing_ok=False):
        calls.append(self)
        raise PermissionError("in use")

    monkeypatch.setattr(Path, "unlink", busy)
    assert not swap.cleanup_old(exe, tries=3, delay=0.001)
    assert len([c for c in calls if c.name.endswith(".old")]) == 3


def test_not_frozen_means_no_running_exe(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert swap.running_exe() is None
    assert swap.cleanup_old() is True
