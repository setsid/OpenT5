"""The schema fields of any asset as a tree (``fields(ref)``, the schema's to_dict).

Read-only for now; ``set_field`` editing comes with opent5.edit. The model
builds child rows only when a branch is first expanded, so a GfxWorld (a few MB
of decoded fields) opens instantly.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QAbstractItemModel, QModelIndex, QSortFilterProxyModel, Qt
from PySide6.QtGui import QColor
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
from opent5.gui.views.base import AssetView, empty_state

ROLE = Qt.ItemDataRole
NO_INDEX = QModelIndex()


def describe(value: Any) -> tuple[str, str]:
    """(value text, type text) for one field value."""
    if isinstance(value, dict):
        kind = value.get("_kind")
        if set(value) == {"bytes"}:
            return f"{value['bytes']} bytes", "blob"
        return (kind or ""), f"{{{len(value)}}}"
    if isinstance(value, list):
        if value and all(isinstance(v, int | float) for v in value) and len(value) <= 16:
            return "  ".join(_scalar(v) for v in value), f"[{len(value)}]"
        return "", f"[{len(value)}]"
    if isinstance(value, bool):
        return str(value).lower(), "bool"
    if isinstance(value, int):
        return _scalar(value), "int"
    if isinstance(value, float):
        return _scalar(value), "float"
    if isinstance(value, str):
        return value, "str"
    if value is None:
        return "null", ""
    return str(value), type(value).__name__


def _scalar(v) -> str:
    if isinstance(v, float):
        return f"{v:.6g}"
    if isinstance(v, int) and not isinstance(v, bool) and (v >= 10 or v < 0):
        return f"{v}  0x{v & 0xFFFFFFFF:x}" if v < 0 else f"{v}  0x{v:x}"
    return str(v)


def _expandable(value: Any) -> bool:
    if isinstance(value, dict):
        return bool(value) and set(value) != {"bytes"}
    if isinstance(value, list):
        return bool(value) and not (
            all(isinstance(v, int | float) for v in value) and len(value) <= 16
        )
    return False


class _Node:
    __slots__ = ("key", "value", "parent", "row", "_children")

    def __init__(self, key: str, value: Any, parent: _Node | None, row: int):
        self.key, self.value, self.parent, self.row = key, value, parent, row
        self._children: list[_Node] | None = None

    @property
    def children(self) -> list[_Node]:
        if self._children is None:
            v = self.value
            if isinstance(v, dict):
                items = [(k, x) for k, x in v.items() if k != "_kind"]
            elif isinstance(v, list) and _expandable(v):
                items = [(f"[{i}]", x) for i, x in enumerate(v)]
            else:
                items = []
            self._children = [_Node(str(k), x, self, i) for i, (k, x) in enumerate(items)]
        return self._children


class FieldsModel(QAbstractItemModel):
    HEADERS = ("Field", "Value", "Type")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.root = _Node("", {}, None, 0)

    def set_data(self, value: Any) -> None:
        self.beginResetModel()
        self.root = _Node(
            "", value if isinstance(value, dict | list) else {"value": value}, None, 0
        )
        self.endResetModel()

    def node(self, index: QModelIndex) -> _Node:
        return index.internalPointer() if index.isValid() else self.root

    def index(self, row, column, parent=NO_INDEX):
        kids = self.node(parent).children
        if 0 <= row < len(kids):
            return self.createIndex(row, column, kids[row])
        return QModelIndex()

    def parent(self, index=NO_INDEX):  # noqa: A003 (Qt override)
        if not index.isValid():
            return QModelIndex()
        p = index.internalPointer().parent
        if p is None or p is self.root:
            return QModelIndex()
        return self.createIndex(p.row, 0, p)

    def rowCount(self, parent=NO_INDEX) -> int:  # noqa: N802
        if parent.column() > 0:
            return 0
        n = self.node(parent)
        if n is not self.root and not _expandable(n.value):
            return 0
        return len(n.children)

    def hasChildren(self, parent=NO_INDEX) -> bool:  # noqa: N802
        n = self.node(parent)
        return n is self.root or _expandable(n.value)

    def columnCount(self, parent=NO_INDEX) -> int:  # noqa: N802
        return 3

    def data(self, index, role=ROLE.DisplayRole):
        if not index.isValid():
            return None
        n = index.internalPointer()
        if role == ROLE.DisplayRole:
            if index.column() == 0:
                return n.key
            text, kind = describe(n.value)
            return text if index.column() == 1 else kind
        if role == ROLE.ForegroundRole:
            t = theme.current()
            if index.column() == 2:
                return QColor(t.text_faint)
            if index.column() == 0 and n.key.startswith("unk_"):
                return QColor(t.text_dim)
        if role == ROLE.ToolTipRole and index.column() == 1:
            text, _ = describe(n.value)
            return text if len(text) > 40 else None
        return None

    def headerData(self, section, orientation, role=ROLE.DisplayRole):  # noqa: N802
        if role == ROLE.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return self.HEADERS[section]
        return None


class FieldsView(AssetView):
    kind = "fields"
    title = "Fields"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.model = FieldsModel(self)
        self.proxy = QSortFilterProxyModel(self)
        self.proxy.setSourceModel(self.model)
        self.proxy.setRecursiveFilteringEnabled(True)
        self.proxy.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.tree = QTreeView()
        self.tree.setModel(self.proxy)
        self.tree.setFont(theme.mono_font())
        self.tree.setUniformRowHeights(True)
        self.tree.setIndentation(14)
        self.tree.setAlternatingRowColors(False)
        self.tree.header().setStretchLastSection(False)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.tree.setColumnWidth(0, 280)
        self.tree.setColumnWidth(2, 70)
        self.filter = QLineEdit(placeholderText="Filter loaded fields (expanded branches)")
        self.filter.textChanged.connect(self.proxy.setFilterFixedString)
        self.note = QLabel("read-only")
        bar = QWidget()
        bar.setObjectName("ViewBar")
        h = QHBoxLayout(bar)
        h.setContentsMargins(6, 3, 6, 3)
        h.setSpacing(6)
        h.addWidget(self.filter, 1)
        h.addWidget(self.note)
        self.empty = empty_state("")
        self.empty.hide()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(bar)
        lay.addWidget(self.tree, 1)
        lay.addWidget(self.empty, 1)

    def load(self, doc, ref) -> None:
        super().load(doc, ref)
        try:
            data = doc.fields(ref)
        except Exception as exc:  # any decoder failure is shown, not raised
            self.tree.hide()
            self.empty.setText(f"Fields could not be decoded: {exc}")
            self.empty.show()
            return
        self.empty.hide()
        self.tree.show()
        self.model.set_data(data)
        self.tree.expandToDepth(0)
        kind = data.get("_kind") if isinstance(data, dict) else None
        self.note.setText(f"{kind or ref.type_name} · read-only")
