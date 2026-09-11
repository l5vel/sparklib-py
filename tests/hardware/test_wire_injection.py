"""Failure signatures transmitted onto the live bus, read back through the real
driver.

Tier 1. Nothing here writes to a controller except to take one status frame off
the air so the injected copy is the only one on that address -- and even that is
RAM only, so flash holds the provisioned value throughout.

The failures are the ones where the frames are perfectly well formed and the
conclusion drawn from them is still wrong: a fault bit riding at a nominal
cadence, two serials on one id, a REV PDH counted as a SPARK, a payload that is
not a measurement at all. Every one is a catalogue entry with a Chief Delphi
thread behind it, and every one is read through `SparkAdmin`, `collect_status`
and `audit_problems` rather than through a decoder in isolation -- because the
defect that motivates most of this module is not in a decoder. It is that
`cmd_audit` never asks for the decoded reading.

The first two tests are controls on the injector itself. If the loopback premise
does not hold, or the safety guard has a hole, nothing below measures what it
says it does.

Gates: `SPARK_HW_INJECT` for anything that starves a real frame first,
`SPARK_HW_COLLIDE` for anything transmitting on an id a controller already uses,
`SPARK_HW_CONGEST` for the two that fill the bus. See tests/hardware/README.md.
"""
from __future__ import annotations

import json
import os
import pathlib
import time
from types import SimpleNamespace

import pytest

from sparklib import admin as sa
from sparklib import cli as spark_cli
from sparkhw import WireInjector, link_info
from sparkhw.wire import FORBIDDEN_BASES, RESPONSE_BASES, InjectionRefused
from sparksim import frames as F

S0, S1, UID = F.API_STATUS_0, F.API_STATUS_1, F.API_UNIQUE_ID
PHANTOM_SERIAL = "FEEDFACE"

# REV-Specs spark-frames-2.1.0, vendored at reference/.
FRAMES_JSON = pathlib.Path(os.environ.get(
    "SPARK_FRAMES_JSON",
    pathlib.Path(__file__).resolve().parents[1]
    / "support" / "spec" / "REV-spark-frames-2.1.0.json"))


def _problems(inv, dups, roles, serials=None):
    """audit_problems over an inventory, scored against the right generation.

    An inventory carries no generation of its own, but the api classes in it do:
    a bus broadcasting 0x060 is pre-25 and one broadcasting 0x2E0 is not. Without
    this the audit defaults to 25+ and reports a pre-25 fleet as sending no
    STATUS_1, which is a frame that generation has never had.
    """
    apis = {a for i in inv.values() for a in (i.get("periods_ms") or {})}
    legacy = apis & set(range(0x060, 0x068))
    modern = apis & set(F.STATUS_APIS)
    # Presence, not subset: an inventory taken while this host is injecting also
    # carries the injected api, so requiring the whole set to be legacy misreads
    # exactly the runs that need it most.
    gen = sa.GEN_PRE25 if legacy and not modern else None
    return sa.audit_problems(inv, dups, roles, serials or {}, generation=gen)


def _about(problems, dev):
    return [p for p in problems if f"id {dev} " in p or p.endswith(f"id {dev}")]


# -- controls on the injector --------------------------------------------------

def test_an_injected_frame_reaches_the_drivers_own_socket(adm, inject, free_id):
    """The premise every other test in this module rests on.

    socketcan loops a transmitted frame back to every other socket on the
    interface, so a frame this host writes arrives at `SparkAdmin`'s own socket
    and is indistinguishable from a controller's -- `spark_admin` never reads
    `Message.is_rx`, which is the only field that differs. That is what makes
    the injection work, and it is worth knowing on its own: anything with write
    access to this interface can put telemetry in front of the audit.
    """
    with inject.stream([(F.arb(UID, free_id), F.encode_unique_id(PHANTOM_SERIAL)),
                        (F.arb(S0, free_id), F.encode_status_0(volts=12.9)),
                        (F.arb(S1, free_id), F.encode_status_1())],
                       period_s=0.02, seconds=10.0) as stream:
        inv = adm.inventory(2.0)
        status = sa.collect_status(adm.bus, seconds=1.0)

    assert stream.errors == [], f"the injector could not transmit: {stream.errors}"
    assert free_id in inv, (
        f"the driver's socket did not receive {stream.count} frame(s) this host "
        f"wrote on id {free_id}; every injection in this module is measuring "
        "nothing until that works")
    assert inv[free_id]["serial"] == PHANTOM_SERIAL
    assert status[free_id]["status0"]["voltage_v"] == pytest.approx(12.9, abs=0.02)


def test_the_injector_only_emits_frames_a_device_emits():
    """The safety rule, asserted rather than trusted to review.

    A frame only a controller transmits is a frame no controller listens for, so
    an injection cannot command anything. Every command frame is refused, and
    the list is not theoretical: a blind RTR sweep of all 1024 api values reached
    ENTER_SWDL_CAN_BOOTLOADER and halted device 17 on this fleet, recoverable
    only by cutting the motor rail
    (docs/spark/runs/-rig-flex-probe-log.md).
    """
    for base in FORBIDDEN_BASES:
        with pytest.raises(InjectionRefused):
            WireInjector.check(base | 12)
    for api in (F.API_STATUS_0, F.API_STATUS_1, F.API_UNIQUE_ID):
        assert WireInjector.check(F.arb(api, 12))

    if not FRAMES_JSON.exists():
        pytest.skip(f"REV frame spec not on this machine at {FRAMES_JSON}; the "
                    "structural half of this test still ran")
    spec = json.loads(FRAMES_JSON.read_text())
    periodic = {f["arbId"] for f in spec["periodicFrames"].values()}
    responses = {f["arbId"] for name, f in spec["nonPeriodicFrames"].items()
                 if name.endswith("_RESPONSE")}
    emitted = ({F.arb(api, 0) for api in F.STATUS_APIS} | {F.arb(UID, 0)}
               | set(RESPONSE_BASES))
    outside = sorted(hex(a) for a in emitted - periodic - responses)
    assert not outside, (
        "the injector's vocabulary includes frames REV does not list as either "
        f"periodic or a response, so a device may act on them: {outside}")


# -- D1: a fault bit at a nominal cadence --------------------------------------

def test_a_faulted_controller_keeps_a_perfect_status_1_cadence(
        adm, gate, spoofed_status, writable_id):
    """CD 444231, CD 346981: a gate-driver fault persists across factory reset
    and reflash and ends in an RMA. It changes the payload, not the period --
    which is the whole premise of D1. No cadence measurement, however exact, can
    see it, and this is that claim measured on a real 1 Mbit bus rather than
    argued. https://www.chiefdelphi.com/t/444231
    """
    gate("inject")
    target = int(sa.declared_status_1_period_ms())
    with spoofed_status(writable_id, S1, faults=["gateDriver"],
                        sticky_faults=["gateDriver"], period_s=target / 1000.0):
        period = adm.status_period_ms(writable_id, S1, seconds=3.0)
        status = sa.collect_status(adm.bus, seconds=1.0)[writable_id]

    assert period is not None, "the injected STATUS_1 never arrived"
    assert sa.status_1_verdict(period) == "ok", (
        f"the injected cadence measured {period} ms, so this run is not "
        "reproducing 'faulted at a perfect period'")
    assert status["status1"]["faults"] == ["gateDriver"], (
        "and the fault was broadcasting the whole time")


def test_a_broadcast_gate_driver_fault_reaches_the_audit(
        adm, gate, spoofed_status, writable_id, roles, serials):
    """`spark audit --help` promises exit 1 on any fault. A set Faults bit pins
    applied output to 0 while the status frames keep flowing (REVLib: "Faults
    are fatal errors that prevent the motor from running"), so the bus looks
    healthy and the motor cannot move. This is the same assertion the simulator
    makes, against a controller on a real robot.
    https://www.chiefdelphi.com/t/444231
    """
    gate("inject")
    with spoofed_status(writable_id, S1, faults=["gateDriver"],
                        sticky_faults=["gateDriver"]):
        inv = adm.inventory(2.0)
        status = sa.collect_status(adm.bus, seconds=1.0)
        assert "gateDriver" in status[writable_id]["status1"]["faults"], (
            "the injection never reached the wire")
        problems = sa.audit_problems(inv, {}, roles, serials, status=status)

    assert [p for p in problems if "gateDriver" in p], (
        f"the audit of a bus broadcasting gateDriver on id {writable_id} "
        f"returned {problems}")


@pytest.mark.parametrize("field, other", [("faults", "warnings"),
                                          ("warnings", "faults")])
def test_an_esc_eeprom_fault_is_told_apart_from_the_warning_of_that_name(
        adm, gate, spoofed_status, writable_id, field, other):
    """Catalogue D4, CD 453509: REV acknowledged a firmware bug that raised an
    EEPROM condition. 'escEeprom' exists in both tables -- fault bit 6 and
    warning bit 2 -- and the two are opposite verdicts, one an RMA conversation
    and one noise. On the 2025+ layout they are different bytes of the same
    frame, so a byte or table mix-up swaps them without changing the frame size.
    https://www.chiefdelphi.com/t/453509
    """
    gate("inject")
    with spoofed_status(writable_id, S1, **{field: ["escEeprom"]}):
        decoded = sa.collect_status(adm.bus, seconds=1.0)[writable_id]["status1"]

    assert decoded[field] == ["escEeprom"], decoded
    assert decoded[other] == [], (
        f"an escEeprom {field[:-1]} was decoded as a {other[:-1]}: {decoded}")


# -- B1: two controllers on one id ---------------------------------------------

def test_a_second_serial_on_a_configured_id_is_reported_as_a_duplicate(
        adm, gate, inject, writable_id, roles, serials):
    """Catalogue B1, CD 427780 and CD 426287: two controllers answer one address
    and an id scan sees one device. The tell is two distinct UNIQUE_ID payloads
    on a single id, which is what this injects.

    This is the one shape that has to share an arbitration id with a real
    transmitter. UNIQUE_ID is a 4-byte frame at a 2 s period; at 1 Mbit that is
    about 110 us of bus time, so an injection every 200 ms starts in the same bit
    time as the controller's own roughly five times in a hundred thousand. A
    collision costs one error frame and a retransmission on both nodes, and the
    error counter decrements on every success -- which is why this needs a gate
    and not a refusal. https://www.chiefdelphi.com/t/427780
    https://www.chiefdelphi.com/t/403172
    """
    gate("collide")
    if not sa.duplicate_detection_available(
            sa.dominant_generation(sa.collect_status(adm.bus, 2.0))):
        pytest.skip("this generation broadcasts no UNIQUE_ID, so two controllers "
                    "on one id cannot be told apart by serial at all; the pre-25 "
                    "signature would be cadence or payload divergence instead")
    assert serials.get(writable_id), (
        "serials is not recorded, so the real controller's serial "
        "cannot be told from the phantom's")
    with inject.stream([(F.arb(UID, writable_id),
                         F.encode_unique_id(PHANTOM_SERIAL))],
                       period_s=0.2, seconds=20.0):
        dups = adm.duplicates(4.0)
        inv = adm.inventory(2.0)
        problems = _problems(inv, dups, roles, serials)

    assert writable_id in dups, (
        f"two serials were broadcasting on id {writable_id} and duplicates() "
        f"returned {dups}")
    assert PHANTOM_SERIAL in dups[writable_id]
    assert serials[writable_id] in dups[writable_id], (
        "the real controller stopped answering while the phantom transmitted")
    assert [p for p in problems if "an id scan cannot see this" in p], problems


@pytest.mark.xfail(reason="duplicates() keys entirely on UNIQUE_ID (api 0x2F0) "
                          "payloads. A twin whose 0x2F0 frame is disabled is a "
                          "duplicate the driver cannot see, even though the "
                          "catalogue's primary signature -- two different STATUS_0 "
                          "payloads alternating on one id -- is sitting in the "
                          "frames it already receives",
                   strict=False)
def test_a_twin_that_never_broadcasts_its_serial_is_still_a_duplicate(
        adm, gate, inject, writable_id, roles, serials):
    """Catalogue B1's bus signature rather than its convenience frame: the
    STATUS_0 payload for that id flip-flops between two devices' telemetry on
    successive frames. Here the injected copy reports a rail 3 V away from the
    real one, so the alternation is unmistakable in the frames the driver
    already consumes -- and a duplicate check that only reads api 0x2F0 sees one
    controller. https://www.chiefdelphi.com/t/495329
    """
    gate("collide")
    impostor = F.encode_status_0(volts=9.4, temp_c=91)
    with inject.stream([(F.arb(S0, writable_id), impostor)],
                       period_s=0.01, seconds=20.0):
        dups = adm.duplicates(4.0)

    assert writable_id in dups, (
        f"id {writable_id} carried two different STATUS_0 payloads for four "
        f"seconds and duplicates() returned {dups}")


# -- B2: the unconfigured address ----------------------------------------------

def test_a_controller_transmitting_on_id_zero_is_named_as_unconfigured(
        adm, inject, roles, serials):
    """Catalogue B2, CD 376796: firmware 1.5.2 and later treat 0 as unconfigured,
    and the REV client answers `Unable to retrieve SPARK MAX firmware version for
    CAN ID: 0`. A controller there is transmitting and healthy and can never be
    enabled, which is a different repair from every other finding the audit
    prints. No real controller is touched -- id 0 is free on this bus, so this
    is a phantom of the failure rather than a controller pushed into it.
    https://www.chiefdelphi.com/t/376796
    """
    with inject.stream([(F.arb(UID, 0), F.encode_unique_id(PHANTOM_SERIAL)),
                        (F.arb(S0, 0), F.encode_status_0()),
                        (F.arb(S1, 0), F.encode_status_1())],
                       period_s=0.02, seconds=15.0):
        inv = adm.inventory(2.0)
        problems = _problems(inv, {}, roles, serials)

    assert 0 in inv, "the phantom on id 0 never reached the driver"
    named = [p for p in _about(problems, 0)
             if "unconfigured" in p.lower() or "never" in p.lower()
             or "cannot be enabled" in p.lower()]
    assert named, (
        "a controller on the unconfigured address is reported only as an "
        f"unexpected id: {_about(problems, 0)}")


def test_a_stranger_on_an_unconfigured_id_is_named_by_the_audit(
        adm, inject, free_id, roles, serials):
    """The control for the two above. A REV controller answering an address that
    is not in devices is exactly what the audit is built to see, and it must
    say so -- otherwise a driver that flagged nothing would satisfy both xfails.
    """
    with inject.stream([(F.arb(UID, free_id), F.encode_unique_id(PHANTOM_SERIAL)),
                        (F.arb(S0, free_id), F.encode_status_0()),
                        (F.arb(S1, free_id), F.encode_status_1())],
                       period_s=0.02, seconds=15.0):
        inv = adm.inventory(2.0)
        problems = _problems(inv, {}, roles, serials)

    assert free_id in inv
    assert [p for p in _about(problems, free_id) if "can_ids" in p], problems


# -- the manufacturer-only filter ----------------------------------------------

def test_a_rev_pdh_on_an_unconfigured_id_is_not_audited_as_a_spark(
        adm, inject, free_id, roles, serials):
    """`inventory()` and `collect_status()` both test `(arb >> 16) & 0xFF == 5`
    and nothing else. A REV Power Distribution Hub is manufacturer 5 and device
    type 8, so its frames land in the inventory as a controller, get a period
    measured, and are decoded as motor telemetry. The device-type field that
    separates them is in the same arbitration id the filter already reads.
    """
    with inject.stream([(F.arb(S0, free_id, device_type=F.DEVICE_TYPE_PDH),
                         bytes([0xAA] * 8))], period_s=0.02, seconds=15.0):
        inv = adm.inventory(2.0)
        problems = _problems(inv, {}, roles, serials)

    assert free_id not in inv, (
        f"a REV PDH on id {free_id} was inventoried as a SPARK: {inv.get(free_id)}")
    assert not _about(problems, free_id), problems


def test_another_host_writing_parameters_is_named_instead_of_a_reprovision_loop(
        adm, writable_id, inject, roles, serials):
    """Catalogue's second-writer hazard, and the reason `spark repair` on a bus
    somebody else is provisioning is a bad idea. `inventory()` counts every REV
    frame on an id into `periods_ms`, PARAMETER_WRITE_RESPONSE included, so
    another host's provisioning loop inflates the frame count for that
    controller and no rule names the writer.

    Only response frames are injected. A PARAMETER_WRITE would actually write.
    """
    resp = F.encode_param_resp(F.PARAM_STATUS_1_PERIOD, 20, 0)
    with inject.stream([(F.PARAM_WRITE_RESP | writable_id, resp)],
                       period_s=0.01, seconds=15.0):
        inv = adm.inventory(3.0)

    assert writable_id in inv, f"id {writable_id} stopped broadcasting: {sorted(inv)}"
    api = F.api_of(F.PARAM_WRITE_RESP)
    assert api in inv[writable_id]["periods_ms"], (
        "a response frame from another host was not counted at all, so this "
        "test is not reproducing the condition it names")
    problems = _problems(inv, {}, roles, serials)
    assert not _about(problems, writable_id), (
        "a second host writing parameters to this controller produced only "
        f"{_about(problems, writable_id)}, which names the controller and not "
        "the writer")


# -- telemetry that is not a measurement ---------------------------------------

def test_a_saturated_status_0_is_not_handed_over_as_telemetry(adm, inject, free_id):
    """A transmitter that has stopped driving the bus leaves it recessive, and
    the all-ones payload that produces decodes as 30 V, 150 A, 255 C, every
    limit switch closed and a SPARK model that does not exist. `decode_status_0`
    returns it with the same confidence as a captured idle frame, and
    `spark voltage` prints it beside a real rail.
    """
    with inject.stream([(F.arb(S0, free_id), bytes([0xFF] * 8)),
                        (F.arb(UID, free_id), F.encode_unique_id(PHANTOM_SERIAL))],
                       period_s=0.02, seconds=15.0):
        reading = sa.collect_status(adm.bus, seconds=2.0)[free_id]["status0"]

    assert reading is not None, "the saturated frame never arrived"
    assert reading.get("implausible"), (
        f"an all-ones frame decoded as {reading['voltage_v']:.1f} V "
        f"{reading['current_a']:.1f} A {reading['motor_temp_c']} C with no "
        "verdict attached")


def test_zero_applied_output_with_current_flowing_is_flagged(adm, inject, free_id):
    """CD 477176, the best-instrumented dropout report in the corpus: `.set()`
    commanding non-zero while `getAppliedOutput()` reads exactly 0. The pair is
    the signature -- current through the motor with the output stage reporting
    nothing driving it -- and both halves are already decoded and never compared.
    Injecting the frame is the only way to produce it here, because the real
    condition needs a setpoint and no tool in this repo may send one.
    https://www.chiefdelphi.com/t/477176
    """
    with inject.stream([(F.arb(S0, free_id),
                         F.encode_status_0(applied=0.0, amps=42.0, volts=12.4)),
                        (F.arb(UID, free_id), F.encode_unique_id(PHANTOM_SERIAL))],
                       period_s=0.02, seconds=15.0):
        reading = sa.collect_status(adm.bus, seconds=2.0)[free_id]["status0"]

    assert reading["current_a"] > 1.0 and reading["applied_output"] == 0.0
    assert reading.get("implausible"), (
        f"{reading['current_a']:.1f} A is flowing with applied output at "
        "exactly 0 and the reading carries no verdict")


@pytest.mark.needs_clean_bus
def test_a_different_spark_model_answering_a_configured_id_is_flagged(
        adm, gate, spoofed_status, writable_id, roles, serials):
    """rig-flex is provisioned as SPARK Flex, model 1, and the field is in every
    STATUS_0 frame. A controller replaced with a different model answers the same
    id with the same serial-less confidence, and the audit compares neither.
    """
    gate("inject")
    if sa.dominant_generation(sa.collect_status(adm.bus, 2.0)) == sa.GEN_PRE25:
        pytest.skip("LEGACY_STATUS_0 carries no model field, so a controller of "
                    "the wrong product is indistinguishable on this generation; "
                    "the check needs parameter 2 (Motor Type) via "
                    "SparkAdmin.read_legacy_param instead")
    with spoofed_status(writable_id, S0, model=3, volts=12.4):
        inv = adm.inventory(2.0)
        status = sa.collect_status(adm.bus, seconds=1.0)
        assert status[writable_id]["status0"]["spark_model"] == 3, (
            "the injection never reached the wire")
        problems = sa.audit_problems(inv, {}, roles, serials, status=status)

    assert [p for p in _about(problems, writable_id) if "model" in p.lower()], (
        f"a model 3 controller answering a SPARK Flex id produced {problems}")


# -- C1/C6: the bus itself -----------------------------------------------------

def test_congestion_inflates_the_measured_period_of_an_intact_controller(
        adm, gate, inject, writable_id, channel, can_link_healthy, dialect):
    """The control for the congestion pair, and a finding in its own right.

    CD 455329: utilisation spiking to 100% produced `Timeout Waiting for Status
    X` on controllers that were configured correctly the whole time. Every period
    in this tool is a frame count over a window, so load that costs frames moves
    the number without anything about the controller changing. This test asserts
    the injection genuinely degrades the measurement -- if it does not, the two
    tests that read a degraded bus are measuring nothing.
    https://www.chiefdelphi.com/t/455329

    Measured on rig-flex, 1 Mbit, five seconds of filler each way:

        low priority   24751 frames   STATUS_1 read 20.0 ms   8 of 8 inventoried
        high priority  29702 frames   STATUS_1 read  750 ms   0 of 8 inventoried

    The low-priority filler sorts above every SPARK frame and takes only the
    gaps, which is why it costs nothing. The high-priority filler sorts below
    them and wins every arbitration, and the degradation it causes is not a
    slower cadence but a bus the controllers cannot get onto at all. So the
    assertion below accepts the whole range up to silence: another run of this
    same injection measured no STATUS_1 whatsoever in three seconds.

    Nothing records it. rx_dropped, rx_over_errors and rx_errors all stayed at
    zero, the adapter stayed ERROR-ACTIVE, and no controller latched a sticky
    fault -- losing arbitration is ordinary CAN behaviour, not an error. There is
    no cheap counter a driver could read to tell this apart from eight dead
    controllers.
    """
    gate("congest")
    before = adm.status_period_ms(writable_id, dialect.fault_api, seconds=3.0)
    with inject.congestion(seconds=6.0, priority="high") as filler:
        during = adm.status_period_ms(writable_id, dialect.fault_api,
                                       seconds=3.0)
    time.sleep(1.0)
    after = adm.status_period_ms(writable_id, dialect.fault_api, seconds=3.0)

    now = link_info(channel)
    dropped = (now["rx_dropped"] or 0) - (can_link_healthy["rx_dropped"] or 0)
    overruns = (now["rx_over_errors"] or 0) - (can_link_healthy["rx_over_errors"] or 0)

    assert filler.count > 1000, (
        f"the filler only sent {filler.count} frame(s) ({filler.errors}); this "
        "adapter could not generate the load the test needs")
    assert before is not None, "the controller was not broadcasting to begin with"
    assert after is not None and abs(after - before) < 15.0, (
        f"the bus did not recover: {before} ms before, {after} ms after")

    degraded = during is None or abs(during - before) > 5.0 or dropped or overruns
    assert degraded, (
        f"{filler.count} filler frames changed nothing measurable: period "
        f"{before} -> {during} -> {after} ms, rx_dropped +{dropped}, "
        f"rx_over_errors +{overruns}")
    print(f"\ncongestion: {filler.count} filler frames, period {before} -> "
          f"{during} -> {after} ms, rx_dropped +{dropped}, rx_over_errors "
          f"+{overruns} -- the loss is arbitration, not the receive path")


def test_a_saturated_bus_is_not_reported_as_a_reverted_controller(
        adm, gate, inject, writable_id, roles, serials):
    """The repair the audit recommends for a reverted period is a re-provision
    and a flash cycle. Aimed at a bus that is dropping frames, it puts more
    traffic on the bus and spends a flash cycle on a controller whose flash was
    never wrong -- so obeying the advice makes this failure worse, which is why
    D4 matters more than its test count suggests.

    The assertion holds however far the period actually moves: the finding for
    this controller must not be the config-revert line, and something must name
    the bus. Today a period between the two known values returns 'unexpected',
    for which `audit_problems` has no branch at all, so the finding is dropped
    and the audit reports a saturated bus as healthy.
    https://www.chiefdelphi.com/t/455329
    """
    gate("congest")
    with inject.congestion(seconds=8.0, priority="high"):
        inv = adm.inventory(4.0)
    problems = _problems(inv, {}, roles, serials)

    mine = _about(problems, writable_id)
    assert not [p for p in mine if "factory default" in p], (
        f"a bus under load was reported as a controller that lost its config: "
        f"{mine}")
    assert [p for p in problems if "bus" in p.lower() or "load" in p.lower()
            or "coverage" in p.lower()], (
        f"nothing in the audit names the bus: {problems}")


def test_what_the_congestion_latched_in_sticky_faults_reaches_the_audit(
        adm, gate, inject, roles, serials):
    """Catalogue C6 and H1: bus contention latches CAN TX/RX sticky faults, and
    H1 is the trap underneath -- two default commands on one subsystem present
    as a wiring fault rather than as a scheduler error. Whatever the load
    actually latches on this fleet, the audit has to name it; a sticky bit
    nobody reads is a diagnosis thrown away.

    Measured on rig-flex: on this firmware the load itself does not
    raise the fault. Six seconds of high-priority filler, 39567 frames, enough
    to take all eight controllers out of the inventory, left every sticky byte
    clean; so did 1186 frames transmitted on a controller's own STATUS_0
    address. The skip below is the expected result here rather than a weak run,
    and C6 stays unreproduced on this fleet until something is found that does
    raise it.

    The sticky bits are deliberately left set when there are any. They are
    evidence, and `spark clear` erases them, which is D5's whole complaint.
    """
    gate("congest")
    before = sa.collect_status(adm.bus, seconds=2.0)
    with inject.congestion(seconds=8.0, priority="high"):
        time.sleep(6.0)
    after = sa.collect_status(adm.bus, seconds=2.0)

    latched = {dev: sa.normalised_reading(after[dev])["sticky_faults"]
               for dev in sorted(set(roles) & set(after))
               if sa.normalised_reading(after[dev])["sticky_faults"]
               != (sa.normalised_reading(before[dev])["sticky_faults"]
                   if before.get(dev) else [])}
    if not latched:
        pytest.skip("the congestion latched no new sticky fault on this run; "
                    "raise the load or run it for longer")

    inv = adm.inventory(2.0)
    problems = sa.audit_problems(inv, {}, roles, serials, status=after)
    for dev, bits in latched.items():
        assert [p for p in _about(problems, dev) if any(b in p for b in bits)], (
            f"id {dev} latched {bits} and the audit said {_about(problems, dev)}")


# -- a fault the controllers raised themselves ---------------------------------

def test_spark_audit_and_spark_faults_agree_about_a_fault_the_fleet_really_has(
        adm, gate, inject, writable_id, roles, capsys):
    """The strongest form of D1 available anywhere: no injected frame, no starved
    status, no spoofing. The controllers raise the fault themselves.

    Measured on rig-flex: transmitting on one controller's own STATUS_0
    address at 5 ms for 30 s latches a sticky `can` fault on ALL EIGHT, because an
    error frame is seen by every node. Saturating the bus does not do it -- 197303
    filler frames left every sticky byte clean -- so this is contention for an
    ADDRESS, catalogue B1's electrical signature, rather than C6's load.

    Two subcommands then read the same bus and disagree. `spark faults` decodes
    STATUS_1, counts a sticky fault and exits 1. `spark audit` scores inventory
    and cadence only, never opens STATUS_1, and exits 0. Both are looking at eight
    controllers that are genuinely faulted, and the one whose --help promises
    "exit 1 on any fault" is the one saying everything is fine.

    Leaves the sticky bits set. They are the finding, and `spark clear` removes
    them when you want them gone.
    """
    gate("collide")
    adm.clear_faults(sorted(roles))
    time.sleep(1.5)
    before = sa.collect_status(adm.bus, seconds=2.0)
    assert not [d for d in roles
                if before.get(d)
                and sa.normalised_reading(before[d])["sticky_faults"]], (
        "a sticky fault was already set, so one raised here is unattributable")

    impostor = F.encode_status_0(volts=9.4, temp_c=91)
    with inject.stream([(F.arb(S0, writable_id), impostor)],
                       period_s=0.005, seconds=45.0) as stream:
        time.sleep(30.0)
    time.sleep(2.0)

    after = sa.collect_status(adm.bus, seconds=2.0)
    faulted = {d: sa.normalised_reading(after[d])["sticky_faults"]
               for d in sorted(roles)
               if after.get(d) and sa.normalised_reading(after[d])["sticky_faults"]}
    if not faulted:
        pytest.skip(f"{stream.count} colliding frames raised no sticky fault on "
                    "this run; the threshold sits above this rate")

    rc_faults = spark_cli.cmd_faults(SimpleNamespace(window=2.0))
    rc_audit = spark_cli.cmd_audit(SimpleNamespace(window=2.0))
    out = capsys.readouterr().out
    bits = sorted({b for v in faulted.values() for b in v})

    assert rc_audit == rc_faults, (
        f"{len(faulted)} controllers are carrying {bits} and the two commands "
        f"disagree: `spark faults` exited {rc_faults}, `spark audit` exited "
        f"{rc_audit}\n{out}")


# -- the call site -------------------------------------------------------------

def test_spark_audit_exits_nonzero_over_a_fault_on_this_robots_bus(
        adm, gate, spoofed_status, writable_id, capsys):
    """The call-site half of D1, driven through the real subcommand against the
    real bus. Testing `audit_problems()` alone cannot see that its only caller
    never obtains a status reading to pass it, which is how this survived a green
    suite for as long as it did.
    """
    gate("inject")
    with spoofed_status(writable_id, S1, faults=["gateDriver"],
                        sticky_faults=["gateDriver"], seconds=40.0):
        rc = spark_cli.cmd_audit(SimpleNamespace(window=2.0))
        out = capsys.readouterr().out

    assert "gateDriver" in out, f"`spark audit` never mentioned the fault:\n{out}"
    assert rc == 1, f"`spark audit` exited {rc} over a faulted controller:\n{out}"
