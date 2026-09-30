"""Tests for `siftool_gui.settings` (spec sections 5.6 and 7; Task 6).

Every test passes an explicit `tmp_path` config file -- never the real
`%APPDATA%\\Siftool\\config.json` -- per the delivery's test-layout rule.
"""

from __future__ import annotations

import json

import pytest
from qfluentwidgets import QConfig, Theme, isDarkTheme, qconfig, setTheme

from siftool.model import Mode

from siftool_gui.settings import SiftoolConfig, default_mode, load_config, output_dir


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
