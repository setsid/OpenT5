"""Exe-import smoke test: every editor, convert and GUI submodule must import.

The packaged build gathers modules with ``collect_submodules("opent5")`` in
``packaging/opent5.spec`` rather than by static analysis, because the GUI imports its
views by name at run time (``opent5.gui.zonepage.VIEWS``) and the parser registers
handlers per asset type. A module that fails to import would then slip through the build
and surface only when a user reached the view or asset that needs it.

This walks ``opent5.edit``, ``opent5.convert`` and ``opent5.gui`` with
``pkgutil.walk_packages`` so a newly added module is covered without touching this test,
and imports each one, failing on any ``ImportError``. ``conftest.py`` sets
``QT_QPA_PLATFORM=offscreen`` before collection, so importing a GUI module needs no
display.
"""

from __future__ import annotations

import importlib
import pkgutil

import pytest

import opent5.convert
import opent5.edit
import opent5.gui

#: The packages whose every submodule must import in the exe.
_PACKAGES = (opent5.edit, opent5.convert, opent5.gui)


def _submodules() -> list[str]:
    names: set[str] = set()
    for pkg in _PACKAGES:
        names.add(pkg.__name__)
        # walk_packages imports subpackages to descend; leaf modules are imported by the
        # test below, so an ImportError in one is reported against that module by name.
        for info in pkgutil.walk_packages(pkg.__path__, pkg.__name__ + "."):
            names.add(info.name)
    return sorted(names)


@pytest.mark.parametrize("name", _submodules())
def test_submodule_imports(name: str) -> None:
    importlib.import_module(name)
