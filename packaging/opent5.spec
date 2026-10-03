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
# PyNaCl's compiled _sodium module imports cffi's backend from C, which static
# analysis cannot see.
hidden.append("_cffi_backend")

import opent5  # noqa: E402
from PyInstaller.utils.win32.versioninfo import (  # noqa: E402
    FixedFileInfo,
    StringFileInfo,
    StringStruct,
    StringTable,
    VarFileInfo,
    VarStruct,
    VSVersionInfo,
)

_v = tuple(int(x) for x in opent5.__version__.split(".")) + (0,)
version = VSVersionInfo(
    ffi=FixedFileInfo(filevers=_v, prodvers=_v),
    kids=[
        StringFileInfo(
            [
                StringTable(
                    "080904B0",
                    [
                        StringStruct("CompanyName", opent5.APP_AUTHOR),
                        StringStruct("FileDescription", f"{opent5.APP_NAME} by {opent5.APP_AUTHOR}"),
                        StringStruct("FileVersion", opent5.__version__),
                        StringStruct("InternalName", opent5.APP_NAME),
                        StringStruct("LegalCopyright", f"Copyright (C) 2026 {opent5.APP_AUTHOR}. GPL-3.0-or-later."),
                        StringStruct("OriginalFilename", f"{opent5.APP_NAME}.exe"),
                        StringStruct("ProductName", opent5.APP_NAME),
                        StringStruct("ProductVersion", opent5.__version__),
                    ],
                )
            ]
        ),
        VarFileInfo([VarStruct("Translation", [0x0809, 1200])]),
    ],
)

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
    version=version,
    console=False,
    upx=False,
    debug=False,
    strip=False,
)
