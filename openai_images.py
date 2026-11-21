"""OpenAI image API: redrawing an image, and generating one from text.

Uses only the standard library, so the AI features add no dependencies to
the server. Both calls return JPEG bytes, ready to print.
"""
import base64
import json
import socket
import urllib.error
import urllib.request
import uuid

import config

EDITS_URL = "https://api.openai.com/v1/images/edits"
GENERATIONS_URL = "https://api.openai.com/v1/images/generations"

# Large images routinely take a minute or more.
TIMEOUT_SECONDS = 300

# Custom output sizes the image model accepts.
MAX_EDGE = 3840
MAX_PIXELS = 8_294_400
MIN_PIXELS = 655_360
SIZE_STEP = 16
MAX_ASPECT = 3

CONTENT_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}


class OpenAIError(Exception):
    """Raised when the image API did not produce an image."""


def enabled():
    return bool(config.OPENAI_API_KEY)


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


def _send(url, body, content_type, api_key, action):
    """POST to the image API and return the first image it sends back."""
    api_key = api_key or config.OPENAI_API_KEY
    if not api_key:
        raise OpenAIError(f"{action} is not configured.")

    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": content_type,
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as reply:
            result = json.loads(reply.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        raise OpenAIError(f"{action} failed: {_api_error(error)}")
    except urllib.error.URLError as error:
        raise OpenAIError(f"Could not reach OpenAI: {error.reason}")
    except (TimeoutError, socket.timeout):
        raise OpenAIError(f"{action} timed out. Try again later.")
    except ValueError:
        raise OpenAIError("OpenAI sent a response that could not be read.")

    try:
        return base64.b64decode(result["data"][0]["b64_json"])
    except (KeyError, IndexError, TypeError, ValueError):
        raise OpenAIError("OpenAI did not return an image.")


def edit(data, suffix, size, prompt, action, api_key=None, model=None):
    """Redraw an image at `size` (width, height) following `prompt`."""
    content_type = CONTENT_TYPES.get(suffix)
    if not content_type:
        raise OpenAIError("Only JPEG and PNG images can be used.")

    body, body_type = _multipart(
        {
            "model": model or config.IMAGE_MODEL,
            "prompt": prompt,
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
    return _send(EDITS_URL, body, body_type, api_key, action)


def generate(prompt, size, quality="high", api_key=None, model=None):
    """Create a new image from a text description."""
    body = json.dumps(
        {
            "model": model or config.IMAGE_MODEL,
            "prompt": prompt,
            "size": f"{size[0]}x{size[1]}",
            "quality": quality,
            "output_format": "jpeg",
            "n": 1,
        }
    ).encode("utf-8")
    return _send(
        GENERATIONS_URL, body, "application/json", api_key, "Image generation"
    )
