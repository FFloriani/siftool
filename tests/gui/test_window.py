"""Tests for the main window shell (spec sections 4.3 and 5.1; Task 3)."""

from __future__ import annotations

import subprocess
import sys

from PySide6.QtCore import QSize

import siftool
from siftool_gui.app import create_window


def test_main_window_has_title_min_size_and_three_pages(qtbot, tmp_path):
    window = create_window(tmp_path / "cfg.json")
    qtbot.addWidget(window)

    assert window.windowTitle() == "Siftool"
    assert window.minimumSize() == QSize(720, 560)

    names = {page.objectName() for page in (window.clean_page, window.settings_page, window.about_page)}
    assert names == {"cleanPage", "settingsPage", "aboutPage"}


def test_gui_version_entry_point(tmp_path):
    result = subprocess.run(
        [sys.executable, "-P", "-m", "siftool_gui", "--version"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    assert siftool.__version__ in result.stdout
