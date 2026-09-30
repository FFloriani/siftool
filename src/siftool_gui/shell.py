"""Showing files and folders in the system's file manager (spec 4.4, item 5, and 5.2).

"Mostrar no Explorer" selects a file in a new Explorer window; "Abrir
pasta" opens a folder. Qt-free at import time: Qt is only imported, inside
the functions, on systems other than Windows.

Explorer's `/select,` switch reads its argument up to the first comma
unless the path is quoted -- and real photo names carry commas
("26 de set. de 2026, 03_30_37.png") -- so the whole path goes between
double quotes. A Windows path cannot contain a double quote, so the
quoting cannot be broken out of.

Explorer itself is started by its full path, from `%SystemRoot%`: a bare
`explorer` makes Windows search the current folder before the system
ones, and a program that opens the folders of other people's photos is
just where a planted `explorer.exe` would sit.
"""

from __future__ import annotations

import ntpath
import os
import subprocess
from pathlib import Path


def explorer_executable() -> str:
    """The full path of Explorer, `%SystemRoot%\\explorer.exe` (`C:\\Windows` when the variable is missing)."""
    return ntpath.join(os.environ.get("SystemRoot", r"C:\Windows"), "explorer.exe")


def explorer_select_command(path: Path) -> str:
    """`"<Explorer's full path>" /select,"<absolute path>"`: both quoted (commas and spaces included)."""
    return f'"{explorer_executable()}" /select,"{ntpath.abspath(str(path))}"'


def reveal_in_explorer(path: Path) -> None:
    """Show `path` selected in the file manager (on other systems: open its folder)."""
    if os.name == "nt":
        # A command *string*: Explorer parses `/select,"..."` itself, CreateProcess must not re-quote it.
        subprocess.Popen(explorer_select_command(path))
        return
    _open_url(Path(path).parent)


def open_folder(path: Path) -> None:
    """Open the folder `path` in the file manager."""
    if os.name == "nt":
        os.startfile(str(path))  # noqa: S606 -- a folder the user chose or wrote to
        return
    _open_url(Path(path))


def _open_url(folder: Path) -> None:
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QDesktopServices

    QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))
