"""Update self-test for the packaged exe: find, download and verify, never install.

    OpenT5.exe --update-selftest REPORT.json
        with OPENT5_UPDATE_TEST_URL=http://127.0.0.1:<port> and OPENT5_UPDATE_TEST_PUBKEY

tools/exe_smoke.py --update runs a fake release server with two release lists under the
override: <url>/good (a newer, correctly signed release) and <url>/tampered (the same
release with one byte of the exe changed). The exe must report "ready" for the first,
with the file in place and matching the manifest, and "failed-verification" for the
second, with nothing left behind. Nothing is swapped: updates found in test mode are
never installed. The JSON report says what happened; exit code 0 only when both hold.
"""

from __future__ import annotations

import json
import sys
import tempfile
import traceback
from dataclasses import replace
from pathlib import Path

import opent5
from opent5.update import check, swap


def run(report_path: str) -> int:
    report: dict = {
        "version": opent5.__version__,
        "frozen": bool(getattr(sys, "frozen", False)),
        "ok": False,
        "errors": [],
    }
    try:
        cfg = check.config()
        if isinstance(cfg, str) or not cfg.test_mode:
            raise RuntimeError(f"the test override is not in effect: {cfg}")
        exe = swap.running_exe()
        before = _state(exe)
        with tempfile.TemporaryDirectory(prefix="opent5-upd-") as tmp:
            good = check.check(
                download_dir=Path(tmp) / "good", cfg=replace(cfg, base=cfg.base + "/good")
            )
            good_ok = (
                good.status == "ready"
                and good.path is not None
                and good.path.is_file()
                and check.sha256_file(good.path)
                == (good.manifest.get("size"), good.manifest.get("sha256"))
                and good.test_mode
            )
            report["good"] = {
                "status": good.status,
                "message": good.message,
                "version": good.version,
                "notes": good.notes,
                "ok": good_ok,
            }
            bad_dir = Path(tmp) / "tampered"
            bad = check.check(download_dir=bad_dir, cfg=replace(cfg, base=cfg.base + "/tampered"))
            leftovers = sorted(p.name for p in bad_dir.glob("*")) if bad_dir.exists() else []
            report["tampered"] = {
                "status": bad.status,
                "message": bad.message,
                "leftovers": leftovers,
                "ok": bad.status == "failed-verification" and not leftovers,
            }
        after = _state(exe)
        report["exe_unchanged"] = before == after
        report["ok"] = good_ok and report["tampered"]["ok"] and report["exe_unchanged"]
    except Exception:  # noqa: BLE001 - the report must say what broke
        report["errors"].append(traceback.format_exc(limit=5))
    Path(report_path).write_text(json.dumps(report, indent=2) + "\n")
    return 0 if report["ok"] else 1


def _state(exe: Path | None) -> tuple:
    if exe is None:
        return ()
    st = exe.stat()
    return (
        st.st_size,
        st.st_mtime_ns,
        swap.old_path(exe).exists(),
        swap.new_path(exe).exists(),
    )
