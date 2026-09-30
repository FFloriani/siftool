"""Tests for `siftool_gui.settings` (spec sections 5.6 and 7; Task 6).

Every test passes an explicit `tmp_path` config file -- never the real
`%APPDATA%\\Siftool\\config.json` -- per the delivery's test-layout rule.
"""

from __future__ import annotations

import json

from qfluentwidgets import Theme

from siftool.model import Mode

from siftool_gui.settings import default_mode, load_config, output_dir


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
