"""Resolve writable app dir vs bundled resource dir (PyInstaller-safe)."""

from __future__ import annotations

import sys
from pathlib import Path


def app_dir() -> Path:
    """Directory for user files (config.json / contacts.json), next to the .exe when frozen."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def resource_dir() -> Path:
    """Bundled read-only resources (ocr script, example config)."""
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return Path(meipass)
        return Path(sys.executable).resolve().parent / "_internal"
    return Path(__file__).resolve().parent
