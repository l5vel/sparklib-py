# sparklib-py

Drive, configure, diagnose and repair REV SPARK MAX and SPARK Flex motor
controllers from Python, over CAN, on any Linux machine.

A lightweight Python-only path to the same hardware the FRC stack drives, with
the failure catalogue and the repair tooling that the hardware actually needs.

```python
from sparklib import SparkBus, SPARK_FLEX

bus = SparkBus(channel="can0")
motor = bus.init_controller(1, SPARK_FLEX)
motor.percent_output(0.1)
```

```console
$ uv run spark status
id  role        firmware   serial     status
10  drive/RB    26.1.6     C7B24C10   ok
11  steer/RB    26.1.6     AB15F6C7   ok
...
$ uv run spark audit
no problems found across 8 controller(s)
```

Runs on any Linux box with a USB CAN adapter.

## What you get

**Failures you can reproduce on a desk.** This is the part that earns its keep. A
frame-level bus simulator injects 42 of the 44 catalogued failure modes, so the
code that diagnoses a wedged bus is exercised by wedged buses on every run rather
than by the happy path. A controller that reverted to factory defaults, two
controllers answering one id, a rail cycle that silenced all eight, a saturated
frame that decodes as 30 V and four closed limits: each is a fixture, and each
has a test asserting what the driver says about it.

That matters because these failures are hard to stage on purpose and expensive to
meet for the first time on a robot. You can run the whole suite on a laptop in a
coffee shop and know the diagnosis path still works.

**Repair that puts it back.** `spark audit` scores the live bus against what you
declared it should hold and names every controller that has drifted.
`spark provision --id N --write` writes the declared values back over CAN.
`spark set-id --serial X --to N` recovers a controller whose CAN id reverted,
addressed by hardware serial because its id is the thing that went missing.

**Answers in seconds.** `spark status` names every controller with its firmware
and serial. `spark faults` decodes the fault and warning words and says what to
do about each. Both run from a laptop, in the pit or at a bench.

**The frames in the open.** Every decoder is a plain function over eight bytes,
written against REV's published specification and tested against it. So a failure
that presents as "it just stopped working" becomes something you can point at.

**The reasoning, written down.** Nineteen sections of field guide, a
source-by-source protocol reading, and a catalogue of 44 failure modes with the
bus signature of each. Every claim carries the evidence that settles it.

## Why a SPARK library ships CANcoder code

Most of what you want needs no encoder at all. Spinning motors, reading the bus,
decoding faults, auditing a configuration, repairing drift, tuning a steer axis
against its own encoder: all of that is SPARK work and none of it is behind the
extra.

The encoder matters when you are replacing a whole swerve stack rather than
sitting beside one, because it is what closes the loop on the thing you actually
care about. A SPARK counts motor revolutions from wherever it powered on, so on a
steer axis it knows how far the axis turned and not which way the wheel points.
Send it 6.5 rotations and it moves 6.5 rotations from an arbitrary place. That is
a closed loop on motor position and an open one on wheel angle. Supplying the
absolute reference once turns the second into a closed loop too.

The interesting part is that the reference arrives through a SPARK operation.
`set_encoder_position` writes the controller's own encoder to the true angle, and
after that its position loop is finally talking about the wheel. The absolute
encoder supplies the number; the SPARK owns the step that uses it.

Splitting those two across separate libraries leaves the step that completes a
swerve module belonging to neither. So it is here, behind an extra, because a
bench rig driving one motor has no use for it:

```console
uv add "sparklib-py[swerve]"      # from a project that depends on this
uv sync --extra swerve            # from a checkout of this repository
```

That installs phoenix6 and enables `sparklib.cancoder`, nine setup and repair
tools, and the two swerve examples. `swerve-25` and `swerve-26` pin the phoenix6
major instead, for a rig whose CANcoder firmware needs a particular one, and
[docs/SWERVE-SETUP.md](docs/SWERVE-SETUP.md) covers which to pick. Everything
else works without any of it, and the core never imports phoenix6.

The chassis math is separate again. `sparklib.kinematics` turns a driver command
into a speed and an angle per wheel, using nothing outside the standard library,
so it runs on a laptop with no bus and no extra installed.

[docs/STEER-CONTROL.md](docs/STEER-CONTROL.md) covers on-device against
host-side loops, the gear-ratio conversion, and why the absolute encoder is a
seed rather than the feedback path.
[docs/INTEGRATION.md](docs/INTEGRATION.md) covers the path from a joystick, a
ROS node or a planner down to four wheels turning.

## Waking a silent bus, and what a power cycle takes with it

**A silent bus is usually asleep, not dead.** After a motor-rail power cycle,
every controller can stop broadcasting while the adapter still works and
`cansend` still completes. One Clear Faults frame per id brings all of them back.
That is what `spark clear` sends, and it is the first thing to try when
`spark status` finds nothing on a bus you know is wired.

    uv run spark clear

**Read the sticky bits before you clear them.** Sticky faults and warnings are
history: they say a thing happened, not that it is happening. `spark clear`
erases the only record. Run `spark faults` first, every time.

**On firmware below 25, configuration lives in RAM.** The status-frame periods
this driver writes do not survive a power cycle, because the pre-25 burn commits
the parameter table and not the periods. Nothing persists them, so re-sending
after every power event is mandatory rather than optional:

    uv run spark throttle

**A controller that loses its CAN id reverts to 0.** Then no id-addressed command
can reach it, and if two controllers do it you have a duplicate that nothing can
tell apart. Record the serials once, while the bus is healthy, and the recovery
path stays open:

    uv run spark learn-serials --write

## Integrating this upstream: gate before you enable

If you are putting sparklib underneath something that drives motors, the single
most valuable thing you can add is a gate that runs after the bus is up and
before the first setpoint.

**"Is it talking" and "will it move" are different questions**, and the second
one is the one that goes unasked. On one of our rigs, three of eight controllers
accepted 1579 setpoint frames each, applied exactly 0.000 and drew 0.00 A,
because both data-port hard limits read as REACHED. The base drove on five motors
for an unknown number of sessions and nothing in the stack noticed. Every
controller was broadcasting the whole time, so any liveness check passed.

A gate wants to refuse to start when a controller reports:

| condition | why it blocks |
| --- | --- |
| a hard forward or reverse limit reached | a real interlock, and the motor will apply nothing |
| locked to another heartbeat source | something else owns it |
| an active fault | it will not drive until the fault clears |
| STATUS_0 that is not a measurement | a saturated frame sets every limit bit at once |

The last one is the subtle one. A transmitter that stops driving the bus leaves
it recessive, so the payload reads all-ones and every field saturates together:
30 V, 150 A, 255 C, and four closed limits. Treating that as four interlocks
stops the robot forever on a reading that was never a reading.

`reading_from_raw` gives you all four from the frames you already buffer, decoded
for the generation the frames actually are:

```python
from sparklib import admin as sa

gen = sa.GEN_FW25 if controller_is_modern else sa.GEN_PRE25
r = sa.reading_from_raw(status0_bytes, status1_bytes, gen)

if r["implausible"]:
    refuse(sa.describe_implausible(r["implausible"]))
elif r["hard_forward_limit"] or r["hard_reverse_limit"]:
    refuse("a hard limit is reached")
elif r["primary_heartbeat_lock"]:
    refuse("another heartbeat source owns this controller")
for bit in r["faults"]:
    refuse(sa.remedy_for([bit], gen))       # the remedy is generation-correct
```

It adds no bus traffic and takes no time, because it reads frames that are
already arriving. Two things to get right: decode for the firmware generation
rather than the product, and take the remedy text from `remedy_for` with the
generation passed in, so a SPARK MAX operator is not handed advice that only
works on firmware 25.

## Why Python, and why this

FRC teams write Java or C++ on a roboRIO, and that stack keeps doing its job.
This sits beside it and answers a different question: what is on the bus right
now, and does it hold what you think it holds.

Underneath the vendor library a SPARK is an ordinary CAN device, and Linux has
spoken CAN natively since 2008. So everything you do to a controller that is not
driving it comes down to three Python packages and a cheap dongle. Reading the
bus, checking a configuration, decoding a fault, reassigning an id and restoring
a setting that drifted all work that way.

That shortens the loop. One command replaces a build, a deploy and an enable. It
also runs where the robot is not, so a swerve module on a cart, a gearbox test
stand or a research base off a mini PC all get the same tooling.

## How it compares

| | REVLib and WPILib | sparklib |
| --- | --- | --- |
| language | Java or C++ | Python |
| host | roboRIO | any Linux box with a USB CAN adapter |
| transport | roboRIO CAN, or CANBridge over USB | SocketCAN |
| to try a change | build, deploy, enable | run it |
| frame handling | inside the library | open, documented, tested frame by frame |
| configuration | code, or REV Hardware Client over USB-C | one YAML file, applied over CAN |
| drift detection | REV Hardware Client, by eye | `spark audit`, against a declared config and a recorded baseline |
| failure injection | -- | 42 failure modes, simulated, in CI |
| firmware split | handled internally | keyed on firmware version, and written down |
| closed loop on device | full, including MAXMotion | REVLib's job; sparklib watches the controller that runs it |
| simulation | WPILib sim | a frame-level bus simulator, no hardware |

Read the last row of that table as the division of labour. REVLib runs the
controller, so on-device closed loop, MAXMotion profiling and the encoder
configuration objects belong there. Configure a position loop with REV Hardware
Client or REVLib, then let sparklib administer and watch the controller running
it. Firmware updates and factory resets stay on USB-C with REV Hardware Client
for the same reason.

The two are worth having together, because REVLib does more and sparklib shows
more.

### Using both on one robot

Most of what `spark` does is listen, and every command is documented to send no
setpoint and start no enable heartbeat. Splice a USB adapter into the CAN chain
as another node and `spark status`, `spark faults` and `spark audit` all work
against the same bus the roboRIO is on.

Save the writes for a disabled robot. `spark provision`, `spark set-id` and
`spark persist` change what a controller holds, and changing that underneath
running robot code produces a fault nobody can reproduce.

## Install

```console
uv add sparklib-py

# on a candleLight or gs_usb adapter, which wants python-can pinned:
uv add "sparklib-py[gsusb]"
```

Working on the library itself:

```console
git clone https://github.com/l5vel/sparklib-py
cd sparklib-py
uv sync
uv run spark status
```

You also need a CAN bus that exists. On a fresh Linux machine that means kernel
modules, `can-utils`, a udev rule and a sudoers grant.
**[docs/CAN-SETUP.md](docs/CAN-SETUP.md) is the whole path**, from `lsusb` to
`candump` scrolling, and it is where to start when `spark status` finds nothing.

The short version, on Ubuntu with a candleLight adapter:

```console
sudo apt install can-utils iproute2 usbutils
sudo modprobe gs_usb
sudo ip link set can0 up type can bitrate 1000000
candump can0            # scrolls once the controllers have power
```

Every SPARK runs 1 Mbit classic CAN. A bus configured for FD or 500k enumerates
cleanly and receives nothing, so match the bitrate first.

## Configure

One file. Copy the shipped example, which is a working configuration off a real
eight-motor bus:

```console
cp "$(uv run python -c 'import sparklib.config as c; print(c.SHIPPED_CONFIG)')" ./spark.yaml
export SPARKLIB_CONFIG=$PWD/spark.yaml
```

Set the product and the interface, then the device ids:

```yaml
controller_type: sparkflex      # or sparkmax

can:
  interface: can0               # whatever your adapter came up as

devices:
  drive: {LF: 14, RF: 12, LB: 16, RB: 10}
  steer: {LF: 15, RF: 13, LB: 17, RB: 11}
```

Role and position names are yours. One motor on a bench is a valid config:

```yaml
devices:
  main: {motor: 1}
```

The declared settings for both SPARK products live in the same file, under
`sparkflex:` and `sparkmax:`, and `controller_type` picks one. The two tables sit
side by side so you can read the difference, and switching products is a one-line
edit.

Configuration is per file and never per hostname. Running several rigs off one
checkout means one file each, selected by pointing `$SPARKLIB_CONFIG` at the
right one from your deployment.

## The `spark` command

Every subcommand reads unless it says otherwise, and none of them sends a
setpoint or starts the enable heartbeat. A controller stays disabled throughout.

| Command | What it does |
| --- | --- |
| `spark status` | what is on the bus, with serials and firmware |
| `spark clear` | clear latched faults, which is what wakes a silent bus |
| `spark faults` | decoded faults, warnings and telemetry per controller |
| `spark voltage` | motor-rail voltage per controller, graded against the battery chemistry |
| `spark audit` | configuration drift and duplicate ids; exit 1 on any fault |
| `spark duplicates` | ids answered by more than one controller |
| `spark params --id 12 --all` | the whole parameter table off one controller |
| `spark identify --serial 498B2579` | blink one controller's LED |
| `spark set-id --serial X --to 17` | reassign a CAN id, addressed by serial |
| `spark learn-serials --write` | record serials into the config, once |
| `spark repair --id 12` | restore one declared setting that drifted |
| `spark provision --id 12 --write` | restore every declared setting that drifted |
| `spark snapshot --write` | record this bus as the baseline to compare against |
| `spark throttle` | re-send the pre-25 status periods a power cycle drops |
| `spark canfix` | clear a wedged USB CAN adapter without replugging it |
| `spark verify` | every claim the driver rests on, and which ones are still open |

Run `spark audit` first when something misbehaves. Then
`spark provision --id N --write` puts back whatever it says has drifted.

## Taking pieces into your own project

The layers separate cleanly, so take the one you need.

**The decoders.** `sparklib/admin.py` holds them as free functions:
`decode_status_0`, `decode_status_1`, their pre-25 counterparts, and
`normalised_reading`, which hides the generation split behind one shape. They
take `bytes` and return a dict, with no bus and no state, so reading them is
enough to port them to another language.

**The protocol.** [docs/PROTOCOL.md](docs/PROTOCOL.md) is written to be
implemented from: arbitration id construction, which api class carries what on
which firmware, both parameter dialects, and what answers an RTR. Every claim
cites REV's own specification, and each measured claim names the bus that
measured it.

**A different transport.** `SparkBus` takes a python-can channel, so SocketCAN,
slcan, PCAN, Vector and a virtual bus all work. The frame layer stays the same
underneath any of them.

**A different config system.** `sparklib.config.set_config(namespace, path=...)`
installs a configuration your application built itself. An application that
already knows its device ids keeps one copy of them, which is how ArmBaseControl,
the robot stack this came out of, uses it.

**The simulator.** `tests/support/sparksim/` is a frame-level SPARK bus with no
hardware. It derives its api class from a firmware version and injects 42 failure
modes, and it is worth reading whatever language you write in.

## The firmware split, which is the one thing to carry away

**The CAN frame layout keys on firmware version.** REV changed it at 25.0.0 and
changed it for both products at once. A SPARK MAX on 25 or later broadcasts on
api `0x2E0` and `0x2E1` with every fault in STATUS_1, exactly as a SPARK Flex
does. Firmware below 25 uses api class 6, where the faults sit in Status 0.

This project keyed on product three times and paid for it each time. Once a 25+
frame decoded with the older layout returned a healthy 13.7 V rail as a fault
bitfield, and gating drive recovery on that wedged a robot until someone read the
raw bytes.

Three primary sources agree on the axis and [docs/PROTOCOL.md](docs/PROTOCOL.md)
quotes all three. Read the firmware version off the wire and branch on that.

## Documentation

Start with the one that matches what you are doing.

| Document | Read it when |
| --- | --- |
| [docs/CAN-SETUP.md](docs/CAN-SETUP.md) | You have an adapter and a Linux box and nothing works yet. |
| [docs/GUIDE.md](docs/GUIDE.md) | You want the hardware explained, from what a motor controller measures through to building CAN frames by hand. Nineteen sections, each with a SKIP IF line. |
| [docs/PROTOCOL.md](docs/PROTOCOL.md) | You are decoding frames or reading parameters and want to know what REV specifies. |
| [docs/FAILURE-CATALOGUE.md](docs/FAILURE-CATALOGUE.md) | Something is behaving badly and you want to know whether it is a known pattern. |
| [docs/BASELINE.md](docs/BASELINE.md) | You want to know what changed on a bus since the day it was good. |
| [docs/SPARK-MAX-BRINGUP.md](docs/SPARK-MAX-BRINGUP.md) | You have SPARK MAX controllers on firmware below 25. |
| [docs/ADVERSARIAL-TESTING.md](docs/ADVERSARIAL-TESTING.md) | You want to see how this proves itself, or you are adding a failure mode. |
| [docs/FIELD-REPORTS.md](docs/FIELD-REPORTS.md) | You want the raw reports behind the catalogue, each graded by how well it is sourced. |
| [examples/](examples/) | You want code to lift. Six files, smallest first: one motor, drive plus steer, the startup gate, a swerve module, seeding one from its absolute encoder, and a whole chassis from a joystick. |
| [docs/INTEGRATION.md](docs/INTEGRATION.md) | You are wiring this into robot code. The path from a driver input to four wheels turning, and how to feed it from a joystick, a ROS node or a planner. |
| [docs/GAPS.md](docs/GAPS.md) | You want to know what this does not do yet, and what would close each gap. |
| [docs/SWERVE-CALIBRATION.md](docs/SWERVE-CALIBRATION.md) | You are calibrating a swerve module, in order, and want to know which steps this library covers. |
| [docs/STEER-CONTROL.md](docs/STEER-CONTROL.md) | You are steering a swerve module: on-device PID against a host-side loop, the gear-ratio conversion, and what the absolute encoder is actually for. |
| [docs/SWERVE-SETUP.md](docs/SWERVE-SETUP.md) | You are bringing up a swerve corner, or replacing a motor or an encoder in one. |
| [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) | Something is misbehaving on a swerve base, ordered by how often each cause turns up. |

## How claims here are graded

A claim backed by a REV specification, a REVLib header or REV's own
documentation is settled. A claim from a forum thread is graded vendor-confirmed,
corroborated or reported, and the first tier is the one safe to build on. A claim
measured here names the bus and the run that produced it.

`sparklib/provenance.py` is the register. Every belief the driver rests on is a
row, graded, with its evidence, and each open row names the command that would
settle it. `spark verify` prints the open rows and exits non-zero, so a check
nobody has run shows up as work rather than as a pass.

[reference/](reference/) holds REV's specification files verbatim, because a
claim citing a document you cannot open is one you cannot check.
[reference/SOURCES.md](reference/SOURCES.md) says what is vendored here and how
to fetch the rest.

## Tests

```console
uv run pytest                # simulated bus, no hardware, safe anywhere
uv run pytest --hardware     # needs a real SPARK bus; see tests/hardware/README.md
```

The simulated suite injects 42 of the 44 catalogued failure modes into a
frame-level bus and asserts on what the driver does about each. The hardware tier
adds `SPARK_HW_*` gates, because some of it writes parameters and some of it
needs a person to cut power between two runs.

## Contributing

Runs on hardware are welcome, on any SPARK product and any firmware version.
Every measurement here names the bus it came from, so a run on a bus this
project has not seen widens what the driver can say with confidence.

The read-only pass takes a minute and needs no configuration beyond the
interface name:

```console
uv run spark status
uv run spark faults
uv run spark params --id <n> --all
```

Open an issue with that output and the firmware version it printed. A run on
firmware 25 or later with SPARK MAX controllers settles several rows in the
register at once.

For the full failure-injection tier, which writes parameters and needs a bus you
can afford to disturb:

```console
uv run pytest --hardware
```

Three references carry the detail. [tests/hardware/README.md](tests/hardware/README.md)
explains the three injection tiers and the `SPARK_HW_*` gates that arm each one.
[docs/ADVERSARIAL-TESTING.md](docs/ADVERSARIAL-TESTING.md) explains how a failure
mode becomes a test and how to add one. `uv run spark verify` lists every open
question in the register, and each row names the command that answers it.

Findings without hardware are welcome too. A thread, a vendor answer or a
reproduction on your own bench all grade in
[docs/FIELD-REPORTS.md](docs/FIELD-REPORTS.md), which records how well each
report is sourced.

## Sources

Everything here rests on published material plus measurement. These are the
sources worth going to directly.

**REV Robotics**

- SPARK MAX parameter table: https://docs.revrobotics.com/brushless/spark-max/parameters
- SPARK Flex specifications: https://docs.revrobotics.com/brushless/spark-flex/specs
- SPARK Flex getting started: https://docs.revrobotics.com/brushless/spark-flex/gs/make-it-spin
- REVLib changelog, which is where firmware behaviour changes are announced: https://docs.revrobotics.com/revlib/install/changelog
- REV Software Binaries, for firmware and the Hardware Client: https://github.com/REVrobotics/REV-Software-Binaries/releases

REV's machine-readable frame specification and the REVLib headers are vendored in
[reference/](reference/), because the tests read them and because a claim citing
a document you cannot open is one you cannot check.

**WPILib and FIRST**

- CAN addressing, which defines the arbitration id layout every FRC device uses:
  https://docs.wpilib.org/en/stable/docs/software/can-devices/can-addressing.html
- Power distribution: https://docs.wpilib.org/en/stable/docs/software/can-devices/power-distribution-module.html
- Brownouts and the voltage behaviour behind most mystery resets:
  https://docs.wpilib.org/en/stable/docs/software/roborio-info/roborio-brownouts.html
- Robot battery: https://docs.wpilib.org/en/stable/docs/hardware/hardware-basics/robot-battery.html

**Swerve modules**

- SDS MK5i, the module on the Flex rig here, at a 26:1 steer reduction:
  https://www.swervedrivespecialties.com/products/mk5i-swerve-module
- The MAX rig runs SDS MK2 modules at 12.8:1. Take your own steer ratio from
  your module's page, because it is the one number that differs between two
  modules that otherwise look identical.

**Linux CAN**

- SocketCAN, the kernel interface this library uses: https://docs.kernel.org/networking/can.html
- python-can: https://python-can.readthedocs.io

**Chief Delphi**

The field reports come from the Chief Delphi forum, harvested 2026-08-27 and
2026-09-01 across 1440 threads. Every finding drawn from it is cited by thread
URL at the point it is used, and graded by how well it is sourced, in
[docs/FIELD-REPORTS.md](docs/FIELD-REPORTS.md). The corpus itself is not
mirrored here; [reference/SOURCES.md](reference/SOURCES.md) says why and how to
re-fetch it.

That forum is where most of this knowledge was already sitting, in pieces. If
this repository is useful, that is largely why.

## License

Apache License 2.0. See [LICENSE](LICENSE).

REV Robotics, SPARK MAX, SPARK Flex, NEO and NEO Vortex are trademarks of REV
Robotics. This project is independent of REV Robotics. Vendor specifications
under [reference/](reference/) are REV's, reproduced so a reader can check a
claim against them.
