"""Reassign a SPARK's CAN ID by its hardware serial, not by its current ID.

This is what makes duplicate CAN IDs fixable without isolating controllers.
Per REV-Specs spark-frames-2.1.0:

  SET_CAN_ID  api 0x095  arbId 0x02052540  5 B
    UNIQUE_ID  uint32  bits 0-31     the value broadcast on api 0x2F0
    CAN_ID     uint8   bits 32-39
  "Allows changing the CAN ID when multiple devices on the bus currently have
   the same CAN ID."

The frame carries no target device id -- every SPARK receives it and only the
one whose serial matches acts. Sends no setpoints and starts no heartbeat.

    uv run python tools/spark_set_can_id.py --serial 498B2579 --to 40
"""
import argparse
import sys
import time

import can

CH = "can0"
SET_CAN_ID = 0x02052540
IDENTIFY_UNIQUE = 0x02051D80


def inventory(bus, seconds=4.0):
    """{device_id: serial_hex} for every REV controller broadcasting."""
    serial, seen = {}, {}
    end = time.time() + seconds
    while time.time() < end:
        m = bus.recv(timeout=max(0.0, end - time.time()))
        if m is None:
            continue
        a = m.arbitration_id
        if (a >> 16) & 0xFF != 5:
            continue
        dev, api = a & 0x3F, (a >> 6) & 0x3FF
        seen.setdefault(dev, 0)
        seen[dev] += 1
        if api == 0x2F0:
            serial[dev] = bytes(m.data).hex().upper()
    return {d: serial.get(d, "?") for d in sorted(seen)}


def set_can_id(bus, serial_hex, new_id):
    payload = bytes.fromhex(serial_hex) + bytes([new_id])
    bus.send(can.Message(arbitration_id=SET_CAN_ID, data=payload,
                         is_extended_id=True))


def identify(bus, serial_hex):
    bus.send(can.Message(arbitration_id=IDENTIFY_UNIQUE,
                         data=bytes.fromhex(serial_hex), is_extended_id=True))


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--serial", required=True, help="8 hex chars from api 0x2F0")
    p.add_argument("--to", type=int, required=True, help="new CAN id 1-62")
    p.add_argument("--identify", action="store_true",
                   help="also blink that controller's LED")
    p.add_argument("--settle", type=float, default=4.0)
    args = p.parse_args(argv)

    if not 1 <= args.to <= 62:
        p.error("CAN id must be 1..62")

    bus = can.Bus(interface="socketcan", channel=CH)
    try:
        before = inventory(bus, args.settle)
        print("before:", {d: s for d, s in before.items()})
        at = [d for d, s in before.items() if s == args.serial.upper()]
        print(f"serial {args.serial.upper()} currently at id(s): {at or 'not seen'}")

        if args.identify:
            identify(bus, args.serial)
            print("sent IDENTIFY_UNIQUE_SPARK -- that controller's LED should blink")

        set_can_id(bus, args.serial, args.to)
        print(f"sent SET_CAN_ID serial={args.serial.upper()} -> id {args.to}")
        time.sleep(1.0)

        after = inventory(bus, args.settle)
        print("after: ", {d: s for d, s in after.items()})
        now = [d for d, s in after.items() if s == args.serial.upper()]
        if now == [args.to]:
            print(f"OK -- serial {args.serial.upper()} now answers on id {args.to}")
            return 0
        print(f"serial now at id(s): {now or 'not seen'} (expected [{args.to}])")
        return 1
    finally:
        bus.shutdown()


if __name__ == "__main__":
    sys.exit(main())
