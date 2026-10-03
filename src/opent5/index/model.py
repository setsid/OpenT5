"""Value types for the cross-zone search index (docs/search-index.md).

An index record is either an asset (what a zone holds: type, name, file offset,
size) or a block of searchable text (a rawfile's source, a stringtable's cells,
a localize value, an entity string). A search returns ``Hit``s inside a
``SearchResult``.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class AssetRecord:
    """One asset of a zone: a top-level asset (``index`` set) or one loaded inside
    another (``inline_key`` set, ``parent`` is the top-level index that loads it)."""

    index: int | None
    inline_key: str | None
    type: int
    type_name: str
    name: str | None
    offset: int
    size: int
    parent: int | None = None

    @property
    def ref(self) -> str:
        """A stable string for the asset, for display and as a texts-table key."""
        return self.inline_key if self.inline_key is not None else str(self.index)


@dataclass(frozen=True)
class TextRecord:
    """A block of searchable text for one asset. ``kind`` is the match kind the text
    yields: "rawfile", "cell", "localize" or "entities". ``ncols`` is the stringtable
    column count (so a line number maps back to a row and column), else None."""

    ref: str
    type: int
    type_name: str
    name: str | None
    kind: str
    ncols: int | None
    text: str


@dataclass
class ZoneResult:
    """What indexing one zone produced. ``error`` set (and the lists empty) when the
    zone could not be opened or parsed; the build records it and carries on."""

    path: str
    mtime: float
    size: int
    zone_name: str
    assets: list[AssetRecord] = field(default_factory=list)
    texts: list[TextRecord] = field(default_factory=list)
    error: str | None = None
    seconds: float = 0.0


@dataclass(frozen=True)
class Hit:
    """One search match."""

    zone: str
    zone_path: str
    type: int
    type_name: str
    asset_name: str | None
    asset_ref: str
    #: "name", "rawfile", "cell", "localize" or "entities".
    kind: str
    #: A short description of where it matched ("name", "line 42", "row 3, column 1").
    where: str
    snippet: str
    line: int | None = None
    row: int | None = None
    column: int | None = None
    score: float = 0.0


@dataclass
class SearchResult:
    term: str
    hits: list[Hit]
    #: Matches found in all (before the cap); ``len(hits)`` may be smaller.
    total: int
    truncated: bool
    zones_searched: int
    seconds: float
    kinds: tuple[str, ...]
