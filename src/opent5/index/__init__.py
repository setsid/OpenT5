"""A search index over every configured zone (docs/search-index.md).

The whole dump is parsed once into a cache on disk, so the second search is instant.
A search answers the questions modders keep asking: which zone has this asset, this
string, this script function.

    from opent5 import env
    from opent5.index import Index, build, Store

    with Store.open() as store:                 # the on-disk cache
        build(env.all_zones(), store)           # parse changed zones, reuse the rest
        result = Index(store).search("Nuketown")
        for hit in result.hits:
            print(hit.zone, hit.type_name, hit.asset_name, hit.where)

``build`` reuses the cache for unchanged zones (keyed by path, mtime and size), so it
is instant once warm. ``Index.search`` ranks and caps the hits. The command-line layer
(``register`` and ``COMMANDS``) is wired into the main parser by the CLI.
"""

from __future__ import annotations

from pathlib import Path

from opent5.index.builder import build, index_zone
from opent5.index.model import AssetRecord, Hit, SearchResult, TextRecord, ZoneResult
from opent5.index.query import ALL_KINDS, DEFAULT_LIMIT, TEXT_KINDS, Index
from opent5.index.store import Store, default_cache_dir

__all__ = [
    "ALL_KINDS",
    "DEFAULT_LIMIT",
    "TEXT_KINDS",
    "AssetRecord",
    "Hit",
    "Index",
    "SearchResult",
    "Store",
    "TextRecord",
    "ZoneResult",
    "build",
    "default_cache_dir",
    "index_zone",
    "search",
]


def search(
    term: str,
    zones: list[Path] | None = None,
    cache_dir: str | Path | None = None,
    ensure: bool = True,
    rebuild: bool = False,
    progress=None,
    **options,
) -> SearchResult:
    """Open the cache, optionally bring it up to date, and search.

    ``zones`` defaults to ``env.all_zones()``. With ``ensure`` the index is updated first
    (changed zones re-parsed, the rest reused); ``rebuild`` re-indexes everything.
    ``options`` are passed through to ``Index.search`` (kinds, regex, case, names_only,
    type_name, zone, limit).
    """
    if zones is None:
        from opent5 import env

        zones = env.all_zones()
    with Store.open(cache_dir) as store:
        if ensure or rebuild:
            build(zones, store, rebuild=rebuild, progress=progress)
        return Index(store).search(term, **options)
