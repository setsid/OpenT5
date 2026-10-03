"""Stringtable grid and the localize key/value table.

Both are QTableViews over small models that read the document and send each
edited cell straight to the backend (``set_cell`` / ``set_localize``). Edited
cells are drawn in the ``modified`` colour. Rows can be added and removed when
the backend offers it (opent5.edit's ``add_row`` / ``remove_row``).
"""

from __future__ import annotations

import re

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt
from PySide6.QtGui import QAction, QColor, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QTableView,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from opent5.gui import theme
from opent5.gui.backend import EditError, Ref, ZoneDoc
from opent5.gui.views.base import AssetView

ROLE = Qt.ItemDataRole
NO_INDEX = QModelIndex()
CELL_DETAIL = re.compile(r"row (\d+), col(?:umn)? (\d+)")


class StringTableModel(QAbstractTableModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.doc: ZoneDoc | None = None
        self.ref: Ref | None = None
        self.rows: list[list[str]] = []
        self.original: list[list[str]] = []
        self.error: str | None = None

    def load(self, doc: ZoneDoc, ref: Ref) -> None:
        self.beginResetModel()
        self.doc, self.ref = doc, ref
        self.rows = doc.table(ref)
        edited = {}
        for c in doc.changes():
            m = CELL_DETAIL.match(c.detail or "")
            if c.key == ref.key and c.kind == "cell" and m:
                edited.setdefault((int(m.group(1)), int(m.group(2))), c.before)
        self.original = [list(r) for r in self.rows]
        for (r, col), before in edited.items():
            if r < len(self.original) and col < len(self.original[r]):
                self.original[r][col] = before
        self.endResetModel()

    def rowCount(self, parent=NO_INDEX) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=NO_INDEX) -> int:  # noqa: N802
        return 0 if parent.isValid() or not self.rows else len(self.rows[0])

    def data(self, index, role=ROLE.DisplayRole):
        if not index.isValid():
            return None
        value = self.rows[index.row()][index.column()]
        if role in (ROLE.DisplayRole, ROLE.EditRole, ROLE.ToolTipRole):
            return value
        if role == ROLE.ForegroundRole and value != self.original[index.row()][index.column()]:
            return QColor(theme.current().modified)
        return None

    def headerData(self, section, orientation, role=ROLE.DisplayRole):  # noqa: N802
        if role != ROLE.DisplayRole:
            return None
        return str(section)

    def flags(self, index):
        base = Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled
        if self.ref is not None and not self.ref.inline:
            base |= Qt.ItemFlag.ItemIsEditable
        return base

    def setData(self, index, value, role=ROLE.EditRole) -> bool:  # noqa: N802
        if role != ROLE.EditRole or not index.isValid() or self.doc is None:
            return False
        r, c = index.row(), index.column()
        if value == self.rows[r][c]:
            return False
        try:
            self.doc.set_cell(self.ref, r, c, str(value))
        except EditError as exc:
            self.error = str(exc)
            return False
        self.rows[r][c] = str(value)
        self.dataChanged.emit(index, index)
        return True


def _table(model) -> QTableView:
    view = QTableView()
    view.setModel(model)
    view.setFont(theme.mono_font())
    view.verticalHeader().setDefaultSectionSize(view.fontMetrics().height() + 6)
    view.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
    view.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
    view.horizontalHeader().setHighlightSections(False)
    view.verticalHeader().setHighlightSections(False)
    view.setAlternatingRowColors(False)
    view.setWordWrap(False)
    view.setEditTriggers(
        QAbstractItemView.EditTrigger.DoubleClicked
        | QAbstractItemView.EditTrigger.EditKeyPressed
        | QAbstractItemView.EditTrigger.AnyKeyPressed
    )
    return view


class TableView(AssetView):
    kind = "table"
    title = "Table"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.model = StringTableModel(self)
        self.view = _table(self.model)
        self.view.verticalHeader().setFont(theme.mono_font())
        self.model.dataChanged.connect(lambda *_: self.edited.emit())
        self.view.selectionModel().currentChanged.connect(self._current)
        self.shape = QLabel("")
        self.insert_btn = QToolButton(text="Insert row")
        self.insert_btn.setToolTip("Insert an empty row below the current one (Ctrl+Shift+Enter)")
        self.insert_btn.clicked.connect(self.insert_row)
        self.insert_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.remove_btn = QToolButton(text="Remove row")
        self.remove_btn.setToolTip("Remove the current row (Ctrl+Shift+Delete)")
        self.remove_btn.clicked.connect(self.remove_row)
        self.remove_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        bar = QWidget()
        bar.setObjectName("ViewBar")
        h = QHBoxLayout(bar)
        h.setContentsMargins(4, 0, 8, 0)
        h.setSpacing(0)
        h.addWidget(self.insert_btn)
        h.addWidget(self.remove_btn)
        h.addStretch(1)
        h.addWidget(self.shape)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(bar)
        lay.addWidget(self.view)
        self.a_insert = QAction("Insert row", self, shortcut=QKeySequence("Ctrl+Shift+Return"))
        self.a_insert.triggered.connect(self.insert_row)
        self.a_remove = QAction("Remove row", self, shortcut=QKeySequence("Ctrl+Shift+Delete"))
        self.a_remove.triggered.connect(self.remove_row)
        for act in (self.a_insert, self.a_remove):
            act.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            self.addAction(act)

    def view_actions(self) -> list[QAction]:
        return [self.a_insert, self.a_remove]

    def load(self, doc, ref) -> None:
        super().load(doc, ref)
        self.model.load(doc, ref)
        self.view.resizeColumnsToContents()
        for c in range(self.model.columnCount()):
            self.view.setColumnWidth(c, min(max(self.view.columnWidth(c), 48), 320))
        self._update_bar()

    def _update_bar(self) -> None:
        can = self.doc is not None and self.doc.can_edit_rows and not self.ref.inline
        for w in (self.insert_btn, self.remove_btn, self.a_insert, self.a_remove):
            w.setEnabled(can)
        if not can:
            self.insert_btn.setToolTip("Adding rows needs opent5.edit")
        self.shape.setText(f"{self.model.rowCount()} rows x {self.model.columnCount()} columns")

    def refresh(self) -> None:
        current = self.view.currentIndex()
        self.model.load(self.doc, self.ref)
        self._update_bar()
        if current.isValid():
            row = min(current.row(), self.model.rowCount() - 1)
            self.view.setCurrentIndex(self.model.index(row, current.column()))

    def insert_row(self) -> None:
        if self.doc is None or not self.doc.can_edit_rows:
            return
        current = self.view.currentIndex()
        at = current.row() + 1 if current.isValid() else self.model.rowCount()
        try:
            row = self.doc.add_row(self.ref, at)
        except EditError as exc:
            self.status.emit(f"insert refused: {exc}")
            return
        self.refresh()
        self.select_cell(row, max(0, current.column()))
        self.edited.emit()

    def remove_row(self) -> None:
        current = self.view.currentIndex()
        if self.doc is None or not self.doc.can_edit_rows or not current.isValid():
            return
        try:
            self.doc.remove_row(self.ref, current.row())
        except EditError as exc:
            self.status.emit(f"remove refused: {exc}")
            return
        self.refresh()
        self.edited.emit()

    def _current(self, index, _prev) -> None:
        if index.isValid():
            self.status.emit(
                f"row {index.row()}, col {index.column()} of "
                f"{self.model.rowCount()} x {self.model.columnCount()}"
            )

    def select_cell(self, row: int, col: int) -> None:
        idx = self.model.index(row, col)
        self.view.setCurrentIndex(idx)
        self.view.scrollTo(idx, QAbstractItemView.ScrollHint.PositionAtCenter)


class LocalizeModel(QAbstractTableModel):
    """Every localize entry of the zone: key, value; values editable."""

    HEADERS = ("Key", "Value")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.doc: ZoneDoc | None = None
        self.refs: list[Ref] = []
        self.values: list[str] = []
        self.keys: list[str] = []
        self.original: dict = {}

    def load(self, doc: ZoneDoc) -> None:
        self.beginResetModel()
        self.doc = doc
        self.refs = [r for r in doc.all_refs if r.type_name == "localize"]
        self.keys, self.values = [], []
        for r in self.refs:
            key, value = doc.localize(r)
            self.keys.append(key or r.name)
            self.values.append(value)
        self.original = {c.key: c.before for c in doc.changes() if c.kind == "localize"}
        self.endResetModel()

    def row_of(self, ref: Ref) -> int:
        for i, r in enumerate(self.refs):
            if r.key == ref.key:
                return i
        return -1

    def rowCount(self, parent=NO_INDEX) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.refs)

    def columnCount(self, parent=NO_INDEX) -> int:  # noqa: N802
        return 0 if parent.isValid() else 2

    def data(self, index, role=ROLE.DisplayRole):
        if not index.isValid():
            return None
        r, c = index.row(), index.column()
        if role in (ROLE.DisplayRole, ROLE.EditRole):
            return self.keys[r] if c == 0 else self.values[r]
        if role == ROLE.ToolTipRole and c == 1:
            before = self.original.get(self.refs[r].key)
            return self.values[r] if before is None else f"was: {before}"
        if role == ROLE.ForegroundRole:
            if c == 0:
                return QColor(theme.current().text_dim)
            if self.refs[r].key in self.original:
                return QColor(theme.current().modified)
        return None

    def headerData(self, section, orientation, role=ROLE.DisplayRole):  # noqa: N802
        if role != ROLE.DisplayRole:
            return None
        if orientation == Qt.Orientation.Horizontal:
            return self.HEADERS[section]
        return str(section)

    def flags(self, index):
        base = Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled
        if index.column() == 1 and not self.refs[index.row()].inline:
            base |= Qt.ItemFlag.ItemIsEditable
        return base

    def setData(self, index, value, role=ROLE.EditRole) -> bool:  # noqa: N802
        if role != ROLE.EditRole or index.column() != 1 or self.doc is None:
            return False
        r = index.row()
        if value == self.values[r]:
            return False
        try:
            self.doc.set_localize(self.refs[r], str(value))
        except EditError:
            return False
        self.values[r] = str(value)
        self.original = {c.key: c.before for c in self.doc.changes() if c.kind == "localize"}
        self.dataChanged.emit(index, index)
        return True


class LocalizeView(AssetView):
    kind = "localize"
    title = "Localize"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.model = LocalizeModel(self)
        self.proxy = QSortFilterProxyModel(self)
        self.proxy.setSourceModel(self.model)
        self.proxy.setFilterKeyColumn(-1)
        self.proxy.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.view = _table(self.proxy)
        self.view.horizontalHeader().setStretchLastSection(True)
        self.view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.view.verticalHeader().hide()
        self.filter = QLineEdit(placeholderText="Filter keys and values")
        self.filter.textChanged.connect(self.proxy.setFilterFixedString)
        self.count = QLabel()
        bar = QWidget()
        bar.setObjectName("ViewBar")
        h = QHBoxLayout(bar)
        h.setContentsMargins(6, 3, 6, 3)
        h.setSpacing(6)
        h.addWidget(self.filter, 1)
        h.addWidget(self.count)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(bar)
        lay.addWidget(self.view)
        self.model.dataChanged.connect(lambda *_: self.edited.emit())
        self._doc_loaded = None

    def load(self, doc, ref) -> None:
        super().load(doc, ref)
        if self._doc_loaded is not doc:
            self.model.load(doc)
            self._doc_loaded = doc
            self.view.setColumnWidth(0, 300)
        self.count.setText(f"{self.model.rowCount()} entries")
        row = self.model.row_of(ref)
        if row >= 0:
            idx = self.proxy.mapFromSource(self.model.index(row, 1))
            if not idx.isValid():
                self.filter.clear()
                idx = self.proxy.mapFromSource(self.model.index(row, 1))
            self.view.setCurrentIndex(idx)
            self.view.scrollTo(idx, QAbstractItemView.ScrollHint.PositionAtCenter)
            self.status.emit(f"entry {row + 1} of {self.model.rowCount()}")

    def refresh(self) -> None:
        current = self.view.currentIndex()
        self.model.load(self.doc)
        if current.isValid():
            self.view.setCurrentIndex(current)
