"""What each MP gametype needs from a map's entities, and a check of a converted map.

Read from the stock gametype scripts the game runs: the installed update's ``patch_mp.ff``
carries its own ``maps/mp/gametypes/{ctf,dem,dm,dom,gun,hlnd,koth,sab,sd,tdm}.gsc`` and
``_gameobjects.gsc`` (they replace common_mp's), ``oic.gsc``, ``shrp.gsc``,
``_spawnlogic.gsc`` and ``_callbacksetup.gsc`` come from ``common_mp.ff``. Line numbers below
are into those rawfiles as ``opent5 extract ZONE OUT --type rawfile`` writes them. The MP
gametypes are the twelve of ``maps/mp/gametypes/_gametypes.txt`` (code_post_gfx_mp), which
are also the twelve ``configstrings_ps3_<map>_<gametype>.csv`` tables of a stock map zone.
docs/convert.md section 9 has the same rules as tables.

Rules shared by every gametype:

- ``_gameobjects.gsc`` main(allowed), lines 4-33, deletes every entity whose
  ``script_gameobjectname`` (space separated tokens; ``[all_modes]`` keeps it) names none of
  the gametype's ``allowed`` list (entity_is_allowed, 34-57). Everything below is checked on
  what survives that filter, so one map can carry every mode.
- ``_spawnlogic.gsc`` placeSpawnPoints (90-100) calls AbortLevel when its class has no
  spawns; addSpawnPoints (76-81, addSpawnPointsInternal 37-68) aborts when the team ends up
  with none. AbortLevel (``_callbacksetup.gsc`` 125-139) sets g_gametype to dm and exits the
  level.
- getRandomIntermissionPoint (``_spawnlogic.gsc`` 892-898) asserts an
  ``mp_global_intermission``.
- ``getEnt`` on a name more than one entity carries is a script error ("getent used with
  more than one entity", t5mp.elf string at file offset 0x8d7d20), so names read with getEnt
  must be unique.
- ``maps\\mp\\_utility::error`` (common_mp ``maps/mp/_utility.gsc`` 21-25) only prints:
  a gametype that returns after it runs without its objective.
- ``_gameobjects.gsc`` createUseObject / createCarryObject (673, 141) make a hold-to-use
  object when the trigger's classname contains "use" (``trigger_use``, ``trigger_use_touch``,
  ``trigger_radius_use``), a walk-in one otherwise.

A trigger with ``model "*N"`` is brush model N of the clipMap (cod2map writes the brush
entities as submodels 1..n with ``origin`` at the brush centre and the bounds relative to
it; the box: 14 trigger brushes, cmodels 1..14). The converter swaps the clipMap's cmodels
and the GfxWorld's brush models field by field (docs/convert.md 4: identical on mp_nuked).
"""

from __future__ import annotations

import struct
from collections.abc import Iterable
from dataclasses import dataclass, field

#: The MP gametypes (code_post_gfx_mp maps/mp/gametypes/_gametypes.txt).
GAMETYPES = ("dm", "tdm", "sd", "dem", "dom", "ctf", "sab", "koth", "oic", "gun", "shrp", "hlnd")

#: What each gametype passes to _gameobjects::main (``allowed``): patch_mp sd.gsc 189-191,
#: dem.gsc 213-221 (``bombzone_dem`` when the map has one, else ``bombzone``), koth.gsc
#: 150-153 (``koth`` too on mp_kowloon only), and ``allowed[0] = "<gametype>"`` in the others
#: (tdm 61, dm 55, dom 114, ctf 219, sab 168, gun 212, hlnd 141; common_mp oic 97, shrp 87).
ALLOWED = {
    "sd": ("sd", "bombzone", "blocker"),
    "dem": ("sd", "bombzone", "blocker", "dem"),
    "koth": ("hq",),
}

#: Spawn classes. "place": placeSpawnPoints (abort when none); "add": addSpawnPoints for a
#: team (abort when the team has none). Optional classes only add spawns.
SPAWNS = {
    "tdm": (
        ("place", "mp_tdm_spawn_allies_start", "patch_mp tdm.gsc onStartGameType 50"),
        ("place", "mp_tdm_spawn_axis_start", "patch_mp tdm.gsc onStartGameType 51"),
        ("add", "mp_tdm_spawn", "patch_mp tdm.gsc onStartGameType 52-53"),
    ),
    "dm": (("add", "mp_dm_spawn", "patch_mp dm.gsc onStartGameType 47-48"),),
    "sd": (
        ("place", "mp_sd_spawn_attacker", "patch_mp sd.gsc onStartGameType 183"),
        ("place", "mp_sd_spawn_defender", "patch_mp sd.gsc onStartGameType 184"),
    ),
    "dem": (
        ("place", "mp_dem_spawn_defender_start", "patch_mp dem.gsc onStartGameType 203"),
        ("place", "mp_dem_spawn_attacker_start", "patch_mp dem.gsc onStartGameType 204"),
        ("add", "mp_dem_spawn_attacker", "patch_mp dem.gsc onStartGameType 205"),
        ("add", "mp_dem_spawn_defender", "patch_mp dem.gsc onStartGameType 206"),
    ),
    "dom": (
        ("place", "mp_dom_spawn_allies_start", "patch_mp dom.gsc onStartGameType 102"),
        ("place", "mp_dom_spawn_axis_start", "patch_mp dom.gsc onStartGameType 103"),
        ("add", "mp_dom_spawn", "patch_mp dom.gsc change_dom_spawns 923-924"),
    ),
    "ctf": (
        ("place", "mp_ctf_spawn_allies_start", "patch_mp ctf.gsc onStartGameType 202"),
        ("place", "mp_ctf_spawn_axis_start", "patch_mp ctf.gsc onStartGameType 203"),
        ("add", "mp_ctf_spawn_allies", "patch_mp ctf.gsc onStartGameType 204"),
        ("add", "mp_ctf_spawn_axis", "patch_mp ctf.gsc onStartGameType 205"),
    ),
    "sab": (
        ("place", "mp_sab_spawn_allies_start", "patch_mp sab.gsc onStartGameType 153"),
        ("place", "mp_sab_spawn_axis_start", "patch_mp sab.gsc onStartGameType 154"),
        ("add", "mp_sab_spawn_allies", "patch_mp sab.gsc onStartGameType 155"),
        ("add", "mp_sab_spawn_axis", "patch_mp sab.gsc onStartGameType 156"),
    ),
    "koth": (("add", "mp_tdm_spawn", "patch_mp koth.gsc onStartGameType 135-146"),),
}
#: The wager modes take mp_wager_spawn when the map has any, else mp_dm_spawn (patch_mp
#: gun.gsc 195-204, hlnd.gsc 124-133; common_mp oic.gsc 80-89, shrp.gsc 70-79).
WAGER = ("oic", "gun", "shrp", "hlnd")
#: Optional spawn classes the scripts add when present (they never abort for them).
OPTIONAL_SPAWNS = {
    "dem": (
        "mp_dem_spawn_attacker_a",
        "mp_dem_spawn_attacker_b",
        "mp_dem_spawn_defender_a",
        "mp_dem_spawn_defender_b",
    ),  # dem.gsc 201-208, 825-835
    "dom": ("mp_dom_spawn_flag_a", "mp_dom_spawn_flag_b", "mp_dom_spawn_flag_c"),  # 930-949
    **{g: ("mp_wager_spawn",) for g in WAGER},
}
#: Labels whose icons the scripts precache ("" is the unlabelled icon): sd.gsc 50-75 and
#: dem.gsc 54-75 precache _a and _b; dom.gsc 43-78 _a to _e.
LABELS = {
    "sd": ("", "_a", "_b"),
    "dem": ("", "_a", "_b"),
    "dom": ("", "_a", "_b", "_c", "_d", "_e"),
}

#: Models the gametype scripts place that common_mp.ff holds (the rest of a stock map's
#: objective models are in the map's own zone: mp_nuked 362 prop_suitcase_bomb,
#: 364 p_glo_bomb_stack, 365 mp_supplydrop_hq). common_mp asset indices: 1177 tag_origin,
#: 5917 mp_flag_neutral, 5944 t5_weapon_briefcase_bomb_world.
COMMON_MODELS = frozenset({"tag_origin", "mp_flag_neutral", "t5_weapon_briefcase_bomb_world"})


@dataclass
class Result:
    have: dict[str, int] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ready(self) -> bool:
        return not self.missing

    def to_dict(self) -> dict:
        out: dict = {"ready": self.ready, "have": self.have}
        if self.missing:
            out["missing"] = self.missing
        if self.warnings:
            out["warnings"] = self.warnings
        return out


def allowed(gametype: str, mapname: str = "") -> tuple[str, ...]:
    if gametype == "koth" and mapname == "mp_kowloon":
        return ("hq", "koth")
    return ALLOWED.get(gametype, (gametype,))


def survives(entity: dict, names: Iterable[str]) -> bool:
    """_gameobjects.gsc entity_is_allowed (34-57)."""
    tag = entity.get("script_gameobjectname")
    if tag is None or tag == "[all_modes]":
        return True
    return bool(set(tag.split()) & set(names))


def filtered(entities: list[dict], gametype: str, mapname: str = "") -> list[dict]:
    names = allowed(gametype, mapname)
    return [e for e in entities if survives(e, names)]


def _vec(text: str | None) -> tuple[float, float, float]:
    try:
        x, y, z = (float(v) for v in (text or "").split())
    except ValueError:
        return (0.0, 0.0, 0.0)
    return (x, y, z)


def _dist2(a: tuple, b: tuple) -> float:
    return sum((a[i] - b[i]) ** 2 for i in range(3))


def cmodel_bounds(clip: dict, endian: str = ">") -> list[tuple[tuple, tuple]]:
    """(mins, maxs) of each cmodel of a clipMap node (``cmodels``: cmodel_t, 0x48 bytes,
    mins and maxs the first 24). ``endian``: ">" for a PS3 or converted node, "<" for a node
    of a PC parse."""
    raw = clip.get("cmodels") or b""
    out = []
    for i in range(len(raw) // 0x48):
        v = struct.unpack_from(endian + "6f", raw, 0x48 * i)
        out.append((v[0:3], v[3:6]))
    return out


class Index:
    """The entities of one gametype after the filter, by targetname and classname."""

    def __init__(self, entities: list[dict], cmodels: list | None) -> None:
        self.entities = entities
        self.cmodels = cmodels
        self.by_name: dict[str, list[dict]] = {}
        self.by_class: dict[str, list[dict]] = {}
        for e in entities:
            if "targetname" in e:
                self.by_name.setdefault(e["targetname"], []).append(e)
            self.by_class.setdefault(e.get("classname", ""), []).append(e)

    def named(self, name: str | None) -> list[dict]:
        return self.by_name.get(name, []) if name else []

    def classed(self, name: str) -> list[dict]:
        return self.by_class.get(name, [])

    def bounds(self, e: dict) -> tuple[tuple, tuple] | None:
        """World bounds of a trigger: its brush model, or radius and height."""
        o = _vec(e.get("origin"))
        model = e.get("model", "")
        if model.startswith("*"):
            if self.cmodels is None:
                return None
            n = int(model[1:]) if model[1:].isdigit() else -1
            if not 0 <= n < len(self.cmodels):
                return None
            lo, hi = self.cmodels[n]
            return tuple(o[i] + lo[i] for i in range(3)), tuple(o[i] + hi[i] for i in range(3))
        if "radius" in e:
            r, h = float(e["radius"]), float(e.get("height", "0"))
            return (o[0] - r, o[1] - r, o[2]), (o[0] + r, o[1] + r, o[2] + h)
        return None


def _is_trigger(e: dict) -> bool:
    return e.get("classname", "").startswith("trigger_")


class _Check:
    def __init__(self, ix: Index, gametype: str) -> None:
        self.ix = ix
        self.g = gametype
        self.r = Result()

    def miss(self, text: str) -> None:
        self.r.missing.append(text)

    def count(self, key: str, n: int) -> int:
        self.r.have[key] = n
        return n

    def spawns(self) -> None:
        for how, cls, where in SPAWNS.get(self.g, ()):
            if not self.count(cls, len(self.ix.classed(cls))):
                verb = "placeSpawnPoints" if how == "place" else "addSpawnPoints"
                self.miss(f"{cls}: none ({where}: {verb} aborts the level)")
        if self.g in WAGER:
            wager = len(self.ix.classed("mp_wager_spawn"))
            dm = self.count("mp_dm_spawn", len(self.ix.classed("mp_dm_spawn")))
            if wager:
                self.count("mp_wager_spawn", wager)
            elif not dm:
                self.miss(f"mp_dm_spawn: none and no mp_wager_spawn ({self.g}.gsc onStartGameType)")
        for cls in OPTIONAL_SPAWNS.get(self.g, ()):
            n = len(self.ix.classed(cls))
            if n:
                self.count(cls, n)

    def unique(self, name: str, where: str, trigger: bool = False) -> dict | None:
        found = self.ix.named(name)
        self.count(name, len(found))
        if not found:
            self.miss(f"targetname {name}: none ({where})")
            return None
        if len(found) > 1:
            self.miss(f"targetname {name}: {len(found)}, getEnt needs exactly one ({where})")
            return None
        if trigger and not _is_trigger(found[0]):
            self.miss(f"targetname {name}: a {found[0].get('classname')}, expected a trigger")
        return found[0]

    def use_trigger(self, e: dict, what: str) -> None:
        cls = e.get("classname", "")
        if not _is_trigger(e):
            self.miss(f"{what}: a {cls}, expected a trigger")
        elif "use" not in cls:
            self.miss(
                f"{what}: {cls} is a walk-in trigger; planting needs a use trigger "
                "(_gameobjects.gsc createUseObject 681)"
            )

    def label(self, e: dict, what: str) -> None:
        label = e.get("script_label", "")
        ok = LABELS.get(self.g)
        if ok is not None and label not in ok:
            self.miss(f"{what}: script_label {label!r}, the scripts precache icons for {ok}")

    def visuals(self, e: dict, what: str, where: str) -> list[dict]:
        vis = self.ix.named(e.get("target"))
        if not vis:
            self.miss(
                f"{what}: target {e.get('target')!r} names no entity; visuals[0] is "
                f"used unchecked ({where})"
            )
        return vis

    def bombzones(self, name: str, where: str) -> None:
        zones = self.ix.named(name)
        if not self.count(name, len(zones)):
            self.miss(f"targetname {name}: none ({where}; no bomb sites)")
        for z in zones:
            what = f"{name} {z.get('script_label', '')}".strip()
            self.use_trigger(z, what)
            self.label(z, what)
            vis = self.visuals(z, what, where)
            if vis:
                defuse = self.ix.named(vis[0].get("target"))
                if len(defuse) != 1:
                    self.miss(
                        f"{what}: its visual's target {vis[0].get('target')!r} names "
                        f"{len(defuse)} entities, the defuse trigger must be one ({where})"
                    )


def _sd(c: _Check) -> None:
    c.unique("sd_bomb_pickup_trig", "patch_mp sd.gsc bombs 435-438", trigger=True)
    c.unique("sd_bomb", "patch_mp sd.gsc bombs 441-444")
    c.bombzones("bombzone", "patch_mp sd.gsc bombs 466-502")


def _dem(c: _Check) -> None:
    name = "bombzone_dem" if c.ix.named("bombzone_dem") else "bombzone"
    c.bombzones(name, "patch_mp dem.gsc bombs 517-551")


def _dom(c: _Check) -> None:
    ix = c.ix
    flags = ix.named("flag_primary") + ix.named("flag_secondary")
    c.count("flag_primary", len(ix.named("flag_primary")))
    if ix.named("flag_secondary"):
        c.count("flag_secondary", len(ix.named("flag_secondary")))
    if len(flags) < 2:
        c.miss(
            f"flag_primary / flag_secondary: {len(flags)}, at least 2 (patch_mp dom.gsc "
            "domFlags 248-253 aborts the level)"
        )
        return
    for f in flags:
        what = f"flag {f.get('script_label', '')}".strip()
        if not _is_trigger(f):
            c.miss(f"{what}: a {f.get('classname')}, expected a trigger")
        c.label(f, what)
        if "target" in f and len(ix.named(f["target"])) != 1:
            c.miss(f"{what}: target {f['target']!r} must name one entity (dom.gsc 265-267)")
    descs = ix.named("flag_descriptor")
    if not c.count("flag_descriptor", len(descs)):
        c.miss("flag_descriptor: none (patch_mp dom.gsc flagSetup 745, aborts at 827-828)")
        return
    nearest: dict[int, dict] = {}
    for f in flags:
        o = _vec(f.get("origin"))
        d = min(descs, key=lambda e, o=o: _dist2(o, _vec(e.get("origin"))))
        if id(d) in nearest:
            c.miss("flag_descriptor: one is nearest to two flags (dom.gsc flagSetup)")
        nearest[id(d)] = f
        if "script_linkname" not in d:
            c.miss("flag_descriptor: no script_linkname (dom.gsc flagSetup)")
    names = {d.get("script_linkname") for d in descs if id(d) in nearest}
    for d in descs:
        if id(d) not in nearest:
            continue
        for link in d.get("script_linkto", "").split():
            if link not in names or link == d.get("script_linkname"):
                c.miss(
                    f"flag_descriptor {d.get('script_linkname')}: script_linkto {link!r} "
                    "is not another flag's descriptor (dom.gsc flagSetup, aborts)"
                )


def _ctf(c: _Check) -> None:
    ix = c.ix
    for name, where in (
        ("ctf_flag_pickup_trig", "patch_mp ctf.gsc ctf 426-429"),
        ("ctf_flag_zone_trig", "patch_mp ctf.gsc ctf 440-443"),
    ):
        found = ix.named(name)
        c.count(name, len(found))
        if len(found) != 2:
            c.miss(f"{name}: {len(found)}, exactly 2 ({where}; no flags otherwise)")
            continue
        teams = sorted(e.get("script_team", "") for e in found)
        if teams != ["allies", "axis"]:
            c.miss(
                f"{name}: script_team {teams}, one allies and one axis "
                "(ctf.gsc createFlag 317, createFlagZone 353)"
            )
        for e in found:
            if not _is_trigger(e):
                c.miss(f"{name}: a {e.get('classname')}, expected a trigger")
            if name.endswith("pickup_trig") and "target" in e and len(ix.named(e["target"])) != 1:
                c.miss(
                    f"{name} {e.get('script_team')}: target {e['target']!r} must name one "
                    "entity (ctf.gsc createFlag 308-310)"
                )


def _sab(c: _Check) -> None:
    c.unique("sab_bomb_pickup_trig", "patch_mp sab.gsc sabotage 312-315", trigger=True)
    c.unique("sab_bomb", "patch_mp sab.gsc sabotage 318-321")
    for team in ("axis", "allies"):
        name = f"sab_bomb_{team}"
        e = c.unique(name, "patch_mp sab.gsc sabotage 339-357")
        if e is not None:
            c.use_trigger(e, name)
            c.visuals(e, name, "patch_mp sab.gsc createBombZone 362, 370")


def _koth(c: _Check) -> None:
    ix = c.ix
    radios = ix.named("hq_hardpoint")
    trigs = ix.named("radiotrigger")
    c.count("hq_hardpoint", len(radios))
    c.count("radiotrigger", len(trigs))
    if len(radios) < 2:
        c.miss(
            f"hq_hardpoint: {len(radios)}, at least 2 (patch_mp koth.gsc SetupRadios "
            "523-524, aborts at 578-579)"
        )
    for r in radios:
        o = _vec(r.get("origin"))
        what = f"hq_hardpoint at {r.get('origin')}"
        if "target" not in r:
            c.miss(f"{what}: no target (koth.gsc SetupRadios 560 reads radio.target)")
        inside, unknown = 0, 0
        for t in trigs:
            b = ix.bounds(t)
            if b is None:
                unknown += 1
            elif all(b[0][i] <= o[i] <= b[1][i] for i in range(3)):
                inside += 1
        if unknown:
            c.r.warnings.append(f"{what}: {unknown} radiotrigger bounds not known; not checked")
        elif inside == 0:
            c.miss(f"{what}: inside no radiotrigger (koth.gsc SetupRadios 528-555 istouching)")
        elif inside > 1:
            c.r.warnings.append(
                f"{what}: inside the bounds of {inside} radiotrigger triggers; istouching needs "
                "exactly 1, which bounds cannot decide"
            )


OBJECTIVES = {"sd": _sd, "dem": _dem, "dom": _dom, "ctf": _ctf, "sab": _sab, "koth": _koth}


def check(
    entities: list[dict],
    gametype: str,
    cmodels: list | None = None,
    xmodels: set[str] | None = None,
    mapname: str = "",
) -> Result:
    """One gametype: what the map has and lacks. ``cmodels``: (mins, maxs) per brush model
    (``cmodel_bounds``), to check ``model "*N"`` and trigger bounds; ``xmodels``: model names
    the zone (with common_mp) provides, to warn about entities naming others."""
    ix = Index(filtered(entities, gametype, mapname), cmodels)
    c = _Check(ix, gametype)
    if not c.count("mp_global_intermission", len(ix.classed("mp_global_intermission"))):
        c.miss("mp_global_intermission: none (_spawnlogic.gsc getRandomIntermissionPoint 895)")
    c.spawns()
    if gametype in OBJECTIVES:
        OBJECTIVES[gametype](c)
    for e in ix.entities:
        model = e.get("model", "")
        if model.startswith("*"):
            n = int(model[1:]) if model[1:].isdigit() else -1
            if cmodels is not None and not 0 <= n < len(cmodels):
                c.miss(
                    f"{e.get('targetname') or e.get('classname')}: brush model {model}, the "
                    f"clipMap has {len(cmodels)} (0..{len(cmodels) - 1})"
                )
        elif (
            model
            and xmodels is not None
            and e.get("classname") == "script_model"
            and "destructibledef" not in e
            and model not in xmodels | COMMON_MODELS
        ):
            text = f"script_model {model!r}: not in the zone or the known common_mp models"
            if text not in c.r.warnings:
                c.r.warnings.append(text)
    return c.r


def report(
    entities: list[dict],
    gametypes: Iterable[str] = GAMETYPES,
    cmodels: list | None = None,
    xmodels: set[str] | None = None,
    mapname: str = "",
) -> dict[str, dict]:
    """Gametype -> ``Result.to_dict()`` (ready, have, missing, warnings)."""
    return {g: check(entities, g, cmodels, xmodels, mapname).to_dict() for g in gametypes}


def zone_report(entities: list[dict], clip: dict, xfile, mapname: str = "") -> dict[str, dict]:
    """``report`` for a converted map: ``clip`` the converted (big-endian) clipMap node,
    ``xfile`` the zone being written (its xmodels are the models the map can place)."""
    from opent5.xfile import AssetType

    xmodels = {a.name for a in xfile.assets if a.type == AssetType.XMODEL and a.name}
    return report(entities, GAMETYPES, cmodel_bounds(clip), xmodels, mapname)
