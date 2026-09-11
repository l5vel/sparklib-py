"""sparklib -- REV SPARK MAX and SPARK Flex over raw CAN, from Python.

No WPILib, no REVLib, no roboRIO. A SocketCAN interface and python-can are the
whole runtime. Everything else here is protocol knowledge: which frame carries
what on which firmware, which parameter id means what, and which failures look
identical on the wire until you know the difference.

    from sparklib import SparkBus, SPARK_FLEX

    bus = SparkBus(channel="can0")
    motor = bus.init_controller(1, SPARK_FLEX)
    motor.percent_output(0.1)

Administration, auditing and repair have no API here: they are the `spark`
command. Start with `spark status`.

The frame layout keys on FIRMWARE VERSION, not on which product you bought.
docs/PROTOCOL.md says why that distinction has been expensive.
"""

__version__ = "0.1.0"

__all__ = [
    "SparkBus",
    "Controller",
    "SPARK_MAX",
    "SPARK_FLEX",
    "SparkAdmin",
    "CanNetdev",
    "load_config",
]


def __getattr__(name):
    if name == "SparkBus":
        from .can_bus import SparkBus
        return SparkBus
    if name in ("Controller", "SPARK_MAX", "SPARK_FLEX"):
        from . import controller
        return getattr(controller, name)
    if name == "SparkAdmin":
        from .admin import SparkAdmin
        return SparkAdmin
    if name == "CanNetdev":
        from .netdev import CanNetdev
        return CanNetdev
    if name == "load_config":
        from .config import load_config
        return load_config
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
