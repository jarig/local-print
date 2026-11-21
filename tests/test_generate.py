"""Generating an image from a description through OpenAI."""
import json
import os

import pytest

import app as app_module
import config
import openai_images
import upscale
from conftest import build_png, http_error

JSON_HEADERS = {"X-Requested-With": "LocalPrint"}


def post_prompt(client, prompt="A red fox in the snow", **fields):
    data = {"prompt": prompt}
    data.update(fields)
    return client.post("/generate", data=data, headers=JSON_HEADERS)


def sent_json(openai):
    return json.loads(openai["calls"][0]["request"].data)


# --------------------------------------------------------------------------
# The API call
# --------------------------------------------------------------------------


def test_generate_sends_the_prompt_and_size(api_key, openai):
    result = openai_images.generate("A lighthouse at dusk", (2416, 3408))

    assert result == b"fake-jpeg"
    request = openai["calls"][0]["request"]
    assert request.full_url == openai_images.GENERATIONS_URL
    assert request.get_header("Authorization") == f"Bearer {api_key}"
    assert request.get_header("Content-type") == "application/json"
    assert sent_json(openai) == {
        "model": "gpt-image-2",
        "prompt": "A lighthouse at dusk",
        "size": "2416x3408",
        "quality": "high",
        "output_format": "jpeg",
        "n": 1,
    }


def test_generate_reports_the_apis_own_error(api_key, openai):
    openai["error"] = http_error(
        400, b'{"error": {"message": "Your request was rejected by the safety system."}}'
    )
    with pytest.raises(openai_images.OpenAIError) as caught:
        openai_images.generate("anything", (1024, 1024))
    assert str(caught.value) == (
        "Image generation failed: "
        "Your request was rejected by the safety system."
    )


def test_generate_needs_a_key(monkeypatch, openai):
    monkeypatch.setattr(config, "OPENAI_API_KEY", "")
    with pytest.raises(openai_images.OpenAIError, match="not configured"):
        openai_images.generate("anything", (1024, 1024))
    assert openai["calls"] == []


@pytest.mark.parametrize("size", app_module.GENERATE_SIZES.values())
def test_generated_sizes_are_a4_shaped_and_drawable(size):
    width, height = size
    assert width % openai_images.SIZE_STEP == 0
    assert height % openai_images.SIZE_STEP == 0
    assert max(size) <= openai_images.MAX_EDGE
    assert openai_images.MIN_PIXELS <= width * height <= openai_images.MAX_PIXELS
    assert max(size) / min(size) == pytest.approx(2 ** 0.5, rel=0.01)


# --------------------------------------------------------------------------
# The route
# --------------------------------------------------------------------------


def test_the_generate_option_is_only_rendered_with_a_key(client, monkeypatch):
    assert 'id="generate"' not in client.get("/").get_data(as_text=True)

    monkeypatch.setattr(config, "OPENAI_API_KEY", "sk-test-key")
    body = client.get("/").get_data(as_text=True)
    assert 'id="generate"' in body
    assert "Generate from text" in body


def test_generate_route_is_off_without_a_key(client, openai):
    assert post_prompt(client).status_code == 404
    assert openai["calls"] == []


def test_generate_route_returns_the_image(client, api_key, openai):
    openai["result"] = build_png(4, 4)
    reply = post_prompt(client)

    assert reply.status_code == 200
    assert reply.mimetype == "image/jpeg"
    assert reply.data == build_png(4, 4)
    assert reply.headers["X-Generated-Width"] == "1024"
    assert reply.headers["X-Generated-Height"] == "1440"
    assert sent_json(openai)["prompt"] == "A red fox in the snow"
    # Cheapest quality, at the smallest A4-shaped size.
    assert sent_json(openai)["quality"] == "low"
    assert sent_json(openai)["size"] == "1024x1440"


def test_generate_route_can_draw_landscape(client, api_key, openai):
    post_prompt(client, orientation="landscape")
    assert sent_json(openai)["size"] == "1440x1024"


def test_generate_route_trims_the_prompt(client, api_key, openai):
    post_prompt(client, prompt="  A cat \n")
    assert sent_json(openai)["prompt"] == "A cat"


@pytest.mark.parametrize(
    "fields, message",
    [
        ({"prompt": ""}, "Describe the image"),
        ({"prompt": "   "}, "Describe the image"),
        ({"prompt": "x" * 4001}, "too long"),
        ({"orientation": "sideways"}, "Invalid orientation"),
    ],
)
def test_generate_route_validates_its_input(client, api_key, openai, fields, message):
    reply = post_prompt(client, **fields)
    assert reply.status_code == 400
    assert message in reply.get_json()["message"]
    assert openai["calls"] == []


def test_generate_route_passes_on_api_failures(client, api_key, openai):
    openai["error"] = http_error(
        429, b'{"error": {"message": "Rate limit reached."}}'
    )
    reply = post_prompt(client)
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
def test_openai_really_generates_an_image():
    # Low quality and the smallest size keep the cost of this check down.
    result = openai_images.generate(
        "A simple red circle on a white background",
        (1024, 1024),
        quality="low",
        api_key=os.environ["LOCALPRINT_TEST_OPENAI_API_KEY"],
    )

    assert upscale.image_size(result) == (1024, 1024)
