"""Verify a staged zone actually contains the intended map, not the base zone.

A converter bug, a bad copy, a shared output directory or a device-side override can
leave a staged ``.ff`` that is really the base zone (or some other map) while the build
log looks fine. This reads the staged zone's GfxWorld and clipMap signature (gfx surface
count, dpvs cell count, world bounds, clip brush count, clip static-model count) and
checks it:

  - against the base it was built onto, asserting the two DIFFER (a staged file whose
    GfxWorld matches the base was never replaced);
  - optionally against an expected signature, from ``--emit`` output or explicit flags
    (the map the build intended to produce);
  - optionally that the base file still hashes to a known value (read-only base check).

    .venv/bin/python tools/verify_staged_map.py STAGED.ff --base BASE.ff
    .venv/bin/python tools/verify_staged_map.py STAGED.ff --emit > built.sig.json   # at build time
    .venv/bin/python tools/verify_staged_map.py STAGED.ff --base BASE.ff --expect built.sig.json

Exit code 0 when every check passes, 1 otherwise. No files are written.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from opent5.container.zone import Zone  # noqa: E402
from opent5.xfile import parse  # noqa: E402
from opent5.xfile.constants import AssetType as T  # noqa: E402
from opent5.xfile.schema import view  # noqa: E402


def signature(ff_path: str | Path) -> dict:
    """The GfxWorld + clipMap signature that distinguishes one map from another.

    Parses the zone but exports nothing. Every field is a plain number so the signature
    is cheap to compare and to store as JSON.
    """
    z = Zone.open(str(ff_path))
    xfile = parse(bytes(z.content), log=False)
    gfx = None
    clip = None
    for asset in xfile.assets:
        if asset.type == T.GFX_MAP and gfx is None:
            f = view(asset.data).fields
            gfx = {
                "surfaces": int(f.surfaceCount),
                "cells": int(f["dpvsPlanes.cellCount"]),
                "mins": [round(float(x), 1) for x in f.mins],
                "maxs": [round(float(x), 1) for x in f.maxs],
            }
        elif asset.type in (T.COL_MAP_MP, T.COL_MAP_SP) and clip is None:
            c = asset.data
            v = view(c)
            brushes = v.array("brushes") if c.get("brushes") is not None else []
            statics = (
                v.array("static_model_list") if c.get("static_model_list") is not None else []
            )
            clip = {"brushes": len(brushes), "static_models": len(statics)}
    return {"gfx": gfx, "clip": clip}


def sha1_of(path: str | Path) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def same_map(a: dict, b: dict) -> bool:
    """True when two signatures describe the same GfxWorld (surfaces, cells and bounds).

    Bounds and surface/cell counts are what change when the map is replaced; a converted
    map that merely reuses the base's asset names still differs here.
    """
    if not a.get("gfx") or not b.get("gfx"):
        return False
    ag, bg = a["gfx"], b["gfx"]
    return (
        ag["surfaces"] == bg["surfaces"]
        and ag["cells"] == bg["cells"]
        and ag["mins"] == bg["mins"]
        and ag["maxs"] == bg["maxs"]
    )


def check(staged_sig, base_sig, expect_sig, expect_flags) -> list[str]:
    problems: list[str] = []
    if staged_sig.get("gfx") is None:
        problems.append("staged zone has no GfxWorld (gfx_map) asset")
    if base_sig is not None and same_map(staged_sig, base_sig):
        problems.append(
            "staged GfxWorld matches the base (same surfaces, cells and bounds): "
            "the map was not replaced - the staged file is effectively the base zone"
        )
    if expect_sig is not None and not same_map(staged_sig, expect_sig):
        problems.append(
            f"staged GfxWorld {staged_sig.get('gfx')} does not match the expected built map "
            f"{expect_sig.get('gfx')}"
        )
    g = staged_sig.get("gfx") or {}
    c = staged_sig.get("clip") or {}
    for key, want, got in (
        ("surfaces", expect_flags.get("surfaces"), g.get("surfaces")),
        ("cells", expect_flags.get("cells"), g.get("cells")),
        ("brushes", expect_flags.get("brushes"), c.get("brushes")),
    ):
        if want is not None and want != got:
            problems.append(f"gfx/clip {key} is {got}, expected {want}")
    return problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("staged", help="the staged zone .ff to verify")
    ap.add_argument("--base", help="the base zone the build was made onto (must differ)")
    ap.add_argument("--base-sha1", help="assert the base file hashes to this (read-only check)")
    ap.add_argument("--expect", help="a signature JSON (from --emit) the staged map must match")
    ap.add_argument("--expect-surfaces", type=int, help="assert the gfx surface count")
    ap.add_argument("--expect-cells", type=int, help="assert the gfx dpvs cell count")
    ap.add_argument("--expect-brushes", type=int, help="assert the clip brush count")
    ap.add_argument("--emit", action="store_true", help="print the staged signature JSON and exit")
    args = ap.parse_args(argv)

    staged_sig = signature(args.staged)
    if args.emit:
        print(json.dumps(staged_sig, indent=1))
        return 0

    base_sig = signature(args.base) if args.base else None
    expect_sig = json.loads(Path(args.expect).read_text()) if args.expect else None
    expect_flags = {
        "surfaces": args.expect_surfaces,
        "cells": args.expect_cells,
        "brushes": args.expect_brushes,
    }

    def label(p: str) -> str:
        return f"{Path(p).parent.name}/{Path(p).name}"

    print(f"staged {label(args.staged)}: {json.dumps(staged_sig)}")
    if base_sig is not None:
        print(f"base   {label(args.base)}: {json.dumps(base_sig)}")

    problems = check(staged_sig, base_sig, expect_sig, expect_flags)

    if args.base_sha1:
        if base_sig is None:
            problems.append("--base-sha1 needs --base")
        else:
            got = sha1_of(args.base)
            want = args.base_sha1.lower()
            if not got.startswith(want):  # a prefix is enough, so a short hash works
                problems.append(
                    f"base sha1 is {got[: max(len(want), 12)]}, expected {want} (base changed)"
                )

    if problems:
        print("FAIL:")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("PASS: staged zone contains a distinct map (not the base)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
