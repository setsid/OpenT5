"""Self-test for the GUI, built to run inside the packaged exe.

    OpenT5.exe --selftest REPORT.json ZONE.ff [ZONE.ff ...]      (QT_QPA_PLATFORM=offscreen)

Opens every zone, picks one asset of every type the zone holds (top-level and
inline), and shows it in every view the GUI offers for it, decoding images and
building meshes synchronously. A view fails when it could not be created (the
"view is not available" placeholder, which is what a module missing from a frozen
build produces), when showing it raises, or when any visible text carries an
import error. The JSON report lists every check; the exit code is 0 only when all
pass and every required kind of asset was exercised at least once.
"""

from __future__ import annotations

import json
import time
import traceback
from pathlib import Path

#: Asset types that must be exercised by the run as a whole (the user-facing views).
REQUIRED = (
    "rawfile",
    "stringtable",
    "localize",
    "image",
    "material",
    "xmodel",
    "gfx_map",
    "col_map",
)
#: Views that must be exercised at least once across the run.
REQUIRED_VIEWS = ("text", "table", "localize", "image", "geometry", "fields", "hex")
#: Text that means a view or module failed to load rather than a data limitation.
BAD_TEXT = (
    "is not available",
    "no module named",
    "importerror",
    "cannot import",
    "modulenotfounderror",
    "traceback",
)


def _settle(app, seconds: float = 0.05) -> None:
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        app.processEvents()
        time.sleep(0.005)


def _visible_text(widget) -> list[str]:
    from PySide6.QtWidgets import QAbstractButton, QLabel

    out = []
    for w in [widget, *widget.findChildren(QLabel), *widget.findChildren(QAbstractButton)]:
        if hasattr(w, "text") and w.isVisibleTo(widget):
            text = w.text()
            if text:
                out.append(text)
    return out


def _family(type_name: str) -> str:
    return "col_map" if type_name.startswith("col_map") else type_name


def run(zones: list[str], report_path: str) -> int:
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication

    from opent5.gui.mainwindow import MainWindow
    from opent5.gui.zonepage import VIEWS, _Missing

    app = QApplication.instance() or QApplication(["opent5"])
    tmp = Path(report_path).with_suffix(".settings.ini")
    win = MainWindow(QSettings(str(tmp), QSettings.Format.IniFormat))
    win.resize(1440, 900)
    win.show()
    checks: list[dict] = []
    seen_types: set[str] = set()
    seen_views: set[str] = set()
    errors: list[str] = []
    started = time.perf_counter()

    for path in zones:
        try:
            (page,) = win.open_paths([Path(path)], sync=True)
        except Exception as exc:  # noqa: BLE001 - the report must say what broke
            errors.append(f"{path}: could not open: {exc!r}")
            continue
        firsts: dict[str, object] = {}
        for ref in page.doc.all_refs:
            firsts.setdefault(ref.type_name, ref)
        for type_name, ref in sorted(firsts.items()):
            for kind in page.available(ref):
                check = {
                    "zone": page.doc.zone_name,
                    "type": type_name,
                    "asset": ref.name,
                    "view": kind,
                    "ok": True,
                    "problem": "",
                }
                try:
                    page.open_ref(ref, kind)
                    view = page.view(kind)
                    if hasattr(view, "load_sync") and kind in ("image", "geometry"):
                        view.load_sync(page.doc, ref)
                    _settle(app, 0.05)
                    if isinstance(view, _Missing):
                        check.update(ok=False, problem=" ".join(_visible_text(view)))
                    else:
                        bad = [
                            t for t in _visible_text(view) if any(b in t.lower() for b in BAD_TEXT)
                        ]
                        if bad:
                            check.update(ok=False, problem=bad[0])
                    module = VIEWS[kind][0]
                    check["module"] = module
                except Exception:  # noqa: BLE001
                    check.update(ok=False, problem=traceback.format_exc(limit=3))
                checks.append(check)
                seen_types.add(_family(type_name))
                seen_views.add(kind)

    missing_types = [t for t in REQUIRED if t not in seen_types]
    missing_views = [v for v in REQUIRED_VIEWS if v not in seen_views]
    failed = [c for c in checks if not c["ok"]]
    ok = not failed and not errors and not missing_types and not missing_views
    report = {
        "ok": ok,
        "checks": len(checks),
        "failed": failed,
        "errors": errors,
        "types_exercised": sorted(seen_types),
        "views_exercised": sorted(seen_views),
        "missing_types": missing_types,
        "missing_views": missing_views,
        "seconds": round(time.perf_counter() - started, 1),
        "results": checks,
    }
    Path(report_path).write_text(json.dumps(report, indent=2) + "\n")
    for i in range(win.tabs.count()):
        doc = getattr(win.tabs.widget(i), "doc", None)
        while doc is not None and doc.can_undo:
            doc.undo()
    win.ask_before_discard = False
    win.close()
    tmp.unlink(missing_ok=True)
    return 0 if ok else 1
