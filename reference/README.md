# Vendor primary sources

Verbatim copies of REV's own published specifications. Nothing here was written
by this project, and nothing here should be edited. When a claim about the SPARK
protocol is contested, these files settle it.

| File | What it is |
| --- | --- |
| `REV-spark-frames-2.1.0.json` | REV-Specs machine-readable CAN frame definitions. Every frame, signal, bit offset and scale, with a `versionImplemented` and `versionDeprecated` on each. |
| `REV-SparkParameters-v0.1.2.md` | REV-Specs parameter table: id, name, type, default and range. Section 1 carries the enum member order, which is what `sparklib.admin.PARAM_ENUM_MEMBERS` is pinned to. |
| `revlib-2026.0.2/SparkParameters.h` | REVLib 2026.0.2 parameter enum. The only primary source for `kStatus8Period = 199` and `kStatus9Period = 224`. |
| `revlib-2026.0.2/SparkLowLevel.h` | REVLib 2026.0.2 `PeriodicStatus` struct set, shared by `SparkMax` and `SparkFlex`. |
| `revlib-2025.0.3/SparkParameters.h` | The 2025.0.3 parameter enum, for diffing against 2026. |
| `revlib-2025.0.3/SparkLowLevel.h` | The 2025.0.3 struct set, which shows the same sharing. |

These files are byte-for-byte as REV published them, non-ASCII characters
included. The repo-wide ASCII rule stops here, because a vendor
specification edited for tooling no longer settles anything.

They live in the repository because tests read them, and because a reader has to be able to check a claim against them. An earlier copy sat in a
volatile session scratchpad, and evidence that disappears on reboot cannot be
re-checked. `tests/adversarial/test_param_sweep.py`, `tests/hardware/test_wire_injection.py`
and `tests/unit/test_param_enums.py` all assert against these files, so a clone on
any machine gets the same answer. The last one parses the enum members out of
`REV-SparkParameters-v0.1.2.md` and pins every ordinal to that file.

`tests/support/sparksim/frames.py` restates the frame layout by hand instead of
importing it. That is deliberate. A simulator built from the driver's own
constants could never catch those constants drifting, so
`tests/adversarial/test_simulator.py` compares the two sets in one place.

The reading of these sources is written up in
[docs/PROTOCOL.md](../docs/PROTOCOL.md).

[SOURCES.md](SOURCES.md) explains how every other kind of claim is graded,
and how to re-fetch the field-report corpus this repository does not mirror.
