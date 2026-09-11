"""Per-tick anomaly detection for a steer axis under closed-loop control.

Append one sample per control tick and this names the three ways a corner goes
wrong while every frame on the bus still looks healthy:

    JUMP     the encoder moved further in one tick than the motor could have
    FIGHT    the controller applies output opposite to what was commanded
    DIVERGE  the error grows for long enough that the loop is not converging

Each one is cheap to check and loud when it fires, so it can stay on for a whole
run. The alternative is reading a log of the numbers afterwards and spotting a
sign flip by eye.

    trace = CornerTrace("LF", gear_ratio=26.0, max_motor_rpm=6784)
    trace.set_target(45.0)
    trace.sample(raw_rev, abs_deg, wheel_deg, err, cmd, "%", applied)

Pure arithmetic on numbers you already have. Nothing here touches a bus.
"""

import time
from collections import deque

JUMP_FLOOR_DEG = 5.0
FIGHT_DURATION_S = 0.10
DIVERGE_DURATION_S = 0.20
DIVERGE_MIN_GROWTH_DEG = 1.0
BUFFER_SIZE = 256


class CornerTrace:
    """One corner's recent history, checked for anomalies as it fills.

    Thresholds suit a swerve steer axis sampled near 100 Hz. Give it
    `gear_ratio` and `max_motor_rpm` and the jump threshold scales with the
    commanded duty, because a wheel slewing at full output genuinely covers
    ground in a tick and a fixed threshold calls that a fault.
    """

    def __init__(self, name, verbose=False, gear_ratio=None,
                 max_motor_rpm=None, load_derate=0.8, tick_period_s=0.01,
                 jump_floor_deg=JUMP_FLOOR_DEG, jump_factor=4.0, out=print):
        self.name = name
        self.verbose = verbose
        self.buf = deque(maxlen=BUFFER_SIZE)
        self.events = []
        self._out = out
        self._fight_start = None
        self._diverge_start_err = None
        self._diverge_start_t = None
        self._t0 = time.time()
        self._gear_ratio = gear_ratio
        self._max_motor_rpm = max_motor_rpm
        self._load_derate = load_derate
        self._tick_period_s = tick_period_s
        self._jump_floor_deg = jump_floor_deg
        self._jump_factor = jump_factor

    def set_target(self, target_deg):
        """Reset the divergence and fight baselines for a new setpoint.

        A change of target steps the error legitimately, and the sign of the
        command can flip with it. Call this whenever you command a new angle and
        both detectors judge the new move on its own terms.
        """
        self._diverge_start_err = None
        self._diverge_start_t = None
        self._fight_start = None

    def _report(self, kind, message):
        self.events.append((kind, message))
        self._out(f"  [{self.name} {kind}] {message}")

    def _jump_threshold_deg(self, cmd, cmd_unit):
        """The per-tick motion budget, scaled by commanded duty where it can be."""
        if (cmd_unit != "%" or cmd is None or self._gear_ratio is None
                or self._max_motor_rpm is None):
            return self._jump_floor_deg
        motor_rpm = self._max_motor_rpm * abs(cmd) * self._load_derate
        wheel_deg_s = (motor_rpm / 60.0) / self._gear_ratio * 360.0
        return max(self._jump_floor_deg,
                   self._jump_factor * wheel_deg_s * self._tick_period_s)

    def sample(self, raw_rev, abs_deg, wheel_deg, err, cmd, cmd_unit, applied):
        """Record one control tick and check it against the previous one."""
        t = time.time() - self._t0
        previous = self.buf[-1] if self.buf else None
        this = (t, raw_rev, abs_deg, wheel_deg, err, cmd, cmd_unit, applied)
        self.buf.append(this)

        if self.verbose:
            shown = float("nan") if applied is None else applied
            self._out(f"  [{self.name}] t={t:6.3f}  raw_rev={raw_rev:+.6f}  "
                      f"abs={abs_deg:+7.2f}  wheel={wheel_deg:+7.2f}  "
                      f"err={err:+7.2f}  cmd={cmd:+.3f}{cmd_unit}  "
                      f"applied={shown:+.3f}")

        if previous is not None:
            self._check_jump(previous, this)
            self._check_fight(t, cmd, applied)
            self._check_divergence(t, err)

    def _check_jump(self, previous, this):
        delta_rev = this[1] - previous[1]
        if delta_rev > 0.5:
            delta_rev -= 1.0
        elif delta_rev < -0.5:
            delta_rev += 1.0
        delta_deg = delta_rev * 360.0
        threshold = self._jump_threshold_deg(this[5], this[6])
        if abs(delta_deg) > threshold:
            dt_ms = (this[0] - previous[0]) * 1000.0
            self._report("JUMP",
                         f"t={this[0]:6.3f}  raw_rev {previous[1]:+.6f} to "
                         f"{this[1]:+.6f}  d={delta_deg:+.2f} deg in "
                         f"{dt_ms:.1f} ms  (threshold {threshold:.1f})")

    def _check_fight(self, t, cmd, applied):
        if applied is None or cmd is None:
            self._fight_start = None
            return
        opposed = ((cmd > 0.01 and applied < -0.01)
                   or (cmd < -0.01 and applied > 0.01))
        if not opposed:
            self._fight_start = None
            return
        if self._fight_start is None:
            self._fight_start = t
        elif t - self._fight_start > FIGHT_DURATION_S:
            self._report("FIGHT",
                         f"t={t:6.3f}  cmd={cmd:+.3f}  applied={applied:+.3f}  "
                         f"for {t - self._fight_start:.2f}s")
            self._fight_start = t

    def _check_divergence(self, t, err):
        magnitude = abs(err)
        if self._diverge_start_err is None or magnitude < self._diverge_start_err - 0.5:
            self._diverge_start_err = magnitude
            self._diverge_start_t = t
            return
        growth = magnitude - self._diverge_start_err
        elapsed = t - self._diverge_start_t
        if growth > DIVERGE_MIN_GROWTH_DEG and elapsed > DIVERGE_DURATION_S:
            self._report("DIVERGE",
                         f"t={t:6.3f}  err {self._diverge_start_err:+.2f} to "
                         f"{magnitude:+.2f} deg over {elapsed:.2f}s")
            self._diverge_start_err = magnitude
            self._diverge_start_t = t

    def latest(self):
        """The most recent sample, or None before the first one."""
        return self.buf[-1] if self.buf else None

    def dump_tail(self, n=10):
        """Print the last n samples, for the moment after something fired."""
        self._out(f"  [{self.name} last {min(n, len(self.buf))} samples]")
        for t, raw_rev, abs_deg, wheel_deg, err, cmd, unit, applied in list(self.buf)[-n:]:
            shown = " None " if applied is None else f"{applied:+.3f}"
            self._out(f"    t={t:6.3f}  raw={raw_rev:+.5f}  abs={abs_deg:+7.2f}  "
                      f"wheel={wheel_deg:+7.2f}  err={err:+7.2f}  "
                      f"cmd={cmd:+.3f}{unit}  applied={shown}")
