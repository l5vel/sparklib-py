"""No advice printed for a pre-25 bus may name something pre-25 cannot do.

This is a guard against a CLASS of defect, not against the three instances of it
that were found by hand. All three had the same shape: a string
written while looking at a SPARK Flex, reached by a code path that also serves a
SPARK MAX, with nothing between them that knew the difference.

    BIT_REMEDIES["hasReset"]        told a MAX operator that firmware 26.1.6
                                    answers no parameter read (every parameter
                                    answers on 24.0.1) and to run
                                    `spark repair --id N --persist` (repair
                                    refuses on pre-25 before it sends anything)
    the reverted+rebooted branch    named `spark repair --id N` as the re-apply
    cmd_faults                      read BIT_REMEDIES directly, so it printed the
                                    25+ text whatever the bus was

Finding them by grep was luck. This asserts on BEHAVIOUR instead: it runs the
real advice-producing functions for a pre-25 bus and checks that nothing they
emit names a command or a firmware fact that does not apply there. A fourth
instance fails here on the day it is written.

The forbidden list is derived from what the frame spec says is unavailable, and
each entry cites why, so this file stays a statement about the hardware rather
than a list of banned words.
"""
from __future__ import annotations

import re

import pytest

from sparklib import admin as sa

# Each: (substring, why it cannot appear in pre-25 advice).
FORBIDDEN_ON_PRE25 = [
    ("spark repair",
     "cmd_repair refuses on this generation before it sends anything, because "
     "fault_frame(pre25)['provisioned'] is False -- the status periods are not "
     "parameters here and repair writes them through PARAMETER_WRITE"),
    ("--persist",
     "PERSIST_PARAMETERS is apiClass 63 index 15, versionImplemented 25.0.0. "
     "there is no pre-25 persist path in this CLI at all: `spark persist` now "
     "refuses on that generation before it sends anything, and `spark repair` "
     "refuses earlier still. The firmware DOES have a working burn at api 0x072 "
     "(pre25.burn_flash_api, measured), but this package neither "
     "sends nor exposes it, so recommending --persist to a MAX operator names a "
     "path that does not exist"),
    ("spark persist",
     "same frame, same reason"),
    ("learn-serials",
     "UNIQUE_ID_BROADCAST is apiClass 47, versionImplemented 25.0.0, so there "
     "is no serial to learn"),
    ("--serial",
     "identify and set-id address by serial, which pre-25 never broadcasts"),
    ("26.1.6",
     "a SPARK Flex firmware. Quoting it at a MAX operator states a fact about "
     "the wrong device; this fleet is 24.0.1"),
]

# Every bit either generation can emit, so the sweep is not limited to the ones
# a test author happened to think of.
ALL_BITS = sorted(set(sa._LEGACY_FAULT_BITS) | set(sa._FAULT_BITS.values())
                  if isinstance(sa._FAULT_BITS, dict)
                  else set(sa._LEGACY_FAULT_BITS) | set(sa._FAULT_BITS))


# Naming a command in order to warn against it is not the defect -- it is the
# cure, and the pre-25 hasReset remedy does exactly that ("Do NOT reach for
# `spark repair --persist`"). What is banned is RECOMMENDING one. The two are
# separated per sentence, because a sentence is the unit in which advice is
# given: a forbidden command is allowed only where its own sentence also carries
# an explicit negation.
#
# Scoped to the sentence rather than the whole message on purpose. Message-wide,
# a single "do not" anywhere would license every recommendation after it.
_NEGATIONS = ("do not", "don't", "never", "cannot", "can not", "refuses",
              "refused", "will not", "won't", "does not", "doesn't", "no use",
              "unavailable", "not implemented", "is not")

# A full stop between two digits is a version number, not a sentence end. Firmware
# strings are the single most common thing this advice quotes -- 24.0.1, 26.1.6,
# 25.0.0 -- and splitting on those cut "26.1.6" into three fragments, so the
# guard silently stopped seeing the one term it was written to catch.
_SENTENCE_SPLIT = re.compile(r"(?<!\d)\.(?!\d)|;|\n")


def _sentences(text):
    return [part for part in _SENTENCE_SPLIT.split(text or "") if part.strip()]


def offending(text):
    """Forbidden commands this text RECOMMENDS, with the reason each is wrong.

    A command inside a sentence that also negates it is a warning and is fine.
    """
    hits = []
    for sentence in _sentences(text):
        low = sentence.lower()
        if any(n in low for n in _NEGATIONS):
            continue
        for sub, why in FORBIDDEN_ON_PRE25:
            if sub.lower() in low:
                hits.append((sub, why))
    return hits


def assert_clean(text, what):
    hits = offending(text)
    assert not hits, (
        f"{what} names {len(hits)} thing(s) a pre-25 SPARK MAX cannot do:\n"
        + "\n".join(f"  {s!r}: {why}" for s, why in hits)
        + f"\n\nfull text:\n{text}")


@pytest.mark.parametrize("bit", ALL_BITS)
def test_no_single_bit_remedy_offers_a_pre25_impossibility(bit):
    assert_clean(sa.remedy_for([bit], sa.GEN_PRE25),
                 f"the pre-25 remedy for {bit!r}")


def test_no_bit_pair_remedy_offers_a_pre25_impossibility():
    """Pairs take a different branch through remedy_for, so sweep them too."""
    for pair in sa.PAIR_REMEDIES:
        assert_clean(sa.remedy_for(list(pair), sa.GEN_PRE25),
                     f"the pre-25 remedy for the pair {pair}")


def test_the_whole_pre25_fault_word_at_once_stays_clean():
    """Every bit set together, which is the worst case an operator can be shown."""
    assert_clean(sa.remedy_for(list(sa._LEGACY_FAULT_BITS), sa.GEN_PRE25),
                 "the pre-25 remedy for all sixteen bits")


def _pre25_reading(**s0):
    base = {"active_faults": 0, "sticky_faults": 0, "applied_output": 0.0}
    base.update(s0)
    return {"status0": base,
            "status1": {"voltage_v": 11.8, "current_a": 0.0, "motor_temp_c": 25},
            "generation": sa.GEN_PRE25}


def test_status_problems_advice_is_clean_for_every_pre25_fault_bit():
    """The path `spark audit` and `spark faults` both print through."""
    roles = {12: "steer/RB"}
    for i, name in enumerate(sa._LEGACY_FAULT_BITS):
        for field in ("active_faults", "sticky_faults"):
            found = sa.status_problems(
                {12: _pre25_reading(**{field: 1 << i})}, roles,
                generation=sa.GEN_PRE25)
            for p in found:
                assert_clean(p, f"status_problems for pre-25 {field} {name!r}")


def test_audit_advice_is_clean_on_a_rail_cycled_pre25_fleet():
    """The exact state rig-max was in: every controller back at
    REV's cold default with sticky hasReset. This is the reading that used to
    print `spark repair --id N` as the remedy."""
    ff = sa.fault_frame(sa.GEN_PRE25)
    roles = {d: f"drive/{d}" for d in range(1, 9)}
    inv = {d: {"serial": None, "firmware": "24.0.1",
               "periods_ms": {ff["api"]: ff["rev_default_ms"]}}
           for d in roles}
    status = {d: _pre25_reading(sticky_faults=sa._LEGACY_FAULT_BITS.index("hasReset")
                                and 1 << sa._LEGACY_FAULT_BITS.index("hasReset"))
              for d in roles}
    found = sa.audit_problems(inv, {}, roles, known_serials=None,
                              status=status, generation=sa.GEN_PRE25)
    assert found, (
        "a fleet at REV's cold default with sticky hasReset audited clean. That "
        "is the defect fault_frame's expected_ms was fixed to close: it means a "
        "rail cycle that dropped every RAM value is invisible to the audit")
    for p in found:
        assert_clean(p, "audit_problems on a rail-cycled pre-25 fleet")


def test_the_coverage_note_is_clean_on_pre25():
    assert_clean(sa.coverage_note(8, sa.GEN_PRE25), "the pre-25 coverage note")


def test_the_guard_would_catch_the_defect_it_was_written_for():
    """The guard has to fail on the old text, or it proves nothing.

    Without this, a forbidden list that no longer matches anything passes for the
    same reason an empty one does. This is the verbatim shape of what
    BIT_REMEDIES["hasReset"] printed at a MAX operator until.
    """
    old_flex_text = ("the controller rebooted and everything volatile went with "
                     "it. To re-apply: `uv run spark repair --id N --persist` "
                     "restores Status 1 Period. Nothing else can be CHECKED over "
                     "CAN, because firmware 26.1.6 answers no parameter read")
    hits = dict(offending(old_flex_text))
    assert "spark repair" in hits and "--persist" in hits and "26.1.6" in hits, (
        "the guard no longer detects the text it was written to catch, so it is "
        f"not guarding anything: {hits}")


def test_the_guard_separates_a_warning_from_a_recommendation():
    """Both halves, because a guard that flagged warnings would push the fix
    towards silence -- and telling a MAX operator NOT to run `spark repair` is
    more useful than never mentioning it."""
    recommends = "To re-apply, run `uv run spark repair --id 3 --persist` now"
    warns = ("Do NOT reach for `uv run spark repair --persist`; it refuses on "
             "this generation")
    assert offending(recommends), "a plain recommendation was not caught"
    assert not offending(warns), (
        "an explicit warning against the command was treated as a "
        "recommendation, which would make the only correct advice unwritable")


def test_a_negation_does_not_license_the_rest_of_the_message():
    """Sentence-scoped, not message-scoped. One `do not` early on must not
    whitelist a real recommendation later."""
    mixed = ("Do NOT power cycle the controller. To re-apply, run `uv run spark "
             "repair --id 3 --persist`")
    assert offending(mixed), (
        "a recommendation later in the message was excused by an unrelated "
        "negation earlier in it")


def test_the_25plus_advice_is_deliberately_not_constrained():
    """The forbidden list is about pre-25 only. On 25+ every one of those
    commands is correct, and a guard that banned them everywhere would be
    removing working advice."""
    text = sa.remedy_for(["hasReset"], sa.GEN_FW25)
    assert text, "the 25+ remedy for hasReset disappeared"
    assert offending(text), (
        "the 25+ text no longer names any generation-gated command, which means "
        "this test is no longer checking that the constraint is pre-25-only")


def test_the_duplicate_command_advice_is_clean_on_pre25():
    """cmd_duplicates builds operator advice too, and the guard missed it.

    Added after pre-25 duplicate detection was implemented and the
    remedy under it still named `spark set-id --serial` and `spark
    learn-serials`, neither of which works on that generation. Same class as the
    hasReset remedy, found the same way: by reading the output rather than by the
    guard catching it.
    """
    import ast
    import pathlib as _p

    src = (_p.Path(__file__).resolve().parents[2] / "sparklib"
           / "cli.py").read_text()
    fn = next(f for f in ast.walk(ast.parse(src))
              if isinstance(f, ast.FunctionDef) and f.name == "cmd_duplicates")
    body = ast.unparse(fn)

    assert "pre25" in body, "cmd_duplicates does not branch on generation at all"
    # The 25+ remedy must still exist, and must sit on the non-pre25 side.
    assert "set-id --serial" in body, "the 25+ remedy was removed"
    for branch in ast.walk(fn):
        if not isinstance(branch, ast.If):
            continue
        test = ast.unparse(branch.test)
        if test.strip() != "pre25":
            continue
        pre = ast.unparse(branch.body)
        hits = offending(pre)
        assert not hits, (
            "the pre-25 branch of cmd_duplicates recommends "
            + ", ".join(repr(h) for h, _ in hits))


# -- the fleet assumption -----------------------------------------------------

def test_the_fleet_assumption_is_stated_and_checkable():
    """MAX means pre-25 and Flex means firmware 25+, WITHIN THIS TREE.

    rig-max is eight MAX on 24.0.1 and rig-flex eight Flex on 26.1.6, and the
    simplifying assumption is fine as long as it is written down and checked.
    What it must never become is a frame decision: a MAX updated to 25.0.0 is
    written exactly like a Flex, and applying the wrong layout is the defect
    that wedged a base once already.
    """
    assert sa.EXPECTED_GENERATION == {"sparkmax": sa.GEN_PRE25,
                                      "sparkflex": sa.GEN_FW25}
    assert sa.unexpected_generation("sparkmax", sa.GEN_PRE25) is None
    assert sa.unexpected_generation("sparkflex", sa.GEN_FW25) is None

    broken = sa.unexpected_generation("sparkmax", sa.GEN_FW25)
    assert broken and "EXPECTED_GENERATION" in broken, (
        "a violation must point at the one place the assumption is written down")
    assert sa.unexpected_generation("sparkflex", sa.GEN_PRE25)


def test_an_unknown_or_missing_product_is_not_a_violation():
    """Absence of a declared product is not evidence against the assumption."""
    assert sa.unexpected_generation(None, sa.GEN_PRE25) is None
    assert sa.unexpected_generation("", sa.GEN_FW25) is None
    assert sa.unexpected_generation("something-else", sa.GEN_PRE25) is None
    assert sa.unexpected_generation("sparkmax", None) is None


def test_the_assumption_is_never_used_as_a_frame_decision():
    """normalise_generation must keep REFUSING a product name.

    EXPECTED_GENERATION makes it possible to map one to the other, which is
    exactly the shortcut that must not creep into the decode path.
    """
    for product in ("sparkmax", "sparkflex", "SPARK MAX", "spark_max"):
        with pytest.raises(ValueError):
            sa.normalise_generation(product)


def test_status_and_audit_both_report_a_violation():
    """The two commands an operator runs when something is wrong."""
    import ast
    import pathlib

    src = (pathlib.Path(__file__).resolve().parents[2] / "sparklib"
           / "cli.py").read_text()
    tree = ast.parse(src)
    for name in ("cmd_status", "cmd_audit"):
        fn = next(f for f in ast.walk(tree)
                  if isinstance(f, ast.FunctionDef) and f.name == name)
        assert "unexpected_generation" in ast.unparse(fn), (
            f"{name} has both the declared product and the observed generation "
            "in hand and does not check them against each other")
