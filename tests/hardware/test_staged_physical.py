"""The failures that need a person: cut the rail, pull a connector, read a meter.

Tier 3. Every test here runs in a stage selected by `SPARK_HW_STAGE`, because
the thing that produces the failure happens between two runs of pytest and
cannot happen inside one. That is the same shape as
tests/hardware/test_spark_persistence.py, which this module extends rather than
replaces.

    SPARK_HW_STAGE=cycle-arm  uv run --with pytest pytest tests/hardware --hardware
    # cut and restore the motor rail
    SPARK_HW_STAGE=cycle-verify  uv run --with pytest pytest tests/hardware --hardware

The ordering inside `cycle-verify` is load-bearing and is enforced structurally
rather than by test order. A session fixture captures faults and inventory once,
before any test can run, because `spark clear` erases the sticky hasReset and
brownout bits that the power cycle just produced -- that is driver defect D5, and
on rig-flex-2 it cost a real observation when `spark clear` was run
before `spark faults`.

Stages, and what each one needs from the operator:

  cycle-arm / cycle-verify   cut and restore the motor rail. A5, A8, D3, E4,
                             and the position-spike signature from CD 460577.
  settle-arm / settle-verify same, plus SPARK_HW_FLASH. A2, A3: the persist that
                             overtakes the RAM commit it was meant to commit.
  can-id-arm / can-id-verify same, plus SPARK_HW_FLASH. B1/A5: whether SET_CAN_ID
                             reaches flash on 26.1.6, which REV's spec does not
                             say and this repo is internally inconsistent about.
  unplug-can                 pull the CAN connector at SPARK_HW_UNPLUG_ID. C2, C5.
  unplug-encoder             pull the motor data cable at SPARK_HW_UNPLUG_ID. E2, E3.
  swap-hl                    swap CANH and CANL at one connector. C4.
  termination                measure CANH to CANL with power off. C7.
  led                        read the status LED on SPARK_HW_UNPLUG_ID. D2.
  inspect                    the mechanical checklist. E6.
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
from sparkhw import link_info
from sparksim import frames as F

S0, S1, UID = F.API_STATUS_0, F.API_STATUS_1, F.API_UNIQUE_ID
P159 = sa.PARAM_STATUS_1_PERIOD

STATE = pathlib.Path(os.environ.get("SPARK_HW_STATE", "/tmp/spark_hw_stage.json"))

# How long a verify stage will wait for a person to cut and restore power.
WAIT_S = float(os.environ.get("SPARK_HW_WAIT_S", "180"))
# A gap this long with no SPARK frame means the rail went away.
QUIET_S = 0.5
# How much of the wake to keep once frames come back.
WAKE_S = 3.0
# How long to keep waiting after the rail goes quiet before calling the bus
# silent. rig-flex comes back from a motor-rail cycle broadcasting nothing at all
# until a Clear Faults frame per id wakes it, so "no frames yet" is a normal
# outcome here rather than a reason to sit out the whole timeout.
SILENT_SETTLE_S = 25.0
# Watch the silence untouched for this long before sending anything. A rail
# cycle can be over in well under a second, so probing early reports "powered but
# not transmitting" during an ordinary gap. The longer wait also makes the run an
# experiment: frames resuming before the probe means the bus recovers on its own,
# and frames resuming just after it means the query is what woke the transmitter
# -- which would break the deadlock a silent return otherwise creates.
PROBE_AFTER_S = 15.0

# CD 432129: the burn has to be delayed after the last parameter write. The arm
# stage below deliberately does not wait, because that is what cmd_repair does.
FIELD_SETTLING_S = 0.200

# Catalogue D2 and D1. A pattern outside this table is one nobody has written
# down, which is worth stopping on rather than filing.
LED_PATTERNS = {
    "blinking-magenta": "no valid signal: not a fault. A broken motor or encoder "
                        "crimp, a CAN wiring fault, or nothing addressing this id",
    "solid-magenta": "no valid signal, held: the same class as blinking magenta",
    "cyan-orange-alternating": "gate driver fault on a SPARK MAX. Survives factory "
                               "reset and reflash; REV replaced these under warranty",
    "blue-yellow-alternating": "gate driver fault indication on a Flex, which in "
                               "CD 444231 turned out to be missing 12 V to the gate "
                               "driver rather than a dead controller",
    "solid-cyan": "brushless, disabled, no fault: the state this suite expects",
    "blinking-cyan": "brushless, enabled and idle",
}

MECHANICAL_CHECKS = ("docking-screws", "jst-seated", "can-connectors",
                     "terminator-present")


def _save(stage, payload):
    STATE.write_text(json.dumps({"stage": stage, "at": time.time(), **payload},
                                indent=2))


def _load(expect):
    if not STATE.exists():
        pytest.skip(f"no staged state at {STATE}; run the {expect} stage first")
    data = json.loads(STATE.read_text())
    if data.get("stage") != expect:
        pytest.skip(f"{STATE} holds the {data.get('stage')!r} stage, not {expect!r}")
    return data


def _env_id(name="SPARK_HW_UNPLUG_ID"):
    raw = os.environ.get(name, "").strip()
    if not raw:
        pytest.skip(f"set {name} to the CAN id the connector was pulled at")
    return int(raw)


def _snapshot(adm, roles):
    """Everything worth having before anything is allowed to change it."""
    inv = adm.inventory(4.0)
    status = sa.collect_status(adm.bus, seconds=2.0)
    return {
        "inventory": {str(d): {"serial": i["serial"],
                               "status_1_period_ms": (i["periods_ms"] or {}).get(S1)}
                      for d, i in inv.items()},
        "status": {str(d): v["status1"] for d, v in status.items() if v["status1"]},
        "roles": {str(d): r for d, r in roles.items()},
    }


# -- the rail cycle ------------------------------------------------------------

def test_stage_cycle_arm_records_the_bus_before_the_rail_is_cut(staged, adm, roles):
    """Catalogue A5 and A8. A power cycle is the only event that tells a RAM-only
    parameter from a persisted one, and sticky hasReset is the only bit that says
    it happened. Both are gone the moment anything clears faults -- including
    BaseHandler, which sends Clear Faults to every SparkFlex it initialises -- so
    the reading has to be taken before the cut and again immediately after.
    https://www.chiefdelphi.com/t/455171
    """
    staged("cycle-arm")
    before = _snapshot(adm, roles)
    missing = sorted(set(roles) - {int(d) for d in before["inventory"]})
    assert not missing, f"these ids are already silent: {missing}"
    _save("cycle-arm", before)
    print(f"\nARMED: {len(before['inventory'])} controllers recorded to {STATE}.\n"
          "Now start the verify stage and cut and restore the motor rail while "
          "it waits:\n"
          "  SPARK_HW_STAGE=cycle-verify uv run --with pytest pytest "
          "tests/hardware -q --hardware\n")


@pytest.fixture(scope="session")
def rail_cycle(channel):
    """Wait out one motor-rail cycle and keep the first frames after it.

    Returns the capture, or None if the bus never went quiet -- which is what
    happens when the rail was cycled before this stage was started. The
    fault-reading tests still run in that case; only the wake capture skips.

    This is a session fixture so that the reading is taken once, before any test
    in this stage can send a Clear Faults frame. Ordering by position in the file
    would work until someone moved a test.
    """
    if os.environ.get("SPARK_HW_STAGE", "") != "cycle-verify":
        return None
    from sparkhw import Sniffer

    sniffer = Sniffer(channel, maxlen=200000)
    print(f"\nwaiting up to {WAIT_S:.0f} s for the motor rail to be cut and "
          "restored...")
    try:
        with sniffer:
            deadline = time.time() + WAIT_S
            went_quiet, resumed, powered_at = None, False, None
            resumed_at = probed_at = None
            probe = sa.SparkAdmin(channel)
            first_id = sorted(spark_cli._spark_roles())[0]
            while time.time() < deadline:
                seen = sniffer.last_status_at
                if went_quiet is None:
                    if seen is not None and time.time() - seen > QUIET_S:
                        went_quiet = time.time()
                        print("  rail is down; waiting for it to come back...")
                    time.sleep(0.1)
                    continue
                if seen is not None and seen > went_quiet:
                    resumed = True
                    resumed_at = time.time() - went_quiet
                    break
                quiet_for = time.time() - went_quiet
                if (powered_at is None and quiet_for > PROBE_AFTER_S
                        and probe.firmware(first_id, wait=0.3)[0]):
                    powered_at = time.time()
                    probed_at = quiet_for
                    print(f"  {quiet_for:.1f}s into the silence the controllers "
                          "answer GET_FIRMWARE and broadcast nothing: powered, "
                          "alive, not transmitting")
                if quiet_for > SILENT_SETTLE_S:
                    break
                time.sleep(0.2)
            probe.close()
            if went_quiet is None:
                print("  the bus never went quiet; the wake capture is skipped")
                return None
            if resumed:
                time.sleep(WAKE_S)
            frames = ([m for m in sniffer.frames if m.timestamp >= went_quiet]
                      if resumed else [])
    finally:
        sniffer.close()
    if resumed:
        print(f"  broadcasting resumed {resumed_at:.1f}s into the silence"
              + (f", {resumed_at - probed_at:.1f}s after the firmware probe"
                 if probed_at is not None else ", with nothing sent to it"))
    return {"quiet_at": went_quiet, "frames": frames, "resumed": resumed,
            "resumed_at": resumed_at, "probed_at": probed_at,
            "answered_while_silent": powered_at is not None,
            "silent_seconds": None if not frames
            else round(frames[0].timestamp - went_quiet, 3)}


@pytest.fixture(scope="session")
def post_cycle(channel, rail_cycle):
    """Inventory and decoded status, read once and before anything clears."""
    if os.environ.get("SPARK_HW_STAGE", "") != "cycle-verify":
        return None
    with sa.SparkAdmin(channel) as adm:
        inv = adm.inventory(5.0)
        status = sa.collect_status(adm.bus, seconds=2.0)
    return {"inventory": inv, "status": status}


def test_stage_cycle_verify_the_configuration_came_back(staged, post_cycle, roles):
    """Catalogue A5, CD 455171 and CD 391976: a Flex that loses its CAN
    configuration across a power cycle. Everything volatile is gone on the way
    back up, so a period that survives is in flash and a period that does not was
    only ever in RAM -- and the bus cannot tell those apart without the cycle.

    A controller that comes back at REV's 250 ms default is the confirmed config
    loss this fleet found on device 12.
    https://www.chiefdelphi.com/t/455171
    """
    staged("cycle-verify")
    before = _load("cycle-arm")
    inv = post_cycle["inventory"]

    missing = sorted({int(d) for d in before["inventory"]} - set(inv))
    assert not missing, (
        f"these ids were broadcasting before the cycle and are silent now: "
        f"{missing}. Read `spark faults` before clearing anything")

    drifted = {}
    for dev_s, was in before["inventory"].items():
        dev = int(dev_s)
        now = (inv[dev]["periods_ms"] or {}).get(S1)
        if was["status_1_period_ms"] is None or now is None:
            continue
        if abs(now - was["status_1_period_ms"]) > 30:
            drifted[dev] = (was["status_1_period_ms"], now)
    assert not drifted, (
        f"Status 1 Period changed across the rail cycle (before, after): "
        f"{drifted}. A value that did not survive was never in flash")

    swapped = {int(d): (v["serial"], inv[int(d)]["serial"])
               for d, v in before["inventory"].items()
               if v["serial"] and inv[int(d)]["serial"] != v["serial"]}
    assert not swapped, f"a serial moved across the power cycle: {swapped}"


def test_stage_cycle_verify_the_reset_evidence_is_read_before_it_is_erased(
        staged, post_cycle, roles):
    """Catalogue A8, CD 480555: "Every time we had a motor reporting kTimeout
    instead of kOk, it also had a has reset sticky fault." The correct response
    to hasReset is to re-apply the whole configuration including the frame
    periods, not to clear the bit and carry on.

    A rail cycle sets it on every controller, so this is the one reading that
    proves the cycle happened at all -- and `spark clear`, the command an
    operator reaches for when the bus comes back silent, erases it. That is D5,
    and the reading here is taken by a session fixture before any test in this
    stage can send a Clear Faults frame. https://www.chiefdelphi.com/t/480555
    """
    staged("cycle-verify")
    status = post_cycle["status"]
    readable = [dev for dev in sorted(roles)
                if dev in status and status[dev]["status1"]]
    if not readable:
        pytest.skip(
            "no controller was broadcasting STATUS_1 after the cycle, so the "
            "sticky evidence cannot be read at all. On this fleet the bus comes "
            "back from a rail cycle silent, and the only thing that wakes it is "
            "the Clear Faults frame that erases what this test came to read -- "
            "so the read-before-you-clear discipline is not merely easy to get "
            "wrong here, it is unavailable. Recorded rather than asserted, "
            "because it is a property of the robot and not of the tool.")
    without = [dev for dev in readable
               if "hasReset" not in status[dev]["status1"]["sticky_warnings"]]
    assert not without, (
        f"these controllers show no sticky hasReset after a power cycle: "
        f"{without}. Either the rail was not actually cut, or something cleared "
        "faults between the cycle and this reading -- BaseHandler does that on "
        "startup without being asked")


def test_stage_cycle_verify_a_silent_bus_is_woken_by_one_frame_per_id(
        staged, post_cycle, channel, roles, capsys):
    """Catalogue D3 as reproduced on this fleet: after a
    motor-rail power cycle all eight controllers were silent while a `cansend`
    still completed -- something ACKed -- and one Clear Faults frame per id
    brought all eight back instantly, device 17 included.

    No REV document explains that, and the catalogue's own CORRECTION 1 says the
    mechanism is unknown while the recovery is confirmed. rig-flex-2 did not
    reproduce it: after its cycle the bus was already broadcasting. Both
    outcomes are recorded here, and only the recovery is asserted.
    """
    staged("cycle-verify")
    silent = sorted(set(roles) - set(post_cycle["inventory"]))
    if not silent:
        pytest.skip("the bus came back broadcasting, so the recovery this test "
                    "asserts was never exercised. rig-flex-2 saw the same on "
                    " and rig-flex did not, and nothing in the corpus "
                    "explains the difference -- record which one happened")

    print(f"\n{len(silent)} controller(s) came back silent: {silent}")
    rc = spark_cli.cmd_clear(SimpleNamespace(window=4.0))
    out = capsys.readouterr().out
    with sa.SparkAdmin(channel) as adm:
        back = sorted(adm.inventory(4.0))

    assert rc == 0, f"`spark clear` did not recover the bus:\n{out}"
    assert set(roles) <= set(back), (
        f"still silent after one Clear Faults frame per id: "
        f"{sorted(set(roles) - set(back))}")


def test_stage_cycle_verify_the_wake_capture_holds_the_first_frames(
        staged, rail_cycle, roles):
    """CD 460577, the best frame-level report in the corpus: on a reset, position
    freezes for about half a second, jumps among 0, +0.2679443359375 and
    -0.2679443359375, and eleven fault bits set for exactly one loop cycle and
    none of them become sticky. A driver that latches any fault bit breaks on
    that; one that feeds the position to a PID slams.

    What is checkable here is the shape: how long the bus was silent, whether
    any fault bit appeared in the first frames and then went away, and whether
    anything latched. The position half is in STATUS_2, which is disabled by
    default on this fleet, so it is off the wire entirely -- that absence is the
    finding, not an oversight. https://www.chiefdelphi.com/t/460577
    """
    staged("cycle-verify")
    if rail_cycle is None:
        pytest.skip("no rail cycle was observed during this run; start the "
                    "verify stage before cutting power")
    if not rail_cycle["resumed"]:
        pytest.skip(
            "the rail came back and the controllers broadcast nothing, so there "
            "are no wake frames to capture. GET_FIRMWARE "
            + ("answered while they were silent, which puts them powered and "
               "alive and merely not transmitting"
               if rail_cycle["answered_while_silent"] else
               "did not answer either, so the rail may still be off")
            + ". CD 460577's fault-bit storm rides on frames this fleet does not "
              "send until Clear Faults wakes it.")

    status_1 = [m for m in rail_cycle["frames"]
                if F.is_spark(m.arbitration_id)
                and F.api_of(m.arbitration_id) == S1
                and F.dev_of(m.arbitration_id) in roles]
    assert status_1, (
        f"no STATUS_1 frame in the {WAKE_S:.0f} s after the rail came back; the "
        f"bus was silent for {rail_cycle['silent_seconds']} s and stayed that way")

    transient = {}
    for m in status_1:
        decoded = sa.decode_status_1(bytes(m.data))
        if decoded["faults"]:
            dev = F.dev_of(m.arbitration_id)
            transient.setdefault(dev, set()).update(decoded["faults"])
    unlatched = {}
    for dev, bits in transient.items():
        last = [m for m in status_1 if F.dev_of(m.arbitration_id) == dev][-1]
        if not sa.decode_status_1(bytes(last.data))["sticky_faults"]:
            unlatched[dev] = sorted(bits)

    print(f"\nwake: silent for {rail_cycle['silent_seconds']} s, "
          f"{len(status_1)} STATUS_1 frame(s) in the first {WAKE_S:.0f} s, "
          f"faults seen and not latched: {unlatched}")
    assert F.API_STATUS_2 not in {F.api_of(m.arbitration_id)
                                  for m in rail_cycle["frames"]}, (
        "STATUS_2 is broadcasting on this fleet; the position field CD 460577 "
        "reported is on the wire and this test should read it rather than say "
        "it cannot")


# -- D2: the persist that overtakes its own write ------------------------------

def test_stage_settle_arm_persists_without_waiting_out_the_ram_commit(
        staged, gate, adm, writable_id):
    """CD 432129: "burning configuration to flash was an issue if it was not
    delayed long enough after sending configuration messages", and the fix was
    batching the writes and flashing 200 ms after them. `persist()` drains and
    sends immediately, and `cmd_repair` calls write_param then persist, so
    nothing paces the burn except an unrelated measurement window.

    This stage does exactly what the driver does. Flash is first put at REV's
    250 ms default, with settling, so that a stale burn is unmistakable; then
    the declared 20 ms is written and PERSIST is sent with no gap at all. Which
    value is in flash cannot be read on 26.1.6 -- the only reader is a power
    cycle. Three flash cycles per full arm-and-verify run.
    https://www.chiefdelphi.com/t/432129
    """
    staged("settle-arm")
    gate("flash")
    target = int(sa.declared_status_1_period_ms())

    seed = adm.write_param(writable_id, P159, sa.REV_DEFAULT_STATUS_1_PERIOD_MS)
    assert seed is not None and seed["result"] == 0, seed
    time.sleep(FIELD_SETTLING_S * 5)
    assert adm.persist(writable_id) == 0, "seeding the flash did not report success"
    time.sleep(2.5)

    written = adm.write_param(writable_id, P159, target)
    assert written is not None and written["result"] == 0, written
    code = adm.persist(writable_id)

    _save("settle-arm", {"device": writable_id, "written_ms": target,
                         "stale_ms": sa.REV_DEFAULT_STATUS_1_PERIOD_MS,
                         "persist_result": code})
    assert code == 0, f"PERSIST_PARAMETERS answered {code}, not Success"
    print(f"\nARMED: id {writable_id} was written to {target} ms and PERSIST was "
          "sent with no settling delay, and it reported Success.\n"
          "Now cut and restore motor power, then run:\n"
          "  SPARK_HW_STAGE=settle-verify uv run --with pytest pytest "
          "tests/hardware -q --hardware\n")


def test_stage_settle_verify_the_flash_holds_the_value_that_was_written(
        staged, gate, adm):
    """The only claim code cannot make on its own. PERSIST reported Success in
    the arm stage; this reads what actually reached flash.

    MEASURED, rig-flex id 10. Flash was seeded with REV's 250 ms and
    settled; then 20 ms was written and PERSIST was sent about a millisecond
    later, with no settling whatsoever; then the motor rail was cut and restored.
    The controller came back at 20 ms. **D2 did not reproduce on SparkFlex
    26.1.6**: the burn caught the RAM commit, and the failure CD 432129 reports
    on 2021-era firmware did not appear.

    That is one controller, one parameter, one trial, and the failure mode is a
    race, so it is evidence rather than proof. The driver defect the handoff
    names is untouched by it -- `persist()` still sends the instant it is called
    and `cmd_repair` still calls it straight after a write. What this run
    changes is the expected consequence, not the shape of the code.

    Carried as a plain assertion rather than an xfail, because the correct
    outcome is the one that was observed. If a controller ever comes back at the
    value the write replaced, this goes red and that is CD 432129 on this fleet.
    Coming back at neither would be catalogue A3, settings resetting after a
    persist on firmware v26. https://www.chiefdelphi.com/t/432129
    https://www.chiefdelphi.com/t/515478
    """
    staged("settle-verify")
    gate("flash")
    state = _load("settle-arm")
    dev, written, stale = state["device"], state["written_ms"], state["stale_ms"]

    observed = adm.status_period_ms(dev, S1, seconds=6.0)
    try:
        assert observed is not None, (
            f"id {dev} is silent after the power cycle; read `spark faults` "
            "before clearing anything")
        assert observed == pytest.approx(written, abs=30), (
            f"id {dev} came back at {observed} ms. PERSIST answered "
            f"{state['persist_result']} for a write of {written} ms, and flash "
            + (f"holds the {stale} ms that write replaced -- CD 432129, and the "
               "one failure here that looks exactly like success and is permanent"
               if abs(observed - stale) <= 30 else
               "holds neither value, which is catalogue A3"))
    finally:
        adm.write_param(dev, P159, written)
        time.sleep(FIELD_SETTLING_S * 5)
        adm.persist(dev)
        STATE.unlink(missing_ok=True)


# -- B1/A5: does SET_CAN_ID reach flash ----------------------------------------

def test_stage_can_id_arm_moves_one_controller_to_a_free_address(
        staged, gate, adm, writable_id, free_id, serials):
    """REV's spec says SET_CAN_ID "allows changing the CAN ID when multiple
    devices on the bus currently have the same CAN ID" and says nothing about
    persistence; PERSIST_PARAMETERS is the only frame it describes as writing
    non-volatile storage. This repo is inconsistent about it -- `spark set-id`
    prints "NOT persisted", the simulator's default writes flash -- and nothing
    has measured it on 26.1.6.

    The move is addressed by hardware serial, which is what makes it safe to aim
    at one of two controllers sharing an id, and reversible for the same reason:
    the controller can be found and moved back whatever address it comes up on.
    """
    staged("can-id-arm")
    gate("flash")
    serial = serials.get(writable_id)
    assert serial, "serials is not recorded; SET_CAN_ID needs the serial"

    adm.set_can_id(writable_id, serial, free_id)
    inv = adm.inventory(5.0)
    at = sorted(d for d, i in inv.items() if i["serial"] == serial)

    _save("can-id-arm", {"serial": serial, "from_id": writable_id,
                         "to_id": free_id, "answered_at": at})
    assert at == [free_id], (
        f"serial {serial} answers at {at}, not at {free_id}; the move did not "
        "take and there is nothing to power-cycle")
    print(f"\nARMED: serial {serial} moved from id {writable_id} to {free_id} "
          "and was NOT persisted by this suite.\n"
          "Now cut and restore motor power, then run:\n"
          "  SPARK_HW_STAGE=can-id-verify uv run --with pytest pytest "
          "tests/hardware -q --hardware\n")


def test_stage_can_id_verify_says_whether_set_can_id_reached_flash(
        staged, gate, adm, capsys):
    """Both outcomes are findings, and the assertion is on the sentence the tool
    prints rather than on the hardware.

    Coming back at the original id means SET_CAN_ID lives in RAM, `spark set-id`
    is right to tell the operator to persist, and the simulator's default -- which
    writes flash on the move -- is modelling something this firmware does not do.
    Coming back at the new id means the opposite, and `spark set-id`'s advice is
    wrong in the direction that leaves a bus colliding on the old address at the
    next power-up.
    """
    staged("can-id-verify")
    gate("flash")
    state = _load("can-id-arm")
    serial, home, moved = state["serial"], state["from_id"], state["to_id"]

    inv = adm.inventory(6.0)
    at = sorted(d for d, i in inv.items() if i["serial"] == serial)
    assert at, (f"serial {serial} is not broadcasting anywhere after the power "
                f"cycle; ids on the bus are {sorted(inv)}")

    persisted = at == [moved]
    if persisted:
        adm.set_can_id(moved, serial, home)
        time.sleep(FIELD_SETTLING_S * 5)
        adm.persist(home)
    elif at != [home]:
        adm.set_can_id(at[0], serial, home)
    back = sorted(d for d, i in adm.inventory(5.0).items() if i["serial"] == serial)
    capsys.readouterr()
    STATE.unlink(missing_ok=True)

    assert back == [home], (
        f"serial {serial} is at {back} and belongs at {home}. Put it back before "
        f"anything else runs: uv run spark set-id --serial {serial} --to {home}")
    assert at == [home], (
        f"serial {serial} came back at {at}. SET_CAN_ID reached flash on 26.1.6, "
        "so `spark set-id`'s message that the move is NOT persisted is wrong, and "
        "so is the simulator carrying set_can_id_ram_only as an opt-in rather "
        "than as the default")


# -- the connector failures ----------------------------------------------------

@pytest.mark.xfail(reason="a contiguous block of missing ids is reported as N "
                          "independent dead controllers; nothing looks at the shape "
                          "of the loss",
                   strict=False)
def test_stage_unplug_can_the_loss_is_one_finding_and_not_one_per_id(
        staged, adm, roles, serials):
    """Catalogue C2 and C5: a controller that drops off the bus, and the WAGO or
    crimp behind it. A break in the daisy chain removes everything downstream at
    once and permanently, which is a different signature from an intermittent
    dropout -- and the repair is one connector, not four controllers.

    The audit emits one line per absent id, so a chain break reads as N
    independent failures and the shape that would name the connector is thrown
    away. Set SPARK_HW_UNPLUG_ID to the id the connector was pulled at.

    NOT AVAILABLE ON rig-flex, whose SPARK CAN chain is soldered and cannot be
    broken at a controller. Kept for a robot that is connectorised; on this one
    the contiguous-loss signature has no way to be produced, which is a gap in
    the coverage rather than in the suite.
    """
    staged("unplug-can")
    pulled = _env_id()
    inv = adm.inventory(5.0)
    problems = sa.audit_problems(inv, {}, roles, serials)

    missing = sorted(set(roles) - set(inv))
    assert pulled in missing, (
        f"id {pulled} is still broadcasting; the connector is still connected "
        f"or the wrong id was named. Missing: {missing}")
    print(f"\nunplugged at {pulled}; {len(missing)} controller(s) went with it: "
          f"{missing}")

    per_id = [p for p in problems if "is not broadcasting" in p]
    assert len(per_id) <= 1, (
        f"{len(missing)} contiguous ids went silent together and the audit "
        f"emitted {len(per_id)} independent findings: {per_id}")


def test_stage_unplug_encoder_a_sensor_fault_appears_and_reaches_the_audit(
        staged, adm, roles, serials):
    """Catalogue E2 and E3, CD 379472 / CD 371676 / CD 400769: a disconnected
    crimp in the motor data cable produces sensor faults and behaviour teams
    describe as "acting very strange". Teams replace JST cables routinely.

    This is the one physical fault this suite can actually cause and undo, so it
    is the only test here where the fault bit on the wire is the controller's own
    rather than one this host transmitted. If no fault appears, that is itself
    worth recording: it would mean 26.1.6 raises the sensor fault only under
    closed-loop control, and an idle audit could never see a broken encoder
    cable. https://www.chiefdelphi.com/t/379472
    https://www.chiefdelphi.com/t/456113
    """
    staged("unplug-encoder")
    pulled = _env_id()
    status = sa.collect_status(adm.bus, seconds=4.0)
    reading = status.get(pulled)
    assert reading and reading["status1"], (
        f"id {pulled} is not broadcasting STATUS_1; pulling the data cable "
        "should not silence the controller")

    bits = reading["status1"]["faults"] + reading["status1"]["sticky_faults"]
    if "sensor" not in bits:
        pytest.skip(
            f"id {pulled} reports {bits or 'no fault'} with its data cable "
            "removed, so on 26.1.6 a broken encoder cable is invisible to an "
            "idle audit -- record that and read it again under closed loop")

    inv = adm.inventory(3.0)
    problems = sa.audit_problems(inv, {}, roles, serials)
    assert [p for p in problems if "sensor" in p], (
        f"id {pulled} is broadcasting a sensor fault and the audit said "
        f"{[p for p in problems if str(pulled) in p]}")


@pytest.mark.bus_off_expected
@pytest.mark.xfail(reason="audit_problems has no bus-level rule: an empty inventory "
                          "produces one finding per configured id and one more per "
                          "baselined id, and the single likely cause is never named",
                   strict=False)
def test_stage_swap_hl_the_bus_is_dead_and_the_tool_says_so(
        staged, adm, channel, roles, serials):
    """Catalogue C4, CD 460286: "CANSparkMax object created for CAN ID 3, which
    is not a SPARK MAX" -- "each time it was due to a CAN connection being
    backwards (H-L, L-H)." Every controller vanishes at once and none of them is
    faulty.

    The adapter's own error state is the evidence that separates a wiring fault
    from eight dead controllers, and it is one `ip link` call away. The audit
    reads only the inventory, so it prints eight independent findings and never
    names the bus. This test expects the interface to leave ERROR-ACTIVE, so the
    link guard does not fail it. https://www.chiefdelphi.com/t/460286
    """
    staged("swap-hl")
    inv = adm.inventory(5.0)
    info = link_info(channel)
    problems = sa.audit_problems(inv, {}, roles, serials)

    assert not set(roles) & set(inv), (
        f"controllers are still answering with CANH and CANL swapped: "
        f"{sorted(set(roles) & set(inv))}")
    print(f"\nlink state {info['state']}, rx_errors {info['rx_errors']}, "
          f"tx_errors {info['tx_errors']}")
    assert len(problems) <= 2, (
        f"an entire bus went away and the audit emitted {len(problems)} findings, "
        "one per controller, with nothing naming the wiring")


# -- the measurements only a person can take -----------------------------------

def test_stage_termination_the_recorded_resistance_is_two_terminators(staged):
    """Catalogue C7: about 60 ohm across CANH and CANL with power off, which is
    two 120 ohm terminators in parallel. 120 means only one end is terminated and
    the bus will work until it does not; anything else is a wiring fault.

    This asserts a number a person measured and typed in. It cannot verify the
    measurement, and saying so is the point -- an operator who skips the meter
    and guesses gets a green test, so the value belongs in the run log beside the
    reading rather than treated as evidence on its own.

        SPARK_HW_TERMINATION_OHMS=59.7 SPARK_HW_STAGE=termination...
    """
    staged("termination")
    raw = os.environ.get("SPARK_HW_TERMINATION_OHMS", "").strip()
    if not raw:
        pytest.skip("measure CANH to CANL with the robot powered down and set "
                    "SPARK_HW_TERMINATION_OHMS")
    ohms = float(raw)
    assert 55.0 <= ohms <= 65.0, (
        f"{ohms} ohm across CANH/CANL. Two 120 ohm terminators in parallel read "
        "about 60; " + ("about 120 means only one end is terminated"
                        if 100 <= ohms <= 140 else
                        "about 40 means a third terminator is on the bus"
                        if 30 <= ohms <= 50 else
                        "this is neither one terminator nor two"))


def test_stage_led_the_recorded_blink_pattern_is_a_known_one(staged):
    """Catalogue D2 and D1. Blinking magenta means no valid signal and is not a
    fault at all -- commonly a broken motor or encoder crimp, a CAN wiring fault,
    or code that is not addressing that id. Alternating cyan and orange on a
    SPARK MAX is the gate driver fault that survives factory reset and reflash
    and ends in an RMA, and the blue-yellow alternation on a Flex turned out in
    CD 444231 to be missing 12 V to the gate driver rather than a dead unit.

    The LED is the only diagnostic that works when the controller has stopped
    talking, and nothing on the bus can read it. A pattern outside the table is
    one nobody has written down, which is worth stopping on.

        SPARK_HW_LED=blinking-magenta SPARK_HW_STAGE=led...
    """
    staged("led")
    pattern = os.environ.get("SPARK_HW_LED", "").strip().lower()
    if not pattern:
        pytest.skip("read the status LED and set SPARK_HW_LED to one of: "
                    + ", ".join(sorted(LED_PATTERNS)))
    assert pattern in LED_PATTERNS, (
        f"{pattern!r} is not in the catalogue's LED table: "
        + ", ".join(sorted(LED_PATTERNS)))
    print(f"\n{pattern}: {LED_PATTERNS[pattern]}")


def test_stage_inspect_the_recorded_mechanical_check_is_complete(staged):
    """Catalogue E6 and the mechanical half of C5 and E2: Flex and Vortex
    docking screws not fully installed, WAGO connectors with too little
    conductor in them, and a break found under shrink wrap. None of it is
    visible over CAN, and all of it presents as an intermittent electrical
    fault that a software tool will spend a long time chasing.

    Like the termination reading, this records what a person says they checked.

        SPARK_HW_INSPECT=docking-screws,jst-seated,can-connectors,terminator-present
    """
    staged("inspect")
    done = {c.strip() for c in os.environ.get("SPARK_HW_INSPECT", "").split(",")
            if c.strip()}
    if not done:
        pytest.skip("set SPARK_HW_INSPECT to the checks that were made: "
                    + ", ".join(MECHANICAL_CHECKS))
    unknown = sorted(done - set(MECHANICAL_CHECKS))
    assert not unknown, f"not checks this suite knows about: {unknown}"
    outstanding = sorted(set(MECHANICAL_CHECKS) - done)
    assert not outstanding, f"still to check: {outstanding}"


# == catalogue E7: the mode button, recorded rather than run ==================

def test_stage_mode_button_flips_motor_type(staged, adm, roles):
    """Catalogue E7. A three second press of the mode button toggles a SPARK
    between brushless and brushed. On a provisioned drive controller that leaves
    a brushless motor in brushed mode, which is CD 424550's end state: the LED
    lights the right colour, the client answers, and the wheel does not turn.
    https://www.chiefdelphi.com/t/424550

    THIS STAGE IS NOT OFFERED IN THE README AND SHOULD NOT BE RUN. It is written
    out because the procedure is worth recording and because the reason it
    cannot be automated is itself the finding.

    Two walls, and neither is about effort:

      * Confirming the flip needs the motor commanded and observed not to turn.
        Nothing in this suite may send a setpoint -- the bus is armed to fail if
        one appears -- so the detection is out of reach here.
      * Putting it back cannot be confirmed either. Firmware 26.1.6 answers no
        read for Motor Type (parameter 2), so after writing BRUSHLESS the suite
        would be asserting its own intent rather than the controller's state, on
        a drive motor that stays silent until someone asks it to move.

    So the tooling side of E7 is closed differently, by refusing to be the cause:
    parameter 2 is in PROTECTED_PARAMS and write_param raises on it, pinned by
    tests/adversarial/test_config_write.py::test_motor_type_is_not_writable_by_this_tooling.
    The operator side belongs to RHC2 over USB-C, which can read the value back.

    If you are here because a wheel takes setpoints and does not move, the order
    to check is in docs/ADVERSARIAL-TESTING.md section 6 step 3; motor type is
    the item RHC2 has to answer.
    """
    staged("mode-button")
    pytest.skip(
        "E7 has no automated verification: detecting the flip needs a setpoint "
        "this suite may not send, and 26.1.6 answers no read for parameter 2, "
        "so restoring it cannot be confirmed either. Use RHC2 over USB-C.")
