"""The XFile layer: a decompressed zone parsed into assets, the way the game
loads it, with a complete event log for re-layout.

    from opent5.xfile import parse
    xfile = parse(content)          # content: the inflated zone (Zone.content)
    xfile.assets[0].data            # structured data from the type's handler
    xfile.problems()                # [] when the walk is exact

Package map: ``constants`` (blocks, pointer markers, asset types), ``stream``
(the loader primitives), ``events`` (the event log), ``model`` (zone-level
parse), ``handlers`` (one module per asset type or family, registered by
type), ``writer`` (the write side's byte sink).
"""

from opent5.xfile.constants import ASSET_TYPE_NAMES, AssetType, Block
from opent5.xfile.events import EventKind, EventLog, PtrKind
from opent5.xfile.handlers import REGISTRY, Handler, handler_for
from opent5.xfile.model import Asset, AssetError, XFile, XFileHeader, parse
from opent5.xfile.stream import XFileError, XStream
from opent5.xfile.writer import Writer

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
    "Writer",
    "XFile",
    "XFileError",
    "XFileHeader",
    "XStream",
    "handler_for",
    "parse",
]
