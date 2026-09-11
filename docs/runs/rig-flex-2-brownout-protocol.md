# rig-flex-2 brownout / config-loss experiment

Question: does draining the pack cause SPARK controllers to lose persisted config?

Current state: 5 of 8 at REV default Status 1 Period, 3 hold the
provisioned 20 ms. Rail 10.40 V at rest. All faults clean.

## DO NOT change these -- they are the experiment

  - the 5 drifted controllers (9,10,11,12,14). Repairing them destroys the
    control condition.
  - the 3 intact controllers (13 steer/RB, 15 steer/LB, 16 drive/LB). These are
    the SUBJECTS. If draining causes config loss, these are what should drift.
  - serials in spark.yaml. Already recorded, and it is the only
    identity that survives a reset -- it is how you prove WHICH controller drifted
    rather than trusting a CAN id.

## DO NOT run during the drain

  - `spark repair --persist` or anything that writes flash. PERSIST_PARAMETERS
    blocks for up to a second and browns out easily; writing flash at low voltage
    introduces the very mechanism under test and confounds the result.
  - BaseHandler / the drive stack, until after step 1 -- startup clears sticky
    faults on every SparkFlex, which is the only record of a brownout.

## STEP 1 -- do this BEFORE draining further. It is the prerequisite.

The 3 controllers at 20 ms may be persisted, or may merely not have rebooted
since something set 20 into RAM. The bus cannot tell these apart. If they are
RAM-only they will drop to 250 on the next power cycle at ANY voltage, and you
would wrongly score that as brownout-induced.

    uv run spark faults          # BEFORE anything else; capture sticky state
    uv run spark status  > pre-cycle.txt
    # power-cycle the motor rail at the CURRENT voltage
    uv run spark faults          # <-- FIRST after the cycle. hasReset latches here
    uv run spark status  > post-cycle.txt
    uv run spark clear           # LAST, and only if the bus is silent
    diff pre-cycle.txt post-cycle.txt

ORDERING IS LOAD-BEARING: `spark clear` erases hasReset and sticky brownout, the
only record that the reboot happened. Running it before `spark faults` destroys
the observation. This was got wrong on the first run and cost a data point.

  3 still at 20 ms  -> they are genuinely persisted. Valid subjects. Proceed.
  3 dropped to 250  -> nothing was ever persisted. The config loss is explained
                       WITHOUT brownout, and there is no experiment to run --
                       fix provisioning instead.

## STEP 1 RESULT: PASSED. 13/15/16 held 20 ms across a power cycle
## at 10.24 V -- they are persisted, and that cycle caused no new loss.
## Experiment is valid. Proceed to step 2.

## STEP 2 -- drain, logging continuously

    python3 measure_rail.py --csv drain.csv --seconds 600   # repeat / background

Record at each meaningful voltage step (say every 0.5 V):

    uv run spark faults > faults-<volts>.txt      # sticky brownout / hasReset
    uv run spark status > status-<volts>.txt      # has any of 13/15/16 drifted?

The signal you are looking for is a controller moving 20 ms -> 250 ms, and
whether sticky `brownout` or `hasReset` latched at the same time.

## STEP 3 -- power-cycle at each step

Config loss shows up on BOOT, not while running. A controller keeps its RAM
config until it restarts. So the drift will only appear after a power cycle at
low voltage -- draining alone may show nothing.

## What counts as evidence

  brownout theory SUPPORTED: a controller at 20 ms drops to 250 after a
      power cycle at low voltage, with sticky brownout or hasReset latched, and
      did NOT drop at the step-1 cycle at higher voltage.
  brownout theory WEAK: drift appears with no sticky brownout/hasReset,
      or appeared at step 1 too.
  NOT brownout: the 3 drop at step 1.

## Caveat on the baseline

spark-baseline.yaml was captured while 5 were drifted, so it records 250 ms as
correct for them. Do not `spark audit` against it and read "clean" as healthy
during this test -- the Status 1 canary fires independently of the baseline, so
read `spark status` directly. Re-snapshot only after the experiment and repair.
