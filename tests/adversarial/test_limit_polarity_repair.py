"""The one tool allowed to write a hard-limit parameter, and its guard rails.

`spark_admin.write_param` refuses parameters 50-53 outright, because the
data-port hard limits are a safety interlock. `tools/spark_limit_polarity_repair.py`
makes one narrow exception: parameters 50 and 51 only, values 0 or 1 only, never
52 or 53, and never while an enable heartbeat is on the bus.

Until the tool wrote blind. The write echo was the only instrument,
and on firmware 26.1.6 that echo is not trustworthy: a BOOL parameter written 2
returns Success and echoes 2, and the read shows the firmware stores the 2. The
tool now READS the parameter back after every write and refuses to report success
on a mismatch. These tests exist because that guard arrived with no test, which
is how a guard that never fires gets shipped.

The polarity mechanism itself is modelled here: with `unwired_limit_inputs` the
simulated controller reads its limit as REACHED exactly when the polarity says
normally-open, which is what rig-flex's steers do on hardware.
"""

from __future__ import annotations

import importlib.util
import pathlib

import pytest

from sparksim import attach, spark
from sparksim import frames as F
from sparksim.faults import SparkBehaviour
from sparksim.fleet import BASE02_FIRMWARE

_SPEC = importlib.util.spec_from_file_location(
    "spark_limit_polarity_repair",
    pathlib.Path(__file__).resolve().parents[2]
    / "tools" / "spark_limit_polarity_repair.py")
repair = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(repair)

DEV = 17
ROLES = {DEV: "steer/LB"}


def _unwired(**kw):
    """A steer controller whose data-port inputs are not wired to anything."""
    behaviour = SparkBehaviour(unwired_limit_inputs=True, **kw)
    return spark(DEV, ram={50: 0, 51: 0, 52: 1, 53: 1}, behaviour=behaviour)


# -- the narrow exception stays narrow ----------------------------------------

@pytest.mark.parametrize("param_id", [0, 2, 52, 53, 59, 158, 159])
def test_the_tool_writes_no_parameter_but_50_and_51(sim, param_id):
    """52 and 53 are the hard-limit ENABLES. Disabling a limit is never a repair,
    and the tool must not be able to reach them however it is called."""
    adm = attach(sim([_unwired()]))
    with pytest.raises(ValueError):
        repair.write_protected(adm, DEV, param_id, 0)


@pytest.mark.parametrize("value", [-1, 2, 3, 255])
def test_the_tool_writes_no_value_but_0_and_1(sim, value):
    """26.1.6 accepts and STORES a BOOL outside 0/1, and the stored value behaves
    as the permissive one, so the refusal has to happen in the driver."""
    adm = attach(sim([_unwired()]))
    with pytest.raises(ValueError):
        repair.write_protected(adm, DEV, 50, value)


def test_a_refused_write_puts_no_frame_on_the_bus(sim):
    """Raising after the send would be a write the caller believes was refused."""
    bus = sim([_unwired()])
    adm = attach(bus)
    before = len(bus.sent)
    for bad in ((52, 0), (50, 2)):
        with pytest.raises(ValueError):
            repair.write_protected(adm, DEV, *bad)
    assert not bus.sent[before:], (
        f"a refused write reached the bus: "
        f"{[hex(m.arbitration_id) for m in bus.sent[before:]]}")


# -- the read-back guard, which is what added ----------------------

def test_a_polarity_write_is_read_back(sim):
    """The echo is not the instrument. The parameter is."""
    bus = sim([_unwired()])
    adm = attach(bus)
    repair.set_polarity(adm, DEV, ROLES)
    reads = [m for m in bus.sent
             if F.READ_PARAM_BASE <= F.base_of(m.arbitration_id) <= F.READ_PARAM_LAST]
    assert len(reads) >= 2, (
        "each of parameters 50 and 51 has to be read back after it is written; "
        f"only {len(reads)} read frame(s) went out")


def test_a_write_that_reports_success_and_did_not_land_is_refused(sim):
    """CD 456184's shape, and the reason the read-back exists. The controller
    answers Success and echoes the value asked for, and holds the old one."""
    bus = sim([_unwired(ignore_writes_for=frozenset({50}))])
    adm = attach(bus)
    assert repair.set_polarity(adm, DEV, ROLES, invert=True) is None, (
        "a write that reported Success without landing was accepted; the "
        "read-back guard did not fire")


def test_a_write_that_landed_is_reported_as_landed(sim):
    """The control for the test above: the same path must not refuse a good write."""
    bus = sim([_unwired()])
    adm = attach(bus)
    assert repair.set_polarity(adm, DEV, ROLES) is not None


# -- the mechanism: polarity raises and clears the limit ----------------------

def test_writing_polarity_1_asserts_both_hard_limits(sim):
    """Measured on rig-flex id 17, with the motors disabled: an
    unwired input rests high, so polarity 1 reads REACHED on both directions."""
    bus = sim([_unwired()])
    adm = attach(bus)
    assert repair.blocked_now(adm, ROLES, seconds=0.4) == {}
    hit = repair.set_polarity(adm, DEV, ROLES, invert=True)
    assert sorted(hit or []) == ["FWD", "REV"], (
        f"polarity 1 did not assert both limits: {hit}\n" + bus.explain(DEV))


def test_writing_polarity_0_clears_them_again(sim):
    """The half that matters more. Asserting a limit is fail-safe; releasing it
    is the direction that has to be proved rather than assumed."""
    bus = sim([_unwired()])
    adm = attach(bus)
    repair.set_polarity(adm, DEV, ROLES, invert=True)
    assert repair.set_polarity(adm, DEV, ROLES) == [], (
        "the limits did not clear when the polarity went back to declared\n"
        + bus.explain(DEV))
    assert repair.polarity_now(adm, DEV, 50) == 0
    assert repair.polarity_now(adm, DEV, 51) == 0


def test_the_hard_limit_enables_are_untouched_throughout(sim):
    """52 and 53 stay 1 for the whole cycle. A tool that cleared them would make
    the wheel move freely and look like it had fixed something."""
    bus = sim([_unwired()])
    ctrl = bus.controllers[0]
    adm = attach(bus)
    repair.set_polarity(adm, DEV, ROLES, invert=True)
    repair.set_polarity(adm, DEV, ROLES)
    assert ctrl.ram[52] == 1 and ctrl.ram[53] == 1
    assert not [w for w in ctrl.write_log if w.param_id in (52, 53)], (
        "the hard-limit enables were written by the polarity tool")


# -- the generation split, which this repo keeps getting wrong ----------------

def _unwired_pre25():
    """The same controller on 24.0.1, which carries none of the 25+ frames."""
    return spark(DEV, firmware=BASE02_FIRMWARE,
                 ram={50: 0, 51: 0, 52: 1, 53: 1},
                 behaviour=SparkBehaviour(unwired_limit_inputs=True))


def test_the_pre25_write_uses_the_pre25_dialect(sim):
    """PARAMETER_WRITE is versionImplemented 25.0.0, so a 24.0.1 device drops it
    in silence and the tool would report 'no response' for a live controller.
    That is the mistake that made this fleet look unwritable for a day."""
    bus = sim([_unwired_pre25()])
    adm = attach(bus)
    before = len(bus.sent)
    assert repair.set_polarity(adm, DEV, ROLES, invert=True, pre25=True) is not None
    modern = [m for m in bus.sent[before:]
              if F.base_of(m.arbitration_id) == F.PARAM_WRITE]
    assert not modern, (
        f"{len(modern)} firmware-25 PARAMETER_WRITE frame(s) went to a 24.0.1 "
        "controller, which carries none of them")


def test_the_pre25_read_back_uses_the_pre25_dialect(sim):
    """READ_PARAMETER is 25.0.0 too, so the read-back has to route by generation
    or the guard silently never confirms anything on a MAX."""
    bus = sim([_unwired_pre25()])
    adm = attach(bus)
    assert repair.polarity_now(adm, DEV, 50, pre25=True) == 0
    modern = [m for m in bus.sent
              if F.READ_PARAM_BASE <= F.base_of(m.arbitration_id) <= F.READ_PARAM_LAST]
    assert not modern, (
        "a firmware-25 READ_PARAMETER frame was sent to a 24.0.1 controller")
