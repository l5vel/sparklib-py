"""Record the wheel-zero offset for every steer corner, one at a time.

    uv run python tools/cancoder_calibrate.py                 # all corners
    uv run python tools/cancoder_calibrate.py --corner LF     # just one

Read-only: it samples the CANcoders and prints a YAML block to paste into your
config. Nothing is written to a device and no motor is commanded.

Do the physical alignment FIRST. An offset measured against a wheel that is not
pointing at your chosen zero bakes that error in permanently, and every later
symptom looks electrical. docs/SWERVE-CALIBRATION.md step 2 covers it.

Needs the swerve extra: `uv sync --extra swerve`, then either export
UV_NO_SYNC=1 or run it as `uv run --extra swerve ...`, because a bare
`uv run` re-syncs to the default extras and removes phoenix6 again.
"""

import argparse
import sys

from sparklib import cancoder
from sparklib import config as cfg


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--corner", help="one label from devices.cancoder, e.g. LF")
    p.add_argument("--bus", help="override cancoder.bus")
    p.add_argument("--settle", type=float, default=0.5,
                   help="seconds a reading must hold still (default 0.5)")
    args = p.parse_args(argv)

    if not cancoder.available():
        print(cancoder.EXTRA_HINT)
        return 2

    cfg.load_host()
    conf = cfg.get()
    group = getattr(getattr(conf, "devices", None), "cancoder", None)
    if group is None:
        print("REFUSED: this config has no `devices.cancoder` group, so there "
              "are no absolute encoders to read.\n"
              "  FIX: add the group and a `cancoder:` block to your config. "
              "sparklib/data/spark.yaml carries a worked example of both.")
        return 2

    devices = vars(group)
    bus = args.bus or getattr(getattr(conf, "cancoder", None), "bus", None)
    if not bus:
        print("REFUSED: no CANcoder bus named. Set `cancoder.bus` in the config "
              "or pass --bus.")
        return 2

    if args.corner:
        if args.corner not in devices:
            print(f"REFUSED: {args.corner!r} is not in devices.cancoder "
                  f"({', '.join(sorted(devices))}).")
            return 2
        devices = {args.corner: devices[args.corner]}

    print(f"Point {'this wheel' if args.corner else 'every wheel'} at your "
          f"chosen zero before continuing.")
    if input("Aligned? [y/N] ").strip().lower() != "y":
        return 1

    encoders = cancoder.open_module_encoders(devices, bus)
    signals = [(label, e.get_absolute_position()) for label, e in encoders.items()]
    print(f"\nsampling {len(signals)} encoder(s) on {bus}:")
    try:
        offsets = cancoder.warmup(signals, settle_window_s=args.settle)
    except TimeoutError as err:
        print(f"\nREFUSED: {err}")
        return 1

    print("\npaste into your config, under cancoder:\n")
    print("  offsets_deg: {"
          + ", ".join(f"{k}: {offsets[k]:.2f}" for k in sorted(offsets))
          + "}")
    print("\nEach number is what that encoder reads with its wheel at zero. "
          "Re-measure after any steer rebuild.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
