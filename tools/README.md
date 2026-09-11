# tools/

Diagnostics that touch real hardware. They live here and not in `tests/` because
the suite must be safe to run anywhere, and several of these move motors.

Run them with the repository root on the path:

    uv run python tools/spark_passive_snapshot.py

## What is safe to run

Almost everything here listens or reads. These are the ones that do not:

| tool | what it does to the hardware |
| --- | --- |
| `spark_heartbeat_config_probe.py` | ENABLES controllers at `percent_output(0.0)`, so they are live at zero |
| `spark_set_can_id.py` | rewrites a controller's CAN id and burns it to flash |
| `spin_all_motors.py` | TURNS EVERY CONFIGURED MOTOR. Put the base on blocks |
| `steer_stress_test.py` | TURNS STEER MOTORS, and drive motors when asked |
| `steer_decode_check.py` | enables one steer controller, commanding zero |
| `steer_pid_check.py` | DRIVES ONE STEER MOTOR to a commanded angle |
| `steer_slip_check.py` | DRIVES ONE STEER MOTOR |
| `steer_tune.py` | DRIVES ONE STEER MOTOR, repeatedly |
| `cancoder_reset_flash.py` | rewrites a CANcoder's magnet offset and burns it to flash |
| `cancoder_clear_faults.py` | clears sticky faults on a CANcoder |

Everything else either only receives, or sends read and configuration frames
that command no motion. `spark_param_write.py` and
`spark_limit_polarity_repair.py` write parameters, which changes behaviour on
the next enable without moving anything now.

## The bus

Listening only, never transmits:

- `spark_passive_snapshot.py` -- read-only snapshot of the bus
- `spark_teleop_watch.py` -- what a drive stack commands and what the controllers do about it
- `spark_pdp_correlate.py` -- does the power-distribution panel report anything useful? Correlates every candidate field in a foreign device's frames against the rail voltage and total current the SPARKs report. Needs the robot MOVING to mean anything, and self-checks that it was
- `watch_for_device.py` -- reports when a device id appears or disappears
- `can_usb_watch.py` -- does the USB CAN adapter survive a session? Samples link state, transmit backlog, error counters and frame rates while the robot runs, survives the adapter vanishing, logs to a file, and watches a second interface as a control so a failing adapter is told apart from a failing hub. Built for an intermittent fault, so leave it running: `--seconds 0`

Sends read or probe frames:

- `spark_param_sweep.py` -- read every parameter off a live bus, writes nothing
- `spark_param_probe.py` -- does a SPARK answer a parameter or firmware read over CAN?
- `spark_read_frame_form.py` -- which FORM of a parameter-read request does this firmware answer? Settled that a read needs a remote frame with dlc 8
- `spark_setperiod_probe.py` -- which arbitration base does the SPARK Flex set-period frame use?
- `spark_silent_bus_probe.py` -- what ends the post-cycle silence on a bus, and can the evidence survive it?
- `spark_fault_bytes.py` -- which STATUS_0 bytes carry SPARK Flex faults, empirically
- `spark_sticky_survival.py` -- do SPARK Flex sticky faults survive a power cycle?
- `spark_blink.py` -- blink a controller's LED, in the form the firmware wants
- `spark_param_write_log.py` -- log parameter writes and interlock transitions as they happen

Writes:

- `spark_param_write.py` -- write a parameter and verify the device accepted it
- `spark_limit_polarity_repair.py` -- restore REV's factory limit-switch polarities, and prove it
- `spark_set_can_id.py` -- reassign a CAN id by hardware serial rather than current id

Enables:

- `spark_heartbeat_config_probe.py` -- do config frames need an active enable heartbeat?

## The adapter

- `can_diag.py` -- system-level CAN diagnostics, no sudo, sends nothing
- `can_tx_health.py` -- can a SocketCAN interface drain transmitted frames?

## Finding what is on the bus

- `can_id_sweep.py` -- which device ids are answering on each interface, and how that compares against the config
- `can_frame_census.py` -- every arbitration id seen on one channel, counted and decoded by device and api

## Setting up a swerve module

All of these need the swerve extra. From this checkout that is
`uv sync --extra swerve`; a project depending on the package uses
`uv add "sparklib-py[swerve]"`.

Read-only, no motion:

- `cancoder_calibrate.py` -- record each corner's wheel-zero offset, after you have aligned the wheels
- `cancoder_audit.py` -- sensor direction, magnet health and recorded offsets, per corner
- `cancoder_config_check.py` -- per-corner verdict: stationary jitter, the offset stored in flash, magnet health, sticky faults
- `cancoder_live.py` -- live angle readout while you turn a wheel by hand, which is what physical alignment needs
- `cancoder_sensor_direction.py` -- turn each wheel by hand and confirm every encoder counts the same way its wheel does

Writes to a CANcoder:

- `cancoder_reset_flash.py` -- WRITES FLASH. Resets `magnet_offset` and clears sticky faults. Run it when `cancoder_config_check.py` reports an offset nobody meant to set
- `cancoder_clear_faults.py` -- retries a sticky-fault clear and falls back to per-fault clears, for bits `cancoder_reset_flash.py` leaves set

DRIVES ONE STEER MOTOR, on a stand:

- `steer_slip_check.py` -- is the chain slipping, or do the gains need work? Run this before tuning
- `steer_tune.py` -- sweep a gain grid on one corner and recommend the set that converged best
- `steer_decode_check.py` -- does the SPARK's own encoder agree with the CANcoder about how far the axis turned?
- `steer_pid_check.py` -- drive one corner to a target angle and grade the approach, on the device loop or on the host

Run the first two in that order. A slipping corner produces a grid where nothing
converges, and the obvious conclusion from that is the wrong one.

DRIVES EVERY MOTOR, on blocks:

- `spin_all_motors.py` -- turn each configured motor briefly, together or one at a time, to confirm every one answers
- `steer_stress_test.py` -- repeatability and stress across corners, logging every run to CSV for comparison

## Not a hardware tool

- `gen_api_reference.py` -- regenerates `docs/API-REFERENCE.md` from the docstrings. Reads source, touches no bus.

## Call sites are guarded, behaviour mostly is not

These tools import `sparklib.admin`, so a signature change can break one without
the suite noticing. `tests/unit/test_spark_callsites.py` walks every file here
and checks each keyword against the real signature.
`tests/unit/test_pdp_correlate.py` goes further for `spark_pdp_correlate.py`,
running its analysis over synthetic frames, because a mistyped decoder key is
silent: it returns None and the tool prints an empty result that reads like a
genuine negative finding.
