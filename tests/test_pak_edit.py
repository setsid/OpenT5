"""Streamed (.pak) images through opent5.edit.Document on a synthetic zone and synthetic
paks: replace, the shared-pak rule, undo, verify-on-save, and the files a save writes.
No game files."""

from __future__ import annotations

import struct

import numpy as np
import pytest

from opent5.container.fastfile import (
    AUTH_MAGIC,
    CHUNKS_OFFSET,
    OFFSET_AUTH_MAGIC,
    OFFSET_ZONE_NAME,
    ZONE_MAGIC,
    ZONE_VERSION,
    split_content,
    write_fastfile,
)
from opent5.container.pak import Pak
from opent5.edit import Document, EditError
from opent5.formats import texture as tx
from opent5.xfile import AssetType, parse
from test_pak import build_pak

INLINE = b"\xff\xff\xff\xff"
ZONE = "mp_testpak"
W = 16  # 16x16 DXT1, five levels: tail 4x4..1x1 (images_low), 8x8 and 16x16 (level pak)


def records(parts: list[tuple[int, int, int, int]]) -> bytes:
    """(levels in the image at this resolution, width, slot, entry) per part, smallest
    first -> the 4 x 12-byte part records (textures.md 2.4, pak.md 3)."""
    out, total, prev = b"", 0, 0
    for mips, w, slot, entry in parts:
        total += tx.face_size(tx.DXT1, w, w, mips - prev)
        prev = mips
        out += struct.pack(">IHHI", (total // 16) << 8 | mips, w, w, slot << 24 | entry)
    return out.ljust(48, b"\0"), total


def streamed_image(name: bytes, parts) -> tuple[int, bytes]:
    h = bytearray(0x70)
    struct.pack_into(">BBBBIHHHB", h, 0, tx.DXT1, 5, 2, 0, 0xAAE4, W, W, 1, 0)
    h[0x18:0x1C] = bytes([3, 2, 3, 1])
    struct.pack_into(">HHH", h, 0x20, 1, 1, 1)
    h[0x26], h[0x27] = 1, 1
    recs, total = records(parts)
    struct.pack_into("<I", h, 0x28, total // (4 * len(parts)))
    struct.pack_into("<I", h, 0x30, total)
    h[0x34:0x64] = recs
    h[0x64] = len(parts)
    h[0x68:0x6C] = INLINE
    return AssetType.IMAGE, bytes(h) + name + b"\0"


def make_content(assets) -> bytes:
    body = struct.pack(">IIII", 0, 0, len(assets), 0xFFFFFFFF)
    body += b"".join(struct.pack(">II", t, 0xFFFFFFFF) for t, _ in assets)
    body += b"".join(d for _, d in assets)
    content = bytes(36) + body
    x = parse(content)
    sizes = list(x.final_cursors)
    sizes[0] = x.temp_high_water + 16
    return struct.pack(">9I", len(content) - 36, 0, *sizes) + body


def header() -> bytes:
    out = bytearray(CHUNKS_OFFSET)
    out[0:8] = ZONE_MAGIC
    struct.pack_into(">I", out, 8, ZONE_VERSION)
    out[OFFSET_AUTH_MAGIC : OFFSET_AUTH_MAGIC + 8] = AUTH_MAGIC
    out[OFFSET_ZONE_NAME : OFFSET_ZONE_NAME + len(ZONE)] = ZONE.encode()
    return bytes(out)


def checker(a, b) -> np.ndarray:
    y, x = np.mgrid[0:W, 0:W]
    out = np.zeros((W, W, 4), np.uint8)
    out[((x // 4) + (y // 4)) % 2 == 0] = a
    out[((x // 4) + (y // 4)) % 2 == 1] = b
    return out


ORIGINAL = checker((255, 0, 0, 255), (255, 255, 255, 255))
NEW = checker((0, 0, 255, 255), (255, 255, 0, 255))


def parts_of(rgba: np.ndarray) -> list[bytes]:
    """The three stored parts of a 16x16 DXT1 image: tail (4..1), 8x8, 16x16."""
    levels = [tx.encode_level(m, tx.DXT1) for m in tx.mip_chain(rgba, 5)]
    return [tx.assemble(tx.DXT1, [levels[2:]]), tx.assemble(tx.DXT1, [levels[1:2]])] + [
        tx.assemble(tx.DXT1, [levels[0:1]])
    ]


@pytest.fixture
def game(tmp_path):
    """A zone with two streamed images beside its level pak and images_low.pak:
    'mixed' (tail in images_low entry 1, levels in the level pak entries 2 and 0) and
    'shared_only' (every part in images_low)."""
    folder = tmp_path / "game"
    folder.mkdir()
    content = make_content(
        [
            streamed_image(b"mixed", [(3, 4, 1, 1), (4, 8, 0, 2), (5, 16, 0, 0)]),
            streamed_image(b"shared_only", [(3, 4, 1, 3), (4, 8, 1, 4), (5, 16, 1, 5)]),
        ]
    )
    (folder / f"{ZONE}.ff").write_bytes(
        write_fastfile(header(), [(p, None) for p in split_content(content, 400)])
    )
    tail, mid, top = parts_of(ORIGINAL)
    (folder / f"{ZONE}.pak").write_bytes(build_pak([top, b"\x11" * 0x900, mid], [0, 2, 1]))
    (folder / "images_low.pak").write_bytes(
        build_pak([b"\x22" * 0x10, tail, b"\x33" * 0x20, tail, mid, top], field08=0)
    )
    return folder


def test_streamed_image_reads_and_is_replaceable(game):
    doc = Document.open(game / f"{ZONE}.ff")
    data = doc.image(0)
    assert data.info["pixels"] == "pak" and data.info["replaceable"]
    assert [(p["pak"], p["shared"]) for p in data.info["parts"]] == [
        ("images_low.pak", True),
        (f"{ZONE}.pak", False),
        (f"{ZONE}.pak", False),
    ]
    assert np.array_equal(data.rgba[..., :3], ORIGINAL[..., :3])


def test_replace_writes_the_level_pak_beside_the_zone(game, tmp_path):
    doc = Document.open(game / f"{ZONE}.ff")
    doc.replace_image(0, NEW)
    assert "images_low.pak entry 1 (4x4)" in doc.changes()[0].detail
    assert np.array_equal(doc.image(0).rgba[..., :3], NEW[..., :3])  # pending edit shown
    out = tmp_path / "out" / f"{ZONE}.ff"
    report = doc.save(out)
    assert report.verified, report.problems
    assert report.identical  # the zone itself does not change
    paks = report.details["paks"]
    assert [p["path"] for p in paks] == [str(out.parent / f"{ZONE}.pak")]
    assert paks[0]["edited_entries"] == [0, 2] and paks[0]["entries_identical"] == 1
    assert report.details["streamed_images_decoded"] == 1
    assert not (out.parent / "images_low.pak").exists()
    tail, mid, top = parts_of(NEW)
    written = Pak.open(out.parent / f"{ZONE}.pak")
    assert written.read(0, len(top)) == top and written.read(2, len(mid)) == mid
    assert written.read(1, 0x900) == b"\x11" * 0x900
    # The source paks are untouched.
    assert Pak.open(game / f"{ZONE}.pak").read(0, len(top)) == parts_of(ORIGINAL)[2]
    back = Document.open(out).image(0)
    assert back.info["source"] == "pak slot 0 entry 0"
    assert np.array_equal(back.rgba[..., :3], NEW[..., :3])


def test_allow_shared_also_writes_images_low(game, tmp_path):
    doc = Document.open(game / f"{ZONE}.ff")
    doc.replace_image(0, NEW, allow_shared=True)
    out = tmp_path / "out" / f"{ZONE}.ff"
    report = doc.save(out)
    assert report.verified, report.problems
    by_name = {p["path"].rsplit("/", 1)[1]: p for p in report.details["paks"]}
    assert by_name["images_low.pak"]["shared"] and by_name["images_low.pak"]["edited_entries"] == [
        1
    ]
    assert "shared" in by_name["images_low.pak"]["note"]
    assert Pak.open(out.parent / "images_low.pak").read(1, 128) == parts_of(NEW)[0]


def test_an_image_only_in_shared_paks_is_refused_by_default(game, tmp_path):
    doc = Document.open(game / f"{ZONE}.ff")
    with pytest.raises(EditError, match="shared pak.*allow_shared=True"):
        doc.replace_image(1, NEW)
    assert not doc.dirty
    doc.replace_image(1, NEW, allow_shared=True)
    report = doc.save(tmp_path / "out" / f"{ZONE}.ff")
    assert report.verified, report.problems
    assert [p["edited_entries"] for p in report.details["paks"]] == [[3, 4, 5]]


def test_undo_drops_the_pak_edit(game, tmp_path):
    doc = Document.open(game / f"{ZONE}.ff")
    doc.replace_image(0, NEW)
    doc.undo()
    assert not doc.pak_edits()
    report = doc.save(tmp_path / "out" / f"{ZONE}.ff")
    assert "paks" not in report.details
    assert not (tmp_path / "out" / f"{ZONE}.pak").exists()
    doc.redo()
    assert sorted(doc.pak_edits()) == [(0, 0), (0, 2)]


def test_dds_input_and_size_checks(game):
    doc = Document.open(game / f"{ZONE}.ff")
    with pytest.raises(EditError, match="expected 16x16 pixels, found 32x32"):
        doc.replace_image(0, np.zeros((32, 32, 4), np.uint8))
    levels = [tx.encode_level(m, tx.DXT1) for m in tx.mip_chain(NEW, 5)]
    doc.replace_image(0, tx.write_dds(tx.Dds(tx.DXT1, W, W, 5, [levels])))
    assert doc.pak_edits()[(0, 0)] == parts_of(NEW)[2]


def test_save_refuses_to_write_a_pak_over_its_source(game):
    doc = Document.open(game / f"{ZONE}.ff")
    doc.replace_image(0, NEW, allow_shared=True)
    # images_low.pak would be written over the source one in the same folder.
    with pytest.raises(EditError, match="the source pak itself"):
        doc.save(game / f"{ZONE}_b.ff")
    assert not (game / f"{ZONE}_b.ff").exists()
    doc.undo()
    doc.replace_image(0, NEW)
    # Level pak only: allowed beside the source (named after the new zone file).
    report = doc.save(game / f"{ZONE}_b.ff")
    assert report.verified, report.problems
    assert report.details["paks"][0]["path"].endswith(f"{ZONE}_b.pak")


# -- another size (pak.md 9.1) ---------------------------------------------------------------


def big_new(side: int) -> np.ndarray:
    y, x = np.mgrid[0:side, 0:side]
    out = np.zeros((side, side, 4), np.uint8)
    out[..., 3] = 255
    out[((x // 4) + (y // 4)) % 2 == 0] = (0, 255, 0, 255)
    return out


def test_resize_needs_the_flag(game):
    doc = Document.open(game / f"{ZONE}.ff")
    with pytest.raises(EditError, match="expected 16x16 pixels, found 32x32.*resize=True"):
        doc.replace_image(0, big_new(32))
    assert not doc.dirty


def test_resize_doubles_and_appends_a_level_pak_entry(game, tmp_path):
    doc = Document.open(game / f"{ZONE}.ff")
    doc.replace_image(0, big_new(32), resize=True)
    change = doc.changes()[-1]
    assert "16x16 (5 mips) -> 32x32 (6 mips), 4 parts" in change.detail
    assert "level pak entries added: 3" in change.detail
    assert "images_low.pak entry 1 (4x4)" in change.detail  # the tail is kept
    info = doc.image(0).info
    assert (info["width"], info["height"], info["mips"]) == (32, 32, 6)
    assert [(p["slot"], p["entry"], p["width"], p["mips"]) for p in info["parts"]] == [
        (1, 1, 4, 3),
        (0, 2, 8, 4),
        (0, 0, 16, 5),
        (0, 3, 32, 6),
    ]
    assert np.array_equal(doc.image(0).rgba[..., :3], big_new(32)[..., :3])
    out = tmp_path / "out" / f"{ZONE}.ff"
    report = doc.save(out)
    assert report.verified, report.problems
    assert not report.identical  # the GfxImage header changed
    (pak,) = report.details["paks"]
    assert pak["edited_entries"] == [0, 2, 3] and pak["appended_entries"] == [3]
    assert pak["entries"] == 4 and pak["entries_identical"] == 1
    assert report.details["streamed_images_decoded"] == 1
    back = Document.open(out)
    data = back.image(0)
    assert (data.info["width"], data.info["mips"]) == (32, 6)
    assert np.array_equal(data.rgba[..., :3], big_new(32)[..., :3])
    hdr = bytes(back.xfile.assets[0].data["header"])
    total = struct.unpack_from("<I", hdr, 0x30)[0]
    assert total == sum(
        tx.face_size(tx.DXT1, w, w, n) for w, n in ((4, 3), (8, 1), (16, 1), (32, 1))
    )
    assert struct.unpack_from("<I", hdr, 0x28)[0] == total // 16
    # the other image and every other zone byte are as they were
    assert back.image(1).info["width"] == 16
    with Pak.open(out.parent / f"{ZONE}.pak") as written:
        assert written.read(1, 0x900) == b"\x11" * 0x900


def test_resize_down_leaves_an_unused_entry_and_undo_restores(game, tmp_path):
    doc = Document.open(game / f"{ZONE}.ff")
    before = bytes(doc.xfile.assets[0].data["header"])
    doc.replace_image(0, big_new(8), resize=True)
    assert "no longer read (left as they were): 0" in doc.changes()[-1].detail
    report = doc.save(tmp_path / "a" / f"{ZONE}.ff")
    assert report.verified, report.problems
    assert Document.open(tmp_path / "a" / f"{ZONE}.ff").image(0).info["parts"][-1]["width"] == 8
    doc.undo()
    assert bytes(doc.xfile.assets[0].data["header"]) == before and not doc.pak_edits()
    doc.redo()
    # a second resize reuses the entry the image reads (2) and appends two more
    doc.replace_image(0, big_new(32), resize=True)
    assert [(p["entry"], p["width"]) for p in doc.image(0).info["parts"]] == [
        (1, 4),
        (2, 8),
        (3, 16),
        (4, 32),
    ]
    report = doc.save(tmp_path / "b" / f"{ZONE}.ff")
    assert report.verified, report.problems


def test_resize_refusals(game):
    doc = Document.open(game / f"{ZONE}.ff")
    with pytest.raises(EditError, match="power-of-two"):
        doc.replace_image(0, np.zeros((24, 24, 4), np.uint8), resize=True)
    with pytest.raises(EditError, match="4 levels above the 4x4 mip tail.*8x8 or 16x16 or 32x32"):
        doc.replace_image(0, big_new(64), resize=True)
    with pytest.raises(EditError, match="mip tail.*found 4x4"):
        doc.replace_image(0, big_new(4), resize=True)
    with pytest.raises(EditError, match="shared pak; resizing changes its size"):
        doc.replace_image(1, big_new(32), resize=True, allow_shared=True)
    assert not doc.dirty


# -- the command line ------------------------------------------------------------------------


def run_cli(capsys, *argv) -> tuple[int, str]:
    from opent5 import cli

    code = cli.main([str(a) for a in argv])
    out, err = capsys.readouterr()
    return code, out + err


def test_cli_allow_shared_writes_images_low_with_the_warning(game, tmp_path, capsys):
    png = tmp_path / "new.png"
    png.write_bytes(tx.write_png(NEW))
    out = tmp_path / "out" / f"{ZONE}.ff"
    code, text = run_cli(capsys, "replace", game / f"{ZONE}.ff", "image:mixed", png, "-o", out)
    assert code == 0, text
    assert "kept as they were (shared pak" in text and not (out.parent / "images_low.pak").exists()
    out2 = tmp_path / "out2" / f"{ZONE}.ff"
    code, text = run_cli(
        capsys, "replace", game / f"{ZONE}.ff", "image:mixed", png, "-o", out2, "--allow-shared"
    )
    assert code == 0, text
    assert "warning: images_low.pak is shared" in text
    assert Pak.open(out2.parent / "images_low.pak").read(1, 128) == parts_of(NEW)[0]
    code, text = run_cli(
        capsys, "replace", game / f"{ZONE}.ff", "image:shared_only", png, "-o", out2, "--json"
    )
    assert code == 1 and "allow_shared=True" in text


def test_cli_resize(game, tmp_path, capsys):
    png = tmp_path / "big.png"
    png.write_bytes(tx.write_png(big_new(32)))
    out = tmp_path / "out" / f"{ZONE}.ff"
    code, text = run_cli(capsys, "replace", game / f"{ZONE}.ff", "image:mixed", png, "-o", out)
    assert code == 1 and "--resize" in text
    code, text = run_cli(
        capsys, "replace", game / f"{ZONE}.ff", "image:mixed", png, "-o", out, "--resize"
    )
    assert code == 0, text
    assert "16x16 (5 mips) -> 32x32 (6 mips)" in text and "1 added" in text
    assert Document.open(out).image(0).info["width"] == 32
