"""Tests for the main window shell (spec sections 4.3 and 5.1; Task 3) and how the program starts (distribution spec 2)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap

import pytest
from PySide6.QtCore import QSize
from qframelesswindow.utils import win32_utils

import siftool
from fixtures.make_fixtures import make_png, write
from siftool_gui.app import create_window, open_window
from siftool_gui.entries import EntryState

SELF_TEST_TIMEOUT = 180  # s; a whole window and a clean, in a fresh process


@pytest.fixture
def offscreen_states(monkeypatch):
    """On a state change qframelesswindow asks Win32 whether the window is maximized; off screen there is
    no Win32 window to ask, so the answer here is "no" (as in test_scale_accessibility)."""
    monkeypatch.setattr(win32_utils, "isMaximized", lambda handle: False)


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


def test_window_has_the_siftool_icon_in_every_size(qtbot, tmp_path):
    window = create_window(tmp_path / "cfg.json")
    qtbot.addWidget(window)

    sizes = {size.width() for size in window.windowIcon().availableSizes()}
    assert {16, 32, 48, 256} <= sizes  # siftool.ico: the title bar, the taskbar and Alt+Tab each pick theirs


def _stop(window) -> None:
    window.clean_page.cancel()
    assert window.runner.wait_for_done(10_000)


def test_open_paths_adds_them_and_brings_the_window_forward(qtbot, tmp_path, settle, offscreen_states):
    photo = write(tmp_path, "foto.png", make_png())
    window = create_window(tmp_path / "cfg.json")
    qtbot.addWidget(window)
    window.show()
    window.switchTo(window.about_page)
    window.showMinimized()

    window.open_paths([str(photo)])

    assert window.isVisible() and not window.isMinimized()
    assert window.stackedWidget.currentWidget() is window.clean_page
    settle(window.clean_page, 1)
    model = window.clean_page.model
    assert [model.entry(entry_id).path for entry_id in model.entry_ids()] == [photo]
    _stop(window)


def test_open_paths_without_paths_only_brings_the_window_forward(qtbot, tmp_path, offscreen_states):
    # A second launch with nothing to add (the Start menu, again): the window just comes back.
    window = create_window(tmp_path / "cfg.json")
    qtbot.addWidget(window)
    window.show()
    window.switchTo(window.settings_page)
    window.showMinimized()

    window.open_paths([])

    assert window.isVisible() and not window.isMinimized()
    assert window.stackedWidget.currentWidget() is window.settings_page  # where the user left it
    assert window.clean_page.model.summary().total == 0


def test_command_line_paths_reach_the_list_once_the_window_shows(qtbot, tmp_path, settle):
    photo = write(tmp_path, "foto.png", make_png())
    folder = tmp_path / "pasta"
    folder.mkdir()
    other = write(folder, "outra.png", make_png())

    window = open_window([str(photo), str(folder)], tmp_path / "cfg.json")
    qtbot.addWidget(window)

    assert window.isVisible()
    settle(window.clean_page, 2)
    model = window.clean_page.model
    assert sorted(model.entry(entry_id).path for entry_id in model.entry_ids()) == sorted([photo, other])
    assert {model.entry(entry_id).state for entry_id in model.entry_ids()} == {EntryState.ANALYZED}
    _stop(window)


# -- the hidden self-test the build runs on the frozen program --------------------------------


def _environment(tmp_path) -> dict[str, str]:
    """A fresh process's environment: off-screen, and an APPDATA and a temporary folder of its own.

    Never the user's settings; and what the process leaves in its temporary folder can be seen.
    """
    temp = tmp_path / "temp"
    temp.mkdir(exist_ok=True)
    return dict(os.environ, QT_QPA_PLATFORM="offscreen", APPDATA=str(tmp_path / "appdata"),
                TEMP=str(temp), TMP=str(temp), TMPDIR=str(temp))


def test_self_test_passes_from_the_source_tree(tmp_path):
    work = tmp_path / "trabalho"
    work.mkdir()
    result = subprocess.run(
        [sys.executable, "-P", "-m", "siftool_gui", "--self-test", "resultado.json"],
        cwd=work,
        env=_environment(tmp_path),
        capture_output=True,
        text=True,
        timeout=SELF_TEST_TIMEOUT,
    )

    report = json.loads((work / "resultado.json").read_text(encoding="utf-8"))
    assert result.returncode == 0, report
    assert report["ok"] is True
    assert report["version"] == siftool.__version__
    assert report["portable"] is False
    assert report["config"] == str(tmp_path / "appdata" / "Siftool" / "config.json")
    assert not (tmp_path / "appdata").exists()  # the user's settings are never read or written
    assert [path.name for path in work.iterdir()] == ["resultado.json"]
    assert list((tmp_path / "temp").iterdir()) == []  # its photo, copy and throwaway settings are gone


def test_self_test_reports_a_failure(tmp_path):
    code = textwrap.dedent(
        """
        from siftool_gui import app

        def broken(*args, **kwargs):
            raise RuntimeError("janela quebrada")

        app.create_window = broken
        raise SystemExit(app.main(["--self-test", "resultado.json"]))
        """
    )

    work = tmp_path / "trabalho"
    work.mkdir()
    result = subprocess.run(
        [sys.executable, "-P", "-c", code],
        cwd=work,
        env=_environment(tmp_path),
        capture_output=True,
        text=True,
        timeout=SELF_TEST_TIMEOUT,
    )

    assert result.returncode == 1, result.stderr
    report = json.loads((work / "resultado.json").read_text(encoding="utf-8"))
    assert report["ok"] is False
    assert "janela quebrada" in report["error"]
    assert report["version"] == siftool.__version__
    assert list((tmp_path / "temp").iterdir()) == []  # cleaned up after a failure too
