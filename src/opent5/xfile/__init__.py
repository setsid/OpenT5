"""The XFile layer: a decompressed zone parsed into assets, the way the game
loads it, with a complete event log for re-layout.

    from opent5.xfile import parse
    xfile = parse(content)          # content: the inflated zone (Zone.content)
    xfile.assets[0].data            # structured data from the type's handler
    xfile.problems()                # [] when the walk is exact

Package map: ``constants`` (blocks, pointer markers, asset types), ``stream``
(the loader primitives), ``events`` (the event log), ``model`` (zone-level
parse), ``handlers`` (one module per asset type or family, registered by
type), ``write`` / ``write_asset`` (the same walk run by ``XWriter``).
"""

from opent5.xfile.constants import ASSET_TYPE_NAMES, AssetType, Block
from opent5.xfile.events import EventKind, EventLog, PtrKind
from opent5.xfile.handlers import REGISTRY, Handler, handler_for
from opent5.xfile.model import (
    Asset,
    AssetError,
    Written,
    XFile,
    XFileHeader,
    parse,
    write,
    write_asset,
)
from opent5.xfile.stream import XFileError, XStream, XWriter

__all__ = [
    "ASSET_TYPE_NAMES",
    "REGISTRY",
    "Asset",
    "AssetError",
    "AssetType",
    "Block",
    "EventKind",
    "EventLog",
    "Handler",
    "PtrKind",
    "XFile",
    "XFileError",
    "XFileHeader",
    "XStream",
    "XWriter",
    "Written",
    "handler_for",
    "parse",
    "write",
    "write_asset",
]
