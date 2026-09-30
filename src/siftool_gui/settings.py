"""Persistent GUI preferences (spec sections 4.3, 5.6 and 7; distribution spec 2).

Stored via QFluentWidgets' `QConfig` machinery, at
`%APPDATA%\\Siftool\\config.json` by default -- or, in the portable copy,
inside its own folder, `<Siftool.exe's folder>\\dados\\config.json` (or an
explicit path, e.g. in tests): the default cleaning mode, where to save
copies and the theme. No file list, no telemetry (spec section 7).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from PySide6.QtCore import Signal
from qfluentwidgets import (
    ConfigItem,
    OptionsConfigItem,
    OptionsValidator,
    QConfig,
    Theme,
    qconfig,
)

from siftool.model import Mode

PORTABLE_MARKER = "Siftool.portable"  # beside Siftool.exe in the portable ZIP: settings stay in the folder
PORTABLE_DATA_FOLDER = "dados"


class SiftoolConfig(QConfig):
    """The GUI's persistent settings (spec section 5.6).

    The theme is QFluentWidgets' own `QConfig.themeMode` item, inherited on
    purpose, never redefined (ruling P8): `setTheme()` and `qconfig.set()`
    only switch the theme when the item they are given *is*
    `qconfig._cfg.themeMode`, and `setTheme()` always passes
    `QConfig.themeMode`. Its library default is `Theme.LIGHT`; `load_config`
    makes it `Theme.AUTO` (spec 5.1) before reading the file.

    A settings file that cannot be written -- the portable copy on
    read-only media, a full disk -- costs the program nothing but the
    saving: `save` does not raise, the change stays in memory and lasts
    until Siftool closes, `save_failed` says so and `saveFailed` announces
    it (the "Configurações" page shows the message).
    """

    saveFailed = Signal()  # the first failed save of a run of failures; a save that works again ends the run

    defaultMode = OptionsConfigItem(
        "Cleaning", "DefaultMode", "privacy", OptionsValidator(["privacy", "photographer", "maximum"])
    )
    saveLocation = OptionsConfigItem("Cleaning", "SaveLocation", "beside", OptionsValidator(["beside", "folder"]))
    outputFolder = ConfigItem("Cleaning", "OutputFolder", "")
    windowGeometry = ConfigItem("Window", "Geometry", "")  # base64 of QWidget.saveGeometry()

    def __init__(self) -> None:
        super().__init__()
        self._save_failed = False

    @property
    def save_failed(self) -> bool:
        """Whether the last save failed: the settings file cannot be written, so they live in memory only."""
        return self._save_failed

    def save(self) -> None:
        """`QConfig.save`, but an `OSError` (read-only folder or medium, full disk...) is not raised.

        `QConfig.set` has already put the new value in the item, and it saves
        *before* it announces a theme change: an error raised from here
        would leave the theme half switched -- the value set, the window
        not restyled. Only an `OSError` is an unwritable folder; anything
        else is a bug and still surfaces.
        """
        try:
            super().save()
        except OSError:
            if not self._save_failed:
                self._save_failed = True
                self.saveFailed.emit()
        else:
            self._save_failed = False


def portable_dir() -> Path | None:
    """The portable copy's folder -- the frozen program's own, with `PORTABLE_MARKER` beside it -- or None.

    Only the frozen program (PyInstaller sets `sys.frozen`) can be the
    portable copy: run from source, `sys.executable` is python.exe, and a
    marker beside it means nothing.
    """
    if not getattr(sys, "frozen", False):
        return None
    folder = Path(sys.executable).parent
    return folder if (folder / PORTABLE_MARKER).is_file() else None


def default_config_path() -> Path:
    """Where preferences live when the caller doesn't pick a path.

    The portable copy keeps them in its own folder (`dados\\config.json`
    beside `Siftool.exe`), so nothing of it is left on the computer;
    otherwise they live in `%APPDATA%\\Siftool\\config.json`.
    """
    portable = portable_dir()
    if portable is not None:
        return portable / PORTABLE_DATA_FOLDER / "config.json"
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
    # QFluentWidgets' own cards (and `setTheme(save=True)`) change a setting with `qconfig.set`, which saves
    # through `qconfig.save` -- the plain `QConfig`'s, not `cfg`'s. `qconfig` saves the very same file and items
    # (its `_cfg` is `cfg`), so it goes through `cfg.save`, which survives a file that cannot be written.
    qconfig.save = cfg.save
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
