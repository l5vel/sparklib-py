"""The SPARK bus simulator and its fault injection, for this package.

One implementation, in tests/support/sparksim, so a test written against
`sparksim` and a test written against `tests.adversarial.simulator` exercise the
same scheduler, the same period resolution and the same persist settling -- the
three places section 5.4/section 6.2/section 6.6 of the spec say a difference would be observable.

    from tests.adversarial.simulator import SparkBusSim, spark, attach
    from sparksim import SparkBusSim, spark, attach        # the same objects

What the simulator gives a test that a frame replayer cannot: ground truth no
CAN frame reveals -- `dev.ram` vs `dev.flash`, `dev.write_log[-1].echoed` vs
`.requested`, `dev.persist_log[-1].stale`, `dev.clear_log`. Every confirmed
driver defect is a disagreement between the driver's conclusion and one of those.
"""
from __future__ import annotations

import os
import sys

_SUPPORT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "support")
if _SUPPORT not in sys.path:
    sys.path.insert(0, _SUPPORT)

from sparksim import frames  # noqa: E402
from sparksim import *  # noqa: E402,F401,F403
from sparksim import __all__ as _names  # noqa: E402
from sparksim.bus import EPS, SimMessage, SparkBusSim  # noqa: E402,F401
from sparksim.clock import VirtualClock  # noqa: E402,F401
from sparksim.controller import SimSpark  # noqa: E402,F401
from sparksim.faults import (ClearRecord, Congestion, IgnoredRecord,  # noqa: E402
                             PersistRecord, SetCanIdRecord, Silence,
                             SparkBehaviour, WriteRecord)
from sparksim.fleet import (ROLES_FLEX, SERIALS_FLEX, build_fleet,  # noqa: E402
                            factory, provisioned, spark)
from sparksim.harness import (assert_no_setpoints, assert_quiet_after,  # noqa: E402
                              attach, explain, patch_can_bus, periods_on_the_wire)

F = frames
__all__ = list(_names) + ["frames", "F", "Silence", "Congestion", "IgnoredRecord", "EPS"]
