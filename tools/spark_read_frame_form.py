#!/usr/bin/env python3
"""Which FORM of a parameter-read request does this firmware answer?

WHY THIS EXISTS

  For eight days this tree recorded "SPARK Flex firmware 26.1.6 answers no
  parameter read" as a property of the firmware. It was a property of the frame
  this package built. REV mark every Read Parameter and Get Parameter Types frame
  `rtr: true`, and the driver was sending zero-length DATA frames.

  Measured on rig-flex, one controller, one parameter, three forms:

      zero-length DATA frame, dlc 0      SILENT
      remote frame, dlc 0                SILENT
      remote frame, dlc 8                ANSWERS

  So a remote frame is necessary and not sufficient. The dlc has to request the
  eight bytes the reply carries. Nothing in the spec says that.

  A null result covers only what was varied. The run that concluded
  "no read answers on any api" varied the API and never the form, which is why it
  reached the wrong answer from correct observations. This tool varies the form.

WHAT IS STILL OPEN, AND WHAT THIS SETTLES IT WITH

  `both.param_read_frames` records that READ_PARAMETER and GET_PARAMETER_TYPES
  are firmware-25+ frames and that a pre-25 SPARK MAX carries none of them. The
  evidence is rig-max: four sends, no answer. TWO of those four were
  remote frames carrying dlc 0, which is now a known-silent form on a Flex. So
  the pre-25 negative is weaker than it reads.

  Run this on rig-max to close it:

      uv run python tools/spark_read_frame_form.py --id 3

  If every form stays silent there, "25+ only" is measured rather than assumed.
  If dlc 8 answers, a pre-25 MAX carries the documented read frames after all and
  spark_admin's generation split needs revisiting.

WHY THIS CANNOT WRITE

  Every frame is a READ_PARAMETER or GET_PARAMETER_TYPES request. Those apis carry
  no write, the remote frames carry no payload at all, and the one data frame sent
  is zero-length on a read api. The arbitration bounds are asserted before any
  index is computed, because one index past the last read frame is
  WRITE_PARAMETER_0_AND_1 -- a write of the device's own CAN id.

    uv run python tools/spark_read_frame_form.py --id 17
    uv run python tools/spark_read_frame_form.py --id 3 --param 50
"""
from __future__ import annotations

import argparse
import sys
import time

import can

from sparklib import admin as sa

FORMS = (
    ("zero-length DATA, dlc 0", dict(is_remote_frame=False, data=b"")),
    ("REMOTE, dlc 0", dict(is_remote_frame=True, dlc=0)),
    ("REMOTE, dlc 8", dict(is_remote_frame=True, dlc=sa.READ_FRAME_DLC)),
)


def read_arb(param_id):
    """Arbitration base for the READ_PARAMETER frame carrying `param_id`."""
    if not 0 <= param_id <= sa.PARAM_ID_MAX:
        raise ValueError(
            f"parameter {param_id} is outside 0-{sa.PARAM_ID_MAX}. One index "
            "past the last read frame is WRITE_PARAMETER_0_AND_1, which writes "
            "the device's own CAN id.")
    return sa.READ_PARAM_BASE + ((param_id // 2) << 6)


def ask(bus, arb, form, wait):
    end = time.time() + 0.1
    while time.time() < end:
        bus.recv(timeout=max(0.0, end - time.time()))
    bus.send(can.Message(arbitration_id=arb, is_extended_id=True, **form))
    end = time.time() + wait
    while time.time() < end:
        m = bus.recv(timeout=max(0.0, end - time.time()))
        if m is not None and m.arbitration_id == arb and not m.is_remote_frame:
            return bytes(m.data)
    return None


def try_forms(bus, arb, label, wait):
    """Returns the names of the forms that were answered."""
    answered = []
    print(f"\n{label}   arb 0x{arb:08X}")
    for name, form in FORMS:
        d = ask(bus, arb, form, wait)
        if d is None:
            print(f"  {name:<26} SILENT")
            continue
        answered.append(name)
        lo = int.from_bytes(d[0:4], "little") if len(d) >= 4 else None
        hi = int.from_bytes(d[4:8], "little") if len(d) >= 8 else None
        print(f"  {name:<26} {d.hex(' ')}   -> {lo}, {hi}")
    return answered


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("-c", "--channel", default=None,
                   help="CAN netdev; defaults to the configured SPARK bus")
    p.add_argument("--id", type=int, required=True, help="target device id")
    p.add_argument("--param", type=int, default=128,
                   help="parameter to read; its PAIR is what the frame carries")
    p.add_argument("--wait", type=float, default=0.6)
    a = p.parse_args(argv)

    channel = a.channel
    if channel is None:
        from sparklib.config import get as _spark_config
        channel = _spark_config().can.interface

    bus = can.Bus(interface="socketcan", channel=channel)
    try:
        print(f"device id {a.id} on {channel}")
        print("sending requests only; no write api is addressed")
        pair = (a.param - a.param % 2, a.param - a.param % 2 + 1)
        answered = try_forms(bus, read_arb(a.param) | a.id,
                             f"READ_PARAMETER, parameters {pair[0]} and {pair[1]}",
                             a.wait)
        answered += try_forms(bus, sa.GET_PARAM_TYPES | a.id,
                              "GET_PARAMETER_TYPES, parameters 0 to 15", a.wait)
    finally:
        bus.shutdown()

    print()
    if not answered:
        print("  NO form was answered. On this firmware these frames are absent, "
              "and that is now measured across all three forms rather than one.")
        return 1
    print("  answered by: " + ", ".join(sorted(set(answered))))
    if any("dlc 8" in n for n in answered) and not any("dlc 0" in n for n in answered):
        print("  the dlc is load-bearing: a remote frame alone draws nothing.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
