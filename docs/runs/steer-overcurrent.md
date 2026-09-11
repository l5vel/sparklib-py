# rig-flex steer overcurrent, reproduced -- and two tool defects that hid it

> **Corrected.** Every conclusion below about a SPARK Flex answering
> no parameter read is wrong, and the reason is worth keeping. The reads went out
> as zero-length data frames, or as remote frames with dlc 0, and firmware 26.1.6
> ignores both. Sent as remote frames with dlc 8 they all answer, on eight api
> classes covering parameters 0-255, on every controller. This file is left as
> the record of what was believed at the time. See
> `docs/runs/rig-flex-parameter-reads.md`.. Robot on a stand, WHEELS UP. Drive output held at zero throughout.

## The result

`overcurrent` latches on all four STEERS and never on a drive. Five consecutive
runs, cleared between each:

    run 1..5    overcurrent sticky on [11, 13, 15, 17]
                drives 10, 12, 14, 16 clean, 0.0 A throughout

It is an active warning, not only a sticky, and the smart current limit is
visibly engaging: 26 samples per run read LIMIT REGULATING (applied output
scaled down while current holds at the limit).

## What actually reaches it

Aggressive DIRECT reversals, not a stepped square wave. Same duty, same
hardware, wheels up:

    stepped, settle to 0 between deflections    17 - 24 A peak
    direct +duty -> -duty, no settle            78 - 150 A peak

The `--reversals N` mode added here does the second. Driving the left stick hard
right-to-left in a loop is the same thing by hand, which is how this was first
noticed.

## Tool defect 1: the pattern blunted the transient it was measuring

`_steps()` returned to 0.0 between every deflection, so the motor decelerated to
rest and every "peak" was a start-from-rest transient. A direct reversal is a 2x
step against a motor still turning the other way. That is the regime that draws
the current, and the tool could not produce it.

## Tool defect 2: the tool starved the watchdog and latched its own e-stop

Every run reported `SPARK(s) went dark -- no CAN frames for 0.75s`, naming a
different random set of controllers each time, drives at 0 A included. The raw
candump says otherwise:

    run1  3952 STATUS_0 frames, 8 controllers, max gap 12.6 ms, 0 gaps >100 ms
    run2  3947 frames, max gap 15.0 ms, 0 gaps >100 ms
    run3  3959 frames, max gap 11.5 ms, 0 gaps >100 ms

On a 10 ms frame that is one frame of jitter. THE FRAMES NEVER STOPPED.

Cause: `SparkBus.bus_monitor()` runs a thread doing `bus.recv(0)` under a lock,
and it is what keeps each controller's `is_live` fresh. The sampling loop read
the SAME socket directly, bypassing the lock. SocketCAN gives each frame to
exactly ONE reader, so the tool ate the monitor's telemetry, liveness went
stale, and the watchdog latched the e-stop on healthy controllers.

Cost: 1778 of 2375 samples taken while latched. Only 291 actually drove.

Fixed by sampling `dt.bus.controllers` -- the decoded state the monitor already
maintains -- instead of the socket. After the fix:

    latched samples   1778 -> 0
    following          291 -> 1208
    LIMIT REGULATING     7 -> 26

The same defect was in the original 50 ms `collect_status` poll, so every steer
stress run ever taken with this tool was contaminated.

## Do not trust the peak number

The current field is uint12 x 0.0366300366, so full scale is 4095 counts =
149.99 A. Measured peaks, one run:

    id 11  149.19 A  4073 counts  99.5% of full scale
    id 13  149.78 A  4089 counts  99.9%
    id 15  148.75 A  4061 counts  99.2%
    id 17  148.46 A  4053 counts  99.0%

while p95 of the whole run was 62.8 A. Four independent motors clustering within
one percent of the ceiling is the field topping out, not four equal true values.

The tool's `pegged` test fired only at >= 149.99 and so called 149.78 a
measurement. It now flags the top one percent separately and says to treat the
peak as a FLOOR: the true current may be higher and a 12-bit field cannot say.

## What is still open

- Wheels up. On the ground the load is higher, so these numbers are a lower
  bound on the real thing.
- Whether the 115 A kCurrentChop threshold is being crossed cannot be settled
  while readings sit against the ceiling. A clamp meter or a lower duty that
  keeps the field in range would separate it.
- `overcurrent` raises no fault and has no LED code, so the only evidence is the
  commanded-versus-applied relationship this tool records.

## Files

    stickies-before-clear.txt   full state captured before anything cleared
    run1..3.csv, run1..3.candump  three cleared-then-run repeats with raw wire
    steer-stress-*.csv          earlier runs, including the contaminated ones

## Sampling at 250 Hz: more data, and chop is still invisible

Parameter 158 (Status 0 Period) written 10 -> 4 ms on the four steers only,
drives left at 10 ms as a control, RAM only, restored afterwards. Writes were
accepted and verified on all four, and the cadence changed on the wire.

    STATUS_0 period   before {all 10.0}   after {steers 4.0, drives 10.0}

                        100 Hz    250 Hz
    samples              1287      3066
    following            1208      2941
    LIMIT REGULATING       26        61
    >80 A per steer      6-10     19-25
    >115 A per steer      3-6      7-13
    CHOP                    0         0
    peak A            148.5-149.8   148.8-149.9

2.5x the frame rate gives 2.4x the samples and proportionally more high-current
samples, so the rate change worked exactly as expected. The peak did not move:
it stays pinned at 4053-4090 of 4095 counts.

**Chop stayed invisible with 46 samples above 115 A across the four steers.**
That is not an aliasing problem that a faster rate or more repetitions can fix,
and here is why.

STATUS_0's APPLIED_OUTPUT is, in REV's own words, "The actual value sent to the
motors from the motor controller" -- the commanded duty being applied, NOT the
instantaneous state of the half bridge. When kCurrentChop disables the bridge
the commanded duty is unchanged, so the field a chop-detector would have to
watch never moves. The signal is not in the frame at any sample rate.

Two consequences:

- The `CHOP?` verdict in spark_steer_stress can only ever fire on a dropout that
  lasts long enough to change the COMMANDED output. It cannot see chopping.
- Absence of a chop signature is not evidence chopping did not happen, and this
  run is not evidence either way.

What is visible, and what is doing the protecting: LIMIT REGULATING, 61 samples
at 250 Hz. The Smart Current Stall Limit is regulating. Current still reaches the
top of the reportable range during a reversal transient, which means the limiter
overshoots before it catches up -- how far is unknown, because the field
saturates at 150 A.

Settling chop needs a current probe on a phase lead. CAN cannot answer it.

## The overcurrent warning tracks the 80 A smart limit, NOT the 115 A chop

A third-party knowledgebase page claims "overcurrent faults trip at 115A
threshold", uncited. A duty sweep on rig-flex refutes it. One variable per run,
cleared between each, wheels up:

    duty   peak A per steer      samples >115 A   LIMIT REG   overcurrent latched
    0.20   120.3 134.1 121.2 127.8      1-5           21       all four
    0.14   107.7 107.2 108.2 116.6      0 0 0 1       22       all four
    0.10    95.6  98.5  91.5  86.1      0 0 0 0        1       id 11 only

At duty 0.14 three of the four steers peaked at 107-108 A, BELOW 115, and all
four latched overcurrent anyway. The 115 A claim does not survive that.

What does track it is the Smart Current Stall Limit engaging. LIMIT REGULATING
counts 21, 22, then 1 -- and at duty 0.10, where the limiter engaged once,
exactly one controller latched. Peaks there were 86-99 A, above the 80 A limit
but not sustained enough to regulate on three of the four.

So the warning follows the limiter doing work, not an instantaneous threshold
crossing, and certainly not the chop level.

## Why the rest of that page does not apply here

Its "Field-Proven Fix Sequence" is aimed at a different symptom -- swerve modules
ROTATING unexpectedly at a path boundary, a PathPlanner problem -- and not at
overcurrent under reversal.

    1-3  re-crimp ferrules, replace CANcoder pigtails, move termination off
         the PDH.  RULED OUT BY MEASUREMENT on this bus: after ~10 runs under
         load, bus-errors 0, arbitration-lost 0, error-warn 0, error-passive 0,
         bus-off 0, across 5,886,691 RX packets with 0 errors. The candump
         agrees: max inter-frame gap 15 ms on a 10 ms frame, no gap over 100 ms.
    4,6,7 PathPlanner-specific. This robot does not use PathPlanner; module
         offsets live in spark.yaml.
    5    restoreFactoryDefaults() + burnFlash() on every SPARK MAX. DO NOT.
         It would wipe the Appendix A configuration on all eight controllers and
         spend a flash cycle doing it, and tests/hardware/test_bus_preconditions
         exists specifically to keep flash writes off any unintended path. It is
         also a REVLib call, and nothing here runs REVLib.

Generic CAN hygiene is sound advice in general. It is ruled out here by the
error counters, which is the point of having them.

## Why the steers reach it at all: the limit is REV's factory default

Mined from the fetched corpus, after the runs above.

`the sparkflex: block` marks every current parameter `deviates: false`,
and nothing in this repo writes one. So the fleet runs REV's factory values:

    Current Chop               115 A
    Current Chop Cycles          0     (cycles BEFORE chop triggers, so immediate)
    Smart Current Stall Limit   80 A
    Smart Current Free Limit    20 A

Chief Delphi 516076, on reducing swerve power draw:

> [Weldingrod1] Low hanging fruits: Turn your steer motor supply current limits
> to 20 Amps.

So community practice for a STEER motor is far below the 80 A this fleet leaves
at default. The steers reaching the limiter on an aggressive reversal is what a
factory-default limit on a steer axis does, not a fault.

### Stator versus supply, which is why the breaker never tripped

Same thread, bhall-ctre:

> Stator limits are for limiting heat or acceleration, supply current limits are
> for brownouts/breaker trip events.

STATUS_0 reports MOTOR (stator) current. Supply current = stator x duty cycle.
At duty 0.30 a 149 A stator reading is roughly 45 A of supply, which is why a
40 A breaker has never tripped on this robot while the wire reports numbers near
150 A. Comparing the reported figure against the breaker rating compares two
different quantities.

### The failure mode on the other side of this

From the fetched GitHub issue corpus, a closed PR titled "Fixed writing
incorrect current limit to steering motors":

> I discovered that the incorrect current limit was getting written to the
> steering motor controllers. I also destroyed a NEO 550 along the way.

This repo writes NO current limit at all, which avoids that failure and creates
the other one: nobody has ever confirmed the controllers actually hold 80/115/20.
Parameter reads are dead on 26.1.6, so RHC2 over USB-C is the only way to check.

### What to do about it, NOT done here

Lowering Smart Current Stall Limit (parameter 59) on the four steers would stop
the overcurrent. That is a configuration decision about how much torque the
steer axis is allowed, not a defect fix, and it changes motor protection
behaviour on a live robot. It needs a deliberate choice and a baseline, not a
side effect of a diagnostic session. Writes to parameter 59 do work on 26.1.6
(measured: 158 and 159 both accepted and verified).

## The current-versus-duty curve, measured off the ceiling, wheels up, `spark clear` between every run, one variable per run.
Duties chosen low ON PURPOSE: at 0.30 every peak sat at 4053-4090 of 4095 counts,
so those runs report a floor and not a measurement. Everything below is inside
the field's range.

    duty   mean peak   max     samples >80 A   LIMIT_REG   overcurrent
    0.06     77.6 A    85.7          1              0      (not sampled)
    0.08     79.4 A    82.0          3              0      (not sampled)
    0.10     98.1 A    99.1         11              4      1 of 4 (earlier run)
    0.12    110.9 A   114.2         15             19      (not sampled)
    0.16    109.9 A   114.0         27             24      all four
    0.14                                                   all four (earlier run)
    0.30    ~149 A    at ceiling    many           26      all four

### What the shape says

**The peak plateaus at 110-114 A between duty 0.12 and 0.16.** Duty rose by a
third and the mean peak went DOWN slightly, 110.9 to 109.9, with the max flat at
114.2 then 114.0. Over the same step, samples above 80 A nearly doubled, 15 to
27, and LIMIT_REGULATING rose 19 to 24.

That is what a current limiter looks like from outside: more duty buys more TIME
above the limit, not a higher peak.

**The limiter switches on between duty 0.08 and 0.10.** LIMIT_REGULATING is 0 at
0.06 and 0.08, then 4 at 0.10. Peaks at 0.08 are already ~80 A, the Smart Current
Stall Limit, so the limiter engages just as the current reaches its setpoint.

**Overcurrent latches when and only when the limiter engages.** Zero LIMIT_REG at
0.06/0.08. At 0.10, LIMIT_REG 4 and one controller of four latched. At 0.12 and
above, LIMIT_REG 19+ and all four latch every time. At duty 0.16 the peaks were
106-114 A -- ALL BELOW 115 -- and all four latched anyway, which is the third
independent refutation of "overcurrent trips at 115 A".

### What this does NOT settle

The 110-114 A plateau sits within 1 A of the documented kCurrentChop threshold,
which is tempting. Do not read it as chop. The duty-0.30 runs reached the 12-bit
ceiling at ~150 A, well past 115, so nothing is clamping hard at that level; the
plateau is more likely the limiter regulating in this duty band while a larger,
faster transient at 0.30 overshoots before it can react. The 0.30 regime stays
uncharacterised because the field saturates there.

Chop remains unobservable over CAN for the structural reason in the section
above, at any duty and any sample rate.

## Supply versus stator, now from a vendor primary source

CTRE, Phoenix 6 hardware reference, "Improving Performance with Current Limits":

> I_supply = I_stator * duty cycle

That upgrades the relationship from a forum post (bhall-ctre, CD 516076) to
vendor documentation. It is TalonFX material and the parameter names do not
transfer to a SPARK, but the physics does, and it is the same formula
spark.yaml already reasons with.

CTRE also split what each limit is FOR:

    stator limit   torque, wheel slip, motor heat
    supply limit   brownouts, breaker trips, battery degradation

and note stator limits are "highly effective" at preventing brownouts during
acceleration, precisely because supply is bounded by stator times duty.
Brownout thresholds they give: 6.3 V on roboRIO 1, 6.75 V on roboRIO 2.

### What that makes of today's numbers

REV's Smart Current Stall Limit acts on the current STATUS_0 reports, which is
motor (stator) current. So every figure measured here is stator, and the supply
current the breaker sees is much smaller:

    duty   stator peak   implied supply
    0.06      77.6 A         4.7 A
    0.08      79.4 A         6.4 A
    0.10      98.1 A         9.8 A
    0.12     110.9 A        13.3 A
    0.16     109.9 A        17.6 A
    0.30     ~149 A         44.7 A     <- today's main runs
    0.40     ~150 A         60.0 A     <- the config comment's worked example
    1.00     ~150 A        150.0 A     <- what 100% duty would mean

The rail held at 13.05-13.12 V across every run, nowhere near either brownout
threshold, which is consistent: peak supply draw was under 45 A even in the
loudest run.

This also explains the standing puzzle plainly. A reported 150 A against a
breaker that never tripped was never a contradiction -- the breaker sees supply,
the frame reports stator, and at duty 0.30 those differ by a factor of three.

### Where it leaves the duty question

At 1.00 the two converge: a 150 A stator draw IS 150 A of supply. That is the
regime spark.yaml warns about, and its warning holds whatever the
per-channel breaker turns out to be:

> Lower this; do not raise the SPARK Smart Current Stall Limit, which the fuse
> already bounds.

NOT VERIFIED: the per-channel breaker rating on this robot. The config comment
asserts 40 A and nothing in the repo records what is actually fitted. The main
breaker is a 120 A Eaton Bussmann surface-mount, which is a different device on
a different path -- it sees TOTAL robot supply current, not one channel.

## A comparison point: what a team chose after browning out

FRC 9715, `TunerConstants.java`, described by them as deliberately low "because
we just wanted to avoid browning out again at the end":

    drive   stator 40 A   supply 35 A
    steer   stator 40 A   supply 20 A
    kSlipCurrent 75 A     kSpeedAt12Volts 3 m/s
    kSteerGearRatio 21.43

Against rig-flex:

    steer stator limit    80 A   REV factory default, nothing in this repo writes it
    steer gear ratio      26.0   HIGHER, so the motor spins faster per unit of
                                 output motion and there is more rotor inertia to
                                 reverse at each direction change
    measured steer peaks  110-114 A at duty 0.12-0.16, ~149 A at 0.30

So this fleet runs a steer stator limit twice what a team picked after a real
brownout, and the measured transient runs three to four times past even that. It
is also on a taller reduction, which is the mechanism that makes a reversal
expensive.

Their 20 A steer SUPPLY limit matches the community advice already recorded here
from CD 516076, "turn your steer motor supply current limits to 20 Amps". Two
independent sources landing on the same number is worth noting.

Caveats, so this is not over-read. These are CTRE TalonFX limits on Phoenix 6,
and REV's Smart Current Stall Limit is a different mechanism on different
hardware; the magnitudes are comparable because both act on motor current, not
because the parameters are equivalent. It is also one team's tuning, not a
vendor recommendation. And rig-flex is wheels-up: the load that matters most is
absent.

What it supports: lowering parameter 59 on the four steers is a reasonable
change with a defensible target, and 40 A is a defensible number to try. It
remains a configuration decision about steer torque, wants a before-and-after on
the same reversal pattern, and is not something to do as a side effect of a
diagnostic session.
