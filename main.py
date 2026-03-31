#!/usr/bin/env python3
"""
HubSpot ↔ Mailchimp Sync - Local convenience wrapper.

Delegates to the unified CLI entrypoint: corev2.cli sync

For full options, run:  python -m corev2.cli --help
"""
import os
import sys
from pathlib import Path

# Auto-load .env for local dev
os.environ.setdefault("LOAD_DOTENV", "1")

from corev2.cli import sync_mode

if __name__ == "__main__":
    sys.exit(sync_mode(Path("corev2/config/production.yaml")))
