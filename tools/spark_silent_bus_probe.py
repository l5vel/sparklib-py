#!/usr/bin/env python3
"""What ends the post-cycle silence on a SPARK bus, and can the evidence survive it?

After a motor-rail power cycle rig-flex sometimes comes back with every controller
powered, on the bus and answering requests, while broadcasting no periodic frame
at all. Sticky hasReset is the record of the reboot, it lives in STATUS_1, and
STATUS_1 is one of the frames that is not being sent. The only recovery anyone
has confirmed is a Clear Faults frame per id, and that erases the bit.

So the question this answers is worth a robot's time: does anything less
destructive than Clear Faults bring the transmitters back?

    uv run python tools/spark_silent_bus_probe.py

It is interactive because the decisive part needs the bus untouched. You cut and
restore the rail and tell it when you are done; it sends absolutely nothing
during the observation window, so a bus that recovers on its own is
distinguishable from one that was woken. Then it escalates one controller at a
time, keeping the other seven as the control, so "the query woke it" and "the
bus came back anyway" cannot be confused.

Escalation, least destructive first. Everything before the last step leaves the
sticky bytes intact:

    1  nothing at all, for OBSERVE_S
    2  one frame carrying a manufacturer no device here decodes
    3  GET_FIRMWARE to one controller
    4  IDENTIFY to one controller, addressed by serial
    5  PARAMETER_WRITE of the declared Status 1 Period, the value it already has
    6  CLEAR_FAULTS to one controller, which is the known recovery and the
       thing that destroys what we came for

Run: sixty seconds of silence changed nothing, and one
GET_FIRMWARE to id 17 brought all eight back with sticky hasReset intact. Step 2
was added afterwards to find out whether any frame would have done it.

Findings go in docs/spark/runs/-rig-flex-probe-log.md sections 7 and 9.
"""
from __future__ import annotations

import sys
import threading
import time

import can

from sparklib.config import get as _spark_config
from sparklib import admin as sa
from sparklib import cli as spark_cli

OBSERVE_S = 60.0        # silence watched with nothing sent to it
SETTLE_S = 5.0          # watched after each escalation step
STATUS_APIS = (sa.STATUS_0_API, sa.STATUS_1_API, 0x2F0)


class Listener:
    """Records which device last broadcast, without transmitting anything."""

    def __init__(self, channel):
        self.bus = can.Bus(interface="socketcan", channel=channel)
        self.last = {}
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        while not self._stop.is_set():
            m = self.bus.recv(timeout=0.2)
            if m is None or (m.arbitration_id >> 16) & 0xFF != sa.REV_MFR:
                continue
            api = (m.arbitration_id >> 6) & 0x3FF
            if api in STATUS_APIS:
                self.last[m.arbitration_id & 0x3F] = time.time()

    def start(self):
        self._thread.start()
        return self

    def stop(self):
        self._stop.set()
        self._thread.join(timeout=2.0)
        self.bus.shutdown()

    def broadcasting(self, since):
        return sorted(d for d, t in self.last.items() if t >= since)


def filler(channel):
    """One frame with a manufacturer no device here claims, so nothing decodes it.

    Placed before GET_FIRMWARE in the escalation to separate two explanations of
    the result, where a single firmware query to one controller
    brought all eight back: either any traffic at all restarts the transmitters,
    or it takes a frame a SPARK recognises. This one is not addressed to anybody.
    """
    bus = can.Bus(interface="socketcan", channel=channel)
    try:
        bus.send(can.Message(arbitration_id=0x1FFFFFFF, data=bytes(8),
                             is_extended_id=True), timeout=0.5)
    finally:
        bus.shutdown()
    return "sent"


def ask(prompt):
    print(f"\n>>> {prompt}")
    try:
        input("    press Enter when done, or Ctrl-C to stop... ")
    except EOFError:
        sys.exit("no console; run this from a terminal")


def report(listener, since, label):
    live = listener.broadcasting(since)
    print(f"    {label}: broadcasting {live or 'nothing'}")
    return live


def sticky_of(adm, devs):
    """Sticky bytes of whichever controllers are talking. Reads only."""
    st = sa.collect_status(adm.bus, seconds=2.0)
    out = {}
    for d in sorted(devs):
        s1 = (st.get(d) or {}).get("status1")
        if s1:
            out[d] = (s1["sticky_faults"], s1["sticky_warnings"])
    return out


def main():
    channel = _spark_config().can.interface
    roles = spark_cli._spark_roles()
    serials = spark_cli._known_serials()
    subject = sorted(roles)[-1]          # one controller; the rest are the control
    declared = int(sa.declared_status_1_period_ms())
    print(__doc__.split("\n\n")[0])
    control = sorted(set(roles) - {subject})
    print(f"\nbus {channel}, controllers {sorted(roles)}, "
          f"subject {subject} ({roles[subject]}), control {control}")

    listener = Listener(channel).start()
    try:
        ask("Cut the motor rail, wait a few seconds, then restore it.")
        t0 = time.time()

        print(f"\n[1] observing {OBSERVE_S:.0f}s with nothing sent to the bus")
        deadline = t0 + OBSERVE_S
        first_seen = None
        while time.time() < deadline:
            live = listener.broadcasting(t0)
            if live and first_seen is None:
                first_seen = time.time() - t0
                print(f"    {first_seen:.1f}s: {live} started broadcasting "
                      "with nothing sent to them")
            time.sleep(0.5)
        live = report(listener, t0, f"after {OBSERVE_S:.0f}s untouched")
        if len(live) == len(roles):
            print("\nRESULT: the bus recovers on its own. The silence is "
                  "transient and no command is needed, so read `spark faults` "
                  "after waiting rather than after clearing.")
            return 0
        if live:
            print("\nNOTE: some came back and some did not; the escalation "
                  "below still applies to those that did not.")

        adm = sa.SparkAdmin(channel)
        steps = [
            ("one frame nothing on this bus decodes", lambda: filler(channel)),
            ("GET_FIRMWARE", lambda: adm.firmware(subject)),
            ("IDENTIFY", lambda: adm.identify(serials[subject])
                if serials.get(subject) else None),
            ("PARAMETER_WRITE of the declared period",
             lambda: adm.write_param(subject, sa.PARAM_STATUS_1_PERIOD, declared)),
            ("CLEAR_FAULTS", lambda: adm.clear_faults([subject])),
        ]
        try:
            for n, (label, action) in enumerate(steps, start=2):
                before = set(listener.broadcasting(t0))
                if label.startswith("CLEAR"):
                    print(f"\n[{n}] {label} to id {subject} -- this ERASES its "
                          "sticky bytes. Reading them first:")
                    readable = sticky_of(adm, before) or "nothing readable"
                    print(f"    sticky now: {readable}")
                    ask("Send Clear Faults to the subject?")
                else:
                    print(f"\n[{n}] {label} to id {subject} only")
                mark = time.time()
                result = action()
                print(f"    returned {result!r}")
                time.sleep(SETTLE_S)
                after = set(listener.broadcasting(mark))
                woke = sorted(after - before)
                print(f"    broadcasting since: {sorted(after) or 'nothing'}")
                if subject in woke and not (woke and set(woke) - {subject}):
                    print(f"\nRESULT: {label} woke id {subject} and nothing else. "
                          "The transmitter is restarted per device by a frame "
                          "addressed to it.")
                    if not label.startswith("CLEAR"):
                        print("This breaks the deadlock: wake each controller "
                              "this way, read STATUS_1 with the sticky bits "
                              "still set, and only then clear.")
                    print(f"    sticky after: {sticky_of(adm, after)}")
                    return 0
                if woke:
                    print(f"\nRESULT: {label} brought back {woke}, which is more "
                          "than the one addressed -- the effect is bus-wide.")
                    print(f"    sticky after: {sticky_of(adm, after)}")
                    return 0
            print("\nRESULT: nothing here restarted the transmitter, Clear "
                  "Faults included. That contradicts and is worth "
                  "capturing with candump before power-cycling again.")
        finally:
            adm.close()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        listener.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
