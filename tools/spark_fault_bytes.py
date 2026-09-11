"""Find which STATUS_0 bytes carry SparkFlex faults, empirically.

The repo decodes Flex faults at bytes 2:4 / 4:6 but marks the result untrusted
(_FAULT_DECODE_TRUSTED is False), and with zero applied output those bytes read
non-zero, so the mapping is wrong. This samples STATUS_0 per device, sends the
clear-faults frame the driver already sends at every boot, samples again, and
reports which byte positions changed -- those are the sticky-fault bytes.

Read-mostly: the only frame sent is clear-faults (api 0x6E).
"""
import argparse
import collections
import sys
import time

import can

CH = "can0"
S0_API = 0x2E0
CLEAR_BASE = 0x02051B80


def sample(bus, seconds):
    """Per device: the set of values seen at each STATUS_0 byte position."""
    seen = collections.defaultdict(lambda: [set() for _ in range(8)])
    end = time.time() + seconds
    while time.time() < end:
        m = bus.recv(timeout=max(0.0, end - time.time()))
        if m is None:
            continue
        a = m.arbitration_id
        if (a >> 6) & 0x3FF != S0_API or (a >> 16) & 0xFF != 5:
            continue
        data = bytes(m.data)
        for i, b in enumerate(data[:8]):
            seen[a & 0x3F][i].add(b)
    return seen


def render(label, seen):
    print(f"\n{label}")
    print(f"  {'dev':>3}  " + "  ".join(f"b{i}" for i in range(8)))
    for dev in sorted(seen):
        cells = []
        for i in range(8):
            vals = seen[dev][i]
            cells.append(f"{min(vals):02X}" if len(vals) == 1
                         else f"{min(vals):02X}~{max(vals):02X}")
        print(f"  {dev:>3}  " + "  ".join(f"{c:>5}" for c in cells))


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("-d", "--duration", type=float, default=4.0)
    p.add_argument("--no-clear", action="store_true",
                   help="sample only, do not send clear-faults")
    args = p.parse_args(argv)

    bus = can.Bus(interface="socketcan", channel=CH)
    try:
        before = sample(bus, args.duration)
        render("BEFORE clear-faults (byte value, or min~max if it varies):", before)
        if args.no_clear:
            return 0

        for dev in sorted(before):
            bus.send(can.Message(arbitration_id=CLEAR_BASE + dev, data=[],
                                 is_extended_id=True))
        print(f"\nsent clear-faults to {len(before)} devices")
        time.sleep(1.0)

        after = sample(bus, args.duration)
        render("AFTER clear-faults:", after)

        print("\nbyte positions that changed (these carry sticky faults):")
        changed = collections.Counter()
        for dev in sorted(before):
            if dev not in after:
                continue
            diffs = [i for i in range(8) if before[dev][i] != after[dev][i]]
            stable = [i for i in diffs if len(before[dev][i]) == 1 == len(after[dev][i])]
            if stable:
                changed.update(stable)
                print(f"  dev {dev:>3}: bytes {stable} "
                      + " ".join(f"b{i}:{min(before[dev][i]):02X}->{min(after[dev][i]):02X}"
                                 for i in stable))
        if not changed:
            print("  none -- no device had sticky faults latched, or faults are "
                  "not in STATUS_0 on this firmware")
        else:
            print(f"\n  consistently changing byte positions: "
                  f"{sorted(changed)} (seen on {max(changed.values())} devices)")
        return 0
    finally:
        bus.shutdown()


if __name__ == "__main__":
    sys.exit(main())
