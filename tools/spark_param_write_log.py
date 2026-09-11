#!/usr/bin/env python3
"""Log what CHANGES on the SPARK bus: parameter writes, and interlock transitions.

Two things on rig-flex come and go without an explanation, and both need somebody
watching at the moment they happen rather than an hour later.

On rig-flex the limit-switch polarities on ids 13, 15 and 16 keep arriving at
True. True is not REV's factory default -- False is -- so this is not a reset
dropping a provisioned value. Something is writing it, the write does not come
from this repo (nothing here writes params 50-53, and five tests enforce that),
and it survives being corrected and persisted.

And the HARD_LIMIT_REACHED bits on ids 13, 15 and 16 set and clear on their own.
Measured: latched while the drive stack was running and the
controllers refused every setpoint; cleared after a parameter write; back again
with no power cycle, since nothing showed hasReset; then gone again once the
stack came up. No single story fits all of that, so this records each transition
with the timestamp the kernel put on the frame.

Both are passive: one receive socket, nothing transmitted, safe to leave running
for days.

    uv run python tools/spark_param_write_log.py
    uv run python tools/spark_param_write_log.py --log /var/tmp/spark_writes.log

Every PARAMETER_WRITE, PERSIST_PARAMETERS and SET_CAN_ID frame is appended with
a timestamp, and a write to one of the four data-port interlock parameters is
marked. Every change in a controller's hard-limit bits is appended too, so the
log answers "when did 13 latch, and what else was on the bus in that second".

Timestamps come from the kernel on each frame, so a line here is when the frame
was on the wire rather than when this process got round to it.
"""
from __future__ import annotations

import argparse
import datetime
import sys
import time

import can

from sparklib.config import get as _spark_config
from sparklib import admin as sa

GUARDED = dict(sa.PROTECTED_PARAMS)
WATCHED = {
    sa.PARAM_WRITE: "PARAMETER_WRITE",
    sa.PERSIST: "PERSIST_PARAMETERS",
    sa.SET_CAN_ID: "SET_CAN_ID",
}


def stamp(ts):
    return datetime.datetime.fromtimestamp(ts).isoformat(timespec="milliseconds")


def describe(base, dev, data):
    if base == sa.PARAM_WRITE and len(data) >= 5:
        param = data[0]
        value = int.from_bytes(data[1:5], "little")
        name = GUARDED.get(param)
        tag = f"  <<< GUARDED: {name}" if name else ""
        return f"id {dev:>2} PARAMETER_WRITE param {param} = {value}{tag}", bool(name)
    if base == sa.PERSIST:
        return f"id {dev:>2} PERSIST_PARAMETERS", False
    if base == sa.SET_CAN_ID and len(data) >= 5:
        return (f"id {dev:>2} SET_CAN_ID serial {data[:4].hex().upper()} "
                f"-> {data[4]}", True)
    return f"id {dev:>2} {WATCHED.get(base, hex(base))} {data.hex()}", False


def limit_state(data):
    """(fwd, rev) hard-limit bits from a STATUS_0 payload, or None."""
    decoded = sa.decode_status_0(data)
    if decoded is None:
        return None
    return decoded["hard_forward_limit"], decoded["hard_reverse_limit"]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--log", default="/var/tmp/spark_param_writes.log")
    ap.add_argument("--channel", default=None)
    args = ap.parse_args(argv)
    channel = args.channel or _spark_config().can.interface

    bus = can.Bus(interface="socketcan", channel=channel)
    print(f"listening on {channel}, appending to {args.log} -- Ctrl-C to stop")
    print(f"guarded parameters: {sorted(GUARDED)} "
          f"({', '.join(GUARDED[p] for p in sorted(GUARDED))})\n")
    seen = 0
    try:
        with open(args.log, "a", buffering=1) as fh:
            fh.write(f"# {stamp(time.time())} started on {channel}\n")
            limits = {}
            while True:
                m = bus.recv(timeout=1.0)
                if m is None:
                    continue
                arb, data = m.arbitration_id, bytes(m.data)
                base, dev = arb & ~0x3F, arb & 0x3F

                if ((arb >> 16) & 0xFF == sa.REV_MFR
                        and (arb >> 6) & 0x3FF == sa.STATUS_0_API):
                    now = limit_state(data)
                    if now is not None and limits.get(dev) != now:
                        was = limits.get(dev)
                        limits[dev] = now
                        if was is not None:
                            def tag(state):
                                hit = [n for n, on in zip(("FWD", "REV"), state) if on]
                                return ",".join(hit) or "clear"
                            record = (f"{stamp(m.timestamp)}  id {dev:>2} "
                                      f"LIMIT {tag(was)} -> {tag(now)}")
                            seen += 1
                            fh.write(record + "\n")
                            print(f"\n*** {record}\n")
                    continue

                if base not in WATCHED:
                    continue
                line, loud = describe(base, dev, data)
                seen += 1
                record = f"{stamp(m.timestamp)}  {line}"
                fh.write(record + "\n")
                print(f"\n*** {record}\n" if loud else record)
    except KeyboardInterrupt:
        print(f"\nstopped after {seen} event(s); log at {args.log}")
    finally:
        bus.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
