"""The texture-pack workflow (opent5.texpack).

The name-mapping tests are synthetic and need no game files. The apply/list tests open the
real mp_nuked zone and are skipped unless .env (or OPENT5_ZONES) points at it; the ones that
rewrite the whole 172 MB level pak are marked slow.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from opent5 import env
from opent5.formats import texture as tx
from opent5.texpack import (
    NameIndex,
    apply_pack,
    list_images,
    load_map,
    normalise,
    preview_pack,
    suggested_filename,
)
from opent5.texpack.naming import TexpackError

# -- the name mapping, with no game files ----------------------------------------------------

NAMES = [
    "~-gmp_nuked_sign_c",
    "~-gus_art_color_white_c",
    "~-gus_art_color_white_n",
    "~-gus_art_color_white_s",
    "~~-gus_art_color_chrome_g",
    "whitesquare",
]


def test_normalise_strips_marker_and_suffix():
    assert normalise("~-gmp_nuked_sign_c") == ("mp_nuked_sign", "_c")
    assert normalise("~~-gus_art_color_chrome_g") == ("us_art_color_chrome_g", "")
    assert normalise("whitesquare") == ("whitesquare", "")
    assert normalise("MP_Nuked_Sign") == ("mp_nuked_sign", "")


def test_match_cleaned_and_raw():
    idx = NameIndex(NAMES)
    assert idx.match("mp_nuked_sign").name == "~-gmp_nuked_sign_c"
    assert idx.match("mp_nuked_sign").how == "cleaned"
    assert idx.match("~-gmp_nuked_sign_c").how == "stored-name"
    assert idx.match("whitesquare").name == "whitesquare"


def test_match_suffix_disambiguates():
    idx = NameIndex(NAMES)
    assert idx.match("us_art_color_white_c").name == "~-gus_art_color_white_c"
    assert idx.match("us_art_color_white_n").name == "~-gus_art_color_white_n"


def test_match_ambiguous_core_is_left_alone():
    idx = NameIndex(NAMES)
    m = idx.match("us_art_color_white")
    assert m.name is None and m.how == "ambiguous"
    assert set(m.candidates) == {
        "~-gus_art_color_white_c",
        "~-gus_art_color_white_n",
        "~-gus_art_color_white_s",
    }


def test_match_no_match_and_wrong_suffix():
    idx = NameIndex(NAMES)
    assert idx.match("nope").how == "no-match"
    wrong = idx.match("us_art_color_white_x")
    assert wrong.name is None


def test_suggested_filename():
    assert suggested_filename("~-gmp_nuked_sign_c") == "mp_nuked_sign_c.png"
    assert suggested_filename("whitesquare") == "whitesquare.png"


def test_load_map_json(tmp_path):
    p = tmp_path / "m.json"
    p.write_text(json.dumps({"sign.png": "~-gmp_nuked_sign_c", "x": "whitesquare"}))
    m = load_map(p)
    assert m.get("sign", "sign.png") == "~-gmp_nuked_sign_c"
    assert m.get("x", "x.png") == "whitesquare"


def test_load_map_csv(tmp_path):
    p = tmp_path / "m.csv"
    p.write_text("file,image\nsign.png,~-gmp_nuked_sign_c\nwhite,~-gus_art_color_white_c\n")
    m = load_map(p)
    assert m.get("sign", "sign.png") == "~-gmp_nuked_sign_c"
    assert m.get("white", "white.png") == "~-gus_art_color_white_c"


def test_load_map_errors(tmp_path):
    with pytest.raises(TexpackError):
        load_map(tmp_path / "missing.json")
    bad = tmp_path / "bad.json"
    bad.write_text("[1, 2, 3]")
    with pytest.raises(TexpackError):
        load_map(bad)


# -- the apply / list workflow on the real zone ----------------------------------------------


def _mp_nuked() -> Path:
    folder = env.path_of("OPENT5_ZONES")
    if folder is None or not (folder / "mp_nuked.ff").is_file():
        pytest.skip("mp_nuked.ff not available (OPENT5_ZONES)")
    return folder / "mp_nuked.ff"


def _checker(w: int, h: int, a, b, block: int = 16) -> np.ndarray:
    """A block-aligned two-colour checker: DXT encodes it back exactly."""
    y, x = np.mgrid[0:h, 0:w]
    img = np.zeros((h, w, 4), np.uint8)
    img[..., 3] = 255
    mask = ((x // block) + (y // block)) % 2 == 0
    img[mask] = a
    img[~mask] = b
    return img


def _build_pack(ff: Path, folder: Path) -> dict[str, np.ndarray]:
    """A pack of recoloured copies of two deferred mp_nuked images, plus a non-matching file
    and a wrong-size file. Returns the new pixels expected back for the two real images."""
    from opent5.edit import Document

    folder.mkdir(parents=True, exist_ok=True)
    doc = Document.open(str(ff))
    expected = {}
    spec = {
        "~-gus_art_color_white_c": ("us_art_color_white_c.png", (255, 0, 255, 255), (0, 0, 0, 255)),
        "~-gus_art_streetsigns_c": ("us_art_streetsigns_c.png", (0, 255, 0, 255), (0, 0, 255, 255)),
    }
    for name, (fname, a, b) in spec.items():
        info = doc.image(doc.find(name, "image").index).info
        orig = doc.image(doc.find(name, "image").index).rgba  # decode the original first
        assert orig is not None
        img = _checker(info["width"], info["height"], a, b)
        (folder / fname).write_bytes(tx.write_png(img))
        expected[name] = img
    nomatch = tx.write_png(_checker(16, 16, (1,) * 4, (2,) * 4))
    (folder / "totally_not_a_zone_image.png").write_bytes(nomatch)
    wrong_size = tx.write_png(_checker(64, 64, (9,) * 4, (1,) * 4))
    (folder / "us_art_color_black_c.png").write_bytes(wrong_size)
    return expected


@pytest.mark.zones
def test_list_images_names_mp_nuked_textures(tmp_path):
    ff = _mp_nuked()
    images = list_images(ff)
    names = {d["name"] for d in images}
    assert "~-gus_art_color_white_c" in names
    assert any(d["pixels"] == "deferred" for d in images)
    only = list_images(ff, replaceable_only=True)
    assert 0 < len(only) <= len(images)
    assert all(d["replaceable"] for d in only)


@pytest.mark.zones
def test_template_round_trips_to_a_usable_map(tmp_path):
    from opent5.texpack import write_template

    ff = _mp_nuked()
    out = tmp_path / "tmpl.json"
    write_template(ff, out)
    data = json.loads(out.read_text())
    # every suggested file name maps back to a real stored image name
    names = {d["name"] for d in list_images(ff, replaceable_only=True)}
    assert data and set(data.values()) <= names


@pytest.mark.zones
def test_dry_run_lists_matches_without_writing(tmp_path):
    ff = _mp_nuked()
    pack = tmp_path / "pack"
    _build_pack(ff, pack)
    out = tmp_path / "out"
    result = preview_pack(ff, pack)
    assert result.dry_run and result.output is None
    assert not out.exists()
    by_name = {Path(f.file).name: f for f in result.files}
    assert by_name["us_art_color_white_c.png"].status == "would-replace"
    assert by_name["us_art_streetsigns_c.png"].status == "would-replace"
    assert by_name["totally_not_a_zone_image.png"].status == "skipped"
    assert by_name["us_art_color_black_c.png"].status == "failed"
    assert "64x64" in by_name["us_art_color_black_c.png"].reason
    assert result.replaced == 2 and result.skipped == 1 and result.failed == 1


@pytest.mark.zones
def test_apply_replaces_deferred_images_and_verifies(tmp_path):
    from opent5.edit import Document

    ff = _mp_nuked()
    pack = tmp_path / "pack"
    expected = _build_pack(ff, pack)
    out = tmp_path / "out"
    result = apply_pack(ff, pack, out)
    assert result.replaced == 2 and result.failed == 1 and result.skipped == 1
    assert result.report["verified"], result.report["problems"]
    saved = Path(result.output)
    assert saved == out / "mp_nuked.ff" and saved.is_file()
    # the deferred edit keeps the zone's length, so no pak is written
    assert "paks" not in result.report
    back = Document.open(str(saved))
    assert back.parse_problems == []
    for name, pixels in expected.items():
        got = back.image(back.find(name, "image").index).rgba
        assert np.array_equal(got, pixels), name


@pytest.mark.zones
def test_map_file_overrides_the_heuristic(tmp_path):
    ff = _mp_nuked()
    pack = tmp_path / "pack"
    pack.mkdir()
    from opent5.edit import Document

    doc = Document.open(str(ff))
    info = doc.image(doc.find("~-gus_art_color_white_c", "image").index).info
    (pack / "my_custom_name.png").write_bytes(
        tx.write_png(_checker(info["width"], info["height"], (10, 20, 30, 255), (40, 50, 60, 255)))
    )
    mp = tmp_path / "map.json"
    mp.write_text(json.dumps({"my_custom_name.png": "~-gus_art_color_white_c"}))
    result = preview_pack(ff, pack, map_file=mp)
    (f,) = result.files
    assert f.image == "~-gus_art_color_white_c" and f.how == "map"
    assert f.status == "would-replace"


@pytest.mark.zones
@pytest.mark.slow
def test_apply_streamed_image_writes_the_pak(tmp_path):
    """A streamed image (mp_nuked.pak): same-size replace writes the level pak beside the zone
    and the .ff stays byte-identical (pak.md 9)."""
    ff = _mp_nuked()
    pack = tmp_path / "pack"
    pack.mkdir()
    (pack / "mp_nuked_townsign_c.png").write_bytes(
        tx.write_png(_checker(512, 512, (255, 0, 255, 255), (0, 0, 0, 255), block=32))
    )
    out = tmp_path / "out"
    result = apply_pack(ff, pack, out)
    assert result.replaced == 1 and result.report["verified"]
    assert result.report["identical"]  # same-size streamed edit leaves the .ff unchanged
    (pak,) = result.report["paks"]
    assert pak["edited_entries"] == [702, 703, 704] and not pak["shared"]
    assert Path(pak["path"]) == out / "mp_nuked.pak" and Path(pak["path"]).is_file()
