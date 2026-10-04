"""Assemble a Radiant ``.map`` (iwmap 4) from greedy-meshed terrain brushes plus entities.

Brush and face syntax follow tools/testmap.py and docs/research/box-map.md 1.1 (the PC tools'
``.map``): one axis-aligned brush per box, three points per face in Radiant winding, a material
and a lightmap token per face. Hidden faces are ``caulk``. Each block face uses a stock
``blockout_test_*`` material (``STOCK_MATERIAL``); the map's own pixel art is supplied to the
converter as overrides keyed by each material's colour-map image (``STOCK_COLORMAP``). A
``lightgrid_volume`` brush fills the playable area so cod2rad bakes a grid, a ``caulk`` shell
seals the world, and a grid of primary lights lights it (the Mod Tools install has no sky
techset, so there is no outdoor sun; ``primary_lights``). See docs/mapgen.md.
"""

from __future__ import annotations

from .greedy import CAULK, FACES, Box
from .terrain import GRASS, SAND, Terrain

LIGHTMAP = "lightmap_gray 16384 16384 0 0 0 0"
#: Each block face uses a stock PC Mod Tools ``blockout_test_*`` material (cod2rad-safe, one
#: proven by the full box) rather than a cloned material (the clones crashed cod2rad,
#: overnight-report). The converter force-builds these from the PC zone and overrides their
#: colour map with the map's own pixel art (``STOCK_COLORMAP`` is the override key), so the
#: block textures are the map's own while the material structure stays stock (docs/mapgen.md,
#: docs/decisions.md). The materials are distinct per tile so each gets its own art.
#: Eight stock ``blockout_test_*`` materials plus three mp_nuked art materials (floor, wall,
#: ceiling), all with a colour-map IWI loose in the PC tools' raw/images and all proven to
#: bake (the box uses the three art ones). Distinct per tile so each gets its own art.
STOCK_MATERIAL = {
    "grass_top": "blockout_test_fabric01",
    "grass_side": "blockout_test_wood",
    "dirt": "blockout_test_concrete_med",
    "stone": "blockout_test_rock",
    "sand": "blockout_test_asphalt",
    "plank": "jun_art_concrete_base02",
    "log_side": "blockout_test_metal",
    "log_top": "blockout_test_metal_dark",
    "leaves": "blockout_test_concrete",
    "water": "us_art_wall_vinylsiding_white",
    "cobble": "pent_art_wall_creampaint02",
}
#: Colour-map image name of each tile's stock material (read from the PC material binaries):
#: the key the converter overrides with the generated art.
STOCK_COLORMAP = {
    "grass_top": "~-gblockout_fabric_01_c",
    "grass_side": "~-gblockout_wood_test_c",
    "dirt": "~-gblockout_concrete_med_test_c",
    "stone": "~-gblockout_rock_test_c",
    "sand": "~-gblockout_asphalt_test_c",
    "plank": "~-gconcrete_base02_c",
    "log_side": "~-gblockout_metal_test_c",
    "log_top": "~-gblockout_metal_dark_test_c",
    "leaves": "~-gblockout_average_test_c",
    "water": "~-gus_art_wall_vinylsiding_white_c",
    "cobble": "~-gpent_art_wall_creampaint02_c",
}
#: The sky faces reuse Nuketown's skybox material: it already exists in the PC tools and in the
#: mp_nuked base zone, so the converter reuses it by name (no new sky asset). cod2map marks these
#: as sky surfaces and cod2rad bakes the worldspawn sun onto what sees them.
SKY = "mtl_skybox_mp_nuked"
LIGHT_GRID = "lightgrid_volume"

#: Worldspawn keys from the PC mp_nuked entity string (box-map.md 1.1); the sun lights the
#: open terrain. The sky box key is carried here as a map of its own.
WORLDSPAWN = (
    ("maxlightgridcolors", "32000"),
    ("exposure", "1.0"),
    ("sunshadowintensity", "7"),
    ("_sunshadowcolor", "0.709804 0.803922 0.984314"),
    ("sunlight", "16"),
    ("suncolor", "0.996078 0.976471 0.886275"),
    # Raised ambient (was 0.18): the map is sealed and lit only by the primary spot grid, so
    # shadowed and far faces need a floor of fill light to read rather than going near-black.
    ("ambientintensity", "0.42"),
    ("_ambientcolor", "0.682353 0.752941 0.898039"),
    ("newsun", "1"),
    ("sundirection", "-45 155 0"),
    ("classname", "worldspawn"),
)


# Face winding of an axis-aligned box (lo, hi), matching testmap._faces.
def _face_points(lo, hi):
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


#: Raw materials that pass straight through (not block tiles): the seal caulk, the light-grid
#: volume, the sky, and the entity-brush materials for triggers and solid script_brushmodels.
#: ``ladder`` and ``clip_player`` are stock tool materials of the PC Mod Tools (verified in
#: raw/materials beside ``clip``/``caulk``/``trigger``): cod2map stamps the ladder surface flag
#: from a ``ladder`` face and the player-clip contents from ``clip_player``, and the converter's
#: clipMap swap carries both to PS3 unchanged (no converter change; a ladder needs no trigger,
#: its mount is engine-driven on contact). See docs/mapgen.md.
LADDER = "ladder"
CLIP_PLAYER = "clip_player"
_PASSTHROUGH = frozenset({CAULK, LIGHT_GRID, SKY, "trigger", "clip", LADDER, CLIP_PLAYER})


def material_name(tile: str) -> str:
    """Material name used in the ``.map`` for a tile (caulk, the grid, sky, trigger and clip
    pass through; every block face maps to its stock ``blockout_test_*`` material)."""
    if tile in _PASSTHROUGH:
        return tile
    return STOCK_MATERIAL[tile]


def colormap_name(tile: str) -> str:
    """The colour-map image name of a tile's stock material: the converter override key."""
    return STOCK_COLORMAP[tile]


def _brush(points_by_face, faces: dict, scale: int) -> list[str]:
    out = ["{"]
    for name in FACES:
        p = points_by_face[name]
        coords = " ".join("( {:g} {:g} {:g} )".format(*pt) for pt in p)
        mat = material_name(faces.get(name, CAULK))
        out.append(f" {coords} {mat} {scale} {scale} 0 0 0 0 {LIGHTMAP}")
    out.append("}")
    return out


def _box_brush(box: Box, scale: int) -> list[str]:
    return _brush(_face_points(box.lo, box.hi), box.faces, scale)


def _axis_brush(lo, hi, material: str, scale: int) -> list[str]:
    faces = {name: material for name in FACES}
    return _brush(_face_points(lo, hi), faces, scale)


def _v(*xs) -> str:
    return " ".join(f"{x:g}" for x in xs)


def _surface_world_z(t: Terrain, i: int, j: int) -> float:
    return t.origin[2] + int(t.heights[i, j]) * t.block


def _flat_columns(t: Terrain) -> list[tuple[int, int]]:
    """Grass or sand top cells above the sea, away from trees/huts: good spawn spots."""
    nx, ny, nz = t.shape
    out = []
    for i in range(2, nx - 2):
        for j in range(2, ny - 2):
            k = int(t.heights[i, j]) - 1
            if k <= t.sea_level or k + 1 >= nz:
                continue
            if int(t.grid[i, j, k]) in (GRASS, SAND) and int(t.grid[i, j, k + 1]) == 0:
                out.append((i, j))
    return out


def _pick(t: Terrain, cols: list[tuple[int, int]], fx: float, fy: float) -> tuple[int, int]:
    """The walkable column nearest the fractional map position (fx, fy in -1..1), so an entity
    lands on open ground and not inside a building, a cover piece or the pond."""
    nx, ny, _ = t.shape
    cx, cy = nx / 2 + fx * nx * 0.46, ny / 2 + fy * ny * 0.46
    if not cols:
        return (int(cx), int(cy))
    return min(cols, key=lambda c: (c[0] - cx) ** 2 + (c[1] - cy) ** 2)


def _origin(t: Terrain, i: int, j: int, dz: int = 8) -> tuple[float, float, float]:
    x, y, _ = t.cell_centre_world(i, j, 0)
    return (x, y, _surface_world_z(t, i, j) + dz)


def _brush_entity(classname: str, centre, size: float, height: float, material: str, keys: dict):
    """A brush entity: a ``size`` x ``size`` x ``height`` box standing on ``centre`` (its z is the
    box bottom), every face ``material`` (``trigger`` for triggers, ``clip`` for solid
    script_brushmodels). cod2map compiles the brushes into a clipMap submodel and gives the entity
    a ``model "*N"``. The brushes live under ``_brushes`` and ``map_text`` emits them."""
    x, y, z = centre
    h = size / 2
    lo, hi = (x - h, y - h, z), (x + h, y + h, z + height)
    return {"classname": classname, **keys, "_brushes": [(lo, hi, material)]}


def spawn_entities(t: Terrain) -> list[dict]:
    """Team-based (TDM/KOTH), FFA and objective-mode spawns spread across the playable ground,
    plus the global start / intermission. The spawn classes needed so the level does not
    AbortLevel (docs/convert.md 9.2): per team a ``mp_tdm_spawn_*_start`` and the neutral
    ``mp_tdm_spawn`` / ``mp_dm_spawn`` pools, plus the SD and DOM spawns for those modes."""
    cols = _flat_columns(t)
    ents: list[dict] = []
    centre = _pick(t, cols, 0.0, 0.0)
    ents.append({"classname": "info_player_start", "origin": _v(*_origin(t, *centre))})
    ents.append(
        {
            "classname": "mp_global_intermission",
            "origin": _v(*_origin(t, *centre, dz=t.block * 10)),
            "angles": "20 90 0",
        }
    )
    # Team start lines on opposite ends: allies at -x (yaw 0, facing +x), axis at +x (yaw 180).
    team_y = (-0.42, -0.16, 0.16, 0.42)
    west_start = ("mp_tdm_spawn_allies_start", "mp_dom_spawn_allies_start", "mp_sd_spawn_attacker")
    east_start = ("mp_tdm_spawn_axis_start", "mp_dom_spawn_axis_start", "mp_sd_spawn_defender")
    for fx, starts, yaw in ((-0.72, west_start, 0), (0.72, east_start, 180)):
        for fy in team_y:
            i, j = _pick(t, cols, fx, fy)
            for cls in starts:
                ents.append(
                    {"classname": cls, "origin": _v(*_origin(t, i, j)), "angles": _v(0, yaw, 0)}
                )
    # Neutral pools (TDM / FFA / DOM) spread over the middle and flanks.
    for fx, fy in (
        (-0.4, -0.4), (0.4, 0.4), (-0.4, 0.4), (0.4, -0.4),
        (0.0, -0.55), (0.0, 0.55), (-0.6, 0.0), (0.6, 0.0), (0.0, 0.0),
    ):
        i, j = _pick(t, cols, fx, fy)
        for cls in ("mp_tdm_spawn", "mp_dm_spawn", "mp_dom_spawn"):
            ents.append({"classname": cls, "origin": _v(*_origin(t, i, j)), "angles": "0 0 0"})
    return ents


def objective_entities(t: Terrain) -> list[dict]:
    """Domination flags (A/B/C) and the two Search & Destroy bomb sites, placed on the ground.

    DOM flags are ``trigger_radius`` points (flag_primary) each with a ``script_origin``
    descriptor, linked A-B-C. SD uses two bomb sites on the defenders' (+x) side, each a
    ``bombzone`` radius-use trigger and a bomb model, plus the attackers' plantable bomb. These
    give the sd / dom scripts the game objects they need without AbortLevel; the radius
    triggers show the objective (the brush-model plant prompt is left for a release build).
    """
    cols = _flat_columns(t)
    ents: list[dict] = []

    def radius(i, j):
        return f"{t.block * 2.5:g}"

    # Domination: three flags across the middle, linked A-B-C.
    links = {"a": "flag_b", "b": "flag_a flag_c", "c": "flag_b"}
    for label, fx in (("a", -0.45), ("b", 0.0), ("c", 0.45)):
        i, j = _pick(t, cols, fx, 0.0)
        ox, oy, oz = _origin(t, i, j, dz=0)
        ents.append(
            {
                "classname": "trigger_radius",
                "origin": _v(ox, oy, oz),
                "targetname": "flag_primary",
                "script_label": f"_{label}",
                "radius": radius(i, j),
                "height": f"{t.block * 4:g}",
                "script_gameobjectname": "dom",
            }
        )
        ents.append(
            {
                "classname": "script_origin",
                "origin": _v(ox, oy, oz + t.block * 2),
                "targetname": "flag_descriptor",
                "script_linkname": f"flag_{label}",
                "script_linkto": links[label],
                "script_gameobjectname": "dom",
            }
        )
    # Search & Destroy: two bomb sites on the +x (defender) side, each the stock mp_nuked
    # five-entity set (tools/testmap.py objective_entities): a trigger_use_touch plant trigger
    # ("bombzone", script_bombmode_original + script_label, targeting the bomb), a second
    # trigger_use_touch defuse trigger, the bomb script_model chaining the two by target with
    # script_exploder, and two solid script_brushmodels. The plant/defuse triggers are brush
    # models so _gameobjects::createUseObject gives a hold-to-use plant prompt (a radius trigger
    # shows the objective but no prompt). cod2map compiles the brushes into the clipMap.
    exploders = {"a": 7121, "b": 7152}
    for label, fy in (("a", -0.4), ("b", 0.4)):
        i, j = _pick(t, cols, 0.42, fy)
        c = _origin(t, i, j, dz=0)
        ents.append(
            _brush_entity(
                "trigger_use_touch", c, 96, 96, "trigger",
                {
                    "targetname": "bombzone",
                    "script_gameobjectname": "bombzone",
                    "target": f"bombzone_{label}_auto1",
                    "script_bombmode_original": "1",
                    "script_label": f"_{label}",
                },
            )
        )
        ents.append(
            _brush_entity(
                "trigger_use_touch", c, 96, 96, "trigger",
                {"targetname": f"bombzone_{label}_auto2", "script_gameobjectname": "bombzone"},
            )
        )
        ents.append(
            {
                "classname": "script_model",
                "origin": _v(c[0], c[1], c[2] + 2),
                "angles": "0 90 0",
                "model": "p_glo_bomb_stack",
                "targetname": f"bombzone_{label}_auto1",
                "target": f"bombzone_{label}_auto2",
                "script_gameobjectname": "bombzone",
                "script_exploder": str(exploders[label]),
                "spawnflags": "5",
            }
        )
        ents.append(
            _brush_entity(
                "script_brushmodel", c, 56, 14, "clip",
                {"script_gameobjectname": "bombzone", "spawnflags": "1"},
            )
        )
        ents.append(
            _brush_entity(
                "script_brushmodel", (c[0], c[1], c[2] + 14), 56, 14, "clip",
                {"script_gameobjectname": "bombzone", "spawnflags": "1"},
            )
        )
    # The attackers' plantable bomb and its pickup trigger (brush), on the -x side.
    i, j = _pick(t, cols, -0.42, 0.0)
    c = _origin(t, i, j, dz=0)
    ents.append(
        _brush_entity(
            "trigger_multiple", c, 48, 48, "trigger",
            {"targetname": "sd_bomb_pickup_trig", "script_gameobjectname": "sd"},
        )
    )
    ents.append(
        {
            "classname": "script_model",
            "origin": _v(c[0], c[1], c[2] + 2),
            "angles": "0 270 0",
            "model": "prop_suitcase_bomb",
            "targetname": "sd_bomb",
            "script_gameobjectname": "sd",
            "spawnflags": "4",
        }
    )
    return ents


def compass_corners(t: Terrain) -> list[dict]:
    """Two ``minimap_corner`` script_origins for the in-game compass (docs/convert.md 10.4);
    the converter also adds these, included here so the ``.map`` is self-contained."""
    (x0, y0, _), (x1, y1, _) = t.world_bounds()
    b = t.block
    z = t.origin[2] + t.sea_level * b  # inside the hull, not on the boundary
    return [
        {
            "classname": "script_origin",
            "targetname": "minimap_corner",
            "origin": _v(x0 + b, y0 + b, z),
        },
        {
            "classname": "script_origin",
            "targetname": "minimap_corner",
            "origin": _v(x1 - b, y1 - b, z),
        },
    ]


def seal_brushes(t: Terrain, thickness: int = 128, margin: int = 128) -> list[list[str]]:
    """A watertight ``caulk`` shell enclosing the world (ceiling, four walls, floor slab).
    Walls span below the floor and overlap the corners, so there is no seam to leak through.
    The play interior ``[x0,y0,z0]..[x1,y1,top]`` stays open (terrain plus air). The shell is
    caulk, not a sky material: this Mod Tools install has no sky techset (the sky materials'
    techsets are absent from raw/techsets), so the map is sealed and lit by primary lights
    (``primary_lights``) rather than an outdoor sun. See docs/mapgen.md."""
    (x0, y0, z0), (x1, y1, z1) = t.world_bounds()
    xl, yl = x0 - margin, y0 - margin
    xr, yr = x1 + margin, y1 + margin
    top = z1 + margin
    zbot = z0 - thickness
    ztop = top + thickness
    s = t.block
    return [
        _axis_brush((xl, yl, top), (xr, yr, ztop), CAULK, s),  # ceiling
        _axis_brush((xl, yl, zbot), (xr, yr, z0), CAULK, s),  # floor slab (under the terrain)
        _axis_brush((xl, yl, zbot), (x0, yr, ztop), CAULK, s),  # -x wall (full height, corners)
        _axis_brush((x1, yl, zbot), (xr, yr, ztop), CAULK, s),  # +x wall
        _axis_brush((x0, yl, zbot), (x1, y0, ztop), CAULK, s),  # -y wall
        _axis_brush((x0, y1, zbot), (x1, yr, ztop), CAULK, s),  # +y wall
    ]


def primary_lights(t: Terrain, grid: int = 3) -> list[dict]:
    """A ``grid`` x ``grid`` of primary spot lights pointing straight down, each with its own
    ``info_null`` target, so cod2rad bakes surface lightmaps and the light grid across the
    sealed world (the box's room light, repeated for the larger area; docs/convert.md 13,
    box-rpcs3-issues.md 2.1). cod2map ignores a primary light with no target."""
    (x0, y0, _), (x1, y1, z1) = t.world_bounds()
    high = z1 - t.block  # just under the ceiling
    low = t.origin[2] + t.block  # near the floor
    out: list[dict] = []
    n = 0
    for gi in range(grid):
        for gj in range(grid):
            x = x0 + (gi + 0.5) * (x1 - x0) / grid
            y = y0 + (gj + 0.5) * (y1 - y0) / grid
            target = f"blocklight_{n}"
            out.append(
                {
                    "classname": "light",
                    "spawnflags": "1",  # PRIMARY (codbo.def), a spot with a target below
                    "origin": _v(x, y, high),
                    "radius": "2600",
                    "intensity": "1.6",
                    "_color": "1 0.97 0.9",
                    "target": target,
                    "fov_outer": "110",
                    "fov_inner": "60",
                }
            )
            out.append(
                {"classname": "info_null", "origin": _v(x, y, low), "targetname": target}
            )
            n += 1
    return out


def path_nodes(t: Terrain, step: int = 4) -> list[dict]:
    """A ``node_pathnode`` on walkable surface cells, on a grid of stride ``step``: the
    spawn-influence connectivity Domination and the other team-based modes need, which cod2map
    compiles into the GameWorldMp path data (docs/convert.md, box-objectives-cd.md 4). Without
    it those modes stall at load. Placed a little above the ground; cod2map links nodes in
    range and drops each to the floor."""
    nx, ny, nz = t.shape
    out: list[dict] = []
    for i in range(2, nx - 2, step):
        for j in range(2, ny - 2, step):
            k = int(t.heights[i, j]) - 1
            if k <= t.sea_level or k + 1 >= nz:
                continue
            if int(t.grid[i, j, k]) in (GRASS, SAND) and int(t.grid[i, j, k + 1]) == 0:
                x, y, _ = t.cell_centre_world(i, j, 0)
                out.append(
                    {"classname": "node_pathnode", "origin": _v(x, y, _surface_world_z(t, i, j) + 16)}
                )
    return out


def light_grid_brush(t: Terrain) -> list[str]:
    """A ``lightgrid_volume`` brush over the playable column band (sea level up to the sky)."""
    (x0, y0, _), (x1, y1, _) = t.world_bounds()
    zlo = t.origin[2] + t.sea_level * t.block
    zhi = t.origin[2] + t.shape[2] * t.block
    return _axis_brush((x0 + 8, y0 + 8, zlo), (x1 - 8, y1 - 8, zhi), LIGHT_GRID, t.block)


# -- o_blocks5 structure primitives --------------------------------------------------------
# Reusable, deterministic brush emitters for the next map iteration: half-height (18u) slab
# stairs, a player-clip edge wall, a ladder face and a trigger_hurt kill volume. They mirror
# the proven ``seal_brushes`` / ``_brush_entity`` patterns (axis brushes and brush entities
# cod2map compiles into the clipMap) and are not wired into ``map_text`` yet, so the proven
# o_blocks4 output is unchanged. See docs/mapgen.md (o_blocks5 plan).


def half_step(t: Terrain) -> int:
    """The BO1 step height, 18 units for the 36-unit block: a full block cannot be stepped, so
    stairs and ramps rise one half-block per step (docs/mapgen.md, terrain.py block note)."""
    return t.block // 2


def slab_stairs(
    t: Terrain,
    foot: tuple[float, float, float],
    axis: str,
    steps: int,
    width: float,
    material: str = "plank",
    rise: int | None = None,
    scale: int | None = None,
) -> list[list[str]]:
    """A flight of ``steps`` half-height (``rise``, default 18u) slab steps climbing along
    ``axis`` ('x' or '-x' / 'y' or '-y') from ``foot`` (the bottom step's near-bottom corner).
    Each step is one tread deep (``rise`` units of run per ``rise`` of climb, a 45-degree
    stair) and ``width`` wide, so the whole flight is walkable at the engine step height. Every
    face uses ``material`` (a stock block material or ``clip``). Returns one brush per step."""
    rise = rise if rise is not None else half_step(t)
    scale = scale if scale is not None else t.block
    sign = -1 if axis.startswith("-") else 1
    along = axis[-1]
    x, y, z = foot
    out: list[list[str]] = []
    for n in range(steps):
        front = n * rise * sign  # tread advances one rise per step (45-degree pitch)
        top = z + (n + 1) * rise
        if along == "x":
            lo = (x + min(front, front + rise * sign), y, z)
            hi = (x + max(front, front + rise * sign), y + width, top)
        else:
            lo = (x, y + min(front, front + rise * sign), z)
            hi = (x + width, y + max(front, front + rise * sign), top)
        # _axis_brush resolves the tile token through material_name itself, so pass the token.
        out.append(_axis_brush(lo, hi, material, scale))
    return out


def clip_wall_ring(
    t: Terrain, inset: int | None = None, height: int | None = None, thickness: int | None = None
) -> list[list[str]]:
    """Four ``clip_player`` walls just inside the world bounds: an invisible barrier at the true
    playable edge (so a lava perimeter or drop is not walkable), leaving the terrain untouched.
    ``inset`` from each side, ``height`` above the floor, ``thickness`` into the wall."""
    inset = inset if inset is not None else t.block * 2
    height = height if height is not None else t.block * 4
    thickness = thickness if thickness is not None else t.block
    (x0, y0, z0), (x1, y1, _) = t.world_bounds()
    xl, xr = x0 + inset, x1 - inset
    yl, yr = y0 + inset, y1 - inset
    zt = z0 + height
    s = t.block
    return [
        _axis_brush((xl, yl, z0), (xr, yl + thickness, zt), CLIP_PLAYER, s),  # -y
        _axis_brush((xl, yr - thickness, z0), (xr, yr, zt), CLIP_PLAYER, s),  # +y
        _axis_brush((xl, yl, z0), (xl + thickness, yr, zt), CLIP_PLAYER, s),  # -x
        _axis_brush((xr - thickness, yl, z0), (xr, yr, zt), CLIP_PLAYER, s),  # +x
    ]


def ladder_brush(
    t: Terrain, foot: tuple[float, float, float], height: float, axis: str, scale: int | None = None
) -> list[str]:
    """A thin climbable ``ladder`` brush of ``height`` standing at ``foot`` (its bottom), its flat
    face normal to ``axis`` ('x'/'-x'/'y'/'-y'). cod2map stamps the ladder surface flag on it;
    the player climbs on contact (no trigger). Depth is a quarter-block so it hugs a wall."""
    scale = scale if scale is not None else t.block
    x, y, z = foot
    d = t.block / 4
    w = t.block  # rung width
    if axis[-1] == "x":
        lo, hi = (x, y, z), (x + d, y + w, z + height)
    else:
        lo, hi = (x, y, z), (x + w, y + d, z + height)
    return _axis_brush(lo, hi, LADDER, scale)


def hurt_volume(
    lo: tuple[float, float, float], hi: tuple[float, float, float], dmg: int = 100
) -> dict:
    """A ``trigger_hurt`` brush entity spanning ``lo``..``hi``: an engine-handled kill volume
    (no custom GSC) for a lava perimeter. cod2map compiles the brush into a clipMap submodel and
    gives the entity ``model "*N"``. ``dmg`` is the per-touch damage (100 = lethal)."""
    return {
        "classname": "trigger_hurt",
        "dmg": str(dmg),
        "origin": _v((lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, lo[2]),
        "_brushes": [(lo, hi, "trigger")],
    }


def map_text(t: Terrain, boxes: list[Box], scale: int | None = None, light_grid: int = 3) -> str:
    """The whole ``.map`` (CRLF added on write)."""
    scale = scale if scale is not None else t.block
    lines = ["iwmap 4", '"000_Global" flags  active', '"The Map" flags ', "// entity 0", "{"]
    lines += [f'"{k}" "{v}"' for k, v in WORLDSPAWN]
    b = 0
    for box in boxes:
        lines.append(f"// brush {b}")
        lines += _box_brush(box, scale)
        b += 1
    for shell in seal_brushes(t):
        lines.append(f"// brush {b}")
        lines += shell
        b += 1
    lines.append(f"// brush {b}")
    lines += light_grid_brush(t)
    lines.append("}")
    ents = (
        spawn_entities(t)
        + objective_entities(t)
        + primary_lights(t, grid=light_grid)
        + compass_corners(t)
        + path_nodes(t)
    )
    for i, e in enumerate(ents, 1):
        lines.append(f"// entity {i}")
        lines.append("{")
        lines += [f'"{k}" "{v}"' for k, v in e.items() if k != "_brushes"]
        for bi, (lo, hi, mat) in enumerate(e.get("_brushes", ())):
            lines.append(f"// brush {bi}")
            lines += _axis_brush(lo, hi, mat, scale)
        lines.append("}")
    return "\n".join(lines) + "\n"
