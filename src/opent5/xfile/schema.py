"""Typed fields over the raw bytes a parse keeps: struct schemas and views.

Nodes keep the exact bytes of every struct and array the loader reads; that is
what makes writing back byte-identical. This module puts names and types on
those bytes without copying them:

    from opent5.xfile.schema import view
    v = view(asset.data)                  # a NodeView
    v.fields.numsurfs                     # a field of the asset's header struct
    v.fields["lodInfo[0].dist"] = 512.0   # written in place, big-endian
    v.array("base_mat")                   # numpy structured array over the bytes
    v.to_dict()                           # the whole asset tree, JSON-ready

A ``Struct`` is a list of ``Field``s (name, offset, type) with every byte of
the struct covered: gaps nobody has named yet are filled with ``unk_0x..``
fields, so coverage is always total and the unnamed share is explicit.
Pointer and count fields are read-only through a view: changing one means
changing the arrays it describes and re-laying the zone (edit the node's
arrays, then ``write`` and remap), not poking a number.

Each node says what it is with a ``"_t"`` key (its kind, set by the handler
that filled it); ``KINDS`` (in ``opent5.xfile.structs``) maps a kind to the
schema of each byte-valued key of such a node.
"""

from __future__ import annotations

import math
import re
import struct
from dataclasses import dataclass, field
from typing import Any

import numpy as np

#: Scalar and fixed-vector types: struct format (big-endian), byte size.
SCALARS: dict[str, tuple[str, int]] = {
    "u8": ("B", 1),
    "s8": ("b", 1),
    "u16": ("H", 2),
    "s16": ("h", 2),
    "u32": ("I", 4),
    "s32": ("i", 4),
    "u64": ("Q", 8),
    "s64": ("q", 8),
    "f16": ("e", 2),
    "f32": ("f", 4),
    "f64": ("d", 8),
    "ptr": ("I", 4),
    "vec2": ("2f", 8),
    "vec3": ("3f", 12),
    "vec4": ("4f", 16),
    "mat3": ("9f", 36),
    "mat4": ("16f", 64),
    #: A unit vector packed by the RSX as CMP, 11:11:10 signed normalised (see unpack_cmp).
    "cmp": ("I", 4),
}
#: numpy dtypes (big-endian) for the scalar types.
_DTYPES = {
    "u8": ">u1",
    "s8": ">i1",
    "u16": ">u2",
    "s16": ">i2",
    "u32": ">u4",
    "s32": ">i4",
    "u64": ">u8",
    "s64": ">i8",
    "f16": ">f2",
    "f32": ">f4",
    "f64": ">f8",
    "ptr": ">u4",
    "cmp": ">u4",
    "vec2": (">f4", (2,)),
    "vec3": (">f4", (3,)),
    "vec4": (">f4", (4,)),
    "mat3": (">f4", (9,)),
    "mat4": (">f4", (16,)),
}
_TYPE = re.compile(r"^(\w+)(?:\[(\d+)\])?$")

#: Roles that make a field read-only through a view.
READ_ONLY = ("ptr", "count")


class FieldError(Exception):
    """A field that cannot be read or set as asked; says why."""


def parse_type(text: str) -> tuple[str, int]:
    """'vec3' -> ('vec3', 1); 'u16[4]' -> ('u16', 4); 'char[64]' -> ('char', 64)."""
    m = _TYPE.match(text)
    if not m:
        raise ValueError(f"field type {text!r}")
    base, count = m.group(1), int(m.group(2) or 1)
    if base not in SCALARS and base not in ("char", "bytes"):
        raise ValueError(f"field type {text!r}: unknown base {base!r}")
    return base, count


def type_size(text: str) -> int:
    base, count = parse_type(text)
    return (1 if base in ("char", "bytes") else SCALARS[base][1]) * count


def unpack_cmp(word: int) -> tuple[float, float, float]:
    """CMP, the RSX's packed vector: x in bits 0..10, y in 11..21 (signed, /1023),
    z in 22..31 (signed, /511). Not renormalised. Evidence: docs/extract.md 3.1."""
    x, y, z = word & 0x7FF, (word >> 11) & 0x7FF, (word >> 22) & 0x3FF
    x = x - 0x800 if x >= 0x400 else x
    y = y - 0x800 if y >= 0x400 else y
    z = z - 0x400 if z >= 0x200 else z
    return (x / 1023.0, y / 1023.0, z / 511.0)


def unpack_cmp_array(words: np.ndarray) -> np.ndarray:
    """u32 CMP words (any shape) -> float64 vectors (..., 3)."""
    w = np.asarray(words, dtype=np.uint32).astype(np.int64)
    x, y, z = w & 0x7FF, (w >> 11) & 0x7FF, (w >> 22) & 0x3FF
    x = np.where(x >= 0x400, x - 0x800, x) / 1023.0
    y = np.where(y >= 0x400, y - 0x800, y) / 1023.0
    z = np.where(z >= 0x200, z - 0x400, z) / 511.0
    return np.stack([x, y, z], -1)


@dataclass(frozen=True)
class Field:
    name: str
    offset: int
    type: str
    #: None, "ptr" (a pointer the loader follows or converts), "count" (sizes an
    #: array), "scrstr" (script string index), "enum", "flags".
    role: str | None = None
    #: For counts: the node key of the array this counts (and its element size).
    counts: str | None = None
    #: For enums: value -> name.
    names: dict | None = None
    #: True for gap fillers nobody has named.
    unknown: bool = False

    @property
    def size(self) -> int:
        return type_size(self.type)

    @property
    def read_only(self) -> bool:
        return self.role in READ_ONLY

    def decode(self, data, base: int = 0) -> Any:
        kind, count = parse_type(self.type)
        at = base + self.offset
        if kind == "char":
            raw = bytes(data[at : at + count])
            return raw.split(b"\0", 1)[0].decode("latin-1")
        if kind == "bytes":
            return bytes(data[at : at + count])
        fmt, size = SCALARS[kind]
        if count == 1:
            values = struct.unpack_from(">" + fmt, data, at)
            return values[0] if len(values) == 1 else values
        out = []
        for i in range(count):
            values = struct.unpack_from(">" + fmt, data, at + i * size)
            out.append(values[0] if len(values) == 1 else values)
        return out

    def encode(self, value: Any) -> bytes:
        kind, count = parse_type(self.type)
        if kind == "char":
            raw = value.encode("latin-1") if isinstance(value, str) else bytes(value)
            if len(raw) > count:
                raise FieldError(f"{self.name}: {len(raw)} bytes do not fit char[{count}]")
            return raw + bytes(count - len(raw))
        if kind == "bytes":
            raw = bytes(value)
            if len(raw) != count:
                raise FieldError(f"{self.name}: expected {count} bytes, found {len(raw)}")
            return raw
        fmt, _ = SCALARS[kind]
        values = [value] if count == 1 else list(value)
        if len(values) != count:
            raise FieldError(f"{self.name}: expected {count} values, found {len(values)}")
        out = bytearray()
        for v in values:
            out += struct.pack(">" + fmt, *(v if isinstance(v, list | tuple) else (v,)))
        return bytes(out)

    def dtype(self):
        kind, count = parse_type(self.type)
        if kind in ("char", "bytes"):
            return f"S{count}" if kind == "char" else ("u1", (count,))
        dt = _DTYPES[kind]
        if count == 1:
            return dt
        if isinstance(dt, tuple):
            return (dt[0], (count, *dt[1]))
        return (dt, (count,))


@dataclass
class Struct:
    """A named layout of `size` bytes. Gaps are filled with unk_ fields."""

    name: str
    size: int
    fields: list[Field] = field(default_factory=list)
    #: Where the names come from (a document section, an ELF address, a PC header).
    source: str = ""

    def __post_init__(self) -> None:
        self.fields = _complete(self.name, self.size, self.fields)
        self.by_name = {f.name: f for f in self.fields}

    def field(self, name: str) -> Field:
        try:
            return self.by_name[name]
        except KeyError:
            raise FieldError(f"{self.name} has no field {name!r}") from None

    def coverage(self) -> tuple[int, int]:
        """(bytes covered by named fields, bytes covered by unk_ fields)."""
        named = sum(f.size for f in self.fields if not f.unknown)
        return named, self.size - named

    def dtype(self) -> np.dtype:
        names = [f.name for f in self.fields]
        formats = [f.dtype() for f in self.fields]
        offsets = [f.offset for f in self.fields]
        return np.dtype(
            {"names": names, "formats": formats, "offsets": offsets, "itemsize": self.size}
        )

    def decode(self, data, base: int = 0) -> dict:
        return {f.name: f.decode(data, base) for f in self.fields}


def _complete(name: str, size: int, fields: list[Field]) -> list[Field]:
    ordered = sorted(fields, key=lambda f: f.offset)
    out: list[Field] = []
    at = 0
    for f in ordered:
        if f.offset < at:
            raise ValueError(f"{name}: field {f.name} at {f.offset:#x} overlaps the one before")
        if f.offset > at:
            out.append(Field(f"unk_{at:#x}", at, f"bytes[{f.offset - at}]", unknown=True))
        out.append(f)
        at = f.offset + f.size
    if at > size:
        raise ValueError(f"{name}: fields end at {at:#x}, past the size {size:#x}")
    if at < size:
        out.append(Field(f"unk_{at:#x}", at, f"bytes[{size - at}]", unknown=True))
    return out


@dataclass(frozen=True)
class ArrayOf:
    """A byte value holding a run of elements: a Struct, a scalar type name, or
    "text" (the whole value is Latin-1 text, NUL-terminated or not)."""

    element: Struct | str

    @property
    def element_size(self) -> int:
        if isinstance(self.element, Struct):
            return self.element.size
        if self.element == "text":
            return 1
        return type_size(self.element)

    def dtype(self):
        if isinstance(self.element, Struct):
            return self.element.dtype()
        if self.element == "text":
            return np.dtype("u1")
        return np.dtype(Field("v", 0, self.element).dtype())


@dataclass(frozen=True)
class Dynamic:
    """A schema chosen per node from the node's own bytes (vertex formats that
    depend on a surface's flags). ``choose(node)`` returns a Struct, an ArrayOf or
    None (opaque)."""

    choose: Any
    note: str = ""


Spec = Struct | ArrayOf | Dynamic


def resolve(spec: Any, node: Any) -> Struct | ArrayOf | None:
    if isinstance(spec, Dynamic):
        return spec.choose(node)
    return spec


class StructView:
    """Named, typed access to one struct inside a node's bytes, in place."""

    __slots__ = ("_struct", "_node", "_key", "_base")

    def __init__(self, struct_: Struct, node: Any, key: Any, base: int = 0):
        object.__setattr__(self, "_struct", struct_)
        object.__setattr__(self, "_node", node)
        object.__setattr__(self, "_key", key)
        object.__setattr__(self, "_base", base)

    @property
    def struct(self) -> Struct:
        return self._struct

    def keys(self) -> list[str]:
        return [f.name for f in self._struct.fields]

    def __getitem__(self, name: str) -> Any:
        return self._struct.field(name).decode(self._node[self._key], self._base)

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        try:
            return self[name]
        except FieldError as exc:
            raise AttributeError(str(exc)) from None

    def __setitem__(self, name: str, value: Any) -> None:
        f = self._struct.field(name)
        if f.read_only:
            target = f" (it sizes {f.counts!r})" if f.counts else ""
            raise FieldError(
                f"{self._struct.name}.{name} is a {f.role} field{target} and is read-only "
                "here: change the arrays it describes in the node, then write the zone "
                "and remap, rather than setting the number"
            )
        data = self._node[self._key]
        if f.type.startswith("char[") and f.decode(data, self._base) == value:
            # Setting a string to its own value keeps any bytes after its NUL.
            return
        encoded = f.encode(value)
        if not isinstance(data, bytearray):
            data = bytearray(data)
            self._node[self._key] = data
        at = self._base + f.offset
        data[at : at + len(encoded)] = encoded

    def __setattr__(self, name: str, value: Any) -> None:
        self[name] = value

    def to_dict(self) -> dict:
        return self._struct.decode(self._node[self._key], self._base)

    def __repr__(self) -> str:
        return f"<{self._struct.name} view of {self._key!r}>"


class NodeView:
    """A node with its schema: ``fields`` (its main struct), ``struct(key)``,
    ``array(key)``, child views, and ``to_dict``."""

    def __init__(self, node: dict, kinds: dict | None = None, xfile: Any = None):
        from opent5.xfile.structs import KINDS  # late: structs imports this module

        self.node = node
        self.xfile = xfile
        self.kinds = kinds if kinds is not None else KINDS
        self.kind = node.get("_t") if isinstance(node, dict) else None
        self.schema: dict = self.kinds.get(self.kind, {}) if self.kind else {}

    @property
    def fields(self) -> StructView | None:
        """The node's own struct: "header" for an asset, "raw" for an element."""
        for key in ("header", "raw"):
            spec = self.schema.get(key)
            if isinstance(spec, Struct) and self.node.get(key) is not None:
                return StructView(spec, self.node, key)
        return None

    def spec(self, key: Any) -> Struct | ArrayOf | None:
        return resolve(self.schema.get(key), self.node)

    def struct(self, key: Any, index: int | None = None) -> StructView:
        """The struct at node[key] (element `index` of it, for an array of structs)."""
        spec = self.spec(key)
        if isinstance(spec, Struct):
            return StructView(spec, self.node, key)
        if isinstance(spec, ArrayOf) and isinstance(spec.element, Struct):
            return StructView(spec.element, self.node, key, (index or 0) * spec.element.size)
        raise FieldError(f"{self.kind}[{key!r}] has no struct schema")

    def array(self, key: Any, writable: bool = False) -> np.ndarray:
        """node[key] as a numpy structured (or scalar) array over its bytes."""
        spec = self.spec(key)
        data = self.node.get(key)
        if data is None:
            raise FieldError(f"{self.kind}[{key!r}] is absent")
        if isinstance(spec, Struct):
            spec = ArrayOf(spec)
        if not isinstance(spec, ArrayOf):
            raise FieldError(f"{self.kind}[{key!r}] has no array schema")
        if writable and not isinstance(data, bytearray):
            data = bytearray(data)
            self.node[key] = data
        size = spec.element_size
        if len(data) % size:
            raise FieldError(f"{self.kind}[{key!r}]: {len(data)} bytes is not a multiple of {size}")
        return np.frombuffer(data, dtype=spec.dtype(), count=len(data) // size)

    def child(self, key: Any) -> Any:
        value = self.node[key]
        if isinstance(value, dict):
            return NodeView(value, self.kinds, self.xfile)
        if isinstance(value, list):
            return [
                NodeView(v, self.kinds, self.xfile) if isinstance(v, dict) else v for v in value
            ]
        return value

    def target(self, name: str, key: Any = None, index: int | None = None) -> Any:
        """Resolve pointer field `name` of the node's own struct (or of the struct at
        `key`, element `index`): a Target for an offset or alias pointer, a list of
        them for a pointer array, None for null and inline (-1 / -2) pointers, whose
        data sits in this node's own keys. Needs a view made by ``xfile.view``."""
        if self.xfile is None:
            raise FieldError("target() needs a view made with xfile.view(node)")
        view = self.fields if key is None else self.struct(key, index)
        if view is None:
            raise FieldError(f"{self.kind} has no struct to read {name!r} from")
        f = view.struct.field(name)
        if f.role != "ptr":
            raise FieldError(f"{view.struct.name}.{name} is not a pointer field")
        value = view[name]
        if isinstance(value, list):
            return [self.xfile.resolve(v) for v in value]
        return self.xfile.resolve(value)

    def to_dict(self, max_items: int | None = None) -> Any:
        return to_dict(self.node, self.kinds, max_items)


def view(node: dict, xfile: Any = None) -> NodeView:
    return NodeView(node, xfile=xfile)


def to_dict(node: Any, kinds: dict | None = None, max_items: int | None = None) -> Any:
    """A JSON-ready tree: struct bytes decoded into named fields, arrays of
    structs into lists of dicts (scalar arrays into lists), opaque blobs as
    {"bytes": n}, child nodes recursively. max_items truncates long arrays."""
    from opent5.xfile.stream import AssetLink, DeferredData

    if kinds is None:
        from opent5.xfile.structs import KINDS

        kinds = KINDS
    if isinstance(node, dict):
        schema = kinds.get(node.get("_t"), {}) if "_t" in node else {}
        out: dict = {}
        for key, value in node.items():
            if key == "_t":
                out["_kind"] = value
                continue
            spec = resolve(schema.get(key), node)
            if isinstance(value, bytes | bytearray) and spec is not None:
                out[str(key)] = _decode_spec(spec, value, max_items)
            else:
                out[str(key)] = to_dict(value, kinds, max_items)
        return out
    if isinstance(node, list):
        items = node if max_items is None else node[:max_items]
        return [to_dict(v, kinds, max_items) for v in items]
    if isinstance(node, bytes | bytearray | memoryview):
        return {"bytes": len(node)}
    if isinstance(node, AssetLink):
        return {"alias": node.raw, "type": node.asset_type, "name": node.name}
    if isinstance(node, DeferredData):
        return {"deferred": node.index, "bytes": node.size, "file_offset": node.file_offset}
    if isinstance(node, float) and not math.isfinite(node):
        return repr(node)
    return node


#: u8 arrays longer than this are reported as blobs by to_dict.
BLOB_BYTES = 256


def _decode_spec(spec: Spec, data: bytes, max_items: int | None) -> Any:
    if isinstance(spec, Struct):
        return _jsonable(spec.decode(data))
    if spec.element == "text":
        return bytes(data).split(b"\0", 1)[0].decode("latin-1")
    if spec.element == "u8" and len(data) > BLOB_BYTES:
        return {"bytes": len(data)}
    size = spec.element_size
    count = len(data) // size
    if max_items is not None:
        count = min(count, max_items)
    if isinstance(spec.element, Struct):
        return [_jsonable(spec.element.decode(data, i * size)) for i in range(count)]
    f = Field("v", 0, spec.element)
    return [_jsonable(f.decode(data, i * size)) for i in range(count)]


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_jsonable(v) for v in value]
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, float) and not math.isfinite(value):
        return repr(value)
    return value
