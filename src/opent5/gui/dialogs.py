"""About, save report and keyboard shortcut dialogs."""

from __future__ import annotations

import sys

from PySide6 import __version__ as PYSIDE_VERSION
from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

import opent5
from opent5.gui import icons, theme
from opent5.gui.backend import SaveReport, edit_available


def _key(text: str) -> QLabel:
    k = QLabel(text)
    k.setObjectName("InfoKey")
    return k


def _val(text: str, wrap: bool = False) -> QLabel:
    v = QLabel(text)
    v.setFont(theme.mono_font())
    v.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    v.setWordWrap(wrap)
    v.setMinimumHeight(v.fontMetrics().height() + 2)
    return v


class AboutDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"About {opent5.APP_NAME}")
        mark = QLabel()
        mark.setPixmap(QPixmap.fromImage(icons.render_mark(64)))
        mark.setAlignment(Qt.AlignmentFlag.AlignTop)
        name = QLabel(f"{opent5.APP_NAME} {opent5.__version__}")
        f = name.font()
        f.setPointSize(f.pointSize() + 4)
        name.setFont(f)
        credit = QLabel(f"{opent5.APP_NAME} by {opent5.APP_AUTHOR}")
        credit.setObjectName("Credit")
        what = QLabel("Open, browse, edit and rebuild Black Ops (T5) PS3 fastfiles.")
        what.setObjectName("AssetMeta")
        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(3)
        rows = [
            (
                "Editing",
                "opent5.edit" if edit_available() else "read adapter; saving needs opent5.edit",
            ),
            ("Qt", f"PySide6 {PYSIDE_VERSION}"),
            ("Python", sys.version.split()[0]),
        ]
        for i, (k, v) in enumerate(rows):
            grid.addWidget(_key(k), i, 0)
            grid.addWidget(_val(v), i, 1)
        grid.setColumnStretch(1, 1)
        notice = QPlainTextEdit(opent5.LICENCE_NOTICE)
        notice.setObjectName("ReportBody")
        notice.setReadOnly(True)
        notice.setFont(theme.mono_font())
        notice.setFixedHeight(notice.fontMetrics().height() * 4 + 16)
        notice.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        text = QVBoxLayout()
        text.setSpacing(6)
        text.addWidget(name)
        text.addWidget(credit)
        text.addWidget(what)
        text.addSpacing(4)
        text.addLayout(grid)
        top = QHBoxLayout()
        top.setSpacing(16)
        top.addWidget(mark)
        top.addLayout(text, 1)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 12)
        lay.setSpacing(12)
        lay.addLayout(top)
        lay.addWidget(notice)
        lay.addWidget(buttons)
        self.resize(620, 0)


class SaveReportDialog(QDialog):
    """What a save wrote and what verify-on-save found, with the signature note."""

    def __init__(self, report: SaveReport, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Save report")
        ok = report.verified and not report.problems
        head = QLabel("Saved and verified" if ok else "Saved, with problems")
        f = head.font()
        f.setPointSize(f.pointSize() + 2)
        head.setFont(f)
        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(3)
        rows = [
            ("File", str(report.path)),
            ("Size", f"{report.bytes:,} bytes"),
            ("Assets checked", str(report.assets_checked)),
            ("Assets changed", str(report.assets_changed)),
            (
                "Verified",
                "yes: reopened, every asset parsed, unchanged assets byte-identical"
                if report.verified
                else "no",
            ),
        ]
        for i, (k, v) in enumerate(rows):
            grid.addWidget(_key(k), i, 0, Qt.AlignmentFlag.AlignTop)
            grid.addWidget(_val(v), i, 1)
        grid.setColumnStretch(1, 1)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 12)
        lay.setSpacing(10)
        lay.addWidget(head)
        lay.addLayout(grid)
        if report.signature_note:
            note = QLabel(report.signature_note)
            note.setObjectName("Notice")
            note.setWordWrap(True)
            lay.addWidget(note)
        if report.problems:
            box = QPlainTextEdit("\n".join(report.problems))
            box.setObjectName("ReportBody")
            box.setReadOnly(True)
            box.setFont(theme.mono_font())
            box.setMaximumHeight(160)
            lay.addWidget(_key(f"Problems ({len(report.problems)})"))
            lay.addWidget(box)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        buttons.accepted.connect(self.accept)
        lay.addWidget(buttons)
        self.resize(640, 0)


def _sha(value: str) -> str:
    return value or "(none)"


def _changes_box(changes) -> QPlainTextEdit:
    """A monospace list of the assets a patch changes: index, type, op, name, size."""
    lines = [
        f"{c.index:>6}  {c.type_name:<14} {c.op:<9} {(c.name or ''):<40} {c.size:>8} bytes"
        for c in changes
    ]
    box = QPlainTextEdit("\n".join(lines))
    box.setObjectName("ReportBody")
    box.setReadOnly(True)
    box.setFont(theme.mono_font())
    box.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
    # size to the content, up to ten rows, leaving room for the horizontal scrollbar so the
    # last change is not clipped by it
    rows = min(max(len(lines), 1), 10)
    scrollbar = box.horizontalScrollBar().sizeHint().height()
    box.setFixedHeight(box.fontMetrics().lineSpacing() * rows + scrollbar + 14)
    return box


def _notice(text: str) -> QLabel:
    note = QLabel(text)
    note.setObjectName("Notice")
    note.setWordWrap(True)
    return note


class CreatePatchResultDialog(QDialog):
    """What ``opent5.patch.create`` produced, with a button to save the .o5patch."""

    def __init__(self, result, on_save=None, parent=None):
        super().__init__(parent)
        self.result = result
        self.setWindowTitle("Mod patch created")
        head = QLabel("Mod patch created")
        f = head.font()
        f.setPointSize(f.pointSize() + 2)
        head.setFont(f)
        rows = [
            ("Source zone", result.source_zone),
            ("Assets changed", str(len(result.changes))),
            ("Patch size", f"{result.patch_bytes:,} bytes"),
            ("Stock content", f"{result.source_bytes:,} bytes"),
            ("Edited content", f"{result.edited_bytes:,} bytes"),
            (
                "Reproduces the edited zone",
                "yes: the capture was replayed on a fresh stock copy and matched byte for byte",
            ),
            ("Source .ff sha1", _sha(result.source_ff_sha1)),
            ("Stock content sha1", _sha(result.source_content_sha1)),
            ("Result content sha1", _sha(result.result_content_sha1)),
        ]
        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(3)
        for i, (k, v) in enumerate(rows):
            grid.addWidget(_key(k), i, 0, Qt.AlignmentFlag.AlignTop)
            grid.addWidget(_val(v, wrap=True), i, 1)
        grid.setColumnStretch(1, 1)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 12)
        lay.setSpacing(10)
        lay.addWidget(head)
        lay.addLayout(grid)
        lay.addWidget(_key(f"Changed assets ({len(result.changes)})"))
        lay.addWidget(_changes_box(result.changes))
        if result.signature_note:
            lay.addWidget(_notice(result.signature_note))
        buttons = QDialogButtonBox()
        if on_save is not None:
            save = buttons.addButton("Save Patch As...", QDialogButtonBox.ButtonRole.ActionRole)
            save.clicked.connect(lambda: on_save(self))
        close = buttons.addButton(QDialogButtonBox.StandardButton.Close)
        close.clicked.connect(self.reject)
        lay.addWidget(buttons)
        self.resize(680, 0)


class ApplyPatchResultDialog(QDialog):
    """What ``opent5.patch.apply`` produced: the source check, what it wrote, the signature."""

    def __init__(self, result, parent=None):
        super().__init__(parent)
        self.result = result
        self.setWindowTitle("Mod patch applied")
        matched = result.found_content_sha1 == result.expected_content_sha1
        ok = result.verified and result.reproduces_target and not result.problems
        head = QLabel("Patch applied and verified" if ok else "Patch applied, with problems")
        f = head.font()
        f.setPointSize(f.pointSize() + 2)
        head.setFont(f)
        rows = [
            ("Source zone", result.source_zone),
            (
                "Source verified",
                "yes: your stock content sha1 matches the patch"
                if matched
                else "no: the stock content sha1 does not match the patch",
            ),
            ("Expected content sha1", _sha(result.expected_content_sha1)),
            ("Found content sha1", _sha(result.found_content_sha1)),
            ("Reproduces the edited zone", "yes" if result.reproduces_target else "no"),
            ("Verified", "yes" if result.verified else "no"),
            ("Assets changed", str(len(result.changes))),
            ("Output", result.output or "(nothing written)"),
        ]
        if result.output:
            rows.append(("Output size", f"{result.output_bytes:,} bytes"))
            if result.output_sha1:
                rows.append(("Output sha1", result.output_sha1))
        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(3)
        for i, (k, v) in enumerate(rows):
            grid.addWidget(_key(k), i, 0, Qt.AlignmentFlag.AlignTop)
            grid.addWidget(_val(v, wrap=True), i, 1)
        grid.setColumnStretch(1, 1)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 12)
        lay.setSpacing(10)
        lay.addWidget(head)
        lay.addLayout(grid)
        lay.addWidget(_key(f"Changed assets ({len(result.changes)})"))
        lay.addWidget(_changes_box(result.changes))
        for note in result.notes:
            lay.addWidget(_notice(note))
        if result.signature_note:
            lay.addWidget(_notice(result.signature_note))
        if result.problems:
            box = QPlainTextEdit("\n".join(result.problems))
            box.setObjectName("ReportBody")
            box.setReadOnly(True)
            box.setFont(theme.mono_font())
            box.setMaximumHeight(140)
            lay.addWidget(_key(f"Problems ({len(result.problems)})"))
            lay.addWidget(box)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        buttons.accepted.connect(self.accept)
        lay.addWidget(buttons)
        self.resize(680, 0)


class ShortcutsDialog(QDialog):
    """Every shortcut, grouped, in two columns."""

    def __init__(self, rows: list[tuple[str, str, str]], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Keyboard shortcuts")
        groups: dict[str, list[tuple[str, str]]] = {}
        for group, label, keys in rows:
            groups.setdefault(group, []).append((label, keys))
        total = sum(len(v) + 1 for v in groups.values())
        columns = QHBoxLayout()
        columns.setSpacing(28)
        grid, r = None, 0
        for group, items in groups.items():
            if grid is None or (r > total // 2 and columns.count() < 2):
                body = QWidget()
                grid = QGridLayout(body)
                grid.setContentsMargins(0, 0, 0, 0)
                grid.setHorizontalSpacing(18)
                grid.setVerticalSpacing(3)
                grid.setColumnStretch(2, 1)
                columns.addWidget(body, 0, Qt.AlignmentFlag.AlignTop)
                r = 0 if columns.count() > 1 else r
            g = _key(group)
            grid.addWidget(g, r, 0, 1, 2)
            r += 1
            for label, keys in items:
                grid.addWidget(QLabel(label), r, 0)
                grid.addWidget(_val(keys), r, 1)
                r += 1
            grid.setRowMinimumHeight(r, 6)
            r += 1
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 12)
        lay.addLayout(columns)
        lay.addWidget(buttons)
