"""Map scripts for a converted map, and the entity checks the gametypes need.

The target zone's own map scripts expect its own entities. Stock mp_nuked
(``maps/mp/mp_nuked.gsc``) runs the mannequin, doomsday clock, population sign and
end-game bomb threads, each starting with ``GetEnt`` / ``GetStruct`` on an entity the box
does not have and using the result unchecked; ``maps/mp/createfx/mp_nuked_fx.gsc`` and
``clientscripts/mp/createfx/mp_nuked_fx.csc`` place 3.4 KB of one-shot effects at Nuketown
positions. ``minimal_scripts`` keeps, from the stock ``main()``, only the calls a minimal
MP map makes (the map's _fx and _amb main, ``maps\\mp\\_load::main()``, the compass and
teamset setup, dvars and strings) and empties the createfx files; every other map script is
left as it is (they only define functions or check what they find; see docs/convert.md for
the reading of each).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from opent5.convert.world import ConvertError

#: Statements of the stock main() that a minimal map keeps.
KEEP = (
    r"maps\\mp\\{base}_fx::main\(\);",
    r"maps\\mp\\_load::main\(\);",
    r"maps\\mp\\{base}_amb::main\(\);",
    r"maps\\mp\\_compass::setupMiniMap\(.*\);",
    r"maps\\mp\\gametypes\\_teamset_\w+::level_init\(\);",
    r"maps\\mp\\gametypes\\_spawning::level_use_unified_spawning\(.*\);",
    r"(?i)setdvar\s*\(.*\);",
    r'game\["strings(_menu)?"\]\[".*"\]\s*=.*;',
)
EMPTY_MAIN = "main()\n{\n}\n"
#: The base's art script tunes the light grid for its own map (mp_nuked
#: ``createart/mp_nuked_art.gsc`` 43-45: r_lightGridEnableTweaks 1, r_lightGridIntensity
#: 1.25, r_lightGridContrast .18); a converted map has its own grid (docs/research/
#: box-rpcs3-issues.md 2.2), so those lines are dropped.
LIGHT_GRID_DVAR = re.compile(r'(?i)\s*setdvar\s*\(\s*"r_lightGrid\w*"\s*,.*\);\s*')
#: Added to a map's own main(): the compass letter grid and the compass turning with the
#: player are engine dvars defaulting on (t5mp.elf: compassGridEnabled registered at
#: 0xdbfc4, compassRotation at 0xdb8dc; box-rpcs3-issues.md 3). INFERRED until a device run
#: that a level script's setdvar reaches the client's compass.
OWN_MAP_DVARS = ('setDvar("compassGridEnabled", 0);', 'setDvar("compassRotation", 0);')


def _function_body(text: str, name: str) -> tuple[int, int] | None:
    """(start, end) line indices of ``name()``'s body lines (between its braces)."""
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if re.fullmatch(rf"{re.escape(name)}\s*\(\s*\)\s*", line.strip()):
            j = i + 1
            if j >= len(lines) or lines[j].strip() != "{":
                continue
            depth = 0
            for k in range(j, len(lines)):
                depth += lines[k].count("{") - lines[k].count("}")
                if depth == 0:
                    return j + 1, k
    return None


def minimal_main(text: str, base: str) -> tuple[str, list[str], list[str]]:
    """The map's main script with only the kept statements of ``main()`` and nothing
    else but its #include lines. Returns (text, kept statements, dropped statements)."""
    span = _function_body(text, "main")
    if span is None:
        raise ConvertError(f"maps/mp/{base}.gsc: expected a main() function, found none")
    lines = text.split("\n")
    patterns = [re.compile(p.format(base=re.escape(base))) for p in KEEP]
    kept, dropped = [], []
    for line in lines[span[0] : span[1]]:
        s = line.strip()
        if not s:
            continue
        (kept if any(p.fullmatch(s) for p in patterns) else dropped).append(s)
    if "maps\\mp\\_load::main();" not in kept:
        raise ConvertError(f"maps/mp/{base}.gsc: main() does not call maps\\mp\\_load::main()")
    includes = [ln for ln in lines if ln.startswith("#include")]
    out = "\n".join([*includes, "main()", "{", *kept, "}", ""])
    return out, kept, dropped


@dataclass
class ScriptPlan:
    #: rawfile name -> new text
    texts: dict[str, str] = field(default_factory=dict)
    kept: list[str] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)


def plan_scripts(base: str, texts: dict[str, str], own_map: bool = False) -> ScriptPlan:
    """``texts``: every rawfile script of the target zone by name. Refuses when another
    script calls a function of the main map script that the minimal one drops.
    ``own_map``: the map has its own name, so its main() runs (not patch_mp's) and gets
    ``OWN_MAP_DVARS``."""
    plan = ScriptPlan()
    main_name = f"maps/mp/{base}.gsc"
    if main_name not in texts:
        raise ConvertError(f"target zone: expected a rawfile {main_name}, found none")
    new, plan.kept, plan.dropped = minimal_main(texts[main_name], base)
    if own_map:
        new = new.replace("\n}\n", "\n" + "\n".join(OWN_MAP_DVARS) + "\n}\n", 1)
        plan.kept += list(OWN_MAP_DVARS)
    plan.texts[main_name] = new
    for name in (f"maps/mp/createfx/{base}_fx.gsc", f"clientscripts/mp/createfx/{base}_fx.csc"):
        if name in texts:
            plan.texts[name] = EMPTY_MAIN
    art = f"maps/mp/createart/{base}_art.gsc"
    if art in texts:
        lines = texts[art].split("\n")
        kept = [ln for ln in lines if not LIGHT_GRID_DVAR.fullmatch(ln)]
        if len(kept) != len(lines):
            plan.texts[art] = "\n".join(kept)
    defined = set(re.findall(r"^(\w+)\s*\(", new, re.M))
    ref = re.compile(rf"maps\\mp\\{re.escape(base)}::(\w+)", re.I)
    for name, text in texts.items():
        if name == main_name:
            continue
        for fn in ref.findall(text):
            if fn not in defined:
                raise ConvertError(
                    f"{name} calls maps\\mp\\{base}::{fn}, which the minimal {main_name} drops"
                )
    return plan


# -- entities -------------------------------------------------------------------------------

#: Entities the base map's *stock* script uses without checking, placed for the converted
#: map. The installed update's patch_mp.ff carries its own ``maps/mp/mp_nuked.gsc`` (and
#: ``clientscripts/mp/mp_nuked_fx.csc``): the stock script plus a spawn fix (it moves an
#: mp_dom_spawn and adds two collision walls at Nuketown positions), which only works if the
#: patch's copy is the one the game runs; so with the update installed the minimal map
#: script is INFERRED not to run. These entities let the stock script run without a script
#: error in a map that is not Nuketown: the doomsday clock hands and population sign
#: counters (rotated by script, unchecked GetEnt), and the end-game camera structs and bomb
#: (nuked_bomb_drop_think: unchecked GetStruct / GetEnt). The decorative models (clock hands
#: and sign counters) use ``tag_origin``, not their Nuketown xmodels: the patch script still
#: finds and rotates the entities (unchecked GetEnt), but nothing renders, so they do not
#: float as Nuketown scenery once the converted map has a visible sky. Positions: above the
#: play area; the camera structs inside it. ``{z}`` is the ceiling height plus 256.
COMPAT_ENTITIES = {
    "mp_nuked": (
        {
            "classname": "script_model",
            "targetname": "clock_min_hand",
            "model": "tag_origin",
            "origin": "-256 0 {z}",
            "angles": "0 0 0",
        },
        {
            "classname": "script_model",
            "targetname": "clock_sec_hand",
            "model": "tag_origin",
            "origin": "-256 0 {z}",
            "angles": "0 0 0",
        },
        {
            "classname": "script_model",
            "targetname": "counter_ones",
            "model": "tag_origin",
            "origin": "256 0 {z}",
            "angles": "0 0 0",
        },
        {
            "classname": "script_model",
            "targetname": "counter_tens",
            "model": "tag_origin",
            "origin": "248 0 {z}",
            "angles": "0 0 0",
        },
        {
            "classname": "script_model",
            "targetname": "nuked_bomb",
            "model": "tag_origin",
            "origin": "0 0 {bomb}",
        },
        {
            "classname": "script_struct",
            "targetname": "endgame_camera_start",
            "target": "endgame_camera_end",
            "origin": "{cx} {cy0} {cz}",
            "angles": "0 90 0",
        },
        {
            "classname": "script_struct",
            "targetname": "endgame_camera_end",
            "origin": "{cx} {cy1} {cz}",
            "angles": "0 90 0",
        },
    ),
}


def compat_entities(base: str, mins: tuple, maxs: tuple) -> list[dict[str, str]]:
    """The base map's compatibility entities placed for a world with these bounds."""
    cx = (mins[0] + maxs[0]) / 2
    values = {
        "z": f"{maxs[2] + 256:g}",
        # the bomb drops 3700 units (nuked_bomb_drop_think); it stops above the roof
        "bomb": f"{maxs[2] + 256 + 3700:g}",
        "cx": f"{cx:g}",
        "cy0": f"{mins[1] + 64:g}",
        "cy1": f"{mins[1] + 72:g}",
        "cz": f"{mins[2] + 160:g}",
    }
    out = []
    for e in COMPAT_ENTITIES.get(base, ()):
        out.append({k: v.format(**values) for k, v in e.items()})
    return out


def entity_text(entities: list[dict[str, str]], newline: str = "\n") -> str:
    out = []
    for e in entities:
        out.append("{")
        out += [f'"{k}" "{v}"' for k, v in e.items()]
        out.append("}")
    return newline.join(out) + newline


def parse_entities(text: str) -> list[dict[str, str]]:
    """The entity string as a list of key/value dicts (brace blocks of "key" "value")."""
    out: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    for line in text.splitlines():
        s = line.strip()
        if s == "{":
            current = {}
        elif s == "}":
            if current is not None:
                out.append(current)
            current = None
        elif current is not None:
            m = re.fullmatch(r'"([^"]*)"\s+"([^"]*)"', s)
            if m:
                current[m.group(1)] = m.group(2)
    return out


#: What each gametype's script needs from the map (stock common_mp / patch_mp
#: maps/mp/gametypes/*.gsc; docs/convert.md): (classnames, "targetname=..." entities).
#: Presence only; ``entities.check`` applies the full rules (counts, links, gameobject
#: filtering, trigger kinds, brush models) with the evidence for each.
GAMETYPES = {
    "tdm": (("mp_tdm_spawn_allies_start", "mp_tdm_spawn_axis_start", "mp_tdm_spawn"), ()),
    "dm": (("mp_dm_spawn",), ()),
    "oic": (("mp_dm_spawn",), ()),
    "gun": (("mp_dm_spawn",), ()),
    "shrp": (("mp_dm_spawn",), ()),
    "hlnd": (("mp_dm_spawn",), ()),
    "sd": (
        ("mp_sd_spawn_attacker", "mp_sd_spawn_defender"),
        ("targetname=sd_bomb_pickup_trig", "targetname=sd_bomb", "targetname=bombzone"),
    ),
    "dem": (
        (
            "mp_dem_spawn_attacker_start",
            "mp_dem_spawn_defender_start",
            "mp_dem_spawn_attacker",
            "mp_dem_spawn_defender",
        ),
        ("targetname=bombzone",),
    ),
    "dom": (
        ("mp_dom_spawn_allies_start", "mp_dom_spawn_axis_start", "mp_dom_spawn"),
        ("targetname=flag_primary", "targetname=flag_descriptor"),
    ),
    "ctf": (
        (
            "mp_ctf_spawn_allies_start",
            "mp_ctf_spawn_axis_start",
            "mp_ctf_spawn_allies",
            "mp_ctf_spawn_axis",
        ),
        ("targetname=ctf_flag_pickup_trig", "targetname=ctf_flag_zone_trig"),
    ),
    "sab": (
        (
            "mp_sab_spawn_allies_start",
            "mp_sab_spawn_axis_start",
            "mp_sab_spawn_allies",
            "mp_sab_spawn_axis",
        ),
        (
            "targetname=sab_bomb_pickup_trig",
            "targetname=sab_bomb",
            "targetname=sab_bomb_allies",
            "targetname=sab_bomb_axis",
        ),
    ),
    "koth": (("mp_tdm_spawn",), ("targetname=hq_hardpoint", "targetname=radiotrigger")),
}
#: Every gametype needs these (maps/mp/gametypes/_spawnlogic.gsc getRandomIntermissionPoint
#: asserts one mp_global_intermission).
COMMON = ("mp_global_intermission",)


def gametype_report(entities: list[dict[str, str]], gametypes: list[str]) -> dict[str, list[str]]:
    """Gametype -> what the map lacks for it (empty: ready)."""
    classes = {e.get("classname") for e in entities}
    targets = {e.get("targetname") for e in entities}
    out: dict[str, list[str]] = {}
    for g in gametypes:
        need_classes, need_targets = GAMETYPES.get(g, ((), ()))
        missing = [c for c in (*COMMON, *need_classes) if c not in classes]
        missing += [t for t in need_targets if t.split("=", 1)[1] not in targets]
        out[g] = missing
    return out
