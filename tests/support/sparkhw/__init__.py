"""Adversarial failure injection against a live SPARK bus.

`sparksim` answers "does the driver see this failure?" with a modelled bus.
This package asks the same question of the wire in front of the robot: real
controllers, real arbitration, real 1 Mbit timing, a real socketcan adapter, and
the same `SparkAdmin` the operator runs.

Three injection tiers, in rising order of what they touch:

  wire    the host transmits the failure's own frames onto the live bus. No
          controller is written to and nothing has to be undone -- stopping the
          stream ends the failure. `wire.WireInjector`.
  config  one reversible parameter write puts a real controller into the state.
          Only Status 0/1 Period, only on the nominated controller, never
          persisted, so a motor-rail power cycle undoes it even if the process
          dies mid-test. `guards.StatusPeriodGuard`.
  staged  a person has to cut power, unplug a connector or read a meter. Two
          runs with the physical act between them, as in
          tests/hardware/test_spark_persistence.py.

`catalogue.MODES` carries every failure mode in SPARK-FAILURE-CATALOGUE.md with
the tier it is injected at, or the reason it is not injected here. That module
imports nothing, so tests/unit can check the manifest against the hardware suite
without pulling python-can into a tier that is meant to have no CAN in it --
which is why everything below `catalogue` is resolved lazily.

Protocol codecs come from `sparksim.frames`, a pure restatement of REV-Specs
spark-frames-2.1.0 with no clock and no I/O. A second copy of the fault-bit
tables here would be a second thing to get wrong, and
tests/adversarial/test_simulator.py already checks that copy against the
driver's constants.
"""
from __future__ import annotations

import importlib

from .catalogue import MODES, Mode, by_id, dispositions

_LAZY = {
    "GATES": "guards", "LIVE_EXPERIMENTS": "guards", "StatusPeriodGuard": "guards",
    "at_rest_reasons": "guards", "base_index": "guards", "link_info": "guards",
    "require_gate": "guards", "require_link": "guards", "require_stage": "guards",
    "link_is_virtual": "guards",
    "stage": "guards", "STARVED_PERIOD_MS": "guards",
    "FILLER_HIGH_PRIORITY": "wire", "FILLER_LOW_PRIORITY": "wire",
    "FORBIDDEN_BASES": "wire", "Sniffer": "wire", "WireInjector": "wire",
    "forbidden_frames": "wire",
}

__all__ = ["FILLER_HIGH_PRIORITY", "FILLER_LOW_PRIORITY", "FORBIDDEN_BASES",
           "GATES", "LIVE_EXPERIMENTS", "MODES", "Mode", "STARVED_PERIOD_MS",
           "Sniffer",
           "StatusPeriodGuard", "WireInjector", "at_rest_reasons", "base_index",
           "by_id", "dispositions", "forbidden_frames", "link_info",
           "link_is_virtual",
           "require_gate", "require_link", "require_stage", "stage"]


def __getattr__(name):
    if name in _LAZY:
        return getattr(importlib.import_module(f".{_LAZY[name]}", __name__), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
