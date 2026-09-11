"""Faults, warnings and clear_faults, driven through the simulator.

Catalogue class D (faults that silence or disable a controller) and the part of
class A a fault bit is the only witness to. Two confirmed driver defects live
here:

  D1 `audit_problems()` takes no status argument and `cmd_audit` never calls
     `collect_status()`, so a latched gate-driver fault broadcasting at a perfect
     20 ms period audits clean and exits 0.
  D5 `clear_faults()` is fire-and-forget, so a hardware-latched gate driver
     needing an RMA and a transient sticky bit produce identical output -- and
     the clear erases the sticky evidence nobody read first.

The passing tests here establish that everything the audit would need is already
on the wire and already decoded correctly; the xfails are the two places that
reading is thrown away.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from sparklib import admin as sa
from sparksim import SparkBehaviour, attach, build_fleet, spark
from sparksim import frames as F
from sparksim.fleet import ROLES_FLEX, SERIALS_FLEX

S0, S1 = F.API_STATUS_0, F.API_STATUS_1
GATE = F.FAULT["gateDriver"]


def _status1(bus, dev, seconds=0.5):
    """What the driver's own collector makes of this controller right now."""
    return sa.collect_status(bus, seconds=seconds)[dev]["status1"]


# -- STATUS_1 decode: four independent bytes, 2025+ layout --------------------

def test_the_four_status_1_bytes_are_decoded_as_four_separate_conditions(sim):
    """Catalogue D3: sticky faults latch after the active fault has gone, so an
    audit reads active and sticky as different questions. On the 2025+ layout
    they are bytes 0, 2, 3 and 5 of STATUS_1, and the fault and warning tables
    share names at different bit positions -- a byte or table mix-up swaps
    'sensor' the fault for 'sensor' the warning without changing the frame size.
    """
    bus = sim([spark(12)])
    c = bus.controller(12)
    c.faults = F.FAULT["sensor"]
    c.warnings = F.WARN["stall"]
    c.sticky_faults = GATE
    c.sticky_warnings = F.WARN["brownout"]

    out = _status1(bus, 12)

    assert out["faults"] == ["sensor"]
    assert out["warnings"] == ["stall"]
    assert out["sticky_faults"] == ["gateDriver"]
    assert out["sticky_warnings"] == ["brownout"]
    assert out["is_follower"] is False


def test_a_warning_alone_is_never_reported_as_a_fault(sim):
    """Catalogue D4 / CD 453509 <https://www.chiefdelphi.com/t/453509>: REV
    acknowledged a firmware bug that raised an EEPROM condition. 'escEeprom'
    exists in both the fault table (bit 6) and the warning table (bit 2), and
    the two are opposite verdicts -- one is an RMA conversation, one is noise.
    """
    bus = sim([spark(12)])
    bus.controller(12).warnings = F.WARN["escEeprom"]
    bus.controller(12).sticky_warnings = F.WARN["escEeprom"]

    out = _status1(bus, 12)

    assert out["warnings"] == ["escEeprom"]
    assert out["faults"] == [] and out["sticky_faults"] == []


def test_the_production_controller_does_not_read_telemetry_as_faults(sim):
    """Catalogue D1 (gate driver, the fault everyone is hunting for) read through
    the decoder base_handler actually uses. On the pre-2025 layout STATUS_0 bytes
    2:4 and 4:6 were active and sticky faults; on 26.1.6 they are the 12-bit bus
    voltage and output current, so a perfectly healthy 13.2 V controller reports
    a large non-zero fault word and a real gate-driver fault is invisible.
    """
    from sparklib.controller import SPARK_FLEX, Controller

    bus = sim([spark(12)])
    attach(bus).status_period_ms(12, S1, seconds=0.3)
    s0 = bus.frames(12, api=S0)[-1][1].data
    s1 = bus.frames(12, api=S1)[-1][1].data

    truth = sa.decode_status_1(s1)
    assert truth["faults"] == [] and truth["sticky_faults"] == [], (
        "the simulated controller is healthy; STATUS_1 is the authority")

    legacy = Controller(bus=None, id=12, controller_type=SPARK_FLEX)
    legacy._status0_raw = s0
    assert (legacy.active_faults, legacy.sticky_faults) == (None, None), (
        "with only STATUS_0 in hand there is no fault reading to give, and "
        "inventing one from the rail bytes is the whole defect")

    legacy._status1_raw = s1
    assert (legacy.active_faults, legacy.sticky_faults) == (0, 0), (
        f"a healthy idle frame decoded as active={legacy.active_faults} "
        f"sticky={legacy.sticky_faults}\n" + bus.explain(12))


# -- the evidence is on the wire; the cadence is not --------------------------

def test_a_latched_gate_driver_fault_does_not_disturb_the_status_1_cadence(sim):
    """CD 444231 <https://www.chiefdelphi.com/t/444231>, CD 346981
    <https://www.chiefdelphi.com/t/346981>: a gate-driver fault persists across
    factory reset and reflash and ends in an RMA. It changes the payload, not the
    period -- which is the whole premise of D1: no cadence measurement, however
    exact, can ever see it.
    """
    bus = sim(build_fleet())
    bus.set_fault(12, faults=["gateDriver"])
    adm = attach(bus)

    period = adm.status_period_ms(12, S1, seconds=2.0)

    assert period == sa.APPENDIX_A_STATUS_1_PERIOD_MS, bus.explain(12)
    assert sa.status_1_verdict(period) == "ok", (
        "the period verdict is clean while the controller is faulted")
    assert _status1(bus, 12)["faults"] == ["gateDriver"], (
        "and the fault was broadcasting the whole time")


def test_a_brownout_leaves_a_sticky_warning_after_the_rail_has_recovered(sim):
    """Catalogue class A, brownout: the rail dips, the controller re-inits, and by
    the time anyone runs a tool the voltage reads nominal again. STATUS_1 byte 5
    is the only surviving witness that it happened at all.
    """
    bus = sim([spark(12)])
    bus.brownout(12, at=0.2, volts=5.8, duration=0.2)

    st = sa.collect_status(bus, seconds=1.0)[12]

    assert st["status0"]["voltage_v"] == pytest.approx(12.6, abs=0.05), "rail is back"
    assert st["status1"]["warnings"] == [], "and the active warning went with it"
    assert st["status1"]["sticky_warnings"] == ["brownout"]


def test_a_rail_cycle_leaves_has_reset_sticky_while_the_cadence_stays_nominal(sim):
    """Catalogue A5 / CD 455171 <https://www.chiefdelphi.com/t/455171>: a Flex
    that loses its CAN configuration across a power cycle. Sticky 'hasReset' is
    the precursor -- it says the controller rebooted, which is when a RAM-only
    parameter silently goes back to flash. The reboot is invisible to the period
    check, because flash still holds the provisioned 20 ms.
    """
    bus = sim([spark(12)])
    bus.controller(12).ram[F.PARAM_STATUS_1_PERIOD] = 250   # never persisted
    bus.power_cycle(12, offline_s=0.3)

    st = sa.collect_status(bus, seconds=1.0)[12]
    period = attach(bus).status_period_ms(12, S1, seconds=2.0)

    assert st["status1"]["sticky_warnings"] == ["hasReset"]
    assert period == sa.APPENDIX_A_STATUS_1_PERIOD_MS, (
        "flash won the reboot, so the cadence never moved\n" + bus.explain(12))
    assert sa.status_1_verdict(period) == "ok"


# -- D1: the audit never asks for any of it -----------------------------------

def _gate_driver(c):
    c.faults |= GATE
    c.sticky_faults |= GATE


def _esc_eeprom(c):
    c.faults |= F.FAULT["escEeprom"]
    c.sticky_faults |= F.FAULT["escEeprom"]


def _sticky_brownout(c):
    c.sticky_warnings |= F.WARN["brownout"]


def _sticky_has_reset(c):
    c.sticky_warnings |= F.WARN["hasReset"]


@pytest.mark.parametrize("condition, field, needle", [
    (_gate_driver, "faults", "gateDriver"),
    (_esc_eeprom, "faults", "escEeprom"),
    (_sticky_brownout, "sticky_warnings", "brownout"),
    (_sticky_has_reset, "sticky_warnings", "hasReset"),
], ids=["D1-gate-driver", "D4-esc-eeprom", "A-brownout", "A5-has-reset"])
def test_the_audit_reports_a_controller_that_is_broadcasting_a_problem(
        sim, condition, field, needle):
    """Catalogue D1 (CD 444231), D4 (CD 453509) and class A5 (CD 455171): every
    one of these conditions rides in STATUS_1 while the controller keeps its
    provisioned 20 ms cadence and its configured serial. `spark audit` is the
    tool a mechanic runs before deciding whether to swap a controller, and each
    of these must be a named problem rather than a clean bill of health.
    """
    bus = sim(build_fleet())
    condition(bus.controller(12))
    adm = attach(bus)

    inv = adm.inventory(1.0)
    dups = adm.duplicates(1.0)
    status = sa.collect_status(bus, seconds=0.5)
    assert needle in status[12]["status1"][field], "the fixture never broadcast it"

    problems = sa.audit_problems(inv, dups, dict(ROLES_FLEX), dict(SERIALS_FLEX),
                                 status=status)

    assert [p for p in problems if needle in p], (
        f"the audit of a fleet broadcasting {needle} returned {problems}")


def test_cmd_audit_exits_nonzero_over_a_latched_gate_driver_fault(sim, monkeypatch,
                                                                  capsys):
    """CD 444231 <https://www.chiefdelphi.com/t/444231>. The call-site half of D1:
    testing audit_problems() alone cannot see that its only caller never obtains
    a status reading to pass it. Here the real cmd_audit runs against a simulated
    fleet whose id 12 has been in gate-driver fault the entire window.
    """
    from sparklib import cli as spark_cli

    bus = sim(build_fleet())
    bus.set_fault(12, faults=["gateDriver"])
    adm = attach(bus)
    monkeypatch.setattr(spark_cli, "_open", lambda: adm)
    monkeypatch.setattr(spark_cli, "_spark_roles", lambda: dict(ROLES_FLEX))
    monkeypatch.setattr(spark_cli, "_known_serials", lambda: dict(SERIALS_FLEX))
    monkeypatch.setattr(spark_cli, "_load_baseline", lambda: (None, "none"))

    rc = spark_cli.cmd_audit(SimpleNamespace(window=1.0))
    out = capsys.readouterr().out

    assert "gateDriver" in out, f"`spark audit` never mentioned the fault:\n{out}"
    assert rc == 1, f"`spark audit` exited {rc} over a faulted controller:\n{out}"


# -- D5: clear_faults cannot tell a latch from a transient --------------------

def test_clear_faults_addresses_only_the_devices_it_was_given(sim):
    """Catalogue D3: recovery from a sticky-latched transmitter is a Clear Faults
    frame. It is also destructive -- it erases the sticky bytes -- so clearing one
    controller must not take the evidence of the seven beside it. Each frame is
    addressed, never broadcast to id 0.
    """
    bus = sim(build_fleet())
    for c in bus.controllers:
        c.sticky_warnings = F.WARN["brownout"]

    attach(bus).clear_faults([12, 13])

    addressed = [F.dev_of(m.arbitration_id)
                 for _, m in bus.sends_matching(F.CLEAR_FAULTS)]
    assert addressed == [12, 13], bus.explain()
    assert [c.dev for c in bus.controllers if c.clear_log] == [12, 13]
    assert bus.controller(14).sticky_warnings == F.WARN["brownout"], (
        "an untouched controller kept its evidence")


def test_a_hardware_latch_and_a_transient_differ_on_the_wire_after_the_clear(sim):
    """CD 444231 <https://www.chiefdelphi.com/t/444231>, CD 491119
    <https://www.chiefdelphi.com/t/491119>: a gate-driver fault regenerates
    immediately after every clear, which is what distinguishes a dead controller
    from a sticky bit left over from a brownout. Bracketing the clear with the
    driver's own collector shows the difference is fully observable -- the
    capability clear_faults is missing is a re-read, not a new frame.
    """
    latched = SparkBehaviour(latched_faults=GATE, latched_sticky_faults=GATE)
    bus = sim([spark(12, behaviour=latched), spark(13)])
    bus.set_fault(12, faults=["gateDriver"])
    bus.set_fault(13, faults=["can"])
    adm = attach(bus)

    before = sa.collect_status(bus, seconds=0.3)
    adm.clear_faults([12, 13])
    after = sa.collect_status(bus, seconds=0.3)

    assert before[12]["status1"]["faults"] == ["gateDriver"]
    assert before[13]["status1"]["faults"] == ["can"]
    assert after[12]["status1"]["faults"] == ["gateDriver"], "the latch survived"
    assert after[12]["status1"]["sticky_faults"] == ["gateDriver"]
    assert after[13]["status1"]["faults"] == [], "the transient released"
    assert after[13]["status1"]["sticky_faults"] == []


def test_a_controller_that_does_not_come_back_is_named_by_the_audit(sim):
    """Catalogue D3, observed on this fleet: after a motor-rail power cycle all
    eight were silent while still ACKing. PROBE-LOG section 11 established that
    any host frame wakes them, so the recovery is not the interesting part --
    the one that stays dark after everything else has returned is, and that much
    the audit does get right: a missing id is a named problem.
    """
    bus = sim(build_fleet())
    for d in range(10, 18):
        bus.silent_until_cleared(d)
    bus.bus_off(15)
    adm = attach(bus)
    assert adm.inventory(1.0) == {}, "a fault-silenced bus looks like an absent bus"

    adm.firmware(10)
    inv = adm.inventory(1.0)
    problems = sa.audit_problems(inv, {}, dict(ROLES_FLEX), dict(SERIALS_FLEX))

    assert sorted(inv) == [10, 11, 12, 13, 14, 16, 17], bus.explain()
    assert len(problems) == 1 and "15" in problems[0] and "broadcasting" in problems[0]


@pytest.mark.parametrize("kind", ["hardware-latched", "regenerating", "transient"])
def test_clear_faults_says_whether_the_fault_actually_released(sim, kind):
    """CD 444231 <https://www.chiefdelphi.com/t/444231>, CD 454533
    <https://www.chiefdelphi.com/t/454533>: units that powered up in gate-driver
    fault and stayed there through repeated reflashes and clear-faults calls, and
    were eventually replaced under warranty. A latch that regenerates and a
    sticky bit that genuinely released are opposite repairs -- RMA the controller
    or carry on -- and today both are reported as a successful clear.

    Also reported at https://www.chiefdelphi.com/t/363736
    Also reported at https://www.chiefdelphi.com/t/426798
    """
    behaviour = (SparkBehaviour(latched_faults=GATE, latched_sticky_faults=GATE)
                 if kind == "hardware-latched" else SparkBehaviour())
    bus = sim([spark(12, behaviour=behaviour)])
    bus.set_fault(12, faults=["gateDriver"])
    if kind == "regenerating":
        bus.schedule(0.3, lambda s: s.set_fault(12, faults=["gateDriver"]))
    adm = attach(bus)

    result = adm.clear_faults([12], verify_seconds=0.6)

    if kind == "transient":
        assert result[12]["cleared"] is True, result
    else:
        assert result[12]["cleared"] is False, (
            "a fault still broadcasting after the clear was reported as cleared")
        assert "gateDriver" in result[12]["faults"], result


def test_clear_faults_preserves_the_sticky_evidence_it_destroys(sim):
    """Catalogue D3 with A5 (CD 455171 <https://www.chiefdelphi.com/t/455171>).
    The field recipe for a silent bus after a rail cycle is one CLEAR_FAULTS per
    id, and that same frame wipes the sticky brownout and hasReset bits that were
    the only record of why the rail dropped. A clear that has not read what it is
    about to erase destroys the diagnosis to fix the symptom.

    Also reported at https://www.chiefdelphi.com/t/429533
    """
    bus = sim(build_fleet())
    for c in bus.controllers:
        c.sticky_warnings = F.WARN["brownout"] | F.WARN["hasReset"]
        bus.silent_until_cleared(c.dev)
    adm = attach(bus)

    record = adm.clear_faults([12])

    assert bus.controller(12).sticky_warnings == 0, (
        "the clear did erase the bits; that part is not in question")
    assert record is not None, "clear_faults returned nothing to report"
    assert "brownout" in record[12]["sticky_warnings"], record
    assert "hasReset" in record[12]["sticky_warnings"], record


# -- folded in from tests/unit/test_spark_adversarial.py ----------------------
# The single-file suite that preceded this package. What survives here is what
# no other module already covers: the static half of the 2024-layout defect, the
# restart that looks like flash loss, and the call-site guard that a status
# helper is wired into the audit rather than sitting beside it.

def test_no_module_decodes_status_0_bytes_two_to_six_as_faults():
    """The static half of the defect the test above proves behaviourally.
    https://www.chiefdelphi.com/t/461037

    A behavioural test reads one class. This reads every module in the package,
    so the next copy of the 2024 slice -- in a diagnostic script, a health check,
    a vendored helper -- fails the moment it is written rather than the next time
    someone happens to test it.
    """
    import ast
    import pathlib

    pkg = pathlib.Path(sa.__file__).resolve().parent
    admin = pathlib.Path(sa.__file__).resolve()
    offenders = []
    for path in pkg.rglob("*.py"):
        if "__pycache__" in path.parts or path == admin:
            continue
        for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
            if not (isinstance(node, ast.Subscript)
                    and isinstance(node.slice, ast.Slice)):
                continue
            src = ast.unparse(node)
            if "status0" in src.lower() and ast.unparse(node.slice) in ("2:4", "4:6",
                                                                       "2:6"):
                offenders.append(f"{path.name}:{node.lineno} {src}")

    assert offenders == [], (
        "STATUS_0 bytes 2-5 are bus voltage, current and temperature on 25.x+; "
        f"faults live in STATUS_1: {offenders}")


def test_a_restart_that_dropped_the_volatile_period_is_not_flash_config_loss(sim):
    """CD 405541 <https://www.chiefdelphi.com/t/405541>: status frame periods are
    volatile and drop to defaults on any restart, so teams write code that detects
    a controller restart and restores them.

    The repairs are opposite. A restart wants the periods rewritten to RAM; a
    reverted flash wants a re-provision and a flash cycle. Sticky hasReset is the
    only bit that separates them, and acting on the wrong one spends a flash
    cycle on a controller whose flash was never wrong.
    """
    bus = sim([spark(12)])
    dev = bus.controller(12)
    dev.flash[F.PARAM_STATUS_1_PERIOD] = 250        # flash was never provisioned
    bus.power_cycle(12, offline_s=0.2)
    adm = attach(bus)

    inv = adm.inventory(3.0)
    status = sa.collect_status(bus, seconds=0.5)
    assert status[12]["status1"]["sticky_warnings"] == ["hasReset"], "the fixture"
    assert sa.status_1_verdict(inv[12]["periods_ms"][S1]) == "reverted", (
        "the cadence alone reads as a config revert\n" + bus.explain(12))

    problems = sa.audit_problems(inv, {}, dict(ROLES_FLEX), dict(SERIALS_FLEX),
                                 status=status)

    assert any("reset" in p.lower() or "restart" in p.lower() for p in problems), (
        f"hasReset is set: the period was lost to a restart, not to flash: {problems}")


def test_the_audit_consumes_every_finding_a_status_helper_produces(sim):
    """Call-site guard for the fix, not for the defect.

    A helper that decodes faults nobody feeds into the audit is the same blind
    spot with extra code, and `collect_status` + `decode_status_1` are already in
    exactly that position. Whatever names the status findings must be reachable
    from `audit_problems`, or D1 comes back the next time someone adds a decoder.
    """
    bus = sim(build_fleet())
    bus.set_fault(12, faults=["gateDriver"])
    adm = attach(bus)
    status = sa.collect_status(bus, seconds=0.5)
    inv = adm.inventory(1.0)

    direct = sa.status_problems(status, dict(ROLES_FLEX))
    audited = sa.audit_problems(inv, {}, dict(ROLES_FLEX), dict(SERIALS_FLEX),
                                status=status)

    assert direct, "the status helper itself found nothing; the fixture is wrong"
    missing = [p for p in direct if p not in audited]
    assert not missing, f"audit_problems drops status findings: {missing}"


def _clear_cli(monkeypatch, bus):
    from sparklib import cli as spark_cli
    adm = attach(bus)
    monkeypatch.setattr(spark_cli, "_open", lambda: adm)
    monkeypatch.setattr(spark_cli, "_spark_roles", lambda: dict(ROLES_FLEX))
    return spark_cli


def test_cmd_clear_prints_the_sticky_evidence_it_is_about_to_destroy(
        sim, monkeypatch, capsys):
    """The call site, not the function. clear_faults() returning a record is
    inert unless cmd_clear reads it -- and cmd_clear is the command an operator
    runs on a silent bus, so it is the last place the sticky bytes exist.
    """
    bus = sim(build_fleet())
    for c in bus.controllers:
        c.sticky_warnings = F.WARN["brownout"] | F.WARN["hasReset"]
    cli = _clear_cli(monkeypatch, bus)

    cli.cmd_clear(SimpleNamespace(window=1.0))
    out = capsys.readouterr().out

    assert "brownout" in out and "hasReset" in out, (
        "the clear destroyed the only record of the reboot without printing it")
    assert all(c.sticky_warnings == 0 for c in bus.controllers), (
        "the bits are gone; that is what makes printing them the only chance")


def test_cmd_clear_names_a_controller_whose_fault_regenerated(
        sim, monkeypatch, capsys):
    """A gate driver fault that survives a clear needs an RMA, not another
    clear. CD 444231 / CD 454533: units that held it through repeated reflashes
    and were replaced under warranty.
    """
    bus = sim(build_fleet())
    bus.controller(12).behaviour = SparkBehaviour(
        latched_faults=F.FAULT["gateDriver"],
        latched_sticky_faults=F.FAULT["gateDriver"])
    bus.set_fault(12, faults=["gateDriver"])
    cli = _clear_cli(monkeypatch, bus)

    cli.cmd_clear(SimpleNamespace(window=1.0))
    out = capsys.readouterr().out

    assert "STILL FAULTED" in out and "12" in out, out
    assert "RMA" in out, "the operator is told to clear again, forever"


def test_the_follower_property_reads_status_1_not_the_limit_bit(sim):
    """STATUS_0 byte 6 bit 0 is hard_forward_limit on 26.1.6; the follower flag
    moved to STATUS_1 byte 6. Reading the old offset reports every controller
    holding a closed limit switch as being in follower mode -- which sends the
    operator to clear a Follower Mode Leader Id that was never set, while the
    wheel stays held by an interlock nobody mentioned.

    print_diagnostics is covered separately; this is the property that
    motor_debug branches on. https://www.chiefdelphi.com/t/461037
    """
    from sparklib.controller import SPARK_FLEX, Controller

    bus = sim([spark(12)])
    attach(bus).status_period_ms(12, S1, seconds=0.3)
    c = Controller(bus=None, id=12, controller_type=SPARK_FLEX)
    c._status0_raw = bytearray(bus.frames(12, api=S0)[-1][1].data)
    c._status1_raw = bus.frames(12, api=S1)[-1][1].data

    assert c.is_follower is False, "the simulated controller follows nothing"

    c._status0_raw[6] |= 0x01                      # close the forward limit
    assert c.hard_forward_limit is True, "that bit is the limit switch"
    assert c.is_follower is False, (
        "a closed hard limit was reported as follower mode -- the 24.x offset")


def test_the_two_generations_do_not_share_a_status_0_layout():
    """Pre-25 and 25+ answer on different apis with different frames, and the
    whole LEGACY defect was one layout applied to both.

    Same eight bytes decoded each way must disagree, or one of the decoders is
    reading the other product's frame. The Flex bytes here are a healthy idle
    controller: 12.6 V, no current, 30 C, nothing faulted.
    """
    import struct
    d = bytearray(8)
    v, a = int(round(12.6 / 0.0073260073260073)), 0
    d[0:2] = struct.pack("<h", 0)
    d[2] = v & 0xFF
    d[3] = ((v >> 8) & 0x0F) | ((a & 0x0F) << 4)
    d[4] = (a >> 4) & 0xFF
    d[5] = 30
    d[6] = 0

    flex = sa.decode_status_0(bytes(d))
    legacy = sa.decode_legacy_status_0(bytes(d))

    assert flex["voltage_v"] == pytest.approx(12.6, abs=0.05)
    assert flex["implausible"] == [], "a healthy rail is a reading"
    assert legacy["active_faults"] != 0, (
        "the SPARK MAX decoder read a healthy 12.6 V rail as a zero fault word, "
        "which means it is no longer reading the bytes the MAX frame puts there")
    assert legacy["active_faults"] != flex["voltage_v"], "different fields entirely"
