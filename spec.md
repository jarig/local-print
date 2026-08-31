# LocalPrint — Specification

A lightweight, mobile-friendly web UI for printing PDFs and images to a
CUPS printer on the home LAN.

---

## 1. Purpose

Any device on the home network (phone, tablet, laptop) should be able to
open a browser, pick a PDF or image, and print it — with no drivers, no
app install, and no cloud service.

The application replaces the current single-file `web.py` prototype with a
maintainable structure that separates the print backend from the web UI, so
the interface can be developed and previewed independently.

---

## 2. Deployment target

| Property | Value |
| --- | --- |
| Host | `my-nas` (Debian 12, Python 3.11.2) |
| User | `me` |
| Deploy path | `~/local-print/` |
| Virtualenv | `~/local-print/venv` (Flask 3.1.3 already installed) |
| Launcher | `~/local-print/start.sh` |
| Printer | CUPS queue named `my-printer` |
| Bind address | LAN IPv4 in `10.1.2.0/24`, port `8081` |

All application files are uploaded back into `~/local-print/` on `my-nas`
as part of deployment. The existing `venv/` and `web.py.bck` on the server
are left untouched.

---

## 3. Core concepts

### 3.1 Print modes

The UI offers exactly two mutually exclusive modes:

**PDF mode**
- Accepts a single `.pdf` file.
- Supports a page selection expression: `1`, `1-3`, `1,3,5-7`.
- Empty page selection means "all pages".
- A thumbnail strip above the page field previews every page and shows
  exactly which pages will be printed (see 4.4).

**Image mode**
- Accepts a single JPEG or PNG file.
- No page selection (an image is one page).

Only one file may be submitted per job. Submitting both is an error.

### 3.2 Shared print options

- **Copies** — integer, 1 to 20.
- **Double-sided** — toggles `sides=two-sided-long-edge`.

### 3.3 Job submission

The server writes the upload to a temporary file, invokes the CUPS `lp`
command line tool, and deletes the temporary file immediately afterwards —
nothing is retained on disk after the job is queued.

The command is assembled as:

```
lp -d <printer> -n <copies> [-o sides=two-sided-long-edge] [-P <pages>] -- <tempfile>
```

The `--` separator prevents a crafted filename from being interpreted as an
option. Arguments are passed as a list (never through a shell), so filenames
cannot inject commands.

### 3.4 Result feedback

On success the browser is redirected back to the form with a status message
(POST/Redirect/GET). This is deliberate: refreshing the page must never
re-submit a print job. Failures re-render the form with an error message and
an appropriate HTTP status code.

---

## 4. Web UI requirements

### 4.1 Mobile-first

- Single-column card layout, `max-width: 520px`, centred.
- Viewport meta tag set; no horizontal scrolling at 320px width.
- Touch targets at least 48px tall.
- Inputs use `font-size: 16px` so iOS Safari does not zoom on focus.
- Correct `inputmode` / `accept` attributes so mobile keyboards and file
  pickers behave sensibly (camera roll for images, Files for PDFs).

### 4.2 Modernized interaction

Beyond the existing prototype, the UI adds:

- **Drag & drop** — drop a file anywhere on the drop zone; the mode
  (PDF vs image) is inferred automatically from the file type.
- **File preview** — thumbnail for images, filename plus size and a
  document icon for PDFs, with a clear "remove" control.
- **Upload progress** — the form submits via `fetch` with an
  `XMLHttpRequest`-style progress indicator, so large files on slow Wi-Fi
  give visible feedback instead of a frozen button.
- **Dark mode** — follows `prefers-color-scheme`, driven by CSS custom
  properties.
- **Inline validation** — wrong file type, oversized file, or a malformed
  page expression is reported before the upload starts.

### 4.3 Progressive enhancement

The form must remain fully functional as a plain multipart `POST` when
JavaScript is unavailable or fails to load. JavaScript only enhances the
experience; it is never required to print.

### 4.4 PDF page preview

When a PDF is selected, a horizontally scrollable strip of page thumbnails
appears directly above the page-selection field, together with a one-line
summary such as *"Printing 4 of 7 pages"*.

- Rendering happens **entirely in the browser** using a vendored copy of
  pdf.js (`static/vendor/`). The file is never uploaded just to be
  previewed, and the NAS needs no PDF tooling.
- pdf.js is vendored rather than loaded from a CDN, because the service must
  keep working on a LAN with no internet access.
- The ~320 KB library is loaded lazily, only once a PDF is actually chosen,
  so the initial page stays light on mobile.
- Thumbnails render lazily through an `IntersectionObserver` as they scroll
  into view, so a long document does not stall a phone.
- Tapping a thumbnail toggles that page in or out of the selection. Excluded
  pages are dimmed with a grey badge; included pages keep an accent border.
- Selection and the text field are **two-way bound**: tapping pages rewrites
  the field using the shortest equivalent expression (`1,4-5,7`), and typing
  an expression re-highlights the thumbnails.
- Because an empty expression means "all pages" to CUPS, deselecting every
  page is reported as an error and blocks submission rather than silently
  printing the whole document.
- The preview is strictly an enhancement: if pdf.js fails to load, the strip
  is hidden, a notice is shown, and printing by typing a range still works.

---

## 5. Constraints and limits

| Constraint | Value | Rationale |
| --- | --- | --- |
| Max upload size | 50 MB | Returns HTTP 413 with a friendly message |
| Allowed PDF extension | `.pdf` | |
| Allowed image extensions | `.jpg`, `.jpeg`, `.png` | |
| Copies range | 1–20 | Guards against accidental paper waste |
| `lp` timeout | 30 s | Prevents a hung CUPS call blocking a worker |
| Bind interface | `10.1.2.x` only | Refuses to start otherwise, so the service is never accidentally exposed beyond the LAN |

### 5.1 Security posture

This is a LAN-only service with **no authentication** — the trust boundary
is the home network itself. Consequently:

- The server refuses to start if no `10.1.2.x` interface exists, rather
  than falling back to `0.0.0.0`.
- Uploads never keep their client-supplied filename on disk; a temporary
  name with a validated extension is used instead.
- The page-range expression is validated against a strict regular
  expression before reaching the command line.
- Uploaded files are deleted in a `finally` block, so they are removed even
  when printing fails.

---

## 6. Project structure

```
local-print/
├── app.py                 # Flask app: routes, validation, CUPS invocation
├── printing.py            # Printer abstraction: builds and runs the lp command
├── config.py              # Printer name, port, limits, extensions
├── templates/
│   └── index.html         # Jinja2 template for the print form
├── static/
│   ├── style.css          # Mobile-first styling, light + dark themes
│   ├── app.js             # Drag & drop, preview, progress, validation
│   └── vendor/            # Vendored pdf.js (offline page thumbnails)
│       ├── pdf.min.js
│       └── pdf.worker.min.js
├── requirements.txt       # Flask pin
├── start.sh               # Launcher (venv/bin/python3 app.py)
├── install.sh             # Installs the systemd service (runs on the NAS)
└── deploy.ps1             # Uploads the above to my-nas:~/local-print/
```

Development-only files that are **not** deployed:

```
├── requirements-dev.txt   # pytest + playwright
├── pytest.ini             # Test discovery and the e2e marker
└── tests/
    ├── conftest.py        # Sample documents, Flask client, live server
    ├── test_printing.py   # Page ranges and lp command construction
    ├── test_routes.py     # HTTP behaviour of the upload endpoint
    └── test_ui.py         # Browser tests driven through Playwright
```

Splitting the template and assets out of the Python source is what allows
the UI to be previewed and iterated on locally in a browser canvas, without
a printer or the NAS being involved.

### 6.1 Local development

A `LOCALPRINT_FAKE_PRINTER=1` environment variable swaps the real `lp`
invocation for a stub that logs the command it would have run and reports
success. This makes the whole UI — including the success and error paths —
developable on a Windows workstation with no CUPS installed.

Local development also relaxes the `10.1.2.x` bind requirement so the
server can listen on `127.0.0.1`.

### 6.2 Tests

The suite guards the behaviour described in this document against
regressions. It never contacts a printer: the backend tests run with
`LOCALPRINT_FAKE_PRINTER=1`, so `lp` is never executed.

```
pip install -r requirements-dev.txt
python -m playwright install chromium

python -m pytest                  # everything (~17s)
python -m pytest -m "not e2e"     # backend only, no browser needed (<1s)
```

**Backend (`test_printing.py`, `test_routes.py`)** — page-range parsing and
its rejection cases, the exact `lp` argument list including the `--`
separator that stops a crafted filename becoming an option, every error
branch of the CUPS call, and the HTTP surface: rendering, uploads, option
pass-through, copy bounds, extension checks, oversized bodies, the
JSON-versus-redirect split, and the guarantee that the temporary upload
exists while printing and is deleted afterwards.

**Browser (`test_ui.py`)** — the behaviour that only exists in JavaScript,
run against a real server in a subprocess: mobile layout at 320 px, dark
mode, drag and drop, the mode following the dropped file's type, the PDF
page preview and its two-way binding with the Pages field, the refusal to
submit an empty page selection, and the no-JavaScript fallback (exercised
by blocking `app.js` and letting the plain form POST). Every browser test
also asserts the page produced no console errors.

Because an empty page expression means *all pages* to CUPS, the tests
covering an empty selection are load-bearing rather than cosmetic: without
them a "select none" regression would silently print the whole document.

---

## 7. Deployment

`deploy.ps1` performs the upload:

1. Verifies the SSH connection to `me@my-nas`.
2. Creates `~/local-print/templates` and `~/local-print/static` if missing.
3. Copies `app.py`, `printing.py`, `config.py`, `requirements.txt`,
   `start.sh`, `install.sh`, `templates/*`, `static/*` via `scp`. Vendored
   third-party bundles under `static/vendor/` are copied byte for byte, while
   the project's own text files are normalised to LF.
4. Normalises line endings to LF and makes `start.sh`, `install.sh` and
   `app.py` executable.
5. Optionally restarts the service. If the systemd unit is installed,
   `-Restart` restarts *that*; otherwise it falls back to starting a detached
   process directly, so the two never fight over the port.

The legacy `web.py` on the server is preserved until the new app is
verified, then may be removed manually.

### 7.1 Running as a service

`install.sh` runs **on the NAS** and registers the app with systemd so it
comes back after a reboot:

```
ssh me@my-nas 'cd ~/local-print && ./install.sh'
```

It re-runs itself under `sudo`, creates the virtualenv if missing, installs
`requirements.txt`, writes `/etc/systemd/system/localprint.service`, enables
it for `multi-user.target` and starts it. It is safe to re-run: it rewrites
the unit and restarts the service. `./install.sh --uninstall` stops,
disables and removes the unit, leaving the application files alone.

The unit runs as the owner of the install directory (not root), with
`NoNewPrivileges`, a private `/tmp` for the uploaded documents, and
`/home` mounted read-only apart from the install directory itself.

Two ordering details matter:

- The app refuses to bind anything other than the `10.1.2.x` address, so
  the unit waits for `network-online.target` and additionally uses
  `Restart=always` with a 3 second delay. If the address is not configured
  yet at boot, the service simply retries until it is, rather than failing.
- `cups.service` is ordered before it but not required, because CUPS is
  needed to *print*, not to serve the page.

Useful commands:

```
systemctl status localprint
journalctl -u localprint -f
sudo systemctl restart localprint
```

---

## 8. Acceptance criteria

1. Opening `http://<lan-ip>:8081` on a phone shows the print form with no
   horizontal scrolling and no zoom-on-focus.
2. Selecting a PDF and pressing Print queues a job on the `my-printer` printer
   and shows the `lp` confirmation message.
3. Selecting a JPEG or PNG prints the image.
4. Dragging a file onto the page selects it and switches to the correct
   mode automatically.
5. A page expression such as `1,3,5-7` is honoured; `abc` is rejected with
   a clear message before upload.
6. Refreshing the page after a successful print does not print again.
7. A 60 MB file is rejected with a readable message, not a stack trace.
8. Disabling JavaScript still allows a print job to be submitted.
9. The temporary upload file no longer exists after the job is queued.
10. Running `deploy.ps1` places every file in `~/local-print/` on `my-nas`
    and the service starts successfully from `start.sh`.
11. Selecting a multi-page PDF shows one thumbnail per page and the summary
    reports the correct total.
12. Tapping thumbnails rewrites the Pages field to the shortest equivalent
    expression, and typing an expression re-highlights the thumbnails.
13. Deselecting every page blocks submission with a clear message instead of
    printing the entire document.
