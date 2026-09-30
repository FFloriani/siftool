"""`MainWindow`: the application shell (spec sections 4.3 and 5.1).

Three navigation items on a `FluentWindow`: "Limpar" (top) and
"Configurações"/"Sobre" (bottom). The pages themselves live in
`siftool_gui.pages`; this module wires them into the shell, owns the
`JobRunner` the "Limpar" page works with, and remembers the window's size
between runs (`SiftoolConfig.windowGeometry`).
"""

from __future__ import annotations

from PySide6.QtCore import QByteArray, QSize
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QWidget
from qfluentwidgets import FluentIcon, FluentWindow, NavigationItemPosition

from siftool_gui.pages.about_page import AboutPage
from siftool_gui.pages.clean_page import CleanPage
from siftool_gui.pages.settings_page import SettingsPage
from siftool_gui.settings import SiftoolConfig
from siftool_gui.workers import JobRunner

MINIMUM_SIZE = QSize(720, 560)
DEFAULT_SIZE = QSize(1100, 760)  # room for the 960 px column beside the navigation rail
CLOSE_WAIT_MS = 10_000  # how long closing waits for copies already being written


class MainWindow(FluentWindow):
    """The Siftool GUI's main window: the navigation shell over its three pages."""

    def __init__(self, config: SiftoolConfig, runner: JobRunner | None = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.config = config
        self.runner = runner if runner is not None else JobRunner(self)

        self.clean_page = CleanPage(config, self.runner, self)
        self.settings_page = SettingsPage(config, self)
        self.about_page = AboutPage(self)

        self.setWindowTitle("Siftool")
        self.setMinimumSize(MINIMUM_SIZE)

        self.addSubInterface(self.clean_page, FluentIcon.BROOM, "Limpar")
        self.addSubInterface(
            self.settings_page, FluentIcon.SETTING, "Configurações", NavigationItemPosition.BOTTOM
        )
        self.addSubInterface(self.about_page, FluentIcon.INFO, "Sobre", NavigationItemPosition.BOTTOM)

        self._restore_geometry()

    # -- size ---------------------------------------------------------------------------

    def _restore_geometry(self) -> None:
        """The size (and place) the window had when it was last closed; a default on first run."""
        saved = self.config.get(self.config.windowGeometry)
        if saved:
            try:
                geometry = QByteArray.fromBase64(saved.encode("ascii"))
            except (AttributeError, UnicodeError):  # not the text `_save_geometry` wrote
                geometry = QByteArray()
            if not geometry.isEmpty() and self.restoreGeometry(geometry):
                return
        self._use_default_geometry()

    def _use_default_geometry(self) -> None:
        screen = self.screen()
        if screen is None:
            self.resize(DEFAULT_SIZE)
            return
        available = screen.availableGeometry()
        size = DEFAULT_SIZE.boundedTo(available.size() - QSize(80, 80)).expandedTo(MINIMUM_SIZE)
        self.resize(size)
        self.move(available.center().x() - size.width() // 2, available.center().y() - size.height() // 2)

    def _save_geometry(self) -> None:
        geometry = bytes(self.saveGeometry().toBase64()).decode("ascii")
        try:
            self.config.set(self.config.windowGeometry, geometry)
        except OSError:  # an unwritable settings file must not keep the window open
            pass

    # -- closing ---------------------------------------------------------------------------

    def closeEvent(self, event: QCloseEvent) -> None:
        """Closing mid-clean: drop the queue, let running copies finish, then remember the size.

        Review focus 4: queued jobs are dropped, a copy being written is
        finished (and verified) rather than cut short -- the engine writes
        atomically, so nothing half-written stays behind -- and the wait is
        bounded, so the window never hangs.
        """
        if self.clean_page.busy():
            self.clean_page.cancel()  # empties the page's own queues, then `runner.cancel()`
            self.runner.wait_for_done(CLOSE_WAIT_MS)
        self._save_geometry()
        super().closeEvent(event)
