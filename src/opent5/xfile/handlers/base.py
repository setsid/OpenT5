"""Handler interface, the registry, and the asset-pointer pattern every type shares.

A handler owns one asset type. Its ``body`` is the type's struct loader written
once against the stream primitives (``XStream`` reads, ``XWriter`` writes): given
the header just loaded (in TEMP), it loads everything the header owns in the
loader's order, keeping each loaded thing in the asset's node (a dict). Reading
fills the node from the file; writing emits the node back. There is no separate
write code to drift from the read code.

Nodes keep raw bytes for every struct and array the loader reads (``"header"``,
``"raw"`` for array elements, named keys for blobs and arrays), strings as text
(Latin-1), child assets as child nodes, and asset references by alias as
``AssetLink``. Scalars inside structs live in those bytes; decoded copies some
handlers add (``io.note``) are for reading convenience and are not written.
"""

from __future__ import annotations

from typing import Any, ClassVar

from opent5.xfile.constants import (
    OFFSET_BLOCK_SHIFT,
    PTR_INLINE,
    PTR_INSERT,
    PTR_NULL,
    Block,
    decode_offset_pointer,
    type_name,
)
from opent5.xfile.events import NONE
from opent5.xfile.stream import AssetLink, Chunk, XStream


class Handler:
    """One asset type. Subclasses set the class attributes and implement ``body``."""

    #: XAssetType value.
    asset_type: ClassVar[int]
    #: sizeof the header struct (the first Load_Stream, in TEMP).
    header_size: ClassVar[int]
    #: DB_AllocStreamPos mask before the header (3 for every type but menu).
    align: ClassVar[int] = 3
    #: The node class (a dict subclass may add typed accessors).
    node_type: ClassVar[type] = dict
    #: The node kind of the asset's root ("_t"), naming its field schema.
    kind: ClassVar[str] = ""

    @property
    def name(self) -> str:
        return type_name(self.asset_type)

    def body(self, io: XStream, header: Chunk, node: dict) -> None:
        """The struct loader, after the header: both directions."""
        raise NotImplementedError(f"{self.name}: no struct loader")

    def load_ptr(self, io: XStream, raw: int, node: Any = None) -> Any:
        """The per-type Load_<X>Ptr: push TEMP; -1 / -2 load the asset right here
        (header in TEMP, aligned; -2 also reserves an alias slot in VIRTUAL);
        another non-zero value is an alias pointer; pop (TEMP rewinds).

        Reading returns the new node (or an AssetLink); writing takes `node`."""
        io.push(Block.TEMP)
        result: Any = None
        if raw in (PTR_INLINE, PTR_INSERT):
            if io.reading:
                node = self.node_type()
                if self.kind:
                    node["_t"] = self.kind
            elif not isinstance(node, dict):
                raise io.fail(f"{self.name}: the pointer says inline, found {type(node).__name__}")
            io.trail.append(self.name)
            io.alloc(self.align)
            slot = io.insert() if raw == PTR_INSERT else None
            header = io.load(self.header_size, node, "header")
            self.body(io, header, node)
            if slot is not None and io.reading:
                io.refs.slot((Block.VIRTUAL << OFFSET_BLOCK_SHIFT) | slot, node, io.asset_index)
            io.trail.pop()
            result = node
        elif raw != PTR_NULL:
            if io.reading:
                block, offset = decode_offset_pointer(raw)
                found = io.refs.asset_at((raw - 1) & 0xFFFFFFFF)
                target, owner = found if found is not None else (None, -1)
                result = AssetLink(self.asset_type, raw, block, offset, target, owner)
            else:
                result = node
        io.pop()
        return result

    def write(self, node: Any, writer: XStream, raw: int = PTR_INLINE) -> None:
        """Emit one asset (header first) as a pointer of value `raw` would load it."""
        self.load_ptr(writer, raw, node)

    def name_of(self, node: Any) -> str | None:
        if isinstance(node, dict):
            return node.get("name")
        return getattr(node, "name", None)


REGISTRY: dict[int, Handler] = {}


def register(cls: type[Handler]) -> type[Handler]:
    """Class decorator: register one handler instance for its asset type."""
    if cls.asset_type in REGISTRY:
        raise ValueError(f"asset type {cls.asset_type} already has a handler")
    REGISTRY[cls.asset_type] = cls()
    return cls


def handler_for(asset_type: int) -> Handler | None:
    return REGISTRY.get(asset_type)


def load_asset(io: XStream, asset_type: int, raw: int, node: Any = None) -> Any:
    """Load (or write) an asset pointer of the given type through its handler."""
    handler = REGISTRY.get(asset_type)
    if handler is None:
        raise io.fail(f"asset type {asset_type} ({type_name(asset_type)}) has no loader")
    return handler.load_ptr(io, raw, node)


def asset_ref(io: XStream, chunk: Chunk, off: int, asset_type: int, node: Any, key: Any) -> Any:
    """An asset reference field inside a struct: logged, then Load_<X>Ptr; the child
    node (or AssetLink) is node[key]."""
    raw = io.ref(chunk, off)
    child = None if io.reading else node[key]
    result = load_asset(io, asset_type, raw, child)
    if io.reading:
        node[key] = result
        if chunk.block not in (Block.TEMP, NONE):
            # Later alias pointers may name this field: it holds the asset's address.
            field_key = (chunk.block << OFFSET_BLOCK_SHIFT) | (chunk.mem + off)
            if isinstance(result, AssetLink):
                io.refs.chain[field_key] = (raw - 1) & 0xFFFFFFFF
            elif result is not None:
                io.refs.slot(field_key, result, io.asset_index)
    return result


def array(
    io: XStream,
    chunk: Chunk,
    off: int,
    mask: int,
    size: int,
    node: Any,
    key: Any,
    owned: bool = False,
) -> Chunk | None:
    """A pointer to data the loader reads with one Load_Stream: when it follows
    inline, align and load it into node[key]; else node[key] is None."""
    if io.follows(chunk, off, owned):
        io.alloc(mask)
        return io.load(size, node, key)
    if io.reading:
        node[key] = None
    return None


def items(
    io: XStream,
    chunk: Chunk,
    off: int,
    mask: int,
    size: int,
    count: int,
    node: Any,
    key: Any,
    owned: bool = False,
    kind: str | None = None,
) -> list[tuple[Chunk, dict]] | None:
    """A pointer to an array of `count` structs: when inline, align and load them
    as element nodes in node[key] (each tagged `kind`); returns (chunk, element)
    pairs, else None."""
    if io.follows(chunk, off, owned):
        io.alloc(mask)
        return io.items(size, count, node, key, kind)
    if io.reading:
        node[key] = None
    return None


def runtime(io: XStream, chunk: Chunk, off: int, mask: int, size: int) -> None:
    """A non-zero pointer to RUNTIME memory: push RUNTIME, align, reserve, pop.
    No file bytes."""
    if io.follows(chunk, off, owned=True):
        io.push(Block.RUNTIME)
        io.alloc(mask)
        io.reserve(size)
        io.pop()
