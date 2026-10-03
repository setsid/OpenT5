"""The on-disk cache, one SQLite database (docs/search-index.md).

SQLite from the standard library is the format: a single file, no dependency, one
writer at a time, and indexes that make a names lookup instant without loading the
text. A zone's rows are keyed by its absolute path and reused while the file's mtime
and size are unchanged; a changed or new zone is re-indexed, a vanished one pruned.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from opent5.index.model import AssetRecord, TextRecord, ZoneResult

#: Bumped when the schema or what is stored changes, so an old cache is rebuilt.
SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS zones (
    path TEXT PRIMARY KEY, mtime REAL, size INTEGER, zone_name TEXT,
    asset_count INTEGER, inline_count INTEGER, text_count INTEGER,
    error TEXT, seconds REAL
);
CREATE TABLE IF NOT EXISTS assets (
    zone_path TEXT, idx INTEGER, inline_key TEXT, type INTEGER, type_name TEXT,
    name TEXT, offset INTEGER, size INTEGER, parent INTEGER
);
CREATE TABLE IF NOT EXISTS texts (
    zone_path TEXT, ref TEXT, type INTEGER, type_name TEXT, name TEXT,
    kind TEXT, ncols INTEGER, text TEXT
);
CREATE INDEX IF NOT EXISTS assets_name ON assets (name COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS assets_zone ON assets (zone_path);
CREATE INDEX IF NOT EXISTS texts_zone ON texts (zone_path);
CREATE INDEX IF NOT EXISTS texts_kind ON texts (kind);
"""


def default_cache_dir() -> Path:
    """Where the cache lives by default: ``$OPENT5_CACHE_DIR`` if set, else the per-user
    cache directory (``$XDG_CACHE_HOME`` or ``~/.cache``) under ``opent5``. Never the
    repository and never a game folder."""
    explicit = os.environ.get("OPENT5_CACHE_DIR")
    if explicit:
        return Path(explicit).expanduser()
    base = os.environ.get("XDG_CACHE_HOME")
    root = Path(base).expanduser() if base else Path.home() / ".cache"
    return root / "opent5"


class Store:
    """The index database. Open it with ``Store.open`` and close it, or use it as a
    context manager."""

    def __init__(self, conn: sqlite3.Connection, path: Path):
        self.conn = conn
        self.path = path

    @classmethod
    def open(cls, cache_dir: str | Path | None = None) -> Store:
        directory = Path(cache_dir) if cache_dir is not None else default_cache_dir()
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "index.sqlite3"
        conn = sqlite3.connect(str(path))
        conn.executescript(_SCHEMA)
        version = conn.execute("SELECT value FROM meta WHERE key='schema'").fetchone()
        if version is None:
            conn.execute(
                "INSERT INTO meta (key, value) VALUES ('schema', ?)", (str(SCHEMA_VERSION),)
            )
            conn.commit()
        elif version[0] != str(SCHEMA_VERSION):
            cls._wipe(conn)
        return cls(conn, path)

    @staticmethod
    def _wipe(conn: sqlite3.Connection) -> None:
        for table in ("zones", "assets", "texts"):
            conn.execute(f"DELETE FROM {table}")
        conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES ('schema', ?)", (str(SCHEMA_VERSION),)
        )
        conn.commit()

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def close(self) -> None:
        self.conn.close()

    # -- freshness -------------------------------------------------------------------------

    def fresh(self, path: str, mtime: float, size: int) -> bool:
        """True when the zone at ``path`` is already indexed at this mtime and size."""
        row = self.conn.execute("SELECT mtime, size FROM zones WHERE path = ?", (path,)).fetchone()
        return row is not None and row[0] == mtime and row[1] == size

    def indexed_paths(self) -> set[str]:
        return {r[0] for r in self.conn.execute("SELECT path FROM zones")}

    def prune(self, keep: set[str]) -> int:
        """Drop every zone not in ``keep`` (removed from the dump). Returns how many."""
        gone = [p for p in self.indexed_paths() if p not in keep]
        for path in gone:
            self._delete(path)
        if gone:
            self.conn.commit()
        return len(gone)

    def _delete(self, path: str) -> None:
        self.conn.execute("DELETE FROM zones WHERE path = ?", (path,))
        self.conn.execute("DELETE FROM assets WHERE zone_path = ?", (path,))
        self.conn.execute("DELETE FROM texts WHERE zone_path = ?", (path,))

    # -- writing ---------------------------------------------------------------------------

    def put(self, result: ZoneResult) -> None:
        """Replace everything stored for one zone with a fresh result."""
        self._delete(result.path)
        self.conn.execute(
            "INSERT INTO zones (path, mtime, size, zone_name, asset_count, inline_count, "
            "text_count, error, seconds) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                result.path,
                result.mtime,
                result.size,
                result.zone_name,
                sum(1 for a in result.assets if a.inline_key is None),
                sum(1 for a in result.assets if a.inline_key is not None),
                len(result.texts),
                result.error,
                result.seconds,
            ),
        )
        self.conn.executemany(
            "INSERT INTO assets (zone_path, idx, inline_key, type, type_name, name, offset, "
            "size, parent) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    result.path,
                    a.index,
                    a.inline_key,
                    a.type,
                    a.type_name,
                    a.name,
                    a.offset,
                    a.size,
                    a.parent,
                )
                for a in result.assets
            ],
        )
        self.conn.executemany(
            "INSERT INTO texts (zone_path, ref, type, type_name, name, kind, ncols, text) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (result.path, t.ref, t.type, t.type_name, t.name, t.kind, t.ncols, t.text)
                for t in result.texts
            ],
        )
        self.conn.commit()

    # -- reading ---------------------------------------------------------------------------

    def zone_names(self) -> dict[str, str]:
        return {r[0]: r[1] for r in self.conn.execute("SELECT path, zone_name FROM zones")}

    def errors(self) -> list[tuple[str, str]]:
        return [
            (r[0], r[1])
            for r in self.conn.execute(
                "SELECT path, error FROM zones WHERE error IS NOT NULL ORDER BY path"
            )
        ]

    def stats(self) -> dict:
        row = self.conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(asset_count), 0), COALESCE(SUM(inline_count), 0), "
            "COALESCE(SUM(text_count), 0), SUM(error IS NOT NULL) FROM zones"
        ).fetchone()
        text_bytes = self.conn.execute(
            "SELECT COALESCE(SUM(LENGTH(text)), 0) FROM texts"
        ).fetchone()[0]
        return {
            "zones": row[0],
            "assets": row[1],
            "inline_assets": row[2],
            "text_blocks": row[3],
            "failed_zones": row[4] or 0,
            "searchable_text_bytes": text_bytes,
            "cache_path": str(self.path),
            "cache_bytes": self.path.stat().st_size if self.path.exists() else 0,
        }

    def iter_assets(self, type_name: str | None = None, like: str | None = None):
        sql = (
            "SELECT zone_path, idx, inline_key, type, type_name, name, offset, size, parent "
            "FROM assets WHERE name IS NOT NULL"
        )
        params: list = []
        if type_name:
            sql += " AND type_name = ?"
            params.append(type_name)
        if like is not None:
            sql += " AND name LIKE ? ESCAPE '\\'"
            params.append(f"%{_escape_like(like)}%")
        for r in self.conn.execute(sql, params):
            yield (
                r[0],
                AssetRecord(
                    index=r[1],
                    inline_key=r[2],
                    type=r[3],
                    type_name=r[4],
                    name=r[5],
                    offset=r[6],
                    size=r[7],
                    parent=r[8],
                ),
            )

    def iter_texts(self, kinds=None, type_name: str | None = None, like: str | None = None):
        sql = "SELECT zone_path, ref, type, type_name, name, kind, ncols, text FROM texts WHERE 1=1"
        params: list = []
        if kinds:
            placeholders = ",".join("?" * len(kinds))
            sql += f" AND kind IN ({placeholders})"
            params += list(kinds)
        if type_name:
            sql += " AND type_name = ?"
            params.append(type_name)
        if like is not None:
            sql += " AND text LIKE ? ESCAPE '\\'"
            params.append(f"%{_escape_like(like)}%")
        for r in self.conn.execute(sql, params):
            yield (
                r[0],
                TextRecord(
                    ref=r[1], type=r[2], type_name=r[3], name=r[4], kind=r[5], ncols=r[6], text=r[7]
                ),
            )


def _escape_like(term: str) -> str:
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
