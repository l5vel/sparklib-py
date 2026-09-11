# SPARK CAN API reference

Generated from the source by `tools/gen_api_reference.py`. Every
line below is a real symbol and its own first docstring line, so regenerate this
rather than editing it by hand.

Modelled on CTRE's Phoenix 6 package summaries, with one deliberate difference.
Theirs lists simulation and hardware classes together in one alphabetical table
and leaves the `Sim` prefix to tell them apart. This one separates them, because
confusing the two is the specific failure this project keeps hitting: the
simulator once modelled a bus the driver expected instead of the bus the
hardware produces, and 1206 tests passed against something firmware 25+ cannot
emit.

## How the pieces fit

    cli            the `spark` command: status, audit, faults, clear, repair
      -> admin     decode, audit, and the only code that writes a parameter
    can_bus            SparkBus: the socketcan reader thread and frame router
      -> controller  one motor object, telemetry decoded per firmware generation
    provenance     every claim the driver rests on, and how it was got

    sparksim             a frame-level bus with no hardware: controllers with RAM
                         and flash, a virtual clock, and 24 failure injectors
    sparkhw              the same tests against a real bus, behind SPARK_HW_* gates

Four rules the API assumes, each learned expensively:

- **Frame layout keys on FIRMWARE VERSION, not product.** `api_set`,
  `fault_frame` and `normalise_generation` take a generation and REFUSE a
  product name. See `docs/PROTOCOL.md`.
- **A silent bus is the normal state.** SPARKs are gated and broadcast nothing
  until asked. `inventory()` is passive on purpose, so an empty reading means
  "nobody spoke", not "nobody is there".
- **Reading the bus consumes frames.** SocketCAN hands each frame to exactly one
  reader. A helper that listens inside an operation starves whatever else is
  reading, which is why `driving_ids()` takes a reading rather than taking one.
- **A parameter read is a remote frame carrying dlc 8.** Both halves are
  required, and the spec only tells you the first. A zero-length data frame and
  a remote frame with dlc 0 are both ignored by firmware 26.1.6. The rule does
  not generalise: `GET_FIRMWARE` is marked rtr in the same spec and answers at
  dlc 0. This package sent data frames for eight days and recorded the silence
  as a property of the firmware.


## Driver: decode, audit and write over raw CAN


### `spark_admin`

SPARK Flex/MAX administration over CAN: inventory, config audit, repair.

```
unexpected_generation()          A sentence describing a fleet-assumption violation, or None.
normalise_product()              The canonical product name, for the SPARK_MODEL check that needs one.
normalise_generation()           The canonical firmware generation. Refuses a product name outright.
api_set()                        Status-frame API classes for a firmware generation. Defaults to 25+.
duplicate_detection_available()  Whether two controllers on one id can be told apart on this generation.
classify_reset()                 Why a controller restarted, read from whichever list carries the bits.
write_fault_record()             Persist a pre-clear capture and return the path.
driving_ids()                    {dev: applied_output} for every controller in a reading that is driving.
class MotorNotAtRestError        Raised before any frame goes out, when the target is driving.
class ProtectedParameterError    Raised when a write targets a safety-interlock parameter.
class SparkAdmin                 Read-mostly admin session on one SPARK bus. Never sends setpoints.
  .close()
  .inventory()                   {device_id: {'serial','frames','periods_ms'}} for every REV controller.
  .duplicates()                  {device_id: [fingerprint,...]} for ids answered by more than one device.
  .read_fingerprint()            The pre-25 per-device fingerprint at api 0x094, or None.
  .firmware()                    (version_string, hw_rev) or (None, None). A remote frame at dlc 0.
  .status_period_ms()            Observed broadcast period, or None if the frame is silent.
  .clear_faults()                Clear latched faults, and report what was erased and what released.
  .identify()                    Blink one controller's LED on FIRMWARE 25+, addressed by serial.
  .identify_by_id()              Blink one controller's LED on PRE-25, addressed by CAN id.
  .read_param_pair()             Read a parameter over the documented READ_PARAMETER frame.
  .read_param_value()            One parameter's raw uint32, or None. Wraps the pair frame.
  .param_types()                 Which parameter ids exist on this device, sixteen types per frame.
  .require_at_rest()             Refuse if `dev` is driving. Listens only; sends nothing.
  .write_param()                 Write one parameter to RAM. Returns the device's response dict.
  .set_legacy_status_period()    Set one pre-25 status period. Returns nothing: the device never answers.
  .read_legacy_param()           Read one parameter from a pre-25 controller. None if it never answers.
  .write_legacy_param()          Write one parameter to a pre-25 controller's RAM and read the echo.
  .persist()                     Commit RAM parameters to flash. Returns the result code, 0 = success.
  .set_can_id()                  Reassign a controller's CAN id, addressed by serial, and commit it.
float_bits()                     Encode a python float as the uint32 PARAMETER_WRITE carries.
status_0_implausible()           Decoded keys whose value is out of band; empty when the frame is a reading.
describe_implausible()           One line naming the pegged fields, shared by the audit and the CLI.
decode_status_0()                Applied output, bus voltage, output current, temperature and flags.
decode_legacy_status_0()         LEGACY_STATUS_0 (api 0x060), and whether it carries readings at all.
legacy_frame_is_beacon()         True when a 0x060 payload is the firmware-25+ presence beacon.
legacy_other_signals()           Byte 6 of a pre-25 LEGACY_STATUS_0 frame, or None if there is no frame.
legacy_other_signals_problem()   A reason string when byte 6 is not the value a driving fleet reports.
decode_legacy_status_1()         Pre-25 Periodic Status 1 (api 0x061): telemetry, and no fault word.
decode_status_1()                Active and sticky faults and warnings. This is where errors actually live.
generation_from_apis()           Which frame generation a controller speaks, from what it broadcast.
dominant_generation()            The generation most controllers on this bus reported.
observed_generation()            The generation this bus actually reported, or None if it reported none.
collect_status()                 {device_id: {'status0':..., 'status1':..., 'generation':...}}.
param_enum_value()               The ordinal a declared enum NAME writes to `param_id`, or None.
declared_value()                 The provisioned value for one parameter, from the declared config file.
declared_status_1_period_ms()    Provisioned Status 1 Period. Single source of truth for canary and repair.
fault_frame()                    The frame this firmware generation reports faults in, and its cadence.
status_1_verdict()               A verdict for one controller's STATUS_1 cadence, given what was measured.
firmware_reports_spark_model()   True when this firmware puts SPARK_MODEL in STATUS_0. None means unknown,
remedy_for()                     The action for each named bit, joined. Empty when none is documented.
normalised_reading()             One shape for a bus reading, whichever generation produced it.
reading_from_raw()               One normalised reading from two BUFFERED status frames.
status_problems()                Findings from one decoded bus reading: faults, flags and telemetry.
audit_problems()                 Pure audit: compare a bus reading against config, baseline and status.
coverage_note()                  What an audit did NOT check. Printed alongside every clean result.
set_controller_type()            Declare which product's configuration file this process should read.
motor_defaults_key()             The config key holding a product's declared configuration.
motor_defaults_file()            How to refer to the declared configuration in a message to an operator.
class MotorDefaultsMissing       The declared per-motor configuration is not on disk.
class MotorDefaultsWrongProduct  The declared configuration was written for a different SPARK product.
motor_defaults_product()         The controller_type the declared configuration was written for, or None.
require_motor_defaults_for()     Raise when the declared configuration is for another product.
load_motor_defaults()            The declared configuration for every motor.
config_env_var()
motor_settings()                 Resolved {param_id: {name, value, type, deviates}} for one role.
declared_deviations()            {param_id: name} the ACTIVE product's file marks as deviating.
deviating_settings()             Only the settings a factory reset drops -- what re-provisioning must restore.
legacy_param_value()             A parameter reply's raw word as the type the controller said it is.
legacy_readable_deviations()     {param_id: setting} for declared deviations a pre-25 read can reach.
legacy_deviation_problems()      Declared configuration vs the parameter table, on pre-25 only.
declared_param_value()           A raw uint32 read off the wire, as the type the declared file says it is.
declared_raw_value()             A declared value as the raw uint32 a parameter write carries.
declared_rows()                  What one controller holds for each declared setting, against the file.
read_param_table()               {param_id: raw uint32} and {param_id: type name} for one controller.
fleet_param_table()              What the whole fleet agrees on, over the ids the declared file leaves out.
baseline_param_problems()        Notes where the fleet no longer holds what the baseline recorded.
modern_deviation_problems()      Declared configuration vs the parameter table, on firmware 25+.
```

### `spark_cli`

`spark` -- SPARK bus inventory, config audit and remote repair.

```
register_power_provider()   Install a callable returning an object with `.percent`, or None.
adapter_instability()       Has this USB CAN adapter been falling off the bus lately?
adapter_instability_note()  The sentences to print when a silent bus might be a silent adapter.
cmd_snapshot()              Capture this bus as the baseline for this base index.
cmd_defaults()              Show the declared configuration every motor is provisioned to.
cmd_status()
cmd_throttle()              Re-send the volatile per-boot status periods a pre-25 fleet loses.
cmd_clear()
cmd_duplicates()
cmd_identify()              Blink one controller's LED, in whichever form this generation wants.
cmd_set_id()
cmd_params()                Read and write the parameter table, in whichever dialect the bus answers.
cmd_canfix()                Clear a wedged USB CAN adapter without physically replugging it.
cmd_verify()                What this driver believes about the hardware, and what it has checked.
cmd_persist()
cmd_repair()
cmd_provision()             Restore every declared setting one controller has drifted on.
cmd_faults()
cmd_voltage()               Motor-rail voltage measured at each controller, plus the pack SoC.
cmd_audit()
cmd_learn_serials()         Record each controller's identity into serials.
main()
```

## Runtime: the objects that drive motors


### `can_bus`

```
class SparkBus
  .init_controller()          Initializes a SPARK controller for sending and receiving messages.
  .apply_boot_config()        Send the per-power-cycle setup a freshly booted SPARK needs.
  .reconfigure_controllers()  Re-apply apply_boot_config() to every registered controller.
  .send_msg()                 Sends msg to controllers via CAN bus.
  .netdev_tx_packets()        Frames this interface has actually put on the wire, or None if unreadable.
  .bus_monitor()              Thread for monitoring the bus for receivable messages.
  .route_frame()              Fan one received frame out to the controller it belongs to.
  .enable_heartbeat()         Enables heartbeat runnable for sending heartbeat message to CAN Bus.
  .broadcast_disable()        Tell every actuator on the bus to stop driving, now.
  .disable_heartbeat()        Disables heartbeat runnable for sending heartbeat message to CAN Bus.
  .wait_for_heartbeat()       Block until the heartbeat thread has sent its first frame.
  .shutdown()                 Stop heartbeat + close the bus. Idempotent.
  .set_heartbeat_ids()        Override which CAN IDs are marked enabled in the SPARK heartbeat.
  .heartbeat_payload_hex()    Return current heartbeat payload as hex for diagnostics.
  .live_ids()                 CAN IDs currently broadcasting STATUS_0 (set by bus_monitor).
  .silent_ids()               Registered CAN IDs with no frame in the last `window_s`, sorted.
  .observed_spark_ids()       Every REV motor-controller device ID seen on the bus so far.
```

### `controller`

```
packer_float()
packer_float_four()
class Controller
  .seconds_since_seen()         Age of the last frame from this controller, or None if never seen.
  .is_live()                    True if a frame arrived within `window_s`. A controller that has gone
  .enable()
  .disable()
  .clear_faults()
  .set_periodic_frame_period()
  .decode_message()             One-liner string describing a received CAN frame.
  .stop_follower_mode()
  .print_diagnostics()
  .percent_output()
  .velocity_output()
  .position_output()
  .set_encoder_position()
  .velocity()
  .position()
  .applied_output()
  .primary_heartbeat_lock()
  .note_frame_api()             Record the generation from the api a frame actually arrived on.
  .active_faults()              Fault mask, or None when this reader cannot honestly produce one.
  .sticky_faults()
  .bus_voltage()
  .output_current()
  .is_follower()
  .hard_forward_limit()         STATUS_0 byte 6 bit 0 on 26.1.6. The bit the 24.x reader called
  .hard_reverse_limit()
health_report()                 Everything worth knowing about one SPARK, printed at startup.
```

## Swerve: absolute encoders, steering and chassis math


### `kinematics`

Swerve kinematics: chassis velocity in, per-module speed and angle out.

```
module_geometry()     {label: (x, y)} module positions from the robot centre, in metres.
module_states()       {label: (speed, angle_rad)} for a chassis velocity.
desaturate()          Scale every module by one factor when any exceeds full output.
optimize()            Take the short way round, reversing the wheel if that is shorter.
chassis_from_stick()  Joystick axes to a chassis velocity, with a deadband and optional expo.
```

### `steer`

Host-side steer loop primitives, and the convergence test for tuning one.

```
normalize_deg()       Wrap an angle to (-180, 180].
normalize_rad()       Wrap an angle to (-pi, pi].
shortest_error_deg()  Signed error by the shorter way round, which is the one to steer.
p_output()            Duty cycle for a proportional steer loop. Zero inside the deadband.
p_controller()        A p_output closure holding one set of gains.
pd_output()           P plus D. D damps the overshoot a high kp produces.
slew()                Move `commanded` toward `wanted` by at most `max_step`.
settled()             Has the axis arrived? Three rules, because one of them can deadlock.
score_step()          Score one commanded angle step. Lower is better.
```

### `cancoder`

The absolute-encoder half of a swerve module, for a CTRE CANcoder.

```
available()             True when the swerve extra is installed.
bus_arg()               A bus argument phoenix6 accepts across its 24.x to 26.x versions.
open_encoder()          One CANcoder on the named bus.
open_module_encoders()  {label: CANcoder} for a `devices` group, so a whole set opens at once.
warmup()                Block until every signal reads a value that has held still.
read_angle_deg()        Absolute angle in degrees, waiting for a fresh stationary reading.
wheel_angle_deg()       Wheel angle from a raw reading and this corner's recorded zero offset.
motor_rotations()       Steer motor rotations for a wheel angle. This is where a wrong ratio bites.
seed_from_absolute()    Teach a SPARK where its steer axis actually is, once, at startup.
measure_offset_deg()    The offset to record for a corner you have physically aligned to zero.
describe()              Configuration and health of one CANcoder, read-only.
class Refused           A tool cannot run because the config does not describe any encoders.
modules()               ({label: device_id}, bus_name) for the corners a tool should act on.
offsets()               {label: offset_deg} recorded for each corner, empty when none are set.
present()               True when this id is actually answering on the bus.
sticky_faults()         The raw sticky fault field, or None when this firmware omits it.
decode_sticky()         [names] for the bits set in a sticky fault field.
actionable_sticky()     The part of a sticky field that a person should act on.
firmware()              Firmware version string, or None when this firmware omits the signal.
health_report()         Everything worth knowing about one CANcoder, printed at startup.
```

### `trace`

Per-tick anomaly detection for a steer axis under closed-loop control.

```
class CornerTrace  One corner's recent history, checked for anomalies as it fills.
  .set_target()    Reset the divergence and fight baselines for a new setpoint.
  .sample()        Record one control tick and check it against the previous one.
  .latest()        The most recent sample, or None before the first one.
  .dump_tail()     Print the last n samples, for the moment after something fired.
```

## Host setup: config, netdev and the motor rail


### `config`

The one configuration file, and how sparklib finds it.

```
config_path()  Where the config is being read from, without loading it.
load_raw()     The config as plain dicts, which is what the declared-settings code wants.
load_config()  The config as a namespace tree, which is what the CLI wants.
get()          The loaded config, read once per process.
reload()       Re-read the config. Used by tests that point at a fixture file.
set_config()   Install a config a host application built itself.
load_host()    Let an installed application install its own configuration first.
```

### `netdev`

SocketCAN netdev bring-up and USB-adapter recovery, independent of any SPARK.

```
present_can_netdevs()     Every CAN netdev on this machine, by name.
check_netdev()            Raise unless the named CAN netdev exists and is UP.
class CanNetdev           Bring one SocketCAN interface up, and recover it when it wedges.
  .is_available()         True once the netdev exists under the configured name.
  .netdev_exists()
  .check_can_interface()
  .setup_can_interface()  Performs a deep reset to clear 'Transmit buffer full' errors.
  .start_can()            The main entry point used in your script.
  .bring_up_can_buses()   Wait for the netdev, then bring the bus up and return its name.
```

### `rail`

Grading a motor rail's voltage, and latching the result without flapping.

```
class PowerLevel       Ordered worst-last so max() picks the more severe of two sources.
  .severity()
  .is_distress()
classify_power()       Percentage of usable range -> PowerLevel. None in, None out.
rail_endpoints()       (full_v, empty_v) for the rail, or (None, None) if it cannot be graded.
rail_implausible()     Why this reading cannot be a charge level, or None if it is believable.
rail_usable_pct()      Where `volts` sits in the pack's usable range, 0-100, or None.
class PowerFsm         Three-state latch with hysteresis, so a boundary reading cannot flap.
  .update()
  .distress_message()
class RailStatus       Motor-rail voltage as measured at the SPARK controllers.
  .mean_v()
  .min_v()
  .max_v()
  .spread_v()
  .to_dict()
get_rail_status()      Read motor-rail voltage from the SPARK bus. None if it cannot be read.
```

## Evidence: what the driver believes and how it was got


### `provenance`

What this driver believes about SPARK hardware, and how each belief was got.

```
class Claim    One thing the driver believes, and the evidence for it.
  .settled()
for_product()  Claims that bear on one product, including the ones marked BOTH.
unsettled()    Claims this driver relies on without having checked them.
report()       Lines for `spark verify`, settled first so the gaps read last.
```

## Simulation: a frame-level bus, no hardware


### `sparksim.bus`

A python-can drop-in that broadcasts a modelled fleet on a virtual clock.

```
class SimMessage
  .coerce()
class SparkBusSim          The bus a SparkAdmin talks to. Nothing here costs real time.
  .channel_info()
  .shutdown()
  .send()
  .recv()
  .schedule()              Run fn(sim) when virtual time reaches `at`. The escape hatch for
  .controllers_at()
  .controller()
  .add()
  .silence()
  .bus_off()
  .power_cycle()
  .silent_until_cleared()
  .set_period()
  .disable_frame()
  .revert_to_defaults()
  .set_can_id()
  .revert_to_id_zero()
  .congestion()
  .set_fault()
  .clear()
  .brownout()
  .brownout_stages()       A rail sag through the roboRIO's three brownout stages.
  .bus_wedged_flat()       The CAN bus inaccessible with utilisation flat at zero.
  .accel_transient()       The current fault a hard acceleration constraint provokes.
  .heartbeat_gap()         A gap in the enable heartbeat longer than the spec's window.
  .flex_status_timeout()   The Flex-only status-frame timeout defect of 2024.
  .drop_config_write()     One missed configuration message, and the sensor fault it causes.
  .current_chop()          The SECOND factory threshold, distinct from the Smart Current Limit.
  .limit_holds_at_value()  What the Smart Current Limit looks like when it is working.
  .stuck_current()         Pin a controller's reported output current, load or no load.
  .loose_dock()            The Vortex-to-Flex docking joint not fully seated.
  .thermal_foldback()
  .sent_ids()
  .sends_matching()
  .frames()
  .measured_period_ms()    The same measurement status_period_ms makes, on the same virtual clock.
  .elapsed()
  .explain()               A readable timeline: what was sent, dropped and delivered.
```

### `sparksim.controller`

One SPARK's state machine, including the state no CAN frame reveals.

```
class SimSpark
  .period_ms()                  Resolved broadcast period, or None when the frame is silent.
  .set_period_ms()
  .enabled_apis()
  .status_0_payload()
  .heartbeat_starved()          True while the enable heartbeat has been absent past the spec window.
  .status_0_payload_sparkmax()
  .status_1_payload_sparkmax()
  .status_1_payload()
  .unique_id_payload()
  .is_sparkmax()
  .generation()                 Frame generation, from firmware version. Not from the product.
  .is_pre25()
  .payload_for()
  .silence_reason()
  .value_of()                   Raw uint32 a read returns: RAM first, then flash, then zero.
  .type_of()                    Parameter type code. 0 is Unused, which is how a device says an id
  .on_request()                 Replies as (absolute delivery time, arb, data). The bus owns the timeline.
  .legacy_type_of()             The pre-25 type tag for one parameter.
  .reboot()                     Volatile config is lost; flash is what comes back.
```

### `sparksim.frames`

SPARK CAN protocol: arbitration maths, payload codecs, constants.

```
arb()                       FRC extended id: device type, manufacturer, api class+index, device id.
class ArbFields
split_arb()
is_spark()
api_of()
dev_of()
base_of()                   The frame base, i.e. the arbitration id with the device field cleared.
generation_for_firmware()   Which frame generation a firmware version broadcasts.
read_pair_arb()             Arbitration base for the READ_PARAMETER frame carrying this parameter.
type_frame_arb()            Arbitration base for the GET_PARAMETER_n_TO_n+15_TYPES frame.
legacy_param_arb()          Arbitration id for one parameter on one device, pre-25 dialect.
legacy_param_id_of()        The parameter id an arbitration id addresses, or None if it is not one.
encode_legacy_param_resp()
legacy_fault_mask()         Mask for these pre-25 fault names. An unknown name raises.
legacy_fault_names()
fault_mask()                Mask for these fault names. An unknown name raises, never returns 0.
warn_mask()
fault_names()
warn_names()
as_fault_mask()             An int mask, a fault name, or an iterable of names.
as_warn_mask()
encode_status_0_sparkmax()  SPARK MAX Periodic Status 0, api 0x060. REV's own description:
encode_status_1_sparkmax()  SPARK MAX Periodic Status 1, api 0x061: velocity, temperature, voltage,
encode_status_0()
decode_status_0()           Independent reference decoder. Tests assert through spark_admin's, not this.
encode_status_1()
decode_status_1()
encode_unique_id()          The 4 serial bytes in the order the device broadcasts them on api 0x2F0.
encode_firmware()
decode_firmware()
encode_param_resp()
encode_param_write()
encode_persist_resp()
encode_persist_request()
encode_set_can_id()
```

### `sparksim.fleet`

rig-flex presets: the eight controllers this suite is written against.

```
serial_for()
spark()
build_fleet()        rig-flex's eight, provisioned to the declared SparkFlex configuration.
build_fleet_pre25()  rig-max's eight, on pre-25 firmware and at this package's boot throttle.
declared_ram()       The declared settings a PROVISIONED firmware-25+ controller holds.
provisioned()        Appendix A: STATUS_1 at 20 ms.
factory()            REV defaults: STATUS_1 at 250 ms, i.e. a controller that lost its config.
```

### `sparksim.clock`

A virtual clock, substituted for the `time` module a driver imports.

```
class VirtualClock  Monotonic virtual seconds. Only `sleep()` and the bus scheduler move it.
  .time()
  .monotonic()
  .perf_counter()
  .sleep()
  .advance_to()     Move to `t`. A target at or before now is a no-op, never an error.
  .advance()
  .patch()          Replace each module's `time` attribute with this clock's functions.
```

## Hardware harness: real bus, gated


### `sparkhw.wire`

What this host is allowed to put on a live SPARK bus, and how it listens.

```
forbidden_frames()         Every frame in `messages` whose base is one this host may never send.
class InjectionRefused     Raised when an injection would emit a frame a controller acts on.
class Sniffer              Everything on the wire while a block runs, with the host's own frames
  .close()
  .from_this_host()        Frames the kernel marked as locally created.
  .from_the_bus()
  .bases_from_this_host()
  .apis_from()
  .explain()               The frames, newest last, for a failure message.
class WireInjector         Failure signatures transmitted onto the live bus from this host.
  .close()
  .check()                 Refuse anything a controller would act on. Raises, never returns False.
  .emit()
  .status_0()              A STATUS_0 payload of this host's choosing on `dev`.
  .status_1()
  .legacy_status_0()       A pre-25 LEGACY_STATUS_0 payload on `dev`, faults and all.
  .unique_id()
  .pdh_status()            A REV Power Distribution Hub frame on a SPARK's id.
  .param_write_response()  The frame another host's provisioning loop leaves on the bus.
  .stream()                Repeat `frames` -- (arb, data) pairs -- every `period_s` in a thread.
  .phantom()               A second controller answering on `dev`: STATUS_0, STATUS_1 and a serial.
  .congestion()            Fill the bus with frames no device on it decodes.
```
