"""Test maps for the converter: write a sealed box room as a Radiant ``.map`` (iwmap 4) with
spawns, a light and, by default, the objective entities of every MP gametype, and compile it
with the PC Mod Tools into a PC ``.ff`` (docs/research/box-map.md 1, docs/demo-box-modes.md).
``--full`` is the whole test map (docs/demo-box-full.md): every objective, a lightgrid_volume
brush filling the room (docs/demo-box-lit.md), walls in a material no PS3 zone has
(``FULL_WALL``) and eight stock mp_nuked props (``PROPS``, docs/demo-box-models.md).

    .venv/bin/python tools/testmap.py write OUT.map [--half 512] [--height 256] [--no-objectives]
                                     [--full | --wall MAT --light-grid --props]
    .venv/bin/python tools/testmap.py build mp_opent5box_full --game GAME --work WORK -o OUT.ff
                                     [the same options]

``GAME`` is the PC game folder and ``WORK`` a scratch folder, both as Windows paths.

``build`` runs from WSL through ``cmd.exe``: it writes the ``.map`` and three ``.bat`` files
into ``--work`` (a Windows folder outside the game), writes ``zone_source/<name>.csv`` into
the game folder, and runs cod2map, cod2rad (``-fast``) and linker_pc through
``launcher_ldr.exe`` with the command lines ``bin/Launcher.exe`` builds (box-map.md 1.2).
The tools write ``raw/maps/mp/<name>.*``, ``zone/English/<name>.ff`` and the linker's
``zone_source/english/assetinfo|assetlist/<name>*`` files; nothing else in the game folder is
touched, and an existing ``zone_source/<name>.csv`` with other content is refused. The map
name must start with ``mp_`` and must not be a stock map. Not product code.

Coordinates: x, y in [-half, half], z in [0, height]; allies / attackers start on the -x
side, axis / defenders on the +x side.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

LIGHTMAP = "lightmap_gray 16384 16384 0 0 0 0"
FLOOR = "jun_art_concrete_base02"
WALL = "us_art_wall_vinylsiding_white"
CEILING = "pent_art_wall_creampaint02"
#: Worldspawn keys from the PC mp_nuked entity string (box-map.md 1.1), sky box dropped.
WORLDSPAWN = (
    ("maxlightgridcolors", "32000"),
    ("exposure", "1.0"),
    ("sunshadowintensity", "7"),
    ("_sunshadowcolor", "0.709804 0.803922 0.984314"),
    ("sunlight", "14"),
    ("suncolor", "0.996078 0.976471 0.886275"),
    ("ambientintensity", "0.1"),
    ("_ambientcolor", "0.803922 0.815686 0.988235"),
    ("newsun", "1"),
    ("sundirection", "-37 221 0"),
    ("classname", "worldspawn"),
)
#: Face order and the three points of each face (Radiant winding) of an axis-aligned box.
FACES = ("bottom", "top", "ymin", "xmax", "ymax", "xmin")
#: cod2map writes the light grid sample points (``.grid_auto``) from brushes in this
#: material (docs/convert.md 10.3); without one the grid holds only the default colour.
LIGHT_GRID = "lightgrid_volume"
#: A PC Mod Tools material whose colour map (``~-gblockout_average_test_c``, a light and
#: dark grey check with printed values) no PS3 zone holds: the converter builds it into the
#: map's zone (docs/convert.md 11).
FULL_WALL = "blockout_test_concrete"
#: Stock props (all XModels of PS3 mp_nuked): model, (x, y) for half 512, yaw. Two rows
#: along the north and south walls, clear of the spawns and objectives.
PROPS = (
    ("p_glo_sandbag", (-300, 448), 0),
    ("p_us_mailbox", (-150, 448), 90),
    ("mp_nuked_fence", (150, 448), 0),
    ("p_glo_cardboardbox_4", (300, 448), 30),
    ("p_dest_trashcan_metal", (-300, -448), 0),
    ("p_glo_potted_plant_01", (-150, -448), 0),
    ("p_jun_wood_stack", (150, -448), 45),
    ("p_glo_barricade_wood_barb", (300, -448), 90),
)


def _faces(lo, hi) -> dict[str, tuple]:
    x0, y0, z0 = lo
    x1, y1, z1 = hi
    return {
        "bottom": ((x1, y1, z0), (x0, y1, z0), (x0, y0, z0)),
        "top": ((x0, y0, z1), (x0, y1, z1), (x1, y1, z1)),
        "ymin": ((x0, y0, z1), (x1, y0, z1), (x1, y0, z0)),
        "xmax": ((x1, y0, z1), (x1, y1, z1), (x1, y1, z0)),
        "ymax": ((x1, y1, z1), (x0, y1, z1), (x0, y1, z0)),
        "xmin": ((x0, y1, z1), (x0, y0, z1), (x0, y0, z0)),
    }


def brush_lines(lo, hi, materials: dict[str, str] | str, scale: int = 256) -> list[str]:
    """One axis-aligned brush; ``materials`` per face (default ``caulk``) or one for all."""
    faces = _faces(lo, hi)
    out = ["{"]
    for name in FACES:
        points = " ".join("( {:g} {:g} {:g} )".format(*p) for p in faces[name])
        mat = materials if isinstance(materials, str) else materials.get(name, "caulk")
        out.append(f" {points} {mat} {scale} {scale} 0 0 0 0 {LIGHTMAP}")
    out.append("}")
    return out


@dataclass
class Entity:
    keys: dict[str, str]
    #: brushes (lo, hi) in world coordinates
    brushes: list[tuple] = field(default_factory=list)
    #: material on every face of this entity's brushes (``trigger`` for triggers, a solid
    #: like ``clip`` for script_brushmodel collision)
    brush_material: str = "trigger"


def _v(*xs) -> str:
    return " ".join(f"{x:g}" for x in xs)


def trigger(classname: str, centre, size, height, **keys) -> Entity:
    """A brush trigger: a box ``size`` x ``size`` x ``height`` standing on ``centre``."""
    x, y, z = centre
    h = size / 2
    lo, hi = (x - h, y - h, z), (x + h, y + h, z + height)
    return Entity({"classname": classname, **keys}, [(lo, hi)])


def solid_brush(classname: str, centre, size, height, material: str = "clip", **keys) -> Entity:
    """A solid brush entity (e.g. ``script_brushmodel``): a box ``size`` x ``size`` x
    ``height`` on ``centre``, every face ``material`` (``clip`` is invisible player
    collision). cod2map writes it as a clipMap submodel ``"model" "*N"`` like a trigger."""
    x, y, z = centre
    h = size / 2
    lo, hi = (x - h, y - h, z), (x + h, y + h, z + height)
    return Entity({"classname": classname, **keys}, [(lo, hi)], brush_material=material)


def point(classname: str, origin, yaw: float | None = None, **keys) -> Entity:
    e = {"classname": classname, "origin": _v(*origin)}
    if yaw is not None:
        e["angles"] = _v(0, yaw, 0)
    e.update(keys)
    return Entity(e)


def radius_trigger(classname: str, centre, radius: float, height: float, **keys) -> Entity:
    """A radius trigger: a point entity with ``radius`` and ``height`` keys and no brush
    (convert.md 9.1: no ``model "*N"`` is written, so it spawns without a clipMap submodel).
    ``classname`` with ``use`` is hold-to-use (_gameobjects.gsc createUseObject 681)."""
    x, y, z = centre
    return point(classname, (x, y, z), 0, radius=f"{radius:g}", height=f"{height:g}", **keys)


def room_brushes(
    half: int, height: int, wall: int = 16, material: str = WALL, light_grid: bool = False
) -> list[list[str]]:
    r, h, t = half, height, wall
    out = [
        brush_lines((-r - t, -r - t, -t), (r + t, r + t, 0), {"top": FLOOR}),
        brush_lines((-r - t, -r - t, h), (r + t, r + t, h + t), {"bottom": CEILING}),
        brush_lines((-r - t, -r, 0), (-r, r, h), {"xmax": material}),
        brush_lines((r, -r, 0), (r + t, r, h), {"xmin": material}),
        brush_lines((-r - t, -r - t, 0), (r + t, -r, h), {"ymax": material}),
        brush_lines((-r - t, r, 0), (r + t, r + t, h), {"ymin": material}),
    ]
    if light_grid:
        # 8 units inside each wall, as the grid box of docs/demo-box-lit.md.
        out.append(brush_lines((-r + 8, -r + 8, 8), (r - 8, r - 8, h - 8), LIGHT_GRID, 64))
    return out


#: Half-extents (hx, hy, hz) of a ``clip`` box per prop, keyed by model. cod2map turns a
#: ``misc_model`` into a draw-only static model with no collision (docs/convert.md 12), so a
#: stock map clips its props with brushes compiled into the BSP; the box does the same, a
#: worldspawn ``clip`` box around each prop (demo-box-models.md).
PROP_CLIP = {
    "p_glo_sandbag": (26, 26, 20),
    "p_us_mailbox": (14, 14, 28),
    "mp_nuked_fence": (64, 6, 40),
    "p_glo_cardboardbox_4": (18, 18, 20),
    "p_dest_trashcan_metal": (14, 14, 26),
    "p_glo_potted_plant_01": (14, 14, 28),
    "p_jun_wood_stack": (28, 20, 20),
    "p_glo_barricade_wood_barb": (34, 10, 26),
}


def prop_entities(half: int) -> list[Entity]:
    s = half / 512
    return [
        Entity(
            {
                "classname": "misc_model",
                "model": model,
                "origin": _v(x * s, y * s, 0),
                "angles": _v(0, yaw, 0),
                "modelscale": "1",
            }
        )
        for model, (x, y), yaw in PROPS
    ]


def prop_clip_brushes(half: int) -> list[list[str]]:
    """A worldspawn ``clip`` box around each prop so cod2map gives it collision."""
    s = half / 512
    out = []
    for model, (x, y), _yaw in PROPS:
        hx, hy, hz = PROP_CLIP.get(model, (24, 24, 40))
        cx, cy = x * s, y * s
        out.append(brush_lines((cx - hx, cy - hy, 0), (cx + hx, cy + hy, hz), "clip", 64))
    return out


#: Start spawn classes per side (west: allies / attackers, east: axis / defenders).
START_WEST = (
    "mp_tdm_spawn_allies_start",
    "mp_dom_spawn_allies_start",
    "mp_ctf_spawn_allies_start",
    "mp_sab_spawn_allies_start",
    "mp_dem_spawn_attacker_start",
    "mp_sd_spawn_attacker",
)
START_EAST = (
    "mp_tdm_spawn_axis_start",
    "mp_dom_spawn_axis_start",
    "mp_ctf_spawn_axis_start",
    "mp_sab_spawn_axis_start",
    "mp_dem_spawn_defender_start",
    "mp_sd_spawn_defender",
)
#: Team respawn classes per side.
TEAM_WEST = ("mp_ctf_spawn_allies", "mp_sab_spawn_allies", "mp_dem_spawn_attacker")
TEAM_EAST = ("mp_ctf_spawn_axis", "mp_sab_spawn_axis", "mp_dem_spawn_defender")
#: Spawns any team uses (tdm and koth: mp_tdm_spawn; dm and the wager modes: mp_dm_spawn;
#: dom: mp_dom_spawn).
NEUTRAL = ("mp_tdm_spawn", "mp_dm_spawn", "mp_dom_spawn")


def spawn_entities(half: int) -> list[Entity]:
    s = half / 512
    out = [
        point("info_player_start", (0, 0, 8), 0),
        point("mp_global_intermission", (0, -400 * s, 200), None, angles="20 90 0"),
    ]
    for side, starts, teams, yaw in (
        (-1, START_WEST, TEAM_WEST, 0),
        (1, START_EAST, TEAM_EAST, 180),
    ):
        for cls in starts:
            for y in (-192, -96, 96, 192):
                out.append(point(cls, (side * 384 * s, y * s, 8), yaw))
        for cls in teams:
            for y in (-384, -256, 256, 384):
                out.append(point(cls, (side * 448 * s, y * s, 8), yaw))
    for x, y, yaw in (
        (-160, -160, 45),
        (160, 160, 225),
        (-160, 160, 315),
        (160, -160, 135),
        (0, -448, 90),
        (0, 448, 270),
    ):
        for cls in NEUTRAL:
            out.append(point(cls, (x * s, y * s, 8), yaw))
    for label, x in (("a", -288), ("b", 0), ("c", 288)):
        out.append(point(f"mp_dom_spawn_flag_{label}", (x * s, 96 * s, 8), 270))
    return out


def objective_entities(half: int, sd_radius: bool = False) -> list[Entity]:
    """Every gametype's objectives (docs/demo-box-modes.md has the plan; the requirements
    with evidence are in opent5.convert.entities). Positions overlap only between modes
    that never run together: _gameobjects::main deletes what a mode does not allow.
    ``sd_radius``: emit the SD / bombzone objective triggers as radius triggers (no brush
    ``"*N"`` model) instead of brush triggers, to tell a brush-model spawn fault from a
    client draw fault (docs/research/box-objectives-cd.md 5, fixC)."""
    s = half / 512
    out: list[Entity] = []

    def at(x, y, z=0):
        return (x * s, y * s, z)

    # fixC: the radius classname per brush trigger class (a "use" trigger stays hold-to-use).
    radius_class = {"trigger_use_touch": "trigger_radius_use", "trigger_multiple": "trigger_radius"}

    def obj_trigger(classname, centre, size, height, **keys):
        if sd_radius:
            return radius_trigger(radius_class[classname], centre, size / 2, height, **keys)
        return trigger(classname, centre, size, height, **keys)

    # sd and dem: two bomb sites on the defenders' (east) side, each the exact five-entity
    # set stock mp_nuked carries (its own map_ents, read with `opent5 extract`): a
    # trigger_use_touch plant trigger (targetname "bombzone", script_label, targeting the
    # bomb), a second trigger_use_touch (the defuse trigger, the "_auto2" name), the bomb
    # script_model chaining the two by target, and two solid script_brushmodels. All tagged
    # "bombzone" (allowed by sd and dem). The plant/defuse triggers are brush models so that
    # _gameobjects::createUseObject makes a hold-to-use plant prompt (radius triggers show
    # the objective but give no prompt; docs/research/box-objectives-cd.md, fixC).
    for label, y, exploder in (("a", -320, 7121), ("b", 320, 7152)):
        c = at(288, y)
        out.append(
            obj_trigger(
                "trigger_use_touch",
                c,
                96,
                96,
                targetname="bombzone",
                script_gameobjectname="bombzone",
                target=f"bombzone_{label}_auto1",
                script_bombmode_original="1",
                script_label=f"_{label}",
            )
        )
        out.append(
            obj_trigger(
                "trigger_use_touch",
                c,
                96,
                96,
                targetname=f"bombzone_{label}_auto2",
                script_gameobjectname="bombzone",
            )
        )
        out.append(
            point(
                "script_model",
                (c[0], c[1], 2),
                90,
                model="p_glo_bomb_stack",
                targetname=f"bombzone_{label}_auto1",
                target=f"bombzone_{label}_auto2",
                script_gameobjectname="bombzone",
                script_exploder=str(exploder),
                spawnflags="5",
            )
        )
        # the two solid brush models: the physical bomb-site object (collision).
        out.append(
            solid_brush(
                "script_brushmodel", c, 56, 14, script_gameobjectname="bombzone", spawnflags="1"
            )
        )
        out.append(
            solid_brush(
                "script_brushmodel",
                (c[0], c[1], 14),
                56,
                14,
                script_gameobjectname="bombzone",
                spawnflags="1",
            )
        )
    # sd: the bomb on the attackers' side.
    c = at(-288, 0)
    out.append(
        obj_trigger(
            "trigger_multiple",
            c,
            48,
            48,
            targetname="sd_bomb_pickup_trig",
            script_gameobjectname="sd",
        )
    )
    out.append(
        point(
            "script_model",
            (c[0], c[1], 2),
            270,
            model="prop_suitcase_bomb",
            targetname="sd_bomb",
            script_gameobjectname="sd",
            spawnflags="4",
        )
    )
    # sab: the bomb in the middle, one target per team on its own side.
    c = at(0, 0)
    out.append(
        trigger(
            "trigger_multiple",
            c,
            48,
            48,
            targetname="sab_bomb_pickup_trig",
            script_gameobjectname="sab",
        )
    )
    out.append(
        point(
            "script_model",
            (c[0], c[1], 2),
            0,
            model="prop_suitcase_bomb",
            targetname="sab_bomb",
            script_gameobjectname="sab",
            spawnflags="4",
        )
    )
    for team, x in (("allies", -288), ("axis", 288)):
        c = at(x, 0)
        out.append(
            trigger(
                "trigger_use_touch",
                c,
                96,
                96,
                targetname=f"sab_bomb_{team}",
                target=f"sab_bomb_{team}_visual",
                script_gameobjectname="sab",
            )
        )
        out.append(
            point(
                "script_model",
                c,
                90,
                model="p_glo_bomb_stack",
                targetname=f"sab_bomb_{team}_visual",
                script_gameobjectname="sab",
                spawnflags="5",
            )
        )
    # ctf: a flag (pickup trigger, zone trigger, model) behind each team's start.
    for team, x in (("allies", -448), ("axis", 448)):
        c = at(x, 0)
        out.append(
            trigger(
                "trigger_multiple",
                c,
                64,
                80,
                targetname="ctf_flag_pickup_trig",
                target=f"ctf_flag_{team}",
                script_team=team,
                script_gameobjectname="ctf",
            )
        )
        out.append(
            trigger(
                "trigger_multiple",
                c,
                96,
                80,
                targetname="ctf_flag_zone_trig",
                script_team=team,
                script_gameobjectname="ctf",
            )
        )
        out.append(
            point(
                "script_model",
                c,
                0,
                model="mp_flag_neutral",
                targetname=f"ctf_flag_{team}",
                script_team=team,
                script_gameobjectname="ctf",
                spawnflags="4",
            )
        )
    # dom: three radius flags in a line, a descriptor above each, linked A-B-C.
    links = {"a": "flag_b", "b": "flag_a flag_c", "c": "flag_b"}
    for label, x in (("a", -288), ("b", 0), ("c", 288)):
        c = at(x, 0)
        out.append(
            point(
                "trigger_radius",
                c,
                0,
                targetname="flag_primary",
                script_label=f"_{label}",
                radius=f"{96 * s:g}",
                height="128",
                script_gameobjectname="dom",
            )
        )
        out.append(
            point(
                "script_origin",
                (c[0], c[1], 64),
                None,
                targetname="flag_descriptor",
                script_linkname=f"flag_{label}",
                script_linkto=links[label],
                script_gameobjectname="dom",
            )
        )
    # koth (Headquarters): two radios, each inside one radiotrigger, each with a crate.
    for i, y in enumerate((-320, 320), 1):
        c = at(0, y)
        out.append(
            trigger(
                "trigger_multiple",
                c,
                160,
                128,
                targetname="radiotrigger",
                script_gameobjectname="hq",
            )
        )
        out.append(
            point(
                "script_model",
                (c[0], c[1], 16),
                None,
                angles="0 0 90",
                model="t5_weapon_briefcase_bomb_world",
                targetname="hq_hardpoint",
                target=f"hq_{i}_crate",
                script_gameobjectname="hq",
            )
        )
        out.append(
            point(
                "script_model",
                c,
                0,
                model="mp_supplydrop_hq",
                targetname=f"hq_{i}_crate",
                script_gameobjectname="hq",
            )
        )
    return out


def path_node_entities(half: int, cells: int = 5) -> list[Entity]:
    """A coarse ``node_pathnode`` grid over the floor (``cells`` x ``cells``): the AI /
    MP spawn-influence connectivity a converted map lacks (game_map_mp nodeCount 0,
    docs/convert.md 3.6; docs/research/box-objectives-cd.md 4, fixD). cod2map auto-links
    nodes in range (no DONT_LINK spawnflag) and writes them to the GameWorldMp PathData,
    which the converter carries (game_map_mp, proven on PC mp_nuked's 316 nodes). Placed a
    little above the floor; cod2map drops each to the ground."""
    s = half / 512
    span = half - 48  # inside the walls
    step = 2 * span / (cells - 1)
    out = []
    for r in range(cells):
        for c in range(cells):
            x = (-span + c * step) * s
            y = (-span + r * step) * s
            out.append(Entity({"classname": "node_pathnode", "origin": _v(x, y, 16)}))
    return out


def map_text(
    half: int = 512,
    height: int = 256,
    objectives: bool = True,
    wall: str = WALL,
    light_grid: bool = False,
    props: bool = False,
    sd_radius: bool = False,
    path_nodes: bool = True,
) -> str:
    """The whole ``.map`` (CRLF line ends are added by ``write_map``)."""
    lines = ["iwmap 4", '"000_Global" flags  active', '"The Map" flags ', "// entity 0", "{"]
    lines += [f'"{k}" "{v}"' for k, v in WORLDSPAWN]
    world_brushes = list(room_brushes(half, height, material=wall, light_grid=light_grid))
    if props:
        world_brushes += prop_clip_brushes(half)
    for i, b in enumerate(world_brushes):
        lines.append(f"// brush {i}")
        lines += b
    lines.append("}")
    entities = [
        Entity(
            {
                "classname": "light",
                # PRIMARY_OMNI (PC tools bin/codbo.def 42-49; docs/research/
                # box-rpcs3-issues.md 2.1). cod2map ignores a primary light without a
                # target ("ignoring primary light without a 'target' key") and one whose
                # fov_outer exceeds 120 unless it has no shadow map.
                "spawnflags": "1",
                "origin": _v(0, 0, height - 56),
                "radius": "1200",
                "intensity": "1.5",
                "_color": "1 1 1",
                "target": "room_light_target",
                "fov_outer": "110",
            }
        ),
        Entity({"classname": "info_null", "origin": "0 0 0", "targetname": "room_light_target"}),
        *spawn_entities(half),
    ]
    if objectives:
        entities += objective_entities(half, sd_radius=sd_radius)
    if props:
        entities += prop_entities(half)
    if path_nodes:
        entities += path_node_entities(half)
    for i, e in enumerate(entities, 1):
        lines.append(f"// entity {i}")
        lines.append("{")
        lines += [f'"{k}" "{v}"' for k, v in e.keys.items()]
        for j, (lo, hi) in enumerate(e.brushes):
            lines.append(f"// brush {j}")
            lines += brush_lines(lo, hi, e.brush_material, 64)
        lines.append("}")
    return "\n".join(lines) + "\n"


def entity_dicts(
    half: int = 512, height: int = 256, objectives: bool = True, sd_radius: bool = False, **_
) -> list[dict]:
    """The point and brush entities as written to the ``.map`` (no worldspawn, no light, no
    props: cod2map turns ``misc_model`` into static models; path nodes become PathData)."""
    out = [dict(e.keys) for e in spawn_entities(half)]
    if objectives:
        out += [dict(e.keys) for e in objective_entities(half, sd_radius=sd_radius)]
    return out


def compiled_entities(
    half: int = 512, height: int = 256, objectives: bool = True, sd_radius: bool = False, **_
) -> tuple[list[dict], list[tuple]]:
    """The entity string and brush model bounds cod2map makes of ``map_text`` (read from
    the compiled box, tests/test_convert_entities.py): the worldspawn, then the entities in
    order without the light; a brush entity gains ``origin`` (its brushes' centre) and
    ``model "*N"`` (N from 1 in order), and brush model N spans the brush relative to that
    origin, widened by 1. Model 0 is the world: the room's outer faces, widened by 1.
    ``misc_model`` props leave the entity string (they become static models).
    cod2rad also adds a ``gndLt`` key (a ground light colour) to some script_models; that
    key is not modelled here."""
    world = dict(WORLDSPAWN)
    r, t = half + 16 + 1, 16 + 1
    cmodels: list[tuple] = [((-r, -r, -t), (r, r, height + t))]
    out = [world]
    objs = objective_entities(half, sd_radius=sd_radius) if objectives else []
    entities = spawn_entities(half) + objs
    for e in entities:
        keys = dict(e.keys)
        if e.brushes:
            lo, hi = e.brushes[0]
            centre = tuple((lo[i] + hi[i]) / 2 for i in range(3))
            keys["origin"] = _v(*centre)
            keys["model"] = f"*{len(cmodels)}"
            half_size = tuple((hi[i] - lo[i]) / 2 + 1 for i in range(3))
            cmodels.append((tuple(-h for h in half_size), half_size))
        out.append(keys)
    return out, cmodels


def write_map(path: Path, **kw) -> Path:
    path.write_text(map_text(**kw), newline="\r\n")
    return path


# -- compiling ------------------------------------------------------------------------------

STOCK_MAPS = re.compile(
    r"mp_(array|cairo|cosmodrome|cracked|crisis|duga|firingrange|hanoi"
    r"|havoc|mountain|nuked|radiation|russianbase|villa|area51|berlinwall2"
    r"|discovery|kowloon|stadium|gridlock|hotel|outskirts|zoo|drivein"
    r"|golfcourse|silo)$"
)


#: Where the PC tools write a map's files (box-map.md 7).
GAME_DIRS = (
    "raw/maps/mp",
    "zone/English",
    "zone_source",
    "zone_source/english/assetinfo",
    "zone_source/english/assetlist",
)


def zone_csv(name: str) -> str:
    """The linker's zone list: box-map.md 1.1 (mp_nuked's list without the map's assets)."""
    return (
        "ignore,code_post_gfx_mp\n"
        "ignore,common_mp\n"
        "include_console,mp_configStrings\n"
        ",\n"
        f"col_map_mp,maps/mp/{name}.d3dbsp\n"
        f"gfx_map,maps/mp/{name}.d3dbsp\n"
    )


def _wsl(win: str) -> Path:
    out = subprocess.run(["wslpath", "-u", win], capture_output=True, text=True, check=True)
    return Path(out.stdout.strip())


def _bat(game: str, line: str) -> str:
    return f'@echo off\r\ncd /d "{game}\\bin"\r\n{line}\r\necho EXITCODE %ERRORLEVEL%\r\n'


def build(name: str, game: str, work: str, out: Path, **kw) -> dict:
    if not re.fullmatch(r"mp_[a-z0-9_]{1,20}", name) or STOCK_MAPS.match(name):
        raise SystemExit(f"map name: expected mp_ and lower case, not a stock map; found {name!r}")
    game_dir, work_dir = _wsl(game), _wsl(work)
    if not (game_dir / "bin" / "launcher_ldr.exe").is_file():
        raise SystemExit(f"{game}: expected bin\\launcher_ldr.exe (the PC Mod Tools), found none")
    work_dir.mkdir(parents=True, exist_ok=True)
    csv = game_dir / "zone_source" / f"{name}.csv"
    text = zone_csv(name)
    if csv.exists() and csv.read_text() != text:
        raise SystemExit(f"{csv}: exists with other content; not overwritten")
    map_path = write_map(work_dir / f"{name}.map", **kw)
    if not csv.exists():
        csv.write_text(text)
    raw = f"{game}\\raw\\maps\\mp\\{name}"
    steps = (
        (
            "cod2map",
            f"launcher_ldr.exe cod2map.dll cod2map.exe -platform pc -loadFrom "
            f'"{work}\\{name}.map" "{raw}"',
        ),
        ("cod2rad", f'launcher_ldr.exe cod2rad.dll cod2rad.exe -platform pc -fast "{raw}"'),
        (
            "linker",
            f"launcher_ldr.exe linker_pc.dll linker_pc.exe -nopause -language english {name}",
        ),
    )
    report: dict = {"map": str(map_path), "zone_csv": str(csv), "steps": {}}
    for step, line in steps:
        bat = work_dir / f"{name}_{step}.bat"
        bat.write_text(_bat(game, line), newline="")
        run = subprocess.run(
            ["cmd.exe", "/c", f"{work}\\{name}_{step}.bat"],
            capture_output=True,
            text=True,
            timeout=1800,
            cwd=work_dir,
        )
        code = re.search(r"EXITCODE (-?\d+)", run.stdout)
        report["steps"][step] = int(code.group(1)) if code else None
        if not code or code.group(1) != "0":
            raise SystemExit(f"{step}: expected exit 0, found {run.stdout[-400:]!r}")
    ff = game_dir / "zone" / "English" / f"{name}.ff"
    out.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ff, out)
    data = out.read_bytes()
    report["ff"] = {"path": str(out), "bytes": len(data), "sha1": hashlib.sha1(data).hexdigest()}
    report["game_files"] = sorted(
        str(p.relative_to(game_dir))
        for d in GAME_DIRS
        for p in (game_dir / d).glob(f"{name}*")
        if p.is_file()
    )
    return report


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    for cmd in ("write", "build"):
        c = sub.add_parser(cmd)
        if cmd == "write":
            c.add_argument("out")
        else:
            c.add_argument("name")
            c.add_argument("--game", required=True, help="the PC game folder (Windows path)")
            c.add_argument("--work", required=True, help="a Windows folder for the .map and .bat")
            c.add_argument("-o", "--out", required=True)
        c.add_argument("--half", type=int, default=512)
        c.add_argument("--height", type=int, default=256)
        c.add_argument("--no-objectives", action="store_true")
        c.add_argument("--wall", default=WALL, help=f"wall material (default {WALL})")
        c.add_argument("--light-grid", action="store_true", help="add a lightgrid_volume brush")
        c.add_argument("--props", action="store_true", help="add the eight stock props")
        c.add_argument(
            "--full",
            action="store_true",
            help=f"the full test map: objectives, light grid, {FULL_WALL} walls, props",
        )
        c.add_argument(
            "--sd-radius",
            action="store_true",
            help="SD / bombzone objective triggers as radius triggers (fixC, box-objectives-cd.md)",
        )
        c.add_argument(
            "--no-path-nodes",
            action="store_true",
            help="omit the node_pathnode grid (on by default: fixD, box-objectives-cd.md)",
        )
    args = p.parse_args(argv)
    kw = {
        "half": args.half,
        "height": args.height,
        "objectives": args.full or not args.no_objectives,
        "wall": FULL_WALL if args.full else args.wall,
        "light_grid": args.full or args.light_grid,
        "props": args.full or args.props,
        "sd_radius": args.sd_radius,
        "path_nodes": not args.no_path_nodes,
    }
    if args.cmd == "write":
        write_map(Path(args.out), **kw)
        print(json.dumps({"map": args.out, "entities": len(entity_dicts(**kw))}))
    else:
        print(json.dumps(build(args.name, args.game, args.work, Path(args.out), **kw), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
