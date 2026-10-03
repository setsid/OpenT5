"""Check the field schemas over every zone: coverage, round trip, sanity.

    .venv/bin/python tools/fields_all.py                       # every zone in .env
    .venv/bin/python tools/fields_all.py --match mp_nuked
    .venv/bin/python tools/fields_all.py --json fields.json --doc docs/research/fields.md

Per zone (one process each, as walk_all.py): parse, then
opent5.xfile.fieldcheck.check_zone sets every field of every struct to its own
value through the view (vectorised for long arrays) and checks the asset bytes
are unchanged, measures how much of each struct is named, and runs the sanity
checks that show the types fit the data. The tables report everything merged.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

from opent5 import env  # noqa: E402
from opent5.container.zone import Zone  # noqa: E402
from opent5.xfile import parse, write_asset  # noqa: E402
from opent5.xfile.fieldcheck import ZoneCheck, check_zone  # noqa: E402
from walk_all import label_of, run, write_doc  # noqa: E402

DOC = ROOT / "docs" / "research" / "fields.md"
START = "<!-- FIELDS START -->"
END = "<!-- FIELDS END -->"


def fields_one(path: str) -> dict:
    row: dict = {"path": path, "zone": Path(path).stem, "label": label_of(Path(path))}
    try:
        content = bytes(Zone.open(path).content)
        started = time.perf_counter()
        xfile = parse(content, log=False)
        row["parse_s"] = round(time.perf_counter() - started, 2)
        started = time.perf_counter()
        check = check_zone(xfile.assets, set_fields=True)
        row["check_s"] = round(time.perf_counter() - started, 2)
        # After every field was set to its own value, every asset must write back as before.
        changed = [
            a.index for a in xfile.assets if write_asset(a) != content[a.file_start : a.file_end]
        ]
        row["assets"] = len(xfile.assets)
        row["assets_changed_by_set"] = changed[:20]
        row["check"] = check.as_dict()
        row["ok"] = not changed
    except Exception as exc:  # report, never stop the run
        row["ok"] = False
        row["error"] = {
            "kind": type(exc).__name__,
            "message": str(exc),
            "traceback": traceback.format_exc(limit=8),
        }
    return row


def merge(rows: list[dict]) -> ZoneCheck:
    total = ZoneCheck()
    for row in rows:
        if "check" in row:
            total.merge(row["check"])
    return total


def tables(rows: list[dict], total: ZoneCheck, wall: float) -> str:
    structs = sorted(total.structs.items(), key=lambda kv: -kv[1].bytes)
    all_bytes = sum(s.bytes for _, s in structs)
    named = sum(s.named for _, s in structs)
    unknown = sum(s.unknown for _, s in structs)
    untyped = sum(total.untyped.values())
    failures = sum(s.round_trip_failures for _, s in structs)
    changed = sum(len(r.get("assets_changed_by_set", [])) for r in rows)
    out = [
        f"{len(rows)} zones, {sum(r.get('assets', 0) for r in rows)} assets, wall {wall:.0f} s. "
        f"Struct bytes under a schema: {all_bytes}; named {named} "
        f"({100 * named / max(all_bytes, 1):.2f}%), unk_ {unknown} "
        f"({100 * unknown / max(all_bytes, 1):.2f}%). Opaque blobs (pixels, vertex streams, "
        f"programs, text): {total.blob_bytes} bytes. Byte values with no schema: {untyped} "
        f"bytes. Field round-trip failures: {failures}; assets changed by setting every "
        f"field to its own value: {changed}. Zones with errors: "
        f"{sum(1 for r in rows if 'error' in r)}.",
        "",
        "### Coverage by struct",
        "",
        "| Struct | Instances | Bytes | Named % | unk_ % | Round-trip failures | " "char tails |",
        "|---|---|---|---|---|---|---|",
    ]
    for name, s in structs:
        out.append(
            f"| {name} | {s.instances} | {s.bytes} | {100 * s.named / max(s.bytes, 1):.1f} | "
            f"{100 * s.unknown / max(s.bytes, 1):.1f} | {s.round_trip_failures} | "
            f"{s.char_tails} |"
        )
    if total.untyped:
        out += ["", "### Byte values with no schema", "", "| Node[key] | Bytes |", "|---|---|"]
        for key, n in sorted(total.untyped.items(), key=lambda kv: -kv[1]):
            out.append(f"| {key} | {n} |")
    bad = [(k, s) for k, s in total.sanity.items() if s.bad]
    out += ["", "### Sanity checks that fail", ""]
    if bad:
        out += ["| Check | Values | Failing | Example | Reason |", "|---|---|---|---|---|"]
        for key, s in sorted(bad, key=lambda kv: -kv[1].bad / max(kv[1].values, 1)):
            out.append(f"| {key} | {s.values} | {s.bad} | {s.example} | {s.reason} |")
    else:
        out.append("None.")
    passed = sum(1 for s in total.sanity.values() if not s.bad)
    out += [
        "",
        f"{passed} other checks pass on every value "
        f"({sum(s.values for s in total.sanity.values() if not s.bad)} values).",
    ]
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--timeout", type=float, default=1800)
    parser.add_argument("--match", action="append", default=[])
    parser.add_argument("--json", type=Path)
    parser.add_argument("--doc", type=Path)
    parser.add_argument("--from-json", type=Path)
    parser.add_argument("zones", nargs="*", type=Path)
    args = parser.parse_args(argv)
    if args.from_json:
        saved = json.loads(args.from_json.read_text())
        rows, wall = saved["rows"], saved["wall_s"]
    else:
        paths = args.zones or env.all_zones()
        if args.match:
            paths = [p for p in paths if any(m in str(p) for m in args.match)]
        started = time.perf_counter()
        rows = run(paths, args.jobs, args.timeout, work=fields_one)
        wall = time.perf_counter() - started
        if args.json:
            args.json.write_text(json.dumps({"rows": rows, "wall_s": wall}, indent=1, default=str))
    total = merge(rows)
    text = tables(rows, total, wall)
    if args.doc:
        write_doc(args.doc, text, START, END)
    print(text[:3000])
    return 0 if all(r.get("ok") for r in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
