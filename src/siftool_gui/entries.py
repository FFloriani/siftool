"""Per-file state machine (spec sections 4.3-4.5). Deliberately Qt-free.

`FileEntry` tracks one dropped/added file through analysis and cleaning.
Every transition is a named method; calling one from the wrong `state`
raises `InvalidTransition` instead of silently corrupting the entry, so
`model.py` (delivery B) can drive it straight from worker signals without
re-deriving these rules -- and so a bug in that wiring fails loudly.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path

from siftool.errors import UnsupportedFormatError
from siftool.model import Category, CleanResult, Report, Status

from siftool_gui.presentation import CATEGORY_ORDER


class EntryState(Enum):
    """Where one `FileEntry` is in its analyze-then-clean lifecycle (spec 4.5)."""

    QUEUED = auto()
    ANALYZING = auto()
    ANALYZED = auto()
    UNSUPPORTED = auto()
    ERROR = auto()
    CLEANING = auto()
    VERIFIED = auto()
    WARNINGS = auto()
    FAILED = auto()


class InvalidTransition(ValueError):
    """Raised when a `FileEntry` method is called from the wrong `state`."""


# States a (re-)clean may start from (spec 4.5: "Limpar" / "Limpar de novo").
_CLEAN_SOURCE_STATES = (EntryState.ANALYZED, EntryState.VERIFIED, EntryState.WARNINGS, EntryState.FAILED)


@dataclass(eq=False)
class FileEntry:
    """One file's path plus its current place in the analyze/clean lifecycle."""

    path: Path
    state: EntryState = EntryState.QUEUED
    report: Report | None = None
    result: CleanResult | None = None
    error: BaseException | None = None

    def _transition(self, *valid_from: EntryState, action: str) -> None:
        if self.state not in valid_from:
            raise InvalidTransition(f"cannot {action} while {self.state.name} ({self.path})")

    def start_analysis(self) -> None:
        """QUEUED -> ANALYZING."""
        self._transition(EntryState.QUEUED, action="start_analysis")
        self.state = EntryState.ANALYZING

    def finish_analysis(self, report: Report) -> None:
        """ANALYZING -> ANALYZED, recording `report`."""
        self._transition(EntryState.ANALYZING, action="finish_analysis")
        self.report = report
        self.state = EntryState.ANALYZED

    def fail_analysis(self, exc: BaseException) -> None:
        """ANALYZING -> UNSUPPORTED (`UnsupportedFormatError`) or ERROR (anything else)."""
        self._transition(EntryState.ANALYZING, action="fail_analysis")
        self.error = exc
        self.state = EntryState.UNSUPPORTED if isinstance(exc, UnsupportedFormatError) else EntryState.ERROR

    def cancel_analysis(self) -> None:
        """ANALYZING -> QUEUED."""
        self._transition(EntryState.ANALYZING, action="cancel_analysis")
        self.state = EntryState.QUEUED

    def start_clean(self) -> None:
        """{ANALYZED, VERIFIED, WARNINGS, FAILED} -> CLEANING; clears `result`/`error`."""
        self._transition(*_CLEAN_SOURCE_STATES, action="start_clean")
        self.result = None
        self.error = None
        self.state = EntryState.CLEANING

    def finish_clean(self, result: CleanResult) -> None:
        """CLEANING -> VERIFIED or WARNINGS, by `result.verdict.status`."""
        self._transition(EntryState.CLEANING, action="finish_clean")
        self.result = result
        self.state = EntryState.VERIFIED if result.verdict.status is Status.VERIFIED else EntryState.WARNINGS

    def fail_clean(self, exc: BaseException) -> None:
        """CLEANING -> FAILED."""
        self._transition(EntryState.CLEANING, action="fail_clean")
        self.error = exc
        self.state = EntryState.FAILED

    def cancel_clean(self) -> None:
        """CLEANING -> ANALYZED (regardless of which finished state the clean started from)."""
        self._transition(EntryState.CLEANING, action="cancel_clean")
        self.state = EntryState.ANALYZED

    @property
    def categories(self) -> list[Category]:
        """Distinct categories among `report.findings`, in `CATEGORY_ORDER`; `[]` without a report."""
        if self.report is None:
            return []
        present = {finding.category for finding in self.report.findings}
        return [category for category in CATEGORY_ORDER if category in present]

    @property
    def cleanable(self) -> bool:
        """Whether this entry belongs in the next bulk "Limpar N fotos"."""
        return self.state is EntryState.ANALYZED

    @property
    def busy(self) -> bool:
        """Whether a background task currently owns this entry."""
        return self.state in (EntryState.ANALYZING, EntryState.CLEANING)
