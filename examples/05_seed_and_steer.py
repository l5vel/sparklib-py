"""Seed a steer axis from its absolute encoder, then hold an angle on the device.

    uv run python examples/05_seed_and_steer.py --interface can0 \
        --corner LF --angle 45

This is the whole swerve integration in one file. The SPARK's encoder is
relative, so at startup it knows how far the axis turned and not where the wheel
points. A CANcoder supplies that once, and after the seed the controller's own
position PID does the work.

Reads `devices`, `serials` and `cancoder` from your config, so the ids, the
offsets and the gear ratio all come from one place.

Needs the swerve extra: `uv sync --extra swerve`, then either export
UV_NO_SYNC=1 or run it as `uv run --extra swerve ...`, because a bare
`uv run` re-syncs to the default extras and removes phoenix6 again.

MOVES A MOTOR. Lift the wheel first.
"""

import argparse
import time

from sparklib import SparkBus, SPARK_FLEX, SPARK_MAX
from sparklib import cancoder
from sparklib import config as cfg


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--interface", help="SPARK bus netdev; defaults to can.interface")
    p.add_argument("--corner", required=True, help="a label from devices.steer, e.g. LF")
    p.add_argument("--angle", type=float, default=0.0, help="target wheel angle, degrees")
    p.add_argument("--seconds", type=float, default=4.0)
    args = p.parse_args(argv)

    if not cancoder.available():
        print(cancoder.EXTRA_HINT)
        return 2

    cfg.load_host()
    conf = cfg.get()
    steer_id = getattr(conf.devices.steer, args.corner, None)
    cc_id = getattr(getattr(conf.devices, "cancoder", None), args.corner, None)
    offset = getattr(getattr(conf.cancoder, "offsets_deg", None), args.corner, None)
    ratio = conf.cancoder.steer_gear_ratio
    if steer_id is None or cc_id is None or offset is None:
        print(f"REFUSED: corner {args.corner!r} needs an id under devices.steer, "
              "an id under devices.cancoder, and an entry in "
              "cancoder.offsets_deg.\n"
              "  FIX: run tools/cancoder_calibrate.py to record the offset.")
        return 2

    iface = args.interface or conf.can.interface
    product = SPARK_FLEX if conf.controller_type == "sparkflex" else SPARK_MAX

    print(f"corner {args.corner}: steer id {steer_id} on {iface}, "
          f"CANcoder {cc_id} on {conf.cancoder.bus}, {ratio}:1")
    if input(f"Move it to {args.angle:+.1f} deg? Wheel free? [y/N] ").strip().lower() != "y":
        return 1

    encoder = cancoder.open_encoder(cc_id, conf.cancoder.bus)
    bus = SparkBus(channel=iface)
    motor = bus.init_controller(steer_id, product, clear_sticky_faults=True)
    time.sleep(0.5)

    # Without this the controller's zero is wherever it powered on.
    seeded = cancoder.seed_from_absolute(motor, encoder, offset, ratio)
    print(f"  seeded at {seeded:+.2f} deg")

    target_rot = cancoder.motor_rotations(args.angle, ratio)
    deadline = time.time() + args.seconds
    while time.time() < deadline:
        motor.position_output(target_rot)
        time.sleep(0.02)

    motor.percent_output(0.0)
    time.sleep(0.1)
    landed = cancoder.wheel_angle_deg(cancoder.read_angle_deg(encoder), offset)
    print(f"  landed at {landed:+.2f} deg, error {args.angle - landed:+.2f}")
    bus.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
