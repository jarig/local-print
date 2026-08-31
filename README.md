# LocalPrint

A small, mobile-friendly web UI for printing PDFs and images to a CUPS
printer on your home network.

Open the page on any phone, tablet or laptop on the LAN, pick a file, choose
which pages you want, and print. No app to install, no cloud service, no
account.

```
┌──────────────────────────────┐
│  Print              ● my-printer  │
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
- **Mobile-first** — designed for a phone, works on a desktop. Follows the
  system light/dark theme.
- **Drag and drop** with upload progress.
- **Works offline** — no CDNs. `pdf.js` is vendored, so the LAN needs no
  internet access.
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

python3 -m venv venv
venv/bin/pip install -r requirements.txt

./start.sh
```

The server prints the URL it bound to, e.g. `http://10.1.2.3:8081`.
Open that from your phone.

> By default the app only binds an address in `10.1.2.0/24` and refuses
> to start otherwise, so it can never accidentally listen on `0.0.0.0`. If
> your LAN uses a different range, set `LOCALPRINT_LAN_PREFIX` (see
> [Configuration](#configuration)).

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
cd ~/local-print
./install.sh
```

It re-runs itself under `sudo` and will:

1. Create the virtualenv (`venv/`) if it is missing.
2. Install `requirements.txt`.
3. Write `/etc/systemd/system/localprint.service`.
4. Enable it for `multi-user.target` and start it.
5. Wait for the app to answer on its LAN URL, and print that URL.

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

`deploy.ps1` (PowerShell) uploads the app over `scp` to `~/local-print/` on the
server:

```powershell
.\deploy.ps1                 # upload only
.\deploy.ps1 -Restart        # upload and restart the running service
```

It normalises line endings to LF (a CRLF shebang makes `start.sh`
unrunnable on Linux) while copying vendored bundles byte for byte. If the
systemd unit is installed, `-Restart` restarts *that*; otherwise it starts a
detached process directly.

Set the target with parameters or environment variables:

```powershell
.\deploy.ps1 -RemoteHost my-nas -RemoteUser me
$env:LOCALPRINT_HOST = "my-nas"; $env:LOCALPRINT_USER = "me"
```

## Configuration

Everything is read from the environment at startup.

| Variable | Default | Purpose |
| --- | --- | --- |
| `LOCALPRINT_PRINTER` | `my-printer` | CUPS queue name (`lpstat -p` lists them) |
| `LOCALPRINT_PORT` | `8081` | Port to listen on |
| `LOCALPRINT_LAN_PREFIX` | `10.1.2.` | Required prefix of the bind address |
| `LOCALPRINT_FAKE_PRINTER` | unset | `1` stubs the printer and allows binding localhost |
| `LOCALPRINT_DEBUG` | unset | `1` enables the Flask reloader |

Limits that are rarely worth changing (upload size, copy bounds, allowed
extensions, `lp` timeout) live at the top of `config.py`.

## Tests

```bash
pip install -r requirements-dev.txt
python -m playwright install chromium

python -m pytest                  # everything (~18s)
python -m pytest -m "not e2e"     # backend only, no browser needed (<1s)
```

- `tests/test_printing.py` — page-range parsing and the exact `lp` argument
  list, including the `--` separator that stops a crafted filename becoming
  an option.
- `tests/test_routes.py` — the HTTP surface: uploads, option pass-through,
  validation, and the guarantee that the temporary upload is deleted after
  printing.
- `tests/test_ui.py` — Playwright browser tests for the parts that only
  exist in JavaScript: the page picker, drag and drop, mobile layout, dark
  mode and the no-JavaScript fallback.

No test ever contacts a real printer.

## How it works

An upload is streamed to a temporary file, passed to `lp` as an argument
list (never a shell string), and deleted in a `finally` block. The client
filename is used only for display and for the extension check — it never
touches the filesystem.

```
local-print/
├── app.py                 # Flask app: routes and request validation
├── printing.py            # Builds and runs the lp command
├── config.py              # Printer name, port, limits
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

## Third-party

PDF thumbnails are rendered with
[pdf.js](https://mozilla.github.io/pdf.js/) (Apache-2.0), vendored under
`static/vendor/` so the server needs no internet access.

## License

MIT — see [LICENSE](LICENSE).
