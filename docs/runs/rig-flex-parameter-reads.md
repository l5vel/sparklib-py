# A SPARK Flex answers parameter reads, and the silence was ours, rig-flex, eight SPARK Flex on firmware 26.1.6, bus `can0`.
Most of this run held the motors disabled, with no setpoint commanded and no
enable heartbeat on the bus. The setpoint check below is the one exception, and
it is where that heartbeat ran. There id 17 ran at 0.15 duty for a quarter
second, which rotates one wheel in place and cannot translate the robot.

## The history

This tree has carried "firmware 26.1.6 answers no parameter read" since the probe log, and the Flex audit was designed around it. Parameters 0
through 15 were probed on rig-flex that day and every one came back silent, so the
silence was written down as a property of the firmware. On a reading
of REV's own frame spec put that in doubt without settling it. All 148 Read
Parameter and Get Parameter Types frames in `REV-spark-frames-2.1.0.json` carry
`rtr: true`, and this package sends every one of them as a zero-length DATA
frame. The one read that does answer on 26.1.6, GET_FIRMWARE, is the one it
sends as a real remote frame. That asymmetry became
`flex.param_reads_were_probed_as_data_frames`, marked UNVERIFIED, because rig-flex
was not attached that day.

It is attached now, and the firmware answers.

## What answers, and what does not

Three frame forms were sent to the same controller, for the same parameter,
inside one run:

    zero-length DATA frame, dlc 0        SILENT     <- what this package sends
    remote frame, dlc 0                  SILENT     <- what provenance predicted
    remote frame, dlc 8                  ANSWERS

So the hypothesis was right about the cause and wrong about the cure. A remote
frame alone draws nothing. The DLC has to request the eight bytes the reply
carries, and then the controller answers with an 8-byte data frame on the same
arbitration id.

## The coverage is wider than this package assumed

`read_param_pair` addresses apiClass 19 only, which covers parameters 128 to
159. That bound is recorded in several places in this tree as a property of the
protocol. It is a property of this code. The spec carries eight read classes:

    class 15  0x02053C00   params   0 -  31        class 19  0x02054C00   params 128 - 159
    class 16  0x02054000   params  32 -  63        class 20  0x02055000   params 160 - 191
    class 17  0x02054400   params  64 -  95        class 21  0x02055400   params 192 - 223
    class 18  0x02054800   params  96 - 127        class 22  0x02055800   params 224 - 255

Each class holds sixteen frames, one per parameter pair, at `base | (index << 6)
| device_id` where `index = (param - first) / 2`. Get Parameter Types is
apiClass 13 at 0x02053400, sixteen frames covering 0 to 255, and it answers on
the same remote-frame-dlc-8 form.

A full sweep of id 17 sent all 16 type frames and all 128 value frames. Every
one answered. Zero silent frames, 256 type slots read, 185 parameter ids
implemented. The capture is `records/flex-param-sweep-id17-20260909.json`.

## What that settles

Parameters 59, 60 and 61 read back 80, 20 and 10000 on all eight controllers.
Those are the Smart Current Stall Limit, the free limit and the Smart Current
Config RPM, and they are exactly what `flex.param.61.limit_rpm` carried as
INFERRED from REV's SPARK MAX parameters page. REV publish no Flex parameter
table, so this number had no Flex source until now. 10000 RPM sits above NEO
Vortex free speed, so the stall-to-free taper is disabled and the limit is a
flat 80 A at every speed on this fleet.

Parameter 11 reads 115.0 on all eight, which confirms kCurrentChop at REV's
documented default on a Flex.

Parameter 0 returns each controller's own CAN id on all eight. That is the
self-check anchor the bring-up procedure asks for, and it passes fleet-wide.

    id  role       p0   p11   p50 p51 p52 p53   p59 p60  p61
    10  drive/RB   10   115     0   0   1   1    80  20  10000
    11  steer/RB   11   115     0   0   1   1    80  20  10000
    12  drive/RF   12   115     0   0   1   1    80  20  10000
    13  steer/RF   13   115     0   0   1   1    80  20  10000
    14  drive/LF   14   115     0   0   1   1    80  20  10000
    15  steer/LF   15   115     0   0   1   1    80  20  10000
    16  drive/LB   16   115     0   0   1   1    80  20  10000
    17  steer/LB   17   115     0   0   1   1    80  20  10000

Both hard limits are enabled on every controller, and both polarities sit at
REV's factory default.

## The polarity trick works on 26.1.6, and the encoding is now proven

`docs/runs/rig-flex-limit-polarity-incident.md` and the bring-up
procedure both say the 0/1 encoding of parameter 50 can only be proven by
flipping the limit type in REV Hardware Client over USB-C. That was true while
the parameter could not be read. It is no longer the only route.

On id 17, a steer controller whose data-port inputs are unwired by doctrine:

    baseline          read p50=0 p51=0     limits reached: none
    write 50=1, 51=1  read p50=1 p51=1     limits reached: FWD, REV
    write 50=0, 51=0  read p50=0 p51=0     limits reached: none

The limits were caused and reversed on demand, which is the same standard bits
14 and 15 were held to on rig-max. An unwired data-port input rests high on the
SPARK's own pull-up. Polarity 0 reads that high line as not reached, so 0 is
normally closed, and 1 is normally open. The encoding is measured rather than
read off a document.

The restore was proved by reading the parameter back, which is the part that was
impossible yesterday. Nothing was persisted, so flash still holds 0 and a rail
cycle would have undone the injection anyway.

## An out-of-range write to a BOOL parameter lands on the permissive side

`tools/spark_limit_polarity_repair.py` records that firmware 26.1.6 accepts any
value on a BOOL parameter, because writing 2 returns Success and echoes 2. The
echo was the only instrument available, so the note reads the result as the echo
lying. The read says something worse. The firmware stores the 2.

With one write of 2 to parameter 50, while parameter 51 still held 1 as an
untreated control inside the same sample:

    p50 = 2   forward limit  NOT reached
    p51 = 1   reverse limit  reached

So 2 behaves as 0 on the forward limit, and 0 is the permissive value on this
wiring. On a robot whose data port needs polarity 1, a corrupted or mistyped
polarity write would silently release the interlock while reporting Success.
That is the fail-dangerous direction, and it argues for reading the parameter
back after every polarity write now that reading is possible.

Both parameters were restored to 0 and the restore was verified by a read.

## No FRC heartbeat is on this bus

Thirty seconds of passive listening, sending nothing, caught 36156 frames across
24 arbitration ids. Neither `0x01011840` nor arbitration id 0 appeared, and no
code in this package sends either. rig-flex carries no roboRIO, so the enable
story on this robot is the secondary heartbeat at `0x02052C80` that `SparkBus`
asserts, exactly as the code assumes.

This was measured at rest. A listen taken while the base drives would add the
host's own traffic and nothing else, since no other node on this bus can source
that frame. The at-rest result is what was measured, and the driving case stays
unmeasured.

The frame count is itself worth recording. The controllers broadcast freely for
the whole listen because the earlier reads woke every gated transmitter, which
is `flex.wake_by_read` behaving as recorded.

## What the audit found once it could read

`spark audit` checked one declared deviation on a Flex before this run and reads
all nine after it. The file listed twelve that morning, and 69, 128 and 194
moved to deviates: false the same day. On the live fleet it reported two things,
and they are different in kind.

**One controller missed part of its provisioning.** id 12, drive/RF, holds
Status 3, 5, 6 and 7 Period at REV's factory default of 20 ms where the declared
baseline asks for 50, 200, 200 and 250. The other seven match the file. Status 1
Period and Status 9 Period took on id 12 as they did everywhere else, so this is
a partial loss and not a factory reset. REV confirm that one dropped
configuration frame can leave a Flex in that state.

The effect is currently nil, and saying so matters as much as the finding. A 20 s
passive census shows only Status 0 and Status 1 transmitting on any controller,
because firmware 25+ sends the rest only if they are needed. So id 12's faster
periods drive no traffic today and would the moment those frames were enabled.

id 12 also holds 17 for Duty Cycle Sensor Prescaler where the other seven hold 7,
and 17 is REV's documented default for that id. Nothing declares parameter 153,
so `spark audit` says nothing about it, and `spark params` shows it because it
compares controllers against each other. Six of 185 parameters differed across the
fleet and five of the six were on id 12, which is the same story from a second
angle.

**Repaired the same day.** Parameters 161, 163, 164 and 165 were written to their
declared 50, 200, 200 and 250 on id 12, each read back, then committed with
`spark persist`. Parameter 153 was written back to 7 and persisted with them, so
all five of id 12's missed settings were restored. The audit went to no problems
found across eight controllers. The fleet-wide diff fell from six parameters to
one, parameter 13, which is the intended steer-against-drive gain split.

Nothing declares parameter 153, so `spark audit` reports nothing about it. The
value 7 came from the seven controllers that already held it, and no declared
file backs it. Adding 153 to `the sparkflex: block` is still a config
decision nobody has made. REV's parameter table says the duty-cycle sensor sets
its time base to the device clock divided by (value + 1). The Flex clock is
170 MHz and the MAX clock is 72 MHz. Nothing on rig-flex uses a duty-cycle sensor,
so the setting is inert here.

**The declared encoder counts were a typo.** All eight controllers hold 4096 for
Encoder Counts Per Rev and Alt Encoder Counts Per Rev, which is REV's factory
default. `the sparkflex: block` declared 409 for both and marked them
deviating. Four things point the same way: the SPARK MAX file declares 4096 for
the same two ids with `deviates: false`, REV's own default is 4096, no Python in
this tree reads either number, and 409 is not a count-per-rev any REV encoder
has. Corrected to 4096, which took the audit from twenty findings to four.

## A setpoint reaches applied output

The last open rig-flex question asked whether firmware 25+ has an equivalent of the
pre-25 state where a controller takes setpoints and applies nothing with every
fault field clean.

One steer module was commanded 0.15 for 0.25 s, which rotates a wheel in place
and cannot translate the robot. id 17 applied exactly 0.1500 and drew 17.11 A,
then returned to 0.0000 when commanded to zero. The other seven controllers sat
on the same bus under the same heartbeat and held 0.0000 throughout, which is the
untreated control.

So the commanded value reaches applied output on 26.1.6. That confirms the
healthy path. It does not prove the pathological state cannot occur, which one
run could not do either way.

## What the reads made possible

Two commands exist now that could not before, and both were exercised on this
fleet the same day.

`spark provision --id N` reads all 46 declared settings off one controller,
writes back what disagrees, and reads each write again, because the echo is not
evidence. It burns once at the end and only when every write read back correct.
A drifted PROTECTED parameter stops the whole run: parameter 2 is Motor Type,
which is CD 424550, a controller that lights up, answers the client and will not
turn, and 50 to 53 are the data-port interlock. Stopping is deliberate, since a
controller this tooling cannot fully restore should not be burned holding a
configuration nobody declared. Verified end to end on id 12: parameter 163 was
drifted to 20, the command found it, wrote 200, read it back and committed it.

`spark snapshot` records the parameter table into the baseline, over the
undeclared ids only. The declared file and the baseline partition the id space,
so one deliberate config edit is never reported twice. A disagreement with the
declared file is a fault, while a disagreement with the baseline is only a note.
It refuses to record the table while any undeclared id differs across the fleet.
That refusal cleared on rig-flex once parameter 153 went back to 7, and the
baseline now carries 138 undeclared ids.

## What an adversarial review of the new commands caught

The design for both commands was reviewed element by element after it was
implemented, which is the wrong order and showed why. Seven findings survived
into code changes.

Four were in `spark provision` as first written. It refused on
`unexpected_generation`, which is documented as a warning and is printed and
continued by every other caller; refusing there would have blocked a legitimate
run on a SPARK MAX updated to 25.0.0, whose declared values are still the right
ones. It called `duplicates()` without the generation, which resolves to
firmware 25+ by default and therefore detects nothing on a pre-25 bus, leaving
the duplicate gate blind on exactly the fleet it was copied from. It burned to
flash even when some drifted rows could not be written, making a partial
configuration permanent. And it took the write dialect from
`dominant_generation`, a bus-wide majority, when the command writes to ONE
controller and that controller's own generation is already in the reading.

Two were in the snapshot. The parameter sweep ran beside the incomplete-bus
refusal rather than after it, on a duplicated predicate, so a full sweep went out
against a bus the next line was about to refuse. And an id that NO controller
answered was simply absent from the capture, so a 16-id block lost to one timeout
would have shortened the table with nothing to show for it.

The seventh was prose. Several places now said the two generations use dialects
that "share no frame". What is measured is one direction only: PARAMETER_WRITE
and READ_PARAMETER are versionImplemented 25.0.0, so a 24.0.1 device drops them
in silence. Whether a firmware-25+ device answers the legacy api has never been
tried, and the generations do overlap on api class 6.

## What this changes

`SparkAdmin.read_param_pair` and `param_types` sent the wrong frame form and
addressed one read class out of eight. Both are fixed. `read_param` is deleted:
it read a parameter by putting a one-byte payload on PARAMETER_WRITE, so one
appended byte made it a write, and it was answered on neither generation.
Anything in this tree that reports a Flex setting as uncheckable, or presents 128
to 159 as the protocol's limit, overstated what the firmware refuses.
