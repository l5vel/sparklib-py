# Driving a base from upstream code

This is the path from a driver input to four wheels turning, split into the five
stages a swerve base needs. Each stage is a plain function with no bus and no
config in it, so you can call them from a joystick loop, a ROS node, a socket
server or an autonomous planner without changing any of them.

`examples/06_joystick_drive.py` is the whole thing in one runnable file. Read
this document alongside it. Run it with `--dry-run` to watch the numbers move
before a wheel does.

## The five stages

    driver input                 -> chassis_from_stick()
    chassis velocity (m/s, rad/s)-> module_states()
    per-module speed and angle   -> desaturate()
    output that fits the motors  -> optimize()
    the short way round          -> position_output() and percent_output()

Everything above the motor calls lives in `sparklib.kinematics` and needs
nothing installed beyond the standard library.

### Stage 1: input to chassis velocity

    forward, strafe, rotate = kinematics.chassis_from_stick(
        x, y, turn, max_speed=3.0, max_angular=2.0, deadband=0.08, expo=0.4)

`x`, `y` and `turn` run from -1 to 1. The deadband applies to the length of the
translation vector, so pushing the stick diagonally gives a clean diagonal. A
per-axis deadband turns the same push into a staircase near the centre.

`expo` softens the middle of the travel and keeps full output at the ends. Start
at 0 and raise it if the base feels twitchy under small corrections.

Upstream code that already speaks in metres per second skips this stage. A ROS
`Twist` arrives as `(msg.linear.x, msg.linear.y, msg.angular.z)` and goes
straight into stage 2.

### Stage 2: chassis velocity to module states

    geometry = kinematics.module_geometry(track_width=0.55, wheel_base=0.62)
    states = kinematics.module_states(forward, strafe, rotate, geometry,
                                      max_speed=3.0, hold_angles=last_angles)

`geometry` maps each corner to its position from the robot centre, in metres,
with x positive to the left and y positive forward. Build it once at startup.
The shipped config carries a `chassis` block for these three numbers.

`states` comes back as `{label: (speed, angle_rad)}`. Speed is normalised
against `max_speed`, which puts it in the same units the motor takes.

Pass `hold_angles` the angles you commanded last tick. A module whose commanded
velocity falls to zero then keeps its heading instead of snapping to straight
ahead. A base that resets all four wheels every time the stick returns to centre
is slow to move again and startling to stand next to.

### Stage 3: desaturation

    states = kinematics.desaturate(states)

A command that mixes translation and rotation can ask one wheel for more than
full output. Scaling all four by the same factor keeps the commanded
forward:strafe:rotate ratio and gives up only overall pace.

Leaving this out is the subtle one. Each motor still clamps its own output, so
the base moves and nothing faults. What changes is the direction: clamping
chops only the saturated wheels and leaves the rest at full magnitude, so
rotation loses most when forward dominates. The base drifts wide on every turn
taken at speed, and the cause looks mechanical.

### Stage 4: the short way round

    speed, target, error = kinematics.optimize(angle, measured_rad, speed)

A module asked to turn more than a quarter turn can instead point at the
opposite heading and drive backwards. The wheel then never travels more than a
quarter turn to reach any commanded direction, which roughly halves the worst
case and takes the sting out of a reversal.

Pass the MEASURED wheel angle, read from the absolute encoder this tick. A
"last commanded" value drifts away from the wheel under a soft host-side loop,
and once it does, two modules resolve the same chassis command to opposite
headings and the base fights itself. Reading the encoder makes the decision
stateless, so it cannot desynchronise.

### Stage 5: output

    steer.position_output(cancoder.motor_rotations(math.degrees(target), ratio))
    drive.percent_output(speed)

`position_output` hands the target to the controller's own position loop, which
runs at the controller's rate.

**Check that route before you build on it.** It needs the controller's position
frame, and firmware 25 and later leave that frame off unless something asks for
it, which REV's release notes state outright. `Controller.position` then reads
0, which looks like a wheel near its zero, and the seed cannot confirm its own
write. Measured on one corner here: 0.11 deg on a host-side loop, and never
settling at all on the device loop.

`tools/spark_passive_snapshot.py` answers it in five seconds: if `0x2E2` is
missing from the frame list, close the loop on the host instead.

    duty = steer_module.percent_output(
        steer.p_output(math.degrees(error), kp=0.008, max_output=0.4))

[STEER-CONTROL.md](STEER-CONTROL.md) has the measurement, the tuning path, and
the P and PD helpers.

## Before the first setpoint

A SPARK applies output only while it is receiving an enable heartbeat, and
`SparkBus` starts that thread when it is constructed. Between construction and
your first command the controllers are enabled and holding whatever setpoint
they had, so send a zero before anything else.

Three questions are worth answering while the wheels are still still. They are
different questions, and the second and third are the ones that go unasked.

**Is it on the bus.** `bus.live_ids()` lists what has broadcast. A controller
missing here is a wiring or an id problem, and
[CAN-SETUP.md](CAN-SETUP.md) covers both.

**Will it apply output.** A controller can accept thousands of setpoint frames,
apply exactly 0.000 and draw no current, because a hard limit reads as reached
or a sticky fault is latched. `examples/03_startup_gate.py` is this check,
written to be lifted into your own startup path.

**Does it know where its wheel points.** A SPARK counts revolutions from
wherever it powered on. Seed each steer axis from its absolute encoder once,
before the loop:

    cancoder.seed_from_absolute(motor, encoder, offset_deg, gear_ratio)

Skipping the seed gives a base that steers smoothly to the wrong angle, with a
different wrong angle after every restart. [SWERVE-SETUP.md](SWERVE-SETUP.md)
covers recording the offsets that make this work.

## The loop, and leaving it

    bus = None
    try:
        bus = SparkBus(channel=iface)
        ...
        while running:
            states = kinematics.desaturate(kinematics.module_states(...))
            for label, (speed, angle) in states.items():
                ...
            time.sleep(period)
    finally:
        for motor in motors:
            motor.percent_output(0.0)
        if bus is not None:
            bus.shutdown()

Build the bus inside the `try`. It starts its heartbeat thread on construction,
so an exception thrown while opening the controllers leaves a process with
enabled motors and no path to `shutdown()`. Installing a SIGINT handler that
raises `SystemExit` routes Ctrl-C through the same `finally`.

Zero every output before shutting the bus down. `shutdown(zero_outputs=True)`
does this too, and doing it explicitly means the last frame on the wire is a
zero even if shutdown itself raises.

## Wiring it to something other than a joystick

The stages take numbers and return numbers, so the input can be anything that
produces a chassis velocity.

**A ROS 2 node.** Subscribe to `geometry_msgs/Twist`, feed
`(linear.x, linear.y, angular.z)` into stage 2, and run stages 3 to 5 on a
timer. Keep the timer period fixed and independent of message arrival, so a
publisher that stalls leaves the base holding rather than stepping.

**A network client.** Accept a velocity triple over a socket and apply the same
watchdog: if nothing has arrived for a few hundred milliseconds, command zero.
A base that keeps driving on the last packet after a link drop is the failure
this prevents.

**An autonomous planner.** A planner producing a path gives you a velocity per
tick after a tracker converts it. Stages 2 to 5 take that velocity unchanged.

**A keyboard or a web page.** Map the inputs to -1 to 1 and start at stage 1,
which gives you the deadband and the response curve for free.

Whatever the source, apply a watchdog and a bound. The bound is `max_speed`,
which you already pass to stage 2.

## Coming from WPILib

The stages map onto the Java classes one for one, which makes porting a robot
project mostly mechanical.

| WPILib | here |
| --- | --- |
| `SwerveDriveKinematics.toSwerveModuleStates` | `kinematics.module_states` |
| `SwerveDriveKinematics.desaturateWheelSpeeds` | `kinematics.desaturate` |
| `SwerveModuleState.optimize` | `kinematics.optimize` |
| `ChassisSpeeds` | the `(forward, strafe, rotate)` triple |
| `SwerveModulePosition` | read from the CANcoder through `sparklib.cancoder` |

Two differences are worth knowing before you port.

Angles here are radians measured with atan2(vx, vy), so zero points forward and
positive turns toward the left. WPILib's `Rotation2d` measures from the positive
x axis. Rotating your frame by 90 degrees once, at the boundary, is easier than
tracking the convention through every call.

Speeds here come back normalised against `max_speed` rather than in metres per
second, because the motor call takes a duty cycle. Multiply by `max_speed` if
you want the physical number for logging.

## When it moves wrong

| What you see | Where to look |
| --- | --- |
| One wheel points the opposite way to its three neighbours | That corner's `sensor_direction` or its housing orientation. Run `tools/cancoder_sensor_direction.py`. |
| Every wheel points 90 degrees off the command | `track_width` and `wheel_base` swapped in the config, or x and y swapped at the input. |
| The base turns wide at speed and tracks true when slow | Desaturation missing, or applied per wheel instead of across all four. |
| A wheel hunts back and forth around its target | Steer gains. [STEER-CONTROL.md](STEER-CONTROL.md) has the tuning path. |
| Two wheels drive opposite each other on a straight command | `optimize` reading a commanded angle instead of the measured one. |
| Correct motion that drifts a little further off after each restart | The seed. Re-record the offsets with `tools/cancoder_calibrate.py`. |

[TROUBLESHOOTING.md](TROUBLESHOOTING.md) carries the longer list, ordered by how
often each cause turns up.
