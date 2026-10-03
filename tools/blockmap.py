"""Blocky (voxel) map generator CLI: write the ``.map`` and the textures, and drive the build.

    .venv/bin/python tools/blockmap.py gen -o OUTDIR [--seed N] [--nx 40 --ny 40 --nz 32]
                                     [--block 64] [--px 64] [--name mp_opent5blocks]
    .venv/bin/python tools/blockmap.py textures -o OUTDIR [--seed N] [--px 64]
    .venv/bin/python tools/blockmap.py build NAME --game GAME --work WORK -o OUT.ff [gen opts]
    .venv/bin/python tools/blockmap.py convert PC_MAP.ff -o OUTDIR [--base mp_nuked]

``gen`` writes a deterministic Radiant ``.map`` plus the block textures (PNG previews and DDS),
two terrain previews (top and angle) and ``report.json`` (seed, counts, the brush/surface
counts against the engine limits). ``build`` runs the PC Mod Tools with the exact command lines
of tools/testmap.py / docs/research/box-map.md 1.2 (requires the tools; absent here). ``convert``
drives the committed converter on a PC zone, passing the generated textures as overrides.

Not product code. See docs/mapgen.md.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

import numpy as np  # noqa: E402

from opent5.formats import texture as tx  # noqa: E402
from opent5.mapgen import greedy, mapwriter, preview, terrain, textures  # noqa: E402

#: The headline engine caps this map stays well under, with their evidence. See docs/mapgen.md.
LIMITS = {
    "collision_brushes": (
        65535,
        "clipMap_t.numBrushes is uint16_t (OAT src/Common/Game/T5/T5_Assets.h)",
    ),
    "gfx_surfaces": (
        65535,
        "GfxWorldDpvsStatic surfaceCount is uint16_t (OAT T5_Assets.h)",
    ),
    "group_vertices": (
        65535,
        "GfxWorld indices are uint16_t* (OAT T5_Assets.h); a surface group is u16-addressable",
    ),
}


def _gen(args) -> tuple[terrain.Terrain, list, dict]:
    t = terrain.generate(nx=args.nx, ny=args.ny, nz=args.nz, block=args.block, seed=args.seed)
    boxes, counts = greedy.mesh(t)
    return t, boxes, counts


def write_textures(outdir: Path, seed: int, px: int) -> list[dict]:
    tdir = outdir / "textures"
    tdir.mkdir(parents=True, exist_ok=True)
    rows = []
    # contact sheet
    pad, cols = 6, 6
    names = textures.TILE_NAMES
    n_rows = (len(names) + cols - 1) // cols
    sheet = np.full((n_rows * (px + pad) + pad, cols * (px + pad) + pad, 4), 30, np.uint8)
    sheet[..., 3] = 255
    for i, name in enumerate(names):
        rgba = textures.render(name, px, seed)
        (tdir / f"{name}.png").write_bytes(textures.preview_png(rgba))
        fmt, stored = textures.encode_ps3(rgba)
        dds = tx.stored_to_dds(stored, fmt, px, px, tx.full_levels(px, px))
        (tdir / f"{name}.dds").write_bytes(dds)
        r, c = divmod(i, cols)
        a = rgba[..., 3:4].astype(np.float32) / 255
        comp = (rgba[..., :3].astype(np.float32) * a + 30 * (1 - a)).astype(np.uint8)
        y, x = pad + r * (px + pad), pad + c * (px + pad)
        sheet[y : y + px, x : x + px, :3] = comp
        rows.append(
            {
                "tile": name,
                "material": mapwriter.material_name(name),
                "colormap": mapwriter.colormap_name(name),
                "format": tx.format_name(fmt),
                "stored_bytes": len(stored),
            }
        )
    (tdir / "_sheet.png").write_bytes(tx.write_png(sheet))
    return rows


def cmd_gen(args) -> int:
    outdir = Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)
    t, boxes, counts = _gen(args)
    map_path = outdir / f"{args.name}.map"
    map_path.write_text(mapwriter.map_text(t, boxes), newline="\r\n")
    (outdir / "preview_top.png").write_bytes(preview.write(preview.top_view(t)))
    (outdir / "preview_angle.png").write_bytes(preview.write(preview.angle_view(t)))
    tex = write_textures(outdir, args.seed, args.px)
    report = {
        "name": args.name,
        "seed": args.seed,
        "grid": {"nx": args.nx, "ny": args.ny, "nz": args.nz, "block": args.block},
        "terrain": t.counts,
        "mesh": counts,
        "limits": {
            k: {"cap": cap, "evidence": ev, "headroom": cap - counts.get(_map_key(k), 0)}
            for k, (cap, ev) in LIMITS.items()
        },
        "textures": tex,
        "map": {
            "path": str(map_path),
            "bytes": map_path.stat().st_size,
            "sha1": hashlib.sha1(map_path.read_bytes()).hexdigest(),
        },
    }
    (outdir / "report.json").write_text(json.dumps(report, indent=1))
    print(json.dumps(report, indent=1))
    return 0


def _map_key(limit: str) -> str:
    return {
        "collision_brushes": "brushes",
        "gfx_surfaces": "surfaces",
        "group_vertices": "vertices_est",
    }[limit]


def cmd_textures(args) -> int:
    outdir = Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)
    rows = write_textures(outdir, args.seed, args.px)
    print(json.dumps({"textures": rows}, indent=1))
    return 0


def cmd_build(args) -> int:
    """Compile the generated ``.map`` with the PC Mod Tools, exact command lines of
    tools/testmap.py (box-map.md 1.2). Writes only new ``<name>.*`` files in the game folder."""
    import testmap  # the sibling tool holds the command-line construction

    t, boxes, _ = _gen(args)
    game_dir = testmap._wsl(args.game)
    if not (game_dir / "bin" / "launcher_ldr.exe").is_file():
        raise SystemExit(
            f"{args.game}: bin\\launcher_ldr.exe (the PC Mod Tools) not found; cannot build. "
            "The textures and .map from `gen` are the offline deliverables."
        )
    # Write the map into --work, then run testmap's exact three steps via a shimmed write_map.
    work_dir = testmap._wsl(args.work)
    work_dir.mkdir(parents=True, exist_ok=True)
    (work_dir / f"{args.name}.map").write_text(mapwriter.map_text(t, boxes), newline="\r\n")
    testmap.map_text = lambda **kw: mapwriter.map_text(t, boxes)  # build() calls write_map()
    report = testmap.build(args.name, args.game, args.work, Path(args.out))
    print(json.dumps(report, indent=1))
    return 0


def _base_path(base: str) -> Path:
    p = Path(base)
    if p.is_file():
        return p
    from opent5 import env

    for d in env.zone_dirs():
        cand = d / f"{base}.ff"
        if cand.is_file():
            return cand
    raise SystemExit(f"base zone {base!r} not found (a path, or <name>.ff in OPENT5_ZONES)")


def cmd_convert(args) -> int:
    """Drive the committed converter on a PC zone, with the generated art as overrides.

    The map's own block textures are passed as ``overrides`` keyed by colour-map name; the
    material structure (techset, texture defs) still comes from the PC zone, so the PC build
    (``build``) must have produced ``PC_MAP.ff`` first.
    """
    from opent5.convert.mapzone import convert_map

    pc = Path(args.pc_map)
    if not pc.is_file():
        raise SystemExit(f"{pc}: PC zone not found; run `build` first (needs the PC Mod Tools)")
    base = _base_path(args.base)
    overrides = {
        mapwriter.colormap_name(n): textures.render(n, args.px, args.seed)
        for n in textures.TILE_NAMES
    }
    outdir = Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)
    result = convert_map(
        pc.read_bytes(),
        base,
        name=args.name,
        image_roots=(),
        force_materials=True,
        overrides=overrides,
    )
    target = outdir / f"{args.name or base.stem}.ff"
    target.write_bytes(result.fastfile)
    out = {
        "source": str(pc),
        "base": str(base),
        "output": str(target),
        "zone_name": result.zone_name,
        "overrides": len(overrides),
        "report": result.report,
    }
    (outdir / "convert.json").write_text(json.dumps(out, indent=1, default=str))
    print(json.dumps(out, indent=1, default=str))
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)

    def gen_opts(c):
        c.add_argument("--seed", type=int, default=1)
        c.add_argument("--nx", type=int, default=40)
        c.add_argument("--ny", type=int, default=40)
        c.add_argument("--nz", type=int, default=32)
        c.add_argument("--block", type=int, default=64)
        c.add_argument("--px", type=int, default=64, help="texture size (multiple of 16)")
        c.add_argument("--name", default="mp_opent5blocks")

    g = sub.add_parser("gen")
    g.add_argument("-o", "--out", required=True)
    gen_opts(g)
    g.set_defaults(func=cmd_gen)

    tx_ = sub.add_parser("textures")
    tx_.add_argument("-o", "--out", required=True)
    tx_.add_argument("--seed", type=int, default=1)
    tx_.add_argument("--px", type=int, default=64)
    tx_.set_defaults(func=cmd_textures)

    b = sub.add_parser("build")
    b.add_argument("name")
    b.add_argument("--game", required=True)
    b.add_argument("--work", required=True)
    b.add_argument("-o", "--out", required=True)
    gen_opts(b)
    b.set_defaults(func=cmd_build)

    cv = sub.add_parser("convert")
    cv.add_argument("pc_map")
    cv.add_argument("-o", "--out", required=True)
    cv.add_argument("--base", default="mp_nuked")
    cv.add_argument("--seed", type=int, default=1)
    cv.add_argument("--px", type=int, default=64)
    cv.set_defaults(func=cmd_convert)

    args = p.parse_args(argv)
    # convert reuses the gen defaults for grid/name it does not define
    defaults = (("name", "mp_opent5blocks"), ("block", 64), ("nx", 40), ("ny", 40), ("nz", 32))
    for attr, default in defaults:
        if not hasattr(args, attr):
            setattr(args, attr, default)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
