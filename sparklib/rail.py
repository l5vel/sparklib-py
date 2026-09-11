"""Grading a motor rail's voltage, and latching the result without flapping.

A SPARK reports the voltage at its own terminals. Turning that into "is this
pack about to brown out" needs the chemistry, because 11.8 V is healthy on one
battery and nearly flat on another. Brownout is the mechanism behind SPARK
configuration loss, so this is a diagnosis tool and not a fuel gauge.

Nothing here talks to a battery. It takes volts and a small config object with
`chemistry`, or explicit `full_v` and `empty_v`, and returns a grade.
"""

import logging
import time
from dataclasses import dataclass
from enum import Enum

_LOG = logging.getLogger(__name__)


# Deliberately three states and no UNKNOWN: a missing reading returns None, so a
# caller must handle "I don't know" explicitly rather than have it silently look
# like OK.

LOW_PCT = 20.0
CRITICAL_PCT = 10.0
# Recovery needs this much margin above a threshold before the state relaxes, so
# a reading sitting on a boundary does not flap between states under load.
HYSTERESIS_PCT = 3.0


class PowerLevel(str, Enum):
    """Ordered worst-last so max() picks the more severe of two sources."""

    CHARGING = "charging"
    OK = "ok"
    LOW = "low"
    CRITICAL = "critical"

    @property
    def severity(self):
        return {"charging": 0, "ok": 1, "low": 2, "critical": 3}[self.value]

    @property
    def is_distress(self):
        return self.value in ("low", "critical")



def classify_power(pct, low=LOW_PCT, critical=CRITICAL_PCT, ac_input_w=None):
    """Percentage of usable range -> PowerLevel. None in, None out."""
    if pct is None:
        return None
    if ac_input_w is not None and ac_input_w > 0:
        return PowerLevel.CHARGING
    if pct <= critical:
        return PowerLevel.CRITICAL
    if pct <= low:
        return PowerLevel.LOW
    return PowerLevel.OK


# Resting endpoints of the USABLE range for each chemistry on the fleet. NOT a
# state of charge: LiFePO4 is nearly flat between its endpoints, SLA has a usable
# slope but is very load- and temperature-sensitive, and both sag under load and
# with age. Every rail on this fleet is a single 12 V battery -- packs are never
# wired in series, so these are absolute volts, not per-cell.
CHEMISTRY = {
    # 4S LiFePO4: ~13.4 V resting full, knee around 12.0 V.
    "LiFePO4": {"full_v": 13.4, "empty_v": 12.0},
    # Sealed lead acid: ~12.7 V resting full. empty_v here is the CELL LIMIT,
    # 1.75 V/cell, not the healthy floor -- so the grade is range remaining, not
    # pack health. docs/BATTERY.md carries the reasoning and the alternative
    # endpoints; read it before changing either number.
    "SLA": {"full_v": 12.7, "empty_v": 10.5},
}

# How far outside the configured range a reading may sit before it is treated as
# a measurement or wiring problem rather than a charge level. Since every rail is
# a single 12 V battery, a reading near double the range means something is wired
# wrong or the chemistry is misconfigured -- and clamping that to a cheerful 100%
# would hide it.
_PLAUSIBLE_ABOVE_FULL = 1.15
_PLAUSIBLE_BELOW_EMPTY = 0.80


def rail_endpoints(cfg):
    """(full_v, empty_v) for the rail, or (None, None) if it cannot be graded.

    Explicit full_v/empty_v in the config win; otherwise they come from the
    chemistry table. An unknown or absent chemistry yields no endpoints, so the
    rail voltage is reported but never graded -- it is not guessed at.
    """
    if cfg is None:
        return None, None
    full = getattr(cfg, "full_v", None)
    empty = getattr(cfg, "empty_v", None)
    if full is None or empty is None:
        chem = getattr(cfg, "chemistry", None)
        table = CHEMISTRY.get(str(chem)) if chem else None
        if table is None:
            return None, None
        full = table["full_v"] if full is None else full
        empty = table["empty_v"] if empty is None else empty
    return float(full), float(empty)


def rail_implausible(volts, full_v, empty_v):
    """Why this reading cannot be a charge level, or None if it is believable.

    Guards the failure the removed `nominal_v` knob used to invite: a rail that is
    not the battery the config describes. Without this, 25 V on a 12 V SLA config
    clamps silently to 100% -- the most dangerous possible answer.
    """
    if volts is None or full_v is None or empty_v is None:
        return None
    if volts > full_v * _PLAUSIBLE_ABOVE_FULL:
        return (f"rail reads {volts:.2f} V, well above the {full_v:.2f} V full point "
                "for the configured chemistry -- check battery.rail.chemistry and "
                "the pack wiring")
    if volts < empty_v * _PLAUSIBLE_BELOW_EMPTY:
        return (f"rail reads {volts:.2f} V, far below the {empty_v:.2f} V empty "
                "point -- the pack is flat, disconnected, or being measured wrong")
    return None


def rail_usable_pct(volts, full_v, empty_v):
    """Where `volts` sits in the pack's usable range, 0-100, or None.

    NOT a state of charge. For LiFePO4 the voltage curve is almost flat across
    most of the usable range, and it sags under load and with age, so treat this
    as a coarse "how much headroom is left" indicator only.
    """
    if volts is None or full_v is None or empty_v is None or full_v <= empty_v:
        return None
    return max(0.0, min(100.0, (volts - empty_v) / (full_v - empty_v) * 100.0))


class PowerFsm:
    """Three-state latch with hysteresis, so a boundary reading cannot flap.

    Falls to a worse state as soon as a reading warrants it, and only recovers
    once the reading clears the threshold by HYSTERESIS_PCT. update() returns
    (level, changed) so a caller can act on transitions rather than poll.
    """

    def __init__(self, low=LOW_PCT, critical=CRITICAL_PCT,
                 hysteresis=HYSTERESIS_PCT, name="power"):
        self.low = low
        self.critical = critical
        self.hysteresis = hysteresis
        self.name = name
        self.level = None

    def update(self, pct, ac_input_w=None):
        candidate = classify_power(pct, self.low, self.critical, ac_input_w=ac_input_w)
        if candidate is None:
            return self.level, False
        if self.level is not None and candidate.severity < self.level.severity:
            # Recovering: require margin above the threshold being left behind.
            floor = self.critical if self.level is PowerLevel.CRITICAL else self.low
            if pct < floor + self.hysteresis:
                return self.level, False
        changed = candidate is not self.level
        self.level = candidate
        return candidate, changed

    def distress_message(self, pct):
        if self.level is None or not self.level.is_distress:
            return None
        urgency = ("CRITICAL -- charge now"
                   if self.level is PowerLevel.CRITICAL else "LOW -- charge soon")
        return f"[{self.name}] {urgency} ({pct:.0f}% of usable range)"


@dataclass(frozen=True)
class RailStatus:
    """Motor-rail voltage as measured at the SPARK controllers."""

    per_controller: dict          # {can_id: volts}
    brownout_ids: tuple = ()      # controllers reporting brownout, active or sticky
    reset_ids: tuple = ()         # controllers reporting a hasReset warning
    updated_at: float = 0.0

    @property
    def mean_v(self):
        v = list(self.per_controller.values())
        return sum(v) / len(v) if v else None

    @property
    def min_v(self):
        return min(self.per_controller.values()) if self.per_controller else None

    @property
    def max_v(self):
        return max(self.per_controller.values()) if self.per_controller else None

    @property
    def spread_v(self):
        if not self.per_controller:
            return None
        return self.max_v - self.min_v

    def to_dict(self):
        return {
            "per_controller": dict(self.per_controller),
            "mean_v": self.mean_v,
            "min_v": self.min_v,
            "max_v": self.max_v,
            "spread_v": self.spread_v,
            "brownout_ids": list(self.brownout_ids),
            "reset_ids": list(self.reset_ids),
            "updated_at": self.updated_at,
        }


def get_rail_status(channel=None, window=4.0):
    """Read motor-rail voltage from the SPARK bus. None if it cannot be read.

    Passive: listens to STATUS_0/STATUS_1 broadcasts and sends nothing, so it
    never enables a controller. Returns None rather than raising when there is no
    CAN bus, so a caller that only wants the pack SoC is unaffected.
    """
    try:
        from . import config as cfg
        from .admin import SparkAdmin, collect_status
    except Exception as err:            # noqa: BLE001 - optional CAN stack
        _LOG.debug("rail voltage unavailable: %s", err)
        return None
    channel = channel or getattr(getattr(cfg.get(), "can", None), "interface", None)
    if not channel:
        return None
    try:
        with SparkAdmin(channel) as adm:
            status = collect_status(adm.bus, window)
    except Exception as err:            # noqa: BLE001 - no bus, down iface, perms
        _LOG.debug("rail voltage read failed on %s: %s", channel, err)
        return None

    # normalised_reading, so a pre-25 bus reports rather than raising KeyError
    # here, outside the try above.
    from .admin import normalised_reading
    volts, brownout, resets = {}, [], []
    for dev, v in status.items():
        n = normalised_reading(v)
        if n.get("voltage_v") is not None:
            volts[dev] = n["voltage_v"]
        warns = (n.get("warnings") or []) + (n.get("sticky_warnings") or [])
        if "brownout" in warns:
            brownout.append(dev)
        if "hasReset" in warns:
            resets.append(dev)
    if not volts:
        return None
    return RailStatus(per_controller=volts, brownout_ids=tuple(sorted(brownout)),
                      reset_ids=tuple(sorted(resets)), updated_at=time.time())
