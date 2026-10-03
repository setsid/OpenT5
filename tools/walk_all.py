"""Walk every zone with the XFile parser and report what does not parse exactly.

    .venv/bin/python tools/walk_all.py                       # every zone in .env
    .venv/bin/python tools/walk_all.py --jobs 4 --timeout 900 --json walk.json
    .venv/bin/python tools/walk_all.py --match mp_nuked --match patch
    .venv/bin/python tools/walk_all.py --doc docs/research/walk-all.md   # refresh the tables

A zone passes when every asset parses, the walk consumes the stream exactly
(asset data, then the deferred tail, ends at the stream length), and every
block ends at the size the XFile header declares. Each zone runs in its own
process with a wall-clock timeout; a zone that overruns is killed and
reported. Results print as a summary and, with --json, as one row per zone.
"""

from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from opent5 import env  # noqa: E402
from opent5.container.zone import Zone  # noqa: E402
from opent5.xfile import AssetError, parse  # noqa: E402
from opent5.xfile.constants import type_name  # noqa: E402


def label_of(path: Path) -> str:
    """disc / update / hdd-<lang> / dlcN-<lang>, from where the zone lives."""
    roots = {
        "disc": env.path_of("OPENT5_ZONES"),
        "update": env.path_of("OPENT5_PATCH_ZONES"),
        "hdd": env.path_of("OPENT5_DLC_ZONES"),
    }
    for label, root in roots.items():
        if root is None:
            continue
        try:
            rel = path.relative_to(root)
        except ValueError:
            continue
        if label == "hdd":
            parts = rel.parts[:-1]
            return "-".join(("hdd",) + parts) if len(parts) == 1 else "-".join(parts)
        return label
    return path.parent.name


def walk_one(path: str) -> dict:
    row: dict = {"path": path, "zone": Path(path).stem, "label": label_of(Path(path))}
    try:
        started = time.perf_counter()
        content = Zone.open(path).content
        row["inflate_s"] = round(time.perf_counter() - started, 2)
        row["length"] = len(content)
        started = time.perf_counter()
        try:
            xfile = parse(content)
        except AssetError as exc:
            row["parse_s"] = round(time.perf_counter() - started, 2)
            row["ok"] = False
            row["error"] = {
                "kind": "asset",
                "index": exc.index,
                "type": exc.asset_type,
                "type_name": type_name(exc.asset_type),
                "names": exc.strings,
                "file_start": exc.file_start,
                "file_offset": exc.file_offset,
                "handler": "/".join(exc.trail),
                "message": str(exc),
            }
            return row
        row["parse_s"] = round(time.perf_counter() - started, 2)
        problems = xfile.problems()
        row["ok"] = not problems
        row["problems"] = problems
        row["assets"] = len(xfile.assets)
        row["counts"] = xfile.counts()
        row["script_strings"] = len(xfile.script_strings)
        row["tail_bytes"] = xfile.end_offset - xfile.tail_offset
        row["deferred_reads"] = len(xfile.deferred)
        row["events"] = len(xfile.log) if xfile.log is not None else 0
        row["block_sizes"] = list(xfile.header.block_sizes)
    except Exception as exc:  # report, never stop the run
        row["ok"] = False
        row["error"] = {
            "kind": type(exc).__name__,
            "message": str(exc),
            "traceback": traceback.format_exc(limit=6),
        }
    return row


def _child(path: str, conn) -> None:
    conn.send(walk_one(path))
    conn.close()


def _failed(path: str, kind: str, message: str) -> dict:
    return {
        "path": path,
        "zone": Path(path).stem,
        "ok": False,
        "error": {"kind": kind, "message": message},
    }


def run(paths: list[Path], jobs: int, timeout: float, progress: bool = True) -> list[dict]:
    """Walk zones in parallel, one process per zone, killing any that overrun."""
    ctx = mp.get_context("fork")
    pending = [str(p) for p in paths]
    running: dict = {}
    rows: list[dict] = []
    while pending or running:
        while pending and len(running) < jobs:
            path = pending.pop(0)
            parent, child = ctx.Pipe(duplex=False)
            proc = ctx.Process(target=_child, args=(path, child), daemon=True)
            proc.start()
            child.close()
            running[path] = (proc, parent, time.monotonic())
        time.sleep(0.05)
        for path, (proc, conn, started) in list(running.items()):
            if conn.poll():
                try:
                    row = conn.recv()
                except EOFError:
                    row = _failed(path, "crash", f"no result, exit code {proc.exitcode}")
                proc.join(5)
            elif not proc.is_alive():
                row = _failed(path, "crash", f"exit code {proc.exitcode}")
            elif time.monotonic() - started > timeout:
                proc.kill()
                proc.join(5)
                row = _failed(path, "timeout", f"over {timeout:.0f} s")
            else:
                continue
            row.setdefault("label", label_of(Path(path)))
            row["wall_s"] = round(time.monotonic() - started, 2)
            rows.append(row)
            conn.close()
            del running[path]
            if progress:
                state = "ok" if row.get("ok") else "FAIL"
                print(
                    f"[{len(rows)}/{len(paths)}] {state} {row['label']}/{row['zone']} "
                    f"{row['wall_s']}s",
                    file=sys.stderr,
                    flush=True,
                )
    order = {str(p): i for i, p in enumerate(paths)}
    rows.sort(key=lambda r: order[r["path"]])
    return rows


def summary(rows: list[dict]) -> dict:
    counts: dict[str, int] = {}
    for row in rows:
        for name, n in row.get("counts", {}).items():
            counts[name] = counts.get(name, 0) + n
    return {
        "zones": len(rows),
        "passed": sum(1 for r in rows if r.get("ok")),
        "failed": [
            {
                "zone": f"{r.get('label')}/{r['zone']}",
                **(r.get("error") or {}),
                "problems": r.get("problems"),
            }
            for r in rows
            if not r.get("ok")
        ],
        "assets": sum(r.get("assets", 0) for r in rows),
        "bytes": sum(r.get("length", 0) for r in rows),
        "inflate_s": round(sum(r.get("inflate_s", 0) for r in rows), 1),
        "parse_s": round(sum(r.get("parse_s", 0) for r in rows), 1),
        "counts": dict(sorted(counts.items(), key=lambda kv: -kv[1])),
    }


DOC = ROOT / "docs" / "research" / "walk-all.md"
START = "<!-- WALK START -->"
END = "<!-- WALK END -->"


def tables(rows: list[dict], stats: dict) -> str:
    """Markdown: per-group totals, per-type counts, then one row per zone."""
    groups: dict[str, list[dict]] = {}
    for row in rows:
        groups.setdefault(row.get("label", "?"), []).append(row)
    out = [
        f"{stats['passed']} of {stats['zones']} zones pass; {stats['assets']} assets, "
        f"{stats['bytes'] / 1e6:.0f} MB of stream; wall {stats.get('wall_s', 0)} s "
        f"(inflate {stats['inflate_s']} s and parse {stats['parse_s']} s summed over workers).",
        "",
        "| Group | Zones | Pass | Assets | Stream MB | Parse s |",
        "|---|---|---|---|---|---|",
    ]
    for label, members in groups.items():
        out.append(
            f"| {label} | {len(members)} | {sum(1 for r in members if r.get('ok'))} | "
            f"{sum(r.get('assets', 0) for r in members)} | "
            f"{sum(r.get('length', 0) for r in members) / 1e6:.0f} | "
            f"{sum(r.get('parse_s', 0) for r in members):.1f} |"
        )
    out += ["", "| Type | Assets (all zones) |", "|---|---|"]
    out += [f"| {name} | {n} |" for name, n in stats["counts"].items()]
    out += [
        "",
        "| Group | Zone | Result | Stream bytes | Assets | Script strings | Tail bytes | "
        "Events | Parse s |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        result = "pass" if row.get("ok") else "FAIL"
        out.append(
            f"| {row.get('label')} | {row['zone']} | {result} | {row.get('length', '')} | "
            f"{row.get('assets', '')} | {row.get('script_strings', '')} | "
            f"{row.get('tail_bytes', '')} | {row.get('events', '')} | {row.get('parse_s', '')} |"
        )
    return "\n".join(out)


def write_doc(doc: Path, text: str) -> None:
    body = doc.read_text()
    if START not in body or END not in body:
        raise SystemExit(f"{doc}: expected the markers {START} and {END}")
    head, rest = body.split(START, 1)
    _, tail = rest.split(END, 1)
    doc.write_text(f"{head}{START}\n{text}\n{END}{tail}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--timeout", type=float, default=900, help="seconds per zone")
    parser.add_argument(
        "--match",
        action="append",
        default=[],
        help="only zones whose path contains this (repeatable)",
    )
    parser.add_argument("--json", type=Path, help="write one row per zone as JSON")
    parser.add_argument(
        "--doc",
        type=Path,
        help=f"write the result tables between the markers in this file "
        f"(normally {DOC.relative_to(ROOT)})",
    )
    parser.add_argument("--from-json", type=Path, help="reuse a --json result instead of walking")
    parser.add_argument("zones", nargs="*", type=Path, help="zones to walk instead of .env")
    args = parser.parse_args(argv)

    if args.from_json:
        saved = json.loads(args.from_json.read_text())
        if args.doc:
            write_doc(args.doc, tables(saved["rows"], saved["summary"]))
        print(json.dumps(saved["summary"], indent=1))
        return 0 if saved["summary"]["passed"] == saved["summary"]["zones"] else 1
    paths = args.zones or env.all_zones()
    if args.match:
        paths = [p for p in paths if any(m in str(p) for m in args.match)]
    if not paths:
        print("no zones found (configure .env or pass paths)", file=sys.stderr)
        return 2
    started = time.perf_counter()
    rows = run(paths, args.jobs, args.timeout)
    stats = summary(rows)
    stats["wall_s"] = round(time.perf_counter() - started, 1)
    if args.json:
        args.json.write_text(json.dumps({"summary": stats, "rows": rows}, indent=1))
    if args.doc:
        write_doc(args.doc, tables(rows, stats))
    print(json.dumps(stats, indent=1))
    return 0 if stats["passed"] == stats["zones"] else 1


if __name__ == "__main__":
    sys.exit(main())
