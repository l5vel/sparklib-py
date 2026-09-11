#!/usr/bin/env python3
"""Do SparkFlex sticky faults survive a power cycle?

`can_bus.py` justifies clearing sticky faults on every SparkFlex it
initialises with this claim:

    sticky faults (SparkFlex latches them across power cycles, so a
    browned-out controller comes back faulted and silently won't drive)

Nothing has tested it, and it is load-bearing: if the claim is false, the
boot-time clear destroys the record of every brownout and reboot to solve a
problem that does not exist. If it is true, fault state has some non-volatile
backing and "is clearing it repeatedly harmless" becomes a real question rather
than a rhetorical one.

WHY THIS NEEDS A MARKER. `hasReset` is set BY the power cycle, so a controller
showing it afterwards proves nothing about survival. The test needs a different
sticky bit, set before the cycle, whose cause is gone by the time the rail is
cut. The `can` bit that appeared on all eight during a full run of the hardware
injection suite is the candidate, and stage `find` hunts for what raises it.

    uv run python tools/spark_sticky_survival.py find     # what sets a sticky bit
    uv run python tools/spark_sticky_survival.py arm      # set it, confirm, record
    #   cut and restore the motor rail
    uv run python tools/spark_sticky_survival.py verify   # wake, read, verdict

Stage `verify` wakes the bus with GET_FIRMWARE rather than Clear Faults, because
a read restores broadcasting and erases nothing -- see PROBE-LOG section 11.
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import threading
import time

import can

from sparklib.config import get as _spark_config
from sparklib import admin as sa
from sparklib import cli as spark_cli

STATE = pathlib.Path("/tmp/spark_sticky_survival.json")

# Manufacturer 0xFF is unclaimed, so nothing here decodes it. The low id wins
# arbitration against every SPARK frame; the high one only fills the gaps.
FILLER_WINS = 0x01FF0000
FILLER_YIELDS = 0x1FFFFFFF
# Eight bytes unlike anything a healthy controller sends, for the collision trial.
IMPOSTOR = bytes([0xA5, 0x5A] * 4)


def link_state(channel):
    out = subprocess.run(["ip", "-details", "-json", "link", "show", channel],
                         capture_output=True, text=True, timeout=5, check=False)
    try:
        info = json.loads(out.stdout)[0]["linkinfo"]["info_data"]
    except Exception:
        return "unknown"
    return info.get("state", "unknown")


class Sender:
    """Transmit one frame repeatedly in the background."""

    def __init__(self, channel, arb, data=bytes(8), period_s=0.0):
        self.bus = can.Bus(interface="socketcan", channel=channel)
        self.msg = can.Message(arbitration_id=arb, data=data, is_extended_id=True)
        self.period_s = period_s
        self.count = 0
        self.error = None
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        while not self._stop.is_set():
            try:
                self.bus.send(self.msg, timeout=0.5)
                self.count += 1
            except can.CanError as exc:
                self.error = repr(exc)
                return
            if self.period_s:
                self._stop.wait(self.period_s)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._thread.join(timeout=2.0)
        self.bus.shutdown()
        return False


def sticky(adm, seconds=2.5):
    """{dev: (sticky_faults, sticky_warnings)} for whoever is broadcasting."""
    st = sa.collect_status(adm.bus, seconds=seconds)
    return {d: (v["status1"]["sticky_faults"], v["status1"]["sticky_warnings"])
            for d, v in sorted(st.items()) if v["status1"]}


def non_reset_bits(reading):
    """Sticky state ignoring hasReset, which every power cycle sets by itself."""
    out = {}
    for dev, (faults, warns) in reading.items():
        rest = [w for w in warns if w != "hasReset"]
        if faults or rest:
            out[dev] = (faults, rest)
    return out


def wake(adm, roles):
    """One read frame restores broadcasting and erases nothing."""
    for dev in sorted(roles):
        if adm.firmware(dev, wait=0.4)[0]:
            return dev
    return None


def cmd_find(channel, adm, roles):
    subject = sorted(roles)[-1]
    print("clearing every sticky byte so the trials start from a known state")
    print("  (this erases the hasReset left by the last staged cycle, which is "
          "already recorded in the probe log)")
    adm.clear_faults(sorted(roles))
    time.sleep(1.5)
    base = sticky(adm)
    print(f"  baseline: {non_reset_bits(base) or 'clean'}")

    trials = [
        ("high-priority filler, 30 s continuous",
         lambda: [Sender(channel, FILLER_WINS)], 30.0),
        ("STATUS_0 collision on id %d, 30 s at 5 ms" % subject,
         lambda: [Sender(channel, 0x0205B800 | subject, IMPOSTOR, 0.005)], 30.0),
        ("both at once, 30 s",
         lambda: [Sender(channel, FILLER_WINS),
                  Sender(channel, 0x0205B800 | subject, IMPOSTOR, 0.005)], 30.0),
        ("low-priority filler, 30 s",
         lambda: [Sender(channel, FILLER_YIELDS)], 30.0),
    ]
    for name, make, seconds in trials:
        if link_state(channel) == "BUS-OFF":
            print(f"\nadapter is BUS-OFF; recover it and re-run:\n"
                  f"  sudo ip link set {channel} down && sudo ip link set {channel} up")
            return 1
        print(f"\ntrial: {name}")
        senders = make()
        try:
            for s in senders:
                s.__enter__()
            time.sleep(seconds)
        finally:
            for s in senders:
                s.__exit__()
        sent = sum(s.count for s in senders)
        time.sleep(2.0)
        found = non_reset_bits(sticky(adm))
        print(f"  {sent} frames sent, link {link_state(channel)}")
        print(f"  sticky now: {found or 'clean'}")
        if found:
            print(f"\nFOUND: this raises a sticky bit on {sorted(found)}. "
                  "Run `arm` next.")
            return 0
    print("\nNone of these raises a sticky bit. Without a marker that is not "
          "hasReset, the survival question cannot be answered over CAN alone -- "
          "the remaining option is a physical sensor fault: unplug a motor data "
          "cable, confirm the sticky sensor bit, plug it back in so the active "
          "fault clears, then run `arm`.")
    return 2


def cmd_arm(adm, roles):
    reading = sticky(adm)
    marker = non_reset_bits(reading)
    if not marker:
        print("no sticky bit other than hasReset is set, so there is nothing to "
              "track across the cycle. Run `find` first, or set one physically.")
        return 1
    active = {d: v["status1"]["faults"] for d, v in
              sa.collect_status(adm.bus, seconds=2.5).items()
              if v["status1"] and v["status1"]["faults"]}
    STATE.write_text(json.dumps({"marker": {str(k): v for k, v in marker.items()},
                                 "active_at_arm": {str(k): v for k, v in
                                                   active.items()}}, indent=2))
    print(f"ARMED. sticky marker: {marker}")
    print(f"active faults right now: {active or 'none'}")
    if active:
        print("  NOTE an active fault is still asserted, so a bit reappearing "
              "after the cycle could be freshly set rather than survived. "
              "Remove the cause first if you can.")
    print("\nNow cut and restore the motor rail, then run:\n"
          "  uv run python tools/spark_sticky_survival.py verify")
    return 0


def cmd_verify(adm, roles):
    if not STATE.exists():
        print(f"no state at {STATE}; run `arm` first")
        return 1
    state = json.loads(STATE.read_text())
    marker = {int(k): tuple(map(tuple, v)) for k, v in state["marker"].items()}

    woke = wake(adm, roles)
    print(f"woke the bus with GET_FIRMWARE to id {woke}" if woke else
          "no controller answered GET_FIRMWARE; is the rail back on?")
    time.sleep(1.5)
    after = sticky(adm)
    cycled = [d for d, (_, w) in after.items() if "hasReset" in w]
    survived = {d: v for d, v in non_reset_bits(after).items() if d in marker}

    print(f"\ncontrollers showing hasReset (so they really rebooted): {cycled}")
    print(f"marker before the cycle : {marker}")
    print(f"sticky after the cycle  : {non_reset_bits(after) or 'clean'}")
    if not cycled:
        print("\nNo controller shows hasReset, so the rail was probably not cut. "
              "Inconclusive.")
        return 1
    if survived:
        print(f"\nVERDICT: sticky state SURVIVED the power cycle on "
              f"{sorted(survived)}. can_bus.py's claim holds for SparkFlex, "
              "fault state has "
              "non-volatile backing, and clearing it is the only way to reset it.")
    else:
        print("\nVERDICT: sticky state did NOT survive the power cycle. The bits "
              "set before the cut are gone and only hasReset remains, which this "
              "cycle set itself. can_bus.py's justification for clearing at "
              "every boot does not hold for SparkFlex on 26.1.6 -- the boot-time "
              "clear is destroying the brownout and reset record for a reason "
              "that is not true.")
    STATE.unlink(missing_ok=True)
    return 0


def main(argv):
    if len(argv) != 2 or argv[1] not in ("find", "arm", "verify"):
        print(__doc__)
        return 2
    channel = _spark_config().can.interface
    roles = spark_cli._spark_roles()
    with sa.SparkAdmin(channel) as adm:
        if argv[1] == "find":
            return cmd_find(channel, adm, roles)
        if argv[1] == "arm":
            return cmd_arm(adm, roles)
        return cmd_verify(adm, roles)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
