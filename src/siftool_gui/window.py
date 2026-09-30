"""`MainWindow`: the application shell (spec sections 4.3 and 5.1).

Three navigation items on a `FluentWindow`: "Limpar" (top) and
"Configurações"/"Sobre" (bottom). The pages themselves live in
`siftool_gui.pages`; this module wires them into the shell and owns the
`JobRunner` the "Limpar" page works with.
"""

from __future__ import annotations

from PySide6.QtCore import QSize
from PySide6.QtWidgets import QWidget
from qfluentwidgets import FluentIcon, FluentWindow, NavigationItemPosition

from siftool_gui.pages.about_page import AboutPage
from siftool_gui.pages.clean_page import CleanPage
from siftool_gui.pages.settings_page import SettingsPage
from siftool_gui.settings import SiftoolConfig
from siftool_gui.workers import JobRunner


class MainWindow(FluentWindow):
    """The Siftool GUI's main window: the navigation shell over its three pages."""

    def __init__(self, config: SiftoolConfig, runner: JobRunner | None = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.config = config
        self.runner = runner if runner is not None else JobRunner(self)

        self.clean_page = CleanPage(config, self.runner, self)
        self.settings_page = SettingsPage(self)
        self.about_page = AboutPage(self)

        self.setWindowTitle("Siftool")
        self.setMinimumSize(QSize(720, 560))

        self.addSubInterface(self.clean_page, FluentIcon.BROOM, "Limpar")
        self.addSubInterface(
            self.settings_page, FluentIcon.SETTING, "Configurações", NavigationItemPosition.BOTTOM
        )
        self.addSubInterface(self.about_page, FluentIcon.INFO, "Sobre", NavigationItemPosition.BOTTOM)
