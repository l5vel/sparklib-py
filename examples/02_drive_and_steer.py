"""Two motors with different jobs: one spins, one holds an angle.

    uv run python examples/02_drive_and_steer.py --interface can0 \
        --drive-id 1 --steer-id 2 --angle 90

A swerve module is the common case, but the split is general. A DRIVE motor is
commanded in open loop and you care about its speed. A STEER motor has to reach
and hold a position, so something has to close a loop around it.

This closes that loop on the host, in plain Python, against the SPARK's own
encoder. Host-side is the honest default here: it needs no on-device
configuration, it is visible when it misbehaves, and a P term plus a slew limit
covers most of what a steer axis needs. Put the loop on the device with REV
Hardware Client when you need the DSP's update rate.

MOVES TWO MOTORS. Unbolt them or lift the wheel first.
"""

import argparse
import time

from sparklib import SparkBus, SPARK_FLEX, SPARK_MAX

# Duty cycle per revolution of position error.
STEER_KP = 0.5
# Largest duty cycle the steer motor may be given, whatever the error.
STEER_MAX_OUTPUT = 0.40
# Largest change in commanded duty per tick.
STEER_SLEW_PER_TICK = 0.04
TICK_S = 0.02


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--interface", default="can0")
    p.add_argument("--drive-id", type=int, required=True)
    p.add_argument("--steer-id", type=int, required=True)
    p.add_argument("--product", choices=("sparkflex", "sparkmax"), default="sparkflex")
    p.add_argument("--duty", type=float, default=0.10, help="drive duty cycle")
    p.add_argument("--angle", type=float, default=90.0, help="steer target, degrees")
    p.add_argument("--gear-ratio", type=float, default=1.0,
                   help="motor revolutions per one revolution of the steered axis")
    p.add_argument("--seconds", type=float, default=4.0)
    args = p.parse_args()

    print(f"About to spin id {args.drive_id} at {args.duty:+.2f} and steer id "
          f"{args.steer_id} to {args.angle:+.1f} deg for {args.seconds:.1f} s.")
    if input("Both motors free to turn? [y/N] ").strip().lower() != "y":
        return 1

    bus = SparkBus(channel=args.interface)
    product = SPARK_FLEX if args.product == "sparkflex" else SPARK_MAX
    drive = bus.init_controller(args.drive_id, product, clear_sticky_faults=True)
    steer = bus.init_controller(args.steer_id, product, clear_sticky_faults=True)
    time.sleep(0.5)

    # Target in motor revolutions, measured from wherever the axis powered on.
    target_rev = (args.angle / 360.0) * args.gear_ratio
    start_rev = steer.position
    commanded = 0.0

    deadline = time.time() + args.seconds
    while time.time() < deadline:
        drive.percent_output(args.duty)

        error_rev = target_rev - (steer.position - start_rev)
        want = max(-STEER_MAX_OUTPUT, min(STEER_MAX_OUTPUT, STEER_KP * error_rev))
        # Slew toward the wanted duty instead of jumping to it.
        step = max(-STEER_SLEW_PER_TICK, min(STEER_SLEW_PER_TICK, want - commanded))
        commanded += step
        steer.percent_output(commanded)

        time.sleep(TICK_S)

    drive.percent_output(0.0)
    steer.percent_output(0.0)
    time.sleep(0.1)
    print(f"  steer error at stop: {target_rev - (steer.position - start_rev):+.3f} rev")
    bus.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# A real module seeds this loop from an absolute encoder, because the SPARK's
# own encoder is relative. sparklib.cancoder does that; see
# examples/05_seed_and_steer.py and docs/STEER-CONTROL.md.
