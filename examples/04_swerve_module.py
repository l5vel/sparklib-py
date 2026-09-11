"""Drive plus steer as one reusable unit, with the startup gate wired in.

    uv run python examples/04_swerve_module.py --interface can0 \
        --modules 1:2,3:4 --duty 0.10 --angle 45

The class below is the shape to lift. It owns two controllers, exposes one
`set(duty, angle_deg)` call, and refuses to move until the gate in
03_startup_gate.py has passed for every id it owns.

The gate is the part worth copying even if you write everything else yourself. A
controller held by a hard limit accepts every setpoint frame you send and applies
exactly nothing, so a stack that only checks liveness reports a healthy robot
while half the modules are dead.

MOVES MOTORS. Lift the wheels first.
"""

import argparse
import time

from sparklib import SparkBus, SPARK_FLEX, SPARK_MAX
from sparklib import admin as sa

STEER_KP = 0.5
STEER_MAX_OUTPUT = 0.40
STEER_SLEW_PER_TICK = 0.04
TICK_S = 0.02


class Module:
    """One drive motor and one steer motor, commanded together."""

    def __init__(self, bus, drive_id, steer_id, product, gear_ratio=1.0):
        self.drive = bus.init_controller(drive_id, product, clear_sticky_faults=True)
        self.steer = bus.init_controller(steer_id, product, clear_sticky_faults=True)
        self.ids = (drive_id, steer_id)
        self.gear_ratio = gear_ratio
        self._zero_rev = None
        self._commanded = 0.0

    def zero_here(self):
        """Treat the current steer position as zero degrees."""
        self._zero_rev = self.steer.position

    def set(self, duty, angle_deg):
        """One tick: open loop on drive, proportional loop on steer."""
        if self._zero_rev is None:
            self.zero_here()
        self.drive.percent_output(duty)

        target_rev = (angle_deg / 360.0) * self.gear_ratio
        error_rev = target_rev - (self.steer.position - self._zero_rev)
        want = max(-STEER_MAX_OUTPUT, min(STEER_MAX_OUTPUT, STEER_KP * error_rev))
        step = max(-STEER_SLEW_PER_TICK, min(STEER_SLEW_PER_TICK, want - self._commanded))
        self._commanded += step
        self.steer.percent_output(self._commanded)
        return error_rev

    def stop(self):
        self.drive.percent_output(0.0)
        self.steer.percent_output(0.0)
        self._commanded = 0.0


def gate(interface, ids, window=3.0):
    """Refuse the ids that will not apply output. Returns {id: [reasons]}."""
    with sa.SparkAdmin(interface) as adm:
        status = sa.collect_status(adm.bus, window)
    generation = sa.dominant_generation(status)

    blocked = {}
    for dev in ids:
        if dev not in status:
            blocked[dev] = ["silent -- nothing arrived; try `spark clear`"]
            continue
        r = sa.normalised_reading(status[dev], generation)
        reasons = []
        if r["implausible"]:
            reasons.append("STATUS_0 is not a measurement: "
                           + sa.describe_implausible(r["implausible"]))
        else:
            if r["hard_forward_limit"]:
                reasons.append("hard forward limit reached")
            if r["hard_reverse_limit"]:
                reasons.append("hard reverse limit reached")
            if r["primary_heartbeat_lock"]:
                reasons.append("locked to another heartbeat source")
        reasons += [f"active fault: {b}" for b in r["faults"]]
        if reasons:
            blocked[dev] = reasons
    return blocked


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--interface", default="can0")
    p.add_argument("--modules", required=True,
                   help="comma-separated drive:steer id pairs, e.g. 1:2,3:4")
    p.add_argument("--product", choices=("sparkflex", "sparkmax"), default="sparkflex")
    p.add_argument("--gear-ratio", type=float, default=1.0)
    p.add_argument("--duty", type=float, default=0.10)
    p.add_argument("--angle", type=float, default=45.0)
    p.add_argument("--seconds", type=float, default=4.0)
    args = p.parse_args()

    pairs = [tuple(int(x) for x in m.split(":")) for m in args.modules.split(",")]
    all_ids = [i for pair in pairs for i in pair]

    # Runs before the drive bus exists, so nothing is enabled while it listens.
    blocked = gate(args.interface, all_ids)
    if blocked:
        for dev, reasons in sorted(blocked.items()):
            print(f"  id {dev}: BLOCKED -- {'; '.join(reasons)}")
        print("\nREFUSING to start.")
        return 1
    print(f"gate passed: {len(all_ids)} controller(s) will apply output")

    print(f"About to drive {len(pairs)} module(s) at {args.duty:+.2f}, "
          f"steering to {args.angle:+.1f} deg, for {args.seconds:.1f} s.")
    if input("Wheels off the ground? [y/N] ").strip().lower() != "y":
        return 1

    bus = SparkBus(channel=args.interface)
    product = SPARK_FLEX if args.product == "sparkflex" else SPARK_MAX
    modules = [Module(bus, d, s, product, args.gear_ratio) for d, s in pairs]
    time.sleep(0.5)
    for m in modules:
        m.zero_here()

    deadline = time.time() + args.seconds
    while time.time() < deadline:
        for m in modules:
            m.set(args.duty, args.angle)
        time.sleep(TICK_S)

    for m in modules:
        m.stop()
    time.sleep(0.1)
    bus.shutdown()
    print("done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
