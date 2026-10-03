"""opent5.edit.Document on a synthetic zone: every reader and editor of docs/edit-api.md,
saving, verification, undo and redo, and the rules that protect the source and the game
folders. No game files needed."""

from __future__ import annotations

import struct
import zlib

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
from opent5.edit import Document, EditError
from opent5.edit import content as ct
from opent5.formats import texture as tx
from opent5.xfile import AssetType, parse
from opent5.xfile.handlers.stringtable import string_hash

INLINE = b"\xff\xff\xff\xff"
VIRTUAL = 4


def ptr(block: int, offset: int) -> bytes:
    return struct.pack(">I", ((block << 29) | offset) + 1)


def make_content(assets: list[tuple[int, bytes]], tail: bytes = b"") -> bytes:
    body = struct.pack(">IIII", 0, 0, len(assets), 0xFFFFFFFF)
    body += b"".join(struct.pack(">II", t, 0xFFFFFFFF) for t, _ in assets)
    body += b"".join(data for _, data in assets) + tail
    content = bytes(36) + body
    x = parse(content)
    sizes = list(x.final_cursors)
    sizes[0] = x.temp_high_water + 16
    return struct.pack(">9I", len(content) - 36, 0, *sizes) + body


def localize(value: bytes, name: bytes, value_ptr: bytes = INLINE):
    data = value_ptr + INLINE + (value + b"\0" if value_ptr == INLINE else b"")
    return AssetType.LOCALIZE, data + name + b"\0"


def rawfile(name: bytes, buffer: bytes):
    """``buffer`` is the stored buffer including its final byte."""
    head = INLINE + struct.pack(">i", len(buffer) - 1) + INLINE
    return AssetType.RAWFILE, head + name + b"\0" + buffer


def script(name: bytes, text: bytes):
    buffer, _ = ct.script_buffer(text + b"\0")
    return rawfile(name, buffer)


def stringtable(name: bytes, rows: list[list[bytes]], shared: dict[int, bytes] | None = None):
    """Cells inline, except those in ``shared`` (cell number -> offset pointer)."""
    shared = shared or {}
    texts = [c for row in rows for c in row]
    columns = len(rows[0])
    cells = b"".join(
        shared.get(i, INLINE) + struct.pack(">I", string_hash(t.decode("latin-1")))
        for i, t in enumerate(texts)
    )
    strings = b"".join(t + b"\0" for i, t in enumerate(texts) if i not in shared)
    hashes = [string_hash(t.decode("latin-1")) for t in texts]
    signed = [h - (1 << 32) if h & 0x80000000 else h for h in hashes]
    order = sorted(range(len(texts)), key=lambda i: signed[i])
    index = b"".join(struct.pack(">h", i) for i in order)
    head = INLINE + struct.pack(">ii", columns, len(rows)) + INLINE + INLINE
    return AssetType.STRINGTABLE, head + name + b"\0" + cells + strings + index


def image_header(deferred: int, size: int = 128) -> bytes:
    h = bytearray(0x70)
    struct.pack_into(">BBBBIHHHB", h, 0, tx.DXT1, 1, 2, 0, 0xAAE4, 8, 8, 1, 0)
    h[0x1B] = deferred
    struct.pack_into(">I", h, 0x1C, size)
    h[0x2C:0x30] = INLINE
    h[0x68:0x6C] = INLINE
    return bytes(h)


def solid(colour) -> np.ndarray:
    out = np.zeros((8, 8, 4), np.uint8)
    out[...] = colour
    return out


PIXELS_A = tx.encode(solid((255, 0, 0, 255)), tx.DXT1, 1).ljust(128, b"\0")
PIXELS_B = tx.encode(solid((0, 0, 255, 255)), tx.DXT1, 1).ljust(128, b"\0")


def image(name: bytes, deferred: bool):
    data = image_header(int(deferred)) + name + b"\0"
    if not deferred:
        data += PIXELS_A
    return AssetType.IMAGE, data


def mapents(name: bytes, text: bytes):
    head = INLINE + INLINE + struct.pack(">I", len(text) + 1)
    return AssetType.MAP_ENTS, head + name + b"\0" + text + b"\0"


ENTS = b'{\n"classname" "worldspawn"\n}\n{\n"classname" "script_model"\n"origin" "1 2 3"\n}\n'
ROWS = [[b"alpha", b"1"], [b"beta", b"2"], [b"alpha", b"3"]]


def synthetic_content() -> bytes:
    """Ten assets. Asset 1's value points at asset 0's string ("Hello", shared); cell 4 of
    the table points at cell 0's string ("alpha", shared); asset 9 points at the plain
    rawfile's name. Built twice: the first build gives the VIRTUAL positions."""

    def build(hello: bytes | None, alpha: bytes | None, cfg_name: bytes | None) -> bytes:
        assets = [
            localize(b"Hello", b"GREETING"),
            localize(b"", b"GREETING_COPY", hello or INLINE)
            if hello
            else localize(b"Hello", b"GREETING_COPY"),
            rawfile(b"test.cfg", b"set a 1\n\0"),
            script(b"maps/test.gsc", b"main()\n{\n\twait 1;\n}\n"),
            stringtable(b"mp/t.csv", ROWS, {4: alpha} if alpha else None),
            image(b"inline_img", False),
            image(b"deferred_img", True),
            mapents(b"maps/test.d3dbsp", ENTS),
            localize(b"World", b"OTHER"),
            localize(b"", b"CFG_NAME", cfg_name or INLINE)
            if cfg_name
            else localize(b"x", b"CFG_NAME"),
        ]
        return make_content(assets, PIXELS_A)

    # Each target string comes before its pointer and before every later replacement, so
    # resolving the pointers one at a time, in order, gives final positions.
    found: dict[bytes, bytes] = {}
    for text in (b"Hello", b"alpha", b"test.cfg"):
        content = build(found.get(b"Hello"), found.get(b"alpha"), found.get(b"test.cfg"))
        rows = parse(content).log.table()
        for r in rows[(rows[:, 0] == 1) & (rows[:, 3] == VIRTUAL)]:
            if content[int(r[1]) : int(r[1]) + int(r[2]) - 1] == text:
                found[text] = ptr(VIRTUAL, int(r[4]))
                break
    return build(found[b"Hello"], found[b"alpha"], found[b"test.cfg"])


def header_for(name: bytes = b"mp_testedit") -> bytes:
    out = bytearray(CHUNKS_OFFSET)
    out[0:8] = ZONE_MAGIC
    struct.pack_into(">I", out, 8, ZONE_VERSION)
    out[OFFSET_AUTH_MAGIC : OFFSET_AUTH_MAGIC + 8] = AUTH_MAGIC
    out[OFFSET_ZONE_NAME : OFFSET_ZONE_NAME + len(name)] = name
    out[OFFSET_SIGNATURE : OFFSET_SIGNATURE + SIGNATURE_SIZE] = bytes(
        (i * 13) & 0xFF for i in range(SIGNATURE_SIZE)
    )
    return bytes(out)


def synthetic_ff() -> bytes:
    content = synthetic_content()
    return write_fastfile(header_for(), [(p, None) for p in split_content(content, 400)])


@pytest.fixture
def ff(tmp_path):
    path = tmp_path / "in" / "mp_testedit.ff"
    path.parent.mkdir()
    path.write_bytes(synthetic_ff())
    return path


@pytest.fixture
def doc(ff):
    return Document.open(ff)


def test_the_synthetic_zone_parses_exactly():
    assert parse(synthetic_content()).problems() == []


# -- reading ---------------------------------------------------------------------------------


def test_lists_assets_and_types(doc):
    assert doc.zone_name == "mp_testedit"
    assert not doc.signed  # a made-up signature field does not decode under the console key
    assert [a.type_name for a in doc.assets][:4] == ["localize", "localize", "rawfile", "rawfile"]
    assert doc.type_counts() == {
        "localize": 4,
        "rawfile": 2,
        "stringtable": 1,
        "image": 2,
        "map_ents": 1,
    }
    assert doc.find("mp/t.csv").index == 4
    assert doc.find("test.cfg", "rawfile").index == 2
    assert doc.find("nothing") is None
    assert "table" in doc.asset(4).editable and "image" in doc.asset(5).editable
    assert doc.asset(2).size == doc.xfile.assets[2].size


def test_reads_every_tier1_kind(doc):
    assert doc.localize(0) == ("GREETING", "Hello")
    assert doc.localize(1) == ("GREETING_COPY", "Hello")
    assert doc.text(2) == "set a 1\n"
    assert doc.text(3) == "main()\n{\n\twait 1;\n}\n"
    assert doc.table(4) == [[c.decode() for c in r] for r in ROWS]
    assert doc.text(7) == ENTS.decode()
    img = doc.image(5)
    assert img.info["pixels"] == "inline" and img.info["replaceable"]
    assert img.rgba.shape == (8, 8, 4) and tuple(img.rgba[0, 0]) == (255, 0, 0, 255)
    assert doc.image(6).info["pixels"] == "deferred"
    assert doc.raw(2) == doc.content[doc.xfile.assets[2].file_start : doc.xfile.assets[2].file_end]
    assert doc.fields(4)["header"]["columnCount"] == 2


def test_search_finds_names_and_contents(doc):
    kinds = {(h.index, h.kind) for h in doc.search("alpha")}
    assert (4, "cell") in kinds
    assert any(h.kind == "text" and h.line == 3 for h in doc.search("wait"))
    assert any(h.kind == "value" for h in doc.search("hello"))
    assert any(h.kind == "name" for h in doc.search("GREETING", contents=False))


def test_wrong_kind_is_an_error(doc):
    with pytest.raises(EditError, match="expected stringtable"):
        doc.table(0)
    with pytest.raises(EditError, match="expected an index"):
        doc.text(99)


# -- editing and saving ----------------------------------------------------------------------


def save_and_reopen(doc, path):
    report = doc.save(path)
    assert report.problems == []
    assert report.verified
    return report, Document.open(path)


@pytest.mark.parametrize("text", ["", "x", "set a 1\nset b 2\n" * 40])
def test_plain_rawfile_any_length(doc, tmp_path, text):
    doc.set_text(2, text)
    report, back = save_and_reopen(doc, tmp_path / "out.ff")
    assert back.text(2) == text
    assert back.localize(9) == ("CFG_NAME", "test.cfg")  # a pointer to the name follows it
    assert report.assets_changed == 1
    assert report.signature_note is None


def test_script_stays_compressed(doc, tmp_path):
    text = "main()\n{\n" + "\tthread loop();\n" * 50 + "}\n"
    doc.set_text(3, text)
    _, back = save_and_reopen(doc, tmp_path / "out.ff")
    node = back.xfile.assets[3].data
    assert ct.rawfile_compressed(node)
    assert back.text(3) == text
    size, packed = struct.unpack_from(">II", node["buffer"], 0)
    assert zlib.decompress(bytes(node["buffer"][8 : 8 + packed])) == text.encode() + b"\0"
    assert size == len(text) + 1


def test_unchanged_script_recompresses_identically(doc):
    node = doc.xfile.assets[3].data
    header, buffer = ct.rawfile_bytes(node, doc.text(3))
    assert (header, buffer) == (bytes(node["header"]), bytes(node["buffer"]))


def test_shared_localize_value_is_copied_not_changed(doc, tmp_path):
    doc.set_localize(0, "Hello there, a much longer greeting")
    change = doc.changes()[0]
    assert "own copy" in change.detail
    _, back = save_and_reopen(doc, tmp_path / "out.ff")
    assert back.localize(0)[1] == "Hello there, a much longer greeting"
    assert back.localize(1)[1] == "Hello"


def test_editing_the_copy_leaves_the_original(doc, tmp_path):
    doc.set_localize(1, "Bye")
    _, back = save_and_reopen(doc, tmp_path / "out.ff")
    assert back.localize(0)[1] == "Hello"
    assert back.localize(1)[1] == "Bye"


def check_index(table):
    columns, rows = ct.table_shape(table)
    hashes = [ct.signed(ct.cell_hash(e)) for e in table["cells"]]
    for e in table["cells"]:
        assert ct.cell_hash(e) == string_hash(e["string"])
    index = [v for (v,) in struct.iter_unpack(">h", table["cell_index"])]
    assert sorted(index) == list(range(columns * rows))
    assert [hashes[i] for i in index] == sorted(hashes)


def test_set_cell_rehashes_and_resorts(doc, tmp_path):
    doc.set_cell(4, 1, 1, "a longer second value")
    _, back = save_and_reopen(doc, tmp_path / "out.ff")
    assert back.table(4)[1] == ["beta", "a longer second value"]
    check_index(back.xfile.assets[4].data)


def test_set_cell_of_a_shared_string_keeps_the_sharer(doc, tmp_path):
    doc.set_cell(4, 0, 0, "omega")
    _, back = save_and_reopen(doc, tmp_path / "out.ff")
    assert back.table(4) == [["omega", "1"], ["beta", "2"], ["alpha", "3"]]


@pytest.mark.parametrize("at", [0, 1, 3])
def test_add_row(doc, tmp_path, at):
    assert doc.add_row(4, ["new", "row"], at) == at
    _, back = save_and_reopen(doc, tmp_path / "out.ff")
    rows = [[c.decode() for c in r] for r in ROWS]
    rows.insert(at, ["new", "row"])
    assert back.table(4) == rows
    check_index(back.xfile.assets[4].data)
    assert back.image(5).rgba is not None  # PHYSICAL data after the table still reads


@pytest.mark.parametrize("row", [0, 1, 2])
def test_remove_row(doc, tmp_path, row):
    removed = doc.remove_row(4, row)
    assert removed == [c.decode() for c in ROWS[row]]
    _, back = save_and_reopen(doc, tmp_path / "out.ff")
    rows = [[c.decode() for c in r] for r in ROWS]
    del rows[row]
    assert back.table(4) == rows
    check_index(back.xfile.assets[4].data)


def test_remove_every_row_then_add_one(doc, tmp_path):
    for _ in range(3):
        doc.remove_row(4, 0)
    doc.add_row(4, ["only", "row"])
    _, back = save_and_reopen(doc, tmp_path / "out.ff")
    assert back.table(4) == [["only", "row"]]


def test_map_ents_text_any_length(doc, tmp_path):
    text = doc.text(7).replace('"1 2 3"', '"1000.5 -2000.25 3000"')
    doc.set_text(7, text)
    _, back = save_and_reopen(doc, tmp_path / "out.ff")
    assert back.text(7) == text
    node = back.xfile.assets[7].data
    assert struct.unpack_from(">I", node["header"], 8)[0] == len(text) + 1


@pytest.mark.parametrize("index", [5, 6])
def test_replace_image_from_rgba(doc, tmp_path, index):
    doc.replace_image(index, solid((0, 0, 255, 255)))
    _, back = save_and_reopen(doc, tmp_path / "out.ff")
    assert tuple(back.image(index).rgba[3, 3]) == (0, 0, 255, 255)
    other = 6 if index == 5 else 5
    assert tuple(back.image(other).rgba[3, 3]) == (255, 0, 0, 255)


def test_replace_image_from_dds(doc, tmp_path):
    dds = tx.write_dds(
        tx.Dds(tx.DXT1, 8, 8, 1, [[tx.encode_dxt(solid((0, 255, 0, 255)), tx.DXT1)]])
    )
    doc.replace_image(6, dds)
    _, back = save_and_reopen(doc, tmp_path / "out.ff")
    assert tuple(back.image(6).rgba[0, 0]) == (0, 255, 0, 255)


def test_replace_image_refuses_other_sizes_and_formats(doc):
    with pytest.raises(EditError, match=r"shape \(8, 8, 4\)"):
        doc.replace_image(5, np.zeros((16, 16, 4), np.uint8))
    dds = tx.write_dds(tx.Dds(tx.DXT45, 8, 8, 1, [[bytes(64)]]))
    with pytest.raises(EditError, match="DXT1 DDS"):
        doc.replace_image(5, dds)
    assert not doc.dirty


def test_set_field(doc, tmp_path):
    doc.set_field(5, "semantic", 2)
    _, back = save_and_reopen(doc, tmp_path / "out.ff")
    assert back.fields(5)["header"]["semantic"] == 2
    with pytest.raises(EditError, match="read-only"):
        doc.set_field(4, "columnCount", 3)


def test_several_edits_at_once(doc, tmp_path):
    doc.set_localize(8, "W" * 300)
    doc.set_text(2, "")
    doc.remove_row(4, 0)
    doc.set_cell(4, 0, 0, "first")
    doc.replace_image(6, solid((0, 255, 255, 255)))
    doc.set_text(3, "main() {}\n")
    report, back = save_and_reopen(doc, tmp_path / "out.ff")
    assert back.localize(8)[1] == "W" * 300
    assert back.table(4) == [["first", "2"], ["alpha", "3"]]
    assert back.text(2) == "" and back.text(3) == "main() {}\n"
    assert back.localize(9)[1] == "test.cfg"
    assert report.details["edited_read_back"] == 5


def test_unedited_save_is_byte_identical(doc, ff, tmp_path):
    report = doc.save(tmp_path / "same.ff")
    assert report.identical and report.verified
    assert (tmp_path / "same.ff").read_bytes() == ff.read_bytes()


# -- history ---------------------------------------------------------------------------------


def test_undo_redo_dirty(doc, tmp_path):
    assert not doc.dirty and not doc.can_undo
    doc.set_localize(8, "one")
    doc.add_row(4, ["a", "b"])
    assert doc.dirty and [c.kind for c in doc.changes()] == ["localize", "row_added"]
    doc.undo()
    assert len(doc.table(4)) == 3 and doc.can_redo
    doc.undo()
    assert doc.localize(8)[1] == "World" and not doc.dirty
    doc.redo()
    doc.redo()
    assert doc.localize(8)[1] == "one" and len(doc.table(4)) == 4
    doc.save(tmp_path / "out.ff")
    assert not doc.dirty
    doc.undo()
    assert doc.dirty


def test_original_text_survives_edits(doc):
    doc.set_text(2, "one")
    doc.set_text(2, "two")
    assert doc.original_text(2) == "set a 1\n" and doc.text(2) == "two"


def test_undo_restores_shared_strings(doc, tmp_path):
    doc.set_localize(0, "changed")
    doc.undo()
    report = doc.save(tmp_path / "out.ff")
    assert report.identical


def test_same_value_is_not_an_edit(doc):
    doc.set_localize(0, "Hello")
    doc.set_text(2, "set a 1\n")
    doc.set_cell(4, 0, 1, "1")
    assert not doc.can_undo


# -- where saving may write ------------------------------------------------------------------


def test_refuses_to_overwrite_the_source(doc, ff):
    with pytest.raises(EditError, match="the source zone itself"):
        doc.save(ff)


def test_refuses_the_game_folders(doc, tmp_path, monkeypatch):
    game = tmp_path / "game"
    game.mkdir()
    monkeypatch.setenv("OPENT5_ZONES", str(game))
    with pytest.raises(EditError, match="outside the game folders"):
        doc.save(game / "mod" / "x.ff")
    assert not (game / "mod").exists()


def test_bytes_open(ff):
    doc = Document.open(ff.read_bytes(), name="memory.ff")
    assert doc.localize(0)[1] == "Hello"
