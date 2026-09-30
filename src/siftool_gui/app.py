"""`main()`, `create_window()`, `open_window()`: the program's entry point (spec section 4.3; distribution spec 2).

`main` reads its command line with `parse_args`:

- `--version` answers without creating a `QApplication` or importing Qt at
  all, so it stays fast and works without a display. It prints the version
  and, with `--version-file <file>`, writes it there too: the frozen
  program is windowed, with no console to print to.
- `--self-test <result.json>` is the hidden check the build runs on the
  frozen program (`siftool_gui.self_test`).
- Qt's own options (`-style fusion`, `-platform offscreen`...) go to Qt.
- Everything else is a file or folder to add to the list: that is how
  "Limpar com Siftool", "Abrir com" and a drop on `Siftool.exe` hand them over.

Otherwise it creates the `QApplication` and -- one window per settings
file (`siftool_gui.single_instance`) -- either opens the window, which gets
the paths once it shows (`open_window`) and later those of every other
launch, or hands the paths to the window already open and exits. With no
temporary folder to keep the lock in, every launch simply opens its own window.
`create_window` builds the window after applying the saved theme.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from siftool_gui import __version__

if TYPE_CHECKING:
    from siftool_gui.window import MainWindow

# Qt's own command-line options that take a value (QGuiApplication and QApplication); Qt reads
# "--option" as "-option" too.
_QT_OPTIONS_WITH_VALUE = frozenset({
    "-platform", "-platformpluginpath", "-platformtheme", "-plugin", "-qmljsdebugger", "-qwindowgeometry",
    "-qwindowicon", "-qwindowtitle", "-session", "-style", "-stylesheet", "-display", "-geometry", "-title", "-name",
})
_OPTIONS_WITH_VALUE = ("--version-file", "--self-test")


@dataclass(frozen=True)
class Launch:
    """What one command line asks for (see `parse_args`)."""

    paths: tuple[str, ...] = ()  # absolute: the files and folders to add to the list
    qt_args: tuple[str, ...] = ()  # Qt's own options, for the QApplication
    version: bool = False
    version_file: str | None = None
    self_test: str | None = None  # where to write the self-test's result


def parse_args(args: Sequence[str]) -> Launch:
    """Read a command line, without the program's name (see the module docstring).

    `--version-file <file>` implies `--version`; `--option=value` works for
    both options that take a file, and one given no file is a `ValueError`.
    An argument starting with "-" is otherwise Qt's, passed on with its
    value when it takes one. The rest -- and everything after `--` -- are
    paths, made absolute against the current folder: a second launch hands
    them to a window that was started elsewhere.
    """
    paths: list[str] = []
    qt_args: list[str] = []
    files: dict[str, str] = {}
    version = False
    rest = iter(args)
    for arg in rest:
        if arg == "--":
            paths.extend(rest)
            break
        name, has_value, value = arg.partition("=")
        if name in _OPTIONS_WITH_VALUE:
            if not has_value:
                value = next(rest, "")
            if not value:
                raise ValueError(f"{name} precisa de um arquivo")
            files[name] = value
        elif arg == "--version":
            version = True
        elif arg.startswith("-"):
            qt_args.append(arg)
            if not has_value and "-" + name.lstrip("-") in _QT_OPTIONS_WITH_VALUE:
                value = next(rest, None)
                if value is not None:
                    qt_args.append(value)
        else:
            paths.append(arg)
    version_file = files.get("--version-file")
    return Launch(
        paths=tuple(os.path.abspath(path) for path in paths),
        qt_args=tuple(qt_args),
        version=version or version_file is not None,
        version_file=version_file,
        self_test=files.get("--self-test"),
    )


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


def open_window(paths: Sequence[str] = (), config_path: Path | None = None) -> "MainWindow":
    """Create the main window and show it; then hand it `paths`, the command line's files and folders."""
    window = create_window(config_path)
    window.show()
    if paths:
        window.open_paths(paths)
    return window


def write_version(version_file: str | None = None) -> int:
    """`--version`: print `siftool-gui <version>`, and write the same line to `version_file` when given.

    `print` does nothing in the windowed program, where `sys.stdout` is
    None: the file is how the build reads the frozen program's version.
    Returns 1 when the file cannot be written.
    """
    line = f"siftool-gui {__version__}"
    print(line)
    if version_file is not None:
        try:
            Path(version_file).write_text(line + "\n", encoding="utf-8")
        except OSError:
            return 1
    return 0


def run(launch: Launch) -> int:
    """Open the window -- or hand `launch.paths` to the one already open -- and run until it closes."""
    from PySide6.QtWidgets import QApplication

    from siftool_gui.single_instance import SingleInstance
    from siftool_gui.window import app_icon

    # Qt takes argv[0] as the program name and parses its own options (-style, -platform...)
    # from the rest: without argv[0] the first real argument would be swallowed as the name.
    app = QApplication([sys.argv[0], *launch.qt_args])
    app.setWindowIcon(app_icon())
    instance = SingleInstance()
    if not instance.claim() and instance.another_is_first() and instance.hand_over(launch.paths):
        return 0  # the window already open has the paths, and comes forward
    # First -- or the first never answered: better a window of our own than paths lost.
    window = open_window(launch.paths)
    if instance.claim():
        instance.pathsReceived.connect(window.open_paths)
        app.lastWindowClosed.connect(instance.release)  # from then on a launch opens a window of its own
        if not instance.listen():
            instance.release()  # first but deaf: the next launches must not wait for this window
    try:
        return app.exec()
    finally:
        instance.release()


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point of `siftool-gui`, `python -m siftool_gui` and the frozen `Siftool.exe`.

    Returns the exit code: 0; 1 when `--version-file` cannot be written or
    the self-test fails; 2 for a command line it refuses (said on stderr,
    when there is one).
    """
    try:
        launch = parse_args(sys.argv[1:] if argv is None else argv)
    except ValueError as exc:
        print(f"siftool-gui: {exc}", file=sys.stderr)  # without a console (sys.stderr None), nothing is printed
        return 2
    if launch.version:
        return write_version(launch.version_file)
    if launch.self_test is not None:
        from siftool_gui.self_test import run_self_test

        return run_self_test(Path(launch.self_test))
    return run(launch)
