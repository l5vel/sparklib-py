# The SPARK field guide

Everything needed to wire, configure and diagnose a REV SPARK MAX or SPARK Flex,
starting from what a motor controller actually measures. That is where most
confusion begins and where the expensive mistakes get made.

Compiled from 1440 Chief Delphi thread bodies out of 2350 harvested topics, in
`the Chief Delphi corpus (chiefdelphi.com)`. Written against firmware 26.1.6.
Part 5 covers both firmware generations this fleet runs, because the frame and
parameter layouts split at 25.0.0 and not by product.

Every sourced claim carries a grade:

    [vendor]      REV or CTRE staff said it, posting under their own account
    [corroborated] several teams independently, or a posted measurement
    [reported]    one team's experience; may not generalise

Read the grade before acting on a line. None of it substitutes for a measurement
on the robot in front of you.

Each section opens with a SKIP IF line. An experienced reader can jump on that
alone.


## Contents

    Part 1  Fundamentals
      1  What a motor controller actually does
      2  The two currents
      3  What the SPARK actually reports
      4  How the current limit works

    Part 2  Wiring
      5  Power and breaker sizing
      6  The CAN bus
      7  The Vortex-to-Flex dock

    Part 3  Configuration
      8  CAN IDs
      9  Making settings stick
      10 What values to actually set

    Part 4  Diagnosis
      11 Faults, warnings and sticky bits
      12 Reading the numbers correctly
      13 Fault reference

    Part 5  Talking to it without WPILib
      14 The arbitration id, and how to build one
      15 Which frames exist, and the firmware split that decides
      16 Reading telemetry and faults off the wire
      17 Reading and writing parameters
      18 Enable, disable and the heartbeat

    Part 6  What experienced teams assume you know
      19 Assumed knowledge


---

# Part 1 - Fundamentals


## 1. What a motor controller actually does

SKIP IF you can explain why a motor controller is a switching converter rather
than a valve.

A SPARK does not throttle current the way a tap throttles water. It switches the
battery onto the motor and off again, thousands of times a second. That switching
is PULSE-WIDTH MODULATION, PWM, and the fraction of each cycle the switch is
closed is the DUTY CYCLE. Duty cycle is what you set when you command 40 percent
output, so "percent output", "duty cycle" and "PWM" all name the same knob here.

One word of warning about the term. PWM also names a separate thing on this
hardware, a servo-style control SIGNAL you can feed the controller instead of
CAN, covered at the end of section 6. The switching above happens either way.

This matters more than it sounds. The motor's inductance smooths those pulses
into a steady current, while the battery only supplies current during the closed
fraction. So current on the motor side and current on the battery side are
different numbers, and confusing them is the most common error in FRC electrical
debugging.


## 2. The two currents

SKIP IF you already reason in stator versus supply current and know which one a
breaker sees.

Every current figure in this world is one of two quantities. Learn both names,
because vendors use them inconsistently and the docs assume you know which is
meant.

    STATOR CURRENT     also: motor, output, winding, torque current
                       flows: controller -> motor windings
                       measured by: the SPARK
                       sets: torque, and how hot the motor gets

    SUPPLY CURRENT     also: input, battery, drawn, PDP/PDH channel current
                       flows: battery -> controller
                       measured by: the PDH channel, and your breaker
                       sets: whether the breaker trips

They are related by the duty cycle, and this relationship is the whole point:

    supply current = stator current x duty cycle

Said in words: stator current is what the motor feels, supply current is what the
battery is asked for, and the controller converts between them. The SPARK reports
the first. Your breaker reacts to the second.

At 20 percent output, a motor pulling 60 A draws about 12 A from the battery. The
controller trades voltage for current. Low duty, high motor current, modest
battery current.

        Battery ---- 12 A supply ----> [ SPARK ] ---- 60 A stator ----> Motor
          12 V           ^              duty 20%
                         |
                    breaker sits here

[vendor] "Are you measuring from the PDP or from the SPARK MAX directly, as these
values will differ since the SPARK MAX measures output current."
  REV staff, https://www.chiefdelphi.com/t/350542

[corroborated] Two teams logged both sides at once:

    SPARK reported    channel read     duty
    60 A              10-20 A          20%      t/408303
    40 A              2.9-4.3 A        ~10%     t/350542

DANGER - the inference that does not hold:

  "My breaker never tripped, so the current must be fine."

  A 40 A auto-reset breaker is thermal. REV confirm it passes far more than 40 A
  before opening, and the datasheet sustains 80 A for a MINIMUM of 5 seconds. An
  untripped breaker is not evidence of anything.
  https://www.chiefdelphi.com/t/374688

The corollary is useful. A stalled motor pushing against a wall shows enormous
stator current and modest supply current, which is exactly why breakers survive
pushing matches. A steering motor holding an azimuth lives in that regime almost
all the time.


## 3. What the SPARK actually reports

SKIP IF you know the STATUS_0 field widths and why 150.00 A is suspicious.

The controller broadcasts telemetry in fixed-width CAN frames. The widths matter,
because a field that runs out of range does not report an error. It reports its
maximum, and the maximum looks like a measurement.

    field               width    scale               saturates at
    applied output      int16    3.0824e-05          +/- 1.0
    bus voltage         uint12   0.0073260073 V      30.00 V
    output current      uint12   0.0366300366 A      150.00 A
    motor temperature   uint8    1 C                 255 C

CAUTION - read this before trusting a big number:

  A current reading of exactly 150.00 A is full scale. It means the true current
  is AT LEAST 150 A and the frame cannot say by how much. Same for 30.00 V and
  255 C. The scales are 150/4095 and 30/4095, so an all-ones field lands exactly
  on the top of its range rather than a whisker below it: the frame spec gives
  encodedMax 4095 on both, and `admin.status_0_implausible` tests the raw
  field against 0x0FFF for that reason. An all-ones payload saturates every
  field at once, which is what a controller that has stopped driving the bus
  looks like.

Those scale factors are REV's own, and the same volts and amps per count are
used on both firmware generations. They are not a calibration: the decoded numbers have
never been compared against a multimeter on the rail or a clamp meter on a phase
lead. Treat the readings as self-consistent rather than as
traceable.

[corroborated - three teams] Separately, SPARK output current has been seen
pinned at a constant implausible value - 72 A, 85 A and 125 A - with the motor
unloaded and applied output varying. A current that does not respond to output is
telemetry failing, not a mechanism loading up.
  https://www.chiefdelphi.com/t/373283


## 4. How the current limit works

SKIP IF you know the factory default is a FLAT 80 A at every speed, and that the
stall/free taper is switched off until you set the limit RPM yourself.

The SPARK's Smart Current Limit is a STATOR limit. There is no supply-side limit
on a SPARK, which is worth knowing before you try to protect a breaker with it.

Three parameters govern it, not two:

    param  name                       default   meaning
    59     Smart Current Stall Limit    80 A    limit at stall, and at any RPM
                                               below the limit-RPM breakpoint
    60     Smart Current Free Limit     20 A    limit at free speed
    61     Smart Current Config      10000 RPM  where the linear reduction STARTS

[vendor] REV's parameter documentation on the third one: "Smart current limit RPM
value to start linear reduction of current limit. Set this > free speed to
disable."
  https://docs.revrobotics.com/brushless/spark-max/parameters

That default of 10000 RPM is above the free speed of every motor you are likely
to be using, so the taper is disabled out of the box:

    NEO Vortex   6784 RPM free speed   -> 10000 is above it, taper OFF
    NEO V1.1     5676 RPM              -> taper OFF
    NEO 2.0      5676 RPM              -> taper OFF
    NEO 550     11000 RPM              -> the one exception, and only over its
                                          top 1000 RPM

[vendor] REV's software lead, stated directly: "Note that the default setting is
actually 80A across RPM range. The linearlization feature can be enabled by the
API by setting the 'limitRPM' to 0... Set the limit RPM to '0' to have a linear
response for the entire RPM range (nice for closed loop)."
  Will_Toth, https://www.chiefdelphi.com/t/350542 (post 25)

So unless your code calls setSmartCurrentLimit with a limit RPM below the motor's
free speed, every controller holds a flat 80 A whatever the motor is doing.

How well that is established on this fleet: all three were READ OFF the hardware. Every one of the eight controllers on rig-flex returns 80 for
parameter 59, 20 for 60 and 10000 for 61, which is exactly the triple REV state.
REV publish no Flex parameter table, so this had no Flex source until the read
frame was fixed, and `provenance.flex.param.61.limit_rpm` now grades HARDWARE.
What stays vendor-stated is the MEANING. That a Config value above free speed
disables the stall-to-free taper is REV's statement about the SPARK MAX, and
nothing here has measured the taper on a Vortex. Parameter 61 is absent from
the `sparkflex:` block of spark.yaml, which carries 59 and 60 and
stops there, so `spark audit` raises no finding on it. The baseline holds it
instead, and spark-baseline.yaml records 61 at 10000 among
138 undeclared ids. A later disagreement there prints as a note and leaves the
exit code alone.

CAUTION - a correction, recorded because this guide got it wrong:

  An earlier version of this section stated the limit tapers 80 A to 20 A with
  speed. That came from one forum post which was a guess, and whose author
  withdrew it in the same thread once REV corrected him -- at a post past the
  point where the first harvest of that thread stopped. If you read that version,
  the taper claim was wrong and any reasoning built on it does not hold.

### Current chop at 115 A, and the default that reads backwards

[vendor] There is a separate hard cutoff: kCurrentChop, parameter 11, default
115 A. It is a distinct mechanism from the Smart Current Limit and it is not what
the Smart limit does.
  https://docs.revrobotics.com/brushless/spark-max/parameters

[vendor] Its companion is kCurrentChopCycles, parameter 12, default 0, and
REV-SparkParameters-v0.1.2 describes it as the "number of cycles before current
chopping is triggered". Zero therefore means chop fires IMMEDIATELY, not that
chopping is switched off. **This is the one to be careful about.** The opposite
reading circulates on the forum, it sounds right, and it was repeated as fact
here too before anyone opened the parameter table. If you are about to reason
from "chopping is off by default", check parameter 12 first.

Two things follow for anyone reading current off the bus. An overcurrent warning
is NOT the 115 A chop: on rig-flex, a duty sweep with the wheels up had
three steers peak at 107-108 A -- below 115 -- and all four latched the warning
anyway, tracking the 80 A Smart Current Stall Limit instead. And chop itself
cannot be seen over CAN at any sample rate, because STATUS_0 reports commanded
applied output rather than the instantaneous half-bridge state: the same run
raised STATUS_0 to 250 Hz, took 2.4x the samples, logged 46 readings above 115 A
across four steers and captured ZERO chop signatures. Absence of the signature is
not evidence that chop did not happen. A current probe on a phase lead is the
only instrument that sees it.
  docs/runs/steer-overcurrent.md

### What the controller is rated for

[vendor] REV rate the SPARK Flex at 60 A CONTINUOUS output current, with 100 A
peak for a 2-second surge. The factory 80 A Smart Current Limit is above the
continuous rating -- it is a motor-protection number, not a controller-thermal one.
  https://docs.revrobotics.com/brushless/spark-flex/specs

[vendor] And REV's own getting-started page for the Flex names 80 A as the
recommended Smart Current Limit for a NEO Vortex, so on that pairing the factory
default is also the recommendation.
  https://docs.revrobotics.com/brushless/spark-flex/gs/make-it-spin

### What the limit actually does when it engages

[vendor] REV's software lead: the Smart Current Limit holds the motor AT the limit
value and scales the duty cycle to do it. So a controller sitting at its limit is
being actively regulated, not merely reporting a number.
  https://www.chiefdelphi.com/t/350542 (post 27)

---

# Part 2 - Wiring


## 5. Power and breaker sizing

SKIP IF you size breakers from supply current and know a tripped thermal breaker
is scrap.

Breakers protect the WIRE, not the motor. They sit on the supply side, so size
them against supply current, which per section 2 is not the number your
controller reports.

  - Drivetrain: 40 A is the conventional choice.
  - Lightly loaded mechanisms: 30 A is fine. There is no reason a SPARK cannot
    sit on a 30 A branch.
  - Protect the motor in software with the current limit, not by picking a
    smaller breaker.

[corroborated - two independent responders] "There's no reason you can't use a
SPARK MAX on a 30A PDP branch circuit. You just need to be careful not to draw
too much current... by using the proper gear ratio for the load, and by setting
a current limit in the SPARK MAX software."
  https://www.chiefdelphi.com/t/375570

DANGER - do not force a main-breaker trip to test anything:

  A team blew a main breaker at full speed. The drivetrain coasted, backdrove,
  and generated a voltage spike with no battery in the loop to absorb it. They
  lost three Limelight cameras, 1200 USD. A BRANCH breaker trip is far less
  severe, because the battery stays connected.
  https://www.chiefdelphi.com/t/461056

One more thing about thermal breakers. Every trip permanently lowers the trip
point, because the bimetallic element takes a set. A breaker that has tripped
should be replaced rather than reused, or it trips earlier and earlier and reads
as a worsening electrical fault.


## 6. The CAN bus

SKIP IF you terminate at both ends and know the SPARK Flex pigtail is a different
gauge from the rest of your harness.

CAN is a daisy chain, not a star. Every device passes through, and the chain needs
120 ohm termination at each physical end. A break anywhere silences everything
DOWNSTREAM of it, which is why the lowest missing device id is where to start
looking rather than the one that seems most broken.

### The gauge trap

[corroborated] Standard FRC CAN wire is 22 AWG. The SPARK Flex pigtail is 26 AWG,
noticeably thinner. Ferrules and WAGO connectors crimped for 22 AWG do not grip it
reliably and it pulls out. Teams report it as a durability problem rather than a
wiring mistake.
  https://www.chiefdelphi.com/t/497359

### Practical rules that come up repeatedly

  - Strain-relieve every crimp. Wires almost always break at the crimp point;
    stopping the flex there solves most of it.
  - You cannot measure termination with the bus live. Continuity and resistance
    checks need the power off and the bus quiet.
  - Watch utilisation. Under 70 percent with no bad peaks is a healthy target. A
    saturated bus produces symptoms that look exactly like failing devices.

### Not using CAN at all: PWM input

A SPARK accepts a servo-style PWM signal on the same four-wire data port that
carries CAN, and drives the motor from that with no bus, no ids and no software.
A 1.5 ms pulse is neutral, roughly 1.0 ms is full reverse and 2.0 ms full
forward, at the usual servo frame rate. Reach for it on a test stand, a demo, or
any rig where a microcontroller with a PWM output is the whole control system.

What it costs is everything this library is about. A PWM-driven controller
reports nothing back: no voltage, no current, no temperature, no fault word, no
firmware version. You command it and you learn what happened by watching the
motor. Configuration arrives over USB-C with REV Hardware Client, because that is
the only other channel there is.

Two parameters exist for this mode, which is why a factory reset lists them by
name. PWM Input Deadband sets how far from neutral a pulse must be before the
controller acts, so a jittery signal does not creep the motor. Duty Cycle Offset
trims where the controller believes neutral is, for a source whose 1.5 ms is not
quite 1.5 ms.

The wiring caution in the practical rules above is about this mode. The same four
26 AWG conductors serve CAN and PWM, so use one connector and keep the other
taped and clipped away from anything it could short against.


## 7. The Vortex-to-Flex dock

SKIP IF you do not run NEO Vortex motors.

A NEO Vortex bolts directly onto its SPARK Flex, and the motor's phase
connections pass through that joint. It is a wiring connection that does not look
like one, and it loosens with vibration.

[vendor] "The EEPROM Fault and Other Error can result from a loose connection
between the Vortex and the Flex. Please reseat them and make sure that the
docking screws are fully installed."
  REV staff, https://www.chiefdelphi.com/t/453509

A team running six Flex-and-Vortex pairs on a swerve base watched CAN errors and
overcurrent warnings escalate from occasional to every match across a season. They
demounted and remounted all six motors and the errors dropped away, without
touching the CAN network at all. Their SPARK MAX controllers on the same robot
were untroubled throughout, which is what pointed at the dock rather than the
harness.
  https://www.chiefdelphi.com/t/461113

The distinguishing test: a loose dock is a per-module fault, so it concentrates.
If errors appear uniformly across every module, look for a systemic cause -
driving style, gearing, or a limit doing its job - before unbolting motors.


---

# Part 3 - Configuration


## 8. CAN IDs

SKIP IF you know a factory reset takes the CAN ID with it.

Every device needs a unique id. Two controllers sharing one id are invisible to
an id-based scan: they look like a single device. A parameter write addressed to
that id reaches both of them while only one reply is read.

CAUTION - the reset trap:

  restoreFactoryDefaults() sets the CAN ID back to 0. If you call it as the first
  line of your configuration routine, a common pattern, you are renaming every
  controller to zero and relying on the rest of the routine to put things back.
  https://www.chiefdelphi.com/t/381069

There is also a hardware failure worth recognising. Individual SPARK Flex units
lose their CAN ID and all burned settings on every power cycle, reverting to 0.
REV treat it as a defect and replace the unit. If one controller keeps coming back
as id 0 while the others hold, it is the controller and not your code.
  https://www.chiefdelphi.com/t/455171


## 9. Making settings stick

SKIP IF you burn flash deliberately and know what a brownout does to an unsaved
parameter.

Settings live in RAM until you commit them to flash. Two consequences follow, and
both bite in a match rather than on the bench.

[vendor] A limit set but not saved reverts to the factory default after a brownout
or reset. If your code sets a current limit at boot and the robot browns out
mid-match, the controller comes back at 80 A.
  https://www.chiefdelphi.com/t/354333

[vendor - REV] Do NOT call factory-reset on every boot when your settings are
already burned. Resetting makes every parameter depend on a config write landing,
and a single dropped CAN write then leaves one setting silently wrong. Burn
known-good settings once, and stop resetting.
  https://www.chiefdelphi.com/t/461113

There is a subtler version of the same trap. A configure call using a
reset-safe-parameters mode that does not itself write the current limit will put
the limit back to 80 A. The value you chose in the hardware client is gone and
nothing reports it.

CAUTION - timing, if you write then burn:

  A burn sent too soon after a parameter write can commit the PRE-WRITE value
  while both operations report success. The field remedy is a delay of at least
  200 ms between the write and the burn.
  https://www.chiefdelphi.com/t/432129


## 10. What values to actually set

SKIP IF you have already tuned limits against your own gearing and measured draw.

Starting points from teams and vendors, not prescriptions. Your gearing and duty
cycle dominate.

    application              suggested   source and grade
    full-size NEO, general   40-50 A     REV support        [vendor]
    NEO 550                  20-30 A     REV support        [vendor]
    swerve steering          ~60 A       team report        [reported]
    NEO drivetrain           50-80 A     several teams      [corroborated]

For steering motors specifically, the more effective lever is often not the limit
at all but the RAMP. Steering draws high current because it accelerates hard and
stops hard.

[reported] "It's pretty common that turn motors draw high current because of the
sudden and rapid accelerations. Even a small ramp rate of about 10-20 ms can make
significant improvements on current draw."
  https://www.chiefdelphi.com/t/461510


---

# Part 4 - Diagnosis


## 11. Faults, warnings and sticky bits

SKIP IF you triage on active-versus-sticky already.

Three distinctions decide whether something needs action:

    kind             meaning                                    act on it?
    active fault     happening now; holds the motor from        yes, immediately
                     spinning
    active warning   happening now; does not stop the motor     usually
    sticky           happened at some point since the last      read it, then judge
                     clear

[vendor] REV's own triage line is exactly this: an active fault holds the motor,
a sticky one only records that the condition happened and is no longer present.
  https://www.chiefdelphi.com/t/449880

CAUTION - read before you clear:

  Clearing faults erases the sticky bits, and those bits are often the only
  record of why something went wrong - a brownout, a reset. Read them first.
  Clearing is not free diagnosis; it is destroying the evidence to remove the
  symptom.


## 12. Reading the numbers correctly

SKIP IF nothing. This section closes the loop.

A checklist to run when a controller reports something alarming.

  1. Is the number at full scale? 150.00 A, 30.00 V or 255 C means the field
     saturated. It is not a measurement.
  2. Does it move? A current that stays constant while applied output changes is
     failing telemetry, not load.
  3. Which current is it? The SPARK reports stator. Multiply by duty cycle before
     comparing it to anything on the supply side.
  4. Is the motor near stall? High stator with low supply is normal there, and it
     is where steering motors spend their lives.
  5. Is it concentrated or fleet-wide? Faults concentrate. A symptom on every
     device at once is usually design or duty, not eight simultaneous failures.
  6. Did anything actually trip? And remember that a thermal breaker which did
     NOT trip proves nothing.

The cheapest instrument you are not using: a DC clamp meter on one motor lead and
one battery lead settles stator-versus-supply in about five minutes,
non-destructively. Everything above is inference; the clamp is a measurement.


## 13. Fault reference

SKIP IF you already know which bits are faults and which are warnings on each
generation. This is a lookup table, not an argument.

    bit                      what it means                     what to do
    overcurrent              exceeded the Smart Current        measure how LONG, not the
                             Limit in force at that speed      peak; brief on a stationary
                                                               wheel is ordinary
    brownout                 rail dipped below the             check pack state of charge
                             controller's floor                and cable resistance, not
                                                               the controller
    hasReset                 the controller rebooted           re-apply the whole config;
                                                               unsaved parameters are gone
    gateDriver               output stage                      survives factory reset and
                                                               reflash; if it returns
                                                               immediately, RMA
    sensor                   encoder or motor data cable       check the JST at the motor;
                                                               on a Vortex check the dock
    escEeprom + other        together, on a Flex               REV's signature for a loose
                                                               Vortex dock; reseat and
                                                               check docking screws
    can                      bus contention                    two devices on one id will
                                                               do it; check for duplicates
    stall                    commanded and did not turn        look for a mechanical bind
                                                               before raising any limit
    motorType                brushless motor in brushed mode   wrong motor type configured;
                                                               it will not run

Those are names, not bit positions, and the positions differ by generation. On
firmware 25+ the faults and the warnings sit in separate bytes of STATUS_1, in
the order section 16 lists. On PRE-25 there is one 16-bit word, in bytes 2:6 of
0x060 with the low half active and the high half sticky, and its order is:

    0 brownout      4 sensor      8 canRx        12 softLimitFwd
    1 overcurrent   5 stall       9 hasReset     13 softLimitRev
    2 iwdtReset     6 eepromCrc  10 gateDriver   14 hardLimitFwd
    3 motorType     7 canTx      11 other        15 hardLimitRev

**Overcurrent is bit 1 of that word.** Bit 11 is kOtherFault, and a report
placing overcurrent at 11 was corrected. The order comes from
REVLib 2024.2.4's own FaultID enum, corroborated by REV's SPARK-MAX-Types.proto,
by the REV Hardware Client's fault list on 24.0.1 and by third-party
re-implementations; bit 9 is additionally measured, three rail cycles on rig-max each leaving every controller at sticky `0x0200` and nothing else.
REVLib 1.1.5 and earlier called bit 2 kOvervoltage without moving it, so an old
decoder mis-NAMES that position rather than mis-placing it.
`admin._LEGACY_FAULT_BITS` and `_FAULT_BITS` carry both words;
`provenance.pre25.fault_bit_order` carries the sourcing.


---

---

# Part 5 - Talking to it without WPILib

Everything above holds whatever software drives the controller. This part is for
a Linux robot with no roboRIO, where the only things on the bus are a SocketCAN
interface, python-can, and frames you build yourself.

Two REV documents make this possible, and both are machine readable. Read them
rather than any summary, this one included:

    REV-spark-frames-2.1.0.json        every frame, every bit position
    REV-SparkParameters-v0.1.2.md      every parameter id, type and default

Both come from github.com/REVrobotics/REV-Specs and are vendored at
`reference/`, because the tests read them. REV's documentation website
carries different and older tables; where the two disagree, the spec repo is
newer by about a year.

Everything in this part splits on FIRMWARE GENERATION and never on product. This
fleet spans both: rig-max's eight SPARK MAXes run 24.0.1 and the Flex rigs's
Flexes run 26.1.6, so a claim measured on one says nothing about the other unless
the generation matches. `sparklib/provenance.py` records which
robot and which run established each fact below.


## 14. The arbitration id, and how to build one

SKIP IF you already build FRC CAN arbitration ids by hand.

Every frame is a 29-bit extended CAN id built from four fields: device type,
manufacturer, api, and device id. A REV motor controller is device type 2,
manufacturer 5, which fixes the top of the id at `0x02050000`.

    arbitration_id = 0x02050000 | (api << 6) | device_id

`api` is ten bits, itself two fields: a six-bit apiClass and a four-bit apiIndex.

    api = (apiClass << 4) | apiIndex

So STATUS_1, apiClass 46 and apiIndex 1, is api `0x2E1`, and on device 12 the
arbitration id is `0x02050000 | (0x2E1 << 6) | 12`, which is `0x0205B84C`.

Two ids sit outside the scheme and matter more than the rest:

- **Arbitration id 0**, zero length, is the FIRST CAN disable broadcast. It
  carries no device type, no manufacturer and no device id, so it reaches a
  controller whose id is wrong, duplicated or unknown. [vendor]
- **`0x01011840`** is the universal heartbeat, sent every 20 ms. A device acts
  as disabled once 100 ms passes without one. [vendor] **Not on this fleet:**
  firmware 24.0.1 ignores that id entirely and takes about 240 ms to drop output
  when its SECONDARY heartbeat stops: four runs reached zero at 224, 242, 244
  and 245 ms. Section 18 has the measurement.

The device id occupies the low six bits, so ids run 0 to 63. Id 0 is not a
broadcast address: it is the factory default, and REV treat it as UNCONFIGURED,
so a controller sitting there will not enable at all. That is why a controller
which has lost its id answers as 0 and appears to have been factory reset. The
reserved device-specific broadcast address is 63, `0x3F`, the top of the field;
section 19.3 carries both.


## 15. Which frames exist, and the firmware split that decides

SKIP IF you know that 0x060 is a legacy frame rather than a SPARK MAX frame.

This is the section most likely to save a day, because the obvious reading is
wrong and the code that gets it wrong looks correct.

`REV-spark-frames-2.1.0.json` describes ONE device. Its `deviceInfo` block names
a device type and a manufacturer and **no product at all**. There is no SPARK MAX
entry and no SPARK Flex entry anywhere in the file. What every frame does carry
is a `versionImplemented`, and one of them carries a `versionDeprecated`:

    LEGACY_STATUS_0    api 0x060    implemented 0.0.1    DEPRECATED 25.0.0
    STATUS_0           api 0x2E0    implemented 25.0.0
    STATUS_1           api 0x2E1    implemented 25.0.0
    STATUS_2.. 8      api 0x2E2-8  implemented 25.0.0
    STATUS_9           api 0x2E9    implemented 26.0.0
    UNIQUE_ID          api 0x2F0    implemented 25.0.0

REV describe LEGACY_STATUS_0 in the spec itself:

> This frame exists purely to inform old software that is not aware of firmware
> version 25+ that the SPARK is present

So **Api 0x060 is the pre-25 frame, not the SPARK MAX frame.** A SPARK MAX
running firmware 25 or later broadcasts on 0x2E0 and 0x2E1 like anything else.
The split is a firmware generation, and the product does not enter into it.

frames 2.1.0 lists exactly one legacy periodic frame, because it describes a 25+
device. **0x061 and 0x062 are real all the same**: REV's SPARK MAX
control-interfaces page documents Periodic Status 1 as velocity, temperature,
voltage and current, and Periodic Status 2 as motor position. They are pre-25
frames on api class 6, and that page is the pre-25 table.

Measured rather than inferred, on rig-max's eight SPARK MAXes at firmware 24.0.1: the pre-25 set on the wire is 0x060, 0x061, 0x062, 0x063, 0x065,
0x066 and 0x067. **0x064 never broadcasts on pre-25 in any state** -- checked
again either side of a rail cycle -- so its absence is this
firmware not emitting the alternate-encoder frame, not a period write that went
missing.

Ask what firmware a controller is on before deciding how to decode it. The
GET_FIRMWARE_VERSION frame answers, and it is a remote frame with no payload, so
it costs nothing and cannot write. You can also read it passively: any frame on
0x2E0-0x2E9 or 0x2F0 means 25+, and a 0x060 payload of exactly
`0000ffffffff0000` is the 25+ compatibility beacon rather than a reading.

**Do not score the bits in that beacon.** On firmware 25+ every signal in
LEGACY_STATUS_0 is pinned -- applied output 0, all 32 fault bits set, other
signals 0, REV's note being "Always has all faults set so that old software
knows that something is wrong". The pre-25 word is sixteen active bits and
sixteen sticky bits, so decoding the beacon as data reports sixteen faults and
sixteen sticky faults on a healthy controller, on every frame.

This has been got wrong four times in this codebase. Once by decoding SPARK
Flex frames with the older layout, so a healthy 13.7 V rail read as a fault
bitfield and gating recovery on it wedged the base. Then again while fixing
that, by branching on MAX versus Flex, which the spec does not do either. Then a
third time as a consequence of the second: with 0x060 selected as the MAX fault
frame, a 25+ MAX would have decoded as sixteen simultaneous faults.

The fourth was found. The swerve startup gate that refuses to
drive a hard-limited controller called `admin.decode_status_0` and
`decode_status_1` straight on the buffer `can_bus` fills from EITHER 0x060 or
0x2E0, with no generation branch, while `controller` had been handling the
same split correctly for weeks. A real 0x060 payload captured off rig-max that
day, `0000000000021000`, decodes correctly as no active fault and sticky
`0x0200`, hasReset. Through the firmware-25 decoder the same eight bytes read as
0.0 V, 0.0 A, 2 C and both hard limits open. The gate was not misreporting: it
was blind, and a 0.0 V rail does not even trip the implausible check. It is
fixed -- `admin.reading_from_raw` now normalises a buffered pair for the
generation it actually is -- and the defect class is the one this codebase has
now paid for four times.

The reason the product axis keeps looking right is worth knowing. REV publishes
exactly ONE periodic status frame table and it is the SPARK MAX one; the SPARK
Flex control-interfaces page documents no frames at all and ends "More
information coming soon!". The only frame table on REV's website describes a
MAX and describes pre-25 firmware, so reading it as "the MAX layout" is the
natural mistake.


## 16. Reading telemetry and faults off the wire

SKIP IF you know which of STATUS_0 and STATUS_1 carries faults on firmware 25+.

On firmware 25 and later the division is clean, and it is the same for both
products.

**STATUS_0, api 0x2E0** carries what the motor is doing:

    APPLIED_OUTPUT   int16    x 3.0824e-05     -1.0 to +1.0
    VOLTAGE          uint12   x 0.0073260073   full scale 30.00 V
    CURRENT          uint12   x 0.0366300366   full scale 150.00 A
    MOTOR_TEMPERATURE uint8                    degrees C

plus the four limit-switch bits, INVERTED, PRIMARY_HEARTBEAT_LOCK, and
SPARK_MODEL.

A current reading of 150.00 A is the twelve-bit field saturating. It is not a
measurement, and no average or dwell figure computed from a run containing them
means anything. Section 12 covers that at length.

APPLIED_OUTPUT is the COMMANDED value -- REV describe it as "the actual value
sent to the motors from the motor controller" -- so nothing that happens inside
one switching period is visible here. Section 4 covers what that costs you.

**SPARK_MODEL** sits at bit 54 and is four bits wide. The controller announces
its own model in every STATUS_0 frame, so a driver can learn what it is talking
to instead of trusting a configuration file. That is worth wiring up early.

**STATUS_1, api 0x2E1** carries every fault, every warning, and the sticky
version of each, plus IS_FOLLOWER:

    faults      OTHER, MOTOR_TYPE, SENSOR, CAN, TEMPERATURE, DRV,
                ESC_EEPROM, FIRMWARE
    warnings    BROWNOUT, OVERCURRENT, ESC_EEPROM, EXT_EEPROM, SENSOR,
                STALL, HAS_RESET, OTHER

A sticky bit stays set until it is cleared, so it survives the condition that
set it. That is what makes HAS_RESET useful: a controller that rebooted says so
long after the reboot, which distinguishes a config that was lost from a config
that was never written. On this generation a power cycle also clears the sticky
byte, so a sticky bit you can see on a Flex is younger than the last power-up;
pre-25 is the other way round and section 19.5 has both.

The status frames are sent periodically, so reading them needs no request at
all. Open the interface, receive, and filter on arbitration id.

Only some of them arrive, though, and on this generation that is a default
rather than a fault. STATUS_0, STATUS_1 and UNIQUE_ID carry `enabledByDefault`
true in the spec and STATUS_2 through STATUS_9 carry false, so a 25+ device that
has not been told otherwise sends exactly those three and nothing else. That is
what rig-flex broadcast on a freshly power-cycled bus, on all eight
controllers. Primary-encoder position lives in STATUS_2, so waiting for it on a
default 25+ controller is waiting for a frame that is switched off;
SET_STATUSES_ENABLED, apiClass 1 index 0, is what turns it on. Pre-25 has no
such frame and no such default: rig-max broadcasts its position frame, 0x062,
without being asked.


## 17. Reading and writing parameters

SKIP IF you have the REV-Specs parameter table open already.

Every setting lives at a numbered parameter. The full table is 199 rows, ids 0 to
198, in `REV-SparkParameters-v0.1.2.md`. That document is titled "SPARK
Configuration Parameters" and says the parameters live "within the SPARK". One
row names both products in a single description, so it is a joint table covering
Flex and MAX rather than either one alone.

### Writing

    PARAMETER_WRITE           apiClass 14, api 0x0E0
        payload: parameter id (1 byte) then value (4 bytes, little endian)
    PARAMETER_WRITE_RESPONSE  apiClass 14, api 0x0E1
        payload: id, type, echoed value (4 bytes), result code

Both frames are versionImplemented 25.0.0, so a PRE-25 controller carries
neither one and silently drops the arbitration id. On rig-max at firmware 24.0.1, a PARAMETER_WRITE for parameter 158 drew no response at all, on ids
1, 4, 5, 6 and 8. That silence is the frame not existing rather than the device
refusing, and pre-25 has its own dialect for both directions -- see Reading,
below.

The response echoes the value the device took. Compare it against what was asked
for, because a device answering Success while echoing a different value is a real
failure mode teams have hit.

Four rules, all learned from other people's bad days:

- **Parameter 0 is the CAN id.** Writing it renames a motor. Move an id with the
  SET_CAN_ID frame, which is addressed by serial number and so reaches the
  controller you chose even when two share an id. That needs a serial, and the
  only source of one on the bus is UNIQUE_ID, which arrived at 25.0.0. No
  controller on rig-max broadcasts it, so on a pre-25 bus there is
  no serial to address, duplicates cannot be told apart over CAN at all, and the
  only way to separate them is off the bus, one at a time, over USB.
- **Retry, and read back.** A REV support thread puts it plainly: you need
  retries for basically every setting, and you need to read the setting back to
  confirm it landed. The thread's cause was settings sent back to back faster
  than the device handled them, so pace writes about 20 ms apart. [vendor]
  You can read it back on either generation, in that generation's dialect: see
  Reading, below. Read it back rather than trusting the echo, because a BOOL
  parameter on 26.1.6 accepts and stores a value outside 0 and 1.
- **Wait before burning to flash.** A parameter written less than about 200 ms
  before PERSIST is committed at its pre-write value, and both frames answer
  Success. Nothing on the bus marks that, so the delay is the only instrument.
- **A write addressed by id reaches every controller sharing that id**, and only
  the first reply gets read. Resolve duplicates before writing anything.

### Reading

The spec defines dedicated read frames, and they are worth using instead of
improvising:

    READ_PARAMETER_n_AND_n+1        apiClass 15-22, api 0x0F0.. 0x16F
        sixteen frames per class, one per pair, so 32 parameter ids per class
        and eight classes cover 0 to 255. Returns two 32-bit values, one per
        parameter of the pair
    WRITE_PARAMETER_n_AND_n+1       apiClass 23-30, api 0x170.. 0x1EF
        the same eight-way split
    GET_PARAMETER_n_TO_n+15_TYPES   apiClass 13, api 0x0D0 + index
        returns sixteen type codes in one frame, covering 0 to 255 in sixteen
        frames

There are 128 read-pair frames covering ids 0 to 255, all implemented at 25.0.0.
**One class is not the file.** apiClass 19 is the fifth of the eight and covers
parameters 128 to 159 only; reading its sixteen indices as the whole read
surface turns 128 frames into 16 and a 256-id table into a 32-id window, which
is the mistake this project made and corrected. The map is in
docs/PROTOCOL.md, class by class with arbitration ids.

Because they sit on a different apiClass from PARAMETER_WRITE, a read cannot be
mistaken for a write by construction. Anything that reads parameters through the
write api is relying on payload length to carry the distinction, which is one
byte of margin on a frame that can change a motor's configuration.

**Send them as REMOTE frames carrying dlc 8.** Both halves are required and the
spec only tells you the first. Every one of the 128 read-pair frames and all
sixteen GET_PARAMETER_TYPES frames is marked `rtr: true`, as is
GET_FIRMWARE_VERSION. Measured on rig-flex: a zero-length data frame is
silent, a remote frame with dlc 0 is silent, and a remote frame with dlc 8
answers. GET_FIRMWARE is the odd one out, because it is rtr with lengthBytes 8
in the same spec and answers at dlc 0, so the dlc rule holds for the parameter
read classes and does not generalise. The claim is
`provenance.flex.param_reads_were_probed_as_data_frames`, and it is now
graded HARDWARE. It began as a reading of the spec against this
package's own code, with no Flex attached. A run on rig-flex settled it the next
day, and the write-up is
docs/runs/rig-flex-parameter-reads.md.

`GET_PARAMETER_*_TYPES` is the fastest way to learn what a controller actually
has. Sixteen type codes per frame means 256 parameters in sixteen frames, and
the codes are 0 Unused, 1 Int, 2 Uint, 3 Float, 4 Boolean. A parameter that
answers with type Unused does not exist on that device, which is how you check
whether a table applies to the hardware in front of you. On rig-flex's eight
Flex at 26.1.6, 185 of the 256 ids come back implemented and the rest read
Unused. Both firmwares on this fleet answer, in different dialects. Read on.

### What actually answers, per generation

Both generations answer a read, in different dialects over different ranges,
so ask a controller's firmware version before designing anything around one.

**SPARK Flex, firmware 26.1.6 (rig-flex): every parameter 0-255 is
readable, over eight apiClasses.** The run on id 11 concluded the
opposite and was wrong for a reason worth keeping. apiClass 19, apiClass 13 and
the undocumented one-byte read on the write api all went out -- arb `0x02054fcb`,
`0x0205364b` and `0x0205380b` -- and none was answered, while GET_FIRMWARE on
`0x0205260b` answered in the same session. That run varied the API and never the
frame form: the first two went out as zero-length data frames, which is the one
form this firmware ignores. Sent as remote frames with dlc 8, all eight read
classes and all sixteen type frames answer, on all eight controllers, with zero
silent frames across a full sweep. The one-byte read on the write api stays
unanswered, and it was deleted. Writes ARE answered on this firmware: a write of 159=50 to id
11 returned result 0 Success, verified on the first attempt, and the cadence
changed on the wire. So a Flex value can be confirmed two ways now, by reading the parameter back and
by measuring what it changes. Read it back after every write, because a BOOL
parameter on 26.1.6 stores 2 when written 2 and still answers Success.

**SPARK MAX, firmware 24.0.1 (rig-max): every parameter reads and
writes, on an api REV do not publish.** The parameter id rides in the
arbitration id and the reply comes back on that same id, with no separate
response frame:

    arb   = 0x02050000 | ((0x300 | param_id) << 6) | device_id
    read    DLC 0                 ->  [uint32 value][type][status]
    write   [int32 value][type]   ->  the device echoes the value it took

`0x300 >> 4` is 48, so this is api class 48 and upward in 25+ numbering. All 134
parameters, 0 to 133, answered on all eight controllers: none refused, none
silent. Parameter 0 returns each device's own CAN id, which is the self-check
that the reads are live and per-device. Type tags here are 0 int32, 1 uint32, 2
float32, 3 bool -- a different encoding from the 25+ types frame above -- and
status byte 0 is success. **Do not sweep past 133.** The same api space holds
commands above the table: `0x300 | 255` lands on Persist Parameters.
`admin.read_legacy_param` and `write_legacy_param` implement it and refuse
ids past the table for that reason; PROTOCOL.md section 3c has the derivation.

So every controller on this fleet can be asked for its configuration, and
firmware picks the dialect. A Flex on 26.1.6 takes the eight documented read
classes as remote frames with dlc 8, covering ids 0 to 255. A pre-25 MAX takes
the undocumented api class 48, covering ids 0 to 133. Read back after every
write on both generations.

REV note one product caveat in the spec, on the read-pair frames. SPARK MAX did
not support them in v25.0.0-prerelease.4. The note appears on all 128 READ frames
and on no WRITE frame, so it is scoped to paired reads on a named prerelease, and
whether shipping 25+ MAX firmware supports them is still an open question. That
question is moot on this fleet, which runs no 25+ MAX at all. The MAXes on rig-max
run 24.0.1, and these frames are versionImplemented 25.0.0, so by the spec they
carry none of them. The run asked anyway, and four sends to id 3 drew
no answer. Each of two frames went out as a remote frame with dlc 0 and as a
zero-length data frame. Both forms are now known to be silent on a Flex, so that
probe read stronger than it was. `tools/spark_read_frame_form.py` varied all
three forms at rig-max and all six sends drew silence, which settles
it across the form axis.

### The status frame period trap

On firmware 25 and later, status frame periods are parameters, and their ids do
not run the way they look:

    Status 0 Period.. Status 7 Period    158.. 165    contiguous
    Status 8 Period                       199           NOT 166
    Status 9 Period                       224           NOT 167

166 and 167 are MAXMotion Max Velocity 0 and MAXMotion Max Accel 0, both floats.
Continuing the run puts a frame period into a motion limit. The ids come from
REVLib's `SparkParameters.h`, which declares one parameter enum with no product
split, inherited unchanged by both product configurations.

The unit is MILLISECONDS and REV-Specs' "Status frame 0 period, in us" is a
documentation error. That was measured, not argued: on rig-flex, with
a control group, parameter 159 was written 20 -> 50 on id 11 alone and its
STATUS_1 cadence went 20.0 -> 50.0 ms while the seven other controllers held
20.0 ms throughout, then returned to 20.0 ms on restore. Fifty microseconds
would be 20 kHz and is not achievable on a 1 Mbit bus. REVLib's own comments and
REV's SPARK MAX page say milliseconds with a 1 to 32767 ms range, and they are
right. `provenance.both.status_period_unit`;
docs/runs/rig-flex-hardware-reads.md.

On PRE-25 firmware the periods are not parameters at all. Ids 158 to 165 sit
outside the addressable table there: rig-max answers every parameter 0 to 133 on
api class 48 and returns a non-zero status for the period ids. They
move on api class 6 instead, one frame index per status frame, sharing the
arbitration id of the status broadcast itself and told apart by DLC -- two bytes
of little-endian milliseconds sets the period, eight bytes is status data. The
device sends no acknowledgement, so the only read-back is measuring the cadence.
Asking for 25 ms on STATUS_0 and measuring 25 ms on the wire was reproduced on
rig-max ids 1 and 4; `admin.set_legacy_status_period`.

Whether a period survives a power cycle also splits on generation, and on pre-25
it is not the exception REV's SPARK MAX page makes it sound. NOTHING this
package writes over CAN survives a rail cycle on 24.0.1: on rig-max,
Idle Mode was written BRAKE on all eight and read back BRAKE, the motor rail was
cut and restored, and all eight came back COAST with sticky `0x0200` -- hasReset
-- on every one. The status periods went with them, from this package's 50 ms
boot throttle to REV's 10 ms cold default, which took controller traffic from
384 to 1872 frames a second. There IS a burn on this generation: api 0x072,
arbitration id `0x02051C80 | id`, carrying two bytes of the magic 15011
little-endian -- the same constant REV name on the 25+ Persist Parameters frame
-- which replies 0x00 accepted or 0xFF refused on the request's own arbitration
id. It commits the parameter table, ids 0 to 133, and it was measured NOT to
reach the periods: id 3 had 0x060 set to a distinctive 77 ms, was burned and
accepted, and read 10.0 ms after the cycle, as did the other seven. So on pre-25
the periods are the one thing no burn can save, and they have to be re-sent on
every boot. This package neither sends 0x072 nor exposes it.

On rig-flex's Flex at 26.1.6 a provisioned period does survive a rail cycle. On a
freshly power-cycled bus, seven of eight controllers held the
provisioned 20 ms on STATUS_1 while device 12 sat at REV's 250 ms factory
default -- which is how one unit's real config loss was found, passively, with
zero writes. Read REV's "the rate does not persist" line as scoped to the pre-25
generation its page describes, not to the SPARK MAX as a product.


## 18. Enable, disable and the heartbeat

SKIP IF you already send a 20 ms heartbeat and know what stops the motors.

A SPARK acts as disabled unless it is being told, repeatedly, that a robot is
enabled. So the heartbeat is the enable, and stopping it is a stop.

WHICH heartbeat depends on the generation, and this section used to name only the
FIRST universal heartbeat, arbitration id `0x01011840` at 20 ms, with a device
disabling itself once 100 ms passes without one. That is the vendor rule and it
still stands as written for the devices it covers. It is NOT what enables this
fleet.

**Measured on rig-max, firmware 24.0.1: pre-25 ignores `0x01011840`
entirely.** Eight controllers drove for a whole session from the SECONDARY
heartbeat, `0x02052C80`, which `SparkBus._heartbeat_runnable` sends, with no
universal heartbeat anywhere on the bus. Putting one there at 20 ms changed
nothing: five payloads including all-ones, which is the superset of every
single-bit payload, and the fleet drove during and after every one. It did not
enable, disable or lock anything.

So on this fleet the Secondary Heartbeat is the enable, and stopping THAT is the
stop. REV describe the Secondary as respected "only when the SPARK is not locked
to the Universal Heartbeat or Primary Heartbeat"; on 24.0.1 there is no sign of
that lock being reachable from the bus.

rig-flex's Flex fleet is enabled by the same Secondary Heartbeat, and no universal
heartbeat is on that bus at all. Thirty seconds of passive listening caught 36156 frames across 24 arbitration ids with no `0x01011840`. A
2.5 s listen while one steer drove at 0.15 caught 6294 frames across 42 ids with
none either. The Secondary Heartbeat ran at 20 ms throughout that drive. That
covers the bus as wired, so whether 26.1.6 would honour a universal heartbeat
stays untested. `provenance.rig-flex.no_frc_heartbeat_on_the_spark_bus`.

For an immediate stop, send the zero-length frame on arbitration id 0. Devices
disable immediately on receiving it, and because the frame carries no addressing
it reaches controllers whose id is wrong or duplicated.

**Send it with the heartbeat, not underneath it.** Measured on rig-max, against a steer motor turning at 823 rpm and still being commanded:
with the enable heartbeat left running the disable did NOTHING -- applied output
stayed 0.151 and the wheel was still at 823 rpm two seconds later, because the
heartbeat re-asserts enable every 20 ms and overrides it before the next status
frame. With the heartbeat stopped in the same call, applied reached zero at
31 ms and the wheel was at rest by about 500 ms; letting the heartbeat lapse on
its own took about 240 ms and the best part of a second. So the frame is worth
roughly 200 ms of
stopping distance, and it is worth nothing at all if something keeps saying
"enabled". `SparkBus.broadcast_disable` clears the heartbeat before sending for
this reason.

Neither of these is a safety device. A software stop depends on software running,
and the thing that actually stops a motor is removing the motor rail. Wire a
physical disconnect and treat the CAN path as convenience.

One useful and non-obvious behaviour: a single request addressed to any one
controller wakes every gated transmitter on the bus, and erases nothing. A bus
that has gone silent after a power cycle comes back after one firmware query,
with sticky faults intact. Measured on rig-flex: sixty seconds of
silence changed nothing, then one GET_FIRMWARE addressed to id 17 alone brought
all eight back, sticky hasReset intact. A read wakes the bus and erases nothing,
which is why the boot-time Clear Faults that used to do the waking is not
needed. That measurement is on the Flex fleet at 26.1.6 and has not been
repeated on rig-max.


---

# Part 6 - What experienced teams assume you know

## 19. Assumed knowledge

SKIP IF you have built several of these robots and maintained them for a season.

This section collects the practice experienced builders treat as obvious: the
build steps, the checks and the cautions that a manual assumes you already know.
It is the knowledge a newcomer most needs, and the forums tend to surface it only
after someone has been caught by it. Read it as a pitfalls list.

Ninety-six items, mined from that corpus and REV's documentation.
Each carries a grade:

    [strong]      several independent posts agree, or it is vendor-stated
    [moderate]    more than one source, or one source plus a mechanism
    [one report]  a single team's experience; a lead, not a rule
    [contested]   experienced people openly disagree; the argument is the finding

Where a grade is [contested], the disagreement IS the information. Do not resolve
it by picking the most confident-sounding post.


### 19.1 Mechanical

Bolts, magnets, shafts and gears. The layer software cannot see,
and the one that produces the symptoms most often blamed on software.


**Bond the diametric magnet into the end of the steering shaft with a retaining compound. It ships as a bare press fit, and the module's absolute reference depends on it staying put. Treat it as a required assembly step: the swerve community does, and at least one module manual calls for it.** [strong]

The press fit alone lets the magnet counter-rotate inside the shaft. The
CANcoder then reports the magnet's angle, not the wheel's, so the module loses
its absolute reference. The wheel points somewhere other than where the code
believes, and the module fights the other three.

> It is fully required to LocTite the encoder magnets on a swerve drive. If
> you do not, the magnet can free spin in the shaft, causing the module to not
> know where it is pointing, which is very bad. I would recommend blue or
> green LocTite. Also, if I remember correctly, it says to LocTite the magnets
> on page 4 of the manual for MK4i

*Source: CD 515109*


**An unbonded magnet presents as an INTERMITTENT, impact-triggered misalignment that appears during matches and is gone by the time the robot is back in the pit. Treat 'it fixes itself when we look at it' as a magnet symptom, not a flaky sensor.** [strong]

The magnet only slips when the module takes a shock or a hard reversal. Any
static bench check afterwards reads correct, so teams conclude the hardware is
fine and rewrite code instead.

> During one of our competitions with our mk4i modules, we had issues with our
> swerves. We found that they didn't have any Loctite on the magnets, and the
> magnets were slowly rotating, making the CANcoder drift. We put Loctite on
> all of the modules, and the issue was fixed.

*Source: CD 504401*


**The NEO Vortex is a torque supply, not a bearing. Its shaft must be carried by at least one additional bearing at the far end. The play you feel between shaft and motor before assembly is normal and disappears once the second bearing and the shaft end screw are installed.** [strong]

A newcomer feels the wobble on the bench and files a defective-motor report,
or worse, assembles it cantilevered. The corpus has a Vortex face plate
sheared off by cantilever mounting on an MK4i drive, and separate reports of
Vortex drive shafts bending after driving over a bump.

> you will need a second bearing to support the vortex shaft. The play you
> might be seeing between the shaft and the motor is actually helping with
> assembly here. Once you install the motor into your swerve module and the
> shaft is constrained by the second bearing and the screw the retains the
> shaft, it should no longer have this play/wobble you are seeing.

*Source: CD 511425*


**The SPARK Flex CAN pigtail is 26 AWG, thinner than the 22 AWG the rest of the harness uses. Ferrules, WAGOs and crimps sized for 22 AWG will not hold it.** [strong]

This is the fleet-relevant version of the crimp problem: the Flex rigs are all
Flex. A 26 AWG lead pulls out of a 22 AWG ferrule under vibration and takes
the whole downstream chain with it. It reads as a random CAN dropout rather
than as a connector that was never going to grip.

> Your hunch is correct. The standard CAN wire for FRC is 22 AWG, while the
> SparkFLEX uses 26 AWG wire.

*Source: CD 497359*


**A single bad crimp silences every device downstream of it, not the device it belongs to. Count your connectors: each one is an independent chance to lose half the robot.** [strong]

This is the price of the daisy chain, and it is worth stating plainly. The consequence is
disproportionate to the fault, so a trivial connector on a minor mechanism can
kill the drive. It also sets the debugging order: look for the break, not for
the broken-looking device.

> The likelihood of failure in the traditional arrangement is high because a
> signal could pass through 16 or more connectors on the way between the RIO
> and a Talon. That's 16 opportunities for failure. The consequence of failure
> in the traditional arrangement is that you lose communication with every
> device downstream of the device. That turns a minor failure in a redundant
> intake motor into a ma...

*Source: CD 358995*


**The tug test on every single termination, immediately after making it, is the accepted acceptance test. Nobody writes it down because everyone was taught it on day one.** [strong]

A crimp or spring terminal that fails the tug test will not fail on the bench;
it fails under vibration hours later, and by then the symptom presents as a
controller fault or a bus break rather than as a wiring defect.

> [AriMB] 'Tug test every connection after it is made. If it can not stand up
> to you pulling on it, it will not stand up in competition.' [nat8622] 'It is
> a given, but never forget the tried and true tug test.' [JoeyD, 254
> CheesyCare] 'If a wire can be tugged loose without any mechanical
> resistance, it will eventually come loose on its own. Every wire connection
> on your robot should have some for...

*Source: CD 358731*


**Strain-relieve within about 1.5 in of the controller by zip-tying the thin wires to the thick ones, and hot-glue every non-latching connector. Wires break at the crimp, not in the middle of the run.** [strong]

Every SPARK connector (JST sensor, CAN/PWM, Dupont) is non-latching or barely
latching. Without a tie-down the flex point lands exactly on the crimp, which
is the weakest section, and the wire fatigues there.

> [bbonner, 6328] 'Zip-tie the sensor wires to the motor power wires within
> 1.5" or so of the controller. This acts as a strain relief; the wires almost
> always break at the crimp point, so if you prevent it from flexing there,
> you largely solve the problem... You should do the same on the CAN
> connector/power input side; those use the same connector type and only
> marginally sturdier wire.' [Techn...

*Source: CD 469824*


**Use a RETAINING COMPOUND (Loctite 603/609/638 class), not a thread locker (242/243 class). Experienced builders treat these as different product families; newcomers read 'Loctite' as one product. Thread locker is better than nothing but is the wrong chemistry for a cylindrical slip joint.** [moderate]

Thread locker is formulated to fill the helical gap of a thread under axial
clamp. A magnet in a bored shaft is a plain cylindrical bond line with no
clamp, which is what retaining compound exists for. A correct bond means the
magnet cannot be removed at all.

> I think SDS has made a couple of different recommendations for encoder
> magnet adhesive and either will do but they are all retaining compound and
> NOT thread locker. As my student builders found out last night that even
> thread locker is much, much better than nothing (two modules each (wrong and
> much, much more wrong) way. I prefer to read the instructions and follow
> them and if you do you will...

*Source: CD 515109*


**The signature of a migrating magnet is a CUMULATIVE divergence between the absolute encoder and the motor's integrated encoder, growing with each azimuth rotation. Rotate one module 360 degrees by hand and compare the two sensors; repeat several turns. This is the diagnostic regulars reach for first and it is written down nowhere.** [moderate]

A slipping magnet does not fail cleanly. It reads correctly at power-up and
degrades with use, so a bench test at zero degrees passes and the robot is
wrong by the third match. Measuring one sensor alone cannot see it; the
comparison can.

> After one full rotation, a discrepancy of about 5-15 degrees appears between
> the two encoders. This error is cumulative; the gap widens with each
> subsequent rotation.

*Source: CD 504401*


**Twisting the magnet with your fingers is NOT a valid retention test. The accepted test is an inertia slam: drive the azimuth hard one way, then command full output the other way, repeatedly. Only rotational inertia reproduces the slip.** [moderate]

Finger torque is a fraction of what a 50:1 azimuth reduction applies during a
direction reversal. A team in the corpus passed the finger test, believed
their magnets were bonded, and kept chasing a software cause that did not
exist.

> To test if magnets are slipping, drive the azimuth really fast, then "slam"
> or apply full output to the other direction. Do this a bunch. This inertia
> will reproduce magnet slippage if that is your issue

*Source: CD 510887*


**Standard thread lockers (Loctite 242/243) attack polycarbonate and other plastics. Loctite 425 is the plastic-safe option. This also rules out wet 243 on the fasteners that thread into a SPARK Flex, whose outer casing is plastic.** [moderate]

The bolt does not fail; the part around it crazes and cracks weeks later.
Because the cause and the effect are separated in time, teams blame impact
damage.

> Loctite and polycarbonate: not all Loctite products are safe for use near
> polycarbonate. Standard threadlockers like 242/243 will craze and crack
> polycarbonate on contact. If you need a threadlocker near polycarbonate,
> Loctite 425 is a cyanoacrylate-based option that is plastic safe.

*Source: CD 520595*


**A set screw clamping onto a plain round shaft is treated by regulars as a known-bad joint. Under a torque spike it gives up and the part free-spins. The accepted fixes are a cross pin, a flat, a key, or a spline; the set screw is a stopgap.** [moderate]

It fails suddenly and completely under shock rather than degrading, and the
mechanism keeps running with no torque transmitted, so the electrical and
software layers see a perfectly healthy motor turning a load that is not
moving.

> we've had many systems over many years fail due to the system getting a jolt
> of torque and then free-spinning after the set screw gives out

*Source: CD 507157*


**On a SPARK Flex plus NEO Vortex, an EEPROM or Sensor fault is first a MECHANICAL seating problem. Check that all four docking screws are fully installed and torqued before treating it as an electronics failure. REV documents a crisscross tightening pattern at 11.5 in-lb.** [moderate]

The Vortex Motor Interface Connector is held closed only by those four screws.
Partly seated, it reads as a firmware-layer fault, which sends you into the
parameter table instead of a hex key. This bears on the Flex rigs directly: all
eight controllers on this fleet are Flex, and the corpus records another
Flex/Vortex swerve whose per-match overcurrent and CAN errors dropped sharply
after the motors were simply undocked and re-seated.

> Double check that your docking screws are fully installed. If the Motor
> Interface Connector may not be fully seated if the screws are not installed.
> This usually presents as a Sensor Fault, but the motor also has an EEPROM to
> store motor-specific parameters.

*Source: CD 449880*


**Grease the module gears and clean debris out on a schedule, but do not over-grease the bevel gear nearest the carpet. Both halves of that rule are treated as obvious and neither is in a REV or SDS manual as a maintenance interval.** [moderate]

Dry or contaminated gears raise the drive current for the same duty, which is
exactly the kind of elevated draw that gets misread as a controller or
current-limit problem. Excess grease on the exposed bevel collects carpet
fibre and makes the contamination worse.

> We just use white lithium ion grease. Don't go nuts on the bevel gear
> attached to the wheel, as it can quickly become a magnet for field carpet
> fuzz

*Source: CD 459914*


**The Mode Button and the DFU path are the fragile part of provisioning, not the software. Press it with a blunt tool -- a straightened paper clip or SIM tool -- feel for the click, and never with a sharp point or heavy force. On the host, the DFU device is a separate USB enumeration that needs its own driver or permission before the client can see it.** [moderate]

A sharp tool slips into the gap beside a misaligned button and permanently
kills it, and REV says some early Flex batches shipped with that misalignment.
On the host side, an invisible DFU device produces exactly the same symptom as
a failed button press -- nothing appears in the client -- so people replace
hardware that is fine. On Windows this cost teams a whole migration season
until REV added a driver-install button in RHC2 v1.0.5; the equivalent Linux
question (udev rule or libusb permission for the STM32 DFU device on a
Debian/Ubuntu host) is not established here and should be checked before the
first rig-flex recovery attempt.

> REV: "DO NOT use a sharp tool to press the Mode Button. Safety pins,
> thumbtacks, pinbacks buttons, and other sharp tools will cause damage to the
> Mode Button's material... Some early batches of SPARK Flex Motor Controllers
> have variances in the alignment of the Mode Button and the case hole...
> avoid the gap between the button and the printed circuit board (PCB)." Also:
> "Do not use any type of p...

*Source: CD 512390*


**On a SPARK MAX the Mode Button changes motor type when held 3-4 seconds, and brushed mode with a brushless motor attached destroys the motor. Treat the button as a hazard on a MAX, and know that on a SPARK Flex the same button only toggles brake/coast because the brushed option is not available without the Flex Dock.** [moderate]

The button is deliberately recessed but it is the same button used for
brake/coast (short press) and for recovery mode (held from power-off), so
anyone poking at it during bring-up can walk a MAX into brushed mode by
holding a fraction too long. The LED is the only feedback: blue or yellow
instead of cyan or magenta means brushed. This is a MAX/Flex asymmetry worth
knowing on a mixed fleet -- the MAX rigs are MAX and carry the risk,
the Flex rigs are Flex and do not.

> REV, SPARK MAX: "It is very important to have the SPARK MAX configured for
> the appropriate motor type. Operating in Brushed Mode with a brushless motor
> connected will permanently damage the motor! With power turned on, press and
> hold the MODE button for approximately 3 - 4 seconds. The Status LED will
> change and indicate which motor type is selected." REV, SPARK Flex Operating
> Modes, documents...

*Source: REV docs*


**NEO phase wires are too large for a standard WAGO, and the SPARK's own thin wires need a different termination than the power wires. One termination standard for the whole robot does not work.** [moderate]

People pick one connector family for tidiness and then force the extremes into
it. The 12 AWG power leads and the 26 AWG CAN pigtail bracket a range no
single crimp die or lever nut covers.

> [JoeyD] 'NEO phase wires are too large to fit properly in standard Wagos and
> should use a different termination method.' [ArchdukeTim] 'Because anderson
> connectors are not meant for wires smaller than 20 AWG, and signal wires
> like CAN are often 22-26 AWG... There is not much reason the power and CAN
> connections need the same standard, since they will never be plugged into
> one another.'

*Source: CD 520595*


**The magnet is a PRESS FIT by design and the vendor ships it that way, so a team replacing an encoder will re-press it and never think to bond it. Assume any module you did not personally bond has an unretained magnet.** [one report]

The failure is invisible at assembly and only appears after hours of driving.
The corpus contains a team that had already swapped a CANcoder, chased the
fault for weeks, and only then asked whether bonding the magnet was even a
thing.

> CANcoder Magnet Mounting: When I replaced the CANcoder, I just press-fit the
> new magnet. Is there a best practice for this? Would you recommend using a
> retaining compound like Loctite, or is that not advised?

*Source: CD 504401*


**Scribe a witness mark across the magnet face and the shaft end at assembly, so that later rotation is visible without instrumenting anything.** [one report]

Without a mark you cannot tell a slipped magnet from a software offset bug,
because both present as a wrong wheel angle. With a mark you rule the magnet
in or out in five seconds in the pit.

> The magnet on the problem module has a mark to track its rotation and it
> never rotates off that mark, so it is glued down.

*Source: CD 425148*


**A GREEN magnet-health reading proves field strength only. It does not prove the magnet is diametrically polarized, and it does not prove the magnet is retained. Do not read green as 'the encoder mechanical install is correct'.** [one report]

This bears directly on this fleet: the harvested corpus in canbus-
a CANcoder check capture, which is not in this repository, reports
MagnetHealthValue.MAGNET_GREEN and '[OK]' on all four rig-flex CANcoders, and
the probe prints 'All corners healthy'. That output is being read as a clean
bill of mechanical health, which the corpus says it is not.

> You need a field of 200 to 1000 gauss at the sensor.... If the LED is green
> on the Canandcoder, you're good to go. Just make sure to actually use a
> diametrically polarized magnet! It'll turn green with enough field, even if
> the magnet isn't polarized properly.

*Source: CD 437329*


**A magnet installed the wrong way up produces the same class of azimuth misalignment as a slipping one. Check orientation before rewriting offsets.** [one report]

Both faults present identically at the software layer, so a team that has
ruled out slip can still be looking at a mechanical cause. Reported but
thinly, so treat it as a lead rather than a rule.

> We had a similar problem that was caused by our absolute coder magnets were
> upside down. But I don't know if it's the same problem as yours.

*Source: CD 472000*


**Do not expect REV documentation to cover the encoder magnet. It is absent because REV's own on-axis Through Bore Encoder has no separate magnet to retain. The practice belongs to the CTRE CANcoder plus SDS-module combination, which is what this fleet actually runs.** [one report]

A newcomer who reads only REV docs will never encounter the step. Searching
the 1040-file REV/WPILib doc dump for magnet retention returns nothing but
navigation-bar hits: there is no REV page on bonding an encoder magnet, and
the nearest page, 'Securing the Encoder Adapters', is about zip-tying a
breakout board to a SPARK MAX.

> Would you ever consider adding 2x 2in spacing threaded holes to the new top-
> plate of the flip and a longer shaft option to accommodate the through bore
> encoder? This would also eliminate the need for a magnet pressed in the
> shaft and also the aluminum plate and standoffs needed to mount the mag
> encoder.

*Source: CD 397877*


**Treat the terminating resistor as a real connection: trim its leads to the connector's strip length, insulate it, and secure it. Do not leave a bare axial resistor dangling in a lever nut.** [one report]

A leaded resistor is the worst mechanical fit in the whole harness and it sits
at the electrically most important point. If it shakes loose or shorts to the
frame, the whole bus degrades and the failure looks nothing like a loose
resistor. One team lost their bus terminator when the robot was dropped.

> Tape up all terminating resistors, cut resistor leads down to wago strip
> length, and don't use knockoff wagos -- our swerve bus terminating resistor
> on our CAN-FD bus on a knockoff wago somehow malfunctioned when our robot
> was dropped, either it touched the frame or the knockoff wago failed

*Source: CD 520595*


**Route wires so nothing can pinch them, and never make a joint in the middle of a run through a moving cable path.** [one report]

An abraded or pinched conductor inside a drag chain produces mid-run dropouts
that come and go with mechanism position, which is the hardest failure to
correlate with anything in a log.

> [JoeyD] 'Wires routed through dynamic cable management systems like igus
> chains, or near rotating mechanisms, are at risk of getting pinched or worn
> through over time. We saw cases of this causing mid-match dropouts. Route
> wires carefully, avoid making connections mid-run as these are prone to
> failure, and inspect them regularly throughout the season.'

*Source: CD 520595*


**There is no agreed torque figure for a battery lug and no agreed washer. Published team practice spans 70 in-lb to 7 ft-lb, and the star-washer vs Nord-Lock question is an open argument between experienced people.** [contested]

A newcomer reading one confident post will torque to a number that another
equally confident source calls wrong. The thing that is actually agreed is the
outcome test (the lug must not rotate), not the number.

> [Team 900 test, CD 415795] 'I torqued the fasteners on both sides to 70in-lb
>... I then connected a torque wrench to the lug and measured the torque at
> which the lug twisted on the tab. Results: Plain lug-tab connection: 55in-
> lb. Star washer: 35in-lb and 45in-lb.' Against that, CD 446922: 'The
> batteries are made with 6in 4 AWG leads and terminals, torqued to 7 ft-lb.'
> And [Al_Skierkiewicz] 'I d...

*Source: CD 415795*


**Do not solder a crimp, and do not tin a stranded wire before putting it into a spring terminal. Crimp with the correct die tool, not a vise.** [contested]

Heating anneals the copper and creates a brittle transition just outside the
joint; solder wicking up a strand bundle makes a stress riser where the wire
flexes. A vise-made crimp can look identical to a good one and measure many
times the resistance.

> [AriMB] 'Don't try to crimp the connections with a vise, even though the
> finished product may look the same as when you use the right tool. Crimps
> can look good from the outside but actually have an extremely high
> resistance. Also, don't solder them.' [gcschmit-adjacent post, CD 462632]
> 'It is also our preferred method to make the end of a wire solid compared to
> tinning the end with solder as t...

*Source: CD 358731*


### 19.2 Electrical and wiring

Crimps, gauges, terminations and the battery path.


**Mechanically support every JST connector and sensor wire on a NEO or SPARK: zip-tie the sensor bundle to the power leads within about 1.5 inches of the controller, and put a small bead of hot glue where the wires enter the connector shell and where the plug seats.** [strong]

These wires break at the crimp, not in the middle, and a broken hall or
encoder wire presents as a sensor fault or a motor that will not commutate,
i.e. it looks like a controller problem. This is the most-repeated electrical-
mechanical practice in the corpus and appears in no REV wiring page.

> These wires are the Achilles' heel of the SparkMax+Neo/Neo550. Best practice
> from our experience with SparkMaxes is: Zip-tie the sensor wires to the
> motor power wires within 1.5" or so of the controller. This acts as a strain
> relief; the wires almost always break at the crimp point, so if you prevent
> it from flexing there, you largely solve the problem.

*Source: CD 469824*


**Put a 120 ohm resistor at BOTH physical ends of the bus, then prove it with a multimeter: a healthy bus reads about 60 ohms across CANH and CANL. 120 ohms means only one end is terminated.** [strong]

CAN transceivers only actively drive the dominant bit and rely on the
resistors to pull the line back to recessive. With one terminator the bus
half-works, so it presents as a flaky device or an intermittent motor instead
of as a wiring fault. 60 ohms is the single fastest check a newcomer can run,
and it is the one most worth memorising.

> The CAN bus should have two terminating resistors in parallel, each 120
> ohms, resulting in a ~60 ohm measurement on a fully intact CAN bus. (If you
> read 120 ohms, that indicates you only have continuity to a single
> terminating resistor, meaning the bus is not terminated at one end, maybe
> because it's broken in the middle.)

*Source: CD 503777*


**Neither the SPARK MAX nor the SPARK Flex contains a terminating resistor. On a Linux rig with no roboRIO and no PDH you must supply BOTH 120 ohm terminators yourself.** [strong]

Every FRC-facing instruction assumes the roboRIO supplies one end and the PDP or
PDH the other, which is why the question rarely comes up on a competition robot.
The rigs measured here carry neither device on the SPARK bus, so a bus built by
daisy-chaining SPARKs alone has zero termination and reads as several kilo-ohms.
The closest primary source is REV's own instruction to add both terminators when
the bus is SPARKs plus a power source.

> If you are only using multiple SPARK MAXs and a power source, you can
> terminate both ends of your CAN Bus with 120 ohm resistors!

*Source: REV docs*


**In stock FRC the two terminators are inside the roboRIO and inside the PDP/PDH, which is why the standard wiring order is roboRIO first and PDP/PDH last. The PDH's is switchable; the roboRIO's is not.** [strong]

This is the fact that makes every piece of FRC advice sound like you only need
one resistor. Carrying that advice onto a roboRIO-free robot leaves the bus
unterminated at both ends. It also explains the FRC habit of routing the chain
so the PDP lands at the end.

> It is recommended that the wiring starts at the roboRIO and ends at the PDP
> because the CAN network is required to be terminated by 120:math:`\Omega`
> resistors and these are built into these two devices. The PDP ships with the
> CAN bus terminating resistor jumper in the "ON" position.

*Source: CD 430997*


**The SPARK's two CAN connectors are not two ports. They are one pair wired straight through inside the case, and the pass-through keeps working when the controller loses power.** [strong]

A newcomer sees two connectors and assumes the controller repeats or switches
the bus, then wonders why unplugging one SPARK kills the rest. The internal
link is what makes the daisy chain possible, and it is also why a dead SPARK
does not by itself break the bus. That distinction tells you whether to chase
power or wiring.

> Each matching wire pair is physically connected to its functional
> counterpart within the device. Even if the SPARK Flex loses power, the CAN
> bus remains unbroken, leaving downstream devices unaffected.

*Source: REV docs*


**Use twisted pair, 22 AWG or heavier, yellow for CAN High and green for CAN Low, and keep the colours consistent at every single connector.** [strong]

CAN is differential, so twisting is what makes external noise hit both
conductors equally and cancel. The yellow/green convention is not cosmetic:
every vendor's connector is keyed and labelled to it, and a swap somewhere in
the middle produces a bus that half works. REV warns that mismatched colours
are hard to diagnose precisely because the symptom appears bus-wide.

> A CAN Bus connection should be made using at least 22 AWG (0.5 mm2) twisted
> pair cable, color-coded in Yellow/Green. This is because CAN Bus is a
> differential signal, and twisted pair cable ensures that electromagnetic
> interference affects the signal in both wires uniformly, limiting errors.

*Source: CD 491147*


**A slow orange/blue blink means 12V is missing. On a controller powered only through USB-C during provisioning that is the expected state, not a fault -- it is the controller telling you it will not drive a motor. Bring up 12V separately when you actually need to spin something.** [strong]

Provisioning happens over USB with the robot powered down, so this blink code
is the first thing a newcomer sees on a bench and the first thing they mistake
for a broken unit. REV attaches the warning to the code precisely because
people try to run motors on USB power. Conversely, a controller showing
orange/blue while it is genuinely on the PDH is a real power-path fault worth
chasing.

> REV's table: "12V Missing: The motor will not drive if powered only by USB.
> This blink code warns the user of this condition. Orange/Blue Slow Blink."
> Independently on the bench: "Is it plugged in to 12V power? I thought ours
> had that issue, but it turns out it blinks like that when it's plugged in
> with USB power only and not 12V." And: "When on the bot, blinking [blue] for
> brake mode brushless...

*Source: CD 346981*


**Bolted main-power joints (battery lug, main breaker, PDP/PDH input) loosen inside one event. The field test is that you cannot rotate the lug by hand, and nylock nuts are avoided on these joints.** [strong]

A joint that has lost clamping force still passes a continuity check but adds
milliohms, and at 100-200 A a few milliohms is volts. The sag reads as a bad
battery or a brownout, and a nylock hides it because the nut holds position
without holding pressure.

> [AriMB] 'Every connection there should be rock solid or your robot will
> reset on the field. If you can twist the connection with your hand, it is
> too loose. Don't be afraid to really put some torque on the bolts. A few
> milliseconds of bad connection while the connector twists can be enough to
> cause a radio reboot. Don't use nylock nuts on these connections. They can
> hide loose connections by ho...

*Source: CD 358731*


**Anderson Powerpole housings slide together on dovetails and must be pinned or tied. Never snap them together, and confirm the contact latched fully inside the housing.** [strong]

REV states directly that a contact seated wrong causes intermittent SPARK
brownouts. A half-mated pair looks correct from outside and drops out only on
impact, which is the hardest class of fault to reproduce on a bench.

> REV: 'Do not attempt to snap the housings together as they can break. After
> the housings are mated together adding a roll pin prevents the housings from
> become detached during operation... Having improper placement of the
> contact in the housing can lead to intermittent brownouts of the SPARK MAX,
> the contact dislodging from the housing, or have cause problems with one or
> more of the phases of...

*Source: CD 520595*


**Strip length into a spring terminal (WAGO, Weidmuller, PDH input) is a spec printed on the part, not a judgement call.** [strong]

Too short and the clamp lands on insulation, giving an intermittent joint. Too
long and there is exposed conductor next to a 40 A rail. REV says this was the
single biggest field problem with the PDH, and one team traced a whole
brownout hunt to it.

> [Greg_Needel, REV] 'We have added a pad print to the top of the device to
> indicate the strip length for the main power wires. Stripping the wire to
> short or too long was the main issue teams have had with the device so we
> hope this helps.' [Weldingrod1] 'When we were chasing brownout issues we
> actually used the PDH voltage measurements to look at the resistance of the
> battery, SB50, main breake...

*Source: CD 442216*


**A breaker protects the wire, not the motor. Size it from the wire gauge and the supply-side current, and protect the motor separately in software.** [strong]

Newcomers size breakers from the motor's stall current or from the number the
controller reports, both of which are the wrong quantity. That leads either to
a breaker too big for its wire (a fire risk) or to nuisance trips blamed on
the motor.

> [Domtech] 'Breakers exist to protect the wires from melting if there is a
> short or overload... You can use smaller wire if you want but for those
> smaller wires to be adequately protected you must also use a smaller
> breaker.' [Jwal] 'stick with the general principal: the fuse protects the
> wire.' REV states the SPARK side of it: 'It is also highly recommended to
> add a fuse or circuit breaker in...

*Source: CD 509339*


**Battery internal resistance under about 20 mOhm is the retire-it line for a 12 V SLA, but the meter reads the whole loop, so your own crimps show up as battery wear.** [strong]

Two failures wear the same mask. A bad lug or a worn SB50 raises the measured
resistance and the tester says the battery is bad, so teams retire good
batteries for a season while the real fault stays on the robot. Note this
threshold is SLA-specific and does not transfer to LiFePO4.

> [AriMB] 'Use a battery beak (or similar) to test each battery internal
> resistance under load. Good batteries usually have an internal resistance of
> less than 20 mOhm (this number can vary though). Once the battery passes
> this point, they should be removed from the rotation.' [Rich_Anderson] 'Bad
> battery connections will also be read by the Battery Beak as internal
> resistance, and the Beak will...

*Source: CD 358731*


**Keep the battery, main breaker and power distribution physically close together and the heavy cable short. The whole main-power resistance budget is a few milliohms.** [strong]

Every extra inch of 6 AWG and every extra joint eats voltage under load.
Published per-joint measurements are in the tenths of a milliohm, so a long
run or one extra connector is a measurable part of the sag that later reads as
a brownout or a bad battery.

> [AriMB] 'Make sure the high current wires are as short as possible. Your
> battery, main breaker, and PDP should all be close together. All of the
> power flows through those wires; do not make them any longer than necessary
> or you will see large voltage drops.' Measured breakdown from CD 495049:
> '0mV (0 mOhm): Battery post to Cu fitting; 20 mV (0.4mOhm): Cu fitting at
> battery to wire-SB50 crimp; 5...

*Source: CD 358731*


**At FRC/robot bus lengths, getting the TOTAL termination to about 60 ohms matters more than where the two resistors physically sit.** [moderate]

Reflections only matter once the round-trip time is long against the
transceiver's ~45 ns edge. Under roughly 5 m the bus behaves like a lumped
load, so a resistor in the wrong place is survivable while a missing one is
not. Knowing this stops a newcomer from rebuilding a harness when the real
defect is a loose terminator.

> Having the total resistance between CANH and CANL be close to 60 ohms is
> more important than exactly where the resistors are until the bus gets to be
> more than 5 meters or so. The longer the bus, the longer it takes for
> reflections to die out.

*Source: CD 430997*


**Anderson SB50 contacts are rated for 250 mating cycles and wear out inside one season on the robot side. Worn contacts present as brownouts that look exactly like a dying battery.** [moderate]

The robot-side connector sees every battery swap, so it wears fastest. Silver
plating rubs off, contact resistance climbs, and every diagnostic points at
the battery. People replace good batteries for a season before anyone looks at
the pins.

> [philso] 'The standard, silver-plated contacts used with the SB50 connectors
> are rated for 250 mating cycles... If the silver plating is worn off the
> contact area so that it is copper colored, the contact resistance will be
> higher, especially if the surface has become rough from the wear... Our
> 2024 robot was browning out when used for driver practice in Nov 2024...
> After replacing the conta...

*Source: CD 481104*


**Use an auto-reset breaker rather than a fuse on any rail that shares a battery with motors.** [moderate]

A brownout drops the rail voltage, so a constant-power load pulls
proportionally more current for a few milliseconds. That transient blows a
fuse it would never blow in steady state, and unlike a breaker the fuse stays
blown for the rest of the run.

> [saraansh] 'when compressing, the compressor draws ~8-10 amps but the robot
> browning out for a fraction of the second led to a current spike that blew
> the fuse.' [Usernam3] 'A shorter spike of current that would burn out a fuse
> would not flip a breaker due to a breakers longer trip time. Also, a breaker
> would reset during the match if it did blow... We had blown multiple fuses
> on our PH too, a...

*Source: CD 408278*


**A thermal breaker that has tripped is scrap. Replace it rather than resetting and reusing it.** [moderate]

The bimetallic element takes a set, so the trip point drops permanently. The
robot then trips earlier and earlier and the symptom reads as a worsening
electrical fault somewhere else entirely.

> [JoeyD] 'Once a main breaker has tripped, it becomes more sensitive to
> future trips and should be replaced. Keep spare main breakers in your pit.'

*Source: CD 520595*


**If you use the Flex's PWM mode, tape over the exposed pins on the unused connector and clip the mating pair together.** [one report]

The same four 26 AWG wires serve CAN and PWM, so the connector you are not
using is live and exposed on a metal chassis. REV ships retention clips
(REV-11-1229) precisely because the connectors are not latching.

> REV: 'When using the PWM interface, only one of the two connectors should be
> used... it is best practice to secure the unused wires and protect the
> exposed pins by covering them with electrical tape. When daisy-chaining or
> extending the connections, use the included PWM Cable Clips (REV-11-1229) to
> secure the two mating connectors together to prevent unintended
> disconnections.'

*Source: REV docs*


**Heat-shrink every exposed battery terminal and secure the pack so it cannot shift. Anti-spark or pre-charge connectors are not part of this practice.** [one report]

A pack that moves a few millimetres per session abrades the insulation over a
season and turns into a short across a source with no current limit. Note
honestly: the corpus contains no anti-spark or pre-charge connector practice
at all, so if you want inrush limiting on a bench rig, that is not established
here.

> [JoeyD] 'Use heat shrink on all exposed battery terminals. Exposed terminals
> are a short waiting to happen. Make sure your battery is mounted securely
> and cannot move around in its battery box. Even small amounts of movement
> over the course of a season can wear through heat shrink or electrical tape
> on the leads, creating a short risk.'

*Source: CD 520595*


**Ferrules on signal and small-gauge wire are near-universal practice. Ferrules on 12 AWG and larger into PDP/PDH spring terminals are genuinely disputed, including a claimed one-size derating.** [contested]

A newcomer who reads 'always use ferrules' and applies it to the 12 AWG SPARK
power leads may be reducing the current-carrying capability of the highest-
current joint on the robot. The people arguing about this are the most
experienced electrical people in the corpus, and they do not agree.

> [cmwilson13] 'When the ctre pdp was released they spent a considerable
> amount of time trying to convince teams NOT to use ferrules on the main pdp
> power ports due to to the MASSIVE reduction in current carrying capability
> it causes. ferrules are great for sensor wires but nothing bigger than 16
> gauge in an frc application... the data sheet for the connectors used on
> the rev pdb specifics that...

*Source: CD 425506*


**40 A on a drive motor and 30 A on a steer motor are both normal. Which you pick is a convenience and inventory decision, not a correctness one.** [contested]

The 30-vs-40 argument reads like a technical disagreement but is really about
slot allocation, colour-coding and wire stocking. A newcomer who thinks there
is a right answer will over-index on it and miss the actual constraint, which
is the PDP's limited high-current slots.

> [Henry_B] '30a breakers also work fine for steer motors... if you give all
> the drivebase motors 40A slots on a PDP, you are left with no high amperage
> slots for subsystems. even then, a steer motor wont draw more than 30A.'
> [thatnameistaken] 'Using 30A also opens the steering motors up to using 14
> AWG wire... There is no performance benefit to running 40A breakers on
> steering motors.' [w4dron...

*Source: CD 459667*


### 19.3 The CAN bus itself

Topology, termination and the daisy chain's consequences.


**Bus length is not the constraint on a robot of this size. The 1 Mbit/s limit is 40 m of trunk, and a robot harness is a small fraction of that.** [strong]

Newcomers who read CAN literature start budgeting length and worrying about
propagation delay. The real budget on a robot is spent on stubs and
connectors, not trunk metres. Knowing the headroom lets you route the harness
for mechanical protection instead of for length.

> At high speed (1mbps), the recommended max bus length for CAN is 40 m. At
> this length, the actual electrical design is important and two 120 ohm
> resistors (one at each end) are a must. CAN also allows for unterminated
> stubs from the bus, at 1mbps the recommended length without any electrical
> design thought is 0.3m.

*Source: CD 149241*


**A yellow/green swap is one of the two most common causes of a bus that mostly works. Suspect it before suspecting a device.** [strong]

A single swap flips one device's view of the pair, so that device talks
garbage and corrupts frames for everyone. Two swaps isolate one device between
them, which is even harder to see. Because the bus still passes most traffic,
the symptom looks like an intermittent motor or a marginal controller.

> In my experience, the most common causes are a yellow-green swap somewhere
> or duplicate CAN IDs. Since your CAN bus seems mostly stable, the yellow-
> green swap might actually be a pair of yellow-green swaps (making it that
> only the device between them is "flipped", which "confuses" the rest of the
> CAN bus when that one device tries to "talk").

*Source: CD 517325*


**Arbitration ID 0 (all 29 bits zero, zero-length payload) is the broadcast Disable. It is not a device address and no device may use it.** [strong]

Anyone writing raw frames on SocketCAN will eventually build an arbitration ID
by shifting fields together, and a bug that zeroes them disables every
actuator on the bus. Read the other way, it is the correct emergency stop: it
reaches controllers whose ID is wrong or duplicated and needs no per-device
addressing. This repo already relies on it.

> Devices should disable immediately when receiving the Disable message (arbID
> 0). Implementation of other broadcast messages is optional.

*Source: WPILib FIRST CAN Device Specifications*


**CTRE and REV devices coexist fine on one standard CAN bus. A CANivore bus is a different animal: CAN-FD, CTRE devices only, and it cannot be joined to the standard bus.** [strong]

Newcomers see two CAN connectors on a robot and assume they are
interchangeable. Putting a SPARK on a CANivore leg gives you a device that
never enumerates and no error explaining why. On this fleet the split is
already made: SPARKs on can0 (gs_usb, 1 Mbit/s), CANcoders on the
CANivore in CAN-FD.

> Keep in mind that the CANivore (today at least) only supports CTRE CAN FD
> devices - so if you have Falcons and CANcoders you're set, but if you're
> running Spark Maxes those won't work. Also be aware that the CAN FD bus is
> much pickier about topology than standard CAN.

*Source: CD 430997*


**The SPARK Flex and SPARK MAX lock out USB control the moment they have seen a roboRIO (or any enable heartbeat) on the CAN bus. Before you can drive or reliably provision one over USB-C, unplug its CAN pigtail and power-cycle the controller. The lock re-arms if it sees the heartbeat again. It still obeys setpoints arriving over CAN while locked, so the device is not broken.** [strong]

A newcomer plugs USB-C into a controller sitting on a live bus, presses Run in
the hardware client, sees nothing move and a solid magenta or cyan LED, and
concludes the controller or the motor is dead. Any host running this driver
transmits an enable heartbeat every 20 ms (0x02052C80,
`can_bus.SparkBus._heartbeat_runnable`), so the bus is never quiet unless that
process is stopped and the rail cycled. Nothing on the bus reports the USB
lockout, and whether this specific heartbeat frame is what arms it is NOT
established here -- REV describe the trigger as seeing a roboRIO.

Do not read the STATUS_0 lock bit as this lock. PRIMARY_HEARTBEAT_LOCK, bit 53,
decoded by `admin.decode_status_0` and reported by
`admin.status_problems`, is a different mechanism: REV-spark-frames-2.1.0
describes it as the SPARK being "in competition mode and will ignore the
Secondary Heartbeat until it is power cycled", and 0x02052C80 is precisely that
Secondary Heartbeat. It says nothing about USB. The two share only their remedy,
which is a power cycle -- no CAN command releases either.

> REV docs: "Please be aware of the CAN lockout feature of the SPARK Flex. If
> it has been connected to the roboRIO's CAN bus, a safety feature within the
> SPARK Flex will lock out USB communication. Disconnecting from the CAN bus
> and power cycling the MAX will release the lock." REV staff (dyanoshak):
> "This is related to a safety feature that locks out USB control when the
> Flex (or MAX) sees a rob...

*Source: CD 453509*


**Assigning a CAN ID through the REV Hardware Client hits every device on the bus that currently shares the ID you are changing. Provision identity one controller at a time, with the CAN chain disconnected or with only the target powered, before anything else touches the bus.** [strong]

Out of the box, or after a reset, every controller is CAN ID 0. Plug eight of
them onto one bus and the client shows one device, because an ID-based scan
cannot distinguish them. Set that device to 1 and all eight become 1. The
newcomer symptom is a client that keeps showing fewer devices than are
physically installed, or a fleet where a parameter write reaches several
controllers while only one answer is read.

> "For some reason when the user sets a CAN ID in the REV Hardware Client,
> that CAN ID is set to ALL REV devices connected via the CAN bus, even if I'm
> trying to only set the device that I am currently directly plugged into via
> USB-C... I'm forced to unplug every Spark Max from the CAN bus, then go
> around plugging in via USB-C one at a time to set each motor controllers'
> CAN ID." Corroborated: "T...

*Source: CD 445298*


**CAN ID 0 is the reserved 'unconfigured' value. A controller at 0 will not enable and will not drive, whatever else you do to it. Any controller found at 0 after provisioning has lost its identity -- new, freshly firmware-updated, factory reset, or defective.** [strong]

A newcomer reads the LED as a fault or the motor as dead when the actual state
is 'this device was never given a name'. On rig-flex the driver treats an
unconfigured ID seen broadcasting as exactly this signal
(`can_bus.SparkBus.observed_spark_ids`).
There is also a Flex-specific hardware defect where an individual unit drops
its ID to 0 on every power cycle no matter how many times it is burned; REV
replaces those units, so a single controller that keeps coming back as 0 while
its neighbours hold is the controller and not your code.

> REV firmware changelog v1.1.31: "CAN ID of 0 now considered 'unconfigured'
> and will not be enabled." REV staff listing the causes of a non-enabling
> controller: "The SPARK MAX does not have its CAN ID saved, it must have a
> value greater than 0." REV docs: "Any configured SPARK MAX must have a CAN
> ID." Community summary: "A device will have an ID of 0 if it is new, has its
> firmware updated, or ha...

*Source: CD 346537*


**The SPARK Flex CAN pigtail is 26 AWG, thinner than every other wire on an FRC-style CAN harness, and ferrules/crimps sized for the usual 22 AWG will not hold it.** [strong]

The pigtail slides back out of a correctly-crimped ferrule with no load on it
at all. The failure looks like a dead controller or a bus break, so people
replace the SPARK (which does not help) instead of re-terminating the wire.

> [BouncingBuilder] 'whenever there was a can failure, it originated from the
> sparkflex can wire... Our sparkflex can constantly falls out of the
> ferrules they were crimped to, even with none to minimal stress on the wire.
> When we replaced the sparkflex, the same phenomena still occurred.'
> [Zatack7] 'Your hunch is correct. The standard CAN wire for FRC is 22 AWG,
> while the SparkFLEX uses 26 AWG...

*Source: CD 497359*


**Measure CAN termination with the battery unplugged and expect about 60 ohms between CANH and CANL. 120 ohms means one terminator missing, near zero means a short, kilohms means no termination at all.** [strong]

It is the fastest wiring test there is and it cannot be done live. People try
to diagnose a silent bus with the rail up, get a meaningless number, and go
looking for firmware problems instead.

> [NathanZhou] 'If you are experiencing wiring issue, the fastest method is
> unplug the battery and measure resistance between your two CAN wire. Normal
> resistance should be around 60 Ohm. If it is reading 120 Ohm, one
> termination resistor is missing... If it is 0 Ohm, it is shorted and if it
> is several kOhm, there is no termination on the line.' [sdtrent] 'With
> everything powered off... use a D...

*Source: CD 502487*


**Priority on a shared bus goes by device type first, then manufacturer, then device number. A lower device ID wins a tie.** [moderate]

This is why ID assignment is not arbitrary once the bus is loaded: the device
you most need to hear from should not sit at the high end of the range. It
also explains reports that REV devices suffer first when CTRE traffic is
heavy, though the direction of that effect is disputed by CTRE staff in the
same thread.

> The device ID is part of the arbitration ID too, which means device 4 will
> take priority over device 7. That's just a consequence of how CAN intrinsic
> arbitration works, it's not due to some sinister favoritism.

*Source: CD 358946*


**The 64-address ceiling per device type and manufacturer comes from the 6-bit device field, not from CAN itself. ISO 11898 recommends at most 30 nodes at 1 Mbit/s.** [moderate]

Two different limits get conflated. The 64 figure is an FRC addressing
artefact inherited from the Jaguar; the 30-node figure is an electrical
loading limit from the standard. A newcomer who reads only the first number
will happily build a 40-node bus and then hunt a phantom software fault.

> Having 64 devices on the CAN bus is an artifact of the way the CAN MessageID
> field was partitioned for Jaguar, it isn't really a CAN thing.

*Source: CD 138836*


**Strip the 26 AWG Flex pigtail by hand or with a tool that actually has a 26 AWG die. A normal FRC wire stripper severs most of the strands.** [moderate]

The wire keeps its insulation profile and looks fine, but half the conductor
is gone. The joint then works until vibration finishes the remaining strands,
producing an intermittent CAN dropout that no continuity check catches while
the robot is at rest.

> [mtuckerFRC4381] 'Definitely a durability issue with the 26 AWG wire. I have
> found the only way to safely strip the insulation and not sacrifice any of
> the few strands is wire inside is to use my finger nails. A wire stripper it
> far too aggressive to use.' [TFM110] 'Almost all wire strippers that you can
> find only go down to 22awg... If you want to use manual stripers, it
> probably will not eve...

*Source: CD 497359*


**Soldering the CAN bus is possible and widely done, but it is the option that fails silently and is unrepairable in the field.** [moderate]

A cold or partly-wicked joint passes a bench continuity test and fails under
vibration. Worse, it cannot be undone quickly when something else needs
swapping, so a five-minute repair becomes a rebuild.

> [Skyehawk] 'You are going to get a LOT of poliarized opinions around
> soldering. You have been warned... (Most of the issues people seem to have
> is soldering is annoying and people soldering well is rare)'... 'Complete
> loss of the can buss, intermittent problems to debug, etc.'
> [Another_Person1] 'Soldering the CAN bus has its own considerations, namely
> that it is very easy to solder incorrectly...

*Source: CD 512740*


**CAN is a twisted pair, at least 22 AWG, colour-coded yellow (CANH) / green (CANL) end to end. The 26 AWG SPARK Flex pigtail sits below that community minimum, and the colours must match connector to connector along the whole chain.** [moderate]

Twisting is what makes the differential rejection work; a mismatched colour
anywhere swaps H and L for everything past that point. REV explicitly warns
this produces bus-wide symptoms that are very hard to trace back to one
connector.

> CD 491147: 'A CAN Bus connection should be made using at least 22 AWG (0.5
> mm2) twisted pair cable, color-coded in Yellow/Green. This is because CAN
> Bus is a differential signal, and twisted pair cable ensures that
> electromagnetic interference affects the signal in both wires uniformly,
> limiting errors.' REV Flex: 'Pay close attention when daisy-chaining
> devices, and make sure that the colors m...

*Source: CD 491147*


**The SPARK Flex CAN pigtail passes the bus through the harness, so killing the motor rail does not break CAN for the devices past it. Repo practice already depends on this: CANcoders sit on the logic rail, SPARKs on the motor rail.** [moderate]

A newcomer expects a dead controller to break the chain and will misread the
failure signature. On this fleet, an e-stop or a tripped motor-rail breaker
silences the SPARKs while CANcoders keep answering, which is a diagnostic
signal rather than a second fault.

> REV: 'Each matching wire pair is physically connected to its functional
> counterpart within the device. Even if the SPARK Flex loses power, the CAN
> bus remains unbroken, leaving downstream devices unaffected.' Repo: 'If the
> CANcoders are healthy and the SPARKs are missing, check motor power first:
> CANcoders run off the CAN/logic rail and SPARKs off the motor rail, so a
> breaker or e-stop silences...

*Source: REV docs*


**Device number 63 (0x3F) is reserved for device-specific broadcast. Do not assign it, and do not decode it as a real device.** [one report]

63 is the top of the 6-bit device field, so a naive sweep or mask will happily
report it. It is also the address CTRE's diagnostic server broadcasts to,
which is how this fleet's ID sweep produced phantom devices. Only one source
in the corpus states the reservation.

> Device Number is a 6-bit quantity indicating the number of the device of a
> particular type. Devices should default to device ID 0 to match other
> components of the FIRST Control System. Device 0x3F may be reserved for
> device specific broadcast messages.

*Source: WPILib FIRST CAN Device Specifications*


**Wire a daisy chain (or a single trunk with stubs under 12 inches). Do not run a star from a central hub.** [contested]

Each stub is an impedance discontinuity that reflects signal energy back onto
the trunk. At FRC scale and 1 Mbit/s a star often works anyway, which is
exactly the trap: it fails later, under vibration or higher load, and the
failure gives you no clue where to look. The stub number people quote comes
from ISO 11898, not from FRC.

> "The High-Speed ISO 11898 Standard specifications are given for a maximum
> signaling rate of 1 Mbps with a bus length of 40 m with a maximum of 30
> nodes. It also recommends a maximum unterminated stub length of 0.3 m." So
> 11.8" stubs are allowed. The CTRE wires coming out of the Talons are about
> that long so adding wire to achieve a star topology is not a good idea.

*Source: CD 358995*


**CAN IDs only have to be unique within one combination of device type and manufacturer. A SPARK MAX at 1 and a CANcoder at 1 can coexist. Assign globally unique IDs anyway.** [contested]

The FRC arbitration ID carries device type and manufacturer above the device
number, so the full 29-bit IDs differ even when the device numbers match. The
reason to still keep them unique is tooling and human error, not the protocol:
at least one team lost all device enumeration in Phoenix Tuner from exactly
this overlap. rig-flex keeps the two ranges apart by putting SPARKs at 10-17 and
CANcoders at 1-4 on a separate bus.

> Device IDs only need to be unique within a given combination of device type
> and manufacturer. The FRC CAN device specification includes the device type
> and manufacturer in the arbitration ID, and CAN only requires that no two
> devices try to use the same arbitration ID. As a result, you can have a
> SPARK MAX with ID 1, a Talon FX with ID 1, and a CANcoder with ID 1 all on
> the same CAN bus.

*Source: CD 481557*


### 19.4 Firmware and configuration

What a firmware update does to your settings, and what the button does.


**When two SPARKs already share an ID, changing one over CAN changes all of them. Pull them off the bus and set IDs one at a time over USB.** [strong]

The ID-set command is addressed by ID, so every controller answering that ID
takes it. A newcomer who tries to fix a duplicate over the bus makes it worse
and cannot tell which unit is which. Duplicates also hide devices from the
client, so the collision is invisible until you go one by one.

> To assign unique CAN IDs -- which is something you must do -- you will
> probably need to temporarily disconnect the CAN cable and use a USB-C
> directly plugged into the motor controller you want to change.
> Unfortunately, duplicate CAN IDs tend to make all but one SPARK MAX not show
> up in the REV H/W Client, and if you try to change the CAN ID, you will
> often wind up changing every SPARK MAX that...

*Source: CD 427801*


**Status frame periods are the lever for utilisation. Slow down the frames you do not read before you buy a second bus.** [strong]

The default periodic rates assume you want everything; most subsystems read
two or three signals. Teams routinely cut utilisation by more than half this
way. On PRE-25 firmware these periods do not survive a power cycle and no burn
saves them, so they have to be re-applied on every boot and after any has-reset
event; on rig-flex's Flex at 26.1.6 a provisioned period does survive one. Section
17 carries both measurements.

> I recommend trying to fix this with status frame periods (as well as double-
> checking wiring and IDs) first: you'd be surprised by how much a difference
> it can make. We've been able to lower CAN utilization to ~25%; our CAN bus
> has the RIO, PDP, PCM, and 10 SparkMaxes.

*Source: CD 408307*


**Every parameter you write lands in RAM. It reaches flash only when you issue the persist (called Burn Flash before REV Hardware Client 1.7.0, Persist Parameters after). Leave at least 200 ms between the last parameter write and the persist, and do not send more configuration for a couple of seconds afterwards.** [strong]

The persist blocks CAN communication on the device while it runs, so writes
sent just before or just after it are dropped or committed as the pre-write
value, and both operations still report success. Nothing errors and nothing
reports the wrong value back. The failure only shows up after the next power
cycle, when the controller comes back with the value you thought you replaced
-- or, for an unpersisted current limit, at REV's 80 A default in the middle
of a match after a brownout.

> REV documents the blocking: "Persisting parameters involves saving them to
> the SPARK controller's memory, which is time-intensive and blocks
> communication with the device." Field remedy: "It turned out that burning
> configuration to flash was an issue if it was not delayed long enough after
> sending configuration messages. We used a wrapper from 1018 that put all
> constructed SparkMaxes into a lis...

*Source: CD 432129*


**Brownout thresholds are per-device and differ across this fleet: REV documents 5.5 V for SPARK MAX and 4.5 V for SPARK Flex. The 6.8/6.3/4.5 V staged scheme everyone quotes is the roboRIO's, and this fleet has no roboRIO.** [strong]

On a roboRIO robot the RIO detects sag and sends CAN motor controllers an
explicit disable command, which is what stops the sag. Nothing on a Linux
robot does that job unless the driver is written to. A sag deep enough to
reset the MAX rigs (MAX, 5.5 V) may leave the Flex rigs (Flex, 4.5 V) running,
so the fleet browns out unevenly.

> REV Flex: 'If the supply voltage drops below 4.5 V the SPARK Flex will brown
> out, which can result in unexpected behavior.' REV MAX: 'If the supply
> voltage drops below 5.5V the SPARK MAX will brown out, resulting in
> unexpected behavior.' WPILib roboRIO: 'Stage 1 - 6v output drop, Voltage
> Trigger - 6.8V... Stage 2 - Output Disable, Voltage Trigger - 6.3V... CAN-
> based motor controllers are sent...

*Source: REV docs*


**A 'factory reset' means two different things and you must know which one you are invoking. REVLib's kResetSafeParameters deliberately spares five parameters -- CAN ID, Motor Type, Idle Mode, PWM Input Deadband, Duty Cycle Offset -- and resets everything else. The older restoreFactoryDefaults() and the hardware client's 'complete factory reset' are broader and take the CAN ID with them.** [moderate]

Two opposite mistakes follow from not knowing this. Call
restoreFactoryDefaults() as the first line of a config routine, a common
copied pattern, and you have just renamed every controller to CAN ID 0 and are
relying on the rest of the routine to put things back. Conversely, configure
with kResetSafeParameters and omit the current limit from the config object,
and the 50 A you set by hand in the hardware client silently goes back to
REV's 80 A default -- the CAN ID survives, so nothing looks wrong.

> REVLib header, verbatim: "If resetMode is ResetMode::kResetSafeParameters,
> this method will reset safe writable parameters to their default values
> before setting the given configuration. The following parameters will not be
> reset by this action: CAN ID, Motor Type, Idle Mode, PWM Input Deadband, and
> Duty Cycle Offset." Forum, the older call: "I believe the
> shooterLeftMasterMotor.restoreFactoryD...

*Source: CD 381069*


**REV Hardware Client 1.x is Windows-only. REV Hardware Client 2 is the first version with a native Linux build (Ubuntu 24.04 / Debian 12 or newer), and it requires every REV device to be on 26.x firmware; anything on 2025 firmware or older must be dragged up through recovery mode before RHC2 will talk to it at all.** [moderate]

On a Linux robot with no roboRIO and no Windows machine in the loop, this
decides whether you can provision at all. Someone who assumes the client is
cross-platform loses a day. Someone who installs RHC2 against a fleet on 2025
firmware sees every controller listed as an 'Outdated REV Device' and has no
path forward except recovery mode, which wipes the config. the Flex rigs sit at
26.1.6, so they are past that gate, but a replacement Flex out of a box on
2025 firmware is not.

> RHC 1.x system requirements, verbatim: "Operating System: Windows 10
> (64-bit) or newer." Its troubleshooting pages repeat "a native Windows based
> computer with the REV Hardware Client installed." RHC2: "Operating System
> Options: Windows 11, Windows 10 (version 1709 - October 2017) or newer;
> macOS 15 (Sequoia) or newer; Linux: Ubuntu 24.04/Debian 12 or newer." And:
> "REV Devices must be running F...

*Source: CD 506380*


**Voltage is a poor state-of-charge proxy on this fleet's chemistry, so the FRC corpus's voltage-sag rules of thumb do not carry over. The repo already refuses to infer a percentage from LiFePO4 rail voltage.** [one report]

Almost every brownout heuristic in the forum corpus assumes a 12 V lead-acid
pack with a usable voltage slope. LiFePO4 sits near flat across most of its
range, so reading a percentage off the rail is invented precision, and a
reading that looks healthy can be close to empty.

> Repo: 'Rail voltage is graded only when base.battery.full_v / empty_v are
> set for the chemistry. It is not a state of charge: LiFePO4 sits between
> roughly 13.0 and 13.3 V across most of its usable range, and voltage sags
> under load and with age.' and 'a percentage inferred from LiFePO4 voltage
> would be invented precision (the curve is nearly flat from ~13.0 to ~13.3
> V)'.

*Source: the host application's power monitor*


**CAN ID 0 is the factory default and means unconfigured. Assign every SPARK a unique ID before it goes on a bus.** [contested]

Out of the box every controller answers to the same address, so a bus of new
SPARKs is a bus of collisions. Every controller on this fleet carries an
assigned id, so none sits at 0: rig-max's MAXes at 1-8 and rig-flex's Flexes at
10-17. Note that REV's own pages disagree on the top of the range:
the control-interfaces page says 1 to 62, the getting-started page says 1 to
63. Treat 1-62 as the safe range because 63 is the FRC broadcast address.

> Out of the box, SPARK Flex is assigned a device ID of 0. This ID is
> considered "unconfigured" and must be assigned to a unique number from 1 to
> 62.

*Source: REV docs*


**REV's parameter table annotates exactly six parameters as surviving a firmware update: kCanID, kMotorType, kSensorType, kIdleMode, kInputDeadband and kDataPortConfig. Treat everything else -- current limits, PID gains, status-frame periods, limit-switch polarity, conversion factors -- as gone after a firmware update, and re-provision from a written-down config rather than from memory.** [contested]

The same REV page opens with a blanket sentence saying parameters 'persist
through a firmware update', which reads as a promise that nothing is lost,
then contradicts itself by annotating only six entries. A newcomer flashes
26.x onto a fleet, sees the CAN IDs intact, assumes the whole config came
through, and drives a robot whose steer motors are back at REV's 80 A default
and whose Status 1 period is back at 250 ms. There is also field precedent for
the CAN ID itself being lost: REV's president told teams to reassign IDs after
the 25.0.0 update, and REV shipped 25.0.1 to fix it.

> REV, same page, two statements: "The parameters are saved in a different
> region of memory from the device firmware and persist through a firmware
> update." and then, per row, "kCanID 0 uint 0 CAN ID This parameter persists
> through a normal firmware update." (annotation present on only kCanID,
> kMotorType, kSensorType, kIdleMode, kInputDeadband, kDataPortConfig). REV's
> president (Greg_Needel), dur...

*Source: CD 471083*


**On PRE-25 firmware, status-frame periods revert on every power cycle -- and so does every other parameter this package writes over CAN, so the periods are not the exception REV's page makes them sound. Code that relies on a fast Status 1 or Status 5 must re-send the period after every controller reboot, not once at provisioning. On firmware 25+ the period is parameter id 159 and a provisioned value survives a rail cycle, measured on rig-flex's Flex at 26.1.6 -- so scope the rule by FIRMWARE GENERATION, not by product.** [contested]

Get this backwards in either direction and you misread the bus. Assume it
persists on pre-25 and your absolute-encoder frame silently returns to its
default period after a brownout, degrading odometry with no error. Assume it
does not persist on rig-flex's Flex and you dismiss the single most useful
passive canary this fleet has: on a freshly power-cycled bus, device 12
broadcast Status 1 at 250 ms (REV's factory default) while the other seven
held the provisioned 20 ms. That is persisted config loss, visible with zero
writes, and it implies the other deviating parameters on that unit reverted
too.

The pre-25 half is measured, and it is broader than the periods. On rig-max at
24.0.1, Idle Mode was written BRAKE on all eight over the legacy
parameter api and read back BRAKE; after a motor-rail cycle all eight read COAST
again, the factory default, with sticky `0x0200` on every one. The periods went
back to REV's 10 ms cold defaults in the same cycle. Api 0x072 with the magic
15011 does burn the parameter table on that firmware and was measured not to
reach the periods, so on pre-25 the periods really are unsavable -- but the
reason is not that everything else is safe.

> REV, documented for SPARK MAX: "This rate can be changed manually in code,
> but unlike other parameters, this setting does not persist through a power
> cycle. The rate can be set anywhere from a minimum 1ms to a maximum 32767ms
> period." Forum, same claim: "One consideration is that not all config
> settings are persisted -- in particular, the periodic status frame periods
> are volatile." and "Since...

*Source: CD 407018*


**Once known-good settings are burned to flash, stop factory-resetting and rewriting them on every boot. Persist at provisioning; on boot, verify rather than rewrite.** [contested]

A reset-then-rewrite boot path makes every single parameter depend on a CAN
write landing, and REVLib config setters do not retry on timeout. One dropped
write during the traffic burst at robot init leaves exactly one setting wrong
on exactly one controller, with no error anywhere. There is also flash-
endurance pressure, though the numbers here are an estimate rather than a REV
figure. This is genuinely contested practice: other experienced builders argue
the opposite, that a controller should be fully configured from code every
boot so a swapped-in spare 'just works'.

> REV's president: "If you are burning to flash and have a known good
> settings, there isn't really a need to reset every time. By resetting to
> default you are relying on setting the parameters in code each time. If for
> a number of reasons including can bus errors something doesn't set, you
> won't have that parameter." The opposing position, same corpus: "Write your
> code and setup such that you can...

*Source: CD 461113*


### 19.5 Operational practice

How to work on the robot without creating the next fault.


**Thread-lock every fastener that does not have a nylock on the other end, then do a full bolt check after the first few hours of running. Regulars treat 'assemble once and drive' as obviously wrong; the manual never states a re-torque interval.** [strong]

Modules shed screws into their own gear trains. The corpus has bolts backing
out until an intermediate gear falls out of mesh, a loose bolt jamming a
steering gear, and a module seizing on a dropped fastener. It is the single
most repeated mechanical finding in the corpus.

> General assembly issues: missing bolts, missing spacers, missing or
> incorrect bearings, and no Loctite on fasteners all showed up repeatedly
> across many different module types. Double check every step of the
> manufacturer's assembly guide before your first competition, and do a full
> bolt check after your first few matches.

*Source: CD 520595*


**Keep bus utilisation under about 70 percent with no bad peaks, and treat the reported number as noisy.** [strong]

Above that, frames start losing arbitration and the symptoms are
indistinguishable from failing hardware: parameters that silently do not
apply, motors that miss initialisation, followers that stutter. Poor wiring
inflates utilisation through retransmits, so a high number can be a wiring
symptom rather than a bandwidth problem.

> Check CAN bus utilization (I like to see under 70% without bad peaks above
> average) and make sure your CAN wiring and termination are good.

*Source: CD 457114*


**Recovery mode (USB DFU) is the bottom of the recovery ladder and it erases everything. Robot 12V off, USB-C only, hold the Mode Button with a blunt tool while plugging in, wait for the host to enumerate the DFU device, then release. The LED stays completely dark the whole time -- that is correct, not a dead controller. Afterwards, power-cycle, clear sticky faults, and re-provision the CAN ID and every parameter from scratch. DFU works only over USB, never over CAN.** [strong]

Every stage of this looks like failure to someone doing it the first time. A
dark LED reads as a bricked unit, but dark is also what USB DFU and corrupt
firmware look like, so the LED cannot tell you which. Leaving robot power on
is the most common reason the device never enters recovery at all. And because
recovery wipes the device, anyone who runs it without a written config to
restore from has just turned a firmware problem into a configuration problem.

> REV: "Performing this procedure will erase all data and settings on the
> device. Be sure to burn your desired settings to flash after recovering the
> device." and "With the Device powered off, press and hold the Mode Button.
> While still holding the Mode Button, connect the Device to the computer
> using the USB-C cable - the Status LED will not illuminate - this is
> expected... Power cycle your devi...

*Source: CD 372085*


**Learn the two axes of the standard LED before treating any colour as a fault. Blinking means no valid signal, solid means valid signal -- so a blinking controller on a disabled robot is correct. Colour carries motor type and idle mode: cyan is brushless/brake, magenta is brushless/coast, blue is brushed/brake, yellow is brushed/coast. Only the orange-paired slow blinks are faults.** [strong]

Blinking cyan or blinking magenta is the single most-reported 'my controller
is broken' symptom in the corpus, and in most of those threads the controller
is fine and the enable signal is missing. Reading the colour also tells you
the persisted motor type and idle mode without any software: a Vortex showing
blue or yellow is configured brushed, which will destroy the motor if it is
driven.

> REV's table: "Brushless Brake No Signal Cyan Blink / Valid Signal Cyan
> Solid; Coast No Signal Magenta Blink / Valid Signal Magenta Solid." REV
> staff: "If the device is blinking Magenta it means that it is not enabled,
> in order to be enabled it must receive a valid heartbeat signal."
> Independently: "Generally blinking magenta means they are configured for
> brushless motors and have no signal. Thi...

*Source: CD 347176*


**The Has Reset flag is how you find out a controller rebooted underneath you. Read it (or its sticky version) on every loop or at least at bring-up: it means the device power-cycled since faults were last cleared, so anything that lived only in RAM -- status-frame periods on a MAX, any unpersisted parameter -- is now back at default and must be re-sent.** [strong]

A controller that resets mid-operation comes back apparently healthy: it
enumerates, it broadcasts, it answers. But its encoder position is zeroed and
its volatile settings are gone, and nothing else on the bus announces that.
Teams chasing 'the shooter spun backwards once' or 'one swerve module fought
the others' are usually chasing this. It is also the cheap check that turns a
reconfiguration into an event-driven action instead of a per-loop CAN storm.

> REV staff: "There is a 'hasReset' flag in the status bits returned by the
> controller at a rate of 10ms. The flag is cleared by the API when you first
> run, so if this is set it is safe to assume the controller reset during
> operation. You can monitor this flag and take action to reinitialize." Same
> source on what it costs you: "If the controller resets it will not [keep
> track of its position]......

*Source: CD 377014*


**A bench power supply cannot run a drivetrain. Put a battery in parallel with the supply, or accept that you can only spin motors free.** [strong]

A lab supply's protection circuit trips on motor inrush long before the
current is dangerous, so the rig appears to have a wiring fault that is
actually the supply defending itself. REV says the same thing from the
controller side: use a source that can handle surge current.

> [jnicho15] 'if you are actually driving around, you might draw upwards of
> 300A. These spikes are rather common, and have a good chance of tripping the
> protection circuitry in a nice power supply. A better idea may be to have a
> single battery in parallel with a charger.'... '548 bought a 13.5V ~50A
> power supply to test motors/gearboxes. Even with a single motor, we had to
> connect the motors thr...

*Source: CD 167245*


**Label both ends of every wire and keep a written map of what is on which channel and which CAN id. This is treated as an electrical deliverable, not documentation overhead.** [strong]

When something dies mid-session, tracing a harness costs the whole session.
The map is also where CAN ids get reconciled between the people who set them
and the people who write code against them.

> [AriMB] 'Take the time to label every wire on both sides, even if it is
> easily traceable (but especially if it is not). You will be thanking
> yourself later when you need to debug or replace something.'
> [Nate_Laverdure] 'Have the electrical subteam create and promulgate a
> living, shared document that contains all the information about the
> configuration of the robot electronics... Setting CAN ID...

*Source: CD 358731*


**Never lift or carry a battery by its leads, and mark any dropped battery bad until it is load-tested.** [strong]

The terminals are an electrical connection, not a mechanical one. Damage to
the internal plate connection raises internal resistance permanently and does
not show on a no-load voltage check, so the battery keeps getting used and
keeps causing unexplained sag.

> [AriMB] 'Never lift your batteries from the wires! The terminals are only an
> electrical connection, not mechanical. You will likely damage the battery
> internals, and kill the battery.' WPILib: 'Never lift or carry batteries by
> the wires! Carrying batteries by the wires has the potential to damage the
> internal connection between the terminals and the plates, dramatically
> increasing internal resi...

*Source: CD 358731*


**Take the resistance and continuity readings with the bus powered down and silent. A live bus cannot be measured.** [moderate]

A meter injects its own current; traffic on the pair makes the reading
meaningless. Newcomers measure with the robot on, get a nonsense number, and
conclude the wiring is fine. This repo already states the rule but does not
say why.

> Note that you can't measure continuity or termination resistance while
> there's data moving across the bus. When the robot is turned off, there
> should be about 60 ohms of resistance between any yellow point on the bus
> and any green point, and every point of the same color should be connected
> with no resistance.

*Source: CD 495731*


**Locate a break by what still enumerates, not by which device looks worst. On this fleet the lowest missing device ID is where the chain stops.** [moderate]

The device that fails first is the one nearest the break, not the one that is
broken. On a roboRIO robot you plug USB-C into any SPARK and the client shows
everything it can still reach; on rig-flex the equivalent is the ID sweep, and
the ordering of the IDs along the harness is what makes it a bisection instead
of a guess.

> If you connect the USB-C cable to one of the REV devices (SparkMax, PDH,
> etc) and open the REV Hardware Client, do all expected devices show up in
> the list? If not, you have a break in your CAN wiring somewhere (either a
> disconnect/bad crimp/etc, or an inversion (green->yellow, yellow->green))

*Source: CD 429371*


**How old a sticky bit can be splits on firmware generation. On 25+, a power cycle clears the sticky byte as well as an explicit Clear Faults, so a sticky fault you can see happened since the last power-up. On PRE-25 the byte survives a power cycle and the cycle itself SETS hasReset, so the event may predate the last power-up. Either way, clearing faults at boot destroys the record for no benefit.** [moderate]

Newcomers read a clean fault word as proof the hardware has never misbehaved,
and read a set bit as ancient history. Both are wrong, and on 25+ the window is
much shorter than it sounds on a robot being power-cycled through testing. On
rig-flex the driver clears sticky faults on every SparkFlex at boot
(`can_bus.SparkBus.apply_boot_config`) on the stated premise that the Flex
latches them across power cycles -- and that premise was tested on this fleet
and did not hold for the one bit that could be tested. What the boot-time clear
actually achieves is waking a silent bus, which a plain read frame does
without erasing the record.

The pre-25 half comes from rig-max at 24.0.1: three motor-rail cycles, two deliberate and one from a battery swap, each brought all eight
controllers back at sticky `0x0200` and no other bit set. `0x0200` is bit 9,
hasReset. So on a MAX, hasReset is what dates the record, and the REV line
quoted below -- written on the pre-25 SPARK MAX page -- is the one place this
generation split is easiest to carry the wrong way.
`admin._STICKY_AGE_PRE25` and `_STICKY_AGE_FW25` carry both sentences so
that an operator is never told the opposite of the truth for the generation in
front of them.

> REV: "Sticky Faults: The same as the Faults field, however the bits do not
> reset until a power cycle or a 'Clear Faults' command is sent." Measured on
> rig-flex, fw 26.1.6: "marker set on all eight sticky_faults = ['can'], no
> active fault / motor rail cut and restored / bus woken with GET_FIRMWARE (a
> read; it erases nothing) / after: hasReset on all eight, sticky faults
> CLEAN. Every controller sho...

*Source: REV docs*


**Provision in a fixed order and finish one controller before starting the next: firmware first, then CAN ID, then parameters, then persist, then power-cycle and verify. Get every controller on the bus onto the same firmware version before you debug anything else.** [moderate]

Each step invalidates the ones before it, so any other order wastes work: a
firmware update can take the CAN ID with it, a reset takes the parameters, and
a persist that was not paced does not take. Mixed firmware across one bus is
also its own failure source and it is cheap to rule out first. The verify step is cheap on both generations of this fleet, because each answers a parameter read. A Flex on 26.1.6 answers reads across parameters 0 to 255, and rig-max's pre-25 MAXes answer 0 to 133. Section 17 gives both dialects, including the api class 48 form the pre-25 fleet answers. Power-cycle first and read afterwards, because a read before the cycle reports RAM and flash may differ. On pre-25 this package sends no burn, so commit the flash over USB-C before you trust that verify.

> A team's ordered bring-up list: "Make sure all Spark Max controllers are at
> the same firmware version... Do a factory reset on all the controllers using
> the Spark Max Client... Check the bus with the Spark Max Client... Look for
> other problems, learn the blink color codes... Do a factory reset in your
> java code. Burn the final controller settings in flash when you're done
> setting them." Communi...

*Source: CD 378088*


**Before believing anything about which controller is which, use the client's Device Identify function -- it fast-blinks white/magenta on the physical unit. Record the hardware serial next to the CAN ID and the mechanism, because after a reset the CAN ID is the one thing that does not survive.** [moderate]

A bus of eight identical black boxes behind a chassis gives you no way to map
ID 13 to the left-front steer motor except by blinking it, and getting that
map wrong sends you replacing a healthy controller. Identify is also the
fastest way to catch a duplicate ID: press it and two units blink. On rig-flex
the serials are already the stable key -- the passive snapshots record serial
alongside role for all eight (dev 13 = steer/RF, serial 4D9AB6E7), which is
what lets a controller be re-identified after it reverts to ID 0.

> REV's table: "Device Identify: White/Magenta Fast Blink." REV docs: "Device
> Identify: Blink the selected SPARK MAX's LED for identification." REV staff
> pointing teams at it: "@AfterTen and @Will_Toth beat me to it! This is what
> the Identify buttons look like." Using it to find duplicates: "you would
> still need to use the 'identify' function to know which two MAXs were
> sharing a CAN ID." The fai...

*Source: CD 373252*


**Make every power connection with the rail dead. A lead-acid or LiFePO4 pack is a far harsher source than a bench supply and will destroy a controller a supply would survive.** [moderate]

REV says reversing V+ and V- permanently damages a SPARK and voids the
warranty, and reverse-polarity protection circuits that survive a current-
limited supply do not survive a battery. Hot-plugging into a live rail is also
called out by REV as a safety risk in its own right.

> REV: 'As with any electrical component, make all connections with the power
> turned off. Connecting the SPARK MAX to a powered system may result in
> unexpected behavior an may pose a safety risk.' and 'DO NOT reverse V+ and
> V- or swap motor and power connections. Doing so will cause permanent damage
> to the SPARK MAX and will void the warranty.' [juchong] 'ProTip: Testing
> using a bench supply is o...

*Source: CD 440931*


**REV publishes a per-match module inspection checklist, and it is the one place a REV-only reader will find the mechanical items. Look for it under the build system docs rather than under the motor or controller docs, which is where most people search first.** [one report]

It names the specific items that go wrong on this hardware, including the NEO
shaft key fouling the encoder fork, and it is the documented counterpart to
the forum's bolt-check habit. Note that it covers REV MAXSwerve, not the SDS
modules this fleet runs.

> We recommend checking the following items before each match... Manually
> spin each wheel to ensure the motor key is securely in place and is not in
> contact with the encoder fork

*Source: CD 464321*


**Expect the quirks not to be documented at all. Experienced users of this hardware state outright that the operating knowledge is transmitted person to person rather than through the manuals.** [one report]

This is the meta-item that justifies mining the forum in the first place. A
newcomer who assumes the vendor documentation is complete will keep hitting
failures that the community regards as settled, solved and not worth
restating.

> YES you have to be VERY careful with how you implement them, and there are
> quirks you just gotta know. The hard part is that a lot of the quirks aren't
> in the docs, instead it's trial by fire or quite a bit of googling.

*Source: CD 507157*


**No termination measurement exists for the Flex rigs anywhere in this corpus. The 60 ohm figure in the repo docs is inherited practice, not a reading taken on this fleet.** [one report]

The CAN setup checklist and the failure catalogue both state the 60 ohm
target, and both trace to Chief Delphi and WPILib rather than to a meter on
these robots. Given that these buses have no roboRIO and no PDH, the
assumption most worth testing first is that they are terminated at all.
Nothing in the probe logs records what supplies termination on can0.

> 4. Wiring: ~60 ohm across CANH/CANL (two 120 ohm terminators), CANH->CANH /
> CANL->CANL.

*Source: sparklib motor configuration notes*


**Re-torque rotational fasteners every 1-3 duty cycles and spring terminals about once per event. This is a scheduled maintenance item, not a build step.** [one report]

The first few hard impacts are what loosen things. A robot that was perfect at
build time will have a marginal main-power joint after a few hours of driving,
and nothing in software will report it.

> WPILib: 'Create a checklist for re-checking electrical connections on a
> regular basis. As a very rough starting point, rotational fasteners such as
> battery and PDP connections should be checked every 1-3 matches. Spring type
> connections such as the WAGO and Weidmuller connectors likely only need to
> be checked once per event.'

*Source: WPILib documentation*


**Fitting the wrong breaker size is one of the most common defects found on other people's robots, not an exotic mistake.** [one report]

It is invisible until it matters, and it fails in both directions: an
oversized breaker leaves the wire unprotected, an undersized one causes
nuisance trips that get blamed on the motor or the code.

> [JoeyD, after helping hundreds of robots across a season] 'Make sure you are
> using the correct breaker size for each device on your PDP/PDH. We saw 40A
> breakers on radios and 10A breakers on swerve modules this year, both of
> which are incorrect. Using the wrong breaker size can either fail to protect
> a device or cause nuisance trips.'

*Source: CD 520595*


**Do not plug USB into a SPARK MAX that is behaving oddly. A shorted phase can back-feed the USB port and destroy the host machine.** [one report]

On a Linux robot the host is the whole control system, and USB is the normal
configuration path. The controller most likely to need a USB session is
exactly the one most likely to have the fault. Only one report exists in this
corpus, so treat it as a caution rather than a settled fact.

> [JoeyD] 'REV Spark MAX USB warning: While I never saw it personally, I did
> hear about it and this is a concerning issue to be aware of. If a Spark MAX
> has a shorted phase wire, connecting to it via USB-C can send voltage back
> through the USB connection and fry your laptop motherboard. If a motor
> controller is behaving unexpectedly, be very cautious before plugging in via
> USB. This happened to m...

*Source: CD 520595*


---

## Sources

`the Chief Delphi corpus (chiefdelphi.com)` holds the harvest: `cd_topics.json` is every topic found,
`threads/` the fetched bodies. [FIELD-REPORTS.md](FIELD-REPORTS.md) beside this
file lists all 158 graded findings with their URLs, including the ones that did
not make it into this guide, and carries the withdrawals and corrections made
since the harvest.

Vendor statements are attributed to REV or CTRE staff posting under their own
accounts. Corroborated items appear independently in more than one thread or rest
on a posted measurement. Reported items are one team's experience and may not
generalise. Follow the link before acting on anything that matters.
