"""Pointer resolution: alias references to their assets, offset pointers to the
node / array element they name (skipped without .env)."""

import struct

import pytest

from opent5 import env
from opent5.xfile import AssetType, parse
from opent5.xfile.constants import encode_offset_pointer
from opent5.xfile.events import EventKind, PtrKind
from opent5.xfile.refs import Refs
from opent5.xfile.stream import AssetLink


class TestRefsUnit:
    def test_allocation_lookup_and_elements(self):
        refs = Refs()
        node = {"items": [{"raw": b"a" * 8}, {"raw": b"b" * 8}]}
        refs.record(4, 0x100, 16, node, "items", 8, 3)
        t = refs.resolve(encode_offset_pointer(4, 0x10C))
        assert (t.kind, t.key, t.index, t.within, t.asset_index) == ("data", "items", 1, 4, 3)
        assert t.element is node["items"][1]
        assert refs.resolve(encode_offset_pointer(4, 0x110)).kind == "unknown"
        assert refs.resolve(0xFFFFFFFF) is None

    def test_alias_chains(self):
        refs = Refs()
        asset = {"name": "m"}
        refs.slot(0x40, asset, 7)
        refs.chain[0x80] = 0x40
        t = refs.resolve(0x81)
        assert t.kind == "asset" and t.asset is asset and t.name == "m" and t.asset_index == 7


def zone_path(name: str):
    for key in ("OPENT5_PATCH_ZONES", "OPENT5_ZONES"):
        folder = env.path_of(key)
        if folder and (folder / f"{name}.ff").is_file():
            return folder / f"{name}.ff"
    return None


@pytest.fixture(scope="module", params=["patch_mp", "mp_nuked"])
def parsed(request):
    from opent5.container.zone import Zone

    path = zone_path(request.param)
    if path is None:
        pytest.skip(f"{request.param}.ff is not on this machine")
    return request.param, parse(bytes(Zone.open(path).content))


def links(node, out, seen):
    if isinstance(node, dict):
        if id(node) in seen:
            return
        seen.add(id(node))
        for v in node.values():
            links(v, out, seen)
    elif isinstance(node, list):
        for v in node:
            links(v, out, seen)
    elif isinstance(node, AssetLink):
        out.append(node)


@pytest.mark.zones
class TestOnZones:
    def test_every_alias_reference_names_its_asset(self, parsed):
        _, xfile = parsed
        found: list = []
        for asset in xfile.assets:
            links(asset.data, found, set())
        assert found
        assert all(link.target is not None and link.name is not None for link in found)
        assert all(link.asset_index >= 0 for link in found)

    def test_every_logged_pointer_resolves(self, parsed):
        _, xfile = parsed
        rows = xfile.log.of_kind(EventKind.POINTER)
        for raw, kind in rows[:, [2, 3]].tolist():
            if kind == PtrKind.OFFSET:
                assert xfile.resolve(raw).kind in ("data", "asset"), hex(raw)
            elif kind == PtrKind.ALIAS_REF:
                assert xfile.resolve(raw).asset is not None, hex(raw)

    def test_shared_data_is_reachable_by_name(self, parsed):
        name, xfile = parsed
        # A techset sharing another's technique: the target is that technique, named.
        shared = [
            t
            for a in xfile.assets
            if a.type == AssetType.TECHSET
            for t in xfile.view(a.data).target("techniques")
            if t is not None
        ]
        assert shared and all(t.kind == "data" and t.node.get("name") for t in shared)
        if name != "mp_nuked":
            return
        # Brush sides point into the clipMap's plane array, element by element.
        clip = next(a for a in xfile.assets if a.type == AssetType.COL_MAP_MP)
        view = xfile.view(clip.data)
        assert view.target("planes").key == "planes"
        side = view.array("brushsides")[0]
        t = xfile.resolve(int(side["plane"]))
        assert t.key == "planes" and t.within % 20 == 0
        # A model surface reusing another surface's vertices names that surface's model.
        hits = []
        for a in xfile.assets:
            if a.type != AssetType.XMODEL:
                continue
            for surface in a.data["surfs"] or []:
                t = xfile.view(surface).target("verts0")
                if t is not None:
                    hits.append((t.key, t.node.get("_t")))
        assert hits and all(h == ("verts0", "XSurface") for h in hits)
        # A GfxWorld surface's material alias names a material.
        world = next(a for a in xfile.assets if a.type == AssetType.GFX_MAP)
        surface = xfile.view(world.data).child("surfaces")[0]
        material = surface.target("material").asset
        assert material["_t"] == "Material" and material["name"]

    def test_script_and_asset_name_strings(self, parsed):
        _, xfile = parsed
        for a in xfile.assets:
            h = a.data.get("header") if isinstance(a.data, dict) else None
            if a.type == AssetType.LOCALIZE and h is not None:
                raw = struct.unpack_from(">I", h, 4)[0]
                if raw not in (0, 0xFFFFFFFF):
                    assert xfile.resolve(raw).name == a.data["name"]
                    return
