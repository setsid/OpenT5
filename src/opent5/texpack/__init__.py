"""Texture packs: a folder of PNG/DDS files named after image assets, batch-replaced across
a zone and its .pak (docs/texture-pack.md).

    from opent5.texpack import apply_pack, preview_pack, list_images
    result = preview_pack("mp_nuked.ff", "my_pack/")        # what would change
    result = apply_pack("mp_nuked.ff", "my_pack/", "out/")  # write a new zone (+ its pak)
    result.replaced, result.skipped, result.failed

The CLI subparser is built by ``opent5.texpack.register``; its command functions return the
same result objects a GUI action would use.
"""

from opent5.texpack.cli import cmd_texpack, register, text_texpack
from opent5.texpack.naming import (
    Match,
    NameIndex,
    PackMap,
    TexpackError,
    load_map,
    normalise,
    suggested_filename,
)
from opent5.texpack.pack import (
    FileResult,
    PackResult,
    apply_pack,
    list_images,
    preview_pack,
    write_template,
)

__all__ = [
    "FileResult",
    "Match",
    "NameIndex",
    "PackMap",
    "PackResult",
    "TexpackError",
    "apply_pack",
    "cmd_texpack",
    "list_images",
    "load_map",
    "normalise",
    "preview_pack",
    "register",
    "suggested_filename",
    "text_texpack",
    "write_template",
]
