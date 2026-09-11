"""rig-flex presets: the eight controllers this suite is written against.

Ids, roles and serials are the live bus (spark.yaml / devices).
`spark()` folds s0/s1/uid periods into ram and mirrors ram into flash, so a fleet
member is internally consistent by construction: its cadence on the wire is its
config, not a number a test hard-coded.
"""
from __future__ import annotations

from. import frames as F
from.controller import SimSpark
from.faults import SparkBehaviour

ROLES_FLEX = {10: "drive/RB", 11: "steer/RB", 12: "drive/RF", 13: "steer/RF",
                14: "drive/LF", 15: "steer/LF", 16: "drive/LB", 17: "steer/LB"}
SERIALS_FLEX = {10: "C7B24C10", 11: "AB15F6C7", 12: "6B029ADD", 13: "4D9AB6E7",
                  14: "08FE696E", 15: "5400CEBD", 16: "F3003513", 17: "498B2579"}

# rig-max: eight SPARK MAX on firmware 24.0.1, so the PRE-25 side of the split.
# Ids and fingerprints are the real ones out of spark.yaml, because a
# tier that exercises `spark repair` reads devices and serials and
# would otherwise be auditing a fleet no robot has.
ROLES_MAX = {8: "drive/LF", 5: "drive/RF", 1: "drive/LB", 4: "drive/RB",
                7: "steer/LF", 6: "steer/RF", 2: "steer/LB", 3: "steer/RB"}
# api 0x094 fingerprints, not serials: pre-25 broadcasts no UNIQUE_ID at all.
SERIALS_MAX = {8: "10487C0D", 5: "249EB2B0", 1: "66D3BDE5", 4: "A7A98E23",
                  7: "5945A484", 6: "44E08E6A", 2: "481F7CB5", 3: "EABE4C2E"}
BASE02_FIRMWARE = "24.0.1"

BASE03_UNIQUE_ID_PERIOD_MS = 1000
APPENDIX_A_STATUS_1_PERIOD_MS = 20
REV_DEFAULT_STATUS_1_PERIOD_MS = 250


def serial_for(dev: int) -> str:
    return SERIALS_FLEX.get(dev, f"{dev:08X}")


# Appendix A, the declared configuration every motor is provisioned to.
APPENDIX_A_PERIODS = {160: 20, 161: 50, 162: 20, 163: 200,
                      164: 200, 165: 250, 199: 20, 224: 100}

# The pre-25 equivalent: the throttle spark_can.apply_boot_config writes at every
# driver start. Sourced from _SPARKMAX_STATUS_PERIODS_MS rather than copied, so a
# change to the driver's table cannot leave the fixture describing a fleet this
# package never produces.
#
# This exists because "a healthy fleet" has to mean the same thing on both
# generations. It did not: s1_ms defaulted to Appendix A, which is the
# PROVISIONED Flex cadence, while s0_ms defaulted to 10, which is REV's FACTORY
# pre-25 cadence. So a default Flex fleet was provisioned and a default MAX fleet
# was cold, and every pre-25 audit test was written against a bus that had lost
# its configuration -- which is why they asserted that a cold MAX audits clean.
def _pre25_boot_throttle():
    from sparklib.can_bus import _SPARKMAX_STATUS_PERIODS_MS
    return dict(_SPARKMAX_STATUS_PERIODS_MS)


def spark(dev, serial=None, *, s1_ms=APPENDIX_A_STATUS_1_PERIOD_MS, s0_ms=None,
          uid_ms=BASE03_UNIQUE_ID_PERIOD_MS, ram=None, flash=None, behaviour=None,
          full_status=True, **fields) -> SimSpark:
    pre25 = F.generation_for_firmware(fields.get("firmware", "26.1.6")) == F.GEN_PRE25
    throttle = _pre25_boot_throttle() if pre25 else {}
    if s0_ms is None:
        # Provisioned on both generations. Pass s0_ms explicitly to build a
        # controller that is NOT provisioned, which is what a cold bus is.
        s0_ms = throttle.get(0, 10) if pre25 else 10
    r = dict(ram or {})
    r.setdefault(F.PARAM_STATUS_0_PERIOD, int(s0_ms))
    r.setdefault(F.PARAM_STATUS_1_PERIOD, int(s1_ms))
    r.setdefault(F.PARAM_CAN_ID, dev)
    periods = dict(fields.pop("periods_ms", {}) or {})
    periods.setdefault(F.API_UNIQUE_ID, int(uid_ms))
    # A healthy provisioned controller broadcasts what the configuration asks
    # for, which is Status 0 through 9. Seeding only 0 and 1 modelled rig-flex as
    # it is TODAY -- statuses 2-9 silent after a reboot -- and a fixture that
    # reproduces the defect cannot be used to detect it.
    if full_status:
        for pid, api in F.API_FOR_PERIOD_PARAM.items():
            if api in (F.API_STATUS_0, F.API_STATUS_1):
                continue
            ms = APPENDIX_A_PERIODS.get(pid)
            if ms:
                periods.setdefault(api, int(ms))
    # Pre-25 status periods drive off no parameter id here, so seed the cadence
    # directly or s0_ms is ignored. Keyed on firmware, which is the axis.
    if pre25:
        periods.setdefault(F.API_LEGACY_STATUS_0, int(s0_ms))
        periods.setdefault(F.API_LEGACY_STATUS_1, int(s1_ms))
        # The rest of the boot throttle, so a provisioned MAX broadcasts the
        # whole table and not just the two frames a test happened to name.
        for idx, ms in throttle.items():
            if idx in (0, 1) or idx == F.LEGACY_ABSENT_FRAME_INDEX:
                continue        # 0/1 set above; frame 4 never broadcasts here
            periods.setdefault(F.API_LEGACY_STATUS_0 + idx, int(ms))
        periods.pop(F.API_UNIQUE_ID, None)
    if fields.get("legacy_beacon"):
        periods.setdefault(F.API_LEGACY_STATUS_0, int(s0_ms))
    return SimSpark(dev=dev, serial=serial or serial_for(dev), ram=r,
                    flash=dict(flash if flash is not None else r),
                    periods_ms=periods,
                    behaviour=behaviour or SparkBehaviour(), **fields)


def _baseline_param_table(name="spark-baseline.yaml"):
    """{param_id: raw} the checked-in baseline records for the undeclared ids.

    A healthy fixture has to hold what the audit compares it against, and the
    audit now compares two files: the declared defaults for the ids they name,
    and the baseline for the ids they do not. Seeding only the first left a
    healthy simulated bus producing a baseline note for all 138 of the second.
    """
    import pathlib
    import yaml
    from sparklib import admin as sa
    from sparklib import config as spark_config
    path = pathlib.Path(spark_config.SHIPPED_CONFIG).with_name(name)
    try:
        doc = yaml.safe_load(path.read_text()) or {}
    except OSError:
        return {}
    common = ((doc.get("parameters") or {}).get("common")) or {}
    return {int(k): int(v) for k, v in common.items()
            if isinstance(v, int) and not isinstance(v, bool)}


def build_fleet(ids=range(10, 18), **kw):
    """rig-flex's eight, provisioned to the declared SparkFlex configuration.

    Firmware 25+ answers every parameter 0-255, so an unseeded controller here is
    one that lost its configuration, and a tier built on it would assert that a
    fleet at factory defaults audits clean. Pass provisioned=False for that case
    on purpose.
    """
    seed = kw.pop("provisioned", True)
    out = []
    for d in ids:
        ram = dict(kw.pop("ram", None) or {})
        if seed:
            declared = dict(_baseline_param_table())
            declared.update(declared_ram(ROLES_FLEX.get(d, "steer/LF")))
            # Status 0 and 1 Period are left to spark(), which derives them from
            # s0_ms and s1_ms. Seeding them here let the parameter say 20 while
            # the wire ran at 37, which no controller does.
            for pid in (F.PARAM_STATUS_0_PERIOD, F.PARAM_STATUS_1_PERIOD):
                declared.pop(pid, None)
            ram = {**declared, **ram}
        out.append(spark(d, ram=ram, **kw))
    return out


def build_fleet_pre25(ids=None, **kw):
    """rig-max's eight, on pre-25 firmware and at this package's boot throttle.

    Not build_fleet(controller_type=...): the PRODUCT does not decide the frame
    layout, the FIRMWARE does, and passing only the product would build eight
    controllers broadcasting 0x2E0 while calling themselves SPARK MAX. That is
    the exact confusion this fleet exists to catch, so the firmware is set here
    and the product follows it.
    """
    kw.setdefault("firmware", BASE02_FIRMWARE)
    kw.setdefault("controller_type", "sparkmax")
    devs = sorted(ROLES_MAX) if ids is None else list(ids)
    seed = kw.pop("provisioned", True)
    out = []
    for d in devs:
        ram = dict(kw.pop("ram", None) or {})
        if seed:
            ram = {**_pre25_declared_ram(ROLES_MAX.get(d, "steer/LF")), **ram}
        out.append(spark(d, serial=SERIALS_MAX.get(d), ram=ram, **kw))
    return out


def _pre25_declared_ram(role):
    """The declared settings a PROVISIONED pre-25 controller holds."""
    return _declared_ram(role, "sparkmax", pre25=True)


def declared_ram(role, product="sparkflex"):
    """The declared settings a PROVISIONED firmware-25+ controller holds."""
    return _declared_ram(role, product, pre25=False)


def _declared_ram(role, product, pre25):
    """The declared configuration, as the raw words a controller would hold.

    A healthy fleet has to mean the same thing on both generations. Pre-25 is
    bounded: ids above LEGACY_PARAM_MAX are unaddressable and the status periods
    are not parameters there. Firmware 25+ answers every id 0-255, measured on
    rig-flex, so a 25+ controller holds the whole declared file.

    Sourced from the same file the audit compares against, because a provisioned
    controller is BY DEFINITION one whose parameters match the declared file;
    copying the values here would only let the two drift.
    """
    from sparklib import admin as sa
    defaults = sa.load_motor_defaults(controller_type=product)
    group = role.split("/")[0]
    ram = {}
    for pid, st in sa.motor_settings(group, defaults).items():
        if pre25 and (pid > sa.LEGACY_PARAM_MAX or pid in F.API_FOR_PERIOD_PARAM):
            continue                      # not addressable, or not a parameter here
        # Through the production encoder, not a copy. RAM holds the raw 32-bit
        # word: a float goes in as its IEEE bits and an enum as its ordinal, and
        # a second implementation here would let a controller the tests call
        # provisioned hold different words than a provision run writes.
        raw = sa.declared_raw_value(st.get("value"), st.get("type"), pid)
        if raw is not None:
            ram[pid] = raw
    return ram


def provisioned(dev, **kw) -> SimSpark:
    """Appendix A: STATUS_1 at 20 ms."""
    kw.setdefault("s1_ms", APPENDIX_A_STATUS_1_PERIOD_MS)
    return spark(dev, **kw)


def factory(dev, **kw) -> SimSpark:
    """REV defaults: STATUS_1 at 250 ms, i.e. a controller that lost its config."""
    kw.setdefault("s1_ms", REV_DEFAULT_STATUS_1_PERIOD_MS)
    return spark(dev, **kw)
