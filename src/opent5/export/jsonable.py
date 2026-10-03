"""Turning parsed nodes into JSON, and safe file names."""

from __future__ import annotations

import dataclasses
import json
import math
import re
from pathlib import Path
from typing import Any

from opent5.xfile.constants import type_name
from opent5.xfile.stream import AssetLink, DeferredData

#: Byte strings up to this length are written inline as hex; longer ones go to a .bin file.
INLINE_BYTES = 4096

_UNSAFE = re.compile(r"[^A-Za-z0-9._+\-,()=#@&$!]")


def safe_name(name: str) -> str:
    """A file-system and OBJ-safe version of an asset name (path separators, spaces,
    colons, * and ~ replaced). Distinct names may collide; see NameTable."""
    out = _UNSAFE.sub("_", name).strip(".") or "_"
    return out[:180]


class NameTable:
    """Unique safe names per folder: a second asset whose safe name collides gets a suffix."""

    def __init__(self):
        self.used: dict[str, dict[str, str]] = {}
        self.taken: dict[str, set[str]] = {}

    def get(self, folder: str, name: str) -> str:
        names = self.used.setdefault(folder, {})
        if name in names:
            return names[name]
        taken = self.taken.setdefault(folder, set())
        base = safe_name(name)
        candidate, k = base, 1
        while candidate.lower() in taken:
            k += 1
            candidate = f"{base}~{k}"
        names[name] = candidate
        taken.add(candidate.lower())
        return candidate


def hexs(b: bytes | memoryview | None) -> str | None:
    return None if b is None else bytes(b).hex()


def clean_float(v: float) -> float | None:
    return v if math.isfinite(v) else None


class Jsonifier:
    """Generic node -> JSON value. Nested assets become references, large byte
    strings are written beside the JSON file, alias links are resolved by name."""

    def __init__(self, resolver=None, classify=None):
        self.resolver = resolver
        self.classify = classify

    def convert(self, obj: Any, blob_dir: Path | None = None, path: str = "", top: bool = True):
        if obj is None or isinstance(obj, bool | int | str):
            return obj
        if isinstance(obj, float):
            return clean_float(obj)
        if isinstance(obj, bytes | bytearray | memoryview):
            b = bytes(obj)
            if len(b) <= INLINE_BYTES or blob_dir is None:
                return {"hex": b.hex()} if len(b) > 64 else b.hex()
            blob_dir.mkdir(parents=True, exist_ok=True)
            file = blob_dir / (safe_name(path or "blob") + ".bin")
            file.write_bytes(b)
            return {"bytes": len(b), "file": f"{blob_dir.name}/{file.name}"}
        if isinstance(obj, DeferredData):
            return {"deferred_bytes": obj.size, "tail_offset": obj.file_offset}
        if isinstance(obj, AssetLink):
            name = self.resolver.name(obj) if self.resolver else obj.name
            return {"ref": type_name(obj.asset_type), "name": name}
        if not top and self.classify is not None:
            t = self.classify(obj)
            if t is not None:
                name = obj.get("name") if isinstance(obj, dict) else None
                return {"asset": type_name(t), "name": name}
        if isinstance(obj, dict):
            return {
                str(k): self.convert(v, blob_dir, f"{path}.{k}" if path else str(k), False)
                for k, v in obj.items()
            }
        if isinstance(obj, list | tuple):
            return [self.convert(v, blob_dir, f"{path}.{i}", False) for i, v in enumerate(obj)]
        if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
            return {
                f.name: self.convert(getattr(obj, f.name), blob_dir, f"{path}.{f.name}", False)
                for f in dataclasses.fields(obj)
            }
        return repr(obj)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=1, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8"
    )
