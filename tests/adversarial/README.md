# The adversarial SPARK suite

Field failures the SPARK tooling currently calls healthy.

Every test here replays a failure that a team reported against real REV hardware --
cited by Chief Delphi URL in the test's docstring -- and asserts what the operator
needed the tool to say. The question is never "does this function work" but "does
the driver believe it succeeded while the controller misbehaved": a config write
that was answered but never applied, a CAN id change that lives only in RAM, a
period reading that is really a power dropout, a fault bit that is decoded and
then thrown away before anything can act on it.

Driver under test: `sparklib/admin.py` and its CLI
`cli.py`, plus the 2024-era `spark_controller.py` that the swerve stack
still instantiates. Firmware 26.1.6, frame layout REV-Specs 2.1.0, bus identity
rig-flex (ids 10-17, serials and roles out of the real config), so the findings read
the way an operator sees them.

## Running it

```bash
uv run python tests/adversarial/run_suite.py            # the entry point
uv run python tests/adversarial/run_suite.py --module faults -v
uv run python tests/adversarial/run_suite.py --list

uv run --with pytest pytest tests/adversarial -q        # the same tests
uv run --with pytest pytest -q -m adversarial           # from anywhere in tests/
uv run --with pytest pytest -q -m "not adversarial"     # everything else
```

`run_suite.py` is a reader of pytest's own reports, not a second test runner: it
runs every module, prints a per-module table, then groups the xfails under the
driver defect each one names, and exits non-zero **only** on a real failure, an
error or a collection error. It borrows pytest through `uv run --with pytest` if
the environment does not have it, so the first command above works from a bare
checkout.

The package carries the `adversarial` marker (applied to every item by
`conftest.py`, declared in `pyproject.toml`), and `testpaths = ["tests"]` means a
plain `pytest` collects it like everything else.

## What an xfail means here

**An xfail is the point.** It is a named driver gap with a live reproduction
attached, not a flake and not a disabled test. The `reason` string names the
missing capability; `run_suite.py` groups them, and `pytest -rx` prints them raw.
Every xfail is `strict=False`, and every one has been checked with `--runxfail` to
confirm it fails on its *final* assertion -- the fixture and the premise assertions
before it pass, so the test is proving the gap and not a broken setup.

An **XPASS is loud** -- but only if you look. `pytest -q` does not print it, so a
fixed defect leaves its xfail silently passing and the file stops being read as
coverage. Run `pytest -rX`, or `run_suite.py`, which reports them. When one appears,
confirm the driver really does the thing and delete the marker.

This is not theoretical. Thirteen xfails in the predecessor suite became live guards
when the D1 fix landed, nobody saw it, and an audit counting markers concluded the
file guarded almost nothing and deleted it. See History below.

The passing tests are not filler. They are the controls that make the xfails
mean something: they establish that the evidence is on the wire, that the driver
decodes it correctly, and that the negative cases stay clean. A test that only
asserted the defect could be satisfied by a driver that flags everything.

## The simulator

`tests/support/sparksim` -- imported as `sparksim`; `tests/adversarial/simulator.py`
re-exports it rather than restating it, so there is exactly one implementation of
the bus. Fixtures (`clock`, `sim`, `rig-flex`) are registered once, from
`sparksim.harness`, via `pytest_plugins` in `tests/conftest.py`.

| piece | what it models |
| --- | --- |
| `SparkBusSim` | a python-can `Bus`: `send`/`recv(timeout)`/`shutdown`, frames materialised from a schedule at their due time, an rx backlog with overrun, congestion loss, and per-frame drop reasons |
| `SimSpark` | one controller: **`ram` vs `flash`**, in-flight RAM commits, the value it *echoed* vs the value it *committed*, latched vs clearable faults, deafness windows, `write_log` / `persist_log` / `clear_log` / `can_id_log` |
| `SparkBehaviour` | the misbehaviour knobs -- dropped write responses, echo transforms, `apply_delay_s`, `persist_settle_s` (default **0.200 s**, i.e. a bare controller reproduces CD 432129), blackouts, refused SET_CAN_ID, latched fault masks, a controller that will not answer GET_FIRMWARE |
| `VirtualClock` | substituted for `admin.time`, so a 5 s inventory window, a 2 s post-flash blackout and a 3.6 s stall cost microseconds and nothing sleeps |
| invariants | the bus *refuses* to carry `ENTER_SWDL_CAN_BOOTLOADER` or any setpoint frame, and (opt-in) a protected-parameter write; a frame budget stops a runaway window |

The reason for a device model rather than a frame replayer is the hidden state:
`dev.flash`, `dev.write_log[-1].echoed` vs `.requested`, `dev.persist_log[-1].stale`.
**Every confirmed driver defect is a disagreement between the driver's conclusion
and one of those**, and a replayer cannot express the disagreement.

`test_simulator.py` is the simulator's own test suite -- encoder round-trips
against the driver's decoders, frame-base agreement with `admin`, clock and
scheduling behaviour, and the injection API. It is part of the suite because a
fault the simulator injects wrongly is a test that proves nothing.

## Adding a scenario

1. **Find the failure, not the function.** Start from
   `SPARK-FAILURE-CATALOGUE.md` or the corpus under
   `the Chief Delphi corpus (chiefdelphi.com)`. Put the citation in the
   docstring. If you cannot name a real report, it is a unit test and belongs in
   `tests/unit/`.
2. **Pick the module** from the table below, so one failure class has one home.
3. **Inject through the simulator**, never with a hand-built bus:
   ```python
   def test_a_latched_fault_is_named_by_the_audit(sim, roles):
       bus = sim(build_fleet())                    # the provisioned eight
       bus.set_fault(12, faults=["gateDriver"])    # the injection
       adm = attach(bus)                           # the REAL SparkAdmin
       problems = sa.audit_problems(adm.inventory(1.0), {}, roles)
       assert any("gateDriver" in p for p in problems), bus.explain(12)
   ```
   Injection API: `set_fault`, `clear`, `brownout`, `thermal_foldback`,
   `power_cycle`, `silence`, `bus_off`, `silent_until_cleared`, `congestion`,
   `set_period`, `disable_frame`, `revert_to_defaults`, `revert_to_id_zero`,
   `set_can_id`, and `schedule(at, fn)` as the escape hatch.
4. **Read it back through the real driver** -- `SparkAdmin`, `collect_status`,
   `audit_problems`, or a `spark` subcommand. A test that asserts on a decode
   helper in isolation cannot see a caller that uses it wrongly, which is how D1
   survived a green suite: the function was fine and `cmd_audit` never called it.
   When the defect is at a call site, drive the subcommand (see `_cli()` in
   `test_recovery.py`) or read the source with `ast` (see
   `test_the_audit_command_reads_a_status_payload`).
5. **Assert the outcome an operator needs**, not the driver's own arithmetic.
   Never restate the expression under test. Add `bus.explain(dev)` to the failure
   message -- it prints the whole timeline.
6. **Write a control.** Prove the healthy case stays clean and the neighbouring
   device is not flagged.
7. **If the driver cannot do it yet**, mark it
   `@pytest.mark.xfail(reason="<the missing capability>", strict=False)` and then
   run it with `--runxfail` to confirm it fails on the assertion you meant and not
   on a typo in the fixture.
8. **Mutation-check anything that passes**: break the driver line the test claims
   to cover, confirm the test goes red, restore, and diff. One pass at a time on a
   working tree -- two concurrent passes race on save/restore -- and judge
   caught/missed by the process exit code, never by grepping for "failed". Clear
   `__pycache__` between passes: restoring a same-size file inside one second
   leaves a stale `.pyc` that fakes both a missed mutant and a spurious failure.

## Catalogue class -> module

| class | subject | module | notes |
| --- | --- | --- | --- |
| **A** | configuration written, answered, does not stick | `test_config_write.py` | A1 CD 456184 (echo without commit), A2 CD 432129 (persist before the RAM commit settles), A4 flash wear. Driver defects **D2**, **D3**. |
| **A5/A6** | CAN config forgotten on power cycle, id that survives a reset | `test_identity.py` | a SET_CAN_ID that lands in RAM only; the persist that has to follow it. |
| **A (end to end)** | repair -> persist -> power cycle -> verify | `test_recovery.py` | section C: the only proof a repair lasted is a reboot. |
| **B** | identity and addressing | `test_identity.py` | B1 duplicate ids, B2 id 0 unconfigured, swap/replacement by serial, identify and set-id addressed by serial. |
| **B (repair)** | a repair aimed at an id two controllers answer | `test_recovery.py` | section D. |
| **B3/B5** | follower mode | `test_telemetry.py` | the `is_follower` bit reaching the audit; the legacy decoder that prints a closed limit as follower mode. |
| **C** | bus health and traffic | `test_bus_health.py` | C1 saturation, C2 dropouts and stalls, C3 disabled frames, C4/C6 foreign devices and contention. Driver defect **D4**. |
| **D** | faults that silence or disable a controller | `test_faults.py` | D1 gate driver, D3 sticky latching, D4 EEPROM; the STATUS_1 decode and `clear_faults`. Driver defects **D1**, **D5**. |
| **D (recovery)** | what a Clear Faults frame wakes, and what nothing wakes | `test_recovery.py` | sections A and B, including the proof that no admin frame can enter the bootloader. |
| **E** | motor, sensor and mechanical | `test_telemetry.py` | E2 encoder/JST faults as STATUS_1 bits, hard limits as interlocks, the applied-output/current pair (dead output stage, current-limit clamp). |
| **F** | firmware and tooling | `test_identity.py` | F2 mixed firmware against the baseline, and the controller that refuses GET_FIRMWARE. |
| **F (coverage)** | what the audit may claim it checked | `test_recovery.py` | sections E and F: exit codes and the coverage note. |
| **G** | closed-loop and control level | `test_telemetry.py`, `test_recovery.py` | G3 input/output mismatch (CD 477176); G2's Status 5 period, measurable and declared unverifiable. |
| -- | the simulator itself | `test_simulator.py` | encoders, clock, scheduler, injection API, and the safety invariants. |

## Confirmed driver defects

`run_suite.py` prints these with the tests that stand for each. Summarised:

| | |
| --- | --- |
| **D1** | *(CLOSED)* `audit_problems()` took no status argument and `cmd_audit` never called `collect_status()`, so a latched gate-driver fault at a perfect 20 ms period yielded "no problems found" and exit 0. `spark audit` now reads STATUS_1 and exits 1 naming the fault. |
| **D2** | `persist()` drains and sends immediately, and `cmd_repair` calls `write_param` then `persist`; the field fix (CD 432129) was >= 200 ms of settling. **Did not reproduce on 26.1.6**: a PERSIST sent ~1 ms after the write, with the rail cut straight after, still burned the new value. One controller, one trial, and it hunts a race -- evidence, not proof. Pacing is still worth doing; it is no longer the defect whose failure is permanent and invisible. |
| **D3** | `write_param()` has no retry, no inter-frame pacing, and never compares the echoed `d[2:6]` against `raw_u32` -- CD 456184's zeroed-kFF failure. |
| **D4** | `status_1_verdict()` sees only a mean period, so a dropout, a stall, a saturated bus and a real reverted config all read as "reverted". |
| **D5** | `clear_faults()` is fire-and-forget: it cannot tell a transient sticky fault from a hardware-latched gate-driver fault needing an RMA. |

## History, and the correction to it

This package replaces two single-file suites that used to live at
`tests/unit/test_spark_adversarial.py` and `tests/unit/test_spark_field_failures.py`.
Both were deleted once their coverage was believed to be folded into the modules
above.

**That belief was checked and was partly wrong.** The audit read
marker counts rather than running the files:

| file | recorded as | what running it showed |
|---|---|---|
| `test_spark_adversarial.py` | 48 tests, 46 xfail, "guarded almost nothing" | 13 XPASS -- the D1 fix had converted them to live guards weeks earlier |
| `test_spark_field_failures.py` | 22 tests, 0 xfail, "every one a passing guard" | 15 of 22 fail; they assert `SparkAdmin.scan`, `clear_faults(verify_seconds=)`, `audit_problems(probes=)` and `max_gap_ms`, none of which has ever existed here |

Absence of an xfail marker is not evidence a test passes, and presence of one is not
evidence it still fails. Both were restored and reconciled:

- `test_spark_field_failures.py` had **no guard unique to it** -- every one of its 22
  tests appears in its sibling or in this package -- so it is gone for good.
- The 16 live guards from `test_spark_adversarial.py` moved to
  `tests/unit/test_spark_audit_guards.py`, which is where the call-site rules and
  the D1 guards now live. Its 34 xfail-blocked tests were dropped because every one
  is twinned here, and this package owns the work list.

The principle stands and is what the mistake violated: two implementations of one
check mean the tests can pass against the copy nobody uses. The addition is that
**deciding which copy to keep requires running both.**
