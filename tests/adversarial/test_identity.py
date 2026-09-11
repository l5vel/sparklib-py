"""Identity and addressing: who is answering, and is it who we think it is.

Catalogue class B. Every failure here is one where the *frames look fine* -- the
cadence is nominal, the telemetry decodes, nothing is faulted -- and the only
thing wrong is which physical controller is behind the address:

  * B1 duplicate CAN ids (CD 427780, CD 426287, CD 495329) -- two controllers on
    one address. An id scan sees one device, parameter writes reach both, and the
    STATUS payloads for that id flip-flop between two devices' telemetry.
  * B2 id 0 is the unconfigured address (CD 376796) -- the expected id goes
    completely silent while a live REV controller transmits on 0.
  * A5/CD 391976 a SET_CAN_ID that landed in RAM only and is gone after the next
    power cycle.
  * a swapped or replaced controller, which only the hardware serial can reveal.

The rule for every test below: inject through the simulator, then read the bus
with the real SparkAdmin and judge with the real audit_problems().
"""
from __future__ import annotations

import pytest

from sparklib import admin as sa
from sparksim import (SparkBehaviour, attach, build_fleet, factory, spark)
from sparksim import frames as F

S0, S1, UID = F.API_STATUS_0, F.API_STATUS_1, F.API_UNIQUE_ID
P159 = F.PARAM_STATUS_1_PERIOD

# A controller that is not in serials: a spare from the shelf, or the
# second half of a duplicate nobody knew about.
INTRUDER = "0BADCAFE"


def _fleet_and_twin(serial=INTRUDER, **kw):
    """The healthy eight plus a second controller answering on id 12."""
    fleet = build_fleet()
    twin = spark(12, serial, **kw)
    return fleet, twin


# -- B1: two controllers on one id ---------------------------------------------

def test_duplicates_names_both_serials_where_an_id_scan_names_one(sim, roles, serials):
    """B1, CD 427780 <https://www.chiefdelphi.com/t/427780>: two controllers share
    an id, only one appears in the client. The distinct UNIQUE_ID payloads are the
    only thing on the wire that says there are two."""
    fleet, twin = _fleet_and_twin()
    bus = sim(fleet + [twin])
    adm = attach(bus)

    inv = adm.inventory(6.0)
    dups = adm.duplicates(6.0)

    assert sorted(inv) == sorted(roles), (
        "nine controllers, eight addresses -- the scan cannot count devices")
    assert dups == {12: sorted([serials[12], INTRUDER])}, bus.explain(12)

    problems = sa.audit_problems(inv, dups, roles)
    assert len(problems) == 2, problems
    assert any("2 controllers" in p for p in problems), problems
    assert any("twice the provisioned rate" in p for p in problems), (
        "the doubled cadence is the same duplicate seen a second way, and "
        "saying so beats blaming a rogue write")
    assert serials[12] in problems[0] and INTRUDER in problems[0], (
        "the audit has to print both serials or the operator cannot tell which of "
        "the two to move")


def test_an_id_scan_alone_reports_a_healthy_eight(sim, roles):
    """B1's headline symptom (CD 426287 <https://www.chiefdelphi.com/t/426287>):
    without the UNIQUE_ID pass the duplicate is not merely unreported, the bus
    reads as perfect. The doubled frame rate on the shared id is the only residue."""
    fleet, twin = _fleet_and_twin()
    bus = sim(fleet + [twin])
    inv = attach(bus).inventory(6.0)

    blind = sa.audit_problems(inv, {}, roles)
    assert not any("2 controllers" in p or "serial" in p for p in blind), (
        "an id scan alone cannot name the second serial; that needs duplicates()")
    assert any("12" in p and "twice the provisioned rate" not in p for p in blind), (
        "but the doubled cadence is on the wire either way, and the audit now "
        f"reports it rather than calling the bus healthy: {blind}")
    assert inv[12]["periods_ms"][S1] == pytest.approx(10.0, abs=0.5), (
        "two controllers transmitting STATUS_1 at 20 ms measure as one at 10 ms")
    assert inv[12]["periods_ms"][UID] == pytest.approx(500.0, abs=5.0)
    assert inv[13]["periods_ms"][S1] == pytest.approx(20.0, abs=0.5), "the control"


@pytest.mark.xfail(strict=False, reason=(
    "collect_status() keeps one payload per (device, api): the last STATUS_1 frame "
    "on the shared id overwrites the previous one, so the faulted twin's frame is "
    "discarded and the id reports the healthy twin's state. Detecting the "
    "catalogue's flip-flop signature needs per-frame divergence tracking, which "
    "the driver does not have. The audit half of this closed, so "
    "what remains is collect_status keeping only the last frame per (device, api)"))
def test_a_faulted_twin_is_not_masked_by_its_healthy_partner(sim):
    """B1 bus signature: 'Status-0 payload for that ID flip-flops between two
    different payloads on successive frames'. A gate driver fault (catalogue D1,
    CD 444231) on one of two controllers sharing id 12 must not vanish because
    the other one transmitted last.
    https://www.chiefdelphi.com/t/444231
    """
    faulted = spark(12, INTRUDER, faults=F.FAULT["gateDriver"],
                    sticky_faults=F.FAULT["gateDriver"])
    healthy = spark(12)
    bus = sim([faulted, healthy])

    status = sa.collect_status(bus, seconds=2.0)

    seen = [tuple(sa.decode_status_1(m.data)["faults"])
            for _, m in bus.frames(12, api=S1)]
    assert {("gateDriver",), ()} == set(seen), (
        "precondition: both payloads really were on the wire\n" + bus.explain(12))
    assert "gateDriver" in status[12]["status1"]["faults"], (
        "one of the two controllers on id 12 is faulted; the reading for that id "
        "must not depend on which of them transmitted last")


def test_a_parameter_write_to_a_duplicated_id_configures_both_controllers(sim):
    """B1, CD 495329: 'if you try to change the ID while both are plugged in, it
    will try and change both'. The same is true of every parameter write -- and the
    response the driver returns is byte-identical to a healthy single-device write,
    so nothing in write_param()'s result can be used to tell the two situations
    apart."""
    twin = spark(12, INTRUDER, s1_ms=250)
    bus = sim([spark(12, s1_ms=250), twin])
    solo = sim([spark(12, s1_ms=250)])

    duplicated = attach(bus).write_param(12, P159, 20)
    single = attach(solo).write_param(12, P159, 20)

    assert duplicated == single, (
        "the caller cannot distinguish one device from two by the reply\n"
        + bus.explain(12))
    assert [c.ram[P159] for c in bus.controllers_at(12)] == [20, 20], (
        "both controllers took the write; only the first one's answer was heard")
    assert len(twin.write_log) == 1 and twin.write_log[-1].requested == 20


def test_a_factory_twin_hides_inside_the_merged_cadence_of_a_shared_id(sim, roles,
                                                                       serials):
    """B1 crossed with A1: the second controller on id 12 is at REV defaults. The
    cadence measured for that id is the union of both transmitters, so it reads
    FASTER than the provisioned 20 ms and status_1_verdict() calls it healthy. Only
    the UNIQUE_ID pass sees anything wrong. CD 427780.

    Also reported at https://www.chiefdelphi.com/t/377919
    """
    fleet, twin = _fleet_and_twin(s1_ms=sa.REV_DEFAULT_STATUS_1_PERIOD_MS)
    bus = sim(fleet + [twin])
    adm = attach(bus)

    period = adm.status_period_ms(12, sa.STATUS_1_API, seconds=4.0)
    assert period < sa.APPENDIX_A_STATUS_1_PERIOD_MS, (
        f"the merged cadence ({period} ms) is faster than either device alone")
    assert sa.status_1_verdict(period) == "ok", (
        "a controller sitting at the factory default is graded healthy because its "
        "twin's frames fill the gaps")

    problems = sa.audit_problems(adm.inventory(6.0), adm.duplicates(6.0), roles,
                                 serials)
    assert [p for p in problems if INTRUDER in p and serials[12] in p], (
        "the duplicate scan is the only thing standing between this bus and a "
        "clean audit\n" + bus.explain(12))


@pytest.mark.xfail(strict=False, reason=(
    "duplicates() keys entirely on UNIQUE_ID (api 0x2F0) payloads. A twin whose "
    "0x2F0 frame is disabled is a duplicate the driver cannot see, even though the "
    "catalogue's primary signature -- two different STATUS_0 payloads alternating "
    "on one id -- is sitting in the frames it already receives"))
def test_a_duplicate_is_found_even_when_the_twin_does_not_broadcast_its_serial(sim):
    """B1 with STATUS_2..9-style frame disabling: UNIQUE_ID is a periodic frame like
    any other and can be turned off in RHC2. The two devices still collide, still
    both take writes, and their STATUS_0 payloads still differ. CD 426287."""
    fleet, twin = _fleet_and_twin(volts=11.0)
    bus = sim(fleet + [twin])
    bus.disable_frame(twin, UID)
    adm = attach(bus)

    dups = adm.duplicates(6.0)

    payloads = {bytes(m.data) for _, m in bus.frames(12, api=S0)}
    assert len(payloads) == 2, (
        "precondition: id 12's STATUS_0 really does flip-flop between two devices")
    assert {sa.decode_status_0(p)["voltage_v"] for p in payloads} != {None}
    assert 12 in dups, (
        "two controllers answer id 12; a duplicate that stops advertising its "
        "serial is still a duplicate\n" + bus.explain(12))


def test_set_can_id_splits_a_duplicate_pair_by_serial(sim, roles, serials):
    """B1 recovery on modern firmware: SET_CAN_ID addressed by hardware serial moves
    exactly one of two controllers sharing an id, without unplugging anything.
    CD 427780 recovery note."""
    fleet, twin = _fleet_and_twin()
    bus = sim(fleet + [twin])
    adm = attach(bus)
    assert 12 in adm.duplicates(6.0)

    adm.set_can_id(12, INTRUDER, 20)

    assert twin.dev == 20, bus.explain()
    original = bus.controller(12)
    assert original.serial == serials[12], "the configured controller stayed put"
    assert [r.reason for r in original.can_id_log] == ["serial mismatch"], (
        "it heard the frame and refused it -- that is what makes this safe")
    assert adm.duplicates(6.0) == {}
    inv = adm.inventory(6.0)
    assert sorted(inv) == sorted(roles) + [20]
    assert inv[20]["serial"] == INTRUDER


# -- B2: reverted to the unconfigured address ----------------------------------

def test_a_controller_reverted_to_id_zero_reads_as_missing_and_as_a_stranger(
        sim, roles):
    """B2, CD 376796 <https://www.chiefdelphi.com/t/376796>: firmware treats id 0 as
    unconfigured. The bus signature is not a dead node -- the expected id goes
    silent while a live REV controller transmits on 0."""
    bus = sim(build_fleet())
    bus.revert_to_id_zero(12)
    adm = attach(bus)

    inv = adm.inventory(2.0)
    assert 12 not in inv and 0 in inv, bus.explain()

    problems = sa.audit_problems(inv, {}, roles)
    assert len(problems) == 2
    assert any(roles[12] in p for p in problems), "the role that went missing"
    assert any(p.startswith("id 0 ") for p in problems), "the address that appeared"


def test_the_controller_at_id_zero_is_alive_and_answers_there(sim):
    """B2 distinguishing signature: 'an enumerate still finds a REV motor
    controller'. A dead node answers nothing at either address; this one answers
    everything at 0. CD 376796."""
    bus = sim(build_fleet())
    bus.revert_to_id_zero(12)
    adm = attach(bus)

    assert adm.firmware(0) == ("26.1.6", 3)
    assert adm.status_period_ms(0, sa.STATUS_1_API, seconds=1.0) == \
        float(sa.APPENDIX_A_STATUS_1_PERIOD_MS), (
        "its provisioned cadence came with it; only the address changed")
    assert adm.firmware(12) == (None, None)
    assert adm.status_period_ms(12, sa.STATUS_1_API, seconds=1.0) is None


def test_the_audit_says_which_controller_reverted_to_id_zero(sim, roles, serials):
    """B2 again: the recovery is `spark set-id --serial... --to 12`, which needs
    the serial. Two unlinked problem lines make an operator guess which of the
    eight went to 0. CD 376796."""
    bus = sim(build_fleet())
    bus.revert_to_id_zero(12)
    adm = attach(bus)

    inv = adm.inventory(2.0)
    assert inv[0]["serial"] == serials[12], "precondition: the serial is right there"

    problems = sa.audit_problems(inv, {}, roles, serials)
    assert any(serials[12] in p for p in problems), (
        "nothing in the audit names the controller that moved")


@pytest.mark.parametrize("new_id", [0, -1, 63, 64])
def test_set_can_id_refuses_to_assign_an_out_of_range_address(sim, serials, new_id):
    """B2: assigning 0 produces 'Unable to retrieve SPARK MAX firmware version for
    CAN ID: 0' and needs a USB-C recovery. The guard has to fire before the frame
    is built, not after. CD 376796."""
    bus = sim(build_fleet())
    with pytest.raises(ValueError):
        attach(bus).set_can_id(12, serials[12], new_id)
    assert bus.sent == [], "no frame left the tool"
    assert bus.controller(12).can_id_log == []


# -- swapped and replaced controllers ------------------------------------------

def test_a_swapped_pair_is_invisible_to_the_id_scan_and_obvious_by_serial(
        sim, roles, serials):
    """Two corners' controllers exchanged during a rebuild: every id is present,
    every cadence is nominal, and the wheels steer the wrong way. Only
    serials (catalogue cross-cutting lesson: identity is the serial, not
    the id) can see it."""
    fleet = build_fleet()
    by_id = {c.dev: c for c in fleet}
    by_id[12].serial, by_id[13].serial = serials[13], serials[12]
    bus = sim(fleet)
    adm = attach(bus)

    inv = adm.inventory(6.0)
    assert sorted(inv) == sorted(roles)
    assert {i["periods_ms"][S1] for i in inv.values()} == {20.0}
    assert adm.duplicates(6.0) == {}, "no id is answered twice; the pair swapped"
    assert sa.audit_problems(inv, {}, roles) == [], (
        "without learned serials a swap passes every check the tool has")

    problems = sa.audit_problems(inv, {}, roles, serials)
    assert len(problems) == 2
    assert any(roles[12] in p and serials[13] in p for p in problems)
    assert any(roles[13] in p and serials[12] in p for p in problems)


def test_a_replacement_controller_is_both_an_unknown_serial_and_a_factory_cadence(
        sim, roles, serials):
    """An RMA replacement (D1 gate driver failures are the commonest reason a SPARK
    is swapped) arrives at REV defaults. The audit must say both things: this is not
    the controller the config expects, AND it has never been provisioned."""
    fleet = [c for c in build_fleet() if c.dev != 12]
    bus = sim(fleet + [factory(12, serial=INTRUDER)])
    adm = attach(bus)

    inv = adm.inventory(6.0)
    assert sa.status_1_verdict(inv[12]["periods_ms"][S1]) == "reverted"

    problems = sa.audit_problems(inv, {}, roles, serials)
    assert len(problems) == 2, problems
    assert all(roles[12] in p for p in problems)
    assert any(INTRUDER in p and serials[12] in p for p in problems), (
        "the swap line has to carry both the serial found and the serial expected")


# -- SET_CAN_ID and IDENTIFY addressing ----------------------------------------

@pytest.mark.parametrize("current, serial_arg, reason", [
    (12, "DD9A026B", "serial mismatch"),   # serial byte-reversed from the broadcast
    (13, "6B029ADD", "serial mismatch"),   # right serial, aimed at the wrong id
    (0, "6B029ADD", "broadcast id"),       # the unconfigured address as a shortcut
])
def test_a_misaddressed_set_can_id_moves_nothing(sim, current, serial_arg, reason):
    """The property that makes SET_CAN_ID safe to fire at one of two controllers
    sharing an id (B1 recovery): every mis-addressing is a no-op rather than a
    partial move. Verified against hardware; the byte order is the one api 0x2F0
    broadcasts."""
    bus = sim(build_fleet())
    before = [c.dev for c in bus.controllers]

    attach(bus).set_can_id(current, serial_arg, 20)

    assert [c.dev for c in bus.controllers] == before, bus.explain()
    assert reason in [why for _, _, why in bus.ignored]
    assert not any(r.accepted for c in bus.controllers for r in c.can_id_log)


def test_a_serial_string_from_inventory_addresses_identify_and_set_can_id(sim,
                                                                          serials):
    """The call site, not the function: inventory() hexes the raw UNIQUE_ID payload,
    and identify()/set_can_id() hand that same string to bytes.fromhex. If either
    end ever flipped byte order or case, an operator following the tool's own
    printout would blink the wrong controller -- or move it. B1 recovery path."""
    bus = sim(build_fleet())
    adm = attach(bus)

    found = adm.inventory(2.0)[12]["serial"]
    assert found == serials[12]

    adm.identify(found)
    assert bus.controller(12).identify_count == 1

    adm.set_can_id(12, found.lower(), 20)
    assert bus.controllers_at(12) == [] and bus.controller(20).serial == found, (
        "the string the tool printed is the string the tool accepts")


def test_identify_picks_one_of_two_controllers_sharing_an_id(sim, serials):
    """B1 field procedure: with two controllers on id 12 you cannot address them
    apart by id, so you blink one LED at a time by serial to learn which is which
    before moving one. IDENTIFY is a broadcast every device inspects."""
    fleet, twin = _fleet_and_twin()
    original = {c.dev: c for c in fleet}[12]
    bus = sim(fleet + [twin])
    adm = attach(bus)

    adm.identify(INTRUDER)
    assert (twin.identify_count, original.identify_count) == (1, 0)

    adm.identify(serials[12])
    assert (twin.identify_count, original.identify_count) == (1, 1)
    assert sum(c.identify_count for c in bus.controllers) == 2, (
        "no third controller blinked\n" + bus.explain())


def test_identify_with_a_serial_no_device_owns_blinks_nothing(sim):
    """The reason `spark learn-serials` exists: IDENTIFY has no acknowledgement
    frame, so a typo'd or stale serial is indistinguishable from a controller that
    is powered down. Every device on the bus simply ignores it."""
    bus = sim(build_fleet())
    attach(bus).identify("DEADBEEF")

    assert sum(c.identify_count for c in bus.controllers) == 0
    assert [why for _, _, why in bus.ignored].count("serial mismatch") == \
        len(bus.controllers), "every controller looked and declined"


def test_a_ram_only_id_change_survives_only_after_a_persist(sim, serials):
    """A5 / CD 391976 <https://www.chiefdelphi.com/t/391976>: a CAN id that lives in
    RAM is gone at the next power cycle, which presents as a controller that
    'randomly' left the bus. The documented remedy -- follow set-id with a persist
    -- has to actually hold across the reboot."""
    bus = sim([spark(12, behaviour=SparkBehaviour(set_can_id_ram_only=True,
                                                  persist_settle_s=0.0))])
    dev = bus.controller(12)
    adm = attach(bus)

    # The controller takes the id into RAM only; the driver is what has to make
    # it permanent. Before the fix this returned with flash still on 12.
    assert adm.set_can_id(12, serials[12], 20) is True, (
        "the move landed and was burned, so the caller is told it succeeded")
    assert (dev.dev, dev.flash[F.PARAM_CAN_ID]) == (20, 20), (
        "set_can_id must follow the move with a persist, or the id is gone at "
        "the next power-up -- which is what CD 391976 describes")

    bus.power_cycle(dev)

    assert dev.dev == 20, "the reboot loaded the persisted id"
    inv = adm.inventory(2.0)
    assert sorted(inv) == [20] and inv[20]["serial"] == serials[12], bus.explain()


def test_set_can_id_reports_a_controller_that_refused_the_move(sim, serials, clock):
    """A6/B1: a controller in recovery mode, or one whose id is locked, ignores
    SET_CAN_ID. Firmware sends no NACK for it, so the tool has to check -- silence
    from a write that had no effect is exactly the CD 456184 shape."""
    bus = sim([spark(12, behaviour=SparkBehaviour(accept_set_can_id=False))])
    adm = attach(bus)

    outcome = adm.set_can_id(12, serials[12], 20)

    assert bus.controller(12).can_id_log[-1].reason == "refused", "ground truth"
    assert clock.now >= 1.5, "and the caller paid the full settle for nothing"
    assert outcome is False, (
        "a move that did not happen must be reported as a move that did not happen")


# -- folded in from tests/unit/test_spark_adversarial.py ----------------------
# Identity questions the modules above do not ask: the address that can never be
# enabled, the two listening windows the audit compares as if they were one, the
# firmware skew the baseline check cannot reach, and the half of a CAN id change
# that lives in flash.

@pytest.mark.xfail(reason="SparkAdmin.scan() does not exist: cmd_audit listens once "
                          "for the inventory and again for the duplicates, and "
                          "compares two different windows as if they were one",
                   strict=False)
def test_one_listening_pass_serves_the_inventory_and_the_duplicate_check(sim, clock):
    """CD 441722 <https://www.chiefdelphi.com/t/441722>: a SPARK that enumerates
    late at boot carries CAN TX/RX sticky faults and is present in one listening
    window and absent from the next.

    `spark audit` runs inventory() and then duplicates(), back to back, and reads
    the two results as one picture of the bus. Anything that changes between them
    -- a late joiner, a controller that drops out, a twin that starts answering --
    is reported against a bus state that never existed, and the operator pays for
    both windows.
    """
    bus = sim([spark(10), spark(11), spark(10, INTRUDER)])
    adm = attach(bus)

    t0 = clock.now
    inv, dups = adm.inventory(6.0), adm.duplicates(6.0)
    assert clock.now - t0 >= 12.0, (
        f"two windows over one bus cost {clock.now - t0:.1f} s and describe two "
        "different six-second slices")
    assert sorted(inv) == [10, 11] and 10 in dups, "both readings are available"

    inv2, dups2 = adm.scan(6.0)

    assert sorted(inv2) == [10, 11] and 10 in dups2, (
        "one pass must answer both questions, or the audit is comparing a "
        "duplicate seen in the second window against an inventory from the first")


@pytest.mark.parametrize("roles_map", [{}, {0: "drive/RB"}],
                         ids=["unmapped", "mapped-to-a-role"])
def test_a_controller_at_id_zero_is_reported_as_never_enableable(sim, roles_map):
    """CD 347357 <https://www.chiefdelphi.com/t/347357>: a NEO stopped running
    while still pushing encoder values over CAN. The controller was on id 0, and
    "CAN ID 0 is considered unconfigured and won't be enabled".

    Nothing on the wire changes -- the frames, the cadence and the telemetry are
    all correct. The device simply cannot actuate, and the repair is to reassign
    it into 1..62. The mapped case is the worse one: a role map that names id 0
    silences the only line the audit does print.
    """
    bus = sim([spark(0, "CCCC0003")])
    inv = attach(bus).inventory(2.0)
    assert sorted(inv) == [0], "a live REV controller is broadcasting on id 0"

    problems = sa.audit_problems(inv, {}, roles_map)

    assert problems, "a device that can never actuate audits clean"
    assert any("enable" in p.lower() or "unconfigured" in p.lower()
               for p in problems), (
        f"id 0 needs reassigning to 1..62, not a note about devices: {problems}")


def test_the_baseline_firmware_check_reads_a_field_the_bus_reading_carries(sim):
    """CD 427137 <https://www.chiefdelphi.com/t/427137>: "Unable to retrieve SPARK
    MAX firmware version. Please verify the deviceID field matches the configured
    CAN ID."

    audit_problems compares info['firmware'] against the baseline. inventory()
    never sets that key -- only cmd_audit patches it in, and only when a baseline
    file exists -- so the branch is dead for anyone calling the driver directly,
    while the data is on the bus: firmware() answers.
    """
    bus = sim([spark(10, firmware="25.0.4")])
    adm = attach(bus)
    assert adm.firmware(10)[0] == "25.0.4", "the controller does answer GET_FIRMWARE"

    # Passive by default, because a gated bus reading empty must stay
    # distinguishable from an absent one. with_firmware asks.
    assert "firmware" not in adm.inventory(1.0).get(10, {}), (
        "inventory must stay passive unless asked; a query would wake the bus")
    inv = adm.inventory(2.0, with_firmware=True)
    assert inv[10]["firmware"] == "25.0.4"

    problems = sa.audit_problems(inv, {}, {10: "drive/RB"}, {10: inv[10]["serial"]},
                                 {10: {"serial": inv[10]["serial"],
                                       "firmware": "26.1.6"}})

    assert any("firmware" in p for p in problems), (
        "a controller two minor versions off baseline audits clean: inventory() "
        f"produced {sorted(inv[10])}, and the baseline check reads 'firmware'")


def test_an_unanswered_firmware_query_is_not_treated_as_a_baseline_match(sim,
                                                                         serials):
    """CD 478032 <https://www.chiefdelphi.com/t/478032>, CD 458677
    <https://www.chiefdelphi.com/t/458677>: four SPARK Flexes stopped answering the
    product-id and firmware-version queries while still visible on the bus with
    CAN TX/RX faults set; and GET_FIRMWARE goes unanswered intermittently at
    startup on a controller that is alive and holding an LED pattern.

    firmware() sends one RTR, waits 0.5 s and gives up. The device that refuses to
    answer is exactly the one that most needs comparing, and it is the one the
    guard skips. The enrichment loop below is the one cmd_audit runs.
    """
    fleet = build_fleet()
    fleet[-2].behaviour = SparkBehaviour(answer_firmware=False)     # id 16
    bus = sim(fleet)
    adm = attach(bus)

    inv = adm.inventory(2.0)
    for dev in inv:                                   # spark_cli.cmd_audit
        inv[dev]["firmware"] = adm.firmware(dev)[0]
    assert inv[16]["firmware"] is None and inv[15]["firmware"] == "26.1.6", (
        "one controller refuses the query and the rest answer")

    base = {d: {"serial": s, "firmware": "26.1.6"} for d, s in serials.items()}
    problems = sa.audit_problems(inv, {}, dict(zip(serials, serials)), serials, base)

    assert any("16" in p and "firmware" in p.lower() for p in problems), (
        f"an unanswered firmware query was treated as a match: {problems}")


def test_set_can_id_commits_the_move_it_made(sim, serials):
    """CD 425589 <https://www.chiefdelphi.com/t/425589>: "every time I reconnect to
    the motor controller, the ID resets". A CAN id written over CAN lands in RAM;
    the flash burn is a separate, independently failable step, and nothing on the
    wire at write time distinguishes the two.

    A renumbering run exists to make a duplicate pair permanent. One that leaves
    the move in RAM has not renumbered anything -- it has armed the same collision
    for the next power cycle, which is CD 391976 all over again.
    """
    bus = sim([spark(17, behaviour=SparkBehaviour(set_can_id_ram_only=True))])
    dev = bus.controller(17)

    attach(bus).set_can_id(17, serials[17], 40)

    assert dev.dev == 40, "ground truth: the controller took the move into RAM"
    assert bus.sends_matching(F.PERSIST), (
        "no PERSIST followed the id change, so the move is gone at the next "
        "power-up and nothing told the operator\n" + bus.explain())
    assert dev.flash[F.PARAM_CAN_ID] == 40, (
        "the PERSIST has to have committed the new id, not merely been sent")


# == catalogue A6: the reset that does not clear the address ==================

COMPLETE_FACTORY_RESET = 0x020505C0
RESET_SAFE_PARAMETERS = 0x02050540


def test_a_factory_reset_does_not_free_the_can_id_it_was_run_to_free(sim, roles):
    """Catalogue A6. CD 507673: a SPARK Flex "resisting a complete factory
    reset". Both the complete factory reset and recovery mode were tried and
    "the device keeps loading its old CAN id"; it cleared only after a firmware
    downgrade, and the controller then would not run the motor at all.
    https://www.chiefdelphi.com/t/507673

    Sending the reset is refused on hardware for a stated reason: it drops the
    CAN ID, Motor Type and Idle Mode of a provisioned controller. The restore is
    confirmable by reading, and the reset stays refused because
    of what it costs when the restore fails, not because it is unobservable. What
    this asserts is the consequence an operator meets. A reset run to break a
    duplicate-id collision leaves both controllers on the same id, so the pair is
    still a duplicate afterwards and the repair path must still be by serial.
    """
    twin = spark(13, serial=INTRUDER)
    bus = sim(build_fleet() + [twin])
    adm = attach(bus)
    before = adm.duplicates(6.0)

    bus.revert_to_defaults(twin)          # the reset lands on one of the pair
    after = adm.duplicates(6.0)

    assert 13 in before and 13 in after, (
        "a factory reset drops the broadcast periods and leaves the address, so "
        f"id 13 is answered by two controllers before and after: {before} -> {after}")
    assert sorted(after[13]) == sorted(before[13]), after
    assert twin.dev == 13, "the reset did not move the controller off the id"


@pytest.mark.parametrize("base,name", [(COMPLETE_FACTORY_RESET, "COMPLETE_FACTORY_RESET"),
                                       (RESET_SAFE_PARAMETERS, "RESET_SAFE_PARAMETERS")])
def test_no_reset_frame_is_ever_addressed_by_this_tooling(sim, base, name):
    """The other half of A6. A reset drops the CAN ID, Motor Type and Idle Mode
    at once, and on 26.1.6 nothing can read any of the three back, so a tool that
    sends one cannot prove what it did. CD 507673 also shows it failing to
    achieve the one thing it is reached for. Nothing here may send it, and the
    constant must not appear in the package at all.
    """
    import pathlib
    root = pathlib.Path(sa.__file__).resolve().parent
    hits = [str(p.relative_to(root)) for p in root.rglob("*.py")
            if f"{base:08X}" in p.read_text().upper().replace("0X", "")]

    assert hits == [], f"{name} (0x{base:08X}) is referenced by {hits}"


def test_the_can_id_is_write_protected_but_still_deliberately_changeable(sim, serials):
    """Two routes to a controller's CAN id, and only one of them is safe.

    A CAN id never needs to change once a base is configured -- until a
    controller is replaced, and then it must. So the id cannot simply be frozen;
    what it needs is a route that is impossible to take by accident.

    PARAM_WRITE to parameter 0 is the accidental route. It is addressed by id,
    so on a duplicated id it hits both controllers; it needs no serial, so it
    cannot be aimed; and one wrong --param on a debug tool renames a motor off
    the drivetrain. That is refused outright.

    SET_CAN_ID is the deliberate route. It carries the target's SERIAL, so it
    reaches the one controller you chose even among duplicates, it is verified
    against who answers afterwards, and it is burned to flash. Replacing a
    controller goes through it.
    """
    bus = sim(build_fleet())
    adm = attach(bus)

    with pytest.raises(sa.ProtectedParameterError) as e:
        adm.write_param(12, sa.PARAM_CAN_ID, 40)
    assert "set-id" in str(e.value), (
        "the refusal has to name the route that does work, or it just blocks "
        f"someone with a real controller to replace: {e.value}")
    assert bus.controller(12).dev == 12, "and nothing moved"

    # the supported route still works, end to end
    assert adm.set_can_id(12, serials[12], 40) is True
    assert bus.controller(40).dev == 40
    assert bus.controller(40).flash[F.PARAM_CAN_ID] == 40, (
        "and it is burned, so replacing a controller survives the power cycle")
