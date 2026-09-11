"""The one configuration file, and how sparklib finds it.

Everything sparklib reads about your bus lives in a single `spark.yaml`: which
product you have, which netdev the adapter came up as, which device ids are on
it, and what every controller is supposed to be set to. The declared settings
for both SPARK products sit side by side in that file and `controller_type`
picks one, so switching products is a one-line edit and the two tables stay
readable against each other.

Resolution order:

    1. the path handed to load_config()
    2. $SPARKLIB_CONFIG
    3. ./spark.yaml in the working directory
    4. the copy shipped inside the package

Nothing here keys on hostname. A rig with several robots wants a file per robot
and $SPARKLIB_CONFIG set per robot; docs/CAN-SETUP.md carries that recipe.
"""

import os
from types import SimpleNamespace

import yaml

ENV_VAR = "SPARKLIB_CONFIG"
CONFIG_NAME = "spark.yaml"
SHIPPED_CONFIG = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "data", CONFIG_NAME)


def _ns(d):
    """Recursively wrap a dict in SimpleNamespace for dot-access."""
    if isinstance(d, dict):
        return SimpleNamespace(**{k: _ns(v) for k, v in d.items()})
    if isinstance(d, list):
        return [_ns(v) for v in d]
    return d


def config_path(path=None):
    """Where the config is being read from, without loading it."""
    if path:
        return os.path.abspath(path)
    if _INJECTED_PATH:
        return _INJECTED_PATH
    env = os.environ.get(ENV_VAR)
    if env:
        return os.path.abspath(env)
    local = os.path.join(os.getcwd(), CONFIG_NAME)
    if os.path.exists(local):
        return local
    return SHIPPED_CONFIG


def load_raw(path=None):
    """The config as plain dicts, which is what the declared-settings code wants."""
    with open(config_path(path)) as fh:
        return yaml.safe_load(fh) or {}


def load_config(path=None):
    """The config as a namespace tree, which is what the CLI wants."""
    return _ns(load_raw(path))


_CONFIG = None
_INJECTED_PATH = None


def get():
    """The loaded config, read once per process."""
    global _CONFIG
    if _CONFIG is None:
        _CONFIG = load_config()
    return _CONFIG


def reload(path=None):
    """Re-read the config. Used by tests that point at a fixture file."""
    global _CONFIG, _INJECTED_PATH
    _CONFIG = load_config(path)
    _INJECTED_PATH = None
    return _CONFIG


def set_config(namespace, path=None):
    """Install a config a host application built itself.

    An application that already has its own configuration should not keep a
    second copy of the same device ids in a second file. It builds the namespace
    sparklib wants and installs it here. `path` is where generated files -- the
    baseline snapshot, an updated serials block -- should be written.
    """
    global _CONFIG, _INJECTED_PATH
    _CONFIG = namespace
    _INJECTED_PATH = os.path.abspath(path) if path else None
    return _CONFIG


HOST_ENTRY_POINT_GROUP = "sparklib.host"
_HOST_LOADED = False


def load_host():
    """Let an installed application install its own configuration first.

    An application that embeds sparklib already knows its bus, and says so by
    declaring an entry point:

        [project.entry-points."sparklib.host"]
        myrobot = "myrobot.spark:configure"

    Every entry point sparklib ships calls this before reading a config, so the
    `spark` command, the tools and the examples all resolve the same bus. Runs
    once per process and does nothing when no host is installed.
    """
    global _HOST_LOADED
    if _HOST_LOADED:
        return None
    _HOST_LOADED = True
    try:
        from importlib.metadata import entry_points
        found = list(entry_points(group=HOST_ENTRY_POINT_GROUP))
    except Exception:                   # noqa: BLE001 - metadata is optional
        return None
    if not found:
        return None
    if len(found) > 1:
        names = ", ".join(sorted(e.name for e in found))
        raise RuntimeError(
            f"{len(found)} applications claim to configure sparklib ({names}).\n"
            "  FIX: uninstall all but one, or run that application's own command.")
    found[0].load()()
    return found[0].name


def bus_names(conf=None):
    """(spark_bus, cancoder_bus) as this config names them, either may be None."""
    if conf is None:
        conf = get()
    spark = getattr(getattr(conf, "can", None), "interface", None)
    cancoder = getattr(getattr(conf, "cancoder", None), "bus", None)
    return spark, cancoder


def group_bus(group, conf=None):
    """Which netdev a `devices` group lives on."""
    spark, cancoder = bus_names(conf)
    return cancoder if group == "cancoder" else spark


def device_roles(channel=None, conf=None):
    """{device_id: 'group/LABEL'} for the devices on one bus.

    Ids are unique per BUS, not per robot, so two adapters may both carry id 1.
    A map built by flattening every group labels a frame with whichever group
    was read last, which makes a tool listening on the SPARK bus report its
    drive motors as CANcoders. Passing the channel keeps each label on the wire
    that can actually produce it.

    channel=None returns every group, which suits a reader tied to no single
    bus. Two groups on one bus sharing an id are joined with '+', because that
    is a collision worth seeing rather than one worth resolving silently.
    """
    if conf is None:
        conf = get()
    devices = getattr(conf, "devices", None)
    if devices is None or not hasattr(devices, "__dict__"):
        return {}

    roles = {}
    for group, members in vars(devices).items():
        if not hasattr(members, "__dict__"):
            continue
        if channel is not None and group_bus(group, conf) != channel:
            continue
        for label, dev_id in vars(members).items():
            try:
                num = int(dev_id)
            except (TypeError, ValueError):
                continue
            name = f"{group}/{label}"
            roles[num] = f"{roles[num]}+{name}" if num in roles else name
    return roles
