# Adversarial testing for the SPARK bus

Every claim this driver makes about a SPARK is tested against a bus that can be
made to fail on demand. This document covers how that works, what it has caught,
and what to read first when a robot misbehaves in the field.

The suite was built against a SPARK Flex bus on firmware 26.1.6. Most of it holds on
a SPARK MAX on 24.0.1, and the parts that differ split by firmware, so every claim
here that turns on firmware names the firmware. The parameter dialect is the split
that shapes most of what follows. Firmware 25+ reads over READ_PARAMETER, writes
over PARAMETER_WRITE and commits with PERSIST_PARAMETERS, while 24.0.1 carries both
directions on api class 48 and commits at api 0x072, which nothing here sends. Where
it says SPARK Flex or 26.1.6, do not read it as SPARK MAX.

This is the overview. Three companion documents carry the detail and are linked
where they apply: [tests/adversarial/README.md](../tests/adversarial/README.md) for
the simulator, [tests/hardware/README.md](../tests/hardware/README.md) for the
injection tiers, and [tests/README.md](../tests/adversarial/README.md) for the suite layout.

---

## 1. What this caught

SPARK Flex firmware 26.1.6 answers a parameter read only when the request is a
genuine remote frame carrying dlc 8. This package sent zero-length data frames
until, and those are silent, which is why the tree recorded the
silence as a property of the firmware. Measured on rig-flex and written up in
`docs/runs/rig-flex-parameter-reads.md`; the earlier reading
is in `docs/runs/rig-flex-probe-log.md`.

One scope on that, and it came out narrower than the doubt was. A genuine
remote frame is necessary and not sufficient, since one carrying dlc 0 drew
silence too. Requests at dlc 1 through 7 were never sent, so dlc 8 is a form
that works and not provably the only one. GET_FIRMWARE is rtr with lengthBytes
8 in the same spec, and it answers at dlc 0. So the dlc rule holds for the
parameter read classes alone. The claim is
`flex.param_reads_were_probed_as_data_frames` in `provenance.py:381-437`,
how=HARDWARE.

The pre-25 half is settled too, and it took a second run to earn it. rig-max had
been sent two of these frames, one read-value and one types, each in two forms,
and both remote sends carried dlc 0. That is a known-silent form on a Flex, so
those sends could not tell an absent frame from a badly formed one.
`tools/spark_read_frame_form.py --id 3` sent both apis in all three forms at
rig-max and all six drew silence. So "25+ only" is measured across
the form axis.

That single fact shaped everything on a Flex bus for the thirteen days this tree
believed it. While the reads went out as zero-length data frames, nothing answered,
so the tooling could only watch broadcasts and infer. Every configuration claim it
made about that fleet was an inference, and a wrong inference looks exactly like a
right one. The failure mode there is a clean bill of health over a broken robot.

That is not hypothetical here. Before this work, `spark audit` printed
`no problems found` and exited 0 for a bus where every controller was broadcasting a
latched fault, against a `--help` that promised "exit 1 on any fault". The same
information was sitting in `spark faults`, which printed it in a table and did not
count it toward its exit code either.

An ordinary test suite cannot find that. The decode function was correct, its unit
test passed, and `cmd_audit` simply never called it.

### The constraint is the firmware, not the SPARK line

Read the opening sentence again and note what it names: a firmware version. The rest
of this document was written as if it named the product.

Measured on rig-max, on eight SPARK MAX running firmware 24.0.1,
generation `pre25`, over a 1 Mbit gs_usb adapter named `can0`: **all 134
parameters, ids 0 through 133, answered a read on all eight controllers.** None
refused, none went silent. The same api answers a write, and the device echoes the
value it took.

```
arb = 0x02050000 | ((0x300 | param_id) << 6) | device_id    # api class 48
  read   DLC 0                        -> reply on the SAME arb id
  write  5 bytes [int32][type tag]    -> reply on the SAME arb id
  reply  [uint32 value][type tag][status byte]     status 0 = ok
  type tags: 0 int32, 1 uint32, 2 float32, 3 bool
```

`SparkAdmin.read_legacy_param` and `SparkAdmin.write_legacy_param`, both in
`sparklib/admin.py`, speak it. Parameter 0 returns each device's
own CAN id, which is the self-check that separates a real read from an echo of the
frame just sent.

Two boundaries, both measured, both load-bearing:

- **Stop at 133.** `admin.LEGACY_PARAM_MAX = 133` is a guard, not a guess.
  Past the end of the table the same api range carries commands, 242 burn-flash and
  255 persist, so a sweep that runs off the end starts issuing them. Those two are
  reached through the parameter arbitration and are not the pre-25 burn-flash at api
  0x072 described below; do not read them as the same frame.
  Parameters 158 to 165, which are the status periods on 25+, reply with a non-zero
  status: on pre-25 they are not parameters at all, and the periods move on api
  class 6 instead.
- **A plain write is RAM only, and a burn commits it.** Idle Mode was set to BRAKE on
  all eight and read back as BRAKE, the motor rail was cut and restored, and all eight
  came back COAST, which is the factory default. Flash-resident provisioning was
  untouched by the same cycle: the drive P of 2.0, the steer P of 0.1 and the 40 A
  stall limit all survived. What a write alone does not do, the pre-25 burn-flash
  does. api 0x072, arbitration id `0x02051C80 | id`, carrying the magic 15011 as two
  little-endian bytes, commits the parameter table on 24.0.1: measured on rig-max with a rail cycle and an unburned control, and written up in step 6 of
  section 6. It commits ids 0 to 133 and NOT the status periods, also measured.
  Nothing in this package sends it, so what this tooling tells a MAX is still gone at
  the next cycle.

The Flex reading above was corrected, once a remote frame with dlc 8
drew answers out of 26.1.6. The generalisation built on top of it fails for a
second reason too, since pre-25 answers a different api class.

### What that changes about an audit

Configuration drift is measured on both generations now, and the dialect
follows the firmware. Pre-25 answers api class 48 over parameters 0-133, while
25+ answers READ_PARAMETER over all of 0-255.

All 134 parameters were read from all eight rig-max controllers and exactly one
differs across the fleet: parameter 13, `P 0`, at 2.0 on the drive ids (1, 4, 5, 8)
and 0.1 on the steer ids (2, 3, 6, 7). The other 133 are byte-identical on all eight.
Two values deviate from REV's default and both are deliberate: that P, and parameter
59 `Smart Current Stall Limit` at 40 against REV's 80. "This fleet has zero
configuration drift" is a sentence with no inference in it.

rig-flex now produces that sentence too, because a fleet-wide read
compared all 185 implemented parameters across the eight controllers. Six of them
differed, and five of the six sat on id 12, which had missed part of its
provisioning. Those five were written back and burned, so the fleet now differs on
parameter 13 alone, the intended steer-against-drive gain split. `coverage_note()`
still earns its place, and what it lists has moved from what the firmware refuses
to what a run did not read (`admin.py:2470-2485`).

`spark audit` now reads the table on both generations. `cmd_audit` picks
`legacy_deviation_problems` or `modern_deviation_problems` from the generation the
bus reports, then compares each declared deviation that dialect can address
(`cli.py:1985-1998`). Pre-25 leaves out the status periods, which are not
parameters there, and `coverage_note()` still says what a run did not read.
`audit_problems` itself takes plain data and scores identity, cadence and faults
(`admin.py:2144-2150`). The parameter comparison sits beside it in the
command, so the pure scorer stays testable without a bus. `spark params` reads both
dialects too, 0-133 on pre-25 and 0-255 on 25+ (`cli.py:926-1002`).
`spark provision --id N --write` puts back what the audit found drifted.

### The failure mode this suite is named after is running on rig-max

`spark faults` was run on rig-max immediately after a rail cycle and
reported all eight controllers clean. All eight had just rebooted. Every one was
carrying sticky `0x0200`.

The pre-25 fault word is sixteen bits, filed twice in bytes 2..6 of `0x060`, active
in the low half and sticky in the high half, and `hasReset` is bit 9. That is
confirmed by three motor-rail cycles: every time, every controller came back at
sticky `0x0200` with no other bit set, and a rail cycle is precisely the event that
sets hasReset and nothing else. `admin._FAULT_BITS` is the 2025+ eight-name
STATUS_1 table, the sixteen-bit legacy word was being scored against it, and bit 9
was past the end.

**The decoder is fixed.** `admin._LEGACY_FAULT_BITS` names the sixteen pre-25
bits (`admin.py:846-850`), `normalised_reading` scores the legacy word against
it on the `GEN_PRE25` path (`admin.py:1491-1497`), `_bits` walks the full width
and emits a synthetic `bit12` for a bit no table covers rather than dropping it
(`admin.py:853-860`), and `classify_reset` searches sticky FAULTS as well as
sticky warnings (`admin.py:190-206`). All four are exercised against the live
bus by `tests/hardware/test_legacy_fault_injection.py`, which transmits a legacy
fault word from this host and reads it back through the driver.

**The ordering is vendor-sourced, not a project guess.** Four independent source
classes were swept and agreed on all sixteen positions: REVLib 2024.2.4's own
`FaultID` enum in `CANSparkBase.h` and `CANSparkBase.java`, REV's
`SPARK-MAX-Types.proto`, the REV Hardware Client fault list on 24.0.1, and
third-party re-implementations of the protocol. Three of the sixteen are measured
on top of that. Bit 9 hasReset came from three motor-rail cycles on rig-max, each leaving all eight controllers at sticky `0x0200` and no other bit
set. Bits 7 canTx and 8 canRx came from a CAN connector pulled off a powered fleet: the segment went silent, the gs_usb adapter went ERROR-PASSIVE, and
when the connector went back all eight read sticky `0x0380`, which is bits 7, 8 and
9 exactly, with hasReset already latched from an earlier cycle. It was not a
deliberate experiment -- it happened during an unrelated test -- but it is the
confound-free kind: one event, two new bits, and the two bits that event predicts.
[docs/SPARKMAX-BRINGUP.md](SPARK-MAX-BRINGUP.md) covers the other thirteen
and concludes most of them should not be caused.
Nothing was vendored to settle it: `reference/` holds REVLib 2025.0.3 and
2026.0.2 and no 2024.x header, and the vendored `REV-spark-frames-2.1.0.json` still
carries the legacy field as one opaque 32-bit signal. The sources are cited rather
than copied, in the `pre25.fault_bit_order` claim in
`sparklib/provenance.py`. REVLib 1.1.5 and earlier called
bit 2 `kOvervoltage` without moving it, so an old decoder mis-names that position
rather than mis-placing it. One conflict resolves against the ordering:
[docs/FIELD-REPORTS.md](FIELD-REPORTS.md):479 puts overcurrent at bit 11,
and it is bit 1. The table at
[docs/FAILURE-CATALOGUE.md](FAILURE-CATALOGUE.md):382-386 was right.

**Two consumers are still open.** Pre-25 has faults and sticky faults and no
warning field, so `hasReset` decodes into `sticky_faults`, and the `GEN_PRE25` branch
of `normalised_reading` returns `"sticky_warnings": []` unconditionally. Three
consumers read the right list now: `spark faults` prints `sticky_faults` under a
`sticky-FAULT` tag, `spark clear` hands both lists to `classify_reset`, and
`audit_problems` builds its `rebooted` set through `classify_reset` too, which
searches sticky FAULTS as well as sticky warnings -- keying on `sticky_warnings`
alone made that set empty by construction on the one generation where a reboot
costs the whole configuration, so every branch guarded by it was unreachable there.
Two still read `sticky_warnings` alone and so see nothing on a MAX: `spark voltage`
prints `has-reset` from it, and `RailStatus.reset_ids` in
the host application's power monitor is filled from it.

That gap costs more on a MAX than it would on a Flex. Every parameter this package
writes over CAN to a MAX is volatile, because nothing here sends the burn that would
commit it, so `hasReset` is the only signal that the configuration it applied is gone
and has to be re-sent. The decoder no longer discards it and `spark audit` reads it
now, on the command whose whole purpose is to not report a broken robot clean. What
still cannot see it is the rail view: `spark voltage` and the battery monitor's
`RailStatus`.

## 2. What was built

Four layers, each answering a question the one below it cannot.

| layer | tests | what it answers |
|---|---|---|
| `tests/support/sparksim` + `tests/adversarial/test_simulator.py` | 92 | is the simulator itself faithful? Encoders round-trip against the driver's decoders, frame bases agree with `admin`, the clock and scheduler behave |
| `tests/adversarial/` (eighteen more modules) | 283, low since the modules landed | replays a real reported field failure against a simulated bus and reads it back through the real driver |
| `tests/unit/test_spark_audit_guards.py`, `test_status0_plausibility.py` | 34 | live guards on behaviour the tooling already has, including call-site discipline |
| `tests/hardware/` | 87 | the same failures injected into a real 8-motor SPARK bus, plus the staged ones a person has to cause by hand |

Those four rows sum to **496 tests**, of which 13 carry an `xfail` marker and
constitute the open defect list: seven in `tests/adversarial/`, six in
`tests/hardware/`.

Every count in the table was regenerated with `pytest -q --collect-only` on rig-max, not remembered. The hardware row is the one that moves: two modules
landed there since the last pass, `test_legacy_period_write.py` and
`test_legacy_fault_injection.py`, and the config-injection tier was rewritten to run
on both generations. An earlier version of this table read 92 / 150 / 31 / 76 under a
line saying "Total for the SPARK work: 450 tests", which never matched its own table:
those four rows summed to 349. The wider figure, if that is what was wanted, is 761
-- `tests/adversarial/` 375, `tests/hardware/` 87, and the seven SPARK-specific files
in `tests/unit/` 299.

### The source material

Findings come from evidence, not from imagination, and the evidence is in this
repository:

- [docs/FIELD-REPORTS.md](FIELD-REPORTS.md) grades 158 findings drawn
  from a corpus of 2350 harvested topics, 1440 of them fetched
  (`docs/FIELD-REPORTS.md:22`). Every finding carries its
  thread URL, and every adversarial test that replays a field failure cites that
  URL in its docstring.
- [docs/FAILURE-CATALOGUE.md](FAILURE-CATALOGUE.md) distils those
  threads into numbered entries in classes A through G, each with a
  disposition.
- [reference/](../reference/) holds REV's frame spec, parameter
  table and the REVLib headers, verbatim.
- [docs/runs/](runs/) writes up each hardware session, including the probe log, which records every measurement rather than only the
  conclusion, and the limit-polarity incident in full.

The raw captures behind those write-ups stay out of the repository. Candumps and
current traces run to hundreds of megabytes and none of it is source.

`tests/support/sparkhw/catalogue.py` carries all 44 catalogue entries with their
disposition, and `tests/unit/test_spark_hardware_catalogue.py` fails if an entry
loses it. Thirteen are refused on rig-flex for stated reasons; the SPARK CAN chain
there is soldered, so a contiguous break cannot be produced.

## 3. Why a device simulator and not a frame replayer

The confirmed defects are all disagreements between what the driver concluded and
hidden device state. `SimSpark` models `ram` and `flash` as separate dicts, tracks
the value a controller *echoed* against the value it *committed*, and logs writes,
persists, clears and id changes.

That is what makes a test like "persist flashed the value the write replaced, and
reported Success" expressible at all. A replayer can produce the frames; it cannot
express the disagreement, because the disagreement is about state no frame carries.

The simulator also runs on a `VirtualClock` substituted for `admin.time`, so a
5 s inventory window, a 2 s post-flash blackout and a 3.6 s stall cost microseconds
and nothing sleeps. On the adversarial suite ran 375 tests in 91 s on
rig-max. The provision and parameter-read modules landed, so
re-collect before quoting either figure. This sentence read "the whole 349-test suite runs in about 30 seconds";
what that 349 counted is not recoverable, since it is also what the layer table above
summed to while its rows were wrong, and that table includes the hardware tier, which
does not run without a robot.

## 4. An xfail is the deliverable, and a plain pytest run hides when one is fixed

**An xfail is the deliverable, not a disabled test.** It is a named driver gap with
a live reproduction attached, asserting the behaviour an operator needs. Fixing the
defect turns it green with no test edits, which is the suite's acceptance criterion.

Every one is `strict=False`. That is deliberate: a fix must not break the suite. It
has a cost that bit this project hard enough to be worth stating plainly.

> **A fixed defect turns its xfails into XPASS, and a plain `pytest -q` never prints
> them.** Always run `pytest -rX` before judging whether a test file still earns its
> place.

On an audit read `tests/unit/test_spark_adversarial.py` as 48 tests with
46 xfails, "documented gaps guarding almost nothing", and it was deleted. Restoring
it produced 13 XPASSes: the D1 fix had converted them to live coverage weeks after
the audit's snapshot, silently. Its sibling `test_spark_field_failures.py` had zero
xfail markers and was recorded as 22 passing guards; on restore, 15 of the 22 failed
because they assert capabilities (`SparkAdmin.scan`, `clear_faults(verify_seconds=)`,
`audit_problems(probes=)`, `max_gap_ms`) that have never existed on this branch.

Counting markers is a static property standing in for a behavioural one, and it was
wrong in both directions. Run the tests.

## 5. How this helps development

**It converts a silent wrong answer into a red test.** The four defects that got
past fully green suites in this project were all at call sites: a popup behind an
early return, a verdict string read for truthiness at one of two call sites, a field
passed to a vendored type that lacked it, and a guard a test re-implemented instead
of reading. `tests/unit/test_spark_audit_guards.py` and the AST checks in
`test_status0_plausibility.py` read the call site itself, so moving a check out of
the path fails the build rather than the robot.

**It gives every finding a next step.** `admin.BIT_REMEDIES` is one table
keyed by the bit names the decoder emits, read by both `spark audit` and
`spark faults`, so a condition cannot be explained in one command and left bare in
the other. Three tests hold the table to that, including one that reads the call
site. They hold it for the 2025+ names only: both completeness guards compare
`BIT_REMEDIES` against `_FAULT_BITS | _WARNING_BITS`
(`tests/unit/test_spark_audit.py:274-280`,
`tests/unit/test_status0_plausibility.py:211-217`), and eight of the sixteen pre-25
names have no entry -- `canRx`, `canTx`, `eepromCrc`, `hardLimitFwd`, `hardLimitRev`,
`iwdtReset`, `softLimitFwd`, `softLimitRev`. Neither command goes silent on those:
`spark faults` prints "no remedy recorded for this bit yet"
(`sparklib/cli.py:784-787`) and `spark audit` falls back to the
category-level advice (`sparklib/admin.py:1544`). What is missing
is the specific next step, and nothing fails while it is.

**The xfail list is the roadmap.** `uv run python tests/adversarial/run_suite.py`
groups the seven open xfails in `tests/adversarial/` under the driver defect each
names, so the work list is generated from the tests rather than maintained beside
them. It runs that directory only, so the six in `tests/hardware/` are not in its
table.

**It catches the fixer's own mistakes.** The saturation verdict added
initially flagged `spark_model` outside `{1, 2}` as invalid, which REV-Specs does not
support -- it records only that 1 is a SPARK Flex. That false positive sat before a
`continue` and silenced three real findings behind it. The restored suite caught it
within minutes, and a recovered test also specified the better interface: the
`implausible` list names decoded keys (`voltage_v`, `current_a`, `motor_temp_c`) so
a caller can act on it without parsing prose. `spark_model` is a 25+ field in any
case: pre-25 `LEGACY_STATUS_0` carries no model, so `normalised_reading` returns
`None` for it on that generation and the fleet-comparison check that consumes it has
nothing to compare on a MAX bus.

**Mutation checking is the standard for anything that passes.** Break the driver
line the test claims to cover, confirm it goes red, restore. One pass at a time on a
working tree, judged by process exit code -- never by grepping stdout for "failed",
because a project that sets `-q` suppresses the summary line.

## 6. Deployment triage: what to look at when a robot misbehaves

Run these in order. Each is read-only unless it says otherwise, and none sends a
setpoint or starts the enable heartbeat, so controllers stay disabled.

### Step 1 -- is the bus there at all?

```bash
bash system/check-can-setup          # read-only doctor
uv run spark status
```

Zero RX with zero bus errors on an UP, ERROR-ACTIVE interface means the adapter is
fine and nothing is answering on the far end. If the CANcoders are healthy and the
SPARKs are missing, check motor power first: CANcoders run off the CAN/logic rail
and SPARKs off the motor rail, so a breaker or e-stop silences only the SPARKs.

**On rig-max the bus is never quiet, and none of that traffic is a SPARK.** A CTRE
power-distribution device shares `can0` at device id 0, broadcasting ten
status frames at 40 Hz each for a constant 404 frames/s, decomposed by arbitration
id. `inventory()` filters on manufacturer 5 and device type 2, so
`spark status` correctly never shows it, and `spark throttle` does not touch it. It
corrects the throttle arithmetic: the boot throttle cuts the CONTROLLER
contribution 1872 -> 384 frames/s, which is 4.9x, while TOTAL bus load goes 2276 ->
788, which is 2.9x. The device is the CTRE PDP 4.0, identified by device type 8 and
manufacturer 4 and confirmed by the operator as belonging on that
bus. `spark.yaml` names no power-distribution device because nothing here
commands one. That traffic is not inert: driven, byte 4 of its api 0x056 tracks
total SPARK current at r = +0.88, so a capture taken on an idle robot understates
what the panel reports.

**A silent bus is often asleep, not dead.** After a rail cycle this fleet comes back
powered, answering requests, and broadcasting nothing. One `GET_FIRMWARE` addressed
to a single controller brings all eight back, with sticky `hasReset` intact.

`spark status` only helps once at least one controller is already broadcasting: it
builds its inventory from broadcasts and queries firmware only for the devices in
it (`cli.py:279-290`), so on a wholly silent fleet it queries nobody. The
read-first wake path there is `spark clear`, which captures the sticky words to
`records/` before it erases them. Read that capture; it is the only copy.

### Step 2 -- what does the fleet say about itself?

```bash
uv run spark status        # inventory with identity and firmware
uv run spark faults        # every fault and warning bit, with a remedy each
uv run spark audit         # config drift and duplicate ids; exit 1 on any fault
uv run spark voltage       # per-controller rail against pack state of charge
```

`spark audit` and `spark faults` must agree. If they disagree, that is the D1 class
of defect returning, and
`tests/hardware/test_spark_audit_and_spark_faults_agree_about_a_fault_the_fleet_really_has`
is the test that pins it.

**On a pre-25 bus, one of these still reads less than its help text claims.** The
others were tooling gaps and have been closed. `spark faults` decodes this
generation correctly now; its one remaining shortfall is that eight of the sixteen
legacy fault names have no `BIT_REMEDIES` entry, so they print without advice:
iwdtReset, eepromCrc, canTx, canRx and the four limit bits. That is in section 1.

- `spark voltage` prints `has-reset` out of `sticky_warnings`, which the pre-25
  decode path always returns empty, and so does `RailStatus.reset_ids`. See
  section 1.

Three things this document used to list as unreachable on 24.0.1 are reachable, all
measured on rig-max and all resting on one find: a per-device
fingerprint at api 0x094, `arb = 0x02050000 | (0x094 << 6) | id`, answered by a
ZERO-LENGTH ADDRESSED request with four read-only bytes. Eight distinct values on
rig-max, stable, not derived from the CAN id, and it is the Unique ID the firmware
itself accepts in SET_CAN_ID. It is NOT what REV calls the serial: REV Hardware
Client does not display it and pre-25 IDENTIFY does not take it, so this tree calls
it a fingerprint.

- `spark status` fills its identity column here by ASKING --
  `inventory(with_fingerprint=True)` -- and heads it `fingerprint` rather than
  `serial`. Unique ID Broadcast really is apiClass 47 at `versionImplemented` 25.0.0
  in [reference/REV-spark-frames-2.1.0.json](../reference/) and
  this fleet really broadcasts none, which is why nothing arrives unless you ask.
  Parameters 47, 48 and 49, which REV's index documents as "Reserved", read
  `0xFFFFFFFF` on all eight and are not identity either; nothing in the parameter
  table is. `spark learn-serials` records the fingerprints into `serials`
  and has been run -- `spark.yaml` carries all eight, written
  with a `.bak` -- so a swapped controller is visible on this generation now.
- `spark identify --id N` blinks an LED. Pre-25 identify is `IDENTIFY_UNIQUE | dev`
  with an EMPTY payload, and the 25+ form -- broadcast on device 0 carrying a
  four-byte serial -- is dropped in silence on 24.0.1, which is why the command used
  to print that it had sent while nothing ever blinked. Recovered by capturing REV
  Hardware Client's own LED button: three DLC-0 frames, 02051D81, 02051D82 and
  02051D83, one per controller blinked, in 9831 lines of candump, reproduced from
  this driver and confirmed by an operator. `tools/spark_blink.py` is the standalone
  tester.
- `spark set-id --serial X --to N` moves a controller, using the standard SET_CAN_ID
  form with the fingerprint as the Unique ID, and the move is RAM ONLY. `set_can_id`
  finishes with PERSIST_PARAMETERS, which 24.0.1 does not carry, so the id reverts at
  the next power cycle. The command says so and exits 1 rather than reporting a
  success it cannot keep. An id that has to survive a rail cycle is still set over
  USB-C in REV Hardware Client.

Two commands used to mislead on this generation and no longer do. `spark duplicates`
DETECTS a duplicate here rather than printing the `CANNOT TELL` it printed before:
`admin.duplicate_detection_available` returns True on both generations, and on pre-25 the command asks every id in 1..62 for its fingerprint and
counts the distinct answers, which is the same tell as two UNIQUE_ID payloads on one
id reached by a different mechanism. Requesting rather than listening is the whole
difference: nothing arrives unless you ask, and when two controllers share an id
they both answer the one request. Verified on real controllers without moving a CAN
id -- see the entry in section 7.

`spark audit` no longer scores a running fleet against REV's cold pre-25
defaults while it broadcasts this package's own boot throttle,
`can_bus._SPARKMAX_STATUS_PERIODS_MS`. `_deliberate_periods` carries that throttle
(`admin.py:1632-1640`) and both the bus-level rule (`admin.py:1683`) and
`status_1_verdict` (`admin.py:1836`) consult it, so the "all 8 thinned out
together, the bus is dropping frames" false positive is gone and a healthy fleet
prints "no problems found across 8 controller(s)". The measurement behind that is the
`pre25.boot_throttle_not_rev_default` claim in
`sparklib/provenance.py`.

**`UNREADABLE` in either table means stop reading the row.** It says the controller
is powered and still on the bus while what it broadcasts is no longer a measurement:
one or more of `voltage_v`, `current_a` and `motor_temp_c` is pegged at the top of
its field. Every other number in that frame is unreadable too, the limit bits
included. Cut and restore motor power, then run `spark faults` again -- the sticky
bits survive and say what preceded it.

### Step 3 -- the wheel takes setpoints and does not move

This is the signature that looks like a tuning problem and is not. Check in this
order:

1. **A hard limit reads asserted.** `spark faults` prints `LIMIT HARD-FWD` /
   `HARD-REV`. That is the data-port safety interlock, and it is DIRECTIONAL:
   one bit withholds output that way only, and the controller still drives the
   other way at the full commanded duty. Both bits together withhold all output,
   measured on rig-max. Parameters 50 to 53 are
   `PROTECTED_PARAMS` and both write paths refuse them, `write_param` on 25+ and
   `write_legacy_param` on pre-25, so no command here can disable them. If
   nothing is physically at an end stop, the polarity has drifted from
   `base.limit_switch_polarity`:
   ```bash
   uv run python tools/spark_limit_polarity_repair.py            # inspect
   uv run python tools/spark_limit_polarity_repair.py --repair --persist
   ```
   This happened on rig-flex, when ids 13, 15 and 16 held
   `Limit Switch Polarity = True` with nothing wired to the data port. Parameters
   50 and 51 answer a read on both generations now, measured on rig-max and on rig-flex. So `spark params --param 50` settles
   the polarity on either fleet, and changing it still goes through the script,
   because both write paths refuse a protected parameter.

   The encoding was proven on rig-flex, on id 17, whose steer data
   port is unwired by doctrine. Writing 1 to parameters 50 and 51 asserted both
   hard limits, and writing 0 cleared them. The restore was proved by a read, so
   0 is normally closed and 1 is normally open. One controller was injected, so
   the encoding is assumed uniform across a fleet on identical firmware, and both
   runs are in `docs/runs/rig-flex-parameter-reads.md`.

   **Read every polarity write back.** Parameter 50 on 26.1.6 accepted a write of
   2, answered Success and read back 2. The forward limit then read NOT reached,
   while parameter 51 held 1 as a control and its limit stayed reached. So the
   firmware stores an out-of-range BOOL and treats it as the permissive value on
   this wiring. On a robot that needs polarity 1, a mistyped write releases the
   interlock while reporting Success. The read back is the check here, and the
   repair script does it before it reports success.
2. **A heartbeat lock.** `primary_heartbeat_lock` means the controller is bound to
   another heartbeat source and will ignore this host until it is power cycled. No
   CAN command releases it, and every conclusion drawn from "it did not move" is
   wrong while it is set.
3. **Follower mode.** REVLib 2025 runs a follower whether or not any code
   references it, so the wheel can be driven by something no object in the program
   holds. Clear Follower Mode Leader Id (param 194) in RHC2 over USB-C, or run
   `uv run spark provision --id N --write` on firmware 25+. That command reads
   every declared setting off one controller and writes back the ones that
   disagree. The file declares 194 as 0 at `the sparkflex: block:332-338`
   with `deviates: false`. `spark audit` compares only the declared deviations, so
   a drifted 194 shows up in a provision run. It sits outside `PROTECTED_PARAMS`
   (`admin.py:246-259`), so the write path passes it through. The
   sweep read parameter 194 as Uint 0 on id 17, so a write here can be confirmed
   by a read. Parameter 194 sits above `LEGACY_PARAM_MAX`, so `read_legacy_param`
   refuses it and it was **not** read on rig-max. Whether 24.0.1 carries the parameter
   at all is an open question. On a MAX the question does not have to go through 194:
   Follower ID is parameter 57, inside the table and answered on all eight, and
   `tests/hardware/test_bus_preconditions.py:180-188` skips its follower check on this
   generation saying exactly that. Ids 158 to 165 were probed above the pre-25 table and
   came back with a non-zero status, so a refusal at 194 is plausible, and plausible
   is not measured. It is settled by a read on 194 with that guard widened, reads
   only, and never anywhere near 242 or 255.
4. **A dead output stage.** Applied output commanded with no current flowing.
   `spark audit` reports the pair; check the motor leads and the data cable, then
   look for a gate-driver fault.

### Step 4 -- before you clear anything

**`spark clear` erases the only record a reboot or a brownout leaves.** The bits are
gone from the controller the moment the frame goes out. On rig-flex the command was run
before `spark faults` after a power cycle and erased the `hasReset` the cycle had just
produced. That was defect **D5**, and it is closed: `clear_faults` captures a reading
before the clear and another after and reports per device whether each fault released
or regenerated through it (`sparklib/admin.py:446-496`), and
`cmd_clear` writes the pre-clear capture to `records/` and classifies the reset before
it prints (`sparklib/cli.py:386-405`). Read first and clear second
is still the order, because the record the command keeps is the only copy.

Also: on 26.1.6 the `can` sticky fault does **not** survive a power cycle, so a
boot-time clear has nothing latched to release. `SparkBus.apply_boot_config` sends
Clear Faults to every SparkFlex at startup and is doing real work -- waking the bus --
for a reason its comment does not give.

That default is product-split and the split is the right way round.
`apply_boot_config` clears sticky faults only when `controller_type == SPARK_FLEX`
(`sparklib/can_bus.py:129-133`), so on a MAX the `hasReset` the cycle
just latched survives boot. What it does on a MAX instead is re-apply
`_SPARKMAX_STATUS_PERIODS_MS` (`can_bus.py:135-137`), and that is not tidying: a
MAX loses every parameter this package writes over CAN at the cycle, and the status
periods are lost even when a burn is sent, because the burn does not reach them. So
the boot throttle has to be re-sent or the fleet comes back at REV's cold defaults of
10 / 20 / 20 / 50 / 200 / 200 ms, measured on rig-max after a rail cycle
with nothing re-applying.
`uv run spark throttle` re-sends that table without constructing a DriveTrain
(`sparklib/cli.py:338-383`). The pre-25 period write carries no
acknowledgement, so it verifies by measuring the cadence afterwards and exits 1 for
any controller that did not take it. `reconfigure_controllers` does not keep the
split. It defaults `clear_sticky_faults=True` for every family
(`can_bus.py:139`), so calling it on a MAX after a power cut erases the evidence
that the power cut happened.

### Step 5 -- config drift, and what cannot be proven

**On a Flex.** `spark audit` reads every declared deviation over READ_PARAMETER and
compares it against `the sparkflex: block` (`admin.py:2976-3005`).
`coverage_note()` then lists the declared settings this run did not read, all of
them readable on firmware 25+ (`admin.py:2470-2485`). Read it before
assuming the audit covered a setting.

One exception is worth knowing: **a write the firmware refuses echoes the
parameter's current value** in bytes 2:6 of `PARAMETER_WRITE_RESPONSE`. Verified on
rig-flex by setting parameter 159 to 1000, then offering 65535, which was refused and
echoed 1000 rather than the default 20. Status 0 Period accepts 1..1000 and Status 1
Period 1..32767; 0 is refused for both. So a refused write reports the parameter's
current value on anything the firmware range-checks, which corroborates the read
frames that 26.1.6 answers. The risk is a probe value being *accepted* instead of
refused, which changes the setting. Do it on one controller, then read the id back
and restore the declared value.

**On a pre-25 MAX, none of that applies and none of it is needed.** `PARAMETER_WRITE`
is apiClass 14 with `versionImplemented` 25.0.0, so 24.0.1 does not carry the frame
and there is no `PARAMETER_WRITE_RESPONSE` to read an echo out of. An unmatched
extended id is dropped in silence, which is why the whole write path looked dead on
rig-max until. Parameter 159 is not a parameter on this firmware at all;
158 to 165 answer with a non-zero status, and the periods move on api class 6
instead. What replaces the echo trick is simply reading the parameter: all 134
answer, so drift is measured directly and the round-trip probe is unnecessary.
`coverage_note()` says so on this generation: it lists the settings the audit does
not check as READABLE here rather than unverifiable, and names the read that would
close them (`admin.py:1942-1959`). The one thing to hold on to is that a MAX
write lands in RAM, so anything read back straight after a write proves the write
landed and proves nothing about the next power cycle. A burn at api 0x072 does commit
it, and nothing in this package sends one; see step 6.

### Step 6 -- repair, and prove it lasted

```bash
uv run spark provision --id 12 --write --persist   # every declared setting
uv run spark repair --id 12 --persist              # Status 1 Period alone, 25+ only
uv run spark snapshot --write        # only when the bus is known good, and see below
```

**`spark provision` is the wider of the two, and the 25+ audit names it as the
remedy for a drifted setting** (`admin.py:3000-3001`). It reads every
declared setting off one controller, writes back what disagrees, then reads each
write again because the echo is not evidence (`cli.py:1469-1758`). It burns
once at the end, after the last write has read back correct. It refuses a silent
target, an id with no role, and `--persist` on pre-25. A drifted PROTECTED
parameter stops the run before anything is written. The write dialect comes from
the target controller's own reading, since a bus-wide majority hides one odd
controller in a fleet of eight.

**`spark snapshot` produces a usable pre-25 baseline, and
`spark-baseline.yaml` exists.** It could not before, for two reasons that were
both this command's own rather than limits of the firmware.

The schema has a per-controller `serial`, and this generation broadcasts none, so
`cmd_snapshot` calling `inventory()` without asking for the fingerprint wrote the
literal string `None` into every one of them -- while `spark status`, `spark
audit` and `spark learn-serials` all passed `with_fingerprint=True` and got a
real identity back. It passes it too now, and the recorded values match
`serials` exactly. And `status_1_period_ms` read the modern frame's api,
which does not exist here, so the field was null for every device; it resolves
the api through the generation now and records the real cadence. The file also
carries `generation:` in its meta.

That mattered more once a fingerprint was on the wire, not less: a later audit
would have compared a real value against the string `None` rather than one blank
against another. See [docs/BASELINE.md](BASELINE.md).

A Status Period write governs only from the frame *after* the one already scheduled,
and a second write does not reschedule what the first armed. Writing 20000 then 20
two seconds later left the wire silent for another 18 s with both writes answered
Success. So any check that compares a measured period against the value asked for
must outlast the period being replaced: repairing 250 to 20 needs at least 250 ms.
That discipline is stricter on pre-25, not looser: the legacy period write on api
class 6 carries no acknowledgement at all, so the cadence on the wire is the only
evidence a write landed, and it cannot be read before the period it replaced expires.

**`spark repair` still refuses on rig-max, and the reason has changed.** It
writes Status 1 Period through PARAMETER_WRITE, and the status periods are not
parameters on pre-25 firmware. `fault_frame` reports this bus unprovisioned, so
the command stops before it sends anything. The other blocker was the defaults
file, and it is gone. `MOTOR_DEFAULTS_KEY` was a hardcoded module constant
naming `the sparkflex: block`, and `_defaults_path()` joined it without
consulting the base's `controller_type`, so `require_motor_defaults_for` raised
`MotorDefaultsWrongProduct` on a MAX host. That is fixed: `MOTOR_DEFAULTS_KEYS` maps each product to its own block,
`set_controller_type` declares which one this process reads, and the guard stays
underneath. A `the sparkmax: block` sits beside it in
the product blocks of `spark.yaml`, in the same meta / common / by_role schema:
37 entries. Thirty are parameters read over CAN from all eight rig-max controllers; the other seven are the status periods, which are not parameters on
this generation and were measured off the wire instead. Nine deviate from REV's
factory default: two parameters, and the seven boot-throttle periods. The
product now selects it, so `spark defaults` prints MAX values on a MAX host, and
`spark audit` reads those two parameters back over CAN and compares them --
which `coverage_note` scores against this file rather than against the Flex
fleet's deviation list.

A second blocker survives that fix, so `spark repair` still refuses on rig-max.
Repair writes the Status 1 Period as parameter 159 through `PARAMETER_WRITE`, and
the status periods are not parameters on 24.0.1. So
`fault_frame("pre25")["provisioned"]` is False (`admin.py:1518-1531`) and
`cmd_repair` returns 1 before it sends a write (`cli.py:1327-1335`). That
gate reads the generation off the wire, so it holds on any pre-25 bus and clears
on firmware 25 or later. Repair did gain two of the gates `provision` has, since
`duplicates()` now takes the generation and `require_at_rest` is read again
before the write. Whether it should survive alongside `spark provision`, which
writes 159 along with every other declared setting, is an open question.

The only proof a persisted parameter survived is a power cycle. A RAM value and a
flashed value read identically until the rail drops, on both generations, so the
check has to come after the cycle. `SPARK_PERSIST_STAGE` splits that across two
runs:

```bash
SPARK_PERSIST_STAGE=arm    uv run --with pytest pytest tests/hardware -q --hardware
# cut and restore motor power
SPARK_PERSIST_STAGE=verify uv run --with pytest pytest tests/hardware -q --hardware
```

Stage `arm` deliberately writes REV's default (250 ms) so a value that merely
survived in RAM is distinguishable from one that reached flash.

On a pre-25 MAX the same power cycle is still the test, and it has been run in both
directions. **Every parameter this package can write to a MAX is RAM only**, because
nothing here commits one. Idle Mode was set to BRAKE on all eight and read back as
BRAKE, the rail was cut and restored, and all eight came back COAST: rig-max as an operator's note, re-run as a test with all eight back
at COAST and sticky `0x0200` on every one. Persist Parameters is apiClass 63 index 15
with `versionImplemented` 25.0.0, so that frame does not reach 24.0.1.

**Pre-25 has its own burn-flash, and it works.** api 0x072, arbitration id
`0x02051C80 | id`, with a two-byte payload carrying the magic 15011 little-endian --
the same `PERSIST_MAGIC` the 25+ PERSIST_PARAMETERS carries, which REV's own spec
names "Magic Number" with `decodedMin == decodedMax == 15011`. Measured on rig-max, firmware 24.0.1: ids 3 and 4 were written Idle Mode COAST to BRAKE and
sent the frame, both replied `0x00` on the request's own arbitration id, id 8 was
written the same value and sent no burn frame, and after a motor-rail cycle 3 and 4
read back BRAKE while the control read back COAST. The control reverting is what
makes it conclusive. Both were then written COAST and burned back, and that survived
a further cycle. The controller checks the payload, identically on ids 1, 2, 5, 6 and
7: correct magic `0x00`, wrong value `0xFF`, big-endian `0xFF`, one byte `0xFF`,
magic plus trailing bytes `0x00`, eight bytes of repeated magic `0x00`, and a
zero-length frame draws no reply at all -- which is why the empty-payload attempt
sent earlier committed nothing and was correctly ignored. The claim is
`pre25.burn_flash_api` in `sparklib/provenance.py`, and
`tests/hardware/test_legacy_burn_flash.py` stages it behind `SPARK_HW_BURN_FLASH`
and an explicit id.

**It does not reach the status periods, and that is measured rather than inferred.**
Id 3 had `0x060` set to a distinctive 77 ms -- neither REV's 10 nor this package's
50, so no other mechanism produces that number -- and was burned with the magic and
accepted `0x00`. After a rail cycle it read 10.0 ms, and so did all seven others. The
periods move on api class 6 and are not part of what the burn commits.

So on a MAX there is now something to prove survived a cycle, and this package cannot
produce it: nothing in `sparklib/` sends api 0x072, and the frame stays on
the injector denylist in `tests/support/sparkhw/wire.py`. `spark persist` refuses on
this generation before it sends anything. It used to send the 25+
PERSIST_PARAMETERS that 24.0.1 drops, then measure the cadence against
`fault_frame`'s `expected_ms` and, on a fleet already sitting at that value, report
that the burn had landed -- a false success on a controller where nothing was
written and nothing could have been. Whether burning as routine practice is wise is a
separate question, because flash cycles are finite. What the tooling writes is still
a configuration to re-apply after every cycle -- which is what makes the `hasReset`
bit in section 1 the load-bearing signal on that fleet, and losing it the expensive
defect -- and the status periods keep that property whatever is burned, so
`spark throttle` stays mandatory after every power event.

## 7. Current status

Updated, except the entries and the
parameter-sweep note below. The tree collected 2107 tests with 13 xfail markers. Several adversarial modules landed, so the test
total is low now, though the marker count still stands at 13. Every count here is regenerated rather than remembered:
`pytest -q --collect-only` gives the total, and `pytest -q -rX` prints any xfail that
has started passing.

**The suite is host-bound and rig-max is not the host it was written on.**
`tests/unit` is clean on rig-max: 1082 passed, 74 skipped, 20 s. `tests/adversarial`
is green only against rig-flex's config. Pinned there with
`ARM_BASE_CONTROL_CONFIG=spark.yaml` it was 368 passed and 7 xfailed, with nothing failing. New modules landed for
`spark provision`, the parameter-read frames and the baseline table, so re-run
the tier before quoting that count. Unpinned it reads whatever `DEFAULT_CONFIG_NAME` resolves to, and
the config names rig-max, which the CLI
tests fail on by construction: `tests/adversarial/test_recovery.py:95-101` asserts
the live config IS `ROLES_BASE03`, and its docstring says every CLI test in the
module leans on that rather than on a monkeypatched role map. That is a
fixture-scoping defect in the suite, not a driver defect and not a robot fault, and
it is why an adversarial run has to name the config it ran under.

**Closed.** D1 -- `spark audit` reads STATUS_1 and exits 1 naming the fault, and
`swerve_drive._verify_controllers_can_drive` refuses to start when a controller
is on the bus and cannot apply output. Saturated STATUS_0 -- `decode_status_0`
returns an `implausible` list that four call sites consult. D3 -- `write_param`
retries, paces writes 20 ms apart and compares the echoed value, reporting
`verified` separately from `result`. D4 -- `status_1_verdict` takes evidence
(`window_ms`, `max_gap_ms`, `last_seen_ms`) and separates `stopped` and
`intermittent` from a slow cadence; the remaining piece is a saturated bus,
which still needs a coverage input. D5 -- `clear_faults` verifies and reports
per device. LEGACY -- `controller` reads faults from STATUS_1 and rail and
current from STATUS_0, with the MAX path routed through its own decoder.
`set_can_id` verifies the move by inventory and then persists.

**Did not reproduce.** D2 -- flash was seeded with 250 ms and settled, 20 ms
written, PERSIST sent about a millisecond later with no settling, then the rail
cut. The controller came back at 20 ms. CD 432129 describes the opposite on
2021-era firmware. One controller, one parameter, one trial, and the thing it
hunts is a race, so this is evidence and not proof. `persist()` settles from the
last write RESPONSE and accepts a `confirm` callback, which is worth keeping.

**Closed.** The driver used to branch on PRODUCT where REV's spec
branches on FIRMWARE VERSION, so `fault_frame()` returned STATUS_0 as the fault
frame for a SPARK MAX while firmware 25+ carries faults in STATUS_1 on every
SPARK. `API_SETS` now keys on `GEN_PRE25` and `GEN_FW25`, `collect_status`
labels each controller with the generation it broadcast, and passing a product
name where a generation belongs raises. See
[docs/PROTOCOL.md](PROTOCOL.md).

**Open.** The remaining xfails, each with a written reason saying what would
close it. The marker is the record, so a fix flips a marker. Catalogue modes F4 and
F6 (firmware-version drift) have no injector.

This paragraph used to end "the simulator has no notion of a controller's firmware
and cannot model a mixed-firmware fleet", and that has stopped being true. `SimSpark`
carries a `firmware` field and derives `generation` from it, never from the product,
and `tests/adversarial/test_firmware_generations.py` puts a pre-25 and a 25+
controller on one bus and asserts `collect_status` labels each correctly. The
pre-25 parameter access in section 1 has stopped being unmodelled too, which it was
when this paragraph was written: `SimSpark` answers the 0x300 api, a write lands in
`ram` and never in `flash` so a reboot discards it, the fingerprint at api 0x094
answers an addressed zero-length request, and both addressing models of IDENTIFY are
answered as measured. The end-to-end write path still needs a robot:
`tests/hardware/test_config_injection.py` reads ten parameters and writes each one
its own value back, so the path is exercised and the controller ends where it
started.

The hardware tier no longer carries its Flex assumptions. Config injection writes
through a `dialect` fixture that reads the generation off the wire and picks the
write path for it (`tests/hardware/conftest.py:381-385`), so a pre-25 bus gets
`set_legacy_status_period` rather than waiting on a `PARAMETER_WRITE` response that
never comes, and `test_bus_preconditions` checks the frames and keys each generation
actually broadcasts. On rig-max, with `SPARK_HW_INJECT`,
`SPARK_HW_COLLIDE` and `SPARK_HW_CONGEST` armed, the tier ran 48 passed, 0 failed, 36
skipped and 3 xfailed. Every skip has a stated reason: the staged tiers need a person
between two runs, `needs_clean_bus` stands down while collide or congest is armed,
the duplicate-by-serial test has no UNIQUE_ID to work with on pre-25 -- the
fingerprint route that replaced it on this generation did not exist until -- and two congestion tests report that neither collision nor congestion
raised a sticky fault on this fleet.

**Measured on rig-flex, SPARK Flex on firmware 26.1.6, no action.** Fleet overcurrent
is peaks and not dwell. Across four teleop runs the worst controller spent 0.21 s
above the 80 A Smart Current Stall Limit in a 17.7 s run at full stick deflection,
about 1 percent, with
temperatures at 30 to 33 C. Do not lower `steer_max_output` on this evidence.
All four steers peg the 150 A telemetry ceiling, so their true peaks are unknown.
rig-max's MAX fleet runs a 40 A stall limit rather than 80, so neither the dwell
figure nor the conclusion transfers to it.
`tools/spark_steer_stress.py` now drives a fixed, repeatable pattern and
classifies each sample as following, limit-regulating, chopping or a dropout, so
two runs are comparable and a config change can be measured rather than argued.

**New.** A read-only parameter sweep, `tools/spark_param_sweep.py`, with the
read-only property proven from the wire rather than asserted: every frame it sends is
inspected by a test, and the firmware behaviour that would break the property is
modelled and shown to stop the run. It used an undocumented read path until, going through a `SparkAdmin.read_param` that put a one-byte payload
on `PARAMETER_WRITE`. It now runs on the frames REV define: READ_PARAMETER across
apiClasses 15 through 22, one per parameter pair over the whole 0-255 range, plus
type queries on apiClass 13. Those carry no payload at all, so the read-only
property is structural rather than one byte of discipline.

**And that sweep is a 25+ tool, which the paragraph above did not say.**
READ_PARAMETER is apiClasses 15-22 and GET_PARAMETER_TYPES is apiClass 13, all
`versionImplemented` 25.0.0, so rig-max's 24.0.1 fleet drops every frame it sends.
Measured on rig-max: a class 19 read and a class 13 types request went
to id 3 both as remote frames and as zero-length data frames, and none of the four
was answered, while the legacy dialect answered the same parameters on the same
controller. One caveat rode on that and has since been discharged. Those remote
frames carried dlc 0, a known-silent form on a Flex, so both apis
went to id 3 in all three forms and all six drew silence. It does
not check: it prints `controller_type` in its header and sweeps regardless. The
pre-25 equivalent is a different api altogether, the 0x300 access in section 1,
reached by `read_legacy_param` and not by this script. The sweep runs REV's own documented
frames now, and the legacy access is the undocumented one. It answers 134 parameters
on a firmware REV has published no read frames for at all.

**New.** Persistence on pre-25 is reachable and unimplemented. api 0x072
with the magic 15011 commits the parameter table on 24.0.1, measured with controls
and written up in section 6 step 6; it does not commit the status periods, measured
the same way. Nothing in `sparklib/` sends the frame and it stays on the
injector denylist, so every statement above about what this tooling can persist to a
MAX still holds. Being a CONFIRMED flash command is a stronger reason to deny it
than the unverified one that entry used to carry: it is a command a controller acts
on, which is the denylist's whole rule, and what it does is spend one of a finite
number of flash cycles and make whatever happens to be in RAM permanent on a live
fleet. The guard refuses the arbitration base rather than a payload, so every form
of the frame stays out, the zero-length one the device ignores included. Off a robot
the simulator now models the three measured cases rather than a switch -- no reply
at all to a zero-length frame, `0xFF` to a wrong magic, and `0x00` plus a commit of
`ram` into `flash` for the correct one, with `periods_ms` deliberately untouched --
so a driver that reads the accept byte can be tested at all.
`legacy_burn_flash_works` survives as a behaviour flag for a controller that
refuses, which is a defect rather than the norm. The measurement lives in
`tests/hardware/test_legacy_burn_flash.py`, which needs a robot, an explicit id and
`SPARK_HW_BURN_FLASH`.

**New.** Duplicate CAN ids are detectable on pre-25, and the
true-positive case is covered on hardware. The per-device fingerprint at api 0x094
is REQUESTED rather than broadcast, so one request to a shared id draws one reply
per controller, and `duplicate_detection_available` returns True on both generations
now. Staging a real duplicate would have meant reassigning an id on a fleet that
could not then be recovered by serial, so it was verified by INJECTING a second
reply on the fingerprint arbitration id instead: clean before, detected during,
clean after, and no controller changed
(`tests/hardware/test_legacy_burn_flash.py`). That test settled a detail worth
carrying into any other injection: `read_fingerprint` discriminates by frame LENGTH
and not by `is_rx`, because SocketCAN flags every locally generated frame as
loopback and an `is_rx` filter dropped all 124 injected replies. The `Sniffer` uses
`is_rx` deliberately, to tell a frame this host wrote from one a controller sent; a
DRIVER path that filters on it cannot see an injected frame at all.

**New, and it is this suite's own near miss.** A zero-length frame is not
inert on 24.0.1. The api sweep that found the fingerprint was justified on the
grounds that zero length is request semantics on this firmware -- api 0x072 with DLC
0 draws no reply and commits nothing, while the same api with its magic replies and
commits -- and that one data point was generalised to a command space. The sweep
cleared the sticky fault word on both swept controllers, because CLEAR_FAULTS (api
0x06E) executes on a zero-length frame: ids 3 and 4 read empty afterwards while the
other six still carried canTx, canRx and hasReset. The damage was bounded only
because a full parameter snapshot and a sticky-fault comparison were taken either
side and the erased bits had already been recorded. The exclusion list the sweep ran
against was `FORBIDDEN_BASES` in `tests/support/sparkhw/wire.py`, which is the
INJECTOR denylist -- frames a test may TRANSMIT -- and CLEAR_FAULTS is legitimately
absent from it for that purpose. Reusing a list built for one purpose as the safety
boundary for another is what let it through. Any future sweep must exclude every
frame with COMMAND semantics whatever its payload length, derived from the frame
spec rather than from this denylist, and must keep the before-and-after snapshot
that caught this one. The claim is `pre25.zero_length_frames_can_still_act`.

**New, superseded.** A pre-25 SPARK MAX answers parameter
reads and writes over api class 48. A SPARK Flex on 26.1.6 answers READ_PARAMETER
and GET_PARAMETER_TYPES once the request is a remote frame with dlc 8. Section 1
carries both measurements. `spark audit` reads both generations now and infers about
neither. What is still open is the rail view: `spark voltage` and
`RailStatus.reset_ids` read `sticky_warnings` alone, which the pre-25 decode path
returns empty. And the 0x300 access is modelled in the simulator, so both dialects
are reproducible off a robot. `audit_problems` has since been moved onto `classify_reset` and sees the pre-25
`hasReset`; `spark voltage` and `RailStatus.reset_ids` still read `sticky_warnings`
and do not. And the 0x300 access is modelled in the simulator now, so the capability
that changes what an audit can claim is reproducible off a robot even though the
audit does not use it.

## 8. Running it

```bash
uv run --with pytest pytest tests -q                       # everything, ~2.5 min
uv run --with pytest pytest tests -q -rX                   # and show any XPASS
uv run python tests/adversarial/run_suite.py               # grouped by defect
uv run python tests/adversarial/run_suite.py --list
uv run --with pytest pytest tests/hardware -q --hardware   # needs a real robot
```

`pytest` is declared in the `dev` dependency group (`pyproject.toml:51-54`) and
`uv sync` installs it by default, so `uv run pytest...` works on its own. The
`--with pytest` above is what still works after a `uv sync --no-dev`.

Adding a scenario is documented step by step in
[tests/adversarial/README.md](../tests/adversarial/README.md) -- find the failure
before the function, inject through the simulator, read it back through the real
driver, write a control, and mutation-check anything that passes.
