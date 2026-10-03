"""The main window: zones as tabs, menus, toolbar, bottom panels, status bar."""

from __future__ import annotations

import traceback
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QSettings, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QAction, QActionGroup, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QSplitter,
    QTabBar,
    QTabWidget,
    QToolBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

import opent5
from opent5 import env
from opent5.gui import icons, theme
from opent5.gui.backend import EditError, SaveReport, ZoneDoc
from opent5.gui.dialogs import AboutDialog, SaveReportDialog, ShortcutsDialog
from opent5.gui.palette import Item, Palette
from opent5.gui.panels import ChangesPanel, SearchPanel
from opent5.gui.tree import human_size
from opent5.gui.zonepage import VIEWS, ZonePage

MAX_RECENT = 12


class _Signals(QObject):
    progress = Signal(str, float)
    done = Signal(object)
    failed = Signal(str)


class Task(QRunnable):
    """Runs ``fn(progress)`` on the thread pool; reports through Qt signals."""

    def __init__(self, fn):
        super().__init__()
        self.fn = fn
        self.signals = _Signals()

    def run(self) -> None:
        try:
            result = self.fn(lambda m, f: self.signals.progress.emit(m, f))
        except Exception as exc:  # shown to the user, with the reason
            detail = "".join(traceback.format_exception_only(type(exc), exc)).strip()
            self.signals.failed.emit(detail)
            return
        self.signals.done.emit(result)


class StartPage(QWidget):
    """Shown with no zone open: recent files and the zones configured in .env."""

    open_path = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        title = QLabel(opent5.APP_NAME)
        f = title.font()
        f.setPointSize(f.pointSize() + 6)
        title.setFont(f)
        hint = QLabel(
            "Open a zone with Ctrl+O, drop a .ff file on this window, or pick one below. "
            "Zones are only read; edits are saved to a new file."
        )
        hint.setObjectName("AssetMeta")
        hint.setWordWrap(True)
        self.recent = self._list()
        self.zones = self._list()
        cols = QHBoxLayout()
        cols.setSpacing(16)
        for label, lst in (("Recent", self.recent), ("Configured zone folders", self.zones)):
            box = QVBoxLayout()
            box.setSpacing(4)
            k = QLabel(label)
            k.setObjectName("InfoKey")
            box.addWidget(k)
            box.addWidget(lst, 1)
            cols.addLayout(box, 1)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(32, 28, 32, 24)
        lay.setSpacing(8)
        lay.addWidget(title)
        lay.addWidget(hint)
        lay.addSpacing(10)
        lay.addLayout(cols, 1)

    def _list(self) -> QListWidget:
        lst = QListWidget()
        lst.setFont(theme.mono_font())
        lst.setUniformItemSizes(True)
        lst.itemActivated.connect(lambda it: self.open_path.emit(it.data(Qt.ItemDataRole.UserRole)))
        return lst

    def fill(self, recent: list[str]) -> None:
        self.recent.clear()
        for p in recent:
            it = QListWidgetItem(Path(p).name + "    " + str(Path(p).parent))
            it.setData(Qt.ItemDataRole.UserRole, p)
            it.setToolTip(p)
            self.recent.addItem(it)
        if not recent:
            it = QListWidgetItem("nothing opened yet")
            it.setFlags(Qt.ItemFlag.NoItemFlags)
            self.recent.addItem(it)

    def scan_zones(self) -> None:
        """List the configured zone folders on the thread pool (a mounted drive can
        take a second to stat), then fill the list."""
        self.zones.clear()
        it = QListWidgetItem("reading the zone folders...")
        it.setFlags(Qt.ItemFlag.NoItemFlags)
        self.zones.addItem(it)
        self._scan = Task(lambda _progress: zone_rows())
        self._scan.signals.done.connect(self._show_zones)
        QThreadPool.globalInstance().start(self._scan)

    def _show_zones(self, rows: list[tuple[str, str]]) -> None:
        self.zones.clear()
        for text, path in rows:
            it = QListWidgetItem(text)
            it.setData(Qt.ItemDataRole.UserRole, path)
            it.setToolTip(path)
            self.zones.addItem(it)
        if not rows:
            it = QListWidgetItem("no zone folders configured (see .env.example)")
            it.setFlags(Qt.ItemFlag.NoItemFlags)
            self.zones.addItem(it)


def zone_rows() -> list[tuple[str, str]]:
    """(list text, path) for every configured zone: name, size, which folder."""
    roots = [
        (env.path_of(k), label)
        for k, label in (
            ("OPENT5_ZONES", "disc"),
            ("OPENT5_PATCH_ZONES", "update"),
            ("OPENT5_DLC_ZONES", "dlc"),
        )
    ]
    out = []
    for z in env.all_zones():
        where = ""
        for root, label in roots:
            if root is not None and z.is_relative_to(root):
                rel = z.parent.relative_to(root).as_posix()
                where = label if rel == "." else rel
                break
        try:
            size = human_size(z.stat().st_size)
        except OSError:
            size = ""
        out.append((f"{z.stem:<28} {size:>8}   {where}", str(z)))
    return out


class MainWindow(QMainWindow):
    def __init__(self, settings: QSettings | None = None):
        super().__init__()
        self.settings = settings or QSettings(opent5.APP_NAME, opent5.APP_NAME)
        self.setWindowTitle(opent5.APP_NAME)
        self.setWindowIcon(icons.app_icon())
        self.setAcceptDrops(True)
        self.pool = QThreadPool.globalInstance()
        self._tasks: list[Task] = []
        self.registry: list[tuple[str, QAction]] = []  # (menu group, action)
        #: Ask before closing a zone with unsaved edits (off for scripted runs).
        self.ask_before_discard = True

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.setTabsClosable(False)  # own close buttons, drawn with the theme
        self.tabs.setMovable(True)
        self.tabs.tabCloseRequested.connect(self.close_tab)
        self.tabs.currentChanged.connect(self._tab_changed)
        self.start = StartPage()
        self.start.scan_zones()
        self.start.open_path.connect(lambda p: self.open_paths([p]))

        self.search = SearchPanel()
        self.search.jump.connect(self._jump_hit)
        self.changes = ChangesPanel()
        self.changes.jump.connect(self._jump_change)
        self.bottom = QTabWidget()
        self.bottom.setDocumentMode(True)
        self.bottom.addTab(self.search, "Search")
        self.bottom.addTab(self.changes, "Changes")

        self.centre = QSplitter(Qt.Orientation.Vertical)
        self.centre.setHandleWidth(1)
        self.centre.setChildrenCollapsible(False)
        self.main_stack = QWidget()
        ms = QVBoxLayout(self.main_stack)
        ms.setContentsMargins(0, 0, 0, 0)
        ms.setSpacing(0)
        ms.addWidget(self.start)
        ms.addWidget(self.tabs)
        self.tabs.hide()
        self.centre.addWidget(self.main_stack)
        self.centre.addWidget(self.bottom)
        self.centre.setStretchFactor(0, 1)
        self.centre.setSizes([700, 220])
        self.bottom.hide()
        self.setCentralWidget(self.centre)

        self.palette = Palette(self)
        self._build_actions()
        self._build_menus()
        self._build_toolbar()
        self._build_status()
        self._apply_theme(self.settings.value("theme", "dark"))
        self._refresh_recent()
        self._update_state()

    # -- actions --------------------------------------------------------------------------

    def _action(
        self,
        group: str,
        text: str,
        slot,
        shortcut=None,
        icon: str | None = None,
        checkable: bool = False,
    ) -> QAction:
        a = QAction(text, self)
        if shortcut is not None:
            keys = shortcut if isinstance(shortcut, list) else [shortcut]
            a.setShortcuts([QKeySequence(k) for k in keys])
        if icon:
            a.setIcon(icons.icon(icon))
            a.setData(icon)
        a.setCheckable(checkable)
        a.triggered.connect(slot)
        self.addAction(a)
        self.registry.append((group, a))
        return a

    def _build_actions(self) -> None:
        A = self._action  # noqa: N806
        self.a_open = A("File", "Open...", self.open_dialog, QKeySequence.StandardKey.Open, "open")
        self.a_close = A(
            "File", "Close Zone", lambda: self.close_tab(self.tabs.currentIndex()), "Ctrl+W"
        )
        self.a_save = A("File", "Save", self.save, QKeySequence.StandardKey.Save, "save")
        self.a_save_as = A("File", "Save As...", self.save_as, "Ctrl+Shift+S", "save")
        self.a_quit = A("File", "Quit", self.close, QKeySequence.StandardKey.Quit)
        self.a_undo = A("Edit", "Undo", self.undo, "Ctrl+Z", "undo")
        self.a_redo = A("Edit", "Redo", self.redo, ["Ctrl+Shift+Z", "Ctrl+Y"], "redo")
        self.a_search = A("Edit", "Search in Zone", self.show_search, "Ctrl+Shift+F", "search")
        self.a_quick = A("Go", "Quick Open Asset...", self.quick_open, "Ctrl+P")
        self.a_palette = A(
            "Go", "Command Palette...", self.command_palette, "Ctrl+Shift+P", "palette"
        )
        self.a_tree = A("Go", "Focus Asset Tree", self.focus_tree, "Ctrl+1")
        self.a_filter = A("Go", "Filter Assets", self.focus_filter, "Ctrl+L")
        self.a_next_zone = A("Go", "Next Zone", lambda: self._cycle(1), "Ctrl+Tab")
        self.a_prev_zone = A("Go", "Previous Zone", lambda: self._cycle(-1), "Ctrl+Shift+Tab")
        self.a_changes = A("View", "Changes", self.show_changes, "Ctrl+Shift+D", "changes")
        self.a_panel = A("View", "Toggle Bottom Panel", self.toggle_panel, "Ctrl+J")
        self.a_world = A("View", "World Geometry", lambda: self.open_geometry("gfx_map"))
        self.a_collision = A("View", "Collision", lambda: self.open_geometry("col_map_mp"))
        self.view_actions: dict[str, QAction] = {}
        for i, kind in enumerate(VIEWS, 1):
            self.view_actions[kind] = A(
                "View",
                f"{VIEWS[kind][2]} View",
                lambda _c=False, k=kind: self.set_view(k),
                f"Alt+{i}",
            )
        self.theme_group = QActionGroup(self)
        self.a_dark = A("View", "Dark Theme", lambda: self._apply_theme("dark"), checkable=True)
        self.a_light = A("View", "Light Theme", lambda: self._apply_theme("light"), checkable=True)
        for a in (self.a_dark, self.a_light):
            self.theme_group.addAction(a)
        self.a_shortcuts = A("Help", "Keyboard Shortcuts", self.show_shortcuts, "F1")
        self.a_about = A("Help", f"About {opent5.APP_NAME}", self.show_about)

    def _build_menus(self) -> None:
        mb = self.menuBar()
        m = mb.addMenu("&File")
        m.addAction(self.a_open)
        self.recent_menu = m.addMenu("Open Recent")
        m.addSeparator()
        m.addActions([self.a_save, self.a_save_as])
        m.addSeparator()
        m.addActions([self.a_close, self.a_quit])
        m = mb.addMenu("&Edit")
        m.addActions([self.a_undo, self.a_redo])
        m.addSeparator()
        m.addAction(self.a_search)
        m = mb.addMenu("&View")
        tm = m.addMenu("Theme")
        tm.addActions([self.a_dark, self.a_light])
        m.addSeparator()
        m.addActions(list(self.view_actions.values()))
        m.addSeparator()
        m.addActions([self.a_world, self.a_collision])
        m.addSeparator()
        m.addActions([self.a_changes, self.a_panel])
        m = mb.addMenu("&Go")
        m.addActions([self.a_quick, self.a_palette])
        m.addSeparator()
        m.addActions([self.a_tree, self.a_filter, self.a_next_zone, self.a_prev_zone])
        m = mb.addMenu("&Help")
        m.addActions([self.a_shortcuts, self.a_about])

    def _build_toolbar(self) -> None:
        tb = QToolBar("Main")
        tb.setMovable(False)
        tb.setFloatable(False)
        tb.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        tb.setIconSize(tb.iconSize().scaled(16, 16, Qt.AspectRatioMode.KeepAspectRatio))
        for a in (self.a_open, self.a_save_as):
            tb.addAction(a)
        tb.addSeparator()
        tb.addAction(self.a_undo)
        tb.addAction(self.a_redo)
        tb.addSeparator()
        tb.addAction(self.a_search)
        tb.addAction(self.a_changes)
        spacer = QWidget()
        spacer.setSizePolicy(
            spacer.sizePolicy().horizontalPolicy().Expanding, spacer.sizePolicy().verticalPolicy()
        )
        tb.addWidget(spacer)
        tb.addAction(self.a_palette)
        for a in tb.actions():
            w = tb.widgetForAction(a)
            if isinstance(w, QToolButton):
                keys = a.shortcut().toString(QKeySequence.SequenceFormat.NativeText)
                w.setToolTip(f"{a.text().rstrip('.')}  {keys}".strip())
        self.addToolBar(tb)
        self.toolbar = tb

    def _build_status(self) -> None:
        sb = self.statusBar()
        sb.setSizeGripEnabled(False)
        self.st_message = QLabel("")
        self.st_message.setStyleSheet("border-left: 0")
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setFixedWidth(140)
        self.progress.setTextVisible(False)
        self.progress.hide()
        self.st_zone = QLabel("")
        self.st_assets = QLabel("")
        self.st_sel = QLabel("")
        self.st_sel.setFont(theme.mono_font())
        self.st_state = QLabel("")
        self.st_backend = QLabel("")
        sb.addWidget(self.st_message, 1)
        sb.addWidget(self.progress)
        for w in (self.st_sel, self.st_zone, self.st_assets, self.st_state, self.st_backend):
            sb.addPermanentWidget(w)

    # -- theme ----------------------------------------------------------------------------

    def _apply_theme(self, name: str) -> None:
        name = name if name in theme.THEMES else "dark"
        theme.apply(name)
        self.settings.setValue("theme", name)
        (self.a_dark if name == "dark" else self.a_light).setChecked(True)
        for _g, a in self.registry:
            if a.data():
                a.setIcon(icons.icon(a.data()))
        bar = self.tabs.tabBar()
        for i in range(bar.count()):
            b = bar.tabButton(i, QTabBar.ButtonPosition.RightSide)
            if b is not None:
                b.setIcon(icons.icon("close"))

    # -- opening --------------------------------------------------------------------------

    def open_dialog(self) -> None:
        start = self.settings.value("last_dir", "") or (
            str(env.zone_dirs()[0]) if env.zone_dirs() else str(Path.home())
        )
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Open zone", start, "Zones (*.ff);;All files (*)"
        )
        if paths:
            self.settings.setValue("last_dir", str(Path(paths[0]).parent))
            self.open_paths(paths)

    def open_paths(self, paths, sync: bool = False) -> list[ZonePage]:
        pages = []
        for p in paths:
            path = Path(p)
            existing = self._page_for(path)
            if existing is not None:
                self.tabs.setCurrentWidget(existing)
                continue
            if not path.is_file():
                self.message(f"Not a file: {path}")
                continue
            if sync:
                doc = ZoneDoc.open(path)
                pages.append(self._add_doc(doc))
            else:
                self._open_async(path)
        return pages

    def _open_async(self, path: Path) -> None:
        task = Task(lambda progress: ZoneDoc.open(path, progress))
        self._tasks.append(task)
        self.progress.setValue(0)
        self.progress.show()
        self.message(f"Opening {path.name}...")
        task.signals.progress.connect(
            lambda m, f: (self.progress.setValue(int(f * 100)), self.message(f"{path.name}: {m}"))
        )
        task.signals.done.connect(lambda doc: self._opened(task, doc))
        task.signals.failed.connect(lambda err: self._open_failed(task, path, err))
        self.pool.start(task)

    def _opened(self, task: Task, doc: ZoneDoc) -> None:
        self._tasks.remove(task)
        if not self._tasks:
            self.progress.hide()
        self._add_doc(doc)
        self.message(f"Opened {doc.zone_name}: {len(doc.refs)} assets in {doc.load_seconds:.1f} s")

    def _open_failed(self, task: Task, path: Path, err: str) -> None:
        self._tasks.remove(task)
        if not self._tasks:
            self.progress.hide()
        self.message(f"Could not open {path.name}")
        QMessageBox.warning(self, "Could not open zone", f"{path}\n\n{err}")

    def _add_doc(self, doc: ZoneDoc) -> ZonePage:
        page = ZonePage(doc)
        page.edited.connect(lambda p=page: self._edited(p))
        page.status.connect(self.st_sel.setText)
        page.selected.connect(lambda _r: self._update_state())
        i = self.tabs.addTab(page, doc.zone_name)
        close = QToolButton(self.tabs.tabBar())
        page.close_button = close  # keep the wrapper alive with the page
        close.setObjectName("TabClose")
        close.setAutoRaise(True)
        close.setIcon(icons.icon("close"))
        close.setToolTip("Close zone (Ctrl+W)")
        close.setFixedSize(16, 16)
        close.clicked.connect(lambda _c=False, p=page: self.close_tab(self.tabs.indexOf(p)))
        self.tabs.tabBar().setTabButton(i, QTabBar.ButtonPosition.RightSide, close)
        self.tabs.setTabToolTip(i, str(doc.path))
        self.tabs.setCurrentIndex(i)
        self._remember(doc.path)
        self.start.hide()
        self.tabs.show()
        self._update_state()
        return page

    def _page_for(self, path: Path) -> ZonePage | None:
        for i in range(self.tabs.count()):
            page = self.tabs.widget(i)
            if page.doc.path.resolve() == path.resolve():
                return page
        return None

    def _remember(self, path: Path) -> None:
        recent = [p for p in self._recent() if p != str(path)]
        recent.insert(0, str(path))
        self.settings.setValue("recent", recent[:MAX_RECENT])
        self._refresh_recent()

    def _recent(self) -> list[str]:
        value = self.settings.value("recent", [])
        if isinstance(value, str):
            value = [value]
        return [str(v) for v in (value or [])]

    def _refresh_recent(self) -> None:
        self.recent_menu.clear()
        recent = self._recent()
        for p in recent:
            a = self.recent_menu.addAction(p)
            a.triggered.connect(lambda _c=False, p=p: self.open_paths([p]))
        self.recent_menu.setEnabled(bool(recent))
        self.start.fill(recent)

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasUrls() and any(u.isLocalFile() for u in event.mimeData().urls()):
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # noqa: N802
        paths = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
        if paths:
            self.open_paths(paths)
            event.acceptProposedAction()

    # -- tabs and state -------------------------------------------------------------------

    def page(self) -> ZonePage | None:
        w = self.tabs.currentWidget()
        return w if isinstance(w, ZonePage) else None

    def _cycle(self, step: int) -> None:
        n = self.tabs.count()
        if n:
            self.tabs.setCurrentIndex((self.tabs.currentIndex() + step) % n)

    def close_tab(self, index: int) -> None:
        page = self.tabs.widget(index)
        if not isinstance(page, ZonePage):
            return
        page.commit()
        if page.doc.dirty and not self._confirm_discard([page]):
            return
        self.tabs.removeTab(index)
        page.doc.close()
        page.deleteLater()
        if not self.tabs.count():
            self.tabs.hide()
            self.start.show()
            self._refresh_recent()
        self._update_state()

    def _confirm_discard(self, pages: list[ZonePage]) -> bool:
        if not self.ask_before_discard:
            return True
        names = ", ".join(p.doc.zone_name for p in pages)
        answer = QMessageBox.question(
            self,
            "Unsaved changes",
            f"{names} has unsaved changes. Close and discard them?",
            QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        return answer == QMessageBox.StandardButton.Discard

    def closeEvent(self, event) -> None:  # noqa: N802
        dirty = []
        for i in range(self.tabs.count()):
            page = self.tabs.widget(i)
            page.commit()
            if page.doc.dirty:
                dirty.append(page)
        if dirty and not self._confirm_discard(dirty):
            event.ignore()
            return
        event.accept()

    def _tab_changed(self, _i: int) -> None:
        page = self.page()
        self.search.set_doc(page.doc if page else None)
        self.changes.set_doc(page.doc if page else None)
        self._update_state()

    def _edited(self, page: ZonePage) -> None:
        if page is self.page():
            self.changes.reload()
        self._update_state()

    def _update_state(self) -> None:
        page = self.page()
        doc = page.doc if page else None
        for i in range(self.tabs.count()):
            p = self.tabs.widget(i)
            self.tabs.setTabText(i, p.doc.zone_name + (" *" if p.doc.dirty else ""))
        has = doc is not None
        for a in (
            self.a_close,
            self.a_save_as,
            self.a_save,
            self.a_search,
            self.a_quick,
            self.a_tree,
            self.a_filter,
            self.a_world,
            self.a_collision,
            self.a_changes,
        ):
            a.setEnabled(has)
        self.a_undo.setEnabled(has and doc.can_undo)
        self.a_redo.setEnabled(has and doc.can_redo)
        ref = page.ref if page else None
        for kind, a in self.view_actions.items():
            a.setEnabled(bool(ref) and kind in page.available(ref))
        if not has:
            self.setWindowTitle(opent5.APP_NAME)
            for w in (self.st_zone, self.st_assets, self.st_state, self.st_backend, self.st_sel):
                w.setText("")
                w.hide()
            return
        for w in (self.st_zone, self.st_assets, self.st_state, self.st_backend, self.st_sel):
            w.show()
        self.setWindowTitle(f"{doc.zone_name}{' *' if doc.dirty else ''} - {opent5.APP_NAME}")
        self.st_zone.setText(doc.zone_name)
        self.st_assets.setText(f"{len(doc.refs)} assets, {len(doc.inline_refs)} inline")
        n = len(doc.changes()) if doc.dirty else 0
        signed = "signed" if doc.signed else "unsigned"
        self.st_state.setText(
            f"{signed} · {n} change{'s' if n != 1 else ''}" if n else f"{signed} · unmodified"
        )
        self.st_backend.setText("edit API" if doc.backend == "edit" else "read adapter")
        if ref is None:
            self.st_sel.setText("")

    def message(self, text: str, timeout: int = 0) -> None:
        self.st_message.setText(text)
        if timeout:
            QTimer.singleShot(timeout, lambda: self.st_message.setText(""))

    # -- editing --------------------------------------------------------------------------

    def undo(self) -> None:
        self._undo_redo(True)

    def redo(self) -> None:
        self._undo_redo(False)

    def _undo_redo(self, undo: bool) -> None:
        page = self.page()
        if page is None:
            return
        page.commit()
        doc = page.doc
        if not (doc.can_undo if undo else doc.can_redo):
            return
        key = doc.undo() if undo else doc.redo()
        ref = doc.ref(key)
        if ref is not None and (page.ref is None or page.ref.key != ref.key):
            page.open_ref(ref)
        page.refresh()
        self.changes.reload()
        self._update_state()
        self.message(("Undid" if undo else "Redid") + " an edit", 3000)

    def save(self) -> None:
        page = self.page()
        if page is None:
            return
        if page.doc.saved_path is None:
            self.save_as()
        else:
            self._save_to(page, page.doc.saved_path)

    def save_as(self) -> None:
        page = self.page()
        if page is None:
            return
        page.commit()
        doc = page.doc
        if not doc.can_save:
            QMessageBox.information(
                self,
                "Save As",
                "Saving needs the opent5.edit package, which is not in this build yet. "
                "Your edits stay in this window (see the Changes panel) until you close it.",
            )
            return
        default_dir = self.settings.value("save_dir", "") or str(env.ROOT / "out")
        suggestion = str(Path(default_dir) / f"{doc.path.stem}_edited.ff")
        path, _ = QFileDialog.getSaveFileName(
            self, "Save zone as (a new file)", suggestion, "Zones (*.ff)"
        )
        if not path:
            return
        self.settings.setValue("save_dir", str(Path(path).parent))
        self._save_to(page, Path(path))

    def _save_to(self, page: ZonePage, path: Path, sync: bool = False) -> SaveReport | None:
        doc = page.doc
        if sync:
            report = doc.save(path, verify=True)
            self._saved(page, report)
            return report
        task = Task(lambda progress: doc.save(path, verify=True))
        self._tasks.append(task)
        self.progress.setRange(0, 0)
        self.progress.show()
        self.message(f"Saving and verifying {path.name}...")

        def done(report):
            self._tasks.remove(task)
            self.progress.setRange(0, 100)
            self.progress.hide()
            self._saved(page, report)

        def failed(err):
            self._tasks.remove(task)
            self.progress.setRange(0, 100)
            self.progress.hide()
            self.message("Save failed")
            QMessageBox.warning(self, "Save failed", err)

        task.signals.done.connect(done)
        task.signals.failed.connect(failed)
        self.pool.start(task)
        return None

    def _saved(self, page: ZonePage, report: SaveReport) -> None:
        self.message(
            f"Saved {report.path.name}: {report.assets_changed} changed, "
            f"{report.assets_checked} checked"
        )
        self._update_state()
        SaveReportDialog(report, self).exec()

    # -- navigation -----------------------------------------------------------------------

    def set_view(self, kind: str) -> None:
        page = self.page()
        if page and page.ref is not None and kind in page.available(page.ref):
            page.set_kind(kind)

    def open_geometry(self, type_name: str) -> None:
        page = self.page()
        if page is None:
            return
        names = {"gfx_map"} if type_name == "gfx_map" else {"col_map_mp", "col_map_sp"}
        ref = next((r for r in page.doc.refs if r.type_name in names), None)
        if ref is None:
            self.message(
                f"This zone has no {'world' if type_name == 'gfx_map' else 'collision'} map", 4000
            )
            return
        page.open_ref(ref, "geometry")

    def focus_tree(self) -> None:
        page = self.page()
        if page:
            page.tree.view.setFocus()

    def focus_filter(self) -> None:
        page = self.page()
        if page:
            page.tree.filter.setFocus()
            page.tree.filter.selectAll()

    def show_search(self) -> None:
        self.bottom.show()
        self.bottom.setCurrentWidget(self.search)
        self.search.focus()

    def show_changes(self) -> None:
        self.bottom.show()
        self.bottom.setCurrentWidget(self.changes)
        self.changes.reload()

    def toggle_panel(self) -> None:
        self.bottom.setVisible(not self.bottom.isVisible())

    def _jump_hit(self, hit) -> None:
        page = self.page()
        if page is None:
            return
        kind = {"text": "text", "cell": "table", "value": "localize"}.get(hit.where)
        page.open_ref(hit.ref, kind)
        view = page.current_view()
        if hit.where == "text" and hasattr(view, "editor"):
            view.editor.go_to_line(hit.line or 1, max(0, hit.column or 0))
            view.editor.setFocus()
        elif hit.where == "cell" and hasattr(view, "select_cell"):
            view.select_cell(hit.row or 0, hit.column or 0)

    def _jump_change(self, change) -> None:
        page = self.page()
        ref = page.doc.ref(change.key) if page else None
        if ref is not None:
            page.open_ref(ref)

    # -- palette ----------------------------------------------------------------------------

    def palette_items(self) -> list[Item]:
        items = []
        for group, a in self.registry:
            keys = a.shortcut().toString(QKeySequence.SequenceFormat.NativeText)
            items.append(Item(f"{group}: {a.text().rstrip('.')}", keys, a, a.isEnabled()))
        page = self.page()
        view = page.current_view() if page else None
        if view is not None:
            for a in view.view_actions():
                keys = a.shortcut().toString(QKeySequence.SequenceFormat.NativeText)
                items.append(Item(f"{view.title}: {a.text().rstrip('.')}", keys, a, a.isEnabled()))
        return items

    def command_palette(self) -> None:
        self.palette.open(self.palette_items(), "Type a command", lambda a: a.trigger())

    def quick_open(self) -> None:
        page = self.page()
        if page is None:
            return
        items = [
            Item(r.label, r.type_name + (" inline" if r.inline else ""), r)
            for r in page.doc.all_refs
        ]
        self.palette.open(items, "Open an asset by name", page.open_ref)

    # -- help -------------------------------------------------------------------------------

    def shortcut_rows(self) -> list[tuple[str, str, str]]:
        rows = []
        for group, a in self.registry:
            keys = ", ".join(
                s.toString(QKeySequence.SequenceFormat.NativeText) for s in a.shortcuts()
            )
            if keys:
                rows.append((group, a.text().rstrip("."), keys))
        rows += [
            ("Text", "Find / Replace", "Ctrl+F, Ctrl+H"),
            ("Text", "Next / previous match", "F3, Shift+F3"),
            ("Hex", "Go to offset", "Ctrl+G"),
            ("Geometry", "Frame all", "F"),
            ("Geometry", "Orbit / pan / zoom", "left drag, right drag, wheel"),
        ]
        return rows

    def show_shortcuts(self) -> None:
        ShortcutsDialog(self.shortcut_rows(), self).exec()

    def show_about(self) -> None:
        AboutDialog(self).exec()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if self.palette.isVisible():
            self.palette._place()


def guard_edit_errors(fn):
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except EditError as exc:
            QMessageBox.warning(QApplication.activeWindow(), "Edit refused", str(exc))
            return None

    return wrapper
