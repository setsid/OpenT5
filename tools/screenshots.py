"""Render every GUI view offscreen against real zones into out/screenshots/.

    QT_QPA_PLATFORM=offscreen .venv/bin/python tools/screenshots.py [--only NAME ...]

Zones come from .env (mp_nuked, patch_mp, ui_mp); they are only read. The save
report comes from a real Save As of an edited patch_mp into a temporary folder,
which is removed afterwards, as are the throwaway settings (never the user's).
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from opent5 import env  # noqa: E402

OUT = ROOT / "out" / "screenshots"
SIZE = (1440, 900)
GSC = "maps/mp/gametypes/_globallogic.gsc"


def zone(name: str) -> Path | None:
    for key in ("OPENT5_PATCH_ZONES", "OPENT5_ZONES"):
        folder = env.path_of(key)
        if folder and (folder / f"{name}.ff").is_file():
            return folder / f"{name}.ff"
    return None


def settle(app: QApplication, seconds: float = 0.05) -> None:
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        app.processEvents()
        time.sleep(0.005)


class Shooter:
    def __init__(self, only: list[str] | None):
        from opent5.gui import theme
        from opent5.gui.__main__ import main as _main  # noqa: F401  (import check)
        from opent5.gui.mainwindow import MainWindow

        self.app = QApplication.instance() or QApplication(["opent5"])
        font = self.app.font()
        font.setPointSize(theme.UI_POINT_SIZE)
        self.app.setFont(font)
        self.tmp = tempfile.mkdtemp(prefix="opent5-shots-")
        self.settings = QSettings(str(Path(self.tmp) / "settings.ini"), QSettings.Format.IniFormat)
        self.win = MainWindow(self.settings)
        self.win.resize(*SIZE)
        self.win.show()
        self.only = set(only or [])
        self.written: list[Path] = []
        OUT.mkdir(parents=True, exist_ok=True)
        self.docs: dict[str, object] = {}

    def wanted(self, name: str) -> bool:
        return not self.only or any(o in name for o in self.only)

    def shot(self, name: str, widget=None) -> None:
        settle(self.app, 0.15)
        w = widget or self.win
        path = OUT / f"{name}.png"
        w.grab().save(str(path))
        self.written.append(path)
        print(f"  {path.relative_to(ROOT)}")

    def theme(self, name: str) -> None:
        self.win._apply_theme(name)
        settle(self.app)

    def page(self, zone_name: str):
        from opent5.gui.zonepage import ZonePage

        for i in range(self.win.tabs.count()):
            p = self.win.tabs.widget(i)
            if isinstance(p, ZonePage) and p.doc.zone_name == zone_name:
                self.win.tabs.setCurrentIndex(i)
                return p
        path = zone(zone_name)
        if path is None:
            print(f"  skip: {zone_name}.ff not configured")
            return None
        (p,) = self.win.open_paths([path], sync=True)
        return p

    def select(self, page, name: str, type_name: str | None = None, kind: str | None = None):
        ref = page.find_ref(name, type_name)
        if ref is None:
            raise SystemExit(f"{page.doc.zone_name}: no asset {type_name} {name}")
        page.open_ref(ref, kind)
        view = page.current_view()
        if hasattr(view, "load_sync") and kind in ("image", "geometry"):
            view.load_sync(page.doc, ref)
        settle(self.app, 0.1)
        return ref, view

    def run(self) -> None:
        win = self.win
        if self.wanted("00_start"):
            self.theme("dark")
            end = time.perf_counter() + 20
            while win.start.zones.count() < 2 and time.perf_counter() < end:
                settle(self.app, 0.05)
            self.shot("00_start_dark")

        nuked = self.page("mp_nuked")
        if nuked is not None:
            for t in ("dark", "light"):
                if not self.wanted(f"01_main_{t}"):
                    continue
                self.theme(t)
                nuked.show_ref(None)
                tree = nuked.tree
                tree.view.collapseAll()
                for row in range(tree.proxy.rowCount()):
                    idx = tree.proxy.index(row, 0)
                    if idx.data() in ("rawfile", "stringtable"):
                        tree.view.expand(idx)
                self.shot(f"01_main_{t}")
            self.theme("dark")

        patch = self.page("patch_mp")
        if patch is not None:
            if self.wanted("02_code"):
                ref, view = self.select(
                    patch, "maps/mp/gametypes/_globallogic.gsc", "rawfile", "text"
                )
                view.find_text("level.gameEnded")
                self.shot("02_code_gsc_find_dark")
                view.findbar.close_bar()
                self.theme("light")
                ref, view = self.select(
                    patch, "maps/mp/gametypes/_globallogic.gsc", "rawfile", "text"
                )
                view.findbar.open_find(True)
                view.findbar.find.setText("level.gameEnded")
                view.findbar.replace.setText("level.game_ended")
                view.findbar.find_next()
                self.shot("02_code_gsc_replace_light")
                view.findbar.close_bar()
                self.theme("dark")
            if self.wanted("03_cfg"):
                self.select(patch, "default_xboxlive.cfg", "rawfile", "text")
                self.shot("03_code_cfg_dark")
            if self.wanted("04_stringtable"):
                self.select(patch, "mp/mapstable.csv", "stringtable", "table")
                self.shot("04_stringtable_dark")
                self.theme("light")
                self.shot("04_stringtable_light")
                self.theme("dark")
            if self.wanted("05_localize"):
                loc = next(r for r in patch.doc.refs if r.type_name == "localize")
                patch.open_ref(loc, "localize")
                self.shot("05_localize_dark")
            if self.wanted("08_hex"):
                self.select(patch, GSC, "rawfile", "hex")
                view = patch.current_view()
                if hasattr(view, "select_range"):
                    view.select_range(0x20, 0x3F)
                self.shot("08_hex_dark")
                self.theme("light")
                self.shot("08_hex_light")
                self.theme("dark")
            if self.wanted("09_fields"):
                weapon = next(r for r in patch.doc.refs if r.type_name == "weapon")
                patch.open_ref(weapon, "fields")
                self.shot("09_fields_dark")
            if self.wanted("10_search"):
                win.show_search()
                win.search.query.setText("killstreak")
                win.search.run()
                self.shot("10_search_dark")
                win.bottom.hide()
            if self.wanted("11_palette"):
                self.select(patch, GSC, "rawfile", "text")
                win.command_palette()
                win.palette.line.setText("view")
                self.shot("11_palette_dark")
                win.palette.hide()
                win.quick_open()
                win.palette.line.setText("globallogic")
                self.shot("11_quick_open_dark")
                win.palette.hide()
            if self.wanted("12_changes"):
                ref, view = self.select(patch, "default_mp.cfg", "rawfile", "text")
                body = patch.doc.text(ref)
                patch.doc.set_text(ref, body.replace("seta", "set", 2) + "set ui_custom 1\n")
                tab = patch.find_ref("mp/mapstable.csv", "stringtable")
                patch.doc.set_cell(tab, 1, 1, "Nuketown (edited)")
                loc = next(r for r in patch.doc.refs if r.type_name == "localize")
                patch.doc.set_localize(loc, "Edited value")
                patch._edited()
                patch.refresh()
                win._edited(patch)
                win.show_changes()
                win.changes.list.setCurrentIndex(win.changes.model.index(0, 0))
                self.shot("12_changes_diff_dark")
                self.theme("light")
                self.shot("12_changes_diff_light")
                self.theme("dark")
                win.bottom.hide()
                while patch.doc.can_undo:
                    patch.doc.undo()
                patch.refresh()
                win._edited(patch)

        if nuked is not None:
            self.win.tabs.setCurrentWidget(nuked)
            shots = [
                ("06_image_dxt_dark", "c_usa_cia_mp_gear_c", None),
                ("06_image_normal_dark", "berlin_books_n", None),
                ("06_image_alpha_dark", "fxt_env_cloud_mist1", "A"),
                ("06_image_blend_light", "rus_metal_wire_fence_c", "blend"),
            ]
            for name, image, mode in shots:
                if not self.wanted(name):
                    continue
                self.theme("light" if name.endswith("light") else "dark")
                ref, view = self.select(nuked, image, "image", "image")
                if mode == "A":
                    view.set_channel("A")
                elif mode == "blend":
                    view.set_blend(True)
                self.shot(name)
                view.set_channel("RGB")
                view.set_blend(False)
            self.theme("dark")
            if self.wanted("07_world"):
                self.select(nuked, "maps/mp/mp_nuked.d3dbsp", "gfx_map", "geometry")
                self.shot("07_world_wire_dark")
                self.theme("light")
                self.shot("07_world_wire_light")
                self.theme("dark")
            if self.wanted("07_collision"):
                ref = next(r for r in nuked.doc.refs if r.type_name == "col_map_mp")
                nuked.open_ref(ref, "geometry")
                nuked.current_view().load_sync(nuked.doc, ref)
                self.shot("07_collision_dark")
            if self.wanted("07_xmodel"):
                model = next(
                    (r for r in nuked.doc.refs if r.type_name == "xmodel" and "truck" in r.name),
                    next(r for r in nuked.doc.refs if r.type_name == "xmodel"),
                )
                nuked.open_ref(model, "geometry")
                nuked.current_view().load_sync(nuked.doc, model)
                self.shot("07_xmodel_dark")
            if self.wanted("07_entities"):
                ref = next(r for r in nuked.doc.refs if r.type_name == "col_map_mp")
                nuked.open_ref(ref, "text")
                self.shot("07_map_ents_dark")

        ui = self.page("ui_mp")
        if ui is not None and self.wanted("05_localize_ui"):
            loc = next(r for r in ui.doc.refs if r.type_name == "localize")
            ui.open_ref(loc, "localize")
            self.shot("05_localize_ui_light" if False else "05_localize_ui_dark")

        if self.wanted("13_save_report"):
            self.save_report()
        if self.wanted("14_about"):
            from opent5.gui.dialogs import AboutDialog

            for t in ("dark", "light"):
                self.theme(t)
                d = AboutDialog(win)
                d.show()
                self.shot(f"14_about_{t}", d)
                d.close()
            self.theme("dark")
        if self.wanted("15_shortcuts"):
            from opent5.gui.dialogs import ShortcutsDialog

            d = ShortcutsDialog(win.shortcut_rows(), win)
            d.show()
            self.shot("15_shortcuts_dark", d)
            d.close()

    def save_report(self) -> None:
        """A real Save As through the backend when opent5.edit is present; the file goes to a
        temporary folder and is deleted afterwards."""
        from opent5.gui.dialogs import SaveReportDialog

        patch = self.page("patch_mp")
        if patch is None:
            return
        if not patch.doc.can_save:
            print("  skip: 13_save_report needs opent5.edit (the read adapter cannot save)")
            return
        ref = patch.find_ref("default_mp.cfg", "rawfile")
        patch.doc.set_text(ref, patch.doc.text(ref) + "set ui_custom 1\n")
        target = Path(self.tmp) / "patch_mp_edited.ff"
        report = patch.doc.save(target, verify=True)
        d = SaveReportDialog(report, self.win)
        d.show()
        self.shot("13_save_report_dark", d)
        d.close()
        target.unlink(missing_ok=True)
        while patch.doc.can_undo:
            patch.doc.undo()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--only", nargs="*", help="substrings of screenshot names to render")
    args = ap.parse_args(argv)
    if not env.zone_dirs():
        print("no zone folders configured in .env; nothing to render")
        return 1
    started = time.perf_counter()
    s = Shooter(args.only)
    s.run()
    seconds = time.perf_counter() - started
    print(f"{len(s.written)} screenshots in {OUT.relative_to(ROOT)} ({seconds:.0f} s)")
    for i in range(s.win.tabs.count()):  # nothing here is kept: drop every edit
        doc = s.win.tabs.widget(i).doc
        while doc.can_undo:
            doc.undo()
    s.win.ask_before_discard = False
    s.win.close()
    shutil.rmtree(s.tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
