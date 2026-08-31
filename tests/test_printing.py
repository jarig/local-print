"""Page-range parsing and the CUPS command that gets built from it."""
import subprocess

import pytest

import app as app_module
import config
import printing


# --------------------------------------------------------------------------
# validate_page_range
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("1", "1"),
        ("1-5", "1-5"),
        ("1,3,7", "1,3,7"),
        ("1-3,5,8-10", "1-3,5,8-10"),
        ("7-7", "7-7"),
        ("  1 , 3 - 5 ", "1,3-5"),
        ("999", "999"),
    ],
)
def test_accepts_valid_page_expressions(raw, expected):
    assert app_module.validate_page_range(raw) == expected


@pytest.mark.parametrize("raw", ["", "   ", None])
def test_blank_expression_means_all_pages(raw):
    assert app_module.validate_page_range(raw) is None


@pytest.mark.parametrize(
    "raw",
    [
        "abc",
        "1-",
        "-3",
        "1,,3",
        "1;3",
        "1-2-3",
        "0",          # pages are 1-based
        "0-4",
        "5-2",        # descending
        "1,0",
        "1..3",
        "1 3",        # space is not a separator once collapsed
        "--",
        "1-3,",
    ],
)
def test_rejects_invalid_page_expressions(raw):
    with pytest.raises(ValueError):
        app_module.validate_page_range(raw)


def test_rejected_expression_explains_the_format():
    with pytest.raises(ValueError, match="1-3"):
        app_module.validate_page_range("nonsense")


# --------------------------------------------------------------------------
# build_command
# --------------------------------------------------------------------------


def test_command_defaults_to_a_single_simplex_copy():
    assert printing.build_command("/tmp/a.pdf", 1, False) == [
        "lp", "-d", config.PRINTER, "-n", "1", "--", "/tmp/a.pdf"
    ]


def test_command_includes_copies_duplex_and_pages():
    assert printing.build_command("/tmp/a.pdf", 3, True, "1,3-5") == [
        "lp", "-d", config.PRINTER, "-n", "3",
        "-o", "sides=two-sided-long-edge",
        "-P", "1,3-5",
        "--", "/tmp/a.pdf",
    ]


def test_command_omits_page_flag_when_pages_is_falsy():
    for pages in (None, ""):
        assert "-P" not in printing.build_command("/tmp/a.pdf", 1, False, pages)


def test_filename_is_separated_so_it_cannot_become_an_option():
    """A filename beginning with a dash must not be read as a flag."""
    command = printing.build_command("/tmp/-o dangerous.pdf", 1, False)
    assert command[-2] == "--"
    assert command[-1] == "/tmp/-o dangerous.pdf"


def test_arguments_are_a_list_so_no_shell_interpolation_happens():
    command = printing.build_command("/tmp/a; rm -rf ~.pdf", 1, False)
    # The whole string stays a single argv entry.
    assert command[-1] == "/tmp/a; rm -rf ~.pdf"


# --------------------------------------------------------------------------
# submit error handling
# --------------------------------------------------------------------------


def test_submit_uses_the_stub_when_fake_printer_is_enabled():
    message = printing.submit("/tmp/a.pdf", 1, False)
    assert "simulated" in message


def _with_real_printer(monkeypatch):
    monkeypatch.setattr(config, "FAKE_PRINTER", False)


def test_submit_reports_a_missing_lp_binary(monkeypatch):
    _with_real_printer(monkeypatch)

    def explode(*_args, **_kwargs):
        raise FileNotFoundError()

    monkeypatch.setattr(subprocess, "run", explode)
    with pytest.raises(printing.PrintError, match="CUPS"):
        printing.submit("/tmp/a.pdf", 1, False)


def test_submit_reports_a_timeout(monkeypatch):
    _with_real_printer(monkeypatch)

    def explode(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(cmd="lp", timeout=30)

    monkeypatch.setattr(subprocess, "run", explode)
    with pytest.raises(printing.PrintError, match="timed out"):
        printing.submit("/tmp/a.pdf", 1, False)


def test_submit_surfaces_the_cups_error_text(monkeypatch):
    _with_real_printer(monkeypatch)

    def explode(*_args, **_kwargs):
        raise subprocess.CalledProcessError(
            returncode=1, cmd="lp", output="", stderr="printer is unplugged"
        )

    monkeypatch.setattr(subprocess, "run", explode)
    with pytest.raises(printing.PrintError, match="printer is unplugged"):
        printing.submit("/tmp/a.pdf", 1, False)


def test_submit_returns_the_cups_confirmation(monkeypatch):
    _with_real_printer(monkeypatch)

    class Result:
        stdout = "request id is office-42 (1 file(s))"

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: Result())
    assert printing.submit("/tmp/a.pdf", 1, False) == (
        "request id is office-42 (1 file(s))"
    )


def test_submit_falls_back_when_cups_says_nothing(monkeypatch):
    _with_real_printer(monkeypatch)

    class Result:
        stdout = "   "

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: Result())
    assert "submitted" in printing.submit("/tmp/a.pdf", 1, False)


def test_submit_honours_the_configured_timeout(monkeypatch):
    _with_real_printer(monkeypatch)
    seen = {}

    class Result:
        stdout = "ok"

    def capture(command, **kwargs):
        seen.update(kwargs)
        seen["command"] = command
        return Result()

    monkeypatch.setattr(subprocess, "run", capture)
    printing.submit("/tmp/a.pdf", 1, False)

    assert seen["timeout"] == config.LP_TIMEOUT_SECONDS
    assert seen["check"] is True
    assert isinstance(seen["command"], list)
