"""Command one steer corner to a fixed wheel angle and score how it converged.

    uv run python tools/steer_pid_check.py --corner LF --dry-run
    uv run python tools/steer_pid_check.py --corner LF --target-deg 0 --duration 4
    uv run python tools/steer_pid_check.py --corner LF --target-deg 45 --mode host

Holds one target for a fixed time, logs the CANcoder wheel angle at the control
rate, then prints steady-state error, overshoot, settling time and a limit-cycle
count. Run it before and after a gain change and the change has a number against
it instead of an impression.

The loop closes in one of two places and controller_type picks the default,
which --mode overrides. "onboard" feeds the controller an absolute position
setpoint, built from its own reported position plus the error, so the gain REV
Hardware Client writes and `spark params --param 13` reads back is the one under
test. "host" closes the loop here through sparklib.steer.pd_output, the same
arithmetic a live host-side loop runs, so a gain that scores well transfers
instead of being re-derived. --kd stays at zero unless you ask for it, which
makes the host loop proportional by default. docs/STEER-CONTROL.md covers the
choice.

Three degrees of wheel-angle error is what a production swerve loop calls
converged, and 20 Hz is a navigation control rate. A loop scored at a rate it
never runs at is a different loop, so match --rate-hz to the rate that will
drive it.

MOVES A STEER MOTOR. The heartbeat enables the controller and every tick sends
it a setpoint. Put the robot on a stand and chock the wheels. Only the named
corner's steer motor is opened, its drive motor is left alone, and the output is
zeroed on every exit path including Ctrl-C.

Both devices are checked before the run, because each one fails quietly. A
CANcoder that has stopped reporting still answers with a plausible angle, so the
signal status is read before the run and again on every tick. A SPARK that sends
no status frame reports position zero, which the onboard loop would take for a
measurement and turn into a move to an angle nobody asked for.

Exit codes: 0 converged, 1 the loop failed or came out marginal, 2 refused with
nothing commanded except zero, 130 interrupted part way through.

Needs the swerve extra: `uv sync --extra swerve`, then either export
UV_NO_SYNC=1 or run it as `uv run --extra swerve ...`, because a bare
`uv run` re-syncs to the default extras and removes phoenix6 again.
"""

import argparse
import math
import sys
import time

from sparklib import SparkBus, SPARK_FLEX, SPARK_MAX
from sparklib import cancoder, steer
from sparklib import config as cfg

TOL_DEG = 3.0
SNAP_TOL_DEG = 0.6
SETTLE_WAIT_S = 1.0
MAX_RATE_HZ = 200.0
MIN_SAMPLES = 4
ID_MIN = 1
ID_MAX = 62
REFUSED = 2
INTERRUPTED = 130


def finite(value):
    """True for a real finite number."""
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


def device_id(value):
    """A CAN id from the config, or None when it is not one."""
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        ident = int(str(value).strip())
    except ValueError:
        return None
    return ident if ID_MIN <= ident <= ID_MAX else None


def group_labels(group):
    """Labels of a devices group in config order, or None when it is not a group."""
    if group is None or not hasattr(group, "__dict__"):
        return None
    return list(vars(group))


def snap_target(target_deg):
    """Snap a near-cardinal target onto the cardinal angle."""
    t = steer.normalize_deg(target_deg)
    if abs(abs(t) - 180.0) < SNAP_TOL_DEG:
        return 180.0
    if abs(abs(t) - 90.0) < SNAP_TOL_DEG:
        return 90.0 if t > 0 else -90.0
    return t


def encoder_fault(encoder):
    """The status code name when the absolute signal is unhealthy, else None."""
    status = getattr(encoder.get_absolute_position(), "status", None)
    is_ok = getattr(status, "is_ok", None)
    if is_ok is None or is_ok():
        return None
    return getattr(status, "name", str(status))


def wait_for_encoder(encoder, timeout_s=2.0):
    """Poll until the absolute signal reports, and return the last fault name."""
    deadline = time.time() + timeout_s
    fault = encoder_fault(encoder)
    while fault is not None and time.time() < deadline:
        time.sleep(0.05)
        fault = encoder_fault(encoder)
    return fault


def read_wheel_deg(encoder, offset):
    """Wheel angle in the robot frame, from a signal that is really reporting."""
    fault = encoder_fault(encoder)
    if fault is not None:
        raise RuntimeError(f"CANcoder signal {fault}")
    raw = cancoder.read_angle_deg(encoder, settle=False)
    if not finite(raw):
        raise RuntimeError(f"CANcoder angle {raw!r}")
    return cancoder.wheel_angle_deg(raw, offset)


def zero_output(motor):
    """Command zero duty, swallowing a bus error on the way out."""
    if motor is None:
        return
    try:
        motor.percent_output(0.0)
    except Exception:                   # noqa: BLE001 - already on the exit path
        pass


def check_args(args):
    """A refusal message for a flag that must not reach the motor, else None."""
    for name in ("target_deg", "duration", "rate_hz", "kp", "kd", "max_output",
                 "deadband", "tol_deg"):
        if not finite(getattr(args, name)):
            flag = "--" + name.replace("_", "-")
            return (f"REFUSED: {flag} is not a finite number.\n"
                    "  FIX: pass a plain decimal value.")
    if args.duration <= 0:
        return (f"REFUSED: --duration {args.duration:g} holds the target for no "
                "time, so there is nothing to score.\n"
                "  FIX: pass a duration above 0; the default is 4.")
    if not 0 < args.rate_hz <= MAX_RATE_HZ:
        return (f"REFUSED: --rate-hz {args.rate_hz:g} is outside 0 to "
                f"{MAX_RATE_HZ:g}. A rate of 0 divides by zero once the motor "
                f"is enabled, and one above {MAX_RATE_HZ:g} floods the bus the "
                "enable heartbeat shares.\n"
                "  FIX: pass the rate your navigation loop runs at, such as 20.")
    if args.duration * args.rate_hz < MIN_SAMPLES:
        return (f"REFUSED: {args.duration:g} s at {args.rate_hz:g} Hz gives fewer "
                f"than the {MIN_SAMPLES} samples the metrics need.\n"
                "  FIX: raise --duration or --rate-hz until their product "
                f"reaches {MIN_SAMPLES}.")
    if not 0 < args.tol_deg <= 180:
        return (f"REFUSED: --tol-deg {args.tol_deg:g} is outside 0 to 180, so no "
                "reading could ever count as converged.\n"
                f"  FIX: pass a tolerance in degrees; the default is {TOL_DEG:g}.")
    if args.kp <= 0:
        return (f"REFUSED: --kp {args.kp:g} is not a positive gain, so the host "
                "loop would either sit still or drive away from the target.\n"
                "  FIX: pass a positive duty per degree of error.")
    if args.kd < 0:
        return (f"REFUSED: --kd {args.kd:g} is negative, which amplifies the "
                "motion it exists to damp.\n"
                "  FIX: pass 0 or a positive value.")
    if not 0 < args.max_output <= 1.0:
        return (f"REFUSED: --max-output {args.max_output:g} is outside 0 to 1, "
                "and duty reaches the SPARK unclamped.\n"
                "  FIX: pass a duty ceiling in that range; the default is 0.40.")
    if not 0 <= args.deadband < 180:
        return (f"REFUSED: --deadband {args.deadband:g} is outside 0 to 180, so "
                "the loop would never command anything.\n"
                "  FIX: pass the degrees of error you want treated as arrived.")
    return None


def run(motor, encoder, offset, target_deg, args, ratio, host, samples):
    """Hold the target for the duration, one (t, wheel, err, cmd) row per tick."""
    period = 1.0 / args.rate_hz
    t0 = time.perf_counter()
    deadline = t0 + args.duration
    unit = "cmd(duty)" if host else "cmd(rot)"
    prev_err = None
    prev_t = None
    print(f"\n{'t(s)':>6} {'wheel':>9} {'err':>9} {unit:>11}")
    while time.perf_counter() < deadline:
        tick = time.perf_counter()
        try:
            wheel = read_wheel_deg(encoder, offset)
            err = steer.shortest_error_deg(target_deg, wheel)
            if host:
                derr = 0.0
                if args.kd and prev_err is not None and 1e-4 < tick - prev_t < 0.5:
                    derr = (err - prev_err) / (tick - prev_t)
                prev_err, prev_t = err, tick
                cmd = steer.pd_output(err, derr, args.kp, args.kd,
                                      args.max_output, args.deadband)
                motor.percent_output(cmd)
            else:
                cmd = motor.position + cancoder.motor_rotations(err, ratio)
                motor.position_output(cmd)
        except Exception as exc:        # noqa: BLE001 - one bad tick is data
            wheel = err = cmd = float("nan")
            # drops the duty a failed tick would otherwise leave running
            if host:
                zero_output(motor)
            print(f"  tick error: {type(exc).__name__}: {exc}")
        t = tick - t0
        samples.append((t, wheel, err, cmd))
        print(f"{t:6.2f} {wheel:9.2f} {err:9.2f} {cmd:11.3f}")
        rest = period - (time.perf_counter() - tick)
        if rest > 0:
            time.sleep(rest)


def report(samples, target_deg, tol, complete=True):
    """Print the convergence metrics and a verdict. Returns an exit code."""
    valid = [s for s in samples if finite(s[2])]
    if len(valid) < MIN_SAMPLES:
        if not complete:
            print(f"\n{len(valid)} of the {MIN_SAMPLES} samples the metrics need "
                  "were logged before the interrupt, so there is nothing to "
                  "score here.")
            return INTERRUPTED
        print(f"\nREFUSED: {len(valid)} of {len(samples)} samples were usable and "
              f"scoring needs {MIN_SAMPLES}.\n"
              "  FIX: confirm the CANcoder reports and the SPARK bus is up, with "
              "tools/cancoder_audit.py and tools/spark_passive_snapshot.py, then "
              "run this again.")
        return REFUSED
    errs = [e for _, _, e, _ in valid]
    n = len(errs)
    tail = errs[max(0, n - max(MIN_SAMPLES, n // 4)):]
    ss_err = sum(abs(e) for e in tail) / len(tail)
    peak_to_peak = max(tail) - min(tail)
    max_abs = max(abs(e) for e in errs)
    e0 = errs[0]
    overshoot = max(0.0, max(((-e if e0 >= 0 else e) for e in errs), default=0.0))

    settle_t = None
    for i, (t, _, _, _) in enumerate(valid):
        if all(abs(e) < tol for (_, _, e, _) in valid[i:]):
            settle_t = t
            break

    sign_changes = sum(
        1 for a, c in zip(tail, tail[1:]) if (a > 0) != (c > 0) and abs(a) > 1.0
    )

    print("\n========== steer-pid report ==========")
    print(f" target               : {target_deg:+.1f} deg")
    print(f" run                  : "
          f"{'complete' if complete else 'INTERRUPTED part way through'}")
    print(f" samples scored       : {n} of {len(samples)} logged")
    print(f" steady-state |err|   : {ss_err:6.2f} deg   (want < {tol} deg)")
    print(f" tail peak-to-peak    : {peak_to_peak:6.2f} deg   (want small)")
    print(f" max |err| seen       : {max_abs:6.2f} deg")
    print(f" overshoot            : {overshoot:6.2f} deg")
    print(f" settling time        : "
          f"{f'{settle_t:.2f} s' if settle_t is not None else 'NEVER (did not settle)'}")
    print(f" tail sign-changes    : {sign_changes}   (>=3 means limit cycle)")

    # An axis that began inside the tolerance was never asked to move, so every
    # number above describes a wheel sitting still. Converging from nowhere is
    # not evidence that the loop converges.
    started_there = abs(e0) < tol

    print(" verdict              : ", end="")
    if not complete:
        print("INCOMPLETE - the hold was cut short, so these numbers cover "
              "only the part that ran")
        code = INTERRUPTED
    elif started_there:
        print(f"INCONCLUSIVE - the wheel began {abs(e0):.2f} deg from the "
              f"target, inside the {tol} deg tolerance, so it had nowhere to "
              "go and this run says nothing about the loop")
        print(f"   re-run with a target well away from {target_deg:+.1f}, for "
              f"example --target-deg {target_deg - 60:+.0f}")
        code = 1
    elif settle_t is not None and ss_err < tol and sign_changes < 3:
        print("PASS - converged and held")
        code = 0
    elif sign_changes >= 3 or peak_to_peak > 4 * tol:
        print("FAIL - oscillating, so the loop is too hot or undamped")
        code = 1
    elif ss_err >= tol and max_abs > tol:
        print("FAIL - no or weak response, so the proportional gain is near zero")
        code = 1
    else:
        print("MARGINAL - see the numbers above")
        code = 1
    print("======================================")
    return code


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--corner", help="a label from devices.steer, or omit on a "
                                    "rig with exactly one")
    p.add_argument("--target-deg", type=float, default=0.0,
                   help="target wheel angle in degrees, robot frame")
    p.add_argument("--duration", type=float, default=4.0,
                   help="seconds to hold the target and log")
    p.add_argument("--rate-hz", type=float, default=20.0,
                   help="control and log rate; match your navigation loop rate")
    p.add_argument("--mode", choices=["onboard", "host"],
                   help="where the loop closes; defaults from controller_type")
    p.add_argument("--kp", type=float, default=0.008,
                   help="host mode: duty per degree of error")
    p.add_argument("--kd", type=float, default=0.0,
                   help="host mode: duty per degree per second, 0 for a P loop")
    p.add_argument("--max-output", type=float, default=0.40,
                   help="host mode: duty ceiling, which is a current limit in disguise")
    p.add_argument("--deadband", type=float, default=0.1,
                   help="host mode: degrees of error below which output is zero")
    p.add_argument("--tol-deg", type=float, default=TOL_DEG,
                   help="wheel-angle error a converged loop stays inside")
    p.add_argument("--interface", help="SPARK bus; defaults to can.interface")
    p.add_argument("--dry-run", action="store_true",
                   help="print what it resolved and command nothing")
    args = p.parse_args(argv)

    bad_flag = check_args(args)
    if bad_flag:
        print(bad_flag)
        return REFUSED

    if not cancoder.available():
        print("REFUSED: scoring a steer loop reads the absolute encoder.")
        print(cancoder.EXTRA_HINT)
        return REFUSED

    cfg.load_host()
    conf = cfg.get()
    devices = getattr(conf, "devices", None)
    cc_conf = getattr(conf, "cancoder", None)
    steer_group = getattr(devices, "steer", None)
    cc_group = getattr(devices, "cancoder", None)
    labels = group_labels(steer_group)
    cc_labels = group_labels(cc_group)
    missing = []
    if labels is None:
        missing.append("devices.steer, the steer motor ids")
    if cc_labels is None:
        missing.append("devices.cancoder, the absolute encoder ids")
    if cc_conf is None:
        missing.append("a top-level cancoder block holding bus, "
                       "steer_gear_ratio and offsets_deg")
    else:
        for key, what in (("bus", "the netdev the encoders are on"),
                          ("steer_gear_ratio", "motor turns per axis turn"),
                          ("offsets_deg", "each corner's wheel-zero reading")):
            if getattr(cc_conf, key, None) is None:
                missing.append(f"cancoder.{key}, {what}")
    if missing:
        print("REFUSED: this config is missing what a steer check reads:")
        for item in missing:
            print(f"    {item}")
        print("  FIX: sparklib/data/spark.yaml carries a worked example of "
              "each. Record the offsets with tools/cancoder_calibrate.py, and "
              "set steer_gear_ratio from your own module's page: an MK5i is "
              "26.0 and an MK2 is 12.8.")
        return REFUSED
    if not labels:
        print("REFUSED: devices.steer is empty, so there is no corner to score.\n"
              "  FIX: add a label and its CAN id under devices.steer, such as "
              "LF: 2.")
        return REFUSED

    product_name = getattr(conf, "controller_type", None)
    if product_name not in (SPARK_FLEX, SPARK_MAX):
        print("REFUSED: config has no usable controller_type.\n"
              f"  FIX: set controller_type: {SPARK_FLEX} or controller_type: "
              f"{SPARK_MAX}.")
        return REFUSED

    corner = args.corner
    if corner is None:
        if len(labels) != 1:
            print(f"REFUSED: devices.steer holds {len(labels)} corners "
                  f"({', '.join(labels)}), so one has to be named.\n"
                  "  FIX: pass --corner with one of those labels.")
            return REFUSED
        corner = labels[0]

    steer_id = device_id(getattr(steer_group, corner, None))
    cc_id = device_id(getattr(cc_group, corner, None))
    offset = getattr(getattr(cc_conf, "offsets_deg", None), corner, None)
    bus_name = getattr(cc_conf, "bus", None)
    missing = []
    if steer_id is None:
        missing.append(f"devices.steer.{corner}, a CAN id from {ID_MIN} to "
                       f"{ID_MAX}")
    if cc_id is None:
        missing.append(f"devices.cancoder.{corner}, a CAN id from {ID_MIN} to "
                       f"{ID_MAX}")
    if not finite(offset):
        missing.append(f"cancoder.offsets_deg.{corner}, in degrees, which "
                       "tools/cancoder_calibrate.py measures")
    if not isinstance(bus_name, str) or not bus_name.strip():
        missing.append("cancoder.bus, the netdev the CANcoders came up on")
    if missing:
        print(f"REFUSED: corner {corner!r} is not configured for this check. "
              f"Corners under devices.steer: {', '.join(labels)}.\n"
              "  FIX: add to the config:\n       " + "\n       ".join(missing))
        return REFUSED

    iface = args.interface or getattr(getattr(conf, "can", None), "interface", None)
    if not isinstance(iface, str) or not iface.strip():
        print("REFUSED: no SPARK bus to open.\n"
              "  FIX: set can.interface in the config, or pass --interface.")
        return REFUSED

    product = SPARK_FLEX if product_name == SPARK_FLEX else SPARK_MAX
    mode = args.mode or ("host" if product == SPARK_FLEX else "onboard")
    host = mode == "host"
    ratio = getattr(cc_conf, "steer_gear_ratio", None)
    if not host:
        try:
            ratio = float(ratio)
        except (TypeError, ValueError):
            ratio = None
        if ratio is None or not finite(ratio) or ratio <= 0:
            print("REFUSED: the onboard loop turns wheel degrees into motor "
                  "rotations, and cancoder.steer_gear_ratio is missing or is not "
                  "a positive number.\n"
                  "  FIX: set steer_gear_ratio to this module's steer reduction, "
                  "or score the host loop with --mode host.")
            return REFUSED

    target = snap_target(args.target_deg)
    enabled_s = SETTLE_WAIT_S + args.duration
    print(f"corner {corner}: steer id {steer_id} on {iface}, CANcoder {cc_id} on "
          f"{bus_name}, offset {offset:+.2f} deg")
    print(f"{product_name}, {mode} loop"
          + (f", kp {args.kp}, kd {args.kd}, max_output {args.max_output}, "
             f"deadband {args.deadband} deg" if host
             else f", {ratio:g}:1 steer ratio"))
    print(f"target {target:+.1f} deg, held {args.duration:.1f} s at "
          f"{args.rate_hz:.0f} Hz, converged means |err| < {args.tol_deg} deg")
    if abs(target - args.target_deg) > 1e-9:
        print(f"  --target-deg {args.target_deg:+.2f} snapped to the cardinal "
              f"{target:+.1f}")
    if host and args.deadband >= args.tol_deg:
        print(f"  WARNING: the {args.deadband} deg deadband is wider than the "
              f"{args.tol_deg} deg tolerance, so the loop stops correcting while "
              "the error is still outside tolerance.")
    print(f"MOVES ONE MOTOR: steer id {steer_id} ({corner}) turns that wheel to "
          f"{target:+.1f} deg and holds it"
          + (f", at up to {args.max_output * 100:.0f} percent duty" if host else "")
          + f". It stays enabled for about {enabled_s:.1f} s, which is "
          f"{SETTLE_WAIT_S:.1f} s of settling and then {args.duration:.1f} s of "
          f"setpoints at {args.rate_hz:.0f} Hz. The {corner} drive motor is never "
          "opened, and no other controller is commanded.")
    if args.dry_run:
        print("dry run: nothing was opened and no frame was sent.")
        return 0

    encoder = cancoder.open_encoder(cc_id, bus_name)
    fault = wait_for_encoder(encoder)
    if fault is not None:
        print(f"REFUSED: CANcoder {cc_id} on {bus_name} answers {fault}, so its "
              "angle would be a default value and not a measurement. Every "
              "number below it would look plausible and mean nothing.\n"
              f"  FIX: check the id against devices.cancoder and confirm "
              f"{bus_name} is up. If the CAN layer logged 'Firmware Too Old' or "
              "a Phoenix version mismatch, that is the cause and the magnet is "
              "not: CTRE require the device firmware major to match the "
              "phoenix6 major, so install the phoenix6 that matches these "
              "encoders or field upgrade them. tools/cancoder_audit.py reports "
              "the device.")
        return REFUSED

    try:
        answer = input("Wheel free to turn, robot on a stand? [y/N] ")
    except (EOFError, KeyboardInterrupt):
        print("\nREFUSED: the run went unconfirmed, so no frame was sent.\n"
              "  FIX: answer y at the prompt once the wheel is free to turn.")
        return REFUSED
    if answer.strip().lower() != "y":
        print("REFUSED at the prompt: no frame was sent.\n"
              "  FIX: answer y once the wheel is free to turn.")
        return REFUSED

    samples = []
    interrupted = False
    motor = None
    bus = None
    try:
        # SparkBus starts its enable heartbeat on construction, so it is built
        # inside the try that shuts it down.
        bus = SparkBus(channel=iface)
        motor = bus.init_controller(steer_id, product, clear_sticky_faults=True)
        # zeroes the setpoint before the enable heartbeat reaches the controller
        motor.percent_output(0.0)
        bus.wait_for_heartbeat(timeout=2.0)
        time.sleep(SETTLE_WAIT_S)
        if motor.seconds_since_seen() is None:
            print(f"REFUSED: SPARK {steer_id} has sent no status frame on "
                  f"{iface}, so its position reads as zero and the loop would be "
                  "scored against a number no sensor produced.\n"
                  "  FIX: check the id, the motor power and the wiring, then "
                  "re-run. tools/spark_passive_snapshot.py lists what the bus "
                  "carries.")
            return REFUSED
        run(motor, encoder, offset, target, args, ratio, host, samples)
    except KeyboardInterrupt:
        interrupted = True
    finally:
        try:
            zero_output(motor)
            time.sleep(0.05)
        finally:
            if bus is not None:
                bus.shutdown()

    if interrupted:
        held = samples[-1][0] if samples else 0.0
        print(f"\ninterrupted: the {corner} wheel was commanded to {target:+.1f} "
              f"deg for {held:.1f} s of the {args.duration:.1f} s asked for, and "
              "the output is now zero. Settling time and steady-state error "
              "describe a hold that never finished, so re-run before reading "
              "either as a result.")
    code = report(samples, target, args.tol_deg, complete=not interrupted)
    return INTERRUPTED if interrupted else code


if __name__ == "__main__":
    sys.exit(main())
