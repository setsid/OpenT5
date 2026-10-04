"""``convert_map``: a PC map zone (PC Mod Tools output) into a PS3 map zone.

Strategy (docs/research/box-map.md 4.2, docs/convert.md): keep a stock PS3 map zone (the
"base", e.g. mp_nuked) with everything the PC tools cannot make for PS3 (RSX techsets,
materials, images and their .pak, configstring tables, sound banks, texture list), and
replace its world: com_map, gfx_map, game_map_mp and col_map_mp with its map_ents, under
the base's names. The base's map scripts are cut down to what a minimal map needs, its
glass panes are removed, and its lightdef keeps its own name. Materials the base lacks are
built into the map's zone (``materials``), static models place the base's XModels by name
(``smodels``), and every gametype's objectives are checked (``entities``). Everything is
written by the product writer and every offset pointer remapped (``splice.Splice``).
"""

from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass, field
from pathlib import Path

from opent5.container.fastfile import OFFSET_ZONE_NAME, ZONE_NAME_SIZE
from opent5.container.zone import Zone
from opent5.convert import compass, mapname, pathlinks, smodels, world
from opent5.convert import entities as ent
from opent5.convert import lighting as lit
from opent5.convert import materials as mats
from opent5.convert import pc as pcmod
from opent5.convert import scripts as sc
from opent5.convert.splice import BASE, FOREIGN, Splice
from opent5.convert.world import ConvertError
from opent5.edit.content import mapents_bytes, mapents_text, rawfile_bytes, rawfile_text
from opent5.xfile import AssetType, parse, write
from opent5.xfile.constants import PTR_INLINE
from opent5.xfile.events import EventKind, PtrKind
from opent5.xfile.remap import Rewrite

WORLD_TYPES = (
    AssetType.COM_MAP,
    AssetType.GFX_MAP,
    AssetType.GAME_MAP_MP,
    AssetType.COL_MAP_MP,
)
LIGHTING = ("baked", "flat", "sunlit", "keep")
GAMETYPES = ent.GAMETYPES


@dataclass
class ConvertResult:
    content: bytes
    fastfile: bytes
    zone_name: str
    report: dict = field(default_factory=dict)
    #: The compass image drawn for the map (RGBA), when one was made.
    compass: object = None


def _one(xfile, asset_type: int, what: str):
    found = [a for a in xfile.assets if a.type == asset_type]
    if len(found) != 1:
        raise ConvertError(
            f"{what}: expected one {AssetType(asset_type).name.lower()} asset, found {len(found)}"
        )
    return found[0]


def _progress(progress, stage: str) -> None:
    if progress is not None:
        progress(stage, 0, 1)


def convert_map(
    pc_fastfile: bytes,
    base_path: str | Path,
    lighting: str = "baked",
    require: tuple[str, ...] = ("tdm", "dm"),
    compat: bool | None = None,
    progress=None,
    name: str | None = None,
    with_compass: bool = True,
    image_roots=(),
    shared_zones=None,
    force_materials: frozenset[str] | bool = frozenset(),
    overrides: dict | None = None,
    compass_image=None,
) -> ConvertResult:
    """Convert. ``pc_fastfile``: the PC ``.ff`` bytes (``IWffu100``); ``base_path``: the
    stock PS3 map zone whose world is replaced. ``lighting``: "baked" (the PC map's own
    cod2rad lightmaps, probes and outdoor image, ``lightmaps``), or "flat", "sunlit", "keep"
    (the base's lightmaps, ``lighting``). ``require``: gametypes that must be ready
    (``entities.check``: spawns and objectives); the others are reported only.
    ``compat``: add the entities the base map's stock script needs
    (``scripts.COMPAT_ENTITIES``); default: only when the map keeps the base's name.
    ``name``: the map's own name (zone ``<name>.ff``, ``mapname.validate`` rules); None
    keeps the base's name. ``with_compass``: add the compass material and image
    (``compass``). ``image_roots``: PC game folders holding the ``.iwi`` of materials the
    base lacks; ``shared_zones``: the always-loaded zones whose images a new material
    references by placeholder (default: code_post_gfx_mp and common_mp beside the base or
    in .env); ``force_materials``: material names (or True) built anew even when the base
    has them; ``overrides``: image name -> RGBA drawn instead of the PC pixels
    (``materials.convert_materials``)."""
    if lighting not in LIGHTING:
        raise ConvertError(f"lighting: expected one of {LIGHTING}, found {lighting!r}")
    unknown = [g for g in require if g not in GAMETYPES]
    if unknown:
        raise ConvertError(f"gametypes: expected some of {GAMETYPES}, found {unknown}")
    report: dict = {"assets": {}, "notes": []}
    _progress(progress, "Reading the PC zone")
    pc_content = pcmod.read_pc_fastfile(pc_fastfile)
    foreign = Rewrite(pc_content, platform=pcmod.PC)
    problems = foreign.xfile.problems()
    if problems:
        raise ConvertError(
            "PC zone does not parse exactly, so the converter will not write a zone it cannot "
            "reproduce (no output written): " + "; ".join(problems[:3])
        )
    report["pc"] = {
        "content_bytes": len(pc_content),
        "assets": [[a.index, a.type_name, a.name] for a in foreign.xfile.assets],
    }

    _progress(progress, "Reading the base zone")
    base_path = Path(base_path)
    zone = Zone.open(base_path)
    base = Rewrite(bytes(zone.content))
    if base.xfile.problems():
        raise ConvertError(
            "base zone does not parse exactly: " + "; ".join(base.xfile.problems()[:3])
        )
    bx = base.xfile

    pcom = _one(foreign.xfile, AssetType.COM_MAP, "PC zone").data
    pgfx = _one(foreign.xfile, AssetType.GFX_MAP, "PC zone").data
    pgame = _one(foreign.xfile, AssetType.GAME_MAP_MP, "PC zone").data
    pclip = _one(foreign.xfile, AssetType.COL_MAP_MP, "PC zone").data
    targets = {t: _one(bx, t, "base zone") for t in WORLD_TYPES}
    stock_gfx = targets[AssetType.GFX_MAP].data
    stock_com = targets[AssetType.COM_MAP].data
    map_name = stock_com.get("name")
    base_name = stock_gfx.get("base_name")
    if not map_name or not base_name:
        raise ConvertError("base zone: its com_map / gfx_map carry no map name")
    own = name if name is not None and name != base_name else None
    renamed = own is not None
    if own is not None:
        mapname.validate(own, base_name)
    if compat is None:
        compat = not renamed

    # Materials and lighting.
    _progress(progress, "Converting the world")
    stock_words = world.stock_material_words(stock_gfx)
    pc_names = [world.surface_material_name(e) for e in pgfx.get("surfaces") or []]
    if force_materials is True:
        new_names = set(pc_names)
    else:
        new_names = {n for n in pc_names if n not in stock_words or n in force_materials}
    shared: set[str] = set()
    if new_names:
        if shared_zones is None:
            shared_zones = _shared_zone_paths(base_path)
        shared = mats.shared_image_names(shared_zones)
    mres = mats.convert_materials(
        pgfx,
        stock_gfx,
        base,
        files=mats.ImageFiles(image_roots),
        shared_images=shared,
        force=force_materials,
        overrides=overrides,
    )
    report["materials"] = mres.report
    own_materials = {n for n, s in mres.sources.items() if s == "foreign"}
    taken = smodels.take(pgfx)
    donors = None
    sun: dict = {}
    if lighting in ("flat", "sunlit") and pc_names:
        from opent5 import env
        from opent5.edit.images import read as read_image

        dirs = [base_path.parent, *env.zone_dirs()]
        images = stock_gfx["lightmaps"][0]
        decoded = [read_image(images[k], zone.name, dirs) for k in (0, 1)]
        for d in decoded:
            if d.rgba is None:
                raise ConvertError(f"lighting: a base lightmap does not decode ({d.reason})")
        lit_names = set(pc_names) - own_materials
        donors = lit.find_donors(stock_gfx, decoded[1].rgba, lit_names)
        if lighting == "sunlit":
            sun = lit.find_donors(
                stock_gfx, decoded[1].rgba, lit_names, decoded[0].rgba, True, True
            )
            donors.update(sun)
            for name in sorted(lit_names - set(sun), key=str):
                report["notes"].append(f"lighting: no sunlit even patch of {name}; flat donor kept")
        _own_donors(donors, own_materials, stock_gfx, decoded[1].rgba, report["notes"])
        image = images[1]
        report["lighting"] = {
            "mode": lighting,
            "image": image.get("name"),
            "donors": {
                material: {
                    "stock_material": d.material,
                    "stock_surface": d.surface,
                    "stock_vertex": d.vertex,
                    "lightmap_uv": [round(d.uv[0], 6), round(d.uv[1], 6)],
                    "secondary_luminance": round(d.luminance, 1),
                    "lightmap_index": d.light[0],
                    "sunlit": lighting == "sunlit" and material in sun,
                    "reflection_probe_index": d.light[1],
                }
                for material, d in donors.items()
            },
        }
    else:
        report["lighting"] = {"mode": lighting}

    words = {name: w for name, (w, _) in stock_words.items()}
    words.update(mres.words)
    gres = world.convert_gfx(pgfx, stock_gfx, words, baked=lighting == "baked")
    report["notes"] += gres.notes
    report["lighting"].update(gres.lighting)
    surfaces = gres.node["surfaces"] or []
    for element, name in zip(surfaces, pc_names, strict=True):
        element["material"] = None if name in own_materials else stock_words[name][1]
    if donors is not None and surfaces:
        layer = bytearray(gres.node["vertex_layer_data"])
        strides = {g.first: (g.layer, g.stride) for g in gres.groups}
        records = lit.apply_flat(layer, [e["raw"] for e in surfaces], pc_names, strides, donors)
        for element, rec in zip(surfaces, records, strict=True):
            element["raw"] = rec
        gres.node["vertex_layer_data"] = bytes(layer)
    world.convert_com(pcom)
    world.convert_game(pgame)
    world.convert_clip(pclip)
    # Path-node links: cod2map does not connect the nodes and the stock Connect Paths step runs
    # the PC game, so a generated map's PathData has no links. The retail engine does not link at
    # load; it reads the baked links, so an unlinked map drops with "Path nodes are not
    # connected." Bake the links here, in the stock format, as one connected component. A real
    # map keeps its own baked links (pathlinks.has_links).
    node_count = struct.unpack_from(">I", pgame["header"], 4)[0]
    report["path_nodes"] = {"nodes": node_count}
    if node_count:
        if pathlinks.has_links(pgame):
            report["path_nodes"]["links"] = "kept (the map's own)"
        else:
            try:
                report["path_nodes"] = pathlinks.generate(pgame)
            except ValueError as e:
                raise ConvertError(str(e)) from e
    model_words = smodels.model_words(bx)
    sres = smodels.convert_static_models(taken, model_words, foreign.xfile)
    clip_models = smodels.repoint_clip(pclip, model_words, foreign.xfile)
    report["static_models"] = {**sres.report, "collision": len(clip_models)}

    # Names: the base's, or the map's own (the game finds the world as
    # maps/mp/<mapname>.d3dbsp, 0x4b5370).
    ents = pclip.get("map_ents")
    if renamed:
        world_name = f"maps/mp/{own}.d3dbsp"
        pcom["name"] = pgfx["name"] = pgame["name"] = pclip["name"] = world_name
        pgfx["base_name"] = own
        if isinstance(ents, dict):
            ents["name"] = world_name
    else:
        pcom["name"] = map_name
        pgfx["name"] = targets[AssetType.GFX_MAP].data.get("name")
        pgfx["base_name"] = base_name
        pgame["name"] = targets[AssetType.GAME_MAP_MP].data.get("name")
        pclip["name"] = targets[AssetType.COL_MAP_MP].data.get("name")
        if isinstance(ents, dict):
            stock_ents = targets[AssetType.COL_MAP_MP].data.get("map_ents")
            ents["name"] = stock_ents.get("name") if isinstance(stock_ents, dict) else map_name
    stock_nodes = {t: targets[t].data for t in WORLD_TYPES}
    for t, node in zip(WORLD_TYPES, (pcom, pgfx, pgame, pclip), strict=True):
        a = targets[t]
        before = a.size
        a.data = node
        report["assets"][a.type_name] = {
            "index": a.index,
            "action": "replaced by the converted PC asset",
            "base_bytes": before,
        }

    # Entities and gametypes.
    text = mapents_text(ents) if isinstance(ents, dict) else ""
    added = []
    bounds = struct.unpack_from(">6f", pgfx["header"], 0x270)
    if isinstance(ents, dict) and compat:
        added = sc.compat_entities(base_name, bounds[0:3], bounds[3:6])
    compat_added = list(added)
    # The compass needs exactly two minimap_corner entities (compass.py).
    corners_found = [e for e in sc.parse_entities(text) if e.get("targetname") == compass.CORNER]
    nw, se = compass.corners(bounds[0:3], bounds[3:6])
    corner_note = "the PC map's own"
    if not corners_found:
        minimap = compass.corner_entities(nw, se, bounds[2])
        added = added + minimap
        corner_note = "added: a square around the world bounds plus 64 units"
    elif len(corners_found) == 2:
        pts = [tuple(float(v) for v in e["origin"].split()[:2]) for e in corners_found]
        nw = (max(p[0] for p in pts), max(p[1] for p in pts))
        se = (min(p[0] for p in pts), min(p[1] for p in pts))
    else:
        raise ConvertError(
            f"entities: expected 0 or 2 {compass.CORNER!r} entities (maps/mp/_compass.gsc "
            f"needs exactly two), found {len(corners_found)}"
        )
    if isinstance(ents, dict) and added:
        newline = "\r\n" if "\r\n" in text else "\n"
        if text and not text.endswith(newline):
            text += newline
        text += sc.entity_text(added, newline)
        ents["header"], ents["entity_string"] = mapents_bytes(ents, text)
    entities = sc.parse_entities(text)
    objectives = ent.zone_report(entities, pclip, bx, own or base_name)
    report["objectives"] = objectives
    report["entities"] = {
        "added_for_the_stock_script": compat_added,
        "minimap_corners": {"north_west": list(nw), "south_east": list(se), "source": corner_note},
        "count": len(entities),
        "classes": _count(e.get("classname") for e in entities),
    }
    report["gametypes"] = {
        g: ("ready" if r["ready"] else {"missing": r["missing"]}) for g, r in objectives.items()
    }
    for g in require:
        if not objectives[g]["ready"]:
            raise ConvertError(f"gametype {g}: " + "; ".join(objectives[g]["missing"]))

    # The lightdef's name pointed into the replaced ComWorld: make it its own.
    for a in bx.by_type(AssetType.LIGHTDEF):
        h = bytearray(a.data["header"])
        if struct.unpack_from(">I", h, 0)[0] != PTR_INLINE:
            h[0:4] = struct.pack(">I", PTR_INLINE)
            a.data["header"] = bytes(h)
            report["assets"].setdefault("lightdef", {"index": a.index})["action"] = (
                f"name {a.data.get('name')!r} stored inline (it pointed into the replaced com_map)"
            )

    # Glass: the base's panes stand at its own positions; remove them.
    for a in bx.by_type(AssetType.GLASSES):
        h = bytearray(a.data["header"])
        count = struct.unpack_from(">I", h, 4)[0]
        struct.pack_into(">II", h, 4, 0, 0)
        a.data["header"] = bytes(h)
        a.data["glasses"] = None
        report["assets"]["glasses"] = {
            "index": a.index,
            "action": f"numGlasses {count} -> 0, glasses pointer -> 0",
        }

    # Scripts.
    texts = {}
    nodes = {}
    for a in bx.by_type(AssetType.RAWFILE):
        if a.name and a.name.endswith((".gsc", ".csc")):
            texts[a.name] = rawfile_text(a.data)
            nodes[a.name] = a
    plan = sc.plan_scripts(base_name, texts, own_map=renamed)
    changed = {}
    for name, new in plan.texts.items():
        a = nodes[name]
        header, buffer = rawfile_bytes(a.data, new)
        a.data["header"] = header
        a.data["buffer"] = buffer
        changed[name] = {
            "index": a.index,
            "bytes_before": len(texts[name]),
            "bytes_after": len(new),
        }
    report["scripts"] = {"changed": changed, "main_kept": plan.kept, "main_dropped": plan.dropped}
    if renamed:
        names = mapname.rename_assets(bx, base_name, own, mapname.name_pointer_nodes(base))
        report["map_name"] = {
            "base": base_name,
            "name": own,
            "world": f"maps/mp/{own}.d3dbsp",
            "gfx_base_name": own,
            **names.to_dict(),
        }

    # Compass: a material and image of the map's own, inside its zone (compass.py).
    compass_node = None
    compass_rgba = None
    if with_compass:
        stock_cpg = _code_post_gfx(base_path)
        if stock_cpg is None:
            report["notes"].append(
                "compass: code_post_gfx_mp.ff not found beside the base or in .env; no compass"
            )
        else:
            label = own if renamed else base_name
            compass_node, compass_rgba, report["compass"] = compass.add_to_zone(
                bx, stock_cpg, label, base_name, pgfx, nw, se, override_rgba=compass_image
            )
            if renamed:
                report["compass"]["script"] = _point_script_at(
                    bx, own, compass.material_name(base_name), compass.material_name(own)
                )
            else:
                report["compass"]["script"] = (
                    "unchanged: the level script names compass_map_" + base_name + "; the "
                    "zone's material of that name replaces code_post_gfx_mp's while the map is "
                    "loaded (zone rank 10 over 1, compass.py; INFERRED until run)"
                )

    # Write and remap.
    _progress(progress, "Writing and remapping")
    splice = Splice(base, foreign)
    splice.origin_ranges(pgfx, "header", gres.stock_ranges, BASE)
    # Data kept from the stock GfxWorld now hangs under the converted node; pointers
    # elsewhere in the base to the replaced assets' names (the last rawfile is named by
    # the GfxWorld's base name string) follow the converted assets' name strings.
    splice.moved(BASE, stock_gfx, pgfx, (*gres.stock_keys, "name", "base_name"))
    for t, node in ((AssetType.COM_MAP, pcom), (AssetType.GAME_MAP_MP, pgame)):
        splice.moved(BASE, stock_nodes[t], node, ("name",))
    splice.moved(BASE, stock_nodes[AssetType.COL_MAP_MP], pclip, ("name",))
    stock_ents = stock_nodes[AssetType.COL_MAP_MP].get("map_ents")
    if isinstance(stock_ents, dict) and isinstance(ents, dict):
        splice.moved(BASE, stock_ents, ents, ("name",))
    for element in gres.stock_nodes:
        splice.origin(element, BASE)
    for element in gres.new_nodes:
        splice.origin(element, FOREIGN)
    if compass_node is not None:
        splice.origin(compass_node, BASE)
    mats.attach(mres, pgfx, pc_names, surfaces, splice)
    smodels.place(pgfx, pclip, sres, splice)
    result = splice.build(check=True, progress=progress)
    report["pointers"] = {
        "via_base": result.via["base"],
        "via_pc": result.via["foreign"],
        "rewritten": result.rewritten,
    }

    _progress(progress, "Checking")
    report["checks"] = check_content(result.content)
    zone.content[:] = result.content
    if renamed:
        zone.header[OFFSET_ZONE_NAME : OFFSET_ZONE_NAME + ZONE_NAME_SIZE] = own.encode(
            "ascii"
        ).ljust(ZONE_NAME_SIZE, b"\0")
    built = zone.build()
    report["zone"] = {
        "name": zone.name,
        "content_bytes": len(result.content),
        "header": [hex(v) for v in result.header],
        "fastfile_bytes": len(built.data),
        "sha1": hashlib.sha1(built.data).hexdigest(),
        "chunks_carried": built.kept,
        "chunks_deflated": built.deflated,
        "signature": "no longer matches: loads only on a client with the signature check "
        "patched out",
    }
    return ConvertResult(result.content, built.data, zone.name, report, compass_rgba)


def _shared_zone_paths(base_path: Path) -> list[Path]:
    """code_post_gfx_mp and common_mp, beside the base or in the .env folders."""
    from opent5 import env

    out = []
    for zone in mats.SHARED_ZONES:
        for folder in (base_path.parent, *env.zone_dirs()):
            path = folder / f"{zone}.ff"
            if path.is_file():
                out.append(path)
                break
        else:
            raise ConvertError(
                f"materials: the map needs materials of its own, and their images are checked "
                f"against {zone}.ff; expected it beside the base or in .env, found none"
            )
    return out


def _own_donors(donors: dict, own: set[str], stock_gfx: dict, secondary, notes: list) -> None:
    """flat / sunlit: a material of the map's own has no stock surface to take its light
    from; it takes the brightest donor found for the others (or for any stock material)."""
    if not own:
        return
    pool = donors or lit.find_donors(
        stock_gfx,
        secondary,
        set(world.stock_material_words(stock_gfx)),
        allow_missing=True,
    )
    if not pool:
        raise ConvertError("lighting: no uniformly lit stock surface; use --lighting keep")
    best = max(pool.values(), key=lambda d: (d.luminance, d.material))
    for name in sorted(own):
        donors[name] = best
        notes.append(f"lighting: {name} is the map's own material; lit like {best.material}")


def _code_post_gfx(base_path: Path):
    """The parsed code_post_gfx_mp (holds the stock compass materials), or None."""
    from opent5 import env

    for folder in (base_path.parent, *env.zone_dirs()):
        path = folder / "code_post_gfx_mp.ff"
        if path.is_file():
            return parse(bytes(Zone.open(path).content), log=False)
    return None


def _point_script_at(xfile, own: str, old: str, new: str) -> str:
    """The map's own level script calls setupMiniMap with its own material."""
    script = f"maps/mp/{own}.gsc"
    for a in xfile.by_type(AssetType.RAWFILE):
        if a.data.get("name") == script:
            text = rawfile_text(a.data)
            call = f'setupMiniMap("{old}")'
            if call not in text:
                return f"{script}: no {call}; unchanged"
            changed = text.replace(call, f'setupMiniMap("{new}")')
            a.data["header"], a.data["buffer"] = rawfile_bytes(a.data, changed)
            return f'{script}: setupMiniMap("{new}")'
    return f"{script}: not found; unchanged"


def _count(values) -> dict:
    out: dict = {}
    for v in values:
        out[v] = out.get(v, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: str(kv[0])))


def check_content(content: bytes) -> dict:
    """The offline checks: exact reparse, write(parse) identical, every offset and alias
    pointer resolving."""
    x = parse(content, log=True)
    problems = x.problems()
    rewritten = write(x, log=False).content
    rows = x.log.of_kind(EventKind.POINTER)
    unresolved = 0
    movable = 0
    for row in rows:
        kind, raw = int(row[3]), int(row[2])
        if kind in (PtrKind.OFFSET, PtrKind.ALIAS_REF):
            movable += 1
            if x.resolve(raw) is None:
                unresolved += 1
    return {
        "reparse_exact": not problems,
        "problems": problems,
        "write_identical": rewritten == content,
        "assets": len(x.assets),
        "offset_and_alias_pointers": movable,
        "unresolved": unresolved,
    }
