"""Upscaling low-resolution images so they print sharply on A4.

A photo that looks fine on a phone can be far too small for a sheet of
paper: stretched across A4 its pixels become visible. This module decides
when that is the case and, if an OpenAI API key is configured, asks an image
model to redraw the picture at a printable resolution.

Everything here uses the standard library, so the feature adds no
dependencies to the server.
"""
import base64
import json
import math
import socket
import struct
import urllib.error
import urllib.request
import uuid

import config

# A4 in inches, portrait.
A4_INCHES = (210 / 25.4, 297 / 25.4)

# Below this an image looks soft or blocky once it fills an A4 page, so the
# upscale button is offered.
LOW_RES_DPI = 150

# What an upscale aims for: photo-quality print resolution.
TARGET_DPI = 300

# Output limits of the image model's custom sizes.
MAX_EDGE = 3840
MAX_PIXELS = 8_294_400
MIN_PIXELS = 655_360
SIZE_STEP = 16
MAX_ASPECT = 3

EDITS_URL = "https://api.openai.com/v1/images/edits"

# Large edits routinely take a minute or more.
TIMEOUT_SECONDS = 300

PROMPT = (
    "Upscale this image to a higher resolution for printing. Reproduce it "
    "faithfully: keep exactly the same composition, framing, colours, "
    "lighting, text, faces and every detail. Only increase sharpness and "
    "clarity, and remove blur, noise and compression artefacts. Do not add, "
    "remove, move or restyle anything."
)

CONTENT_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}


class UpscaleError(Exception):
    """Raised when an image could not be upscaled."""


def enabled():
    return bool(config.OPENAI_API_KEY)


# --------------------------------------------------------------------------
# Deciding whether, and how far, to upscale
# --------------------------------------------------------------------------


def _page_inches(width, height):
    """A4 turned to match the picture, as printing will turn it."""
    short, long = A4_INCHES
    return (short, long) if height >= width else (long, short)


def print_dpi(width, height):
    """Resolution of the image when scaled to fill an A4 page."""
    page_width, page_height = _page_inches(width, height)
    return min(width / page_width, height / page_height)


def target_size(width, height):
    """The size to upscale to, or None if upscaling would not help.

    Aims for TARGET_DPI on A4, keeping the aspect ratio, within what the
    image model can produce.
    """
    if max(width, height) > MAX_ASPECT * min(width, height):
        return None

    scale = min(
        TARGET_DPI / print_dpi(width, height),
        MAX_EDGE / max(width, height),
        math.sqrt(MAX_PIXELS / (width * height)),
    )

    target_width = int(width * scale) // SIZE_STEP * SIZE_STEP
    target_height = int(height * scale) // SIZE_STEP * SIZE_STEP

    if target_width * target_height < MIN_PIXELS:
        return None
    if target_width <= width or target_height <= height:
        return None
    return target_width, target_height


def plan(width, height):
    """Describe an image's print quality and what an upscale would give."""
    # Rounded first, so the verdict agrees with the number shown.
    dpi = round(print_dpi(width, height))
    low_res = dpi < LOW_RES_DPI
    target = target_size(width, height) if low_res else None

    result = {
        "width": width,
        "height": height,
        "dpi": dpi,
        "low_res": low_res,
        "target": None,
    }
    if target:
        result["target"] = {
            "width": target[0],
            "height": target[1],
            "dpi": round(print_dpi(*target)),
        }
    return result


# --------------------------------------------------------------------------
# Reading an image's size without an imaging library
# --------------------------------------------------------------------------

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

# Start-of-frame markers carry the dimensions. C4, C8 and CC share the range
# but mean something else.
JPEG_FRAME_MARKERS = set(range(0xC0, 0xD0)) - {0xC4, 0xC8, 0xCC}

# Markers that stand alone, without a length field.
JPEG_STANDALONE_MARKERS = set(range(0xD0, 0xDA)) | {0x01}


def image_size(data):
    """(width, height) of a PNG or JPEG, read from its header.

    This is the stored size: a JPEG with an EXIF rotation displays with its
    edges swapped.
    """
    if data.startswith(PNG_SIGNATURE) and data[12:16] == b"IHDR":
        return struct.unpack(">II", data[16:24])

    if data.startswith(b"\xff\xd8"):
        offset = 2
        while offset + 4 <= len(data):
            if data[offset] != 0xFF:
                break
            marker = data[offset + 1]
            if marker == 0xFF:  # Fill byte before a marker.
                offset += 1
                continue
            if marker in JPEG_STANDALONE_MARKERS:
                offset += 2
                continue

            (length,) = struct.unpack(">H", data[offset + 2:offset + 4])
            if marker in JPEG_FRAME_MARKERS and offset + 9 <= len(data):
                height, width = struct.unpack(
                    ">HH", data[offset + 5:offset + 9]
                )
                return width, height
            offset += 2 + length

    raise ValueError("Could not read the image. Is it a valid JPEG or PNG?")


# --------------------------------------------------------------------------
# Calling the image model
# --------------------------------------------------------------------------


def _multipart(fields, file_field, filename, content_type, payload):
    boundary = uuid.uuid4().hex
    body = bytearray()

    for name, value in fields.items():
        body += (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
            f"{value}\r\n"
        ).encode()

    body += (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="{file_field}"; '
        f'filename="{filename}"\r\n'
        f"Content-Type: {content_type}\r\n\r\n"
    ).encode()
    body += payload
    body += f"\r\n--{boundary}--\r\n".encode()

    return bytes(body), f"multipart/form-data; boundary={boundary}"


def _api_error(error):
    """The API's own explanation of a failed request, if it gave one."""
    try:
        detail = json.loads(error.read().decode("utf-8"))["error"]["message"]
    except (ValueError, KeyError, TypeError, OSError):
        detail = ""
    return detail or f"HTTP {error.code}"


def upscale(data, suffix, size, api_key=None, model=None):
    """Redraw the image at `size` (width, height). Returns JPEG bytes."""
    api_key = api_key or config.OPENAI_API_KEY
    if not api_key:
        raise UpscaleError("Upscaling is not configured.")

    content_type = CONTENT_TYPES.get(suffix)
    if not content_type:
        raise UpscaleError("Only JPEG and PNG images can be upscaled.")

    body, body_type = _multipart(
        {
            "model": model or config.UPSCALE_MODEL,
            "prompt": PROMPT,
            "size": f"{size[0]}x{size[1]}",
            "quality": "high",
            "output_format": "jpeg",
            "n": "1",
        },
        "image",
        "image" + suffix,
        content_type,
        data,
    )

    request = urllib.request.Request(
        EDITS_URL,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": body_type,
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as reply:
            result = json.loads(reply.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        raise UpscaleError(f"Upscaling failed: {_api_error(error)}")
    except urllib.error.URLError as error:
        raise UpscaleError(f"Could not reach OpenAI: {error.reason}")
    except (TimeoutError, socket.timeout):
        raise UpscaleError("Upscaling timed out. Try again later.")
    except ValueError:
        raise UpscaleError("OpenAI sent a response that could not be read.")

    try:
        return base64.b64decode(result["data"][0]["b64_json"])
    except (KeyError, IndexError, TypeError, ValueError):
        raise UpscaleError("OpenAI did not return an image.")
