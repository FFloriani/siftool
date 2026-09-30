"""`FileTreeView`: the list of files on the "Limpar" page (spec sections 5.2 and 6).

QFluentWidgets' `TreeView` plus the page's mouse and keyboard rules; what
the rows look like is `siftool_gui.delegate`'s business, what they hold is
`siftool_gui.model`'s.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QModelIndex, QPersistentModelIndex, QPoint, Qt, Signal
from PySide6.QtGui import QContextMenuEvent, QKeyEvent, QMouseEvent
from PySide6.QtWidgets import QTreeView, QWidget
from qfluentwidgets import TreeView

from siftool_gui.delegate import GUTTER
from siftool_gui.model import KIND_ROLE, RowKind


class FileTreeView(TreeView):
    """The list of files: QFluentWidgets' `TreeView` plus the page's mouse and keyboard rules.

    - a plain click on a file row (or on "Detalhes técnicos") opens or
      closes it; Enter does the same for the current row (spec 5.2, 6);
    - → / ← expand and collapse (Qt's own), Delete asks the page to remove
      the selection (`deleteRequested`), right click and the Menu key ask
      for the row menu (`menuRequested`, with the global position to show
      it at).

    Clicks on the expand arrow are left to the arrow rule below: the row
    only reacts to clicks on its card, which starts `GUTTER` px into the
    item rect.
    """

    deleteRequested = Signal()
    menuRequested = Signal(QModelIndex, QPoint)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._pressed = QPersistentModelIndex()

    def is_expandable(self, index: QModelIndex) -> bool:
        return (
            index.isValid()
            and index.data(KIND_ROLE) in (RowKind.FILE, RowKind.TECH_HEADER)
            and self.model().hasChildren(index)
        )

    def toggle(self, index: QModelIndex) -> None:
        if self.is_expandable(index):
            self.setExpanded(index, not self.isExpanded(index))

    def viewportEvent(self, event: QEvent) -> bool:
        if event.type() != QEvent.Type.MouseButtonPress:
            return super().viewportEvent(event)
        # QFluentWidgets' `TreeView.viewportEvent` toggles a row when its (shifted) arrow is
        # pressed, but reads the deprecated `QMouseEvent.pos()`; the same rule, with `position()`.
        pos = event.position()
        index = self.indexAt(pos.toPoint())
        if index.isValid():
            level = 0
            parent = index.parent()
            while parent.isValid():
                level += 1
                parent = parent.parent()
            arrow = level * self.indentation() + 20
            if arrow < pos.x() < arrow + 10:
                self.setExpanded(index, not self.isExpanded(index))
        return QTreeView.viewportEvent(self, event)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        self._pressed = QPersistentModelIndex(self.indexAt(event.position().toPoint()))
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        pos = event.position().toPoint()
        index = self.indexAt(pos)
        toggle = (
            event.button() == Qt.MouseButton.LeftButton
            and event.modifiers() == Qt.KeyboardModifier.NoModifier
            and index.isValid()
            and QPersistentModelIndex(index) == self._pressed
            and pos.x() >= self.visualRect(index).left() + GUTTER
        )
        super().mouseReleaseEvent(event)
        if toggle:
            self.toggle(index)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        key = event.key()
        plain = event.modifiers() in (Qt.KeyboardModifier.NoModifier, Qt.KeyboardModifier.KeypadModifier)
        if plain and key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self.is_expandable(self.currentIndex()):
            self.toggle(self.currentIndex())
            event.accept()
            return
        if plain and key == Qt.Key.Key_Delete:
            self.deleteRequested.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:
        if event.reason() == QContextMenuEvent.Reason.Mouse:
            index = self.indexAt(self.viewport().mapFromGlobal(event.globalPos()))
            pos = event.globalPos()
        else:  # the Menu key: just under the current row's card
            index = self.currentIndex()
            rect = self.visualRect(index)
            pos = self.viewport().mapToGlobal(QPoint(rect.left() + GUTTER + 12, rect.bottom()))
        if not index.isValid():
            event.ignore()
            return
        event.accept()
        self.menuRequested.emit(index, pos)
