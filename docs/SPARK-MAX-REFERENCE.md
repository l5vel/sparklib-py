# SPARK MAX on pre-25 firmware: configuration and debugging

The per-product reference for a SPARK MAX running firmware older than 25.0.0.
Read the [README](../README.md) first for the install, the udev names and the
CAN bring-up, which are identical for both products.
[CAN-SETUP.md](CAN-SETUP.md) covers bus recovery, and
[SPARK-MAX-BRINGUP.md](SPARK-MAX-BRINGUP.md) covers first power-on on a new MAX
robot.

Everything below was measured on one rig: eight SPARK MAX controllers on `can0`
behind a gs_usb adapter at 1 Mbit, all eight reporting firmware **24.0.1**,
generation `pre25`.

Code is cited by file and symbol rather than by line, because those two modules
are under active change.

## 1. The distinction this document turns on

The frame set keys on **firmware generation**, never on the product. A SPARK MAX
updated to 25.0.0 broadcasts and is written exactly like a SPARK Flex, and
nothing in this file applies to it. `normalise_generation` in `sparklib/admin.py` refuses a product name outright
instead of resolving one to a default. That substitution has already been made
three times here, and each time it cost a debugging session.

| | this document | the [README](../README.md) and most of the rest of the repo |
| --- | --- | --- |
| generation | `GEN_PRE25` | `GEN_FW25` |
| firmware | below 25.0.0. rig-max is 24.0.1 | 25.0.0 and later. rig-flex is 26.1.6 |
| status frames | `0x060`, `0x061`, `0x062`, `0x063`, `0x065`, `0x066`, `0x067` | `0x2E0`, `0x2E1`, `0x2F0` |
| faults live in | `0x060` bytes 2:6, one 32-bit word | `0x2E1` (STATUS_1) |
| identity | no broadcast. A per-device fingerprint is REQUESTED at api 0x094 | `UNIQUE_ID_BROADCAST` arrives unasked on `0x2F0` |
| parameter reads | **0-133 answer**, api class 48, one id per arbitration id | **0-255 answer**, READ_PARAMETER, remote frame with dlc 8 |
| parameter writes | legacy api, RAM until burned with api 0x072 | `PARAMETER_WRITE`, persistable |

The api sets are declared at `API_SETS` in `sparklib/admin.py`.

## Scope: what this tree assumes about the fleet

**Within sparklib as it stands, every SPARK MAX is pre-25 and every SPARK
Flex is firmware 25 or later.** rig-max is eight MAX on 24.0.1 and rig-flex is eight
Flex on 26.1.6. No robot here mixes them.

That is a fact about this fleet, not about the protocol, and the difference
matters. Frame layout, the fault word, the parameter dialect, persistence and
identify all key on FIRMWARE GENERATION. A SPARK MAX updated to 25.0.0 broadcasts
and is written exactly like a Flex. Conflating product with generation has gone
wrong here before, most expensively when a MAX layout was applied to a Flex and a
healthy 13.7 V rail decoded as a fault bitfield, which wedged a base.

So the code keeps reading the generation off the wire, and the assumption is
written down in one place -- `EXPECTED_GENERATION` in `sparklib/admin.py` -- with
`unexpected_generation` reporting a violation. `spark status` and `spark audit`
both print FLEET ASSUMPTION BROKEN if the wire ever disagrees with
`controller_type`.

**If you change anything that leans on "MAX means pre-25", say so at the site and
check it.** Search for `EXPECTED_GENERATION` to find everywhere that leans on it.
If a controller is ever reflashed, this is the first thing to revisit.

## 2. Parameter reads: two dialects, one on each generation

Both generations answer a parameter read and they share no working frame between
them. A pre-25 SPARK MAX answers on api class 48 for parameters 0 to 133.
Firmware 25+ answers READ_PARAMETER across api classes 15 to 22 for all of 0 to
255, provided the request is a remote frame carrying dlc 8.

For eight days this section said a Flex answered nothing, and that was this
package sending zero-length data frames on one class of eight.

The Flex half settled once the frame form was found. REV-Specs marks
every parameter-read frame `rtr: true`, this package was sending zero-length DATA
frames, and 26.1.6 answers all of them when the request is a remote frame
carrying dlc 8 (`flex.param_reads_were_probed_as_data_frames`, HARDWARE;
[the run](runs/rig-flex-parameter-reads.md)).
Nothing on the MAX side depended on how that landed, and nothing changed here.

The parameter id rides in the **arbitration id**, not the payload, and the reply
comes back on that same id. There is no separate response frame.

```
api   = 0x300 | param_id
arb   = 0x02050000 | (api << 6) | device_id

param   0  ->  api 0x300  ->  api class 48, index 0  ->  arb 0x0205C000 | id
param 133  ->  api 0x385  ->  api class 56, index 5  ->  arb 0x0205E140 | id
```

`0x02050000` is device type 2 (motor controller) in bits 28:24 and manufacturer
5 (REV) in bits 23:16. The api occupies bits 15:6, split into class 15:10 and
index 9:6, and the device id is bits 5:0
(`docs/FAILURE-CATALOGUE.md:372-374`). So the parameter table is not a
single api class. It starts at class 48 index 0 and walks up through the api
space, reaching class 56 index 5 at parameter 133. The arithmetic lives in
`SparkAdmin._legacy_param_arb`, `sparklib/admin.py`.

| direction | payload sent | reply |
| --- | --- | --- |
| read | zero-length frame, DLC 0 | `[uint32 value LE][type tag][status byte]` |
| write | 5 bytes, `[int32 value LE][type tag]` | same shape, carrying the value the device actually took |

Type tags, `LEGACY_PARAM_TYPE` in `sparklib/admin.py`:

| tag | meaning |
| --- | --- |
| 0 | int32 |
| 1 | uint32 |
| 2 | float32 |
| 3 | bool |

**These are not the 25+ type codes.** `PARAM_TYPE` in `sparklib/admin.py` is
`{0: Unused, 1: Int, 2: Uint, 3: Float, 4: Boolean}`. Tag 2 means float32 in the
legacy dialect and Uint in the modern one. Reading a legacy reply through the
modern table turns every float parameter into an integer, and reports it as
successful.

Status byte 0 is success, `LEGACY_PARAM_OK` in `sparklib/admin.py`. Anything else is
the device replying and refusing.

**Measured, rig-max,:** all 134 parameters, ids 0 through 133, answered
on all eight controllers. Zero refused, zero silent. Parameter 0 returns each
device's own CAN id, which is the self-check that the reads are live and
per-device rather than one cached answer.

Parameters 158 through 165 reply with a **non-zero status**. On pre-25 the status
periods are not parameters at all. They move on the api class 6 dialect in
section 3.

### The CLI for this

`uv run spark params`, added. `SparkAdmin.read_legacy_param` and
`write_legacy_param` had been the only entry points since the dialect was found,
and nothing exposed them.

    uv run spark params                     every parameter, every controller,
                                            showing only what differs
    uv run spark params --all               the whole table anyway
    uv run spark params --param 6           one parameter across the fleet
    uv run spark params --id 3 --param 13   one parameter, one controller
    uv run spark params --id 3 --param 6 --set 1     write it, RAM only

Run on the MAX rig: 134 of 134 parameters answered on all eight, and
exactly one differs across the fleet -- parameter 13, P 0, reading 2 on the four
drive controllers and 0.1 on the four steer, which independently reproduces the
 measurement in section 8. A float is shown decoded rather than as its
uint32 bits, using the per-parameter type tag the legacy reply carries.

It refuses three things and names which: the six `PROTECTED_PARAMS`, ids 158-165
because periods are not parameters here, and anything above `LEGACY_PARAM_MAX`
because the same api range carries commands above the table. On a 25+ bus it
switches to READ_PARAMETER and reaches all of 0-255, the periods included.

`tools/spark_param_sweep.py` is a 25+ tool and stays one. It runs on
READ_PARAMETER and GET_PARAMETER_TYPES, both versionImplemented 25.0.0, so it
returns nothing on 24.0.1. Use `uv run spark params` on a MAX, which picks the
dialect from the firmware on the wire.

### Reading identity: the fingerprint at api 0x094

Identity is not a parameter here, but it is read like one, so it belongs beside
them.

**Nothing in the parameter table is an identity.** Measured on rig-max, exhaustively rather than as a spot check: all 134 parameters on all
eight controllers. 132 are byte-identical across the fleet, parameter 13 takes
two values split by role, and exactly one is unique per device -- parameter 0,
the CAN id, which is unique because we set it. Parameters 47, 48 and 49,
documented "Reserved", read `0xFFFFFFFF` on every controller. That upgrades the
older position, "those three are not serials", to "nothing in the table is"
(`pre25.no_serial_exists_in_the_parameter_table`).

**A fingerprint is readable outside the table.** api 0x094 answers a ZERO-LENGTH
ADDRESSED request with four read-only bytes:

```
arb = 0x02050000 | (0x094 << 6) | device_id      # LEGACY_FINGERPRINT_API
```

Measured on rig-max,: eight distinct values, one per controller, all
stable across a two second gap, high entropy, no bit fixed across the fleet and
not derived from the CAN id. It sits in apiClass 9, the device-identity class, at
index 4 -- beside `SET_CAN_ID` at index 5 and `GET_FIRMWARE` at index 8, and the
sweep that found it returned the correct firmware payload at 0x098, which
corroborates the api decoding. REV-Specs 2.1.0 names nothing at apiClass 9 index
4, so the api number itself is not cited to a vendor document; the behaviour is
measured. `SparkAdmin.read_fingerprint` sends it and
`SparkAdmin.inventory(with_fingerprint=True)` fills the `serial` key from it,
both in `sparklib/admin.py`.

**It is the firmware's Unique ID. It is not "the REV serial".** `SET_CAN_ID`
carries a Unique ID at bit 0 and a CAN ID at bit 32 per REV-Specs, and sending it
with this value moved a controller from id 3 to id 20 -- located there by
fingerprint -- and then back (rig-max, ). So the firmware itself accepts
it for addressing. But REV Hardware Client does not display it, and pre-25
identify takes no serial at all, so it cannot be matched against anything REV
prints or shows. Call it the fingerprint, or the Unique ID.

**Read-only, as far as it has been tried.** `DEADBEEF` written to the same
arbitration id as four bytes and as eight drew no reply and changed nothing, and
a full 134-parameter snapshot either side was identical. That is two payload
shapes tried and ignored, not a proof of immutability: a magic-guarded write could exist
here exactly as it does for the burn at api 0x072 (section 3), so the register is
not recorded as immutable.

**What it buys, all of it live on this fleet.** The identity column of `spark
status`, which `cmd_status` heads `fingerprint` on this generation and fills by
asking; `spark learn-serials`, which has been run -- `spark.yaml` carries
a `can_serials` block of the eight fingerprints, written  with a `.bak`
kept; swap detection in `spark status` and `spark audit`, which compare the
answer against that block; duplicate detection (section 7); and the addressing
`spark set-id` needs, which works and lands in RAM (section 7). All of it costs a
round trip per controller, and every caller asks explicitly, because a request
wakes a gated bus and a passive inventory must stay able to say "nobody spoke".

## 3. Writing: two dialects, and the one that does not exist here

A pre-25 MAX answers two write dialects, and they share no frames. The dialect
the rest of the package speaks is absent from this firmware entirely.

| | legacy parameter write | legacy status-period write | 25+ `PARAMETER_WRITE` |
| --- | --- | --- | --- |
| api | class 48 upward, `api = 0x300 \| param_id` | class 6, index = frame number | class 14, index 0 |
| arbitration id | `0x02050000 \| ((0x300 \| pid) << 6) \| id` | `0x02051800 + (frame << 6) + id` | `0x02053800 \| id` |
| payload | 5 bytes, `[int32 value][type tag]` | 2 bytes, little-endian ms, DLC 2 | 5 bytes, `[param id][uint32 value]` |
| acknowledgement | the value the device took, on the same arb id | **none, ever** | `PARAMETER_WRITE_RESPONSE` on `0x02053840` |
| how you confirm it | read the echo, or read the parameter back | measure the cadence on the wire | read the echo |
| on 24.0.1 | works | works | **silently dropped** |
| method | `SparkAdmin.write_legacy_param` | `SparkAdmin.set_legacy_status_period` | `SparkAdmin.write_param` |

The status-period write shares its arbitration id with the `LEGACY_STATUS_N`
broadcast the controller emits. `0x1800 >> 6` is `0x60`, which is api class 6
index 0, the same api as `LEGACY_STATUS_0`. The SPARK tells the two apart by DLC:
2 bytes sets the period, 8 bytes is status data (`LEGACY_SET_PERIOD` and
`SparkAdmin.set_legacy_status_period` in `sparklib/admin.py`). Nothing comes back,
so the only read-back is measuring the cadence.

`PARAMETER_WRITE` is `versionImplemented` **25.0.0** in
`tests/support/spec/REV-spark-frames-2.1.0.json`. A 24.x device does not carry
the frame, and an unmatched extended id is dropped in silence. That is why this
firmware looked unwritable: the wrong dialect and a dead controller produce the
same wire behaviour, which is nothing.

Diagnosed on rig-max,. `spark repair` reported a healthy
eight-controller bus as unwritable and the whole config-injection tier failed the
same way, before that tier was ported to speak both dialects (section 7), while
the evidence against that conclusion was on the wire the entire time, because the
fleet was already broadcasting this package's own boot throttle rather than REV's
defaults. Pinned by
[tests/hardware/test_legacy_period_write.py](../tests/hardware/test_legacy_period_write.py),
which asserts both that the api class 6 write changes the cadence and that the
`PARAMETER_WRITE` dialect is still unanswered.

### Every write lands in RAM, and only the parameter table can be burned out of it

Proven on rig-max,  and re-run as an automated test on. Idle
Mode was set to BRAKE on all eight and read back as BRAKE, an operator confirmed
the brake resistance by hand, sticky faults were cleared to `0x0000`, then the
motor rail was cut and restored. All eight came back **COAST**, which is REV's
factory default, with sticky `0x0200` confirming the cycle. A plain write in
either dialect is RAM only, and that has not changed.

What has changed is that RAM is no longer the end of it. `PERSIST_PARAMETERS` is
apiClass 63 index 15, `versionImplemented` 25.0.0, so the frame the rest of this
package burns with does not exist on 24.0.1. Pre-25 firmware has its own
burn-flash command at api 0x072, arbitration id `0x02051C80 | id`, and **it
works**. Measured on rig-max, with rail-cycle controls
(`pre25.burn_flash_api` in `sparklib/provenance.py`, and section 11 item 1):

| | measured on 24.0.1 |
| --- | --- |
| payload | two bytes, the magic **15011 little-endian** (`a3 3a`). It is the same `PERSIST_MAGIC` the 25+ `PERSIST_PARAMETERS` carries, and REV's own spec names that signal "Magic Number" with `decodedMin == decodedMax == 15011` |
| reply | on the request's **own** arbitration id: `0x00` accepted, `0xFF` refused |
| refused | wrong value, big-endian, a single byte. A zero-length frame draws no reply at all |
| accepted | the magic alone, the magic with trailing bytes, eight bytes of repeated magic. Only the first two bytes are read |
| what it commits | the parameter table, ids 0 through 133 |
| what it does not commit | the status periods |

Ids 3 and 4 were written Idle Mode COAST to BRAKE, burned, and read back BRAKE
after a motor-rail cycle, while a no-burn control on id 8 came back COAST. Both
were then written COAST and burned again, and that survived a further cycle. The
periods were measured separately rather than inferred: id 3 had `0x060` set to a
distinctive 77 ms -- neither REV's 10 nor this package's 50, so no other
mechanism produces that number -- and was burned with the magic and accepted
`0x00`, and after a rail cycle it read **10.0 ms**, like every other controller.
The periods move on api class 6 and are not part of what api 0x072 commits.

**This package does not send it.** Nothing in `sparklib/` emits api
0x072, and the arbitration id stays on the injector's denylist (`DENIED_BASES`
in `tests/support/sparkhw/wire.py`). The `spark persist` subcommand is not a
pre-25 path either: `SparkAdmin.persist` sends `PERSIST`, `0x0205FFC0`, which is
the 25+ frame this firmware drops (section 7). Persistence is now reachable on a
pre-25 MAX and is not implemented here. Whether burning as routine practice is
wise is a separate question, because flash cycles are finite.

The practical consequence, unchanged for the status periods and therefore for
everything this package writes today: on a MAX, `spark repair` would be a
re-apply and not a fix. A motor-rail power cycle is always the backstop.
`SparkBus.apply_boot_config` in `sparklib/can_bus.py` puts the status periods back, but
note when: it runs from `init_controller` and from `reconfigure_controllers`, so
the periods are restored by the next driver start or recovery call, not by the
rail cycle itself.
`uv run spark throttle` re-sends the same table from the CLI without starting the
driver (`cmd_throttle` in `sparklib/cli.py`). The write is unacknowledged, so it
measures the cadence afterwards and exits 1 naming any controller that did not
take it.

Flash-resident provisioning is unaffected. The drive P of 2.0, the steer P of 0.1
and the 40 A stall limit in section 8 survived both rail cycles.

## 4. The parameter table: the hard rules

1. **Never address above 133.** `LEGACY_PARAM_MAX` in `sparklib/admin.py` is 133,
   and `SparkAdmin._legacy_param_arb` raises rather than send. Above the table
   the same api range carries commands.
   `0x300 | 255` is `0x3FF`, api class 63 index 15, whose arbitration id is
   `0x0205FFC0`: byte for byte the `PERSIST` constant in `sparklib/admin.py` and
   the spec's `PERSIST_PARAMETERS`. A sweep that runs to 255 fires a flash
   command at the end of it. That frame is `versionImplemented` 25.0.0, so a
   24.0.1 device drops it and the hazard here is the sweep code shared with the
   25+ path rather than this firmware. Pre-25's own burn-flash sits outside the
   parameter range at api 0x072 (section 3), so no parameter sweep reaches it.
2. **158 through 165 are not parameters here.** They answer with a non-zero
   status. Use `SparkAdmin.set_legacy_status_period` instead.
3. **Six parameters are refused by tooling**, in both dialects.
   `PROTECTED_PARAMS` in `sparklib/admin.py` holds 0 (CAN ID), 2 (Motor Type) and
   50, 51, 52, 53 (limit switch polarity and hard limit enables). A bad write to
   0 puts a controller at an address nothing expects, and can land it on top of
   another controller. That is no longer unrecoverable over CAN: sweep the ids
   for fingerprints and `SET_CAN_ID` by fingerprint puts it back (section 2).
   Both the bad write and the recovery are RAM only, so a rail cycle restores
   whatever the flash copy holds -- which is the backstop, not a reason to try
   it.
4. **`rev_parameter_index.tsv` runs to id 198** and is not qualified by product.
   Its status-period defaults do not match this firmware: it gives parameter 159
   a default of 250 ms and parameter 165 a default of 20 ms, while the measured
   pre-25 defaults for those frames are 20 ms and 250 ms. See section 5. Use that
   table for names and types, not for pre-25 defaults.
5. **A zero-length frame is not inert.** A parameter read is zero-length and so
   is the fingerprint request, which makes it tempting to treat DLC 0 as request
   semantics generally. It is not: `CLEAR_FAULTS` (api 0x06E) executes on one.
   Measured on rig-max, and the hard way -- an api sweep justified on
   exactly that reasoning erased the sticky fault word on ids 3 and 4 while the
   other six still carried canTx, canRx and hasReset
   (`pre25.zero_length_frames_can_still_act`). Any future sweep must exclude
   every frame with COMMAND semantics whatever its length, derived from the frame
   spec rather than from `FORBIDDEN_BASES` in `tests/support/sparkhw/wire.py`,
   which is an injector denylist and was never a safety boundary for reads.

## 5. Status periods, measured either side of a rail cycle

Both readings are from rig-max, measured on the wire.

**REV pre-25 factory defaults**, measured after a rail cycle with nothing
re-applying:

| frame | period |
| --- | --- |
| `0x060` Status 0 | 10 ms |
| `0x061` Status 1 | 20 ms |
| `0x062` Status 2 | 20 ms |
| `0x063` Status 3 | 50 ms |
| `0x064` Status 4 | **never broadcasts** |
| `0x065` Status 5 | 200 ms |
| `0x066` Status 6 | 200 ms |
| `0x067` Status 7 | 250 ms |

**What a running rig-max broadcasts**, which is this package's own boot throttle
and not any REV default:

| frame | period | source |
| --- | --- | --- |
| `0x060` | 50 ms | `_SPARKMAX_STATUS_PERIODS_MS[0]` in `sparklib/can_bus.py` |
| `0x061` | 100 ms | `[1]` |
| `0x062` | 100 ms | `[2]` |
| `0x063` | 500 ms | `[3]` |
| `0x064` | absent | `[4]` is 500, and no frame is emitted to throttle |
| `0x065` | 1000 ms | `[5]` |
| `0x066` | 1000 ms | `[6]` |
| `0x067` | 250 ms | untouched. The boot table stops at frame 6 |

`SparkBus.apply_boot_config` in `sparklib/can_bus.py` re-sends that table from
`init_controller` and from `reconfigure_controllers`. Between a rail cycle and
the next driver start the bus runs at the factory defaults above, unless
`uv run spark throttle` re-sends the table first.

Three things follow.

**`0x064` is not a lost write.** It does not broadcast in either state. This
firmware does not emit the alternate-encoder frame at all.

**`0x067` cannot be addressed by this package.** `LEGACY_FRAME_MAX` in
`sparklib/admin.py` is 6, so `set_legacy_status_period` raises on frame index 7, and
`_SPARKMAX_STATUS_PERIODS_MS` has no key 7. Status 7 runs at REV's 250 ms default
on a provisioned robot. Whether frame index 7 is writable at all is open. Nothing
has sent it.

**The periods cannot be burned to flash, and the parameters can.** The pre-25
burn-flash at api 0x072 commits the parameter table and measurably does not reach
these frames (section 3): on rig-max, `0x060` on id 3 was set to a
distinctive 77 ms, burned with the magic and accepted `0x00`, and read 10.0 ms
after a rail cycle. So the factory table above is what a cold bus runs at however
much else has been persisted, and `uv run spark throttle` or a driver start
remains the only way to leave it. That is not a tidiness matter: the same rail
cycle took CONTROLLER traffic from 384 to 1872 frames/s
(`max.param_writes_are_ram_only`, rig-max ), and gs_usb turns every CAN
frame into a transfer on a 12 Mbit full-speed USB link, which is what
[docs/SPARK-MAX-BRINGUP.md](SPARK-MAX-BRINGUP.md) calls the wedge
risk. Total bus load moves by less than that ratio, 788 to 2276 frames/s, because
a CTRE device on the same bus contributes a constant 404 frames/s that the
throttle does not touch (section 7, and section 11 item 9).

## 6. Faults

The pre-25 fault word is in `LEGACY_STATUS_0` (`0x060`) bytes 2:6, decoded by
`decode_legacy_status_0` in `sparklib/admin.py`. It is one 32-bit field split
16/16: the low half is active faults, the high half is sticky faults. Frames
2.1.0 names one opaque 32-bit signal and does not describe that split, so the
split comes from older SPARK MAX documentation.

**hasReset is bit 9.** Confirmed on rig-max, by two independent
motor-rail cycles: every one of the eight came back with sticky `0x0200` and no
other bit set, and a rail cycle is precisely the event that sets hasReset and
nothing else.

**canTx is bit 7 and canRx is bit 8.** Confirmed on rig-max, by an
event nobody staged: a CAN connector was pulled while all eight controllers were
powered and broadcasting. The segment went silent, the gs_usb adapter went
ERROR-PASSIVE, and when the connector was replaced every one of the eight read
sticky `0x0380` -- bits 7, 8 and 9 exactly. hasReset was already latched from an
earlier rail cycle, so the two NEW bits are 7 and 8, and severing a bus is
precisely what stops a controller transmitting and stops anyone acknowledging it.
Eight of the sixteen have hardware behind them as of: bits 4, 7, 8, 9,
12, 13, 14 and 15 (`pre25.fault_bit_hasreset`, `pre25.fault_bits_cantx_canrx`,
`pre25.fault_bit_sensor`, `pre25.fault_bits_softlimit_fwd_rev` and
`pre25.fault_bits_hardlimit_fwd_rev`). The other eight are in
[docs/SPARK-MAX-BRINGUP.md](SPARK-MAX-BRINGUP.md), which gives each
one a checked reason for being left alone.

The full 16-bit ordering, `_LEGACY_FAULT_BITS` in `sparklib/admin.py`:

| bit | name | bit | name |
| --- | --- | --- | --- |
| 0 | brownout | 8 | **canRx** (confirmed) |
| 1 | overcurrent | 9 | **hasReset** (confirmed) |
| 2 | iwdtReset | 10 | gateDriver |
| 3 | motorType | 11 | other |
| 4 | sensor | 12 | softLimitFwd |
| 5 | stall | 13 | softLimitRev |
| 6 | eepromCrc | 14 | hardLimitFwd |
| 7 | **canTx** (confirmed) | 15 | hardLimitRev |

The ordering is **vendor-sourced**, not a project guess. Four independent source
classes were swept and agreed on all sixteen positions: REVLib 2024.2.4's own
`FaultID` enum in `CANSparkBase.h` and `CANSparkBase.java`, REV's
`SPARK-MAX-Types.proto`, the REV Hardware Client fault list on 24.0.1, and
third-party re-implementations of the protocol. Nothing was vendored to settle
it: the sources are cited in the `pre25.fault_bit_order` claim in
`sparklib/provenance.py`, not copied, and no REVLib 2024.x header sits in
`tests/support/spec/`. Bits 7, 8 and 9 are measured on top of that, and
`docs/FAILURE-CATALOGUE.md:382-386` agrees on all sixteen.

REV's own names are kBrownout, kOvercurrent, kIWDTReset, kMotorFault,
kSensorFault, kStall, kEEPROMCRC, kCANTX, kCANRX, kHasReset, kDRVFault,
kOtherFault, kSoftLimitFwd, kSoftLimitRev, kHardLimitFwd, kHardLimitRev. The
table above keeps this package's camelCase. REVLib 1.1.5 and earlier called bit 2
kOvervoltage without moving it, so an old decoder mis-names that position rather
than mis-placing it.

### What went wrong

`_FAULT_BITS` in `sparklib/admin.py` is the **2025+ eight-name STATUS_1** table: `other, motorType, sensor, can, temperature, gateDriver, escEeprom,
firmware`. `normalised_reading` applied it to the pre-25 sixteen-bit word, so
names 0 through 7 were wrong and bits 8 through 15 were dropped by `_bits()`.
`spark faults` was run on rig-max immediately after that reboot and reported all
eight controllers **CLEAN**.

On a MAX, hasReset is the only signal that RAM configuration was lost and has to
be re-sent, and it was the one bit the decoder discarded. Everything this package
writes is volatile, because nothing in it burns to flash and the status periods
cannot be burned at all (section 3), so on this generation that bit is the whole
warning system.

The pre-25 branch of `normalised_reading` now scores against
`_LEGACY_FAULT_BITS`, so `spark faults` names `sticky-FAULT: hasReset` after a
reboot. `_bits()` walks the full width of the word rather than stopping at
eight, which is what lets bits 8 through 15 through at all. All sixteen pre-25
positions are named, so its unnamed-bit fallback (`bit12` and the like) only
shows on the 25+ eight-name table. `classify_reset` reads sticky faults as well as sticky warnings, which
is what makes reset detection work at all on this generation.

### What is still wrong

Two defects survive in the tree.

**`spark audit` names the reboot, but never as a reboot.** The sticky word now
reaches `status_problems` through `normalised_reading`, so a rail-cycled fleet
audits as eight `sticky FAULT: hasReset` findings and the command exits 1. The
branch written for exactly this case still cannot fire. `audit_problems` builds
its `rebooted` set from `sticky_warnings`, and the pre-25 branch of
`normalised_reading` returns `"sticky_warnings": []` unconditionally, because
pre-25 has faults and sticky faults and no warning field. Both functions are in
`sparklib/admin.py`. On a MAX, hasReset lands in `sticky_faults` instead, so that
set is always empty here. The `verdict == "reverted" and dev in rebooted` branch
is out of reach for a second reason too: `fault_frame` gives pre-25 the same
10 ms for `expected_ms` and `rev_default_ms`, so a fleet sitting at REV's cold
default scores `ok` and neither `reverted` branch fires at all. So the audit
reports the bit and says nothing about the periods that went with it.

**The printed remedy for hasReset is Flex text.** `BIT_REMEDIES["hasReset"]` in
`sparklib/admin.py` points at `spark repair --id N --persist`, and repair refuses a
pre-25 bus outright. The parameter-read half of that string was corrected on
; the command half is still wrong on a MAX. `spark audit`
prints it through `remedy_for` and `spark faults` reads `BIT_REMEDIES` directly,
so it appears under both.

All sixteen legacy names print advice now. `LEGACY_BIT_REMEDIES` covers the eight
that had none -- iwdtReset, eepromCrc, canTx, canRx and the four limit bits -- and
the rest fall through to `BIT_REMEDIES`, which is correct for both generations.

Read the sticky bits before clearing. `spark clear` works on this firmware
(`CLEAR_FAULTS` is apiClass 6 index 14, `versionImplemented` 1.0.0) and it erases
the only record that the reboot happened.

One thing is in your favour here. `apply_boot_config` clears sticky faults by
default only for a SPARK Flex: on a MAX `clear_sticky_faults` defaults to False
(`sparklib/can_bus.py`). So the sticky word survives a driver start, and the hasReset
bit is still there to be read when you get to the robot.

A driver start only. `reconfigure_controllers` passes `clear_sticky_faults=True`
for every family, and `swerve_drive` calls it on CAN restore, so an automatic
recovery erases the bit. Read `spark faults` before letting the driver recover
the bus.

## 7. Debugging matrix

| Symptom | Root cause | Fix |
| --- | --- | --- |
| `uv run spark repair` prints `REFUSED: spark repair writes the declared configuration` and exits 2 | `cmd_repair` in `sparklib/cli.py` calls `require_motor_defaults_for(controller_type)`. rig-max declares `controller_type: sparkmax` (`spark.yaml`) and the defaults file that call resolves declares `applies_to: SPARK Flex` (the `sparkflex:` block of `spark.yaml`). `MOTOR_DEFAULTS_KEYS` in `sparklib/admin.py` maps each product to its own block, and `_defaults_path()` resolves it from the `controller_type` `sparklib.cli` passes down. | Nothing to fix on the robot. The refusal is correct: writing Flex values into a MAX would look successful for every value it wrote. the `sparkmax:` block of `spark.yaml` is live: the audit scores rig-max against it and `spark defaults` prints its values. `spark-baseline.yaml` has been captured. Until then, put the status periods back with `uv run spark throttle` and re-apply the rest by hand with `SparkAdmin.write_legacy_param`. Those hand writes land in RAM: the pre-25 burn at api 0x072 would commit them and nothing in this package sends it (section 3). Cycling the motor rail and restarting the driver also re-sends the periods, through `apply_boot_config`. |
| `uv run spark defaults` prints `WARNING: this file applies to sparkflex and this robot is sparkmax` | The same missing file. `cmd_defaults` in `sparklib/cli.py` reads `meta.applies_to` and says so rather than resolving Flex values silently. | Read the warning and stop. Every value under it is a Flex value. This fleet's real configuration is section 8, read off the controllers. |
| `uv run spark faults` reported every controller **clean** right after a motor-rail power cycle, before the pre-25 decode was added | The pre-25 fault word is 16 bits and hasReset is bit 9. The 2025+ `_FAULT_BITS` table names only bits 0-7. Section 6. | The pre-25 decode path now scores against `_LEGACY_FAULT_BITS` in `normalised_reading`, so a reboot shows as `sticky-FAULT: hasReset`. On an older checkout, read the raw word instead: sticky `0x0200` with nothing else set is a clean power cycle. Then re-apply everything in section 3, because none of it had been burned to flash: `uv run spark throttle` for the status periods, `SparkAdmin.write_legacy_param` for the rest. |
| `uv run spark audit` after a rail cycle names `sticky FAULT: hasReset` on every controller, then prints Flex advice under it | The bit is decoded correctly now; the remedy is not. `remedy_for` prints `BIT_REMEDIES["hasReset"]`, which points at `spark repair --id N --persist`, and repair refuses a pre-25 bus outright. The branch written for a reboot never fires either: `rebooted` is built from `sticky_warnings`, which `normalised_reading` returns empty by construction on pre-25, and `fault_frame` gives this generation the same 10 ms for `expected_ms` and `rev_default_ms`, so a fleet at REV's cold default scores `ok` rather than `reverted`. All in `sparklib/admin.py`. | Read the bit, ignore the remedy under it. On a MAX the reboot means RAM went with it: `uv run spark throttle` puts the status periods back and section 3 covers the rest. `spark repair` refuses on this fleet, and its `--persist` sends the 25+ `PERSIST_PARAMETERS`, which 24.0.1 does not carry. The pre-25 burn at api 0x072 does commit the parameter table (section 3), but nothing in this package sends it, and it would not have saved the periods in any case. |
| `uv run spark persist` on a MAX prints `REFUSED: this bus reads pre25...` and exits 2 | Fixed. `cmd_persist` in `sparklib/cli.py` now reads the generation and refuses before it sends anything. It used to send `PERSIST`, `0x0205FFC0`, apiClass 63 index 15, `versionImplemented` 25.0.0, which a 24.0.1 device drops; the no-response fallback then compared the cadence against `fault_frame`'s `expected_ms` and, on a fleet already sitting at that value, printed that the burn had landed and exited 0 -- a false success with nothing written and nothing writable. | Nothing to do. The refusal names api 0x072 as the firmware's real burn and says this package does not send it. Pinned by `test_spark_persist_refuses_before_sending_on_pre25` in `tests/adversarial/test_pre25_persistence.py`, which asserts on the wire as well as the exit code. |
| `uv run spark duplicates` used to print `CANNOT TELL -- this bus reads pre25, which broadcasts no UNIQUE_ID` | It does not any more, and the refusal was right while identity was thought to be broadcast-only. `SparkAdmin.duplicates` found a duplicate by seeing two distinct `UNIQUE_ID` payloads on one id, and `UNIQUE_ID_BROADCAST` is apiClass 47, `versionImplemented` 25.0.0. The pre-25 route does not listen, it ASKS: the fingerprint at api 0x094 is REQUESTED, so one request to a shared id draws one reply per controller sitting on it. `_duplicates_pre25` sweeps ids 1..62 and counts the distinct answers, which is what finds a duplicate at an id no config knows about, and `duplicate_detection_available` now returns True on both generations. | Read the result in both directions now: a clean sweep means the ids were asked and each answered once, not that the check could not run. Verified on hardware by INJECTING a second reply on the fingerprint arbitration id rather than by moving a real CAN id -- clean before, detected during, clean after, and no controller changed (`test_duplicate_detection_finds_an_injected_second_responder`, `tests/hardware/test_legacy_burn_flash.py`). `read_fingerprint` discriminates by frame LENGTH and not by `is_rx`, because SocketCAN flags every locally generated frame as loopback and an `is_rx` filter dropped all 124 injected replies. What is still not possible is addressing the two apart once found: an addressed frame reaches both and only the first reply is read, so it is unplug-one-at-a-time and then RHC2 over USB-C. |
| The identity column in `uv run spark status` was empty for every controller | It was, while `SparkAdmin.inventory` filled `serial` only from `UNIQUE_ID_API` `0x2F0`, which is `versionImplemented` 25.0.0. Fixed: the column is headed `fingerprint` on this generation and `cmd_status` calls `inventory(with_fingerprint=True)`, which REQUESTS the value at api 0x094 (section 2). One round trip per controller, and opt-in rather than automatic, so a passive inventory can still tell "nobody spoke" from "nobody is there". | Nothing to fix. What has not changed: parameters 47, 48 and 49 are documented "Reserved" in `rev_parameter_index.tsv` and read `0xFFFFFFFF` on all eight, so they are **not** identity, and nothing else in the table is either. What has: `spark learn-serials` runs here and has been run, so `spark.yaml` carries the eight fingerprints and both `spark status` and `spark audit` now flag an id whose answer disagrees with the config. It is a fingerprint, not the serial REV prints. |
| `uv run spark set-id` on a MAX prints `MOVED, RAM ONLY` and exits 1 | Correct, and deliberate. `SET_CAN_ID` is apiClass 9 index 5, `versionImplemented` **1.5.0**, and the standard form works on 24.0.1 with the api 0x094 fingerprint as the Unique ID: `cmd_set_id` in `sparklib/cli.py` finds the target through `inventory(with_fingerprint=True)`, and id 3 was moved to id 20 and back on rig-max,. `SparkAdmin.set_can_id` then finishes by calling `persist`, which sends the 25+ `PERSIST_PARAMETERS` this firmware drops, so the move is RAM and reverts at the next power cycle. It used to print `serial <s> is not broadcasting -- nothing to reassign`, because nothing broadcast one to look up. | Believe the exit code: the id is live now and gone at the next rail cycle. If it has to survive one, set it over USB-C in the REV Hardware Client. The firmware's own burn at api 0x072 commits the parameter table and the CAN id is parameter 0, but whether that would persist a `SET_CAN_ID` move is untested, and this package does not send the burn in any case (section 3). Do not write parameter 0 by hand either: it is in `PROTECTED_PARAMS`. |
| `uv run spark identify` claimed to have sent, and no LED blinked | Fixed, and it was addressing rather than firmware. `IDENTIFY_UNIQUE_SPARK` is apiClass 7 index 6, `versionImplemented` **1.5.0**, so the frame exists -- but 25+ broadcasts it on device 0 with a 4-byte serial in the payload, and pre-25 wants `IDENTIFY_UNIQUE \| dev` with an EMPTY payload. The 25+ form is silently ignored on 24.0.1, so `SparkAdmin.identify` reported that it had sent and nothing ever happened. Recovered by capturing the REV Hardware Client's own LED button: exactly three DLC-0 frames in 9831 lines of candump -- `02051D81`, `02051D82`, `02051D83`, one per controller blinked -- reproduced from this driver and confirmed by an operator. `SparkAdmin.identify_by_id`. | `uv run spark identify --id N`. `--serial` is refused here with the reason, because pre-25 identify takes no serial at all. Nothing acknowledges the frame, so an operator watching the LED is the only confirmation; `uv run python tools/spark_blink.py --id N --repeat 5` is a standalone tester if the pulse is too short to catch. One consequence worth stating: identify cannot be used to confirm what the api 0x094 fingerprint is, because this form carries no identity. |
| `uv run spark audit` calls a running fleet's 50 ms `0x060` unexpected, or reports all eight as thinned out together with the bus dropping frames | Fixed. `_deliberate_periods` in `sparklib/admin.py` carries this package's boot throttle, and both the bus-level rule `_bus_level_finding` and `status_1_verdict` (through `also_ok`) consult it, so the throttle is no longer scored as loss. A healthy rig-max now audits as `no problems found across 8 controller(s)`. | Nothing to repair. 50 ms on `0x060` is provisioned and correct. The reference for a running pre-25 bus is `sparklib.can_bus._SPARKMAX_STATUS_PERIODS_MS`, which is also what `StatusPeriodGuard._default_restore` in `tests/support/sparkhw/guards.py` restores to. |
| `uv run spark audit`'s coverage footer says 3 of 9 deviating settings were checked | Accurate, and both numbers moved on. The denominator was 12, which is the FLEX block's deviation list hardcoded in `sparklib/admin.py`; it now derives from the ACTIVE product's block, and the `sparkmax:` block of `spark.yaml` declares nine. The numerator was 1; the audit now reads Smart Current Stall Limit and P 0 back over CAN through `legacy_deviation_problems` and compares them against the declared value. The remaining six are the status periods, which are not parameters on this generation and refuse a read. | Nothing to fix on the robot. A controller holding a wrong declared value is now reported by id and parameter instead of being listed as unverifiable. |
| A drive wheel coasts instead of braking | On rig-max this is **not** evidence of anything. Parameter 6 Idle Mode reads 0 (COAST) on all eight, and COAST is REV's factory default (`rev_parameter_index.tsv` id 6). The Flex row in [TROUBLESHOOTING.md](TROUBLESHOOTING.md) treats coast as a lost-config canary because Appendix A provisions a Flex to BRAKE. | Read it rather than infer it: `read_legacy_param(dev, 6)`. If you set BRAKE it is gone at the next rail cycle unless it is burned to flash, and nothing in this package burns (section 3). |
| `candump can0` shows a constant ~400 frames/s from a device id that is in no config | A CTRE power-distribution device shares this bus. Decomposed by arbitration id on the MAX rig: device type 8, manufacturer 4, apis 0x050 through 0x059 at device id 0, each at 40 Hz, plus api 0x3E0 at device id 63 at 4 Hz. `inventory` filters on manufacturer 5 and device type 2, so `spark status` correctly never showed it, and `spark throttle` does not touch it. Almost none of the payload varies: six of the ten frames are byte-for-byte constant over 480 samples. Not self-inflicted -- the same split was captured from a process importing neither `phoenix6` nor `sparklib`. | It corrects the throttle arithmetic. The boot throttle cuts CONTROLLER traffic 1872 -> 384, which is 4.9x, but TOTAL bus load only 2276 -> 788, which is 2.9x, because the 404 is constant in both states. The device is the CTRE PDP 4.0 and it belongs there, confirmed by the operator. Nothing is changed and no causal link to the gs_usb wedge was ever shown. Do not unplug it to find out: doing so silenced the whole bus and put the adapter ERROR-PASSIVE, which suggests the connector is inline in the CAN chain. |
| Every controller goes silent at once, and `spark clear` does not wake them | CHECK THE ADAPTER BEFORE THE ROBOT. On rig-max, this was a CANable whose USB connector is intermittent, reproduced on demand by flicking it with a finger. The CAN controller stays ERROR-ACTIVE with every error counter at zero throughout, which is exactly what a dead fleet looks like. The tell is in `journalctl -k`: ten numbered `usb xmit fail` inside 200 ms, then `USB disconnect`, then a re-enumeration, sometimes with `device descriptor read/64, error -32`. A latched fault does NOT explain it: four of eight were latched and all eight went dark, and silencing only the latched four in the simulator leaves the rest broadcasting (`tests/adversarial/test_rig_max_drive_overcurrent_silence.py`). | The fastest check needs no software: if the host is sending and the adapter's TX LED is dark, the frames never reach it. `spark status` and `spark clear` now read the kernel log themselves when nothing answers. `uv run python tools/can_usb_watch.py --seconds 0` watches a whole session with a second interface as a control. An intermittent connector is not fixable in software: replace the adapter or its cable. See `docs/runs/rig-max-drive-overcurrent-adapter-flap.md`. |
| Every drive controller holds sticky `overcurrent` after a fast run, and no steer controller does | Measured twice on rig-max, at drive scale 0.99, with `reset_kind` "none" throughout so nothing browned out. Per-controller current spans reached 54 A. `gateDriver` came with it on one controller in the first reading and on three in the second, moving between units, so it is not one bad unit. | Read the faults BEFORE clearing, since `spark clear` erases the only record. Whether this is tuning or hardware is open: this fleet's Smart Current Stall Limit is 40 A against REV's 80, the one setting that deviates. Run at reduced drive scale and read the faults again -- latches that track scale are load, latches that persist at low output are hardware. |
| `candump can0` shows no `0x064` from any controller | This firmware does not emit the alternate-encoder frame. Measured absent both before and after a rail cycle. | Nothing to fix. Do not throttle frame 4 and then read its silence as a lost write. |
| The hardware suite fails on `PARAMETER_WRITE` timeouts | It does not any more. The config-injection tier was ported to run on both generations through the `dialect` fixture in `tests/hardware/conftest.py`, which picks `set_legacy_status_period` and parameter 158 on pre-25 and `write_param` on 25+. Two pre-25 modules were added beside it: `tests/hardware/test_legacy_period_write.py` and `tests/hardware/test_legacy_fault_injection.py`. | Nothing to fix. Measured on rig-max with `SPARK_HW_INJECT`, `SPARK_HW_COLLIDE` and `SPARK_HW_CONGEST` armed: 48 passed, 0 failed, 36 skipped, 3 xfailed. The skips are real ones: the staged tiers need a person at the robot, `needs_clean_bus` stands down under collide and congest, the duplicate-by-serial test injects a `UNIQUE_ID` broadcast and that is a 25+ frame, and two congestion tests report that neither collision nor congestion raised a sticky fault on this fleet. The duplicate skip is not the pre-25 route going unexercised: that route is covered by injecting a second reply to the fingerprint request, in `tests/hardware/test_legacy_burn_flash.py`. |
| `tests/hardware/test_bus_preconditions.py` raises `KeyError: 'faults'` or `KeyError: 'is_follower'` | It does not any more. The module is generation-aware: it reads through `sa.normalised_reading`, scores the frame set against `_expected_apis(generation)`, and skips the follower check on a generation that broadcasts no follower bit. What has not changed is the decode underneath: `decode_legacy_status_1` in `sparklib/admin.py` returns `velocity_rpm`, `motor_temp_c`, `voltage_v` and `current_a` and nothing else. | Read through `normalised_reading`, which carries both generations' shapes, in any new test or tool. Reaching into a raw pre-25 reading by a Flex key is still a `KeyError`. |
| `uv run --with pytest pytest tests/adversarial` fails its CLI tests on a rig-max host | Those CLI tests read the live hostname-bound config and assert against `ROLES_BASE03`. | Pin them to rig-flex, where the adversarial tier is 368 passed and 7 xfailed. The unit tier is host-independent and gives 1082 passed, 74 skipped. |
| `spark status` or `spark clear` shows only six of the eight SPARKs, LB and RB missing | `_role_map` used to flatten every group in `devices` into one dict. rig-max puts CANcoders on the CANivore at ids 1, 2, 3, 4 and drive SPARKs at 1 and 4, so the cancoder entries overwrote the rear drive SPARKs. Ids are unique **per bus**, not per robot. | Fixed. `_role_map` in `sparklib/cli.py` partitions by group and `_spark_roles()` selects only `drive` and `steer`. |

## 8. The MAX rig as measured

All 134 parameters read off all eight controllers.

**CAN id map.** Two buses. `drive` and `steer` are SPARK ids on
`can.interface` (`can0`); `cancoder` ids are on the CANivore named
by `cancoder.bus` (`canivore`). The overlap on 1 through 4 is legal because
ids are unique per bus (`spark.yaml:95-101`).

| group | LF | RF | LB | RB |
| --- | --- | --- | --- | --- |
| drive (SPARK) | 8 | 5 | 1 | 4 |
| steer (SPARK) | 7 | 6 | 2 | 3 |
| cancoder (CANivore) | 1 | 4 | 2 | 3 |

**Configuration drift: zero.** Exactly one parameter of the 134 differs across
the fleet, and it splits by role. The other 133 are byte-identical on all eight.

| param | name | drive, ids 1, 4, 5, 8 | steer, ids 2, 3, 6, 7 | REV default |
| --- | --- | --- | --- | --- |
| 13 | P 0 | 2.0 | 0.1 | 0.0 |

**Deviations from REV factory default: two.** Parameter 13 above, and:

| param | name | value | REV default |
| --- | --- | --- | --- |
| 59 | Smart Current Stall Limit | 40 | 80 |

Both are flash-resident and survived both rail cycles.

Ids 3 and 4 are the only two controllers on this fleet whose parameter table has
been written to flash over CAN. Both were later burned Idle Mode BRAKE with api
0x072 and then burned back to COAST (section 3), so every value above still
reads as measured on those two as well. What changed there is not a value
but where it was last written from: their flash copy was committed over CAN.

The steer P of 0.1 is the controller's **on-board** loop. `base.swerve.steer_kp`
is 0.01 (`spark.yaml:92`), a separate host-side gain used by `zero_test`
and `motor_test` only, because live steer on this robot runs the on-board PID.

**Everything else matches REV's default.** Worth stating explicitly, because it
means a factory reset on this fleet would change two numbers.

| param | name | value on all eight |
| --- | --- | --- |
| 0 | CAN ID | each device's own id, the read self-check |
| 6 | Idle Mode | 0, COAST |
| 10 | Pole Pairs | 7 |
| 45 | Inverted | false |
| 47, 48, 49 | Reserved | `0xFFFFFFFF`. Not serials, and nothing else in the table is identity either |
| 52, 53 | Hard Limit Fwd / Rev Enabled | true |
| 63 | Motor Kv | 480 |
| 69 | Encoder Counts Per Rev | 4096 |

Parameter 5, Control Type, is a **runtime** value and not stored configuration.
Do not read a difference there as drift.

**Fingerprints, read.** The four read-only bytes each controller
answers with at api 0x094 (section 2), written into `spark.yaml` as
`serials` by `uv run spark learn-serials --write`, with the previous
copy kept at `spark.yaml.bak`. They are what makes a swapped controller
visible on this fleet; they are not the serial REV Hardware Client shows.

| group | LF | RF | LB | RB |
| --- | --- | --- | --- | --- |
| drive | 10487C0D | 249EB2B0 | 66D3BDE5 | A7A98E23 |
| steer | 5945A484 | 44E08E6A | 481F7CB5 | EABE4C2E |

## 9. What this firmware cannot do

Every row is cited against `tests/support/spec/REV-spark-frames-2.1.0.json`
unless it says otherwise. `versionImplemented` 25.0.0 means the frame does not
exist on 24.0.1, and an unmatched extended id is dropped without a NACK.

And every row is about a FRAME. The absence of a frame is not the absence of the
capability. On this firmware it twice was not: the 25+ persist frame is missing
and a working burn sits at another api, and the 25+ identity broadcast is missing
while identity is readable on request. Rows written as capability limits that
turned out to be frame limits were rewritten in place rather than annotated, so
what this table says now is what was measured.

| Not possible | Citation | Consequence |
| --- | --- | --- |
| Receive an identity broadcast | `UNIQUE_ID_BROADCAST`, apiClass 47, `versionImplemented` 25.0.0 | Nothing arrives unasked, so every identity read costs a request: `read_fingerprint` asks at api 0x094 and gets four read-only per-device bytes (section 2). That is enough for `learn-serials`, swap detection, duplicate detection and `set-id` addressing, and all four run on this generation now. What stays out of reach is the serial REV Hardware Client displays: no route to that one has been found, and the fingerprint is not it. |
| Write a parameter with `PARAMETER_WRITE` | apiClass 14, indices 0 and 1, `versionImplemented` 25.0.0 | Use the legacy dialect in section 3. `spark repair` refuses on this generation before it sends anything, and the config-injection tier now writes in whichever dialect the bus speaks. |
| Read a parameter with `READ_PARAMETER_n_AND_n+1` | apiClasses 15-22, 128 frames over parameters 0-255 in pairs, all `versionImplemented` 25.0.0 | Use `read_legacy_param`: a MAX answers reads on api class 48. That it answers nothing on apiClass 19 or 13 is weaker than it reads. Both remote probes to rig-max carried dlc 0, which 26.1.6 is measured to ignore, so the genuine dlc 8 form has never been tried here. `uv run python tools/spark_read_frame_form.py --id 3` settles it AT rig-max. |
| Ask the device which parameter ids exist | `GET_PARAMETER_n_TO_n+15_TYPES`, apiClass 13, sixteen frames over 0-255, `versionImplemented` 25.0.0. Untried at rig-max in the dlc 8 form, as the row above says | Each legacy reply still carries a per-parameter type tag in byte 4, so the device names its own types one id at a time. |
| Burn RAM to flash with `PERSIST_PARAMETERS`, the frame the rest of this package persists with | apiClass 63 index 15, `versionImplemented` 25.0.0 | The FRAME is absent; the CAPABILITY is not. api 0x072, arbitration id `0x02051C80 \| id`, carrying the magic 15011 little-endian, commits the parameter table (ids 0-133) to flash: measured on rig-max, against a no-burn control across two rail cycles. It does **not** reach the status periods, measured on a controller burned at a distinctive 77 ms that read 10.0 ms afterwards. This package neither sends it nor exposes it, and the arbitration id stays on the injector denylist, so nothing this package writes survives a power cycle today. Do not read this row as persistence being available from the CLI. Section 3 and section 11 item 1. |
| Blink an LED with `IDENTIFY`, or with the 25+ way of addressing `IDENTIFY_UNIQUE_SPARK` | `IDENTIFY` is apiClass 7 index 7, `versionImplemented` 25.0.0. `IDENTIFY_UNIQUE_SPARK` is apiClass 7 index 6, `versionImplemented` 1.5.0, so it exists -- but broadcasting it on device 0 with a 4-byte serial payload, which is the 25+ form, is silently ignored on 24.0.1 | Identify itself WORKS on a MAX. Pre-25 wants `IDENTIFY_UNIQUE \| dev` with an empty payload: `uv run spark identify --id N`, measured on the MAX rig and confirmed by an operator watching the LED. It carries no identity, so it cannot be used to confirm what the fingerprint is. Section 7. |
| Receive `0x064`, Status 4 | Measured absent on the MAX rig in both the throttled and the factory-default state | Do not read its absence as a lost write or a dead controller. |
| Read a warning, sticky or live | Pre-25 has faults and sticky faults and no warning field (`normalised_reading` in `sparklib/admin.py`) | `warnings` and `sticky_warnings` are empty by construction. Anything keying on them is dead code on this generation, which is the `spark audit` defect in section 6. |
| Read `SPARK_MODEL` from a status frame | `LEGACY_STATUS_0` carries no model field, and `SPARK_MODEL` arrived in firmware 26.1.0 (`SPARK_MODEL_MIN_FIRMWARE` in `sparklib/admin.py`) | `normalised_reading` returns `spark_model: None`, so the fleet-comparison check that finds one foreign controller has nothing to compare on. |
| Reboot a controller over CAN | There is no such command in the frame set | Cut and restore the motor rail. On this generation that is also how you restore RAM configuration, so it is the intended recovery and not a last resort. |

Frames that **do** exist on 24.0.1, and are the ones worth reaching for:
`GET_FIRMWARE_VERSION` (apiClass 9 index 8, `versionImplemented` 0.0.1),
`CLEAR_FAULTS` (apiClass 6 index 14, 1.0.0), `SET_CAN_ID` (apiClass 9 index 5,
1.5.0) and `IDENTIFY_UNIQUE_SPARK` (apiClass 7 index 6, 1.5.0). This paragraph
used to end "the last two are unreachable through the CLI only because it
addresses them by serial". Both are reachable now: identify takes a CAN id and an
empty payload, and set-id takes the api 0x094 fingerprint as its Unique ID --
and lands in RAM, because the frame that would commit it is the 25+ one this
firmware drops. Add the fingerprint request itself to the list, at apiClass 9
index 4, which REV-Specs 2.1.0 does not name.

## 10. Driving a MAX: what the generation changes, and what it does not

Everything above is about frames, and frames split on firmware generation. The
layers above the frame do not. A MAX rig runs the same steering and the same
chassis math as a Flex rig, and the code is the same code.

**Chassis math is product-independent.** `sparklib.kinematics` takes a chassis
velocity and returns a speed and an angle per wheel. It touches no bus and reads
no config, so the MAX rig and the Flex rig run identical arithmetic.
[INTEGRATION.md](INTEGRATION.md) walks the whole path from a driver input.

**The steer loop is where this rig differs, and it is a mechanical difference.**
The MAX rig carries SDS MK2 modules at 12.8:1, against 26:1 on the Flex rig. Set
`cancoder.steer_gear_ratio` to 12.8 and the conversion follows. A quarter turn of
the wheel is 3.2 motor rotations here, and 6.5 on the other rig, so a ratio
copied between them sends the axis confidently to the wrong angle.

**This rig closes the steer loop on the controller, and that is measured.** Its
slot-0 position PID is provisioned with P of 0.1 (section 8), so a steer command
is one `position_output` call carrying a motor-rotation target, and the loop then
runs at the controller's own rate rather than at the rate your process sends
frames. A corner commanded 53 deg away settled in 0.85 s and held to 2.34 deg,
with no overshoot and no sign changes. The standing error is what a
proportional-only loop leaves.

The route needs the position frame, api 0x062, and this generation broadcasts
it. Firmware 25 and later send only the frames something asks for, so the same
route on a Flex rig never settled at all.
[STEER-CONTROL.md](STEER-CONTROL.md) carries both measurements side by side.
The Flex rig closes the same loop on the host. [STEER-CONTROL.md](STEER-CONTROL.md)
covers when each is the right choice, and `sparklib.steer` supplies the P and PD
helpers the host-side route needs.

The absolute encoder seeds that loop once at startup, through
`cancoder.seed_from_absolute`. It is the same call on both rigs, because it is a
SPARK operation and the SPARK end of it is generation-independent.

**The diagnostics work here too.** `cancoder.health_report` and
`controller.health_report` print one startup block per device, and
`controller.health_report` reads its fault word through `normalised_reading`, so
it decodes the pre-25 layout without being told which generation it is on.
`sparklib.trace` watches a steer corner tick by tick and names a jump, a fight
between command and applied output, or an error that stops converging.

**What a MAX costs you at this layer.** Nothing in the steer or chassis path.
The costs are in section 9 and they are all frame-level: no identity broadcast,
no warning field, no SPARK_MODEL, and configuration that does not survive a
power cycle through this package.

## 11. Related

- the [README](../README.md), install, udev, bus bring-up, and the 25+ debugging matrix
- [docs/SPARK-MAX-BRINGUP.md](SPARK-MAX-BRINGUP.md), first power-on on a MAX robot and the open work list
- [TROUBLESHOOTING.md](TROUBLESHOOTING.md), cross-product gotchas, written mostly against a Flex
- [STEER-CONTROL.md](STEER-CONTROL.md), on-device against host-side steering, and the gear-ratio conversion
- [INTEGRATION.md](INTEGRATION.md), the path from a driver input to four wheels turning
- [SWERVE-SETUP.md](SWERVE-SETUP.md), bringing up a swerve corner on either product
- [docs/FAILURE-CATALOGUE.md](FAILURE-CATALOGUE.md), the legacy fault bit ordering, at lines 382-386
- [docs/SPARK-MAX-BRINGUP.md](SPARK-MAX-BRINGUP.md), what measuring the remaining thirteen fault bits would cost, and why most of them should not be caused
- `uv run spark verify`, every claim this driver rests on and how each one was got. It scores 40 claims against a MAX and none is unsettled. Nothing in the tree is unsettled now: 56 claims across both products, 0 gaps. The ones this document leans on are `max.param_access_answers_both_ways`, `max.param_writes_are_ram_only`, `pre25.burn_flash_api`, `pre25.serial_is_readable_at_api_0x094`, `pre25.no_serial_exists_in_the_parameter_table`, `pre25.zero_length_frames_can_still_act`, `max.firmware_honours_period_write`, `pre25.status_frame_7_period_is_not_writable`, `pre25.fault_bit_order`, `pre25.fault_bits_cantx_canrx`, `rig-max.foreign_traffic_on_the_spark_bus` and `pre25.boot_throttle_not_rev_default`.

## 12. Open work

Ordered by what blocks the most, though the ordering is now historical. Items
1, 4, 5, 6 and 8 are settled. They keep their positions instead of being
renumbered or deleted, so what changed stays visible. Each says what was found
and what is left of it.

**1. Persistence. SETTLED on rig-max, and the answer splits:
parameters can be burned to flash, the status periods cannot.** This item was
written as the largest unknown in the file and it is no longer an unknown. By
this list's own ordering it would now fall after item 3, because nothing here
still blocks on the hardware.

**What is settled, and it is measured rather than reported.** api 0x072,
arbitration id `0x02051C80 | id`, is a working burn-flash command on firmware
24.0.1. It takes a two-byte payload carrying the magic 15011 little-endian --
the same `PERSIST_MAGIC` the 25+ `PERSIST_PARAMETERS` uses, and REV's own spec
names that signal "Magic Number" with `decodedMin == decodedMax == 15011`
(`tests/support/spec/REV-spark-frames-2.1.0.json`) -- it replies on the
request's own arbitration id with `0x00` when it accepts and `0xFF` when it
refuses, and it commits the parameter table, ids 0 through 133. Ids 3 and 4 were
read across all 134 parameters to prove them healthy, written Idle Mode COAST to
BRAKE, sent `0x02051C83` / `0x02051C84` carrying `struct.pack("<H", 15011)`, and
both replied `0x00`. Id 8 was written the same value and sent no burn frame, as
the control. After a motor-rail cycle ids 3 and 4 read BRAKE and the control read
COAST: the rail really dropped, plain writes really are volatile, and only the
two that were burned survived. Both were then written COAST and burned back, and
that also survived a further cycle. The payload rules were checked on ids 1, 2,
5, 6 and 7 and are identical on every one: correct magic `0x00`, wrong value
`0xFF`, big-endian `0xFF`, one byte `0xFF`, magic with trailing bytes `0x00`,
eight bytes of repeated magic `0x00`, and zero length no reply at all. Only the
first two bytes are read, which is also what explains the earlier empty-payload
run on this fleet: a zero-length frame draws no reply and commits nothing, so
that run was the payload guard working rather than the api not existing.

**The status periods are the measured exception.** Id 3 had `0x060` set to a
distinctive 77 ms -- neither REV's 10 nor the throttle's 50, so nothing else
produces that number -- and was burned with the magic and accepted `0x00`. After
a rail cycle it read 10.0 ms, and so did all seven others. The periods move on
api class 6 and are not part of what the burn commits, so a rail-cycled fleet
still sits at REV's cold defaults until `uv run spark throttle` or a driver start
puts the throttle back. That is the half that binds operationally, and it is what
keeps the gs_usb wedge risk alive. Sections 3 and 5.

**What is left.**

- **Nothing here sends it.** No code in `sparklib/` emits api 0x072,
  and the arbitration id stays on the injector's denylist (`DENIED_BASES` in
  `tests/support/sparkhw/wire.py`). `spark persist` is not the pre-25 path: it
  sends the 25+ `PERSIST_PARAMETERS`, which this firmware drops, and on this
  generation it can report a burn that never happened (section 7). The
  capability is in the firmware and not in this package.
  `tests/hardware/test_legacy_burn_flash.py` stages `burn-arm` and `burn-verify`
  re-run the experiment by hand.
- **Whether to burn at all is a decision, not a gap.** Flash cycles are finite,
  and a re-apply on every boot costs only time. Persisting configuration also
  gives up the property this fleet has today, that a rail cycle returns every
  controller to a known state.
- **The api number's source is still uncited in this tree.** Its behaviour is
  now measured; where the number came from is not. Every document citing it
  traces to one comment in `tests/support/sparkhw/wire.py`, which derives the
  arbitration arithmetic and not the api number.

**2. The CTRE power-distribution device on `can0` is the PDP 4.0, and it
DOES report under load.** Identified: device type 8 and manufacturer 4
name it, and the operator confirms it is wired to that bus. Nothing is added to
`spark.yaml`, because nothing here commands it, and `spark status`
correctly never shows it because `inventory` filters on manufacturer 5 and device
type 2.

The earlier reading, that it carries almost no information, was drawn from an
idle robot and does not survive a driven one. Captured while the base drove for
30 s (`tools/spark_pdp_correlate.py`, `records/rig-max-post-rail-cycle-20260910.log`):
byte 4 of api 0x056 tracks total SPARK current at r = +0.88, and four fields in
api 0x050 track it at +0.78 to +0.79, which is the shape of a frame carrying
per-channel currents. A panel measuring nothing reports nothing, so the constant
frames were the sample and not the device.

The current match is INDIRECT and does not calibrate anything: a panel channel
measures SUPPLY current into a controller while the controller reports PHASE
current, and the two differ by roughly the duty cycle. No field tracked the rail
voltage in that window, where the rail moved only 0.466 V, so the telemetry-scale
question stays open.

Its 404 frames a second stay. No causal link to the gs_usb wedge was ever shown:
at roughly 110 bits per extended frame the cold bus is about 25 percent of 1 Mbit
and the throttled bus about 9 percent, so raw utilisation is not obviously the
mechanism, and there is no reason to spend risk slowing a device that is doing no
harm.
