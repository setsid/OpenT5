"""The exporter on real zones: counts on mp_nuked (and a smoke run on mp_firingrange).

Skipped unless .env points at the zones. The numbers are mp_nuked's own (asset
list, GfxWorld and clipMap headers), so a change in any of them means the
exporter or the parser lost or invented something.
"""

from pathlib import Path

import numpy as np
import pytest

from opent5 import env
from opent5.container.zone import Zone
from opent5.export import vertex as vx
from opent5.export.nodes import AliasResolver, AssetIndex, MemoryMap, Resolver
from opent5.export.zone import ZoneExporter
from opent5.xfile import AssetType, parse

pytestmark = pytest.mark.zones


def zone_path(name: str) -> Path | None:
    for key in ("OPENT5_PATCH_ZONES", "OPENT5_ZONES"):
        folder = env.path_of(key)
        if folder and (folder / f"{name}.ff").is_file():
            return folder / f"{name}.ff"
    return None


def pak_dirs(path: Path) -> list[Path]:
    dirs = [path.parent]
    for key in ("OPENT5_PATCH_ZONES", "OPENT5_ZONES"):
        folder = env.path_of(key)
        if folder and folder.is_dir():
            dirs.append(folder)
    return dirs


@pytest.fixture(scope="module")
def nuked():
    path = zone_path("mp_nuked")
    if path is None:
        pytest.skip("mp_nuked.ff not configured in .env")
    content = Zone.open(path).content
    xfile = parse(content, log=True)
    index = AssetIndex(xfile)
    memory = MemoryMap(xfile, content)
    resolver = Resolver(index, AliasResolver(memory))
    return {
        "path": path,
        "content": content,
        "xfile": xfile,
        "index": index,
        "memory": memory,
        "resolver": resolver,
    }


def test_assets_found_including_inline(nuked):
    counts = nuked["index"].counts()
    assert counts["image"] == 1036
    assert counts["material"] == 585
    assert counts["xmodel"] == 293
    assert counts["techset"] == 104
    assert counts["vertexshader"] == 234
    assert counts["pixelshader"] == 980


def _one(xfile, asset_type):
    (data,) = [a.data for a in xfile.assets if a.type == asset_type]
    return data


def test_alias_references_resolve(nuked):
    g = _one(nuked["xfile"], AssetType.GFX_MAP)
    r, index = nuked["resolver"], nuked["index"]
    materials = [r.name(s["material"]) for s in g["surfaces"]]
    assert len(materials) == 3494 and None not in materials
    assert all(index.get(AssetType.MATERIAL, m) is not None for m in materials)
    models = [r.name(d["model"]) for d in g["smodel_draw_insts"]]
    assert len(models) == 4209 and None not in models
    assert all(index.get(AssetType.XMODEL, m) is not None for m in models)


def test_world_mesh(nuked):
    g = _one(nuked["xfile"], AssetType.GFX_MAP)
    mesh, surfaces, ranges = vx.world_mesh(
        bytes(g["vertices"]),
        bytes(g["vertex_layer_data"]),
        bytes(g["indices"]),
        [bytes(s["raw"]) for s in g["surfaces"]],
    )
    assert mesh.positions.shape == (176344, 3)
    assert mesh.triangles.shape == (117181, 3)
    assert mesh.triangles.max() < len(mesh.positions)
    # The packed vertex normals and the triangles' own normals agree (the game's
    # front faces are clockwise, so the cross product points the other way).
    p, t = mesh.positions.astype(np.float64), mesh.triangles
    n = np.cross(p[t[:, 1]] - p[t[:, 0]], p[t[:, 2]] - p[t[:, 0]])
    keep = np.linalg.norm(n, axis=1) > 1e-6
    n = n[keep] / np.linalg.norm(n[keep], axis=1, keepdims=True)
    vn = mesh.normals[t[keep]].mean(1)
    assert ((n * vn).sum(1) < -0.5).mean() > 0.99


def test_model_meshes_resolve(nuked, tmp_path):
    exporter = ZoneExporter(
        nuked["content"],
        "mp_nuked",
        tmp_path,
        [],
        images=False,
        previews=False,
        log=lambda _m: None,
    )
    exporter.xfile = nuked["xfile"]
    exporter.index, exporter.memory = nuked["index"], nuked["memory"]
    exporter.resolver = nuked["resolver"]
    failures = []
    for name, node in nuked["index"].of(AssetType.XMODEL).items():
        if name.startswith(","):
            continue
        try:
            parts = exporter.model_lod0(node)
        except ValueError as exc:
            failures.append((name, str(exc)))
            continue
        for _mat, mesh in parts:
            assert mesh.triangles.max() < len(mesh.positions), name
            assert np.isfinite(mesh.positions).all(), name
    assert failures == []
    bus = exporter.model_lod0(nuked["index"].get(AssetType.XMODEL, "t5_veh_schoolbus"))
    pos = np.concatenate([m.positions for _, m in bus])
    # Inside the model's own bounds (XModel +0xb4 mins, +0xc0 maxs), a little slack.
    assert pos.min(0) == pytest.approx([-225.13, -54.31, -0.53], abs=2)
    assert pos.max(0) == pytest.approx([225.57, 61.35, 132.6], abs=2)


def test_export_without_images(nuked, tmp_path):
    manifest = ZoneExporter(
        nuked["content"],
        "mp_nuked",
        tmp_path,
        pak_dirs(nuked["path"]),
        images=False,
        instance_models=False,
        previews=False,
        log=lambda _m: None,
    ).run()
    assert manifest["failures"] == []
    exported = manifest["counts"]["exported"]
    assert exported["material"] == 585 and exported["xmodel"] == 293
    by_type = {a["type"]: a for a in manifest["assets"]}
    assert by_type["gfx_map"]["triangles"] == 117181
    assert by_type["gfx_map"]["static_models"] == 4209
    assert by_type["col_map_mp"]["brushes"] == 5890
    assert by_type["col_map_mp"]["triangles"] == 7492
    assert by_type["map_ents"]["entities"] == 1106
    assert by_type["game_map_mp"]["nodes"] == 316
    assert by_type["com_map"]["lights"] == 24
    assert exported["rawfile"] == 26 and exported["stringtable"] == 12
    assert (tmp_path / "world" / "mp_nuked.obj").stat().st_size > 10_000_000
    assert (tmp_path / "map_ents" / "mp_nuked.ents").read_text(encoding="latin-1").startswith("{")


@pytest.mark.slow
def test_every_image_decodes(nuked, tmp_path):
    """Every image decodes except the ',' references to other zones (27 in mp_nuked)."""
    manifest = ZoneExporter(
        nuked["content"],
        "mp_nuked",
        tmp_path,
        pak_dirs(nuked["path"]),
        models=False,
        previews=False,
        log=lambda _m: None,
    ).run()
    failed = [f for f in manifest["failures"] if f["type"] == "image"]
    assert len(failed) == 27
    assert all(f["name"].startswith(",") for f in failed)
    images = [a for a in manifest["assets"] if a["type"] == "image" and a["files"]]
    assert len(images) == 1036 - 27


@pytest.mark.slow
def test_firingrange_exports(tmp_path):
    path = zone_path("mp_firingrange")
    if path is None:
        pytest.skip("mp_firingrange.ff not configured in .env")
    manifest = ZoneExporter(
        Zone.open(path).content,
        "mp_firingrange",
        tmp_path,
        pak_dirs(path),
        images=False,
        instance_models=False,
        previews=False,
        log=lambda _m: None,
    ).run()
    assert manifest["failures"] == []
    by_type = {a["type"]: a for a in manifest["assets"]}
    assert by_type["gfx_map"]["triangles"] > 0 and by_type["col_map_mp"]["brushes"] > 0
