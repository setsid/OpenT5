"""Smoke-test the BUILT OpenT5.exe, not the source tree.

    .venv/bin/python tools/exe_smoke.py C:\\o5\\src\\dist\\OpenT5.exe \\
        C:\\...\\english\\mp_nuked.ff C:\\...\\english\\zombietron.ff

Runs the exe offscreen (QT_QPA_PLATFORM=offscreen, fonts from C:\\Windows\\Fonts) with
--selftest: it opens each zone, shows one asset of every type in every view the GUI
offers for it (text, table, localize, image, geometry, fields, hex), and writes a JSON
report. This tool waits for the report and fails when any view could not be created
(a module missing from the build), raised, or showed an import error, or when a
required view or asset type was never exercised. From WSL the exe is started through
cmd.exe; on Windows it is started directly.

Exit code 0 when the report is clean, 1 otherwise.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path


def windows_to_local(path: str) -> Path:
    """C:\\a\\b -> /mnt/c/a/b under WSL; unchanged on Windows."""
    if os.name == "nt" or len(path) < 3 or path[1] != ":":
        return Path(path)
    return Path("/mnt") / path[0].lower() / path[3:].replace("\\", "/")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("exe", help="the built OpenT5.exe (Windows path)")
    ap.add_argument("zones", nargs="+", help="zones to open (Windows paths)")
    ap.add_argument("--report", default=r"C:\o5\selftest.json", help="Windows path")
    ap.add_argument("--timeout", type=float, default=900)
    args = ap.parse_args(argv)

    report = windows_to_local(args.report)
    report.unlink(missing_ok=True)
    command = (
        "set QT_QPA_PLATFORM=offscreen&& set QT_QPA_FONTDIR=C:\\Windows\\Fonts&& "
        f"{args.exe} --selftest {args.report} " + " ".join(args.zones)
    )
    started = time.perf_counter()
    if os.name == "nt":
        env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
        code = subprocess.run(
            [args.exe, "--selftest", args.report, *args.zones], env=env, timeout=args.timeout
        ).returncode
    else:
        # A windowed exe returns to cmd.exe at once, so wait for the report to appear and
        # stop changing.
        code = subprocess.run(["cmd.exe", "/c", command], cwd="/mnt/c").returncode
        deadline = started + args.timeout
        last = -1
        while time.perf_counter() < deadline:
            if report.is_file() and report.stat().st_size == last and last > 0:
                break
            last = report.stat().st_size if report.is_file() else -1
            time.sleep(2)
    if not report.is_file():
        print(f"exe_smoke: no report at {args.report} (exit code {code})", file=sys.stderr)
        return 1
    data = json.loads(report.read_text())
    seconds = time.perf_counter() - started
    print(
        f"{args.exe}: {data['checks']} view checks, {len(data['failed'])} failed, "
        f"{len(data['errors'])} errors, {seconds:.0f} s"
    )
    print(f"  views exercised: {', '.join(data['views_exercised'])}")
    print(f"  types exercised: {', '.join(data['types_exercised'])}")
    for f in data["failed"]:
        print(f"  FAIL {f['zone']} {f['type']} {f['asset']!r} {f['view']}: {f['problem']}")
    for e in data["errors"]:
        print(f"  ERROR {e}")
    if data["missing_types"] or data["missing_views"]:
        print(f"  not exercised: {data['missing_types'] + data['missing_views']}")
    return 0 if data["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
