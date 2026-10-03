"""The cross-zone search index: cache keying, query logic, and a zone-marked proof.

The fast tests fabricate index records, so they need no game files. The slow test at
the end builds the real index from the zones in .env and checks known hits; it is marked
``zones`` and ``slow`` and skips when the zones are absent.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from opent5 import env
from opent5.index import Index, Store, build, index_zone
from opent5.index.builder import index_zone as _index_zone
from opent5.index.model import AssetRecord, TextRecord, ZoneResult
from opent5.index.store import SCHEMA_VERSION, default_cache_dir


def _result(path="/z/mp_demo.ff", mtime=100.0, size=200) -> ZoneResult:
    zr = ZoneResult(path, mtime, size, Path(path).stem)
    zr.assets = [
        AssetRecord(0, None, 38, "rawfile", "maps/mp/gametypes/dm.gsc", 16, 500),
        AssetRecord(1, None, 25, "localize", "NUKETOWN", 600, 40),
        AssetRecord(None, "inline:10:logo", 10, "image", "mp_demo_logo", 0, 0, parent=3),
    ]
    zr.texts = [
        TextRecord(
            "0",
            38,
            "rawfile",
            "maps/mp/gametypes/dm.gsc",
            "rawfile",
            None,
            "line one\nlevel.gameEnded = true;\nlast line",
        ),
        TextRecord("1", 25, "localize", "NUKETOWN", "localize", None, "Nuketown"),
        TextRecord("2", 39, "stringtable", "mp/x.csv", "cell", 2, "a\nNuketown\nc\nd"),
    ]
    return zr


# -- cache keying -------------------------------------------------------------------------


def test_fresh_tracks_mtime_and_size(tmp_path):
    with Store.open(tmp_path) as s:
        s.put(_result())
        assert s.fresh("/z/mp_demo.ff", 100.0, 200)
        assert not s.fresh("/z/mp_demo.ff", 101.0, 200)  # touched
        assert not s.fresh("/z/mp_demo.ff", 100.0, 201)  # resized
        assert not s.fresh("/z/other.ff", 100.0, 200)  # unknown


def test_put_replaces_a_zone(tmp_path):
    with Store.open(tmp_path) as s:
        s.put(_result())
        s.put(_result())  # again: no duplication
        assert s.stats()["zones"] == 1
        assert s.stats()["assets"] == 2  # two top-level, one inline counted separately
        assert s.stats()["inline_assets"] == 1


def test_prune_drops_zones_not_kept(tmp_path):
    with Store.open(tmp_path) as s:
        s.put(_result("/z/a.ff"))
        s.put(_result("/z/b.ff"))
        assert s.prune({"/z/a.ff"}) == 1
        assert s.indexed_paths() == {"/z/a.ff"}


def test_schema_mismatch_wipes(tmp_path, monkeypatch):
    with Store.open(tmp_path) as s:
        s.put(_result())
    # Pretend the stored schema is from an older build.
    import sqlite3

    conn = sqlite3.connect(str(tmp_path / "index.sqlite3"))
    conn.execute("UPDATE meta SET value='0' WHERE key='schema'")
    conn.commit()
    conn.close()
    with Store.open(tmp_path) as s:
        assert s.stats()["zones"] == 0
        row = s.conn.execute("SELECT value FROM meta WHERE key='schema'").fetchone()
        assert row[0] == str(SCHEMA_VERSION)


def test_default_cache_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENT5_CACHE_DIR", str(tmp_path / "explicit"))
    assert default_cache_dir() == tmp_path / "explicit"
    monkeypatch.delenv("OPENT5_CACHE_DIR")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    assert default_cache_dir() == tmp_path / "xdg" / "opent5"


# -- query logic --------------------------------------------------------------------------


@pytest.fixture
def index(tmp_path):
    store = Store.open(tmp_path)
    store.put(_result())
    yield Index(store)
    store.close()


def test_search_finds_every_kind(index):
    r = index.search("Nuketown")
    kinds = {(h.type_name, h.kind) for h in r.hits}
    assert ("localize", "name") in kinds  # the localize key is an asset name
    assert ("localize", "localize") in kinds  # its value
    assert ("stringtable", "cell") in kinds
    assert r.total == len(r.hits) == 3


def test_cell_hit_maps_to_row_and_column(index):
    r = index.search("Nuketown", kinds=("cell",))
    assert len(r.hits) == 1
    hit = r.hits[0]
    assert (hit.row, hit.column) == (0, 1)  # second cell, two columns
    assert hit.where == "row 0, column 1"


def test_rawfile_hit_has_line_number(index):
    r = index.search("level.gameEnded", kinds=("rawfile",))
    assert [h.line for h in r.hits] == [2]
    assert r.hits[0].snippet == "level.gameEnded = true;"


def test_names_only_is_name_kind(index):
    r = index.search("mp_demo_logo", names_only=True)
    assert len(r.hits) == 1
    assert r.hits[0].kind == "name" and r.hits[0].asset_ref == "inline:10:logo"


def test_name_ranking(index):
    r = index.search("nuketown")  # exact (case-insensitive) name beats a substring text hit
    assert r.hits[0].kind == "name"
    assert r.hits[0].score >= r.hits[-1].score


def test_type_filter(index):
    r = index.search("dm.gsc", type_name="rawfile")
    assert {h.type_name for h in r.hits} == {"rawfile"}


def test_zone_glob(index):
    assert index.search("Nuketown", zone="mp_*").total == 3
    assert index.search("Nuketown", zone="zombie_*").total == 0


def test_regex_and_case(index):
    assert index.search("NUKE.*", regex=True).total >= 1
    assert index.search("nuketown", case=True, kinds=("localize",)).total == 0
    assert index.search("Nuketown", case=True, kinds=("localize",)).total == 1


def test_limit_and_truncation(index):
    r = index.search("Nuketown", limit=1)
    assert len(r.hits) == 1 and r.total == 3 and r.truncated


def test_bad_kind_rejected(index):
    with pytest.raises(ValueError):
        index.search("x", kinds=("nope",))


# -- building, without game files ---------------------------------------------------------


def test_index_zone_records_error_not_raises(tmp_path):
    junk = tmp_path / "not_a.ff"
    junk.write_bytes(b"this is not a fastfile" * 10)
    result = _index_zone(junk)
    assert result.error and not result.assets
    assert result.zone_name == "not_a"


def test_index_zone_missing_file(tmp_path):
    result = index_zone(tmp_path / "gone.ff")
    assert result.error and "stat failed" in result.error


def test_build_reuses_then_rebuilds_on_change(tmp_path):
    zone = tmp_path / "junk.ff"
    zone.write_bytes(b"garbage")
    with Store.open(tmp_path / "cache") as s:
        first = build([zone], s, jobs=1)
        assert first["indexed"] == 1 and first["reused"] == 0 and first["failed"] == 1
        second = build([zone], s, jobs=1)
        assert second["reused"] == 1 and second["indexed"] == 0
        zone.write_bytes(b"garbage and more")  # size changes
        third = build([zone], s, jobs=1)
        assert third["indexed"] == 1 and third["reused"] == 0
        fourth = build([], s, jobs=1)  # the zone left the dump
        assert fourth["pruned"] == 1 and s.indexed_paths() == set()


def test_cli_register_and_commands():
    import argparse

    from opent5.index.cli import COMMANDS, register

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true")
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    register(sub, parents=[common])
    assert set(COMMANDS) == {"search", "index"}
    args = parser.parse_args(["search", "GetEnt", "--names", "--limit", "5"])
    assert args.command == "search" and args.names and args.limit == 5
    args = parser.parse_args(["index", "build", "--rebuild"])
    assert args.command == "index" and args.index_command == "build" and args.rebuild


# -- the real zones (slow) ----------------------------------------------------------------


@pytest.mark.zones
@pytest.mark.slow
def test_build_and_search_real_zones(tmp_path):
    zones = env.all_zones()
    if not zones:
        pytest.skip("no zones configured (OPENT5_ZONES / .env)")
    with Store.open(tmp_path) as store:
        cold_start = time.perf_counter()
        summary = build(zones, store)
        cold = time.perf_counter() - cold_start
        assert summary["indexed"] >= 1
        index = Index(store)

        # A known string lands in the expected zone.
        nuke = index.search("Nuketown")
        assert any(h.zone == "mp_nuked" for h in nuke.hits), "Nuketown not found in mp_nuked"

        # An asset name resolves to the zone(s) that hold it.
        names = index.search("mp_nuked", names_only=True)
        assert any(h.zone == "mp_nuked" and h.asset_name == "mp_nuked" for h in names.hits)

        # A rawfile code search returns a file and a line.
        code = index.search("level.gameEnded", kinds=("rawfile",))
        assert code.total >= 1
        assert all(h.line and h.kind == "rawfile" for h in code.hits)

        # A bad zone is recorded, never fatal.
        assert summary["failed"] == len(store.errors())

        warm_start = time.perf_counter()
        warm_summary = build(zones, store)
        warm = time.perf_counter() - warm_start
        assert (
            warm_summary["reused"] == summary["zones"] - summary["failed"]
            or warm_summary["reused"] >= 1
        )
        assert warm < cold  # the cache makes the second build far quicker

    # A touched zone is detected and rebuilt on its own.
    with Store.open(tmp_path) as store:
        target = str(zones[0])
        store.conn.execute("UPDATE zones SET size = size + 1 WHERE path = ?", (target,))
        store.conn.commit()
        again = build(zones, store)
        assert again["indexed"] >= 1 and again["reused"] >= 1
