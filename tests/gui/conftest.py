"""Shared setup for Qt-dependent tests (spec section 8, "Com Qt").

Sets the offscreen platform *before* anything in this process gets a
chance to import Qt, and skips this whole directory when PySide6 isn't
installed -- so the pure-logic suite (`tests/test_gui_*.py`,
`tests/test_engine_isolation.py`) stays runnable without the `gui` extra.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
