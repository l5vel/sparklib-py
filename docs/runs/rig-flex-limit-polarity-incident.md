# Incident: three swerve modules silently unable to drive

rig-flex, found and repaired. Root cause NOT established.

## What happened

Limit Switch Fwd Polarity and Limit Switch Rev Polarity (parameters 50 and 51)
were set to True on controllers 13 (steer/RF), 15 (steer/LF) and 16 (drive/LB).
REV's factory default for both is False.

On rig-flex's data-port wiring, True makes both hard limits read as
HARD_LIMIT_REACHED. A reached hard limit pins applied output to zero. So those
three controllers accepted every setpoint the drive stack sent, applied nothing,
and drew no current, while broadcasting at a perfect cadence with the right
serial and no fault bit set.

## Impact

Only one of the four swerve modules was fully functional.

    RB   drive OK    steer OK     fully working
    RF   drive OK    steer BLOCKED
    LF   drive OK    steer BLOCKED
    LB   drive BLOCKED  steer OK

Measured under teleop, 1579 setpoint frames to each of the eight, all eight in
the enable heartbeat mask:

    id 13 steer/RF  commanded 0.400  applied 0.000  0.00 A   limits FWD,REV
    id 15 steer/LF  commanded 0.400  applied 0.000  0.00 A   limits FWD,REV
    id 16 drive/LB  commanded 0.100  applied 0.000  0.00 A   limits FWD,REV
    the other five commanded and applied normally

Any closed-loop work measured against rig-flex's motion in this period was fitting
a model to a plant missing three of eight actuators. Trajectory tracking,
convergence, drive PI gains, deadzone thresholds and odometry-against-Quest are
all affected. Unit-level maths, arm work and Quest pose work are not.

Nothing in the stack noticed. `spark faults` printed the limit state in a table
and did not count it toward its exit code; `spark audit` did not read STATUS_1 at
all and exited 0.

## Timeline

Every line has evidence behind it except where marked as recollection.

    Wed 26th 09:00   factory reset performed                  (recollection)
    Wed 26th ~10:00  tested, driving normally                 (recollection)
    Thu 27th 11:09   candump: all eight byte6 = 0x40, no limits asserted,
                     6500 frames each, unanimous
                     the rig-flex capture, the session candump
    Thu 27th ~12:xx  blind RTR sweep of all 1024 api values on dev 17; that
                     controller halted via ENTER_SWDL_CAN_BOOTLOADER
    Thu 27th ~13:0x  motor-rail power cycle; bus came back silent; one
                     CLEAR_FAULTS per id woke all eight
    Thu 27th 13:03   tools/spark_param_write.py last modified
    Thu 27th 13:06   tools/spark_set_can_id.py last modified
    Thu 27th 17:59   passive snapshot: ids 13, 15 and 16 at byte6 = 0x43,
                     both limits asserted; the other five at 0x40
                     the post-cycle capture, spark-passive.yaml
    Fri 28th         still asserted; found, diagnosed and repaired

So the change happened inside a six-hour window on Thursday 27th, between 11:09
and 17:59.

## Diagnosis, and how it was proven

The cause was established by reproducing it on demand rather than by inference.
tools/spark_limit_polarity_repair.py --inject 16 --cycles 3, on a controller
that had already been repaired and was reading clean:

    cycle 1  polarity -> True   limits FWD,REV    polarity -> False   clear
    cycle 2  polarity -> True   limits FWD,REV    polarity -> False   clear
    cycle 3  polarity -> True   limits FWD,REV    polarity -> False   clear

Three for three, both directions. Before that run the evidence was one
directional -- writing False cleared a fault that was already there -- which is
equally consistent with the write merely disturbing something.

WRONG TURNS, recorded because they cost time. Both hard limits asserted at once
was read as a data-port hardware fault, since a mechanism cannot be at both end
stops: CD 455481, a misaligned breakout board holding a limit closed. Reseating
the connectors changed nothing, which should have retired that theory sooner
than it did. Nothing physical was ever wrong.

## Repair, and it survived a power cycle

Parameters 50 and 51 written to False on 13, 15 and 16 and persisted. All eight
then commanded and applied under teleop for the first time.

VERIFIED, and this is the test that matters, because a power cycle is
what exposed the fault on Thursday: the rail was cut and restored, and all eight
came back with their limits clear and sticky hasReset set. The persist held.

The reading itself used the wake-by-read procedure from PROBE-LOG section 11.
The bus came back silent, as it does here; one round of GET_FIRMWARE brought all
eight back with the sticky bytes untouched. The previously known recovery was a
Clear Faults frame per id, which would have erased the hasReset that proves the
cycle happened at all -- so the verification would have destroyed its own
evidence.

## Root cause: NOT ESTABLISHED

What wrote True is unknown. Ruled out by evidence:

  a factory or safe reset   both restore defaults, and the default is False, so
                            a reset would have fixed this rather than caused it
  this repository           nothing in this package writes parameters
                            50-53, five tests enforce it, and a grep across all
                            five workspace repos finds only a test asserting the
                            refusal
  RHC2 on the occasion      not opened (recollection)
  the drive stack           apply_boot_config sends Clear Faults and, on
                            SparkMax only, status-frame periods. Not polarity.

Not ruled out, and the leading candidate: tools/spark_param_write.py, a
general-purpose parameter writer taking --id --param --value with no protected-
parameter guard, last modified Thursday 13:03 -- inside the window and minutes
after the power cycle. A file mtime says it was edited, not that it was run, and
~/.bash_history is empty, so there is no record either way. Suggestive, not a
conclusion.

Also unexplained: why exactly three of eight, and why those three.

## What was changed so it cannot happen the same way twice

  spark audit now reads STATUS_1 and STATUS_0 flags and exits 1 on a fault, a
  reached limit, a heartbeat lock or a follower. It exited 0 on this bus all
  week. (admin.status_problems, wired into audit_problems and cmd_audit.)

  swerve_drive._verify_controllers_can_drive refuses to start the drive stack
  when a controller reports it cannot apply output, naming the id and the
  reason. The existing check asked whether controllers were talking, not whether
  they could move.

  tools/spark_param_write.py now refuses parameters 50-53 with a pointer to the
  narrow repair tool.

  tools/spark_param_write_log.py listens passively and logs every parameter
  write and every hard-limit transition on the bus with the kernel's timestamp.
  If this recurs, it names the frame and the moment instead of leaving a
  reconstruction from file mtimes.

  The expected Limit Switch Fwd/Rev Polarity is now declared as
  base.limit_switch_polarity in spark.yaml, false on rig-flex, and nothing
  named that value before. `spark faults` prints the declared setting under a
  reached limit, and both generations answer a read of parameters 50 and 51.

## Related observation: current, and why the peaks do not settle it

Not caused by this incident. Four teleop runs, peak current per controller:

    id            pre-fix   run 2   run 3   run 4 (after a power cycle)
    11 steer/RB     99.7    100.1   149.2   145.5
    13 steer/RF   blocked   149.9   136.6   107.1
    15 steer/LF   blocked   149.1   148.5   131.0
    17 steer/LB    128.4    106.7   127.6   149.9
    drives         80-90    54-83   77-96   81-98

Declared Smart Current Stall Limit 80 A, Current Chop 115 A. The STATUS_0
current field is 12 bits at 0.0366 A per count and saturates at 150.0 A, so
anything near that is a lower bound.

WHAT IS SOLID. The drives were already drawing 80-90 A before the repair, so
fleet-wide overcurrent is not something the fix caused. By run 4 all eight latch
a sticky overcurrent warning on every session. Temperatures stayed at 30-33 C
throughout, so nothing is thermally stressed today.

THE TWO THAT WERE BLOCKED ARE TRENDING DOWN across three runs, 149.9 to 107.1
and 149.1 to 131.0, which is consistent with stiffness from disuse working
itself loose rather than damage.

WHAT THESE NUMBERS CANNOT SUPPORT. Every one is a single maximum from a 10 ms
sample, taken during manually varied driving that was never the same twice. A
peak of 150 A held for one frame and one held for two seconds are entirely
different problems and this measurement cannot tell them apart. The apparent
upward trend on ids 11 and 17 is as likely to be a heavier turn of the sticks.

SO THE INSTRUMENT WAS CHANGED. tools/spark_teleop_watch.py now buckets every
STATUS_0 sample against the declared limits and reports dwell time -- how long
each controller spent above 80 A, above 115 A, and above 145 A -- rather than a
peak. A brief excursion at the current limit is ordinary on a stationary wheel;
seconds above it is not, and that is the number worth acting on.

That measurement has not been taken yet.
