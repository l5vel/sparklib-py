# Every mined finding, and what was done with it

58 findings from the mining pass over 1440 ChiefDelphi threads,
732 REV doc pages, 308 WPILib doc pages and 557 GitHub issues.

A finding is only useful once it becomes a test, an injector, a line in the
guide, or an explicit decision not to act. This file is that decision, for
all of them, so none sits unread in a workflow result.

## DONE - injected or cited in code  (6)

- **[vendor-confirmed]** REV's software lead states the SPARK MAX factory default Smart Current Limit is a FLAT 80 A across the entire RPM range, not a taper. The speed-based reduction is opt-in: you enable it by setting limitRPM to 0 in setSmartCurrentLimit(). This corrects two first-pass findings from this same thread, which claimed the default 'scales down with speed' and that rig-flex therefore sits on an interpolated curve whose effective limit at steer speeds is 'well below 80 A'. It is not: with the shipped kSmartCurrentConfig of 10000 RPM (above NEO free speed 5676 and Vortex 6784) the reduction is disabled, so all eight controllers are clamped at a flat 80 A at every speed.
  - bears on: The first pass's leading explanation for the OVERCURRENT latch -- that the effective limit at steer speed is far below 80 A -- is wrong. The limit is 80 A everywhere, so a 107-150 A steer reading is n
  - https://www.chiefdelphi.com/t/350542

- **[vendor-confirmed]** REV identified the root cause of the 2024 SPARK Flex Sensor Fault epidemic as a DROPPED CONFIGURATION MESSAGE: one missed config write, from high bus utilization or poor CAN wiring, leaves the Flex in an invalid internal sensor configuration that latches a fault. Fixed in Flex firmware 24.0.6.
  - bears on: Bears directly on any plan to start writing the Smart Current Stall Limit to eight Flexes at init. On this hardware a dropped config write does not merely fail to apply; REV confirms it can leave the
  - https://www.chiefdelphi.com/t/456113

- **[vendor-confirmed]** REV confirms the SPARK Flex status-frame timeout defect is a Flex-only firmware issue dating to 2024, that REVLib's default CAN validity window was widened to 500 ms as a stopgap specifically to mask it, and that it was fixed in Flex firmware 25.0.2 with REV intending to lower the 500 ms default back down.
  - bears on: The stack rig-flex reads through treats a Flex value as valid for up to 500 ms by default. A repeated, identical 150.00 A across several samples may be one stale frame held valid, not eight independent
  - https://www.chiefdelphi.com/t/480555

- **[reported]** A team running Flex/Vortex swerve reported behaviour consistent with the SPARK Flex not honouring its current limits, together with brownouts too brief to be logged, and stated this was new with Flex and had not happened with NEO plus SPARK MAX in prior seasons. REV's reply in the same thread declined to attribute it to the 24.0.6 sensor-fault fix and asked the team to open a support ticket.
  - bears on: Prior art for a Flex-specific current-limit-adherence complaint on a drivetrain, left unresolved by REV. Weakens any inference that rig-flex's behaviour must be explainable by SPARK MAX-era evidence, an
  - https://www.chiefdelphi.com/t/456113

- **[vendor-confirmed]** REV's software lead states that the Smart Current Limit holds the motor AT the limit value and scales duty cycle instead, so a controller reporting 81-98 A against an 80 A stall limit is showing a working limiter, while a reading of 107-150 A is above what the limiter is supposed to permit at all.
  - bears on: Splits rig-flex's two populations. The drives at 81-98 A are sitting on the limit, which is the limiter doing its job. The steers at 107-150 A are above it, which is either a genuine excursion past the
  - https://www.chiefdelphi.com/t/350542

- **[vendor-confirmed]** REV's own engineer gave the root cause and the fixing firmware version for the Flex sticky-fault episode the first pass recorded only as a library-version correlation: a missed configuration message, caused by high CAN bus utilisation or poor CAN wiring, leaves the Flex in an invalid sensor configuration that latches a fault, and Flex firmware 24.0.6 fixes it.
  - bears on: Establishes, from REV, that a dropped configuration write on a Flex leaves the controller in a state that latches a sticky fault with no physical cause. That is a live hazard for rig-flex, where nothing
  - https://www.chiefdelphi.com/t/456113

## INJECTABLE - outstanding  (16)

- **[reported]** Polling a SPARK's fault and warning bits at 1 Hz produced a random one-second hang in robot code execution, appearing shortly after enable and usually during autonomous. Filed 2025-04-30 against REVLib and still open with no REV response captured.
  - bears on: The act of reading the sticky OVERCURRENT bits off eight controllers. A one-second loop stall on a swerve base is itself a large motion transient, so a fault-polling health monitor can manufacture the
  - https://github.com/REVrobotics/REV-Software-Binaries/issues/22

- **[reported]** On WPILib 2026.2.1 with REVLib 2026.0.1 and one SPARK Flex on the bus, a Restart Robot Code leaves the CAN bus inaccessible with flat utilisation on every other restart; a second restart clears it. The reporter reproduced the same alternating pattern on a roboRIO 1 carrying only a PDP.
  - bears on: Whether a fault latched after driving reflects the drive or the restart that preceded it. This gives a full version stack in which the bus comes up dead on alternate code restarts, so faults collected
  - https://github.com/wpilibsuite/allwpilib/issues/8634

- **[vendor-confirmed]** The roboRIO brownout is a three-stage scheme, and 6.8 V is NOT the disable threshold. 6.8 V is only where the 6V PWM rail starts to sag; output disable happens at 6.3 V (fixed on roboRIO 1.0, 6.75 V default on roboRIO 2.0), recovery is above 7.5 V, and full blackout is 4.5 V with reboot above 4.65 V.
  - bears on: SPARK-FORUM-FINDINGS.md states 'The roboRIO brownout threshold is 6.8 V with recovery at 7.5 V'. That pairs a stage-1 trigger with a stage-2 recovery. On rig-flex the number that matters for 'did the ro
  - https://raw.githubusercontent.com/wpilibsuite/frc-docs/main/source/docs/software/systemcore-info/roborio-brownouts.rst

- **[vendor-confirmed]** At brownout stage 2 the roboRIO does not merely stop commanding: it sends CAN motor controllers an explicit disable command, alongside disabling PWM, the 6V/5V/3.3V user rails, relays and pneumatics.
  - bears on: Separates two failure pictures on rig-flex. A robot-wide sag arrives at each SPARK as a commanded disable from the host; eight controllers latching sticky OVERCURRENT with no host-side disable event poi
  - https://raw.githubusercontent.com/wpilibsuite/frc-docs/main/source/docs/software/systemcore-info/roborio-brownouts.rst

- **[vendor-confirmed]** The FIRST CAN spec names the frame behind that disable: a zero-length broadcast on arbitration ID 0, which every actuator node must act on immediately, and it is the only broadcast the robot controller currently sends.
  - bears on: Gives the exact bus signature to look for in a rig-flex candump when deciding whether the eight SPARKs were commanded off or faulted off. arbID 0 with DLC 0 is the disable; its absence means nothing dis
  - https://raw.githubusercontent.com/wpilibsuite/frc-docs/main/source/docs/software/can-devices/can-addressing.rst

- **[vendor-confirmed]** The disable broadcast is always transmitted as a classic CAN 2.0 frame and never as a CAN FD frame, and it is forwarded to every downstream bus.
  - bears on: rig-flex runs its SPARK bus through an FD-capable interface. A filter or socket configured only for FD frames would silently miss the one frame that must always be honoured, so the disable path has to b
  - https://raw.githubusercontent.com/wpilibsuite/frc-docs/main/source/docs/software/can-devices/can-addressing.rst

- **[vendor-confirmed]** The robot controller emits a universal CAN heartbeat every 20 ms at full CAN ID 0x01011840 carrying an eight-byte bitfield, and the spec sets the enable rule from it: the System watchdog bit enables motor controllers, and 100 ms of silence means devices must behave as if the robot were disabled.
  - bears on: rig-flex drives eight SPARKs from a Linux host with no roboRIO. This is the documented contract for keeping them enabled and the documented deadline at which they drop out, which sets the maximum tolera
  - https://raw.githubusercontent.com/wpilibsuite/frc-docs/main/source/docs/software/can-devices/can-addressing.rst

- **[vendor-confirmed]** The PDH latches a per-channel record in hardware: a red blinking channel LED means a sticky fault on that channel from a tripped breaker or fuse, and the hub's own status LED has a distinct Orange/Magenta Blinking pattern for Device Over Current.
  - bears on: Gives a software-free check on 'the breakers never tripped'. If rig-flex's distribution is a PDH, the channel LEDs hold the answer without any code; if it is a CTRE PDP, the documented LED set has no pe
  - https://raw.githubusercontent.com/wpilibsuite/frc-docs/main/source/docs/hardware/hardware-basics/status-lights-ref.rst

- **[vendor-confirmed]** REV states that SPARK MAX velocity readings carry a fixed 112 ms measurement delay from hall-sensor filtering, and that at the time the filtering parameters were not configurable, so the delay could not be reduced in firmware.
  - bears on: Calibrates how much smoothing REV applies to what it publishes over CAN, and establishes that a filter's parameters can be inaccessible. Alongside the first pass's 65.75 ms Flex quadrature figure, it
  - https://www.chiefdelphi.com/t/400769

- **[vendor-confirmed]** The SPARK carries a SECOND factory current threshold that nobody has been looking at: kCurrentChop, parameter ID 11, defaults to 115 A and hard-disables the motor driver for kCurrentChopCycles PWM periods whenever the half-bridge detects it. rig-flex's steer readings of 107-150 A straddle exactly this threshold, while the 80 A Smart Current Stall Limit sits well below it. A latched overcurrent bit on a steer is at least as consistent with the 115 A chop tripping as with the 80 A smart limit, and the two are separate parameters with separate defaults.
  - bears on: Which factory default actually trips the sticky OVERCURRENT on the steers. The first pass treated the 80/20/10000 smart-limit triple as the whole untouched configuration; kCurrentChop at 115 A is a th
  - https://docs.revrobotics.com/brushless/spark-max/parameters

- **[vendor-confirmed]** REVLib 2026.0.0 removed the automatic clearFaults() call that previous versions issued when a SparkFlex or SparkMax object was constructed. On 2024/2025 REVLib every code deploy silently wiped the sticky words; from 2026.0.0 they persist until someone calls clearFaults explicitly.
  - bears on: Why all eight controllers now show a sticky OVERCURRENT where earlier seasons appeared clean. This is a host-library behaviour change, not a change in the robot's current draw, and it means a sticky b
  - https://docs.revrobotics.com/revlib/install/changelog

- **[vendor-confirmed]** REV documents that SPARK sticky fault bits clear on a power cycle as well as on a Clear Faults command, and that the Motor Current field in Periodic Status 1 is a 12-bit fixed-point value carrying the raw phase current of the motor.
  - bears on: Two things at once. First, it is the vendor's own confirmation that the current field decoded off the gs_usb bus is 12 bits wide and is phase current, which is what makes a 150.00 A reading suspect as
  - https://docs.revrobotics.com/brushless/spark-max/control-interfaces

- **[vendor-confirmed]** REVLib 2025.0.0 split faults and warnings into separate objects and added hasActiveWarning() and hasStickyWarning(), which is the vendor confirmation that overcurrent moved out of the fault word and into a warnings word in the 2025 and later API.
  - bears on: Decoding raw words off the gs_usb bus. The first pass established that in the 2024 FaultID enum bit 11 decoded to Overcurrent and then wrote 'Confirm against the 26.x field layout, since 2025+ moved o
  - https://docs.revrobotics.com/revlib/install/changelog

- **[vendor-confirmed]** Neither the SPARK Flex nor the SPARK MAX has an LED blink code for overcurrent. REV's complete Fault Conditions table for the Flex lists 12V Missing, Sensor Fault, Gate Driver Fault, CAN Fault, Temperature Cutoff Fault and Corrupt Firmware, and the MAX table is the same minus the temperature cutoff.
  - bears on: How much weight the eight latched warnings deserve. REV does not surface overcurrent on the controller at all, which is consistent with it being a warning bit rather than a condition the controller tr
  - https://docs.revrobotics.com/brushless/spark-flex/status-led

- **[reported]** A team measured a NEO Vortex free-spinning on a SPARK Flex through the REV Hardware Client and got 20 A where the motor should draw about 0.2 A, alongside a velocity of 350,000 RPM. The current was wrong by a factor of 100, the same laptop had read correctly two days earlier, and the suspected cause is RHC1 being used against parameters that changed in the 2026 firmware.
  - bears on: The most direct precedent in the corpus for rig-flex's implausible readings: a client-side scale error on exactly this hardware, giving a Vortex-on-Flex current reading two orders of magnitude too high
  - https://www.chiefdelphi.com/t/515158

- **[reported]** On a swerve steering axis, pushing the profiled-PID acceleration constraint to its maximum makes the SPARK flag a current fault intermittently, and backing the acceleration off removes it.
  - bears on: A cheap, testable cause for steer-only overcurrent that costs nothing to check: the commanded azimuth acceleration, not a mechanical bind and not the current limit. It lines up with REV's own remedy i
  - https://www.chiefdelphi.com/t/397109

## REFERENCE - in the guide, no test to write  (23)

- **[reported]** REVLib caps status signal periods at 1000 ms without documenting the limit, and offers no way to read back which status signals are enabled on a given Spark. This driver does not inherit that gap on firmware 25+: the period parameters 159, 161, 163-165, 199 and 224 read back over READ_PARAMETER. On pre-25 they refuse a read and the cadence on the wire is the only instrument.
  - bears on: Verifying what the eight controllers are actually reporting. The same issue states there is no REVLib method to get which status signals are enabled on a Spark, so a claim that the current telemetry a
  - https://github.com/REVrobotics/REV-Software-Binaries/issues/19

- **[reported]** SPARK MAX firmware 1.5.2 running against the 1.4.x libraries crashed robot code on deploy with a driver-loading error, a straight firmware-to-library version mismatch.
  - bears on: The version-matrix discipline for rig-flex. REV has shipped at least one firmware generation whose mismatch with the library is a hard crash rather than a degraded read, which is the failure mode to exp
  - https://github.com/REVrobotics/SPARK-MAX-Examples/issues/13

- **[vendor-confirmed]** The spec makes enable provenance a requirement on the device, not on the host: any CAN node that drives an actuator must verify both that the robot is enabled and that commands came from the main robot controller.
  - bears on: Explains why a SPARK on rig-flex can sit healthy on the bus and still refuse to actuate, and why the enable evidence the Linux host sends is part of the motion path rather than a convenience. Pairs with
  - https://raw.githubusercontent.com/wpilibsuite/frc-docs/main/source/docs/software/can-devices/can-addressing.rst

- **[vendor-confirmed]** WPILib puts a hard ceiling on what the pack can deliver: a single 12 V 18 Ah SLA battery briefly supplies over 180 A and arcs over 500 A when fully charged.
  - bears on: Falsifies any supply-side reading of the rig-flex telemetry by arithmetic. Eight controllers at 107-150 A would be 850 to 1000 A of input current, roughly double a dead-short arc, so the SPARK numbers c
  - https://raw.githubusercontent.com/wpilibsuite/frc-docs/main/source/docs/hardware/hardware-basics/robot-battery.rst

- **[vendor-confirmed]** WPILib's own power budget worksheet sets a sustained ceiling of 180 A for the whole robot and notes that teams commonly gear a drivetrain to slip its wheels at 40-50 A per motor, which already consumes or exceeds that budget on four motors.
  - bears on: rig-flex runs eight controllers whose steers report 107-150 A and drives 81-98 A against REV's untouched 80 A default. Measured against the official budget the fleet is far past 180 A if any of those we
  - https://raw.githubusercontent.com/wpilibsuite/frc-docs/main/source/docs/software/systemcore-info/roborio-brownouts.rst

- **[corroborated]** The official hardware overview states that the rating stamped on a branch breaker is a continuous-current rating and that temporary peaks can be considerably higher, and it says this for both the Snap Action MX5/VB3 breakers and the REV ATO breakers, naming the three datasheets that carry the actual trip curves.
  - bears on: Kills the inference that untripped 40 A breakers bound the current. It also names where to get the real answer: the MX5 and VB3 spec sheets and REV-11-1860-1863-DS.pdf, rather than another forum estim
  - https://raw.githubusercontent.com/wpilibsuite/frc-docs/main/source/docs/controls-overviews/control-system-hardware.rst

- **[corroborated]** WPILib documents no current limiting whatsoever. Across all 308 wpilib__* files the strings 'stator', 'supply current' and 'current limit' never appear in a motor-controller sense; the only current WPILib itself exposes is the PDP/PDH per-channel draw, and the motor-controller article hands every CAN feature to the vendor libraries.
  - bears on: Settles where the supply-versus-stator vocabulary comes from. It is CTRE's, not the official control system's, so a SPARK reading has no official counterpart to compare against except the PDP/PDH chan
  - https://raw.githubusercontent.com/wpilibsuite/frc-docs/main/source/docs/software/can-devices/power-distribution-module.rst

- **[vendor-confirmed]** The per-channel current the Driver Station pulls off the PDP/PDH still has no viewer: WPILib says one is under development and tells teams to log the PowerDistribution values themselves and plot them in AdvantageScope.
  - bears on: Means the claim that rig-flex's 40 A breakers never tripped rests on no current record unless someone logged channel current deliberately. If channel current is not being logged, the supply-side half of
  - https://raw.githubusercontent.com/wpilibsuite/frc-docs/main/source/docs/software/systemcore-info/roborio-brownouts.rst

- **[vendor-confirmed]** The official battery thresholds are tighter than the forum consensus: ideal internal resistance below 0.015 ohm, manufacturer specification about 0.011 ohm, and retire above 0.020 ohm.
  - bears on: Sets the pass/fail for rig-flex's pack when deciding whether observed sag is the battery or the wiring. SPARK-FORUM-FINDINGS.md carries a forum spread of 0.013 target and 0.017 preferred; the documented
  - https://raw.githubusercontent.com/wpilibsuite/frc-docs/main/source/docs/hardware/hardware-basics/robot-battery.rst

- **[reported]** WPILib's own telemetry example teaches the wrong label for motor-controller current: its custom-logger sample declares a vendor motor with getInputCurrent() and publishes it as 'Input Current (A)'.
  - bears on: A dashboard built from the official example will label a SPARK's winding current as input current, which is exactly the confusion behind reading 107-150 A against a 40 A breaker. Anything rig-flex logs
  - https://raw.githubusercontent.com/wpilibsuite/frc-docs/main/source/docs/software/telemetry/robot-telemetry-with-annotations.rst

- **[corroborated]** WPILib states that the SPARK MAX applies a 40-tap FIR filter with 19.5 ms of delay and sends status frames every 20 ms by default.
  - bears on: Sets the sampling rate of anything read off a SPARK over CAN, including the current field. A default 50 Hz status frame cannot resolve a shorter current transient, so a peg at the field's full scale s
  - https://raw.githubusercontent.com/wpilibsuite/frc-docs/main/source/docs/software/advanced-controls/state-space/state-space-intro.rst

- **[vendor-confirmed]** REV states directly that the default output-current limit alone is sufficient to keep a 40 A auto-reset breaker from tripping, because the breaker's reaction time is slow relative to the PDP's measurement bandwidth. An untripped breaker under the 80 A factory default is therefore the designed outcome, not an anomaly.
  - bears on: Closes the 'why have the 40 A CTRE breakers never tripped' half of the live question with a direct vendor statement rather than an inference from duty cycle: REV designed the 80 A default so that it w
  - https://www.chiefdelphi.com/t/354333

- **[vendor-confirmed]** REV's president states the SPARK MAX limits and reports OUTPUT current because the hardware directly measures phase currents, rather than deriving anything from total input current. This is a measurement-path statement, not just a units statement: the number on the CAN bus comes off a phase shunt.
  - bears on: Establishes that the 107-150 A steer figures come from a phase-side sensor, so no firmware model or estimator sits between the shunt and the reported value. Rules out one class of explanation for the
  - https://www.chiefdelphi.com/t/337013

- **[vendor-confirmed]** REV states the reason the SPARK cannot offer an input-current limit natively: the current sensor is physically placed on the output side, so any input limit would have to be derived from the output measurement.
  - bears on: Explains why no supply-side limit exists on any of the eight SPARK Flex controllers, and why breaker protection on this fleet has to come from the duty-cycle relationship rather than from a second lim
  - https://www.chiefdelphi.com/t/354333

- **[reported]** REV gave a team the explicit expression for backing supply current out of SPARK telemetry -- inputCurrent = getOutputCurrent() * getAppliedOutput() -- and told them input-current limiting has to be implemented on the roboRIO by clamping the max output duty cycle. Two Will Toth emails are quoted verbatim in the post.
  - bears on: Gives the exact two signals to log together on rig-flex. Logging getOutputCurrent() without getAppliedOutput() on the same sample makes the 107-150 A numbers uninterpretable; with both, the breaker-side
  - https://www.chiefdelphi.com/t/471083

- **[vendor-confirmed]** CTRE's engineer gives a bench test that settles the input-vs-output current question without trusting either controller's telemetry: clamp an ammeter on a motor phase lead and on the controller input, run at 10 percent output, and the output reads ten times the input.
  - bears on: An independent physical check for rig-flex that does not depend on the SPARK's own reading or the PDH channel telemetry -- the two instruments currently in dispute. Also anchors the 10x ratio at the ~10
  - https://www.chiefdelphi.com/t/401460

- **[corroborated]** The SPARK Flex measures current on all three motor phases, which the SPARK MAX did not; REV's own Flex overview lists '3-phase current sensing' among the features that improve on the SPARK MAX foundation. A second team independently states the MAX measured current in only one of the three phases.
  - bears on: The first pass's strongest artifact precedent for an implausible pegged current -- three teams seeing SPARK MAX 'Output Current' stuck at 72, 85 and 125 A -- came from single-phase sensing hardware. T
  - https://www.chiefdelphi.com/t/442595

- **[vendor-confirmed]** REV deliberately ships the SPARK MAX with no hard protective cutoff, on the grounds that it drives many different brushed and brushless motors, and directs teams to the locked-rotor thermal data and to starting from low current limits instead. The vendor's substitute for a cutoff is the current limit itself.
  - bears on: Said in answer to a request for a motor-temperature cutoff, so it is about thermal protection specifically. It means the fleet's only motor protection is the Smart Current Limit that the repo never wr
  - https://www.chiefdelphi.com/t/409457

- **[vendor-confirmed]** REV's software lead states directly that an output-side current limit is sufficient to prevent breaker trips, that input current is never higher than output current, and that the 40 A breaker is too slow to trip even at the default output limit.
  - bears on: The live question exactly: whether never-tripping 40 A breakers contradict a latched OVERCURRENT. REV says they do not, and that the default limit is designed to coexist with an untripped 40 A breaker
  - https://www.chiefdelphi.com/t/354333

- **[vendor-confirmed]** REV's published SPARK Flex specification rates the controller at 60 A continuous output current and 100 A peak for a 2 second surge. The factory 80 A Smart Current Stall Limit therefore sits above the controller's own continuous rating, and the 107-150 A steer readings sit above its 2-second peak rating.
  - bears on: Whether 107-150 A is an operating point the Flex is rated for. It is not. The first pass reached the same conclusion only for the SPARK MAX generation and explicitly hedged 'on the MAX generation at l
  - https://docs.revrobotics.com/brushless/spark-flex/specs

- **[vendor-confirmed]** REV's own documentation names 80 A as the recommended Smart Current Limit for a NEO Vortex on a SPARK Flex, and the Flex getting-started page attaches a warranty warning to setting a limit outside the suggested range. For this motor and controller pairing the factory default is the vendor's recommendation, not merely an untuned leftover.
  - bears on: Reverses the first pass's reading. It recorded REV recommending 40-50 A for a NEO and framed the untouched 80 A as sitting above REV's own advice. For a Vortex behind a Flex, 80 A IS the published adv
  - https://docs.revrobotics.com/brushless/spark-flex/gs/make-it-spin

- **[vendor-confirmed]** REV publishes the conversion between the current the SPARK reports and the current the breaker carries, as a one-line formula, on the locked-rotor testing page. It also publishes locked-rotor time-to-failure data for the NEO V1, V1.1 and 550 but none for the NEO Vortex, while telling teams to choose the limit from that data.
  - bears on: Turns the 107-150 A steer readings into a breaker-side number that can be checked against the 40 A CTRE breakers, using data rig-flex already logs. A steer holding azimuth runs at low duty cycle, so a l
  - https://docs.revrobotics.com/brushless/neo/locked-rotor-testing

- **[corroborated]** A team quoted two 2019 emails from REV's Will Toth giving the exact arithmetic for converting a SPARK reading to breaker-side current, and stated that the SPARK MAX measures one phase while the SPARK Flex has current sensors on all three phases.
  - bears on: Gives rig-flex a computable input current from two fields it already reads, and matches REV's own published formula word for word. The three-phase sensing difference is a candidate mechanism for the Fle
  - https://www.chiefdelphi.com/t/471083

## NOT OURS - a defect in other software  (13)

- **[vendor-confirmed]** REV published a compatibility notice on its own CANBridge repository declaring it incompatible with 2026 and higher SPARK firmware. CANBridge is the USB-CAN layer behind the REV Hardware Client and node-can-bridge, so any bench tool built on it is out of contract against a 2026-firmware SPARK.
  - bears on: Any bench read of the eight controllers' Smart Current Stall Limit or sticky fault bits over a REV USB-CAN path. If the controllers carry 2026 firmware and the reading tool sits on CANBridge, the vend
  - https://github.com/REVrobotics/CANBridge/pull/53

- **[corroborated]** The status code returned by CANBridge's sendCANMessage() is not the status of the message just sent. It is whatever the sending thread last recorded, i.e. the result of some earlier message. The issue is open and unfixed.
  - bears on: The premise that a config write can be verified by its return code. If rig-flex ever starts writing the Smart Current Stall Limit through a REV USB-CAN path and trusts the returned status, a silently dr
  - https://github.com/REVrobotics/CANBridge/issues/39

- **[vendor-confirmed]** Before the 2023-11-10 fix, CANBridge's ReceiveMessage() returned the most recent matching frame only when the mask was all ones. With any narrower mask it returned the newest frame for whichever fully-matching arbitration ID it happened to iterate to first.
  - bears on: Reading SPARK periodic status by masked arbitration ID on a bus with eight controllers. Under the pre-fix behaviour a per-device current or fault read taken through a mask can return another device's
  - https://github.com/REVrobotics/CANBridge/pull/22

- **[corroborated]** CANBridge's CANMessage allowed m_dataSize to exceed the 8-byte data array, and the Candle WinUSB path assumes frame.data is at least as large as frame.can_dlc even though DLC values from 1001 to 1111 encode 12 to 64 bytes. The hardening PR is open and does not compile.
  - bears on: Whether the 150.00 A readings are a measurement or a decode. 150.00 A is exactly the 12-bit field's full scale, and REV's own CAN bridge has an open, unfixed out-of-bounds read in the path that hands
  - https://github.com/REVrobotics/CANBridge/pull/40

- **[vendor-confirmed]** CANBridge's periodic-frame scheduling was broken in three separate, separately-fixed ways: repeating frames did not actually repeat until 2024-03-25, setting the interval to -1 cleared every scheduled frame instead of the one named (same day), and a cancelled repeating frame was not removed immediately until 2024-09-17.
  - bears on: The enable heartbeat and periodic setpoint frames that keep a SPARK out of its disabled state. Cancelling one repeating frame wiped all of them, which drops every controller's periodic traffic at once
  - https://github.com/REVrobotics/CANBridge/pull/25

- **[reported]** REVLib's C++ SparkSim::iterate was wrong in the current-limit path specifically: the return code of c_SIM_Spark_GetSimCurrentLimitOutput was assigned into the float appliedOutput, silently overwriting the out-parameter, so iterate always behaved as though the setpoint were zero. The Java implementation was unaffected because it is a re-implementation rather than a wrapper.
  - bears on: Any attempt to reason about what the 80 A Smart Current Stall Limit does to applied output by simulating it. REVLib's own current-limit simulation returned an error code cast to a float, and the Java
  - https://github.com/REVrobotics/REV-Software-Binaries/issues/13

- **[reported]** REVLib 2026.0.0-alpha-1 regressed the C++ SparkSim getters, which return zero in Release builds while working in Debug. The same test passes in both modes on REVLib v2025.0.3.
  - bears on: Choosing a REVLib version to validate against. A named regression between 2025.0.3 and 2026.0.0-alpha-1 that only appears in Release means a green Debug test suite proves nothing about the shipped bui
  - https://github.com/REVrobotics/REV-Software-Binaries/issues/24

- **[reported]** REV's own SPARK MAX sample code calls restoreFactoryDefaults() after initialising the controller and setting parameters, and the issue states this ordering was causing major problems for teams who set all parameters in one place. Filed 2023-05-08 and still open.
  - bears on: How a config path that does write the Smart Current Stall Limit should be ordered. The first pass established from ChiefDelphi that kResetSafeParameters restores 80 A. This adds that REV's shipped exa
  - https://github.com/REVrobotics/SPARK-MAX-Examples/issues/32

- **[reported]** A contributor to REV's official MAXSwerve C++ template found that the wrong current limit was being written to the steering motor controllers, and destroyed a NEO 550 before finding it.
  - bears on: The steer channels, which are the ones reporting 107-150 A on rig-flex against drives at 81-98 A. A swerve template writing the drive current limit to the steer controllers is a documented defect in REV
  - https://github.com/REVrobotics/MAXSwerve-Cpp-Template/pull/14

- **[reported]** A commit in REV's MAXSwerve Java template changed the driving velocity feedforward from 1/kDriveWheelFreeSpeedRps to nominalVoltage/kDriveWheelFreeSpeedRps with nominalVoltage = 12.0, making the feedforward twelve times too high. Filed 2026-03-04 against the current template and still open.
  - bears on: A mechanism that latches OVERCURRENT with no breaker trip. A drive feedforward 12x too high saturates the closed-loop output at trivial stick deflection, which is high stator current at low speed and
  - https://github.com/REVrobotics/MAXSwerve-Java-Template/issues/41

- **[reported]** WPILib's motor simulations multiplied signed motor current by Math.signum(V) rather than by duty cycle, so they reported motor-current magnitude carrying a power-direction sign instead of the battery-side current a PDP or breaker model sees. The correction is an open pull request as of 2026-07-28.
  - bears on: Any current budget for this swerve base that came out of a WPILib simulation. The shipped sims report the same motor-side quantity the SPARK reports, not the supply-side quantity the 40 A breaker carr
  - https://github.com/wpilibsuite/allwpilib/pull/9172

- **[reported]** Constructing a REV Pneumatic Hub after a CANSparkMax with no CAN bus attached crashed robot code on WPILib 2022.3.1 with HAL status -35007, CAN Output Buffer Full. Reversing the construction order, or removing either device, avoided the crash.
  - bears on: Reading CAN output buffer errors during bring-up. The error text names a missing device, but the actual trigger was construction order with the bus absent, so the message points away from the real cau
  - https://github.com/wpilibsuite/allwpilib/issues/4002

- **[corroborated]** Dropped REVLib configuration writes are a documented, multi-team failure mode on SPARK controllers. burnFlash() is an undocumented blocking and 'deafening' call; REV's 200 ms pre-delay advice exists only in CD posts and not in the javadocs or docs site; REV's own MAXSwerve Java template omits it; and config setters do not retry on timeout. One team calls dropped configs their number one reliability issue of the season, and a second reports needing 500 ms delays plus five retries and still losing a setting on one of four controllers.
  - bears on: The premise 'nothing in the robot code ever writes the current limit' has a corollary worth testing: even if it did, a naive write would not reliably land on all eight. Verify the eight controllers re
  - https://www.chiefdelphi.com/t/442216
