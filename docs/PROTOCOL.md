# SPARK protocol ground truth, read off REV's own machine-readable spec

Everything here comes from two files REV publishes, mirrored in this tree, plus
REVLib C++ headers preserved from a volatile scratchpad:

    reference/REV-spark-frames-2.1.0.json
    reference/REV-SparkParameters-v0.1.2.md
    reference/revlib-2026.0.2/SparkParameters.h
    reference/revlib-2025.0.3/SparkParameters.h

Both REV files came from github.com/REVrobotics/REV-Specs, named at
`docs/runs/rig-flex-probe-log.md:64`. Read them rather than
this summary whenever the two disagree.

This document exists because the driver was built on a product split that the
primary source does not make, and three separate things in the code were wrong
in the same way. It records what the spec actually says.

Amended on rig-max. Two things changed. Section 3's frame count was
wrong and is corrected below from a recount of the JSON, which reverses an
overrule this file made against an earlier draft that had it right. And section
3c is new: the pre-25 parameter api, which nothing else here describes, because
the vendored spec describes a 25+ device and nothing else.

So the sentence above needs one qualification from here on. Sections 1 through
3b come from the files named above. Sections 3c, 3d and 3e do not and cannot:
they are measurement on eight SPARK MAX running firmware 24.0.1, and for most of
the frames they describe there is no vendor document at all. The exception is
named where it applies -- SET_CAN_ID in 3d is REV's own frame,
versionImplemented 1.5.0, which is why a controller on 24.0.1 carries it.

Corrected again, in place instead of as a new section. Section 2's
period unit is settled, not unresolved. 3c was missing the firmware's type check
on a write, and the burn-flash leg of its safety rule does not hold. Section 4
items 6 and 7 were both wrong as written. And several file:line citations had
moved under the code and were repointed at what they name.

Amended again on rig-max, and this is the largest change the file has
taken. The pre-25 burn flash was sent with a payload for the first time and it
works, so section 3c's "nothing here persists" is wrong and is rewritten, and
the burn's wire contract has a subsection of its own. Three further pre-25
frames were recovered afterwards and section 3d is their wire contract: a
per-device fingerprint answered at api 0x094, identify addressed by CAN id with
an empty payload, and SET_CAN_ID taking that fingerprint as its Unique ID.
Section 3e is the shape all of them share, which is that payload LENGTH carries
meaning here and a zero-length frame is not inert. Section 3 gained a
subsection of a different kind, read off the vendored spec and not
measured. Every documented parameter READ frame is marked RTR, and this
package was sending zero-length data frames, which put the cause of the Flex
read silence back in question.
Section 4 carries the consequences as items 12 through 16, section 5 gains the
questions they answer, and the 242-versus-255 correction in the safety rule is
re-stated now that 0x072 is known to be a live command rather than a rumoured
one. The file:line
citations into this package had drifted again, so they are cited by file and
SYMBOL from here on; symbols do not move under an edit and line numbers do.

What did NOT change, and must not be read into any of the above. A plain
parameter write is still RAM only. The status periods still do not survive a
power cycle and the burn does not reach them. This package still neither sends
nor exposes the pre-25 burn, so nothing here persists a configuration on that
generation. Firmware 25+ carries PERSIST_PARAMETERS, and `spark persist`,
`spark repair --persist` and `spark provision --persist` send it. And the four
bytes at api 0x094 are a fingerprint, or the firmware's Unique ID, and NOT "the
REV serial": REV Hardware Client does not display them and pre-25 identify does
not take them.

Amended again on rig-flex, and it overturns what this file said about
Flex parameter reads. A SPARK Flex on firmware 26.1.6 answers every parameter
read, so the frame form caused the silence recorded here. The request has to be
a genuine remote frame carrying dlc 8. A zero-length data frame is ignored, and
so is a remote frame at dlc 0. The driver sends that form now across all eight
read classes, and `read_param` is deleted because it read on the write api.
Section 3 and its RTR subsection are rewritten on that basis. Section 4 carries
the consequences in items 4, 5, 10 and 11, and section 5 answers both read rows.
GET_FIRMWARE stays the exception, because it is rtr with lengthBytes 8 in the
same spec and answers at dlc 0. So the dlc rule is a property of the parameter
classes alone.

---


## Scope: what this tree assumes about the fleet

**On the rigs measured here, every SPARK MAX is pre-25 and every SPARK
Flex is firmware 25 or later.** rig-max is eight MAX on 24.0.1 and rig-flex is eight
Flex on 26.1.6. No robot here mixes them.

That is a fact about this fleet, not about the protocol, and the difference
matters. Frame layout, the fault word, the parameter dialect, persistence and
identify all key on FIRMWARE GENERATION. A SPARK MAX updated to 25.0.0 broadcasts
and is written exactly like a Flex. Conflating product with generation has gone
wrong here before, most expensively when a MAX layout was applied to a Flex and a
healthy 13.7 V rail decoded as a fault bitfield, which wedged a base.

So the code keeps reading the generation off the wire, and the assumption is
written down in one place -- `EXPECTED_GENERATION` in `admin.py` -- with
`unexpected_generation` reporting a violation. `spark status` and `spark audit`
both print FLEET ASSUMPTION BROKEN if the wire ever disagrees with
`controller_type`.

**If you change anything that leans on "MAX means pre-25", say so at the site and
check it.** Search for `EXPECTED_GENERATION` to find everywhere that leans on it.
If a controller is ever reflashed, this is the first thing to revisit.

## 1. The frame split is FIRMWARE VERSION, not product

This is the headline, and it overturns how `admin.API_SETS` is organised.

`REV-spark-frames-2.1.0.json` describes ONE device. Its `deviceInfo` block is:

    {"deviceType": "MotorController", "deviceTypeNumber": 2,
     "manufacturer": "REV", "manufacturerNumber": 5}

There is no product field anywhere in it. No SPARK MAX entry, no SPARK Flex
entry. Every frame instead carries `versionImplemented` and sometimes
`versionDeprecated`, and that is the only axis the spec splits on.

| frame | api | implemented | deprecated |
| --- | --- | --- | --- |
| LEGACY_STATUS_0 | 0x060 | 0.0.1 | **25.0.0** |
| STATUS_0 | 0x2E0 | 25.0.0 | - |
| STATUS_1 | 0x2E1 | 25.0.0 | - |
| STATUS_2.. STATUS_8 | 0x2E2.. 0x2E8 | 25.0.0 | - |
| STATUS_9 | 0x2E9 | 26.0.0 | - |
| UNIQUE_ID_BROADCAST | 0x2F0 | 25.0.0 | - |
| BOOTLOADER_0 | 0x2C0 | 26.0.0 | - |

REV's own description of LEGACY_STATUS_0:

> This frame exists purely to inform old software that is not aware of firmware
> version 25+ that the SPARK is present

So 0x060 is not "the SPARK MAX frame". It is the pre-25 frame, deprecated at
25.0.0, and on firmware 25+ it is a presence beacon for old software. A SPARK
MAX on firmware 25+ broadcasts on 0x2E0/0x2E1 like anything else.

**There is exactly ONE legacy periodic frame in the spec.** LEGACY_STATUS_1
(0x061) and LEGACY_STATUS_2 (0x062) do not appear in frames 2.1.0 at all. The
repo carries them in `tests/support/sparksim/frames.py` and in
`controller._CONFIGS[SPARK_MAX]`; they describe pre-25 firmware and are
not part of the current protocol.

### What this means for the driver

`fault_frame()` in `admin.py` returned STATUS_0 as the fault-carrying
frame for a SPARK MAX. **On firmware 25+ that is wrong.** Faults are in STATUS_1
for every SPARK on 25+. The correct discriminator is the controller's firmware
version, which `SparkAdmin.firmware` already reads. `fault_frame` takes a
generation now; section 4 item 2 has the fix.

This is the same mistake as the 0x06CE incident, made again in the opposite
direction: a product distinction imposed where the real one is a firmware
generation.

### Which frame carries what, on firmware 25+

STATUS_0 (0x2E0) signals:

    APPLIED_OUTPUT, VOLTAGE, CURRENT, MOTOR_TEMPERATURE,
    HARD_FORWARD_LIMIT_REACHED, HARD_REVERSE_LIMIT_REACHED,
    SOFT_FORWARD_LIMIT_REACHED, SOFT_REVERSE_LIMIT_REACHED,
    INVERTED, PRIMARY_HEARTBEAT_LOCK, SPARK_MODEL, RESERVED

STATUS_1 (0x2E1) signals -- every fault, warning and sticky, plus IS_FOLLOWER:

    OTHER_FAULT, MOTOR_TYPE_FAULT, SENSOR_FAULT, CAN_FAULT,
    TEMPERATURE_FAULT, DRV_FAULT, ESC_EEPROM_FAULT, FIRMWARE_FAULT,
    BROWNOUT_WARNING, OVERCURRENT_WARNING, ESC_EEPROM_WARNING,
    EXT_EEPROM_WARNING, SENSOR_WARNING, STALL_WARNING, HAS_RESET_WARNING,
    OTHER_WARNING,... the sticky counterpart of each..., IS_FOLLOWER

### The product is readable from the wire

`STATUS_0` carries **SPARK_MODEL**: uint, bit position 54, length 4 bits. So a
controller says which model it is in every STATUS_0 frame, and the driver does
not have to be told by a config file at all. `admin.MODEL_FOR_TYPE`
already maps 1 -> Flex and 2 -> MAX and the audit compares it; nothing yet uses
it to SELECT the decode.

---

## 2. The parameter table is product-neutral

`rev_parameter_index.tsv` is a column-reordered, description-stripped copy of
`REV-SparkParameters-v0.1.2.md`. Verified row for row: 199 rows each, ids 0-198
on both sides, all 199 identical on name, type, access mode and default, zero
disagreements.

That REV document is titled **"SPARK Configuration Parameters"**. Line 61 reads
"all the configurable parameters within the SPARK". It names no product in its
heading or preamble, and the one row that distinguishes hardware names BOTH:

    | Duty Cycle Sensor Prescaler | 153 | UINT32 | RW | 17 |... For Flex, the
    device clock is 170MHz, and for MAX, the device clock is 72MHz |

So the table is REV's joint SPARK table. The repo's earlier reading of it as
"the Flex parameter index" was wrong.

The separate page at docs.revrobotics.com/brushless/spark-max/parameters IS
SPARK MAX scoped, but it is a different and older table: it stops at id 133,
is stamped "Last updated 1 year ago", and contradicts REV-Specs on ids 3, 4 and
45. It describes the pre-25 parameter set.

### Status frame period parameter ids

From `REV-SparkParameters-v0.1.2.md:225-232` and REVLib `SparkParameters.h`:

| parameter | id | type | mode | default |
| --- | --- | --- | --- | --- |
| Status 0 Period | 158 | UINT32 | RW | 10 |
| Status 1 Period | 159 | UINT32 | RW | 250 |
| Status 2 Period | 160 | UINT32 | RW | 20 |
| Status 3 Period | 161 | UINT32 | RW | 20 |
| Status 4 Period | 162 | UINT32 | RW | 20 |
| Status 5 Period | 163 | UINT32 | RW | 20 |
| Status 6 Period | 164 | UINT32 | RW | 20 |
| Status 7 Period | 165 | UINT32 | RW | 20 |
| **Status 8 Period** | **199** | UINT32 | RW | - |
| **Status 9 Period** | **224** | UINT32 | RW | - |

**Status 8 and 9 are NOT 166 and 167.** 166 and 167 are MAXMotion Max Velocity 0
and MAXMotion Max Accel 0, both FLOAT. The simulator mapped 0x2E8 and 0x2E9 to
166/167 because they follow 165, which would have made a period write land on a
motion limit; fixed and pinned by
`tests/adversarial/test_param_sweep.py::test_status_8_and_9_periods_are_not_166_and_167`.

The fleet's parameter index stops at 198, so it could never have shown this. The
ids come from REVLib `SparkParameters.h:190` and `:215`, which declares ONE
`enum SparkParameter: uint8_t` in `namespace rev::spark` with no product split.
The `signals` config that writes them belongs to `SparkBaseConfig`, inherited
unchanged by both `SparkMaxConfig` and `SparkFlexConfig`.

Force-enable flags: `kForceEnableStatus_0` = 186 through `_7` = 193, then
`_8` = 200 and `_9` = 225.

### SETTLED: the period unit is MILLISECONDS

REV-Specs says the period is **microseconds**:

    | Status 0 Period | 158 | UINT32 | RW | 10 | Status frame 0 period, in us |

That is a documentation error, and the wire says so. REVLib's own doc comments
say milliseconds, and the SPARK MAX control-interfaces page gives a 1 to 32767
**ms** range. Parameter 159 was written 20 -> 50 on rig-flex id 11 alone, and that
controller's STATUS_1 cadence moved 20.0 -> 50.0 ms while the other seven held
20.0 ms throughout and it returned to 20.0 ms on restore. The rig-flex run note for
that date is written up as read-only and does not carry this write, so take the
provenance claim as the record of it. 50 us would be 20 kHz
and is not achievable on a 1 Mbit bus. Recorded as `both.status_period_unit`,
marked HARDWARE, at `provenance.py:203`. The driver writes these values as
milliseconds and is right to.

Scope, because it matters here: that measurement is a SPARK Flex on 26.1.6, so
it settles the unit for the 25+ PARAMETER at ids 158-165. On pre-25 the question
does not arise. The periods are not parameters there and move on api class 6,
whose payload is two bytes of little-endian milliseconds. See section 3c.

---

## 3. The documented parameter READ protocol, and the one the driver used to use

Until the driver had a `SparkAdmin.read_param` that sent a one-byte
frame on `PARAMETER_WRITE` (api 0x0E0) and treated a short payload as a read.
**That was not in REV's spec**, one appended byte made it a write, and it was
answered on neither generation. It is deleted. The frames below are what the
driver sends now.

What the spec actually defines:

| purpose | frame | apiClass | api | shape |
| --- | --- | --- | --- | --- |
| write one parameter | PARAMETER_WRITE | 14 | 0x0E0 | PARAMETER_ID, VALUE |
| write response | PARAMETER_WRITE_RESPONSE | 14 | 0x0E1 | PARAMETER_ID, PARAMETER_TYPE, VALUE, RESULT_CODE |
| read a PAIR of parameters | READ_PARAMETER_n_AND_n+1 | 15 to 22 | 0x0F0 upward | FIRST_PARAMETER_VALUE, SECOND_PARAMETER_VALUE |
| write a PAIR | WRITE_PARAMETER_n_AND_n+1 | 23 to 30 | 0x170 upward | FIRST_PARAMETER_VALUE, SECOND_PARAMETER_VALUE |
| get 16 parameter TYPES | GET_PARAMETER_n_TO_n+15_TYPES | 13 | 0x0D0 + index | TYPE_0.. TYPE_15 |

Reads are addressed by a dedicated arbitration id per parameter PAIR, one frame
per pair, and the coverage is the WHOLE table. What stood here
said the opposite. It was wrong, and the correction follows.

### CORRECTION. The read frames cover 0-255, not 128-159

Recounted from `reference/REV-spark-frames-2.1.0.json` by walking
`nonPeriodicFrames` and grouping every frame on its own `apiClass` field, rather
than reading two example lines:

    READ_PARAMETER_n_AND_n+1         128 frames   apiClass 15-22   16 per class
    WRITE_PARAMETER_n_AND_n+1        128 frames   apiClass 23-30   16 per class
    GET_PARAMETER_n_TO_n+15_TYPES     16 frames   apiClass 13

All 272 carry `versionImplemented` 25.0.0, and none carries a
`versionDeprecated`. Each read frame carries two parameters, so 128 frames carry
256 ids. Taking the ids from the frame NAMES and sorting them gives exactly 0
through 255, no gap and no repeat: the first frame is READ_PARAMETER_0_AND_1 and
the last is READ_PARAMETER_254_AND_255. The write set is the same shape.

| kind | apiClass | parameters | arbId first.. last |
| --- | --- | --- | --- |
| READ | 15 | 0-31 | 0x2053C00.. 0x2053FC0 |
| READ | 16 | 32-63 | 0x2054000.. 0x20543C0 |
| READ | 17 | 64-95 | 0x2054400.. 0x20547C0 |
| READ | 18 | 96-127 | 0x2054800.. 0x2054BC0 |
| READ | 19 | 128-159 | 0x2054C00.. 0x2054FC0 |
| READ | 20 | 160-191 | 0x2055000.. 0x20553C0 |
| READ | 21 | 192-223 | 0x2055400.. 0x20557C0 |
| READ | 22 | 224-255 | 0x2055800.. 0x2055BC0 |
| WRITE | 23-30 | 0-255, 32 per class | 0x2055C00.. 0x2057BC0 |

**The earlier draft was right, and this correction reverses the overrule.** The
text that stood here said: "An earlier draft of this file said 128 such frames
covering ids 0-255. That was wrong". The draft was not wrong. The overrule was,
and 128 frames covering 0-255 is exactly what the file contains.

The error is worth naming, because both observations behind it are true and
neither means what was drawn from it. apiIndex 15 IS the last frame of apiClass
19, and apiClass 19 DOES carry parameters 158 and 159. But apiIndex counts
within a class, and apiClass 19 is the fifth of eight read classes. One class
was counted and reported as the file total, which turned 128 frames into 16 and
a whole parameter table into a 32-id window.

A second mis-citation, found in the same recount. This file said REV's SPARK MAX
note appears "128 occurrences across the file, once per read and write pair
frame". The number 128 is right; the attribution is not. The note is on all 128
READ frames and on NO write frame. READ_PARAMETER_0_AND_1's description reads:

> Read parameter 0 and 1 at the same time. SPARK MAX does not currently support
> this in v25.0.0-prerelease.4

WRITE_PARAMETER_0_AND_1's reads:

> Write Parameter 0 and 1 at the same time. Two Write Parameter Response frames
> will be sent in response.

So REV's product caveat is scoped to paired READS on a named prerelease, and the
spec says nothing about a MAX and paired writes either way.

### What the correction changes

The old conclusion that "the read path cannot be retired" rested entirely on the
undercount, and it does not survive. On firmware 25+ every parameter this
package cares about has a documented read frame:

| parameter | frame | apiClass | apiIndex | arbId |
| --- | --- | --- | --- | --- |
| 0 CAN ID | READ_PARAMETER_0_AND_1 | 15 | 0 | 0x2053C00 |
| 11 Current Chop | READ_PARAMETER_10_AND_11 | 15 | 5 | 0x2053D40 |
| 59 Smart Current Stall Limit | READ_PARAMETER_58_AND_59 | 16 | 13 | 0x2054340 |
| 61 Smart Current Config | READ_PARAMETER_60_AND_61 | 16 | 14 | 0x2054380 |
| 199 Status 8 Period | READ_PARAMETER_198_AND_199 | 21 | 3 | 0x20554C0 |
| 224 Status 9 Period | READ_PARAMETER_224_AND_225 | 22 | 0 | 0x2055800 |

So spec coverage never forced the undocumented one-byte read on PARAMETER_WRITE
on a 25+ device. The driver forced it, by implementing one read class out of
eight. On `read_param_pair` took the whole 0-255 range, and
`read_param` was deleted for reading on an api that can write. The canary in
tools/spark_param_sweep.py stays, because it would catch a future edit that
routed these reads back onto a write api.

Two reasons make the documented frames the right ones, and the recount widened
the first from a 32-id window to the whole table. A read on apiClass 15-22 is a
different api from PARAMETER_WRITE, so no payload length can turn it into a
write, which is the hazard the write-api read carried until it was deleted. And
`GET_PARAMETER_n_TO_n+15_TYPES` answers "which ids exist on this device and what
type is each" in sixteen frames of sixteen codes, apiClass 13, apiIndex 0 at
arbId 0x2053400 through apiIndex 15 at 0x20537C0, covering 0-255.

**The undercount was copied into the code and the tests, and was corrected.** Each of these restated 16 frames and 128-159:

    admin.READ_PAIR_FIRST_ID = 128 and READ_PAIR_LAST_ID = 159,
        and the comment above them
    admin.read_param_pair, which returned None outside that window
    sparksim.frames.READ_PARAM_PAIR, so the simulator answered apiClass 19
        and no other
    tests/adversarial/test_param_read_frames.py, both the module docstring
        and the coverage test

That test is gone, and `tests/adversarial/test_param_read_frames.py` now asserts the opposite in
`test_every_parameter_0_to_255_has_a_documented_read_frame`. Parameters 0, 59, 199 and 224 all
answer, and `read_param_pair` range-checks against `PARAM_ID_MAX`, so an id outside 0-255 returns
None and sends nothing. Driver, simulator and test were widened together, and the
widening was measured on rig-flex. A full sweep of id 17 sent all 16 type frames and all 128 value
frames, and every one answered. The fleet comparison then covered 185 implemented ids across all
eight controllers. A pre-25 device answers none of them:
`tools/spark_read_frame_form.py` sent both apis to rig-max id 3 in all three forms and all six drew silence.

### The read frames are RTR, and this driver sends data frames

Added from the vendored spec and this package's own code, and
SETTLED on hardware. The prediction was half right. A remote frame is
necessary and it is not sufficient: the request also has to carry dlc 8, and a
remote frame with dlc 0 draws the same silence as a data frame.

Every Read Parameter and Get Parameter Types frame in frames 2.1.0 carries
`rtr: true` -- 144 of them, sixteen in each of apiClass 13 and 15 through 22.
`SparkAdmin.read_param`, `read_param_pair` and `param_types` all send
`can.Message(arbitration_id=arb, data=b"")`, which is a zero-length DATA frame
and not a remote frame. The one read that DOES answer on 26.1.6 is GET_FIRMWARE,
apiClass 9 index 8, also `rtr: true` in the spec, and it is the one this package
sends with `is_remote_frame=True` in `SparkAdmin.firmware`. So the read that
works is formed the way the spec asks and the reads that are silent are not.

This DOES overturn `flex.no_param_reads_on_any_api` as its name reads. The
silence was a property of the frame form. Sent as remote frames with dlc 8, all eight read classes and all sixteen type frames answered on every
controller on rig-flex, with zero silent frames across a full 0-255 sweep. The run that produced the claim varied the API and never the frame form,
so its conclusion did not follow from its evidence.

What survives of that claim is its third frame. The undocumented one-byte read
on PARAMETER_WRITE has no rtr form to get wrong, it went out correctly, and it
was not answered. `read_param` was deleted for that reason and
because one appended byte made it a write. Recorded as
`flex.param_reads_were_probed_as_data_frames`, now HARDWARE, with
`docs/runs/rig-flex-parameter-reads.md` carrying the run.

It is the same shape as the burn one section further down. A wrongly-formed
frame drew silence, and the silence was read as the capability not existing. The
frame form was fixed the day it was measured, so this driver now reads a Flex
parameter table over all eight read classes.

### REV records a SPARK MAX limitation here, in the primary source

The note quoted above appears once on each of the 128 READ_PARAMETER frames.
That is REV stating a product difference explicitly, in the spec, and it is the
only such statement about these frames. It is version-scoped to
v25.0.0-prerelease.4, so whether a shipping MAX firmware on 25+ supports paired
reads is open. A MAX on 24.0.1 should carry none of them, since they are
versionImplemented 25.0.0, and rig-max answered no read on apiClass 13 or 19.
That probe read weaker than it looked, because two of its four sends were remote
frames with dlc 0, and rig-flex has since shown that form silent even where the
frames exist. `tools/spark_read_frame_form.py --id 3` closed it at rig-max: both apis in all three forms, six sends, no answer.

---

## 3b. Why the product axis looked right, and the third source that settles it

Added after re-reading the fetched docs corpus.

**REV publishes exactly one periodic status frame table, and it is the SPARK MAX
one.** Of 1040 pages fetched during the 2026-08 documentation sweep, which was
not kept in this repository, only
`rev__docs-revrobotics-com-brushless-spark-max-control-interfaces.txt` carries a
"Periodic Status N" table. The SPARK Flex counterpart,
`...spark-flex-feature-description-control-interfaces.txt`, is 4 KB, is stamped
"Last updated 2 years ago", and its CAN section ends "More information coming
soon!" It documents no frames at all.

So the only per-device frame documentation on REV's website describes a SPARK
MAX, and it describes pre-25 firmware. Reading it as "the MAX layout" rather
than "the pre-25 layout" is the natural mistake, because there is no Flex table
to compare it against. That is the likely origin of `_CONFIGS[SPARK_MAX]` and of
everything downstream of it.

**The third source.** REVLib's changelog, `Version 2025.0.0`, Major Changes:

> [REVLib] Requires non-prerelease versions of SPARK and Servo Hub firmware
> v25.0.0 or higher

REV speak of one "SPARK firmware v25.0.0", not of a MAX firmware and a Flex
firmware. The same release adds `SparkFlexConfig` and `SparkMaxConfig` as
CONFIG classes while the frame structs stay shared in `SparkLowLevel.h`. Product
decides configuration surface; firmware version decides frames.

That is three independent sources agreeing: the frame spec, the library headers,
and the release notes.

---

## 3c. The pre-25 parameter api: api class 48, measured and not published

Added. Measured on rig-max: eight SPARK MAX, firmware 24.0.1,
generation `pre25`, on a 1 Mbit gs_usb adapter named `can0`.

Sections 1 to 3 describe 25+ frames only, because the vendored spec describes a
25+ device only. In the parameter space `REV-spark-frames-2.1.0.json` contains
nothing below `versionImplemented` 25.0.0: the 272 read, write and type frames
counted above are all 25.0.0, and so is PARAMETER_WRITE itself, apiClass 14,
apiIndex 0, arbId 0x2053800. A controller on 24.0.1 carries none of them, and an
extended id that matches no frame is dropped with no reply. That silence is why
this document had nothing to say about pre-25 parameter access, and why a 24.0.1
fleet reads as unwritable.

It is not unwritable. It answers on a different api.

### The frame

    arb   = 0x02050000 | ((0x300 | param_id) << 6) | device_id

    read   DLC 0, no payload
    write  5 bytes:  [int32 value, little-endian][type tag]
    reply  on the SAME arbitration id: [uint32 value][type tag][status byte]

There is no separate response id, and no response frame to match up. The
parameter id rides in the ARBITRATION id rather than in the payload, and DLC
alone separates a read from a write. Type tags:

    0 int32     1 uint32     2 float32     3 bool

Status byte 0 is success. Any other value is a refusal, and the device still
replies. A write echoes back the value the device took, so a write is its own
read-back.

The firmware TYPE-CHECKS a write. The tag in byte 4 has to match the type the
parameter actually holds; a mismatch is refused with a non-zero status and the value does
not move. So the tag belongs to the parameter, not to the caller. Read the id
first and reuse the tag its reply carries rather than assuming one, which is
what `_legacy_roundtrip_burst` in `tests/hardware/test_config_injection.py`
does, and says.

`0x300 >> 4` is 48, so in 25+ numbering this is api class 48 and upward:
parameters 0-15 land in class 48, 16-31 in class 49, and the last id of the
pre-25 table, 133, in class 56 index 5 at arbId 0x0205E140. Frames 2.1.0 defines
no frame at all in apiClass 48 through 62. The api space pre-25 firmware uses
for parameters is empty in the 25+ spec, which is why the two dialects coexist
in one driver. That holds until the top of the range, which is the hazard in the
safety rule below.

The driver side is `SparkAdmin.read_legacy_param` and
`SparkAdmin.write_legacy_param`, with `admin.LEGACY_PARAM_ACCESS` = 0x300
and `admin.LEGACY_PARAM_MAX` = 133.

### What was measured

On rig-max, all 134 parameters, ids 0 through 133, were read on all
EIGHT controllers. 134 ids times 8 devices, every one answered. Zero refused,
zero silent.

Parameter 0 returns each device's own CAN id. That is the self-check that the
reads are live and per-device: eight different answers to the same question, one
per controller, each matching the id the frame was addressed to.

Writes answer in the same way. Idle Mode, parameter 6, was toggled both
directions four times with independent reads in between, and an operator
confirmed the brake resistance by hand.

Parameters 158 to 165 reply with a NON-ZERO status byte. On pre-25 the status
periods are not parameters at all. They move on api class 6,
`admin.LEGACY_SET_PERIOD`, arb = 0x02051800 + (frame_index << 6) + id, two
bytes little-endian milliseconds, and the device sends no acknowledgement. A
refusal is still an answer, which is how that case is told apart from a device
that is not listening.

That write reaches frames 0 through 6 and no further, which is MEASURED and not
a precaution. On rig-max, id 3's 0x067 was broadcasting at REV's
250 ms default; a two-byte 400 ms payload went to 0x020519C3 -- the identical
arithmetic and identical frame shape that moves frames 0 through 6 on this same
fleet -- and the cadence read 250 ms before and 250 ms after, across a ten
second settling window. So `admin.LEGACY_FRAME_MAX` = 6 is a measured
bound. Note the scope, which is the same caution the burn taught: it settles the
write AS SENT, and whether some other mechanism reaches frame 7 is untested. The
obvious candidate is closed -- parameter 165 is Status 7 Period but sits above
`LEGACY_PARAM_MAX`, so it is not addressable on this generation either.

Reading 158 to 165 means addressing above the cap that `_legacy_param_arb` now
enforces, so it cannot be repeated through the driver as it stands. Those ids
land in apiClass 57 and 58, which frames 2.1.0 leaves empty, and every one
answered with a refusal status and no observed side effect. That is the evidence
they are inert, and it is not a licence to go higher: see the safety rule below.

### Writes are RAM only, and stay there unless they are burned

Proven, not assumed. Idle Mode was set to BRAKE on all eight and read back as
BRAKE. The motor rail was then cut and restored. All eight came back COAST,
which is the default REV's parameter table gives for Idle Mode
(`REV-SparkParameters-v0.1.2.md:73`), with sticky fault 0x0200, bit 9, hasReset,
confirming the cycle. The same cycle reverted every status period to REV's
pre-25 defaults.

Flash-resident provisioning is not affected: the drive P of 2.0, the steer P of
0.1 and the 40 A Smart Current Stall Limit survived both rail cycles. REV's
documented default for that parameter, id 59, is 80
(`REV-SparkParameters-v0.1.2.md:126`), so the 40 on this fleet is provisioning
that lives in flash and that no CAN write in this package put there. Only what
this package writes over CAN is volatile, and it stays volatile because this
package never burns.

A write on its own persists nothing, and until nothing on this
generation was known to. PERSIST_PARAMETERS is versionImplemented 25.0.0, so a
24.0.1 device does not carry it, and the pre-25 burn sent by hand with
an EMPTY payload, against a no-burn control, committed nothing across a rail
cycle. The frame was right and the payload was not. Api 0x072 carrying the
two-byte magic does commit the parameter table, measured on the
same fleet, and the next subsection is its wire contract.

So the operational rule splits in two. A PARAMETER can be made to survive a
power cycle -- write it, then burn it -- but NOT from here: this package does
not send the burn, so from the CLI a parameter write is still volatile.
Everything else is volatile on any route and has to be re-applied on every boot,
the status periods included, because the burn does not reach them either. A
power cycle is always the backstop for the volatile half.

The status periods have a command for that re-apply. `uv run spark throttle`
re-sends the pre-25 boot throttle on api class 6, then measures the cadence,
because the write is unacknowledged and the wire is the only read-back; it exits
1 naming any controller that did not take it (`cli.cmd_throttle`).
Parameter values have two commands on this generation. `spark params --set`
writes one parameter to one controller and says RAM ONLY in its own output.
`spark provision --id N --write` puts back every declared parameter that has
drifted, though the status periods stay out of reach here. Both leave the value
in RAM, and provision refuses `--persist` on pre-25 because PERSIST_PARAMETERS
is versionImplemented 25.0.0. So making a parameter survive a power cycle needs
a burn from outside this package, over USB-C or by hand on api 0x072. `spark
repair` is no route either, because `fault_frame` returns `provisioned: False`
on pre-25 and repair refuses before it writes anything.

### The burn: api 0x072, and what it commits

Added. Measured on rig-max, firmware 24.0.1, across ids 1 through 7
with a no-burn control on id 8. REV publish no pre-25 frame specification, so
what follows is the wire, read off the wire, exactly like the parameter api
above.

    arb      = 0x02051C80 | device_id          (api 0x072)
    payload  2 bytes: uint16 15011, LITTLE-ENDIAN, on the wire a3 3a
    reply    1 byte, on the REQUEST'S OWN arbitration id
             0x00 accepted     0xFF refused

There is no separate response id and no response frame to match up, which is the
same shape as api class 48. The magic is not a new constant. 15011 is
`admin.PERSIST_MAGIC`, the value the 25+ PERSIST_PARAMETERS
frame carries, and REV name the signal themselves: PERSIST_PARAMETERS in
`REV-spark-frames-2.1.0.json` is `lengthBytes` 2 with one signal, MAGIC_NUMBER,
uint, 16 bits, `isBigEndian` false, `decodedMin` == `decodedMax` == 15011. The
two dialects share the constant and the byte order across a frame REV never
documented for pre-25 at all.

Payload rules, measured on ids 1, 2, 5, 6 and 7 and identical on every one. Only
the first two bytes are read.

| payload | reply |
| --- | --- |
| the magic, little-endian, 2 bytes | 0x00 accepted |
| the magic then trailing bytes | 0x00 accepted |
| eight bytes of the magic repeated | 0x00 accepted |
| the magic BIG-endian | 0xFF refused |
| any other value; 0x0000 and 12345 were both tried | 0xFF refused |
| one byte | 0xFF refused |
| zero length, DLC 0 | NO REPLY AT ALL |

The zero-length row is the one to remember, because it is what the first run
sent. A DLC 0 burn draws no reply and commits nothing, and that silence read as
evidence the api did not exist. It is not: it is the firmware declining to parse
a payload it cannot read. Before the frame had never been sent at
all, which is why this tree recorded 0x072 as unverified and possibly
non-existent; the empty-payload run looked like confirmation, and the payload
sweep overturned both.

**What it commits: the parameter table, ids 0 through 133.** Proven with a
no-burn control, across two rail cycles. Ids 3 and 4 were written Idle Mode
COAST -> BRAKE and
burned, both replying 0x00, while id 8 was written the same value and not
burned. After a motor-rail cycle ids 3 and 4 read BRAKE and id 8 read COAST. The
control reverting is what makes it conclusive: the rail really dropped, a plain
write really is volatile, and only the burned pair survived. Both were then
written back to COAST and burned again, and that survived a further cycle.

**What it does NOT commit: the status periods.** Measured, not inferred. Id 3
had 0x060 set to 77 ms -- a value neither REV's pre-25 default of 10 nor this
package's boot throttle of 50 produces, so nothing else on the bus makes that
number -- and was burned with the magic and accepted 0x00. After a rail cycle it
read 10.0 ms, like all seven others. The periods move on api class 6,
`LEGACY_SET_PERIOD` as recorded earlier in this section, and are not part of
what 0x072 commits.

That split is the operationally important half. Configuration parameters on a
pre-25 MAX CAN be made to persist over CAN -- by the firmware, not by anything
in this package, which does not send the frame that does it. The status periods
cannot be persisted by any route, so
`uv run spark throttle` stays mandatory after every power event, and a
rail-cycled fleet sits at REV's cold defaults until something re-applies the
throttle. That state is the gs_usb wedge risk: the rail cycle recorded in
`max.param_writes_are_ram_only` took the wire from 788 frames/s at the boot
throttle to 1872 at REV's defaults, and gs_usb turns every CAN frame into a
transfer on a 12 Mbit full-speed USB link.

**This package does not send the burn.** Nothing in `sparklib/`
transmits api 0x072, and there is no `spark persist` path for this generation:
`SparkAdmin.persist` sends the 25+ PERSIST_PARAMETERS frame at `PERSIST`,
0x0205FFC0, which 24.0.1 does not carry, and `cmd_persist` now refuses on this
generation before sending anything at all. 0x02051C80 stays denied to the fault
injector in `FORBIDDEN_BASES` (`tests/support/sparkhw/wire.py`), for the reason
the denial gives: a payload-carrying burn is the one frame that must not go out
by accident, because it is the only thing here that spends a flash cycle. The
one place in the tree that emits it is
`tests/hardware/test_legacy_burn_flash.py`, which stages arm and verify around
an operator's rail cycle behind `SPARK_HW_BURN_FLASH=1`.

Two things this does not settle. Whether burning as routine practice is WISE is
a separate question, and flash endurance is finite; nothing here has measured
it. And the api NUMBER's original source is still uncited in this tree. What is
settled is the behaviour, measured directly.

### SAFETY: never address above parameter 133

`LEGACY_PARAM_MAX` is 133 and `SparkAdmin._legacy_param_arb` raises rather than
transmit past it. Do not raise that cap, and do not sweep past it "to see what
answers".

The reason is arithmetic and can be checked against the vendored spec with no
hardware at all. Put 255 through the same formula:

    (0x300 | 255) << 6  ->  0x0205FFC0

0x0205FFC0 is PERSIST_PARAMETERS in frames 2.1.0, apiClass 63, apiIndex 15. The
command space sits at the top of the very api range the parameter table starts
in: id 255 lands exactly on persist. Note the scoping, because it cuts both ways.
PERSIST_PARAMETERS is versionImplemented 25.0.0, so a pre-25 controller does not
carry it and a sweep there cannot reach it. On a 25+ bus, where a five-byte
payload is a write, a sweep that runs to 255 finishes by committing whatever is
in RAM to flash on every controller it reaches. Either way the cap stands: ids
above the table address frames nobody has characterised.

Persist is the one collision this file can show, and it is enough. Ids 134 to
254 climb through apiClass 56 to 63, and frames 2.1.0 defines no frame at all in
apiClass 48 through 62 and only PERSIST_PARAMETERS in 63. Uncharacterised is not
the same as safe.

One correction to the reason the cap is usually given. The driver's own refusal
message and several docs used to say "242 is burn-flash and 255 is persist". The
255 half is exact and the 242 half does not check out, so the 242 leg has been
removed from the driver, the defaults file and the guides. For the record: `0x300 | 242` is `0x3F2`, apiClass 63 index 2, arbId 0x0205FC80, and
frames 2.1.0 defines nothing there. Pre-25 burn-flash is api 0x072, arbId
0x02051C80 (`FORBIDDEN_BASES` in `tests/support/sparkhw/wire.py`), which is below
0x300 and so is not reachable through the parameter formula at any id. The cap
does not need that leg and should not be defended with it.

Restated, because one half of that has changed, and it strengthens
the cap rather than weakening it. 0x072 is no longer a rumoured number: it is a
measured, working burn-flash command on 24.0.1 that commits the parameter table
when it is given the magic. It stays unreachable through the parameter formula,
which is the only thing the safety rule needed from it. What it adds is a worked
example of the reasoning error the cap guards against. Arb 0x02051C80 is
apiClass 7 apiIndex 2, and frames 2.1.0 defines nothing there -- the class holds
IDENTIFY_UNIQUE_SPARK at index 6 and IDENTIFY at index 7 and no index 2 -- and
the frame answers on this fleet anyway. So "frames 2.1.0 defines no frame at all
in apiClass 48 through 62" is not evidence that a pre-25 controller has nothing
there. "Uncharacterised is not the same as safe" now has a live command behind
it, and that command writes flash.

The measured table stops at 133 for the same reason the cap does. REV's SPARK
MAX parameter page also stops at 133, as section 2 records, which is an
independent agreement about where the table ends. But the cap is not an estimate
of the table's end. It is a guard on where the commands begin.

### What this rests on

REV has never published a pre-25 CAN frame specification. There is no vendor
document behind any of the above. `reference/README.md` lists what IS
vendored here: frames 2.1.0, parameters v0.1.2, and REVLib 2025.0.3 and 2026.0.2
headers. Every one of those is 25+. No REVLib 2024.x header is in this tree, so
nothing here can be diffed against the library that spoke this dialect.

So this section rests on measurement, plus the api-class layout older REVLib
used, which is where the 0x300 base and the type tags come from. It is not a
reading of a REV specification and must not be cited as one. Two things in this
tree corroborate it independently, both of them constants that only REV could
have set. The arithmetic above: parameter id 255 lands exactly on a frame REV
does document, at the exact arbId REV gives it, which is not what a made-up
formula does. And, the burn's magic: the pre-25 command takes
15011 little-endian and refuses every other value and the reversed byte order,
and 15011 little-endian is precisely the MAGIC_NUMBER signal REV specify on the
25+ PERSIST_PARAMETERS frame. Two dialects agreeing on a constant that appears
nowhere else is not a coincidence a guess produces.

`docs/runs/rig-flex-probe-log.md:11-12` records the same api
probed on rig-flex and answering nothing. The line reads "Parameter access at api
0x300|param: NO REPLY for params 0-15, with DLC 0, DLC 1, DLC 2, DLC 4 and RTR",
on SPARK Flex firmware 26.1.6. Read it with the finding in hand,
because none of those five forms was a remote frame carrying dlc 8. That is the
form the 25+ read classes answer, and a data frame or a dlc 0 remote frame draws
silence. So this null covers only what was varied, and api class 48 on 26.1.6
stays untried in the form that answers.

### The inversion, and how it resolved

Most of this package was built around a controller believed unable to be asked.
SPARK Flex on 26.1.6 answered no parameter read in the form the package was
sending, so the audit, the sweep and the repair paths were shaped to reason from
broadcasts instead. That belief was about the FRAME FORM, not the firmware: the
reads were going out as zero-length data frames where the spec marks them RTR.

Asked properly, a Flex answers. A remote frame carrying dlc 8 draws a reply on
all eight documented read classes, covering parameters 0 through 255, on every
controller. The inference paths remain because they are useful when a controller
is silent for other reasons, and they are no longer the only route.

A pre-25 SPARK MAX reaches the same place by a different dialect. Its
configuration is read one parameter at a time on api class 48, over ids 0 to 133,
and written back with the write echoing the value taken. So both generations can
be asked, and the difference is which frames to send. Anything in this repo
phrased as "a SPARK cannot be read over CAN" predates the frame-form fix and is
wrong as written.

---

## 3d. The pre-25 identity frames: fingerprint, identify, set-id

Added. Measured on rig-max: eight SPARK MAX, firmware 24.0.1,
generation `pre25`, on a 1 Mbit gs_usb adapter named `can0`.

Three positions this tree held are overturned here. A pre-25 controller has no
readable identity; identify does not work on it; and set-id cannot be aimed at
one. Each is wrong, and each is wrong in a different way, so they are taken one
at a time.

### The fingerprint: api 0x094, a zero-length ADDRESSED request

    arb      = 0x02050000 | (0x094 << 6) | device_id      (0x02052500 | id)
    request  DLC 0, no payload
    reply    4 bytes, on the SAME arbitration id

Read on all eight of rig-max: 66d3bde5, 481f7cb5, eabe4c2e, a7a98e23, 249eb2b0,
44e08e6a, 5945a484, 10487c0d. Eight distinct values, four bytes each, stable
across a two second gap, no bit fixed across the fleet, and not derived from the
CAN id.

Note ADDRESSED, and note that the request carries the device id rather than a
serial. Nothing is broadcast on this generation; a controller answers because it
was asked. That is not a detail of convenience -- it is the whole reason
duplicate detection works here, and the last part of this section is why.

The request and its reply share an arbitration id, the same shape api class 48
and the burn both have, so DLC is what separates them: the request is the empty
one. `SparkAdmin.read_fingerprint` discriminates on frame LENGTH for that reason
and NOT on `is_rx`. SocketCAN flags every locally generated frame as loopback,
so an `is_rx` filter throws away an injected reply as readily as it throws away
the driver's own echo -- measured on rig-max, 124 injected frames, every one with
`is_rx` False.

Where it sits. apiClass 9, index 4. Frames 2.1.0 defines index 3 (LED_SYNC),
index 5 (SET_CAN_ID) and index 8 (GET_FIRMWARE_VERSION) in that class and
nothing at index 4, so this is one more api the 25+ spec leaves empty and pre-25
firmware answers on, alongside api class 48 and api 0x072. It was found by
sweeping zero-length requests across api 0x000-0x2FF on two controllers and
keeping the apis that answered with per-device-different payloads: eight
answered, three differed per device, and two of those were explained -- 0x071
carries the CAN id, and 0x0C1 is a counter, five of whose eight values moved
within two seconds. The same sweep returned the correct firmware payload at
0x098, which corroborates the api decoding rather than just the arithmetic. It
is also the sweep that cleared two controllers' sticky faults; section 3e is
that story.

Two write attempts were refused. DEADBEEF was sent to the same arbitration id as
four bytes and as eight; neither drew a reply, nothing changed, and a full
134-parameter snapshot either side was identical. That establishes read-only IN
THE TWO SHAPES TRIED and nothing more. A magic-guarded write could exist,
exactly as one does for the burn at api 0x072, so do not record the register as
immutable.

**What to call it.** It IS the firmware's Unique ID -- set-id below accepts it
as one, which is the evidence. It is NOT "the REV serial". REV Hardware Client
does not display this value, and pre-25 identify takes no serial at all, so
nothing measured here connects it to a number an operator can read off REV's own
tool. Fingerprint, or the Unique ID. Both are accurate; "the REV serial" is not.

### Identify: IDENTIFY_UNIQUE | dev, with an EMPTY payload

    firmware 25+   arb = IDENTIFY_UNIQUE, device 0, payload 4-byte serial
    pre-25         arb = IDENTIFY_UNIQUE | device_id, payload EMPTY, DLC 0

One api, two addressing models. REV's frame is IDENTIFY_UNIQUE_SPARK, apiClass 7
index 6, versionImplemented 1.5.0, arbitration base 0x02051D80, and the spec
gives it lengthBytes 4 with a single UNIQUE_ID signal -- which is the 25+ form.
A 24.0.1 controller ignores that form in silence. That is why `spark identify`
used to report that it had sent and no LED ever blinked: the frame went out,
matched nothing, and was dropped, and on a generation where nothing is
acknowledged a dropped frame and a successful one look identical.

Recovered by capturing REV Hardware Client rather than by guessing. An operator
pressed RHC's LED button for controllers 1, 2 and 3, and 9831 lines of candump
held exactly three frames that could be it: 02051D81, 02051D82 and 02051D83, all
DLC 0, one per controller blinked. Reproducing those three from this driver
blinked the same three controllers, confirmed by an operator watching them.
`SparkAdmin.identify_by_id` sends that form, `spark identify --id N` reaches it,
and `tools/spark_blink.py` is a standalone tester for it.

Fire and forget, like every pre-25 command: nothing is acknowledged, so the only
confirmation is an operator watching an LED.

The empty payload matters beyond the blink. The pre-25 form takes NO serial, so
it cannot be used to ask a controller whether the four bytes at api 0x094 are
the value it identifies by. Those two questions are unrelated on this
generation, which is why the fingerprint had to be confirmed through set-id
instead.

### SET_CAN_ID takes the fingerprint as its Unique ID, and does not persist

    arb      = SET_CAN_ID | current_id                    (0x02052540 | id)
    payload  5 bytes: [Unique ID, uint32, little-endian][new CAN ID, uint8]

This one IS in the vendored spec: apiClass 9 index 5, versionImplemented 1.5.0,
lengthBytes 5, signals UNIQUE_ID at bit 0 for 32 bits and CAN_ID at bit 32 for 8
bits, both little-endian. 1.5.0 is far below 25.0.0, so unlike the parameter
frames a controller on 24.0.1 carries it, and the standard form is the one to
send.

Measured on rig-max: SET_CAN_ID addressed to id 3, carrying that controller's own
fingerprint and a new id, moved it to id 20 -- located there by its fingerprint
-- and a second frame moved it back. So the four bytes at api 0x094 are not
merely a stable per-device value. They are the identifier the firmware itself
accepts for addressing, and that is what makes "Unique ID" the right name.

**On this generation it is RAM ONLY.** `SparkAdmin.set_can_id` finishes by
calling `SparkAdmin.persist`, which sends the 25+ PERSIST_PARAMETERS frame that
24.0.1 does not carry, so the move is never committed and reverts at the next
power cycle. `spark set-id` prints MOVED, RAM ONLY and exits 1 rather than
reporting success. The firmware's own burn at api 0x072 would commit it and this
package does not send that -- see 3c. An id that has to survive a rail cycle is
set over USB-C in REV Hardware Client.

### What the fingerprint already solves: duplicate CAN ids

This is what the identity work was for. Two controllers on one id are invisible
to an id scan, because they look like one controller, and every addressed write
reaches both while only the first reply is read. On 25+ the tell is two
different UNIQUE_ID broadcasts on one id. Pre-25 broadcasts no identity at all,
which is why the check was believed impossible there.

Requesting rather than listening is the whole difference. One request to a
shared id draws one reply per controller sitting on it, so two answers to one
question is the same tell by another mechanism. `SparkAdmin.duplicates` routes
to `_duplicates_pre25` on this generation, and
`admin.duplicate_detection_available` returns True on both generations
now.

Verified on hardware, and verified the awkward way: by INJECTING a second reply
on the fingerprint arbitration id rather than by moving a real controller onto a
shared id. Clean before, detected during, clean after, and no controller
changed. The injection is also what exposed the `is_rx` filter described above,
which had been dropping all 124 injected replies -- a defect this path could not
have shown any other way, because every controller on the fleet has its own id
and so the code never met a second answer.

Swap detection follows from the same value and has been run.
`spark learn-serials` wrote the eight fingerprints into
`spark.yaml` under `serials`, leaving a `.bak` beside it, and detection was tested in both
directions. The block is labelled fingerprints in that file, for the naming
reason above.

---

## 3e. Payload length is part of the frame, and DLC 0 is not inert

Added. This is the one generalisation the day's measurements support,
and it earns a section of its own because getting it backwards erased data.

Pre-25 reuses arbitration ids and separates meanings by DLC. Every case measured
so far:

| api | zero length means | a payload means |
| --- | --- | --- |
| 0x300 \| pid, class 48 | read the parameter | 5 bytes writes it |
| 0x094, fingerprint | request the four bytes | refused in both shapes tried |
| 0x072, burn flash | no reply, commits nothing | the 2-byte magic commits the table |
| 0x076, identify | BLINKS THE LED | the 4-byte serial is the 25+ form, ignored here |
| 0x06E, CLEAR_FAULTS | CLEARS THE STICKY WORD | -- |

Api class 6 is the same idea without a zero-length case: the period write and
the device's own status broadcast share an arbitration id, and two bytes against
eight is what tells them apart.

The last two rows are the point. **A zero-length frame is not a query.** On this
firmware it can be a command that acts, and for CLEAR_FAULTS REV's own spec says
so -- apiClass 6 index 14, lengthBytes 0, versionImplemented 1.0.0, in the file
vendored in this tree the whole time.

That was learned by erasing something. The api sweep described in 3d sent
zero-length requests across api 0x000-0x2FF on two controllers, justified on the
grounds that zero length is request semantics on this firmware. That belief came
from one data point: api 0x072, where DLC 0 drew no reply and committed nothing
while the same api with its magic replied and committed. One frame was
generalised to a command space. The sweep cleared the sticky fault word on both
controllers it touched -- ids 3 and 4 read empty afterwards while the other six
still carried canTx, canRx and hasReset -- and the api that did it was 0x06E.

The damage was bounded by the before-and-after snapshot, not by the exclusion
list. A full parameter snapshot and a sticky-fault comparison were taken either
side, and the bits that were erased had already been recorded, as
`pre25.fault_bits_cantx_canrx`, so nothing was lost that was not already
written down.

The exclusion list is the lesson. It was taken from `FORBIDDEN_BASES` in
`tests/support/sparkhw/wire.py`, which is the INJECTOR denylist: frames a test
may not TRANSMIT because a device would act on them. CLEAR_FAULTS is
legitimately absent from it, because this package sends it on purpose --
`spark clear` is a supported command and `SparkAdmin.clear_faults` is how it
does it. The list was
answering a different question from the one the sweep asked of it, and reusing a
list built for one purpose as the safety boundary for another is what let it
through.

So the rule for any future sweep: exclude every frame with COMMAND semantics
regardless of payload length, derive the exclusions from the frame spec rather
than from an injector denylist, and keep the before-and-after snapshot that
caught this one.

---

## 4. What the driver got wrong, and what is left

First recorded, when most of these were fixed. The list has been
revisited since and now carries several dates: each entry says what replaced it
and when, and item 3 is still open.

1. FIXED. `API_SETS` split on product. It now keys on `GEN_PRE25` / `GEN_FW25`.
2. FIXED. `fault_frame()` returned STATUS_0 for a MAX. It now takes a firmware
   generation, and on 25+ returns STATUS_1 for every SPARK.
3. PARTLY WRONG AS WRITTEN. `API_LEGACY_STATUS_1` (0x061) and
   `API_LEGACY_STATUS_2` (0x062) are absent from frames 2.1.0 because that file
   describes a 25+ device. They are REAL pre-25 frames: REV's SPARK MAX
   control-interfaces page documents Periodic Status 1 as velocity, temperature,
   voltage and current. They are kept, scoped to pre-25.
4. FIXED, and widened. `read_param` used the write api and is
   deleted. `read_param_pair` covers apiClasses 15-22 and every parameter
   0-255, `param_types` covers apiClass 13, and both go out as remote frames
   with dlc 8.
5. WRONG AS WRITTEN, corrected and closed. It said
   apiClass 19 covers 128-159 only, so the write-api read was unavoidable. The
   documented read frames cover 0-255 across apiClass 15-22; see the correction
   in section 3. The driver implements all eight classes now and has no
   fallback, because `read_param` is deleted. On a pre-25 device neither api
   exists, and the answer there is api class 48, section 3c.
6. FIXED. `spark repair` no longer refuses on product. It listens, takes the
   dominant firmware generation, and refuses when that generation has no
   verified period parameter. The separate question it raised -- whether the
   FIRMWARE honours the write -- was settled on rig-max and is no
   longer open: 25 ms was asked for on STATUS_0 through
   `set_legacy_status_period` and 25 ms was measured on the wire, reproduced on
   ids 1 and 4, while PARAMETER_WRITE for parameter 158 in the same run drew no
   response at all. `max.firmware_honours_period_write` in
   `provenance.py`, HARDWARE. It is scoped to status periods on api class
   6. No pre-25 PARAMETER had been written by this package when that claim was
   recorded; api class 48 answers that half, section 3c.
7. WRONG AS WRITTEN, corrected. It said
   `the sparkflex: block` carries Status 0-7 Period only, and that
   Status 8 (199) and Status 9 (224) were missing from the provisioned baseline.
   The file carries all ten. Status 8 Period is in
   `the `sparkflex:` block of spark.yaml` with
   `param_id: 199` and Status 9 Period beside it with `param_id: 224`, which
   matches the ten frames Appendix A of the assembly manual configures. Its
   Status 3, 5, 6 and 7 deviations were checked against the file and are
   correct.

Found while fixing the above, and not in the original list:

8. FIXED. On firmware 25+, LEGACY_STATUS_0 is pinned to a constant -- applied
   output 0, FAULTS_AND_STICKY_FAULTS 0xFFFFFFFF, other signals 0. The old
   decode scored those bits, so a healthy 25+ SPARK MAX read as eight faults and
   eight sticky faults on every frame. See
   `docs/runs/legacy-beacon.md`.
9. FIXED, and NARROWED. A pre-25 bus broadcasts no UNIQUE_ID, so
   every controller read serial `None` and the audit reported all of them as
   swapped. The broadcast half is still true and always will be. What was wrong
   was the conclusion drawn from it: identity is REQUESTED on this generation,
   at api 0x094, so `inventory(with_fingerprint=True)` fills the field the
   audit needs. `spark learn-serials` has been run on rig-max and
   `serials` in its config carries the eight fingerprints. Section 3d.

Found. Item 10 closed; item 11 has since closed in
part:

10. CLOSED. The 16-frame undercount was copied into
    `admin.READ_PAIR_FIRST_ID`, `sparksim.frames.READ_PAIR_FIRST_ID` and
    `tests/adversarial/test_param_read_frames.py`. Driver, simulator and test
    agreed with each other and disagreed with the spec, which is the exact shape
    the simulator exists to catch, and it did not catch it, because it was
    written from the same wrong reading of the same file rather than from the
    file. All three now address the full 128-frame run over parameters 0-255
    through one base constant, and the driver was checked against the vendored
    spec exhaustively: `0x02053C00 + ((param_id // 2) << 6)` reproduces every
    spec arbId with no mismatch. The simulator checks the frame FORM as well as
    the id, so a regression to a data frame fails the suite.
11. CLOSED for reading, and closed for the audit. When
    this was written `read_legacy_param` and `write_legacy_param` were the only
    things in the package that spoke the pre-25 parameter api, and no command
    called them. `spark params` now does, through `cmd_params` and
    `_params_write`, and it reads both generations -- 0-133 over api class 48,
    0-255 over READ_PARAMETER. `--set` writes one parameter to one controller
    and prints RAM ONLY. It has been run on rig-max, over all 134 ids on all
    eight controllers. Exactly one parameter differed across the fleet, id 13
    (P 0), split by role. `spark audit` now reads the declared deviations off
    the controller on both generations, through `declared_rows`. Pre-25 keeps
    one gap, because the status periods are not parameters there, so 158-165
    are still judged from the broadcast cadence. `spark repair` writes Status 1
    Period only and refuses a pre-25 bus outright. `spark provision` restores
    the rest of the declared table, and it takes the write dialect from the
    target's own reading. On pre-25 it strands the periods and writes to RAM
    only.

The first is a decision still to make. The rest are fixed:

12. OPEN, as a decision rather than a defect. The pre-25 burn flash works and
    this package does not send it. Api 0x072 with the two-byte magic commits the
    parameter table on 24.0.1, so a MAX's configuration CAN be made to persist
    over CAN, and nothing in `sparklib/` emits the frame. Whether it
    ever should is the open part, and flash endurance is the reason it is not
    obvious. Until that is decided, nothing in this package persists anything on
    this generation, and no document here should be written as though it does.
    The status periods are outside what 0x072 commits either way, so `spark
    throttle` stays mandatory whatever is decided.
13. FIXED. `spark persist` used to be a false success on pre-25.
    `cmd_persist` called `SparkAdmin.persist` with no generation check; the 25+
    PERSIST_PARAMETERS frame is dropped in silence by 24.0.1, and the command
    then fell through to a wire check that compares the measured status period
    against `expected_ms` -- which on this generation is REV's own 10 ms default,
    so a cold bus satisfied it and the command reported that the burn had landed.
    It now reads the generation first and REFUSES before sending anything,
    naming api 0x072 as the firmware's own burn and saying that this package
    does not expose it.
14. FIXED. `spark identify` sent the firmware-25 form -- broadcast on
    device 0 with a 4-byte serial -- at a 24.0.1 fleet, which ignores it, and
    reported that it had sent. Pre-25 identify is IDENTIFY_UNIQUE | dev with an
    EMPTY payload, recovered from a REV Hardware Client capture and reproduced
    against an operator's eyes. `spark identify --id N` now works. Section 3d.
15. PARTLY FIXED, and the remainder is a hardware limit rather than a
    defect. `spark set-id` was unusable on pre-25 because it addresses by serial
    and no serial was thought to exist. It works now, using the standard
    SET_CAN_ID frame with the fingerprint as the Unique ID, and the move was
    measured both directions on rig-max. But it is RAM ONLY on this generation --
    `set_can_id` finishes with PERSIST_PARAMETERS, which 24.0.1 does not carry
    -- so the command says MOVED, RAM ONLY and exits 1 instead of claiming
    success. Setting an id that must survive a rail cycle is a USB-C job.
16. FIXED. `duplicate_detection_available` returned False on pre-25,
    because a duplicate was found from two differing UNIQUE_ID broadcasts and
    this generation broadcasts none. It returns True on both generations now:
    the fingerprint is requested per id, and two controllers on one id both
    answer. Verified on hardware by injecting a second reply, not by moving a
    real controller. Section 3d.

---

## 5. What only hardware can settle

Ordered by how much rests on the answer.

| question | how | why it matters |
| --- | --- | --- |
| what firmware are the MAX rigs on? | `uv run spark status` -- it reads GET_FIRMWARE_VERSION | ANSWERED for rig-max: 24.0.1 on all eight, generation pre25. rig-max-2 is still unmeasured |
| does a MAX on 25+ broadcast 0x2E0/0x2E1? | needs a MAX that has been updated past 25.0.0; rig-max's eight are all 24.0.1 and cannot answer it | the driver already keys on generation rather than product (`API_SETS`), so this would confirm that split rather than change it |
| is the period unit ms or us, on 25+? | read 159 back after writing 20, and time the wire | ANSWERED on rig-flex: MILLISECONDS, by a control-group write of 159 = 50 on id 11 alone while seven controllers held 20.0 ms. `both.status_period_unit`, HARDWARE. Never answerable on rig-max: on pre-25 the periods are not parameters, they move on api class 6 in ms and 158-165 refuse |
| does a MAX on 25+ answer READ_PARAMETER pairs? | the sweep, which reaches all eight read classes now | REV says a named prerelease did not. rig-max is 24.0.1 and answers on api class 48 instead, and rig-max-2 is unmeasured. The pre-25 half rested on versionImplemented and on four sends whose forms are now known-silent, and the row below has since varied the form axis and closed it |
| does a PRE-25 device answer READ_PARAMETER as a remote frame with dlc 8? | `uv run python tools/spark_read_frame_form.py --id 3` AT rig-max, which varies all three forms | ANSWERED on rig-max, after a rail cycle: NO, in any form. READ_PARAMETER class 19 and GET_PARAMETER_TYPES class 13 each went to id 3 as a zero-length data frame, as a remote frame with dlc 0, and as a remote frame with dlc 8. All six drew silence, so the generation split is measured across the form axis rather than inferred from two silent-form sends. `both.param_read_frames`, HARDWARE, `records/rig-max-post-rail-cycle-20260910.log` |
| does 26.1.6 Flex answer parameter reads? | same, on rig-flex | ANSWERED on rig-flex: YES, on all of them. Every parameter 0-255 answers over apiClasses 15-22, and all sixteen apiClass 13 type frames answer, on every one of the eight controllers. The answer to this row was NO and it was wrong; the row below says why |
|...and were those reads sent in the form the spec asks for? | on rig-flex, send one READ_PARAMETER as a genuine remote frame for a parameter whose value is known | ANSWERED on rig-flex, and it OVERTURNS the row above. They were not. The reads went out as zero-length DATA frames, so that run varied the api and never the frame form, and a null result covers only what was varied. A remote frame with `dlc=0` is silent too, which this row predicted would work. Only `is_remote_frame=True` with `dlc=8` answers. `flex.param_reads_were_probed_as_data_frames`, HARDWARE, and `docs/runs/rig-flex-parameter-reads.md` |
| does a 25+ SPARK emit 0x060 at all? | candump | rig-flex's capture contains none, so the beacon is documented and unobserved |
| is there any per-device identity on a pre-25 bus? | sweep zero-length requests across the api space on two controllers and keep the apis whose replies differ per device | ANSWERED on rig-max: YES. Four read-only bytes at api 0x094, distinct on all eight, requested rather than broadcast. `pre25.serial_is_readable_at_api_0x094`, HARDWARE. Call it the fingerprint or the Unique ID, not the REV serial |
| does identify work on pre-25? | capture REV Hardware Client's LED button and reproduce what it sends | ANSWERED on rig-max: YES, as IDENTIFY_UNIQUE \| dev with an empty payload. The 25+ broadcast-plus-serial form is silently ignored on 24.0.1, which is why the command looked like it worked. Confirmed by an operator watching the LED |
| can a pre-25 CAN id be changed over CAN? | send SET_CAN_ID with the fingerprint as the Unique ID, then find the controller at the new id by fingerprint | ANSWERED on rig-max: YES, id 3 to 20 and back. But RAM ONLY -- the move is not burned, because PERSIST_PARAMETERS does not exist on 24.0.1 and this package does not send api 0x072 |
| is there a magic-guarded WRITE to the fingerprint register? | OPEN. Two payload shapes were refused in silence and that is all that is known | decides whether the Unique ID can be forged or cleared by accident. Until it is answered, do not record api 0x094 as immutable |
| does api class 6 reach status frame 7? | write a distinctive period to 0x067 and time the cadence either side | ANSWERED on rig-max: NO. 250 ms before and after a 400 ms write, so `LEGACY_FRAME_MAX` = 6 is measured. Scoped to the write AS SENT; parameter 165 is not a way round it, since it sits above `LEGACY_PARAM_MAX` |
| what does SPARK_MODEL read as? | STATUS_0 bit 54, 4 bits | lets the driver stop trusting config for product |
| does a MAX on 25+ answer the pre-25 api at all? | read parameter 0 on api class 48 against a 25+ MAX | decides whether api class 48 is a pre-25 dialect or a MAX dialect. rig-max is 24.0.1, so it cannot separate the two, and every claim in 3c is scoped to pre-25 for that reason |
| does pre-25 burn-flash, api 0x072, commit anything? | write a parameter, send 0x072, cycle the rail against a no-burn control | ANSWERED on rig-max: YES for the parameter table, ids 0-133, with a two-byte payload carrying 15011 little-endian, and NO for the status periods, both measured with controls. `pre25.burn_flash_api`, HARDWARE. Still open: whether routine burning is wise, since flash cycles are finite, and where the api number came from |

---

## 6. Provenance of this document

Every claim above was read from the named file during the session,
not recalled. The workflow journals and the session transcript from the
adversarial pass that produced most of it are not in this tree; what survives of
that pass here is the run notes under `docs/runs/`. Six evidence probes
ran, each checked by three independent refuters; **every one of the six probes
was refuted by a majority**, in each case for conceding that something was
unknown when the primary source in this tree settles it. That pattern is the
lesson: the corpus was searched, the answer was drawn from the wrong file in it,
and the confident summary that resulted was wrong.

Amended. The frame counts in section 3 were recounted from
`reference/REV-spark-frames-2.1.0.json` in this tree, by grouping every
entry of `nonPeriodicFrames` on its own `apiClass` field and counting, not by
reading example lines. Section 3c is different in kind: it is hardware
measurement on rig-max, not a reading of any file here, and it says so in its own
"What this rests on". Its claims are recorded as
`max.param_access_answers_both_ways` and `max.param_writes_are_ram_only` in
`sparklib/provenance.py`, both marked HARDWARE, and the
sticky-0x0200 evidence for the rail cycles is `pre25.fault_bit_hasreset`.

The correction carries the same lesson this section already records, one step
further on. The pass overruled a correct earlier draft, then wrote
the overrule down as settled and cited two example lines as the proof. The
primary source was in the tree the whole time, and counting it takes one pass
over one file.

Amended again. The burn subsection in 3c is hardware measurement on
rig-max, like the rest of 3c, and its claim is `pre25.burn_flash_api` in
`sparklib/provenance.py`, marked HARDWARE. Read that claim
rather than this summary: it carries all three runs, including the empty-payload
run that committed nothing and the 77 ms status-period run that did not survive
a rail cycle. The one thing in the vendored files that bears on it is the
MAGIC_NUMBER signal REV specify on PERSIST_PARAMETERS, which is where the
constant comes from.

The lesson is a different one from the recount. Api 0x072 stood in this tree as
unverified, never sent and possibly non-existent, and the first run that did
send it used an empty payload, drew no reply, and read as confirmation of all
three. The frame was right and the payload was wrong. "No answer" was taken for
"no such command" when it meant "a payload the firmware will not parse", and
separating the two took one run with a payload sweep and a no-burn control
beside it.

Sections 3d and 3e are hardware measurement on the MAX rig, and their
claims are `pre25.serial_is_readable_at_api_0x094` and
`pre25.zero_length_frames_can_still_act` in
`sparklib/provenance.py`, both marked HARDWARE. Read those
rather than this summary; they carry the scope each finding is limited to, and
neither says more than the wire showed. The measured frame-7 bound now in 3c is
`pre25.status_frame_7_period_is_not_writable`, also HARDWARE.

Two things qualify how 3d may be read. The claim
`pre25.no_serial_exists_in_the_parameter_table` is exhaustive and still true --
nothing in ids 0 through 133 is a per-device
identity except the CAN id -- which is precisely why the fingerprint had to be
found somewhere that is not the parameter table, and it is not contradicted by
finding one at api 0x094. And the only vendored file that bears on any of this
is REV's SET_CAN_ID frame, whose UNIQUE_ID signal is what makes "Unique ID" a
defensible name for four bytes REV Hardware Client never shows. Nothing else in
3d has a vendor document behind it.

The RTR subsection in section 3 began as a third kind again. On it
was the vendored spec's `rtr` field read against this package's own send calls,
with no hardware involved, and rig-flex was not attached. Hardware settled it the
next day, so `flex.param_reads_were_probed_as_data_frames` is marked HARDWARE
and it overturned the result it had only questioned. The prediction was half
right, because a remote frame is necessary and the form that answered also
carried dlc 8. The run is
`docs/runs/rig-flex-parameter-reads.md`, and
`flex.no_param_reads_on_any_api` now covers the one-byte read on
PARAMETER_WRITE alone.

Two failures of reasoning are worth naming here, because they are the same
failure twice. Absence from the parameter table was read as absence from the
device, so a value the firmware holds and will act on went unlooked-for; and
silence in response to a wrongly-shaped frame was read as the capability not
existing, which is exactly what happened with the burn's empty payload and again
with identify's 25+ addressing. In each case a correctly-formed frame answered
on the first attempt. Before recording a pre-25 capability as absent, check that
the frame that would exercise it was sent in the form this firmware takes.

---

## 7. How much of the fetched corpus this rests on

Counted. The harvest is much larger than what has been read:

    Chief Delphi threads fetched      1441      cited in curated docs    163
    REV / WPILib pages fetched        1040      distinct REV urls cited    8
    REVLib headers fetched             156      cited                      2

So roughly a ninth of the forum corpus and under one percent of the fetched
documentation has been processed. Absence of a fact from these documents is NOT
evidence it is absent from the corpus.

Two facts found by re-reading pages that had been fetched weeks
earlier and never opened, both of which changed conclusions:

- The SPARK Flex control-interfaces page documents no frames at all, which is
  why the only frame table REV publishes is a pre-25 SPARK MAX one. That is the
  likely origin of the product axis.
- REVLib's changelog, Version 2025.0.0: "Requires non-prerelease versions of
  SPARK and Servo Hub firmware v25.0.0 or higher" -- one SPARK firmware line,
  which is a third independent source for the firmware axis.

The 1040 fetched pages and 156 headers are small and structured, so a targeted
sweep of them is cheap and needs no hardware. The 25 MB forum corpus is the
expensive one. Do the cheap one first.
