# Bringing up a SPARK MAX robot

For rig-max-2 and rig-max. Until everything this package knew about SPARK
controllers had been measured on SPARK Flex, and this document said so. rig-max
has now been read end to end, twice across a motor-rail cycle, and most of what
follows is a record rather than a prediction.

What was measured: eight SPARK MAX on `can0`, a gs_usb adapter at 1
Mbit, all eight on firmware **24.0.1**, which the driver labels generation
`pre25`. rig-max and, then a long session
that settled the burn flash in
[Parameters](#parameters-a-pre-25-max-answers-reads-and-writes) and the whole of
[Identity](#identity-a-fingerprint-on-pre-25-a-serial-on-25-and-later).

Read [docs/PROTOCOL.md](PROTOCOL.md) first if you have not. The one
thing that decides everything below is the controller's firmware version, not
the fact that it is a MAX. The headline from rig-max is that a pre-25 MAX answers
a parameter read on its own dialect, api class 48. A Flex on 26.1.6 answers too,
over READ_PARAMETER, once the request goes out as a remote frame with dlc 8. See
[Parameters](#parameters-a-pre-25-max-answers-reads-and-writes).

## The declared configuration must be present

Everything this guide refers to is tracked: the test suite, the vendored REV
specifications, both motor-defaults files and both legacy hardware modules. A
fresh clone gets all of it.

That matters because a tree missing the declared motor configuration no longer
audits every controller against an empty table and calls it clean.
`load_motor_defaults` raises `MotorDefaultsMissing` rather than returning None,
and `motor_settings` reaches it with `required=True`, so an absent file is an
error rather than a silent pass.

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

## Install

```bash
git clone git@github.com:l5vel/sparklib-py.git
cd sparklib-py
uv sync --extra sparkmax          # phoenix6 25.4.0, python-can 4.5.0
uv run arm-base-install
```

The python-can pin is not a preference. Versions 4.6.0 and 4.6.1 let
`bus.send()` return success while no heartbeat frame reaches the motors over
gs_usb, so the base looks alive and does not move. rig-max sets
`verify_hardware: true`, and the preflight raises on a wrong version rather than
letting you find out on the floor.

[docs/SETUP.md](CAN-SETUP.md) covers the udev rule, the sudoers grant and the venv.
The rule names the SPARK bus `can0`; rig-max's CANcoders are on a separate
CANivore netdev.

## What rig-max declares

| Setting | Value |
| --- | --- |
| Selected by | `controller_type: sparkmax` in `spark.yaml` |
| `controller_type` | `sparkmax` |
| CAN interface | `can0`, gs_usb adapter, 1 Mbit |
| CANcoder bus | `canivore`, a CANivore netdev (`base.cancoder_bus`) |
| Drive ids | LF 8, RF 5, LB 1, RB 4 |
| Steer ids | LF 7, RF 6, LB 2, RB 3 |
| CANcoder ids | LF 1, RF 4, LB 2, RB 3, on the other bus |
| Firmware, measured | 24.0.1 on all eight |
| xArm | 172.16.0.11 |

**Two buses, and the ids overlap legally.** CANcoder 1-4 and SPARK 1-4 are
different devices on different netdevs. `cli._role_map` used to flatten all
three groups into one `{id: role}` dict, so the CANcoder entries overwrote the
rear SPARKs and `spark status` and `spark clear` silently skipped LB and RB.
Fixed; `_role_map` now takes a group filter and `_spark_roles()` asks
only for the SPARK groups (`cli.py:82-102`).

**`serials` now exists on rig-max.** `spark learn-serials --write`
wrote the eight per-device fingerprints and kept the previous file
at `spark.yaml.bak`. They are fingerprints read at api 0x094, not REV
serials, and the block says so in a comment; see
[Identity](#identity-a-fingerprint-on-pre-25-a-serial-on-25-and-later). There is
still no `spark-baseline.yaml`.

## Step 1. Read the firmware version, because it decides everything

```bash
uv run spark status
```

A silent bus is normal. These controllers gate their transmitters, and one read
addressed to any single device wakes every one of them. If nothing appears, run
`uv run spark clear` and look again before concluding anything is wrong.

Read the firmware column. Everything below splits on it.

**Firmware 25.0.0 or later.** The controllers broadcast api 0x2E0 and 0x2E1 with
every fault in STATUS_1, and 0x2F0 with a serial, exactly as the Flex fleet on
rig-flex does. This is the path the driver exercises daily.

**Firmware below 25.** Faults live in the 0x060 fault word, telemetry in 0x061,
and nothing broadcasts an identity. An identity can still be REQUESTED, at api
0x094, which is what fills the column -- see
[Identity](#identity-a-fingerprint-on-pre-25-a-serial-on-25-and-later). rig-max is
here.

Either way the driver reads the generation off the wire and labels each device
with what it actually broadcast. You do not configure it.

## Step 2. Read the faults before you clear anything, and do not trust "clean"

```bash
uv run spark faults      # reads and records; it does NOT clear
uv run spark status
uv run spark clear       # last, and only once you have read what was there
```

The order is load-bearing. A power cycle latches `hasReset`, and that bit is the
only evidence the reboot happened. `spark clear` erases it. So does
`SparkBus.reconfigure_controllers`, which defaults `clear_sticky_faults=True` for
every family (`can_bus.py:139-151`). `apply_boot_config` on its own does not,
on a MAX: its default is `controller_type == SPARK_FLEX`, so a MAX keeps its
sticky bits through a normal handler construction (`can_bus.py:129-133`). That
asymmetry is deliberate and it means the recovery path, not start-up, is what
destroys the evidence.

**`spark faults` reported a rebooted MAX fleet as clean, until.**
After each of two motor-rail cycles on rig-max all eight controllers came back with
a sticky fault word of `0x0200` and no other bit set, which is bit 9, which is
`HasReset`. `spark faults` was run immediately afterwards and printed `clean` on
all eight. That is this suite's own worst failure mode, a clean bill of health
over a robot that had just lost every volatile setting it held.

The cause was a decoder mismatch, not a controller problem.
`admin._FAULT_BITS` is the 2025+ STATUS_1 table and has **eight** names. The
pre-25 fault word is **sixteen** bits wide, split 16 active / 16 sticky out of
0x060 bytes 2..6, and `normalised_reading` was running the legacy word through the
eight-name table.

Both halves are fixed in this tree. `_LEGACY_FAULT_BITS` is the sixteen-name
pre-25 table (`admin.py:1076-1080`) and the pre-25 branch of
`normalised_reading` selects it (`admin.py:1894` and `:1899`); `_bits()`
now walks the full width of the word and emits `bit12` for anything the table
does not name, rather than dropping it (`admin.py:1083-1090`).
`classify_reset` reads sticky FAULTS as well as sticky warnings, which is the
half pre-25 files these bits in (`admin.py:271-287`). `spark clear` on rig-max now prints
`hasReset without brownout: an ordinary power cycle` and records
`reset_kind: power-cycle`, which it could not do before; the records it wrote are under `records/` (`admin.py:268`).

All sixteen names are now vendor-sourced rather than project-authored, and five
of the sixteen are measured on top of that, so a decoded name can be acted on.

To read the raw word yourself, take it from the 0x060 payload:
`admin.decode_legacy_status_0` returns `faults_and_sticky`, `active_faults`
and `sticky_faults` (`admin.py:1166-1193`). `read_legacy_param` will NOT give
you this -- it reads the parameter table, and the fault word is not in it. A
sticky `0x0200` after a power event is the normal, expected reading and not a
fault to chase.

The pre-25 bit ordering is in
[docs/FAILURE-CATALOGUE.md:382-386](FAILURE-CATALOGUE.md): 0 Brownout,
1 Overcurrent, 2 IWDT, 3 MotorType, 4 Sensor, 5 Stall, 6 EEPROM CRC, 7 CAN TX,
8 CAN RX, 9 HasReset, 10 GateDriver, 11 Other, 12-15 soft and hard limits. Every
one of the sixteen positions is settled, and the claim is
`pre25.fault_bit_order`, carried as `[vendor]`
(`provenance.py:509-535`). REVLib 2024.2.4's own `FaultID` enum
(`CANSparkBase.h` and `.java`), REV's `SPARK-MAX-Types.proto`, the REV Hardware
Client fault list on 24.0.1 and third-party re-implementations were swept
independently and agreed on all sixteen; only the naming style varied. Nothing
was vendored to establish it -- the sources are cited, not copied, and
`reference/` holds REVLib 2025.0.3 and 2026.0.2 headers only.

**Bits 7 and 8 are hardware-confirmed as well.** A CAN
connector was pulled from the bus while all eight controllers were powered and
broadcasting: the segment went silent, the gs_usb adapter went ERROR-PASSIVE
with 37 error-warn and 10 error-pass events, and when the connector was replaced
every one of the eight read sticky `0x0380` -- bits 7, 8 and 9 exactly, decoding
as canTx, canRx and hasReset. hasReset was already latched from an earlier rail
cycle, so the two NEW bits are 7 and 8, and severing a bus is precisely what
stops a controller transmitting and stops anyone acknowledging it. Not a
deliberate experiment, but the confound-free kind: one event, two new bits, and
the two bits the event predicts. The claim is `pre25.fault_bits_cantx_canrx`.
**Bits 14 and 15 were measured CAUSALLY**, which is a stronger
class of evidence than either of the above: Limit Switch Fwd/Rev Polarity,
parameters 50 and 51, were written 1 on id 3 with no switch wired to the data
port, the active word read `0xC000` -- bits 14 and 15 and nothing else -- and
writing them back to 0 returned it to `0x0000`, twice. The claim is
`pre25.fault_bits_hardlimit_fwd_rev`, and note that it is a RAM-only write:
`tools/spark_limit_polarity_repair.py` refuses it on pre-25 without
`--allow-ram-only`, because an inverted polarity nobody recorded is a hard limit
with no flash value to explain it. That takes the hardware-confirmed count to
five of sixteen at the time, eight since. "The pre-25 fault word, and which
bits are measured" below carries the tally, covers the rest and concludes most
should not be caused deliberately.

Two naming traps. REVLib 1.1.5 and earlier called bit 2 `kOvervoltage` without
moving it, so an old decoder mis-names that position rather than mis-placing it.
And [docs/FIELD-REPORTS.md:479](FIELD-REPORTS.md) carries a Chief
Delphi post putting overcurrent at bit 11; it is bit 1, and the catalogue is the
one to trust.

The vendored `REV-spark-frames-2.1.0.json` still carries no pre-25 fault enum at
all, only one opaque 32-bit field, so the ordering cannot be checked against it
-- see the `FAULTS_AND_STICKY_FAULTS` note in `decode_legacy_status_0`.

## Step 3. Record what the bus is, before changing it

**`spark snapshot` works on pre-25 now, and rig-max's baseline was taken with it.** It used to write the literal string `None` into every serial field
and null into every `status_1_period_ms`. Both gaps were this command's own, and
neither was the firmware's. It now requests the api 0x094 fingerprint on pre-25
and reads the period through `API_SETS[generation]`, so the file it writes can be
audited against. See
[docs/BASELINE.md](BASELINE.md).

What to capture instead is the parameter table, which this generation does
answer, plus the cadences off the wire. `uv run spark params --all` reads and
prints the whole table across the fleet, `SparkAdmin.read_legacy_param` reads any
one of parameters 0 to 133, and `spark status` gives the periods.

`spark snapshot` produces a usable pre-25 baseline, and it is
the same step here as in the Flex guide:

```bash
uv run spark snapshot --write   # --no-parameters records identity and cadence only
```

`spark-baseline.yaml` was captured with identity and cadence only, so it carries
no `parameters:` block. A run now reads the undeclared ids over 0 to 133 as well, and it refuses to record them while any of those ids differs across
the fleet.

**Do run `spark learn-serials --write`.** Until this command read the
serial out of the UNIQUE_ID broadcast, which a pre-25 controller never sends, and
exited 1 on the first configured id telling the operator to listen longer --
advice for a frame that was never going to arrive. It now REQUESTS the identity at
api 0x094 on this generation instead, and it has been run on rig-max: the eight
fingerprints are in `spark.yaml` under `serials`, written that
day with a `.bak`. That is what makes a swapped controller visible, and it is
the record `snapshot` below still cannot take for itself. See
[Identity](#identity-a-fingerprint-on-pre-25-a-serial-on-25-and-later).

**Both holes are closed and `spark-baseline.yaml` exists**, written. `cmd_snapshot` used to land with two of its own, neither of them the
firmware's fault. It asked for an inventory without fingerprints, so every
`serial` field held the literal string `None`; it now passes
`with_fingerprint=True` on pre-25 and records the api 0x094 values, which match
`serials` exactly. And its `status_1_period_ms` read
`periods_ms.get(0x2E1)`, an api that does not exist on this generation, so the
field was `null` for every device; it now resolves the api through
`API_SETS[generation]` and records the real cadence. The file also carries
`generation:` in its meta. For the record, the old text follows. The
serial is worse than empty: `cmd_snapshot` calls `inventory` WITHOUT
`with_fingerprint=True`, unlike `cmd_status`, `cmd_audit` and
`cmd_learn_serials`, so `inventory` leaves the serial `None` and the line is
built by f-string -- every controller would be recorded as the literal string
`'None'`, a value that reads like a serial to anything comparing them later. The
identity is one addressed request away and this command does not ask for it.
Snapshot would still exit 0 and still write the file. Its old fallback of running
`learn-serials` for you no longer fires on rig-max, because `serials` is
set; on a fresh pre-25 bus it would now fire and succeed.

On a MAX there is a better record available, and it is the one taken here:
the parameter table itself. See the next section.

## Parameters: a pre-25 MAX answers reads AND writes

Both generations answer a parameter read, in different dialects. What is
measured is one direction: PARAMETER_WRITE and READ_PARAMETER are
versionImplemented 25.0.0, so a 24.0.1 device drops them in silence, and that
reads exactly like a dead controller. Whether a firmware-25+ device answers the
legacy api class 48 has never been tried, and the two generations do overlap on
api class 6. A
pre-25 SPARK MAX answers on **api class 48** for parameters 0-133, and firmware
25+ answers READ_PARAMETER across api classes 15-22 for all of 0-255. The pre-25
dialect below was found first, and for eight days it was believed to be the only
one, because the 25+ reads were going out in a form 26.1.6 ignores.

```
arb   = 0x02050000 | ((0x300 | param_id) << 6) | device_id
read  = a zero-length frame (DLC 0)     -> reply on the SAME arb id
write = 5 bytes, [int32 value][type tag] -> the device echoes the value it took
reply = [uint32 value][type tag][status byte]
type tags: 0 int32, 1 uint32, 2 float32, 3 bool.   status 0 = ok.
```

The parameter id rides in the arbitration id, and the answer comes back on that
same id. There is no separate response frame.
`SparkAdmin.read_legacy_param` (`admin.py:908`) and
`SparkAdmin.write_legacy_param` (`admin.py:924`) speak it, over
`LEGACY_PARAM_ACCESS = 0x300` and `LEGACY_PARAM_MAX = 133`
(`admin.py:62-63`).

Measured on rig-max: **all 134 parameters, 0 through 133, answered on
all eight controllers. Zero refused, zero silent.** Parameter 0 returns each
device's own CAN id, which is the self-check proving the reads are live and
per-device rather than one echo repeated.

Three limits on this, and all three matter.

**Do not sweep past 133.** The same api range carries commands above the
parameter table, and 255 lands on Persist Parameters. Ids between the table and
255 map to frames REV's spec does not define, which is its own reason not to send
them. `_legacy_param_arb` raises rather than build such a frame.

**Parameters 158-165 answer with a non-zero status byte.** On pre-25 the status
periods are not parameters at all. They move on api class 6, below.

**Writes are RAM only until they are burned.** Proven: Idle Mode
was set to BRAKE on all eight and read back as BRAKE, an operator confirmed the
brake resistance by hand, then the motor rail was cut and restored. All eight
came back COAST, the factory default, with sticky `0x0200` confirming the cycle.
Re-confirmed, that time as an automated staged test rather than an
operator's note, with the same result on all eight. The 25+ `Persist Parameters`
frame is apiClass 63 idx 15, `versionImplemented` 25.0.0, so it does nothing
here. What does work on this generation is its own burn-flash on api 0x072, and
everything that follows about it was measured on rig-max.

**The pre-25 burn flash exists, and it commits the parameter table.** The frame
is `arb = 0x02051C80 | id`, and it requires a two-byte payload: the magic 15011,
little-endian, bytes `a3 3a`. That is the same `PERSIST_MAGIC` the 25+
`PERSIST_PARAMETERS` carries (`admin.PERSIST_MAGIC`), and REV's own spec
names that signal "Magic Number" with `decodedMin == decodedMax == 15011`
(`reference/REV-spark-frames-2.1.0.json`), so the two generations share
the constant. The reply arrives on the request's own arbitration id: `0x00`
accepted, `0xFF` refused. The claim is `pre25.burn_flash_api` in
`provenance.py`, carried as `[hardware]`.

Payload rules, measured identically on ids 1, 2, 5, 6 and 7: the correct magic
answers `0x00`; a wrong value, the magic big-endian, and a single byte each
answer `0xFF`; the magic with trailing bytes, and eight bytes of the repeated
magic, each answer `0x00`; a zero-length frame draws no reply at all. Only the
first two bytes are read. That is why the empty-payload form sent earlier the
same day was silent and committed nothing: the api was understood and the
payload refused, not the frame missing.

That it reaches flash was proven three times, with a control every time. Ids 3
and 4 were written Idle Mode COAST to BRAKE, burned, and the motor rail was cut
and restored; both came back BRAKE while the no-burn control on id 8 came back
COAST. Both were then written back to COAST and burned again, and that also
survived a further rail cycle. The control reverting is what makes it
conclusive. The scope is the parameter table, ids 0 through 133.

**The burn does NOT reach the status periods.** Measured, not inferred: id 3 had
0x060 set to a distinctive 77 ms -- neither REV's 10 nor this package's throttle
of 50, so nothing else on this bus produces that number -- and was burned with
the magic and accepted `0x00`. After a rail cycle it read 10.0 ms, like every
other controller. The periods move on api class 6 and are not part of what 0x072
commits, so `spark throttle` stays mandatory after every power event and the
cold-bus risk below is unchanged.

**This package does not send the burn.** Nothing in `sparklib/` emits api
0x072, there is no `spark persist` path on pre-25, and the arbitration id stays
on the injector denylist in `tests/support/sparkhw/wire.py`. The measurements
above came from `tests/hardware/test_legacy_burn_flash.py`, which is staged and
env-gated and needs an operator to cut the rail between its two halves. So in
ordinary use anything written over CAN to a MAX still has to be re-applied on
every boot. Whether burning is wise as routine practice is a separate question,
because flash cycles are finite. And the api number's own original source is
still uncited in this tree; what is settled is its behaviour, measured directly.

Flash-resident provisioning is untouched by any of this. The P gains and the
stall limit below survived both rail cycles.

Why this looked impossible until: `PARAMETER_WRITE` is apiClass 14,
`versionImplemented` 25.0.0 in `reference/REV-spark-frames-2.1.0.json`,
so 24.0.1 does not carry the frame, and an unmatched extended id is dropped in
silence. Sending the wrong dialect reads exactly like a dead or refusing
controller and is neither.

## What the fleet actually holds

All 134 parameters were read from all eight controllers, and again with `uv run spark params --all`, which is the command for it.

**Exactly one parameter differs across the fleet, and it splits by role.**
Parameter 13, `P 0`, is 2.0 on the drive motors (ids 1, 4, 5, 8) and 0.1 on the
steer motors (ids 2, 3, 6, 7). The other 133 are byte-identical across all eight.
There is zero configuration drift on this robot.

Two values deviate from REV's defaults, and both are deliberate: parameter 13
above, and parameter 59 `Smart Current Stall Limit` at 40 A against REV's 80.

That uniformity is also why the table holds no identity. Parameter 0, the CAN id,
is the only per-device value in it, and it is per-device only because we set it.
See [Identity](#identity-a-fingerprint-on-pre-25-a-serial-on-25-and-later).

Worth knowing before you change anything:

| param | name | value on rig-max |
| --- | --- | --- |
| 0 | CAN id | each device's own id |
| 5 | Control Type | a runtime value, not stored config |
| 6 | Idle Mode | 0, COAST, on all eight |
| 10 | Motor Pole Pairs | 7 |
| 13 | P 0 | 2.0 drive, 0.1 steer |
| 45 | Inverted | false |
| 52 / 53 | Hard Limit Fwd / Rev Enabled | true |
| 59 | Smart Current Stall Limit | 40 |
| 63 | Motor Kv | 480 |
| 69 | Encoder Counts Per Rev | 4096 |

## Status periods, and why a cold bus is not the same bus

Periods move on **api class 6**, not through the parameter table:
`arb = 0x02051800 + (frame_index << 6) + id`, two bytes little-endian
milliseconds, DLC 2, and **no acknowledgement, ever**. That is
`SparkAdmin.set_legacy_status_period` (`admin.py:856`), frame indices 0
through 6 only (`admin.py:37`). The only way to confirm one landed is to
measure the cadence on the wire. `uv run spark throttle` is the command that
sends them and then measures.

Frames actually on rig-max's wire: **0x060, 0x061, 0x062, 0x063, 0x065, 0x066,
0x067**. No 0x2E0, no 0x2E1, no 0x2F0.

**0x064 never broadcasts, before or after a rail cycle.** The boot table writes
500 ms to frame 4 whenever it runs and nothing appears. That is this firmware
declining to emit the alternate-encoder frame, not a lost write.

Two states, both measured:

| frame | running rig-max | after a rail cycle, nothing re-applied |
| --- | --- | --- |
| 0x060 | 50 ms | 10 ms |
| 0x061 | 100 ms | 20 ms |
| 0x062 | 100 ms | 20 ms |
| 0x063 | 500 ms | 50 ms |
| 0x064 | absent | absent |
| 0x065 | 1000 ms | 200 ms |
| 0x066 | 1000 ms | 200 ms |
| 0x067 | 250 ms | 250 ms |

The left column is this package's own throttle,
`can_bus._SPARKMAX_STATUS_PERIODS_MS = {0:50, 1:100, 2:100, 3:500, 4:500,
5:1000, 6:1000}` (`can_bus.py:25-27`), applied by `apply_boot_config` on every
power cycle (`can_bus.py:136`). The right column is REV's pre-25 factory
default. 0x067 never moves because the boot table stops at frame 6, and the boot
table stops there because **frame 7 does not take the write**. Measured on rig-max, id 3: a two-byte 400 ms payload was sent to `0x020519C3`, the
identical arithmetic and frame shape that moves frames 0 through 6 on this fleet,
and the cadence read 250 ms before and 250 ms after across a ten second settling
window. So `LEGACY_FRAME_MAX = 6` is a measured bound and not a cautious one
(`pre25.status_frame_7_period_is_not_writable`). That settles the write AS SENT;
the obvious alternative is closed too, because parameter 165 is Status 7 Period
but sits above `LEGACY_PARAM_MAX` and is not addressable on this generation.

Two consequences.

**The audit knows this throttle is deliberate.**
`REV_DEFAULT_LEGACY_STATUS_0_PERIOD_MS` is 10 (`admin.py:1497`) and
`fault_frame` keeps it as `rev_default_ms` for pre-25 while returning the boot
throttle as `expected_ms` (`admin.py:1518-1531`). What keeps a healthy
fleet at 50 ms from reading as eight controllers that all thinned out together
is `_deliberate_periods`, which returns the boot throttle keyed by api
(`admin.py:2076-2084`). The bus-level rule excludes any device sitting on
one of those cadences (`admin.py:2126-2132`), and `status_1_verdict`
scores it `ok` through `also_ok` (`admin.py:1573-1584`, passed at
`admin.py:2283-2291`). Until neither consulted it, and
`spark audit` called this fleet a bus dropping frames.

**A cold bus is a loud bus, and the throttle buys less than the controller
numbers suggest.** Between power-up and the first `apply_boot_config`, eight
controllers broadcast at 10/20/20/50/200/200 ms. Measured across the rail cycle
of, the CONTROLLER contribution is about 1872 frames/s cold against
about 384 at the boot throttle, which is 4.9x. TOTAL bus load only goes 2276 ->
788, which is 2.9x, because a CTRE power-distribution device -- device type 8,
manufacturer 4, sitting at device id 0 -- broadcasts a constant 404 frames/s that
`spark throttle` does not touch and `inventory` correctly filters out of `spark
status`. Whether it belongs on this bus is an open question for the operator and
not a finding; it is written up under item 8 of the open list below
(`rig-max.foreign_traffic_on_the_spark_bus`). The controller half is the load
`_SPARKMAX_STATUS_PERIODS_MS` exists to avoid, and the comment above it names the
failure: enough RX on an eight-motor bus to risk wedging gs_usb TX under sustained
load (`can_bus.py:21-24`). At roughly 110 bits per extended frame the cold bus
is about 25 percent of 1 Mbit and the throttled bus about 9 percent, so raw
utilisation is not obviously the wedge mechanism and no causal link has been
shown. Treat the throttle as a precaution with a measured cost, not a proven
cure.

Either way, do not leave a MAX bus powered and un-throttled while you work on
something else. Nothing re-applies the throttle on its own, so after any power
event run `uv run spark throttle`
(`cli.py:338-383`): it re-sends the table and then measures the cadence
back, because the write is never acknowledged, and exits 1 naming any controller
that did not take it.

## Step 4. Audit, and read what it does not cover

```bash
uv run spark audit
```

On a healthy pre-25 fleet this now ends `no problems found across 8
controller(s)` (`cli.py:2036`), because the period rules consult the boot
throttle. The coverage footer under it lists the settings the audit does not
check and says they are READABLE on this generation over the parameter table.
The footer on firmware 25+ now says the same over READ_PARAMETER and all of
0-255, and it names what a run did not read (`admin.py:2406-2485`).

`spark repair` USED to refuse on rig-max for a reason that has since been fixed.
`MOTOR_DEFAULTS_KEY` was a hardcoded module constant naming the Flex block and
`_defaults_path()` joined it without consulting `controller_type`, so
`require_motor_defaults_for` raised on `meta.applies_to: SPARK Flex` before
anything reached the bus. `MOTOR_DEFAULTS_KEYS` now maps each product to its own
file and `set_controller_type` declares which one this process reads, with the
`applies_to` guard still underneath; `spark defaults` prints MAX values on a MAX
host. The second reason stands and is not a bug: `fault_frame` returns
`provisioned: False` for pre-25, so the repair path stops on its own.
The wrong-product warning above its table fires only when a file's `meta.applies_to` disagrees with the robot reading it.

`the sparkmax: block` sits beside the Flex one in
the `sparkmax:` block of `spark.yaml`, and it is live. `motor_defaults_key()`
resolves the filename from the declared product, so `spark defaults`,
`spark audit` and `spark provision` all read the MAX file on rig-max.

`uv run spark provision --id N --write` is what re-applies a drifted setting
here, and no run against rig-max has happened yet. It reads 29 of the 37 settings
this file declares for one motor over api class 48, then writes back what
disagrees through `write_legacy_param`. Each write is read again afterwards,
because the echo is not evidence. What lands is a re-apply that holds until the
next power cycle, because the burn on api 0x072 has to follow it and nothing
here sends that frame. `--persist` is refused before anything goes out, since
PERSIST_PARAMETERS arrived at 25.0.0. The other eight settings are the status
periods at ids 158-165, which sit above the pre-25 parameter table, so
`uv run spark throttle` still owns them. The api 0x072 burn commits the
parameter table only, measured on rig-max, so a rail cycle takes
the periods back to 10 ms.

## Step 5. Run the suite

```bash
uv run --with pytest pytest tests/unit -q
```

Measured on rig-max: **1082 passed, 74 skipped**. This tier carries no
xfails, so there is nothing for `-rX` to report here.

Add `-rX` to the adversarial and hardware runs below, which do carry them. Every
xfail in this repo is `strict=False`, so a defect that got fixed turns its test
into an XPASS that a plain run never prints, and the marker silently stops
guarding.

The adversarial tier passes **368, with 7 xfailed**, when pinned to rig-flex.
Unpinned on a rig-max host, **25 fail**, because the CLI tests read the live
hostname-bound config and assert against `ROLES_BASE03`. That is a fixture
binding, not a robot fault.

Then the hardware tiers, in this order:

```bash
uv run --with pytest pytest tests/hardware -q -rX --hardware -m "not inject and not staged"
SPARK_HW_INJECT=1 uv run --with pytest pytest tests/hardware -q -rX --hardware -m "not staged"
SPARK_HW_INJECT=1 SPARK_HW_COLLIDE=1 SPARK_HW_CONGEST=1 \
  uv run --with pytest pytest tests/hardware -q -rX --hardware -m "not staged"
```

Measured on rig-max, with all three gates armed: **48 passed, 0
failed, 36 skipped, 3 xfailed**. Nothing in the tier fails on this generation any
more. The whole config-injection tier was ported to run on both, through the
`dialect` fixture that reads the write dialect off the wire
(`tests/hardware/conftest.py:382-383`), and
`test_bus_preconditions` asks the generation before it asserts a frame set, a
serial or a fault key (`test_bus_preconditions.py:51-52`, `:80`, `:106`).

The skips are legitimate, and worth recognising rather than chasing:

- the staged tier, which needs a person to cut power between two runs.
- everything marked `needs_clean_bus`, which stands down while `SPARK_HW_COLLIDE`
  or `SPARK_HW_CONGEST` is armed, because those fault every device
  (`pyproject.toml:84`).
- the duplicate-by-serial test, because pre-25 broadcasts no UNIQUE_ID. That
  skip is still right -- the test keys on the broadcast -- but it is no longer
  the whole picture. Pre-25 duplicate detection is exercised by
  `test_duplicate_detection_finds_an_injected_second_responder` in
  `tests/hardware/test_legacy_burn_flash.py`, behind `SPARK_HW_INJECT`, and that
  module was added after the run recorded above.
- two congestion tests, reporting that neither collision nor congestion raised a
  sticky fault on this fleet.

One Flex assumption is still in the tree and is simply no longer reached here:
`test_legacy_decoder.py:78` asserts `spark_model == 1`, the Flex value, and
pre-25 `normalised_reading` returns `spark_model: None` because LEGACY_STATUS_0
has no model field at all (`admin.py:1487`). On rig-max its `captured`
fixture skips first, because that module reads 0x2E0 and 0x2E1 and this fleet
broadcasts neither (`test_legacy_decoder.py:43-53`). Parameterise the expectation
by `controller_type` rather than loosening it.

Two modules were added for this generation.
`tests/hardware/test_legacy_period_write.py` pins both write dialects and asserts
the right one for the generation on the wire, and
`tests/hardware/test_legacy_fault_injection.py` exercises the pre-25 fault path.

Read [tests/hardware/README.md](../tests/hardware/README.md) before the injector
tiers. They leave a sticky `can` fault on every controller and nothing clears it
for you, so record and clear between tiers. `writable_id` now picks its target at
random once per session and prints the pick, so wear spreads across the fleet;
`SPARK_HW_SEED` reproduces a pick and `SPARK_HW_ID` pins one
(`tests/hardware/conftest.py:210-229`).

## Identity: a fingerprint on pre-25, a serial on 25 and later

Short answer: on 25+ the serial arrives on its own on api 0x2F0, the same place
a Flex puts it. Below 25 nothing is broadcast, but a per-device identity can be
**requested**, and that turns out to be enough for everything this package uses
identity for.

The vendor citation for the broadcast is
`reference/REV-spark-frames-2.1.0.json`: `Unique ID Broadcast` is
**apiClass 47, apiIndex 0, versionImplemented 25.0.0**. It did not exist before
that. The driver knows it, `API_SETS[GEN_PRE25]["unique_id"]` is `None`, and
`tests/adversarial/test_firmware_generations.py` pins it. Confirmed on metal: no
controller on rig-max broadcast UNIQUE_ID in any capture. That much of the old
answer stands, and it is the whole of what is missing on this generation.

**A pre-25 MAX does expose a readable per-device value, at api 0x094.** Measured
on rig-max: `arb = 0x02050000 | (0x094 << 6) | id`, a ZERO-LENGTH
ADDRESSED request, answered with four read-only bytes. Eight distinct values
across the fleet, all stable across a two second gap, no bit fixed across
controllers, high entropy, not derived from the CAN id, and unchanged by two
write attempts with a full 134-parameter snapshot either side. It sits in
apiClass 9, the device-identity class, beside `SET_CAN_ID` at index 5 and
`GET_FIRMWARE` at index 8, and the same sweep returned the correct firmware
payload at 0x098, which corroborates the api decoding. The claim is
`pre25.serial_is_readable_at_api_0x094`, carried as `[hardware]`, and
`SparkAdmin.read_fingerprint` speaks it over `LEGACY_FINGERPRINT_API`.

**It IS the firmware's Unique ID.** `SET_CAN_ID` carries a Unique ID at bit 0 and
a CAN ID at bit 32 per REV-Specs, and sending `SET_CAN_ID | 3` with this value
plus a new id moved that controller from id 3 to id 20 -- located there by the
same value -- and moved it back. So it is not merely a stable unique number: it
is the identifier the firmware itself accepts for addressing.

**Call it a fingerprint, or the Unique ID. Not "the REV serial".** REV Hardware
Client does not display it, so it will not match anything on a case label or in
RHC2, and pre-25 IDENTIFY does not take it: identify on this generation is
addressed by CAN id and carries no payload at all, so nothing here can confirm
this value is what `IDENTIFY_UNIQUE_SPARK` compares against on 25+. Read-only is
also scoped to the two payload shapes that were tried -- four bytes and eight --
so a magic-guarded write could exist, exactly as it did for the burn at api
0x072. Do not record the register as immutable.

**Nothing in the parameter table is an identity, and that is now exhaustive.**
All 134 parameters were read from all eight controllers with `spark params`: 132
are byte-identical across the fleet, parameter 13 takes two values split by role,
and parameter 0 -- the CAN id, which is per-device only because we set it -- is
the only unique one. Parameters 47, 48 and 49, documented "Reserved", read
`0xFFFFFFFF` on every controller, so they are identical and cannot be identity.
`rev_parameter_index.tsv` names nothing containing serial, unique, uid, ident,
mac, hash or guid at any id up to 198. The claim is
`pre25.no_serial_exists_in_the_parameter_table`, and it upgrades the previous
position from "those three ids are not serials" to "nothing in the table is".
The identity is off the parameter table entirely, on api 0x094.

What that leaves on a pre-25 bus:

| Command | On 25+ | Below 25 |
| --- | --- | --- |
| `spark status` identity column | serial per device, broadcast | fingerprint per device, one request each |
| `spark learn-serials --write` | writes `serials` | writes `serials`, fingerprints |
| `spark snapshot` serial + period fields | populated | serial `'None'`, null period |
| `spark duplicates` | two UNIQUE_IDs on one id | one request per id, two replies |
| `spark audit` swapped-controller check | works | works |
| `spark identify` | broadcast, addressed by serial | addressed by CAN id, `--id N` |
| `spark set-id` | moves it and burns it | moves it, and it reverts at the next power cycle |

**`identify` works, addressed differently.** Pre-25 identify is
`IDENTIFY_UNIQUE | dev` with an EMPTY payload. The 25+ model -- broadcast on
device 0 with a four-byte serial -- is silently ignored on 24.0.1, which is why
`spark identify` used to report that it had sent and no LED ever blinked.
Recovered on rig-max, by capturing REV Hardware Client while an
operator pressed its LED button: exactly three DLC-0 frames appeared in 9831
lines of candump, `02051D81`, `02051D82` and `02051D83`, one per controller
blinked. Reproducing them from this driver blinked the same controllers, and an
operator confirmed it. `SparkAdmin.identify_by_id` sends that form, `spark
identify --id N` reaches it, and `tools/spark_blink.py` is a standalone tester
for a pulse too short to catch. Nothing acknowledges the frame on either
generation, so an operator watching the LED is the only confirmation.
`spark identify --serial X` now refuses on pre-25 with exit 2 rather than sending
a frame the firmware drops.

**`set-id` works, and the move is RAM only.** `SparkAdmin.set_can_id` sends the
standard `SET_CAN_ID | current_id` frame with the fingerprint as the Unique ID
and the new id appended, and on rig-max that is what moved id 3 to id 20 and back.
But `set_can_id` finishes with `persist()`, which sends the 25+
`PERSIST_PARAMETERS` that 24.0.1 does not carry, so the burn returns None and the
new id is gone at the next power cycle. `spark set-id` prints `MOVED, RAM ONLY`,
says the move will revert, and **exits 1** rather than claiming success. If an id
has to survive a rail cycle, set it over USB-C in REV Hardware Client. The
firmware's own burn at api 0x072 would commit it, and this package deliberately
does not send that.

**Duplicate CAN ids are detectable on both generations.**
`duplicate_detection_available` returns True on pre-25. The
mechanism is the whole difference: on 25+ two different UNIQUE_ID broadcasts on
one id are the tell; on pre-25 the fingerprint is REQUESTED rather than listened
for, so one request to a shared id draws one reply per controller sitting on it.
`SparkAdmin.duplicates` sweeps every id in 1..62 on this generation, which is
what finds a duplicate at an id no config knows about, and `cmd_duplicates`
prints the fingerprints and what each one is recorded as.

That was verified on hardware by INJECTING a second reply on the fingerprint
arbitration id rather than by moving a real controller onto a shared id: clean
before, detected during, clean after, and no controller changed
(`test_duplicate_detection_finds_an_injected_second_responder` in
`tests/hardware/test_legacy_burn_flash.py`, gated behind `SPARK_HW_INJECT`).
`read_fingerprint` discriminates by frame LENGTH and not by `is_rx` for that
reason: SocketCAN flags every locally generated frame as loopback, so an `is_rx`
filter dropped all 124 injected replies and made the path impossible to exercise
without a genuine second controller. Length works because the request is the
zero-length frame and a reply never is, which is the same DLC discrimination this
firmware uses for period writes.

**Swap detection is live on rig-max.** `spark learn-serials --write` wrote the
eight fingerprints into `spark.yaml` with a `.bak`, and it
was tested in both directions: `spark status` prints `serial != config` on an id
whose fingerprint does not match, and `audit_problems` reports the swap and names
the fix. `cmd_status`, `cmd_audit` and `cmd_learn_serials` all pass
`with_fingerprint=True` on this generation; `cmd_snapshot` still does not, which
Replace from the clause on:725 through:726, so the text reads: "`cmd_status`, `cmd_audit`, `cmd_set_id` and `cmd_learn_serials` all pass `with_fingerprint=True` on this generation, and `cmd_snapshot` joined them. `spark-baseline.yaml` now carries the same eight fingerprints `serials` holds."

Two things about the fingerprint are NOT settled and should not be written as if
they were. Whether it is the same value `IDENTIFY_UNIQUE_SPARK` compares against
on 25+ cannot be tested here, because pre-25 identify takes no serial at all.
And read-only is a statement about two payload shapes, not about the register.

## Which commands work on a MAX today

rig-max, firmware 24.0.1. Most of this table is measured rather than read off the
code path: `status`, `clear`, `faults`, `voltage`, `throttle`, `params`,
`duplicates`, `identify` and `set-id` were all run against the fleet across and, and the last three of those write to a
controller. `snapshot` ran and wrote `spark-baseline.yaml`,
which records identity and cadence only. `verify` touches no bus at all,
and `persist`, `repair` and `defaults` are read off the code path and the
vendored frame spec.

| Command | On rig-max | Why |
| --- | --- | --- |
| `spark status` | works, and the identity column is populated | pre-25 broadcasts no identity, so it requests the fingerprint at api 0x094, one round trip per controller |
| `spark clear` | works | Clear Faults is not generation-gated |
| `spark throttle` | re-sends the boot throttle, then measures every frame back | api class 6 is unacknowledged, so the cadence is the only read-back |
| `spark faults` | works | decodes the pre-25 sixteen-bit word against the vendor-sourced sixteen-name table |
| `spark voltage` | works | pre-25 telemetry decodes from 0x061 |
| `spark params` | works, reads and writes the whole table | api class 48 answers both directions on this generation; every write lands in RAM only |
| `spark audit` | runs; a healthy fleet reads clean | the boot throttle is scored as deliberate, see Step 4, and the swap check has fingerprints to compare against |
| `spark verify` | works, exits 0 | every claim resolving for `sparkmax` is `[hardware]` or `[vendor]`; nothing is inferred or unverified |
| `spark canfix` | works, and fully substitutes for a physical replug | it rebinds the gs_usb driver; the adapter is full-speed USB and rejects `restart-ms` outright, so SocketCAN cannot auto-recover from bus-off on it |
| `spark learn-serials` | works, and has been run | reads the fingerprint at api 0x094 instead of waiting for a broadcast; wrote rig-max's `can_serials` |
| `spark duplicates` | works, on both generations | one fingerprint request per id in 1..62, and two controllers sharing an id both answer |
| `spark identify` | works with `--id N` | pre-25 identify is `IDENTIFY_UNIQUE \| dev` with an empty payload; `--serial` refuses with exit 2, because the 25+ broadcast form is silently dropped here |
| `spark set-id` | moves the controller, then **exits 1** | the move is RAM only: `set_can_id` finishes with PERSIST_PARAMETERS, which 24.0.1 does not carry |
| `spark snapshot` | works, and wrote `spark-baseline.yaml` | it requests the api 0x094 fingerprint on pre-25 and resolves the STATUS_1 api through `API_SETS[generation]`; that run predates the parameter block, so the table half is unread on this generation |
| `spark persist` | **refuses before sending**, exit 2 | Persist Parameters is apiClass 63 idx 15, `versionImplemented` 25.0.0. It used to send the frame, measure the cadence against `expected_ms` and report a burn that could not have happened. The pre-25 burn on api 0x072 does commit parameters, measured, and nothing in this package sends it |
| `spark repair` | **refuses** | Flex defaults file, and `provisioned: False` on pre-25 |
| `spark provision` | reads and writes on this generation with `--write`, and has not been run on rig-max yet; `--persist` **refuses**, exit 2 | it reads the 29 declared settings that are parameters here over api class 48, writes back what disagrees and reads each write again. The eight status periods are out of reach, so `spark throttle` still owns them. PERSIST_PARAMETERS is versionImplemented 25.0.0, so the burn is refused before anything is read or sent, and every write stays in RAM |
| `spark defaults` | works, and prints MAX values | `motor_defaults_file()` resolves `the sparkmax: block` from the declared product; the wrong-product warning fires only on a mismatch |

## What rig-max settled, and what is still open

The simulator models both generations. Before rig-max was read, every pre-25 claim
it encoded came from REV's documentation.
`sparklib/provenance.py` now carries each measured one with the
robot, the date and the evidence, and it is the authoritative record -- this
document is a reading of it, not a second source.

Measured and:

- `max.hardware.unprobed`, what has been read off rig-max and what turned up.
- `max.firmware_honours_period_write`, api class 6 works, `PARAMETER_WRITE` is
  absent rather than refusing.
- `max.param_access_answers_both_ways`, the api class 48 finding above.
- `max.param_writes_are_ram_only`, the rail-cycle proof, re-confirmed as an automated staged test rather than an operator's note.
- `pre25.fault_bit_hasreset`, bit 9, plus the decoder defect it exposed.
- `pre25.boot_throttle_not_rev_default`, the two-column period table above.

Measured:

- `pre25.burn_flash_api`, api 0x072 with the magic 15011 little-endian commits
  the parameter table and does not touch the status periods.
- `pre25.serial_is_readable_at_api_0x094`, the fingerprint, which is the
  firmware's Unique ID and is what duplicate, swap and identity work now run on.
- `pre25.no_serial_exists_in_the_parameter_table`, exhaustive across all 134
  parameters on all eight controllers, which is what sent the search off the
  table and onto the api sweep.
- `pre25.fault_bits_cantx_canrx`, bits 7 and 8, from a pulled CAN connector.
- `pre25.fault_bits_hardlimit_fwd_rev`, bits 14 and 15, caused and reversed on
  demand through the limit-switch polarity parameters.
- `pre25.status_frame_7_period_is_not_writable`, so `LEGACY_FRAME_MAX = 6` is a
  measured bound.
- `pre25.zero_length_frames_can_still_act`, CLEAR_FAULTS executing on a DLC-0
  frame, found by the safety net rather than by design.
- `rig-max.foreign_traffic_on_the_spark_bus`, the CTRE device at id 0 and the
  corrected throttle arithmetic.
- `max.startup_drive_gate_decodes_the_wrong_generation`, a defect in the swerve
  startup drive gate: it decoded pre-25 frames with the firmware-25 layout, so it
  could not see the fault word at all and would pass a hard-limited controller.
  Fixed, and the gate now routes through
  `admin.reading_from_raw`. Proved in the simulator and against a captured
  frame; the end-to-end refusal on a real controller held at its hard limit is
  not measured.

One more claim was settled without touching the robot at all:
`pre25.fault_bit_order`, the whole sixteen-bit ordering, carried as `[vendor]`
because four independent source classes agree on every position. A few
`[hardware]` claims marked as applying to both products also resolve for
`sparkmax` without having been measured on a MAX -- `both.status_period_unit`,
`both.chop_is_invisible_on_can` and `flex.runtime_decode_keys_on_firmware` came
off a Flex.

**Nothing in `provenance.py` is `[inferred]` or `[unverified]` for either
product**, so `uv run spark verify` exits 0 on rig-max and on rig-flex. Run it for
the current tally rather than trusting a count written here; the file is still
being added to. The three Flex claims that were open all closed on the Flex rig:
`flex.param_reads_were_probed_as_data_frames`,
`flex.param.61.limit_rpm` and `flex.param.50.polarity_encoding`. See
`docs/runs/rig-flex-parameter-reads.md`.

Two things are still open. Items that closed have been removed rather than struck
through, and the run logs under `docs/runs/` carry what was done and when.

**1. The volt scale is measured wrong, and the amp scale is still unmeasured.**
The driver uses REV's control-interfaces figures, 0.00732600 V and 0.03663003 A
per count, named as `VOLT_PER_COUNT` and `AMP_PER_COUNT` in `admin.py`.
`controller` had used /128.0 and /32.0 until, when they were
replaced on the grounds that 1/128 read about 6.6 percent high. That reasoning
assumed REV's figure was right, and nothing had measured it.

A meter now has. rig-max, at rest, motors disabled, all eight reporting 0.00 A:

    multimeter at the battery                       12.20 V
    spark voltage, mean of eight, same moment       11.544 V shown
    the scale that makes that gap zero              0.00774248 V per count

REV's published figure sits 5.4 percent below that and 1/128 sits 0.9 percent
above it. At rest the gap between battery and controller must be near zero, and
REV's scale needs a 0.66 V drop at roughly zero current. Resistance cannot make
that; it would take a series diode. 1/128 needs per-controller offset up to
+0.28 V, and this fleet already disagrees with itself by 0.26 V.

The CTRE PDP on the same bus says the same thing independently. Byte 6 of its
apis 0x052, 0x055 and 0x058 has a raw mean of 159.8, which under CTRE's published
scaling reads 11.99 V where the controllers showed 11.28 V, implying 0.0077871 V
per count.

It costs state of charge today. `rail_usable_pct` on the shown 11.544 V returns
47 percent; on the measured 12.20 V it returns 77 percent. The robot reports
itself far nearer empty than it is.

Nothing has been changed. Two things stand in the way. The meter was at the
BATTERY while the controllers sit downstream of the motor-rail breaker, so
probing one controller's own power input against its own reported volts removes
the last wiring question. And the 0.9 percent residual against 1/128 is
unexplained, so the right constant may be neither published figure. Changing
`VOLT_PER_COUNT` moves everything calibrated on it, including the SLA thresholds
and every brownout margin, so it is a deliberate change and not a constant swap.
The claim is `both.volt_scale_reads_low`, how=HARDWARE.

The AMP scale is untouched by any of this. It wants a DC clamp meter rated past
60 A, and no reading here constrains it, because a panel channel measures supply
current where a controller reports phase current. Note that SLA `empty_v` moved
from 11.8 V to 10.5 V (`battery.py:710`, see
[docs/BATTERY.md](../reference/SOURCES.md)), so a `spark voltage` SoC read against an older
note will not match, and the scale finding above shifts it again.

**2. A pre-25 CAN id change does not survive a power cycle.** Addressing itself
works: `duplicates`, `identify` and `set-id` all reach a pre-25 controller over
the api 0x094 fingerprint, and `set-id` moved one from id 3 to id 20 and back. See
[Identity](#identity-a-fingerprint-on-pre-25-a-serial-on-25-and-later).

What is left is persistence. `set_can_id` finishes with `PERSIST_PARAMETERS`,
which 24.0.1 does not carry, so the move reverts at the next power cycle. The
command says so and exits 1 rather than claiming success. Closing it means
sending the pre-25 burn at api 0x072, which this package deliberately does not
do, so a permanent id change stays a USB-C job in REV Hardware Client.

## Known landmines

- **A MAX that just rebooted has lost every setting this package wrote it.**
  Sticky `0x0200` alone is bit 9, `hasReset`, and it is the only evidence the
  reboot happened. `spark faults` decodes it correctly now; until it
  ran the sixteen-bit word through the eight-name 2025+ table and printed `clean`
  over a fleet measured, after two rail cycles on rig-max, at exactly that word.
  Read the faults, then run `uv run spark throttle`.
- **`spark duplicates` asks rather than listens on a pre-25 bus.** Since it requests the fingerprint at api 0x094 from every id in 1..62 and
  counts the distinct replies, so exit 0 there is a real result. It was not
  always: until it printed CANNOT TELL and exited 0, and until it printed a clean bill, which is the confident wrong answer this
  package exists to avoid. It costs one addressed request per id, so it wakes
  the bus, and it is not a passive read.
- **Everything this package writes to a MAX is volatile.** Parameters and status
  periods both, because nothing here sends the burn that would commit the
  parameters, and the burn does not reach the periods in any case, measured. A cold bus therefore sits at REV's defaults, 10/20/20/50/200/200
  ms across eight controllers, which is the load the boot throttle exists to
  avoid and a real risk of wedging gs_usb TX. `apply_boot_config` fixes it when
  something constructs the handler, and `uv run spark throttle` fixes it without
  one.
- **A frame on `0x02051C80 | id` carrying the magic 15011 spends a flash cycle.**
  That is the pre-25 burn, and it is a real write to real flash on 24.0.1, not a
  no-op. A zero-length or wrong payload is refused, which is why the
  empty-payload run of cost nothing, and it is the only thing
  standing between a stray frame and a flash cycle. The arbitration id is on the
  injector denylist in `tests/support/sparkhw/wire.py` and belongs there.
- **What `serials` holds on rig-max is a fingerprint, not a REV serial.**
  It is the firmware's Unique ID, read at api 0x094, and `SET_CAN_ID` accepts it
  -- but REV Hardware Client does not display it, so it will not match a case
  label or anything in RHC2, and pre-25 identify does not take it. It is unique,
  stable and read-only, which is what swap and duplicate detection need, and
  that is all it is used for here. Read-only is scoped to the two payload shapes
  that were tried, so do not treat the register as immutable.
- **`spark set-id` moves a controller and does not keep it there.** The move
  lands in RAM and reverts at the next power cycle, because `set_can_id`
  finishes with `PERSIST_PARAMETERS` and 24.0.1 does not carry that frame. The
  command prints MOVED, RAM ONLY and exits 1. A permanent id change is a USB-C
  job in REV Hardware Client.
- **A zero-length frame is not inert on this firmware.** CLEAR_FAULTS (api
  0x06E) executes on one. An api sweep, justified on the grounds
  that zero length is request semantics here, cleared the sticky fault words on
  ids 3 and 4 while the other six still carried canTx, canRx and hasReset. The
  damage was bounded only because a full parameter snapshot and a sticky-fault
  comparison were taken either side. Any future sweep has to exclude every frame
  with COMMAND semantics regardless of payload length, derived from the frame
  spec rather than from `FORBIDDEN_BASES` in `tests/support/sparkhw/wire.py`,
  which is a list of what a test may TRANSMIT and was never a safety boundary.
- **Never sweep the parameter api past 133.** 255 lands on Persist Parameters,
  and the ids between the table and 255 map to frames REV's spec does not
  define. `_legacy_param_arb` raises rather than build one.
- **A wedged gs_usb adapter does not need a physical replug.** `uv run spark
  canfix` rebinds the driver and fully substitutes for one. Note what it cannot
  do: this adapter is full-speed USB and REJECTS `restart-ms` outright, so
  SocketCAN will not auto-recover it from bus-off. Something has to run `canfix`.

## The other bus: the CANcoders

Nothing under `spark` reads this. The SPARKs are on `can.interface` and the
CANcoders are on the CANivore named by `base.cancoder_bus`, so every `spark`
subcommand is blind to half the swerve feedback path by design. That is also why
their ids may safely repeat the SPARK ids.

[`tools/cancoder_audit.py`](../tools/cancoder_audit.py) is the tool. It reads
magnet health, sticky faults and the persisted magnet offset.

Read on rig-max, ids from `devices.cancoder`:

| corner | id | magnet health | absolute position |
| --- | --- | --- | --- |
| LF | 1 | `MAGNET_GREEN` | -37.617 deg |
| RF | 4 | `MAGNET_GREEN` | +68.379 deg |
| LB | 2 | `MAGNET_GREEN` | +110.303 deg |
| RB | 3 | **`MAGNET_ORANGE`** | +172.705 deg |

RB is the outlier and orange means the magnet-to-sensor distance is outside the
ideal band. It is mechanical: magnet height, seating or concentricity, not
electrical and not a CAN problem. It will not appear in `spark faults`, `spark
audit` or any fault word on the SPARK bus, because it is not on that bus.

All four also carry a sticky bad-magnet bit and a sticky undervoltage bit
(sticky field `0x80008`). Sticky bits are history rather than the present, and
only RB reports orange live, so read them as a record of past conditions and
clear them once the mechanical check is done.

## Should you update the firmware?

Not without deciding what you are trading. This section is a literature study,
not a measurement, and it is graded as such.

**The update path exists and is not per-device.** One controller takes a USB-C
cable and acts as a USB-to-CAN bridge for the rest; REV Hardware Client's
"Update All" then flashes every out-of-date device on the bus. The CAN transport
is the SWDL frame set, `SWDL_DATA` and `SWDL_CHECKSUM` with
`ENTER_SWDL_CAN_BOOTLOADER`, all at versionImplemented 1.2.0 to 1.5.0 and so
present on 24.0.1.

**But the first hop is not.** RHC2 is the only client with a Linux build and it
cannot talk to a 2025-or-earlier device over USB at all. The bridge must first be
dragged forward through Recovery Mode, which means power fully removed, mode
button held while USB is connected, and the device erased. Only then can the
others go over CAN. Check that all eight USB-C ports and mode buttons are
physically reachable before committing, because that is also the fallback if the
bulk update leaves devices dark, which two teams have reported.

**Configuration does not survive.** REV's parameter table marks only a handful of
parameters as surviving a firmware update, and REV's own release notes tell you
to factory-reset and re-persist every device afterwards. Capture the full
parameter table off every controller first; it is the only backup that exists.

**What you gain, and it is less than it looked before.** Persist
Parameters is REV's supported way to make configuration written over CAN stick,
but the pre-25 burn on api 0x072 already commits the parameter table on 24.0.1,
so 25+ adds a documented frame rather than a new capability -- and neither one is
exposed by this package. Unique ID Broadcast makes the identity arrive on its own
instead of being requested, but `identify`, `learn-serials`, duplicate detection
and swap detection are all working on 24.0.1 already, over the fingerprint at api
0x094 and the empty-payload identify. `spark snapshot` asks for that fingerprint
on a pre-25 bus, so a broadcast identity would save it one round trip. What
genuinely changes is a `set-id` that survives a power cycle, because the move can
be burned. `spark repair` also starts working, since it refuses a pre-25 bus
where no parameter id for the period is verified. `spark provision` and the
config injection tier already write on 24.0.1 through the api class 48 dialect.

**What you would be trading, and it is smaller than it looked:** the pre-25
parameter read path on api class 48 is retired at 25.0.0. Whether 25+ firmware on
a MAX answers the newer read frames is still unmeasured, though a SPARK Flex on
26.1.6 answers all of them when the request is a remote frame carrying dlc 8. If
an updated MAX behaves like the Flex, you keep configuration auditing and gain
persistence. If it answers neither dialect, you lose the audit. Nobody has
updated a MAX here to find out.

## What collisions and congestion did not do

Under `SPARK_HW_COLLIDE`, 5831 frames transmitted onto ids real controllers own
raised no sticky fault on any of them. Under `SPARK_HW_CONGEST`, filling the bus
raised none either. Fault words stayed `0x0000` throughout both.

This is an observation about this fleet at this rate, not a guarantee. The two
tests concerned skip with that message rather than failing, because "the
threshold sits above this rate" is the honest reading. Anyone chasing a
suspected contention fault here should not assume the bus will announce it.

---

## The pre-25 fault word, and which bits are measured

Eight of sixteen bits have hardware behind them on this fleet. The other eight
are vendor-sourced and stay that way on purpose. This tally is closed: the
remaining eight are either unreachable or destructive, and the reasons below are
measured rather than assumed.

| bit | name | how it was established |
| --- | --- | --- |
| 4 | sensor | caused: Sensor Type written to a sensor the motor does not have |
| 7 | canTx | a CAN connector pulled while powered |
| 8 | canRx | the same event |
| 9 | hasReset | motor-rail cycles and |
| 12 | softLimitFwd | caused: soft limit set ahead of position, driven past it |
| 13 | softLimitRev | the reverse mirror, same run |
| 14 | hardLimitFwd | caused: polarity inverted with no switch wired |
| 15 | hardLimitRev | the same capture, caused and cleared twice |

The ordering is `0 brownout, 1 overcurrent, 2 iwdtReset, 3 motorType, 4 sensor,
5 stall, 6 eepromCrc, 7 canTx, 8 canRx, 9 hasReset, 10 gateDriver, 11 other,
12 softLimitFwd, 13 softLimitRev, 14 hardLimitFwd, 15 hardLimitRev`.

**Bit 4 needed no hardware handling in the end.** The standing plan called for
unplugging an encoder JST with the rail down. Making the controller's DECLARED
sensor disagree with the one it has does the same thing from a parameter write:
Sensor Type, parameter 4, set to 0, 2 or 3 raises `0x0010` and nothing else, and
restoring 1 clears it. No heartbeat was on the bus, so nothing could drive. That
generalises: where a fault means "configuration disagrees with reality", the
configuration half is usually reachable and the reality half is not.

**Why the other eight are not reachable, checked rather than assumed:**

- **bit 1, overcurrent.** Attempted the safe way, by LOWERING a limit rather than
  raising current. Smart Current Stall Limit written from 40 to 1, confirmed by
  readback, then driven at 0.30: 9.56 A drawn and every fault word sampled
  through the run was `0x0000`. The smart limit does not announce itself, which
  is what REV publish for both products. Not reachable without a real current
  event.
- **bits 0 brownout, 2 iwdtReset, 5 stall.** No parameter exists as a lever. The
  table has no brownout threshold, no watchdog control and no stall setting, so
  there is nothing to make disagree with reality. brownout needs the rail to
  collapse under load and this pack is already worn; stall means commanding a
  motor into a bind; iwdtReset means firmware failed to service its watchdog and
  is not inducible from outside at all.
- **bits 3 motorType, 6 eepromCrc, 10 gateDriver.** Levers exist and are
  destructive. motorType means running brushless in brushed mode, and parameter 2
  is in `PROTECTED_PARAMS` for that reason; eepromCrc means corrupting stored
  configuration on a generation where no command here can undo it; gateDriver
  survives a reflash and causing one is an RMA.
- **bit 11, other.** No documented meaning, so nothing to induce toward.

Four sources already agree on the ordering. If someone wants the last eight
settled, the route is a vendored REVLib 2024.x header in `reference/`,
not hardware.

**A refused parameter write looks exactly like a null result.** Parameter 11
Current Chop accepts 100, 60 and 120 but refuses 1.0 with STATUS 4 and keeps its
previous value. The echo's status byte is load-bearing: a caller that writes and
does not read it can believe it set a value the controller rejected, and an
experiment built on that write measures nothing. This was found the hard way,
inside the overcurrent attempt above.

Every measurement lands in `provenance.py` as a claim with `how=HARDWARE`,
the robot, the date and the evidence. A finding recorded only in Markdown has not
been recorded, because `spark verify` does not read Markdown.

## The hard limits are an interlock, and they have a direction

Measured on id 3 with the wheels free, commanding 0.15 with untreated
controls interleaved between every treated case.

| limit asserted | +duty | -duty |
| --- | --- | --- |
| none | applied 0.1515, 834 rpm | applied 0.1515, 829 rpm |
| **forward** | **applied 0.0000** | applied 0.1515, 823 rpm |
| **reverse** | applied 0.1515, 834 rpm | **applied 0.0000** |
| **both** | **applied 0.0000** | **applied 0.0000** |

Parameters 52 and 53, Hard Limit Fwd/Rev En, are REV-default TRUE and read 1 on
this fleet, so the interlock is armed out of the box and never needs writing.
What decides whether it fires is the POLARITY, parameters 50 and 51: with nothing
wired to the data port, setting one to 1 makes that open input read REACHED.

`write_param` and `write_legacy_param` both refuse 50-53 as PROTECTED_PARAMS.
The one tool that writes them is `tools/spark_limit_polarity_repair.py`, which
touches 50 and 51 only, never 52 or 53, accepts only 0 or 1, and refuses to run
while an enable heartbeat is on the bus:

```
uv run python tools/spark_limit_polarity_repair.py --inject 3 --cycles 1
```

The frame underneath is the legacy parameter write, arbitration id
`0x02050000 | ((0x300 | pid) << 6) | dev` carrying `<int32 little-endian> + 0x03`.
Confirm with the fault word: `0x4000` forward, `0x8000` reverse, `0xC000` both.
The write is RAM only and a rail cycle undoes it.

**One limit is an interlock. Both together are a real, latching stop.**

An earlier version of this section called the whole thing "not an e-stop" on the
strength of the direction alone. That argument was too weak, because anyone using
it as a stop asserts both, and both were then measured in the scenario that
actually matters: asserted while the motor was already turning at 811 rpm.

| | applied reaches 0 | wheel at rest | latches |
| --- | --- | --- | --- |
| both limits, asserted mid-motion | 87, 88, 90 ms | 490-592 ms | **yes** |
| arbitration id 0 with the heartbeat stopped | 31 ms | ~500 ms | no |
| heartbeat left to lapse | 220 ms | ~1 s | n/a |

It then held through ten seconds of alternating +/-0.15 at peak applied `0.0000`,
and neither 0.30 nor 0.60 in either direction broke through.

The last column is the interesting one. Arbitration id 0 is faster but does NOT
latch: the next enable heartbeat overrides it, which is a defect found the same
day. The limit stays asserted until something writes it back.

**What still disqualifies it as THE stop** is not the direction. It is addressed
per controller, so a controller whose id is wrong or duplicated is missed
entirely, where arbitration id 0 reaches it in one unaddressed frame. It is two
writes per controller rather than one broadcast, so latency scales with the
fleet. The write is unacknowledged on this generation. And like every other CAN
path it needs the bus and the software working at the moment it is needed.

None of the three is a safety device. The motor rail is.

## How long the software stop actually takes

Measured on id 3, from a steer motor turning at 823 rpm, with the
setpoint STILL being commanded throughout so the stop had to win on its own.

| stop | applied reaches 0 | wheel at rest |
| --- | --- | --- |
| `broadcast_disable()`, heartbeat left running | **never, 2 s** | **never, still 823 rpm** |
| `broadcast_disable()`, heartbeat stopped with it | 31 ms | about 500 ms |
| heartbeat stopped alone | 220-245 ms, median 243 | about 1 s |

**The first row was a defect and is fixed.** `SparkBus` asserts enable every
20 ms from its own thread, so a disable sent underneath it was overridden before
the next status frame and the motor simply kept running. `broadcast_disable` now
clears `heartbeat_enabled` BEFORE sending, so the last enable is older than the
disable; pass `stop_heartbeat=False` only to send the frame for its own sake.
Re-measured after the fix: applied zero at 36 ms, wheel at rest by about 600 ms.

Two things to take from the numbers. The disable frame is worth roughly 190 ms
of stopping distance over letting the heartbeat lapse, so it is worth sending.
And **the heartbeat timeout on this fleet is about 240 ms, not the 100 ms the
FIRST specification states** -- the measurement floor is one STATUS_0 period
plus up to 20 ms of heartbeat phase, around 60 ms, far short of the 143 ms gap.
Design a stop around 250 ms, not 100. The heartbeat that matters here is the
SECONDARY one, `0x02052C80`; 24.0.1 ignores `0x01011840` entirely.

Nothing in the suite called `broadcast_disable` before this, which is how a stop
that did not stop went unnoticed. `tests/unit/test_stop_path.py` covers it now,
including the ordering.

**The BOOL hazard, which is worse than it looked.** On 26.1.6 a BOOL parameter
accepts any value, so writing 2 to parameter 50 returns Success and echoes 2.
While the echo was the only instrument, that read as the echo lying. Reading the
parameter back showed the firmware genuinely STORES the 2, and
that with parameter 50 at 2 the forward limit reads not reached while parameter
51 at 1 holds the reverse limit asserted. So an out-of-range polarity write
lands on the permissive side and reports success. On a robot whose data port
needs polarity 1 that silently releases the interlock, which is why every
polarity write is now read back. `base.limit_switch_polarity` stays in the robot
config as the record of intent, because the read says what a controller holds
and not what it should hold. Both generations can raise or clear a limit over
CAN alone with no data-port hardware, and both now confirm it by reading the
parameter back. For a bench guard, a test fixture or any interlock you want to
assert and then VERIFY from software, either generation will do.

## When a fleet takes every setpoint and applies nothing, all eight controllers applied exactly `0.0000` to every setpoint for
a whole session. Active faults `0x0000`, no limit asserted, rail at 11.1 V, the
duty and heartbeat frames confirmed on the wire against REV's own spec file, and
telemetry fresh to 14 ms. The LEDs blinked magenta, which the failure catalogue
calls "no valid signal". A motor-rail power cycle fixed it.

**The only field that differed is byte 6 of LEGACY_STATUS_0**, `0x54` while dead
and `0x10` since. The startup drive gate now refuses a pre-25 controller whose
byte 6 is not `0x10` and names the rail cycle, because every fault-based check
passes in that state and cleared the fleet.

What has been eliminated as the cause, each by measurement rather than argument:

| candidate | verdict |
| --- | --- |
| configuration drift | ruled out, nine parameters written with readback confirming each |
| the sticky fault word | ruled out, ids 3 and 4 differed there and behaved the same |
| output or limit state | ruled out, byte 6 unmoved by driving or by asserting limits |
| the limit switch inputs | ruled out, a controller with no heartbeat still faults on a limit, so the dead state's clean word means the limits genuinely were not tripped |
| a latched `broadcast_disable` | ruled out, arbitration id 0 does not latch here |
| a Universal Heartbeat lock | ruled out, see below |

**Firmware 24.0.1 ignores `0x01011840` entirely.** Five payloads were put on the
bus at 20 ms against a driving fleet, including all-ones, which is the superset
of every single-bit payload. Nothing enabled, disabled or locked, and byte 6
never moved. So the enable on this generation really is the Secondary Heartbeat,
`0x02052C80`, that `SparkBus` already sends, and REV's "locked to the Universal
Heartbeat" wording is not reachable from the bus here.

The cause is unknown with six things crossed off. Bits 2 and 6 of byte 6 remain
unseparated, because separating them needs a second transition into a state
nobody can yet produce on purpose. The detector does not depend on knowing why.

## Byte 6, and why its old decode was wrong

The pre-25 branch of `print_diagnostics` read byte 6 as `0x01` follower, `0x10`
inverted, `0x20` heartbeat lock and the model in bits 6-7. That is the
FIRMWARE-25 STATUS_0 byte 6, byte for byte. The same generation mix-up as the
drive gate decoding a legacy buffer with the firmware-25 pair, in a second call
site. It reported a model that changed across a power cycle, which a model cannot
do.

Nine parameters were written on id 3, each confirmed by reading it back:

| bit | mask | meaning | how |
| --- | --- | --- | --- |
| 0 | `0x01` | REV name it Is Follower | vendor; writing 57 and 58 did not set it |
| 1 | `0x02` | **Inverted, parameter 45** | **measured, caused and reversed** |
| 2 | `0x04` | set only on a fleet that would not drive | unplaced |
| 3 | `0x08` | never seen set | unknown |
| 4 | `0x10` | set on every controller in every state | unknown |
| 5 | `0x20` | never seen set | unknown |
| 6 | `0x40` | set only on a fleet that would not drive | unplaced |
| 7 | `0x80` | never seen set | unknown |

Control Type, Idle Mode, both soft-limit enables, Sensor Type, Voltage
Compensation Mode, Follower ID and Follower Config all wrote successfully and
none appears in this byte. `decode_legacy_status_0` returns `inverted` from bit
1; nothing else in the byte is decoded, on purpose. Do not confuse parameter 45
with `base.swerve.reverse_speeds`, which is a software sign flip in the drive
layer and writes no parameter.

## The adversarial tier now runs on both generations

`tests/adversarial/` built rig-flex and only rig-flex, so every scenario in the
config-write, recovery and identity tiers was a firmware-25 scenario and the MAX
path through `spark repair`, `spark audit`, `spark clear` and `spark set-id` had
no adversarial treatment. That mattered because every generation defect this
package has shipped was a Flex assumption reaching a MAX, and none of them could
fail a test that only ever built a Flex.

`tests/adversarial/test_pre25_tiers.py` runs the same SCENARIOS against a pre-25
fleet: seventeen tests over the premise, config write, recovery and identity. It
is not a dialect test -- `test_sparkmax_bus.py` and `test_pre25_persistence.py`
already cover the dialect. What is new is the CLI behaviour on the other
generation. Ask for the `rig-max` bus and the `pinned_to_max` fixture together
and the simulated robot and the declared robot are the same machine again, with
rig-max's real ids and its api 0x094 fingerprints.

**The fixture needed fixing before any of it meant anything.** A "healthy" fleet
has to mean the same thing on both generations and it did not. The Flex fixture
never had to hold a declared value for an audit to find, because the 25+ reads
were going out in a silent form. Pre-25 answers reads and the audit compares
them, so an unseeded controller is one that has lost its configuration, and a
tier built on it would have asserted that a fleet at factory defaults audits
clean. Both fleet builders now seed the declared settings from the same file the
audit compares against, floats as their IEEE bits and enum names as the ordinal
the controller stores. `build_fleet` leaves Status 0 and 1 Period to `spark()`,
so a fixture cannot claim a period the wire contradicts.

Both halves were checked by mutation. Scoring coverage against the Flex list
again fails the tier; so does deleting the pre-25 parameter read, which the first
fifteen tests did NOT catch until two more were written for it.

## The fingerprint is not the identify serial, watched on an LED

The one question in this tree that no amount of code could answer. api 0x094
returns a stable per-device value on pre-25 and `IDENTIFY_UNIQUE_SPARK` on
firmware 25+ takes a serial in its payload, so it was worth knowing whether they
are the same value. If they were, identify and serial-addressed `set-id` would
both work on this generation.

They are not. One controller was watched, id 3, the right-back steer, through
three windows with ten seconds of silence between them:

| window | frame sent | LED |
| --- | --- | --- |
| addressed form, `02051D83 [0]` | pre-25 identify | **fast blink** |
| broadcast form, `02051D80 [4] EA BE 4C 2E` | that controller's own fingerprint | standard idle blink only |
| addressed form again | pre-25 identify | **fast blink** |

The identify signature is distinguishable from the resting blink, which is what
makes the middle window a negative rather than a missed observation, and the
control fired on the same LED either side of it. Both frame forms were confirmed
on the wire before the run.

So `read_fingerprint` returns a device fingerprint and not a REV serial, and
pre-25 identify is addressed by CAN id only. What the fingerprint DOES solve is
the problem it was wanted for: two controllers sharing a CAN id are invisible to
an id scan, and the fingerprint is requested per device rather than broadcast, so
it tells twins apart.

Getting the observation took three attempts, and the first two failed for the
same reason: the operator was reported "nothing blinked" while the run was still
in its lead-in. A timed sequence the observer has to correlate by clock is a bad
instrument. What worked was one controller, one variable, and a control window
either side.

## What rig-flex would settle

All four ran on rig-flex and all four are closed. The run is
`docs/runs/rig-flex-parameter-reads.md`; the claims they
produced are in `provenance.py` and `uv run spark verify` reads them.

- **Does a parameter read answer on 26.1.6 when sent as a genuine remote frame?**
  Yes, and the form matters more than the question assumed. This bullet asked for
  `is_remote_frame=True, dlc=0`, and that is silent. Only `dlc=8` answers, on all
  eight read classes, for every parameter 0-255. A full sweep of all eight
  controllers returned zero silent frames. So `spark audit` now checks every
  declared deviation instead of one, and it found two things nobody could see
  before. id 12 held four status periods at REV's factory default where
  `the sparkflex: block` declares otherwise, and all eight hold 4096
  for Encoder Counts Per Rev where the declared file asked for 409.
- **Does the unwired-input polarity trick raise a limit on 26.1.6?** Yes, and the
  reason this bullet called it harder no longer holds. Polarity reads back now.
  On id 17, a steer whose data-port inputs are unwired, writing 1 to parameters
  50 and 51 asserted both hard limits, writing 0 cleared them, and the restore
  was proved by reading the parameter rather than trusting the write echo. That
  also settles the 0/1 encoding: 0 is normally closed.
- **Does firmware 25+ have an equivalent of the state where a controller takes
  setpoints and applies nothing?** Not on a healthy fleet. One steer commanded
  0.15 for 0.25 s applied exactly 0.1500 and drew 17.11 A, while the other seven
  controllers on the same bus and the same heartbeat held 0.0000 throughout. That
  confirms the healthy path and does not prove the pathological state cannot
  occur, which no single run could.
- **Is `0x01011840` on rig-flex's bus?** No. Thirty seconds of passive listening
  caught 36156 frames across 24 arbitration ids and neither the FIRST universal
  heartbeat nor the arbitration-id-0 disable broadcast appeared. rig-flex carries no
  roboRIO and nothing in this package sends either. The driving half ran the
  same day, with one steer commanded 0.15 for 2.5 s while a second reader
  counted every arbitration id. That listen caught 6294 frames across 42 ids,
  and `0x01011840` never appeared. Arbitration id 0 did appear three times, and
  those three were this package's own `broadcast_disable` teardown.

**One of the two findings was a typo in the declared file.**
`the sparkflex: block` gave Encoder Counts Per Rev and Alt Encoder
Counts Per Rev a value of 409 and marked both deviating, while all eight
controllers hold REV's default of 4096. The SPARK MAX file declares 4096 for the
same two ids and marks them non-deviating, no code reads either number, and 409
is not a count-per-rev any REV encoder has. Corrected to 4096 with `deviates:
false`, which took the audit from twenty findings to the four that were real.
Follower Mode Leader Id (194) moved the same way in the same pass, since its
declared value already equalled the factory default. All eight controllers
matched it, so the audit read that id as provisioned. The file now declares
9 deviating settings out of 46, and its `meta` block records that count.
Revert by putting 409 back in that file if the deviation was ever meant.

**Both are closed.** id 12's four status periods went back to 50,
200, 200 and 250, each read back and then committed with `spark persist`. Duty
Cycle Sensor Prescaler went from REV's 17 to the 7 the other seven hold, and it
persisted with them. The audit now reports no problems across eight
controllers. The fleet-wide parameter diff fell from six to one, parameter 13,
the intended steer-against-drive gain split.

`spark provision --id N [--write] [--persist]` landed with it, which is the
command the audit remedy had been naming with nothing behind it. It reads all 46
declared settings off one controller and reports what disagrees. `--write` sends
the corrections and reads each one back, because the echo is not evidence, and
`--persist` burns once at the end. A run without `--write` stops after the report
and exits 1 when anything needed writing. A drifted PROTECTED parameter stops the
whole run before any write, and the command prints each one with the reason it
refuses.

**Parameter 153 was settled by a write.** id 12 held REV's default of 17 for Duty
Cycle Sensor Prescaler where the other seven held 7. It went back to 7 with the
four status periods and was persisted with them. REV's table says the duty-cycle
sensor sets its time base to the device clock divided by (value + 1). Nothing on
rig-flex uses a duty-cycle sensor, so the setting is inert here. The id is still
undeclared, so `spark snapshot` records it in the baseline among the undeclared
ids. `spark audit` reports a later disagreement there as a note. Declaring 153 in
`the sparkflex: block` would move it under the audit.
