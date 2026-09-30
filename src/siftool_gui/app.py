"""`main()` / `create_window()`: the GUI's process entry point (spec section 4.3).

`main` handles `--version` without creating a `QApplication` or importing
Qt at all, so `siftool-gui --version` stays fast and works even without a
display. Any other invocation creates the `QApplication`, builds the
window via `create_window` and runs the event loop.
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
    see `siftool_gui.settings.default_config_path`) and binds it as the
    global `qconfig`, before the window (and the QFluentWidgets
    components it builds) can read the theme from it.
    """
    from siftool_gui.settings import load_config
    from siftool_gui.window import MainWindow

    load_config(config_path)
    return MainWindow()


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

    app = QApplication(args)
    window = create_window()
    window.show()
    return app.exec()
