# Steering a swerve module with a SPARK

A drive motor spins a wheel and open loop is enough. A steer motor has to reach
an angle and hold it against the wheel trying to push it somewhere else, so
something has to close a loop. There are two places to close it, the SPARK gives
you both, and the two rigs behind this repository use one each.

## The two places to close the loop

**On the device.** Send a position setpoint and let the SPARK's own PID run it.
The controller compares against its internal encoder at the DSP's rate, far
faster than anything you can do over CAN, and keeps working if your host stalls.
`position_output` sends that setpoint.

**On the host.** Read the angle, compute a duty cycle, send it, repeat. Slower,
because your loop rate is the rate you send frames at, and it stops the instant
your process does. In exchange every term is visible in your own code, and you
can change the gains without touching the controller.

| | on-device PID | host-side duty loop |
| --- | --- | --- |
| loop rate | the controller's, in kHz | yours, typically 20 to 100 Hz |
| what you send | `position_output(rotations)` | `percent_output(duty)` |
| where gains live | in controller flash, set over USB-C or REVLib | in your code |
| survives a host stall | yes, holds the last setpoint | no, output persists then times out |
| visible when wrong | the setpoint and the reported position | every term |
| feedback source | the controller's own encoder | whatever you read |

## Check the position frame before you choose the device route

The on-device route needs the controller's position, and on a SPARK Flex that
reaches you in frame `0x2E2`. `Controller.position` and `Controller.velocity`
both decode from it.

**On firmware 25 and later that frame is off unless something asks for it.**
REV's own release notes for sparkflex-25.0.0 and sparkmax-25.0.0 say so:

> Only sends periodic CAN frames if they are needed (except for frames 0 and 1,
> which are enabled by default)

So a 25+ controller silent on Status 2 through 9 is behaving as designed.
[FAILURE-CATALOGUE.md](FAILURE-CATALOGUE.md) carries the release-note reading,
and `docs/runs/rig-flex-probe-log.md` records that `set_periodic_frame_period`
is a no-op on Flex 26.1.6 at either arbitration base, so a host cannot turn it
back on over CAN.

Nothing announces the consequence. `Controller.position` returns 0, which is a
plausible reading for a wheel near its zero, and `set_encoder_position` cannot
confirm its own write.

What it costs is measurable. The same corner, the same target, the two routes:

| route | steady-state error | settling time | verdict |
| --- | --- | --- | --- |
| host, closed on the CANcoder | 0.11 deg | 0.35 s | converged and held |
| on-device position PID | 43.78 deg | never settled | oscillating |

`uv run python tools/steer_pid_check.py --corner LF --target-deg 45 --mode host`
and the same command with `--mode onboard` produce those two rows on your own
rig. `tools/spark_passive_snapshot.py` answers the prior question in five
seconds: if `0x2E2` is absent from the frame list, the device route has no
feedback and the host route is the one that works.

A SPARK MAX below firmware 25 broadcasts its position frame, which is why that
rig uses the device route and this one does not. Both halves are measured:

| rig | firmware | position frame | on-device route |
| --- | --- | --- | --- |
| MAX | 24.0.1 | 0x062, broadcast | 2.34 deg in 0.85 s |
| Flex | 26.1.6 | 0x2E2, absent | 43.78 deg, never settled |

So this is a property of the firmware generation rather than of either rig.

## What the two rigs here do, and why they differ

**The SPARK MAX rig** drives SDS MK2 modules at a 12.8:1 steer reduction and uses
the **on-device position PID**. The module class sends `position_output(target)`
in motor rotations and the controller does the rest. Host-side gains exist in its
config, but only the bring-up and zeroing tools use them.

Measured on that rig, running firmware 24.0.1: a corner commanded 53 deg away
settled in 0.85 s and held to 2.34 deg, with no overshoot and no sign changes.
The standing error is a proportional-only loop stopping short, which the
provisioned slot-0 P of 0.1 produces, and it is inside the 3 deg tolerance. Its
position frame, api 0x062, is on the wire, which is what makes the route
possible.

**The SPARK Flex rig** drives SDS MK5i modules at a 26:1 steer reduction and
closes the loop **host-side**, in proportional control with an optional
derivative term. It sends `percent_output`. The on-device PID slot is configured
on those controllers and is not what steers them.

The honest reason for the difference is history rather than a finding: the
on-device loop was never wired up on the Flex side, and the host-side loop worked
well enough that nobody went back. It is worth knowing because the Flex rig's
declared `slot0_p` value is live configuration that nothing reads, which is
exactly the kind of thing an audit flags and a reader misinterprets.

If you are choosing today: start on the device. Fall back to the host loop when
you want the gains in version control, or when your angle source is not the
controller's own encoder.

## The gear ratio is the units conversion, and it bites

`position_output` speaks **motor rotations**, not degrees of wheel angle. Between
the two sits the steer reduction, so:

    motor_rotations = (angle_deg / 360) * steer_gear_ratio

At 26:1 a quarter turn of the wheel is 6.5 motor rotations. At 12.8:1 it is 3.2.
Get the ratio wrong and the axis moves confidently to the wrong place, which
reads as a tuning problem and is not one. Take the number from your module's
documentation rather than from another robot's config, because it is the one
value that differs between two modules that otherwise look identical.

The same conversion applies to the host-side loop. The error you feed the
proportional term is in degrees of wheel angle, and the duty you send is
dimensionless, so `kp` carries the units: duty per degree.

## The absolute encoder seeds the loop; it is usually not the feedback

This is the part that surprises people, and it is why a swerve module needs an
absolute encoder even though the SPARK never reads one.

The SPARK's encoder is incremental. It counts from wherever the controller
powered on, so at boot it knows how far the axis has turned and not which way the
wheel points. An absolute sensor on the steer shaft, usually a CTRE CANcoder
reading a diametric magnet, supplies that missing reference **once**:

1. At startup, read the absolute angle.
2. Subtract the recorded wheel-zero offset to get true wheel angle.
3. Convert to motor rotations and write it with `set_encoder_position`.
4. From then on, the loop runs on the controller's own encoder.

So the absolute encoder is a seed, not the feedback path. What it buys is the
loop closing on the right quantity: without it you have closed-loop control of
motor position and open-loop control of wheel angle, which is the one you steer
by. That matters three ways. It explains why an unbonded magnet produces an error that survives a reboot
and vanishes on the bench. It explains why the absolute sensor can sit on a
different CAN bus at a different bitrate without hurting loop rate. And it is why
this library does not speak to one: the seed arrives once, from whatever library
owns that sensor, and everything after it is SPARK work.

A host-side loop can use the absolute angle directly as feedback instead, which
skips the seeding step and costs you loop rate, since you are then limited by how
fast that sensor reports.

## Tuning the host-side loop

Four numbers, and they interact. `docs/SWERVE-CALIBRATION.md` covers the method;
this is what each one is.

| knob | units | what goes wrong |
| --- | --- | --- |
| `kp` | duty per degree | too high hunts, too low never arrives |
| `max_output` | duty ceiling | too high spikes current, too low stalls against friction |
| `deadband` | degrees | too small buzzes at rest, too large leaves standing error |
| slew | duty per tick | absent, a step from zero to full is what trips a breaker |

Two gain sets are worth having: a coarse one for tracking while the robot is
moving, and a gentler one for the final approach where overshoot costs accuracy.
Switch between them on the state your navigation is in.

**`max_output` is a current limit in disguise.** The fuse carries supply current,
which is duty times motor current, so capping duty caps what the fuse sees. On a
40 A fuse at 0.40 duty a 150 A motor draw is about 60 A through the fuse. Lower
this knob before touching the SPARK's Smart Current Stall Limit, which the fuse
already bounds.

A derivative term damps the overshoot that a high proportional gain produces, and
it stops helping at low loop rates: at 20 Hz the difference between successive
error samples is mostly quantisation, so the term adds noise rather than damping.
Leave it at zero until the loop runs fast enough to earn it.

## Before the first setpoint

A steer controller held by a hard limit accepts every setpoint and applies
exactly nothing, so check before you command. `examples/03_startup_gate.py` is
that check, and [../README.md](../README.md) explains what it looks at and why.

## One module, or a whole chassis

Everything above steers one axis to an angle you chose. Choosing those angles
for four corners at once is inverse kinematics, and it lives in
`sparklib.kinematics`:

    states = kinematics.desaturate(
        kinematics.module_states(forward, strafe, rotate, geometry, max_speed))

Each corner comes back with a speed and an angle, and each angle goes into the
loop this document describes. [INTEGRATION.md](INTEGRATION.md) walks the whole
path from a driver input, and `examples/06_joystick_drive.py` runs it.

Two of those functions matter more than their size suggests. `desaturate` keeps
a mixed translate-and-rotate command pointing where it was aimed once one wheel
would clip. `optimize` lets a module reach any heading within a quarter turn, by
pointing at the opposite heading and driving backwards. Both are easy to leave
out, and both produce motion that feels wrong instead of obviously broken.

## The modules behind these numbers

- SDS MK5i, 26:1 steer reduction, on the SPARK Flex rig:
  https://www.swervedrivespecialties.com/products/mk5i-swerve-module
- SDS MK2, 12.8:1, on the SPARK MAX rig.

Take the ratio from your own module's page. Two modules that look identical from
across a workshop can differ here, and the number is the units conversion every
steer command passes through.
