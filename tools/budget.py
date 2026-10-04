"""Report the mp_nuked zone budget: .ff bytes, decompressed content, header-declared size and
block sizes, and the gfx/collision u16 counts against their caps. Not product code."""

from __future__ import annotations

import os
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from opent5.edit.document import Document
from opent5.xfile.constants import AssetType as T

U16 = 65535


def report(path: str) -> None:
    ff = os.path.getsize(path)
    doc = Document.open(path)
    content = len(doc.content)
    hdr = doc.xfile.header
    blocks = hdr.block_sizes
    print(f"\n===== {path} =====")
    print(f"  .ff on disk            : {ff:,} bytes")
    print(f"  decompressed content   : {content:,} bytes")
    print(f"  header declared size   : {hdr.size:,} bytes  (content - 32 header)")
    print(f"  block sizes            : {[hex(b) for b in blocks]}  sum={sum(blocks):,}")
    counts = doc.xfile.counts()
    print(f"  asset counts           : {counts}")
    for a in doc.xfile.assets:
        if a.type == T.GFX_MAP:
            n = a.data
            surfs = len(n.get("surfaces") or [])
            verts = len(n.get("vertices") or b"") // 0x10
            idx = len(n.get("indices") or b"") // 2
            print(f"  gfx surfaceCount       : {surfs:,} / {U16:,}  ({100 * surfs / U16:.1f}%)")
            print(f"  gfx vertexCount        : {verts:,}")
            print(f"  gfx indexCount         : {idx:,} / {U16:,}  ({100 * idx / U16:.1f}%)")
        if a.type == T.COL_MAP_MP:
            n = a.data
            brushes = len(n.get("brushes") or b"") // 0x60
            print(f"  clipMap numBrushes     : {brushes:,} / {U16:,}  ({100 * brushes / U16:.1f}%)")


if __name__ == "__main__":
    for p in sys.argv[1:]:
        report(p)
