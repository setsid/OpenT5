"""The ``search`` and ``index`` command-line commands (docs/search-index.md).

This module only builds the subparsers and the command handlers; the main parser in
``opent5.cli`` wires them in. Each handler returns a dict (``--json`` prints it); a
matching text renderer formats it for a terminal, the same shape every other command
uses.
"""

from __future__ import annotations

import sys
from pathlib import Path

from opent5.index.builder import build
from opent5.index.query import ALL_KINDS, Index
from opent5.index.store import Store


def _zones():
    from opent5 import env

    return env.all_zones()


def _progress(as_json: bool):
    if as_json:
        return None

    def report(done: int, total: int, path: str, status: str) -> None:
        mark = "failed" if status == "failed" else "indexed"
        print(f"  [{done:>3}/{total}] {mark}: {Path(path).name}", file=sys.stderr)

    return report


def cmd_search(args) -> dict:
    as_json = getattr(args, "json", False)
    kinds = (args.kind,) if getattr(args, "kind", None) else None
    with Store.open() as store:
        summary = build(_zones(), store, rebuild=args.rebuild, progress=_progress(as_json))
        result = Index(store).search(
            args.term,
            kinds=kinds,
            regex=args.regex,
            case=args.case,
            names_only=args.names,
            type_name=args.type,
            zone=args.zone,
            limit=args.limit,
        )
    return {
        "term": result.term,
        "kinds": list(result.kinds),
        "zones_searched": result.zones_searched,
        "total": result.total,
        "shown": len(result.hits),
        "truncated": result.truncated,
        "seconds": result.seconds,
        "build": summary,
        "hits": [
            {
                "zone": h.zone,
                "type": h.type_name,
                "name": h.asset_name,
                "asset": h.asset_ref,
                "kind": h.kind,
                "where": h.where,
                "line": h.line,
                "row": h.row,
                "column": h.column,
                "snippet": h.snippet,
            }
            for h in result.hits
        ],
    }


def text_search(d: dict) -> str:
    lines = []
    for h in d["hits"]:
        where = h["where"]
        name = h["name"] or "(unnamed)"
        head = f"{h['zone']:<22} {h['type']:<12} {name}"
        if h["kind"] == "name":
            lines.append(head)
        else:
            lines.append(f"{head}  [{where}] {h['snippet']}")
    kinds = ", ".join(d["kinds"])
    shown = d["shown"]
    summary = (
        f"{d['total']} hit(s) across {d['zones_searched']} zone(s) "
        f"for {d['term']!r} ({kinds}) in {d['seconds']}s"
    )
    if d["truncated"]:
        summary += f"; showing the top {shown}"
    b = d["build"]
    summary += (
        f"\nindex: {b['reused']} reused, {b['indexed']} indexed, "
        f"{b['failed']} failed of {b['zones']} zone(s)"
    )
    lines.append(summary)
    return "\n".join(lines)


def cmd_index(args) -> dict:
    sub = getattr(args, "index_command", None)
    if sub == "build":
        as_json = getattr(args, "json", False)
        with Store.open() as store:
            summary = build(_zones(), store, rebuild=args.rebuild, progress=_progress(as_json))
            summary["stats"] = store.stats()
            summary["errors"] = [{"zone": Path(p).name, "error": e} for p, e in store.errors()]
        summary["action"] = "build"
        return summary
    # status (the default)
    with Store.open() as store:
        stats = store.stats()
        errors = [{"zone": Path(p).name, "error": e} for p, e in store.errors()]
    stats["action"] = "status"
    stats["errors"] = errors
    return stats


def text_index(d: dict) -> str:
    if d.get("action") == "build":
        s = d["stats"]
        lines = [
            f"indexed {d['zones']} zone(s): {d['reused']} reused, {d['indexed']} parsed, "
            f"{d['failed']} failed, {d['pruned']} pruned ({d['jobs']} job(s))",
            f"  cache    {s['cache_path']} ({s['cache_bytes']} bytes)",
            f"  assets   {s['assets']} ({s['inline_assets']} inline), "
            f"{s['text_blocks']} text block(s), {s['searchable_text_bytes']} searchable bytes",
        ]
        for e in d["errors"][:20]:
            lines.append(f"  failed   {e['zone']}: {e['error']}")
        return "\n".join(lines)
    lines = [
        f"index: {d['zones']} zone(s), {d['assets']} assets ({d['inline_assets']} inline), "
        f"{d['text_blocks']} text block(s)",
        f"  cache    {d['cache_path']} ({d['cache_bytes']} bytes)",
        f"  text     {d['searchable_text_bytes']} searchable bytes",
        f"  failed   {d['failed_zones']} zone(s)",
    ]
    for e in d["errors"][:20]:
        lines.append(f"  failed   {e['zone']}: {e['error']}")
    return "\n".join(lines)


#: Merged into opent5.cli.COMMANDS by the lead: command name -> (handler, renderer).
COMMANDS = {
    "search": (cmd_search, text_search),
    "index": (cmd_index, text_index),
}


def register(sub, parents=()) -> None:
    """Add the ``search`` and ``index`` subparsers to ``sub`` (an argparse subparsers
    object). ``parents`` carries the shared options (``--json``), exactly as the other
    opent5 subcommands get them."""
    parents = list(parents)

    p = sub.add_parser(
        "search", parents=parents, help="search across every configured zone at once"
    )
    p.add_argument("term", help="the text, name or pattern to look for")
    p.add_argument("--names", action="store_true", help="match asset names only (the fast path)")
    p.add_argument("--type", help="only this asset type (rawfile, image, material, ...)")
    p.add_argument(
        "--kind",
        choices=ALL_KINDS,
        help="only this match kind: name, rawfile, cell, localize or entities",
    )
    p.add_argument("--regex", action="store_true", help="treat the term as a regular expression")
    p.add_argument("--case", action="store_true", help="match case-sensitively")
    p.add_argument("--zone", metavar="GLOB", help="only zones whose name matches this glob")
    p.add_argument("--limit", type=int, default=200, help="most hits to show (default 200)")
    p.add_argument("--rebuild", action="store_true", help="re-index every zone before searching")

    p = sub.add_parser("index", parents=parents, help="build or inspect the on-disk search index")
    isub = p.add_subparsers(dest="index_command", metavar="SUBCOMMAND")
    b = isub.add_parser("build", parents=parents, help="build or update the index")
    b.add_argument("--rebuild", action="store_true", help="re-index every zone, ignoring the cache")
    isub.add_parser("status", parents=parents, help="what the index holds")
