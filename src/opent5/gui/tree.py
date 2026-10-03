"""The zone tree: asset types with counts, assets underneath, and a filter box."""

from __future__ import annotations

import threading

import numpy as np
from PySide6.QtCore import (
    QAbstractItemModel,
    QModelIndex,
    QObject,
    QRunnable,
    QSize,
    QSortFilterProxyModel,
    Qt,
    QThreadPool,
    Signal,
)
from PySide6.QtGui import QColor, QFont, QIcon, QImage, QPainter, QPixmap
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
from opent5.gui.backend import Ref, ZoneDoc
from opent5.xfile.constants import AssetType as T

ROLE = Qt.ItemDataRole
NO_INDEX = QModelIndex()
REF_ROLE = Qt.ItemDataRole.UserRole + 1
#: Thumbnail side in logical pixels; kept small so the tree stays dense.
THUMB_PX = 18


class _ThumbSignals(QObject):
    ready = Signal(object, object)  # (asset key, scaled QImage) or (key, None)


class _ThumbJob(QRunnable):
    def __init__(self, loader: ThumbnailLoader, ref: Ref):
        super().__init__()
        self.loader, self.ref = loader, ref

    def run(self) -> None:
        self.loader.signals.ready.emit(self.ref.key, self.loader.decode(self.ref))


class ThumbnailLoader:
    """Decodes small image thumbnails on worker threads, lazily and once per asset.

    It uses its own .pak handles (not the document's), so it never races the image
    view or the geometry textures, and serialises its own decodes with a lock."""

    def __init__(self, doc: ZoneDoc, px: int = THUMB_PX):
        self.doc = doc
        self.px = px
        self.signals = _ThumbSignals()
        self._lock = threading.Lock()
        self._paks = None

    def request(self, ref: Ref) -> None:
        QThreadPool.globalInstance().start(_ThumbJob(self, ref))

    def decode(self, ref: Ref):
        from opent5.export.images import ImageError, PakSet, decode_image
        from opent5.gui.backend import pak_dirs

        node = self.doc.node(ref)
        if not isinstance(node, dict):
            return None
        try:
            with self._lock:
                if self._paks is None:
                    self._paks = PakSet(self.doc.zone_name, pak_dirs(self.doc.path))
                decoded = decode_image(node, self._paks)
            rgba = decoded.layers[0][1] if decoded.layers else None
        except (ImageError, ValueError, KeyError, IndexError, TypeError):
            return None
        if rgba is None or not len(rgba):
            return None
        return _scaled_image(rgba, self.px)

    def close(self) -> None:
        if self._paks is not None:
            self._paks.close()
            self._paks = None


def _scaled_image(rgba: np.ndarray, px: int) -> QImage:
    """RGBA array -> a QImage no larger than px, keeping aspect (worker-thread safe)."""
    arr = np.ascontiguousarray(rgba, np.uint8)
    h, w = arr.shape[:2]
    image = QImage(arr.tobytes(), w, h, 4 * w, QImage.Format.Format_RGBA8888).copy()
    target = px * 2  # 2x for crisp thumbnails on high-dpi displays
    return image.scaled(
        target,
        target,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )


def _thumb_icon(image: QImage, px: int) -> QIcon:
    """Centre a scaled thumbnail on a px square with a 1px border (GUI thread)."""
    ratio = image.width() / max(1, image.height())
    side = px * 2
    pm = QPixmap(side, side)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    iw = side if ratio >= 1 else round(side * ratio)
    ih = side if ratio <= 1 else round(side / ratio)
    x, y = (side - iw) // 2, (side - ih) // 2
    p.drawImage(x, y, image.scaled(iw, ih))
    p.setPen(QColor(theme.current().border))
    p.drawRect(x, y, iw - 1, ih - 1)
    p.end()
    pm.setDevicePixelRatio(2.0)
    return QIcon(pm)


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
        self.thumbs: dict = {}  # asset key -> QIcon thumbnail
        self._thumb_requested: set = set()
        self.loader: ThumbnailLoader | None = None

    def set_doc(self, doc: ZoneDoc | None) -> None:
        self.beginResetModel()
        self.groups = []
        self.thumbs = {}
        self._thumb_requested = set()
        if self.loader is not None:
            self.loader.close()
        self.loader = None
        if doc is not None:
            self.loader = ThumbnailLoader(doc)
            self.loader.signals.ready.connect(self._thumb_ready)
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
            if role == ROLE.DecorationRole and index.column() == 0:
                return icons.type_icon(group.type_name)
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
        if role == ROLE.DecorationRole and index.column() == 0:
            return self._decoration(r)
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

    def _decoration(self, r: Ref) -> QIcon:
        """A cached thumbnail for an image asset (requested lazily on first sight),
        else the per-type line icon."""
        if r.type == T.IMAGE:
            thumb = self.thumbs.get(r.key)
            if thumb is not None:
                return thumb
            if r.key not in self._thumb_requested and self.loader is not None:
                self._thumb_requested.add(r.key)
                self.loader.request(r)
        return icons.type_icon(r.type_name)

    def _thumb_ready(self, key, image) -> None:
        if image is not None:
            self.thumbs[key] = _thumb_icon(image, THUMB_PX)
        idx = self.index_of(key)
        if idx.isValid():
            self.dataChanged.emit(idx, idx, [ROLE.DecorationRole])

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
        self.view.setIconSize(QSize(THUMB_PX, THUMB_PX))
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
