# Troubleshooting

Common failure modes seen on this swerve base, ordered by how often they
bite. If your symptom isn't here, work top-to-bottom -- earlier entries are
more frequent.

---

## "The wheel-zero offset keeps changing between runs"

### Symptom
You calibrated `RF_OFFSET = 121.20`, ran the robot for a while, came back
later, and now the wheel points 30 deg off when commanded to zero. Re-calibrate;
get a different value (e.g., 22.15). Comes back later, different again.

### Root cause: `cancoder.configurator.apply()` somewhere in your workflow

`CANcoderConfiguration()` with no args has `magnet_offset = 0.0`.
`configurator.apply(...)` commits to flash. Any script that calls this
will overwrite whatever device-side calibration was there with `0`. If a
previous user/tool had set a non-zero `magnet_offset`, every absolute
reading shifts by that previous offset on the next read.

### How to verify

```
uv run python tools/cancoder_config_check.py --corner <name>
```

Look at the `magnet_offset:...  [persisted in flash]` line. In our setup
it should always be `+0.000 deg`. If you see a non-zero value, something
wrote it.

### Fix

1. Find the script that's writing config and remove the `apply()` call.
   The live readout that replaced it is
   [`cancoder_live.py`](../tools/cancoder_live.py), which reads and never
   applies. Every tool here that writes flash says so in its own docstring and
   asks before it writes.
2. After removing, re-calibrate (the previous offset is gone). Future
   runs should be stable.

### How to prevent recurrence

[`cancoder_config_check.py`](../tools/cancoder_config_check.py) prints the
`magnet_offset` held in flash for every corner, and compares it against what you
expect. Run it after any work on a CANcoder and an unintended offset shows up
straight away.

---

## "The encoder seems messed up after the motor spins hard"

### Symptom
Pre-spin and post-spin readings differ by ~1.5 deg for the same physical
wheel position. Larger after longer/harder spins. Sometimes signed
random across runs, sometimes consistent.

### Root cause: deadband, not encoder

`steer_output` returns 0 for any `|err| < DEADBAND_DEG = 1.5 deg`. The wheel
coasts into the deadband, friction stops it wherever it lands, controller
applies zero torque. Where exactly inside `[-1.5 deg, +1.5 deg]` depends on
approach direction and friction state -- both of which differ pre-spin vs
post-spin.

### How to verify

Run consecutive spin tests without power-cycling. Per-corner deltas
should oscillate in sign across runs (e.g., LF: `+1.15, +0.05, -1.20,
+0.40`) rather than accumulating in one direction (e.g., `+1.15, +2.30,
+3.45`). Random within the deadband band = deadband artifact. Monotonic
growth = real magnet/coupling slip.

```
uv run python tools/steer_stress_test.py --corner all --label spin --spin-seconds 30
# repeat 5x without restarting
uv run python tools/steer_stress_test.py --summary
```

### Fix

Use `--settle-mode static`, which is already the default in `steer_stress_test.py`
and in `record_spin`. Static-settle
measures where the wheel actually rests instead of demanding tighter
convergence than the deadband can deliver. Reported deltas drop from
~1.5 deg to ~0.1 deg.

If you need tighter than 1.5 deg in operational driving, lower
`DEADBAND_DEG` per maneuver -- but at low duty the motor may not overcome
stiction, so test before relying on it.

---

## "STATUS_0 not received" on startup

### Symptom
Script bails with: `ERROR: SRF (CAN ID 11) STATUS_0 not received after 3s.`

### Root cause: bus mismatch or no power

The can0 bus is up but the spark is either not powered, not
connected, or on a different bus.

### How to verify

```
ip link show can0           # interface up?
sudo candump can0 | head    # any traffic at all?
candump can0 | grep -i 02055d80   # the spark's STATUS_0 frame ID range
```

If `candump` shows traffic from other CAN IDs but not yours, that spark
specifically is offline. If no traffic at all, the bus is up but no
device is talking.

### Fix
- Power-cycle the offending controller.
- Verify CAN cable is seated at both ends.
- If the spark was previously bricked / unflashed, REV Hardware Client
  can recover it.
- Check the CAN ID matches expectation (CAN ID is set in REV Client; the
  table in [README.md](SWERVE-SETUP.md) is the canonical
  list for this codebase).

---

## "magnet_health: ORANGE" or "RED"

### Symptom

`cancoder_health_report` reports magnet health below GREEN.

### Root cause: magnet too far from / too close to / off-axis from the cancoder IC

The CTRE CANcoder uses a Hall-effect IC sensing a diametrically-magnetized
disk magnet. The magnet must be:
- Centered on the IC axis (within ~0.5 mm)
- At correct height above the IC (typically 0.5-2 mm; check CTRE datasheet)
- Strong enough (a degraded magnet weakens with age/heat)

ORANGE = marginal but works. RED = below-threshold; readings unreliable.

### Fix
- Reseat the magnet on its shaft.
- Verify the cancoder body hasn't shifted on its mount.
- If the magnet is loose on the shaft (set screw, glue), tighten / re-bond.
  Aggressive spinning over time can loosen a marginal bond.
- If a magnet that used to be GREEN is now ORANGE/RED, the magnet is
  failing. Replace it.

A loose magnet that physically rotates on the shaft will *also* shift
your software offset -- manifests as the "offset keeps changing" symptom
above, but in this case the cause is mechanical not flash-write.

---

## "Wheel converges then drifts off"

### Symptom
The P loop reaches target, declares settled, then over the next several
seconds the wheel slowly creeps to a different angle.

### Root cause: gravity / cable tension / unbalanced wheel

After the P loop hits deadband, output = 0 and there's no holding
torque. Whatever passive force (gravity on a tilted wheel, cable
flexure pulling the wheel back, weight imbalance) takes over and slowly
re-orients the wheel.

### How to verify
With the loop stopped and motors at 0%, watch the cancoder for 30 s. If
absolute_position changes by more than the noise floor (~0.1 deg), passive
forces are at work.

### Fix options
- Mechanical: balance the wheel, route cables to not pull on the
  steer shaft, eliminate the tilt.
- Software: hold a small percent_output (e.g., +/-0.05) when the wheel is
  "parked" -- overcomes the passive forces but is wasteful (heat, current).
- Software (better): use position mode on the spark instead of percent
  mode -- the spark holds the commanded position with full PID. Off-by-default
  in motor_test.py because position mode requires careful seeding of the
  motor's internal encoder; see `STEER_CONTROL_MODE` in
  [motor_test.py](../sparklib/data/spark.yaml).

---

## "Drive controller faulted under stress -- sticky_faults bit 0x20"

### Symptom

`steer_stress_test.py --summary` (or end-of-run line) reports for a specific corner:
```
[<corner>/drive] sticky changed 0x00 -> 0x20 during run <id>.
Drive controller faulted under stress.
```

### What 0x20 means

On SparkFlex sticky_faults byte (STATUS_1[3]), bit 0x20 = **stall fault**.
The controller observed motor current consistent with stall (current drawn
but RPM not rising as expected).

### How to verify the cause

The fault doesn't tell you *why* it stalled. Run these to discriminate:

```
# 0. Charged battery, then repeat the run that failed:
uv run python tools/steer_stress_test.py --corner all --label spin \
    --spin-seconds 30 --drive-output 0.3 --drive-pattern mirror

# 1. Same corner, single direction (no flips):
uv run python tools/steer_stress_test.py --corner <name> --label spin \
    --spin-seconds 30 --drive-output 0.3 --drive-pattern static

# 2. Same corner, lower duty:
uv run python tools/steer_stress_test.py --corner <name> --label spin \
    --spin-seconds 30 --drive-output 0.15 --drive-pattern mirror

# 3. All four corners, mirror, same duty:
uv run python tools/steer_stress_test.py --corner all --label spin \
    --spin-seconds 30 --drive-output 0.3 --drive-pattern mirror
```

Diagnostic matrix:
- A charged battery clears it on every corner -> the pack was sagging under
  simultaneous load, and every controller was configured correctly. On a depleted
  pack three of four corners faulted at 0.3 duty, and all four ran clean at that
  duty after a swap. The corner that stayed clean sat below the current threshold
  and its configuration matched the others. Run this first, because it is the
  cheapest step here and it explains an N-1-of-N pattern that otherwise reads as
  three bad controllers.
- `static` clears it but `mirror` reproduces -> direction-reversal energy
  triggering stall detection. Either reduce flip frequency
  (`SPIN_FLIP_PERIOD` in steer_stress_test.py) or accept it as expected at this
  duty.
- Lower duty clears it -> stall threshold is being hit at the higher duty;
  this is the controller correctly detecting that current is rising
  faster than RPM. Inspect motor wiring, brake state, and connection
  resistance.
- Other corners stay clean but `<name>` always faults -> corner-specific
  problem. Check:
  - **Motor type**, parameter 2. Read it over CAN with `uv run spark params
    --param 2`; it must match the motor fitted (NEO Vortex, NEO 550, brushed).
    Changing it is an RHC2 job over USB-C, because the write is refused here.
  - **Wiring**: a high-resistance connection at the spark or motor
    terminal looks like stall to the controller
  - **Brake/coast** mode setting; brake mode can spike current at
    direction reversals
  - **Hall sensor / encoder cable** if using a sensored mode

### Stall is electrical, not mechanical

The fault means "current high, RPM low." Causes are usually **not**
physical wheel binding (the test is run with wheels jacked, no real load).
They are typically:
- Quick reversal regenerative current
- Mismatched motor type config
- Loose or high-resistance wiring
- Hall/encoder feedback disagreeing with applied phase

To clear the fault after fixing the cause, either power-cycle the spark
or call `spark.clear_faults()` from a small one-off script.

---

## "Wheels visibly misaligned but cancoder math says zero -- and which corner is wrong shifts run-to-run"

### Symptom

Per-corner `wheel_deg` reads ~0 from `cancoder_config_check.py` and `cancoder_audit.py`.
Phoenix Tuner X agrees. But visually, multiple wheels are pointing in the
wrong direction. The misalignments are quantized (typical: 30 deg, 60 deg, 90 deg)
and the **corner that's wrong, the magnitude, and the direction all change
between runs**, even no-spin runs with brake mode holding shafts.

This is the failure mode that means the cancoder is reading correctly but
**something between the cancoder and the wheel housing is rotating without
the cancoder seeing it**.

### How to confirm

Run:
```
uv run python tools/steer_slip_check.py --corner all
```

The tool drives each steer motor at a slow 4% duty and samples the
cancoder at 200 Hz, looking for stair-step patterns indicating slip.
A rigid chain produces a smooth ramp; a slip layer produces stuck-then-jump
patterns.

Result interpretation:
- **All corners SMOOTH** -> no slip detectable. Misalignment is somewhere
  else (calibration reference, visual ambiguity, magnet issue we can't
  detect). Re-do the chassis-tape protocol carefully with multiple
  witness checks.
- **One corner QUANTIZED, rest SMOOTH** -> per-corner mechanical issue on
  that module only. Inspect it.
- **Multiple corners QUANTIZED** -> common-mode slip across all modules.
  Likely a manufacturing or assembly tolerance affecting the entire batch.
  Physical inspection per below.

### Physical inspection protocol (SDS Mk5i specific)

The Mk5i CANcoder magnet is press-fit into the main steering gear (bull
gear). The bull gear is mounted in the wheel housing. The slip layer can
be either:

**(a) Magnet loose in the bull gear bore** -- press-fit compromised.
**(b) Bull gear loose in the wheel housing** -- fastener loose or wear.

Both are invisible to the cancoder (which reads magnet position
regardless of whether the magnet has rotated relative to the gear or the
gear has rotated relative to the housing).

Procedure:

1. **Power off the robot completely.** Disconnect the battery.
2. Remove the steer module's top cover on the worst-offending corner
   (refer to SDS Mk5i assembly guide for fasteners).
3. **Test 1 -- magnet-in-gear slop.** With the bull gear held still
   (clamp it or hold by hand), try to rotate the magnet inside its bore
   using a fingertip or small magnetic tool. Should be impossible. Any
   movement = compromised press-fit.
4. **Test 2 -- gear-on-housing slop.** With the wheel housing held
   still (grip the wheel rim, the housing won't rotate), try to rotate
   the bull gear by hand. Should be impossible. Any movement = bull-gear
   fastener loose, or wear in the bore.
5. **Test 3 -- planetary backlash (last resort).** If 1 and 2 are clean,
   power down the spark, grip the wheel housing firmly, rock it
   back-and-forth lightly. Some backlash is normal, but large discrete
   steps mean a tooth in the steer reduction is damaged or the
   reduction itself has slop.

Repeat on a second corner to confirm whether it's a single-module issue
or batch-wide.

### Resolution

| Test that fails | Fix |
|---|---|
| Test 1 (magnet in gear) | Pull magnet, clean bore, re-seat with thread-locker / epoxy. SDS may have a service note on this. |
| Test 2 (gear on housing) | Tighten fasteners to spec. If they were already tight, the bore or fastener is worn -- contact SDS. |
| Test 3 (planetary backlash) | Open the reduction. Damaged tooth = replace. Severe backlash with no damaged tooth = SDS warranty case. |
| All three pass | Slip isn't in the steer chain. Re-examine calibration with the chassis-tape protocol; verify visual reference is correct. |

### Why no software fix is possible at this layer

The cancoder reads the magnet, which sits inside the bull gear. If the
magnet rotates relative to the gear, or the gear rotates relative to the
housing, the cancoder cannot detect that -- by definition, what it sees
*is* the magnet position. The "wheel direction" is determined by the
housing, which the cancoder cannot read directly.

The motor encoder (in the spark) tracks motor revolutions. With a 26:1
reduction, knowing motor revolutions tells you the bull gear rotation --
but only if the reduction is rigid (no slip in the planetary). And it
still doesn't tell you what the housing did if the bull-gear-to-housing
joint slips.

The only software workarounds would involve cross-checking motor
encoder against cancoder, but they're a half-fix at best -- they detect
*that* slip happened, not *which interface* slipped, and the resulting
position is still ambiguous.

The right fix is mechanical.

---

## "DIVERGE / JUMP / FIGHT lines flood the log during normal operation"

### Symptom
Even though the robot is doing what it should, motor_test.py emits
hundreds of `[RF DIVERGE]` or `[RF JUMP]` lines.

### Root cause: detector thresholds set for stationary wheel, but wheel is moving fast

The per-tick anomaly detector in
[`steer_stress_test.py`](../tools/steer_stress_test.py) reports:
- `JUMP`: cancoder delta > threshold per tick. Default threshold (`5 deg`)
  is fine when stationary but legitimate fast slewing exceeds it.
  Recently made speed-aware (scales with `cmd`).
- `DIVERGE`: `|err|` growing > threshold over time. Fires when target
  changes (legitimate err discontinuity). Recently fixed via
  `tr.set_target(...)` call in `_steer_loop`.

### How to verify
Look at the `cmd=` field in the JUMP line. If `cmd > 0.2`, the motor is
genuinely moving fast and the JUMP is real motion, not a glitch.

### Fix
The threshold scales with commanded speed, so a fast slew is judged against a
wider budget than a stationary wheel. Raise the jump factor if a legitimate slew
still trips it.

---

## "All SPARKs show `applied=0 hb_lock=False active=0x0000`"

### Symptom
Every SparkMax broadcasts STATUS_0 normally (you see voltages and the
`_status0_raw` is populated) but `applied_output=0`, `primary_heartbeat_lock=False`,
and `active_faults=0x0000` -- no faults, no enable, no output. Wheels
don't move regardless of commanded duty.

### Root cause: gs_usb TX wedge under SparkMax STATUS broadcast flood

REVLib defaults broadcast STATUS_0 at 10ms, STATUS_1/2 at 20ms,
STATUS_3 at 50ms, STATUS_4 at 20ms, STATUS_5/6 at 200ms. On an 8-motor
bus that's ~2240 fps RX. Under degraded gs_usb state (accumulated TX URB
exhaustion, USB hiccups), `bus.send()` silently drops frames -- no
exception, no `tx_error_count` increment, no kernel-level drop counter.
Heartbeat at 50Hz can't keep the SPARKs enabled if most heartbeat frames
die between python-can and the wire.

### How to verify
```
# 1. Are heartbeat frames reaching the wire?
candump can0 | grep 02052C80
# Should see ~50 fps. If 0-5 fps, gs_usb is dropping host TX.

# 2. Kernel-level TX health (bypasses python-can):
.venv/bin/python tools/can_tx_health.py can0 --frames 100 --delay 0.02
# Expect sent_packets~100, backlog=0. If sent<<100 + backlog>0, gs_usb wedged.
```

### Detection in-process: count what reaches the wire, not exceptions

`DriveTrain._post_init_tx_health_check()` used to sample `bus.tx_error_count`, which only
moves when `bus.send()` RAISES -- so it was blind to the very failure above, which drops
frames silently, and never fired through any observed wedge. It now compares frames handed
to the socket (`SparkBus.tx_frames`) against frames the kernel completed
(`SparkBus.netdev_tx_packets()`, a read of `/sys/class/net/<iface>/statistics/tx_packets`)
and calls `_recover_can_bus()` once when delivery drops below half. `tx_error_count` is
kept as a second signal, not the only one.

Guarded against false positives, because recovering a healthy bus costs a rebind and a
re-init: too little traffic in the window is not judged (the heartbeat may not have
started), an unreadable counter is not a wedge, and the adapter disappearing BETWEEN the
two samples must not raise out of `DriveTrain.__init__`.

### Fix (already in code)
`sparklib/can_bus.py:_SPARKMAX_STATUS_PERIODS_MS` table + auto-apply in
`init_controller`. Slows STATUS_0 to 50ms and disables unused STATUS_3..6,
reducing bus RX from ~2240 fps to ~400 fps. SparkMax-gated; SparkFlex
left at defaults. Volatile per-power-cycle but re-applied at every init.

### Recovery from a wedged state
USB-replug the gs_usb dongle (kernel rebind alone is sometimes insufficient).
Then start the script; CheckCanID brings the netdev back up and the
throttle commands land on the now-clean bus.

---

## "Cancoder reads broken after Phoenix Tuner X firmware flash"

### Symptom
After field-upgrading CANcoder CRF (via Tuner X on a Windows/Mac machine),
phoenix6 still reports `CAN frame not received/too-stale` or `Firmware Too Old`
for every cancoder. Wheel angles freeze across diagnostic snapshots even
though the flash was confirmed successful in Tuner X.

### Root cause: CANivore netdev returns DOWN after CANivore USB replug

`canivore-fd.service` is a oneshot at boot -- it configures the CANivore
netdev (`canivore`) for 1 Mbit arbitration / 2 Mbit data with `fd on`.
When you move the CANivore between machines (or just replug the USB), the
kernel reattaches the device but the netdev comes back **DOWN and in
classical-CAN mode**, not FD. Phoenix6's userspace USB driver prints
`CANbus Network Up` because it sees the device on USB, but the socketcan
path is broken -- cancoder FD position frames (32-byte payloads) get
silently dropped by the classical-CAN socket reader.

### How to verify
```
ip -d link show canivore
# Look for: state UP, <FD> flag, dbitrate 2000000
# If any are missing, the bus is in the broken state.
```

### Fix (automated, in code)
`CheckCanID.verify_cancoder_bus()` is called from `check_can_interface()`
on every script start. It autodetects the canivore_usb netdev, checks
state UP + `<FD>` + dbitrate=2M, and runs
`sudo systemctl restart canivore-fd.service` if any are missing.

### Manual recovery
```
sudo systemctl restart canivore-fd.service
```

---

## "Wheels overshoot zero and oscillate (zero_test never converges)"

### Symptom
`zero_test.py` shows `max_err` dropping rapidly toward 0 (e.g., 171 deg -> 22 deg in
one tick) and then swinging back through +/-50-60 deg instead of settling.
Wheels physically rotate but never stop at target.

### Root cause: steer P-gain tuned for a different gear ratio

`zero_test.py` originally hardcoded `KP_STEER=0.03` and `MAX_OUTPUT=0.5`,
tuned for rig-flex-2 (NEO Vortex, 26:1 steer gear). On rig-max-2 / rig-max (NEO,
12.8:1 gear), the same gain produces 2x the wheel-side rotation rate per
duty cycle. With no damping in the P-control, the wheel sails through
zero at saturation and the loop oscillates.

### How to verify
Check `_CONFIG.base.swerve.steer_kp` and `swerve.steer_max_output` in the
relevant base config. Should be ~ `0.02 * (steer_gear_ratio / 26.0)` and
`0.5 * (steer_gear_ratio / 26.0)` respectively.

### Fix (already in code)
`zero_test.py` reads `KP_STEER` and `MAX_OUTPUT` from `_CONFIG.base.swerve`
via `getattr`. rig-max-2/02 set `steer_kp: 0.01`, `steer_max_output: 0.25`
(half of rig-flex-2's 0.02 / 0.5). When adding a new base, compute starting
values from gear ratio and tune from there.

### Note
This only affects `zero_test.py`'s cancoder-percent loop. Teleop through
SwervePIDController uses its own steer gains (sparkmax: on-board slot-0
PID in flash; sparkflex: `_FLEX_KP` in swerve_drive.py) which are
independent.

---

## "Base rotates the wrong way (teleop + nav spins to opposite heading, oscillates)"

### Symptom
In joyop teleop, a rotate command turns the base the *opposite* way (left
input rotates right). In autonomous nav the heading controller becomes
positive feedback: the base spins toward the opposite heading and limit-cycles
near the goal instead of converging.

### Root cause: `reverse_speeds` mirrors the rotation sense
`base.swerve.reverse_speeds: true` is required on bases whose right-side drive
motors need inverting so a rotate command actually spins the base (without it
the wheels lock into an X). But inverting only the right-side speeds also
*mirrors the rotation direction*. Both teleop and nav feed the same
`swerveDrive()`, so the mirrored turn shows up everywhere.

### How to verify
Confirm straight-line teleop (forward/strafe) is correct and only rotation is
mirrored. If translation is also wrong, it's a `reverse_speeds` problem, not
this. The sign lives in your config, alongside the other per-rig values.

### Fix
Set `base.swerve.reverse_rotate: true` for that base. It negates the rotate
command once inside `swerveDrive()` (default false, per-base), restoring correct
turn direction for both teleop and nav while keeping `reverse_speeds` intact.

---

## Glossary

Both names below are examples. A netdev is called whatever your udev rule calls
it, and `system/99-spark-can.rules` is the shipped starting point.

- **the SPARK bus** -- a gs_usb / candleLight USB-CAN adapter, cheap, classic
  CAN at 1 Mbit. Whatever `can.interface` names in your config.
- **the CANcoder bus** -- a CTRE CANivore, CAN FD, carrying the absolute
  encoders. Whatever `cancoder.bus` names. phoenix6 addresses it by that same
  name, so the netdev name and the CTRE bus name are one string.
- **STATUS_0** -- first periodic CAN frame from a SparkFlex/SparkMax,
  carries applied output, fault flags, etc. Receipt = "this device is
  alive."
- **deadband** -- error band inside which the controller emits zero
  output. Prevents chatter near target. 1.5 deg in motor_test.py.
- **magnet_offset** -- device-side offset (in cancoder flash) that the
  cancoder adds to raw magnetic angle before publishing
  `absolute_position`. Should be 0 in our setup; software does the
  offset.
- **`*_OFFSET`** -- software-side per-corner offset constant in
  motor_test.py. Captured during calibration. `wheel_deg = abs_deg - OFFSET`.
- **static-settle** -- convergence rule based on "wheel hasn't moved for
  X seconds," as opposed to "|err| < tolerance." Bypasses the deadband
  paradox.

## "gs_usb rebind failed on can0; replug the USB CAN adapter"

### Symptom

The drivetrain refuses to initialise. `ip link` has no `can0`, and the boot-time
chain looks dead:

```
$ systemctl is-active spark-can.service
inactive
$ ls /sys/bus/usb/drivers/gs_usb/          # only new_id / remove_id -- nothing bound
```

The adapter is still visible on USB (`lsusb` shows `1d50:606f`) and `gs_usb` is loaded,
which is what makes it confusing: everything *looks* present.

### Root cause: a stalled USB endpoint, not a bring-up failure

```
gs_usb 3-1.2:1.0 can0: usb xmit fail 5/4/3/2
gs_usb 3-1.2:1.0: Couldn't send data format (err=-32)
gs_usb 3-1.2:1.0: probe with driver gs_usb failed with error -32
```

`-32` is EPIPE -- a halted endpoint. Note `can0` appears in the `usb xmit fail`
lines: the interface *existed* and the adapter wedged **mid-transmit while in use**,
counting a transmit down its retries before the endpoint stalled. A reboot does not clear
it, because VBUS stays energised and the device never re-enumerates.

This is NOT a boot-order problem. It is also separate from the udev bug described below,
which decides whether the interface comes back UP after the replug.

### How to verify

```bash
lsusb | grep 1d50                                   # adapter present?
ls /sys/bus/usb/drivers/gs_usb/                     # anything bound?
sudo dmesg | grep -iE "gs_usb|1d50" | tail -12      # look for err=-32
```

### Fix: power-cycle the adapter

Run `uv run spark canfix` first. It rebinds the gs_usb driver, waits for the netdev
to come back up, and says which of the two faults it is looking at. A stalled
endpoint is the one it cannot clear: unplug the candleLight for about 10 s and
replug. Software recovery fails on a stalled endpoint, and all of these were tested
against the same `err=-32`:

| attempt | result |
|---|---|
| `echo -n 3-1.2:1.0 > /sys/bus/usb/drivers/gs_usb/bind` | accepted, nothing bound |
| `authorized` 0 then 1 (software replug) | no re-enumeration -- and it can drop the device off the bus entirely |
| `usbreset 1d50:606f` | "No such device" once the device has gone |
| reboot | VBUS persists; device stays wedged |

**Do not deauthorize a device that is already stalled.** In one instance that took it from
"wedged but enumerated" to absent from `lsusb` entirely, which then needs the physical
replug anyway.

A second CAN adapter on the same hub (the CTRE CANivore, `29ca:4481`, iface `canivore`)
staying healthy throughout is good evidence the hub, its power and the USB path are fine --
the fault is confined to the one adapter.

### After the replug: the interface comes back DOWN

The udev rename fires correctly (the iface is `can0`, not `can0`) but
`spark-can.service` stays `inactive`, leaving the interface `DOWN` with no bitrate. The
service is `static` -- it has no `[Install]` section and is pulled in only by
`ENV{SYSTEMD_WANTS}` from `99-spark-can.rules`, so losing that tag means it never runs.

**Root cause: the `NAME=` rename fires a second uevent that the tag rule did not match.**
Renaming a netdev makes the kernel emit a `move` uevent after the `add`. Every rule in
`99-spark-can.rules` used to be `ACTION=="add"`-gated, so the `move` pass rewrote the udev
database *without* `SYSTEMD_WANTS`, and systemd was left with an empty `Wants=` on the
device unit. It was never a match-key mismatch: the `NAME=` rule and the tag rule have
identical match keys.

Diagnose it with an explicit action -- a bare `udevadm test <path>` defaults to
`--action=add`, prints `SYSTEMD_WANTS`, and hides the bug:

```bash
udevadm test --action=add  /sys/class/net/can0 2>&1 | grep SYSTEMD_WANTS
udevadm test --action=move /sys/class/net/can0 2>&1 | grep SYSTEMD_WANTS
udevadm info /sys/class/net/can0 | grep -E "TAGS|SYSTEMD"
systemctl show sys-subsystem-net-devices-can0.device -p Wants
```

The `add` line printing while the `move` line, `udevadm info` and `Wants=` are all empty is
the signature. Fixed by matching `ACTION=="add|move"` on the tag rule; the `NAME=` rules stay
`add`-only because udev ignores `NAME=` on a `move` event by design.

Manual recovery, and the check for a fault in the unit or `spark-can-bringup` instead:

```bash
sudo systemctl start spark-can.service && ip -br link show can0
journalctl -u spark-can.service -n 20 --no-pager
```

### Prevention

The adapter wedges **during operation**, not at boot -- most likely when a process holding
the bus dies mid-transfer. Preventing that teardown is a better fix than recovering from
it; recovery requires physical access, which a robot may not have.


## "A motor lost its configuration" -- find which one, without touching it

### Symptom

Something behaves differently after a power cycle -- a drive wheel coasts instead
of braking, a corner feels wrong -- but every CAN ID is present and nothing errors.

### Root cause

A controller has reverted some parameters to REV factory defaults, usually
because the post-firmware-update factory reset was skipped (see
[CAN-SETUP.md](CAN-SETUP.md) section 6). It stays on its CAN ID, so an ID scan looks
perfectly healthy.

### How to verify

```bash
uv run spark audit
```

The audit reads all nine declared deviations off each controller as parameters and
compares them against the `sparkflex:` block of `spark.yaml`. Firmware 26.1.6 answers a
parameter read when the request is a remote frame carrying dlc 8. That was measured on
rig-flex's eight controllers, where a zero-length data frame drew no answer. Status 1
Period is also timed off the wire, which gives a second and passive check. Appendix A
of the assembly manual sets parameter 159 to **20 ms**, against REV's factory default
of **250 ms**. A controller broadcasting STATUS_1 at 250 ms has reverted, which the
audit reports as its cadence verdict. Both checks stay read-only and run over CAN, so
a laptop on the bus is enough.

Found on rig-flex this way:

```
dev 10,11,13,14,15,16,17   STATUS_1 @  20 ms   = Appendix A
dev 12  drive/RF 6B029ADD  STATUS_1 @ 250 ms   = FACTORY DEFAULT
```

### Fix

`uv run spark repair --id 12 --persist` restores that parameter and commits it.
But treat the canary as a *flag, not the whole fault*: if Status 1 Period
reverted, the other Appendix A deviations very likely reverted too -- Idle Mode
(BRAKE->COAST), Closed Loop Control Sensor (MAIN_ENCODER->NONE), P0. Those cannot
be read back on this firmware, so re-provision the controller through RHC2 rather
than assuming one write fixed it.

the `sparkflex:` block of `spark.yaml` lists the nine settings that
deviate from factory default, with their parameter IDs. Its `meta` block records
`deviating_from_factory_default: 9`, and `tests/unit/test_param_enums.py` counts
the file's own entries so the number cannot drift again.

---

## SPARK Flex faults and telemetry decode to nonsense

### Symptom

`sticky_faults` reports non-zero on a controller with zero applied output, or
`bus_voltage` / `output_current` look wrong.

### Root cause

`sparklib/controller.py` decodes faults from STATUS_0 bytes 2:6. On firmware 25.x+
those bytes are **bus voltage, output current and motor temperature** -- faults
moved to STATUS_1 entirely. This is why `_FAULT_DECODE_TRUSTED` is False for
SparkFlex.

Real STATUS_0 layout (REV-Specs 2.1.0, mirrored in the product blocks of `spark.yaml`):

| field | bits | scale |
|---|---|---|
| applied output | 0-15 | int16 x 3.082369457075716e-05 |
| bus voltage | 16-27 | uint12 x 0.0073260073260073 V |
| output current | 28-39 | uint12 x 0.0366300366300366 A |
| motor temperature | 40-47 | uint8, degrees C |
| limit / inverted / heartbeat-lock flags | 48-53 | |
| SPARK model | 54-57 | 1 = SPARK Flex |

Faults and warnings live in **STATUS_1 (API 0x2E1)**: active faults byte 0,
warnings byte 2, sticky faults byte 3, sticky warnings byte 5.

### Fix

Use `uv run spark faults`, which decodes against the published spec.

Note STATUS_1 is `enabledByDefault` but a controller can have it disabled -- such
a controller reports **no faults at all**, which reads as healthy. `spark audit`
flags that case explicitly.

---

## A limit switch is asserted

`spark faults` shows `LIMIT HARD-FWD` or `LIMIT HARD-REV` with status
`interlock`. That is a data-port limit switch, an **e-stop-equivalent safety
interlock** -- a state to respect, not a fault to clear. Parameters 50-53 are
write-protected in `SparkAdmin.write_param`, so no tool here can disable them.

---

## Battery reads a plausible but wrong percentage

### Symptom

`uv run battery` returns a number that disagrees with the Anker app.

### Root cause

`ANKER_DEVICE_SN` set in `~/.config/sparklib/anker.env` or the
environment. Those are account-wide and shared between machines, so one stale
serial points every robot that reads it at the same battery. On rig-flex this
reported another unit's 9% while its own pack was at 100%.

### Fix

The serial is per-robot hardware identity and belongs in the config `battery:`
block, which now **outranks** the environment -- `_load_credentials()` warns when
they disagree. Remove `ANKER_DEVICE_SN` from `anker.env`; leave credentials there.

Separately, a device whose model has no MQTT field map decodes to nothing and
fails with "no state of charge reported by the device". The A1765 (C1000X Gen 2)
was unmapped until `anker-solix-api` **v3.8.1**; the pin was bumped accordingly.

### Note on rail voltage vs pack charge

`uv run spark voltage` reports the motor-rail voltage measured at each SPARK,
which is a different quantity from the Anker pack's state of charge. A controller
can brown out while the pack reads healthy. For brownout diagnosis the rail
reading and the sticky brownout/has-reset warnings are the better evidence.

---

## "A CAN ID sweep shows devices that are not there"

### Symptom

A passive ID sweep reports live devices at IDs nothing is configured at, and the
numbers move between runs. On rig-flex-2 they appeared at 7-10, then at 8-11, then at
7, 8, 12 and 13. They are present only while teleop or another drive process is
running, and they go away when it stops. `spark status` shows a matching phantom:
a ninth controller at ID 0 with no serial, no firmware and no STATUS_1.

The bus itself is healthy. `uv run spark duplicates` comes back clean with the
drive stack up and with it stopped, because it keys on distinct UNIQUE_ID
payloads answering one ID and never sees these frames at all.

### Root cause: frames that carry no device ID, decoded as though they did

FRC addressing puts the device ID in the low 6 bits of a 29-bit arbitration ID.
The sweep applied that mask to every frame it received, so three classes of frame
each became a device that does not exist.

**socketcan error frames.** python-can enables them by default and masks a
non-extended frame to `can_id & 0x7FF`, so the error class bits from
`linux/can/error.h` land in the same 6 bits as a device number:

```
TX_TIMEOUT 0x001 -> device  1     PROT 0x008 -> device  8
LOSTARB    0x002 -> device  2     TRX  0x010 -> device 16
CRTL       0x004 -> device  4     ACK  0x020 -> device 32
```

All of them read with manufacturer `0x00`, which no real device can produce, so
that column is the tell.

**Frames this host transmitted.** socketcan local loopback delivers what other
processes put on the bus, so a sweep sees the drive stack's own traffic. The
SPARK heartbeat is the clearest case. `SparkBus._heartbeat_runnable` sends
arbitration ID `0x02052C80` every 20 ms, which decodes to device type 2,
manufacturer REV, API `0x0B2` and **device 0**. That one frame is the entire ID 0
row in `spark status`. REV's factory default ID is also 0, so it reads as a
controller that lost its flashed identity when nothing has.

**Phoenix diagnostic server sessions.** This is where the moving block comes from,
and it gets its own section below.

### Why the ghost IDs move: the Phoenix diagnostic server

phoenix6 starts a diagnostics server so Phoenix Tuner X can reach the bus. It
broadcasts UDS ReadEcuIdentification to device 63, the all-devices address, and
then holds a TesterPresent session with every responder over ISO-TP transport.
Measured on rig-flex-2 `can0` across APIs `0x000A`, `0x3E4` and `0x3E5`:

```
TX  api 0x000A  dev 63   02 1a 02 00              ReadEcuIdentification, broadcast
TX  api 0x03E4  dev 63   02 3e 00 aa aa aa aa aa  TesterPresent, broadcast
TX  api 0x03E4  dev  7   30 00 05 aa aa aa aa aa  ISO-TP flow control, CTS
RX  api 0x03E5  dev  7   06 7e 05 04 fc 56 07 aa  single frame, 6 bytes
RX  api 0x03E5  dev  7   10 62 f0 1a 01 00 06 e5  first frame, 98 bytes follow
RX  api 0x03E5  dev  7   21 00 00 f5 00 00 03 00  consecutive frame
```

Those channel numbers are allocated per session, so they land somewhere different
each time the server starts. Four CANcoders answer the broadcast, which is why
four consecutive channels show up and look like four devices. Only four CANcoders
and one CANivore are physically connected to that bus.

The replies are convincing on their own, which is the trap. Each channel opens
its own ISO-TP session and the single frame carries `05 04` (device type 5,
manufacturer CTRE) followed by the channel number, so it reads exactly like a
device announcing its own ID.

### How to verify

Run the sweep. Since the guard landed it prints what it refused, so error frames
and host traffic show as a tally instead of as devices:

```bash
uv run python tools/can_id_sweep.py --channels can0 --duration 3
```

To see the diagnostic traffic itself, census the bus by frame class. `is_rx` is
False for anything this host transmitted, and it is populated by the socketcan
backend in both pinned python-can versions, 4.5.0 and 4.6.1:

```python
import collections, time, can
bus = can.Bus(interface="socketcan", channel="can0", fd=True)
seen, end = collections.Counter(), time.time() + 3.0
while time.time() < end:
    m = bus.recv(timeout=max(0.0, end - time.time()))
    if m is None:
        continue
    a = m.arbitration_id
    seen[("RX" if m.is_rx else "TX", m.is_error_frame,
          (a >> 24) & 0x1F, (a >> 16) & 0xFF, (a >> 6) & 0x3FF, a & 0x3F)] += 1
bus.shutdown()
for k, n in seen.most_common():
    print(k, n)
```

A healthy `can0` with the server off shows only the four CANcoders: API `0x00AF`
at 100 Hz plus `0x01AF` and `0x011D` at 4 Hz, all `RX`, nothing else.

### Fix

Two independent parts, and both are wanted.

**The sweep validates frames before decoding them.** `_not_a_device()` in
`can_id_sweep.py` rejects error frames, standard 11-bit frames and anything with
`is_rx` False, and `main()` prints the tally by reason. Turning socketcan local
loopback off does *not* substitute for this, because error frames arrive through
`CAN_RAW_ERR_FILTER` and are unaffected by the loopback setting.

**The diagnostic server stays off.** `set_phoenix_diagnostics_start_time()` takes
a negative value to shut the server down or keep it from starting, per the
docstring in `phoenix6/unmanaged.py`. It has to run before any phoenix6 device is
constructed, so `sparklib/__init__.py` beside the `CTR_TARGET` line is the
place:

```python
from phoenix6 import unmanaged
unmanaged.set_phoenix_diagnostics_start_time(-1)
```

Measured saving on rig-flex-2 `can0`: 534 frames per 3 s, about 178 frames per second
and 29 percent of the frame count on that bus. Every CANcoder stream is untouched
at 300 / 12 / 12 frames per device per 3 s. That is frame count and not bus
utilisation in bits, and the CANcoder frames are 32-byte FD against the
diagnostic server's 8-byte frames, so the saving in bits is smaller.

### What the diagnostic server was giving us

Nothing that any code reads. `c_Phoenix_Diagnostics_SetSecondsToStart` is the only
diagnostics symbol in the whole phoenix6 Python binding, and it is a write-only
switch with no getter. The server exists to serve Phoenix Tuner X over the
network.

Config reads and writes go down a different path and keep working with the server
off. `parent_configurator.py` calls `c_ctre_phoenix6_get_configs` and
`c_ctre_phoenix6_set_configs`, which is the device API over CAN. So
`configurator.refresh()` and `.apply()`, every status signal, `magnet_offset`,
`sensor_direction` and the sticky-fault clears are all unaffected.

Tuner X itself is still used on this base, always by hand and always on a stopped
robot: cross-checking a CANcoder angle (`cancoder_audit.py`), flipping
`sensor_direction` (`cancoder_sensor_direction.py`), field-upgrading CANcoder
firmware, and setting device IDs or applying a Pro license. If you want the
server back for one of those, gate the call on an environment variable in the
style of `base.skip_gs_usb_rebind` and set it in the robot's config.

### Still open

`SparkAdmin.inventory()` in `sparklib/admin.py` filters on manufacturer alone, so
`spark status` still reports the ID 0 heartbeat row whenever a drive process is
running. It needs the same treatment, keyed on `is_rx` or on an API allowlist,
because the heartbeat is genuinely device type 2 and manufacturer REV.
