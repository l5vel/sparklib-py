"""The smallest thing that turns a motor: bus, controller, heartbeat, setpoint.

    uv run python examples/01_spin_one_motor.py --interface can0 --id 1

Four things have to be true before a SPARK applies output, and this file is the
four of them in order:

  1. the CAN interface is up at 1 Mbit
  2. a controller object exists for the id, which starts the enable heartbeat
  3. the controller has no latched fault holding it disabled
  4. a setpoint arrives

Miss the third and the motor sits silent while every frame is accepted, which is
the single most common "it is not working" on this hardware.

MOVES A MOTOR. Unbolt it or lift the wheel first.
"""

import argparse
import time

from sparklib import SparkBus, SPARK_FLEX, SPARK_MAX


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--interface", default="can0", help="CAN netdev name")
    p.add_argument("--id", type=int, required=True, help="controller CAN id")
    p.add_argument("--product", choices=("sparkflex", "sparkmax"), default="sparkflex")
    p.add_argument("--duty", type=float, default=0.10, help="duty cycle, -1.0 to 1.0")
    p.add_argument("--seconds", type=float, default=2.0)
    args = p.parse_args()

    print(f"About to spin id {args.id} at {args.duty:+.2f} duty for {args.seconds:.1f} s.")
    if input("Motor free to turn? [y/N] ").strip().lower() != "y":
        return 1

    bus = SparkBus(channel=args.interface)
    product = SPARK_FLEX if args.product == "sparkflex" else SPARK_MAX

    # Starts the enable heartbeat and clears sticky faults.
    motor = bus.init_controller(args.id, product, clear_sticky_faults=True)
    time.sleep(0.5)                      # let the first status frames arrive

    print(f"  bus voltage {motor.bus_voltage:.2f} V")

    deadline = time.time() + args.seconds
    while time.time() < deadline:
        motor.percent_output(args.duty)  # re-send: the device times out in ~100 ms
        time.sleep(0.02)

    motor.percent_output(0.0)
    time.sleep(0.1)
    bus.shutdown()
    print("done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
