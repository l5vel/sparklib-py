"""Repeatability and stress test for swerve steer corners, logged to one CSV.

    uv run python tools/steer_stress_test.py --label locked
    uv run python tools/steer_stress_test.py --corner RF --label spin --spin-seconds 30
    uv run python tools/steer_stress_test.py --label spin --spin-output 1.0 --drive-output 0.3
    uv run python tools/steer_stress_test.py --summary

Asks whether a corner comes back to the same wheel angle after it has been
stressed, and separates an encoder that drifts from mechanics that slip. Every
run appends to one CSV and --summary reads the whole log back, because a single
reading cannot tell a drifting corner from a noisy one.

Three labels:

  locked   passive CANcoder read with the wheel held still mechanically. No
           motor is commanded. Repeat it across power cycles, and the spread
           across runs is the encoder's own repeatability.
  spin     drive to a target, spin hard, drive back to the same target, and
           record the converged reading on each side. With --drive-output above
           zero the drive motors run during the spin, which stresses the corner
           the way the robot stresses it.
  ad-hoc   passive read under its own label, for manual probing.

MOVES MOTORS UNDER --label spin. It turns every named steer motor at up to full
duty for the whole spin phase, and --drive-output also turns the drive motors,
which rolls the robot unless the wheels are off the ground. The confirmation
names every motor that will move, its duty, and the worst case time under power,
then waits for a typed y. Every flag and every id is checked before that
question, so a bad number never reaches an enabled controller. Corners are
walked in the order the config lists them, which is the order a person walks
around the robot.

The bus is opened inside the try whose finally zeroes every output and shuts it
down, and the controllers are created inside it, so from the moment anything is
enabled a Ctrl-C or a failure has something to stop the motors. An interrupted
run reports which phases it reached and exits non-zero.

A silent CANcoder is refused instead of measured. phoenix6 answers a missing
device with a default value and a bad status code rather than raising, so an
encoder that is not on the bus reads as a plausible angle and would log as one.

Settling is tunable because one rule can deadlock. --settle-mode tol waits for
|error| below a tolerance, so a deadband wider than that tolerance commands zero
before the error is ever small enough to accept and the move never finishes. The
default, static, accepts once the readings stop moving and sidesteps it.
--fine-converge then runs a second low-gain loop with a much smaller deadband,
so the wheel parks visibly at zero, and gives up per corner where stiction holds
one. A phase that never converged is recorded as such, because the wheel is then
somewhere other than the target and its delta measures the approach.

Two measurements shape the defaults. The CANcoder broadcast rate is raised to
500 Hz because phoenix6 defaults to about 100 Hz, and a 10 ms control loop then
reads frames up to a full period old, which appears as jumps at high RPM. A loop
tick longer than 12 ms is a host-scheduling stall, from the GIL or from USB
latency on the CANcoder link, so those ticks are counted separately and not read
as an encoder fault.

Exit codes: 0 when everything asked for was measured, 1 when the operator
declined or interrupted a run or a phase never converged, and 2 for a refusal.

Needs the swerve extra: `uv sync --extra swerve`, then either export
UV_NO_SYNC=1 or run it as `uv run --extra swerve ...`, because a bare
`uv run` re-syncs to the default extras and removes phoenix6 again.
"""

import argparse
import csv
import os
import statistics
import sys
import time
from collections import deque

from sparklib import SparkBus, SPARK_FLEX, SPARK_MAX
from sparklib import cancoder, netdev, steer
from sparklib import config as cfg

N_SAMPLES = 50
SAMPLE_DT = 0.02
WARMUP_SAMPLES = 20

SPIN_FLIP_PERIOD = 2.0
SPIN_LOOP_DT = 0.01

SETTLE_ERR_DEG = 0.5
SETTLE_HOLD_S = 1.0
DRIVE_TIMEOUT_S = 15.0

# A window this short holds too few samples to tell a stopped wheel from a slow one.
MIN_SETTLE_WINDOW_S = 5 * SPIN_LOOP_DT

# Ticks with no fresh CANcoder frame before a closed loop stops commanding.
SIGNAL_LOSS_TICKS = 50

# Threshold scales with speed; the floor catches a jump at a standstill.
SPIN_JUMP_FLOOR_DEG = 15.0
SPIN_JUMP_FACTOR = 4.0
LOAD_DERATE = 0.8
FREE_RPM_DEFAULT = 6700.0
CANCODER_RATE_HZ = 500.0
LONG_TICK_S = 0.012

DRIFT_BUDGET_DEG = 0.3
LARGE_DRIFT_DEG = 5.0

REFUSED = 2
CAN_ID_MAX = 62

FIELDS = [
    "timestamp", "epoch", "rig_name", "corner", "label", "n",
    "mean_deg", "stdev_deg", "jitter_deg", "raw_rev", "wheel_deg",
    "magnet", "sticky_hex",
    "phase", "run_id", "target_deg", "spin_seconds", "spin_output",
    "jump_count", "max_jump_deg",
    "long_tick_count", "max_tick_dt_ms", "spin_jump_threshold_deg",
    "drive_output", "drive_pattern", "drive_max_applied",
    "drive_pre_sticky", "drive_post_sticky",
    "settle_mode", "settle_window_s", "settle_motion_deg",
    "kp", "max_output", "deadband_deg", "converged",
    "settle_s", "overshoot_deg", "final_error_deg", "saturated_frac",
]

_PHOENIX = None


def _refresh(signals):
    """Refresh every CANcoder signal in one call, through sparklib's import."""
    global _PHOENIX
    if _PHOENIX is None:
        _PHOENIX = cancoder._phoenix()
    return _PHOENIX.BaseStatusSignal.refresh_all(*signals)


def _signal_ok(sig):
    """True when the last refresh of this signal actually carried a frame."""
    status = getattr(sig, "status", None)
    is_ok = getattr(status, "is_ok", None)
    return True if is_ok is None else bool(is_ok())


def _signal_status(sig):
    """Status code of the last refresh, for a message about a quiet device."""
    status = getattr(sig, "status", None)
    return "unknown" if status is None else getattr(status, "name", str(status))


def _expected_deg_per_tick(spin_output, free_rpm, gear_ratio):
    """Wheel motion in one spin tick at this duty, derated for load."""
    motor_rpm = free_rpm * spin_output * LOAD_DERATE
    wheel_deg_per_sec = (motor_rpm / 60.0) / gear_ratio * 360.0
    return wheel_deg_per_sec * SPIN_LOOP_DT


def _spin_jump_threshold(spin_output, free_rpm, gear_ratio):
    """Per-tick motion that counts as anomalous at this commanded speed."""
    expected = _expected_deg_per_tick(spin_output, free_rpm, gear_ratio)
    return max(SPIN_JUMP_FLOOR_DEG, SPIN_JUMP_FACTOR * expected)


def _set_cancoder_rate(sig, hz):
    """Raise one CANcoder's broadcast rate, or carry on at the default."""
    try:
        status = sig.set_update_frequency(hz)
        ok = status.is_ok() if hasattr(status, "is_ok") else True
        if ok:
            print(f"[init] CANcoder broadcast rate -> {hz:.0f} Hz")
        else:
            print(f"[init] WARNING: set_update_frequency returned {status}")
    except Exception as err:            # noqa: BLE001 - optional on older wrappers
        print(f"[init] WARNING: could not set the update frequency ({err}); "
              "continuing at the default rate")


# ---------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------

def _append_rows(path, new_rows):
    """Append rows, rewriting the file when its header predates a column."""
    existing = []
    if os.path.exists(path):
        with open(path) as fh:
            existing = list(csv.DictReader(fh))
    tmp = f"{path}.tmp"
    with open(tmp, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS, extrasaction="ignore")
        writer.writeheader()
        for row in existing + new_rows:
            writer.writerow({k: row.get(k, "") for k in FIELDS})
    os.replace(tmp, path)


def _safe_float(text, default=0.0):
    try:
        return float(text)
    except (ValueError, TypeError):
        return default


# ---------------------------------------------------------------------------
# Reading one corner
# ---------------------------------------------------------------------------

def _read_magnet_and_sticky(encoder):
    """Magnet health and sticky-fault field of one CANcoder, best effort."""
    try:
        health = str(encoder.get_magnet_health().value)
    except Exception:                   # noqa: BLE001 - optional signal
        health = "unknown"
    try:
        sticky_hex = f"0x{int(encoder.get_sticky_fault_field().value):08X}"
    except Exception:                   # noqa: BLE001 - optional signal
        sticky_hex = "?"
    return health, sticky_hex


def _read_spark_sticky(spark):
    """Sticky-fault mask of one SPARK as hex, or '?' when nothing decoded."""
    try:
        mask = spark.sticky_faults
    except Exception:                   # noqa: BLE001 - no frame yet
        return "?"
    return "?" if mask is None else f"0x{mask:04X}"


def _wheel_deg(sig, offset_deg):
    """Wheel angle from a signal the caller already refreshed this tick."""
    return cancoder.wheel_angle_deg(sig.value * 360.0, offset_deg)


def _sample_cancoder(sig, n=N_SAMPLES, dt=SAMPLE_DT):
    """n absolute readings at dt apart, and whether any frame in them was fresh.

    phoenix6 answers a refresh with no new frame the same way it answers a
    device that is not there, so freshness is judged over the whole window and
    never off one tick.
    """
    samples, fresh = [], False
    for _ in range(n):
        _refresh([sig])
        fresh = fresh or _signal_ok(sig)
        samples.append(sig.value * 360.0)
        time.sleep(dt)
    return samples, fresh


def _summarize(samples):
    """Mean, population stdev and peak-to-peak jitter of one sample window.

    Every sample is measured as a wrapped difference from the first, so a corner
    parked on the encoder's own discontinuity reports the jitter it has instead
    of a full turn of it. The mean stays on the 0 to 360 scale the log uses.
    """
    anchor = samples[0]
    deltas = [steer.normalize_deg(s - anchor) for s in samples]
    mean = (anchor + sum(deltas) / len(deltas)) % 360.0
    stdev = statistics.pstdev(deltas) if len(deltas) > 1 else 0.0
    return mean, stdev, max(deltas) - min(deltas)


def _spread_deg(values):
    """Peak-to-peak spread and stdev of angles, measured the same wrapped way."""
    deltas = [steer.normalize_deg(v - values[0]) for v in values]
    stdev = statistics.pstdev(deltas) if len(deltas) > 1 else 0.0
    return max(deltas) - min(deltas), stdev


def record_one(label, name, cc_id, bus_name, offset, log_path, rig_name,
               rate_hz=CANCODER_RATE_HZ):
    """One passive read of a stationary corner, appended to the log.

    Returns False when the encoder never answered, so the caller can carry on
    with the other corners and still report a failure at the end.
    """
    print(f"[init] {name}: CANcoder {cc_id} on '{bus_name}'")
    encoder = cancoder.open_encoder(cc_id, bus_name)
    sig = encoder.get_absolute_position()
    _set_cancoder_rate(sig, rate_hz)

    print(f"[warmup] discarding {WARMUP_SAMPLES} samples")
    warm = False
    for _ in range(WARMUP_SAMPLES):
        _refresh([sig])
        warm = warm or _signal_ok(sig)
        time.sleep(SAMPLE_DT)

    if not warm:
        print(f"REFUSED: {name}: CANcoder {cc_id} on '{bus_name}' sent no frame "
              f"in {WARMUP_SAMPLES * SAMPLE_DT:.1f} s (status "
              f"{_signal_status(sig)}), so nothing was recorded for it.\n"
              "  FIX: check the id and the CANcoder bus wiring, then run "
              "tools/cancoder_config_check.py --corner all. A missing encoder "
              "answers with a plausible default rather than an error.")
        return False

    samples, fresh = _sample_cancoder(sig)
    if not fresh:
        print(f"REFUSED: {name}: CANcoder {cc_id} stopped reporting during the "
              f"read (status {_signal_status(sig)}), so the window is not a "
              "measurement.\n"
              "  FIX: check the CANcoder bus cabling and power, then repeat.")
        return False

    mean, stdev, jitter = _summarize(samples)
    health, sticky_hex = _read_magnet_and_sticky(encoder)
    wheel = cancoder.wheel_angle_deg(mean, offset) if offset is not None else None

    row = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "epoch": f"{time.time():.3f}",
        "rig_name": rig_name,
        "corner": name,
        "label": label,
        "n": N_SAMPLES,
        "mean_deg": f"{mean:+.4f}",
        "stdev_deg": f"{stdev:.4f}",
        "jitter_deg": f"{jitter:.4f}",
        "raw_rev": f"{mean / 360.0:+.6f}",
        "wheel_deg": "" if wheel is None else f"{wheel:+.4f}",
        "magnet": health,
        "sticky_hex": sticky_hex,
    }
    _append_rows(log_path, [row])

    print(f"\n[result] {row['timestamp']}  corner={name}  label={label}")
    print(f"  mean   = {row['mean_deg']} deg absolute")
    if wheel is not None:
        print(f"  wheel  = {wheel:+.4f} deg (offset {offset:+.2f})")
    print(f"  stdev  = {row['stdev_deg']} deg")
    print(f"  jitter = {row['jitter_deg']} deg  (max-min over {N_SAMPLES} samples)")
    print(f"  magnet = {health}   sticky = {sticky_hex}")
    print(f"  appended to {log_path}")
    return True


# ---------------------------------------------------------------------------
# Motion
# ---------------------------------------------------------------------------

def _stop_all(corner_states):
    """Zero every steer and drive output."""
    for cs in corner_states:
        cs["spark"].percent_output(0.0)
        if cs["drive"] is not None:
            cs["drive"].percent_output(0.0)


def _fine_converge_multi(corner_states, target_deg, label, fine):
    """Nudge every corner inside a tight tolerance with a low-gain loop.

    The main loop stops commanding inside its deadband, which parks a wheel up
    to that deadband away from the target. This pass uses a much smaller
    deadband and a gain low enough not to oscillate, and it stops driving each
    corner the moment that corner is inside tolerance, so a fast corner is not
    nudged while a slow one catches up. A corner stiction holds is given up on
    when the timeout expires.
    """
    sigs = [cs["sig"] for cs in corner_states]
    print(f"  [{label}/fine] tight converge: deadband={fine['deadband_deg']:.2f} "
          f"kp={fine['kp']:.3f} max={fine['max_output']:.2f} "
          f"tol={fine['tol_deg']:.2f} timeout={fine['timeout_s']:.1f}s")
    converged = {cs["name"]: False for cs in corner_states}
    last_errs = {cs["name"]: 0.0 for cs in corner_states}
    end_t = time.time() + fine["timeout_s"]
    last_print_t = time.time()

    while time.time() < end_t and not all(converged.values()):
        _refresh(sigs)
        for cs in corner_states:
            name = cs["name"]
            err = steer.shortest_error_deg(target_deg,
                                           _wheel_deg(cs["sig"], cs["offset"]))
            last_errs[name] = err
            if abs(err) < fine["tol_deg"]:
                converged[name] = True
                cs["spark"].percent_output(0.0)
            elif not converged[name]:
                cs["spark"].percent_output(
                    steer.p_output(err, fine["kp"], fine["max_output"],
                                   fine["deadband_deg"]))
            if cs["drive"] is not None:
                cs["drive"].percent_output(0.0)
        time.sleep(SPIN_LOOP_DT)
        if time.time() - last_print_t > 0.5:
            last_print_t = time.time()
            parts = []
            for cs in corner_states:
                name = cs["name"]
                parts.append(f"{name}=OK" if converged[name]
                             else f"{name}={last_errs[name]:+.2f}")
            print(f"  [{label}/fine] {' '.join(parts)}")

    _stop_all(corner_states)
    parts = []
    for cs in corner_states:
        name = cs["name"]
        tag = "OK" if converged[name] else "STIC"
        parts.append(f"{name}={last_errs[name]:+.3f} {tag}")
    n_conv = sum(1 for v in converged.values() if v)
    print(f"  [{label}/fine] done: {n_conv}/{len(corner_states)} within "
          f"{fine['tol_deg']:.2f} deg; per-corner: {'  '.join(parts)}")


def _drive_to_target_multi(corner_states, target_deg, label, gains, settle, fine):
    """Run the steer P loop on every corner at once until all of them settle.

    Acceptance follows settle["mode"], and every corner must be settled and have
    stayed settled for settle["hold_s"]. Each timeout relaxes both tolerances by
    half and restarts the clock; after three relaxations it gives up instead of
    grinding against a corner that will not move.

    Drive controllers are commanded 0.0 every tick, which keeps their heartbeat
    watchdog fed through a convergence that can take seconds.

    Returns ({corner: [(t, wheel_deg, duty)]}, converged). The samples are what
    steer.score_step scores; converged says whether the wheels are at the target
    or wherever the loop gave up, which is what makes the reading comparable.
    """
    names = ", ".join(cs["name"] for cs in corner_states)
    print(f"  [{label}] driving {{{names}}} to {target_deg:+.2f} deg "
          f"(mode={settle['mode']}, motion={settle['motion_deg']:.2f}, "
          f"tol={settle['tol_deg']:.2f}, deadband={gains['deadband_deg']:.2f}, "
          f"kp={gains['kp']:.3f})")
    sigs = [cs["sig"] for cs in corner_states]
    window_n = max(2, int(settle["window_s"] / SPIN_LOOP_DT))
    histories = {cs["name"]: deque(maxlen=window_n) for cs in corner_states}
    samples = {cs["name"]: [] for cs in corner_states}
    last_errs = {cs["name"]: 0.0 for cs in corner_states}
    stale = {cs["name"]: 0 for cs in corner_states}
    motion_tol = settle["motion_deg"]
    err_tol = settle["tol_deg"]
    relax_count = 0
    start = time.time()
    settled_since = None

    while True:
        _refresh(sigs)
        all_settled = True
        for cs in corner_states:
            name = cs["name"]
            angle = _wheel_deg(cs["sig"], cs["offset"])
            err = steer.shortest_error_deg(target_deg, angle)
            duty = steer.p_output(err, gains["kp"], gains["max_output"],
                                  gains["deadband_deg"])
            cs["spark"].percent_output(duty)
            if cs["drive"] is not None:
                cs["drive"].percent_output(0.0)
            histories[name].append(angle)
            samples[name].append((time.time(), angle, duty))
            last_errs[name] = err
            stale[name] = 0 if _signal_ok(cs["sig"]) else stale[name] + 1
            if not steer.settled(histories[name], err, settle["mode"],
                                 motion_tol, err_tol):
                all_settled = False
        lost = [n for n, ticks in stale.items() if ticks >= SIGNAL_LOSS_TICKS]
        if lost:
            _stop_all(corner_states)
            print(f"  [{label}] STOPPED: no frame from {', '.join(lost)} for "
                  f"{SIGNAL_LOSS_TICKS * SPIN_LOOP_DT:.1f} s, so the loop was "
                  "steering a motor on a frozen angle")
            return samples, False
        time.sleep(SPIN_LOOP_DT)

        now = time.time()
        if all_settled:
            if settled_since is None:
                settled_since = now
            elif now - settled_since >= settle["hold_s"]:
                _stop_all(corner_states)
                err_str = "  ".join(f"{n}={e:+.3f}" for n, e in last_errs.items())
                print(f"  [{label}] settled (mode={settle['mode']}, "
                      f"motion_tol={motion_tol:.3f}, err_tol={err_tol:.3f}); "
                      f"per-corner err: {err_str}")
                if fine["enabled"]:
                    _fine_converge_multi(corner_states, target_deg, label, fine)
                return samples, True
        else:
            settled_since = None

        if now - start > settle["timeout_s"]:
            relax_count += 1
            motion_tol *= 1.5
            err_tol *= 1.5
            start = now
            print(f"  [{label}] timeout; relaxing motion_tol -> {motion_tol:.3f}, "
                  f"err_tol -> {err_tol:.3f}")
            if relax_count >= 3:
                _stop_all(corner_states)
                err_str = "  ".join(f"{n}={e:+.3f}" for n, e in last_errs.items())
                print(f"  [{label}] giving up after {relax_count} relaxations; "
                      f"per-corner err: {err_str}")
                return samples, False


def _aggressive_spin_multi(corner_states, duration, output, jump_threshold_deg,
                           expected_deg, drive_output=0.0,
                           drive_pattern="mirror"):
    """Spin every corner together, reversing on a fixed period.

    drive_pattern decides what the drive motors do while the steer axes reverse:
    mirror flips the drive direction with the steer direction, static holds
    +drive_output throughout, and off leaves the drive controllers at zero.

    Returns (per_corner_stats, long_tick_count, max_tick_dt_ms), where each
    corner reports jump_count, max_jump_deg and drive_max_applied.
    """
    names = ", ".join(cs["name"] for cs in corner_states)
    drive_moves = drive_output > 0 and drive_pattern != "off"
    print(f"  [spin] {{{names}}} {duration:.1f}s at steer +/-{output:.2f} duty, "
          f"flip {SPIN_FLIP_PERIOD:.1f}s, "
          f"jump_threshold={jump_threshold_deg:.1f} deg/tick "
          f"(expected ~{expected_deg:.1f} deg/tick)")
    if drive_moves:
        print(f"  [spin] DRIVE motors active: +/-{drive_output:.2f} duty, "
              f"pattern={drive_pattern}")
    else:
        print(f"  [spin] drive motors held at 0 (--drive-output {drive_output:.2f}, "
              f"pattern {drive_pattern})")

    sigs = [cs["sig"] for cs in corner_states]
    stats = {cs["name"]: {"jump_count": 0, "max_jump_deg": 0.0,
                          "drive_max_applied": 0.0, "stale_ticks": 0}
             for cs in corner_states}
    prev_deg = {cs["name"]: None for cs in corner_states}
    t_start = time.time()
    end_t = t_start + duration
    flip_t = t_start + SPIN_FLIP_PERIOD
    direction = +1
    prev_t = t_start
    long_tick_count = 0
    max_tick_dt = 0.0
    last_print_t = t_start

    ticks = 0
    while time.time() < end_t:
        ticks += 1
        now = time.time()
        dt = now - prev_t
        prev_t = now
        if dt > LONG_TICK_S:
            long_tick_count += 1
        max_tick_dt = max(max_tick_dt, dt)
        if now > flip_t:
            direction = -direction
            flip_t += SPIN_FLIP_PERIOD

        if not drive_moves:
            drive_dir = 0
        elif drive_pattern == "static":
            drive_dir = 1
        else:
            drive_dir = direction

        _refresh(sigs)
        for cs in corner_states:
            name = cs["name"]
            raw_deg = cs["sig"].value * 360.0
            prev = prev_deg[name]
            if not _signal_ok(cs["sig"]):
                stats[name]["stale_ticks"] += 1
            if prev is not None:
                moved = abs(steer.normalize_deg(raw_deg - prev))
                if moved > jump_threshold_deg:
                    stats[name]["jump_count"] += 1
                    stats[name]["max_jump_deg"] = max(
                        stats[name]["max_jump_deg"], moved)
                    tag = " (long_tick)" if dt > LONG_TICK_S else ""
                    print(f"  [{name} JUMP] t={now - t_start:.2f}s  "
                          f"d={moved:.2f} deg  abs={raw_deg:+.2f}  "
                          f"dt={dt * 1000:.1f}ms{tag}")
            prev_deg[name] = raw_deg
            cs["spark"].percent_output(direction * output)
            if cs["drive"] is not None:
                cs["drive"].percent_output(drive_dir * drive_output)
                applied = cs["drive"].applied_output
                if applied is not None:
                    stats[name]["drive_max_applied"] = max(
                        stats[name]["drive_max_applied"], abs(applied))

        if now - last_print_t > 1.0:
            last_print_t = now
            jumps_str = " ".join(f"{n}={s['jump_count']}"
                                 for n, s in stats.items())
            extra = ""
            if drive_moves:
                drv_str = " ".join(f"{n}={s['drive_max_applied']:.2f}"
                                   for n, s in stats.items())
                extra = f"  drv_max[{drv_str}]"
            print(f"  [spin] t={now - t_start:.1f}s  dir={direction:+d}  "
                  f"jumps[{jumps_str}]  long_ticks={long_tick_count}{extra}")

        time.sleep(SPIN_LOOP_DT)

    _stop_all(corner_states)
    max_dt_ms = max_tick_dt * 1000
    print(f"  [spin] done. long_ticks={long_tick_count}  "
          f"max_tick_dt={max_dt_ms:.1f}ms")
    for name, s in stats.items():
        drv = (f"  drive_max_applied={s['drive_max_applied']:.3f}"
               if drive_moves else "")
        stale = (f"  stale_ticks={s['stale_ticks']}/{ticks}"
                 if s["stale_ticks"] else "")
        print(f"          [{name}]  jump_count={s['jump_count']}  "
              f"max_jump_deg={s['max_jump_deg']:.2f}{drv}{stale}")
        if ticks and s["stale_ticks"] == ticks:
            print(f"          [{name}]  WARNING: no fresh frame all spin, so "
                  f"jump_count=0 says nothing about this corner")
    return stats, long_tick_count, max_dt_ms


def _init_corners(bus, plan, product, cancoder_bus, corner_states,
                  rate_hz=CANCODER_RATE_HZ):
    """Open both controllers and the CANcoder for every corner on an open bus.

    States are appended to `corner_states` as they are built, and every
    controller is commanded zero the moment it exists, so the caller's finally
    can stop whatever was reached. Returns a refusal message, or None.
    """
    print(f"[init] {len(plan)} corner(s) on '{bus.channel}'")
    for item in plan:
        drive_label = "-" if item["drive_id"] is None else item["drive_id"]
        print(f"[init] {item['name']}: steer={item['steer_id']}  "
              f"drive={drive_label}  cancoder={item['cancoder_id']}  "
              f"offset={item['offset']:+.2f}")
        spark = bus.init_controller(item["steer_id"], product,
                                    clear_sticky_faults=False)
        spark.percent_output(0.0)
        state = {"name": item["name"], "spark": spark, "drive": None,
                 "cancoder": None, "sig": None,
                 "cancoder_id": item["cancoder_id"], "offset": item["offset"]}
        corner_states.append(state)
        if item["drive_id"] is not None:
            drive = bus.init_controller(item["drive_id"], product,
                                        clear_sticky_faults=False)
            drive.percent_output(0.0)
            state["drive"] = drive
        encoder = cancoder.open_encoder(item["cancoder_id"], cancoder_bus)
        state["cancoder"] = encoder
        state["sig"] = encoder.get_absolute_position()
        _set_cancoder_rate(state["sig"], rate_hz)

    deadline = time.time() + 3.0
    while time.time() < deadline:
        if all(cs["spark"].is_live(1.0)
               and (cs["drive"] is None or cs["drive"].is_live(1.0))
               for cs in corner_states):
            break
        time.sleep(0.1)

    missing = []
    for cs in corner_states:
        if not cs["spark"].is_live(1.0):
            missing.append((cs["name"], "steer", cs["spark"].id))
        if cs["drive"] is not None and not cs["drive"].is_live(1.0):
            missing.append((cs["name"], "drive", cs["drive"].id))
    if missing:
        lines = [f"REFUSED: {len(missing)} controller(s) sent no status frame:"]
        for name, kind, can_id in missing:
            lines.append(f"  - {name} {kind} (CAN id {can_id})")
        lines.append(f"  FIX: power the motors and check the CAN cabling on "
                     f"'{bus.channel}', then run `spark status`.")
        return "\n".join(lines)

    sigs = [cs["sig"] for cs in corner_states]
    answered = set()
    deadline = time.time() + 2.0
    while time.time() < deadline and len(answered) < len(corner_states):
        _refresh(sigs)
        for cs in corner_states:
            if _signal_ok(cs["sig"]):
                answered.add(cs["name"])
        time.sleep(0.05)
    quiet = [cs for cs in corner_states if cs["name"] not in answered]
    if quiet:
        lines = [f"REFUSED: {len(quiet)} CANcoder(s) sent no frame on "
                 f"'{cancoder_bus}':"]
        for cs in quiet:
            lines.append(f"  - {cs['name']} (id {cs['cancoder_id']}, status "
                         f"{_signal_status(cs['sig'])})")
        lines.append("  FIX: check the ids and the CANcoder bus wiring, then run "
                     "tools/cancoder_config_check.py --corner all. A missing "
                     "encoder answers with a plausible default rather than an "
                     "error, so the whole run would be fiction.")
        return "\n".join(lines)

    print(f"[init] all {len(corner_states)} corner(s) reporting, "
          "controllers and CANcoders both")
    return None


def record_spin(corner_states, target_deg, spin_seconds, spin_output,
                drive_output, drive_pattern, gains, settle, fine,
                jump_threshold_deg, expected_deg, log_path, rig_name):
    """Baseline, stress and re-converge every corner, then log both readings.

    Returns 0 when both approaches converged, and 1 when one gave up, because
    the delta between two readings only means something if the wheel came back
    to the same commanded angle.
    """
    try:
        cancoder.warmup([(cs["name"], cs["sig"]) for cs in corner_states],
                        timeout_s=2.0, settle_window_s=0.5,
                        stable_tol_deg=0.3, sample_dt=SAMPLE_DT)
    except TimeoutError as err:
        print(f"[warmup] WARNING: {err}")

    run_id = f"{time.time():.0f}"
    pre_stats, post_stats = {}, {}
    pre_scores, post_scores = {}, {}
    drive_pre_sticky, drive_post_sticky = {}, {}
    converged = {}
    reached = []
    stale_reads = []

    try:
        print(f"\n--- Phase A: pre-spin drive to {target_deg:+.2f} deg ---")
        samples, converged["pre"] = _drive_to_target_multi(
            corner_states, target_deg, "pre", gains, settle, fine)
        for cs in corner_states:
            name = cs["name"]
            pre_scores[name] = steer.score_step(samples[name], target_deg,
                                                gains["deadband_deg"],
                                                settle["tol_deg"])
            window, fresh = _sample_cancoder(cs["sig"])
            if not fresh:
                stale_reads.append(f"{name} pre")
            pre_stats[name] = _summarize(window)
            mean, stdev, jitter = pre_stats[name]
            print(f"  [{name} pre] mean={mean:+.4f}  stdev={stdev:.4f}  "
                  f"jitter={jitter:.4f}")
        reached.append("pre")

        for cs in corner_states:
            drive_pre_sticky[cs["name"]] = (
                _read_spark_sticky(cs["drive"]) if cs["drive"] else "?")

        print("\n--- Phase B: aggressive spin ---")
        per_corner_stats, long_tick_count, max_tick_dt_ms = _aggressive_spin_multi(
            corner_states, spin_seconds, spin_output, jump_threshold_deg,
            expected_deg, drive_output=drive_output,
            drive_pattern=drive_pattern)
        reached.append("spin")

        for cs in corner_states:
            drive_post_sticky[cs["name"]] = (
                _read_spark_sticky(cs["drive"]) if cs["drive"] else "?")

        time.sleep(0.5)

        print(f"\n--- Phase C: post-spin drive to {target_deg:+.2f} deg ---")
        samples, converged["post"] = _drive_to_target_multi(
            corner_states, target_deg, "post", gains, settle, fine)
        for cs in corner_states:
            name = cs["name"]
            post_scores[name] = steer.score_step(samples[name], target_deg,
                                                 gains["deadband_deg"],
                                                 settle["tol_deg"])
            window, fresh = _sample_cancoder(cs["sig"])
            if not fresh:
                stale_reads.append(f"{name} post")
            post_stats[name] = _summarize(window)
            mean, stdev, jitter = post_stats[name]
            print(f"  [{name} post] mean={mean:+.4f}  stdev={stdev:.4f}  "
                  f"jitter={jitter:.4f}")
        reached.append("post")
    except KeyboardInterrupt:
        missed = [p for p in ("pre", "spin", "post") if p not in reached]
        print("\n[abort] KeyboardInterrupt; stopping all motors")
        print(f"  phases completed: {', '.join(reached) or 'none'}")
        print(f"  never reached: {', '.join(missed)}")
        print(f"  no rows were logged, so none of "
              f"{', '.join(cs['name'] for cs in corner_states)} has a pre/post "
              "pair from this run")
        raise
    finally:
        _stop_all(corner_states)

    # One spin event, so run_id, long ticks and the threshold are shared.
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    rows = []
    for cs in corner_states:
        name = cs["name"]
        health, sticky_hex = _read_magnet_and_sticky(cs["cancoder"])
        s = per_corner_stats[name]
        common = {
            "rig_name": rig_name,
            "corner": name,
            "label": "spin",
            "n": N_SAMPLES,
            "magnet": health,
            "sticky_hex": sticky_hex,
            "run_id": run_id,
            "target_deg": f"{target_deg:+.4f}",
            "spin_seconds": f"{spin_seconds:.1f}",
            "spin_output": f"{spin_output:.3f}",
            "jump_count": str(s["jump_count"]),
            "max_jump_deg": f"{s['max_jump_deg']:.3f}",
            "long_tick_count": str(long_tick_count),
            "max_tick_dt_ms": f"{max_tick_dt_ms:.2f}",
            "spin_jump_threshold_deg": f"{jump_threshold_deg:.2f}",
            "drive_output": f"{drive_output:.3f}",
            "drive_pattern": drive_pattern,
            "drive_max_applied": f"{s['drive_max_applied']:.3f}",
            "drive_pre_sticky": drive_pre_sticky.get(name, "?"),
            "drive_post_sticky": drive_post_sticky.get(name, "?"),
            "settle_mode": settle["mode"],
            "settle_window_s": f"{settle['window_s']:.3f}",
            "settle_motion_deg": f"{settle['motion_deg']:.3f}",
            "kp": f"{gains['kp']:.4f}",
            "max_output": f"{gains['max_output']:.3f}",
            "deadband_deg": f"{gains['deadband_deg']:.3f}",
        }
        for phase, stats_by_name, scores in (("pre", pre_stats, pre_scores),
                                             ("post", post_stats, post_scores)):
            mean, stdev, jitter = stats_by_name[name]
            score = scores.get(name) or {}
            rows.append(dict(
                common, phase=phase, timestamp=ts, epoch=f"{time.time():.3f}",
                converged="yes" if converged.get(phase) else "no",
                mean_deg=f"{mean:+.4f}", stdev_deg=f"{stdev:.4f}",
                jitter_deg=f"{jitter:.4f}", raw_rev=f"{mean / 360.0:+.6f}",
                wheel_deg=f"{cancoder.wheel_angle_deg(mean, cs['offset']):+.4f}",
                settle_s=_fmt(score.get("settle_s")),
                overshoot_deg=_fmt(score.get("overshoot_deg")),
                final_error_deg=_fmt(score.get("final_error_deg")),
                saturated_frac=_fmt(score.get("saturated_frac"))))
    _append_rows(log_path, rows)

    print(f"\n[spin run_id={run_id}]")
    for cs in corner_states:
        name = cs["name"]
        pre_mean = pre_stats[name][0]
        post_mean = post_stats[name][0]
        s = per_corner_stats[name]
        line = (f"  [{name}] pre={pre_mean:+.4f}  post={post_mean:+.4f}  "
                f"delta={steer.normalize_deg(post_mean - pre_mean):+.4f}  "
                f"jumps={s['jump_count']}  "
                f"max_jump={s['max_jump_deg']:.2f}")
        if drive_output > 0:
            pre_sf = drive_pre_sticky.get(name, "?")
            post_sf = drive_post_sticky.get(name, "?")
            sticky = f"{pre_sf}->{post_sf}"
            if pre_sf != post_sf and post_sf != "?":
                sticky += " [CHANGED]"
            line += (f"  drive_max={s['drive_max_applied']:.2f}  "
                     f"drive_sticky={sticky}")
        print(line)
        print(f"         approach pre {_score_str(pre_scores.get(name))}  |  "
              f"post {_score_str(post_scores.get(name))}")
    print(f"  long ticks (>{LONG_TICK_S * 1000:.0f}ms loop dt): "
          f"{long_tick_count}  (max {max_tick_dt_ms:.1f}ms)")
    print(f"  appended {len(rows)} rows to {log_path}")

    rc = 0
    gave_up = [phase for phase in ("pre", "post") if not converged.get(phase)]
    if gave_up:
        print(f"  the {' and '.join(gave_up)} approach never converged, so the "
              "wheels ended somewhere other than the target. Those rows carry "
              "converged=no and the deltas measure the approach, not the corner")
        rc = 1
    if stale_reads:
        print(f"  no fresh CANcoder frame during {', '.join(stale_reads)}, so "
              "that reading is the last value phoenix6 held and not a "
              "measurement. Check the CANcoder bus before trusting the delta")
        rc = 1
    return rc


def _fmt(value, spec=".3f"):
    """A number for the CSV, or an empty cell when it was never measured."""
    return "" if value is None else format(value, spec)


def _score_str(score):
    """One line of steer.score_step output."""
    if not score or score.get("cost") is None:
        return "n/a"
    settle = score.get("settle_s")
    settle_s = "none" if settle is None else f"{settle:.2f}s"
    return (f"settle={settle_s} overshoot={score['overshoot_deg']:.2f} "
            f"final_err={score['final_error_deg']:+.2f} "
            f"sat={score['saturated_frac']:.2f}")


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

def _conv_tag(row):
    """One character for whether that phase's approach converged."""
    value = (row.get("converged") or "").strip()
    return {"yes": "y", "no": "n"}.get(value, "?")


def summary(log_path, deadband_deg):
    """Read the whole log back and say what each corner has been doing."""
    if not os.path.exists(log_path):
        print(f"REFUSED: there is no log at {log_path}.\n"
              "  FIX: record a run first, such as `--corner all --label locked`, "
              "or pass --log to point at the CSV you meant.")
        return REFUSED
    with open(log_path) as fh:
        rows = list(csv.DictReader(fh))
    if not rows:
        print(f"REFUSED: the log at {log_path} has a header and no rows.\n"
              "  FIX: record a run first, such as `--corner all --label locked`.")
        return REFUSED

    print(f"=== summary over {len(rows)} rows ({log_path}) ===\n")
    print(f"{'#':>3} {'timestamp':<20} {'corner':<6} {'label':<8} {'phase':<5} "
          f"{'mean_deg':>10} {'jitter':>8} {'magnet':<22}")
    for i, r in enumerate(rows, 1):
        print(f"{i:>3} {r.get('timestamp', ''):<20} {r.get('corner', '?'):<6} "
              f"{r.get('label', ''):<8} {r.get('phase', ''):<5} "
              f"{r.get('mean_deg', ''):>10} {r.get('jitter_deg', ''):>8} "
              f"{r.get('magnet', '?'):<22}")

    by_key = {}
    for r in rows:
        if r.get("label") in ("locked", "ad-hoc"):
            key = (r["label"], r.get("corner") or "?")
            by_key.setdefault(key, []).append(_safe_float(r.get("mean_deg")))
    if by_key:
        print("\n--- passive-read spread (by corner) ---")
        for (label, corner), means in sorted(by_key.items()):
            if len(means) < 2:
                print(f"  [{label}/{corner}] n={len(means)}; need at least 2")
                continue
            spread, stdev = _spread_deg(means)
            print(f"  [{label}/{corner}] n={len(means):2d}  "
                  f"mean_of_means={sum(means) / len(means):+8.3f}  "
                  f"spread={spread:6.3f} deg  stdev={stdev:6.3f}")

    spin_rows = [r for r in rows
                 if r.get("label") == "spin" and r.get("run_id")]
    runs = {}
    for r in spin_rows:
        key = (r["run_id"], r.get("corner") or "?")
        runs.setdefault(key, {})[r.get("phase", "")] = r
    complete = [(k, d) for k, d in runs.items() if "pre" in d and "post" in d]

    if complete:
        print(f"\n--- spin runs (n={len(complete)} corner-runs) ---")
        print(f"  {'run_id':<14} {'corner':<6} {'conv':>5} {'target':>8} "
              f"{'spin_s':>7} "
              f"{'duty':>6} {'pre_mean':>10} {'post_mean':>10} {'delta':>9} "
              f"{'jumps':>6} {'max_jump':>9} {'long':>5} {'maxdt_ms':>9} "
              f"{'drv_out':>8} {'drv_max':>8} {'drv_sticky':<20} {'mode':<7}")
        for (rid, corner), d in sorted(complete):
            pre, post = d["pre"], d["post"]
            pre_mean = _safe_float(pre.get("mean_deg"))
            post_mean = _safe_float(post.get("mean_deg"))
            pre_sf = post.get("drive_pre_sticky", "")
            post_sf = post.get("drive_post_sticky", "")
            sticky = f"{pre_sf}->{post_sf}" if pre_sf or post_sf else ""
            print(f"  {rid:<14} {corner:<6} "
                  f"{_conv_tag(pre) + '/' + _conv_tag(post):>5} "
                  f"{_safe_float(post.get('target_deg')):>+8.3f} "
                  f"{_safe_float(post.get('spin_seconds')):>7.1f} "
                  f"{_safe_float(post.get('spin_output')):>6.3f} "
                  f"{pre_mean:>+10.4f} {post_mean:>+10.4f} "
                  f"{steer.normalize_deg(post_mean - pre_mean):>+9.4f} "
                  f"{post.get('jump_count', '0'):>6} "
                  f"{_safe_float(post.get('max_jump_deg')):>9.2f} "
                  f"{post.get('long_tick_count', '0'):>5} "
                  f"{_safe_float(post.get('max_tick_dt_ms')):>9.2f} "
                  f"{_safe_float(post.get('drive_output')):>8.3f} "
                  f"{_safe_float(post.get('drive_max_applied')):>8.3f} "
                  f"{sticky:<20} {post.get('settle_mode', ''):<7}")

        scored = [(k, d) for k, d in complete
                  if d["post"].get("settle_s") or d["pre"].get("settle_s")]
        if scored:
            print("\n--- approach quality (steer.score_step, per corner-run) ---")
            print(f"  {'run_id':<14} {'corner':<6} {'phase':<5} {'settle_s':>9} "
                  f"{'overshoot':>10} {'final_err':>10} {'saturated':>10}")
            for (rid, corner), d in sorted(scored):
                for phase in ("pre", "post"):
                    r = d[phase]
                    settle_cell = r.get("settle_s") or "none"
                    print(f"  {rid:<14} {corner:<6} {phase:<5} {settle_cell:>9} "
                          f"{_safe_float(r.get('overshoot_deg')):>10.2f} "
                          f"{_safe_float(r.get('final_error_deg')):>+10.2f} "
                          f"{_safe_float(r.get('saturated_frac')):>10.2f}")

    incomplete = [k for k, d in runs.items()
                  if not ("pre" in d and "post" in d)]
    if incomplete:
        print(f"  (skipping {len(incomplete)} incomplete corner-run(s): "
              f"{', '.join(f'{rid}/{corner}' for rid, corner in sorted(incomplete))})")

    print("\n--- diagnosis (per corner) ---")
    locked_by_corner = {}
    for r in rows:
        if r.get("label") == "locked":
            corner = r.get("corner") or "?"
            locked_by_corner.setdefault(corner, []).append(
                _safe_float(r.get("mean_deg")))
    for corner in sorted(locked_by_corner):
        means = locked_by_corner[corner]
        if len(means) < 2:
            continue
        spread, _ = _spread_deg(means)
        if spread < DRIFT_BUDGET_DEG:
            print(f"  [{corner}/locked] spread {spread:.3f} deg < "
                  f"{DRIFT_BUDGET_DEG}: the encoder repeats")
        else:
            print(f"  [{corner}/locked] spread {spread:.3f} deg >= "
                  f"{DRIFT_BUDGET_DEG}: the encoder calibration is DRIFTING. "
                  f"Reseat or replace the magnet")

    deltas_by_corner = {}
    unjudged = {}
    for (rid, corner), d in complete:
        if "n" in (_conv_tag(d["pre"]), _conv_tag(d["post"])):
            unjudged.setdefault(corner, []).append(rid)
            continue
        delta = steer.normalize_deg(_safe_float(d["post"].get("mean_deg"))
                                    - _safe_float(d["pre"].get("mean_deg")))
        band = _safe_float(d["post"].get("deadband_deg"), 0.0) or deadband_deg
        deltas_by_corner.setdefault(corner, []).append((delta, band))
    for corner in sorted(set(deltas_by_corner) | set(unjudged)):
        left_out = unjudged.get(corner, [])
        judged = deltas_by_corner.get(corner, [])
        if not judged:
            print(f"  [{corner}/spin] nothing to judge: run(s) "
                  f"{', '.join(left_out)} had an approach that never converged, "
                  f"so their deltas measure the approach and not the corner")
            continue
        worst, band = max(judged, key=lambda x: abs(x[0]))
        worst = abs(worst)
        band_budget = band + 0.1
        if worst < DRIFT_BUDGET_DEG:
            print(f"  [{corner}/spin] max |delta|={worst:.3f} deg < "
                  f"{DRIFT_BUDGET_DEG}: under the sampling jitter, so the "
                  f"encoder and the mechanics both held")
        elif worst < band_budget:
            print(f"  [{corner}/spin] max |delta|={worst:.3f} deg in "
                  f"[{DRIFT_BUDGET_DEG}, {band_budget:.1f}): WITHIN THE "
                  f"DEADBAND ({band:.1f} deg), which is what static settle "
                  f"gives you. The wheel rests wherever physics left it inside "
                  f"the no-output zone, so the encoder is likely fine")
        elif worst < LARGE_DRIFT_DEG:
            print(f"  [{corner}/spin] max |delta|={worst:.3f} deg in "
                  f"[{band_budget:.1f}, {LARGE_DRIFT_DEG}): DRIFT BEYOND THE "
                  f"DEADBAND. Suspect the magnet slipping on the shaft, "
                  f"thermal movement, or seed drift on the controller side")
        else:
            print(f"  [{corner}/spin] max |delta|={worst:.3f} deg >= "
                  f"{LARGE_DRIFT_DEG}: LARGE MISCOUNT during the spin. Suspect "
                  f"an encoder firmware glitch at high RPM, dropped frames, or "
                  f"the controller losing its encoder")
        if left_out:
            print(f"  [{corner}/spin] judged {len(judged)} run(s); left out "
                  f"{', '.join(left_out)} because an approach never converged")

    for (rid, corner), d in sorted(runs.items()):
        post = d.get("post")
        if not post:
            continue
        pre_sf = post.get("drive_pre_sticky", "") or ""
        post_sf = post.get("drive_post_sticky", "") or ""
        if (pre_sf and post_sf and pre_sf != post_sf
                and post_sf not in ("?", "0x00", "0x0000")):
            print(f"  [{corner}/drive] sticky changed {pre_sf} -> {post_sf} "
                  f"during run {rid}. The drive controller faulted under stress")
    return 0


# ---------------------------------------------------------------------------
# Config and flags
# ---------------------------------------------------------------------------

def _group(conf, name):
    """One devices group as {label: id}, or None when the config has no such group."""
    group = getattr(getattr(conf, "devices", None), name, None)
    return None if group is None else vars(group)


def _default_log_path(conf):
    """The CSV log, kept beside the config the runs were recorded against."""
    rig = str(getattr(conf, "rig_name", "") or "rig")
    return os.path.join(os.path.dirname(cfg.config_path()),
                        f"steer-stress-{rig}.csv")


def _can_ids(group, group_name):
    """({label: int id}, refusal) for one devices group, in the config's order."""
    if not group:
        return None, (f"REFUSED: devices.{group_name} names no device.\n"
                      f"  FIX: give it one CAN id per corner, as "
                      f"`{group_name}: {{LF: 15, RF: 13}}`, or drop the group "
                      f"and the flags that need it.")
    ids, bad = {}, []
    for label, value in group.items():
        try:
            ids[label] = int(value)
        except (TypeError, ValueError):
            bad.append(f"{label}={value!r}")
            continue
        if not 0 <= ids[label] <= CAN_ID_MAX:
            bad.append(f"{label}={value!r}")
    if bad:
        return None, (f"REFUSED: devices.{group_name} has {len(bad)} label(s) "
                      f"without a usable CAN id: {', '.join(bad)}.\n"
                      f"  FIX: give each one a whole number from 0 to "
                      f"{CAN_ID_MAX}, which is the CAN device id range.")
    return ids, None


def _offsets(cc_conf):
    """({label: float deg}, refusal) for cancoder.offsets_deg."""
    offsets_ns = getattr(cc_conf, "offsets_deg", None)
    if offsets_ns is None:
        return {}, None
    out, bad = {}, []
    for label, value in vars(offsets_ns).items():
        try:
            out[label] = float(value)
        except (TypeError, ValueError):
            bad.append(f"{label}={value!r}")
    if bad:
        return None, (f"REFUSED: cancoder.offsets_deg has {len(bad)} label(s) "
                      f"without a number: {', '.join(bad)}.\n"
                      "  FIX: run tools/cancoder_calibrate.py for each one and "
                      "write the degrees it prints into the config.")
    return out, None


def _select_corners(requested, group, group_name):
    """Corner labels to act on in the config's order, or (None, refusal)."""
    labels = list(group)
    if requested == "all":
        return labels, None
    wanted = []
    for item in requested.split(","):
        item = item.strip()
        if item and item not in wanted:
            wanted.append(item)
    if not wanted:
        return None, (f"REFUSED: --corner {requested!r} names no corner.\n"
                      f"  FIX: pass one of {', '.join(labels)}, a "
                      "comma-separated list of them, or all.")
    unknown = [c for c in wanted if c not in group]
    if unknown:
        return None, (f"REFUSED: {', '.join(unknown)} is not in "
                      f"devices.{group_name} ({', '.join(labels)}).\n"
                      "  FIX: use one of those labels, a comma-separated list "
                      "of them, or all.")
    return wanted, None


def _bad_range(name, value, low, high, fix, low_ok=False):
    """A refusal when a flag is outside its range, NaN included, or None."""
    within = (low <= value if low_ok else low < value) and value <= high
    if within:
        return None
    bracket = "[" if low_ok else "("
    return (f"REFUSED: {name} is {value!r}, outside {bracket}{low:g}, {high:g}].\n"
            f"  FIX: {fix}")


def _flag_refusal(args, moving):
    """The first flag that would reach hardware unusable, or None.

    Called before the confirmation, so a duty, a duration or a rate that makes
    no sense is refused while every controller is still untouched.
    """
    checks = [_bad_range("--cancoder-rate-hz", args.cancoder_rate_hz, 0.0, 1000.0,
                         "a rate from 1 to 1000 Hz; 500 is the default")]
    if moving:
        checks += [
            _bad_range("--spin-output", args.spin_output, 0.0, 1.0,
                       "a steer duty above 0 and at most 1, such as 0.5"),
            _bad_range("--drive-output", args.drive_output, 0.0, 1.0,
                       "a drive duty from 0 to 1; 0 holds the drive motors",
                       low_ok=True),
            _bad_range("--spin-seconds", args.spin_seconds, 0.0, 3600.0,
                       "a stress phase longer than 0 s and at most an hour"),
            _bad_range("--target", args.target, -3600.0, 3600.0,
                       "a wheel angle in degrees, such as 0", low_ok=True),
            _bad_range("--free-rpm", args.free_rpm, 0.0, 100000.0,
                       "the motor's no-load RPM; 6700 is a NEO Vortex"),
            _bad_range("--kp", args.kp, 0.0, 10.0,
                       "a gain above 0; 0.03 is the default"),
            _bad_range("--max-output", args.max_output, 0.0, 1.0,
                       "a duty clamp above 0 and at most 1"),
            _bad_range("--deadband-deg", args.deadband_deg, 0.0, 90.0,
                       "a deadband from 0 to 90 deg", low_ok=True),
            _bad_range("--settle-window-s", args.settle_window_s,
                       MIN_SETTLE_WINDOW_S, 10.0,
                       f"at least {MIN_SETTLE_WINDOW_S:.2f} s, which is "
                       f"{int(MIN_SETTLE_WINDOW_S / SPIN_LOOP_DT)} loop ticks. "
                       "A shorter window holds too few samples for a stopped "
                       "wheel to be told from a slow one", low_ok=True),
            _bad_range("--settle-motion-deg", args.settle_motion_deg, 0.0, 90.0,
                       "a motion tolerance above 0 deg"),
            _bad_range("--settle-tol-deg", args.settle_tol_deg, 0.0, 180.0,
                       "an error tolerance above 0 and at most 180 deg"),
            _bad_range("--settle-hold-s", args.settle_hold_s, 0.0, 60.0,
                       "how long every corner must stay settled, 0 or more",
                       low_ok=True),
            _bad_range("--settle-timeout-s", args.settle_timeout_s, 0.0, 600.0,
                       "seconds before the tolerances relax, above 0"),
            _bad_range("--fine-deadband-deg", args.fine_deadband_deg, 0.0, 90.0,
                       "an inner deadband from 0 to 90 deg", low_ok=True),
            _bad_range("--fine-kp", args.fine_kp, 0.0, 10.0,
                       "a gain above 0; 0.025 is the default"),
            _bad_range("--fine-max-output", args.fine_max_output, 0.0, 1.0,
                       "a duty clamp above 0 and at most 1"),
            _bad_range("--fine-tol-deg", args.fine_tol_deg, 0.0, 90.0,
                       "an accept threshold above 0 deg"),
            _bad_range("--fine-timeout-s", args.fine_timeout_s, 0.0, 600.0,
                       "a per-corner time cap above 0 s"),
        ]
    for message in checks:
        if message:
            return message
    return None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv=None):
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--label", choices=["locked", "spin", "ad-hoc"],
                      help="record one run under this label")
    mode.add_argument("--summary", action="store_true",
                      help="read the log back and print stats over every run")
    p.add_argument("--corner", default="all",
                   help="corner label, a comma-separated list of them, or all. "
                        "Labels come from devices.steer, and all keeps the "
                        "order the config lists them in (default: all)")
    p.add_argument("--interface", help="SPARK bus; defaults to can.interface")
    p.add_argument("--bus", help="CANcoder bus; defaults to cancoder.bus")
    p.add_argument("--log", help="CSV to append to; defaults to a file beside "
                                 "the config")
    p.add_argument("--bring-up", action="store_true",
                   help="reset and bring up the CAN netdev before opening the "
                        "bus, for an adapter that wedges between runs")
    p.add_argument("--target", type=float, default=0.0,
                   help="(spin) wheel angle to converge on, deg (default 0.0)")
    p.add_argument("--spin-seconds", type=float, default=30.0,
                   help="(spin) length of the stress phase (default 30)")
    p.add_argument("--spin-output", type=float, default=0.5,
                   help="(spin) steer duty magnitude, 0 to 1 (default 0.5)")
    p.add_argument("--free-rpm", type=float, default=FREE_RPM_DEFAULT,
                   help="motor no-load RPM, for sizing the jump threshold "
                        "(default 6700, a NEO Vortex)")
    p.add_argument("--cancoder-rate-hz", type=float, default=CANCODER_RATE_HZ,
                   help="CANcoder broadcast rate to request (default 500)")
    p.add_argument("--drive-output", type=float, default=0.0,
                   help="(spin) drive duty magnitude, 0 to 1. 0 holds the drive "
                        "motors at zero (default). Above 0 SPINS THE WHEELS, "
                        "which needs them off the ground")
    p.add_argument("--drive-pattern", choices=["off", "mirror", "static"],
                   default="mirror",
                   help="(spin) drive direction against steer direction. mirror "
                        "flips with the steer reversal, static holds "
                        "+drive-output, off holds zero. Ignored at "
                        "--drive-output 0 (default: mirror)")
    p.add_argument("--kp", type=float, default=0.03,
                   help="steer proportional gain (default 0.03)")
    p.add_argument("--max-output", type=float, default=0.5,
                   help="steer duty clamp (default 0.5)")
    p.add_argument("--deadband-deg", type=float, default=1.5,
                   help="steer deadband; inside it the loop commands zero "
                        "(default 1.5)")
    p.add_argument("--settle-mode", choices=["static", "tol", "either"],
                   default="static",
                   help="acceptance rule. static accepts a stopped wheel, tol "
                        "accepts |err| under --settle-tol-deg, either takes the "
                        "first to fire. static sidesteps the deadband deadlock "
                        "(default: static)")
    p.add_argument("--settle-window-s", type=float, default=0.20,
                   help="(static) look-back window for a stopped wheel "
                        "(default 0.20)")
    p.add_argument("--settle-motion-deg", type=float, default=0.30,
                   help="(static) largest angle range over that window still "
                        "counted as stopped (default 0.30)")
    p.add_argument("--settle-tol-deg", type=float, default=SETTLE_ERR_DEG,
                   help="(tol/either) |err| threshold (default 0.5)")
    p.add_argument("--settle-hold-s", type=float, default=SETTLE_HOLD_S,
                   help="how long every corner must stay settled (default 1.0)")
    p.add_argument("--settle-timeout-s", type=float, default=DRIVE_TIMEOUT_S,
                   help="seconds before the tolerances relax (default 15.0)")
    p.add_argument("--fine-converge", action="store_true",
                   help="after the main settle, run a tight low-gain loop that "
                        "bypasses the main deadband so each wheel parks at the "
                        "target")
    p.add_argument("--fine-deadband-deg", type=float, default=0.20,
                   help="(fine-converge) inner deadband (default 0.20)")
    p.add_argument("--fine-kp", type=float, default=0.025,
                   help="(fine-converge) proportional gain (default 0.025)")
    p.add_argument("--fine-max-output", type=float, default=0.15,
                   help="(fine-converge) duty clamp (default 0.15)")
    p.add_argument("--fine-tol-deg", type=float, default=0.20,
                   help="(fine-converge) per-corner accept threshold "
                        "(default 0.20)")
    p.add_argument("--fine-timeout-s", type=float, default=2.0,
                   help="(fine-converge) per-corner time cap (default 2.0)")
    args = p.parse_args(argv)

    cfg.load_host()
    conf = cfg.get()
    log_path = args.log or _default_log_path(conf)
    rig_name = str(getattr(conf, "rig_name", "") or "")

    if args.summary:
        return summary(log_path, args.deadband_deg)

    refusal = _flag_refusal(args, moving=args.label == "spin")
    if refusal:
        print(refusal)
        return REFUSED

    if not cancoder.available():
        print(cancoder.EXTRA_HINT)
        return REFUSED

    cc_conf = getattr(conf, "cancoder", None)
    cc_raw = _group(conf, "cancoder")
    bus_name = args.bus or getattr(cc_conf, "bus", None)
    if cc_raw is None or not bus_name:
        print("REFUSED: this config names no CANcoders, so there is nothing to "
              "measure.\n"
              "  FIX: add a `devices.cancoder` group and a `cancoder.bus`. "
              "sparklib/data/spark.yaml carries a worked example of both.")
        return REFUSED
    cc_group, refusal = _can_ids(cc_raw, "cancoder")
    if refusal:
        print(refusal)
        return REFUSED
    offsets, refusal = _offsets(cc_conf)
    if refusal:
        print(refusal)
        return REFUSED

    if args.label in ("locked", "ad-hoc"):
        names, refusal = _select_corners(args.corner, cc_group, "cancoder")
        if refusal:
            print(refusal)
            return REFUSED
        read, silent = [], []
        try:
            for name in names:
                if record_one(args.label, name, cc_group[name], bus_name,
                              offsets.get(name), log_path, rig_name,
                              rate_hz=args.cancoder_rate_hz):
                    read.append(name)
                else:
                    silent.append(name)
        except KeyboardInterrupt:
            never = [n for n in names if n not in read and n not in silent]
            print(f"\n[abort] interrupted after {len(read)} of {len(names)} "
                  f"corner(s). Recorded: {', '.join(read) or 'none'}. "
                  f"Never read: {', '.join(never) or 'none'}.")
            return 1
        if silent:
            print(f"\n{len(silent)} of {len(names)} corner(s) reported nothing: "
                  f"{', '.join(silent)}")
            return REFUSED
        return 0

    steer_raw = _group(conf, "steer")
    if steer_raw is None:
        print("REFUSED: this config names no steer motors.\n"
              "  FIX: add a `devices.steer` group naming one CAN id per corner.")
        return REFUSED
    steer_group, refusal = _can_ids(steer_raw, "steer")
    if refusal:
        print(refusal)
        return REFUSED
    drive_raw = _group(conf, "drive")
    drive_group = {}
    if drive_raw:
        drive_group, refusal = _can_ids(drive_raw, "drive")
        if refusal:
            print(refusal)
            return REFUSED

    iface = args.interface or getattr(getattr(conf, "can", None),
                                      "interface", None)
    if not iface:
        print("REFUSED: no SPARK bus named.\n"
              "  FIX: set `can.interface` in the config, or pass --interface.")
        return REFUSED
    product_name = getattr(conf, "controller_type", None)
    if product_name not in ("sparkflex", "sparkmax"):
        print(f"REFUSED: controller_type is {product_name!r}.\n"
              "  FIX: set `controller_type` to sparkflex or sparkmax.")
        return REFUSED
    try:
        ratio = float(getattr(cc_conf, "steer_gear_ratio", None))
    except (TypeError, ValueError):
        ratio = 0.0
    if ratio <= 0:
        print("REFUSED: no usable steer gear ratio, so the jump threshold "
              "cannot be sized to the commanded speed.\n"
              "  FIX: set `cancoder.steer_gear_ratio` to a number above 0 "
              "(26.0 on an SDS MK5i, 12.8 on an MK2).")
        return REFUSED

    names, refusal = _select_corners(args.corner, steer_group, "steer")
    if refusal:
        print(refusal)
        return REFUSED
    missing_cc = [n for n in names if n not in cc_group]
    missing_off = [n for n in names if n not in offsets]
    if missing_cc or missing_off:
        print(f"REFUSED: {', '.join(sorted(set(missing_cc + missing_off)))} has "
              "no CANcoder id or no recorded offset, so its wheel angle is "
              "unknown.\n"
              "  FIX: add the id under `devices.cancoder` and run "
              "tools/cancoder_calibrate.py for the offset.")
        return REFUSED
    if args.drive_output > 0:
        missing_drive = [n for n in names if n not in drive_group]
        if missing_drive:
            print(f"REFUSED: --drive-output needs a drive id for "
                  f"{', '.join(missing_drive)}.\n"
                  "  FIX: add them under `devices.drive`, or run at "
                  "--drive-output 0.")
            return REFUSED

    plan = [{"name": n,
             "steer_id": steer_group[n],
             "drive_id": drive_group.get(n),
             "cancoder_id": cc_group[n],
             "offset": offsets[n]} for n in names]
    seen = {}
    for item in plan:
        for role in ("steer_id", "drive_id"):
            can_id = item[role]
            if can_id is None:
                continue
            owner = seen.get(can_id)
            if owner:
                print(f"REFUSED: CAN id {can_id} is both {owner} and "
                      f"{item['name']} {role.split('_')[0]}, so one motor would "
                      "take two different commands at once.\n"
                      "  FIX: give every motor its own id in `devices`, then "
                      "run `spark status` to see who answers on which.")
                return REFUSED
            seen[can_id] = f"{item['name']} {role.split('_')[0]}"

    if not args.bring_up:
        try:
            netdev.check_netdev(iface)
        except RuntimeError as err:
            print(f"REFUSED: {err}\n"
                  "  FIX: bring the bus up, pass --bring-up to have this tool "
                  "do it, or `uv run spark canfix` if it stays down.")
            return REFUSED

    drive_moves = args.drive_output > 0 and args.drive_pattern != "off"
    expected_deg = _expected_deg_per_tick(args.spin_output, args.free_rpm, ratio)
    jump_threshold_deg = _spin_jump_threshold(args.spin_output, args.free_rpm,
                                              ratio)
    converge_cap_s = 3 * args.settle_timeout_s + args.settle_hold_s
    if args.fine_converge:
        converge_cap_s += args.fine_timeout_s
    total_cap_s = 2 * converge_cap_s + args.spin_seconds

    steer_ids = ", ".join(str(item["steer_id"]) for item in plan)
    print(f"\nAbout to turn {len(plan)} STEER motor(s) on {iface}, "
          f"CAN ids {steer_ids}.")
    if drive_moves:
        drive_ids = ", ".join(str(item["drive_id"]) for item in plan)
        print(f"Also turning {len(plan)} DRIVE motor(s), CAN ids {drive_ids}. "
              "THE WHEELS WILL TURN.")
    for item in plan:
        drive_label = "-" if item["drive_id"] is None else item["drive_id"]
        print(f"  {item['name']}: steer id {item['steer_id']}, drive id "
              f"{drive_label}, CANcoder {item['cancoder_id']}, "
              f"offset {item['offset']:+.2f}")
    print(f"  steer to {args.target:+.2f} deg at up to {args.max_output:.2f} "
          f"duty, then spin {args.spin_seconds:.1f} s at "
          f"+/-{args.spin_output:.2f} duty, reversing every "
          f"{SPIN_FLIP_PERIOD:.1f} s, then steer back")
    if drive_moves:
        print(f"  drive motors at +/-{args.drive_output:.2f} duty for the whole "
              f"spin, pattern {args.drive_pattern}")
    print(f"  worst case {total_cap_s:.0f} s under power in total: two converge "
          f"phases of up to {converge_cap_s:.0f} s each, and the "
          f"{args.spin_seconds:.1f} s spin")
    print(f"  jump threshold {jump_threshold_deg:.1f} deg/tick, against "
          f"{expected_deg:.1f} deg/tick expected at this duty")
    print(f"  settle: mode={args.settle_mode}, window={args.settle_window_s:.2f}s, "
          f"motion={args.settle_motion_deg:.2f}, tol={args.settle_tol_deg:.2f}")
    print(f"  log: {log_path}")
    question = ("Wheels off the ground and free to roll? [y/N] "
                if drive_moves else
                "Wheels free to turn, robot on a stand? [y/N] ")
    try:
        if input(question).strip().lower() != "y":
            return 1
    except KeyboardInterrupt:
        print("\nnot confirmed; nothing was commanded")
        return 1

    if args.bring_up:
        from sparklib.netdev import CanNetdev
        can_conf = getattr(conf, "can", None)
        CanNetdev(iface,
                  wait_timeout_s=getattr(can_conf, "wait_timeout_s", 20),
                  skip_gs_usb_rebind=getattr(can_conf, "skip_gs_usb_rebind",
                                             False),
                  bitrate=getattr(can_conf, "bitrate", 1000000),
                  ).check_can_interface(adopt=True)

    gains = {"kp": args.kp, "max_output": args.max_output,
             "deadband_deg": args.deadband_deg}
    settle = {"mode": args.settle_mode, "window_s": args.settle_window_s,
              "motion_deg": args.settle_motion_deg,
              "tol_deg": args.settle_tol_deg, "hold_s": args.settle_hold_s,
              "timeout_s": args.settle_timeout_s}
    fine = {"enabled": args.fine_converge,
            "deadband_deg": args.fine_deadband_deg, "kp": args.fine_kp,
            "max_output": args.fine_max_output, "tol_deg": args.fine_tol_deg,
            "timeout_s": args.fine_timeout_s}

    product = SPARK_FLEX if product_name == "sparkflex" else SPARK_MAX
    bus = SparkBus(channel=iface)
    corner_states = []
    try:
        refusal = _init_corners(bus, plan, product, bus_name, corner_states,
                                rate_hz=args.cancoder_rate_hz)
        if refusal:
            print(refusal)
            return REFUSED
        time.sleep(0.5)
        return record_spin(corner_states, args.target, args.spin_seconds,
                           args.spin_output, args.drive_output,
                           args.drive_pattern, gains, settle, fine,
                           jump_threshold_deg, expected_deg, log_path, rig_name)
    except KeyboardInterrupt:
        return 1
    finally:
        _stop_all(corner_states)
        time.sleep(0.05)
        bus.shutdown()


if __name__ == "__main__":
    sys.exit(main())
