"""Tests for `siftool_gui.settings` (spec sections 5.6 and 7; Task 6).

Every test passes an explicit `tmp_path` config file -- never the real
`%APPDATA%\\Siftool\\config.json` -- per the delivery's test-layout rule.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from qfluentwidgets import QConfig, Theme, isDarkTheme, qconfig, setTheme

from siftool.model import Mode

from siftool_gui.settings import (
    PORTABLE_MARKER,
    SiftoolConfig,
    default_config_path,
    default_mode,
    load_config,
    output_dir,
    portable_dir,
)


@pytest.fixture
def theme_guard():
    before = qconfig.get(qconfig.themeMode)
    yield
    setTheme(before)


def test_defaults_without_file(tmp_path):
    cfg = load_config(tmp_path / "does-not-exist.json")

    assert default_mode(cfg) is Mode.PRIVACY
    assert output_dir(cfg) is None
    assert cfg.get(cfg.themeMode) is Theme.AUTO


def test_values_persist_across_loads(tmp_path):
    path = tmp_path / "cfg.json"
    cfg = load_config(path)
    cfg.set(cfg.defaultMode, "maximum")
    assert path.exists()

    reloaded = load_config(path)
    assert default_mode(reloaded) is Mode.MAXIMUM


def test_invalid_values_fall_back_to_defaults(tmp_path):
    path = tmp_path / "cfg.json"
    path.write_text(json.dumps({"Cleaning": {"DefaultMode": "xyz"}}), encoding="utf-8")

    cfg = load_config(path)
    assert default_mode(cfg) is Mode.PRIVACY


def test_output_dir_rules(tmp_path):
    path = tmp_path / "cfg.json"
    cfg = load_config(path)
    assert output_dir(cfg) is None  # default save location: "beside"

    cfg.set(cfg.saveLocation, "folder")
    assert output_dir(cfg) is None  # "folder" chosen, but no path yet

    target = tmp_path / "saidas"
    cfg.set(cfg.outputFolder, str(target))
    assert output_dir(cfg) == target

    cfg.set(cfg.saveLocation, "beside")
    assert output_dir(cfg) is None  # "beside" wins even with a folder configured


def test_set_theme_still_switches_after_load_config(tmp_path, theme_guard):
    # Ruling P8: the config must keep QFluentWidgets' own `themeMode` item, or
    # `setTheme()` (which sets `qconfig.themeMode`) no longer reaches the theme.
    # Loading first makes this independent of which test ran before.
    load_config(tmp_path / "cfg.json")

    setTheme(Theme.DARK)
    assert isDarkTheme()
    setTheme(Theme.LIGHT)
    assert not isDarkTheme()


def test_theme_is_qfluentwidgets_item_and_persists(tmp_path, theme_guard):
    assert SiftoolConfig.themeMode is QConfig.themeMode

    path = tmp_path / "cfg.json"
    cfg = load_config(path)
    assert cfg.get(cfg.themeMode) is Theme.AUTO
    cfg.set(cfg.themeMode, Theme.DARK)
    assert isDarkTheme()

    reloaded = load_config(path)
    assert reloaded.get(reloaded.themeMode) is Theme.DARK
    assert load_config(tmp_path / "other.json").get(cfg.themeMode) is Theme.AUTO  # a fresh file: the default


# -- where the settings live (distribution spec 2: installed and portable copies) --------------------


def _frozen_program(monkeypatch, folder, *, portable: bool):
    """Pretend to be the frozen `Siftool.exe` in `folder` (PyInstaller sets `sys.frozen`); with the marker when `portable`."""
    folder.mkdir(parents=True, exist_ok=True)
    exe = folder / "Siftool.exe"
    exe.write_bytes(b"")
    if portable:
        (folder / PORTABLE_MARKER).write_text("", encoding="utf-8")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    return folder


def test_installed_program_keeps_its_settings_under_appdata(tmp_path, monkeypatch):
    _frozen_program(monkeypatch, tmp_path / "Programs" / "Siftool", portable=False)
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))

    assert portable_dir() is None
    assert default_config_path() == tmp_path / "appdata" / "Siftool" / "config.json"


def test_portable_copy_keeps_its_settings_in_its_own_folder(tmp_path, monkeypatch):
    folder = _frozen_program(monkeypatch, tmp_path / "pendrive" / "Siftool", portable=True)
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))

    assert portable_dir() == folder
    assert default_config_path() == folder / "dados" / "config.json"


def test_portable_copy_writes_its_settings_there(tmp_path, monkeypatch):
    folder = _frozen_program(monkeypatch, tmp_path / "Siftool", portable=True)
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))

    cfg = load_config()  # no path: where this copy keeps them
    cfg.set(cfg.defaultMode, "maximum")

    saved = json.loads((folder / "dados" / "config.json").read_text(encoding="utf-8"))
    assert saved["Cleaning"]["DefaultMode"] == "maximum"
    assert not (tmp_path / "appdata").exists()


def test_marker_only_counts_for_the_frozen_program(tmp_path, monkeypatch):
    # Run from source, `sys.executable` is python.exe: a marker next to it means nothing.
    python = tmp_path / "venv" / "Scripts"
    python.mkdir(parents=True)
    (python / PORTABLE_MARKER).write_text("", encoding="utf-8")
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setattr(sys, "executable", str(python / "python.exe"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))

    assert portable_dir() is None
    assert default_config_path() == tmp_path / "appdata" / "Siftool" / "config.json"


# -- a settings file that cannot be written (release prep: a portable copy on read-only media) ------------------


def test_a_failed_save_keeps_the_change_in_memory_and_says_so_once(unwritable_config):
    cfg = load_config(unwritable_config)
    said = []
    cfg.saveFailed.connect(lambda: said.append("not saved"))

    cfg.set(cfg.defaultMode, "maximum")  # no OSError
    cfg.set(cfg.saveLocation, "folder")
    cfg.set(cfg.outputFolder, "D:/copias")

    assert default_mode(cfg) is Mode.MAXIMUM and cfg.get(cfg.saveLocation) == "folder"
    assert output_dir(cfg) == Path("D:/copias")
    assert cfg.save_failed
    assert said == ["not saved"]  # one message for the whole run of failures, not one per setting


def test_the_library_path_that_saves_before_announcing_the_theme_still_switches_it(
    qtbot, unwritable_config, theme_guard
):
    # QFluentWidgets' own cards call `qconfig.set`, which saves BEFORE it announces a theme change: an OSError
    # there would stop the change half way -- the value set, the theme not.
    cfg = load_config(unwritable_config)

    with qtbot.waitSignal(qconfig.themeChanged, timeout=2000):
        qconfig.set(qconfig.themeMode, Theme.DARK)

    assert isDarkTheme()
    assert cfg.get(cfg.themeMode) is Theme.DARK and cfg.save_failed
    qconfig.set(cfg.defaultMode, "photographer")
    assert default_mode(cfg) is Mode.PHOTOGRAPHER


def test_set_theme_that_saves_survives_an_unwritable_file(unwritable_config, theme_guard):
    cfg = load_config(unwritable_config)

    setTheme(Theme.DARK, save=True)

    assert isDarkTheme() and cfg.get(cfg.themeMode) is Theme.DARK


def test_a_save_that_works_again_ends_the_failure_and_the_next_one_says_so_again(tmp_path, monkeypatch):
    path = tmp_path / "cfg.json"
    cfg = load_config(path)
    failing = [True]
    real_save = QConfig.save

    def save(self):
        if failing[0]:
            raise PermissionError(13, "Acesso negado")
        real_save(self)

    monkeypatch.setattr(QConfig, "save", save)  # the save itself raises, wherever the file is
    said = []
    cfg.saveFailed.connect(lambda: said.append("not saved"))

    cfg.set(cfg.defaultMode, "maximum")
    assert cfg.save_failed and said == ["not saved"]

    failing[0] = False  # the media is writable again
    cfg.set(cfg.defaultMode, "photographer")
    assert not cfg.save_failed
    assert json.loads(path.read_text(encoding="utf-8"))["Cleaning"]["DefaultMode"] == "photographer"

    failing[0] = True
    cfg.set(cfg.defaultMode, "privacy")
    assert said == ["not saved", "not saved"]


def test_only_an_oserror_is_a_save_that_cannot_happen(tmp_path, monkeypatch):
    # A read-only folder is not a bug: anything else still surfaces.
    cfg = load_config(tmp_path / "cfg.json")

    def broken(self):
        raise RuntimeError("a bug")

    monkeypatch.setattr(QConfig, "save", broken)

    with pytest.raises(RuntimeError, match="a bug"):
        cfg.set(cfg.defaultMode, "maximum")
    assert not cfg.save_failed


def test_writable_settings_never_report_a_failure(tmp_path):
    cfg = load_config(tmp_path / "cfg.json")
    said = []
    cfg.saveFailed.connect(lambda: said.append("not saved"))

    cfg.set(cfg.defaultMode, "maximum")

    assert said == [] and not cfg.save_failed
