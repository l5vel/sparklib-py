"""Host-side steer loop primitives, and the convergence test for tuning one.

A steer axis closed on the host is four numbers and a wrap rule. This module is
those, as plain functions, so a tuning sweep and the live loop can share the same
arithmetic. Gains tuned against a re-implementation do not transfer.

    duty = p_output(error_deg, kp=0.008, max_output=0.40, deadband_deg=0.5)
    duty = slew(commanded, duty, max_step=0.04)

Nothing here touches a bus or needs the swerve extra. docs/STEER-CONTROL.md
covers when to use this instead of the SPARK's own position loop.
"""

import math

WRAP_DEG = 180.0


def normalize_deg(angle_deg):
    """Wrap an angle to (-180, 180].

    The seam is closed at the top, so half a turn reads +180 and never -180.
    cancoder.wheel_angle_deg answers the same way, and a steer path that mixes
    the two conventions sends a wheel the long way round at exactly that angle.
    """
    a = (angle_deg + WRAP_DEG) % 360.0 - WRAP_DEG
    return WRAP_DEG if a == -WRAP_DEG else a


def normalize_rad(angle_rad):
    """Wrap an angle to (-pi, pi], with the seam closed at the top."""
    a = (angle_rad + math.pi) % (2 * math.pi) - math.pi
    return math.pi if a == -math.pi else a


def shortest_error_deg(target_deg, actual_deg):
    """Signed error by the shorter way round, which is the one to steer."""
    return normalize_deg(target_deg - actual_deg)


def p_output(error_deg, kp, max_output, deadband_deg=0.0):
    """Duty cycle for a proportional steer loop. Zero inside the deadband."""
    if abs(error_deg) < deadband_deg:
        return 0.0
    return max(-max_output, min(max_output, kp * error_deg))


def p_controller(kp, max_output, deadband_deg=0.0):
    """A p_output closure holding one set of gains.

    A run that uses different gains for different manoeuvres builds one of
    these per manoeuvre, which keeps the gains next to the motion they belong
    to instead of in a module constant something else also reads.
    """
    def output(error_deg):
        return p_output(error_deg, kp, max_output, deadband_deg)
    return output


def pd_output(error_deg, derror_dt_deg_s, kp, kd, max_output, deadband_deg=0.0):
    """P plus D. D damps the overshoot a high kp produces.

    At low loop rates the difference between error samples is mostly
    quantisation, so kd adds noise rather than damping. Leave it at zero until
    the loop runs fast enough to earn it.
    """
    if abs(error_deg) < deadband_deg:
        return 0.0
    out = kp * error_deg + kd * derror_dt_deg_s
    return max(-max_output, min(max_output, out))


def slew(commanded, wanted, max_step):
    """Move `commanded` toward `wanted` by at most `max_step`.

    A step from zero to full is what turns an overcurrent warning into a
    tripped breaker, because the fuse sees duty times motor current.
    """
    step = max(-max_step, min(max_step, wanted - commanded))
    return commanded + step


def settled(history_deg, current_error_deg, mode="either",
            motion_deg=0.15, tol_deg=1.0):
    """Has the axis arrived? Three rules, because one of them can deadlock.

    history_deg is a deque of recent angle readings, sized by the caller to the
    window it wants; this returns False until it is full.

    mode:
      "static"  the readings stopped moving, whatever the error says. Survives
                the case below.
      "tol"     the error is inside tol_deg. Deadlocks when deadband_deg is
                larger than tol_deg, because the loop commands zero before the
                error is ever small enough to accept.
      "either"  whichever fires first, and the tolerance rule applies from the
                first tick, before the window has filled.
    """
    if mode == "tol":
        return abs(current_error_deg) < tol_deg
    if mode not in ("static", "either"):
        raise ValueError(f"unknown settle mode: {mode!r}")
    if history_deg.maxlen is None or len(history_deg) < history_deg.maxlen:
        return mode == "either" and abs(current_error_deg) < tol_deg
    stopped = (max(history_deg) - min(history_deg)) < motion_deg
    if mode == "static":
        return stopped
    return stopped or abs(current_error_deg) < tol_deg


def score_step(samples, target_deg, deadband_deg, settle_tol_deg=1.0):
    """Score one commanded angle step. Lower is better.

    samples is [(t_seconds, angle_deg, duty)], one per tick, from the moment the
    step was commanded.

    Returns a dict with the four numbers worth comparing across a gain grid:
    time to settle, peak overshoot past the target, the error it came to rest
    at, and how long it spent saturated at the duty ceiling.
    """
    if not samples:
        return {"settle_s": None, "overshoot_deg": None,
                "final_error_deg": None, "saturated_frac": None, "cost": None}

    t0 = samples[0][0]
    errors = [(t - t0, shortest_error_deg(target_deg, a), d) for t, a, d in samples]
    final_error = errors[-1][1]

    settle_s = None
    for t, e, _ in errors:
        if abs(e) <= max(settle_tol_deg, deadband_deg):
            settle_s = t
            break

    approach = errors[0][1]
    overshoot = 0.0
    for _, e, _ in errors:
        if approach > 0 and e < 0:
            overshoot = max(overshoot, -e)
        elif approach < 0 and e > 0:
            overshoot = max(overshoot, e)

    duties = [abs(d) for _, _, d in errors]
    ceiling = max(duties) if duties else 0.0
    saturated = sum(1 for d in duties if ceiling and d >= ceiling - 1e-9) / len(duties)

    # Settling time dominates; overshoot and standing error are the tie-breaks.
    cost = ((settle_s if settle_s is not None else errors[-1][0] * 2)
            + 0.05 * overshoot
            + 0.05 * abs(final_error)
            + 0.5 * saturated)
    return {"settle_s": settle_s, "overshoot_deg": overshoot,
            "final_error_deg": final_error, "saturated_frac": saturated,
            "cost": cost}
