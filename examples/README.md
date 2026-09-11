# examples/

Six runnable files, smallest first. Each one is written to be read and then
lifted into your own code, so none of them hides anything behind a helper.

| file | what it shows |
| --- | --- |
| `01_spin_one_motor.py` | the whole minimum: a bus, a controller, an enable heartbeat, a setpoint |
| `02_drive_and_steer.py` | two motors with different jobs, closed loop host-side on the steer |
| `03_startup_gate.py` | the check to run before the first setpoint, and why |
| `04_swerve_module.py` | drive plus steer as one unit, with the gate wired in |
| `05_seed_and_steer.py` | seeding from a CANcoder, then holding an angle on the device |
| `06_joystick_drive.py` | a whole chassis from a stick: kinematics, seeding, and the shutdown contract |

Run them from a checkout with a bus already up:

    uv run python examples/01_spin_one_motor.py --interface can0 --id 1

The first four take `--interface` and read nothing from `spark.yaml`, so they
work before you have written a config. `05` and `06` read the config, because
the ids, offsets, gear ratio and chassis geometry belong in one place, and both
need the swerve extra: `uv add "sparklib-py[swerve]"`.

`06 --dry-run` opens no bus and moves nothing. It prints the module states for
whatever the stick is doing, which makes the math in
[../docs/INTEGRATION.md](../docs/INTEGRATION.md) readable before a wheel turns.

**They move motors.** Unbolt the motor or lift the wheels off the ground first.
Each file prints what it is about to do and waits for you to confirm.
