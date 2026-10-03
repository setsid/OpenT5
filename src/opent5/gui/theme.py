"""Colours, fonts and the one stylesheet.

Every colour the GUI paints is a named token on a ``Theme``. Widgets that paint
themselves (hex view, image checkerboard, mesh view, gutter) read the tokens
from ``current()``; everything else gets them through the palette and the
stylesheet built here. One accent colour, used for selection and focus only.
"""

from __future__ import annotations

from dataclasses import dataclass, fields

import shiboken6
from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette
from PySide6.QtWidgets import QApplication

#: Monospace families in order of preference; the first installed one wins.
MONO_FAMILIES = ("Cascadia Mono", "Consolas", "JetBrains Mono", "DejaVu Sans Mono")
MONO_POINT_SIZE = 9
UI_POINT_SIZE = 9


@dataclass(frozen=True)
class Theme:
    name: str
    # surfaces, darkest to lightest (dark theme) or the reverse (light)
    base: str  # editors, lists, grids
    window: str  # the main background
    panel: str  # headers, toolbar, status bar, tab bar
    raised: str  # hovered rows and buttons
    border: str  # 1px dividers
    border_strong: str  # input frames
    # text
    text: str
    text_dim: str
    text_faint: str
    # the accent: selection and focus only
    accent: str
    accent_text: str  # text on a solid accent fill
    selection: str  # selected rows / text background
    selection_inactive: str
    current_line: str
    # semantic
    modified: str  # unsaved markers
    error: str
    diff_add: str
    diff_del: str
    # syntax
    syn_keyword: str
    syn_string: str
    syn_comment: str
    syn_number: str
    syn_builtin: str
    syn_preproc: str
    # data views
    hex_offset: str
    hex_zero: str
    checker_a: str
    checker_b: str
    wire: str  # mesh edges
    wire_far: str
    grid: str

    def color(self, token: str) -> QColor:
        return QColor(getattr(self, token))

    def tokens(self) -> dict[str, str]:
        return {f.name: getattr(self, f.name) for f in fields(self) if f.name != "name"}


DARK = Theme(
    name="dark",
    base="#151617",
    window="#1b1c1e",
    panel="#202124",
    raised="#2a2b2f",
    border="#2f3135",
    border_strong="#3d3f44",
    text="#c9cbce",
    text_dim="#8b8e94",
    text_faint="#5c5f65",
    accent="#c4a062",
    accent_text="#151617",
    selection="#3a3428",
    selection_inactive="#2c2b29",
    current_line="#1c1d1f",
    modified="#c4a062",
    error="#cf7468",
    diff_add="#22301f",
    diff_del="#3a2321",
    syn_keyword="#b39ddb",
    syn_string="#a5b878",
    syn_comment="#6b6f76",
    syn_number="#d19a66",
    syn_builtin="#7fb0c4",
    syn_preproc="#c47f7f",
    hex_offset="#6b6f76",
    hex_zero="#4a4d52",
    checker_a="#2a2b2e",
    checker_b="#202124",
    wire="#b9bcc2",
    wire_far="#3a3d42",
    grid="#26282b",
)

LIGHT = Theme(
    name="light",
    base="#ffffff",
    window="#f4f4f2",
    panel="#ebebe8",
    raised="#e0e0dc",
    border="#d4d4cf",
    border_strong="#b9b9b3",
    text="#1d1e20",
    text_dim="#5c5f65",
    text_faint="#94969b",
    accent="#8c6418",
    accent_text="#ffffff",
    selection="#f0e3c6",
    selection_inactive="#e8e6e0",
    current_line="#f7f6f2",
    modified="#8c6418",
    error="#b23b2e",
    diff_add="#e3f0dc",
    diff_del="#f6dfdc",
    syn_keyword="#6a3fa8",
    syn_string="#4f7a1f",
    syn_comment="#8a8d92",
    syn_number="#a8561a",
    syn_builtin="#1f6a8a",
    syn_preproc="#a03838",
    hex_offset="#94969b",
    hex_zero="#c2c3c6",
    checker_a="#e6e6e3",
    checker_b="#fafaf8",
    wire="#2b2d31",
    wire_far="#c9cace",
    grid="#ecece8",
)

THEMES = {"dark": DARK, "light": LIGHT}
_current: Theme = DARK
_listeners: list = []


def current() -> Theme:
    return _current


def on_change(callback, owner=None) -> None:
    """Call ``callback(theme)`` after every theme switch (custom-painted widgets).
    With ``owner`` (a QObject) the callback is dropped once the owner is deleted."""
    _listeners.append((callback, owner))


def mono_family() -> str:
    installed = set(QFontDatabase.families())
    for family in MONO_FAMILIES:
        if family in installed:
            return family
    return QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont).family()


def mono_font(size: int = MONO_POINT_SIZE) -> QFont:
    font = QFont(mono_family(), size)
    font.setStyleHint(QFont.StyleHint.Monospace)
    font.setFixedPitch(True)
    return font


def palette(t: Theme) -> QPalette:
    p = QPalette()
    roles = QPalette.ColorRole
    pairs = {
        roles.Window: t.window,
        roles.WindowText: t.text,
        roles.Base: t.base,
        roles.AlternateBase: t.panel,
        roles.ToolTipBase: t.panel,
        roles.ToolTipText: t.text,
        roles.PlaceholderText: t.text_faint,
        roles.Text: t.text,
        roles.Button: t.panel,
        roles.ButtonText: t.text,
        roles.BrightText: t.error,
        roles.Highlight: t.selection,
        roles.HighlightedText: t.text,
        roles.Link: t.accent,
        roles.Mid: t.border,
        roles.Dark: t.border_strong,
        roles.Midlight: t.raised,
        roles.Light: t.raised,
        roles.Shadow: t.base,
    }
    for role, value in pairs.items():
        p.setColor(role, QColor(value))
    p.setColor(QPalette.ColorGroup.Inactive, roles.Highlight, QColor(t.selection_inactive))
    for role in (roles.Text, roles.WindowText, roles.ButtonText):
        p.setColor(QPalette.ColorGroup.Disabled, role, QColor(t.text_faint))
    return p


def stylesheet(t: Theme) -> str:
    """The single stylesheet. Flat surfaces, 1px dividers, accent on selection/focus."""
    return f"""
* {{ outline: 0; }}
QWidget {{ color: {t.text}; }}
QMainWindow, QDialog {{ background: {t.window}; }}
QMainWindow::separator {{ background: {t.border}; width: 1px; height: 1px; }}
QSplitter::handle {{ background: {t.border}; }}
QSplitter::handle:horizontal {{ width: 1px; }}
QSplitter::handle:vertical {{ height: 1px; }}
QToolTip {{ background: {t.panel}; color: {t.text}; border: 1px solid {t.border_strong};
           padding: 2px 4px; }}

QMenuBar {{ background: {t.panel}; border-bottom: 1px solid {t.border}; padding: 0; }}
QMenuBar::item {{ padding: 3px 8px; background: transparent; }}
QMenuBar::item:selected {{ background: {t.raised}; }}
QMenu {{ background: {t.panel}; border: 1px solid {t.border_strong}; padding: 2px 0; }}
QMenu::item {{ padding: 3px 24px 3px 20px; }}
QMenu::item:selected {{ background: {t.selection}; }}
QMenu::item:disabled {{ color: {t.text_faint}; }}
QMenu::separator {{ height: 1px; background: {t.border}; margin: 2px 0; }}
QMenu::indicator {{ width: 10px; height: 10px; left: 5px; }}

QToolBar {{ background: {t.panel}; border: 0; border-bottom: 1px solid {t.border};
           padding: 1px 4px; spacing: 1px; }}
QToolBar::separator {{ background: {t.border}; width: 1px; margin: 4px 4px; }}
QToolButton {{ background: transparent; border: 1px solid transparent; padding: 2px 6px;
              color: {t.text}; }}
QToolButton:hover {{ background: {t.raised}; }}
QToolButton:pressed, QToolButton:checked {{ background: {t.selection}; }}
QToolButton:disabled {{ color: {t.text_faint}; }}
QToolButton:focus {{ border: 1px solid {t.accent}; }}

QStatusBar {{ background: {t.panel}; border-top: 1px solid {t.border}; color: {t.text_dim}; }}
QStatusBar::item {{ border: 0; }}
QStatusBar QLabel {{ color: {t.text_dim}; padding: 0 8px; border-left: 1px solid {t.border}; }}

QTabWidget::pane {{ border: 0; border-top: 1px solid {t.border}; }}
QTabBar {{ background: {t.panel}; }}
QTabBar::tab {{ background: {t.panel}; color: {t.text_dim}; padding: 4px 10px;
               border: 0; border-right: 1px solid {t.border};
               border-top: 1px solid transparent; }}
QTabBar::tab:selected {{ background: {t.window}; color: {t.text};
                        border-top: 1px solid {t.accent}; }}
QTabBar::tab:hover:!selected {{ color: {t.text}; }}
QTabBar::tab:selected QToolButton {{ background: transparent; }}
#TabClose {{ border: 0; padding: 0; margin: 0; background: transparent; }}
#TabClose:hover {{ background: {t.raised}; }}

QTreeView, QListView, QTableView, QPlainTextEdit, QTextEdit {{
    background: {t.base}; border: 0; selection-background-color: {t.selection};
    selection-color: {t.text}; }}
QTreeView::item, QListView::item {{ padding: 1px 2px; border: 0; }}
QTreeView::item:hover, QListView::item:hover {{ background: {t.raised}; }}
QTreeView::item:selected, QListView::item:selected, QTableView::item:selected {{
    background: {t.selection}; color: {t.text}; }}
QTableView {{ gridline-color: {t.border}; }}
QHeaderView {{ background: {t.panel}; border: 0; }}
QHeaderView::section {{ background: {t.panel}; color: {t.text_dim}; border: 0;
    border-right: 1px solid {t.border}; border-bottom: 1px solid {t.border};
    padding: 2px 6px; }}
QTableCornerButton::section {{ background: {t.panel}; border: 0;
    border-right: 1px solid {t.border}; border-bottom: 1px solid {t.border}; }}

QLineEdit, QComboBox, QSpinBox {{ background: {t.base}; border: 1px solid {t.border_strong};
    padding: 2px 4px; selection-background-color: {t.selection}; selection-color: {t.text}; }}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus {{ border: 1px solid {t.accent}; }}
QComboBox::drop-down {{ border: 0; width: 14px; }}
QComboBox QAbstractItemView {{ background: {t.panel}; border: 1px solid {t.border_strong}; }}

QPushButton {{ background: {t.panel}; border: 1px solid {t.border_strong};
    padding: 3px 12px; min-width: 64px; }}
QPushButton:hover {{ background: {t.raised}; }}
QPushButton:pressed {{ background: {t.selection}; }}
QPushButton:focus, QPushButton:default {{ border: 1px solid {t.accent}; }}
QPushButton:disabled {{ color: {t.text_faint}; }}

QCheckBox {{ spacing: 4px; }}
QScrollBar:vertical {{ background: {t.base}; width: 10px; margin: 0; border: 0;
    border-left: 1px solid {t.border}; }}
QScrollBar:horizontal {{ background: {t.base}; height: 10px; margin: 0; border: 0;
    border-top: 1px solid {t.border}; }}
QScrollBar::handle {{ background: {t.border_strong}; min-height: 20px; min-width: 20px; }}
QScrollBar::handle:hover {{ background: {t.text_faint}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}
QProgressBar {{ background: {t.base}; border: 1px solid {t.border}; max-height: 6px;
    text-align: center; }}
QProgressBar::chunk {{ background: {t.text_dim}; }}

/* named surfaces */
#PanelHeader {{ background: {t.panel}; border-bottom: 1px solid {t.border}; }}
#PanelHeader QLabel {{ color: {t.text_dim}; }}
#AssetHeader {{ background: {t.panel}; border-bottom: 1px solid {t.border}; }}
#AssetTitle {{ color: {t.text}; }}
#AssetMeta {{ color: {t.text_dim}; }}
#FindBar {{ background: {t.panel}; border-top: 1px solid {t.border}; }}
#FindBar QLineEdit {{ padding: 1px 4px; }}
#Palette {{ background: {t.panel}; border: 1px solid {t.border_strong}; }}
#Palette QLineEdit {{ border: 0; border-bottom: 1px solid {t.border}; padding: 6px 8px;
    background: {t.panel}; }}
#Palette QListView {{ background: {t.panel}; }}
#EmptyState {{ color: {t.text_faint}; }}
#InfoPanel {{ background: {t.panel}; border-left: 1px solid {t.border}; }}
#InfoPanel QLabel {{ color: {t.text}; }}
#InfoKey {{ color: {t.text_dim}; }}
#ReportBody {{ background: {t.base}; border: 1px solid {t.border}; }}
#Notice {{ color: {t.text}; border-left: 2px solid {t.accent}; padding: 4px 8px;
    background: {t.panel}; }}
#ViewBar {{ background: {t.panel}; border-bottom: 1px solid {t.border}; }}
#ViewBar QToolButton {{ padding: 2px 8px; color: {t.text_dim}; }}
#ViewBar QToolButton:checked {{ color: {t.text}; background: {t.window};
    border-bottom: 1px solid {t.accent}; }}
"""


def apply(name: str, app: QApplication | None = None) -> Theme:
    global _current
    _current = THEMES[name]
    app = app or QApplication.instance()
    if app is not None:
        if app.style().name().lower() != "fusion":
            app.setStyle("Fusion")
        app.setPalette(palette(_current))
        app.setStyleSheet(stylesheet(_current))
    for entry in list(_listeners):
        callback, owner = entry
        if owner is not None and not shiboken6.isValid(owner):
            _listeners.remove(entry)
            continue
        try:
            callback(_current)
        except RuntimeError:  # a deleted widget
            _listeners.remove(entry)
    return _current
