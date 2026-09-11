"""Phase 0b probe: does a REV SPARK answer a parameter/firmware read over CAN?

Sends ONE zero-length frame to ONE device id and reports any frame that comes
back on an arbitration id we were not already seeing. Zero-length frames are
what the repo already uses for clear_faults, so this adds no new class of TX.

The heartbeat is deliberately NOT started, so the controllers stay disabled and
cannot actuate.

    uv run python tools/spark_param_probe.py --id 17 --api 0x098
"""

import argparse
import time

import can

BASE = 0x02050000


def arb(api, dev_id):
    return BASE | (api << 6) | dev_id


def baseline_ids(bus, seconds):
    """Arbitration ids already on the wire, so a reply is distinguishable."""
    seen = set()
    deadline = time.time() + seconds
    while time.time() < deadline:
        m = bus.recv(timeout=max(0.0, deadline - time.time()))
        if m is not None:
            seen.add(m.arbitration_id)
    return seen


def probe(bus, api, dev_id, known, data=b"", wait=0.4):
    req = arb(api, dev_id)
    bus.send(can.Message(arbitration_id=req, data=data, is_extended_id=True))
    replies = []
    deadline = time.time() + wait
    while time.time() < deadline:
        m = bus.recv(timeout=max(0.0, deadline - time.time()))
        if m is None:
            continue
        if m.arbitration_id in known:
            continue
        replies.append(m)
        known.add(m.arbitration_id)
    return req, replies


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("-c", "--channel", default="can0")
    p.add_argument("--id", type=int, required=True, help="target device id")
    p.add_argument("--api", required=True, help="api, e.g. 0x098 or 0x300")
    p.add_argument("--data", default="", help="payload hex; empty = zero-length read")
    p.add_argument("--baseline", type=float, default=3.0)
    args = p.parse_args(argv)

    api = int(args.api, 0)
    payload = bytes.fromhex(args.data) if args.data else b""

    bus = can.Bus(interface="socketcan", channel=args.channel)
    try:
        print(f"learning existing traffic for {args.baseline}s ...")
        known = baseline_ids(bus, args.baseline)
        print(f"  {len(known)} arbitration ids already broadcasting")

        req, replies = probe(bus, api, args.id, known, payload)
        print(f"\nsent  arb=0x{req:08X}  api=0x{api:03X}  dev={args.id}  "
              f"dlc={len(payload)}  data={payload.hex().upper() or '(none)'}")
        if not replies:
            print("NO REPLY -- this api/format is not a read on this firmware.")
            return 1
        for m in replies:
            a = m.arbitration_id
            print(f"REPLY arb=0x{a:08X}  api=0x{(a >> 6) & 0x3FF:03X}  "
                  f"dev={a & 0x3F}  data={bytes(m.data).hex().upper()}")
        return 0
    finally:
        bus.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
