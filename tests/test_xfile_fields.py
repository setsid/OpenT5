"""The typed field layer: schemas, in-place views, numpy arrays, to_dict, and the
field checks on real zones (zone tests skipped without .env)."""

import json
import struct

import numpy as np
import pytest

from opent5 import env
from opent5.xfile import AssetType, parse, write_asset
from opent5.xfile.fieldcheck import check_zone
from opent5.xfile.handlers.rawfile import RawFile
from opent5.xfile.handlers.stringtable import StringTable
from opent5.xfile.schema import (
    ArrayOf,
    Dynamic,
    Field,
    FieldError,
    Struct,
    StructView,
    to_dict,
    unpack_cmp,
    view,
)
from opent5.xfile.structs import KINDS, XSurface


class TestStructs:
    def test_every_schema_covers_its_struct_exactly(self):
        for kind, schema in KINDS.items():
            for key, spec in schema.items():
                struct_ = spec.element if isinstance(spec, ArrayOf) else spec
                if not isinstance(struct_, Struct):
                    continue
                end = 0
                for f in struct_.fields:
                    assert f.offset == end, f"{kind}[{key}] {struct_.name}.{f.name}"
                    end = f.offset + f.size
                assert end == struct_.size, f"{kind}[{key}] {struct_.name}"

    def test_gaps_become_unknown_fields(self):
        s = Struct("t", 12, [Field("a", 0, "u32"), Field("b", 8, "u16")])
        assert [f.name for f in s.fields] == ["a", "unk_0x4", "b", "unk_0xa"]
        assert s.coverage() == (6, 6)

    def test_overlaps_are_refused(self):
        with pytest.raises(ValueError, match="overlaps"):
            Struct("t", 8, [Field("a", 0, "u32"), Field("b", 2, "u16")])

    def test_dtype_matches_the_bytes(self):
        s = Struct("t", 16, [Field("x", 0, "vec3"), Field("n", 12, "u16")])
        data = struct.pack(">3fH2x", 1.0, -2.0, 3.5, 7)
        arr = np.frombuffer(data, dtype=s.dtype())
        assert arr["x"][0].tolist() == [1.0, -2.0, 3.5] and int(arr["n"][0]) == 7

    def test_cmp_unpacks_axes(self):
        assert unpack_cmp(0x000003FF) == (1.0, 0.0, 0.0)
        assert unpack_cmp(0x00200800) == (0.0, -1.0, 0.0)


class TestViews:
    def test_get_and_set_in_place(self):
        node = RawFile.build("a.txt", b"abc")
        node["_t"] = "RawFile"
        fields = view(node).fields
        assert fields.len == 3
        node["header"] = bytearray(node["header"])
        with pytest.raises(FieldError, match="read-only"):
            fields.len = 4
        with pytest.raises(FieldError, match="read-only"):
            fields.name = 0

    def test_set_writes_big_endian_bytes(self):
        s = Struct("t", 8, [Field("f", 0, "f32"), Field("c", 4, "char[4]")])
        node = {"raw": bytes(8)}
        v = StructView(s, node, "raw")
        v.f = 1.5
        v["c"] = "ab"
        assert node["raw"] == struct.pack(">f", 1.5) + b"ab\0\0"
        assert v.to_dict() == {"f": 1.5, "c": "ab"}

    def test_array_views_and_dynamic_vertex_formats(self):
        raw = bytearray(XSurface.size)
        struct.pack_into(">H", raw, 2, 0)  # flags & 1 == 0: float positions
        node = {
            "_t": "XSurface",
            "raw": bytes(raw),
            "verts0": struct.pack(">4f4f", 1, 2, 3, 1, 4, 5, 6, -1),
        }
        v = view(node)
        arr = v.array("verts0")
        assert arr["xyz"].tolist() == [[1, 2, 3], [4, 5, 6]]
        assert isinstance(KINDS["XSurface"]["verts0"], Dynamic)
        writable = v.array("verts0", writable=True)
        writable["xyz"][1] = (7, 8, 9)
        assert struct.unpack_from(">3f", node["verts0"], 16) == (7, 8, 9)

    def test_to_dict_is_json_ready(self):
        table = StringTable.build("t.csv", [["x", "y"]])
        table["_t"] = "StringTable"
        for cell in table["cells"]:
            cell["_t"] = "StringTableCell"
        out = to_dict(table)
        json.dumps(out)
        assert out["header"]["columnCount"] == 2
        assert out["cells"][1]["raw"]["hash"] == table.hashes()[1]


SAMPLE = (env.path_of("OPENT5_PATCH_ZONES") or env.ROOT / "missing") / "patch_mp.ff"


@pytest.mark.zones
@pytest.mark.skipif(not SAMPLE.is_file(), reason="patch_mp.ff is not on this machine")
class TestOnAZone:
    @pytest.fixture(scope="class")
    def zone(self):
        from opent5.container.zone import Zone

        content = bytes(Zone.open(SAMPLE).content)
        return content, parse(content, log=False)

    def test_every_byte_value_has_a_schema(self, zone):
        _, xfile = zone
        check = check_zone(xfile.assets, set_fields=False)
        assert dict(check.untyped) == {}
        assert dict(check.unknown_kinds) == {}

    def test_setting_every_field_to_itself_changes_nothing(self, zone):
        content, xfile = zone
        check = check_zone(xfile.assets, set_fields=True)
        assert sum(s.round_trip_failures for s in check.structs.values()) == 0
        for asset in xfile.assets:
            assert write_asset(asset) == content[asset.file_start : asset.file_end]

    def test_counts_match_their_arrays(self, zone):
        _, xfile = zone
        check = check_zone(xfile.assets, set_fields=False)
        counts = {k: s for k, s in check.sanity.items() if ": count = len" in k}
        assert counts and all(s.bad == 0 for s in counts.values())

    def test_view_of_a_material(self, zone):
        _, xfile = zone
        material = next(a for a in xfile.assets if a.type == AssetType.MATERIAL).data
        fields = view(material).fields
        assert fields["info.name"] == 0xFFFFFFFF or fields["info.name"] > 0
        assert fields.textureCount == len(material.get("textures") or [])
        json.dumps(view(material).to_dict(max_items=4))
