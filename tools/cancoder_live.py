"""Live-print the angle every CANcoder is reporting, while you turn a wheel.

    uv run python tools/cancoder_live.py
    uv run python tools/cancoder_live.py --corner RF --raw

The readout to have open during physical alignment. Every other CANcoder tool
takes one sample and prints a verdict, which answers a different question: this
one shows the number moving, so you can see a wheel reach the zero you are
aiming for and see whether the angle tracks the wheel smoothly.

Each corner prints its wheel angle, the raw reading in brackets, and how far it
has moved since the run started. --raw swaps the first two for the reading in
rotations, which is the unit the device publishes.

Read-only. It opens no SPARK bus, sends no configuration and writes no flash,
so it is safe to leave running beside anything else that is reading.

Needs the swerve extra: `uv sync --extra swerve`, then either export
UV_NO_SYNC=1 or run it as `uv run --extra swerve ...`, because a bare
`uv run` re-syncs to the default extras and removes phoenix6 again.
"""

import argparse
import sys
import time

from sparklib import cancoder
from sparklib import steer

REFUSED = 2
INTERRUPTED = 130


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--corner", help="one label from devices.cancoder, e.g. LF")
    p.add_argument("--bus", help="override cancoder.bus")
    p.add_argument("--raw", action="store_true",
                   help="also print the reading in rotations")
    p.add_argument("--hz", type=float, default=10.0, help="print rate")
    p.add_argument("--seconds", type=float, default=0.0,
                   help="stop after this long; 0 runs until Ctrl-C")
    args = p.parse_args(argv)

    if args.hz <= 0:
        print(f"REFUSED: --hz {args.hz:g} sets no print rate.\n"
              "  FIX: pass a positive rate, for example --hz 10.")
        return REFUSED
    if args.seconds < 0:
        print(f"REFUSED: --seconds {args.seconds:g} is negative.\n"
              "  FIX: pass 0 to run until Ctrl-C, or a positive duration.")
        return REFUSED

    if not cancoder.available():
        print(cancoder.EXTRA_HINT)
        return REFUSED

    try:
        devices, bus = cancoder.modules(corner=args.corner, bus=args.bus)
    except cancoder.Refused as err:
        print(f"REFUSED: {err}")
        return REFUSED

    offsets = cancoder.offsets()
    encoders = cancoder.open_module_encoders(devices, bus)

    absent = [label for label, enc in encoders.items()
              if not cancoder.present(enc)]
    if absent:
        print(f"REFUSED: no reply from {', '.join(absent)} on {bus}.\n"
              "  FIX: check the CAN id against devices.cancoder and check the "
              "wiring. tools/can_id_sweep.py lists what is answering.")
        return REFUSED

    labels = list(encoders)
    offsetless = [l for l in labels if l not in offsets]
    if offsetless:
        print(f"no recorded offset for {', '.join(offsetless)}, so the wheel "
              "column reads '-' there. tools/cancoder_calibrate.py records one.")
    print(f"{len(labels)} encoder(s) on {bus}. Turn a wheel by hand. "
          "Ctrl-C to stop.")

    start = {}
    period = 1.0 / args.hz
    deadline = time.time() + args.seconds if args.seconds else None

    try:
        while deadline is None or time.time() < deadline:
            cells = []
            for label, enc in encoders.items():
                raw = cancoder.read_angle_deg(enc, settle=False)
                start.setdefault(label, raw)
                moved = steer.normalize_deg(raw - start[label])
                if args.raw:
                    cells.append(f"{label} {raw / 360.0:+8.4f}rot {moved:+7.2f}")
                elif label in offsets:
                    wheel = cancoder.wheel_angle_deg(raw, offsets[label])
                    cells.append(f"{label} {wheel:+7.2f} ({raw:+7.2f}) {moved:+7.2f}")
                else:
                    cells.append(f"{label}       - ({raw:+7.2f}) {moved:+7.2f}")
            print("\r  " + "  ".join(cells), end="", flush=True)
            time.sleep(period)
    except KeyboardInterrupt:
        print()
        return INTERRUPTED

    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
