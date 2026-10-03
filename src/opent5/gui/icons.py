"""Monochrome line icons drawn with QPainter, and the application mark.

Icons are drawn on a 16-unit grid with a 1.2-unit pen in the theme's text
colour, so they follow the theme. ``icon(name)`` is cached per theme.

The application mark (``resources/opent5.svg``) is a filled square with a
bevelled corner and a geometric "T5" cut out of it; ``write_app_icon`` renders
it with Qt to PNGs at 16, 24, 32, 48, 64 and 256 px and writes them into
``opent5.ico`` (PNG-compressed ICO entries).
"""

from __future__ import annotations

import struct
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QImage, QPainter, QPainterPath, QPen, QPixmap

from opent5.gui import theme

RESOURCES = Path(__file__).resolve().parent / "resources"
APP_SVG = RESOURCES / "opent5.svg"
APP_ICO = RESOURCES / "opent5.ico"
ICO_SIZES = (16, 24, 32, 48, 64, 256)

#: The mark on a 64 grid: square with the bottom-right corner bevelled, and a
#: T and a 5 cut out (even-odd fill).
MARK_SQUARE = [(4, 4), (60, 4), (60, 50), (50, 60), (4, 60)]
MARK_T = [(12, 14), (30, 14), (30, 20), (24, 20), (24, 50), (18, 50), (18, 20), (12, 20)]
MARK_5 = [
    (34, 14),
    (52, 14),
    (52, 20),
    (40, 20),
    (40, 29),
    (52, 29),
    (52, 50),
    (34, 50),
    (34, 44),
    (46, 44),
    (46, 35),
    (34, 35),
]
MARK_COLOUR = "#c4a062"


def _svg_poly(points) -> str:
    return "M" + " L".join(f"{x} {y}" for x, y in points) + " Z"


def app_svg(colour: str = MARK_COLOUR) -> str:
    d = " ".join(_svg_poly(p) for p in (MARK_SQUARE, MARK_T, MARK_5))
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" viewBox="0 0 64 64">\n'
        f'  <path fill="{colour}" fill-rule="evenodd" d="{d}"/>\n'
        "</svg>\n"
    )


def mark_path(scale: float = 1.0) -> QPainterPath:
    path = QPainterPath()
    path.setFillRule(Qt.FillRule.OddEvenFill)
    for poly in (MARK_SQUARE, MARK_T, MARK_5):
        path.moveTo(poly[0][0] * scale, poly[0][1] * scale)
        for x, y in poly[1:]:
            path.lineTo(x * scale, y * scale)
        path.closeSubpath()
    return path


def render_mark(size: int, colour: str = MARK_COLOUR) -> QImage:
    image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    p = QPainter(image)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, size >= 32)
    p.fillPath(mark_path(size / 64.0), QColor(colour))
    p.end()
    return image


def png_bytes(image: QImage) -> bytes:
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buf, "PNG")
    buf.close()
    return bytes(data)


def ico_bytes(pngs: list[tuple[int, bytes]]) -> bytes:
    """An ICO file whose entries are PNG streams (valid since Windows Vista)."""
    head = struct.pack("<HHH", 0, 1, len(pngs))
    offset = 6 + 16 * len(pngs)
    entries, blobs = b"", b""
    for size, data in pngs:
        dim = 0 if size >= 256 else size
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(data), offset)
        blobs += data
        offset += len(data)
    return head + entries + blobs


def write_app_icon(folder: Path = RESOURCES) -> tuple[Path, Path]:
    folder.mkdir(parents=True, exist_ok=True)
    svg = folder / APP_SVG.name
    svg.write_text(app_svg())
    ico = folder / APP_ICO.name
    ico.write_bytes(ico_bytes([(s, png_bytes(render_mark(s))) for s in ICO_SIZES]))
    return svg, ico


def app_icon() -> QIcon:
    out = QIcon()
    for s in ICO_SIZES:
        out.addPixmap(QPixmap.fromImage(render_mark(s)))
    return out


# -- line icons ----------------------------------------------------------------------------


def _lines(p: QPainter, *segs) -> None:
    for x1, y1, x2, y2 in segs:
        p.drawLine(QPointF(x1, y1), QPointF(x2, y2))


def _poly(p: QPainter, pts, close=False) -> None:
    path = QPainterPath(QPointF(*pts[0]))
    for pt in pts[1:]:
        path.lineTo(QPointF(*pt))
    if close:
        path.closeSubpath()
    p.drawPath(path)


def _open(p):
    _poly(p, [(1.5, 3.5), (6, 3.5), (7.5, 5), (14.5, 5), (14.5, 13.5), (1.5, 13.5)], True)


def _save(p):
    _poly(p, [(2, 2.5), (12, 2.5), (14, 4.5), (14, 13.5), (2, 13.5)], True)
    p.drawRect(QRectF(5, 2.5, 5, 3.5))
    p.drawRect(QRectF(4.5, 9, 7, 4.5))


def _undo(p):
    path = QPainterPath(QPointF(3, 7))
    path.cubicTo(QPointF(6, 3.5), QPointF(13, 4), QPointF(13.5, 10))
    p.drawPath(path)
    _poly(p, [(3, 3), (3, 7), (7, 7)])


def _redo(p):
    p.save()
    p.translate(16, 0)
    p.scale(-1, 1)
    _undo(p)
    p.restore()


def _search(p):
    p.drawEllipse(QRectF(2.5, 2.5, 8, 8))
    _lines(p, (9.5, 9.5, 13.5, 13.5))


def _palette(p):
    p.drawRect(QRectF(1.5, 3.5, 13, 9))
    _poly(p, [(4, 6), (6, 8), (4, 10)])
    _lines(p, (8, 10, 11.5, 10))


def _hex(p):
    p.drawRect(QRectF(1.5, 2.5, 13, 11))
    _lines(p, (4, 6, 6, 6), (8, 6, 10, 6), (4, 10, 6, 10), (8, 10, 10, 10), (12, 6, 12.5, 6))


def _fields(p):
    _lines(p, (2, 3.5, 5, 3.5), (5, 7.5, 9, 7.5), (5, 11.5, 9, 11.5), (3.5, 3.5, 3.5, 11.5))
    _lines(
        p, (3.5, 7.5, 5, 7.5), (3.5, 11.5, 5, 11.5), (10.5, 7.5, 14, 7.5), (10.5, 11.5, 14, 11.5)
    )


def _text(p):
    _lines(p, (2, 3.5, 14, 3.5), (2, 6.5, 11, 6.5), (2, 9.5, 14, 9.5), (2, 12.5, 9, 12.5))


def _image(p):
    p.drawRect(QRectF(1.5, 2.5, 13, 11))
    _poly(p, [(1.5, 11), (6, 7), (9, 10), (11, 8), (14.5, 11.5)])
    p.drawEllipse(QRectF(9.5, 4.5, 2, 2))


def _mesh(p):
    _poly(p, [(8, 1.5), (14, 5), (14, 11), (8, 14.5), (2, 11), (2, 5)], True)
    _lines(p, (2, 5, 8, 8.5), (14, 5, 8, 8.5), (8, 8.5, 8, 14.5))


def _table(p):
    p.drawRect(QRectF(1.5, 2.5, 13, 11))
    _lines(p, (1.5, 6, 14.5, 6), (1.5, 9.75, 14.5, 9.75), (6, 2.5, 6, 13.5))


def _frame(p):
    _poly(p, [(1.5, 5), (1.5, 1.5), (5, 1.5)])
    _poly(p, [(11, 1.5), (14.5, 1.5), (14.5, 5)])
    _poly(p, [(14.5, 11), (14.5, 14.5), (11, 14.5)])
    _poly(p, [(5, 14.5), (1.5, 14.5), (1.5, 11)])
    p.drawRect(QRectF(5.5, 5.5, 5, 5))


def _changes(p):
    _lines(p, (3, 4, 8, 4), (5.5, 1.5, 5.5, 6.5), (3, 11.5, 8, 11.5))
    _lines(p, (10.5, 2, 10.5, 14))


def _close(p):
    _lines(p, (4.5, 4.5, 11.5, 11.5), (11.5, 4.5, 4.5, 11.5))


def _up(p):
    _poly(p, [(4, 10), (8, 6), (12, 10)])


def _down(p):
    _poly(p, [(4, 6), (8, 10), (12, 6)])


def _export(p):
    _lines(p, (8, 2, 8, 10))
    _poly(
        p,
        [
            (
                5,
                5,
            ),
            (8, 2),
            (11, 5),
        ],
    )
    _poly(p, [(2.5, 9), (2.5, 13.5), (13.5, 13.5), (13.5, 9)])


def _import(p):
    _lines(p, (8, 2, 8, 10))
    _poly(p, [(5, 7), (8, 10), (11, 7)])
    _poly(p, [(2.5, 9), (2.5, 13.5), (13.5, 13.5), (13.5, 9)])


DRAW = {
    "open": _open,
    "save": _save,
    "undo": _undo,
    "redo": _redo,
    "search": _search,
    "palette": _palette,
    "hex": _hex,
    "fields": _fields,
    "text": _text,
    "image": _image,
    "mesh": _mesh,
    "table": _table,
    "frame": _frame,
    "changes": _changes,
    "close": _close,
    "up": _up,
    "down": _down,
    "export": _export,
    "import": _import,
}

_cache: dict[tuple[str, str, str], QIcon] = {}


def draw_pixmap(name: str, colour: str, size: int = 16, ratio: float = 2.0) -> QPixmap:
    px = QPixmap(int(size * ratio), int(size * ratio))
    px.fill(Qt.GlobalColor.transparent)
    p = QPainter(px)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.scale(size * ratio / 16.0, size * ratio / 16.0)
    pen = QPen(QColor(colour), 1.2)
    pen.setCapStyle(Qt.PenCapStyle.SquareCap)
    pen.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    DRAW[name](p)
    p.end()
    px.setDevicePixelRatio(ratio)
    return px


def icon(name: str) -> QIcon:
    t = theme.current()
    key = (name, t.text, t.text_faint)
    if key not in _cache:
        out = QIcon()
        out.addPixmap(draw_pixmap(name, t.text_dim), QIcon.Mode.Normal)
        out.addPixmap(draw_pixmap(name, t.text), QIcon.Mode.Active)
        out.addPixmap(draw_pixmap(name, t.text_faint), QIcon.Mode.Disabled)
        _cache[key] = out
    return _cache[key]
