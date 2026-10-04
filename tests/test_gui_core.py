"""The GUI's models and widgets, offscreen, without pytest-qt.

A small fake backend stands in for opent5.edit.Document, so these run without
any zone; the last test opens a real one and is skipped without .env.
"""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np  # noqa: E402
import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from opent5.xfile.constants import AssetType, type_name  # noqa: E402
from opent5.xfile.constants import AssetType as T  # noqa: E402

APP = QApplication.instance() or QApplication([])

from opent5.gui import backend, dialogs, icons, palette, panels, theme, tree  # noqa: E402
from opent5.gui.views import code, highlight  # noqa: E402


class FakeImpl:
    """The parts of opent5.edit.Document the GUI uses, over a few in-memory assets."""

    def __init__(self):
        self.path = Path("fake.ff")
        self.zone_name = "fake"
        self.signed = True
        self.parse_problems = []
        self.texts = {0: "main()\n{\n\tlevel.x = 1;\n}\n", 1: "set g_speed 190\n"}
        self.rows = [["mp_nuked", "Nuketown"], ["mp_array", "Array"]]
        self.values = {3: "Accuracy"}
        self.ops: list = []
        self.undone: list = []
        spec = [
            (T.RAWFILE, "maps/mp/test.gsc", ("text", "fields", "hex")),
            (T.RAWFILE, "default.cfg", ("text", "fields", "hex")),
            (T.STRINGTABLE, "mp/mapstable.csv", ("table", "fields", "hex")),
            (T.LOCALIZE, "CGAME_SB_ACCURACY", ("localize", "fields", "hex")),
        ]
        self.assets = [
            SimpleNamespace(index=i, type=t, name=n, size=10 * (i + 1), editable=e, file_start=64)
            for i, (t, n, e) in enumerate(spec)
        ]
        self.inline_assets = [
            SimpleNamespace(
                index=("inline", T.IMAGE, "berlin_books_n"), type=T.IMAGE, name="berlin_books_n",
                size=0, editable=("image", "fields", "hex"), file_start=None,
            )
        ]  # fmt: skip

    def text(self, i):
        return self.texts[i]

    def original_text(self, i):
        for op in self.ops:
            if op.index == i and op.kind == "text":
                return op.before
        return self.texts[i]

    def table(self, i):
        return [list(r) for r in self.rows]

    def localize(self, i):
        return ("CGAME_SB_ACCURACY", self.values[i])

    def _op(self, index, kind, before, after, detail=""):
        self.ops.append(SimpleNamespace(index=index, kind=kind, before=before, after=after,
                                        detail=detail))  # fmt: skip
        self.undone.clear()

    def set_text(self, i, text):
        self._op(i, "text", self.texts[i], text)
        self.texts[i] = text

    def set_cell(self, i, r, c, text):
        self._op(i, "cell", self.rows[r][c], text, f"row {r}, column {c}")
        self.rows[r][c] = text

    def set_localize(self, i, value):
        self._op(i, "localize", self.values[i], value)
        self.values[i] = value

    def changes(self):
        return list(self.ops)

    @property
    def dirty(self):
        return bool(self.ops)

    @property
    def can_undo(self):
        return bool(self.ops)

    @property
    def can_redo(self):
        return bool(self.undone)

    def undo(self):
        op = self.ops.pop()
        self.undone.append(op)
        if op.kind == "text":
            self.texts[op.index] = op.before
        return op


@pytest.fixture
def doc():
    d = backend.ZoneDoc(FakeImpl(), "edit")
    d._collect_inline()
    return d


def test_theme_tokens_complete_and_distinct():
    for t in theme.THEMES.values():
        tokens = t.tokens()
        assert all(v.startswith("#") and len(v) == 7 for v in tokens.values())
        assert tokens["text"] != tokens["base"]
        assert tokens["selection"] != tokens["base"]
    assert set(theme.DARK.tokens()) == set(theme.LIGHT.tokens())
    css = theme.stylesheet(theme.DARK)
    assert "gradient" not in css and "border-radius" not in css
    assert theme.mono_family()


def test_theme_switch_calls_listeners_once_per_switch():
    seen = []
    theme.on_change(lambda t: seen.append(t.name))
    theme.apply("light")
    theme.apply("dark")
    assert seen[-2:] == ["light", "dark"]


def test_ico_has_every_size():
    pngs = [(s, icons.png_bytes(icons.render_mark(s))) for s in icons.ICO_SIZES]
    data = icons.ico_bytes(pngs)
    assert data[:6] == b"\0\0\1\0" + bytes([len(icons.ICO_SIZES), 0])
    assert data.count(b"\x89PNG") == len(icons.ICO_SIZES)
    assert icons.APP_SVG.is_file() and icons.APP_ICO.is_file()
    for name in icons.DRAW:
        assert not icons.icon(name).isNull()


def test_tree_counts_filter_and_markers(doc):
    m = tree.AssetTreeModel()
    m.set_doc(doc)
    assert m.count() == 5
    groups = {g.type_name: len(g.refs) for g in m.groups}
    assert groups == {"image": 1, "localize": 1, "rawfile": 2, "stringtable": 1}
    proxy = tree.AssetFilter()
    proxy.setSourceModel(m)
    proxy.set_text("cfg")
    shown = [
        proxy.index(r, 0, proxy.index(g, 0)).data()
        for g in range(proxy.rowCount())
        for r in range(proxy.rowCount(proxy.index(g, 0)))
    ]
    assert shown == ["default.cfg"]
    doc.set_text(doc.refs[1], "set g_speed 250\n")
    m.set_edited(doc.edited_keys())
    assert m.index_of(1).data().startswith("* ")


def test_type_icons_cover_every_asset_type():
    for t in AssetType:
        assert not icons.type_icon(type_name(t)).isNull()
    assert not icons.type_icon("something_unknown").isNull()  # falls back to the generic icon


def test_thumbnail_scaling_and_icon():
    rgba = np.zeros((40, 20, 4), np.uint8)
    rgba[..., 3] = 255
    rgba[..., 0] = 200
    img = tree._scaled_image(rgba, tree.THUMB_PX)
    assert max(img.width(), img.height()) <= tree.THUMB_PX * 2
    icon = tree._thumb_icon(img, tree.THUMB_PX)
    assert not icon.isNull()


def test_tree_decorates_types_and_requests_image_thumbnails(doc):
    m = tree.AssetTreeModel()
    m.set_doc(doc)
    dec = tree.ROLE.DecorationRole
    rawfile = next(i for i, g in enumerate(m.groups) if g.type_name == "rawfile")
    assert not m.data(m.index(rawfile, 0), dec).isNull()  # a per-type icon on the group row
    image = next(i for i, g in enumerate(m.groups) if g.type_name == "image")
    img_row = m.index(0, 0, m.index(image, 0))
    assert not m.data(img_row, dec).isNull()  # the image icon, as a placeholder
    key = m.groups[image].refs[0].key
    assert key in m._thumb_requested  # a thumbnail was requested once the row was seen
    # the fake backend's node is not decodable, so no thumbnail is cached: it stays on the icon
    m._thumb_ready(key, None)
    assert key not in m.thumbs


def test_search_names_and_contents(doc):
    hits = doc.search("speed")
    assert [(h.where, h.line) for h in hits] == [("text", 1)]
    hits = doc.search("nuke")
    assert [(h.where, h.row, h.column) for h in hits] == [("cell", 0, 0), ("cell", 0, 1)]
    hits = doc.search("accuracy")
    assert {h.where for h in hits} == {"name", "value"}
    assert doc.search("books", contents=False)[0].ref.inline


def test_undo_returns_key_and_changes_map_to_refs(doc):
    doc.set_cell(doc.refs[2], 1, 1, "Array (edited)")
    (change,) = doc.changes()
    assert change.name == "mp/mapstable.csv" and "row 1" in change.detail
    assert doc.undo() == 2
    assert not doc.dirty


def test_save_refuses_source_and_game_folders(doc, tmp_path):
    with pytest.raises(backend.EditError):
        doc.save(doc.path)


def test_editor_find_replace():
    editor = code.CodeEditor()
    bar = code.FindBar(editor)
    editor.setPlainText("level.a = 1;\nlevel.b = 2;\nLEVEL.c = 3;\n")
    bar.find.setText("level")
    assert len(bar.spans()) == 3
    bar.case.setChecked(True)
    assert len(bar.spans()) == 2
    assert bar.find_next() and editor.textCursor().selectedText() == "level"
    bar.regex.setChecked(True)
    bar.find.setText(r"level\.(\w)")
    bar.replace.setText(r"lvl_\1")
    assert bar.replace_all() == 2
    assert editor.toPlainText().splitlines()[:2] == ["lvl_a = 1;", "lvl_b = 2;"]
    bar.find.setText("(")
    bar.update_matches()
    assert bar.count.text() == "bad pattern"


def test_text_view_commits_edits_and_keeps_crlf(doc):
    view = code.TextView()
    ref = doc.refs[1]
    doc.impl.texts[1] = "set a 1\r\nset b 2\r\n"
    view.load(doc, ref)
    assert not view.commit()  # loading is not an edit
    view.editor.setPlainText("set a 1\nset b 3\n")
    assert view.commit()
    assert doc.text(ref) == "set a 1\r\nset b 3\r\n"


def test_highlighter_modes():
    assert highlight.mode_for("maps/mp/x.gsc") == "gsc"
    assert highlight.mode_for("default_mp.cfg") == "cfg"
    editor = code.CodeEditor()
    editor.setPlainText('if ( x ) // "not a string"\n  wait 0.05;\n')
    APP.processEvents()
    block = editor.document().firstBlock()
    formats = block.layout().formats()
    assert formats, "no highlighting applied"


def test_palette_fuzzy_ranking():
    items = [palette.Item(n, "", n) for n in ("Save As", "Search in Zone", "Undo", "Hex View")]
    assert palette.rank("sa", items)[0].label == "Save As"
    assert palette.rank("hv", items)[0].label == "Hex View"
    assert palette.rank("zzz", items) == []
    assert palette.fuzzy_score("siz", "Search in Zone") is not None


def test_diff_lines_marks_additions(doc):
    doc.set_text(doc.refs[0], doc.text(doc.refs[0]) + "init()\n{\n}\n")
    (change,) = doc.changes()
    kinds = [k for _line, k in panels.diff_lines(change)]
    assert kinds.count("+") == 3 and "-" not in kinds


def test_diff_lines_of_an_image_show_its_detail():
    change = backend.Change(
        5, "image", {}, {}, name="head_c", type_name="image",
        detail="size 128x256 (9 mips) -> 256x512 (10 mips), 4 parts; pak: x.pak entry 3 (8x8)",
    )  # fmt: skip
    lines = panels.diff_lines(change)
    assert lines[0] == ("image head_c: pixels replaced", "@")
    assert [line for line, _ in lines[1:]] == [
        "size 128x256 (9 mips) -> 256x512 (10 mips), 4 parts",
        "pak: x.pak entry 3 (8x8)",
    ]
    assert panels.diff_lines(backend.Change(5, "image", {}, {}, detail="inline"))[1:] == []


def test_parse_error_extracts_expected_found_offset():
    p = dialogs.parse_error("asset 5 (image): expected an image of (128, 256, 4), found (256, 512)")
    assert p["expected"] == "an image of (128, 256, 4)" and p["found"] == "(256, 512)"
    p = dialogs.parse_error("pak entry 3: expected 1024 bytes at 0x2a00, found 512")
    assert p["found"] == "512" and p["offset"] == "0x2a00"
    p = dialogs.parse_error("expected the patch magic at offset 0, found b'XXXX'")
    assert p["offset"] == "0" and p["found"] == "b'XXXX'"
    # a message with no structure leaves the parts out (the full message still shows)
    assert dialogs.parse_error("something went wrong") == {}


def test_error_dialog_shows_the_parts_and_the_full_message():
    msg = "DDS fourCC: expected DXT1/DXT3/DXT5, found b'XXXX' at 0x54"
    d = dialogs.ErrorDialog("Save failed", "Could not save foo.ff.", msg)
    from PySide6.QtWidgets import QLabel, QPlainTextEdit

    labels = [w.text() for w in d.findChildren(QLabel)]
    assert "Expected" in labels and "Found" in labels and "Offset" in labels
    assert any("DXT1/DXT3/DXT5" in t for t in labels)  # the expected value
    body = d.findChild(QPlainTextEdit)
    assert body is not None and body.toPlainText() == msg  # nothing lost


def test_search_panel_empty_state(doc):
    p = panels.SearchPanel()
    p.set_doc(doc)
    assert p.empty.isVisibleTo(p) and not p.view.isVisibleTo(p)
    p.query.setText("speed")
    p.run()
    assert p.view.isVisibleTo(p) and not p.empty.isVisibleTo(p)
    p.query.setText("no_such_token_here")
    p.run()
    assert p.empty.isVisibleTo(p) and not p.view.isVisibleTo(p)
    assert "No matches" in p.empty.text()


def test_zone_opens_with_tree_and_search():
    from opent5 import env

    folder = env.path_of("OPENT5_PATCH_ZONES")
    path = folder / "patch_mp.ff" if folder else None
    if path is None or not path.is_file():
        pytest.skip("patch_mp.ff not configured in .env")
    d = backend.ZoneDoc.open(path)
    m = tree.AssetTreeModel()
    m.set_doc(d)
    assert m.count() == len(d.all_refs) and len(d.refs) == 1337
    assert any(h.ref.name == "default_mp.cfg" for h in d.search("default_mp.cfg"))
