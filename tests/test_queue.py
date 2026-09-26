"""Reading the print queue, and clearing it when it gets stuck.

The failure these guard against is quiet: CUPS stops a queue when a backend
fails, keeps accepting jobs afterwards, and never restarts by itself. `lp`
still reports success, so nothing printed and nothing complained.
"""
import subprocess

import pytest

import config
import printing


# Real `lpstat` output from the queue that prompted all this, with the
# printer and user renamed. The reason is wrapped onto its own indented
# line, which is exactly what naive parsing gets wrong.
STOPPED_OUTPUT = (
    "printer office disabled since Sat Sep 26 14:31:03 2026 -\n"
    "\tUnable to send raster data to the driver.\n"
)

IDLE_OUTPUT = (
    "printer office is idle.  enabled since Sat Sep 26 17:35:24 2026\n"
)

BUSY_OUTPUT = (
    "printer office now printing office-34.  "
    "enabled since Sat Sep 26 17:36:01 2026\n"
)

JOBS_OUTPUT = (
    "office-26                alex       2539520   Sat Sep 26 14:29:07 2026\n"
    "office-27                alex       2539520   Sat Sep 26 14:31:35 2026\n"
    "office-28                alex       2607104   Sat Sep 26 14:31:55 2026\n"
)


# --------------------------------------------------------------------------
# Reading `lpstat`
# --------------------------------------------------------------------------


def test_idle_queue_is_enabled():
    status = printing.parse_queue_status(IDLE_OUTPUT, "")
    assert status["enabled"] is True
    assert status["jobs"] == 0
    assert status["reason"] == ""


def test_printing_queue_is_enabled():
    assert printing.parse_queue_status(BUSY_OUTPUT, "")["enabled"] is True


def test_stopped_queue_is_reported_as_disabled():
    assert printing.parse_queue_status(STOPPED_OUTPUT, "")["enabled"] is False


def test_stop_reason_is_unwrapped_onto_one_line():
    status = printing.parse_queue_status(STOPPED_OUTPUT, "")
    assert status["reason"] == "Unable to send raster data to the driver."


def test_idle_queue_has_no_reason():
    assert printing.parse_queue_status(IDLE_OUTPUT, "")["reason"] == ""


def test_waiting_jobs_are_counted():
    assert printing.parse_queue_status(IDLE_OUTPUT, JOBS_OUTPUT)["jobs"] == 3


def test_empty_job_list_counts_zero():
    assert printing.parse_queue_status(IDLE_OUTPUT, "\n  \n")["jobs"] == 0


def test_job_count_ignores_lpstat_chatter():
    noise = "lpstat: Error - no default destination\n" + JOBS_OUTPUT
    assert printing.parse_queue_status(IDLE_OUTPUT, noise)["jobs"] == 3


def test_stopped_queue_with_jobs_reports_both():
    status = printing.parse_queue_status(STOPPED_OUTPUT, JOBS_OUTPUT)
    assert status["enabled"] is False
    assert status["jobs"] == 3


def test_unreadable_printer_state_is_treated_as_running():
    # Nothing said is not the same as "stopped": refuse to cry wolf.
    assert printing.parse_queue_status("", "")["enabled"] is True


# --------------------------------------------------------------------------
# Locating the CUPS admin tools
# --------------------------------------------------------------------------


def test_tool_prefers_whatever_is_on_the_path(monkeypatch):
    monkeypatch.setattr(printing.shutil, "which", lambda _n: "/usr/bin/cancel")
    assert printing._tool("cancel") == "/usr/bin/cancel"


def test_tool_falls_back_to_sbin(monkeypatch):
    # cupsenable lives in sbin, which is not on a service user's PATH.
    monkeypatch.setattr(printing.shutil, "which", lambda _n: None)
    monkeypatch.setattr(
        printing.os,
        "access",
        lambda path, _mode: path == "/usr/sbin/cupsenable",
    )
    assert printing._tool("cupsenable") == "/usr/sbin/cupsenable"


def test_missing_tool_keeps_its_name(monkeypatch):
    monkeypatch.setattr(printing.shutil, "which", lambda _n: None)
    monkeypatch.setattr(printing.os, "access", lambda *_a: False)
    assert printing._tool("cupsenable") == "cupsenable"


# --------------------------------------------------------------------------
# queue_status()
# --------------------------------------------------------------------------


def test_status_uses_the_fake_queue_when_faking():
    printing.FAKE_QUEUE.update(enabled=False, jobs=4, reason="Out of paper")
    status = printing.queue_status()
    assert status == {
        "enabled": False,
        "jobs": 4,
        "reason": "Out of paper",
        "known": True,
    }


def test_status_does_not_hand_out_the_fake_queue_itself():
    # A caller mutating the reply must not rewrite the source of truth.
    printing.queue_status()["jobs"] = 99
    assert printing.FAKE_QUEUE["jobs"] == 0


def test_status_shells_out_to_lpstat(monkeypatch):
    monkeypatch.setattr(config, "FAKE_PRINTER", False)
    calls = []

    def fake_run(command, **_kwargs):
        calls.append(command)
        output = STOPPED_OUTPUT if "-p" in command else JOBS_OUTPUT
        return subprocess.CompletedProcess(command, 0, output, "")

    monkeypatch.setattr(printing.subprocess, "run", fake_run)

    status = printing.queue_status()
    assert status["enabled"] is False
    assert status["jobs"] == 3
    assert status["known"] is True
    assert calls == [
        ["lpstat", "-p", config.PRINTER],
        ["lpstat", "-o", config.PRINTER],
    ]


@pytest.mark.parametrize(
    "error",
    [
        FileNotFoundError("lpstat"),
        subprocess.TimeoutExpired("lpstat", 5),
        OSError("no cups"),
    ],
)
def test_status_survives_a_broken_lpstat(monkeypatch, error):
    monkeypatch.setattr(config, "FAKE_PRINTER", False)

    def explode(*_args, **_kwargs):
        raise error

    monkeypatch.setattr(printing.subprocess, "run", explode)

    status = printing.queue_status()
    assert status["known"] is False
    # Unknown must not masquerade as broken.
    assert status["enabled"] is True


# --------------------------------------------------------------------------
# clear_queue()
# --------------------------------------------------------------------------


def test_clearing_empties_the_fake_queue():
    printing.FAKE_QUEUE.update(enabled=False, jobs=3, reason="Stopped")
    message = printing.clear_queue()

    assert printing.FAKE_QUEUE == {"enabled": True, "jobs": 0, "reason": ""}
    assert "3 job(s) cancelled" in message


def test_clearing_an_empty_queue_says_so():
    assert "empty" in printing.clear_queue().lower()


def test_a_printed_job_shows_up_in_the_fake_queue(tmp_path):
    sample = tmp_path / "doc.pdf"
    sample.write_bytes(b"%PDF-1.4\n")

    printing.submit(str(sample), 1, False)
    assert printing.queue_status()["jobs"] == 1


def _record_admin(monkeypatch, returncode=0, stderr=""):
    monkeypatch.setattr(config, "FAKE_PRINTER", False)
    monkeypatch.setattr(printing, "_tool", lambda name: "/usr/sbin/" + name)
    monkeypatch.setattr(
        printing, "queue_status", lambda: {"enabled": False, "jobs": 2}
    )

    calls = []

    def fake_run(command, **_kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, returncode, "", stderr)

    monkeypatch.setattr(printing.subprocess, "run", fake_run)
    return calls


def test_clearing_cancels_then_restarts_the_printer(monkeypatch):
    calls = _record_admin(monkeypatch)
    printing.clear_queue()

    # Cancelling alone would leave a stopped queue stopped, so the next job
    # would vanish exactly like the ones just discarded.
    assert calls == [
        ["/usr/sbin/cancel", "-a", config.PRINTER],
        ["/usr/sbin/cupsenable", config.PRINTER],
        ["/usr/sbin/cupsaccept", config.PRINTER],
    ]


def test_clearing_reports_how_much_was_discarded(monkeypatch):
    _record_admin(monkeypatch)
    assert "2 job(s) cancelled" in printing.clear_queue()


def test_a_refused_command_is_an_error(monkeypatch):
    _record_admin(monkeypatch, returncode=1, stderr="cancel: Forbidden")

    with pytest.raises(printing.PrintError) as caught:
        printing.clear_queue()

    assert "Forbidden" in str(caught.value)
    # The likeliest cause by far, so say it rather than make them guess.
    assert "lpadmin" in str(caught.value)


def test_a_missing_command_is_an_error(monkeypatch):
    monkeypatch.setattr(config, "FAKE_PRINTER", False)
    monkeypatch.setattr(printing, "queue_status", lambda: {"jobs": 0})
    monkeypatch.setattr(printing, "_tool", lambda name: name)

    def explode(*_args, **_kwargs):
        raise FileNotFoundError("cupsenable")

    monkeypatch.setattr(printing.subprocess, "run", explode)

    with pytest.raises(printing.PrintError) as caught:
        printing.clear_queue()
    assert "cancel" in str(caught.value)


def test_a_hung_command_is_an_error(monkeypatch):
    monkeypatch.setattr(config, "FAKE_PRINTER", False)
    monkeypatch.setattr(printing, "queue_status", lambda: {"jobs": 0})
    monkeypatch.setattr(printing, "_tool", lambda name: name)

    def explode(*_args, **_kwargs):
        raise subprocess.TimeoutExpired("cancel", 5)

    monkeypatch.setattr(printing.subprocess, "run", explode)

    with pytest.raises(printing.PrintError) as caught:
        printing.clear_queue()
    assert "timed out" in str(caught.value)
