"""Every failure mode in SPARK-FAILURE-CATALOGUE.md, and what this suite does
about it on real hardware.

The catalogue is 353 Chief Delphi threads distilled to 44 numbered failures. A
hardware suite that quietly covered the easy two thirds would read, from its
summary line, exactly like one that covered all of it. So the manifest is the
deliverable and the tests hang off it: every entry carries a disposition, and
`test_catalogue_coverage.py` fails if an entry has neither a test that names it
nor a written reason for not having one.

Dispositions:

  WIRE     the host transmits the failure's own frames onto the live bus. The
           controllers are not written to; stopping the stream ends the failure.
  CONFIG   a reversible parameter write puts a real controller into the state.
           Status 0/1 Period only, never persisted.
  STAGED   a person cuts power, unplugs a connector or reads a meter, and the
           test runs in two stages around the physical act.
  DETECT   cannot be injected here, but the tool has to report it if it is ever
           present, and the test asserts the fleet's current answer.
  REFUSED  this suite will not do it on hardware. `why` says what would break.

REFUSED is not a gap in coverage of the *tool* -- most of these are covered
frame-for-frame in tests/adversarial against the simulator, which is what a
simulator is for. It is a statement about what may be done to a robot that has
to drive tomorrow.

Sources: docs/spark/FAILURE-CATALOGUE.md (parts 1 and 2), REV-Specs
spark-frames-2.1.0, and this fleet's own probe log at
docs/spark/runs/-rig-flex-probe-log.md.
"""
from __future__ import annotations

from typing import NamedTuple

WIRE, CONFIG, STAGED, DETECT, REFUSED = ("wire", "config", "staged", "detect",
                                         "refused")


class Mode(NamedTuple):
    """One catalogue entry and its hardware disposition."""

    id: str
    title: str
    cd: tuple            # Chief Delphi topic ids behind it
    sim: str             # the catalogue's own "can this be simulated" column
    hw: tuple            # one or more dispositions
    tests: tuple         # test function names in tests/hardware that carry it
    why: str             # required when a disposition is REFUSED or DETECT
    sim_tests: tuple = ()  # test names in tests/adversarial that simulate it


def _cd(*ids):
    return tuple(ids)


MODES = (
    # -- A. configuration is written but does not stick ------------------------
    Mode("A1", "Config write not acknowledged; settings silently revert",
         _cd(456184), "YES", (CONFIG,),
         ("test_a_provisioning_burst_is_answered_write_for_write",
          "test_a_write_to_an_id_no_controller_owns_is_reported_as_no_response"),
         ""),
    Mode("A2", "Burn/persist issued too soon after the config writes",
         _cd(432129), "YES", (STAGED,),
         ("test_stage_settle_arm_persists_without_waiting_out_the_ram_commit",
          "test_stage_settle_verify_the_flash_holds_the_value_that_was_written"),
         ""),
    Mode("A3", "Settings reset after persisting, firmware v26",
         _cd(515478), "PART", (STAGED,),
         ("test_stage_settle_verify_the_flash_holds_the_value_that_was_written",),
         "the verify stage IS this failure: a value that persisted cleanly and "
         "was gone after the rail cycle is A3 rather than A2, and only the "
         "settling gap tells them apart"),
    Mode("A4", "Flash wear from persisting on every boot",
         _cd(455171), "NO", (DETECT,),
         ("test_no_start_up_path_commits_parameters_to_flash",
          "test_an_injection_run_spends_no_flash_cycle_unless_it_was_asked_to"),
         "endurance is 1e4 to 1e5 cycles, so reproducing wear would take longer "
         "than the controllers' service life; what is testable is that nothing "
         "persists unasked"),
    Mode("A5", "CAN configuration forgotten on a power cycle",
         _cd(455171, 391976), "PART", (STAGED,),
         ("test_stage_cycle_arm_records_the_bus_before_the_rail_is_cut",
          "test_stage_cycle_verify_the_configuration_came_back"), ""),
    Mode("A6", "Factory reset / recovery mode does not clear the CAN ID",
         _cd(507673), "NO", (REFUSED,), (),
         "reproducing it means sending COMPLETE_FACTORY_RESET, which drops the "
         "CAN ID, Motor Type and Idle Mode of a provisioned controller. The "
         "restore is readable over CAN; what still refuses it "
         "is the cost when the restore fails, and recovery is RHC2 over USB-C",
         ("test_a_factory_reset_does_not_free_the_can_id_it_was_run_to_free", "test_no_reset_frame_is_ever_addressed_by_this_tooling")),
    Mode("A7", "configureAsync() returns kOk before the work is done",
         _cd(), "-", (REFUSED,), (),
         "a REVLib call, not a frame. This driver writes PARAMETER_WRITE and "
         "waits for PARAMETER_WRITE_RESPONSE, so there is no async result to "
         "mistake for a confirmation",
         ("test_a_write_that_is_never_answered_is_not_reported_as_success", "test_no_module_depends_on_revlib")),
    Mode("A8", "hasReset means re-apply the whole configuration, not just clear",
         _cd(480555), "PART", (STAGED,),
         ("test_stage_cycle_verify_the_reset_evidence_is_read_before_it_is_erased",),
         ""),
    Mode("A9", "A BOOL parameter stores a value outside 0/1, permissively",
         _cd(), "YES", (REFUSED,), (),
         "measured on rig-flex id 17 rather than injected here, and "
         "not repeated: writing 2 to a hard-limit polarity releases the interlock "
         "on a robot wired for polarity 1, and this suite does not write an "
         "out-of-range value to an interlock to watch it happen. The driver "
         "refuses any value but 0 or 1 and reads every polarity write back",
         ("test_the_tool_writes_no_value_but_0_and_1",
          "test_a_write_that_reports_success_and_did_not_land_is_refused")),

    # -- B. identity and addressing --------------------------------------------
    Mode("B1", "Duplicate CAN IDs; an id scan sees one device",
         _cd(427780, 426287, 495329, 403172), "YES", (WIRE, STAGED),
         ("test_a_second_serial_on_a_configured_id_is_reported_as_a_duplicate",
          "test_a_twin_that_never_broadcasts_its_serial_is_still_a_duplicate",
          "test_stage_can_id_arm_moves_one_controller_to_a_free_address",
          "test_stage_can_id_verify_says_whether_set_can_id_reached_flash"), ""),
    Mode("B2", "CAN ID 0 is the unconfigured address and can never be enabled",
         _cd(376796), "YES", (WIRE,),
         ("test_a_controller_transmitting_on_id_zero_is_named_as_unconfigured",), ""),
    Mode("B3", "Follower with a lower CAN ID than its leader stutters",
         _cd(378716), "YES", (REFUSED, DETECT),
         ("test_no_controller_on_this_bus_is_in_follower_mode",),
         "injecting it means writing Follower Mode Leader Id, and REVLib 2025 "
         "makes follower mode run even where no user code references the "
         "follower -- a mis-set leader drives a wheel with nothing on the host "
         "holding the reins. The bit is read instead"),
    Mode("B4", "Follower mode polarity changed across a firmware update",
         _cd(363633), "PART", (REFUSED,), (),
         "same write as B3, and reproducing the change needs two firmware "
         "versions on one controller",
         ("test_a_follower_is_reported_with_the_firmware_that_decides_its_polarity",)),
    Mode("B5", "A stuck follower drives a mechanism with no object in your program",
         _cd(), "-", (DETECT,),
         ("test_no_controller_on_this_bus_is_in_follower_mode",),
         "the is_follower bit in STATUS_1 is the whole observable, and it is "
         "already on the wire; what is missing is a reader"),

    # -- C. bus health and traffic ---------------------------------------------
    Mode("C1", "CAN utilisation spiking to 100%",
         _cd(455329), "YES", (WIRE,),
         ("test_congestion_inflates_the_measured_period_of_an_intact_controller",
          "test_a_saturated_bus_is_not_reported_as_a_reverted_controller"), ""),
    Mode("C2", "Controller intermittently drops off the bus",
         _cd(480555), "YES", (CONFIG, STAGED),
         ("test_a_dropout_inside_the_sample_window_is_not_reported_as_reverted",
          "test_stage_unplug_can_the_loss_is_one_finding_and_not_one_per_id"),
         "the config half runs anywhere. The staged half needs a connector to "
         "pull, and rig-flex's SPARK CAN chain is soldered, so on this robot only "
         "the dropout injection is available"),
    Mode("C3", "Periodic Status N timeouts on deliberately disabled frames",
         _cd(432129), "YES", (CONFIG,),
         ("test_a_starved_status_1_is_absent_while_the_controller_still_answers",), ""),
    Mode("C4", "CAN H and L swapped",
         _cd(460286), "NO", (STAGED,),
         ("test_stage_swap_hl_the_bus_is_dead_and_the_tool_says_so",), ""),
    Mode("C5", "WAGO / crimp connectors causing intermittent CAN",
         _cd(480394, 460286), "NO", (STAGED,),
         ("test_stage_unplug_can_the_loss_is_one_finding_and_not_one_per_id",),
         "the electrical cause is a bad conductor and the bus signature is the "
         "same as pulling the connector, which is what the stage does. NOT "
         "AVAILABLE ON rig-flex: its SPARK CAN chain is soldered and cannot be "
         "broken at a controller, so the contiguous-loss signature has no way "
         "to be produced here. The stage is kept for a robot that is "
         "connectorised"),
    Mode("C6", "CAN TX/RX sticky faults from bus contention",
         _cd(426287), "YES", (WIRE,),
         ("test_what_the_congestion_latched_in_sticky_faults_reaches_the_audit",), ""),
    Mode("C7", "Termination is 60 ohm across CANH/CANL with power off",
         _cd(), "-", (STAGED,),
         ("test_stage_termination_the_recorded_resistance_is_two_terminators",), ""),

    # -- D. faults that silence or disable a controller ------------------------
    Mode("D1", "Gate driver fault, the most reported hardware failure",
         _cd(444231, 346981, 454533, 491119), "PART",
         (WIRE, REFUSED),
         ("test_a_broadcast_gate_driver_fault_reaches_the_audit",
          "test_a_faulted_controller_keeps_a_perfect_status_1_cadence"),
         "the physical fault is a dead output stage and ends in an RMA. What is "
         "injectable is its bus signature: the fault bit set in STATUS_1 while "
         "the cadence stays nominal, which is the half the audit is blind to"),
    Mode("D2", "Blinking magenta means no valid signal, not a fault",
         _cd(379472, 347357), "YES", (STAGED,),
         ("test_stage_led_the_recorded_blink_pattern_is_a_known_one",), ""),
    Mode("D3", "Sticky faults latching; the silent bus a Clear Faults frame wakes",
         _cd(), "YES", (STAGED,),
         ("test_stage_cycle_verify_a_silent_bus_is_woken_by_one_frame_per_id",),
         "reproduced on this fleet and not explained by any REV "
         "document; see CORRECTION 1 in the catalogue"),
    Mode("D4", "EEPROM fault raised by a firmware bug",
         _cd(453509), "PART", (WIRE,),
         ("test_an_esc_eeprom_fault_is_told_apart_from_the_warning_of_that_name",), ""),

    # -- E. motor, sensor and mechanical ---------------------------------------
    Mode("E1", "Motor type misconfigured; brushless motor in brushed mode",
         _cd(424550), "PART", (REFUSED,), (),
                  "the injection is a write to Motor Type, and write_param refuses "
         "that parameter through PROTECTED_PARAMS. The value reads back over "
         "CAN on both generations, so a restore can be confirmed. What still "
         "refuses the injection is the cost when a restore fails. A drive "
         "motor left in brushed mode is silent until someone asks it to move",
         ("test_motor_type_is_not_writable_by_this_tooling",)),
    Mode("E2", "Encoder / JST data cable faults",
         _cd(379472, 371676, 400769), "PART", (STAGED,),
         ("test_stage_unplug_encoder_a_sensor_fault_appears_and_reaches_the_audit",),
         ""),
    Mode("E3", "Inconsistent sensor faults traced to a Vortex adapter",
         _cd(456113), "PART", (STAGED,),
         ("test_stage_unplug_encoder_a_sensor_fault_appears_and_reaches_the_audit",),
         "same injection as E2: the data path is interrupted and the sensor "
         "fault bit is the observable"),
    Mode("E4", "Internal encoder resetting to abnormal positions",
         _cd(460577), "PART", (DETECT,),
         ("test_stage_cycle_verify_the_wake_capture_holds_the_first_frames",),
         "the position field is in STATUS_2, which is disabled by default on "
         "this fleet, so the drift itself is off the wire. The wake capture "
         "records the fault-bit storm CD 460577 saw beside it"),
    Mode("E5", "A shorted NEO destroying the SPARK MAX it is wired to",
         _cd(435486, 439040), "NO", (REFUSED,), (),
         "the injection is a motor short. It destroyed three controllers in "
         "succession in the report",
         ("test_a_dark_controller_is_not_advised_to_be_replaced_on_its_own",)),
    Mode("E6", "Flex/Vortex docking screws not fully installed",
         _cd(453509), "NO", (STAGED,),
         ("test_stage_inspect_the_recorded_mechanical_check_is_complete",), ""),
    Mode("E7", "The mode button toggles motor type on a three second press",
         _cd(), "-", (REFUSED,), (),
         "pressing it flips a provisioned controller to brushed. The motor type "
         "reads back over CAN now, and a suite that writes it still risks "
         "leaving a drive motor silent if the restore fails",
         ("test_motor_type_is_not_writable_by_this_tooling",)),

    # -- F. firmware and tooling -----------------------------------------------
    Mode("F1", "Firmware update reports success without updating",
         _cd(341470, 372085), "NO", (DETECT,),
         ("test_every_controller_answers_the_firmware_version_the_baseline_recorded",),
         "reproducing it needs a reflash over USB, so the version is checked "
         "instead through GET_FIRMWARE_VERSION"),
    Mode("F2", "Mixed firmware versions across the bus",
         _cd(456184, 480394), "PART", (DETECT,),
         ("test_the_whole_fleet_runs_one_firmware_version",),
         "same reflash problem as F1; the fleet's uniformity is asserted"),
    Mode("F3", "kS rejected by firmware; hard crash on v26",
         _cd(513879), "PART", (REFUSED,), (),
         "REVLib 2026.0.1 against SPARK MAX 26.1. This driver never writes kS "
         "and never loads REVLib, so there is nothing here to crash",
         ("test_a_rejected_parameter_id_is_surfaced_and_not_read_as_success", "test_no_module_depends_on_revlib")),
    Mode("F4", "Controller reverting to older firmware on its own",
         _cd(400769), "NO", (DETECT,),
         ("test_every_controller_answers_the_firmware_version_the_baseline_recorded",),
         "not injectable; the baseline comparison is what would catch it"),
    Mode("F5", "Default velocity filtering unusable for high-speed control",
         _cd(514567), "PART", (REFUSED,), (),
         "the filter settings are parameters and read back over CAN on both "
         "generations; writing them is what this tooling declines",
         ("test_the_velocity_filter_is_declared_at_revs_default_on_purpose",)),
    Mode("F6", "Firmware 25.0.0 drives utilisation up beside a 24.0.x device",
         _cd(), "-", (DETECT,),
         ("test_the_whole_fleet_runs_one_firmware_version",),
         "the precondition is a mixed-version bus, which this fleet is not; the "
         "uniformity check is what would notice one appearing"),

    # -- G. closed loop and control level --------------------------------------
    Mode("G1", "Conversion factors not applied",
         _cd(396629), "PART", (REFUSED,), (),
         "the conversion factors are parameters and are not readable on 26.1.6",
         ("test_the_conversion_factors_are_never_claimed_to_be_applied",)),
    Mode("G2", "Duty-cycle absolute encoder refresh at 50 Hz, too slow for a PID",
         _cd(438386), "PART", (CONFIG,),
         ("test_the_status_periods_the_audit_calls_unverifiable_are_measurable",),
         ""),
    Mode("G3", "Intermittent input/output mismatch: commanded non-zero, applied zero",
         _cd(477176), "PART", (WIRE,),
         ("test_zero_applied_output_with_current_flowing_is_flagged",),
         "the real failure needs a setpoint, which no tool in this repo may "
         "send. The frame pair it produces -- applied output exactly 0 with "
         "current flowing -- is injected instead"),
    Mode("G4", "Velocity filter default is a 164 ms window with 82 ms of lag",
         _cd(514567), "-", (REFUSED,), (),
                  "a defaults trap and not a fault. The filter settings read back over "
         "CAN on both generations, and F5 above carries the same refusal to "
         "write them",
         ("test_the_velocity_filter_is_declared_at_revs_default_on_purpose",)),

    # -- H. software wearing a wiring bug's symptoms ---------------------------
    Mode("H1", "Two default commands on one subsystem present as CAN sticky faults",
         _cd(426287), "-", (WIRE,),
         ("test_what_the_congestion_latched_in_sticky_faults_reaches_the_audit",),
         "the host-side cause cannot be injected into a controller; the bus "
         "signature is C6's and is injected there"),

    # -- signatures named in part 2 but not numbered ---------------------------
    Mode("SIG-POSITION-SPIKE",
         "Position freezes, jumps, and eleven fault bits set for one loop",
         _cd(460577), "PART", (STAGED,),
         ("test_stage_cycle_verify_the_wake_capture_holds_the_first_frames",),
         ""),
    Mode("SIG-THERMAL",
         "Applied output falls to zero, frames keep flowing, it recovers alone",
         _cd(), "-", (REFUSED,), (),
         "the injection is a motor held in stall until it is hot. That needs a "
         "setpoint and a loaded mechanism, and the recovery signature only "
         "appears after the thermal event has already happened",
         ("test_a_thermal_foldback_that_recovers_alone_leaves_a_readable_trace",)),
)


def by_id(mode_id: str) -> Mode:
    for m in MODES:
        if m.id == mode_id:
            return m
    raise KeyError(mode_id)


def dispositions(kind: str) -> tuple:
    """Every mode carrying one disposition, e.g. dispositions(WIRE)."""
    return tuple(m for m in MODES if kind in m.hw)


def named_tests() -> set:
    """Every test name the manifest claims exists."""
    return {name for m in MODES for name in m.tests}
