"""The schema fields of any asset as a tree (``fields(ref)``, the schema's to_dict).

The model builds child rows only when a branch is first expanded, so a GfxWorld (a few MB
of decoded fields) opens instantly.

Scalar fields of the asset's own struct and of its sub-structs (``"key:field"``,
``"key[i]:field"``) are editable through ``set_field``: integers (decimal or 0x hex, range
checked for the field's type), floats, vectors as space-separated components, enums by
name or number, flags as numbers. Pointers and counts change layout and stay read-only;
they carry a lock and say why. Fields of nodes nested in the asset (a material inside a
model, a pass inside a technique) are edited the same way, through ``"child/"`` steps.
"""

from __future__ import annotations

import math
import re
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

from opent5.gui import icons, theme
from opent5.gui.backend import EditError
from opent5.gui.views.base import AssetView, empty_state
from opent5.xfile.schema import SCALARS, parse_type

#: Integer ranges by schema type.
INT_RANGES = {
    "u8": (0, 0xFF),
    "s8": (-0x80, 0x7F),
    "u16": (0, 0xFFFF),
    "s16": (-0x8000, 0x7FFF),
    "u32": (0, 0xFFFFFFFF),
    "s32": (-0x80000000, 0x7FFFFFFF),
    "u64": (0, 0xFFFFFFFFFFFFFFFF),
    "s64": (-0x8000000000000000, 0x7FFFFFFFFFFFFFFF),
    "cmp": (0, 0xFFFFFFFF),
}
FLOAT_MAX = {"f16": 65504.0, "f32": 3.4028234663852886e38, "f64": 1.7976931348623157e308}
_SPLIT = re.compile(r"[\s,]+")


class FieldInputError(ValueError):
    """Input that does not fit a field; the message says what was expected."""


def _int(text: str, kind: str, names: dict | None) -> int:
    t = text.strip()
    if names:
        for value, name in names.items():
            if str(name).lower() == t.lower():
                return int(value)
    try:
        v = int(t, 0)
    except ValueError:
        choices = f" or one of {', '.join(map(str, names.values()))}" if names else ""
        raise FieldInputError(
            f"expected an integer (decimal or 0x hex){choices}, found {t!r}"
        ) from None
    lo, hi = INT_RANGES.get(kind, (None, None))
    if lo is not None and not lo <= v <= hi:
        raise FieldInputError(f"expected {kind} in {lo}..{hi}, found {v}")
    return v


def _float(text: str, kind: str) -> float:
    try:
        v = float(text.strip())
    except ValueError:
        raise FieldInputError(f"expected a number, found {text.strip()!r}") from None
    limit = FLOAT_MAX.get(kind, FLOAT_MAX["f32"])
    if math.isfinite(v) and abs(v) > limit:
        raise FieldInputError(f"expected {kind} within +-{limit:g}, found {v:g}")
    return v


def parse_input(text: str, info: dict) -> Any:
    """The value ``set_field`` takes for ``text`` typed into a field described by
    ``field_info``: an int, a float, or a list for vectors and arrays. Raises
    FieldInputError saying what was expected."""
    kind, count = parse_type(info["type"])
    if kind in ("char", "bytes"):
        raise FieldInputError(f"{info['type']} fields are not edited here")
    fmt = SCALARS[kind][0]
    per = int(fmt[:-1]) if fmt[:-1].isdigit() else 1  # vec3 -> 3 floats
    is_float = fmt[-1] in "efd"
    total = per * count
    parts = [p for p in _SPLIT.split(text.strip().strip("[]()")) if p]
    if total == 1:
        if len(parts) != 1:
            raise FieldInputError(f"expected one value, found {len(parts)}")
        return _float(parts[0], kind) if is_float else _int(parts[0], kind, info.get("names"))
    if len(parts) != total:
        raise FieldInputError(f"expected {total} values for {info['type']}, found {len(parts)}")
    values = [_float(p, kind) if is_float else _int(p, kind, None) for p in parts]
    if per > 1:
        groups = [values[i : i + per] for i in range(0, total, per)]
        return groups[0] if count == 1 else groups
    return values


def format_value(value: Any) -> str:
    """The text an editor starts from: plain numbers, space-separated for vectors."""
    if isinstance(value, list | tuple):
        return " ".join(format_value(v) for v in value)
    if isinstance(value, float):
        return repr(value)
    return str(value)


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


def field_path(n: _Node) -> str | None:
    """The ``set_field`` path of a leaf: "name" in a node's own struct (under "header" or
    "raw"), "key:name" or "key[i]:name" in a sub-struct, after one "child/" or
    "child[i]/" step for each nested node on the way (a material inside a model); None
    for anything else."""
    chain = []
    while n is not None and n.parent is not None:
        chain.append(n)
        n = n.parent
    chain.reverse()
    if not chain or isinstance(chain[-1].value, dict):
        return None
    if isinstance(chain[-1].value, list) and _expandable(chain[-1].value):
        return None
    steps: list[str] = []
    rest: list[_Node] = []
    for c in chain[:-1]:
        rest.append(c)
        if isinstance(c.value, dict) and "_kind" in c.value:
            if len(rest) == 1:
                steps.append(rest[0].key)
            elif len(rest) == 2 and isinstance(rest[0].value, list) and rest[1].key.startswith("["):
                steps.append(rest[0].key + rest[1].key)
            else:
                return None
            rest = []
    rest.append(chain[-1])
    first = rest[0]
    if len(rest) == 2 and not rest[1].key.startswith("["):
        field = rest[1].key if first.key in ("header", "raw") else f"{first.key}:{rest[1].key}"
    elif (
        len(rest) == 3
        and isinstance(first.value, list)
        and rest[1].key.startswith("[")
        and isinstance(rest[1].value, dict)
    ):
        field = f"{first.key}{rest[1].key}:{rest[2].key}"
    else:
        return None
    return "/".join([*steps, field])


class FieldsModel(QAbstractItemModel):
    HEADERS = ("Field", "Value", "Type")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.root = _Node("", {}, None, 0)
        self.doc = None
        self.ref = None
        self.error: str | None = None
        self._info: dict[int, tuple[str | None, dict | None, str | None]] = {}
        #: set_field paths edited in this asset (drawn in the modified colour)
        self.edited: set[str] = set()

    def set_data(self, value: Any, doc=None, ref=None) -> None:
        self.beginResetModel()
        self.doc, self.ref = doc, ref
        self._info = {}
        self._read_edited()
        self.root = _Node(
            "", value if isinstance(value, dict | list) else {"value": value}, None, 0
        )
        self.endResetModel()

    def info(self, n: _Node) -> tuple[str | None, dict | None, str | None]:
        """(path, field_info, why not editable) for a leaf, cached."""
        key = id(n)
        if key not in self._info:
            path = field_path(n) if self.doc is not None and self.ref is not None else None
            info, why = None, None
            if path is None:
                why = None
            elif self.ref.inline and not getattr(self.doc, "native", True):
                why = "inline asset: editing needs opent5.edit"
            else:
                try:
                    info = self.doc.field_info(self.ref, path)
                    why = info.get("reason")
                except EditError:
                    path = None  # not a field of a struct of this asset (a reference, say)
            self._info[key] = (path, info, why)
        return self._info[key]

    def _read_edited(self) -> None:
        self.edited = set()
        if self.doc is None or self.ref is None:
            return
        for c in self.doc.changes():
            if c.kind == "field" and c.key == self.ref.key:
                self.edited.add(c.detail)

    def editable(self, n: _Node) -> bool:
        path, info, why = self.info(n)
        return path is not None and info is not None and not why

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
            if index.column() == 1 and self.edited and self.info(n)[0] in self.edited:
                return QColor(t.modified)
            if index.column() == 2:
                return QColor(t.text_faint)
            if index.column() == 0 and n.key.startswith("unk_"):
                return QColor(t.text_dim)
        if index.column() == 1 and role in (ROLE.DecorationRole, ROLE.ToolTipRole, ROLE.EditRole):
            path, info, why = self.info(n)
            if role == ROLE.EditRole:
                return format_value(n.value)
            if role == ROLE.DecorationRole:
                return icons.icon("lock") if path is not None and why else None
            text, _ = describe(n.value)
            if path is not None and why:
                return f"Read-only: {why}"
            if path is not None and info is not None:
                kind = info["type"] + (f" ({info['role']})" if info.get("role") else "")
                names = info.get("names")
                extra = f"; names: {', '.join(map(str, names.values()))}" if names else ""
                return f"{path}: {kind}{extra}. Double-click or F2 to edit."
            return text if len(text) > 40 else None
        if role == ROLE.ToolTipRole and index.column() == 1:
            text, _ = describe(n.value)
            return text if len(text) > 40 else None
        return None

    def flags(self, index):
        base = Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled
        if index.isValid() and index.column() == 1 and self.editable(index.internalPointer()):
            base |= Qt.ItemFlag.ItemIsEditable
        return base

    def setData(self, index, value, role=ROLE.EditRole) -> bool:  # noqa: N802
        if role != ROLE.EditRole or not index.isValid() or index.column() != 1:
            return False
        n = index.internalPointer()
        path, info, _ = self.info(n)
        if path is None or info is None:
            return False
        try:
            parsed = parse_input(str(value), info)
        except FieldInputError as exc:
            self.error = f"{path}: {exc}"
            return False
        try:
            self.doc.set_field(self.ref, path, parsed)
            now = self.doc.field_info(self.ref, path)
        except EditError as exc:
            self.error = f"{path}: {exc}"
            return False
        self.error = None
        self._read_edited()
        n.value = _plain(now["value"])
        self._info.pop(id(n), None)
        self.dataChanged.emit(index, index.siblingAtColumn(2))
        return True

    def headerData(self, section, orientation, role=ROLE.DisplayRole):  # noqa: N802
        if role == ROLE.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return self.HEADERS[section]
        return None


def _plain(value: Any) -> Any:
    """A decoded value in the shape the tree shows (tuples as lists)."""
    if isinstance(value, list | tuple):
        return [_plain(v) for v in value]
    return value


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
        self.tree.setEditTriggers(
            QTreeView.EditTrigger.DoubleClicked | QTreeView.EditTrigger.EditKeyPressed
        )
        self.model.dataChanged.connect(self._changed)
        self.error = QLabel()
        self.error.setObjectName("FieldError")
        self.error.setWordWrap(True)
        self.error.hide()
        self.note = QLabel("")
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
        lay.addWidget(self.error)
        self._editing_delegate()

    def _editing_delegate(self) -> None:
        """Report refused input under the tree (the model keeps the reason)."""
        delegate = self.tree.itemDelegate()

        def closed(*_args) -> None:
            if self.model.error:
                self.error.setText(f"Not changed: {self.model.error}")
                self.error.show()
                self.status.emit(f"field edit refused: {self.model.error}")
                self.model.error = None

        delegate.closeEditor.connect(closed)

    def _changed(self, *_args) -> None:
        self.error.hide()
        self.edited.emit()

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
        self.error.hide()
        self.tree.show()
        self.model.set_data(data, doc, ref)
        self.tree.expandToDepth(0)
        kind = data.get("_kind") if isinstance(data, dict) else None
        can = getattr(doc, "native", False) and hasattr(doc, "field_info")
        what = "scalar fields editable, locked ones say why" if can else "read-only"
        self.note.setText(f"{kind or ref.type_name} · {what}")

    def refresh(self) -> None:
        """After undo or redo: re-read the fields, keeping the expanded branches."""
        if self.doc is None or self.ref is None:
            return
        expanded = self._expanded_paths()
        self.load(self.doc, self.ref)
        self._expand_paths(expanded)

    def _expanded_paths(self) -> set[tuple[str, ...]]:
        out: set[tuple[str, ...]] = set()

        def walk(parent) -> None:
            for row in range(self.proxy.rowCount(parent)):
                idx = self.proxy.index(row, 0, parent)
                if self.tree.isExpanded(idx):
                    n = self.model.node(self.proxy.mapToSource(idx))
                    chain = []
                    while n is not None and n.parent is not None:
                        chain.append(n.key)
                        n = n.parent
                    out.add(tuple(reversed(chain)))
                    walk(idx)

        walk(QModelIndex())
        return out

    def _expand_paths(self, paths: set[tuple[str, ...]]) -> None:
        def walk(parent, prefix) -> None:
            for row in range(self.proxy.rowCount(parent)):
                idx = self.proxy.index(row, 0, parent)
                key = (*prefix, idx.data())
                if key in paths:
                    self.tree.expand(idx)
                    walk(idx, key)

        walk(QModelIndex(), ())
