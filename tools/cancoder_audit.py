"""Read-only health and configuration audit of every CANcoder.

    uv run python tools/cancoder_audit.py

Reports the fields that break a swerve module quietly: which direction each
sensor counts, where its own zero sits, and whether the magnet is close enough
to be believed. Sends no configuration and writes no flash.

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
    p.add_argument("--bus", help="override cancoder.bus")
    args = p.parse_args(argv)

    if not cancoder.available():
        print(cancoder.EXTRA_HINT)
        return 2

    cfg.load_host()
    conf = cfg.get()
    try:
        devices, bus = cancoder.modules(bus=args.bus, conf=conf)
    except cancoder.Refused as err:
        print(f"REFUSED: {err}")
        return 2

    encoders = cancoder.open_module_encoders(devices, bus)

    print(f"bus {bus} -- {len(encoders)} CANcoder(s)\n")

    # Ask whether each id is answering before reading anything off it. A silent
    # encoder returns 0.000 with a bad status code, and warmup settles on it
    # happily because a constant is the stillest reading there is. Every field
    # after that is a default, and the one field with no default -- magnet
    # health -- then reads as a magnet mounted wrong.
    silent = [label for label, e in encoders.items() if not cancoder.present(e)]
    if silent:
        print(f"REFUSED: no reply from {', '.join(sorted(silent))} on {bus}.")
        print("  Every reading from a silent encoder is a default, so this "
              "would report a wheel at 0.00 deg and blame the magnet.")
        print("  FIX: check the ids against devices.cancoder, and the power and "
              "wiring to those encoders. If the CANivore logged 'Firmware Too "
              "Old' or a Phoenix version mismatch, that is the cause: field "
              "upgrade the CRF with Phoenix Tuner X, or install the phoenix6 "
              "that matches the firmware these encoders run.")
        return 2

    signals = [(label, e.get_absolute_position()) for label, e in encoders.items()]
    try:
        angles = cancoder.warmup(signals, verbose=False)
    except TimeoutError as err:
        print(f"REFUSED: {err}")
        return 1

    offsets = vars(getattr(getattr(conf, "cancoder", None), "offsets_deg", None) or type("", (), {})())
    ratio = getattr(getattr(conf, "cancoder", None), "steer_gear_ratio", None)

    print(f"  {'corner':<8} {'id':>3}  {'raw':>8}  {'wheel':>8}  {'direction':<34} magnet")
    problems = []
    for label in sorted(encoders):
        d = cancoder.describe(encoders[label])
        raw = angles[label]
        off = offsets.get(label)
        wheel = cancoder.wheel_angle_deg(raw, off) if off is not None else float("nan")
        health = d.get("magnet_health", "-")
        print(f"  {label:<8} {devices[label]:>3}  {raw:>+8.2f}  {wheel:>+8.2f}  "
              f"{d['sensor_direction'][:34]:<34} {health}")
        if off is None:
            problems.append(f"{label} has no recorded offset, so its wheel angle "
                            "is unknown. Run tools/cancoder_calibrate.py.")
        reading = str(health).upper()
        if "INVALID" in reading or "UNKNOWN" in reading:
            problems.append(f"{label} reports magnet health {health}, which is "
                            "the encoder saying it has no reading rather than a "
                            "verdict on the magnet. Check that this id answers "
                            "and that the phoenix6 version matches its firmware "
                            "before touching the mounting.")
        elif "MAGNET" in reading and "GREEN" not in reading:
            problems.append(f"{label} reports magnet health {health}. Green is "
                            "the only reading to trust; the others mean the "
                            "magnet is too far, too close or off-centre.")

    directions = {cancoder.describe(e)["sensor_direction"] for e in encoders.values()}
    if len(directions) > 1:
        problems.append(f"the encoders disagree on sensor direction ({directions}), "
                        "so at least one corner counts backwards and its loop "
                        "will drive away from the target.")

    if ratio:
        print(f"\n  steer_gear_ratio {ratio}: a quarter turn of a wheel is "
              f"{cancoder.motor_rotations(90, ratio):.2f} motor rotations")

    if problems:
        print("\nproblems:")
        for x in problems:
            print(f"  - {x}")
        return 1
    print("\nno problems found")
    return 0


if __name__ == "__main__":
    sys.exit(main())
