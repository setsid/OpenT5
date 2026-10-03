"""Strips across the top of a zone page, the loading page, and the shared-string choice.

- ``UnsavedStrip``: "Unsaved changes: N assets edited. Save As to write a new file." with
  Save As and Discard, shown whenever the zone has edits.
- ``ProgressStrip``: a determinate bar with the current stage, for a save in progress.
- ``LoadingPage``: what a zone's tab shows while it is being read (stage, bar, percentage).
- ``ShareChoiceDialog``: asked before an edit to a string other fields share: edit all of
  them, or split this one off. It always asks, starts on the last choice, and works from
  the keyboard (A, S, Enter, Esc).
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from opent5.gui import theme

SPLIT, ALL = "split", "all"


class UnsavedStrip(QWidget):
    save_as = Signal()
    discard = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("UnsavedStrip")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.label = QLabel()
        self.label.setObjectName("UnsavedText")
        self.save_btn = QPushButton("Save As...")
        self.save_btn.setObjectName("StripButton")
        self.save_btn.setToolTip("Write the edited zone to a new file (Ctrl+Shift+S)")
        self.save_btn.clicked.connect(self.save_as)
        self.discard_btn = QPushButton("Discard")
        self.discard_btn.setObjectName("StripButton")
        self.discard_btn.setToolTip("Undo every edit in this zone (Redo brings them back)")
        self.discard_btn.clicked.connect(self.discard)
        h = QHBoxLayout(self)
        h.setContentsMargins(10, 4, 8, 4)
        h.setSpacing(8)
        h.addWidget(self.label, 1)
        h.addWidget(self.save_btn)
        h.addWidget(self.discard_btn)
        self.hide()

    def set_count(self, assets: int, changes: int) -> None:
        if not changes:
            self.hide()
            return
        what = f"{assets} asset{'s' if assets != 1 else ''} edited"
        edits = f" ({changes} edit{'s' if changes != 1 else ''})"
        self.label.setText(f"Unsaved changes: {what}{edits}. Save As to write a new file.")
        self.show()


class ProgressStrip(QWidget):
    """Stage text, a determinate bar and the percentage."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("ProgressStrip")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.title = QLabel()
        self.title.setObjectName("ProgressTitle")
        self.stage = QLabel()
        self.stage.setObjectName("AssetMeta")
        self.bar = QProgressBar()
        self.bar.setObjectName("BigProgress")
        self.bar.setRange(0, 1000)
        self.bar.setTextVisible(False)
        self.pct = QLabel("0%")
        self.pct.setObjectName("ProgressPct")
        self.pct.setFont(theme.mono_font())
        self.pct.setMinimumWidth(self.pct.fontMetrics().horizontalAdvance("100%") + 4)
        self.pct.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        top = QHBoxLayout()
        top.setSpacing(10)
        top.addWidget(self.title)
        top.addWidget(self.stage, 1)
        top.addWidget(self.pct)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 5, 10, 6)
        lay.setSpacing(4)
        lay.addLayout(top)
        lay.addWidget(self.bar)
        self.hide()

    def start(self, title: str) -> None:
        self.title.setText(title)
        self.set_progress("Starting", 0.0)
        self.show()

    def set_progress(self, stage: str, fraction: float) -> None:
        fraction = min(max(fraction, 0.0), 1.0)
        self.stage.setText(stage)
        self.bar.setValue(int(fraction * 1000))
        self.pct.setText(f"{int(fraction * 100)}%")


class LoadingPage(QWidget):
    """A zone tab while the zone is read on a worker thread: a centred panel with the
    file name, the stage, a bar and the percentage."""

    def __init__(self, path, parent=None):
        super().__init__(parent)
        self.path = path
        self.cancelled = False
        self.progress = ProgressStrip()
        self.progress.setObjectName("LoadingPanel")
        self.progress.setFixedWidth(560)
        self.progress.start(f"Opening {path.name}")
        hint = QLabel(str(path))
        hint.setObjectName("EmptyState")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay = QVBoxLayout(self)
        lay.addStretch(2)
        lay.addWidget(self.progress, 0, Qt.AlignmentFlag.AlignHCenter)
        lay.addSpacing(8)
        lay.addWidget(hint)
        lay.addStretch(3)

    def set_progress(self, stage: str, fraction: float) -> None:
        self.progress.set_progress(stage, fraction)


class ShareChoiceDialog(QDialog):
    """Edit all fields sharing a string, or split this one off. ``choice`` is "all",
    "split", or None when cancelled."""

    last = SPLIT

    def __init__(self, what: str, others: list[str], parent=None, default: str | None = None):
        super().__init__(parent)
        self.setWindowTitle("Shared string")
        self.choice: str | None = None
        names = ", ".join(others[:8]) + (f" and {len(others) - 8} more" if len(others) > 8 else "")
        head = QLabel(f"{what} is stored once and shared with: {names}.")
        head.setWordWrap(True)
        body = QLabel(
            "Edit all sharing keys: the stored string changes, so every key above shows "
            "the new text.\nSplit this key: only this one changes; the others keep the old "
            "text in their own copy."
        )
        body.setObjectName("AssetMeta")
        body.setWordWrap(True)
        self.all_btn = QPushButton("Edit &all sharing keys")
        self.split_btn = QPushButton("&Split this key")
        cancel = QPushButton("Cancel")
        self.all_btn.clicked.connect(lambda: self._pick(ALL))
        self.split_btn.clicked.connect(lambda: self._pick(SPLIT))
        cancel.clicked.connect(self.reject)
        default = default or ShareChoiceDialog.last
        first = self.all_btn if default == ALL else self.split_btn
        first.setDefault(True)
        first.setAutoDefault(True)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(self.all_btn)
        buttons.addWidget(self.split_btn)
        buttons.addWidget(cancel)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 12)
        lay.setSpacing(10)
        lay.addWidget(head)
        lay.addWidget(body)
        lay.addLayout(buttons)
        self.setMinimumWidth(520)
        first.setFocus()

    def _pick(self, choice: str) -> None:
        self.choice = choice
        ShareChoiceDialog.last = choice
        self.accept()


#: Replaced in tests and scripted runs: (what, other labels, parent) -> "all" | "split" | None.
def ask_share(what: str, others: list[str], parent=None) -> str | None:
    d = ShareChoiceDialog(what, others, parent)
    d.exec()
    return d.choice
