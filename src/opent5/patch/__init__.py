"""Shareable mod patches: the difference from a stock zone to an edited one, so a mod can be
shared without redistributing any game files (docs/patch-format.md).

    from opent5.patch import export, apply, info
    data = export("patch_mp.ff", "patch_mp_edited.ff")   # the .o5patch bytes
    result = apply(data, "patch_mp.ff", "out/patch_mp.ff")
    result.verified, result.reproduces_target

The CLI subparser is built by ``opent5.patch.register``; its command functions return the
same result objects the GUI uses.
"""

from opent5.patch.commands import cmd_patch, register, text_patch
from opent5.patch.core import (
    ApplyResult,
    ChangeInfo,
    CreateResult,
    PatchError,
    PatchInfo,
    apply,
    create,
    export,
    info,
)
from opent5.patch.format import (
    FORMAT_VERSION,
    MAGIC,
    SUFFIX,
    Manifest,
    PatchChange,
    PatchFormatError,
)

__all__ = [
    "FORMAT_VERSION",
    "MAGIC",
    "SUFFIX",
    "ApplyResult",
    "ChangeInfo",
    "CreateResult",
    "Manifest",
    "PatchChange",
    "PatchError",
    "PatchFormatError",
    "PatchInfo",
    "apply",
    "cmd_patch",
    "create",
    "export",
    "info",
    "register",
    "text_patch",
]
