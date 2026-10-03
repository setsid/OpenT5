"""Parse every zone, write it back from the parsed data, and compare.

    .venv/bin/python tools/rewrite_all.py                        # every zone in .env
    .venv/bin/python tools/rewrite_all.py --match mp_nuked
    .venv/bin/python tools/rewrite_all.py --json rewrite.json --doc docs/research/rewrite-all.md
    .venv/bin/python tools/rewrite_all.py --recompress --match mp_nuked   # deflate afresh too

For each zone, four checks, all byte for byte:

1. per asset: ``write_asset`` of every parsed asset equals its file span;
2. whole content: ``write`` of the parsed zone equals the inflated content, with
   the XFile header derived from the written stream (size and block sizes);
3. event log: the writer's log equals the parser's (same reads, blocks,
   alignments, pointers, in the same order);
4. container: the rebuilt content put back through ``Zone.save`` (here
   ``Zone.build``) gives the original .ff; with --recompress every chunk is
   deflated afresh instead of carried.

Each zone runs in its own process with a timeout (shared with walk_all.py).
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
from opent5.xfile import parse, write, write_asset  # noqa: E402
from walk_all import label_of, run, write_doc  # noqa: E402

DOC = ROOT / "docs" / "research" / "rewrite-all.md"
START = "<!-- REWRITE START -->"
END = "<!-- REWRITE END -->"
RECOMPRESS = False


def first_difference(one: bytes, other: bytes) -> int:
    for i, (a, b) in enumerate(zip(one, other, strict=False)):
        if a != b:
            return i
    return min(len(one), len(other))


def rewrite_one(path: str) -> dict:
    row: dict = {"path": path, "zone": Path(path).stem, "label": label_of(Path(path))}
    try:
        source = Path(path).read_bytes()
        zone = Zone.open(source)
        content = bytes(zone.content)
        row["length"] = len(content)
        started = time.perf_counter()
        xfile = parse(content)
        row["parse_s"] = round(time.perf_counter() - started, 2)
        row["assets"] = len(xfile.assets)

        started = time.perf_counter()
        bad = []
        for asset in xfile.assets:
            got = write_asset(asset)
            want = content[asset.file_start : asset.file_end]
            if got != want:
                bad.append(
                    {
                        "index": asset.index,
                        "type": asset.type_name,
                        "name": asset.name,
                        "file_start": asset.file_start,
                        "expected_bytes": len(want),
                        "found_bytes": len(got),
                        "first_difference": first_difference(got, want),
                    }
                )
        row["asset_s"] = round(time.perf_counter() - started, 2)
        row["assets_identical"] = len(xfile.assets) - len(bad)
        row["asset_failures"] = bad[:20]

        started = time.perf_counter()
        written = write(xfile)
        row["write_s"] = round(time.perf_counter() - started, 2)
        row["content_identical"] = written.content == content
        if not row["content_identical"]:
            row["content_first_difference"] = first_difference(written.content, content)
            row["content_lengths"] = [len(written.content), len(content)]
        row["header_identical"] = written.header == xfile.header
        row["log_identical"] = bool(
            len(written.log) == len(xfile.log) and (written.log.table() == xfile.log.table()).all()
        )
        del xfile

        started = time.perf_counter()
        zone.content[:] = written.content
        built = zone.build()
        row["ff_identical"] = built.identical and built.data == source
        row["ff_chunks_kept"] = built.kept
        if RECOMPRESS:
            fresh = zone.build(recompress=True)
            row["ff_recompressed_identical"] = fresh.data == source
        row["ff_s"] = round(time.perf_counter() - started, 2)
        row["ok"] = (
            not bad
            and row["content_identical"]
            and row["header_identical"]
            and row["log_identical"]
            and row["ff_identical"]
            and row.get("ff_recompressed_identical", True)
        )
    except Exception as exc:  # report, never stop the run
        row["ok"] = False
        row["error"] = {
            "kind": type(exc).__name__,
            "message": str(exc),
            "traceback": traceback.format_exc(limit=6),
        }
    return row


def summary(rows: list[dict]) -> dict:
    return {
        "zones": len(rows),
        "passed": sum(1 for r in rows if r.get("ok")),
        "assets": sum(r.get("assets", 0) for r in rows),
        "assets_identical": sum(r.get("assets_identical", 0) for r in rows),
        "content_identical": sum(1 for r in rows if r.get("content_identical")),
        "log_identical": sum(1 for r in rows if r.get("log_identical")),
        "ff_identical": sum(1 for r in rows if r.get("ff_identical")),
        "bytes": sum(r.get("length", 0) for r in rows),
        "parse_s": round(sum(r.get("parse_s", 0) for r in rows), 1),
        "write_s": round(sum(r.get("write_s", 0) + r.get("asset_s", 0) for r in rows), 1),
        "failed": [
            {
                "zone": f"{r.get('label')}/{r['zone']}",
                "error": r.get("error"),
                "asset_failures": r.get("asset_failures"),
                "content_first_difference": r.get("content_first_difference"),
            }
            for r in rows
            if not r.get("ok")
        ],
    }


def tables(rows: list[dict], stats: dict) -> str:
    def mark(value) -> str:
        return "yes" if value else ("-" if value is None else "NO")

    out = [
        f"{stats['passed']} of {stats['zones']} zones pass every check; "
        f"{stats['assets_identical']} of {stats['assets']} assets write back identically; "
        f"{stats['content_identical']} contents, {stats['log_identical']} event logs and "
        f"{stats['ff_identical']} .ff files identical; {stats['bytes'] / 1e6:.0f} MB of "
        f"stream; wall {stats.get('wall_s', 0)} s (parse {stats['parse_s']} s and write "
        f"{stats['write_s']} s summed over workers).",
        "",
        "| Group | Zone | Assets | Assets identical | Content | Header | Event log | .ff | "
        "Parse s | Write s |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        out.append(
            f"| {row.get('label')} | {row['zone']} | {row.get('assets', '')} | "
            f"{row.get('assets_identical', '')} | {mark(row.get('content_identical'))} | "
            f"{mark(row.get('header_identical'))} | {mark(row.get('log_identical'))} | "
            f"{mark(row.get('ff_identical'))} | {row.get('parse_s', '')} | "
            f"{row.get('write_s', '')} |"
        )
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    global RECOMPRESS
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--timeout", type=float, default=1800, help="seconds per zone")
    parser.add_argument("--match", action="append", default=[], help="path substring filter")
    parser.add_argument("--recompress", action="store_true", help="also deflate every chunk")
    parser.add_argument("--json", type=Path, help="write one row per zone as JSON")
    parser.add_argument("--doc", type=Path, help=f"refresh the tables in {DOC.relative_to(ROOT)}")
    parser.add_argument("--from-json", type=Path, help="reuse a --json result")
    parser.add_argument("zones", nargs="*", type=Path, help="zones instead of .env")
    args = parser.parse_args(argv)
    RECOMPRESS = args.recompress

    if args.from_json:
        saved = json.loads(args.from_json.read_text())
        rows, stats = saved["rows"], saved["summary"]
    else:
        paths = args.zones or env.all_zones()
        if args.match:
            paths = [p for p in paths if any(m in str(p) for m in args.match)]
        if not paths:
            print("no zones found (configure .env or pass paths)", file=sys.stderr)
            return 2
        started = time.perf_counter()
        rows = run(paths, args.jobs, args.timeout, work=rewrite_one)
        stats = summary(rows)
        stats["wall_s"] = round(time.perf_counter() - started, 1)
        if args.json:
            args.json.write_text(json.dumps({"summary": stats, "rows": rows}, indent=1))
    if args.doc:
        write_doc(args.doc, tables(rows, stats), START, END)
    print(json.dumps(stats, indent=1))
    return 0 if stats["passed"] == stats["zones"] else 1


if __name__ == "__main__":
    sys.exit(main())
