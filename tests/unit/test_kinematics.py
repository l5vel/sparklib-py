"""Swerve kinematics, checked against motion a person can picture.

Every case here states the chassis command in words first, because the failure
mode this math has is a robot that moves smoothly in the wrong direction, and a
test that restates the formula would agree with a sign error.
"""

import math

import pytest

from sparklib import kinematics as kin
from sparklib import steer

GEOM = kin.module_geometry(track_width=0.5, wheel_base=0.6)
MAX_SPEED = 3.0


def deg(rad):
    return math.degrees(rad)


def test_the_four_corners_sit_where_a_rectangle_puts_them():
    assert GEOM["LF"] == (0.25, 0.3)
    assert GEOM["RF"] == (-0.25, 0.3)
    assert GEOM["LB"] == (0.25, -0.3)
    assert GEOM["RB"] == (-0.25, -0.3)


def test_a_custom_corner_set_drops_the_rest():
    three = kin.module_geometry(0.5, 0.6, corners=("LF", "RF", "LB"))
    assert set(three) == {"LF", "RF", "LB"}


def test_driving_straight_forward_points_every_wheel_at_zero():
    states = kin.module_states(1.5, 0.0, 0.0, GEOM, MAX_SPEED)
    for label, (speed, angle) in states.items():
        assert deg(angle) == pytest.approx(0.0), label
        assert speed == pytest.approx(0.5), label


def test_strafing_right_points_every_wheel_at_ninety():
    states = kin.module_states(0.0, 1.5, 0.0, GEOM, MAX_SPEED)
    for label, (speed, angle) in states.items():
        assert deg(angle) == pytest.approx(90.0), label
        assert speed == pytest.approx(0.5), label


def test_driving_backwards_points_every_wheel_at_a_half_turn():
    states = kin.module_states(-1.5, 0.0, 0.0, GEOM, MAX_SPEED)
    for label, (_, angle) in states.items():
        assert abs(deg(angle)) == pytest.approx(180.0), label


def test_a_forward_left_diagonal_points_every_wheel_the_same_way():
    states = kin.module_states(1.0, -1.0, 0.0, GEOM, MAX_SPEED)
    angles = {deg(a) for _, a in states.values()}
    assert len(angles) == 1
    assert angles.pop() == pytest.approx(-45.0)


def test_spinning_in_place_turns_the_wheels_tangent_to_the_circle():
    """Every corner faces perpendicular to its own radius, and no two agree."""
    states = kin.module_states(0.0, 0.0, 1.0, GEOM, MAX_SPEED)
    for label, (_, angle) in states.items():
        x, y = GEOM[label]
        radius = math.atan2(x, y)
        assert abs(deg(steer.normalize_rad(angle - radius))) == pytest.approx(90.0), label
    assert len({round(deg(a), 3) for _, a in states.values()}) == 4


def test_spinning_in_place_gives_all_four_corners_the_same_speed():
    states = kin.module_states(0.0, 0.0, 1.0, GEOM, MAX_SPEED)
    speeds = {round(s, 9) for s, _ in states.values()}
    assert len(speeds) == 1


def test_a_stopped_chassis_holds_the_angles_it_was_given():
    held = {"LF": 1.0, "RF": -2.0, "LB": 0.5, "RB": 3.0}
    states = kin.module_states(0.0, 0.0, 0.0, GEOM, MAX_SPEED, hold_angles=held)
    for label, (speed, angle) in states.items():
        assert speed == 0.0
        assert angle == held[label]


def test_a_stopped_chassis_with_no_history_asks_for_zero():
    states = kin.module_states(0.0, 0.0, 0.0, GEOM, MAX_SPEED)
    assert all(a == 0.0 for _, a in states.values())


def test_desaturate_leaves_a_command_that_already_fits():
    states = {"LF": (0.4, 0.0), "RF": (0.9, 0.0)}
    assert kin.desaturate(states) == states


def test_desaturate_scales_the_whole_set_when_one_wheel_clips():
    states = {"LF": (2.0, 0.1), "RF": (1.0, 0.2), "LB": (0.5, 0.3), "RB": (0.0, 0.4)}
    out = kin.desaturate(states)
    assert out["LF"][0] == pytest.approx(1.0)
    assert out["RF"][0] == pytest.approx(0.5)
    assert out["LB"][0] == pytest.approx(0.25)
    assert out["RB"][0] == pytest.approx(0.0)


def test_desaturate_keeps_every_angle_untouched():
    states = {"LF": (2.0, 0.1), "RF": (1.0, 0.2)}
    out = kin.desaturate(states)
    assert [a for _, a in out.values()] == [0.1, 0.2]


def test_desaturate_preserves_the_ratio_that_clamping_would_destroy():
    """The whole reason this function exists, stated as the comparison."""
    states = {"LF": (1.6, 0.0), "RF": (0.8, 0.0)}
    clamped = {m: (min(1.0, s), a) for m, (s, a) in states.items()}
    scaled = kin.desaturate(states)

    assert clamped["LF"][0] / clamped["RF"][0] == pytest.approx(1.25)
    assert scaled["LF"][0] / scaled["RF"][0] == pytest.approx(2.0)
    assert states["LF"][0] / states["RF"][0] == pytest.approx(2.0)


def test_desaturate_handles_a_reversed_wheel_by_magnitude():
    out = kin.desaturate({"LF": (-2.0, 0.0), "RF": (1.0, 0.0)})
    assert out["LF"][0] == pytest.approx(-1.0)
    assert out["RF"][0] == pytest.approx(0.5)


def test_desaturate_on_an_empty_set_returns_an_empty_set():
    assert kin.desaturate({}) == {}


def test_a_small_turn_is_taken_directly():
    speed, target, error = kin.optimize(math.radians(30), math.radians(0), 0.6)
    assert speed == pytest.approx(0.6)
    assert deg(target) == pytest.approx(30.0)
    assert deg(error) == pytest.approx(30.0)


def test_a_three_quarter_turn_becomes_a_quarter_turn_backwards():
    speed, target, error = kin.optimize(math.radians(170), math.radians(0), 0.6)
    assert speed == pytest.approx(-0.6)
    assert deg(target) == pytest.approx(-10.0)
    assert abs(deg(error)) == pytest.approx(10.0)


def test_optimize_never_asks_for_more_than_a_quarter_turn():
    for target in range(-180, 181, 5):
        for measured in range(-180, 181, 15):
            _, _, error = kin.optimize(math.radians(target),
                                       math.radians(measured), 1.0)
            assert abs(deg(error)) <= 90.0 + 1e-9, (target, measured)


def test_optimize_reaches_the_same_wheel_line_it_was_asked_for():
    """A reversed module still points along the commanded line of travel."""
    for target in range(-180, 181, 7):
        speed, out, _ = kin.optimize(math.radians(target), math.radians(140), 1.0)
        gap = abs(deg(steer.normalize_rad(out - math.radians(target))))
        assert gap == pytest.approx(0.0 if speed > 0 else 180.0, abs=1e-6)


def test_optimize_compares_against_the_measured_angle_not_the_command():
    """Two modules on one command must not resolve to opposite headings."""
    command = math.radians(179)
    a_speed, a_target, _ = kin.optimize(command, math.radians(175), 1.0)
    b_speed, b_target, _ = kin.optimize(command, math.radians(178), 1.0)
    assert a_speed == b_speed
    assert a_target == pytest.approx(b_target)


def test_the_stick_centre_is_quiet():
    assert kin.chassis_from_stick(0.0, 0.0, 0.0, 3.0, 6.0) == (0.0, 0.0, 0.0)


def test_a_nudge_inside_the_deadband_is_quiet():
    assert kin.chassis_from_stick(0.05, 0.0, 0.02, 3.0, 6.0) == (0.0, 0.0, 0.0)


def test_a_full_forward_push_asks_for_full_speed():
    forward, strafe, rotate = kin.chassis_from_stick(0.0, 1.0, 0.0, 3.0, 6.0)
    assert forward == pytest.approx(3.0)
    assert strafe == pytest.approx(0.0)
    assert rotate == pytest.approx(0.0)


def test_a_full_diagonal_push_keeps_its_direction():
    """The deadband applies to the vector, so a diagonal stays at 45 degrees."""
    v = 1.0 / math.sqrt(2)
    forward, strafe, _ = kin.chassis_from_stick(v, v, 0.0, 3.0, 6.0)
    assert deg(math.atan2(strafe, forward)) == pytest.approx(45.0)


def test_a_clockwise_twist_asks_for_a_negative_rotation_rate():
    _, _, rotate = kin.chassis_from_stick(0.0, 0.0, 1.0, 3.0, 6.0)
    assert rotate == pytest.approx(-6.0)


def test_expo_softens_the_centre_and_keeps_the_ends():
    soft = kin.chassis_from_stick(0.0, 0.5, 0.0, 3.0, 6.0, expo=0.5)[0]
    hard = kin.chassis_from_stick(0.0, 0.5, 0.0, 3.0, 6.0, expo=0.0)[0]
    assert soft < hard
    full_soft = kin.chassis_from_stick(0.0, 1.0, 0.0, 3.0, 6.0, expo=0.5)[0]
    assert full_soft == pytest.approx(3.0)


def test_a_stick_command_survives_the_whole_chain():
    """Joystick to per-module output, the path docs/INTEGRATION.md describes."""
    forward, strafe, rotate = kin.chassis_from_stick(0.3, 0.9, 0.4, 3.0, 6.0)
    states = kin.desaturate(
        kin.module_states(forward, strafe, rotate, GEOM, MAX_SPEED))
    assert set(states) == set(GEOM)
    for label, (speed, angle) in states.items():
        assert -1.0 <= speed <= 1.0, label
        assert -math.pi <= angle <= math.pi, label
