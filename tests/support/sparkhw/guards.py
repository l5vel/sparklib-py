"""The preconditions and the undo. Nothing in this suite writes to a controller
until all of these hold.

Four guards, each for a way this suite could do harm rather than find it:

  live experiments  rig-flex-2 is mid-experiment. Five drifted controllers are the
                    control condition and three intact ones are the subjects,
                    and a single `spark repair --persist` destroys both. The
                    denylist here refuses the whole hardware directory on a base
                    that is carrying an experiment, including the persistence
                    test that was written before this file existed.
  the link          `restart-ms 0` on this fleet, so a bus-off state does not
                    clear itself. Checked before and after every test, because
                    the congestion injections can reach it and the next test
                    would otherwise measure a dead adapter.
  at rest           a running SparkBus sends SECONDARY_HEARTBEAT every 20 ms and
                    the controllers are then enabled. Writing a parameter into a
                    controller that is driving is the missing at-rest
                    precondition the adversarial suite has an xfail for, and it
                    would be a poor look to have the suite do it.
  the undo          `StatusPeriodGuard` restores the declared period on the way
                    out, and says so loudly if it could not. Nothing here
                    persists, so the backstop under every configuration
                    injection is that flash still holds the provisioned value
                    and a motor-rail power cycle restores it.

Opt-in gates, all environment variables, all off by default. `--hardware` alone
buys reading the bus and injecting frames; each gate below buys one further
class of side effect, and the README tabulates them.
"""
from __future__ import annotations

import json
import os
import subprocess
import time

import can

from sparklib import admin as sa
from sparksim import frames as F

from.wire import FORBIDDEN_BASES

# Bases that carry a robot doing something, rather than a robot sitting still.
_COMMAND_BASES = dict(FORBIDDEN_BASES)
_COMMAND_BASES.update({sa.PARAM_WRITE: "PARAMETER_WRITE",
                       sa.SET_CAN_ID: "SET_CAN_ID",
                       sa.PERSIST: "PERSIST_PARAMETERS",
                       sa.CLEAR_FAULTS: "CLEAR_FAULTS"})

# A base index listed here is carrying an experiment whose value is destroyed by
# writing to it. Remove the entry when the experiment is finished and its
# findings are written up -- not to get a test to run.
LIVE_EXPERIMENTS = {
    "rig-flex-2": (
        "rig-flex-2 is the brownout / config-loss experiment. Controllers 9, 10, 11, "
        "12 and 14 are drifted and are the control; 13, 15 and 16 hold the "
        "provisioned 20 ms across a power cycle at 10.24 V and are the subjects. "
        "Any write, and every persist, destroys both halves. Protocol: "
        "docs/runs/rig-flex-2-brownout-protocol.md"
 ),
}

GATES = {
    "inject": ("SPARK_HW_INJECT",
               ("reversible parameter writes, never persisted: Status 0/1 "
                "Period on one nominated controller, and on pre-25 firmware "
                "Idle Mode across the fleet for the rail-cycle volatility "
                "proof, which a power cycle reverts by definition")),
    "collide": ("SPARK_HW_COLLIDE",
                ("transmitting on a CAN id a real controller owns, which "
                 "produces occasional arbitration errors on that controller")),
    "flash": ("SPARK_HW_FLASH",
              "PERSIST_PARAMETERS, which spends a flash cycle"),
    "congest": ("SPARK_HW_CONGEST",
                ("filling the bus, which can drive the adapter to bus-off on an "
                 "interface with restart-ms 0")),
}

STAGE_ENV = "SPARK_HW_STAGE"

# What 26.1.6 actually accepts, measured on id 10. REV's parameter
# table documents neither bound. A refused write answers Invalid and echoes the
# value the device still holds, which is the signal a verify-and-retry needs.
# Per-parameter ceilings for a status period. REVLib caps every status signal at
# 1000 ms and does not document it (REV-Software-Binaries#19), but that is a
# library limit: this suite writes raw CAN and bypasses REVLib entirely, and
# this fleet demonstrably held 20000 ms on parameter 159 -- the wire went quiet
# for 18 s. So the numbers here are the firmware's, not REVLib's, and a tool
# that goes through REVLib will see something different.
MAX_PERIOD_MS = {158: 1000, 159: 32767}
MIN_PERIOD_MS = 1

# The period written to take one status frame off the air, clamped per parameter
# to the range above. It is a compromise in one direction only: long enough that
# no real frame lands inside a test body, and short enough that putting it back
# does not cost the suite the whole period.
#
# It costs the whole period because a Status Period write governs only from the
# NEXT frame onward. Writing 20000 and then writing 20 back two seconds later
# both returned Success, and the wire stayed silent for another 18 s before
# resuming at a clean 20 ms. So an injected period is also the worst-case time to
# undo it, and 20000 made every restore in this suite time out and report a
# failure that was really its own impatience.
STARVED_PERIOD_MS = 8000

# Above this, a period is not worth reading back inside the guard: the window
# needed to see one frame is longer than the settle a caller can afford, and
# sampling it steals the frame the caller's own assertion is waiting for.
_MEASURABLE_CEILING_MS = 2000


def base_index():
    """'rig-flex' -- the per-robot key, read through the CLI's own resolver."""
    from sparklib import cli as spark_cli
    return spark_cli._base_index()


def live_experiment_reason(index=None):
    """Why this robot must not be written to, or None."""
    return LIVE_EXPERIMENTS.get(index or base_index())


# -- the CAN link --------------------------------------------------------------

def link_info(channel):
    """`ip -details -statistics -json link show`, or None if the netdev is absent."""
    try:
        out = subprocess.run(["ip", "-details", "-statistics", "-json", "link",
                              "show", channel],
                             capture_output=True, text=True, timeout=5,
                             check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0 or not out.stdout.strip():
        return None
    try:
        rows = json.loads(out.stdout)
    except ValueError:
        return None
    if not rows:
        return None
    row = rows[0]
    linkinfo = row.get("linkinfo") or {}
    info = linkinfo.get("info_data") or {}
    stats = row.get("stats64") or {}
    return {
        "kind": linkinfo.get("info_kind"),
        "operstate": row.get("operstate"),
        "state": info.get("state"),
        "restart_ms": info.get("restart_ms"),
        "bitrate": (info.get("bittiming") or {}).get("bitrate"),
        "rx_errors": (stats.get("rx") or {}).get("errors"),
        "rx_dropped": (stats.get("rx") or {}).get("dropped"),
        "rx_over_errors": (stats.get("rx") or {}).get("over_errors"),
        "tx_errors": (stats.get("tx") or {}).get("errors"),
        "rx_packets": (stats.get("rx") or {}).get("packets"),
        "tx_packets": (stats.get("tx") or {}).get("packets"),
    }


def require_link(channel):
    """The reason this bus cannot be used, or None."""
    info = link_info(channel)
    if info is None:
        return f"CAN interface {channel} does not exist; bring the bus up first"
    if info["operstate"] not in ("UP", "UNKNOWN"):
        return f"CAN interface {channel} is {info['operstate']}, not UP"
    if info["state"] == "BUS-OFF":
        return (f"CAN interface {channel} is BUS-OFF and restart-ms is "
                f"{info['restart_ms']}, so it will not recover on its own: "
                f"sudo ip link set {channel} down && sudo ip link set {channel} up")
    return None


def link_is_error_active(channel):
    info = link_info(channel)
    return bool(info) and info.get("state") in ("ERROR-ACTIVE", "ERROR-WARNING")


def link_is_virtual(channel):
    """A vcan interface, which has no controller and so no CAN error state.

    Worth knowing because the wire tier runs unchanged on one: socketcan loops
    frames between sockets whether or not a transceiver is attached, so the
    injector and the driver can be exercised with no robot. What a vcan run
    cannot produce is a controller, so every test that reads one fails there
    honestly.
    """
    info = link_info(channel)
    return bool(info) and info.get("kind") != "can"


# -- the bus at rest -----------------------------------------------------------

def at_rest_reasons(channel, seconds=1.5):
    """Why this bus is not at rest, as a list of strings. Empty means it is.

    A robot at rest broadcasts status frames and nothing else. Anything that
    commands, enables or writes means another process owns these controllers,
    and this suite must not be the second writer on them.
    """
    reasons = []
    bus = can.Bus(interface="socketcan", channel=channel)
    seen = {}
    try:
        end = time.time() + seconds
        while time.time() < end:
            m = bus.recv(timeout=max(0.0, end - time.time()))
            if m is None:
                continue
            base = m.arbitration_id & ~0x3F
            name = _COMMAND_BASES.get(base) or _COMMAND_BASES.get(m.arbitration_id)
            if name:
                seen[name] = seen.get(name, 0) + 1
    finally:
        bus.shutdown()
    for name, count in sorted(seen.items()):
        reasons.append(f"{count} {name} frame(s) in {seconds:.1f} s")
    return reasons


# -- opt-in --------------------------------------------------------------------

def gate_open(name):
    var, _ = GATES[name]
    return os.environ.get(var, "").strip() not in ("", "0", "false", "no")


def require_gate(name):
    """The skip reason for a closed gate, or None."""
    if gate_open(name):
        return None
    var, what = GATES[name]
    return f"set {var}=1 to allow {what}"


def stage():
    return os.environ.get(STAGE_ENV, "").strip()


def require_stage(name):
    if stage() == name:
        return None
    return f"set {STAGE_ENV}={name} and follow tests/hardware/README.md"


# -- the undo ------------------------------------------------------------------

class RestoreFailed(RuntimeError):
    """The injected value could not be put back over CAN."""


class StatusPeriodGuard:
    """Write a status-frame period on one controller and put it back.

    The only parameter class this suite writes. Nothing is persisted, so flash
    still holds the provisioned value throughout and a motor-rail power cycle is
    the backstop if this process dies with the injection in place. The restore
    is verified on the wire rather than trusted to the response code, because a
    result code is not proof (catalogue cross-cutting lesson 1).
    """

    def __init__(self, adm, dev, param_id=sa.PARAM_STATUS_1_PERIOD, role="drive",
                 restore_to=None, generation=None):
        self.adm = adm
        self.dev = dev
        self.param_id = param_id
        self.role = role
        self.generation = generation or sa.dominant_generation(
            sa.collect_status(adm.bus, 2.0), default=sa.GEN_FW25)
        self.restore_to = int(
            restore_to if restore_to is not None
            else self._default_restore(param_id, role))
        self.injected = None

    def _default_restore(self, param_id, role):
        """What this frame goes back to.

        On pre-25 the reference is the boot throttle spark_can re-sends on every
        power cycle, not the declared configuration, which describes 25+ values.
        """
        if self.legacy:
            from sparklib.can_bus import _SPARKMAX_STATUS_PERIODS_MS
            return _SPARKMAX_STATUS_PERIODS_MS[self.frame_index]
        return sa.declared_value(param_id, role,
                                 default=sa.APPENDIX_A_STATUS_1_PERIOD_MS)

    @property
    def legacy(self):
        """Whether this bus speaks the pre-25 write dialect."""
        return self.generation == sa.GEN_PRE25

    @property
    def frame_index(self):
        """Status frame number, 0..6. Parameters 158..165 run Status 0..7."""
        return self.param_id - sa.PARAM_STATUS_0_PERIOD

    @property
    def api(self):
        """The status frame this parameter's period governs.

        Pre-25 carries the same frames on the legacy api class, so the parameter
        id still names WHICH frame -- it just is not how the period gets written.
        """
        if self.legacy:
            return sa.LEGACY_STATUS_0_API + self.frame_index
        return F.API_FOR_PERIOD_PARAM[self.param_id]

    def starve_value(self):
        """The longest period this frame takes, capped by the suite's own.

        MAX_PERIOD_MS holds the 25+ ceilings PARAMETER_WRITE refuses past. Pre-25
        does not go through a parameter: LEGACY_SET_PERIOD carries a 16-bit
        millisecond value, so 65535 is the only ceiling there.
        """
        if self.legacy:
            return min(STARVED_PERIOD_MS, 0xFFFF)
        return min(STARVED_PERIOD_MS, MAX_PERIOD_MS.get(self.param_id,
                                                        STARVED_PERIOD_MS))

    def write(self, value_ms):
        """Write one period and return a response dict.

        Two dialects. On 25+ this is PARAMETER_WRITE and the device echoes what
        it took, so the echo is the check. Pre-25 does not carry that frame; the
        period moves on api class 6, which answers nothing, so the legacy branch
        measures the cadence and synthesises the same response shape. A caller
        can therefore treat both alike.
        """
        value_ms = int(value_ms)
        ceiling = 0xFFFF if self.legacy else MAX_PERIOD_MS.get(self.param_id)
        if ceiling is not None and not MIN_PERIOD_MS <= value_ms <= ceiling:
            raise ValueError(
                f"parameter {self.param_id} accepts {MIN_PERIOD_MS}..{ceiling} ms "
                f"on firmware 26.1.6 and {value_ms} would be refused as Invalid")
        if self.legacy:
            return self._write_legacy(value_ms)
        result = self.adm.write_param(self.dev, self.param_id, value_ms)
        if result is None:
            raise RestoreFailed(
                f"id {self.dev} did not answer PARAMETER_WRITE for parameter "
                f"{self.param_id}; the bus reading is not what this test thinks")
        if not result.get("verified", True):
            # A device that answers Success while echoing a different value is
            # CD 456184 exactly, and a guard that starves a frame to a period
            # the firmware silently clamped is measuring the wrong thing for
            # the rest of the test. REVLib caps status periods at 1000 ms
            # (REV-Software-Binaries#19); raw CAN does not -- this fleet held
            # 20000 ms and stayed silent 18 s to prove it -- so an echo that
            # disagrees here is a real refusal rather than a known ceiling.
            raise RestoreFailed(
                f"id {self.dev} answered {result['result_text']} for parameter "
                f"{self.param_id} but echoed {result['value']} when asked for "
                f"{value_ms}. The period on the wire is not the one this test "
                "set, so nothing measured after this point means anything")
        if result["result"] == 0:
            self.injected = int(value_ms)
        return result

    def _write_legacy(self, value_ms):
        """Set the period on api class 6 and read the cadence back.

        `injected` is recorded BEFORE the measurement, deliberately: the
        controller changed the instant the frame went out, so gating the record
        on a successful read-back would leave restore() with nothing to undo
        whenever the measurement is inconclusive.

        Silence is a legitimate read-back, since a starved frame may not fill a
        window. Either the asked-for cadence or no frame at all counts as taken;
        only a cadence that is neither is a refusal.
        """
        self.adm.set_legacy_status_period(self.dev, self.frame_index, value_ms)
        self.injected = value_ms
        if value_ms > _MEASURABLE_CEILING_MS:
            # Too slow to time without consuming the frame the caller's own
            # window is waiting for: `status_period_ms` divides the window by the
            # frame count, so one late frame reads as a cadence rather than as
            # silence. Send, record, and leave verification to the caller.
            return {"param_id": self.param_id, "value": value_ms,
                    "requested": value_ms, "verified": True, "attempts": 1,
                    "result": 0, "observed_ms": None, "dialect": "legacy",
                    "result_text": "sent; too slow to time without eating the "
                                   "caller's window, so not read back here"}
        # A period governs only from the frame AFTER the next one, so the old
        # cadence keeps landing briefly. Settle, then drain, or the window below
        # times those stale frames instead of the new cadence.
        time.sleep(min(2.0, value_ms / 1000.0 + 0.3))
        self.adm._drain()
        window = min(5.0, max(1.0, 2.5 * value_ms / 1000.0))
        observed = self.adm.status_period_ms(self.dev, self.api, seconds=window)
        took = observed is None or abs(observed - value_ms) < max(30, value_ms * 0.25)
        return {"param_id": self.param_id, "value": value_ms,
                "requested": value_ms, "verified": took,
                "attempts": 1, "result": 0 if took else 1,
                "result_text": ("sent; pre-25 acknowledges nothing, so the "
                                f"cadence is the read-back and it measured {observed}"),
                "observed_ms": observed, "dialect": "legacy"}

    def restore(self, verify_seconds=1.0):
        """Put the declared value back and confirm it on the wire.

        The write is sent first and then this waits for the frame the injected
        period already armed, because the new period governs only from the frame
        after that one. Measuring before it fires reads silence and calls a
        restore that worked a failure -- which is what happened on the first
        hardware run of this suite, four times.
        """
        if self.injected is None:
            return None
        if self.legacy:
            self.adm.set_legacy_status_period(self.dev, self.frame_index,
                                              self.restore_to)
            result = {"result": 0, "dialect": "legacy"}
        else:
            result = self.adm.write_param(self.dev, self.param_id,
                                          self.restore_to)
        # Drain FIRST. A caller that spoke for this frame while the guard held it
        # leaves its own injected frames in the socket, and the wait below would
        # break on one of those instead of on the controller's first real frame.
        self.adm._drain()
        # Worst case is two injected periods: the restore governs only from the
        # frame after the one the old period already armed.
        deadline = time.time() + 2.0 * self.injected / 1000.0 + 2.0
        while time.time() < deadline:
            if self.adm.status_period_ms(self.dev, self.api,
                                         seconds=0.25) is not None:
                break
        # The frame that breaks the loop above is the one the OLD period armed;
        # the restored cadence only starts after it. Let a few of the restored
        # periods pass and drain, or the window below can open inside that gap
        # and read silence from a controller that has already recovered.
        time.sleep(min(2.0, 3.0 * self.restore_to / 1000.0))
        self.adm._drain()
        observed = self.adm.status_period_ms(self.dev, self.api,
                                             seconds=verify_seconds)
        ok = (result is not None and result["result"] == 0
              and observed is not None
              and abs(observed - self.restore_to) < 30)
        if not ok:
            raise RestoreFailed(
                f"id {self.dev} parameter {self.param_id} was set to "
                f"{self.injected} and is now reading {observed} ms, not the "
                f"declared {self.restore_to} ms (write response {result}). "
                "Nothing was persisted, so cutting and restoring motor power "
                "reloads the provisioned value from flash.")
        self.injected = None
        return observed

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.restore()
        return False
