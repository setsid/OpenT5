"""The GUI side of update checks (opent5.update does the work, in a worker thread).

    window.updates = updates.attach(window)   # menu items, palette entries, status strip
    window.updates.start()                    # only from a real app start (__main__)

Adds to the Help menu (and so to the command palette): "Check for Updates Now", "Check
for Updates on Start" (a setting, on by default) and "Restart to Update". A verified
update shows as a small strip in the status bar: "Version X is ready. Restart to
update." with the release notes (plain text) one click away. A verification failure
shows there too, and that version is remembered and never tried again. Nothing modal
appears unless the person clicks something.
"""

from __future__ import annotations

import logging
import threading

from PySide6.QtCore import QObject, QSettings, Signal
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QToolButton,
    QVBoxLayout,
)

import opent5
from opent5.update import check, swap

log = logging.getLogger("opent5.update")

ON_START = "updates/check_on_start"
FAILED = "updates/failed_versions"


def failed_versions(settings: QSettings) -> set[str]:
    raw = settings.value(FAILED, "")
    if isinstance(raw, list | tuple):
        raw = ",".join(str(x) for x in raw)
    return {v for v in str(raw or "").split(",") if v}


def remember_failed(settings: QSettings, version: str) -> None:
    versions = failed_versions(settings) | {version}
    settings.setValue(FAILED, ",".join(sorted(versions)))


class _Bridge(QObject):
    done = Signal(object)


class Strip(QFrame):
    """The status bar notice: a message, What's New, Restart and a close button."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("updateStrip")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(6, 0, 2, 0)
        lay.setSpacing(4)
        self.label = QLabel("")
        self.notes_button = QToolButton()
        self.notes_button.setText("What's New")
        self.restart_button = QToolButton()
        self.restart_button.setText("Restart to Update")
        self.close_button = QToolButton()
        self.close_button.setText("×")
        self.close_button.setToolTip("Dismiss")
        self.close_button.setAutoRaise(True)
        self.close_button.clicked.connect(self.hide)
        for w in (self.label, self.notes_button, self.restart_button, self.close_button):
            lay.addWidget(w)
        self.hide()

    def show_ready(self, text: str) -> None:
        self.label.setText(text)
        self.notes_button.show()
        self.restart_button.show()
        self.show()

    def show_problem(self, text: str) -> None:
        self.label.setText(text)
        self.notes_button.hide()
        self.restart_button.hide()
        self.show()


class Updates(QObject):
    def __init__(self, window, check_fn=None):
        super().__init__(window)
        self.window = window
        self.settings: QSettings = window.settings
        self.check_fn = check_fn or check.check
        self.outcome: check.Outcome | None = None
        self.busy = False
        self.started = 0  # checks started, for tests
        self._thread: threading.Thread | None = None
        self._bridge = _Bridge()
        self._bridge.done.connect(self._finished)

        A = window._action  # noqa: N806 - the window's action factory registers palette items
        self.a_check = A("Help", "Check for Updates Now", lambda: self.check(manual=True))
        self.a_on_start = A(
            "Help", "Check for Updates on Start", self._set_on_start, checkable=True
        )
        self.a_on_start.setChecked(self.check_on_start())
        self.a_restart = A("Help", "Restart to Update", self.restart)
        self.a_restart.setEnabled(False)
        menu = _help_menu(window)
        if menu is not None:
            menu.addSeparator()
            menu.addActions([self.a_check, self.a_on_start, self.a_restart])

        self.strip = Strip()
        self.strip.notes_button.clicked.connect(self.show_notes)
        self.strip.restart_button.clicked.connect(self.restart)
        window.statusBar().addPermanentWidget(self.strip)
        self._notes: QDialog | None = None

    # -- settings ---------------------------------------------------------------------

    def check_on_start(self) -> bool:
        value = self.settings.value(ON_START, True)
        return value if isinstance(value, bool) else str(value).lower() in ("true", "1")

    def _set_on_start(self, on: bool) -> None:
        self.settings.setValue(ON_START, bool(on))

    # -- checking ---------------------------------------------------------------------

    def start(self) -> None:
        """App start: remove a left-over .old exe, then check if the setting allows."""
        threading.Thread(target=swap.cleanup_old, name="opent5-update-cleanup", daemon=True).start()
        if self.check_on_start():
            self.check(manual=False)
        else:
            log.info("update check on start is off")

    def check(self, manual: bool = False) -> None:
        if self.busy:
            return
        self.busy = True
        self.started += 1
        if manual:
            self.window.message("Checking for updates...")
        failed = failed_versions(self.settings)
        self._thread = threading.Thread(
            target=self._run, args=(failed, manual), name="opent5-update", daemon=True
        )
        self._thread.start()

    def _run(self, failed: set[str], manual: bool) -> None:
        try:
            outcome = self.check_fn(failed=failed)
        except Exception as exc:  # noqa: BLE001 - a check must never take the app down
            log.exception("update check crashed")
            outcome = check.Outcome("error", f"update check failed: {exc!r}")
        self._bridge.done.emit((outcome, manual))

    def _finished(self, payload) -> None:
        outcome, manual = payload
        self.busy = False
        if outcome.status == "ready":
            self.outcome = outcome
            self.a_restart.setEnabled(True)
            self.strip.show_ready(outcome.message)
        elif outcome.status == "failed-verification":
            remember_failed(self.settings, outcome.version)
            self.strip.show_problem(
                f"The update to {outcome.version} failed verification and was discarded."
            )
            self.strip.setToolTip(outcome.message)
        elif manual:
            self.window.message(outcome.message[:1].upper() + outcome.message[1:], 10000)

    # -- notes and restart ------------------------------------------------------------

    def show_notes(self) -> None:
        if self.outcome is None:
            return
        if self._notes is None:
            self._notes = QDialog(self.window)
            self._notes.setModal(False)
            lay = QVBoxLayout(self._notes)
            self._notes_text = QPlainTextEdit()
            self._notes_text.setReadOnly(True)  # plain text: no links, no remote images
            lay.addWidget(self._notes_text)
            self._notes.resize(560, 420)
        self._notes.setWindowTitle(f"{opent5.APP_NAME} {self.outcome.version}: What's New")
        self._notes_text.setPlainText(self.outcome.notes or "This release has no notes.")
        self._notes.show()
        self._notes.raise_()

    def restart(self) -> None:
        out = self.outcome
        if out is None or out.status != "ready" or out.path is None:
            return
        if out.test_mode:
            self.window.message("Updates found through the test override are never installed.")
            return
        exe = swap.running_exe()
        if exe is None:
            QMessageBox.information(
                self.window,
                "Restart to Update",
                f"{opent5.APP_NAME} is running from source, so there is no exe to replace "
                "and restarting does nothing. Update the source instead (git pull).",
            )
            return
        app = QApplication.instance()
        quit_on_close = app.quitOnLastWindowClosed()
        app.setQuitOnLastWindowClosed(False)
        if not self.window.close():  # unsaved edits: the person chose to stay
            app.setQuitOnLastWindowClosed(quit_on_close)
            return
        try:
            swap.apply(out.path, exe, out.manifest["size"], out.manifest["sha256"])
        except swap.SwapError as exc:
            app.setQuitOnLastWindowClosed(quit_on_close)
            self.window.show()
            QMessageBox.warning(self.window, "Update Failed", str(exc))
            return
        app.quit()


def _help_menu(window) -> QMenu | None:
    # findChildren, not QAction.menu(): PySide 6.8 can hand back a wrapper whose QMenu
    # it then reports as deleted.
    for m in window.menuBar().findChildren(QMenu):
        if m.title().replace("&", "") == "Help":
            return m
    return None


def attach(window, check_fn=None) -> Updates:
    return Updates(window, check_fn)
