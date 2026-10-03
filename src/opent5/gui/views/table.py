"""Stringtable grid and the localize key/value table.

Both are QTableViews over small models that read the document and send each
edited cell straight to the backend (``set_cell`` / ``set_localize``). Edited
cells are drawn in the ``modified`` colour. Rows can be added and removed when
the backend offers it (opent5.edit's ``add_row`` / ``remove_row``).

Shared strings: a value the zone stores once for several keys or cells carries a link
marker, the localize table lists what it is shared with, and the line under each table
names the sharers of the current value. Editing a shared value always asks first: edit all
sharing keys, or split this key (``strips.ShareChoiceDialog``).
"""

from __future__ import annotations

import re

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt
from PySide6.QtGui import QAction, QColor, QIcon, QKeySequence
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

from opent5.gui import icons, strips, theme
from opent5.gui.backend import EditError, Ref, ZoneDoc
from opent5.gui.views.base import AssetView

ROLE = Qt.ItemDataRole
NO_INDEX = QModelIndex()
CELL_DETAIL = re.compile(r"row (\d+), col(?:umn)? (\d+)")


def link_icon() -> QIcon:
    """The shared-string marker, in the accent colour of the current theme."""
    t = theme.current()
    return QIcon(icons.draw_pixmap("link", t.accent, 14))


def shared_text(sharers: list) -> str:
    return "shared with: " + ", ".join(s.label for s in sharers) if sharers else ""


class _SharedCache:
    """Sharers per key, computed when a row is first drawn (the index itself is built
    when the zone opens)."""

    def __init__(self):
        self.doc = None
        self.values: dict = {}

    def get(self, doc, key, fn):
        if doc is not self.doc:
            self.doc, self.values = doc, {}
        if key not in self.values:
            self.values[key] = fn() if doc is not None and doc.can_share else []
        return self.values[key]

    def clear(self) -> None:
        self.values = {}


class StringTableModel(QAbstractTableModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.doc: ZoneDoc | None = None
        self.ref: Ref | None = None
        self.rows: list[list[str]] = []
        self.original: list[list[str]] = []
        self.error: str | None = None
        self.shared = _SharedCache()
        #: (what, other labels) -> "all" | "split" | None; replaced in tests
        self.ask = strips.ask_share
        self.last_share: str | None = None

    def sharers(self, r: int, c: int) -> list:
        return self.shared.get(
            self.doc, (r, c), lambda: self.doc.shared_with(self.ref, r, c) if self.ref else []
        )

    def load(self, doc: ZoneDoc, ref: Ref) -> None:
        self.beginResetModel()
        self.doc, self.ref = doc, ref
        self.shared.clear()
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
        if role in (ROLE.DisplayRole, ROLE.EditRole):
            return value
        if role == ROLE.ToolTipRole:
            shared = shared_text(self.sharers(index.row(), index.column()))
            return f"{value}\n{shared}" if shared else value
        if role == ROLE.DecorationRole:
            return link_icon() if self.sharers(index.row(), index.column()) else None
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
        share = "split"
        sharers = self.sharers(r, c)
        if sharers:
            choice = self.ask(
                f'Cell (row {r}, column {c}) "{self.rows[r][c]}"', [s.label for s in sharers]
            )
            if choice is None:
                return False
            share = choice
        try:
            self.doc.set_cell(self.ref, r, c, str(value), share=share)
        except EditError as exc:
            self.error = str(exc)
            return False
        self.last_share = share if sharers else None
        if sharers:
            # other cells (and the sharing state) may have changed
            current = [list(row) for row in self.original]
            self.load(self.doc, self.ref)
            self.original = current
            return True
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
        self.note = share_note()
        self.model.ask = lambda what, others: strips.ask_share(what, others, self)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(bar)
        lay.addWidget(self.view)
        lay.addWidget(self.note)
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
            sharers = self.model.sharers(index.row(), index.column())
            show_share_note(self.note, f"Row {index.row()}, column {index.column()}", sharers)
        else:
            self.note.hide()

    def select_cell(self, row: int, col: int) -> None:
        idx = self.model.index(row, col)
        self.view.setCurrentIndex(idx)
        self.view.scrollTo(idx, QAbstractItemView.ScrollHint.PositionAtCenter)


def share_note() -> QLabel:
    note = QLabel()
    note.setObjectName("ShareNote")
    note.setWordWrap(True)
    note.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    note.hide()
    return note


def show_share_note(note: QLabel, what: str, sharers: list) -> None:
    if not sharers:
        note.hide()
        return
    names = ", ".join(s.label for s in sharers)
    note.setText(
        f"{what} is stored once and shared with: {names}. Editing it asks whether to "
        "change every sharing key or only this one."
    )
    note.show()


class _KeyValueFilter(QSortFilterProxyModel):
    """Filter on the key and value columns only (not the computed shared column)."""

    needle = ""

    def set_needle(self, text: str) -> None:
        self.needle = text.lower()
        self.invalidateFilter()

    def filterAcceptsRow(self, row, parent) -> bool:  # noqa: N802
        if not self.needle:
            return True
        m = self.sourceModel()
        return self.needle in m.keys[row].lower() or self.needle in m.values[row].lower()


class LocalizeModel(QAbstractTableModel):
    """Every localize entry of the zone: key, value, what it shares its stored string
    with; values editable."""

    HEADERS = ("Key", "Value", "Shared with")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.doc: ZoneDoc | None = None
        self.refs: list[Ref] = []
        self.values: list[str] = []
        self.keys: list[str] = []
        self.original: dict = {}
        self.shared = _SharedCache()
        self.ask = strips.ask_share
        self.last_share: str | None = None

    def sharers(self, r: int) -> list:
        ref = self.refs[r]
        return self.shared.get(self.doc, ref.key, lambda: self.doc.shared_with(ref))

    def load(self, doc: ZoneDoc) -> None:
        self.beginResetModel()
        self.doc = doc
        self.shared.clear()
        self.refs = [r for r in doc.all_refs if r.type_name == "localize"]
        self.keys, self.values = [], []
        for r in self.refs:
            key, value = doc.localize(r)
            self.keys.append(key or r.name)
            self.values.append(value)
        self.original = {c.key: c.before for c in doc.changes() if c.kind == "localize"}
        for key, before in self._shared_befores().items():
            self.original.setdefault(key, before)
        self.endResetModel()

    def row_of(self, ref: Ref) -> int:
        for i, r in enumerate(self.refs):
            if r.key == ref.key:
                return i
        return -1

    def rowCount(self, parent=NO_INDEX) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.refs)

    def columnCount(self, parent=NO_INDEX) -> int:  # noqa: N802
        return 0 if parent.isValid() else 3

    def data(self, index, role=ROLE.DisplayRole):
        if not index.isValid():
            return None
        r, c = index.row(), index.column()
        if c == 2:
            if role in (ROLE.DisplayRole, ROLE.ToolTipRole):
                return ", ".join(s.label for s in self.sharers(r))
            if role == ROLE.ForegroundRole:
                return QColor(theme.current().text_dim)
            return None
        if role in (ROLE.DisplayRole, ROLE.EditRole):
            return self.keys[r] if c == 0 else self.values[r]
        if role == ROLE.DecorationRole and c == 1:
            return link_icon() if self.sharers(r) else None
        if role == ROLE.ToolTipRole and c == 1:
            before = self.original.get(self.refs[r].key)
            text = self.values[r] if before is None else f"was: {before}"
            shared = shared_text(self.sharers(r))
            return f"{text}\n{shared}" if shared else text
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
        share = "split"
        sharers = self.sharers(r)
        if sharers:
            choice = self.ask(f'{self.keys[r]} ("{self.values[r]}")', [s.label for s in sharers])
            if choice is None:
                return False
            share = choice
        try:
            self.doc.set_localize(self.refs[r], str(value), share=share)
        except EditError as exc:
            self.error = str(exc)
            return False
        self.last_share = share if sharers else None
        if sharers:
            # other keys may now read the new text, and the sharing has changed
            self.refresh_values()
            return True
        self.values[r] = str(value)
        self.original = {c.key: c.before for c in self.doc.changes() if c.kind == "localize"}
        self.dataChanged.emit(index, index)
        return True

    def refresh_values(self) -> None:
        """Re-read every value and the sharing (after an edit that changed several)."""
        self.values = [self.doc.localize(r)[1] for r in self.refs]
        self.original = {c.key: c.before for c in self.doc.changes() if c.kind == "localize"}
        for key, before in self._shared_befores().items():
            self.original.setdefault(key, before)
        self.shared.clear()
        if self.refs:
            self.dataChanged.emit(self.index(0, 0), self.index(len(self.refs) - 1, 2))

    def _shared_befores(self) -> dict:
        """Keys changed through another key's share="all" edit: their text before."""
        out = {}
        for c in self.doc.changes():
            if c.kind == "localize":
                for key in c.also:
                    out.setdefault(key, c.before)
        return out


class LocalizeView(AssetView):
    kind = "localize"
    title = "Localize"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.model = LocalizeModel(self)
        self.proxy = _KeyValueFilter(self)
        self.proxy.setSourceModel(self.model)
        self.proxy.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.view = _table(self.proxy)
        self.view.horizontalHeader().setStretchLastSection(True)
        self.model.ask = lambda what, others: strips.ask_share(what, others, self)
        self.note = share_note()
        self.view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.view.verticalHeader().hide()
        self.filter = QLineEdit(placeholderText="Filter keys and values")
        self.filter.textChanged.connect(self.proxy.set_needle)
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
        lay.addWidget(self.note)
        self.model.dataChanged.connect(lambda *_: self.edited.emit())
        self.view.selectionModel().currentChanged.connect(self._current)
        self._doc_loaded = None

    def _current(self, index, _prev) -> None:
        if not index.isValid():
            self.note.hide()
            return
        r = self.proxy.mapToSource(index).row()
        show_share_note(self.note, self.model.keys[r], self.model.sharers(r))

    def load(self, doc, ref) -> None:
        super().load(doc, ref)
        if self._doc_loaded is not doc:
            self.model.load(doc)
            self._doc_loaded = doc
            self.view.setColumnWidth(0, 300)
            self.view.setColumnWidth(1, 520)
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
        where = (current.row(), current.column()) if current.isValid() else None
        self.model.load(self.doc)  # resets the model: the old index is stale now
        if where is not None and where[0] < self.proxy.rowCount():
            self.view.setCurrentIndex(self.proxy.index(*where))
