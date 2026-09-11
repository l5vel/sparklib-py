"""rig-max: four drive controllers latched overcurrent and the whole
bus went dark. How much of that is reproducible off the robot, and what is not.

The incident, in the order it was measured:

  1. A high-speed drive test. Afterwards the four DRIVE controllers held sticky
     overcurrent, id 4 also gateDriver, and the four STEER controllers held
     nothing. reset_kind was "none" on all eight, so nothing rebooted.
  2. The bus went silent, all eight, for roughly ten minutes.
  3. `spark canfix` and a physical replug both failed to bring it back.
  4. The kernel logged the adapter disconnecting and re-enumerating seven times,
     one instance surviving 0.54 s, with three `device descriptor read/64,
     error -32` stalls.
  5. It recovered on a `spark clear` that happened to land in a good window.

This module pins the half that IS reproducible and, more usefully, proves the
half that is NOT -- because the tooling's own advice blames a latched fault for
silencing the whole bus, and this incident does not fit that story.

docs/spark/runs/-rig-max-drive-overcurrent-and-adapter-flap.md
"""
from __future__ import annotations

from sparklib import admin as sa
from sparksim import attach, build_fleet_pre25
from sparksim import frames as F
from sparksim.fleet import ROLES_MAX

# The exact pattern read off rig-max in docs/spark/runs/-rig-max-drive-overcurrent-and-adapter-flap.md.
DRIVE = sorted(d for d, r in ROLES_MAX.items() if r.startswith("drive"))
STEER = sorted(d for d, r in ROLES_MAX.items() if r.startswith("steer"))
OVERCURRENT = F.LEGACY_FAULT["overcurrent"]
GATE = F.LEGACY_FAULT["gateDriver"]


def _read(bus, seconds=0.5):
    """This fleet's sticky faults, through the generation-neutral accessor.

    Reaching in by key is 25+-only by construction: pre-25 keeps faults in
    LEGACY_STATUS_0 and telemetry in 0x061, so `reading["status1"]` has no
    sticky_faults at all. The first draft of this file made exactly that
    mistake, which is the trap the repo names first.
    """
    st = sa.collect_status(bus, seconds=seconds)
    return {d: sa.normalised_reading(r) for d, r in st.items()}


def _as_measured(bus):
    """Set the fleet to exactly what the robot held after the run."""
    for d in DRIVE:
        c = bus.controller(d)
        c.sticky_faults |= OVERCURRENT
        if ROLES_MAX[d] == "drive/RB":
            c.sticky_faults |= GATE
    return bus


def test_the_fixture_matches_the_record_it_is_named_after():
    """Guards the premise: a fleet split the wrong way proves nothing below."""
    assert DRIVE == [1, 4, 5, 8], DRIVE
    assert STEER == [2, 3, 6, 7], STEER
    assert not set(DRIVE) & set(STEER)


# -- what the simulator DOES reproduce ----------------------------------------

def test_a_latched_drive_controller_goes_dark_and_clear_brings_it_back(sim):
    """The controller half of the incident, and it models cleanly."""
    bus = _as_measured(sim(build_fleet_pre25()))
    for d in DRIVE:
        bus.silent_until_cleared(d)
    adm = attach(bus)

    assert sorted(adm.inventory(1.0)) == STEER, "only the latched four go dark"

    adm.clear_faults(DRIVE)
    assert sorted(adm.inventory(1.0)) == sorted(ROLES_MAX), bus.explain()


def test_the_sticky_bits_survive_until_something_clears_them(sim):
    """Why the record was still readable ten minutes and three clears later:
    the earlier clears never reached the wire, and sticky state does not decay."""
    bus = _as_measured(sim(build_fleet_pre25()))
    first = _read(bus)
    for d in DRIVE:
        assert "overcurrent" in first[d]["sticky_faults"], d
    # time passes, nothing is sent
    bus.clock.advance(600.0)
    later = _read(bus)
    for d in DRIVE:
        assert "overcurrent" in later[d]["sticky_faults"], d
    for d in STEER:
        assert later[d]["sticky_faults"] == [], d


def test_only_the_drive_half_faults_under_a_drive_load(sim):
    """The role split is the strongest thing in the record: the steer
    controllers saw the same window and latched nothing."""
    bus = _as_measured(sim(build_fleet_pre25()))
    st = _read(bus)
    faulted = {d for d in st if st[d]["sticky_faults"]}
    assert faulted == set(DRIVE), st
    assert set(st[4]["sticky_faults"]) == {"overcurrent", "gateDriver"}


# -- what the simulator does NOT reproduce, which is the part that mattered ----

def test_latched_faults_alone_do_not_silence_the_whole_bus(sim):
    """THE INCIDENT DOES NOT FIT THE TOOLING'S OWN EXPLANATION.

    `spark status` tells the operator a latched fault "stops a controller
    broadcasting AND ACKing, which can silence the whole bus". Four of eight
    were latched on rig-max and ALL EIGHT went dark. Silence the four that
    actually faulted and the other four keep broadcasting, here and on any bus
    where the latched-fault story is the whole story.

    So something outside the controllers took the bus down, and the kernel log
    says what: the adapter was disconnecting and re-enumerating on its own.
    """
    bus = _as_measured(sim(build_fleet_pre25()))
    for d in DRIVE:
        bus.silent_until_cleared(d)
    adm = attach(bus)
    still_up = sorted(adm.inventory(1.0))
    assert still_up == STEER, (
        "the four unfaulted controllers must stay on the wire; if this ever "
        "fails the latched-fault explanation becomes viable and the run doc "
        "needs revisiting")
    assert still_up, "a total blackout here would mean the sim models it too"


def test_the_simulator_has_no_adapter_layer_to_fail(sim):
    """The cause is below the simulator's floor, and that is a fact about the
    harness worth pinning so nobody hunts for a repro that cannot exist.

    SimBus is a Python object. There is no socket, no USB device and no qdisc,
    so `usb xmit fail`, a self-disconnect, an enumeration stall and a transmit
    backlog that will not drain have nowhere to happen.
    """
    bus = sim(build_fleet_pre25())
    for attr in ("fileno", "socket", "_sock"):
        assert not hasattr(bus, attr), f"SimBus grew a {attr}; revisit this"
    # The one thing it does model is a bus that answers nothing at all, which is
    # what the operator SEES. It cannot say why.
    for d in sorted(ROLES_MAX):
        bus.silent_until_cleared(d)
    assert attach(bus).inventory(1.0) == {}, "a dark bus is modellable"
    #...and that indistinguishability is exactly the diagnostic problem.
