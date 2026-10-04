"""The staged-map gate logic: a staged zone must be a distinct map, not the base."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import verify_staged_map as g  # noqa: E402

BLOCKY = {
    "gfx": {
        "surfaces": 614,
        "cells": 1,
        "mins": [-2592.0, -2592.0, 72.0],
        "maxs": [2592.0, 2592.0, 504.0],
    },
    "clip": {"brushes": 4205, "static_models": 0},
}
STOCK = {
    "gfx": {
        "surfaces": 3494,
        "cells": 23,
        "mins": [-45236.0, -69636.0, -3340.0],
        "maxs": [60308.0, 62976.0, 22492.0],
    },
    "clip": {"brushes": 5890, "static_models": 1385},
}
STOCK_PLUS_CLIP = {  # a clip-only edit: same world, one extra brush
    "gfx": STOCK["gfx"],
    "clip": {"brushes": 5891, "static_models": 1385},
}


def test_same_map_true_for_identical_world():
    assert g.same_map(STOCK, dict(STOCK))
    # a clip-only edit does not change the GfxWorld, so it still reads as the same map
    assert g.same_map(STOCK, STOCK_PLUS_CLIP)


def test_same_map_false_for_distinct_world():
    assert not g.same_map(BLOCKY, STOCK)


def test_same_map_false_without_gfx():
    assert not g.same_map({"gfx": None}, STOCK)


def test_blocky_against_base_passes():
    assert g.check(BLOCKY, STOCK, None, {}) == []


def test_base_against_base_fails():
    problems = g.check(STOCK, STOCK, None, {})
    assert problems and "was not replaced" in problems[0]


def test_expect_signature_mismatch_fails():
    assert g.check(STOCK, None, BLOCKY, {}) != []


def test_expect_brush_count_for_clip_edit():
    # a clip edit keeps the base world, so it is gated by the expected brush count, not by same_map
    assert g.check(STOCK_PLUS_CLIP, None, None, {"brushes": 5891}) == []
    assert g.check(STOCK_PLUS_CLIP, None, None, {"brushes": 5892}) != []


def test_missing_gfx_is_a_problem():
    assert g.check({"gfx": None, "clip": None}, None, None, {}) != []
