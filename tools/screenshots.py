"""Render every GUI view offscreen against real zones into out/screenshots/.

    QT_QPA_PLATFORM=offscreen .venv/bin/python tools/screenshots.py [--only NAME ...]

The driver is opent5.gui.shots, which the packaged exe also runs with --screenshots.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from opent5.gui import shots  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--only", nargs="*", help="substrings of screenshot names to render")
    ap.add_argument("--out", type=Path, default=ROOT / "out" / "screenshots")
    args = ap.parse_args(argv)
    return shots.run(args.out, args.only)


if __name__ == "__main__":
    sys.exit(main())
