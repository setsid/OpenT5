"""The ``opent5 texpack`` command group: apply a folder of textures to a zone, or list a
zone's image names.

``register(subparsers)`` builds the subparser; the lead wires it into ``opent5.cli`` and adds
``"texpack": (cmd_texpack, text_texpack)`` to the command table (docs/cli.md gives the exact
lines). Everything here is headless and front-end-agnostic: the work lives in
``opent5.texpack.pack`` and returns the result objects a GUI action would reuse.
"""

from __future__ import annotations

import argparse
from typing import Any

from opent5.texpack import naming, pack


def _fail(message: str, data: dict | None = None):
    from opent5.cli import Failure

    raise Failure(message, data)


def cmd_texpack(args) -> dict:
    action = getattr(args, "texpack_cmd", None)
    if action == "apply":
        return _apply(args)
    if action == "list":
        return _list(args)
    _fail(f"unknown texpack command {action!r}")
    return {}


def _apply(args) -> dict:
    try:
        if args.dry_run:
            result = pack.preview_pack(
                args.zone,
                args.packdir,
                args.map,
                resize=args.resize,
                allow_shared=args.allow_shared,
            )
        else:
            result = pack.apply_pack(
                args.zone,
                args.packdir,
                args.output,
                map_file=args.map,
                resize=args.resize,
                allow_shared=args.allow_shared,
            )
    except naming.TexpackError as exc:
        _fail(str(exc))
    data = result.to_dict()
    # Per-file failures (a wrong size, a name that matched nothing) are reported in the data
    # and do not fail the command, as with ``extract``; only a bad save does.
    if result.report and not result.report.get("verified", True):
        problems = result.report.get("problems", [])
        _fail("verification found problems: " + "; ".join(problems[:3]), data)
    return data


def _list(args) -> dict:
    try:
        images = pack.list_images(args.zone, replaceable_only=args.replaceable)
        template = None
        if args.template:
            template = str(pack.write_template(args.zone, args.template))
    except naming.TexpackError as exc:
        _fail(str(exc))
    return {
        "zone": args.zone,
        "count": len(images),
        "images": images,
        "template": template,
    }


def text_texpack(d: dict[str, Any]) -> str:
    if "images" in d:
        return _text_list(d)
    return _text_apply(d)


def _text_apply(d: dict) -> str:
    c = d["counts"]
    head = "would apply" if d["dry_run"] else "applied"
    lines = [
        f"{head} {d['pack_dir']} to {d['zone']}: "
        f"{c['replaced']} replaced, {c['skipped']} skipped, {c['failed']} failed "
        f"of {c['files']} file(s)"
    ]
    for f in d["files"]:
        name = f.get("image", "(no match)")
        size = f"{f['size'][0]}x{f['size'][1]}" if "size" in f else ""
        line = f"  {f['status']:<13} {f['file']} -> {name} {size}".rstrip()
        if f.get("reason"):
            line += f"  [{f['reason']}]"
        lines.append(line)
    if d.get("output"):
        r = d["report"]
        lines.append(f"  saved {d['output']} ({r['bytes']} bytes, sha1 {r['sha1']})")
        for p in r.get("paks", []):
            added = p.get("appended_entries") or []
            lines.append(
                f"  pak   {p['path']} ({p['bytes']} bytes); entries "
                f"{', '.join(map(str, p.get('edited_entries') or []))} written"
                + (f", {len(added)} added" if added else "")
            )
            if p.get("note"):
                lines.append(f"  warning: {p['note']}")
        if r.get("signature_note"):
            lines.append(f"  note: {r['signature_note']}")
    return "\n".join(lines)


def _text_list(d: dict) -> str:
    lines = []
    for im in d["images"]:
        flag = "" if im["replaceable"] else "  (not replaceable: " + (im["reason"] or "?") + ")"
        lines.append(
            f"  {im['format']:<8} {im['width']:>4}x{im['height']:<4} {im['pixels']:<8} "
            f"{im['name']}{flag}"
        )
    lines.append(f"{d['count']} image(s)")
    if d.get("template"):
        lines.append(f"template written: {d['template']}")
    return "\n".join(lines)


def register(subparsers) -> argparse.ArgumentParser:
    """Build the ``texpack`` subparser on ``subparsers`` (an argparse subparsers object) and
    return it. Each sub-command carries ``--json`` like every other OpenT5 command."""
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", help="machine-readable output on stdout")

    texpack = subparsers.add_parser(
        "texpack",
        parents=[common],
        help="batch-replace a zone's textures from a folder of PNG/DDS files",
        description="Drop a folder of PNGs named after the images they replace, and swap them "
        "across a zone and its .pak in one pass. docs/texture-pack.md has the naming rule.",
    )
    inner = texpack.add_subparsers(dest="texpack_cmd", metavar="SUBCOMMAND", required=True)

    apply_ = inner.add_parser(
        "apply", parents=[common], help="replace matching images and save a new zone (+ its pak)"
    )
    apply_.add_argument("zone", help="the zone to edit (path or bare name)")
    apply_.add_argument("packdir", help="a folder of PNG/DDS files named after the images")
    apply_.add_argument("-o", "--output", help="output folder for the new zone (and pak)")
    apply_.add_argument("--map", help="a JSON or CSV file -> image map for exact control")
    apply_.add_argument(
        "--resize",
        action="store_true",
        help="let a streamed image take another power-of-two size (pak.md 9.1)",
    )
    apply_.add_argument(
        "--allow-shared",
        action="store_true",
        help="also write parts in a shared pak (images_low, common, ui_mp); that changes the "
        "image in every zone that uses the pak",
    )
    apply_.add_argument(
        "--dry-run",
        action="store_true",
        help="list what would be replaced, skipped or fail, without writing anything",
    )

    list_ = inner.add_parser(
        "list", parents=[common], help="list a zone's image names (and optionally a map template)"
    )
    list_.add_argument("zone", help="the zone to read (path or bare name)")
    list_.add_argument(
        "--template", help="write a JSON map template (suggested file name -> image) here"
    )
    list_.add_argument(
        "--replaceable", action="store_true", help="only images that can be replaced"
    )

    return texpack
