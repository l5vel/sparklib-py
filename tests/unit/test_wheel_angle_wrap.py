"""Steer error across the wrap seam, on cases taken from a live rig.

Every case is (raw reading, recorded offset, target) with the error a person
worked out by hand. They exist because the seam at 180 degrees is where this
arithmetic goes wrong quietly: the loop still converges, to an angle most of a
turn away from the one commanded.

The error is two calls, and pinning them together is the point. Wrapping the
wheel angle and then taking the shortest error has to give the same answer as
wrapping once at the end.
"""

import pytest

from sparklib import cancoder, steer

# (raw_deg, offset_deg, target_deg, expected_error_deg, what it is)
CASES = [
    (0.0, 0.0, 0.0, 0.0, "everything at zero"),
    (90.0, 0.0, 0.0, -90.0, "a quarter turn past the target"),
    (-90.0, 0.0, 0.0, 90.0, "a quarter turn short of it"),
    (179.0, 0.0, 0.0, -179.0, "just inside the seam"),
    (-179.0, 0.0, 0.0, 179.0, "just the other side of it"),
    (179.0, 0.0, -179.0, 2.0, "two degrees, straight across the seam"),
    (142.21, 51.2, 0.0, -91.01, "a corner with its recorded offset"),
    (142.21, 141.85, 0.0, -0.36, "the same corner with the offset it should have"),
    (50.0, -170.0, 0.0, 140.0, "an offset sitting near the seam"),
    (-170.0, 170.0, 0.0, -20.0, "reading and offset on opposite sides of it"),
]


@pytest.mark.parametrize("raw,offset,target,expected,label",
                         CASES, ids=[c[4] for c in CASES])
def test_the_error_a_steer_loop_should_see(raw, offset, target, expected, label):
    wheel = cancoder.wheel_angle_deg(raw, offset)
    assert steer.shortest_error_deg(target, wheel) == pytest.approx(expected, abs=0.01)


@pytest.mark.parametrize("raw,offset,target,expected,label",
                         CASES, ids=[c[4] for c in CASES])
def test_wrapping_twice_agrees_with_wrapping_once(raw, offset, target, expected, label):
    """The property the cases above are examples of."""
    once = steer.normalize_deg(target - (raw - offset))
    twice = steer.shortest_error_deg(target, cancoder.wheel_angle_deg(raw, offset))
    assert twice == pytest.approx(once, abs=1e-9)


@pytest.mark.parametrize("raw", [-540, -181, -180, -0.0, 0.0, 180, 181, 540])
def test_a_wheel_angle_always_lands_inside_one_turn(raw):
    assert -180.0 < cancoder.wheel_angle_deg(raw, 0.0) <= 180.0


@pytest.mark.parametrize("value", [180.0, -180.0, 540.0, -540.0])
def test_both_wrappers_close_the_seam_at_the_same_end(value):
    """Half a turn reads +180 from both, or a wheel there takes the long way."""
    assert steer.normalize_deg(value) == 180.0
    assert cancoder.wheel_angle_deg(value, 0.0) == 180.0


def test_the_error_never_asks_for_the_long_way_round():
    for target in range(-360, 361, 13):
        for raw in range(-360, 361, 17):
            error = steer.shortest_error_deg(target, cancoder.wheel_angle_deg(raw, 0.0))
            assert -180.0 < error <= 180.0, (target, raw)
