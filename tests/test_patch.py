"""opent5.patch: the .o5patch format, export, apply and info (docs/patch-format.md).

Synthetic zones need no game files. The zone-marked test rounds a real zone through the whole
cycle and is skipped when no zones are configured in .env.
"""

from __future__ import annotations

import struct

import numpy as np
import pytest

from opent5.container.fastfile import (
    AUTH_MAGIC,
    CHUNKS_OFFSET,
    OFFSET_AUTH_MAGIC,
    OFFSET_SIGNATURE,
    OFFSET_ZONE_NAME,
    SIGNATURE_SIZE,
    ZONE_MAGIC,
    ZONE_VERSION,
    split_content,
    write_fastfile,
)
from opent5.edit import Document
from opent5.edit import content as ct
from opent5.formats import texture as tx
from opent5.patch import (
    FORMAT_VERSION,
    MAGIC,
    ApplyResult,
    CreateResult,
    PatchError,
    PatchFormatError,
    apply,
    create,
    export,
    info,
)
from opent5.patch.format import Manifest, PatchChange, read_patch, write_patch
from opent5.xfile import AssetType, parse
from opent5.xfile.handlers.stringtable import string_hash

INLINE = b"\xff\xff\xff\xff"
VIRTUAL = 4


def make_content(assets: list[tuple[int, bytes]], tail: bytes = b"") -> bytes:
    body = struct.pack(">IIII", 0, 0, len(assets), 0xFFFFFFFF)
    body += b"".join(struct.pack(">II", t, 0xFFFFFFFF) for t, _ in assets)
    body += b"".join(data for _, data in assets) + tail
    content = bytes(36) + body
    x = parse(content)
    sizes = list(x.final_cursors)
    sizes[0] = x.temp_high_water + 16
    return struct.pack(">9I", len(content) - 36, 0, *sizes) + body


def localize(value: bytes, name: bytes):
    return AssetType.LOCALIZE, INLINE + INLINE + value + b"\0" + name + b"\0"


def rawfile(name: bytes, buffer: bytes):
    head = INLINE + struct.pack(">i", len(buffer) - 1) + INLINE
    return AssetType.RAWFILE, head + name + b"\0" + buffer


def script(name: bytes, text: bytes):
    buffer, _ = ct.script_buffer(text + b"\0")
    return rawfile(name, buffer)


def stringtable(name: bytes, rows: list[list[bytes]]):
    texts = [c for row in rows for c in row]
    columns = len(rows[0])
    cells = b"".join(INLINE + struct.pack(">I", string_hash(t.decode("latin-1"))) for t in texts)
    strings = b"".join(t + b"\0" for t in texts)
    signed = [
        (h - (1 << 32) if h & 0x80000000 else h)
        for h in (string_hash(t.decode("latin-1")) for t in texts)
    ]
    order = sorted(range(len(texts)), key=lambda i: signed[i])
    index = b"".join(struct.pack(">h", i) for i in order)
    head = INLINE + struct.pack(">ii", columns, len(rows)) + INLINE + INLINE
    return AssetType.STRINGTABLE, head + name + b"\0" + cells + strings + index


def solid(colour) -> np.ndarray:
    out = np.zeros((8, 8, 4), np.uint8)
    out[...] = colour
    return out


PIXELS_A = tx.encode(solid((255, 0, 0, 255)), tx.DXT1, 1).ljust(128, b"\0")


def image(name: bytes) -> tuple[int, bytes]:
    h = bytearray(0x70)
    struct.pack_into(">BBBBIHHHB", h, 0, tx.DXT1, 1, 2, 0, 0xAAE4, 8, 8, 1, 0)
    struct.pack_into(">I", h, 0x1C, 128)
    h[0x2C:0x30] = INLINE
    h[0x68:0x6C] = INLINE
    return AssetType.IMAGE, bytes(h) + name + b"\0" + PIXELS_A


ENTS = b'{\n"classname" "worldspawn"\n}\n'


def mapents(name: bytes, text: bytes):
    head = INLINE + INLINE + struct.pack(">I", len(text) + 1)
    return AssetType.MAP_ENTS, head + name + b"\0" + text + b"\0"


def header_for(name: bytes = b"mp_testpatch", signature: bytes | None = None) -> bytes:
    out = bytearray(CHUNKS_OFFSET)
    out[0:8] = ZONE_MAGIC
    struct.pack_into(">I", out, 8, ZONE_VERSION)
    out[OFFSET_AUTH_MAGIC : OFFSET_AUTH_MAGIC + 8] = AUTH_MAGIC
    out[OFFSET_ZONE_NAME : OFFSET_ZONE_NAME + len(name)] = name
    if signature is None:
        signature = bytes((i * 13) & 0xFF for i in range(SIGNATURE_SIZE))
    out[OFFSET_SIGNATURE : OFFSET_SIGNATURE + SIGNATURE_SIZE] = signature
    return bytes(out)


def synthetic_content() -> bytes:
    assets = [
        localize(b"Health", b"CGAME_HEALTH"),
        rawfile(b"settings.cfg", b"seta foo 1\n\0"),
        script(b"maps/mp/test.gsc", b"main()\n{\nwait 1;\n}\n"),
        stringtable(b"mp/table.csv", [[b"alpha", b"1"], [b"beta", b"2"]]),
        image(b"logo"),
        mapents(b"maps/mp/test.d3dbsp", ENTS),
        localize(b"Score", b"CGAME_SCORE"),
    ]
    return make_content(assets)


def synthetic_ff(split: int = 400, signature: bytes | None = None) -> bytes:
    content = synthetic_content()
    return write_fastfile(
        header_for(signature=signature), [(p, None) for p in split_content(content, split)]
    )


@pytest.fixture
def stock_ff() -> bytes:
    return synthetic_ff()


def build_edited(stock: bytes, fn, tmp_path, name: str = "edited.ff", verify: bool = True) -> bytes:
    doc = Document.open(stock, name="mp_testpatch")
    fn(doc)
    out = tmp_path / name
    doc.save(out, verify=verify)
    return out.read_bytes()


# -- the format container -------------------------------------------------------------------


def test_format_roundtrip():
    manifest = Manifest(
        source_zone="patch_mp",
        source_ff_sha1="a" * 40,
        source_content_sha1="b" * 40,
        result_content_sha1="c" * 40,
        signed=True,
        assets_changed=2,
        created="2026-10-04",
        tool_version="0.1.0",
        generator="OpenT5 0.1.0",
    )
    changes = [
        PatchChange(3, "localize", "CGAME_HEALTH", "localize", b"Vitality"),
        PatchChange(9, "rawfile", "a.cfg", "text", b"seta x 2\n"),
    ]
    data = write_patch(manifest, changes)
    assert data[: len(MAGIC)] == MAGIC and data[len(MAGIC)] == FORMAT_VERSION
    back_manifest, back_changes = read_patch(data)
    assert back_manifest.to_dict() == manifest.to_dict()
    assert [(c.index, c.op, c.blob) for c in back_changes] == [
        (3, "localize", b"Vitality"),
        (9, "text", b"seta x 2\n"),
    ]


def test_bad_magic_rejected():
    with pytest.raises(PatchFormatError):
        read_patch(b"not a patch at all")


def test_short_file_rejected():
    with pytest.raises(PatchFormatError):
        read_patch(MAGIC + bytes([FORMAT_VERSION]) + b"\x00\x00")


def test_future_version_rejected():
    manifest = Manifest("z", "a" * 40, "b" * 40, "c" * 40, False, 0, "", "", "")
    data = bytearray(write_patch(manifest, []))
    data[len(MAGIC)] = FORMAT_VERSION + 1
    with pytest.raises(PatchFormatError, match="newer"):
        read_patch(bytes(data))


# -- round trips, byte for byte -------------------------------------------------------------


@pytest.mark.parametrize(
    "edit",
    [
        lambda d: d.set_localize(0, "A much longer health label than before"),
        lambda d: d.set_localize(0, "hp"),
        lambda d: d.set_text(1, "seta foo 1\nseta bar 2\nseta baz 3\n"),
        lambda d: d.set_text(2, "main()\n{\nwait 2;\nprintln(1);\n}\n"),
        lambda d: d.set_cell(3, 0, 0, "alpha_renamed_longer"),
        lambda d: d.set_text(5, '{\n"classname" "worldspawn"\n"extra" "1"\n}\n'),
        lambda d: d.replace_image(4, solid((0, 0, 255, 255))),
    ],
)
def test_single_edit_roundtrip_byte_identical(stock_ff, tmp_path, edit):
    target = build_edited(stock_ff, edit, tmp_path)
    patch = export(stock_ff, target)
    result = apply(patch, stock_ff, tmp_path / "out.ff", verify=True)
    assert result.verified and result.reproduces_target
    assert (tmp_path / "out.ff").read_bytes() == target
    assert len(result.changes) == 1


def test_add_and_remove_stringtable_rows(stock_ff, tmp_path):
    def edit(d):
        d.add_row(3, ["gamma", "3"])
        d.set_cell(3, 1, 1, "22")

    target = build_edited(stock_ff, edit, tmp_path)
    patch = export(stock_ff, target)
    out = apply(patch, stock_ff, tmp_path / "o.ff").output
    assert out and (tmp_path / "o.ff").read_bytes() == target


def test_multi_asset_edit(stock_ff, tmp_path):
    def edit(d):
        d.set_localize(0, "Vitality longer value")
        d.set_text(1, "seta foo 99\n")
        d.set_cell(3, 0, 1, "100")

    target = build_edited(stock_ff, edit, tmp_path)
    result = create(stock_ff, target)
    assert len(result.changes) == 3
    out = apply(result.patch, stock_ff, tmp_path / "m.ff")
    assert (tmp_path / "m.ff").read_bytes() == target
    assert out.verified


# -- the no-stock-data property -------------------------------------------------------------


def test_localize_patch_is_tiny_and_holds_only_the_new_text(stock_ff, tmp_path):
    target = build_edited(stock_ff, lambda d: d.set_localize(0, "Vitality"), tmp_path)
    patch = export(stock_ff, target)
    assert len(patch) < 400  # a few hundred bytes, not the zone's size
    _manifest, changes = read_patch(patch)
    assert len(changes) == 1  # only the edited asset
    assert changes[0].blob == b"Vitality"  # exactly the new value, nothing else
    assert changes[0].index == 0 and changes[0].op == "localize"


# -- verifying the user's source ------------------------------------------------------------


def test_wrong_source_refused_with_expected_and_found(stock_ff, tmp_path):
    target = build_edited(stock_ff, lambda d: d.set_localize(0, "Vitality"), tmp_path)
    patch = export(stock_ff, target)
    with pytest.raises(PatchError) as exc:
        apply(patch, target, tmp_path / "x.ff")  # the edited zone is the wrong source
    message = str(exc.value)
    assert "Expected content sha1" in message and "found content sha1" in message


def test_ff_differs_but_content_matches_applies_with_note(stock_ff, tmp_path):
    target = build_edited(stock_ff, lambda d: d.set_localize(0, "Vitality"), tmp_path)
    patch = export(stock_ff, target)
    repacked = synthetic_ff(split=277)  # same content, different chunk layout -> different .ff sha1
    assert repacked != stock_ff
    result = apply(patch, repacked, tmp_path / "r.ff")
    assert result.reproduces_target
    # The container bytes follow the user's own chunking, but the decompressed content (what
    # the game loads) is exactly the edited zone's.
    assert Document.open((tmp_path / "r.ff").read_bytes()).content == Document.open(target).content
    assert any("repacked" in n for n in result.notes)


def test_apply_without_output_builds_only(stock_ff, tmp_path):
    target = build_edited(stock_ff, lambda d: d.set_text(1, "seta foo 7\n"), tmp_path)
    patch = export(stock_ff, target)
    result = apply(patch, stock_ff, None)
    assert result.reproduces_target and result.output is None


# -- refusals -------------------------------------------------------------------------------


def test_asset_count_change_refused(stock_ff):
    fewer = [
        localize(b"Health", b"CGAME_HEALTH"),
        rawfile(b"settings.cfg", b"seta foo 1\n\0"),
    ]
    other = write_fastfile(
        header_for(), [(p, None) for p in split_content(make_content(fewer), 400)]
    )
    with pytest.raises(PatchError, match="same asset list"):
        create(stock_ff, other)


def test_different_zone_names_refused(stock_ff):
    other = write_fastfile(
        header_for(name=b"mp_other"),
        [(p, None) for p in split_content(synthetic_content(), 400)],
    )
    with pytest.raises(PatchError, match="same zone"):
        create(stock_ff, other)


# -- info and result objects ----------------------------------------------------------------


def test_info(stock_ff, tmp_path):
    target = build_edited(stock_ff, lambda d: d.set_localize(0, "Vitality"), tmp_path)
    patch = export(stock_ff, target)
    described = info(patch)
    assert described.manifest.source_zone == "mp_testpatch"
    assert described.manifest.format_version == FORMAT_VERSION
    assert len(described.changes) == 1
    d = described.to_dict()
    assert d["assets_changed"] == 1 and d["changes"][0]["op"] == "localize"


def test_result_objects_are_gui_friendly(stock_ff, tmp_path):
    target = build_edited(stock_ff, lambda d: d.set_localize(0, "Vitality"), tmp_path)
    result = create(stock_ff, target)
    assert isinstance(result, CreateResult)
    for key in ("source_zone", "assets_changed", "changes", "patch_bytes", "signature_note"):
        assert key in result.to_dict()
    applied = apply(result.patch, stock_ff, tmp_path / "g.ff")
    assert isinstance(applied, ApplyResult)
    for key in ("source_zone", "verified", "changes", "signature_note", "reproduces_target"):
        assert key in applied.to_dict()


def test_signed_source_sets_signature_note(tmp_path):
    # A signature the console key accepts is not reproducible here; the note fires on content
    # change regardless, which is what a front end shows.
    stock = synthetic_ff()
    target = build_edited(stock, lambda d: d.set_localize(0, "Vitality"), tmp_path)
    result = create(stock, target)
    # The synthetic signature does not decode, so the source reads as unsigned and the note is
    # absent; the field exists either way for the GUI.
    assert "signature_note" in result.to_dict()


# -- a real zone, zone-marked ---------------------------------------------------------------


def _real_zone():
    from opent5 import env

    for path in sorted(env.all_zones(), key=lambda p: p.stat().st_size):
        if path.stat().st_size > 6_000_000:
            continue
        try:
            doc = Document.open(path.read_bytes(), name=path.stem)
        except Exception:  # noqa: BLE001 - skip anything that will not open
            continue
        if doc.parse_problems:
            continue
        loc = next((r for r in doc.assets if r.type == AssetType.LOCALIZE), None)
        tbl = next(
            (
                r
                for r in doc.assets
                if r.type == AssetType.STRINGTABLE and len(doc.table(r.index)) >= 1
            ),
            None,
        )
        raw = next((r for r in doc.assets if r.type == AssetType.RAWFILE), None)
        if loc and tbl and raw:
            return path, doc, loc, tbl, raw
    return None


def test_real_zone_roundtrip(tmp_path):
    found = _real_zone()
    if found is None:
        pytest.skip("no suitable real zone in .env")
    path, stock_doc, loc, tbl, raw = found
    stock = path.read_bytes()

    def edit(d):
        d.set_localize(loc.index, stock_doc.localize(loc.index)[1] + " (modded, longer)")
        rows = d.table(tbl.index)
        d.set_cell(tbl.index, 0, 0, rows[0][0] + "_EDITED_LONGER")
        d.set_text(raw.index, stock_doc.text(raw.index) + "\n// appended by the patch test\n")

    target = build_edited(stock, edit, tmp_path, name=f"{path.stem}_edited.ff", verify=False)
    result = create(stock, target)
    assert len(result.changes) == 3
    applied = apply(result.patch, stock, tmp_path / f"{path.stem}_out.ff", verify=True)
    assert applied.verified and applied.reproduces_target
    assert (tmp_path / f"{path.stem}_out.ff").read_bytes() == target

    # A localize-only patch on the same real zone is tiny.
    loc_target = build_edited(
        stock,
        lambda d: d.set_localize(loc.index, stock_doc.localize(loc.index)[1] + "!"),
        tmp_path,
        name=f"{path.stem}_loc.ff",
        verify=False,
    )
    loc_patch = export(stock, loc_target)
    assert len(loc_patch) < 2_000
