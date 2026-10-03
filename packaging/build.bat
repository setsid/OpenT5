@echo off
rem Build OpenT5.exe on Windows from a short path, with a short-path venv.
rem Store Python breaks the 260-character path limit during the build; use the
rem python.org installer (py launcher) instead.
rem
rem   packaging\build.bat            (run from a copy of the source at a short path, e.g. C:\o5\src)
rem
rem Output: dist\OpenT5.exe
setlocal
set VENV=%~d0\o5\v
if not exist %VENV%\Scripts\python.exe (
    py -3.12 -m venv %VENV% || exit /b 1
)
%VENV%\Scripts\python -m pip install --disable-pip-version-check -q ^
    numpy==2.2.6 PySide6-Essentials==6.8.1 shiboken6==6.8.1 pyinstaller==6.11.1 || exit /b 1
rem O5_WORK overrides the PyInstaller work folder, so two builds (for example tools\release.py
rem in C:\o5\rel) do not share one.
if not defined O5_WORK set O5_WORK=%~d0\o5\w
%VENV%\Scripts\pyinstaller --noconfirm --clean --distpath dist --workpath %O5_WORK% packaging\opent5.spec || exit /b 1
echo built dist\OpenT5.exe
