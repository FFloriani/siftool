"""Persistent GUI preferences (spec sections 4.3, 5.6 and 7).

Stored via QFluentWidgets' `QConfig` machinery, at
`%APPDATA%\\Siftool\\config.json` by default (or an explicit path, e.g.
in tests): the default cleaning mode, where to save copies and the
theme. No file list, no telemetry (spec section 7).
"""

from __future__ import annotations

import os
from pathlib import Path

from qfluentwidgets import (
    ConfigItem,
    OptionsConfigItem,
    OptionsValidator,
    QConfig,
    Theme,
    qconfig,
)

from siftool.model import Mode


class SiftoolConfig(QConfig):
    """The GUI's persistent settings (spec section 5.6).

    The theme is QFluentWidgets' own `QConfig.themeMode` item, inherited on
    purpose, never redefined (ruling P8): `setTheme()` and `qconfig.set()`
    only switch the theme when the item they are given *is*
    `qconfig._cfg.themeMode`, and `setTheme()` always passes
    `QConfig.themeMode`. Its library default is `Theme.LIGHT`; `load_config`
    makes it `Theme.AUTO` (spec 5.1) before reading the file.
    """

    defaultMode = OptionsConfigItem(
        "Cleaning", "DefaultMode", "privacy", OptionsValidator(["privacy", "photographer", "maximum"])
    )
    saveLocation = OptionsConfigItem("Cleaning", "SaveLocation", "beside", OptionsValidator(["beside", "folder"]))
    outputFolder = ConfigItem("Cleaning", "OutputFolder", "")
    windowGeometry = ConfigItem("Window", "Geometry", "")  # base64 of QWidget.saveGeometry()


def default_config_path() -> Path:
    """Where preferences live when the caller doesn't pick a path."""
    return Path(os.environ.get("APPDATA") or Path.home()) / "Siftool" / "config.json"


def load_config(path: Path | None = None) -> SiftoolConfig:
    """Build a `SiftoolConfig`, load `path` into it, and bind it as the global config.

    In the installed QFluentWidgets 1.11.3, every `ConfigItem`
    (`defaultMode`, `saveLocation`, ...) is defined as a *class*
    attribute of `SiftoolConfig` -- exactly like `QConfig.themeMode`
    itself -- so it is one object shared by every instance of the class,
    not a fresh copy per `SiftoolConfig()`. `QConfig.load(file, config)`
    (`qfluentwidgets/common/config.py`) reads `file` and, for each key it
    finds, calls that shared item's `deserializeFrom` -- but it never
    resets an item the file doesn't mention. So without the reset below,
    a value `.set()` by an *earlier* `load_config()` call in the same
    process (an earlier test, or a previous call here with a different
    `path`) would leak into what should be a fresh load -- `path` having
    no such key is not the same as `path` saying "use the default".
    Every item is therefore reset to its `defaultValue` first -- and then
    the inherited `themeMode` to `Theme.AUTO`, the GUI's default (its
    library default is `Theme.LIGHT`; see `SiftoolConfig`).

    `qconfig` itself (the module-level singleton most QFluentWidgets
    widgets read `theme`/`themeChanged` from) is a plain `QConfig`, with
    no `defaultMode` of its own -- calling `qconfig.load(str(path), cfg)`
    does not copy our items onto it. What it does instead: reassign
    `qconfig`'s internal `_cfg` pointer to our `cfg` (so `qconfig.get`/
    `.set`/`.save()` called *on `qconfig`* afterwards act on `cfg`'s file
    and items) and connect `cfg.themeChanged` to `qconfig.themeChanged`
    (so theme-aware widgets built against the global `qconfig` still
    react when `cfg`'s theme changes) -- then finally read `path`'s JSON,
    if any, into `cfg`'s items. Callers should keep using the returned
    `cfg` (`cfg.get(...)`/`cfg.set(...)`) for our own settings; `qconfig`
    stays the right handle only for theme plumbing shared with
    QFluentWidgets' own components.
    """
    cfg = SiftoolConfig()
    for name in dir(SiftoolConfig):
        item = getattr(SiftoolConfig, name)
        if isinstance(item, ConfigItem):
            item.value = item.defaultValue
    cfg.themeMode.value = Theme.AUTO

    qconfig.load(str(path or default_config_path()), cfg)
    return cfg


def default_mode(cfg: SiftoolConfig) -> Mode:
    """The configured default cleaning `Mode`."""
    return Mode(cfg.get(cfg.defaultMode))


def output_dir(cfg: SiftoolConfig) -> Path | None:
    """The configured output folder, or `None` for "beside the original" / unset."""
    if cfg.get(cfg.saveLocation) != "folder":
        return None
    folder = cfg.get(cfg.outputFolder)
    return Path(folder) if folder else None
