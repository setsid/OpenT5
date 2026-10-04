"""The bottom panels: zone-wide search, cross-zone search, and the changes/diff list."""

from __future__ import annotations

import difflib
from dataclasses import dataclass

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, Signal
from PySide6.QtGui import QColor, QTextCharFormat, QTextCursor, QTextFormat
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QProgressBar,
    QSplitter,
    QTableView,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from opent5 import env
from opent5.gui import theme
from opent5.gui.backend import Change, SearchHit, ZoneDoc
from opent5.index import Index, Store, build

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
        self.empty = QLabel(self.DEFAULT_EMPTY)
        self.empty.setObjectName("EmptyState")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.setWordWrap(True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(bar)
        lay.addWidget(self.view, 1)
        lay.addWidget(self.empty, 1)
        self.view.hide()
        self.query.returnPressed.connect(self.run)

    DEFAULT_EMPTY = (
        "Search this zone's asset names and contents (scripts, stringtable cells and "
        "localize values). Type a query and press Enter."
    )

    def _show_hits(self, hits: list[SearchHit], empty_text: str | None = None) -> None:
        self.model.set_hits(hits)
        self.view.setVisible(bool(hits))
        self.empty.setVisible(not hits)
        if not hits:
            self.empty.setText(empty_text or self.DEFAULT_EMPTY)

    def set_doc(self, doc: ZoneDoc | None) -> None:
        if doc is not self.doc:
            self.doc = doc
            self._show_hits([])
            self.summary.setText("")

    def run(self) -> list[SearchHit]:
        text = self.query.text()
        if self.doc is None or not text:
            self._show_hits([])
            self.summary.setText("")
            return []
        hits = self.doc.search(
            text,
            names=self.names.isChecked(),
            contents=self.contents.isChecked(),
            case=self.case.isChecked(),
        )
        self._show_hits(hits, f'No matches for "{text}" in this zone.')
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
        out = [(f"{title}: pixels replaced", "@")]
        for part in (c.detail or "").split("; "):
            if part and part not in ("inline", "deferred"):
                out.append((part, " "))
        return out
    head = [(f"{title}  {c.detail}".strip(), "@")]
    return head + [(f"- {c.before}", "-"), (f"+ {c.after}", "+")]


# -- cross-zone search (opent5.index over every configured zone) ----------------------------

#: The match kinds the kind filter offers (label, value passed to Index.search as one kind).
KIND_CHOICES = (
    ("All kinds", ""),
    ("Asset name", "name"),
    ("Script / rawfile", "rawfile"),
    ("Stringtable cell", "cell"),
    ("Localize value", "localize"),
    ("Entity string", "entities"),
)


@dataclass
class SearchParams:
    """What the global-search header describes, passed to ``run_search``."""

    term: str
    names_only: bool = False
    type_name: str = ""
    kind: str = ""
    regex: bool = False
    case: bool = False
    zone: str = ""


def run_search(params: SearchParams, progress=None, cache_dir=None):
    """Bring the index up to date (changed zones only), then search it.

    ``progress(done, total)`` is called once per re-parsed zone while the cache is built,
    so a cold first run shows determinate progress; a warm run returns at once. Returns a
    ``opent5.index.SearchResult``.
    """

    def on_zone(done, total, _path, _status):
        if progress is not None:
            progress(done, total)

    kinds = (params.kind,) if params.kind else None
    with Store.open(cache_dir) as store:
        build(env.all_zones(), store, progress=on_zone)
        return Index(store).search(
            params.term,
            kinds=kinds,
            regex=params.regex,
            case=params.case,
            names_only=params.names_only,
            type_name=params.type_name or None,
            zone=params.zone or None,
        )


class GlobalSearchPanel(QWidget):
    """Search every configured zone through ``opent5.index``: a query with options, results
    grouped by zone, and a double-click that opens the zone and jumps to the match.

    The index is built on a worker the first time (determinate bar); later searches are
    instant. The panel does not open zones itself: it emits ``search_requested`` for the
    window to run on a worker, and ``open_hit`` when a result is activated."""

    search_requested = Signal(object)  # SearchParams
    open_hit = Signal(object)  # opent5.index.Hit

    def __init__(self, parent=None):
        super().__init__(parent)
        self.query = QLineEdit(placeholderText="Search all zones (Enter)")
        self.query.setClearButtonEnabled(True)
        self.names = QCheckBox("Names only")
        self.names.setToolTip("Match asset names alone (the fast path), ignoring text contents")
        self.case = QCheckBox("Match case")
        self.regex = QCheckBox("Regex")
        self.kind = QComboBox()
        for label, value in KIND_CHOICES:
            self.kind.addItem(label, value)
        self.kind.setToolTip("Keep only one kind of match")
        self.type_box = QLineEdit(placeholderText="type")
        self.type_box.setToolTip("Keep one asset type (for example weapon, material, image)")
        self.type_box.setMaximumWidth(110)
        self.type_box.setClearButtonEnabled(True)
        self.zone_box = QLineEdit(placeholderText="zone glob")
        self.zone_box.setToolTip("Keep zones whose name matches this glob, for example mp_*")
        self.zone_box.setMaximumWidth(120)
        self.zone_box.setClearButtonEnabled(True)
        self.summary = QLabel("")
        self.summary.setObjectName("AssetMeta")
        self.progress = QProgressBar()
        self.progress.setMaximumWidth(160)
        self.progress.setRange(0, 1000)
        self.progress.setTextVisible(False)
        self.progress.hide()
        bar = QWidget()
        bar.setObjectName("PanelHeader")
        h = QHBoxLayout(bar)
        h.setContentsMargins(4, 3, 6, 3)
        h.setSpacing(8)
        h.addWidget(self.query, 1)
        for w in (
            self.names,
            self.case,
            self.regex,
            self.kind,
            self.type_box,
            self.zone_box,
            self.progress,
            self.summary,
        ):
            h.addWidget(w)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(4)
        self.tree.setHeaderLabels(["Asset", "Type", "Where", "Match"])
        self.tree.setRootIsDecorated(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tree.setColumnWidth(0, 320)
        self.tree.setColumnWidth(1, 90)
        self.tree.setColumnWidth(2, 120)
        self.tree.header().setStretchLastSection(True)
        self.tree.header().setHighlightSections(False)
        self.tree.itemActivated.connect(self._activated)
        self.tree.itemDoubleClicked.connect(self._activated)
        self.empty = QLabel(
            "Search every configured zone. The first search builds the index; later searches "
            "are instant."
        )
        self.empty.setObjectName("EmptyState")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(bar)
        lay.addWidget(self.tree, 1)
        lay.addWidget(self.empty, 1)
        self.tree.hide()
        self.query.returnPressed.connect(self._emit_request)

    def current_params(self) -> SearchParams:
        return SearchParams(
            term=self.query.text(),
            names_only=self.names.isChecked(),
            type_name=self.type_box.text().strip(),
            kind=self.kind.currentData() or "",
            regex=self.regex.isChecked(),
            case=self.case.isChecked(),
            zone=self.zone_box.text().strip(),
        )

    def _emit_request(self) -> None:
        if not self.query.text():
            self.set_result(None)
            return
        self.search_requested.emit(self.current_params())

    def focus(self) -> None:
        self.query.setFocus()
        self.query.selectAll()

    def begin_progress(self) -> None:
        self.summary.setText("")
        self.progress.setValue(0)
        self.progress.show()

    def set_progress(self, done: int, total: int) -> None:
        if not self.progress.isVisible():
            self.progress.show()
        self.progress.setValue(int((done / total) * 1000) if total else 0)
        self.summary.setText(f"Indexing {done} of {total} zones")

    def set_error(self, message: str) -> None:
        self.progress.hide()
        self.summary.setText(message)

    def set_result(self, result) -> None:
        """Populate the tree from a ``SearchResult`` (or clear it when ``None``)."""
        self.progress.hide()
        self.tree.clear()
        if result is None:
            self.summary.setText("")
            self.tree.hide()
            self.empty.show()
            return
        groups: dict[str, QTreeWidgetItem] = {}
        mono = theme.mono_font()
        dim = QColor(theme.current().text_dim)
        for hit in result.hits:
            parent = groups.get(hit.zone)
            if parent is None:
                parent = QTreeWidgetItem(self.tree, [hit.zone])
                parent.setForeground(0, QColor(theme.current().modified))
                parent.setFirstColumnSpanned(False)
                groups[hit.zone] = parent
            item = QTreeWidgetItem(
                parent,
                [hit.asset_name or f"<{hit.type_name}>", hit.type_name, hit.where, hit.snippet],
            )
            item.setFont(3, mono)
            item.setForeground(1, dim)
            item.setForeground(2, dim)
            item.setData(0, ROLE.UserRole, hit)
        for zone, parent in groups.items():
            n = parent.childCount()
            parent.setText(0, f"{zone}  ({n})")
        self.tree.expandAll()
        self.empty.setVisible(not result.hits)
        self.tree.setVisible(bool(result.hits))
        if result.truncated:
            self.summary.setText(
                f"showing {len(result.hits)} of {result.total} matches in "
                f"{len(groups)} zone{'s' if len(groups) != 1 else ''} "
                f"({result.seconds * 1000:.0f} ms)"
            )
        elif result.hits:
            self.summary.setText(
                f"{result.total} match{'es' if result.total != 1 else ''} in "
                f"{len(groups)} zone{'s' if len(groups) != 1 else ''} "
                f"({result.seconds * 1000:.0f} ms)"
            )
        else:
            self.summary.setText("no matches")

    def _activated(self, item, _column=0) -> None:
        hit = item.data(0, ROLE.UserRole)
        if hit is not None:
            self.open_hit.emit(hit)
