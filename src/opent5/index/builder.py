"""Turn zones into index records, once per zone, in parallel (docs/search-index.md).

``index_zone`` opens and parses one zone and pulls out its assets and its searchable
text. It never raises: a zone that will not open or parse comes back as a ``ZoneResult``
with ``error`` set and empty lists, so one bad zone cannot stop the build.

``build`` is the orchestrator: it reuses the cache for unchanged zones, re-indexes the
rest in a process pool, prunes zones that left the dump, and reports progress.
"""

from __future__ import annotations

import os
import sys
import time
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from opent5.index.model import AssetRecord, TextRecord, ZoneResult
from opent5.index.store import Store

# Built once per worker process (REGISTRY is read-only): node "_t" kind -> asset type,
# the same mapping opent5.edit.document uses to find assets loaded inside others.
_KIND_TYPES: dict[str, int] | None = None


def _kind_types() -> dict[str, int]:
    global _KIND_TYPES
    if _KIND_TYPES is None:
        from opent5.xfile import REGISTRY

        out: dict[str, int] = {}
        for t, h in sorted(REGISTRY.items()):
            if h.kind and h.kind not in out:
                out[h.kind] = t
        _KIND_TYPES = out
    return _KIND_TYPES


def index_zone(path: str | Path) -> ZoneResult:
    """Open, parse and harvest one zone. Returns a ``ZoneResult``; records any failure in
    ``error`` rather than raising."""
    path = Path(path)
    started = time.perf_counter()
    try:
        stat = path.stat()
        mtime, size = stat.st_mtime, stat.st_size
    except OSError as exc:
        return ZoneResult(str(path), 0.0, 0, path.stem, error=f"stat failed: {exc}")
    result = ZoneResult(str(path), mtime, size, path.stem)
    try:
        from opent5.container.zone import Zone
        from opent5.xfile import parse

        zone = Zone.open(path)
        result.zone_name = zone.name
        xfile = parse(bytes(zone.content), log=False)
    except Exception as exc:  # noqa: BLE001 - one bad zone must not stop the build
        result.error = f"{type(exc).__name__}: {exc}"
        result.seconds = round(time.perf_counter() - started, 3)
        return result
    try:
        _harvest(xfile, result)
    except Exception as exc:  # noqa: BLE001 - partial harvest is better than none
        result.error = f"harvest failed: {type(exc).__name__}: {exc}"
    result.seconds = round(time.perf_counter() - started, 3)
    return result


def _harvest(xfile, result: ZoneResult) -> None:
    from opent5.edit import content as ct
    from opent5.export.nodes import walk
    from opent5.xfile.constants import AssetType as T
    from opent5.xfile.constants import type_name

    kinds = _kind_types()
    seen_inline: set[tuple[int, str]] = set()
    for a in xfile.assets:
        result.assets.append(
            AssetRecord(
                index=a.index,
                inline_key=None,
                type=a.type,
                type_name=a.type_name,
                name=a.name,
                offset=a.file_start,
                size=a.size,
            )
        )
        if not isinstance(a.data, dict):
            continue
        _harvest_text(ct, T, a, result)
        for node in walk(a.data):
            if node is a.data or not isinstance(node, dict) or "header" not in node:
                continue
            t = kinds.get(node.get("_t"))
            name = node.get("name")
            if t is None or name is None or (t, name) in seen_inline:
                continue
            seen_inline.add((t, name))
            result.assets.append(
                AssetRecord(
                    index=None,
                    inline_key=f"inline:{t}:{name}",
                    type=t,
                    type_name=type_name(t),
                    name=name,
                    offset=0,
                    size=0,
                    parent=a.index,
                )
            )


def _harvest_text(ct, T, a, result: ZoneResult) -> None:
    """Pull one asset's searchable text, guarded so a decode slip skips the asset only."""
    try:
        if a.type == T.RAWFILE:
            text = ct.rawfile_text(a.data)
            if text:
                result.texts.append(_text(a, "rawfile", None, text))
        elif a.type == T.STRINGTABLE:
            rows = ct.table_rows(a.data)
            columns = len(rows[0]) if rows else 0
            flat = "\n".join(cell for row in rows for cell in row)
            if flat:
                result.texts.append(_text(a, "cell", columns, flat))
        elif a.type == T.LOCALIZE:
            key, value = ct.localize_state(a.data)
            if value:
                result.texts.append(_text(a, "localize", None, value, name=key or a.name))
        elif a.type == T.MAP_ENTS:
            text = ct.mapents_text(a.data)
            if text:
                result.texts.append(_text(a, "entities", None, text))
        elif a.type in (T.COL_MAP_MP, T.COL_MAP_SP):
            ents = a.data.get("map_ents")
            if isinstance(ents, dict):
                text = ct.mapents_text(ents)
                if text:
                    result.texts.append(_text(a, "entities", None, text))
    except Exception:  # noqa: BLE001 - a text block that will not decode is skipped
        return


def _text(a, kind: str, ncols: int | None, text: str, name: str | None = None) -> TextRecord:
    return TextRecord(
        ref=str(a.index),
        type=a.type,
        type_name=a.type_name,
        name=name if name is not None else a.name,
        kind=kind,
        ncols=ncols,
        text=text,
    )


def default_jobs() -> int:
    env = os.environ.get("OPENT5_INDEX_JOBS")
    if env and env.isdigit() and int(env) > 0:
        return int(env)
    # A PyInstaller exe has no separate Python to spawn as a pool worker (the worker would
    # re-launch the exe), so index serially when frozen.
    if getattr(sys, "frozen", False):
        return 1
    return max(1, min((os.cpu_count() or 2), 6))


def build(
    zones: list[Path],
    store: Store,
    rebuild: bool = False,
    jobs: int | None = None,
    progress: Callable[[int, int, str, str], None] | None = None,
) -> dict:
    """Bring ``store`` up to date for ``zones`` (typically ``env.all_zones()``).

    Unchanged zones are reused from the cache; changed, new or (with ``rebuild``) all
    zones are parsed again, in a process pool. Zones that left the dump are pruned.
    ``progress(done, total, path, status)`` is called once per zone, status one of
    "reused", "indexed", "failed".
    """
    paths = [Path(p) for p in zones]
    keep = {str(p) for p in paths}
    pruned = store.prune(keep)

    to_parse: list[Path] = []
    reused = 0
    for p in paths:
        try:
            stat = p.stat()
        except OSError:
            to_parse.append(p)
            continue
        if not rebuild and store.fresh(str(p), stat.st_mtime, stat.st_size):
            reused += 1
        else:
            to_parse.append(p)

    total = len(paths)
    done = reused  # reused zones are counted in the summary, not reported one by one
    indexed = failed = 0
    workers = jobs if jobs is not None else default_jobs()
    if to_parse:
        if workers <= 1 or len(to_parse) == 1:
            results = (index_zone(p) for p in to_parse)
            for result in results:
                done += 1
                failed += _store_result(store, result)
                indexed += 1
                _report(progress, done, total, result)
        else:
            try:
                with ProcessPoolExecutor(max_workers=workers) as pool:
                    for result in pool.map(index_zone, [str(p) for p in to_parse]):
                        done += 1
                        failed += _store_result(store, result)
                        indexed += 1
                        _report(progress, done, total, result)
            except Exception:  # a pool that cannot start (e.g. a frozen build) falls back to serial
                workers = 1
                for p in to_parse[indexed:]:
                    result = index_zone(str(p))
                    done += 1
                    failed += _store_result(store, result)
                    indexed += 1
                    _report(progress, done, total, result)

    return {
        "zones": total,
        "reused": reused,
        "indexed": indexed,
        "failed": failed,
        "pruned": pruned,
        "jobs": workers,
    }


def _store_result(store: Store, result: ZoneResult) -> int:
    store.put(result)
    return 1 if result.error else 0


def _report(progress, done: int, total: int, result: ZoneResult) -> None:
    if progress is not None:
        progress(done, total, result.path, "failed" if result.error else "indexed")
