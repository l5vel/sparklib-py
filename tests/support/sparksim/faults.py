"""Misbehaviour knobs, audit records, and the invariants the bus enforces.

`SparkBehaviour` is all-keyword and every field defaults to a healthy 26.1.6
controller with one deliberate exception: `persist_settle_s` defaults to 0.200,
so a bare controller reproduces CD 432129. The well-behaved controller is the
opt-in, because a simulator that defaults to lenient turns an adversarial test
into a test of a healthy bus.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Mapping, Optional


# -- invariants --------------------------------------------------------------

class BootloaderFrameForbidden(AssertionError):
    """ENTER_SWDL_CAN_BOOTLOADER halts the controller. Nothing may send it."""


class SetpointFrameForbidden(AssertionError):
    """spark_admin claims it never sends a setpoint; this enforces the claim."""


class ProtectedWriteReachedBus(AssertionError):
    """A hard-limit parameter write left the driver instead of being refused."""


class AmbiguousDeviceError(LookupError):
    """Two controllers answer this CAN id; pass the SimSpark to disambiguate."""


class SimFrameBudgetExceeded(RuntimeError):
    """A window asked for more frames than any real test needs."""


class BusShutdown(RuntimeError):
    """send()/recv() after shutdown()."""


# -- behaviour ---------------------------------------------------------------

@dataclass
class SparkBehaviour:
    reply_latency_s: float = 0.001

    # PARAM_WRITE
    write_result: int = 0
    drop_write_responses: int = 0
    drop_write_responses_for: frozenset = frozenset()
    echo_value: Optional[int] = None
    echo_transform: Optional[Callable[[int, int], int]] = None
    apply_delay_s: float = 0.0
    ignore_writes_for: frozenset = frozenset()

    # PERSIST
    persist_result: int = 0
    persist_settle_s: float = 0.200
    persist_blackout_s: float = 0.0
    drop_persist_response: bool = False

    # identity
    accept_set_can_id: bool = True
    set_can_id_ram_only: bool = False

    # faults
    latched_faults: int = 0
    latched_warnings: int = 0
    latched_sticky_faults: int = 0
    latched_sticky_warnings: int = 0
    ignore_clear_faults: bool = False
    clear_faults_response: bool = False

    # PARAM read (id alone, no value)
    answer_param_reads: bool = True
    unreadable_params: frozenset = frozenset()
    read_result: int = 0
    # Firmware that mistakes a short frame for a write. The hazard the sweep's
    # canary exists to catch: the "read" silently zeroes what it reports.
    param_read_writes_zero: bool = False
    param_read_reports_pre_value: bool = False
    # Data-port limit inputs left unwired, which is how rig-flex's steers are
    # built. The input rests high on the SPARK's own pull-up, so the limit reads
    # REACHED exactly when the polarity parameter says normally-open. Measured
    # on rig-flex id 17. Off by default: a fixture that sets hard_fwd
    # directly is modelling a switch, not a polarity.
    unwired_limit_inputs: bool = False

    # presence
    answer_firmware: bool = True

    # -- the pre-25 dialect --------------------------------------------------
    # A pre-25 controller answers parameter access on api class 48, takes a
    # status-period write on api class 6 and takes a burn flash on api 0x072,
    # and carries none of the 25+ parameter frames. These four say how well it
    # does the parameter and period halves; the burn is the field below them.
    answer_legacy_param_reads: bool = True
    legacy_param_refuse_status: int = 0     # non-zero: the device replies, refusing
    unanswerable_legacy_params: frozenset = frozenset()
    ignore_legacy_period_writes: bool = False

    # Pre-25 burn flash, api 0x072. MEASURED on rig-max, firmware
    # 24.0.1: the first two bytes are read little-endian and must be the 25+
    # persist magic 15011; the magic is answered 0x00 on the request's own
    # arbitration id and commits the PARAMETER TABLE to flash, anything else is
    # answered 0xFF, and a zero-length frame draws no reply at all. The status
    # periods are never committed (pre25.burn_flash_api, how=HARDWARE).
    #
    # Defaults True because the simulator models the hardware. Nothing in this
    # package sends the frame, so this default is reached only by a test that
    # sends it deliberately. Set False to model a controller that REFUSES a
    # correctly-formed burn, which is a defect rather than the norm.
    legacy_burn_flash_works: bool = True

    def __post_init__(self):
        self.drop_write_responses_for = frozenset(self.drop_write_responses_for)
        self.ignore_writes_for = frozenset(self.ignore_writes_for)
        self.unreadable_params = frozenset(self.unreadable_params)
        self.unanswerable_legacy_params = frozenset(self.unanswerable_legacy_params)


# -- records -----------------------------------------------------------------

@dataclass(frozen=True)
class WriteRecord:
    at: float
    param_id: int
    requested: int
    echoed: int
    result: int
    response_at: Optional[float]
    commit_at: Optional[float]
    pre_value: Optional[int]


@dataclass(frozen=True)
class PersistRecord:
    at: float
    committed: Mapping[int, int]
    stale: tuple
    result: int


@dataclass(frozen=True)
class ClearRecord:
    at: float
    before: tuple
    after: tuple


@dataclass(frozen=True)
class SetCanIdRecord:
    at: float
    from_id: int
    to_id: int
    serial: str
    accepted: bool
    reason: str


@dataclass(frozen=True)
class IgnoredRecord:
    at: float
    arb: int
    reason: str


@dataclass
class Silence:
    start: float
    end: float
    apis: Optional[frozenset] = None
    reason: str = "dropout"

    def covers(self, t: float, api: Optional[int] = None) -> bool:
        if not (self.start <= t < self.end):
            return False
        return self.apis is None or api is None or api in self.apis


@dataclass
class Congestion:
    fraction: float
    start: float = 0.0
    end: Optional[float] = None

    def covers(self, t: float) -> bool:
        return t >= self.start and (self.end is None or t < self.end)
