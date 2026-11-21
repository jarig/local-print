# LocalPrint

A small, mobile-friendly web UI for printing PDFs and images to a CUPS
printer on your home network.

Open the page on any phone, tablet or laptop on the LAN, pick a file, choose
which pages you want, and print. No app to install, no cloud service, no
account.

```
┌──────────────────────────────┐
│  Print            ● printer  │
│  ┌────────┬────────┐         │
│  │  PDF   │ Image  │         │
│  └────────┴────────┘         │
│  ┌──────────────────────┐    │
│  │  Choose a file       │    │
│  │  or drop it here     │    │
│  └──────────────────────┘    │
│  ▣ ▣ ▢ ▣ ▣ ▢ ▣   ← tap to    │
│  Printing 5 of 7 pages          exclude pages │
│  Pages [ 1-2,4-5,7 ]         │
│  Copies [− 1 +]  Duplex ( ●) │
│  ┌──────────────────────┐    │
│  │        Print         │    │
│  └──────────────────────┘    │
└──────────────────────────────┘
```

## Features

- **PDF and image printing** — PDF, JPEG and PNG.
- **Visual page picker** — thumbnails of every page in the PDF; tap to
  include or exclude. The page range stays in sync with the thumbnails, and
  you can still type `1,3,5-7` by hand.
- **Copies and double-sided** printing.
- **Print options discovered from your printer** — colour vs. black & white,
  quality, paper size, paper type and paper source, read straight from CUPS.
  Nothing to configure. See [Print options](#print-options).
- **Queue status and a Clear queue button** — a stopped printer is shown
  instead of silently swallowing jobs. See [When nothing
  prints](#when-nothing-prints).
- **Mobile-first** — designed for a phone, works on a desktop. Follows the
  system light/dark theme.
- **Drag and drop** with upload progress.
- **Upscaling for low-resolution images** (optional) — an image too small
  to print sharply on A4 gets an *Upscale 480×640 → 2480×3312* button that
  redraws it through OpenAI. See [Upscaling](#upscaling).
- **Generate from text** (optional) — describe a picture, preview what
  OpenAI draws, then print it. See [Generating images](#generating-images).
- **Large image preview** — the chosen image is shown full width before
  printing.
- **Works offline** — no CDNs. `pdf.js` is vendored, so the LAN needs no
  internet access. (Only the optional OpenAI features reach out.)
- **Works without JavaScript** — the form degrades to a plain POST.

## Requirements

- A Linux host with **CUPS** configured and a working printer (`lp` must be
  able to reach it).
- **Python 3.9+**.
- Clients on the same LAN.

## Quick start

```bash
git clone https://github.com/jarig/local-print.git
cd local-print

cp localprint.conf.example localprint.conf
$EDITOR localprint.conf          # printer name, port, LAN prefix

python3 -m venv venv
venv/bin/pip install -r requirements.txt

./start.sh
```

The server prints the URL it bound to, e.g. `http://10.1.2.15:8081`.
Open that from your phone.

> There are no built-in defaults. If `localprint.conf` is missing or
> incomplete the app stops with a message naming the setting, rather than
> guessing a printer or a network. See [Configuration](#configuration).

> The app only binds an address starting with `LOCALPRINT_LAN_PREFIX` and
> refuses to start otherwise, so it can never accidentally listen on
> `0.0.0.0`.

### Developing without a printer

`LOCALPRINT_FAKE_PRINTER=1` stubs out the `lp` call and relaxes the LAN bind
check, so the whole UI — including success and error paths — can be worked
on from any machine with no CUPS installed:

```bash
LOCALPRINT_FAKE_PRINTER=1 LOCALPRINT_DEBUG=1 python3 app.py
# -> http://127.0.0.1:8081
```

## Installing as a service

`install.sh` registers the app with systemd so it starts on boot and
restarts if it dies. Run it **on the server**, from the install directory:

```bash
cd /path/to/local-print
cp localprint.conf.example localprint.conf   # if you have not already
./install.sh
```

It re-runs itself under `sudo` and will:

1. Check that `localprint.conf` exists and is complete, and stop if not.
2. Create the virtualenv (`venv/`) if it is missing.
3. Install `requirements.txt`.
4. Write `/etc/systemd/system/localprint.service`.
5. Enable it for `multi-user.target` and start it.
6. Wait for the app to answer on its LAN URL, and print that URL.

It is safe to re-run — it rewrites the unit and restarts the service. Any
copy you started by hand is stopped first, so the two never fight over the
port.

```bash
./install.sh --uninstall     # stop, disable and remove the unit
```

The application files are never touched by `--uninstall`.

### Managing the service

```bash
systemctl status localprint
journalctl -u localprint -f
sudo systemctl restart localprint
```

The unit runs as the owner of the install directory — **not** as root —
with `NoNewPrivileges`, a private `/tmp` for uploaded documents, and `/home`
mounted read-only apart from the install directory.

Because the app binds a specific LAN address, the unit waits for
`network-online.target` and uses `Restart=always`. If the address is not
configured yet at boot, the service retries until it is rather than failing
permanently.

## Deploying from a workstation

`deploy.ps1` (PowerShell) uploads the app over `scp` to the server named in
`localprint.conf`:

```powershell
.\deploy.ps1                 # upload only
.\deploy.ps1 -Restart        # upload and restart the running service
```

It normalises line endings to LF (a CRLF shebang makes `start.sh`
unrunnable on Linux) while copying vendored bundles byte for byte, and
uploads `localprint.conf` along with the code so the server has its
settings. If the systemd unit is installed, `-Restart` restarts *that*;
otherwise it starts a detached process directly.

The target comes from `LOCALPRINT_HOST`, `LOCALPRINT_USER` and
`LOCALPRINT_REMOTE_PATH` in `localprint.conf`, and can be overridden per
run:

```powershell
.\deploy.ps1 -RemoteHost my-nas -RemoteUser me
```

## Configuration

All settings live in **`localprint.conf`** next to `app.py`. That file is
git-ignored because it describes your network, not the project; copy the
committed template to create it:

```bash
cp localprint.conf.example localprint.conf
```

It is a plain `KEY=value` file (`#` starts a comment), readable by the app,
by `install.sh` and by `deploy.ps1` alike. **Nothing has a default** — every
setting below must be present, or the app exits with an error naming it.
Environment variables of the same name override the file, which is handy for
one-off runs and for the test suite.

| Setting | Example | Purpose |
| --- | --- | --- |
| `LOCALPRINT_PRINTER` | `office` | CUPS queue name (`lpstat -p` lists them) |
| `LOCALPRINT_PORT` | `8081` | Port to listen on |
| `LOCALPRINT_LAN_PREFIX` | `10.1.2.` | Required prefix of the bind address; the trailing dot is required, or `10.1.2` would also match `10.1.22.x` |
| `LOCALPRINT_MAX_UPLOAD_MB` | `50` | Largest accepted upload; also shown in the UI |
| `LOCALPRINT_LP_TIMEOUT_SECONDS` | `30` | How long to wait for `lp` |
| `LOCALPRINT_MIN_COPIES` | `1` | Lower bound of the Copies field |
| `LOCALPRINT_MAX_COPIES` | `20` | Upper bound of the Copies field |

Deployment settings, read by `deploy.ps1` only:

| Setting | Example | Purpose |
| --- | --- | --- |
| `LOCALPRINT_HOST` | `my-nas` | SSH host to deploy to |
| `LOCALPRINT_USER` | `me` | SSH user |
| `LOCALPRINT_REMOTE_PATH` | `local-print` | Folder under the user's home |

Optional settings, which turn a feature on when present:

| Setting | Purpose |
| --- | --- |
| `LOCALPRINT_OPENAI_API_KEY` | OpenAI API key; enables [Upscaling](#upscaling) and [Generating images](#generating-images) |
| `LOCALPRINT_OPENAI_IMAGE_MODEL` | Image model for both (default `gpt-image-2`) |

Optional development switches, normally left unset:

| Setting | Purpose |
| --- | --- |
| `LOCALPRINT_FAKE_PRINTER` | `1` stubs the printer and allows binding localhost |
| `LOCALPRINT_DEBUG` | `1` enables the Flask reloader |
| `LOCALPRINT_CONFIG` | Path to an alternative config file |

Accepted file types are a property of the printing pipeline rather than a
site preference, so they stay in `config.py`.

## Print options

The extra controls under **Print options** are not configured anywhere. On
each request LocalPrint asks CUPS what the printer can actually do:

```bash
lpoptions -p <printer> -l
```

Everything the printer reports for these keywords is offered, in this order:

| Keyword | Shown as |
| --- | --- |
| `ColorModel` | Colour |
| `cupsPrintQuality` | Quality |
| `PageSize` | Paper size |
| `MediaType` | Paper type |
| `InputSlot` | Paper source |
| `Resolution` | Resolution |

So a mono laser offers no colour choice, and a photo printer offers its
borderless paper sizes — without either being spelled out anywhere. Groups
the printer marks with a single choice are dropped, and the choice CUPS
starts with (`*`) is preselected.

Paper type is the one exception: if the printer offers an **Auto** setting
it wins, because PPDs tend to default to one specific stock (Canon starts
on `Com.canon.mtinkjeta`) and the driver guesses better than a fixed pick.

The panel starts **collapsed** — printing is normally "choose a file, press
Print" — and its summary line lists whatever differs from the defaults, or
reads "Printer defaults".

`Duplex` is deliberately not listed: the form already has a double-sided
toggle, and two controls for one setting would disagree.

The result is cached for five minutes. If CUPS cannot be reached the panel
is simply empty and printing continues with the printer's own defaults.

Values are validated against what was discovered, so the browser can only
ever ask for a choice the printer itself reported.

Two things follow from reading the printer directly:

- Choice names come from the PPD, so they are the manufacturer's, not ours
  (`Com.canon.mtinkjeta`). LocalPrint tidies the common ones — `Gray`
  becomes *Black & white* — and prettifies the rest.
- After swapping the printer or its driver, restart the service (or wait
  five minutes) for the new options to appear.

## Upscaling

A picture that looks fine on a phone can be far too small for paper. When
an image is chosen, LocalPrint works out its resolution once it is scaled to
fill an A4 page (turned to match the picture). Below **150 dpi** it shows a
note and a button saying exactly what would happen:

> Low resolution for A4: 55 dpi. It may print blurry.
> **Upscale 480×640 → 2480×3312**

The target aims for 300 dpi on A4, keeps the picture's shape, and stays
within what the image model can produce (edges in multiples of 16 px, at
most 3840 px and about 8.3 megapixels). Pictures more than 3:1 wide or tall
get the note but no button, since the model cannot draw them.

Pressing the button sends the image to OpenAI's image edit endpoint
(`gpt-image-2` by default), asking for a faithful redraw at that size. It
usually takes about a minute; Print is disabled meanwhile. The result
replaces the chosen file as `<name>-upscaled.jpg`, so you can check the
preview before printing it — upscaling never prints anything on its own.

To enable it, add your key to `localprint.conf`:

```
LOCALPRINT_OPENAI_API_KEY=sk-...
```

Without a key the button never appears. The server needs internet access to
`api.openai.com` for this feature only, and each upscale is billed to the
key's OpenAI account.

## Generating images

With the same key, Image mode offers **Generate from text**. Tapping it
opens a box to describe the picture and a choice of portrait or landscape;
**Generate** asks OpenAI's image model to draw it.

The image is drawn in A4's proportions at 1024×1440 with OpenAI's `low`
quality, which keeps each image to roughly 150 output tokens — about half a
cent — and takes 10–20 seconds. Print is disabled meanwhile. That is about
120 dpi on A4: fine for most pictures, and the [Upscale](#upscaling) button
appears so a keeper can be redrawn sharper (upscaling runs at high quality
and costs considerably more).

The result becomes the chosen image, `generated-<description>.jpg`, and is
shown full width so you can check it first. Nothing prints until you press
**Print**; if it is not right, edit the description and press **Generate
again**. Choosing a file of your own, or switching to PDF, abandons a
generation still in progress.

OpenAI may refuse a description that breaks its usage policies; the reason
it gives is shown as the error.

## When nothing prints

CUPS stops a queue when the printer fails mid-job — a dropped Wi-Fi
connection is enough — and it never restarts it on its own. A stopped queue
still *accepts* jobs, so `lp` keeps reporting success and every print piles
up unseen. This is the most likely reason nothing comes out.

LocalPrint makes that visible. The dot beside the printer name turns red and
a panel appears above the form giving the state, the reason CUPS recorded,
and how many jobs are waiting:

> **Printer stopped · 8 jobs stuck**
> Unable to send raster data to the driver.

**Clear queue** cancels everything waiting and restarts the printer. Both
halves matter: cancelling alone would leave the queue stopped, so the next
job would vanish exactly like the ones just discarded.

The panel hides itself when there is nothing waiting and the printer is
running, and it works without JavaScript.

### Permissions

Cancelling other people's jobs and restarting a printer are CUPS
administrative operations, limited to its `SystemGroup` (`lpadmin` on
Debian). `install.sh` adds the service user to that group, which is enough
over the local socket — no password, and no `sudo`, which the service could
not use anyway because its unit sets `NoNewPrivileges`.

If you installed by hand, grant it yourself and restart the service:

```bash
sudo usermod -aG lpadmin "$USER"
sudo systemctl restart localprint
```

Without it the button reports `Forbidden` and says so.

You can always fall back to the command line:

```bash
lpstat -p              # is the queue stopped, and why?
lpstat -o              # what is waiting
sudo cancel -a <printer>
sudo cupsenable <printer>
```

## Tests

```bash
pip install -r requirements-dev.txt
python -m playwright install chromium

python -m pytest                  # everything (~20s)
python -m pytest -m "not e2e"     # backend only, no browser needed (<1s)
```

- `tests/test_printing.py` — page-range parsing and the exact `lp` argument
  list, including the `--` separator that stops a crafted filename becoming
  an option.
- `tests/test_options.py` — parsing `lpoptions` output, the choices that get
  offered, caching, and coping with a printer that cannot be queried.
- `tests/test_routes.py` — the HTTP surface: uploads, option pass-through,
  validation, and the guarantee that the temporary upload is deleted after
  printing.
- `tests/test_config.py` — the config loader, including its refusal to
  invent defaults.
- `tests/test_upscale.py` — when an upscale is offered and to what size,
  reading image sizes, and the OpenAI request, with the API stubbed out.
- `tests/test_generate.py` — generating from a description: the request,
  sizes and validation, with the API stubbed out.
- `tests/test_ui.py` — Playwright browser tests for the parts that only
  exist in JavaScript: the page picker, drag and drop, upscaling, generating
  from text, the large preview, mobile layout, dark mode and the
  no-JavaScript fallback.

No test ever contacts a real printer, nor OpenAI unless you opt in:

```bash
LOCALPRINT_TEST_OPENAI_API_KEY=sk-... python -m pytest -m openai
```

## How it works

An upload is streamed to a temporary file, passed to `lp` as an argument
list (never a shell string), and deleted in a `finally` block. The client
filename is used only for display and for the extension check — it never
touches the filesystem.

```
local-print/
├── app.py                 # Flask app: routes and request validation
├── printing.py            # Builds and runs the lp command
├── upscale.py             # Low-resolution check and upscaling
├── openai_images.py       # OpenAI image API: edit and generate
├── config.py              # Strict loader for localprint.conf
├── localprint.conf.example# Configuration template
├── templates/index.html
├── static/
│   ├── style.css          # Mobile-first, light + dark
│   ├── app.js             # Drag & drop, page picker, upload progress
│   └── vendor/            # Vendored pdf.js
├── start.sh               # Run in the foreground
├── install.sh             # Install as a systemd service
└── deploy.ps1             # Upload from a Windows workstation
```

`spec.md` documents the design and acceptance criteria in more detail.

## Security

**LocalPrint has no authentication.** Anyone who can reach the port can
print. The trust boundary is your LAN, which is why the app refuses to bind
anything outside the configured private range.

Do not expose it to the internet. If you need remote access, put it behind
a VPN.

With the OpenAI features enabled, anyone on the LAN can spend the configured
key's credit. Images and descriptions are sent to OpenAI only when someone
presses **Upscale** or **Generate**.

## Third-party

PDF thumbnails are rendered with
[pdf.js](https://mozilla.github.io/pdf.js/) (Apache-2.0), vendored under
`static/vendor/` so the server needs no internet access.

## License

MIT — see [LICENSE](LICENSE).
