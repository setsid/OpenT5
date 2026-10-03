"""GUI: shared-string markers and the choice, unsaved state, progress, formatted GSC and
field editing, offscreen against a fake backend (no zone needed)."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtCore import QSettings, Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from opent5.edit.types import SharedField  # noqa: E402
from opent5.xfile.constants import AssetType as T  # noqa: E402

APP = QApplication.instance() or QApplication([])

from opent5.gui import backend, strips  # noqa: E402
from opent5.gui.views import code, fields, table  # noqa: E402
from test_gui_core import FakeImpl  # noqa: E402

GSC = "main()\n{\nif ( x )\nfoo();\n}\n"


class SharingImpl(FakeImpl):
    """FakeImpl with two localize keys sharing one string, a shared cell, share modes and
    schema fields."""

    def __init__(self):
        super().__init__()
        self.texts[0] = GSC
        self.assets.append(
            SimpleNamespace(
                index=4, type=T.LOCALIZE, name="MENU_ACCURACY", size=10,
                editable=("localize", "fields", "hex"), file_start=64,
            )
        )  # fmt: skip
        self.values[4] = "Accuracy"
        self.split: set[int] = set()
        self.cells_split = False
        self.field_values = {"fireTime": 0.25, "damage": 50, "count": 3}
        self.calls: list = []

    def localize(self, i):
        return ({3: "CGAME_SB_ACCURACY", 4: "MENU_ACCURACY"}[i], self.values[i])

    def shared_with(self, i, row=None, col=None):
        if row is not None:
            if self.cells_split or (row, col) not in ((0, 1), (1, 1)):
                return []
            other = 1 - row
            return [SharedField(2, "stringtable", "mp/mapstable.csv", f"row {other}, column 1")]
        if i in self.split or 3 in self.split or 4 in self.split:
            return []
        other = 4 if i == 3 else 3
        return [SharedField(other, "localize", self.localize(other)[0], "value", other == 3)]

    def set_localize(self, i, value, share="split"):
        self.calls.append(("localize", i, share))
        if share == "all" and not self.split:
            for k in (3, 4):
                self._op(k, "localize", self.values[k], value, "share=all" if k == i else "")
                self.values[k] = value
            self.ops = self.ops[:-2] + [self.ops[-1] if self.ops[-1].index == i else self.ops[-2]]
            return
        self.split.add(i)
        super().set_localize(i, value)

    def set_cell(self, i, r, c, text, share="split"):
        self.calls.append(("cell", r, c, share))
        if share == "all" and (r, c) in ((0, 1), (1, 1)):
            for rr in (0, 1):
                self.rows[rr][c] = text
            self._op(i, "cell", "", text, f"row {r}, column {c}; share=all")
            return
        self.cells_split = True
        super().set_cell(i, r, c, text)

    def fields(self, i):
        return {
            "_kind": "WeaponDef",
            "header": dict(self.field_values),
            "nested": {"_kind": "Material", "x": 1},
        }

    def field_info(self, i, path):
        if path not in self.field_values:
            raise backend.EditError(f"no field {path}")
        kinds = {"fireTime": ("f32", None), "damage": ("s32", None), "count": ("u16", "count")}
        t, role = kinds[path]
        reason = "a count: it sizes the array" if role == "count" else None
        return {"type": t, "role": role, "names": None, "value": self.field_values[path],
                "editable": reason is None, "reason": reason}  # fmt: skip

    def set_field(self, i, path, value):
        self._op(i, "field", self.field_values[path], value, path)
        self.field_values[path] = value


@pytest.fixture
def doc():
    d = backend.ZoneDoc(SharingImpl(), "edit")
    d._collect_inline()
    return d


@pytest.fixture
def win(tmp_path):
    from opent5.gui.mainwindow import MainWindow

    w = MainWindow(QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat))
    w.ask_before_discard = False
    yield w
    w.close()


# -- shared strings --------------------------------------------------------------------------


def test_localize_marks_shared_values_and_asks(doc):
    m = table.LocalizeModel()
    m.load(doc)
    asked = []
    m.ask = lambda what, others: asked.append((what, others)) or "all"
    assert m.data(m.index(0, 2)) == "MENU_ACCURACY"
    assert m.data(m.index(0, 1), Qt.ItemDataRole.DecorationRole) is not None
    assert "shared with: MENU_ACCURACY" in m.data(m.index(0, 1), Qt.ItemDataRole.ToolTipRole)
    assert m.setData(m.index(0, 1), "Hit rate")
    assert asked and asked[0][1] == ["MENU_ACCURACY"]
    assert doc.impl.calls[-1] == ("localize", 3, "all")
    assert m.values == ["Hit rate", "Hit rate"]  # both keys re-read


def test_localize_cancel_and_split(doc):
    m = table.LocalizeModel()
    m.load(doc)
    m.ask = lambda what, others: None
    assert not m.setData(m.index(1, 1), "Nope")
    assert doc.impl.calls == []
    m.ask = lambda what, others: "split"
    assert m.setData(m.index(1, 1), "Aim")
    assert doc.impl.calls[-1] == ("localize", 4, "split")
    assert m.values == ["Accuracy", "Aim"]
    assert m.data(m.index(0, 2)) == ""  # no longer shared


def test_unshared_values_do_not_ask(doc):
    doc.impl.split.add(3)
    m = table.LocalizeModel()
    m.load(doc)
    m.ask = lambda *_: pytest.fail("asked for an unshared value")
    assert m.setData(m.index(0, 1), "x")


def test_stringtable_cell_choice(doc):
    m = table.StringTableModel()
    ref = doc.ref(2)
    m.load(doc, ref)
    m.ask = lambda what, others: "all"
    assert m.data(m.index(0, 1), Qt.ItemDataRole.DecorationRole) is not None
    assert m.data(m.index(0, 0), Qt.ItemDataRole.DecorationRole) is None
    assert m.setData(m.index(0, 1), "Same")
    assert doc.impl.calls[-1] == ("cell", 0, 1, "all")
    assert [r[1] for r in m.rows] == ["Same", "Same"]


def test_share_dialog_defaults_to_the_last_choice():
    strips.ShareChoiceDialog.last = "all"
    d = strips.ShareChoiceDialog("KEY", ["OTHER"])
    assert d.all_btn.isDefault() and not d.split_btn.isDefault()
    d.split_btn.click()
    assert d.choice == "split" and strips.ShareChoiceDialog.last == "split"
    d2 = strips.ShareChoiceDialog("KEY", ["OTHER"])
    assert d2.split_btn.isDefault()
    assert "&a" in d2.all_btn.text().lower() and "&s" in d2.split_btn.text().lower()


# -- unsaved state and progress --------------------------------------------------------------


def test_unsaved_title_tab_and_strip(win, doc):
    page = win._add_doc(doc)
    assert win.windowTitle() == "fake - OpenT5"
    assert page.unsaved.isHidden()
    doc.set_text(doc.ref(1), "set g_speed 200\n")
    page._edited()
    win._update_state()
    assert win.windowTitle() == "* fake - OpenT5"
    assert win.tabs.tabText(0) == "* fake"
    assert not page.unsaved.isHidden()
    assert page.unsaved.label.text().startswith("Unsaved changes: 1 asset edited")
    win.discard(page)
    assert not doc.dirty and page.unsaved.isHidden() and win.tabs.tabText(0) == "fake"


def test_staged_progress_fills_each_share():
    seen = []
    p = backend.staged(lambda text, f: seen.append((text, f)), backend.SAVE_STAGES)
    p("Writing assets", 0, 100)
    p("Writing assets", 100, 100)
    p("Verifying assets", 50, 100)
    assert seen[0] == ("Writing assets 0 of 100", 0.0)
    assert seen[1][1] == pytest.approx(0.18)
    assert seen[2][1] == pytest.approx(0.925)
    assert backend.staged(None, backend.SAVE_STAGES) is None


def test_loading_page_and_progress_strip(tmp_path):
    page = strips.LoadingPage(Path(tmp_path / "mp_x.ff"))
    page.set_progress("Parsing assets 10 of 20", 0.55)
    assert page.progress.pct.text() == "55%"
    assert page.progress.bar.value() == 550


# -- formatted GSC ---------------------------------------------------------------------------


def test_text_view_formats_and_stores_minified(doc):
    code.FORMAT_SCRIPTS = True
    view = code.TextView()
    ref = doc.ref(0)
    view.load(doc, ref)
    assert view.formatted
    assert view.editor.toPlainText() == "main()\n{\n\tif ( x )\n\t\tfoo();\n}\n"
    assert not view.commit()  # unedited: nothing sent
    view.editor.setPlainText(view.editor.toPlainText().replace("foo();", "foo();\n\t\tbar();"))
    assert view.commit()
    assert doc.text(ref) == "main()\n{\nif ( x )\nfoo();\nbar();\n}\n"
    code.FORMAT_SCRIPTS = False
    view.load(doc, ref)
    assert not view.formatted and view.editor.toPlainText() == doc.text(ref)
    code.FORMAT_SCRIPTS = True
    cfg = doc.ref(1)
    view.load(doc, cfg)
    assert not view.formatted


# -- fields ----------------------------------------------------------------------------------


def test_parse_input_by_type():
    f32 = {"type": "f32"}
    assert fields.parse_input("1.5", f32) == 1.5
    assert fields.parse_input("0x10", {"type": "u8"}) == 16
    assert fields.parse_input("1 2 3", {"type": "vec3"}) == [1.0, 2.0, 3.0]
    assert fields.parse_input("1, 2, 3, 4", {"type": "u16[4]"}) == [1, 2, 3, 4]
    assert fields.parse_input("rifle", {"type": "s32", "names": {3: "rifle"}}) == 3
    for text, info in (
        ("256", {"type": "u8"}),
        ("-1", {"type": "u16"}),
        ("abc", {"type": "s32"}),
        ("1e40", f32),
        ("1 2", {"type": "vec3"}),
    ):
        with pytest.raises(fields.FieldInputError):
            fields.parse_input(text, info)


def test_fields_edit_lock_and_refusal(doc):
    view = fields.FieldsView()
    ref = doc.ref(0)
    view.load(doc, ref)
    m = view.model
    header = m.index(0, 0)
    rows = {m.index(r, 0, header).data(): r for r in range(m.rowCount(header))}
    fire = m.index(rows["fireTime"], 1, header)
    count = m.index(rows["count"], 1, header)
    assert m.flags(fire) & Qt.ItemFlag.ItemIsEditable
    assert not m.flags(count) & Qt.ItemFlag.ItemIsEditable
    assert m.data(count, Qt.ItemDataRole.DecorationRole) is not None
    assert "count" in m.data(count, Qt.ItemDataRole.ToolTipRole)
    assert m.setData(fire, "0.5")
    assert doc.impl.field_values["fireTime"] == 0.5
    assert doc.changes()[-1].kind == "field" and doc.changes()[-1].detail == "fireTime"
    dmg = m.index(rows["damage"], 1, header)
    assert not m.setData(dmg, "lots")
    assert "expected an integer" in m.error
    nested = m.index(2, 0)
    assert fields.field_path(m.index(0, 1, nested).internalPointer()) is None
