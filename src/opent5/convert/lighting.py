"""Lighting for a converted map that keeps the target zone's lightmaps.

The PC map's lightmaps are PC images (cod2rad output) and are not converted; the
target's lightmap images stay (mp_nuked: one lightmap, ``*lightmap0_primary`` DXT1 2048 x
2048, ``*lightmap0_secondary`` R5G6B5 1024 x 2048, ``*lightmap0_secondaryb`` Y16_X16
1024 x 1024, pixels deferred in the zone). The PC lightmap coordinates would sample
those images at arbitrary places.

``flat`` (the default) gives every vertex of a converted surface one lightmap
coordinate: the vertex, among the target's own surfaces drawn with the same material,
whose secondary lightmap texel is brightest in a uniform 5 x 5 neighbourhood (standard
deviation of the luminance at most 6 of 255, at least 3 texels from the image border). The
surface then takes that donor surface's lightmap and reflection probe indices. So a wall
is lit the way the brightest even patch of the same material in the stock map is lit:
uniform, never black. The secondary image carries the colour (mean RGB 60, 66, 73 in
mp_nuked); the primary is one channel (green; INFERRED sun visibility), and a closed box
is in the sun's shadow at run time (INFERRED), so the choice ranks by the secondary.
``keep`` leaves the PC coordinates (and the PC lightmap and probe indices).
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

import numpy as np

from opent5.convert.world import ConvertError, surface_material_name


@dataclass
class Donor:
    material: str
    surface: int
    vertex: int
    uv: tuple[float, float]
    luminance: float
    light: bytes  # +0x44..+0x47 of the donor surface


def _groups(surfaces: list[dict], vertex_count: int, layer_size: int) -> dict[int, int]:
    """first vertex -> stride of every vertex group of a PS3 GfxWorld."""
    starts = sorted(
        {
            (
                struct.unpack_from(">i", s["raw"], 0x1C)[0],
                struct.unpack_from(">i", s["raw"], 0xC)[0],
            )
            for s in surfaces
        }
    )
    out = {}
    for i, (first, off) in enumerate(starts):
        nf, no = starts[i + 1] if i + 1 < len(starts) else (vertex_count, layer_size)
        out[first] = (off, (no - off) // max(nf - first, 1))
    return out


def _box5(a: np.ndarray) -> np.ndarray:
    """Mean over the 5 x 5 neighbourhood of every pixel (edges repeated)."""
    p = np.pad(a, 2, mode="edge")
    c = np.zeros((p.shape[0] + 1, p.shape[1] + 1))
    c[1:, 1:] = p.cumsum(axis=0).cumsum(axis=1)
    h, w = a.shape
    total = c[5 : 5 + h, 5 : 5 + w] - c[0:h, 5 : 5 + w] - c[5 : 5 + h, 0:w] + c[0:h, 0:w]
    return total / 25.0


def _uniform(image: np.ndarray, channels: list[int]) -> tuple[np.ndarray, np.ndarray]:
    """5 x 5 mean and standard deviation of the mean of ``channels`` (0..255)."""
    a = image[:, :, channels].astype(np.float64).mean(axis=2)
    mean = _box5(a)
    return mean, np.sqrt(np.maximum(_box5(a * a) - mean * mean, 0.0))


def find_donors(
    stock: dict,
    secondary: np.ndarray,
    materials: set[str],
    primary: np.ndarray | None = None,
    sunlit: bool = False,
    allow_missing: bool = False,
) -> dict[str, Donor]:
    """Per material, the brightest uniform secondary lightmap sample among the stock
    surfaces. ``sunlit`` (needs ``primary``) only takes samples whose primary green is
    uniform and at least 200 of 255 (lit by the sun in the stock map, INFERRED);
    otherwise the samples found have primary 0 (in shade) in mp_nuked. The primary of
    mp_nuked is a mask: its red and blue are 0 everywhere, its green is 0 on 89% of the
    texels and 240 or more on 9% (INFERRED sun visibility). ``allow_missing`` returns
    what was found instead of raising."""
    surfaces = stock.get("surfaces") or []
    vertex_count = len(stock.get("vertices") or b"") // 16
    layer = bytes(stock.get("vertex_layer_data") or b"")
    groups = _groups(surfaces, vertex_count, len(layer))
    lum = secondary[:, :, :3].astype(np.float64) @ np.array([0.299, 0.587, 0.114])
    h, w = lum.shape
    mean = _box5(lum)
    std = np.sqrt(np.maximum(_box5(lum * lum) - mean * mean, 0.0))
    if sunlit:
        if primary is None:
            raise ConvertError("lighting: sunlit donors need the primary lightmap")
        pmean, pstd = _uniform(primary, [1])
        ph, pw = pmean.shape
    out: dict[str, Donor] = {}
    for n, s in enumerate(surfaces):
        name = surface_material_name(s)
        raw = bytes(s["raw"])
        if name not in materials or raw[0x44] == 0x1F:  # 0x1f: no lightmap (sky, decals)
            continue
        first, off, count = struct.unpack_from(">iiH", raw, 0x1C)
        group_layer, stride = groups[first]
        for k in range(off, off + count):
            at = group_layer + k * stride + 12
            u, v = struct.unpack_from(">2f", layer, at)
            x, y = int(u * w), int(v * h)
            # away from the image border (bilinear filtering and padding there) and
            # in an even patch (not on a shadow edge or chart seam)
            if not (3 <= x < w - 3 and 3 <= y < h - 3) or std[y, x] > 6.0:
                continue
            if sunlit:
                px, py = int(u * pw), int(v * ph)
                if not (3 <= px < pw - 3 and 3 <= py < ph - 3):
                    continue
                if pmean[py, px] < 200 or pstd[py, px] > 6.0:
                    continue
            score = float(mean[y, x])
            best = out.get(name)
            if best is None or score > best.luminance:
                out[name] = Donor(name, n, first + k, (u, v), score, raw[0x44:0x48])
    missing = materials - set(out)
    if missing and not allow_missing:
        raise ConvertError(
            f"lighting: no uniformly lit stock surface for {sorted(missing)}; use --lighting keep"
        )
    return out


def apply_flat(
    layer: bytearray,
    records: list[bytes],
    names: list[str],
    groups_stride: dict[int, tuple[int, int]],
    donors: dict[str, Donor],
) -> list[bytes]:
    """Set every vertex of each converted surface to its donor's lightmap uv; returns the
    surface records with the donor's lightmap and probe indices (+0x44, +0x45)."""
    out = []
    for rec, name in zip(records, names, strict=True):
        d = donors[name]
        first, off, count = struct.unpack_from(">iiH", rec, 0x1C)
        group_layer, stride = groups_stride[first]
        for k in range(off, off + count):
            struct.pack_into(">2f", layer, group_layer + k * stride + 12, *d.uv)
        rec = bytearray(rec)
        rec[0x44:0x46] = d.light[0:2]
        out.append(bytes(rec))
    return out
