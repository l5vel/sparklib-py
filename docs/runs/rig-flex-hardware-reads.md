# rig-flex hardware run

> **Corrected.** Every conclusion below about a SPARK Flex answering
> no parameter read is wrong, and the reason is worth keeping. The reads went out
> as zero-length data frames, or as remote frames with dlc 0, and firmware 26.1.6
> ignores both. Sent as remote frames with dlc 8 they all answer, on eight api
> classes covering parameters 0-255, on every controller. This file is left as
> the record of what was believed at the time. See
> `docs/runs/rig-flex-parameter-reads.md`.

First run of the firmware-axis code against metal. rig-flex: 8 SPARK Flex, ids
10-17, on `can0` (gs_usb 1d50:606f). All read-only; nothing was written
and nothing persisted.

## 1. The bus is gated, and silence means nothing

A passive `candump` returned ZERO frames over repeated windows, and
`ip -d -s link` showed ERROR-ACTIVE with RX 0. One GET_FIRMWARE_VERSION brought
back all eight. This reproduces `provenance.flex.wake_by_read` exactly.

Recorded here because the silence was first read as "the SPARKs are not
powered". It is now section 0.5 of the spark-raw-can-protocol skill.

## 2. Firmware, all eight

    id 10..17    26.1.6

Wire evidence, id 11: remote request on arb 0x0205260b (api 0x098), reply
`1A 01 00 06 00 00 00 00` -> 0x1A=26, 0x01=1, 0x0006=6.

## 3. What a 26.1.6 SPARK Flex actually broadcasts

12 s capture after a wake, 72,322 REV motor frames, device ids 10-17:

    api 0x2E0   48001      STATUS_0
    api 0x2E1   24001      STATUS_1
    api 0x2F0     304      UNIQUE_ID
    api 0x098      16      firmware replies to our own queries
    api 0x060       0      NOT ONE

So a 26.1.6 device is on the 25+ frame set, and it does NOT emit the deprecated
0x060 compatibility beacon. `fw25.legacy_beacon` stays documented-but-unobserved:
the guard against scoring its all-ones word is still correct, and still has never
been seen on this fleet. Consistent with the earlier capture, which also had
no 0x060.

## 4. The driver read it correctly

`collect_status` with nothing declared, all eight controllers:

    generation=fw25+   observed=True   model=1 (kSparkFlex)
    13.12 - 13.19 V    0.00 A    25-29 C
    faults=[] sticky=[]  sticky_warnings=['hasReset']

dominant_generation -> fw25+, fault_frame -> STATUS_1 (0x2E1), expected 20 ms.

The generation was READ OFF THE WIRE on every device, not defaulted. A 13.1 V
rail decoded as a rail, which is the exact inverse of the 0x06CE incident.

hasReset sticky on all eight is expected after the power cycle.

## 5. 26.1.6 answers NO parameter reads, on any api

The open hypothesis was that "26.1.6 answers no parameter reads" described the
undocumented write-api path rather than the firmware. It does not. All three
paths were sent to id 11 and captured on the wire:

    arb 0x02054fcb   apiClass 19 idx 15   READ_PARAMETER_158_AND_159    no reply
    arb 0x0205364b   apiClass 13 idx  9   GET_PARAMETER_144_TO_159_TYPES no reply
    arb 0x0205380b   apiClass 14 idx  0   1-byte payload 0x9E (=158)     no reply
    arb 0x0205260b   apiClass  9 idx  8   GET_FIRMWARE                   ANSWERED

The arbitration ids match the values derived independently in
SPARK-PROTOCOL-GROUND-TRUTH.md (0x2054FC0 and 0x2053640, plus device id 11), so
the frames were correct and the firmware declined all three. The firmware query
answering in the same session rules out a dead or deaf controller.

HYPOTHESIS REFUTED. Rebuilding reads on apiClass 19/13 removes the read/write
hazard, which is worth having, but it does not unlock parameter reads on this
firmware. Every parameter value on rig-flex remains unreadable over CAN.

`read-paths-candump.log` is the raw capture.
