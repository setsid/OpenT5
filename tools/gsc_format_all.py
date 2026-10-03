"""Check the GSC formatter on every GSC / CSC rawfile of every zone.

    .venv/bin/python tools/gsc_format_all.py [--jobs 6] [--timeout 300] [--json out.json]
    .venv/bin/python tools/gsc_format_all.py --match patch_mp

For every script: ``unformat_script(format_script(text)) == text`` exactly (the save policy
of opent5.edit.gscformat), and the formatted text's indentation is balanced and consistent
(``check_consistent``: braces balance, each closing brace sits at its opener's level).
Each zone runs in its own process with a timeout (tools/walk_all.py's runner).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

from opent5 import env  # noqa: E402
from walk_all import run  # noqa: E402


def check_zone(path: str) -> dict:
    try:
        return _check_zone(path)
    except Exception as exc:  # report, never stop the run
        return {"path": path, "zone": Path(path).stem, "ok": False, "error": repr(exc)}


def _check_zone(path: str) -> dict:
    from opent5.container.zone import Zone
    from opent5.edit import content as ct
    from opent5.edit import gscformat as g
    from opent5.xfile import parse
    from opent5.xfile.constants import AssetType as T

    started = time.perf_counter()
    zone = Zone.open(Path(path))
    xfile = parse(bytes(zone.content), log=False)
    row = {"path": path, "zone": zone.name, "ok": True, "scripts": 0, "lines": 0}
    row["round_trip_failures"] = []
    row["brace_problems"] = []
    row["unchanged_by_format"] = 0
    row["hashes"] = []
    for a in xfile.assets:
        if a.type != T.RAWFILE or not isinstance(a.data, dict) or not g.is_script(a.name):
            continue
        text = ct.rawfile_text(a.data)
        formatted = g.format_script(text)
        row["scripts"] += 1
        row["lines"] += text.count("\n") + 1
        row["hashes"].append(hashlib.sha1(text.encode("latin-1")).hexdigest())
        if formatted == text:
            row["unchanged_by_format"] += 1
        if g.unformat_script(formatted) != text:
            row["ok"] = False
            row["round_trip_failures"].append(a.name)
        problems = g.check_consistent(text)
        if problems:
            row["brace_problems"].append({"script": a.name, "problems": problems[:3]})
    row["seconds"] = round(time.perf_counter() - started, 2)
    return row


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--jobs", type=int, default=6)
    ap.add_argument("--timeout", type=float, default=300)
    ap.add_argument("--match", help="only zones whose path contains this")
    ap.add_argument("--json", help="write every row here")
    args = ap.parse_args(argv)
    paths = [p for p in env.all_zones() if not args.match or args.match in str(p)]
    started = time.monotonic()
    rows = run(paths, args.jobs, args.timeout, work=check_zone)
    done = [r for r in rows if "scripts" in r]
    unique = {h for r in done for h in r["hashes"]}
    failures = [(r["zone"], s) for r in done for s in r["round_trip_failures"]]
    braces = [(r["zone"], b) for r in done for b in r["brace_problems"]]
    summary = {
        "zones": len(paths),
        "zones_checked": len(done),
        "zones_not_read": [
            {"zone": r.get("zone"), "path": r["path"], "error": r.get("error") or r.get("kind")}
            for r in rows
            if "scripts" not in r
        ],
        "scripts": sum(r["scripts"] for r in done),
        "distinct_scripts": len(unique),
        "lines": sum(r["lines"] for r in done),
        "round_trip_exact": sum(r["scripts"] for r in done) - len(failures),
        "round_trip_failures": failures,
        "brace_problems": braces,
        "seconds": round(time.monotonic() - started, 1),
    }
    if args.json:
        for r in rows:
            r.pop("hashes", None)
        Path(args.json).write_text(json.dumps({"summary": summary, "rows": rows}, indent=1))
    print(json.dumps(summary, indent=1))
    return 0 if not failures and not summary["zones_not_read"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
