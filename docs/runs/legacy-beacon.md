# The 0x060 distress beacon, and what this driver does with it. No hardware involved: the finding comes from REV's own spec plus the
driver's real decode path. `probe_legacy_beacon.py` reproduces it.

## What REV says

`REV-spark-frames-2.1.0.json`, periodicFrames.LEGACY_STATUS_0, api 0x060,
implemented 0.0.1, **deprecated at 25.0.0**. REV describe it as existing "purely
to inform old software that is not aware of firmware version 25+ that the SPARK
is present".

On firmware 25+ it carries no data. Every signal has `decodedMin == decodedMax`,
so the whole payload is a constant:

    APPLIED_OUTPUT            bit 0,  16 bits   always 0
    FAULTS_AND_STICKY_FAULTS  bit 16, 32 bits   always 0xFFFFFFFF
    OTHER_SIGNALS             bit 48, 16 bits   always 0

REV's own note on the fault field: "Always has all faults set so that old
software knows that something is wrong."

Note the shape. It is ONE 32-bit field at bit 16, and not two 16-bit fields.

## What the driver does with it

`fault_frame()` returns 0x060 as the fault frame for a SPARK MAX, and
`decode_status_0_sparkmax()` reads active faults from bytes 2:4 and sticky faults
from bytes 4:6. Feeding it the payload above:

    decode_status_0_sparkmax(0000ffffffff0000)
      -> active_faults 65535, sticky_faults 65535, applied_output 0.0
    faults named:        other motorType sensor can temperature gateDriver escEeprom firmware
    sticky faults named: other motorType sensor can temperature gateDriver escEeprom firmware

So a healthy SPARK MAX on firmware 25+ reports all eight faults and all eight
sticky faults active, on every frame, with applied output pinned to zero. Nothing
distinguishes that from a controller that has genuinely died.

This is the 0x06CE failure class again. There, a healthy 13.7 V rail was read as
a fault bitfield, and gating recovery on it wedged a base permanently.

## Two further defects in the same code

`API_SETS["sparkmax"]` maps status_1 to **0x061**. That api appears nowhere in
frames 2.1.0; the spec has exactly one legacy periodic frame. The id reached the
driver from `spark_controller._CONFIGS[SPARK_MAX]`.

That same row is also internally incoherent. It mixes 0x060 (deprecated AT
25.0.0) with 0x2F0 (implemented AT 25.0.0), so no single firmware generation
matches it: a pre-25 MAX cannot broadcast 0x2F0 at all.

## Why the tests did not catch it

`tests/support/sparksim/frames.py` defines API_LEGACY_STATUS_1 = 0x061, and
`controller.py` broadcasts it for a simulated MAX. The simulator manufactures the
same fabricated bus the driver expects, so the suite is green against a bus that
cannot exist on firmware 25+.
