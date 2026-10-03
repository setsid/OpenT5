"""Coverage, round-trip and sanity checks of the field schemas over parsed zones.

Used by tools/fields_all.py and the tests. For one parsed zone, ``check_zone``
walks every node, and for every byte value with a schema:

* coverage: bytes of each struct type under named fields vs unk_ fields, and
  byte values (keys) that have no schema at all;
* round trip: every field's bytes decode and re-encode to themselves (for
  single structs and array elements the view's own set path is used; for long
  homogeneous arrays the same conversions run vectorised in numpy);
* sanity, to show the types are right on real data: float fields are finite and
  of plausible magnitude, unit-vector fields (normals, directions) have length
  one, counts equal the length of the arrays they size, and positions lie inside
  the asset's bounds where the asset declares them.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from opent5.xfile.constants import AssetType
from opent5.xfile.schema import (
    ArrayOf,
    Field,
    Struct,
    StructView,
    parse_type,
    resolve,
    unpack_cmp_array,
)
from opent5.xfile.stream import DeferredData
from opent5.xfile.structs import KINDS

FLOAT_TYPES = ("f32", "f16", "f64", "vec2", "vec3", "vec4", "mat3", "mat4")
#: Magnitude beyond which a float is reported as implausible (+-FLT_MAX, used as
#: "no bound" sentinels, is allowed).
FLOAT_LIMIT = 1.0e12
FLT_MAX = 3.4028234663852886e38
#: Non-zero floats below the smallest normal float are denormals: almost always an
#: integer read as a float.
FLOAT_TINY = 1.1754943508222875e-38
#: vec3 fields that hold positions in the world (or the asset): checked against bounds.
POSITION_NAMES = ("xyz", "origin", "placement.origin", "constant.vOrigin", "lightingOrigin")
#: Assets whose positions lie inside the zone's GfxWorld bounds.
WORLD_TYPES = (
    AssetType.GFX_MAP,
    AssetType.COL_MAP_MP,
    AssetType.COL_MAP_SP,
    AssetType.GAME_MAP_MP,
    AssetType.GAME_MAP_SP,
    AssetType.COM_MAP,
)


@dataclass
class StructStats:
    instances: int = 0
    bytes: int = 0
    named: int = 0
    unknown: int = 0
    round_trip_failures: int = 0
    #: char[n] values with non-zero bytes after the NUL (kept by an identity set;
    #: a new string clears them).
    char_tails: int = 0


@dataclass
class FieldStats:
    values: int = 0
    bad: int = 0
    example: Any = None
    reason: str = ""


@dataclass
class ZoneCheck:
    structs: dict = field(default_factory=lambda: defaultdict(StructStats))
    #: (kind, key) -> bytes held by values with no schema.
    untyped: dict = field(default_factory=lambda: defaultdict(int))
    #: kinds met with no KINDS entry -> node count.
    unknown_kinds: dict = field(default_factory=lambda: defaultdict(int))
    #: "Struct.field: check" -> FieldStats.
    sanity: dict = field(default_factory=lambda: defaultdict(FieldStats))
    blob_bytes: int = 0

    def merge(self, other: dict) -> None:
        for name, s in other["structs"].items():
            mine = self.structs[name]
            for k in (
                "instances",
                "bytes",
                "named",
                "unknown",
                "round_trip_failures",
                "char_tails",
            ):
                setattr(mine, k, getattr(mine, k) + s.get(k, 0))
        for k, v in other["untyped"].items():
            self.untyped[k] += v
        for k, v in other["unknown_kinds"].items():
            self.unknown_kinds[k] += v
        for k, s in other["sanity"].items():
            mine = self.sanity[k]
            mine.values += s["values"]
            mine.bad += s["bad"]
            if mine.example is None and s["example"] is not None:
                mine.example = s["example"]
                mine.reason = s["reason"]
        self.blob_bytes += other["blob_bytes"]

    def as_dict(self) -> dict:
        return {
            "structs": {k: vars(v) for k, v in self.structs.items()},
            "untyped": {f"{k[0]}[{k[1]}]": v for k, v in self.untyped.items()},
            "unknown_kinds": dict(self.unknown_kinds),
            "sanity": {k: vars(v) for k, v in self.sanity.items()},
            "blob_bytes": self.blob_bytes,
        }


def check_zone(assets, set_fields: bool = True) -> ZoneCheck:
    """Walk every asset node of a parse."""
    out = ZoneCheck()
    gather: dict[str, list[bytes]] = defaultdict(list)
    world = next((_bounds(a.data) for a in assets if a.type == AssetType.GFX_MAP), None)
    for asset in assets:
        bounds = _bounds(asset.data)
        if asset.type in WORLD_TYPES:
            bounds = world
        _walk(asset.data, out, gather, bounds, set_fields, set())
    for name, chunks in gather.items():
        _check_array(STRUCTS[name], b"".join(chunks), out)
        del chunks
    return out


#: Every Struct in KINDS by name, for the gathered arrays.
STRUCTS: dict[str, Struct] = {}
for _kind in KINDS.values():
    for _spec in _kind.values():
        _s = _spec if isinstance(_spec, Struct) else getattr(_spec, "element", None)
        if isinstance(_s, Struct):
            STRUCTS[_s.name] = _s


def _bounds(node: Any) -> tuple | None:
    """(mins, maxs) of an asset whose header declares them (GfxWorld, XModel)."""
    if not isinstance(node, dict) or "header" not in node:
        return None
    spec = KINDS.get(node.get("_t"), {}).get("header")
    if not isinstance(spec, Struct) or "mins" not in spec.by_name or "maxs" not in spec.by_name:
        return None
    view = StructView(spec, node, "header")
    return tuple(view["mins"]), tuple(view["maxs"])


def _walk(node: Any, out: ZoneCheck, gather, bounds, set_fields: bool, seen: set) -> None:
    if isinstance(node, list):
        for v in node:
            if isinstance(v, dict | list):
                _walk(v, out, gather, bounds, set_fields, seen)
        return
    if not isinstance(node, dict) or id(node) in seen:
        return
    seen.add(id(node))
    kind = node.get("_t")
    schema = KINDS.get(kind) if kind else None
    if kind and schema is None:
        out.unknown_kinds[kind] += 1
    for key, value in node.items():
        if isinstance(value, bytes | bytearray):
            spec = resolve(schema.get(key), node) if schema else None
            if spec is None:
                out.untyped[(kind or "-", str(key))] += len(value)
            elif isinstance(spec, Struct):
                _check_struct(spec, node, key, out, set_fields, bounds)
            else:
                _check_spec_array(spec, value, out, gather, bounds, kind, key, node)
        elif isinstance(value, dict | list):
            _walk(value, out, gather, bounds, set_fields, seen)
        elif isinstance(value, DeferredData):
            out.blob_bytes += value.size
    if schema:
        _check_counts(schema, node, out)


def _check_struct(
    spec: Struct, node: dict, key: Any, out: ZoneCheck, set_fields: bool, bounds=None
) -> None:
    data = bytes(node[key])
    stats = out.structs[spec.name]
    stats.instances += 1
    stats.bytes += spec.size
    named, unknown = spec.coverage()
    stats.named += named
    stats.unknown += unknown
    view = StructView(spec, node, key)
    for f in spec.fields:
        value = f.decode(data)
        if set_fields and not f.read_only:
            view[f.name] = value
        elif f.encode(value) != data[f.offset : f.offset + f.size]:
            if f.type.startswith("char["):
                stats.char_tails += 1
            else:
                stats.round_trip_failures += 1
        _sanity_value(spec, f, value, out)
        if bounds is not None and f.type == "vec3" and f.name in POSITION_NAMES:
            stats_p = out.sanity[f"{spec.name}.{f.name}: inside the world / asset bounds"]
            stats_p.values += 1
            if any(v < lo - 1.0 or v > hi + 1.0 for v, lo, hi in zip(value, *bounds, strict=True)):
                stats_p.bad += 1
                if stats_p.example is None:
                    stats_p.example, stats_p.reason = list(value), f"outside {bounds}"
    if set_fields and bytes(node[key]) != data:
        stats.round_trip_failures += 1
        node[key] = data


def _check_spec_array(spec: ArrayOf, data, out, gather, bounds, kind, key, node) -> None:
    element = spec.element
    if isinstance(element, Struct):
        if len(data) % element.size:
            out.sanity[f"{kind}[{key}]: length"].bad += 1
            return
        STRUCTS.setdefault(element.name, element)
        gather[element.name].append(bytes(data))
        if bounds is not None:
            for name in POSITION_NAMES:
                f = element.by_name.get(name)
                if f is not None and f.type == "vec3":
                    _check_positions(element, name, data, bounds, out)
        return
    if element in ("u8", "text"):
        out.blob_bytes += len(data)
        return
    base, _ = parse_type(element)
    f = Field("v", 0, element)
    size = f.size
    if len(data) % size:
        out.sanity[f"{kind}[{key}] {element}: length"].bad += 1
        return
    arr = np.frombuffer(bytes(data), dtype=np.dtype(f.dtype()))
    if base in FLOAT_TYPES:
        _float_checks(f"{kind}[{key}] {element}", arr, out)
    if base in FLOAT_TYPES and arr.size:
        back = arr.astype(np.float64).astype(arr.dtype)
        if back.tobytes() != arr.tobytes():
            out.sanity[f"{kind}[{key}] {element}: float round trip"].bad += 1


def _check_array(spec: Struct, data: bytes, out: ZoneCheck) -> None:
    """Long arrays of one struct: coverage, vectorised round trip and sanity."""
    count = len(data) // spec.size
    if not count:
        return
    stats = out.structs[spec.name]
    stats.instances += count
    stats.bytes += count * spec.size
    named, unknown = spec.coverage()
    stats.named += named * count
    stats.unknown += unknown * count
    arr = np.frombuffer(data, dtype=spec.dtype(), count=count)
    for f in spec.fields:
        base, _ = parse_type(f.type)
        column = np.ascontiguousarray(arr[f.name])
        if base in FLOAT_TYPES:
            back = column.astype(np.float64).astype(column.dtype)
            if back.tobytes() != column.tobytes():
                stats.round_trip_failures += int(
                    np.count_nonzero(back.view(np.uint8) != column.view(np.uint8))
                )
            _float_checks(f"{spec.name}.{f.name}", column, out)
            if base == "vec3" and _is_unit_name(f.name):
                _unit_checks(f"{spec.name}.{f.name}", column.reshape(-1, 3), out)
        elif base == "cmp":
            _unit_checks(
                f"{spec.name}.{f.name} (CMP)",
                unpack_cmp_array(column).reshape(-1, 3),
                out,
                tolerance=0.02,
            )
        elif base == "char":
            raw = column.view(np.uint8).reshape(count, -1)
            first_nul = np.argmax(raw == 0, axis=1)
            has_nul = (raw == 0).any(axis=1)
            tail = np.arange(raw.shape[1])[None, :] > first_nul[:, None]
            dirty = (raw != 0) & tail & has_nul[:, None]
            stats.char_tails += int(np.count_nonzero(dirty.any(axis=1)))


def _float_checks(name: str, values: np.ndarray, out: ZoneCheck) -> None:
    flat = np.asarray(values, dtype=np.float64).ravel()
    if not flat.size:
        return
    finite = np.isfinite(flat)
    absval = np.abs(np.where(finite, flat, 0.0))
    bad = (
        ~finite
        | ((absval > FLOAT_LIMIT) & (absval != FLT_MAX))
        | ((absval < FLOAT_TINY) & (absval > 0))
    )
    stats = out.sanity[f"{name}: float plausible"]
    stats.values += flat.size
    n = int(np.count_nonzero(bad))
    stats.bad += n
    if n and stats.example is None:
        stats.example = (
            float(flat[np.argmax(bad)]) if np.isfinite(flat[np.argmax(bad)]) else "nan/inf"
        )
        stats.reason = "non-finite, |x| > 1e12 (not FLT_MAX), or denormal"


def _is_unit_name(name: str) -> bool:
    leaf = name.rsplit(".", 1)[-1]
    return leaf in ("normal", "dir")


def _unit_checks(name: str, vectors: np.ndarray, out: ZoneCheck, tolerance: float = 0.01) -> None:
    lengths = np.linalg.norm(vectors.astype(np.float64), axis=1)
    nonzero = lengths > 0
    bad = nonzero & (np.abs(lengths - 1.0) > tolerance)
    stats = out.sanity[f"{name}: unit length"]
    stats.values += int(np.count_nonzero(nonzero))
    n = int(np.count_nonzero(bad))
    stats.bad += n
    if n and stats.example is None:
        stats.example = float(lengths[np.argmax(bad)])
        stats.reason = f"length differs from 1 by more than {tolerance}"


def _check_positions(element: Struct, name: str, data, bounds, out: ZoneCheck) -> None:
    arr = np.frombuffer(bytes(data), dtype=element.dtype())
    xyz = arr[name].astype(np.float64)
    lo = np.array(bounds[0]) - 1.0
    hi = np.array(bounds[1]) + 1.0
    bad = ((xyz < lo) | (xyz > hi)).any(axis=1)
    stats = out.sanity[f"{element.name}.{name}: inside the world / asset bounds"]
    stats.values += len(xyz)
    n = int(np.count_nonzero(bad))
    stats.bad += n
    if n and stats.example is None:
        stats.example = [float(v) for v in xyz[np.argmax(bad)]]
        stats.reason = f"outside mins {bounds[0]} / maxs {bounds[1]}"


def _sanity_value(spec: Struct, f: Field, value: Any, out: ZoneCheck) -> None:
    base, _ = parse_type(f.type)
    if base == "cmp":
        words = value if isinstance(value, list) else [value]
        lengths = np.linalg.norm(unpack_cmp_array(np.array(words, dtype=np.uint32)), axis=-1)
        stats = out.sanity[f"{spec.name}.{f.name} (CMP): unit length"]
        for length in lengths:
            if length > 0:
                stats.values += 1
                if abs(length - 1.0) > 0.02:
                    stats.bad += 1
                    if stats.example is None:
                        stats.example, stats.reason = (
                            float(length),
                            "length differs from 1 by > 0.02",
                        )
        return
    if base not in FLOAT_TYPES:
        if f.names is not None:
            stats = out.sanity[f"{spec.name}.{f.name}: enum range"]
            stats.values += 1
            if value not in f.names:
                stats.bad += 1
                if stats.example is None:
                    stats.example, stats.reason = value, "not a known value"
        return
    flat = _flatten(value)
    stats = out.sanity[f"{spec.name}.{f.name}: float plausible"]
    stats.values += len(flat)
    for x in flat:
        if not math.isfinite(x) or FLT_MAX != abs(x) > FLOAT_LIMIT or 0 < abs(x) < FLOAT_TINY:
            stats.bad += 1
            if stats.example is None:
                stats.example = x if math.isfinite(x) else repr(x)
                stats.reason = "non-finite, |x| > 1e12 (not FLT_MAX), or denormal"
    if base == "vec3" and _is_unit_name(f.name):
        length = math.sqrt(sum(x * x for x in flat[:3]))
        if length > 0:
            ustats = out.sanity[f"{spec.name}.{f.name}: unit length"]
            ustats.values += 1
            if abs(length - 1.0) > 0.01:
                ustats.bad += 1
                if ustats.example is None:
                    ustats.example, ustats.reason = length, "length differs from 1 by > 0.01"


def _flatten(value: Any) -> list[float]:
    if isinstance(value, list | tuple):
        out: list[float] = []
        for v in value:
            out.extend(_flatten(v))
        return out
    return [float(value)]


def _check_counts(schema: dict, node: dict, out: ZoneCheck) -> None:
    """Each count field equals the length of the array it sizes (or that length
    minus one, for the arrays that hold count + 1), where the relation is one to
    one (a key sized by several counts is skipped)."""
    for key in ("header", "raw", "head"):
        spec = schema.get(key)
        data = node.get(key)
        if not isinstance(spec, Struct) or not isinstance(data, bytes | bytearray):
            continue
        targets: dict[str, list[Field]] = defaultdict(list)
        for f in spec.fields:
            if f.role == "count" and f.counts:
                targets[f.counts].append(f)
        for target, fields in targets.items():
            if len(fields) != 1 or target not in node:
                continue
            f = fields[0]
            count = f.decode(data)
            value = node[target]
            if value is None:
                continue
            target_spec = schema.get(target)
            if isinstance(value, list):
                length = len(value)
            elif isinstance(value, bytes | bytearray) and isinstance(target_spec, ArrayOf):
                length = len(value) // target_spec.element_size
            elif isinstance(value, DeferredData):
                length = value.size
            else:
                continue
            stats = out.sanity[f"{spec.name}.{f.name}: count = len({target})"]
            stats.values += 1
            # Some arrays hold count + 1 elements (rawfile len, FX interval counts).
            if length not in (count, count + 1):
                stats.bad += 1
                if stats.example is None:
                    stats.example, stats.reason = [count, length], "count, length"
