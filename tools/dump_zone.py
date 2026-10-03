"""Dump a zone completely to open formats (PNG, OBJ/MTL, JSON, CSV, text). Headless.

    .venv/bin/python tools/dump_zone.py mp_nuked                 # finds mp_nuked.ff via .env
    .venv/bin/python tools/dump_zone.py mp_firingrange --out /tmp/fr
    .venv/bin/python tools/dump_zone.py path/to/zone.ff --no-images --no-previews
    .venv/bin/python tools/dump_zone.py --zone-file inflated.zone --name mp_nuked

The zone is read through .env (nothing is written beside it). Streamed image
pixels are read from .pak files in the zone's own folder and the folders in
.env. Output defaults to out/extract/<zone>/ (git-ignored). See docs/extract.md.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from opent5 import env  # noqa: E402
from opent5.container.zone import Zone  # noqa: E402
from opent5.export.zone import ZoneExporter  # noqa: E402


def find_zone(arg: str) -> Path | None:
    path = Path(arg)
    if path.is_file():
        return path
    stem = arg[:-3] if arg.endswith(".ff") else arg
    for zone in env.all_zones():
        if zone.stem == stem:
            return zone
    return None


def pak_dirs(zone_path: Path | None) -> list[Path]:
    dirs = []
    if zone_path is not None:
        dirs.append(zone_path.parent)
    for key in ("OPENT5_PATCH_ZONES", "OPENT5_ZONES"):
        found = env.path_of(key)
        if found and found.is_dir() and found not in dirs:
            dirs.append(found)
    return dirs


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("zone", nargs="?", help="zone name (found via .env) or a .ff path")
    ap.add_argument("--zone-file", type=Path, help="an already inflated .zone stream")
    ap.add_argument("--name", help="zone name when using --zone-file")
    ap.add_argument("--out", type=Path, help="output folder (default out/extract/<zone>)")
    ap.add_argument("--no-images", action="store_true", help="skip PNG decoding")
    ap.add_argument("--no-models", action="store_true", help="skip model meshes")
    ap.add_argument(
        "--no-instances",
        action="store_true",
        help="skip the OBJ of static models placed in the world",
    )
    ap.add_argument("--no-previews", action="store_true", help="skip the preview renders")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    zone_path = None
    if args.zone_file:
        content = args.zone_file.read_bytes()
        name = args.name or args.zone_file.stem
        zone_path = find_zone(name)
    elif args.zone:
        zone_path = find_zone(args.zone)
        if zone_path is None:
            print(
                f"zone {args.zone!r}: not a file and not found in the folders in .env",
                file=sys.stderr,
            )
            return 2
        started = time.perf_counter()
        content = Zone.open(zone_path).content
        name = zone_path.stem
        if not args.quiet:
            print(f"inflated {zone_path.name} in {time.perf_counter() - started:.1f} s")
    else:
        ap.error("give a zone name, a .ff path, or --zone-file")
    out = args.out or ROOT / "out" / "extract" / name
    log = (lambda _m: None) if args.quiet else (lambda m: print(m, flush=True))
    manifest = ZoneExporter(
        content,
        name,
        out,
        pak_dirs(zone_path),
        images=not args.no_images,
        models=not args.no_models,
        instance_models=not args.no_instances,
        previews=not args.no_previews,
        log=log,
    ).run()
    counts = manifest["counts"]
    print(
        json.dumps(
            {
                "out": str(out),
                "exported": counts["exported"],
                "files": counts["files"],
                "failures": counts["failures"],
                "seconds": manifest["seconds"],
            },
            indent=1,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
