"""Where pointers lead: alias references to assets, offset pointers to data.

A parse records, as it goes:

* every allocation outside TEMP: the block range a Load_Stream, string or
  deferred / RUNTIME reservation occupied, and the node and key that hold the
  loaded thing (``Refs.record``);
* every asset loaded inline through a pointer field outside TEMP, by the field's
  block position, and every -2 alias slot (``Refs.slots``): both later hold a
  pointer to that asset, so an alias reference (DB_ConvertOffsetToAlias) naming
  either one means that asset;
* every alias reference field, by its block position (``Refs.chain``): a later
  alias may name the field itself, which in turn names its asset.

``Refs.resolve(value)`` turns a raw pointer value into a ``Target``: the asset
an alias slot holds, or the node, key, element and byte offset of the data an
offset pointer names.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, field
from typing import Any

from opent5.xfile.constants import BLOCK_COUNT, OFFSET_BLOCK_SHIFT, OFFSET_MASK, Block

#: An alias chain longer than this is reported unresolved rather than followed.
MAX_CHAIN = 16


@dataclass
class Target:
    """What a pointer value names."""

    block: int
    offset: int
    #: "asset" (an alias slot), "data" (inside an allocation; see also ``asset``),
    #: "runtime" (RUNTIME memory, no file bytes), or "unknown".
    kind: str
    #: The node holding the target (the asset node itself for kind "asset").
    node: Any = field(default=None, repr=False)
    #: The key in that node (None for an asset), and the element of a list of
    #: structs with its index, when the target lies inside one.
    key: Any = None
    element: Any = field(default=None, repr=False)
    index: int | None = None
    #: Byte offset of the target from the start of node[key] (or of the element).
    within: int = 0
    #: Index of the top-level asset whose load put it there.
    asset_index: int = -1
    #: When the location holds a pointer to an asset (an alias slot, or the field
    #: that loaded an asset inline): that asset's node. An alias reference means
    #: this; an offset pointer to the same place means the data around it.
    asset: Any = field(default=None, repr=False)

    @property
    def name(self) -> str | None:
        """The text, for a string; else the nearest name: the element's, the node's
        (for an asset target, the asset's)."""
        if self.kind == "data" and isinstance(self.node, dict | list):
            try:
                value = self.node[self.key]
            except (KeyError, IndexError, TypeError):
                value = None
            if isinstance(value, str):
                return value
        for holder in (self.element, self.node, self.asset):
            if isinstance(holder, dict) and holder.get("name") is not None:
                return holder["name"]
        return None


class Refs:
    """Allocation and alias-slot records of one parse; see the module docstring."""

    def __init__(self) -> None:
        self.starts: list[list[int]] = [[] for _ in range(BLOCK_COUNT)]
        self.records: list[list[tuple]] = [[] for _ in range(BLOCK_COUNT)]
        #: memory key -> (asset node, top-level asset index)
        self.slots: dict[int, tuple[Any, int]] = {}
        #: memory key of an alias reference field -> the memory key it names
        self.chain: dict[int, int] = {}

    def record(
        self,
        block: int,
        mem: int,
        size: int,
        node: Any,
        key: Any,
        element_size: int,
        asset_index: int,
    ) -> None:
        if block == Block.TEMP or size <= 0:
            return
        self.starts[block].append(mem)
        self.records[block].append((mem, size, node, key, element_size, asset_index))

    def slot(self, key: int, node: Any, asset_index: int) -> None:
        self.slots[key] = (node, asset_index)

    def asset_at(self, key: int) -> tuple[Any, int] | None:
        """The asset an alias names, following alias fields that name alias fields."""
        for _ in range(MAX_CHAIN):
            found = self.slots.get(key)
            if found is not None:
                return found
            key = self.chain.get(key, -1)
            if key < 0:
                return None
        return None

    def at(self, block: int, offset: int) -> Target:
        """The allocation containing (block, offset)."""
        starts = self.starts[block] if 0 <= block < BLOCK_COUNT else []
        i = bisect_right(starts, offset) - 1
        if i < 0:
            return Target(block, offset, "unknown")
        mem, size, node, key, element_size, asset_index = self.records[block][i]
        if offset >= mem + size:
            return Target(block, offset, "unknown")
        within = offset - mem
        if node is None:
            kind = "runtime" if block == Block.RUNTIME else "unknown"
            return Target(block, offset, kind, within=within, asset_index=asset_index)
        target = Target(block, offset, "data", node, key, within=within, asset_index=asset_index)
        if element_size:
            index, inner = divmod(within, element_size)
            elements = node[key]
            if isinstance(elements, list) and index < len(elements):
                target.index = index
                target.element = elements[index]
                target.within = inner
        return target

    def resolve(self, value: int) -> Target | None:
        """A raw pointer value ((block << 29 | offset) + 1) -> Target; None for 0,
        -1 and -2, which name nothing yet."""
        if value in (0, 0xFFFFFFFF, 0xFFFFFFFE):
            return None
        key = (value - 1) & 0xFFFFFFFF
        block, offset = key >> OFFSET_BLOCK_SHIFT, key & OFFSET_MASK
        target = self.at(block, offset)
        asset = self.asset_at(key)
        if asset is not None:
            if target.kind == "unknown":
                # An alias slot reserved by DB_InsertPointer: no allocation holds it.
                return Target(
                    block, offset, "asset", asset[0], asset_index=asset[1], asset=asset[0]
                )
            target.asset = asset[0]
        return target
