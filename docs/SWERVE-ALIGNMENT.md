# Physical Alignment Procedure

This is step 2 of the [README.md setup workflow](SWERVE-SETUP.md).
Goal: get all four wheels physically pointing in your chosen "wheel-zero"
direction so calibration in step 3 produces sane offsets.

Skip ahead to "Procedure" if you've done this before. If it's your first
time, read "What we're doing" first.

---

## What we're doing

A swerve corner has two motors:

- **Steer** -- rotates the wheel about a vertical axis (changes which way
  the wheel points). Goes through a 26:1 reducer to the wheel hub. Has a
  cancoder on the steer shaft reporting absolute angle.
- **Drive** -- spins the wheel about its horizontal axle (makes it roll).
  No absolute encoder -- just the motor's internal incremental encoder.

The cancoder reads "absolute degrees of the steer shaft." There is **no
preferred zero** built into the hardware: when the cancoder ships, its
zero is arbitrary relative to where you mounted the magnet on the shaft
and where the wheel is bolted to the hub.

Calibration is the act of saying: "when the wheel is pointing in *this*
physical direction, call that 0 deg." Whatever cancoder value happens to be
there at that moment becomes the corner's `*_OFFSET`. The control loop
then subtracts the offset to get wheel-frame degrees.

This file describes how to choose and physically achieve the zero
direction so all four corners agree.

---

## Choosing the zero direction

By convention in this codebase: **all four wheels point straight forward,
parallel to each other, with the drive face leading the way.** "Forward"
is the direction the robot drives when commanded `+vx, vy=0, omega=0`.

If your robot has a clear "front" (battery in back, sensors in front),
use that. If it's symmetric, pick the side facing the workbench when
you're calibrating -- you'll need it accessible.

The choice of forward matters once and only once. After calibration, all
software targets are in wheel-frame degrees relative to this zero, so as
long as you're consistent, you're fine.

---

## Procedure

### Materials
- A straightedge (a metal ruler, 12+ inches; or any rigid straight bar)
- Optional: a printed protractor or a digital level
- Wood blocks / wedges / clamps to physically hold a wheel in place
  (you'll need them in step 3 of the README)

### Steps

**1. Place the robot on a flat surface or jack stands.**

If on the floor, you'll be rotating the wheels by hand against the
ground -- easier with the wheels lifted clear (jack stands, ramps, or
flipped on its back if light enough).

**2. Visually pre-align all four wheels to "forward."**

By eye, rotate each wheel about its steer axis until it looks like it's
pointing forward. This gets you within 5-10 deg. Don't worry about precision
yet.

**3. Align each axle pair using the straightedge.**

Lay the straightedge flat against the outer face of one front wheel and
extend it back. Slide the straightedge until it also touches the outer
face of the rear wheel on the same side. The two wheels are now parallel
to each other along that side.

Repeat for the opposite side.

```
   Front of robot
        ^
   +---------+
   |         |
LF |         | RF       <- straightedge runs from LF to LB
   |         |             on the outer side; both wheels
   |         |             flush with it
LB |         | RB
   |         |
   `---------+
```

If wheel-zero means "wheels parallel to robot fore-aft axis," the front
of the LF/LB straightedge should also be parallel to the robot's
front/back midline. If you're unsure, sight along the robot from
overhead.

**4. Cross-check with the front pair.**

Lay the straightedge across the front faces of LF and RF. The straightedge
should be parallel to the robot's left-right axis. If LF or RF is
twisted, you'll see a gap.

**5. Lock the wheels in place.**

Clamp/wedge each wheel so it cannot rotate while you run the calibration
script. Even small movements (0.5 deg from a wheel jiggling) will introduce
calibration noise.

A simple method: jam wood blocks between the chassis and tire, one on
each side, so the wheel cannot rotate either direction. Or use a pair of
spring clamps gripping the tire.

**6. Verify alignment by eye + straightedge one more time.**

Tightening the clamps can shift the wheel by 1-2 deg. Re-check.

**7. Now run step 3 in [README.md](SWERVE-SETUP.md).**

While the wheels are clamped, run `uv run python tools/cancoder_calibrate.py`.
The script samples each cancoder for ~1 second per corner; jitter should
be sub-1 deg if mechanical lock is good.

---

## Common alignment mistakes

**The wheel "looks" parallel but is actually 5 deg off.**
Eyeballing is unreliable past 1-2 deg. If the offsets that come out of
calibration look weird (e.g., one corner is 30 deg different from its
mirror), it's almost always alignment, not the script.

**Wheel rotates during calibration.**
Causes the captured offset to be the average of where the wheel was
during sampling. Symptom: high jitter (>1 deg) in the calibration output.
Fix: better mechanical lock.

**Different wheels at different "zero" directions.**
If you align LF/LB to "forward" but accidentally align RF/RB to "back"
(rotated 180 deg), driving will have one side going forward and one
backward. Symptom: when you command forward translation, the robot
spins. Fix: physically inspect each wheel and verify the drive face is
on the same side for all four.

**Calibrating with the robot on its back.**
Gravity is now on the steer axis instead of the drive axis. Wheels can
flop. If you must, stabilize each wheel as if it's in normal orientation.

---

## Hard physical reference (the most important step nobody does)

The single biggest source of "calibrations don't match yesterday" is using
a **mental model** of forward instead of a **physical reference** that
lives on the chassis. Eyeballing forward is reliable to about +/-5 deg. Across
two calibration sessions a week apart, it drifts more.

Make forward a property of the chassis, not your memory:

1. Pick a chassis edge or beam that's known to be parallel to the
   robot's intended forward direction (the long edge of the deck, the
   centerline beam, etc.).
2. Apply two pieces of tape to that edge -- one near the front, one near
   the back of that side. Draw a line on each tape that's exactly aligned
   with the edge.
3. Do this once. Treat the tape as the calibration reference for all
   future sessions. If the tape ever falls off or gets bumped, re-mark
   it before any calibration.

When calibrating each wheel:

1. Lay a straightedge from the chassis tape to the wheel hub face.
2. Adjust the wheel until its hub face is parallel with the straightedge.
3. Lock mechanically (clamps).
4. Run `uv run python tools/cancoder_calibrate.py --corner <name>`.

This eliminates "calibrations don't match yesterday" as a class of
failure. The chassis is the truth; the calibration just records
"cancoder reads X when the wheel is parallel to chassis-truth."

The same chassis tape is used to *verify* alignment after a calibration:

1. After updating offsets in motor_test.py, drive each wheel to 0 deg via
   stress_test (with `--fine-converge`).
2. Lay the straightedge from chassis tape to each wheel hub.
3. Each wheel hub face should be parallel to the straightedge.

If a corner is off the straightedge by more than 1 deg, either:
- The offset captured a wrong physical reference (re-do the calibration
  for that corner with the straightedge).
- The wheel-on-shaft coupling slipped between calibration and now (mechanical;
  see `tools/cancoder_audit.py --corner <name> --rotate-test`).

The rotate test compares the cancoder against an angle you set by hand, so
operator estimation bounds its accuracy. On this bench that error reached roughly
plus or minus 20 degrees of the measured delta. No operator hits the 1.0 degree
`ROTATE_TOLERANCE` by hand, so a slip verdict from it stays ambiguous. Use it for
gross checks of sign and rough 1:1 tracking, and settle a suspected slip with
`tools/steer_slip_check.py`, which creeps the steer at 4 percent duty and
samples the cancoder at 200 Hz looking for stair-steps.

## After calibration

Once `*_OFFSET` values are written into [motor_test.py](../sparklib/data/spark.yaml),
you should be able to release the wheels (unclamp them). Run:

```
uv run python tools/cancoder_config_check.py --corner all
```

The `wheel_deg` line for each corner should read close to 0 deg (within
deadband ~1.5 deg). If not, that corner's offset is wrong by however many
degrees `wheel_deg` reads -- re-clamp and re-calibrate that corner.
