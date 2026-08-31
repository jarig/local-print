"""Printer abstraction: builds and runs the CUPS `lp` command."""
import subprocess

import config


class PrintError(Exception):
    """Raised when a print job could not be queued."""


def build_command(filename, copies, duplex, pages=None):
    command = ["lp", "-d", config.PRINTER, "-n", str(copies)]
    if duplex:
        command += ["-o", "sides=two-sided-long-edge"]
    if pages:
        command += ["-P", pages]
    # `--` stops lp from treating a crafted filename as an option.
    command += ["--", filename]
    return command


def submit(filename, copies, duplex, pages=None):
    """Queue a job and return the confirmation message from CUPS."""
    command = build_command(filename, copies, duplex, pages)

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
