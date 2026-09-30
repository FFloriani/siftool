"""Tests for following Windows' light/dark setting live (spec 5.1 and 5.6; ruling P11(d)).

No test changes the real Windows theme: the listener thread is started and
stopped for real, and a change is simulated by calling the handler the
thread calls (`_onThemeChanged`) with `darkdetect.theme` answering what
"Windows" now says. `theme_state` restores the theme mode *and* the
resolved theme afterwards -- a system change only moves the latter.
"""

from __future__ import annotations

import json
import time

import darkdetect
import pytest
from qfluentwidgets import SystemThemeListener, Theme, isDarkTheme, qconfig
from qfluentwidgets.common.style_sheet import updateStyleSheet

from siftool_gui.app import create_window
from siftool_gui.system_theme import ThemeListener

TIMEOUT = 5_000  # ms


@pytest.fixture
def theme_state():
    mode, theme = qconfig.themeMode.value, qconfig.theme
    yield
    qconfig.themeMode.value = mode
    qconfig.theme = theme
    updateStyleSheet(lazy=True)


def _choose(card, text: str) -> None:
    (button,) = [button for button in card.buttonGroup.buttons() if button.text() == text]
    button.click()


def test_listener_stops_at_once_and_never_terminates(qtbot, monkeypatch):
    def terminate(self):  # killing a thread that may hold the GIL can hang the whole program
        raise AssertionError("the listener must stop without QThread.terminate()")

    monkeypatch.setattr(ThemeListener, "terminate", terminate)
    listener = ThemeListener()
    assert isinstance(listener, SystemThemeListener)  # QFluentWidgets' listener, made stoppable

    for pause in (0.0, 0.05):  # right after starting, and once it waits for a change
        listener.start()
        assert listener.isRunning()
        time.sleep(pause)
        started = time.perf_counter()
        listener.stop()
        assert not listener.isRunning()
        assert time.perf_counter() - started < 1.0
    listener.stop()  # stopping twice is harmless


def test_windows_theme_is_followed_only_while_seguir_o_windows(qtbot, tmp_path, theme_state):
    window = create_window(tmp_path / "cfg.json")  # the default: "Seguir o Windows"
    qtbot.addWidget(window)
    first = window.theme_listener
    assert isinstance(first, ThemeListener) and first.isRunning()

    _choose(window.settings_page.theme_card, "Claro")
    assert window.theme_listener is None  # a theme of its own: nothing to follow
    assert not first.isRunning()

    _choose(window.settings_page.theme_card, "Seguir o Windows")
    again = window.theme_listener
    assert again is not None and again.isRunning()

    assert window.close()
    assert window.theme_listener is None
    assert not again.isRunning()  # stopped with the window

    qconfig.set(qconfig.themeMode, Theme.DARK)
    qconfig.set(qconfig.themeMode, Theme.AUTO)
    assert window.theme_listener is None  # a closed window does not start listening again


def test_a_saved_theme_starts_no_listener(qtbot, tmp_path, theme_state):
    path = tmp_path / "light.json"
    path.write_text(json.dumps({"QFluentWidgets": {"ThemeMode": "Light"}}), encoding="utf-8")

    window = create_window(path)
    qtbot.addWidget(window)

    assert window.theme_listener is None


def test_a_windows_theme_change_restyles_the_open_window(qtbot, tmp_path, monkeypatch, theme_state):
    window = create_window(tmp_path / "cfg.json")
    qtbot.addWidget(window)
    listener = window.theme_listener
    qconfig.theme = Theme.LIGHT  # whatever this machine's theme is, the window starts light here

    monkeypatch.setattr(darkdetect, "theme", lambda: "Dark")  # Windows switched to dark
    with qtbot.waitSignal(qconfig.themeChangedFinished, timeout=TIMEOUT):  # restyled, as `setTheme` does
        listener._onThemeChanged("Dark")  # what the listener thread calls on the change
    assert isDarkTheme()

    monkeypatch.setattr(darkdetect, "theme", lambda: "Light")  # and back
    with qtbot.waitSignal(qconfig.themeChangedFinished, timeout=TIMEOUT):
        listener._onThemeChanged("Light")
    assert not isDarkTheme()
