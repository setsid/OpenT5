"""Image viewer: zoom and pan over a checkerboard, channel toggles, info panel,
PNG export and PNG/DDS import (through ``replace_image``)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PySide6.QtCore import QObject, QPointF, QRectF, QRunnable, Qt, QThreadPool, Signal
from PySide6.QtGui import QBrush, QColor, QImage, QPainter, QPixmap, QTransform
from PySide6.QtWidgets import (
    QButtonGroup,
    QFileDialog,
    QGridLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from opent5.gui import theme
from opent5.gui.backend import EditError, ImageData, Ref, ZoneDoc
from opent5.gui.views.base import AssetView, hbox, vbox

CHECKER = 8
ZOOM_MIN, ZOOM_MAX = 1 / 64, 64.0
CHANNELS = ("RGB", "R", "G", "B", "A")


def compose(rgba: np.ndarray, channel: str, blend: bool) -> np.ndarray:
    """The RGBA pixels to draw for a channel choice. With ``blend`` the alpha
    channel is kept (drawn over the checkerboard); without it the result is opaque."""
    out = np.empty_like(rgba)
    if channel == "RGB":
        out[..., :3] = rgba[..., :3]
    elif channel == "A":
        out[..., :3] = rgba[..., 3:4]
    else:
        k = "RGB".index(channel)
        out[..., :3] = rgba[..., k : k + 1]
    out[..., 3] = rgba[..., 3] if blend and channel != "A" else 255
    return out


def to_qimage(rgba: np.ndarray) -> QImage:
    rgba = np.ascontiguousarray(rgba, np.uint8)
    h, w = rgba.shape[:2]
    img = QImage(rgba.data, w, h, w * 4, QImage.Format.Format_RGBA8888)
    return img.copy()  # own the bytes


def from_qimage(img: QImage) -> np.ndarray:
    img = img.convertToFormat(QImage.Format.Format_RGBA8888)
    w, h = img.width(), img.height()
    buf = np.frombuffer(img.constBits(), np.uint8, img.bytesPerLine() * h)
    return buf.reshape(h, img.bytesPerLine())[:, : w * 4].reshape(h, w, 4).copy()


class Canvas(QWidget):
    """Draws one QImage with zoom (fit, 1:1, wheel) and drag-to-pan."""

    def __init__(self, view: ImageView):
        super().__init__(view)
        self.view = view
        self.image: QImage | None = None
        self.rgba: np.ndarray | None = None
        self.zoom = 1.0
        self.fit = True
        self.pan = QPointF(0, 0)  # image-space centre offset in widget pixels
        self.message = ""
        self._drag: QPointF | None = None
        self.setMouseTracking(True)
        self.setMinimumSize(120, 120)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        theme.on_change(lambda _t: self.update(), self)

    def set_image(self, image: QImage | None, rgba: np.ndarray | None, keep_view=False) -> None:
        self.image, self.rgba = image, rgba
        if not keep_view:
            self.fit = True
            self.pan = QPointF(0, 0)
        self.update()

    def fit_zoom(self) -> float:
        if self.image is None or self.image.isNull():
            return 1.0
        m = 16
        sx = (self.width() - m) / max(1, self.image.width())
        sy = (self.height() - m) / max(1, self.image.height())
        return max(ZOOM_MIN, min(sx, sy, 8.0))

    def effective_zoom(self) -> float:
        return self.fit_zoom() if self.fit else self.zoom

    def image_rect(self) -> QRectF:
        z = self.effective_zoom()
        w, h = self.image.width() * z, self.image.height() * z
        x = (self.width() - w) / 2 + self.pan.x()
        y = (self.height() - h) / 2 + self.pan.y()
        return QRectF(round(x), round(y), w, h)

    def paintEvent(self, event) -> None:
        t = theme.current()
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(t.base))
        if self.image is None or self.image.isNull():
            if self.message:
                p.setPen(QColor(t.text_faint))
                p.drawText(
                    self.rect().adjusted(24, 24, -24, -24),
                    Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                    self.message,
                )
            p.end()
            return
        r = self.image_rect()
        clip = r.intersected(QRectF(self.rect()))
        # checkerboard, anchored to the image's corner
        tile = QPixmap(2 * CHECKER, 2 * CHECKER)
        tile.fill(QColor(t.checker_b))
        tp = QPainter(tile)
        a = QColor(t.checker_a)
        tp.fillRect(0, 0, CHECKER, CHECKER, a)
        tp.fillRect(CHECKER, CHECKER, CHECKER, CHECKER, a)
        tp.end()
        brush = QBrush(tile)
        brush.setTransform(QTransform.fromTranslate(r.left(), r.top()))
        p.fillRect(clip, brush)
        smooth = self.effective_zoom() < 1.0
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, smooth)
        p.drawImage(r, self.image)
        p.setPen(QColor(t.border))
        p.drawRect(r.adjusted(-0.5, -0.5, 0.5, 0.5))
        p.end()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.view.update_zoom_label()

    def set_zoom(self, zoom: float, around: QPointF | None = None) -> None:
        if self.image is None:
            return
        old = self.effective_zoom()
        zoom = max(ZOOM_MIN, min(ZOOM_MAX, zoom))
        if around is not None:
            c = QPointF(self.width() / 2, self.height() / 2) + self.pan
            self.pan += (around - c) * (1 - zoom / old)
        else:
            self.pan *= zoom / old
        self.fit = False
        self.zoom = zoom
        self.update()
        self.view.update_zoom_label()

    def show_fit(self) -> None:
        self.fit = True
        self.pan = QPointF(0, 0)
        self.update()
        self.view.update_zoom_label()

    def show_actual(self) -> None:
        self.pan = QPointF(0, 0)
        self.fit = False
        self.zoom = 1.0
        self.update()
        self.view.update_zoom_label()

    def wheelEvent(self, event) -> None:
        steps = event.angleDelta().y() / 120
        if steps:
            self.set_zoom(self.effective_zoom() * (1.25**steps), event.position())

    def mousePressEvent(self, event) -> None:
        if event.button() in (Qt.MouseButton.LeftButton, Qt.MouseButton.MiddleButton):
            self._drag = event.position()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseReleaseEvent(self, event) -> None:
        self._drag = None
        self.unsetCursor()

    def mouseMoveEvent(self, event) -> None:
        pos = event.position()
        if self._drag is not None and self.image is not None:
            if self.fit:
                self.zoom, self.fit = self.fit_zoom(), False
            self.pan += pos - self._drag
            self._drag = pos
            self.update()
        self.view.report_pixel(self.pixel_at(pos))

    def pixel_at(self, pos: QPointF) -> tuple[int, int] | None:
        if self.image is None or self.rgba is None:
            return None
        r = self.image_rect()
        z = self.effective_zoom()
        x, y = int((pos.x() - r.left()) // z), int((pos.y() - r.top()) // z)
        h, w = self.rgba.shape[:2]
        return (x, y) if 0 <= x < w and 0 <= y < h else None


class _Divider(QWidget):
    """A 1px vertical rule with a little air either side, in the border colour."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(11)

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        h = self.height()
        p.fillRect(5, h // 4, 1, h - h // 2, QColor(theme.current().border_strong))
        p.end()


class _Signals(QObject):
    done = Signal(int, object)


class _Decode(QRunnable):
    def __init__(self, doc: ZoneDoc, ref: Ref, token: int, signals: _Signals):
        super().__init__()
        self.doc, self.ref, self.token, self.signals = doc, ref, token, signals

    def run(self) -> None:
        try:
            data = self.doc.image(self.ref)
        except Exception as exc:  # report any decode failure in the view
            data = ImageData({}, None, f"{type(exc).__name__}: {exc}")
        self.signals.done.emit(self.token, data)


INFO_ROWS = (
    ("name", "Name"),
    ("format", "Format"),
    ("size", "Size"),
    ("mips", "Mips"),
    ("cube", "Cube"),
    ("semantic", "Semantic"),
    ("source", "Pixels"),
    ("decoded", "Decoded"),
    ("notes", "Notes"),
)


class InfoPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("InfoPanel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedWidth(232)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 10)
        lay.setSpacing(6)
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(3)
        self.values: dict[str, QLabel] = {}
        self.keys: dict[str, QLabel] = {}
        mono = theme.mono_font()
        for row, (key, label) in enumerate(INFO_ROWS):
            k = QLabel(label)
            k.setObjectName("InfoKey")
            k.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
            v = QLabel("")
            v.setWordWrap(True)
            v.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            v.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
            if key not in ("name", "notes", "source", "semantic"):
                v.setFont(mono)
            grid.addWidget(k, row, 0)
            grid.addWidget(v, row, 1)
            self.values[key] = v
            self.keys[key] = k
        grid.setColumnStretch(1, 1)
        lay.addLayout(grid)
        lay.addStretch(1)
        self.export_btn = QPushButton("Export PNG...")
        self.import_btn = QPushButton("Import PNG/DDS...")
        self.import_note = QLabel("")
        self.import_note.setObjectName("InfoKey")
        self.import_note.setWordWrap(True)
        self.import_note.hide()
        lay.addWidget(self.import_note)
        lay.addWidget(self.export_btn)
        lay.addWidget(self.import_btn)

    def show_info(self, name: str, data: ImageData | None) -> None:
        info = dict(data.info) if data else {}
        w, h, d = info.get("width"), info.get("height"), info.get("depth")
        size = ""
        if w is not None:
            size = f"{w} x {h}" + (f" x {d}" if d and d > 1 else "")
        source = info.get("source", "")
        if data is not None and data.rgba is None:
            source = f"not in this zone: {data.reason}" if data.reason else "not in this zone"
        cube = info.get("cube")
        values = {
            "name": name,
            "format": info.get("format", ""),
            "size": size,
            "mips": "" if info.get("mips") is None else str(info.get("mips")),
            "cube": "" if cube is None else ("yes" if cube else "no"),
            "semantic": str(info.get("semantic", "")),
            "source": source,
            "decoded": info.get("decoded", ""),
            "notes": "; ".join(x for x in (info.get("notes"), info.get("layers")) if x),
        }
        if info.get("layers"):
            values["notes"] = "; ".join(
                x for x in (info.get("notes"), "faces " + info["layers"]) if x
            )
        for key, label in self.values.items():
            text = values.get(key, "")
            label.setText(text)
            label.setVisible(bool(text))
            self.keys[key].setVisible(bool(text))


class ImageView(AssetView):
    kind = "image"
    title = "Image"

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.data: ImageData | None = None
        self.channel = "RGB"
        self.blend = True
        self._token = 0
        self._signals = _Signals()
        self._signals.done.connect(self._decoded)

        bar = QWidget(self)
        bar.setObjectName("ViewBar")
        bar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.fit_btn = self._button("Fit", checkable=False)
        self.actual_btn = self._button("1:1", checkable=False)
        self.zoom_label = QLabel("")
        self.zoom_label.setFont(theme.mono_font())
        self.zoom_label.setMinimumWidth(52)
        self.zoom_label.setObjectName("InfoKey")
        self.channel_group = QButtonGroup(self)
        self.channel_buttons = {}
        for ch in CHANNELS:
            b = self._button(ch)
            self.channel_group.addButton(b)
            self.channel_buttons[ch] = b
        self.channel_buttons["RGB"].setChecked(True)
        self.blend_btn = self._button("Alpha blend")
        self.blend_btn.setChecked(True)
        self.blend_btn.setToolTip("Draw the alpha channel over the checkerboard")
        sep1, sep2 = self._divider(), self._divider()
        self._dividers = (sep1, sep2)
        row = hbox(
            bar,
            self.fit_btn,
            self.actual_btn,
            self.zoom_label,
            sep1,
            *self.channel_buttons.values(),
            sep2,
            self.blend_btn,
            margins=(4, 0, 4, 0),
        )
        row.addStretch(1)

        self.canvas = Canvas(self)
        self.info = InfoPanel(self)
        self.setFocusProxy(self.canvas)
        body = QWidget(self)
        hbox(body, self.canvas, self.info)
        vbox(self, bar, body)

        self.fit_btn.clicked.connect(self.canvas.show_fit)
        self.actual_btn.clicked.connect(self.canvas.show_actual)
        for ch, b in self.channel_buttons.items():
            b.clicked.connect(lambda _c=False, ch=ch: self.set_channel(ch))
        self.blend_btn.toggled.connect(self.set_blend)
        self.info.export_btn.clicked.connect(self.export_png)
        self.info.import_btn.clicked.connect(self.import_file)

    def _button(self, text: str, checkable: bool = True) -> QToolButton:
        b = QToolButton(self)
        b.setText(text)
        b.setCheckable(checkable)
        b.setAutoRaise(True)
        b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        return b

    def _divider(self) -> QWidget:
        return _Divider(self)

    # loading
    def load(self, doc: ZoneDoc, ref: Ref) -> None:
        super().load(doc, ref)
        self._token += 1
        self.data = None
        self.canvas.message = "Decoding..."
        self.canvas.set_image(None, None)
        self.info.show_info(ref.label, None)
        self._update_buttons()
        QThreadPool.globalInstance().start(_Decode(doc, ref, self._token, self._signals))

    def load_sync(self, doc: ZoneDoc, ref: Ref) -> None:
        AssetView.load(self, doc, ref)
        self._token += 1
        self.show_data(doc.image(ref))

    def _decoded(self, token: int, data: ImageData) -> None:
        if token == self._token:
            self.show_data(data)

    def show_data(self, data: ImageData, keep_view: bool = False) -> None:
        self.data = data
        self.info.show_info(self.ref.label if self.ref else "", data)
        if data.rgba is None:
            self.canvas.message = data.reason or "No pixels."
            self.canvas.set_image(None, None)
        else:
            self.canvas.message = ""
            self._redraw(keep_view)
        self._update_buttons()
        self.update_zoom_label()

    def _redraw(self, keep_view: bool = True) -> None:
        if self.data is None or self.data.rgba is None:
            return
        rgba = self.data.rgba
        self.canvas.set_image(
            to_qimage(compose(rgba, self.channel, self.blend)), rgba, keep_view=keep_view
        )

    def _update_buttons(self) -> None:
        has = self.data is not None and self.data.rgba is not None
        self.info.export_btn.setEnabled(has)
        ok, why = True, ""
        if self.doc is not None and self.ref is not None:
            info = self.data.info if self.data is not None else None
            ok, why = self.doc.can_replace_image(self.ref, info)
        self.info.import_btn.setEnabled(has and ok)
        self.info.import_btn.setVisible(ok)
        self.info.import_note.setText(f"Not replaceable: {why}" if not ok else "")
        self.info.import_note.setVisible(not ok and bool(why))
        self.info.import_btn.setToolTip(
            why if not ok else "Replace the pixels with a PNG or DDS of the same size and format"
        )
        for b in (*self.channel_buttons.values(), self.blend_btn, self.fit_btn, self.actual_btn):
            b.setEnabled(has)

    # controls
    def set_channel(self, channel: str) -> None:
        self.channel = channel
        self.channel_buttons[channel].setChecked(True)
        self._redraw()

    def set_blend(self, on: bool) -> None:
        self.blend = on
        if self.blend_btn.isChecked() != on:
            self.blend_btn.setChecked(on)
        self._redraw()

    def update_zoom_label(self) -> None:
        if self.canvas.image is None:
            self.zoom_label.setText("")
            return
        z = self.canvas.effective_zoom() * 100
        self.zoom_label.setText(f"{z:.0f}%" if z >= 1 else f"{z:.1f}%")

    def report_pixel(self, xy: tuple[int, int] | None) -> None:
        zoom = self.zoom_label.text()
        if xy is None or self.data is None or self.data.rgba is None:
            self.status.emit(f"zoom {zoom}" if zoom else "")
            return
        x, y = xy
        r, g, b, a = (int(v) for v in self.data.rgba[y, x])
        self.status.emit(f"zoom {zoom} · x {x} y {y} · rgba {r} {g} {b} {a}")

    # export / import
    def export_png(self) -> None:
        if self.data is None or self.data.rgba is None:
            return
        stem = (self.ref.name if self.ref else "image").lstrip(",").replace("/", "_")
        path, _ = QFileDialog.getSaveFileName(
            self, "Export PNG", f"{stem}.png", "PNG images (*.png)"
        )
        if path:
            self.write_png(Path(path))

    def write_png(self, path: Path) -> None:
        from opent5.formats import texture as tx

        path.write_bytes(tx.write_png(np.ascontiguousarray(self.data.rgba, np.uint8)))

    def import_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Import image", "", "Images (*.png *.dds);;PNG (*.png);;DDS (*.dds)"
        )
        if path:
            self.import_path(Path(path))

    def import_path(self, path: Path) -> bool:
        if self.doc is None or self.ref is None or self.data is None or self.data.rgba is None:
            return False
        h, w = self.data.rgba.shape[:2]
        if path.suffix.lower() == ".dds":
            payload = path.read_bytes()
        else:
            img = QImage(str(path))
            if img.isNull():
                QMessageBox.warning(self, "Import image", f"{path.name}: not a readable image.")
                return False
            if (img.width(), img.height()) != (w, h):
                QMessageBox.warning(
                    self,
                    "Import image",
                    f"{path.name}: expected {w} x {h}, found {img.width()} x {img.height()}.",
                )
                return False
            payload = from_qimage(img)
        try:
            self.doc.replace_image(self.ref, payload)
        except EditError as exc:
            QMessageBox.warning(self, "Import image", str(exc))
            return False
        self.edited.emit()
        if isinstance(payload, np.ndarray):
            self.show_data(ImageData(self.data.info, payload, None), keep_view=True)
        return True


__all__ = ["ImageView", "compose", "to_qimage", "from_qimage"]
