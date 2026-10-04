"""Build two box maps identical but for one extra worldspawn clip brush, compile
both with the real PC Mod Tools (cod2map, cod2rad, linker_pc), and report the
resulting .ff paths. Scratch for the collision compiler-diff R&D; not product code.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

import testmap  # noqa: E402

GAME = r"C:\Program Files (x86)\Steam\steamapps\common\Call of Duty Black Ops"
WORK = r"C:\o5\clipdiff"

# A small axial clip box in the middle of the room floor (contents = player-clip),
# the single difference between the two maps.
CLIP_LO = (176, 176, 0)
CLIP_HI = (224, 224, 40)


def base_map_text() -> str:
    return testmap.map_text(objectives=False, path_nodes=False, props=False)


def inject_clip(text: str) -> str:
    """Insert one clip brush box into the worldspawn (entity 0, the first brace
    block). The worldspawn ends at the first line that is exactly '}'."""
    lines = text.split("\n")
    # worldspawn starts at the first '{'; its matching close is the first standalone '}'.
    close = next(i for i, ln in enumerate(lines) if ln == "}")
    brush = ["// brush CLIP"] + testmap.brush_lines(CLIP_LO, CLIP_HI, "clip", 64)
    out = lines[:close] + brush + lines[close:]
    return "\n".join(out)


def write_map(path: Path, text: str) -> None:
    path.write_text(text, newline="\r\n")


def compile_map(name: str, work_dir: Path) -> dict:
    game_dir = Path(subprocess.run(["wslpath", "-u", GAME], capture_output=True, text=True,
                                   check=True).stdout.strip())
    csv = game_dir / "zone_source" / f"{name}.csv"
    csv.write_text(testmap.zone_csv(name))
    raw = f"{GAME}\\raw\\maps\\mp\\{name}"
    steps = (
        ("cod2map", f'launcher_ldr.exe cod2map.dll cod2map.exe -platform pc -loadFrom '
                    f'"{WORK}\\{name}.map" "{raw}"'),
        ("cod2rad", f'launcher_ldr.exe cod2rad.dll cod2rad.exe -platform pc -fast "{raw}"'),
        ("linker", f'launcher_ldr.exe linker_pc.dll linker_pc.exe -nopause -language english {name}'),
    )
    report = {"steps": {}}
    for step, line in steps:
        bat = work_dir / f"{name}_{step}.bat"
        bat.write_text(testmap._bat(GAME, line), newline="")
        run = subprocess.run(["cmd.exe", "/c", f"{WORK}\\{name}_{step}.bat"],
                             capture_output=True, text=True, timeout=1800, cwd=work_dir)
        m = re.search(r"EXITCODE (-?\d+)", run.stdout)
        report["steps"][step] = int(m.group(1)) if m else None
        if not m or m.group(1) != "0":
            report["fail"] = {"step": step, "tail": run.stdout[-600:], "err": run.stderr[-300:]}
            return report
    ff = game_dir / "zone" / "English" / f"{name}.ff"
    data = ff.read_bytes()
    report["ff"] = str(ff)
    report["bytes"] = len(data)
    report["sha1"] = hashlib.sha1(data).hexdigest()
    return report


def main() -> int:
    work_dir = Path(subprocess.run(["wslpath", "-u", WORK], capture_output=True, text=True,
                                   check=True).stdout.strip())
    work_dir.mkdir(parents=True, exist_ok=True)
    base = base_map_text()
    clip = inject_clip(base)
    write_map(work_dir / "mp_opent5cda.map", base)
    write_map(work_dir / "mp_opent5cdb.map", clip)
    import json
    rep = {}
    import os
    if not os.environ.get("ONLY_B"):
        rep["a"] = compile_map("mp_opent5cda", work_dir)
    rep["b"] = compile_map("mp_opent5cdb", work_dir)
    print(json.dumps(rep, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
