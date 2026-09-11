"""Phase 0c: which arbitration base does the SparkFlex set-period frame use?

spark_controller.set_periodic_frame_period() hardcodes 0x02051800, the SparkMax
STATUS base (api 0x60). SparkFlex STATUS lives at api 0x2E0 (0x0205B800). This
sends the same 2-byte set-period payload at each base to one device and reports
which one makes the frame start broadcasting.

RAM-only, reversible by a power cycle. Sends no setpoints and starts no
heartbeat, so the controller stays disabled and cannot actuate.
"""
import sys, time, can

CH, DEV, FRAME, PERIOD = "can0", 17, 2, 20
FLEX_STATUS_API = 0x2E0
MAX_STATUS_API = 0x060


def count(bus, api, dev, seconds):
    target = 0x02050000 | (api << 6) | dev
    n = 0
    end = time.time() + seconds
    while time.time() < end:
        m = bus.recv(timeout=max(0.0, end - time.time()))
        if m is not None and m.arbitration_id == target:
            n += 1
    return n


def set_period(bus, status_api, dev, frame, period_ms):
    arb = 0x02050000 | ((status_api + frame) << 6) | dev
    data = bytes([period_ms & 0xFF, (period_ms >> 8) & 0xFF])
    bus.send(can.Message(arbitration_id=arb, data=data, is_extended_id=True))
    return arb


def main():
    bus = can.Bus(interface="socketcan", channel=CH)
    try:
        target_api = FLEX_STATUS_API + FRAME
        before = count(bus, target_api, DEV, 2.0)
        print(f"dev {DEV}: 0x{target_api:03X} frames in 2 s BEFORE = {before}")

        arb = set_period(bus, MAX_STATUS_API, DEV, FRAME, PERIOD)
        print(f"\nsent set-period at SparkMax base  arb=0x{arb:08X}")
        time.sleep(0.5)
        after_max = count(bus, target_api, DEV, 2.0)
        print(f"  0x{target_api:03X} frames in 2 s = {after_max}")

        arb = set_period(bus, FLEX_STATUS_API, DEV, FRAME, PERIOD)
        print(f"\nsent set-period at SparkFlex base arb=0x{arb:08X}")
        time.sleep(0.5)
        after_flex = count(bus, target_api, DEV, 2.0)
        print(f"  0x{target_api:03X} frames in 2 s = {after_flex}")

        print("\nverdict:")
        if after_max > before:
            print("  SparkMax base 0x02051800 WORKS on SparkFlex")
        elif after_flex > before:
            print("  SparkFlex base 0x0205B800 works; the hardcoded SparkMax base does NOT")
            print("  -> spark_controller.set_periodic_frame_period is a no-op on Flex")
        else:
            print("  NEITHER base started the frame -- set-period is not accepted this way")
        return 0
    finally:
        bus.shutdown()


if __name__ == "__main__":
    sys.exit(main())
