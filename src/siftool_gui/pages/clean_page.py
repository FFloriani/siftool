"""The "Limpar" page (spec sections 4.4, 5.2 and 6).

One column, at most `MAX_CONTENT_WIDTH` wide and centered, scrolling as a
whole only when the window is too short for it:

1. the header ("Limpar fotos" and its sentence);
2. the `DropArea` -- dominant while the list is empty, a low strip after;
3. the summary line with "Limpar lista" (hidden while the list is empty);
4. the list: a `FileTreeView` over the `FileListModel`, painted by the
   `FileItemDelegate`. A click (or Enter) on a file opens its details in
   place; Delete removes the selection.

Below the column, fixed at the bottom of the page, the action bar: the
mode selector with the current mode's description, and the main button.

Work runs on a `JobRunner` (spec 4.4): dropped folders are walked in the
background, every new file is analyzed and gets a preview; the model is
only touched here, in the GUI thread, from the runner's signals.

The list's expand arrows hang in the page margin: the view is placed
`indentation + GUTTER` px further left (and a few px further right) than
the other rows, so the file cards line up with the drop area and the
header.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from pathlib import Path

from PySide6.QtCore import QEvent, QModelIndex, QPersistentModelIndex, Qt, Signal
from PySide6.QtGui import QColor, QKeyEvent, QMouseEvent, QPainter
from PySide6.QtWidgets import QAbstractItemView, QHBoxLayout, QTreeView, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    FluentIcon,
    PrimaryPushButton,
    ProgressBar,
    PushButton,
    ScrollArea,
    SegmentedWidget,
    TitleLabel,
    TransparentPushButton,
    TreeView,
    isDarkTheme,
)

from siftool.model import Mode

from siftool_gui.delegate import GUTTER, FileItemDelegate
from siftool_gui.drop_area import DropArea, accept_as_copy, local_paths
from siftool_gui.entries import EntryState
from siftool_gui.model import KIND_ROLE, THUMBNAIL_ROLE, FileListModel, RowKind, Summary
from siftool_gui.presentation import clean_button_text, mode_description, mode_title, summary_text
from siftool_gui.settings import SiftoolConfig, default_mode
from siftool_gui.workers import JobRunner

TITLE_TEXT = "Limpar fotos"
SUBTITLE_TEXT = "Tire localização, dados pessoais e marcas de IA das suas fotos. O original nunca é alterado."
CLEAR_LIST_TEXT = "Limpar lista"
CANCEL_TEXT = "Cancelar"

MAX_CONTENT_WIDTH = 960
SIDE_MARGIN = 36
LIST_RIGHT_OVERHANG = 9  # the view's QSS right padding (5) + the delegate's card inset (4)

_SECONDARY_LIGHT = QColor(96, 96, 96)
_SECONDARY_DARK = QColor(206, 206, 206)


def _centered(widget: QWidget, margins: tuple[int, int, int, int]) -> QHBoxLayout:
    """`widget` as wide as it may be (its maximum width), centered, inside `margins`."""
    row = QHBoxLayout()
    row.setContentsMargins(*margins)
    row.setSpacing(0)
    row.addStretch(1)
    row.addWidget(widget, 10_000)  # takes all the room up to its maximum width; the rest centers it
    row.addStretch(1)
    return row


class _ActionBar(QWidget):
    """The bar fixed at the bottom of the page: a hairline above it sets it apart from the list."""

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        dark = isDarkTheme()
        painter.fillRect(self.rect(), QColor(255, 255, 255, 8) if dark else QColor(255, 255, 255, 110))
        painter.fillRect(0, 0, self.width(), 1, QColor(255, 255, 255, 21) if dark else QColor(0, 0, 0, 18))


def _inset(widget: QWidget, left: int, right: int) -> QHBoxLayout:
    row = QHBoxLayout()
    row.setContentsMargins(left, 0, right, 0)
    row.setSpacing(0)
    row.addWidget(widget)
    return row


class FileTreeView(TreeView):
    """The list of files: QFluentWidgets' `TreeView` plus the page's mouse and keyboard rules.

    - a plain click on a file row (or on "Detalhes técnicos") opens or
      closes it; Enter does the same for the current row (spec 5.2, 6);
    - → / ← expand and collapse (Qt's own), Delete asks the page to remove
      the selection (`deleteRequested`).

    Clicks on the expand arrow are left to the base class, which already
    toggles there: the row only reacts to clicks on its card, which starts
    `GUTTER` px into the item rect.
    """

    deleteRequested = Signal()

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


class CleanPage(QWidget):
    """The "Limpar" page (see the module docstring)."""

    def __init__(self, config: SiftoolConfig, runner: JobRunner | None = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("cleanPage")
        self.setAcceptDrops(True)  # a drop anywhere on the page counts, not only on the drop area
        self._config = config
        self.runner = runner if runner is not None else JobRunner(self)
        self.model = FileListModel(default_mode(config), self)
        self._pending_scans = 0

        self._build_widgets()
        self._build_layout()
        self._connect()

        self.set_mode(self.model.mode())
        self._on_summary(self.model.summary())

    # -- construction -------------------------------------------------------------

    def _build_widgets(self) -> None:
        self.title_label = TitleLabel(TITLE_TEXT, self)
        self.subtitle_label = BodyLabel(SUBTITLE_TEXT, self)
        self.subtitle_label.setTextColor(_SECONDARY_LIGHT, _SECONDARY_DARK)
        self.subtitle_label.setWordWrap(True)

        self.drop_area = DropArea(self)

        self.summary_label = BodyLabel(self)
        self.clear_button = TransparentPushButton(FluentIcon.DELETE, CLEAR_LIST_TEXT, self)

        self.view = FileTreeView(self)
        self.view.setModel(self.model)
        self.view.setItemDelegate(FileItemDelegate(self.view))
        self.view.setHeaderHidden(True)
        self.view.setUniformRowHeights(False)
        self.view.setAnimated(True)
        self.view.setExpandsOnDoubleClick(False)
        self.view.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.view.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.view.setMinimumHeight(180)
        self.view.setAccessibleName("Lista de fotos")

        self.mode_selector = SegmentedWidget(self)
        for mode in Mode:
            self.mode_selector.addItem(mode.value, mode_title(mode))
        self.mode_selector.setAccessibleName("Modo de limpeza")
        self.mode_description = BodyLabel(self)
        self.mode_description.setTextColor(_SECONDARY_LIGHT, _SECONDARY_DARK)
        self.mode_description.setWordWrap(True)

        self.clean_button = PrimaryPushButton(FluentIcon.BROOM, clean_button_text(0), self)
        self.clean_button.setMinimumWidth(170)
        self.clean_button.setEnabled(False)

        self.progress_bar = ProgressBar(self)
        self.cancel_button = PushButton(CANCEL_TEXT, self)
        self.progress_bar.hide()
        self.cancel_button.hide()

    def _build_layout(self) -> None:
        # The scrolling column: header, drop area, summary, list.
        self._column = QWidget()
        self._column.setMaximumWidth(MAX_CONTENT_WIDTH + SIDE_MARGIN + LIST_RIGHT_OVERHANG)
        content = QVBoxLayout(self._column)
        content.setContentsMargins(0, 28, 0, 12)
        content.setSpacing(0)
        self._content_layout = content

        branch_room = self.view.indentation() + GUTTER  # where the expand arrows hang
        header = QVBoxLayout()
        header.setContentsMargins(branch_room, 0, LIST_RIGHT_OVERHANG, 0)
        header.setSpacing(6)
        header.addWidget(self.title_label)
        header.addWidget(self.subtitle_label)
        content.addLayout(header)
        content.addSpacing(20)

        self._drop_row = _inset(self.drop_area, branch_room, LIST_RIGHT_OVERHANG)
        content.addLayout(self._drop_row)

        self._summary_row = QWidget(self._column)
        summary = QHBoxLayout(self._summary_row)
        summary.setContentsMargins(branch_room, 12, LIST_RIGHT_OVERHANG - 4, 4)
        summary.setSpacing(8)
        summary.addWidget(self.summary_label, 1)
        summary.addWidget(self.clear_button, 0)
        content.addWidget(self._summary_row)
        content.addWidget(self.view, 1)

        scroll_widget = QWidget()
        scroll_widget.setObjectName("cleanPageScrollWidget")
        scroll_layout = _centered(self._column, (0, 0, SIDE_MARGIN - LIST_RIGHT_OVERHANG, 0))
        scroll_widget.setLayout(scroll_layout)

        self._scroll = ScrollArea(self)
        self._scroll.setWidget(scroll_widget)
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.enableTransparentBackground()

        # The action bar, fixed at the bottom: mode on the left, the main button on the right.
        self._action_bar = _ActionBar(self)
        self._action_bar.setObjectName("cleanPageActionBar")
        bar_content = QWidget(self._action_bar)
        bar_content.setMaximumWidth(MAX_CONTENT_WIDTH)
        bar = QVBoxLayout(bar_content)
        bar.setContentsMargins(0, 0, 0, 0)
        bar.setSpacing(10)

        progress = QHBoxLayout()
        progress.setSpacing(12)
        progress.addWidget(self.progress_bar, 1)
        progress.addWidget(self.cancel_button, 0)
        bar.addLayout(progress)

        main = QHBoxLayout()
        main.setSpacing(24)
        mode_column = QVBoxLayout()
        mode_column.setSpacing(8)
        mode_column.addWidget(self.mode_selector, 0, Qt.AlignmentFlag.AlignLeft)
        mode_column.addWidget(self.mode_description)
        main.addLayout(mode_column, 1)
        main.addWidget(self.clean_button, 0, Qt.AlignmentFlag.AlignVCenter)
        bar.addLayout(main)

        self._action_bar.setLayout(_centered(bar_content, (SIDE_MARGIN, 14, SIDE_MARGIN, 18)))

        page = QVBoxLayout(self)
        page.setContentsMargins(0, 0, 0, 0)
        page.setSpacing(0)
        page.addWidget(self._scroll, 1)
        page.addWidget(self._action_bar, 0)

    def _connect(self) -> None:
        self.drop_area.pathsDropped.connect(self.add_paths)
        self.clear_button.clicked.connect(self.clear_list)
        self.view.deleteRequested.connect(self.remove_selected)
        self.mode_selector.currentItemChanged.connect(self._on_mode_item_changed)
        self.model.summaryChanged.connect(self._on_summary)

        runner = self.runner
        runner.pathsFound.connect(self._add_files)
        runner.scanFinished.connect(self._on_scan_finished)
        runner.analyzed.connect(self.model.apply_report)
        runner.analysisFailed.connect(self.model.apply_analysis_error)
        runner.thumbnailReady.connect(self.model.set_thumbnail)
        runner.jobDropped.connect(self.model.revert_dropped)

    # -- adding files ------------------------------------------------------------------

    def add_paths(self, paths: Iterable[str | Path]) -> None:
        """Add dropped or chosen paths: folders are walked in the background, files analyzed."""
        files: list[str] = []
        folders: list[str] = []
        for raw in paths:
            (folders if os.path.isdir(raw) else files).append(str(raw))
        if folders:
            self._pending_scans += 1
            self.runner.scan(folders)
        if files:
            self._add_files(files)
        self._update_actions()

    def _add_files(self, paths: list[str]) -> None:
        for entry_id in self.model.add_paths(paths):
            entry = self.model.entry(entry_id)
            self.model.mark_analyzing(entry_id)
            self.runner.analyze(entry_id, entry.path)
            if self.model.index_for(entry_id).data(THUMBNAIL_ROLE) is None:
                self.runner.thumbnail(entry_id, entry.path)

    def _on_scan_finished(self) -> None:
        self._pending_scans = max(0, self._pending_scans - 1)
        self._update_actions()

    # -- mode -----------------------------------------------------------------------------

    def set_mode(self, mode: Mode) -> None:
        """Switch the mode: the list recomputes "sai"/"fica" (no new analysis)."""
        mode = Mode(mode)
        self.model.set_mode(mode)
        self.mode_selector.setCurrentItem(mode.value)
        self.mode_description.setText(mode_description(mode))

    def _on_mode_item_changed(self, route_key: str) -> None:
        self.set_mode(Mode(route_key))

    # -- removing -------------------------------------------------------------------------

    def remove_selected(self) -> None:
        """Remove the selected files (a selected detail row counts for its file).

        Spec 4.5: any state except "Limpando" -- a file being cleaned stays.
        """
        indexes = self.view.selectionModel().selectedIndexes() or [self.view.currentIndex()]
        ids: list[int] = []
        for index in indexes:
            entry_id = self.model.entry_id_at(index)
            if entry_id is not None and entry_id not in ids:
                ids.append(entry_id)
        for entry_id in ids:
            if self.model.entry(entry_id).state is not EntryState.CLEANING:
                self.model.remove_entry(entry_id)

    def clear_list(self) -> None:
        """Empty the list -- only when no file is being walked, analyzed or cleaned."""
        if self._working():
            return
        self.model.clear_entries()

    # -- state ------------------------------------------------------------------------------

    def _working(self) -> bool:
        return self._pending_scans > 0 or self.model.summary().busy > 0

    def _on_summary(self, summary: Summary) -> None:
        has_rows = summary.total > 0
        self.summary_label.setText(summary_text(summary.total, summary.with_findings, summary.unsupported))
        self._summary_row.setVisible(has_rows)
        self.view.setVisible(has_rows)
        self.drop_area.set_compact(has_rows)
        self._content_layout.setStretchFactor(self._drop_row, 0 if has_rows else 1)
        self._update_actions()

    def _update_actions(self) -> None:
        summary = self.model.summary()
        self.clean_button.setText(clean_button_text(summary.cleanable))
        self.clear_button.setEnabled(summary.total > 0 and not self._working())

    # -- drops anywhere on the page ------------------------------------------------------

    def dragEnterEvent(self, event) -> None:
        self.drop_area.set_highlighted(accept_as_copy(event))

    def dragMoveEvent(self, event) -> None:
        accept_as_copy(event)

    def dragLeaveEvent(self, event) -> None:
        self.drop_area.set_highlighted(False)

    def dropEvent(self, event) -> None:
        self.drop_area.set_highlighted(False)
        if accept_as_copy(event):
            self.add_paths(local_paths(event.mimeData()))
