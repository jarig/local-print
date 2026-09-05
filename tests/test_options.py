"""Discovery of the printer's own options via `lpoptions -l`."""
import subprocess

import pytest

import config
import printing


REAL_CANON_OUTPUT = """PageSize/Media Size: 3.5x5 4x6 4x6.Borderless *A4 A5 B5 Legal Letter Custom.WIDTHxHEIGHT
InputSlot/Media Source: *Auto Main Rear
MediaType/Media Type: Com.canon.mtglossy *Com.canon.mtinkjeta Com.canon.mthagaki Envelope Stationery Auto
cupsPrintQuality/cupsPrintQuality: Draft *Normal High
ColorModel/Output Mode: *RGB Gray
Duplex/Duplex: *None DuplexNoTumble DuplexTumble
OutputBin/OutputBin: *FaceUp
"""


@pytest.fixture(autouse=True)
def clear_cache():
    printing.reset_option_cache()
    yield
    printing.reset_option_cache()


@pytest.fixture
def fake_lpoptions(monkeypatch):
    """Feed discovery a chosen `lpoptions -l` output."""

    def use(text):
        monkeypatch.setattr(printing, "_run_lpoptions", lambda: text)

    return use


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------


def test_parses_choices_and_the_starred_default():
    parsed = printing.parse_options(REAL_CANON_OUTPUT)
    assert parsed["ColorModel"]["choices"] == ["RGB", "Gray"]
    assert parsed["ColorModel"]["default"] == "RGB"
    assert parsed["cupsPrintQuality"]["default"] == "Normal"


def test_custom_placeholder_is_not_a_choice():
    # "Custom.WIDTHxHEIGHT" is a template for a custom size, not a size.
    parsed = printing.parse_options(REAL_CANON_OUTPUT)
    assert "Custom.WIDTHxHEIGHT" not in parsed["PageSize"]["choices"]
    assert "A4" in parsed["PageSize"]["choices"]


def test_groups_with_a_single_choice_are_dropped():
    # OutputBin offers only FaceUp; a control with one option is noise.
    assert "OutputBin" not in printing.parse_options(REAL_CANON_OUTPUT)


def test_a_group_with_no_starred_default_falls_back_to_the_first():
    parsed = printing.parse_options("ColorModel/Output Mode: RGB Gray\n")
    assert parsed["ColorModel"]["default"] == "RGB"


def test_unparseable_lines_are_skipped():
    text = "warning: something went wrong\n\nColorModel/Output Mode: *RGB Gray\n"
    assert list(printing.parse_options(text)) == ["ColorModel"]


def test_empty_output_yields_nothing():
    assert printing.parse_options("") == {}


# --------------------------------------------------------------------------
# Choice labels
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value, expected",
    [
        ("RGB", "Colour"),
        ("CMYK", "Colour"),
        ("Gray", "Black & white"),
        ("Grayscale", "Black & white"),
        ("Black", "Black & white"),
    ],
)
def test_colour_choices_get_readable_names(value, expected):
    assert printing.label_for_choice("ColorModel", value) == expected


def test_vendor_prefixes_are_stripped():
    # PPDs carry no human text for these, so the keyword is all we have.
    assert printing.label_for_choice(
        "MediaType", "Com.canon.mtinkjeta"
    ) == "Inkjeta"


def test_dotted_values_become_readable():
    assert printing.label_for_choice("PageSize", "4x6.Borderless") == (
        "4x6 Borderless"
    )


def test_plain_values_are_left_alone():
    assert printing.label_for_choice("PageSize", "A4") == "A4"


# --------------------------------------------------------------------------
# discover_options
# --------------------------------------------------------------------------


def test_discovery_exposes_the_supported_groups_in_order(fake_lpoptions):
    fake_lpoptions(REAL_CANON_OUTPUT)
    keywords = [option["keyword"] for option in printing.discover_options()]
    assert keywords == [
        "ColorModel",
        "cupsPrintQuality",
        "PageSize",
        "MediaType",
        "InputSlot",
    ]


def test_duplex_is_not_offered_twice(fake_lpoptions):
    # The form already has a dedicated double-sided toggle.
    fake_lpoptions(REAL_CANON_OUTPUT)
    keywords = [option["keyword"] for option in printing.discover_options()]
    assert "Duplex" not in keywords


def test_discovery_labels_the_groups_and_the_choices(fake_lpoptions):
    fake_lpoptions(REAL_CANON_OUTPUT)
    colour = printing.discover_options()[0]

    assert colour["label"] == "Colour"
    assert colour["default"] == "RGB"
    assert colour["choices"] == [
        {"value": "RGB", "label": "Colour"},
        {"value": "Gray", "label": "Black & white"},
    ]


def test_unknown_groups_are_ignored(fake_lpoptions):
    fake_lpoptions("StapleLocation/Staple: *None UpperLeft\n")
    assert printing.discover_options() == []


# --------------------------------------------------------------------------
# Preferred defaults
# --------------------------------------------------------------------------


def media_type(options):
    return next(o for o in options if o["keyword"] == "MediaType")


def test_paper_type_prefers_auto_over_the_printers_own_default(fake_lpoptions):
    # The Canon PPD starts on a specific stock; letting the driver work it
    # out is a better default for someone printing from a phone.
    fake_lpoptions(REAL_CANON_OUTPUT)
    assert media_type(printing.discover_options())["default"] == "Auto"


def test_preferring_auto_does_not_remove_the_other_choices(fake_lpoptions):
    fake_lpoptions(REAL_CANON_OUTPUT)
    values = [c["value"] for c in media_type(printing.discover_options())["choices"]]
    assert "Com.canon.mtinkjeta" in values
    assert "Auto" in values


def test_paper_type_keeps_the_printers_default_when_auto_is_absent(
    fake_lpoptions,
):
    fake_lpoptions(
        "MediaType/Media Type: Plain *Glossy Envelope\n"
    )
    assert media_type(printing.discover_options())["default"] == "Glossy"


@pytest.mark.parametrize("spelling", ["Auto", "AutoDetect", "Automatic"])
def test_the_common_spellings_of_auto_are_recognised(fake_lpoptions, spelling):
    fake_lpoptions(f"MediaType/Media Type: Plain *Glossy {spelling}\n")
    assert media_type(printing.discover_options())["default"] == spelling


def test_other_groups_keep_the_printers_default(fake_lpoptions):
    # InputSlot also has an Auto, but its PPD default is already sensible
    # and no preference is declared for it.
    fake_lpoptions("PageSize/Media Size: A4 *Letter Auto\n")
    assert printing.discover_options()[0]["default"] == "Letter"


def test_the_result_is_cached(monkeypatch):
    calls = []

    def counted():
        calls.append(1)
        return REAL_CANON_OUTPUT

    monkeypatch.setattr(printing, "_run_lpoptions", counted)
    printing.discover_options()
    printing.discover_options()
    assert len(calls) == 1


def test_refresh_bypasses_the_cache(monkeypatch):
    calls = []

    def counted():
        calls.append(1)
        return REAL_CANON_OUTPUT

    monkeypatch.setattr(printing, "_run_lpoptions", counted)
    printing.discover_options()
    printing.discover_options(refresh=True)
    assert len(calls) == 2


@pytest.mark.parametrize(
    "error",
    [
        FileNotFoundError(),
        subprocess.TimeoutExpired(cmd="lpoptions", timeout=30),
        subprocess.CalledProcessError(returncode=1, cmd="lpoptions"),
    ],
)
def test_a_broken_printer_query_never_breaks_the_page(monkeypatch, error):
    def explode():
        raise error

    monkeypatch.setattr(printing, "_run_lpoptions", explode)
    assert printing.discover_options() == []


def test_the_fake_printer_offers_options_without_cups():
    # Development and the test suite must not need a real CUPS install.
    keywords = [option["keyword"] for option in printing.discover_options()]
    assert "ColorModel" in keywords


def test_the_real_query_asks_cups_for_this_printer(monkeypatch):
    monkeypatch.setattr(config, "FAKE_PRINTER", False)
    seen = {}

    class Result:
        stdout = REAL_CANON_OUTPUT

    def record(command, **kwargs):
        seen["command"] = command
        seen["timeout"] = kwargs.get("timeout")
        return Result()

    monkeypatch.setattr(subprocess, "run", record)
    printing.discover_options()

    assert seen["command"] == ["lpoptions", "-p", config.PRINTER, "-l"]
    assert seen["timeout"] == config.LP_TIMEOUT_SECONDS


# --------------------------------------------------------------------------
# The options reaching the lp command
# --------------------------------------------------------------------------


def test_selected_options_become_lp_flags():
    command = printing.build_command(
        "/tmp/a.pdf", 1, False, None, {"ColorModel": "Gray"}
    )
    assert command == [
        "lp", "-d", config.PRINTER, "-n", "1",
        "-o", "ColorModel=Gray",
        "--", "/tmp/a.pdf",
    ]


def test_options_are_emitted_in_a_stable_order():
    # Form field order must not change the command that gets run.
    forwards = printing.build_command(
        "/tmp/a.pdf", 1, False, None,
        {"ColorModel": "Gray", "cupsPrintQuality": "Draft", "PageSize": "A4"},
    )
    backwards = printing.build_command(
        "/tmp/a.pdf", 1, False, None,
        {"PageSize": "A4", "cupsPrintQuality": "Draft", "ColorModel": "Gray"},
    )
    assert forwards == backwards
    assert forwards[5:11] == [
        "-o", "ColorModel=Gray",
        "-o", "cupsPrintQuality=Draft",
        "-o", "PageSize=A4",
    ]


def test_options_combine_with_copies_duplex_and_pages():
    command = printing.build_command(
        "/tmp/a.pdf", 2, True, "1-3", {"ColorModel": "Gray"}
    )
    assert command == [
        "lp", "-d", config.PRINTER, "-n", "2",
        "-o", "ColorModel=Gray",
        "-o", "sides=two-sided-long-edge",
        "-P", "1-3",
        "--", "/tmp/a.pdf",
    ]


def test_duplex_is_applied_after_the_discovered_options():
    # Later -o wins in CUPS, so the dedicated toggle must come last.
    command = printing.build_command(
        "/tmp/a.pdf", 1, True, None, {"ColorModel": "Gray"}
    )
    assert command.index("sides=two-sided-long-edge") > command.index(
        "ColorModel=Gray"
    )


@pytest.mark.parametrize("options", [None, {}, {"ColorModel": ""}])
def test_no_options_means_no_extra_flags(options):
    command = printing.build_command("/tmp/a.pdf", 1, False, None, options)
    assert command == [
        "lp", "-d", config.PRINTER, "-n", "1", "--", "/tmp/a.pdf"
    ]


def test_unsupported_keywords_never_reach_the_command():
    """Only keywords the app knows about are ever emitted.

    Belt and braces: the route already rejects anything the printer did not
    offer, but build_command must not forward stray keys either.
    """
    command = printing.build_command(
        "/tmp/a.pdf", 1, False, None, {"HackAttempt": "; rm -rf /"}
    )
    assert command == [
        "lp", "-d", config.PRINTER, "-n", "1", "--", "/tmp/a.pdf"
    ]


def test_submit_passes_the_options_through(monkeypatch):
    monkeypatch.setattr(config, "FAKE_PRINTER", False)
    seen = {}

    class Result:
        stdout = "ok"

    def record(command, **_kwargs):
        seen["command"] = command
        return Result()

    monkeypatch.setattr(subprocess, "run", record)
    printing.submit("/tmp/a.pdf", 1, False, None, {"ColorModel": "Gray"})
    assert "ColorModel=Gray" in seen["command"]
