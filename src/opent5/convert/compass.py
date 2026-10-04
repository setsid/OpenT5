"""The compass (minimap) of a converted map: two corner entities, a top-down image and a
material of the map's own, stored inside the map's zone.

How the game draws it (docs/research/box-lighting.md 6; common_mp ``maps/mp/_compass.gsc``,
asset 1249): the level script calls ``setupMiniMap(<material name>)``; that function takes
the entities with targetname ``minimap_corner`` (exactly two, else it prints "There are not
exactly two "minimap_corner" entities" and returns without a minimap), orders them by
``getnorthyaw()`` into north-west and south-east, widens them to
``scr_requiredMapAspectRatio`` and calls ``setMiniMap(material, nw.x, nw.y, se.x, se.y)``.
So the material is chosen by name by the script, nothing else (no map table column or
worldspawn key). With no ``northyaw`` key (neither mp_nuked nor the box has one) north is +X:
image row 0 is the edge x = nw.x, column 0 the edge y = nw.y (checked against the stock
Nuketown compass image, box-lighting.md 6).

Which material wins when two zones carry the same name: DB_LinkXAssetEntry (t5mp.elf
0x25f390..0x25f4bc) ranks the two zones by their load flags, and the newly loaded asset
replaces the existing one when its rank is not lower (0x25f4b8 ``cmpw r7,r10``; ``bge``
0x25f914 swaps the entries and chains the old one, 0x25f924..0x25f958). Ranks from the
switch at 0x25f404..0x25fe7c: flag 0x4 -> 1, 0x40 -> 4, 0x8000 -> 10, 0x1 -> 20. The startup
zone table at 0xb34cac gives patch_mp 0x1, code_post_gfx_mp 0x4, common_mp 0x40; the level
zone is loaded with 0x8000 (0x3a4e18 ``ori r26,r10,32768`` stored at +8 of its XZoneInfo).
So a map zone's material named like one in code_post_gfx_mp (rank 1) replaces it while the
map is loaded, and patch_mp's assets (rank 20) replace the map zone's: which is why the
update's ``maps/mp/mp_nuked.gsc`` runs instead of the converted zone's own. INFERRED until a
device run: that the map's material is the one ``setMiniMap`` gets (the branch at 0x25f914
also has a deferred path, 0x25f7d0, not decoded).
"""

from __future__ import annotations

import struct

import numpy as np

from opent5.convert.images import new_image
from opent5.formats import texture as tx
from opent5.xfile.constants import PTR_INLINE, AssetType

SIZE = 512
MARGIN = 64.0
CORNER = "minimap_corner"
INLINE = struct.pack(">I", PTR_INLINE)


def material_name(map_name: str) -> str:
    """``compass_map_<map>``: the stock maps' pattern (code_post_gfx_mp assets 119..)."""
    return f"compass_map_{map_name}"


def corners(mins, maxs, margin: float = MARGIN) -> tuple[tuple[float, float], tuple[float, float]]:
    """North-west and south-east corners: a square around the world bounds (x, y), so the
    512 x 512 image keeps its aspect."""
    cx, cy = (mins[0] + maxs[0]) / 2, (mins[1] + maxs[1]) / 2
    half = max(maxs[0] - mins[0], maxs[1] - mins[1]) / 2 + margin
    return (cx + half, cy + half), (cx - half, cy - half)


def corner_entities(nw, se, z: float) -> list[dict[str, str]]:
    return [
        {"classname": "script_origin", "targetname": CORNER, "origin": f"{x:g} {y:g} {z:g}"}
        for x, y in (nw, se)
    ]


def world_triangles(gfx: dict) -> tuple[np.ndarray, np.ndarray, list[int]]:
    """Positions (n, 3), triangles (m, 3) and each triangle's surface index of a PS3
    GfxWorld node (surface +0x1c first vertex, +0x26 triangle count, +0x28 base index)."""
    pos = np.frombuffer(bytes(gfx.get("vertices") or b""), ">f4").reshape(-1, 4)[:, :3]
    idx = np.frombuffer(bytes(gfx.get("indices") or b""), ">u2").astype(np.int64)
    tris, owner = [], []
    for n, s in enumerate(gfx.get("surfaces") or ()):
        raw = s["raw"]
        (first,) = struct.unpack_from(">i", raw, 0x1C)
        (count,) = struct.unpack_from(">H", raw, 0x26)
        (base,) = struct.unpack_from(">i", raw, 0x28)
        t = idx[base : base + 3 * count].reshape(-1, 3) + first
        tris.append(t)
        owner += [n] * len(t)
    t = np.concatenate(tris) if tris else np.zeros((0, 3), np.int64)
    return pos.astype(np.float64), t, owner


def render(positions, triangles, nw, se, size: int = SIZE) -> np.ndarray:
    """RGBA (size, size, 4): floors and other upward faces filled (grey, lighter when
    higher), downward faces (ceilings) left out so the plan shows, vertical faces drawn as
    3-pixel light outlines; alpha 0 where nothing is drawn."""
    p = np.asarray(positions, np.float64)
    t = np.asarray(triangles, np.int64).reshape(-1, 3)
    row = (nw[0] - p[:, 0]) / (nw[0] - se[0]) * size
    col = (nw[1] - p[:, 1]) / (nw[1] - se[1]) * size
    img = np.zeros((size, size, 4), np.uint8)
    zbuf = np.full((size, size), -np.inf)
    if not len(t):
        return img
    a, b, c = p[t[:, 0]], p[t[:, 1]], p[t[:, 2]]
    n = np.cross(b - a, c - a)
    n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)
    z = p[:, 2]
    zlo, zhi = float(z.min()), float(z.max())
    for i in np.nonzero(n[:, 2] > 0.3)[0]:
        _fill(img, zbuf, row[t[i]], col[t[i]], z[t[i]], zlo, zhi)
    for i in np.nonzero(np.abs(n[:, 2]) <= 0.3)[0]:
        for j in range(3):
            v0, v1 = t[i, j], t[i, (j + 1) % 3]
            if abs(row[v0] - row[v1]) + abs(col[v0] - col[v1]) > 0.5:
                _line(img, (row[v0], col[v0]), (row[v1], col[v1]))
    return img


def _fill(img, zbuf, r, c, z, zlo, zhi) -> None:
    size = img.shape[0]
    area = (c[1] - c[0]) * (r[2] - r[0]) - (c[2] - c[0]) * (r[1] - r[0])
    if abs(area) < 1e-9:
        return
    r0, r1 = int(max(0, np.floor(r.min()))), int(min(size - 1, np.ceil(r.max())))
    c0, c1 = int(max(0, np.floor(c.min()))), int(min(size - 1, np.ceil(c.max())))
    if r0 > r1 or c0 > c1:
        return
    gr, gc = np.meshgrid(np.arange(r0, r1 + 1) + 0.5, np.arange(c0, c1 + 1) + 0.5, indexing="ij")
    w0 = ((c[1] - gc) * (r[2] - gr) - (c[2] - gc) * (r[1] - gr)) / area
    w1 = ((c[2] - gc) * (r[0] - gr) - (c[0] - gc) * (r[2] - gr)) / area
    w2 = 1 - w0 - w1
    inside = (w0 >= -1e-6) & (w1 >= -1e-6) & (w2 >= -1e-6)
    zz = (w0 * z[0] + w1 * z[1] + w2 * z[2])[inside]
    rr = (gr[inside] - 0.5).astype(int)
    cc = (gc[inside] - 0.5).astype(int)
    k = zz > zbuf[rr, cc]
    rr, cc, zz = rr[k], cc[k], zz[k]
    zbuf[rr, cc] = zz
    shade = 70 + 90 * (zz - zlo) / max(zhi - zlo, 1.0)
    img[rr, cc, 0:3] = shade.astype(np.uint8)[:, None]
    img[rr, cc, 3] = 200


def _line(img, a, b, width: int = 3) -> None:
    size = img.shape[0]
    steps = int(max(abs(b[0] - a[0]), abs(b[1] - a[1])) * 2) + 2
    s = np.linspace(0.0, 1.0, steps)
    r = a[0] + (b[0] - a[0]) * s
    c = a[1] + (b[1] - a[1]) * s
    h = width // 2
    for dr in range(-h, h + 1):
        for dc in range(-h, h + 1):
            rr = np.clip(np.floor(r).astype(int) + dr, 0, size - 1)
            cc = np.clip(np.floor(c).astype(int) + dc, 0, size - 1)
            img[rr, cc] = (235, 235, 235, 255)


def compass_image(name: str, rgba: np.ndarray) -> dict:
    """DXT23 (GCM 0x87), one level, as code_post_gfx_mp ``compass_map_mp_nuked``
    (``87010200 0001aae4 02000200``: DXT23 512 x 512, 1 level); stored in the zone."""
    h, w = rgba.shape[:2]
    return new_image(name, tx.DXT23, [[tx.encode_level(rgba, tx.DXT23)]], w, h)


def find_2d_material(xfile):
    """A material of the zone drawn with the ``2d`` technique set (in a map zone, the
    ``,2d`` reference to code_post_gfx_mp's; mp_nuked asset 444 ``faction_128_specops``)."""
    for a in xfile.by_type(AssetType.MATERIAL):
        ts = a.data.get("technique_set") if isinstance(a.data, dict) else None
        name = ts.get("name") if isinstance(ts, dict) else getattr(ts, "name", None)
        if name is None and hasattr(ts, "asset_index"):
            name = xfile.assets[ts.asset_index].name
        if (name or "").lstrip(",") == "2d":
            return a
    return None


def stock_material(stock_xfile, name: str) -> tuple[bytes, bytes, bytes]:
    """(header, texture def, state bits) of a stock compass material, by name; the state
    bits are followed when the material points at another material's."""
    found = [a for a in stock_xfile.by_type(AssetType.MATERIAL) if a.name == name]
    if not found:
        raise KeyError(name)
    m = found[0].data
    texture = bytes(m["textures"][0]["raw"])
    sb = m["state_bits"][0]
    bits = sb.get("bits")
    if bits is None:
        target = stock_xfile.resolve(int.from_bytes(bytes(sb["raw"]), "big"))
        owner = stock_xfile.assets[target.asset_index].data
        bits = owner["state_bits"][target.index]["bits"]
    return bytes(m["header"]), texture, bytes(bits)


def compass_material(
    name: str, stock: tuple[bytes, bytes, bytes], techset_word: bytes, techset_link, image: dict
) -> dict:
    """A Material node: the stock compass material's header, texture def and state bits,
    with its own name (inline), the zone's own ``2d`` technique set reference
    (``techset_word``, an alias pointer of the zone), its image inline and its state bits
    inline."""
    header, texture, bits = stock
    h = bytearray(header)
    h[0:4] = INLINE
    h[0x70:0x74] = techset_word
    h[0x74:0x78] = INLINE
    h[0x78:0x7C] = bytes(4)
    h[0x7C:0x80] = INLINE
    if h[0x67] != 1 or h[0x69] != 1:
        raise ValueError(
            f"stock compass material: expected 1 texture and 1 state bits entry at +0x67 / "
            f"+0x69, found {h[0x67]} and {h[0x69]}"
        )
    h[0x68] = 0
    tex = bytearray(texture)
    tex[12:16] = INLINE
    return {
        "_t": "Material",
        "header": bytes(h),
        "name": name,
        "technique_set": techset_link,
        "textures": [{"_t": "MaterialTextureDef", "raw": bytes(tex), "image": image}],
        "constants": None,
        "state_bits": [{"_t": "MaterialStateBitsRef", "raw": INLINE, "bits": bits}],
    }


def append_asset(xfile, asset_type: int, node: dict, name: str):
    """Add an asset at the end of the zone's asset list (header pointer -1: inline)."""
    from opent5.xfile.model import Asset

    entries = bytes(xfile.asset_entries or b"")
    if xfile.platform.endian != ">":
        raise ValueError("append_asset: PS3 zones only")
    stored = next(
        (s for s, t in getattr(xfile.platform, "type_map", {}).items() if t == asset_type),
        int(asset_type),
    )
    xfile.asset_entries = entries + struct.pack(">II", stored, PTR_INLINE)
    lst = bytearray(xfile.asset_list)
    count = struct.unpack_from(">I", lst, 8)[0]
    struct.pack_into(">I", lst, 8, count + 1)
    xfile.asset_list = bytes(lst)
    asset = Asset(count, asset_type, PTR_INLINE, name, 0, 0, (), (), node)
    xfile.assets.append(asset)
    return asset


def add_to_zone(xfile, stock_xfile, map_name: str, base_name: str, gfx: dict, nw, se,
                override_rgba=None):
    """Render the compass of ``gfx`` between the corners, and add ``compass_map_<map>``
    (material, its image inline) to ``xfile``, cloned from the stock
    ``compass_map_<base>`` of ``stock_xfile`` (code_post_gfx_mp). Returns (material node,
    RGBA image, report). ``override_rgba``: a ready SIZE x SIZE RGBA (the map's own coloured
    top-down) to use as the image instead of the grey geometry render."""
    import hashlib

    from opent5.convert.world import ConvertError

    name = material_name(map_name)
    if any(a.name == name for a in xfile.by_type(AssetType.MATERIAL)):
        raise ConvertError(f"compass: the zone already holds a material {name!r}")
    try:
        stock = stock_material(stock_xfile, material_name(base_name))
    except KeyError:
        raise ConvertError(
            f"compass: code_post_gfx_mp has no material {material_name(base_name)!r} to clone"
        ) from None
    donor = find_2d_material(xfile)
    if donor is None:
        raise ConvertError(
            "compass: no material of the base zone uses the 2d technique set, so the zone "
            "holds no reference to it for the compass material"
        )
    word = bytes(donor.data["header"][0x70:0x74])
    if override_rgba is not None:
        rgba = np.asarray(override_rgba)
        if rgba.shape != (SIZE, SIZE, 4):
            raise ConvertError(
                f"compass override: expected {SIZE}x{SIZE} RGBA, found {rgba.shape}"
            )
        source = "map top-down (override)"
    else:
        positions, triangles, _ = world_triangles(gfx)
        rgba = render(positions, triangles, nw, se)
        source = "gfx geometry render"
    image = compass_image(name, rgba)
    node = compass_material(name, stock, word, donor.data["technique_set"], image)
    asset = append_asset(xfile, AssetType.MATERIAL, node, name)
    report = {
        "material": name,
        "asset_index": asset.index,
        "cloned_from": f"code_post_gfx_mp {material_name(base_name)}",
        "technique_set_reference_from": f"asset {donor.index} {donor.name}",
        "image": {
            "name": name,
            "format": "DXT23",
            "width": rgba.shape[1],
            "height": rgba.shape[0],
            "bytes": len(image["pixels"]),
            "sha1": hashlib.sha1(image["pixels"]).hexdigest(),
            "drawn_pixels": int((rgba[:, :, 3] > 0).sum()),
            "source": source,
        },
        "north_west": list(nw),
        "south_east": list(se),
    }
    return node, rgba, report
