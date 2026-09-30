"""`FileListModel`: the tree behind the "Limpar" page's list (spec sections 4.3-4.5 and 5.2).

One top-level `FILE` row per `FileEntry`. Its children are rebuilt from
the entry every time the entry's state changes:

- with a report that has findings: one `CATEGORY` row per category (in
  `CATEGORY_ORDER`), then the result's `NOTE` rows when there is a result
  or a clean failure (the explanation, then one row per note or
  violation), then a `TECH_HEADER` ("Detalhes técnicos") whose `TECH`
  children are the findings as the engine reported them -- plus, for a
  failed verification, its violations;
- with a report and no findings: a `NOTE` "Nada escondido nesta foto."
  (followed by the result's notes, once cleaned);
- `UNSUPPORTED` / `ERROR`: a `NOTE` with the error's explanation.

Roles (`Qt.UserRole + 1..8`), by row kind:

| role | FILE | CATEGORY | NOTE | TECH_HEADER | TECH |
|---|---|---|---|---|---|
| `DisplayRole` | file name | category name | text | "Detalhes técnicos" | finding label |
| `ToolTipRole` | full path | "name: value" | text | -- | label — detail — where |
| `KIND_ROLE` | `RowKind` of the row (every row) |||||
| `ENTRY_ID_ROLE` | the owning entry's id (every row) |||||
| `STATE_ROLE` | `EntryState` | | | | |
| `VALUE_ROLE` | status text ("" when the tags say it all) | human value | | | finding detail |
| `CATEGORY_ROLE` | tuple of `Category` (tags) | `Category` value | | | |
| `OUTCOME_ROLE` | | `Outcome` value | | | |
| `FINDING_ROLE` | | | | | `Finding` |
| `THUMBNAIL_ROLE` | `QPixmap` or None | | | | |

Every row also has an `AccessibleTextRole`; the file row's is
"<name>, <state>, <categories>" (spec section 6).

`QModelIndex.data()` always goes through a `QVariant`, and PySide6 turns
a `str`-based enum member into a plain `str` on the way: a CATEGORY row's
`CATEGORY_ROLE` reads back as `"location"`, its `OUTCOME_ROLE` as `"sai"`.
Those values compare and hash equal to `Category.LOCATION` /
`Outcome.REMOVED`, so they work with `==` and as keys of the
`presentation` tables; call `Category(value)` / `Outcome(value)` where
the member itself (or `is`) is needed. `RowKind`, `EntryState`, tuples
and `Finding`s come back as the very objects stored.

"sai"/"fica" (`OUTCOME_ROLE`) is predicted from the current mode
(`presentation.category_outcome`) and recomputed in place by `set_mode`,
without a new analysis (spec 4.4). Once an entry has a saved copy
(Verificado / Com ressalvas) it instead states what that copy actually
kept (`CleanResult.kept`): a later mode switch must not rewrite what
already happened, and a block can be dropped whole even when the mode
allows one of its categories.

The model is only ever touched from the GUI thread: `JobRunner` delivers
every result there. Results for an entry removed meanwhile (its id is
never reused) are ignored.
"""

from __future__ import annotations

import os
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import replace
from enum import IntEnum
from pathlib import Path
from typing import Any, NamedTuple

from PySide6.QtCore import QModelIndex, QObject, QPersistentModelIndex, Qt, Signal
from PySide6.QtGui import QImage, QPixmap, QStandardItem, QStandardItemModel

from siftool.batch import path_key
from siftool.errors import VerificationFailedError
from siftool.model import Category, CleanResult, Finding, Mode, Report

from siftool_gui.entries import EntryState, FileEntry
from siftool_gui.presentation import (
    Outcome,
    category_name,
    category_outcome,
    category_value,
    error_explanation,
    error_title,
    result_explanation,
    result_title,
)


class RowKind(IntEnum):
    """What a row of the tree shows."""

    FILE = 1
    CATEGORY = 2
    NOTE = 3
    TECH_HEADER = 4
    TECH = 5


_USER_ROLE = int(Qt.ItemDataRole.UserRole)
KIND_ROLE = _USER_ROLE + 1
ENTRY_ID_ROLE = _USER_ROLE + 2
CATEGORY_ROLE = _USER_ROLE + 3
OUTCOME_ROLE = _USER_ROLE + 4
VALUE_ROLE = _USER_ROLE + 5
FINDING_ROLE = _USER_ROLE + 6
THUMBNAIL_ROLE = _USER_ROLE + 7
STATE_ROLE = _USER_ROLE + 8

_DISPLAY = int(Qt.ItemDataRole.DisplayRole)
_TOOLTIP = int(Qt.ItemDataRole.ToolTipRole)
_ACCESSIBLE = int(Qt.ItemDataRole.AccessibleTextRole)

NO_FINDINGS_TEXT = "Nada escondido nesta foto."
TECH_HEADER_TEXT = "Detalhes técnicos"

# Spec 4.5's names for the states whose text comes from neither a result nor
# an error. "Analisado" only reaches the accessible text: on screen, an
# analyzed file with findings shows its category tags instead.
_STATE_NAMES: dict[EntryState, str] = {
    EntryState.QUEUED: "Na fila",
    EntryState.ANALYZING: "Analisando…",
    EntryState.ANALYZED: "Analisado",
    EntryState.CLEANING: "Limpando…",
}

_ITEM_FLAGS = Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled


class Summary(NamedTuple):
    """Counts behind the summary line and the clean button (spec 5.2)."""

    total: int
    with_findings: int
    unsupported: int
    errors: int
    cleanable: int
    verified: int
    warnings: int
    failed: int
    busy: int


_EMPTY_SUMMARY = Summary(0, 0, 0, 0, 0, 0, 0, 0, 0)


def status_text(entry: FileEntry) -> str:
    """The short state shown on the right of a file row (spec 5.2 and 5.5).

    Empty for an analyzed file with findings: its category tags already
    say what the row is about.
    """
    state = entry.state
    if state in (EntryState.UNSUPPORTED, EntryState.ERROR, EntryState.FAILED) and entry.error is not None:
        return error_title(entry.error)
    if state in (EntryState.VERIFIED, EntryState.WARNINGS) and entry.result is not None:
        return result_title(entry.result)
    if state is EntryState.ANALYZED:
        return "" if entry.categories else "Nada escondido"
    return _STATE_NAMES.get(state, "")


def finding_text(finding: Finding) -> str:
    """"label — detail — where", leaving out the parts the engine did not report."""
    return " — ".join(part for part in (finding.label, finding.detail, finding.where) if part)


def _one_line(text: str) -> str:
    """Collapse every whitespace run (newlines included) into one space.

    Notes, labels and details can carry text read from the file itself; a
    row shows one line, and one note must stay one row.
    """
    return " ".join(text.split())


def _single_line_finding(finding: Finding) -> Finding:
    detail = _one_line(finding.detail) if finding.detail else finding.detail
    return replace(finding, label=_one_line(finding.label), detail=detail)


def _result_lines(result: CleanResult) -> list[str]:
    tidy = replace(
        result,
        notes=[_one_line(note) for note in result.notes],
        verdict=replace(result.verdict, notes=[_one_line(note) for note in result.verdict.notes]),
    )
    return result_explanation(tidy, Path(result.dst).parent).split("\n")


def _error_lines(exc: BaseException) -> list[str]:
    if isinstance(exc, VerificationFailedError):
        verdict = exc.verdict
        exc = VerificationFailedError(
            replace(verdict, violations=[_single_line_finding(f) for f in verdict.violations])
        )
        return error_explanation(exc).split("\n")
    return [_one_line(error_explanation(exc))]


def _summary_key(entry: FileEntry) -> tuple[EntryState, bool]:
    return entry.state, bool(entry.report is not None and entry.report.findings)


def _set(item: QStandardItem, role: int, value: Any) -> None:
    """`item.setData`, skipping the `dataChanged` when nothing changes.

    (A stored str-based enum comes back as its plain value, which still
    compares equal to the member.)
    """
    if item.data(role) != value:
        item.setData(value, role)


class FileListModel(QStandardItemModel):
    """The list of files on the "Limpar" page, one tree per file (see the module docstring)."""

    summaryChanged = Signal(object)  # Summary

    def __init__(self, mode: Mode = Mode.PRIVACY, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.setColumnCount(1)
        self._mode = Mode(mode)
        self._next_id = 1
        self._entries: dict[int, FileEntry] = {}  # in row order
        self._items: dict[int, QStandardItem] = {}
        self._keys: dict[str, int] = {}  # path_key -> entry id
        self._counts: Counter[tuple[EntryState, bool]] = Counter()
        self._last_summary = _EMPTY_SUMMARY

    # -- entries --------------------------------------------------------------

    def add_paths(self, paths: Iterable[str | Path]) -> list[int]:
        """Add a QUEUED entry per new path; duplicates (by `siftool.batch.path_key`) are ignored.

        Returns the ids to dispatch: every new entry's, plus those of
        entries already in the list and still QUEUED (e.g. after a
        cancel), each once, in the order given.
        """
        ids: list[int] = []
        returned: set[int] = set()
        new_items: list[QStandardItem] = []
        for raw in paths:
            key = path_key(raw)
            entry_id = self._keys.get(key)
            if entry_id is not None:
                if self._entries[entry_id].state is EntryState.QUEUED and entry_id not in returned:
                    ids.append(entry_id)
                    returned.add(entry_id)
                continue

            entry_id = self._next_id
            self._next_id += 1
            entry = FileEntry(Path(os.path.abspath(raw)))
            item = QStandardItem()
            item.setFlags(_ITEM_FLAGS)
            item.setData(RowKind.FILE, KIND_ROLE)
            item.setData(entry_id, ENTRY_ID_ROLE)
            item.setData(entry.path.name, _DISPLAY)
            item.setData(str(entry.path), _TOOLTIP)
            self._fill_file_item(item, entry)

            self._entries[entry_id] = entry
            self._items[entry_id] = item
            self._keys[key] = entry_id
            self._counts[_summary_key(entry)] += 1
            new_items.append(item)
            ids.append(entry_id)
            returned.add(entry_id)

        if new_items:
            self.invisibleRootItem().appendRows(new_items)
            self._emit_summary()
        return ids

    def entry(self, entry_id: int) -> FileEntry:
        """The entry with `entry_id` (`KeyError` if it is not in the list)."""
        return self._entries[entry_id]

    def entry_ids(self) -> list[int]:
        """Every entry id, in row order."""
        return list(self._entries)

    def index_for(self, entry_id: int) -> QModelIndex:
        """The file row of `entry_id`; an invalid index if it is not in the list."""
        item = self._items.get(entry_id)
        return item.index() if item is not None else QModelIndex()

    def entry_id_at(self, index: QModelIndex | QPersistentModelIndex) -> int | None:
        """The id of the entry owning `index`, whatever the row kind; None for an invalid index."""
        if not index.isValid() or index.model() is not self:
            return None
        while index.parent().isValid():
            index = index.parent()
        entry_id = index.data(ENTRY_ID_ROLE)
        return entry_id if entry_id in self._entries else None

    def remove_entry(self, entry_id: int) -> None:
        """Remove `entry_id`'s row (any state; the page decides what may be removed)."""
        entry = self._entries.pop(entry_id, None)
        if entry is None:
            return
        item = self._items.pop(entry_id)
        del self._keys[path_key(entry.path)]
        self._counts[_summary_key(entry)] -= 1
        self.removeRow(item.row())
        self._emit_summary()

    def clear_entries(self) -> None:
        """Remove every row. Ids keep counting up: a late result never lands on a new entry."""
        if not self._entries:
            return
        self.removeRows(0, self.rowCount())
        self._entries.clear()
        self._items.clear()
        self._keys.clear()
        self._counts.clear()
        self._emit_summary()

    def cleanable_ids(self) -> list[int]:
        """The entries the next "Limpar N fotos" cleans, in row order."""
        return [entry_id for entry_id, entry in self._entries.items() if entry.cleanable]

    # -- mode -----------------------------------------------------------------

    def mode(self) -> Mode:
        return self._mode

    def set_mode(self, mode: Mode) -> None:
        """Recompute "sai"/"fica" for every entry without a saved copy; no new analysis."""
        mode = Mode(mode)
        if mode is self._mode:
            return
        self._mode = mode
        for entry_id, entry in self._entries.items():
            if entry.result is not None:
                continue
            file_item = self._items[entry_id]
            for row in range(file_item.rowCount()):
                child = file_item.child(row)
                if child.data(KIND_ROLE) == RowKind.CATEGORY:
                    self._fill_category_item(child, Category(child.data(CATEGORY_ROLE)), entry)

    # -- state transitions (driven by the page and by `JobRunner` signals) -----

    def mark_analyzing(self, entry_id: int) -> None:
        self._transition(entry_id, FileEntry.start_analysis)

    def apply_report(self, entry_id: int, report: Report) -> None:
        self._transition(entry_id, lambda entry: entry.finish_analysis(report))

    def apply_analysis_error(self, entry_id: int, exc: BaseException) -> None:
        self._transition(entry_id, lambda entry: entry.fail_analysis(exc))

    def mark_cleaning(self, entry_id: int) -> None:
        self._transition(entry_id, FileEntry.start_clean)

    def apply_result(self, entry_id: int, result: CleanResult) -> None:
        self._transition(entry_id, lambda entry: entry.finish_clean(result))

    def apply_clean_error(self, entry_id: int, exc: BaseException) -> None:
        self._transition(entry_id, lambda entry: entry.fail_clean(exc))

    def revert_dropped(self, entry_id: int, job: str) -> None:
        """Undo `mark_analyzing` / `mark_cleaning` for a job `JobRunner` dropped before it started."""
        if job == "analyze":
            action = FileEntry.cancel_analysis
        elif job == "clean":
            action = FileEntry.cancel_clean
        else:
            raise ValueError(f"unknown job kind {job!r}; expected 'analyze' or 'clean'")
        self._transition(entry_id, action)

    def set_thumbnail(self, entry_id: int, image: QImage) -> None:
        """Store the preview as a `QPixmap` (GUI thread only); a null image means "no preview"."""
        item = self._items.get(entry_id)
        if item is None:
            return
        pixmap = None if image is None or image.isNull() else QPixmap.fromImage(image)
        item.setData(pixmap, THUMBNAIL_ROLE)

    # -- summary ----------------------------------------------------------------

    def summary(self) -> Summary:
        counts = self._counts

        def count(*states: EntryState) -> int:
            return sum(n for (state, _), n in counts.items() if state in states)

        return Summary(
            total=len(self._entries),
            with_findings=sum(n for (_, has_findings), n in counts.items() if has_findings),
            unsupported=count(EntryState.UNSUPPORTED),
            errors=count(EntryState.ERROR),
            cleanable=count(EntryState.ANALYZED),
            verified=count(EntryState.VERIFIED),
            warnings=count(EntryState.WARNINGS),
            failed=count(EntryState.FAILED),
            busy=count(EntryState.ANALYZING, EntryState.CLEANING),
        )

    def _emit_summary(self) -> None:
        summary = self.summary()
        if summary != self._last_summary:
            self._last_summary = summary
            self.summaryChanged.emit(summary)

    # -- internals ----------------------------------------------------------------

    def _transition(self, entry_id: int, action: Callable[[FileEntry], None]) -> None:
        entry = self._entries.get(entry_id)
        if entry is None:  # removed while its job was running: nothing to update
            return
        before = _summary_key(entry)
        action(entry)  # raises InvalidTransition on a wiring bug, before anything changes
        self._counts[before] -= 1
        self._counts[_summary_key(entry)] += 1
        item = self._items[entry_id]
        self._fill_file_item(item, entry)
        self._rebuild_children(entry_id, item, entry)
        self._emit_summary()

    def _fill_file_item(self, item: QStandardItem, entry: FileEntry) -> None:
        status = status_text(entry)
        names = [category_name(category) for category in entry.categories]
        state = status or _STATE_NAMES.get(entry.state, "")
        accessible = ", ".join(part for part in (entry.path.name, state, *names) if part)
        _set(item, STATE_ROLE, entry.state)
        _set(item, VALUE_ROLE, status)
        _set(item, CATEGORY_ROLE, tuple(entry.categories))
        _set(item, _ACCESSIBLE, accessible)

    def _rebuild_children(self, entry_id: int, item: QStandardItem, entry: FileEntry) -> None:
        if item.rowCount():
            item.removeRows(0, item.rowCount())
        rows = self._child_rows(entry_id, entry)
        if rows:
            item.appendRows(rows)

    def _child_rows(self, entry_id: int, entry: FileEntry) -> list[QStandardItem]:
        report = entry.report
        if report is None:
            if entry.error is None:  # queued or being analyzed: nothing to show yet
                return []
            return [self._note(entry_id, line) for line in _error_lines(entry.error)]

        rows: list[QStandardItem] = []
        categories = entry.categories
        for category in categories:
            item = self._child(RowKind.CATEGORY, entry_id, category_name(category))
            self._fill_category_item(item, category, entry)
            rows.append(item)
        if not categories:
            rows.append(self._note(entry_id, NO_FINDINGS_TEXT))

        if entry.state in (EntryState.VERIFIED, EntryState.WARNINGS) and entry.result is not None:
            rows.extend(self._note(entry_id, line) for line in _result_lines(entry.result))
        elif entry.state is EntryState.FAILED and entry.error is not None:
            rows.extend(self._note(entry_id, line) for line in _error_lines(entry.error))

        technical = list(report.findings)
        if entry.state is EntryState.FAILED and isinstance(entry.error, VerificationFailedError):
            technical.extend(entry.error.verdict.violations)
        if technical:
            header = self._child(RowKind.TECH_HEADER, entry_id, TECH_HEADER_TEXT)
            header.appendRows([self._tech(entry_id, finding) for finding in technical])
            rows.append(header)
        return rows

    def _fill_category_item(self, item: QStandardItem, category: Category, entry: FileEntry) -> None:
        name = category_name(category)
        value = category_value(category, entry.report.findings if entry.report is not None else [])
        if entry.result is not None:  # a saved copy exists: say what it kept
            kept = any(finding.category is category for finding in entry.result.kept)
            outcome = Outcome.KEPT if kept else Outcome.REMOVED
        else:
            outcome = category_outcome(category, self._mode)
        _set(item, CATEGORY_ROLE, category)
        _set(item, VALUE_ROLE, value)
        _set(item, OUTCOME_ROLE, outcome)
        _set(item, _TOOLTIP, f"{name}: {value}")
        _set(item, _ACCESSIBLE, f"{name}: {value}, {outcome.value}")

    def _child(self, kind: RowKind, entry_id: int, text: str) -> QStandardItem:
        item = QStandardItem(text)
        item.setFlags(_ITEM_FLAGS)
        item.setData(kind, KIND_ROLE)
        item.setData(entry_id, ENTRY_ID_ROLE)
        item.setData(text, _ACCESSIBLE)
        return item

    def _note(self, entry_id: int, text: str) -> QStandardItem:
        item = self._child(RowKind.NOTE, entry_id, text)
        item.setData(text, _TOOLTIP)
        return item

    def _tech(self, entry_id: int, finding: Finding) -> QStandardItem:
        text = _one_line(finding_text(finding))
        item = self._child(RowKind.TECH, entry_id, finding.label)
        item.setData(finding.detail, VALUE_ROLE)
        item.setData(finding, FINDING_ROLE)
        item.setData(text, _TOOLTIP)
        item.setData(text, _ACCESSIBLE)
        return item
