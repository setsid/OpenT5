"""python -m opent5.gui [zone.ff ...]"""

from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    import opent5

    parser = argparse.ArgumentParser(
        prog="python -m opent5.gui", description=f"{opent5.APP_NAME} GUI"
    )
    parser.add_argument("zones", nargs="*", help="zones (.ff) to open")
    parser.add_argument("--theme", choices=("dark", "light"), help="colour theme")
    # Smoke test for packaged builds: render, save the window to PATH and exit. Run with
    # QT_QPA_PLATFORM=offscreen so nothing appears on screen.
    parser.add_argument("--screenshot", metavar="PATH", help=argparse.SUPPRESS)
    parser.add_argument("--after", type=float, default=8.0, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    from PySide6.QtWidgets import QApplication

    from opent5.gui import icons, theme
    from opent5.gui.mainwindow import MainWindow

    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName(opent5.APP_NAME)
    app.setOrganizationName(opent5.APP_NAME)
    app.setApplicationVersion(opent5.__version__)
    app.setWindowIcon(icons.app_icon())
    font = app.font()
    font.setPointSize(theme.UI_POINT_SIZE)
    app.setFont(font)
    window = MainWindow()
    if args.theme:
        window._apply_theme(args.theme)
    window.resize(1440, 900)
    window.show()
    if args.zones:
        window.open_paths(args.zones)
    if args.screenshot:
        from PySide6.QtCore import QTimer

        def grab() -> None:
            window.grab().save(args.screenshot)
            app.quit()

        QTimer.singleShot(int(args.after * 1000), grab)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
