"""Watch the SPARK bus and report when a device id appears or disappears.

Passive: opens the bus read-only and never transmits. Used to confirm a
motor-rail power cycle brought a silent controller back.

    uv run python tools/watch_for_device.py --id 17 --timeout 900
"""
import argparse
import sys
import time

import can


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("-c", "--channel", default="can0")
    p.add_argument("--id", type=int, default=17)
    p.add_argument("--timeout", type=float, default=900.0)
    args = p.parse_args(argv)

    bus = can.Bus(interface="socketcan", channel=args.channel)
    deadline = time.time() + args.timeout
    present, last_report = set(), 0.0
    print(f"watching {args.channel} for device {args.id} "
          f"(up to {args.timeout:.0f}s). Press+release the e-stop now.")
    try:
        while time.time() < deadline:
            m = bus.recv(timeout=1.0)
            now = time.time()
            if m is not None and (m.arbitration_id >> 16) & 0xFF == 5:
                dev = m.arbitration_id & 0x3F
                if dev not in present:
                    present.add(dev)
                    if dev == args.id:
                        print(f"\n*** device {args.id} IS BACK at {time.strftime('%H:%M:%S')} ***")
                        api = (m.arbitration_id >> 6) & 0x3FF
                        print(f"    first frame: api=0x{api:03X} "
                              f"data={bytes(m.data).hex().upper()}")
                        print(f"    devices now present: {sorted(present)}")
                        return 0
            # Re-arm the presence set periodically so a power cut is visible too.
            if now - last_report >= 20.0:
                print(f"  {time.strftime('%H:%M:%S')} present: {sorted(present)}"
                      + ("" if args.id in present else f"  (waiting for {args.id})"))
                present, last_report = set(), now
        print(f"\ntimed out after {args.timeout:.0f}s -- device {args.id} did not appear")
        return 1
    finally:
        bus.shutdown()


if __name__ == "__main__":
    sys.exit(main())
