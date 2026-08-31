"""Tests for the configuration loader.

`config.py` runs its validation at import time, so each case loads a fresh
copy of the module under a private name rather than reusing the one the app
already imported.
"""
import importlib.util
import os
import sys
from pathlib import Path

import pytest

import config

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "localprint.conf.example"

COMPLETE = """
LOCALPRINT_PRINTER=office
LOCALPRINT_PORT=9000
LOCALPRINT_LAN_PREFIX=10.0.0.
LOCALPRINT_MAX_UPLOAD_MB=25
LOCALPRINT_LP_TIMEOUT_SECONDS=15
LOCALPRINT_MIN_COPIES=1
LOCALPRINT_MAX_COPIES=5
"""


def write(tmp_path, text):
    path = tmp_path / "localprint.conf"
    path.write_text(text, encoding="utf-8")
    return path


def load(monkeypatch, config_path, **environment):
    """Import config.py in isolation, with a controlled environment."""
    for key in list(os.environ):
        if key.startswith("LOCALPRINT_"):
            monkeypatch.delenv(key, raising=False)

    if config_path is not None:
        monkeypatch.setenv("LOCALPRINT_CONFIG", str(config_path))
    for key, value in environment.items():
        monkeypatch.setenv(key, value)

    name = "localprint_config_under_test"
    spec = importlib.util.spec_from_file_location(name, ROOT / "config.py")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(name, None)
    return module


# --------------------------------------------------------------------------
# The template is the documentation, so it has to stay in step with the code
# --------------------------------------------------------------------------


def test_template_is_committed():
    assert TEMPLATE.is_file()


def test_template_documents_every_required_key():
    text = TEMPLATE.read_text(encoding="utf-8")
    missing = [
        key for key in config.REQUIRED_KEYS if f"\n{key}=" not in f"\n{text}"
    ]
    assert missing == [], f"localprint.conf.example is missing: {missing}"


def test_template_documents_the_deployment_keys():
    values = config.parse(TEMPLATE)
    for key in (
        "LOCALPRINT_HOST",
        "LOCALPRINT_USER",
        "LOCALPRINT_REMOTE_PATH",
    ):
        assert values.get(key), f"{key} is missing from the template"


def test_template_loads(monkeypatch):
    loaded = load(monkeypatch, TEMPLATE)
    assert loaded.PRINTER
    assert loaded.PORT > 0


def test_the_real_config_is_not_committed():
    ignored = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "localprint.conf" in [line.strip() for line in ignored]


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------


def test_parse_ignores_comments_and_blank_lines(tmp_path):
    path = write(tmp_path, "# a comment\n\nA=1\n   # indented comment\nB=2\n")
    assert config.parse(path) == {"A": "1", "B": "2"}


def test_parse_strips_surrounding_quotes(tmp_path):
    path = write(tmp_path, 'A="one"\nB=\'two\'\nC=three\n')
    assert config.parse(path) == {"A": "one", "B": "two", "C": "three"}


def test_parse_keeps_equals_signs_in_the_value(tmp_path):
    path = write(tmp_path, "A=one=two\n")
    assert config.parse(path) == {"A": "one=two"}


def test_parse_rejects_a_line_without_an_equals_sign(tmp_path):
    path = write(tmp_path, "A=1\nnonsense\n")
    with pytest.raises(config.ConfigError) as error:
        config.parse(path)
    assert "nonsense" in str(error.value)


# --------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------


def test_values_come_from_the_file(monkeypatch, tmp_path):
    loaded = load(monkeypatch, write(tmp_path, COMPLETE))
    assert loaded.PRINTER == "office"
    assert loaded.PORT == 9000
    assert loaded.LAN_PREFIX == "10.0.0."
    assert loaded.MAX_UPLOAD_MB == 25
    assert loaded.MAX_UPLOAD_SIZE == 25 * 1024 * 1024
    assert loaded.LP_TIMEOUT_SECONDS == 15
    assert loaded.MIN_COPIES == 1
    assert loaded.MAX_COPIES == 5


def test_the_environment_wins_over_the_file(monkeypatch, tmp_path):
    loaded = load(
        monkeypatch,
        write(tmp_path, COMPLETE),
        LOCALPRINT_PRINTER="other",
        LOCALPRINT_PORT="1234",
    )
    assert loaded.PRINTER == "other"
    assert loaded.PORT == 1234


def test_the_environment_alone_is_enough(monkeypatch, tmp_path):
    loaded = load(
        monkeypatch,
        tmp_path / "absent.conf",
        LOCALPRINT_PRINTER="office",
        LOCALPRINT_PORT="9000",
        LOCALPRINT_LAN_PREFIX="10.0.0.",
        LOCALPRINT_MAX_UPLOAD_MB="25",
        LOCALPRINT_LP_TIMEOUT_SECONDS="15",
        LOCALPRINT_MIN_COPIES="1",
        LOCALPRINT_MAX_COPIES="5",
    )
    assert loaded.PRINTER == "office"


def test_flags_are_off_unless_set(monkeypatch, tmp_path):
    loaded = load(monkeypatch, write(tmp_path, COMPLETE))
    assert loaded.FAKE_PRINTER is False
    assert loaded.DEBUG is False


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on"])
def test_flags_accept_the_usual_spellings(monkeypatch, tmp_path, value):
    loaded = load(
        monkeypatch, write(tmp_path, COMPLETE), LOCALPRINT_DEBUG=value
    )
    assert loaded.DEBUG is True


@pytest.mark.parametrize("value", ["0", "false", "no", "off", ""])
def test_flags_reject_anything_else(monkeypatch, tmp_path, value):
    loaded = load(
        monkeypatch, write(tmp_path, COMPLETE), LOCALPRINT_DEBUG=value
    )
    assert loaded.DEBUG is False


# --------------------------------------------------------------------------
# There are no defaults: anything missing or invalid must stop the app
# --------------------------------------------------------------------------


def test_a_missing_config_file_is_reported_clearly(monkeypatch, tmp_path):
    with pytest.raises(RuntimeError) as error:
        load(monkeypatch, tmp_path / "absent.conf")
    message = str(error.value)
    assert "localprint.conf.example" in message
    assert "absent.conf" in message


@pytest.mark.parametrize("missing", config.REQUIRED_KEYS)
def test_every_required_key_is_required(monkeypatch, tmp_path, missing):
    text = "\n".join(
        line
        for line in COMPLETE.strip().splitlines()
        if not line.startswith(missing + "=")
    )
    with pytest.raises(RuntimeError) as error:
        load(monkeypatch, write(tmp_path, text))
    assert missing in str(error.value)


def test_an_empty_value_counts_as_missing(monkeypatch, tmp_path):
    text = COMPLETE.replace("LOCALPRINT_PRINTER=office", "LOCALPRINT_PRINTER=")
    with pytest.raises(RuntimeError) as error:
        load(monkeypatch, write(tmp_path, text))
    assert "LOCALPRINT_PRINTER" in str(error.value)


def test_a_non_numeric_port_is_rejected(monkeypatch, tmp_path):
    text = COMPLETE.replace("LOCALPRINT_PORT=9000", "LOCALPRINT_PORT=eighty")
    with pytest.raises(RuntimeError) as error:
        load(monkeypatch, write(tmp_path, text))
    assert "whole number" in str(error.value)


@pytest.mark.parametrize(
    "key,value",
    [
        ("LOCALPRINT_PORT", "0"),
        ("LOCALPRINT_MAX_UPLOAD_MB", "0"),
        ("LOCALPRINT_LP_TIMEOUT_SECONDS", "0"),
        ("LOCALPRINT_MIN_COPIES", "0"),
    ],
)
def test_numbers_below_the_minimum_are_rejected(
    monkeypatch, tmp_path, key, value
):
    loader = lambda: load(  # noqa: E731
        monkeypatch, write(tmp_path, COMPLETE), **{key: value}
    )
    with pytest.raises(RuntimeError) as error:
        loader()
    assert "at least" in str(error.value)


def test_a_lan_prefix_without_a_trailing_dot_is_rejected(
    monkeypatch, tmp_path
):
    # "10.1.2" would also match 10.1.21.x, which is a different network.
    with pytest.raises(RuntimeError) as error:
        load(
            monkeypatch,
            write(tmp_path, COMPLETE),
            LOCALPRINT_LAN_PREFIX="10.1.2",
        )
    assert "must end with a dot" in str(error.value)


def test_max_copies_below_min_copies_is_rejected(monkeypatch, tmp_path):
    with pytest.raises(RuntimeError) as error:
        load(
            monkeypatch,
            write(tmp_path, COMPLETE),
            LOCALPRINT_MIN_COPIES="10",
            LOCALPRINT_MAX_COPIES="5",
        )
    assert "below" in str(error.value)
