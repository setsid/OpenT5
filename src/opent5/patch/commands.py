"""The ``opent5 patch`` command group: create, apply, info.

``register(subparsers)`` builds the subparser; the lead wires it into ``opent5.cli`` and adds
``"patch": (cmd_patch, text_patch)`` to the command table (docs/patch-format.md gives the exact
lines). Everything here is headless and front-end-agnostic: the real work lives in
``opent5.patch.core`` and returns result objects the GUI reuses.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from opent5.patch import core
from opent5.patch.format import SUFFIX


def _zone_by_name(value: str) -> str:
    """A bare zone name such as ``patch_mp`` found in the .env folders; a path or an existing
    file is returned unchanged (mirrors opent5.cli.zone_by_name)."""
    if Path(value).exists() or "/" in value or "\\" in value or value.endswith(".ff"):
        return value
    from opent5 import env

    matches = [p for p in env.all_zones() if p.stem == value]
    return str(matches[0]) if matches else value


def _out_path(value: str) -> Path:
    """An output path that is never inside a configured game folder."""
    from opent5.edit import EditError, check_target

    try:
        check_target(Path(value), None)
    except EditError as exc:
        from opent5.cli import Failure

        raise Failure(str(exc)) from None
    return Path(value)


def _fail(message: str) -> None:
    from opent5.cli import Failure

    raise Failure(message)


# -- commands -------------------------------------------------------------------------------


def cmd_patch(args) -> dict:
    action = getattr(args, "patch_cmd", None)
    if action == "create":
        return _create(args)
    if action == "apply":
        return _apply(args)
    if action == "info":
        return _info(args)
    _fail(f"unknown patch command {action!r}")
    return {}


def _create(args) -> dict:
    stock = _zone_by_name(args.stock)
    edited = _zone_by_name(args.edited)
    target = _out_path(args.output)
    try:
        result = core.create(stock, edited)
    except core.PatchError as exc:
        _fail(str(exc))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(result.patch)
    data = {"action": "create", "output": str(target), **result.to_dict()}
    return data


def _apply(args) -> dict:
    patch_path = Path(args.patch)
    if not patch_path.is_file():
        _fail(f"{args.patch}: expected a patch file, found none")
    stock = _zone_by_name(args.stock)
    target = _out_path(args.output)
    try:
        result = core.apply(patch_path.read_bytes(), stock, target, verify=not args.no_verify)
    except core.PatchError as exc:
        _fail(str(exc))
    data = {"action": "apply", **result.to_dict()}
    if result.problems:
        from opent5.cli import Failure

        raise Failure("verification found problems: " + "; ".join(result.problems[:3]), data)
    return data


def _info(args) -> dict:
    patch_path = Path(args.patch)
    if not patch_path.is_file():
        _fail(f"{args.patch}: expected a patch file, found none")
    try:
        result = core.info(patch_path.read_bytes())
    except core.PatchError as exc:
        _fail(str(exc))
    return {"action": "info", "patch": str(patch_path), **result.to_dict()}


# -- text rendering -------------------------------------------------------------------------


def text_patch(d: dict[str, Any]) -> str:
    action = d.get("action")
    if action == "create":
        return _text_create(d)
    if action == "apply":
        return _text_apply(d)
    if action == "info":
        return _text_info(d)
    return "\n".join(f"{k}: {v}" for k, v in d.items() if k != "ok")


def _change_lines(d: dict) -> list[str]:
    return [
        f"    {c['index']:>6}  {c['type']:<13} {c['op']:<9} {c['size']:>8}  {c['name']}"
        for c in d.get("changes", [])
    ]


def _text_create(d: dict) -> str:
    lines = [
        f"patch {d['output']} ({d['patch_bytes']} bytes)",
        f"  source   {d['source_zone']}  "
        f"(content {d['source_bytes']} -> {d['edited_bytes']} bytes)",
        f"  ff sha1  {d['source_ff_sha1']}",
        f"  content  {d['source_content_sha1']}",
        f"  result   {d['result_content_sha1']}",
        f"  changes  {d['assets_changed']} asset(s)",
    ]
    lines += _change_lines(d)
    if d.get("signature_note"):
        lines.append(f"  note: {d['signature_note']}")
    return "\n".join(lines)


def _text_apply(d: dict) -> str:
    lines = [
        f"applied to {d['source_zone']} -> {d['output']} "
        f"({d['output_bytes']} bytes, sha1 {d['output_sha1']})",
        f"  source   verified: content sha1 {d['found_content_sha1']} matches the patch",
        f"  changes  {d['assets_changed']} asset(s)",
    ]
    lines += _change_lines(d)
    lines.append(
        f"  reproduces the edited zone: {'yes' if d['reproduces_target'] else 'NO'}; "
        f"verified: {'yes' if d['verified'] else 'no'}"
    )
    for note in d.get("notes", []):
        lines.append(f"  note: {note}")
    for problem in d.get("problems", []):
        lines.append(f"  problem: {problem}")
    if d.get("signature_note"):
        lines.append(f"  note: {d['signature_note']}")
    return "\n".join(lines)


def _text_info(d: dict) -> str:
    lines = [
        f"{d['patch']}",
        f"  format   version {d['format_version']}, made by {d.get('generator', '')}",
        f"  created  {d.get('created', '')}",
        f"  source   {d['source_zone']}  (signed: {'yes' if d.get('signed') else 'no'})",
        f"  ff sha1  {d['source_ff_sha1']}",
        f"  content  {d['source_content_sha1']}",
        f"  result   {d['result_content_sha1']}",
        f"  changes  {d['assets_changed']} asset(s)",
    ]
    lines += _change_lines(d)
    return "\n".join(lines)


# -- subparser ------------------------------------------------------------------------------


def register(subparsers) -> argparse.ArgumentParser:
    """Build the ``patch`` subparser on ``subparsers`` (an argparse subparsers object) and
    return it. Each sub-command carries ``--json`` like every other OpenT5 command."""
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", help="machine-readable output on stdout")

    patch = subparsers.add_parser(
        "patch",
        parents=[common],
        help="create, apply and inspect shareable mod patches",
        description="Share a mod as the difference from a stock zone, without redistributing "
        "any game files.",
    )
    inner = patch.add_subparsers(dest="patch_cmd", metavar="SUBCOMMAND", required=True)

    create = inner.add_parser(
        "create", parents=[common], help="capture the difference between a stock and edited zone"
    )
    create.add_argument("stock", help="the unmodified source zone (path or bare name)")
    create.add_argument("edited", help="the edited zone to capture the change from")
    create.add_argument("-o", "--output", required=True, help=f"the patch file to write ({SUFFIX})")

    apply_ = inner.add_parser(
        "apply", parents=[common], help="apply a patch to your own stock zone"
    )
    apply_.add_argument("patch", help="the patch file to apply")
    apply_.add_argument("stock", help="your unmodified copy of the source zone")
    apply_.add_argument("-o", "--output", required=True, help="the rebuilt zone to write")
    apply_.add_argument("--no-verify", action="store_true", help="skip verification after saving")

    info_ = inner.add_parser("info", parents=[common], help="describe a patch without applying it")
    info_.add_argument("patch", help="the patch file to describe")

    return patch
