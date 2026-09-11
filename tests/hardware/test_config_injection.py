"""Catalogue class A on real firmware: configuration written, answered, and not
necessarily applied.

Tier 2. These are the only tests in the suite that write to a controller, and
they write one thing: a status-frame period, on the one controller
`SPARK_HW_ID` nominates, never persisted. Flash holds the provisioned value
throughout, so cutting and restoring the motor rail is the backstop under every
test here even if the process is killed mid-run.

That narrowness is not caution for its own sake. Firmware 26.1.6 answers no
parameter reads, so for every other setting there is no way to prove a value was
put back -- and a suite that could not undo itself would be a worse failure than
the ones it hunts. Status 0 and Status 1 Period are the exceptions because their
effect is on the wire: the broadcast cadence is the read-back.

  A1  CD 456184: a config routine sent settings back to back faster than the
      device handled them, some writes went unanswered, and controllers came
      back at defaults with nothing on the bus complaining. The burst below is
      that routine, made idempotent -- every value written is the value the
      declared configuration already holds -- so the pacing is exercised and the
      controller's state does not move.
  C2/C3  a frame that stops for part of a window, and a frame that is off
      entirely. Both read as one number to `status_1_verdict`, and only one of
      them is a config problem.
  A4  the flash cycle nobody asked for.
  G2  what the coverage note means when it calls a status period unverifiable.

Gate: SPARK_HW_INJECT.
"""
from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest

from sparklib import admin as sa
from sparklib import cli as spark_cli
from sparkhw import StatusPeriodGuard
from sparksim import frames as F

S0, S1 = F.API_STATUS_0, F.API_STATUS_1
P159 = sa.PARAM_STATUS_1_PERIOD

# Parameters a pre-25 burst can round-trip WITHOUT changing the controller: each
# is read first and written back its own value, so the device ends where it
# started and only the write path is under test. None is in PROTECTED_PARAMS.
# Status periods are deliberately absent: they are not parameters on pre-25.
LEGACY_BURST_PARAMS = (6, 10, 45, 59, 63, 69, 70, 71, 121, 124)


_TAG_FOR_TYPE = {name: tag for tag, name in sa.LEGACY_PARAM_TYPE.items()}


def _legacy_roundtrip_burst(adm, dev):
    """Read then rewrite each parameter's own value. {param_id: response}.

    The type tag comes from the READ rather than from a guess, because the
    firmware validates it and refuses a mismatch with a non-zero status.
    """
    answers = {}
    for pid in LEGACY_BURST_PARAMS:
        current = adm.read_legacy_param(dev, pid, wait=1.0)
        if current is None or not current["ok"]:
            answers[pid] = None
            continue
        tag = _TAG_FOR_TYPE.get(current["type"])
        if tag is None:
            answers[pid] = None
            continue
        answers[pid] = adm.write_legacy_param(dev, pid, current["raw"],
                                              type_tag=tag, wait=1.0)
    return answers

# CD 456184: the field recovery was retries on every setting plus a read-back.
# CD 432129's sibling number, and the smallest gap this suite calls paced.
MIN_INTER_WRITE_GAP_S = 0.002

# A period that is neither the provisioned 20 ms nor REV's 250 ms default, so
# the verdict for it is the third one nothing reads. STARVED_PERIOD_MS, the
# value that takes a frame off the air without a frame this suite may not send,
# comes from sparkhw so that the conftest and this module cannot disagree.
NEITHER_VALUE_MS = 1000


def _declared_status_periods():
    """{param_id: value} for the status periods the declared config names."""
    settings = sa.motor_settings("drive")
    return {pid: int(s["value"]) for pid, s in sorted(settings.items())
            if pid in F.API_FOR_PERIOD_PARAM and s.get("value") is not None}


# -- A1: a write that is answered, and one that is not -------------------------

def test_a_provisioning_burst_is_answered_write_for_write(
        adm, gate, writable_id, period_guard, dialect):
    """CD 456184: "you need to have retries in your code for basically every
    setting, and also read the setting back to ensure that it's been set
    correctly." The failure was a burst of writes sent faster than the
    controller handled, some of which were never answered -- and the settings
    that went missing included kFF, so motors output 0 while the code believed
    it had commanded motion.

    Every value here is the one the declared configuration already holds, so the
    controller's state is the same before and after and only the pacing is under
    test. If a response goes missing on this firmware, that is CD 456184
    reproduced on this fleet rather than inferred from a thread.
    https://www.chiefdelphi.com/t/456184
    """
    gate("inject")
    if dialect.legacy:
        # Pre-25 answers parameter writes too, on api class 48, so the same
        # question is askable -- just not about status periods, which are not
        # parameters here. Each value is the one already in the device.
        answers = _legacy_roundtrip_burst(adm, writable_id)
        wanted = {pid: r for pid, r in answers.items()}
        unanswered = sorted(p for p, r in wanted.items() if r is None)
        assert not unanswered, (
            f"id {writable_id} did not answer a pre-25 parameter write for "
            f"{unanswered} in a burst of {len(LEGACY_BURST_PARAMS)}; that is CD "
            "456184's mechanism on this firmware")
        refused = {p: r["status"] for p, r in wanted.items() if not r["ok"]}
        assert not refused, f"the device refused writes it should accept: {refused}"
        mis_echoed = {p: (r["raw"], r["requested"]) for p, r in wanted.items()
                      if r["raw"] != r["requested"]}
        assert not mis_echoed, (
            "the device echoed a different value than it was asked for "
            f"(echoed, requested): {mis_echoed}")
        return

    guard = period_guard(writable_id)
    declared = _declared_status_periods()
    assert len(declared) >= 4, f"the declared config names too few periods: {declared}"

    answers = {}
    for param_id, value in declared.items():
        answers[param_id] = adm.write_param(writable_id, param_id, value, wait=1.5)

    unanswered = sorted(p for p, r in answers.items() if r is None)
    assert not unanswered, (
        f"id {writable_id} did not answer PARAMETER_WRITE for parameters "
        f"{unanswered} in a burst of {len(declared)}; that is CD 456184's "
        "mechanism on this firmware, and write_param has no retry")
    refused = {p: r["result_text"] for p, r in answers.items() if r["result"] != 0}
    assert not refused, f"the device refused writes it should accept: {refused}"
    mis_echoed = {p: (r["value"], declared[p]) for p, r in answers.items()
                  if r["value"] != declared[p]}
    assert not mis_echoed, (
        f"the device echoed a different value than it was asked for "
        f"(echoed, requested): {mis_echoed}")

    observed = adm.status_period_ms(writable_id, dialect.fault_api, seconds=3.0)
    assert observed == pytest.approx(guard.restore_to, abs=5), (
        f"the burst left STATUS_1 at {observed} ms rather than the declared "
        f"{guard.restore_to} ms")


def test_a_write_to_an_id_no_controller_owns_is_reported_as_no_response(
        adm, gate, free_id):
    """The other half of A1: the write is sent, no result frame returns, and the
    device keeps its old value with nothing erroring. Aimed at a CAN id nothing
    on this bus owns, so the frame is real, the silence is real, and no
    controller is written to.

    `write_param` returning None is the only signal a caller gets, and it is
    indistinguishable from a controller that is present and deaf -- which is why
    the fix for D3 is a retry on the no-response case and nothing else.
    """
    gate("inject")
    started = time.monotonic()
    result = adm.write_param(free_id, P159, 20, wait=1.0)
    waited = time.monotonic() - started

    assert result is None, (
        f"something answered a parameter write on unclaimed id {free_id}: {result}")
    assert waited >= 1.0, (
        f"write_param returned after {waited:.3f} s without waiting out its "
        "own timeout")


def test_consecutive_parameter_writes_are_paced(adm, gate, sniff, writable_id):
    """CD 456184 again, at the frame level. The controller that lost settings was
    sent slot 0 and slot 1 back to back; the recovery was to slow down. Here the
    two writes carry the declared value, so nothing changes on the controller and
    the only thing measured is how close together the driver puts them on the
    wire. https://www.chiefdelphi.com/t/456184
    """
    gate("inject")
    target = int(sa.declared_status_1_period_ms())
    with sniff() as sniffer:
        adm.write_param(writable_id, P159, target)
        adm.write_param(writable_id, P159, target)

    writes = [m for m in sniffer.from_this_host
              if (m.arbitration_id & ~0x3F) == sa.PARAM_WRITE]
    assert len(writes) >= 2, (
        f"expected two PARAMETER_WRITE frames from this host, saw {len(writes)}\n"
        + sniffer.explain())
    gap = writes[1].timestamp - writes[0].timestamp
    assert gap >= MIN_INTER_WRITE_GAP_S, (
        f"consecutive writes left {gap * 1000:.2f} ms apart, which is the "
        "response latency and not a pace")


def test_a_write_response_says_whether_the_value_it_echoed_was_the_one_asked_for(
        adm, gate, writable_id, dialect):
    """A result code is not proof (catalogue cross-cutting lesson 1). The device
    echoes the value it took in bytes 2:6 of the response, and CD 456184's
    failure was a write of 20 answered with 250 and reported Success. The
    comparison costs one line and the driver does not make it, so every caller
    has to remember to -- and `cmd_repair`, the only caller, does not.
    https://www.chiefdelphi.com/t/456184
    """
    gate("inject")
    if dialect.legacy:
        # Pre-25 echoes too, on api class 48. The period is not a parameter here,
        # so the echo is tested on one that is -- written back its own value, so
        # the controller ends where it started.
        pid = 6                                   # Idle Mode, uint32, read-write
        current = adm.read_legacy_param(writable_id, pid, wait=1.0)
        assert current is not None and current["ok"], (
            f"id {writable_id} did not answer a read of parameter {pid}: {current}")
        result = adm.write_legacy_param(writable_id, pid, current["raw"],
                                        type_tag=1, wait=1.0)
        assert result is not None and result["ok"], result
        assert result["verified"] is True, (
            f"the echo carries {result['raw']} against a requested "
            f"{result['requested']}, and nothing compares them: {result}")
        return

    target = int(sa.declared_status_1_period_ms())
    result = adm.write_param(writable_id, P159, target)

    assert result is not None and result["result"] == 0, result
    assert result.get("verified") is True, (
        f"the response carries the echoed value {result['value']} and the "
        "requested value is known, and nothing in the driver compares them: "
        f"{result}")


# -- the canary, and the three readings that are not it ------------------------

def test_a_controller_written_to_revs_factory_period_is_named_by_the_canary(
        adm, gate, writable_id, roles, serials, period_guard, dialect):
    """The control for everything below: the Status 1 Period canary really does
    fire on this hardware. Device 12 on this fleet was found at REV's 250 ms
    default on a freshly power-cycled bus, which is the one
    confirmed case of real config loss here -- this reproduces that reading on
    purpose, in RAM, and puts it back.
    """
    gate("inject")
    guard = period_guard(writable_id, dialect.param)
    response = guard.write(int(dialect.rev_default_ms))
    assert response["result"] == 0, response
    time.sleep(0.5)

    observed = adm.status_period_ms(writable_id, dialect.fault_api, seconds=4.0)
    inv = adm.inventory(3.0)
    problems = sa.audit_problems(inv, {}, roles, serials,
                                 generation=dialect.generation)

    verdict = sa.status_1_verdict(observed, expected=dialect.expected_ms,
                                  rev_default=dialect.rev_default_ms)
    if dialect.expected_ms == dialect.rev_default_ms:
        # Pre-25 provisions Status 0 to the value REV already ships, so writing
        # "the factory default" writes the provisioned one and there is no
        # reverted state to reach. The canary cannot fire on this generation, and
        # saying so is the finding.
        assert verdict == "ok", (
            f"{dialect.generation}: expected and factory are both "
            f"{dialect.expected_ms} ms, so {observed} ms should read ok")
        guard.restore()
        pytest.skip(
            f"{dialect.generation} provisions this frame to REV's own default "
            f"({dialect.expected_ms} ms), so the canary has nothing to detect. "
            "The throttle apply_boot_config writes is the real reference here, "
            "and it is checked by test_legacy_period_write.py")

    assert verdict == "reverted", (
        f"a controller broadcasting at {observed} ms was not read as reverted")
    assert [p for p in problems if f"id {writable_id} " in p
            and "factory default" in p], problems

    guard.restore()
    back = adm.status_period_ms(writable_id, dialect.fault_api, seconds=4.0)
    assert sa.status_1_verdict(back, expected=dialect.expected_ms,
                               rev_default=dialect.rev_default_ms) == "ok", (
        f"the restore left id {writable_id} at {back} ms")


def test_a_starved_status_1_is_absent_while_the_controller_still_answers(
        adm, gate, writable_id, roles, serials, period_guard, dialect):
    """Catalogue C3, CD 432129: `Timeout Waiting for Status X` on a frame that
    was deliberately turned down. The controller is alive, repairable over CAN
    and nowhere near an RMA, and on the wire its silence is the same silence a
    dead controller makes.

    The discriminator is already in the driver: GET_FIRMWARE_VERSION is the one
    read this firmware answers, and a controller that answers it is a config
    problem rather than a hardware one. Nothing in the audit asks.
    https://www.chiefdelphi.com/t/432129

    The drain is load-bearing rather than tidy. `status_period_ms` counts
    whatever the socket hands it, including frames that arrived before the window
    opened, so without it the one frame this controller sent on the way down is
    attributed to a window it was never in. The test below pins that separately.
    """
    gate("inject")
    guard = period_guard(writable_id, dialect.param)
    starve = guard.starve_value()
    assert guard.write(starve)["result"] == 0
    time.sleep(0.5)
    adm._drain()

    observed = adm.status_period_ms(writable_id, dialect.fault_api, seconds=2.0)
    version, _ = adm.firmware(writable_id)
    inv = adm.inventory(2.0)
    problems = sa.audit_problems(inv, {}, roles, serials,
                                 generation=dialect.generation)

    assert observed is None, (
        f"the scored frame was starved to {starve} ms and still measured "
        f"{observed} ms; "
        "every window in this test has to close before the starved period "
        "arms its next frame")
    assert version, (
        "the controller stopped answering GET_FIRMWARE_VERSION as well, so this "
        "run cannot separate a quiet controller from a dead one")
    assert [p for p in problems if f"id {writable_id} " in p
            and dialect.frame_label in p], problems

    guard.restore()
    assert adm.status_period_ms(writable_id, dialect.fault_api, seconds=4.0) is not None


@pytest.mark.xfail(reason="status_period_ms() never drains the socket before it "
                          "starts counting, so a frame that arrived before the "
                          "window is attributed to it and a controller that has "
                          "stopped broadcasting reads as a slow cadence",
                   strict=False)
def test_a_measurement_window_counts_only_the_frames_that_arrived_inside_it(
        adm, gate, writable_id, period_guard, dialect):
    """Found by this suite on rig-flex, and not previously recorded.

    Two identical measurements of a bus that is not changing have to agree. They
    do not: the first call inherits whatever is sitting in the receive socket and
    counts it as if it had arrived inside the window, and the second sees the
    truth because the first drained it. On a controller broadcasting at 20 ms one
    stale frame is half a percent; on one that has gone quiet it is the entire
    reading, and it turns "this frame has stopped" into "this frame is slow" --
    which is the same confusion D4 is about, arriving by a different route.

    `write_param` and `persist` both call `_drain()` before they send.
    `status_period_ms`, `inventory`, `duplicates` and `collect_status` do not,
    and `cmd_repair` measures the period immediately after a write.
    """
    gate("inject")
    guard = period_guard(writable_id)
    assert guard.write(guard.starve_value())["result"] == 0
    time.sleep(0.5)

    first = adm.status_period_ms(writable_id, dialect.fault_api, seconds=2.0)
    second = adm.status_period_ms(writable_id, dialect.fault_api, seconds=2.0)

    assert first == second, (
        f"the same silent frame measured {first} ms and then {second} ms, so the "
        "first reading was of the socket rather than of the bus")


def test_a_period_that_is_neither_provisioned_nor_factory_is_reported(
        adm, gate, writable_id, roles, serials, period_guard, dialect):
    """A controller broadcasting at 1000 ms is not at the provisioned 20 and not
    at REV's 250, so the verdict is 'unexpected' -- and `audit_problems` has no
    branch for that word, so the finding is computed and dropped. A real bus
    reaches this reading through congestion, a dropout or a half-applied
    provisioning run, and all three are worth a line.
    """
    gate("inject")
    guard = period_guard(writable_id, dialect.param)
    # NEITHER_VALUE_MS is picked to miss the 25+ pair (20 provisioned, 250
    # factory). On pre-25 both are 10, so the value only has to miss that.
    neither = NEITHER_VALUE_MS if not dialect.legacy else 137
    assert guard.write(neither)["result"] == 0
    time.sleep(0.5)
    try:
        observed = adm.status_period_ms(writable_id, dialect.fault_api, seconds=6.0)
        inv = adm.inventory(4.0)
        problems = sa.audit_problems(inv, {}, roles, serials)
    finally:
        guard.restore()

    assert sa.status_1_verdict(observed) == "unexpected", (
        f"{observed} ms was classified as {sa.status_1_verdict(observed)}; this "
        "run is not reproducing the condition")
    assert [p for p in problems if f"id {writable_id} " in p], (
        f"a controller broadcasting at {observed} ms produced no finding at "
        f"all: {problems}")


def test_a_dropout_inside_the_sample_window_is_not_reported_as_reverted(
        adm, channel, gate, writable_id, roles, serials, dialect):
    """CD 480555: `REV Spark Flex CAN Timeout. Periodic Status 2`, with the
    utilisation dips lining up exactly with the timeouts -- the controller really
    left the bus and came back. Status frames stop for 50 to 500 ms and resume
    with hasReset NOT set, because it never rebooted; it went deaf.

    Here the hole is made by turning the frame down and back up from a second
    session while the first is mid-window, which is what a second host on the bus
    looks like too. The mean over the window lands between the provisioned value
    and REV's default, and the largest inter-arrival gap -- the thing that would
    separate a hole from a slow cadence -- is not recorded by `inventory()` at
    all. https://www.chiefdelphi.com/t/480555
    """
    gate("inject")
    role = roles.get(writable_id, "drive/?").split("/")[0]
    with sa.SparkAdmin(channel) as writer:
        guard = StatusPeriodGuard(writer, writable_id, P159, role=role)
        starve = threading.Timer(1.0, lambda: guard.write(guard.starve_value()))
        resume = threading.Timer(
            4.0, lambda: writer.write_param(writable_id, P159, guard.restore_to))
        starve.start()
        resume.start()
        try:
            inv = adm.inventory(6.0)
        finally:
            starve.cancel()
            resume.cancel()
            guard.restore()

    observed = (inv.get(writable_id, {}).get("periods_ms") or {}).get(dialect.fault_api)
    assert observed is not None, f"id {writable_id} sent no STATUS_1 at all: {inv}"
    problems = sa.audit_problems(inv, {}, roles, serials)
    mine = [p for p in problems if f"id {writable_id} " in p]
    assert not [p for p in mine if "factory default" in p], (
        f"a three second hole inside a six second window measured {observed} ms "
        f"and was reported as config loss: {mine}")
    assert "max_gap_ms" in (inv.get(writable_id) or {}), (
        "inventory() consumed every inter-arrival gap in the window and kept "
        f"only their mean: {inv.get(writable_id)}")


# -- A4: the flash cycle nobody asked for --------------------------------------

def test_an_injection_run_spends_no_flash_cycle_unless_it_was_asked_to(
        adm, gate, sniff, writable_id, capsys):
    """Catalogue A4, CD 455171: flash endurance is ten to hundreds of thousands
    of cycles and some REV examples burned on every boot. This suite writes to a
    controller on every run, so the question "did that cost a flash cycle" has to
    have an answer, and the answer has to come off the wire rather than from
    reading the code.

    `spark repair` without `--persist` is included because it is the command an
    operator reaches for, and its own help says RAM only.
    https://www.chiefdelphi.com/t/455171
    """
    gate("inject")
    with sniff() as sniffer:
        adm.write_param(writable_id, P159, int(sa.declared_status_1_period_ms()))
        spark_cli.cmd_repair(SimpleNamespace(id=writable_id, persist=False,
                                             window=2.0))
    capsys.readouterr()

    burns = [m for m in sniffer.from_this_host
             if (m.arbitration_id & ~0x3F) == sa.PERSIST]
    assert not burns, (
        f"{len(burns)} PERSIST_PARAMETERS frame(s) reached the wire during a "
        "run that never asked for one\n" + sniffer.explain())


# -- G2: what "unverifiable" actually means ------------------------------------

def test_the_status_periods_the_audit_calls_unverifiable_are_measurable(
        adm, writable_id, dialect):
    """CD 438386: an absolute-encoder loop was degraded for a whole season
    because Status 5 sat at its 200 ms factory default. No error, no fault,
    nothing in any log.

    `status_period_ms(dev, api, seconds)` is generic over api and measures
    STATUS_0 and STATUS_1 here without a change. What stops it reading Status 3,
    5, 6 and 7 on this fleet is not the reader: those frames are
    enabledByDefault false in REV's spec and are off, so there is no cadence to
    measure. That is a narrower claim than the coverage note's "firmware 26.1.6
    answers no parameter reads", and the difference matters -- CD 438386's
    failure is invisible here because the frame is disabled, and it would become
    visible the moment anything enabled it. https://www.chiefdelphi.com/t/438386
    """
    if dialect.legacy:
        # The claim inverts on pre-25. Nothing here is enabledByDefault false:
        # 0x060-0x063 and 0x065-0x067 all broadcast, and what the reader measures
        # is apply_boot_config's throttle rather than a declared file. 0x064 is
        # the exception and it never appears in ANY state, cold or running, which
        # is this firmware not emitting the alternate-encoder frame.
        from sparklib.can_bus import _SPARKMAX_STATUS_PERIODS_MS
        base = sa.LEGACY_STATUS_0_API
        for idx, want in _SPARKMAX_STATUS_PERIODS_MS.items():
            api = base + idx
            got = adm.status_period_ms(writable_id, api, seconds=4.0)
            if idx == 4:
                assert got is None, (
                    f"api 0x{api:03X} measured {got} ms, but Status 4 has never "
                    "broadcast on this firmware in any state")
                continue
            assert got == pytest.approx(want, abs=max(8, want * 0.3)), (
                f"one generic reader measured api 0x{api:03X} at {got} ms "
                f"against the boot throttle's {want} ms")
        return

    declared = _declared_status_periods()
    measured = {api: adm.status_period_ms(writable_id, api, seconds=4.0)
                for api in (S0, S1, F.API_STATUS_3, F.API_STATUS_5,
                            F.API_STATUS_6, F.API_STATUS_7)}

    for api in (S0, S1):
        want = declared[F.PERIOD_PARAM_FOR_API[api]]
        assert measured[api] == pytest.approx(want, abs=max(5, want * 0.25)), (
            f"one generic reader measured api 0x{api:03X} at {measured[api]} ms "
            f"against a declared {want} ms")

    off = {api: measured[api] for api in (F.API_STATUS_3, F.API_STATUS_5,
                                          F.API_STATUS_6, F.API_STATUS_7)}
    assert set(off.values()) == {None}, (
        f"a status frame REV disables by default is broadcasting: {off}")
    for api in off:
        assert F.PERIOD_PARAM_FOR_API[api] in sa.UNVERIFIABLE_DEVIATIONS, (
            f"api 0x{api:03X} is silent on this fleet and the coverage note "
            "does not list it among the settings it could not check")
