"""Diagnostic: measure the gfx_map RUNTIME residual in built PC zones.

Parses each PC zone, instruments the gfx handler's runtime() reserves and header
field reads, and reports the per-asset RUNTIME cursor against what the header wants.
Not product code.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from opent5.convert import pc as pcmod
from opent5.xfile.constants import Block
from opent5.xfile.model import parse


def probe(path: Path) -> None:
    content = pcmod.open_pc_zone(path)

    log: list = []
    hdr: dict = {}
    orig_runtime = pcmod.runtime

    def traced_runtime(io, chunk, off, mask, size):
        before = io.pos[Block.RUNTIME]
        orig_runtime(io, chunk, off, mask, size)
        after = io.pos[Block.RUNTIME]
        log.append((off, mask, size, before, after, after - before))

    # capture header field values at gfx parse time
    orig_body = pcmod.PCGfxWorldHandler.body

    def traced_body(self, io, h, node):
        start = io.pos[Block.RUNTIME]
        fields = {
            "sun_index(0xFC)": h.u32(0xFC),
            "lights(0x100)": h.u32(0x100),
            "cells(0x140)": h.s32(0x140),
            "surfaces(0x10)": h.s32(0x10),
            "smodels(0x344)": h.u32(0x344),
            "static_surfaces(0x34C)": h.u32(0x34C),
            "smodel_vis(0x368)": h.u32(0x368),
            "surface_vis(0x36C)": h.u32(0x36C),
            "words(0x3B4,0x3B8)": (h.u32(0x3B4), h.u32(0x3B8)),
            "dyn(0x3BC,0x3C0)": (h.u32(0x3BC), h.u32(0x3C0)),
        }
        hdr.update(fields)
        hdr["_rt_start"] = start
        orig_body(self, io, h, node)
        hdr["_rt_end"] = io.pos[Block.RUNTIME]

    pcmod.runtime = traced_runtime
    pcmod.PCGfxWorldHandler.body = traced_body
    try:
        xf = parse(content, log=False, platform=pcmod.PC)
    finally:
        pcmod.runtime = orig_runtime
        pcmod.PCGfxWorldHandler.body = orig_body

    probs = xf.problems()
    print(f"\n===== {path} =====")
    for k, v in hdr.items():
        if not k.startswith("_"):
            print(f"  {k} = {v}")
    sun = hdr["sun_index(0xFC)"]
    lights = hdr["lights(0x100)"]
    print(f"  shadow = lights - sun - 1 = {lights - sun - 1}")
    print(f"  gfx RUNTIME consumed = {hdr['_rt_end'] - hdr['_rt_start']:#x}")
    print("  problems:")
    for p in probs:
        print("    " + p)
    if not probs:
        print("    (none - exact)")
    # per-reserve log
    print("  runtime reserves (off, mask, size, before, after, delta):")
    for off, mask, size, b, a, d in log:
        pad = b
        # show alignment pad applied
        print(f"    {off:#06x} mask={mask:<4} size={size:#010x} {b:#x}->{a:#x} (+{d:#x})")


if __name__ == "__main__":
    for arg in sys.argv[1:]:
        probe(Path(arg))
