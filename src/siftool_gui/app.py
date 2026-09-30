"""`main()` / `create_window()`: the GUI's process entry point (spec section 4.3).

`main` handles `--version` without creating a `QApplication` or importing
Qt at all, so `siftool-gui --version` stays fast and works even without a
display. Any other invocation creates the `QApplication`, builds the
window via `create_window` -- which applies the saved theme first -- and
runs the event loop.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

from siftool_gui import __version__

if TYPE_CHECKING:
    from siftool_gui.window import MainWindow


def create_window(config_path: Path | None = None) -> "MainWindow":
    """Build a `MainWindow`, without starting the Qt event loop.

    Testable without `QApplication.exec()`. Loads the persistent
    configuration from `config_path` (default location when `None`,
    see `siftool_gui.settings.default_config_path`), binds it as the
    global `qconfig` and applies its theme, before the window (and the
    QFluentWidgets components it builds) is created: every widget is born
    in the saved theme, and the window is ready to be shown.
    """
    from qfluentwidgets import setTheme

    from siftool_gui.settings import load_config
    from siftool_gui.window import MainWindow

    config = load_config(config_path)
    # Lazy: nothing of this window exists yet, so there is nothing to restyle now; widgets
    # that do exist (another window, earlier tests) restyle themselves when next painted.
    setTheme(config.get(config.themeMode), lazy=True)
    return MainWindow(config)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for `siftool-gui` / `python -m siftool_gui`.

    `--version` prints `siftool-gui <version>` and returns 0 without
    creating a window. Otherwise creates the `QApplication`, the main
    window and runs the event loop, returning its exit code.
    """
    args = sys.argv[1:] if argv is None else argv
    if "--version" in args:
        print(f"siftool-gui {__version__}")
        return 0

    from PySide6.QtWidgets import QApplication

    # Qt takes argv[0] as the program name and parses its own options (-style, -platform...)
    # from the rest: without argv[0] the first real argument would be swallowed as the name.
    app = QApplication([sys.argv[0], *args])
    window = create_window()
    window.show()
    return app.exec()
