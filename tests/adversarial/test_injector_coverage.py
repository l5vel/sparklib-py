"""Injectors that nothing depended on, given something to depend on them.

A mutation sweep neutered each of the simulator's 24 failure
injectors in turn and re-ran the tests that reference it. Twenty-one were
caught. Three were not:

    congestion       the METHOD was never called -- every test used the
                     SparkBusSim(congestion=...) constructor kwarg instead,
                     which cannot express a burst that starts and ends
    clear            never called at all. `grep '\\.clear('` matched
                     list.clear, dict.clear and Event.clear, so a count of
                     callers said six and the real number was zero
    stuck_current    called once, but the test asserted only that an invariant
                     current was reported. A constant 0.0 A is also invariant,
                     so it passed with the pin removed. Fixed in
                     test_power_path.py by asserting the value.

The point of an injector is readiness: the code already handles the failure on
the day it arrives. One that no test depends on rehearses nothing.
"""

from __future__ import annotations

import pytest

from sparklib import admin as sa
from sparksim import attach, build_fleet, spark
from sparksim import frames as F
from sparksim.fleet import ROLES_FLEX, SERIALS_FLEX

S1 = F.API_STATUS_1


# -- congestion as a BURST, which the constructor kwarg cannot express --------


def test_a_congestion_burst_drops_frames_only_inside_its_window(sim):
    """Catalogue C2: utilisation spikes when a subsystem starts, then settles.

    The constructor kwarg congests the whole run. Real congestion arrives and
    leaves, and a driver that only ever sees uniform loss has never been shown
    the case where the bus recovers on its own.
    """
    bus = sim(build_fleet([12]))
    bus.congestion(0.95, start=bus.start + 1.0, end=bus.start + 2.0, seed=7)
    attach(bus).inventory(3.0)

    dropped = [t for t, _arb, why in bus.dropped if why == "congestion"]
    assert dropped, "the burst dropped nothing; congestion() did not take"

    inside = [t for t in dropped if bus.start + 1.0 <= t <= bus.start + 2.0]
    assert len(inside) == len(dropped), (
        f"{len(dropped) - len(inside)} frames were dropped outside the burst "
        "window; congestion is supposed to be bounded by start and end")


def test_the_bus_recovers_by_itself_once_a_congestion_burst_ends(sim):
    """A burst that clears must leave the cadence correct afterwards, or the
    audit will call a recovered bus a reverted config."""
    bus = sim(build_fleet([12]))
    bus.congestion(0.95, start=bus.start + 0.0, end=bus.start + 1.0, seed=3)
    adm = attach(bus)

    adm.inventory(1.0)                      # during the burst
    after = adm.status_period_ms(12, S1, seconds=2.0)

    assert after == pytest.approx(20.0, abs=4.0), (
        f"once the burst ended the cadence should be the provisioned 20 ms, "
        f"measured {after}")


def test_congestion_outside_the_window_leaves_the_bus_alone(sim):
    """The negative half. A burst scheduled entirely in the future must not
    thin the frames measured before it starts."""
    bus = sim(build_fleet([12]))
    bus.congestion(0.95, start=bus.start + 10.0, end=bus.start + 11.0, seed=5)

    measured = attach(bus).status_period_ms(12, S1, seconds=2.0)
    assert measured == pytest.approx(20.0, abs=2.0), (
        f"a burst ten seconds away thinned the cadence now: {measured} ms")
    assert not bus.dropped, f"frames dropped outside the window: {bus.dropped[:3]}"


# -- clear: someone else wiping the evidence ---------------------------------


def test_another_host_clearing_faults_erases_the_record_mid_window(sim):
    """`bus.clear()` models something OTHER than this driver clearing faults.

    That is not hypothetical. `spark_can.apply_boot_config` clears sticky faults
    unconditionally on every SPARK Flex at boot, so any DriveTrain construction
    -- teleop, a debug tool, the dashboard -- wipes the stickies before an
    operator ever runs `spark audit`. On a steer-stress run erased
    hasReset on all eight of rig-flex exactly this way.

    The audit tells the operator to read the bit before clearing it. This is the
    case where it was already gone.
    """
    bus = sim(build_fleet([12]))
    bus.set_fault(12, warnings=("hasReset",), sticky=True)
    before = sa.collect_status(bus, seconds=0.4)[12]
    assert "hasReset" in sa.normalised_reading(before)["sticky_warnings"], (
        "premise: the sticky must be set before anything clears it")

    bus.clear(12)
    after = sa.collect_status(bus, seconds=0.4)[12]

    assert sa.normalised_reading(after)["sticky_warnings"] == [], (
        "clear() left the sticky standing, so nothing here models a second "
        "host wiping the evidence")


def test_a_cleared_bus_audits_clean_and_that_is_the_hazard(sim):
    """The consequence, stated as a test: after a clear there is no evidence a
    reboot ever happened, and the audit cannot invent it."""
    bus = sim(build_fleet())
    for dev in range(10, 18):
        bus.set_fault(dev, warnings=("hasReset",), sticky=True)
        bus.clear(dev)

    status = sa.collect_status(bus, seconds=0.5)
    inv = attach(bus).inventory(0.5)
    problems = sa.audit_problems(inv, {}, dict(ROLES_FLEX),
                                 dict(SERIALS_FLEX), status=status)

    assert not any("hasReset" in p for p in problems), (
        "premise: a cleared bus reports no sticky")
    assert not any("reboot" in p.lower() for p in problems), (
        "a cleared bus is indistinguishable from one that never rebooted -- if "
        "this ever starts reporting a reboot, something is inferring it from "
        "state that a clear does not erase, and that would be worth keeping")


# -- the sweep itself, so a new injector cannot arrive untested --------------


def test_every_injector_on_the_bus_is_called_by_some_test():
    """A caller count is not proof an injector works, but zero callers IS proof
    it does not. `clear` had zero and looked like six, because `.clear(` also
    matches list, dict and Event. Match the receiver, not just the name.
    """
    import ast
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1]
    bus_py = root / "support" / "sparksim" / "bus.py"
    tree = ast.parse(bus_py.read_text())
    cls = next(n for n in ast.walk(tree)
               if isinstance(n, ast.ClassDef) and "Bus" in n.name)
    public = {n.name for n in cls.body
              if isinstance(n, ast.FunctionDef) and not n.name.startswith("_")}
    # Readers and helpers, not failure injectors.
    NOT_INJECTORS = {"controller", "controllers_at", "add", "schedule", "frames",
                     "sent_ids", "sends_matching", "measured_period_ms",
                     "elapsed", "explain", "send", "recv", "shutdown", "flush",
                     "set_period", "channel_info"}
    injectors = public - NOT_INJECTORS

    # `clear` collides with list.clear, dict.clear and Event.clear, which is how
    # it read as six callers and had none. For that name require a bus-shaped
    # receiver; for the rest the name alone is unambiguous.
    BUS_NAMES = {"bus", "rig-flex", "congested", "clean", "sim_bus", "b"}
    called = set()
    for f in root.rglob("*.py"):
        if "__pycache__" in str(f) or f.name == bus_py.name:
            continue
        try:
            t = ast.parse(f.read_text())
        except SyntaxError:
            continue
        for n in ast.walk(t):
            if not (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)):
                continue
            name, recv = n.func.attr, n.func.value
            if name not in injectors:
                continue
            if name in {"clear"}:
                if isinstance(recv, ast.Name) and recv.id in BUS_NAMES:
                    called.add(name)
            else:
                called.add(name)

    missing = sorted(injectors - called)
    assert not missing, (
        "these injectors are never called on a bus by any test, so nothing "
        f"rehearses the failure they model: {missing}")
