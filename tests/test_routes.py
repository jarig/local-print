"""HTTP behaviour of the print form: uploads, validation, and responses."""
import io
import os

import pytest

import config

JSON_HEADERS = {"X-Requested-With": "LocalPrint"}


def upload(name, payload):
    return (io.BytesIO(payload), name)


def post(client, data, json=False):
    return client.post(
        "/",
        data=data,
        content_type="multipart/form-data",
        headers=JSON_HEADERS if json else None,
    )


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------


def test_get_renders_the_form(client):
    response = client.get("/")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert 'id="print-form"' in body
    assert config.PRINTER in body


def test_get_shows_a_message_from_the_query_string(client):
    response = client.get("/?message=request+id+is+office-9")
    assert "request id is office-9" in response.get_data(as_text=True)


def test_form_declares_multipart_encoding(client):
    body = client.get("/").get_data(as_text=True)
    assert 'enctype="multipart/form-data"' in body


def test_page_is_mobile_ready(client):
    body = client.get("/").get_data(as_text=True)
    assert 'name="viewport"' in body
    assert "width=device-width" in body


def test_assets_are_served(client):
    for path in (
        "/static/style.css",
        "/static/app.js",
        "/static/vendor/pdf.min.js",
        "/static/vendor/pdf.worker.min.js",
    ):
        assert client.get(path).status_code == 200, path


def test_the_page_publishes_the_configured_upload_limit(client):
    # The client-side size check must not be able to drift from the server's.
    body = client.get("/").get_data(as_text=True)
    assert f"maxUploadMb: {config.MAX_UPLOAD_MB}" in body
    assert f"up to {config.MAX_UPLOAD_MB} MB" in body


def test_the_script_takes_its_limit_from_the_page(client):
    script = client.get("/static/app.js").get_data(as_text=True)
    assert "window.LOCALPRINT && window.LOCALPRINT.maxUploadMb" in script
    assert "50 * 1024 * 1024" not in script


# --------------------------------------------------------------------------
# Successful printing
# --------------------------------------------------------------------------


def test_printing_a_pdf_queues_one_job(client, recorded_jobs, pdf_bytes):
    response = post(
        client,
        {"mode": "pdf", "copies": "1", "pdf": upload("a.pdf", pdf_bytes)},
    )

    assert response.status_code == 302
    assert len(recorded_jobs) == 1
    assert recorded_jobs[0]["copies"] == 1
    assert recorded_jobs[0]["duplex"] is False
    assert recorded_jobs[0]["pages"] is None


def test_printing_an_image_queues_one_job(client, recorded_jobs, png_bytes):
    response = post(
        client,
        {"mode": "image", "copies": "1", "image": upload("a.png", png_bytes)},
    )

    assert response.status_code == 302
    assert len(recorded_jobs) == 1


def test_options_are_passed_through_to_the_printer(
    client, recorded_jobs, pdf_bytes
):
    post(
        client,
        {
            "mode": "pdf",
            "copies": "4",
            "duplex": "on",
            "pages": "1,3-5",
            "pdf": upload("a.pdf", pdf_bytes),
        },
    )

    job = recorded_jobs[0]
    assert job["copies"] == 4
    assert job["duplex"] is True
    assert job["pages"] == "1,3-5"


def test_page_expression_is_normalised_before_printing(
    client, recorded_jobs, pdf_bytes
):
    post(
        client,
        {
            "mode": "pdf",
            "copies": "1",
            "pages": " 1 , 3 - 5 ",
            "pdf": upload("a.pdf", pdf_bytes),
        },
    )
    assert recorded_jobs[0]["pages"] == "1,3-5"


def test_uploaded_bytes_reach_the_printer_intact(
    client, recorded_jobs, pdf_bytes
):
    post(
        client,
        {"mode": "pdf", "copies": "1", "pdf": upload("a.pdf", pdf_bytes)},
    )
    assert recorded_jobs[0]["existed"] is True
    assert recorded_jobs[0]["size"] == len(pdf_bytes)


def test_temporary_upload_is_deleted_afterwards(
    client, recorded_jobs, pdf_bytes
):
    post(
        client,
        {"mode": "pdf", "copies": "1", "pdf": upload("a.pdf", pdf_bytes)},
    )
    assert not os.path.exists(recorded_jobs[0]["filename"])


def test_temporary_file_keeps_the_correct_suffix(
    client, recorded_jobs, pdf_bytes, png_bytes
):
    post(
        client,
        {"mode": "pdf", "copies": "1", "pdf": upload("a.pdf", pdf_bytes)},
    )
    post(
        client,
        {"mode": "image", "copies": "1", "image": upload("b.PNG", png_bytes)},
    )

    assert recorded_jobs[0]["filename"].endswith(".pdf")
    assert recorded_jobs[1]["filename"].endswith(".png")


def test_client_filename_is_not_reused_on_disk(
    client, recorded_jobs, pdf_bytes
):
    """A crafted upload name must not become the temporary path."""
    post(
        client,
        {
            "mode": "pdf",
            "copies": "1",
            "pdf": upload("../../etc/passwd.pdf", pdf_bytes),
        },
    )
    assert "passwd" not in recorded_jobs[0]["filename"]


def test_success_redirects_so_a_refresh_cannot_reprint(
    client, recorded_jobs, pdf_bytes
):
    response = post(
        client,
        {"mode": "pdf", "copies": "1", "pdf": upload("a.pdf", pdf_bytes)},
    )

    assert response.status_code == 302
    assert "message=" in response.headers["Location"]

    # Following the redirect must not queue a second job.
    client.get(response.headers["Location"])
    assert len(recorded_jobs) == 1


def test_uppercase_extensions_are_accepted(
    client, recorded_jobs, pdf_bytes, png_bytes
):
    assert post(
        client,
        {"mode": "pdf", "copies": "1", "pdf": upload("A.PDF", pdf_bytes)},
    ).status_code == 302
    assert post(
        client,
        {"mode": "image", "copies": "1", "image": upload("B.JPG", png_bytes)},
    ).status_code == 302
    assert len(recorded_jobs) == 2


@pytest.mark.parametrize("name", ["a.jpg", "a.jpeg", "a.png"])
def test_all_supported_image_types_print(client, recorded_jobs, png_bytes, name):
    response = post(
        client,
        {"mode": "image", "copies": "1", "image": upload(name, png_bytes)},
    )
    assert response.status_code == 302


@pytest.mark.parametrize("copies", ["1", "20"])
def test_copies_at_the_boundaries_are_allowed(
    client, recorded_jobs, pdf_bytes, copies
):
    response = post(
        client,
        {"mode": "pdf", "copies": copies, "pdf": upload("a.pdf", pdf_bytes)},
    )
    assert response.status_code == 302
    assert recorded_jobs[0]["copies"] == int(copies)


# --------------------------------------------------------------------------
# Rejected requests
# --------------------------------------------------------------------------


def test_unknown_mode_is_rejected(client, recorded_jobs, pdf_bytes):
    response = post(
        client,
        {"mode": "fax", "copies": "1", "pdf": upload("a.pdf", pdf_bytes)},
    )
    assert response.status_code == 400
    assert not recorded_jobs


def test_missing_mode_is_rejected(client, recorded_jobs, pdf_bytes):
    response = post(client, {"copies": "1", "pdf": upload("a.pdf", pdf_bytes)})
    assert response.status_code == 400
    assert not recorded_jobs


def test_pdf_mode_requires_a_file(client, recorded_jobs):
    response = post(client, {"mode": "pdf", "copies": "1"})
    assert response.status_code == 400
    assert not recorded_jobs


def test_image_mode_requires_a_file(client, recorded_jobs):
    response = post(client, {"mode": "image", "copies": "1"})
    assert response.status_code == 400
    assert not recorded_jobs


def test_non_pdf_upload_is_rejected_in_pdf_mode(
    client, recorded_jobs, png_bytes
):
    response = post(
        client,
        {"mode": "pdf", "copies": "1", "pdf": upload("a.png", png_bytes)},
    )
    assert response.status_code == 400
    assert not recorded_jobs


def test_unsupported_image_type_is_rejected(client, recorded_jobs, pdf_bytes):
    response = post(
        client,
        {"mode": "image", "copies": "1", "image": upload("a.gif", pdf_bytes)},
    )
    assert response.status_code == 400
    assert not recorded_jobs


def test_sending_both_a_pdf_and_an_image_is_rejected(
    client, recorded_jobs, pdf_bytes, png_bytes
):
    response = post(
        client,
        {
            "mode": "pdf",
            "copies": "1",
            "pdf": upload("a.pdf", pdf_bytes),
            "image": upload("a.png", png_bytes),
        },
    )
    assert response.status_code == 400
    assert not recorded_jobs


@pytest.mark.parametrize("copies", ["0", "-1", "21", "999", "abc", "1.5", ""])
def test_invalid_copies_are_rejected(
    client, recorded_jobs, pdf_bytes, copies
):
    response = post(
        client,
        {"mode": "pdf", "copies": copies, "pdf": upload("a.pdf", pdf_bytes)},
    )
    assert response.status_code == 400
    assert not recorded_jobs


def test_invalid_page_expression_is_rejected(client, recorded_jobs, pdf_bytes):
    response = post(
        client,
        {
            "mode": "pdf",
            "copies": "1",
            "pages": "not-pages",
            "pdf": upload("a.pdf", pdf_bytes),
        },
    )
    assert response.status_code == 400
    assert not recorded_jobs


# --------------------------------------------------------------------------
# Printer options
# --------------------------------------------------------------------------


def test_the_form_offers_the_printers_own_options(client):
    body = client.get("/").get_data(as_text=True)
    assert 'name="opt_ColorModel"' in body
    assert 'value="Gray"' in body
    assert "Black &amp; white" in body


def test_the_printers_default_is_preselected(client):
    body = client.get("/").get_data(as_text=True)
    colour = body[body.index('name="opt_ColorModel"'):]
    # RGB is starred in the discovered output, so it must arrive checked.
    assert colour[: colour.index("Gray")].count("checked") == 1


def test_duplex_is_not_offered_as_a_printer_option(client):
    # It has its own toggle; two controls for one setting would conflict.
    assert 'name="opt_Duplex"' not in client.get("/").get_data(as_text=True)


def test_the_options_panel_starts_collapsed(client):
    body = client.get("/").get_data(as_text=True)
    panel = body[body.index('<details class="options"'):]
    assert " open>" not in panel[: panel.index(">") + 1]
    # Collapsed, it must still say something useful without JavaScript.
    assert "Printer defaults" in body


def test_paper_type_starts_on_auto(client, recorded_jobs, pdf_bytes):
    body = client.get("/").get_data(as_text=True)
    media = body[body.index('name="opt_MediaType"'):]
    chosen = media[: media.index("</select>")]
    assert 'value="Auto"' in chosen
    selected = chosen[chosen.index('value="Auto"'):]
    assert "selected" in selected[: selected.index("</option>")]


def test_a_chosen_option_reaches_the_printer(
    client, recorded_jobs, pdf_bytes
):
    post(
        client,
        {
            "mode": "pdf",
            "copies": "1",
            "opt_ColorModel": "Gray",
            "pdf": upload("a.pdf", pdf_bytes),
        },
    )
    assert recorded_jobs[0]["options"] == {"ColorModel": "Gray"}


def test_several_options_reach_the_printer_together(
    client, recorded_jobs, pdf_bytes
):
    post(
        client,
        {
            "mode": "pdf",
            "copies": "1",
            "opt_ColorModel": "Gray",
            "opt_cupsPrintQuality": "Draft",
            "opt_PageSize": "A5",
            "pdf": upload("a.pdf", pdf_bytes),
        },
    )
    assert recorded_jobs[0]["options"] == {
        "ColorModel": "Gray",
        "cupsPrintQuality": "Draft",
        "PageSize": "A5",
    }


def test_omitted_options_are_left_to_the_printer(
    client, recorded_jobs, pdf_bytes
):
    post(
        client,
        {"mode": "pdf", "copies": "1", "pdf": upload("a.pdf", pdf_bytes)},
    )
    assert recorded_jobs[0]["options"] == {}


@pytest.mark.parametrize(
    "value",
    [
        "Sepia",             # not offered by this printer
        "Gray extra",
        "Gray;rm -rf /",
        "$(reboot)",
        "../../etc/passwd",
    ],
)
def test_a_value_the_printer_never_offered_is_rejected(
    client, recorded_jobs, pdf_bytes, value
):
    """The discovered choices are an allow-list.

    Nothing the client sends may reach the lp command line unless the
    printer itself reported it as a valid choice.
    """
    response = post(
        client,
        {
            "mode": "pdf",
            "copies": "1",
            "opt_ColorModel": value,
            "pdf": upload("a.pdf", pdf_bytes),
        },
    )
    assert response.status_code == 400
    assert not recorded_jobs


def test_an_option_the_printer_does_not_have_is_ignored(
    client, recorded_jobs, pdf_bytes
):
    # Only discovered keywords are read, so stray fields cannot inject flags.
    post(
        client,
        {
            "mode": "pdf",
            "copies": "1",
            "opt_StapleLocation": "UpperLeft",
            "pdf": upload("a.pdf", pdf_bytes),
        },
    )
    assert recorded_jobs[0]["options"] == {}


def test_rejecting_an_option_explains_which_one(
    client, recorded_jobs, pdf_bytes
):
    response = post(
        client,
        {
            "mode": "pdf",
            "copies": "1",
            "opt_ColorModel": "Sepia",
            "pdf": upload("a.pdf", pdf_bytes),
        },
        json=True,
    )
    assert "Colour" in response.get_json()["message"]


def test_the_form_still_works_when_the_printer_cannot_be_queried(
    client, recorded_jobs, pdf_bytes, monkeypatch
):
    import printing

    monkeypatch.setattr(printing, "discover_options", lambda *a, **k: [])

    assert client.get("/").status_code == 200
    post(
        client,
        {"mode": "pdf", "copies": "1", "pdf": upload("a.pdf", pdf_bytes)},
    )
    assert len(recorded_jobs) == 1


def test_oversized_upload_is_rejected_politely(client, recorded_jobs):
    payload = b"%PDF-1.4\n" + b"0" * (config.MAX_UPLOAD_SIZE + 1024)
    response = post(
        client,
        {"mode": "pdf", "copies": "1", "pdf": upload("big.pdf", payload)},
    )

    assert response.status_code == 413
    assert "too large" in response.get_data(as_text=True).lower()
    assert not recorded_jobs


def test_a_printer_failure_is_reported_as_a_server_error(
    client, monkeypatch, pdf_bytes
):
    import app as app_module
    import printing

    def explode(*_args, **_kwargs):
        raise printing.PrintError("Print failed: printer is on fire")

    monkeypatch.setattr(app_module.printing, "submit", explode)

    response = post(
        client,
        {"mode": "pdf", "copies": "1", "pdf": upload("a.pdf", pdf_bytes)},
    )
    assert response.status_code == 500
    assert "on fire" in response.get_data(as_text=True)


def test_upload_is_cleaned_up_even_when_printing_fails(
    client, monkeypatch, pdf_bytes
):
    import app as app_module
    import printing

    captured = {}

    def explode(filename, *_args, **_kwargs):
        captured["filename"] = filename
        raise printing.PrintError("nope")

    monkeypatch.setattr(app_module.printing, "submit", explode)
    post(
        client,
        {"mode": "pdf", "copies": "1", "pdf": upload("a.pdf", pdf_bytes)},
    )

    assert not os.path.exists(captured["filename"])


# --------------------------------------------------------------------------
# JSON responses for the enhanced client
# --------------------------------------------------------------------------


def test_xhr_success_returns_json_instead_of_a_redirect(
    client, recorded_jobs, pdf_bytes
):
    response = post(
        client,
        {"mode": "pdf", "copies": "1", "pdf": upload("a.pdf", pdf_bytes)},
        json=True,
    )

    assert response.status_code == 200
    assert response.is_json
    assert response.get_json()["ok"] is True
    assert "office" in response.get_json()["message"]


def test_xhr_failure_returns_json_with_the_reason(client, recorded_jobs):
    response = post(client, {"mode": "pdf", "copies": "1"}, json=True)

    assert response.status_code == 400
    assert response.is_json
    assert response.get_json()["ok"] is False
    assert "PDF" in response.get_json()["message"]


def test_xhr_oversized_upload_returns_json(client):
    payload = b"%PDF-1.4\n" + b"0" * (config.MAX_UPLOAD_SIZE + 1024)
    response = post(
        client,
        {"mode": "pdf", "copies": "1", "pdf": upload("big.pdf", payload)},
        json=True,
    )

    assert response.status_code == 413
    assert response.get_json()["ok"] is False
