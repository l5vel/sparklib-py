# Motor Configuration & Verification

Self-contained guide for setting up and verifying the swerve base motors
(steer + drive + cancoder per corner). Use this directory the first time
you bring up a corner, after replacing any motor or cancoder, after a
firmware update, or whenever wheels appear miscalibrated.

These tools live in
`tools/`. It reuses CAN / hardware-bus
infrastructure from the rest of the package
(`sparklib.can_bus.SparkBus`,
`sparklib.netdev.CanNetdev`,
`sparklib.controller`). All CAN IDs, software offsets,
controller type, and bus names are sourced from your active YAML config
(`spark.yaml`) via
`sparklib.config` -- re-export points are in
the config block in `spark.yaml`. Run them from a checkout:

```bash
uv run python tools/cancoder_config_check.py --corner all
uv run python tools/steer_stress_test.py  --corner LF --label spin --spin-seconds 5
```

For end-to-end configuration of sparkflex vs sparkmax (installer, systemd,
phoenix6 wheel selection, runtime verify gate), see
the [README](../README.md).

## Hardware reference

Below is the reference mapping for the SparkArm robot this toolkit was
originally built against. **The active values are whatever is in your YAML
config under `devices` and `base.module_offsets_deg`** -- this table is
documentation, not configuration.

```
Corner | Steer CAN | Drive CAN | CANcoder CAN | Software offset (deg)
-------+-----------+-----------+--------------+-----------------------
LF     | SLF=9     | LF=10     | 21           | LF_OFFSET
RF     | SRF=11    | RF=12     | 22           | RF_OFFSET
RB     | SRB=13    | RB=14     | 23           | RB_OFFSET
LB     | SLB=15    | LB=16     | 24           | LB_OFFSET
```

In both **sparkflex** and **sparkmax** modes, steer + drive Sparks live on
**can0** (a gs_usb / candleLight USB-CAN adapter) and cancoders on
**canivore** (CTRE CANivore). `controller_type` still selects the Spark CAN
frame format and phoenix6 version band, but both families share the same
gs_usb netdev. Mismatched buses = no STATUS frames; a fast-fail check lives
in every script.

The four `*_OFFSET` constants come from `_CONFIG.base.module_offsets_deg`.
They map "cancoder absolute degrees at wheel-zero" to "0 degrees in
wheel-frame." Update the YAML config whenever the wheel-zero reference
changes -- no script edit needed.

## When to run what

| Situation | Run |
|---|---|
| First-time setup of a fresh robot | [SETUP.md](#setup-from-scratch) below, top to bottom |
| Suspect a wheel pointing the wrong direction | `uv run python tools/cancoder_config_check.py --corner all` then [ALIGNMENT.md](SWERVE-ALIGNMENT.md) |
| Replaced a CANcoder | `uv run python tools/cancoder_config_check.py --corner <name>` to confirm presence + magnet, then `cancoder_calibrate.py` |
| Replaced a motor | `uv run python tools/cancoder_config_check.py --corner <name>` to confirm CAN, then `uv run python tools/steer_stress_test.py --corner <name> --label spin --spin-seconds 5` |
| Wheel offsets seem wrong | `cancoder_calibrate.py` |
| `cancoder_config_check.py` reports `[MISMATCH]` on `magnet_offset` | `cancoder_reset_flash.py --corner <name>`, then `cancoder_calibrate.py --corner <name>` |
| `cancoder_config_check.py` reports sticky_faults `[CHECK]` on a cancoder | `cancoder_reset_flash.py --corner <name>` (also clears sticky) |
| Encoder values drift mysteriously between runs | First check [TROUBLESHOOTING.md](TROUBLESHOOTING.md) for the flash-wipe trap |
| Stress / repeatability under load | `steer_stress_test.py --corner all --label spin` |
| Steer wheels oscillate / overshoot / need gains | `steer_tune.py --module LF --profile coarse` (and `--profile fine`); see [Steer tuning](#steer-tuning) |
| Base rotates the wrong way (teleop + nav spins to opposite heading) | Set `base.swerve.reverse_rotate: true`; see [TROUBLESHOOTING.md](TROUBLESHOOTING.md) |
| Quick "does every motor spin" check | `spin_all_motors.py` |
| Verify steer convergence to one angle | `steer_pid_check.py --module LF --target-deg 45` |
| Sanity-check the steer position decode | `steer_decode_check.py` |
| **Whole SPARK bus is silent, controllers powered** | wake it with a read first (`tools/spark_silent_bus_probe.py`), then `uv run spark faults`; `uv run spark clear` wakes it too and erases the sticky `hasReset` |
| **"Did a motor lose its config?"** | `uv run spark audit` -- exits non-zero, names the controller |
| **Two controllers on one CAN ID** | `uv run spark duplicates`, then `uv run spark set-id --serial <s> --to <n>` |
| **Which physical motor is CAN ID N?** | `uv run spark identify --serial <s>` -- blinks that one controller's LED |
| **Motor rail voltage / brownout history** | `uv run spark voltage` |
| **Decoded faults, warnings, temperature** | `uv run spark faults` |
| **After re-provisioning a motor, in RHC2 or with `spark provision`** | `uv run spark learn-serials --write`, then `uv run spark snapshot --write` |
| CAN bus errors, adapter re-enumeration, TX wedge | `python tools/can_diag.py` |

## The `spark` CLI

`spark` is the CAN-side admin tool: inventory, config-drift audit and remote
repair of SPARK Flex/MAX controllers. It never sends a setpoint and never starts
the enable heartbeat, so controllers stay disabled and cannot actuate while it
runs. Frame definitions come from REV's published spec (see
the product blocks of `spark.yaml`).

```bash
uv run spark status          # ids, roles, serials, firmware, config canary
uv run spark audit           # drift + duplicates vs the baseline; exit 1 on fault
uv run spark faults          # decoded faults/warnings + limit-switch interlock state
uv run spark voltage         # per-controller rail voltage + pack state of charge
uv run spark clear           # clear latched faults; wakes a silent bus
uv run spark duplicates      # CAN ids answered by more than one controller
uv run spark identify --serial 498B2579
uv run spark set-id --serial 498B2579 --to 17
uv run spark repair --id 12 --persist            # Status 1 Period alone, Flex only
uv run spark provision --id 12 --write --persist # every declared setting, both generations
uv run spark params --id 12 --param 6            # read the parameter table over CAN
uv run spark snapshot --write        # capture a known-good bus as the baseline
uv run spark learn-serials --write   # one-time: record serials into the base config
```

### Identity is the serial, not the CAN ID

Every SPARK broadcasts a 4-byte hardware serial on API `0x2F0`. That is the only
stable identity: a CAN ID can be lost, duplicated or reassigned, a serial cannot.
`learn-serials` records the id-to-serial mapping into `serials` once per
robot, after which `audit` can tell "this controller was swapped" apart from
"this controller changed id".

Two controllers sharing a CAN ID are invisible to an id scan -- they look like one
device. `duplicates` finds them by spotting two distinct serials behind one id,
and `set-id` fixes it in place: the frame carries the serial, so only the matching
controller acts. No isolation, no one-at-a-time power-up.

### What can and cannot be read back

On SparkFlex firmware 26.1.6, measured on hardware: **both directions work**.
`PARAMETER_WRITE`, `PERSIST_PARAMETERS` and `SET_CAN_ID` all succeed and return a
result code, and every parameter 0-255 reads back over `READ_PARAMETER` provided
the request is a remote frame carrying dlc 8. Zero-length data frames draw
silence, which is what this package sent until  and why reads were
recorded as unavailable. A full config read-back is available now, so drift is
compared against the declared file rather than inferred from broadcast behaviour
(the Status 1 Period canary in [TROUBLESHOOTING.md](TROUBLESHOOTING.md) still
covers what a read cannot: whether a stored value was applied).

### Safety interlocks are write-protected

Hard limit switches wired to the data port are an e-stop-equivalent interlock. A
triggered limit is a state to respect, not a fault to clear. Parameters 50-53
(limit switch polarity and hard limit enables) are refused by
`SparkAdmin.write_param`, which raises `ProtectedParameterError` -- no `spark`
command can disable them.


## Steer tuning

SparkFlex steer is host-side P(D) control (`swerve_drive._STEER_GAINS`). Two gain
sets -- **coarse** (full-nav, fast tracking) and **fine** (terminal approach,
near-zero overshoot) -- auto-switched by `BaseHandler` at `FINE_APPROACH`.
`steer_tune.py` sweeps `kp x kd x max_out x deadband`, scores classic
step-response metrics (overshoot / settle / limit-cycle), and prints
paste-ready gains. Use `--profile coarse|fine` to set the right steps + weights
and which config keys to emit. Robot on a stand / wheels off the ground.

```bash
uv run python tools/steer_tune.py --module LF --profile coarse \
    --kp 0.006,0.008,0.010 --max-out 0.35,0.40,0.45
uv run python tools/steer_tune.py --module LF --profile fine \
    --kp 0.004,0.006,0.008 --max-out 0.20,0.25,0.30
```

Notes: keep `kd=0` (an un-filtered derivative at 20 Hz destabilizes; lower
`max_output` is what tames overshoot). Tune at the battery voltage you run at --
steer duty->torque scales with bus voltage.

## Setup from scratch

This is the workflow for a robot you're touching for the first time, or
after rewiring/swapping motors. Every step has a checkpoint: don't proceed
until the checkpoint passes.

**Each product has its own declared configuration.** `MOTOR_DEFAULTS_KEYS` in
`sparklib/admin.py` maps `sparkflex` and `sparkmax` to their own blocks. `sparklib.cli`
reads `controller_type` and passes it down through `set_controller_type`, so
`motor_settings()`, `declared_value()` and the audit coverage note all resolve the
right file. `require_motor_defaults_for` refuses any command that would write one
product's numbers through the other's frames. `spark repair` refuses a pre-25 bus
outright, because no parameter id for its Status 0 Period is verified there.
`spark provision` runs on both generations and takes its write dialect from the
target controller's own reading.

### Step 0 -- Power on, prerequisites

Power the robot, plug in the gs_usb USB-CAN adapter, connect the CANivore, and
install the swerve extra with the phoenix6 major your CANcoders run:

```console
uv sync --extra swerve-25     # CANcoders on 25.x firmware
uv sync --extra swerve-26     # CANcoders on 26.x firmware
```

**Get that major right before anything else.** CTRE require the device firmware
major to match the API major, and a mismatch fails quietly rather than loudly:
the encoders report `Firmware Too Old`, every read times out, and absolute
position comes back as a constant 0.000. A settle check accepts a constant,
because nothing is stiller than a number that never changes, so the failure
reaches you as four dead encoders or as four badly mounted magnets. Phoenix
Tuner X field-upgrades the CRF if you would rather move the encoders forward.

**Checkpoint:** both netdevs exist, `ip link show <spark bus>` and
`ip link show <cancoder bus>`. The CANivore wants `can <FD>` with a `dbitrate`;
a missing one is its FD bring-up and not dead hardware. Then
`uv run python tools/cancoder_audit.py` reads every encoder and reports magnet
health. Otherwise stop, because the bus is not present.

### Step 0b -- Record the SPARK hardware serials (once per robot)

Do this immediately after the CAN IDs are set in RHC2, before anything else.
Every SPARK broadcasts a 4-byte hardware serial that is its only stable
identity -- a CAN ID can be lost, duplicated or reassigned, a serial cannot.

```bash
uv run spark clear            # if the bus is silent after a power cycle
uv run spark learn-serials    # dry run: shows what it would record
uv run spark learn-serials --write
```

This writes a `can_serials:` block into the robot's own
`spark.yaml`, keeping a `.bak`. It refuses to
run unless every configured CAN ID is broadcasting, so a partial bus cannot
record a wrong mapping.

Once recorded, `spark audit` can tell **"this controller was swapped"** apart
from **"this controller changed CAN ID"**, and `spark duplicates` can name which
physical corner is colliding when two controllers share an ID.

**If you forget, it is filled in for you:** `spark snapshot --write` (Step 0c)
records the serials first when `serials` is missing, so the baseline
can never be captured against an unidentified bus.

### Step 0c -- Capture the baseline

With the bus known good:

```bash
uv run spark snapshot --write
```

Writes `spark-baseline.yaml` -- serial, firmware and
provisioned status-frame period per controller, keyed by base index and bound to
this host through the same config resolution the rest of the package uses. On
firmware 25+ it also records the undeclared parameter ids as raw uint32 words, 138
of them on rig-flex. It refuses to record that table while any undeclared id differs
across the fleet or any controller missed an id, and `--no-parameters` skips it.
`spark audit` compares against the file thereafter, and reports a baseline
disagreement as a note, because nothing declares those ids.

Capture this only when the bus is genuinely healthy: a baseline taken from a
drifted controller enshrines the drift as correct.

### Step 1 -- Verify all 4 cancoders are alive

```
uv run python tools/cancoder_config_check.py --corner all
```

This is the **first real diagnostic**. For each corner it reads the
device config (no writes) and reports:

- `magnet_health` -- must be `MAGNET_GREEN`. Anything else means the magnet
  is too weak, too strong, or off-axis. Fix the mechanical mount.
- `magnet_offset` (persisted in flash) -- should match what you expect.
  For our setup it should be `+0.000 deg` (we use software offsets in
  [motor_test.py](../sparklib/data/spark.yaml), not flash offsets).
- `sticky_faults` -- should be `0x00000000`. Any non-zero requires
  power-cycling the cancoder, then checking [TROUBLESHOOTING.md](TROUBLESHOOTING.md).
- `firmware` -- log the value; mismatch across corners can cause subtle
  behavior differences.
- `absolute_position` -- live read. This is what the cancoder *currently*
  sees, in cancoder-frame degrees. Used in step 2.

**Checkpoint:** all four corners report `MAGNET_GREEN` + sticky `0x0` +
`magnet_offset = 0`. If any corner is missing entirely (timeout / no
frame), it's a CAN ID or wiring issue, not a software issue.

### Step 2 -- Physically align all wheels to "wheel-zero"

Read [ALIGNMENT.md](SWERVE-ALIGNMENT.md). The robot's "zero direction" is what we
choose to call `wheel_deg = 0`. Typically: all four wheels pointing
forward, parallel to each other, drive faces forward.

**Tools:** a straightedge laid across both wheels of an axle is enough to
get within 1 deg. A printed protractor on the wheel hub helps for fine work.

**Checkpoint:** all four wheels are physically pointing in your chosen
zero direction, mechanically held there (chocks / blocks / human hands)
while you do step 3.

### Step 3 -- Capture each corner's wheel-zero offset

Wheels are aligned and held. Now read the cancoder for each corner and
record the absolute degrees:

```
uv run python tools/cancoder_calibrate.py
```

Interactive script. For each corner: confirms wheel is at zero, takes 50
samples, averages them, prints the value, asks you to type it into
[motor_test.py](../sparklib/data/spark.yaml). Also prints jitter -- if jitter
> 0.5 deg while the wheel is locked, the magnet/bus is suspect (see
[TROUBLESHOOTING.md](TROUBLESHOOTING.md)).

After updating motor_test.py: `LF_OFFSET = <captured value>`, etc.

**Checkpoint:** all four `*_OFFSET` in motor_test.py are updated.

### Step 4 -- Verify offsets land at zero

Release the wheels (let the motor hold them, or leave free; either is fine
for this verification -- we're just reading). Run:

```
uv run python tools/cancoder_config_check.py --corner all
```

The `wheel_deg` line for each corner should now read **close to 0 deg**
(typically within +/-1 deg if you released the wheel; tighter if held).

If a corner reads 90 deg or some other large number, the offset is wrong by
that amount. Re-run step 3 for that corner.

### Step 5 -- Steer motor smoke test (per corner)

For each corner one at a time, drive the steer to the existing wheel
position (a no-op P-loop) to verify the motor responds:

```
uv run python tools/steer_stress_test.py --corner LF --label spin --spin-seconds 5
uv run python tools/steer_stress_test.py --corner RF --label spin --spin-seconds 5
uv run python tools/steer_stress_test.py --corner LB --label spin --spin-seconds 5
uv run python tools/steer_stress_test.py --corner RB --label spin --spin-seconds 5
```

(or one corner at a time on the bench while observing physically.)

**Checkpoint:** each spin completes without errors; reported `delta` is
under ~1.5 deg (deadband-bound); `long_ticks=0`.

### Step 6 -- All-corner stress test

Final check: simultaneous spin on all four corners.

```
uv run python tools/steer_stress_test.py --corner all --label spin --spin-seconds 30
uv run python tools/steer_stress_test.py --summary
```

**Checkpoint:** all four corners report deltas under ~1.5 deg, `long_ticks=0`,
no `STICKY` faults appear. Diagnosis output classifies all corners as
"system survives stress", since static-settle is the default settle mode.

### Step 7 -- Drive motor smoke test

Lift the robot or place on jack stands so the wheels are free to rotate.
Drive each in turn (use the existing `run_drive_sequence` in motor_test.py
or write a one-off; future versions will have a dedicated `--label drive-spin`).

**Checkpoint:** each drive motor responds to commanded duty, sticky faults
stay clear under load.

## Conventions and invariants

These hold true across the codebase. If something violates one, that's the
bug, not the convention.

1. **Software offsets, not flash offsets.** Device-side `magnet_offset` is
   `0` for all cancoders. `*_OFFSET` constants in motor_test.py do the
   conversion. This is so calibration lives in version control, not in
   per-device flash, and so accidentally running a script that writes
   default config to the cancoder doesn't destroy calibration. (See the
   flash-wipe footgun in [TROUBLESHOOTING.md](TROUBLESHOOTING.md).)

2. **Wheel-frame degrees = cancoder-frame degrees - offset.** Any time
   you see `wheel_deg = abs_deg - offset`, it's this. The wrap to
   [-180, +180] happens in `steer_error_deg`.

3. **can0 = sparks; canivore = cancoders.** Don't co-locate. The
   gs_usb `can0` adapter is intentionally low-bandwidth (USB-CAN);
   putting cancoders on it would saturate. The CANivore handles cancoder
   traffic.

4. **The deadband is a feature, not a bug.** `DEADBAND_DEG = 1.5 deg` in
   motor_test.py keeps the controller from chattering near target. It
   means the wheel can rest anywhere in `[-1.5 deg, +1.5 deg]` after settling.
   Pre/post comparisons should use static-settle to measure where the
   wheel actually stopped, not assume tighter convergence than the
   controller can deliver.

## See also

- [ALIGNMENT.md](SWERVE-ALIGNMENT.md) -- physical alignment procedure (step 2 above)
- [TROUBLESHOOTING.md](TROUBLESHOOTING.md) -- common failure modes and fixes
- the config block in `spark.yaml` -- operational steer + drive
- [`steer_stress_test.py`](../tools/steer_stress_test.py) -- repeatability + stress
- [`cancoder_config_check.py`](../tools/cancoder_config_check.py) -- per-corner config inspector
- [`cancoder_live.py`](../tools/cancoder_live.py) -- live angle readout while you turn a wheel, with `--raw` for rotations and `--corner` for one at a time
- [`cancoder_reset_flash.py`](../tools/cancoder_reset_flash.py) -- **WRITES FLASH.** Resets `magnet_offset` to 0 and clears sticky_faults. Run only when `cancoder_config_check.py` reports a non-default `magnet_offset`.
- [`cancoder_clear_faults.py`](../tools/cancoder_clear_faults.py) -- More aggressive sticky-fault clear. Retries `clear_sticky_faults()`, decodes the bit map, falls back to per-fault clears. Use when `cancoder_reset_flash.py` left sticky bits set.
