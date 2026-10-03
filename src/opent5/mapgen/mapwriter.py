"""Assemble a Radiant ``.map`` (iwmap 4) from greedy-meshed terrain brushes plus entities.

Brush and face syntax follow tools/testmap.py and docs/research/box-map.md 1.1 (the PC tools'
``.map``): one axis-aligned brush per box, three points per face in Radiant winding, a material
and a lightmap token per face. Hidden faces are ``caulk``. The map's own block materials are
named ``mp_opent5blocks_<tile>``; its own art is supplied to the converter as overrides keyed by
the colour-map name ``~-g<material>_c`` (docs/convert.md 11, demo-box-textures.md 4). A
``lightgrid_volume`` brush fills the playable area so cod2rad bakes a grid, and a skybox encloses
the world so the sun lights it outdoors. See docs/mapgen.md.
"""

from __future__ import annotations

from .greedy import CAULK, FACES, Box
from .terrain import GRASS, SAND, Terrain

LIGHTMAP = "lightmap_gray 16384 16384 0 0 0 0"
MATERIAL_PREFIX = "mp_opent5blocks_"
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
    ("ambientintensity", "0.18"),
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


def material_name(tile: str) -> str:
    """Material name used in the ``.map`` for a tile (``caulk`` and the grid pass through)."""
    if tile in (CAULK, LIGHT_GRID, SKY):
        return tile
    return MATERIAL_PREFIX + tile


def colormap_name(tile: str) -> str:
    """The colour-map image name of a tile material (the converter override key). Named
    ``mp_opent5blocks_*`` so its ``.iwi`` obeys the custom-map file-naming rule, rather than
    the tools' usual ``~-g..._c`` convention."""
    return f"{MATERIAL_PREFIX}{tile}_c"


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


def spawn_entities(t: Terrain) -> list[dict]:
    """TDM and FFA spawns on the terrain surface, plus the global start/intermission.

    The spawn classes needed so the level does not AbortLevel (docs/convert.md 9.2): per team
    one ``mp_tdm_spawn_*_start``, and ``mp_tdm_spawn`` (TDM/koth) and ``mp_dm_spawn`` (FFA).
    """
    nx, ny, _ = t.shape
    cols = _flat_columns(t)
    cx, cy = nx // 2, ny // 2
    cols.sort(key=lambda c: (c[0] - cx) ** 2 + (c[1] - cy) ** 2)
    ents: list[dict] = []

    def at(i, j, dz=16):
        x, y, _ = t.cell_centre_world(i, j, 0)
        return (x, y, _surface_world_z(t, i, j) + dz)

    centre = cols[0] if cols else (cx, cy)
    ents.append({"classname": "info_player_start", "origin": _v(*at(*centre))})
    ents.append(
        {
            "classname": "mp_global_intermission",
            "origin": _v(*at(*centre, dz=t.block * 6)),
            "angles": "20 90 0",
        }
    )
    west = [c for c in cols if c[0] < cx]
    east = [c for c in cols if c[0] >= cx]
    for cls, pool, yaw in (
        ("mp_tdm_spawn_allies_start", west, 90),
        ("mp_tdm_spawn_axis_start", east, 270),
    ):
        for c in pool[:4] or cols[:4]:
            ents.append({"classname": cls, "origin": _v(*at(*c)), "angles": _v(0, yaw, 0)})
    # neutral TDM and FFA spawns spread across the flat columns
    spread = cols[:: max(1, len(cols) // 10)][:10] if cols else [(cx, cy)]
    for c in spread:
        for cls in ("mp_tdm_spawn", "mp_dm_spawn"):
            ents.append({"classname": cls, "origin": _v(*at(*c)), "angles": "0 0 0"})
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


def skybox_brushes(t: Terrain, thickness: int = 128, margin: int = 128) -> list[list[str]]:
    """A watertight shell enclosing the world: ``sky`` walls and ceiling (so cod2rad bakes the
    sun on what sees them), a ``caulk`` floor slab below the terrain. Walls span below the floor
    and overlap the corners, so there is no seam to leak through. The play interior
    ``[x0,y0,z0]..[x1,y1,top]`` stays open (terrain plus air)."""
    (x0, y0, z0), (x1, y1, z1) = t.world_bounds()
    xl, yl = x0 - margin, y0 - margin
    xr, yr = x1 + margin, y1 + margin
    top = z1 + margin
    zbot = z0 - thickness
    ztop = top + thickness
    s = t.block
    return [
        _axis_brush((xl, yl, top), (xr, yr, ztop), SKY, s),  # ceiling
        _axis_brush((xl, yl, zbot), (xr, yr, z0), CAULK, s),  # floor slab (under the terrain)
        _axis_brush((xl, yl, zbot), (x0, yr, ztop), SKY, s),  # -x wall (full height, corners)
        _axis_brush((x1, yl, zbot), (xr, yr, ztop), SKY, s),  # +x wall
        _axis_brush((x0, yl, zbot), (x1, y0, ztop), SKY, s),  # -y wall
        _axis_brush((x0, y1, zbot), (x1, yr, ztop), SKY, s),  # +y wall
    ]


def light_grid_brush(t: Terrain) -> list[str]:
    """A ``lightgrid_volume`` brush over the playable column band (sea level up to the sky)."""
    (x0, y0, _), (x1, y1, _) = t.world_bounds()
    zlo = t.origin[2] + t.sea_level * t.block
    zhi = t.origin[2] + t.shape[2] * t.block
    return _axis_brush((x0 + 8, y0 + 8, zlo), (x1 - 8, y1 - 8, zhi), LIGHT_GRID, 64)


def map_text(t: Terrain, boxes: list[Box], scale: int | None = None) -> str:
    """The whole ``.map`` (CRLF added on write)."""
    scale = scale if scale is not None else t.block
    lines = ["iwmap 4", '"000_Global" flags  active', '"The Map" flags ', "// entity 0", "{"]
    lines += [f'"{k}" "{v}"' for k, v in WORLDSPAWN]
    b = 0
    for box in boxes:
        lines.append(f"// brush {b}")
        lines += _box_brush(box, scale)
        b += 1
    for sky in skybox_brushes(t):
        lines.append(f"// brush {b}")
        lines += sky
        b += 1
    lines.append(f"// brush {b}")
    lines += light_grid_brush(t)
    lines.append("}")
    ents = spawn_entities(t) + compass_corners(t)
    for i, e in enumerate(ents, 1):
        lines.append(f"// entity {i}")
        lines.append("{")
        lines += [f'"{k}" "{v}"' for k, v in e.items()]
        lines.append("}")
    return "\n".join(lines) + "\n"
