#!/bin/bash
#
# Installs LocalPrint as a systemd service so it starts with the server.
#
#   ./install.sh              install (or re-install) and start the service
#   ./install.sh --uninstall  stop, disable and remove the service
#
# Safe to run repeatedly: it rewrites the unit, refreshes the virtualenv and
# restarts the service.
#
set -euo pipefail

SERVICE=localprint
UNIT="/etc/systemd/system/${SERVICE}.service"
APP_DIR="$(cd "$(dirname "$0")" && pwd)"

# Writing to /etc/systemd needs root, so re-run under sudo and remember who
# invoked us: the service must keep running as the owner of the app folder.
if [ "$(id -u)" -ne 0 ]; then
    exec sudo -E "$0" "$@"
fi

RUN_USER="${SUDO_USER:-$(stat -c '%U' "$APP_DIR")}"
RUN_GROUP="$(id -gn "$RUN_USER")"
VENV="$APP_DIR/venv"
PYTHON="$VENV/bin/python3"

say() { printf '\033[36m%s\033[0m\n' "$*"; }
ok()  { printf '\033[32m%s\033[0m\n' "$*"; }
die() { printf '\033[31m%s\033[0m\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------- uninstall

if [ "${1:-}" = "--uninstall" ]; then
    say "Removing the ${SERVICE} service..."
    systemctl disable --now "$SERVICE" 2>/dev/null || true
    rm -f "$UNIT"
    systemctl daemon-reload
    systemctl reset-failed "$SERVICE" 2>/dev/null || true
    ok "Removed. The application files in $APP_DIR were left untouched."
    exit 0
fi

if [ "${1:-}" != "" ]; then
    die "Unknown option: $1 (expected --uninstall or nothing)"
fi

# ------------------------------------------------------------------ checks

[ -f "$APP_DIR/app.py" ] || die "app.py not found in $APP_DIR."
[ -f "$APP_DIR/requirements.txt" ] || die "requirements.txt not found in $APP_DIR."
command -v systemctl >/dev/null || die "systemd is required but systemctl was not found."

if ! command -v lp >/dev/null; then
    printf '\033[33m%s\033[0m\n' "Warning: the 'lp' command was not found. Install CUPS before printing."
fi

# ----------------------------------------------------------------- python

if [ ! -x "$PYTHON" ]; then
    say "Creating the virtualenv in $VENV..."
    sudo -u "$RUN_USER" python3 -m venv "$VENV" \
        || die "Could not create the virtualenv. Try: apt install python3-venv"
fi

say "Installing Python dependencies..."
sudo -u "$RUN_USER" "$VENV/bin/pip" install --quiet --upgrade pip
sudo -u "$RUN_USER" "$VENV/bin/pip" install --quiet -r "$APP_DIR/requirements.txt"

PORT="$(sudo -u "$RUN_USER" "$PYTHON" -c 'import config; print(config.PORT)' 2>/dev/null || echo 8081)"

# ------------------------------------------------------------------- unit

say "Writing $UNIT..."
cat > "$UNIT" <<UNIT_EOF
[Unit]
Description=LocalPrint web UI
Documentation=file://$APP_DIR/spec.md
# The app binds a specific LAN address, so it needs the network configured.
# CUPS is not required to start, only to print, hence a soft ordering.
Wants=network-online.target
After=network-online.target cups.service

[Service]
Type=simple
User=$RUN_USER
Group=$RUN_GROUP
WorkingDirectory=$APP_DIR
Environment=PYTHONUNBUFFERED=1
ExecStart=$PYTHON $APP_DIR/app.py

# The LAN address may still be missing when the unit first runs, so keep
# retrying instead of failing the boot.
Restart=always
RestartSec=3

NoNewPrivileges=true
PrivateTmp=true
ProtectHome=read-only
ReadWritePaths=$APP_DIR

[Install]
WantedBy=multi-user.target
UNIT_EOF

# --------------------------------------------------------------- takeover

# A copy started by hand (setsid ./start.sh) would still hold the port.
say "Stopping any instance that is already running..."
systemctl stop "$SERVICE" 2>/dev/null || true
pkill -u "$RUN_USER" -f "python3 .*app\.py" 2>/dev/null || true
sleep 1

say "Enabling and starting the service..."
systemctl daemon-reload
systemctl enable "$SERVICE" >/dev/null
systemctl restart "$SERVICE"

# ----------------------------------------------------------------- verify

say "Verifying..."
for _ in $(seq 1 20); do
    if systemctl is-active --quiet "$SERVICE"; then
        break
    fi
    sleep 0.5
done

if ! systemctl is-active --quiet "$SERVICE"; then
    printf '\033[31m%s\033[0m\n' "The service did not start. Recent log:" >&2
    journalctl -u "$SERVICE" -n 30 --no-pager >&2
    exit 1
fi

# The app binds the LAN address only, so ask it which one rather than
# assuming 127.0.0.1 (which it deliberately never listens on).
LAN_IP="$(sudo -u "$RUN_USER" "$PYTHON" -c 'import app; print(app.get_lan_ip())' 2>/dev/null || true)"
[ -n "$LAN_IP" ] || LAN_IP="127.0.0.1"
URL="http://${LAN_IP}:${PORT}"

if command -v curl >/dev/null; then
    # systemd reports a Type=simple unit active as soon as it forks, which is
    # before Flask has bound the socket, so poll rather than asking once.
    CODE="000"
    for _ in $(seq 1 30); do
        CODE="$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 "$URL/" 2>/dev/null)" || CODE="000"
        if [ "$CODE" = "200" ]; then
            break
        fi
        sleep 1
    done

    if [ "$CODE" != "200" ]; then
        printf '\033[31m%s\033[0m\n' "The service is running but $URL/ returned HTTP $CODE. Recent log:" >&2
        journalctl -u "$SERVICE" -n 30 --no-pager >&2
        exit 1
    fi
fi

ok "LocalPrint is installed and will start automatically at boot."
ok "  URL:     $URL"
ok "  Status:  systemctl status $SERVICE"
ok "  Logs:    journalctl -u $SERVICE -f"
ok "  Restart: sudo systemctl restart $SERVICE"
ok "  Remove:  sudo $APP_DIR/install.sh --uninstall"
