"""The "Configurações" page (spec section 5.6).

Provisional shell for Task 3: just the final `objectName` `MainWindow`
routes on. The `SettingCardGroup`s land in a later delivery.
"""

from __future__ import annotations

from PySide6.QtWidgets import QWidget


class SettingsPage(QWidget):
    """Placeholder for the "Configurações" page."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("settingsPage")
