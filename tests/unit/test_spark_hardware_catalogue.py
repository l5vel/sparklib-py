"""The hardware injection suite's manifest, checked against the suite and
against the catalogue it claims to cover.

`tests/hardware` says it injects every failure mode the Chief Delphi corpus
produced. That is a claim about coverage, and a coverage claim nobody checks is
the thing this whole line of work exists to catch: a suite that quietly covered
the easy two thirds would read, from its summary line, exactly like one that
covered all of it.

So the manifest in `sparkhw.catalogue` is the deliverable and this module is its
audit. It needs no robot -- nothing here opens a socket or imports python-can,
which is why `sparkhw` resolves everything below `catalogue` lazily -- and it
runs on every `pytest`, so the manifest cannot drift away from the suite on a
laptop and be discovered on a robot.
"""
from __future__ import annotations

import ast
import os
import pathlib
import re

import pytest

from sparkhw import MODES, catalogue

HARDWARE_DIR = pathlib.Path(__file__).resolve().parents[1] / "hardware"

# The catalogue document the manifest is checked against.
CATALOGUE_MD = pathlib.Path(os.environ.get(
    "SPARK_FAILURE_CATALOGUE",
    HARDWARE_DIR.parents[1] / "docs" / "FAILURE-CATALOGUE.md"))

VALID = {catalogue.WIRE, catalogue.CONFIG, catalogue.STAGED, catalogue.DETECT,
         catalogue.REFUSED}

# Entries the manifest adds because part 2 of the catalogue describes them as bus
# signatures without numbering them. They cannot come from a heading scan.
UNNUMBERED = {"SIG-POSITION-SPIKE", "SIG-THERMAL"}


def _adversarial_tests():
    """{test function name: [module stem, ...]} across tests/adversarial."""
    found = {}
    adv = HARDWARE_DIR.parent / "adversarial"
    for path in sorted(adv.glob("test_*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
                found.setdefault(node.name, []).append(path.stem)
    return found


def _hardware_tests():
    """{test function name: [module stem, ...]} across tests/hardware."""
    found = {}
    for path in sorted(HARDWARE_DIR.glob("test_*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
                found.setdefault(node.name, []).append(path.stem)
    return found


def test_every_catalogue_failure_mode_has_a_hardware_disposition():
    """Every entry says what this suite does about it, in one of five words. A
    mode with no disposition is one somebody stopped thinking about halfway.
    """
    ids = [m.id for m in MODES]
    doubled = sorted({i for i in ids if ids.count(i) > 1})
    assert not doubled, f"duplicate ids in the manifest: {doubled}"
    for mode in MODES:
        assert mode.hw, f"{mode.id} has no hardware disposition"
        unknown = set(mode.hw) - VALID
        assert not unknown, f"{mode.id} carries unknown disposition(s) {unknown}"
        assert mode.title.strip(), f"{mode.id} has no title"


def test_every_mode_is_either_tested_or_refused_with_a_reason():
    """The two ways a mode is allowed to leave this suite: a test that injects
    it, or a written reason it is not injected on a robot. Silence is neither,
    and silence is what makes a coverage claim worthless.
    """
    for mode in MODES:
        if catalogue.REFUSED in mode.hw or catalogue.DETECT in mode.hw:
            assert mode.why.strip(), (
                f"{mode.id} is {'/'.join(mode.hw)} and says nothing about what "
                "would break or why it cannot be injected")
        if catalogue.REFUSED not in mode.hw:
            assert mode.tests, (
                f"{mode.id} claims disposition {'/'.join(mode.hw)} and names no "
                "test")


def test_every_test_the_manifest_names_exists():
    """The manifest points at test function names. A rename that does not come
    back here turns a covered mode into an uncovered one with no visible change.
    """
    defined = _hardware_tests()
    missing = sorted(name for name in catalogue.named_tests() if name not in defined)
    assert not missing, (
        f"the manifest names tests that do not exist in {HARDWARE_DIR.name}/: "
        f"{missing}")


def test_every_named_test_is_defined_in_exactly_one_module():
    """A function name defined twice makes the manifest ambiguous, and makes it
    possible to fix the copy the run does not reach -- which is the failure this
    repo's own testing rules single out.
    """
    defined = _hardware_tests()
    doubled = {name: mods for name, mods in defined.items() if len(mods) > 1}
    assert not doubled, f"test names defined in more than one module: {doubled}"


def test_the_manifest_covers_every_entry_in_the_catalogue_document():
    """The point of the whole exercise: 353 threads distilled to a numbered list,
    and a hardware suite that says it addresses all of it. The document is the
    authority, so the manifest is read against its headings rather than against
    somebody's memory of them.
    """
    if not CATALOGUE_MD.exists():
        pytest.skip(f"the catalogue is not on this machine at {CATALOGUE_MD}; set "
                    "SPARK_FAILURE_CATALOGUE to point at it")
    text = CATALOGUE_MD.read_text()
    numbered = set(re.findall(r"^### ([A-H]\d)\.", text, re.MULTILINE))
    numbered |= set(re.findall(r"^- \*\*([A-H]\d)\.", text, re.MULTILINE))
    assert len(numbered) > 30, (
        f"only {len(numbered)} entries were found in {CATALOGUE_MD.name}; the "
        "heading format changed and this check is no longer reading it")

    manifest = {m.id for m in MODES}
    uncovered = sorted(numbered - manifest)
    assert not uncovered, (
        f"the catalogue names failure modes the hardware manifest does not: "
        f"{uncovered}")
    invented = sorted(manifest - numbered - UNNUMBERED)
    assert not invented, (
        f"the manifest carries entries the catalogue does not name: {invented}")


# -- the simulator half of the manifest ----------------------------------------
# `tests` names what tests/hardware injects. A mode refused on hardware can still
# be simulated, and until `sim_tests` existed the manifest had nowhere to say so:
# eleven modes read as covered nowhere while their simulated half was sitting in
# tests/adversarial with no way to point at it.


def test_every_simulated_test_the_manifest_names_exists():
    """Same contract as the hardware half. A rename that does not come back here
    turns a covered mode into an uncovered one with no visible change."""
    known = _adversarial_tests()
    missing = [(m.id, name) for m in MODES for name in m.sim_tests
               if name not in known]

    assert missing == [], missing


def test_a_mode_refused_on_hardware_names_the_test_that_simulates_it():
    """The gap this field closes. A mode refused on a robot and injected by no
    hardware test is covered nowhere unless the simulator carries it, and a
    manifest that cannot express that reads as complete while it is not.
    """
    orphans = [m.id for m in MODES
               if catalogue.REFUSED in m.hw and not m.tests and not m.sim_tests]

    assert orphans == [], (
        f"{orphans} are refused on hardware, injected by no hardware test, and "
        "name no simulator test either")


def test_a_mode_that_cannot_be_simulated_does_not_claim_a_simulator_test():
    """The control. `sim` is the catalogue's own column; a NO there beside a
    named simulator test means one of the two is stale."""
    contradictions = [(m.id, m.sim, m.sim_tests) for m in MODES
                      if m.sim == "NO" and m.sim_tests
                      and not m.why.strip()]

    assert contradictions == [], contradictions


# -- the citation chain --------------------------------------------------------

def _cited(root, pattern=re.compile(r"chiefdelphi\.com/t/(\d{6})|CD (\d{6})")):
    out = set()
    for path in root.rglob("*.py"):
        for a, b in pattern.findall(path.read_text()):
            out.add(int(a or b))
    return out


def test_every_thread_a_test_cites_was_actually_fetched():
    """The suite's evidence rule is that a claim rests on a source somebody can
    open. A docstring citing a thread the corpus does not hold is a claim from
    memory, which is the failure mode this whole line of work exists to catch.

    One citation was exactly that -- a six-digit thread id given for a
    gate-driver fault, absent from the 353-thread corpus and absent from the
    catalogue's own D1 citations. It was replaced with the D1 thread that is in
    the corpus and is titled "Gate driver fault". This docstring deliberately
    names no id, because the scan below would read it as a citation.
    """
    # The thread corpus is a 25 MB scrape and is not vendored. Point
    # SPARK_CD_CORPUS at it to run this check; the graded findings drawn from it
    # are in docs/FIELD-REPORTS.md, each with its thread URL.
    threads = pathlib.Path(os.environ.get(
        "SPARK_CD_CORPUS",
        HARDWARE_DIR.parents[2] / "canbus-baseline" / "chiefdelphi-corpus" / "threads"))
    if not threads.is_dir():
        pytest.skip(f"thread corpus not present; set SPARK_CD_CORPUS: {threads}")
    have = {int(p.stem) for p in threads.glob("*.txt") if p.stem.isdigit()}
    cited = _cited(HARDWARE_DIR.parent)

    assert cited - have == set(), (
        f"cited by a test and not in the corpus: {sorted(cited - have)}")


def test_every_thread_the_catalogue_cites_reaches_a_test():
    """The other direction. A thread the catalogue judged worth citing, with no
    test naming it, is a failure mode that was read and then dropped."""
    doc = CATALOGUE_MD
    if not doc.is_file():
        pytest.skip(f"the catalogue document is not on this machine: {doc}")
    in_doc = {int(a or b) for a, b in
              re.findall(r"chiefdelphi\.com/t/(\d{6})|CD (\d{6})", doc.read_text())}
    cited = _cited(HARDWARE_DIR.parent)

    assert in_doc - cited == set(), (
        f"the catalogue cites these and no test does: {sorted(in_doc - cited)}")
