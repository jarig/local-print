"""Printer abstraction: builds and runs the CUPS `lp` command.

Print options (colour mode, quality, paper) are not configured anywhere:
they are discovered from the printer itself with `lpoptions -l`, so the UI
offers exactly what this printer can actually do.
"""
import re
import subprocess
import time

import config


class PrintError(Exception):
    """Raised when a print job could not be queued."""


# --------------------------------------------------------------------------
# Discovering what the printer can do
# --------------------------------------------------------------------------

# `lpoptions -l` prints one option group per line:
#
#     ColorModel/Output Mode: *RGB Gray
#
# i.e. keyword, human-readable group name, then the choices with the
# printer's own default marked by an asterisk.
OPTION_LINE = re.compile(r"^([A-Za-z0-9_.\-]+)/([^:]*):\s*(.+)$")

# Which discovered groups to surface, in the order they appear in the form.
# Anything else the printer reports is ignored: PPDs expose plenty of
# internal knobs (PageRegion, OutputBin and friends) that are not meaningful
# choices for someone printing a document from their phone.
#
# `Duplex` is deliberately absent -- the form has a dedicated double-sided
# toggle, and offering the same setting twice invites the two to disagree.
SUPPORTED_OPTIONS = (
    ("ColorModel", "Colour"),
    ("cupsPrintQuality", "Quality"),
    ("PageSize", "Paper size"),
    ("MediaType", "Paper type"),
    ("InputSlot", "Paper source"),
    ("Resolution", "Resolution"),
)

# PPDs are free to name their choices whatever they like and, unlike the
# group names, those choices carry no human-readable text at all. Spell out
# the standard ones; everything else falls back to tidying up the keyword.
CHOICE_LABELS = {
    "ColorModel": {
        "RGB": "Colour",
        "CMY": "Colour",
        "CMYK": "Colour",
        "RGBW": "Colour",
        "Gray": "Black & white",
        "Grayscale": "Black & white",
        "KGray": "Black & white",
        "Black": "Black & white",
        "W": "Black & white",
    },
    "InputSlot": {
        "Auto": "Automatic",
        "Main": "Main tray",
        "Rear": "Rear tray",
        "Manual": "Manual feed",
    },
}

# Canon and friends prefix their media types, e.g. Com.canon.mtinkjeta.
VENDOR_PREFIX = re.compile(r"^Com\.[A-Za-z0-9]+\.(?:mt)?")

# Discovery costs a subprocess call, so hold the answer for a while. A
# printer's capabilities change about as often as the printer does.
CACHE_SECONDS = 300

_cache = {"at": 0.0, "options": None}

# Stands in for a real printer during development and in the test suite.
FAKE_OPTIONS = """PageSize/Media Size: *A4 A5 Letter Legal 4x6 Custom.WIDTHxHEIGHT
InputSlot/Media Source: *Auto Main Rear
cupsPrintQuality/cupsPrintQuality: Draft *Normal High
ColorModel/Output Mode: *RGB Gray
Duplex/Duplex: *None DuplexNoTumble DuplexTumble
OutputBin/OutputBin: *FaceUp
"""


def label_for_choice(keyword, value):
    """A human-readable name for one choice of one option."""
    known = CHOICE_LABELS.get(keyword, {})
    if value in known:
        return known[value]

    text = VENDOR_PREFIX.sub("", value).replace(".", " ").strip()
    if not text:
        return value
    return text[0].upper() + text[1:]


def parse_options(text):
    """Turn `lpoptions -l` output into {keyword: {choices, default}}."""
    discovered = {}

    for line in text.splitlines():
        match = OPTION_LINE.match(line.strip())
        if not match:
            continue

        keyword, _group_label, remainder = match.groups()
        choices = []
        default = None

        for token in remainder.split():
            is_default = token.startswith("*")
            value = token[1:] if is_default else token

            # "Custom.WIDTHxHEIGHT" is a placeholder for a custom page size
            # rather than something anyone can pick from a list.
            if not value or value.startswith("Custom."):
                continue

            choices.append(value)
            if is_default:
                default = value

        # A group with a single choice is not a choice.
        if len(choices) >= 2:
            discovered[keyword] = {
                "choices": choices,
                "default": default or choices[0],
            }

    return discovered


def _run_lpoptions():
    if config.FAKE_PRINTER:
        return FAKE_OPTIONS

    result = subprocess.run(
        ["lpoptions", "-p", config.PRINTER, "-l"],
        capture_output=True,
        text=True,
        timeout=config.LP_TIMEOUT_SECONDS,
        check=True,
    )
    return result.stdout


def discover_options(refresh=False):
    """The printer's selectable options, ready for the template.

    Never raises: if CUPS cannot be reached the form simply offers no
    options and printing falls back to the printer's own defaults.
    """
    now = time.monotonic()
    if (
        not refresh
        and _cache["options"] is not None
        and now - _cache["at"] < CACHE_SECONDS
    ):
        return _cache["options"]

    try:
        discovered = parse_options(_run_lpoptions())
    except (OSError, subprocess.SubprocessError) as error:
        print(f"[localprint] could not read printer options: {error}")
        discovered = {}

    options = []
    for keyword, label in SUPPORTED_OPTIONS:
        found = discovered.get(keyword)
        if not found:
            continue

        options.append(
            {
                "keyword": keyword,
                "label": label,
                "default": found["default"],
                "choices": [
                    {"value": value, "label": label_for_choice(keyword, value)}
                    for value in found["choices"]
                ],
            }
        )

    _cache["at"] = now
    _cache["options"] = options
    return options


def reset_option_cache():
    _cache["at"] = 0.0
    _cache["options"] = None


# --------------------------------------------------------------------------
# Printing
# --------------------------------------------------------------------------


def build_command(filename, copies, duplex, pages=None, options=None):
    command = ["lp", "-d", config.PRINTER, "-n", str(copies)]

    # Emitted in the order the options are declared, so the command is
    # reproducible regardless of how the form serialised the fields.
    for keyword, _label in SUPPORTED_OPTIONS:
        value = (options or {}).get(keyword)
        if value:
            command += ["-o", f"{keyword}={value}"]

    # After the discovered options, so the form's own toggle wins if a
    # printer ever reports duplex under a keyword we surface.
    if duplex:
        command += ["-o", "sides=two-sided-long-edge"]
    if pages:
        command += ["-P", pages]
    # `--` stops lp from treating a crafted filename as an option.
    command += ["--", filename]
    return command


def submit(filename, copies, duplex, pages=None, options=None):
    """Queue a job and return the confirmation message from CUPS."""
    command = build_command(filename, copies, duplex, pages, options)

    if config.FAKE_PRINTER:
        print("[fake printer] " + " ".join(command))
        return (
            f"request id is {config.PRINTER}-000 ({copies} file(s)) "
            "[simulated - fake printer mode]"
        )

    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=config.LP_TIMEOUT_SECONDS,
            check=True,
        )
    except FileNotFoundError:
        raise PrintError(
            "The `lp` command was not found. Is CUPS installed?"
        )
    except subprocess.TimeoutExpired:
        raise PrintError("Printing timed out while contacting CUPS.")
    except subprocess.CalledProcessError as error:
        details = (
            error.stderr.strip()
            or error.stdout.strip()
            or "Unknown CUPS error"
        )
        raise PrintError(f"Print failed: {details}")

    return result.stdout.strip() or "Print job submitted successfully."
