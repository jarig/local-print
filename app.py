#!/usr/bin/env python3
"""LocalPrint - a mobile-friendly LAN web UI for printing PDFs and images."""
import os
import re
import socket
import tempfile

from flask import (
    Flask,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)

import config
import printing

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = config.MAX_UPLOAD_SIZE

PAGE_RANGE_PATTERN = re.compile(r"\d+(?:-\d+)?(?:,\d+(?:-\d+)?)*")
DIGITS_SPLIT_BY_SPACE = re.compile(r"\d\s+\d")


def get_lan_ip():
    """Find this machine's IPv4 address inside the LAN subnet."""
    addresses = set()
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(
            hostname, None, family=socket.AF_INET, type=socket.SOCK_STREAM
        ):
            addresses.add(info[4][0])
    except socket.gaierror:
        pass

    # Helps when hostname resolution does not expose the LAN interface.
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect((config.LAN_PREFIX + "1", 9))
        addresses.add(sock.getsockname()[0])
        sock.close()
    except OSError:
        pass

    for address in sorted(addresses):
        if address.startswith(config.LAN_PREFIX):
            return address

    raise RuntimeError(
        f"No {config.LAN_PREFIX}x LAN interface found. Refusing to start."
    )


def validate_page_range(value):
    """Accept expressions such as 1, 1-5, 1,3,7 or 1-3,5,8-10."""
    if not value:
        return None

    value = value.strip()
    if not value:
        return None

    # Whitespace around separators is fine, but "1 3" must not silently
    # collapse into page 13.
    if DIGITS_SPLIT_BY_SPACE.search(value):
        raise ValueError(
            "Separate page numbers with commas, e.g. 1,3 or 1-3."
        )

    value = re.sub(r"\s+", "", value)
    if not PAGE_RANGE_PATTERN.fullmatch(value):
        raise ValueError(
            "Invalid page selection. Use e.g. 1, 1-3, or 1,3,5-7."
        )

    for part in value.split(","):
        if "-" in part:
            start, end = map(int, part.split("-", 1))
            if start < 1 or end < 1 or start > end:
                raise ValueError(f"Invalid page range: {part}")
        elif int(part) < 1:
            raise ValueError("Page numbers must start at 1.")

    return value


def wants_json():
    """True when the request came from the fetch/XHR enhanced form."""
    return request.headers.get("X-Requested-With") == "LocalPrint"


def respond(message, status=200, ok=False):
    """Render the form, or reply with JSON for the enhanced client."""
    if wants_json():
        return jsonify(ok=ok, message=message), status
    return render_template(
        "index.html",
        message=message,
        ok=ok,
        printer=config.PRINTER,
        max_upload_mb=config.MAX_UPLOAD_MB,
        min_copies=config.MIN_COPIES,
        max_copies=config.MAX_COPIES,
        printer_options=printing.discover_options(),
    ), status


def select_printer_options():
    """Read the print options from the form, allowing only real choices.

    The browser submits a keyword's *value*, never a whole `lp` option, and
    each value has to be one the printer told us about. Nothing the client
    sends is ever passed through to the command line unchecked.
    """
    chosen = {}

    for option in printing.discover_options():
        value = (request.form.get("opt_" + option["keyword"]) or "").strip()
        if not value:
            continue

        if value not in {choice["value"] for choice in option["choices"]}:
            raise ValueError(
                f"{option['label']}: {value} is not supported by this printer."
            )
        chosen[option["keyword"]] = value

    return chosen


def parse_copies():
    raw = request.form.get("copies", str(config.MIN_COPIES))
    try:
        copies = int(raw)
    except (TypeError, ValueError):
        raise ValueError(
            f"Copies must be a whole number between "
            f"{config.MIN_COPIES} and {config.MAX_COPIES}."
        )
    if copies < config.MIN_COPIES or copies > config.MAX_COPIES:
        raise ValueError(
            f"Copies must be between {config.MIN_COPIES} "
            f"and {config.MAX_COPIES}."
        )
    return copies


def select_upload(mode):
    """Return (upload, suffix, pages) for the requested mode."""
    pdf_upload = request.files.get("pdf")
    image_upload = request.files.get("image")
    has_pdf = bool(pdf_upload and pdf_upload.filename)
    has_image = bool(image_upload and image_upload.filename)

    if has_pdf and has_image:
        raise ValueError("Choose either a PDF or an image, not both.")

    if mode == "pdf":
        if not has_pdf:
            raise ValueError("Please select a PDF file.")
        _, suffix = os.path.splitext(pdf_upload.filename.lower())
        if suffix not in config.PDF_EXTENSIONS:
            raise ValueError("The selected file must be a PDF.")
        pages = validate_page_range(request.form.get("pages", ""))
        return pdf_upload, suffix, pages

    if not has_image:
        raise ValueError("Please select an image.")
    _, suffix = os.path.splitext(image_upload.filename.lower())
    if suffix not in config.IMAGE_EXTENSIONS:
        raise ValueError("Image must be a JPEG or PNG.")
    return image_upload, suffix, None


@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "GET":
        # Success messages arrive via the post/redirect/get query string.
        return respond(request.args.get("message"), ok=True)

    mode = request.form.get("mode")
    if mode not in {"pdf", "image"}:
        return respond("Invalid print mode.", 400)

    temp_filename = None
    try:
        copies = parse_copies()
        duplex = "duplex" in request.form
        printer_options = select_printer_options()
        upload, suffix, pages = select_upload(mode)

        with tempfile.NamedTemporaryFile(
            suffix=suffix, delete=False
        ) as tmp:
            temp_filename = tmp.name
        upload.save(temp_filename)

        message = printing.submit(
            temp_filename, copies, duplex, pages, printer_options
        )
    except ValueError as error:
        return respond(str(error), 400)
    except printing.PrintError as error:
        return respond(str(error), 500)
    except Exception as error:  # noqa: BLE001 - surfaced to the user
        return respond(f"Error: {error}", 500)
    finally:
        if temp_filename and os.path.exists(temp_filename):
            os.unlink(temp_filename)

    if wants_json():
        return jsonify(ok=True, message=message)

    # Post/redirect/get so a refresh never reprints the job.
    return redirect(url_for("index", message=message))


@app.errorhandler(413)
def too_large(_error):
    return respond(
        f"File is too large. Maximum size is {config.MAX_UPLOAD_MB} MB.",
        413,
    )


def main():
    if config.FAKE_PRINTER:
        host = "127.0.0.1"
        print(f"LocalPrint (fake printer): http://{host}:{config.PORT}")
    else:
        host = get_lan_ip()
        print(f"LocalPrint server: http://{host}:{config.PORT}")

    app.run(host=host, port=config.PORT, debug=config.DEBUG)


if __name__ == "__main__":
    main()
