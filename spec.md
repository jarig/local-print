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

The concrete host, user, printer and network are **not** recorded here:
they live in `localprint.conf` (see §5.2), which is git-ignored. The
project only assumes the shape of the target:

| Property | Value |
| --- | --- |
| Host | A Debian-family Linux box reachable over SSH (`$LOCALPRINT_HOST`) |
| User | An unprivileged account with passwordless `sudo` (`$LOCALPRINT_USER`) |
| Deploy path | `~/$LOCALPRINT_REMOTE_PATH/` |
| Virtualenv | `venv/` inside the deploy path |
| Launcher | `start.sh` inside the deploy path |
| Printer | A CUPS queue named by `$LOCALPRINT_PRINTER` |
| Bind address | The LAN IPv4 starting with `$LOCALPRINT_LAN_PREFIX`, on `$LOCALPRINT_PORT` |

All application files are uploaded back into the deploy path as part of
deployment. The existing `venv/` and any previous `web.py.bck` on the server
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

### 3.3 Printer-discovered options

Beyond copies and duplex, the printer decides what can be adjusted. The
server runs `lpoptions -p <printer> -l` and offers the choices the printer
reports for a fixed set of keywords, in this order:

| Keyword | Label |
| --- | --- |
| `ColorModel` | Colour |
| `cupsPrintQuality` | Quality |
| `PageSize` | Paper size |
| `MediaType` | Paper type |
| `InputSlot` | Paper source |
| `Resolution` | Resolution |

Rules:

- **Nothing is configured.** A mono printer simply offers no colour choice.
- Keywords outside the list are ignored — a PPD also exposes internal knobs
  (`PageRegion`, `OutputBin`) that mean nothing to a person.
- `Duplex` is excluded on purpose: §3.2 already provides a dedicated toggle,
  and two controls for one setting would disagree.
- Groups with fewer than two choices are dropped; there is nothing to pick.
- The choice CUPS marks with `*` is preselected, with one exception: when
  the printer offers an automatic paper type (`Auto`, `AutoDetect`,
  `Automatic`) that is preselected instead. PPDs tend to default to one
  specific stock, and the driver guesses better than a fixed pick.
- PPDs carry no human-readable text for individual choices, so labels are
  derived: a small table renames the ones that matter (`Gray` →
  *Black & white*), and the rest are tidied (`Com.canon.mtinkjeta` →
  *Inkjeta*).
- Discovery is cached for five minutes and never raises. If CUPS is
  unreachable no options are offered and printing still works.
- Submitted values are validated against the discovered choices, so the
  browser can only request something the printer itself advertised.

### 3.4 Job submission

The server writes the upload to a temporary file, invokes the CUPS `lp`
command line tool, and deletes the temporary file immediately afterwards —
nothing is retained on disk after the job is queued.

The command is assembled as:

```
lp -d <printer> -n <copies> [-o <Keyword>=<Choice> ...] [-o sides=two-sided-long-edge] [-P <pages>] -- <tempfile>
```

Discovered options are emitted before `sides`, so the dedicated
double-sided toggle wins if a printer ever exposes duplex twice — with
`lp`, the last `-o` for a keyword takes effect.

The `--` separator prevents a crafted filename from being interpreted as an
option. Arguments are passed as a list (never through a shell), so filenames
cannot inject commands.

### 3.5 Result feedback

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
- **Print options panel** — the discovered options (§3.3) sit in a
  `<details>` disclosure that starts **collapsed**, since the common case is
  "choose a file, press Print". Groups of up to three choices render as
  tappable chips; larger ones (paper sizes) become a `<select>`, which stays
  usable on a phone. The summary line lists whatever differs from the
  defaults, or reads "Printer defaults". The fields are hidden rather than
  disabled while collapsed, so they are still submitted.

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

Every value here is configurable per site (see §5.2); the column below
shows the shipped example values.

| Constraint | Example | Rationale |
| --- | --- | --- |
| Max upload size | 50 MB | Returns HTTP 413 with a friendly message |
| Allowed PDF extension | `.pdf` | |
| Allowed image extensions | `.jpg`, `.jpeg`, `.png` | |
| Copies range | 1–20 | Guards against accidental paper waste |
| `lp` timeout | 30 s | Prevents a hung CUPS call blocking a worker |
| Bind interface | The configured LAN prefix only | Refuses to start otherwise, so the service is never accidentally exposed beyond the LAN |

### 5.1 Security posture

This is a LAN-only service with **no authentication** — the trust boundary
is the home network itself. Consequently:

- The server refuses to start if no interface matches the configured LAN
  prefix, rather than falling back to `0.0.0.0`.
- Uploads never keep their client-supplied filename on disk; a temporary
  name with a validated extension is used instead.
- The page-range expression is validated against a strict regular
  expression before reaching the command line.
- Uploaded files are deleted in a `finally` block, so they are removed even
  when printing fails.

### 5.2 Configuration

Everything site-specific lives in a single `localprint.conf` beside
`app.py`, in `KEY=value` form so the app, `install.sh` and `deploy.ps1` can
all read it. `localprint.conf.example` is committed as the template;
`localprint.conf` itself is git-ignored.

**There are no defaults.** A missing or empty setting raises `ConfigError`
at import time with a message naming the key, so the app fails immediately
and visibly instead of printing to the wrong queue or binding the wrong
network. Environment variables of the same name take precedence over the
file.

Required by the app: `LOCALPRINT_PRINTER`, `LOCALPRINT_PORT`,
`LOCALPRINT_LAN_PREFIX`, `LOCALPRINT_MAX_UPLOAD_MB`,
`LOCALPRINT_LP_TIMEOUT_SECONDS`, `LOCALPRINT_MIN_COPIES`,
`LOCALPRINT_MAX_COPIES`. Read by `deploy.ps1` only: `LOCALPRINT_HOST`,
`LOCALPRINT_USER`, `LOCALPRINT_REMOTE_PATH`. Optional switches:
`LOCALPRINT_FAKE_PRINTER`, `LOCALPRINT_DEBUG`, `LOCALPRINT_CONFIG`.

Two values are validated beyond being present, because getting them wrong
fails silently rather than loudly: `LOCALPRINT_LAN_PREFIX` must end with a
dot (otherwise a prefix such as `10.1.2` would also match `10.1.22.x`), and
`MAX_COPIES` must not
be below `MIN_COPIES`.

The upload limit is published to the browser through `window.LOCALPRINT`
rather than duplicated in `app.js`, so the client-side size check cannot
drift from what the server enforces.

Accepted file extensions stay in `config.py`: they describe what the
printing pipeline can handle, not a per-site preference.

---

## 6. Project structure

```
local-print/
├── app.py                 # Flask app: routes, validation, CUPS invocation
├── printing.py            # Printer abstraction: discovers options, runs lp
├── config.py              # Strict loader for localprint.conf; no defaults
├── localprint.conf        # Site settings (git-ignored)
├── localprint.conf.example# Committed template for the above
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
└── deploy.ps1             # Uploads the above to the configured host
```

Development-only files that are **not** deployed:

```
├── requirements-dev.txt   # pytest + playwright
├── pytest.ini             # Test discovery and the e2e marker
└── tests/
    ├── conftest.py        # Sample documents, Flask client, live server
    ├── test_config.py     # Config parsing, validation and the template
    ├── test_options.py    # lpoptions parsing, discovery and caching
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

Local development also relaxes the LAN bind requirement so the server can
listen on `127.0.0.1`.

### 6.2 Tests

The suite guards the behaviour described in this document against
regressions. It never contacts a printer: the backend tests run with
`LOCALPRINT_FAKE_PRINTER=1`, so `lp` is never executed. They also point
`LOCALPRINT_CONFIG` at the committed `localprint.conf.example`, so the suite
is reproducible on any machine and never depends on the developer's own
`localprint.conf`.

```
pip install -r requirements-dev.txt
python -m playwright install chromium

python -m pytest                  # everything (~20s)
python -m pytest -m "not e2e"     # backend only, no browser needed (<1s)
```

**Backend (`test_printing.py`, `test_options.py`, `test_routes.py`)** —
page-range parsing and its rejection cases, the exact `lp` argument list
including the `--` separator that stops a crafted filename becoming an
option, every error branch of the CUPS call, the parsing of `lpoptions`
output together with caching and its tolerance of an unreachable printer,
and the HTTP surface: rendering, uploads, option pass-through, copy bounds,
extension checks, oversized bodies, the JSON-versus-redirect split, and the
guarantee that the temporary upload exists while printing and is deleted
afterwards.

A dedicated group of tests treats the discovered choices as an allow-list:
a value the stubbed printer never advertised must be rejected with 400, and
an unknown `opt_*` field must be ignored rather than forwarded.

**Browser (`test_ui.py`)** — the behaviour that only exists in JavaScript,
run against a real server in a subprocess: mobile layout at 320 px, dark
mode, drag and drop, the mode following the dropped file's type, the PDF
page preview and its two-way binding with the Pages field, the refusal to
submit an empty page selection, the print options panel and its collapsed
summary, and the no-JavaScript fallback (exercised by blocking `app.js` and
letting the plain form POST). Every browser test also asserts the page
produced no console errors.

Because an empty page expression means *all pages* to CUPS, the tests
covering an empty selection are load-bearing rather than cosmetic: without
them a "select none" regression would silently print the whole document.

---

## 7. Deployment

`deploy.ps1` performs the upload:

1. Reads the target host, user and remote path from `localprint.conf`, then
   verifies the SSH connection.
2. Creates `templates/` and `static/` under the deploy path if missing.
3. Copies `app.py`, `printing.py`, `config.py`, `localprint.conf`,
   `localprint.conf.example`, `requirements.txt`, `start.sh`, `install.sh`,
   `templates/*`, `static/*` via `scp`. Vendored third-party bundles under
   `static/vendor/` are copied byte for byte, while the project's own text
   files are normalised to LF.
4. Normalises line endings to LF and makes `start.sh`, `install.sh` and
   `app.py` executable.
5. Optionally restarts the service. If the systemd unit is installed,
   `-Restart` restarts *that*; otherwise it falls back to starting a detached
   process directly, so the two never fight over the port.

`localprint.conf` is deliberately part of the upload: with no defaults in
the code, the server cannot start without it.

The legacy `web.py` on the server is preserved until the new app is
verified, then may be removed manually.

### 7.1 Running as a service

`install.sh` runs **on the server** and registers the app with systemd so it
comes back after a reboot:

```
ssh "$LOCALPRINT_USER@$LOCALPRINT_HOST" 'cd ~/local-print && ./install.sh'
```

It re-runs itself under `sudo`, checks that `localprint.conf` is present and
complete (failing early with the offending key rather than leaving systemd
to restart a doomed service forever), creates the virtualenv if missing,
installs `requirements.txt`, writes
`/etc/systemd/system/localprint.service`, enables it for
`multi-user.target` and starts it. It is safe to re-run: it rewrites the
unit and restarts the service. `./install.sh --uninstall` stops, disables
and removes the unit, leaving the application files alone.

The unit runs as the owner of the install directory (not root), with
`NoNewPrivileges`, a private `/tmp` for the uploaded documents, and
`/home` mounted read-only apart from the install directory itself.

Two ordering details matter:

- The app refuses to bind anything outside the configured LAN prefix, so
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

1. Opening `http://<lan-ip>:<port>` on a phone shows the print form with no
   horizontal scrolling and no zoom-on-focus.
2. Selecting a PDF and pressing Print queues a job on the configured printer
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
10. Running `deploy.ps1` places every file in the configured deploy path on
    the configured host, and the service starts successfully from
    `start.sh`.
11. Selecting a multi-page PDF shows one thumbnail per page and the summary
    reports the correct total.
12. Tapping thumbnails rewrites the Pages field to the shortest equivalent
    expression, and typing an expression re-highlights the thumbnails.
13. Deselecting every page blocks submission with a clear message instead of
    printing the entire document.
14. The Print options panel starts collapsed and lists exactly what
    `lpoptions -p <printer> -l` reports for the supported keywords, with the
    printer's own defaults preselected, and no configuration file mentions
    any of them.
15. Where the printer offers an automatic paper type, that is what the form
    starts on rather than the PPD's chosen stock.
16. Choosing "Black & white" sends `-o ColorModel=Gray` to `lp`; a value the
    printer never advertised is rejected with a 400.
17. Stopping CUPS leaves the form working, with no options offered.
