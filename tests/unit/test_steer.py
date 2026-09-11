"""The host-side steer primitives, including the rule that can deadlock.

These are the arithmetic a steer loop runs every tick, so a wrong branch here
is a wheel that hunts, stalls just short of its target, or accepts a position it
never reached.
"""

from collections import deque

import pytest

from sparklib import steer


def window(values, size):
    d = deque(maxlen=size)
    for v in values:
        d.append(v)
    return d


@pytest.mark.parametrize("value,expected", [
    (0.0, 0.0), (90.0, 90.0), (-90.0, -90.0),
    (190.0, -170.0), (-190.0, 170.0),
    (360.0, 0.0), (720.0, 0.0), (-360.0, 0.0),
    (540.0, 180.0), (180.0, 180.0), (-180.0, 180.0),
])
def test_an_angle_wraps_to_one_turn(value, expected):
    assert steer.normalize_deg(value) == pytest.approx(expected)


@pytest.mark.parametrize("turns", [-3, -1, 1, 2])
def test_adding_whole_turns_changes_nothing(turns):
    for base in (-179.0, -45.0, 0.0, 45.0, 179.0):
        assert steer.normalize_deg(base + 360.0 * turns) == pytest.approx(base)


def test_the_shortest_error_goes_the_short_way():
    assert steer.shortest_error_deg(170.0, -170.0) == pytest.approx(-20.0)
    assert steer.shortest_error_deg(-170.0, 170.0) == pytest.approx(20.0)


def test_output_inside_the_deadband_is_zero():
    assert steer.p_output(0.4, kp=0.01, max_output=0.4, deadband_deg=0.5) == 0.0


def test_output_outside_the_deadband_follows_the_error():
    assert steer.p_output(10.0, kp=0.01, max_output=0.4) == pytest.approx(0.1)
    assert steer.p_output(-10.0, kp=0.01, max_output=0.4) == pytest.approx(-0.1)


def test_output_is_capped_in_both_directions():
    assert steer.p_output(500.0, kp=0.01, max_output=0.4) == pytest.approx(0.4)
    assert steer.p_output(-500.0, kp=0.01, max_output=0.4) == pytest.approx(-0.4)


def test_a_gain_closure_matches_calling_the_function():
    controller = steer.p_controller(kp=0.008, max_output=0.35, deadband_deg=0.5)
    for err in (-90.0, -0.3, 0.0, 0.3, 12.0, 400.0):
        assert controller(err) == steer.p_output(err, 0.008, 0.35, 0.5)


def test_two_closures_keep_their_own_gains():
    """The reason the closure exists: one run, two manoeuvres, two gain sets."""
    coarse = steer.p_controller(0.010, 0.45)
    fine = steer.p_controller(0.004, 0.20)
    assert coarse(10.0) == pytest.approx(0.10)
    assert fine(10.0) == pytest.approx(0.04)


def test_the_derivative_term_opposes_a_closing_error():
    approaching = steer.pd_output(10.0, -50.0, kp=0.01, kd=0.001, max_output=0.4)
    proportional = steer.p_output(10.0, kp=0.01, max_output=0.4)
    assert approaching < proportional


def test_the_pd_output_respects_the_deadband_and_the_cap():
    assert steer.pd_output(0.2, 0.0, 0.01, 0.001, 0.4, deadband_deg=0.5) == 0.0
    assert steer.pd_output(9e9, 0.0, 0.01, 0.001, 0.4) == pytest.approx(0.4)


def test_slew_limits_how_fast_output_can_change():
    assert steer.slew(0.0, 1.0, max_step=0.04) == pytest.approx(0.04)
    assert steer.slew(0.0, -1.0, max_step=0.04) == pytest.approx(-0.04)


def test_slew_arrives_when_the_gap_is_small():
    assert steer.slew(0.38, 0.40, max_step=0.04) == pytest.approx(0.40)


def test_the_tolerance_rule_accepts_a_small_error():
    assert steer.settled(deque(maxlen=5), 0.4, "tol", tol_deg=1.0) is True
    assert steer.settled(deque(maxlen=5), 4.0, "tol", tol_deg=1.0) is False


def test_the_static_rule_waits_for_a_full_window():
    half = window([10.0, 10.0], 5)
    assert steer.settled(half, 0.0, "static", motion_deg=0.15) is False


def test_the_static_rule_accepts_a_wheel_that_stopped_moving():
    still = window([10.0, 10.02, 10.01, 10.0, 10.01], 5)
    assert steer.settled(still, 90.0, "static", motion_deg=0.15) is True


def test_the_static_rule_rejects_a_wheel_still_turning():
    moving = window([10.0, 12.0, 14.0, 16.0, 18.0], 5)
    assert steer.settled(moving, 0.0, "static", motion_deg=0.15) is False


def test_the_static_rule_survives_the_deadband_deadlock():
    """A deadband wider than the tolerance stops the error ever getting small.

    The loop commands zero, the wheel stops, and the tolerance rule waits for an
    error that nothing is left to reduce. The static rule accepts it.
    """
    still = window([10.0] * 5, 5)
    assert steer.settled(still, 2.0, "tol", tol_deg=1.0) is False
    assert steer.settled(still, 2.0, "static", motion_deg=0.15) is True


def test_either_accepts_on_tolerance_before_the_window_fills():
    """An axis already on target must not wait out a window it never needed."""
    half = window([10.0, 10.0], 5)
    assert steer.settled(half, 0.2, "either", tol_deg=1.0) is True
    assert steer.settled(half, 9.0, "either", tol_deg=1.0) is False


def test_either_accepts_on_stillness_once_the_window_fills():
    still = window([10.0] * 5, 5)
    assert steer.settled(still, 90.0, "either", motion_deg=0.15, tol_deg=1.0) is True


def test_an_unknown_settle_mode_is_refused():
    """Silently treating a typo as `either` accepts positions never reached."""
    with pytest.raises(ValueError, match="unknown settle mode"):
        steer.settled(deque(maxlen=3), 0.0, "statc")


def test_an_unbounded_history_never_satisfies_the_static_rule():
    assert steer.settled(deque([1.0, 1.0, 1.0]), 90.0, "static") is False


def test_scoring_an_empty_run_returns_no_numbers():
    result = steer.score_step([], 45.0, 0.5)
    assert all(v is None for v in result.values())


def test_a_clean_step_scores_better_than_one_that_overshoots():
    clean = [(t / 10.0, 45.0 - 45.0 * (0.7 ** t), 0.3) for t in range(20)]
    wild = [(t / 10.0, 45.0 + 20.0 * (-0.8) ** t, 0.4) for t in range(20)]
    assert steer.score_step(clean, 45.0, 0.5)["cost"] < \
        steer.score_step(wild, 45.0, 0.5)["cost"]


def test_overshoot_is_measured_past_the_target():
    past = [(0.0, 0.0, 0.4), (0.1, 40.0, 0.4), (0.2, 52.0, 0.1), (0.3, 45.0, 0.0)]
    assert steer.score_step(past, 45.0, 0.5)["overshoot_deg"] == pytest.approx(7.0)


def test_a_run_that_never_arrives_reports_no_settle_time():
    stuck = [(t / 10.0, 5.0, 0.4) for t in range(10)]
    assert steer.score_step(stuck, 45.0, 0.5)["settle_s"] is None


def test_time_spent_at_the_output_ceiling_is_reported():
    saturated = [(t / 10.0, t, 0.4) for t in range(10)]
    assert steer.score_step(saturated, 45.0, 0.5)["saturated_frac"] == pytest.approx(1.0)
