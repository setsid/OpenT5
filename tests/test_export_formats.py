"""Unit tests for the export writers and unpackers, on synthetic data (no game files)."""

import struct

import numpy as np
import pytest

from opent5.export import collision as col
from opent5.export import vertex as vx
from opent5.export.entities import EntityError, entity_text, parse_entities
from opent5.export.images import rebuild_normal, stream_parts
from opent5.export.jsonable import Jsonifier, NameTable, safe_name
from opent5.export.obj import ObjGroup, ObjMesh, mtl_text, obj_text, read_obj
from opent5.export.preview import render
from opent5.export.zone import axes_to_angles, orthonormal, r_hash
from opent5.xfile.structs import XStreamNTUV, XSurface, XVertexPacked, cbrush_t

POS_SHIFT = XSurface.field("posScaleExp").offset

# -- CMP normals, halves, packed positions -------------------------------------------------


def test_cmp_axes():
    words = np.array([0x000003FF, 0x001FF800, 0x7FC00000, 0x00000401], np.uint32)
    v = vx.unpack_cmp(words)
    assert np.allclose(v[0], [1, 0, 0])
    assert np.allclose(v[1], [0, 1, 0])
    assert np.allclose(v[2], [0, 0, 1])
    assert np.allclose(v[3], [-1, 0, 0])


def test_packed_positions():
    surf = bytearray(0x5C)
    struct.pack_into(">3f", surf, XSurface.field("posOffset").offset, 1.0, 2.0, 32.0)
    surf[POS_SHIFT : POS_SHIFT + 3] = bytes([6, 6, 9])
    q = np.array([[16384, -16384, 32767], [0, 0, 0]])
    p = vx.packed_positions(q, bytes(surf))
    assert np.allclose(p[0], [1 + 32, 2 - 32, 32 + 512 * 32767 / 32768], atol=1e-3)
    assert np.allclose(p[1], [1, 2, 32])


def test_xsurface_mesh_format5():
    # Typed arrays in the parser's own formats for flags 5 (XVertexPacked, XStreamNTUV).
    surf = bytearray(0x5C)
    surf[POS_SHIFT : POS_SHIFT + 3] = bytes([6, 6, 6])
    verts0 = np.frombuffer(
        np.array([[0, 0, 0, 32767], [512, 0, 0, 32767], [0, 512, 0, 32767]], ">i2").tobytes(),
        XVertexPacked.dtype(),
    )
    stream = np.frombuffer(
        b"".join(
            struct.pack(">II", 0x7FC00000, 0x000003FF) + np.array([u, v], ">f2").tobytes()
            for u, v in ((0, 0), (1, 0), (0, 1))
        ),
        XStreamNTUV.dtype(),
    )
    m = vx.xsurface_mesh(verts0, stream, np.array([[0, 1, 2]], ">u2"), bytes(surf))
    assert np.allclose(m.positions, [[0, 0, 0], [1, 0, 0], [0, 1, 0]])
    assert np.allclose(m.normals, [[0, 0, 1]] * 3)
    assert np.allclose(m.uvs, [[0, 0], [1, 0], [0, 1]])
    assert m.triangles.tolist() == [[0, 1, 2]]


# -- world ---------------------------------------------------------------------------------


def _surface(i, first, layer, vc, tc, base):
    return vx.WorldSurface(i, first, layer, vc, tc, base)


def _layer_vertex(uv, normal_word, extra=0):
    return (
        b"\xff\x80\x40\xff"
        + struct.pack(">2f", *uv)
        + struct.pack(">2f", 0.25, 0.75)
        + struct.pack(">II", normal_word, 0x001FF800)
        + bytes(extra)
    )


def test_world_mesh_groups_and_strides():
    # Two groups: 3 vertices with stride 28, then 4 vertices with stride 36.
    pos = np.array([[i, 2 * i, 3 * i] for i in range(7)], np.float32)
    layer = b"".join(_layer_vertex((i, -i), 0x7FC00000) for i in range(3))
    layer += b"".join(_layer_vertex((i, i), 0x000003FF, 8) for i in range(4))
    # indices are relative to the group's first vertex
    idx = np.array([0, 1, 2, 0, 1, 2, 1, 2, 3], ">u2")
    surfs = [_surface(0, 0, 0, 3, 1, 0), _surface(1, 3, 84, 4, 2, 3)]
    mesh, ranges = vx.world_mesh(pos, layer, idx, surfs)
    assert ranges == [(0, 1), (1, 2)]
    assert mesh.triangles.tolist() == [[0, 1, 2], [3, 4, 5], [4, 5, 6]]
    assert np.allclose(mesh.normals[:3], [0, 0, 1])
    assert np.allclose(mesh.normals[3:], [1, 0, 0])
    assert np.allclose(mesh.uvs[4], [1, 1])
    assert mesh.colours[0].tolist() == [255, 128, 64, 255]


def test_world_group_stride_mismatch():
    surfs = [_surface(0, 0, 0, 3, 1, 0)]
    with pytest.raises(ValueError, match="whole stride"):
        vx.world_group_strides(surfs, 3, 85)


# -- OBJ / MTL -----------------------------------------------------------------------------


def test_obj_round_trip():
    pos = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]], np.float32)
    uv = np.array([[0, 0], [1, 0], [0, 1], [1, 1]], np.float32)
    n = np.tile([0, 0, 1], (4, 1)).astype(np.float32)
    mesh = ObjMesh(
        pos,
        n,
        uv,
        [ObjGroup("a", "m1", np.array([[0, 1, 2]])), ObjGroup("b", None, np.array([[0, 2, 3]]))],
    )
    text = obj_text(mesh, mtllib="x.mtl", header="test")
    assert text.startswith("# test\nmtllib x.mtl\n")
    assert "usemtl m1" in text and "g b" in text
    assert "f 1/1/1 2/2/2 3/3/3" in text
    assert text.split("vt ", 1)[1].startswith("0 1\n")  # v flipped: (0, 0) -> (0, 1)
    p, f = read_obj(text)
    assert np.allclose(p, pos)
    assert f.tolist() == [[0, 1, 2], [0, 2, 3]]


def test_obj_positions_only():
    mesh = ObjMesh(np.zeros((3, 3)), groups=[ObjGroup("g", None, np.array([[0, 1, 2]]))])
    assert "f 1 2 3" in obj_text(mesh)


def test_mtl():
    text = mtl_text({"wall": {"map_Kd": "../images/wall.png", "map_bump": None}})
    assert "newmtl wall" in text and "map_Kd ../images/wall.png" in text
    assert "map_bump" not in text


# -- collision -----------------------------------------------------------------------------


def test_box_brush_faces():
    b = col.Brush(np.array([0.0, 0, 0]), np.array([2.0, 3, 4]), 1, [], [])
    faces = col.brush_faces(b)
    assert len(faces) == 6
    pts = np.concatenate(faces)
    assert np.allclose(pts.min(0), [0, 0, 0]) and np.allclose(pts.max(0), [2, 3, 4])
    area = sum(
        0.5 * np.linalg.norm(np.cross(f[1:-1] - f[0], f[2:] - f[0]), axis=1).sum() for f in faces
    )
    assert area == pytest.approx(2 * (6 + 8 + 12))


def test_brush_with_cut_side():
    # Unit cube cut by x + y <= 1: a triangular prism with 5 faces.
    n = np.array([1.0, 1.0, 0]) / np.sqrt(2)
    b = col.Brush(np.zeros(3), np.ones(3), 1, [(n, 1 / np.sqrt(2))], [(0, 0)])
    faces = col.brush_faces(b)
    assert len(faces) == 5
    pts = np.concatenate(faces)
    assert (pts @ n <= 1 / np.sqrt(2) + 1e-6).all()


def test_parse_brushes_resolves_sides():
    brush = bytearray(0x60)
    struct.pack_into(">3fi3fII", brush, 0, 0, 0, 0, 1, 1, 1, 1, 1, 0x80000201)
    seen = []

    def sides_of(raw, count):
        seen.append((raw, count))
        return [((0.0, 0.0, 1.0), 0.5, 0, 0)]

    (b,) = col.parse_brushes(np.frombuffer(bytes(brush), cbrush_t.dtype()), sides_of)
    assert seen == [(0x80000201, 1)]
    assert b.contents == 1 and len(b.planes) == 1 and b.planes[0][1] == 0.5
    pts = np.concatenate(col.brush_faces(b))
    assert pts[:, 2].max() == pytest.approx(0.5)


def test_triangulate():
    sq = np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]], float)
    p, t = col.triangulate([sq, sq + 2])
    assert len(p) == 8 and t.tolist() == [[0, 1, 2], [0, 2, 3], [4, 5, 6], [4, 6, 7]]


# -- entities ------------------------------------------------------------------------------


def test_entities():
    raw = b'{\n"classname" "worldspawn"\n}\n{\n"origin" "1 2 3"\n"classname" "mp_tdm_spawn"\n}\n\0'
    ents = parse_entities(entity_text(raw))
    assert ents == [{"classname": "worldspawn"}, {"origin": "1 2 3", "classname": "mp_tdm_spawn"}]
    with pytest.raises(EntityError):
        parse_entities('{ "a" "b" ')


# -- images, names, misc -------------------------------------------------------------------


def test_stream_parts():
    fields = {
        "streamingMode": 1,
        "partCount": 2,
        "parts": [
            (0xB0 << 8) | 7,
            (64 << 16) | 64,
            (1 << 24) | 0x262E,
            (0x2B0 << 8) | 8,
            (128 << 16) | 128,
            2,
        ]
        + [0] * 6,
    }
    parts = stream_parts(fields)
    assert [(p.cumulative, p.mips, p.width, p.slot, p.entry) for p in parts] == [
        (0xB00, 7, 64, 1, 0x262E),
        (0x2B00, 8, 128, 0, 2),
    ]
    assert stream_parts({"streamingMode": 0}) == []


def test_rebuild_normal_flat():
    rgba = np.zeros((2, 2, 4), np.uint8)
    rgba[..., 1] = 128
    rgba[..., 3] = 128
    out = rebuild_normal(rgba)
    assert abs(int(out[0, 0, 2]) - 255) <= 1 and abs(int(out[0, 0, 0]) - 128) <= 1


def test_names():
    assert safe_name("*33n_69n(wc/a:b)") == "_33n_69n(wc_a_b)"
    t = NameTable()
    assert t.get("m", "a/b") == "a_b"
    assert t.get("m", "a:b") == "a_b~2"
    assert t.get("m", "a/b") == "a_b"


def test_jsonifier_blobs(tmp_path):
    j = Jsonifier()
    value = j.convert(
        {"small": b"\x01\x02", "big": bytes(5000), "f": float("nan")}, tmp_path / "x.blobs"
    )
    assert value["small"] == "0102" and value["f"] is None
    assert value["big"]["bytes"] == 5000 and (tmp_path / "x.blobs").is_dir()


def test_r_hash_sampler_names():
    # Values as stored in mp_nuked materials (name_hash with first/last characters).
    assert r_hash("colorMap") == 0xA0AB1041
    assert r_hash("normalMap") == 0x59D30D0F
    assert r_hash("specularMap") == 0x34ECCCB3


def test_axes_to_angles():
    assert axes_to_angles(np.eye(3)) == [0.0, 0.0, 0.0]
    yaw90 = np.array([[0, 1, 0], [-1, 0, 0], [0, 0, 1]], float)
    assert axes_to_angles(yaw90) == [0.0, 90.0, 0.0]
    a = orthonormal(np.array([[1, 0.01, 0], [0, 1, 0], [0, 0, 1]], float))
    assert np.allclose(a @ a.T, np.eye(3), atol=1e-6)


def test_render_draws_triangle():
    p = np.array([[0, 0, 0], [10, 0, 0], [0, 10, 0]], float)
    img = render(p, np.array([[0, 1, 2]]), "top", 64)
    assert img.shape == (64, 64, 3) and (img != 24).any()
