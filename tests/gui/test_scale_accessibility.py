"""Scale, keyboard and accessibility guarantees (spec 3 criterion 2, 5.2 and 6; Task 13).

- a folder of 1,000 photos is analyzed without freezing the window: a
  10 ms probe timer never waits 250 ms or more, at most `max_workers`
  analyses run at once, all end "Analisado" within 180 s, and the list
  scrolls to its end at once;
- the tab order and the shortcuts of spec 6;
- every icon-only control has a Portuguese tooltip and an accessible name;
- long names are cut with an ellipsis, whole in the tooltip.

Windows are shown off screen (`QT_QPA_PLATFORM=offscreen`, see conftest).
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, Qt, QTimer
from PySide6.QtGui import QFontMetrics, QHelpEvent, QImage, QPainter
from PySide6.QtWidgets import QAbstractButton, QApplication, QFileDialog, QStyleOptionViewItem, QToolTip, QWidget
from qfluentwidgets import InfoBar
from qfluentwidgets.components.navigation.navigation_widget import NavigationTreeItem, NavigationWidget
from qfluentwidgets.components.widgets.scroll_bar import ArrowButton
from qframelesswindow.utils import win32_utils

import siftool
from fixtures.make_fixtures import make_jpeg, make_png, write
from siftool.model import Mode

from siftool_gui.app import create_window
from siftool_gui.entries import EntryState
from siftool_gui.pages.clean_page import CleanPage, wrap_to_width
from siftool_gui.presentation import batch_message

TIMEOUT = 10_000  # ms
MAX_GAP_S = 0.250  # the longest the event loop may be kept busy (spec 3, criterion 2)
ANALYSIS_BUDGET_MS = 180_000  # 1,000 photos, analyzed


def _show(qtbot, widget: QWidget, width: int, height: int) -> None:
    widget.resize(width, height)
    widget.show()
    qtbot.waitExposed(widget)
    widget.activateWindow()
    qtbot.waitUntil(widget.isActiveWindow, timeout=TIMEOUT)


def _window(qtbot, tmp_path: Path, name: str = "cfg.json"):
    window = create_window(tmp_path / name)
    qtbot.addWidget(window)
    return window


# -- a thousand photos ----------------------------------------------------------------


def test_thousand_files_keep_ui_responsive(make_page, qtbot, tmp_path, monkeypatch):
    folder = tmp_path / "mil fotos"
    folder.mkdir()
    png, jpeg = make_png(), make_jpeg()
    for number in range(1000):
        if number % 2:
            write(folder, f"foto {number:04d}.jpg", jpeg)
        else:
            write(folder, f"foto {number:04d}.png", png)

    running = 0
    most = 0
    lock = threading.Lock()
    real_inspect = siftool.inspect

    def counted_inspect(path):  # how many analyses run at the same time
        nonlocal running, most
        with lock:
            running += 1
            most = max(most, running)
        try:
            return real_inspect(path)
        finally:
            with lock:
                running -= 1

    monkeypatch.setattr(siftool, "inspect", counted_inspect)
    page = make_page()
    _show(qtbot, page, 1000, 800)

    ticks = [time.perf_counter()]
    probe = QTimer()  # any other user of the event loop: painting, input, animations
    probe.setInterval(10)
    probe.timeout.connect(lambda: ticks.append(time.perf_counter()))
    probe.start()
    try:
        page.add_paths([str(folder)])

        def all_analyzed() -> bool:
            states = [page.model.entry(i).state for i in page.model.entry_ids()]
            return len(states) == 1000 and all(state is EntryState.ANALYZED for state in states)

        qtbot.waitUntil(all_analyzed, timeout=ANALYSIS_BUDGET_MS)
        qtbot.waitUntil(lambda: not page.busy(), timeout=ANALYSIS_BUDGET_MS)  # the previews too
    finally:
        probe.stop()
    ticks.append(time.perf_counter())

    gaps = [after - before for before, after in zip(ticks, ticks[1:])]
    print(f"1,000 photos: {ticks[-1] - ticks[0]:.1f} s, longest wait {max(gaps) * 1000:.0f} ms, "
          f"at most {most} analyses at once")  # shown with -s
    assert max(gaps) < MAX_GAP_S, f"the window froze for {max(gaps) * 1000:.0f} ms"
    assert most <= page.runner.max_workers()  # a bounded number of analyses at once
    assert page.summary_label.text() == "1000 fotos · 1000 com informações escondidas"

    # The list scrolls to its end, at once.
    view = page.view
    started = time.perf_counter()
    view.scrollToBottom()
    view.viewport().repaint()
    assert time.perf_counter() - started < MAX_GAP_S
    last = page.model.index_for(page.model.entry_ids()[-1])
    bar = view.verticalScrollBar()
    assert bar.maximum() > 0 and bar.value() == bar.maximum()
    assert view.viewport().rect().intersects(view.visualRect(last))


# -- keyboard ---------------------------------------------------------------------------


def _next_tab_stops(start: QWidget, count: int) -> list[QWidget]:
    """The next `count` widgets the focus chain gives Tab after `start` (shown, enabled, tab-focusable)."""
    stops: list[QWidget] = []
    widget = start.nextInFocusChain()
    while len(stops) < count and widget is not start:
        if widget.isVisible() and widget.isEnabled() and widget.focusPolicy() & Qt.FocusPolicy.TabFocus:
            stops.append(widget)
        widget = widget.nextInFocusChain()
    return stops


def test_tab_order(make_page, qtbot, photos, settle, monkeypatch):
    calls: list[tuple[QWidget, QWidget]] = []
    real_set_tab_order = QWidget.setTabOrder

    def recording_set_tab_order(first, second):
        calls.append((first, second))
        real_set_tab_order(first, second)

    monkeypatch.setattr(QWidget, "setTabOrder", staticmethod(recording_set_tab_order))
    page = make_page()
    monkeypatch.undo()
    page.add_paths([str(write(photos, "a.jpg", make_jpeg()))])  # a list, and "Limpar" enabled
    settle(page, 1)
    _show(qtbot, page, 900, 700)

    # Spec 6: "Adicionar fotos" -> "Adicionar pasta" -> list -> mode -> "Limpar".
    expected = [page.drop_area.add_files_button, page.drop_area.add_folder_button, page.view,
                *(page.mode_selector.widget(mode.value) for mode in Mode), page.clean_button]
    pairs = list(zip(expected, expected[1:]))
    assert [call for call in calls if call in pairs] == pairs  # set up by `setTabOrder`, in this order
    assert _next_tab_stops(expected[0], len(expected) - 1) == expected[1:]  # the chain that results

    expected[0].setFocus()  # and what Tab really does
    seen = [QApplication.focusWidget()]
    for _ in expected[1:]:
        qtbot.keyClick(QApplication.focusWidget(), Qt.Key.Key_Tab)
        seen.append(QApplication.focusWidget())
    assert seen == expected

    qtbot.keyClick(page.mode_selector.widget("maximum"), Qt.Key.Key_Space)  # a mode, from the keyboard
    assert page.model.mode() is Mode.MAXIMUM


def test_shortcuts(qtbot, tmp_path, monkeypatch):
    opened: list[str] = []
    monkeypatch.setattr(QFileDialog, "getOpenFileNames",
                        lambda *args: (opened.append("fotos"), ([], ""))[1])
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *args: (opened.append("pasta"), "")[1])
    cleaned: list[str] = []
    monkeypatch.setattr(CleanPage, "start_cleaning", lambda self: cleaned.append("limpar"))
    window = _window(qtbot, tmp_path)
    _show(qtbot, window, 1100, 760)
    target = window.clean_page.view
    control, shift = Qt.KeyboardModifier.ControlModifier, Qt.KeyboardModifier.ShiftModifier

    qtbot.keyClick(target, Qt.Key.Key_O, control)
    assert opened == ["fotos"]  # Ctrl+O: "Adicionar fotos"
    qtbot.keyClick(target, Qt.Key.Key_O, control | shift)
    assert opened == ["fotos", "pasta"]  # Ctrl+Shift+O: "Adicionar pasta"
    qtbot.keyClick(target, Qt.Key.Key_Return, control)
    qtbot.keyClick(target, Qt.Key.Key_Enter, control)
    assert cleaned == ["limpar", "limpar"]  # Ctrl+Enter, from either Enter key: "Limpar"

    window.switchTo(window.settings_page)  # the shortcuts belong to the "Limpar" page
    qtbot.waitUntil(lambda: not window.clean_page.isVisible(), timeout=TIMEOUT)
    qtbot.keyClick(window.settings_page, Qt.Key.Key_O, control)
    qtbot.keyClick(window.settings_page, Qt.Key.Key_Return, control)
    assert opened == ["fotos", "pasta"] and cleaned == ["limpar", "limpar"]


# -- screen reader and tooltips ---------------------------------------------------------------


def _icon_only_controls(window) -> list[QWidget]:
    """Every shown control that is only an icon: text-less buttons, and the (compact) navigation rail."""
    controls = []
    for widget in window.findChildren(QWidget):
        if not widget.isVisible() or isinstance(widget, ArrowButton):  # arrows: part of their scroll bar
            continue
        if isinstance(widget, NavigationWidget) or (isinstance(widget, QAbstractButton) and not widget.text()):
            controls.append(widget)
    return controls


def test_icon_only_controls_have_accessible_names_and_tooltips(qtbot, tmp_path, photos, settle, monkeypatch):
    window = _window(qtbot, tmp_path)
    _show(qtbot, window, 1100, 760)
    page = window.clean_page
    page.add_paths([str(write(photos, "a.jpg", make_jpeg()))])
    settle(page, 1)
    with qtbot.waitSignal(page.batchFinished, timeout=TIMEOUT):
        page.start_cleaning()  # the result message has a close button too

    missing: list[str] = []
    for current in (window.clean_page, window.settings_page, window.about_page):
        window.switchTo(current)
        qtbot.waitUntil(current.isVisible, timeout=TIMEOUT)
        for control in _icon_only_controls(window):
            # A rail item draws its icon in an inner part; the item shows the tooltip for both.
            tooltip = (control.parentWidget() if isinstance(control, NavigationTreeItem) else control).toolTip()
            if not tooltip.strip() or not control.accessibleName().strip():
                missing.append(f"{type(control).__name__} (tooltip {tooltip!r}, "
                               f"name {control.accessibleName()!r}) on {current.objectName()}")
    assert missing == [], "\n".join(missing)

    # In Portuguese, like the rest of the program.
    bar, panel = window.titleBar, window.navigationInterface.panel
    assert [bar.minBtn.toolTip(), bar.maxBtn.toolTip(), bar.closeBtn.toolTip()] == [
        "Minimizar", "Maximizar", "Fechar"]
    assert panel.returnButton.toolTip() == "Voltar"
    assert panel.menuButton.toolTip() == "Abrir navegação"
    panel.expand(useAni=False)  # the rail sets its own (English) tooltip as it opens and closes
    assert panel.menuButton.toolTip() == "Fechar navegação"
    panel.collapse()
    assert panel.menuButton.toolTip() == "Abrir navegação"
    for route, text in (("cleanPage", "Limpar"), ("settingsPage", "Configurações"), ("aboutPage", "Sobre")):
        item = window.navigationInterface.widget(route)
        assert (item.toolTip(), item.accessibleName()) == (text, text)
    (message,) = window.clean_page.findChildren(InfoBar)
    assert (message.closeButton.toolTip(), message.closeButton.accessibleName()) == (
        "Fechar", "Fechar mensagem")
    # A scroll bar's arrows are not tab stops: Tab only lands on named controls.
    assert all(arrow.focusPolicy() == Qt.FocusPolicy.NoFocus for arrow in window.findChildren(ArrowButton))

    # On a state change qframelesswindow asks Win32 whether to refresh its blur effect; off screen
    # there is no Win32 window to ask, so the answer here is "no".
    monkeypatch.setattr(win32_utils, "isMaximized", lambda handle: False)
    window.showMaximized()  # the maximize button says what it does now
    qtbot.waitUntil(lambda: bar.maxBtn.toolTip() == "Restaurar", timeout=TIMEOUT)
    assert bar.maxBtn.accessibleName() == "Restaurar"
    window.showNormal()
    qtbot.waitUntil(lambda: bar.maxBtn.toolTip() == "Maximizar", timeout=TIMEOUT)


@pytest.mark.parametrize("first_line", [
    "2 fotos limpas e verificadas · 1 com ressalvas · 1",  # seen at 200%: "... · 1" / "não limpou — ..."
    "2 fotos limpas e verificadas · 1 com ressalvas · 1 não",  # seen at 100%: "... · 1 não" / "limpou — ..."
])
def test_a_wrapped_message_breaks_between_its_parts(qtbot, first_line):
    # In the smallest window the message wraps; it breaks between the parts of the counts, not inside one.
    text = batch_message(2, 1, 1)
    metrics = QFontMetrics(QApplication.font())

    lines = wrap_to_width(text, metrics, metrics.horizontalAdvance(first_line)).split("\n")

    assert lines[0] == "2 fotos limpas e verificadas · 1 com ressalvas ·"
    assert lines[1].startswith("1 não limpou")
    assert " ".join(lines) == text  # only where the lines break changed


def test_rail_tooltips_come_without_a_python_translator(qtbot, tmp_path):
    # A QTranslator written in Python hung the program: Qt asked it from a thumbnail decoder's thread
    # while holding a lock the GUI thread (holding the GIL) waited for. See `window._PortugueseToolTips`.
    window = _window(qtbot, tmp_path)
    assert QCoreApplication.translate("NavigationPanel", "Back") == "Back"  # nothing translates Qt texts
    assert window.navigationInterface.panel.returnButton.toolTip() == "Voltar"  # yet the rail is Portuguese


class _RecordingPainter(QPainter):
    """A painter that remembers every text drawn through it (the delegate draws with `drawText`)."""

    def __init__(self, device) -> None:
        super().__init__(device)
        self.texts: list[str] = []

    def drawText(self, *args) -> None:
        if args and isinstance(args[-1], str):
            self.texts.append(args[-1])
        super().drawText(*args)


def test_long_names_are_elided_with_full_tooltip(make_page, qtbot, tmp_path, settle):
    name = ("viagem de família para a praia no verão de 2026, com os primos, os tios e os avós, "
            "foto número 0001 (editada).jpg")
    folder = tmp_path / "Fotos de família, viagens e passeios de fim de semana" / "Praia do Rosa, janeiro"
    folder.mkdir(parents=True)
    path = write(folder, name, make_jpeg())
    page = make_page()
    page.add_paths([str(path)])
    settle(page, 1)
    _show(qtbot, page, 720, 560)  # the smallest window: the least room for the name
    index = page.model.index_for(page.model.entry_ids()[0])
    rect = page.view.visualRect(index)

    image = QImage(rect.size(), QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(0)
    painter = _RecordingPainter(image)
    option = QStyleOptionViewItem()
    option.rect = rect.translated(-rect.topLeft())
    page.view.itemDelegate().paint(painter, option, index)
    painter.end()

    (shown_name,) = [text for text in painter.texts if text.endswith(".jpg")]
    assert shown_name != name and "…" in shown_name  # cut with an ellipsis...
    assert name.startswith(shown_name.split("…")[0]) and name.endswith(shown_name.split("…")[-1])
    assert shown_name.endswith("(editada).jpg")  # ... in the middle: the end, extension included, stays
    (shown_folder,) = [text for text in painter.texts if text.startswith(str(tmp_path)[:3]) and "…" in text]
    head, tail = shown_folder.split("…")
    assert str(folder).startswith(head) and str(folder).endswith(tail) and len(shown_folder) < len(str(folder))

    # The whole name (and folder) is one hover away.
    assert index.data(Qt.ItemDataRole.ToolTipRole) == str(path)
    center = rect.center()
    event = QHelpEvent(QEvent.Type.ToolTip, center, page.view.viewport().mapToGlobal(center))
    QApplication.sendEvent(page.view.viewport(), event)
    qtbot.waitUntil(QToolTip.isVisible, timeout=TIMEOUT)
    assert QToolTip.text() == str(path)
    QToolTip.hideText()
