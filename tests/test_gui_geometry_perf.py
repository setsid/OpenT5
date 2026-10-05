"""v0.4.0 deep performance pass: the static-model placement decode and world+models assembly
were vectorised (batch struct decode, one matmul per model). These lock in that the vectorised
build stays byte-identical to the per-prop reference loop it replaced, and that it is materially
faster, on the pinned retail mp_nuked (never the live .env zone).
"""

from __future__ import annotations

import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np  # noqa: E402
import pytest  # noqa: E402

from opent5.gui import geometry  # noqa: E402
from opent5.gui.backend import ZoneDoc  # noqa: E402
from opent5.xfile.constants import AssetType as T  # noqa: E402
from retail_fixtures import retail_zone  # noqa: E402


def _world_asset(doc):
    return next(a for a in doc.xfile.assets if a.type == T.GFX_MAP and a.data is not None)


@pytest.mark.zones
def test_decode_placements_matches_exporter_field_for_field():
    """The vectorised placement decode reproduces the exporter's scalar ``static_models`` for
    every field the mesh uses (model, origin, axes, scale), bit for bit."""
    doc = ZoneDoc.open(retail_zone("mp_nuked"))
    asset = _world_asset(doc)
    h = geometry._helper(doc.xfile)
    ref = h.static_models(asset.data)
    got = geometry._decode_placements(h, asset.data)
    assert len(got) == len(ref)
    for a, b in zip(ref, got, strict=True):
        assert b["index"] == a["index"]
        assert b["model"] == a["model"]
        assert b["origin"] == a["origin"]
        assert b["axes"] == a["axes"]
        assert b["scale"] == a["scale"]


@pytest.mark.zones
def test_vectorised_world_models_is_byte_identical_to_the_loop():
    """The batched ``with_static_models`` builds the exact same mesh the per-prop loop did:
    same vertices, normals, uvs, triangles, groups, materials and placement map."""
    doc = ZoneDoc.open(retail_zone("mp_nuked"))
    asset = _world_asset(doc)
    xfile = doc.xfile
    world = geometry.mesh(doc, "world", asset.index)

    new = geometry.with_static_models(xfile, asset, world)
    drawn_new = geometry._models_state[(id(xfile), asset.index)]["drawn"]
    ref = geometry._with_static_models_loop(xfile, asset, world)
    drawn_ref = geometry._models_state[(id(xfile), asset.index)]["drawn"]

    assert np.array_equal(new.positions, ref.positions)
    assert new.positions.dtype == ref.positions.dtype == np.float32
    assert np.array_equal(new.normals, ref.normals)
    assert np.array_equal(new.uvs, ref.uvs)
    assert np.array_equal(new.triangles, ref.triangles)
    assert new.triangles.dtype == ref.triangles.dtype == np.int32
    assert np.array_equal(new.groups, ref.groups)
    assert new.materials == ref.materials
    assert new.notes == ref.notes
    assert drawn_new == drawn_ref


@pytest.mark.zones
def test_vectorised_build_is_materially_faster_than_the_loop():
    """The whole point of the pass: the batched decode and build beat the per-prop loop by a
    wide margin. Compared with the model-LOD cache warm, so this measures the placement work,
    not the one-off model decode. A conservative 2x bar, well inside the ~10x decode speedup."""
    doc = ZoneDoc.open(retail_zone("mp_nuked"))
    asset = _world_asset(doc)
    xfile = doc.xfile
    world = geometry.mesh(doc, "world", asset.index)
    geometry.with_static_models(xfile, asset, world)  # warm the model-LOD cache

    def _best(fn, n=3):
        best = float("inf")
        for _ in range(n):
            start = time.perf_counter()
            fn()
            best = min(best, time.perf_counter() - start)
        return best

    loop = _best(lambda: geometry._with_static_models_loop(xfile, asset, world))
    vec = _best(lambda: geometry.with_static_models(xfile, asset, world))
    assert vec < loop / 2.0, f"vectorised {vec * 1000:.0f} ms, loop {loop * 1000:.0f} ms"


def _run_textures(doc, mesh):
    xfile = doc.xfile
    for key in [k for k in geometry._texcache if k[0] == id(xfile)]:
        del geometry._texcache[key]
    return geometry.textures(doc, mesh)


@pytest.mark.zones
def test_parallel_texture_decode_matches_serial(monkeypatch):
    """Decoding colour maps across a thread pool gives the exact same tri_tex and texture list,
    in the same order, as a single-threaded decode."""
    doc = ZoneDoc.open(retail_zone("mp_nuked"))
    mesh = geometry.mesh(doc, "world_models", None)
    tri_tex_p, tex_p = _run_textures(doc, mesh)
    monkeypatch.setattr(geometry, "_TEXTURE_WORKERS", 1)
    tri_tex_s, tex_s = _run_textures(doc, mesh)
    assert np.array_equal(tri_tex_p, tri_tex_s)
    assert len(tex_p) == len(tex_s)
    for a, b in zip(tex_p, tex_s, strict=True):
        assert a.shape == b.shape
        assert np.array_equal(a, b)


@pytest.mark.zones
def test_parallel_texture_decode_is_faster(monkeypatch):
    """The thread pool decodes the map's colour maps materially faster than serial. A modest
    1.5x bar (measured ~3x on an eight-core box) so the test is not flaky on a busy machine."""
    doc = ZoneDoc.open(retail_zone("mp_nuked"))
    mesh = geometry.mesh(doc, "world_models", None)
    if (os.cpu_count() or 1) < 2:
        pytest.skip("no second core to parallelise onto")

    start = time.perf_counter()
    _run_textures(doc, mesh)
    parallel = time.perf_counter() - start
    monkeypatch.setattr(geometry, "_TEXTURE_WORKERS", 1)
    start = time.perf_counter()
    _run_textures(doc, mesh)
    serial = time.perf_counter() - start
    assert (
        parallel < serial / 1.5
    ), f"parallel {parallel * 1000:.0f} ms, serial {serial * 1000:.0f} ms"
