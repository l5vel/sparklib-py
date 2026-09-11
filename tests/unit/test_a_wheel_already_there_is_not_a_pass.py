"""A run that starts inside tolerance proves nothing about the loop.

Hardware found this. The same corner was commanded to +45 twice. The first run
moved it 53 deg and settled in 0.85 s, which is a result. The second started
2.46 deg away, moved nothing, reported a settling time of 0.00 s and returned
PASS. Converging from nowhere is not convergence.
"""

import importlib.util
import pathlib

import pytest

TOOL = (pathlib.Path(__file__).resolve().parents[2]
        / "tools" / "steer_pid_check.py")


@pytest.fixture(scope="module")
def spc():
    spec = importlib.util.spec_from_file_location("spc", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def samples(errors, duty=0.2):
    """(t, angle, err, duty) rows, which is the shape report() scores."""
    return [(i * 0.05, 45.0, e, duty) for i, e in enumerate(errors)]


def test_a_wheel_that_began_on_target_is_inconclusive(spc, capsys):
    code = spc.report(samples([2.4] * 80), 45.0, 3.0)
    out = capsys.readouterr().out
    assert "INCONCLUSIVE" in out
    assert code != 0


def test_the_inconclusive_verdict_says_what_to_do_instead(spc, capsys):
    spc.report(samples([2.4] * 80), 45.0, 3.0)
    out = capsys.readouterr().out
    assert "--target-deg" in out, "an inconclusive run must name a usable re-run"


def test_a_real_move_that_converges_still_passes(spc, capsys):
    errors = [max(2.4, 53.0 - i * 3.0) for i in range(80)]
    code = spc.report(samples(errors), 45.0, 3.0)
    assert "PASS" in capsys.readouterr().out
    assert code == 0


def test_a_real_move_that_never_arrives_still_fails(spc, capsys):
    code = spc.report(samples([53.0] * 80), 45.0, 3.0)
    out = capsys.readouterr().out
    assert "FAIL" in out
    assert code != 0


def test_an_oscillating_move_still_fails(spc, capsys):
    errors = [40.0 * (-0.95) ** i for i in range(80)]
    code = spc.report(samples(errors), 45.0, 3.0)
    assert "FAIL" in capsys.readouterr().out
    assert code != 0


@pytest.mark.parametrize("first_error,verdict", [
    (0.0, "INCONCLUSIVE"),
    (2.99, "INCONCLUSIVE"),
    (3.01, "PASS"),
    (53.0, "PASS"),
])
def test_the_boundary_is_the_tolerance(spc, capsys, first_error, verdict):
    """Inside the tolerance it had nowhere to go; outside it, it had to move."""
    errors = [first_error] + [2.4] * 79
    spc.report(samples(errors), 45.0, 3.0)
    assert verdict in capsys.readouterr().out
