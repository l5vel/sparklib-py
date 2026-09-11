"""The power path: what the SPARK reports, what the breaker carries, and the
two failure modes the corpus says produce a current reading that is not a load.

Every claim here is sourced. REV state that the SPARK reports MOTOR current
while the PDP/PDH channel reads INPUT current and the two differ
<https://www.chiefdelphi.com/t/350542>, and CTRE give the identity
supply = stator x duty cycle <https://www.chiefdelphi.com/t/512713>. So a
controller pinned at its motor-current limit can be drawing a few amps at the
breaker, and an untripped breaker is not evidence the draw is small: REV confirm
the 40 A auto-reset parts pass much more than 40 A before opening
<https://www.chiefdelphi.com/t/374688>.
"""

from __future__ import annotations

import contextlib

import pytest

from sparklib import admin as sa
from sparksim import attach, build_fleet, spark
from sparksim import frames as F
from sparksim.fleet import ROLES_FLEX, SERIALS_FLEX

S0 = F.API_STATUS_0


def test_a_current_that_never_moves_is_not_a_measurement(sim):
    """CD 373283: three teams saw SPARK MAX output current stuck at a constant
    implausible value -- 72 A, 85 A, 125 A -- with the motor unloaded and the
    applied output varying. A reading that does not respond to applied output is
    the controller's telemetry failing, not the mechanism loading up.
    https://www.chiefdelphi.com/t/373283
    """
    bus = sim(build_fleet())
    bus.stuck_current(12, 125.0)
    bus.schedule(0.3, lambda s: setattr(s.controller(12), "applied", 0.9))
    adm = attach(bus)

    # collect first: the applied-output change has to land inside the window
    # being examined, or there is no variation to compare the current against.
    status = sa.collect_status(bus, seconds=1.0)
    inv = adm.inventory(1.0)
    problems = sa.audit_problems(inv, {}, dict(ROLES_FLEX), dict(SERIALS_FLEX),
                                 status=status)

    assert any("12" in p and "current" in p.lower() for p in problems), (
        "a controller reporting a fixed 125 A while its applied output swung "
        f"from rest to 0.9 audited clean: {problems}")

    # The finding above fires for ANY invariant current, including a constant
    # 0.0 A, so on its own it does not prove the pin took. Mutation-checked
    #: neutering stuck_current left this test green. Assert the
    # implausible VALUE reached the decode, which is the thing being modelled.
    amps = {round(sa.normalised_reading(r)["current_a"], 1)
            for r in [status[12]] if r}
    assert amps == {125.0}, (
        f"the pinned 125 A never reached the reading: {amps}. A constant 0 A "
        "also looks invariant, so without this the test passes whether or not "
        "the controller reported an implausible number")
    assert adm is not None


def test_a_loose_vortex_dock_is_named_as_a_mechanical_joint(sim):
    """REV, CD 453509: "The EEPROM Fault and Other Error can result from a loose
    connection between the Vortex and the Flex. Please reseat them and make sure
    that the docking screws are fully installed."
    https://www.chiefdelphi.com/t/453509

    A team on a Flex swerve base saw exactly this escalate to overcurrents every
    match and fixed it by remounting all six motors, without touching the CAN
    network at all. https://www.chiefdelphi.com/t/461113
    """
    bus = sim(build_fleet())
    bus.loose_dock(12)
    adm = attach(bus)
    inv = adm.inventory(1.0)
    status = sa.collect_status(bus, seconds=0.5)

    problems = sa.audit_problems(inv, {}, dict(ROLES_FLEX), dict(SERIALS_FLEX),
                                 status=status)
    joint = [p for p in problems if "12" in p and
             ("dock" in p.lower() or "reseat" in p.lower())]
    assert joint, (
        "EEPROM and Other on one controller is REV's own signature for a "
        f"docking joint that is not seated, and nothing said so: {problems}")


def test_the_two_currents_are_not_conflated_in_any_remedy():
    """The SPARK's number and the breaker's number are different quantities.

    Any remedy that tells an operator to compare the reported current against a
    breaker rating is wrong, and REV say the inference in the other direction is
    wrong too: an untripped thermal breaker is not evidence the draw is under
    its rating. https://www.chiefdelphi.com/t/374688
    """
    text = sa.BIT_REMEDIES["overcurrent"].lower()
    assert "motor" in text and "input" in text, (
        "the overcurrent remedy has to name which current is which, or it "
        "invites the comparison that produced this whole investigation")
    assert "duty" in text, "the conversion between them is the duty cycle"


def test_a_resting_controller_is_not_accused_of_stuck_telemetry(sim):
    """At rest the current is constant because nothing is flowing, and the
    applied output is constant because nothing is commanded. That is the normal
    state of every controller on a parked robot, and it looks identical to a
    pinned reading unless the applied output is required to have MOVED.

    Without that requirement the audit reports all eight controllers as having
    failed telemetry every time the robot is sitting still.
    """
    bus = sim(build_fleet())
    status = sa.collect_status(bus, seconds=1.0)

    accused = [d for d, r in status.items()
               if "current_a_invariant" in ((r.get("status0") or {}).get("implausible") or [])]
    assert accused == [], (
        f"a parked fleet was accused of stuck current telemetry: {accused}")

    problems = sa.audit_problems(attach(bus).inventory(1.0), {}, dict(ROLES_FLEX),
                                 dict(SERIALS_FLEX), status=status)
    assert not any("not a measurement" in p for p in problems), problems


def test_a_healthy_fleet_never_wedges_the_restore_gate(sim):
    """The failure this gate caused before the decode was fixed.

    docs/ESTOP.md records it: an active_faults read using the SparkMax layout
    returned a steady 0x06CE on all eight controllers, which was really bus
    voltage -- 0x06D5/128 is about 13.7 V. Gating recovery on that wedged the
    base permanently, because it re-latched, retried every tick and never
    re-enabled. _FAULT_DECODE_TRUSTED was set False for SparkFlex to stop it.

    The decode is fixed and the gate is on again, so the thing that must now be
    impossible is a HEALTHY fleet reading as faulted. A clean bus has to report
    zero faults through the same path the gate uses, or the base cannot recover
    from a power cycle and no one finds out until it is on the field.
    """
    from sparklib.controller import SPARK_FLEX, Controller

    bus = sim(build_fleet())
    adm = attach(bus)
    status = sa.collect_status(bus, seconds=0.6)
    assert len(status) == 8, "premise: all eight are broadcasting and healthy"

    for dev, reading in status.items():
        c = Controller(bus=None, id=dev, controller_type=SPARK_FLEX)
        c._status0_raw = bus.frames(dev, api=S0)[-1][1].data
        c._status1_raw = bus.frames(dev, api=F.API_STATUS_1)[-1][1].data
        assert c.active_faults == 0, (
            f"id {dev} reads {c.active_faults:#06x} active faults on a healthy "
            "bus; that is the 0x06CE shape and it wedges the restore gate")
        assert c.sticky_faults == 0, f"id {dev} sticky {c.sticky_faults:#06x}"


def test_the_rail_voltage_can_never_be_read_as_a_fault_mask(sim):
    """The specific number, pinned. 0x06CE was a 13.7 V rail decoded as faults.

    A rail in the ordinary 12-14 V band must not produce a non-zero fault word
    through any reader, at any voltage in that band -- not just at the one value
    that happened to be on the bus the day it was found.
    """
    from sparklib.controller import SPARK_FLEX, Controller

    for volts in (11.5, 12.0, 12.6, 13.2, 13.7, 14.0):
        bus = sim([spark(12, volts=volts)])
        attach(bus).status_period_ms(12, F.API_STATUS_1, seconds=0.2)
        c = Controller(bus=None, id=12, controller_type=SPARK_FLEX)
        c._status0_raw = bus.frames(12, api=S0)[-1][1].data
        c._status1_raw = bus.frames(12, api=F.API_STATUS_1)[-1][1].data
        assert (c.active_faults, c.sticky_faults) == (0, 0), (
            f"a {volts} V rail decoded as faults "
            f"{c.active_faults:#06x}/{c.sticky_faults:#06x}")
        assert c.bus_voltage == pytest.approx(volts, abs=0.05), (
            "and the rail has to come out of STATUS_0 as the voltage it is")


@contextlib.contextmanager
def _spark_bus_on(sim_bus):
    """A real SparkBus wired to the simulated CAN bus, shut down afterwards.

    spark_can does `from can import Bus`, so the name is bound in that module
    and patching can.Bus does not reach it. Patch the module's own reference.

    A context manager rather than a factory because SparkBus starts a heartbeat
    thread and a bus monitor as daemons, and a leaked one keeps calling
    time.sleep for the rest of the session. That is invisible here and lands on
    some later test that patches time.sleep to record calls -- which is exactly
    how this leak was found, as two million recorded sleeps in an unrelated
    api test.
    """
    from sparklib import can_bus as spark_can
    orig = spark_can.Bus
    spark_can.Bus = lambda *a, **kw: sim_bus
    try:
        b = spark_can.SparkBus(channel="sim0", bustype="socketcan",
                               bitrate=1000000)
    finally:
        spark_can.Bus = orig
    try:
        yield b
    finally:
        b.shutdown(zero_outputs=False, join_timeout=0.5)


# -- the stop path ------------------------------------------------------------

def test_a_stop_broadcasts_the_disable_frame_not_just_silence(sim):
    """FIRST CAN Device Specification: "Devices should disable immediately when
    receiving the Disable message (arbID 0)."

    Stopping the enable heartbeat also disables the controllers, but each one
    waits out its own timeout first. The broadcast is immediate, needs no
    per-controller addressing, and reaches a controller whose id is wrong or
    duplicated -- which is exactly the population you cannot address on the one
    occasion you most need to.

    It is an annunciator, not the stop. The stop is the break in the motor rail.
    """
    bus = sim(build_fleet())
    with _spark_bus_on(bus) as real:
        real.disable_heartbeat()

    assert bus.sends_matching(F.DISABLE_BROADCAST), (
        "a stop stopped sending the enable heartbeat and broadcast nothing, so "
        "every controller keeps driving until its own timeout expires")
    for c in bus.controllers:
        assert c.disable_broadcasts >= 1, f"id {c.dev} never saw the disable"
        assert c.applied == 0.0, f"id {c.dev} is still commanding output"


def test_the_disable_reaches_a_controller_that_answers_nothing_else(sim):
    """The population the broadcast exists for. A controller gated awaiting a
    clear ignores requests; one on a duplicated id cannot be addressed
    unambiguously. Neither can be told to stop by id, and both must still stop.
    """
    bus = sim(build_fleet() + [spark(12, "DEADBEEF")])
    bus.silent_until_cleared(14)
    with _spark_bus_on(bus) as real:
        real.disable_heartbeat()

    for dev in (12, 14):
        for c in bus.controllers_at(dev):
            assert c.disable_broadcasts >= 1, (
                f"id {dev} did not receive the disable, and it is precisely the "
                "controller that cannot be addressed individually")


# -- the two thresholds, told apart -------------------------------------------

def test_current_chop_is_silent_on_the_bus_and_only_the_output_shows_it(sim):
    """What actually happens when the driver is disabled by kCurrentChop.

    Parameter 11, default 115 A: "If the half bridge detects this current limit,
    it will disable the motor driver for a fixed amount of time set by
    kCurrentChopCycles." So the driver goes OFF and back ON repeatedly -- an
    oscillation, not a sustained dead output.

    The part that makes it hard: REV's published Fault Conditions table for the
    SPARK Flex has no overcurrent entry, and neither product has an LED blink
    code for it. Nothing on the bus announces a chop. The mechanism stutters,
    the operator sees a mechanical problem, and the only evidence is the shape
    of the applied output against a current sitting above the threshold.
    https://docs.revrobotics.com/brushless/spark-max/parameters
    https://docs.revrobotics.com/brushless/spark-flex/status-led
    """
    bus = sim(build_fleet())
    bus.current_chop(12)

    applied, amps, bits = [], [], set()
    end = bus.elapsed() + 0.8
    while bus.elapsed() < end:
        st = sa.collect_status(bus, seconds=0.05).get(12)
        if not st or not st.get("status0"):
            continue
        applied.append(round(st["status0"]["applied_output"], 3))
        amps.append(st["status0"]["current_a"])
        s1 = st.get("status1") or {}
        for k in ("faults", "warnings", "sticky_faults", "sticky_warnings"):
            bits |= set(s1.get(k) or [])

    assert min(amps) > 115.0, (
        f"premise: current stays above the 115 A chop threshold, saw {min(amps)}")
    assert len(set(applied)) > 1, (
        "the driver cuts out and comes back, so applied output must oscillate; "
        f"a single value means the chop is being modelled as a dead output: {set(applied)}")
    assert 0.0 in applied and any(a > 0.1 for a in applied), (
        f"the oscillation has to reach both states: {sorted(set(applied))}")

    assert bits == set(), (
        "REV publish no fault condition for overcurrent on either product, so a "
        f"chopping driver must raise nothing at all -- got {bits}. If this ever "
        "starts failing, REV added a bit and the guide needs updating")


def test_a_chopping_driver_is_still_flagged_by_the_output_current_pair(sim):
    """Silent on the fault bits does not mean invisible.

    The instant the driver is off, applied output is zero while real current
    flows -- the same pair that catches a dropout (CD 477176). So the chop IS
    detectable, from telemetry the driver already decodes, even though no bit
    is set. What the pair cannot do is say WHICH of the two it is, and the
    remedies are opposite: a chop is the controller protecting itself at a
    documented threshold, a dropout is a controller that stopped following its
    command.
    """
    bus = sim(build_fleet())
    bus.current_chop(12)
    st = sa.collect_status(bus, seconds=0.04)[12]["status0"]

    assert abs(st["applied_output"]) < 0.01 and st["current_a"] > 115.0, (
        "premise: caught in the off half of the chop")
    assert "applied_output" in st["implausible"], (
        "zero applied output with 118 A flowing has to be flagged; whether it "
        "is a chop or a dropout is the operator's next question, and the "
        "current sitting above 115 A is the hint that decides it")


def test_a_controller_at_its_limit_is_not_reported_as_faulted(sim):
    """The Smart Current Limit working correctly.

    REV's software lead: the limit holds the motor AT the limit value and scales
    duty cycle to do it. So a steady current at the limit with reduced applied
    output is a controller being regulated -- the mechanism doing its job, not a
    fault -- and an audit that calls it a problem sends someone to chase a
    healthy controller. https://www.chiefdelphi.com/t/350542
    """
    bus = sim(build_fleet())
    for c in bus.controllers:
        c.applied = 0.9                      # what the host asked all of them for
    bus.limit_holds_at_value(12, amps=80.0)
    inv = attach(bus).inventory(1.0)
    status = sa.collect_status(bus, seconds=0.5)

    mine = status[12]["status0"]
    peer = status[14]["status0"]
    assert peer["applied_output"] > 0.8, "premise: an unregulated peer follows"
    assert mine["applied_output"] < peer["applied_output"] * 0.6, (
        f"regulation means the duty is SCALED DOWN to hold the current: id 12 "
        f"applied {mine['applied_output']:.2f} against a peer's "
        f"{peer['applied_output']:.2f}. Equal duty means nothing is regulating "
        "and the injector is not reproducing what REV describe")
    assert abs(mine["current_a"] - 80.0) < 2.0, (
        f"and the current is held AT the limit, saw {mine['current_a']:.1f} A")

    problems = sa.audit_problems(inv, {}, dict(ROLES_FLEX), dict(SERIALS_FLEX),
                                 status=status)
    assert not any("12" in p and "not a measurement" in p for p in problems), (
        f"a controller regulating at its current limit audited as broken: "
        f"{[p for p in problems if '12' in p]}")


# -- the dropped configuration message ----------------------------------------

def test_one_dropped_config_write_latches_the_sensor_fault(sim):
    """REV's root cause for the 2024 SPARK Flex Sensor Fault epidemic, verbatim:
    "due to a missed configuration message (from high bus utilization, poor CAN
    wiring, etc.) the Flex gets into an invalid sensor configuration resulting
    in the Sensor Fault." https://www.chiefdelphi.com/t/456113

    One dropped write, not a broken controller -- which is why the retry loop in
    write_param is a fix for a hardware-looking fault.
    """
    bus = sim(build_fleet())
    bus.drop_config_write(12, 159)
    status = sa.collect_status(bus, seconds=0.5)

    bits = set(status[12]["status1"]["sticky_faults"])
    assert "sensor" in bits, (
        "a missed configuration message has to present as the sensor fault REV "
        f"describes, or the injector is not reproducing the epidemic: {bits}")


# -- the enable window, which the spec puts on the device ---------------------

def test_a_starved_heartbeat_stops_the_motors_without_the_host_asking(sim):
    """FIRST CAN Device Specification, on the universal heartbeat: "If 100 ms
    has passed since this packet was received, the robot program can be
    considered hung, and devices should act as if the robot has been disabled."

    The duty is on the DEVICE: a node driving an actuator "must implement a way
    to verify that the robot is enabled and that commands originate with the
    main robot controller". That matters here because a host stall -- a blocked
    thread, a wedged gs_usb adapter -- produces no stop frame at all. Nobody
    sends anything. The controller has to notice the silence itself.
    """
    bus = sim(build_fleet())
    for c in bus.controllers:
        c.applied = 0.4
    bus.heartbeat_gap(seconds=0.15)

    st = sa.collect_status(bus, seconds=0.3)
    still_driving = [d for d, r in st.items()
                     if abs(((r or {}).get("status0") or {}).get("applied_output", 0)) > 0.01]
    assert still_driving == [], (
        f"ids {still_driving} kept applying output through a heartbeat gap "
        "longer than the spec's 100 ms window. A host that stalls sends no "
        "stop frame, so this is the only thing that ends the motion")


def test_a_flex_status_timeout_is_not_read_as_healthy_telemetry(sim):
    """REV on the Flex-only defect: "SPARK Flex firmware v25.0.2 has been
    released which includes a fix for the status timeouts... The 500ms timeout
    was put in as a stopgap during 2024 for when users were experiencing this
    same exact issue." https://www.chiefdelphi.com/t/480555

    A controller that stops broadcasting for longer than the reader's validity
    window leaves the last frame standing. Nothing marks it stale, so a tool
    that keeps the newest payload reports a reading that has stopped being true.
    """
    bus = sim(build_fleet())
    bus.flex_status_timeout(12)
    inv = attach(bus).inventory(1.0)

    assert 12 not in inv or (inv[12].get("max_gap_ms") or {}).get(F.API_STATUS_1, 0) > 400, (
        "a Flex that went quiet for 600 ms has to show as a gap in the "
        f"inventory, not as a healthy cadence: {inv.get(12)}")

    problems = sa.audit_problems(inv, {}, dict(ROLES_FLEX), dict(SERIALS_FLEX))
    assert any("12" in p for p in problems), (
        f"a controller that stopped broadcasting audited clean: {problems}")


# -- brownout, and what actually stops the motors -----------------------------

def test_a_brownout_ends_in_the_disable_frame_not_just_a_low_rail(sim):
    """WPILib documents the roboRIO brownout as staged, and 6.8 V is NOT where
    output stops: "When the voltage drops below 6.8V, the 6V output on the PWM
    pins will start to drop." Disable comes lower, and at stage 2 the roboRIO
    "sends CAN motor controllers an explicit disable command".

    So the thing that stopped the motors is a FRAME, not the voltage. A tool
    that reads the rail and reasons from the number alone is watching the
    symptom; the disable broadcast is the mechanism.
    """
    bus = sim(build_fleet())
    for c in bus.controllers:
        c.applied = 0.4
    bus.brownout_stages()

    st = sa.collect_status(bus, seconds=0.6)
    rails = [r["status0"]["voltage_v"] for r in st.values() if r.get("status0")]
    assert rails and min(rails) < 6.8, f"premise: the rail sagged, saw {min(rails)}"

    for c in bus.controllers:
        assert c.disable_broadcasts >= 1, (
            f"id {c.dev} browned out and was never sent the explicit disable; "
            "the rail dropping is not what stops a CAN motor controller")
        assert c.applied == 0.0, f"id {c.dev} is still commanding output"

    warned = [d for d, r in st.items()
              if "brownout" in ((r.get("status1") or {}).get("sticky_warnings") or [])]
    assert sorted(warned) == sorted(st), (
        f"every controller saw the sag; only {warned} recorded it")


# -- the wedge the recovery path exists for -----------------------------------

def test_a_wedged_bus_is_told_apart_from_a_fleet_that_died(sim):
    """Reported on WPILib 2026.2.1 with REVLib 2026.0.1: "doing a Restart Robot
    Code from the DriverStation leads to a state where the CANbus seems to be
    locked up/inaccessible. A second Restart Robot Code clears the problem."

    On the wire this is indistinguishable from eight dead controllers by
    reply-counting alone, and the two repairs are opposite: one is the adapter,
    the other is the robot. The audit has to name the bus rather than accuse
    eight devices, which is what the bus-level rule is for.
    """
    bus = sim(build_fleet())
    bus.bus_wedged_flat()
    inv = attach(bus).inventory(1.0)

    assert inv == {}, "premise: nothing is transmitting at all"
    problems = sa.audit_problems(inv, {}, dict(ROLES_FLEX), dict(SERIALS_FLEX))
    assert len(problems) == 1, (
        f"a wedged bus reported as {len(problems)} separate device failures, "
        f"which sends someone to check eight controllers: {problems}")
    assert "bus" in problems[0].lower(), problems[0]


# -- the transient a hard acceleration makes ----------------------------------

def test_an_acceleration_transient_is_visible_in_the_peak_not_the_mean(sim):
    """Reported on a swerve steering axis: "pushing it to the max causes the
    SPARK MAX to flag a current fault at times", and backing the acceleration
    off removes it.

    It is a transient at the start of a move, so a window that reports an
    average never sees it. This is the regime tools/spark_steer_stress.py
    provokes deliberately, and the reason that script samples rather than
    summarising.
    """
    bus = sim(build_fleet())
    bus.accel_transient(12, peak_a=132.0)

    peak, samples = 0.0, []
    end = bus.elapsed() + 0.3
    while bus.elapsed() < end:
        r = sa.collect_status(bus, seconds=0.03).get(12)
        s0 = (r or {}).get("status0")
        if not s0:
            continue
        samples.append(s0["current_a"])
        peak = max(peak, s0["current_a"])

    assert samples, "the controller was sampled"
    assert peak > 115.0, (
        f"the transient peak has to be visible, saw {peak:.1f} A")
    mean = sum(samples) / len(samples)
    assert mean < peak * 0.8, (
        f"and it has to be a TRANSIENT: mean {mean:.1f} A against peak "
        f"{peak:.1f} A. If the mean tracks the peak this is a steady load and "
        "the injector is not reproducing what was reported")
