"""The text editor: GSC/CSC, cfg, menu text and entity strings.

``CodeEditor`` is a QPlainTextEdit with a line-number gutter, a current-line
band and syntax highlighting. ``FindBar`` does find and replace (plain or
regular expression, case sensitive or not) with every match marked.
``TextView`` puts them together over a document: edits go to the backend after
a short pause (or at once before undo, save or a switch), and undo and redo go
through the backend, so the editor's own undo stack is off.

GSC / CSC scripts are stored without indentation. With View > Formatted GSC (on by
default, ``FORMAT_SCRIPTS``) the editor shows them indented by
``opent5.edit.gscformat.format_script`` and stores what is typed through
``unformat_script``, so an unedited script reads back exactly as stored and an edited line
is stored the way the game's files are (docs/edit-api.md, GSC formatting).
"""

from __future__ import annotations

import contextlib
import re

from PySide6.QtCore import QEvent, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QAction,
    QColor,
    QKeySequence,
    QPainter,
    QTextCursor,
    QTextFormat,
)
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from opent5.edit import gscformat
from opent5.gui import icons, theme
from opent5.gui.backend import EditError
from opent5.gui.views.base import AssetView, empty_state
from opent5.gui.views.highlight import Highlighter, mode_for

COMMIT_DELAY_MS = 400
#: Show .gsc / .csc indented (View > Formatted GSC).
FORMAT_SCRIPTS = True


class _Gutter(QWidget):
    def __init__(self, editor: CodeEditor):
        super().__init__(editor)
        self.editor = editor

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(self.editor.gutter_width(), 0)

    def paintEvent(self, event) -> None:  # noqa: N802
        self.editor.paint_gutter(event)


class CodeEditor(QPlainTextEdit):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setFont(theme.mono_font())
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.setTabStopDistance(self.fontMetrics().horizontalAdvance(" ") * 4)
        self.setFrameShape(QPlainTextEdit.Shape.NoFrame)
        self.setUndoRedoEnabled(False)
        self.gutter = _Gutter(self)
        self.highlighter = Highlighter(self.document())
        self.matches: list[QTextEdit.ExtraSelection] = []
        self.blockCountChanged.connect(self._update_margins)
        self.updateRequest.connect(self._update_gutter)
        self.cursorPositionChanged.connect(self._highlight_line)
        self._update_margins()
        self._highlight_line()
        theme.on_change(lambda _t: (self._highlight_line(), self.gutter.update()), self)

    def event(self, e) -> bool:
        # Undo and redo go through the document (the window's actions), not the widget.
        if e.type() == QEvent.Type.ShortcutOverride and (
            e.matches(QKeySequence.StandardKey.Undo)
            or e.matches(QKeySequence.StandardKey.Redo)
            or e.keyCombination().toCombined() == QKeySequence("Ctrl+Shift+Z")[0].toCombined()
        ):
            e.ignore()
            return False
        return super().event(e)

    # gutter
    def gutter_width(self) -> int:
        digits = max(4, len(str(max(1, self.blockCount()))))
        return 12 + self.fontMetrics().horizontalAdvance("9") * digits

    def _update_margins(self, _count: int = 0) -> None:
        self.setViewportMargins(self.gutter_width(), 0, 0, 0)

    def _update_gutter(self, rect: QRect, dy: int) -> None:
        if dy:
            self.gutter.scroll(0, dy)
        else:
            self.gutter.update(0, rect.y(), self.gutter.width(), rect.height())
        if rect.contains(self.viewport().rect()):
            self._update_margins()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        cr = self.contentsRect()
        self.gutter.setGeometry(QRect(cr.left(), cr.top(), self.gutter_width(), cr.height()))

    def paint_gutter(self, event) -> None:
        t = theme.current()
        p = QPainter(self.gutter)
        p.fillRect(event.rect(), QColor(t.base))
        p.setPen(QColor(t.border))
        x = self.gutter.width() - 1
        p.drawLine(x, event.rect().top(), x, event.rect().bottom())
        block = self.firstVisibleBlock()
        number = block.blockNumber()
        top = round(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
        bottom = top + round(self.blockBoundingRect(block).height())
        current = self.textCursor().blockNumber()
        p.setFont(self.font())
        height = self.fontMetrics().height()
        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                p.setPen(QColor(t.text_dim if number == current else t.text_faint))
                p.drawText(
                    0,
                    top,
                    self.gutter.width() - 8,
                    height,
                    Qt.AlignmentFlag.AlignRight,
                    str(number + 1),
                )
            block = block.next()
            top = bottom
            bottom = top + round(self.blockBoundingRect(block).height())
            number += 1

    # current line + match marks
    def _highlight_line(self) -> None:
        line = QTextEdit.ExtraSelection()
        line.format.setBackground(QColor(theme.current().current_line))
        line.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
        line.cursor = self.textCursor()
        line.cursor.clearSelection()
        self.setExtraSelections([line, *self.matches])
        self.gutter.update()

    def set_matches(self, cursors: list[QTextCursor]) -> None:
        t = theme.current()
        out = []
        for c in cursors[:5000]:
            sel = QTextEdit.ExtraSelection()
            sel.format.setBackground(QColor(t.selection))
            sel.format.setProperty(QTextFormat.Property.OutlinePen, QColor(t.accent))
            sel.cursor = c
            out.append(sel)
        self.matches = out
        self._highlight_line()

    def go_to_line(self, line: int, column: int = 0) -> None:
        block = self.document().findBlockByNumber(max(0, line - 1))
        cursor = QTextCursor(block)
        cursor.movePosition(QTextCursor.MoveOperation.Right, n=max(0, column))
        self.setTextCursor(cursor)
        self.centerCursor()


class FindBar(QWidget):
    """Find (Ctrl+F) and replace (Ctrl+H) under an editor."""

    closed = Signal()

    def __init__(self, editor: CodeEditor, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("FindBar")
        self.editor = editor
        self.find = QLineEdit(placeholderText="Find")
        self.replace = QLineEdit(placeholderText="Replace")
        self.case = self._toggle("Aa", "Match case")
        self.regex = self._toggle(".*", "Regular expression")
        self.count = QLabel("")
        self.count.setMinimumWidth(80)
        prev_b = self._button("up", "Previous match (Shift+F3)", self.find_previous)
        next_b = self._button("down", "Next match (F3)", self.find_next)
        close_b = self._button("close", "Close (Esc)", self.close_bar)
        self.replace_one = QToolButton(text="Replace")
        self.replace_one.clicked.connect(self.replace_next)
        self.replace_all_b = QToolButton(text="All")
        self.replace_all_b.setToolTip("Replace all")
        self.replace_all_b.clicked.connect(self.replace_all)

        top = QHBoxLayout()
        top.setContentsMargins(6, 3, 4, 3)
        top.setSpacing(3)
        for w in (self.find, self.case, self.regex, prev_b, next_b, self.count):
            top.addWidget(w)
        top.addStretch(1)
        top.addWidget(close_b)
        self.replace_row = QWidget()
        bottom = QHBoxLayout(self.replace_row)
        bottom.setContentsMargins(6, 0, 4, 3)
        bottom.setSpacing(3)
        for w in (self.replace, self.replace_one, self.replace_all_b):
            bottom.addWidget(w)
        bottom.addStretch(1)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addLayout(top)
        lay.addWidget(self.replace_row)
        self.find.setMinimumWidth(260)
        self.replace.setMinimumWidth(260)
        self.find.textChanged.connect(self.update_matches)
        self.case.toggled.connect(self.update_matches)
        self.regex.toggled.connect(self.update_matches)
        self.find.returnPressed.connect(self.find_next)
        self.replace.returnPressed.connect(self.replace_next)
        self.hide()

    def _toggle(self, text: str, tip: str) -> QToolButton:
        b = QToolButton(text=text)
        b.setCheckable(True)
        b.setToolTip(tip)
        b.setFont(theme.mono_font())
        return b

    def _button(self, icon_name: str, tip: str, slot) -> QToolButton:
        b = QToolButton()
        b.setIcon(icons.icon(icon_name))
        b.setToolTip(tip)
        b.setAutoRaise(True)
        b.clicked.connect(slot)
        return b

    def open_find(self, replace: bool = False) -> None:
        self.replace_row.setVisible(replace)
        self.show()
        selected = self.editor.textCursor().selectedText()
        if selected and " " not in selected:
            self.find.setText(selected)
        self.find.setFocus()
        self.find.selectAll()
        self.update_matches()

    def close_bar(self) -> None:
        self.hide()
        self.editor.set_matches([])
        self.editor.setFocus()
        self.closed.emit()

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Escape:
            self.close_bar()
            return
        super().keyPressEvent(event)

    # matching
    def pattern(self) -> re.Pattern | None:
        text = self.find.text()
        if not text:
            return None
        flags = 0 if self.case.isChecked() else re.IGNORECASE
        try:
            return re.compile(text if self.regex.isChecked() else re.escape(text), flags | re.M)
        except re.error:
            return None

    def spans(self) -> list[tuple[int, int]]:
        pattern = self.pattern()
        if pattern is None:
            return []
        body = self.editor.toPlainText()
        return [m.span() for m in pattern.finditer(body) if m.end() > m.start()]

    def update_matches(self, *_args) -> None:
        if self.find.text() and self.pattern() is None:
            self.count.setText("bad pattern")
            self.editor.set_matches([])
            return
        spans = self.spans()
        doc = self.editor.document()
        cursors = []
        for a, b in spans[:5000]:
            c = QTextCursor(doc)
            c.setPosition(a)
            c.setPosition(b, QTextCursor.MoveMode.KeepAnchor)
            cursors.append(c)
        self.editor.set_matches(cursors)
        self._show_count(spans)

    def _show_count(self, spans) -> None:
        if not self.find.text():
            self.count.setText("")
            return
        if not spans:
            self.count.setText("no matches")
            return
        pos = self.editor.textCursor().selectionStart()
        current = next((i for i, (a, _b) in enumerate(spans) if a == pos), None)
        prefix = f"{current + 1} of " if current is not None else ""
        self.count.setText(f"{prefix}{len(spans)}")

    def _select(self, span) -> None:
        c = self.editor.textCursor()
        c.setPosition(span[0])
        c.setPosition(span[1], QTextCursor.MoveMode.KeepAnchor)
        self.editor.setTextCursor(c)
        self.editor.centerCursor()

    def find_next(self) -> bool:
        spans = self.spans()
        if not spans:
            self._show_count(spans)
            return False
        pos = self.editor.textCursor().selectionEnd()
        nxt = next((s for s in spans if s[0] >= pos), spans[0])
        self._select(nxt)
        self._show_count(spans)
        return True

    def find_previous(self) -> bool:
        spans = self.spans()
        if not spans:
            return False
        pos = self.editor.textCursor().selectionStart()
        prev = next((s for s in reversed(spans) if s[0] < pos), spans[-1])
        self._select(prev)
        self._show_count(spans)
        return True

    def _replacement(self, matched: str) -> str:
        if not self.regex.isChecked():
            return self.replace.text()
        return self.pattern().sub(self.replace.text(), matched, count=1)

    def replace_next(self) -> None:
        if self.editor.isReadOnly():
            return
        c = self.editor.textCursor()
        pattern = self.pattern()
        if pattern is None:
            return
        if c.hasSelection() and pattern.fullmatch(c.selectedText()):
            c.insertText(self._replacement(c.selectedText()))
        self.find_next()
        self.update_matches()

    def replace_all(self) -> int:
        if self.editor.isReadOnly():
            return 0
        pattern = self.pattern()
        if pattern is None:
            return 0
        body = self.editor.toPlainText()
        repl = (
            self.replace.text()
            if self.regex.isChecked()
            else self.replace.text().replace("\\", "\\\\")
        )
        new, n = pattern.subn(repl, body)
        if n:
            c = self.editor.textCursor()
            c.beginEditBlock()
            c.select(QTextCursor.SelectionType.Document)
            c.insertText(new)
            c.endEditBlock()
        self.update_matches()
        self.count.setText(f"replaced {n}")
        return n


class TextView(AssetView):
    kind = "text"
    title = "Text"

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.editor = CodeEditor()
        self.findbar = FindBar(self.editor)
        self.empty = empty_state("")
        self.empty.hide()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self.editor, 1)
        lay.addWidget(self.empty, 1)
        lay.addWidget(self.findbar)
        self._loading = False
        self._shown: str | None = None
        self._eol = "\n"
        #: True while the shown script is the formatted form of the stored one.
        self.formatted = False
        self._timer = QTimer(self, singleShot=True, interval=COMMIT_DELAY_MS)
        self._timer.timeout.connect(self.commit)
        self.editor.textChanged.connect(self._changed)
        self.editor.cursorPositionChanged.connect(self._cursor_status)
        self.a_find = QAction("Find", self, shortcut=QKeySequence.StandardKey.Find)
        self.a_find.triggered.connect(lambda: self.findbar.open_find(False))
        self.a_replace = QAction("Replace", self, shortcut=QKeySequence("Ctrl+H"))
        self.a_replace.triggered.connect(lambda: self.findbar.open_find(True))
        self.a_next = QAction("Find next", self, shortcut=QKeySequence("F3"))
        self.a_next.triggered.connect(self.findbar.find_next)
        self.a_prev = QAction("Find previous", self, shortcut=QKeySequence("Shift+F3"))
        self.a_prev.triggered.connect(self.findbar.find_previous)
        for a in (self.a_find, self.a_replace, self.a_next, self.a_prev):
            a.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            self.addAction(a)

    def view_actions(self) -> list[QAction]:
        return [self.a_find, self.a_replace, self.a_next, self.a_prev]

    def load(self, doc, ref) -> None:
        if self.doc is not None and self.ref is not None and (doc, ref) != (self.doc, self.ref):
            self.commit()
        super().load(doc, ref)
        try:
            body = doc.text(ref)
        except (EditError, ValueError, Exception) as exc:  # zlib.error included
            self.editor.hide()
            self.empty.setText(f"No text for this asset: {exc}")
            self.empty.show()
            return
        self.empty.hide()
        self.editor.show()
        mode = (
            "ents"
            if ref.type_name in ("map_ents", "col_map_mp", "col_map_sp")
            else mode_for(ref.name)
        )
        self.editor.highlighter.set_mode(mode)
        self.editor.setReadOnly(ref.inline)
        self.formatted = (
            FORMAT_SCRIPTS and gscformat.is_script(ref.name) and gscformat.round_trips(body)
        )
        self.set_body(body)

    def set_body(self, body: str) -> None:
        """Show ``body``. The editor keeps lines with \\n only, so the line ending of
        the asset is remembered and restored on commit; an untouched buffer never
        reads as an edit. A script is shown formatted when ``formatted`` is set."""
        if self.formatted:
            body = gscformat.format_script(body)
        self._eol = "\r\n" if "\r\n" in body else "\n"
        shown = body.replace("\r\n", "\n") if self._eol == "\r\n" else body
        if shown == self.editor.toPlainText():
            self._shown = shown
            return
        bar = self.editor.verticalScrollBar().value()
        pos = self.editor.textCursor().position()
        self._loading = True
        self.editor.setPlainText(shown)
        self._shown = self.editor.toPlainText()
        self._loading = False
        c = self.editor.textCursor()
        c.setPosition(min(pos, len(body)))
        self.editor.setTextCursor(c)
        self.editor.verticalScrollBar().setValue(bar)
        if self.findbar.isVisible():
            self.findbar.update_matches()

    def refresh(self) -> None:
        self._timer.stop()
        if self.doc is not None and self.ref is not None:
            with contextlib.suppress(EditError):
                self.set_body(self.doc.text(self.ref))

    def _changed(self) -> None:
        if not self._loading and not self.editor.isReadOnly():
            self._timer.start()

    def commit(self) -> bool:
        """Send pending typing to the backend now. True when something was sent."""
        self._timer.stop()
        if self.doc is None or self.ref is None or self.editor.isReadOnly():
            return False
        shown = self.editor.toPlainText()
        if shown == getattr(self, "_shown", None):
            return False
        body = shown.replace("\n", self._eol) if self._eol != "\n" else shown
        if self.formatted:
            body = gscformat.unformat_script(body)
        try:
            if body == self.doc.text(self.ref):
                self._shown = shown
                return False
            self.doc.set_text(self.ref, body)
            self._shown = shown
        except EditError as exc:
            self.status.emit(f"edit refused: {exc}")
            return False
        self.edited.emit()
        return True

    def _cursor_status(self) -> None:
        c = self.editor.textCursor()
        sel = abs(c.selectionEnd() - c.selectionStart())
        text = f"Ln {c.blockNumber() + 1}, Col {c.positionInBlock() + 1}"
        if sel:
            text += f" ({sel} selected)"
        if self.formatted:
            text += "  formatted"
        self.status.emit(text)

    def go_to(self, line: int, column: int = 0) -> None:
        """Go to a line and a column of the stored text (search results count columns
        there); in a formatted script the column moves by the indentation added."""
        if self.formatted:
            block = self.editor.document().findBlockByNumber(max(line - 1, 0))
            shown = block.text()
            column += len(shown) - len(shown.lstrip(" \t"))
        self.editor.go_to_line(line, column)

    def find_text(self, text: str) -> None:
        self.findbar.open_find(False)
        self.findbar.find.setText(text)
        self.editor.moveCursor(QTextCursor.MoveOperation.Start)
        self.findbar.find_next()
