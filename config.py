"""Configuration for the LocalPrint web server."""
import os

PRINTER = os.environ.get("LOCALPRINT_PRINTER", "my-printer")
PORT = int(os.environ.get("LOCALPRINT_PORT", "8081"))

MAX_UPLOAD_SIZE = 50 * 1024 * 1024  # 50 MB
LP_TIMEOUT_SECONDS = 30

MIN_COPIES = 1
MAX_COPIES = 20

PDF_EXTENSIONS = {".pdf"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png"}

LAN_PREFIX = os.environ.get("LOCALPRINT_LAN_PREFIX", "10.1.2.")

# Development mode: skip the real `lp` call and relax the LAN bind check so
# the UI can be worked on from a workstation without CUPS.
FAKE_PRINTER = os.environ.get("LOCALPRINT_FAKE_PRINTER") == "1"

# Kept separate from FAKE_PRINTER: the reloader interferes with the test
# server, which needs a fake printer but a single predictable process.
DEBUG = os.environ.get("LOCALPRINT_DEBUG") == "1"
