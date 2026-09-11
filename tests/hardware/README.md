# The hardware injection suite

Field failures from the Chief Delphi corpus, injected into the robot in front of
you and read back through the tool an operator actually runs.

`tests/adversarial` asks whether the driver sees a failure, using a modelled
bus. This suite asks the same question of the wire: eight real SPARK Flex
controllers on a real 1 Mbit adapter, real arbitration, and the same
`SparkAdmin`, `collect_status` and `spark audit` that run on the robot. The
simulator can prove that a decoder is wrong. Only hardware can prove that a
persisted value survived the rail going off, or that this firmware answers a
burst of writes, or that a frame this host wrote is indistinguishable from a
controller's once it reaches the driver.

Every failure comes from `docs/FAILURE-CATALOGUE.md`, which is 353 threads and
about 3,383 posts distilled into 43 numbered entries plus two unnumbered bus
signatures. All 45 have a disposition in `tests/support/sparkhw/catalogue.py`,
and `tests/unit/test_spark_hardware_catalogue.py` fails if any entry loses one. That test needs no robot, so the coverage claim is
checked on every run of the ordinary suite.

## Running it

```bash
uv run --with pytest pytest tests/hardware -q --hardware          # read-only tier
uv run --with pytest pytest tests/hardware --hardware -m "not inject and not staged"
SPARK_HW_INJECT=1 uv run --with pytest pytest tests/hardware -q --hardware
```

Use `-s` for the staged runs. They print instructions between stages, and pytest
swallows those without it.

## The three tiers

Collected sizes, which `pytest --collect-only -m <marker>` regenerates:

`_apply_tier_markers` in `conftest.py` decides the selections, and
`pytest --collect-only -m <marker>` prints their current sizes. Five modules carry
`inject`: `test_wire_injection`, `test_config_injection`, `test_spark_persistence`,
`test_legacy_period_write` and `test_legacy_fault_injection`. One carries `staged`:
`test_staged_physical`. Everything else collects in the read-only selection,
`test_legacy_burn_flash` included, which gates its own writing stages on
`SPARK_MAX_CYCLE_STAGE` instead of on a marker.

Those markers come from one `pytest_collection_modifyitems` in `conftest.py`.
There were two, and the second shadowed the first, so no marker was applied at
all and every selection collected the whole directory. The write-and-persist
module then sat inside the selection documented as read-only, and three runs spent a flash cycle each before anyone noticed.
`tests/unit/test_hardware_tier_markers.py` now fails if the hook is defined twice,
if a writing module loses its marker, or if a test that persists stops gating on
`SPARK_HW_FLASH`.


**wire** transmits the failure's own frames onto the live bus from this host. No
controller is written to, and stopping the stream ends the failure. socketcan
loops a transmitted frame back to every other socket on the interface, so
`SparkAdmin` receives what the injector writes and cannot tell it from a
controller's own broadcast. The kernel marks locally created frames
`MSG_DONTROUTE`, which python-can surfaces as `Message.is_rx is False`, and that
is the only field that differs. Nothing in `admin` reads it.

**config** writes one parameter into one controller, restores it, and verifies
the restore on the wire. Only Status 0 Period and Status 1 Period, because their
effect is the broadcast cadence and the cadence is the read-back. That scope
predates, when every parameter 0-255 became readable on 26.1.6, so
the restore of any setting can now be proved by reading it as well as by timing
a frame. Widening the tier is a separate change and has not been made. Nothing is persisted, so flash holds the
provisioned value throughout and cutting the motor rail is the backstop if the
process is killed mid-test.

**staged** needs a person. Power is cut, a connector is pulled, a meter is read,
and the test runs in two stages around the physical act.

## Gates

`--hardware` buys the read-only tier and the wire injections that use a free CAN
id. Each gate below buys one further class of side effect, and all of them are
off by default.

| variable | what it allows | what it risks |
| --- | --- | --- |
| `SPARK_HW_INJECT=1` | a reversible period write on the nominated controller | that controller stops broadcasting one status frame for a few seconds |
| `SPARK_HW_COLLIDE=1` | transmitting on a CAN id a controller already owns | occasional arbitration errors on that controller, one error frame each |
| `SPARK_HW_CONGEST=1` | filling the bus with frames nothing decodes | the adapter can reach bus-off, and this fleet runs `restart-ms 0` |
| `SPARK_HW_FLASH=1` | `PERSIST_PARAMETERS` | one flash cycle per call, out of an endurance of 1e4 to 1e5 |
| `SPARK_HW_STAGE=<name>` | one staged run | whatever that stage asks a person to do |
| `SPARK_HW_ID=<id>` | moves the writes to another controller | defaults to the lowest configured id |

## Staged runs

Each stage is two commands with a physical act between them.

```bash
# A5, A8, D3, E4 and the CD 460577 wake signature
SPARK_HW_STAGE=cycle-arm uv run --with pytest pytest tests/hardware -s --hardware
SPARK_HW_STAGE=cycle-verify uv run --with pytest pytest tests/hardware -s --hardware
# cut and restore the motor rail WHILE the verify stage waits, up to SPARK_HW_WAIT_S

# A2 and A3: the persist that overtakes the write it was meant to commit
SPARK_HW_FLASH=1 SPARK_HW_STAGE=settle-arm...
# cut and restore motor power
SPARK_HW_FLASH=1 SPARK_HW_STAGE=settle-verify...

# B1 and A5: does SET_CAN_ID reach flash on 26.1.6
SPARK_HW_FLASH=1 SPARK_HW_STAGE=can-id-arm...
# cut and restore motor power
SPARK_HW_FLASH=1 SPARK_HW_STAGE=can-id-verify...

SPARK_HW_UNPLUG_ID=12 SPARK_HW_STAGE=unplug-can...
SPARK_HW_UNPLUG_ID=12 SPARK_HW_STAGE=unplug-encoder...
SPARK_HW_UNPLUG_ID=12 SPARK_HW_STAGE=swap-hl...
SPARK_HW_TERMINATION_OHMS=59.7 SPARK_HW_STAGE=termination...
SPARK_HW_LED=blinking-magenta SPARK_HW_STAGE=led...
SPARK_HW_INSPECT=docking-screws,jst-seated,can-connectors,terminator-present \
  SPARK_HW_STAGE=inspect...
```

`cycle-verify` starts a recorder and waits, so start it first and cut power while
it runs. That is how the wake capture gets the first frames after the rail comes
back, which is where CD 460577's fault-bit storm lives.

The staged state lives in `/tmp/spark_hw_stage.json`, overridable with
`SPARK_HW_STATE`.

## Ordering, which is load-bearing

Read faults before clearing anything. A power cycle latches `hasReset` on every
controller, and that bit is the only evidence the reboot happened at all.
`spark clear` erases it, and so does `BaseHandler`, because
`SparkBus.apply_boot_config` sends Clear Faults to every SparkFlex it
initialises. On rig-flex-2 the clear ran first and cost a real
observation, which is what driver defect D5 does in the field.

```
uv run spark faults     # FIRST, after any power cycle
uv run spark status
uv run spark clear      # LAST, and only if the bus is silent
```

The same applies after an injector tier. `SPARK_HW_COLLIDE` and
`SPARK_HW_CONGEST` leave a sticky `can` fault on all eight controllers, and
nothing clears it for you, because an automatic clear would erase the `hasReset`
that a real reboot leaves behind. So a read-only run started after one of those
inherits a faulted fleet. The `clean_bus` fixture reads the bus before any
`needs_clean_bus` test and skips it with that instruction rather than failing on
its own premise. Record and clear between tiers, in that order:

```
uv run spark faults     # records what is set, into records/
uv run spark clear      # the command that actually clears
```

`spark faults` only reads and records. `spark clear` is what erases the bits, and
it erases `hasReset` too, which is the only evidence a reboot happened.

The `cycle-verify` stage enforces this structurally. A session fixture reads
faults and inventory once, before any test in that stage can send a frame, so
moving a test in the file cannot reorder the reading.

## Guards

Four things are refused before any test runs.

**A live experiment.** `sparkhw.guards.LIVE_EXPERIMENTS` lists base indexes that
are carrying an experiment, and the whole directory skips on one. rig-flex-2 is
there now: five drifted controllers are the control condition and three intact
ones are the subjects, and one `spark repair --persist` ends both halves. Remove
an entry when the experiment is finished and written up.

**A bus that is not at rest.** A running `SparkBus` sends `SECONDARY_HEARTBEAT`
every 20 ms and the controllers are then enabled. Writing a parameter into a
controller that is driving is exactly the missing precondition the adversarial
suite has an xfail for.

**An adapter that is bus-off.** This fleet runs `restart-ms 0`, so the state does
not clear itself and every later test would read a dead adapter and report eight
dead controllers. The link is checked before and after each test.

**A frame a controller acts on.** The injector may only emit frames a REV device
itself emits: periodic status frames, the unique-id broadcast, and response
frames. Twenty-one command bases are refused outright, including both
heartbeats, the seven 26.1.6 setpoints, the two resets and the bootloader entry.
That list has a history. On a blind RTR sweep across all 1024 api
values reached `ENTER_SWDL_CAN_BOOTLOADER`, which needs no payload, and device 17
stopped broadcasting. Clear Faults did nothing and cutting the motor rail brought
it back. The frame sits one bit pattern away from `GET_FIRMWARE_VERSION`, which
the same sweep had just used successfully.

## If something is left injected

Nothing this suite writes is persisted, so **cut and restore the motor rail** and
every controller reloads its provisioned configuration from flash. That is the
answer for a starved status frame, a period left at 250 ms, and a CAN id moved by
`set_can_id`, unless a staged run with `SPARK_HW_FLASH=1` persisted it
deliberately, in which case its verify stage puts it back.

For a CAN id that will not come back, `SET_CAN_ID` is addressed by hardware
serial and works whatever address the controller answers on:

```bash
uv run spark status
uv run spark set-id --serial 6B029ADD --to 12
```

For an adapter in bus-off:

```bash
sudo ip link set can0 down && sudo ip link set can0 up
```

The congestion tests can leave a `can` sticky fault set on a controller. That is
evidence rather than damage, and `spark faults` reads it before `spark clear`
removes it.

## What is refused, and why

Thirteen of the 44 entries are not injected here. Each one says what would break,
in `catalogue.py`, and most of them are covered frame for frame in
`tests/adversarial` against the simulator, which is what a simulator is for.

The reasons fall into three groups. Some injections need a write this firmware
cannot prove was undone: Motor Type, the follower leader id, the filter settings,
and the mode button that flips motor type on a three second press. That reason
weakened, because a written value can now be read back on either
generation. What still argues against these four is what they cost when a
restore fails, not whether the restore is observable. Some need damage: a shorted NEO destroyed three
controllers in the report behind E5, and a factory reset drops the CAN ID on a
provisioned controller. Some are not frames at all: `configureAsync()` is a
REVLib call, and `kS` crashing on v26 needs REVLib loaded, and this driver writes
raw frames and loads neither.

A clean run of this suite is not a healthy robot. It says that the failures the
corpus describes were injected and that the tool reported what it reported. Until a controller could also pass everything here while sitting in COAST
with no closed-loop sensor, because eight of the nine deviating settings were
invisible over CAN. `spark audit` reads all nine now and fails on that
controller, which closes the gap this paragraph was written to disclose. What a
read cannot tell you is whether the firmware APPLIED a value it stored.
