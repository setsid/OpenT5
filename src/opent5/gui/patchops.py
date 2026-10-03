"""Thin wrappers the GUI runs on a worker to build, apply and save mod patches.

The real work is ``opent5.patch`` (docs/patch-format.md); these keep the GUI from
importing it (and so ``opent5.edit``) until a patch action is actually used, and give
the worker a single call to make. The result objects are returned straight to the
patch dialogs, which read them by attribute.
"""

from __future__ import annotations

from pathlib import Path


def create_patch(stock, edited, progress=None):
    """Build a patch capturing the difference from the ``stock`` zone to the ``edited`` one.

    Raises ``opent5.patch.PatchError`` (expected vs found) when the two cannot be diffed into
    a patch that reproduces the edited zone exactly."""
    from opent5 import patch

    return patch.create(Path(stock), Path(edited))


def apply_patch(patch_file, stock, out, progress=None):
    """Apply the patch at ``patch_file`` to the user's own ``stock`` zone, writing ``out``.

    Raises ``opent5.patch.PatchError`` when the stock zone is not the source the patch was
    built from (the message carries the expected and found content sha1s)."""
    from opent5 import patch

    data = Path(patch_file).read_bytes()
    return patch.apply(data, Path(stock), Path(out))


def save_patch(result, out_path) -> int:
    """Write a built patch's bytes to ``out_path``; returns how many bytes were written."""
    data = result.patch
    Path(out_path).write_bytes(data)
    return len(data)
