"""What a rail cycle costs a pre-25 SPARK MAX, on real motors.

Two separate questions, and the second is not a feature request.

**Is every CAN write to a MAX volatile?** Believed on the strength of one
measurement: rig-max, eight controllers set to BRAKE, rail cut and
restored, all eight back at COAST with sticky 0x0200. The stages below re-run
that as a test rather than as an operator's note, on whichever parameter is
safest, and they read the sticky word before anything clears it.

**Does the pre-25 burn flash at api 0x072 exist at all?** ANSWERED HERE, by these stages. It does, and `pre25.burn_flash_api` in
provenance.py carries the evidence.

The sequence is worth keeping, because the first answer was wrong. Run 1 sent the
frame with DLC 0 to ids 3 and 4 against a no-burn control on id 8; nothing came
back and after a rail cycle all three had reverted. Read alone that says the
command does not exist. It only settled the frame AS SENT: the 25+
PERSIST_PARAMETERS carries a two-byte magic so a stray frame cannot spend a flash
cycle, and a pre-25 burn plausibly wanted the same. Run 2 sent the magic, 15011
little-endian. Both controllers answered 0x00, and after a rail cycle both held
the written value while the control reverted. A payload sweep on ids 1, 2, 5, 6
and 7 then showed the rule: only the first two bytes are read, the magic is
answered 0x00, a wrong value, a big-endian magic and a single byte are answered
0xFF, and a zero-length frame draws no reply at all -- which is why run 1 was
silent. Run 3 burned a distinctive 77 ms status period; it read 10.0 ms after the
cycle, so the burn does NOT reach the periods.

What is still uncited is the api NUMBER's origin: every document tracing it
reaches one comment in tests/support/sparkhw/wire.py which derives the
arbitration ARITHMETIC and not the number. The behaviour is measured; the
provenance of the number is not. It stays on the injector denylist, and it is a
better candidate for denial now than when it was unverified.

The burn stages remain an EXPERIMENT with an abort criterion, not a repair. They
refuse to run without `SPARK_HW_BURN_FLASH=1` and an explicit `SPARK_HW_ID`,
because a real flash write aimed at a controller that broadcasts no serial is not
recoverable by any `spark` subcommand if it goes wrong.

    # the volatility proof, no unknown frames sent
    SPARK_MAX_CYCLE_STAGE=arm uv run --with pytest pytest \
        tests/hardware/test_legacy_burn_flash.py -q --hardware
    #   cut and restore the motor rail
    SPARK_MAX_CYCLE_STAGE=verify uv run --with pytest pytest \
        tests/hardware/test_legacy_burn_flash.py -q --hardware

    # the experiment, on ONE controller you have chosen
    SPARK_HW_BURN_FLASH=1 SPARK_HW_ID=<id> SPARK_MAX_CYCLE_STAGE=burn-arm...
    #   cut and restore the motor rail
    SPARK_HW_BURN_FLASH=1 SPARK_HW_ID=<id> SPARK_MAX_CYCLE_STAGE=burn-verify...

Sends no setpoints and starts no heartbeat: controllers stay disabled throughout.
"""

import json
import os
import pathlib
import struct
import time

import pytest

from sparklib import admin as sa

pytestmark = pytest.mark.hardware

STAGE = os.environ.get("SPARK_MAX_CYCLE_STAGE", "")
STATE = pathlib.Path(os.environ.get(
    "SPARK_MAX_CYCLE_STATE", "/tmp/spark_max_cycle_stage.json"))

# Idle Mode: 0 COAST, 1 BRAKE. The parameter rig-max's proof was run on, and the
# safest one to move -- a controller that is not being commanded does not care
# which it is in, and an operator can confirm it by hand. It is NOT a status
# period: periods are not parameters on this generation.
P_IDLE_MODE = 6
COAST, BRAKE = 0, 1

# api 0x072, arbitration id 0x02051C80 | id. Named here rather than in
# spark_admin because a driver that carries the constant is one edit away from
# sending it, and nothing in the driver may send it while the claim is open.
LEGACY_BURN_FLASH = 0x02051C80


def _burn_payload():
    """The payload to send with api 0x072, and a label for the record.

    The PAYLOAD is the variable under test, so it is an input rather than a
    literal. The run sent DLC 0 and committed nothing, which settles
    that frame and not the api: the 25+ PERSIST_PARAMETERS carries a two-byte
    magic (15011) specifically so a stray frame cannot spend a flash cycle, and a
    pre-25 burn plausibly has the same guard, in which case a zero-length frame
    is correctly ignored.

        SPARK_HW_BURN_PAYLOAD=empty   DLC 0, the form already measured
        SPARK_HW_BURN_PAYLOAD=magic   struct.pack("<H", 15011), as the 25+ persist
        SPARK_HW_BURN_PAYLOAD=<hex>   anything else, e.g. "A3 3A" or "a33a"
    """
    raw = os.environ.get("SPARK_HW_BURN_PAYLOAD", "empty").strip().lower()
    if raw in ("", "empty", "none"):
        return b"", "empty (DLC 0)"
    if raw == "magic":
        return struct.pack("<H", sa.PERSIST_MAGIC), f"magic {sa.PERSIST_MAGIC} LE"
    data = bytes.fromhex(raw.replace(" ", ""))
    assert len(data) <= 8, f"a CAN frame carries at most 8 bytes, got {len(data)}"
    return data, f"0x{data.hex()}"


def _listen_for_a_reply(bus, dev, seconds=1.0):
    """Any frame from this device that is not one of its periodic broadcasts.

    Worth capturing because it would settle the question WITHOUT a rail cycle: a
    command that does not exist is dropped in silence, so anything coming back on
    a non-status api is evidence the frame was understood. The 25+ persist answers
    on PERSIST_RESP; nobody knows what, if anything, the pre-25 one does.
    """
    import time as _t
    periodic = {0x060 + i for i in range(8)}
    seen, end = [], _t.time() + seconds
    while _t.time() < end:
        m = bus.recv(timeout=max(0.0, end - _t.time()))
        if m is None:
            continue
        arb = m.arbitration_id
        if (arb & 0x3F) != dev:
            continue
        api = (arb >> 6) & 0x3FF
        if api in periodic:
            continue
        seen.append((arb, bytes(m.data)))
    return seen


@pytest.fixture
def pre25_bus(adm):
    """The admin, once this bus has been confirmed pre-25 on the wire."""
    gen = sa.dominant_generation(sa.collect_status(adm.bus, 4.0))
    if gen != sa.GEN_PRE25:
        pytest.skip(f"this bus reads {gen}; everything here is about pre-25")
    return adm


@pytest.fixture
def chosen_ids():
    """The controllers the burn experiment may touch. No default, ever.

    Comma-separated, so one rail cycle settles more than one controller. Two is
    worth more than one here: the question is whether api 0x072 does anything
    at all, and a single silent controller cannot distinguish "the command does
    not exist" from "this controller did not take it".
    """
    raw = os.environ.get("SPARK_HW_ID", "").strip()
    if not raw:
        pytest.skip(
            "set SPARK_HW_ID to the controller(s) you have chosen, comma "
            "separated. There is no default: pick the ones whose loss costs "
            "least. Pre-25 broadcasts no serial, so a controller that stops "
            "answering cannot be found by any means this package has")
    return [int(x) for x in raw.split(",") if x.strip()]


def _require_burn_gate():
    if os.environ.get("SPARK_HW_BURN_FLASH") != "1":
        pytest.skip(
            "set SPARK_HW_BURN_FLASH=1 to send api 0x072. It is a REAL flash "
            "write -- measured on rig-max to commit the parameter "
            "table -- and it sits on the injector denylist for that reason. It "
            "spends a finite flash cycle and outlives the power cycle. Read "
            "pre25.burn_flash_api in provenance.py before arming it")


# -- preconditions ------------------------------------------------------------

def test_the_bus_is_whole_before_anything_is_written(pre25_bus):
    inv = pre25_bus.inventory(4.0)
    assert inv, "no controllers broadcasting; run `spark clear` first"
    dups = pre25_bus.duplicates(4.0)
    assert not dups, f"duplicate CAN ids present, refusing to write: {dups}"


def test_the_25plus_write_dialect_is_still_absent_on_this_firmware(pre25_bus):
    """The finding that cost a day on rig-max, kept as a regression.

    A wrong dialect and a dead controller produce the same wire behaviour, which
    is nothing. If a firmware update ever makes this answer, the whole pre-25
    branch of the driver is the wrong path for this fleet and `spark verify`
    should be re-run before anything else.
    """
    dev = sorted(pre25_bus.inventory(4.0))[0]
    assert pre25_bus.write_param(dev, sa.PARAM_STATUS_1_PERIOD, 100) is None, (
        f"id {dev} answered PARAMETER_WRITE. That frame is versionImplemented "
        "25.0.0, so this bus is not pre-25 and every generation gate in this "
        "package is now reading it wrong")


def test_every_parameter_answers_a_read_on_this_generation(pre25_bus):
    """The capability that makes the volatility proof measurable at all. On a
    Flex the value could only be inferred from broadcast behaviour."""
    dev = sorted(pre25_bus.inventory(4.0))[0]
    r = pre25_bus.read_legacy_param(dev, sa.PARAM_CAN_ID)
    assert r is not None and r["ok"], f"id {dev} answered no parameter read: {r}"
    assert r["raw"] == dev, (
        f"parameter 0 on id {dev} returned {r['raw']}. It is the device's own "
        "CAN id, and a mismatch means the reads are not per-device")


# -- the volatility proof, no unknown frames ----------------------------------

@pytest.mark.skipif(STAGE != "arm", reason="set SPARK_MAX_CYCLE_STAGE=arm")
def test_stage_arm_write_brake_to_every_controller(pre25_bus, gate):
    gate("inject")
    written = {}
    for dev in sorted(pre25_bus.inventory(4.0)):
        before = pre25_bus.read_legacy_param(dev, P_IDLE_MODE)
        assert before is not None and before["ok"], f"id {dev} read no Idle Mode"
        w = pre25_bus.write_legacy_param(dev, P_IDLE_MODE, BRAKE)
        assert w is not None and w["ok"] and w["verified"], (
            f"id {dev} refused the write or echoed the wrong value: {w}")
        back = pre25_bus.read_legacy_param(dev, P_IDLE_MODE)
        assert back["raw"] == BRAKE, (
            f"id {dev} echoed BRAKE and reads back {back['raw']}")
        written[dev] = before["raw"]

    pre25_bus.clear_faults(sorted(written))
    STATE.write_text(json.dumps({"was": written, "wrote": BRAKE,
                                 "at": time.time()}))
    print(f"\nARMED: {len(written)} controller(s) at BRAKE, sticky faults "
          "cleared.\nCut and restore the MOTOR RAIL, then run stage `verify`.\n"
          "Do NOT run `spark clear` in between: the sticky word is the only "
          "record that the cycle happened.")


@pytest.mark.skipif(STAGE != "verify", reason="set SPARK_MAX_CYCLE_STAGE=verify")
def test_stage_verify_the_write_did_not_survive_the_rail_cycle(pre25_bus):
    """The claim under test is that it did NOT survive.

    A pass here is a controller back at COAST. A failure means something on this
    fleet persists a CAN write, which would settle SPARKMAX.md item 1 in the
    other direction and is a finding, not a bug in this test. Record it in
    spark_provenance before changing anything downstream.
    """
    assert STATE.exists(), "run stage `arm` first"
    state = json.loads(STATE.read_text())
    was = {int(d): v for d, v in state["was"].items()}

    inv = pre25_bus.inventory(6.0)
    assert set(was) <= set(inv), (
        f"silent after the cycle: {sorted(set(was) - set(inv))}. Run "
        "`spark clear` and re-read, then re-run this stage")

    survived, reset_seen = [], []
    for dev in sorted(was):
        r = pre25_bus.read_legacy_param(dev, P_IDLE_MODE)
        assert r is not None and r["ok"], f"id {dev} read no Idle Mode: {r}"
        if r["raw"] != COAST:
            survived.append((dev, r["raw"]))

    status = sa.collect_status(pre25_bus.bus, 4.0)
    for dev in sorted(was):
        reading = sa.normalised_reading(status.get(dev) or {}, sa.GEN_PRE25)
        if "hasReset" in reading["sticky_faults"]:
            reset_seen.append(dev)

    assert reset_seen == sorted(was), (
        f"hasReset is set on {reset_seen} of {sorted(was)}. Bit 9 is the only "
        "evidence a MAX rebooted, so a controller missing it either did not "
        "cycle or had its sticky word cleared before this ran")
    assert not survived, (
        f"a CAN write SURVIVED a power cycle on {survived}. That contradicts "
        "max.param_writes_are_ram_only. Settle it in spark_provenance before "
        "anything downstream is changed")

    STATE.unlink(missing_ok=True)
    print(f"\nCONFIRMED: {len(was)} controller(s) back at COAST with sticky "
          "hasReset. Every CAN write to this fleet is RAM only.\n"
          "Re-apply the status periods with `uv run spark throttle` before "
          "leaving the bus unattended.")


# -- the experiment -----------------------------------------------------------

@pytest.mark.skipif(STAGE != "burn-arm", reason="set SPARK_MAX_CYCLE_STAGE=burn-arm")
def test_stage_burn_arm_send_the_flash_command(pre25_bus, chosen_ids, gate):
    """Write one harmless value, send api 0x072, and record enough to diagnose
    a controller that stops answering.

    Aborts before sending if the device does not answer parameter 0, because a
    controller that is already unwell is the wrong one to aim an unknown frame at.
    """
    import can

    gate("inject")
    gate("flash")
    _require_burn_gate()

    record = {}
    for dev in chosen_ids:
        table = {}
        for pid in range(0, sa.LEGACY_PARAM_MAX + 1):
            r = pre25_bus.read_legacy_param(dev, pid, wait=0.4)
            if r is not None and r["ok"]:
                table[pid] = r["raw"]
        assert table.get(sa.PARAM_CAN_ID) == dev, (
            f"id {dev} did not return its own CAN id from parameter 0. Aborting "
            "before sending a real flash command to a controller that is not "
            "answering normally")
        assert len(table) > 100, (
            f"only {len(table)} of 134 parameters answered on id {dev}; this "
            "fleet reads all of them. Aborting rather than sending 0x072 into "
            "that")

        was = table.get(P_IDLE_MODE, COAST)
        target = BRAKE if was == COAST else COAST
        w = pre25_bus.write_legacy_param(dev, P_IDLE_MODE, target)
        assert w is not None and w["ok"] and w["verified"], (
            f"id {dev}: the write failed: {w}")

        payload, label = _burn_payload()
        pre25_bus._drain()
        pre25_bus.bus.send(can.Message(arbitration_id=LEGACY_BURN_FLASH | dev,
                                       data=payload, is_extended_id=True))
        replies = _listen_for_a_reply(pre25_bus.bus, dev)
        time.sleep(sa.PERSIST_SETTLE_S)

        after = pre25_bus.read_legacy_param(dev, P_IDLE_MODE)
        assert after is not None and after["raw"] == target, (
            f"id {dev} lost the RAM value when 0x072 was sent, which means the "
            f"frame did something and it was not a burn: {after}")
        record[dev] = {"was": was, "wrote": target, "table": table,
                       "payload": label,
                       "replies": [[f"0x{a:08X}", d.hex()] for a, d in replies]}
        print(f"  id {dev}: Idle Mode {was} -> {target}, sent "
              f"0x{LEGACY_BURN_FLASH | dev:08X} payload {label}, "
              f"{len(table)} parameters recorded")
        if replies:
            print(f"    ANSWERED -- {len(replies)} non-status frame(s) came back, "
                  "which alone says the command was understood:")
            for a, d in replies:
                print(f"      0x{a:08X}  {d.hex() or '(empty)'}")
        else:
            print("    no reply, which is what an unmatched extended id does")

    STATE.write_text(json.dumps({"devices": record, "at": time.time()}))
    print(f"\nARMED: {len(record)} controller(s) written and sent 0x072.\n"
          f"Their full parameter tables are saved in {STATE}.\n"
          "Cut and restore the MOTOR RAIL, then run stage `burn-verify`.")


# -- does the burn reach the STATUS PERIODS? ----------------------------------
#
# The periods are the part that matters operationally: they are what a rail cycle
# drops, and leaving a MAX fleet at REV's cold defaults puts roughly six times the
# frame rate on a full-speed gs_usb adapter, which is the wedge risk. If the burn
# reaches them, that problem disappears permanently. If it does not, `spark
# throttle` stays mandatory after every power event.
#
# It was ASSUMED not to, on the grounds that ids 158-165 are outside
# LEGACY_PARAM_MAX. That is a fact about this DRIVER's addressing, not about the
# firmware: the periods move on api class 6, and whether the burn commits them is
# a separate question that the parameter table cannot answer.

PERIOD_MARKER_MS = 77       # distinctive: neither REV's 10 nor the throttle's 50
PERIOD_FRAME = 0            # 0x060, the frame the audit scores


@pytest.mark.skipif(STAGE != "period-arm", reason="set SPARK_MAX_CYCLE_STAGE=period-arm")
def test_stage_period_arm_burn_a_distinctive_status_period(pre25_bus, chosen_ids,
                                                           gate):
    """Set one controller's 0x060 to a value nothing else produces, then burn.

    A marker rather than the throttle value, so the read-back cannot be confused
    with `spark throttle` having been re-run, with apply_boot_config, or with a
    controller that simply never lost anything.
    """
    import can

    gate("inject")
    gate("flash")
    _require_burn_gate()

    record = {}
    for dev in chosen_ids:
        pre25_bus.set_legacy_status_period(dev, PERIOD_FRAME, PERIOD_MARKER_MS)

        # Wait for the new cadence to REACH THE WIRE before measuring it. Nothing
        # acknowledges this write, so the cadence is the only read-back, and a
        # window that straddles the change averages the old period with the new
        # one and reports a number the controller never ran at. cmd_repair has
        # the same loop for the same reason. Measured on rig-max: a
        # window opened immediately after the write read 66.7 ms for a 77 ms
        # target, and the same read a second later gave 76.9 ms and stayed there.
        measured, deadline = None, time.time() + 8.0
        while time.time() < deadline:
            measured = pre25_bus.status_period_ms(dev, sa.LEGACY_STATUS_0_API,
                                                  seconds=2.0)
            if measured is not None and sa._near(measured, PERIOD_MARKER_MS):
                break
        assert measured is not None and sa._near(measured, PERIOD_MARKER_MS), (
            f"id {dev}: the period write did not take: measured {measured} ms "
            f"against {PERIOD_MARKER_MS} ms after settling. Nothing acknowledges "
            "this write, so the cadence is the only read-back")

        payload, label = _burn_payload()
        pre25_bus._drain()
        pre25_bus.bus.send(can.Message(arbitration_id=LEGACY_BURN_FLASH | dev,
                                       data=payload, is_extended_id=True))
        replies = _listen_for_a_reply(pre25_bus.bus, dev)
        assert replies, (
            f"id {dev}: the burn was not acknowledged with payload {label}. It "
            "answers 0x00 to the magic and 0xFF to anything else; silence means "
            "the frame was not understood at all")
        code = replies[0][1].hex()
        assert code == "00", (
            f"id {dev}: the burn was REFUSED (0x{code}) with payload {label}")
        record[dev] = {"marker_ms": PERIOD_MARKER_MS, "measured": measured,
                       "payload": label, "reply": code}
        print(f"  id {dev}: 0x060 set to {measured} ms, burn accepted (0x{code})")

    STATE.write_text(json.dumps({"period_devices": record, "at": time.time()}))
    print(f"\nARMED: {len(record)} controller(s) at {PERIOD_MARKER_MS} ms and "
          "burned.\nEvery OTHER controller is the control -- they are at the "
          "boot throttle and were not burned in this state.\n"
          "Cut and restore the MOTOR RAIL, then run stage `period-verify`.\n"
          "Do NOT run `spark throttle` before that stage: it would re-apply the "
          "periods and destroy the reading.")


@pytest.mark.skipif(STAGE != "period-verify",
                    reason="set SPARK_MAX_CYCLE_STAGE=period-verify")
def test_stage_period_verify_whether_the_burn_reached_the_periods(pre25_bus):
    """Read the cadence before anything re-applies it.

    Reports rather than asserts an outcome: both answers are findings. What it
    DOES assert is that the run is interpretable -- an unburned controller must
    have reverted, or the rail did not drop and nothing can be concluded.
    """
    assert STATE.exists(), "run stage `period-arm` first"
    state = json.loads(STATE.read_text())
    devices = {int(d): v for d, v in state["period_devices"].items()}

    inv = pre25_bus.inventory(6.0)
    assert set(devices) <= set(inv), (
        f"silent after the cycle: {sorted(set(devices) - set(inv))}")

    rev_default = float(sa.REV_DEFAULT_LEGACY_STATUS_0_PERIOD_MS)
    others = sorted(set(inv) - set(devices))
    assert others, "no unburned controller left to read as a control"

    def near(v, target):
        return v is not None and abs(v - target) <= max(3.0, target * 0.25)

    control_ms = {d: pre25_bus.status_period_ms(d, sa.LEGACY_STATUS_0_API,
                                                seconds=3.0) for d in others}
    reverted = [d for d, v in control_ms.items() if near(v, rev_default)]
    assert reverted, (
        f"no unburned controller came back at REV's {rev_default} ms default: "
        f"{control_ms}. Either the rail did not drop or something re-applied the "
        "throttle, and nothing about the burn can be concluded from this run")

    print(f"\ncontrols (not burned at the marker): "
          + ", ".join(f"id {d} {v} ms" for d, v in sorted(control_ms.items())))

    kept = {}
    for dev, rec in sorted(devices.items()):
        ms = pre25_bus.status_period_ms(dev, sa.LEGACY_STATUS_0_API, seconds=3.0)
        kept[dev] = near(ms, rec["marker_ms"])
        print(f"  id {dev}: burned at {rec['marker_ms']} ms, now {ms} ms -> "
              + ("KEPT" if kept[dev] else "reverted"))

    if all(kept.values()):
        print("\nThe burn DOES reach the status periods on 24.0.1. The boot "
              "throttle can be made permanent, and the gs_usb wedge risk from a "
              "cold bus after a rail cycle is fixable once and for all.")
    elif not any(kept.values()):
        print("\nThe burn does NOT reach the status periods. They are not part "
              "of what api 0x072 commits, so `spark throttle` stays mandatory "
              "after every power event and the cold-bus wedge risk stands.")
    else:
        print("\nMIXED, which means neither. Re-run before concluding anything.")
    print("Record the outcome against pre25.burn_flash_api in provenance.py.")
    STATE.unlink(missing_ok=True)


@pytest.mark.skipif(STAGE != "burn-verify",
                    reason="set SPARK_MAX_CYCLE_STAGE=burn-verify")
def test_stage_burn_verify_whether_the_value_reached_flash(pre25_bus):
    """One read settles pre25.burn_flash_api, whichever way it goes.

    This test does not assert an outcome. Both answers are findings and neither
    is a failure of the driver: it reports which one happened and refuses to be
    read as a pass for either. Record the result in spark_provenance with
    how=HARDWARE and the robot it was measured on.
    """
    assert STATE.exists(), "run stage `burn-arm` first"
    state = json.loads(STATE.read_text())
    devices = {int(d): v for d, v in state["devices"].items()}

    inv = pre25_bus.inventory(6.0)
    silent = sorted(set(devices) - set(inv))
    assert not silent, (
        f"silent after the cycle: {silent}. Run `spark clear` and re-read. If "
        "they stay silent, 0x072 is not a burn command and those controllers "
        "need REV Hardware Client over USB-C")

    results = {}
    for dev, rec in sorted(devices.items()):
        r = pre25_bus.read_legacy_param(dev, P_IDLE_MODE)
        assert r is not None and r["ok"], (
            f"id {dev} answers no parameter read after the cycle: {r}")
        results[dev] = r["raw"] == rec["wrote"]

    # The control: written the same value through the same dialect, but never
    # sent 0x072. It is what separates "the burn did nothing" from "this rail
    # cycle did not actually happen" -- without it, every controller coming back
    # at COAST has two explanations and the run settles nothing.
    control = state.get("control")
    if control:
        cdev = int(control["device"])
        cr = pre25_bus.read_legacy_param(cdev, P_IDLE_MODE)
        assert cr is not None and cr["ok"], (
            f"control id {cdev} answers no parameter read: {cr}")
        control_survived = cr["raw"] == control["wrote"]
        assert not control_survived, (
            f"the CONTROL on id {cdev} kept its RAM value across the rail cycle "
            "without any burn command being sent. Either the rail did not "
            "actually drop or pre-25 writes are not volatile after all, and "
            "either way nothing about api 0x072 can be concluded from this run")
        print(f"\ncontrol id {cdev}: reverted to {cr['raw']} as it must, so the "
              "rail cycle really happened and pre-25 writes really are volatile")

    print(f"\nRESULT for pre25.burn_flash_api, rig-max:")
    for dev, persisted in sorted(results.items()):
        rec = devices[dev]
        print(f"  id {dev}: payload {rec.get('payload', 'empty')}, wrote "
              f"{rec['wrote']}, was {rec['was']}, now "
              f"{'SURVIVED' if persisted else 'reverted'}"
              + (f", and it ANSWERED {len(rec['replies'])} frame(s) when sent"
                 if rec.get("replies") else ""))
    if all(results.values()):
        print("\napi 0x072 COMMITS RAM TO FLASH on firmware 24.0.1. Every "
              "controller kept the value across the rail cycle.")
    elif not any(results.values()):
        print("\napi 0x072 does nothing on firmware 24.0.1: every value "
              "reverted, exactly as if the frame had never been sent. The "
              "command either does not exist on this firmware or is not a burn.")
    else:
        print("\nMIXED, which is the one answer that means neither. Do not "
              "record this as settled; re-run before concluding anything.")
    print("Record the outcome in provenance.py as how=HARDWARE with the "
          "robot and the date, then update SPARKMAX.md item 1.")

    # Put every controller back whichever way it went. If the burn DID work this
    # write is RAM only and the flashed value stands, which is worth saying out
    # loud rather than leaving an operator to discover it.
    for dev, rec in sorted(devices.items()):
        pre25_bus.write_legacy_param(dev, P_IDLE_MODE, rec["was"])
    if any(results.values()):
        print("\nNOTE: the restore above is a RAM write. On any controller "
              "where the burn worked, the FLASHED value is still the one that "
              "was burned and it will come back at the next power cycle. Use "
              "REV Hardware Client over USB-C to put those back permanently.")
    STATE.unlink(missing_ok=True)


# -- duplicate CAN ids, verified on hardware by injection ---------------------

def test_duplicate_detection_finds_an_injected_second_responder(pre25_bus, gate):
    """The true-positive case, on real controllers, without moving a CAN id.

    Two controllers on one id is the failure the identity work exists to solve,
    and staging a real one means reassigning an id on a fleet that cannot be
    recovered by serial if it goes wrong. Injecting a second REPLY to the
    fingerprint request tests the same code path against real bus traffic and
    changes nothing on any controller.

    This is also why read_fingerprint discriminates by frame LENGTH rather than
    by is_rx. Measured on rig-max: an is_rx filter excluded the
    injected frames entirely -- SocketCAN flags every locally generated frame as
    loopback, so 124 injected replies arrived with is_rx False and were dropped.
    Length works because the request is the zero-length frame and a reply never
    is, which is the same DLC discrimination this firmware uses for period
    writes.
    """
    import threading

    import can

    gate("inject")
    dev = sorted(pre25_bus.inventory(4.0))[0]
    arb = 0x02050000 | (sa.LEGACY_FINGERPRINT_API << 6) | dev

    assert pre25_bus.duplicates(generation=sa.GEN_PRE25, ids=[dev]) == {}, (
        "premise: this id must read clean before anything is injected")

    stop = threading.Event()

    def inject():
        bus = can.Bus(interface="socketcan", channel=pre25_bus.channel)
        try:
            while not stop.is_set():
                bus.send(can.Message(arbitration_id=arb,
                                     data=bytes.fromhex("DEADBEEF"),
                                     is_extended_id=True))
                time.sleep(0.01)
        finally:
            bus.shutdown()

    t = threading.Thread(target=inject, daemon=True)
    t.start()
    try:
        time.sleep(0.2)
        dups = pre25_bus.duplicates(generation=sa.GEN_PRE25, ids=[dev])
    finally:
        stop.set()
        t.join(timeout=2.0)
        time.sleep(0.5)

    assert dev in dups and len(dups[dev]) == 2, (
        f"a second responder on id {dev} was not detected: {dups}")
    assert pre25_bus.duplicates(generation=sa.GEN_PRE25, ids=[dev]) == {}, (
        "the id still reads as duplicated after the injection stopped")
