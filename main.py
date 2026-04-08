#!/usr/bin/env python3
"""
HubSpot ↔ Mautic Sync - Entry Point

Local usage:
    python main.py

Loads .env automatically. For GitHub Actions, secrets are injected directly.
"""
import sys
from pathlib import Path
from corev2.cli import sync_mode

if __name__ == "__main__":
    sys.exit(sync_mode(Path("corev2/config/production.yaml")))
