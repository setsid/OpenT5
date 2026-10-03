"""``convert_map``: a PC map zone (PC Mod Tools output) into a PS3 map zone.

Strategy (docs/research/box-map.md 4.2, docs/convert.md): keep a stock PS3 map zone (the
"base", e.g. mp_nuked) with everything the PC tools cannot make for PS3 (RSX techsets,
materials, images and their .pak, configstring tables, sound banks, texture list), and
replace its world: com_map, gfx_map, game_map_mp and col_map_mp with its map_ents, under
the base's names. The base's map scripts are cut down to what a minimal map needs, its
glass panes are removed, and its lightdef keeps its own name. Everything is written by the
product writer and every offset pointer remapped (``splice.Splice``).
"""

from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass, field
from pathlib import Path

from opent5.container.fastfile import OFFSET_ZONE_NAME, ZONE_NAME_SIZE
from opent5.container.zone import Zone
from opent5.convert import lighting as lit
from opent5.convert import mapname, world
from opent5.convert import pc as pcmod
from opent5.convert import scripts as sc
from opent5.convert.splice import BASE, Splice
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
GAMETYPES = ("dm", "sab", "sd", "tdm", "ctf", "koth", "dom", "dem", "hlnd", "oic", "gun", "shrp")


@dataclass
class ConvertResult:
    content: bytes
    fastfile: bytes
    zone_name: str
    report: dict = field(default_factory=dict)


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
    lighting: str = "flat",
    require: tuple[str, ...] = ("tdm", "dm"),
    compat: bool | None = None,
    progress=None,
    name: str | None = None,
) -> ConvertResult:
    """Convert. ``pc_fastfile``: the PC ``.ff`` bytes (``IWffu100``); ``base_path``: the
    stock PS3 map zone whose world is replaced. ``lighting``: "flat" or "keep"
    (``lighting``). ``require``: gametypes whose spawns must be in the map. ``compat``:
    add the entities the base map's stock script needs (``scripts.COMPAT_ENTITIES``);
    default: only when the map keeps the base's name. ``name``: the map's own name (zone
    ``<name>.ff``, ``mapname.validate`` rules); None keeps the base's name."""
    if lighting not in ("flat", "sunlit", "keep"):
        raise ConvertError(f"lighting: expected 'flat', 'sunlit' or 'keep', found {lighting!r}")
    report: dict = {"assets": {}, "notes": []}
    _progress(progress, "Reading the PC zone")
    pc_content = pcmod.read_pc_fastfile(pc_fastfile)
    foreign = Rewrite(pc_content, platform=pcmod.PC)
    problems = foreign.xfile.problems()
    if problems:
        raise ConvertError("PC zone does not parse exactly: " + "; ".join(problems[:3]))
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
    missing = sorted({n for n in pc_names if n not in stock_words}, key=str)
    if missing:
        raise ConvertError(
            f"materials {missing}: not used by any world surface of {base_path.name}; the "
            "converter reuses the base zone's PS3 materials by name (docs/convert.md)"
        )
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
        donors = lit.find_donors(stock_gfx, decoded[1].rgba, set(pc_names))
        if lighting == "sunlit":
            sun = lit.find_donors(
                stock_gfx, decoded[1].rgba, set(pc_names), decoded[0].rgba, True, True
            )
            donors.update(sun)
            for name in sorted(set(pc_names) - set(sun), key=str):
                report["notes"].append(f"lighting: no sunlit even patch of {name}; flat donor kept")
        image = images[1]
        report["lighting"] = {
            "mode": lighting,
            "image": image.get("name"),
            "donors": {
                d.material: {
                    "stock_surface": d.surface,
                    "stock_vertex": d.vertex,
                    "lightmap_uv": [round(d.uv[0], 6), round(d.uv[1], 6)],
                    "secondary_luminance": round(d.luminance, 1),
                    "lightmap_index": d.light[0],
                    "sunlit": lighting == "sunlit" and d.material in sun,
                    "reflection_probe_index": d.light[1],
                }
                for d in donors.values()
            },
        }
    else:
        report["lighting"] = {"mode": "keep"}

    words = {name: w for name, (w, _) in stock_words.items()}
    gres = world.convert_gfx(pgfx, stock_gfx, words)
    report["notes"] += gres.notes
    surfaces = gres.node["surfaces"] or []
    for element, name in zip(surfaces, pc_names, strict=True):
        element["material"] = stock_words[name][1]
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
    if isinstance(ents, dict) and compat:
        bounds = struct.unpack_from(">6f", pgfx["header"], 0x270)
        added = sc.compat_entities(base_name, bounds[0:3], bounds[3:6])
        if added:
            newline = "\r\n" if "\r\n" in text else "\n"
            if text and not text.endswith(newline):
                text += newline
            text += sc.entity_text(added, newline)
            ents["header"], ents["entity_string"] = mapents_bytes(ents, text)
    entities = sc.parse_entities(text)
    ready = sc.gametype_report(entities, list(GAMETYPES))
    report["entities"] = {
        "added_for_the_stock_script": added,
        "count": len(entities),
        "classes": _count(e.get("classname") for e in entities),
    }
    report["gametypes"] = {g: ("ready" if not m else {"missing": m}) for g, m in ready.items()}
    for g in require:
        if ready.get(g):
            raise ConvertError(f"gametype {g}: the map lacks {ready[g]}")

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
    plan = sc.plan_scripts(base_name, texts)
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

    # Write and remap.
    _progress(progress, "Writing and remapping")
    splice = Splice(base, foreign)
    splice.origin_ranges(pgfx, "header", gres.stock_ranges, BASE)
    # Data kept from the stock GfxWorld now hangs under the converted node; pointers
    # elsewhere in the base to the replaced assets' names (the last rawfile is named by
    # the GfxWorld's base name string) follow the converted assets' name strings.
    splice.moved(BASE, stock_gfx, pgfx, (*world.STOCK_KEYS, "name", "base_name"))
    for t, node in ((AssetType.COM_MAP, pcom), (AssetType.GAME_MAP_MP, pgame)):
        splice.moved(BASE, stock_nodes[t], node, ("name",))
    splice.moved(BASE, stock_nodes[AssetType.COL_MAP_MP], pclip, ("name",))
    stock_ents = stock_nodes[AssetType.COL_MAP_MP].get("map_ents")
    if isinstance(stock_ents, dict) and isinstance(ents, dict):
        splice.moved(BASE, stock_ents, ents, ("name",))
    for element in gres.stock_nodes:
        splice.origin(element, BASE)
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
    return ConvertResult(result.content, built.data, zone.name, report)


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
