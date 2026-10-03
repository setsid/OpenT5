"""The zone tree: asset types with counts, assets underneath, and a filter box."""

from __future__ import annotations

from PySide6.QtCore import (
    QAbstractItemModel,
    QModelIndex,
    QSortFilterProxyModel,
    Qt,
    Signal,
)
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from opent5.gui import theme
from opent5.gui.backend import Ref, ZoneDoc

ROLE = Qt.ItemDataRole
NO_INDEX = QModelIndex()
REF_ROLE = Qt.ItemDataRole.UserRole + 1


def human_size(n: int) -> str:
    if n <= 0:
        return ""
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} K"
    return f"{n / (1024 * 1024):.1f} M"


class _Group:
    __slots__ = ("type_name", "refs", "row")

    def __init__(self, type_name: str, refs: list[Ref], row: int):
        self.type_name, self.refs, self.row = type_name, refs, row


class AssetTreeModel(QAbstractItemModel):
    """Two levels: one row per asset type (name, count), the assets under it."""

    HEADERS = ("Asset", "Size")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.groups: list[_Group] = []
        self.edited: set = set()
        self._group_of: dict = {}

    def set_doc(self, doc: ZoneDoc | None) -> None:
        self.beginResetModel()
        self.groups = []
        if doc is not None:
            by_type: dict[str, list[Ref]] = {}
            for r in doc.all_refs:
                by_type.setdefault(r.type_name, []).append(r)
            for i, name in enumerate(sorted(by_type)):
                refs = sorted(by_type[name], key=lambda r: (r.inline, r.label.lower()))
                self.groups.append(_Group(name, refs, i))
        self._group_of = {}
        for g in self.groups:
            for row, r in enumerate(g.refs):
                self._group_of[r.key] = (g, row)
        self.edited = set(doc.edited_keys()) if doc is not None else set()
        self.endResetModel()

    def set_edited(self, keys: set) -> None:
        changed = keys ^ self.edited
        self.edited = set(keys)
        for key in changed:
            idx = self.index_of(key)
            if idx.isValid():
                self.dataChanged.emit(idx, idx.siblingAtColumn(1))
                parent = idx.parent()
                self.dataChanged.emit(parent, parent.siblingAtColumn(1))

    def index_of(self, key) -> QModelIndex:
        found = self._group_of.get(key)
        if found is None:
            return QModelIndex()
        g, row = found
        return self.createIndex(row, 0, g)

    def count(self) -> int:
        return sum(len(g.refs) for g in self.groups)

    # Qt model
    def index(self, row, column, parent=NO_INDEX):
        if not parent.isValid():
            if 0 <= row < len(self.groups):
                return self.createIndex(row, column, None)
            return QModelIndex()
        if parent.internalPointer() is not None:
            return QModelIndex()
        g = self.groups[parent.row()]
        if 0 <= row < len(g.refs):
            return self.createIndex(row, column, g)
        return QModelIndex()

    def parent(self, index=NO_INDEX):  # noqa: A003
        if not index.isValid():
            return QModelIndex()
        g = index.internalPointer()
        if g is None:
            return QModelIndex()
        return self.createIndex(g.row, 0, None)

    def rowCount(self, parent=NO_INDEX) -> int:  # noqa: N802
        if not parent.isValid():
            return len(self.groups)
        if parent.internalPointer() is None and parent.column() == 0:
            return len(self.groups[parent.row()].refs)
        return 0

    def columnCount(self, parent=NO_INDEX) -> int:  # noqa: N802
        return 2

    def ref(self, index: QModelIndex) -> Ref | None:
        if not index.isValid() or index.internalPointer() is None:
            return None
        return index.internalPointer().refs[index.row()]

    def data(self, index, role=ROLE.DisplayRole):
        if not index.isValid():
            return None
        t = theme.current()
        g = index.internalPointer()
        if g is None:  # a type row
            group = self.groups[index.row()]
            edited = any(r.key in self.edited for r in group.refs)
            if role == ROLE.DisplayRole:
                if index.column() == 0:
                    return ("* " if edited else "") + group.type_name
                return str(len(group.refs))
            if role == ROLE.ForegroundRole:
                if edited and index.column() == 0:
                    return QColor(t.modified)
                return QColor(t.text_dim if index.column() == 1 else t.text)
            if role == ROLE.FontRole and index.column() == 0:
                f = QFont()
                f.setWeight(QFont.Weight.DemiBold)
                return f
            if role == ROLE.TextAlignmentRole and index.column() == 1:
                return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            return None
        r = g.refs[index.row()]
        edited = r.key in self.edited
        if role == ROLE.DisplayRole:
            if index.column() == 0:
                return ("* " if edited else "") + r.label
            return "inline" if r.inline else human_size(r.size)
        if role == REF_ROLE:
            return r
        if role == ROLE.ToolTipRole and index.column() == 0:
            where = "loaded inside another asset" if r.inline else f"asset {r.key}"
            off = f", zone offset 0x{r.offset:x}" if r.offset is not None else ""
            return f"{r.label}\n{r.type_name}, {where}{off}"
        if role == ROLE.ForegroundRole:
            if edited and index.column() == 0:
                return QColor(t.modified)
            if index.column() == 1 or r.inline:
                return QColor(t.text_faint if index.column() == 1 else t.text_dim)
        if role == ROLE.TextAlignmentRole and index.column() == 1:
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        return None

    def headerData(self, section, orientation, role=ROLE.DisplayRole):  # noqa: N802
        if role == ROLE.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return self.HEADERS[section]
        return None


class AssetFilter(QSortFilterProxyModel):
    """Keeps assets whose name contains every word typed; a type row stays when its
    name matches (all its assets shown) or any of its assets does."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.words: list[str] = []
        self.setRecursiveFilteringEnabled(True)

    def set_text(self, text: str) -> None:
        self.words = text.lower().split()
        self.invalidateFilter()

    def filterAcceptsRow(self, row, parent) -> bool:  # noqa: N802
        if not self.words:
            return True
        model: AssetTreeModel = self.sourceModel()
        if not parent.isValid():
            name = model.groups[row].type_name
            return all(w in name for w in self.words)
        g = model.groups[parent.row()]
        name = (g.refs[row].label + " " + g.type_name).lower()
        return all(w in name for w in self.words)


class AssetTree(QWidget):
    """The left panel: a filter box over the tree."""

    activated = Signal(object)  # Ref

    def __init__(self, parent=None):
        super().__init__(parent)
        self.model = AssetTreeModel(self)
        self.proxy = AssetFilter(self)
        self.proxy.setSourceModel(self.model)
        self.filter = QLineEdit(placeholderText="Filter assets")
        self.filter.setClearButtonEnabled(True)
        self.filter.textChanged.connect(self._filter)
        self.count = QLabel("")
        header = QWidget()
        header.setObjectName("PanelHeader")
        h = QHBoxLayout(header)
        h.setContentsMargins(4, 4, 6, 4)
        h.setSpacing(6)
        h.addWidget(self.filter, 1)
        h.addWidget(self.count)
        self.view = QTreeView()
        self.view.setModel(self.proxy)
        self.view.setUniformRowHeights(True)
        self.view.setIndentation(12)
        self.view.setRootIsDecorated(True)
        self.view.setHeaderHidden(False)
        self.view.header().setStretchLastSection(False)
        self.view.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.view.header().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self.view.setColumnWidth(1, 58)
        self.view.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.view.selectionModel().currentChanged.connect(self._current)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(header)
        lay.addWidget(self.view, 1)

    def set_doc(self, doc: ZoneDoc) -> None:
        self.model.set_doc(doc)
        self._update_count()

    def _filter(self, text: str) -> None:
        self.proxy.set_text(text)
        if text:
            self.view.expandAll()
        self._update_count()

    def _update_count(self) -> None:
        total = self.model.count()
        if not self.proxy.words:
            self.count.setText(f"{total}")
            return
        shown = 0
        for row in range(self.proxy.rowCount()):
            shown += self.proxy.rowCount(self.proxy.index(row, 0))
        self.count.setText(f"{shown} / {total}")

    def _current(self, index, _prev) -> None:
        ref = self.model.ref(self.proxy.mapToSource(index))
        if ref is not None:
            self.activated.emit(ref)

    def select(self, ref: Ref) -> None:
        src = self.model.index_of(ref.key)
        idx = self.proxy.mapFromSource(src)
        if not idx.isValid():
            self.filter.clear()
            idx = self.proxy.mapFromSource(src)
        self.view.expand(idx.parent())
        self.view.setCurrentIndex(idx)
        self.view.scrollTo(idx)

    def current_ref(self) -> Ref | None:
        return self.model.ref(self.proxy.mapToSource(self.view.currentIndex()))
