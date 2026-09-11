# Calibrating a swerve module

A swerve corner has two motors doing different jobs, and only one of them needs
calibrating. The drive motor spins a wheel and open loop is enough. The steer
motor has to reach an angle and hold it, so it needs a reference to measure
against and gains to close the loop with.

This covers what to do, in order, and says plainly which steps this library can
help with and which need an absolute encoder it does not speak to.

## What has to be true, in order

Each step assumes the one above it is done. Skipping down the list is the usual
reason a module fights the other three.

| # | step | what goes wrong without it |
| --- | --- | --- |
| 1 | the magnet is bonded into the steer shaft | the absolute angle drifts after an impact and returns to normal on the bench |
| 2 | all four wheels physically aligned to one chosen zero | offsets measured against a crooked wheel are wrong by that much |
| 3 | wheel-zero offset recorded per corner | the module points somewhere other than commanded, consistently |
| 4 | sensor direction matches the mounting | the loop drives away from the target instead of toward it |
| 5 | steer gains tuned | the axis hunts, overshoots, or never arrives |
| 6 | repeatability verified | it works once and drifts over a match |

Steps 1 to 4 are about the absolute encoder. Step 5 is about the SPARK. Step 6
measures both together.

## Steps 1 to 4 need an absolute encoder, and this library does not speak to one

The SPARK's own encoder counts motor revolutions from wherever the controller
happened to power on. It cannot tell you which way the wheel is pointing, so a
steer axis needs a separate absolute sensor on the steer shaft. On most swerve
modules that is a CTRE CANcoder reading a diametric magnet, usually on its own
CAN bus at a different bitrate.

sparklib talks to SPARKs. Read your absolute encoder with whatever library it
needs, and feed the angle into the loop below. Two cautions that cost teams
whole seasons:

**Bond the magnet in.** It ships as a bare press fit into the end of the steer
shaft. Unbonded, it counter-rotates under impact and the encoder then reports the
magnet's angle rather than the wheel's. The signature is an intermittent
misalignment that appears during a match and reads correct on the bench
afterwards. Treat "it fixes itself when we look at it" as a magnet symptom.

**Record the offset against a wheel you have actually aligned.** Point every
wheel the same way by eye and by straight edge first, then sample the encoder and
write down what it reads at that position. That reading is the offset. Measuring
an offset against a crooked wheel bakes the crookedness in.

## Step 5, tuning the steer loop, is SPARK work

Close the loop on the host, in your own code, against the absolute angle. Four
numbers control it, and they interact:

| knob | what it does | symptom when it is wrong |
| --- | --- | --- |
| `kp` | duty cycle per unit of angle error | too high hunts, too low never arrives |
| `max_output` | ceiling on commanded duty, whatever the error | too high draws current and trips limits, too low stalls against friction |
| `deadband` | error below which the loop commands zero | too small buzzes at rest, too large leaves a standing error |
| `slew` | largest change in duty per tick | absent, a step from zero to full is what turns a warning into a tripped breaker |

`examples/02_drive_and_steer.py` is that loop in about fifteen lines, and
`examples/04_swerve_module.py` wraps it as a reusable class.

[STEER-CONTROL.md](STEER-CONTROL.md) covers the choice this assumes: whether to
close the loop on the device or on the host, and why the two rigs here differ.

**Tune one corner at a time, on a stand.** Sweep a grid rather than guessing:
step the axis through a repeatable angle change, several times per combination,
and score each on how fast it converged, how far it overshot, and whether it
settled. Start with a low `kp` and raise it until overshoot appears, then back
off. A useful grid is a handful of `kp` values, two or three `max_output`
ceilings, and two deadbands.

Score on convergence, not on looks. What you want is: reached the target within
the deadband, stayed there, and did not saturate on the way. A combination that
arrives fast and overshoots is worse than one that arrives smoothly, because the
overshoot is where the current spike lives.

Gains from a grid sweep transfer to the live loop only if the sweep uses the same
arithmetic the live loop does. Run the candidate gains through the same function
your robot code calls, not a re-implementation of it.

## Step 6, verifying it holds

Tuned once is not the same as repeatable. Command the same angle change many
times and look at the spread, not the mean. A module that converges to within a
degree on average and occasionally lands five degrees out has a mechanical
problem that gains will not fix.

Two things to watch while it runs, both of which sparklib reports:

**Sticky warnings.** `spark faults` shows what a controller latched since it was
last cleared. An overcurrent warning appearing only at high `max_output` is the
limiter doing its job. One appearing at low output is a mechanical bind or a
hardware fault. Read the bits before `spark clear` erases them.

**Whether the module can move at all.** A steer controller held by a hard limit
accepts every setpoint and applies exactly nothing. Run the gate in
`examples/03_startup_gate.py` before a tuning run, or spend an afternoon tuning
against a motor that was never going to turn.

## Slip, which looks like bad tuning and is not

If the steer chain slips, the motor turns and the wheel does not follow. Command
a small slow ramp and watch the absolute angle: a rigid chain gives a smooth
monotonic response, and a slipping one gives a stepped or lagging one that
catches up in jumps. Tuning cannot fix it and higher gains make it worse.

The same test separates a slipping chain from an unbonded magnet. A magnet slip
shows up as an offset that changed since the last alignment. A chain slip shows
up during the ramp itself.

## What to record when you are done

Per corner: the wheel-zero offset, the sensor direction, and the four gains. Keep
them in one file next to the rest of your configuration, because the day a module
is rebuilt you will want to know what the old one was set to. `spark snapshot
--write` records the SPARK side of that, and `spark audit` tells you afterwards
when a controller has drifted from it.
