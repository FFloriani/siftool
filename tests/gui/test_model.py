"""Tests for `siftool_gui.model.FileListModel` (spec sections 4.3-4.5 and 5.2; Task 8).

Reports come from the real engine over the synthetic fixtures (`siftool.inspect`),
or are built by hand where a test needs an exact shape (results, violations).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from PySide6.QtCore import QModelIndex, Qt
from PySide6.QtGui import QColor, QImage, QPixmap

import siftool
from fixtures.make_fixtures import make_jpeg, make_png, write
from siftool.errors import (
    CorruptFileError,
    DestinationExistsError,
    UnsafeFileError,
    UnsupportedFormatError,
    VerificationFailedError,
)
from siftool.model import Category, CleanResult, Finding, Mode, Report, Status, Verdict

from siftool_gui.entries import EntryState, InvalidTransition
from siftool_gui.model import (
    CATEGORY_ROLE,
    ENTRY_ID_ROLE,
    FINDING_ROLE,
    KIND_ROLE,
    OUTCOME_ROLE,
    STATE_ROLE,
    THUMBNAIL_ROLE,
    VALUE_ROLE,
    FileListModel,
    RowKind,
    Summary,
)
from siftool_gui.presentation import CATEGORY_ORDER, Outcome, category_name, category_value, summary_text


def _children(model: FileListModel, parent: QModelIndex) -> list[QModelIndex]:
    return [model.index(row, 0, parent) for row in range(model.rowCount(parent))]


def _kinds(model, parent) -> list[RowKind]:
    return [index.data(KIND_ROLE) for index in _children(model, parent)]


def _of_kind(model, parent, kind) -> list[QModelIndex]:
    return [index for index in _children(model, parent) if index.data(KIND_ROLE) is kind]


def _texts(indexes) -> list[str]:
    return [index.data(Qt.ItemDataRole.DisplayRole) for index in indexes]


def _analyzed(model: FileListModel, path: Path, report: Report | None = None) -> int:
    (entry_id,) = model.add_paths([path])
    model.mark_analyzing(entry_id)
    model.apply_report(entry_id, report if report is not None else siftool.inspect(path))
    return entry_id


def _report(path: Path, *findings: Finding, notes: list[str] | None = None) -> Report:
    return Report(path=str(path), format="jpeg", findings=list(findings), essential_digest="0" * 64,
                  notes=notes or [])


GPS = Finding(Category.LOCATION, "GPSLatitude", "37.7749", "APP1/GPS")
ICC = Finding(Category.APPEARANCE, "ICC profile", "Display P3", "APP2:ICC", 144)
C2PA = Finding(Category.AI_PROVENANCE, "C2PA label", None, "RESERVED:0xF0")


@pytest.fixture
def model(qtbot):
    return FileListModel()


# -- adding and deduplicating ------------------------------------------------


def test_add_paths_dedupes_normalized(model, monkeypatch, tmp_path):
    photo = write(tmp_path, "Foto.jpg", make_jpeg())
    monkeypatch.chdir(tmp_path)
    variants = [photo, str(photo), str(photo).replace("\\", "/"), "Foto.jpg", Path(".") / "Foto.jpg"]
    if os.path.normcase("A") == "a":  # the OS folds case (Windows): so must the list
        variants += [str(photo).upper(), str(photo).lower()]

    ids = model.add_paths(variants)

    assert model.rowCount() == 1
    assert ids == [1]
    assert model.entry(1).path == photo
    assert model.entry(1).path.is_absolute()
    assert model.add_paths([photo]) == [1]  # still QUEUED: returned again, to be re-dispatched


def test_add_paths_returns_existing_queued_ids(model, tmp_path):
    a = write(tmp_path, "a.jpg", make_jpeg())
    b = write(tmp_path, "b.png", make_png())
    c = write(tmp_path, "c.png", make_png())

    first = model.add_paths([a, b])
    model.mark_analyzing(first[0])  # a is busy now, b stays QUEUED (e.g. its job was dropped)

    again = model.add_paths([a, b, c, b])

    assert first == [1, 2]
    assert again == [2, 3]  # b (still queued) and the new c; a is already being analyzed
    assert model.entry_ids() == [1, 2, 3]
    assert model.rowCount() == 3


def test_ids_are_never_reused(model, tmp_path):
    a = write(tmp_path, "a.jpg", make_jpeg())
    (first,) = model.add_paths([a])
    model.remove_entry(first)
    (second,) = model.add_paths([a])
    assert second != first


# -- child rows ---------------------------------------------------------------


def test_report_builds_category_rows_in_order_with_outcomes(model, tmp_path):
    jpg = write(tmp_path, "a.jpg", make_jpeg())
    report = siftool.inspect(jpg)
    entry_id = _analyzed(model, jpg, report)
    parent = model.index_for(entry_id)

    present = {finding.category for finding in report.findings}
    expected = [category for category in CATEGORY_ORDER if category in present]
    rows = _of_kind(model, parent, RowKind.CATEGORY)

    assert Category.APPEARANCE in present and Category.LOCATION in present
    assert [Category(index.data(CATEGORY_ROLE)) for index in rows] == expected
    assert _texts(rows) == [category_name(category) for category in expected]
    assert [index.data(VALUE_ROLE) for index in rows] == [category_value(c, report.findings) for c in expected]
    outcomes = {Category(index.data(CATEGORY_ROLE)): Outcome(index.data(OUTCOME_ROLE)) for index in rows}
    assert outcomes[Category.APPEARANCE] is Outcome.KEPT
    assert all(outcome is Outcome.REMOVED for category, outcome in outcomes.items()
               if category is not Category.APPEARANCE)
    # Categories first, then the technical details (no result yet, so no notes).
    assert _kinds(model, parent) == [RowKind.CATEGORY] * len(expected) + [RowKind.TECH_HEADER]


def test_enum_roles_read_back_usable(model, tmp_path):
    jpg = write(tmp_path, "a.jpg", make_jpeg())
    entry_id = _analyzed(model, jpg)
    parent = model.index_for(entry_id)
    row = _of_kind(model, parent, RowKind.CATEGORY)[0]  # LOCATION comes first

    # PySide6 hands str-based enums back as their plain value (see the model's docstring).
    assert row.data(CATEGORY_ROLE) == Category.LOCATION
    assert Category(row.data(CATEGORY_ROLE)) is Category.LOCATION
    assert category_name(row.data(CATEGORY_ROLE)) == "Localização"  # works as a table key
    assert row.data(OUTCOME_ROLE) == Outcome.REMOVED
    assert Outcome(row.data(OUTCOME_ROLE)) is Outcome.REMOVED
    # Everything else comes back as the very object stored.
    assert type(row.data(KIND_ROLE)) is RowKind
    assert type(parent.data(STATE_ROLE)) is EntryState
    assert all(type(category) is Category for category in parent.data(CATEGORY_ROLE))


def test_set_mode_updates_outcomes_and_emits_data_changed(model, qtbot, tmp_path):
    jpg = write(tmp_path, "a.jpg", make_jpeg())
    entry_id = _analyzed(model, jpg)
    parent = model.index_for(entry_id)

    def outcomes():
        return {Category(index.data(CATEGORY_ROLE)): Outcome(index.data(OUTCOME_ROLE))
                for index in _of_kind(model, parent, RowKind.CATEGORY)}

    changed: list[QModelIndex] = []
    model.dataChanged.connect(lambda top_left, bottom_right, roles=(): changed.append(top_left))

    model.set_mode(Mode.PHOTOGRAPHER)
    assert model.mode() is Mode.PHOTOGRAPHER
    assert outcomes()[Category.DEVICE] is Outcome.KEPT
    assert outcomes()[Category.CAMERA_SETTINGS] is Outcome.KEPT
    assert outcomes()[Category.LOCATION] is Outcome.REMOVED
    device_row = next(index for index in _of_kind(model, parent, RowKind.CATEGORY)
                      if index.data(CATEGORY_ROLE) == Category.DEVICE)
    assert device_row in changed
    location_row = next(index for index in _of_kind(model, parent, RowKind.CATEGORY)
                        if index.data(CATEGORY_ROLE) == Category.LOCATION)
    assert location_row not in changed  # still "sai": no needless repaint

    model.set_mode(Mode.MAXIMUM)
    assert set(outcomes().values()) == {Outcome.REMOVED}


def test_no_findings_note(model, tmp_path):
    jpg = write(tmp_path, "plain.jpg", make_jpeg())
    entry_id = _analyzed(model, jpg, _report(jpg))
    parent = model.index_for(entry_id)

    assert _kinds(model, parent) == [RowKind.NOTE]
    assert _texts(_children(model, parent)) == ["Nada escondido nesta foto."]
    assert parent.data(VALUE_ROLE) == "Nada escondido"


def test_unsupported_and_error_notes(model, tmp_path):
    heic, broken = model.add_paths([tmp_path / "a.heic", tmp_path / "b.jpg"])
    for entry_id in (heic, broken):
        model.mark_analyzing(entry_id)
    model.apply_analysis_error(heic, UnsupportedFormatError("HEIC"))
    model.apply_analysis_error(broken, CorruptFileError("truncated JPEG"))

    heic_index = model.index_for(heic)
    broken_index = model.index_for(broken)
    assert heic_index.data(STATE_ROLE) is EntryState.UNSUPPORTED
    assert heic_index.data(VALUE_ROLE) == "Ainda não suportado"
    assert _texts(_children(model, heic_index)) == ["Fotos HEIC e vídeos chegam numa próxima versão."]
    assert broken_index.data(STATE_ROLE) is EntryState.ERROR
    assert broken_index.data(VALUE_ROLE) == "Arquivo danificado"
    assert _texts(_children(model, broken_index)) == [
        "O arquivo tem uma estrutura inválida; limpar assim não seria seguro."
    ]
    assert _kinds(model, heic_index) == [RowKind.NOTE]


def _clean_result(src: Path, status: Status, notes: list[str], mode: Mode = Mode.PRIVACY,
                  removed=(GPS,), kept=(ICC,)) -> CleanResult:
    dst = src.with_name(src.stem + "_clean" + src.suffix)
    return CleanResult(src=str(src), dst=str(dst), mode=mode, removed=list(removed), kept=list(kept),
                       verdict=Verdict(status, [], list(notes)), notes=list(notes))


def test_result_rows_verified_warnings_failed(model, tmp_path):
    photos = [tmp_path / name for name in ("ok.jpg", "warn.jpg", "fail.jpg")]
    ids = [_analyzed(model, photo, _report(photo, GPS, ICC)) for photo in photos]
    for entry_id in ids:
        model.mark_cleaning(entry_id)
        assert model.index_for(entry_id).data(VALUE_ROLE) == "Limpando…"

    note = "Unrecognized structure kept: RESERVED:0xF0"
    model.apply_result(ids[0], _clean_result(photos[0], Status.VERIFIED, []))
    model.apply_result(ids[1], _clean_result(photos[1], Status.WARNINGS, [note]))
    model.apply_clean_error(ids[2], VerificationFailedError(Verdict(Status.FAILED, [C2PA], [note])))

    ok, warn, fail = (model.index_for(entry_id) for entry_id in ids)
    assert [index.data(STATE_ROLE) for index in (ok, warn, fail)] == [
        EntryState.VERIFIED, EntryState.WARNINGS, EntryState.FAILED]
    assert [index.data(VALUE_ROLE) for index in (ok, warn, fail)] == ["Verificado", "Com ressalvas", "Não limpou"]

    assert _texts(_of_kind(model, ok, RowKind.NOTE)) == [
        f"Cópia salva em {tmp_path}. Nada do que o modo proíbe ficou no arquivo."
    ]
    # Portuguese only (spec 3, criterion 4): the engine's note and labels are technical details.
    assert _texts(_of_kind(model, warn, RowKind.NOTE)) == [
        "A cópia foi salva, mas havia uma estrutura que o Siftool não conhece e precisou ficar para a "
        "imagem não quebrar.",
    ]
    assert _texts(_of_kind(model, fail, RowKind.NOTE)) == [
        "A verificação encontrou dados que não saíram, por isso nenhuma cópia foi salva.",
        "Marca de IA: ainda estava na cópia",
    ]
    for index in (warn, fail):
        (header,) = _of_kind(model, index, RowKind.TECH_HEADER)
        assert _texts(_children(model, header)).count(note) == 1  # once, even when both lists carry it

    # A saved copy: its categories, then where it went. A copy with reservations, or none: the
    # reason first, right under the file (ruling P11(b)). The technical details always come last.
    assert _kinds(model, ok) == [RowKind.CATEGORY, RowKind.CATEGORY, RowKind.NOTE, RowKind.TECH_HEADER]
    assert _kinds(model, warn) == [RowKind.NOTE, RowKind.CATEGORY, RowKind.CATEGORY, RowKind.TECH_HEADER]
    assert _kinds(model, fail) == [RowKind.NOTE, RowKind.NOTE, RowKind.CATEGORY, RowKind.CATEGORY,
                                   RowKind.TECH_HEADER]


def test_engine_texts_with_embedded_newlines_stay_one_row_each(model, tmp_path):
    photo = tmp_path / "fail.jpg"
    entry_id = _analyzed(model, photo, _report(photo, GPS))
    model.mark_cleaning(entry_id)
    sneaky = Finding(Category.TEXT, "Comment", "line one\nline two", "COM")
    verdict = Verdict(Status.FAILED, [sneaky, C2PA, C2PA], ["note one\nnote two"])
    model.apply_clean_error(entry_id, VerificationFailedError(verdict))

    index = model.index_for(entry_id)
    notes = _texts(_of_kind(model, index, RowKind.NOTE))
    assert notes[1:] == ["Textos e comentários: ainda estava na cópia", "Marca de IA: ainda estava na cópia"]
    (header,) = _of_kind(model, index, RowKind.TECH_HEADER)
    rows = _children(model, header)
    assert [row.data(FINDING_ROLE) for row in rows[:4]] == [GPS, sneaky, C2PA, C2PA]
    assert _texts(rows[4:]) == ["note one note two"]  # the verdict's note: one row, on one line
    for row in rows:
        assert "\n" not in row.data(Qt.ItemDataRole.ToolTipRole)


def test_race_and_unexpected_errors_read_in_portuguese(model, tmp_path):
    photo = tmp_path / "a.jpg"
    exists_id = _analyzed(model, photo, _report(photo, GPS))
    model.mark_cleaning(exists_id)
    model.apply_clean_error(exists_id, DestinationExistsError(f"{tmp_path / 'a_clean.jpg'} already exists"))
    (broken_id,) = model.add_paths([tmp_path / "b.jpg"])
    model.mark_analyzing(broken_id)
    model.apply_analysis_error(broken_id, ValueError("unpack requires a buffer of 4 bytes"))

    exists = model.index_for(exists_id)
    assert exists.data(VALUE_ROLE) == "Já existe um arquivo com esse nome"
    assert _kinds(model, exists)[0] is RowKind.NOTE  # the reason first (ruling P11(b))
    assert _texts(_of_kind(model, exists, RowKind.NOTE)) == [
        "A cópia não foi salva para não sobrescrever um arquivo existente."
    ]
    broken = model.index_for(broken_id)
    assert broken.data(VALUE_ROLE) == "Não foi possível limpar"
    assert _texts(_children(model, broken)) == ["O Siftool encontrou um problema com este arquivo."]
    for index in (exists, broken, *_of_kind(model, exists, RowKind.NOTE), *_children(model, broken)):
        for role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole, Qt.ItemDataRole.AccessibleTextRole,
                     VALUE_ROLE):
            text = str(index.data(role) or "")
            assert "already exists" not in text and "unpack" not in text


def test_outcomes_after_cleaning_describe_the_copy(model, tmp_path):
    photo = tmp_path / "a.jpg"
    device = Finding(Category.DEVICE, "Model", "Pixel 8", "APP1/IFD0")
    iso = Finding(Category.CAMERA_SETTINGS, "ISO", "100", "XMP")  # dropped with its whole block
    entry_id = _analyzed(model, photo, _report(photo, GPS, device, iso))
    model.mark_cleaning(entry_id)
    model.apply_result(entry_id, _clean_result(photo, Status.VERIFIED, [], mode=Mode.PHOTOGRAPHER,
                                               removed=(GPS, iso), kept=(device,)))

    def outcomes():
        rows = _of_kind(model, model.index_for(entry_id), RowKind.CATEGORY)
        return {Category(index.data(CATEGORY_ROLE)): Outcome(index.data(OUTCOME_ROLE)) for index in rows}

    # What the saved copy really kept -- not what PHOTOGRAPHER would allow in general.
    expected = {Category.LOCATION: Outcome.REMOVED, Category.DEVICE: Outcome.KEPT,
                Category.CAMERA_SETTINGS: Outcome.REMOVED}
    assert outcomes() == expected
    model.set_mode(Mode.MAXIMUM)  # a later mode switch must not rewrite the copy's story
    assert outcomes() == expected

    model.mark_cleaning(entry_id)  # "Limpar de novo": back to predicting by the current mode
    assert outcomes() == {Category.LOCATION: Outcome.REMOVED, Category.DEVICE: Outcome.REMOVED,
                          Category.CAMERA_SETTINGS: Outcome.REMOVED}


def test_tech_rows_carry_findings(model, tmp_path):
    jpg = write(tmp_path, "a.jpg", make_jpeg())
    report = siftool.inspect(jpg)
    entry_id = _analyzed(model, jpg, report)
    parent = model.index_for(entry_id)

    (header,) = _of_kind(model, parent, RowKind.TECH_HEADER)
    assert header.data(Qt.ItemDataRole.DisplayRole) == "Detalhes técnicos"
    rows = _children(model, header)
    assert [index.data(KIND_ROLE) for index in rows] == [RowKind.TECH] * len(report.findings)
    assert [index.data(FINDING_ROLE) for index in rows] == report.findings
    assert _texts(rows) == [finding.label for finding in report.findings]
    assert [index.data(VALUE_ROLE) for index in rows] == [finding.detail for finding in report.findings]


def test_failed_tech_rows_include_violations(model, tmp_path):
    photo = tmp_path / "fail.jpg"
    entry_id = _analyzed(model, photo, _report(photo, GPS, ICC))
    model.mark_cleaning(entry_id)
    model.apply_clean_error(entry_id, VerificationFailedError(Verdict(Status.FAILED, [C2PA], [])))

    (header,) = _of_kind(model, model.index_for(entry_id), RowKind.TECH_HEADER)
    assert [index.data(FINDING_ROLE) for index in _children(model, header)] == [GPS, ICC, C2PA]


# -- entry ids, summary, removal ------------------------------------------------


def test_entry_id_at_resolves_any_row_kind(model, tmp_path):
    jpg = write(tmp_path, "a.jpg", make_jpeg())
    model.add_paths([write(tmp_path, "z.png", make_png())])
    entry_id = _analyzed(model, jpg)
    parent = model.index_for(entry_id)
    (header,) = _of_kind(model, parent, RowKind.TECH_HEADER)

    for index in [parent, *_children(model, parent), *_children(model, header)]:
        assert model.entry_id_at(index) == entry_id
        assert index.data(ENTRY_ID_ROLE) == entry_id
    assert model.entry_id_at(QModelIndex()) is None
    assert not model.index_for(999).isValid()


def test_summary_counts_and_signal(model, qtbot, tmp_path):
    summaries: list[Summary] = []
    model.summaryChanged.connect(summaries.append)
    assert model.summary() == Summary(0, 0, 0, 0, 0, 0, 0, 0, 0)

    paths = [tmp_path / f"{name}.jpg" for name in "abcdefg"]
    ids = model.add_paths(paths)
    assert summaries[-1] == model.summary() == Summary(total=7, with_findings=0, unsupported=0, errors=0,
                                                       cleanable=0, verified=0, warnings=0, failed=0, busy=0)
    for entry_id in ids:
        model.mark_analyzing(entry_id)
    assert summaries[-1].busy == 7

    a, b, c, d, e, f, g = ids
    model.apply_report(a, _report(paths[0], GPS))
    model.apply_report(b, _report(paths[1]))  # nothing hidden
    model.apply_report(c, _report(paths[2], GPS, ICC))
    model.apply_report(d, _report(paths[3], ICC))
    model.apply_analysis_error(e, UnsupportedFormatError("HEIC"))
    model.apply_analysis_error(f, CorruptFileError("bad"))
    for entry_id in (c, d):
        model.mark_cleaning(entry_id)
    model.apply_result(c, _clean_result(paths[2], Status.VERIFIED, []))

    assert model.summary() == Summary(total=7, with_findings=3, unsupported=1, errors=1, cleanable=2,
                                      verified=1, warnings=0, failed=0, busy=2, problems=1)
    assert model.cleanable_ids() == [a, b]
    assert summaries[-1] == model.summary()

    model.apply_clean_error(d, VerificationFailedError(Verdict(Status.FAILED, [C2PA], [])))
    model.apply_report(g, _report(paths[6], GPS))
    assert summaries[-1] == Summary(total=7, with_findings=4, unsupported=1, errors=1, cleanable=3,
                                    verified=1, warnings=0, failed=1, busy=0, problems=1)

    count = len(summaries)
    model.set_mode(Mode.MAXIMUM)  # no count changes: no new emission
    assert len(summaries) == count


def test_summary_counts_formats_that_are_coming_apart_from_problems(model, tmp_path):
    # Final review: only HEIC and video are "ainda não suportada"; the other unsupported formats
    # (txt, PDF, GIF...) and the files with an error are "com problema".
    paths = [tmp_path / name for name in ("a.jpg", "b.heic", "c.mp4", "d.gif", "e.txt", "f.pdf", "g.png", "h.jpg")]
    ids = model.add_paths(paths)
    for entry_id in ids:
        model.mark_analyzing(entry_id)
    a, heic, mp4, gif, txt, pdf, corrupt, huge = ids
    model.apply_report(a, _report(paths[0], GPS))
    model.apply_analysis_error(heic, UnsupportedFormatError("HEIC"))
    model.apply_analysis_error(mp4, UnsupportedFormatError("MP4/MOV"))
    model.apply_analysis_error(gif, UnsupportedFormatError("GIF"))
    model.apply_analysis_error(txt, UnsupportedFormatError(None))
    model.apply_analysis_error(pdf, UnsupportedFormatError("PDF"))
    model.apply_analysis_error(corrupt, CorruptFileError("bad"))
    model.apply_analysis_error(huge, UnsafeFileError("file too large for an image"))

    summary = model.summary()
    assert summary.unsupported == 2  # HEIC and MP4/MOV
    assert summary.errors == 2  # the corrupt file and the huge one
    assert summary.problems == 5  # ... plus GIF, txt and PDF, which are "Formato não suportado"
    assert summary.total == 8 and summary.with_findings == 1 and summary.cleanable == 1
    assert summary_text(summary.total, summary.with_findings, summary.unsupported, summary.problems) == (
        "8 fotos · 1 com informação escondida · 2 ainda não suportadas · 5 com problemas"
    )

    summaries: list[Summary] = []
    model.summaryChanged.connect(summaries.append)
    model.remove_entry(gif)  # a row that goes leaves the counts
    model.remove_entry(mp4)
    assert (summaries[-1].unsupported, summaries[-1].problems) == (1, 4)
    model.clear_entries()
    assert model.summary() == Summary(0, 0, 0, 0, 0, 0, 0, 0, 0, 0)


def test_revert_dropped(model, tmp_path):
    a, b = model.add_paths([tmp_path / "a.jpg", tmp_path / "b.jpg"])
    model.mark_analyzing(a)
    model.mark_analyzing(b)
    model.apply_report(b, _report(tmp_path / "b.jpg", GPS))
    model.mark_cleaning(b)

    model.revert_dropped(a, "analyze")
    model.revert_dropped(b, "clean")

    assert model.entry(a).state is EntryState.QUEUED
    assert model.entry(b).state is EntryState.ANALYZED
    assert model.summary().busy == 0
    assert model.add_paths([tmp_path / "a.jpg"]) == [a]  # queued again: ready to be re-dispatched
    with pytest.raises(ValueError):
        model.revert_dropped(a, "thumbnail")
    with pytest.raises(InvalidTransition):
        model.revert_dropped(a, "clean")


def test_remove_and_clear(model, qtbot, tmp_path):
    a, b, c = model.add_paths([tmp_path / "a.jpg", tmp_path / "b.jpg", tmp_path / "c.jpg"])
    summaries: list[Summary] = []
    model.summaryChanged.connect(summaries.append)

    model.remove_entry(b)
    assert model.entry_ids() == [a, c]
    assert model.rowCount() == 2
    assert _texts([model.index(row, 0) for row in range(2)]) == ["a.jpg", "c.jpg"]
    assert model.index_for(c).row() == 1
    assert summaries[-1].total == 2
    with pytest.raises(KeyError):
        model.entry(b)

    # A worker result for an entry removed meanwhile is ignored, never an error.
    model.mark_analyzing(b)
    model.apply_report(b, _report(tmp_path / "b.jpg"))
    model.apply_analysis_error(b, CorruptFileError("bad"))
    model.set_thumbnail(b, QImage(4, 4, QImage.Format.Format_ARGB32))
    model.revert_dropped(b, "analyze")

    assert model.add_paths([tmp_path / "b.jpg"]) == [4]  # a removed file can be added again
    model.clear_entries()
    assert model.rowCount() == 0
    assert model.entry_ids() == []
    assert model.summary() == Summary(0, 0, 0, 0, 0, 0, 0, 0, 0)
    assert summaries[-1] == model.summary()
    assert model.add_paths([tmp_path / "a.jpg"]) == [5]


def test_thumbnail_role_is_pixmap(model, tmp_path):
    (entry_id,) = model.add_paths([tmp_path / "a.jpg"])
    index = model.index_for(entry_id)
    assert index.data(THUMBNAIL_ROLE) is None

    image = QImage(20, 10, QImage.Format.Format_ARGB32)
    image.fill(QColor("tomato"))
    model.set_thumbnail(entry_id, image)

    pixmap = index.data(THUMBNAIL_ROLE)
    assert isinstance(pixmap, QPixmap)
    assert (pixmap.width(), pixmap.height()) == (20, 10)

    model.set_thumbnail(entry_id, QImage())  # decoding failed: back to the generic icon
    assert index.data(THUMBNAIL_ROLE) is None


def test_accessible_and_tooltip_roles(model, tmp_path):
    jpg = write(tmp_path, "fotó 26 de set., ção.jpg", make_jpeg())
    queued_id, entry_id = model.add_paths([tmp_path / "later.png", jpg])
    model.mark_analyzing(entry_id)
    model.apply_report(entry_id, _report(jpg, GPS, ICC))
    index = model.index_for(entry_id)

    assert index.data(Qt.ItemDataRole.DisplayRole) == "fotó 26 de set., ção.jpg"
    assert index.data(Qt.ItemDataRole.ToolTipRole) == str(jpg)
    assert index.data(Qt.ItemDataRole.AccessibleTextRole) == (
        "fotó 26 de set., ção.jpg, Analisado, Localização, Orientação e cores"
    )
    assert model.index_for(queued_id).data(Qt.ItemDataRole.AccessibleTextRole) == "later.png, Na fila"

    for child in _children(model, index):
        assert child.data(Qt.ItemDataRole.AccessibleTextRole)
    note_model = FileListModel()
    (note_id,) = note_model.add_paths([tmp_path / "x.jpg"])
    note_model.mark_analyzing(note_id)
    note_model.apply_analysis_error(note_id, CorruptFileError("bad"))
    (note,) = _children(note_model, note_model.index_for(note_id))
    assert note.data(Qt.ItemDataRole.ToolTipRole) == note.data(Qt.ItemDataRole.DisplayRole)
