#!/bin/bash
set -e
cd "$(dirname "$0")"
chmod +x app.py
exec venv/bin/python3 app.py
