from can.interface import Bus
from can import Message, CanError
from threading import Thread, Event, Lock
import time

from . import controller as spark_controller


"""
Description: Library for providing objects for controlling and receving feedback
from multiple Spark Max / Spark Flex Controllers via CAN.
Author: Jacob Peskuski, Gabriel Carlson
"""


# SparkMax STATUS broadcast periods (ms). REVLib defaults push enough RX on
# an 8-motor bus to risk gs_usb TX wedging under sustained load -- throttle
# the unused frames hard and the used ones to ~20 Hz. SparkFlex uses different
# status APIs and is left at defaults. See test/BASE01_STATUS.md.
_SPARKMAX_STATUS_PERIODS_MS = {
    0: 50, 1: 100, 2: 100, 3: 500, 4: 500, 5: 1000, 6: 1000,
}

# FRC CAN arbitration-ID fields identifying a REV motor controller. Used to tell
# SPARK traffic apart from CANcoder/other frames when auditing device IDs.
_REV_DEVICE_TYPE_MOTOR = 2
_REV_MANUFACTURER = 5

# Factory-default SPARK device ID. Never a valid configured ID -- a controller
# broadcasting here has lost its flashed identity.
SPARK_DEFAULT_ID = 0
# Valid configurable SPARK device-ID range (6-bit field; 0 reserved as above).
SPARK_ID_MIN, SPARK_ID_MAX = 1, 62


class SparkBus:
    def __init__(self, channel='can0', bustype='socketcan', bitrate=1000000):
        """
        Object for sending and receiving Spark Max / Spark Flex CAN messages.

        @param channel: Serial channel the CAN interface is on.
        @type channel: str
        @param bustype: Type of bus, set to 'None' to let it be resolved automatically
        from the default configuration.
        @type bustype: str
        @param bitrate: Rate at which bits are sent through the CAN bus.
        @type bitrate: int
        """
        # init CAN bus
        self.channel = channel
        self.bustype = bustype
        self.bitrate = bitrate
        self.bus = Bus(channel=channel, bustype=bustype, bitrate=bitrate)
        self._bus_lock = Lock()
        self.tx_error_count = 0
        self.tx_frames = 0
        self._closed = False

        # dictionary to store all of the controllers
        self.controllers = {}

        # array of all the currently added CAN IDs (Used for heartbeat)
        self.can_ids = []

        # Every REV motor-controller device ID seen broadcasting, configured or
        # not. A SparkFlex that loses its flash reverts to the factory default
        # ID 0, so an unconfigured ID here means a controller has dropped its
        # identity -- see observed_spark_ids() and CAN_BRINGUP.md section 6.
        self.observed_ids = set()

        # Start heartbeat thread
        self.heartbeat_enabled = True
        self.enable_id_array = [0, 0, 0, 0, 0, 0, 0, 0]
        self._heartbeat_started = Event()
        self.heartbeat_thread = Thread(target=self._heartbeat_runnable, daemon=True)
        self.heartbeat_thread.start()

        # Start monitor thread
        self.monitor_thread = Thread(target=self.bus_monitor, daemon=True)
        self.monitor_thread.start()

    def init_controller(self, canID, controller_type=spark_controller.SPARK_MAX,
                        clear_sticky_faults=None):
        """
        Initializes a SPARK controller for sending and receiving messages.

        @param canID: CAN ID of the controller
        @param controller_type: spark_controller.SPARK_MAX or
                                spark_controller.SPARK_FLEX. Defaults to SPARK_MAX
                                for backward compatibility.
        @param clear_sticky_faults: if True, sends a Clear Faults frame immediately
                                    after creating the controller. Defaults to True
                                    for SparkFlex (latches faults across power cycles)
                                    and False for SparkMax (b7c06d1 behavior).
        @return: Controller object pointer
        """
        controller = spark_controller.Controller(self, canID, controller_type)
        self.controllers[canID] = controller

        self.can_ids.append(canID)

        # update enable_id_array
        self._update_heartbeat_array()

        self.apply_boot_config(controller, clear_sticky_faults=clear_sticky_faults)

        return controller

    def apply_boot_config(self, controller, clear_sticky_faults=None):
        """Send the per-power-cycle setup a freshly booted SPARK needs.

        Split out of init_controller because BOTH of these are volatile and are
        lost whenever the controller loses power -- not just at process start:

          * sticky faults (SparkFlex latches them across power cycles, so a
            browned-out controller comes back faulted and silently won't drive)
          * STATUS frame periods (see set_periodic_frame_period -- REVLib defaults
            push enough RX to wedge the gs_usb dongle)

        Safe to re-send at any time; call it on power restore / CAN re-detection
        instead of re-running init_controller, which would rebuild the Controller
        object and orphan the SwerveModule's reference to it.
        """
        if clear_sticky_faults is None:
            clear_sticky_faults = (controller.controller_type
                                   == spark_controller.SPARK_FLEX)
        if clear_sticky_faults:
            controller.clear_faults()

        if controller.controller_type == spark_controller.SPARK_MAX:
            for frame_idx, period_ms in _SPARKMAX_STATUS_PERIODS_MS.items():
                controller.set_periodic_frame_period(frame_idx, period_ms)

    def reconfigure_controllers(self, clear_sticky_faults=True):
        """Re-apply apply_boot_config() to every registered controller.

        The recovery entry point after the SPARKs have rebooted (base power
        restored, CAN adapter replugged). Defaults to clearing sticky faults for
        every family -- after a power cut the SparkMax default of "leave them
        latched" is not what we want either, since a brownout fault would keep
        the controller refusing output with nothing on the host to say so.
        """
        for controller in list(self.controllers.values()):
            try:
                self.apply_boot_config(controller,
                                       clear_sticky_faults=clear_sticky_faults)
            except Exception as e:
                print(f"[SparkBus] reconfigure of ID {controller.id} failed: {e}")

    def send_msg(self, msg):
        """
        Sends msg to controllers via CAN bus.

        @param msg: CAN message to be sent to controller.
        @type msg: Message
        """
        try:
            with self._bus_lock:
                if self._closed:
                    return
                self.bus.send(msg)
                self.tx_frames += 1
        except CanError:
            self.tx_error_count += 1
            pass  # TX buffer full -- drop frame, don't flood stdout

    def netdev_tx_packets(self):
        """Frames this interface has actually put on the wire, or None if unreadable.

        The kernel's own counter, not python-can's. A wedged gs_usb accepts frames and
        never completes them, so `tx_frames` climbs while this does not -- which is the
        only in-process signal that separates the two.
        """
        try:
            with open(f"/sys/class/net/{self.channel}/statistics/tx_packets") as fh:
                return int(fh.read().strip())
        except (OSError, ValueError):
            return None

    def bus_monitor(self):
        """
        Thread for monitoring the bus for receivable messages.
        """

        while not self._closed:
            try:
                with self._bus_lock:
                    if self._closed:
                        return
                    message = self.bus.recv(0)
            except (CanError, OSError):
                # Socket closed by shutdown() or transient driver hiccup.
                # Exit if closed, otherwise back off and retry.
                if self._closed:
                    return
                time.sleep(0.001)
                continue
            except Exception:
                if self._closed:
                    return
                time.sleep(0.001)
                continue
            if message is None:
                time.sleep(0.001)
                continue

            self.route_frame(message)

    def route_frame(self, message):
        """Fan one received frame out to the controller it belongs to.

        Extracted from `bus_monitor` so it can be tested without a socket. The
        thread loop was the only caller and the routing was never exercised
        directly, which is how a product-keyed api match survived: a SPARK MAX
        on firmware 25+ broadcasts 0x2E0/0x2E1, matched neither 0x60 nor 0x61,
        and had no status frame captured at all.
        """
        # get api (class and index) and id of device from the message id
        api = (message.arbitration_id & 0x0000FFC0) >> 6
        devID = (message.arbitration_id & 0x0000003F)
        devType = (message.arbitration_id & 0x1F000000) >> 24
        mfg = (message.arbitration_id & 0x00FF0000) >> 16

        # Record presence before the configured-controller filter below, so
        # a squatter on an unexpected ID is still visible to the ID audit.
        if devType == _REV_DEVICE_TYPE_MOTOR and mfg == _REV_MANUFACTURER:
            self.observed_ids.add(devID)

        if devID in self.controllers.keys():
            ctrl = self.controllers[devID]
            # Liveness stamp: ANY frame from this device proves it's powered
            # and on the bus right now. Read by silent_ids() / the DriveTrain
            # power watchdog.
            ctrl._last_seen = time.monotonic()
            # Accept both generations: a 25+ SPARK MAX sends 0x2E0/0x2E1, which
            # _CONFIGS[controller_type] does not match.
            ctrl.note_frame_api(api)
            if api in (ctrl._s0_api, 0x2E0):
                ctrl._status0_raw = bytes(message.data)
            elif api in (ctrl._s1_api, 0x2E1):
                ctrl._status1_raw = bytes(message.data)
            # Decode structured statuses
            if api in ctrl.statuses and ctrl.statuses[api] is not None:
                ctrl.statuses[api].decode(message.data)

    def enable_heartbeat(self):
        """Enables heartbeat runnable for sending heartbeat message to CAN Bus."""
        self.heartbeat_enabled = True

    # FIRST CAN Device Specification: "Devices should disable immediately when
    # receiving the Disable message (arbID 0)." Zero-length, no device id, and
    # the only broadcast every actuator on the bus must act on.
    DISABLE_BROADCAST_ARB = 0x00000000

    def broadcast_disable(self, repeats=3, stop_heartbeat=True):
        """Tell every actuator on the bus to stop driving, now.

        The frame is immediate, reaches devices whose id is wrong or duplicated,
        and needs no per-controller addressing, which is what makes it the right
        thing to send on a stop edge rather than a per-id sweep.

        THE HEARTBEAT STOPS FIRST, and that is not optional by default. This
        object's own heartbeat thread asserts "enabled" every 20 ms, so a
        disable sent underneath it is overridden before the next status frame
        and the motor keeps turning. Measured: see docs/SPARKMAX-BRINGUP.md.
        Pass stop_heartbeat=False only to send the frame for its own sake, never
        to stop something.

        Sent more than once because a stop is the one message that must not be
        lost to a single dropped frame. It is an annunciator, not the stop: the
        stop is the break in the motor rail.
        """
        if stop_heartbeat:
            # Before the frames, not after: the last heartbeat has to be older
            # than the disable or the controller re-arms between the two.
            self.heartbeat_enabled = False
        sent = 0
        for _ in range(max(1, repeats)):
            try:
                with self._bus_lock:
                    self.bus.send(Message(arbitration_id=self.DISABLE_BROADCAST_ARB,
                                          data=[], is_extended_id=True))
                sent += 1
            except Exception as e:
                print(f"[SparkBus] disable broadcast failed: {e} -- FIX: the "
                      "controllers still disable when the enable heartbeat "
                      "stops, but not immediately. Cut motor power by hand if "
                      "anything is moving.")
                break
        return sent

    def disable_heartbeat(self):
        """Disables heartbeat runnable for sending heartbeat message to CAN Bus.

        Broadcasts the spec's disable first, so controllers stop on this frame
        rather than waiting out their own heartbeat timeout. broadcast_disable
        now clears the flag itself; the assignment stays so this reads as the
        one place that owns the decision.
        """
        self.broadcast_disable()
        self.heartbeat_enabled = False

    def wait_for_heartbeat(self, timeout=2.0):
        """Block until the heartbeat thread has sent its first frame."""
        return self._heartbeat_started.wait(timeout)

    def shutdown(self, zero_outputs=True, join_timeout=1.0):
        """Stop heartbeat + close the bus. Idempotent."""
        if self._closed:
            return
        if zero_outputs:
            deadline = time.perf_counter() + 0.2
            while time.perf_counter() < deadline:
                for controller in list(self.controllers.values()):
                    try:
                        controller.percent_output(0.0)
                    except Exception:
                        pass
                time.sleep(0.02)
        self.heartbeat_enabled = False
        self._closed = True
        try:
            with self._bus_lock:
                self.bus.shutdown()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.shutdown()

    def set_heartbeat_ids(self, can_ids):
        """Override which CAN IDs are marked enabled in the SPARK heartbeat."""
        self.can_ids = list(can_ids)
        self._update_heartbeat_array()

    def heartbeat_payload_hex(self):
        """Return current heartbeat payload as hex for diagnostics."""
        return bytes(self.enable_id_array).hex().upper()

    def live_ids(self):
        """CAN IDs currently broadcasting STATUS_0 (set by bus_monitor)."""
        return [cid for cid, c in self.controllers.items()
                if c._status0_raw is not None]

    def silent_ids(self, window_s):
        """Registered CAN IDs with no frame in the last `window_s`, sorted.

        Unlike live_ids() this is a NOW question, so it detects a controller that
        was alive and has since lost power. Empty list = the whole bus is healthy.
        """
        return sorted(cid for cid, c in self.controllers.items()
                      if not c.is_live(window_s))

    def observed_spark_ids(self):
        """Every REV motor-controller device ID seen on the bus so far.

        Superset of live_ids(): includes IDs that aren't configured controllers,
        which is what makes a factory-reset SPARK squatting on ID 0 detectable.
        """
        return sorted(self.observed_ids)

    def _update_heartbeat_array(self):
        """Helper to update the heartbeat CAN message when a controller is added."""
        enable_array = ['0'] * 64
        for id in self.can_ids:
            enable_array[id] = '1'
        enable_array.reverse()
        self.enable_id_array = [0, 0, 0, 0, 0, 0, 0, 0]
        self.enable_id_array[7] = int("".join(enable_array[0:8]), 2)
        self.enable_id_array[6] = int("".join(enable_array[8:16]), 2)
        self.enable_id_array[5] = int("".join(enable_array[16:24]), 2)
        self.enable_id_array[4] = int("".join(enable_array[24:32]), 2)
        self.enable_id_array[3] = int("".join(enable_array[32:40]), 2)
        self.enable_id_array[2] = int("".join(enable_array[40:48]), 2)
        self.enable_id_array[1] = int("".join(enable_array[48:56]), 2)
        self.enable_id_array[0] = int("".join(enable_array[56:64]), 2)

    # Multithreaded runnable to continuously send heartbeat without blocking main thread.
    def _heartbeat_runnable(self):
        time.sleep(1.0)  # wait for bus and devices to settle after interface reset
        while not self._closed:
            if self.heartbeat_enabled:
                msg = Message(
                    arbitration_id=0x02052C80,
                    data=self.enable_id_array,
                    is_extended_id=True,
                )
                self.send_msg(msg)
                self._heartbeat_started.set()
            time.sleep(.02)
