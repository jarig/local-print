"""Shared fixtures for the LocalPrint test suite."""
import os
import socket
import struct
import subprocess
import sys
import time
import zlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Tests run against the committed template, never against whatever the
# developer happens to have in localprint.conf.
os.environ["LOCALPRINT_CONFIG"] = str(ROOT / "localprint.conf.example")

# The app must never touch a real printer during tests.
os.environ["LOCALPRINT_FAKE_PRINTER"] = "1"

import app as app_module  # noqa: E402
import printing  # noqa: E402


# --------------------------------------------------------------------------
# Sample documents
# --------------------------------------------------------------------------


def build_pdf(pages=1):
    """Build a minimal but structurally valid multi-page PDF."""
    objects = []

    def add(body):
        objects.append(body)
        return len(objects)

    font_id = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    content_ids = []
    for number in range(1, pages + 1):
        stream = f"BT /F1 96 Tf 72 560 Td (Page {number}) Tj ET\n".encode()
        content_ids.append(
            add(
                b"<< /Length "
                + str(len(stream)).encode()
                + b" >>\nstream\n"
                + stream
                + b"endstream"
            )
        )

    pages_id = len(objects) + pages + 1
    page_ids = []
    for index in range(pages):
        page_ids.append(
            add(
                b"<< /Type /Page /Parent "
                + str(pages_id).encode()
                + b" 0 R /MediaBox [0 0 612 792]"
                b" /Resources << /Font << /F1 "
                + str(font_id).encode()
                + b" 0 R >> >> /Contents "
                + str(content_ids[index]).encode()
                + b" 0 R >>"
            )
        )

    kids = b" ".join(str(pid).encode() + b" 0 R" for pid in page_ids)
    add(
        b"<< /Type /Pages /Kids ["
        + kids
        + b"] /Count "
        + str(pages).encode()
        + b" >>"
    )
    catalog_id = add(
        b"<< /Type /Catalog /Pages " + str(pages_id).encode() + b" 0 R >>"
    )

    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for index, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += str(index).encode() + b" 0 obj\n" + body + b"\nendobj\n"

    xref_at = len(out)
    out += b"xref\n0 " + str(len(objects) + 1).encode() + b"\n"
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()

    out += (
        b"trailer\n<< /Size "
        + str(len(objects) + 1).encode()
        + b" /Root "
        + str(catalog_id).encode()
        + b" 0 R >>\nstartxref\n"
        + str(xref_at).encode()
        + b"\n%%EOF\n"
    )
    return bytes(out)


def build_png(width=8, height=8):
    """Build a solid-colour PNG without pulling in an imaging library."""

    def chunk(tag, payload):
        body = tag + payload
        return (
            struct.pack(">I", len(payload))
            + body
            + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)
        )

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    raw = b"".join(
        b"\x00" + b"\x46\x82\xc8" * width for _ in range(height)
    )
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


@pytest.fixture(scope="session")
def pdf_bytes():
    return build_pdf(1)


@pytest.fixture(scope="session")
def pdf7_bytes():
    return build_pdf(7)


@pytest.fixture(scope="session")
def png_bytes():
    return build_png()


@pytest.fixture(scope="session")
def sample_files(tmp_path_factory):
    """Sample documents on disk, for the browser tests to upload."""
    directory = tmp_path_factory.mktemp("samples")
    files = {
        "pdf1": ("one-page.pdf", build_pdf(1)),
        "pdf7": ("seven-pages.pdf", build_pdf(7)),
        # Deliberately capitalised, to guard the extension check.
        "pdfUpper": ("Contract.PDF", build_pdf(3)),
        "png": ("picture.png", build_png()),
        "txt": ("notes.txt", b"not printable"),
    }

    paths = {}
    for key, (name, payload) in files.items():
        path = directory / name
        path.write_bytes(payload)
        paths[key] = str(path)
    return paths


# --------------------------------------------------------------------------
# Flask test client
# --------------------------------------------------------------------------


@pytest.fixture
def flask_app():
    app_module.app.config.update(TESTING=True)
    return app_module.app


@pytest.fixture
def client(flask_app):
    return flask_app.test_client()


@pytest.fixture
def recorded_jobs(monkeypatch):
    """Capture calls to the printer instead of queueing anything."""
    jobs = []

    def fake_submit(filename, copies, duplex, pages=None):
        jobs.append(
            {
                "filename": filename,
                "copies": copies,
                "duplex": duplex,
                "pages": pages,
                # Recorded while the call is in flight, so tests can assert
                # the upload exists at print time and is cleaned up after.
                "existed": os.path.exists(filename),
                "size": os.path.getsize(filename),
            }
        )
        return "request id is office-1 (1 file(s))"

    monkeypatch.setattr(app_module.printing, "submit", fake_submit)
    return jobs


# --------------------------------------------------------------------------
# Live server for browser tests
# --------------------------------------------------------------------------


def _free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="session")
def live_server(tmp_path_factory):
    """Run the real app in a subprocess with the printer stubbed out."""
    port = _free_port()
    environment = dict(os.environ)
    environment["LOCALPRINT_FAKE_PRINTER"] = "1"
    environment["LOCALPRINT_DEBUG"] = "0"
    environment["LOCALPRINT_PORT"] = str(port)

    # The server logs every request, so its output goes to a file rather than
    # a pipe: an undrained pipe fills up and blocks the server mid-suite.
    log_path = tmp_path_factory.mktemp("server") / "server.log"
    log_file = log_path.open("wb")

    process = subprocess.Popen(
        [sys.executable, str(ROOT / "app.py")],
        env=environment,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        cwd=str(ROOT),
    )

    def read_log():
        try:
            return log_path.read_text(errors="replace")
        except OSError:
            return "<no output>"

    deadline = time.time() + 30
    while time.time() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"Server exited early:\n{read_log()}")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                break
        except OSError:
            time.sleep(0.2)
    else:
        process.kill()
        raise RuntimeError(f"Server did not start in time:\n{read_log()}")

    yield f"http://127.0.0.1:{port}"

    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
    log_file.close()


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "e2e: browser test, requires Playwright"
    )
