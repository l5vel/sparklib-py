# Known gaps

What this driver does not do yet, and what would close each. Every entry here is
pinned by a test marked `xfail`, so the day the behaviour changes the suite says
so instead of staying quietly green.

Run `uv run pytest -q -rx` to see them with their current reasons, and
`uv run spark verify` for the separate question of which beliefs about the
hardware are measured.

## The bus can hide a stopped frame

`audit_problems` reads the STATUS_1 period only. A controller that stops
broadcasting STATUS_0 while STATUS_1 keeps perfect time is reported healthy, and
STATUS_0 is where the telemetry and the limit bits live. Closing it means scoring
every api class the generation expects, not one.

`inventory` also counts every REV frame on an id into that period, including
parameter writes from another host on the same bus. Two hosts administering one
bus therefore looks like a controller broadcasting too fast. The audit has no
rule that names the writer.

A saturated bus reaches `status_1_verdict` through one path with a bare mean
rather than the timing evidence, so it reads as a reverted configuration. The
same case is correct on hardware, where `inventory` supplies the evidence.

## Duplicate ids are found by only one signature

`duplicates` keys entirely on the UNIQUE_ID broadcast. A twin whose UNIQUE_ID
frame is switched off is a duplicate the driver cannot see, even though the
catalogue's primary signature, two different STATUS_0 payloads alternating on one
id, is already arriving in frames it receives.

Worse for diagnosis, `collect_status` keeps one payload per device and api, so
the last frame on a shared id overwrites the previous one. A faulted twin is
masked by its healthy partner. Detecting the alternating signature needs
per-frame divergence tracking, which does not exist yet.

## Silent and halted look the same

`audit_problems` is handed an inventory and nothing else, so a controller that
answers a direct request and one that is off the bus entirely both produce "is
not broadcasting". Separating them means probing the missing ids rather than
inferring from silence.

## The audit listens twice

`cmd_audit` listens once for the inventory and again for the duplicate check,
then compares two different windows as though they were one. A single scan
serving both would make the comparison sound and halve the time.

## Firmware drift is catalogued but not injected

Two of the 44 failure modes concern firmware differing across a fleet. Both are
documented in [FAILURE-CATALOGUE.md](FAILURE-CATALOGUE.md) and neither has a
simulator injector, because the simulator models one firmware generation per
controller and not a fleet that disagrees with itself.

## SPARK MAX on firmware 25 or later, believed but unmeasured

Every claim in the register is measured or vendor-stated for both products, but
the measurements come from two firmware generations on two rigs. The gap is a
SPARK MAX running firmware 25 or later. The driver believes such a device
broadcasts and is written exactly like a SPARK Flex, on three independent primary
sources, and no one here has confirmed it on a bus.

If you have one, `uv run spark status` and `uv run spark params --id N --all`
settle it in about a minute. See the contributing section of the README.

## What is deliberately absent

On-device closed-loop control, MAXMotion profiling and the encoder configuration
objects belong to REVLib, which runs the controller. Firmware updates and factory
resets need USB-C and REV Hardware Client. The factory reset is refused over CAN
on purpose, because it drops the CAN id, motor type and idle mode together and no
read confirms what it did.
