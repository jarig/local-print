"""Upscaling low-resolution images: when it is offered, and the API call."""
import io
import os
import struct
import urllib.error

import pytest

import config
import openai_images
import upscale
from conftest import build_png, http_error

JSON_HEADERS = {"X-Requested-With": "LocalPrint"}


def build_jpeg_header(width, height):
    """Just enough of a JPEG for its size to be read: SOI, APP0, SOF0."""
    app0 = b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    sof0 = struct.pack(">BHHB", 8, height, width, 3) + b"\x01\x22\x00" * 3
    return (
        b"\xff\xd8"
        + b"\xff\xe0" + struct.pack(">H", len(app0) + 2) + app0
        + b"\xff\xc0" + struct.pack(">H", len(sof0) + 2) + sof0
        + b"\xff\xd9"
    )


# --------------------------------------------------------------------------
# When to offer an upscale
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "size, low_res",
    [
        ((600, 800), True),
        ((1000, 1333), True),
        ((1240, 1754), False),  # 150 dpi on A4: the threshold.
        ((2480, 3508), False),
        ((4000, 3000), False),
    ],
)
def test_low_resolution_is_judged_against_a4(size, low_res):
    assert upscale.plan(*size)["low_res"] is low_res


def test_landscape_images_are_measured_against_landscape_a4():
    assert upscale.plan(800, 600)["dpi"] == upscale.plan(600, 800)["dpi"]


def test_sharp_images_get_no_target():
    assert upscale.plan(2480, 3508)["target"] is None


@pytest.mark.parametrize(
    "size", [(600, 800), (800, 600), (64, 64), (300, 100), (1000, 1333)]
)
def test_targets_fit_what_the_model_can_produce(size):
    target = upscale.plan(*size)["target"]
    width, height = target["width"], target["height"]

    assert width % 16 == 0 and height % 16 == 0
    assert max(width, height) <= upscale.MAX_EDGE
    assert upscale.MIN_PIXELS <= width * height <= upscale.MAX_PIXELS
    assert width > size[0] and height > size[1]
    # The picture keeps its shape, give or take the rounding to 16px.
    assert abs(width / height - size[0] / size[1]) < 0.02


def test_target_reports_the_resulting_print_resolution():
    plan = upscale.plan(600, 800)
    assert plan["dpi"] == 68
    assert plan["target"] == {"width": 2480, "height": 3312, "dpi": 283}


def test_shapes_the_model_cannot_draw_are_not_offered():
    plan = upscale.plan(400, 100)  # 4:1, beyond the model's 3:1 limit.
    assert plan["low_res"] is True
    assert plan["target"] is None


# --------------------------------------------------------------------------
# Reading the size
# --------------------------------------------------------------------------


def test_png_size_is_read_from_the_header():
    assert upscale.image_size(build_png(37, 53)) == (37, 53)


def test_jpeg_size_is_read_from_the_frame_header():
    assert upscale.image_size(build_jpeg_header(640, 480)) == (640, 480)


@pytest.mark.parametrize("data", [b"", b"not an image", b"\xff\xd8\xff\xd9"])
def test_unreadable_images_are_refused(data):
    with pytest.raises(ValueError):
        upscale.image_size(data)


# --------------------------------------------------------------------------
# Calling OpenAI
# --------------------------------------------------------------------------


def test_upscale_sends_the_image_and_size(api_key, openai):
    png = build_png(10, 12)
    result = upscale.upscale(png, ".png", (2480, 3312))

    assert result == b"fake-jpeg"
    request = openai["calls"][0]["request"]
    assert request.full_url == openai_images.EDITS_URL
    assert request.get_header("Authorization") == f"Bearer {api_key}"

    body = request.data
    assert b'name="size"\r\n\r\n2480x3312\r\n' in body
    assert b'name="model"\r\n\r\ngpt-image-2\r\n' in body
    assert b'name="output_format"\r\n\r\njpeg\r\n' in body
    assert b"Content-Type: image/png\r\n\r\n" + png in body


def test_upscale_reports_the_apis_own_error(api_key, openai):
    openai["error"] = http_error(
        400, b'{"error": {"message": "Your request was rejected."}}'
    )
    with pytest.raises(openai_images.OpenAIError, match="Your request was rejected"):
        upscale.upscale(build_png(), ".png", (1024, 1024))


def test_upscale_survives_an_error_without_a_body(api_key, openai):
    openai["error"] = http_error(503, b"")
    with pytest.raises(openai_images.OpenAIError, match="HTTP 503"):
        upscale.upscale(build_png(), ".png", (1024, 1024))


def test_upscale_reports_an_unreachable_api(api_key, openai):
    openai["error"] = urllib.error.URLError("no route to host")
    with pytest.raises(openai_images.OpenAIError, match="Could not reach OpenAI"):
        upscale.upscale(build_png(), ".png", (1024, 1024))


def test_upscale_needs_a_key(monkeypatch, openai):
    monkeypatch.setattr(config, "OPENAI_API_KEY", "")
    with pytest.raises(openai_images.OpenAIError, match="not configured"):
        upscale.upscale(build_png(), ".png", (1024, 1024))
    assert openai["calls"] == []


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------


def post_image(client, payload, name="small.png", **fields):
    data = {"image": (io.BytesIO(payload), name)}
    data.update({key: str(value) for key, value in fields.items()})
    return client.post(
        "/upscale",
        data=data,
        content_type="multipart/form-data",
        headers=JSON_HEADERS,
    )


def test_the_button_is_only_rendered_with_a_key(client, monkeypatch):
    assert 'id="upscale"' not in client.get("/").get_data(as_text=True)

    monkeypatch.setattr(config, "OPENAI_API_KEY", "sk-test-key")
    assert 'id="upscale"' in client.get("/").get_data(as_text=True)


def test_routes_are_off_without_a_key(client, openai):
    assert client.get("/upscale/plan?width=10&height=10").status_code == 404
    assert post_image(client, build_png()).status_code == 404
    assert openai["calls"] == []


def test_plan_route_describes_the_image(client, api_key):
    reply = client.get("/upscale/plan?width=600&height=800").get_json()
    assert reply["ok"] is True
    assert reply["low_res"] is True
    assert reply["target"]["width"] == 2480


@pytest.mark.parametrize(
    "query", ["", "width=10", "width=a&height=10", "width=0&height=10",
              "width=10&height=999999"]
)
def test_plan_route_rejects_bad_sizes(client, api_key, query):
    assert client.get("/upscale/plan?" + query).status_code == 400


def test_upscale_route_returns_the_upscaled_image(client, api_key, openai):
    reply = post_image(client, build_png(60, 80))

    assert reply.status_code == 200
    assert reply.mimetype == "image/jpeg"
    assert reply.data == b"fake-jpeg"
    assert reply.headers["X-Upscaled-Width"] == "2480"
    assert reply.headers["X-Upscaled-Height"] == "3312"


def test_upscale_route_follows_the_displayed_orientation(
    client, api_key, openai
):
    # Stored landscape, but an EXIF rotation shows it portrait.
    reply = post_image(
        client, build_jpeg_header(80, 60), name="photo.jpg", width=60, height=80
    )

    assert reply.status_code == 200
    assert b'name="size"\r\n\r\n2480x3312\r\n' in openai["calls"][0]["request"].data
    assert b"Content-Type: image/jpeg" in openai["calls"][0]["request"].data


def test_upscale_route_ignores_a_size_that_does_not_match(
    client, api_key, openai
):
    post_image(client, build_png(60, 80), width=1000, height=2000)
    assert b'name="size"\r\n\r\n2480x3312\r\n' in openai["calls"][0]["request"].data


def test_upscale_route_refuses_sharp_images(client, api_key, openai):
    reply = post_image(client, build_png(2480, 3508))
    assert reply.status_code == 400
    assert "already sharp" in reply.get_json()["message"]
    assert openai["calls"] == []


@pytest.mark.parametrize(
    "payload, name, message",
    [
        (b"%PDF-1.4", "doc.pdf", "JPEG or PNG"),
        (b"garbage", "fake.png", "valid JPEG or PNG"),
    ],
)
def test_upscale_route_refuses_non_images(
    client, api_key, openai, payload, name, message
):
    reply = post_image(client, payload, name=name)
    assert reply.status_code == 400
    assert message in reply.get_json()["message"]
    assert openai["calls"] == []


def test_upscale_route_passes_on_api_failures(client, api_key, openai):
    openai["error"] = http_error(
        429, b'{"error": {"message": "Rate limit reached."}}'
    )
    reply = post_image(client, build_png(60, 80))
    assert reply.status_code == 502
    assert "Rate limit reached" in reply.get_json()["message"]


# --------------------------------------------------------------------------
# The real thing, opt-in
# --------------------------------------------------------------------------


@pytest.mark.openai
@pytest.mark.skipif(
    not os.environ.get("LOCALPRINT_TEST_OPENAI_API_KEY"),
    reason="set LOCALPRINT_TEST_OPENAI_API_KEY to call OpenAI (costs money)",
)
def test_openai_really_upscales_a_small_image():
    plan = upscale.plan(48, 64)
    size = (plan["target"]["width"], plan["target"]["height"])

    result = upscale.upscale(
        build_png(48, 64),
        ".png",
        size,
        api_key=os.environ["LOCALPRINT_TEST_OPENAI_API_KEY"],
    )

    assert upscale.image_size(result) == size
