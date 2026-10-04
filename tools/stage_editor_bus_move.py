"""Stage an EDITOR-MADE bus move for a device test (v0.3.0 Phase 2).

Drives the in-place map editor's own clip path (``opent5.edit.mapedit.EditSession``) to
MOVE the Nuketown school bus: it finds the bus's existing clip cluster by footprint and
moves the WHOLE cluster to the destination (so the old spot goes clear, not just a new
wall at the destination), moves the bus's render to match (the clipMap cStaticModel and
the GfxWorld draw instance), saves through the editor's save-with-verify onto the pristine
base, and stages the result with the mandatory anti-stale-cache protocol.

Device reading (the whole point of moving the existing cluster): at the destination the
bus is SOLID, and the OLD spot is CLEAR with no leftover invisible wall. The bus's baked
lightmap shadow stays at the old spot (the editor cannot relight); that is expected.

Local CPU only. No push, no deploy.

    .venv/bin/python tools/stage_editor_bus_move.py
"""

from __future__ import annotations

import hashlib
import struct
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

import stage_build  # noqa: E402

from opent5.convert import propclip as pc  # noqa: E402
from opent5.edit.document import Document  # noqa: E402
from opent5.edit.mapedit import EditSession  # noqa: E402
from opent5.xfile.constants import AssetType as T  # noqa: E402

BASE = "/mnt/c/Users/bolst/Desktop/opent5-hwtest/nuked/d_pak/mp_nuked.ff"
DEST = "/mnt/c/Users/bolst/Desktop/opent5-hwtest/nuked/q_editor_busmove/mp_nuked.ff"
BUS_MODEL = "t5_veh_schoolbus"
#: move the bus +300 X onto the central road crossing the clip R&D used (204,-47), a spot
#: players cross, so a leftover wall at the OLD spot would be obvious.
DELTA = (300.0, 0.0, 0.0)


def _gfx_node(doc) -> tuple[dict, int]:
    for a in doc.assets:
        if a.type == T.GFX_MAP:
            return doc.xfile.assets[a.index].data, a.index
    raise SystemExit("no gfx_map asset")


def _move_render(session: EditSession, bus_index: int, delta) -> None:
    """Shift the bus's render by ``delta``: the clipMap cStaticModel (origin, absmin,
    absmax) and its GfxWorld draw instance (origin) plus the parallel smodel inst bounds,
    so the bus is drawn at the new spot too. Raw node edits (one-off staging)."""
    clip = session._clip_node
    sml = bytearray(clip["static_model_list"])
    o = bus_index * 0x50
    old_origin = struct.unpack_from(">3f", sml, o + 8)
    for off in (8, 0x38, 0x44):  # origin, absmin, absmax
        v = struct.unpack_from(">3f", sml, o + off)
        struct.pack_into(">3f", sml, o + off, *(v[k] + delta[k] for k in range(3)))
    clip["static_model_list"] = bytes(sml)

    gfx, gfx_index = _gfx_node(session.doc)
    di = gfx["smodel_draw_insts"]
    insts = bytearray(gfx["smodel_insts"]) if gfx.get("smodel_insts") else None
    j = None
    for k in range(len(di)):
        raw = di[k]["raw"] if isinstance(di[k], dict) else di[k]
        org = struct.unpack_from(">3f", raw, 4)
        if all(abs(org[t] - old_origin[t]) < 0.5 for t in range(3)):
            j = k
            break
    if j is None:
        raise SystemExit(f"no GfxWorld draw inst matches the bus origin {old_origin}")
    draw = bytearray(di[j]["raw"] if isinstance(di[j], dict) else di[j])
    org = struct.unpack_from(">3f", draw, 4)
    struct.pack_into(">3f", draw, 4, *(org[k] + delta[k] for k in range(3)))
    di[j]["raw"] = bytes(draw)
    if insts is not None:
        io = j * 0x28
        for off in (0, 0xC):  # mins, maxs
            v = struct.unpack_from(">3f", insts, io + off)
            struct.pack_into(">3f", insts, io + off, *(v[k] + delta[k] for k in range(3)))
        gfx["smodel_insts"] = bytes(insts)
    session.doc.touch_asset(gfx_index)


def main() -> int:
    session = EditSession(Document.open(Path(BASE).read_bytes(), name="mp_nuked.ff"))
    cm, loc = session._clip_engine()
    bus = next((p for p in session.static_models() if p.model == BUS_MODEL), None)
    if bus is None:
        raise SystemExit("no school bus static model in the base map")
    cluster = session.clip_cluster_in_footprint(*bus.footprint)
    print(f"bus is static model {bus.index}; clip cluster {cluster}")
    old_centres = {i: tuple((cm.brush(i).mins[k] + cm.brush(i).maxs[k]) / 2 for k in range(3))
                   for i in cluster}

    result = session.move_prop_clip(bus.index, DELTA, footprint=bus.footprint)
    assert result["found"], "no cluster found under the bus footprint"
    for w in result["warnings"]:
        print("warning:", w)
    _move_render(session, bus.index, DELTA)

    # offline confirmation: new spot solid, old spot clear, numBrushes unchanged.
    def solid(idx, p):
        b = cm.brush(idx)
        leaf = loc.locate(p)
        return (leaf is not None and idx in cm.reachable_brushes(leaf)
                and all(b.mins[k] <= p[k] <= b.maxs[k] for k in range(3)))

    new_centres = {i: tuple((cm.brush(i).mins[k] + cm.brush(i).maxs[k]) / 2 for k in range(3))
                   for i in cluster}
    new_solid = all(solid(i, new_centres[i]) for i in cluster)
    old_clear = not any(
        all(cm.brush(i).mins[k] <= old_centres[j][k] <= cm.brush(i).maxs[k] for k in range(3))
        for i in cluster for j in cluster
    )
    print(f"offline: new spot solid={new_solid}  old spot clear={old_clear}  numBrushes={cm.num_brushes}")

    with tempfile.TemporaryDirectory() as d:
        built = Path(d) / "mp_nuked.ff"
        report = session.save(built)
        print(f"save-with-verify: verified={report.verified} problems={report.problems[:1]}")
        if not report.verified:
            raise SystemExit("save did not verify; not staging")
        data = built.read_bytes()

    print("built sha1:", hashlib.sha1(data).hexdigest())
    stage_build.stage(
        data,
        DEST,
        label="q_editor_busmove",
        landmark="central road crossing between the two houses (204,-47): walk where the bus now is",
        marker=f"the school bus moved {int(DELTA[0])} units east onto the central road crossing",
        base=None,  # a clip/prop move keeps the base world on purpose; gated by brush count
        expect_brushes=cm.num_brushes,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
