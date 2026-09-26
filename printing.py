"""Printer abstraction: builds and runs the CUPS `lp` command.

Print options (colour mode, quality, paper) are not configured anywhere:
they are discovered from the printer itself with `lpoptions -l`, so the UI
offers exactly what this printer can actually do.
"""
import os
import re
import shutil
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

# A printer's own default is normally the right thing to preselect, but paper
# type is an exception: PPDs tend to default to a specific stock (Canon picks
# Com.canon.mtinkjeta), while every driver that offers an automatic setting
# does a better job of guessing than we can. Prefer it when it exists.
PREFERRED_DEFAULTS = {
    "MediaType": ("Auto", "AutoDetect", "Automatic"),
}

# Discovery costs a subprocess call, so hold the answer for a while. A
# printer's capabilities change about as often as the printer does.
CACHE_SECONDS = 300

_cache = {"at": 0.0, "options": None}

# Stands in for a real printer during development and in the test suite. It
# mimics the awkward parts of a real PPD: vendor-prefixed media types whose
# default is a specific stock rather than Auto, a group with a single choice,
# and one group large enough to need a dropdown.
FAKE_OPTIONS = """PageSize/Media Size: *A4 A5 Letter Legal 4x6 Custom.WIDTHxHEIGHT
InputSlot/Media Source: *Auto Main Rear
MediaType/Media Type: Com.example.mtglossy *Com.example.mtplain Envelope Auto
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


def preferred_default(keyword, found):
    """Which choice to start on, which is not always the printer's own.

    Falls back to the printer's default whenever no preference applies, so
    an untouched form still behaves like a bare `lp` invocation.
    """
    for candidate in PREFERRED_DEFAULTS.get(keyword, ()):
        if candidate in found["choices"]:
            return candidate
    return found["default"]


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
                "default": preferred_default(keyword, found),
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
        # Keep the fake queue honest, so the status panel and the clear
        # button can be exercised without a printer.
        FAKE_QUEUE["jobs"] += 1
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


# --------------------------------------------------------------------------
# Queue health
# --------------------------------------------------------------------------
#
# CUPS stops a queue when a backend reports a failure, and nothing brings it
# back automatically. A stopped queue still *accepts* jobs, so `lp` keeps
# succeeding and every print silently piles up unseen. Reading the queue back
# is what makes that failure visible, and clearing it is what undoes it.

# `lpstat -p` describes the queue in one sentence:
#     printer NAME is idle.  enabled since Fri 01 Jan 2027 10:00:00
#     printer NAME disabled since Fri 01 Jan 2027 10:00:00 -
#             Unable to send raster data to the driver.
DISABLED_STATE = re.compile(r"\bdisabled since\b", re.IGNORECASE)

# `lpstat -o` lists one job per line, starting with NAME-NUMBER.
JOB_LINE = re.compile(r"^\S+-\d+\s+\S+\s+\d+")

# Stands in for a real queue in fake-printer mode. Tests mutate it to
# rehearse the stuck queue this exists for.
FAKE_QUEUE = {"enabled": True, "jobs": 0, "reason": ""}

# `cupsenable` and `cupsaccept` live in sbin, which is not on a service
# user's PATH on Debian, so they cannot simply be named.
SBIN_DIRECTORIES = ("/usr/sbin", "/usr/local/sbin", "/sbin")


def _tool(name):
    """Absolute path to a CUPS command, looking in sbin as well as PATH."""
    found = shutil.which(name)
    if found:
        return found

    for directory in SBIN_DIRECTORIES:
        # Joined by hand: these are POSIX locations whatever the host
        # running the test suite happens to use as a separator.
        candidate = directory + "/" + name
        if os.access(candidate, os.X_OK):
            return candidate

    # Nothing found: hand back the bare name so the eventual error names it.
    return name


def parse_queue_status(printer_text, jobs_text):
    """Turn `lpstat -p` and `lpstat -o` output into a queue summary."""
    # The reason for a stop is wrapped onto its own indented line, so the
    # whole description has to be flattened before it can be split.
    description = " ".join(printer_text.split())

    reason = ""
    _, separator, trailing = description.partition(" - ")
    if separator:
        reason = trailing.strip()

    return {
        "enabled": not DISABLED_STATE.search(description),
        "jobs": sum(
            1
            for line in jobs_text.splitlines()
            if JOB_LINE.match(line.strip())
        ),
        "reason": reason,
    }


def _read_queue(argument):
    result = subprocess.run(
        ["lpstat", argument, config.PRINTER],
        capture_output=True,
        text=True,
        timeout=config.LP_TIMEOUT_SECONDS,
        check=False,
    )
    return result.stdout


def queue_status():
    """How the queue is doing, ready for the template.

    Never raises: a status the page cannot read must not take down the page
    that would let someone fix it. `known` says whether to trust the rest.
    """
    if config.FAKE_PRINTER:
        return dict(FAKE_QUEUE, known=True)

    try:
        status = parse_queue_status(_read_queue("-p"), _read_queue("-o"))
    except (OSError, subprocess.SubprocessError) as error:
        print(f"[localprint] could not read the print queue: {error}")
        return {"enabled": True, "jobs": 0, "reason": "", "known": False}

    status["known"] = True
    return status


def _run_admin(command):
    """Run one CUPS admin command, returning an error string or None."""
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=config.LP_TIMEOUT_SECONDS,
            check=False,
        )
    except FileNotFoundError:
        return f"{command[0]} was not found. Is CUPS installed?"
    except subprocess.TimeoutExpired:
        return f"{command[0]} timed out while contacting CUPS."
    except OSError as error:
        return f"{command[0]} could not be run: {error}"

    if result.returncode == 0:
        return None
    return result.stderr.strip() or result.stdout.strip() or (
        f"{command[0]} failed with status {result.returncode}."
    )


def clear_queue():
    """Cancel everything waiting and bring a stopped queue back to life.

    Both halves matter. Cancelling alone leaves a stopped queue stopped, so
    the next job would be swallowed exactly like the ones just discarded.

    These commands need CUPS administrative rights, which the service gets
    from membership of the `lpadmin` group rather than from sudo -- the unit
    sets NoNewPrivileges, so escalation is not available to it anyway.
    """
    if config.FAKE_PRINTER:
        cancelled = FAKE_QUEUE["jobs"]
        FAKE_QUEUE.update(enabled=True, jobs=0, reason="")
        return _clear_message(cancelled)

    waiting = queue_status()["jobs"]

    failures = []
    for command in (
        # Nothing to cancel is not a failure, so this stays tolerant.
        [_tool("cancel"), "-a", config.PRINTER],
        [_tool("cupsenable"), config.PRINTER],
        [_tool("cupsaccept"), config.PRINTER],
    ):
        error = _run_admin(command)
        if error:
            failures.append(error)

    if failures:
        raise PrintError(
            "Could not clear the queue: "
            + " ".join(failures)
            + " The print user may not be in the 'lpadmin' group -- "
            "re-run install.sh to fix that."
        )

    return _clear_message(waiting)


def _clear_message(cancelled):
    if cancelled:
        return (
            f"Queue cleared: {cancelled} job(s) cancelled, "
            "the printer is ready again."
        )
    return "Queue was empty. The printer is ready."
