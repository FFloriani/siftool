"""The "Limpar" page (spec section 5.2).

Provisional shell for Task 3: just the final `objectName` `MainWindow`
routes on. The drop area, list and action bar land in delivery B.
"""

from __future__ import annotations

from PySide6.QtWidgets import QWidget


class CleanPage(QWidget):
    """Placeholder for the "Limpar" page."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("cleanPage")
