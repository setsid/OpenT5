"""The value types of the editing API (docs/edit-api.md)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

#: An asset key: the index in the zone's asset list, or ("inline", type, name) for an
#: asset loaded inside another one (an image inside a material, the entity string inside
#: the clipMap). Every reader and editor takes either.
AssetKey = int | tuple


class EditError(Exception):
    """An edit or save that cannot be done. The message says what was expected, what was
    found, and the asset index and offset where they apply."""


@dataclass(frozen=True)
class AssetRef:
    index: AssetKey
    type: int
    type_name: str
    name: str | None
    #: File bytes of the asset's own span (header through its last read).
    size: int
    #: Which editors apply: "text", "table", "localize", "image", "entities", "geometry",
    #: "hex" (and "fields" for every asset with a schema).
    editable: tuple[str, ...]
    #: Additions to the contract: content offset of the span, and for an inline asset the
    #: index of the top-level asset that loads it.
    file_start: int | None = None
    parent: int | None = None

    @property
    def inline(self) -> bool:
        return isinstance(self.index, tuple)

    @property
    def key(self) -> AssetKey:
        return self.index


@dataclass(frozen=True)
class SearchHit:
    ref: AssetRef
    #: "name", "text", "cell" or "value"
    kind: str
    snippet: str
    line: int | None = None
    row: int | None = None
    column: int | None = None

    @property
    def index(self) -> AssetKey:
        return self.ref.index


@dataclass
class ImageData:
    #: name, format, format_code, width, height, mips, kind ("2d" / "cube" / "volume"),
    #: pixels ("inline" / "deferred" / "pak" / "elsewhere"), source, semantic,
    #: replaceable, and why not when it is not.
    info: dict[str, Any]
    rgba: np.ndarray | None
    reason: str | None = None


@dataclass
class Change:
    index: AssetKey
    #: "text", "cell", "row_added", "row_removed", "localize", "image", "field"
    kind: str
    before: Any
    after: Any
    #: Where in the asset (cell "row 3, column 1", field path) and side effects (strings
    #: other assets shared and now hold inline).
    detail: str = ""

    @property
    def key(self) -> AssetKey:
        return self.index


@dataclass
class SaveReport:
    path: Path
    bytes: int
    assets_checked: int
    assets_changed: int
    verified: bool
    problems: list[str]
    signature_note: str | None
    #: Additions: sha1 of the written file, whether it is byte-identical to the source,
    #: and what the verification found asset by asset (counts).
    sha1: str = ""
    identical: bool = False
    details: dict[str, Any] = field(default_factory=dict)


SIGNATURE_NOTE = (
    "The console signature at 0x3c is the original one and no longer matches the "
    "content: this file loads only on a client with the signature check patched out."
)
SIGNATURE_INTACT = (
    "The file is byte-identical to the source, so its console signature still matches."
)
