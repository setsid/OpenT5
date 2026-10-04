"""Stage a built zone for a device test with the mandatory anti-stale-cache protocol.

RPCS3 caches the decrypted disc in ``dev_hdd1\\caches\\<title>\\cache.dat`` (~1.68 GB for
BLES01031). Copy-Item preserves a file's mtime, so an older-dated build silently loads from
that cache instead of the new one - which is how a correct o_blocks5 rendered as Nuketown and
a working clip read as "no collision". Every device build goes through here, which:

  1. writes the staged file with a FRESH mtime (so a copy carries a new timestamp);
  2. requires a visible in-map marker to be named, so a stale load is obvious on screen;
  3. runs the offline staged-map gate (``verify_staged_map``) when a base is given;
  4. prints a CHECKLIST whose step 1 is clearing the RPCS3 cache.

See docs/hwtest-checklist.md for the full procedure.

    .venv/bin/python tools/stage_build.py BUILT.ff DEST/mp_nuked.ff \
        --label p_clip_x --landmark "doorway north of the T spawn" \
        --marker "red crate at the clip" --base BASE.ff
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import verify_staged_map as gate  # noqa: E402


def stage(
    data: bytes,
    dest: str | Path,
    *,
    label: str,
    landmark: str,
    marker: str,
    base: str | Path | None = None,
    expect_brushes: int | None = None,
) -> dict:
    """Write ``data`` to ``dest`` with a fresh mtime and print the device CHECKLIST.

    ``marker`` must be a non-empty description of a visible in-map marker; staging a test
    build without one is refused, because a stale cache load is otherwise invisible.
    Returns the staged signature and sha1.
    """
    if not marker or not marker.strip():
        raise ValueError("a visible in-map marker must be named for every device build")
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    os.utime(dest, None)  # fresh mtime: a later copy carries a new timestamp, busting the cache
    sha = hashlib.sha1(data).hexdigest()

    sig = gate.signature(dest)
    base_sig = gate.signature(base) if base is not None else None
    # A full-map (blocky) build is gated against the base (must differ); a clip-only edit
    # keeps the base world on purpose, so it is gated by its expected brush count instead.
    flags = {"brushes": expect_brushes} if expect_brushes is not None else {}
    gate_problems = gate.check(sig, base_sig, None, flags)

    print(_checklist(label, dest, sha, landmark, marker, sig, gate_problems))
    if gate_problems:
        print("WARNING: staged-map gate problems:")
        for p in gate_problems:
            print(f"  - {p}")
    return {"sha1": sha, "signature": sig, "gate_problems": gate_problems}


def _checklist(label, dest, sha, landmark, marker, sig, gate_problems) -> str:
    ok = "clean" if not gate_problems else f"{len(gate_problems)} PROBLEM(S) - see below"
    return (
        f"\n=== DEVICE TEST CHECKLIST: {label} ===\n"
        f"staged file : {dest}\n"
        f"sha1        : {sha}\n"
        f"fresh mtime : set to now by this script\n"
        f"gate        : {ok}\n"
        f"signature   : {sig}\n"
        f"\nBefore testing, in order:\n"
        f"  1. Close RPCS3. Clear or rename dev_hdd1\\caches\\BLES01031_BLES01031 "
        f"(the FIOS disc cache) - a stale cache will serve the OLD map.\n"
        f"  2. Copy the staged mp_nuked.ff to the load path.\n"
        f"  3. Confirm the file at the load path has sha1 {sha[:12]} and a current timestamp.\n"
        f"  4. Launch and look for the visible marker: {marker}.\n"
        f"     If the marker is absent, you are looking at a STALE load - go back to step 1.\n"
        f"  5. Run the test at: {landmark}.\n"
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("built", help="the built zone .ff to stage")
    ap.add_argument("dest", help="where to stage it (e.g. .../nuked/p_clip_x/mp_nuked.ff)")
    ap.add_argument("--label", required=True, help="a short name for the build")
    ap.add_argument("--landmark", required=True, help="where to run the test, in words")
    ap.add_argument("--marker", required=True, help="the visible in-map marker to look for")
    ap.add_argument("--base", help="the base zone, to run the staged-map gate against")
    ap.add_argument("--expect-brushes", type=int, help="assert the clip brush count")
    args = ap.parse_args(argv)
    data = Path(args.built).read_bytes()
    result = stage(
        data,
        args.dest,
        label=args.label,
        landmark=args.landmark,
        marker=args.marker,
        base=args.base,
        expect_brushes=args.expect_brushes,
    )
    return 1 if result["gate_problems"] else 0


if __name__ == "__main__":
    sys.exit(main())
