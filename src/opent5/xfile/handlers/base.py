"""Handler interface, the registry, and the asset-pointer pattern every type shares.

A handler owns one asset type. Its read side is the type's struct loader: given
the header the loader has just read (in TEMP), it loads everything the header
owns, through the ``XStream`` primitives, in the loader's order, and returns
structured data. Its write side turns that data back into the stream bytes of
the asset; it is implemented for the tier-1 types and raises
NotImplementedError elsewhere, so a type can be promoted to read/write by
filling in ``write`` without touching anything else.
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
from opent5.xfile.stream import AssetLink, Chunk, XStream


class Handler:
    """One asset type. Subclasses set the class attributes and implement ``read``."""

    #: XAssetType value.
    asset_type: ClassVar[int]
    #: sizeof the header struct (the first Load_Stream, in TEMP).
    header_size: ClassVar[int]
    #: DB_AllocStreamPos mask before the header (3 for every type but menu).
    align: ClassVar[int] = 3
    #: Whether ``write`` is implemented.
    writable: ClassVar[bool] = False

    @property
    def name(self) -> str:
        return type_name(self.asset_type)

    def read(self, st: XStream, header: Chunk) -> Any:
        raise NotImplementedError(f"{self.name}: no struct loader")

    def write(self, data: Any, writer: Any) -> None:
        """Emit the stream bytes of one asset (header first) into ``writer``."""
        raise NotImplementedError(
            f"{self.name} (type {self.asset_type}): writing is not implemented yet; this "
            "handler is read-only"
        )

    def load_ptr(self, st: XStream, raw: int) -> Any:
        """The per-type Load_<X>Ptr: push TEMP; -1 / -2 load the asset right here
        (header in TEMP, aligned; -2 also reserves an alias slot in VIRTUAL);
        another non-zero value is an alias pointer; pop (TEMP rewinds)."""
        st.push(Block.TEMP)
        data: Any = None
        if raw in (PTR_INLINE, PTR_INSERT):
            st.trail.append(self.name)
            st.alloc(self.align)
            slot = st.insert() if raw == PTR_INSERT else None
            header = st.load(self.header_size)
            data = self.read(st, header)
            if slot is not None:
                st.slots[(Block.VIRTUAL << OFFSET_BLOCK_SHIFT) | slot] = data
            st.trail.pop()
        elif raw != PTR_NULL:
            block, offset = decode_offset_pointer(raw)
            target = st.slots.get((raw - 1) & 0xFFFFFFFF)
            data = AssetLink(self.asset_type, raw, block, offset, target)
        st.pop()
        return data

    def name_of(self, data: Any) -> str | None:
        if isinstance(data, dict):
            return data.get("name")
        return getattr(data, "name", None)


REGISTRY: dict[int, Handler] = {}


def register(cls: type[Handler]) -> type[Handler]:
    """Class decorator: register one handler instance for its asset type."""
    if cls.asset_type in REGISTRY:
        raise ValueError(f"asset type {cls.asset_type} already has a handler")
    REGISTRY[cls.asset_type] = cls()
    return cls


def handler_for(asset_type: int) -> Handler | None:
    return REGISTRY.get(asset_type)


def load_asset(st: XStream, asset_type: int, raw: int) -> Any:
    """Load an asset pointer of the given type through its handler."""
    handler = REGISTRY.get(asset_type)
    if handler is None:
        raise st.fail(f"asset type {asset_type} ({type_name(asset_type)}) has no loader")
    return handler.load_ptr(st, raw)


def asset_ref(st: XStream, chunk: Chunk, off: int, asset_type: int) -> Any:
    """An asset reference field inside a struct: logged, then Load_<X>Ptr."""
    return load_asset(st, asset_type, st.ref(chunk, off))


def array(st: XStream, chunk: Chunk, off: int, mask: int, size: int, owned: bool = False):
    """A pointer to an array the loader reads with one Load_Stream: when it
    follows inline, align and read it; returns the Chunk, else None."""
    if st.follows(chunk, off, owned):
        st.alloc(mask)
        return st.load(size)
    return None


def runtime(st: XStream, chunk: Chunk, off: int, mask: int, size: int) -> None:
    """A non-zero pointer to RUNTIME memory: push RUNTIME, align, reserve, pop.
    No file bytes."""
    if st.follows(chunk, off, owned=True):
        st.push(Block.RUNTIME)
        st.alloc(mask)
        st.reserve(size)
        st.pop()


def blob(chunk: Chunk | None) -> bytes | None:
    return None if chunk is None else chunk.bytes()
