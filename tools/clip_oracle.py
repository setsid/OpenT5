"""Offline structural oracle for clip-brush collision, used in the collision R&D
(docs/research/clipmap-add-brush-recipe.md).

It models how the engine reaches a world brush at a point: point-locate the cNode
BSP leaf (``BspLocator``), then gather the brushes reachable from that leaf's
leafBrushNode kd-tree (``ClipMap.reachable_brushes``). ``validate`` checks the model
against stock ground truth (the mp_nuked bus player-clip cluster is solid, open
space is not); ``solid_at`` reports whether a player-clip brush covers a point.

Caveat proven in the R&D: this oracle reports p_clip_a's added clip as solid, yet the
device walks through it, so the oracle does NOT reproduce the device failure. It is a
necessary check (a clip it calls not-solid is certainly not solid), not a sufficient
one. Do not gate a device build solely on it. Local CPU only; not product code.

    python tools/clip_oracle.py validate ZONE.ff
    python tools/clip_oracle.py solid ZONE.ff X Y Z
"""
from __future__ import annotations
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tools" / "loader_emu"))

from opent5.container.zone import Zone  # noqa: E402
from opent5.xfile import parse  # noqa: E402
from opent5.xfile.constants import AssetType  # noqa: E402
from opent5.convert.propclip import ClipMap, BspLocator, PLAYER_CLIP_CONTENTS  # noqa: E402


def load(path: str):
    data = Path(path).read_bytes()
    content = bytes(Zone.open(data).content) if data[:8] == b"IWff0100" else data
    x = parse(content, log=True)
    for a in x.assets:
        if a.type in (AssetType.COL_MAP_MP, AssetType.COL_MAP_SP):
            return x, a
    raise SystemExit(f"{path}: no clipMap")


# A player hull half-extent, so the check sweeps a box like the real trace and does
# not fall through the gaps between a cluster's small clip boxes.
PLAYER_HALF = (16.0, 16.0, 36.0)


def solid_at(cm: ClipMap, loc: BspLocator, point, contents=PLAYER_CLIP_CONTENTS, half=PLAYER_HALF):
    """Is a brush of ``contents`` whose AABB overlaps the player hull at ``point``
    reachable from the leaf ``point`` locates to? Returns (bool, leaf, [brushes])."""
    p = tuple(float(v) for v in point)
    leaf = loc.locate(p)
    if leaf is None:
        return False, None, []
    lo = tuple(p[k] - half[k] for k in range(3))
    hi = tuple(p[k] + half[k] for k in range(3))
    hits = []
    for b in cm.reachable_brushes(leaf):
        br = cm.brush(b)
        if (contents is None or br.contents == contents) and all(
            br.mins[k] <= hi[k] and lo[k] <= br.maxs[k] for k in range(3)
        ):
            hits.append(b)
    return bool(hits), leaf, hits


def validate(cm: ClipMap, loc: BspLocator) -> bool:
    """Check the oracle against stock mp_nuked ground truth: a point at the centre of
    a stock player-clip brush is solid; a point high in the air is not."""
    ok = True
    clips = [i for i in range(cm.num_brushes)
             if cm.brush(i).numsides == 0 and cm.brush(i).contents == PLAYER_CLIP_CONTENTS]
    hit = 0
    for i in clips:
        br = cm.brush(i)
        c = tuple((br.mins[k] + br.maxs[k]) / 2 for k in range(3))
        s, _, _ = solid_at(cm, loc, c, half=(1.0, 1.0, 1.0))
        hit += 1 if s else 0
    print(f"stock axial player-clips solid at their own centre: {hit}/{len(clips)}")
    if clips and hit < len(clips) * 0.95:
        print("  FAIL: expected stock player-clips to be solid at their centre"); ok = False
    air, _, _ = solid_at(cm, loc, (-96, -48, 454))
    print(f"high air (-96,-48,454): solid? {air} (expected False)")
    if air:
        ok = False
    return ok


def main() -> int:
    cmd = sys.argv[1]
    x, a = load(sys.argv[2])
    cm = ClipMap(a.data)
    loc = BspLocator.from_xfile(x, a.data)
    if cmd == "validate":
        print("OK" if validate(cm, loc) else "VALIDATION FAILED")
    elif cmd == "solid":
        p = tuple(float(v) for v in sys.argv[3:6])
        s, leaf, hits = solid_at(cm, loc, p)
        print(f"point {p}: located leaf {leaf}; player-clip solid? {s}; covering brushes {hits}")
    else:
        raise SystemExit("usage: validate ZONE.ff | solid ZONE.ff X Y Z")
    return 0


if __name__ == "__main__":
    sys.exit(main())
