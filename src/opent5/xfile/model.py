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
    OFFSET_BLOCK_SHIFT,
    PTR_NULL,
    Block,
    type_name,
)
from opent5.xfile.events import NONE, EventKind, EventLog
from opent5.xfile.handlers.base import PS3
from opent5.xfile.refs import Refs, Target
from opent5.xfile.stream import DeferredData, Platform, XFileError, XStream, XWriter

#: blockSize[0] (TEMP) is the TEMP high-water mark plus 16 in every zone walked
#: (TEMP rewinds after each asset, so the final position is 0). INFERRED: the
#: writer counts the 16-byte XAssetList in TEMP, which the PS3 loader reads into
#: a global instead (0x233b04).
TEMP_SLACK = ASSET_LIST_SIZE


@dataclass(frozen=True)
class XFileHeader:
    """The 36-byte prefix: nine u32 (big-endian on PS3; ``endian`` "<" for PC)."""

    size: int
    external_size: int
    block_sizes: tuple[int, ...]

    @classmethod
    def parse(cls, data: bytes, endian: str = ">") -> XFileHeader:
        if len(data) < HEADER_SIZE:
            raise XFileError(f"XFile header: expected {HEADER_SIZE} bytes, found {len(data)}")
        words = struct.unpack_from(endian + "9I", data, 0)
        return cls(words[0], words[1], tuple(words[2:9]))

    def pack(self, endian: str = ">") -> bytes:
        return struct.pack(endian + "9I", self.size, self.external_size, *self.block_sizes)


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
    #: What the writer needs beyond the assets: the raw XAssetList (16 bytes), the
    #: script string pointer array and the asset array, as read.
    asset_list: bytes = b""
    script_string_ptrs: bytes | None = field(repr=False, default=None)
    asset_entries: bytes | None = field(repr=False, default=None)
    #: Pointer resolution records (opent5.xfile.refs).
    refs: Refs | None = field(repr=False, default=None)
    #: Byte order, handlers and type map the zone was parsed with (PS3 unless given).
    platform: Platform = field(repr=False, default=PS3)

    def resolve(self, value: int) -> Target | None:
        """What a raw pointer value names: the asset behind an alias, or the node,
        key, element and byte offset of the data an offset pointer points at."""
        if self.refs is None:
            raise XFileError("this XFile was not parsed with reference records")
        return self.refs.resolve(value)

    def view(self, node: dict):
        """A field view of `node` that can also resolve its pointer fields."""
        from opent5.xfile.schema import NodeView

        return NodeView(node, xfile=self)

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


def parse(
    content: bytes | bytearray | memoryview,
    log: bool = True,
    progress=None,
    platform: Platform | None = None,
) -> XFile:
    """Walk a whole decompressed zone. Raises AssetError (an XFileError) naming
    the asset, handler and offset at the first asset that does not parse.
    ``progress(stage, done, total)`` is optional (asset counts). ``platform``
    (default PS3) gives the byte order, handlers and asset type map."""
    platform = platform or PS3
    data = bytes(content)
    header = XFileHeader.parse(data, platform.endian)
    if len(data) < ASSET_LIST_OFFSET + ASSET_LIST_SIZE:
        raise XFileError(
            f"XAssetList at {ASSET_LIST_OFFSET:#x}: expected {ASSET_LIST_SIZE} bytes, "
            f"stream is {len(data)}"
        )
    st = XStream(data, log=log, platform=platform)
    st.progress = progress
    # The XAssetList is read raw: it occupies no block memory.
    list_bytes = data[ASSET_LIST_OFFSET : ASSET_LIST_OFFSET + ASSET_LIST_SIZE]
    st.fp = ASSET_LIST_OFFSET + ASSET_LIST_SIZE
    parts: dict = {}
    walked = _walk(st, list_bytes, ASSET_LIST_OFFSET, parts, None)
    return XFile(
        header=header,
        script_strings=parts.get("script_strings") or [],
        assets=walked.assets,
        script_strings_offset=walked.strings_at,
        asset_array_offset=walked.array_at,
        tail_offset=walked.tail,
        end_offset=st.fp,
        length=len(data),
        deferred=st.deferred,
        final_cursors=st.cursors(),
        temp_high_water=st.temp_high,
        log=st.log,
        asset_list=list_bytes,
        script_string_ptrs=parts.get("script_string_ptrs"),
        asset_entries=parts.get("asset_entries"),
        refs=st.refs,
        platform=platform,
    )


@dataclass
class _Walked:
    assets: list[Asset]
    strings_at: int
    array_at: int
    tail: int


def _walk(io: XStream, list_bytes: bytes, list_at: int, parts: dict, source: XFile | None):
    """Script strings, the asset array, every asset, the deferred tail: both directions.
    Reading fills `parts` and returns new Assets; writing takes them from `source`."""
    asset_list = io.platform.chunk_type(memoryview(bytearray(list_bytes)), list_at, NONE, NONE)
    string_count, asset_count = asset_list.u32(0), asset_list.u32(8)

    io.push(Block.VIRTUAL)
    strings_at = io.fp
    if io.follows(asset_list, 4, owned=True):
        io.alloc(3)
        pointers = io.load(4 * string_count, parts, "script_string_ptrs")
        strings = io.children(parts, "script_strings")
        for i in range(string_count):
            if io.reading:
                strings.append(None)
            io.string(pointers, 4 * i, strings, i)
    io.pop()

    io.push(Block.VIRTUAL)
    assets: list[Asset] = []
    array_at = io.fp
    if io.follows(asset_list, 12, owned=True):
        io.alloc(3)
        entries = io.load(8 * asset_count, parts, "asset_entries")
        report = getattr(io, "progress", None)
        for index in range(asset_count):
            if report is not None and index % 16 == 0:
                report(
                    "Writing assets" if source is not None else "Parsing assets", index, asset_count
                )
            data = None if source is None else source.assets[index].data
            assets.append(_entry(io, entries, index, data))
    io.pop()

    tail = io.fp
    io.flush_deferred()
    return _Walked(assets, strings_at, array_at, tail)


def write(
    xfile: XFile,
    log: bool = True,
    pointer_values: dict[int, int] | None = None,
    derive_header: bool = True,
) -> Written:
    """Serialise a parsed (possibly edited) zone back to its content bytes.

    Every asset is emitted from its node by the same handler code that parsed it,
    with block positions tracked as the loader tracks them. With derive_header the
    XFile header is computed from the result (size from the length, blockSize[]
    from the final block positions, TEMP as its high-water mark plus 16); for an
    unedited parse that is the original header. pointer_values overrides pointer
    fields by ordinal (see XWriter)."""
    endian = xfile.platform.endian
    writer = XWriter(log=log, pointer_values=pointer_values, platform=xfile.platform)
    writer.raw(xfile.header.pack(endian))
    list_at = writer.raw(xfile.asset_list)
    parts = {
        "script_string_ptrs": xfile.script_string_ptrs,
        "script_strings": xfile.script_strings,
        "asset_entries": xfile.asset_entries,
    }
    walked = _walk(writer, xfile.asset_list, list_at, parts, xfile)
    content = writer.out
    if derive_header:
        sizes = list(writer.pos)
        sizes[Block.TEMP] = writer.temp_high + TEMP_SLACK
        header = XFileHeader(len(content) - HEADER_SIZE, xfile.header.external_size, tuple(sizes))
    else:
        header = xfile.header
    content[0:HEADER_SIZE] = header.pack(endian)
    return Written(
        content=bytes(content),
        header=header,
        assets=walked.assets,
        tail_offset=walked.tail,
        final_cursors=writer.cursors(),
        temp_high_water=writer.temp_high,
        log=writer.log,
    )


@dataclass
class Written:
    """The result of ``write``: the content and where everything went."""

    content: bytes
    header: XFileHeader
    #: Each asset's span and block positions in the written stream.
    assets: list[Asset]
    tail_offset: int
    final_cursors: tuple[int, ...]
    temp_high_water: int
    #: The writer's event log: the same records as a parse's, in output offsets.
    log: EventLog | None = field(repr=False, default=None)


def write_asset(asset: Asset, log: bool = False, platform: Platform | None = None) -> bytes:
    """One asset's own bytes (its file span; deferred bytes it queues are not
    included), from its node."""
    platform = platform or PS3
    handler = platform.registry[asset.type]
    writer = XWriter(log=log, platform=platform)
    writer.push(Block.VIRTUAL)
    handler.write(asset.data, writer, asset.header_ptr)
    writer.pop()
    return writer.getvalue()


def _entry(io: XStream, entries, index: int, data: Any) -> Asset:
    asset_type = io.platform.asset_type(entries.u32(8 * index))
    start = io.fp
    before = io.cursors()
    io.asset_index = index
    io.asset_strings = []
    event_start = -1
    if io.log is not None:
        event_start = len(io.log)
        io.log.append(EventKind.ASSET, index, asset_type, start)
    handler = io.registry.get(asset_type)
    raw = entries.u32(8 * index + 4)
    try:
        if handler is None:
            # Load_XAssetHeader has no case for this type: the game loads nothing.
            raise io.fail(
                f"asset {index}: type {asset_type} ({type_name(asset_type)}) has no loader "
                "in the game; expected one of the loaded types"
            )
        # Load_XAsset passes the header pointer already read with the array.
        raw = io.ref(entries, 8 * index + 4)
        data = handler.load_ptr(io, raw, data)
        if io.reading and isinstance(data, dict):
            field_key = (entries.block << OFFSET_BLOCK_SHIFT) | (entries.mem + 8 * index + 4)
            io.refs.slot(field_key, data, index)
    except AssetError:
        raise
    except (XFileError, IndexError, ValueError, struct.error) as exc:
        name = io.trail[:]
        raise AssetError(
            f"asset {index} ({type_name(asset_type)}) starting at {start:#x}: {exc}",
            index,
            asset_type,
            start,
            name,
            io.fp,
            list(io.asset_strings),
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
        file_end=io.fp,
        cursors_before=before,
        cursors_after=io.cursors(),
        data=data,
        event_start=event_start,
    )
