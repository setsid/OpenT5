"""Command palette (Ctrl+Shift+P) and quick open (Ctrl+P): one popup, two sources.

Items are (label, hint, payload). The filter is a fuzzy subsequence match:
every typed character must appear in order; contiguous runs, word starts and
an early first match score higher.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import QAbstractListModel, QModelIndex, QRect, QSize, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QFrame,
    QLineEdit,
    QListView,
    QStyle,
    QStyledItemDelegate,
    QVBoxLayout,
    QWidget,
)

from opent5.gui import theme

ROLE = Qt.ItemDataRole
NO_INDEX = QModelIndex()
MAX_SHOWN = 400


@dataclass
class Item:
    label: str
    hint: str
    payload: Any
    enabled: bool = True


def fuzzy_score(query: str, text: str) -> int | None:
    """None when ``query`` is not a subsequence of ``text`` (case-insensitive);
    otherwise a score, higher is better."""
    if not query:
        return 0
    q, t = query.lower(), text.lower()
    if q in t:
        at = t.index(q)
        start_bonus = 40 if at == 0 or not t[at - 1].isalnum() else 0
        return 1000 - at + start_bonus + 5 * len(q) - len(t) // 8
    score, pos, run = 0, 0, 0
    for ch in q:
        found = t.find(ch, pos)
        if found < 0:
            return None
        if found == pos:
            run += 1
            score += 6 * run
        else:
            run = 0
            score -= min(found - pos, 10)
        if found == 0 or not t[found - 1].isalnum():
            score += 8
        pos = found + 1
    return score - len(t) // 8


def rank(query: str, items: list[Item]) -> list[Item]:
    scored = []
    for i, item in enumerate(items):
        s = fuzzy_score(query, item.label)
        if s is None and item.hint:
            s2 = fuzzy_score(query, f"{item.label} {item.hint}")
            s = None if s2 is None else s2 - 50
        if s is not None:
            scored.append((-s if item.enabled else 10**6 - s, i, item))
    scored.sort(key=lambda x: (x[0], x[1]) if query else (not x[2].enabled, x[1]))
    return [item for _s, _i, item in scored]


class _Model(QAbstractListModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.items: list[Item] = []

    def set_items(self, items: list[Item]) -> None:
        self.beginResetModel()
        self.items = items[:MAX_SHOWN]
        self.endResetModel()

    def rowCount(self, parent=NO_INDEX) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.items)

    def data(self, index, role=ROLE.DisplayRole):
        if not index.isValid():
            return None
        item = self.items[index.row()]
        if role == ROLE.DisplayRole:
            return item.label
        if role == ROLE.UserRole:
            return item
        return None


class _Delegate(QStyledItemDelegate):
    """Label on the left, hint (shortcut or type) right-aligned in the mono font."""

    def paint(self, p: QPainter, option, index) -> None:
        t = theme.current()
        item: Item = index.data(ROLE.UserRole)
        r = option.rect
        if option.state & QStyle.StateFlag.State_Selected:
            p.fillRect(r, QColor(t.selection))
            p.fillRect(QRect(r.left(), r.top(), 2, r.height()), QColor(t.accent))
        p.setPen(QColor(t.text if item.enabled else t.text_faint))
        p.setFont(option.font)
        inner = r.adjusted(12, 0, -10, 0)
        mono = theme.mono_font()
        hint_w = 0
        if item.hint:
            p.save()
            p.setFont(mono)
            p.setPen(QColor(t.text_dim))
            hint_w = p.fontMetrics().horizontalAdvance(item.hint) + 16
            p.drawText(
                inner, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, item.hint
            )
            p.restore()
        label_rect = inner.adjusted(0, 0, -hint_w, 0)
        text = option.fontMetrics.elidedText(
            item.label, Qt.TextElideMode.ElideMiddle, label_rect.width()
        )
        p.drawText(label_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, text)

    def sizeHint(self, option, index) -> QSize:  # noqa: N802
        return QSize(100, option.fontMetrics.height() + 8)


class Palette(QFrame):
    """A popup list with a filter line, placed at the top centre of its parent."""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setObjectName("Palette")
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.line = QLineEdit()
        self.list = QListView()
        self.list.setItemDelegate(_Delegate(self.list))
        self.list.setUniformItemSizes(True)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.model = _Model(self)
        self.list.setModel(self.model)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(1, 1, 1, 1)
        lay.setSpacing(0)
        lay.addWidget(self.line)
        lay.addWidget(self.list)
        self.items: list[Item] = []
        self.on_accept: Callable[[Any], None] | None = None
        self.line.textChanged.connect(self._filter)
        self.line.returnPressed.connect(self.accept)
        self.list.activated.connect(lambda _i: self.accept())
        self.line.installEventFilter(self)
        self.hide()

    def open(
        self, items: list[Item], placeholder: str, on_accept: Callable[[Any], None], text: str = ""
    ) -> None:
        self.items = items
        self.on_accept = on_accept
        self.line.setPlaceholderText(placeholder)
        self.line.setText(text)
        self._filter(text)
        self._place()
        self.show()
        self.raise_()
        self.line.setFocus()

    def _place(self) -> None:
        parent = self.parentWidget()
        width = min(640, max(420, parent.width() - 80))
        rows = min(14, max(1, self.model.rowCount()))
        height = self.line.sizeHint().height() + rows * (self.fontMetrics().height() + 8) + 4
        self.setGeometry((parent.width() - width) // 2, 40, width, height)

    def _filter(self, text: str) -> None:
        self.model.set_items(rank(text, self.items))
        if self.model.rowCount():
            self.list.setCurrentIndex(self.model.index(0))
        if self.isVisible():
            self._place()

    def accept(self) -> None:
        idx = self.list.currentIndex()
        if not idx.isValid():
            return
        item: Item = idx.data(ROLE.UserRole)
        if not item.enabled:
            return
        self.hide()
        if self.on_accept is not None:
            self.on_accept(item.payload)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if obj is self.line and event.type() == event.Type.KeyPress:
            key = event.key()
            if key == Qt.Key.Key_Escape:
                self.hide()
                self.parentWidget().setFocus()
                return True
            if key in (Qt.Key.Key_Down, Qt.Key.Key_Up, Qt.Key.Key_PageDown, Qt.Key.Key_PageUp):
                n = self.model.rowCount()
                if n:
                    row = self.list.currentIndex().row()
                    step = {
                        Qt.Key.Key_Down: 1,
                        Qt.Key.Key_Up: -1,
                        Qt.Key.Key_PageDown: 10,
                        Qt.Key.Key_PageUp: -10,
                    }[key]
                    self.list.setCurrentIndex(self.model.index(max(0, min(n - 1, row + step))))
                return True
        return super().eventFilter(obj, event)

    def focusOutEvent(self, event) -> None:  # noqa: N802
        super().focusOutEvent(event)
