"""Tests for the "Configurações" and "Sobre" pages, the theme and the window geometry (spec 5.1, 5.6, 5.7; Task 12).

Every config lives under `tmp_path`. Tests that switch the theme restore it
afterwards (`theme_guard`), so their order does not matter.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

import pytest
from PySide6.QtCore import QSize
from PySide6.QtWidgets import QApplication, QFileDialog, QLabel
from qfluentwidgets import InfoBar, ScrollArea, SettingCardGroup, Theme, TitleLabel, isDarkTheme, qconfig, setTheme

import siftool
from siftool.model import Mode

from siftool_gui import app as app_module
from siftool_gui.app import create_window
from siftool_gui.pages.about_page import AboutPage
from siftool_gui.pages.settings_page import NOT_SAVED_CONTENT, NOT_SAVED_TITLE, SettingsPage
from siftool_gui.presentation import display_version
from siftool_gui.settings import default_mode, load_config, output_dir
from siftool_gui.window import DEFAULT_SIZE, MINIMUM_SIZE


@pytest.fixture
def theme_guard():
    before = qconfig.get(qconfig.themeMode)
    yield
    setTheme(before)


@pytest.fixture
def settings_page(qtbot, config):
    page = SettingsPage(config)
    qtbot.addWidget(page)
    return page


def _options(card) -> list[str]:
    return [button.text() for button in card.buttonGroup.buttons()]


def _choose(card, text: str) -> None:
    (button,) = [button for button in card.buttonGroup.buttons() if button.text() == text]
    button.click()


# -- Configurações --------------------------------------------------------------------


def test_settings_page_cards_and_groups(settings_page):
    page = settings_page
    assert isinstance(page, ScrollArea)
    assert page.objectName() == "settingsPage"
    assert isinstance(page.title_label, TitleLabel) and page.title_label.text() == "Configurações"

    groups = page.findChildren(SettingCardGroup)
    assert [group.titleLabel.text() for group in groups] == ["Limpeza", "Aparência"]
    assert _options(page.mode_card) == ["Privacidade", "Fotógrafo", "Máximo"]
    assert _options(page.save_card) == ["Ao lado do original", "Numa pasta que eu escolher"]
    assert _options(page.theme_card) == ["Claro", "Escuro", "Seguir o Windows"]
    assert page.folder_card.button.text() == "Escolher pasta"
    for card in (page.mode_card, page.save_card, page.folder_card):
        assert card.parent() is groups[0]
    assert page.theme_card.parent() is groups[1]

    # The defaults, as the cards show them.
    assert page.mode_card.choiceLabel.text() == "Privacidade"
    assert page.save_card.choiceLabel.text() == "Ao lado do original"
    assert page.theme_card.choiceLabel.text() == "Seguir o Windows"


def test_default_mode_card_updates_config_and_next_clean_page(settings_page, config, make_page):
    open_page = make_page()  # a list that is already open, in the old default

    _choose(settings_page.mode_card, "Máximo")

    assert default_mode(config) is Mode.MAXIMUM
    assert settings_page.mode_card.choiceLabel.text() == "Máximo"
    assert open_page.model.mode() is Mode.PRIVACY  # an open list keeps its mode
    assert make_page().model.mode() is Mode.MAXIMUM  # the next one starts in the new default
    assert default_mode(load_config(config.file)) is Mode.MAXIMUM  # and it was saved


def test_save_location_toggles_folder_card(settings_page, config):
    page = settings_page
    assert not page.folder_card.isEnabled()
    assert page.folder_card.contentLabel.text() == "Nenhuma pasta escolhida"

    _choose(page.save_card, "Numa pasta que eu escolher")
    assert config.get(config.saveLocation) == "folder"
    assert page.folder_card.isEnabled()

    _choose(page.save_card, "Ao lado do original")
    assert config.get(config.saveLocation) == "beside"
    assert not page.folder_card.isEnabled()


def test_folder_card_sets_output_folder(settings_page, config, monkeypatch, tmp_path):
    chosen = tmp_path / "cópias, limpas"
    chosen.mkdir()
    asked: list[str] = []

    def fake_dialog(parent, caption, directory):
        asked.append(caption)
        return str(chosen).replace(os.sep, "/")  # Qt answers with forward slashes

    monkeypatch.setattr(QFileDialog, "getExistingDirectory", fake_dialog)
    page = settings_page
    _choose(page.save_card, "Numa pasta que eu escolher")

    page.folder_card.button.click()

    assert asked == ["Escolher pasta"]
    assert config.get(config.outputFolder) == str(chosen)
    assert output_dir(config) == chosen
    assert page.folder_card.contentLabel.text() == str(chosen)

    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *args: "")  # the dialog is cancelled
    page.folder_card.button.click()
    assert output_dir(config) == chosen

    assert output_dir(load_config(config.file)) == chosen  # saved


def test_folder_card_shows_a_saved_folder(qtbot, tmp_path):
    folder = tmp_path / "saida"
    path = tmp_path / "saved.json"
    path.write_text(json.dumps({"Cleaning": {"SaveLocation": "folder", "OutputFolder": str(folder)}}), encoding="utf-8")

    page = SettingsPage(load_config(path))
    qtbot.addWidget(page)

    assert page.folder_card.isEnabled()
    assert page.folder_card.contentLabel.text() == str(folder)
    assert page.save_card.choiceLabel.text() == "Numa pasta que eu escolher"


def test_theme_card_applies_theme(settings_page, config, qtbot, theme_guard):
    with qtbot.waitSignal(qconfig.themeChangedFinished, timeout=5000):  # `setTheme()` ran, at once
        _choose(settings_page.theme_card, "Escuro")
    assert isDarkTheme()
    assert config.get(config.themeMode) is Theme.DARK

    with qtbot.waitSignal(qconfig.themeChangedFinished, timeout=5000):
        _choose(settings_page.theme_card, "Claro")
    assert not isDarkTheme()
    assert load_config(config.file).get(config.themeMode) is Theme.LIGHT  # saved


# -- a settings folder that cannot be written (release prep: a portable copy on read-only media) ------------------


def _bars(page) -> list[InfoBar]:
    return page.findChildren(InfoBar)


def test_a_setting_changed_in_an_unwritable_folder_still_applies_and_says_so_once(
    qtbot, unwritable_config, theme_guard
):
    config = load_config(unwritable_config)
    page = SettingsPage(config)
    qtbot.addWidget(page)

    with qtbot.waitSignal(qconfig.themeChangedFinished, timeout=5000):  # the theme switches, as it always does
        _choose(page.theme_card, "Escuro")
    assert isDarkTheme() and config.get(config.themeMode) is Theme.DARK
    assert page.theme_card.choiceLabel.text() == "Escuro"

    (bar,) = _bars(page)  # and one message says the choice will not outlast this run
    assert bar.title == NOT_SAVED_TITLE == "Não foi possível salvar as configurações nesta pasta."
    assert bar.content == NOT_SAVED_CONTENT
    assert bar.closeButton.toolTip() == "Fechar" and bar.closeButton.accessibleName() == "Fechar mensagem"

    _choose(page.mode_card, "Máximo")
    _choose(page.save_card, "Numa pasta que eu escolher")
    assert len(_bars(page)) == 1  # one message for the whole run of failures
    assert default_mode(config) is Mode.MAXIMUM and config.get(config.saveLocation) == "folder"


def test_the_folder_chosen_in_an_unwritable_folder_is_used_anyway(qtbot, unwritable_config, monkeypatch, tmp_path):
    config = load_config(unwritable_config)
    page = SettingsPage(config)
    qtbot.addWidget(page)
    chosen = tmp_path / "cópias"
    chosen.mkdir()
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *args: str(chosen))
    _choose(page.save_card, "Numa pasta que eu escolher")

    page.folder_card.button.click()

    assert output_dir(config) == chosen  # this run copies there
    assert page.folder_card.contentLabel.text() == str(chosen)
    assert len(_bars(page)) == 1


def test_a_closed_message_does_not_come_back_while_saving_keeps_failing(qtbot, unwritable_config):
    config = load_config(unwritable_config)
    page = SettingsPage(config)
    qtbot.addWidget(page)
    _choose(page.mode_card, "Máximo")
    (bar,) = _bars(page)

    bar.close()
    qtbot.waitUntil(lambda: not _bars(page), timeout=5000)
    _choose(page.mode_card, "Fotógrafo")

    assert _bars(page) == []  # the same failure, already told
    assert default_mode(config) is Mode.PHOTOGRAPHER


def test_a_writable_folder_shows_no_message(qtbot, config):
    page = SettingsPage(config)
    qtbot.addWidget(page)

    _choose(page.mode_card, "Máximo")

    assert _bars(page) == []


def test_a_window_on_an_unwritable_folder_closes_without_a_word(qtbot, unwritable_config):
    window = create_window(unwritable_config)
    qtbot.addWidget(window)
    window.show()
    window.switchTo(window.settings_page)
    window.resize(760, 600)

    assert window.close()  # remembering the size fails: no error, and no message on a window that is going

    assert _bars(window.settings_page) == []
    assert window.config.save_failed


# -- Sobre ------------------------------------------------------------------------------


def test_about_page_texts(qtbot):
    page = AboutPage()
    qtbot.addWidget(page)
    assert isinstance(page, ScrollArea)
    assert page.objectName() == "aboutPage"
    assert isinstance(page.title_label, TitleLabel) and page.title_label.text() == "Sobre o Siftool"

    texts = [label.text() for label in page.findChildren(QLabel)]
    shown = display_version(siftool.__version__)  # "2.0.0 beta 1", as people read it (not PEP 440's "2.0.0b1")
    expected = [
        f"Versão {shown}",
        f"Motor de limpeza {shown}",  # spec 5.7: the program's and the engine's versions
        "Funciona 100% offline.",
        "Nunca altera o arquivo original.",
        "Nunca recomprime a imagem.",
        "Só dá o selo quando a verificação passa.",
        "Licença: GPLv3",
        "Feito com PySide6/Qt (LGPL) e QFluentWidgets (GPLv3).",
        "Fotos HEIC e vídeos chegam numa próxima versão.",
    ]
    for text in expected:
        assert texts.count(text) == 1, text
    positions = [texts.index(text) for text in expected]
    assert positions == sorted(positions)  # the promises come one per line, in the spec's order


def test_about_page_shows_the_version_the_way_people_read_it(qtbot):
    # Release prep: "2.0.0 beta 1", as the installer and Siftool.exe say it -- never PEP 440's "2.0.0b1".
    assert siftool.__version__ == "2.0.0b1"  # this release (tests/test_package.py pins it too)
    page = AboutPage()
    qtbot.addWidget(page)

    assert page.version_label.text() == "Versão 2.0.0 beta 1"
    assert page.engine_label.text() == "Motor de limpeza 2.0.0 beta 1"
    assert not [label.text() for label in page.findChildren(QLabel) if "2.0.0b1" in label.text()]


# -- window: pages, theme and geometry ------------------------------------------------------


def test_window_hosts_the_real_pages_with_its_config(qtbot, tmp_path):
    window = create_window(tmp_path / "cfg.json")
    qtbot.addWidget(window)

    assert isinstance(window.settings_page, SettingsPage)
    assert isinstance(window.about_page, AboutPage)
    _choose(window.settings_page.mode_card, "Fotógrafo")
    assert default_mode(window.config) is Mode.PHOTOGRAPHER


def test_create_window_applies_the_saved_theme(qtbot, tmp_path, theme_guard):
    dark = tmp_path / "dark.json"
    dark.write_text(json.dumps({"QFluentWidgets": {"ThemeMode": "Dark"}}), encoding="utf-8")
    with qtbot.waitSignal(qconfig.themeChangedFinished, timeout=5000):  # applied with `setTheme()`, on purpose
        window = create_window(dark)
    qtbot.addWidget(window)
    assert isDarkTheme()
    assert window.settings_page.theme_card.choiceLabel.text() == "Escuro"

    light = tmp_path / "light.json"
    light.write_text(json.dumps({"QFluentWidgets": {"ThemeMode": "Light"}}), encoding="utf-8")
    qtbot.addWidget(create_window(light))
    assert not isDarkTheme()


def test_window_geometry_round_trip(qtbot, tmp_path):
    path = tmp_path / "cfg.json"
    first = create_window(path)
    qtbot.addWidget(first)
    first.resize(760, 600)
    assert first.close()  # closing remembers the size

    second = create_window(path)
    qtbot.addWidget(second)
    assert second.size() == QSize(760, 600)


def test_window_without_a_usable_saved_geometry_gets_the_default_size(qtbot, tmp_path):
    fresh = create_window(tmp_path / "fresh.json")
    qtbot.addWidget(fresh)
    available = fresh.screen().availableGeometry()
    # The default size, shrunk to leave some room on a small screen, never below the minimum.
    expected = DEFAULT_SIZE.boundedTo(available.size() - QSize(80, 80)).expandedTo(MINIMUM_SIZE)
    assert fresh.size() == expected
    assert MINIMUM_SIZE == QSize(720, 560) and fresh.minimumSize() == MINIMUM_SIZE
    off_center = fresh.geometry().center() - available.center()  # and centered on its screen
    assert abs(off_center.x()) <= 4 and abs(off_center.y()) <= 4

    broken = tmp_path / "broken.json"
    broken.write_text(json.dumps({"Window": {"Geometry": "not a geometry"}}), encoding="utf-8")
    window = create_window(broken)
    qtbot.addWidget(window)
    assert window.size() == fresh.size()


def test_main_hands_qt_the_program_name_first(monkeypatch, qapp, tmp_path):
    seen: dict = {}

    class FakeSignal:
        def connect(self, slot) -> None:
            pass

    class FakeApplication:
        instance = staticmethod(QApplication.instance)  # pytest-qt asks for it at teardown

        def __init__(self, argv):
            seen["argv"] = list(argv)
            self.lastWindowClosed = FakeSignal()

        def setWindowIcon(self, icon) -> None:
            seen["icon"] = not icon.isNull()

        def exec(self) -> int:
            return 7

    class FakeWindow:
        def show(self) -> None:
            seen["shown"] = True

        def open_paths(self, paths) -> None:
            seen["paths"] = paths

    monkeypatch.setattr("PySide6.QtWidgets.QApplication", FakeApplication)
    monkeypatch.setattr(app_module, "create_window", lambda config_path=None: FakeWindow())
    monkeypatch.setattr(sys, "argv", ["siftool-gui", "left over"])
    # Settings, and so the single-window lock and server name, of its own: a Siftool open here stays out of it.
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))

    assert app_module.main(["-style", "fusion"]) == 7

    # Qt reads argv[0] as the program name: without it, "-style" would be swallowed as the name.
    assert seen == {"argv": ["siftool-gui", "-style", "fusion"], "icon": True, "shown": True}
    assert not list(tmp_path.glob("*.lock"))  # the first window's place is given back on the way out


def test_long_folder_path_is_cut_to_fit_and_whole_in_the_tooltip(settings_page, config, qtbot):
    page = settings_page
    config.set(config.saveLocation, "folder")
    page.resize(720, 560)
    page.show()
    qtbot.waitExposed(page)
    long_folder = "/".join(["C:", "Users", "Floriani", "OneDrive", "Documentos",
                            *["Fotos de família, viagens e trabalho"] * 3, "limpas"]).replace("/", os.sep)

    config.set(config.outputFolder, long_folder)

    card = page.folder_card
    shown = card.contentLabel.text()
    assert shown != long_folder and "…" in shown
    assert shown.startswith("C:") and shown.endswith("limpas")  # cut in the middle: both ends stay
    assert card.toolTip() == long_folder  # spec 6: the whole text in the tooltip
    text_right = card.contentLabel.mapTo(card, card.contentLabel.rect().topRight()).x()
    assert text_right < card.button.x()  # and it no longer runs under the button

    short_folder = os.path.join("C:", os.sep, "Fotos limpas")
    config.set(config.outputFolder, short_folder)
    assert card.contentLabel.text() == short_folder


def test_real_start_shows_the_window_and_keeps_its_settings_under_appdata(tmp_path):
    # The true entry point, in its own process (off-screen): `main()` builds the QApplication,
    # shows the window and, on close, writes the settings where APPDATA says -- here a temp folder.
    code = textwrap.dedent(
        """
        import sys
        from PySide6.QtWidgets import QApplication
        from siftool_gui.app import main
        from siftool_gui.window import MainWindow

        def run_briefly():
            app = QApplication.instance()
            app.processEvents()
            (window,) = [w for w in app.topLevelWidgets() if isinstance(w, MainWindow)]
            print("visible:", window.isVisible(), "| title:", window.windowTitle(), "| qt args:", app.arguments()[1:])
            window.close()
            return 0

        QApplication.exec = staticmethod(run_briefly)
        sys.argv = ["siftool-gui"]
        print("exit:", main(["-style", "fusion"]))
        """
    )
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", APPDATA=str(tmp_path), TEMP=str(tmp_path),
               TMP=str(tmp_path), PYTHONPATH=str(Path(__file__).parents[2] / "src"))  # its window lock there too

    result = subprocess.run([sys.executable, "-P", "-c", code], cwd=tmp_path, env=env, capture_output=True,
                            text=True, timeout=120)

    assert result.returncode == 0, result.stderr
    assert "visible: True | title: Siftool | qt args: []" in result.stdout  # Qt took "-style fusion" for itself
    assert "exit: 0" in result.stdout
    saved = json.loads((tmp_path / "Siftool" / "config.json").read_text(encoding="utf-8"))
    assert saved["Window"]["Geometry"]  # the size was remembered on close
