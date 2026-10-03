"""The packaged-build self-test must catch a view whose module cannot be imported."""

import json
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from opent5 import env  # noqa: E402

PATCH = (env.path_of("OPENT5_PATCH_ZONES") or env.ROOT / "missing") / "patch_mp.ff"


@pytest.mark.zones
@pytest.mark.skipif(not PATCH.is_file(), reason="patch_mp.ff is not configured")
def test_a_view_module_missing_from_the_build_fails_the_selftest(tmp_path, monkeypatch):
    from opent5.gui import selftest, zonepage

    views = dict(zonepage.VIEWS)
    views["hex"] = ("opent5.gui.views.not_bundled", "HexView", "Hex")
    monkeypatch.setattr(zonepage, "VIEWS", views)
    report = tmp_path / "report.json"

    code = selftest.run([str(PATCH)], str(report))

    data = json.loads(report.read_text())
    assert code == 1 and not data["ok"]
    hex_failures = [f for f in data["failed"] if f["view"] == "hex"]
    assert hex_failures
    assert all("No module named" in f["problem"] for f in hex_failures)
    assert not [f for f in data["failed"] if f["view"] != "hex"]
