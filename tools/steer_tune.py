"""Sweep steer gains on one corner and recommend the set that converged best.

    uv run python tools/steer_tune.py --corner LF --dry-run    # no motion
    uv run python tools/steer_tune.py --corner LF
    uv run python tools/steer_tune.py --corner LF \
        --kp 0.004,0.008,0.012 --max-out 0.30,0.40 --deadband 0.5,1.0

Drives repeatable angle steps through each combination, scores them on settling
time, overshoot, standing error and time spent saturated, and prints a YAML
snippet for the winner.

The candidate loop is `sparklib.steer.p_output`, which is the same function a
live loop calls, so a tuned result transfers instead of being re-derived.

DRIVES ONE STEER MOTOR. Put the robot on a stand. Only the named corner is
commanded; output is zeroed on every exit path including Ctrl-C.

Needs the swerve extra: `uv sync --extra swerve`, then either export
UV_NO_SYNC=1 or run it as `uv run --extra swerve ...`, because a bare
`uv run` re-syncs to the default extras and removes phoenix6 again.
"""

import argparse
import signal
import sys
import time
from collections import deque

from sparklib import SparkBus, SPARK_FLEX, SPARK_MAX
from sparklib import cancoder, steer
from sparklib import config as cfg

TICK_S = 0.02
SETTLE_WINDOW = 10


def _floats(text):
    return [float(x) for x in text.split(",") if x.strip()]


def run_step(motor, encoder, offset, target_deg, kp, max_out, deadband,
             slew_step, timeout_s):
    """Command one angle step and return its samples."""
    samples, commanded = [], 0.0
    history = deque(maxlen=SETTLE_WINDOW)
    t_end = time.time() + timeout_s
    while time.time() < t_end:
        angle = cancoder.wheel_angle_deg(
            cancoder.read_angle_deg(encoder, settle=False), offset)
        err = steer.shortest_error_deg(target_deg, angle)
        want = steer.p_output(err, kp, max_out, deadband)
        commanded = steer.slew(commanded, want, slew_step)
        motor.percent_output(commanded)
        samples.append((time.time(), angle, commanded))
        history.append(angle)
        if steer.settled(history, err, "either"):
            break
        time.sleep(TICK_S)
    motor.percent_output(0.0)
    return samples


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--corner", required=True, help="a label from devices.steer")
    p.add_argument("--interface", help="SPARK bus; defaults to can.interface")
    p.add_argument("--kp", default="0.004,0.008,0.012")
    p.add_argument("--max-out", default="0.30,0.40")
    p.add_argument("--deadband", default="0.5,1.0")
    p.add_argument("--slew", type=float, default=0.04)
    p.add_argument("--steps", default="45,-45", help="angles to step to")
    p.add_argument("--reps", type=int, default=2)
    p.add_argument("--timeout", type=float, default=4.0, help="seconds per step")
    p.add_argument("--dry-run", action="store_true",
                   help="print the grid and the config it resolved, command nothing")
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
              "  FIX: run tools/cancoder_calibrate.py first.")
        return 2

    grid = [(kp, mo, db)
            for kp in _floats(args.kp)
            for mo in _floats(args.max_out)
            for db in _floats(args.deadband)]
    targets = _floats(args.steps)
    total = len(grid) * len(targets) * args.reps

    print(f"corner {args.corner}: steer id {steer_id}, CANcoder {cc_id}, {ratio}:1")
    print(f"grid {len(grid)} combination(s) x {len(targets)} step(s) x "
          f"{args.reps} rep(s) = {total} runs, up to "
          f"{total * args.timeout / 60:.1f} min")
    if args.dry_run:
        for kp, mo, db in grid:
            print(f"  kp={kp:<7} max_out={mo:<6} deadband={db}")
        return 0

    if input("Wheel free to turn, robot on a stand? [y/N] ").strip().lower() != "y":
        return 1

    iface = args.interface or conf.can.interface
    product = SPARK_FLEX if conf.controller_type == "sparkflex" else SPARK_MAX
    encoder = cancoder.open_encoder(cc_id, conf.cancoder.bus)
    bus = SparkBus(channel=iface)
    motor = bus.init_controller(steer_id, product, clear_sticky_faults=True)

    def stop(*_):
        motor.percent_output(0.0)
        time.sleep(0.05)
        bus.shutdown()
        print("\nstopped")
        sys.exit(1)

    signal.signal(signal.SIGINT, stop)
    time.sleep(0.5)

    results = []
    try:
        for kp, mo, db in grid:
            costs = []
            for target in targets:
                for _ in range(args.reps):
                    s = run_step(motor, encoder, offset, target,
                                 kp, mo, db, args.slew, args.timeout)
                    costs.append(steer.score_step(s, target, db))
                    time.sleep(0.3)
            valid = [c for c in costs if c["cost"] is not None]
            mean = sum(c["cost"] for c in valid) / len(valid) if valid else None
            settles = [c["settle_s"] for c in valid if c["settle_s"] is not None]
            over = [c["overshoot_deg"] for c in valid]
            results.append({
                "kp": kp, "max_out": mo, "deadband": db, "cost": mean,
                "settle": sum(settles) / len(settles) if settles else None,
                "overshoot": max(over) if over else None,
            })
            print(f"  kp={kp:<7} max_out={mo:<6} deadband={db:<5} "
                  f"cost={mean:.3f}" if mean is not None else
                  f"  kp={kp} max_out={mo} deadband={db} cost=n/a")
    finally:
        motor.percent_output(0.0)
        time.sleep(0.05)
        bus.shutdown()

    scored = [r for r in results if r["cost"] is not None]
    if not scored:
        print("\nnothing converged. Widen the grid: lower kp, raise max_out, "
              "or loosen deadband.")
        return 1

    scored.sort(key=lambda r: r["cost"])
    print(f"\n{'rank':>4} {'kp':>8} {'max_out':>8} {'deadband':>9} "
          f"{'settle_s':>9} {'overshoot':>10} {'cost':>7}")
    for i, r in enumerate(scored[:8], 1):
        print(f"{i:>4} {r['kp']:>8} {r['max_out']:>8} {r['deadband']:>9} "
              f"{(r['settle'] or float('nan')):>9.2f} "
              f"{(r['overshoot'] or float('nan')):>10.2f} {r['cost']:>7.3f}")

    best = scored[0]
    print("\npaste into your config, under the steer gains you use:\n")
    print(f"  steer_kp: {best['kp']}")
    print(f"  steer_max_output: {best['max_out']}")
    print(f"  steer_deadband_deg: {best['deadband']}")
    print("\nRe-run on another corner before trusting one result across all four.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
