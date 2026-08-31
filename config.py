"""Configuration for LocalPrint.

Every setting is read from `localprint.conf` next to this file, or from the
environment (which takes precedence). There are deliberately no built-in
defaults: the app refuses to start rather than guess a printer name, a port
or a network range.

Copy `localprint.conf.example` to `localprint.conf` to get started.
"""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONFIG_NAME = "localprint.conf"
TEMPLATE_NAME = "localprint.conf.example"

# Settings the application itself needs. The deployment settings used by
# deploy.ps1 (host, user, remote path) live in the same file but are not
# read here.
REQUIRED_KEYS = (
    "LOCALPRINT_PRINTER",
    "LOCALPRINT_PORT",
    "LOCALPRINT_LAN_PREFIX",
    "LOCALPRINT_MAX_UPLOAD_MB",
    "LOCALPRINT_LP_TIMEOUT_SECONDS",
    "LOCALPRINT_MIN_COPIES",
    "LOCALPRINT_MAX_COPIES",
)


class ConfigError(RuntimeError):
    """Raised when the configuration is missing, incomplete or malformed."""


def _unquote(value):
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def parse(path):
    """Read a KEY=value file, ignoring blank lines and # comments."""
    values = {}
    for number, raw in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue

        key, separator, value = line.partition("=")
        if not separator:
            raise ConfigError(
                f"{path}:{number}: expected KEY=value, found {line!r}"
            )
        values[key.strip()] = _unquote(value.strip())
    return values


def _config_path():
    override = os.environ.get("LOCALPRINT_CONFIG")
    return Path(override) if override else ROOT / CONFIG_NAME


CONFIG_PATH = _config_path()
_FILE = parse(CONFIG_PATH) if CONFIG_PATH.is_file() else {}


def _require(key):
    """Look the key up in the environment, then the config file."""
    value = os.environ.get(key, _FILE.get(key, "")).strip()
    if value:
        return value

    if CONFIG_PATH.is_file():
        hint = f"Add it to {CONFIG_PATH}"
    else:
        hint = (
            f"No config file at {CONFIG_PATH}. "
            f"Copy {TEMPLATE_NAME} to {CONFIG_NAME} and fill it in"
        )
    raise ConfigError(f"{key} is not set. {hint}, or set it in the environment.")


def _require_int(key, minimum=None):
    raw = _require(key)
    try:
        value = int(raw)
    except ValueError:
        raise ConfigError(
            f"{key} must be a whole number, found {raw!r}."
        ) from None

    if minimum is not None and value < minimum:
        raise ConfigError(f"{key} must be at least {minimum}, found {value}.")
    return value


def _flag(key):
    """An optional switch. Absent means off."""
    value = os.environ.get(key, _FILE.get(key, "")).strip().lower()
    return value in {"1", "true", "yes", "on"}


PRINTER = _require("LOCALPRINT_PRINTER")
PORT = _require_int("LOCALPRINT_PORT", minimum=1)

# The trailing dot matters: "10.1.2" would also match 10.1.22.x.
LAN_PREFIX = _require("LOCALPRINT_LAN_PREFIX")
if not LAN_PREFIX.endswith("."):
    raise ConfigError(
        f"LOCALPRINT_LAN_PREFIX must end with a dot, found {LAN_PREFIX!r} "
        '(for example "10.1.2.").'
    )

MAX_UPLOAD_MB = _require_int("LOCALPRINT_MAX_UPLOAD_MB", minimum=1)
MAX_UPLOAD_SIZE = MAX_UPLOAD_MB * 1024 * 1024

LP_TIMEOUT_SECONDS = _require_int("LOCALPRINT_LP_TIMEOUT_SECONDS", minimum=1)

MIN_COPIES = _require_int("LOCALPRINT_MIN_COPIES", minimum=1)
MAX_COPIES = _require_int("LOCALPRINT_MAX_COPIES", minimum=1)
if MAX_COPIES < MIN_COPIES:
    raise ConfigError(
        f"LOCALPRINT_MAX_COPIES ({MAX_COPIES}) is below "
        f"LOCALPRINT_MIN_COPIES ({MIN_COPIES})."
    )

# What the printing pipeline can handle, rather than a site preference.
PDF_EXTENSIONS = {".pdf"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png"}

# Development switches, normally left unset.
FAKE_PRINTER = _flag("LOCALPRINT_FAKE_PRINTER")
DEBUG = _flag("LOCALPRINT_DEBUG")
