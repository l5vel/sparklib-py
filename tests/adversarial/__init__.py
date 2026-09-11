"""Adversarial SPARK suite: field failures the driver currently calls healthy.

The simulator these tests run against lives in tests/support/sparksim and is
imported as `sparksim` (tests/conftest.py puts tests/support on sys.path).
`simulator.py` here is the entry point for this package; it re-exports sparksim
rather than restating it, because two implementations of one bus would mean the
tests could pass against the copy the suite does not use.
"""
