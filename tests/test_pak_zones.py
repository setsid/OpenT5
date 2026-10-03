"""The .pak reader and writer, and streamed-image replacement, on the real files.

Skipped unless .env points at the game folders. Header values are those recorded in
docs/research/pak.md; the slow tests rewrite whole paks (hundreds of MB).
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import pytest

from opent5 import env
from opent5.container.pak import Pak, compare, header_bytes, sha1_of
from opent5.edit import Document

pytestmark = pytest.mark.zones


def disc(name: str) -> Path:
    folder = env.path_of("OPENT5_ZONES")
    if folder is None or not (folder / name).is_file():
        pytest.skip(f"{name} not available (OPENT5_ZONES)")
    return folder / name


def update(name: str) -> Path:
    folder = env.path_of("OPENT5_PATCH_ZONES")
    if folder is None or not (folder / name).is_file():
        pytest.skip(f"{name} not available (OPENT5_PATCH_ZONES)")
    return folder / name


def test_mp_nuked_pak_header():
    with Pak.open(disc("mp_nuked.pak")) as pak:
        assert (pak.timestamp, pak.field08, pak.count) == (0x4CA3E2EE, 1, 0x805)
        assert (pak.sector, pak.header_size, pak.reserved) == (0x800, 0x2800, 0)
        assert pak.header_size == header_bytes(pak.count)
        assert pak.starts[:3] == (5, 0x45, 0x55)
        assert pak.order == list(range(pak.count))
        last = pak.order[-1]
        assert pak.offset(last) + pak.capacities[last] == pak.size == 172005376


def test_img_patch_round_trip_and_its_two_regions():
    path = update("img_patch.pak")
    with Pak.open(path) as pak:
        assert pak.field08 == 0x11 and pak.count == 256
        assert pak.order[:3] == [57, 58, 59]  # the resident region comes first in the file
        assert pak.starts[0] == 0xD90
        sha = hashlib.sha1()
        for piece in pak.chunks():
            sha.update(piece)
    assert sha.hexdigest() == sha1_of(path)


@pytest.mark.slow
def test_mp_nuked_pak_round_trip_is_byte_identical():
    path = disc("mp_nuked.pak")
    with Pak.open(path) as pak:
        sha = hashlib.sha1()
        for piece in pak.chunks():
            sha.update(piece)
    assert sha.hexdigest() == sha1_of(path)


@pytest.mark.slow
def test_replace_a_streamed_mp_nuked_texture(tmp_path):
    ff = disc("mp_nuked.ff")
    doc = Document.open(ff)
    ref = doc.find("~-gmp_nuked_townsign_c", "image")
    assert ref is not None
    info = doc.image(ref.index).info
    assert [(p["slot"], p["entry"]) for p in info["parts"]] == [
        (1, 9845),
        (0, 704),
        (0, 703),
        (0, 702),
    ]
    y, x = np.mgrid[0:512, 0:512]
    rgba = np.zeros((512, 512, 4), np.uint8)
    rgba[..., 3] = 255
    rgba[((x // 32) + (y // 32)) % 2 == 0] = (255, 0, 255, 255)
    doc.replace_image(ref.index, rgba)
    out = tmp_path / "mp_nuked.ff"
    report = doc.save(out)
    assert report.verified, report.problems
    assert report.identical and report.sha1 == sha1_of(ff)
    (pak,) = report.details["paks"]
    assert pak["edited_entries"] == [702, 703, 704] and pak["entries_identical"] == 2050
    with Pak.open(disc("mp_nuked.pak")) as src, Pak.open(out.parent / "mp_nuked.pak") as new:
        assert new.size == src.size
        edited = {i: new.read(i) for i in (702, 703, 704)}
        assert compare(src, new, edited) == []
    back = Document.open(out).image(ref.index)
    assert back.info["source"] == "pak slot 0 entry 702"
    assert np.array_equal(back.rgba, rgba)
