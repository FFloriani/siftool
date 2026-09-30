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

Every control that is only an icon -- the title bar's buttons, the
navigation rail (icons only while compact) -- has a Portuguese tooltip and
accessible name (spec 6). QFluentWidgets ships no Portuguese translation
and sets the rail's own tooltips itself ("Back", "Open Navigation"...);
`_PortugueseToolTips` swaps them as they are set. A scroll bar's arrow
buttons are not tab stops.

The window wears Siftool's icon (`assets/siftool.ico`, every size from 16
to 256 px), and `open_paths` is how files reach it from Windows -- the
command line, "Limpar com Siftool", a second launch (distribution spec 2).
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from PySide6.QtCore import QByteArray, QCoreApplication, QEvent, QObject, QSize, Qt
from PySide6.QtGui import QCloseEvent, QIcon
from PySide6.QtWidgets import QWidget
from qfluentwidgets import FluentIcon, FluentWindow, NavigationItemPosition, Theme, setTheme
from qfluentwidgets.components.widgets.scroll_bar import ArrowButton

from siftool_gui.pages.about_page import AboutPage
from siftool_gui.pages.clean_page import CleanPage
from siftool_gui.pages.settings_page import SettingsPage
from siftool_gui.settings import SiftoolConfig
from siftool_gui.system_theme import ThemeListener
from siftool_gui.workers import JobRunner

MINIMUM_SIZE = QSize(720, 560)
DEFAULT_SIZE = QSize(1100, 760)  # room for the 960 px column beside the navigation rail
CLOSE_WAIT_MS = 10_000  # how long closing waits for copies already being written
ICON_PATH = Path(__file__).with_name("assets") / "siftool.ico"  # package data: in a wheel and in the frozen program

MINIMIZE_TEXT = "Minimizar"
MAXIMIZE_TEXT = "Maximizar"
RESTORE_TEXT = "Restaurar"
CLOSE_TEXT = "Fechar"
BACK_TEXT = "Voltar"
MENU_NAME = "Abrir ou fechar a navegação"

# The tooltips QFluentWidgets gives the navigation rail's two buttons, and ours.
_RAIL_TOOLTIPS = {
    "Back": BACK_TEXT,
    "Open Navigation": "Abrir navegação",
    "Close Navigation": "Fechar navegação",
}


def app_icon() -> QIcon:
    """Siftool's icon, every size the .ico holds (the title bar, the taskbar and Alt+Tab each pick theirs)."""
    return QIcon(str(ICON_PATH))


class _PortugueseToolTips(QObject):
    """Swaps the rail buttons' English tooltips for Portuguese ones, each time QFluentWidgets sets one.

    The rail sets them itself -- at start, and again as it opens and
    closes. Not with a `QTranslator` subclass: Qt asks translators from any
    thread (a thumbnail decoder's `QImageReader` included), sometimes while
    holding a lock of its own, and a translator written in Python then
    needs the GIL -- which the GUI thread may hold while it waits for that
    same lock: the program hung. This filter runs in the GUI thread only.
    """

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.ToolTipChange:
            portuguese = _RAIL_TOOLTIPS.get(watched.toolTip())
            if portuguese is not None:
                watched.setToolTip(portuguese)  # its own ToolTipChange finds nothing more to swap
        return False


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
        self.setWindowIcon(app_icon())
        self.setMinimumSize(MINIMUM_SIZE)

        self.addSubInterface(self.clean_page, FluentIcon.BROOM, "Limpar")
        self.addSubInterface(
            self.settings_page, FluentIcon.SETTING, "Configurações", NavigationItemPosition.BOTTOM
        )
        self.addSubInterface(self.about_page, FluentIcon.INFO, "Sobre", NavigationItemPosition.BOTTOM)
        self._label_icon_only_controls()

        self._restore_geometry()

        self.theme_listener: ThemeListener | None = None  # only while "Seguir o Windows" is chosen
        config.themeMode.valueChanged.connect(self._follow_theme_mode)
        self._watching_theme_mode = True
        app = QCoreApplication.instance()
        if app is not None:  # a session ending may quit without closing the window first
            app.aboutToQuit.connect(self._stop_theme_listener)
        self._follow_theme_mode(config.get(config.themeMode))

    # -- files from Windows -----------------------------------------------------------------

    def open_paths(self, paths: Sequence[str]) -> None:
        """Files and folders from Windows: the command line, "Limpar com Siftool", a second launch.

        They go to the "Limpar" page -- shown, whatever page was open -- and
        the window comes to the front, restored if it was minimized. With no
        paths (Siftool opened again from the Start menu), it only comes forward.
        """
        if paths:
            self.switchTo(self.clean_page)
            self.clean_page.add_paths(list(paths))
        self.bring_to_front()

    def bring_to_front(self) -> None:
        """Show the window, restored if minimized (maximized again if it was), above the others and active."""
        if self.isMinimized():
            self.setWindowState(self.windowState() & ~Qt.WindowState.WindowMinimized)
        self.show()
        self.raise_()
        self.activateWindow()

    # -- accessibility --------------------------------------------------------------------

    def _label_icon_only_controls(self) -> None:
        """Spec 6: a Portuguese tooltip and accessible name for every control that is only an icon."""
        bar = self.titleBar
        for button, text in ((bar.minBtn, MINIMIZE_TEXT), (bar.closeBtn, CLOSE_TEXT)):
            button.setToolTip(text)
            button.setAccessibleName(text)
        self._label_maximize_button()

        panel = self.navigationInterface.panel
        self._rail_tooltips = _PortugueseToolTips(self)
        for button, name in ((panel.returnButton, BACK_TEXT), (panel.menuButton, MENU_NAME)):
            button.installEventFilter(self._rail_tooltips)
            button.setToolTip(_RAIL_TOOLTIPS.get(button.toolTip(), button.toolTip()))  # the one set already
            button.setAccessibleName(name)
        for page in (self.clean_page, self.settings_page, self.about_page):
            item = self.navigationInterface.widget(page.objectName())
            text = item.toolTip()  # the rail shows it as the item's tooltip already
            item.setAccessibleName(text)
            item.itemWidget.setAccessibleName(text)  # the part that draws the icon and takes the click

        # A scroll bar's arrow buttons are drawn for the mouse; Tab should only land on named controls.
        for arrow in self.findChildren(ArrowButton):
            arrow.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def _label_maximize_button(self) -> None:
        text = RESTORE_TEXT if self.isMaximized() else MAXIMIZE_TEXT
        self.titleBar.maxBtn.setToolTip(text)
        self.titleBar.maxBtn.setAccessibleName(text)

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange and getattr(self, "titleBar", None) is not None:
            self._label_maximize_button()  # "Maximizar" or "Restaurar": what the button does now

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
        """Remember the size. A settings file that cannot be written (`SiftoolConfig.save` does not raise) must not
        keep the window open, and on a window that is going nobody is left to read the message about it."""
        geometry = bytes(self.saveGeometry().toBase64()).decode("ascii")
        was_blocked = self.config.blockSignals(True)  # no "não foi possível salvar" on the way out
        try:
            self.config.set(self.config.windowGeometry, geometry)
        finally:
            self.config.blockSignals(was_blocked)

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
