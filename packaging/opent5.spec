# PyInstaller spec for the single-file Windows build. Run through packaging/build.bat.
# -*- mode: python -*-
from pathlib import Path

root = Path(SPECPATH).parent
resources = root / "src" / "opent5" / "gui" / "resources"

a = Analysis(
    [str(root / "packaging" / "launch.py")],
    pathex=[str(root / "src")],
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
