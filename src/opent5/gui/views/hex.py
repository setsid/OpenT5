"""Hex view: offset, 16 bytes in two groups of 8, ASCII. Paints only visible rows."""

from __future__ import annotations

import struct

from PySide6.QtCore import QPoint, QRectF, Qt
from PySide6.QtGui import QColor, QFontMetricsF, QKeySequence, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractScrollArea,
    QApplication,
    QInputDialog,
    QStackedLayout,
    QWidget,
)

from opent5.gui import theme
from opent5.gui.backend import Ref, ZoneDoc
from opent5.gui.views.base import AssetView, empty_state

ROW = 16
INLINE_NOTE = "This asset is loaded inside another asset and has no byte span of its own."


class HexModel:
    """Pure data side of the hex view: rows of 16 bytes over ``data``, with
    displayed offsets starting at ``base``."""

    def __init__(self, data: bytes, base: int = 0):
        self.data = data if isinstance(data, bytes | bytearray | memoryview) else bytes(data)
        self.base = base

    def __len__(self) -> int:
        return len(self.data)

    @property
    def row_count(self) -> int:
        return (len(self.data) + ROW - 1) // ROW

    def row_bytes(self, row: int) -> bytes:
        return bytes(self.data[row * ROW : row * ROW + ROW])

    def offset_of(self, row: int, col: int) -> int:
        """Index into data of (row, column)."""
        return row * ROW + col

    def display_offset(self, index: int, relative: bool = False) -> int:
        return index if relative else self.base + index

    @staticmethod
    def ascii_char(b: int) -> str:
        return chr(b) if 0x20 <= b < 0x7F else "."

    def hex_text(self, row: int) -> str:
        b = self.row_bytes(row)
        left = " ".join(f"{x:02x}" for x in b[:8])
        right = " ".join(f"{x:02x}" for x in b[8:])
        return f"{left}  {right}".rstrip()

    def ascii_text(self, row: int) -> str:
        return "".join(self.ascii_char(x) for x in self.row_bytes(row))

    def text(self, row: int, relative: bool = False) -> str:
        off = self.display_offset(row * ROW, relative)
        return f"{off:08x}  {self.hex_text(row):<48}  {self.ascii_text(row)}"

    def find(self, needle: bytes, start: int = 0) -> int:
        if not needle:
            return -1
        if isinstance(self.data, memoryview):
            return bytes(self.data).find(needle, start)
        return self.data.find(needle, start)

    def inspect(self, index: int) -> str:
        """Short data inspector string for the bytes at ``index``."""
        if not 0 <= index < len(self.data):
            return ""
        chunk = bytes(self.data[index : index + 4])
        parts = [f"u8 {chunk[0]}"]
        if len(chunk) >= 2:
            parts.append(f"u16 BE {struct.unpack('>H', chunk[:2])[0]}")
        if len(chunk) == 4:
            u32 = struct.unpack(">I", chunk)[0]
            f32 = struct.unpack(">f", chunk)[0]
            parts.append(f"u32 BE {u32:#010x}")
            parts.append(f"f32 BE {f32:.6g}")
        return " · ".join(parts)


class HexArea(QAbstractScrollArea):
    """The painted, virtualised byte grid."""

    def __init__(self, view: HexView):
        super().__init__(view)
        self.view = view
        self.model = HexModel(b"")
        self.relative = False
        self.cursor = 0
        self.anchor = 0
        self.drag_ascii = False
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setFrameShape(QAbstractScrollArea.Shape.NoFrame)
        self.viewport().setCursor(Qt.CursorShape.IBeamCursor)
        self.set_font()
        theme.on_change(lambda _t: self._theme_changed(), self)

    def _theme_changed(self) -> None:
        self.viewport().update()

    # geometry
    def set_font(self) -> None:
        self.font_ = theme.mono_font()
        fm = QFontMetricsF(self.font_)
        self.cw = fm.horizontalAdvance("0")
        self.lh = int(fm.height() + 2)
        self.ascent = fm.ascent()
        pad = self.cw
        self.x_off = pad
        self.x_hex = self.x_off + 8 * self.cw + 2 * pad
        self.x_ascii = self.x_hex + (16 * 3 + 1) * self.cw + 2 * pad
        self.x_end = self.x_ascii + 16 * self.cw + pad

    def byte_x(self, col: int) -> float:
        return self.x_hex + (col * 3 + (1 if col >= 8 else 0)) * self.cw

    def set_model(self, model: HexModel) -> None:
        self.model = model
        self.cursor = self.anchor = 0
        self.verticalScrollBar().setValue(0)
        self.update_scroll()
        self.viewport().update()

    def visible_rows(self) -> int:
        return max(1, self.viewport().height() // self.lh)

    def update_scroll(self) -> None:
        vs = self.verticalScrollBar()
        vs.setRange(0, max(0, self.model.row_count - self.visible_rows() + 1))
        vs.setPageStep(self.visible_rows())
        hs = self.horizontalScrollBar()
        hs.setRange(0, max(0, int(self.x_end) - self.viewport().width()))
        hs.setPageStep(self.viewport().width())

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.update_scroll()

    def selection(self) -> tuple[int, int]:
        """Inclusive start, exclusive end."""
        a, b = sorted((self.anchor, self.cursor))
        return a, min(b + 1, len(self.model))

    def paintEvent(self, event) -> None:
        t = theme.current()
        p = QPainter(self.viewport())
        p.setFont(self.font_)
        vp = self.viewport().rect()
        p.fillRect(vp, QColor(t.base))
        dx = -self.horizontalScrollBar().value()
        p.translate(dx, 0)
        first = self.verticalScrollBar().value()
        rows = self.visible_rows() + 1
        sel_a, sel_b = self.selection()
        has_sel = sel_b - sel_a > 1
        c_text, c_zero, c_off = QColor(t.text), QColor(t.hex_zero), QColor(t.hex_offset)
        c_sel, c_acc = QColor(t.selection), QColor(t.accent)
        cw, lh = self.cw, self.lh
        p.setPen(QPen(QColor(t.border), 1))
        for x in (self.x_hex - cw, self.x_ascii - cw):
            p.drawLine(int(x), 0, int(x), vp.height())
        p.drawLine(int(self.x_end), 0, int(self.x_end), vp.height())
        n = len(self.model)
        for i in range(rows):
            row = first + i
            if row >= self.model.row_count:
                break
            y = i * lh
            base = row * ROW
            data = self.model.row_bytes(row)
            if has_sel and sel_a < base + ROW and sel_b > base:
                for col in range(len(data)):
                    idx = base + col
                    if sel_a <= idx < sel_b:
                        p.fillRect(QRectF(self.byte_x(col) - cw / 2, y, cw * 3, lh), c_sel)
                        p.fillRect(QRectF(self.x_ascii + col * cw, y, cw, lh), c_sel)
            p.setPen(c_off)
            off = self.model.display_offset(base, self.relative)
            p.drawText(int(self.x_off), int(y + 1 + self.ascent), f"{off:08x}")
            for col, b in enumerate(data):
                p.setPen(c_zero if b == 0 else c_text)
                p.drawText(int(self.byte_x(col)), int(y + 1 + self.ascent), f"{b:02x}")
                p.drawText(
                    int(self.x_ascii + col * cw),
                    int(y + 1 + self.ascent),
                    HexModel.ascii_char(b),
                )
            if base <= self.cursor < base + ROW and self.cursor < n:
                col = self.cursor - base
                p.setPen(QPen(c_acc, 1))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRect(int(self.byte_x(col) - 2), y, int(cw * 2 + 3), lh - 1)
                p.drawRect(int(self.x_ascii + col * cw), y, int(cw), lh - 1)
        p.end()

    # interaction
    def hit(self, pos: QPoint) -> tuple[int, bool] | None:
        x = pos.x() + self.horizontalScrollBar().value()
        row = self.verticalScrollBar().value() + pos.y() // self.lh
        if x >= self.x_ascii - self.cw / 2:
            col = int((x - self.x_ascii) // self.cw)
            is_ascii = True
        else:
            rel = (x - self.x_hex) / self.cw
            if rel >= 24:
                rel -= 1
            col = int((rel + 0.5) // 3)
            is_ascii = False
        col = max(0, min(15, col))
        idx = min(self.model.offset_of(max(0, row), col), max(0, len(self.model) - 1))
        return idx, is_ascii

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton or not len(self.model):
            return
        idx, self.drag_ascii = self.hit(event.position().toPoint())
        self.cursor = idx
        if not event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            self.anchor = idx
        self.changed()

    def mouseMoveEvent(self, event) -> None:
        if event.buttons() & Qt.MouseButton.LeftButton and len(self.model):
            self.cursor = self.hit(event.position().toPoint())[0]
            self.changed()

    def wheelEvent(self, event) -> None:
        steps = event.angleDelta().y() // 40
        vs = self.verticalScrollBar()
        vs.setValue(vs.value() - steps)

    def keyPressEvent(self, event) -> None:
        if event.matches(QKeySequence.StandardKey.Copy):
            self.copy()
            return
        if event.key() == Qt.Key.Key_G and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.view.goto_dialog()
            return
        n = len(self.model)
        if not n:
            return
        key, mods = event.key(), event.modifiers()
        ctrl = mods & Qt.KeyboardModifier.ControlModifier
        page = self.visible_rows() * ROW
        moves = {
            Qt.Key.Key_Left: -1,
            Qt.Key.Key_Right: 1,
            Qt.Key.Key_Up: -ROW,
            Qt.Key.Key_Down: ROW,
            Qt.Key.Key_PageUp: -page,
            Qt.Key.Key_PageDown: page,
        }
        c = self.cursor
        if key in moves:
            c += moves[key]
        elif key == Qt.Key.Key_Home:
            c = 0 if ctrl else c - c % ROW
        elif key == Qt.Key.Key_End:
            c = n - 1 if ctrl else c - c % ROW + ROW - 1
        else:
            super().keyPressEvent(event)
            return
        self.cursor = max(0, min(n - 1, c))
        if not mods & Qt.KeyboardModifier.ShiftModifier:
            self.anchor = self.cursor
        self.changed()

    def ensure_visible(self) -> None:
        row = self.cursor // ROW
        vs = self.verticalScrollBar()
        vis = self.visible_rows()
        if row < vs.value():
            vs.setValue(row)
        elif row >= vs.value() + vis:
            vs.setValue(row - vis + 1)

    def goto(self, index: int) -> None:
        if not len(self.model):
            return
        self.cursor = self.anchor = max(0, min(len(self.model) - 1, index))
        vs = self.verticalScrollBar()
        vs.setValue(max(0, self.cursor // ROW - self.visible_rows() // 3))
        self.changed()

    def changed(self) -> None:
        self.ensure_visible()
        self.viewport().update()
        self.view.status.emit(self.status_text())

    def status_text(self) -> str:
        if not len(self.model):
            return ""
        a, b = self.selection()
        off = self.model.display_offset
        if b - a > 1:
            sel = f"sel {off(a, self.relative):#x}-{off(b, self.relative):#x} ({b - a} bytes)"
        else:
            sel = f"offset {off(self.cursor, self.relative):#x}"
        return f"{sel} · {self.model.inspect(self.cursor)}"

    def copy(self) -> None:
        a, b = self.selection()
        if b > a:
            QApplication.clipboard().setText(bytes(self.model.data[a:b]).hex(" "))


class HexView(AssetView):
    kind = "hex"
    title = "Hex"

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.stack = QStackedLayout(self)
        self.area = HexArea(self)
        self.empty = empty_state(INLINE_NOTE, self)
        self.stack.addWidget(self.area)
        self.stack.addWidget(self.empty)

    @property
    def model(self) -> HexModel:
        return self.area.model

    def load(self, doc: ZoneDoc, ref: Ref) -> None:
        super().load(doc, ref)
        data = doc.raw(ref)
        self.set_data(data, ref.offset or 0)

    def set_data(self, data: bytes | None, base: int = 0) -> None:
        if data is None:
            self.area.set_model(HexModel(b""))
            self.empty.setText(INLINE_NOTE)
            self.stack.setCurrentWidget(self.empty)
            return
        self.area.set_model(HexModel(data, base))
        self.stack.setCurrentWidget(self.area)

    def set_relative(self, relative: bool) -> None:
        self.area.relative = relative
        self.area.viewport().update()

    def goto_dialog(self) -> None:
        text, ok = QInputDialog.getText(
            self, "Go to offset", "Offset (hex; zone offset unless relative):"
        )
        if not ok or not text.strip():
            return
        try:
            value = int(text.strip().removeprefix("0x"), 16)
        except ValueError:
            return
        if not self.area.relative:
            value -= self.model.base
        self.area.goto(value)

    def view_actions(self):
        from PySide6.QtGui import QAction

        go = QAction("Hex: Go to Offset...", self)
        go.setShortcut(QKeySequence("Ctrl+G"))
        go.triggered.connect(self.goto_dialog)
        rel = QAction("Hex: Relative Offsets", self)
        rel.setCheckable(True)
        rel.setChecked(self.area.relative)
        rel.toggled.connect(self.set_relative)
        return [go, rel]


__all__ = ["HexModel", "HexView", "INLINE_NOTE"]
