@echo off
rem Build OpenT5.exe on Windows from a short path, with a short-path venv.
rem Store Python breaks the 260-character path limit during the build; use the
rem python.org installer (py launcher) instead.
rem
rem   packaging\build.bat            (run from a copy of the source at a short path, e.g. C:\o5\src)
rem
rem Always build through this script's clean venv. Do NOT run pyinstaller against a system
rem interpreter: if full "PySide6" sits alongside "PySide6-Essentials"/"Addons" in the same
rem environment, PyInstaller bundles a conflicting Qt and the one-file exe fails with
rem "no Qt platform plugin could be initialized". The pinned set below is Essentials only.
rem
rem Output: dist\OpenT5.exe
setlocal
set VENV=%~d0\o5\v
if not exist %VENV%\Scripts\python.exe (
    py -3.12 -m venv %VENV% || exit /b 1
)
%VENV%\Scripts\python -m pip install --disable-pip-version-check -q ^
    numpy==2.2.6 PyNaCl==1.5.0 cffi==2.1.1 pycparser==3.0 ^
    PySide6-Essentials==6.8.1 shiboken6==6.8.1 pyinstaller==6.11.1 || exit /b 1
rem Guard: a reused venv must not contain the full "PySide6" meta-package (only Essentials).
rem Full PySide6 drags in a second, conflicting Qt that breaks the one-file platform plugin.
%VENV%\Scripts\python -m pip show PySide6 >nul 2>&1 && (
    echo ERROR: full "PySide6" is installed in %VENV% alongside PySide6-Essentials.
    echo        This breaks the one-file Qt platform plugin. Delete %VENV% and re-run so the
    echo        build uses a clean venv with only the pinned deps.
    exit /b 1
)
rem O5_WORK overrides the PyInstaller work folder, so two builds (for example tools\release.py
rem in C:\o5\rel) do not share one.
if not defined O5_WORK set O5_WORK=%~d0\o5\w
%VENV%\Scripts\pyinstaller --noconfirm --clean --distpath dist --workpath %O5_WORK% packaging\opent5.spec || exit /b 1
echo built dist\OpenT5.exe
