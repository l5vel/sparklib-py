#!/usr/bin/env python3
"""Set the limit-switch polarities back to REV's factory default, and prove it.

WHY THIS BYPASSES A GUARD, AND WHAT IT WILL NOT DO

`spark_admin.write_param` refuses parameters 50-53 outright, and five tests
enforce that no such frame reaches the wire. That guard exists because the
data-port hard limits are a safety interlock, and
disabling or repolarising one is never a repair a tool should make casually.

ONE limit is an interlock; BOTH are a latching stop. Measured rig-max: an asserted forward limit withholds FORWARD output entirely --
applied 0.0000 -- while reverse still drives at the full commanded duty, and
reverse mirrors it. So a single limit left asserted does not make a mechanism
safe, it makes it one-directional, which is worse than either knowing it is
stopped or knowing it is free.

Asserting both while the motor was already turning at 811 rpm zeroed applied
output in 87-90 ms over three trials, stopped the wheel in 490-592 ms, and held
through ten seconds of alternating command with 0.60 in both directions unable to
break through. That is slower than arbitration id 0 (31 ms) and faster than
letting the heartbeat lapse (220 ms), and unlike arbitration id 0 it LATCHES --
no heartbeat overrides it. It is still not the stop: it is addressed per
controller, so a wrong or duplicated id is missed, and the write is
unacknowledged here. The stop is the motor rail.

This script makes exactly one exception, deliberately and narrowly:

  * it writes parameters 50 and 51 ONLY -- Limit Switch Fwd/Rev Polarity
  * it writes False (REV's factory default) or, under --inject, True and then
    False again; no other value, and no other parameter
  * it never touches 52 or 53, so the hard limits stay ENABLED throughout
  * it refuses to run while an enable heartbeat is on the bus, because writing
    an interlock into a controller that is being commanded is the one time it
    could matter

Writing False is safe in both branches. If a controller had drifted to True,
this is the repair. If it was already False, nothing changes and the limit will
stay asserted -- which is itself the answer, because it rules the configuration
out and leaves the data-port hardware.

    uv run python tools/spark_limit_polarity_repair.py              # report only
    uv run python tools/spark_limit_polarity_repair.py --repair
    uv run python tools/spark_limit_polarity_repair.py --repair --persist
    uv run python tools/spark_limit_polarity_repair.py --inject 16 --cycles 3

PROVING THE CAUSE, not just repairing it. `--inject` writes True to 50 and 51 on
one controller and checks whether the hard limits assert, then writes False back
and checks whether they clear, and repeats. Until this runs we have only ever
seen one direction -- writing False cleared a fault that was already there --
which is consistent with polarity being the cause and also with the write merely
disturbing something. Making the fault appear on demand settles it.

The injected value is never persisted. Flash already holds False, so a power
cycle undoes an injection even if this process is killed mid-loop.

Background: docs/spark/runs/-rig-flex-probe-log.md sections 15 and 16.
"""
from __future__ import annotations

import struct
import sys
import time

import can

from sparklib.config import get as _spark_config
from sparklib import admin as sa
from sparklib import cli as spark_cli

POLARITY_PARAMS = {50: "Limit Switch Fwd Polarity", 51: "Limit Switch Rev Polarity"}
REV_DEFAULT = 0                # REV-Specs SparkParameters v0.1.2: BOOL, default false


def declared_polarity():
    """What this robot's config says the limit-switch polarity should be.

    From base.limit_switch_polarity in baseNN_config.yaml, because it follows
    the data-port wiring and so is a property of the robot rather than of the
    motor. The config is the record of INTENT; the controller reports what it
    holds, which is a different question and is read back by `polarity_now`.

    Changing the intended polarity is a config edit in a reviewable file, not a
    number typed at this prompt.
    """
    return int(bool(getattr(_spark_config(), "limit_switch_polarity", REV_DEFAULT)))
INTER_WRITE_S = 0.02           # CD 456184: pace consecutive writes
SECONDARY_HEARTBEAT = 0x02052C80
LIVE_EXPERIMENTS = {"rig-flex-2"}


def blocked_now(adm, roles, seconds=2.5):
    """{dev: [which limits are reached]} for the configured controllers."""
    status = sa.collect_status(adm.bus, seconds=seconds)
    gen = sa.dominant_generation(status)
    out = {}
    for dev in sorted(roles):
        reading = status.get(dev)
        if not reading:
            continue
        # Through normalised_reading, which carries BOTH generations' shapes.
        # Reaching into a raw pre-25 status0 by a firmware-25 key is a KeyError
        # at best -- that generation has no hard-limit FLAGS at all. Its limits
        # arrive as bits 14 and 15 of the fault word instead, which is why both
        # sources are checked here.
        r = sa.normalised_reading(reading, gen)
        hit = []
        if r["hard_forward_limit"] or "hardLimitFwd" in r["faults"]:
            hit.append("FWD")
        if r["hard_reverse_limit"] or "hardLimitRev" in r["faults"]:
            hit.append("REV")
        if hit:
            out[dev] = hit
    return out


def heartbeat_present(channel, seconds=1.5):
    bus = can.Bus(interface="socketcan", channel=channel)
    try:
        end = time.time() + seconds
        while time.time() < end:
            m = bus.recv(timeout=max(0.0, end - time.time()))
            if m is not None and m.arbitration_id == SECONDARY_HEARTBEAT:
                return True
    finally:
        bus.shutdown()
    return False


def write_protected(adm, dev, param_id, value):
    """PARAMETER_WRITE for one guarded parameter, refusing anything else.

    Deliberately not routed through `write_param`, which exists to refuse these.
    The frame is built from spark_admin's own constants so there is still only
    one place the arbitration base is written down.
    """
    if param_id not in POLARITY_PARAMS or value not in (0, 1):
        raise ValueError(
            f"this tool writes only {sorted(POLARITY_PARAMS)} and only 0 or 1; "
            f"refused {param_id}={value}")
    payload = bytes([param_id]) + struct.pack("<I", value)
    adm._drain()
    adm.bus.send(can.Message(arbitration_id=sa.PARAM_WRITE | dev, data=payload,
                             is_extended_id=True))
    data = adm._await(sa.PARAM_WRITE_RESP | dev, 1.5)
    if data is None or len(data) < 7:
        return None
    return {"value": int.from_bytes(data[2:6], "little"), "result": data[6],
            "result_text": sa.WRITE_RESULT.get(data[6], f"code {data[6]}")}


def write_protected_pre25(adm, dev, param_id, value):
    """The same guarded write, in the dialect 24.0.1 actually answers.

    PARAMETER_WRITE is apiClass 14, versionImplemented 25.0.0, so the frame
    write_protected sends is dropped in silence on this generation and the tool
    reports nothing wrong. The legacy dialect puts the parameter id in the
    ARBITRATION id and echoes the value the device took.

    `write_legacy_param` refuses these ids for the same reason `write_param`
    does, so this bypasses it deliberately and narrowly, exactly as
    write_protected does on 25+.
    """
    if param_id not in POLARITY_PARAMS or value not in (0, 1):
        raise ValueError(
            f"this tool writes only {sorted(POLARITY_PARAMS)} and only 0 or 1; "
            f"refused {param_id}={value}")
    arb = adm._legacy_param_arb(dev, param_id)
    adm._drain()
    adm.bus.send(can.Message(arbitration_id=arb,
                             data=struct.pack("<i", value) + bytes([3]),
                             is_extended_id=True))
    r = adm._await_legacy_param(arb, 1.0)
    if r is None:
        return None
    return {"value": r["raw"], "result": 0 if r["ok"] else 1,
            "result_text": "Success" if r["ok"] else f"status {r['status']}"}


RAM_ONLY_REFUSAL = """\
REFUSED: on pre-25 firmware a limit-polarity write is RAM ONLY.

  PERSIST_PARAMETERS is apiClass 63 index 15, versionImplemented 25.0.0, so the
  frame this package burns with does not exist on 24.0.1. The firmware's own
  burn is api 0x072 with the magic 15011 -- measured to work on rig-max -- and this package deliberately neither sends nor exposes it.

  So anything written here is gone at the next power cycle, and a controller
  left with an inverted polarity is holding a hard limit that no flash value
  explains. Someone reading `spark faults` afterwards sees an interlock with no
  cause, and a rail cycle silently "fixes" it.

  That is fine for a deliberate, supervised experiment and it is not fine by
  accident, which is why it is off by default.

  TO PROCEED, pass --allow-ram-only:

      uv run python tools/spark_limit_polarity_repair.py --inject {dev} \\
          --cycles {cycles} --allow-ram-only

  Restore afterwards by cutting and restoring the motor rail, which is what
  makes this safe: the flash value is untouched and comes back on its own."""


def polarity_now(adm, dev, param_id, pre25=False):
    """What the controller actually holds for `param_id`, or None."""
    if pre25:
        r = adm.read_legacy_param(dev, param_id, wait=0.5)
        return None if not r or not r.get("ok") else int(r["raw"])
    return adm.read_param_value(dev, param_id, wait=0.5)


def set_polarity(adm, dev, roles, invert=False, pre25=False):
    """Write both polarity parameters and report what the limits did.

    Writes what the config declares, or its opposite under --inject.

    Routed by GENERATION, not by product. write_protected sends PARAMETER_WRITE,
    apiClass 14, versionImplemented 25.0.0 -- a 24.0.1 device drops it in
    silence, so on pre-25 this tool would have reported "no response" for every
    write and looked like a dead controller rather than a wrong dialect. That is
    the same mistake that made this fleet look unwritable for a day.
    """
    value = (1 - declared_polarity()) if invert else declared_polarity()
    write = write_protected_pre25 if pre25 else write_protected
    for param_id in sorted(POLARITY_PARAMS):
        r = write(adm, dev, param_id, value)
        label = "no response" if r is None else\
            f"{r['result_text']} (echoed {r['value']})"
        print(f"    param {param_id} {POLARITY_PARAMS[param_id]:<26} "
              f"= {value} -> {label}")
        if r is None or r["result"] != 0:
            return None
        # The echo is not enough on 26.1.6: a BOOL parameter accepts and STORES
        # a value outside 0/1, and 2 behaves as the permissive 0.
        held = polarity_now(adm, dev, param_id, pre25=pre25)
        print(f"      read back: {held}")
        if held is not None and held != value:
            print(f"      REFUSING to report success: asked {value}, holds {held}")
            return None
        time.sleep(INTER_WRITE_S)
    time.sleep(0.8)
    return blocked_now(adm, {dev: roles.get(dev, "?")}, seconds=2.0).get(dev, [])


def run_injection(adm, roles, dev, cycles, pre25=False):
    """Make the fault appear on demand, then clear it, and say whether it held."""
    if dev not in roles:
        sys.exit(f"id {dev} is not in devices -- pick one of "
                 f"{sorted(roles)}, or fix the config if the id is real")
    start = blocked_now(adm, roles).get(dev)
    if start:
        sys.exit(f"id {dev} already reports {','.join(start)}; run --repair first "
                 "so the injection starts from a clean controller")

    print(f"\ninjecting on id {dev} ({roles[dev]}) -- {cycles} cycle(s). "
          "Nothing is persisted; flash already holds the factory default.")
    results = []
    try:
        for n in range(1, cycles + 1):
            print(f"\n  cycle {n}: polarity -> {1 - declared_polarity()} "
                  "(the opposite of what the config declares)")
            asserted = set_polarity(adm, dev, roles, invert=True, pre25=pre25)
            print(f"    limits now: {','.join(asserted) if asserted else 'clear'}")
            print(f"  cycle {n}: polarity -> {declared_polarity()} (declared)")
            cleared = set_polarity(adm, dev, roles, pre25=pre25)
            print(f"    limits now: {','.join(cleared) if cleared else 'clear'}")
            results.append((bool(asserted), not cleared))
    finally:
        left = set_polarity(adm, dev, roles, pre25=pre25)
        print(f"\n  left at the declared polarity; limits "
              f"{','.join(left) if left else 'clear'}")

    good = [n for n, (a, c) in enumerate(results, 1) if a and c]
    print(f"\n{len(good)} of {len(results)} cycle(s) reproduced AND repaired.")
    if len(good) == len(results):
        print("CAUSE CONFIRMED: writing True to the limit-switch polarities makes "
              "both hard limits read as reached on this wiring, and writing REV's "
              "factory default False clears them. Repeatable on demand, so the "
              "fault that blocked three wheels was a polarity value and nothing "
              "physical.")
    elif not any(a for a, _ in results):
        print("NOT REPRODUCED: True did not assert the limits, so polarity alone "
              "is not what blocked those three and the earlier repair worked for "
              "some other reason. That is worth knowing before trusting it.")
    else:
        print("MIXED: it reproduced on some cycles and not others, which points "
              "at something intermittent underneath the polarity value.")
    return 0


def main(argv):
    repair = "--repair" in argv
    persist = "--persist" in argv
    inject_id = None
    if "--inject" in argv:
        inject_id = int(argv[argv.index("--inject") + 1])
    cycles = int(argv[argv.index("--cycles") + 1]) if "--cycles" in argv else 1
    # Off by default. A RAM-only interlock that nobody wrote down is a hard limit
    # with no cause, and the rail cycle that clears it also erases the evidence.
    allow_ram_only = "--allow-ram-only" in argv
    channel = _spark_config().can.interface
    roles = spark_cli._spark_roles()
    index = spark_cli._base_index()
    if index in LIVE_EXPERIMENTS:
        sys.exit(f"refusing to write on {index}: it is carrying an experiment. "
                 "Run this on the robot that has the fault, or drop the entry "
                 "from LIVE_EXPERIMENTS at the top of this file once that "
                 "experiment is finished.")

    with sa.SparkAdmin(channel) as adm:
        gen = sa.dominant_generation(sa.collect_status(adm.bus, 2.0))
        pre25 = sa.normalise_generation(gen) == sa.GEN_PRE25
        if pre25:
            print(f"bus reads {gen}: writes go through the legacy dialect, and "
                  "every one of them is RAM only.\n")
        if inject_id is not None:
            if heartbeat_present(channel):
                sys.exit("an enable heartbeat is on the bus, so the drive stack "
                         "is running. Stop it before injecting an interlock.")
            if pre25 and not allow_ram_only:
                sys.exit(RAM_ONLY_REFUSAL.format(dev=inject_id, cycles=cycles))
            return run_injection(adm, roles, inject_id, cycles,
                                 pre25=pre25)
        before = blocked_now(adm, roles)
        print(f"bus {channel} ({index})")
        if not before:
            print("  no controller reports a hard limit reached; nothing to do")
            return 0
        for dev, hit in before.items():
            print(f"  id {dev} ({roles[dev]}) limits reached: {','.join(hit)}")
        if repair and pre25 and not allow_ram_only:
            sys.exit(RAM_ONLY_REFUSAL.format(dev="N", cycles=1)
 .replace("--inject N \\\n          --cycles 1", "--repair"))
        if not repair:
            print(f"\nreport only -- pass --repair to write "
                  f"{declared_polarity()} to parameters "
                  f"{sorted(POLARITY_PARAMS)} (base.limit_switch_polarity)")
            return 0

        if heartbeat_present(channel):
            sys.exit("\nan enable heartbeat is on the bus, so the drive stack is "
                     "running and these controllers are enabled. Stop it before "
                     "writing an interlock parameter.")

        want = declared_polarity()
        print(f"\nwriting {sorted(POLARITY_PARAMS)} = {want} on {sorted(before)} "
              "(base.limit_switch_polarity)")
        for dev in sorted(before):
            for param_id in sorted(POLARITY_PARAMS):
                r = write_protected(adm, dev, param_id, want)
                label = "no response" if r is None else\
                    f"{r['result_text']} (echoed {r['value']})"
                print(f"  id {dev} param {param_id} "
                      f"{POLARITY_PARAMS[param_id]:<26} = {want} -> {label}")
                time.sleep(INTER_WRITE_S)

        time.sleep(1.0)
        after = blocked_now(adm, roles)
        print("\nafter the write:")
        cleared = []
        for dev in sorted(before):
            still = after.get(dev)
            if still:
                print(f"  id {dev} ({roles[dev]}) STILL reports {','.join(still)}"
                      " -- polarity was not the cause here")
            else:
                cleared.append(dev)
                print(f"  id {dev} ({roles[dev]}) limits CLEARED")

        if cleared and persist:
            print("\npersisting the cleared controllers so it survives a power "
                  "cycle:")
            for dev in cleared:
                code = adm.persist(dev)
                print(f"  id {dev} PERSIST_PARAMETERS -> "
                      + ("no response" if code is None else
                         f"{'Success' if code == 0 else 'FAILED'} (code {code})"))
        elif cleared:
            print("\nRAM only -- pass --persist as well, or this returns at the "
                  "next power cycle")

        if not cleared:
            print("\nEvery controller still reports its limits reached with the "
                  "polarity at REV's factory default, so the configuration is "
                  "not what is holding them. That leaves the data port itself: "
                  "swap a data-port connection between a blocked controller and "
                  "a working one and see whether the fault follows the cable.")
        return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
