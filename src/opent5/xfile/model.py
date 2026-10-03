"""The XFile model: a whole zone parsed the way the game's loader reads it.

    from opent5.container.zone import Zone
    from opent5.xfile import parse

    xfile = parse(Zone.open("patch_mp.ff").content)
    for asset in xfile.assets:
        print(asset.index, asset.type_name, asset.name, hex(asset.file_start))

Order, as DB_LoadXFile does it (docs/research/xfile.md sections 1 to 3): the
36-byte header; the 16-byte XAssetList read raw at 0x24; push VIRTUAL, the
script strings, pop; push VIRTUAL, the asset array (8 bytes each), every asset
through its type's pointer loader, pop; then the deferred LARGE_RUNTIME /
PHYSICAL_RUNTIME reads (the tail).

A parse is complete when the walk consumes the stream exactly (asset data,
then the tail, ends at len(content)) and every block ends at the size the
header declares (TEMP: its high-water mark plus 16, see TEMP_SLACK).
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Any

from opent5.xfile import handlers as _handlers  # noqa: F401  (registers the handlers)
from opent5.xfile.constants import (
    ASSET_LIST_OFFSET,
    ASSET_LIST_SIZE,
    BLOCK_COUNT,
    HEADER_SIZE,
    PTR_NULL,
    Block,
    type_name,
)
from opent5.xfile.events import NONE, EventKind, EventLog
from opent5.xfile.handlers.base import REGISTRY
from opent5.xfile.stream import Chunk, DeferredData, XFileError, XStream

#: blockSize[0] (TEMP) is the TEMP high-water mark plus 16 in every zone walked
#: (TEMP rewinds after each asset, so the final position is 0). INFERRED: the
#: writer counts the 16-byte XAssetList in TEMP, which the PS3 loader reads into
#: a global instead (0x233b04).
TEMP_SLACK = ASSET_LIST_SIZE


@dataclass(frozen=True)
class XFileHeader:
    """The 36-byte prefix: nine big-endian u32."""

    size: int
    external_size: int
    block_sizes: tuple[int, ...]

    @classmethod
    def parse(cls, data: bytes) -> XFileHeader:
        if len(data) < HEADER_SIZE:
            raise XFileError(f"XFile header: expected {HEADER_SIZE} bytes, found {len(data)}")
        words = struct.unpack_from(">9I", data, 0)
        return cls(words[0], words[1], tuple(words[2:9]))

    def pack(self) -> bytes:
        return struct.pack(">9I", self.size, self.external_size, *self.block_sizes)


@dataclass
class Asset:
    index: int
    type: int
    #: The XAsset.header value from the asset array (-1 in every shipped zone).
    header_ptr: int
    name: str | None
    #: File span of the asset's own bytes (header through its last sub-read; the
    #: deferred bytes it queued are in the tail, see ``deferred``).
    file_start: int
    file_end: int
    #: The seven block positions before and after loading it.
    cursors_before: tuple[int, ...]
    cursors_after: tuple[int, ...]
    data: Any = field(repr=False, default=None)
    #: Index of this asset's ASSET record in the event log.
    event_start: int = -1

    @property
    def type_name(self) -> str:
        return type_name(self.type)

    @property
    def size(self) -> int:
        return self.file_end - self.file_start


class AssetError(XFileError):
    """An asset that failed to parse; carries where."""

    def __init__(
        self,
        message: str,
        index: int,
        asset_type: int,
        file_start: int,
        trail: list[str],
        file_offset: int,
        strings: list[str | None],
        cursors_before: tuple[int, ...] = (),
    ):
        super().__init__(message)
        #: The seven block positions when the asset started.
        self.cursors_before = cursors_before
        #: The first strings read in the asset; the first is usually its name.
        self.strings = strings
        self.index = index
        self.asset_type = asset_type
        self.file_start = file_start
        self.trail = trail
        self.file_offset = file_offset


@dataclass
class XFile:
    header: XFileHeader
    script_strings: list[str | None]
    assets: list[Asset]
    #: File offset of the script string pointer array and of the asset array.
    script_strings_offset: int
    asset_array_offset: int
    #: Where the deferred tail starts and where the walk ended.
    tail_offset: int
    end_offset: int
    length: int
    deferred: list[DeferredData]
    final_cursors: tuple[int, ...]
    temp_high_water: int
    log: EventLog | None = field(repr=False, default=None)

    def problems(self) -> list[str]:
        """Every way the walk disagrees with the header; empty when the parse is exact."""
        out = []
        if self.header.size != self.length - HEADER_SIZE:
            out.append(
                f"XFile.size at 0x0: expected {self.length - HEADER_SIZE:#x}, "
                f"found {self.header.size:#x}"
            )
        if self.end_offset != self.length:
            out.append(
                f"stream end: walk ended at {self.end_offset:#x}, stream is {self.length:#x}"
            )
        ends = list(self.final_cursors)
        ends[Block.TEMP] = self.temp_high_water + TEMP_SLACK
        for block in range(BLOCK_COUNT):
            if ends[block] != self.header.block_sizes[block]:
                out.append(
                    f"block {Block(block).name}: walk ends at {ends[block]:#x}, header "
                    f"blockSize[{block}] at {8 + 4 * block:#x} is "
                    f"{self.header.block_sizes[block]:#x}"
                )
        return out

    @property
    def exact(self) -> bool:
        return not self.problems()

    def by_type(self, asset_type: int) -> list[Asset]:
        return [a for a in self.assets if a.type == asset_type]

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for a in self.assets:
            out[a.type_name] = out.get(a.type_name, 0) + 1
        return out


def parse(content: bytes | bytearray | memoryview, log: bool = True) -> XFile:
    """Walk a whole decompressed zone. Raises AssetError (an XFileError) naming
    the asset, handler and offset at the first asset that does not parse."""
    data = bytes(content)
    header = XFileHeader.parse(data)
    if len(data) < ASSET_LIST_OFFSET + ASSET_LIST_SIZE:
        raise XFileError(
            f"XAssetList at {ASSET_LIST_OFFSET:#x}: expected {ASSET_LIST_SIZE} bytes, "
            f"stream is {len(data)}"
        )
    st = XStream(data, log=log)
    # The XAssetList is read raw: it occupies no block memory.
    asset_list = Chunk(
        st.view[ASSET_LIST_OFFSET : ASSET_LIST_OFFSET + ASSET_LIST_SIZE],
        ASSET_LIST_OFFSET,
        NONE,
        NONE,
    )
    st.fp = ASSET_LIST_OFFSET + ASSET_LIST_SIZE
    string_count, asset_count = asset_list.u32(0), asset_list.u32(8)

    st.push(Block.VIRTUAL)
    script_strings: list[str | None] = []
    strings_at = st.fp
    if st.follows(asset_list, 4, owned=True):
        st.alloc(3)
        pointers = st.load(4 * string_count)
        script_strings = [st.string(pointers, 4 * i) for i in range(string_count)]
    st.pop()

    st.push(Block.VIRTUAL)
    assets: list[Asset] = []
    array_at = st.fp
    if st.follows(asset_list, 12, owned=True):
        st.alloc(3)
        entries = st.load(8 * asset_count)
        for index in range(asset_count):
            assets.append(_load_entry(st, entries, index))
    st.pop()

    tail = st.fp
    st.flush_deferred()
    return XFile(
        header=header,
        script_strings=script_strings,
        assets=assets,
        script_strings_offset=strings_at,
        asset_array_offset=array_at,
        tail_offset=tail,
        end_offset=st.fp,
        length=len(data),
        deferred=st.deferred,
        final_cursors=st.cursors(),
        temp_high_water=st.temp_high,
        log=st.log,
    )


def _load_entry(st: XStream, entries: Chunk, index: int) -> Asset:
    asset_type = entries.u32(8 * index)
    start = st.fp
    before = st.cursors()
    st.asset_index = index
    st.asset_strings = []
    event_start = -1
    if st.log is not None:
        event_start = len(st.log)
        st.log.append(EventKind.ASSET, index, asset_type, start)
    handler = REGISTRY.get(asset_type)
    raw = entries.u32(8 * index + 4)
    data = None
    try:
        if handler is None:
            # Load_XAssetHeader has no case for this type: the game loads nothing.
            raise st.fail(
                f"asset {index}: type {asset_type} ({type_name(asset_type)}) has no loader "
                "in the game; expected one of the loaded types"
            )
        # Load_XAsset passes the header pointer already read with the array.
        st.ref(entries, 8 * index + 4)
        data = handler.load_ptr(st, raw)
    except AssetError:
        raise
    except (XFileError, IndexError, ValueError, struct.error) as exc:
        name = st.trail[:]
        raise AssetError(
            f"asset {index} ({type_name(asset_type)}) starting at {start:#x}: {exc}",
            index,
            asset_type,
            start,
            name,
            st.fp,
            list(st.asset_strings),
            before,
        ) from exc
    name = None
    if handler is not None and data is not None:
        name = handler.name_of(data)
    if raw == PTR_NULL:
        name = None
    return Asset(
        index=index,
        type=asset_type,
        header_ptr=raw,
        name=name,
        file_start=start,
        file_end=st.fp,
        cursors_before=before,
        cursors_after=st.cursors(),
        data=data,
        event_start=event_start,
    )
