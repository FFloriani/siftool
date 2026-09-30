"""`Siftool.exe --self-test <result.json>`: the check the build runs on the frozen program (distribution spec 2).

It proves, without showing anything (Qt's offscreen platform), that what
the program needs made it into the bundle:

- the Qt plugins: the image formats of the previews and of the window icon
  -- a tiny JPEG and a tiny WEBP are decoded, so a plugin that is missing,
  or listed but unable to decode, fails the build -- and the Windows
  platform plugin (loaded, though not the one in use here);
- what keeps Siftool to one window: its lock takes and its local server
  (QtNetwork) listens, under a name made with hashlib;
- QFluentWidgets and its resources: the main window is built and drawn,
  with its icon, and a Fluent icon draws something;
- the engine and the workers: a tiny generated PNG carrying a personal
  text goes through the "Limpar" page -- analysis, preview, clean -- and
  its copy comes out verified, without the text;
- where this copy keeps its settings (`config`), and whether it is the
  portable one (`portable`).

The window works on a throwaway settings file, in a temporary folder with
the photo and its copy, all removed afterwards: the user's settings are
never read or written -- except in the portable copy, whose settings file,
in its own folder, is loaded and saved back as it is. That is what proves a
portable copy keeps its settings inside itself.

Writes `{"ok": true, "version", "portable", "config"}` to `result.json` and
returns 0; on any failure `{"ok": false, ..., "error", "traceback"}` and 1.
"""

from __future__ import annotations

import base64
import json
import os
import struct
import sys
import tempfile
import traceback
import zlib
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from siftool_gui import __version__

if TYPE_CHECKING:
    from PySide6.QtGui import QImage
    from PySide6.QtWidgets import QApplication

    from siftool_gui.pages.clean_page import CleanPage
    from siftool_gui.window import MainWindow

STEP_TIMEOUT_MS = 60_000  # the analysis, then the clean; generous for a cold start of the frozen program
PERSONAL_TEXT = b"Siftool self-test"  # what the clean must take out of the photo
IMAGE_FORMATS = ("png", "jpeg", "webp", "ico")  # the previews of every photo format, and the window icon

# Tiny photos of the formats the previews decode with Qt's plugins (PNG is built into Qt; the window icon, an .ico,
# is checked with the window): 8x8, one solid color, made with Pillow -- JPEG quality 75 with optimized tables, WEBP
# lossy quality 75. A listed plugin that cannot decode them, or whose library did not make it into the bundle, fails
# the self-test instead of a user's preview.
TINY_SIZE = 8
TINY_COLOR = (200, 40, 40)  # RGB; JPEG and WEBP are lossy, so it decodes to within _COLOR_TOLERANCE of this
_COLOR_TOLERANCE = 40
TINY_JPEG = base64.b64decode(
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRof"
    "Hh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/2wBDAQkJCQwLDBgNDRgyIRwh"
    "MjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjL/wAAR"
    "CAAIAAgDASIAAhEBAxEB/8QAFQABAQAAAAAAAAAAAAAAAAAAAAb/xAAUEAEAAAAAAAAAAAAA"
    "AAAAAAAA/8QAFQEBAQAAAAAAAAAAAAAAAAAABQf/xAAUEQEAAAAAAAAAAAAAAAAAAAAA/9oA"
    "DAMBAAIRAxEAPwCXADqO/9k="
)
TINY_WEBP = base64.b64decode(
    "UklGRjgAAABXRUJQVlA4ICwAAACQAQCdASoIAAgAAgA0JaACdLoAA5gA/u921+N5vNbOP/7B"
    "3/6Dv/0Hf7JAAA=="
)


class SelfTestError(RuntimeError):
    """One of the self-test's checks failed."""


def run_self_test(result_path: Path) -> int:
    """Run every check and write the report to `result_path` (see the module docstring); the exit code."""
    os.environ["QT_QPA_PLATFORM"] = "offscreen"  # before any QApplication: nothing is ever on a screen
    report: dict[str, object] = {"version": __version__}
    try:
        report = {"ok": True, **report, **_check_everything()}
        code = 0
    except Exception as exc:  # every failure belongs in the report
        report = {"ok": False, **report, "error": f"{type(exc).__name__}: {exc}", "traceback": traceback.format_exc()}
        code = 1
    try:
        Path(result_path).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except OSError:
        return 1
    return code


def _check_everything() -> dict[str, object]:
    from PySide6.QtWidgets import QApplication

    from siftool_gui import app as gui_app  # its create_window, looked up now (a test replaces it)
    from siftool_gui.settings import default_config_path, load_config, portable_dir

    qt_app = QApplication.instance() or QApplication([sys.argv[0]])
    _check_image_formats()
    _check_platform_plugin()

    config = default_config_path()
    portable = portable_dir() is not None
    if portable:
        _check_portable_settings(config)

    with tempfile.TemporaryDirectory(prefix="siftool-self-test-") as folder:
        throwaway = Path(folder) / "config.json"
        _check_single_instance(throwaway, Path(folder))
        window = gui_app.create_window(throwaway)
        try:
            _check_window(qt_app, window)
            _check_cleaning(window.clean_page, Path(folder))
        finally:
            window.close()  # waits for any copy still being written; saves the size in the throwaway settings
    return {"portable": portable, "config": str(config)}


def _check_portable_settings(config: Path) -> None:
    """The portable copy keeps its settings inside itself: `config`, in its own folder, is written as it is.

    `SiftoolConfig.save` does not raise on a folder that cannot be written, so the check asks it: a file left
    by an earlier run would otherwise make a copy on read-only media pass.
    """
    from siftool_gui.settings import load_config

    cfg = load_config(config)
    cfg.save()  # as they are: this only proves where a portable copy keeps them, and that it can write there
    if cfg.save_failed or not config.is_file():
        raise SelfTestError(f"the portable settings were not written: {config}")


def _check_image_formats() -> None:
    from PySide6.QtGui import QImageReader

    available = {name.data().decode("ascii", "replace").lower() for name in QImageReader.supportedImageFormats()}
    missing = [name for name in IMAGE_FORMATS if name not in available]
    if missing:
        raise SelfTestError(f"image formats missing: {', '.join(missing)}")
    _check_decodes("jpeg", TINY_JPEG)
    _check_decodes("webp", TINY_WEBP)


def _check_decodes(name: str, data: bytes) -> None:
    """Qt decodes `data`, a tiny photo of the format `name`: its size and color come out as they went in."""
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice
    from PySide6.QtGui import QImageReader

    buffer = QBuffer()  # kept alive, beside the reader that reads from it
    buffer.setData(QByteArray(data))
    buffer.open(QIODevice.OpenModeFlag.ReadOnly)
    reader = QImageReader(buffer)
    image = reader.read()
    if image.isNull():
        raise SelfTestError(f"a {name} photo did not decode: {reader.errorString()}")
    if (image.width(), image.height()) != (TINY_SIZE, TINY_SIZE):
        raise SelfTestError(f"a {name} photo decoded to {image.width()}x{image.height()}, not {TINY_SIZE}x{TINY_SIZE}")
    pixel = image.pixelColor(TINY_SIZE // 2, TINY_SIZE // 2)
    color = (pixel.red(), pixel.green(), pixel.blue())
    if any(abs(got - want) > _COLOR_TOLERANCE for got, want in zip(color, TINY_COLOR)):
        raise SelfTestError(f"a {name} photo decoded to the wrong color {color}, not about {TINY_COLOR}")


def _check_platform_plugin() -> None:
    """On Windows, the platform plugin the real window uses loads (this check itself runs offscreen)."""
    if sys.platform != "win32":
        return
    from PySide6.QtCore import QCoreApplication, QPluginLoader

    for folder in QCoreApplication.libraryPaths():
        plugin = Path(folder) / "platforms" / "qwindows.dll"
        if plugin.is_file():
            loader = QPluginLoader(str(plugin))
            if not loader.load():
                raise SelfTestError(f"the Windows platform plugin does not load: {loader.errorString()}")
            return
    raise SelfTestError("the Windows platform plugin (platforms/qwindows.dll) is missing")


def _check_single_instance(config: Path, folder: Path) -> None:
    """The one-window machinery, for the throwaway settings `config` (a name no real window uses), lock in `folder`."""
    from siftool_gui.single_instance import SingleInstance

    instance = SingleInstance(config, lock_dir=folder)
    try:
        if not instance.claim():
            raise SelfTestError("the single-window lock cannot be taken")
        if not instance.listen():
            raise SelfTestError("the single-window server does not listen")
    finally:
        instance.release()


def _check_window(qt_app: "QApplication", window: "MainWindow") -> None:
    from qfluentwidgets import FluentIcon

    window.show()  # offscreen: drawn, never on a screen
    qt_app.processEvents()
    if window.windowIcon().pixmap(32, 32).isNull():
        raise SelfTestError("the window icon did not load")
    if window.grab().isNull():
        raise SelfTestError("the window did not draw")
    if not _has_ink(FluentIcon.BROOM.icon().pixmap(32, 32).toImage()):
        raise SelfTestError("a Fluent icon draws nothing")


def _has_ink(image: "QImage") -> bool:
    return any(image.pixelColor(x, y).alpha() for x in range(image.width()) for y in range(image.height()))


def _check_cleaning(page: "CleanPage", folder: Path) -> None:
    from siftool_gui.entries import EntryState
    from siftool_gui.model import THUMBNAIL_ROLE

    photo = folder / "foto.png"
    photo.write_bytes(_photo_with_personal_text())
    page.add_paths([str(photo)])
    _wait_until(lambda: page.model.summary().cleanable == 1 and not page.busy(), "the photo was not analyzed")
    (entry_id,) = page.model.entry_ids()
    if page.model.index_for(entry_id).data(THUMBNAIL_ROLE) is None:
        raise SelfTestError("the photo got no preview")

    finished: list[tuple[int, int, int]] = []
    page.batchFinished.connect(finished.append)
    page.start_cleaning()
    _wait_until(lambda: bool(finished) and not page.busy(), "the photo was not cleaned")
    entry = page.model.entry(entry_id)
    if finished != [(1, 0, 0)] or entry.state is not EntryState.VERIFIED:
        raise SelfTestError(f"the clean ended {entry.state.name}; (verified, warnings, failed) = {finished}")
    if PERSONAL_TEXT in Path(entry.result.dst).read_bytes():
        raise SelfTestError("the personal text survived the clean")


def _wait_until(condition: Callable[[], bool], failure: str) -> None:
    """Run the event loop until `condition()` holds; `SelfTestError(failure)` after `STEP_TIMEOUT_MS`."""
    from PySide6.QtCore import QEventLoop, QTimer

    if condition():
        return
    loop = QEventLoop()
    check = QTimer()
    check.setInterval(20)
    check.timeout.connect(lambda: condition() and loop.quit())
    give_up = QTimer()
    give_up.setSingleShot(True)
    give_up.timeout.connect(loop.quit)
    check.start()
    give_up.start(STEP_TIMEOUT_MS)
    loop.exec()
    check.stop()
    give_up.stop()
    if not condition():
        raise SelfTestError(failure)


def _photo_with_personal_text() -> bytes:
    """A 16x16 RGB PNG with a personal text chunk (tEXt "Author"), which every mode removes."""
    size = 16
    rows = b"".join(b"\x00" + bytes(value for x in range(size) for value in (x * 16, y * 16, 128)) for y in range(size))

    def chunk(kind: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(kind + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", crc)

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"tEXt", b"Author\x00" + PERSONAL_TEXT)
        + chunk(b"IEND", b"")
    )
