"""Read and rewrite every .pak on the machine and check the result is byte-identical.

    .venv/bin/python tools/pak_all.py                          # every pak under the .env folders
    .venv/bin/python tools/pak_all.py --json pak_all.json --match mp_nuked

Folders searched (recursively): the parent of OPENT5_ZONES (the disc USRDIR, so both
language folders), the parent of OPENT5_PATCH_ZONES, OPENT5_DLC_ZONES and OPENT5_WADS.
Only files that start with 'pak2' count. For each pak: header fields, whether the start
table is ascending, whether the header size is round_up(0x1c + 4 * count, 0x800), then
the sha1 of the file and the sha1 of ``Pak.open(path).chunks()`` (the writer laying out
every entry again from the parsed table): equal means a byte-identical round trip.
Nothing is written to disk. Prints a markdown table (docs/research/pak.md section 5).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from opent5 import env  # noqa: E402
from opent5.container.pak import MAGIC, Pak, PakError, header_bytes, sha1_of  # noqa: E402


def folders() -> list[tuple[str, Path]]:
    out = []
    for label, key, parent in (
        ("disc", "OPENT5_ZONES", True),
        ("update", "OPENT5_PATCH_ZONES", True),
        ("hdd", "OPENT5_DLC_ZONES", False),
        ("wads", "OPENT5_WADS", False),
    ):
        p = env.path_of(key)
        if p is None:
            continue
        p = p.parent if parent else p
        if p.is_dir():
            out.append((label, p))
    return out


def find_paks() -> list[tuple[str, Path]]:
    found = []
    for label, root in folders():
        for p in sorted(root.rglob("*.pak")):
            try:
                with open(p, "rb") as f:
                    if f.read(4) != MAGIC:
                        continue
            except OSError:
                continue
            found.append((label, p))
    return found


def check(label: str, root_rel: str, path: Path) -> dict:
    t = time.time()
    row = {"where": label, "file": root_rel, "bytes": path.stat().st_size}
    try:
        pak = Pak.open(path)
    except PakError as exc:
        row["error"] = str(exc)
        return row
    with pak:
        row.update(
            built=datetime.fromtimestamp(pak.timestamp, timezone.utc).strftime("%Y-%m-%d %H:%M"),
            field08=pak.field08,
            count=pak.count,
            sector=pak.sector,
            header_size=pak.header_size,
            header_rule=pak.header_size == header_bytes(pak.count, pak.sector),
            ascending=all(a < b for a, b in zip(pak.starts, pak.starts[1:], strict=False)),
            aliases=len(pak.alias),
            lead=pak.lead,
            tail_zero=not any(pak.header_tail),
        )
        sha = hashlib.sha1()
        n = 0
        for piece in pak.chunks():
            sha.update(piece)
            n += len(piece)
    row["sha1"] = sha1_of(path)
    row["rebuilt_sha1"] = sha.hexdigest()
    row["rebuilt_bytes"] = n
    row["identical"] = row["sha1"] == row["rebuilt_sha1"] and n == row["bytes"]
    row["seconds"] = round(time.time() - t, 1)
    return row


def table(rows: list[dict]) -> str:
    lines = [
        "| Where | File | Bytes | Built (UTC) | 0x08 | Entries | Header | Table order | "
        "Round trip | sha1 |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        if "error" in r:
            lines.append(
                f"| {r['where']} | {r['file']} | {r['bytes']} | | | | | | ERROR: {r['error']} | |"
            )
            continue
        lines.append(
            f"| {r['where']} | {r['file']} | {r['bytes']:,} | {r['built']} | {r['field08']:#x} | "
            f"{r['count']} | {r['header_size']:#x} | "
            f"{'ascending' if r['ascending'] else 'not ascending'} | "
            f"{'identical' if r['identical'] else 'DIFFERS'} | `{r['sha1'][:12]}` |"
        )
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", help="write one row per pak to this file")
    ap.add_argument("--match", action="append", help="only paks whose path contains this")
    args = ap.parse_args()
    paks = find_paks()
    if args.match:
        paks = [(lab, p) for lab, p in paks if any(m in str(p) for m in args.match)]
    roots = dict(folders())
    rows = []
    for label, p in paks:
        rel = str(p.relative_to(roots[label]))
        row = check(label, rel, p)
        rows.append(row)
        status = "ERROR" if "error" in row else ("identical" if row["identical"] else "DIFFERS")
        print(f"{label:6} {rel:40} {status} {row.get('seconds', '')}s", file=sys.stderr, flush=True)
        if args.json:
            Path(args.json).write_text(json.dumps(rows, indent=1))
    print(table(rows))
    bad = [r for r in rows if not r.get("identical")]
    print(
        f"\n{len(rows)} pak(s), {len(rows) - len(bad)} byte-identical, {len(bad)} not",
        file=sys.stderr,
    )
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
