"""The update GUI glue, offscreen: menu and palette entries, the setting, the strip."""

from __future__ import annotations

import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

APP = QApplication.instance() or QApplication([])

from opent5.gui import updates  # noqa: E402
from opent5.gui.mainwindow import MainWindow  # noqa: E402
from opent5.update import check  # noqa: E402


class Recorder:
    def __init__(self, outcome: check.Outcome):
        self.outcome = outcome
        self.calls: list[set[str]] = []

    def __call__(self, failed):
        self.calls.append(set(failed))
        return self.outcome


@pytest.fixture
def window(tmp_path):
    win = MainWindow(QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat))
    win.ask_before_discard = False
    yield win
    win.close()


def _use(win, outcome) -> Recorder:
    rec = Recorder(outcome)
    win.updates.check_fn = rec
    return rec


def _settle(win, seconds: float = 3.0) -> None:
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        APP.processEvents()
        if not win.updates.busy:
            return
        time.sleep(0.005)
    raise AssertionError("the update check did not finish")


def test_menu_and_palette_entries(window):
    names = [i.label for i in window.palette_items()]
    assert "Help: Check for Updates Now" in names
    assert "Help: Check for Updates on Start" in names
    assert "Help: Restart to Update" in names
    assert not window.updates.a_restart.isEnabled()
    help_texts = [a.text() for a in updates._help_menu(window).actions()]
    assert "Check for Updates Now" in help_texts


def test_on_start_setting_defaults_on_and_off_means_no_request(window):
    assert window.updates.check_on_start() and window.updates.a_on_start.isChecked()
    rec = _use(window, check.Outcome("up-to-date", "up to date"))
    window.updates.a_on_start.trigger()  # untick
    assert window.settings.value(updates.ON_START) in (False, "false")
    window.updates.start()
    _settle(window)
    assert rec.calls == [] and window.updates.started == 0


def test_start_checks_quietly_when_on(window):
    rec = _use(window, check.Outcome("error", "update check failed (not-found): 404"))
    window.updates.start()
    _settle(window)
    assert len(rec.calls) == 1
    assert not window.updates.strip.isVisibleTo(window)  # never nags
    assert "404" not in window.st_message.text()


def test_ready_shows_the_strip_and_notes(window):
    out = check.Outcome(
        "ready", "Version 9.0.0 is ready. Restart to update.", "9.0.0", "Fixes.\n<b>x</b>"
    )
    _use(window, out)
    window.updates.check(manual=False)
    _settle(window)
    strip = window.updates.strip
    assert strip.isVisibleTo(window)
    assert strip.label.text() == "Version 9.0.0 is ready. Restart to update."
    assert window.updates.a_restart.isEnabled()
    window.updates.show_notes()
    assert window.updates._notes_text.toPlainText() == "Fixes.\n<b>x</b>"  # shown as text
    assert not window.updates._notes.isModal()
    strip.close_button.click()
    assert not strip.isVisibleTo(window)


def test_failed_verification_is_remembered_and_passed_on(window):
    out = check.Outcome("failed-verification", "bad sig", "9.0.0")
    rec = _use(window, out)
    window.updates.check(manual=False)
    _settle(window)
    assert window.updates.strip.label.text() == (
        "The update to 9.0.0 failed verification and was discarded."
    )
    assert updates.failed_versions(window.settings) == {"9.0.0"}
    window.updates.check(manual=True)
    _settle(window)
    assert rec.calls[-1] == {"9.0.0"}


def test_manual_check_reports_in_the_status_bar(window):
    _use(window, check.Outcome("disabled", "update check disabled: no repository set"))
    window.updates.a_check.trigger()
    _settle(window)
    assert window.st_message.text().startswith("Update check disabled")


def test_crashing_check_is_contained(window):
    def boom(failed):
        raise RuntimeError("bug")

    window.updates.check_fn = boom
    window.updates.check(manual=True)
    _settle(window)
    assert "update check failed" in window.st_message.text().lower()


def test_restart_from_source_explains(window, monkeypatch, tmp_path):
    shown = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a: shown.append(a[2]))
    exe = tmp_path / "x.exe"
    exe.write_bytes(b"x")
    window.updates.outcome = check.Outcome("ready", "r", "9.0.0", path=exe)
    window.updates.restart()
    assert shown and "running from source" in shown[0]


def test_restart_refused_in_test_mode(window, tmp_path):
    exe = tmp_path / "x.exe"
    exe.write_bytes(b"x")
    window.updates.outcome = check.Outcome("ready", "r", "9.0.0", path=exe, test_mode=True)
    window.updates.restart()
    assert "never installed" in window.st_message.text()


def test_window_starts_when_the_updater_cannot_load(monkeypatch, tmp_path):
    """A broken update module (for example a crypto library missing from a frozen build)
    disables update checks; the editor still opens."""
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication

    from opent5.gui import mainwindow, updates

    def broken(window):
        raise ModuleNotFoundError("No module named '_cffi_backend'")

    monkeypatch.setattr(updates, "attach", broken)
    QApplication.instance() or QApplication(["opent5"])
    win = mainwindow.MainWindow(QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat))
    assert win.updates is None
    win.ask_before_discard = False
    win.close()
