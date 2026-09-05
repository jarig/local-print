"""Browser tests for the print form.

These drive the real UI against a live server with the printer stubbed out,
covering the parts that only exist in JavaScript: mode switching, drag and
drop, the PDF page preview, and the no-JavaScript fallback.
"""
import re

import pytest

playwright_api = pytest.importorskip(
    "playwright.sync_api", reason="Playwright is not installed"
)
sync_playwright = playwright_api.sync_playwright
expect = playwright_api.expect

pytestmark = pytest.mark.e2e

PHONE = {"width": 390, "height": 844}

# The stubbed printer always reports a colour choice, so it is the handiest
# option to drive in tests.
COLOUR = 'input[name="opt_ColorModel"][value="RGB"]'
GRAY = 'input[name="opt_ColorModel"][value="Gray"]'


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


@pytest.fixture(scope="session")
def browser():
    with sync_playwright() as playwright:
        instance = playwright.chromium.launch()
        yield instance
        instance.close()


@pytest.fixture
def page(browser, live_server):
    context = browser.new_context(viewport=PHONE)
    page = context.new_page()

    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on(
        "console",
        lambda message: (
            errors.append(message.text) if message.type == "error" else None
        ),
    )

    page.goto(live_server)
    page.wait_for_function("() => !document.documentElement.classList.contains('no-js')")

    yield page

    # No test should leave console errors behind.
    assert errors == [], f"Console errors: {errors}"
    context.close()


@pytest.fixture
def no_js_page(browser, live_server):
    """A page where app.js never loads, exercising the fallback form."""
    context = browser.new_context(viewport=PHONE)
    page = context.new_page()
    page.route("**/static/app.js", lambda route: route.abort())
    page.goto(live_server)
    yield page
    context.close()


def choose(page, selector, path):
    page.set_input_files(selector, path)


MIME_BY_SUFFIX = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".txt": "text/plain",
}


def drop(page, path):
    """Dispatch a genuine drop event carrying the file onto the drop zone."""
    import base64
    import pathlib

    path = pathlib.Path(path)
    payload = base64.b64encode(path.read_bytes()).decode("ascii")
    mime = MIME_BY_SUFFIX[path.suffix.lower()]

    page.evaluate(
        """
        ({ name, mime, payload }) => {
            const binary = atob(payload);
            const bytes = new Uint8Array(binary.length);
            for (let i = 0; i < binary.length; i++) {
                bytes[i] = binary.charCodeAt(i);
            }
            const file = new File([bytes], name, { type: mime });
            const transfer = new DataTransfer();
            transfer.items.add(file);
            document.getElementById("drop-zone").dispatchEvent(
                new DragEvent("drop", {
                    bubbles: true,
                    cancelable: true,
                    dataTransfer: transfer,
                })
            );
        }
        """,
        {"name": path.name, "mime": mime, "payload": payload},
    )


def select_pdf(page, path, pages=7):
    choose(page, "#pdf", path)
    page.wait_for_selector(".page-tile")
    if pages:
        page.wait_for_function(
            "count => document.querySelectorAll('.page-tile').length === count",
            arg=pages,
        )


# --------------------------------------------------------------------------
# Layout
# --------------------------------------------------------------------------


def test_form_fits_a_narrow_phone_without_sideways_scrolling(page):
    page.set_viewport_size({"width": 320, "height": 800})
    overflow = page.evaluate(
        "() => document.documentElement.scrollWidth"
        " - document.documentElement.clientWidth"
    )
    assert overflow == 0


def test_touch_targets_are_large_enough(page):
    for selector in ("#submit", "#copies", "#copies-up", "#copies-down"):
        box = page.locator(selector).bounding_box()
        assert box["height"] >= 44, f"{selector} is only {box['height']}px tall"


def test_text_inputs_avoid_ios_zoom_on_focus(page):
    for selector in ("#pages", "#copies"):
        size = page.eval_on_element = page.locator(selector).evaluate(
            "element => parseFloat(getComputedStyle(element).fontSize)"
        )
        assert size >= 16, f"{selector} font-size is {size}px"


def test_dark_mode_follows_the_system_setting(page):
    page.emulate_media(color_scheme="dark")
    background = page.evaluate(
        "() => getComputedStyle(document.body).backgroundColor"
    )
    assert background == "rgb(11, 11, 12)"


# --------------------------------------------------------------------------
# Mode switching
# --------------------------------------------------------------------------


def test_pdf_is_the_default_mode(page):
    expect(page.locator("#mode-pdf")).to_be_checked()
    expect(page.locator("#pages-field")).to_be_visible()


def test_switching_to_image_hides_the_page_field(page):
    page.click("label:has(#mode-image)")
    expect(page.locator("#pages-field")).to_be_hidden()
    expect(page.locator("#pdf-preview")).to_be_hidden()


def test_switching_modes_clears_the_chosen_file(page, sample_files):
    choose(page, "#pdf", sample_files["pdf1"])
    expect(page.locator("#preview")).to_be_visible()

    page.click("label:has(#mode-image)")
    expect(page.locator("#preview")).to_be_hidden()
    assert page.eval_on_selector("#pdf", "e => e.value") == ""


# --------------------------------------------------------------------------
# File selection
# --------------------------------------------------------------------------


def test_choosing_a_pdf_shows_its_name_and_size(page, sample_files):
    choose(page, "#pdf", sample_files["pdf1"])
    expect(page.locator("#preview")).to_be_visible()
    expect(page.locator("#filename")).to_have_text("one-page.pdf")
    expect(page.locator("#filesize")).not_to_have_text("")


def test_choosing_an_image_shows_a_thumbnail(page, sample_files):
    page.click("label:has(#mode-image)")
    choose(page, "#image", sample_files["png"])
    expect(page.locator("#thumb img")).to_be_visible()


def test_a_pdf_dropped_in_image_mode_switches_back_to_pdf(page, sample_files):
    """The mode follows the file, so the wrong tab is never a dead end."""
    page.click("label:has(#mode-image)")
    drop(page, sample_files["pdf1"])
    expect(page.locator("#mode-pdf")).to_be_checked()
    expect(page.locator("#pages-field")).to_be_visible()
    expect(page.locator("#filename")).to_have_text("one-page.pdf")


def test_an_image_dropped_in_pdf_mode_switches_to_image(page, sample_files):
    drop(page, sample_files["png"])
    expect(page.locator("#mode-image")).to_be_checked()
    expect(page.locator("#pages-field")).to_be_hidden()
    expect(page.locator("#thumb img")).to_be_visible()


def test_unsupported_files_are_refused_before_upload(page, sample_files):
    choose(page, "#pdf", sample_files["txt"])
    expect(page.locator("#live-message")).to_be_visible()
    expect(page.locator("#live-message")).to_contain_text("Unsupported")
    expect(page.locator("#preview")).to_be_hidden()


def test_uppercase_extensions_are_accepted(page, sample_files):
    select_pdf(page, sample_files["pdfUpper"], pages=3)
    expect(page.locator("#filename")).to_have_text("Contract.PDF")


def test_removing_a_file_resets_the_form(page, sample_files):
    select_pdf(page, sample_files["pdf7"])
    page.click("#remove")

    expect(page.locator("#preview")).to_be_hidden()
    expect(page.locator("#pdf-preview")).to_be_hidden()
    assert page.locator(".page-tile").count() == 0


def test_dropping_a_file_selects_it(page, sample_files):
    drop(page, sample_files["pdf7"])
    expect(page.locator("#preview")).to_be_visible()
    expect(page.locator("#filename")).to_have_text("seven-pages.pdf")
    page.wait_for_selector(".page-tile")
    assert page.locator(".page-tile").count() == 7


def test_dropping_an_unsupported_file_is_refused(page, sample_files):
    drop(page, sample_files["txt"])
    expect(page.locator("#live-message")).to_contain_text("Unsupported")
    expect(page.locator("#preview")).to_be_hidden()


# --------------------------------------------------------------------------
# PDF page preview
# --------------------------------------------------------------------------


def test_preview_renders_one_tile_per_page(page, sample_files):
    select_pdf(page, sample_files["pdf7"])
    assert page.locator(".page-tile").count() == 7
    expect(page.locator("#preview-summary")).to_have_text("Printing all 7 pages")


def test_every_visible_tile_gets_rendered(page, sample_files):
    select_pdf(page, sample_files["pdf7"])
    page.wait_for_function(
        "() => document.querySelectorAll('.page-tile canvas').length > 0"
    )
    assert page.locator(".page-tile canvas").count() > 0


def test_a_single_page_pdf_reads_naturally(page, sample_files):
    select_pdf(page, sample_files["pdf1"], pages=1)
    expect(page.locator("#preview-summary")).to_have_text("Printing 1 page")


def test_tapping_a_page_excludes_it(page, sample_files):
    select_pdf(page, sample_files["pdf7"])
    page.click('.page-tile[data-page="3"]')

    expect(page.locator('.page-tile[data-page="3"]')).to_have_class(
        re.compile(r"\boff\b")
    )
    assert page.input_value("#pages") == "1-2,4-7"
    expect(page.locator("#preview-summary")).to_have_text("Printing 6 of 7 pages")


def test_tapping_again_restores_the_page(page, sample_files):
    select_pdf(page, sample_files["pdf7"])
    page.click('.page-tile[data-page="3"]')
    page.click('.page-tile[data-page="3"]')

    assert page.input_value("#pages") == ""
    expect(page.locator("#preview-summary")).to_have_text("Printing all 7 pages")


def test_selection_is_written_as_the_shortest_expression(page, sample_files):
    select_pdf(page, sample_files["pdf7"])
    for number in (2, 3, 6):
        page.click(f'.page-tile[data-page="{number}"]')

    assert page.input_value("#pages") == "1,4-5,7"
    expect(page.locator("#preview-summary")).to_have_text("Printing 4 of 7 pages")


def test_typing_an_expression_updates_the_tiles(page, sample_files):
    select_pdf(page, sample_files["pdf7"])
    page.fill("#pages", "1,3,6-7")
    page.dispatch_event("#pages", "input")

    page.wait_for_function(
        "() => document.querySelectorAll('.page-tile.off').length === 3"
    )
    off = page.eval_on_selector_all(
        ".page-tile.off", "els => els.map(e => e.dataset.page).join(',')"
    )
    assert off == "2,4,5"
    expect(page.locator("#preview-summary")).to_have_text("Printing 4 of 7 pages")


def test_out_of_range_pages_are_ignored_by_the_preview(page, sample_files):
    select_pdf(page, sample_files["pdf7"])
    page.fill("#pages", "1,99")
    page.dispatch_event("#pages", "input")

    expect(page.locator("#preview-summary")).to_have_text("Printing 1 of 7 pages")


def test_select_none_then_select_all(page, sample_files):
    select_pdf(page, sample_files["pdf7"])

    page.click("#toggle-all")
    expect(page.locator("#preview-summary")).to_have_text("No pages selected")
    assert page.locator(".page-tile.off").count() == 7

    page.click("#toggle-all")
    expect(page.locator("#preview-summary")).to_have_text("Printing all 7 pages")
    assert page.locator(".page-tile.off").count() == 0


def test_deselecting_everything_blocks_printing(page, sample_files):
    """An empty expression means "all pages" to CUPS, so this must not submit."""
    select_pdf(page, sample_files["pdf7"])
    page.click("#toggle-all")

    expect(page.locator("#pages-error")).to_be_visible()
    page.click("#submit")
    expect(page.locator("#live-message")).to_contain_text("No pages are selected")


def test_preview_is_cleared_after_a_successful_print(page, sample_files):
    select_pdf(page, sample_files["pdf7"])
    page.click("#submit")

    expect(page.locator("#live-message")).to_contain_text("request id")
    expect(page.locator("#pdf-preview")).to_be_hidden()


# --------------------------------------------------------------------------
# Page expression validation
# --------------------------------------------------------------------------


@pytest.mark.parametrize("expression", ["abc", "1-", "5-2", "0", "1 3"])
def test_bad_page_expressions_are_flagged_inline(page, sample_files, expression):
    select_pdf(page, sample_files["pdf7"])
    page.fill("#pages", expression)
    page.dispatch_event("#pages", "input")
    expect(page.locator("#pages-error")).to_be_visible()


def test_a_bad_expression_prevents_submission(page, sample_files):
    select_pdf(page, sample_files["pdf7"])
    page.fill("#pages", "abc")
    page.dispatch_event("#pages", "input")
    page.click("#submit")
    expect(page.locator("#live-message")).to_contain_text("Fix the page selection")


@pytest.mark.parametrize("expression", ["1", "1-3", "1,3,5-7", " 1 , 3 "])
def test_good_page_expressions_pass(page, sample_files, expression):
    select_pdf(page, sample_files["pdf7"])
    page.fill("#pages", expression)
    page.dispatch_event("#pages", "input")
    expect(page.locator("#pages-error")).to_be_hidden()


# --------------------------------------------------------------------------
# Copies and duplex
# --------------------------------------------------------------------------


def test_the_stepper_changes_the_copy_count(page):
    page.click("#copies-up")
    page.click("#copies-up")
    assert page.input_value("#copies") == "3"

    page.click("#copies-down")
    assert page.input_value("#copies") == "2"


def test_the_stepper_respects_the_limits(page):
    page.click("#copies-down")
    assert page.input_value("#copies") == "1"

    page.fill("#copies", "20")
    page.click("#copies-up")
    assert page.input_value("#copies") == "20"


def test_duplex_can_be_toggled(page):
    expect(page.locator("#duplex")).not_to_be_checked()
    page.click("label:has(#duplex)")
    expect(page.locator("#duplex")).to_be_checked()


# --------------------------------------------------------------------------
# Printer options
# --------------------------------------------------------------------------


def test_the_printers_options_are_shown(page):
    expect(page.locator("#options")).to_be_visible()
    expect(page.locator(f"label:has({GRAY})")).to_contain_text("Black & white")


def test_the_printers_defaults_start_selected(page):
    expect(page.locator(COLOUR)).to_be_checked()
    expect(page.locator(GRAY)).not_to_be_checked()


def test_choosing_black_and_white_sticks(page):
    page.click(f"label:has({GRAY})")
    expect(page.locator(GRAY)).to_be_checked()
    expect(page.locator(COLOUR)).not_to_be_checked()


def test_collapsed_options_say_printer_defaults(page):
    page.click("#options > summary")
    expect(page.locator("#options-summary")).to_have_text("Printer defaults")


def test_collapsed_options_summarise_the_changes(page):
    page.click(f"label:has({GRAY})")
    page.select_option('select[name="opt_PageSize"]', "A5")
    page.click("#options > summary")

    summary = page.locator("#options-summary")
    expect(summary).to_contain_text("Black & white")
    expect(summary).to_contain_text("A5")


def test_the_summary_ignores_untouched_options(page):
    # Only what the user actually changed is worth reporting.
    page.click(f"label:has({GRAY})")
    page.click("#options > summary")
    expect(page.locator("#options-summary")).to_have_text("Black & white")


def test_a_chosen_option_is_sent_when_printing(page, sample_files):
    page.click(f"label:has({GRAY})")
    select_pdf(page, sample_files["pdf1"], pages=1)
    page.click("#submit")
    expect(page.locator("#live-message")).to_contain_text("request id")


def test_options_survive_a_mode_switch(page):
    page.click(f"label:has({GRAY})")
    page.click("label:has(#mode-image)")
    expect(page.locator(GRAY)).to_be_checked()


# --------------------------------------------------------------------------
# Submitting
# --------------------------------------------------------------------------


def test_submitting_without_a_file_is_refused(page):
    page.click("#submit")
    expect(page.locator("#live-message")).to_contain_text("choose a PDF")


def test_a_successful_print_reports_the_job_and_stays_put(page, sample_files):
    select_pdf(page, sample_files["pdf7"])
    page.click("#submit")

    expect(page.locator("#live-message")).to_contain_text("request id")
    expect(page.locator("#live-message")).to_have_class(re.compile(r"\bok\b"))
    # The enhanced path posts in the background, so no navigation happens.
    assert "?" not in page.url


def test_printing_an_image_succeeds(page, sample_files):
    page.click("label:has(#mode-image)")
    choose(page, "#image", sample_files["png"])
    page.click("#submit")
    expect(page.locator("#live-message")).to_contain_text("request id")


def test_the_form_is_reusable_after_printing(page, sample_files):
    select_pdf(page, sample_files["pdf7"])
    page.click("#submit")
    expect(page.locator("#live-message")).to_contain_text("request id")

    select_pdf(page, sample_files["pdf1"], pages=1)
    page.click("#submit")
    expect(page.locator("#live-message")).to_contain_text("request id")


# --------------------------------------------------------------------------
# Fallback without JavaScript
# --------------------------------------------------------------------------


def test_the_file_inputs_are_visible_without_javascript(no_js_page):
    box = no_js_page.locator("#pdf").bounding_box()
    assert box["width"] > 100
    assert no_js_page.locator("#drop-idle").is_hidden()


def test_a_pdf_can_be_printed_without_javascript(no_js_page, sample_files):
    no_js_page.set_input_files("#pdf", sample_files["pdf1"])
    with no_js_page.expect_navigation():
        no_js_page.click("#submit")

    assert "message=" in no_js_page.url
    expect(no_js_page.locator(".message:not(#live-message)")).to_contain_text("request id")


def test_an_image_can_be_printed_without_javascript(no_js_page, sample_files):
    no_js_page.click("label:has(#mode-image)")
    no_js_page.set_input_files("#image", sample_files["png"])
    with no_js_page.expect_navigation():
        no_js_page.click("#submit")

    expect(no_js_page.locator(".message:not(#live-message)")).to_contain_text("request id")


def test_server_side_validation_still_applies_without_javascript(
    no_js_page, sample_files
):
    no_js_page.set_input_files("#pdf", sample_files["pdf1"])
    no_js_page.fill("#pages", "abc")
    with no_js_page.expect_navigation():
        no_js_page.click("#submit")

    expect(no_js_page.locator(".message:not(#live-message)")).to_contain_text("Invalid page selection")
