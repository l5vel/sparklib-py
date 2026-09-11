"""Recovery procedures end to end: what actually brings a bus back, and what a
recovery is allowed to claim afterwards.

Every other module here asks "does the driver see the failure?". This one asks
the operator's question: the bus is dead, you ran the procedure, is it fixed?
Five procedures, each driven through the real SparkAdmin and the real
`spark` subcommands against the simulator:

  A  the silent bus a Clear Faults frame wakes -- reproduced on rig-flex after a motor-rail power cycle, catalogue D3 / CORRECTION 1.
  B  the halted controller no CAN command revives. There is no reboot frame;
     the only frame that changes a controller's execution state is
     ENTER_SWDL_CAN_BOOTLOADER, which halts it. Recovery is a person and a
     screwdriver (catalogue D1, CD 444231).
  C  repair -> persist -> power cycle -> verify it survived (CD 432129).
  D  a repair aimed at an id two controllers answer (catalogue B1).
  E/F what `spark audit` exits, and what its coverage note may claim.

Cross-cutting lessons this module is built on, from the catalogue's own list:

  1. A result code is not proof. `spark repair` prints Success from a write that
     never took and from a persist that flashed the previous value; the only
     proof is the behaviour afterwards, and the only proof it *lasted* is a
     power cycle (section C).
  2. Persist needs breathing room. The one repair path that survives a reboot
     survives it by accident -- an unrelated measurement window happens to
     outlast the settling time. Change `--window` and the same command silently
     flashes the old value (section C, the --window pair).
  3. The same symptom has several causes. "Not broadcasting" is a controller
     waiting for a Clear Faults frame, a controller whose gate driver died, and
     a break in the daisy chain. The bus can tell them apart -- a fault-gated
     controller still answers a firmware request -- and the audit does not
     (section B).
  4. Most catastrophic failures are physical. Section B says so directly: the
     driver's entire command vocabulary, aimed at a halted controller, changes
     nothing at all.
  5. A clean audit is not a healthy motor. The coverage note is load-bearing,
     not decoration: section F passes an audit on a controller in COAST with
     the wrong encoder counts, which is exactly the state the note warns about.
"""
from __future__ import annotations

import ast
import pathlib
import re
from types import SimpleNamespace

import pytest

from sparklib import admin as sa
from sparklib import cli as spark_cli
from sparksim import (SparkBehaviour, attach, build_fleet, factory, spark)
from sparksim import frames as F
from sparksim.fleet import ROLES_FLEX, SERIALS_FLEX

S0, S1, UID = F.API_STATUS_0, F.API_STATUS_1, F.API_UNIQUE_ID
P159 = F.PARAM_STATUS_1_PERIOD
DRIVER_FILES = (pathlib.Path(sa.__file__), pathlib.Path(spark_cli.__file__))

# Frame bases the admin tooling is allowed to put on the wire.
ADMIN_BASES = {F.CLEAR_FAULTS, F.IDENTIFY_UNIQUE, F.SET_CAN_ID, F.GET_FIRMWARE,
               F.PARAM_WRITE, F.PERSIST}


def _cli(monkeypatch, bus):
    """Point `spark_cli._open()` at the simulated bus.

    The returned session is a real SparkAdmin; only `close()` is neutered, so
    the simulator is still readable after the subcommand has exited its `with`.
    """
    def _open():
        adm = attach(bus)
        adm.close = lambda: None
        return adm
    monkeypatch.setattr(spark_cli, "_open", _open)


def _args(**kw):
    kw.setdefault("window", 1.0)
    return SimpleNamespace(**kw)


def _problems(capsys):
    """The problem lines `spark audit` printed."""
    return [ln.strip()[2:].strip() for ln in capsys.readouterr().out.splitlines()
            if ln.startswith("  - ")]


def _ids_in(problems):
    return {int(n) for p in problems for n in re.findall(r"\bid (\d+)", p)}


# -- the fleet the whole module leans on ---------------------------------------

def test_the_simulated_fleet_is_the_fleet_this_robot_is_configured_for():
    """Every CLI test below runs against the real rig-flex config rather than a
    monkeypatched role map, so a config edit must not silently detune them."""
    assert spark_cli._spark_roles() == ROLES_FLEX
    assert spark_cli._known_serials() == SERIALS_FLEX
    baseline, _ = spark_cli._load_baseline()
    assert {int(d) for d in baseline["controllers"]} == set(ROLES_FLEX)


# -- A. the silent bus a Clear Faults frame wakes ------------------------------

def test_a_rail_cycle_silences_the_bus_and_spark_clear_brings_it_back(
        rig_flex, monkeypatch, capsys):
    """rig-flex: after a motor-rail power cycle all eight controllers
    were silent while a `cansend` still completed, and one Clear Faults frame per
    id brought all eight back. Catalogue D3 / Part 2 CORRECTION 1 (the mechanism
    is unexplained; the recovery is confirmed)."""
    for dev in ROLES_FLEX:
        rig_flex.power_cycle(dev)
        rig_flex.silent_until_cleared(dev)
    _cli(monkeypatch, rig_flex)

    assert spark_cli.cmd_audit(_args()) == 1
    silent = _problems(capsys)
    assert len(silent) == 1 and "the bus" in silent[0], (
        "nothing arrived from any configured id, which is one bus finding and "
        f"not eight controllers that each failed at the same instant: {silent}")

    assert spark_cli.cmd_clear(_args()) == 0, capsys.readouterr().out
    assert spark_cli.cmd_audit(_args()) == 0, (
        "the bus came back but the audit still calls it broken\n"
        + "\n".join(_problems(capsys)))
    assert all(c.clear_log for c in rig_flex.controllers)


def test_any_host_frame_wakes_the_whole_bus_not_just_the_ids_cleared(
        rig_flex, monkeypatch, capsys):
    """PROBE-LOG section 11 retired the per-id recovery this repo carried.

    After a rail cycle the fleet is silent and stays silent -- 60 s of quiet
    changed nothing. One GET_FIRMWARE addressed to id 17 alone brought all eight
    back, with sticky hasReset intact. So the wake is not a per-controller
    command being processed; it is every controller reacting to the bus being
    used at all. An operator who clears only the ids they remember still gets a
    whole live bus, and the clear was never what did the waking.
    """
    for dev in ROLES_FLEX:
        rig_flex.silent_until_cleared(dev)
    adm = attach(rig_flex)
    assert adm.inventory(1.0) == {}, "the premise: nothing is broadcasting"

    adm.firmware(17)

    assert [c.dev for c in rig_flex.controllers if c.awaiting_clear] == [], (
        "one read addressed to a single id must wake every gated transmitter")
    _cli(monkeypatch, rig_flex)
    assert spark_cli.cmd_audit(_args()) == 0, _problems(capsys)


def test_the_wake_leaves_the_sticky_record_the_clear_would_have_destroyed(rig_flex):
    """The half of section 11 that matters. A read wakes them and erases
    nothing, so hasReset -- the precursor to config loss, and the only evidence
    the rail dropped -- is still readable afterwards. That is what makes
    'read the faults BEFORE you clear' winnable on this fleet.
    """
    for c in rig_flex.controllers:
        c.sticky_warnings = F.WARN["hasReset"] | F.WARN["brownout"]
        rig_flex.silent_until_cleared(c.dev)
    adm = attach(rig_flex)

    adm.firmware(17)
    status = sa.collect_status(rig_flex, seconds=1.0)

    assert set(status) == set(ROLES_FLEX), "the read woke the whole bus"
    for dev in ROLES_FLEX:
        warn = status[dev]["status1"]["sticky_warnings"]
        assert "hasReset" in warn and "brownout" in warn, (
            f"id {dev} lost its sticky record to a wake that should erase nothing")


def test_the_reboot_evidence_is_destroyed_by_the_clear_that_recovers_the_bus(sim):
    """A8 / CD 480555: hasReset is the precursor to config loss, and the correct
    response is to re-apply the whole configuration. It is a sticky warning, so
    the Clear Faults frame that revives the bus also erases the one piece of
    evidence that says the controllers rebooted -- read it first or lose it.
    https://www.chiefdelphi.com/t/480555"""
    bus = sim(build_fleet())
    adm = attach(bus)
    for dev in ROLES_FLEX:
        bus.power_cycle(dev)

    before = sa.collect_status(bus, seconds=0.5)
    assert all(v["status1"]["sticky_warnings"] == ["hasReset"]
               for v in before.values()), bus.explain()

    adm.clear_faults(sorted(ROLES_FLEX))
    after = sa.collect_status(bus, seconds=0.5)
    assert all(v["status1"]["sticky_warnings"] == [] for v in after.values())
    assert all(c.ram[P159] == 20 for c in bus.controllers), (
        "nothing re-applied the configuration; it survived only because flash "
        "held it")


def test_clear_faults_reports_which_controllers_actually_cleared(sim):
    """Catalogue D1: a gate driver fault survives factory reset and reflash and
    needs an RMA, while a sticky brownout clears on the first frame. The
    procedure that tells them apart is one Clear Faults frame and a look at the
    next STATUS_1. https://www.chiefdelphi.com/t/444231"""
    latched = SparkBehaviour(latched_sticky_faults=F.FAULT["gateDriver"])
    bus = sim([spark(12, behaviour=latched), spark(13)])
    for c in bus.controllers:
        c.sticky_faults = F.FAULT["gateDriver"]
    adm = attach(bus)

    outcome = adm.clear_faults([12, 13])

    assert outcome is not None, "clear_faults() reports nothing at all"
    assert outcome[12] != outcome[13], (
        "id 12 needs an RMA and id 13 needed a frame; the caller cannot tell")


# -- B. the halted controller no CAN command revives ---------------------------

def test_no_can_command_revives_a_halted_controller(sim):
    """Catalogue D1, CD 444231: "completely unresponsive to our CAN Bus and the
    REV hardware client". There is no reboot frame in the protocol, so the
    driver's entire vocabulary aimed at a dead controller is a no-op -- software
    can detect and report, not repair.
    https://www.chiefdelphi.com/t/444231"""
    bus = sim(build_fleet())
    dead = bus.controller(12)
    bus.bus_off(12)
    adm = attach(bus)

    assert adm.firmware(12) == (None, None)
    adm.clear_faults([12])
    assert adm.write_param(12, P159, 20, wait=0.2) is None
    assert adm.persist(12, wait=0.2) is None
    adm.set_can_id(12, SERIALS_FLEX[12], 20, settle=0.1)

    assert (dead.clear_log, dead.write_log, dead.persist_log, dead.can_id_log) ==\
        ([], [], [], []), "a halted controller does not process a single frame"
    assert dead.dev == 12 and dead.ram[P159] == 20, "and nothing about it changed"
    assert {why for _, _, why in bus.ignored} == {"bus_off"}
    assert 12 not in adm.inventory(1.0), bus.explain(12)
    assert not bus.frames(12)


def test_nothing_in_the_admin_tooling_can_send_the_bootloader_frame(sim):
    """ENTER_SWDL_CAN_BOOTLOADER (0x02057FC0) halts the controller: it is the one
    frame that changes execution state, and it changes it the wrong way. The
    source check is the real one -- a behavioural check only covers the paths a
    test happens to walk."""
    literals = set()
    for path in DRIVER_FILES:
        for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
            if isinstance(node, ast.Constant) and isinstance(node.value, int):
                literals.add(node.value)
    assert F.ENTER_SWDL_CAN_BOOTLOADER not in literals, (
        "the bootloader arbitration id is written down in the driver; the only "
        "safe number of copies is zero")

    bus = sim(build_fleet())
    adm = attach(bus)
    adm.inventory(0.5)
    adm.firmware(12)
    adm.clear_faults([12])
    adm.write_param(12, P159, 20)
    adm.persist(12)
    adm.identify(SERIALS_FLEX[12])
    assert {F.base_of(m.arbitration_id) for m in bus.sent} <= ADMIN_BASES


def test_a_firmware_read_separates_a_gated_controller_from_a_halted_one(
        sim, monkeypatch, capsys):
    """On the wire the two are the same silence -- the rig-flex rail cycle left
    eight controllers that ACKed and broadcast nothing, which is what a dead one
    does too (catalogue D3 / CORRECTION 1, and D1 for the one that never comes
    back).

    A read tells them apart, and it destroys nothing on the way. PROBE-LOG
    section 9: with the bus in that silence, GET_FIRMWARE to all eight was
    ANSWERED by all eight, while a passive listen over the same window saw no
    REV frame at all. So the clear is not what distinguishes them, and reaching
    for it first erases the sticky record for no diagnostic gain.
    """
    bus = sim(build_fleet())
    bus.silent_until_cleared(12)
    bus.bus_off(13)
    adm = attach(bus)

    silent = set(range(10, 18)) - set(adm.inventory(1.0))
    assert silent == {12, 13}, "neither of the two is broadcasting; that is the premise"
    assert adm.firmware(12) != (None, None), (
        "a gated controller answers a request while broadcasting nothing")
    assert adm.firmware(13) == (None, None), (
        "a halted controller answers nothing, which is what separates the two")

    _cli(monkeypatch, bus)
    rc = spark_cli.cmd_clear(_args())
    out = capsys.readouterr().out

    assert rc == 1, out
    assert "13" in out.split("still silent:")[1]
    inv = attach(bus).inventory(1.0)
    assert 12 in inv and 13 not in inv, bus.explain()


def test_a_controller_with_its_frames_disabled_still_answers_a_request(sim):
    """Catalogue C3 / CD 432129: periodic status timeouts on frames that were
    deliberately disabled. The controller is alive and repairable over CAN -- it
    answers a firmware request and takes a period write -- and its silence is a
    config problem, not an RMA. https://www.chiefdelphi.com/t/432129"""
    bus = sim(build_fleet())
    # A provisioned controller broadcasts Status 0-9, so silencing it means
    # silencing all of them, not only the two the audit scores.
    for api in list(F.STATUS_APIS) + [UID]:
        bus.disable_frame(12, api)
    bus.bus_off(13)
    adm = attach(bus)

    assert 12 not in adm.inventory(1.0) and 13 not in adm.inventory(1.0)
    assert adm.firmware(12) == ("26.1.6", 3), bus.explain(12)
    assert adm.firmware(13) == (None, None), bus.explain(13)

    assert adm.write_param(12, P159, 20)["result"] == 0
    assert 12 in adm.inventory(1.0), (
        "a controller that answers requests is recovered by a write, not by a "
        "replacement\n" + bus.explain(12))


@pytest.mark.xfail(reason="audit_problems() is given only an inventory, so a "
                          "controller that answers requests and one that is off the "
                          "bus produce the same 'is not broadcasting' line; nothing "
                          "probes the ids that are missing",
                   strict=False)
def test_the_audit_separates_an_answering_controller_from_a_halted_one(sim):
    """The audit is what an operator reads at 2am. One of these two is fixed by
    `spark repair` over CAN and the other needs a screwdriver, and the driver has
    already proved it can tell -- GET_FIRMWARE is the one read 26.1.6 answers
    (catalogue C3 vs D1).

    Also reported at https://www.chiefdelphi.com/t/391557
    """
    bus = sim(build_fleet())
    # Silence id 12 COMPLETELY. A provisioned controller broadcasts Status 0-9,
    # so disabling only S0/S1/UID leaves it visible in the inventory, which
    # makes the two controllers differ for a reason that has nothing to do with
    # the defect: that an inventory alone cannot tell answering from halted.
    for api in list(F.STATUS_APIS) + [UID]:
        bus.disable_frame(12, api)
    bus.bus_off(13)
    inv = attach(bus).inventory(1.0)
    assert 12 not in inv and 13 not in inv, (
        "premise: both controllers must be absent from the inventory, or the "
        "audit is telling them apart on presence rather than on evidence")

    problems = sa.audit_problems(inv, {}, dict(ROLES_FLEX))
    anon = re.compile(r"id \d+ \([^)]*\)")
    alive = anon.sub("id N", next(p for p in problems if 12 in _ids_in([p])))
    dead = anon.sub("id N", next(p for p in problems if 13 in _ids_in([p])))

    assert alive != dead, (
        "id 12 answered a firmware request one second ago and id 13 answered "
        f"nothing; the audit says the same words about both: {alive!r}")


# -- C. repair -> persist -> power cycle -> verify it survived -----------------

def test_a_repair_persists_and_survives_when_the_window_outlasts_the_settling(
        sim, monkeypatch, capsys):
    """CD 432129: the fix was flashing +200 ms after configuration. `spark repair
    --persist` clears that bar here only because the STATUS_1 measurement it
    takes between the write and the persist happens to be a second long -- the
    settling is a side effect of an unrelated option, not a guarantee.
    https://www.chiefdelphi.com/t/432129"""
    bus = sim([factory(12)])
    dev = bus.controller(12)
    _cli(monkeypatch, bus)

    assert spark_cli.cmd_repair(_args(id=12, persist=True, window=1.0)) == 0,\
        capsys.readouterr().out
    gap = dev.persist_log[-1].at - dev.write_log[-1].response_at
    assert gap > dev.behaviour.persist_settle_s

    bus.power_cycle(12)
    assert sa.status_1_verdict(
        attach(bus).status_period_ms(12, sa.STATUS_1_API, seconds=1.0)) == "ok"


def test_a_repair_persists_and_survives_with_a_short_measurement_window(
        sim, monkeypatch, capsys):
    """The same command, the same controller, `--window 0.1`. CD 432129's field
    fix was >=200 ms of settling between the writes and the flash; at 0.1 s the
    flash takes the pre-write value, PERSIST_PARAMETERS still answers Success,
    and the repair is undone by the next power cycle.
    https://www.chiefdelphi.com/t/432129"""
    bus = sim([factory(12)])
    dev = bus.controller(12)
    _cli(monkeypatch, bus)

    rc = spark_cli.cmd_repair(_args(id=12, persist=True, window=0.1))
    assert rc == 0, "the command reported a clean repair: " + capsys.readouterr().out

    bus.power_cycle(12)
    assert sa.status_1_verdict(
        attach(bus).status_period_ms(12, sa.STATUS_1_API, seconds=1.0)) == "ok", (
        f"the flash took {dev.flash[P159]} ms; stale params were "
        f"{dev.persist_log[-1].stale}\n" + bus.explain(12))


def test_persist_straight_after_a_write_must_not_flash_the_pre_write_value(sim):
    """The primitive under the procedure above. Same failure, one call site down:
    any caller that writes and then persists gets the previous value in flash and
    a Success code on the bus (CD 432129).
    https://www.chiefdelphi.com/t/432129"""
    bus = sim([factory(12)])
    dev = bus.controller(12)
    adm = attach(bus)

    assert adm.write_param(12, P159, 20)["result"] == 0
    assert adm.persist(12) == 0

    assert dev.persist_log[-1].stale == (), (
        f"parameters {dev.persist_log[-1].stale} were flashed at their pre-write "
        f"values")
    bus.power_cycle(12)
    assert adm.status_period_ms(12, sa.STATUS_1_API, seconds=1.0) == 20.0


def test_a_repair_whose_write_did_not_take_must_not_exit_zero(
        sim, monkeypatch, capsys):
    """CD 456184: the write is acknowledged with Success and the device keeps the
    old value. A result code is not proof (catalogue cross-cutting lesson 1); the
    cadence measured afterwards is, and `spark repair` already has it in a local
    variable. https://www.chiefdelphi.com/t/456184"""
    bus = sim([factory(12, behaviour=SparkBehaviour(ignore_writes_for={P159}))])
    dev = bus.controller(12)
    _cli(monkeypatch, bus)

    rc = spark_cli.cmd_repair(_args(id=12, persist=False, window=1.0))
    out = capsys.readouterr().out

    assert dev.ram[P159] == 250, "the device never took the write"
    assert rc != 0, "`spark repair` exited 0 over a repair that changed nothing:\n" + out


def test_a_real_repair_moves_the_cadence_and_exits_zero(sim, monkeypatch, capsys):
    """The control for the three above: on a controller that reverted to REV's
    factory 250 ms, the repair path works and must say so. Without this, a driver
    that failed every repair would satisfy all of them."""
    bus = sim([factory(12)])
    dev = bus.controller(12)
    _cli(monkeypatch, bus)

    assert spark_cli.cmd_repair(_args(id=12, persist=False, window=1.0)) == 0,\
        capsys.readouterr().out
    assert dev.ram[P159] == int(sa.declared_status_1_period_ms())
    assert sa.status_1_verdict(
        attach(bus).status_period_ms(12, sa.STATUS_1_API, seconds=1.0)) == "ok"
    assert dev.flash[P159] == 250, "RAM only, as the command says"


# -- D. a repair aimed at an id two controllers answer -------------------------

def test_a_repair_on_a_duplicated_id_must_be_refused(sim, monkeypatch, capsys):
    """Catalogue B1, CD 427780 / CD 426287: two controllers on one id look like
    one to an id-based scan. Repairing "id 12" then reconfigures a controller the
    operator cannot see and did not choose, and CD 495329's second response is
    silently discarded. https://www.chiefdelphi.com/t/427780

    Also reported at https://www.chiefdelphi.com/t/427801
    """
    twin = spark(12, "DEADBEEF")
    bus = sim(build_fleet() + [twin])
    _cli(monkeypatch, bus)

    rc = spark_cli.cmd_repair(_args(id=12, persist=False, window=1.0))
    out = capsys.readouterr().out

    assert rc != 0, "`spark repair` reconfigured an ambiguous id and exited 0:\n" + out
    assert twin.write_log == [], (
        "the write reached a controller that was never addressed by serial")


def test_the_duplicate_is_recovered_by_serial_before_any_repair(sim, monkeypatch,
                                                                capsys):
    """The procedure that does work, addressed by serial the whole way: see the
    duplicate, blink the one you are about to move, move it, confirm the id is
    answered by one controller again. Catalogue B1 / CD 427780."""
    twin = spark(12, "DEADBEEF")
    bus = sim(build_fleet() + [twin])
    original = next(c for c in bus.controllers if c.serial == SERIALS_FLEX[12])
    adm = attach(bus)

    assert adm.duplicates(1.0) == {12: sorted(["6B029ADD", "DEADBEEF"])}

    adm.identify("DEADBEEF")
    assert (twin.identify_count, original.identify_count) == (1, 0)

    adm.set_can_id(12, "DEADBEEF", 20)
    assert twin.dev == 20 and twin.flash[F.PARAM_CAN_ID] == 20
    assert original.dev == 12 and not any(r.accepted for r in original.can_id_log)
    assert adm.duplicates(1.0) == {}

    _cli(monkeypatch, bus)
    assert spark_cli.cmd_audit(_args()) == 1
    assert 20 in _ids_in(_problems(capsys)), "the moved controller is now visible"


# -- E. what `spark audit` exits -----------------------------------------------

def test_a_healthy_bus_audits_clean_and_exits_zero(rig_flex, monkeypatch, capsys):
    """The control every exit code below is read against: `spark audit --help`
    promises exit 1 on any fault, so it must not cry wolf on the eight
    controllers the baseline was captured from."""
    _cli(monkeypatch, rig_flex)
    rc = spark_cli.cmd_audit(_args())
    out = capsys.readouterr().out
    assert rc == 0, out
    assert not [ln for ln in out.splitlines() if ln.startswith("  - ")]


@pytest.mark.parametrize("name, inject", [
    ("reverted config", lambda bus: bus.set_period(12, S1, 250)),
    ("halted controller", lambda bus: bus.bus_off(12)),
    ("duplicate id", lambda bus: bus.add(spark(12, "DEADBEEF"))),
    ("swapped controller", lambda bus: setattr(bus.controller(12), "serial",
                                               "0BADCAFE")),
])
def test_a_structural_failure_exits_one_and_names_the_controller(
        rig_flex, monkeypatch, capsys, name, inject):
    """The four failures the audit is actually built to see: config drift
    (catalogue A1), a controller off the bus (C2/D1), one id answered twice (B1),
    and a swapped controller. Each must exit 1 and each must point at id 12."""
    inject(rig_flex)
    _cli(monkeypatch, rig_flex)

    rc = spark_cli.cmd_audit(_args())
    problems = _problems(capsys)
    assert rc == 1, f"{name} audited clean"
    assert 12 in _ids_in(problems), problems


def test_a_latched_fault_at_a_perfect_period_must_not_exit_zero(
        rig_flex, monkeypatch, capsys):
    """Catalogue D1 / Part 2 CORRECTION 1: a set fault bit pins applied output to
    0 while the status frames keep flowing at exactly their provisioned cadence.
    The bus looks healthy, the motor cannot move, and `spark audit --help`
    promises exit 1 on any fault. https://www.chiefdelphi.com/t/444231"""
    rig_flex.set_fault(12, faults=["gateDriver"])
    _cli(monkeypatch, rig_flex)

    rc = spark_cli.cmd_audit(_args())
    assert rc == 1, ("a latched gate driver fault audited clean:\n"
                     + capsys.readouterr().out)


# -- F. what the coverage note may claim ---------------------------------------

@pytest.mark.parametrize("role", ["drive", "steer"])
def test_the_coverage_note_counts_agree_with_the_declared_configuration(role):
    """The note's denominator is a hardcoded pair of dicts; the truth is
    sparkflex_motor_defaults.yaml, which an operator is invited to edit. Adding
    one `deviates: true` setting there must not leave the audit quietly claiming
    it checked a larger fraction than it did."""
    declared = set(sa.deviating_settings(role))
    checked = set(sa.VERIFIABLE_DEVIATIONS)
    unchecked = set(sa.UNVERIFIABLE_DEVIATIONS)

    assert checked | unchecked == declared, (
        "the coverage note and the declared configuration disagree about which "
        f"settings a factory reset drops: {(checked | unchecked) ^ declared}")
    # Widened on hardware. A broadcast period is measurable from the
    # wire whatever the firmware answers to a read, and so is its absence:
    # rig-flex declares Status 0-9 and broadcasts only 0 and 1.
    # Status 0 and 1 are "enabled by default" per REV's 25.0.0 release notes;
    # 2-9 are sent only if needed, so only Status 1 Period is observable here.
    assert checked == {sa.PARAM_STATUS_1_PERIOD}, (
        "the only deviation observable over CAN is the Status 1 Period, because "
        "frames 0 and 1 are the only ones 25+ firmware broadcasts by default")
    assert all(pid in sa.API_FOR_PERIOD_PARAM for pid in checked), (
        "a setting called verifiable that drives no status frame has nothing "
        "on the wire to measure")

    n, total = re.search(r"checked (\d+) of (\d+)", sa.coverage_note(8)).groups()
    assert (int(n), int(total)) == (len(checked), len(declared))


def test_a_controller_in_coast_with_the_wrong_encoder_cpr_now_fails_the_audit(
        rig_flex, monkeypatch, capsys):
    """Catalogue cross-cutting lesson 5, and the case that closed it.

    This controller is in COAST with the factory encoder counts per rev -- the
    two settings CD 424550 and the E2-class failures turn on. Until
    both were invisible over CAN on 26.1.6 and the audit passed the controller,
    leaving a disclosure note as the only thing between that exit code and a
    wrong conclusion. The reads answer now, so the audit names them and exits 1.
    """
    dev = rig_flex.controller(12)
    dev.ram[6], dev.ram[161] = 0, 20     # Idle Mode COAST, Status 3 back at factory
    _cli(monkeypatch, rig_flex)

    rc = spark_cli.cmd_audit(_args())
    out = capsys.readouterr().out

    assert rc == 1, out
    assert "Idle Mode (parameter 6) reads 0, declared BRAKE" in out
    assert "Status 3 Period (parameter 161) reads 20, declared 50" in out
    assert "id 12" in out, "the finding has to name the controller"


def test_a_clean_audit_still_discloses_what_it_did_not_check(
        rig_flex, monkeypatch, capsys):
    """A clean audit is still not a certificate of health.

    What it can claim shrank in the right direction: the note now lists what
    this run did not read instead of what the firmware refuses. It must still
    name every declared deviation, so a reader can see the difference between
    checked and merely quiet.
    """
    _cli(monkeypatch, rig_flex)
    rc = spark_cli.cmd_audit(_args())
    out = capsys.readouterr().out

    assert rc == 0, out
    assert sa.UNVERIFIABLE_DEVIATIONS[6] in out
    assert sa.UNVERIFIABLE_DEVIATIONS[161] in out
    assert "read over CAN and compared against the declared file" in out


# -- folded in from tests/unit/test_spark_adversarial.py ----------------------

def test_a_status_period_is_measurable_only_while_the_frame_is_broadcast(rig_flex):
    """CD 438386 <https://www.chiefdelphi.com/t/438386>: an absolute-encoder loop
    degraded for a whole season because Status 5 sat at its 200 ms factory
    default. No error, no fault, nothing in any log.

    `status_period_ms()` is generic over api, so the period IS measurable WHILE
    the frame is on the wire. That is the whole of what the driver can claim.

    What it cannot claim is the converse. REV, sparkflex-25.0.0 and
    sparkmax-25.0.0, verbatim: "Only sends periodic CAN frames if they are
    needed (except for frames 0 and 1, which are enabled by default)". So on 25+
    a silent Status 2-9 is normal, and its absence is not evidence of a wrong
    period, a lost configuration, or anything else.

    This test previously argued the opposite and was closed as fixed, after rig-flex showed Status 2-9 silent with sticky hasReset on all
    eight. That reading produced an audit rule reporting a lost configuration on
    a healthy fleet. The firmware release notes refuted it. Both
    halves are pinned here so neither is re-derived from the wire alone.
    """
    dev = rig_flex.controller(12)
    dev.set_period_ms(F.API_STATUS_5, 200)
    measured = attach(rig_flex).status_period_ms(12, F.API_STATUS_5, seconds=2.0)
    assert measured == pytest.approx(200, abs=20), (
        "while a frame IS broadcast its period is measurable with the driver's "
        "own generic reader\n" + rig_flex.explain(12))

    #...and the converse does not hold, so a silent frame is still not a finding.
    for pid, name in ((161, "Status 3 Period"), (163, "Status 5 Period"),
                      (164, "Status 6 Period"), (165, "Status 7 Period"),
                      (224, "Status 9 Period")):
        assert pid not in sa.VERIFIABLE_DEVIATIONS, (
            f"{name} drives a Status 2-9 frame. Firmware 25+ sends those only "
            "if needed, so a silent one is not a finding and the audit must not "
            "claim to have measured its CADENCE")

    # The parameter itself is a different instrument, and it answers. Measured
    # on rig-flex: every id 0-255 replies to a remote frame with dlc 8,
    # so a period nobody can time is still a period anybody can read.
    adm = attach(rig_flex)
    assert adm.read_param_value(12, 163) == 200, (
        "Status 5 Period reads back over READ_PARAMETER even while the frame it "
        "drives is silent\n" + rig_flex.explain(12))

    note = sa.coverage_note(8)
    assert "NOT verified" not in note
    assert "Status 5 Period" in note.split("not checked in this run")[1]
    assert "Idle Mode" in note, "every declared deviation must still be named"


# == catalogue E5: the motor that kills every controller wired to it ==========

def test_a_dark_controller_is_not_advised_to_be_replaced_on_its_own(sim, roles):
    """Catalogue E5. CD 435486: a SPARK MAX died mid-match, a brand new one died
    on the next power-up, and a third died the moment the three motor wires went
    back on. A fourth survived only after the NEO was replaced too. CD 439040
    carries the mechanism from REV: an internally shorted motor "kills the SPARK
    MAX that's attached to the motor. Any further motor controller attached to
    that motor will also be killed", and a transient short may not kill it at
    once.
    https://www.chiefdelphi.com/t/435486
    https://www.chiefdelphi.com/t/439040

    The injection is a motor short, which is refused here: it destroyed three
    controllers in the report. What is provable is the advice. A controller that
    has gone dark reaches the operator through this finding, and if the finding
    says only "check motor power" the operator swaps the controller alone and
    loses the replacement. The remedy has to name the motor.
    """
    bus = sim(build_fleet(), )
    bus.silence(13, start=0.0, end=99.0)
    adm = attach(bus)

    problems = sa.audit_problems(adm.inventory(1.0), {}, roles)
    dark = [p for p in problems if "id 13" in p and "not broadcasting" in p]

    assert dark, f"a silent controller produced no finding: {problems}"
    assert any("motor" in p.lower() and "replace" in p.lower() for p in dark), (
        "the remedy for a dark controller must say to replace the motor with it; "
        f"a controller-only swap dies on the next power-up. Got: {dark}")


# -- spark set-id: the three outcomes an operator has to tell apart -----------

def _setid(monkeypatch, bus, serial, to, dev=None):
    _cli(monkeypatch, bus)
    monkeypatch.setattr(spark_cli, "_spark_roles", lambda: dict(ROLES_FLEX))
    return spark_cli.cmd_set_id(_args(serial=serial, to=to, id=dev))


def test_set_id_reports_a_burned_move_as_done(sim, monkeypatch, capsys):
    """The good path. The id moved AND the burn committed, so it survives the
    next power cycle and the operator can stop thinking about it."""
    bus = sim(build_fleet())
    rc = _setid(monkeypatch, bus, SERIALS_FLEX[17], 40)
    out = capsys.readouterr().out

    assert rc == 0, out
    assert "burned to flash" in out, out
    assert bus.controller(40).flash[F.PARAM_CAN_ID] == 40, (
        "reported as burned, so flash has to hold it")


def test_set_id_reports_a_refusal_as_a_refusal(sim, monkeypatch, capsys):
    """A controller in recovery mode ignores SET_CAN_ID and firmware sends no
    NACK, so silence and success look identical on the wire. Telling the
    operator it worked is the failure this exists to prevent."""
    bus = sim(build_fleet())
    bus.controller(17).behaviour = SparkBehaviour(accept_set_can_id=False)
    rc = _setid(monkeypatch, bus, SERIALS_FLEX[17], 40)
    out = capsys.readouterr().out

    assert rc == 1, "a refused move must not exit 0:\n" + out
    assert "REFUSED" in out and "still at id 17" in out, out
    assert bus.controller(17).dev == 17, "ground truth: it never moved"


def test_set_id_says_so_when_the_move_landed_but_the_burn_did_not(sim, monkeypatch,
                                                                 capsys):
    """The dangerous middle case. The controller answers on the new id right
    now, so every check an operator would run says success -- and the next power
    cycle puts it back, which is CD 391976. It has to be reported as unfinished,
    not as done."""
    bus = sim(build_fleet())
    bus.controller(17).behaviour = SparkBehaviour(set_can_id_ram_only=True,
                                                  drop_persist_response=True)
    rc = _setid(monkeypatch, bus, SERIALS_FLEX[17], 40)
    out = capsys.readouterr().out

    assert rc == 1, "an unconfirmed burn must not exit 0:\n" + out
    assert "NOT CONFIRMED" in out, out
    assert "391976" in out or "power cycle" in out, (
        "the operator needs to know what happens next, not just that "
        f"something was odd:\n{out}")
