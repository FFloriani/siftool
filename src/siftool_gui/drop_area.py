"""`DropArea`: where photos enter the "Limpar" page (spec sections 5.2, item 2, and 6).

A card with a dashed border, "Arraste fotos ou pastas aqui" and the
"Adicionar fotos" / "Adicionar pasta" buttons. It is the page's dominant
element while the list is empty and shrinks to a low strip once the list
has rows (`set_compact`). While a drag carrying local files hovers it, the
border turns solid in the accent color.

Everything that enters -- dropped, or chosen in the file dialogs -- leaves
through one signal, `pathsDropped(list[str])`: local files and folders,
with native separators. Only local files count: web links and text are
refused at `dragEnterEvent`.

A drop is always accepted as a *copy* (or a link), never as a move: when a
drop target accepts a move, Windows Explorer deletes the source files --
and the originals are the one thing Siftool never touches.
"""

from __future__ import annotations

import os

from PySide6.QtCore import QMimeData, QRectF, QStandardPaths, Qt, Signal
from PySide6.QtGui import QColor, QDragEnterEvent, QDragMoveEvent, QDropEvent, QFont, QPainter, QPen
from PySide6.QtWidgets import QBoxLayout, QFileDialog, QFrame, QHBoxLayout, QSizePolicy, QWidget
from qfluentwidgets import (
    BodyLabel,
    FluentIcon,
    IconWidget,
    PushButton,
    drawIcon,
    isDarkTheme,
    setFont,
    themeColor,
)

DROP_TEXT = "Arraste fotos ou pastas aqui"
HINT_TEXT = "Você vê o que cada foto esconde antes de limpar."
ADD_FILES_TEXT = "Adicionar fotos"
ADD_FOLDER_TEXT = "Adicionar pasta"
FILE_FILTER = "Imagens (*.jpg *.jpeg *.png *.webp *.heic *.heif);;Todos os arquivos (*.*)"

BIG_MIN_HEIGHT = 220
RADIUS = 8.0


def local_paths(mime: QMimeData | None) -> list[str]:
    """The local files and folders a drag carries (native separators); `[]` when it has none."""
    if mime is None or not mime.hasUrls():
        return []
    paths: list[str] = []
    for url in mime.urls():
        if url.isLocalFile() and url.toLocalFile():
            paths.append(os.path.normpath(url.toLocalFile()))
    return paths


def accept_as_copy(event: QDragEnterEvent | QDragMoveEvent | QDropEvent) -> bool:
    """Accept `event` as a copy (else a link) when it carries local paths; ignore it otherwise.

    Never as a move -- see the module docstring. Returns whether the event
    was accepted.
    """
    if not local_paths(event.mimeData()):
        event.ignore()
        return False
    possible = event.possibleActions()
    if possible & Qt.DropAction.CopyAction:
        event.setDropAction(Qt.DropAction.CopyAction)
    elif possible & Qt.DropAction.LinkAction:
        event.setDropAction(Qt.DropAction.LinkAction)
    else:  # the source only offers a move: refuse rather than risk the originals
        event.ignore()
        return False
    event.accept()
    return True


class AccentIcon(IconWidget):
    """A Fluent icon painted in the current accent color (it differs between themes)."""

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
        drawIcon(self._icon, painter, self.rect(), fill=themeColor().name())


class DropArea(QFrame):
    """The drop target and add buttons of the "Limpar" page (see the module docstring)."""

    pathsDropped = Signal(list)  # list[str]: local files and folders, dropped or chosen

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("dropArea")
        self.setAcceptDrops(True)
        self.setAccessibleName(DROP_TEXT)
        self._compact = False
        self._highlighted = False
        self._last_dir = ""  # this session only: no recent-files list (spec 7)

        self.icon_widget = AccentIcon(FluentIcon.PHOTO, self)
        self.title_label = BodyLabel(DROP_TEXT, self)
        self.hint_label = BodyLabel(HINT_TEXT, self)
        self.hint_label.setTextColor(QColor(96, 96, 96), QColor(206, 206, 206))
        self.hint_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.add_files_button = PushButton(FluentIcon.PHOTO, ADD_FILES_TEXT, self)
        self.add_folder_button = PushButton(FluentIcon.FOLDER_ADD, ADD_FOLDER_TEXT, self)
        self.add_files_button.setToolTip("Escolher fotos (Ctrl+O)")
        self.add_folder_button.setToolTip("Escolher uma pasta (Ctrl+Shift+O)")
        self.add_files_button.clicked.connect(self.choose_files)
        self.add_folder_button.clicked.connect(self.choose_folder)

        self._buttons = QWidget(self)
        buttons = QHBoxLayout(self._buttons)
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.setSpacing(8)
        buttons.addWidget(self.add_files_button)
        buttons.addWidget(self.add_folder_button)

        self._layout = QBoxLayout(QBoxLayout.Direction.TopToBottom, self)
        self._arrange()

    # -- state -------------------------------------------------------------------

    def set_compact(self, compact: bool) -> None:
        """Big (the list is empty) or a low strip (the list has rows)."""
        compact = bool(compact)
        if compact == self._compact:
            return
        self._compact = compact
        self._arrange()

    def is_compact(self) -> bool:
        return self._compact

    def set_highlighted(self, highlighted: bool) -> None:
        """Stand out while a drag with local files hovers the page."""
        highlighted = bool(highlighted)
        if highlighted != self._highlighted:
            self._highlighted = highlighted
            self.update()

    def is_highlighted(self) -> bool:
        return self._highlighted

    # -- the add buttons ------------------------------------------------------------

    def choose_files(self) -> None:
        """"Adicionar fotos": pick files in a dialog; emit them through `pathsDropped`."""
        paths, _ = QFileDialog.getOpenFileNames(self, ADD_FILES_TEXT, self._start_dir(), FILE_FILTER)
        paths = [os.path.normpath(path) for path in paths if path]
        if paths:
            self._last_dir = os.path.dirname(paths[0])
            self.pathsDropped.emit(paths)

    def choose_folder(self) -> None:
        """"Adicionar pasta": pick a folder in a dialog; emit it through `pathsDropped`."""
        folder = QFileDialog.getExistingDirectory(self, ADD_FOLDER_TEXT, self._start_dir())
        if folder:
            folder = os.path.normpath(folder)
            self._last_dir = folder
            self.pathsDropped.emit([folder])

    def _start_dir(self) -> str:
        return self._last_dir or QStandardPaths.writableLocation(QStandardPaths.StandardLocation.PicturesLocation)

    # -- drag and drop ------------------------------------------------------------

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        self.set_highlighted(accept_as_copy(event))

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:
        accept_as_copy(event)

    def dragLeaveEvent(self, event) -> None:
        self.set_highlighted(False)

    def dropEvent(self, event: QDropEvent) -> None:
        self.set_highlighted(False)
        if accept_as_copy(event):
            self.pathsDropped.emit(local_paths(event.mimeData()))

    # -- look -----------------------------------------------------------------------

    def _arrange(self) -> None:
        layout = self._layout
        while layout.count():
            layout.takeAt(0)

        if self._compact:
            layout.setDirection(QBoxLayout.Direction.LeftToRight)
            layout.setContentsMargins(16, 10, 12, 10)
            layout.setSpacing(12)
            self.icon_widget.setFixedSize(20, 20)
            setFont(self.title_label, 14, QFont.Weight.DemiBold)
            self.hint_label.hide()
            layout.addWidget(self.icon_widget, 0, Qt.AlignmentFlag.AlignVCenter)
            layout.addWidget(self.title_label, 0, Qt.AlignmentFlag.AlignVCenter)
            layout.addStretch(1)
            layout.addWidget(self._buttons, 0, Qt.AlignmentFlag.AlignVCenter)
            self.setMinimumHeight(0)
            self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        else:
            layout.setDirection(QBoxLayout.Direction.TopToBottom)
            layout.setContentsMargins(24, 28, 24, 28)
            layout.setSpacing(6)
            self.icon_widget.setFixedSize(44, 44)
            setFont(self.title_label, 20, QFont.Weight.DemiBold)
            self.hint_label.show()
            layout.addStretch(1)
            layout.addWidget(self.icon_widget, 0, Qt.AlignmentFlag.AlignHCenter)
            layout.addSpacing(6)
            layout.addWidget(self.title_label, 0, Qt.AlignmentFlag.AlignHCenter)
            layout.addWidget(self.hint_label, 0, Qt.AlignmentFlag.AlignHCenter)
            layout.addSpacing(14)
            layout.addWidget(self._buttons, 0, Qt.AlignmentFlag.AlignHCenter)
            layout.addStretch(1)
            self.setMinimumHeight(BIG_MIN_HEIGHT)
            self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        self.title_label.adjustSize()
        self.updateGeometry()
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        dark = isDarkTheme()
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        if self._highlighted:
            accent = themeColor()
            fill = QColor(accent)
            fill.setAlpha(40 if dark else 26)
            pen = QPen(accent, 2)
        else:
            fill = QColor(255, 255, 255, 12) if dark else QColor(255, 255, 255, 150)
            pen = QPen(QColor(255, 255, 255, 110) if dark else QColor(0, 0, 0, 90), 1.5)
            pen.setDashPattern([4.0, 3.0])
        painter.setPen(pen)
        painter.setBrush(fill)
        painter.drawRoundedRect(rect, RADIUS, RADIUS)
