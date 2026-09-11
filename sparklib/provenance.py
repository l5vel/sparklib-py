"""What this driver believes about SPARK hardware, and how each belief was got.

Every decode, parameter id and threshold in this package rests on a claim about
the hardware. Some were measured on a robot, some come from REV's documentation,
and some are inferences from a neighbouring product that nobody has checked. The
three are not interchangeable, and until they are written down they are
indistinguishable in the code that uses them.

They have already been confused once, expensively. `controller` decoded
SPARK Flex frames with the SPARK MAX layout, so a healthy 13.7 V rail read as a
fault bitfield, gating the base's recovery on it wedged the base permanently,
and the workaround was to switch the whole fault path off for Flex. That was a
MAX belief applied to a Flex without anyone recording that it had been.

    HOW                what it means
    hardware           measured on a robot in this fleet, with the run recorded
    vendor             stated by REV or CTRE in documentation or by staff
    inferred           taken from the other product, or from a neighbouring
                       parameter, and NOT checked on this one
    unverified         believed, with no source better than "it seems to work"

`inferred` and `unverified` are the ones that bite. `spark verify` prints them
and gives the command that would settle each.
"""

from __future__ import annotations

from dataclasses import dataclass, field


SPARKMAX = "sparkmax"
SPARKFLEX = "sparkflex"
BOTH = "both"

HARDWARE = "hardware"
VENDOR = "vendor"
INFERRED = "inferred"
UNVERIFIED = "unverified"

_SETTLED = (HARDWARE, VENDOR)


@dataclass(frozen=True)
class Claim:
    """One thing the driver believes, and the evidence for it."""

    key: str
    product: str
    how: str
    what: str
    evidence: str
    settle_with: str = ""
    used_by: tuple = field(default_factory=tuple)

    @property
    def settled(self) -> bool:
        return self.how in _SETTLED


CLAIMS = (
    # -- frame layout --------------------------------------------------------
    Claim(
        key="flex.status0.layout", product=SPARKFLEX, how=HARDWARE,
        what="STATUS_0 (api 0x2E0) is applied output, bus voltage, output "
             "current, motor temperature, then the limit and flag bits",
        evidence="REV-Specs spark-frames-2.1.0, and read off rig-flex on "
                 "; docs/spark/runs/-rig-flex-probe-log.md",
        used_by=("admin.decode_status_0", "controller.bus_voltage"),
 ),
    Claim(
        key="flex.status1.faults", product=SPARKFLEX, how=HARDWARE,
        what="faults and warnings live in STATUS_1 (0x2E1) bytes 0/2/3/5, and "
             "NOT in STATUS_0",
        evidence="proven by the 0x06CE incident: STATUS_0 bytes 2:4 read as a "
                 "fault word returned bus voltage on all eight; docs/ESTOP.md",
        used_by=("admin.decode_status_1", "controller.active_faults"),
 ),
    Claim(
        key="pre25.status0.layout", product=BOTH, how=HARDWARE,
        what="on PRE-25 firmware, Periodic Status 0 (api 0x060) carries applied "
             "output as an int16 then Faults and Sticky Faults, and Periodic "
             "Status 1 (0x061) is velocity, temperature, voltage and current",
        evidence="REV state both frames field by field at docs.revrobotics.com/"
                 "brushless/spark-max/control-interfaces. That page is stamped "
                 "'Last updated 1 year ago' and is the PRE-25 table: it gives "
                 "no arbitration ids, and 0x060-0x062 follow from api class 6. "
                 "Scoped to firmware, not to product -- a pre-25 SPARK Flex is "
                 "on the same frames. "
                 "MEASURED. rig-max, with a fault CAUSED rather than waited "
                 "for. The forward limit was asserted on id 3 and the raw 0x060 "
                 "payload read 0000004010321000: bytes 0:2 applied 0, bytes 2:4 "
                 "0x4000 which is the hard forward limit and nothing else, bytes "
                 "4:6 the sticky word. decode_legacy_status_0 returned the same "
                 "three values and clearing the polarity returned bytes 2:4 to "
                 "0x0000. The sticky half corroborated it independently: it had "
                 "accumulated 0x3210, exactly the sensor, hasReset, softLimitFwd "
                 "and softLimitRev bits caused earlier, and nothing "
                 "else.",
        used_by=("admin.decode_legacy_status_0",
                 "admin.decode_legacy_status_1",
                 "sparksim.frames.encode_status_0_sparkmax"),
 ),
    Claim(
        key="fw25.legacy_beacon", product=BOTH, how=VENDOR,
        what="on firmware 25+, api 0x060 is a fixed presence beacon carrying no "
             "readings: applied output 0, FAULTS_AND_STICKY_FAULTS 0xFFFFFFFF, "
             "other signals 0",
        evidence="REV-spark-frames-2.1.0.json LEGACY_STATUS_0: every signal has "
                 "decodedMin == decodedMax, and REV's own note on the fault "
                 "field is 'Always has all faults set so that old software "
                 "knows that something is wrong'. Scoring those bits reports "
                 "sixteen faults on a healthy controller.",
        settle_with="candump a 25+ controller and check whether 0x060 appears "
                    "at all -- rig-flex's capture contains none",
        used_by=("admin.LEGACY_BEACON_PAYLOAD",
                 "admin.legacy_frame_is_beacon",
                 "admin.normalised_reading"),
 ),
    Claim(
        key="max.param.status_period", product=BOTH, how=VENDOR,
        what="Status 0 Period is parameter 158 and Status 1 Period is 159, both "
             "UINT32 and READ-WRITE, for the SPARK generally rather than for one "
             "product",
        evidence="REV-Specs SparkParameters-v0.1.2, vendored at tests/support/"
                 "spec/REV-SparkParameters-v0.1.2.md:225-226. That "
                 "document is titled 'SPARK Configuration Parameters', says the "
                 "parameters live 'within the SPARK', and its id-153 row names "
                 "BOTH Flex and MAX in one description. REVLib SparkParameters.h "
                 "declares one enum with no product split, inherited unchanged "
                 "by SparkMaxConfig and SparkFlexConfig. THIS CLAIM REPLACES an "
                 "UNVERIFIED entry written earlier asserting no id "
                 "was known for a MAX; that was refuted by the primary source "
                 "sitting in this tree.",
        used_by=("admin.fault_frame", "admin.PARAM_STATUS_1_PERIOD"),
 ),
    Claim(
        key="max.firmware_honours_period_write", product=SPARKMAX, how=HARDWARE,
        what="a pre-25 SPARK MAX honours a STATUS-PERIOD write on api class 6 "
             "(LEGACY_SET_PERIOD). It does not implement PARAMETER_WRITE at all, "
             "so silence there is expected rather than a refusal. Scoped to "
             "status periods; parameter access is a separate path and is covered "
             "by max.param_access_answers_both_ways",
        evidence="rig-max, firmware 24.0.1. Asked for 25 ms on "
                 "STATUS_0 via set_legacy_status_period and measured 25 ms on the "
                 "wire, then restored; reproduced on ids 1 and 4. The same run "
                 "sent PARAMETER_WRITE for parameter 158 and got no response at "
                 "all, as it had on ids 5, 6 and 8. Standing corroboration: the "
                 "fleet broadcasts can_bus._SPARKMAX_STATUS_PERIODS_MS "
                 "(50/100/100/500/1000/1000) rather than REV's pre-25 defaults "
                 "(10/20/20/50/20/200/200), so apply_boot_config's writes have "
                 "been reaching these controllers over CAN all along, and the "
                 "SparkAdmin session that ran the controlled write sends no "
                 "heartbeat, so the write needs no enable. "
                 "tests/hardware/test_legacy_period_write.py pins both halves. "
                 "GRADE THE TWO HALVES DIFFERENTLY. The PARAMETER_WRITE half is "
                 "vendor-settled: REV-spark-frames-2.1.0.json stamps api class 14 "
                 "idx 0 and idx 1 versionImplemented 25.0.0, so 24.0.1 does not "
                 "carry the frame and an unmatched extended id is silently "
                 "dropped. The period-set half is NOT vendor-documented -- api "
                 "class 6 in that same spec holds only Legacy Status 0 and Clear "
                 "Faults, with no set-period frame at any index -- so it rests on "
                 "this measurement plus REVLib's own 1.1.5-era source and the "
                 "independent grayson-arendt/sparkcan driver, which documents "
                 "working on 24.0.X and breaking on 25.0.X.",
        used_by=("cli.cmd_repair",),
 ),
    Claim(
        key="both.frames_split_on_firmware", product=BOTH, how=HARDWARE,
        what="the frame layout is decided by FIRMWARE VERSION, not by product: "
             "api 0x060 is LEGACY_STATUS_0, deprecated at 25.0.0, and every "
             "SPARK on 25+ broadcasts STATUS_0..8 on 0x2E0..0x2E8 with all "
             "faults in STATUS_1",
        evidence="REV-spark-frames-2.1.0.json. Its deviceInfo block carries NO "
                 "product field. Every frame carries versionImplemented; "
                 "LEGACY_STATUS_0 alone carries versionDeprecated 25.0.0, and "
                 "REV describe it as existing 'purely to inform old software "
                 "that is not aware of firmware version 25+ that the SPARK is "
                 "present'. LEGACY_STATUS_1 and _2 appear nowhere in the spec. "
                 "MEASURED. rig-max. GET_FIRMWARE answered 24.0.1 on id 3, and "
                 "every frame that id broadcast over the next four seconds was "
                 "api class 6 -- 0x060, 0x061, 0x062, 0x063, 0x065, 0x066 and "
                 "0x067 -- with no 0x2E0 anywhere. The split is on firmware and "
                 "this fleet sits on the pre-25 side of it.",
        used_by=("admin.API_SETS", "admin.fault_frame"),
 ),
    Claim(
        key="both.status_8_9_param_ids", product=BOTH, how=VENDOR,
        what="Status 8 Period is parameter 199 and Status 9 Period is 224; they "
             "do NOT continue the 158-165 run",
        evidence="REVLib SparkParameters.h:190 and:215, preserved at "
                 "tests/support/spec/revlib-2026.0.2/. Parameters 166 and "
                 "167 are MAXMotion Max Velocity 0 and Max Accel 0, both FLOAT, "
                 "so the obvious continuation writes a period into a motion "
                 "limit. rev_parameter_index.tsv stops at 198 and cannot show "
                 "this.",
        used_by=("sparksim.frames.PERIOD_PARAM_FOR_API",),
 ),
    Claim(
        key="pre25.drive_overcurrent_under_load", product=SPARKMAX, how=HARDWARE,
        what="a high-speed run latches sticky overcurrent on EVERY drive "
             "controller and on no steer controller, without rebooting any of "
             "them. It does NOT silence the bus, and the tooling's advice that "
             "it might is wrong for this case",
        evidence="rig-max, after a drive test at scale 0.99. Read "
                 "back on all eight: ids 1, 4, 5 and 8 hold sticky overcurrent "
                 "and id 4 also holds gateDriver; ids 2, 3, 6 and 7 hold "
                 "nothing. The faulted set IS the drive set. reset_kind is "
                 "'none' on all eight, so nothing browned out and rebooted. "
                 "Per-controller current spans in those runs reached 54 A. "
                 "THE BUS WENT DARK AT THE SAME MOMENT AND THAT WAS SOMETHING "
                 "ELSE. cli tells the operator a latched fault 'stops a "
                 "controller broadcasting AND ACKing, which can silence the "
                 "whole bus'. Four of eight were latched here and all eight "
                 "went dark, which that story does not explain. Silence exactly "
                 "the four that faulted in the simulator and the other four keep "
                 "broadcasting: "
                 "tests/adversarial/test_rig_max_drive_overcurrent_silence.py. "
                 "The real cause was the CAN adapter leaving the USB bus, see "
                 "rig-max.gs_usb_adapter_will_not_stay_enumerated. "
                 "docs/spark/runs/-rig-max-drive-overcurrent-and-adapter-flap.md",
        settle_with="MEASURED TWICE, AND THE GATE-DRIVER HALF IS GETTING "
                    "WORSE. At 18:48:36 one controller held gateDriver, id 4. "
                    "After further driving, at 19:16:02, THREE held it -- ids 1, "
                    "5 and 8 -- and id 4 did not. So it is not one unit "
                    "misbehaving; it recurs across the drive half under load and "
                    "moves between units. Both readings are exactly the drive "
                    "set with reset_kind 'none' throughout. "
                    "IT IS NOT THE ADAPTER. That fault is a mechanically "
                    "intermittent USB connector carrying no motor current, and "
                    "it cannot latch a gate driver. "
                    "WHAT IS NOT ESTABLISHED is whether this is tuning or "
                    "hardware. The Smart Current Stall Limit on this fleet is "
                    "40 A against REV's 80, the one setting that deviates, and "
                    "nothing here has related the two. A gate-driver latch that "
                    "regenerates after a clear is an RMA on this hardware and a "
                    "transient one is not; these cleared and came back under "
                    "load, which is a third case. "
                    "NEXT: read the faults after a run at reduced scale. If the "
                    "gate-driver latches track drive scale they are load, and if "
                    "they persist at low output they are hardware.",
        used_by=("cli.cmd_clear pre-clear record",
                 "admin.audit_problems"),
 ),
    Claim(
        key="rig-max.gs_usb_adapter_will_not_stay_enumerated", product=SPARKMAX,
        how=HARDWARE,
        what="the CANable gs_usb adapter is MECHANICALLY INTERMITTENT and "
             "leaves the USB bus when disturbed. A finger flick reproduces it "
             "on demand. It is not a driver wedge, so `spark canfix` cannot fix "
             "it and neither can a replug",
        evidence="rig-max, failing mid high-speed run as OSError "
                 "errno 100 Network is down. can_diag showed the CAN side "
                 "healthy throughout: link UP, ERROR-ACTIVE, every controller "
                 "error counter zero, no error frames in an 8 s watch. "
                 "THE KERNEL LOG NAMES IT. Ten `usb xmit fail` in 20 ms, then "
                 "`usb 3-1.3: USB disconnect` 28 ms after the last one, so the "
                 "transmit failures and the disconnect are ONE event: the "
                 "device leaving the bus. Over the session it burned through "
                 "seven device numbers -- 5, 8, 9, 10, 11, 12, 15 -- with three "
                 "`device descriptor read/64, error -32` stalls during "
                 "enumeration. One instance survived 0.54 s before "
                 "disconnecting again, another 2.34 s. "
                 "NOT THE MACHINE OR THE HUB: the CANivore at 3-1.1 on the same "
                 "hub enumerated cleanly every time. "
                 "IT EXPLAINS THE APPARENT RANDOMNESS. The operator read the "
                 "recovery as depending on whether `spark clear` or `spark "
                 "status` ran first. With instances lasting under a second, "
                 "whether any command worked depended on landing inside a good "
                 "window. Two clears did nothing, a third was refused with "
                 "'gsusb does not exist', and the fourth recovered all "
                 "eight straight after the adapter came back. "
                 "REPRODUCED ON DEMAND 19:13, which identifies "
                 "the cause. With tools/can_usb_watch.py running and the drive "
                 "stack up at 495 tx/s and 1330 rx/s, the operator FLICKED THE "
                 "ADAPTER WITH A FINGER. It dropped at that instant with the "
                 "identical signature: ten numbered transmit failures inside "
                 "200 ms, `USB disconnect`, then a clean re-enumeration 300 ms "
                 "later. The CANivore control saw nothing through the same "
                 "window. "
                 "SO IT IS A PHYSICALLY INTERMITTENT CONNECTION at the adapter, "
                 "not EMI, not ground shift, not transmit saturation and not "
                 "firmware. A high-speed run shakes the robot, which is the "
                 "same stimulus applied for longer, and that is why it failed "
                 "mid-run and why an earlier identical run survived. "
                 "docs/spark/runs/-rig-max-drive-overcurrent-and-adapter-flap.md",
        settle_with="CAUSE IDENTIFIED, HARDWARE NOT YET REPLACED. Replace the "
                    "adapter, or its cable, or resolder its connector. Nothing "
                    "in software fixes an intermittent joint, and every recovery "
                    "that appeared to work -- canfix, a replug, a particular "
                    "command order -- was the connection happening to make "
                    "contact again. "
                    "THE FASTEST INDICATOR NEEDS NO SOFTWARE: the adapter's "
                    "TX LED. If the host is sending and that LED is dark, the "
                    "frames are not reaching the adapter and the fault is the "
                    "USB link, not the CAN bus. That is what the operator "
                    "noticed first. "
                    "`spark status` and `spark clear` now read the kernel log "
                    "when nothing answers, through "
                    "cli.adapter_instability_note, and report "
                    "disconnects, transmit failures, enumeration stalls and "
                    "re-enumerations in the last half hour before blaming the "
                    "robot. They stay silent on a healthy machine and a plain "
                    "canfix rebind does not trip them. "
                    "THERE IS NOW AN ACCEPTANCE TEST. With "
                    "tools/can_usb_watch.py running, flick the adapter. A "
                    "healthy one does not notice. Use it on the replacement "
                    "before trusting it, and on this one to confirm the "
                    "diagnosis independently. "
                    "THE DIAGNOSTIC GAP IS FIXABLE AND IS NOT FIXED. No `spark` "
                    "command consults the kernel log when the bus is empty, "
                    "though can_diag already knows how. A bus answering nothing, "
                    "with an adapter that re-enumerated seven times in the last "
                    "hour, should say so instead of sending the operator to "
                    "check motor power and the CAN chain. That advice cost an "
                    "hour here while a meter had already shown 12.20 V at the "
                    "panel and every controller LED was lit.",
        used_by=("cli.adapter_instability",
                 "cli.adapter_instability_note",
                 "cli._require_working_adapter", "cli._adapter_state",
                 "cli.cmd_canfix", "tools/can_diag.py",
                 "tools/can_usb_watch.py"),
 ),
    Claim(
        key="both.volt_scale_reads_low", product=BOTH, how=HARDWARE,
        what="REV's published 0.0073260073260073 V per count reads about 5 "
             "percent LOW against a meter. decode_status_0 and "
             "decode_legacy_status_1 both use it, through VOLT_PER_COUNT, and "
             "the true figure sits near 1/128",
        evidence="rig-max, at rest with the motors disabled and all "
                 "eight reporting 0.00 A, so there is no wire drop to argue "
                 "about. Multimeter at the battery terminals: 12.20 V. "
                 "`spark voltage` at the same time: 11.55 11.57 11.44 11.55 "
                 "11.70 11.50 11.44 11.60, mean 11.544 V shown, which is 1575.7 "
                 "raw counts. "
                 "THE SCALE THAT MAKES THE GAP ZERO IS 0.00774248 V per count, "
                 "or 1/129.2. REV's published figure is 5.4 percent below that "
                 "and 1/128 is 0.9 percent above it. "
                 "THE COMPETING EXPLANATION IS WIRING, and the robot's own "
                 "behaviour rules it out. If cable resistance drops 0.66 V at "
                 "rest, the same resistance governs the sag while driving. The "
                 "fourth driving run sagged the rail 2.249 V with per-controller "
                 "phase-current spans summing to 223 A, so the feed is on the "
                 "order of 11 to 45 milliohms depending on what fraction of "
                 "phase current the supply carries. At 1 A of resting draw that "
                 "is 0.01 to 0.05 V, not 0.66 V. Run it backwards and it is "
                 "worse: 0.66 V at 1 A means 0.66 ohm, which would drop 6.6 V "
                 "at only 10 A and the base could not move at all. To make 0.66 "
                 "V at rest at the resistance a 100 A load implies would take "
                 "29 A of resting current. "
                 "THE REPORTED 0.00 A DOES NOT SETTLE THAT ON ITS OWN, because "
                 "a SPARK measures phase current and not its own supply draw, "
                 "so quiescent current is real but unreported. The argument "
                 "above does not depend on the reported figure; it depends on "
                 "the resistance the driving sag implies. "
                 "THE BATTERY-TO-PDP LEG DROPS NOTHING, measured the same "
                 "day: the meter reads 12.20 V at the battery terminals and "
                 "12.20 V at the PDP input. So any drop has to appear across "
                 "the PDP-to-controller leg alone, and that leg would need 330 "
                 "to 1320 milliohms to make 0.66 V at a resting draw of 2 A "
                 "down to 0.5 A. At 50 A the same leg would then drop 16 to 66 "
                 "V. A single short run cannot be an order of magnitude more "
                 "resistive than the whole feed the driving sag implies. "
                 "The 0.061 V measured across a controller's MOTOR OUTPUT leads "
                 "at rest is the output side and carries no supply information; "
                 "it confirms only that the controller is disabled. "
                 "A FIXED-DROP ELEMENT would evade the resistance argument, but "
                 "a series diode at these currents dissipates tens of watts and "
                 "a stuck-open precharge resistor would stop the base driving. "
                 "1/128 needs per-controller offset up to +0.28 V instead, and "
                 "the fleet already disagrees with itself by 0.26 V, so that "
                 "magnitude is already demonstrated on this hardware. The two "
                 "lowest controllers, ids 3 and 7, land on 12.20 V exactly "
                 "under 1/128. "
                 "IT COSTS STATE OF CHARGE. rail_usable_pct on the shown 11.544 "
                 "V gives 47 percent of usable range; on the measured 12.20 V "
                 "it gives 77 percent. The robot reports itself far nearer "
                 "empty than it is. "
                 "PROVENANCE OF THE TWO FIGURES: 1/128 is what controller "
                 "used until, when it was replaced by REV's "
                 "control-interfaces figure on the grounds that it read about "
                 "6.6 percent high. That reasoning assumed REV's figure was "
                 "right and nothing had measured it. "
                 "CORROBORATED INDEPENDENTLY by the CTRE PDP 4.0 on the same "
                 "bus: byte 6 of apis 0x052, 0x055 and 0x058 has a raw mean of "
                 "159.8, which under CTRE's published raw*0.05+4.0 reads 11.99 "
                 "V where the controllers showed 11.28 V, implying 0.0077871 V "
                 "per count. See rig-max.foreign_traffic_on_the_spark_bus. "
                 "docs/spark/runs/-rig-max-post-rail-cycle.md",
        settle_with="MEASURED but NOT YET ACTED ON, and the driver still uses "
                    "REV's figure. Two things stand between this and a change. "
                    "ONE MEASUREMENT ENDS THE WIRING ARGUMENT WITHOUT ANY "
                    "ARITHMETIC: put the meter on a single controller's own "
                    "power input terminals, at rest, and compare it against "
                    "that same controller's reported volts. Reading 12.20 there "
                    "means no drop and the scale is wrong; reading about 11.5 "
                    "means the drop is real and REV's figure stands. It is the "
                    "same 0-20 V measurement, moved twelve inches. And the 0.9 "
                    "percent residual against 1/128 is unexplained, so the "
                    "right constant may be neither published figure. "
                    "Changing VOLT_PER_COUNT moves everything calibrated on it, "
                    "including rail_usable_pct, the SLA full and empty "
                    "thresholds in battery.py and every brownout margin, so it "
                    "is a deliberate change and not a constant swap. "
                    "THE AMP SCALE IS UNTOUCHED BY THIS. It wants a DC clamp "
                    "meter past 60 A and no reading here constrains it, because "
                    "the panel measures supply current where a controller "
                    "reports phase current.",
        used_by=("admin.VOLT_PER_COUNT", "admin.decode_status_0",
                 "admin.decode_legacy_status_1",
                 "battery.rail_usable_pct", "spark voltage"),
 ),
    Claim(
        key="both.param_read_frames", product=BOTH, how=HARDWARE,
        what="parameter reads have dedicated frames: READ_PARAMETER_n_AND_n+1 "
             "across apiClasses 15-22 covering all of 0-255, and "
             "GET_PARAMETER_n_TO_n+15_TYPES on apiClass 13, both separate from "
             "PARAMETER_WRITE on apiClass 14",
        evidence="REV-spark-frames-2.1.0.json defines 128 read-pair frames "
                 "covering ids 0-255, all versionImplemented 25.0.0. Their "
                 "description records one product caveat: 'SPARK MAX does not "
                 "currently support this in v25.0.0-prerelease.4'. "
                 "SparkAdmin.read_param used to send a one-byte frame on the "
                 "WRITE api, which is not in the spec; it was deleted. "
                 "MEASURED TWICE on the pre-25 half. rig-max: all 32 "
                 "frames on "
                 "apiClass 13 and 19 are versionImplemented 25.0.0 and rtr true "
                 "in the spec, so none should answer on 24.0.1. Read Parameter "
                 "158 and 159 (class 19) and Get Parameter 0 to 15 Types (class "
                 "13) were each sent to id 3 BOTH as a genuine remote frame and "
                 "as a zero-length data frame. Four sends, no answer to any, "
                 "while the legacy dialect answers the same parameters on the "
                 "same controller. "
                 "CONFIRMED across the FORM axis on rig-max, after a "
                 "rail cycle: tools/spark_read_frame_form.py sent both apis to "
                 "id 3 as a zero-length data frame, as a remote frame with dlc "
                 "0 and as a remote frame with dlc 8. All six sends drew "
                 "silence. That matters because dlc 8 is the only form 26.1.6 "
                 "answers, so the earlier four sends had used forms now known "
                 "to be silent on a Flex and could not have distinguished an "
                 "absent frame from a badly formed one. The frames are 25+ only "
                 "and pre-25 needs the legacy path. "
                 "docs/spark/runs/-rig-max-post-rail-cycle.md",
        settle_with="SETTLED on BOTH generations. 25+, rig-flex: 26.1.6 "
                    "answers these as remote frames with dlc 8 and ignores both "
                    "the zero-length data frame and a remote frame with dlc 0. "
                    "Pre-25, rig-max: id 3 was sent READ_PARAMETER "
                    "class 19 and GET_PARAMETER_TYPES class 13 in ALL THREE "
                    "forms and answered none of them. So '25+ only' is measured "
                    "across the form axis and no longer rests on two sends that "
                    "happened to use a silent form. "
                    "docs/spark/runs/-rig-max-post-rail-cycle.md",
        used_by=("admin.read_param_pair", "admin.param_types",
                 "tools/spark_param_sweep.py"),
 ),
    Claim(
        key="both.status_period_unit", product=BOTH, how=HARDWARE,
        what="a status period parameter is in MILLISECONDS. REV-Specs' "
             "'Status frame 0 period, in us' is a documentation error",
        evidence="MEASURED on rig-flex with a control group. Parameter "
                 "159 written 20 -> 50 on id 11 alone: its STATUS_1 cadence went "
                 "20.0 -> 50.0 ms while the seven other controllers held 20.0 ms "
                 "throughout, then returned to 20.0 ms on restore. 50 us would "
                 "be 20 kHz and is not achievable on a 1 Mbit bus. REVLib doc "
                 "comments and REV's SPARK MAX page both say milliseconds, range "
                 "1-32767 ms, and they are right. "
                 "docs/spark/runs/-rig-flex-hardware.md",
        used_by=("admin.declared_status_1_period_ms",
                 "admin.fault_frame", "admin.write_param"),
 ),
    Claim(
        key="flex.writes_answered_reads_not", product=SPARKFLEX, how=HARDWARE,
        what="firmware 26.1.6 answers PARAMETER_WRITE with a "
             "PARAMETER_WRITE_RESPONSE, so a write is acknowledged. The second "
             "half of this claim, that reads are not verifiable, was withdrawn on "
             ": both directions are verifiable now",
        evidence="rig-flex: write of 159=50 to id 11 returned result 0 "
                 "Success with verified=True on the first attempt, and the "
                 "cadence changed on the wire. The same session got no answer "
                 "from apiClass 19, 13 or the one-byte read on 14. "
                 "CORRECTED: those first two were sent as zero-length "
                 "data frames, and as remote frames with dlc 8 they answer. So a "
                 "read-back after a write no longer has to measure the observable "
                 "and can read the parameter, which matters most where there IS "
                 "no observable -- a Status 2-9 period that nothing broadcasts. "
                 "The write echo alone is not enough: see "
                 "flex.bool_params_store_out_of_range_values. "
                 "docs/spark/runs/-rig-flex-hardware.md",
        used_by=("admin.write_param", "admin.status_period_ms"),
 ),
    Claim(
        key="pre25.status0.period_default", product=BOTH, how=VENDOR,
        what="on PRE-25 firmware Periodic Status 0 broadcasts every 10 ms by "
             "default, which is also the value this audit scores it against",
        evidence="REV's SPARK MAX control-interfaces page gives 10 ms. That "
                 "page is the pre-25 table and trails REV-Specs by about a "
                 "year, so the figure is scoped to pre-25 rather than to a "
                 "product. Because provisioned and factory are the same number "
                 "here, pre-25 cannot produce the 'reverted' verdict -- the "
                 "audit scores 'ok' first, so a healthy 10 ms is not reported "
                 "as a lost config. "
                 "MEASURED. NOT settled, and rig-max cannot settle it as things stand. "
                 "Measured there at a median 50.0 ms, which is "
                 "exactly what this package's own boot throttle writes for "
                 "Status 0. That confirms the throttle reaches the hardware and "
                 "says nothing about the factory value, because every path that "
                 "opens the bus applies it first.",
        settle_with="candump a pre-25 controller in the window between a motor "
                    "rail cycle and the first run of anything that calls "
                    "apply_boot_config, and compare against 10 ms",
        used_by=("admin.fault_frame", "admin.status_1_verdict"),
 ),
    Claim(
        key="max.unique_id.api", product=SPARKMAX, how=HARDWARE,
        what="UNIQUE_ID is api 0x2F0 on a SPARK MAX running firmware 25+, as "
             "it is on a Flex, and a pre-25 controller sends no UNIQUE_ID at all",
        evidence="REV-spark-frames-2.1.0.json UNIQUE_ID_BROADCAST, "
                 "versionImplemented 25.0.0, names the product directly: 'the "
                 "SPARK Flex firmware will send this at an irregular period "
                 "between 1000ms and 2000ms. SPARK MAX may use a constant "
                 "period of 1000ms.' REV describing MAX behaviour on a frame "
                 "implemented at 25.0.0 also places the MAX on the 25+ set. "
                 "Documented, not measured: never seen on a MAX bus, and every "
                 "serial-addressed command depends on it. "
                 "MEASURED. rig-max. Four seconds of listening caught ZERO "
                 "frames on api 0x2F0 from any of the eight, while a zero-length "
                 "request to api 0x094 on id 3 returned EABE4C2E, matching the "
                 "fingerprint base.can_serials records for that corner. The "
                 "identity exists and is readable on this generation; it is "
                 "simply never broadcast.",
        used_by=("admin.api_set", "admin.duplicates"),
 ),

    # -- parameters ----------------------------------------------------------
    Claim(
        key="flex.param.59.stall_limit", product=SPARKFLEX, how=HARDWARE,
        what="parameter 59 is Smart Current Stall Limit, factory default 80 A",
        evidence="MEASURED on rig-flex: parameter 59 reads 80 on all "
                 "eight controllers over READ_PARAMETER. REV's parameters page "
                 "for the MAX gives the same id and default, and "
                 "the `sparkflex:` block of spark.yaml declares it. "
                 "records/flex-param-sweep-fleet-20260909.json",
        used_by=("admin.BIT_REMEDIES",),
 ),
    Claim(
        key="flex.param.11.current_chop", product=SPARKFLEX, how=HARDWARE,
        what="parameter 11 is kCurrentChop, factory default 115 A, and it "
             "disables the half bridge for kCurrentChopCycles",
        evidence="MEASURED on rig-flex: parameter 11 reads 115.0 on all "
                 "eight controllers over READ_PARAMETER, so the id and the value "
                 "are confirmed on a Flex rather than carried over from the MAX "
                 "table. sparkflex_motor_defaults.yaml declares the same, and "
                 "docs.revrobotics.com/brushless/spark-max/parameters gives the "
                 "behaviour, which is vendor evidence and stays that way. "
                 "records/flex-param-sweep-fleet-20260909.json",
        used_by=("tests/support/sparksim/bus.current_chop",),
 ),
    Claim(
        key="max.startup_drive_gate_decodes_the_wrong_generation", product=SPARKMAX,
        how=HARDWARE,
        what="the swerve startup gate that refuses to drive a hard-limited "
             "controller decodes pre-25 frames with the firmware-25 layout, so on "
             "rig-max it cannot see the fault word at all and passes a controller "
             "it exists to stop",
        evidence="Verified from code and demonstrated on a frame "
                 "captured off rig-max. swerve_drive.py, in the "
                 "controllers-can-drive check, calls admin.decode_status_0 "
                 "and decode_status_1 directly on Controller._status0_raw with no "
                 "generation branch. can_bus.py deliberately fills that buffer "
                 "from EITHER 0x060 (pre-25) or 0x2E0 (fw25), and "
                 "controller.py already handles the split correctly by "
                 "calling decode_legacy_status_0 -- this call site does not. "
                 "Real captured 0x060 payload 0000000000021000 from a healthy idle "
                 "controller decodes correctly as active_faults 0x0000 and sticky "
                 "0x0200 (hasReset). Through decode_status_0 the same bytes read "
                 "as voltage 0.0 V, current 0.0 A, motor temp 2 C, both hard "
                 "limits False and implausible empty -- so nothing fires. The "
                 "pre-25 fault word lives in bytes 2:6, which the fw25 decoder "
                 "reads as bus voltage and output current, and the pre-25 hard "
                 "limit bits are 14 and 15 of that word rather than flags in byte "
                 "6. The gate is not misreporting on rig-max; it is blind, and a "
                 "0.0 V rail does not even trip the implausible check. "
                 "This is the exact defect class this file's own header describes "
                 "as having been expensive once already.",
        settle_with="FIXED, with a behavioural test written first "
                    "that reproduced the defect. admin.reading_from_raw "
                    "now turns a pair of buffered frames into one normalised "
                    "reading for the generation they actually are, and the gate "
                    "goes through it; the same commit made the gate's remedy "
                    "text generation-aware, because it was appending 25+ advice "
                    "under the findings as well. Pinned by "
                    "tests/unit/test_drive_gate_generation.py, which asserts a "
                    "pre-25 controller held by a hard limit and one with a stall "
                    "fault are both refused, that a healthy one carrying only "
                    "sticky hasReset is not, and that the firmware-25 path is "
                    "unchanged. tests/unit/test_generation_advice.py was extended "
                    "to cover the gate, since the class guard had not reached it. "
                    "REMAINING: this was proved in the simulator and against a "
                    "captured frame, not by holding a real rig-max controller at "
                    "its hard limit. The decode is measured; the end-to-end "
                    "refusal on hardware is not.",
        used_by=("swerve_drive controllers-can-drive startup gate",),
 ),
    Claim(
        key="flex.param_reads_were_probed_as_data_frames", product=SPARKFLEX,
        how=HARDWARE,
        what="firmware 26.1.6 DOES answer parameter reads. The belief that it "
             "answered none was an artefact of the frame FORM: the request has to "
             "be a genuine remote frame carrying dlc 8, and this package sent "
             "zero-length data frames",
        evidence="MEASURED on rig-flex, eight SPARK Flex on 26.1.6, "
                 "gsusb at 1 Mbit classic CAN. Three forms of the same "
                 "READ_PARAMETER request went to id 17 for the same parameter and "
                 "exactly one was answered. The zero-length DATA frame, which is "
                 "what read_param_pair and param_types built until this date, drew "
                 "silence. A genuine remote frame with dlc 0 drew silence. A "
                 "genuine remote frame with dlc 8 answered, with an eight-byte "
                 "data frame on the request's own arbitration id. "
                 "COVERAGE. All eight read apiClasses answer, not only 19. A full "
                 "sweep of id 17 sent all 16 type frames and all 128 value frames "
                 "with ZERO silent frames, reading 256 type slots of which 185 are "
                 "implemented, and the same sweep on all eight controllers "
                 "answered every frame. Every controller returned its own CAN id "
                 "at parameter 0, which is the self-check anchor. Captures: "
                 "records/flex-param-sweep-id17-20260909.json and "
                 "records/flex-param-sweep-fleet-20260909.json. "
                 "INDEPENDENTLY CROSS-CHECKED against a different instrument. "
                 "Parameters 158 and 159 read 10 and 20, and a 20 s passive frame "
                 "census measured STATUS_0 at 10 ms and STATUS_1 at 20 ms on all "
                 "eight. The parameter and the wire agree. "
                 "THE ASYMMETRY IS MEASURED, NOT EXPLAINED. GET_FIRMWARE is rtr "
                 "true with lengthBytes 8 in the same spec, and firmware() sends "
                 "it with dlc 0 and has been answered on 26.1.6 all along. The dlc "
                 "rule holds for the parameter read classes and does not "
                 "generalise to every frame REV mark rtr. "
                 "WHAT WAS NOT VARIED. dlc 1 through 7 were never sent, so dlc 8 "
                 "is a form that works and not provably the only one. "
                 "WHY A GREEN SUITE NEVER CAUGHT IT. The simulator dispatched on "
                 "arbitration id alone and answered a read in whatever form "
                 "arrived, while checking the form for GET_FIRMWARE in the same "
                 "file. It manufactured a bus that answers the frames this driver "
                 "sent. Fixed, and three mutations of the driver are "
                 "now caught by tests/adversarial/test_param_read_frames.py. "
                 "This is the same shape as pre25.burn_flash_api, where a "
                 "zero-length probe drew silence and was read as the command not "
                 "existing, until the correctly-formed frame answered. "
                 "docs/spark/runs/-rig-flex-flex-parameter-reads.md",
        settle_with="SETTLED on rig-flex, and the answer is narrower "
                    "than this claim predicted. Three forms of the same request "
                    "went to id 17 and exactly one was answered: a zero-length "
                    "DATA frame drew silence, a remote frame with dlc 0 -- what "
                    "this settle_with told a future run to send -- ALSO drew "
                    "silence, and a remote frame with dlc 8 answered. So "
                    "is_remote_frame=True is necessary and not sufficient. "
                    "REMAINING, none of it load-bearing: dlc 1 through 7 were "
                    "never sent, so dlc 8 is a form that works rather than the "
                    "only one that could.",
        used_by=("admin.read_param_pair", "admin.param_types",
                 "admin.modern_deviation_problems",
                 "admin.coverage_note", "spark params",
                 "tools/spark_param_sweep.py"),
 ),
    Claim(
        key="flex.param.61.limit_rpm", product=SPARKFLEX, how=HARDWARE,
        what="parameter 61 is Smart Current Config, default 10000 RPM, and a "
             "value above free speed disables the stall-to-free taper",
        evidence="MEASURED on rig-flex, all eight controllers over "
                 "READ_PARAMETER: parameter 59 reads 80, 60 reads 20 and 61 reads "
                 "10000 on every one, which is exactly the triple REV's SPARK MAX "
                 "parameters page gives. REV publish NO Flex parameter table, so "
                 "this had no Flex source until the read frame was fixed. 10000 "
                 "RPM sits above NEO Vortex free speed of 6784, so the "
                 "stall-to-free taper is disabled and the limit is a flat 80 A at "
                 "every speed on this fleet. Parameter 61 is still absent from "
                 "sparkflex_motor_defaults.yaml, so the audit does not compare it. "
                 "records/flex-param-sweep-fleet-20260909.json",
        settle_with="SETTLED over CAN, so the USB-C route this "
                    "settle_with asked for was never needed. What is still open "
                    "is the MEANING: 10000 is read off the controller, and that "
                    "a value above free speed disables the taper is REV's "
                    "statement about the SPARK MAX and stays vendor evidence.",
        used_by=("README-SPARK-GUIDE.md section 4",),
 ),
    Claim(
        key="flex.param.50.polarity_encoding", product=SPARKFLEX, how=HARDWARE,
        what="Limit Switch Polarity 0 means normally closed and 1 means "
             "normally open",
        evidence="MEASURED on rig-flex id 17, steer/LB, with the "
                 "motors disabled and no heartbeat on the bus. A steer "
                 "controller's data-port inputs are unwired by doctrine, so an "
                 "unwired input rests high on the SPARK's own pull-up and no "
                 "switch state confounds the reading. Caused and reversed on "
                 "demand, which is the standard bits 14 and 15 were held to on "
                 "rig-max: read p50=0 p51=0 with no limit reached, wrote both to 1 "
                 "and read p50=1 p51=1 with FWD and REV both reached, wrote both "
                 "back to 0 and read 0 with the limits clear. Polarity 0 reads an "
                 "open input as not reached, so 0 is normally closed and 1 is "
                 "normally open. "
                 "The restore was proved by READING the parameter back, which the "
                 "fixed read frame made possible; before it, the write echo was "
                 "the only instrument and it is not trustworthy here -- see "
                 "flex.bool_params_store_out_of_range_values. Nothing was "
                 "persisted, so flash still holds 0. "
                 "docs/spark/runs/-rig-flex-flex-parameter-reads.md",
        settle_with="measured on ONE controller. The other seven read p50=0 and "
                    "p51=0 and were not injected, so the encoding is proven on id "
                    "17 and assumed uniform across a fleet on identical firmware.",
        used_by=("tools/spark_limit_polarity_repair.py",
                 "swerve_drive.LIMIT_SWITCH_POLARITY"),
 ),

    # -- behaviour -----------------------------------------------------------
    Claim(
        key="flex.read_classes_cover_the_whole_table", product=SPARKFLEX,
        how=HARDWARE,
        what="READ_PARAMETER is eight apiClasses, 15 through 22, covering "
             "parameters 0-255 in pairs. One base plus (param_id // 2) << 6 "
             "addresses every one of the 128 frames",
        evidence="MEASURED on rig-flex. A full sweep of id 17 sent all "
                 "128 value frames and all 16 apiClass 13 type frames and every "
                 "one answered: zero silent frames, 256 type slots read, 185 ids "
                 "implemented. Repeated on all eight controllers with the same "
                 "result. The arbitration ids were derived from the vendored spec "
                 "and checked against it exhaustively -- 0x02053C00 + "
                 "((param_id // 2) << 6) reproduces all 128 spec arbIds with no "
                 "mismatch -- so the run addressed the frames REV document. "
                 "THE BOUND IS LOAD-BEARING. One index past the last read frame "
                 "is WRITE_PARAMETER_0_AND_1 at 0x02055C00, which writes the "
                 "device's own CAN id, and one index past the last types frame is "
                 "PARAMETER_WRITE. Both bounds are checked before the index is "
                 "computed, and a mutation that moves either check is caught by "
                 "tests/adversarial/test_param_read_frames.py. "
                 "records/flex-param-sweep-fleet-20260909.json",
        settle_with="the pre-25 side is NOT covered. rig-max was only ever sent "
                    "two of these ids, in two forms now known to be silent on a "
                    "Flex, so what 24.0.1 does with 142 of them is unmeasured.",
        used_by=("admin.READ_PARAM_BASE", "admin.read_param_pair",
                 "spark params", "tools/spark_param_sweep.py"),
 ),
    Claim(
        key="flex.bool_params_store_out_of_range_values", product=SPARKFLEX,
        how=HARDWARE,
        what="a BOOL parameter on 26.1.6 accepts and STORES a value outside "
             "0/1, and the stored value behaves as the permissive one. A write "
             "of 2 to Limit Switch Fwd Polarity returns Success and reads back "
             "as 2, and the forward limit then reads NOT reached",
        evidence="MEASURED on rig-flex id 17, motors disabled and no "
                 "heartbeat on the bus. With parameter 51 held at 1 as an "
                 "untreated control inside the same sample: p50 written 2, "
                 "echoed 2, READ BACK 2, forward limit not reached; p51 = 1, "
                 "reverse limit reached. So 2 behaves as 0 on the forward limit, "
                 "and 0 is the permissive value on this robot's wiring. "
                 "WHY THIS MATTERS. On a robot whose data port needs polarity 1, "
                 "a corrupted or mistyped polarity write silently RELEASES the "
                 "interlock while reporting Success. That is the fail-dangerous "
                 "direction. tools/spark_limit_polarity_repair.py had recorded "
                 "the Success-and-echo-2 behaviour and read it as the echo lying, "
                 "because the echo was the only instrument available. The read "
                 "says the firmware really stores it. "
                 "The write was restored to 0 and the restore was proved by a "
                 "read. Nothing was persisted. "
                 "docs/spark/runs/-rig-flex-flex-parameter-reads.md",
        settle_with="only parameter 50 was probed, with only the value 2. Whether "
                    "every BOOL parameter behaves this way, and what a large "
                    "value does, is untested and does not need testing on an "
                    "interlock. The rule that follows is enough: read a BOOL "
                    "parameter back after writing it.",
        used_by=("tools/spark_limit_polarity_repair.py",
                 "swerve_drive.LIMIT_SWITCH_POLARITY"),
 ),
    Claim(
        key="rig-flex.no_frc_heartbeat_on_the_spark_bus", product=SPARKFLEX,
        how=HARDWARE,
        what="nothing on rig-flex's SPARK bus sends the FIRST universal heartbeat "
             "0x01011840, or the arbitration-id-0 disable broadcast. The enable "
             "story is the secondary heartbeat at 0x02052C80 that SparkBus "
             "asserts, exactly as the code assumes",
        evidence="MEASURED on rig-flex. Thirty seconds of passive "
                 "listening with nothing transmitted caught 36156 frames across "
                 "24 arbitration ids, and neither 0x01011840 nor id 0 appeared. "
                 "rig-flex carries no roboRIO, and no code in this package sends "
                 "either frame. "
                 "AND WHILE DRIVING. One steer module was "
                 "commanded 0.15 for 2.5 s with a second reader counting every "
                 "arbitration id: 6294 frames across 42 ids, and 0x01011840 "
                 "never appeared. The secondary heartbeat 0x02052C80 was present "
                 "throughout at the 20 ms the code asserts. "
                 "Arbitration id 0 DID appear, exactly three times, and they were "
                 "this script's own teardown: broadcast_disable sends the "
                 "zero-length broadcast three times (can_bus.py). Counting "
                 "those as a foreign device would have been a false finding. "
                 "The frame count is itself the record of flex.wake_by_read: the "
                 "controllers broadcast for the whole listen because earlier "
                 "reads had woken every gated transmitter.",
        settle_with="SETTLED both at rest and while driving. What is "
                    "still assumed is that nothing ELSE could join this bus and "
                    "source the frame; the measurement covers the bus as wired.",
        used_by=("can_bus.SparkBus heartbeat",),
 ),
    Claim(
        key="flex.no_param_reads_on_any_api", product=SPARKFLEX, how=HARDWARE,
        what="the undocumented one-byte read on PARAMETER_WRITE is not answered "
             "on 26.1.6. The wider claim this key still carries in its name -- "
             "that no parameter read answers on any api -- is FALSE, and was "
             "refuted",
        evidence="rig-flex id 11, all three captured on the wire: arb "
                 "0x02054fcb (apiClass 19 idx 15), 0x0205364b (apiClass 13 idx 9) "
                 "and 0x0205380b (apiClass 14, payload 0x9E) all went out and none "
                 "was answered, while GET_FIRMWARE on 0x0205260b answered 26.1.6 "
                 "in the same session. "
                 "CORRECTED. That run varied the API and never the "
                 "FRAME FORM: the first two went out as zero-length data frames, "
                 "which is the one form 26.1.6 ignores on those classes. A null "
                 "result covers what was varied and nothing else, so the "
                 "conclusion drawn from it -- 'this REFUTES the hypothesis that "
                 "the silence was an artifact' -- did not follow. Sent as remote "
                 "frames with dlc 8, apiClasses 13 and 15-22 all "
                 "answer. See flex.param_reads_were_probed_as_data_frames. "
                 "What SURVIVES is the third frame. The one-byte read on "
                 "PARAMETER_WRITE has no rtr form to get wrong, it went out "
                 "correctly, and it was not answered. read_param was deleted on "
                 " for that reason and because it could write. "
                 "docs/spark/runs/-rig-flex-hardware.md and "
                 "docs/spark/runs/-rig-flex-flex-parameter-reads.md",
        settle_with="SCOPE. This now covers the PARAMETER_WRITE one-byte read "
                    "only, and its key is kept because seven documents cite it.",
        used_by=("admin.read_param_pair", "admin.param_types"),
 ),
    Claim(
        key="flex.fw26_broadcasts_modern_frames", product=SPARKFLEX, how=HARDWARE,
        what="a SPARK Flex on firmware 26.1.6 broadcasts STATUS_0 (0x2E0), "
             "STATUS_1 (0x2E1) and UNIQUE_ID (0x2F0), and emits NO 0x060 at all",
        evidence="rig-flex: 12 s capture after a wake, 72,322 REV "
                 "motor frames over ids 10-17 -- 0x2E0 x48001, 0x2E1 x24001, "
                 "0x2F0 x304, 0x060 ZERO. The capture agrees. "
                 "collect_status read generation=fw25+ observed=True on all "
                 "eight with nothing declared, model=1 (kSparkFlex), rail "
                 "13.12-13.19 V decoded as a rail. "
                 "docs/spark/runs/-rig-flex-hardware.md",
        used_by=("admin.generation_from_apis", "admin.collect_status",
                 "admin.fault_frame"),
 ),
    Claim(
        key="flex.steer_overcurrent_is_the_smart_limit", product=SPARKFLEX,
        how=HARDWARE,
        what="the overcurrent WARNING tracks the 80 A Smart Current Stall Limit "
             "engaging, NOT the 115 A chop threshold",
        evidence="rig-flex duty sweep, wheels up, cleared between runs. "
                 "At duty 0.14 three steers peaked at 107-108 A, BELOW 115, and "
                 "all four latched anyway. LIMIT REGULATING counts track the "
                 "latch: 21, 22, then 1 at duty 0.10 where exactly one "
                 "controller latched. Refutes an uncited third-party claim that "
                 "overcurrent trips at 115 A. "
                 "docs/spark/runs/-steer-overcurrent.md",
        used_by=("tools/spark_steer_stress.py",),
 ),
    Claim(
        key="both.chop_is_invisible_on_can", product=BOTH, how=HARDWARE,
        what="current chopping cannot be observed over CAN at any sample rate, "
             "because STATUS_0 reports COMMANDED applied output rather than the "
             "instantaneous half-bridge state",
        evidence="REV: APPLIED_OUTPUT is 'The actual value sent to the motors "
                 "from the motor controller'. rig-flex: raising "
                 "STATUS_0 to 250 Hz gave 2.4x the samples and 46 readings above "
                 "115 A across four steers, with ZERO chop signatures. Absence "
                 "of the signature is not evidence chop did not happen; a "
                 "current probe on a phase lead is the only way to see it.",
        used_by=("tools/spark_steer_stress.py",),
 ),
    Claim(
        key="both.chop_cycles_zero_means_immediate", product=BOTH, how=VENDOR,
        what="kCurrentChopCycles default 0 means chop triggers IMMEDIATELY, not "
             "that chopping is disabled",
        evidence="REV-Specs SparkParameters: '| Current Chop Cycles | 12 | "
                 "UINT32 | RW | 0 | Number of cycles before current chopping is "
                 "triggered |'. Chief Delphi 403595 carries the opposite "
                 "reading -- 'I believe this would turn the motor off for the "
                 "kCurrentChopCycles period' -- and that belief was repeated as "
                 "fact in this repo's own skill until. One forum user "
                 "hedging with 'I believe' does not outrank the vendor table.",
        used_by=("~/.claude/skills/spark-raw-can-protocol",),
 ),
    Claim(
        key="flex.runtime_decode_keys_on_firmware", product=BOTH, how=HARDWARE,
        what="controller and the SparkBus router select frame layout by "
             "OBSERVED firmware generation, not by the configured product",
        evidence="Until SparkBus.bus_monitor matched incoming frames "
                 "against _CONFIGS[controller_type], so a SPARK MAX on firmware "
                 "25+ broadcasting 0x2E0/0x2E1 matched neither 0x60 nor 0x61 and "
                 "had NO status frame captured -- telemetry empty forever while "
                 "_last_seen kept updating and the power watchdog called it "
                 "healthy. Verified on rig-flex: all eight controllers now report "
                 "generation fw25+ read off the wire, 13.09-13.16 V, no faults.",
        used_by=("can_bus.route_frame", "controller.note_frame_api",
                 "controller._modern"),
 ),
    Claim(
        key="flex.wake_by_read", product=SPARKFLEX, how=HARDWARE,
        what="one request addressed to any single controller wakes every gated "
             "transmitter on the bus, and erases nothing",
        evidence="measured on rig-flex: 60 s of silence changed nothing, one "
                 "GET_FIRMWARE to id 17 brought all eight back with sticky "
                 "hasReset intact; PROBE-LOG.md section 11",
        used_by=("admin.clear_faults", "tests/support/sparksim/bus.send"),
 ),
    Claim(
        key="both.disable_broadcast", product=BOTH, how=HARDWARE,
        what="a zero-length frame on arbitration id 0 disables every actuator "
             "immediately, PROVIDED the enable heartbeat stops with it",
        evidence="FIRST CAN Device Specification: 'Devices should disable "
                 "immediately when receiving the Disable message (arbID 0).' "
                 "Measured on rig-max, id 3, against a steer motor "
                 "turning at 823 rpm and STILL being commanded 0.15 throughout. "
                 "With the heartbeat stopped in the same call, applied output "
                 "read 0.0000 at 31 ms and the wheel was at rest by about 500 "
                 "ms. Stopping the heartbeat alone took 220 ms to zero output "
                 "and about a second to rest, so the frame is worth roughly 190 "
                 "ms of stopping distance. "
                 "WITH THE HEARTBEAT LEFT RUNNING IT DID NOTHING AT ALL: applied "
                 "stayed 0.151 and the wheel stayed at 823 rpm for two full "
                 "seconds. See can_bus.broadcast_disable_was_overridden.",
        used_by=("can_bus.SparkBus.broadcast_disable",),
 ),
    Claim(
        key="can_bus.broadcast_disable_was_overridden", product=BOTH,
        how=HARDWARE,
        what="broadcast_disable() did not stop a commanded motor, because the "
             "same SparkBus asserted enable 50 times a second underneath it",
        evidence="rig-max, id 3. A steer motor at 823 rpm, commanded "
                 "0.15, with broadcast_disable() called and the setpoint kept "
                 "up: applied output stayed 0.151 and the wheel stayed at 823 "
                 "rpm for the full two seconds sampled. It never stopped. "
                 "_heartbeat_runnable sends the enable at 20 ms and the disable "
                 "was overridden before the next status frame, so the failure "
                 "was invisible in telemetry as well -- an earlier single run "
                 "caught a transient zero at 11 ms purely by sampling luck, "
                 "which is what a 40 ms status cadence does to a 20 ms race. "
                 "FIXED: broadcast_disable clears heartbeat_enabled "
                 "BEFORE sending, so the last enable is older than the disable. "
                 "Re-measured after the fix, still commanded throughout: applied "
                 "zero at 36 ms, wheel at rest by about 600 ms. "
                 "Nothing in the suite called broadcast_disable at all, which is "
                 "how a stop that did not stop went unnoticed; five tests now "
                 "cover it, including the ordering.",
        used_by=("can_bus.SparkBus.broadcast_disable",
                 "can_bus.SparkBus.disable_heartbeat",
                 "tests/unit/test_stop_path.py"),
 ),
    Claim(
        key="both.heartbeat_window", product=BOTH, how=HARDWARE,
        what="output persists about 240 ms after the enable heartbeat stops on "
             "pre-25, not the 100 ms the specification states",
        evidence="FIRST CAN Device Specification gives 100 ms, for the universal "
                 "heartbeat 0x01011840 at 20 ms. Measured on rig-max, id 3, "
                 ", four runs from 823 rpm with the setpoint still "
                 "being commanded: applied output reached zero at 224, 242, 244 "
                 "and 245 ms, median 243. The wheel was not at rest until about "
                 "a second. Timing floor is one STATUS_0 period, about 40 ms on "
                 "this throttled fleet, plus up to 20 ms of heartbeat phase -- "
                 "far short of the 143 ms gap, so the discrepancy is real and "
                 "not resolution. "
                 "Note the heartbeat that matters here is the SECONDARY one, "
                 "0x02052C80; 24.0.1 ignores 0x01011840 entirely. Treat 100 ms "
                 "as a floor and 250 ms as the number to design a stop around.",
        settle_with="repeat on rig-flex at 26.1.6; a generation that honours a "
                    "different heartbeat may honour a different timeout",
        used_by=("tests/support/sparksim/bus.heartbeat_gap",),
 ),
    Claim(
        key="flex.status_period.max", product=SPARKFLEX, how=HARDWARE,
        what="parameter 159 accepts a period well past REVLib's undocumented "
             "1000 ms ceiling",
        evidence="20000 was written to rig-flex and the wire stayed silent 18 s. "
                 "REVLib caps at 1000 (REV-Software-Binaries#19) but this "
                 "package writes raw CAN and bypasses it.",
        used_by=("tests/support/sparkhw/guards.MAX_PERIOD_MS",),
 ),

    # -- the simulator -------------------------------------------------------
    Claim(
        key="sim.models_both_generations", product=BOTH, how=VENDOR,
        what="the simulator models BOTH firmware generations: a controller "
             "carries a firmware version and broadcasts that generation's api "
             "class, so a pre-25 device answers on 0x060/0x061 and a 25+ device "
             "on 0x2E0/0x2E1 whatever product either one is",
        evidence="frames.generation_for_firmware splits at major version 25, "
                 "matching versionImplemented in REV-spark-frames-2.1.0.json. "
                 "Until it keyed on product instead, so the "
                 "simulator manufactured the same wrong bus the driver "
                 "expected and the suite could not detect the defect at all.",
        used_by=("tests/support/sparksim/controller.enabled_apis",
                 "tests/support/sparksim/controller.generation"),
 ),
    Claim(
        key="max.hardware.unprobed", product=SPARKMAX, how=HARDWARE,
        what="rig-max's eight SPARK MAXes on firmware 24.0.1 have been read by "
             "this package: inventory, status, faults, voltage, duplicates, the "
             "read-only hardware tier and a restored period write",
        evidence="rig-max. Eight controllers on gsusb at 1 "
                 "Mbit, all 24.0.1, generation pre25, broadcasting 0x060/0x061/"
                 "0x062/0x063/0x065/0x066/0x067 and no 0x064. Raw legacy fault "
                 "words read 0x0000 active and sticky on all eight after a "
                 "clear. What the read turned up that the documents did not: "
                 "PARAMETER_WRITE is unanswered while api class 6 works (see "
                 "max.firmware_honours_period_write); no controller broadcasts "
                 "UNIQUE_ID, so serials are unreadable and learn-serials cannot "
                 "run here; and the audit's pre-25 expected_ms of 10 ms is "
                 "contradicted by a fleet at 50 ms, which is this package's own "
                 "boot throttle rather than any REV default.",
        used_by=("admin.api_set", "admin.decode_legacy_status_0"),
 ),
    Claim(
        key="max.param_access_answers_both_ways", product=SPARKMAX, how=HARDWARE,
        what="a pre-25 SPARK MAX answers parameter READS and WRITES on api class "
             "48, the whole table, which is the capability 25+ firmware does not "
             "have. The parameter id rides in the arbitration id and the reply "
             "returns on that same id: arb = 0x02050000 | ((0x300|pid) << 6) | "
             "dev, a zero-length frame reads, five bytes [int32 value][type] "
             "writes, and the reply is [uint32 value][type][status]",
        evidence="rig-max, firmware 24.0.1. All 134 parameters (0-133) "
                 "answered on all eight controllers: zero refused, zero silent. "
                 "Parameter 0 returns each device's own CAN id, which is the "
                 "self-check that the reads are live and per-device. Type tags "
                 "are 0 int32 / 1 uint32 / 2 float32 / 3 bool, and status byte 0 "
                 "is success -- parameters 158-165 reply with a non-zero status, "
                 "because on pre-25 the status periods are not parameters and "
                 "move on LEGACY_SET_PERIOD instead. Writes were confirmed by "
                 "toggling Idle Mode both directions four times with independent "
                 "reads between, and an operator confirmed the brake resistance "
                 "by hand. This was read at the time as INVERTING the premise "
                 "the Flex work rested on, because 26.1.6 appeared to answer no "
                 "parameter read. That premise fell. Both "
                 "generations can be asked, in different dialects: "
                 "pre-25 on api class 48 for parameters 0-133, and 25+ on "
                 "READ_PARAMETER for all of 0-255.",
        used_by=("admin.read_legacy_param", "admin.write_legacy_param"),
 ),
    Claim(
        key="max.param_writes_are_ram_only", product=SPARKMAX, how=HARDWARE,
        what="a parameter written over CAN to a pre-25 SPARK MAX lands in RAM and "
             "is gone after a power cycle, exactly like the status periods. "
             "Anything this package writes has to be re-applied on every boot",
        evidence="rig-max. Idle Mode set to BRAKE on all eight and "
                 "read back as BRAKE, sticky faults cleared to 0x0000, then the "
                 "motor rail was cut and restored: all eight came back COAST, "
                 "which is the factory default, with sticky 0x0200 confirming the "
                 "cycle. The same cycle reverted every status period to REV's "
                 "pre-25 defaults (10/20/20/50/200/200, Status 7 untouched at "
                 "250). Flash-resident provisioning is unaffected -- the drive P "
                 "of 2.0, steer P of 0.1 and the 40 A stall limit survived both "
                 "cycles -- so only what this package writes over CAN is volatile. "
                 "Consequence at the time: `spark repair` on a MAX would be a "
                 "re-apply, not a fix. SUPERSEDED IN PART -- see "
                 "pre25.burn_flash_api. A plain legacy write is still RAM only, "
                 "which is what this claim says and it remains true, but api "
                 "0x072 with the magic 15011 does commit the parameter table to "
                 "flash, so persistence IS reachable on this generation. The "
                 "status periods are the exception and stay volatile. "
                 "RE-CONFIRMED rig-max, this time as an automated "
                 "test rather than an operator's note: all eight read COAST and "
                 "clean, were written BRAKE over the legacy dialect and read back "
                 "BRAKE, the rail was cut and restored, and all eight came back "
                 "COAST with sticky 0x0200 on every one. The status periods went "
                 "with them, from the 50 ms boot throttle to REV's 10 ms cold "
                 "default, which took the wire from 384 to 1872 frames/s of controller traffic. "
                 "tests/hardware/test_legacy_burn_flash.py stages arm and verify "
                 "re-run it on demand.",
        used_by=("admin.write_legacy_param",
                 "tests/hardware/test_legacy_burn_flash.py",
                 "admin.set_legacy_status_period"),
 ),
    Claim(
        key="rig-max.foreign_traffic_on_the_spark_bus", product=SPARKMAX,
        how=HARDWARE,
        what="a CTRE power-distribution device shares gsusb on rig-max, "
             "broadcasting ten status frames at 40 Hz each for a constant 404 "
             "frames/s. It is 51 percent of the throttled bus and `spark "
             "throttle` does not touch it. IT REPORTS BOTH CURRENT AND THE "
             "RAIL VOLTAGE UNDER LOAD: byte 6 of apis 0x052, 0x055 and 0x058 "
             "is one shared field that tracks the rail at +0.81. Its payload "
             "looks static only on an idle robot",
        evidence="rig-max, decomposed by arbitration id. Device type "
                 "8, manufacturer 4 (CTRE), apis 0x050 through 0x059 at device "
                 "id 0, each at 40 Hz, plus api 0x3E0 at device id 63 at 4 Hz. "
                 "NOT self-inflicted: an identical 384 + 404 split was captured "
                 "from a process that imports neither phoenix6 nor this "
                 "driver, so it is not this tooling's diagnostics "
                 "server. inventory() filters on manufacturer 5 and device type "
                 "2, so `spark status` correctly never showed it. "
                 "CARRIES ALMOST NO INFORMATION *ON AN IDLE ROBOT*, which is "
                 "the only condition it was ever sampled in. The capture "
                 "conditions were not recorded, and every command in play that "
                 "day -- status, audit, throttle, candump -- is read-only and "
                 "starts no enable heartbeat, so the motors were disabled and "
                 "drawing nothing. A power distribution panel's whole job is "
                 "per-channel current sensing, so constant frames are exactly "
                 "what an idle panel should send. On a static bus a field "
                 "holding a steady 12.8 V is indistinguishable from padding. "
                 "The conclusion below is therefore about the sample, not about "
                 "the device. Over 480 samples of each frame: "
                 "0x050, 0x051, 0x053, 0x054, 0x057 and 0x059 are byte-for-byte "
                 "CONSTANT; 0x052, 0x055 and 0x058 vary only in bytes 6 and 7 and "
                 "do so identically to each other, which behaves like a shared "
                 "counter rather than per-frame telemetry; 0x056 toggles one bit "
                 "in byte 2; 0x3E0 is high-entropy in bytes 0-5. No field was "
                 "found that tracks the rail voltage the SPARKs report -- on a "
                 "bus where that voltage never moved. "
                 "RE-SAMPLED WHILE DRIVING, AND THE IDLE READING "
                 "DOES NOT SURVIVE. tools/spark_pdp_correlate.py listened for "
                 "30 s while the base drove, sending nothing. Byte 4 of api "
                 "0x056 tracks total SPARK current at r = +0.88 over 150 bins, "
                 "and four fields in api 0x050 track it at +0.78 to +0.79, "
                 "clustering in the 10-bit packed layout CTRE use for "
                 "per-channel currents. The window carried real load: "
                 "per-controller current spans ran 1.26 to 24.93 A. So the "
                 "panel senses and broadcasts, and the constant frames were the "
                 "sample. "
                 "SECOND RUN THE SAME DAY at drive scale 0.99 sagged the rail "
                 "2.310 V, five times deeper, which is enough to correlate "
                 "against. u10#1 of api 0x050 tracks total current at +0.81 and "
                 "the rail at -0.67; u10#1 of api 0x052 tracks them at +0.77 "
                 "and -0.69. The SIGNS say both are current channels: a field "
                 "measuring current rises as current rises and falls as the "
                 "rail sags under it, and a field measuring the rail does the "
                 "opposite. Nothing did. "
                 "IT STILL DOES NOT SETTLE THE TELEMETRY SCALES. The current "
                 "match is INDIRECT, because a panel channel measures SUPPLY "
                 "current into a controller while the controller reports PHASE "
                 "current and the two differ by roughly the duty cycle. The "
                 "voltage half is the one that would be direct and it is not "
                 "identified. "
                 "THE RAIL IS IN BYTE 6, and less cleanly than one run "
                 "suggested. u8@6 of apis 0x052, 0x055 and 0x058 RISES with the "
                 "rail while every other strong field falls with it, which is "
                 "the sign that identifies a voltage. It is one shared field: "
                 "back-solving the mean from each frame gives 159.8, 159.7 and "
                 "159.7. Those are the same three frames the idle survey called "
                 "a shared counter, and that reading was wrong. Its strength "
                 "moves between runs: +0.81 over a 1.276 V sag, +0.69 over a "
                 "2.249 V sag, so R2 is 0.66 then 0.48 and it explains under "
                 "half the rail's movement in the deeper run. A first reading "
                 "called it settled on the +0.81 alone and that was too strong. "
                 "A LEAD ON THE TELEMETRY SCALES, on two unmeasured "
                 "assumptions. The raw mean is exact rather than fitted, since "
                 "least squares passes through it. Raw 159.8 under CTRE's "
                 "published raw*0.05+4.0 reads 11.99 V where the controllers "
                 "read 11.28 V, so a controller count would be worth 0.0077871 "
                 "V. REV publish 0.0073260, which is 5.9 percent away. 1/128 is "
                 "0.0078125, which is 0.3 percent away, and 1/128 is what "
                 "controller used until when it was replaced "
                 "for reading about 6.6 percent high. This points the other "
                 "way. The assumptions are that byte 6 is the bus voltage and "
                 "that CTRE's scaling applies to this panel; either being wrong "
                 "dissolves it, and it is not a calibration in any case because "
                 "the comparison uses the controller's own reading as its "
                 "reference. It names a number to check and a reason to doubt "
                 "the current one. "
                 "BYTE 7 WAS A RED HERRING and is recorded because it cost time. "
                 "The absolute-match pass flagged it in every run, because "
                 "x*0.125 puts it within 0.15 V of the rail, and it tracked the "
                 "rail at only +0.10 to +0.25. It lands in range by arithmetic. "
                 "Correlation ignores amplitude and is what separates the two, "
                 "which is why every absolute hit now carries its correlation "
                 "beside it. "
                 "docs/spark/runs/-rig-max-post-rail-cycle.md. "
                 "IT CORRECTS THE THROTTLE ARITHMETIC. The boot throttle cuts the "
                 "CONTROLLER contribution 1872 -> 384, which is 4.9x, but total "
                 "bus load only 2276 -> 788, which is 2.9x, because the 404 is "
                 "constant in both states.",
        settle_with="CLOSED as a question, not as a change. The "
                    "device is the CTRE PDP 4.0, which is what device type 8 and "
                    "manufacturer 4 say, and the operator confirms it belongs on "
                    "that bus and carries no utility worth reading. Nothing is "
                    "added to spark.yaml, because nothing here commands "
                    "it. The closure rests on that, NOT on the payload survey, "
                    "which was taken on an idle robot and has since been "
                    "measured wrong: driven, the panel reports current. "
                    "WHAT IS STILL OPEN is the rail voltage, which is the half "
                    "that would settle the legacy telemetry scales, because "
                    "both devices measure the same rail and the numbers would "
                    "compare directly. No candidate field tracked it over a "
                    "2.310 V sag, and byte 6 was found tracking it at +0.81 on "
                    "a third run. So the panel IS a candidate independent "
                    "instrument for the legacy voltage scale, which this repo "
                    "has never had. "
                    "tools/spark_pdp_correlate.py solves for the scale rather "
                    "than guessing it: report_rail_fit least-squares fits the "
                    "controller-reported rail against every field that tracks "
                    "it and prints volts per count, offset and residual. CTRE "
                    "publish the PDP bus voltage as raw*0.05 + 4.0, so a fit "
                    "landing there is two independently specified scales "
                    "agreeing, which corroborates REV's volts-per-count and "
                    "CTRE's at once. "
                    "THE TEST NEEDS VOLTS ONLY. A meter reads voltage in "
                    "parallel, so any 0-20 V DC meter settles the volt scale; "
                    "no current rating and no clamp. Take it AT REST with the "
                    "motors disabled, because no current means no wire drop and "
                    "the meter and controller then see the same voltage. If "
                    "`spark voltage` shows 11.64 the meter reads 11.64 when "
                    "REV's scale is right and 12.41 when 1/128 is, a 6.6 "
                    "percent split any meter resolves. The AMP scale is the one "
                    "wanting a DC clamp meter past 60 A and it stays open. "
                    "IT IS STILL NOT A CALIBRATION. The fit uses the "
                    "controller's own reading as its reference and cannot prove "
                    "that reading correct. It makes agreement unlikely by "
                    "chance, because a clean documented slope falling out of "
                    "another vendor's device is worth more than a round number "
                    "is. Only a multimeter breaks the circle. "
                    "The load hypothesis was weaker than it looked and is not "
                    "pursued: at roughly 110 bits per extended frame the cold bus "
                    "is about 25 percent of 1 Mbit and the throttled bus about 9 "
                    "percent, so raw utilisation is not obviously the gs_usb wedge "
                    "mechanism and no causal link was ever shown. Slowing its "
                    "periods would save more than the SPARK throttle saves, and "
                    "there is no reason to spend the risk on it.",
        used_by=("admin.inventory device-type filter",),
 ),
    Claim(
        key="pre25.closed_loop_sensor_reads_all_ones", product=SPARKMAX,
        how=HARDWARE,
        what="parameter 9 IS Closed Loop Control Sensor on 24.0.1, and the "
             "rig-max fleet holds 4294967295 in it, which is outside the Sensor "
             "enum. That is an UNSET state and not Sensor.NONE: 0 is None and 1 "
             "is Primary Encoder, both of which RHC2 renders by name",
        evidence="rig-max, read on all eight over api class 48: seven "
                 "controllers returned 4294967295 and id 3 returned 0, because "
                 "id 3 was the one `spark provision --write` had just written. "
                 "So the fleet value is the all-ones word and the single "
                 "controller holding 0 was the anomaly, not the reverse. "
                 "CONFIRMED BY A SECOND FLEET-WIDE READ, "
                 "after the write test below: all eight read 4294967295, id 3 "
                 "included. That reading needs no argument about which "
                 "controller was the outlier, because there is none, and it "
                 "also shows the writes did not persist. "
                 "IT CORRECTS THE DECLARED FILE, WHICH WAS WRONG. "
                 "sparkmax_motor_defaults.yaml carried value NONE with a comment "
                 "saying it reads Sensor.NONE on all eight, recorded. "
                 "Nothing in that reading survives: the fleet does not hold 0. "
                 "The audit never caught it because the entry is deviates:false "
                 "and the audit reads only the deviating subset; `spark "
                 "provision` reads every declared setting, which is how the "
                 "disagreement surfaced and also how it got written. "
                 "A USB-C SESSION DOES NOT CHANGE IT, measured with a control. "
                 "id 1 read 4294967295 before RHC2 was connected, and 4294967295 "
                 "again after it was disconnected with nothing else done in "
                 "between. So the operator's own hypothesis, that an earlier "
                 "replug had moved the value, is ruled out rather than argued "
                 "away. "
                 "THE ID MAPPING IS CONFIRMED, by writing values and watching "
                 "RHC2 follow them. With 4294967295 stored the "
                 "field shows NO OPTION SELECTED, blank rather than None. "
                 "Written 0 it shows None. Written 1 it shows Primary Encoder. "
                 "So id 9 is that field, the enum is 0 None and 1 Primary "
                 "Encoder as the 25+ table says, and the fleet is sitting on a "
                 "value outside it. An earlier note here read the blank field as "
                 "None and was corrected by the operator; blank and None are "
                 "different states and only the write test separated them. "
                 "SAME SHAPE AS flex.bool_params_store_out_of_range_values: a "
                 "SPARK stores a value its own enum does not define, and the "
                 "tooling above it cannot tell that from a legitimate setting. "
                 "RHC2's read was intermittent during the session, failing and "
                 "then succeeding on reconnect, and it did that for default and "
                 "custom values alike. Recorded as noise, not as a finding. "
                 "The file now records 4294967295 as measured, which is what a "
                 "working fleet holds, so no command writes it. It is "
                 "deliberately NOT expressed as an enum name, because under "
                 "either explanation a name would encode the wrong number. "
                 "docs/spark/runs/-rig-max-post-rail-cycle.md",
        settle_with="SETTLED, and it turns out to matter less than it looked. "
                    "An earlier draft here said rig-max's steering depends on "
                    "this parameter and wanted a tracking test. It does not. "
                    "SwerveModule._read_wheel_rad closes the steer loop in code "
                    "on the CANcoder, over the CANivore bus, and the SPARK is "
                    "handed a short relative move anchored to its own integrated "
                    "position. The SPARK never sees the CANcoder, and whatever "
                    "its on-board loop uses is corrected on the next tick. Drive "
                    "is open loop, percent_output only, with no sensor in the "
                    "path at all. So an unset value here degrades neither half. "
                    "The declared file keeps 4294967295, so nothing writes the "
                    "parameter and provision reports it clean. What survives is "
                    "the narrower finding: a SPARK stores a value outside its "
                    "own enum and the tooling cannot tell that from a setting.",
        used_by=("the `sparkmax:` block of spark.yaml param 9",
                 "spark provision", "spark audit"),
 ),
    Claim(
        key="pre25.serial_is_readable_at_api_0x094", product=SPARKMAX,
        how=HARDWARE,
        what="a pre-25 SPARK MAX DOES expose a per-device serial over CAN, at "
             "api 0x094 (arb 0x02050000 | (0x094 << 6) | id), answered by a "
             "zero-length request with four read-only bytes. This overturns "
             "'no serial exists on the wire on pre-25'",
        evidence="rig-max. Found by sweeping zero-length requests "
                 "across api 0x000-0x2FF on two controllers and comparing which "
                 "apis answered with per-device-different payloads. Eight apis "
                 "answered; three differed per device; two of those were "
                 "explained (0x071 carries the CAN id, 0x0C1 is a counter -- five "
                 "of eight values changed within two seconds). "
                 "0x094 read on all eight: 66d3bde5, 481f7cb5, eabe4c2e, "
                 "a7a98e23, 249eb2b0, 44e08e6a, 5945a484, 10487c0d. Eight "
                 "distinct values, all stable across a two second gap, four bytes "
                 "each, high entropy, not derived from the CAN id. "
                 "READ-ONLY: writing DEADBEEF as four bytes and as eight bytes to "
                 "the same arbitration id drew no reply and changed nothing, and "
                 "a full 134-parameter snapshot either side was identical. "
                 "It sits in apiClass 9, the device-identity class -- index 5 is "
                 "SET_CAN_ID and index 8 is GET_FIRMWARE, and the sweep returned "
                 "the correct firmware payload at 0x098, which corroborates the "
                 "api decoding. Four bytes is also exactly what "
                 "IDENTIFY_UNIQUE_SPARK takes as its payload. "
             "CONFIRMED to BE the firmware's Unique ID: SET_CAN_ID "
             "(apiClass 9 index 5, versionImplemented 1.5.0) carries a Unique ID "
             "at bit 0 and a CAN ID at bit 32 per REV-Specs, and sending "
             "SET_CAN_ID | 3 with this fingerprint plus a new id moved that "
             "controller from id 3 to id 20 -- located there by fingerprint -- "
             "and moved it back. So it is not merely a stable unique value: it "
             "is the identifier the firmware itself accepts for addressing. "
             "Note the asymmetry, which is measured rather than assumed: "
             "SET_CAN_ID takes it, and pre-25 IDENTIFY does not -- identify is "
             "addressed by CAN id and carries no payload at all.",
        settle_with="SETTLED, on an LED, and the answer is NO: this "
                    "value is NOT what IDENTIFY_UNIQUE_SPARK matches against. "
                    "One controller was watched, id 3 steer/RB, through three "
                    "windows with ten seconds of silence between them -- the "
                    "pre-25 ADDRESSED form, then the firmware-25 broadcast "
                    "carrying that controller's own fingerprint EABE4C2E, then "
                    "the addressed form again. The operator reported a FAST "
                    "blink in the first and third windows and only the "
                    "controller's standard idle blink in the middle one. The "
                    "identify signature is distinguishable from the resting "
                    "blink, the control fired either side of the negative, and "
                    "the frames were confirmed on the wire as 02051D83 [0] and "
                    "02051D80 [4] EA BE 4C 2E. So the fingerprint addresses "
                    "nothing: pre-25 identify takes no serial and this register "
                    "is a device fingerprint and not a REV serial. "
                    "ONE THING REMAINS. Read-only is scoped to the two payload "
                    "shapes tried. A "
                    "magic-guarded write could exist, exactly as it did for the "
                    "burn at api 0x072, so do not record the register as "
                    "immutable. "
                    "WHAT IT ALREADY SOLVES, verified on hardware: "
                    "duplicate CAN ids, which is what the identity work was for. "
                    "Two controllers on one id are invisible to an id scan and "
                    "every addressed write reaches both while only the first "
                    "reply is read. The fingerprint is REQUESTED rather than "
                    "broadcast, so one request to a shared id draws one reply "
                    "per controller. Tested on rig-max by injecting a second "
                    "reply on the fingerprint arbitration id rather than by "
                    "moving a real CAN id: clean before, detected during, clean "
                    "after, and no controller changed. "
                    "read_fingerprint discriminates by frame LENGTH and not by "
                    "is_rx for that reason -- SocketCAN flags every locally "
                    "generated frame as loopback, so an is_rx filter dropped all "
                    "124 injected replies and made the path untestable without a "
                    "genuine second device.",
        used_by=("admin.read_fingerprint", "admin.duplicates",
                 "cli.cmd_duplicates", "admin.inventory"),
 ),
    Claim(
        key="pre25.zero_length_frames_can_still_act", product=SPARKMAX,
        how=HARDWARE,
        what="a zero-length frame is NOT inert on this firmware. CLEAR_FAULTS "
             "(api 0x06E) executes on one",
        evidence="rig-max, found by the safety net rather than by "
                 "design. The api sweep above was justified on the grounds that "
                 "zero length is request semantics here, because api 0x072 with "
                 "DLC 0 drew no reply and committed nothing while the same api "
                 "with its magic replied and committed. That was one data point "
                 "generalised to a command space. The sweep cleared the sticky "
                 "fault word on both swept controllers -- ids 3 and 4 read empty "
                 "afterwards while the other six still carried canTx, canRx and "
                 "hasReset -- and api 0x06E is CLEAR_FAULTS. "
                 "The damage was bounded because a full parameter snapshot and a "
                 "sticky-fault comparison were taken either side, and the erased "
                 "bits had already been recorded in "
                 "pre25.fault_bits_cantx_canrx. The exclusion list came from "
                 "FORBIDDEN_BASES in tests/support/sparkhw/wire.py, which is the "
                 "INJECTOR denylist -- frames a test may transmit -- and "
                 "CLEAR_FAULTS is legitimately absent from it for that purpose. "
                 "Reusing a list built for one purpose as the safety boundary for "
                 "another is what let it through.",
        settle_with="Any future api sweep must exclude every frame with COMMAND "
                    "semantics regardless of payload length, derived from the "
                    "frame spec rather than from an injector denylist, and must "
                    "keep the before/after snapshot that caught this one.",
        used_by=("tests/support/sparkhw/wire.py FORBIDDEN_BASES",),
 ),
    Claim(
        key="pre25.no_serial_exists_in_the_parameter_table", product=SPARKMAX,
        how=HARDWARE,
        what="no serial or unique identifier exists anywhere in the addressable "
             "pre-25 parameter table. The CAN id is the only per-device value "
             "there, and it is per-device because we set it",
        evidence="rig-max, exhaustive rather than a spot check. All "
                 "134 parameters, ids 0 through 133, were read from all eight "
                 "controllers with `spark params`. 132 are byte-identical across "
                 "the fleet. One differs and is not unique -- parameter 13, P 0, "
                 "which takes two values split by role. Exactly one is unique per "
                 "device: parameter 0, the CAN id. "
                 "Parameters 47, 48 and 49, which earlier notes single out as "
                 "'Reserved, not serials', read 0xFFFFFFFF on every controller, "
                 "so they are identical and cannot be identity. "
                 "rev_parameter_index.tsv names nothing containing serial, "
                 "unique, uid, ident, mac, hash or guid at any id up to 198. "
                 "This upgrades the previous position from 'those three ids are "
                 "not serials' to 'nothing in the table is'.",
        settle_with="SCOPE. This covers ids 0-133, which is what "
                    "_legacy_param_arb will address. It does not cover ids above "
                    "133, and it cannot: that is command space on this "
                    "generation, and 0x300 | 255 is the persist arbitration id. "
                    "It also says nothing about routes that are not parameters. "
                    "The device DOES hold its own serial -- IDENTIFY_UNIQUE_SPARK "
                    "is apiClass 7 index 6 at versionImplemented 1.5.0 and "
                    "compares a serial carried in its payload -- so the value "
                    "exists inside the controller with no read path off it. "
                    "Recovering it by sending candidate serials and watching for "
                    "a blink is 2^32 attempts and is not a plan. The only real "
                    "route to serials on this fleet is firmware 25.0.0, where "
                    "UNIQUE_ID_BROADCAST (apiClass 47) begins.",
        used_by=("admin.inventory", "cli.cmd_learn_serials",
                 "cli.cmd_set_id", "cli.cmd_identify"),
 ),
    Claim(
        key="pre25.fault_bits_hardlimit_fwd_rev", product=SPARKMAX, how=HARDWARE,
        what="hardLimitFwd is bit 14 and hardLimitRev is bit 15 of the pre-25 "
             "fault word, measured causally rather than observed",
        evidence="rig-max, id 3, made to appear and clear on demand. "
                 "Limit Switch Fwd/Rev Polarity (parameters 50 and 51) were "
                 "written 1 -- the opposite of REV's default, with no switch "
                 "wired to the data port -- and the active fault word read "
                 "0xC000, which is bits 14 and 15 and nothing else, exactly as "
                 "predicted. Written back to 0 and the word returned to 0x0000. "
                 "Repeated twice through the tool's own inject cycle before this "
                 "capture, asserting and clearing both times. "
                 "This is a stronger class of evidence than the other measured "
                 "bits: hasReset, canTx and canRx were observed after events "
                 "that happened, whereas this one was caused, reversed and "
                 "caused again. This took the count to five of sixteen on the day "
                 "them. "
                 "The write goes through the LEGACY dialect. PARAMETER_WRITE is "
                 "versionImplemented 25.0.0, so the tool's original write path "
                 "was dropped in silence on this firmware and would have "
                 "reported a dead controller rather than a wrong dialect. "
                 "RAM ONLY, and deliberately gated: "
                 "tools/spark_limit_polarity_repair.py refuses to write these "
                 "parameters on pre-25 without --allow-ram-only, because an "
                 "inverted polarity that nobody recorded is a hard limit with no "
                 "flash value to explain it, and the rail cycle that clears it "
                 "also erases the evidence.",
        used_by=("admin._LEGACY_FAULT_BITS",
                 "tools/spark_limit_polarity_repair.py",
                 "swerve_drive controllers-can-drive startup gate"),
 ),
    Claim(
        key="pre25.fault_bits_softlimit_fwd_rev", product=SPARKMAX, how=HARDWARE,
        what="softLimitFwd is bit 12 and softLimitRev is bit 13 of the pre-25 "
             "fault word, caused on demand against real motion",
        evidence="rig-max, id 3 steer/RB, wheels free. The internal "
                 "encoder was confirmed to track first -- position moved 226.32 "
                 "to 241.10 rev under a 0.15 duty -- because a soft limit cannot "
                 "trip against a position that never changes and the test would "
                 "have measured nothing. Soft Limit Forward, parameter 115, was "
                 "then set half a turn ahead of the current position and "
                 "parameter 54 enabled; driving forward past it read 0x1000, "
                 "which is bit 12 and nothing else. The reverse mirror through "
                 "parameters 116 and 55 read 0x2000. Both cleared on restore. "
                 "This took the count to seven of sixteen on the day. "
                 "Output overshoots the limit by about a turn at 830 rpm: idle "
                 "mode is COAST on this fleet, so the fault is the boundary the "
                 "controller stopped commanding at, not where the mechanism "
                 "stopped. A soft limit is not a mechanical stop.",
        settle_with="the eight remaining bits are deliberately not caused; "
                    "brownout, overcurrent, stall, eepromCrc, gateDriver, "
                    "motorType, iwdtReset and other each cost hardware or mean "
                    "corrupting stored configuration",
        used_by=("admin._LEGACY_FAULT_BITS",),
 ),
    Claim(
        key="pre25.fault_bit_sensor", product=SPARKMAX, how=HARDWARE,
        what="sensor is bit 4 of the pre-25 fault word, and it can be caused "
             "from a parameter write with no hardware handling at all",
        evidence="rig-max, id 3, NO enable heartbeat on the bus so "
                 "nothing could drive. Sensor Type, parameter 4, reads 1 (hall) "
                 "on this fleet. Written to 0, to 2 and to 3 in turn, each write "
                 "confirmed by reading it back, and the active word read 0x0010 "
                 "every time -- bit 4 and nothing else. Restored to 1 and it "
                 "cleared. The standing plan called for unplugging an encoder "
                 "JST with the rail down; making the controller's declared "
                 "sensor disagree with the one it has produces the same fault "
                 "and touches no connector. This closed the tally at eight of "
                 "sixteen; the other eight are unreachable or destructive, each "
                 "for a checked reason. docs/SPARKMAX-BRINGUP.md carries it.",
        used_by=("admin._LEGACY_FAULT_BITS",),
 ),
    Claim(
        key="pre25.smart_current_limit_raises_no_fault", product=SPARKMAX,
        how=HARDWARE,
        what="the smart current limit does not announce itself in the fault "
             "word, so overcurrent (bit 1) is not reachable by capping current",
        evidence="rig-max, id 3. Smart Current Stall Limit, parameter "
                 "59, was written from 40 to 1 and the write was confirmed by "
                 "reading it back. Driving at 0.30 for four seconds drew 9.56 A "
                 "peak against that 1 A setting and EVERY fault word sampled "
                 "through the run was 0x0000. Lowering a current limit is safer "
                 "than the default, which is why this was the way to try for the "
                 "bit. It confirms on pre-25 what REV publish for both products "
                 "and what rig-flex found: neither the smart limit nor the chop "
                 "raises a fault or an LED code, and the only evidence either is "
                 "acting is the relationship between commanded and applied. "
                 "Whether the limit REGULATED is not claimed: a four-second "
                 "burst cannot separate inrush from a loop that never engaged.",
        settle_with="a longer hold with commanded and applied logged together, "
                    "which is what tools/spark_steer_stress.py already records",
        used_by=("admin._LEGACY_FAULT_BITS",),
 ),
    Claim(
        key="pre25.legacy_param_writes_are_range_checked", product=SPARKMAX,
        how=HARDWARE,
        what="a legacy parameter write is validated against the parameter's "
             "range and REFUSED with a non-zero status, keeping the old value",
        evidence="rig-max, id 3, parameter 11 Current Chop. Writes of "
                 "100.0, 60.0 and 120.0 each returned status 0 and read back as "
                 "written. A write of 1.0 returned STATUS 4 and the parameter "
                 "stayed at its previous value, 60.0. So the echo's status byte "
                 "is load-bearing: a caller that writes and does not read the "
                 "status can believe it set a value the controller rejected. "
                 "This was found because a current-limit experiment appeared to "
                 "produce a null result when the write had simply been refused.",
        used_by=("admin.write_legacy_param", "admin.LEGACY_PARAM_OK"),
 ),
    Claim(
        key="pre25.hard_limits_are_enabled_by_rev_default", product=SPARKMAX,
        how=HARDWARE,
        what="the hard limit interlock is ARMED out of the box, so the polarity "
             "parameters alone decide whether an unwired data port reads as "
             "tripped",
        evidence="rig-max, id 3, whole parameter table read over CAN. "
                 "sparklib/data/rev_parameter_index.tsv names 50 and 51 Limit "
                 "Switch Fwd/Rev Polarity, default false, and 52 and 53 Hard "
                 "Limit Fwd/Rev En, default TRUE. The hardware agrees: 50 and 51 "
                 "read 0, 52 and 53 read 1. Earlier sessions described 50 and 51 "
                 "as the enable, which put the danger in the wrong parameter -- "
                 "the enable is already on and never had to be written, so a "
                 "polarity flip alone is enough to stop a mechanism. "
                 "Follower ID 57 and Follower Config 58 both read 0 on the same "
                 "pass, so nothing on this fleet is following.",
        settle_with="on rig-flex: read 50-53 on 26.1.6 and confirm the same "
                    "defaults hold on the later generation",
        used_by=("tools/spark_limit_polarity_repair.py",),
 ),
    Claim(
        key="pre25.hard_limit_blocks_output_directionally", product=SPARKMAX,
        how=HARDWARE,
        what="an asserted hard limit withholds output in ITS OWN direction only; "
             "the opposite direction still drives at the full commanded duty",
        evidence="rig-max, id 3 steer/RB, commanded 0.15 with the "
                 "wheels free to turn. Eight cases with untreated controls "
                 "interleaved, every one as predicted: limits clear drove both "
                 "ways at applied 0.1515 and 834 rpm; forward asserted blocked "
                 "+0.15 at applied 0.0000 and 0.0 rpm while -0.15 still drove at "
                 "0.1515 and 823 rpm; reverse asserted mirrored it exactly; both "
                 "asserted blocked both directions. Clearing the polarity "
                 "restored motion each time, so the effect is reversible and was "
                 "caused rather than observed. "
                 "ONE asserted limit is an interlock, not a stop: it leaves the "
                 "mechanism free to run the other way at full commanded duty. "
                 "BOTH asserted IS a stop, and a latching one. Measured "
                 " in the scenario that matters, asserting both while "
                 "the motor was already turning at 811 rpm: applied output "
                 "reached zero at 87, 88 and 90 ms across three trials and the "
                 "wheel was at rest by 490-592 ms. It then held through ten "
                 "seconds of alternating +/-0.15 at peak applied 0.0000, and "
                 "0.30 and 0.60 in both directions did not break through. "
                 "An earlier framing here called it 'not an e-stop' on the "
                 "strength of the direction alone. That was too weak an "
                 "argument, because anyone using it as a stop asserts both. The "
                 "real comparison is: arbitration id 0 zeroes output at 31 ms "
                 "and reaches ids that are wrong or duplicated, in ONE frame "
                 "rather than two writes per controller, but it does NOT latch "
                 "-- the next enable heartbeat overrides it, see "
                 "can_bus.broadcast_disable_was_overridden. The limit latches "
                 "until something writes it back. Letting the heartbeat lapse "
                 "takes 220 ms, slower than either. "
                 "What still disqualifies it as THE stop: it is addressed per "
                 "controller so a wrong or duplicated id is missed, the write is "
                 "unacknowledged on this generation, and it needs the bus and "
                 "software working at the moment it is needed. None of the three "
                 "is a safety device; the motor rail is.",
        used_by=("tools/spark_limit_polarity_repair.py",
                 "swerve_drive controllers-can-drive startup gate"),
 ),
    Claim(
        key="pre25.status0_byte6_tracks_the_drive_dead_state", product=SPARKMAX,
        how=HARDWARE,
        what="byte 6 of LEGACY_STATUS_0 is the only passive field that has "
             "separated a pre-25 fleet which drives from one which does not",
        evidence="rig-max. All eight controllers read byte 6 = 0x54 "
                 "while every one of them applied 0.0000 to every setpoint, with "
                 "active faults 0x0000, no limit asserted and nothing else to "
                 "see. A motor-rail power cycle was the only intervention, after "
                 "which all eight read 0x10 and all eight drive. "
                 "Controls: ids 3 and 4 held 0x54 with their sticky word already "
                 "cleared to 0x0000 while the other six were at 0x0380, so the "
                 "byte does not follow sticky faults. After the cycle the byte "
                 "stayed 0x10 through driving and through asserting and clearing "
                 "both limits, so it does not follow output or limit state "
                 "either. "
                 "ONE transition has been observed. What sets 0x54 is unknown "
                 "and no CAN command has been found that clears it. Treated as a "
                 "whole-byte signature with a rail-cycle remedy rather than as a "
                 "diagnosis.",
        settle_with="reproduce the non-driving state deliberately and confirm "
                    "byte 6 goes to 0x54 again; then narrow which of bits 2 and "
                    "6 carries it",
        used_by=("admin.legacy_other_signals_problem",
                 "swerve_drive controllers-can-drive startup gate",
                 "controller.print_diagnostics"),
 ),
    Claim(
        key="pre25.status0_byte6_bit_names_are_refuted", product=SPARKMAX,
        how=HARDWARE,
        what="the bit names this package used to print out of pre-25 byte 6 are "
             "wrong, and the byte is reported raw instead",
        evidence="rig-max. The field decoded as SPARK_MODEL, bits 6-7 "
                 "of byte 6 plus bits 0-1 of byte 7, read 1 before a power cycle "
                 "and 0 after it. A model cannot change, so the extraction is "
                 "wrong -- and 1 maps to Flex in MODEL_FOR_TYPE on a fleet of "
                 "SPARK MAX. "
                 "The bit positions were the FIRMWARE-25 STATUS_0 byte 6 layout "
                 "applied to a legacy frame -- 0x01, 0x10, 0x20 and the model in "
                 "bits 6-7 are byte for byte what sparksim.frames.encode_status_0 "
                 "builds for 25+. The same generation mix-up as the drive gate, "
                 "in a different call site. "
                 "MEASURED REPLACEMENT: on pre-25 Inverted is bit 1, 0x02. "
                 "Parameter 45 was written on id 3 and that bit followed it, with "
                 "the write confirmed by reading the parameter back; writing it "
                 "away cleared the bit again. Eight other parameters -- control "
                 "type, idle mode, both soft-limit enables, sensor type, voltage "
                 "compensation mode, follower id and follower config -- were "
                 "written the same way with readback confirming each, and none of "
                 "them appears in this byte. "
                 "The bit decoded as INVERTED, 0x10, is set on all eight in both "
                 "states while parameter 45 Inverted reads 0 on the hardware and "
                 "sparkmax_motor_defaults.yaml expects False. Not the software "
                 "sign flips: base.swerve.reverse_speeds mirrors right-side drive "
                 "speeds in the drive layer and writes no parameter. "
                 "The bit decoded as PRIMARY_HEARTBEAT_LOCK, 0x20, read 0 in both "
                 "states, so it never flagged the fleet that would not drive. "
                 "Bit 0 as Is Follower is left alone: it is REV's own wording, it "
                 "reads 0, and parameters 57 and 58 Follower ID and Follower "
                 "Config both read 0 on the same pass.",
        settle_with="place bits 2 and 6, the two that differ between a fleet "
                    "that drives and one that does not; every parameter swept "
                    "so far leaves them untouched",
        used_by=("controller.print_diagnostics",
                 "sparksim.frames.encode_status_0_sparkmax"),
 ),
    Claim(
        key="pre25.fault_bits_cantx_canrx", product=SPARKMAX, how=HARDWARE,
        what="canTx is bit 7 and canRx is bit 8 of the pre-25 fault word, "
             "measured, taking the count to three of sixteen on the day",
        evidence="rig-max. A CAN connector was pulled from the bus "
                 "while all eight controllers were powered and broadcasting. The "
                 "segment went silent, the gs_usb adapter went ERROR-PASSIVE with "
                 "37 error-warn and 10 error-pass events, and when the connector "
                 "was replaced every one of the eight read sticky 0x0380 -- bits "
                 "7, 8 and 9 exactly, decoding as canTx, canRx and hasReset. "
                 "hasReset was already latched from an earlier rail cycle, so the "
                 "two NEW bits are 7 and 8. The cause is unambiguous: the only "
                 "event was severing the bus, and severing a bus is precisely "
                 "what stops a controller transmitting and stops anyone "
                 "acknowledging it, which is what the CAN TX and RX error "
                 "counters count. "
                 "Not a deliberate experiment -- it happened during an unrelated "
                 "test -- but the confound-free kind: one event, two new bits, "
                 "and the two bits the event predicts.",
        used_by=("admin._LEGACY_FAULT_BITS",),
 ),
    Claim(
        key="pre25.status_frame_7_period_is_not_writable", product=SPARKMAX,
        how=HARDWARE,
        what="status frame 7 (0x067) does not take an api class 6 period write, "
             "so LEGACY_FRAME_MAX = 6 is a measured bound and not a cautious one",
        evidence="rig-max, id 3. 0x067 was broadcasting at REV's 250 "
                 "ms default. A two-byte 400 ms payload was sent to "
                 "0x020519C3 -- LEGACY_SET_PERIOD + (7 << 6) + dev, the identical "
                 "arithmetic and identical frame shape that moves frames 0 "
                 "through 6 on this fleet -- and the cadence did not move: it "
                 "read 250 ms before and 250 ms after, across a ten second "
                 "settling window. Restored and re-measured at 222 ms, which is "
                 "inside the tolerance for 250 and is measurement noise rather "
                 "than a change. "
                 "SCOPE, and this is the same caution the burn taught: it "
                 "settles the write AS SENT. The api class 6 form does not reach "
                 "frame 7. Whether some other mechanism does is untested, and the "
                 "obvious candidate is closed -- parameter 165 is Status 7 Period "
                 "but sits above LEGACY_PARAM_MAX, so it is not addressable on "
                 "this generation either.",
        used_by=("admin.LEGACY_FRAME_MAX",
                 "can_bus._SPARKMAX_STATUS_PERIODS_MS"),
 ),
    Claim(
        key="pre25.fault_bit_order", product=BOTH, how=VENDOR,
        what="the pre-25 16-bit fault word runs kBrownout 0, kOvercurrent 1, "
             "kIWDTReset 2, kMotorFault 3, kSensorFault 4, kStall 5, kEEPROMCRC "
             "6, kCANTX 7, kCANRX 8, kHasReset 9, kDRVFault 10, kOtherFault 11, "
             "kSoftLimitFwd 12, kSoftLimitRev 13, kHardLimitFwd 14, "
             "kHardLimitRev 15",
        evidence="REVLib 2024.2.4's own FaultID enum, CANSparkBase.h and "
                 "CANSparkBase.java, corroborated independently by REV's "
                 "SPARK-MAX-Types.proto, by the REV Hardware Client fault list on "
                 "firmware 24.0.1, and by third-party re-implementations of the "
                 "protocol. Four independent source classes were swept and all "
                 "sixteen positions agreed; the only variation was naming style. "
                 "Bit 9 is ALSO measured: three motor-rail cycles on rig-max "
                 " each left every controller at sticky 0x0200 with no "
                 "other bit set, and a rail cycle sets hasReset and nothing else. "
                 "Nothing was vendored to establish this -- the sources are cited, "
                 "not copied, and REVLib 2024.x is not in tests/support/spec/. "
                 "CORRECTION: docs/spark/FIELD-REPORTS.md:479 places overcurrent "
                 "at bit 11. It is bit 1. FAILURE-CATALOGUE.md:382-386 was right "
                 "and that conflict resolves in its favour. NOTE bit 2 is "
                 "kIWDTReset on 24.x; REVLib 1.1.5 and earlier called the same "
                 "position kOvervoltage, so an old decoder mis-NAMES it without "
                 "mis-placing it.",
        used_by=("admin._LEGACY_FAULT_BITS",
                 "admin.normalised_reading"),
 ),
    Claim(
        key="pre25.fault_bit_hasreset", product=BOTH, how=HARDWARE,
        what="hasReset is bit 9 of the pre-25 16-bit fault word, measured rather "
             "than read off a document. It is the only bit of the sixteen with "
             "hardware behind it",
        evidence="rig-max, three motor-rail cycles: two deliberate and "
                 "one from a battery swap. Every time, all eight controllers came "
                 "back with sticky 0x0200 and no other bit set, and a rail cycle "
                 "is precisely the event that sets hasReset and nothing else. "
                 "This is the hardware half of pre25.fault_bit_order, which "
                 "carries the other fifteen positions from REVLib's own enum. On "
                 "a MAX hasReset is the only signal that RAM configuration has "
                 "been lost and must be re-sent, so a decoder that cannot see it "
                 "reports a fleet that has lost every volatile setting as clean.",
        used_by=("admin.normalised_reading",
                 "admin._LEGACY_FAULT_BITS"),
 ),
    Claim(
        key="pre25.boot_throttle_not_rev_default", product=SPARKMAX, how=HARDWARE,
        what="the 50/100/100/500/1000/1000 cadence a running rig-max broadcasts is "
             "this package's own throttle, not a REV default. REV's pre-25 "
             "defaults are 10/20/20/50/200/200 with Status 7 at 250",
        evidence="rig-max, measured either side of a rail cycle. Before: "
                 "0x060 50, 0x061 100, 0x062 100, 0x063 500, 0x065 1000, 0x066 "
                 "1000, 0x067 250. After, with nothing having re-run "
                 "apply_boot_config: 10 / 20 / 20 / 50 / 200 / 200 / 250. That "
                 "matches can_bus._SPARKMAX_STATUS_PERIODS_MS on one side and "
                 "REV's documented pre-25 table on the other, and confirms "
                 "pre25.status0.period_default by measurement. 0x067 never moves "
                 "because the boot table stops at frame 6. 0x064 does not "
                 "broadcast in EITHER state, so its absence is this firmware not "
                 "emitting the alternate-encoder frame rather than a lost write. "
                 "The audit's expected_ms of 10 is therefore the right number "
                 "against the WRONG REFERENCE: it scores a running fleet against "
                 "REV's cold default, which is what produces the 'all 8 thinned "
                 "out together, the bus is dropping frames' false positive.",
        used_by=("admin.fault_frame", "admin._bus_level_finding"),
 ),
)

BY_KEY = {c.key: c for c in CLAIMS}


def for_product(product):
    """Claims that bear on one product, including the ones marked BOTH."""
    return [c for c in CLAIMS if c.product in (product, BOTH)]


def unsettled(product=None):
    """Claims this driver relies on without having checked them."""
    rows = for_product(product) if product else list(CLAIMS)
    return [c for c in rows if not c.settled]


def report(product=None):
    """Lines for `spark verify`, settled first so the gaps read last."""
    rows = for_product(product) if product else list(CLAIMS)
    order = {HARDWARE: 0, VENDOR: 1, INFERRED: 2, UNVERIFIED: 3}
    out = []
    for c in sorted(rows, key=lambda x: (order[x.how], x.key)):
        out.append(f"  [{c.how:<10}] {c.key}")
        out.append(f"               {c.what}")
        if not c.settled and c.settle_with:
            out.append(f"               SETTLE IT: {c.settle_with}")
    return out
