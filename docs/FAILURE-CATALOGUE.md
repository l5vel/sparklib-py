# SPARK MAX / Flex failure catalogue

Compiled from Chief Delphi via the Discourse JSON API
(`chiefdelphi.com/search.json?q=`, `/t/<id>.json` -- the HTML is blocked, the API
is not). 39 search queries -> 704 unique topics -> 353 scored as SPARK/CAN
relevant -> 55 threads fetched in full. Raw threads are held in the Chief
Delphi corpus (chiefdelphi.com), with its digest in `cd_digest.txt`.

Column "sim" = can this be reproduced against a python-can driver in a test?
  YES   = frame-level simulation is faithful
  PART  = the driver-visible half can be simulated; the physical cause cannot
  NO    = hardware-only, no software recovery path to test

---


## Firmware history: where it actually lives, and what it retires

Corrected. An earlier version of this section said REV publish no
SPARK firmware changelog. **That was wrong.** It is not on docs.revrobotics.com
-- the sitemap there lists firmware changelogs for the PDH, Pneumatic Hub,
MAXSpline encoder, Duo Control, RHC, RHC2 and Servo Hub, and none for the SPARK,
and two guessed doc URLs 404. It is published as **GitHub releases**:

    https://github.com/REVrobotics/REV-Software-Binaries/releases

**The tag scheme CHANGES mid-history, and searching the old prefix alone misses
every recent release:**

    SPARK MAX    sparkmax-1.6.1.. sparkmax-25.0.2, then sm-25.0.3 onward
    SPARK Flex   sparkflex-23.0.2.. sparkflex-25.0.2, then sf-26.1.6

Releases seen (the repo also carries sh- Servo Hub, spline- MAXSpline,
rhc-, a301- tags, so filter):

    SPARK MAX   1.6.1  1.6.2  1.6.3  24.0.0  24.0.1
                25.0.0  25.0.1  25.0.2  25.0.3  25.0.4
                26.1.0  26.1.1  26.1.2  26.1.3  26.1.4  26.1.5
    SPARK Flex  23.0.2  24.0.0  24.0.2  24.0.4  24.0.5  24.0.6  24.0.7  24.0.8
                25.0.0  25.0.2  26.1.6

rig-flex runs SPARK Flex 26.1.6, which is the newest Flex release. Note the Flex
has no 26.1.0-26.1.5: those version numbers exist for the MAX only.

`sources/github/REVrobotics__REV-Software-Binaries.json` in this corpus holds
that repo's ISSUES, not its releases. The harvest looked complete and was not.

### What the release notes settle

**Frames 2-9 are not broadcast by default on 25+.** sparkflex-25.0.0 and
sparkmax-25.0.0, verbatim:

> Only sends periodic CAN frames if they are needed (except for frames 0 and 1,
> which are enabled by default)

So a 25+ controller silent on Status 2-9 is behaving as designed. rig-flex does
exactly this and is healthy. An audit rule built on the opposite reading was
written and then reverted. Hardware appeared to confirm it, and only the
release notes refuted it.

**0x060 was removed in firmware 26.1.4**, sparkmax-26.1.4:

> Removes Legacy Status Frame 0, which is unused by REV Hardware Client 2 and
> newer releases of REV Hardware Client 1

That is why rig-flex on 26.1.6 emits no 0x060 at all. The all-ones beacon exists
only on firmware 25.0.0 through 26.1.3, which is a narrow window, and the guard
against scoring it is still correct for anything in that band.

**SPARK_MODEL in STATUS_0 arrived in 26.1.0**, sparkmax-26.1.0: "Adds the
specific SPARK Model to status 0" and "Changes model value to 2 for MAX in
Status 0 frame". Model detection from the frame works on 26.1.0+ only.

**Modes with a firmware fix**, so not live on 26.1.6:

- high CAN utilisation with 24.0.x devices present -- fixed sparkmax-25.0.1
- Flex CAN timeouts -- sparkflex-25.0.2 (2025-01-21), verbatim: "Improves
  CAN handling to fix CAN timeouts"
- follower mode spinning after a stop command -- both, 25.0.2
- status 8 initialisation -- sparkmax-26.1.2
- enabling an already-enabled periodic frame resetting its send time -- 26.1.3

Everything else in this catalogue has no firmware fix on record, so it stays a
readiness rehearsal: the point is that the code already handles the failure on
the day it arrives.

---

## A. Configuration is written but does not stick

### A1. Config write not acknowledged; settings silently revert to defaults  [sim YES]
- **Symptom** motors accept commands and output zero; mechanism does not move.
- **Bus** the write is sent, no `kOk`/result frame returns. Device keeps its old
  value. Nothing errors.
- **Cause** config routine sent settings back-to-back (slot 0 then slot 1) faster
  than the device handled; some controllers had lost `kFF` and re-defaulted to 0.
- **Recovery** *"you need to have retries in your code for basically every
  setting, and also read the setting back to ensure that it's been set
  correctly."*
- CD 456184 <https://www.chiefdelphi.com/t/456184>

### A2. Burn/persist issued too soon after config writes  [sim YES]
- **Symptom** settings do not survive a power cycle even though the burn
  reported success.
- **Cause** *"burning configuration to flash was an issue if it was not delayed
  long enough after sending configuration messages."*
- **Recovery** delay between the last parameter write and persist.
- CD 432129 <https://www.chiefdelphi.com/t/432129>

### A3. Settings reset *after* persisting  [sim PART]
- **Symptom** absolute-encoder and position-PID settings reset after a persist,
  on firmware v26. Two controllers affected.
- **Status** unresolved in-thread; possibly a v26 firmware regression.
- CD 515478 <https://www.chiefdelphi.com/t/515478>

### A4. Flash wear from persisting every boot  [sim NO]
- Flash endurance is "ten to hundreds of thousands" of cycles. Calling burn on
  every boot (as some REV examples did) consumes it.
- **Recovery** persist only at provisioning, not per boot.
- CD 455171 <https://www.chiefdelphi.com/t/455171>

### A5. CAN configuration forgotten on power cycle  [sim PART]
- **Symptom** Flex loses CAN ID/config across power cycles, latest firmware.
- **Workaround reported** hold the roboRIO reset from power-on until the ID is
  set and the config burned. Some units were RMA'd as defective.
- CD 455171 <https://www.chiefdelphi.com/t/455171>, CD 391976
  <https://www.chiefdelphi.com/t/391976>

### A6. Factory reset / recovery mode does not clear the CAN ID  [sim NO]
- **Symptom** *"I tried the 'complete factory reset' and the 'recovery mode', but
  the device keeps loading its old CAN id and still won't perform."*
- **Recovery** full DFU reflash.
- CD 507673 <https://www.chiefdelphi.com/t/507673>

---

## B. Identity and addressing

### B1. Duplicate CAN IDs  [sim YES]
- **Symptom** only ONE device appears in the client; others show "no signal" LED
  codes. Teams commonly discover it only after a controller misbehaves.
- **Bus** both devices answer on the same address; an ID scan sees one device.
- **Recovery** connect one at a time over USB and assign unique IDs. (On modern
  firmware `SET_CAN_ID` addressed by hardware serial fixes it in place.)
- CD 427780 <https://www.chiefdelphi.com/t/427780>, CD 426287
  <https://www.chiefdelphi.com/t/426287>

### B2. CAN ID 0 is "unconfigured" and must never be used  [sim YES]
- Firmware 1.5.2+ treats 0 as unconfigured; assigning it produces
  `Unable to retrieve SPARK MAX firmware version for CAN ID: 0`.
- CD 376796 <https://www.chiefdelphi.com/t/376796>

### B3. Follower with a lower CAN ID than its leader  [sim YES]
- **Symptom** ~4 stutters per second until power-cycled.
- **Cause** firmware ordering bug (since fixed). Also traced in-thread to a flaky
  power connection on one controller -- two different root causes, same symptom.
- CD 378716 <https://www.chiefdelphi.com/t/378716>

### B4. Follower mode inverted/polarity changed across a firmware update  [sim PART]
- CD 363633 <https://www.chiefdelphi.com/t/363633>

---

## C. Bus health and traffic

### C1. CAN utilisation spiking to 100%  [sim YES]
- **Symptom** `Timeout Waiting for Status X`, wildly fluctuating utilisation even
  while disabled.
- **Cause** in this case a third-party logging library, not REV. Fixed by
  updating it.
- **Test implication** absent status frames from congestion look identical to
  status frames disabled by config -- opposite remedies.
- CD 455329 <https://www.chiefdelphi.com/t/455329>

### C2. Controller intermittently drops off the bus  [sim YES]
- **Symptom** `REV Spark Flex CAN Timeout. Periodic Status 2`; CAN utilisation
  dips line up exactly with the timeouts, i.e. the device really left the bus.
- **Partial workaround** lower current limits; reflashing helped temporarily.
- CD 480555 <https://www.chiefdelphi.com/t/480555>

### C3. Periodic Status N timeouts on deliberately disabled frames  [sim YES]
- Disabling a status frame then reading it produces timeouts.
- CD 432129 <https://www.chiefdelphi.com/t/432129>

### C4. CAN H/L swapped  [sim NO]
- **Symptom** `CANSparkMax object created for CAN ID 3, which is not a SPARK MAX`
 -- *"each time it was due to a CAN connection being backwards (H-L, L-H)."*
- CD 460286 <https://www.chiefdelphi.com/t/460286>

### C5. WAGO / crimp connectors causing intermittent CAN  [sim NO]
- Replacing WAGOs with PWM-style connectors resolved recurring CAN errors;
  insufficient conductor in the WAGO is the failure.
- CD 480394 <https://www.chiefdelphi.com/t/480394> is the WAGO thread, and its
  resolution is PWM-style connectors in place of the WAGOs.
- The shrink-wrap find belongs to CD 460286 <https://www.chiefdelphi.com/t/460286>
  alone, where a solder joint under heat shrink shorted CANH to CANL: "Under
  shrink wrap. Non isolated solder. Hard to find. Yellow was to Yellow and Green
  was to Green. But Yellow was also to Green as well by contact".

### C6. CAN TX/RX sticky faults from bus contention  [sim YES]
- **Cause** an LED subsystem overriding other commands; reproducible by making
  a PID fight hard while LED state changed.
- CD 426287 <https://www.chiefdelphi.com/t/426287>

---

## D. Faults that silence or disable a controller

### D1. Gate driver fault -- the most reported hardware failure  [sim PART]
- **LED** alternating cyan/orange (SPARK MAX). Blue/yellow alternating on Flex
  turned out to be **missing 12 V to the gate driver**.
- **Behaviour** persists across factory reset and reflash; recurs after
  appearing to clear; swapping the controller usually fixes it, implicating
  hardware. REV replaced units under warranty.
- CD 444231 <https://www.chiefdelphi.com/t/444231>, CD 346981
  <https://www.chiefdelphi.com/t/346981>, CD 454533
  <https://www.chiefdelphi.com/t/454533>, CD 491119
  <https://www.chiefdelphi.com/t/491119>

### D2. Blinking magenta = no valid signal  [sim YES]
- Means no communication, not a fault. Commonly a broken motor/encoder crimp, a
  CAN wiring fault, or the code not addressing that ID.
- CD 379472 <https://www.chiefdelphi.com/t/379472>, CD 347357
  <https://www.chiefdelphi.com/t/347357>

### D3. Sticky faults latching, and the silent bus a read wakes  [sim YES]
- After a motor-rail power cycle rig-flex went silent while still ACKing. Recovery
  is one frame addressed to any controller, not a Clear Faults; see CORRECTION 1.

### D4. EEPROM fault  [sim PART]
- REV acknowledged a bug producing an EEPROM fault, fixed in a later firmware.
- CD 453509 <https://www.chiefdelphi.com/t/453509>

---

## E. Motor, sensor and mechanical

### E1. Motor type misconfigured (brushless motor in brushed mode)  [sim PART]
- **Symptom** controller does not light up as expected, motor will not run.
- CD 424550 <https://www.chiefdelphi.com/t/424550>

### E2. Encoder / JST data cable faults  [sim PART]
- A disconnected crimp in the motor data cable produces sensor faults and
  "acting very strange" behaviour. Teams replace JST cables routinely.
- CD 379472, CD 371676 <https://www.chiefdelphi.com/t/371676>, CD 400769
  <https://www.chiefdelphi.com/t/400769>

### E3. Inconsistent sensor faults on Vortex  [sim PART]
- Traced to a misconnection between the Vortex and its adapter, after swapping
  encoder wires, Sparks and Vortexes.
- CD 456113 <https://www.chiefdelphi.com/t/456113>

### E4. Internal encoder resetting to abnormal positions randomly  [sim PART]
- Flex internal encoder jumps; suspected hardware or firmware. Another team saw
  the same and moved to NEO/SPARK MAX.
- CD 460577 <https://www.chiefdelphi.com/t/460577>

### E5. NEO internal short destroying the SPARK MAX  [sim NO]
- A shorted NEO killed three SPARK MAXes in succession; the temperature sense
  wire can short to the motor coils in an over-current state. Replace both.
- CD 435486 <https://www.chiefdelphi.com/t/435486>, CD 439040
  <https://www.chiefdelphi.com/t/439040>

### E6. Flex/Vortex docking screws not fully installed  [sim NO]
- CD 453509

---

## F. Firmware and tooling

### F1. Firmware update reports success without updating  [sim NO]
- "Says firmware was updated even though it always showed connection failed";
  recovery mode + mode button needed. Directly parallels the pre-24.0.1
  burn-flash bug; rig-max on 24.0.1 burns what it reports.
- CD 341470 <https://www.chiefdelphi.com/t/341470>, CD 372085
  <https://www.chiefdelphi.com/t/372085>

### F2. Mixed firmware versions across the bus  [sim PART]
- Repeated advice: update *every* device, including non-REV, then re-check.
- CD 456184, CD 480394

### F3. `kS` rejected by firmware / hard program crash on v26  [sim PART]
- REVLib 2026.0.1 + SPARK MAX 26.1: setting `kS` crashes; `kV` is accepted.
  Workaround: arbitrary feedforward. **Same firmware generation as rig-flex.**
- CD 513879 <https://www.chiefdelphi.com/t/513879>

### F4. Controller reverting to older firmware on its own  [sim PART]
- CD 400769
-: the simulator now carries a firmware version per controller and
  derives its api class from it, so a controller ON pre-25 firmware and a
  mixed-firmware fleet can both be modelled --
  `tests/adversarial/test_firmware_generations.py` and
  `test_sparkmax_bus.py::test_a_mixed_firmware_bus_shows_both_generations_at_once`.
  What is still missing is an injector that CHANGES a controller's firmware
  mid-run, which is what this entry actually describes.

### F5. Default velocity filtering unusable for high-speed control  [sim PART]
- CD 514567 <https://www.chiefdelphi.com/t/514567>

---

## G. Closed-loop / control-level

### G1. Conversion factors not applied  [sim PART] -- CD 396629
### G2. Duty-cycle absolute encoder refresh rate ~50 Hz, too slow for PID  [sim PART] -- CD 438386
### G3. Intermittent input/output mismatch and oscillation  [sim PART]
- Fixed only by restarting robot code, **not** by reconfiguring devices with the
  same code running -- points at host-side state, not the controller.
- CD 477176 <https://www.chiefdelphi.com/t/477176>

---

## Cross-cutting lessons

1. **A result code is not proof.** Writes go unacknowledged, or are acknowledged
   while the value does not stick. Retry, then read back (A1).
2. **Persist needs breathing room** after the writes it commits (A2), and should
   not be done every boot (A4).
3. **The same symptom has several causes.** Follower stutter was both a firmware
   bug and a flaky power connection (B3); missing status frames are both
   congestion and disabled frames (C1/C3).
4. **Most catastrophic failures are physical** -- gate driver, crimps, WAGOs,
   H/L swaps, shorted motors. Software can detect and report, not repair.
5. **A clean audit is not a healthy motor.** A parameter read catches A1's zeroed
   `kFF`, id 16, and E1's wrong motor type, id 2, on both generations. D1's gate
   driver fault stays invisible until the output stage is asked to work. No read
   tells you whether the firmware applied a value it stored.

---
---

# Part 2 -- protocol detail, bus signatures, and corrections

Second pass, independent research over the same corpus plus REV's docs (which
serve clean Markdown by appending `.md` to any URL; `docs.revrobotics.com/sitemap.md`
lists every page). This part adds the frame-level detail Part 1 lacked, and
corrects two things asserted earlier.

## CORRECTION 1 -- "sticky faults silence the transmitter" is unproven

Part 1 D3, and `TROUBLESHOOTING.md` as it then read, both assert that a latched
sticky fault silences a SPARK's CAN transmitter. **No REV documentation supports
this, and the semantics point the other way**: sticky faults ride in a periodic
frame -- LEGACY_STATUS_0 on pre-25, STATUS_1 on firmware 25+ -- so a device with
sticky faults is by definition still transmitting.

What actually produces "powered but silent on CAN":
- **Gate Driver Fault** -- CD 444231: "completely unresponsive to our CAN Bus and
  the REV hardware client". That is a dead unit, not fault-gating.
- **Faults vs Warnings (2025+)** -- REVLib: "Faults are fatal errors that prevent
  the motor from running." A set Faults bit pins applied output to 0 **while
  Status frames keep flowing**. The bus looks healthy; the motor does not move.
  This is a much better adversarial case than the one we assumed.
- **CAN lockout of USB** -- a SPARK that has seen a roboRIO refuses USB until
  disconnected from CAN *and* power-cycled. Looks bricked, isn't.

**However** -- on rig-flex, after a motor-rail power cycle all 8
controllers were genuinely silent while a `cansend` still completed (something
ACKed). settled the recovery: one GET_FIRMWARE addressed to id 17
alone woke all eight and erased nothing, so the clear was never the cure and
destroys the record. The *mechanism* of the silence is still unexplained.

## CORRECTION 2 -- REV's current advice is to reassert config every boot

Part 1 A4 warns against persisting every boot, which is right. But the position
that won CD 515478 goes further: **"You don't reassert the entire config from
your robot code on startup? You should. Don't depend on persistence."**
Reassert every boot in RAM; persist rarely. Note this cuts against a
configure-on-demand-only design.

## Protocol facts for building a simulator

Arbitration ID (29-bit): `[28:24]` device type (2 = motor controller),
`[23:16]` manufacturer (5 = REV), `[15:10]` API class, `[9:6]` API index,
`[5:0]` device ID.

- **roboRIO heartbeat `0x01011840`**, every 20 ms, 8 bytes, carries the enable
  bit. **100 ms without it and devices must act as if disabled.** This is the
  single best lever for simulating "everything goes limp". Measured on rig-max, firmware 24.0.1 ignores this id completely and honours the
  SECONDARY heartbeat `0x02052C80` instead, dropping output after about 240 ms
  rather than 100. Model the spec here; do not read 100 ms as a fleet number.
- Broadcast messages (devtype 0, mfr 0, class 0): Disable(0), Halt(1), Reset(2),
  Assign(3), Query(4), Heartbeat(5), Sync(6), Update(7), FirmwareVersion(8),
  Enumerate(9), Resume(10). Devices disable immediately on Disable -- but an
  enable heartbeat sent afterwards re-enables them within its own period, so a
  Disable underneath a running heartbeat stops nothing. Measured rig-max: a motor held 823 rpm for two seconds through one.
- **Legacy 16-bit fault bit order** (same bits in Faults and Sticky Faults):
  `0 Brownout, 1 Overcurrent, 2 IWDT/Watchdog Reset, 3 Motor Type, 4 Sensor,
  5 Stall, 6 EEPROM CRC, 7 CAN TX, 8 CAN RX, 9 Has Reset, 10 Gate Driver,
  11 Other, 12 SoftLimitFwd, 13 SoftLimitRev, 14 HardLimitFwd, 15 HardLimitRev`.
  Cross-checked against REVLib's FaultID enum and a verbatim RHC dump in CD 444231.
  That word lives in bytes 2:6 of api 0x060, little-endian, the low sixteen
  bits active and the high sixteen sticky (`admin.decode_legacy_status_0`).
  On firmware 25+ the same field is pinned all-ones as a presence beacon, so
  scoring it reports sixteen faults on a healthy controller.
- **Firmware 25+ splits these into Faults{} and Warnings{} with a different
  layout**, so a 2024-era decoder mis-parses a 25+ frame and a 25-era decoder
  mis-parses a pre-25 one. The split is by FIRMWARE, not by product: rig-flex's
  Flexes on 26.1.6 carry the STATUS_1 layout, rig-max's SPARK MAXes on 24.0.1
  carry the 16-bit word above, and a SPARK MAX updated to 25.0.0 would carry
  STATUS_1 like a Flex. `admin._FAULT_BITS` and `_WARNING_BITS` hold the
  25+ bytes and `_LEGACY_FAULT_BITS` the word above; `normalise_generation`
  refuses a product name outright so the two cannot be picked by product.
- Timeouts: default periodic-status timeout **500 ms** (REVLib 2024.2.4+), floor
  `2.1 x` frame period; RTR retries default to **5**.
- ~70% bus utilisation is the practical ceiling.

## Bus signatures worth simulating, per failure

**Duplicate CAN ID** -- Status-0 payload for that ID **flip-flops between two
different payloads** on successive frames; error frames burst; TEC/REC climb on
both nodes; at TEC > 255 a node goes bus-off. Parameter *reads* get two colliding
answers (which is why a scan sees one device); parameter *writes* hit **both** --
"if you try to change the ID while both are plugged in, it will try and change
both, and you end up with them still being the same" (CD 495329). Classic
presentation: "only the first, third and fifth Sparks work" (CD 403172).

**Reverted to ID 0** -- the device *is* transmitting, at `base | 0`, colliding
with anything else on 0. The expected ID goes completely silent. An enumerate
still finds a REV motor controller. Distinguishes cleanly from a dead node.

**Intermittent dropoff** -- Status frames stop entirely for 50-500 ms then resume
**with `hasReset` NOT set** (it never rebooted, it went deaf). CAN utilisation
dips align with the gap. CAN TX/RX sticky latch and stay -- two bits on pre-25,
canTx 7 and canRx 8, which firmware 25+ folds into a single CAN fault at bit 3 of
the STATUS_1 Faults byte, so the pair cannot be told apart there
(`admin._FAULT_BITS`). A physically broken chain instead removes everything
*downstream* at once, permanently -- different signature. Best-instrumented case
CD 477176: `.set()` commanding non-zero while `getAppliedOutput()` reads
exactly 0.

**Brownout** -- bus voltage collapses, read out of Periodic Status 1 on pre-25
and out of STATUS_0 on firmware 25+; the brownout bit sets and its sticky copy
**latches after recovery**, so a driver reading only live faults sees nothing
afterwards. Which bit that is splits on firmware as well. On pre-25 it is bit 0
of the 16-bit word above and `Has Reset` is bit 9. On firmware 25+ both are
WARNINGS rather than faults -- BROWNOUT_WARNING at bit 16 of STATUS_1 and
HAS_RESET_WARNING at bit 22, sticky copies at 40 and 46
(`admin._WARNING_BITS`, REV-spark-frames-2.1.0 STATUS_1). If the
controller's own rail dips far enough it reboots, hasReset latches, and all
volatile config goes with it: on rig-max, a rail cycle reverted every
parameter this package had written over CAN, and every status period with them.
The reboot is also where the two generations part. It CLEARS the sticky byte on
firmware 25+, erasing the brownout record it has just written, and leaves that
byte standing on pre-25, where the cycle SETS hasReset instead
(`admin._STICKY_AGE_FW25` and `_STICKY_AGE_PRE25`). Note the roboRIO does
**not** stop transmitting CAN at brownout stage 2 -- **CAN can look healthy while
every actuator is dead.**

**Position spike on reset (CD 460577)** -- the single best frame-level report in
the corpus. Position freezes ~0.5 s, jumps among `0`, `+0.2679443359375`,
`-0.2679443359375`, then resumes counting from one of them. Simultaneously
**eleven fault bits set for exactly one loop cycle and none become sticky**. A
driver that latches any fault bit breaks; one that feeds position to a PID slams.

**Thermal** -- motor temperature climbs, applied output goes to 0 while frames
keep flowing, then **recovers by itself with no sticky fault and no operator
action**. Auto-recovery is the distinguishing signature. What can be REPORTED
splits on firmware and not on product: firmware 25+ raises a temperature fault,
bit 4 of the STATUS_1 Faults byte (TEMPERATURE_FAULT in REV-spark-frames-2.1.0,
`admin._FAULT_BITS`), while the pre-25 16-bit word has no temperature bit
at all, so a pre-25 controller folding back has nothing to show for it but the
motor temperature in Periodic Status 1. Whether a pre-25 controller folds back
the same way has not been tested on rig-max. SPARK MAX has no documented LED code
or threshold for this.

**Silent return from a power cycle (firmware 25+)** -- every controller comes
back powered and ACKing, broadcasting nothing at all, and stays that way: 60 s of
quiet changed nothing on rig-flex, and then one GET_FIRMWARE addressed
to id 17 alone brought all eight back with sticky `hasReset` intact. Pre-25 does
not do this -- rig-max's eight MAXes on 24.0.1 came back broadcasting immediately
from every rail cycle, at REV's cold 10 ms default rather than the 50 ms this
package writes (rig-max and). An instrument that transmits
cannot measure this signature at all, which is how it was mis-read twice.

## Additions to the failure list

- **B5. Follower mechanics** -- `kFollowerID` (param 57) stores the **full 29-bit
  arbitration ID** of the leader's Status 0 frame. So the **follow rate is capped
  by the leader's Status 0 period** -- lowering it to save bandwidth makes
  followers steppy, a software bug that looks mechanical. REV-Specs
  SparkParameters v0.1.2 names 57 "Legacy follower arbitration ID"; firmware 25+
  follows on parameter 194 Follower Mode Leader Id, which takes a plain CAN id,
  and 195 Follower Mode Is Inverted -- another pre-25/25+ split rather than a
  MAX/Flex one. A follower with a missing leader sets its own Is-Follower bit
  with applied output 0. REVLib 2025 makes follower mode work "even if the
  follower is not referenced in user code"
 -- **a stuck follower can drive a mechanism with no object in your program.**
- **A7. `configureAsync()` returns `kOk` immediately** and does the work in the
  background. A driver treating that as confirmation knows nothing.
- **A8. `hasReset` correlates with `kTimeout`** -- CD 480555: "Every time we had a
  motor reporting kTimeout instead of kOk, it also had a has reset sticky fault."
  Correct response to `hasReset` is to **re-apply the full configuration
  including frame periods**, then clear sticky -- not just clear the fault.
- **A2 refined** -- CD 432129's actual fix was batching all SPARKs and flashing
  them together **+200 ms after** configuration.
- **E7. The mode button toggles motor type on a ~3 s press.** A pinched or
  vibrating mode button silently flips a controller to brushed mid-match.
- **C7. Termination is measurable**: ~**60  ohm** across CANH/CANL with power off
  (two 120  ohm in parallel). 120  ohm means only one end is terminated. Anything else
  is a wiring fault.
- **F6. Firmware 25.0.0 causes high CAN utilisation** whenever a 24.0.x device is
  on the bus and the Hardware Client is open. Fixed in 25.0.1; update 25.0.0
  units **individually with robot power off**.: a 24.0.x device can
  now be placed on a simulated bus (`spark(dev, firmware="24.0.0")` broadcasts
  api class 6), so the mixed-firmware precondition is expressible; the
  utilisation behaviour itself still has no injector.
- **G4. Velocity filter default** is a 64-tap FIR at 1 ms plus a 100 ms averaging
  window -- ~164 ms window, **~82 ms phase lag**. Not a fault; a defaults trap.
- **H1. Two default commands on one subsystem** presents as CAN TX/RX sticky
  faults, not as a command-scheduler error (CD 426287). The inverse trap: a
  software bug wearing a wiring bug's symptoms.
- **A9. A BOOL parameter stores a value outside 0/1, and the stored value behaves
  as the permissive one.** Measured on rig-flex id 17, firmware 26.1.6.
  Limit Switch Fwd Polarity written 2 answered Success, echoed 2 and read back 2,
  and the forward limit then read NOT reached, with parameter 51 held at 1 as the
  control in the same sample. On a robot whose data port needs polarity 1, a
  mistyped write releases the interlock while reporting success. That is the
  fail-dangerous direction, and it is why every polarity write is read back
  (`provenance.flex.bool_params_store_out_of_range_values`).

## Additional unconfirmed items

- Per-frame arbitration IDs beyond Status 0 for the *legacy* layout are derived,
  not published: REV's pre-25 control-interfaces page gives no arbitration ids at
  all, and 0x060-0x067 follow from api class 6. This was recorded as irrelevant
  because rig-flex is on 26.1.6 and takes its ids from REV-Specs. It is not
  irrelevant now: rig-max broadcasts on exactly those derived ids, and 0x064 is
  absent there in every state observed (rig-max and).
- No published CAN control-frame watchdog timeout; the documented 60/50 ms is
  PWM-only, and REV's two pages disagree. Only firm number is WPILib's 100 ms
  heartbeat rule.
- REV's Status 2 default period is stated as both 20 ms and 50 ms on one page.
  REV-spark-frames-2.1.0 gives STATUS_2 `defaultPeriodMs` 20, which is the newer
  source and the one to score against.
- SPARK MAX over-temperature behaviour: no LED code, no published threshold.
- Follower-with-lower-CAN-ID stutter: two teams, independent hardware, never
  reproduced by REV, never resolved. Inversion may be the real variable.
- REV's USB packet table says manufacturer `0x15`; the decoded value is `0x05`.
  The page appears to be in error: REV-spark-frames-2.1.0 `deviceInfo` gives
  `manufacturerNumber` 5 and `deviceTypeNumber` 2.
