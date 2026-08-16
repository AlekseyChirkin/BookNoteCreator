#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Application paths for development and portable (PyInstaller) builds."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def is_frozen() -> bool:
    return bool(getattr(sys, 'frozen', False))


def app_dir() -> Path:
    """Directory next to the executable (portable) or project root (dev)."""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def resource_dir() -> Path:
    """Bundled read-only resources (templates, static) when frozen."""
    if is_frozen():
        return Path(getattr(sys, '_MEIPASS', app_dir()))
    return app_dir()


def output_dir() -> Path:
    """Writable output folder for generated markdown and covers."""
    path = app_dir() / '_resources'
    path.mkdir(parents=True, exist_ok=True)
    return path


def templates_dir() -> Path:
    return resource_dir() / 'templates'


def static_dir() -> Path:
    return resource_dir() / 'static'


def playwright_browsers_dir() -> Path:
    """Local Playwright browser cache shipped with the portable folder."""
    path = app_dir() / 'playwright-browsers'
    path.mkdir(parents=True, exist_ok=True)
    return path


def configure_playwright() -> None:
    """Point Playwright to browsers inside the portable folder."""
    os.environ.setdefault('PLAYWRIGHT_BROWSERS_PATH', str(playwright_browsers_dir()))
