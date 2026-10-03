"""The command line. Every command is headless; --json gives machine output.

    opent5 info ZONE                       what the zone holds
    opent5 list ZONE [--type T] [--match GLOB] [--inline]
    opent5 extract ZONE OUTDIR [--type T] [--name GLOB]
    opent5 replace ZONE ASSET FILE [ASSET FILE ...] -o OUT
    opent5 unpack ZONE DIR / opent5 pack DIR -o OUT
    opent5 verify ZONE [--against SOURCE]
    opent5 rebuild ZONE -o OUT

Exit codes: 0 done, 1 failed (the message says why), 2 usage. Nothing is ever written into
the game folders named in .env, or over the zone being read. docs/cli.md has every command
with examples and its JSON shape.
"""

from __future__ import annotations

import argparse
import csv
import fnmatch
import hashlib
import io
import json
import sys
import time
from pathlib import Path
from typing import Any

from opent5 import APP_NAME, LICENCE_NOTICE, __version__

EXIT_OK, EXIT_FAIL, EXIT_USAGE = 0, 1, 2


class UsageError(Exception):
    pass


class Failure(Exception):
    def __init__(self, message: str, data: dict | None = None):
        super().__init__(message)
        self.data = data or {}


class _Version(argparse.Action):
    """--version printed as written: argparse's version action reflows the text and
    loses the licence notice's line breaks."""

    def __init__(self, option_strings, dest=argparse.SUPPRESS, default=argparse.SUPPRESS, **kw):
        super().__init__(option_strings, dest=dest, default=default, nargs=0, **kw)

    def __call__(self, parser, namespace, values, option_string=None):
        sys.stdout.write(f"{APP_NAME} {__version__}\n{LICENCE_NOTICE}\n")
        parser.exit(0)


class _Parser(argparse.ArgumentParser):
    def error(self, message):  # usage errors exit 2, also under --json
        self.print_usage(sys.stderr)
        self.exit(EXIT_USAGE, f"{self.prog}: error: {message}\n")


def sha1_of(path: Path) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def out_file(path: str | Path, *sources: Path) -> Path:
    """An output file path: never a source, never inside a game folder."""
    from opent5.edit import EditError, check_target

    target = Path(path)
    for source in sources:
        try:
            check_target(target, source)
        except EditError as exc:
            raise Failure(str(exc)) from None
    if not sources:
        try:
            check_target(target, None)
        except EditError as exc:
            raise Failure(str(exc)) from None
    return target


def out_dir(path: str | Path) -> Path:
    from opent5.edit import game_folders

    target = Path(path).expanduser().resolve()
    for folder in game_folders():
        try:
            target.relative_to(folder.resolve())
        except ValueError:
            continue
        raise Failure(
            f"{path}: expected a folder outside the game folders, found one inside {folder}"
        )
    return Path(path)


def zone_by_name(value: str) -> str:
    """A bare zone name such as ``mp_nuked`` found in the .env folders; anything that
    exists, or looks like a path, is returned unchanged."""
    if Path(value).exists() or "/" in value or "\\" in value or value.endswith(".ff"):
        return value
    from opent5 import env

    matches = [p for p in env.all_zones() if p.stem == value]
    return str(matches[0]) if matches else value


def open_doc(path: str):
    from opent5.edit import Document, EditError

    try:
        return Document.open(path)
    except EditError as exc:
        raise Failure(str(exc)) from None


def ref_json(r) -> dict:
    return {
        "index": list(r.index) if r.inline else r.index,
        "type": r.type_name,
        "name": r.name,
        "size": r.size,
        "offset": r.file_start,
        "editable": list(r.editable),
        **({"parent": r.parent} if r.inline else {}),
    }


def resolve_asset(doc, spec: str):
    """ASSET on the command line: an index, a name, or type:name."""
    from opent5.edit import EditError

    if spec.isdigit():
        try:
            return doc.asset(int(spec))
        except EditError as exc:
            raise Failure(str(exc)) from None
    type_part, sep, name = spec.partition(":")
    from opent5.xfile.constants import ASSET_TYPE_NAMES

    typed = sep and type_part in ASSET_TYPE_NAMES
    found = doc.find(name, type_part) if typed else doc.find(spec)
    if found is None:
        raise Failure(f"asset {spec!r}: expected an index, a name or type:name, found no match")
    return found


# -- commands --------------------------------------------------------------------------------


def cmd_info(args) -> dict:
    path = Path(args.zone)
    started = time.perf_counter()
    doc = open_doc(args.zone)
    header = doc.xfile.header
    return {
        "zone": doc.zone_name,
        "path": str(path),
        "bytes": path.stat().st_size,
        "sha1": sha1_of(path),
        "signed": doc.signed,
        "content_bytes": len(doc.content),
        "chunks": len(doc._zone.chunk_sizes),
        "block_sizes": list(header.block_sizes),
        "assets": len(doc.assets),
        "inline_assets": len(doc.inline_assets),
        "type_counts": doc.type_counts(),
        "parse_exact": not doc.parse_problems,
        "parse_problems": doc.parse_problems,
        "seconds": round(time.perf_counter() - started, 2),
    }


def text_info(d: dict) -> str:
    lines = [
        f"{d['zone']}  ({d['path']})",
        f"  file      {d['bytes']} bytes, sha1 {d['sha1']}",
        f"  signed    {'yes (console signature at 0x3c)' if d['signed'] else 'no'}",
        f"  content   {d['content_bytes']} bytes in {d['chunks']} chunks",
        f"  blocks    {' '.join(hex(b) for b in d['block_sizes'])}",
        f"  assets    {d['assets']} in the asset list, {d['inline_assets']} loaded inline",
        f"  parse     {'exact' if d['parse_exact'] else 'NOT exact: ' + d['parse_problems'][0]}",
        "  types",
    ]
    lines += [f"    {n:>6}  {t}" for t, n in sorted(d["type_counts"].items(), key=lambda x: -x[1])]
    return "\n".join(lines)


def cmd_list(args) -> dict:
    doc = open_doc(args.zone)
    refs = list(doc.assets) + (list(doc.inline_assets) if args.inline else [])
    if args.type:
        refs = [r for r in refs if r.type_name == args.type]
    if args.match:
        refs = [r for r in refs if r.name and fnmatch.fnmatchcase(r.name, args.match)]
    return {"zone": doc.zone_name, "count": len(refs), "assets": [ref_json(r) for r in refs]}


def text_list(d: dict) -> str:
    lines = []
    for a in d["assets"]:
        index = a["index"] if not isinstance(a["index"], list) else "inline"
        lines.append(f"{index!s:>6}  {a['type']:<15} {a['size']:>10}  {a['name']}")
    lines.append(f"{d['count']} asset(s)")
    return "\n".join(lines)


def _safe_rel(name: str) -> Path:
    parts = [p for p in name.replace("\\", "/").split("/") if p not in ("", ".", "..")]
    return Path(*parts) if parts else Path("unnamed")


def cmd_extract(args) -> dict:
    out = out_dir(args.outdir)
    doc = open_doc(args.zone)
    if not args.type and not args.name:
        from opent5.export.zone import ZoneExporter

        manifest = ZoneExporter(
            doc.content,
            doc.zone_name,
            out,
            doc.pak_dirs(),
            previews=args.previews,
            log=(lambda *_: None) if args.json else (lambda m: print(m, file=sys.stderr)),
        ).run()
        return {
            "zone": doc.zone_name,
            "outdir": str(out),
            "mode": "full export",
            "files": manifest["counts"]["files"],
            "failures": manifest["counts"]["failures"],
            "manifest": str(out / "manifest.json"),
        }
    refs = list(doc.assets) + list(doc.inline_assets)
    if args.type:
        refs = [r for r in refs if r.type_name == args.type]
    if args.name:
        refs = [r for r in refs if r.name and fnmatch.fnmatchcase(r.name, args.name)]
    written, failed = [], []
    for r in refs:
        try:
            written += _extract_one(doc, r, out)
        except Exception as exc:  # noqa: BLE001 - report and carry on with the rest
            failed.append({"name": r.name, "type": r.type_name, "reason": str(exc)})
    return {
        "zone": doc.zone_name,
        "outdir": str(out),
        "mode": "selected",
        "assets": len(refs),
        "files": written,
        "failures": failed,
    }


def _extract_one(doc, r, out: Path) -> list[str]:
    from opent5.formats import texture as tx
    from opent5.xfile.constants import AssetType as T

    rel = _safe_rel(r.name or f"asset_{r.index}")
    if r.type == T.RAWFILE:
        path = out / "rawfiles" / rel
        data = doc.text(r.index).encode("latin-1")
    elif r.type == T.STRINGTABLE:
        path = out / "stringtables" / rel.with_suffix(".csv")
        buf = io.StringIO()
        csv.writer(buf, lineterminator="\n").writerows(doc.table(r.index))
        data = buf.getvalue().encode("latin-1")
    elif r.type == T.LOCALIZE:
        path = out / "localize" / (rel.name + ".txt")
        data = doc.localize(r.index)[1].encode("latin-1")
    elif r.type == T.IMAGE:
        img = doc.image(r.index)
        if img.rgba is None:
            raise ValueError(img.reason or "no pixels")
        path = out / "images" / (rel.name + ".png")
        data = tx.write_png(img.rgba)
    elif "text" in r.editable and r.type in (T.MAP_ENTS, T.COL_MAP_MP, T.COL_MAP_SP):
        path = out / "map_ents" / (rel.name + ".ents")
        data = doc.text(r.index).encode("latin-1")
    else:
        path = out / r.type_name / (rel.name + ".json")
        data = (json.dumps(doc.fields(r.index), indent=1, default=str) + "\n").encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return [str(path)]


def _apply_replacement(doc, ref, file: Path, share: str = "split") -> str:
    from opent5.xfile.constants import AssetType as T

    data = file.read_bytes()
    if ref.type == T.IMAGE:
        from opent5.formats import texture as tx

        if data[:4] == b"DDS ":
            doc.replace_image(ref.index, data)
            return "image from DDS"
        doc.replace_image(ref.index, tx.read_png(data))
        return "image from PNG"
    if ref.type == T.STRINGTABLE:
        rows = list(csv.reader(io.StringIO(data.decode("latin-1"), newline="")))
        current = doc.table(ref.index)
        columns = len(current[0]) if current else (len(rows[0]) if rows else 0)
        for n, row in enumerate(rows):
            if len(row) != columns:
                raise Failure(
                    f"{file}: expected {columns} columns in every row, found {len(row)} in row {n}"
                )
        while len(doc.table(ref.index)) > len(rows):
            doc.remove_row(ref.index, len(doc.table(ref.index)) - 1)
        for n, row in enumerate(rows):
            have = doc.table(ref.index)
            if n >= len(have):
                doc.add_row(ref.index, row)
                continue
            for c, cell in enumerate(row):
                if have[n][c] != cell:
                    doc.set_cell(ref.index, n, c, cell, share=share)
        return f"table, {len(rows)} rows"
    text = data.decode("latin-1")
    if ref.type == T.LOCALIZE:
        if text.endswith("\n"):
            text = text[:-1]
        doc.set_localize(ref.index, text, share=share)
        return "localize value"
    doc.set_text(ref.index, text)
    return "text"


def cmd_replace(args) -> dict:
    from opent5.edit import EditError

    if len(args.pairs) % 2:
        raise UsageError("replace takes ASSET FILE pairs")
    zone = Path(args.zone)
    target = out_file(args.output, zone)
    doc = open_doc(args.zone)
    done = []
    try:
        for spec, file in zip(args.pairs[0::2], args.pairs[1::2], strict=True):
            path = Path(file)
            if not path.is_file():
                raise Failure(f"{file}: expected a file to read, found none")
            ref = resolve_asset(doc, spec)
            how = _apply_replacement(doc, ref, path, args.share)
            done.append({"asset": ref_json(ref), "file": str(path), "as": how})
        report = doc.save(target, verify=not args.no_verify)
    except EditError as exc:
        raise Failure(str(exc)) from None
    data = {
        "zone": doc.zone_name,
        "output": str(report.path),
        "bytes": report.bytes,
        "sha1": report.sha1,
        "source_sha1": hashlib.sha1(doc._source).hexdigest(),
        "replaced": done,
        "changes": [
            {
                "index": c.index if isinstance(c.index, int) else list(c.index),
                "kind": c.kind,
                "detail": c.detail,
            }  # fmt: skip
            for c in doc.changes()
        ],
        "verified": report.verified,
        "problems": report.problems,
        "verification": report.details,
        "signature_note": report.signature_note,
    }
    if report.problems:
        raise Failure("verification found problems: " + "; ".join(report.problems[:3]), data)
    return data


def text_replace(d: dict) -> str:
    lines = [f"{d['zone']} -> {d['output']} ({d['bytes']} bytes, sha1 {d['sha1']})"]
    for r in d["replaced"]:
        lines.append(
            f"  replaced {r['asset']['type']} {r['asset']['name']} from {r['file']} ({r['as']})"
        )
    for c in d["changes"]:
        lines.append(f"  change: asset {c['index']} {c['kind']} {c['detail']}".rstrip())
    v = d["verification"]
    if v:
        lines.append(
            f"  verified: {v['assets_checked']} assets; {v['identical']} identical, "
            f"{v['identical_after_pointer_remap']} identical apart from {v['pointers_checked']} "
            f"remapped pointers, {v['edited']} edited and read back"
        )
    if d["signature_note"]:
        lines.append(f"  note: {d['signature_note']}")
    return "\n".join(lines)


def cmd_unpack(args) -> dict:
    from opent5.container.fastfile import FastFileError, unpack

    directory = out_dir(args.directory)
    sink = io.StringIO()
    try:
        ff = unpack(Path(args.zone), directory, out=sink)
    except FastFileError as exc:
        raise Failure(f"{args.zone}: {exc}") from None
    return {
        "zone": ff.zone_name,
        "directory": str(directory),
        "chunks": len(ff.chunks),
        "content_bytes": len(ff.content),
        "log": sink.getvalue().splitlines(),
    }


def cmd_pack(args) -> dict:
    from opent5.container.fastfile import FastFileError, pack

    target = out_file(args.output)
    sink = io.StringIO()
    try:
        data = pack(Path(args.directory), target, out=sink, derive_size=not args.keep_size)
    except (FastFileError, OSError) as exc:
        raise Failure(f"{args.directory}: {exc}") from None
    return {
        "directory": args.directory,
        "output": str(target),
        "bytes": len(data),
        "sha1": hashlib.sha1(data).hexdigest(),
        "log": sink.getvalue().splitlines(),
    }


def cmd_verify(args) -> dict:
    from opent5.container.fastfile import FastFileError
    from opent5.container.zone import verify as verify_container

    path = Path(args.zone)
    data: dict[str, Any] = {"zone_path": str(path), "sha1": sha1_of(path), "checks": {}}
    problems: list[str] = []
    try:
        v = verify_container(path)
        data["checks"]["container"] = {
            "chunks": v.chunks,
            "content_bytes": v.content_bytes,
            "terminators": v.terminators,
            "padded_as_original_writer": v.padded_as_original_writer,
        }
    except FastFileError as exc:
        problems.append(f"container: {exc}")
        data.update(ok=False, problems=problems)
        raise Failure(problems[0], data) from None
    doc = open_doc(args.zone)
    data["zone"] = doc.zone_name
    data["signed"] = doc.signed
    data["checks"]["parse_exact"] = not doc.parse_problems
    problems += [f"parse: {p}" for p in doc.parse_problems]
    if not doc.parse_problems:
        from opent5.xfile import write

        rewritten = write(doc.xfile, log=False).content
        same = rewritten == doc.content
        data["checks"]["rewrites_identically"] = same
        if not same:
            problems.append("the parsed zone does not write back to the same content")
    if args.against:
        data["against"] = _against(doc, Path(args.against))
    data.update(ok=not problems, problems=problems)
    if problems:
        raise Failure(problems[0], data)
    return data


def _against(doc, source: Path) -> dict:
    from opent5.edit.verify import _Side, compare_assets

    base = open_doc(str(source))
    out: dict[str, Any] = {"source": str(source), "source_sha1": sha1_of(source)}
    if len(base.xfile.assets) != len(doc.xfile.assets):
        out["asset_count"] = [len(base.xfile.assets), len(doc.xfile.assets)]
        out["comparable"] = False
        return out
    result = compare_assets(_Side(base.xfile, base.content), _Side(doc.xfile, doc.content), None)
    differs = [
        {
            "index": i,
            "type": base.xfile.assets[i].type_name,
            "name": base.xfile.assets[i].name,
            "reason": r,
        }  # fmt: skip
        for i, s, r in result.rows
        if s == "differs"
    ]
    out.update(
        comparable=True,
        identical=result.identical,
        identical_after_pointer_remap=result.remapped,
        pointers_checked=result.pointers,
        changed=differs,
    )
    return out


def text_verify(d: dict) -> str:
    c = d["checks"]
    lines = [
        f"{d.get('zone', d['zone_path'])}: {'OK' if d['ok'] else 'PROBLEMS'} (sha1 {d['sha1']})"
    ]
    if "container" in c:
        k = c["container"]
        lines.append(
            f"  container: {k['chunks']} chunks, {k['content_bytes']} content bytes, "
            f"{k['terminators']} terminators"
        )
    if "parse_exact" in c:
        lines.append(f"  parse: {'exact' if c['parse_exact'] else 'not exact'}")
    if "rewrites_identically" in c:
        lines.append(
            f"  rewrite from the parse: {'identical' if c['rewrites_identically'] else 'differs'}"
        )
    a = d.get("against")
    if a:
        if not a.get("comparable"):
            lines.append(f"  against {a['source']}: asset counts differ {a.get('asset_count')}")
        else:
            lines.append(
                f"  against {a['source']}: {a['identical']} identical, "
                f"{a['identical_after_pointer_remap']} identical apart from remapped pointers, "
                f"{len(a['changed'])} changed"
            )
            for x in a["changed"][:40]:
                lines.append(f"    changed: {x['index']} {x['type']} {x['name']}: {x['reason']}")
    for p in d["problems"]:
        lines.append(f"  problem: {p}")
    return "\n".join(lines)


def cmd_rebuild(args) -> dict:
    from opent5.container.zone import Zone
    from opent5.xfile import XFileError, parse, write

    zone_path = Path(args.zone)
    target = out_file(args.output, zone_path)
    started = time.perf_counter()
    zone = Zone.open(zone_path)
    original = bytes(zone.content)
    try:
        xfile = parse(original, log=False)
    except XFileError as exc:
        raise Failure(f"{zone_path}: does not parse: {exc}") from None
    problems = xfile.problems()
    if problems:
        raise Failure(f"{zone_path}: the parse is not exact: {problems[0]}")
    content = write(xfile, log=False).content
    zone.content[:] = content
    built = zone.build(recompress=args.recompress)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(built.data)
    data = {
        "zone": zone.name,
        "source": str(zone_path),
        "source_sha1": sha1_of(zone_path),
        "output": str(target),
        "sha1": hashlib.sha1(built.data).hexdigest(),
        "bytes": len(built.data),
        "assets": len(xfile.assets),
        "content_identical": content == original,
        "file_identical": built.identical,
        "chunks_carried": built.kept,
        "chunks_deflated": built.deflated,
        "seconds": round(time.perf_counter() - started, 2),
    }
    if not built.identical:
        raise Failure("the rebuilt file differs from the source", data)
    return data


def text_rebuild(d: dict) -> str:
    return "\n".join(
        [
            f"{d['zone']}: {d['assets']} assets parsed and written back",
            f"  content  {'identical' if d['content_identical'] else 'DIFFERS'}",
            f"  file     {'byte-identical' if d['file_identical'] else 'DIFFERS'} "
            f"({d['chunks_carried']} chunks carried, {d['chunks_deflated']} deflated afresh)",
            f"  source   {d['source_sha1']}  {d['source']}",
            f"  output   {d['sha1']}  {d['output']}",
        ]
    )


def text_generic(d: dict) -> str:
    lines = []
    for k, v in d.items():
        if isinstance(v, list) and len(v) > 12:
            lines.append(f"{k}: {len(v)} item(s)")
        elif k != "log":
            lines.append(f"{k}: {v}")
    return "\n".join(lines)


COMMANDS = {
    "info": (cmd_info, text_info),
    "list": (cmd_list, text_list),
    "extract": (cmd_extract, text_generic),
    "replace": (cmd_replace, text_replace),
    "unpack": (cmd_unpack, text_generic),
    "pack": (cmd_pack, text_generic),
    "verify": (cmd_verify, text_verify),
    "rebuild": (cmd_rebuild, text_rebuild),
}


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", help="machine-readable output on stdout")
    parser = _Parser(
        prog="opent5",
        description=f"{APP_NAME}: open, browse, edit and rebuild Black Ops (T5) PS3 fastfiles",
        epilog="Exit codes: 0 done, 1 failed, 2 usage. See docs/cli.md.",
    )
    parser.add_argument("--version", action=_Version, help="show the version and licence")
    sub = parser.add_subparsers(dest="command", metavar="COMMAND", parser_class=_Parser)

    p = sub.add_parser("info", parents=[common], help="what a zone holds")
    p.add_argument("zone")

    p = sub.add_parser("list", parents=[common], help="list the assets")
    p.add_argument("zone")
    p.add_argument("--type", help="only this asset type (rawfile, stringtable, image, ...)")
    p.add_argument("--match", help="only names matching this glob (case-sensitive)")
    p.add_argument("--inline", action="store_true", help="include assets loaded inline")

    p = sub.add_parser("extract", parents=[common], help="write assets out in open formats")
    p.add_argument("zone")
    p.add_argument("outdir")
    p.add_argument("--type", help="only this asset type")
    p.add_argument("--name", help="only names matching this glob")
    p.add_argument("--previews", action="store_true", help="full export: also render previews")

    p = sub.add_parser("replace", parents=[common], help="replace assets and save a new zone")
    p.add_argument("zone")
    p.add_argument(
        "pairs",
        nargs="+",
        metavar="ASSET FILE",
        help="asset (index, name or type:name) and the file with its new content",
    )
    p.add_argument("-o", "--output", required=True, help="the new .ff (never the source)")
    p.add_argument("--no-verify", action="store_true", help="skip verification after saving")
    p.add_argument(
        "--share",
        choices=("split", "all"),
        default="split",
        help="localize values and stringtable cells whose string other fields share: split "
        "(default, only the named asset changes) or all (every field sharing it changes)",
    )

    p = sub.add_parser("unpack", parents=[common], help="decrypt and inflate a fastfile")
    p.add_argument("zone")
    p.add_argument("directory")

    p = sub.add_parser("pack", parents=[common], help="rebuild a fastfile from an unpacked folder")
    p.add_argument("directory")
    p.add_argument("-o", "--output", required=True)
    p.add_argument("--keep-size", action="store_true", help="do not derive the zone length field")

    p = sub.add_parser(
        "verify", parents=[common], help="check a zone, optionally against its source"
    )
    p.add_argument("zone")
    p.add_argument("--against", help="the source zone to compare asset by asset")

    p = sub.add_parser("rebuild", parents=[common], help="parse, write back and pack a zone")
    p.add_argument("zone")
    p.add_argument("-o", "--output", required=True)
    p.add_argument("--recompress", action="store_true", help="deflate every chunk afresh")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return EXIT_USAGE
    from opent5.edit.types import EditError

    for attr in ("zone", "against"):
        value = getattr(args, attr, None)
        if value:
            setattr(args, attr, zone_by_name(value))
    run, render = COMMANDS[args.command]
    as_json = getattr(args, "json", False)
    try:
        data = run(args)
    except UsageError as exc:
        parser.print_usage(sys.stderr)
        print(f"opent5: error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except Failure as exc:
        if as_json:
            print(json.dumps({"ok": False, "error": str(exc), **exc.data}, indent=2, default=str))
        else:
            if exc.data and args.command in ("verify", "rebuild", "replace"):
                print(render({**exc.data, **({"ok": False} if args.command == "verify" else {})}))
            print(f"opent5 {args.command}: {exc}", file=sys.stderr)
        return EXIT_FAIL
    except (OSError, ValueError, EditError) as exc:
        message = str(exc) if isinstance(exc, EditError) else f"{type(exc).__name__}: {exc}"
        if as_json:
            print(json.dumps({"ok": False, "error": message}, indent=2))
        else:
            print(f"opent5 {args.command}: {message}", file=sys.stderr)
        return EXIT_FAIL
    if as_json:
        print(json.dumps({"ok": True, **data}, indent=2, default=str))
    else:
        print(render(data))
    return EXIT_OK
