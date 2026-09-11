#!/usr/bin/env python3
"""Blink a SPARK's LED, in the form the firmware actually wants.

WHY THIS SCRIPT EXISTS

`spark identify` never blinked anything on rig-max, and the reason was addressing
rather than capability. There are two different models on the same api:

    firmware 25+   0x02051D80, device id 0 (BROADCAST), 4-byte serial payload
    pre-25         0x02051D80 | dev  (ADDRESSED BY CAN ID), payload EMPTY

`SparkAdmin.identify` implements the first. On 24.0.1 that frame is ignored, so
the command reported success and nothing happened.

The pre-25 form was recovered by capturing REV Hardware Client
while an operator pressed its LED button for controllers 1, 2 and 3. Three frames
appeared in 9831 lines of candump, one per controller, and nothing else:

    02051D81  [0]
    02051D82  [0]
    02051D83  [0]

api 0x076 is apiClass 7 index 6, Identify Unique SPARK, versionImplemented 1.5.0
in REV-Specs spark-frames-2.1.0 -- so the frame has existed since long before
this firmware. What changed at 25.0.0 is how you address it.

Note the consequence for identity: pre-25 identify takes NO serial. It cannot be
used to confirm that the per-device value at api 0x094 is a serial, because it
never asks for one.

    uv run python tools/spark_blink.py --id 3
    uv run python tools/spark_blink.py --id 1 --id 2 --repeat 5
    uv run python tools/spark_blink.py --all --gap 3

Sends nothing but this one frame. No setpoint, no heartbeat, no parameter write:
the controllers stay disabled and cannot actuate.
"""
from __future__ import annotations

import argparse
import sys
import time

import can

from sparklib.config import get as _spark_config

# apiClass 7 index 6. The pre-25 form ORs the device id into the arbitration id
# and carries no payload; the 25+ form broadcasts on device 0 with a serial.
IDENTIFY_UNIQUE = 0x02051D80


def spark_ids():
    """Configured SPARK ids only. CANcoders are on the other bus and reuse these
    numbers, so flattening every group would address the wrong devices."""
    out = set()
    for group, corners in vars(_spark_config().devices).items():
        if group in ("drive", "steer"):
            out.update(int(v) for v in vars(corners).values())
    return sorted(out)


def roles():
    out = {}
    for group, corners in vars(_spark_config().devices).items():
        if group in ("drive", "steer"):
            for corner, dev in vars(corners).items():
                out[int(dev)] = f"{group}/{corner}"
    return out


def main(argv=None):
    p = argparse.ArgumentParser(
        prog="spark_blink", description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    p.add_argument("--id", type=int, action="append", dest="ids", default=None,
                   help="controller to blink; repeat for several")
    p.add_argument("--all", action="store_true", help="every configured SPARK id")
    p.add_argument("--repeat", type=int, default=3,
                   help="frames per controller (default 3); one is enough on "
                        "known-good firmware, more makes a short pulse easier to see")
    p.add_argument("--interval", type=float, default=1.5,
                   help="seconds between repeats (default 1.5)")
    p.add_argument("--gap", type=float, default=4.0,
                   help="seconds between controllers, so two are distinguishable "
                        "(default 4)")
    p.add_argument("--channel", default=None, help="override base.can_interface")
    args = p.parse_args(argv)

    channel = args.channel or _spark_config().can.interface
    role = roles()

    if args.all:
        targets = spark_ids()
    elif args.ids:
        targets = args.ids
    else:
        p.error("give --id N (repeatable) or --all")

    unknown = [d for d in targets if d not in role]
    if unknown:
        print(f"note: {unknown} are not in devices for this robot. Sending "
              "anyway, since an unconfigured controller is exactly the thing you "
              "may be trying to find.")

    print(f"bus {channel} -- blinking {len(targets)} controller(s), "
          f"{args.repeat} frame(s) each\n")
    bus = can.Bus(interface="socketcan", channel=channel)
    try:
        for n, dev in enumerate(targets):
            arb = IDENTIFY_UNIQUE | dev
            print(f"  id {dev} ({role.get(dev, 'not in config')})  "
                  f"0x{arb:08X}  DLC 0", flush=True)
            for _ in range(args.repeat):
                bus.send(can.Message(arbitration_id=arb, data=b"",
                                     is_extended_id=True))
                time.sleep(args.interval)
            if n < len(targets) - 1:
                time.sleep(args.gap)
    finally:
        bus.shutdown()

    print("\nIf nothing blinked, the bus is reachable but this form is not the "
          "one your firmware wants:")
    print("  * check the controllers answer at all -- `uv run spark status`")
    print("  * on firmware 25+ this form is wrong by design; use "
          "`uv run spark identify --serial X` there")
    return 0


if __name__ == "__main__":
    sys.exit(main())
