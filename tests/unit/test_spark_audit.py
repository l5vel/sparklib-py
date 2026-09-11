"""Audit logic: what code-only repair can and cannot prove about a motor.

These tests exist because the tooling repairs controllers over CAN without REV
Hardware Client, and the honest limits of that matter more than the happy path.
The audit has two instruments and they answer different questions. Watching the
wire measures a CADENCE, and only Status 0 and Status 1 are broadcast on 25+.
Reading a parameter reports a stored VALUE, for every id 0-255 on either
generation. Neither says whether the firmware applied what it stored, and
asserting that it did would be a lie the operator acts on.
"""

import pytest

from sparklib import admin as sa

S1 = sa.STATUS_1_API


# Appendix A declares Status 0-9, so a HEALTHY controller broadcasts all ten.
# Seeding only 0 and 1 modelled rig-flex as it is today -- statuses 2-9 lost in a
# reboot -- and the audit now reports that, correctly.
DECLARED_PERIODS = {0x2E2: 20.0, 0x2E3: 50.0, 0x2E4: 20.0, 0x2E5: 200.0,
                    0x2E6: 200.0, 0x2E7: 250.0, 0x2E8: 20.0, 0x2E9: 100.0}


def ctrl(serial, status_1_ms=20.0, firmware="26.1.6", full_status=True):
    periods = {sa.STATUS_0_API: 10.0}
    if status_1_ms is not None:
        periods[S1] = status_1_ms
    if full_status:
        periods.update(DECLARED_PERIODS)
    return {"serial": serial, "firmware": firmware, "periods_ms": periods}


ROLES = {10: "drive/RB", 11: "steer/RB"}
HEALTHY = {10: ctrl("AAAA0001"), 11: ctrl("BBBB0002")}
SERIALS = {10: "AAAA0001", 11: "BBBB0002"}


def test_healthy_bus_reports_no_problems():
    assert sa.audit_problems(HEALTHY, {}, ROLES, SERIALS) == []


# -- the config-loss canary ---------------------------------------------------

def test_factory_default_status_1_period_is_reported_as_reverted():
    """20 ms is provisioned per Appendix A; 250 ms is REV's default."""
    inv = {10: ctrl("AAAA0001", status_1_ms=250.0), 11: ctrl("BBBB0002")}
    problems = sa.audit_problems(inv, {}, ROLES, SERIALS)
    assert len(problems) == 1
    assert "factory default" in problems[0]
    assert "id 10" in problems[0] and "drive/RB" in problems[0]


def test_provisioned_period_is_not_flagged():
    assert sa.audit_problems({10: ctrl("AAAA0001", 20.0)}, {}, {10: "drive/RB"},
                             {10: "AAAA0001"}) == []


@pytest.mark.parametrize("ms, expect", [
    (20.0, "ok"), (19.0, "ok"), (250.0, "reverted"), (240.0, "reverted"),
    (None, "absent"), (100.0, "unexpected"),
])
def test_status_1_verdict(ms, expect):
    assert sa.status_1_verdict(ms) == expect


def test_missing_status_1_means_no_fault_reporting_at_all():
    """Faults live in STATUS_1. A controller not sending it looks healthy but
    cannot report a fault, which is worse than a fault."""
    inv = {10: ctrl("AAAA0001", status_1_ms=None)}
    problems = sa.audit_problems(inv, {}, {10: "drive/RB"}, {10: "AAAA0001"})
    assert len(problems) == 1
    assert "no STATUS_1" in problems[0]
    assert "reports no faults" in problems[0]


# -- identity: serial vs CAN id -----------------------------------------------

def test_swapped_controller_is_detected_by_serial():
    """Same CAN id, different hardware. An id-only scan cannot see this."""
    inv = {10: ctrl("DEADBEEF"), 11: ctrl("BBBB0002")}
    problems = sa.audit_problems(inv, {}, ROLES, SERIALS)
    assert any("swapped" in p and "DEADBEEF" in p for p in problems)


def test_missing_controller_is_named_with_its_role():
    problems = sa.audit_problems({10: ctrl("AAAA0001")}, {}, ROLES, SERIALS)
    assert any("id 11" in p and "steer/RB" in p and "not broadcasting" in p
               for p in problems)


def test_unconfigured_controller_on_the_bus_is_reported():
    """id 0 gets its own line, and it names the consequence rather than the
    bookkeeping. "not in devices" is true of any stray id; what matters
    about 0 is that REV treat it as unconfigured and will not enable it, so the
    controller answers, reports telemetry, and never actuates. CD 347357."""
    inv = dict(HEALTHY)
    inv[0] = ctrl("CCCC0003")           # factory-default id 0
    problems = sa.audit_problems(inv, {}, ROLES, SERIALS)
    hit = [p for p in problems if "id 0" in p]
    assert hit, "a controller on id 0 audits clean"
    assert any("never be enabled" in p.lower() for p in hit), (
        f"id 0 reported without saying it cannot actuate: {hit}")
    assert any("set-id" in p for p in hit), "the finding carries no remedy"


def test_audit_without_recorded_serials_cannot_detect_a_swap():
    """Documents the cost of skipping `spark learn-serials`."""
    inv = {10: ctrl("DEADBEEF"), 11: ctrl("BBBB0002")}
    assert sa.audit_problems(inv, {}, ROLES, known_serials={}) == []


# -- duplicate CAN ids --------------------------------------------------------

def test_duplicate_can_id_is_reported_with_both_serials():
    dups = {10: ["AAAA0001", "DEADBEEF"]}
    problems = sa.audit_problems(HEALTHY, dups, ROLES, SERIALS)
    assert any("AAAA0001" in p and "DEADBEEF" in p for p in problems)
    assert any("id scan cannot see this" in p for p in problems)


def test_duplicates_are_reported_before_other_problems():
    """A duplicate invalidates every id-addressed reading, so it must lead."""
    inv = {10: ctrl("AAAA0001", status_1_ms=250.0), 11: ctrl("BBBB0002")}
    problems = sa.audit_problems(inv, {10: ["AAAA0001", "DEADBEEF"]}, ROLES, SERIALS)
    assert "answered by" in problems[0]


# -- baseline comparison ------------------------------------------------------

def test_firmware_drift_against_the_baseline_is_reported():
    base = {10: {"serial": "AAAA0001", "firmware": "26.1.6"}}
    inv = {10: ctrl("AAAA0001", firmware="25.0.4")}
    problems = sa.audit_problems(inv, {}, {10: "drive/RB"}, {10: "AAAA0001"}, base)
    assert any("firmware 25.0.4" in p for p in problems)


def test_controller_in_baseline_but_absent_from_bus():
    base = {10: {"serial": "AAAA0001", "firmware": "26.1.6"},
            99: {"serial": "ZZZZ9999", "firmware": "26.1.6"}}
    problems = sa.audit_problems({10: ctrl("AAAA0001")}, {}, {10: "drive/RB"},
                                 {10: "AAAA0001"}, base)
    assert any("id 99" in p and "baseline" in p for p in problems)


# -- the blind spot, asserted so it cannot be quietly widened -----------------

def test_only_the_status_1_period_is_verifiable_over_can():
    """A period is knowable two ways and only one of them is a CADENCE. Watching
    the frame arrive works for Status 0 and 1 and for nothing else on 25+, since
    the rest are sent only if needed. Reading the parameter reaches all of them,
    which is a different instrument and is what `spark audit` uses now.

    REV, sparkflex-25.0.0 and sparkmax-25.0.0, verbatim: "Only sends periodic
    CAN frames if they are needed (except for frames 0 and 1, which are enabled
    by default)". So the ABSENCE of Status 2-9 from the wire is normal and says
    nothing about the configured period.

    This was briefly widened to 6 settings, on the reading that
    rig-flex's silent Status 2-9 meant a configuration lost in a reboot. Hardware
    appeared to confirm it. The release notes refuted it. Move a setting between
    the two sets deliberately -- do not let it drift, and check the firmware
    notes before assuming a silent frame is a fault.
    """
    assert set(sa.VERIFIABLE_DEVIATIONS) == {159}
    for pid in sa.VERIFIABLE_DEVIATIONS:
        assert pid in sa.API_FOR_PERIOD_PARAM, (
            f"parameter {pid} is called verifiable but drives no status frame, "
            "so there is nothing on the wire to measure")
    for pid in (161, 163, 164, 165, 224):
        assert pid in sa.UNVERIFIABLE_DEVIATIONS, (
            f"parameter {pid} drives a Status 2-9 frame, which 25+ firmware "
            "sends only if needed; its absence is not evidence")


def test_the_unverifiable_settings_are_the_ones_that_change_behaviour():
    unverifiable = sa.UNVERIFIABLE_DEVIATIONS
    assert 6 in unverifiable, "Idle Mode: BRAKE vs COAST changes how the base stops"
    assert 9 in unverifiable, "Closed Loop Control Sensor"
    assert 13 in unverifiable, "P 0"
    assert not set(unverifiable) & set(sa.VERIFIABLE_DEVIATIONS)


def test_coverage_note_states_what_was_not_checked():
    """A clean audit must never read as 'this motor is correctly configured'.

    The denominator is derived rather than written down here. It used to say
    "1 of 11" and went stale the day the limit-switch polarities were declared,
    which is the second copy of a number that has one real source --
    sparkflex_motor_defaults.yaml. tests/adversarial/test_recovery.py checks
    that the derivation itself agrees with that file.
    """
    total = len(sa.VERIFIABLE_DEVIATIONS) + len(sa.UNVERIFIABLE_DEVIATIONS)
    checked = len(sa.VERIFIABLE_DEVIATIONS)
    note = sa.coverage_note(8)
    assert f"{checked} of {total}" in note
    assert "not checked in this run" in note
    assert "Idle Mode" in note and "COAST" in note
    assert "RHC2" not in note,\
        "26.1.6 answers a parameter read, so USB-C is no longer the only route"


def test_coverage_note_counts_what_the_audit_read_on_25_plus():
    """The 25+ note has to shrink as the audit reads more, like the pre-25 one.

    Before the 25+ branch ignored checked_params entirely, so a
    setting the audit had successfully read still printed as unverified.
    """
    read = [6, 9, 161]        # ids that are still declared deviations
    note = sa.coverage_note(8, checked_params=read)
    assert "read over CAN and compared against the declared file" in note
    assert "Idle Mode" in note.split("not checked in this run")[0]
    total = len(sa.VERIFIABLE_DEVIATIONS) + len(sa.UNVERIFIABLE_DEVIATIONS)
    scored = len(sa.VERIFIABLE_DEVIATIONS) + len(read)
    assert f"{scored} of {total}" in note


def test_coverage_note_names_every_unverifiable_setting():
    note = sa.coverage_note(1)
    for name in sa.UNVERIFIABLE_DEVIATIONS.values():
        assert name in note, f"{name} silently dropped from the disclosure"


# -- the declared config file is the single source of truth -------------------
# sparkflex_motor_defaults.yaml is the file an operator edits to change what a
# motor should be provisioned to. If the canary or the repair path carried its
# own copy of a value, editing that file would silently change nothing -- which
# is exactly the trap these guard.

def test_the_declared_config_file_loads_and_covers_both_roles():
    d = sa.load_motor_defaults()
    assert d, "sparkflex_motor_defaults.yaml is missing or unreadable"
    for role in ("steer", "drive"):
        settings = sa.motor_settings(role)
        assert settings, f"no settings resolved for {role}"
        assert sa.PARAM_STATUS_1_PERIOD in settings


def test_the_two_roles_differ_only_where_the_file_says_they_do():
    steer, drive = sa.motor_settings("steer"), sa.motor_settings("drive")
    differing = {pid for pid in steer if steer[pid]["value"] != drive[pid]["value"]}
    assert differing == {13}, (
        f"steer and drive should differ only at P 0 (13), got {differing}")
    assert steer[13]["value"] != drive[13]["value"]


def test_editing_the_file_changes_the_canary_target(tmp_path, monkeypatch):
    """The point of the file: change the declared value, the audit follows."""
    # 100 ms, not 40: the verdict tolerance is +/-30 ms, so 40 would still read
    # as "ok" against a declared 20 and the test would prove nothing.
    # Block form, not flow form: the closing "}}" of an f-string brace pair is a
    # literal "}}" in the following plain string, which is invalid YAML. That
    # made load_motor_defaults return None and the assertion read the fallback.
    edited = tmp_path / "defaults.yaml"
    edited.write_text(
        "meta: {}\n"
        "common:\n"
        "  - name: Status 1 Period\n"
        f"    param_id: {sa.PARAM_STATUS_1_PERIOD}\n"
        "    type: UINT32\n"
        "    value: 100\n"
        "    rev_default: 250\n"
        "    deviates: true\n"
        "by_role: []\n")
    monkeypatch.setattr(sa, "_defaults_path", lambda *a, **k: str(edited))
    assert sa.declared_status_1_period_ms() == 100.0
    assert sa.status_1_verdict(100.0) == "ok", "the edited value must now read as ok"
    assert sa.status_1_verdict(20.0) == "unexpected", (
        "the previously-declared value must stop reading as ok once the file changes")


def test_the_canary_falls_back_when_the_file_is_unreadable(tmp_path, monkeypatch):
    """A checkout without the file must still audit, not crash."""
    monkeypatch.setattr(sa, "_defaults_path",
                        lambda *a, **k: str(tmp_path / "nope.yaml"))
    assert sa.declared_status_1_period_ms() == float(sa.APPENDIX_A_STATUS_1_PERIOD_MS)
    assert sa.status_1_verdict(20.0) == "ok"


def test_deviating_settings_are_the_ones_a_factory_reset_drops():
    dev = sa.deviating_settings("drive")
    assert sa.PARAM_STATUS_1_PERIOD in dev, "the canary parameter must deviate"
    assert 6 in dev, "Idle Mode BRAKE deviates from the COAST default"
    assert len(dev) < len(sa.motor_settings("drive")), "not everything deviates"


# -- remedies ---------------------------------------------------------------
#
# `spark faults` printed eight controllers latching overcurrent and said nothing
# about what to do next. The remedy table exists so that never recurs, and these
# tests read the callers, not just the table: a bit added to the decoder without
# a remedy, or a print path that drops the remedy, both fail here.

def test_every_bit_the_decoder_can_emit_has_a_remedy():
    from sparklib import admin as sa
    emittable = set(sa._FAULT_BITS) | set(sa._WARNING_BITS)
    missing = sorted(emittable - set(sa.BIT_REMEDIES))
    assert not missing, (
        f"STATUS_1 can raise {missing} and nothing tells the operator what to "
        "do about it; add an entry to spark_admin.BIT_REMEDIES")


def test_no_remedy_is_a_restatement_of_the_bit_name():
    """A remedy has to name an action, not spell the bit out again."""
    from sparklib import admin as sa
    for bit, text in sa.BIT_REMEDIES.items():
        assert len(text.split()) >= 8, f"{bit} remedy is too thin to act on"
        assert text.lower() != bit.lower()


def test_cmd_faults_prints_the_remedy_for_a_bit_it_reports(capsys, monkeypatch):
    """The call site, not the table: cmd_faults must reach BIT_REMEDIES."""
    from types import SimpleNamespace
    from sparklib import admin as sa
    from sparklib import cli as spark_cli

    monkeypatch.setattr(spark_cli, "_open", lambda: _NullAdmin())
    monkeypatch.setattr(spark_cli, "collect_status", lambda bus, w, **kw: {
        10: {"status0": {"voltage_v": 13.0, "current_a": 0.0, "motor_temp_c": 29,
                         "hard_forward_limit": False, "hard_reverse_limit": False,
                         "soft_forward_limit": False, "soft_reverse_limit": False},
             "status1": {"faults": [], "warnings": [],
                         "sticky_faults": [], "sticky_warnings": ["overcurrent"]}}})
    spark_cli.cmd_faults(SimpleNamespace(window=0.1))
    out = capsys.readouterr().out
    assert "sticky-warn: overcurrent" in out
    head = sa.BIT_REMEDIES["overcurrent"].split(".")[0]
    assert head in " ".join(out.split()), (
        "cmd_faults reported a bit without printing its remedy")


class _NullAdmin:
    bus = None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_every_check_that_reports_a_problem_names_a_fix():
    """AST over the source: a function that reports a problem and never names a
    next step leaves an operator stuck.

    Scope is the function, because that is the unit of output a person reads --
    a table row's remedy may sit in a block printed after the last row. The
    trouble text is matched over every string in the function, not just the one
    inside the print, because _verify_controllers_can_drive builds its message
    in a variable and raises that.
    """
    import ast
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[2] / "sparklib"
    TROUBLE = ("refus", "cannot", "could not", "failed", "silent", "missing",
               "wedge", "not broadcasting", "disabled", "invalid", "unreadable",
               "not on the bus", "no response", "went dark", "not up", "wrong")
    ACTION = ("fix", "uv run", "sudo ", "replug", "re-sync", "uv sync", "run `",
              "confirm", "cut base power", "power cycle", "estop clear",
              "listen longer", "check ", "retry", "edit ", "recovery")

    def reports(fn):
        return any(isinstance(n, ast.Raise) or (
            isinstance(n, ast.Expr) and isinstance(n.value, ast.Call)
            and isinstance(n.value.func, ast.Name) and n.value.func.id == "print")
            for n in ast.walk(fn))

    bare = []
    # cli.py is the operator surface. admin.py raises and cli.py prints, so a
    # message an operator reads is built here or nowhere.
    for path in (root / "cli.py",):
        for fn in ast.walk(ast.parse(path.read_text())):
            if not isinstance(fn, ast.FunctionDef) or not reports(fn):
                continue
            strings = " ".join(n.value.lower() for n in ast.walk(fn)
                               if isinstance(n, ast.Constant)
                               and isinstance(n.value, str))
            if any(t in strings for t in TROUBLE) and\
                    not any(a in strings for a in ACTION):
                bare.append(f"{path.name}:{fn.name}")

    assert not bare, (
        "these functions report a problem and never name a next step, so an "
        "operator reads the output and is stuck: " + ", ".join(sorted(set(bare))))


# -- Appendix A completeness --------------------------------------------------


def test_the_defaults_file_carries_all_ten_appendix_a_status_periods():
    """Appendix A of the assembly manual configures Status 0 through Status 9.

    The file carried 0 through 7 only, because `rev_parameter_index.tsv` stops
    at 198 and the last two ids are not where the run would put them:

        Status 0..7 Period   158..165   contiguous
        Status 8 Period      199        NOT 166
        Status 9 Period      224        NOT 167

    166 and 167 are MAXMotion Max Velocity 0 and Max Accel 0, both FLOAT.
    Continuing the run would write a status period into a motion limit. The ids
    come from REVLib SparkParameters.h.
    """
    from sparklib import admin as sa

    rows = {r["name"]: r for r in sa.load_motor_defaults()["common"]}
    want = {
        "Status 0 Period": (158, 10), "Status 1 Period": (159, 20),
        "Status 2 Period": (160, 20), "Status 3 Period": (161, 50),
        "Status 4 Period": (162, 20), "Status 5 Period": (163, 200),
        "Status 6 Period": (164, 200), "Status 7 Period": (165, 250),
        "Status 8 Period": (199, 20), "Status 9 Period": (224, 100),
    }
    missing = sorted(set(want) - set(rows))
    assert not missing, f"Appendix A status frames absent from the file: {missing}"

    for name, (pid, value) in want.items():
        assert rows[name]["param_id"] == pid, (
            f"{name} is parameter {rows[name]['param_id']}, Appendix A and "
            f"SparkParameters.h say {pid}")
        assert rows[name]["value"] == value, (
            f"{name} is {rows[name]['value']}, Appendix A says {value}")


def test_no_status_period_is_written_into_a_maxmotion_parameter():
    """166 and 167 are MAXMotion Max Velocity 0 and Max Accel 0, both FLOAT.

    Assuming Status 8 and 9 continue the 158-165 run puts a period into a motion
    limit, which the controller would accept and act on.
    """
    from sparklib import admin as sa

    periods = {r["param_id"] for r in sa.load_motor_defaults()["common"]
               if "Status" in r["name"] and "Period" in r["name"]}
    assert not (periods & {166, 167}), (
        f"a status period is declared at {sorted(periods & {166, 167})}, which "
        "is a MAXMotion motion limit, not a frame period")


# -- SPARK_MODEL is newer than STATUS_0 --------------------------------------


def test_the_model_check_is_skipped_on_firmware_that_does_not_report_a_model():
    """REV added SPARK_MODEL to STATUS_0 in sm-26.1.0. On 25.0.0 through 26.0.x
    those bits are reserved, so the field decodes as 0.

    Comparing that against MODEL_FOR_TYPE reports every controller on the bus as
    "a different controller is answering that address" -- the same false
    positive as scoring a frame the firmware never broadcasts, which was written
    and reverted.
    """
    from sparklib import admin as sa

    old_fw = {10: ctrl("AAAA0001", firmware="25.0.2"),
              11: ctrl("BBBB0002", firmware="25.0.2")}
    for r in old_fw.values():
        r["periods_ms"] = dict(r["periods_ms"])
    status = {d: {"status0": {"spark_model": 0}, "status1": {},
                  "generation": sa.GEN_FW25} for d in old_fw}

    problems = sa.audit_problems(old_fw, {}, ROLES, SERIALS, status=status,
                                 controller_type="sparkflex")
    assert not any("SPARK model" in p for p in problems), (
        f"firmware 25.0.2 does not report a model, and the audit scored one "
        f"anyway: {[p for p in problems if 'SPARK model' in p]}")


def test_the_model_check_still_runs_on_firmware_that_does_report_one():
    """The gate must not silence a genuinely swapped controller on 26.1.0+."""
    from sparklib import admin as sa

    inv = {10: ctrl("AAAA0001", firmware="26.1.6"),
           11: ctrl("BBBB0002", firmware="26.1.6")}
    status = {d: {"status0": {"spark_model": 2}, "status1": {},
                  "generation": sa.GEN_FW25} for d in inv}

    problems = sa.audit_problems(inv, {}, ROLES, SERIALS, status=status,
                                 controller_type="sparkflex")
    assert any("SPARK model" in p for p in problems), (
        "a bus provisioned for SPARK Flex (model 1) reporting model 2 on 26.1.6 "
        "is a real finding and must still be reported")


@pytest.mark.parametrize("version,reports", [
    ("26.1.6", True), ("26.1.0", True), ("26.0.9", False),
    ("25.0.2", False), ("1.6.3", False), (None, False), ("unknown", False),
])
def test_which_firmware_reports_a_spark_model(version, reports):
    from sparklib import admin as sa
    assert sa.firmware_reports_spark_model(version) is reports



def test_a_controller_that_moved_to_a_non_zero_id_is_named_by_serial():
    """The serial join, at an id where the id-0 rule cannot cover it.

    A controller that wandered to id 5 produces two unlinked lines -- "id 12 is
    missing" and "id 5 is not in devices" -- and the operator has to guess
    which of eight moved. The serial that identifies it is already in the
    inventory. The recovery, `spark set-id --serial X --to 12`, needs exactly
    that serial. CD 376796.

    Mutation-checked: without this, dropping the join leaves the suite green,
    because the id-0 rule happens to print a serial too.
    """
    inv = {10: ctrl("AAAA0001"), 5: ctrl("BBBB0002")}   # 11 moved to 5
    problems = sa.audit_problems(inv, {}, ROLES, SERIALS)

    joined = [p for p in problems if "BBBB0002" in p and "5" in p]
    assert joined, (
        "nothing joins the serial found on id 5 to the id 11 it belongs to; "
        f"the operator gets two unlinked lines: {problems}")
    assert any("11" in p for p in joined), (
        "the finding must name the id it should go back to")
    assert any("set-id" in p and "BBBB0002" in p for p in joined), (
        "the remedy must carry the serial, which is what addresses it")


# -- the pre-clear record ----------------------------------------------------
#
# `clear_faults` always read before it cleared, but the record only ever reached
# the terminal. On a steer-stress run wiped hasReset on all eight of
# rig-flex before anyone read it, because any DriveTrain construction clears Flex
# stickies via apply_boot_config. A printed record dies with the shell.


@pytest.mark.parametrize("bits,expect", [
    (["hasReset", "brownout"], "brownout"),
    (["brownout", "hasReset"], "brownout"),
    (["hasReset"], "power-cycle"),
    (["brownout"], "sagged-without-reset"),
    ([], "none"),
    (None, "none"),
    (["overcurrent"], "none"),
])
def test_classify_reset_separates_a_brownout_from_a_power_cycle(bits, expect):
    """The two need opposite responses. A brownout is a POWER PATH fault and
    re-provisioning buys time until the next sag; hasReset alone is an ordinary
    power cycle with nothing to chase."""
    from sparklib import admin as sa
    assert sa.classify_reset(bits) == expect


def test_the_pre_clear_record_is_written_and_carries_the_classification(tmp_path):
    import json
    from sparklib import admin as sa

    record = {12: {"sticky_warnings": ["hasReset", "brownout"],
                   "sticky_faults": [], "faults": [], "warnings": [],
                   "cleared": True},
              13: {"sticky_warnings": ["hasReset"], "sticky_faults": [],
                   "faults": [], "warnings": [], "cleared": True}}
    status = {12: {"status0": {"voltage_v": 6.1, "current_a": 0.0},
                   "status1": {}, "generation": "fw25+"}}

    path = sa.write_fault_record(record, status=status,
                                 directory=str(tmp_path), stamp="TEST")
    saved = json.load(open(path))

    assert saved["controllers"]["12"]["reset_kind"] == "brownout"
    assert saved["controllers"]["13"]["reset_kind"] == "power-cycle"
    assert saved["controllers"]["12"]["sticky_warnings"] == ["hasReset", "brownout"]
    assert saved["controllers"]["12"]["volts"] == 6.1, (
        "the rail at capture is what says whether a brownout is plausible")


def test_the_record_survives_a_reading_that_carries_no_status():
    """clear_faults can run on a bus that answered no status frames. The record
    must still be written -- that is the case where the bits are all there is."""
    import json
    import tempfile
    from sparklib import admin as sa

    with tempfile.TemporaryDirectory() as d:
        path = sa.write_fault_record({12: {"sticky_warnings": ["hasReset"]}},
                                     status=None, directory=d, stamp="T")
        saved = json.load(open(path))
    assert saved["controllers"]["12"]["reset_kind"] == "power-cycle"
    assert "volts" not in saved["controllers"]["12"]


def test_cmd_clear_persists_before_it_clears():
    """The call site. write_fault_record existing proves nothing about whether
    cmd_clear calls it, and the whole defect was a read that went nowhere."""
    import ast
    import pathlib

    cli = pathlib.Path(__file__).resolve().parents[2] / "sparklib" / "cli.py"
    fn = next(n for n in ast.walk(ast.parse(cli.read_text()))
              if isinstance(n, ast.FunctionDef) and n.name == "cmd_clear")
    calls = [n for n in ast.walk(fn) if isinstance(n, ast.Call)]
    names = {getattr(c.func, "attr", None) or getattr(c.func, "id", None) for c in calls}

    assert "write_fault_record" in names, (
        "cmd_clear does not persist the pre-clear capture; the sticky bits are "
        "the only record a brownout leaves and printing them is not keeping them")
    assert "clear_faults" in names


# -- the declared configuration is product-specific ---------------------------


@pytest.mark.parametrize("product", ["sparkflex", "sparkmax"])
def test_each_product_resolves_a_file_that_declares_that_product(product):
    """`meta.applies_to` has been in the file since it was written, and until nothing selected on it, so a SPARK MAX base resolved the Flex file
    and read Flex values with nothing to say so."""
    assert sa.motor_defaults_key(product) == product
    assert sa.motor_defaults_product(controller_type=product) == product, (
        f"{sa.motor_defaults_file(product)} no longer declares meta.applies_to "
        f"for {product}, or declares something motor_defaults_product cannot map")


def test_an_unset_product_still_resolves_the_flex_block():
    """The module is used without a robot config -- by the hardware tier, by
    debug scripts, by anything that imports it directly. Leaving the product
    unset resolves what the whole tree resolved before the MAX file existed,
    rather than raising or guessing."""
    was = sa.set_controller_type(None)
    try:
        assert sa.motor_defaults_key() == "sparkflex"
    finally:
        sa.set_controller_type(was)


def test_the_declared_product_follows_set_controller_type():
    was = sa.set_controller_type("sparkmax")
    try:
        assert sa.motor_defaults_key() == "sparkmax"
        assert sa.motor_defaults_product() == "sparkmax"
    finally:
        sa.set_controller_type(was)


def test_a_robot_is_refused_a_file_written_for_the_other_product():
    """Selecting the file by product makes the mismatch unreachable by accident.
    It stays reachable by a bad edit to meta.applies_to or to
    base.controller_type, and that is what this guard is for now.

    Both directions, so a guard that always refuses cannot pass.
    """
    sa.require_motor_defaults_for("sparkflex")           # the shipped pair
    sa.require_motor_defaults_for("sparkmax")            # also the shipped pair

    flex_file = {"meta": {"applies_to": "SPARK Flex"}}
    with pytest.raises(sa.MotorDefaultsWrongProduct) as err:
        sa.require_motor_defaults_for("sparkmax", defaults=flex_file)
    msg = str(err.value)
    assert "sparkmax" in msg, (
        "the refusal must name the declared block it read")
    assert "sparkflex" in msg and "controller_type" in msg, (
        "the refusal must name what the file declares and the config key that "
        "disagrees with it, because either one could be the wrong half")


def test_spark_repair_refuses_before_it_opens_the_bus_on_a_product_mismatch():
    """The call site, not the helper.

    `spark repair` writes what the declared configuration says, so the product
    check has to happen before the bus is opened and before anything is sent. A
    refusal that arrives after the write is not a refusal.
    """
    import ast
    import pathlib

    cli = (pathlib.Path(__file__).resolve().parents[2]
           / "sparklib" / "cli.py")
    fn = next(n for n in ast.walk(ast.parse(cli.read_text()))
              if isinstance(n, ast.FunctionDef) and n.name == "cmd_repair")

    checks = [n.lineno for n in ast.walk(fn)
              if isinstance(n, ast.Call)
              and getattr(n.func, "attr", None) == "require_motor_defaults_for"]
    assert checks, "cmd_repair never checks which product the defaults apply to"

    opens = [n.lineno for n in ast.walk(fn)
             if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "_open"]
    assert opens and min(checks) < min(opens), (
        f"cmd_repair opens the bus at {min(opens)} before checking the declared "
        f"product at {min(checks)}")


def test_a_missing_applies_to_key_blocks_nothing():
    """Absence of an answer is not an answer. A file with no meta.applies_to is
    the historical shape and must keep working rather than refusing every robot."""
    assert sa.motor_defaults_product({"meta": {}}) is None
    assert sa.motor_defaults_product({}) is None
    sa.require_motor_defaults_for("sparkmax", defaults={"meta": {}})


# -- the throttle this package writes is not a finding ------------------------

def test_the_pre25_boot_throttle_is_not_reported_as_drift():
    """A cadence this package wrote itself is not drift.

    `apply_boot_config` throttles every pre-25 status frame at boot, because
    REVLib's defaults push enough receive traffic to risk wedging the gs_usb
    dongle. A running fleet therefore broadcasts slower than REV's default by
    design, and an audit that knows only expected-versus-factory has no third
    category for it and reports the package's own writes as loss.
    """
    from sparklib.can_bus import _SPARKMAX_STATUS_PERIODS_MS
    throttle = float(_SPARKMAX_STATUS_PERIODS_MS[0])
    rev_default = float(sa.REV_DEFAULT_LEGACY_STATUS_0_PERIOD_MS)
    assert throttle != rev_default, (
        "this test is meaningless unless the two differ; if the throttle is ever "
        "set to REV's default, delete it")

    ff = sa.fault_frame(sa.GEN_PRE25)
    assert ff["expected_ms"] == throttle, (
        "expected_ms is what this package PROVISIONS, on both generations -- the "
        "25+ branch returns declared_status_1_period_ms() and not REV's 250. "
        "Returning REV's cold default here made expected_ms and rev_default_ms "
        "the same number, so status_1_verdict could never say 'reverted' on this "
        "generation and a rail-cycled fleet audited as healthy. Confirmed on "
        "rig-max: a rail cycle took all eight from 50 ms to 10 ms and "
        "the audit had no way to say so")
    assert ff["rev_default_ms"] == rev_default, (
        "rev_default_ms is where an unthrottled controller really sits, which is "
        "what makes 'reverted' distinguishable from 'unexpected'")
    assert ff["provisioned"] is False, (
        "`spark repair` writes parameter 159 through PARAMETER_WRITE, which "
        "24.0.1 does not carry -- offering a repair here would be a lie")

    deliberate = sa._deliberate_periods(sa.GEN_PRE25)
    assert deliberate.get(ff["api"]) == throttle
    assert sa._deliberate_periods(sa.GEN_FW25) == {}, (
        "25+ is left at REV's defaults; nothing is written at boot there")

    assert sa.status_1_verdict(throttle, expected=rev_default,
                               rev_default=rev_default,
                               also_ok=(throttle,)) == "ok"
    assert sa.status_1_verdict(rev_default, expected=throttle,
                               rev_default=rev_default,
                               also_ok=(throttle,)) == "reverted", (
        "a controller back at REV's cold default has LOST the throttle, and on "
        "this generation that means it lost every RAM value with it. Scoring it "
        "'ok' is what hid a rail cycle from the audit. It is recoverable rather "
        "than broken -- `spark throttle` re-sends the table -- but the audit has "
        "to say so, because leaving a MAX fleet cold puts roughly six times the "
        "frame rate on a full-speed gs_usb adapter")
    assert sa.status_1_verdict(throttle * 3 + 7, expected=throttle,
                               rev_default=rev_default,
                               also_ok=(throttle,)) != "ok", (
        "exempting the throttle must not exempt every slow cadence")


def test_a_whole_pre25_fleet_at_the_boot_throttle_raises_no_bus_level_finding():
    """The bus-level half: a whole fleet at the throttle is healthy, not the
    'thinned out together' signature of frame loss.
    """
    from sparklib.can_bus import _SPARKMAX_STATUS_PERIODS_MS
    api = sa.LEGACY_STATUS_0_API
    throttle = float(_SPARKMAX_STATUS_PERIODS_MS[0])
    roles = {d: "drive/X" for d in range(1, 9)}
    inv = {d: {"periods_ms": {api: throttle}, "serial": None} for d in roles}

    assert sa._bus_level_finding(inv, roles, generation=sa.GEN_PRE25) is None, (
        "a fleet sitting on the cadence this package wrote is not frame loss")

    slow = {d: {"periods_ms": {api: throttle * 6}, "serial": None} for d in roles}
    assert sa._bus_level_finding(slow, roles, generation=sa.GEN_PRE25), (
        "a fleet far slower than both the throttle and the default IS the "
        "thinning signature and must still be caught")
