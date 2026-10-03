"""Searching the built index (docs/search-index.md).

``Index`` wraps a ``Store`` and answers queries. A names-only search reads the assets
table (indexed, so it is quick even cold); a full-text search scans the stored text,
which is small next to the zones because only rawfiles, stringtables, localize values
and entity strings are kept. Non-regex, case-insensitive searches pre-filter in SQL
with LIKE so only the blocks that could match are scanned.
"""

from __future__ import annotations

import fnmatch
import re
import time
from pathlib import Path

from opent5.index.model import Hit, SearchResult
from opent5.index.store import Store

#: Full-text match kinds (everything that is not an asset-name match).
TEXT_KINDS = ("rawfile", "cell", "localize", "entities")
ALL_KINDS = ("name", *TEXT_KINDS)
DEFAULT_LIMIT = 200


class Index:
    """A query handle over a ``Store``. ``Index.open(cache_dir)`` opens the cache."""

    def __init__(self, store: Store, own: bool = False):
        self.store = store
        self._own = own
        self._zone_names = store.zone_names()

    @classmethod
    def open(cls, cache_dir=None) -> Index:
        return cls(Store.open(cache_dir), own=True)

    def close(self) -> None:
        if self._own:
            self.store.close()

    def __enter__(self) -> Index:
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def search(
        self,
        term: str,
        kinds: tuple[str, ...] | None = None,
        regex: bool = False,
        case: bool = False,
        names_only: bool = False,
        type_name: str | None = None,
        zone: str | None = None,
        limit: int | None = DEFAULT_LIMIT,
    ) -> SearchResult:
        """Search every indexed zone.

        ``kinds`` restricts which match kinds count (default all). ``names_only`` is the
        fast path over asset names alone. ``type_name`` keeps one asset type, ``zone`` a
        glob over zone names. ``regex`` treats ``term`` as a regular expression; ``case``
        makes it case-sensitive. Results are ranked and capped at ``limit`` (None for no
        cap); ``total`` is the count before the cap.
        """
        started = time.perf_counter()
        wanted = self._kinds(kinds, names_only)
        matcher = _Matcher(term, regex, case)
        zone_match = (lambda name: fnmatch.fnmatch(name, zone)) if zone else (lambda _n: True)

        hits: list[Hit] = []
        if "name" in wanted:
            hits += self._name_hits(matcher, type_name, zone_match)
        text_kinds = tuple(k for k in wanted if k != "name")
        if text_kinds:
            hits += self._text_hits(matcher, text_kinds, type_name, zone_match)

        hits.sort(key=lambda h: (-h.score, h.zone, h.type_name, h.asset_name or "", h.line or 0))
        total = len(hits)
        truncated = limit is not None and total > limit
        if limit is not None:
            hits = hits[:limit]
        return SearchResult(
            term=term,
            hits=hits,
            total=total,
            truncated=truncated,
            zones_searched=sum(1 for n in self._zone_names.values() if zone_match(n)),
            seconds=round(time.perf_counter() - started, 4),
            kinds=wanted,
        )

    # -- internals -------------------------------------------------------------------------

    @staticmethod
    def _kinds(kinds, names_only: bool) -> tuple[str, ...]:
        if names_only:
            return ("name",)
        if not kinds:
            return ALL_KINDS
        bad = [k for k in kinds if k not in ALL_KINDS]
        if bad:
            raise ValueError(f"kind {bad[0]!r}: expected one of {', '.join(ALL_KINDS)}")
        return tuple(kinds)

    def _zone_name(self, path: str) -> str:
        return self._zone_names.get(path, Path(path).stem)

    def _name_hits(self, matcher: _Matcher, type_name, zone_match) -> list[Hit]:
        out: list[Hit] = []
        for path, rec in self.store.iter_assets(type_name, matcher.like_term()):
            zone = self._zone_name(path)
            if not zone_match(zone) or rec.name is None:
                continue
            if matcher.matches(rec.name):
                out.append(
                    Hit(
                        zone=zone,
                        zone_path=path,
                        type=rec.type,
                        type_name=rec.type_name,
                        asset_name=rec.name,
                        asset_ref=rec.ref,
                        kind="name",
                        where="name",
                        snippet=rec.name,
                        score=matcher.name_score(rec.name),
                    )
                )
        return out

    def _text_hits(self, matcher: _Matcher, kinds, type_name, zone_match) -> list[Hit]:
        out: list[Hit] = []
        like = matcher.like_term()
        for path, rec in self.store.iter_texts(kinds, type_name, like):
            zone = self._zone_name(path)
            if not zone_match(zone):
                continue
            out += _text_hits_in(matcher, zone, path, rec)
        return out


def _text_hits_in(matcher: _Matcher, zone: str, path: str, rec) -> list[Hit]:
    out: list[Hit] = []
    if rec.kind == "localize":
        if matcher.matches(rec.text):
            out.append(_hit(zone, path, rec, "localize", "value", rec.text[:200]))
        return out
    lines = rec.text.split("\n")
    if rec.kind == "cell":
        ncols = rec.ncols or 1
        for i, cell in enumerate(lines):
            if matcher.matches(cell):
                row, col = divmod(i, ncols)
                out.append(
                    _hit(
                        zone,
                        path,
                        rec,
                        "cell",
                        f"row {row}, column {col}",
                        cell[:200],
                        row=row,
                        column=col,
                    )
                )
        return out
    # rawfile and entities: report the matching source line and its number
    for n, line in enumerate(lines, 1):
        if matcher.matches(line):
            out.append(_hit(zone, path, rec, rec.kind, f"line {n}", line.strip()[:200], line=n))
    return out


def _hit(zone, path, rec, kind, where, snippet, line=None, row=None, column=None) -> Hit:
    return Hit(
        zone=zone,
        zone_path=path,
        type=rec.type,
        type_name=rec.type_name,
        asset_name=rec.name,
        asset_ref=rec.ref,
        kind=kind,
        where=where,
        snippet=snippet,
        line=line,
        row=row,
        column=column,
        score=_TEXT_SCORE[kind],
    )


#: Text matches rank below a name match; a localize value above a buried script line.
_TEXT_SCORE = {"localize": 3.0, "cell": 2.5, "entities": 2.0, "rawfile": 1.5}


class _Matcher:
    """Plain-substring or regular-expression matching, case folding applied once."""

    def __init__(self, term: str, regex: bool, case: bool):
        self.term = term
        self.regex = regex
        self.case = case
        if regex:
            self._re = re.compile(term, 0 if case else re.IGNORECASE)
        else:
            self._needle = term if case else term.lower()

    def matches(self, text: str) -> bool:
        if self.regex:
            return self._re.search(text) is not None
        return (self._needle in text) if self.case else (self._needle in text.lower())

    def like_term(self) -> str | None:
        """The literal to pre-filter with in SQL, or None (regex, or case-sensitive, so
        SQL's ASCII-only case folding would be wrong)."""
        if self.regex or self.case:
            return None
        return self.term

    def name_score(self, name: str) -> float:
        other = name if self.case else name.lower()
        if self.regex:
            m = self._re.fullmatch(name)
            return 10.0 if m else (6.0 if self._re.match(name) else 4.0)
        needle = self._needle
        if other == needle:
            return 10.0
        if other.startswith(needle):
            return 6.0
        return 4.0
