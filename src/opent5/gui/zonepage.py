"""One open zone: the asset tree on the left, the selected asset's views on the right."""

from __future__ import annotations

import importlib

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QSplitter,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from opent5.gui import theme
from opent5.gui.backend import Ref, ZoneDoc
from opent5.gui.tree import AssetTree, human_size
from opent5.gui.views.base import AssetView, empty_state

#: View kind -> (module, class, button label). Modules load on first use.
VIEWS = {
    "text": ("opent5.gui.views.code", "TextView", "Text"),
    "table": ("opent5.gui.views.table", "TableView", "Table"),
    "localize": ("opent5.gui.views.table", "LocalizeView", "Localize"),
    "image": ("opent5.gui.views.image", "ImageView", "Image"),
    "geometry": ("opent5.gui.views.mesh", "MeshView", "Geometry"),
    "fields": ("opent5.gui.views.fields", "FieldsView", "Fields"),
    "hex": ("opent5.gui.views.hex", "HexView", "Hex"),
}
ORDER = list(VIEWS)


class ZoneSummary(QWidget):
    """Shown when no asset is selected: what the zone is."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.grid = QGridLayout()
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(16)
        self.grid.setVerticalSpacing(3)
        self.title = QLabel()
        f = self.title.font()
        f.setPointSize(f.pointSize() + 3)
        self.title.setFont(f)
        self.hint = QLabel("Select an asset on the left, or press Ctrl+P to open one by name.")
        self.hint.setObjectName("EmptyState")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(10)
        lay.addWidget(self.title)
        lay.addLayout(self.grid)
        lay.addWidget(self.hint)
        lay.addStretch(1)

    def show_doc(self, doc: ZoneDoc) -> None:
        while self.grid.count():
            w = self.grid.takeAt(0).widget()
            if w:
                w.deleteLater()
        self.title.setText(doc.zone_name)
        size = doc.path.stat().st_size if doc.path.exists() else 0
        problems = doc.problems
        rows = [
            ("File", str(doc.path)),
            ("Size", f"{size:,} bytes ({human_size(size)})"),
            ("Assets", f"{len(doc.refs)} in the asset list, {len(doc.inline_refs)} loaded inline"),
            ("Signature", "console signature present" if doc.signed else "no console signature"),
            ("Parse", "exact" if not problems else "; ".join(problems)),
            (
                "Backend",
                "opent5.edit"
                if doc.backend == "edit"
                else "read adapter (saving needs opent5.edit)",
            ),
            ("Opened in", f"{doc.load_seconds:.1f} s"),
        ]
        counts = sorted(doc.type_counts().items(), key=lambda kv: -kv[1])
        rows.append(("Types", ", ".join(f"{k} {v}" for k, v in counts)))
        mono = theme.mono_font()
        for i, (k, v) in enumerate(rows):
            key = QLabel(k)
            key.setObjectName("InfoKey")
            val = QLabel(v)
            val.setFont(mono)
            val.setWordWrap(True)
            val.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self.grid.addWidget(key, i, 0, Qt.AlignmentFlag.AlignTop)
            self.grid.addWidget(val, i, 1)
        self.grid.setColumnStretch(1, 1)


class ZonePage(QWidget):
    edited = Signal()
    status = Signal(str)
    selected = Signal(object)  # Ref | None

    def __init__(self, doc: ZoneDoc, parent=None):
        super().__init__(parent)
        self.doc = doc
        self.ref: Ref | None = None
        self.views: dict[str, AssetView] = {}
        self.kind: str | None = None
        self._kind_for_type: dict[str, str] = {}

        self.tree = AssetTree()
        self.tree.set_doc(doc)
        self.tree.activated.connect(self.show_ref)

        # asset header: name, then type/size/offset; the view switcher underneath
        self.header = QWidget()
        self.header.setObjectName("AssetHeader")
        self.name_label = QLabel()
        self.name_label.setObjectName("AssetTitle")
        self.name_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.meta_label = QLabel()
        self.meta_label.setObjectName("AssetMeta")
        self.meta_label.setFont(theme.mono_font())
        hh = QHBoxLayout(self.header)
        hh.setContentsMargins(8, 4, 8, 4)
        hh.setSpacing(12)
        hh.addWidget(self.name_label, 1)
        hh.addWidget(self.meta_label)

        self.viewbar = QWidget()
        self.viewbar.setObjectName("ViewBar")
        self.viewbar_lay = QHBoxLayout(self.viewbar)
        self.viewbar_lay.setContentsMargins(4, 0, 4, 0)
        self.viewbar_lay.setSpacing(0)
        self.buttons: dict[str, QToolButton] = {}
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        for kind in ORDER:
            b = QToolButton(text=VIEWS[kind][2])
            b.setCheckable(True)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.setAutoRaise(True)
            b.clicked.connect(lambda _c=False, k=kind: self.set_kind(k))
            self.group.addButton(b)
            self.viewbar_lay.addWidget(b)
            self.buttons[kind] = b
        self.viewbar_lay.addStretch(1)

        self.stack = QStackedWidget()
        self.summary = ZoneSummary()
        self.summary.show_doc(doc)
        self.stack.addWidget(self.summary)

        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(0)
        rl.addWidget(self.header)
        rl.addWidget(self.viewbar)
        rl.addWidget(self.stack, 1)
        self.header.hide()
        self.viewbar.hide()

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setHandleWidth(1)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.addWidget(self.tree)
        self.splitter.addWidget(right)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([320, 1000])
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.splitter)

    # views
    def view(self, kind: str) -> AssetView:
        if kind not in self.views:
            module, cls, _label = VIEWS[kind]
            try:
                view = getattr(importlib.import_module(module), cls)()
            except (ImportError, AttributeError) as exc:
                view = _Missing(kind, str(exc))
            view.edited.connect(self._edited)
            view.status.connect(self.status)
            self.views[kind] = view
            self.stack.addWidget(view)
        return self.views[kind]

    def available(self, ref: Ref) -> list[str]:
        kinds = [k for k in ORDER if k in ref.editable]
        if "entities" in ref.editable and "text" not in kinds:
            kinds.insert(0, "text")
        return kinds or ["fields", "hex"]

    def show_ref(self, ref: Ref | None, kind: str | None = None) -> None:
        self.commit()
        self.ref = ref
        if ref is None:
            self.header.hide()
            self.viewbar.hide()
            self.stack.setCurrentWidget(self.summary)
            self.selected.emit(None)
            return
        self.header.show()
        self.viewbar.show()
        self._update_header()
        kinds = self.available(ref)
        for k, b in self.buttons.items():
            b.setVisible(k in kinds)
        if kind is None or kind not in kinds:
            remembered = self._kind_for_type.get(ref.type_name)
            kind = remembered if remembered in kinds else kinds[0]
        self.set_kind(kind)
        self.selected.emit(ref)

    def _update_header(self) -> None:
        ref = self.ref
        if ref is None:
            return
        mark = "* " if ref.key in self.doc.edited_keys() else ""
        self.name_label.setText(mark + ref.label)
        parts = [ref.type_name]
        if ref.inline:
            parts.append("inline")
        else:
            parts.append(f"#{ref.key}")
            parts.append(f"{ref.size:,} bytes")
            if ref.offset is not None:
                parts.append(f"@0x{ref.offset:x}")
        self.meta_label.setText("  ".join(parts))

    def set_kind(self, kind: str) -> None:
        if self.ref is None:
            return
        self.commit()
        self.kind = kind
        self._kind_for_type[self.ref.type_name] = kind
        self.buttons[kind].setChecked(True)
        view = self.view(kind)
        self.status.emit("")
        view.load(self.doc, self.ref)
        self.stack.setCurrentWidget(view)

    def current_view(self) -> AssetView | None:
        w = self.stack.currentWidget()
        return w if isinstance(w, AssetView) else None

    def commit(self) -> None:
        """Flush pending typing in the text view to the backend."""
        view = self.views.get("text")
        if view is not None and hasattr(view, "commit"):
            view.commit()

    def refresh(self) -> None:
        """After undo or redo: re-read the shown asset and the markers."""
        view = self.current_view()
        if view is not None:
            view.refresh()
        self.tree.model.set_edited(self.doc.edited_keys())
        self._update_header()

    def _edited(self) -> None:
        self.tree.model.set_edited(self.doc.edited_keys())
        self._update_header()
        self.edited.emit()

    def open_ref(self, ref: Ref, kind: str | None = None) -> None:
        self.tree.select(ref)
        if self.ref is None or self.ref.key != ref.key:
            self.show_ref(ref, kind)
        elif kind is not None and kind != self.kind:
            self.set_kind(kind)

    def find_ref(self, name: str, type_name: str | None = None) -> Ref | None:
        for r in self.doc.all_refs:
            if r.name == name and (type_name is None or r.type_name == type_name):
                return r
        return None


class _Missing(AssetView):
    def __init__(self, kind: str, reason: str):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.addWidget(empty_state(f"The {kind} view is not available: {reason}"))
