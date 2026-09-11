"""Swerve kinematics: chassis velocity in, per-module speed and angle out.

Arithmetic on the standard library alone, so this runs on a laptop and ports
cleanly into whatever language your robot code is written in.

    geom   = module_geometry(track_width=0.5, wheel_base=0.6)
    states = desaturate(module_states(forward, strafe, rotate, geom, 3.0))
    speed, target, error = optimize(angle, measured_rad, speed)

Two of these functions earn their place. `desaturate` holds a commanded motion's
shape once one wheel would clip, and `optimize` keeps every wheel within a
quarter turn of any heading. Leaving either out gives a base that moves smoothly
and goes somewhere other than where it was aimed, which reads as a mechanical
fault.

docs/INTEGRATION.md walks a joystick through all of it.
"""

import math

# Corner labels in the order a four-module chassis is usually described.
DEFAULT_CORNERS = ("LF", "RF", "LB", "RB")


def module_geometry(track_width, wheel_base, corners=DEFAULT_CORNERS):
    """{label: (x, y)} module positions from the robot centre, in metres.

    x is positive to the left, y positive forward, which is what the velocity
    terms below assume. Pass your own mapping instead for a chassis that is not
    a rectangle of four.
    """
    half_w, half_l = track_width / 2.0, wheel_base / 2.0
    layout = {
        "LF": (+half_w, +half_l),
        "RF": (-half_w, +half_l),
        "LB": (+half_w, -half_l),
        "RB": (-half_w, -half_l),
    }
    return {c: layout[c] for c in corners}


def module_states(forward, strafe, rotate, geometry, max_speed,
                  hold_angles=None, idle_epsilon=0.01):
    """{label: (speed, angle_rad)} for a chassis velocity.

    forward and strafe are m/s, rotate is rad/s. speed comes back normalised
    against max_speed, so it is a duty-cycle-shaped number in -1 to 1 before
    desaturation.

    A module whose commanded velocity is essentially zero keeps the angle in
    `hold_angles` rather than snapping to zero, because a wheel that resets to
    straight every time the stick is released is both startling and slow to
    recover from. Pass the angles you last commanded.
    """
    hold_angles = hold_angles or {}
    states = {}
    for label, (x, y) in geometry.items():
        vx = strafe - rotate * y
        vy = forward + rotate * x
        if abs(vx) < idle_epsilon and abs(vy) < idle_epsilon:
            states[label] = (0.0, hold_angles.get(label, 0.0))
            continue
        speed = math.hypot(vx, vy) / max_speed
        states[label] = (speed, math.atan2(vx, vy))
    return states


def desaturate(states):
    """Scale every module by one factor when any exceeds full output.

    Without this the motor layer clamps each wheel on its own, which chops only
    the saturated ones and leaves the rest at full magnitude. The robot then
    moves, but not in the direction asked for: rotation suffers most when
    forward dominates. Scaling all four by the same factor keeps the commanded
    forward:strafe:rotate ratio intact and only loses overall pace.
    """
    if not states:
        return states
    peak = max(abs(s) for s, _ in states.values())
    if peak <= 1.0:
        return dict(states)
    scale = 1.0 / peak
    return {m: (s * scale, a) for m, (s, a) in states.items()}


def optimize(target_rad, measured_rad, speed):
    """Take the short way round, reversing the wheel if that is shorter.

    A module asked to turn more than a quarter turn can instead turn to the
    opposite heading and drive backwards, which is never more than a quarter
    turn away. Returns (speed, target_rad) already adjusted.

    Compare against the MEASURED angle, not against the last angle you
    commanded. A stale commanded value desynchronises from the wheel, and then
    two wheels resolve the same chassis command to opposite headings and the
    base fights itself. That failure is stateless to avoid, so avoid it.
    """
    from .steer import normalize_rad

    target = normalize_rad(target_rad)
    error = normalize_rad(target - measured_rad)
    if abs(error) > math.pi / 2:
        speed = -speed
        target = normalize_rad(target + math.copysign(math.pi, error))
        error = normalize_rad(target - measured_rad)
    return speed, target, error


def chassis_from_stick(x, y, turn, max_speed, max_angular,
                       deadband=0.08, expo=0.0):
    """Joystick axes to a chassis velocity, with a deadband and optional expo.

    x is strafe right, y is forward, turn is clockwise, each -1 to 1. The
    deadband is applied to the magnitude of the translation vector rather than
    per axis, so a diagonal push is not clipped into a staircase.

    expo between 0 and 1 softens the centre: 0 is linear, 0.5 is a common
    starting point for a base that feels twitchy near neutral.
    """
    def shape(v):
        return v if not expo else (1 - expo) * v + expo * v ** 3

    mag = math.hypot(x, y)
    if mag < deadband:
        fx = fy = 0.0
    else:
        scaled = shape((mag - deadband) / (1.0 - deadband))
        fx, fy = (x / mag) * scaled, (y / mag) * scaled

    t = 0.0 if abs(turn) < deadband else shape(
        math.copysign((abs(turn) - deadband) / (1.0 - deadband), turn))

    return fy * max_speed, fx * max_speed, -t * max_angular
