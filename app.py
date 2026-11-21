#!/usr/bin/env python3
"""LocalPrint - a mobile-friendly LAN web UI for printing PDFs and images."""
import os
import re
import socket
import tempfile

from flask import (
    Flask,
    Response,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)

import config
import openai_images
import printing
import upscale

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
        queue=printing.queue_status(),
        ai_enabled=openai_images.enabled(),
        max_prompt_length=MAX_PROMPT_LENGTH,
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


@app.route("/queue")
def queue():
    """The queue's current state, for the status line to poll."""
    return jsonify(printing.queue_status())


@app.route("/queue/clear", methods=["POST"])
def clear_queue():
    """Discard everything waiting and restart a stopped queue."""
    try:
        message = printing.clear_queue()
    except printing.PrintError as error:
        return respond(str(error), 500)

    if wants_json():
        return jsonify(
            ok=True, message=message, queue=printing.queue_status()
        )

    return redirect(url_for("index", message=message))


# Generous, but stops an absurd size from reaching the arithmetic.
MAX_IMAGE_EDGE = 100_000


def parse_dimension(name, required=True):
    raw = request.values.get(name, "")
    if not raw and not required:
        return None
    try:
        value = int(raw)
    except ValueError:
        raise ValueError(f"{name} must be a whole number of pixels.")
    if not 1 <= value <= MAX_IMAGE_EDGE:
        raise ValueError(f"{name} must be between 1 and {MAX_IMAGE_EDGE}.")
    return value


@app.route("/upscale/plan")
def upscale_plan():
    """Whether an image of this size is too small for A4, and the fix."""
    if not openai_images.enabled():
        return jsonify(ok=False, message="Upscaling is not configured."), 404
    try:
        width = parse_dimension("width")
        height = parse_dimension("height")
    except ValueError as error:
        return jsonify(ok=False, message=str(error)), 400
    return jsonify(ok=True, **upscale.plan(width, height))


@app.route("/upscale", methods=["POST"])
def upscale_image():
    """Upscale an image and send it back for the browser to print."""
    if not openai_images.enabled():
        return jsonify(ok=False, message="Upscaling is not configured."), 404

    image = request.files.get("image")
    if not image or not image.filename:
        return jsonify(ok=False, message="Please select an image."), 400

    _, suffix = os.path.splitext(image.filename.lower())
    if suffix not in config.IMAGE_EXTENSIONS:
        return jsonify(ok=False, message="Image must be a JPEG or PNG."), 400

    data = image.read()
    try:
        width, height = upscale.image_size(data)

        # The browser reports the size as displayed. A photo with an EXIF
        # rotation stores its edges the other way round, and only the
        # displayed orientation tells the model which way up to draw it.
        shown = (
            parse_dimension("width", required=False),
            parse_dimension("height", required=False),
        )
        if all(shown) and sorted(shown) == sorted((width, height)):
            width, height = shown
    except ValueError as error:
        return jsonify(ok=False, message=str(error)), 400

    target = upscale.plan(width, height)["target"]
    if not target:
        return jsonify(
            ok=False,
            message="This image is already sharp enough for A4.",
        ), 400

    size = (target["width"], target["height"])
    try:
        result = upscale.upscale(data, suffix, size)
    except openai_images.OpenAIError as error:
        return jsonify(ok=False, message=str(error)), 502

    return Response(
        result,
        mimetype="image/jpeg",
        headers={
            "X-Upscaled-Width": str(size[0]),
            "X-Upscaled-Height": str(size[1]),
            "Cache-Control": "no-store",
        },
    )


# A4's proportions (1:√2) at the smallest size that still previews well,
# about 123 dpi on paper. Output tokens, and so cost, grow with the pixel
# count; a picture worth printing sharply can be upscaled afterwards.
GENERATE_SIZES = {
    "portrait": (1024, 1440),
    "landscape": (1440, 1024),
}

# Low quality costs about a tenth of medium (153 against 1,418 output tokens
# at 1024x1440) and is barely distinguishable on paper.
GENERATE_QUALITY = "low"

# The model accepts far more, but a description longer than this is almost
# certainly a paste gone wrong.
MAX_PROMPT_LENGTH = 4000


@app.route("/generate", methods=["POST"])
def generate_image():
    """Draw an image from a description, for the browser to preview."""
    if not openai_images.enabled():
        return jsonify(ok=False, message="Image generation is not configured."), 404

    prompt = (request.form.get("prompt") or "").strip()
    if not prompt:
        return jsonify(ok=False, message="Describe the image to generate."), 400
    if len(prompt) > MAX_PROMPT_LENGTH:
        return jsonify(
            ok=False,
            message=f"The description is too long. Keep it under "
            f"{MAX_PROMPT_LENGTH} characters.",
        ), 400

    orientation = request.form.get("orientation") or "portrait"
    size = GENERATE_SIZES.get(orientation)
    if not size:
        return jsonify(ok=False, message="Invalid orientation."), 400

    try:
        result = openai_images.generate(prompt, size, quality=GENERATE_QUALITY)
    except openai_images.OpenAIError as error:
        return jsonify(ok=False, message=str(error)), 502

    return Response(
        result,
        mimetype="image/jpeg",
        headers={
            "X-Generated-Width": str(size[0]),
            "X-Generated-Height": str(size[1]),
            "Cache-Control": "no-store",
        },
    )


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
