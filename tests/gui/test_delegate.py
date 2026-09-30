"""Tests for `siftool_gui.delegate.FileItemDelegate` (spec section 5.2; Task 9).

Rows are painted straight into a `QImage` (no view), once per row kind and
per file state, in the light and then the dark theme. Pixel checks stay
coarse on purpose (a thumbnail shows up where it should, nothing paints a
blank row); the look itself is reviewed through screenshots.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QModelIndex, QRect, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QStyle, QStyleOptionViewItem
from qfluentwidgets import Theme, isDarkTheme, qconfig, setTheme

from siftool.errors import CorruptFileError, UnsupportedFormatError, VerificationFailedError
from siftool.model import Category, CleanResult, Finding, Mode, Report, Status, Verdict

from siftool_gui.delegate import (
    CHILD_ROW_HEIGHT,
    FILE_ROW_HEIGHT,
    MAX_TAGS,
    FileItemDelegate,
    fit_tags,
    status_icon,
)
from siftool_gui.entries import EntryState
from siftool_gui.model import KIND_ROLE, FileListModel, RowKind

FINDINGS = [
    Finding(Category.LOCATION, "GPSLatitude", "37.7749", "APP1/GPS"),
    Finding(Category.IDENTITY, "Artist", "Felipe", "APP1/IFD0"),
    Finding(Category.AI_PROVENANCE, "C2PA manifest", "generator: ChatGPT", "APP11:JUMBF"),
    Finding(Category.TIME, "DateTimeOriginal", "2026:09:26 03:30:37", "APP1/Exif"),
    Finding(Category.DEVICE, "Model", "Pixel 8", "APP1/IFD0"),
    Finding(Category.APPEARANCE, "ICC profile", "Display P3", "APP2:ICC"),
]
C2PA = Finding(Category.AI_PROVENANCE, "C2PA label", None, "RESERVED:0xF0")


def _report(path, findings=FINDINGS) -> Report:
    return Report(path=str(path), format="jpeg", findings=list(findings), essential_digest="0" * 64, notes=[])


def _result(path, status: Status, notes=()) -> CleanResult:
    return CleanResult(src=str(path), dst=str(path.with_name(path.stem + "_clean" + path.suffix)),
                       mode=Mode.PRIVACY, removed=FINDINGS[:-1], kept=FINDINGS[-1:],
                       verdict=Verdict(status, [], list(notes)), notes=list(notes))


def build_sample_model(tmp_path) -> tuple[FileListModel, dict[str, int]]:
    """One file row per state (two for ANALYZED: with and without findings)."""
    model = FileListModel()
    names = ["queued", "analyzing", "analyzed", "nothing", "unsupported", "error", "cleaning", "verified",
             "warnings", "failed"]
    paths = {name: tmp_path / f"{name}.jpg" for name in names}
    ids = dict(zip(names, model.add_paths(paths.values())))
    for name in names[1:]:
        model.mark_analyzing(ids[name])
    for name in ("analyzed", "cleaning", "verified", "warnings", "failed"):
        model.apply_report(ids[name], _report(paths[name]))
    model.apply_report(ids["nothing"], _report(paths["nothing"], findings=[]))
    model.apply_analysis_error(ids["unsupported"], UnsupportedFormatError("HEIC"))
    model.apply_analysis_error(ids["error"], CorruptFileError("truncated JPEG"))
    for name in ("cleaning", "verified", "warnings", "failed"):
        model.mark_cleaning(ids[name])
    model.apply_result(ids["verified"], _result(paths["verified"], Status.VERIFIED))
    model.apply_result(ids["warnings"], _result(paths["warnings"], Status.WARNINGS,
                                                ["Unrecognized structure kept: RESERVED:0xF0"]))
    model.apply_clean_error(ids["failed"], VerificationFailedError(Verdict(Status.FAILED, [C2PA], [])))
    return model, ids


def _rows(model: FileListModel, parent: QModelIndex = QModelIndex()) -> list[QModelIndex]:
    found = []
    for row in range(model.rowCount(parent)):
        index = model.index(row, 0, parent)
        found.append(index)
        found.extend(_rows(model, index))
    return found


def _paint(delegate: FileItemDelegate, index: QModelIndex, state=QStyle.StateFlag.State_Enabled) -> QImage:
    image = QImage(600, 64, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    option = QStyleOptionViewItem()
    option.state = state
    option.rect = QRect(0, 0, 600, delegate.sizeHint(option, index).height())
    painter = QPainter(image)
    try:
        delegate.paint(painter, option, index)
    finally:
        painter.end()
    return image


def _uniform(image: QImage) -> bool:
    data = bytes(image.constBits())
    return data == data[:4] * (len(data) // 4)


@pytest.fixture
def theme_guard():
    before = qconfig.get(qconfig.themeMode)
    yield
    setTheme(before)


def _apply_theme(theme: Theme) -> None:
    setTheme(theme)
    # Guard against a vacuous pass: the rows below must really be painted in `theme`.
    assert isDarkTheme() is (theme is Theme.DARK), "setTheme() did not switch the theme"


def test_size_hints_per_kind(qtbot, tmp_path):
    model, _ = build_sample_model(tmp_path)
    delegate = FileItemDelegate()
    option = QStyleOptionViewItem()
    heights: dict[RowKind, set[int]] = {}
    for index in _rows(model):
        heights.setdefault(index.data(KIND_ROLE), set()).add(delegate.sizeHint(option, index).height())

    assert (FILE_ROW_HEIGHT, CHILD_ROW_HEIGHT) == (64, 32)
    assert FileItemDelegate.FILE_ROW_HEIGHT == 64 and FileItemDelegate.CHILD_ROW_HEIGHT == 32
    assert heights == {
        RowKind.FILE: {64},
        RowKind.CATEGORY: {32},
        RowKind.NOTE: {32},
        RowKind.TECH_HEADER: {32},
        RowKind.TECH: {32},
    }


def test_paint_every_kind_and_state_in_both_themes(qtbot, tmp_path, theme_guard):
    model, ids = build_sample_model(tmp_path)
    thumbnail = QImage(96, 72, QImage.Format.Format_ARGB32)
    thumbnail.fill(QColor("seagreen"))
    model.set_thumbnail(ids["verified"], thumbnail)
    delegate = FileItemDelegate()
    rows = _rows(model)

    file_states = {index.data(KIND_ROLE): None for index in rows}
    assert set(file_states) == set(RowKind)
    painted_states = {model.entry(model.entry_id_at(index)).state for index in rows
                      if index.data(KIND_ROLE) is RowKind.FILE}
    assert painted_states == set(EntryState)

    looks = (
        QStyle.StateFlag.State_Enabled,
        QStyle.StateFlag.State_Enabled | QStyle.StateFlag.State_MouseOver,
        QStyle.StateFlag.State_Enabled | QStyle.StateFlag.State_Selected | QStyle.StateFlag.State_HasFocus,
        QStyle.StateFlag.State_Enabled | QStyle.StateFlag.State_HasFocus,
    )
    for theme in (Theme.LIGHT, Theme.DARK):
        _apply_theme(theme)
        for index in rows:
            for look in looks:
                image = _paint(delegate, index, look)
                assert not _uniform(image), (theme, index.data(KIND_ROLE), index.data(Qt.ItemDataRole.DisplayRole))


def test_light_and_dark_rows_differ(qtbot, tmp_path, theme_guard):
    model, ids = build_sample_model(tmp_path)
    delegate = FileItemDelegate()
    index = model.index_for(ids["verified"])

    _apply_theme(Theme.LIGHT)
    light = _paint(delegate, index)
    _apply_theme(Theme.DARK)
    dark = _paint(delegate, index)
    assert bytes(light.constBits()) != bytes(dark.constBits())


def test_thumbnail_is_drawn_when_present(qtbot, tmp_path, theme_guard):
    _apply_theme(Theme.LIGHT)
    model, ids = build_sample_model(tmp_path)
    delegate = FileItemDelegate()
    index = model.index_for(ids["analyzed"])

    def red_pixels(image: QImage) -> int:
        return sum(
            1 for x in range(0, 120) for y in range(8, 56)
            if (color := QColor.fromRgba(image.pixel(x, y))).red() > 200 and color.green() < 60
        )

    assert red_pixels(_paint(delegate, index)) == 0  # the generic photo icon, no preview yet
    red = QImage(96, 64, QImage.Format.Format_ARGB32)
    red.fill(QColor(255, 0, 0))
    model.set_thumbnail(ids["analyzed"], red)
    assert red_pixels(_paint(delegate, index)) > 40 * 40  # a 48 x 48 rounded, center-cropped square


def test_fit_tags_shows_at_most_three_and_counts_the_rest():
    names = ["Localização", "Dados pessoais", "Marca de IA", "Data e hora", "Aparelho"]

    def width(text: str) -> int:
        return 10 * len(text) + 16  # a fake pill: 10 px per character plus padding

    assert MAX_TAGS == 3
    assert fit_tags(names, 10_000, width) == (names[:3], 2)
    assert fit_tags(names[:2], 10_000, width) == (names[:2], 0)
    assert fit_tags([], 10_000, width) == ([], 0)

    # Narrower: fewer names, the rest folded into "+N" -- and always within the room given.
    room = width(names[0]) + 6 + width("+4")
    assert fit_tags(names, room, width) == (names[:1], 4)
    assert fit_tags(names, width("+5"), width) == ([], 5)
    assert fit_tags(names, width("+5") - 1, width) == ([], 0)  # not even "+N" fits: draw nothing


def test_every_outcome_state_has_an_icon_beside_its_text():
    # Spec 6: the state is never shown by color alone -- text (from the model) plus an icon.
    for state in (EntryState.VERIFIED, EntryState.WARNINGS, EntryState.FAILED, EntryState.ERROR,
                  EntryState.UNSUPPORTED, EntryState.ANALYZING, EntryState.CLEANING, EntryState.QUEUED):
        assert status_icon(state) is not None, state
    assert status_icon(EntryState.VERIFIED) != status_icon(EntryState.WARNINGS) != status_icon(EntryState.FAILED)
    assert status_icon(EntryState.ERROR) == status_icon(EntryState.FAILED)
