# rig-flex-2 -- first run of the SPARK tooling

> **Corrected.** The line below saying Idle Mode, Closed Loop Sensor
> and P 0 cannot be read back on 26.1.6 is wrong, and this package's own read code
> produced that silence. Its pair reader addressed one api class, covering
> parameters 128 to 159, and it sent every read as a zero-length data frame, which
> firmware 26.1.6 ignores. Sent as remote frames with dlc 8 the reads answer,
> across eight api classes covering parameters 0-255. So REV Hardware Client over
> USB-C is no longer the only route to those settings. That was measured on rig-flex;
> rig-flex-2 runs the same 26.1.6 and should behave the same, though nobody has run it
> there. The counts of eleven and ten are stale as well, because the declared Flex
> file now marks nine settings as deviating. This file is the record of what was
> believed at the time. See
> `docs/runs/rig-flex-parameter-reads.md`.

Context: rig-flex-2 is under deliberate stress testing with a known-bad battery. The
low rail below is expected and is NOT being treated as a fault to fix.

## Bus inventory (uv run spark status, slcan0)

    id  role       serial     fw        status1
     9  steer/LF   53B5FDF1   26.1.6    250.0 ms   <- factory default
    10  drive/LF   1BED6384   26.1.6    250.0 ms   <- factory default
    11  steer/RF   885A61D7   26.1.6    250.0 ms   <- factory default
    12  drive/RF   ED85328F   26.1.6    250.0 ms   <- factory default
    13  steer/RB   4899DA52   26.1.6     20.0 ms
    14  drive/RB   18C4FEA9   26.1.6    250.0 ms   <- factory default
    15  steer/LB   2F57EAB7   26.1.6     20.0 ms
    16  drive/LB   82805562   26.1.6     20.0 ms

8/8 present, 8 distinct serials, all on firmware 26.1.6.
5 of 8 at REV's default Status 1 Period; 3 hold the provisioned 20 ms.
Split does not follow corners, so it is per-controller provisioning history
rather than one event.

## Rail (uv run spark voltage)

    mean 10.40 V, min 10.33, max 10.42, spread 0.07, all at 0.00 A, 27-33 C

Spread of 0.07 V across 8 controllers is tight -- the wiring is fine, the pack is
simply low. Expected under the stress test.

## Faults (uv run spark faults)

    all 8 clean: no active faults, no sticky faults, no sticky brownout, no hasReset

WEAK EVIDENCE, not proof of no brownout: SparkBus.init_controller clears sticky
faults on every SparkFlex at startup, so this only means "no event since the last
clear". If the drive stack ran after the config was lost, the evidence is gone.
To make it count, read faults BEFORE starting BaseHandler.

## HOW TRUTHFUL IS THE "FACTORY DEFAULT" VERDICT

Trustworthy, and separately, precise about what it does NOT cover.

DIRECTLY OBSERVED, no inference:
  - the CAN ID. The device is broadcasting AT that id -- the low 6 bits of the
    arbitration id ARE its configured id. Nothing is inferred.
  - the serial. api 0x2F0 is a 4-byte hardware value. It is not in REV's 199-entry
    parameter table at all, so no reset can touch it and there is no frame that
    writes one. It is manufacturing identity.
  - the firmware version, answered by the device itself (api 0x098).
  - the Status 1 broadcast period, measured off the wire.

THE CAN IDs DID NOT REVERT, and the bus proves it:
  - COMPLETE_FACTORY_RESET resets "all writable parameters... even CAN ID".
    A controller that had one would answer at id 0. None does.
  - RESET_SAFE_PARAMETERS spares "CAN ID, Motor Type, Idle Mode, PWM Input
    Deadband, and Duty Cycle Offset".
  - All 8 answer at their configured ids 9-16 with 8 distinct serials.
  => whatever happened was NOT a complete factory reset, and if it was a safe
     reset then Idle Mode (BRAKE) survived too.

WHAT IS INFERRED, and must not be read as observation:
  - the ROLE label ("steer/LF"). That is spark.yaml's can_ids mapping,
    i.e. the expectation. If an id had drifted, the tool would still label it by
    whatever that id means in config, and be confidently wrong. It is only
    trustworthy because the ids match config AND every serial is distinct.
  - "the config reverted". The tool measures ONE of the 11 settings that deviate
    from factory default. Idle Mode, Closed Loop Sensor and P 0 cannot be read
    back at all on 26.1.6. A controller can show 20 ms and still be misconfigured.

WORDING DEFECT FOUND AND FIXED: the status table said "CONFIG REVERTED to factory
default", which reads as "including the CAN ID" -- the opposite of what the bus
shows. Now: "Status1 at factory default (CAN ID intact)". The audit line was
widened the same way, naming it as one observable parameter and pointing at RHC2
for the other ten.

## Baseline caveat

`spark snapshot --write` ran while 5 controllers were drifted, so
spark-baseline.yaml records 250 ms as correct for those five. The canary is
independent of the baseline so `spark audit` still flags them, but re-snapshot
after repair or the drift is enshrined.

## Open question

rig-flex-2's chemistry is tagged SLA in config, but that was INFERRED from "other
bases use MK ES17-12", never measured on rig-flex-2. 10.40 V means very different
things per chemistry: dead flat for 12 V SLA, ~15-20% for 3S LiPo, near full for
3S LiFePO4. Confirm the actual pack before trusting any rail grading here.

---

## STEP 1 RESULT -- power cycle at 10.2 V

Rail had drifted down from 10.40 V to ~10.24 V over the session.

    before cycle: 9,10,11,12,14 = 250 ms; 13,15,16 = 20 ms
    after  cycle: 9,10,11,12,14 = 250 ms; 13,15,16 = 20 ms   (identical)

### Conclusion 1 -- the three are PERSISTED, not RAM-only

13 (steer/RB), 15 (steer/LB), 16 (drive/LB) held 20 ms across a full power
cycle. Their Status 1 Period is in flash. The confound is eliminated and they
are valid subjects for the drain test.

By elimination the other five never persisted it, or lost it earlier.

### Conclusion 2 -- first evidence AGAINST the simple brownout theory

A complete power cycle at 10.24 V -- far below the 11.8 V SLA floor -- produced
NO additional config loss. If low-voltage power cycling alone destroyed
persisted config, this was a good opportunity and nothing moved.

That does not clear brownout entirely. Untested: a cycle at even lower voltage,
and a brownout DURING a flash write, which is the mechanism with a plausible
physical story.

### PROTOCOL FLAW FOUND ON THIS RUN -- ordering matters

`spark clear` was run BEFORE `spark faults` after the cycle. The power cycle
would have latched hasReset on all eight; the clear erased it. So the post-cycle
fault reading proves nothing about what the reboot did.

This is the clear_faults defect the adversarial suite flagged: it destroys the
evidence it is about to collect, and it cost a real observation here.

CORRECT ORDER after every power cycle:

    uv run spark faults     # FIRST -- hasReset/brownout from the cycle just done
    uv run spark status     # often works without clearing
    uv run spark clear      # LAST, and only if the bus is silent

Note the bus was NOT silent after this cycle -- `spark clear` reported 8/8
broadcasting -- unlike rig-flex, where every controller came up silent. So the
clear may not have been needed at all.
