"""The "Limpar" page (spec sections 4.4, 4.5, 5.2, 5.5 and 6).

One column, at most `MAX_CONTENT_WIDTH` wide and centered, scrolling as a
whole only when the window is too short for it:

1. the header ("Limpar fotos" and its sentence);
2. the `DropArea` -- dominant while the list is empty, a low strip after;
3. the summary line with "Limpar lista" (hidden while the list is empty);
4. the list: a `FileTreeView` (`siftool_gui.file_view`) over the
   `FileListModel`, painted by the `FileItemDelegate`. A click (or Enter)
   on a file opens its details in place; Delete removes the selection;
   right click or the Menu key opens the row menu ("Mostrar no Explorer",
   "Limpar de novo", "Remover da lista").

Below the column, fixed at the bottom of the page, the action bar: the
progress of the current batch with "Cancelar" (only while there is work),
the mode selector with the current mode's description, and the main
button "Limpar N fotos" (Ctrl+Enter).

Work runs on a `JobRunner` (spec 4.4): dropped folders are walked in the
background, every new file is analyzed and gets a preview, and "Limpar"
sends every analyzed file not cleaned yet to `siftool.clean`. The model is
only touched here, in the GUI thread, from the runner's signals. Output
names come from `siftool.batch.output_path_for`; every name handed out is
reserved for the rest of the session, so two copies never share one.

Two rules keep the window responsive with a folder of a thousand photos
(spec 3, criterion 2):

- backpressure: the page keeps its own queues of files to analyze and to
  clean and hands the runner at most `2 x max_workers` of them at a time,
  so results cannot pile up by the thousand. A file waits as "Na fila"
  until its analysis really starts; a batch's mode and output folder are
  fixed when the batch starts;
- a time budget: what the runner reports only lands in an inbox. A
  zero-interval timer applies it to the model for at most
  `RESULT_BUDGET_S` per turn of the event loop, then hands out more work
  and brings the labels up to date once -- painting, timers and input get
  their turn in between, however fast the workers are. User actions
  ("Cancelar", closing the window) apply the whole inbox at once.

The list's expand arrows hang in the page margin: the view is placed
`indentation + GUTTER` px further left (and a few px further right) than
the other rows, so the file cards line up with the drop area and the
header.
"""

from __future__ import annotations

import os
import time
from collections import deque
from collections.abc import Callable, Iterable
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QModelIndex, QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QImage, QKeySequence, QPainter, QShortcut
from PySide6.QtWidgets import QAbstractItemView, QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (
    Action,
    BodyLabel,
    FluentIcon,
    InfoBar,
    InfoBarPosition,
    PrimaryPushButton,
    ProgressBar,
    PushButton,
    RoundMenu,
    ScrollArea,
    SegmentedWidget,
    TitleLabel,
    TransparentPushButton,
    isDarkTheme,
)

from siftool.batch import output_path_for, path_key
from siftool.model import CleanResult, Mode, Report, Status

from siftool_gui import shell
from siftool_gui.delegate import GUTTER, FileItemDelegate
from siftool_gui.drop_area import DropArea, accept_as_copy, local_paths
from siftool_gui.entries import EntryState, FileEntry
from siftool_gui.file_view import FileTreeView
from siftool_gui.model import THUMBNAIL_ROLE, FileListModel, Summary
from siftool_gui.presentation import (
    clean_button_text,
    done_text,
    failed_hint,
    failed_title,
    mode_description,
    mode_title,
    summary_text,
)
from siftool_gui.settings import SiftoolConfig, default_mode, output_dir
from siftool_gui.workers import JobRunner

TITLE_TEXT = "Limpar fotos"
SUBTITLE_TEXT = "Tire localização, dados pessoais e marcas de IA das suas fotos. O original nunca é alterado."
CLEAR_LIST_TEXT = "Limpar lista"
CANCEL_TEXT = "Cancelar"
SCANNING_TEXT = "Procurando fotos…"
ANALYZING_TEXT = "Analisando fotos… {done} de {total}"
CLEANING_TEXT = "Limpando fotos… {done} de {total}"
DONE_TITLE = "Pronto"
OPEN_FOLDER_TEXT = "Abrir pasta"
FOLDER_UNAVAILABLE_TITLE = "Pasta indisponível"
FOLDER_UNAVAILABLE_TEXT = (
    "A pasta escolhida para salvar as cópias não está disponível. Escolha outra em Configurações."
)
SHOW_IN_EXPLORER_TEXT = "Mostrar no Explorer"
CLEAN_AGAIN_TEXT = "Limpar de novo"
REMOVE_TEXT = "Remover da lista"

MAX_CONTENT_WIDTH = 960
SIDE_MARGIN = 36
RESULT_BUDGET_S = 0.008  # how long one turn of the event loop may spend applying results
LIST_RIGHT_OVERHANG = 9  # the view's QSS right padding (5) + the delegate's card inset (4)

# States a clean has finished in: "Limpar de novo" applies to them (spec 4.5).
_FINISHED_STATES = (EntryState.VERIFIED, EntryState.WARNINGS, EntryState.FAILED)

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


def _inset(widget: QWidget, left: int, right: int) -> QHBoxLayout:
    row = QHBoxLayout()
    row.setContentsMargins(left, 0, right, 0)
    row.setSpacing(0)
    row.addWidget(widget)
    return row


class _ActionBar(QWidget):
    """The bar fixed at the bottom of the page: a hairline above it sets it apart from the list."""

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        dark = isDarkTheme()
        painter.fillRect(self.rect(), QColor(255, 255, 255, 8) if dark else QColor(255, 255, 255, 110))
        painter.fillRect(0, 0, self.width(), 1, QColor(255, 255, 255, 21) if dark else QColor(0, 0, 0, 18))


@dataclass
class _Batch:
    """One "Limpar" (or "Limpar de novo") run, counted until its last copy is done or dropped."""

    folder: Path | None  # the configured output folder, if any
    verified: int = 0
    warnings: int = 0
    failed: int = 0
    first_copy_folder: Path | None = None


class CleanPage(QWidget):
    """The "Limpar" page (see the module docstring)."""

    batchFinished = Signal(object)  # (verified, warnings, failed) of the batch that just ended

    def __init__(self, config: SiftoolConfig, runner: JobRunner | None = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("cleanPage")
        self.setAcceptDrops(True)  # a drop anywhere on the page counts, not only on the drop area
        self._config = config
        self.runner = runner if runner is not None else JobRunner(self)
        self.model = FileListModel(default_mode(config), self)

        self._pending_scans = 0
        self._to_analyze: dict[int, None] = {}  # entry ids waiting for an analysis, in order
        self._to_clean: dict[int, tuple[Path | None, Mode]] = {}  # waiting for a clean: (folder, mode)
        self._in_flight = 0  # analyses and cleans handed to the runner, not back yet
        self._work_total = 0  # analyses and cleans queued in the current stretch of work
        self._work_done = 0  # ... and how many of them are over (done, dropped or skipped)
        self._cleaning: set[int] = set()  # entries of the running batch not cleaned yet (queued or in flight)
        self._batch: _Batch | None = None
        self._taken: set[str] = set()  # every output name handed out this session (`path_key`s)
        self._bars: list[InfoBar] = []  # the messages about the last batch

        self._inbox: deque[tuple[Callable[..., None], tuple]] = deque()  # what the runner reported, not applied yet
        self._summary_deferred = False  # while many rows change in one go, the summary waits
        self._summary_stale = False
        self._drain_timer = QTimer(self)
        self._drain_timer.setSingleShot(True)
        self._drain_timer.setInterval(0)
        self._drain_timer.timeout.connect(self._drain)

        self._build_widgets()
        self._build_layout()
        self._build_shortcuts()
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
        self.clean_button.setToolTip("Limpar as fotos analisadas (Ctrl+Enter)")
        self.clean_button.setEnabled(False)

        self.progress_label = BodyLabel(self)
        self.progress_label.setTextColor(_SECONDARY_LIGHT, _SECONDARY_DARK)
        self.progress_bar = ProgressBar(self)
        self.progress_bar.setAccessibleName("Progresso")
        self.cancel_button = PushButton(FluentIcon.CLOSE, CANCEL_TEXT, self)
        for widget in (self.progress_label, self.progress_bar, self.cancel_button):
            widget.hide()

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
        scroll_widget.setLayout(_centered(self._column, (0, 0, SIDE_MARGIN - LIST_RIGHT_OVERHANG, 0)))

        self._scroll = ScrollArea(self)
        self._scroll.setWidget(scroll_widget)
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.enableTransparentBackground()

        # The action bar, fixed at the bottom: progress, then the mode and the main button.
        self._action_bar = _ActionBar(self)
        self._action_bar.setObjectName("cleanPageActionBar")
        bar_content = QWidget(self._action_bar)
        bar_content.setMaximumWidth(MAX_CONTENT_WIDTH)
        bar = QVBoxLayout(bar_content)
        bar.setContentsMargins(0, 0, 0, 0)
        bar.setSpacing(12)

        # The messages about the last batch sit here, inline, where the user just clicked:
        # floating InfoBars would cover the drop area's buttons.
        self._message_host = bar_content
        self._messages = QVBoxLayout()
        self._messages.setContentsMargins(0, 0, 0, 0)
        self._messages.setSpacing(8)
        bar.addLayout(self._messages)

        self._progress_row = QWidget(bar_content)
        progress = QHBoxLayout(self._progress_row)
        progress.setContentsMargins(0, 0, 0, 0)
        progress.setSpacing(12)
        progress.addWidget(self.progress_label, 0)
        progress.addWidget(self.progress_bar, 1)
        progress.addWidget(self.cancel_button, 0)
        self._progress_row.hide()
        bar.addWidget(self._progress_row)

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

        # Spec 6: "Adicionar fotos" -> "Adicionar pasta" -> list -> mode -> "Limpar".
        chain = [self.drop_area.add_files_button, self.drop_area.add_folder_button, self.view,
                 *(self.mode_selector.widget(mode.value) for mode in Mode), self.clean_button,
                 self.cancel_button, self.clear_button]
        for first, second in zip(chain, chain[1:]):
            QWidget.setTabOrder(first, second)

    def _build_shortcuts(self) -> None:
        # Window-wide, but only while this page is the one shown (see `_when_shown`).
        for keys, action in (
            ("Ctrl+Return", self.start_cleaning),
            ("Ctrl+Enter", self.start_cleaning),
            ("Ctrl+O", self.drop_area.choose_files),
            ("Ctrl+Shift+O", self.drop_area.choose_folder),
        ):
            shortcut = QShortcut(QKeySequence(keys), self)
            shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
            shortcut.activated.connect(self._when_shown(action))

    def _when_shown(self, action):
        def run() -> None:
            if self.isVisible():
                action()

        return run

    def _connect(self) -> None:
        self.drop_area.pathsDropped.connect(self.add_paths)
        self.clear_button.clicked.connect(self.clear_list)
        self.clean_button.clicked.connect(self.start_cleaning)
        self.cancel_button.clicked.connect(self.cancel)
        self.view.deleteRequested.connect(self.remove_selected)
        self.view.menuRequested.connect(self._show_row_menu)
        self.mode_selector.currentItemChanged.connect(self._on_mode_item_changed)
        self.model.summaryChanged.connect(self._on_summary)

        # Bound methods (not lambdas): Qt drops these connections when the page is destroyed.
        runner = self.runner
        runner.pathsFound.connect(self._on_paths_found)
        runner.scanFinished.connect(self._on_scan_finished)
        runner.analyzed.connect(self._on_analyzed)
        runner.analysisFailed.connect(self._on_analysis_failed)
        runner.thumbnailReady.connect(self._on_thumbnail)
        runner.cleaned.connect(self._on_cleaned)
        runner.cleanFailed.connect(self._on_clean_failed)
        runner.jobDropped.connect(self._on_job_dropped)

    # -- what the runner reports: into the inbox, applied on a time budget ---------------------

    def _on_paths_found(self, paths: list) -> None:
        self._receive(self._add_files, paths)

    def _on_scan_finished(self) -> None:
        self._receive(self._scan_finished)

    def _on_analyzed(self, entry_id: int, report: Report) -> None:
        self._receive(self._analyzed, entry_id, report)

    def _on_analysis_failed(self, entry_id: int, exc: BaseException) -> None:
        self._receive(self._analysis_failed, entry_id, exc)

    def _on_thumbnail(self, entry_id: int, image: QImage) -> None:
        self._receive(self.model.set_thumbnail, entry_id, image)

    def _on_cleaned(self, entry_id: int, result: CleanResult) -> None:
        self._receive(self._cleaned, entry_id, result)

    def _on_clean_failed(self, entry_id: int, exc: BaseException) -> None:
        self._receive(self._clean_failed, entry_id, exc)

    def _on_job_dropped(self, entry_id: int, kind: str) -> None:
        self._receive(self._job_dropped, entry_id, kind)

    def _receive(self, handler: Callable[..., None], *args) -> None:
        self._inbox.append((handler, args))
        if not self._drain_timer.isActive():
            self._drain_timer.start()

    def _drain(self, everything: bool = False) -> None:
        """Apply what the runner reported: for `RESULT_BUDGET_S`, or all of it when `everything`."""
        deadline = time.perf_counter() + RESULT_BUDGET_S
        try:
            with self._deferring_summary():
                while self._inbox:
                    handler, args = self._inbox.popleft()
                    handler(*args)
                    if not everything and time.perf_counter() >= deadline:
                        break
        finally:
            if self._inbox:  # the rest next turn: paints, timers and input come first
                self._drain_timer.start()
        self._catch_up()

    @contextmanager
    def _deferring_summary(self):
        outer, self._summary_deferred = self._summary_deferred, True
        try:
            yield
        finally:
            self._summary_deferred = outer

    def _catch_up(self) -> None:
        """After work arrived or came back: hand out more, update the page, close a finished batch."""
        with self._deferring_summary():
            self._pump()
        if self._summary_stale:
            self._summary_stale = False
            self._show_summary(self.model.summary())
        self._refresh()
        if self._batch is not None and not self._cleaning:
            self._finish_batch()  # last: whoever listens to `batchFinished` sees the page up to date

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
        self._catch_up()

    def _add_files(self, paths: list[str]) -> None:
        for entry_id in self.model.add_paths(paths):
            if entry_id in self._to_analyze:
                continue
            self._to_analyze[entry_id] = None
            self._work_total += 1
            if self.model.index_for(entry_id).data(THUMBNAIL_ROLE) is None:
                # Previews go to the runner at once: they have a lower priority there.
                self.runner.thumbnail(entry_id, self.model.entry(entry_id).path)

    def _scan_finished(self) -> None:
        self._pending_scans = max(0, self._pending_scans - 1)

    def _analyzed(self, entry_id: int, report: Report) -> None:
        try:
            self.model.apply_report(entry_id, report)
        finally:  # whatever happens, the job is back: the counts must not drift
            self._job_came_back()

    def _analysis_failed(self, entry_id: int, exc: BaseException) -> None:
        try:
            self.model.apply_analysis_error(entry_id, exc)
        finally:
            self._job_came_back()

    # -- the queues -----------------------------------------------------------------------

    def _pump(self) -> None:
        """Hand the runner queued work until `2 x max_workers` jobs are in flight (cleans first)."""
        limit = 2 * self.runner.max_workers()
        while self._in_flight < limit and (self._to_clean or self._to_analyze):
            if self._to_clean:
                entry_id = next(iter(self._to_clean))
                folder, mode = self._to_clean.pop(entry_id)
                self._start_clean_job(entry_id, folder, mode)
            else:
                entry_id = next(iter(self._to_analyze))
                del self._to_analyze[entry_id]
                self._start_analysis_job(entry_id)

    def _start_analysis_job(self, entry_id: int) -> None:
        entry = self._entry_or_none(entry_id)
        if entry is None or entry.state is not EntryState.QUEUED:  # removed meanwhile
            self._work_done += 1
            return
        self.model.mark_analyzing(entry_id)
        self._in_flight += 1
        self.runner.analyze(entry_id, entry.path)

    def _start_clean_job(self, entry_id: int, folder: Path | None, mode: Mode) -> None:
        entry = self._entry_or_none(entry_id)
        if entry is None or entry.state not in (EntryState.ANALYZED, *_FINISHED_STATES):  # removed meanwhile
            self._cleaning.discard(entry_id)
            self._work_done += 1
            return
        dst = output_path_for(entry.path, folder, False, self._taken)
        self._taken.add(path_key(dst))
        self.model.mark_cleaning(entry_id)
        self._in_flight += 1
        self.runner.clean(entry_id, entry.path, dst, mode)

    def _abandon_queues(self) -> None:
        """Forget the queued work (it never left its state, so nothing needs reverting)."""
        self._work_total -= len(self._to_analyze) + len(self._to_clean)
        for entry_id in self._to_clean:
            self._cleaning.discard(entry_id)
        self._to_analyze.clear()
        self._to_clean.clear()

    def _job_came_back(self) -> None:
        self._in_flight = max(0, self._in_flight - 1)
        self._work_done += 1

    # -- cleaning -------------------------------------------------------------------------

    def start_cleaning(self) -> None:
        """"Limpar N fotos": clean every analyzed file not cleaned yet, in the current mode.

        Does nothing while a clean is running. When the configured output
        folder is missing, not a folder or not writable, nothing starts and
        one message says so (spec review focus 5), instead of every row
        failing on its own.
        """
        if self._cleaning:
            return
        ids = self.model.cleanable_ids()
        if not ids:
            return
        usable, folder = self._output_folder()
        if not usable:
            return
        self._begin_batch(folder)
        for entry_id in ids:
            self._queue_clean(entry_id, folder)
        self._catch_up()

    def clean_again(self, entry_id: int) -> None:
        """"Limpar de novo": a new copy of a finished file, in the current mode, under a new name."""
        entry = self._entry_or_none(entry_id)
        if entry is None or entry.state not in _FINISHED_STATES:
            return
        usable, folder = self._output_folder()
        if not usable:
            return
        self._begin_batch(folder)
        self._queue_clean(entry_id, folder)
        self._catch_up()

    def cancel(self) -> None:
        """"Cancelar": drop what has not started (it goes back to where it was); the rest finishes.

        The page is up to date when this returns: what the runner dropped
        (and any result already waiting) is applied at once.
        """
        self._abandon_queues()
        self.runner.cancel()
        self._drain(everything=True)

    def busy(self) -> bool:
        """Whether any work (walk, analysis, preview or clean) is queued, running or waiting to be shown."""
        return self.runner.busy() or bool(self._to_analyze or self._to_clean or self._inbox)

    def _output_folder(self) -> tuple[bool, Path | None]:
        """`(usable, folder)`: `folder` is None for "beside the original"; shows why when unusable."""
        cfg = self._config
        if cfg.get(cfg.saveLocation) != "folder":
            return True, None
        folder = output_dir(cfg)
        if folder is not None and folder.is_dir() and os.access(folder, os.W_OK):
            return True, folder
        self._close_bars()
        self._post(InfoBar.error, FOLDER_UNAVAILABLE_TITLE, FOLDER_UNAVAILABLE_TEXT)
        return False, folder

    def _queue_clean(self, entry_id: int, folder: Path | None) -> None:
        if entry_id in self._cleaning:
            return
        self._to_clean[entry_id] = (folder, self.model.mode())
        self._cleaning.add(entry_id)
        self._work_total += 1

    def _begin_batch(self, folder: Path | None) -> None:
        if self._batch is None:
            self._close_bars()
            self._batch = _Batch(folder)

    def _cleaned(self, entry_id: int, result: CleanResult) -> None:
        try:
            self.model.apply_result(entry_id, result)
            batch = self._batch
            if batch is not None:
                if result.verdict.status is Status.VERIFIED:
                    batch.verified += 1
                else:
                    batch.warnings += 1
                if batch.first_copy_folder is None:
                    batch.first_copy_folder = Path(result.dst).parent
        finally:
            self._cleaning.discard(entry_id)
            self._job_came_back()

    def _clean_failed(self, entry_id: int, exc: BaseException) -> None:
        try:
            self.model.apply_clean_error(entry_id, exc)
            if self._batch is not None:
                self._batch.failed += 1
        finally:
            self._cleaning.discard(entry_id)
            self._job_came_back()

    def _job_dropped(self, entry_id: int, kind: str) -> None:
        # Only a cancel drops jobs -- whoever asked for it, nothing queued may start afterwards.
        self._abandon_queues()
        try:
            self.model.revert_dropped(entry_id, kind)
        finally:
            if kind == "clean":
                self._cleaning.discard(entry_id)
            self._job_came_back()

    def _finish_batch(self) -> None:
        batch, self._batch = self._batch, None
        counts = (batch.verified, batch.warnings, batch.failed)
        if batch.verified or batch.warnings:
            folder = batch.folder or batch.first_copy_folder
            bar = self._post(InfoBar.success, DONE_TITLE, done_text(*counts))
            button = PushButton(FluentIcon.FOLDER, OPEN_FOLDER_TEXT, bar)
            button.clicked.connect(lambda: shell.open_folder(folder))
            bar.addWidget(button)
        if batch.failed:
            self._post(InfoBar.warning, failed_title(batch.failed), failed_hint(batch.failed))
        self.batchFinished.emit(counts)

    def _post(self, kind, title: str, content: str) -> InfoBar:
        """Show one message about the last batch, inline in the action bar, until closed or replaced."""
        bar = kind(title, content, orient=Qt.Orientation.Horizontal, isClosable=True, duration=-1,
                   position=InfoBarPosition.NONE, parent=self._message_host)
        self._messages.addWidget(bar)
        self._bars.append(bar)
        return bar

    def _close_bars(self) -> None:
        for bar in self._bars:
            try:
                bar.hide()
                bar.close()  # QFluentWidgets deletes it and slides the others into place
            except RuntimeError:  # already closed by the user
                pass
        self._bars.clear()

    # -- the row menu ------------------------------------------------------------------------

    def row_menu(self, entry_id: int) -> RoundMenu:
        """The menu of one file row: "Mostrar no Explorer", "Limpar de novo", "Remover da lista"."""
        entry = self.model.entry(entry_id)
        menu = RoundMenu(parent=self)

        reveal = Action(FluentIcon.FOLDER, SHOW_IN_EXPLORER_TEXT, menu)
        reveal.setEnabled(self._reveal_target(entry) is not None)
        reveal.triggered.connect(lambda: self.reveal(entry_id))

        again = Action(FluentIcon.SYNC, CLEAN_AGAIN_TEXT, menu)
        again.setEnabled(entry.state in _FINISHED_STATES)
        again.triggered.connect(lambda: self.clean_again(entry_id))

        remove = Action(FluentIcon.DELETE, REMOVE_TEXT, menu)
        remove.setEnabled(entry.state is not EntryState.CLEANING)
        remove.triggered.connect(lambda: self._remove_from_menu(entry_id))

        menu.addAction(reveal)
        menu.addAction(again)
        menu.addSeparator()
        menu.addAction(remove)
        return menu

    def reveal(self, entry_id: int) -> None:
        """"Mostrar no Explorer": the clean copy when it exists, else the original."""
        entry = self._entry_or_none(entry_id)
        target = self._reveal_target(entry) if entry is not None else None
        if target is not None:
            shell.reveal_in_explorer(target)

    @staticmethod
    def _reveal_target(entry: FileEntry) -> Path | None:
        if entry.result is not None and Path(entry.result.dst).exists():
            return Path(entry.result.dst)
        return entry.path if entry.path.exists() else None

    def _show_row_menu(self, index: QModelIndex, global_pos: QPoint) -> None:
        entry_id = self.model.entry_id_at(index)
        if entry_id is not None:
            self.row_menu(entry_id).exec(global_pos)

    def _remove_from_menu(self, entry_id: int) -> None:
        selected = self._selected_ids()
        if entry_id in selected:
            self._remove(selected)
        else:
            self._remove([entry_id])

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
        self._remove(self._selected_ids())

    def clear_list(self) -> None:
        """Empty the list -- only when no file is being walked, analyzed or cleaned."""
        if self._working():
            return
        self._close_bars()
        self.model.clear_entries()

    def _selected_ids(self) -> list[int]:
        indexes = self.view.selectionModel().selectedIndexes() or [self.view.currentIndex()]
        ids: list[int] = []
        for index in indexes:
            entry_id = self.model.entry_id_at(index)
            if entry_id is not None and entry_id not in ids:
                ids.append(entry_id)
        return ids

    def _remove(self, ids: Iterable[int]) -> None:
        for entry_id in ids:
            entry = self._entry_or_none(entry_id)
            if entry is not None and entry.state is not EntryState.CLEANING:
                self.model.remove_entry(entry_id)

    def _entry_or_none(self, entry_id: int) -> FileEntry | None:
        try:
            return self.model.entry(entry_id)
        except KeyError:
            return None

    # -- state ------------------------------------------------------------------------------

    def _working(self) -> bool:
        """Whether a folder is being walked or a file is waiting for, or in, an analysis or a clean."""
        return self._pending_scans > 0 or self.model.summary().busy > 0 or bool(self._to_analyze or self._to_clean)

    def _on_summary(self, summary: Summary) -> None:
        if self._summary_deferred:  # many rows change in one go: `_catch_up` shows the summary once
            self._summary_stale = True
            return
        self._show_summary(summary)
        self._refresh()

    def _show_summary(self, summary: Summary) -> None:
        has_rows = summary.total > 0
        self.summary_label.setText(summary_text(summary.total, summary.with_findings, summary.unsupported))
        self._summary_row.setVisible(has_rows)
        self.view.setVisible(has_rows)
        self.drop_area.set_compact(has_rows)
        self._content_layout.setStretchFactor(self._drop_row, 0 if has_rows else 1)

    def _refresh(self) -> None:
        """Bring the progress row and the buttons up to date."""
        in_progress = self._pending_scans > 0 or self._work_done < self._work_total
        if not in_progress:  # the stretch of work is over: the next one counts from zero
            self._work_total = self._work_done = 0
        self._progress_row.setVisible(in_progress)
        for widget in (self.progress_label, self.progress_bar, self.cancel_button):
            widget.setVisible(in_progress)
        if in_progress:
            total, done = self._work_total, min(self._work_done, self._work_total)
            self.progress_bar.setRange(0, max(1, total))
            self.progress_bar.setValue(done)
            if total == 0:
                self.progress_label.setText(SCANNING_TEXT)
            else:
                text = CLEANING_TEXT if self._cleaning else ANALYZING_TEXT
                self.progress_label.setText(text.format(done=done, total=total))

        summary = self.model.summary()
        self.clean_button.setText(clean_button_text(summary.cleanable))
        self.clean_button.setEnabled(summary.cleanable > 0 and not self._cleaning)
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
