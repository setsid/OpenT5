"""Finding every asset in a parsed zone, and following the pointers it holds.

A zone lists only its top-level assets; most images, materials, techsets,
shaders and models are loaded inline inside other assets (a material inside a
GfxWorld, an image inside a material). ``AssetIndex`` walks the parsed data and
collects every asset node of the types the exporter writes, by name, using the
node kind (``"_t"``) each handler sets.

References are the parser's: an alias reference (``AssetLink``) carries its
target node, and ``XFile.resolve`` turns an offset pointer (a shared vertex
buffer, a brush side's plane) into the node and key holding the data
(``opent5.xfile.refs``). ``Resolver`` wraps both for the exporter.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterator
from typing import Any

from opent5.xfile.constants import AssetType, type_name
from opent5.xfile.stream import AssetLink, DeferredData

#: Node kind ("_t") -> asset type, for the asset kinds the exporter collects.
KIND_TYPES = {
    "GfxImage": AssetType.IMAGE,
    "Material": AssetType.MATERIAL,
    "XModel": AssetType.XMODEL,
    "MaterialTechniqueSet": AssetType.TECHSET,
    "MaterialVertexShader": AssetType.VERTEXSHADER,
    "MaterialPixelShader": AssetType.PIXELSHADER,
    "GfxLightDef": AssetType.LIGHTDEF,
    "PhysPreset": AssetType.PHYSPRESET,
    "PhysConstraints": AssetType.PHYSCONSTRAINTS,
    "FxEffectDef": AssetType.FX,
}


def classify(node: Any) -> int | None:
    """The asset type of a handler's output node, from its kind; None for anything
    that is not an asset node of a collected kind."""
    if not isinstance(node, dict):
        return None
    return KIND_TYPES.get(node.get("_t"))


def children(obj: Any) -> Iterator[Any]:
    if isinstance(obj, dict):
        yield from obj.values()
    elif isinstance(obj, list | tuple):
        yield from obj
    elif (
        dataclasses.is_dataclass(obj)
        and not isinstance(obj, type)
        and not isinstance(obj, AssetLink)
    ):
        for f in dataclasses.fields(obj):
            yield getattr(obj, f.name)


def walk(root: Any) -> Iterator[Any]:
    """Every node under root, depth first, each container once."""
    seen: set[int] = set()
    stack = [root]
    while stack:
        obj = stack.pop()
        if isinstance(obj, bytes | bytearray | memoryview | str | int | float) or obj is None:
            continue
        if id(obj) in seen:
            continue
        seen.add(id(obj))
        yield obj
        kids = list(children(obj))
        kids.reverse()
        stack.extend(kids)


class AssetIndex:
    """Every asset node in a parsed zone, top level and inline, by type and name.

    ``top`` keeps the zone's own asset list order; ``by_type[t]`` maps a name to
    its first node (a name loaded twice is the same asset)."""

    def __init__(self, xfile):
        self.xfile = xfile
        self.by_type: dict[int, dict[str, Any]] = {}
        self.unnamed: dict[int, int] = {}
        for asset in xfile.assets:
            if asset.data is None or isinstance(asset.data, AssetLink):
                continue
            self._add(asset.type, asset.data)
            for node in walk(asset.data):
                t = classify(node)
                if t is not None and node is not asset.data:
                    self._add(t, node)

    def _add(self, asset_type: int, node: Any) -> None:
        name = node.get("name") if isinstance(node, dict) else getattr(node, "name", None)
        if name is None:
            self.unnamed[asset_type] = self.unnamed.get(asset_type, 0) + 1
            return
        self.by_type.setdefault(asset_type, {}).setdefault(name, node)

    def of(self, asset_type: int) -> dict[str, Any]:
        return self.by_type.get(asset_type, {})

    def get(self, asset_type: int, name: str | None) -> Any:
        if name is None:
            return None
        return self.by_type.get(asset_type, {}).get(name)

    def counts(self) -> dict[str, int]:
        return {type_name(t): len(v) for t, v in sorted(self.by_type.items())}


class Resolver:
    """Names and nodes behind asset references, and bytes behind offset pointers."""

    def __init__(self, index: AssetIndex, xfile: Any):
        self.index = index
        self.xfile = xfile

    def name(self, ref: Any) -> str | None:
        if ref is None:
            return None
        if isinstance(ref, AssetLink):
            return ref.name
        if isinstance(ref, dict):
            return ref.get("name")
        return getattr(ref, "name", None)

    def node(self, ref: Any) -> Any:
        if isinstance(ref, AssetLink):
            return ref.target
        return ref

    def target(self, raw: int):
        """``XFile.resolve`` of a raw offset pointer: the node, key and byte offset of
        the data it names (None for 0, -1, -2 or an unknown position)."""
        found = self.xfile.resolve(raw)
        if found is None or found.kind != "data":
            return None
        return found

    def read(self, raw: int, size: int) -> bytes | None:
        """``size`` bytes at an offset pointer, from the node that loaded them."""
        found = self.target(raw)
        if found is None:
            return None
        holder = found.node[found.key]
        if isinstance(holder, list):  # a list of struct elements: the element's raw bytes
            if found.element is None:
                return None
            holder = found.element.get("raw")
        if not isinstance(holder, bytes | bytearray | memoryview):
            return None
        data = bytes(holder[found.within : found.within + size])
        return data if len(data) == size else None


def pixel_bytes(pixels: Any) -> bytes | None:
    """An image's zone-held pixels: inline bytes or the deferred tail slice."""
    if pixels is None:
        return None
    if isinstance(pixels, DeferredData):
        return None if pixels.data is None else bytes(pixels.data)
    return bytes(pixels)
