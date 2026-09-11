"""The anomaly detector, checked against the motion each rule is meant to name.

Every case drives the detector with a sequence a real corner could produce, so
a rule that fires on the wrong thing shows up as a wrong verdict here.
"""

import pytest

from sparklib.trace import CornerTrace


def collect(**kw):
    """A trace that records its events instead of printing them."""
    lines = []
    return CornerTrace("LF", out=lines.append, **kw), lines


def tick(trace, raw_rev=0.0, err=0.0, cmd=0.0, applied=0.0, unit="%"):
    trace.sample(raw_rev, raw_rev * 360.0, raw_rev * 360.0, err, cmd, unit, applied)


def kinds(trace):
    return [k for k, _ in trace.events]


def test_a_steady_corner_reports_nothing():
    trace, _ = collect()
    for _ in range(20):
        tick(trace, raw_rev=0.1, err=0.2, cmd=0.05, applied=0.05)
    assert trace.events == []


def test_an_encoder_that_leaps_reports_a_jump():
    trace, _ = collect()
    tick(trace, raw_rev=0.0)
    tick(trace, raw_rev=0.1)
    assert kinds(trace) == ["JUMP"]


def test_a_move_across_the_wrap_point_is_not_a_jump():
    """Half a degree that crosses zero must not read as most of a turn."""
    trace, _ = collect()
    tick(trace, raw_rev=0.4993)
    tick(trace, raw_rev=-0.4993)
    assert trace.events == []


def test_a_fast_slew_is_judged_against_a_wider_budget():
    """At full duty the wheel genuinely covers ground in one tick."""
    fixed, _ = collect()
    tick(fixed, raw_rev=0.0, cmd=1.0)
    tick(fixed, raw_rev=0.03, cmd=1.0)
    assert kinds(fixed) == ["JUMP"]

    scaled, _ = collect(gear_ratio=26.0, max_motor_rpm=6784.0)
    tick(scaled, raw_rev=0.0, cmd=1.0)
    tick(scaled, raw_rev=0.03, cmd=1.0)
    assert scaled.events == []


def test_the_wider_budget_still_catches_a_real_leap():
    trace, _ = collect(gear_ratio=26.0, max_motor_rpm=6784.0)
    tick(trace, raw_rev=0.0, cmd=1.0)
    tick(trace, raw_rev=0.5, cmd=1.0)
    assert kinds(trace) == ["JUMP"]


def test_a_command_in_other_units_falls_back_to_the_floor():
    trace, _ = collect(gear_ratio=26.0, max_motor_rpm=6784.0)
    tick(trace, raw_rev=0.0, cmd=1.0, unit="rev")
    tick(trace, raw_rev=0.03, cmd=1.0, unit="rev")
    assert kinds(trace) == ["JUMP"]


def test_output_opposing_the_command_reports_a_fight():
    trace, _ = collect()
    for _ in range(3):
        trace._t0 -= 1.0
        tick(trace, cmd=0.3, applied=-0.3)
    assert "FIGHT" in kinds(trace)


def test_agreeing_output_reports_no_fight():
    trace, _ = collect()
    for _ in range(10):
        trace._t0 -= 0.5
        tick(trace, cmd=0.3, applied=0.3)
    assert trace.events == []


def test_an_unreported_applied_output_reports_no_fight():
    """A controller whose frame has not landed is unknown, not fighting."""
    trace, _ = collect()
    for _ in range(10):
        trace._t0 -= 0.5
        tick(trace, cmd=0.3, applied=None)
    assert trace.events == []


def test_an_error_that_keeps_growing_reports_divergence():
    trace, _ = collect()
    for i in range(8):
        trace._t0 -= 0.1
        tick(trace, err=1.0 + i * 1.5)
    assert "DIVERGE" in kinds(trace)


def test_an_error_that_shrinks_reports_nothing():
    trace, _ = collect()
    for i in range(8):
        trace._t0 -= 0.1
        tick(trace, err=12.0 - i * 1.5)
    assert trace.events == []


def test_a_new_target_clears_the_divergence_baseline():
    """The error step a retarget produces is the move, not a failure."""
    trace, _ = collect()
    for i in range(8):
        trace._t0 -= 0.1
        tick(trace, err=1.0 + i * 1.5)
    assert "DIVERGE" in kinds(trace)

    fresh, _ = collect()
    for i in range(4):
        fresh._t0 -= 0.1
        tick(fresh, err=1.0 + i * 0.2)
        fresh.set_target(90.0)
    assert fresh.events == []


def test_the_buffer_keeps_the_most_recent_samples():
    trace, _ = collect()
    for i in range(400):
        tick(trace, raw_rev=0.0, err=0.0)
    assert len(trace.buf) == 256
    assert trace.latest()[4] == 0.0


def test_latest_is_empty_before_the_first_sample():
    trace, _ = collect()
    assert trace.latest() is None


def test_dump_tail_prints_what_it_has():
    trace, lines = collect()
    tick(trace, raw_rev=0.1, applied=None)
    lines.clear()
    trace.dump_tail(n=5)
    assert len(lines) == 2
    assert "None" in lines[1]


@pytest.mark.parametrize("verbose,expected", [(False, 0), (True, 2)])
def test_verbose_prints_every_tick(verbose, expected):
    trace, lines = collect(verbose=verbose)
    tick(trace)
    tick(trace)
    assert len(lines) == expected
