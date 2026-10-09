"""Locate the folder that owns user data.

A source checkout and a PyInstaller build disagree about where a module lives.
In a one-folder build the Python files sit inside the ``_internal`` bundle, so
``__file__`` points into the bundle instead of the folder the user opened.
Saved games stay beside the executable, which is what the README documents.
"""

from __future__ import annotations

import sys
from pathlib import Path


def application_dir() -> Path:
    """Return the folder that holds user data.

    A frozen build writes beside its executable; a source checkout writes next
    to this module.
    """

    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent
