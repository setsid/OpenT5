"""Pure gizmo and transform maths for the in-place map editor, without a GPU or a window."""

from __future__ import annotations

import math

import numpy as np

from opent5.edit import gizmo as gz


def test_ray_plane_hits_ground():
    hit = gz.ray_plane((0, 0, 100), (0, 0, -1), (0, 0, 0), (0, 0, 1))
    assert np.allclose(hit, (0, 0, 0))


def test_ray_plane_parallel_is_none():
    assert gz.ray_plane((0, 0, 100), (1, 0, 0), (0, 0, 0), (0, 0, 1)) is None


def test_ray_plane_behind_is_none():
    assert gz.ray_plane((0, 0, 100), (0, 0, 1), (0, 0, 0), (0, 0, 1)) is None


def test_axis_param_recovers_distance_along_axis():
    # a ray that crosses the X axis at x = 40, looking straight down
    s = gz.axis_param((40, 0, 50), (0, 0, -1), (0, 0, 0), (1, 0, 0))
    assert math.isclose(s, 40.0, abs_tol=1e-6)


def test_translate_on_axis_moves_by_param_delta():
    out = gz.translate_on_axis((10, 20, 30), (1, 0, 0), 5.0, 25.0)
    assert np.allclose(out, (30, 20, 30))


def test_axis_param_parallel_ray_projects_origin():
    # ray parallel to the X axis, offset: the closest param is the origin's projection
    s = gz.axis_param((7, 3, 0), (1, 0, 0), (0, 0, 0), (1, 0, 0))
    assert math.isclose(s, 7.0, abs_tol=1e-6)


def test_rotation_delta_quarter_turn_about_z():
    centre = (0, 0, 0)
    start = (10, 0, 0)
    now = (0, 10, 0)
    d = gz.rotation_delta(centre, (0, 0, 1), start, now)
    assert math.isclose(d, math.pi / 2, abs_tol=1e-6)


def test_rotation_delta_sign_flips_with_axis():
    centre = (0, 0, 0)
    a = gz.rotation_delta(centre, (0, 0, 1), (10, 0, 0), (0, 10, 0))
    b = gz.rotation_delta(centre, (0, 0, -1), (10, 0, 0), (0, 10, 0))
    assert math.isclose(a, -b, abs_tol=1e-6)


def test_rotate_angles_z_changes_yaw():
    out = gz.rotate_angles((0.0, 10.0, 0.0), 2, 90.0)
    assert out == (0.0, 100.0, 0.0)


def test_rotate_angles_wraps():
    out = gz.rotate_angles((0.0, 350.0, 0.0), 2, 20.0)
    assert math.isclose(out[1], 10.0, abs_tol=1e-9)


def test_rotate_angles_x_is_roll_y_is_pitch():
    assert gz.rotate_angles((0, 0, 0), 0, 30.0) == (0.0, 0.0, 30.0)
    assert gz.rotate_angles((0, 0, 0), 1, 30.0) == (30.0, 0.0, 0.0)


def test_snap():
    assert gz.snap(13.0, 8.0) == 16.0
    assert gz.snap(11.0, 8.0) == 8.0
    assert gz.snap(5.0, 0.0) == 5.0
    assert np.allclose(gz.snap_vec((13, 3, -5), 8), (16, 0, -8))


def test_duplicate_offset():
    assert np.allclose(gz.duplicate_offset((1, 2, 3), (64, 0, 0)), (65, 2, 3))


def test_project_point_centre_of_view():
    # looking down -X from (100,0,0): a point at the origin projects to screen centre
    eye = np.array([100.0, 0, 0])
    forward = np.array([-1.0, 0, 0])
    right = np.array([0.0, 1, 0])
    up = np.array([0.0, 0, 1])
    sx, sy, depth, front = gz.project_point((0, 0, 0), eye, right, up, forward, 200.0, 640, 480)
    assert front
    assert math.isclose(sx, 320.0, abs_tol=1e-6)
    assert math.isclose(sy, 240.0, abs_tol=1e-6)
    assert math.isclose(depth, 100.0, abs_tol=1e-6)


def test_project_point_behind_camera():
    eye = np.array([100.0, 0, 0])
    forward = np.array([-1.0, 0, 0])
    right = np.array([0.0, 1, 0])
    up = np.array([0.0, 0, 1])
    _sx, _sy, _depth, front = gz.project_point(
        (200, 0, 0), eye, right, up, forward, 200.0, 640, 480
    )
    assert not front


def test_nearest_marker_picks_closest_within_radius():
    pts = np.array([[100.0, 100.0], [300.0, 300.0], [305.0, 302.0]])
    assert gz.nearest_marker(pts, (302, 301), 10.0) == 1
    assert gz.nearest_marker(pts, (500, 500), 10.0) is None


def test_nearest_marker_depth_tie_break():
    pts = np.array([[300.0, 300.0], [300.0, 300.0]])
    # both equally close; the nearer depth wins
    assert gz.nearest_marker(pts, (300, 300), 10.0, depths=[50.0, 10.0]) == 1
