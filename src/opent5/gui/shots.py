"""Render every GUI view offscreen against real zones.

Lives in the package so the packaged exe can run it too:

    QT_QPA_PLATFORM=offscreen OpenT5.exe --screenshots OUTDIR [--only NAME ...]
    QT_QPA_PLATFORM=offscreen .venv/bin/python tools/screenshots.py [--only NAME ...]

Zones come from .env (mp_nuked, patch_mp, ui_mp); they are only read. The save
report comes from a real Save As of an edited patch_mp into a temporary folder,
which is removed afterwards, as are the throwaway settings (never the user's).
"""

from __future__ import annotations

import shutil
import tempfile
import time
from pathlib import Path

import numpy as np
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from opent5 import env

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
    def __init__(self, only: list[str] | None, out: Path):
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
        self.out = out
        out.mkdir(parents=True, exist_ok=True)
        self.docs: dict[str, object] = {}

    def wanted(self, name: str) -> bool:
        return not self.only or any(o in name for o in self.only)

    def shot(self, name: str, widget=None) -> None:
        settle(self.app, 0.15)
        w = widget or self.win
        path = self.out / f"{name}.png"
        w.grab().save(str(path))
        self.written.append(path)
        print(f"  {path}")

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

    def shaded(self, name: str, page, asset: str, type_name: str) -> None:
        """The geometry view in shaded, textured mode (the GPU path, offscreen)."""
        ref = page.find_ref(asset, type_name)
        if ref is None:
            print(f"  skip: {name} has no {type_name} {asset}")
            return
        page.open_ref(ref, "geometry")
        view = page.current_view()
        if not view.canvas.gl_available():
            print(f"  skip: {name} needs OpenGL, not available here")
            return
        view.shaded_button.setChecked(True)
        view.load_sync(page.doc, ref)
        settle(self.app, 0.1)
        self.shot(name)
        view.shaded_button.setChecked(False)

    def thumbnails(self, nuked) -> None:
        """The asset tree with image thumbnails: the image group expanded, a few rows
        visible long enough for their thumbnails to decode on the worker."""
        from PySide6.QtCore import Qt

        self.win.tabs.setCurrentWidget(nuked)
        nuked.show_ref(None)
        tree = nuked.tree
        tree.filter.clear()
        tree.view.collapseAll()
        image_idx = None
        for row in range(tree.proxy.rowCount()):
            idx = tree.proxy.index(row, 0)
            if idx.data() == "image":
                tree.view.expand(idx)
                image_idx = idx
        # Point at the streamed colour maps (names start with '~'): these carry their
        # pixels in this zone's .pak and decode, unlike the ',' cross-zone references.
        if image_idx is not None:
            total = tree.proxy.rowCount(image_idx)
            start = 0
            for row in range(total):
                name = tree.proxy.index(row, 0, image_idx).data() or ""
                if name.startswith("~-g") and "_c" in name:  # streamed colour maps
                    start = row
                    break
            tree.view.scrollTo(
                tree.proxy.index(start, 0, image_idx), tree.view.ScrollHint.PositionAtTop
            )
            rows = range(start, min(start + 34, total))
            for row in rows:
                child = tree.proxy.index(row, 0, image_idx)
                tree.model.data(tree.proxy.mapToSource(child), Qt.ItemDataRole.DecorationRole)
            import time as _t

            end = _t.perf_counter() + 15
            target = int(len(rows) * 0.8)
            while _t.perf_counter() < end and len(tree.model.thumbs) < target:
                settle(self.app, 0.05)
            tree.view.scrollTo(
                tree.proxy.index(start, 0, image_idx), tree.view.ScrollHint.PositionAtTop
            )
        self.shot("23_thumbnails_dark")
        self.theme("light")
        settle(self.app, 0.3)
        self.shot("23_thumbnails_light")
        self.theme("dark")
        tree.view.collapseAll()

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
            if self.wanted("07_world_shaded"):
                self.shaded("07_world_shaded_dark", nuked,
                            "maps/mp/mp_nuked.d3dbsp", "gfx_map")  # fmt: skip
            if self.wanted("07_xmodel_shaded"):
                model = next(
                    (r for r in nuked.doc.refs if r.type_name == "xmodel" and "truck" in r.name),
                    next(r for r in nuked.doc.refs if r.type_name == "xmodel"),
                )
                self.shaded("07_xmodel_shaded_dark", nuked, model.name, "xmodel")
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
            if self.wanted("21_image_resize") and nuked.doc.can_save:
                self.image_resize(nuked)
            if self.wanted("23_thumbnails"):
                self.thumbnails(nuked)

        ui = self.page("ui_mp")
        if ui is not None and self.wanted("05_localize_ui"):
            loc = next(r for r in ui.doc.refs if r.type_name == "localize")
            ui.open_ref(loc, "localize")
            self.shot("05_localize_ui_light" if False else "05_localize_ui_dark")

        if self.wanted("16_shared") or self.wanted("17_loading") or self.wanted("22_shared"):
            self.shared_strings()
        if patch is not None and (self.wanted("18_unsaved") or self.wanted("19_saving")):
            self.unsaved(patch)
        if patch is not None and self.wanted("20_fields_edit"):
            self.fields_edit(patch)
        if patch is not None and self.wanted("20_fields_nested"):
            self.fields_nested(patch)

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

    def shared_strings(self) -> None:
        """code_post_gfx_mp opened the way a user does (worker thread, loading page captured
        mid-load), then the localize view on a key that shares its string, and the choice
        an edit of it asks for."""
        from opent5.gui.strips import ShareChoiceDialog
        from opent5.gui.zonepage import ZonePage

        path = zone("code_post_gfx_mp")
        if path is None:
            print("  skip: code_post_gfx_mp.ff not configured")
            return
        win = self.win
        win.open_paths([path])
        captured = False
        end = time.perf_counter() + 60
        while time.perf_counter() < end:
            settle(self.app, 0.02)
            w = win.tabs.currentWidget()
            if isinstance(w, ZonePage):
                break
            bar = getattr(getattr(w, "progress", None), "bar", None)
            if not captured and bar is not None and bar.value() >= 350:
                if self.wanted("17_loading"):
                    self.shot("17_loading_dark")
                captured = True
        page = win.tabs.currentWidget()
        if not isinstance(page, ZonePage):
            return
        if self.wanted("22_shared"):
            self.shared_tree(page)
        if not self.wanted("16_shared"):
            return
        ref = page.find_ref("MPUI_PLAYER_MATCH_CAPS", "localize")
        page.open_ref(ref, "localize")
        view = page.current_view()
        view.filter.setText("PLAYER_MATCH")
        row = view.model.row_of(ref)
        view.view.setCurrentIndex(view.proxy.mapFromSource(view.model.index(row, 1)))
        self.shot("16_shared_localize_dark")
        ShareChoiceDialog.last = "all"
        d = ShareChoiceDialog(
            'MPUI_PLAYER_MATCH_CAPS ("PLAYER MATCH")', ["MENU_PLAYER_MATCH_CAPS"], win
        )
        d.show()
        self.shot("16_share_choice_dark", d)
        self.theme("light")
        self.shot("16_share_choice_light", d)
        d.close()
        self.theme("dark")
        # what the Changes panel says after each choice
        doc = page.doc
        doc.set_localize(ref, "PLAYER MATCH (ALL)", share="all")
        other = page.find_ref("MPUI_PLAYER_MATCH", "localize")
        if other is not None and doc.shared_with(other):
            doc.set_localize(other, "Player match (split)", share="split")
        page._edited()
        page.refresh()
        view.model.refresh_values()
        win._edited(page)
        win.show_changes()
        self.shot("16_shared_changes_dark")
        win.bottom.hide()
        doc.discard_all()
        page.refresh()
        view.filter.clear()
        win._update_state()

    def shared_tree(self, page) -> None:
        """A share="all" edit made while the localize view was never opened: the tree marks
        both keys it changed."""
        ref = page.find_ref("MPUI_PLAYER_MATCH_CAPS", "localize")
        page.open_ref(ref, "fields")
        page.doc.set_localize(ref, "PLAYER MATCH (ALL)", share="all")
        page._edited()
        page.refresh()
        self.win._update_state()
        page.tree.filter.setText("PLAYER_MATCH_CAPS")
        page.tree.view.expandAll()
        self.shot("22_shared_tree_dark")
        page.doc.discard_all()
        page.tree.filter.clear()
        page._edited()
        page.refresh()
        self.win._update_state()

    def image_resize(self, nuked) -> None:
        """A streamed image given twice its size (the mannequin head, 128x256 to 256x512):
        the image view and the Changes panel. Nothing is saved."""
        from opent5.formats import texture as tx

        win = self.win
        ref, view = self.select(nuked, "~-gmp_nuked_manneq_head_male_01_c", "image", "image")
        w, h = 256, 512
        y, x = np.mgrid[0:h, 0:w]
        rgba = np.zeros((h, w, 4), np.uint8)
        rgba[..., 3] = 255
        rgba[((x // 16) + (y // 16)) % 2 == 0] = (255, 0, 255, 255)
        rgba[(x % 8 == 0) | (y % 8 == 0)] = (255, 255, 255, 255)
        rgba[200:264] = (255, 220, 0, 255)
        png = Path(self.tmp) / "resize.png"
        png.write_bytes(tx.write_png(rgba))
        view.ask_resize = lambda old, new: True
        view.import_path(png)
        nuked._edited()
        win._edited(nuked)
        self.shot("21_image_resize_dark")
        win.show_changes()
        win.changes.list.setCurrentIndex(win.changes.model.index(0, 0))
        self.shot("21_image_resize_changes_dark")
        win.bottom.hide()
        nuked.doc.discard_all()
        nuked.refresh()
        win._update_state()
        png.unlink(missing_ok=True)

    def fields_nested(self, patch) -> None:
        """A field of a node nested in a model (its first material's sort key) after an
        edit, with the path in the Changes panel."""
        win = self.win
        win.tabs.setCurrentWidget(patch)
        model = next(r for r in patch.doc.refs if r.type_name == "xmodel")
        patch.open_ref(model, "fields")
        view = patch.current_view()
        proxy = view.proxy

        def child(parent, key):
            for r in range(proxy.rowCount(parent)):
                idx = proxy.index(r, 0, parent)
                if idx.data() == key:
                    view.tree.expand(idx)
                    return idx
            return None

        def find():
            node = view.proxy.index(-1, -1)
            for key in ("materials", "[0]", "material", "header", "info.sortKey"):
                node = child(node, key) if node is not None else None
            return node

        node = find()
        if node is None:
            print("  skip: 20_fields_nested found no materials[0]/material/info.sortKey")
            return
        proxy.setData(node.siblingAtColumn(1), "7")
        patch._edited()
        win._edited(patch)
        win.show_changes()
        settle(self.app, 0.1)
        value = find().siblingAtColumn(1)
        view.tree.setCurrentIndex(value)
        view.tree.scrollTo(value, view.tree.ScrollHint.PositionAtCenter)
        self.shot("20_fields_nested_dark")
        win.bottom.hide()
        patch.doc.discard_all()
        patch.refresh()
        win._update_state()

    def unsaved(self, patch) -> None:
        """The unsaved strip, tab and title in both themes, and a save in progress."""
        win = self.win
        win.tabs.setCurrentWidget(patch)
        ref = patch.find_ref("default_mp.cfg", "rawfile")
        patch.doc.set_text(ref, patch.doc.text(ref) + "set ui_custom 1\n")
        tab = patch.find_ref("mp/mapstable.csv", "stringtable")
        patch.doc.set_cell(tab, 1, 1, "Nuketown (edited)")
        patch.open_ref(ref, "text")
        patch._edited()
        patch.refresh()
        win._update_state()
        for t in ("dark", "light"):
            if self.wanted(f"18_unsaved_{t}"):
                self.theme(t)
                self.shot(f"18_unsaved_{t}")
        self.theme("dark")
        if self.wanted("19_saving") and patch.doc.can_save:
            target = Path(self.tmp) / "patch_mp_saving.ff"
            win.modal_reports = False
            win._save_to(patch, target)
            end = time.perf_counter() + 60
            shot = False
            while win._tasks and time.perf_counter() < end:
                settle(self.app, 0.01)
                if not shot and patch.saving.bar.value() >= 300:
                    self.shot("19_saving_dark")
                    shot = True
            # the report dialog is modal in a real save; here it was opened by _saved
            for w in self.app.topLevelWidgets():
                if w.isVisible() and w.windowTitle() == "Save report":
                    w.close()
            target.unlink(missing_ok=True)
        patch.doc.discard_all()
        patch.refresh()
        win._update_state()

    def fields_edit(self, patch) -> None:
        """Editable fields: a material (its counts are locked) after one field edit."""
        win = self.win
        win.tabs.setCurrentWidget(patch)
        mat = next(r for r in patch.doc.refs if r.type_name == "material")
        patch.open_ref(mat, "fields")
        view = patch.current_view()
        m = view.model
        header = view.proxy.index(0, 0)
        for r in range(view.proxy.rowCount(header)):
            idx = view.proxy.index(r, 0, header)
            if idx.data() == "info.sortKey":
                value = view.proxy.index(r, 1, header)
                view.proxy.setData(value, "5")
                view.tree.setCurrentIndex(value)
                break
        m.dataChanged.emit(m.index(0, 0), m.index(0, 0))
        win._update_state()
        self.shot("20_fields_edit_dark")
        self.theme("light")
        self.shot("20_fields_edit_light")
        self.theme("dark")
        patch.doc.discard_all()
        patch.refresh()
        win._update_state()

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


def run(out: Path, only: list[str] | None = None) -> int:
    if not env.zone_dirs():
        print("no zone folders configured in .env; nothing to render")
        return 1
    started = time.perf_counter()
    s = Shooter(only, Path(out))
    s.run()
    seconds = time.perf_counter() - started
    print(f"{len(s.written)} screenshots in {out} ({seconds:.0f} s)")
    for i in range(s.win.tabs.count()):  # nothing here is kept: drop every edit
        doc = s.win.tabs.widget(i).doc
        while doc.can_undo:
            doc.undo()
    s.win.ask_before_discard = False
    s.win.close()
    shutil.rmtree(s.tmp, ignore_errors=True)
    return 0
