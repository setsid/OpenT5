"""Hex view: the pure model and an offscreen paint."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from opent5.gui.views.hex import HexModel, HexView  # noqa: E402

app = QApplication.instance() or QApplication([])


def test_model_rows_and_text():
    data = bytes(range(40))
    m = HexModel(data, base=0x1000)
    assert m.row_count == 3
    assert m.row_bytes(2) == bytes(range(32, 40))
    assert m.offset_of(1, 3) == 19
    line = m.text(0)
    assert line.startswith("00001000  00 01 02 03 04 05 06 07  08 09")
    assert m.text(0, relative=True).startswith("00000000")
    assert m.ascii_text(2) == " !\"#$%&'"
    assert m.ascii_text(0) == "." * 16


def test_model_find_and_inspect():
    m = HexModel(b"\0\0abc\x3f\x80\0\0abc")
    assert m.find(b"abc") == 2
    assert m.find(b"abc", 3) == 9
    assert m.find(b"zz") == -1
    text = m.inspect(5)
    assert "u32 BE 0x3f800000" in text and "f32 BE 1" in text


def test_large_buffer_is_cheap():
    m = HexModel(bytes(64 * 1024 * 1024))
    assert m.row_count == 4 * 1024 * 1024
    assert m.text(m.row_count - 1).startswith("03fffff0")


def test_view_paints_and_selects():
    v = HexView()
    v.resize(700, 300)
    v.set_data(bytes(range(256)) * 64, base=0x40)
    v.show()
    app.processEvents()
    seen = []
    v.status.connect(seen.append)
    v.area.anchor, v.area.cursor = 4, 11
    v.area.changed()
    assert v.area.selection() == (4, 12)
    assert seen and "sel 0x44-0x4c (8 bytes)" in seen[-1]
    img = v.grab().toImage()
    colours = {img.pixel(x, y) for x in range(0, 600, 7) for y in range(0, 200, 5)}
    assert len(colours) > 3
    v.area.goto(0x3000)
    assert v.area.verticalScrollBar().value() > 0


def test_view_inline_empty_state():
    v = HexView()
    v.set_data(None)
    assert v.stack.currentWidget() is v.empty
