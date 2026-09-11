"""SPARKSIM: a SPARK CAN bus simulator with ground truth no frame reveals.

    from sparksim import SparkBusSim, SimSpark, SparkBehaviour, spark, attach
    from sparksim import frames as F

Reached by having tests/support on sys.path (see tests/conftest.py); `tests` is
deliberately not a package.
"""
from .bus import SimMessage, SparkBusSim
from .clock import VirtualClock
from .controller import SimSpark
from .faults import (AmbiguousDeviceError, BootloaderFrameForbidden, BusShutdown,
                     ClearRecord, PersistRecord, ProtectedWriteReachedBus,
                     SetCanIdRecord, SetpointFrameForbidden, SimFrameBudgetExceeded,
                     SparkBehaviour, WriteRecord)
from .fleet import (APPENDIX_A_STATUS_1_PERIOD_MS, BASE03_UNIQUE_ID_PERIOD_MS,
                    REV_DEFAULT_STATUS_1_PERIOD_MS, ROLES_FLEX, SERIALS_FLEX,
                    ROLES_MAX, SERIALS_MAX, BASE02_FIRMWARE,
                    build_fleet_pre25,
                    build_fleet, factory, provisioned, spark)
from .harness import (assert_no_setpoints, assert_quiet_after, attach, explain,
                      patch_can_bus, periods_on_the_wire)

__all__ = [
    "VirtualClock", "SimSpark", "SparkBehaviour", "SparkBusSim", "SimMessage",
    "spark", "build_fleet", "provisioned", "factory", "attach", "patch_can_bus",
    "ROLES_FLEX", "SERIALS_FLEX", "BASE03_UNIQUE_ID_PERIOD_MS",
    "ROLES_MAX", "SERIALS_MAX", "BASE02_FIRMWARE", "build_fleet_pre25",
    "APPENDIX_A_STATUS_1_PERIOD_MS", "REV_DEFAULT_STATUS_1_PERIOD_MS",
    "WriteRecord", "PersistRecord", "ClearRecord", "SetCanIdRecord",
    "BootloaderFrameForbidden", "SetpointFrameForbidden", "ProtectedWriteReachedBus",
    "AmbiguousDeviceError", "SimFrameBudgetExceeded", "BusShutdown",
    "assert_no_setpoints", "assert_quiet_after", "periods_on_the_wire", "explain",
]
