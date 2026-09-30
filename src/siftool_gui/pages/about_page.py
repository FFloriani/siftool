"""The "Sobre" page (spec section 5.7).

Provisional shell for Task 3: just the final `objectName` `MainWindow`
routes on. The version/promises/license/credits content lands in a
later delivery.
"""

from __future__ import annotations

from PySide6.QtWidgets import QWidget


class AboutPage(QWidget):
    """Placeholder for the "Sobre" page."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("aboutPage")
