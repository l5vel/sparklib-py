# rig-max after a rail cycle, and a declared value that was wrong, rig-max, eight SPARK MAX on firmware 24.0.1, bus `can0`.
Nothing was recorded before the rail came on, so nothing here can be attributed
to what happened before it. Terminal capture:
`records/rig-max-post-rail-cycle-20260910.log`.

## The last protocol question is settled

`both.param_read_frames` recorded READ_PARAMETER and GET_PARAMETER_TYPES as
frames only firmware 25 and later carries. The evidence was four sends to rig-max
that drew no answer, and two of those four were remote frames carrying dlc 0.
Once rig-flex showed that dlc 0 is a silent form on a Flex, those two sends could
no longer tell an absent frame from a badly formed one.

`tools/spark_read_frame_form.py --id 3` sent both apis in all three forms:

    READ_PARAMETER, class 19       zero-length DATA   SILENT
                                   REMOTE dlc 0       SILENT
                                   REMOTE dlc 8       SILENT
    GET_PARAMETER_TYPES, class 13  zero-length DATA   SILENT
                                   REMOTE dlc 0       SILENT
                                   REMOTE dlc 8       SILENT

Six sends, no answer. So the frames are absent on 24.0.1, measured across the
form axis rather than inferred from two sends that happened to use a form now
known to be silent elsewhere.

## Parameter 9 was wrong in the declared file, and the tooling acted on it

`the sparkmax: block` declared Closed Loop Control Sensor as NONE, with
a comment recording that it "Reads Sensor.NONE on all eight".
`spark provision --id 3` read 4294967295 instead, called it drift, and
`--write` wrote 0 over it. The write reported landed and the read-back agreed.

Reading the parameter across the fleet settled which side was wrong:

    id      1           2           3     4           5           6           7           8
    p9  4294967295  4294967295      0  4294967295  4294967295  4294967295  4294967295  4294967295

Seven controllers agree on the all-ones word, and the only controller holding 0
is the one that had just been written. So 4294967295 is what a working rig-max
holds, the note is wrong, and this was never drift.

The declared file now records 4294967295 as measured, which is what a working
fleet holds, so no command writes it. It is deliberately not written as an enum
name, for the reason below.

## The id mapping, settled by writing values and watching RHC2 follow

The USB session is ruled out as a cause first, with a control. id 1 read
4294967295 before RHC2 was connected and 4294967295 again after it was
disconnected, with nothing else done in between. That was the operator's own
first hypothesis and it is eliminated by measurement rather than by argument.

Then the write test, on a drive controller so steering stayed untouched:

    parameter 9 holds        RHC2 shows
    4294967295               no option selected
    0                        None
    1                        Primary Encoder

So id 9 IS Closed Loop Control Sensor on 24.0.1, and the enum is what the
firmware-25 table says. What the fleet holds is a value outside that enum, which
RHC2 cannot render and leaves blank.

An earlier reading here called that blank field None and it was wrong. Blank and
None are different states, and only writing the values apart separated them. The
correction came from the operator, not from the tooling.

This is the same shape as the Boolean finding on rig-flex: a SPARK stores a value
its own enum does not define, and every layer above it reads that as if it were a
setting. It is catalogued there as A9.

RHC2's read was intermittent through the session, failing and then succeeding on
reconnect, and it did that for default and custom values alike. Recorded as
noise, not as a finding.

The write test never named which controller took the 0 and the 1, so the fleet
was read again afterwards to be sure nothing was left written:

    id      1           2           3           4           5           6           7           8
    p9  4294967295  4294967295  4294967295  4294967295  4294967295  4294967295  4294967295  4294967295

All eight hold the all-ones word, id 3 included, so both the provision write and
the mapping test are gone. That is a second fleet-wide read with no outlier,
which is stronger evidence for the declared value than the first one was: the
first had id 3 sitting at 0 and had to argue that the single written controller
was the anomaly.

## It matters less than the finding first suggested

An earlier draft of this file said rig-max's steering depends on parameter 9 and
called for a tracking test. Reading the drivetrain shows it does not.

`SwerveModule._read_wheel_rad` closes the steer loop in code, on the CANcoder,
over the CANivore bus. The error is computed host-side and only then handed down:

    delta_rot  = err_rad * STEER_GEAR_RATIO / (2 * math.pi)
    target_rot = self.steer_motor.position + delta_rot
    self.steer_motor.position_output(target_rot)

The SPARK never sees the CANcoder. It gets a short relative move anchored to its
own integrated position, and the next tick recomputes the error from the CANcoder
regardless of what the on-board loop did with it. The outer loop is the authority
and the SPARK is the actuator.

Drive is open loop. Every command is `drive_motor.percent_output`, a duty cycle,
with no velocity or position setpoint and no sensor in the path.

So an unset closed-loop sensor degrades neither half, which is what a fleet that
has been driving correctly already told us. What survives is the narrower finding
and it is worth keeping: a SPARK stores a value outside its own enum, and every
layer above reads it as though it were a setting.

id 3 was put back with `spark params --id 3 --param 9 --set 4294967295`, read
back, and it now matches its seven siblings.

## Why the audit missed it and provision did not

The entry is marked `deviates: false`, and the audit reads only the deviating
subset because it pays that cost across eight controllers. `spark provision`
reads every declared setting on the one controller it targets, which is how the
disagreement surfaced. That wider read is the feature. Writing on the strength
of a declared value nobody had verified is the hazard beside it, and it is the
same shape as the encoder counts-per-rev typo caught on rig-flex, except this one
reached hardware.

## The boot throttle is not something an operator has to remember

The first audit reported eight period findings and eight sticky `hasReset` bits.
`spark throttle` cleared the period half and the second audit reported only the
reset bits, which is the command working.

None of that means the robot needed it. `SparkBus.init_controller` calls
`apply_boot_config` on every controller it registers, and both paths write the
same table, `_SPARKMAX_STATUS_PERIODS_MS`. So starting the drive stack applies
the throttle already, and this fleet has been driving without anyone running the
command. What the cold periods cost is bus load, and the code names the failure:
REVLib defaults push enough RX to wedge the gs_usb adapter. An audit taken on a
cold bus before the drive stack has run will report those eight findings every
time, and they heal themselves the moment ABC starts.

Reading the faults before priming was the right order. `spark clear` destroys
the `hasReset` record, which is the only evidence the reboot happened.

## The foreign device is the PDP

Device type 8 and manufacturer 4 identify it as the CTRE PDP 4.0, and the
operator confirms it belongs on that bus. Nothing is added to
`spark.yaml`, because nothing here commands it.

## The panel reports under load, and the idle survey was wrong

The earlier reading said it carries almost no information: six of its ten frames
byte-for-byte constant across 480 samples, no field tracking anything the SPARKs
reported. That sample was taken on an idle robot. A panel measuring nothing
reports nothing, so the survey could not tell an uninformative device from an
unloaded one.

Driving it settles that. `tools/spark_pdp_correlate.py` listened for 30 s while
the base drove, sending nothing. Capture:
`records/rig-max-post-rail-cycle-20260910.log`.

    frame    field      tracks          r
    0x056    u8@4       total current   +0.88
    0x050    u10#2      total current   +0.79
    0x050    u8@0, u8@3, u8@4           +0.78 to +0.79

Byte 4 of 0x056 alone carries it, and the 0x050 hits cluster in the 10-bit packed
layout CTRE use for per-channel currents. So the panel is sensing and
broadcasting, and the constant frames belonged to the sample.

The self-check confirms the window had load: per-controller current spans ran
1.26 to 24.93 A across the eight. Per-controller r against applied output stayed
between -0.14 and +0.33, which is expected, because current follows torque and
not duty cycle.

## It does not settle the telemetry scales

The current match is INDIRECT. A panel channel measures SUPPLY current into a
controller while the controller reports PHASE current, and the two differ by
roughly the duty cycle. Comparing them constrains nothing to better than that
factor.

The voltage half is what would settle it, and a second run at drive scale 0.99
sagged the rail 2.310 V, five times the first window's 0.466 V. That is deep
enough to correlate against, and the result is still no voltage field.

    frame    field    tracks current   tracks voltage
    0x050    u10#1        +0.81            -0.67
    0x052    u10#1        +0.77            -0.69

Those two are current channels, and the sign says so. A field measuring current
rises as current rises and falls as the rail sags under it. A field measuring the
rail would do the opposite. Nothing did.

## The panel reports the rail in byte 6, less cleanly than one run suggested

`u8@6` of apis 0x052, 0x055 and 0x058 rises with the rail while every other
strong field falls with it. The sign is the identification: a field measuring the
rail rises as the rail rises, and a current channel falls as the rail sags under
it.

It is one shared field. Back-solving the mean from each frame's fit gives 159.8,
159.7 and 159.7, which agree. Those are the three frames the idle survey called a
shared counter, and that reading was wrong.

The strength is modest and it moves between runs:

    run    rail sag    byte 6 vs rail    R2
    3       1.276 V         +0.81       0.66
    4       2.249 V         +0.69       0.48

So byte 6 explains under half the rail's movement in the deeper-sag run. A first
reading of run 3 called this settled on the +0.81 alone, and that was too strong.

Byte 7 was a red herring throughout. The absolute-match pass flagged it in every
run because `x*0.125` puts it within 0.15 V of the rail, and it tracked the rail
at +0.10 to +0.25. It lands in range by arithmetic. That is the case the
correlation column exists to catch.

## A lead on the telemetry scales, resting on two assumptions

The raw mean is exact rather than fitted, because least squares passes through
it, so the comparison below carries none of the slope's noise.

    panel byte 6 raw mean 159.8, under CTRE's raw*0.05 + 4.0    11.99 V
    controllers, under REV's 0.0073260073260073 V per count     11.28 V   -5.9 %
    the same controller counts under 1/128                      12.03 V   +0.3 %

A controller count would therefore be worth 0.0077871 V. REV publish 0.0073260,
which is 5.9 percent away. 1/128 is 0.0078125, which is 0.3 percent away.

1/128 is what `spark_controller` used until, when it was replaced by
REV's published figure on the grounds that it read about 6.6 percent high. This
points the other way.

Two assumptions carry all of that and neither is measured here. That byte 6 is
the bus voltage, and that CTRE's published scaling applies to this panel. Either
being wrong dissolves the result.

It is also not a calibration even if both hold, because the comparison uses the
controller's own reading as its reference. What it does is name a specific number
to check and a specific reason to doubt the current one. A multimeter on the rail
settles it in a minute, and that is now worth doing.
