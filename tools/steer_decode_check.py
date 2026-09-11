"""Position decode sanity check on one steer motor.

    uv run python tools/steer_decode_check.py --corner LF

Prints the decoded motor position beside the CANcoder angle so a hand-turned
wheel can be correlated against what the controller reports, then says whether
the decode survived. One zero-duty frame goes out on enable, so a setpoint left
over from an earlier run cannot drive the axis, and nothing else is commanded.
The wheel stays free to turn by hand for the whole run.

Position comes from Status 2. On firmware 25+ that frame carries velocity in
float[0] and position in float[1], and before 25 position has a frame of its
own. Reading the wrong half of the wrong frame is what this catches, and it
looks like working telemetry until someone turns the wheel.

Procedure, printed again at runtime: hold the wheel STILL for the first
samples, then rotate it about one full WHEEL revolution by hand for the rest.

A correct decode gives:
  - position constant within noise while the wheel is held still
  - position monotonic while the wheel turns, with a consistent sign
  - position travel over one wheel revolution near cancoder.steer_gear_ratio
    motor rotations, which is 12.8 on an MK2 and 26.0 on an MK5i

Jumps, NaN, a stuck value or a tiny range mean Status 2 is being decoded wrong
for the firmware on this controller.

ENABLES ONE STEER CONTROLLER at zero duty for the length of the run, because a
disabled SPARK stops broadcasting the frame under test. Robot on a stand.

Exit 0 means the decode survived, 1 that it is unproven or the run was cut
short, and 2 that the check refused to run and energized nothing.

Needs the swerve extra: `uv sync --extra swerve`, then either export
UV_NO_SYNC=1 or run it as `uv run --extra swerve ...`, because a bare
`uv run` re-syncs to the default extras and removes phoenix6 again.
"""

import argparse
import math
import signal
import sys
import time

from sparklib import SparkBus, SPARK_FLEX, SPARK_MAX
from sparklib import cancoder, steer
from sparklib import config as cfg

EXIT_OK = 0
EXIT_UNPROVEN = 1
EXIT_REFUSED = 2

LIVE_WINDOW_S = 1.0
MIN_TURN_DEG = 30.0
SPARK_ID_RANGE = (1, 62)
CANCODER_ID_RANGE = (0, 62)


def _number(value):
    """True for a finite numeric reading."""
    return isinstance(value, (int, float)) and not isinstance(value, bool) \
        and math.isfinite(value)


def _read_position(motor):
    """Decoded motor position in rotations, or a short error tag."""
    try:
        return motor.position
    except Exception as err:            # noqa: BLE001 - a failed read is the result
        return f"ERR:{type(err).__name__}"


def _read_raw_deg(angle_sig):
    """Absolute CANcoder angle in degrees, or a short error tag.

    phoenix6 answers a device that is not talking with a default value and a
    bad status code instead of raising, so a silent encoder reads as a
    plausible angle. The status decides whether the number is believed.
    """
    try:
        angle_sig.refresh(False)
    except Exception as err:            # noqa: BLE001 - a failed read is the result
        return f"ERR:{type(err).__name__}"
    status = getattr(angle_sig, "status", None)
    if status is not None and hasattr(status, "is_ok") and not status.is_ok():
        return f"ERR:{getattr(status, 'name', status)}"
    return angle_sig.value * 360.0


def _spread(values):
    return (max(values) - min(values)) if values else float("nan")


def _travel_deg(angles):
    """Total signed wheel travel across wrapped absolute readings."""
    return sum(steer.normalize_deg(b - a) for a, b in zip(angles, angles[1:]))


def _fmt(value, places):
    return f"{value:+.{places}f}" if _number(value) else str(value)


def _above_zero(flag, value, what):
    """Refusal text for a flag that has to sit above zero, or None."""
    if _number(value) and value > 0:
        return None
    return (f"REFUSED: {flag} is {value}, and it sets {what}.\n"
            f"  FIX: pass {flag} above zero.")


def _device_id(where, value, limits):
    """One CAN id as an int, or refusal text saying what to put in the config."""
    low, high = limits
    ok = not isinstance(value, bool)
    try:
        as_int = int(value)
        ok = ok and as_int == float(value)
    except (TypeError, ValueError):
        ok = False
    if not ok:
        return None, (f"REFUSED: {where} is {value!r}, which is not a CAN id.\n"
                      "  FIX: set it to the device id, a whole number.")
    if not low <= as_int <= high:
        return None, (f"REFUSED: {where} is {as_int}, outside the {low} to "
                      f"{high} this bus can address.\n  FIX: set it to the id "
                      "the device actually answers on.")
    return as_int, None


def analyse(rows, hold, still_tol_rot, ratio, ratio_tol):
    """Summary numbers and the problems they show, from the printed samples."""
    positions = [p for p, _, _ in rows]
    held = [p for p in positions[:hold] if _number(p)]
    turn_pos = [p for p, _, _ in rows[hold:] if _number(p)]
    turn_raw = [r for _, r, _ in rows[hold:] if _number(r)]

    deltas = [b - a for a, b in zip(turn_pos, turn_pos[1:])]
    moving = [d for d in deltas if abs(d) > still_tol_rot]
    reversals = sum(1 for a, b in zip(moving, moving[1:]) if a * b < 0)
    pos_travel = abs(sum(deltas))
    wheel_travel = abs(_travel_deg(turn_raw))
    measured = (pos_travel / (wheel_travel / 360.0)) if wheel_travel > 1.0 else None

    bad_pos = sum(1 for p in positions if not _number(p))
    bad_raw = sum(1 for _, r, _ in rows if not _number(r))
    stale = sum(1 for _, _, age in rows if age > LIVE_WINDOW_S)
    still_spread = _spread(held)

    problems = []
    if stale:
        problems.append(f"{stale} of {len(rows)} samples came from a controller "
                        f"that had sent nothing for over {LIVE_WINDOW_S:.1f} s. "
                        "Those positions are the last value decoded, not a live "
                        "reading, so the travel below is short by whatever "
                        "happened while it was quiet.")
    if bad_pos:
        problems.append(f"{bad_pos} of {len(rows)} position reads came back "
                        "non-numeric. Status 2 is not arriving, or it is being "
                        "decoded against the wrong firmware generation.")
    if bad_raw:
        problems.append(f"{bad_raw} of {len(rows)} CANcoder reads failed, so the "
                        "correlation below rests on fewer samples than it looks.")
    if len(held) >= 2 and still_spread > still_tol_rot:
        problems.append(f"position moved {still_spread:.4f} rot while the wheel "
                        f"was held still, over a tolerance of {still_tol_rot:.4f}. "
                        "A held axis that reports motion is reading a field that "
                        "is not position.")
    if len(turn_pos) >= 2 and _spread(turn_pos) < still_tol_rot:
        problems.append("position never changed while the wheel turned, so "
                        "either the value is stuck or Status 2 never arrived. "
                        "A plausible constant hides both.")
    if reversals:
        problems.append(f"position reversed {reversals} time(s) during a turn in "
                        "one direction. A correct decode is monotonic here.")
    if wheel_travel < MIN_TURN_DEG:
        problems.append(f"the wheel only travelled {wheel_travel:.1f} deg, which "
                        "is too little to judge the ratio. Re-run and turn it a "
                        "full revolution.")
    elif measured is not None and ratio:
        gap = abs(measured - float(ratio)) / float(ratio)
        if gap > ratio_tol:
            problems.append(f"one wheel revolution measured {measured:.2f} motor "
                            f"rotations against the configured steer_gear_ratio "
                            f"of {float(ratio):.2f}, a gap of {gap * 100:.0f} "
                            "percent. Either the decode is wrong or the ratio is.")

    return {"still_spread": still_spread, "pos_travel": pos_travel,
            "wheel_travel": wheel_travel, "measured_ratio": measured,
            "reversals": reversals, "samples": len(rows),
            "still_samples": len(held), "turn_samples": len(turn_pos),
            "stale": stale, "problems": problems}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--corner", required=True, help="a label from devices.steer")
    p.add_argument("--interface", help="SPARK bus; defaults to can.interface")
    p.add_argument("--bus", help="CANcoder bus; defaults to cancoder.bus")
    p.add_argument("--samples", type=int, default=25, help="readings to take")
    p.add_argument("--hold", type=int, default=10,
                   help="leading samples taken with the wheel held still")
    p.add_argument("--interval", type=float, default=0.30,
                   help="seconds between readings")
    p.add_argument("--warmup", type=float, default=1.5,
                   help="seconds to let Status 2 and CANcoder frames populate")
    p.add_argument("--still-tol-rot", type=float, default=0.05,
                   help="position movement allowed while the wheel is held still")
    p.add_argument("--ratio-tol", type=float, default=0.25,
                   help="fraction the measured ratio may differ from the config")
    args = p.parse_args(argv)

    if args.hold < 2:
        print(f"REFUSED: --hold is {args.hold}, which leaves the still phase one "
              "sample, and the spread of one sample is always zero.\n"
              "  FIX: pass --hold 2 or more.")
        return EXIT_REFUSED
    if args.samples < args.hold + 2:
        print(f"REFUSED: --samples {args.samples} leaves fewer than two readings "
              f"after --hold {args.hold}, so the turning phase has no travel to "
              "measure.\n  FIX: raise --samples to at least --hold plus 2.")
        return EXIT_REFUSED
    if not _number(args.warmup) or args.warmup < 0:
        print(f"REFUSED: --warmup is {args.warmup}, and it is a wait before the "
              "first reading.\n  FIX: pass --warmup at zero or above.")
        return EXIT_REFUSED
    for text in (_above_zero("--interval", args.interval,
                             "the gap between readings"),
                 _above_zero("--still-tol-rot", args.still_tol_rot,
                             "the motion a held wheel is allowed"),
                 _above_zero("--ratio-tol", args.ratio_tol,
                             "how far the measured ratio may sit from the config")):
        if text:
            print(text)
            return EXIT_REFUSED

    if not cancoder.available():
        print(cancoder.EXTRA_HINT)
        return EXIT_REFUSED

    cfg.load_host()
    conf = cfg.get()
    devices = getattr(conf, "devices", None)
    steer_group = getattr(devices, "steer", None)
    cc_group = getattr(devices, "cancoder", None)
    if steer_group is None or cc_group is None:
        print("REFUSED: this check reads one steer motor against one CANcoder.\n"
              "  FIX: add a `devices.steer` group and a `devices.cancoder` "
              "group to spark.yaml.")
        return EXIT_REFUSED

    labels = list(vars(steer_group))
    steer_id = getattr(steer_group, args.corner, None)
    cc_id = getattr(cc_group, args.corner, None)
    if steer_id is None or cc_id is None:
        print(f"REFUSED: corner {args.corner!r} needs an id under devices.steer "
              f"and one under devices.cancoder.\n"
              f"  FIX: name one of {', '.join(labels) or 'nothing configured'}, "
              "or add this corner to both groups.")
        return EXIT_REFUSED

    steer_id, refusal = _device_id(f"devices.steer.{args.corner}", steer_id,
                                   SPARK_ID_RANGE)
    if refusal:
        print(refusal)
        return EXIT_REFUSED
    cc_id, refusal = _device_id(f"devices.cancoder.{args.corner}", cc_id,
                                CANCODER_ID_RANGE)
    if refusal:
        print(refusal)
        return EXIT_REFUSED

    can_conf = getattr(conf, "can", None)
    cc_conf = getattr(conf, "cancoder", None)
    iface = args.interface or getattr(can_conf, "interface", None)
    bus_name = args.bus or getattr(cc_conf, "bus", None)
    product = getattr(conf, "controller_type", None)

    missing = []
    if not iface:
        missing.append("can.interface, the netdev the SPARKs came up on")
    if not bus_name:
        missing.append("cancoder.bus, the netdev the CANcoders are on")
    if product not in (SPARK_FLEX, SPARK_MAX):
        missing.append(f"controller_type, either {SPARK_FLEX} or {SPARK_MAX}")
    if missing:
        print("REFUSED: the config is missing what this check reads.\n"
              + "".join(f"    {m}\n" for m in missing)
              + "  FIX: add those keys to spark.yaml.")
        return EXIT_REFUSED

    ratio = getattr(cc_conf, "steer_gear_ratio", None)
    if ratio is not None and not (_number(ratio) and ratio > 0):
        print(f"REFUSED: cancoder.steer_gear_ratio is {ratio!r}, which is not a "
              "ratio the travel can be compared against.\n  FIX: set it to motor "
              "rotations per wheel revolution, 26.0 on an MK5i and 12.8 on an "
              "MK2, or drop the key and the comparison is skipped.")
        return EXIT_REFUSED

    offset = getattr(getattr(cc_conf, "offsets_deg", None), args.corner, None)
    if offset is not None and not _number(offset):
        print(f"REFUSED: cancoder.offsets_deg.{args.corner} is {offset!r}, which "
              "is not an angle.\n  FIX: set it to the degrees this encoder reads "
              "with the wheel at its zero, or drop the entry and the wheel "
              "column stays blank.")
        return EXIT_REFUSED

    run_s = args.warmup + args.samples * args.interval
    print(f"corner {args.corner}: steer id {steer_id} on {iface} ({product}), "
          f"CANcoder {cc_id} on {bus_name}")
    if ratio:
        print(f"one wheel revolution should read about {float(ratio):.2f} "
              "motor rotations")
    else:
        print("cancoder.steer_gear_ratio is unset, so the expected travel goes "
              "unchecked. Add it to compare the two.")
    if offset is None:
        print(f"cancoder.offsets_deg has no entry for {args.corner}, so the "
              "wheel angle column stays blank. The check runs on raw angles.")
    print(f"The {args.corner} steer controller alone is enabled, at zero duty, "
          f"for about {run_s:.0f} s. It is the only motor this touches, and the "
          "zero duty frame is the only setpoint it sends.")
    try:
        answer = input("Robot on a stand, hands clear of the wheel until the "
                       "table starts? [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        answer = ""
    if answer != "y":
        print("\nREFUSED: not confirmed, so nothing was enabled.\n  FIX: answer "
              "y at the prompt, on a terminal this check can read.")
        return EXIT_REFUSED

    encoder = cancoder.open_encoder(cc_id, bus_name)
    angle_sig = encoder.get_absolute_position()

    motor = None
    rows = []
    interrupted = False

    def stop(*_):
        """Zero the output, then let the run unwind through the finally."""
        if motor is not None:
            motor.percent_output(0.0)
        raise KeyboardInterrupt

    bus = SparkBus(channel=iface)
    try:
        signal.signal(signal.SIGINT, stop)
        # keeps sticky faults latched for whoever reads them next
        motor = bus.init_controller(steer_id, product, clear_sticky_faults=False)
        motor.percent_output(0.0)
        bus.wait_for_heartbeat(timeout=2.0)
        time.sleep(args.warmup)

        if not motor.is_live(LIVE_WINDOW_S):
            print(f"REFUSED: SPARK {steer_id} has sent nothing on {iface} in the "
                  f"last {LIVE_WINDOW_S:.1f} s, so every position below would be "
                  "the zero this process started with.\n  FIX: check that the "
                  "controller has motor power and is on this bus with `spark "
                  "status`, then re-run.")
            return EXIT_REFUSED

        first_raw = _read_raw_deg(angle_sig)
        if not _number(first_raw):
            print(f"REFUSED: CANcoder {cc_id} on {bus_name} answered "
                  f"{first_raw}, so the angle it reports is a default and not a "
                  "measurement.\n  FIX: check the encoder is powered and on that "
                  "bus, then re-run. tools/cancoder_audit.py reads the rest.")
            return EXIT_REFUSED
        try:
            cancoder.warmup([(args.corner, angle_sig)], verbose=False)
        except TimeoutError as err:
            print(f"WARNING: {err}")
            print("Carrying on, because the encoder is reporting. Hold the wheel "
                  "harder for the still phase, and read the angle column as "
                  "noisier than usual.")

        print(f"\n>>> HOLD the {args.corner} steer wheel STILL for samples "
              f"0-{args.hold - 1} <<<")
        print(">>> then HAND-ROTATE it about one full wheel revolution for "
              f"samples {args.hold}-{args.samples - 1} <<<\n")
        print(f"{'i':>2}  {'position(rot)':>14}  {'cancoder(deg)':>14}  "
              f"{'wheel(deg)':>11}")
        for i in range(args.samples):
            pos = _read_position(motor)
            raw = _read_raw_deg(angle_sig)
            age = motor.seconds_since_seen()
            wheel = (cancoder.wheel_angle_deg(raw, offset)
                     if offset is not None and _number(raw) else "-")
            rows.append((pos, raw, float("inf") if age is None else age))
            print(f"{i:>2}  {_fmt(pos, 5):>14}  {_fmt(raw, 2):>14}  "
                  f"{_fmt(wheel, 2):>11}")
            if i == args.hold - 1:
                print("  --- now start turning the wheel ---")
            if i < args.samples - 1:
                time.sleep(args.interval)
    except KeyboardInterrupt:
        interrupted = True
        print("\nstopped")
    finally:
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        if motor is not None:
            try:
                motor.percent_output(0.0)
            except Exception:           # noqa: BLE001 - shutdown zeroes again
                pass
        bus.shutdown()
        signal.signal(signal.SIGINT, signal.SIG_DFL)

    generation = getattr(motor, "_observed_generation", None)
    expected = "fw25+" if product == SPARK_FLEX else "pre25"
    if generation and generation != expected:
        print(f"\nNOTE: frames arrived in the {generation} layout while "
              f"controller_type says {product}, whose layout is {expected}. "
              "sparklib decoded what the wire showed, so fix controller_type "
              "before the next tool reads it the declared way.")

    if not rows:
        print("\nno samples were taken, so nothing here says anything about the "
              "decode.")
        return EXIT_UNPROVEN

    r = analyse(rows, args.hold, args.still_tol_rot, ratio, args.ratio_tol)
    print(f"\n  samples       {r['samples']} of {args.samples}, {r['still_samples']} "
          f"held and {r['turn_samples']} turning")
    print(f"  still phase   position spread {r['still_spread']:.4f} rot")
    print(f"  turning       wheel {r['wheel_travel']:.1f} deg, position "
          f"{r['pos_travel']:.3f} rot, {r['reversals']} reversal(s)")
    if r["measured_ratio"] is not None:
        print(f"  measured      {r['measured_ratio']:.2f} motor rotations per "
              "wheel revolution")

    if interrupted:
        print(f"\nINTERRUPTED after {r['samples']} of {args.samples} samples.")
        if r["samples"] <= args.hold:
            print(f"The turning phase never ran, so samples {args.hold}-"
                  f"{args.samples - 1} were never taken and the decode is "
                  "untested. Re-run and let it finish.")
        else:
            print(f"The turning phase took {r['samples'] - args.hold} of its "
                  f"{args.samples - args.hold} samples, which proves nothing "
                  "unless the wheel covered a full revolution inside them.")

    if r["problems"]:
        print("\nproblems:")
        for x in r["problems"]:
            print(f"  - {x}")

    if interrupted:
        return EXIT_UNPROVEN

    if r["problems"]:
        print("\nTreat the decode as unproven until these clear. A steer loop "
              "seeded from a position field that is not position drives "
              "confidently to the wrong angle.")
        return EXIT_UNPROVEN
    print("\ndecode looks right: still while held, monotonic while turning, and "
          "the travel matches the configured gear ratio.")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
