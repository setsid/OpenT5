"""The bottom panels: zone-wide search, and the list of changes with a diff."""

from __future__ import annotations

import difflib

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, Signal
from PySide6.QtGui import QColor, QTextCharFormat, QTextCursor, QTextFormat
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QSplitter,
    QTableView,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from opent5.gui import theme
from opent5.gui.backend import Change, SearchHit, ZoneDoc

ROLE = Qt.ItemDataRole
NO_INDEX = QModelIndex()


def _table_view(model) -> QTableView:
    v = QTableView()
    v.setModel(model)
    v.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    v.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    v.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    v.setShowGrid(False)
    v.setWordWrap(False)
    v.verticalHeader().hide()
    v.verticalHeader().setDefaultSectionSize(v.fontMetrics().height() + 4)
    v.horizontalHeader().setHighlightSections(False)
    v.horizontalHeader().setStretchLastSection(True)
    v.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
    return v


class HitsModel(QAbstractTableModel):
    HEADERS = ("Asset", "Type", "Where", "Match")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.hits: list[SearchHit] = []

    def set_hits(self, hits: list[SearchHit]) -> None:
        self.beginResetModel()
        self.hits = hits
        self.endResetModel()

    def rowCount(self, parent=NO_INDEX) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.hits)

    def columnCount(self, parent=NO_INDEX) -> int:  # noqa: N802
        return 4

    @staticmethod
    def where(h: SearchHit) -> str:
        if h.where == "text":
            return f"line {h.line}"
        if h.where == "cell":
            return f"row {h.row}, col {h.column}"
        return h.where

    def data(self, index, role=ROLE.DisplayRole):
        if not index.isValid():
            return None
        h = self.hits[index.row()]
        if role == ROLE.DisplayRole:
            return (h.ref.label, h.ref.type_name, self.where(h), h.snippet)[index.column()]
        if role == ROLE.ForegroundRole and index.column() in (1, 2):
            return QColor(theme.current().text_dim)
        if role == ROLE.FontRole and index.column() == 3:
            return theme.mono_font()
        return None

    def headerData(self, section, orientation, role=ROLE.DisplayRole):  # noqa: N802
        if role == ROLE.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return self.HEADERS[section]
        return None


class SearchPanel(QWidget):
    """Search across asset names and rawfile text, stringtable cells, localize values."""

    jump = Signal(object)  # SearchHit

    def __init__(self, parent=None):
        super().__init__(parent)
        self.doc: ZoneDoc | None = None
        self.query = QLineEdit(placeholderText="Search names and contents (Enter)")
        self.query.setClearButtonEnabled(True)
        self.names = QCheckBox("Names")
        self.contents = QCheckBox("Contents")
        self.case = QCheckBox("Match case")
        self.names.setChecked(True)
        self.contents.setChecked(True)
        self.summary = QLabel("")
        self.summary.setObjectName("AssetMeta")
        bar = QWidget()
        bar.setObjectName("PanelHeader")
        h = QHBoxLayout(bar)
        h.setContentsMargins(4, 3, 6, 3)
        h.setSpacing(8)
        h.addWidget(self.query, 1)
        for w in (self.names, self.contents, self.case, self.summary):
            h.addWidget(w)
        self.model = HitsModel(self)
        self.view = _table_view(self.model)
        self.view.setColumnWidth(0, 300)
        self.view.setColumnWidth(1, 90)
        self.view.setColumnWidth(2, 110)
        self.view.activated.connect(self._activated)
        self.view.doubleClicked.connect(self._activated)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(bar)
        lay.addWidget(self.view, 1)
        self.query.returnPressed.connect(self.run)

    def set_doc(self, doc: ZoneDoc | None) -> None:
        if doc is not self.doc:
            self.doc = doc
            self.model.set_hits([])
            self.summary.setText("")

    def run(self) -> list[SearchHit]:
        text = self.query.text()
        if self.doc is None or not text:
            self.model.set_hits([])
            self.summary.setText("")
            return []
        hits = self.doc.search(
            text,
            names=self.names.isChecked(),
            contents=self.contents.isChecked(),
            case=self.case.isChecked(),
        )
        self.model.set_hits(hits)
        assets = len({h.ref.key for h in hits})
        self.summary.setText(f"{len(hits)} matches in {assets} assets" if hits else "no matches")
        return hits

    def focus(self) -> None:
        self.query.setFocus()
        self.query.selectAll()

    def _activated(self, index) -> None:
        if index.isValid():
            self.jump.emit(self.model.hits[index.row()])


class ChangesModel(QAbstractTableModel):
    HEADERS = ("Asset", "Type", "Change")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.changes: list[Change] = []

    def set_changes(self, changes: list[Change]) -> None:
        self.beginResetModel()
        self.changes = changes
        self.endResetModel()

    def rowCount(self, parent=NO_INDEX) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.changes)

    def columnCount(self, parent=NO_INDEX) -> int:  # noqa: N802
        return 3

    @staticmethod
    def summary(c: Change) -> str:
        if c.kind == "text" and isinstance(c.before, str) and isinstance(c.after, str):
            a, b = c.before.splitlines(), c.after.splitlines()
            plus = minus = 0
            for line in difflib.unified_diff(a, b, lineterm="", n=0):
                if line.startswith("+") and not line.startswith("+++"):
                    plus += 1
                elif line.startswith("-") and not line.startswith("---"):
                    minus += 1
            return f"text  +{plus} -{minus}"
        return f"{c.kind}  {c.detail}".strip()

    def data(self, index, role=ROLE.DisplayRole):
        if not index.isValid():
            return None
        c = self.changes[index.row()]
        if role == ROLE.DisplayRole:
            return (c.name or str(c.key), c.type_name, self.summary(c))[index.column()]
        if role == ROLE.ForegroundRole:
            if index.column() == 0:
                return QColor(theme.current().modified)
            return QColor(theme.current().text_dim)
        return None

    def headerData(self, section, orientation, role=ROLE.DisplayRole):  # noqa: N802
        if role == ROLE.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return self.HEADERS[section]
        return None


class ChangesPanel(QWidget):
    """Edited assets on the left, the selected change as a unified diff on the right."""

    jump = Signal(object)  # Change

    def __init__(self, parent=None):
        super().__init__(parent)
        self.doc: ZoneDoc | None = None
        self.model = ChangesModel(self)
        self.list = _table_view(self.model)
        self.list.setColumnWidth(0, 240)
        self.list.setColumnWidth(1, 80)
        self.list.selectionModel().currentRowChanged.connect(self._show)
        self.list.doubleClicked.connect(self._jump)
        self.diff = QPlainTextEdit()
        self.diff.setReadOnly(True)
        self.diff.setFont(theme.mono_font())
        self.diff.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.empty = QLabel("No changes. Edits appear here until the zone is saved.")
        self.empty.setObjectName("EmptyState")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        split = QSplitter(Qt.Orientation.Horizontal)
        split.setHandleWidth(1)
        split.addWidget(self.list)
        split.addWidget(self.diff)
        split.setSizes([420, 800])
        self.split = split
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(split)
        lay.addWidget(self.empty)
        self.split.hide()
        theme.on_change(lambda _t: self._show(self.list.currentIndex(), None), self)

    def set_doc(self, doc: ZoneDoc | None) -> None:
        self.doc = doc
        self.reload()

    def reload(self) -> None:
        changes = self.doc.changes() if self.doc is not None else []
        current = self.list.currentIndex().row()
        self.model.set_changes(changes)
        self.split.setVisible(bool(changes))
        self.empty.setVisible(not changes)
        if changes:
            row = min(max(current, 0), len(changes) - 1)
            self.list.setCurrentIndex(self.model.index(row, 0))
            self._show(self.model.index(row, 0), None)
        else:
            self.diff.clear()

    def _jump(self, index) -> None:
        if index.isValid():
            self.jump.emit(self.model.changes[index.row()])

    def _show(self, index, _prev) -> None:
        if not index.isValid() or index.row() >= len(self.model.changes):
            return
        c = self.model.changes[index.row()]
        lines = diff_lines(c)
        self.diff.setPlainText("\n".join(text for text, _ in lines))
        t = theme.current()
        colours = {"+": t.diff_add, "-": t.diff_del}
        sels = []
        block = self.diff.document().firstBlock()
        for _text, kind in lines:
            if kind in colours or kind == "@":
                sel = QTextEdit.ExtraSelection()
                if kind == "@":
                    fmt = QTextCharFormat()
                    fmt.setForeground(QColor(t.text_faint))
                    sel.format = fmt
                else:
                    sel.format.setBackground(QColor(colours[kind]))
                sel.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
                cur = QTextCursor(block)
                sel.cursor = cur
                sels.append(sel)
            block = block.next()
        self.diff.setExtraSelections(sels)


def diff_lines(c: Change) -> list[tuple[str, str]]:
    """(line, kind) where kind is '+', '-', '@' (hunk header) or ' '."""
    title = f"{c.type_name} {c.name}".strip()
    if c.kind == "text" and isinstance(c.before, str) and isinstance(c.after, str):
        out = []
        for line in difflib.unified_diff(
            c.before.splitlines(),
            c.after.splitlines(),
            fromfile=f"{title} (original)",
            tofile=f"{title} (edited)",
            lineterm="",
            n=3,
        ):
            if line.startswith(("---", "+++")) or line.startswith("@@"):
                out.append((line, "@"))
            else:
                out.append((line, line[:1] if line[:1] in "+-" else " "))
        return out or [("(no textual difference)", " ")]
    if c.kind == "image":
        return [(f"{title}: pixels replaced", " ")]
    head = [(f"{title}  {c.detail}".strip(), "@")]
    return head + [(f"- {c.before}", "-"), (f"+ {c.after}", "+")]
