"""`MainWindow`: the application shell (spec sections 4.3 and 5.1).

Three navigation items on a `FluentWindow`: "Limpar" (top) and
"Configurações"/"Sobre" (bottom). The pages themselves live in
`siftool_gui.pages`; this module wires them into the shell, owns the
`JobRunner` the "Limpar" page works with, and remembers the window's size
between runs (`SiftoolConfig.windowGeometry`).

"Seguir o Windows" (`Theme.AUTO`, the default) follows Windows' light/dark
setting live (ruling P11(d)): while it is the chosen theme, a
`ThemeListener` thread waits for Windows to switch and the window restyles
itself the way `setTheme` does. Choosing "Claro" or "Escuro" stops it;
choosing "Seguir o Windows" again starts a new one; closing the window (or
the application quitting) stops it for good.
"""

from __future__ import annotations

from PySide6.QtCore import QByteArray, QCoreApplication, QSize
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QWidget
from qfluentwidgets import FluentIcon, FluentWindow, NavigationItemPosition, Theme, setTheme

from siftool_gui.pages.about_page import AboutPage
from siftool_gui.pages.clean_page import CleanPage
from siftool_gui.pages.settings_page import SettingsPage
from siftool_gui.settings import SiftoolConfig
from siftool_gui.system_theme import ThemeListener
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

        self.theme_listener: ThemeListener | None = None  # only while "Seguir o Windows" is chosen
        config.themeMode.valueChanged.connect(self._follow_theme_mode)
        self._watching_theme_mode = True
        app = QCoreApplication.instance()
        if app is not None:  # a session ending may quit without closing the window first
            app.aboutToQuit.connect(self._stop_theme_listener)
        self._follow_theme_mode(config.get(config.themeMode))

    # -- theme ----------------------------------------------------------------------------

    def _follow_theme_mode(self, mode) -> None:
        """Listen to Windows' theme only while "Seguir o Windows" (`Theme.AUTO`) is the chosen theme."""
        if mode != Theme.AUTO:
            self._stop_theme_listener()
        elif self.theme_listener is None:
            listener = ThemeListener(self)
            # Emitted in the listener's thread: queued here, restyling happens in the GUI thread.
            listener.systemThemeChanged.connect(self._on_system_theme_changed)
            listener.start()
            self.theme_listener = listener

    def _stop_theme_listener(self) -> None:
        listener, self.theme_listener = self.theme_listener, None
        if listener is not None:
            listener.stop()
            listener.deleteLater()

    def _on_system_theme_changed(self) -> None:
        """Windows switched between light and dark: restyle, as choosing a theme does.

        The listener has already read the new theme into `qconfig`; with
        the theme mode still `Theme.AUTO`, `setTheme` only restyles the
        widgets and announces `themeChangedFinished` (Mica included).
        """
        if self.config.get(self.config.themeMode) == Theme.AUTO:
            setTheme(Theme.AUTO, lazy=True)

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
        # Never again: a closed window must not start a listener when the setting changes later.
        if self._watching_theme_mode:
            self._watching_theme_mode = False
            self.config.themeMode.valueChanged.disconnect(self._follow_theme_mode)
        self._stop_theme_listener()
        super().closeEvent(event)
