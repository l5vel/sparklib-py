#!/usr/bin/env python3
"""One entry point for the adversarial SPARK suite.

    uv run python tests/adversarial/run_suite.py
    uv run python tests/adversarial/run_suite.py --module faults -v
    uv run python tests/adversarial/run_suite.py --list

Runs every module in tests/adversarial, prints a per-module table and then the
driver defects the xfails stand for, and exits non-zero only on a real failure,
an error or a collection error. An xfail is the expected state of this suite: it
is a named driver gap with a reproduction attached, not a broken test. An XPASS
is reported loudly, because it means a gap closed and its marker should come off.

Everything here is a reader of pytest's own reports -- the suite itself is plain
pytest and `pytest tests/adversarial` gives the same results.
"""
from __future__ import annotations

import argparse
import os
import pathlib
import re
import subprocess
import sys
import textwrap

SUITE = pathlib.Path(__file__).resolve().parent
REPO = SUITE.parents[1]

# The confirmed defects this suite was built to pin. The tag is matched against
# each xfail reason, so a new xfail that names one is folded in automatically.
DEFECTS = {
    # D1 closed: audit_problems takes status=, cmd_audit calls
    # collect_status, and `spark audit` exits 1 naming the fault. Kept out of the
    # table so a stale mention in a reason string cannot regroup a live gap under it.
    "D2": "persist() drains and sends immediately, so a flash burn can commit the "
          "value the write replaced, report Success and survive the power cycle.",
    "D3": "write_param() has no retry, no inter-frame pacing, and never compares the "
          "echoed d[2:6] against the value it asked for.",
    "D4": "status_1_verdict() sees only a mean period, so a dropout, a stall, a "
          "saturated bus and a real config revert all read as 'reverted'.",
    "D5": "clear_faults() is fire-and-forget: a transient sticky bit and a "
          "hardware-latched gate driver needing an RMA produce identical output.",
}


def _reexec_with_pytest(argv):
    """pytest is not a runtime dependency of this package; borrow it via uv."""
    if os.environ.get("_ADVERSARIAL_SUITE_REEXEC"):
        sys.exit("pytest is not importable and re-exec already tried; run with "
                 "`uv run --with pytest python tests/adversarial/run_suite.py`")
    env = dict(os.environ, _ADVERSARIAL_SUITE_REEXEC="1")
    cmd = ["uv", "run", "--with", "pytest", "python", str(SUITE / "run_suite.py")]
    raise SystemExit(subprocess.call(cmd + argv, cwd=str(REPO), env=env))


def modules():
    return sorted(p for p in SUITE.glob("test_*.py"))


def resolve(names):
    """--module faults | test_faults | test_faults.py all mean the same file."""
    available = {p.stem: p for p in modules()}
    chosen = []
    for name in names:
        stem = pathlib.Path(name).stem
        for candidate in (stem, f"test_{stem}"):
            if candidate in available:
                chosen.append(available[candidate])
                break
        else:
            sys.exit(f"no such module: {name}\navailable: "
                     + ", ".join(sorted(s[5:] for s in available)))
    return chosen


class Recorder:
    """Turns pytest's report stream into per-module counts and xfail reasons."""

    def __init__(self):
        self.outcome = {}       # nodeid -> passed|xfailed|xpassed|failed|error|skipped
        self.reason = {}        # nodeid -> xfail reason
        self.duration = {}      # module stem -> seconds
        self.collect_errors = []

    @staticmethod
    def _module_of(nodeid):
        return pathlib.Path(nodeid.split("::")[0]).stem

    def pytest_runtest_logreport(self, report):
        self.duration[self._module_of(report.nodeid)] = \
            self.duration.get(self._module_of(report.nodeid), 0.0) + report.duration
        wasxfail = getattr(report, "wasxfail", None)
        if wasxfail is not None:
            self.reason[report.nodeid] = re.sub(r"^reason: ", "", wasxfail).strip()
        if report.when == "call":
            if report.outcome == "passed":
                self.outcome[report.nodeid] = "xpassed" if wasxfail else "passed"
            elif report.outcome == "failed":
                self.outcome[report.nodeid] = "failed"
            elif report.outcome == "skipped":
                self.outcome[report.nodeid] = "xfailed" if wasxfail else "skipped"
        elif report.outcome == "failed":
            self.outcome[report.nodeid] = "error"
        elif report.when == "setup" and report.outcome == "skipped":
            self.outcome.setdefault(report.nodeid,
                                    "xfailed" if wasxfail else "skipped")

    def pytest_collectreport(self, report):
        if report.failed:
            self.collect_errors.append(report.nodeid)

    def counts(self):
        per = {}
        for nodeid, outcome in self.outcome.items():
            per.setdefault(self._module_of(nodeid), {})[outcome] = \
                per.setdefault(self._module_of(nodeid), {}).get(outcome, 0) + 1
        return per


COLUMNS = ("passed", "xfailed", "failed", "errors", "skipped")
_KEY = {"errors": "error"}


def print_table(rec, chosen):
    per = rec.counts()
    width = max([len(p.stem) for p in chosen] + [len("module")])
    head = f"  {'module':<{width}}  " + "".join(f"{c:>8}" for c in COLUMNS) + "     time"
    print("\n" + head)
    print("  " + "-" * (len(head) - 2))
    totals = dict.fromkeys(COLUMNS, 0)
    for path in chosen:
        row = per.get(path.stem, {})
        cells = [row.get(_KEY.get(c, c), 0) for c in COLUMNS]
        for c, n in zip(COLUMNS, cells):
            totals[c] += n
        print(f"  {path.stem:<{width}}  " + "".join(f"{n:>8}" for n in cells)
              + f"  {rec.duration.get(path.stem, 0.0):>6.2f}s")
    print("  " + "-" * (len(head) - 2))
    print(f"  {'TOTAL':<{width}}  " + "".join(f"{totals[c]:>8}" for c in COLUMNS)
          + f"  {sum(rec.duration.values()):>6.2f}s")
    xpassed = sum(1 for o in rec.outcome.values() if o == "xpassed")
    if xpassed:
        print(f"\n  {xpassed} XPASS: a gap named below has been closed. Re-read the "
              "reason, then delete the marker.")
        for nodeid, outcome in sorted(rec.outcome.items()):
            if outcome == "xpassed":
                print(f"    {nodeid}")
    return totals


def print_defects(rec):
    """The xfails, grouped by the driver gap each one names."""
    xfails = {n: r for n, r in rec.reason.items()
              if rec.outcome.get(n) in ("xfailed", "xpassed")}
    if not xfails:
        return
    print("\n  Driver defects these xfails stand for")
    print("  " + "=" * 74)
    claimed = set()
    for tag, summary in DEFECTS.items():
        owned = sorted(n for n, r in xfails.items()
                       if re.search(rf"\b{tag}\b", r))
        claimed.update(owned)
        print(f"\n  {tag}  ({len(owned)} test{'' if len(owned) == 1 else 's'})")
        for line in textwrap.wrap(summary, 72):
            print(f"      {line}")
        for nodeid in owned:
            print(f"      - {nodeid.split('::', 1)[-1]}")

    rest = {}
    for nodeid, reason in xfails.items():
        if nodeid not in claimed:
            rest.setdefault(reason, []).append(nodeid)
    if rest:
        print("\n  Further capabilities the suite asks for and the driver does not "
              "have")
        print("  " + "=" * 74)
        for reason, nodes in sorted(rest.items(), key=lambda kv: -len(kv[1])):
            print()
            for line in textwrap.wrap(reason, 72):
                print(f"      {line}")
            for nodeid in sorted(nodes):
                print(f"      - {nodeid.split('::', 1)[-1]}")


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(
        prog="run_suite.py", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--module", action="append", metavar="NAME",
                        help="run one module (faults, bus_health,...); repeatable")
    parser.add_argument("--list", action="store_true", help="list the modules")
    parser.add_argument("-v", "--verbose", action="count", default=0,
                        help="passed through to pytest; repeat for more")
    args, passthrough = parser.parse_known_args(argv)

    if args.list:
        for path in modules():
            print(path.stem[5:])
        return 0

    chosen = resolve(args.module) if args.module else modules()

    try:
        import pytest
    except ModuleNotFoundError:
        _reexec_with_pytest(argv)
        return 1                                        # unreachable

    rec = Recorder()
    pytest_argv = [str(p) for p in chosen] + ["-p", "no:cacheprovider"]
    # -ra from pyproject would print every xfail reason twice: once here and once
    # in the defect section below, which is the same list with the grouping added.
    pytest_argv += ["-v"] * args.verbose + ["-ra"] if args.verbose else ["-q", "-rfE"]
    pytest_argv += passthrough

    print(f"  adversarial SPARK suite -- {len(chosen)} module(s) under "
          f"{SUITE.relative_to(REPO)}")
    rc = pytest.main(pytest_argv, plugins=[rec])

    totals = print_table(rec, chosen)
    print_defects(rec)

    broken = totals["failed"] + totals["errors"] + len(rec.collect_errors)
    if rec.collect_errors:
        print("\n  COLLECTION ERRORS: " + ", ".join(rec.collect_errors))
    print()
    if broken:
        print(f"  FAIL -- {broken} real failure(s)/error(s). pytest exit code {rc}.")
        return 1
    print(f"  OK -- {totals['passed']} passed, {totals['xfailed']} xfailed "
          "(expected: each names a driver gap above), 0 failed.")
    return 0


if __name__ == "__main__":
    os.chdir(REPO)
    sys.exit(main())
