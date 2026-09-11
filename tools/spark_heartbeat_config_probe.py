"""Phase 0b/0c retry: do config frames need an active enable heartbeat?

RHC and REVLib always send one; the earlier probes did not, and nothing
answered. This starts the normal SparkBus heartbeat while a thread holds every
controller at percent_output(0.0), then retries the parameter reads and the
set-period frame.

Every probe is bracketed by two controls, because a null result is worthless
without them:
  * a firmware read (api 0x098), known to answer with no heartbeat
  * the kernel's tx_packets counter, so a wedged gs_usb that accepts frames and
    never puts them on the wire is visible instead of looking like "no reply"

User-authorised live test. Outputs are pinned to zero throughout.
"""
import sys
import threading
import time

import can

from sparklib import controller as spark_controller
from sparklib.can_bus import SparkBus

CH = "can0"
DEV = 17
IDS = range(10, 18)
BASE = 0x02050000


def tx_packets():
    try:
        with open(f"/sys/class/net/{CH}/statistics/tx_packets") as fh:
            return int(fh.read().strip())
    except (OSError, ValueError):
        return None


def main():
    bus = SparkBus(channel=CH, bustype="socketcan", bitrate=1000000)
    ctrls = {i: bus.init_controller(i, spark_controller.SPARK_FLEX,
                                    clear_sticky_faults=True) for i in IDS}
    bus.wait_for_heartbeat(3.0)

    stop = threading.Event()

    def hold_zero():
        # 10 Hz, not 50: the earlier attempt pushed ~400 fps of TX and wedged
        # the gs_usb, which is what invalidated it.
        while not stop.is_set():
            for c in ctrls.values():
                c.percent_output(0.0)
            time.sleep(0.1)

    holder = threading.Thread(target=hold_zero, daemon=True)
    holder.start()
    time.sleep(2.0)

    raw = can.Bus(interface="socketcan", channel=CH)
    try:
        print("heartbeat live, all 8 outputs held at 0.0 (10 Hz)")

        known = set()
        end = time.time() + 2.5
        while time.time() < end:
            m = raw.recv(timeout=max(0.0, end - time.time()))
            if m is not None:
                known.add(m.arbitration_id)

        def probe(label, api, **kw):
            arb = BASE | (api << 6) | DEV
            before = tx_packets()
            raw.send(can.Message(arbitration_id=arb, is_extended_id=True, **kw))
            got = []
            end = time.time() + 0.3
            while time.time() < end:
                m = raw.recv(timeout=max(0.0, end - time.time()))
                if m is not None and m.arbitration_id not in known:
                    got.append(m)
                    known.add(m.arbitration_id)
            after = tx_packets()
            wire = "" if (after or 0) > (before or 0) else "  [TX DID NOT REACH WIRE]"
            detail = ", ".join(
                f"arb=0x{m.arbitration_id:08X} "
                f"api=0x{(m.arbitration_id >> 6) & 0x3FF:03X} "
                f"data={bytes(m.data).hex().upper()}" for m in got)
            print(f"  {label:<32} {detail or 'no reply'}{wire}")
            return bool(got)

        print("\ncontrol before:")
        ok_before = probe("firmware api=0x098 RTR", 0x098,
                          is_remote_frame=True, dlc=0)

        print("\nparameter reads:")
        for p in range(8):
            probe(f"param {p} api=0x{0x300 | p:03X} RTR", 0x300 | p,
                  is_remote_frame=True, dlc=0)

        print("\ncontrol after:")
        ok_after = probe("firmware api=0x098 RTR", 0x098,
                         is_remote_frame=True, dlc=0)

        print("\nset-period frame 2 -> 20 ms:")
        target = BASE | ((0x2E0 + 2) << 6) | DEV
        for name, sbase in (("SparkMax 0x060", 0x060), ("SparkFlex 0x2E0", 0x2E0)):
            arb = BASE | ((sbase + 2) << 6) | DEV
            raw.send(can.Message(arbitration_id=arb, data=b"\x14\x00",
                                 is_extended_id=True))
            time.sleep(0.5)
            n = 0
            end = time.time() + 2.0
            while time.time() < end:
                m = raw.recv(timeout=max(0.0, end - time.time()))
                if m is not None and m.arbitration_id == target:
                    n += 1
            print(f"  {name:<18} arb=0x{arb:08X} -> 0x2E2 frames in 2 s = {n}")

        print()
        if not (ok_before and ok_after):
            print("INVALID RUN -- the firmware control did not answer, so the "
                  "nulls above prove nothing about the heartbeat hypothesis.")
        else:
            print("VALID RUN -- controls answered, so the parameter/set-period "
                  "nulls are real: a heartbeat does not unlock config over CAN.")
    finally:
        stop.set()
        holder.join(timeout=1.0)
        raw.shutdown()
        bus.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
