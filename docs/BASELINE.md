# SPARK baselines

A baseline is a file that records what was on the CAN bus on the day someone
looked at it and believed the bus was good. It exists to answer one question:
**has this bus changed since then, and where?**

It is not a description of correct configuration. The declared configuration
lives in `the `sparkflex:` block of spark.yaml` and is
what a controller *should* be set to. A baseline is what a controller *was*.
Those two files sit in the same directory and are read by different code for
different reasons, and confusing them sends an operator to reprovision hardware
that never drifted. A `the sparkmax: block` sits in that directory too, and the product now
selects it. `MOTOR_DEFAULTS_KEYS` maps each product to its own block
(`admin.py:2492-2495`), and `set_controller_type` declares which one this
process reads (`admin.py:2513`).

Files: `spark-baseline.yaml`, written beside your config.
Written by `spark snapshot --write`. Three exist today: `spark-baseline.yaml`,
`spark-baseline.yaml` and `spark-baseline.yaml`. rig-max's was captured, once `cmd_snapshot` learned to ask a pre-25 fleet for its identity.

---

## 1. Why the file exists at all

This file was written when a SPARK Flex on 26.1.6 appeared to answer no
parameter read, so a controller's configuration could only be inferred from what
it broadcast unprompted, and the only provisioned setting visible that way was
the Status 1 Period. That limit was the frame form and not the firmware. Since the driver sends its reads as remote frames with dlc 8. A full sweep
of all eight controllers returned zero silent frames over ids 0-255, of which 185
are implemented. So `spark audit` compares all nine declared deviations against
the controller, where it once compared one. The baseline still earns its
place: it records identity, firmware and cadence per corner, which no parameter
read reports.

That is the hole a baseline fills. Two facts about a Flex are readable over CAN
and recorded nowhere else: its serial, from the Unique ID Broadcast on api 0x2F0,
and its firmware version. The serial can also be written into `serials`
by `spark learn-serials --write`, and rig-flex and rig-flex-2 have that block; the
firmware version has no other home at all. Write them down once, on a bus you
trust, and a later `spark audit` can catch a swapped controller and a firmware
that moved on its own.

That question is settled, and the answer came out narrower than the doubt. Three
forms of one READ_PARAMETER request went to id 17 on rig-flex. The
zero-length data frame drew silence, a remote frame with dlc 0 drew silence, and a
remote frame with dlc 8 answered. So a remote frame is necessary and not
sufficient, and dlc 1 through 7 were never sent. `GET_FIRMWARE` is rtr with
lengthBytes 8 in the same spec and answers at dlc 0, so the dlc rule holds for the
read classes and does not generalise. The claim is
`flex.param_reads_were_probed_as_data_frames` in `provenance.py:381-437`,
how=HARDWARE, and the run is
`docs/runs/rig-flex-parameter-reads.md`.

**This premise does not hold on a pre-25 SPARK MAX.** Measured on rig-max, all 134 parameters (ids 0 through 133) answered a read on all eight
controllers: none refused, none stayed silent. On that fleet a setting can simply
be asked for, so the argument for inferring configuration from broadcasts
collapses. Measured on the same fleet, a parameter can also be made
to STICK: api 0x072 carrying the two-byte magic 15011, little-endian, commits the
parameter table to flash on firmware 24.0.1. So on rig-max a captured table is not
only something to compare against, it is something that could be written back and
made permanent. And identity, measured alongside it, is readable there as well: a
zero-length ADDRESSED request to api 0x094 answers with four read-only bytes that
differ per controller, so the other half of what a baseline exists to record --
which controller this is -- is no longer missing on this generation. Section 7
covers what a baseline should be on that hardware instead, what to call that
identity, and what the burn does not reach.
`docs/README.md` states the general rule: the frame layout keys on firmware
version, not on product.

---

## 2. The schema, field by field

The whole file is written by hand as text lines in `cmd_snapshot`
(`cli.py:148-171`). There is no serialiser and no schema object. What
follows is the complete set of keys, and every one of them is emitted
unconditionally.

### Header comment

Three lines, `cli.py:252-254`:

```
# SPARK baseline for rig-flex -- the reference a later `spark audit`
# compares against. Regenerate with `spark snapshot --write` only when the
# bus is known good.
```

"Only when the bus is known good" is advice, not a gate. Nothing in the code
checks it. See section 5.

### `meta`

| Key | Source | Read by |
| --- | --- | --- |
| `base_index` | `_base_index()`, the `the rig` prefix of the active config filename (`cli.py:107-111`) | `test_bus_preconditions.py:233`, in an assertion message only |
| `hostname` | `socket.gethostname()` | nothing |
| `config` | basename of `ARM_BASE_CONTROL_CONFIG` or `DEFAULT_CONFIG_NAME` (`cli.py:100-104`) | nothing |
| `captured_utc` | `_utcnow()`, ISO 8601 to the second, UTC | `cmd_audit` prints it (`cli.py:884-885`) |
| `controller_type` | `controller_type` from the active config | nothing |
| `can_interface` | `can.interface` from the active config | nothing |
| `git_rev` | `git -C <package dir> rev-parse --short HEAD`, or the literal `unknown` if git fails (`cli.py:145-147`) | nothing |

Six of the eight `meta` keys are provenance for a human reader and are never
loaded by any code path. That is fine, but it means nothing detects a baseline
captured on the wrong robot. A `spark-baseline.yaml` whose `meta.hostname` says
a different rig would audit exactly as happily as a correct one, because the file
is selected by filename and the filename comes from the config, not from the
contents.

`git_rev` resolves against whatever repository contains
`sparklib/`, which for a vendored copy is the consuming repository
rather than this one. Run `git -C sparklib rev-parse --short HEAD`
to see what a snapshot taken here would record.

### `controllers`

A map keyed by CAN device id, integer, one block per controller that was
broadcasting during the snapshot window. Four fields, `cli.py:273-277`:

| Key | Source | Read by |
| --- | --- | --- |
| `role` | `_spark_roles()[dev]`, as `group/CORNER`, or the literal `UNCONFIGURED` if the id is not in `devices` | nothing |
| `serial` | `inventory()[dev]['serial']`, 8 hex characters. On 25+ it arrives on its own in the api 0x2F0 broadcast. On pre-25 `cmd_snapshot` REQUESTS the api 0x094 fingerprint, because it passes `with_fingerprint=pre25` (`cli.py:230`), and a device that leaves that request unanswered still writes the literal `'None'` (`cli.py:275`) | `audit_problems` (`admin.py:2367`) |
| `firmware` | one `GET_FIRMWARE` round trip per device, or the literal `unknown` | `audit_problems` (`admin.py:2372-2389`), `test_bus_preconditions.py:214-234` |
| `status_1_period_ms` | `inventory()[dev]['periods_ms'][0x2E1]`, a float in ms, or `null` | **nothing** |

`status_1_period_ms` is written and never read back. No period verdict consults
it. On 25+ the expected value comes from `declared_status_1_period_ms()`, which
reads `the sparkflex: block` (`admin.py:1464-1479`). On pre-25
`fault_frame()` scores LEGACY_STATUS_0 against this package's own boot throttle and
reads no declared value at all (`admin.py:1514-1527`). Grep for the key and it turns up in the writer at `cli.py:277` and in the three
baseline files. Every other hit
builds its own dict under the same name rather than reading the baseline's, so
nothing consumes this field.
If you want to know whether a period drifted since capture, you diff the file by
hand.

`cli.py:269` picks the api by generation, so
`API_SETS[normalise_generation(gen)]["status_1"]` gives 0x2E1 on 25+ and 0x061
before it (`admin.py:111-114`). It was a hardcoded `0x2E1` until, which is why any pre-25 capture would have written null here.
`spark-baseline.yaml` now records 100.0 ms on all eight, which is this
package's own boot throttle for frame 1 (`can_bus.py:25`). Section 7 has
what else a pre-25 baseline should carry.

### `parameters`

A third top-level key, added and written at `cli.py:306-328`.
Passing `--no-parameters` leaves it out, so this is the one key the writer emits
conditionally.

| Key | Source | Read by |
| --- | --- | --- |
| `captured_ids` | `len(table['common'])` (`cli.py:312`) | `tests/adversarial/test_baseline_params.py:133` only; no production path reads it |
| `common` | `{param_id: raw uint32}` for every id the whole fleet answered with the same word, from `fleet_param_table` (`admin.py:2892-2931`) | `baseline_param_problems` (`admin.py:2934-2973`), called by `cmd_audit` at `cli.py:1997-1998` |

The map holds every id that `the sparkflex: block` does not name, minus
parameter 0, which is each controller's own CAN id. Section 3 step 4a covers the
refusals that stand between a read and a recorded table. A disagreement with the
declared file is a fault. A disagreement here prints as a note and leaves the exit
code alone (`cli.py:2005-2011`).

`baseline_param_problems` reads `parameters.common` off the loaded file and
compares it id by id. Keys or values that are not whole numbers stop the
comparison with a message, so a broken baseline cannot take the audit down
(`admin.py:2945-2955`).

Only `spark-baseline.yaml` carries the block today, with 138 ids. The rig-max and
rig-flex-2 files were captured before it existed.

### A worked example

The rig-flex baseline: `can0`, `controller_type: sparkflex`, `generation: fw25+`. Eight
controllers, ids 10 through 17, all firmware 26.1.6, all
`status_1_period_ms: 20.0`, and a parameter table of 138 undeclared ids. The
eight serials match `spark.yaml:98-100` exactly. Both readings come from the
same api 0x2F0 broadcast, one written by `spark snapshot` and one by
`spark learn-serials`, so agreement between them is expected and a disagreement
would mean one of the two files is stale.

The rig-flex-2 baseline: `slcan0`. Also eight controllers, ids 9 through 16, all 26.1.6. Its periods are
mixed: ids 9, 10, 11, 12 and 14 recorded at 250.0 ms and ids 13, 15 and 16 at
20.0 ms.

Read that second file carefully before drawing a conclusion from it. 250 ms is
the REV factory default for Status 1 Period and 20 ms is the value this project
provisions (`the sparkflex: block:269-275`), so five of the eight look
drifted. They are drifted, deliberately. rig-flex-2 is the brownout and config-loss
experiment: `LIVE_EXPERIMENTS` in `cli.py:204-213` names 9, 10, 11, 12 and
14 as the drifted control group and 13, 15 and 16 as the subjects holding 20 ms
across a power cycle at 10.24 V. That baseline records the experiment's starting
state on purpose.

The general lesson stands anyway: **`spark snapshot` records what is on the wire
and never compares it against the declared configuration.** A baseline can
legitimately contain values that a `spark audit` will call problems.

---

## 3. What `spark snapshot` actually does

`cmd_snapshot`, `cli.py:218-357`. In order:

1. `roles = _spark_roles()`. The drive and steer groups only. CANcoders live on
   `base.cancoder_bus` and reuse ids 1 to 4, so they are excluded by group
   (`cli.py:82-102`). Before that partition was added, on rig-max the
   cancoder entries overwrote the rear SPARKs on ids 1 to 4 and hid LB and RB
   from the role map entirely.
2. `inv = adm.inventory(args.window, with_fingerprint=pre25)` (`cli.py:230`).
   Passive listening, default 5 s, and a silent bus reads as "nobody spoke", not
   "nobody is there" (`admin.py:455-458`). On a pre-25 bus the fingerprint
   flag adds one api 0x094 request per device that already spoke
   (`admin.py:490-495`), so the step is active there. On 25+ the flag is
   False, because the serial arrives on its own in the api 0x2F0 broadcast.
3. One `GET_FIRMWARE` per device found. The parameter sweep in step 4a
   transmits too, and on a pre-25 bus the inventory above asks each device for
   its fingerprint. Those frames make the snapshot an active command.
4. **Completeness gate.** If any configured id did not broadcast, it prints
   `refusing to snapshot an incomplete bus; these ids are silent:`, lists them,
   suggests `spark clear`, and returns 1. There is still no correctness gate on
   the identity half.
4a. **The parameter table**, added once every parameter became
   readable. It reads the whole addressable table off every configured
   controller and records the ids the fleet agrees on, EXCLUDING every id the
   declared file names and excluding parameter 0. The two files therefore
   partition the id space: `the sparkflex: block` says what a motor
   should hold and the audit treats a disagreement there as a fault, while the
   baseline says what the fleet was holding when someone called it good and the
   audit treats a disagreement there as a NOTE. Without that split one
   deliberate config edit would be reported twice.
   Values go in as raw uint32 words, because that is what the wire carries and
   what compares exactly; a float round-tripped through a YAML decimal does not.
   The decoded value and the parameter name ride in a generated comment.
   **It refuses twice.** The first refusal fires when any controller missed ids
   the others answered (`cli.py:282-292`). One apiClass 13 timeout loses a
   whole 16-id block, and a shorter table would read as a smaller fleet
   configuration. The second fires while any undeclared id differs across the
   fleet, and it names the id with every controller's value
   (`cli.py:293-305`). The majority is not an authority. On rig-flex
   parameter 153 read 17 on controller 12 and 7 on the other seven, and 17 is
   REV's own documented default. Recording the majority would have written a
   number nobody chose into the reference file. Controller 12 was put back, so the fleet now agrees at 7 and `spark-baseline.yaml:136`
   records it. `--no-parameters` captures identity and cadence without the table.
5. Builds the text. Without `--write` it prints the file to stdout and says
   `dry run -- pass --write to save to <path>`, then returns 0. **The dry run does
   not diff against the existing baseline.** Comparing an old and a new capture
   is a manual `diff`.
6. With `--write`: creates the directory, copies any existing baseline to
   `<path>.bak`, writes the new one, prints `wrote <path>` (`cli.py:337-343`).
   One backup slot. A second snapshot overwrites the first backup.
7. If `serials` is unset, chains into `cmd_learn_serials(write=True)`,
   which rewrites the robot's config file in place with a `.bak` of its own
   (`cli.py:348-356`, `cli.py:2172-2178`).

Two things worth knowing about step 7. It edits `spark.yaml`, a source
file, as a side effect of a command whose name suggests it only reads. And it
runs *after* the baseline is already on disk, so if it fails the baseline still
exists.

`spark snapshot` is **not** covered by `_refuse_on_a_live_experiment`
(`cli.py:375-391`), which guards five writing commands: `spark set-id`
(`cli.py:779`), `spark params --set` (1040), `spark persist` (1235),
`spark repair` (1305) and `spark provision` (1477).
Running `spark snapshot --write` on rig-flex-2 would overwrite the experiment's
recorded starting state, leaving one `.bak`.

---

## 4. When to capture one

- Once, at the end of bring-up, when every controller has been provisioned and
  the bus has been read clean. `spark provision --id N --write --persist` does
  that over CAN, one controller at a time (`cli.py:1469-1758`). RHC2 over
  USB-C is still the route for protected parameters 2 and 50 to 53, which both
  write paths refuse (`admin.py:246-259`). `docs/SETUP.md` is the bring-up
  path.
- After a deliberate change that you intend to become the new reference: a
  firmware update applied to every device on the bus, a controller replaced on
  purpose, a CAN id reassignment.
- Never in response to an audit finding you have not explained. Re-baselining is
  how a real fault becomes invisible, and it takes one command.

---

## 5. When a captured baseline becomes a lie

A baseline goes stale silently. Nothing in it expires, nothing revalidates it,
and `cmd_audit` prints `captured <timestamp>` and no warning however old that is
(`cli.py:1962-1963`).

**After a reprovision.** Any RHC2 Save over USB-C, any `spark repair`, any
`spark provision --write`, any parameter write. On a pre-25 MAX that last one has two halves, measured on
rig-max: a plain parameter write is volatile and the next power cycle undoes it,
so it cannot leave a baseline stale for long, while a write that was then
committed with the api 0x072 burn is permanent and can. The baseline still names
the old firmware and the old serial, which are usually still right, so this one
is mostly harmless. The parameter table is what goes stale here. rig-flex's baseline records 138
undeclared ids, and `baseline_param_problems` reads them back on every audit
(`admin.py:2934-2973`). A reprovision that moves one of those ids leaves
the file disagreeing with the fleet. The audit prints that disagreement as a
NOTE and keeps it out of the problem list, so the exit code still answers for
findings alone (`cli.py:1997-2011`). `status_1_period_ms` stays the
exception, because nothing reads that field back.

**After a controller swap.** This is the case the serial field exists for. The
audit reports `id N serial X != baseline Y` and tells you to re-baseline if the
swap was deliberate (`admin.py:1907-1911`). The trap is that
`spark snapshot --write` will happily record an unintended swap as the new truth,
with no confirmation prompt. Once you have done that, the swap is undetectable.
Explain the finding before you regenerate the file.

**After a firmware update.** The firmware field is compared in two places and
both fail loudly on a stale baseline: `audit_problems` at `admin.py:2376-2393`
and `test_every_controller_answers_the_firmware_version_the_baseline_recorded`
at `tests/hardware/test_bus_preconditions.py:214`. That test exists because a
reflash that reports success without updating, and a controller that reverts to
an older version on its own, are both in the catalogue and neither is injectable
in simulation. Update every device on the bus, then re-baseline, in that order.
A half-updated bus baselined mid-way records the mixed state as correct.

**After a CAN id change.** The `controllers` map is keyed by id. A moved
controller produces two findings, `id N is in the baseline but is not on the bus`
(`admin.py:2365`) and `id M is broadcasting but is not in devices`. If
`serials` is populated the audit joins them by serial and names the
mover instead (`admin.py:2226-2239`).

**After an edit to `devices`.** Same shape as a physical id change, from the
other direction.

**Immediately, if the bus was not actually good when you captured it.** There is
no gate on this. See rig-flex-2 in section 2 for a baseline that records
factory-default periods on five controllers, in that case on purpose.

One asymmetry worth remembering: the firmware check refuses to treat an
unanswered query as a match. If the `firmware` key is present and empty, the
audit reports that the device did not answer rather than passing it
(`admin.py:2378-2388`). Guarding on truthiness there once let a controller
refusing `GET_FIRMWARE` audit clean against any baseline at all.

---

## 6. Every consumer of `_load_baseline`, and what each does without one

`_load_baseline()` (`cli.py:209-215`) returns `(data, path)`, or
`(None, path)` when the file does not exist. It does not catch a YAML error, so a
corrupt file raises out of every caller. An empty file parses to `None` and is
treated as absent by all three consumers below.

`_baseline_path()` (`cli.py:202-206`) builds the path from `_base_index()`,
which comes from the active config filename, which is hostname bound. There is no
override flag and no environment variable. The robot you are on selects the file.

**1. `spark audit`** (`cli.py:1953-2039`).

With a baseline it prints `baseline: <file> (captured <utc>)`, then enriches the
inventory with one `GET_FIRMWARE` per device. The file then feeds two checks.
`baseline["controllers"]` goes into `audit_problems`, which runs the serial
check and the firmware check (`cli.py:2000-2004`). The whole baseline goes
into `baseline_param_problems`, which compares the undeclared ids it recorded
against the live fleet and reports each disagreement as a note
(`cli.py:1997-1998`).

Without: prints

```
note: no baseline for rig-max -- run `spark snapshot --write` on a known-good bus
```

and continues. Exit code is unaffected. Everything else still runs: duplicates,
missing ids, unconfigured ids, the id 0 rule, period verdicts, faults, and the
declared-deviation comparison, which reads the motor defaults file and not the
baseline. What is lost is the serial comparison, the firmware comparison, the
`is in the baseline but is not on the bus` finding, and the undeclared-parameter
notes. `baseline_param_problems` returns an empty list when the baseline carries
no `parameters.common` table (`admin.py:2942-2944`), and rig-max's and
rig-flex-2's files carry none. Note also that the firmware query at
`cli.py:1982-1984` is inside `if baseline:`, so **without a baseline
`spark audit` never asks any controller its firmware version at all.**

**2. The `baseline` fixture in the hardware suite**
(`tests/hardware/conftest.py:195-201`).

```python
data, path = cli._load_baseline()
if not data:
    pytest.skip(f"no baseline at {path}; run `spark snapshot --write` on a "
                "known-good bus")
```

Skip, not fail. One test uses it today,
`test_every_controller_answers_the_firmware_version_the_baseline_recorded`
(`tests/hardware/test_bus_preconditions.py:214-234`). On a robot with no baseline
that test reports as skipped, which is correct but easy to miss in a run summary.

**3. `tests/adversarial/test_recovery.py:100-101`**, inside
`test_the_simulated_fleet_is_the_fleet_this_robot_is_configured_for`:

```python
baseline, _ = cli._load_baseline()
assert {int(d) for d in baseline["controllers"]} == set(ROLES_BASE03)
```

No guard. On a host whose config resolves to any base index without a baseline
file, this raises `TypeError` on `None["controllers"]`. That is part of why the
adversarial suite is pinned to rig-flex: run unpinned on a rig-max host it produces
26 failures, because the CLI tests read the live hostname-bound config and assert
against `ROLES_BASE03` (`docs/ADVERSARIAL-TESTING.md:686-696`).

Two tests monkeypatch the loader to the absent case on purpose,
`tests/adversarial/test_faults.py:231` and
`tests/unit/test_spark_audit_guards.py:660`, both with
`lambda: (None, "none")`.

Note what is **not** a consumer. `cmd_repair` never calls `_load_baseline`. It
writes `int(declared_status_1_period_ms())` from `the sparkflex: block`
(`cli.py:1354-1355`). The help text now agrees, and `spark repair --help`
reads "restore Status 1 Period from the declared config"
(`cli.py:2241-2242`). It said "from the baseline" until, and
that sent readers to the wrong file.

---

## 7. The pre-25 `serial` field, and the defect that is now closed

### What snapshot used to write on rig-max

rig-max is eight SPARK MAX on firmware 24.0.1, generation `pre25`, on a 1 Mbit
gs_usb adapter named `can0`, measured. Unique ID Broadcast is
apiClass 47, `versionImplemented` 25.0.0 in
`reference/REV-spark-frames-2.1.0.json`. Firmware 24.0.1 does not carry
it. Nothing on that bus BROADCASTS an identity. The frames observed are 0x060,
0x061, 0x062, 0x063, 0x065, 0x066 and 0x067, and no 0x2E0, 0x2E1 or 0x2F0.

Broadcasting none is not the same as having none, and that is what
changed. `inventory()` still collects serials from `UNIQUE_ID_API` frames, and it
now also takes `with_fingerprint=True`, which REQUESTS the per-device value at api
0x094 and fills the same field from the reply. `cmd_snapshot` has passed it. It reads the generation off the first second of the bus, then calls
`inventory(args.window, with_fingerprint=pre25)` (`cli.py:224-230`).
A pre-25 capture records a real identity that way, and a 25+ capture keeps
taking its serial from the broadcast. `spark status`, `spark audit`,
`spark set-id` and `spark learn-serials` pass the same flag.

The writer at `cli.py:275` is:

```python
f"    serial: '{inv[dev]['serial']}'",  # pre-25: the api 0x094 fingerprint
```

An f-string with hard-coded single quotes around it. It prints whatever
`inventory()` left in the field, so a serial reaches the file only when
the read asked for one. Before the read returned `None` on this
generation, and `None` prints as four characters inside those quotes. The
period lookup asked the 25+ api on a pre-25 bus and found nothing
broadcasting. A rig-max capture then read:

```yaml
  1:
    role: drive/LB
    serial: 'None'
    firmware: '24.0.1'
    status_1_period_ms: null
```

`spark-baseline.yaml:14-18` is what the same writer produced:

```yaml
  1:
    role: drive/LB
    serial: '66D3BDE5'
    firmware: '24.0.1'
    status_1_period_ms: 100.0
```

**RESOLVED: `cmd_snapshot` passes `with_fingerprint=pre25`, so it
asks a pre-25 bus for its identity.** `spark-baseline.yaml` was captured that
day with eight real four-byte identities in it, so the file above never reached
disk. Answering the question as it stood before that, in three parts.

**Was the file valid?** Yes. It was well-formed YAML and `yaml.safe_load` parsed
it without complaint.

**Was it poisoned?** Yes, and specifically with the literal string, not a null.
Verified by parsing that exact text: `serial` loads as the `str` `'None'`, which
is truthy. `status_1_period_ms` is a genuine `None`, because the writer branches
on `p1 is not None` and emits the bare token `null` (`cli.py:169`), which
YAML resolves to null correctly. The two fields fail differently. One degrades
cleanly, one does not.

**Is it refused?** No. `cmd_snapshot` has no serial check anywhere. The
completeness gate at `cli.py:137` passes, because all eight controllers do
broadcast (on 0x060 through 0x067, which clear the manufacturer and device-type
filters at `admin.py:367-371`). The file is written, `wrote <path>` is
printed, and the command returns 0.

What happens after that used to be the chain at the end of `cmd_snapshot`.
rig-max's config had no `can_serials` block, so `cmd_learn_serials` ran, found no
serial on the first id, printed `no serial seen -- listen longer with --window 10`
and returned 1 -- advice for a frame that was never going to arrive. Both halves
have moved since. `cmd_learn_serials` requests the fingerprint on pre-25 and
succeeds, and it has been run here, so `spark.yaml` carries all eight
values and the chain no longer fires. What moved is the caller.
`cmd_snapshot` reads the generation first and passes `with_fingerprint=pre25`
(`cli.py:230`), so the value the writer formats on this bus is a real
fingerprint. The rig-max baseline on disk carries eight of them, captured. **The writer itself is still unguarded, so a fingerprint that goes
unanswered would put the literal `None` on disk.**

### What it would have broken

Every subsequent `spark audit` on rig-max would have hit the baseline serial
check in `audit_problems` (`admin.py:2371`):

```python
if ref.get("serial") and info.get("serial") != ref["serial"]:
```

`ref["serial"]` would have been `'None'` and truthy, and `info.get("serial")`
whatever the live bus answered, so the two would have disagreed either way.
Since `cmd_audit` began requesting the fingerprint on this generation, the live
answer is a real four-byte value and the finding would have read:

```
id 1 serial 66D3BDE5 != baseline None
```

Eight of those, every run, exit code 1 forever, with a fix line telling the
operator to re-baseline if the controller was replaced deliberately. It read
`serial None != baseline None` before the fingerprint existed, which was the same
failure with nothing in it for an operator to look at.

The audit already knows how to handle a fleet whose identity cannot be read. The
check against `serials` is generation-aware: `serials_unavailable`
detects that no controller answered with an identity at all, prints one
explanation, and suppresses the per-device identity finding. The **baseline**
serial check has no such guard.

That suppression is no longer the live path on rig-max. Identity IS answered there
now and `serials` is populated, so the config-side check runs properly
and finds the fleet correct.

**RESOLVED.** `cmd_snapshot` now requests the fingerprint on a pre-25
bus (`cli.py:230`), as `spark status`, `spark audit`, `spark set-id` and
`spark learn-serials` do. `spark-baseline.yaml` was captured with eight
real fingerprints in it. They are the same eight the `can_serials` block in
`spark.yaml` carries, so the baseline check has real values to compare.
The other fix, teaching that check to skip a recorded value the running
generation could not have produced, is still open. `audit_problems` compares the
recorded string straight through (`admin.py:2371`), so an older capture
would still fire eight findings.

### What a pre-25 baseline should record instead

Start with identity, which this section used to say could not be recorded at all.
It can. **A pre-25 SPARK MAX answers a per-device identity when it is ASKED**, at
api 0x094, `arb = 0x02050000 | (0x094 << 6) | id`, a zero-length ADDRESSED request
answered with four read-only bytes. Measured on rig-max: eight
distinct values, four bytes each, stable across a two-second gap, not derived from
the CAN id, and unchanged by two attempts to write them. It is the firmware's own
Unique ID -- SET_CAN_ID carries a Unique ID at bit 0 and a CAN ID at bit 32 per
REV-Specs, and sending that frame with this value moved a controller from id 3 to
id 20 and back. So a baseline on this generation can fill the same identity field
a Flex one does, from a request instead of from a broadcast, and a SPARK MAX
swapped for another SPARK MAX carrying identical configuration is no longer
invisible to this tooling.

Two things it is not. It is NOT "the REV serial": REV Hardware Client does not
display it, and pre-25 IDENTIFY does not take it -- that command is addressed by
CAN id and carries no payload at all -- so it cannot be matched against a label on
the case, and `spark learn-serials` prints that caveat when it writes the block.
And read-only is scoped to the two payload shapes that were tried; a magic-guarded
write could exist here exactly as one does for the burn at api 0x072, so do not
record the register as immutable.

Nothing in the parameter table is identity, and that is a separate, exhaustive
measurement rather than a spot check. All 134 ids were read from all eight
controllers. Parameters 47, 48 and 49, which the index documents as `Reserved`
(`sparklib/data/rev_parameter_index.tsv`), read `0xFFFFFFFF` on every one.
Parameter 0 returns each device's own CAN id, which is the self-check proving the
read path is live, and is not identity: it is the thing you already used to
address the frame.

Now the part that is better than a Flex baseline. All 134 parameters, ids 0
through 133, answered a read on all eight controllers: zero
refused, zero silent. The read is api class 48,
`arb = 0x02050000 | ((0x300 | param_id) << 6) | device_id` with a zero-length
frame, replying on the same arb id
(`SparkAdmin.read_legacy_param`, `admin.py:908-922`; `LEGACY_PARAM_ACCESS`
and `LEGACY_PARAM_MAX = 133` at `admin.py:62-63`). So a pre-25 baseline
does not need a proxy for configuration. It can record the configuration.

Concretely, per controller:

- **The fingerprint from api 0x094**, in the `serial` field the schema already
  has. `cmd_snapshot` requests it on a pre-25 bus (`cli.py:230`), and the
  writer labels the field for what it is (`cli.py:275`). rig-max's baseline,
  captured, carries all eight of them. `spark learn-serials` wrote the
  same eight values into `serials`, so the baseline and the config can be
  checked against each other the way rig-flex's are.
- **The parameter table over the undeclared ids**, as raw uint32 words.
  `fleet_param_table` excludes parameter 0 and every id the declared file names
  (`admin.py:2892-2931`). So the baseline and
  `the sparkflex: block` partition the id space on both generations.
  Pre-25 reaches ids 0-133 and 25+ reaches 0-255, of which 185 are implemented
  on 26.1.6.
- **A digest over that table** for fast comparison, with runtime entries
  excluded. Parameter 5 Control Type is a runtime value and not stored
  configuration, so a digest that includes it churns with whatever the robot last
  did. Which other entries in 0..133 are runtime rather than stored is **open**.
  Reading the table twice across a motor-rail cycle with nothing re-applying, and
  diffing, would settle it.
- **The known-good values spelled out** so a human can read the file: parameter 6
  Idle Mode, 13 P 0, 59 Smart Current Stall Limit, 63 Motor Kv, 69 Encoder Counts
  Per Rev, 10 Pole Pairs, 45 Inverted, 52 and 53 Hard Limit Fwd/Rev Enabled.
  Measured on rig-max, two of the 134 vary across the fleet and only
  one of them is configuration. Parameter 0 is each device's own CAN id, which is
  addressing rather than drift. Parameter 13 "P 0" splits by role: 2.0 on the
  drive controllers (ids 1, 4, 5, 8) and 0.1 on the steer controllers (ids 2, 3,
  6, 7). The other 132 are byte-identical across all eight. Two deviate from the REV
  default: parameter 13, and parameter 59 at 40 against REV's 80.
- **Observed status-frame cadences**, keyed by the pre-25 api ids that actually
  exist. `cmd_snapshot` resolves the api by generation (`cli.py:269`), so
  a pre-25 capture reads 0x061 and records a real cadence.
  `spark-baseline.yaml` holds 100.0 ms on all eight, which is this project's
  boot throttle for frame 1. `inventory` times every api it hears and the writer
  keeps only that one field, so six cadences are measured and dropped. Record
  0x060, 0x062, 0x063, 0x065, 0x066 and 0x067 as well, and record that 0x064
  never broadcasts. That last one is not a lost
  write. Measured before and after a rail cycle, this firmware does not emit the
  alternate-encoder frame at all, so a baseline that leaves 0x064 out silently
  looks the same as one where it stopped.
- **Which period set was in force at capture.** This matters and is easy to get
  wrong. REV's pre-25 factory defaults, measured after a rail cycle with nothing
  re-applying, are 0x060=10, 0x061=20, 0x062=20, 0x063=50, 0x065=200, 0x066=200,
  0x067=250 ms. This project's boot throttle,
  `can_bus._SPARKMAX_STATUS_PERIODS_MS` (`can_bus.py:25`) at
  `{0:50, 1:100, 2:100, 3:500, 4:500, 5:1000, 6:1000}`, is applied by
  `apply_boot_config` when the driver constructs a handler. A snapshot taken after the stack has
  started records the throttle, and a snapshot taken on a bare rail records REV's
  defaults. The file must say which. `spark throttle` re-sends that table and
  verifies it by measuring the cadence, since the write is unacknowledged
  (`cli.py:338-383`), so a bare rail can be put into the running state
  before a capture rather than recorded as it fell. `0x067` is in neither table,
  and that bound is measured rather than cautious: on rig-max a
  two-byte 400 ms payload sent to `0x020519C3` -- the api class 6 arbitration for
  frame 7, identical arithmetic to the writes that move frames 0 through 6 -- left
  the cadence at 250 ms before and after across a ten second window. Frame 7 is
  recordable and not settable, which is what `LEGACY_FRAME_MAX = 6` means.
- **The sticky fault word**, the four bytes at offset 2 in 0x060: 16 bits active in the low
  half, 16 bits sticky in the high half. Record it so a later hasReset is
  comparable against something.

The status periods must **not** be recorded as parameters on this generation.
Parameters 158 to 165 reply with a non-zero status byte on pre-25: they are not
parameters there. Periods move on api class 6,
`arb = 0x02051800 + (frame_index << 6) + id`, two bytes little-endian ms, DLC 2,
with no acknowledgement (`LEGACY_SET_PERIOD`, `admin.py:36`;
`SparkAdmin.set_legacy_status_period`, `admin.py:856-885`).

Two warnings for anyone implementing this.

Never sweep past parameter 133. `_legacy_param_arb` refuses above
`LEGACY_PARAM_MAX` (`admin.py:887-893`) because the same api range carries
commands and 255 lands on Persist Parameters (verified against
`REV-spark-frames-2.1.0.json`; ids between the table and 255 map to frames the
spec does not define, which is its own reason not to send them).

A baseline capture must stay read-only, and that matters more than it used to.
A plain write on this path reaches RAM, which was proven rather than assumed:
Idle Mode was set to BRAKE on all eight, confirmed by read-back, the rail was
cycled, and all eight came back COAST, the factory default. PARAMETER_WRITE on
api class 14 is `versionImplemented` 25.0.0 in the vendored spec, so 24.0.1 never
carried it, and an unmatched extended id is dropped without a reply. That is why
writes looked impossible until the api class 48 path was found. Pre-25 has its
own burn-flash on api 0x072, this package deliberately does not send it, and it is known to WORK: given the two-byte magic it commits the
parameter table, so a stray burn during a capture would make whatever happens to
be in RAM permanent and spend a flash cycle doing it. Nor is a zero-length frame a
safe probe here, which is the other thing that run taught: CLEAR_FAULTS (api
0x06E) executes on one, and an api sweep justified on "zero length is request
semantics on this firmware" erased the sticky words on two controllers. A capture
reads. It does not write, it does not burn, and it does not sweep.

### What the burn changes about a pre-25 baseline

Measured on rig-max, firmware 24.0.1: api 0x072, arbitration id
0x02051C80 | id, carrying two bytes of the magic 15011 little-endian, replies
0x00 and commits the parameter table to flash. Ids 3 and 4 were written Idle Mode
COAST -> BRAKE and burned, id 8 was written the same value and not burned, and
after a motor-rail cycle the two burned controllers read BRAKE while the control
read COAST. The wire contract is `docs/PROTOCOL.md` section 3c, "The burn:
api 0x072, and what it commits", and the run is `pre25.burn_flash_api` in
`provenance.py`.

That changed, and a captured table is now a restore source on both
fleets. rig-flex reads every parameter 0 to 255 and writes them through
PARAMETER_WRITE, and `spark persist` commits them, which is how controller 12's
four status periods went back. rig-max reads and writes the same 134 values on its
own dialect and can commit them too. Read the drift, write
the recorded value, burn it, and it holds across the next power cycle. Nothing in
this package does that today -- no command sends 0x072, and the arbitration id
stays on the injector denylist in `tests/support/sparkhw/wire.py` -- but a
capture is no longer a record of something unrepairable, which is the argument
that kept a pre-25 baseline looking pointless.

Three limits on that, all three load-bearing.

**The status periods are not in it.** Id 3 had 0x060 set to a distinctive 77 ms,
neither REV's pre-25 default of 10 nor this package's boot throttle of 50, and
was burned and accepted 0x00. After a rail cycle it read 10.0 ms like every other
controller. The periods move on api class 6 and the burn does not reach them, so
the cadence half of a baseline stays a record of what was on the wire.
`spark throttle` remains the only way to put it back and is still mandatory
after every power event.

**A burn spends a flash cycle.** Flash endurance is finite and nothing here has
measured it, so "read the baseline, restore, burn" is not something to wire into
a check that runs on every boot. Whether routine burning is wise is open, and it
is a different question from whether the frame works.

**The burn commits configuration, not identity.** A MAX swapped for another MAX
carrying the same burned configuration is caught by the fingerprint or it is not
caught at all, which is why the identity field earns its place in a pre-25
baseline even though every configuration value beside it is readable too.

### The identity commands, which used to be the gap

`spark set-id` and `spark identify` both addressed their target by a serial no
pre-25 controller broadcasts, and neither could reach one. Both work now, and they
are addressed differently from each other, which is the part to remember.

`spark identify --id N` blinks an LED. Pre-25 identify is `IDENTIFY_UNIQUE | dev`
with an EMPTY payload, addressed by CAN id and taking no serial at all, while the
25+ form -- broadcast on device 0 with a four-byte serial -- is dropped in silence
on 24.0.1. That is why the command used to print `its LED should blink` and exit 0
over a frame no controller could match. The pre-25 form was recovered by capturing REV Hardware Client's own LED button: three DLC-0 frames,
02051D81, 02051D82 and 02051D83, one per controller blinked, in 9831 lines of
candump, reproduced from this driver and confirmed by an operator.
`tools/spark_blink.py` is the standalone tester.

`spark set-id --serial X --to N` uses the standard SET_CAN_ID form with the
fingerprint as the Unique ID, and it moves the controller -- id 3 to id 20 and
back, on rig-max. It is RAM ONLY: `set_can_id` finishes with PERSIST_PARAMETERS,
which 24.0.1 does not carry, so the move reverts at the next power cycle. The
command says so and exits 1 rather than claiming a success it cannot keep. An id
that has to survive a rail cycle is set over USB-C in REV Hardware Client.

Duplicate detection works here too, which matters for this document because it is
the one check a baseline cannot substitute for. `SparkAdmin.duplicates` keys on
UNIQUE_ID broadcasts on 25+ and on the REQUESTED fingerprint on pre-25, where it
asks every id in 1..62 and counts the distinct answers, so
`duplicate_detection_available` returns True on both generations and
`cmd_duplicates` no longer prints `CANNOT TELL`. It was verified on rig-max by
INJECTING a second reply on the fingerprint arbitration id rather than by moving a
real CAN id: clean before, detected during, clean after, and no controller
changed. `read_fingerprint` discriminates by frame LENGTH and not by `is_rx`
because of that test -- SocketCAN flags every locally generated frame as loopback,
so an `is_rx` filter dropped all 124 injected replies.

---

## 8. rig-max's baseline, and the two defects that delayed it

One baseline per bus, written beside its config. rig-max's
was the last to arrive, and two defects held it up. They are recorded here in
order of how much they mattered.

**RESOLVED: `spark-baseline.yaml` exists.** `spark snapshot --write`
was run on a complete bus after `cmd_snapshot` was fixed for this generation. The
rest of this section is the reasoning that kept it from being run before, kept
because the two defects it describes were real and the fix is only meaningful
next to them.

**Running it before would have produced the poisoned file described in
section 7.** Eight `serial: 'None'` entries, eight `status_1_period_ms: null`, and
a `spark audit` that exits 1 with eight meaningless findings from then on --
comparing a real fingerprint on the wire against the string `None` in the file.
The one field that would have been recorded correctly is `firmware: '24.0.1'`,
since `GET_FIRMWARE` answers on that firmware. `cmd_snapshot` now requests the
fingerprint when the bus is pre-25 (`cli.py:230`). It also takes the
Status 1 api from `API_SETS` for the generation it found (`cli.py:269`), so
both fields carry real values in `spark-baseline.yaml`.

**The whole baseline design was built on rig-flex and rig-flex-2, which are SPARK Flex
on 26.1.6.** Both of those files record `controller_type: sparkflex`. The design
assumed that identity arrives unprompted and that configuration cannot be asked
for, and neither assumption survived. `cmd_snapshot` now requests the
fingerprint on pre-25, and both generations answer a parameter read in their own
dialect. The schema took one new `meta` key to hold the difference.
`spark-baseline.yaml:10` records `generation: pre25`, and `_load_baseline`
reads both fleets the same way.

The adjacent gaps on rig-max had the same root and are closed.
`spark.yaml` holds a `sparkmax:` block beside the `sparkflex:` one, and the product now
selects it: `MOTOR_DEFAULTS_KEYS` maps each product to its own block and
`set_controller_type` declares which one this process reads, so `_defaults_path`
no longer resolves the Flex block whatever the robot is. `spark defaults` prints
MAX values on a MAX host, and `spark audit` reads the readable deviating
parameters back over CAN and compares them against that file rather than
reporting them unverifiable.

**Decided.** A pre-25 baseline is the same file, and `meta` now carries
a `generation` key recording which dialect the bus spoke. An optional
`parameters` block holds the undeclared ids as raw uint32 words. The sweep runs
on both generations and reaches ids 0 to 133 on pre-25. rig-max's file was
captured before that block existed, so it records identity and cadence only. The
full-table comparison sits beside `audit_problems` in `baseline_param_problems`
(`admin.py:2934-2973`), which runs on every audit and returns NOTES.
`cmd_audit` prints them under a heading of their own, apart from `problems`, so
the exit code stays where `audit_problems` left it (`cli.py:2006-2011`).

**Still open.** How large that note list should be allowed to grow.
`baseline_param_problems` emits one note per controller per differing id, so a
parameter that moved across the fleet prints eight lines. rig-flex's recorded
table carries 138 ids, so a broad drift prints a long list.

A second question joined it, when the pre-25 burn was measured
working. If a captured table can be written back and committed, a rig-max baseline
is a restore source and not only a reference, which raises the stakes on getting
its schema right and raises a new one: whether a restore should burn at all, and
if so on whose say-so. Section 7 has the constraints. The burn commits the
parameter table and not the status periods, it costs a flash cycle, and nothing
in this package sends the frame today.

---

## Related

- `docs/README.md`, the frame layout keys on firmware version, not product.
- `docs/PROTOCOL.md`, what REV actually specifies, plus section 3c for the
  pre-25 dialect this fleet speaks: the api class 48 parameter access and the
  api 0x072 burn, both measured on rig-max and neither published by REV.
- `docs/FAILURE-CATALOGUE.md`, the failure patterns the audit checks for.
- `docs/SPARKMAX-BRINGUP.md`, the pre-25 bus.
- `docs/SETUP.md`, where a first baseline belongs in bring-up.
- `the `sparkflex:` block of spark.yaml`, the declared
  configuration, which is a different file answering a different question.
