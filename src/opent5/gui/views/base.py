"""The common shape of an asset view, and small shared widgets."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from opent5.gui.backend import Ref, ZoneDoc


class AssetView(QWidget):
    """One way of looking at an asset. The zone page creates one per kind and calls
    ``load`` whenever the selected asset changes; ``refresh`` after undo or redo."""

    kind = "hex"
    title = "Hex"
    #: An edit went through the document (the page refreshes markers and panels).
    edited = Signal()
    #: Text for the status bar's selection field (offset, cursor position, zoom).
    status = Signal(str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.doc: ZoneDoc | None = None
        self.ref: Ref | None = None

    def load(self, doc: ZoneDoc, ref: Ref) -> None:
        self.doc, self.ref = doc, ref

    def refresh(self) -> None:
        if self.doc is not None and self.ref is not None:
            self.load(self.doc, self.ref)

    def view_actions(self) -> list[QAction]:
        """Actions this view adds to the command palette while it is shown."""
        return []


def empty_state(text: str, parent: QWidget | None = None) -> QLabel:
    label = QLabel(text, parent)
    label.setObjectName("EmptyState")
    label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    label.setWordWrap(True)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return label


def vbox(widget: QWidget, *children, margins=(0, 0, 0, 0), spacing=0) -> QVBoxLayout:
    lay = QVBoxLayout(widget)
    lay.setContentsMargins(*margins)
    lay.setSpacing(spacing)
    for c in children:
        lay.addWidget(c)
    return lay


def hbox(widget: QWidget, *children, margins=(0, 0, 0, 0), spacing=0) -> QHBoxLayout:
    lay = QHBoxLayout(widget)
    lay.setContentsMargins(*margins)
    lay.setSpacing(spacing)
    for c in children:
        lay.addWidget(c)
    return lay
