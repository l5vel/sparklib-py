"""Catalogue class A: configuration is written, reported Success, and does not stick.

Two confirmed driver defects live here, and both hide behind a clean bus:

  D3  write_param() has no retry, no inter-frame pacing, and never compares the
      echoed value in d[2:6] against the value it asked for. CD 456184's teams
      lost kFF that way -- the controller re-defaulted the setting to 0, answered
      the write, and nothing on the wire complained.
      https://www.chiefdelphi.com/t/456184  (catalogue A1)

  D2  persist() drains and sends immediately, and cmd_repair calls write_param
      then persist. A PERSIST that lands before the RAM commit has settled flashes
      the value the write replaced, answers Success, and survives the power cycle.
      The field fix was >= 200 ms between the last write and the burn.
      https://www.chiefdelphi.com/t/432129  (catalogue A2)

Every failure here is injected into the simulator and then read back through the
real SparkAdmin, or through the `spark repair` / `spark persist` call sites that
use it. The assertions are outcomes -- what reached flash, what the wire cadence
became, what exit code an operator got -- not restatements of the driver's own
arithmetic. Ground truth (`dev.ram`, `dev.flash`, `dev.write_log`,
`dev.persist_log`) is the half of the story no CAN frame carries.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from sparklib import admin as sa
from sparksim import SparkBehaviour, assert_no_setpoints, attach, factory
from sparksim import frames as F

S1 = F.API_STATUS_1
P159 = F.PARAM_STATUS_1_PERIOD
P158 = F.PARAM_STATUS_0_PERIOD
P167 = F.PARAM_STATUS_9_PERIOD

# CD 432129: the burn has to be delayed after the last parameter write.
FIELD_SETTLING_S = 0.200
# CD 456184: back-to-back writes outran the controller; pace them.
MIN_INTER_WRITE_GAP_S = 0.020


class _KeepOpen:
    """`with _open() as adm` without the shutdown, so a test can still read the bus."""

    def __init__(self, adm):
        self.adm = adm

    def __enter__(self):
        return self.adm

    def __exit__(self, *exc):
        return False


def run_cli(monkeypatch, adm, handler, **args):
    """Run one `spark` subcommand against an already-attached SparkAdmin."""
    from sparklib import cli as spark_cli

    monkeypatch.setattr(spark_cli, "_open", lambda: _KeepOpen(adm))
    return handler(SimpleNamespace(**args))


def repair(monkeypatch, adm, dev=12, persist=True, window=1.0):
    from sparklib import cli as spark_cli

    return run_cli(monkeypatch, adm, spark_cli.cmd_repair,
                   id=dev, persist=persist, window=window)


def persist_cmd(monkeypatch, adm, dev=12):
    from sparklib import cli as spark_cli

    return run_cli(monkeypatch, adm, spark_cli.cmd_persist, id=dev)


def persist_frames(bus):
    return bus.sends_matching(F.PERSIST)


def write_frames(bus):
    return bus.sends_matching(F.PARAM_WRITE)


# == A1: the write that is answered and does not take =========================

def test_a_parameter_write_is_one_five_byte_frame_aimed_at_one_controller(sim):
    """The baseline the rest of class A is read against: on a healthy 26.1.6 bus a
    write is a single PARAMETER_WRITE to one device, and only that device takes it.
    Catalogue A1's whole difficulty is that the failing case looks exactly like this.
    """
    bus = sim([factory(12), factory(13)])
    adm = attach(bus)

    r = adm.write_param(12, P159, 20)

    assert r["result"] == 0 and r["param_id"] == P159
    assert [m.arbitration_id for m in bus.sent] == [F.PARAM_WRITE | 12]
    assert bus.sent[0].data == F.encode_param_write(P159, 20)
    assert bus.controller(12).write_log[-1].requested == 20
    assert bus.controller(13).write_log == [], "id 13 never heard the frame"
    assert bus.controller(13).ram[P159] == 250


def test_a_lost_write_response_is_retried_until_the_device_takes_the_value(sim):
    """CD 456184: "you need to have retries in your code for basically every
    setting, and also read the setting back to ensure that it's been set
    correctly." https://www.chiefdelphi.com/t/456184  (catalogue A1)

    The first PARAMETER_WRITE is swallowed -- no response, and the device applied
    nothing. One retry would have fixed it.

    Also reported at https://www.chiefdelphi.com/t/491595
    """
    bus = sim([factory(12, behaviour=SparkBehaviour(drop_write_responses=1))])
    adm = attach(bus)
    dev = bus.controller(12)

    adm.write_param(12, P159, 20)

    assert len(write_frames(bus)) >= 2, (
        "one unanswered write and the driver stopped; the controller is still on "
        "the factory period\n" + bus.explain(12))
    assert dev.ram[P159] == 20


def test_repair_reports_failure_when_the_write_is_never_answered(sim, monkeypatch,
                                                                 capsys):
    """The `spark repair` call site does get this one right: an unanswered
    PARAMETER_WRITE stops the command before it burns anything to flash.
    Catalogue A1, CD 456184 <https://www.chiefdelphi.com/t/456184>.
    """
    bus = sim([factory(12, behaviour=SparkBehaviour(drop_write_responses=99))])
    adm = attach(bus)

    rc = repair(monkeypatch, adm)

    assert rc == 1, capsys.readouterr().out
    assert persist_frames(bus) == [], "nothing was flashed over a silent write"
    assert bus.controller(12).flash[P159] == 250


@pytest.mark.parametrize("code", [1, 3, 5])
def test_repair_refuses_to_persist_a_write_the_device_rejected(sim, monkeypatch,
                                                               capsys, code):
    """A refused write is the one class-A failure the driver already reads
    correctly: InvalidID / AccessMode / NotImplemented all stop the repair before
    the burn. Catalogue A1.
    """
    bus = sim([factory(12, behaviour=SparkBehaviour(write_result=code))])
    adm = attach(bus)
    dev = bus.controller(12)

    rc = repair(monkeypatch, adm)

    assert rc == 1, capsys.readouterr().out
    assert persist_frames(bus) == []
    assert dev.ram[P159] == 250, "the device applied nothing"
    assert dev.write_log[-1].result == code


def test_repair_stops_when_the_echoed_value_is_not_the_value_requested(sim, monkeypatch,
                                                                       capsys):
    """CD 456184: controllers that had lost kFF re-defaulted the setting to 0 and
    still answered the write. https://www.chiefdelphi.com/t/456184  (catalogue A1)

    The device echoes 0 for a write of 20 and keeps its factory period. The
    disagreement between requested and echoed is the only evidence on the bus, and
    it is sitting in the frame the driver already parsed.
    """
    bus = sim([factory(12, behaviour=SparkBehaviour(echo_value=0,
                                                    ignore_writes_for={P159}))])
    adm = attach(bus)
    dev = bus.controller(12)

    rc = repair(monkeypatch, adm)

    assert rc == 1, (
        "`spark repair` exited 0 over a controller that answered with a value "
        "nobody asked for:\n" + capsys.readouterr().out)
    assert persist_frames(bus) == []
    assert (dev.write_log[-1].requested, dev.write_log[-1].echoed) == (20, 0)


def test_repair_stops_when_the_period_on_the_wire_did_not_change(sim, monkeypatch,
                                                                 capsys):
    """CD 432129: nearly every boot one controller never took its status frame
    period, a different one each time, while the write itself looked fine.
    https://www.chiefdelphi.com/t/432129  (catalogue A1)

    Response perfect, echo perfect, cadence unmoved. cmd_repair already measures
    that cadence -- it just never reads its own measurement.
    """
    bus = sim([factory(12, behaviour=SparkBehaviour(ignore_writes_for={P159}))])
    adm = attach(bus)
    dev = bus.controller(12)

    rc = repair(monkeypatch, adm)
    out = capsys.readouterr().out

    assert rc == 1, ("`spark repair` exited 0 with the controller still broadcasting "
                     "at the factory period:\n" + out)
    assert persist_frames(bus) == []
    assert dev.ram[P159] == 250


def test_consecutive_parameter_writes_are_paced(sim, clock):
    """CD 456184's cause: the config routine "sent settings back-to-back (slot 0
    then slot 1) faster than the device handled".
    https://www.chiefdelphi.com/t/456184  (catalogue A1)

    Three settings, the shape of a re-provisioning run.

    Also reported at https://www.chiefdelphi.com/t/378088
    """
    bus = sim([factory(12)])
    adm = attach(bus)

    for pid, value in ((P158, 10), (P159, 20), (P167, 100)):
        adm.write_param(12, pid, value)

    stamps = [t for t, _ in write_frames(bus)]
    gaps = [b - a for a, b in zip(stamps, stamps[1:])]
    assert gaps and min(gaps) >= MIN_INTER_WRITE_GAP_S, (
        f"writes left {min(gaps) * 1e3:.1f} ms apart: {stamps}")


# == the safety interlock, which the driver does refuse =======================

@pytest.mark.parametrize("param_id", sorted(sa.PROTECTED_PARAMS))
def test_the_protected_parameters_never_reach_the_bus(sim, param_id):
    """Two kinds of parameter the driver refuses to write.

    The data-port limit switches are an e-stop-equivalent interlock; disabling
    or repolarising one is never a repair. Parameter 0 is the device's own CAN
    id, and a stray write renames a motor off the drivetrain -- which presents
    as a controller that "randomly factory reset", can hit several motors
    without repeating on one, and is therefore hard to attribute to anything.

    The refusal is in the driver, so it holds for any caller, and the bus is
    armed to fail if a frame slips out.
    """
    bus = sim([factory(12)], forbid_protected_writes=True)
    adm = attach(bus)
    dev = bus.controller(12)
    before = dict(dev.ram)

    with pytest.raises(sa.ProtectedParameterError):
        adm.write_param(12, param_id, 0)

    assert bus.sent == []
    assert dev.write_log == []
    # ram[0] legitimately holds the controller's own id, so the check is that
    # nothing CHANGED rather than that the key is absent.
    assert dev.ram == before, f"parameter {param_id} was modified: {dev.ram}"


def test_an_unprotected_neighbour_parameter_is_still_writable(sim):
    """The control for the refusal above: the guard is a named set, not a range,
    so the parameter next to it still goes out."""
    bus = sim([factory(12)])
    adm = attach(bus)

    r = adm.write_param(12, 54, 1)

    assert r["result"] == 0
    assert bus.controller(12).ram[54] == 1


# == A2: the burn that flashes the value the write replaced ===================

def test_persist_waits_for_the_ram_commit_to_settle(sim):
    """CD 432129: "burning configuration to flash was an issue if it was not
    delayed long enough after sending configuration messages."
    https://www.chiefdelphi.com/t/432129  (catalogue A2)

    Both frames answer Success. Flash takes the pre-write value, and the next
    power cycle brings it back.
    """
    bus = sim([factory(12)])
    adm = attach(bus)
    dev = bus.controller(12)

    assert adm.write_param(12, P159, 20)["result"] == 0
    assert adm.persist(12) == 0, "PERSIST_PARAMETERS answered Success"

    # Both timestamps are accumulated virtual-clock floats, so their difference
    # lands within an ulp of the target rather than exactly on it. A strict >=
    # here failed at a 5.6e-17 s shortfall once an unrelated change shifted the
    # absolute times. Compare with a nanosecond of slack: the claim is a 200 ms
    # settle, not a bit-exact float.
    gap = dev.persist_log[-1].at - dev.write_log[-1].response_at
    assert gap >= FIELD_SETTLING_S - 1e-9, (
        f"the burn landed {gap * 1e3:.0f} ms after the write response; the field "
        "fix was at least 200 ms")
    assert dev.flash[P159] == 20
    bus.power_cycle(12)
    assert sa.status_1_verdict(
        adm.status_period_ms(12, sa.STATUS_1_API, seconds=4.0)) == "ok"


def test_a_caller_paced_persist_commits_the_new_value_and_survives_a_power_cycle(
        sim, clock):
    """The same controller, the same 200 ms settling behaviour, with the delay CD
    432129 prescribes supplied by the caller instead of the driver. Without this
    control, a simulator that staled every burn would make the test above pass for
    the wrong reason. https://www.chiefdelphi.com/t/432129  (catalogue A2)
    """
    bus = sim([factory(12)])
    adm = attach(bus)
    dev = bus.controller(12)

    adm.write_param(12, P159, 20)
    bus.measured_period_ms(12, S1, 0.25)          # the caller's settling delay
    assert adm.persist(12) == 0

    assert dev.persist_log[-1].stale == ()
    assert dev.flash[P159] == 20
    bus.power_cycle(12)
    assert dev.ram[P159] == 20, "the reboot loaded the value that was flashed"
    assert sa.status_1_verdict(
        adm.status_period_ms(12, sa.STATUS_1_API, seconds=4.0)) == "ok"


def test_repair_with_a_short_window_flashes_the_value_the_write_replaced(
        sim, monkeypatch, capsys):
    """CD 432129: settings do not survive a power cycle even though the burn
    reported success. https://www.chiefdelphi.com/t/432129  (catalogue A2)

    Nothing between cmd_repair's write and its persist is a delay: the only gap is
    however long the operator's --window happens to be, and at 100 ms the burn
    lands inside the settling window.
    """
    bus = sim([factory(12)])
    adm = attach(bus)
    dev = bus.controller(12)

    rc = repair(monkeypatch, adm, window=0.1)
    out = capsys.readouterr().out

    assert rc == 0 and dev.persist_log, out
    assert dev.flash[P159] == 20, (
        f"`spark repair --persist` reported success and flashed "
        f"{dev.flash[P159]}, the value the write replaced\n" + out)
    bus.power_cycle(12)
    assert dev.ram[P159] == 20


def test_repair_with_the_default_window_flashes_the_new_value(sim, monkeypatch,
                                                              capsys):
    """The paired reading of CD 432129: at the default window the four-second
    cadence measurement between the write and the burn is long enough for the RAM
    commit to settle, so the repair does hold. It holds by accident, but a suite
    that only showed the failure would be claiming the command never works.
    """
    bus = sim([factory(12)])
    adm = attach(bus)
    dev = bus.controller(12)

    rc = repair(monkeypatch, adm, window=4.0)

    assert rc == 0, capsys.readouterr().out
    assert dev.persist_log[-1].stale == ()
    assert dev.flash[P159] == 20
    bus.power_cycle(12)
    assert sa.status_1_verdict(
        adm.status_period_ms(12, sa.STATUS_1_API, seconds=4.0)) == "ok"
    assert_no_setpoints(bus)


def test_persist_does_not_report_failure_when_the_response_is_lost_in_the_blackout(
        sim, monkeypatch, capsys):
    """REV's PSA behaviour behind catalogue A2/A4: the controller stops answering
    for about two seconds after a flash burn, so PERSIST_RESP can be lost even
    though the burn succeeded. https://www.chiefdelphi.com/t/432129

    `spark persist` then reports a failure, and the operator's next move is to burn
    again -- flash wear for a controller that was already correct (catalogue A4,
    CD 455171 <https://www.chiefdelphi.com/t/455171>).
    """
    bus = sim([factory(12, behaviour=SparkBehaviour(persist_blackout_s=0.5))])
    adm = attach(bus)
    dev = bus.controller(12)

    adm.write_param(12, P159, 20)
    bus.measured_period_ms(12, S1, 0.25)
    rc = persist_cmd(monkeypatch, adm)
    out = capsys.readouterr().out

    assert dev.flash[P159] == 20, "the burn itself succeeded"
    assert "blackout" in [why for _, _, why in bus.dropped]
    assert rc == 0, "`spark persist` called a successful burn a failure:\n" + out


def test_persist_surfaces_a_device_reported_flash_failure(sim, monkeypatch, capsys):
    """A burn the controller itself refuses is reported correctly: the result byte
    comes back non-zero and the command exits 1. Catalogue A2's other direction --
    the driver is only blind to the burn that answers Success.
    """
    bus = sim([factory(12, behaviour=SparkBehaviour(persist_result=4))])
    adm = attach(bus)

    assert adm.persist(12) == 4
    rc = persist_cmd(monkeypatch, adm)

    assert rc == 1, capsys.readouterr().out
    assert len(persist_frames(bus)) == 2


# -- folded in from tests/unit/test_spark_adversarial.py ----------------------
# The write path's remaining shapes: the refusal that must NOT be retried (the
# opposite repair to D3's missing retry), the caller that reads a refusal as a
# success, the precondition nobody checks before reconfiguring, and the write
# aimed into the post-flash blackout.

def test_a_rejected_value_is_reported_and_not_retried(sim):
    """CD 427628 <https://www.chiefdelphi.com/t/427628>: "received parameter invalid
    error parameter id 113" -- an out-of-range computed value, a zero conversion
    factor from an unset gear ratio, refused with result 4.

    Timeout and rejection are opposite repairs: one is retried, one loops forever
    if retried. Any retry added for CD 456184 (the xfail above) has to branch on
    which it is, so this is the control that stops the fix from becoming an
    infinite loop against a device that will refuse the value every time.
    """
    from sparksim import spark

    bus = sim([spark(12, behaviour=SparkBehaviour(write_result=4))])
    r = attach(bus).write_param(12, 113, sa.float_bits(0.0))

    assert (r["result"], r["result_text"]) == (4, "Invalid")
    assert len(write_frames(bus)) == 1, (
        "a refused value was re-sent; the device will refuse it forever")
    assert bus.controller(12).ram.get(113) is None, "and nothing was committed"


def test_no_caller_reads_a_write_response_for_truthiness():
    """Call-site guard for the same pair, over the whole package.

    A rejection is a dict with result=4, and a dict is truthy. `if
    adm.write_param(...)` therefore reads a refused write as a success, and the
    only way to be sure no caller does that is to read every caller.
    """
    import ast
    import pathlib

    pkg = pathlib.Path(sa.__file__).resolve().parent
    offenders = []
    for path in pkg.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        for fn in ast.walk(ast.parse(path.read_text(), filename=str(path))):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            names = {t.id for n in ast.walk(fn) if isinstance(n, ast.Assign)
                     if isinstance(n.value, ast.Call)
                     and isinstance(n.value.func, ast.Attribute)
                     and n.value.func.attr == "write_param"
                     for t in n.targets if isinstance(t, ast.Name)}
            for name in names:
                reads = [n for n in ast.walk(fn) if isinstance(n, ast.Subscript)
                         and isinstance(n.value, ast.Name) and n.value.id == name
                         and isinstance(n.slice, ast.Constant)
                         and n.slice.value == "result"]
                if not reads:
                    offenders.append(f"{path.name}:{fn.name} ignores {name}['result']")

    assert offenders == [], offenders


def test_a_driving_controller_is_detectable_from_a_reading_already_in_hand(sim):
    """CD 346537 <https://www.chiefdelphi.com/t/346537>: a firmware/API version
    mismatch left controllers running while the robot was disabled and
    e-stopped, nearly destroying an elevator.

    spark_admin's docstring said a controller "stays disabled and cannot actuate
    while these run". That is a claim about what this tool SENDS, not about what
    the controller is doing. STATUS_0 carries applied output and the tool
    already decodes it, so a session about to reconfigure a moving motor is
    detectable.

    WHERE the check goes matters, and this is the second attempt. Putting it
    inside write_param and set_can_id was tried and reverted: the
    guard did its own listen, a listen CONSUMES frames, and it ate the write
    responses the operation then waited for. Seven tests across three files
    began failing with writes that silently did not take. A precondition that
    perturbs the operation it guards is worse than none.

    So the check takes a reading the caller already holds and consumes nothing.
    """
    from sparksim import spark

    bus = sim([spark(12, applied=0.35)])
    status = sa.collect_status(bus, seconds=0.3)
    assert status[12]["status0"]["applied_output"] == pytest.approx(0.35, abs=0.01), (
        "the controller is driving, on the wire")

    driving = sa.driving_ids(status)
    assert driving == {12: pytest.approx(0.35, abs=0.01)}, (
        "a motor at 0.35 applied output is not at rest")


def test_a_stopped_controller_is_not_reported_as_driving(sim):
    """The negative half: an idle bus must not refuse every repair."""
    from sparksim import spark

    bus = sim([spark(12, applied=0.0)])
    assert sa.driving_ids(sa.collect_status(bus, seconds=0.3)) == {}


def test_the_repair_command_refuses_a_driving_controller(sim, monkeypatch, capsys):
    """The call site. `driving_ids` returning the right dict proves nothing
    about whether cmd_repair asks it before writing."""
    from types import SimpleNamespace
    from sparksim import attach, spark
    from sparklib import cli as spark_cli

    bus = sim([spark(12, applied=0.35)])
    adm = attach(bus)

    class _Keep:
        def __enter__(self): return adm
        def __exit__(self, *e): return False

    monkeypatch.setattr(spark_cli, "_base",
                        lambda: SimpleNamespace(controller_type="sparkflex"))
    monkeypatch.setattr(spark_cli, "_open", lambda: _Keep())

    rc = spark_cli.cmd_repair(SimpleNamespace(id=12, window=1.0, persist=False))
    out = capsys.readouterr().out

    assert rc == 1, "`spark repair` proceeded into a moving motor"
    assert "DRIVING" in out, f"the refusal does not say why:\n{out}"
    assert "346537" in out, "the finding carries no reference for the hazard"


def test_a_write_after_a_persist_is_not_aimed_into_the_post_flash_blackout(sim):
    """REV PSA, via CD 438386 <https://www.chiefdelphi.com/t/438386>: a controller
    blocks CAN for about two seconds after a flash burn or a factory reset, and
    writes issued in that window are lost.

    The blackout is set here by hand rather than through `persist_blackout_s`,
    because that knob also swallows the PERSIST response itself -- a different
    failure, covered above. This one is about what happens after a persist that
    was answered normally: the driver has the answer, knows a burn just finished,
    and offers the caller no way to wait it out.
    """
    from sparksim import spark

    bus = sim([spark(12, behaviour=SparkBehaviour(persist_settle_s=0.0))])
    dev = bus.controller(12)
    adm = attach(bus)

    assert adm.persist(12) == 0, "the burn was answered"
    dev.deaf_until = bus.clock.now + 2.0            # REV's ~2 s of CAN silence
    r = adm.write_param(12, P159, 20)

    assert r is not None and r["result"] == 0, (
        "the write landed inside the post-flash blackout and was reported as a "
        "failure, so a repair run stops on a controller that is perfectly healthy")
    assert dev.ram[P159] == 20, "and the parameter never reached RAM"


# == catalogue A7, E1, E7, F3, F5, G1, G4 =====================================
# Seven catalogue modes whose hardware reproduction is refused, for reasons
# recorded in tests/support/sparkhw/catalogue.py. Each still has a half that is
# provable here, and that half is what these assert. The unprovable half is
# named in each docstring so nobody mistakes coverage for completeness.


def test_a_write_that_is_never_answered_is_not_reported_as_success(sim):
    """Catalogue A7. REVLib's configureAsync() returns kOk before the controller
    has taken the value, so a caller reads a success it has not been given.

    This driver has no async path -- it sends PARAMETER_WRITE and waits for
    PARAMETER_WRITE_RESPONSE -- and that is exactly the property worth pinning,
    because it is what makes A7 inapplicable here. A driver that returned a
    success-shaped dict on a dropped response would reintroduce it.
    """
    bus = sim([factory(12, behaviour=SparkBehaviour(drop_write_responses=99))])
    adm = attach(bus)

    r = adm.write_param(12, P159, 20, wait=0.3)

    assert r is None, f"a write with no response returned {r!r}"
    assert bus.controller(12).write_log, "the frame did leave, so this is not a send failure"


def test_no_module_depends_on_revlib(sim):
    """Catalogue A7 and F3 both originate in REVLib rather than on the wire:
    configureAsync()'s early kOk, and the kS parameter (id 209) that REVLib
    2026.0.1 sends and SPARK MAX 26.1 rejects with a hard crash on init.
    https://www.chiefdelphi.com/t/513879

    Neither can happen here while nothing imports REVLib and nothing writes a
    closed-loop feed-forward gain. Both halves are checked, because either one
    returning would bring the failure back with it.
    """
    import ast, pathlib
    root = pathlib.Path(sa.__file__).resolve().parent
    imported = set()
    for path in root.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import):
                imported |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])

    assert not {"rev", "revlib", "com"} & imported, (
        f"a REVLib import reached the package: {sorted(imported)}")
    assert 209 not in sa.motor_settings("drive"), (
        "parameter 209 is kS, the Feed Forward Static Gain that CD 513879 shows "
        "26.1 rejecting; the declared config must not carry it")


def test_a_rejected_parameter_id_is_surfaced_and_not_read_as_success(sim):
    """Catalogue F3. CD 513879: the firmware answered a kS write with
    "Invalid parameter id (209)" and REVLib turned that into a crash on init.
    https://www.chiefdelphi.com/t/513879

    A non-zero result code is the controller declining, and it must arrive at the
    caller as a decline. Reporting it as Success is the failure this pins; so is
    retrying it, because a refusal repeated is still a refusal (D3's fix must
    retry only the no-response case).
    """
    bus = sim([factory(12, behaviour=SparkBehaviour(write_result=1))])
    adm = attach(bus)

    r = adm.write_param(12, 209, 0x3E19999A)

    assert r is not None and r["result"] != 0, f"a rejected write returned {r!r}"
    assert sa.WRITE_RESULT.get(r["result"]) == "Invalid ID"
    assert len(bus.controller(12).write_log) == 1, "a refusal must not be retried"


def test_motor_type_is_not_writable_by_this_tooling(sim):
    """Catalogue E1 and E7. CD 424550: SPARK MAXes that lit the right colour,
    answered the client, and would not turn -- the controllers were in brushed
    mode with brushless motors on them. E7 is the same end state reached by a
    three second press of the mode button.
    https://www.chiefdelphi.com/t/424550

    The hardware reproduction is refused for a stated reason: 26.1.6 answers no
    parameter read, so nothing on this bus could confirm the value was put back,
    and a drive motor left in brushed mode is silent until someone asks it to
    move. That same fact is the argument for the guard -- a parameter this
    tooling cannot read back, whose wrong value silently disables a wheel, is one
    it must not write.
    """
    bus = sim([factory(12)], forbid_protected_writes=True)
    adm = attach(bus)

    with pytest.raises(sa.ProtectedParameterError):
        adm.write_param(12, 2, 0)

    assert bus.sent == [] and bus.controller(12).write_log == []


def test_the_velocity_filter_is_declared_at_revs_default_on_purpose(sim):
    """Catalogue F5 and G4. CD 514567: REV's default velocity filtering is a
    64-tap FIR sampled at 1 ms with a 100 ms averaging window, so velocity is
    measured across ~164 ms and carries ~82 ms of phase lag.
    https://www.chiefdelphi.com/t/514567

    Not readable on 26.1.6, so the audit cannot confirm what a controller is
    actually filtering with. What it can do is state what this fleet declares.
    These sit at REV's default deliberately: the steer loop closes on CANcoders
    rather than on motor velocity, so the lag does not enter it. This test exists
    so that changing them is a decision somebody made rather than a drift, and so
    the next reader finds the thread.
    """
    declared = sa.motor_settings("drive")
    depth, delta = declared.get(70), declared.get(71)

    assert depth and delta, "the filter parameters left the declared config"
    assert depth["value"] == 64 and delta["value"] == 200
    assert not depth["deviates"] and not delta["deviates"], (
        "the filter now deviates from factory default, so a factory reset drops "
        "it and re-provisioning must restore it; add it to VERIFIABLE_ or "
        "UNVERIFIABLE_DEVIATIONS and update this test")


def test_the_conversion_factors_are_never_claimed_to_be_applied(sim):
    """Catalogue G1. CD 396629: setPositionConversionFactor() did nothing, and
    the telemetry tab still showed 1. The factor is sent as a parameter, and the
    firmware appeared not to apply it.
    https://www.chiefdelphi.com/t/396629

    Every declared setting became READABLE, so the audit compares
    all of them against the declared file. That closes half the gap and not the
    whole of it, and the distinction is the point of this test. Reading a
    parameter back proves what the controller STORED. CD 396629 is a report of a
    factor that was stored and not APPLIED, which no parameter read can rule out.

    The counts-per-rev parameters left this list for a different
    reason: the declared 409 was a typo for REV's 4096, so they no longer deviate
    from factory default and there is nothing to re-apply.
    """
    for pid in (69, 128):
        assert pid not in sa.deviating_settings("steer"), (
            f"parameter {pid} is declared as deviating again; if that is "
            "deliberate it needs a value the fleet actually holds")

    note = sa.coverage_note(8)
    assert "Idle Mode" in note
    assert "no parameter reads" not in note, (
        "26.1.6 answers parameter reads, measured on rig-flex")

    # And the audit must still not claim the value was applied, only stored.
    assert "applied" not in note.lower(), (
        "a parameter read confirms the stored value; CD 396629 is a factor that "
        "was stored and not applied, and nothing over CAN separates the two")


def test_every_deviating_setting_is_declared_verifiable_or_not(sim):
    """The coverage note counts "checked N of M settings that deviate". A setting
    that deviates and appears in neither table is dropped from both halves of
    that sentence: never checked, and never disclaimed. Holds today at 11 of 11.
    """
    deviating = set(sa.deviating_settings("drive"))
    tabled = set(sa.VERIFIABLE_DEVIATIONS) | set(sa.UNVERIFIABLE_DEVIATIONS)

    assert deviating - tabled == set(), "deviates but is in neither table"
    assert tabled - deviating == set(), "tabled but does not deviate"


def test_a_write_the_device_echoed_wrong_is_unverified_even_when_it_took(sim):
    """The echo is the only check most parameters ever get.

    A read can confirm a write on both generations, and the echo
    still has to be right, because it is what the write path itself returns. It is
    also demonstrably not trustworthy on its own: firmware 26.1.6 answers Success
    and echoes 2 for a BOOL parameter written 2, and stores the 2. So a device
    answering Success with a value nobody asked for is reported unverified whether
    or not that parameter happens to be checkable another way.
    CD 456184 <https://www.chiefdelphi.com/t/456184>.
    """
    bus = sim([factory(12, behaviour=SparkBehaviour(echo_value=0))])
    adm = attach(bus)

    r = adm.write_param(12, P159, 20)

    assert bus.controller(12).ram[P159] == 20, (
        "ground truth: the device did apply the value, so the wire looks right")
    assert r["result"] == 0, "and it answered Success, so the code says nothing"
    assert r["verified"] is False, (
        "the echo disagreed with the request and that was reported as a good "
        f"write: {r}")
    assert r["requested"] == 20 and r["value"] == 0, r


def test_repair_stops_on_a_bad_echo_even_when_the_wire_agrees(sim, monkeypatch,
                                                              capsys):
    """The call site of the test above. write_param reporting `verified` is
    inert unless cmd_repair refuses to persist on it -- and the wire check
    cannot stand in, because it only exists for Status 1 Period.
    """
    bus = sim([factory(12, behaviour=SparkBehaviour(echo_value=0))])
    adm = attach(bus)

    rc = repair(monkeypatch, adm)
    out = capsys.readouterr().out

    assert bus.controller(12).ram[P159] == 20, "the wire agrees; only the echo is wrong"
    assert rc == 1, f"`spark repair` exited 0 on an echo nobody asked for:\n{out}"
    assert persist_frames(bus) == [], "and it must not spend a flash cycle on it"


def test_repair_waits_for_the_value_to_reach_the_wire_before_burning(sim, monkeypatch,
                                                                     capsys):
    """The burn goes on evidence, not on a clock.

    CD 432129's fix was a delay, because nothing on the bus marks the moment a
    written parameter becomes flash-eligible. But the half that IS observable --
    the new cadence appearing on the wire -- should be waited for rather than
    assumed. Here the device takes 0.6 s to apply, well past the 200 ms floor,
    so a timer-only persist burns while the controller is still running the old
    period and flash takes a value the operator did not ask for.
    """
    bus = sim([factory(12, behaviour=SparkBehaviour(apply_delay_s=0.6))])
    adm = attach(bus)
    dev = bus.controller(12)

    rc = repair(monkeypatch, adm, persist=True)
    out = capsys.readouterr().out

    assert rc == 0, out
    assert dev.flash[P159] == 20, (
        "the burn landed before the write reached the wire, so flash took the "
        f"value it replaced: {dev.persist_log[-1]}\n{out}")
    assert dev.persist_log[-1].stale == (), dev.persist_log[-1]


def test_persist_waits_for_the_confirm_before_it_burns(sim):
    """The ACK mechanism itself, tested where it lives.

    cmd_repair happens to wait for the wire before it measures, so by the time
    it persists the value is already live and `confirm` never blocks there. That
    makes the callback invisible at that call site, and any other caller that
    persists WITHOUT measuring first depends on it entirely.
    """
    bus = sim([factory(12, behaviour=SparkBehaviour(apply_delay_s=0.6))])
    adm = attach(bus)
    dev = bus.controller(12)

    adm.write_param(12, P159, 20)
    seen = []
    adm.persist(12, confirm=lambda: seen.append(bus.elapsed()) or dev.ram[P159] == 20)

    assert seen, "persist never consulted the confirm callback"
    assert dev.flash[P159] == 20, (
        "the burn went ahead while the write was still pending, so flash took "
        f"the value it replaced: {dev.persist_log[-1]}")
    assert dev.persist_log[-1].stale == (), dev.persist_log[-1]
