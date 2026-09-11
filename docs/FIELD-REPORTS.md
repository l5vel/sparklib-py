# SPARK findings from ChiefDelphi, with sources

WITHDRAWN: two findings in this file about the Smart Current Limit
tapering with speed are wrong and must not be used.

  - "the current limit is interpolated between stall and free" -- true as a
    mechanism, but OFF at factory defaults. kSmartCurrentConfig defaults to
    10000 RPM and REV's docs say a value above free speed disables the taper.
    Every motor on this fleet is below 10000 RPM free speed.
  - "a linear taper from 80 A at stall down to 0 A at free speed" -- the taper
    ends at the free limit (20 A), not 0 A, and does not run by default.

Both trace to one forum poster who withdrew the claim himself once REV corrected
him, at a post past the 20-post cap the first harvest stopped at. The reasoning
built on them -- that a hidden speed-dependent limit explains an overcurrent
warning at 107-150 A -- is dead. Nothing writes setSmartCurrentLimit on this
fleet, so all eight controllers hold a flat 80 A at every speed.

See GUIDE.md section 4, which now carries the vendor sources.

CORRECTED: one finding in this file placed overcurrent at bit 11
of the pre-25 16-bit fault word. It is bit 1; bit 11 is kOtherFault. The line
is corrected where it sits, under `reported`, and the whole sixteen-bit order
is written out there. Sources: REVLib 2024.2.4's own FaultID enum, carried as
provenance.pre25.fault_bit_order and decoded by
admin._LEGACY_FAULT_BITS.


Mined from a corpus of 2350 harvested topics and 1440 fetched
thread bodies under `the Chief Delphi corpus (chiefdelphi.com)`. Graded by how well the corpus
supports each claim:

    vendor-confirmed  REV or CTRE staff said it
    corroborated      several teams independently, or a measurement
    reported          one team says so

Read the grade before acting on a line. Nothing here is a substitute for a
measurement on the robot in front of you.

## vendor-confirmed (42)

- **REV set the SPARK MAX factory default Smart Current Limit to 80 A deliberately, as a drivetrain-appropriate value that protects a full-size NEO without costing much performance.**
  - bears on: Whether the untouched 80 A Smart Current Stall Limit on all eight controllers is a defect or an intended default; it is intended, and is REV's own drivetrain recommendation.
  - https://www.chiefdelphi.com/t/337013

- **REV states the 80 A smart current limit is the factory default and that REV considers it appropriate for a drivetrain.**
  - bears on: The 80 A Smart Current Stall Limit found on all eight controllers is REV's deliberate drivetrain default, not a misconfiguration -- but it was chosen for drive motors, not azimuth. Greg_Needel is REV.
  - https://www.chiefdelphi.com/t/337013

- **REV staff state that the SPARK's current limit and reading are on OUTPUT (motor winding) current, that this differs from the PDP-reported input current, and that the gap is much larger for brushless than for brushed motors because winding resistance is low.**
  - bears on: Whether the 107-150 A the SPARKs report on rig-flex is the same quantity the 40 A breaker carries. It is not: the SPARK reads motor-side, the breaker carries input-side.
  - https://www.chiefdelphi.com/t/350003

- **REV confirms the SPARK current limit acts on winding (output) current, that the gap between it and the PDP-reported input current is much larger for brushless than brushed motors because winding resistance is lower, and that REV enables the limit by default deliberately.**
  - bears on: Interpreting the OVERCURRENT sticky warning against the never-tripped 40 A breakers; the two instruments measure different currents by design.
  - https://www.chiefdelphi.com/t/350003

- **The SPARK's Smart Current Limit and its reported current are MOTOR (stator/winding) current, not the input current the PDP/PDH breaker carries, and REV enables the limit by default precisely because the two diverge so far on a brushless motor.**
  - bears on: The core premise of the live question: SPARK reports motor current, breaker carries input current. REV staff (Will_Toth) confirms both the distinction and its magnitude.
  - https://www.chiefdelphi.com/t/350003

- **The SPARK MAX/Flex current limit and current report are MOTOR (stator) current, not the input current the breaker carries; REV says the gap is much larger than on brushed motors because NEO winding resistance is so low.**
  - bears on: Why 107-150 A SPARK readings coexist with 40 A CTRE breakers that have never tripped -- the two instruments measure different currents. Will_Toth is REV staff.
  - https://www.chiefdelphi.com/t/350003

- **REV states directly that the SPARK MAX reports OUTPUT (motor) current while the PDP reports input current, and that the two will differ.**
  - bears on: The core of the rig-flex puzzle -- 107-150 A reported by the SPARK against 40 A breakers that never trip. Vendor confirmation that these are different quantities.
  - https://www.chiefdelphi.com/t/350542

- **REV confirms that an unsaved current limit reverts to the factory default after a brownout or reset, so a limit set in code without burnFlash does not survive.**
  - bears on: If rig-flex ever starts writing a Smart Current Limit, it has to be persisted, or a mid-match localized brownout puts all eight controllers back on the 80 A default silently.
  - https://www.chiefdelphi.com/t/354333

- **REV support: a limit set but not saved to flash reverts to default after a brownout.**
  - bears on: Whether a client-set limit could survive; it does not unless persisted, and a brownout reverts it.
  - https://www.chiefdelphi.com/t/354333

- **A brownout wipes an unsaved SPARK MAX current limit back to factory default; REV instructs teams to call BurnFlash after setting parameters for this reason.**
  - bears on: The repo never writes the Smart Current Stall Limit, so every controller is at REV's 80 A default -- and even if it were written at runtime, a sag event would revert it unless burned. dyanoshak is REV staff.
  - https://www.chiefdelphi.com/t/354333

- **REV staff repeat the same distinction in a second thread, naming the PDP log value as input current and the SPARK value as winding current.**
  - bears on: Independent REV restatement; means rig-flex telemetry cannot be compared to a breaker rating without MULTIPLYING the reported motor current by duty cycle, which is the conversion the 408303 entries below work through: input current = motor current x duty cycle.
  - https://www.chiefdelphi.com/t/362484

- **REV attributes SPARK MAX Gate Driver Faults to a localized brownout at the controller from a sudden load change, and prescribes adjusting the Smart Current Limit or adding a 10-50 ms ramp rate to cut the instantaneous voltage drop.**
  - bears on: The sticky-fault-after-driving pattern on all eight controllers. REV's own remedy for a sag-induced sticky fault is the Smart Current Limit plus a short ramp -- the exact parameter the repo never writes. dyanoshak is REV staff.
  - https://www.chiefdelphi.com/t/371929

- **REV confirms the 40 A auto-reset breakers are thermal and pass much more than 40 A before tripping, so an untripped breaker is not evidence that current is under 40 A.**
  - bears on: The inference 'breakers never tripped, so draw is fine' -- it is not a valid inference.
  - https://www.chiefdelphi.com/t/374688

- **CTRE staff answer directly that supply current equals the current on the input side of the controller and is the quantity to limit for breaker protection; the same thread carries a worked case where 20 A supply at 50 percent output equals 40 A stator.**
  - bears on: Settles which quantity a breaker sees. The SPARK's Smart Current Stall Limit is not a breaker-protection setting.
  - https://www.chiefdelphi.com/t/374780

- **CTRE staff state that supply current is identically the PDP channel current, and lay out the division of labour: stator limit for motor heat and acceleration, supply limit for breaker trips.**
  - bears on: Independent CTRE confirmation that breaker current == PDP/PDH channel current == supply current, not the controller's reported motor current.
  - https://www.chiefdelphi.com/t/374780

- **CTRE states that the PDP/PDH channel current is the SUPPLY (input) current, and that supply current limiting -- not stator/motor current limiting -- is what prevents breaker trips.**
  - bears on: Whether the 80 A SPARK Smart Current Stall Limit has any bearing on the 40 A CTRE breakers; it does not, because the SPARK limit is a motor-side limit and the breaker carries input current.
  - https://www.chiefdelphi.com/t/374780

- **CTRE draws the mapping explicitly: stator limit governs the motor, supply limit equals the current through the PDP channel and is the one to set against breaker trips.**
  - bears on: Which of the two currents the 40 A auto-reset breakers on rig-flex actually see, and which number an OVERCURRENT latch corresponds to.
  - https://www.chiefdelphi.com/t/374780

- **CTRE staff give the rule for which limit to use for which purpose: stator limits the motor and its acceleration, supply is what the PDP channel sees and is the one that prevents breaker trips.**
  - bears on: The SPARK offers only the motor-side (Smart Current) limit, so breaker protection on this fleet has to come from the duty-cycle relationship, not from a supply limit.
  - https://www.chiefdelphi.com/t/374780

- **REV staff state that 80 A is the factory default Smart Current Limit in the SPARK MAX, and recommend 40-50 A as the starting point for a NEO and 20-30 A for a NEO 550.**
  - bears on: The repo never writes the Smart Current Stall Limit, so all eight controllers sit at REV's factory 80 A -- confirmed here as the shipped default, and above REV's own recommended NEO value.
  - https://www.chiefdelphi.com/t/376025

- **REV support restates the 80 A default and gives 40-50 A as the starting band for a full-size NEO, with the limit chosen from the published locked-rotor data for the specific motor.**
  - bears on: What value to write for the drive and steer Smart Current Stall Limit if the repo starts setting it.
  - https://www.chiefdelphi.com/t/376025

- **REV support gave the same two-second figure directly to a team whose NEO 550 smoked.**
  - bears on: Corroborates the 80 A default and its consequence on small motors, from a REV phone-support conversation.
  - https://www.chiefdelphi.com/t/376025

- **REV staff draw the operative line as active vs sticky: an active fault holds the motor from spinning, a sticky one only records that the condition happened and is no longer present.**
  - bears on: Whether a latched-but-sticky OVERCURRENT on eight controllers is actionable at all. REV's own triage question is whether it is still active; a sticky-only bit is by definition a past event.
  - https://www.chiefdelphi.com/t/449880

- **REV's own first diagnostic for a Vortex/Flex EEPROM or Sensor Fault is the docking screws: if they are not fully installed the Motor Interface Connector does not seat, and the motor's own EEPROM (holding motor-specific parameters) cannot be read.**
  - bears on: Any sticky Sensor/EEPROM fault latched on the eight rig-flex controllers after driving: check the two Flex-to-Vortex docking screws before suspecting CAN or code. This failure mode does not exist on SPARK MAX, which has no docking interface.
  - https://www.chiefdelphi.com/t/449880

- **CTRE's engineer stated that when Phoenix is initialized on the bus, only SPARK Flex has been reported failing; SPARK MAX has not.**
  - bears on: If any CTRE library or device shares the rig-flex bus, the Flex is the part that misbehaves. Reproduced with all CTRE devices physically removed and only the Phoenix enable frame present.
  - https://www.chiefdelphi.com/t/449976

- **REV confirmed a loose Vortex-to-Flex dock produces EEPROM Fault and "Other Error", and separately acknowledged tracking a firmware bug that could produce a spurious EEPROM fault.**
  - bears on: Distinguishing a real mechanical dock problem from a firmware artifact when reading sticky faults off the eight controllers; means an EEPROM fault alone is not proof of a bad dock.
  - https://www.chiefdelphi.com/t/453509

- **REV Hardware Client control over USB is deliberately locked out whenever the Flex sees a roboRIO on the CAN bus; the device still accepts setpoints over CAN and a reboot clears the lockout.**
  - bears on: Explains a Flex that appears dead to the hardware client while healthy on CAN. Prevents misreading a lockout as a hardware failure during bench diagnosis of the eight controllers.
  - https://www.chiefdelphi.com/t/453509

- **CTRE staff state the invariant directly: stator (motor) current magnitude is always greater than or equal to supply (input) current.**
  - bears on: Bounding the true input current from the SPARK's motor-current telemetry; the 150 A pegged readings cannot correspond to 150 A at the breaker.
  - https://www.chiefdelphi.com/t/454392

- **REV's president stated that in internal development testing the only way they cracked a Vortex faceplate was a very large direct impact on the motor can, roughly a full-force 5 lb dead-blow swing; multiple teams nonetheless cracked 3 of 4 drive motors in normal MK4i play.**
  - bears on: Sets the vendor's own impact threshold for the Vortex faceplate. Cracks appearing below it indicate a mounting or shaft-support problem, not an impact event.
  - https://www.chiefdelphi.com/t/456992

- **REV advises against calling factory-reset on every boot, because it makes every parameter depend on a config write that a CAN error can silently drop.**
  - bears on: How the repo's SPARK configuration path should be structured once it starts writing the current limit; a reset-then-write path can leave one controller at 80 A.
  - https://www.chiefdelphi.com/t/461113

- **REV's president advises against calling restoreFactoryDefaults() every boot when settings are already burned to flash, because a dropped CAN write then silently leaves a parameter unset.**
  - bears on: this driver never writes the Smart Current Stall Limit, so all eight controllers sit at REV's 80 A factory default. This is REV saying the parameter must be persisted, and that relying on per-boot writes is the failure-prone path.
  - https://www.chiefdelphi.com/t/461113

- **REV's software lead gave the SPARK Flex quadrature velocity filter defaults and the resulting measurement delay: average depth 64, measurement period 100 ms, 0.5 ms sample period, for 65.75 ms of delay. SPARK MAX defaults are depth 8 and 32 ms.**
  - bears on: Everything read back from a Flex through the default filter is smoothed over a ~66 ms window, so the 81-98 A drive and 107-150 A steer traces are already attenuated figures; true peaks are higher than what is logged.
  - https://www.chiefdelphi.com/t/470791

- **A REV engineer reading a CAN log of Flex timeouts concluded the Flex was dropping off the bus and that the cause was in Flex firmware, and stated the failure appears limited to Flex and not seen on SPARK MAX.**
  - bears on: Confirms that Flex-only CAN/fault behavior on a mixed or all-Flex bus is a known firmware-side asymmetry, not a wiring conclusion to be drawn about the gs_usb adapter.
  - https://www.chiefdelphi.com/t/480555

- **CTRE gives a quantitative sanity check for wiring health: about 75 A of draw pulling the bus from 11.9 V to 6.2 V is an electrical defect, because that drop should take roughly 280 A. That implies a healthy total system resistance near 20 mOhm.**
  - bears on: Gives a vendor-anchored volts-per-amp yardstick to test rig-flex's own battery-to-PDH path before touching current limits. bhall-ctre is CTRE staff.
  - https://www.chiefdelphi.com/t/481104

- **REV fixed a Flex firmware defect in 25.0.4 where a limit switch could not be read back once it was disabled in configuration.**
  - bears on: Sets a firmware floor for Flex limit-switch readback behavior; rig-flex at 26.1.6 is past it.
  - https://www.chiefdelphi.com/t/491111

- **REV specifies that the Vortex has an internal bearing supporting the installed shaft and that at least one additional bearing is required at the far end, without overconstraining the mechanism.**
  - bears on: Vortex shaft wobble or apparent runout on an unmounted motor is expected and is taken up by the second bearing once installed; it is not by itself a reason to replace a drive motor on rig-flex.
  - https://www.chiefdelphi.com/t/511425

- **CTRE ships Kraken defaults of 120 A stator and 70 A supply for drivetrain use, and states the supply limit is the hard cap on battery draw.**
  - bears on: Calibration for rig-flex's 80 A REV stall limit: another vendor considers 120 A stator normal for drive, so 80 A stator is not an aggressive setting.
  - https://www.chiefdelphi.com/t/512144

- **CTRE's TalonFX defaults are 120 A stator and 70 A supply, chosen for a drivetrain, and CTRE explicitly directs teams to lower SUPPLY current (not stator, not slip) to cure brownouts and breaker trips; for a steer motor CTRE recommends leaving stator at 60 A and dropping supply to 20 A instead.**
  - bears on: The nearest vendor guidance to rig-flex's situation, from the vendor with two separate limits. It says the motor-side number (which is what the SPARK reports) is the wrong knob for a breaker or brownout problem. Corroborated in the same author's steer-specific advice at https://www.chiefdelphi.com/t/516964.
  - https://www.chiefdelphi.com/t/512144

- **CTRE staff give the governing identity supply = stator * duty cycle, and state explicitly that a stator reading ABOVE the supply limit while supply stays below both limits is the normal, expected outcome at low speed.**
  - bears on: Explains 107-150 A steer stator readings coexisting with 40 A breakers that have never tripped. The two numbers are consistent, not contradictory.
  - https://www.chiefdelphi.com/t/512713

- **The same CTRE engineer gives the swerve-specific split: set the stator/slip limit at the wheel-slip point (typically 80-120 A) and lower the SUPPLY limit, not the stator limit, to cure brownouts and breaker trips; clamping a steer stator limit from 60 A to 20 A degrades azimuth tracking.**
  - bears on: If rig-flex's OVERCURRENT latch is addressed by lowering the 80 A stall limit, this warns that clamping steer stator current costs azimuth tracking and is the wrong lever for breaker protection.
  - https://www.chiefdelphi.com/t/516964

- **CTRE staff give 70 A as the Phoenix default supply current limit and recommend 40-60 A for a swerve drive motor to stop breaker trips and brownouts.**
  - bears on: A vendor-set input-current figure to compare against: 40-60 A input on a 40 A breaker is the working range, far below the 80 A motor-side default.
  - https://www.chiefdelphi.com/t/516964

- **CTRE staff put the stator current at which an FRC swerve wheel starts slipping at typically 80-120 A, and warn that limiting below it costs acceleration.**
  - bears on: rig-flex's drives reading 81-98 A motor current sit exactly in the normal traction-limited band, so those readings are not by themselves a fault.
  - https://www.chiefdelphi.com/t/516964

- **REV confirmed that SPARK Flex follower mode double-applies voltage compensation to the leader's already-compensated output; the interim workaround is to disable voltage compensation on the follower.**
  - bears on: If any of rig-flex's eight controllers run as followers with voltage compensation enabled, applied output and therefore current draw is higher than commanded.
  - https://www.chiefdelphi.com/t/518209

## corroborated (39)

- **Snap Action MX5 40 A auto-reset breaker (the CTRE PDP part) has a datasheet trip band of 3.9 to 47 seconds at 60 A, i.e. 150 percent of rating.**
  - bears on: How long a 40 A CTRE breaker will carry the drives' 81-98 A motor current translated to input current; the band is wide enough that no trip is expected at typical swerve duty cycles.
  - https://www.chiefdelphi.com/t/126391

- **Measured 10x split on a stalled NEO drivetrain: the PDP read 2.9-4.3 A per channel while the four SPARK MAXes simultaneously reported 39.97-40.0 A, with getAppliedOutput() around 0.10-0.12.**
  - bears on: Quantifies how far apart motor current and breaker current run. A steer reading 107-150 A at ~10-25 percent duty is 10-40 A at the breaker, which never trips a 40 A auto-reset.
  - https://www.chiefdelphi.com/t/350542

- **A second hardware measurement of the same effect: four SPARK MAXes each reported ~40.0 A at stall while their PDP channels read 2.9-4.25 A, with getAppliedOutput near 0.10.**
  - bears on: Independent confirmation that a SPARK pinned at its motor-current limit can be drawing under 5 A at the breaker; supports treating the 107-150 A readings as motor current only.
  - https://www.chiefdelphi.com/t/350542

- **Three separate teams report the SPARK MAX reporting a large, constant, load-independent 'Output Current' - 72 A, 85 A and 125 A - with the motor unloaded and at varying commanded output; REV staff called the value high and asked whether it reproduced when read through code rather than the Hardware Client Run tab.**
  - bears on: Whether rig-flex's 150.00 A steer readings are physical at all. There is precedent for the SPARK reporting a fixed, wrong, high current; cross-check against PDH channel current and applied output before treating 150 A as a real motor draw.
  - https://www.chiefdelphi.com/t/373283

- **Three teams independently saw SPARK MAX 'Output Current' stuck at a constant implausible value regardless of load -- 72 A, 85 A, and 125 A -- and the 72 A case went away and came back on its own with no faults reported.**
  - bears on: Prior art for treating a pegged current telemetry value as an artifact. Strengthens the read that rig-flex's exact 150.00 A is a saturated field, not a measurement.
  - https://www.chiefdelphi.com/t/373283

- **Second and third teams in the same thread report the same stuck-high reading at different values, confirming it is not one bad controller.**
  - bears on: Same as above -- corroborates that SPARK output-current telemetry can read a fixed high number with no physical basis.
  - https://www.chiefdelphi.com/t/373283

- **The roboRIO brownout threshold is 6.8 V with recovery at 7.5 V, and the resistance budget that sets it is: battery internal resistance typically up to 20 mOhm, 6 AWG 0.4 mOhm/ft, 4 AWG 0.25 mOhm/ft, SB-50 mated connector about 0.2 mOhm.**
  - bears on: Lets you compute the expected sag on rig-flex's own cable run and decide whether observed sag is wiring or draw. A second thread quotes 6.75 V for the roboRIO 2 (https://www.chiefdelphi.com/t/515133).
  - https://www.chiefdelphi.com/t/374780

- **Team-reported working values on NEO drivetrains cluster at 50-55 A, with stuttering under commanded acceleration above 60 A.**
  - bears on: A starting value for the drive Smart Current Stall Limit, against the drives' observed 81-98 A.
  - https://www.chiefdelphi.com/t/379284

- **Independent statement that a NEO tolerates the 80 A default thermally under normal match-length FRC duty, with the caveat that long practice sessions or sustained stall need a lower limit.**
  - bears on: rig-flex drives continuously rather than in 2:30 match bursts, which is the exact caveat named here. Relevant to whether the untouched default is appropriate for this duty cycle.
  - https://www.chiefdelphi.com/t/405541

- **Total-draw thresholds for brownout onset: 150 A to 300 A depending on battery health and wiring, with roughly 240 A treated as an instant brownout; the MK battery datasheet is cited for 270 A sustained for about 5 s.**
  - bears on: A power budget for eight controllers. Corroborated by Carly_Buchanan at https://www.chiefdelphi.com/t/350003 ("generally ~240A draw is assumed to be an instant brownout") and daltzsmith's MK datasheet citation at https://www.chiefdelphi.com/t/454392.
  - https://www.chiefdelphi.com/t/405541

- **A 20 A fuse on a REV Pneumatic Hub blew mid-match from a brownout-driven current spike even though the compressor normally draws only 8-10 A; multiple named teams hit this and the fix is a 20 A auto-reset breaker instead of a fuse.**
  - bears on: Fuse vs auto-reset breaker choice on low-current auxiliary rails; a fuse will not survive brownout inrush that a breaker rides through.
  - https://www.chiefdelphi.com/t/408278

- **A measured 5:1 discrepancy: SPARK MAX reported 60 A while the PDH channel feeding it read 10-20 A, at 20 percent applied output. Peter Johnson closed the power balance to within ~10 W (input 10.2 V * 13 A = 133 W; output 10.2 V * 20% * 60 A = 122 W).**
  - bears on: A worked, instrumented case of exactly the rig-flex symptom: high SPARK current with a quiet supply side. Gives the arithmetic to back out true supply current from applied output.
  - https://www.chiefdelphi.com/t/408303

- **Independent second measurement of the same split: SPARK MAX reporting a flat 60 A output while the PDH channel oscillated between 10 and 20 A, at 20 percent applied output; WPILib's Peter Johnson attributes it to duty cycle and the numbers close on a power balance (10.2 V x 13 A in = 133 W; 10.2 V x 20% x 60 A out = 122 W).**
  - bears on: Gives the conversion to apply to the rig-flex numbers: I_input = I_motor x duty cycle. Log applied output alongside current before treating 150 A as a hazard.
  - https://www.chiefdelphi.com/t/408303

- **A worked, logged case: a SPARK MAX reported 60 A while the PDH channel feeding it read 10-20 A, because the applied duty cycle was 20 percent; input current is output current times duty cycle.**
  - bears on: Why steers reporting 107-150 A and drives 81-98 A can coexist with 40 A breakers that have never tripped -- at low duty cycle the breaker sees a small fraction of the reported motor current.
  - https://www.chiefdelphi.com/t/408303

- **Measured on hardware: a SPARK MAX reported 60 A output while the PDH channel feeding it read 10-20 A, because applied duty cycle was 20 percent.**
  - bears on: Why steers reading 107-150 A and drives 81-98 A have never tripped a 40 A breaker; the breaker sees motor current times duty cycle.
  - https://www.chiefdelphi.com/t/408303

- **The input/output current relationship is exactly I_in = I_out * duty cycle, which is also the inverse of the voltage ratio, so a target input-current cap converts directly into a SPARK output-current limit.**
  - bears on: Converting the observed 107-150 A motor readings into the input current the 40 A breakers actually see, and sizing the Smart Current Limit against the 40 A breaker.
  - https://www.chiefdelphi.com/t/408303

- **Measured on a real robot: a SPARK MAX reported 60 A while the PDH channel feeding it read 10-20 A, at 20 percent duty cycle; input current = output current x duty cycle, so a 150 A motor-side reading at 20 percent duty is only ~30 A through the breaker.**
  - bears on: Quantifies the motor-vs-input divergence for the 8 controllers reporting 81-150 A on 40 A breakers. Peter_Johnson is the WPILib lead; the 60 A vs 10-20 A pair is a measured SPARK-vs-PDH comparison on the same channel.
  - https://www.chiefdelphi.com/t/408303

- **Mismatched per-module current limits make swerve modules fight each other and brown out a well-charged battery. On one robot one module was at 20 A and the rest at 80 A; on another, the SDS library was silently writing the limits.**
  - bears on: Confirmed: parameter 59 reads 80 on all eight rig-flex controllers, and 60 reads 20 beside it. `uv run spark params --param 59` re-checks it, and `spark provision` compares it against the declared 80. The audit does not, because that entry is marked `deviates: false`. Corroborated at https://www.chiefdelphi.com/t/425534 ("one was 20a and the rest were 80a") and https://www.chiefdelphi.com/t/416428 (library resetting to 20 A steer / 80 A drive).
  - https://www.chiefdelphi.com/t/415697

- **Battery internal resistance targets from multiple teams: below 0.020 ohm required, below 0.017 preferred, 0.013 as a target, retire at 0.020; 25-28 mOhm is called the brownout danger zone unless total draw is capped at 200 A.**
  - bears on: Sets the acceptance test for rig-flex's pack before blaming the controllers. Corroborated by TaylerUva at https://www.chiefdelphi.com/t/477377 ("Internal Resistance (Rint): 0.016 or less") and mtuckerFRC4381 at https://www.chiefdelphi.com/t/374780 ("If you start to see 25 - 28 mOHM of internal resistance over it's life cycle then it is entering the danger zone").
  - https://www.chiefdelphi.com/t/429533

- **Every FRC thermal breaker permanently lowers its own trip point once it has tripped, because the bimetallic element takes a set.**
  - bears on: If rig-flex's breakers ever do trip, they should be replaced rather than reused; also explains repeat trips after a first event.
  - https://www.chiefdelphi.com/t/432546

- **The 80 A SPARK default is a stator limit with no input-current counterpart; the SPARK MAX has no input current limit, and 80 A at full current destroys a NEO 550 in about 2 seconds.**
  - bears on: There is no SPARK-side knob that limits what the 40 A breaker sees; the only lever is the stator limit plus duty cycle.
  - https://www.chiefdelphi.com/t/435697

- **Leaving the 80 A default in place destroys a NEO 550 in about two seconds at full current -- the concrete cost of never writing the limit.**
  - bears on: Risk assessment for any small motor left at default in the fleet; full-size NEO/Vortex tolerate 80 A, small motors do not.
  - https://www.chiefdelphi.com/t/435697

- **Per the REV 40 A auto-reset breaker datasheet it sustains 80 A for a minimum of 5 s, and the 120 A main breaker holds near double its rating; a measured swerve drivetrain log shows a 120 A supply spike for 0.5 s on a 0-to-100 percent duty step, settling to about 10 A.**
  - bears on: Sets the real headroom of rig-flex's 40 A CTRE breakers, and shows supply current is a short transient at duty-cycle steps rather than a steady value comparable to the SPARK's stator reading.
  - https://www.chiefdelphi.com/t/454392

- **Same figure from a second thread: the REV 40 A breaker datasheet allows 80 A for a minimum of 5 s.**
  - bears on: Corroborates that the branch breakers are a fire-protection device for the wire, not a current limit for the motor.
  - https://www.chiefdelphi.com/t/454392

- **The 40 A auto-reset breakers carry far more than 40 A before opening: the REV 40 A datasheet sustains 80 A for a minimum of 5 s, and one team deliberately runs 80 A for 0.25 s then drops to 55 A on a 40 A breaker.**
  - bears on: Explains why the 40 A CTRE breakers on rig-flex have never tripped even if input current genuinely exceeded 40 A in bursts -- a non-tripping breaker is not evidence that draw stayed under 40 A. Corroborated at https://www.chiefdelphi.com/t/460309 ("We run our current at 80 per for.25 seconds then drop it to 55.").
  - https://www.chiefdelphi.com/t/454392

- **Individual SPARK Flex units lose their CAN ID and all burned settings on power cycle, reverting the ID to 0; REV treats it as a hardware defect and RMAs the unit.**
  - bears on: A Flex-only defect that would silently restore the 80 A factory current limit on a controller that had been configured otherwise. The read-back exists now: `uv run spark params --param 59` reads the limit off all eight over CAN, and it read 80 everywhere.
  - https://www.chiefdelphi.com/t/455171

- **REVLib 2024.2.3 introduced spurious Sensor Faults on SPARK Flex triggered by a code deploy; three teams independently fixed it by downgrading to 2024.2.0, with Flex firmware unchanged.**
  - bears on: Precedent that a host-library version, not the controller firmware, can be the source of Flex sensor faults. Worth pinning and bisecting the host stack before condemning hardware on rig-flex.
  - https://www.chiefdelphi.com/t/456113

- **REVLib's zero-argument getEncoder() branches on device model: SPARK Flex gets a quadrature encoder at 7168 counts/rev, SPARK MAX gets a hall sensor at 42. REV's software lead confirmed the Flex sensor is not a hall sensor.**
  - bears on: Any Flex encoder or velocity-filter configuration copied from SPARK MAX code is wrong by a factor of 170 in resolution and lands on the wrong filter parameter family (quadrature* vs uvw*/hall).
  - https://www.chiefdelphi.com/t/456242

- **A swerve team running steer motors on 30 A breakers reports the steer motors do draw over 30 A but not long enough to trip; they run a 60 A software limit on steer and 90 A on drive, the latter on a 40 A breaker.**
  - bears on: What per-motor limits swerve teams actually run against 40 A breakers -- 90 A drive / 60 A steer -- versus rig-flex's untouched 80 A on all eight.
  - https://www.chiefdelphi.com/t/459667

- **REV 40 A breakers carry far above 40 A briefly and slightly above 40 A for minutes; one team deliberately runs 80 A for 0.25 s then drops to 55 A on that breaker.**
  - bears on: Sustained-current headroom of a 40 A auto-reset breaker, and what current limits other teams actually pair with one.
  - https://www.chiefdelphi.com/t/460309

- **The SPARK Flex data port is a physically different connector from the SPARK MAX's; the MAX ribbon cable will not mate, the mating part is a Samtec SFSD-05-28-H-10-00-SR, and with a soldered breakout an absolute encoder needs 5 V, GND and pin 6.**
  - bears on: Any external encoder or limit-switch wiring plan carried over from SPARK MAX hardware needs a different cable on the Flex. Part number from https://www.chiefdelphi.com/t/454063; the P6 pinout from guineawheek in this thread.
  - https://www.chiefdelphi.com/t/462157

- **REV changed Vortex manufacturing in December 2024, moving the front piece from a casting to a CNC-machined part, which reportedly ended the faceplate cracking.**
  - bears on: Dates the hardware revision. Base03's motors on 26.1.6-era firmware are almost certainly post-change CNC parts, so faceplate cracking is unlikely to be the failure mode here.
  - https://www.chiefdelphi.com/t/475933

- **The Vortex-to-MAXPlanetary coupler seats the shaft about 20 thou below the coupler plate where CAD calls for 40 thou, binding the first-stage sun gear; four teams independently fixed it by shimming with #10 washers.**
  - bears on: A mechanical source of extra load, and therefore extra current, on any Vortex driving a MAXPlanetary. A 20 thou shim error is invisible by hand but shows up as elevated steady-state draw.
  - https://www.chiefdelphi.com/t/484530

- **80 A is the SPARK factory default smart current limit, and configuring with kResetSafeParameters silently restores it whenever the configuration does not set it explicitly.**
  - bears on: Confirms the 80 A on rig-flex is REV's untouched factory value, and explains why setting it once in the client would not stick if code configures with kResetSafeParameters.
  - https://www.chiefdelphi.com/t/491595

- **A configure() call using kResetSafeParameters that does not itself write the current limit puts it back to 80 A -- the exact failure mode of a repo that never writes the limit.**
  - bears on: Why all eight controllers read exactly the factory 80 A even if someone once set a value in the REV Hardware Client; check the ResetMode used in the config path.
  - https://www.chiefdelphi.com/t/491595

- **The SPARK Flex CAN pigtail is 26 AWG, not the 22 AWG standard used elsewhere in FRC, and it repeatedly pulls out of ferrules crimped for standard wire.**
  - bears on: A physical, Flex-only cause of intermittent CAN faults on a bus of eight Flexes. Any ferrule, WAGO or terminal on rig-flex sized for 22 AWG is undersized for the Flex leads.
  - https://www.chiefdelphi.com/t/497359

- **A stalled pushing match produces high stator current and low supply current, which is why breakers survive it.**
  - bears on: Why the steer motors, which spend their time in near-stall azimuth moves, report the highest motor currents while contributing least to breaker load.
  - https://www.chiefdelphi.com/t/516964

- **A stalled pushing match produces high stator current with low supply current -- so the breaker sees little of what the controller reports during a stall.**
  - bears on: Directly explains steers latching OVERCURRENT at 107-150 A motor current (near-stall azimuth holding) while their 40 A input breakers stay closed.
  - https://www.chiefdelphi.com/t/516964

- **Swerve gear ratio dominated brownouts for two teams independently: SDS MK5i at R3 caused constant brownouts and 160 A spikes on direction reversal; going to R2 eliminated them. Stator 60 / supply 40 on all motors made the largest single difference.**
  - bears on: Drive gearing as a cause of high drive current (81-98 A observed). The second team's independent confirmation is in the same thread: "Exactly the problem we had. R3 is insanely power demanding. Switching to R2 completely eliminated our brownout issues."
  - https://www.chiefdelphi.com/t/518203

## reported (77)

- **These are thermal breakers, so a breaker that starts a run hot trips at a lower current than one at room temperature.**
  - bears on: Repeat-run behaviour on rig-flex: an absence of trips on a cold breaker does not predict a warm one after sustained driving.
  - https://www.chiefdelphi.com/t/138997

- **A stalled motor was measured pulling 90 amps through a 40 A breaker for long enough for a mentor to react and shut it down.**
  - bears on: That never-tripped 40 A breakers do not bound the current on rig-flex anywhere near 40 A; the breakers are not evidence the draw is low.
  - https://www.chiefdelphi.com/t/163262

- **At 300 percent overload (about 120 A on a 40 A breaker) the AndyMark-listed Snap Action datasheet gives a trip time of 0.5 to 1.1 seconds, and longer as the overload shrinks.**
  - bears on: Sets the fast end of the 40 A trip curve: if rig-flex's breakers had ever seen 120 A input current for one second they would have opened, so input current must be well below that.
  - https://www.chiefdelphi.com/t/350003

- **Mechanical friction is measurable as current: one team's bent drivetrain frame moved their draw from 50 A to 85 A on the same mechanism.**
  - bears on: A 70 percent current rise from one mechanical defect. Gives a scale for how much of rig-flex's 81-98 A drive current could be drivetrain friction rather than duty.
  - https://www.chiefdelphi.com/t/350003

- **Measured ~10:1 ratio on four drive SPARK MAXes at stall: PDP channels read 4.25 / 4.125 / 2.875 / 4.25 A while the SPARKs simultaneously reported 39.97 / 39.97 / 40.0 / 40.0 A, with getAppliedOutput around 0.105-0.117.**
  - bears on: Confirms the ratio equals the duty cycle to within noise (0.11 * 40 A = 4.4 A). The measurement to take on rig-flex is applied-output logged alongside current.
  - https://www.chiefdelphi.com/t/350542

- **At stall with an 80 A smart current limit, the SPARK MAX applies roughly 0.3 duty cycle when commanded 1.0, implying about 24 A drawn from the battery per controller.**
  - bears on: The 80 A REV factory default on rig-flex: at stall it corresponds to roughly 24 A of breaker current, well under the 40 A auto-reset breakers, consistent with them never tripping.
  - https://www.chiefdelphi.com/t/350542

- **The 80 A factory default is a STALL limit that scales down with speed, not a flat cap: the advertised defaults are 20 A free limit, 80 A stall limit, 10000 RPM limit-RPM, interpolated between.**
  - WITHDRAWN: the first of the two findings retracted at the top of this file, kept as harvested because the poster withdrew it himself later in the same thread. The three defaults are right; "interpolated between" is not, at factory settings. Parameter 61 Smart Current Config ships at 10000 RPM (REV-SparkParameters-v0.1.2 and sparklib/data/rev_parameter_index.tsv), above the free speed of every motor on this fleet, and REV's parameter page says a value above free speed disables the reduction. Measured on a Flex since: parameter 61 reads 10000 on all eight rig-flex controllers, and provenance.flex.param.61.limit_rpm is graded hardware.
  - bears on: nothing now. The reasoning it carried -- that the repo never writes setSmartCurrentLimit, so rig-flex rides an interpolated curve whose effective limit at steer speed is well below 80 A and explains an overcurrent bit at a reported 107-150 A -- is dead. The limit is a flat 80 A at every speed. What the warning does track was measured on rig-flex: the 80 A Smart Current Stall Limit engaging, docs/runs/steer-overcurrent.md.
  - https://www.chiefdelphi.com/t/350542

- **With the 80 A smart limit, a full-output command at stall results in roughly 0.3 applied duty cycle -- the limiter works by clamping duty cycle, which is exactly the mechanism that decouples motor current from breaker current.**
  - bears on: Predicts rig-flex input current: a steer at 150 A motor current with the limiter clamping duty cycle to ~0.2-0.3 draws 30-45 A input, right at the 40 A breaker's slow-trip region.
  - https://www.chiefdelphi.com/t/350542

- **At full command into a stall with an 80 A smart limit, the SPARK applies roughly 0.3 duty cycle, so the breaker sees about 30 percent of the limit value.**
  - bears on: Estimating input current on the drive channels: an 80 A stall limit implies roughly 24 A at the 40 A breaker, consistent with the breakers never tripping.
  - https://www.chiefdelphi.com/t/350542

- **The advertised REV factory defaults are a triple, not a single number: 80 A stall limit, 20 A free limit, 10000 RPM limit-transition speed.**
  - bears on: What the untouched configuration actually is on all eight controllers -- the free limit and RPM breakpoint are also at defaults, not just the 80 A stall limit.
  - https://www.chiefdelphi.com/t/350542

- **REV's own dyno test of NEO + SPARK MAX with current limiting disabled measured a 150 A stall current, far below the ~330 A the winding resistance predicts, because of MOSFET, board and wiring resistance.**
  - bears on: The 150.00 A readings: 150 A is both the 12-bit field full scale and the measured unlimited stall current of a REV brushless motor on a SPARK, so a 150.00 reading is ambiguous and should be treated as pegged.
  - https://www.chiefdelphi.com/t/350542

- **The SPARK MAX is not designed for continuous operation above 100 A of motor current, and REV's published empirical curves reflect a ~100 A ceiling rather than the theoretical stall point.**
  - bears on: Whether the steer readings of 107-150 A represent an operating point the controller is rated for; they do not, on the MAX generation at least.
  - https://www.chiefdelphi.com/t/350542

- **The SPARK MAX smart current limit is a linear taper from the stall limit at zero speed down to 0 A at free speed, and REV's own dyno with current limiting disabled put the NEO + SPARK MAX stall current at 150 A, not the 330 A the 36.5 mOhm winding predicts.**
  - WITHDRAWN IN PART: the taper half is the second of the two findings retracted at the top of this file, kept as harvested for the same reason. The taper ends at the 20 A free limit, not 0 A, and at factory settings it does not run at all. The dyno half stands and is carried by the entry above.
  - bears on: 150.00 A is both the 12-bit field full scale AND the measured no-limit stall current of a REV brushless drive -- worth ruling out that steers reading exactly 150 are genuinely at that ceiling rather than merely pegged. It does NOT explain how a controller with an 80 A stall limit reports far above 80 A; the taper is off. rig-flex, shows the limiter regulating and overshooting on a reversal transient by an amount the saturated field cannot measure: docs/runs/steer-overcurrent.md.
  - https://www.chiefdelphi.com/t/350542

- **A capacitor bank sized to ride through a brownout is impractical: holding 1200 W (12 V x 100 A) while the bus falls from 12 V to 8 V for one second needs 15 F, and the inrush on power-up would blow the main breaker without a limiting circuit.**
  - bears on: Closes off bulk capacitance as a mitigation for rig-flex's sag, with the number that kills it.
  - https://www.chiefdelphi.com/t/364491

- **A three-way statement of what each limit protects: stator limits protect the controller and the motor windings, supply limits protect everything upstream of the controller.**
  - bears on: Frames the rig-flex decision: the 80 A stall limit is a motor and controller protection number; the 40 A breakers are the upstream protection, and nothing in software currently targets them.
  - https://www.chiefdelphi.com/t/374780

- **A SPARK MAX can brown out and reboot on line voltage sag at its own motor leads while the rest of the robot's voltage looks fine; the LED goes from green/red to blue and the cycle repeats. Fixed on that robot by adding a 0.05-0.1 s open loop ramp rate.**
  - bears on: A per-controller sag mechanism that leaves sticky faults without the roboRIO ever logging a robot-wide brownout -- matches eight controllers latching after driving with no breaker trip.
  - https://www.chiefdelphi.com/t/380499

- **A full-size NEO tolerates the 80 A default thermally in normal match duty; team 3005 nonetheless ran drive motors at 55 A.**
  - bears on: Whether the 80 A default is actively damaging the fleet's drive motors, versus merely un-tuned; duty cycle and run length are the deciding variables.
  - https://www.chiefdelphi.com/t/405541

- **Because motor-side current exceeds supply-side current at low duty cycle, resistive losses are worse on the controller-to-motor run than on the battery-to-controller run, so controllers belong close to the motors when low-duty performance matters.**
  - bears on: Wiring layout consequence of the supply/stator split for the rig-flex swerve modules, whose steer motors run at low duty cycle.
  - https://www.chiefdelphi.com/t/408303

- **The SDS swerve library writes 20 A on steer and 80 A on drive over whatever was set in the REV Hardware Client, silently reverting client-set values.**
  - bears on: If the fleet uses a vendored swerve library, that library may be the thing writing (or resetting) the limit, not the repo's own code -- grep the vendored module before concluding nothing writes it.
  - https://www.chiefdelphi.com/t/416428

- **Logged brownouts on a 4-NEO tank drive occurred at 200 A-plus battery current with each of four drive motors near 80 A; the team had to cap total draw near 120 A.**
  - bears on: Total-system current at which an FRC battery browns out, well before any individual 40 A branch breaker opens.
  - https://www.chiefdelphi.com/t/429533

- **Brownouts on a 4-NEO base occurred at 200 A-plus battery current with each drive motor near 80 A; a 120 A total cap stopped them, and shortening the battery-to-PDP loop from about 8 feet to under 3 feet helped substantially.**
  - bears on: Gives a total-draw number where a 4-motor NEO base at the 80 A default starts browning out, and quantifies cable-length as a fix. Note the 80 A per motor here is the same REV default rig-flex is running.
  - https://www.chiefdelphi.com/t/429533

- **A worked Ohm's-law diagnosis of a suspect battery-to-PDP path: 0.7 ohm would drop 4.2 V at only 6 A, which is why that reading was instrument error; 0.07 ohm at 80 A gives a 5.6 V drop, matching the observed brownouts. Target total system resistance is low-20s milliohms.**
  - bears on: A repeatable procedure to convert a measured drop into a resistance and decide whether rig-flex's sag is wiring or draw, including the caution that a cheap multimeter cannot resolve these values.
  - https://www.chiefdelphi.com/t/429533

- **A specific match failure traced to a previously-tripped main breaker: the robot died mid-qualification with the breaker lever still latched in, months after a single earlier trip.**
  - bears on: A degraded breaker can open without visibly latching, so 'the lever is in' is not proof the breaker has not opened.
  - https://www.chiefdelphi.com/t/432546

- **The SPARK MAX offers no input/supply-side current limit at all - only a stator limit - so on REV hardware there is no software setting that directly protects the breaker.**
  - bears on: Whether changing the 80 A Smart Current Stall Limit on rig-flex changes breaker exposure. It changes motor torque and heat; it touches breaker current only indirectly through duty cycle.
  - https://www.chiefdelphi.com/t/435697

- **The Smart Current Limit is a stator limit only; the SPARK MAX has no input-current limit, so nothing in the controller protects the branch breaker.**
  - bears on: Explains structurally why the 80 A Smart Current Limit and the 40 A breaker never interact: they govern different currents on opposite sides of the H-bridge.
  - https://www.chiefdelphi.com/t/435697

- **The 80 A default is considered thermally survivable for a full-size NEO in normal FRC duty, but destroys a NEO 550 in about 2 seconds at full current -- so the default's safety depends entirely on which motor is behind it.**
  - bears on: Whether leaving rig-flex's eight controllers on the factory 80 A is itself a hazard. It is not for NEO/Vortex-class motors; it would be for anything smaller on the same default.
  - https://www.chiefdelphi.com/t/435697

- **A load-generator test of the three legal FRC 120 A main breakers at 240 A gave trip times of 2m14s (Bussmann CB185), 2m18s (CB285) and 1m50s (Optifuse), all brand new units.**
  - bears on: How much a nominally-rated thermal breaker will carry before opening; 2x rated current runs for two minutes.
  - https://www.chiefdelphi.com/t/439983

- **Hard reverse braking on a MAXSwerve chassis pushed current back into the battery hard enough that brownout events were logged on the regeneration peaks; at an assumed 22 mOhm to the battery the peaks were estimated at 181 A of braking current.**
  - bears on: A high-current event that occurs on deceleration rather than acceleration, on REV swerve specifically -- worth checking against rig-flex's OVERCURRENT latching "after driving". The logger in the thread confirms: "on a couple of the peaks there were brownout events recorded."
  - https://www.chiefdelphi.com/t/442611

- **Merely instantiating a CANSparkFlex alongside an initialized Phoenix library (diagnostics disabled, no CTRE hardware reads) makes the Flex stutter and its reported output current oscillate 0 to 10-20 A with no mechanical load.**
  - bears on: A mechanism by which a Flex reports large current with no real load. Relevant when judging whether rig-flex's 107-150 A steer readings reflect real draw.
  - https://www.chiefdelphi.com/t/450525

- **In the 2024 REVLib FaultID enum, getFaults() bit 1 (decimal 2) decodes to kOvercurrent. Bit 11 (decimal 2048) is kOtherFault.**
  - CORRECTED: this line placed overcurrent at bit 11 until today. The pre-25 16-bit word runs kBrownout 0, kOvercurrent 1, kIWDTReset 2, kMotorFault 3, kSensorFault 4, kStall 5, kEEPROMCRC 6, kCANTX 7, kCANRX 8, kHasReset 9, kDRVFault 10, kOtherFault 11, kSoftLimitFwd 12, kSoftLimitRev 13, kHardLimitFwd 14, kHardLimitRev 15 -- REVLib 2024.2.4's own FaultID enum, recorded as provenance.pre25.fault_bit_order and decoded by admin._LEGACY_FAULT_BITS. Bit 9 is the one position with hardware behind it: rig-max, three motor-rail cycles each left all eight controllers at sticky 0x0200 and nothing else. REVLib 1.1.5 and earlier called bit 2 kOvervoltage without moving it, so an old decoder mis-names that position rather than mis-placing it. Whether the thread said bit 11 or this file's first pass misread the enum cannot be recovered from the harvest; either way the position is settled, and FAILURE-CATALOGUE.md carried it correctly.
  - bears on: Decoding raw fault words off the gs_usb bus without REVLib. Confirm against the 26.x field layout, since 2025+ moved overcurrent into a separate Warnings word. On rig-flex's firmware-26 Flexes it is not in the fault word at all: overcurrent is warnings bit 1 of STATUS_1 (api 0x2E1), byte 2 active and byte 5 sticky, per REV-spark-frames-2.1.0.json and admin.decode_status_1.
  - https://www.chiefdelphi.com/t/454240

- **A REV 40 A auto-reset breaker will sustain 80 A for at least 5 seconds per its datasheet.**
  - bears on: Quantifies the headroom above the nameplate rating. Corroborates that a non-tripping 40 A breaker is compatible with transient input currents around 80 A.
  - https://www.chiefdelphi.com/t/454392

- **Mounting screws driven deeper than 0.25 in into a SPARK Flex reach the circuit board and destroy it, presenting as a Gate Driver Fault that never clears; the team lost two Flexes and a Vortex to 0.5 in screws.**
  - bears on: A specific, checkable mounting spec for the eight rig-flex Flexes: screw engagement into the controller must not exceed 0.25 in. A persistent Gate Driver Fault after remounting points here.
  - https://www.chiefdelphi.com/t/454533

- **REV's own 40 A ATO breaker datasheet gives a trip-time band at 60 A running from just over 10 seconds to over 1000 seconds, which is why a branch can carry well above its rating indefinitely.**
  - bears on: The claim that 'the 40 A breakers have never tripped' is weak evidence that input current stayed under 40 A. A breaker at 50 percent overload may simply never reach trip within a run.
  - https://www.chiefdelphi.com/t/455228

- **The Snap-Action breakers used in the CTRE PDP are described from direct experience as slow to trip.**
  - bears on: rig-flex uses CTRE auto-reset breakers specifically. Their never having tripped does not bound the input current tightly.
  - https://www.chiefdelphi.com/t/455228

- **REV's own datasheet for the 40 A ATO auto-resetting breaker shows a trip-time spread at 60 A of a little over 10 seconds to over 1000 seconds -- two orders of magnitude of unit-to-unit variation.**
  - bears on: Any inference from 'the breakers never tripped' to a current bound is weak; individual breakers vary by 100x in trip time at the same current.
  - https://www.chiefdelphi.com/t/455228

- **Datasheet numbers for the 40 A auto-reset breaker: it sustains 80 A for at least 5 s, and trip time at 60 A ranges from just over 10 s to over 1000 s across units.**
  - bears on: How much headroom the 40 A CTRE breakers actually give, and why they have never tripped despite the OVERCURRENT warnings.
  - https://www.chiefdelphi.com/t/455228

- **An 'Over Current' active error appeared on a SPARK MAX at a 10 A limit, persisted after the team raised the limit to 40 A with a matching 40 A fuse, then cleared on its own with no identified cause.**
  - bears on: Second instance where changing the Smart Current Limit did not clear the overcurrent state, and where it later cleared spontaneously.
  - https://www.chiefdelphi.com/t/455428

- **The small housing screws on the Vortex back out under load; when they loosen the faceplate flexes away from the motor body and the output shaft goes floppy, and several holes were found stripped past re-tightening.**
  - bears on: A Vortex-specific preventative-maintenance item with no SPARK MAX/NEO equivalent: put the housing screws on the inspection checklist for rig-flex's eight modules alongside the docking screws.
  - https://www.chiefdelphi.com/t/456992

- **An overcurrent flag re-latched on a swerve steer SPARK MAX within one second of clearing faults, with the wheel up on chocks and free to spin -- i.e. the bit set under essentially zero mechanical load.**
  - bears on: What clears the bit (the REV Hardware Client clear-faults action does clear it) and how fast it comes back; also whether a steer-motor overcurrent means real current. A no-load re-latch in ~1 s argues it does not.
  - https://www.chiefdelphi.com/t/459394

- **Raising the Smart Current Limit above the default did not stop the overcurrent flag on swerve steer SPARK MAXes: 60 A and 70 A both left it latching, and the same flag appeared on modules that behaved normally.**
  - bears on: The plan of writing a non-default Smart Current Limit to stop the sticky OVERCURRENT on the steers. One team changed the limit twice and the flag was unaffected.
  - https://www.chiefdelphi.com/t/459394

- **Swerve steer SPARK MAXes latched repeated overcurrent faults with the wheels on chocks and free to spin; root cause was traced to CAN wiring and a failing CANcoder, not motor load.**
  - bears on: The steer channels reporting 107-150 A: a stale or lost azimuth sensor makes the steer PID run away and draw hard, and it presents as a sticky overcurrent. Check the CAN/encoder path before assuming a mechanical load.
  - https://www.chiefdelphi.com/t/459394

- **Over multiple seasons one team saw a steering-motor breaker trip exactly once, and only when the module jammed mechanically; a smaller breaker there also kept the motor cooler.**
  - bears on: A steer breaker trip is a jam signature; rig-flex's steers latch OVERCURRENT with no trip, which points at the SPARK's motor-current limit, not a mechanical bind.
  - https://www.chiefdelphi.com/t/459667

- **CTRE's PDP manual rates the small (non-40 A) channels at 30 A even though REV 40 A ATO breakers physically fit them and FIRST ruled them legal in Q&A 25.**
  - bears on: Channel rating on a CTRE distribution panel is a separate limit from the breaker in it, if rig-flex's breakers sit in small slots.
  - https://www.chiefdelphi.com/t/459667

- **A SPARK Flex fired eleven fault bits at once, including kOvercurrent, for a single loop cycle with none becoming sticky; the cause was the controller itself and replacing the unit fixed it.**
  - bears on: Signature to look for on rig-flex: if OVERCURRENT arrives alongside a scatter of unrelated bits in one frame, it is a controller glitch or reset, not current. Check whether the eight latched warnings came in isolated or in a burst.
  - https://www.chiefdelphi.com/t/460577

- **A concrete on-robot test to prove a SPARK rebooted from a power event: the relative encoder position jumps to zero and then resumes summing from zero.**
  - bears on: Cheap discriminator between a genuine controller reset (sag) and a mere sticky-warning latch, using data the base already logs. Same thread's OP reports Over Current sticky faults on SPARK Flex.
  - https://www.chiefdelphi.com/t/460936

- **A team set SupplyCurrentLimit on Krakens and kept blowing the 120 A main breaker; adding StatorCurrentLimit mid-event stopped it. Two other posters in the same thread contradict the premise, so community reasoning on which limit the breaker sees is genuinely contested.**
  - bears on: A documented field case where reasoning about which limit the breaker sees went wrong. The supply/stator split is not purely academic; enforcement quality differs between them.
  - https://www.chiefdelphi.com/t/461056

- **Correction to the above within the same thread: supply current limits do function on current CTRE firmware, but are enforced less precisely than stator limits.**
  - bears on: Tempers the previous finding. The supply limit is real, just softer; a stator limit is the harder cap on what the motor can pull.
  - https://www.chiefdelphi.com/t/461056

- **On a six-Flex/Vortex drivetrain running an 80 A Smart Current Limit, overcurrent plus CAN TX/RX errors appeared on the SPARK Flexes every match while the SPARK MAXes on the same robot, same code and same CAN bus almost never reported either; the errors dropped sharply after the Vortexes were demounted and remounted to the Flex bases and the oldest unit was swapped.**
  - bears on: The rig-flex fleet is all Flex. This is the closest match in the corpus: same 80 A default limit, overcurrent on every Flex, and a mechanical fix (Vortex-to-Flex docking seat) rather than a current fix.
  - https://www.chiefdelphi.com/t/461113

- **The same team's overcurrent errors accumulated with run time rather than appearing at power-up -- clean for the first few minutes, then latching every match.**
  - bears on: The observation that all eight rig-flex controllers latch OVERCURRENT after driving, not at boot. A warm-up/degradation pattern, not a startup config problem.
  - https://www.chiefdelphi.com/t/461113

- **Reseating the Vortex on the Flex base and replacing the worst unit measurably reduced the overcurrent and CAN error rate, and the residual errors tracked the older controllers.**
  - bears on: A cheap physical check before any firmware or current-limit work on rig-flex: the Vortex-to-Flex Motor Interface Connector seating and docking screws.
  - https://www.chiefdelphi.com/t/461113

- **A team running six SPARK Flex + NEO Vortex on swerve with an 80 A smart limit got CAN errors and overcurrent faults every match; the fix was mechanical -- demounting and remounting the Vortex on the Flex base, plus replacing the worst unit.**
  - bears on: The sticky OVERCURRENT warning on all eight controllers: the closest match in the corpus to this exact hardware (Flex + Vortex + swerve + 80 A default), and its cause was the motor-to-controller mount, not the current limit.
  - https://www.chiefdelphi.com/t/461113

- **A team running four SPARK Flex/Vortex drives at an 80 A smart current limit got escalating OVERCURRENT plus CAN TX/RX sticky faults every match, while the SPARK MAXes on the same robot and bus reported essentially none.**
  - bears on: The live question directly: a sticky OVERCURRENT warning at an 80 A Smart Current Stall Limit is a documented Flex-specific pattern that does not appear on MAX hardware at the same limit on the same bus.
  - https://www.chiefdelphi.com/t/461113

- **That team's configuration was the same shape as rig-flex's: 80 A smart current limit, restoreFactoryDefaults() on every boot, roughly ten parameters set, then burn to flash.**
  - bears on: Establishes 80 A on a Flex/Vortex swerve drive as the exact condition under which the sticky OVERCURRENT warning was observed by another team, so rig-flex's readings are not anomalous.
  - https://www.chiefdelphi.com/t/461113

- **Physically undocking each Vortex from its SPARK Flex and re-seating it sharply reduced both the CAN errors and the overcurrent faults, with no change to the CAN wiring.**
  - bears on: A cheap, testable first action on rig-flex's eight latched OVERCURRENT warnings before touching the current limit or the code: re-seat and re-torque the docks and re-read the faults.
  - https://www.chiefdelphi.com/t/461113

- **A SPARK Flex latched an overcurrent sticky fault while the PDH input current for that channel stayed under 12 A, so the sticky bit fired with no breaker-level draw anywhere on the circuit.**
  - bears on: Whether the sticky OVERCURRENT on all eight rig-flex controllers implies the 40 A CTRE breakers were anywhere near tripping. This thread is a direct counterexample: latched overcurrent at <12 A input.
  - https://www.chiefdelphi.com/t/462958

- **A SPARK Flex latched a sticky overcurrent fault while the PDH channel showed under 12 A of input current.**
  - bears on: Sticky OVERCURRENT on the Flex can latch with negligible input current; the fault is not proof of a real power-path problem.
  - https://www.chiefdelphi.com/t/462958

- **Two NEO Vortexes burned out on one robot, attributed by the team to current limits set above 80 A.**
  - bears on: Upper bound on the Vortex Smart Current Limit; 80 A is the ceiling teams report, not a floor to raise from.
  - https://www.chiefdelphi.com/t/464873

- **A team burned out two Vortexes over a season and attributed both to running current limits above 80 A; 80 A was their working boundary rather than a safe headroom figure.**
  - bears on: Places rig-flex's 80 A factory default right at the edge teams associate with Vortex burnout, and supports writing a lower drive limit rather than leaving the default.
  - https://www.chiefdelphi.com/t/464873

- **An overcurrent fault was latched on a SPARK Flex flywheel controller that was running correctly, while its misbehaving twin had CAN TX/RX faults instead -- the overcurrent bit did not track the actual defect.**
  - bears on: Whether a latched OVERCURRENT is diagnostic of anything. Here it sits on the healthy unit and is absent from the faulty one.
  - https://www.chiefdelphi.com/t/468430

- **The prevailing CD reading is that a sticky-only fault is ignorable and only an active fault is actionable.**
  - bears on: How much weight to give the eight latched OVERCURRENT warnings before spending time on them.
  - https://www.chiefdelphi.com/t/468430

- **A simulated MK4i L3 swerve, 120 lb, NEO drive, with 60 A drive current limits pulls the bus down to about 8.3 V on a 40 ft run; L1 gearing reaches the same 8.3 V but holds it for less time.**
  - bears on: Reference point for expected sag on a NEO swerve at 60 A limits, against which rig-flex's 80 A default and its observed sag can be judged.
  - https://www.chiefdelphi.com/t/470903

- **Worked motor-curve example on a NEO 1.1: with an 80 A stator limit and a 40 A supply limit, holding 1.5 Nm gives 930 RPM at ~30 percent efficiency; gearing 2:1 at the same limits gives 2280 RPM and ~80 percent efficiency.**
  - bears on: Why the steers sit at high motor current: a near-stall operating point is the low-efficiency region, and gear ratio, not the limit, is the lever that moves it.
  - https://www.chiefdelphi.com/t/477010

- **Measured constants for the NEO Vortex show kB and kT do not match: back-EMF constant 0.01674 V-rad/s against a torque constant of 0.01546 N-m/A.**
  - bears on: Any torque-from-current calculation on the Vortex fleet; using kV-derived kT overestimates torque by about 8 percent.
  - https://www.chiefdelphi.com/t/477010

- **On a roboRIO 2 the brownout threshold is software-settable, and teams do lower it (one runs 5.5 V), but the named cost is that coprocessors reset or power off at that input voltage and take seconds to reboot.**
  - bears on: An escape hatch and its stated cost, if rig-flex sag turns out to be genuine and unfixable in wiring. The counter-argument is in the same thread: "Lowering the brownout voltage is very much a bandaid here [...] just kicking the can down the road."
  - https://www.chiefdelphi.com/t/477377

- **In the same thread a team found the Flex timeouts persisted even after cutting Flex current limits all the way to 10 A, so the fault is not simply current-driven.**
  - bears on: Warns against reading a lowered current limit as a fix for Flex fault behavior; the fault survived an 8x reduction below rig-flex's 80 A.
  - https://www.chiefdelphi.com/t/480555

- **Severe voltage sag on a swerve base was traced to Kraken motor screw terminals that had broken off despite being torqued; the robot sat at 10 V standing still, and replacing the affected motors made the brownouts disappear.**
  - bears on: A specific failure that produces per-controller sag and sticky faults with no breaker trip, located at the motor terminal rather than in the main power path everyone checks first.
  - https://www.chiefdelphi.com/t/481104

- **Setting the free limit above the stall limit inverts the intended behavior and visibly cripples acceleration: a Vortex swerve drive on a 50 A free / 30 A stall pair took 0.3 s to reach full duty cycle.**
  - bears on: How the stall and free limits interact once the repo starts writing them; stall must be the larger of the pair.
  - https://www.chiefdelphi.com/t/492810

- **A concrete supply-to-stator conversion at low speed: a 60 A supply limit still permits roughly 148 A of stator current at 0 rpm under trapezoidal commutation, at about 40 percent duty cycle.**
  - bears on: Directly brackets rig-flex's 107-150 A steer readings: numbers in that range at low speed are what a 40-60 A supply-side draw looks like on the motor side, not evidence of a fault.
  - https://www.chiefdelphi.com/t/501776

- **Replacing SB50 battery connectors with SB120 buys larger 4 AWG conductors in place of the standard 6 AWG, and is characterised by an experienced team as a real but minor gain that will not fix a bad battery or a friction problem.**
  - bears on: Ranks the wiring upgrade honestly against the other fixes. The next post in the thread pushes back: "these things are 'nice to haves'. They will not fix your problem if the battery is bad."
  - https://www.chiefdelphi.com/t/515133

- **SPARK Flex current control treats commanded current as unsigned, so a negative current setpoint produces unbounded integral windup and the motor cannot be driven in reverse; the invert parameter has no effect on current control. REV firmware 26.1.5 is reported to address it.**
  - bears on: Base03 runs 26.1.6, one release past the reported fix. If any code path uses Flex current control, sign handling changed between 26.1.4 and 26.1.5 and old tuning will not carry over.
  - https://www.chiefdelphi.com/t/515846

- **Stalled or near-stalled operation draws high stator current and low supply current; drawing large supply current requires the motor to be moving fast.**
  - bears on: Swerve steer motors are low-speed, high-reduction, near-stall much of the time. Predicts exactly the rig-flex pattern: steers show the highest stator numbers (107-150 A) and the lowest breaker load.
  - https://www.chiefdelphi.com/t/516964

- **A stalled pushing match produces high stator current but low supply current; large supply current requires the robot to be moving fast.**
  - bears on: Steer motors, which run at low speed and low duty cycle, can peg a motor-current reading while contributing almost nothing to breaker heating.
  - https://www.chiefdelphi.com/t/516964

- **A Kraken swerve robot tripped its 120 A main breaker at least three times in one event during sustained pushing, with 100+ brownouts in a day; the cause was current-limit fields set to the wrong quantity (supply value written into stator), fixed by correcting stator/supply/slip limits.**
  - bears on: A concrete match-trip case where the root cause was a mis-set current-limit field rather than a hardware fault.
  - https://www.chiefdelphi.com/t/516964

- **Aggressive drive current limits cut one swerve team from about 100 brownouts to two in a match with acceptable driver impact; on the same robot a swerve bearing installed upside down drew a lot of current and overheated its motor.**
  - bears on: Scale of improvement available from limits alone on an 8-NEO swerve, plus a specific assembly defect that raises steer current on one module only -- testable by comparing the eight controllers against each other.
  - https://www.chiefdelphi.com/t/517514

- **In the 2026 season a multi-team mentor reports the distinct REV failure modes as dead spots inside the Vortex and gate driver faults in the SPARK Flex, with fresh 2026 motors failing alongside 2025 units.**
  - bears on: Current-season failure taxonomy for the exact hardware on rig-flex; names the two symptoms to look for when a Flex/Vortex pair degrades rather than dies outright.
  - https://www.chiefdelphi.com/t/517972

- **A Mk5n / Kraken X60+X44 swerve robot tripped a breaker mid-match and shut down, with field staff attributing it to overheating, while running 60 A drive supply and 50 A steer stator limits.**
  - bears on: What an actual in-match breaker trip on a swerve base looks like, and the limit values in force when it happened.
  - https://www.chiefdelphi.com/t/518203

- **Bound-up swerve steering gears made two steer motors stall for an entire match, producing unexplained brownouts while the robot still drove basically normally because worn tread on those modules let the other two overcome them.**
  - bears on: The single best mechanical explanation for steers pegged at 107-150 A while the robot drives fine. A pushed-by-hand freeness check on each azimuth, and a per-module steer current comparison, tests it directly.
  - https://www.chiefdelphi.com/t/518203
