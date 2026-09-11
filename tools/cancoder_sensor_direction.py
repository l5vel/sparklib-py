"""Check that every corner's CANcoder counts the same way its wheel turns.

    uv run python tools/cancoder_sensor_direction.py
    uv run python tools/cancoder_sensor_direction.py --corner LF --rotate-deg 30

Every encoder on a bus can report COUNTER_CLOCKWISE_POSITIVE and still disagree
with the one next to it. sensor_direction is a device setting, and it says
nothing about how the housing is bolted on. Turn one housing 180 deg about its
own axis and the same setting produces the opposite sign of motion for the same
wheel rotation. A configuration audit cannot see that, because
tools/cancoder_audit.py reads back what the device was told and then trusts the
absolute position it publishes.

So this tool asks you to rotate each wheel by hand, roughly 30 deg clockwise as
viewed from above, which is the usual swerve heading convention, and reports
whether the reading went up or down. Every corner should agree in sign. Two up
and two down means those two are mounted opposite, and their sensor_direction
needs flipping. Run it as a swerve setup step, because a corner that counts
backwards drives away from its target instead of toward it.

A corner whose encoder never answers is reported as unread, because phoenix6
hands back a plausible default with a bad status code when an id is silent. A
corner you stop before, or one that moved too little to sign, is named in the
summary and the run exits non-zero, so a partial pass never reads as a clean
one.

Read-only. It writes no configuration, clears no faults, and commands no motor,
since you turn the wheels yourself. The repair is a one-time write of
sensor_direction, best done in Phoenix Tuner X.

Needs the swerve extra: `uv sync --extra swerve`, then either export
UV_NO_SYNC=1 or run it as `uv run --extra swerve ...`, because a bare
`uv run` re-syncs to the default extras and removes phoenix6 again.
"""

import argparse
import sys
import time

from sparklib import cancoder, steer
from sparklib import config as cfg

STATIONARY_SAMPLES = 20
SAMPLE_DT = 0.02
SIGN_TOLERANCE_DEG = 5.0        # noise floor for sign determination
JITTER_WARN_DEG = 0.5
WRAP_DEG = 180.0
ZERO = "ZERO (rotate more)"
UNREAD = "NOT READ"
REFUSED = 2


def _device_id(value):
    """A CANcoder id as a whole number, or None when the config has none."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _sensor_direction(encoder):
    """The configured count direction, or why it could not be read."""
    try:
        return cancoder.describe(encoder)["sensor_direction"]
    except Exception as err:            # noqa: BLE001 - report, do not abort
        return f"<unavailable: {err}>"


def _report_status(encoder):
    """Whether the absolute-position signal reported, and what its status says.

    phoenix6 hands back a default value carrying a bad StatusCode when nothing
    answers an id, so an unchecked read makes a silent CANcoder look plausible.
    A version that exposes no status is trusted, since there is nothing to check
    the reading against.
    """
    try:
        status = getattr(encoder.get_absolute_position(), "status", None)
        if status is None or not hasattr(status, "is_ok"):
            return True, "<status not reported by this phoenix6>"
        return bool(status.is_ok()), str(getattr(status, "name", status))
    except Exception as err:            # noqa: BLE001 - a failed read is a result
        return False, f"<unreadable: {err}>"


def _hold_reading(label, encoder):
    """Mean angle over a stationary window, its spread, and the signal status.

    Every sample is measured as a wrapped difference from the first, so a corner
    parked on the encoder's own discontinuity reports the jitter it has instead
    of a full turn of it.
    """
    sig = encoder.get_absolute_position()
    try:
        cancoder.warmup([(label, sig)], stable_tol_deg=JITTER_WARN_DEG,
                        verbose=False)
    except TimeoutError:
        pass                            # the spread below carries it
    anchor = cancoder.read_angle_deg(encoder, settle=False)
    deltas = [0.0]
    for _ in range(STATIONARY_SAMPLES - 1):
        time.sleep(SAMPLE_DT)
        deltas.append(steer.normalize_deg(
            cancoder.read_angle_deg(encoder, settle=False) - anchor))
    reporting, status = _report_status(encoder)
    mean = steer.normalize_deg(anchor + sum(deltas) / len(deltas))
    return mean, max(deltas) - min(deltas), reporting, status


def _unread(label, sensor_direction, status):
    """The result record for a corner whose encoder never reported."""
    print(f"  absolute_position: <not reported: {status}>")
    print("    --> nothing answered at this id, so any angle here would be a "
          "phoenix6 default. Check the CAN id and the wiring on this corner "
          "before turning the wheel.")
    return {"name": label, "delta": None, "sign": UNREAD,
            "sensor_direction": sensor_direction}


def inspect_one(label, device_id, encoder, rotate_deg):
    """Read one corner before and after a hand rotation, and sign the delta."""
    print(f"\n=== {label} (CANcoder id {device_id}) ===")
    sd = _sensor_direction(encoder)
    print(f"  sensor_direction (config): {sd}")

    print("  Step 1/2: hold the wheel still. Reading P0 ...")
    p0, j0, reporting, status = _hold_reading(label, encoder)
    if not reporting:
        return _unread(label, sd, status)
    print(f"  P0 = {p0:+8.3f} deg  (jitter {j0:.3f})")
    if j0 > JITTER_WARN_DEG:
        print(f"  WARNING: jitter {j0:.3f} is over {JITTER_WARN_DEG} deg, so "
              "the wheel is still moving. Lock it and re-run.")

    print()
    print(f"  Step 2/2: rotate the {label} wheel by hand, about "
          f"{rotate_deg:+.0f} deg")
    print("           CLOCKWISE as viewed from ABOVE, looking down at the chassis.")
    print("           Precision does not matter; only the SIGN of motion is read.")
    input("  Press Enter once the wheel is rotated and held still ... ")

    p1, j1, reporting, status = _hold_reading(label, encoder)
    if not reporting:
        return _unread(label, sd, status)
    print(f"  P1 = {p1:+8.3f} deg  (jitter {j1:.3f})")

    delta = steer.normalize_deg(p1 - p0)
    print(f"  delta = P1 - P0 = {delta:+8.3f} deg")
    print("  the sign on its own depends on the mounting, so agreement across "
          "corners is the test")
    if abs(delta) < SIGN_TOLERANCE_DEG:
        sign = ZERO
    elif delta > 0:
        sign = "POSITIVE"
    else:
        sign = "NEGATIVE"
    print(f"  detected sign:   {sign}")
    return {"name": label, "delta": delta, "sign": sign, "sensor_direction": sd}


def summarize(results, not_reached):
    """Print the table and the cross-corner diagnosis. Returns an exit code."""
    print()
    print("=" * 70)
    print("Summary")
    print("=" * 70)
    print(f"  {'corner':<6} {'delta':>8}  {'sign':<18} sensor_direction")
    for r in results:
        shown = "      --" if r["delta"] is None else f"{r['delta']:+8.2f}"
        print(f"  {r['name']:<6} {shown}  {r['sign']:<18} "
              f"{r['sensor_direction']}")
    for label in not_reached:
        print(f"  {label:<6} {'--':>8}  {'NOT REACHED':<18} -")
    print()

    unread = [r["name"] for r in results if r["sign"] == UNREAD]
    measured = [r for r in results if r["sign"] != UNREAD]
    zeros = [r["name"] for r in measured if r["sign"] == ZERO]

    if unread:
        print(f"  {len(unread)} corner(s) never reported: {', '.join(unread)}. "
              "Nothing below covers them.")
    if not_reached:
        print(f"  {len(not_reached)} corner(s) not reached: "
              f"{', '.join(not_reached)}. The run ended early, so this is a "
              "partial result.")
    if zeros:
        print(f"  {len(zeros)} corner(s) moved too little to sign: "
              f"{', '.join(zeros)}. Rotate those further and re-run.")
    incomplete = bool(unread or not_reached or zeros)

    if not measured:
        print("  Nothing was measured, so there is no consistency verdict.")
        return 1
    if len(measured) < 2:
        print("  One corner on its own says nothing about consistency. Measure "
              "the others before changing any setting.")
        return 1 if incomplete else 0

    signs = [r["sign"] for r in measured if r["sign"] != ZERO]
    pos = sum(1 for s in signs if s == "POSITIVE")
    neg = sum(1 for s in signs if s == "NEGATIVE")

    if pos and neg:
        print(f"  MIXED SIGNS detected ({pos} positive, {neg} negative).")
        print("  The CANcoders are not all mounted the same way relative to")
        print("  their configured sensor_direction. Keep the majority sign and")
        print("  flip sensor_direction on the minority corners in Phoenix Tuner X:")
        majority = "POSITIVE" if pos >= neg else "NEGATIVE"
        for r in measured:
            if r["sign"] in ("POSITIVE", "NEGATIVE") and r["sign"] != majority:
                print(f"    [{r['name']}] minority sign, flip sensor_direction "
                      "in Tuner X")
        print()
        print("  Re-run this tool after flipping to confirm every sign matches.")
        return 1

    if pos and not neg:
        print("  Every corner that gave a sign reports a POSITIVE delta for a")
        print("  clockwise turn from above, so mounting and sensor_direction")
        print("  are CONSISTENT across those corners.")
        return 1 if incomplete else 0

    if neg and not pos:
        print("  Every corner that gave a sign reports a NEGATIVE delta for a")
        print("  clockwise turn from above, so mounting and sensor_direction")
        print("  are CONSISTENT across those corners. The sign is simply")
        print("  opposite to what the COUNTER_CLOCKWISE_POSITIVE label suggests")
        print("  visually.")
        return 1 if incomplete else 0

    print("  No clear signs detected. Try a larger --rotate-deg.")
    return 1


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--corner", default="all",
                   help="one label from devices.cancoder, e.g. LF, or 'all' "
                        "(default: all)")
    p.add_argument("--bus", help="override cancoder.bus")
    p.add_argument("--rotate-deg", type=float, default=30.0,
                   help="approximate hand rotation in deg (default 30; "
                        "precision is unimportant, only the sign matters)")
    args = p.parse_args(argv)

    if not SIGN_TOLERANCE_DEG < args.rotate_deg < WRAP_DEG:
        print(f"REFUSED: --rotate-deg {args.rotate_deg:g} cannot produce a "
              f"readable sign. Below {SIGN_TOLERANCE_DEG:.0f} deg the delta "
              f"sits in the noise floor, and at {WRAP_DEG:.0f} deg or beyond it "
              "wraps and reads as the opposite sign.\n"
              f"  FIX: pass a value between {SIGN_TOLERANCE_DEG:.0f} and "
              f"{WRAP_DEG:.0f}, such as the default 30.")
        return REFUSED

    if not cancoder.available():
        print(cancoder.EXTRA_HINT)
        return REFUSED

    cfg.load_host()
    conf = cfg.get()
    group = getattr(getattr(conf, "devices", None), "cancoder", None)
    if group is None or not vars(group):
        print("REFUSED: this config names no CANcoders, so there are no "
              "absolute encoders to check.\n"
              "  FIX: add a `devices.cancoder` group and a `cancoder:` block to "
              "your config. sparklib/data/spark.yaml carries a worked example "
              "of both.")
        return REFUSED

    devices = vars(group)
    bus = args.bus or getattr(getattr(conf, "cancoder", None), "bus", None)
    if not bus:
        print("REFUSED: no CANcoder bus named.\n"
              "  FIX: set `cancoder.bus` in the config, or pass --bus. It is "
              "the netdev the encoders are on, which is usually a different "
              "adapter from the SPARK bus.")
        return REFUSED

    if args.corner.lower() != "all":
        if args.corner not in devices:
            print(f"REFUSED: {args.corner!r} is not in devices.cancoder "
                  f"({', '.join(devices)}).\n"
                  "  FIX: name one of those labels, or pass --corner all.")
            return REFUSED
        devices = {args.corner: devices[args.corner]}

    ids = {}
    unusable = []
    for label, value in devices.items():
        parsed = _device_id(value)
        if parsed is None:
            unusable.append(f"{label}={value!r}")
        else:
            ids[label] = parsed
    if unusable:
        print("REFUSED: devices.cancoder carries no usable CAN id for "
              f"{', '.join(unusable)}.\n"
              "  FIX: give every label a whole number, as in "
              "`cancoder: {LF: 1, RF: 3, LB: 2, RB: 4}`.")
        return REFUSED

    order = list(ids)
    print("CANcoder mounting and sensor_direction consistency check")
    print("=" * 70)
    print(f"bus {bus} -- {len(order)} CANcoder(s): {', '.join(order)}")
    print("Read-only: it writes no configuration and commands no motor, so "
          "every rotation below is one you make by hand.")
    print(f"Each of those wheels in turn gets rotated about "
          f"{args.rotate_deg:.0f} deg")
    print("CLOCKWISE as viewed from above. The tool reports the sign of the")
    print("CANcoder delta, and every corner should agree on it.")

    results = []
    not_reached = []
    for index, label in enumerate(order):
        try:
            encoder = cancoder.open_encoder(ids[label], bus)
            results.append(inspect_one(label, ids[label], encoder,
                                       args.rotate_deg))
        except (KeyboardInterrupt, EOFError):
            print(f"\n  [{label}] aborted before it was measured")
            not_reached = order[index:]
            break
        except Exception as err:        # noqa: BLE001 - one bad corner, not the run
            print(f"  [{label}] FAILED: {err}")
            results.append({"name": label, "delta": None, "sign": UNREAD,
                            "sensor_direction": f"<unavailable: {err}>"})

    return summarize(results, not_reached)


if __name__ == "__main__":
    sys.exit(main())
