"""Pure camera maths for the geometry viewer: zoom-to-cursor, pan, framing and picking."""

import math
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np

from opent5.gui.views import mesh as mv


def _cam(yaw=35.0, pitch=30.0, distance=512.0, target=(0.0, 0.0, 0.0)):
    c = mv.Camera()
    c.yaw = math.radians(yaw)
    c.pitch = math.radians(pitch)
    c.distance = distance
    c.target = np.asarray(target, np.float64)
    return c


def test_cursor_ray_centre_is_forward():
    c = _cam()
    eye, d = mv.cursor_ray(c, 320, 240, 640, 480)
    _e, _r, _u, forward = c.basis()
    assert np.allclose(eye, c.basis()[0])
    assert np.allclose(d, forward, atol=1e-6)


def test_zoom_to_cursor_keeps_point_under_cursor():
    c = _cam()
    eye, d = mv.cursor_ray(c, 500, 150, 640, 480)  # an off-centre pixel
    _e, _r, _u, forward = c.basis()
    t = c.distance / float(d @ forward)
    aim = eye + d * t  # the world point under the cursor, at pivot depth
    target, distance = mv.dolly_to_ray(eye, forward, c.distance, d, 0.5, 1.0)
    new_eye = target - forward * distance
    # the point under the cursor still lies on the same ray from the new eye
    v = aim - new_eye
    assert np.linalg.norm(np.cross(v, d)) < 1e-6
    assert float(v @ d) > 0  # still in front, not passed yet
    assert distance < c.distance  # zoomed in


def test_zoom_passes_through_without_stalling():
    c = _cam(distance=100.0)
    forward = c.basis()[3]
    min_dist = 1.0
    eye = c.basis()[0]
    start = float(eye @ forward)
    # zoom in hard, many notches: the eye must keep advancing past the floor
    for _ in range(40):
        eye = c.basis()[0]
        _e, d = mv.cursor_ray(c, 320, 240, 640, 480)
        c.target, c.distance = mv.dolly_to_ray(eye, forward, c.distance, d, 0.8, min_dist)
    assert c.distance == min_dist  # floored, not driven to zero
    moved = float(c.basis()[0] @ forward) - start
    assert moved > 50.0  # the eye travelled well past the original pivot depth


def test_pan_delta_scales_with_distance():
    right = np.array([1.0, 0.0, 0.0])
    up = np.array([0.0, 0.0, 1.0])
    near = mv.pan_delta(right, up, 100.0, 500.0, 10.0, 0.0)
    far = mv.pan_delta(right, up, 400.0, 500.0, 10.0, 0.0)
    assert np.allclose(far, near * 4.0)  # four times the depth, four times the shift


def test_frame_distance_fits_radius():
    d = mv.frame_distance(100.0, 50.0, 1.0)
    half = math.radians(50.0) / 2.0
    assert math.isclose(d, 100.0 / math.sin(half) * 0.85, rel_tol=1e-9)


def test_look_angles_round_trip():
    eye = np.array([120.0, -40.0, 60.0])
    target = np.array([10.0, 15.0, -5.0])
    c = mv.Camera()
    c.yaw, c.pitch, c.distance = mv.look_angles(eye, target)
    c.target = target
    assert np.allclose(c.basis()[0], eye, atol=1e-6)


def test_zoom_to_cursor_tracks_the_surface():
    # with an anchor distance (the depth of the surface under the cursor), the anchored point
    # stays exactly under the cursor however hard you zoom.
    c = _cam()
    eye, d = mv.cursor_ray(c, 520, 180, 640, 480)
    forward = c.basis()[3]
    anchor_t = 240.0  # a surface nearer than the pivot plane
    anchor = eye + d * anchor_t
    target, distance = mv.dolly_to_ray(eye, forward, c.distance, d, 0.3, 1.0, anchor_t)
    new_eye = target - forward * distance
    v = anchor - new_eye
    assert np.linalg.norm(np.cross(v, d)) < 1e-6  # still on the same ray
    assert float(v @ d) > 0  # still in front


def test_preset_angles_face_the_axes():
    top = mv.Camera()
    top.yaw, top.pitch, top.ortho = mv.PRESETS["top"]
    assert top.ortho and np.allclose(top.basis()[3], [0, 0, -1], atol=1e-6)  # straight down
    front = mv.Camera()
    front.yaw, front.pitch, front.ortho = mv.PRESETS["front"]
    assert front.ortho and np.allclose(front.basis()[3], [0, 1, 0], atol=1e-6)
    side = mv.Camera()
    side.yaw, side.pitch, side.ortho = mv.PRESETS["side"]
    assert side.ortho and np.allclose(side.basis()[3], [1, 0, 0], atol=1e-6)
    assert mv.PRESETS["perspective"][2] is False


def test_pitch_clamp_prevents_the_flip():
    c = mv.Camera()
    c.set_pitch(math.radians(200.0))  # dragged way past straight up
    assert c.pitch == mv.PITCH_LIMIT  # clamped, not wrapped round to a flipped view
    c.set_pitch(-math.radians(200.0))
    assert c.pitch == -mv.PITCH_LIMIT
    # the view direction still points down from a positive pitch and never flips over the top
    c.set_pitch(mv.PITCH_LIMIT)
    assert c.basis()[3][2] < 0  # looking downward, up is still up


def test_ortho_zoom_keeps_point_under_cursor():
    c = _cam()
    c.ortho = True
    c.ortho_half_h = 512.0
    px, py, w, h = 540, 170, 640, 480
    before, _d = mv.cursor_ray(c, px, py, w, h)  # world point under that pixel
    c.target, c.ortho_half_h = mv.dolly_ortho(c, px, py, w, h, 0.5, 1.0)
    after, _d = mv.cursor_ray(c, px, py, w, h)
    assert c.ortho_half_h == 256.0  # zoomed in by the factor
    assert np.allclose(before, after, atol=1e-6)  # the cursor's world point did not move


def test_ortho_half_for_fits_width_and_height():
    assert mv.ortho_half_for(100.0, 1.0) >= 100.0
    # a viewport narrower than it is tall needs more world half-height to fit the width
    assert mv.ortho_half_for(100.0, 0.5) > mv.ortho_half_for(100.0, 1.0)


def test_ray_mesh_hit_cube_top_face():
    pos = np.array([[x, y, z] for z in (0, 64) for y in (0, 64) for x in (0, 64)], np.float32)
    quads = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
    tris = np.array([t for a, b, c, d in quads for t in ((a, b, c), (a, c, d))])
    hit = mv.ray_mesh_hit(np.array([32.0, 32.0, 200.0]), np.array([0.0, 0.0, -1.0]), pos, tris)
    assert hit is not None
    assert math.isclose(hit[2], 64.0, abs_tol=1e-4)
    away = mv.ray_mesh_hit(np.array([32.0, 32.0, 200.0]), np.array([0.0, 0.0, 1.0]), pos, tris)
    assert away is None  # a ray pointing away from the cube never meets it
