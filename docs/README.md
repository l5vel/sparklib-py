# Documentation

| Document | Read it when |
| --- | --- |
| [CAN-SETUP.md](CAN-SETUP.md) | You have an adapter and a Linux box and nothing works yet. Kernel modules, packages, udev rules, sudoers, and how a netdev gets its name. |
| [GUIDE.md](GUIDE.md) | You want the hardware explained. A field guide from what a motor controller measures through to building CAN frames by hand. |
| [PROTOCOL.md](PROTOCOL.md) | You are decoding frames or reading parameters and want to know what REV specifies. |
| [BASELINE.md](BASELINE.md) | You want to know what changed on a bus since the day it was good. |
| [FAILURE-CATALOGUE.md](FAILURE-CATALOGUE.md) | Something is behaving badly and you want to know whether it is a known pattern. |
| [FIELD-REPORTS.md](FIELD-REPORTS.md) | You want the raw reports behind the catalogue, graded by how well each is sourced. |
| [FINDINGS-DISPOSITION.md](FINDINGS-DISPOSITION.md) | You are picking the work up and want to know which findings became tests. |
| [GAPS.md](GAPS.md) | You want to know what the driver does not do yet, each pinned by an xfail test. |
| [SWERVE-CALIBRATION.md](SWERVE-CALIBRATION.md) | You are calibrating a swerve module: alignment, offsets, steer gains, and what needs an absolute encoder. |
| [INTEGRATION.md](INTEGRATION.md) | You are wiring this into robot code: a joystick, a ROS node, a planner. The path from a driver input to four wheels turning. |
| [STEER-CONTROL.md](STEER-CONTROL.md) | You are steering a swerve module and want to know whether to close the loop on the device or on the host. |
| [SWERVE-SETUP.md](SWERVE-SETUP.md) | You are bringing up a swerve corner for the first time, or after replacing a motor or an encoder. |
| [SWERVE-ALIGNMENT.md](SWERVE-ALIGNMENT.md) | You are pointing the wheels at a common zero, which every offset is measured against. |
| [TROUBLESHOOTING.md](TROUBLESHOOTING.md) | Something on a swerve base is misbehaving, ordered by how often each cause turns up. |
| [SPARK-MAX-REFERENCE.md](SPARK-MAX-REFERENCE.md) | You are on a SPARK MAX below firmware 25 and want the per-product reference. |
| [ADVERSARIAL-TESTING.md](ADVERSARIAL-TESTING.md) | You want to see how this proves itself, or you are adding a failure mode. |
| [SPARK-MAX-BRINGUP.md](SPARK-MAX-BRINGUP.md) | You have SPARK MAX controllers on firmware below 25. |
| [API-REFERENCE.md](API-REFERENCE.md) | You are looking up a public class or function signature. |

## The one thing to know

The CAN frame layout keys on firmware version. REV changed it at firmware 25.0.0
and changed it for both controllers at once. A SPARK MAX running 25 or later
broadcasts on api 0x2E0 and 0x2E1 with every fault in STATUS_1, exactly as a
SPARK Flex does. Firmware below 25 uses api class 6, where the faults sit in
Status 0.

This project keyed on product three times and paid for it each time. Decoding a
25+ frame with the older layout once reported a healthy 13.7 V rail as a fault
bitfield, and gating recovery on that wedged a robot.
[PROTOCOL.md](PROTOCOL.md) carries the three primary sources that settle it.

## How claims here are graded

A claim backed by a REV specification, a REVLib header or REV's own
documentation is settled. A claim backed by field reports is graded in
[FIELD-REPORTS.md](FIELD-REPORTS.md) as vendor-confirmed, corroborated or
reported, and the first tier is the one safe to build on. A claim measured here
names the rig and the run that produced it.

Where a document says something is unverified, nobody has measured it. Those are
leads, and `spark verify` lists them with the command that settles each.

## The rigs the measurements came from

Measurements cite the bus they were taken on. There are three, and every claim
that names one means this:

| label | what it is |
| --- | --- |
| `rig-flex` | eight SPARK Flex on firmware 26.1.6, gs_usb adapter, 1 Mbit classic CAN |
| `rig-max` | eight SPARK MAX on firmware 24.0.1, gs_usb adapter, same bus speed |
| `rig-flex-2` | eight SPARK Flex on 26.1.6 behind a CANable slcan adapter, running a deliberate brownout experiment |

All three are swerve drive bases, so a role is `drive` or `steer` and a position
is a wheel corner. Nothing about the protocol depends on that.

## What lives elsewhere

REV's own specifications sit in [../reference/](../reference/), because the tests
read them and because a claim citing a document you cannot open is one you cannot
check.

Dated run logs sit in [runs/](runs/), one file per session at the hardware. A run
log carries a correction banner where a later measurement overturned it, and it
stays standing as the record.

Raw hardware captures stay out of this repository. Candumps, current traces and
CSV logs run to hundreds of megabytes and none of it is source. The measurements
drawn from them are written into the documents here, and
[PROTOCOL.md](PROTOCOL.md) records what remains unmeasured.
