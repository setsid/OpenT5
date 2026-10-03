"""Dump a whole zone to an output folder in open formats.

    from opent5.export.zone import ZoneExporter
    manifest = ZoneExporter(content, "mp_nuked", out_dir, pak_dirs).run()

Layout of the output folder (every path is relative to it):

    manifest.json                 every asset -> files written, counts, failures
    images/<image>.png            GfxImage level 0 (cube: _px.._nz faces)
    materials/<material>.json     techset, texture slots -> image names, constants, state bits
    techsets/<techset>.json       techniques, passes, shader names, arguments
    shaders/<shader>.vs.bin|.ps.bin + shaders/index.json
    world/<zone>.obj/.mtl         every GfxWorld surface, one group per surface
    world/static_models.json      placements; world/<zone>_static_models.obj instanced LOD0
    collision/<zone>_brushes.obj, <zone>_triangles.obj, collision.json
    map_ents/<zone>.ents, map_ents/entities.json
    game_map/paths.json, com_map/lights.json, lightdefs/<name>.json
    xmodels/<model>/<model>.obj/.mtl, bones.json, model.json
    rawfiles/<path as named>, stringtables/<name>.csv, localize/localize.json
    <type>/<name>.json            everything else, generically (fx, sound, weapon, xanim, ...)
    previews/*.png                renders of the world and collision (optional)
"""

from __future__ import annotations

import csv
import io
import math
import time
from pathlib import Path
from typing import Any

import numpy as np

from opent5.export import collision as col
from opent5.export import vertex as vx
from opent5.export.entities import entity_text, parse_entities, vector
from opent5.export.images import ImageError, PakSet, decode_image
from opent5.export.jsonable import Jsonifier, NameTable, clean_float, hexs, safe_name, write_json
from opent5.export.nodes import AssetIndex, Resolver, classify
from opent5.export.obj import ObjGroup, ObjMesh, mtl_text, write_obj
from opent5.formats import texture as tx
from opent5.xfile import parse
from opent5.xfile.constants import AssetType as T
from opent5.xfile.constants import type_name
from opent5.xfile.schema import view

#: Material texture slot names, matched by R_HashString (djb2 with XOR, each byte | 0x20,
#: start 0; docs/extract.md 4). Names confirmed by their hash and first/last characters
#: in mp_nuked.
SAMPLER_NAMES = [
    "colorMap",
    "normalMap",
    "specularMap",
    "detailMap",
    "colorMap1",
    "colorMap2",
    "colorMap3",
    "normalMap1",
    "normalMap2",
    "specularMap1",
    "specularMap2",
    "Detail_Map",
    "Normal_Map",
    "Specular_Map",
    "colorDetailMap",
    "occlusionMap",
    "outdoorMap",
    "reflectionProbe",
    "lightmapSecondary",
    "lut2D",
    "lut3D",
]


def r_hash(name: str) -> int:
    h = 0
    for c in name.encode("latin-1"):
        h = ((h * 33) ^ (c | 0x20)) & 0xFFFFFFFF
    return h


SAMPLER_BY_HASH = {r_hash(n): n for n in SAMPLER_NAMES}
MTL_SLOTS = {"colorMap": "map_Kd", "normalMap": "map_bump", "specularMap": "map_Ks"}

#: GfxImage pixels flag at +0x1b; semantic names (PC TextureSemantic order).
SEMANTICS = {0: "2d", 1: "function", 2: "colour", 5: "normal", 8: "specular", 11: "water"}

NODE_TYPES = (
    "badnode pathnode cover_stand cover_crouch cover_crouch_window cover_prone cover_right "
    "cover_left cover_wide_right cover_wide_left cover_pillar concealment_stand "
    "concealment_crouch concealment_prone reacquire balcony scripted negotiation_begin "
    "negotiation_end turret guard"
).split()


def png_bytes(rgba: np.ndarray) -> bytes:
    return tx.write_png(np.ascontiguousarray(rgba, np.uint8))


def axes_to_angles(axes: np.ndarray) -> list[float]:
    """Row axes (forward, left, up) -> pitch, yaw, roll in degrees (the game's AxisToAngles)."""
    f, left, up = axes
    yaw = math.degrees(math.atan2(f[1], f[0]))
    pitch = math.degrees(math.atan2(-f[2], math.hypot(f[0], f[1])))
    roll = math.degrees(math.atan2(left[2], up[2]))
    return [round(pitch, 4), round(yaw, 4), round(roll, 4)]


def orthonormal(axes: np.ndarray) -> np.ndarray:
    """Clean up three packed (11:11:10) axes into a right-handed orthonormal frame."""
    f = vx.normalise(axes[0])
    up = axes[2] - f * float(np.dot(axes[2], f))
    up = vx.normalise(up)
    left = np.cross(up, f)
    return np.array([f, left, up], np.float64)


class ZoneExporter:
    def __init__(
        self,
        content: bytes,
        zone_name: str,
        out_dir: Path,
        pak_dirs: list[Path] | None = None,
        *,
        images: bool = True,
        models: bool = True,
        instance_models: bool = True,
        previews: bool = True,
        log=print,
    ):
        self.content = bytes(content)
        self.zone = zone_name
        self.out = Path(out_dir)
        self.paks = PakSet(zone_name, pak_dirs or [])
        self.do_images = images
        self.do_models = models
        self.do_instances = instance_models
        self.do_previews = previews
        self.log = log
        self.names = NameTable()
        self.manifest: dict[str, Any] = {"zone": zone_name, "assets": [], "failures": []}
        self.image_files: dict[str, str] = {}  # image name -> primary png path
        self.material_maps: dict[str, dict[str, str | None]] = {}
        self.model_meshes: dict[str, list[tuple[str, vx.Mesh]]] = {}

    # -- bookkeeping --------------------------------------------------------------------------

    def record(self, asset_type: int | str, name: str | None, files: list[str], **extra) -> None:
        entry = {
            "type": asset_type if isinstance(asset_type, str) else type_name(asset_type),
            "name": name,
            "files": files,
        }
        if name and name.startswith(","):
            entry["reference"] = True  # defined in another zone; only a stub is here
        entry.update(extra)
        self.manifest["assets"].append(entry)

    def fail(self, asset_type: int | str, name: str | None, reason: str) -> None:
        tname = asset_type if isinstance(asset_type, str) else type_name(asset_type)
        self.manifest["failures"].append({"type": tname, "name": name, "reason": reason})

    def rel(self, path: Path) -> str:
        return path.relative_to(self.out).as_posix()

    def file_for(self, folder: str, name: str, suffix: str) -> Path:
        return self.out / folder / (self.names.get(folder, name) + suffix)

    # -- main ---------------------------------------------------------------------------------

    def run(self) -> dict:
        started = time.perf_counter()
        self.out.mkdir(parents=True, exist_ok=True)
        self.xfile = parse(self.content, log=False)
        problems = self.xfile.problems()
        if problems:
            self.manifest["parse_problems"] = problems
        self.index = AssetIndex(self.xfile)
        self.resolver = Resolver(self.index, self.xfile)
        self.json = Jsonifier(self.resolver, classify)
        self.script_strings = self.xfile.script_strings
        steps = [
            ("images", self.export_images),
            ("shaders", self.export_shaders),
            ("techsets", self.export_techsets),
            ("materials", self.export_materials),
            ("xmodels", self.export_xmodels),
            ("world", self.export_world),
            ("collision", self.export_collision),
            ("map_ents", self.export_map_ents),
            ("game_map", self.export_game_map),
            ("com_map", self.export_com_map),
            ("content", self.export_content),
            ("generic", self.export_generic),
        ]
        timings = {}
        for label, step in steps:
            t0 = time.perf_counter()
            self.log(f"{label} ...")
            step()
            timings[label] = round(time.perf_counter() - t0, 2)
        if self.do_previews:
            t0 = time.perf_counter()
            self.export_previews()
            timings["previews"] = round(time.perf_counter() - t0, 2)
        counts: dict[str, int] = {}
        for a in self.manifest["assets"]:
            counts[a["type"]] = counts.get(a["type"], 0) + 1
        self.manifest["counts"] = {
            "zone_asset_list": self.xfile.counts(),
            "assets_found_including_inline": self.index.counts(),
            "exported": counts,
            "failures": len(self.manifest["failures"]),
            "files": sum(len(a["files"]) for a in self.manifest["assets"]),
        }
        self.manifest["timings_s"] = timings
        self.manifest["seconds"] = round(time.perf_counter() - started, 1)
        write_json(self.out / "manifest.json", self.manifest)
        return self.manifest

    # -- images -------------------------------------------------------------------------------

    def export_images(self) -> None:
        for name, node in sorted(self.index.of(T.IMAGE).items()):
            base = self.names.get("images", name)
            f = view(node).fields
            info = {
                "format": tx.format_name(f["texture.format"]),
                "width": f["texture.width"],
                "height": f["texture.height"],
                "depth": f["texture.depth"],
                "mips": f["texture.mipmap"],
                "cube": bool(f["texture.cubemap"]),
                "semantic": SEMANTICS.get(f["semantic"], f["semantic"]),
            }
            if not self.do_images:
                self.image_files[name] = f"images/{base}.png"
                continue
            try:
                decoded = decode_image(node, self.paks)
            except ImageError as exc:
                self.fail(T.IMAGE, name, str(exc))
                self.record(T.IMAGE, name, [], **info, error=str(exc))
                continue
            files = []
            for suffix, rgba in decoded.layers:
                path = self.out / "images" / f"{base}{suffix}.png"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(png_bytes(rgba))
                files.append(self.rel(path))
            self.image_files[name] = files[0]
            info.update(source=decoded.source, exported_size=[decoded.width, decoded.height])
            if decoded.notes:
                info["notes"] = decoded.notes
            self.record(T.IMAGE, name, files, **info)
        self.paks.close()

    # -- shaders, techsets, materials -----------------------------------------------------------

    def export_shaders(self) -> None:
        index = {}
        for asset_type, ext in ((T.VERTEXSHADER, ".vs.bin"), (T.PIXELSHADER, ".ps.bin")):
            for name, node in sorted(self.index.of(asset_type).items()):
                program = node.get("program")
                files = []
                if program is not None:
                    path = self.file_for("shaders", f"{name}{ext}", "")
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(bytes(program))
                    files.append(self.rel(path))
                index.setdefault(type_name(asset_type), {})[name] = {
                    "program_words": view(node).fields.programWords,
                    "file": files[0] if files else None,
                    "magic": bytes(program[:4]).decode("latin-1") if program else None,
                }
                self.record(asset_type, name, files)
        write_json(self.out / "shaders" / "index.json", index)

    def technique_json(self, index: int, tech: dict) -> dict:
        passes = []
        for p in tech.get("passes") or []:
            pf = view(p).fields
            args = []
            for a in p.get("args") or []:
                af = view(a).fields
                arg = {"type": af.type, "dest": af.dest, "value": f"{af.u:#010x}"}
                if a.get("literal") is not None:
                    lit = np.frombuffer(bytes(a["literal"]), ">f4")
                    arg["literal"] = [clean_float(float(x)) for x in lit]
                args.append(arg)
            passes.append(
                {
                    "vertex_decl": hexs(p.get("vertex_decl")),
                    "vertex_shader": self.resolver.name(p.get("vertex_shader")),
                    "pixel_shader": self.resolver.name(p.get("pixel_shader")),
                    "per_prim_arg_count": pf.perPrimArgCount,
                    "per_obj_arg_count": pf.perObjArgCount,
                    "stable_arg_count": pf.stableArgCount,
                    "args": args,
                }
            )
        return {
            "index": index,
            "name": tech.get("name"),
            "flags": view(tech).struct("head").flags,
            "passes": passes,
        }

    def export_techsets(self) -> None:
        for name, node in sorted(self.index.of(T.TECHSET).items()):
            v = view(node, self.xfile)
            shared = v.target("techniques")
            techniques = []
            for i, tech in enumerate(node["techniques"]):
                if isinstance(tech, dict):
                    techniques.append(self.technique_json(i, tech))
                elif shared[i] is not None:
                    # A technique loaded earlier (shared by offset pointer).
                    techniques.append(
                        {
                            "index": i,
                            "shared": shared[i].name,
                            "pointer": f"{v.fields.techniques[i]:#010x}",
                        }
                    )
            path = self.file_for("techsets", name, ".json")
            header = bytes(node["header"])
            write_json(
                path,
                {
                    "name": name,
                    "world_vert_format": v.fields.worldVertFormat,
                    "flags": header[4:8].hex(),
                    "techniques": techniques,
                },
            )
            self.record(T.TECHSET, name, [self.rel(path)])

    def export_materials(self) -> None:
        for name, node in sorted(self.index.of(T.MATERIAL).items()):
            textures = []
            maps: dict[str, str | None] = {}
            for t in node.get("textures") or []:
                tf = view(t).fields
                slot = SAMPLER_BY_HASH.get(tf.nameHash)
                semantic = tf.semantic
                entry = {
                    "slot": slot,
                    "name_hash": f"{tf.nameHash:#010x}",
                    "name_first_last": chr(tf.nameStart) + chr(tf.nameEnd),
                    "sampler_state": tf["samplerState.bits_filter_mipMap_clampU_clampV_clampW"],
                    "semantic": SEMANTICS.get(semantic, semantic),
                }
                if semantic == 11:
                    w = t.get("water")
                    image = self.resolver.name(w.get("image")) if isinstance(w, dict) else None
                    if isinstance(w, dict) and w.get("raw") is not None:
                        wf = view(w).fields
                        entry["water"] = {"M": wf.M, "N": wf.N}
                else:
                    image = self.resolver.name(t.get("image"))
                entry["image"] = image
                entry["file"] = self.image_files.get(image) if image else None
                if slot in MTL_SLOTS and entry["file"] and MTL_SLOTS[slot] not in maps:
                    maps[MTL_SLOTS[slot]] = entry["file"]
                textures.append(entry)
            self.material_maps[name] = maps
            v = view(node)
            constants = []
            if node.get("constants") is not None:
                for c in v.array("constants"):
                    constants.append(
                        {
                            "name": c["name"].split(b"\0", 1)[0].decode("latin-1"),
                            "name_hash": f"{int(c['nameHash']):#010x}",
                            "literal": [clean_float(float(x)) for x in c["literal"]],
                        }
                    )
            state_bits = [
                hexs(sb.get("bits") if sb.get("bits") is not None else sb.get("raw"))
                for sb in node.get("state_bits") or []
            ]
            f = v.fields
            header = bytes(node["header"])
            out = {
                "name": name,
                "technique_set": self.resolver.name(node.get("technique_set")),
                "game_flags": f["info.gameFlags"],
                "sort_key": f["info.sortKey"],
                "info": header[0:0x20].hex(),
                "state_bits_entry": header[0x20:0x67].hex(),
                "texture_count": f.textureCount,
                "constant_count": f.constantCount,
                "state_bits_count": f.stateBitsCount,
                "textures": textures,
                "constants": constants,
                "state_bits": state_bits,
            }
            path = self.file_for("materials", name, ".json")
            write_json(path, out)
            self.record(T.MATERIAL, name, [self.rel(path)])

    def mtl_for(self, material_names: list[str], rel_to: str) -> str:
        """MTL text naming each material by its safe name, maps relative to ``rel_to``."""
        up = "../" * (rel_to.count("/") + 1) if rel_to else ""
        mats = {}
        for name in material_names:
            maps = self.material_maps.get(name, {})
            mats[self.names.get("materials", name)] = {k: up + v for k, v in maps.items() if v}
        return mtl_text(mats)

    # -- xmodels ------------------------------------------------------------------------------

    def surface_array(self, s: dict, key: str, pointer: str, count: int) -> np.ndarray | None:
        """An XSurface buffer as its schema's typed array: the surface's own, or, when
        the pointer is an offset to another surface's buffer (shared), the bytes there
        read with this surface's format."""
        v = view(s)
        if s.get(key) is not None:
            return v.array(key)
        spec = v.spec(key)
        if spec is None:
            return None
        data = self.resolver.read(v.fields[pointer], spec.element_size * count)
        return None if data is None else np.frombuffer(data, spec.dtype(), count)

    def xsurface_mesh(self, s: dict) -> vx.Mesh:
        f = view(s).fields
        count, tris = f.vertCount, f.triCount
        verts0 = self.surface_array(s, "verts0", "verts0", count)
        stream = self.surface_array(s, "vertex_stream", "vertexStream", count)
        indices = self.surface_array(s, "tri_indices", "triIndices", tris)
        if verts0 is None or indices is None:
            raise ValueError(
                f"surface buffers do not resolve (verts0 {f.verts0:#010x}, "
                f"tris {f.triIndices:#010x})"
            )
        return vx.xsurface_mesh(verts0, stream, indices, bytes(s["raw"]))

    @staticmethod
    def lods(node: dict) -> list[dict]:
        f = view(node).fields
        out = []
        for k in range(4):
            n = f[f"lodInfo[{k}].numsurfs"]
            if n:
                out.append(
                    {
                        "lod": k,
                        "dist": f[f"lodInfo[{k}].dist"],
                        "numsurfs": n,
                        "surf_index": f[f"lodInfo[{k}].surfIndex"],
                    }
                )
        return out

    @staticmethod
    def material_ref(entry: Any) -> Any:
        """XModel materialHandles element: a node {"raw", "material"} or the reference."""
        return entry.get("material") if isinstance(entry, dict) and "raw" in entry else entry

    def bones(self, node: dict) -> list[dict]:
        """parentList holds u8 offsets back to the parent, quats s16 / 32767."""
        v = view(node)
        n, roots = v.fields.numBones, v.fields.numRootBones

        def arr(key):
            return v.array(key) if node.get(key) is not None else []

        names, parents, quats, trans, base = (
            arr(k) for k in ("bone_names", "parent_list", "quats", "trans", "base_mat")
        )
        out = []
        for i in range(n):
            si = int(names[i]) if i < len(names) else None
            bone: dict[str, Any] = {
                "index": i,
                "name": self.script_strings[si]
                if si is not None and si < len(self.script_strings)
                else si,
            }
            if i < roots:
                bone["parent"] = None
            else:
                k = i - roots
                if k < len(parents):
                    bone["parent"] = i - int(parents[k])
                if k < len(quats):
                    bone["local_quat"] = [int(q) / 32767.0 for q in quats[k]]
                if k < len(trans):
                    bone["local_trans"] = [float(t) for t in trans[k][:3]]
            if i < len(base):
                b = base[i]
                bone["base_quat"] = [float(q) for q in b["quat"]]
                bone["base_trans"] = [float(t) for t in b["trans"]]
                bone["trans_weight"] = float(b["transWeight"])
            out.append(bone)
        return out

    def model_lod0(self, node: dict) -> list[tuple[str, vx.Mesh]]:
        """[(material name, mesh)] for LOD0, cached."""
        name = node["name"]
        if name in self.model_meshes:
            return self.model_meshes[name]
        surfs = node.get("surfs") or []
        lods = self.lods(node)
        first, count = (lods[0]["surf_index"], lods[0]["numsurfs"]) if lods else (0, len(surfs))
        mats = node.get("materials") or []
        out = []
        for i in range(first, min(first + count, len(surfs))):
            mesh = self.xsurface_mesh(surfs[i])
            mat = self.resolver.name(self.material_ref(mats[i])) if i < len(mats) else None
            out.append((mat or "default", mesh))
        self.model_meshes[name] = out
        return out

    def export_xmodels(self) -> None:
        for name, node in sorted(self.index.of(T.XMODEL).items()):
            folder = "xmodels/" + self.names.get("xmodels", name)
            files = []
            f = view(node).fields
            mats = [self.resolver.name(self.material_ref(m)) for m in (node.get("materials") or [])]
            surfaces = []
            for s in node.get("surfs") or []:
                sf = view(s).fields
                surfaces.append(
                    {
                        "flags": sf.flags,
                        "verts": sf.vertCount,
                        "tris": sf.triCount,
                        "skinned_vert_counts": list(sf["vertInfo.vertCount"]),
                    }
                )
            info = {
                "name": name,
                "num_bones": f.numBones,
                "num_root_bones": f.numRootBones,
                "numsurfs": f.numsurfs,
                "lods": self.lods(node),
                "radius": clean_float(f.radius),
                "mins": list(f.mins),
                "maxs": list(f.maxs),
                "materials": mats,
                "surfaces": surfaces,
                "phys_preset": self.resolver.name(node.get("phys_preset")),
                "coll_surf_count": f.numCollSurfs,
                "collmaps": f.numCollmaps,
            }
            try:
                if self.do_models:
                    parts = self.model_lod0(node)
                    files += self.write_mesh_obj(folder, safe_name(name), parts, name)
            except ValueError as exc:
                self.fail(T.XMODEL, name, f"LOD0 mesh: {exc}")
                info["error"] = str(exc)
            bones_path = self.out / folder / "bones.json"
            write_json(bones_path, self.bones(node))
            model_path = self.out / folder / "model.json"
            write_json(model_path, info)
            files += [self.rel(bones_path), self.rel(model_path)]
            self.record(T.XMODEL, name, files)

    def write_mesh_obj(
        self, folder: str, stem: str, parts: list[tuple[str, vx.Mesh]], title: str
    ) -> list[str]:
        positions, normals, uvs, groups = [], [], [], []
        at = 0
        for i, (mat, mesh) in enumerate(parts):
            n = len(mesh.positions)
            positions.append(mesh.positions)
            normals.append(mesh.normals if mesh.normals is not None else np.zeros((n, 3)))
            uvs.append(mesh.uvs if mesh.uvs is not None else np.zeros((n, 2)))
            tri = mesh.triangles[:, [0, 2, 1]] + at
            groups.append(ObjGroup(f"surf{i}", self.names.get("materials", mat), tri))
            at += n
        if not positions:
            return []
        obj = ObjMesh(
            np.concatenate(positions), np.concatenate(normals), np.concatenate(uvs), groups
        )
        obj_path = self.out / folder / f"{stem}.obj"
        mtl_path = self.out / folder / f"{stem}.mtl"
        write_obj(obj_path, obj, mtllib=mtl_path.name, header=f"{title} LOD0; inches, Z up")
        mtl_path.write_text(self.mtl_for(sorted({m for m, _ in parts}), folder))
        return [self.rel(obj_path), self.rel(mtl_path)]

    # -- world --------------------------------------------------------------------------------

    def static_models(self, g: dict) -> list[dict]:
        out = []
        for i, d in enumerate(g["smodel_draw_insts"] or []):
            f = view(d).fields
            packed = np.array(f["placement.axis"], np.uint32)
            axes = orthonormal(vx.unpack_cmp(packed).astype(np.float64))
            out.append(
                {
                    "index": i,
                    "model": self.resolver.name(d["model"]),
                    "origin": [round(v, 4) for v in f["placement.origin"]],
                    "angles": axes_to_angles(axes),
                    "axes": [[round(float(v), 5) for v in row] for row in axes],
                    "scale": round(f["placement.scale"], 5),
                    "cull_dist": clean_float(f.cullDist),
                    "flags": f.flags & 0xFFFFFFFF,
                }
            )
        return out

    def export_world(self) -> None:
        for g in [a.data for a in self.xfile.assets if a.type == T.GFX_MAP]:
            name = g["name"] or self.zone
            stem = safe_name(g.get("base_name") or self.zone)
            files = []
            v = view(g)
            surfs = [
                vx.WorldSurface.from_fields(i, view(s).fields) for i, s in enumerate(g["surfaces"])
            ]
            try:
                mesh, ranges = vx.world_mesh(
                    v.array("vertices")["xyz"],
                    bytes(g["vertex_layer_data"]),
                    v.array("indices"),
                    surfs,
                )
            except ValueError as exc:
                self.fail(T.GFX_MAP, name, f"world mesh: {exc}")
                continue
            mats = [self.resolver.name(s["material"]) or "default" for s in g["surfaces"]]
            tris = mesh.triangles[:, [0, 2, 1]]  # the game's front faces are clockwise
            groups = [
                ObjGroup(f"surf{i}", self.names.get("materials", mats[i]), tris[a : a + n])
                for i, (a, n) in enumerate(ranges)
            ]
            obj = ObjMesh(mesh.positions, mesh.normals, mesh.uvs, groups)
            obj_path = self.out / "world" / f"{stem}.obj"
            mtl_path = self.out / "world" / f"{stem}.mtl"
            write_obj(
                obj_path,
                obj,
                mtllib=mtl_path.name,
                header=f"{name}: GfxWorld surfaces; inches, Z up",
            )
            mtl_path.write_text(self.mtl_for(sorted(set(mats)), "world"))
            files += [self.rel(obj_path), self.rel(mtl_path)]
            self.world = (
                mesh.positions,
                tris,
                [m for i, m in enumerate(mats) for _ in range(ranges[i][1])],
            )
            surfaces_json = [
                {
                    "index": s.index,
                    "material": mats[s.index],
                    "first_vertex": s.first_vertex,
                    "vertex_count": s.vertex_count,
                    "tri_count": s.tri_count,
                    "base_index": s.base_index,
                    "layer_offset": s.layer_offset,
                    "lightmap": s.lightmap_index,
                    "flags": s.flags,
                }
                for s in surfs
            ]
            surf_path = self.out / "world" / "surfaces.json"
            write_json(surf_path, surfaces_json)
            files.append(self.rel(surf_path))
            placements = self.static_models(g)
            sm_path = self.out / "world" / "static_models.json"
            write_json(sm_path, placements)
            files.append(self.rel(sm_path))
            if self.do_instances and self.do_models:
                files += self.instance_models(stem, placements)
            info_path = self.out / "world" / "gfx_map.json"
            write_json(info_path, self.gfx_summary(g, mesh, mats))
            files.append(self.rel(info_path))
            self.record(
                T.GFX_MAP,
                name,
                files,
                vertices=len(mesh.positions),
                triangles=len(tris),
                surfaces=len(surfs),
                static_models=len(placements),
            )

    def gfx_summary(self, g: dict, mesh: vx.Mesh, mats: list[str]) -> dict:
        f = view(g).fields
        probes = g.get("reflection_probes") or []
        lightmaps = []
        for entry in g.get("lightmaps") or []:
            if isinstance(entry, dict) and "raw" in entry:
                lightmaps += [self.resolver.name(entry.get(k)) for k in (0, 1, 2)]
            else:
                lightmaps.append(self.resolver.name(entry))
        return {
            "name": g["name"],
            "base_name": g.get("base_name"),
            "plane_count": f.planeCount,
            "node_count": f.nodeCount,
            "surface_count": f.surfaceCount,
            "cell_count": f["dpvsPlanes.cellCount"],
            "vertex_count": len(mesh.positions),
            "index_count": f["draw.indexCount"],
            "vertex_layer_bytes": f["draw.vertexLayerDataSize"],
            "materials": sorted(set(mats)),
            "sky_image": self.resolver.name(g.get("sky_image")),
            "sky_box_model": g.get("sky_box_model"),
            "outdoor_image": self.resolver.name(g.get("outdoor_image")),
            "lightmaps": lightmaps,
            "reflection_probes": [
                {
                    "origin": list(view(p).fields.origin),
                    "image": self.resolver.name(p.get("image")),
                }
                for p in probes
            ],
            "mins": list(f.mins),
            "maxs": list(f.maxs),
            "sun_light": None
            if g.get("sun_light") is None
            else {"def": self.resolver.name(g["sun_light"].get("def"))},
            "occluders": f.numOccluders,
            "material_memory": [
                self.resolver.name(m.get("material")) for m in (g.get("material_memory") or [])
            ],
        }

    def instance_models(self, stem: str, placements: list[dict]) -> list[str]:
        """One OBJ with every static model's LOD0 placed in the world."""
        path = self.out / "world" / f"{stem}_static_models.obj"
        mtl_names: set[str] = set()
        buf = io.StringIO()
        buf.write(f"# {self.zone}: static models (LOD0) placed; inches, Z up\n")
        buf.write(f"mtllib {stem}_static_models.mtl\n")
        at = 1
        all_pos, all_tri, tri_keys = [], [], []
        base_at = 0
        missing = 0
        for p in placements:
            node = self.index.get(T.XMODEL, p["model"])
            if node is None:
                missing += 1
                continue
            try:
                parts = self.model_lod0(node)
            except ValueError:
                missing += 1
                continue
            axes = np.array(p["axes"])
            origin = np.array(p["origin"])
            buf.write(f"o smodel{p['index']}_{safe_name(p['model'])}\n")
            for mat, mesh in parts:
                world = origin + p["scale"] * (mesh.positions.astype(np.float64) @ axes)
                n = len(world)
                np.savetxt(buf, world, fmt="v %.4f %.4f %.4f")
                tri = mesh.triangles[:, [0, 2, 1]]
                mname = self.names.get("materials", mat)
                mtl_names.add(mat)
                buf.write(f"usemtl {mname}\n")
                np.savetxt(buf, tri + at, fmt="f %d %d %d")
                all_pos.append(world)
                all_tri.append(tri + base_at)
                tri_keys += [p["model"]] * len(tri)
                at += n
                base_at += n
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(buf.getvalue())
        mtl = self.out / "world" / f"{stem}_static_models.mtl"
        mtl.write_text(self.mtl_for(sorted(mtl_names), "world"))
        if all_pos:
            self.smodels = (np.concatenate(all_pos), np.concatenate(all_tri), tri_keys)
        if missing:
            self.fail("static_models", None, f"{missing} placements without a usable model")
        return [self.rel(path), self.rel(mtl)]

    # -- collision ----------------------------------------------------------------------------

    def array_at(self, raw: int, count: int) -> np.ndarray | None:
        """``count`` typed elements at an offset pointer, from the array that loaded them."""
        target = self.resolver.target(raw)
        if target is None or not isinstance(target.node, dict):
            return None
        holder = target.node.get(target.key)
        if not isinstance(holder, bytes | bytearray | memoryview):
            return None
        arr = view(target.node).array(target.key)
        first, rem = divmod(target.within, arr.dtype.itemsize)
        if rem or first + count > len(arr):
            return None
        return arr[first : first + count]

    def brush_sides(self, raw: int, count: int):
        """(normal, dist, cflags, sflags) of each side of a brush, through the side
        and plane pointers."""
        sides = self.array_at(raw, count)
        if sides is None:
            return None
        out = []
        for side in sides:
            plane = self.array_at(int(side["plane"]), 1)
            if plane is None:
                raise ValueError(
                    f"brush side plane pointer {int(side['plane']):#010x} does " "not resolve"
                )
            out.append((plane[0]["normal"], plane[0]["dist"], side["cflags"], side["sflags"]))
        return out

    def export_collision(self) -> None:
        for asset in self.xfile.assets:
            if asset.type not in (T.COL_MAP_MP, T.COL_MAP_SP):
                continue
            c = asset.data
            v = view(c)
            name = c["name"] or self.zone
            files = []
            try:
                brushes = (
                    col.parse_brushes(v.array("brushes"), self.brush_sides)
                    if c.get("brushes") is not None
                    else []
                )
            except ValueError as exc:
                self.fail(asset.type, name, str(exc))
                brushes = []
            by_contents: dict[int, int] = {}
            empty = 0
            positions, groups, at = [], [], 0
            keys, extents = [], []
            for i, b in enumerate(brushes):
                by_contents[b.contents] = by_contents.get(b.contents, 0) + 1
                faces = col.brush_faces(b)
                if not faces:
                    empty += 1
                    continue
                p, t = col.triangulate(faces)
                positions.append(p)
                groups.append(
                    ObjGroup(f"brush{i}_contents_{b.contents & 0xFFFFFFFF:08x}", None, t + at)
                )
                keys += [f"{b.contents:#x}"] * len(t)
                extents += [float(np.max(b.maxs[:2] - b.mins[:2]))] * len(t)
                at += len(p)
            stem = safe_name(self.zone)
            if positions:
                obj = ObjMesh(np.concatenate(positions), groups=groups)
                path = self.out / "collision" / f"{stem}_brushes.obj"
                write_obj(
                    path,
                    obj,
                    header=f"{name}: {len(brushes)} brushes as convex faces; "
                    "one group per brush, named with its contents",
                )
                files.append(self.rel(path))
                self.collision_brushes = (
                    obj.positions,
                    np.concatenate([g.triangles for g in groups]),
                    keys,
                    np.array(extents),
                )
            tp, tt = col.collision_triangles(
                v.array("verts") if c.get("verts") is not None else None,
                v.array("tri_indices") if c.get("tri_indices") is not None else None,
            )
            if len(tt):
                path = self.out / "collision" / f"{stem}_triangles.obj"
                write_obj(
                    path,
                    ObjMesh(tp, groups=[ObjGroup("triangles", None, tt)]),
                    header=f"{name}: collision triangles",
                )
                files.append(self.rel(path))
                self.collision_tris = (tp, tt)
            summary = self.collision_summary(c, brushes, by_contents, empty, len(tt))
            path = self.out / "collision" / "collision.json"
            write_json(path, summary)
            files.append(self.rel(path))
            self.record(asset.type, name, files, brushes=len(brushes), triangles=int(len(tt)))

    def collision_summary(self, c: dict, brushes, by_contents, empty: int, tris: int) -> dict:
        v = view(c)
        f = v.fields

        def arr(key):
            return v.array(key) if c.get(key) is not None else []

        mats = [
            {
                "name": m["material"].split(b"\0", 1)[0].decode("latin-1"),
                "surface_flags": f"{int(m['surfaceFlags']) & 0xFFFFFFFF:#010x}",
                "content_flags": f"{int(m['contentFlags']) & 0xFFFFFFFF:#010x}",
            }
            for m in arr("materials")
        ]
        statics = []
        for s in arr("static_model_list"):
            target = self.xfile.resolve(int(s["xmodel"]))
            statics.append(
                {
                    "model": target.asset.get("name") if target and target.asset else None,
                    "origin": [float(x) for x in s["origin"]],
                    "absmin": [float(x) for x in s["absmin"]],
                    "absmax": [float(x) for x in s["absmax"]],
                }
            )
        cmodels = [
            {
                "mins": [float(x) for x in m["mins"]],
                "maxs": [float(x) for x in m["maxs"]],
                "radius": float(m["radius"]),
            }
            for m in arr("cmodels")
        ]
        dyn = []
        for defs in (c.get("dyn_ent_def_list0"), c.get("dyn_ent_def_list1")):
            for d in defs or []:
                dyn.append(
                    {
                        "xmodel": self.resolver.name(d["xmodel"]),
                        "destroyed_xmodel": self.resolver.name(d["destroyed_xmodel"]),
                        "destroy_fx": self.resolver.name(d["destroy_fx"]),
                        "phys_preset": self.resolver.name(d["phys_preset"]),
                        "raw": hexs(d["raw"]),
                    }
                )
        return {
            "name": c["name"],
            "counts": {
                "planes": f.planeCount,
                "brushes": len(brushes),
                "brushes_without_faces": empty,
                "brush_sides": f.numBrushSides,
                "brush_verts": f.numBrushVerts,
                "verts": f.vertCount,
                "triangles": tris,
                "partitions": f.partitionCount,
                "aabb_trees": f.aabbTreeCount,
                "leafs": f.numLeafs,
                "nodes": f.numNodes,
                "cmodels": len(cmodels),
                "static_models": len(statics),
                "materials": len(mats),
                "dyn_ents": len(dyn),
                "constraints": len(c.get("constraints") or []),
            },
            "brushes_by_contents": {
                f"{k & 0xFFFFFFFF:#010x}": n for k, n in sorted(by_contents.items())
            },
            "materials": mats,
            "cmodels": cmodels,
            "static_models": statics,
            "dyn_ents": dyn,
        }

    # -- entities, paths, lights --------------------------------------------------------------

    def export_map_ents(self) -> None:
        nodes = list(self.index.of(T.MAP_ENTS).values())
        for c in self.xfile.assets:
            if (
                c.type in (T.COL_MAP_MP, T.COL_MAP_SP)
                and isinstance(c.data.get("map_ents"), dict)
                and all(c.data["map_ents"] is not n for n in nodes)
            ):
                nodes.append(c.data["map_ents"])
        for node in nodes:
            name = node.get("name") or self.zone
            text = entity_text(node.get("entity_string"))
            stem = safe_name(self.zone if len(nodes) == 1 else name)
            ents_path = self.out / "map_ents" / f"{stem}.ents"
            ents_path.parent.mkdir(parents=True, exist_ok=True)
            ents_path.write_text(text, encoding="latin-1")
            files = [self.rel(ents_path)]
            try:
                ents = parse_entities(text)
            except ValueError as exc:
                self.fail(T.MAP_ENTS, name, str(exc))
                ents = []
            for e in ents:
                for key in ("origin", "angles"):
                    if key in e and vector(e[key]) is not None:
                        e[key + "_vec"] = vector(e[key])
            json_path = self.out / "map_ents" / "entities.json"
            write_json(json_path, ents)
            files.append(self.rel(json_path))
            classes: dict[str, int] = {}
            for e in ents:
                classes[e.get("classname", "")] = classes.get(e.get("classname", ""), 0) + 1
            self.entities = ents
            self.record(T.MAP_ENTS, name, files, entities=len(ents), classnames=classes)

    def export_game_map(self) -> None:
        for asset in self.xfile.assets:
            if asset.type not in (T.GAME_MAP_MP, T.GAME_MAP_SP):
                continue
            data = asset.data
            f = view(data).fields
            count = f["path.nodeCount"]
            node_list = data.get("nodes") or []
            nodes = []
            for i in range(min(count, len(node_list))):
                v = view(node_list[i])
                n = v.fields
                kind = n["constant.type"]
                node = {
                    "index": i,
                    "type": NODE_TYPES[kind] if 0 <= kind < len(NODE_TYPES) else kind,
                    "spawnflags": n["constant.spawnflags"],
                    "targetname": self.sstr(n["constant.targetname"]),
                    "script_linkname": self.sstr(n["constant.script_linkName"]),
                    "script_noteworthy": self.sstr(n["constant.script_noteworthy"]),
                    "target": self.sstr(n["constant.target"]),
                    "animscript": self.sstr(n["constant.animscript"]),
                    "origin": list(n["constant.vOrigin"]),
                    "angle": n["constant.fAngle"],
                    "radius": n["constant.fRadius"],
                    "links": [],
                }
                if node_list[i].get("links") is not None:
                    for link in v.array("links"):
                        node["links"].append(
                            {"node": int(link["nodeNum"]), "dist": round(float(link["fDist"]), 3)}
                        )
                nodes.append(node)
            out = {
                "name": data["name"],
                "node_count": count,
                "chain_node_count": f["path.chainNodeCount"],
                "vis_bytes": f["path.visBytes"],
                "node_tree_count": f["path.nodeTreeCount"],
                "nodes": nodes,
            }
            path = self.out / "game_map" / "paths.json"
            write_json(path, out)
            self.paths = nodes
            self.record(asset.type, asset.data["name"], [self.rel(path)], nodes=count)

    def sstr(self, index: int) -> str | None:
        if not index or index >= len(self.script_strings):
            return None
        return self.script_strings[index]

    def export_com_map(self) -> None:
        for asset in self.xfile.assets:
            if asset.type != T.COM_MAP:
                continue
            lights = []
            for i, light in enumerate(asset.data.get("primary_lights") or []):
                f = view(light).fields
                lights.append(
                    {
                        "index": i,
                        "type": f.type,
                        "can_use_shadow_map": f.canUseShadowMap,
                        "exponent": f.exponent,
                        "priority": f.priority,
                        "cull_dist": f.cullDist,
                        "color": [clean_float(x) for x in f.color],
                        "dir": [clean_float(x) for x in f.dir],
                        "origin": [clean_float(x) for x in f.origin],
                        "radius": clean_float(f.radius),
                        "cos_half_fov_outer": clean_float(f.cosHalfFovOuter),
                        "cos_half_fov_inner": clean_float(f.cosHalfFovInner),
                        "def_name": light.get("def_name"),
                        "raw": bytes(light["raw"]).hex(),
                    }
                )
            path = self.out / "com_map" / "lights.json"
            write_json(path, {"name": asset.data["name"], "lights": lights})
            self.record(T.COM_MAP, asset.data["name"], [self.rel(path)], lights=len(lights))
        for name, node in sorted(self.index.of(T.LIGHTDEF).items()):
            f = view(node).fields
            image = self.resolver.name(node["image"])
            path = self.file_for("lightdefs", name, ".json")
            write_json(
                path,
                {
                    "name": name,
                    "image": image,
                    "image_file": self.image_files.get(image or ""),
                    "sampler_state": f["attenuation.samplerState"],
                    "lmap_lookup_start": f.lmapLookupStart,
                },
            )
            self.record(T.LIGHTDEF, name, [self.rel(path)])

    # -- rawfiles, stringtables, localize -----------------------------------------------------

    def export_content(self) -> None:
        localize = {}
        for asset in self.xfile.assets:
            d = asset.data
            if d is None:
                continue
            if asset.type == T.RAWFILE:
                name = d.name or f"rawfile_{asset.index}"
                try:
                    body = d.contents()
                except (ValueError, Exception) as exc:  # zlib.error
                    self.fail(T.RAWFILE, name, str(exc))
                    continue
                if body is None:
                    continue
                parts = [safe_name(p) for p in name.replace("\\", "/").split("/") if p]
                path = self.out / "rawfiles" / Path(*parts)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(body)
                self.record(
                    T.RAWFILE, name, [self.rel(path)], compressed=d.compressed, bytes=len(body)
                )
            elif asset.type == T.STRINGTABLE:
                name = d.name or f"stringtable_{asset.index}"
                buf = io.StringIO()
                w = csv.writer(buf, lineterminator="\n")
                for row in range(d.row_count):
                    w.writerow([d.cell(row, col) or "" for col in range(d.column_count)])
                path = self.file_for("stringtables", name, "")
                if not path.name.lower().endswith(".csv"):
                    path = path.with_name(path.name + ".csv")
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(buf.getvalue(), encoding="utf-8")
                self.record(
                    T.STRINGTABLE, name, [self.rel(path)], rows=d.row_count, columns=d.column_count
                )
            elif asset.type == T.LOCALIZE:
                localize[d.name] = d.value
        if localize:
            path = self.out / "localize" / "localize.json"
            write_json(path, localize)
            self.record(T.LOCALIZE, None, [self.rel(path)], entries=len(localize))

    # -- everything else ----------------------------------------------------------------------

    HANDLED = {
        T.IMAGE,
        T.MATERIAL,
        T.TECHSET,
        T.VERTEXSHADER,
        T.PIXELSHADER,
        T.XMODEL,
        T.GFX_MAP,
        T.COL_MAP_MP,
        T.COL_MAP_SP,
        T.MAP_ENTS,
        T.GAME_MAP_MP,
        T.GAME_MAP_SP,
        T.COM_MAP,
        T.LIGHTDEF,
        T.RAWFILE,
        T.STRINGTABLE,
        T.LOCALIZE,
    }

    def export_generic(self) -> None:
        seen: set[tuple[int, str]] = set()
        todo = []
        for asset in self.xfile.assets:
            if asset.type in self.HANDLED or asset.data is None:
                continue
            todo.append((asset.type, asset.data))
        for t in (T.FX, T.PHYSPRESET):
            for node in self.index.of(t).values():
                todo.append((t, node))
        for t, node in todo:
            name = node.get("name") if isinstance(node, dict) else getattr(node, "name", None)
            key = (t, name or f"#{id(node)}")
            if key in seen:
                continue
            seen.add(key)
            folder = type_name(t)
            stem = self.names.get(folder, name or f"{folder}_{len(seen)}")
            blob_dir = self.out / folder / f"{stem}.blobs"
            value = self.json.convert(node, blob_dir, "", True)
            if t == T.XANIM and isinstance(node, dict):
                value = self.xanim_summary(node, value)
            path = self.out / folder / f"{stem}.json"
            write_json(path, value)
            files = [self.rel(path)]
            if blob_dir.is_dir():
                files += [self.rel(p) for p in sorted(blob_dir.iterdir())]
            self.record(t, name, files)

    def xanim_summary(self, node: dict, value: dict) -> dict:
        v = view(node)
        f = v.fields
        names = v.array("names") if node.get("names") is not None else []
        notes = []
        if node.get("notify") is not None:
            for n in v.array("notify"):
                notes.append(
                    {"name": self.sstr(int(n["name"])), "time": clean_float(float(n["time"]))}
                )
        summary = {
            "name": node.get("name"),
            "numframes": f.numframes,
            "bone_count": list(f.boneCount),
            "bones": [self.sstr(int(i)) for i in names],
            "notify": notes,
        }
        summary["data"] = value
        return summary

    # -- previews -----------------------------------------------------------------------------

    def export_previews(self) -> None:
        from opent5.export import preview as pv

        out = self.out / "previews"
        out.mkdir(parents=True, exist_ok=True)
        files = []

        def save(label: str, img: np.ndarray) -> None:
            rgba = np.dstack([img, np.full(img.shape[:2], 255, np.uint8)])
            path = out / f"{label}.png"
            path.write_bytes(png_bytes(rgba))
            files.append(self.rel(path))

        bounds = self.play_bounds()
        world = getattr(self, "world", None)
        if world is not None:
            p, t, keys = world
            tints = pv.tint_by_key(keys)
            for view in ("top", "angle"):
                save(f"world_{view}", pv.render(p, t, view, 1024, tints, bounds))
        sm = getattr(self, "smodels", None)
        if world is not None and sm is not None:
            p0, t0, k0 = world
            p1, t1, k1 = sm
            p = np.concatenate([p0, p1])
            t = np.concatenate([t0, t1 + len(p0)])
            tints = np.concatenate([pv.tint_by_key(k0), np.tile([[230, 200, 120]], (len(t1), 1))])
            for view in ("top", "angle"):
                save(f"world_models_{view}", pv.render(p, t, view, 1024, tints, bounds))
        cb = getattr(self, "collision_brushes", None)
        if cb is not None:
            p, t, keys, extents = cb
            # Only solid brushes (contents bit 0), and none wider than the play area: the
            # clipMap also holds sky, clip and other volumes that would hide everything
            # from above.
            limit = (
                np.inf if bounds is None else 0.75 * float(np.max(bounds[1][:2] - bounds[0][:2]))
            )
            solid = np.array([int(k, 16) & 1 for k in keys], bool)
            keep = solid & (extents < limit)
            tints = pv.tint_by_key(np.array(keys)[keep].tolist())
            for view in ("top", "angle"):
                save(f"collision_brushes_{view}", pv.render(p, t[keep], view, 1024, tints, bounds))
        ct = getattr(self, "collision_tris", None)
        if ct is not None:
            p, t = ct
            for view in ("top", "angle"):
                save(f"collision_triangles_{view}", pv.render(p, t, view, 1024, None, bounds))
        self.record(
            "preview",
            None,
            files,
            bounds=None if bounds is None else [bounds[0].tolist(), bounds[1].tolist()],
        )

    def play_bounds(self):
        """The playable area: spawn points and path nodes, padded; None when unknown."""
        pts = []
        for e in getattr(self, "entities", []) or []:
            if e.get("classname", "").startswith("mp_") and "origin_vec" in e:
                pts.append(e["origin_vec"][:3])
        for n in getattr(self, "paths", []) or []:
            pts.append(n["origin"])
        if len(pts) < 4:
            return None
        a = np.array(pts, np.float64)
        lo, hi = a.min(0), a.max(0)
        pad = np.array([1500.0, 1500.0, 0.0])
        return lo - pad - np.array([0, 0, 600.0]), hi + pad + np.array([0, 0, 1500.0])
