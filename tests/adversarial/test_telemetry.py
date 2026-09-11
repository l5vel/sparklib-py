"""STATUS_0 telemetry: what the frame says, and whether the driver believes it.

Every test here puts a modelled controller on the simulated bus and reads it back
through the real driver -- `collect_status`, `decode_status_0`, the audit pipeline
`cmd_audit` runs -- rather than handing a decoder a literal payload. The failures
are the ones where the bytes are perfectly well formed and the conclusion drawn
from them is still wrong:

  * a payload that is not a measurement at all (all-ones, a REV PDH answering on
    a SPARK's id) reported with the same confidence as a captured idle frame;
  * evidence that exists for part of an observation window and is gone by the
    last frame, which is the only frame `collect_status` keeps;
  * the 2024-era layout still live in `spark_controller`, which reads the rail
    out of the fault frame and the fault word out of the rail -- the two frames
    are swapped, so a healthy controller reads as faulted and a browning-out one
    reads as 0.01 V.

Frame layout: REV-Specs 2.1.0. Firmware under test: SparkFlex 26.1.6, rig-flex.
Catalogue: docs/spark/FAILURE-CATALOGUE.md.

An xfail here names a missing capability in its reason, not a flake.
"""
from __future__ import annotations

import ast
import pathlib
from types import SimpleNamespace

import pytest

from sparklib import rail as battery
from sparklib import admin as sa
from sparklib import controller as sc
from sparksim import SimSpark, attach, spark
from sparksim import frames as F

S0, S1 = F.API_STATUS_0, F.API_STATUS_1
SPARK_CLI = (pathlib.Path(__file__).resolve().parents[2] / "sparklib"
             / "cli.py")

# The all-ones payload a dead transmitter leaves on a recessive bus, produced by
# a controller rather than typed in, so it is a frame the driver received.
SATURATED = dict(applied=-F.APPLIED_SCALE, volts=30.0, amps=150.0, temp_c=255,
                 model=15, hard_fwd=True, hard_rev=True, soft_fwd=True,
                 soft_rev=True, inverted=True, heartbeat_lock=True)


def audit_as_cmd_audit_does(bus, roles, window=1.0):
    """The audit pipeline exactly as spark_cli.cmd_audit runs it.

    A mirror of the call site, so it has to track the call site. It used to take
    two listening windows and hand `audit_problems` no status payload at all,
    which was the whole point of the tests calling it: D1 was that cmd_audit
    never collected one. Now that it does, a mirror still missing it would be
    testing a pipeline that no longer exists.

    `test_the_audit_command_reads_a_status_payload` is the guard that this stays
    honest -- it reads cmd_audit's own source and fails if collect_status leaves
    it.
    """
    adm = attach(bus)
    return sa.audit_problems(adm.inventory(window), adm.duplicates(window), roles,
                             status=sa.collect_status(bus, seconds=window))


def last_frame(bus, dev, api):
    """The final payload of that frame the bus actually delivered."""
    seen = bus.frames(dev, api=api)
    assert seen, f"dev {dev} never broadcast api 0x{api:03X}\n" + bus.explain(dev)
    return bytes(seen[-1][1].data)


# -- the frame the hardware really sends ---------------------------------------

def test_the_captured_rig_flex_idle_frame_reaches_the_driver_unchanged(sim):
    """The dev-12 idle capture from the live rig-flex bus, put back on the wire.

    A decoder is only trustworthy against a frame nobody wrote by hand. This one
    is assembled from the rig-flex candump rather than taken whole off
    the wire: the voltage, temperature and model fields are measured on this
    fleet, and the byte combination pairs two controllers. Its raw voltage field
    is 1804, i.e. 13.216 V, not the round 13.2 an invented frame would carry. Catalogue, Protocol facts: the 2025+ STATUS_0 layout.
    """
    bus = sim([spark(12, volts=F.CAPTURED_BASE03_IDLE_VOLTS, temp_c=31)])
    st = sa.collect_status(bus, seconds=0.5)

    assert last_frame(bus, 12, S0) == F.CAPTURED_BASE03_IDLE_STATUS_0, (
        "the modelled controller must put the captured bytes on the wire")
    reading = st[12]["status0"]
    assert reading["voltage_v"] == pytest.approx(13.216, abs=0.005)
    assert reading["motor_temp_c"] == 31
    assert reading["current_a"] == pytest.approx(0.0, abs=0.02)
    assert reading["spark_model"] == 1, "1 is SPARK Flex"
    assert not any(reading[k] for k in ("hard_forward_limit", "hard_reverse_limit",
                                        "soft_forward_limit", "soft_reverse_limit"))


def test_collect_status_reads_the_whole_provisioned_eight(healthy):
    """The control every failure below is read against: eight healthy SparkFlex.

    Without it, a bug that made `collect_status` return nothing would let most of
    this file pass. Catalogue, cross-cutting lesson 5: a clean audit is not a
    healthy motor -- but an audit that cannot even see the fleet is worse.
    """
    bus, _ = healthy
    st = sa.collect_status(bus, seconds=0.5)

    assert sorted(st) == list(range(10, 18)), bus.explain()
    for dev, v in st.items():
        assert v["status0"]["voltage_v"] == pytest.approx(12.6, abs=0.02), (
            f"id {dev} read the wrong rail\n" + bus.explain(dev))
        assert v["status1"]["faults"] == [] and v["status1"]["sticky_faults"] == []


@pytest.mark.parametrize("field, key", [
    ("hard_fwd", "hard_forward_limit"),
    ("hard_rev", "hard_reverse_limit"),
    ("soft_fwd", "soft_forward_limit"),
    ("soft_rev", "soft_reverse_limit"),
])
def test_each_limit_bit_reaches_the_driver_on_its_own(sim, field, key):
    """A misaligned data-port breakout board held one hard limit asserted, and the
    first advice the team was given was to disable the limits.
    https://www.chiefdelphi.com/t/455481

    Four interlock bits sit in one byte next to `inverted` and the heartbeat lock.
    A swap between fwd and rev sends a repair at the wrong end of the mechanism,
    and a bleed into byte 6 bit 4 makes it look like a polarity problem instead.
    """
    bus = sim([spark(12, **{field: True})])
    reading = sa.collect_status(bus, seconds=0.3)[12]["status0"]

    asserted = [k for k in ("hard_forward_limit", "hard_reverse_limit",
                            "soft_forward_limit", "soft_reverse_limit")
                if reading[k]]
    assert asserted == [key], f"one switch was closed, the driver read {asserted}"
    assert not reading["inverted"] and not reading["primary_heartbeat_lock"], (
        "the neighbouring flags in byte 6 must not move with a limit switch")


def test_a_closed_hard_limit_is_an_interlock_and_not_a_fault(sim):
    """The other half of t/455481: a limit switch holding a mechanism is a state
    to respect, not an error to clear. https://www.chiefdelphi.com/t/455481

    The two live in different frames on 26.1.6 -- interlock in STATUS_0 byte 6,
    errors in STATUS_1 -- and a tool that folded them together would invite
    exactly the repair the driver refuses (params 50-53 are write-protected).
    """
    bus = sim([spark(12, hard_fwd=True, hard_rev=True)])
    v = sa.collect_status(bus, seconds=0.3)[12]

    assert v["status0"]["hard_forward_limit"] and v["status0"]["hard_reverse_limit"]
    reported = {k: val for k, val in v["status1"].items() if val}
    assert reported == {}, (
        "a closed interlock must not appear anywhere in the error frame, and "
        f"STATUS_1 reported {reported}")


def test_the_spark_model_field_is_reassembled_across_the_byte_boundary(sim):
    """Mixed hardware and firmware on one bus is routine, and the two families do
    not share a STATUS layout. Catalogue F2 (mixed firmware versions) and Protocol
    facts: "a 2024-era decoder mis-parses 2025 frames".

    spark_model is bits 54-57, i.e. two bits at the top of byte 6 and two at the
    bottom of byte 7, so any model above 3 is lost by a decoder that reads one
    byte. That is the field that says whether the layout being decoded is the
    right one at all.
    """
    bus = sim([spark(12, model=1), spark(13, model=5)])
    st = sa.collect_status(bus, seconds=0.3)

    assert st[12]["status0"]["spark_model"] == 1, "SPARK Flex"
    assert st[13]["status0"]["spark_model"] == 5, (
        "a model number that does not fit in byte 6 was truncated\n"
        + bus.explain(13))


def test_applied_output_keeps_its_sign_and_scale_on_the_wire(sim):
    """Applied output is the field the best-instrumented dropout report is built
    on -- `.set()` commanding non-zero while `getAppliedOutput()` read exactly 0.
    https://www.chiefdelphi.com/t/477176

    It is a signed int16 sharing the frame with an unsigned 12-bit current, so a
    sign error reads a reversing wheel as a driving one and a field bleed reads
    current where there is none.
    """
    bus = sim([spark(12, applied=-0.75, amps=0.0), spark(13, applied=0.75)])
    st = sa.collect_status(bus, seconds=0.3)

    assert st[12]["status0"]["applied_output"] == pytest.approx(-0.75, abs=1e-3)
    assert st[13]["status0"]["applied_output"] == pytest.approx(0.75, abs=1e-3)
    assert st[12]["status0"]["current_a"] == pytest.approx(0.0, abs=0.02), (
        "a full-reverse applied output must not bleed into the current field")


def test_the_four_status_1_error_sets_do_not_bleed_into_each_other(sim):
    """The legacy layout packed faults and sticky faults into one 16-bit word; the
    2025+ layout splits them across bytes 0, 2, 3 and 5 with different bit
    meanings for faults and warnings. Catalogue, Protocol facts; the confusion is
    still in circulation, https://www.chiefdelphi.com/t/461037

    Four distinct sets, set at once, is the only way to catch a decoder that reads
    the right bit out of the wrong byte.
    """
    dev = spark(12)
    dev.faults = F.fault_mask("can")
    dev.warnings = F.warn_mask("stall")
    dev.sticky_faults = F.fault_mask("gateDriver")
    dev.sticky_warnings = F.warn_mask("hasReset")
    bus = sim([dev])

    reading = sa.collect_status(bus, seconds=0.3)[12]["status1"]

    assert reading["faults"] == ["can"]
    assert reading["warnings"] == ["stall"]
    assert reading["sticky_faults"] == ["gateDriver"]
    assert reading["sticky_warnings"] == ["hasReset"]


def test_a_brownout_that_recovered_leaves_the_sticky_warning_the_driver_reads(sim):
    """Catalogue, bus signatures, Brownout: the rail collapses, the warning sets,
    and the sticky copy latches after recovery -- so a driver reading only live
    warnings sees nothing afterwards.

    This is the one piece of history 26.1.6 keeps for the driver, and `spark
    voltage` is built on it: the dip itself is long gone by the time anyone looks.
    """
    bus = sim([spark(12)])
    bus.brownout(12, at=0.2, volts=5.8, duration=0.3)

    v = sa.collect_status(bus, seconds=1.0)[12]

    assert v["status0"]["voltage_v"] == pytest.approx(12.6, abs=0.05), (
        "the rail came back before the window ended")
    assert v["status1"]["warnings"] == [], "the live warning went with it"
    assert v["status1"]["sticky_warnings"] == ["brownout"], (
        "the sticky bit is the only evidence left that the rail ever collapsed\n"
        + bus.explain(12))


def test_a_thermal_fault_is_visible_while_it_is_still_asserted(sim):
    """Catalogue, bus signatures, Thermal (Flex only): motor temperature climbs,
    applied output goes to 0 while frames keep flowing, and it recovers by itself.

    The control for the transient test below -- while the fault is asserted the
    driver does read it, so a window that misses it missed it by timing, not by
    decoding.
    """
    bus = sim([spark(12)])
    bus.thermal_foldback(12, at=0.05, temp_c=95, duration=5.0)

    v = sa.collect_status(bus, seconds=0.5)[12]

    assert v["status1"]["faults"] == ["temperature"]
    assert v["status0"]["motor_temp_c"] == 95
    assert v["status0"]["voltage_v"] == pytest.approx(12.6, abs=0.05), (
        "temperature rides in the same frame as the rail and must not disturb it")


def test_a_status_0_the_driver_never_received_is_not_invented(sim):
    """Status frames are individually disableable, and a disabled frame is a
    documented cause of "missing status" that looks exactly like congestion.
    Catalogue C3 (periodic status timeouts on deliberately disabled frames).

    With no STATUS_0 there is no voltage, no current and no limit state, and the
    honest answer is None -- `cmd_faults` and `cmd_voltage` both branch on it.
    """
    bus = sim([spark(12)])
    bus.disable_frame(12, S0)

    v = sa.collect_status(bus, seconds=0.5)[12]

    assert v["status0"] is None, "no frame arrived; there is nothing to report"
    assert v["status1"]["faults"] == [], "STATUS_1 kept flowing"
    assert sa.decode_status_0(F.CAPTURED_BASE03_IDLE_STATUS_0[:4]) is None, (
        "a truncated payload is not a partial reading either")


def test_the_repo_already_knows_a_thirty_volt_rail_is_impossible(sim):
    """A SPARK whose status stream died reported 30 V until it was rebooted.
    https://www.chiefdelphi.com/t/407271

    `battery.rail_implausible` exists precisely to refuse a reading that cannot be
    a charge level, and it rejects the saturated field. The knowledge is in the
    repo; the next test is about where it is not applied.
    """
    bus = sim([spark(12, **SATURATED)])
    volts = sa.collect_status(bus, seconds=0.3)[12]["status0"]["voltage_v"]
    full, empty = battery.rail_endpoints(SimpleNamespace(chemistry="LiFePO4"))

    assert (full, empty) == (13.4, 12.0)
    assert battery.rail_implausible(volts, full, empty), (
        f"{volts:.2f} V must not be graded as a charge level")
    assert battery.rail_implausible(12.6, full, empty) is None, (
        "and a real rail must still pass")


# -- what the driver believes anyway -------------------------------------------

def test_a_saturated_status_0_is_not_handed_over_as_telemetry(sim):
    """A SPARK whose status stream died reported 30 V until reboot.
    https://www.chiefdelphi.com/t/407271

    An all-ones payload is what a stuck-recessive transmitter leaves behind, and
    every field saturates together: 0xFFF at 0.0073260073 V/LSB is 29.9999 V,
    0xFFF at 0.0366300366 A/LSB is 149.99 A, temperature 255 C, and spark_model
    15 -- a model REV has never shipped. `battery.rail_implausible` rejects the
    voltage already (test above); nothing on the STATUS_0 path asks it.
    """
    bus = sim([spark(12, **SATURATED)])
    reading = sa.collect_status(bus, seconds=0.3)[12]["status0"]

    assert reading.get("implausible"), (
        f"a single frame reported {reading['voltage_v']:.2f} V, "
        f"{reading['current_a']:.1f} A, {reading['motor_temp_c']} C and model "
        f"{reading['spark_model']} with the same confidence as an idle capture")


def test_a_fault_that_recovered_inside_the_window_survives_collect_status(sim):
    """Catalogue, bus signatures, Thermal (Flex only): the controller folds back,
    then "recovers by itself with no sticky fault and no operator action". The
    same shape at frame resolution is CD 460577 -- eleven fault bits set for
    exactly one loop cycle and none of them become sticky.
    https://www.chiefdelphi.com/t/460577

    A fault with no sticky copy exists only in the frames that carried it. The
    window here contains ~20 STATUS_1 frames asserting `temperature`; the driver
    keeps the last frame of the window, which is clean, so a controller that
    folded back twice a minute audits identically to one that never has.

    Also reported at https://www.chiefdelphi.com/t/353365
    Also reported at https://www.chiefdelphi.com/t/371676
    """
    bus = sim([spark(12)])
    bus.thermal_foldback(12, at=0.2, temp_c=95, duration=0.4)

    reading = sa.collect_status(bus, seconds=1.5)[12]["status1"]

    seen = set(reading["faults"]) | set(reading["sticky_faults"])\
        | set(reading.get("faults_seen", ()))
    assert "temperature" in seen, (
        "the controller folded back inside the observation window and the "
        "reading handed to the caller was clean")


def test_zero_applied_output_with_current_flowing_is_flagged(sim):
    """The best-instrumented dropout report in the corpus: `.set()` commanding a
    non-zero value while `getAppliedOutput()` read exactly 0, fixed only by
    restarting robot code. https://www.chiefdelphi.com/t/477176
    Catalogue G3 / bus signatures, Intermittent dropoff.

    Applied output and output current arrive in the same 8 bytes. Exactly 0.0
    applied with 45 A flowing is not a state a working controller reaches -- it is
    a controller that stopped following its commanded output while the mechanism
    kept loading it, and it is visible in one frame without any host-side state.
    """
    bus = sim([spark(12, applied=0.0, amps=45.0)])
    reading = sa.collect_status(bus, seconds=0.3)[12]["status0"]

    assert reading.get("implausible"), (
        f"applied output {reading['applied_output']:.4f} with "
        f"{reading['current_a']:.1f} A flowing was reported as two ordinary "
        "numbers")


def test_a_rev_pdh_sharing_a_spark_id_is_not_read_as_motor_telemetry(sim):
    """Catalogue, Protocol facts: bits 28-24 of the arbitration id are the device
    type, 2 for a motor controller. A REV PDH is manufacturer 5 like every SPARK
    and legitimately carries device id 10 -- ids are only unique per device type.

    The PDH's frames land on api 0x2E0 with a payload that is not STATUS_0, and
    the driver keys them under id 10 alongside the real controller. Whichever
    arrived last wins, so `spark faults` prints a 0.00 V motor controller that is
    in fact a healthy power distribution hub.
    """
    pdh = SimSpark(dev=10, serial="0BADCAFE", device_type=F.DEVICE_TYPE_PDH,
                   volts=0.0, temp_c=0)
    bus = sim([spark(10, volts=12.6), pdh])

    reading = sa.collect_status(bus, seconds=0.5)[10]["status0"]

    assert reading["voltage_v"] == pytest.approx(12.6, abs=0.05), (
        f"id 10 reported {reading['voltage_v']:.2f} V: the PDH's frame was "
        "decoded as motor telemetry and overwrote the SPARK's under the same id\n"
        + bus.explain(10))


def test_a_latched_gate_driver_fault_reaches_the_audit(sim, roles):
    """The most reported SPARK hardware failure: a gate driver fault that persists
    across factory reset and reflash, recurs after appearing to clear, and is
    fixed by swapping the controller. Catalogue D1.
    https://www.chiefdelphi.com/t/444231 https://www.chiefdelphi.com/t/491119

    The controller keeps broadcasting at a perfect 20 ms with the right serial, so
    every cadence check passes and the audit exits 0 while id 14 is broadcasting
    the reason it will not drive.
    """
    from sparksim import build_fleet

    bus = sim(build_fleet())
    bus.set_fault(14, faults=["gateDriver"], sticky=True)

    problems = audit_as_cmd_audit_does(bus, roles)

    assert any("14" in p and "gatedriver" in p.lower() for p in problems), (
        "id 14 is latched into a gate driver fault and the audit reported "
        + (f"{len(problems)} unrelated problem(s): {problems}" if problems
           else "no problems at all"))


def test_the_audit_command_reads_a_status_payload():
    """The call site behind D1, checked as source rather than behaviour.

    Fixing `audit_problems` to accept faults changes nothing until `cmd_audit`
    collects them: the function and its caller are two separate defects, and a
    behavioural test of the function alone cannot see the second one. Catalogue
    cross-cutting lesson 5.
    """
    tree = ast.parse(SPARK_CLI.read_text(), filename=str(SPARK_CLI))
    body = next(n for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef) and n.name == "cmd_audit")
    called = {ast.unparse(n.func) for n in ast.walk(body) if isinstance(n, ast.Call)}

    assert any("collect_status" in c for c in called), (
        f"cmd_audit calls {sorted(called)} -- it never looks at a status frame, "
        "so a latched fault cannot reach its exit code")


# -- the 2024-era decoder still in production ----------------------------------

def test_the_legacy_reader_does_not_take_the_rail_out_of_the_fault_frame(sim):
    """The 2025+ firmware moved telemetry into STATUS_0 and split faults into
    Faults{} and Warnings{} in STATUS_1, so a 2024-era decoder mis-parses both
    frames. Catalogue, Protocol facts; the older answer is still in circulation,
    https://www.chiefdelphi.com/t/461037

    `spark_controller.Controller` is the class the swerve stack instantiates, and
    `bus_monitor` fills its raw buffers from exactly these two frames. Its
    `bus_voltage` reads STATUS_1 byte 5 -- the sticky warning byte -- so a
    controller that browned out reports a rail voltage derived from its own
    brownout bit, and `spark_admin` reads the same bus at 12.6 V.
    """
    dev = spark(12, volts=12.6, amps=3.0)
    dev.sticky_warnings = F.warn_mask("brownout")
    bus = sim([dev])
    truth = sa.collect_status(bus, seconds=0.3)[12]["status0"]

    c = sc.Controller(None, 12, sc.SPARK_FLEX)
    c._status0_raw = last_frame(bus, 12, S0)      # the two lines bus_monitor runs
    c._status1_raw = last_frame(bus, 12, S1)

    assert c.bus_voltage == pytest.approx(truth["voltage_v"], abs=0.5), (
        f"the same controller reads {truth['voltage_v']:.2f} V in STATUS_0 and "
        f"{c.bus_voltage:.2f} V through the legacy path, which is its sticky "
        f"warning byte scaled by 1/128")
    assert c.output_current == pytest.approx(truth["current_a"], abs=0.5)


def test_a_closed_hard_limit_is_not_reported_as_follower_mode(sim, capsys):
    """Two failures wearing each other's symptoms. A closed hard limit is a
    misaligned breakout board or a mechanism against its stop
    (https://www.chiefdelphi.com/t/455481); a stuck follower is a controller
    driving a mechanism with no object in your program (catalogue B5). The repair
    for one is not the repair for the other.

    `sparklib.controller.health_report` prints this line straight to an operator, and on
    26.1.6 byte 6 bit 0 is the forward interlock, so every controller sitting on
    its forward limit is announced as a follower.
    """
    bus = sim([spark(12, hard_fwd=True, follower=False)])
    truth = sa.collect_status(bus, seconds=0.3)[12]

    c = sc.Controller(None, 12, sc.SPARK_FLEX)
    c._status0_raw = last_frame(bus, 12, S0)
    c._status1_raw = last_frame(bus, 12, S1)
    c.print_diagnostics()
    out = capsys.readouterr().out

    assert truth["status0"]["hard_forward_limit"] is True
    assert truth["status1"]["is_follower"] is False, "STATUS_1 says it follows nobody"
    assert "is_follower=True" not in out, (
        "a closed forward interlock was printed to the operator as follower "
        f"mode:\n{out.strip()}")


# -- folded in from tests/unit/test_spark_adversarial.py ----------------------
# Four more STATUS fields the driver decodes correctly and then discards, and the
# applied-output/current pair. All of them are D1 seen from the telemetry side:
# the frame is well formed, the decode is right, and the conclusion never happens.

def _heartbeat_locked(c):
    c.heartbeat_lock = True


def _hard_limit(c):
    c.hard_rev = True


def _follower(c):
    c.follower = True


@pytest.mark.parametrize("condition, needle, url", [
    (_heartbeat_locked, "lock", "https://www.chiefdelphi.com/t/452062"),
    (_hard_limit, "limit", "https://www.chiefdelphi.com/t/455481"),
    (_follower, "follow", "https://www.chiefdelphi.com/t/378716"),
], ids=["heartbeat-lock", "hard-limit", "follower-mode"])
def test_a_decoded_status_flag_reaches_the_audit(sim, roles, condition, needle, url):
    """Three states that make a controller unable to do its job while every
    cadence check passes.

    Heartbeat lock: once a SPARK has seen a master -- a roboRIO, or the REV
    Hardware Client -- it ignores every other source until it is power-cycled, so
    a whole locked-out fleet audits clean and every conclusion drawn from "it did
    not move" is wrong (t/452062, t/403283).
    Hard limit: a misaligned data-port breakout board held a limit closed; the
    first advice the team got was to disable the hard limits, which this driver
    rightly refuses -- and refusing is only defensible if the audit says what is
    actually wrong (t/455481).
    Follower: a leader latched into follower mode fights its own commands about
    four times a second and survives a power cycle (t/378716, t/373252).
    """
    from sparksim import build_fleet

    bus = sim(build_fleet())
    condition(bus.controller(12))
    adm = attach(bus)
    status = sa.collect_status(bus, seconds=0.5)

    problems = sa.audit_problems(adm.inventory(1.0), {}, roles, status=status)

    about_12 = [p for p in problems if "12" in p]
    assert about_12 and any(needle in p.lower() for p in about_12), (
        f"id 12 is broadcasting the reason it cannot be driven ({url}) and the "
        f"audit said: {problems}")
    assert not [p for p in problems if "13" in p and "12" not in p], (
        "no other controller is in this state")


@pytest.mark.parametrize("applied, amps, why", [
    (0.62, 0.03, "62% output commanded and no current drawn: a dead output stage "
                 "that still reports the output it believes it commands "
                 "(https://www.chiefdelphi.com/t/422153)"),
    (0.079, 40.0, "near-zero output while pulling the configured 40 A: a mechanism "
                  "clamped by the smart current limit, no fault and no warning "
                  "raised (https://www.chiefdelphi.com/t/491331)"),
], ids=["dead-output-stage", "clamped-at-current-limit"])
def test_applied_output_is_correlated_against_the_current_it_produced(
        sim, roles, applied, amps, why):
    """The two halves of one missing rule, with their own negative controls.

    No fault bit is set in either case, so this is a plausibility check on three
    already-decoded fields or it is nothing. An idle controller and one doing real
    work at its limit must stay clean, or the rule is noise.
    """
    bus = sim([spark(12, applied=applied, amps=amps, volts=12.4),
               spark(13, applied=0.0, amps=0.2, volts=12.4),      # idle
               spark(14, applied=0.85, amps=40.0, volts=12.4)])   # working hard
    adm = attach(bus)
    status = sa.collect_status(bus, seconds=0.5)

    problems = sa.audit_problems(adm.inventory(1.0), {}, roles, status=status)

    assert any("12" in p for p in problems), f"{why}, and the audit said {problems}"
    assert not any("13" in p for p in problems), "an idle controller is not stalled"
    assert not any("14" in p for p in problems), (
        "a controller doing real work at its limit is not clamped out")


def test_a_different_spark_model_answering_a_configured_id_is_flagged(sim, roles,
                                                                      serials):
    """'CANSparkMax object created for CAN ID 10, which is not a SPARK MAX' -- the
    host detected over the bus that the device was a different model.
    https://www.chiefdelphi.com/t/475584

    The expectation exists too: base.controller_type and the baseline meta both
    record which model belongs on this bus. Nothing compares them.
    """
    bus = sim([spark(d) for d in range(10, 18)])
    bus.controller(10).model = 2                      # a SPARK MAX among Flexes
    adm = attach(bus)
    status = sa.collect_status(bus, seconds=0.5)
    assert status[10]["status0"]["spark_model"] == 2, "the fixture broadcast it"

    problems = sa.audit_problems(adm.inventory(1.0), {}, roles, serials,
                                 status=status, controller_type="sparkflex")

    assert any("10" in p and "model" in p.lower() for p in problems), (
        f"a foreign controller model answered id 10 and audited clean: {problems}")


# == catalogue B4 and SIG-THERMAL =============================================

def test_a_follower_is_reported_with_the_firmware_that_decides_its_polarity(sim):
    """Catalogue B4. CD 363633: two NEOs on one elevator, one SPARK MAX inverted
    and following the other. After a firmware and API upgrade both controllers
    ran the same direction regardless of the inversion set in code, and there was
    no way to alter the inversion from the client. Rolling both back restored it.
    https://www.chiefdelphi.com/t/363633

    Reproducing the change needs two firmware versions on one controller, which
    is why the hardware injection is refused. What makes the failure findable is
    that both halves reach the operator at once: the follower flag, which rides
    on every STATUS_1, and the firmware version, which GET_FIRMWARE answers. A
    follower reported without its firmware leaves the reader unable to connect a
    direction that reversed to an upgrade that caused it.
    """
    bus = sim([spark(12, follower=True), spark(13)])
    adm = attach(bus)
    status = sa.collect_status(adm.bus, seconds=0.3)

    assert status[12]["status1"]["is_follower"] is True
    assert status[13]["status1"]["is_follower"] is False, "the control"

    problems = sa.status_problems(status, {12: "steer/RB", 13: "drive/RB"})
    follower = [p for p in problems if "follower" in p.lower()]

    assert follower, f"the follower bit reached no finding: {problems}"
    assert adm.firmware(12)[0] is not None, (
        "On 26.1.6 GET_FIRMWARE answers at dlc 0 while a parameter read needs "
        "dlc 8, and it dates a polarity change to an upgrade")


def test_a_thermal_foldback_that_recovers_alone_leaves_a_readable_trace(sim):
    """Catalogue SIG-THERMAL. The signature is a controller whose applied output
    falls to zero while its frames keep flowing, and which recovers with no
    operator action and no sticky fault. It is refused on hardware because the
    injection is a motor held in stall until it is hot, which needs a setpoint
    and a loaded mechanism, and the recovery only appears after the event.

    In the simulator the event is producible, and this pins the two facts an
    operator needs: the controller never left the bus, so absence of frames is
    not the tell, and the temperature bit was raised while it lasted. Whether
    that bit survives a collect_status window that outlives the event is a
    separate open gap, pinned by
    test_a_fault_that_recovered_inside_the_window_survives_collect_status.
    """
    bus = sim([spark(12)])
    bus.thermal_foldback(12, at=0.2, temp_c=95, duration=0.4)

    during = sa.collect_status(attach(bus).bus, seconds=0.35)
    after = sa.collect_status(attach(bus).bus, seconds=0.5)

    assert "temperature" in during[12]["status1"]["faults"], (
        f"the foldback raised no fault bit: {during[12]['status1']}")
    assert during[12]["status0"]["motor_temp_c"] == 95
    assert "temperature" not in after[12]["status1"]["faults"], (
        "it must clear on its own; a latched bit would be a different failure")
    assert 12 in after, "the controller kept broadcasting throughout"


# -- the runtime motor object keys on firmware too ----------------------------


def test_a_25_plus_sparkmax_gets_its_status_frames_captured_at_all():
    """The routing half of the 0x06CE defect, still live until.

    `SparkBus.bus_monitor` matched incoming frames against `ctrl._s0_api` and
    `_s1_api`, which come from `_CONFIGS[controller_type]`. For a SPARK MAX that
    is 0x60/0x61. A MAX on firmware 25+ broadcasts 0x2E0/0x2E1, so NEITHER
    matched and no status frame was ever captured.

    That is worse than a misdecode: telemetry reads empty forever while
    `_last_seen` keeps updating from the same frames, so the DriveTrain power
    watchdog calls the controller healthy the whole time.
    """
    from sparklib.controller import Controller, SPARK_MAX

    from sparklib.controller import _CONFIGS

    c = Controller.__new__(Controller)
    c.controller_type = SPARK_MAX
    c.id = 12
    c._status0_raw = c._status1_raw = None
    c._observed_generation = None
    c._apply_config(_CONFIGS[SPARK_MAX])
    assert c._s0_api == 0x60, "premise: a declared MAX starts on the legacy apis"

    # what a 25+ MAX actually puts on the wire
    c.note_frame_api(0x2E0)
    assert c._observed_generation == "fw25+", (
        "a frame on 0x2E0 is 25+ by definition; the api is implemented at "
        "25.0.0 and exists on no earlier firmware")
    assert c._modern() is True, (
        "the declared product is SPARK MAX, but the frames say 25+ and the "
        "frames are what the decode has to follow")
    assert (c._s0_api, c._s1_api, c._s2_api) == (0x2E0, 0x2E1, 0x2E2), (
        "the generation was observed but the layout stayed on the declared "
        "product, so the api keys, the velocity and position sources and the "
        "setpoint frames all kept the wrong generation for the life of the "
        "object while _modern() reported the right one")
    assert c._velocity_api == 0x2E2 and c._position_api == 0x2E2, (
        "velocity and position still read from the pre-25 frames")


def test_a_pre25_controller_is_still_read_with_the_legacy_layout():
    """The other half. Observing the generation must not lose pre-25."""
    from sparklib.controller import Controller, SPARK_MAX

    from sparklib.controller import _CONFIGS

    c = Controller.__new__(Controller)
    c.controller_type = SPARK_MAX
    c.id = 12
    c._status0_raw = c._status1_raw = None
    c._observed_generation = None
    c._apply_config(_CONFIGS[SPARK_MAX])

    c.note_frame_api(0x061)          # exists only pre-25
    assert c._observed_generation == "pre25"
    assert c._modern() is False
    assert (c._s0_api, c._s1_api, c._s2_api) == (0x60, 0x61, 0x62), (
        "a pre-25 controller was re-pointed away from the legacy layout")


def test_the_ambiguous_legacy_frame_does_not_decide_the_generation():
    """0x060 is real data pre-25 and a pinned all-ones beacon on 25.0.0-26.1.3,
    so seeing it settles nothing. Deciding on it would put a 25+ controller on
    the legacy decode and read its beacon as sixteen faults."""
    from sparklib.controller import Controller, SPARK_MAX

    c = Controller.__new__(Controller)
    c.controller_type = SPARK_MAX
    c._observed_generation = None

    c.note_frame_api(0x060)
    assert c._observed_generation is None, (
        "0x060 alone is ambiguous and must not set a generation")


def test_the_declared_product_is_only_a_fallback_before_any_frame():
    from sparklib.controller import (Controller, SPARK_MAX,
                                                        SPARK_FLEX)
    for declared, expect in ((SPARK_FLEX, True), (SPARK_MAX, False)):
        c = Controller.__new__(Controller)
        c.controller_type = declared
        c._observed_generation = None
        assert c._modern() is expect, (
            "before a frame arrives the declared type is all there is, and "
            "every raw buffer is None so nothing is decoded from it anyway")


def _routing_bus(controller_type):
    """A SparkBus with one controller, wired for routing only. No socket."""
    from types import SimpleNamespace
    from sparklib.can_bus import SparkBus
    from sparklib.controller import Controller

    b = SparkBus.__new__(SparkBus)
    c = Controller.__new__(Controller)
    c.controller_type = controller_type
    c.id = 12
    from sparklib.controller import _CONFIGS
    c._status0_raw = c._status1_raw = None
    c._observed_generation = None
    c._last_seen = 0.0
    # The real constructor's layout step. Setting the api keys by hand left the
    # double without _active_config, so it could not model a controller
    # re-pointing itself when the wire contradicts the declared product.
    c._apply_config(_CONFIGS[controller_type])
    b.controllers = {12: c}
    b.observed_ids = set()
    return b, c


def _msg(api, dev=12, data=b"\x01\x02\x03\x04\x05\x06\x07\x08"):
    from types import SimpleNamespace
    arb = (2 << 24) | (5 << 16) | (api << 6) | dev
    return SimpleNamespace(arbitration_id=arb, data=data)


def test_the_router_captures_a_25_plus_frame_from_a_declared_sparkmax():
    """The routing defect at its own call site.

    Tested through `SparkBus.route_frame`, not through the predicate, because a
    test of `_modern()` alone cannot see the router matching the wrong api. That
    is exactly what happened: reverting the router to a product-only match left
    every predicate test green.
    """
    from sparklib.controller import SPARK_MAX

    bus, ctrl = _routing_bus(SPARK_MAX)
    assert ctrl._s0_api == 0x60, "premise: a declared MAX is configured for 0x60"

    bus.route_frame(_msg(0x2E0))          # what a 25+ MAX actually sends
    assert ctrl._status0_raw is not None, (
        "a 25+ SPARK MAX broadcast STATUS_0 on 0x2E0 and the router dropped it "
        "because _s0_api is 0x60. No telemetry is captured at all, while "
        "_last_seen still updates and the power watchdog calls it healthy")

    bus.route_frame(_msg(0x2E1))
    assert ctrl._status1_raw is not None, "STATUS_1 on 0x2E1 was dropped too"
    assert ctrl._modern() is True
    assert ctrl._s0_api == 0x2E0, (
        "the router captured the frame but the controller kept the declared "
        "product's layout, so its setpoints still go out as pre-25 frames")


def test_the_router_still_captures_a_pre25_frame():
    """Accepting the modern apis must not stop the legacy ones arriving."""
    from sparklib.controller import SPARK_MAX

    bus, ctrl = _routing_bus(SPARK_MAX)
    bus.route_frame(_msg(0x60))
    bus.route_frame(_msg(0x61))

    assert ctrl._status0_raw is not None, "pre-25 STATUS_0 on 0x060 was dropped"
    assert ctrl._status1_raw is not None, "pre-25 STATUS_1 on 0x061 was dropped"
    assert ctrl._modern() is False, "0x061 exists only pre-25"


def test_the_router_records_presence_for_an_unconfigured_id():
    """Unchanged behaviour, pinned so the extraction did not lose it."""
    from sparklib.controller import SPARK_MAX

    bus, _ = _routing_bus(SPARK_MAX)
    bus.route_frame(_msg(0x2E0, dev=30))
    assert 30 in bus.observed_ids, (
        "a squatter on an unexpected id must stay visible to the id audit")
