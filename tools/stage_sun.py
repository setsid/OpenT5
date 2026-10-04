"""Stage an OBVIOUS sun change for a device test (v0.3.0 sun probe).

Opens the pristine retail mp_nuked, swings the sun to a low angle from the opposite
side and warms its colour, saves through the editor's own save-with-verify path
(``Document.set_field`` + ``Document.save``), and stages the result with the mandatory
anti-stale-cache protocol. No new Edit kind is added to the map editor; this drives the
existing Document field editor only.

What it edits and why
---------------------
The runtime sun in mp_nuked is held twice as static zone data, with identical direction
and colour:

  - the ComWorld primary light at ``sunPrimaryLightIndex`` (1): type-1 directional light,
    the sun the engine samples for dynamic-model and player lighting and the sun shadow;
  - the GfxWorld embedded ``sun_light`` (GfxLight): the gfx-side sun used for the sun
    shadow and the sun sprite.

Both are changed together so the scene changes whichever the engine reads. The worldspawn
keys (``sundirection``, ``suncolor``, ``sunlight``) and the baked lightmaps are COMPILE
inputs: the world's baked lighting is already in the lightmap images and will NOT change
from this edit, so static world surfaces keep their original shade. What changes on device
is the real-time sun: the direction and colour of sun shadows cast by players and dynamic
models, and the sun diffuse/specular colour on those dynamic entities and the viewmodel.
No script overrides the sun at runtime (no setSunLight in any .gsc; sun/mp_nuked.sun sets
only the lens-flare sprite), so the edit is not undone by GSC.

Local CPU only. No push, no deploy.

    .venv/bin/python tools/stage_sun.py
    .venv/bin/python tools/stage_sun.py --dest /tmp/sun_check/mp_nuked.ff   # dry destination
"""

from __future__ import annotations

import argparse
import hashlib
import math
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

import stage_build  # noqa: E402
import verify_staged_map as gate  # noqa: E402
from opent5.edit.document import Document  # noqa: E402
from opent5.xfile.constants import AssetType as T  # noqa: E402

BASE = "/mnt/c/Users/bolst/Desktop/opent5-hwtest/nuked/d_pak/mp_nuked.ff"
DEST = "/mnt/c/Users/bolst/Desktop/opent5-hwtest/nuked/r_sun/mp_nuked.ff"

#: The new sun direction, as the direction the light travels (engine convention
#: forward.x=cos(pitch)cos(yaw), forward.y=cos(pitch)sin(yaw), forward.z=-sin(pitch); the
#: retail worldspawn "sundirection -37 221 0" reproduces the stored dir this way). Low
#: pitch puts the sun near the horizon (long shadows); the opposite yaw swings it to the
#: other side.
NEW_PITCH, NEW_YAW = -8.0, 41.0  # retail was pitch -37, yaw 221

#: Warm the sun strongly: keep red bright, drop green and blue, so a glance reads orange.
#: Retail was near-white (14.0, 13.7, 12.5).
WARM_RGB = (14.0, 6.0, 1.5)


def _dir_from_angles(pitch: float, yaw: float) -> list[float]:
    p, y = math.radians(pitch), math.radians(yaw)
    return [
        round(math.cos(p) * math.cos(y), 6),
        round(math.cos(p) * math.sin(y), 6),
        round(-math.sin(p), 6),
    ]


def _warm(old: tuple, rgb: tuple) -> list[float]:
    """``rgb`` for the first three components; keep any remaining component (vec4 w)."""
    return [rgb[0], rgb[1], rgb[2], *list(old)[3:]]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", default=BASE, help="the pristine retail zone to edit")
    ap.add_argument("--dest", default=DEST, help="where to stage the built zone")
    args = ap.parse_args(argv)

    new_dir = _dir_from_angles(NEW_PITCH, NEW_YAW)

    doc = Document.open(Path(args.base).read_bytes(), name="mp_nuked.ff")
    if doc.parse_problems:
        raise SystemExit(f"base does not parse exactly: {doc.parse_problems[0]}")

    gfx = next(a for a in doc.assets if a.type == T.GFX_MAP)
    com = next(a for a in doc.assets if a.type == T.COM_MAP)

    old_dir = doc.field_info(com.index, "primary_lights[1]/dir")["value"]
    old_col = doc.field_info(com.index, "primary_lights[1]/color")["value"]

    # The ComWorld primary sun light (index 1): direction and all three colour fields.
    doc.set_field(com.index, "primary_lights[1]/dir", new_dir)
    doc.set_field(com.index, "primary_lights[1]/color", list(WARM_RGB))
    for f in ("diffuseColor", "specularColor"):
        cur = doc.field_info(com.index, f"primary_lights[1]/{f}")["value"]
        doc.set_field(com.index, f"primary_lights[1]/{f}", _warm(cur, WARM_RGB))

    # The GfxWorld embedded sun light: the same direction, and the diffuse/specular colour
    # the sun casts on dynamic entities.
    doc.set_field(gfx.index, "sun_light/dir", new_dir)
    for f in ("diffuseColor", "specularColor"):
        cur = doc.field_info(gfx.index, f"sun_light/{f}")["value"]
        doc.set_field(gfx.index, f"sun_light/{f}", _warm(cur, WARM_RGB))

    print("sun direction (light travel vector):")
    print(f"  was {tuple(round(x, 4) for x in old_dir)}  ->  now {tuple(new_dir)}")
    print(
        f"  (retail pitch -37 yaw 221  ->  pitch {NEW_PITCH:g} yaw {NEW_YAW:g}: "
        "opposite side, low)"
    )
    print(f"sun colour: was {tuple(round(x, 2) for x in old_col)}  ->  now {WARM_RGB}")

    base_brushes = gate.signature(args.base)["clip"]["brushes"]

    with tempfile.TemporaryDirectory() as d:
        built = Path(d) / "mp_nuked.ff"
        report = doc.save(built, verify=True)
        print(
            f"save-with-verify: verified={report.verified} "
            f"assets_changed={report.assets_changed} problems={report.problems[:1]}"
        )
        if not report.verified:
            raise SystemExit("save did not verify; not staging")
        data = built.read_bytes()

    print("built sha1:", hashlib.sha1(data).hexdigest())
    stage_build.stage(
        data,
        args.dest,
        label="r_sun",
        landmark=(
            "open yard by either house: look at a player's sun shadow and the warm tint "
            "on models"
        ),
        marker="sun low and warm from the opposite side (long shadows the other way)",
        base=None,  # a sun edit keeps the world geometry; the gate-vs-base would false-flag it
        expect_brushes=base_brushes,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
