"""Wavefront OBJ and MTL writers.

Coordinates are written as the game stores them: inches, Z up. Texture v is
flipped (OBJ's v runs upwards, the game's downwards). Faces keep the game's
winding.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np


@dataclass
class ObjGroup:
    """Triangles of one object/material, indexing the writer's shared vertex arrays."""

    name: str
    material: str | None
    triangles: np.ndarray  # (m, 3) int, 0-based into the shared vertices


@dataclass
class ObjMesh:
    positions: np.ndarray
    normals: np.ndarray | None = None
    uvs: np.ndarray | None = None
    groups: list[ObjGroup] = field(default_factory=list)


def _block(fmt: str, rows: np.ndarray) -> str:
    if len(rows) == 0:
        return ""
    buf = io.StringIO()
    np.savetxt(buf, rows, fmt=fmt)
    return buf.getvalue()


def obj_text(mesh: ObjMesh, mtllib: str | None = None, header: str = "") -> str:
    """The OBJ file for one mesh (several groups sharing one vertex set)."""
    out = io.StringIO()
    for line in header.splitlines():
        out.write(f"# {line}\n")
    if mtllib:
        out.write(f"mtllib {mtllib}\n")
    pos = np.asarray(mesh.positions, np.float64)
    out.write(_block("v %.6g %.6g %.6g", pos))
    has_uv = mesh.uvs is not None
    has_n = mesh.normals is not None
    if has_uv:
        uv = np.asarray(mesh.uvs, np.float64).copy()
        uv[:, 1] = 1.0 - uv[:, 1]
        out.write(_block("vt %.6g %.6g", uv))
    if has_n:
        out.write(_block("vn %.4f %.4f %.4f", np.asarray(mesh.normals, np.float64)))
    if has_uv and has_n:
        fmt = "f %d/%d/%d %d/%d/%d %d/%d/%d"
        reps = 3
    elif has_uv or has_n:
        fmt = "f %d/%d %d/%d %d/%d" if has_uv else "f %d//%d %d//%d %d//%d"
        reps = 2
    else:
        fmt = "f %d %d %d"
        reps = 1
    for g in mesh.groups:
        out.write(f"g {g.name}\n")
        if g.material:
            out.write(f"usemtl {g.material}\n")
        t = np.asarray(g.triangles, np.int64) + 1
        if len(t) == 0:
            continue
        cols = np.repeat(t, reps, axis=1)
        out.write(_block(fmt, cols))
    return out.getvalue()


def write_obj(path: Path, mesh: ObjMesh, mtllib: str | None = None, header: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(obj_text(mesh, mtllib, header), encoding="utf-8")


def mtl_text(materials: dict[str, dict[str, str | None]]) -> str:
    """name -> {"map_Kd": path, "map_bump": path, "map_Ks": path} (missing -> omitted)."""
    out = io.StringIO()
    for name, maps in materials.items():
        out.write(f"newmtl {name}\nKd 1 1 1\nKa 0 0 0\nKs 0 0 0\nd 1\nillum 1\n")
        for key in ("map_Kd", "map_bump", "map_Ks"):
            value = maps.get(key)
            if value:
                out.write(f"{key} {value}\n")
        out.write("\n")
    return out.getvalue()


def read_obj(text: str) -> tuple[np.ndarray, np.ndarray]:
    """Positions and triangle vertex indices (0-based) of an OBJ; for tests and previews."""
    pos, faces = [], []
    for line in text.splitlines():
        if line.startswith("v "):
            pos.append([float(v) for v in line.split()[1:4]])
        elif line.startswith("f "):
            faces.append([int(p.split("/")[0]) - 1 for p in line.split()[1:4]])
    return np.array(pos, np.float64).reshape(-1, 3), np.array(faces, np.int64).reshape(-1, 3)
