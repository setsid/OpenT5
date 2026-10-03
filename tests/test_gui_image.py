"""Image view: channel composition and drawing with a stub document."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from opent5.gui.backend import ImageData, Ref  # noqa: E402
from opent5.gui.views.image import ImageView, compose, from_qimage, to_qimage  # noqa: E402

app = QApplication.instance() or QApplication([])


def pixels():
    rgba = np.zeros((32, 48, 4), np.uint8)
    rgba[..., 0] = 200
    rgba[..., 1] = 50
    rgba[..., 2] = 10
    rgba[..., 3] = np.arange(48, dtype=np.uint8)[None, :] * 5
    return rgba


class StubDoc:
    def __init__(self, data):
        self.data = data
        self.replaced = None

    def image(self, ref):
        return self.data

    def can_replace_image(self, ref, info=None):
        if info and info.get("replaceable") is False:
            return False, str(info.get("not_replaceable", ""))
        return True, ""

    def replace_image(self, ref, payload, resize=False):
        self.replaced = payload
        self.resize = resize
        if resize:
            h, w = payload.shape[:2]
            self.data = ImageData(dict(self.data.info, width=w, height=h), payload)


REF = Ref(3, 10, "image", "test_image", 100, ("image", "fields", "hex"), 0x100)


def test_compose_channels():
    rgba = pixels()
    assert (compose(rgba, "R", False)[..., :3] == 200).all()
    assert (compose(rgba, "G", False)[..., 2] == 50).all()
    a = compose(rgba, "A", True)
    assert (a[..., 0] == rgba[..., 3]).all() and (a[..., 3] == 255).all()
    assert (compose(rgba, "RGB", True)[..., 3] == rgba[..., 3]).all()
    assert (compose(rgba, "RGB", False)[..., 3] == 255).all()


def test_qimage_round_trip():
    rgba = pixels()
    assert (from_qimage(to_qimage(rgba)) == rgba).all()


def test_view_channels_change_drawing(tmp_path):
    doc = StubDoc(ImageData({"format": "DXT1", "width": 48, "height": 32}, pixels()))
    v = ImageView()
    v.resize(500, 300)
    v.show()
    v.load_sync(doc, REF)
    app.processEvents()
    first = v.canvas.grab().toImage()
    v.set_channel("G")
    app.processEvents()
    second = v.canvas.grab().toImage()
    assert first != second
    assert v.info.values["format"].text() == "DXT1"
    assert v.info.export_btn.isEnabled() and v.info.import_btn.isEnabled()
    out = tmp_path / "x.png"
    v.write_png(out)
    assert out.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert v.import_path(out)
    assert doc.replaced.shape == (32, 48, 4)


def test_view_without_pixels():
    doc = StubDoc(ImageData({"format": "DXT1"}, None, "streamed; no part readable"))
    v = ImageView()
    v.load_sync(doc, REF)
    assert v.canvas.image is None
    assert "no part readable" in v.canvas.message
    assert "no part readable" in v.info.values["source"].text()
    assert not v.info.export_btn.isEnabled()


def test_a_streamed_image_asks_before_taking_another_size(tmp_path):
    info = {"format": "DXT1", "width": 48, "height": 32, "pixels": "pak"}
    doc = StubDoc(ImageData(info, pixels()))
    v = ImageView()
    v.load_sync(doc, REF)
    big = tmp_path / "big.png"
    from opent5.formats import texture as tx

    big.write_bytes(tx.write_png(np.zeros((64, 96, 4), np.uint8)))
    asked = []
    v.ask_resize = lambda old, new: asked.append((old, new)) or False
    assert not v.import_path(big) and doc.replaced is None
    assert asked == [((48, 32), (96, 64))]
    v.ask_resize = lambda old, new: True
    assert v.import_path(big) and doc.resize
    assert v.data.info["width"] == 96 and v.data.rgba.shape == (64, 96, 4)
