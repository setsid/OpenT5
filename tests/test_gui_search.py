"""The cross-zone (global) search panel, offscreen, without pytest-qt.

Fake ``Hit``/``SearchResult`` objects drive the panel model, and ``run_search`` is
checked against a fake index so no real zones or cache are needed.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

APP = QApplication.instance() or QApplication([])

from opent5.gui import panels  # noqa: E402
from opent5.index.model import Hit, SearchResult  # noqa: E402


def _hit(zone, name, type_name, ref, kind, where, snippet, **kw) -> Hit:
    return Hit(
        zone=zone,
        zone_path=f"/zones/{zone}.ff",
        type=0,
        type_name=type_name,
        asset_name=name,
        asset_ref=ref,
        kind=kind,
        where=where,
        snippet=snippet,
        **kw,
    )


def _result() -> SearchResult:
    hits = [
        _hit("mp_nuked", "CGAME_SB_ACCURACY", "localize", "95", "localize", "value", "Accuracy"),
        _hit(
            "mp_nuked",
            "maps/mp/_x.gsc",
            "rawfile",
            "12",
            "rawfile",
            "line 42",
            "level.foo = 1;",
            line=42,
        ),  # fmt: skip
        _hit(
            "patch_mp",
            "mp/mapstable.csv",
            "stringtable",
            "7",
            "cell",
            "row 3, column 1",
            "Nuketown",
            row=3,
            column=1,
        ),  # fmt: skip
    ]
    return SearchResult("x", hits, total=3, truncated=False, zones_searched=2, seconds=0.012,
                        kinds=("name",))  # fmt: skip


def test_panel_groups_results_by_zone():
    panel = panels.GlobalSearchPanel()
    panel.set_result(_result())
    assert panel.tree.topLevelItemCount() == 2  # mp_nuked, patch_mp
    top0 = panel.tree.topLevelItem(0)
    assert top0.text(0).startswith("mp_nuked") and "(2)" in top0.text(0)
    child = top0.child(1)  # the rawfile line hit
    assert child.text(0) == "maps/mp/_x.gsc"
    assert child.text(2) == "line 42"
    assert "match" in panel.summary.text()


def test_double_click_emits_open_request():
    panel = panels.GlobalSearchPanel()
    panel.set_result(_result())
    captured = []
    panel.open_hit.connect(captured.append)
    item = panel.tree.topLevelItem(1).child(0)  # the patch_mp cell hit
    panel._activated(item, 0)
    assert len(captured) == 1
    assert captured[0].kind == "cell" and captured[0].zone == "patch_mp"
    # a zone group row carries no hit, so activating it does nothing
    panel._activated(panel.tree.topLevelItem(0), 0)
    assert len(captured) == 1


def test_empty_result_clears_the_tree():
    panel = panels.GlobalSearchPanel()
    panel.set_result(_result())
    panel.set_result(SearchResult("x", [], 0, False, 2, 0.001, ("name",)))
    assert panel.tree.topLevelItemCount() == 0
    assert panel.summary.text() == "no matches"


def test_emit_request_builds_params_from_header():
    panel = panels.GlobalSearchPanel()
    panel.query.setText("spawn")
    panel.names.setChecked(True)
    panel.case.setChecked(True)
    panel.regex.setChecked(True)
    panel.type_box.setText("weapon")
    panel.zone_box.setText("mp_*")
    panel.kind.setCurrentIndex([v for _l, v in panels.KIND_CHOICES].index("name"))
    seen = []
    panel.search_requested.connect(seen.append)
    panel._emit_request()
    (params,) = seen
    assert params.term == "spawn" and params.names_only and params.case and params.regex
    assert params.type_name == "weapon" and params.zone == "mp_*" and params.kind == "name"


def test_run_search_calls_the_index_api(monkeypatch):
    calls: dict = {}

    def fake_build(zones, store, progress=None):
        calls["build_zones"] = zones
        if progress is not None:
            progress(1, 2, "/zones/a.ff", "indexed")
        return {}

    class FakeIndex:
        def __init__(self, store):
            calls["index_store"] = store

        def search(self, term, **kw):
            calls["term"] = term
            calls["kw"] = kw
            return SearchResult(term, [], 0, False, 1, 0.0, ("name",))

    class FakeStore:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        @classmethod
        def open(cls, cache_dir=None):
            calls["cache_dir"] = cache_dir
            return cls()

    monkeypatch.setattr(panels, "build", fake_build)
    monkeypatch.setattr(panels, "Index", FakeIndex)
    monkeypatch.setattr(panels, "Store", FakeStore)
    monkeypatch.setattr(panels.env, "all_zones", lambda: ["/zones/a.ff"])

    progressed = []
    params = panels.SearchParams(
        term="killstreak", names_only=True, type_name="weapon", kind="name",
        regex=True, case=True, zone="mp_*",
    )  # fmt: skip
    result = panels.run_search(params, progress=lambda d, t: progressed.append((d, t)))

    assert calls["term"] == "killstreak"
    kw = calls["kw"]
    assert kw["names_only"] is True and kw["type_name"] == "weapon"
    assert kw["kinds"] == ("name",) and kw["regex"] is True and kw["case"] is True
    assert kw["zone"] == "mp_*"
    assert calls["build_zones"] == ["/zones/a.ff"]
    assert progressed == [(1, 2)]
    assert isinstance(result, SearchResult)


@pytest.mark.parametrize("names_only,kind,expect", [(False, "", None), (False, "cell", ("cell",))])
def test_kind_filter_maps_to_kinds(monkeypatch, names_only, kind, expect):
    captured: dict = {}

    class FakeIndex:
        def __init__(self, store):
            pass

        def search(self, term, **kw):
            captured["kinds"] = kw["kinds"]
            return SearchResult(term, [], 0, False, 0, 0.0, ())

    class FakeStore:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        @classmethod
        def open(cls, cache_dir=None):
            return cls()

    monkeypatch.setattr(panels, "build", lambda *a, **k: {})
    monkeypatch.setattr(panels, "Index", FakeIndex)
    monkeypatch.setattr(panels, "Store", FakeStore)
    monkeypatch.setattr(panels.env, "all_zones", lambda: [])
    panels.run_search(panels.SearchParams(term="x", names_only=names_only, kind=kind))
    assert captured["kinds"] == expect
