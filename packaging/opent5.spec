# PyInstaller spec for the single-file Windows build. Run through packaging/build.bat.
# -*- mode: python -*-
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

root = Path(SPECPATH).parent
resources = root / "src" / "opent5" / "gui" / "resources"

# Every opent5 module, generated from the package rather than listed by hand: the GUI
# imports its views by name at run time (opent5.gui.zonepage.VIEWS) and the parser
# registers handlers per asset type, so static analysis alone misses modules.
sys.path.insert(0, str(root / "src"))
hidden = collect_submodules("opent5")

a = Analysis(
    [str(root / "packaging" / "launch.py")],
    pathex=[str(root / "src")],
    hiddenimports=hidden,
    datas=[
        (str(resources), "opent5/gui/resources"),
        (str(root / "LICENSE"), "."),
    ],
    excludes=["tkinter", "matplotlib", "PySide6.QtNetwork", "PySide6.QtQml", "PySide6.QtQuick"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="OpenT5",
    icon=str(resources / "opent5.ico"),
    console=False,
    upx=False,
    debug=False,
    strip=False,
)
