"""Shared strings (``shared_with``, ``share="split" | "all"``), progress callbacks, and
``field_info``: on the synthetic zone of test_edit_document (no game files), then on
code_post_gfx_mp and patch_mp (``zones``)."""

from __future__ import annotations

from pathlib import Path

import pytest

from opent5 import env
from opent5.edit import Document, EditError
from test_edit_document import synthetic_ff


@pytest.fixture
def doc(tmp_path):
    path = tmp_path / "in" / "mp_testedit.ff"
    path.parent.mkdir()
    path.write_bytes(synthetic_ff())
    return Document.open(path)


def labels(fields) -> list[tuple]:
    return [(f.index, f.field, f.owner) for f in fields]


def test_shared_with_lists_the_other_fields(doc):
    # asset 1's value points at asset 0's "Hello"; cell 4 of the table at cell 0's "alpha"
    assert labels(doc.shared_with(0)) == [(1, "value", False)]
    assert labels(doc.shared_with(1)) == [(0, "value", True)]
    assert labels(doc.shared_with(4, 0, 0)) == [(4, "row 2, column 0", False)]
    assert labels(doc.shared_with(4, 2, 0)) == [(4, "row 0, column 0", True)]
    assert labels(doc.shared_with(9)) == [(2, "RawFile.name", True)]
    assert doc.shared_with(8) == []
    assert doc.shared_with(1)[0].label == "GREETING"
    with pytest.raises(EditError):
        doc.shared_with(4)  # a table needs the cell


def test_split_is_the_default_and_unchanged(doc):
    doc.set_localize(1, "Bye")
    assert doc.localize(0)[1] == "Hello"
    assert "share=split" in doc.changes()[-1].detail
    assert doc.shared_with(0) == [] and doc.shared_with(1) == []


def test_share_all_changes_every_sharing_key(doc, tmp_path):
    doc.set_localize(1, "Howdy, a longer greeting", share="all")
    assert doc.localize(0)[1] == doc.localize(1)[1] == "Howdy, a longer greeting"
    change = doc.changes()[-1]
    assert change.detail.startswith("share=all") and "GREETING" in change.detail
    assert labels(doc.shared_with(0)) == [(1, "value", False)]  # still one stored string
    report = doc.save(tmp_path / "out.ff")
    assert report.verified, report.problems
    back = Document.open(tmp_path / "out.ff")
    assert back.localize(0)[1] == back.localize(1)[1] == "Howdy, a longer greeting"
    assert labels(back.shared_with(1)) == [(0, "value", True)]


def test_share_all_on_cells_rehashes_and_resorts(doc, tmp_path):
    doc.set_cell(4, 2, 0, "gamma, longer", share="all")
    assert [r[0] for r in doc.table(4)] == ["gamma, longer", "beta", "gamma, longer"]
    report = doc.save(tmp_path / "out.ff")
    assert report.verified, report.problems
    assert Document.open(tmp_path / "out.ff").table(4)[0][0] == "gamma, longer"


def test_share_all_undo_redo(doc, tmp_path):
    doc.set_localize(0, "Changed", share="all")
    doc.undo()
    assert doc.localize(0)[1] == doc.localize(1)[1] == "Hello"
    assert doc.save(tmp_path / "same.ff").identical
    doc.redo()
    assert doc.localize(1)[1] == "Changed"


def test_share_all_refuses_renaming_an_asset(doc):
    # CFG_NAME's value is the rawfile's name "test.cfg"
    with pytest.raises(EditError, match="name of asset 2"):
        doc.set_localize(9, "other.cfg", share="all")
    doc.set_localize(9, "other.cfg")  # split works
    assert doc.asset(2).name == "test.cfg"


def test_share_mode_is_checked(doc):
    with pytest.raises(EditError, match="share"):
        doc.set_localize(0, "x", share="both")


def test_progress_reports_real_counts(doc, tmp_path):
    seen: dict[str, list] = {}

    def progress(stage, done, total):
        seen.setdefault(stage, []).append((done, total))

    Document.open(doc.path, progress=progress)
    assert "Decrypting and inflating" in seen and "Parsing assets" in seen
    assert seen["Parsing assets"][0] == (0, 10)
    seen.clear()
    doc.set_localize(8, "World, edited")
    report = doc.save(tmp_path / "out.ff", progress=progress)
    assert report.verified
    for stage in ("Writing assets", "Compressing", "Encrypting", "Writing the file"):
        assert stage in seen, stage
    assert "Verifying assets" in seen and "Verifying: parsing" in seen


def test_field_info_says_why_not(doc):
    info = doc.field_info(5, "texture.width")
    assert info["editable"] and info["type"] == "u16" and info["value"] == 8
    with pytest.raises(EditError):
        doc.field_info(5, "no_such_field")


# -- zones -----------------------------------------------------------------------------------


def find(name: str) -> Path | None:
    for z in env.all_zones():
        if z.stem == name and "dlc" not in str(z):
            return z
    return None


def key_index(doc: Document, key: str) -> int:
    for r in doc.assets:
        if r.type_name == "localize" and r.name == key:
            return r.index
    raise AssertionError(key)


@pytest.mark.zones
@pytest.mark.slow
def test_code_post_gfx_mp_player_match(tmp_path):
    """The case from the RPCS3 run: the menu button reads MPUI_PLAYER_MATCH_CAPS, which shares
    "PLAYER MATCH" with MENU_PLAYER_MATCH_CAPS. share="all" changes both and the saved file
    verifies (verification reads both keys back); share="split" changes only one."""
    path = find("code_post_gfx_mp")
    if path is None:
        pytest.skip("code_post_gfx_mp.ff is not configured")
    doc = Document.open(path)
    mpui, menu = key_index(doc, "MPUI_PLAYER_MATCH_CAPS"), key_index(doc, "MENU_PLAYER_MATCH_CAPS")
    assert doc.localize(mpui)[1] == doc.localize(menu)[1] == "PLAYER MATCH"
    assert [f.name for f in doc.shared_with(mpui)] == ["MENU_PLAYER_MATCH_CAPS"]
    assert [f.name for f in doc.shared_with(menu)] == ["MPUI_PLAYER_MATCH_CAPS"]
    doc.set_localize(mpui, "PLAYER MATCH (EDITED)", share="all")
    assert doc.localize(menu)[1] == "PLAYER MATCH (EDITED)"
    report = doc.save(tmp_path / "cpg.ff")
    assert report.verified, report.problems[:3]
    assert report.details["edited_read_back"] == 2  # both keys read back from the file
    doc.undo()
    doc.set_localize(mpui, "PLAYER MATCH (EDITED)")
    assert doc.localize(menu)[1] == "PLAYER MATCH"
    assert doc.shared_with(mpui) == [] and doc.shared_with(menu) == []


@pytest.mark.zones
@pytest.mark.slow
def test_patch_mp_weapon_fields_save_and_count_refused(tmp_path):
    path = find("patch_mp")
    if path is None:
        pytest.skip("patch_mp.ff is not configured")
    doc = Document.open(path)
    weapon = next(r for r in doc.assets if r.type_name == "weapon")
    header = doc.fields(weapon.index)["header"]
    floats = [k for k, v in header.items() if isinstance(v, float) and not k.startswith("unk")]
    ints = [
        k
        for k, v in header.items()
        if isinstance(v, int)
        and not k.startswith("unk")
        and doc.field_info(weapon.index, k)["editable"]
    ]
    f_name, i_name = floats[0], ints[0]
    f_info = doc.field_info(weapon.index, f_name)
    i_info = doc.field_info(weapon.index, i_name)
    doc.set_field(weapon.index, f_name, f_info["value"] + 1.5)
    doc.set_field(weapon.index, i_name, i_info["value"] ^ 1)
    report = doc.save(tmp_path / "patch_mp.ff")
    assert report.verified, report.problems[:3]
    assert report.details["edited_read_back"] == 2  # both fields read back from the file
    # a count field: field_info says why, set_field refuses
    material = next(r for r in doc.assets if r.type_name == "material")
    info = doc.field_info(material.index, "textureCount")
    assert info["role"] == "count" and not info["editable"] and "count" in info["reason"]
    with pytest.raises(EditError):
        doc.set_field(material.index, "textureCount", info["value"] + 1)
