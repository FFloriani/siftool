"""Tests for `siftool_gui.entries.FileEntry` (spec sections 4.3-4.5; Task 5). No Qt."""

from __future__ import annotations

from pathlib import Path

import pytest

from siftool.errors import CorruptFileError, UnsupportedFormatError
from siftool.model import Category, CleanResult, Finding, Mode, Report, Status, Verdict

from siftool_gui.entries import EntryState, FileEntry, InvalidTransition


def _report(*categories: Category) -> Report:
    findings = [Finding(category, "label", "detail") for category in categories]
    return Report(path="a.jpg", format="JPEG", findings=findings, essential_digest="digest", notes=[])


def _result(status: Status) -> CleanResult:
    verdict = Verdict(status, [], [])
    return CleanResult(src="a.jpg", dst="a_clean.jpg", mode=Mode.PRIVACY, removed=[], kept=[], verdict=verdict)


def test_happy_path_transitions():
    entry = FileEntry(Path("a.jpg"))
    assert entry.state is EntryState.QUEUED
    assert entry.busy is False
    assert entry.cleanable is False

    entry.start_analysis()
    assert entry.state is EntryState.ANALYZING
    assert entry.busy is True
    assert entry.cleanable is False

    report = _report(Category.LOCATION)
    entry.finish_analysis(report)
    assert entry.state is EntryState.ANALYZED
    assert entry.report is report
    assert entry.busy is False
    assert entry.cleanable is True

    entry.start_clean()
    assert entry.state is EntryState.CLEANING
    assert entry.busy is True
    assert entry.cleanable is False

    result = _result(Status.VERIFIED)
    entry.finish_clean(result)
    assert entry.state is EntryState.VERIFIED
    assert entry.result is result
    assert entry.cleanable is False

    # Cleaning again (e.g. "Limpar de novo") can end in WARNINGS instead.
    entry.start_clean()
    assert entry.state is EntryState.CLEANING
    assert entry.result is None  # cleared by start_clean

    warned = _result(Status.WARNINGS)
    entry.finish_clean(warned)
    assert entry.state is EntryState.WARNINGS
    assert entry.result is warned


def test_unsupported_vs_error():
    unsupported = FileEntry(Path("a.heic"))
    unsupported.start_analysis()
    unsupported.fail_analysis(UnsupportedFormatError("HEIC"))
    assert unsupported.state is EntryState.UNSUPPORTED
    assert isinstance(unsupported.error, UnsupportedFormatError)

    corrupt = FileEntry(Path("b.jpg"))
    corrupt.start_analysis()
    corrupt.fail_analysis(CorruptFileError("bad structure"))
    assert corrupt.state is EntryState.ERROR

    os_error = FileEntry(Path("c.jpg"))
    os_error.start_analysis()
    os_error.fail_analysis(OSError("disk error"))
    assert os_error.state is EntryState.ERROR


@pytest.mark.parametrize(
    "start_state", [EntryState.ANALYZED, EntryState.VERIFIED, EntryState.WARNINGS, EntryState.FAILED]
)
def test_clean_again_from_finished_states(start_state):
    entry = FileEntry(
        Path("a.jpg"),
        state=start_state,
        result=_result(Status.VERIFIED),
        error=ValueError("stale error from a previous attempt"),
    )
    entry.start_clean()
    assert entry.state is EntryState.CLEANING
    assert entry.result is None
    assert entry.error is None


@pytest.mark.parametrize(
    "start_state, action",
    [
        (EntryState.QUEUED, "start_clean"),
        (EntryState.ERROR, "start_clean"),
        (EntryState.UNSUPPORTED, "start_clean"),
        (EntryState.ANALYZING, "start_clean"),
        (EntryState.CLEANING, "start_clean"),
        (EntryState.QUEUED, "finish_analysis"),
        (EntryState.ANALYZED, "finish_analysis"),
        (EntryState.QUEUED, "fail_analysis"),
        (EntryState.ANALYZED, "fail_analysis"),
        (EntryState.QUEUED, "cancel_analysis"),
        (EntryState.ANALYZED, "cancel_analysis"),
        (EntryState.ANALYZED, "start_analysis"),
        (EntryState.CLEANING, "start_analysis"),
        (EntryState.ANALYZED, "finish_clean"),
        (EntryState.QUEUED, "finish_clean"),
        (EntryState.ANALYZED, "fail_clean"),
        (EntryState.ANALYZING, "fail_clean"),
        (EntryState.ANALYZED, "cancel_clean"),
        (EntryState.VERIFIED, "cancel_clean"),
        (EntryState.QUEUED, "cancel_clean"),
    ],
)
def test_invalid_transitions_raise(start_state, action):
    entry = FileEntry(Path("a.jpg"), state=start_state)
    method = getattr(entry, action)

    with pytest.raises(InvalidTransition):
        if action == "finish_analysis":
            method(_report(Category.LOCATION))
        elif action == "finish_clean":
            method(_result(Status.VERIFIED))
        elif action in ("fail_analysis", "fail_clean"):
            method(ValueError("boom"))
        else:
            method()


def test_categories_distinct_in_display_order():
    entry = FileEntry(Path("a.jpg"))
    assert entry.categories == []

    entry.report = _report(Category.APPEARANCE, Category.LOCATION, Category.LOCATION, Category.TIME)
    assert entry.categories == [Category.LOCATION, Category.TIME, Category.APPEARANCE]


def test_cancel_reverts():
    entry = FileEntry(Path("a.jpg"))
    entry.start_analysis()
    entry.cancel_analysis()
    assert entry.state is EntryState.QUEUED

    entry.start_analysis()
    entry.finish_analysis(_report(Category.LOCATION))
    entry.start_clean()
    entry.cancel_clean()
    assert entry.state is EntryState.ANALYZED

    # cancel_clean always lands on ANALYZED, even re-cleaning from WARNINGS.
    entry.state = EntryState.WARNINGS
    entry.result = _result(Status.WARNINGS)
    entry.start_clean()
    entry.cancel_clean()
    assert entry.state is EntryState.ANALYZED
