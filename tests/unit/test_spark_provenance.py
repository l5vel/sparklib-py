"""The provenance registry has to stay honest, or it is worse than nothing.

A register of what has been checked is only useful while it tracks the code. If
a claim can be deleted while its consumer stays, or a consumer can name a
function that no longer exists, the register starts reassuring people about
things nobody verified -- which is the failure it exists to prevent.
"""

from __future__ import annotations

import pathlib

from sparklib import provenance as pv


def test_every_claim_says_how_it_was_established():
    for c in pv.CLAIMS:
        assert c.how in (pv.HARDWARE, pv.VENDOR, pv.INFERRED, pv.UNVERIFIED), c.key
        assert c.what.strip(), f"{c.key} says nothing"
        assert len(c.evidence) > 40, (
            f"{c.key} cites no real evidence; 'it works' is not provenance")


def test_an_unsettled_claim_carries_the_command_that_settles_it():
    """The register's whole value is telling someone what to go and do."""
    for c in pv.CLAIMS:
        if c.settled:
            continue
        assert c.settle_with.strip(), (
            f"{c.key} is {c.how} and gives no way to settle it, so it is a "
            "worry with no action attached")


def test_no_claim_points_at_code_that_has_gone():
    """A used_by naming a function that no longer exists means the register is
    describing a version of the package that is not this one."""
    root = pathlib.Path(pv.__file__).resolve().parents[1]
    src = " ".join(p.read_text(errors="ignore")
                   for p in root.rglob("*.py") if ".venv" not in p.parts)
    missing = []
    for c in pv.CLAIMS:
        for ref in c.used_by:
            sym = ref.split(".")[-1].split()[0]
            if sym.endswith(".md") or " " in ref and "." not in sym:
                continue
            if sym not in src:
                missing.append(f"{c.key} -> {ref}")
    assert not missing, "provenance points at code that is gone: " + ", ".join(missing)


def test_the_register_and_the_simulator_agree_on_the_generation_split():
    """The specific trap this register was built for.

    spark_controller decoded Flex frames with the MAX layout, a 13.7 V rail read
    as a fault word, and gating recovery on it wedged the base. The register has
    to keep describing the simulator this package actually has.

    A claim that the simulator models both generations is only true while the
    simulator derives its api class from a firmware version. Keyed on product it
    manufactures the same wrong bus the driver expects, and the suite goes green
    against a bus that firmware 25+ never produces.
    """
    sim = pathlib.Path(pv.__file__).resolve().parents[1] / "tests" / "support" / "sparksim"
    body = " ".join(p.read_text(errors="ignore") for p in sim.rglob("*.py"))
    models_generations = ("generation_for_firmware" in body
                          and "LEGACY_BEACON_PAYLOAD" in body)

    claim = pv.BY_KEY["sim.models_both_generations"]
    assert claim.settled == models_generations, (
        "the register and the simulator disagree about whether firmware "
        f"generations are modelled (simulator={models_generations}, claim "
        f"settled={claim.settled}). Update the claim rather than leaving the "
        "register describing a version of the package that is not this one.")

    # Modelling a product from documents is not the same as having seen one.
    # rig-max has now been read, so the claim is settled -- but only `hardware`
    # settles it, and only with a run behind it. `vendor` here would be the
    # original error wearing a different label: REV's documents are what the
    # measurement contradicted.
    probed = pv.BY_KEY["max.hardware.unprobed"]
    assert probed.how == pv.HARDWARE, (
        f"max.hardware.unprobed is graded {probed.how!r}. Reading a MAX bus is "
        "the only thing that settles it; a simulator built from REV's docs is "
        "not hardware verification and neither is REV's own documentation.")
    assert "rig-max" in probed.evidence and "24.0.1" in probed.evidence, (
        "the evidence must name the robot and the firmware that were read, or "
        "the claim cannot be checked against the fleet it describes")
    pinned = (pathlib.Path(pv.__file__).resolve().parents[1] / "tests"
              / "hardware" / "test_legacy_period_write.py")
    assert pinned.exists(), (
        f"{pinned.name} is what re-measures this on demand; without it the "
        "claim is a note about one afternoon rather than a checkable fact")


def test_spark_verify_exits_nonzero_while_anything_is_unchecked():
    """A verification command that always exits 0 verifies nothing."""
    from types import SimpleNamespace

    from sparklib import cli as spark_cli
    rc = spark_cli.cmd_verify(SimpleNamespace(product="sparkflex"))
    gaps = pv.unsettled("sparkflex")
    assert (rc == 1) == bool(gaps), (
        f"{len(gaps)} unchecked claim(s) but cmd_verify returned {rc}")


# -- the ledger has to be able to catch the defect it exists to catch ---------
#
# Until three claims were tagged [vendor] at once:
#   both.frames_split_on_firmware  faults are in STATUS_1 on 25+
#   max.status0.layout             faults are in 0x060 on a MAX
#   sim.models_flex_only           a MAX answers on 0x060/0x061, never the Flex apis
# The first refutes the other two, and every one of them was marked settled. The
# safety net was vouching for the thing it was built to flag.


def test_no_settled_claim_keys_a_frame_on_a_product():
    """A frame claim scoped to one product contradicts frames_split_on_firmware.

    REV's spec describes one device and splits only on versionImplemented, so a
    claim that says which api a frame lives on is a claim about firmware. One
    scoped to SPARKMAX or SPARKFLEX and marked settled is the old defect back.
    """
    from sparklib import provenance as pv

    frame_words = ("api 0x", "0x060", "0x061", "0x2E0", "0x2E1", "0x2F0",
                   "STATUS_0", "STATUS_1", "Periodic Status")
    offenders = []
    for c in pv.CLAIMS:
        if c.product not in (pv.SPARKMAX, pv.SPARKFLEX):
            continue
        # A HARDWARE claim is a measurement, and a measurement is scoped to the
        # thing measured: reading a Flex says nothing about a MAX, and saying so
        # is honest rather than a product split. What this guard is for is a
        # VENDOR claim keying a frame on product, because the spec does not.
        if c.how != pv.VENDOR:
            continue
        if any(w in c.what for w in frame_words):
            offenders.append(c.key)

    # REV's spec names the product itself on UNIQUE_ID_BROADCAST, so this one
    # reports what the source says rather than inventing a split.
    allowed = {"max.unique_id.api"}
    unexpected = sorted(set(offenders) - allowed)
    assert not unexpected, (
        "these VENDOR claims key a frame on a product, which frames 2.1.0 "
        f"does not do: {unexpected}. Scope them to a firmware generation, or "
        "say in the evidence why the source itself names the product")


def test_the_ledger_agrees_with_the_driver_about_where_faults_live():
    """The ledger and the code must not drift apart.

    A claim can be corrected while the code it describes stays wrong, which is
    how `max.status0.layout` outlived the decode it justified. Read the driver.
    """
    from sparklib import admin as sa
    from sparklib import provenance as pv

    claim = pv.BY_KEY["both.frames_split_on_firmware"]
    assert claim.settled, "the firmware axis is the driver's load-bearing claim"

    assert sa.fault_frame(sa.GEN_FW25)["api"] == sa.STATUS_1_API, (
        "the ledger says faults are in STATUS_1 on 25+ and the driver disagrees")
    assert sa.fault_frame(sa.GEN_PRE25)["api"] == sa.LEGACY_STATUS_0_API
    assert set(sa.API_SETS) == {sa.GEN_PRE25, sa.GEN_FW25}, (
        "API_SETS is keyed on something other than firmware generation")


def test_every_used_by_target_still_exists():
    """`used_by` names the code a claim justifies. A stale name means the claim
    is describing something that no longer exists, which is how a corrected
    ledger can still point at the wrong function."""
    import importlib
    from sparklib import provenance as pv

    missing = []
    for c in pv.CLAIMS:
        for target in c.used_by:
            mod, _, attr = target.rpartition(".")
            # used_by also carries prose pointers (a doc section, a test path).
            # Only dotted module.name pairs are checkable here.
            if not mod.isidentifier() or not attr.isidentifier():
                continue
            try:
                m = importlib.import_module(f"sparklib.{mod}")
            except ModuleNotFoundError:
                continue          # not a driver module; nothing to check
            found = hasattr(m, attr) or any(
                hasattr(v, attr) for v in vars(m).values() if isinstance(v, type))
            if not found:
                missing.append(f"{c.key} -> {target}")
    assert not missing, (
        "claims naming driver code that does not exist:\n  "
        + "\n  ".join(missing))


def test_every_capture_a_claim_cites_is_in_the_repo():
    """`records/` is gitignored, so a cited capture has to be un-ignored by name.
    A claim pointing at a file a clone does not have cannot be checked."""
    import re
    import subprocess

    repo = pathlib.Path(__file__).resolve().parents[2]
    cited = set()
    for c in pv.CLAIMS:
        for field in (c.evidence or "", c.settle_with or "", c.what or ""):
            cited.update(re.findall(r"records/[A-Za-z0-9._-]+\.(?:log|json)", field))
    assert cited, "no claim cites a capture; this check would be vacuous"
    for rel in sorted(cited):
        assert (repo / rel).exists(), f"{rel} is cited but not on disk"
        out = subprocess.run(["git", "check-ignore", "-q", rel],
                             cwd=repo, capture_output=True)
        assert out.returncode != 0, (
            f"{rel} is cited by a claim but gitignored, so a clone cannot open "
            "it. Un-ignore it by name in.gitignore")
