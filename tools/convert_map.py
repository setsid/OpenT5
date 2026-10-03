"""Converter tool: convert a PC map, compare the converter with a map that exists on both
platforms, and check a converted zone with the game's own loader (emulated).

    .venv/bin/python tools/convert_map.py convert PC.ff --base mp_nuked -o OUTDIR
    .venv/bin/python tools/convert_map.py compare PC_MAP.ff PS3_MAP.ff [-o report.json]
    .venv/bin/python tools/convert_map.py oracle ZONE.ff|ZONE.zone [-o report.json]

``compare`` walks the PC zone from its com_map to its col_map_mp (pc.walk_range; the PC
XModel / FX / sound handlers are not written, docs/convert.md) and runs
opent5.convert.compare.compare_world. ``oracle`` runs t5mp.elf's XFile loader in the local
PowerPC interpreter in tools/loader_emu (the harness tools/remap_oracle.py uses) over the
content: it must consume the stream exactly, end every block at the header's size, and
convert exactly the offset / alias pointers the product parser reads, with the same values,
each landing inside its block. Local CPU only; nothing is run on a console or an emulator
of one. Not product code.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def _content(path: Path) -> bytes:
    data = path.read_bytes()
    if data[:8] == b"IWff0100":
        from opent5.container.zone import Zone

        return bytes(Zone.open(data).content)
    return data


def cmd_convert(args) -> dict:
    from opent5.cli import zone_by_name
    from opent5.convert.mapzone import convert_map

    base = Path(zone_by_name(args.base))
    t0 = time.time()
    result = convert_map(Path(args.pc).read_bytes(), base, lighting=args.lighting)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    target = out / f"{result.zone_name}.ff"
    target.write_bytes(result.fastfile)
    report = dict(result.report)
    report["output"] = str(target)
    report["seconds"] = round(time.time() - t0, 1)
    (out / "convert.json").write_text(json.dumps(report, indent=1, default=str))
    return report


def pc_world_range(content: bytes) -> tuple[int, int, int]:
    """(file offset of the com_map, its asset index, the col_map_mp's index) of a PC map
    zone: the com_map header (64 bytes, name -1) sits just before its inline name."""
    from opent5.convert.pc import _asset_array_offset

    count = struct.unpack_from("<I", content, 0x2C)[0]
    at = _asset_array_offset(content)
    types = [struct.unpack_from("<I", content, at + 8 * i)[0] for i in range(count)]
    com = types.index(13)  # PC com_map
    clip = types.index(12)  # PC col_map_mp
    marker = b"\xff\xff\xff\xff"
    pos = content.find(b".d3dbsp\0", at)
    while pos >= 0:
        start = content.rfind(b"maps/", 0, pos)
        header = start - 0x40
        if header > 0 and content[header : header + 4] == marker:
            return header, com, clip
        pos = content.find(b".d3dbsp\0", pos + 1)
    raise SystemExit("PC zone: no com_map header found before a .d3dbsp name")


def cmd_compare(args) -> dict:
    from opent5.convert.compare import compare_world
    from opent5.convert.pc import read_pc_fastfile

    raw = Path(args.pc).read_bytes()
    pc = read_pc_fastfile(raw) if raw[:8] == b"IWffu100" else raw
    ps3 = _content(Path(args.ps3))
    start, first, last = pc_world_range(pc)
    report = compare_world(pc, start, first, last, ps3)
    report["pc_world_range"] = {"com_map_offset": hex(start), "first": first, "last": last}
    return report


def cmd_oracle(args) -> dict:
    sys.path.insert(0, str(ROOT / "tools" / "loader_emu"))
    sys.path.insert(0, str(ROOT / "tools"))
    from opent5.xfile import parse
    from opent5.xfile.events import EventKind, PtrKind
    from remap_oracle import TO_ALIAS, TracingEmu

    content = _content(Path(args.zone))
    t0 = time.time()
    tr = TracingEmu(content)
    consumed = tr.run()
    header = struct.unpack_from(">9I", content, 0)
    final = tr.final_positions()
    blocks = list(header[2:9])
    # TEMP rewinds to 0; its header size is the high-water mark plus 16 (model.TEMP_SLACK).
    ends_match = all(final[b] == blocks[b] for b in range(1, 7)) and final[0] == 0
    x = parse(content, log=True)
    product = {}
    for row in x.log.of_kind(EventKind.POINTER).tolist():
        _, at, raw, kind, _, _ = row
        if kind in (PtrKind.OFFSET, PtrKind.ALIAS_REF):
            product[at] = (raw, kind)
    loader = {}
    outside = 0
    for fn, _addr, raw, fo in tr.convs:
        loader[fo] = (raw, PtrKind.ALIAS_REF if fn == TO_ALIAS else PtrKind.OFFSET)
        v = (raw - 1) & 0xFFFFFFFF
        b, off = v >> 29, v & 0x1FFFFFFF
        if b > 6 or off >= max(blocks[b], 1):
            outside += 1
    same_fields = set(product) == set(loader)
    same_values = sum(1 for k in product if loader.get(k, (None,))[0] == product[k][0])
    return {
        "content_bytes": len(content),
        "consumed": consumed,
        "consumed_exactly": consumed == len(content),
        "final_positions": [hex(p) for p in final],
        "header_blocks": [hex(b) for b in blocks],
        "blocks_end_at_header": ends_match,
        "loader_conversions": len(tr.convs),
        "product_offset_and_alias_pointers": len(product),
        "same_fields": same_fields,
        "same_values": same_values,
        "targets_outside_their_block": outside,
        "assets": len(tr.assets),
        "seconds": round(time.time() - t0, 1),
        "sha1_content": hashlib.sha1(content).hexdigest(),
    }


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("convert")
    c.add_argument("pc")
    c.add_argument("--base", required=True)
    c.add_argument("-o", "--out", required=True)
    c.add_argument("--lighting", default="flat", choices=("flat", "sunlit", "keep"))
    c = sub.add_parser("compare")
    c.add_argument("pc")
    c.add_argument("ps3")
    c.add_argument("-o", "--out")
    c = sub.add_parser("oracle")
    c.add_argument("zone")
    c.add_argument("-o", "--out")
    args = p.parse_args(argv)
    report = {"convert": cmd_convert, "compare": cmd_compare, "oracle": cmd_oracle}[args.cmd](args)
    text = json.dumps(report, indent=1, default=str)
    if getattr(args, "out", None) and args.cmd != "convert":
        Path(args.out).write_text(text)
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
