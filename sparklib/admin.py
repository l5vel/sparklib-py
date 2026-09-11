"""SPARK Flex/MAX administration over CAN: inventory, config audit, repair.

Frame definitions come from REV's published spec, REV-Specs
spark-frames-2.1.0, mirrored in reference/. Verified on SparkFlex
firmware 26.1.6 against a live 8-motor bus.

What works on 26.1.6, measured:
  * GET_FIRMWARE_VERSION answers an RTR or zero-length request
  * PARAMETER_WRITE / PERSIST_PARAMETERS / SET_CAN_ID all work and reply
  * every parameter READ frame (0x0C0, 0x0D0, 0x0F0+) is silent, so a config
    read-back is not available -- drift is detected from broadcast behaviour

Nothing here sends a setpoint or starts the enable heartbeat, so a controller
stays disabled and cannot actuate while these run.
"""

import os
import struct
import time

import can

# Frame bases, arbitration id = base | device_id unless noted.
CLEAR_FAULTS = 0x02051B80
IDENTIFY_UNIQUE = 0x02051D80
SET_CAN_ID = 0x02052540
GET_FIRMWARE = 0x02052600
PARAM_WRITE = 0x02053800
PARAM_WRITE_RESP = 0x02053840

# Pre-25 status-period write. Shares the STATUS_N broadcast id; the SPARK tells
# the two apart by DLC -- 2 bytes sets the period, 8 bytes is status data. Fire
# and forget: the device sends no response, so the only read-back is measuring
# the cadence. This is the dialect a MAX honours; PARAM_WRITE above is answered
# on 25+ only, which is why `repair` reads as unwritable on 24.0.1.
LEGACY_SET_PERIOD = 0x02051800
LEGACY_FRAME_MIN, LEGACY_FRAME_MAX = 0, 6

# Pre-25 parameter access. The id rides in the ARBITRATION ID and the reply returns
# on that same id; no separate response frame.
#
#   arb   = 0x02050000 | ((0x300 | param_id) << 6) | device_id
#   read  = DLC 0                -> [uint32 value][type][status]
#   write = [int32 value][type]  -> device echoes the value it took
#
# Pre-25 answers both directions here. The 25+ PARAMETER_WRITE above is
# versionImplemented 25.0.0, so pre-25 carries neither it nor any read frame and
# drops unmatched arbitration ids without replying.
# Evidence and coverage: docs/SPARKMAX-BRINGUP.md, provenance.
# Pre-25 device fingerprint. A zero-length request to this api, ADDRESSED to a
# device, is answered with four read-only bytes that are unique per controller.
# Measured on rig-max: eight distinct values, all stable, no bit fixed
# across the fleet, and unaffected by two write attempts. It sits in apiClass 9
# beside SET_CAN_ID at index 5 and GET_FIRMWARE at index 8.
#
# It is NOT the serial IDENTIFY_UNIQUE_SPARK compares against -- pre-25 identify
# is addressed by CAN id and carries no serial at all -- and REV Hardware Client
# does not surface it. Treat it as a device fingerprint, which is all that
# identity, duplicate detection and swap detection actually need.
LEGACY_FINGERPRINT_API = 0x094

LEGACY_PARAM_ACCESS = 0x300
LEGACY_PARAM_MAX = 133          # the real table ends here; see the api collision below
# Pre-25 puts commands in the SAME api space above the parameter table:
# param 255 lands on Persist Parameters. Never sweep past the table.
LEGACY_PARAM_TYPE = {0: "int32", 1: "uint32", 2: "float32", 3: "bool"}
LEGACY_PARAM_OK = 0             # status byte; anything else is a refusal

# Documented parameter frames, frames 2.1.0 nonPeriodicFrames, all at 25.0.0 and
# all rtr:true. READ_PARAMETER is apiClasses 15-22, a contiguous run of 128
# frames covering parameters 0-255 in pairs, so one base plus (param_id // 2)
# addresses every one. apiClass 19 is the fifth of the eight and covers 128-159;
# reading it as the whole read api is what limited this package to that band.
# GET_PARAMETER_n_TO_n+15_TYPES is apiClass 13, sixteen frames over 0-255.
#
# Both ranges are bounded by a write frame. One index past the last read is
# WRITE_PARAMETER_0_AND_1 at 0x02055C00, a write of the device's own CAN id, and
# one index past the last types frame is PARAMETER_WRITE at 0x02053800.
READ_PARAM_BASE = 0x02053C00        # apiClass 15, index 0 = params 0 and 1
READ_PARAM_LAST = 0x02055BC0        # apiClass 22, index 15 = params 254 and 255
GET_PARAM_TYPES = 0x02053400        # apiClass 13, index 0 = params 0 to 15
GET_PARAM_TYPES_LAST = 0x020537C0   # apiClass 13, index 15 = params 240 to 255
PARAM_ID_MAX = 255

# A read request is a REMOTE frame and its dlc has to request the eight bytes the
# reply carries. Measured rig-flex: dlc 0 draws silence on these classes.
READ_FRAME_DLC = 8
PERSIST = 0x0205FFC0
PERSIST_RESP = 0x02050500

PERSIST_MAGIC = 15011
REV_MFR = 5

# FRC arbitration id bits 28:24. A REV frame is not necessarily a motor
# controller: the PDH, Pneumatic Hub and Servo Hub carry the same manufacturer
# and default to low device ids, so filtering on manufacturer alone enumerates
# them as phantom SPARKs.
DEVICE_TYPE_MOTOR = 2
# REV's published telemetry scales, the same on both firmware generations. Not a
# calibration: nothing here has compared them against a meter.
VOLT_PER_COUNT = 0.0073260073260073
AMP_PER_COUNT = 0.0366300366300366

STATUS_0_API, STATUS_1_API, UNIQUE_ID_API = 0x2E0, 0x2E1, 0x2F0
LEGACY_STATUS_0_API = 0x060

# Frame layout keys on firmware version, per REV-spark-frames-2.1.0.json.
GEN_PRE25 = "pre25"
GEN_FW25 = "fw25+"
GENERATIONS = (GEN_PRE25, GEN_FW25)

# Unknown reads as 25+; shipping firmware has been 25+ on both products.
DEFAULT_GENERATION = GEN_FW25

# Pre-25 is api class 6 and has no UNIQUE_ID; 0x2F0 arrived at 25.0.0.
API_SETS = {
    GEN_FW25:  {"status_0": 0x2E0, "status_1": 0x2E1, "unique_id": 0x2F0},
    GEN_PRE25: {"status_0": 0x060, "status_1": 0x061, "unique_id": None},
}

KNOWN_PRODUCTS = ("sparkflex", "sparkmax")


# -- THE FLEET ASSUMPTION, AND ITS LIMITS -------------------------------------
#
# Within the scope of ArmBaseControl as it stands: every SPARK MAX is pre-25 and
# every SPARK Flex is firmware 25 or later. rig-max is eight MAX on 24.0.1 and
# rig-flex is eight Flex on 26.1.6, and no robot here mixes them.
#
# That is a fact about THIS FLEET, not about the protocol, and the distinction is
# load-bearing. Frame layout, the fault word, the parameter dialect, persistence
# and identify all key on FIRMWARE GENERATION. A SPARK MAX updated to 25.0.0
# broadcasts and is written exactly like a Flex; a Flex could in principle ship
# on older firmware. Conflating the two has already gone wrong in this repo more
# than once -- most expensively when a MAX layout was applied to a Flex and a
# healthy 13.7 V rail decoded as a fault bitfield, which wedged a base.
#
# So the code keeps keying on generation, read off the wire, and this pair is
# what the fleet is EXPECTED to be. `unexpected_generation` reports a violation
# rather than letting it pass silently.
#
# ANY CHANGE THAT RELIES ON "MAX MEANS PRE-25" MUST SAY SO AND MUST CHECK IT.
# If a controller is ever reflashed, this assumption is the first thing to
# revisit: search for EXPECTED_GENERATION to find every site that leans on it.
EXPECTED_GENERATION = {"sparkmax": GEN_PRE25, "sparkflex": GEN_FW25}


def unexpected_generation(controller_type, observed_generation):
    """A sentence describing a fleet-assumption violation, or None.

    Called wherever both the declared product and the observed generation are in
    hand. It is not an error: the driver reads the wire and works either way.
    It is a warning that the tree's simplifying assumption no longer holds, so
    anything written against it needs re-reading.
    """
    product = str(controller_type or "").strip().lower()
    want = EXPECTED_GENERATION.get(product)
    if want is None or observed_generation is None:
        return None
    got = normalise_generation(observed_generation)
    if got == want:
        return None
    return (f"this robot declares controller_type {product!r}, which this tree "
            f"assumes is {want}, and the bus reads {got}. The driver follows the "
            "WIRE and keeps working, but every note and default written against "
            "the assumption should be re-read -- see EXPECTED_GENERATION in "
            "admin.py. A MAX updated to 25.0.0 is written exactly like a "
            "Flex.")


def normalise_product(controller_type=None):
    """The canonical product name, for the SPARK_MODEL check that needs one.

    The product decides which model number STATUS_0 should announce at bit 54.
    It decides nothing about frame layout: use `normalise_generation` for that.
    Within this tree a product does IMPLY a generation -- see EXPECTED_GENERATION
    above -- but that is a fleet assumption and must never be used as a frame
    decision.
    """
    name = str(controller_type or "").strip().lower().replace("-", "").replace("_", "")
    return name if name in KNOWN_PRODUCTS else "sparkflex"


def normalise_generation(generation=None):
    """The canonical firmware generation. Refuses a product name outright.

    Passing "sparkmax" here used to select 0x060/0x061 and read a healthy 25+
    controller as a fault storm, so the wrong argument raises instead of
    resolving to a default. It keeps raising even though EXPECTED_GENERATION
    would now let a product be mapped: the map is a statement about this fleet,
    and a frame decision must come from the wire.
    """
    if generation is None:
        return DEFAULT_GENERATION
    name = str(generation).strip().lower().replace("-", "").replace("_", "")
    if name in GENERATIONS:
        return name
    if name in KNOWN_PRODUCTS:
        raise ValueError(
            f"{generation!r} is a product, and frame layout keys on firmware "
            f"generation. Pass one of {GENERATIONS}.")
    raise ValueError(f"unknown firmware generation {generation!r}; "
                     f"expected one of {GENERATIONS}")


def api_set(generation=None):
    """Status-frame API classes for a firmware generation. Defaults to 25+."""
    return API_SETS[normalise_generation(generation)]


def duplicate_detection_available(generation=None):
    """Whether two controllers on one id can be told apart on this generation.

    True on BOTH generations. It used to be 25+ only, because a
    duplicate was found by two different UNIQUE_ID broadcasts and pre-25
    broadcasts none -- so a clean-looking scan there meant "cannot tell" rather
    than "the bus is clean", and reporting the second is the confident-wrong-
    answer this package exists to avoid.

    Pre-25 now has its own route: the fingerprint at LEGACY_FINGERPRINT_API is
    REQUESTED per id rather than listened for, and two controllers sharing an id
    both answer. Same tell, different mechanism.
    """
    if normalise_generation(generation or DEFAULT_GENERATION) == GEN_PRE25:
        return True
    return api_set(generation).get("unique_id") is not None

WRITE_RESULT = {0: "Success", 1: "Invalid ID", 2: "Mismatched Type",
                3: "Access Mode", 4: "Invalid", 5: "Not Implemented"}
PARAM_TYPE = {0: "Unused", 1: "Int", 2: "Uint", 3: "Float", 4: "Boolean"}

# Parameter ids used by the audit. Full table: sparklib/data/rev_parameter_index.tsv
# 158..165 run Status 0..7, so `param - PARAM_STATUS_0_PERIOD` is the frame index
# LEGACY_SET_PERIOD wants on pre-25.
PARAM_STATUS_0_PERIOD = 158
PARAM_STATUS_1_PERIOD = 159
PARAM_CAN_ID = 0

# Parameters write_param refuses outright, rather than relying on callers to
# remember. Two reasons, and both end in a wheel that will not move.
#
# 50-53 are the data-port limit switches: a safety interlock equivalent to an
# e-stop, and a triggered limit is a state to respect rather than a fault to
# clear. Disabling or repolarising one is never a repair.
#
# 2 is Motor Type. CD 424550 is a set of SPARK MAXes that lit the right colour,
# answered the client and would not turn, because they were in brushed mode with
# brushless motors on them; the mode button reaches the same state on a three
# second press. The value is readable over CAN on both generations, so a wrong
# one shows up in `spark audit`; changing it is still an RHC2 job over USB-C.
PROTECTED_PARAMS = {
    # Parameter 0 is the device's own CAN id. A stray write here renames a motor
    # off the drivetrain, and the base loses that corner until someone finds it
    # with a serial scan and puts it back. It reads exactly like a controller
    # that "randomly factory reset", and it can hit several motors without
    # hitting the same one twice, which is what makes it hard to attribute.
    # set_can_id() is the supported route: addressed by serial, verified, burned.
    0: "CAN ID -- use `spark set-id --serial X --to N` instead",
    2: "Motor Type",
    50: "Limit Switch Fwd Polarity",
    51: "Limit Switch Rev Polarity",
    52: "Hard Limit Fwd En",
    53: "Hard Limit Rev En",
}


# Applied output below this is at rest: a hundredth of full output.
AT_REST_APPLIED = 0.01


# Where a pre-clear capture is written. Sticky bits are the only record a
# brownout or a reboot leaves, and clearing destroys them.
FAULT_RECORD_DIR = "records"


def classify_reset(sticky_warnings, sticky_faults=None):
    """Why a controller restarted, read from whichever list carries the bits.

    hasReset with brownout is a rail collapse and a POWER PATH fault. hasReset
    alone is an ordinary power cycle. Neither is not a restart.

    Both lists are searched because the generations file these two bits
    differently: warnings on 25+, faults on pre-25.
    """
    s = set(sticky_warnings or ()) | set(sticky_faults or ())
    if "brownout" in s and "hasReset" in s:
        return "brownout"
    if "brownout" in s:
        return "sagged-without-reset"
    if "hasReset" in s:
        return "power-cycle"
    return "none"


def write_fault_record(record, status=None, directory=None, stamp=None):
    """Persist a pre-clear capture and return the path.

    `spark clear` printed what it erased and kept nothing. The bits are gone the
    moment the frame goes out, so a printed record dies with the terminal -- a steer-stress run wiped hasReset on all eight of rig-flex before
    anyone read it, because any DriveTrain construction clears Flex stickies.
    """
    import datetime
    import json
    import os

    directory = directory or FAULT_RECORD_DIR
    os.makedirs(directory, exist_ok=True)
    # datetime, not time.strftime: tests replace this module's `time` with a
    # stub that has only time() and sleep().
    stamp = stamp or datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    path = os.path.join(directory, f"faults-{stamp}.json")

    out = {}
    for dev, r in (record or {}).items():
        sticky_warn = list(r.get("sticky_warnings") or [])
        entry = {k: list(v) if isinstance(v, (list, tuple, set)) else v
                 for k, v in r.items()}
        entry["reset_kind"] = classify_reset(sticky_warn,
                                             r.get("sticky_faults"))
        if status and dev in status:
            n = normalised_reading(status[dev])
            entry["volts"] = n.get("voltage_v")
            entry["amps"] = n.get("current_a")
            entry["generation"] = status[dev].get("generation")
        out[str(dev)] = entry

    with open(path, "w") as fh:
        json.dump({"captured": stamp, "controllers": out}, fh,
                  indent=2, sort_keys=True)
    return path


def driving_ids(status):
    """{dev: applied_output} for every controller in a reading that is driving.

    Takes an ALREADY-COLLECTED reading. It reads no bus and consumes nothing,
    which is the whole point: see require_at_rest for what happened when the
    check did its own listen inside the write primitive.
    """
    out = {}
    for dev, reading in (status or {}).items():
        applied = normalised_reading(reading).get("applied_output")
        if applied is not None and abs(applied) > AT_REST_APPLIED:
            out[dev] = applied
    return out


class MotorNotAtRestError(RuntimeError):
    """Raised before any frame goes out, when the target is driving.

    CD 346537: a firmware/API version mismatch left controllers running while
    the robot was disabled and e-stopped, nearly destroying an elevator.

    admin's own docstrings said a controller "stays disabled and cannot
    actuate while these run". That was a claim about what this tool SENDS, not
    about what the controller is doing. STATUS_0 carries applied output and this
    module already decodes it, so a session about to reconfigure a moving motor
    is detectable before the write.
    """


class ProtectedParameterError(RuntimeError):
    """Raised when a write targets a safety-interlock parameter."""


# CD 456184: a re-provisioning run that sent settings back-to-back faster than
# the device handled left one slot unwritten while every response said Success.
INTER_WRITE_S = 0.020

# CD 432129: a burn sent too soon after a write commits the pre-write value and
# both frames still answer Success. The field fix was at least 200 ms.
PERSIST_SETTLE_S = 0.200
WRITE_ATTEMPTS = 3

# A read may be retried where a write may not. A READ_PARAMETER request is a
# remote frame carrying no payload, so re-sending it is idempotent and there is
# no refusal to mistake for a timeout: the controller either answers or it does
# not. A write can be DECLINED, and CD 456184 is the case where retrying one
# hides the decline. Three attempts covers the post-burn window, where a
# controller stops answering for about two seconds (CD 432129).
READ_ATTEMPTS = 3

def _largest_gap_ms(times, start, end):
    """Longest silence in the window, counting the edges.

    A mean period cannot tell a hole from a slow cadence: eight frames spread
    evenly over four seconds and eight frames in the first 200 ms then nothing
    average identically. The largest gap separates them, and the window edges
    count because a controller that stopped mid-window is silent from its last
    frame to the end.
    """
    marks = [start] + sorted(times) + [end]
    return round(max(b - a for a, b in zip(marks, marks[1:])) * 1000.0, 1)


class SparkAdmin:
    """Read-mostly admin session on one SPARK bus. Never sends setpoints."""

    # Class-level so a session built without __init__ still paces its writes;
    # tests attach to a simulated bus that way. None means no write has gone
    # out yet, and the first one has nothing to be paced against.
    _last_write_at = None
    _last_write_resp_at = None
    controller_type = None
    # None lets collect_status read each controller's generation off the wire.
    generation = None

    def __init__(self, channel):
        self.channel = channel
        self.bus = can.Bus(interface="socketcan", channel=channel)

    def close(self):
        try:
            self.bus.shutdown()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()

    # -- listening ----------------------------------------------------------

    def _drain(self):
        while self.bus.recv(timeout=0) is not None:
            pass

    def _ask_read(self, arb, wait, attempts=READ_ATTEMPTS):
        """Send a read request and return the reply payload, retrying silence.

        Idempotent by construction: the request is a remote frame with no
        payload, so a repeat cannot change anything on the controller. Only
        SILENCE is retried, because a parameter read has no refusal to confuse
        with a timeout.
        """
        for _ in range(max(1, attempts)):
            self._drain()
            self.bus.send(can.Message(arbitration_id=arb, is_extended_id=True,
                                      is_remote_frame=True, dlc=READ_FRAME_DLC))
            d = self._await(arb, wait)
            if d is not None:
                return d
        return None

    def _await(self, arb, wait):
        end = time.time() + wait
        while time.time() < end:
            m = self.bus.recv(timeout=max(0.0, end - time.time()))
            if m is not None and m.arbitration_id == arb and not m.is_remote_frame:
                return bytes(m.data)
        return None

    def inventory(self, seconds=5.0, with_firmware=False,
                  with_fingerprint=False):
        """{device_id: {'serial','frames','periods_ms'}} for every REV controller.

        Passive by default, and that matters: a gated bus broadcasts nothing
        until something asks, so a silent reading here means "nobody spoke",
        not "nobody is there". Sending a query would wake the bus and destroy
        that distinction.

        `with_firmware=True` adds one GET_FIRMWARE round trip per device found
        and populates a 'firmware' key. The baseline firmware check in
        audit_problems reads that key, and without it the branch was dead for
        anyone calling the driver directly. It is opt-in because it makes this
        call active.

        `with_fingerprint=True` does the same for the PRE-25 identity at
        LEGACY_FINGERPRINT_API, filling 'serial' for a generation that broadcasts
        none. Opt-in for the same reason and no other: the fingerprint must be
        REQUESTED, and a request wakes the bus. On 25+ it is a no-op, because
        the serial arrives on its own in the passive pass above.
        """
        arrivals, serial = {}, {}
        start = time.time()
        end = start + seconds
        while time.time() < end:
            m = self.bus.recv(timeout=max(0.0, end - time.time()))
            if m is None:
                continue
            a = m.arbitration_id
            if (a >> 16) & 0xFF != REV_MFR:
                continue
            if (a >> 24) & 0x1F != DEVICE_TYPE_MOTOR:
                continue          # REV's PDH, Pneumatic Hub and Servo Hub share
                                  # the manufacturer field and default to low ids
            dev, api = a & 0x3F, (a >> 6) & 0x3FF
            at = getattr(m, "timestamp", None) or time.time()
            arrivals.setdefault(dev, {}).setdefault(api, []).append(at)
            if api == UNIQUE_ID_API:
                serial[dev] = bytes(m.data).hex().upper()
        if with_fingerprint:
            # Only for devices that already spoke, so this never turns a silent
            # bus into an apparently-populated one.
            for dev in sorted(arrivals):
                if serial.get(dev) is None:
                    serial[dev] = self.read_fingerprint(dev)
        out = {}
        for dev, apis in sorted(arrivals.items()):
            out[dev] = {
                "serial": serial.get(dev),
                "frames": sum(len(t) for t in apis.values()),
                "periods_ms": {api: round(seconds * 1000.0 / len(t), 1)
                               for api, t in sorted(apis.items()) if t},
                "window_ms": round(seconds * 1000.0, 1),
                "max_gap_ms": {api: _largest_gap_ms(t, start, end)
                               for api, t in sorted(apis.items()) if t},
                "last_seen_ms": {api: round((max(t) - start) * 1000.0, 1)
                                 for api, t in sorted(apis.items()) if t},
            }
        if with_firmware:
            for dev in out:
                try:
                    fw, _ = self.firmware(dev, wait=0.3)
                except Exception:
                    fw = None
                # None means the query went unanswered, which is NOT a match.
                # The baseline check must be able to tell those apart.
                out[dev]["firmware"] = fw
        return out

    def duplicates(self, seconds=6.0, generation=None, ids=None):
        """{device_id: [fingerprint,...]} for ids answered by more than one device.

        A duplicate is invisible to an id-based scan -- two controllers on one id
        look like one, and every addressed write reaches both while only the
        first reply is read. Two DIFFERENT identity payloads on a single id are
        the tell, and both generations can produce them:

            firmware 25+   listen for UNIQUE_ID broadcasts (api 0x2F0)
            pre-25         REQUEST the fingerprint at api 0x094 per id, and see
                           how many distinct replies come back

        The pre-25 route was impossible until because no serial was
        known to exist on that generation. It does: see LEGACY_FINGERPRINT_API.
        Requesting rather than listening is the whole difference -- pre-25
        broadcasts no identity, so nothing arrives unless you ask, and when two
        controllers share an id they both answer the one request.

        `ids` limits the pre-25 probe; without it every id in 1..62 is asked,
        which is what finds a duplicate at an id no config knows about.
        """
        if normalise_generation(generation or DEFAULT_GENERATION) == GEN_PRE25:
            return self._duplicates_pre25(ids=ids, wait=min(0.3, seconds))
        by_id = {}
        end = time.time() + seconds
        while time.time() < end:
            m = self.bus.recv(timeout=max(0.0, end - time.time()))
            if m is None:
                continue
            a = m.arbitration_id
            if ((a >> 16) & 0xFF != REV_MFR
                    or (a >> 24) & 0x1F != DEVICE_TYPE_MOTOR
                    or (a >> 6) & 0x3FF != UNIQUE_ID_API):
                continue
            by_id.setdefault(a & 0x3F, set()).add(bytes(m.data).hex().upper())
        return {d: sorted(s) for d, s in sorted(by_id.items()) if len(s) > 1}

    def read_fingerprint(self, dev, wait=0.3, all_replies=False):
        """The pre-25 per-device fingerprint at api 0x094, or None.

        With `all_replies`, collect EVERY reply within the window instead of the
        first. That is what makes duplicate detection work: one request to a
        shared id draws one answer per controller sitting on it.
        """
        arb = 0x02050000 | (LEGACY_FINGERPRINT_API << 6) | dev
        self._drain()
        self.bus.send(can.Message(arbitration_id=arb, data=b"",
                                  is_extended_id=True))
        seen, end = [], time.time() + wait
        while time.time() < end:
            m = self.bus.recv(timeout=max(0.0, end - time.time()))
            if m is None or m.arbitration_id != arb:
                continue
            # Discriminate by LENGTH, not by is_rx. The request and the reply
            # share an arbitration id -- as the period write and the status
            # broadcast do -- and the request is the zero-length one, so dropping
            # empty frames excludes our own echo exactly.
            #
            # is_rx would also work for that, and it is what this was written
            # with, but it conflates "our own echo" with "any locally generated
            # frame". SocketCAN flags every local frame as loopback, so a second
            # socket injecting a reply is invisible to an is_rx filter -- which
            # made this path impossible to exercise on real hardware without a
            # genuine second controller. Measured on rig-max: 124
            # injected frames arrived, every one with is_rx False.
            if not m.data:
                continue
            val = bytes(m.data).hex().upper()
            if val not in seen:
                seen.append(val)
            if not all_replies:
                break
        if all_replies:
            return seen
        return seen[0] if seen else None

    def _duplicates_pre25(self, ids=None, wait=0.3):
        """Ask every id for its fingerprint and count the distinct answers."""
        out = {}
        for dev in (sorted(ids) if ids else range(1, 63)):
            answers = self.read_fingerprint(dev, wait=wait, all_replies=True)
            if len(answers) > 1:
                out[dev] = sorted(answers)
        return out

    # -- queries ------------------------------------------------------------

    def firmware(self, dev, wait=0.5):
        """(version_string, hw_rev) or (None, None). A remote frame at dlc 0.

        The dlc matters and does not generalise: a parameter read on the same
        firmware needs dlc 8, and this one is answered at 0.
        """
        arb = GET_FIRMWARE | dev
        self._drain()
        self.bus.send(can.Message(arbitration_id=arb, is_extended_id=True,
                                  is_remote_frame=True, dlc=0))
        d = self._await(arb, wait)
        if d is None or len(d) < 6:
            return None, None
        return f"{d[0]}.{d[1]}.{int.from_bytes(d[2:4], 'big')}", d[5]

    def status_period_ms(self, dev, api=STATUS_1_API, seconds=4.0):
        """Observed broadcast period, or None if the frame is silent."""
        arb = 0x02050000 | (api << 6) | dev
        n, end = 0, time.time() + seconds
        while time.time() < end:
            m = self.bus.recv(timeout=max(0.0, end - time.time()))
            if m is not None and m.arbitration_id == arb:
                n += 1
        return round(seconds * 1000.0 / n, 1) if n else None

    # -- actions ------------------------------------------------------------

    def clear_faults(self, devs, verify_seconds=0.35, capture_seconds=0.12):
        """Clear latched faults, and report what was erased and what released.

        Returns {dev: {"sticky_faults", "sticky_warnings", "faults", "warnings",
        "cleared"}}, where the sticky lists are what the controller held BEFORE
        the frame went out and "cleared" says whether the fault is gone now.

        The read comes first because the clear destroys it. PROBE-LOG section 11
        measured that any host frame wakes a gated bus and erases nothing, so a
        silent fleet can be read before it is cleared -- which is what makes a
        hardware latch (regenerates, needs an RMA) distinguishable from a sticky
        bit that genuinely released.
        """
        devs = list(devs)
        if not devs:
            return {}

        before = collect_status(self.bus, seconds=capture_seconds,
                                generation=self.generation)
        if not any(d in before for d in devs):
            # A gated controller answers requests but broadcasts nothing, so a
            # passive capture reads an empty bus. One read wakes the whole bus
            # and destroys nothing; only pay for it when the bus is silent.
            self.firmware(devs[0], wait=0.1)
            before = collect_status(self.bus, seconds=capture_seconds,
                                    generation=self.generation)

        for d in devs:
            self.bus.send(can.Message(arbitration_id=CLEAR_FAULTS | d, data=[],
                                      is_extended_id=True))
            time.sleep(0.05)

        after = collect_status(self.bus, seconds=verify_seconds,
                               generation=self.generation)

        # normalised_reading, not the raw status1: on a pre-25 bus the faults are
        # in LEGACY_STATUS_0 and this recorded nothing while reporting cleared.
        record = {}
        for d in devs:
            was = normalised_reading(before.get(d))
            now = normalised_reading(after.get(d))
            still = list(now.get("faults") or []) + list(now.get("sticky_faults") or [])
            record[d] = {
                "sticky_faults": list(was.get("sticky_faults") or []),
                "sticky_warnings": list(was.get("sticky_warnings") or []),
                "faults": list(now.get("faults") or []),
                "warnings": list(now.get("warnings") or []),
                "cleared": not still,
                "answered": d in after,
            }
        return record

    def identify(self, serial_hex):
        """Blink one controller's LED on FIRMWARE 25+, addressed by serial.

        Broadcast on device 0 with the serial in the payload, so it reaches the
        controller you chose even where two share a CAN id. That model does not
        exist on pre-25 -- use identify_by_id there, and see its docstring for
        why sending this form to a 24.0.1 controller looks like success and does
        nothing.
        """
        self.bus.send(can.Message(arbitration_id=IDENTIFY_UNIQUE,
                                  data=bytes.fromhex(serial_hex),
                                  is_extended_id=True))

    def identify_by_id(self, dev):
        """Blink one controller's LED on PRE-25, addressed by CAN id.

        Same api -- apiClass 7 index 6, Identify Unique SPARK, versionImplemented
        1.5.0 -- and a completely different addressing model:

            firmware 25+   IDENTIFY_UNIQUE, device 0, 4-byte serial payload
            pre-25         IDENTIFY_UNIQUE | dev, payload EMPTY

        Recovered on rig-max, by capturing REV Hardware Client while
        an operator pressed its LED button for controllers 1, 2 and 3. Exactly
        three frames appeared in 9831 lines of candump -- 02051D81, 02051D82 and
        02051D83, all DLC 0 -- and reproducing them from this driver blinked the
        same controllers. Until then `spark identify` sent the 25+ form at a
        24.0.1 fleet, which is silently ignored, so the command reported that it
        had sent and nothing ever happened.

        The consequence for identity is worth stating: this form takes NO serial,
        so it cannot be used to confirm what the per-device value at api 0x094
        is. The two questions are unrelated on this generation, and that is
        measured rather than argued -- the firmware-25 broadcast carrying a
        controller's own 0x094 fingerprint blinks nothing, with the addressed
        form firing either side of it on the same LED. docs/SPARKMAX-BRINGUP.md
        has the run.

        Fire and forget, like every pre-25 command: nothing is acknowledged, so
        the only confirmation is an operator watching the LED.
        """
        self.bus.send(can.Message(arbitration_id=IDENTIFY_UNIQUE | dev,
                                  data=b"", is_extended_id=True))

    def read_param_pair(self, dev, param_id, wait=0.5,
                        attempts=READ_ATTEMPTS):
        """Read a parameter over the documented READ_PARAMETER frame.

        A read api, so it cannot be mistaken for a write however the payload
        comes out -- which is the whole hazard `read_param` carries. Frames are
        addressed per PAIR and there are 128 of them, covering the whole 0-255
        table. Outside that range this returns None and sends nothing.

        The request is a REMOTE frame carrying dlc=8. Both halves are required:
        a zero-length data frame and a remote frame with dlc=0 both draw silence
        on firmware 26.1.6.

        Returns {"first_id", "first", "second_id", "second"} on a reply, both
        values raw uint32.
        """
        if not 0 <= param_id <= PARAM_ID_MAX:
            return None                  # index 128 is WRITE_PARAMETER_0_AND_1
        index = param_id // 2
        arb = (READ_PARAM_BASE + (index << 6)) | dev
        d = self._ask_read(arb, wait, attempts=attempts)
        if d is None or len(d) < 8:
            return None
        first = index * 2
        return {"first_id": first,
                "first": int.from_bytes(d[0:4], "little"),
                "second_id": first + 1,
                "second": int.from_bytes(d[4:8], "little")}

    def read_param_value(self, dev, param_id, wait=0.5,
                         attempts=READ_ATTEMPTS):
        """One parameter's raw uint32, or None. Wraps the pair frame."""
        pair = self.read_param_pair(dev, param_id, wait=wait,
                                    attempts=attempts)
        if pair is None:
            return None
        return pair["second"] if param_id % 2 else pair["first"]

    def param_types(self, dev, start_id=0, wait=0.5,
                    attempts=READ_ATTEMPTS):
        """Which parameter ids exist on this device, sixteen types per frame.

        apiClass 13, sixteen frames over the whole 0-255 range. Type 0 is
        Unused, so the reply says directly which ids the firmware implements.
        Sent as a remote frame with dlc=8, for the reason read_param_pair gives.
        Returns {param_id: type_name}.
        """
        if not 0 <= start_id <= PARAM_ID_MAX:
            return None                          # index 16 is PARAMETER_WRITE
        index = start_id // 16
        arb = (GET_PARAM_TYPES + (index << 6)) | dev
        d = self._ask_read(arb, wait, attempts=attempts)
        if d is None or len(d) < 8:
            return None
        base = index * 16
        out = {}
        for i in range(16):
            nib = (d[i // 2] >> (4 * (i % 2))) & 0x0F
            out[base + i] = PARAM_TYPE.get(nib, nib)
        return out

    def require_at_rest(self, dev, seconds=0.05):
        """Refuse if `dev` is driving. Listens only; sends nothing.

        A driving controller is receiving an enable heartbeat and therefore
        broadcasting, so a passive listen catches it. The converse does NOT
        hold: SPARKs are gated and a silent bus is the normal state, so seeing
        nothing means "no evidence of motion", not "proven at rest". That is the
        honest limit of a check that must not transmit -- the caller asked to
        reconfigure, and waking the bus to find out would itself be a write to
        the timeline this is protecting.
        """
        reading = collect_status(self.bus, seconds=seconds)
        r = (reading or {}).get(dev)
        if not r:
            return None                   # nothing heard; cannot prove motion
        applied = normalised_reading(r).get("applied_output")
        if applied is not None and abs(applied) > AT_REST_APPLIED:
            raise MotorNotAtRestError(
                f"id {dev} is driving: applied output {applied:+.3f}. "
                "Reconfiguring a moving motor is how CD 346537 nearly destroyed "
                "an elevator. Stop the mechanism, then retry.")
        return applied

    def write_param(self, dev, param_id, raw_u32, wait=1.5,
                    attempts=WRITE_ATTEMPTS):
        """Write one parameter to RAM. Returns the device's response dict.

        Refuses the hard-limit parameters: they are the data-port safety
        interlock and disabling them is never a repair.

        CD 456184 is the whole design here: "you need to have retries in your
        code for basically every setting, and also read the setting back to
        ensure that it's been set correctly", and the cause of that thread was a
        routine that "sent settings back-to-back faster than the device handled".
        So an unanswered write is retried, consecutive writes are paced, and the
        echoed value is compared against what was asked for -- reported as
        `verified`, because a device answering Success with a different value in
        d[2:6] is the failure the thread describes.
        """
        if param_id in PROTECTED_PARAMS:
            raise ProtectedParameterError(
                f"parameter {param_id} ({PROTECTED_PARAMS[param_id]}) is a safety "
                "interlock and must not be written by tooling")
        payload = bytes([param_id]) + struct.pack("<I", raw_u32)
        d, sent = None, 0
        for _ in range(max(1, attempts)):
            self._pace_write()
            self._drain()
            self.bus.send(can.Message(arbitration_id=PARAM_WRITE | dev,
                                      data=payload, is_extended_id=True))
            sent += 1
            d = self._await(PARAM_WRITE_RESP | dev, wait)
            if d is not None and len(d) >= 7:
                break
        if d is None or len(d) < 7:
            return None
        self._last_write_resp_at = time.time()
        echoed = int.from_bytes(d[2:6], "little")
        return {"param_id": d[0], "type": PARAM_TYPE.get(d[1], d[1]),
                "value": echoed,
                "requested": raw_u32,
                "verified": echoed == raw_u32 and d[6] == 0,
                "attempts": sent,
                "result": d[6], "result_text": WRITE_RESULT.get(d[6], f"code {d[6]}")}

    def set_legacy_status_period(self, dev, frame_index, period_ms):
        """Set one pre-25 status period. Returns nothing: the device never answers.

        The MAX write dialect. `write_param` is the 25+ one and this firmware
        ignores it, so anything that reconfigures a pre-25 controller has to come
        through here and verify by MEASURING the cadence -- there is no echo and
        no result code to check.

        Volatile, like the 25+ periods, and the pre-25 burn does not change
        that. Api 0x072 commits the parameter table and was measured NOT to
        reach the periods: on rig-max, id 3 had 0x060 set to a
        distinctive 77 ms, was burned, was accepted with 0x00, and read 10.0 ms
        after a rail cycle, as did all seven others.

        Nothing on the controller restores them: apply_boot_config re-sends the
        table when the driver constructs or reconfigures a handler, so a rail
        cycle leaves REV's defaults on the wire until that happens or
        `spark throttle` is run.
        """
        if not LEGACY_FRAME_MIN <= frame_index <= LEGACY_FRAME_MAX:
            raise ValueError(
                f"frame_index must be {LEGACY_FRAME_MIN}..{LEGACY_FRAME_MAX}, "
                f"got {frame_index}")
        if not 0 <= period_ms <= 0xFFFF:
            raise ValueError(f"period_ms must be 0..65535, got {period_ms}")
        self._pace_write()
        self.bus.send(can.Message(
            arbitration_id=LEGACY_SET_PERIOD + (frame_index << 6) + dev,
            data=bytes([period_ms & 0xFF, (period_ms >> 8) & 0xFF]),
            is_extended_id=True))

    def _legacy_param_arb(self, dev, param_id):
        if not 0 <= param_id <= LEGACY_PARAM_MAX:
            raise ValueError(
                f"parameter {param_id} is outside the pre-25 table (0.."
                f"{LEGACY_PARAM_MAX}). Higher ids collide with the command space "
                "in the same api range: 255 lands on Persist Parameters.")
        return (0x02050000 | ((LEGACY_PARAM_ACCESS | param_id) << 6) | dev)

    def _await_legacy_param(self, arb, wait):
        deadline = time.time() + wait
        while time.time() < deadline:
            m = self.bus.recv(0.1)
            if (m is not None and m.arbitration_id == arb and m.is_rx
                    and len(m.data) >= 6):
                d = bytes(m.data)
                return {"raw": int.from_bytes(d[0:4], "little"),
                        "type": LEGACY_PARAM_TYPE.get(d[4], d[4]),
                        "status": d[5],
                        "ok": d[5] == LEGACY_PARAM_OK}
        return None

    def read_legacy_param(self, dev, param_id, wait=1.0):
        """Read one parameter from a pre-25 controller. None if it never answers.

        The capability 25+ does not have: on that generation configuration can
        only be inferred from what a controller broadcasts.

        `ok` is False when the device replies but refuses. The status-period ids
        do refuse, because periods are not parameters on this generation and
        move on LEGACY_SET_PERIOD instead.
        """
        arb = self._legacy_param_arb(dev, param_id)
        self._drain()
        self.bus.send(can.Message(arbitration_id=arb, data=b"",
                                  is_extended_id=True))
        return self._await_legacy_param(arb, wait)

    def write_legacy_param(self, dev, param_id, raw_u32, type_tag=1, wait=1.0):
        """Write one parameter to a pre-25 controller's RAM and read the echo.

        Refuses PROTECTED_PARAMS, as write_param does: parameter 0 is the CAN id
        and a bad write costs a controller its identity on a bus with no serials.

        Writes to RAM. This frame commits nothing to flash, and the 25+ PERSIST
        frame is versionImplemented 25.0.0, so a motor-rail power cycle is the
        backstop under every write made here.

        Persistence is nevertheless REACHABLE on this generation, which it was
        documented not to be until. Api 0x072, arbitration id
        0x02051C80 | id, carrying the magic 15011 little-endian -- the same
        PERSIST_MAGIC the 25+ frame uses -- replies 0x00 on the request's own id
        and commits parameters 0-133 to flash on firmware 24.0.1. Measured on
        rig-max, against a no-burn control that reverted while the
        burned controllers held their value across a rail cycle. It does NOT
        reach the status periods, measured on a controller burned at a
        distinctive period that read the throttled value back afterwards.

        Nothing in this package sends that frame and no CLI command exposes it,
        so what this method writes stays volatile in practice. The claim is
        pre25.burn_flash_api in provenance.
        """
        if param_id in PROTECTED_PARAMS:
            raise ProtectedParameterError(
                f"parameter {param_id} ({PROTECTED_PARAMS[param_id]}) is a safety "
                "interlock and must not be written by tooling")
        arb = self._legacy_param_arb(dev, param_id)
        self._pace_write()
        self._drain()
        self.bus.send(can.Message(
            arbitration_id=arb,
            data=int(raw_u32).to_bytes(4, "little") + bytes([type_tag]),
            is_extended_id=True))
        echo = self._await_legacy_param(arb, wait)
        if echo is not None:
            echo["requested"] = int(raw_u32)
            echo["verified"] = echo["ok"] and echo["raw"] == int(raw_u32)
        return echo

    def _pace_write(self):
        """Hold INTER_WRITE_S between parameter frames (CD 456184)."""
        if self._last_write_at is not None:
            gap = time.time() - self._last_write_at
            if gap < INTER_WRITE_S:
                time.sleep(INTER_WRITE_S - gap)
        self._last_write_at = time.time()

    def persist(self, dev, wait=4.0, settle=PERSIST_SETTLE_S, confirm=None):
        """Commit RAM parameters to flash. Returns the result code, 0 = success.

        Two field behaviours shape this. CD 432129: "burning configuration to
        flash was an issue if it was not delayed long enough after sending
        configuration messages" -- so the burn waits out `settle` from the last
        write response, or flash takes the pre-write value while both frames
        answer Success. And the controller stops answering for about two seconds
        after a burn, so a lost PERSIST_RESP is not evidence the burn failed;
        that case returns None, which callers must not read as a failure.
        """
        # Two different events, and only one of them is observable.
        #
        # The write response is an ACK that the device took the frame -- it
        # arrives in about a millisecond and says nothing about flash. What
        # CD 432129 measured is that a parameter written less than ~200 ms
        # before the burn is committed at its PRE-WRITE value, with both frames
        # answering Success. Nothing on the bus marks that transition, so there
        # is no ACK to wait on and the delay is the only instrument.
        #
        # `confirm` closes the half that IS observable: pass a callable that
        # returns True once the new value shows up on the wire (the STATUS_1
        # cadence changing, say) and the burn waits for the real evidence
        # instead of assuming. The floor still applies, because a value visible
        # in RAM is not the same as one the flash controller is ready to take.
        if self._last_write_resp_at is not None:
            deadline = self._last_write_resp_at + settle
            while time.time() < deadline:
                time.sleep(min(0.01, max(0.0, deadline - time.time())))
            if confirm is not None:
                give_up = time.time() + wait
                while time.time() < give_up and not confirm():
                    time.sleep(0.02)
        self._drain()
        self.bus.send(can.Message(arbitration_id=PERSIST | dev,
                                  data=struct.pack("<H", PERSIST_MAGIC),
                                  is_extended_id=True))
        d = self._await(PERSIST_RESP | dev, wait)
        return None if d is None else d[0]

    def set_can_id(self, current_id, serial_hex, new_id, settle=1.5):
        """Reassign a controller's CAN id, addressed by serial, and commit it.

        Returns True when the controller answers on the new id and the burn
        succeeded, False when the move was refused outright, and None when the
        move landed but the flash burn did not confirm -- which means the id is
        live now and gone at the next power-up.

        Both fields are required and were verified against hardware: the
        arbitration id must carry the device's CURRENT id, and the serial must
        match that device in the byte order it broadcasts on api 0x2F0. A wrong
        serial, a wrong current id, a broadcast id, or reversed serial bytes all
        do nothing -- which is what makes this safe to aim at one of two
        controllers sharing an id.
        """
        if not 1 <= new_id <= 62:
            raise ValueError(f"CAN id must be 1..62, got {new_id}")
        payload = bytes.fromhex(serial_hex) + bytes([new_id])
        self.bus.send(can.Message(arbitration_id=SET_CAN_ID | current_id,
                                  data=payload, is_extended_id=True))
        time.sleep(settle)

        # Firmware sends no NACK for a refused SET_CAN_ID, so silence and success
        # look identical on the wire. A controller in recovery mode, or one whose
        # id is locked, simply ignores it. Look at who is broadcasting instead.
        inv = self.inventory(seconds=max(0.5, settle / 2))
        moved = any(i.get("serial", "").upper() == serial_hex.upper()
                    for d, i in inv.items() if d == new_id)
        if not moved:
            return False

        # The move landed in RAM. Flash is a separate, independently failable
        # step, and CD 425589 is what happens without it: "every time I reconnect
        # to the motor controller, the ID resets". A renumbering that is not
        # burned has not renumbered anything -- it has armed the same collision
        # for the next power cycle.
        code = self.persist(new_id)
        return True if code == 0 else None


def float_bits(value):
    """Encode a python float as the uint32 PARAMETER_WRITE carries."""
    return struct.unpack("<I", struct.pack("<f", float(value)))[0]


# STATUS_0 / STATUS_1 decoding, bit positions straight from REV-Specs 2.1.0.
# The legacy decode in controller.py reads faults from STATUS_0 bytes 2:6,
# which on 25.x+ firmware are bus voltage, output current and motor temperature
# -- so it reports faults that are really telemetry. These are the real layouts.

_FAULT_BITS = ("other", "motorType", "sensor", "can",
               "temperature", "gateDriver", "escEeprom", "firmware")
_WARNING_BITS = ("brownout", "overcurrent", "escEeprom", "extEeprom",
                 "sensor", "stall", "hasReset", "other")


# The pre-25 fault word: sixteen bits, and a different ordering from the 2025+
# table above. The two are not interchangeable in either direction.
#
# Names keep this package's camelCase. REV's own are kBrownout, kOvercurrent,
# kIWDTReset, kMotorFault, kSensorFault, kStall, kEEPROMCRC, kCANTX, kCANRX,
# kHasReset, kDRVFault, kOtherFault, kSoftLimitFwd/Rev, kHardLimitFwd/Rev; REVLib
# 1.1.5 and earlier named bit 2 kOvervoltage without moving it.
# Sourcing: the pre25.fault_bit_order claim in provenance.
_LEGACY_FAULT_BITS = ("brownout", "overcurrent", "iwdtReset", "motorType",
                      "sensor", "stall", "eepromCrc", "canTx",
                      "canRx", "hasReset", "gateDriver", "other",
                      "softLimitFwd", "softLimitRev",
                      "hardLimitFwd", "hardLimitRev")


def _bits(word, names):
    """Set bit names. A bit past the end of `names` becomes `bit12` rather than
    being dropped, so an unnamed fault still reaches the caller."""
    out = []
    for i in range(max(word.bit_length(), len(names))):
        if word & (1 << i):
            out.append(names[i] if i < len(names) else f"bit{i}")
    return out


# Raw full scale for the STATUS_0 telemetry fields, from the field widths above.
# A field sitting exactly here is the top of its range, not a measurement.
_S0_FULL_SCALE_12 = 0x0FFF
_S0_FULL_SCALE_8 = 0xFF


def status_0_implausible(r, raw_v, raw_a):
    """Decoded keys whose value is out of band; empty when the frame is a reading.

    A transmitter that stops driving the bus leaves it recessive, so an all-ones
    payload saturates every telemetry field at once: 30 V, 150 A and 255 C. Each
    one decodes to a number, and without this list the reading prints as fact.
    Model identity is judged by status_problems against base.controller_type;
    REV-Specs records only that 1 is a SPARK Flex, not the whole set.
    """
    why = []
    if raw_v >= _S0_FULL_SCALE_12:
        why.append("voltage_v")
    if raw_a >= _S0_FULL_SCALE_12:
        why.append("current_a")
    if r["motor_temp_c"] >= _S0_FULL_SCALE_8:
        why.append("motor_temp_c")
    # Applied output and output current arrive in the same eight bytes and are
    # decoded as two independent numbers. Exactly zero applied while real
    # current flows is not a state a working controller reaches: it stopped
    # following its commanded output while the mechanism kept loading it
    # (CD 477176, fixed only by restarting robot code). The pair is the
    # evidence; neither field alone says anything.
    if (not why
            and abs(r["applied_output"]) < _DEAD_APPLIED
            and r["current_a"] >= _LIVE_CURRENT_A):
        why.append("applied_output")   # only when both fields are readings
    return why


def describe_implausible(keys):
    """One line naming the pegged fields, shared by the audit and the CLI."""
    return (", ".join(keys) + " pegged at the top of "
            + ("its field" if len(keys) == 1 else "their fields"))


def decode_status_0(data):
    """Applied output, bus voltage, output current, temperature and flags.

    `implausible` lists why the reading is not a measurement; empty when it is.
    """
    if data is None or len(data) < 8:
        return None
    d = bytes(data)
    raw_v = d[2] | ((d[3] & 0x0F) << 8)
    raw_a = (d[3] >> 4) | (d[4] << 4)
    r = {
        "applied_output": int.from_bytes(d[0:2], "little", signed=True)
                          * 3.082369457075716e-05,
        "voltage_v": raw_v * VOLT_PER_COUNT,
        "current_a": raw_a * AMP_PER_COUNT,
        "motor_temp_c": d[5],
        "hard_forward_limit": bool(d[6] & 0x01),
        "hard_reverse_limit": bool(d[6] & 0x02),
        "soft_forward_limit": bool(d[6] & 0x04),
        "soft_reverse_limit": bool(d[6] & 0x08),
        "inverted": bool(d[6] & 0x10),
        "primary_heartbeat_lock": bool(d[6] & 0x20),
        "spark_model": ((d[6] >> 6) & 0x03) | ((d[7] & 0x03) << 2),
    }
    r["implausible"] = status_0_implausible(r, raw_v, raw_a)
    return r


# What 0x060 carries on firmware 25+: every signal pinned, all faults set.
LEGACY_BEACON_PAYLOAD = b"\x00\x00\xff\xff\xff\xff\x00\x00"


def decode_legacy_status_0(data):
    """LEGACY_STATUS_0 (api 0x060), and whether it carries readings at all.

    frames 2.1.0 gives three signals: APPLIED_OUTPUT at bit 0 (16 bits),
    FAULTS_AND_STICKY_FAULTS at bit 16 (ONE 32-bit field), OTHER_SIGNALS at bit
    48. On firmware 25+ all three are pinned, so the frame is a presence beacon
    and `is_beacon` says to read nothing else out of it.

    The 16/16 split of the fault word into active and sticky halves is the
    pre-25 layout and is not in frames 2.1.0, which names one 32-bit field. The
    full word is returned alongside so a caller need not rely on the split.
    """
    if data is None or len(data) < 8:
        return None
    d = bytes(data)
    combined = int.from_bytes(d[2:6], "little")
    return {
        "applied_output": int.from_bytes(d[0:2], "little", signed=True)
        * 3.082369457075716e-05,
        "faults_and_sticky": combined,
        "active_faults": combined & 0xFFFF,
        "sticky_faults": (combined >> 16) & 0xFFFF,
        "other_signals": int.from_bytes(d[6:8], "little"),
        # Byte 6 bit 1. The only bit of that byte placed by measurement rather
        # than by reading it off a spec; the rest stay undecoded on purpose.
        "inverted": bool(d[6] & LEGACY_OTHER_INVERTED),
        "is_beacon": d == LEGACY_BEACON_PAYLOAD,
    }


def legacy_frame_is_beacon(data):
    """True when a 0x060 payload is the firmware-25+ presence beacon."""
    return data is not None and bytes(data) == LEGACY_BEACON_PAYLOAD


# Byte 6 of LEGACY_STATUS_0 is REV's OTHER_SIGNALS field. Its individual bit
# meanings are NOT established on this generation, so it is handled as a whole
# byte rather than decoded into named flags. The one value seen on a fleet that
# drives is 0x10; 0x54 was seen on a fleet that took every setpoint and applied
# nothing. See docs/SPARKMAX-BRINGUP.md for the measurement.
LEGACY_OTHER_SIGNALS_DRIVING = 0x10

# Byte 6 bit 1 is Inverted, parameter 45. Measured by writing the parameter and
# watching the bit follow it, with the write confirmed by readback. This package
# previously read Inverted at bit 4, which is wrong: bit 4 is set on every
# controller of a fleet whose parameter 45 is 0. Eight other parameters were
# written the same way and none of them appears in this byte.
LEGACY_OTHER_INVERTED = 0x02

LEGACY_OTHER_SIGNALS_REMEDY = (
    "power-cycle the motor rail. No CAN command has been found that clears "
    "this, and the controller reports no fault while it holds")


def legacy_other_signals(status0_raw):
    """Byte 6 of a pre-25 LEGACY_STATUS_0 frame, or None if there is no frame."""
    if status0_raw is None or len(status0_raw) < 8:
        return None
    return bytes(status0_raw)[6]


def legacy_other_signals_problem(status0_raw):
    """A reason string when byte 6 is not the value a driving fleet reports.

    This is the only PASSIVE signal that has separated a pre-25 fleet which
    drives from one which does not. Every fault field reads clean in both
    states, so a gate that checks faults alone cannot tell them apart and will
    clear a fleet that moves nothing.

    Deliberately reports the byte rather than naming bits. The decode that named
    them is refuted: the field it called `spark_model` changed value across a
    power cycle, and a model cannot change.

    Returns None when there is no frame, so absence of telemetry is somebody
    else's finding to report and not silently this one.
    """
    other = legacy_other_signals(status0_raw)
    if other is None or other == LEGACY_OTHER_SIGNALS_DRIVING:
        return None
    return (f"STATUS_0 byte 6 is 0x{other:02X}, not the "
            f"0x{LEGACY_OTHER_SIGNALS_DRIVING:02X} a driving fleet reports; "
            f"this controller may take setpoints and apply nothing. FIX: "
            f"{LEGACY_OTHER_SIGNALS_REMEDY}")


def decode_legacy_status_1(data):
    """Pre-25 Periodic Status 1 (api 0x061): telemetry, and no fault word.

    REV's SPARK MAX control-interfaces page: "Motor Velocity 32-bit IEEE
    floating-point... Motor Temperature 8-bit unsigned... Motor Voltage 12-bit
    fixed-point... This is the input voltage to the controller... Motor Current
    12-bit fixed-point". Reading it with decode_status_1 scores a velocity as a
    fault byte.
    """
    if data is None or len(data) < 8:
        return None
    d = bytes(data)
    raw_v = d[5] | ((d[6] & 0x0F) << 8)
    raw_a = (d[6] >> 4) | (d[7] << 4)
    return {
        "velocity_rpm": struct.unpack("<f", d[0:4])[0],
        "motor_temp_c": d[4],
        "voltage_v": raw_v * VOLT_PER_COUNT,
        "current_a": raw_a * AMP_PER_COUNT,
    }


def decode_status_1(data):
    """Active and sticky faults and warnings. This is where errors actually live."""
    if data is None or len(data) < 7:
        return None
    d = bytes(data)
    return {
        "faults": _bits(d[0], _FAULT_BITS),
        "warnings": _bits(d[2], _WARNING_BITS),
        "sticky_faults": _bits(d[3], _FAULT_BITS),
        "sticky_warnings": _bits(d[5], _WARNING_BITS),
        "is_follower": bool(d[6] & 0x01),
    }


def generation_from_apis(apis_seen, legacy_payload=None):
    """Which frame generation a controller speaks, from what it broadcast.

    Any 0x2E0-0x2E9 or 0x2F0 frame is implemented at 25.0.0 and settles it. A
    controller heard only on 0x060 is read by its payload: the pinned beacon
    means 25+ with its status frames switched off, and anything else is pre-25.
    Returns None when nothing was heard, so a caller can say so.
    """
    apis = set(apis_seen or ())
    if any(a == UNIQUE_ID_API or 0x2E0 <= a <= 0x2E9 for a in apis):
        return GEN_FW25
    if apis & {0x061, 0x062}:
        return GEN_PRE25          # 25+ retired api class 6 past index 0
    if LEGACY_STATUS_0_API in apis:
        return GEN_FW25 if legacy_frame_is_beacon(legacy_payload) else GEN_PRE25
    return None


def dominant_generation(status, default=None):
    """The generation most controllers on this bus reported.

    A mixed fleet has no single answer, so the majority is what a bus-wide
    message can honestly say. Ties and an empty reading fall back.
    """
    seen = [r.get("generation") for r in (status or {}).values()
            if r.get("generation")]
    if not seen:
        return normalise_generation(default)
    counts = {g: seen.count(g) for g in set(seen)}
    top = max(counts.values())
    winners = sorted(g for g, n in counts.items() if n == top)
    return winners[0] if len(winners) == 1 else normalise_generation(default)


def observed_generation(status):
    """The generation this bus actually reported, or None if it reported none.

    dominant_generation falls back to its `default` on an empty reading, and
    that fallback is indistinguishable from an observation at the call site. A
    silent bus was reported as reading firmware 25+ on rig-max.
    """
    if not any(r.get("generation") for r in (status or {}).values()):
        return None
    return dominant_generation(status)


def collect_status(bus_obj, seconds=4.0, generation=None):
    """{device_id: {'status0':..., 'status1':..., 'generation':...}}.

    Listens on both generations at once and decides per device, so a mixed-
    firmware bus reads correctly. `generation` pins the decode when a caller
    already knows it; leaving it None lets the wire decide.
    """
    pinned = normalise_generation(generation) if generation is not None else None
    raw, seen, varied = {}, {}, {}
    watched = {LEGACY_STATUS_0_API, API_SETS[GEN_PRE25]["status_1"],
               STATUS_0_API, STATUS_1_API, UNIQUE_ID_API}
    end = time.time() + seconds
    while time.time() < end:
        m = bus_obj.recv(timeout=max(0.0, end - time.time()))
        if m is None:
            continue
        a = m.arbitration_id
        if (a >> 16) & 0xFF != REV_MFR:
            continue
        if (a >> 24) & 0x1F != DEVICE_TYPE_MOTOR:
            continue              # a PDH on a SPARK's id is not motor telemetry
        dev, api = a & 0x3F, (a >> 6) & 0x3FF
        if api in watched:
            data = bytes(m.data)
            raw.setdefault(dev, {})[api] = data
            if api == STATUS_0_API and len(data) >= 7:
                # A current that never moves while applied output does is the
                # telemetry failing, not the mechanism loading up (CD 373283:
                # three teams saw it pinned at 72, 85 and 125 A unloaded). One
                # frame cannot show it; the window can.
                d0 = decode_status_0(data)
                if d0:
                    v = varied.setdefault(dev, {"amps": set(), "applied": set()})
                    v["amps"].add(round(d0["current_a"], 3))
                    v["applied"].add(round(d0["applied_output"], 3))
            if api == STATUS_1_API and len(data) >= 7:
                # Keeping only the last frame loses every fault that recovered
                # inside the window. A thermal foldback clears itself with no
                # sticky copy (CD 460577: eleven bits set for one loop cycle),
                # so a controller folding back twice a minute reads identically
                # to one that never has. OR the bytes as they go past.
                acc = seen.setdefault(dev, [0, 0])
                acc[0] |= data[0]
                acc[1] |= data[2]
    out = {}
    for dev, v in sorted(raw.items()):
        legacy_raw = v.get(LEGACY_STATUS_0_API)
        # observed says the wire settled it. Falling back to the default is an
        # assumption, and a consumer that reports faults should say which it is.
        heard = generation_from_apis(v.keys(), legacy_raw)
        gen = pinned or heard or DEFAULT_GENERATION
        observed = bool(pinned or heard)
        if gen == GEN_PRE25:
            out[dev] = {
                "status0": decode_legacy_status_0(legacy_raw),
                "status1": decode_legacy_status_1(
                    v.get(API_SETS[GEN_PRE25]["status_1"])),
                "generation": gen, "generation_observed": observed}
            continue
        s1 = decode_status_1(v.get(STATUS_1_API))
        if s1 is not None and dev in seen:
            fbits, wbits = seen[dev]
            s1["faults_seen"] = _bits(fbits, _FAULT_BITS)
            s1["warnings_seen"] = _bits(wbits, _WARNING_BITS)
        s0 = decode_status_0(v.get(STATUS_0_API))
        vv = varied.get(dev)
        if s0 is not None and vv and len(vv["applied"]) > 1 and len(vv["amps"]) == 1:
            s0.setdefault("implausible", []).append("current_a_invariant")
        out[dev] = {"status0": s0, "status1": s1,
                    "generation": gen, "generation_observed": observed}
    return out


# Appendix A settings that differ from REV factory default, i.e. what a factory
# reset drops. Only STATUS_1_PERIOD is observable: firmware 26.1.6 answers no
# parameter reads, so the rest cannot be verified over CAN at all. An audit that
# reported "healthy" without saying so would be overclaiming -- a controller can
# be sitting in COAST with no closed-loop sensor and still pass every check here.
# Status N Period parameter -> api. 8 and 9 are 199 and 224, not 166/167.
API_FOR_PERIOD_PARAM = {158 + i: 0x2E0 + i for i in range(8)}
API_FOR_PERIOD_PARAM[199] = 0x2E8
API_FOR_PERIOD_PARAM[224] = 0x2E9
PERIOD_PARAM_FOR_API = {v: k for k, v in API_FOR_PERIOD_PARAM.items()}

# Enum members in declaration order, from REV-SparkParameters-v0.1.2 section 1.
# The ordinal is the position, which is what the controller stores and returns.
# tests/unit/test_param_enums.py parses that file and pins every entry to it.
PARAM_ENUM_MEMBERS = {
    "MotorType": ("BRUSHED", "BRUSHLESS"),
    "IdleMode": ("COAST", "BRAKE"),
    "Sensor": ("NONE", "MAIN_ENCODER", "ANALOG", "ALT_ENCODER", "DUTY_CYCLE"),
    "VoltageCompMode": ("NO_VOLTAGE_COMP", "CLOSED_LOOP_VOLTAGE_OUTPUT",
                        "NOMINAL_VOLTAGE_COMP"),
    "CompatibilityPort": ("DEFAULT", "ALTERNATE_ENCODER"),
    "DutyCycleMode": ("ABSOLUTE", "RELATIVE", "RELATIVE_STARTING_OFFSET"),
    "AnalogMode": ("ABSOLUTE", "RELATIVE"),
}

# Which parameters carry an enum, from the same file's parameter table.
PARAM_ENUMS = {2: "MotorType", 6: "IdleMode", 9: "Sensor",
               74: "VoltageCompMode", 127: "CompatibilityPort",
               142: "DutyCycleMode"}


def param_enum_value(param_id, name):
    """The ordinal a declared enum NAME writes to `param_id`, or None."""
    members = PARAM_ENUM_MEMBERS.get(PARAM_ENUMS.get(param_id), ())
    key = str(name).strip().upper()
    return members.index(key) if key in members else None


# The axis here is CADENCE, not readability. Only Status 0 and 1 are broadcast on
# 25+, so only their periods can be timed off the wire; every declared deviation
# is READABLE as a parameter on both generations. See SPARK-FAILURE-CATALOGUE.md.
VERIFIABLE_DEVIATIONS = {159: "Status 1 Period"}
UNVERIFIABLE_DEVIATIONS = {
    6: "Idle Mode (BRAKE vs COAST)",
    9: "Closed Loop Control Sensor (MAIN_ENCODER vs NONE)",
    13: "P 0",
    # Sent only "if they are needed" on firmware 25+, so absence from the wire
    # is not evidence of anything. See VERIFIABLE_DEVIATIONS above.
    161: "Status 3 Period",
    163: "Status 5 Period",
    164: "Status 6 Period",
    165: "Status 7 Period",
    224: "Status 9 Period",
}

# Fallback only. The real value is read from sparkflex_motor_defaults.yaml so
# that editing the declared configuration actually reaches the audit and the
# repair path -- a second hardcoded copy here would silently diverge from it.
APPENDIX_A_STATUS_1_PERIOD_MS = 20
REV_DEFAULT_STATUS_1_PERIOD_MS = 250
_PERIOD_TOLERANCE_MS = 30


def declared_value(param_id, role="drive", default=None):
    """The provisioned value for one parameter, from the declared config file.

    Falls back to `default` when the file is missing or does not carry that
    parameter, so the tooling still works on a checkout without it.
    """
    settings = motor_settings(role)
    entry = settings.get(param_id)
    return default if entry is None or entry.get("value") is None else entry["value"]


def declared_status_1_period_ms():
    """Provisioned Status 1 Period. Single source of truth for canary and repair.

    Falls back to the constant when the defaults file is absent, because the
    period verdict is a single number and a checkout without the file must still
    read a cadence. The audit and repair paths do not fall back: motor_settings
    raises there, since comparing a controller against an empty table reports it
    clean whatever it is set to.
    """
    try:
        return float(declared_value(PARAM_STATUS_1_PERIOD,
                                    default=APPENDIX_A_STATUS_1_PERIOD_MS))
    except MotorDefaultsMissing:
        return float(APPENDIX_A_STATUS_1_PERIOD_MS)
    except (TypeError, ValueError):
        return float(APPENDIX_A_STATUS_1_PERIOD_MS)


# A cadence is judged against its own magnitude. An absolute +/-30 ms window
# accepts 2.5x error at 20 ms and rejects 1.12x at 250 ms, so the same rule is
# far too loose where this fleet lives and too tight at the factory default.
# Below this the controller is commanding nothing; above the other, real
# current is flowing. Both at once is the dropout signature (CD 477176).
_DEAD_APPLIED = 0.001
_LIVE_CURRENT_A = 5.0

# REV's SPARK MAX GitBook page: Periodic Status 0 every 10 ms by default. That
# page is the pre-25 table and trails REV-Specs by about a year, so this figure
# applies to pre-25 firmware only. Documented, never measured on this fleet.
REV_DEFAULT_LEGACY_STATUS_0_PERIOD_MS = 10


def fault_frame(generation=None):
    """The frame this firmware generation reports faults in, and its cadence.

    Faults live in STATUS_1 on firmware 25+, for both products. Pre-25 firmware
    has only LEGACY_STATUS_0, whose 32-bit fault word is the whole of what it
    can say. Keying this on product read a healthy 25+ SPARK MAX off the 0x060
    beacon, which is all-ones by design, as sixteen simultaneous faults.

    `provisioned` says whether `spark repair` can write this period. It is False
    on pre-25: repair goes through PARAMETER_WRITE, and the status periods are not
    parameters on that generation -- they move on LEGACY_SET_PERIOD, which repair
    does not use.

    `expected_ms` is REV's own default, which is what an unthrottled controller
    holds. This package additionally throttles pre-25 at boot, so a running fleet
    reads slower by design; `_deliberate_periods` carries those values and the
    cadence rules consult it rather than treating the difference as loss.
    """
    if normalise_generation(generation) == GEN_PRE25:
        # expected_ms is what this fleet SHOULD be running, which on pre-25 is
        # the throttle apply_boot_config writes and not REV's cold default.
        # Returning the cold default for both made status_1_verdict unable to
        # say "reverted" on this generation at all: a rail-cycled fleet matched
        # `expected` and scored ok, so the audit reported a bus that had just
        # lost its whole configuration as healthy. Confirmed on rig-max
        # by a rail cycle -- 50 ms became 10 ms on all eight.
        throttled = _deliberate_periods(GEN_PRE25).get(LEGACY_STATUS_0_API)
        return {"api": LEGACY_STATUS_0_API, "label": "LEGACY_STATUS_0",
                "expected_ms": float(throttled if throttled is not None
                                     else REV_DEFAULT_LEGACY_STATUS_0_PERIOD_MS),
                "rev_default_ms": float(REV_DEFAULT_LEGACY_STATUS_0_PERIOD_MS),
                "provisioned": False}
    return {"api": STATUS_1_API, "label": "STATUS_1",
            "expected_ms": declared_status_1_period_ms(),
            "rev_default_ms": float(REV_DEFAULT_STATUS_1_PERIOD_MS),
            "provisioned": True}


_PERIOD_TOLERANCE_FRAC = 0.25

# A silence this many times the expected period is a hole, not a slow cadence.
_GAP_FACTOR = 4.0


def _near(measured, target):
    return abs(measured - target) <= max(2.0, target * _PERIOD_TOLERANCE_FRAC)


def status_1_verdict(period_ms, evidence=None, expected=None, rev_default=None,
                     also_ok=()):
    """A verdict for one controller's STATUS_1 cadence, given what was measured.

    'ok' | 'reverted' | 'absent' | 'stopped' | 'intermittent' | 'unexpected'.

    A mean alone cannot separate a lost config from a lossy bus: frames spread
    evenly over the window and frames that stopped halfway through average the
    same. `evidence` carries what does separate them -- max_gap_ms, last_seen_ms
    and window_ms from inventory() -- so pass it whenever it exists. Called with
    a bare mean the verdict is still correct about 'reverted' and 'ok', which is
    all the old signature could ever say.
    """
    if period_ms is None:
        return "absent"
    ev = evidence or {}
    window = ev.get("window_ms")
    gap = ev.get("max_gap_ms")
    last = ev.get("last_seen_ms")
    expected = declared_status_1_period_ms() if expected is None else expected
    factory = (float(REV_DEFAULT_STATUS_1_PERIOD_MS)
               if rev_default is None else float(rev_default))
    # Cadences that are correct for a reason the expected/factory pair cannot
    # express -- on pre-25, the throttle apply_boot_config writes at boot. Without
    # this the tool reports its own writes as unexplained drift.
    deliberate = [float(v) for v in (also_ok or ()) if v is not None]

    if window and last is not None and (window - last) > max(
            _GAP_FACTOR * expected, 0.25 * window):
        return "stopped"
    if gap is not None and gap > _GAP_FACTOR * max(expected, period_ms):
        return "intermittent"
    # 'ok' is scored before 'reverted' because on a product whose provisioned
    # period IS the factory default the two overlap, and a healthy MAX at its
    # documented 10 ms would otherwise be reported as having reverted.
    if any(_near(period_ms, d) for d in deliberate):
        return "ok"
    if _near(period_ms, expected):
        return "ok"
    if _near(period_ms, factory):
        return "reverted"
    return "unexpected"


# SPARK_MODEL in STATUS_0, per REV-Specs 2.1.0: 1 is a SPARK Flex. The map is
# only consulted when the caller says what this bus is provisioned for; without
# it the odd controller out is found by comparing the fleet against itself.
# SPARK_MODEL arrived in firmware 26.1.0; before that those bits are reserved.
SPARK_MODEL_MIN_FIRMWARE = (26, 1, 0)


def firmware_reports_spark_model(version):
    """True when this firmware puts SPARK_MODEL in STATUS_0. None means unknown,
    and unknown is treated as NOT reporting -- a model check on a guess is the
    same false positive as scoring a frame that was never broadcast."""
    if not version:
        return False
    try:
        parts = tuple(int(x) for x in str(version).split(".")[:3])
    except (TypeError, ValueError):
        return False
    return parts >= SPARK_MODEL_MIN_FIRMWARE


MODEL_FOR_TYPE = {"sparkflex": 1, "sparkmax": 2}

# Applied output and output current are decoded independently and never compared.
# Below these floors the controller is commanding nothing and nothing is flowing;
# one without the other is the dropout signature (CD 477176) and the
# current-limit clamp (CD 491331). Neither raises a fault bit, so this is a
# plausibility check on already-decoded fields or it is nothing.
# Tuned against a moving robot, not only the simulator. A drive motor commanded
# at 0.10 duty pulling 11 A is ordinary, and the first draft of these floors
# reported it as a clamp: 0.0999 fell under an APPLIED_FLOOR of 0.1 and 11.6 A
# cleared a CURRENT_FLOOR of 1.0. The current floor is now the declared Smart
# Current Free Limit, which real driving stays under and a clamped mechanism does
# not, and the dead-output floors are far enough from any working setpoint that a
# transient between command and current cannot trip them.
APPLIED_FLOOR = 0.1
DEAD_OUTPUT_APPLIED = 0.2
DEAD_OUTPUT_CURRENT_A = 0.5
CLAMPED_CURRENT_A = 20.0

# Every finding carries the action that clears it, after this marker. An operator
# reading `spark audit` at 2am should not have to know which of eleven commands
# applies -- and the whole of was spent learning that a message saying
# what is wrong, without saying what to do, costs hours.
FIX = " -- FIX: "

# What to do about each bit STATUS_1 can raise. One table, read by both
# `spark audit` and `spark faults`, so a condition cannot be explained in one
# command and left bare in the other.
BIT_REMEDIES = {
    "overcurrent":
        "the motor drew more than its Smart Current Stall Limit, which is 80 A "
        "-- REV's factory default, because nothing in this repo ever writes it. "
        "Sticky means it happened, not that it is happening now. Do NOT raise "
        "the limit to quiet this. The SPARK reports MOTOR current while an "
        "in-line fuse carries INPUT current, and the two differ by the duty "
        "cycle: I_fuse is roughly duty x I_motor. On rig-flex's 40 A fuses at "
        "0.40 steer duty, a 150 A motor draw is about 60 A through a 40 A fuse, "
        "which is why the fuse survives and why raising the limit is the wrong "
        "direction. The knob is steer_max_output in spark.yaml. Measure "
        "first: `uv run python tools/spark_teleop_watch.py` reports dwell and "
        "flags samples where the 12-bit current field pegged at 150.00 A and "
        "stopped being a reading",
    "brownout":
        "the rail dipped below the controller's floor and the controller reset "
        "with it, so expect hasReset beside this and a lost configuration. "
        "`uv run spark voltage` for per-controller rail against pack state of "
        "charge. A brownout under load is a power-path fault -- battery state of "
        "charge, main breaker, lug torque, wire gauge -- and re-provisioning the "
        "controllers fixes none of it",
    "hasReset":
        "the controller rebooted and everything volatile went with it, status "
        "frame periods above all. The rail bit is NOT set beside this, so it "
        "was an ordinary power cycle and not a brownout -- no power-path fault "
        "to chase. To re-apply: `uv run spark defaults` prints what every motor "
        "should hold, and `uv run spark repair --id N --persist` restores Status "
        "1 Period. A raw CAN write to the other status periods does work "
        "(measured on rig-flex: parameter 159 written 20 to 50 changed the cadence "
        "and read back Success), and every parameter can be CHECKED over CAN on "
        "both generations, so `spark audit` reports which settings came back "
        "wrong. Read the bit before clearing it: `spark clear` destroys the only "
        "record",
    "stall":
        "commanded and did not turn. Check the mechanism for a bind before "
        "raising any current limit",
    "sensor":
        "encoder or motor data cable. Check the JST at the motor; teams replace "
        "these routinely",
    "gateDriver":
        "survives factory reset and reflash. If it returns immediately after "
        "`uv run spark clear`, the controller is the fault and it is an RMA",
    "can":
        "bus contention. Two devices answering one id will do it -- check with "
        "`uv run spark duplicates`",
    "temperature":
        "let it cool, then look for a mechanical bind. Flex recovers on its own "
        "with no sticky bit, so a sticky one means it was sustained",
    "motorType":
        "a brushless motor in brushed mode will not run. Motor Type is param 2, "
        "and `uv run spark params --param 2` reads it on either generation. "
        "Changing it stays an RHC2 job over USB-C, because 2 is in "
        "PROTECTED_PARAMS and this tooling refuses the write",
    "escEeprom":
        "REV acknowledged a firmware bug raising an EEPROM condition. Check the "
        "version with `uv run spark status` before suspecting the hardware",
    "extEeprom":
        "the external EEPROM is on the data port. Check what is plugged into it",
    "firmware":
        "compare versions across the bus with `uv run spark status`; a mixed-"
        "version bus misbehaves on its own",
    "other":
        "no documented meaning. Capture `uv run spark faults` output before "
        "clearing, because the bit is all the evidence there is",
}


# Keyed by a PAIR of bits, because a reset WITH a rail collapse means something
# neither bit means alone. Kept out of BIT_REMEDIES, which is keyed by the names
# decode_status_1 emits and is checked against exactly those.
PAIR_REMEDIES = {
    ("brownout", "hasReset"):
        "hasReset+brownout -- the rail collapsed and took the controller with "
        "it. This is a POWER PATH fault: battery state of charge, main breaker, "
        "lug torque, wire gauge. Re-provisioning the controllers fixes none of "
        "it, and the configuration is lost again at the next sag. "
        "`uv run spark voltage` for per-controller rail against pack SoC. "
        "Re-apply the configuration once the rail is fixed, not before.",
}


# What to do about a bit ON PRE-25, where the answer differs from the 25+ one.
# Consulted first on that generation; anything absent here falls through to
# BIT_REMEDIES, which is correct for both.
#
# hasReset is the entry that matters. The 25+ text points at `spark repair --id N
# --persist`, and repair refuses on this generation before it sends anything.
LEGACY_BIT_REMEDIES = {
    "hasReset":
        "the controller rebooted and EVERYTHING this package wrote over CAN "
        "went with it -- status frame periods, and every parameter besides. "
        "Nothing this package sends survives a power cycle on a pre-25 MAX: a "
        "legacy parameter write lands in RAM, and PERSIST_PARAMETERS is "
        "versionImplemented 25.0.0. Confirmed on rig-max twice, with all eight "
        "back at COAST after the rail came up. "
        "Api 0x072 with the magic 15011 little-endian DOES commit parameters "
        "0-133 to flash on this firmware, measured, but no command "
        "here sends it and the burn was measured not to reach the status "
        "periods. "
        "To re-apply: starting the drive stack already does it, because "
        "SparkBus.init_controller calls apply_boot_config and both paths write "
        "the same table. `uv run spark throttle` is the standalone form, for a "
        "bus being administered before the stack has run; it reports which "
        "controllers took the periods. Do NOT reach for `spark repair "
        "--persist`; it refuses on this generation and it would not reach the "
        "periods either. Read the bit before clearing it: `spark clear` "
        "destroys the only record the reboot happened",
    "iwdtReset":
        "the independent watchdog reset the controller, which is firmware "
        "failing to service it rather than a rail event. Expect hasReset "
        "beside it and a lost configuration. If it repeats on one controller "
        "and no other, that controller is the suspect",
    "eepromCrc":
        "stored configuration failed its checksum, so what the controller came "
        "back with may not be what was provisioned. Read the parameter table "
        "and compare -- on this generation every parameter answers, so this is "
        "checkable over CAN without USB-C",
    "canTx":
        "the controller could not transmit: nobody acknowledged its frames. "
        "One device alone on a bus does this, and so does a broken termination "
        "or a wiring fault. Check `uv run spark status` sees the whole fleet",
    "canRx":
        "the controller's receive error counter passed its threshold. Bus "
        "contention, a duplicate id, or a bitrate mismatch. Note that on this "
        "generation a rail cycle drops the boot throttle and the fleet returns "
        "to REV's faster defaults, which is a large step up in bus load",
    "softLimitFwd":
        "a soft limit stopped forward travel. It is configuration, not damage: "
        "parameters are readable here, so read the limit back rather than "
        "guessing",
    "softLimitRev":
        "a soft limit stopped reverse travel. As for softLimitFwd",
    "hardLimitFwd":
        "the forward hard-limit input read as reached. On a robot with no "
        "switch fitted this is a polarity setting, not a switch: parameters 50 "
        "and 51 are the polarities and both are in PROTECTED_PARAMS, so change "
        "them in REV Hardware Client over USB-C",
    "hardLimitRev":
        "the reverse hard-limit input read as reached. As for hardLimitFwd",
}


def remedy_for(bits, generation=None):
    """The action for each named bit, joined. Empty when none is documented.

    hasReset and brownout together mean something different from either alone,
    so the pair gets its own entry: a reset WITH a rail collapse is a power
    fault, and re-provisioning it fixes nothing.

    `generation` selects the pre-25 overlay. Without it the caller gets the 25+
    text, which is what every caller got unconditionally until -- so
    a rail-cycled MAX was told to run a command that refuses on its own firmware.
    """
    table = dict(BIT_REMEDIES)
    if normalise_generation(generation) == GEN_PRE25:
        table.update(LEGACY_BIT_REMEDIES)
    seen = list(bits)
    out = []
    for pair, text in PAIR_REMEDIES.items():
        if all(b in seen for b in pair):
            out.append(text)
            seen = [b for b in seen if b not in pair]
    out += [f"{b} -- {table[b]}." for b in seen if b in table]
    return " ".join(out)


# How old a sticky bit can be, which differs by generation and is the difference
# between "since the last power-up" and "since somebody last cleared it". The
# placeholder is filled per reading; a generation-blind sentence here told a
# pre-25 operator the opposite of the truth, because on that generation a power
# cycle SETS hasReset rather than clearing the byte.
_STICKY_AGE_CLAUSE = "{sticky_age}"
_STICKY_AGE_FW25 = ("; a power cycle clears this byte on 26.1.6, so the event is "
                    "more recent than the last power-up")
_STICKY_AGE_PRE25 = ("; this byte survives a power cycle on this generation, and "
                     "a power cycle SETS hasReset, so the event may predate the "
                     "last power-up")


_STATUS_1_FINDINGS = (
    ("faults", "FAULT",
     "a fault pins applied output to 0, so this controller cannot drive while "
     "its status frames keep perfect time" + FIX + "remove the cause, then "
     "`uv run spark clear`. A fault that comes straight back after clearing is "
     "hardware; gateDriver especially, which survives factory reset and reflash "
     "and ends in an RMA"),
    ("sticky_faults", "sticky FAULT",
     "it faulted at some point since the last clear" + _STICKY_AGE_CLAUSE + FIX
     + "this is evidence, not a live fault. Read it, then `uv run spark clear` "
     "erases it"),
    ("warnings", "warning",
     "a warning does not stop the motor, and it records a condition worth "
     "reading before it goes away" + FIX + "`uv run spark voltage` for brownout, "
     "`uv run spark faults` for the rest"),
    ("sticky_warnings", "sticky warning",
     "a condition that has already happened and cleared, and `spark clear` "
     "erases the only record of it" + FIX + "read it before clearing"),
)


def normalised_reading(reading, generation=None):
    """One shape for a bus reading, whichever generation produced it.

    The two generations put the same information in different frames: 25+ keeps
    telemetry in STATUS_0 and faults in STATUS_1, pre-25 keeps faults in
    LEGACY_STATUS_0 and telemetry in 0x061, with no warning field at all. Every
    consumer that reached into a reading by key was 25+-only by construction --
    audit_problems raised KeyError('sticky_warnings') on a pre-25 bus.

    The reading carries its own generation from `collect_status`, so a caller
    passing nothing gets the one the wire reported.
    """
    s0 = (reading or {}).get("status0") or {}
    s1 = (reading or {}).get("status1") or {}
    gen = normalise_generation(
        (reading or {}).get("generation") or generation or DEFAULT_GENERATION)
    if gen == GEN_FW25:
        return {
            "applied_output": s0.get("applied_output"),
            "voltage_v": s0.get("voltage_v"),
            "current_a": s0.get("current_a"),
            "motor_temp_c": s0.get("motor_temp_c"),
            "implausible": s0.get("implausible") or [],
            "spark_model": s0.get("spark_model"),
            "primary_heartbeat_lock": s0.get("primary_heartbeat_lock"),
            "hard_forward_limit": s0.get("hard_forward_limit"),
            "hard_reverse_limit": s0.get("hard_reverse_limit"),
            "faults": s1.get("faults") or [],
            "warnings": s1.get("warnings") or [],
            "sticky_faults": s1.get("sticky_faults") or [],
            "sticky_warnings": s1.get("sticky_warnings") or [],
            "faults_seen": s1.get("faults_seen") or [],
            "is_follower": s1.get("is_follower"),
            # Firmware 25+ names every signal in STATUS_0, so there is no
            # undecoded byte to carry forward.
            "other_signals": None,
            # Firmware 25+ names INVERTED in STATUS_0; decode_status_0 has not
            # been extended to lift it, so this is unset rather than false.
            "inverted": None,
            "has_telemetry": bool(s0),
        }
    # A 0x060 beacon is pinned to all-ones by firmware 25+ and says nothing
    # about faults, so scoring its bits would report sixteen on a healthy
    # controller. Report the misread instead of the bits.
    beacon = bool(s0.get("is_beacon"))
    return {
        "applied_output": None if beacon else s0.get("applied_output"),
        "voltage_v": s1.get("voltage_v"),
        "current_a": s1.get("current_a"),
        "motor_temp_c": s1.get("motor_temp_c"),
        "implausible": ["legacy_beacon_not_a_reading"] if beacon else [],
        # Pre-25 LEGACY_STATUS_0 carries no model field; the fleet-comparison
        # check that uses it has nothing to compare on this generation.
        "spark_model": None,
        "primary_heartbeat_lock": None,
        "hard_forward_limit": None,
        "hard_reverse_limit": None,
        "faults": [] if beacon
        else _bits(s0.get("active_faults") or 0, _LEGACY_FAULT_BITS),
        # Pre-25 has faults and sticky faults and no warning field, so an empty
        # warning list here is the truth about the generation, not a gap.
        "warnings": [],
        "sticky_faults": [] if beacon
        else _bits(s0.get("sticky_faults") or 0, _LEGACY_FAULT_BITS),
        "sticky_warnings": [],
        "faults_seen": [],
        "is_follower": None,
        "other_signals": None if beacon else s0.get("other_signals"),
        "inverted": None if beacon else s0.get("inverted"),
        "has_telemetry": bool(s1),
    }


def reading_from_raw(status0_raw, status1_raw, generation=None):
    """One normalised reading from two BUFFERED status frames.

    For callers that hold raw bytes rather than a collect_status() result --
    SparkBus keeps the last frame per controller in Controller._status0_raw and
    _status1_raw, and fills them from EITHER generation's api by design.

    This exists because that is a trap. swerve_drive's drive gate decoded those
    buffers with decode_status_0/decode_status_1 unconditionally, which is the
    firmware-25 pair, so on a pre-25 bus it read the 16-bit fault word in bytes
    2:6 as bus voltage and output current and could not see a fault at all. The
    gate was not misreporting there, it was blind, and a hard-limited controller
    passed it. Verified on rig-max.

    Returns the shape normalised_reading defines, so a consumer reads the same
    keys whatever arrived. On pre-25 the hard-limit and heartbeat-lock fields are
    None -- that generation has no such flags -- and the limits appear instead as
    hardLimitFwd and hardLimitRev in `faults`, which is where the firmware puts
    them.
    """
    gen = normalise_generation(generation)
    if gen == GEN_PRE25:
        reading = {
            "status0": decode_legacy_status_0(status0_raw) if status0_raw else None,
            "status1": decode_legacy_status_1(status1_raw) if status1_raw else None,
        }
    else:
        reading = {
            "status0": decode_status_0(status0_raw) if status0_raw else None,
            "status1": decode_status_1(status1_raw) if status1_raw else None,
        }
    reading["generation"] = gen
    return normalised_reading(reading, gen)


def status_problems(status, roles, expected_model=None, generation=None):
    """Findings from one decoded bus reading: faults, flags and telemetry.

    Split out of audit_problems so the fault half can be read and tested on its
    own, and so the audit has something to consume. Decoding a fault correctly
    and then having nowhere to put it is exactly how `spark audit` came to
    print "no problems found" over a latched gate-driver fault.

    `expected_model` is the SPARK model this bus is provisioned for. Without it
    the check falls back to comparing the fleet against itself, which still
    finds one foreign controller among seven.
    """
    problems = []
    models = {}
    for dev, reading in sorted((status or {}).items()):
        r = normalised_reading(reading, generation)
        s1_dock = (reading or {}).get("status1") or {}
        bits = set(s1_dock.get("faults") or []) | set(s1_dock.get("sticky_faults") or [])
        if {"escEeprom", "other"} <= bits:
            who = f"id {dev} ({roles.get(dev, '?')})"
            problems.append(
                f"{who} is reporting escEeprom and other together, which is "
                "REV's own signature for a Vortex that is not fully seated on "
                "its SPARK Flex -- a mechanical joint, not a CAN or a config "
                "problem" + FIX + "reseat the motor on the controller and check "
                "that both docking screws are fully installed. A team on a Flex "
                "swerve base saw this escalate to overcurrent every match and "
                "fixed it by remounting, without touching the CAN network: "
                "https://www.chiefdelphi.com/t/453509")
        who = f"id {dev} ({roles.get(dev, 'not in base.can_ids')})"
        s0 = (reading or {}).get("status0")
        s1 = (reading or {}).get("status1")

        if s1:
            for key, label, why in _STATUS_1_FINDINGS:
                bits = r.get(key) or []
                if not bits:
                    continue
                why = why.replace(
                    _STICKY_AGE_CLAUSE,
                    _STICKY_AGE_PRE25
                    if normalise_generation(generation) == GEN_PRE25
                    else _STICKY_AGE_FW25)
                detail, _, tail = why.partition(FIX)
                fix = remedy_for(bits, generation) or tail
                problems.append(
                    f"{who} {label}: {','.join(bits)} -- {detail}" + FIX + fix)
            if r.get("is_follower"):
                problems.append(
                    f"{who} is in follower mode -- REVLib 2025 runs a follower "
                    "whether or not any code references it, so this wheel can "
                    "be driven by something no object in the program holds"
                    + FIX + "clear Follower Mode Leader Id, param 194, in RHC2 "
                    "over USB-C. Nothing here writes it")

        if not r["has_telemetry"]:
            continue
        if r["implausible"]:
            problems.append(
                f"{who} sent a STATUS_0 that is not a measurement: "
                + describe_implausible(r["implausible"])
                + " -- every other number and limit bit in this frame is "
                "unreadable too" + FIX + "the controller has stopped driving the "
                "bus rather than stopped working. Power cycle it, then `uv run "
                "spark faults` for the sticky record of what preceded it")
            continue
        if r["spark_model"] is not None:
            models[dev] = r["spark_model"]
        if r["primary_heartbeat_lock"]:
            problems.append(
                f"{who} is locked to another heartbeat source and will ignore "
                "this host until it is power cycled -- every conclusion drawn "
                "from 'it did not move' is wrong while this is set"
                + FIX + "cut and restore motor power. No CAN command releases it")
        for key, name in (("hard_forward_limit", "hard forward limit"),
                          ("hard_reverse_limit", "hard reverse limit")):
            if r[key]:
                problems.append(
                    f"{who} has its {name} closed -- that is the data-port "
                    "safety interlock, a state to respect rather than a fault "
                    "to clear, and the interlock parameters are write-protected "
                    "here" + FIX + "if nothing is physically at an end stop, the "
                    "polarity has drifted from base.limit_switch_polarity: run "
                    "`uv run python tools/spark_limit_polarity_repair.py` to see "
                    "it and `--repair --persist` to correct it")
        applied, amps = r["applied_output"], r["current_a"]
        if applied is None or amps is None:
            continue
        if abs(applied) > DEAD_OUTPUT_APPLIED and amps < DEAD_OUTPUT_CURRENT_A:
            problems.append(
                f"{who} reports {applied:+.2f} applied output drawing only "
                f"{amps:.2f} A -- output commanded with nothing flowing, which "
                "is what a dead output stage looks like" + FIX + "check the "
                "motor leads and the data cable, then `uv run spark faults` for "
                "a gate-driver fault")
        elif abs(applied) < APPLIED_FLOOR and amps > CLAMPED_CURRENT_A:
            problems.append(
                f"{who} reports {applied:+.2f} applied output while {amps:.1f} A "
                "flows -- current with nothing commanding it, which is what a "
                "mechanism clamped at its current limit looks like" + FIX
                + "check the mechanism for a bind; `uv run spark params --id N` "
                "reads the Smart Current Stall Limit back over CAN")

    if expected_model is not None:
        for dev, model in sorted(models.items()):
            if model != expected_model:
                problems.append(
                    f"id {dev} ({roles.get(dev, '?')}) reports SPARK model "
                    f"{model} and this bus is provisioned for model "
                    f"{expected_model} -- a different controller is answering "
                    "that address" + FIX + "`uv run spark status` prints the "
                    "serial on that id; compare it against base.can_serials. A "
                    "swapped controller needs its id set by serial, `uv run "
                    "spark set-id --serial X --to N`; a wrong base.controller_type "
                    "needs the config corrected and `uv sync --extra` re-run")
    elif len(set(models.values())) > 1:
        counts = {}
        for model in models.values():
            counts[model] = counts.get(model, 0) + 1
        common = max(counts, key=counts.get)
        for dev, model in sorted(models.items()):
            if model != common:
                problems.append(
                    f"id {dev} ({roles.get(dev, '?')}) reports SPARK model "
                    f"{model} where the rest of the bus reports {common} -- a "
                    "different controller is answering that address" + FIX
                    + "`uv run spark status` prints the serial on that id; "
                    "compare it against base.can_serials, then `uv run spark "
                    "set-id --serial X --to N` if the controller moved")
    return problems


def _deliberate_periods(generation=None):
    """{api: ms} this package writes at boot, so cadence rules can tell a
    deliberate throttle from frame loss. Empty on 25+, which is left at REV's
    defaults."""
    if normalise_generation(generation) != GEN_PRE25:
        return {}
    from.can_bus import _SPARKMAX_STATUS_PERIODS_MS
    return {LEGACY_STATUS_0_API + idx: float(ms)
            for idx, ms in _SPARKMAX_STATUS_PERIODS_MS.items()}


def _bus_level_finding(inventory, roles, baseline=None, generation=None):
    """One finding when the shape of the loss says the bus failed, not a device.

    Returns None when the loss looks like independent controllers, which is when
    the per-device rules are the right ones.
    """
    configured = sorted(set(roles or {}) | set(baseline or {}))
    if not configured:
        return None
    present = sorted(set(inventory) & set(configured))
    missing = [d for d in configured if d not in inventory]

    if not present:
        return (
            "no REV motor-controller frame arrived from any configured id -- "
            "that is the bus, not eight controllers that each failed at once"
            + FIX + "check the CAN adapter is up (`ip -br link show type can`), "
            "then power at the first controller in the chain, then the 120 ohm "
            "termination at each end. `uv run spark clear` wakes a gated bus")

    # A contiguous tail of the chain going together is one break, not N deaths.
    if len(missing) > 1 and missing == configured[len(present):] and present == \
            configured[:len(present)]:
        return (
            f"ids {missing[0]}-{missing[-1]} are all silent and they are the "
            f"contiguous tail of the chain past id {present[-1]} -- one break in "
            "the wiring segment, not "
            f"{len(missing)} controllers failing together"
            + FIX + "check the CAN link between "
            f"id {present[-1]} and id {missing[0]} first: connector, crimp and "
            "termination. Everything downstream of a break goes silent whether "
            "or not it is healthy")

    # Every controller present but every one thinned: the bus ate the traffic.
    #
    # Unless the whole fleet sits on a cadence this package asked for. On pre-25
    # apply_boot_config throttles every status frame at boot, because REVLib's
    # defaults push enough receive traffic to risk wedging the gs_usb dongle, so a
    # running fleet reads slower than REV's default by design rather than by loss.
    ff = fault_frame(generation)
    deliberate = _deliberate_periods(generation).get(ff["api"])
    thinned = [d for d in present
               if (inventory[d].get("periods_ms") or {}).get(ff["api"])
               and inventory[d]["periods_ms"][ff["api"]] > 3 * ff["expected_ms"]
               and not (deliberate is not None
                        and _near(inventory[d]["periods_ms"][ff["api"]], deliberate))]
    if present and len(thinned) == len(present) and len(present) > 2:
        return (
            f"all {len(present)} controllers thinned out together -- every "
            "measured period is more than three times the provisioned value at "
            "once. Controllers do not lose their config in unison; the bus is "
            "dropping frames" + FIX + "check utilisation and termination before "
            "re-provisioning anything. `uv run spark status` shows whether the "
            "adapter is logging bus errors")
    return None


def audit_problems(inventory, duplicates, roles, known_serials=None,
                   baseline=None, status=None, controller_type=None,
                   generation=None):
    """Pure audit: compare a bus reading against config, baseline and status.

    Takes plain data so it is testable without a CAN bus. Returns a list of
    human-readable problem strings, most structural first.

    `status` is a `collect_status()` reading. Without it the audit scores
    broadcast cadence and identity only, which is how a controller in
    gate-driver fault at a perfect 20 ms period used to audit clean.
    """
    known_serials = known_serials or {}
    problems = []
    # A status reading already carries the generation each device reported, so a
    # caller that passed one need not say it twice. Defaulting to 25+ while
    # holding a pre-25 reading scores the wrong frame.
    if generation is None and status:
        generation = dominant_generation(status)
    ff = fault_frame(generation)
    frame = ff["label"]
    period_fix = ("`uv run spark repair --id N --persist` puts it back"
                  if ff["provisioned"] else
                  f"this package has no verified parameter id for the {frame} "
                  "Period on a SPARK MAX, so the repair command is withheld "
                  "here -- it writes 159, the Flex id, which nobody has checked "
                  "on this product. Set the period in REV Hardware Client over "
                  "USB-C, then settle max.param.status_period with "
                  "`uv run spark verify`")

    # A structural failure first, or it is reported N times as N unrelated ones.
    # An empty bus, a lost chain segment and a saturated bus all present as
    # missing or thinned controllers, and per-device findings for each send the
    # operator to re-provision hardware that never lost its config.
    bus_level = _bus_level_finding(inventory, roles, baseline, generation)
    if bus_level:
        return [bus_level]

    # Through classify_reset, which searches sticky warnings AND sticky faults.
    # Keying on sticky_warnings alone made this set empty by construction on
    # pre-25 -- that generation has no warning field, so hasReset lands in
    # sticky_faults and every branch guarded by `dev in rebooted` was unreachable
    # on exactly the product where a reboot costs the whole configuration.
    rebooted = set()
    for dev, reading in (status or {}).items():
        r = normalised_reading(reading, generation)
        if classify_reset(r["sticky_warnings"], r["sticky_faults"]) != "none":
            rebooted.add(dev)

    for dev, serials in sorted((duplicates or {}).items()):
        problems.append(
            f"CAN id {dev} is answered by {len(serials)} controllers "
            f"({', '.join(serials)}) -- an id scan cannot see this" + FIX
            + "`uv run spark identify --serial X` blinks one of them, then "
            "`uv run spark set-id --serial X --to N` moves it. Addressed by "
            "serial, so it reaches the one you chose")

    for dev in sorted(set(roles) - set(inventory)):
        problems.append(
            f"id {dev} ({roles[dev]}) is not broadcasting" + FIX
            + "`uv run spark status` sends a firmware query, which wakes a bus "
            "that came back silent from a power cycle and erases nothing. Read "
            "`uv run spark faults` before considering `uv run spark clear`. If "
            "it stays silent, check motor power and the status LED. A controller "
            "that is dark and unresponsive over USB too may have been killed by "
            "a shorted motor (CD 435486): replace the MOTOR as well, or the "
            "replacement controller dies on the next power-up")
    # id 0 is unconfigured and REV will not enable it (CD 347357). Reported
    # whether or not a role maps to it.
    if 0 in inventory:
        who = inventory[0].get("serial")
        role0 = roles.get(0)
        problems.append(
            f"id 0 is broadcasting"
            + (f" as {role0}" if role0 else "")
            + (f", serial {who}" if who else "")
            + " and can NEVER be enabled -- REV treat CAN id 0 as unconfigured, "
            "so the controller answers, reports telemetry, and refuses to "
            "actuate" + FIX + "reassign it into 1..62: `uv run spark set-id "
            f"--serial {who or 'X'} --to N`. Addressed by serial, so it reaches "
            "the one you mean even if something else is also on 0")

    # Join a missing id to a stray one by serial, so the mover is named.
    missing_by_serial = {known_serials[d]: d for d in set(roles) - set(inventory)
                         if known_serials.get(d)}
    for dev in sorted(set(inventory) - set(roles)):
        found = (inventory[dev].get("serial") or "").upper()
        came_from = missing_by_serial.get(found) or missing_by_serial.get(
            found.upper())
        if came_from is not None:
            problems.append(
                f"id {dev} is serial {found}, which base.can_serials records as "
                f"id {came_from} ({roles.get(came_from, '?')}) -- that "
                f"controller moved, it was not replaced" + FIX
                + f"`uv run spark set-id --serial {found} --to {came_from}` puts "
                "it back. Addressed by serial, so it reaches the one that moved")

    for dev in sorted(set(inventory) - set(roles)):
        if 0 == dev:
            continue                      # already reported above, with its cause
        problems.append(
            f"id {dev} is broadcasting but is not in base.can_ids" + FIX
            + "if it belongs here, add it to base.can_ids and run "
            "`uv run spark learn-serials --write`; if it is at the wrong "
            "address, `uv run spark set-id --serial X --to N`")

    # A generation with no UNIQUE_ID frame cannot answer an identity question,
    # and an audit that says "swapped" instead of "unreadable" sends the
    # operator to re-address eight healthy controllers.
    shared = sorted(set(inventory) & set(roles))
    serials_unavailable = bool(shared) and bool(known_serials) and all(
        inventory[d].get("serial") is None for d in shared)
    if serials_unavailable:
        problems.append(
            "no controller on this bus answered with an identity, so nothing "
            "here can be checked against base.can_serials" + FIX
            + "on firmware 25+ the serial arrives on its own in UNIQUE_ID "
            "(api 0x2F0) and a silent one means the controller is not "
            "broadcasting. On pre-25 there is no broadcast to wait for: the "
            "identity is REQUESTED at api 0x094, so this means the caller did "
            "not ask -- `inventory(with_fingerprint=True)` -- or a controller "
            "answered the inventory and not the request, which is worth "
            "investigating on its own")
    for dev in shared:
        info = inventory[dev]
        role = roles.get(dev, "?")
        want_serial = known_serials.get(dev)
        # UNIQUE_ID is implemented at 25.0.0, so a pre-25 controller broadcasts
        # no serial and every id would otherwise read as swapped. Only the
        # identity check is skipped; cadence and faults still run below.
        if serials_unavailable:
            want_serial = None
        if want_serial and info.get("serial") != want_serial:
            problems.append(
                f"id {dev} ({role}) has serial {info.get('serial')}, config says "
                f"{want_serial} -- a controller was swapped or an id moved" + FIX
                + "if the swap was intended, `uv run spark learn-serials --write` "
                "and re-snapshot; otherwise `uv run spark set-id` to put it back")
        measured = (info.get("periods_ms") or {}).get(ff["api"])
        verdict = status_1_verdict(
            measured,
            evidence={
                "window_ms": info.get("window_ms"),
                "max_gap_ms": (info.get("max_gap_ms") or {}).get(ff["api"]),
                "last_seen_ms": (info.get("last_seen_ms") or {}).get(ff["api"]),
            },
            expected=ff["expected_ms"], rev_default=ff["rev_default_ms"],
            also_ok=(_deliberate_periods(generation).get(ff["api"]),))
        if verdict == "stopped":
            problems.append(
                f"id {dev} ({role}) {frame} stopped part-way through the window "
                "and never came back -- the frames ended rather than slowed, so "
                "this is a controller leaving the bus and not a config change"
                + FIX + "check power and the CAN chain at that controller before "
                "re-provisioning anything; `uv run spark faults` says whether it "
                "is answering at all")
        elif verdict == "intermittent":
            problems.append(
                f"id {dev} ({role}) {frame} has a dropout: one gap far longer "
                "than its own cadence, with the cadence otherwise correct. The "
                "device left the bus and came back, which re-provisioning hides "
                "rather than fixes" + FIX + "look for a connector or a break in "
                "the harness -- CD 460286 found a solder joint shorting CANH to "
                "CANL under shrink wrap -- and for a current limit tripping "
                "under load")
        elif verdict == "unexpected" and dev in (duplicates or {}):
            problems.append(
                f"id {dev} ({role}) is broadcasting at {measured} ms, about "
                "twice the provisioned rate, which is what two controllers "
                "sharing one id look like on the wire. The cadence is not a "
                "config finding here" + FIX + "resolve the duplicate first; the "
                "period cannot be measured on a shared id")
        elif verdict == "unexpected":
            problems.append(
                f"id {dev} ({role}) {frame} Period is {measured} ms, which is "
                f"neither the provisioned {int(ff['expected_ms'])} ms nor REV's "
                f"{int(ff['rev_default_ms'])} ms default. No reset and no "
                "provisioning run produces that value, so something wrote it"
                + FIX + period_fix
                + ". If it drifts again, another host is writing to this bus")
        elif verdict == "absent":
            problems.append(
                f"id {dev} ({role}) sends no {frame} -- it reports no faults at all"
                + FIX + period_fix
                + f". Until the {frame} Period is restored this controller "
                "cannot report a fault to anything")
        elif verdict == "reverted" and dev in rebooted:
            # The remedy differs by generation and naming the wrong one is worse
            # than naming none: `spark repair` REFUSES on pre-25 before it sends
            # anything (fault_frame provisioned=False), so telling a MAX operator
            # to run it sends them to a command that cannot work.
            reapply = ("starting the drive stack re-applies these on its own, "
                       "through apply_boot_config. `uv run spark throttle` is "
                       "the standalone form: it re-sends the whole status-period "
                       "table and measures the cadence back, because nothing on "
                       "this generation acknowledges the write"
                       if normalise_generation(generation) == GEN_PRE25
                       else "`uv run spark repair --id N` writes RAM only")
            problems.append(
                f"id {dev} ({role}) {frame} Period is at the REV factory default "
                "and its sticky hasReset says it restarted. Status frame periods "
                "are volatile, so the likely cause is a reboot that dropped a "
                "RAM-only value rather than flash config loss." + FIX
                + "re-apply the configuration rather than spending a flash cycle: "
                + reapply)
        elif verdict == "reverted":
            problems.append(
                f"id {dev} ({role}) {frame} Period is at the REV factory default "
                f"({int(ff['rev_default_ms'])} ms, not the provisioned "
                f"{int(ff['expected_ms'])} ms). That is ONE observable "
                "parameter, not proof the whole config reverted -- and the CAN ID "
                "is demonstrably intact, since the device answered here." + FIX
                + period_fix
                + ". `uv run spark provision --id N --write` reads every "
                "declared setting off this controller, writes back what "
                "disagrees, and reads each write again")

    for dev, ref in sorted((baseline or {}).items()):
        dev = int(dev)
        if dev not in inventory:
            if dev not in roles:
                # A device in roles already produced "is not broadcasting"
                # above; saying it twice reads as two separate faults.
                problems.append(
                    f"id {dev} is in the baseline but is not on the bus" + FIX
                    + "as for a controller that is not broadcasting: query it "
                    "with `uv run spark status` first, which wakes a silent bus "
                    "without erasing anything")
            continue
        info = inventory[dev]
        if ref.get("serial") and info.get("serial") != ref["serial"]:
            problems.append(
                f"id {dev} serial {info.get('serial')} != baseline "
                f"{ref['serial']}" + FIX + "if the controller was replaced "
                "deliberately, re-baseline with `uv run spark snapshot --write`")
        if ref.get("firmware") not in (None, "unknown"):
            live = info.get("firmware")
            if "firmware" in info and not live:
                # The key is present and empty: the query was ASKED and went
                # unanswered. Guarding on `if live` treated that as a match,
                # so a controller refusing GET_FIRMWARE audited clean against
                # any baseline. CD 427137 is that refusal.
                problems.append(
                    f"id {dev} did not answer a firmware query, so it cannot be "
                    f"compared against the baseline {ref['firmware']}" + FIX
                    + "a controller that refuses GET_FIRMWARE is usually "
                    "answering on a different id than configured (CD 427137); "
                    "`uv run spark status` shows what is actually broadcasting")
            elif live and live != ref["firmware"]:
                problems.append(
                    f"id {dev} firmware {live} != baseline {ref['firmware']}"
                    + FIX + "update every device on the bus, including non-REV "
                    "ones, then re-baseline with `uv run spark snapshot --write`")

    # Score the model only where firmware reports one; 25.x reads model 0.
    firmwares = [inventory[d].get("firmware") for d in inventory]
    model_is_readable = bool(firmwares) and all(
        firmware_reports_spark_model(f) for f in firmwares)
    expected = (MODEL_FOR_TYPE[normalise_product(controller_type)]
                if controller_type and model_is_readable else None)
    problems += status_problems(status, roles, expected_model=expected,
                                generation=generation)
    return problems


def coverage_note(controller_count, generation=None, checked_params=(),
                  defaults=None):
    """What an audit did NOT check. Printed alongside every clean result.

    Both generations answer a parameter read, in different dialects and over
    different ranges, so `checked_params` is what the audit actually read and the
    note shrinks as coverage grows. Pre-25 reaches parameters 0-133 and refuses
    the status periods; 25+ reaches all of 0-255.
    """
    checked_params = set(checked_params or ())
    # Derived from the ACTIVE product's declared file, not from the two dicts
    # below. Those enumerate the Flex fleet's deviations exactly, and scoring a
    # SPARK MAX bus against them reported coverage of settings that file does
    # not declare while missing ones it does. Same shape as every other Flex
    # constant that reached a MAX host.
    universe = declared_deviations(defaults)
    if not universe:
        universe = dict(VERIFIABLE_DEVIATIONS)
        universe.update(UNVERIFIABLE_DEVIATIONS)
    # The file names a setting the way RHC2 does; the two dicts below spell out
    # what it MEANS, and "Idle Mode (BRAKE vs COAST)" is the half of that
    # disclosure an operator acts on. Take the universe from the file and the
    # wording from here, wherever this knows better.
    universe = {k: (UNVERIFIABLE_DEVIATIONS.get(k)
                    or VERIFIABLE_DEVIATIONS.get(k) or v)
                for k, v in universe.items()}
    cadence = {k: v for k, v in universe.items() if k in VERIFIABLE_DEVIATIONS}
    total = len(universe)
    # Count only against the twelve this note enumerates. A declared deviation
    # outside that list is still read and still reported; adding it to the
    # numerator would make the fraction exceed its own denominator.
    scored = set(cadence) | (set(checked_params) & set(universe))
    head = (f"checked {len(scored)} of {total} settings that deviate from "
            f"factory default, on {controller_count} controller(s).\n")
    if normalise_generation(generation) == GEN_PRE25:
        # Not all of them. Pre-25 answers parameters 0-133, so anything outside
        # that range is unaddressable, and the status periods are not parameters
        # on this generation at all -- they refuse with a non-zero status and
        # move on LEGACY_SET_PERIOD instead.
        rest_of = {k: v for k, v in universe.items() if k not in cadence}
        readable = {k: v for k, v in rest_of.items()
                    if k <= LEGACY_PARAM_MAX and k not in API_FOR_PERIOD_PARAM
                    and k not in checked_params}
        done = {k: v for k, v in rest_of.items() if k in checked_params}
        rest = {k: v for k, v in rest_of.items()
                if k not in readable and k not in done}
        out = head
        if done:
            out += ("  read over CAN and compared against the declared file: "
                    + ", ".join(done.values()) + ".\n")
        if readable:
            out += ("  not checked here, but READABLE on this generation: "
                    + ", ".join(readable.values()) + ".\n"
                    "  SparkAdmin.read_legacy_param reads any of parameters "
                    "0-133 over CAN, so these need no USB. They are absent from "
                    "the declared file, so there is nothing to compare against; "
                    "add them to sparkmax_motor_defaults.yaml to have the audit "
                    "check them.\n")
        if rest:
            out += ("  still unchecked: " + ", ".join(rest.values()) + ".\n"
                    "  The status periods are not parameters on this generation "
                    "and are measurable only as a cadence; ids above "
                    f"{LEGACY_PARAM_MAX} are not addressable at all.")
        return out
    # 25+ answers READ_PARAMETER for all of 0-255, status periods included, so
    # nothing declared is out of reach here. What is left is what this run did
    # not read, which is a different admission from one it cannot read.
    rest_of = {k: v for k, v in universe.items() if k not in cadence}
    done = {k: v for k, v in rest_of.items() if k in checked_params}
    rest = {k: v for k, v in rest_of.items() if k not in checked_params}
    out = head
    if done:
        out += ("  read over CAN and compared against the declared file: "
                + ", ".join(done.values()) + ".\n")
    if rest:
        out += ("  not checked in this run, and READABLE on this generation: "
                + ", ".join(rest.values()) + ".\n"
                "  SparkAdmin.read_param_pair reads any of parameters 0-255 over "
                "CAN on firmware 25+, so these need no USB.")
    return out


# -- the declared motor configuration -----------------------------------------
# One shared file describes what EVERY motor should be set to; only per-motor
# identity (CAN id, serial, wheel offset) is per-robot. Loaded rather than
# hardcoded so changing a provisioned value is a config edit, not a code change.

# One block per SPARK product inside spark.yaml. The two share a schema exactly
# -- meta, common, by_role, same per-entry fields -- so motor_settings and
# deviating_settings read either one unchanged, and only the values differ.
MOTOR_DEFAULTS_KEYS = {
    "sparkflex": "sparkflex",
    "sparkmax": "sparkmax",
}
# Read when no product has been declared.
MOTOR_DEFAULTS_KEY = MOTOR_DEFAULTS_KEYS["sparkflex"]

# The product whose declared configuration this process reads. The blocks are per
# product and the product is per bus, so the selection can only come from the
# config -- which this module does not read, by design: it takes CAN frames and a
# bus, and knows nothing about which rig it is on. `cli` resolves
# controller_type and hands it down through set_controller_type.
#
# Leaving it unset is not a fallback to a guess. It resolves the Flex block, and
# require_motor_defaults_for still refuses a mismatched pair underneath it.
_CONTROLLER_TYPE = None


def set_controller_type(controller_type):
    """Declare which product's configuration file this process should read.

    Returns the previous value, so a caller that needs to change it for one
    operation can put it back.
    """
    global _CONTROLLER_TYPE
    was = _CONTROLLER_TYPE
    _CONTROLLER_TYPE = controller_type or None
    return was


def motor_defaults_key(controller_type=None):
    """The config key holding a product's declared configuration.

    An unknown product resolves the Flex block rather than raising, because the
    refusal that matters belongs to require_motor_defaults_for, which reads
    meta.applies_to out of whatever was loaded and names both sides. Raising here
    would report a config typo as a missing block.
    """
    ct = controller_type or _CONTROLLER_TYPE
    return MOTOR_DEFAULTS_KEYS.get(ct, MOTOR_DEFAULTS_KEY)


def motor_defaults_file(controller_type=None):
    """How to refer to the declared configuration in a message to an operator."""
    return f"spark.yaml -> {motor_defaults_key(controller_type)}:"


def _defaults_path(controller_type=None):
    from. import config
    return config.config_path()


class MotorDefaultsMissing(RuntimeError):
    """The declared per-motor configuration is not on disk."""


class MotorDefaultsWrongProduct(RuntimeError):
    """The declared configuration was written for a different SPARK product."""


# meta.applies_to in the defaults file, as a controller_type.
_APPLIES_TO_TYPE = {"spark flex": "sparkflex", "spark max": "sparkmax"}


def motor_defaults_product(defaults=None, controller_type=None):
    """The controller_type the declared configuration was written for, or None.

    Read out of the file rather than assumed from the filename, so a file that
    is named for one product and declares another is caught rather than trusted.
    """
    d = (defaults if defaults is not None
         else load_motor_defaults(required=False, controller_type=controller_type))
    applies = ((d or {}).get("meta") or {}).get("applies_to")
    return _APPLIES_TO_TYPE.get(str(applies).strip().lower()) if applies else None


def require_motor_defaults_for(controller_type, defaults=None):
    """Raise when the declared configuration is for another product.

    `spark repair` writes what this file declares, and writing Flex values into a
    MAX would look successful for every value it wrote. This used to be the only
    thing standing between rig-max and that, because the file was selected by a
    module constant; now _defaults_path selects on the product and this is the
    check that the selection landed on a file which agrees.
    """
    if not controller_type:
        return
    resolved = motor_defaults_file(controller_type)
    declared = motor_defaults_product(defaults, controller_type)
    if declared and declared != controller_type:
        raise MotorDefaultsWrongProduct(
            f"the declared motor configuration in {resolved} applies "
            f"to {declared}, and this robot is {controller_type}. Writing it "
            f"would provision a {controller_type} with {declared} values. "
            f"Either meta.applies_to in {resolved} is wrong, or "
            f"controller_type in this config is.")


def load_motor_defaults(path=None, required=True, controller_type=None):
    """The declared configuration for every motor.

    Raises when the file is missing and `required`. It used to return None, and
    `motor_settings` turned that into an empty dict, so `spark audit` compared a
    controller against nothing and reported it clean. A clone that lacks this
    file has to say so.
    """
    from . import config as _config
    path = path or _defaults_path(controller_type)
    key = motor_defaults_key(controller_type)

    def _read(p):
        try:
            import yaml
            with open(p) as fh:
                return yaml.safe_load(fh) or {}
        except FileNotFoundError:
            return None
        except Exception:               # noqa: BLE001 - unreadable, not absent
            return {}

    doc = _read(path)
    if doc is None and _config._INJECTED_PATH:
        # A host application installs its own config and carries no declared
        # tables, and its path need not be a file at all. The tables are per
        # PRODUCT and never per rig, so the packaged copy is the same answer.
        # Only for an INSTALLED config: a path a user named and got wrong must
        # still be an error rather than silently resolving to someone else's.
        doc = _read(_config.SHIPPED_CONFIG)
    if doc is None:
        if required:
            raise MotorDefaultsMissing(
                f"configuration file not found at {path}. Without it "
                "`spark audit` compares against nothing and `spark repair` has "
                "no value to write.\n"
                "  FIX: copy sparklib/data/spark.yaml beside your work and run "
                f"with ${config_env_var()} set to it.")
        return None
    block = doc.get(key)
    if block is None and "common" in doc:
        # A document that is already a product block: an explicit path handed in
        # by a caller, or the older one-file-per-product layout.
        block = doc
    if block is None:
        # A host application that injects its own bus config carries no declared
        # tables, so fall back to the copy shipped with the package. They are
        # per PRODUCT, never per rig, so the shipped copy is the same answer.
        from. import config
        shipped = config.SHIPPED_CONFIG
        if os.path.abspath(path) != os.path.abspath(shipped):
            try:
                import yaml
                with open(shipped) as fh:
                    block = (yaml.safe_load(fh) or {}).get(key)
            except Exception:           # noqa: BLE001 - unreadable, not absent
                block = None
    if block is None:
        if required:
            raise MotorDefaultsMissing(
                f"{path} has no `{key}:` block, so there is nothing declared for "
                "this product. `spark audit` would compare against nothing and "
                "`spark repair` would have no value to write.")
        return None
    return block


def config_env_var():
    from. import config
    return config.ENV_VAR


def motor_settings(role, defaults=None):
    """Resolved {param_id: {name, value, type, deviates}} for one role.

    role is 'steer' or 'drive'. Common settings apply to both; by_role entries
    override per role, which is how the two P 0 gains stay in one file instead of
    being duplicated or flattened into a string nothing can apply.
    """
    d = defaults if defaults is not None else load_motor_defaults()
    if not d:
        return {}
    out = {}
    for e in d.get("common") or []:
        out[e["param_id"]] = {"name": e["name"], "value": e.get("value"),
                              "type": e.get("type"), "tab": e.get("tab"),
                              "deviates": bool(e.get("deviates"))}
    for e in d.get("by_role") or []:
        vals = e.get("values") or {}
        if role in vals:
            out[e["param_id"]] = {"name": e["name"], "value": vals[role],
                                  "type": e.get("type"), "tab": e.get("tab"),
                                  "deviates": bool(e.get("deviates"))}
    return out


def declared_deviations(defaults=None):
    """{param_id: name} the ACTIVE product's file marks as deviating.

    The one source for how many settings an audit could check. Which product
    that is comes from set_controller_type, so a MAX host is scored against the
    MAX file rather than against the Flex fleet's list.
    """
    d = defaults if defaults is not None else load_motor_defaults()
    out = {}
    for group in ("common", "by_role"):
        for e in (d or {}).get(group) or []:
            if e.get("deviates"):
                out[e["param_id"]] = e["name"]
    return out


def deviating_settings(role, defaults=None):
    """Only the settings a factory reset drops -- what re-provisioning must restore."""
    return {pid: s for pid, s in motor_settings(role, defaults).items()
            if s["deviates"]}


def legacy_param_value(reply):
    """A parameter reply's raw word as the type the controller said it is.

    read_legacy_param returns the word undecoded, because the type tag comes
    back with it and a caller comparing against a float has to reinterpret the
    bits rather than read them as an integer.
    """
    if not reply or not reply.get("ok"):
        return None
    raw = reply.get("raw")
    kind = reply.get("type")
    if raw is None:
        return None
    if kind == "bool":
        return bool(raw)
    if kind == "float32":
        return struct.unpack("<f", struct.pack("<I", raw & 0xFFFFFFFF))[0]
    if kind == "int32":
        return struct.unpack("<i", struct.pack("<I", raw & 0xFFFFFFFF))[0]
    return raw


def _declared_matches(declared, actual, param_id=None):
    """Whether a declared setting and a read parameter agree.

    Floats are compared with a tolerance: the file carries decimal literals and
    the controller stores IEEE single, so 0.1 does not round-trip exactly.

    Some settings are declared by enum NAME and read back as the ordinal, so
    BRAKE has to compare equal to 1. Without `param_id` an enum name cannot be
    resolved and the comparison fails closed, reporting a mismatch.
    """
    if isinstance(declared, str) and param_id in PARAM_ENUMS:
        ordinal = param_enum_value(param_id, declared)
        if ordinal is None:
            return False
        declared = ordinal
    if isinstance(declared, bool) or isinstance(actual, bool):
        return bool(declared) == bool(actual)
    if isinstance(declared, float) or isinstance(actual, float):
        try:
            return abs(float(declared) - float(actual)) <= 1e-4
        except (TypeError, ValueError):
            return False
    try:
        return int(declared) == int(actual)
    except (TypeError, ValueError):
        return declared == actual


def legacy_readable_deviations(defaults=None, role="steer"):
    """{param_id: setting} for declared deviations a pre-25 read can reach.

    Excludes ids past the addressable table and the status periods, which are
    not parameters on this generation and refuse the read.
    """
    return {pid: st for pid, st in deviating_settings(role, defaults).items()
            if pid <= LEGACY_PARAM_MAX and pid not in API_FOR_PERIOD_PARAM}


def legacy_deviation_problems(adm, roles, defaults=None, wait=0.4):
    """Declared configuration vs the parameter table, on pre-25 only.

    The audit reported these settings as unverifiable on every generation, which
    was true of 25+ and not of this one: pre-25 answers a read for any of
    parameters 0-133. A controller can otherwise sit in COAST with the declared
    file saying BRAKE and pass a clean audit.

    Returns (problems, checked_param_ids). A parameter the controller refuses is
    reported as unread rather than as a mismatch, because a refusal and a wrong
    value are different faults with different remedies.
    """
    problems, checked = [], set()
    for dev in sorted(roles):
        role = str(roles.get(dev) or "").split("/")[0]
        wanted = legacy_readable_deviations(defaults, role)
        if not wanted:
            continue
        for row in declared_rows(adm, dev, role, defaults, wait=wait,
                                 pre25=True, settings=wanted):
            pid, name = row["param_id"], row["name"]
            if row["state"] == "silent":
                problems.append(
                    f"id {dev} did not answer a read of parameter {pid} "
                    f"({name}), so the declared value cannot be confirmed"
                    + FIX + "`uv run spark status` shows what is broadcasting; a "
                    "controller answering status but not parameters is worth a "
                    "second look before trusting any of its settings")
                continue
            if row["state"] == "refused":
                problems.append(
                    f"id {dev} refused a read of parameter {pid} "
                    f"({name}) with status {row['status']}" + FIX
                    + "a non-zero status is the controller declining, not a "
                    "timeout. Parameters 0-133 are readable on this generation; "
                    "an id outside that range or a status period is not a "
                    "parameter here and refusing is correct")
                continue
            checked.add(pid)
            if row["state"] == "drift":
                problems.append(
                    f"id {dev} {name} (parameter {pid}) reads {row['actual']}, "
                    f"declared {row['declared']}" + FIX + "re-provision this "
                    "controller. Every CAN write on this generation is RAM only, "
                    "so a value that drifted came back from flash and will do it "
                    "again on the next power cycle")
    return problems, checked


def declared_param_value(raw, declared_type):
    """A raw uint32 read off the wire, as the type the declared file says it is.

    READ_PARAMETER carries no type tag, so the type comes from the file. A FLOAT
    read as an integer gives a number no controller holds, which is the mistake
    the simulator's RAM seeding already had to fix.
    """
    if raw is None:
        return None
    kind = str(declared_type or "").upper()
    if kind == "FLOAT":
        return struct.unpack("<f", struct.pack("<I", raw & 0xFFFFFFFF))[0]
    if kind == "BOOL":
        return bool(raw)
    if kind == "INT32":
        return struct.unpack("<i", struct.pack("<I", raw & 0xFFFFFFFF))[0]
    return raw


def declared_raw_value(value, declared_type, param_id=None):
    """A declared value as the raw uint32 a parameter write carries.

    The inverse of declared_param_value, and the only encoder in the package.
    The simulator's provisioned RAM is built from it too, so a controller the
    tests call provisioned holds exactly what a provision run writes.

    Returns None for anything it cannot encode, because a write that guessed
    looks on the wire exactly like one that was asked for.
    """
    if value is None:
        return None
    if isinstance(value, str):
        return param_enum_value(param_id, value)
    kind = str(declared_type or "").upper()
    try:
        if kind == "FLOAT":
            return float_bits(float(value))
        if kind == "BOOL":
            return int(bool(value))
        return int(value) & 0xFFFFFFFF
    except (TypeError, ValueError):
        return None


DECLARED_STATES = ("ok", "drift", "silent", "refused", "unaddressable")


def declared_rows(adm, dev, role, defaults=None, wait=0.4, pre25=False,
                  settings=None):
    """What one controller holds for each declared setting, against the file.

    The one reader behind `spark audit` and `spark provision`, so the two cannot
    disagree about what has drifted. `settings` chooses the scope: the audit
    passes the deviating subset it has always read, a provision run passes the
    whole declared table for one controller.

    `pre25` picks the read dialect and decides what is out of reach there: the
    status periods are not parameters on that generation, and ids above
    LEGACY_PARAM_MAX are not addressable at all.
    """
    wanted = settings if settings is not None else deviating_settings(role, defaults)
    rows = []
    for pid, st in sorted(wanted.items()):
        row = {"param_id": pid, "name": st.get("name"), "type": st.get("type"),
               "deviates": bool(st.get("deviates")), "declared": st.get("value"),
               "raw": None, "actual": None, "wire_type": None,
               "status": None, "state": "ok"}
        if pre25 and (pid > LEGACY_PARAM_MAX or pid in API_FOR_PERIOD_PARAM):
            row["state"] = "unaddressable"
        elif pre25:
            reply = adm.read_legacy_param(dev, pid, wait=wait)
            if reply is None:
                row["state"] = "silent"
            elif not reply.get("ok"):
                row["state"], row["status"] = "refused", reply.get("status")
            else:
                row["raw"], row["wire_type"] = reply.get("raw"), reply.get("type")
                row["actual"] = legacy_param_value(reply)
        else:
            raw = adm.read_param_value(dev, pid, wait=wait)
            if raw is None:
                row["state"] = "silent"
            else:
                row["raw"] = raw
                row["actual"] = declared_param_value(raw, st.get("type"))
        if row["state"] == "ok" and not _declared_matches(row["declared"],
                                                          row["actual"], pid):
            row["state"] = "drift"
        rows.append(row)
    return rows


def read_param_table(adm, dev, wait=0.4, pre25=False):
    """{param_id: raw uint32} and {param_id: type name} for one controller.

    The whole addressable table, not the declared subset. Pre-25 reaches
    parameters 0-133 and carries the type in each reply; 25+ reaches 0-255 and
    reports the types in sixteen frames of its own.
    """
    values, types = {}, {}
    if pre25:
        for pid in range(0, LEGACY_PARAM_MAX + 1):
            if pid in API_FOR_PERIOD_PARAM:
                continue                 # not a parameter on this generation
            reply = adm.read_legacy_param(dev, pid, wait=wait)
            if reply and reply.get("ok"):
                values[pid] = reply.get("raw")
                types[pid] = reply.get("type")
        return values, types
    for start in range(0, PARAM_ID_MAX + 1, 16):
        types.update(adm.param_types(dev, start_id=start, wait=wait) or {})
    for pid in range(0, PARAM_ID_MAX + 1):
        if str(types.get(pid, "")).lower() in ("", "unused"):
            continue
        raw = adm.read_param_value(dev, pid, wait=wait)
        if raw is not None:
            values[pid] = raw
    return values, types


def fleet_param_table(adm, devs, defaults=None, wait=0.4, pre25=False,
                      role_of=None):
    """What the whole fleet agrees on, over the ids the declared file leaves out.

    Returns {"common", "divergent", "types", "declared", "read"}. `common` holds
    the ids every controller answered with the same word; `divergent` maps an id
    to {dev: raw} where they disagree. Declared ids are excluded from both,
    because `sparkflex_motor_defaults.yaml` already answers for them and a
    baseline that repeated them would report one deliberate edit twice.

    Parameter 0 is each controller's own CAN id and is excluded by definition.
    """
    role_of = role_of or (lambda d: "steer")
    declared = set()
    for dev in devs:
        declared |= set(motor_settings(str(role_of(dev) or "").split("/")[0],
                                       defaults))
    per_dev, types = {}, {}
    for dev in devs:
        values, t = read_param_table(adm, dev, wait=wait, pre25=pre25)
        per_dev[dev] = values
        types.update(t)
    # The UNION, so an id one controller failed to answer lands in `divergent`
    # rather than vanishing. A per-device intersection would let a 16-id block
    # lost to a single timeout shorten the table with nothing to show for it.
    seen = set()
    for values in per_dev.values():
        seen |= set(values)
    short = {d: sorted(seen - set(v)) for d, v in per_dev.items() if seen - set(v)}
    common, divergent = {}, {}
    for pid in sorted(seen - declared - {PARAM_CAN_ID}):
        answers = {d: per_dev[d].get(pid) for d in devs}
        if any(v is None for v in answers.values()):
            divergent[pid] = answers
        elif len(set(answers.values())) == 1:
            common[pid] = next(iter(answers.values()))
        else:
            divergent[pid] = answers
    return {"common": common, "divergent": divergent, "types": types,
            "declared": sorted(declared), "read": per_dev, "short": short}


def baseline_param_problems(adm, roles, baseline, wait=0.4, pre25=False):
    """Notes where the fleet no longer holds what the baseline recorded.

    NOTES, not findings. These ids are undeclared, so nothing says what they
    SHOULD be: the baseline records what a bus known to be good was holding, and
    a later disagreement is worth reading rather than acting on. Returns a list
    of strings; the caller must not let them change an exit code.
    """
    want = ((baseline or {}).get("parameters") or {}).get("common") or {}
    if not want:
        return []
    # A reference file that does not parse as {int: int} is a broken baseline,
    # and it must say so rather than take the audit down with a ValueError.
    bad = {k: v for k, v in want.items()
           if not (isinstance(k, int) and isinstance(v, int)
                   and not isinstance(v, bool))}
    if bad:
        sample = ", ".join(f"{k}: {v!r}" for k, v in list(sorted(
            bad.items(), key=lambda kv: str(kv[0])))[:4])
        return [f"the baseline's parameter table has {len(bad)} entr(y/ies) "
                f"that are not whole numbers ({sample}), so it was not "
                "compared. Re-capture it with `uv run spark snapshot --write`"]
    notes = []
    for dev in sorted(roles):
        for pid, expected in sorted(want.items()):
            pid = int(pid)
            if pre25 and (pid > LEGACY_PARAM_MAX or pid in API_FOR_PERIOD_PARAM):
                continue
            if pre25:
                reply = adm.read_legacy_param(dev, pid, wait=wait)
                raw = reply.get("raw") if reply and reply.get("ok") else None
            else:
                raw = adm.read_param_value(dev, pid, wait=wait)
            if raw is None or int(raw) == int(expected):
                continue
            notes.append(
                f"id {dev} parameter {pid} reads {raw}, and the baseline "
                f"recorded {expected} across the whole fleet. Nothing declares "
                "this id, so this is a note and not a fault")
    return notes


def modern_deviation_problems(adm, roles, defaults=None, wait=0.4):
    """Declared configuration vs the parameter table, on firmware 25+.

    Reads every declared deviation over READ_PARAMETER, the status periods
    included, which is what the pre-25 path has to exclude. Returns
    (problems, checked_param_ids), shaped like legacy_deviation_problems.
    """
    problems, checked = [], set()
    for dev in sorted(roles):
        role = str(roles.get(dev) or "").split("/")[0]
        for row in declared_rows(adm, dev, role, defaults, wait=wait):
            pid, name = row["param_id"], row["name"]
            if row["state"] == "silent":
                problems.append(
                    f"id {dev} did not answer a read of parameter {pid} "
                    f"({name}), so the declared value cannot be confirmed"
                    + FIX + "every parameter 0-255 answers on this generation, so "
                    "a silent one is the controller and not the range. `uv run "
                    "spark status` shows whether it is broadcasting at all")
                continue
            checked.add(pid)
            if row["state"] == "drift":
                problems.append(
                    f"id {dev} {name} (parameter {pid}) reads {row['actual']}, "
                    f"declared {row['declared']}" + FIX + "re-provision this "
                    "controller with `uv run spark provision --id N --write`. A "
                    "deviating setting sitting at REV's factory default is a "
                    "config write that went missing, which REV confirm can "
                    "happen on a Flex from one dropped frame")
    return problems, checked
