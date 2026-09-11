# Phase 0 probe log -- rig-flex, SparkFlex

> **Corrected.** Every conclusion below about a SPARK Flex answering
> no parameter read is wrong, and the reason is worth keeping. The reads went out
> as zero-length data frames, or as remote frames with dlc 0, and firmware 26.1.6
> ignores both. Sent as remote frames with dlc 8 they all answer, on eight api
> classes covering parameters 0-255, on every controller. This file is left as
> the record of what was believed at the time. See
> `docs/runs/rig-flex-parameter-reads.md`.

## 0a backup
the session candump (6.7 MB, 65 s, both buses)
spark-passive.yaml, cancoder-check.txt, spark.yaml

## 0b read probe
- Firmware read WORKS: api 0x098, DLC-0 data frame AND RTR both reply.
  All 8 controllers: 1A 01 00 06 -> v26.1.6. Uniform.
  => pre-24.0.1 burn-flash bug is RULED OUT as the cause.
- Parameter access at api 0x300|param: NO REPLY for params 0-15,
  with DLC 0, DLC 1, DLC 2, DLC 4 and RTR.
- Full RTR sweep of all 1024 API values on dev 17, only 3 responders:
    api 0x098  firmware       1A01000600000000
    api 0x1F1  (follower?)    01
    api 0x1F3  (follower?)    <empty>
  => no parameter-read API is reachable over CAN without a heartbeat.

## 0c set-period probe (dev 17, frame 2, 20 ms)
- SparkMax base  0x02051891 -> 0x2E2 still silent
- SparkFlex base 0x0205B891 -> 0x2E2 still silent
=> set_periodic_frame_period is a NO-OP on SparkFlex 26.1.6 at either base.

TX control: the 0x098 firmware read got replies in the same session, so host TX
is reaching the wire. The null results above are real, not a gs_usb TX wedge.

## Working conclusion
On SparkFlex fw 26.1.6 no device-configuration API answers over CAN:
no parameter read, no set-period. Only firmware (0x098) and two follower
APIs (0x1F1, 0x1F3) respond. Consistent with REV 26.1.0 moving USB CAN
bridging to SLCan and requiring RHC2 -- config may now be USB-only.

UNTESTED hypothesis: config frames are ignored unless a valid enable heartbeat
is present (RHC and REVLib always send one). Testing needs the motors enabled.

## INCIDENT: dev 17 (steer/LB, serial 498B2579) went dark

After the full 1024-API RTR sweep on dev 17, that controller stopped
broadcasting entirely. Confirmed:
  - REV device ids on the bus now: 10,11,12,13,14,15,16  (17 absent)
  - not at factory-default id 0 either
  - clear-faults (api 0x6E) did not bring it back
  - the other 7 controllers are unaffected

Cause: the RTR sweep. RTR carries no data, but for zero-argument command APIs
the API *is* the command and the payload is irrelevant, so the sweep invoked
commands, not just reads. REV's software-download / bootloader APIs sit near
0x099, adjacent to the firmware api 0x098 that answered.

DO NOT repeat a blind API sweep against a REV controller.

Recovery, in order:
  1. Power-cycle the motor rail; re-run can_id_sweep.
  2. Read the controller's status LED - a bootloader/recovery blink pattern
     tells us it is alive but not running application firmware.
  3. If still dark: RHC2 over USB-C (RHC2 has a Linux build, Ubuntu 24.04+).
     Recovery Mode erases all settings including CAN ID, so it returns at
     id 0 and must be reassigned to 17.

Baseline for restoring it is in this directory: serial 498B2579, CAN id 17,
role steer/LB, firmware 26.1.6, status frames 1-6 disabled (matching 6 of the
other 7 - only dev 16 had them enabled).

## BREAKTHROUGH: REV publishes the full CAN spec
https://github.com/REVrobotics/REV-Specs  (copies saved in this directory)
  can-frames/spark-frames-2.1.0        every frame, exact bit positions
  parameters/SparkParameters-v0.1.2.md every parameter id, type, default

### Corrections to earlier conclusions in this log
1. Serial-gated CAN ID assignment DOES exist -- I was wrong to say otherwise.
   SET_CAN_ID  api 0x095  arbId 0x02052540  5 bytes  impl fw 1.5.0
     UNIQUE_ID uint32 (bits 0-31) + CAN_ID uint8 (bits 32-39)
   REV: "Allows changing the CAN ID when multiple devices on the bus currently
   have the same CAN ID."
   IDENTIFY_UNIQUE_SPARK api 0x076, 4-byte uniqueId, blinks ONE device even
   when CAN IDs collide. The 0x2F0 broadcast supplies the uniqueId.

2. Status-frame drift, restated correctly.
   STATUS_2..9 are enabledByDefault:false -- their absence is NORMAL.
   STATUS_1 is enabledByDefault:true @250ms -- absent on 7 devices, so those
   7 have NO fault reporting at all (faults live in STATUS_1, not STATUS_0).
   dev 16 measured 10/20/20/50/20/200/200 which matches Appendix A EXACTLY.
   => dev 16 is the only correctly-provisioned controller; 7 are at defaults.

3. set_periodic_frame_period() is a SparkMax-era frame; Flex ignores it.
   The correct mechanism is SET_STATUSES_ENABLED api 0x010 (arbId 0x02050400,
   4 bytes: MASK u16 + ENABLED_BITFIELD u16) plus the Status N Period params
   (ids 158..167) and Force Enable Status N (ids 186..193, 200, 225).

4. STATUS_0 (api 0x2E0) real layout -- the repo's decode is wrong:
     APPLIED_OUTPUT bits  0-15  int16  x 3.082369457075716e-05
     VOLTAGE        bits 16-27  uint12 x 0.0073260073260073 V
     CURRENT        bits 28-39  uint12 x 0.0366300366300366 A
     MOTOR_TEMP     bits 40-47  uint8  degC
     limit/inverted/heartbeat-lock flags bits 48-53
     SPARK_MODEL    bits 54-57  (1 = SPARK Flex)
   So b2 = voltage low byte and b5 = motor temperature. Our b6=0x40 decodes
   as SPARK_MODEL=1, no limits, not inverted, NOT heartbeat-locked.
   Faults/warnings are STATUS_1 (api 0x2E1) bytes 0/2/3/5.

### fw 26.1.6 read surface -- tested on healthy dev 16
  0x098 GET_FIRMWARE_VERSION    ANSWERS (RTR and DLC-0)
  0x0C0 GET_TEMPERATURES        no reply
  0x0C5 GET_MOTOR_INTERFACE     no reply
  0x0D0 GET_PARAMETER_0_15_TYPES no reply
  0x0F0 READ_PARAMETER_0_AND_1  no reply
=> config READ-BACK over CAN is not available on this firmware. Writes
   (PARAMETER_WRITE 0x0E0, SET_STATUSES_ENABLED 0x010, SET_CAN_ID 0x095,
   PERSIST_PARAMETERS 0x3FF) are untested.

### Cause of the dev 17 incident, now identified
The 1024-API RTR sweep sent frames at api 0x1F0/0x1F2 (start/stop follower
mode -- we saw their RESPONSE frames 0x1F1/0x1F3 come back) and at
api 0x1FF = ENTER_SWDL_CAN_BOOTLOADER (DLC 0). That command needs no payload,
so an RTR frame at that id invokes it. BOOTLOADER_0 (api 0x2C0) is not
broadcasting, so it is not sitting in the CAN bootloader -- it is halted.
Power-cycle the motor rail.

## RECOVERY ~13:0x -- device 17 IS BACK
Motor rail was power-cycled. On return the whole bus was SILENT (0/8) even
though a cansend completed, i.e. a node ACKed -> devices powered but not
broadcasting. One CLEAR_FAULTS frame (api 0x6E, DLC 0) per id 10-17 brought
ALL EIGHT up instantly, dev 17 included, at the right CAN id with serial
498B2579 unchanged. Firmware 26.1.6 on all 8, hw_rev 0.
=> a SPARK Flex halted by ENTER_SWDL_CAN_BOOTLOADER recovers fully from a
   power cycle. No USB, no RHC2, no Mode button needed.

## MAJOR CORRECTION: the status-frame divergence was RUNTIME, not persisted
Post-cycle every controller broadcasts exactly STATUS_0 + STATUS_1 + UNIQUE_ID,
which is precisely REV's enabledByDefault set (STATUS_0 true, STATUS_1 true,
STATUS_2..9 false). Device 16 no longer shows STATUS_2..6.

So the pre-restart picture -- 7 devices missing STATUS_1, dev 16 carrying
STATUS_2..6 -- was leftover RUNTIME state from whatever last ran on the bus,
NOT persisted config drift, and NOT evidence that 7 motors had lost config.
Earlier entries in this log claiming dev 16 was "the only correctly
provisioned controller" are WRONG and superseded by this.

## REAL config loss found: device 12 (drive/RF, serial 6B029ADD)
Measured on a freshly power-cycled bus, so this is PERSISTED state, not
leftover runtime state:

  dev 10,11,13,14,15,16,17   STATUS_1 @  20 ms   = Appendix A value
  dev 12                     STATUS_1 @ 250 ms   = REV FACTORY DEFAULT

Appendix A declares Status 1 Period (param id 159) = 20. REV's default is 250.
Seven controllers hold the provisioned value; device 12 has reverted to
factory default. 750 frames vs 60 frames in 15 s -- unambiguous, not noise.

This is the "motors lose their config" failure, caught on one identified unit.

Implication: Status 1 Period is a CANARY for factory-default reversion, and it
is observable passively with zero writes. If it reverted on dev 12, the other
deviating parameters likely reverted too -- Idle Mode 6 (BRAKE -> COAST),
Closed Loop Control Sensor 9 (MAIN_ENCODER -> NONE), P 0 id 13 (0.119 -> 0).
A drive motor coasting instead of braking is functionally significant.
Cannot be confirmed by read-back: fw 26.1.6 answers no parameter reads.

---

# - status frame period behaviour, measured on dev 10

Four facts about the Status Period parameters on fw 26.1.6 that REV's parameter
table does not carry. All four came out of the hardware injection suite
(tests/hardware) failing on its first run against this bus, and
each one is a measurement rather than an inference.

## 1. A period write does not cancel the interval already running

Writing Status 1 Period = 8000 on dev 10, with the frame at its provisioned
20 ms:

    last frame before the write returned   -0.002 s
    write 8000 -> Success                   0.000 s   (round trip 0.7 ms)
    next frame                             +0.018 s   <- one more at the OLD 20 ms
    next frame                             +8.018 s
    write 20 -> Success                    +12.000 s
    next frame                             +16.017 s  <- the armed 8000 still ran
    next frame                             +16.037 s  <- 20 ms from here on

So a period write governs from the frame after the one already scheduled, and a
second write does not reschedule what the first armed. Writing 20000 and then
writing 20 back two seconds later left the wire silent for another 18 seconds,
with both writes answered Success.

CONSEQUENCE FOR THE TOOLING. `cmd_repair` writes the period and then measures it
to decide whether the repair worked. The measurement has to outlast the period
being replaced or it reads the old cadence, and D3's fix -- compare the measured
period against the value asked for -- will report a failure that is not one
unless it waits. Repairing 250 -> 20 needs at least 250 ms of patience; the
reverse needs 20 ms.

## 2. The accepted range is narrower than uint32 and differs per frame

    Status 0 Period (158)   accepts 1.. 1000      0 and 1001 refused as Invalid
    Status 1 Period (159)   accepts 1.. 32767     0 and 65535 refused as Invalid

Values were bisected on hardware; 1001, 1024, 1500, 2000, 3000, 4000, 4095 and
4096 are all refused for 158. A period of 0 does NOT disable a frame on this
firmware, which rules out the obvious way to take one off the air. The only
frame-level control is SET_STATUSES_ENABLED (api 0x010), still untested here.

## 3. A refused write echoes the value the device is holding

    159 set to 1000, then 159 <- 65535   -> Invalid, echoed 1000
    158 set to  500, then 158 <-  2000   -> Invalid, echoed  500

The live value was deliberately moved off the default first, so the echo is the
device's current value rather than a default or the value requested. A refused
write also changes nothing: the cadence measured the same before and after.

CONSEQUENCE, AND IT IS THE LARGEST ONE HERE. This log records that config
read-back is not available on 26.1.6, and `coverage_note()` tells operators the
same. That is true of every READ frame (0x0C0, 0x0D0, 0x0F0+, all silent). It is
not true of the parameter write path: an out-of-range write to a parameter the
firmware range-checks returns that parameter's current value in bytes 2:6 of
PARAMETER_WRITE_RESPONSE. For those parameters the audit can stop inferring
config from broadcast behaviour and read it.

UNTESTED, and worth doing before anything is built on it: whether the other nine
settings in UNVERIFIABLE_DEVIATIONS range-check their input. Idle Mode and
Closed Loop Control Sensor are enums and probably do; P 0 and the encoder counts
are less likely. Each needs one refusable value found by hand.

## 4. status_period_ms() counts frames that arrived before its window

Not a firmware behaviour -- a driver one, found the same afternoon.

    dev 10 starved to 8000 ms, then measured twice with nothing changing:
      status_period_ms(seconds=2.0)                 -> 2000.0 ms
      status_period_ms(seconds=2.0) again           -> None
      _drain() then status_period_ms(seconds=2.0)   -> None

The first call inherits whatever is sitting in the receive socket and counts it
inside the window; the second sees the truth because the first drained it.
`write_param` and `persist` both call `_drain()` before they send.
`status_period_ms`, `inventory`, `duplicates` and `collect_status` do not.

On a controller at 20 ms one stale frame is half a percent of the reading. On
one that has stopped it is the whole reading, and it turns "this frame has
stopped" into "this frame is slow" -- the same confusion D4 is about, reached by
a different route, and reached most easily right after a write, which is exactly
where `cmd_repair` measures.

Pinned by tests/hardware/test_config_injection.py::
test_a_measurement_window_counts_only_the_frames_that_arrived_inside_it

## 5. A saturated bus makes healthy controllers disappear, and nothing records it

Measured on can0 at 1 Mbit, filling the bus from this host
with frames carrying manufacturer 0xFF, which no device here decodes. Five
seconds each way, back to back:

    filler at LOW priority   (arb 0x1FFFFFFF, sorts above every SPARK frame)
      24751 frames sent      STATUS_1 read 20.0 ms      8 of 8 inventoried
    filler at HIGH priority  (arb 0x01FF0000, sorts below them)
      29702 frames sent      STATUS_1 read  750 ms      0 of 8 inventoried

      rx_packets +29594   rx_dropped +0   rx_over_errors +0   rx_errors +0
      adapter state ERROR-ACTIVE throughout, both ways

Three things follow.

The loss is arbitration, not the receive path. Nothing is dropped and nothing
errors, because losing arbitration is ordinary CAN behaviour rather than a
fault. So there is no counter on the host or on the controllers that a driver
could read to tell a saturated bus from a dead one.

`spark audit` on a bus in this state reports eight controllers "not
broadcasting". Every one of them is healthy and the fault is a single
misbehaving talker. That is the failure the adversarial suite pins as
test_a_silent_bus_is_one_finding_and_not_one_per_configured_id, reproduced on
hardware.

The reading is not merely inflated, it is unstable across the whole verdict
space. One window measured 750 ms; another run of the same injection measured no
STATUS_1 at all in three seconds. So under load `status_1_verdict` can return
"ok", "reverted", "unexpected" or "absent" for one controller whose flash was
never touched, and D4 is a continuum rather than four discrete confusions.

## 6. What congestion does NOT do, and one thing still unexplained

The first draft of section 5 said congestion latches no sticky CAN fault. That
was inferred from a test skipping rather than measured, so it was checked
directly. Clear faults on all eight, confirm clean, then inject:

    39567 high-priority filler frames over 6 s   -> sticky faults: none
     1186 frames colliding on id 10's own
           STATUS_0 arbitration id over 12 s     -> sticky faults: none
           interface rx_errors delta 0, all eight still broadcasting

So neither bus saturation nor a same-address transmitter raises a CAN fault on
this firmware over those durations. Catalogue C6 attributes CAN TX/RX sticky
faults to bus contention; that is not reproduced here at this scale.

RESOLVED, later the same day, by raising the collision rate. See section 12: it
is the collision that does it, not the load, and the earlier probe was simply
too short and too sparse.

## 7. The post-cycle return is always silent (corrected twice)

This section has been wrong twice and both errors had the same cause: a
conclusion drawn from a run whose own instrumentation was transmitting.

FIRST VERSION said the evidence can never be read on rig-flex, from one cycle that
stayed silent for 180 s. SECOND VERSION said the silent return is intermittent,
because the next cycle "came back broadcasting on its own" after 0.6 s. It did
not. The staged fixture at that time probed GET_FIRMWARE on the first poll after
declaring the bus quiet, and its own output says so -- "the controllers answer
but broadcast nothing" only prints when that probe was answered. Frames resumed
0.103 s after it. The instrument sent the wake frame.

WHAT THE FOUR OBSERVATIONS ACTUALLY SHOW, once the instrument is accounted for:

    cycle   host frames during the silence          outcome
    A       none, the probe was not written yet     silent for the full 180 s
    B       GET_FIRMWARE, on the first poll         woke 0.1 s later
    C       GET_FIRMWARE x8, after three            woke
            listen-only tests had failed
    today   none for 60 s, then one GET_FIRMWARE    silent 60 s, then woke

There is no intermittency. Every post-cycle return observed on this fleet has
been silent, and every apparent recovery followed a frame this host sent. See
section 11 for the controlled run.

THE LESSON, since it cost two wrong entries: an instrument that transmits cannot
measure whether a bus recovers untouched. The staged fixture now watches for 15 s
before sending anything and records how far into the silence broadcasting
resumed and whether a probe preceded it.

## 8. D2 did not reproduce: an unpaced PERSIST committed the value that was written

Staged on dev 10, three flash cycles.

    seed    write 159 = 250, wait 1 s, PERSIST -> Success, wait 2.5 s
    arm     write 159 = 20, then PERSIST about a millisecond later, no settling
            PERSIST -> Success
    cut and restore the motor rail
    verify  dev 10 came back at 20 ms

So the burn caught the RAM commit. CD 432129 reports the opposite on 2021-era
SPARK MAX firmware, and the handoff calls D2 the one defect whose failure looks
exactly like success and is permanent. On SparkFlex 26.1.6 it did not happen.

READ THIS NARROWLY. One controller, one parameter, one trial, and the failure it
is looking for is a race, so this is evidence and not proof. Nothing about the
driver changed: `persist()` still sends the instant it is called and `cmd_repair`
still calls it directly after a write. What moved is the expected consequence.

The test now carries a plain assertion rather than an xfail, so a controller that
ever comes back holding the value the write replaced turns it red.

## 9. The controllers answer GET_FIRMWARE while broadcasting nothing

The verify run above started while the bus was still in its post-cycle silence,
and the ordering is informative:

    test 1  inventory over 4 s            0 of 8 broadcasting     FAILED
    test 2  passive listen over 4 s       no REV frames at all    FAILED
    test 3  collect_status over 3 s       no status frames        FAILED
    test 5  GET_FIRMWARE to all eight     ALL EIGHT ANSWERED      passed
    test 6  firmware against baseline     all eight matched       passed
 ...
    later   Status 0 and Status 1 periods measured 10 and 20 ms   passed

So the silence is not a dead bus and not a rail that is still off. Every
controller is powered, on the bus, and answering requests, while transmitting no
periodic frame at all. That settles the question section 7 left open about which
kind of silence this is.

WHAT IS NOT SETTLED is what ended it. The bus was broadcasting again later in the
same run, and the only host traffic in between was those firmware queries. Either
it recovers on its own after some tens of seconds, or a request wakes the
transmitter. If a request wakes it, the deadlock in section 7 is broken: query
firmware, read STATUS_1 with the sticky bits still set, and only then clear.

The staged fixture now runs that as an experiment. It watches the silence
untouched for 15 s, sends one round of GET_FIRMWARE only if the bus is still
quiet, and records how far into the silence broadcasting resumed and whether the
probe preceded it. The next silent return answers it.

## 10. A second test that passed over an empty list

`test_no_controller_on_this_bus_is_in_follower_mode` filtered on "controllers
that are broadcasting" and then asserted none of them was a follower. On the
silent bus above there were none of them, so it passed while reading nothing.
That is the second instance of the same shape in this suite -- the first was the
hasReset assertion in the staged verify -- and both are now required to find at
least one readable controller before they assert anything.

Both were written the same way and both were caught by the same event, which is
an argument for running a new hardware suite against a bus in a degraded state
deliberately, rather than only against a healthy one.

## 11. The silent bus is woken by one read frame, and the evidence survives it

RESOLVED, with tools/spark_silent_bus_probe.py. This supersedes the
open question in sections 7 and 9 and changes the recovery procedure this repo
has carried.

Rail cut for several seconds and restored. Then, with the host transmitting
absolutely nothing:

    60 s of silence, no frame sent by anyone   -> 0 of 8 broadcasting
    one GET_FIRMWARE addressed to id 17 only   -> 8 of 8 broadcasting
    sticky read straight afterwards            -> hasReset on all eight

Three things follow, and the third is the one that matters.

The bus does NOT recover on its own. A full minute of quiet changed nothing, so
waiting is not a strategy and the silence is a stable state rather than a
settling period.

The wake is bus-wide from a frame addressed to one device. Only id 17 was
queried; all eight resumed. So this is not a per-controller command being
processed, it is every controller reacting to the bus being used at all.

CLEAR FAULTS IS NOT REQUIRED. A read wakes them, a read erases nothing, and the
sticky hasReset that records the reboot is still there afterwards. The
"read faults BEFORE you clear" rule from rig-flex-2's protocol is no longer a race
that is impossible to win on this fleet -- the correct procedure is:

    query firmware on any one controller     (wakes the bus, destroys nothing)
    uv run spark faults                      (hasReset and brownout, intact)
    uv run spark status
    uv run spark clear                       (only if something still needs it)

Every post-cycle fault reading taken on rig-flex before today was taken after a
Clear Faults frame and is worth nothing. From here they can be taken properly.

WHAT IS STILL OPEN is the mechanism. The escalation was written with GET_FIRMWARE
as its first active step, so it cannot say whether any traffic would have done
it or whether a SPARK has to recognise the frame. The probe now sends one frame
with an unclaimed manufacturer first, which nothing on this bus decodes, and the
next silent return answers it.

IMPLICATION FOR THE TOOLING. `spark status` prints MISSING and `spark faults`
prints "no controllers broadcasting -- run `spark clear`". That advice destroys
the evidence for no reason. Both should query firmware first and re-read: the
audit already has `firmware()` and already calls it, and on a silent bus it is
the difference between a diagnosis and a guess.

## 12. What raises a CAN sticky fault, and it is not load

Trigger hunt with tools/spark_sticky_survival.py, all eight cleared to a known
state first:

    high-priority filler, 30 s continuous   197303 frames   sticky: clean
    STATUS_0 collision on id 17, 30 s @5ms    5870 frames   sticky: `can` on ALL EIGHT
    link stayed ERROR-ACTIVE throughout both

Saturation does not raise it at any scale this adapter can produce. Transmitting
on a controller's OWN STATUS_0 arbitration id does, and it raises the bit on
every controller on the bus rather than only the one being collided with -- an
error frame is seen by every node, so every node counts it.

There is a threshold. An earlier probe at 10 ms spacing for 12 s, 1186 frames,
raised nothing; 5 ms for 30 s, 5870 frames, raises it on all eight. Somewhere
between those the contention becomes frequent enough to matter.

This corrects catalogue C6, which attributes CAN TX/RX sticky faults to "bus
contention" generally. On this firmware the distinction is sharp: contention for
the bus is harmless, contention for an ADDRESS is what latches. Which makes the
bit a duplicate-id signature rather than a load signature -- catalogue B1 -- and
means an operator seeing sticky `can` on the whole fleet should look for two
devices on one id before suspecting wiring.

It also explains the sticky bits left by the hardware injection suite: the
collide-gated tests stream on a real controller's address for 20 s, which is
over the threshold.

## 13. The `can` sticky fault does NOT survive a power cycle (one bit, not all)

The claim under test, from can_bus.py, and the justification for clearing
sticky faults on every SparkFlex the drive stack initialises:

    sticky faults (SparkFlex latches them across power cycles, so a
    browned-out controller comes back faulted and silently won't drive)

It is false for SparkFlex on this firmware.

    marker set on all eight        sticky_faults = ['can'], no active fault
    motor rail cut and restored
    bus woken with GET_FIRMWARE    (a read; it erases nothing)
    after                          hasReset on all eight, sticky faults CLEAN

Every controller shows hasReset, so all eight really rebooted, and the `can` bit
that was set before the cut is gone. The power cycle cleared it.

PRECISE SCOPE, and it is narrower than the heading of the first draft claimed.

ONE BIT. The marker was `can`, sticky fault bit 3, because it is the only fault
bit that can be raised from the host. The other seven -- other, motorType,
sensor, temperature, gateDriver, escEeprom, firmware -- need a physical
condition or a write this suite refuses. Whether they behave the same way is an
extrapolation, not a measurement, and bits backed differently in firmware would
not be a surprise.

STICKY WARNINGS ARE NOT TESTED AT ALL. hasReset is set by the cycle itself, so
it cannot answer the question, and brownout, overcurrent and stall all need a
condition this suite will not create.

AND THE MOST LIKELY WAY BOTH STORIES ARE TRUE. A controller with a persistent
physical fault -- a dead gate driver, an unplugged encoder -- comes back from a
reboot faulted, because the CONDITION is still there and re-asserts on boot.
From the outside that is indistinguishable from a bit that survived, and it is
exactly the behaviour can_bus.py's comment describes ("comes back faulted and
silently won't drive"). This experiment rules out survival for one bit whose
cause was gone by the time the rail was cut. It does not rule out a fault that
re-asserts, and that is probably what anyone remembers seeing.

WHAT WOULD SETTLE IT: a second marker with a removable physical cause. Unplug a
motor data cable, confirm the sticky sensor bit, plug it back in so the ACTIVE
fault clears and only the sticky one remains, then cycle the rail. That
distinguishes "the bit survived" from "the condition re-asserted" for a bit that
is not `can`, and it is the one physical action still available on this robot.

CONSEQUENCES.

The boot-time clear in apply_boot_config rests on a premise that does not hold.
A power cycle already clears the sticky faults, so there is nothing latched for
it to release, and an ACTIVE fault whose cause persists re-asserts whether or not
anything cleared it.

What the boot-time clear is actually doing is waking the bus. On this fleet the
controllers come back from a rail cycle broadcasting nothing (section 7), and
Clear Faults is one of the frames that ends that. So it works, for a reason its
comment does not give -- and section 11 shows a plain GET_FIRMWARE does the same
job while leaving the record intact. Replacing the boot-time clear with a read
would keep the wake and stop destroying every brownout and reset record.

It also dates a fault, which is useful: any sticky fault visible on this fleet
was raised since the last power-up, because the power-up cleared the byte.

And it weakens one line in rig-flex-2's FINDINGS.md further. That document notes the
all-clean fault reading only means "no event since the last clear". It now means
"no event since the last clear OR the last power cycle", which on a robot being
power-cycled through a drain test is a much shorter window than it sounds.

## 14. D1 demonstrated on a genuinely faulted fleet, with nothing simulated

Every earlier demonstration of D1 used an injected STATUS_1 frame: honest, but
the controllers themselves were never faulted. Section 12's trigger removes that
caveat, so the whole thing can be done for real.

    clear all eight, confirm every sticky byte clean
    stream STATUS_0 on id 10's own address, 5 ms, 30 s
    all eight latch sticky `can`
    then, on that same bus, in the same second:

        uv run spark faults   ->  exit 1, "sticky-FAULT: can" on all eight
        uv run spark audit    ->  exit 0

`spark audit --help` promises "exit 1 on any fault". Eight controllers are
carrying a fault, `spark faults` reads it out of STATUS_1 and reports it, and the
audit exits clean because it scores inventory and cadence and never opens
STATUS_1 at all.

Two commands, one bus, one second apart, opposite verdicts. That is D1 with no
simulator anywhere in the loop, and it is the argument for fixing it first.

Pinned by tests/hardware/test_wire_injection.py::
test_spark_audit_and_spark_faults_agree_about_a_fault_the_fleet_really_has

## 15. Three wheels are blocked by the data-port interlock, not one

Captured with tools/spark_teleop_watch.py, passively, while the
dashboard drove the base. The watcher decodes the setpoint frames the host sends,
the applied output the controllers report, and the enable heartbeat mask, and
puts them on one line -- which is the only way to tell "the host never asked"
from "the controller refused".

    id role       enab  cmd frames  max cmd  max applied  max amps  limits
    10 drive/RB    yes        1579    0.100        0.100     86.37  -
    11 steer/RB    yes        1579    0.400        0.400     99.71  -
    12 drive/RF    yes        1579    0.100        0.100     80.48  -
    13 steer/RF    yes        1579    0.400        0.000      0.00  FWD,REV
    14 drive/LF    yes        1579    0.100        0.100     90.29  -
    15 steer/LF    yes        1579    0.400        0.000      0.00  FWD,REV
    16 drive/LB    yes        1579    0.100        0.000      0.00  FWD,REV
    17 steer/LB    yes        1579    0.400        0.400    128.39  -

Eight for eight. Every controller reporting HARD_FORWARD_LIMIT_REACHED and
HARD_REVERSE_LIMIT_REACHED applied nothing and drew nothing; every controller
without them applied exactly what it was commanded.

The host is not at fault. All eight received 1579 setpoint frames and all eight
are in the enable mask. The three are refusing, and REV's own wording for the bit
is "whether the physical limit switch has been reached".

WHAT IS NOT THE CAUSE, each ruled out by measurement:

  polarity      params 50 and 51 read false, which is REV's factory default
  enable        params 52 and 53 are true, factory default and the declared value
  the host      identical setpoint frames to all eight, all eight enabled
  CANcoders     all four broadcast at about 100 Hz on the FD bus. An earlier
                entry claiming that bus was silent was wrong: the socket had
                been opened without fd=True and received nothing on an mtu 72
                interface

BOTH DIRECTIONS AT ONCE is the discriminator. A mechanism cannot be at its
forward and reverse end stops simultaneously, so the two limit inputs are being
read as asserted together rather than a switch doing its job.

THE FIRST READING OF THAT WAS WRONG, and it is recorded here because it cost an
hour. It was read as CD 455481, a misaligned data-port breakout board holding a
limit closed, with E2 and E6 beside it, and the recommendation was to reseat the
connectors. Reseating changed nothing, which should have moved the diagnosis on
sooner than it did. The cause was a parameter value. See section 17.

FOOTNOTE ON CURRENT. Peak current reached 128 A on id 17 and 80 to 99 A on the
others during steering. The declared Smart Current Stall Limit is 80 A and
Current Chop is 115 A, so those peaks sit at or above both. They are single
samples from a 10 ms frame and may be inrush against a stationary wheel, but
they are worth a second look and this suite has never measured the fleet under
load before.

## 16. D1 earned its keep the hour it landed

`spark audit` reported these three interlocks. Before the fix landed this
afternoon it scored inventory and cadence only and exited 0 on this bus, and the
same information sat in `spark faults` -- which prints it in a table and does not
count it toward its exit code either. Three blocked wheels were visible to
nobody, on a robot in daily use.

## 17. Cause confirmed on demand: Limit Switch Polarity = True blocks the wheel

Params 50 and 51 set to True make both hard limits read as REACHED on rig-flex's
wiring, which pins applied output to 0. REV's factory default for both is False.

Written as a reproduce-and-repair loop rather than argued, with
tools/spark_limit_polarity_repair.py --inject 16 --cycles 3:

    cycle 1  polarity -> 1   limits FWD,REV     polarity -> 0   limits clear
    cycle 2  polarity -> 1   limits FWD,REV     polarity -> 0   limits clear
    cycle 3  polarity -> 1   limits FWD,REV     polarity -> 0   limits clear

Three for three, both directions, on demand, on a controller that had been
repaired and was healthy at the start. Before this run the evidence was one
directional: writing False cleared a fault that was already present, which is
equally consistent with the write merely disturbing something. It is not.

SO NOTHING PHYSICAL WAS EVER WRONG. The data ports, the breakout boards and the
cables are fine, and the reseating in section 15 was chasing a theory that had
already been falsified by the reseat itself.

REPAIR, applied and persisted on 13, 15 and 16 the same day, after which all
eight were commanded and applied for the first time:

    id 10 drive/RB  0.100 -> 0.100    59.8 A
    id 11 steer/RB  0.400 -> 0.400   100.1 A
    id 12 drive/RF  0.100 -> 0.100    61.4 A
    id 13 steer/RF  0.400 -> 0.400   149.9 A
    id 14 drive/LF  0.100 -> 0.100    54.1 A
    id 15 steer/LF  0.400 -> 0.400   149.1 A
    id 16 drive/LB  0.100 -> 0.100    83.3 A
    id 17 steer/LB  0.400 -> 0.400   106.7 A

WHAT IS STILL OPEN is what wrote True. It is not a factory or safe reset: those
restore defaults, and the default is False, so a reset would have fixed this
rather than caused it. It is not this repo -- nothing here writes parameters
50-53 and five tests enforce that, and a grep across all five workspace repos
finds only a test asserting the refusal. RHC2 was not opened on the occasion
that produced it.

So something writes True to two guarded parameters on three of eight
controllers, survives being corrected and persisted, and is not accounted for.
tools/spark_param_write_log.py listens passively for exactly that, logging every
parameter write and every hard-limit transition with the kernel's timestamp.

TWO THINGS TO WATCH, both new since the wheels started turning:

  steer current. steer/RF and steer/LF pulled 149.9 A and 149.1 A. The STATUS_0
  current field is 12 bits at 0.0366 A per count and saturates at 150.0, so both
  were at the top of the measurable range. Declared Smart Current Stall Limit is
  80 A and Current Chop is 115 A. The two motors that spent the longest blocked
  now draw the most.

  the power cycle. The repair is persisted but has not yet survived a rail
  cycle. If the limits return after one, that is catalogue A3 -- settings
  resetting after a persist on firmware v26 -- and a different problem again.

## 18. Steer current is peaks, not dwell, and the ramp does not move it

Four teleop runs with `tools/spark_teleop_watch.py`, run lengths derived from the
STATUS_0 sample count at the declared 10 ms period.

    run                    length   steer peak A      drive peak A
    1 gentle, pre-fix       28.7 s   58.3 - 74.6       20.8 - 23.4
    2 aggressive, pre-fix    9.1 s  124.7 - 149.1      76.4 - 105.8
    3 aggressive, mixed     21.7 s  138.4 - 150.0      89.0 - 99.9
    4 smooth, post-fix      29.6 s   60.1 - 79.4       23.0 - 37.0
    5 aggressive, post-fix  17.7 s  148.7 - 150.0      83.0 - 97.2

Neither gentle run produced a single sample above the 80 A Smart Current Stall
Limit, so no dwell table was printed for them.

Mean steer dwell as a fraction of the run:

    threshold      run 2 (pre)   run 5 (post)
    > 80 A            1.19 %        1.00 %
    > 115 A           0.469 %       0.396 %
    > 145 A           0.166 %       0.155 %
    drives > 80 A     0.248 %       0.226 %

THE ANSWER TO THE OPEN QUESTION. Dwell is tens of milliseconds, not seconds. The
worst controller spent 0.21 s above the stall limit across a 17.7 s run at full
stick deflection, and section 15 recorded temperatures at 30 to 33 C throughout.
The sticky overcurrent bits latch truthfully on peaks that carry no thermal
weight. Do not lower `steer_max_output` on this evidence.

THE RAMP FIX IS A NULL RESULT, and it is recorded as one. `steer_max_doutput` was
0.75 against a `steer_max_output` of 0.40, so the clamp could only ever see a step
of 0.80 and bound in a 0.05-wide sliver; a re-steer from rest passed untouched.
The budget was also per tick, and the drive loop ticks at `ctrl_loop_hz` 20 while
teleop measured 62 to 70 Hz, so one value meant a ramp three times shorter in
teleop. Both are now fixed: `steer_slew_duty_per_s` is a rate, multiplied by the
real tick. Every bucket in run 5 still lands within 15 percent of run 2.

The ramp shapes the first 80 ms of a re-steer, and a 90 degree module
reorientation takes far longer than that, so duty sits pegged at 0.40 for most of
the maneuver and the current is set by that sustained phase. Run 3 appeared to
halve the dwell and was discarded: it overlapped an edit window in which the
config had the new key while the code still read the old one, which disables the
ramp entirely.

WHAT IS STILL OPEN. All four steers in run 5 read 148.7 to 149.9 A. The field is
12 bits at 0.0366 A per count and saturates at 149.99, so all four are pegged and
their true peaks are unknown. Declared Current Chop is 115 A and Chop Cycles is 0,
which `REV-SparkParameters-v0.1.2.md` defines as the number of cycles before
chopping is triggered, so chopping should fire immediately. Whether those limits
are actually in force on these controllers is answerable by the refused-write
read-back in section 12, on params 11 and 59, and has not been done.

## What cites this log, and where it overstates it

`CAPTURED_BASE03_IDLE_STATUS_0` in `tests/support/sparksim/frames.py` is
assembled, and no controller sent those eight bytes. The candump
carries `0205B80F#00000C07001B4000` on id 15 for 958 frames, at zero current and
27 C. Id 12 sent `0205B80C#00000C07011F4000` once, holding the same 1804 voltage
field at 31 C with current byte 0x01. So the voltage, temperature and model
fields are measured on this fleet, while the exact payload combines two
controllers. The four docstrings that called it a capture now say so.

The STATUS_0 layout claim in `provenance.py` stays hardware-settled. The
layout is what this log measured, and only the byte combination is derived.
