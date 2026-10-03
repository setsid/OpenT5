"""Install a verified update by swapping the exe, with rollback; clean up afterwards.

Windows cannot overwrite a running exe, but it can rename one. So:
  1. stage: copy the verified download next to the exe as <name>.new and check its
     size and SHA-256 against the manifest again (the temp folder is not trusted);
  2. rename the running exe to <name>.old;
  3. rename <name>.new to <name>;
  4. start the new exe; the caller then exits.
If a step fails, the steps already done are undone in reverse (the original exe gets its
name back), so the installed version keeps working, and SwapError says what happened.
On the next start, cleanup_old() deletes <name>.old once the old process has exited.
Only a frozen build (sys.frozen) swaps; from source there is no exe to replace.
"""

from __future__ import annotations

import contextlib
import logging
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

from opent5.update.check import sha256_file

log = logging.getLogger("opent5.update")


class SwapError(Exception):
    """The swap failed; message says what, and whether the old exe was restored."""


class Ops:
    """The file and process operations a swap uses (tests replace them to inject faults)."""

    def copy(self, src: Path, dst: Path) -> None:
        shutil.copyfile(src, dst)

    def rename(self, src: Path, dst: Path) -> None:
        os.replace(src, dst)

    def unlink(self, path: Path) -> None:
        path.unlink()

    def launch(self, exe: Path) -> None:
        env = dict(os.environ)
        # A PyInstaller one-file exe started from another must not reuse the parent's
        # unpacked files, which the parent deletes when it exits.
        env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
        flags = 0
        if os.name == "nt":
            flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        subprocess.Popen(  # noqa: S603 - the verified exe, by absolute path, no shell
            [str(exe)], cwd=str(exe.parent), env=env, close_fds=True, creationflags=flags
        )


def running_exe() -> Path | None:
    """The running exe when frozen, else None."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve()
    return None


def old_path(exe: Path) -> Path:
    return exe.with_name(exe.name + ".old")


def new_path(exe: Path) -> Path:
    return exe.with_name(exe.name + ".new")


def apply(new_file: Path, exe: Path, size: int, sha256: str, ops: Ops | None = None) -> None:
    """Swap EXE for NEW_FILE and start it. Raises SwapError after rolling back."""
    ops = ops or Ops()
    staged, old = new_path(exe), old_path(exe)
    undo: list[tuple[str, Callable[[], None]]] = []

    def rollback(step: str, exc: BaseException) -> SwapError:
        problems = []
        for what, fn in reversed(undo):
            try:
                fn()
            except OSError as e:
                problems.append(f"{what}: {e}")
        if problems:
            log.error("update rollback incomplete: %s", "; ".join(problems))
            return SwapError(
                f"The update failed while {step} ({exc}), and putting the old version back "
                f"also failed ({'; '.join(problems)}). The previous exe may be at {old}."
            )
        log.warning("update failed while %s (%s); rolled back", step, exc)
        return SwapError(
            f"The update failed while {step} ({exc}). Nothing was changed: "
            f"{exe.name} is still the previous version."
        )

    try:
        if old.exists():
            ops.unlink(old)  # left from an earlier update whose clean-up failed
        ops.copy(new_file, staged)
        undo.append(("removing the staged copy", lambda: ops.unlink(staged)))
        found = sha256_file(staged)
        if found != (size, sha256):
            raise OSError(f"the staged copy is {found}, expected {(size, sha256)}")
    except OSError as exc:
        raise rollback("staging the new version", exc) from None
    try:
        ops.rename(exe, old)
        undo.append(("restoring the original exe", lambda: ops.rename(old, exe)))
    except OSError as exc:
        raise rollback("moving the running exe aside", exc) from None
    try:
        ops.rename(staged, exe)
        # Undone first: move the new exe aside, then the old one returns, then the staged
        # copy is removed.
        undo.append(("moving the new exe aside", lambda: ops.rename(exe, staged)))
    except OSError as exc:
        raise rollback("moving the new version into place", exc) from None
    try:
        ops.launch(exe)
    except OSError as exc:
        raise rollback("starting the new version", exc) from None
    log.info("update installed; started %s", exe)


def cleanup_old(exe: Path | None = None, tries: int = 20, delay: float = 0.5) -> bool:
    """Delete <exe>.old (and a stray <exe>.new) left by an update. True when none is left.

    The old process may still be exiting, so retry for up to tries * delay seconds.
    """
    exe = exe or running_exe()
    if exe is None:
        return True
    with contextlib.suppress(OSError):
        new_path(exe).unlink(missing_ok=True)
    old = old_path(exe)
    for i in range(tries):
        try:
            old.unlink(missing_ok=True)
            if i:
                log.info("removed %s", old)
            return True
        except OSError:
            time.sleep(delay)
    log.info("could not remove %s yet; will try on the next start", old)
    return False
