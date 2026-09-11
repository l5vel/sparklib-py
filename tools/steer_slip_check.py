"""Tell a slipping steer chain apart from gains that need tuning.

    uv run python tools/steer_slip_check.py --corner LF

Commands a slow, small ramp and watches the absolute angle. A rigid chain gives
a smooth monotonic response. A slipping one lags and then catches up in jumps,
and no amount of tuning fixes it: higher gains make it worse.

This is the test to run before a tuning session, because a slipping corner
produces a grid where nothing converges and the obvious conclusion is wrong.

DRIVES ONE STEER MOTOR, slowly and briefly. Robot on a stand.

Needs the swerve extra: `uv sync --extra swerve`, then either export
UV_NO_SYNC=1 or run it as `uv run --extra swerve ...`, because a bare
`uv run` re-syncs to the default extras and removes phoenix6 again.
"""

import argparse
import signal
import sys
import time

from sparklib import SparkBus, SPARK_FLEX, SPARK_MAX
from sparklib import cancoder, steer
from sparklib import config as cfg

TICK_S = 0.05


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--corner", required=True)
    p.add_argument("--interface")
    p.add_argument("--duty", type=float, default=0.06, help="ramp duty, small")
    p.add_argument("--seconds", type=float, default=3.0)
    p.add_argument("--jump-factor", type=float, default=3.0,
                   help="a tick this many times the run's own median counts as "
                        "a catch-up")
    args = p.parse_args(argv)

    if not cancoder.available():
        print(cancoder.EXTRA_HINT)
        return 2

    cfg.load_host()
    conf = cfg.get()
    steer_id = getattr(conf.devices.steer, args.corner, None)
    cc_id = getattr(getattr(conf.devices, "cancoder", None), args.corner, None)
    if steer_id is None or cc_id is None:
        print(f"REFUSED: corner {args.corner!r} needs an id under devices.steer "
              "and one under devices.cancoder.")
        return 2

    print(f"corner {args.corner}: steer id {steer_id}, CANcoder {cc_id}, "
          f"{args.duty:+.2f} duty for {args.seconds:.1f} s")
    if input("Wheel free to turn? [y/N] ").strip().lower() != "y":
        return 1

    iface = args.interface or conf.can.interface
    product = SPARK_FLEX if conf.controller_type == "sparkflex" else SPARK_MAX
    encoder = cancoder.open_encoder(cc_id, conf.cancoder.bus)
    bus = SparkBus(channel=iface)
    motor = bus.init_controller(steer_id, product, clear_sticky_faults=True)

    def stop(*_):
        motor.percent_output(0.0); time.sleep(0.05); bus.shutdown()
        print("\nstopped"); sys.exit(1)

    signal.signal(signal.SIGINT, stop)
    time.sleep(0.5)

    angles = []
    try:
        t_end = time.time() + args.seconds
        while time.time() < t_end:
            motor.percent_output(args.duty)
            angles.append(cancoder.read_angle_deg(encoder, settle=False))
            time.sleep(TICK_S)
    finally:
        motor.percent_output(0.0)
        time.sleep(0.05)
        bus.shutdown()

    deltas = [steer.normalize_deg(b - a) for a, b in zip(angles, angles[1:])]
    if not deltas:
        print("no readings; the encoder did not report")
        return 1

    moved = sum(deltas)

    # A catch-up is fast against THIS run's own pace. A fixed threshold below
    # the pace the duty produces marks every tick, which is a slip verdict on a
    # healthy corner. The median sets the scale, so --duty stays free.
    magnitudes = sorted(abs(d) for d in deltas)
    median = magnitudes[len(magnitudes) // 2]
    jump_deg = max(0.5, median * args.jump_factor)
    stall_deg = max(0.02, median * 0.05)

    # Ends of the run always include a slow tick, so judge the middle.
    interior = deltas[2:-2] if len(deltas) > 8 else deltas
    jumps = [d for d in interior if abs(d) > jump_deg]
    stalls = [d for d in interior if abs(d) < stall_deg]
    reversals = sum(1 for a, b in zip(deltas, deltas[1:]) if a * b < 0)

    print(f"\n  travelled     {moved:+.2f} deg over {len(deltas)} ticks")
    print(f"  per-tick      mean {moved/len(deltas):+.3f}  "
          f"median {median:.3f}  max {max(deltas, key=abs):+.3f}")
    print(f"  catch-ups     {len(jumps)} tick(s) over {jump_deg:.2f} deg "
          f"({args.jump_factor:g}x the median)")
    print(f"  stalls        {len(stalls)} tick(s) under {stall_deg:.3f} deg")
    print(f"  reversals     {reversals}")

    if abs(moved) < 1.0:
        print("\nthe axis barely moved. Raise --duty, or the chain is bound or "
              "the motor is held by a limit. Run examples/03_startup_gate.py.")
        return 1
    if jumps and stalls:
        print("\nSLIP LIKELY: the angle stalled and then caught up. Tuning will "
              "not fix this. Check the magnet bonding first, then the steer "
              "chain fasteners. docs/SWERVE-CALIBRATION.md covers both.")
        return 1
    print("\nno slip signature: the response is smooth and monotonic. A tuning "
          "problem here is a tuning problem.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
