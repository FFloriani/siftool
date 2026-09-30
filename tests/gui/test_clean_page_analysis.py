"""Tests for the "Limpar" page: dropping, analysis and the list (spec sections 5.2 and 6; Task 10).

Every page (fixtures in `conftest.py`) gets a config file under `tmp_path`
(never `%APPDATA%`) and a real `JobRunner` running the real engine over the
synthetic fixtures. Waiting is done with `qtbot.waitUntil` / `waitSignal`; the
only slow job is a monkeypatched `siftool.inspect` that blocks on a
`threading.Event`.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

import pytest
from PySide6.QtCore import QMimeData, QPoint, QPointF, Qt, QTimer, QUrl
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QImage
from PySide6.QtWidgets import QApplication, QFileDialog

import siftool
from fixtures.make_fixtures import PNG_SIGNATURE, make_jpeg, make_png, make_webp, write
from siftool.errors import UnsupportedFormatError
from siftool.model import Category, Mode

from siftool_gui.drop_area import FILE_FILTER
from siftool_gui.entries import EntryState
from siftool_gui.model import CATEGORY_ROLE, KIND_ROLE, OUTCOME_ROLE, STATE_ROLE, THUMBNAIL_ROLE, VALUE_ROLE, RowKind
from siftool_gui.pages import clean_page as clean_page_module
from siftool_gui.pages.clean_page import CleanPage
from siftool_gui.presentation import Outcome, mode_description, summary_text
from siftool_gui.settings import load_config
from siftool_gui.workers import JobRunner

TIMEOUT = 10_000  # ms

HEIC_BYTES = b"\x00\x00\x00\x18ftypheic" + b"\x00" * 64
MP4_BYTES = b"\x00\x00\x00\x18ftypisom" + b"\x00" * 64


def _names(page: CleanPage) -> list[str]:
    return [page.model.entry(entry_id).path.name for entry_id in page.model.entry_ids()]


def _states(page: CleanPage) -> dict[str, EntryState]:
    return {page.model.entry(i).path.name: page.model.entry(i).state for i in page.model.entry_ids()}


def _id_named(page: CleanPage, name: str) -> int:
    (entry_id,) = [i for i in page.model.entry_ids() if page.model.entry(i).path.name == name]
    return entry_id


def _child_rows(page: CleanPage, entry_id: int, kind: RowKind):
    parent = page.model.index_for(entry_id)
    rows = [page.model.index(row, 0, parent) for row in range(page.model.rowCount(parent))]
    return [index for index in rows if index.data(KIND_ROLE) == kind]


def _outcome(page: CleanPage, entry_id: int, category: Category) -> Outcome:
    for index in _child_rows(page, entry_id, RowKind.CATEGORY):
        if index.data(CATEGORY_ROLE) == category:
            return Outcome(index.data(OUTCOME_ROLE))
    raise AssertionError(f"no {category} row")


def _mime(*urls: QUrl) -> QMimeData:
    mime = QMimeData()
    mime.setUrls(list(urls))
    return mime


# -- dropping and analysis -------------------------------------------------------


def test_drop_files_adds_rows_and_analyzes(page, qtbot, photos, settle):
    jpg = write(photos, "a.jpg", make_jpeg())
    png = write(photos, "b.png", make_png())

    page.drop_area.pathsDropped.emit([str(jpg), str(png)])
    settle(page, 2)

    assert _states(page) == {"a.jpg": EntryState.ANALYZED, "b.png": EntryState.ANALYZED}
    assert page.model.summary().total == 2
    assert page.summary_label.text() == summary_text(2, 2, 0)
    for entry_id in page.model.entry_ids():  # the preview arrives too (decoded by Qt, in memory)
        assert page.model.index_for(entry_id).data(THUMBNAIL_ROLE) is not None


def test_real_drop_event_with_urls(page, qtbot, photos, settle):
    jpg = write(photos, "a.jpg", make_jpeg())
    mime = _mime(QUrl.fromLocalFile(str(jpg)), QUrl("https://example.com/c.jpg"))
    actions = Qt.DropAction.CopyAction | Qt.DropAction.MoveAction

    enter = QDragEnterEvent(QPoint(5, 5), actions, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    enter.setDropAction(Qt.DropAction.MoveAction)  # what Explorer proposes for a drag within one drive
    page.drop_area.dragEnterEvent(enter)
    assert enter.isAccepted()
    assert enter.dropAction() == Qt.DropAction.CopyAction
    assert page.drop_area.is_highlighted()

    drop = QDropEvent(QPointF(5, 5), actions, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    drop.setDropAction(Qt.DropAction.MoveAction)
    with qtbot.waitSignal(page.drop_area.pathsDropped, timeout=TIMEOUT) as blocker:
        page.drop_area.dropEvent(drop)

    assert blocker.args == [[str(jpg)]]  # only the local file; the web link is left out
    # Never a move: Windows Explorer deletes the source of a move the target accepts.
    assert drop.isAccepted() and drop.dropAction() == Qt.DropAction.CopyAction
    assert not page.drop_area.is_highlighted()
    settle(page, 1)
    assert _states(page) == {"a.jpg": EntryState.ANALYZED}


def test_a_drag_that_only_offers_a_move_is_refused(page, photos):
    jpg = write(photos, "a.jpg", make_jpeg())
    mime = _mime(QUrl.fromLocalFile(str(jpg)))
    enter = QDragEnterEvent(QPoint(5, 5), Qt.DropAction.MoveAction, mime, Qt.MouseButton.LeftButton,
                            Qt.KeyboardModifier.NoModifier)

    page.drop_area.dragEnterEvent(enter)

    assert not enter.isAccepted()  # accepting it would let the source delete the original


def test_drag_without_local_files_is_refused(page):
    mime = _mime(QUrl("https://example.com/c.jpg"))  # the event only points at it: keep it alive
    enter = QDragEnterEvent(QPoint(5, 5), Qt.DropAction.CopyAction, mime, Qt.MouseButton.LeftButton,
                            Qt.KeyboardModifier.NoModifier)
    page.drop_area.dragEnterEvent(enter)

    assert not enter.isAccepted()
    assert not page.drop_area.is_highlighted()


def test_drop_on_the_list_area_is_accepted_too(page, qtbot, photos, settle):
    jpg = write(photos, "a.jpg", make_jpeg())
    mime = _mime(QUrl.fromLocalFile(str(jpg)))
    drop = QDropEvent(QPointF(5, 5), Qt.DropAction.CopyAction | Qt.DropAction.MoveAction, mime,
                      Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    drop.setDropAction(Qt.DropAction.MoveAction)

    page.dropEvent(drop)

    assert drop.dropAction() == Qt.DropAction.CopyAction
    settle(page, 1)
    assert _names(page) == ["a.jpg"]


def test_drop_accented_names(page, qtbot, photos, settle):
    name = "fotó 26 de set., ção.png"
    path = write(photos, name, make_png())

    # Through a real drop, so the name survives the QUrl round trip too.
    mime = _mime(QUrl.fromLocalFile(str(path)))
    drop = QDropEvent(QPointF(5, 5), Qt.DropAction.CopyAction, mime, Qt.MouseButton.LeftButton,
                      Qt.KeyboardModifier.NoModifier)
    page.drop_area.dropEvent(drop)
    settle(page, 1)

    (entry_id,) = page.model.entry_ids()
    index = page.model.index_for(entry_id)
    assert index.data(Qt.ItemDataRole.DisplayRole) == name
    assert index.data(Qt.ItemDataRole.ToolTipRole) == str(path)
    assert page.model.entry(entry_id).path == path
    assert index.data(STATE_ROLE) is EntryState.ANALYZED
    assert index.data(THUMBNAIL_ROLE) is not None


def test_drop_folder_skips_previous_outputs(page, qtbot, tmp_path, settle):
    folder = tmp_path / "viagem, set. 2026"
    (folder / "dia 2").mkdir(parents=True)
    photo = write(folder, "a.jpg", make_jpeg())
    write(folder, "a_clean.jpg", make_jpeg())
    write(folder, "a_clean (2).jpg", make_jpeg())
    write(folder / "dia 2", "b.png", make_png())
    write(folder / "dia 2", "b_clean.png", make_png())
    write(folder, "notas.txt", b"not a photo")

    # The folder and one of its own files together: still one row per photo.
    page.add_paths([str(folder), str(photo)])
    settle(page, 2)

    assert sorted(_names(page)) == ["a.jpg", "b.png"]
    assert set(_states(page).values()) == {EntryState.ANALYZED}


def test_heic_row_shows_not_supported_yet(page, qtbot, tmp_path, settle):
    folder = tmp_path / "celular"
    folder.mkdir()
    write(folder, "IMG_0001.HEIC", HEIC_BYTES)
    write(folder, "VID_0002.mp4", MP4_BYTES)
    write(folder, "IMG_0003.jpg", make_jpeg())

    page.add_paths([str(folder)])
    settle(page, 3)

    states = _states(page)
    assert states == {
        "IMG_0001.HEIC": EntryState.UNSUPPORTED,
        "VID_0002.mp4": EntryState.UNSUPPORTED,
        "IMG_0003.jpg": EntryState.ANALYZED,  # the others are not held back
    }
    for entry_id in page.model.entry_ids():
        entry = page.model.entry(entry_id)
        if entry.state is not EntryState.UNSUPPORTED:
            continue
        index = page.model.index_for(entry_id)
        assert index.data(VALUE_ROLE) == "Ainda não suportado"
        notes = _child_rows(page, entry_id, RowKind.NOTE)
        assert [note.data(Qt.ItemDataRole.DisplayRole) for note in notes] == [
            "Fotos HEIC e vídeos chegam numa próxima versão."
        ]
    assert page.summary_label.text() == summary_text(3, 1, 2)


def test_summary_line_tells_formats_that_are_coming_from_files_with_a_problem(page, photos, settle):
    # Final review: a .txt or a .gif is not "ainda não suportada" -- no next version opens it -- and a
    # damaged file is a problem too; only HEIC and video are "coming".
    page.add_paths([
        str(write(photos, "a.jpg", make_jpeg())),
        str(write(photos, "b.heic", HEIC_BYTES)),
        str(write(photos, "c.mp4", MP4_BYTES)),
        str(write(photos, "d.gif", b"GIF89a" + b"\x00" * 32)),
        str(write(photos, "notas.txt", b"not a photo")),
        str(write(photos, "e.png", PNG_SIGNATURE + b"garbage" * 20)),
    ])
    settle(page, 6)

    assert _states(page) == {
        "a.jpg": EntryState.ANALYZED,
        "b.heic": EntryState.UNSUPPORTED,
        "c.mp4": EntryState.UNSUPPORTED,
        "d.gif": EntryState.UNSUPPORTED,
        "notas.txt": EntryState.UNSUPPORTED,
        "e.png": EntryState.ERROR,
    }
    assert page.summary_label.text() == (
        "6 fotos · 1 com informação escondida · 2 ainda não suportadas · 3 com problemas"
    )
    # ... and the rows say the same thing one by one.
    statuses = {
        page.model.entry(i).path.name: page.model.index_for(i).data(VALUE_ROLE) for i in page.model.entry_ids()
    }
    assert statuses["b.heic"] == statuses["c.mp4"] == "Ainda não suportado"
    assert statuses["d.gif"] == statuses["notas.txt"] == "Formato não suportado"
    assert statuses["e.png"] == "Arquivo danificado"

    for name in ("d.gif", "notas.txt", "e.png"):  # rows that go leave the counts
        page.model.remove_entry(_id_named(page, name))
    assert page.summary_label.text() == "3 fotos · 1 com informação escondida · 2 ainda não suportadas"


def test_a_video_says_not_supported_yet_without_being_read_whole(page, monkeypatch, photos, settle):
    # Spec 3, criterion 3; final review: `siftool.inspect` is what reads a file whole, and a video
    # (recognized from its first bytes) must be refused without it.
    handed_over: list[str] = []

    def inspect_must_not_run(path):
        handed_over.append(str(path))
        raise AssertionError(f"{path} was handed to the engine")

    monkeypatch.setattr(siftool, "inspect", inspect_must_not_run)
    clip = write(photos, "VID_0001.mp4", MP4_BYTES)

    page.add_paths([str(clip)])
    settle(page, 1)

    assert handed_over == []
    (entry_id,) = page.model.entry_ids()
    entry = page.model.entry(entry_id)
    assert entry.state is EntryState.UNSUPPORTED
    assert isinstance(entry.error, UnsupportedFormatError)
    assert page.model.index_for(entry_id).data(VALUE_ROLE) == "Ainda não suportado"
    assert page.summary_label.text() == "1 foto · 1 ainda não suportada"


def test_a_huge_video_says_not_supported_yet_not_too_big(page, photos, settle):
    # Final review: the engine checks its 512 MB cap before it detects the format, so a 600 MB MP4
    # ended as "Grande demais" (an error), and was left out of the "ainda não suportadas" count.
    video = photos / "filme.mp4"
    try:
        with open(video, "wb") as f:
            f.write(MP4_BYTES)
            f.seek(600 * 1024 * 1024)
            f.write(b"\0")
    except OSError as exc:
        video.unlink(missing_ok=True)
        pytest.skip(f"the file system refuses a 600 MB file: {exc}")
    try:
        assert video.stat().st_size > 512 * 1024 * 1024

        page.add_paths([str(video)])
        settle(page, 1)

        (entry_id,) = page.model.entry_ids()
        assert page.model.entry(entry_id).state is EntryState.UNSUPPORTED
        assert page.model.index_for(entry_id).data(VALUE_ROLE) == "Ainda não suportado"
        assert page.summary_label.text() == "1 foto · 1 ainda não suportada"
    finally:
        video.unlink(missing_ok=True)  # 600 MB must not linger in pytest's temp folders


# -- mode ---------------------------------------------------------------------------


def test_initial_mode_comes_from_the_config(make_page, tmp_path):
    cfg = load_config(tmp_path / "other.json")
    cfg.set(cfg.defaultMode, "maximum")

    page = make_page(cfg)

    assert page.model.mode() is Mode.MAXIMUM
    assert page.mode_selector.currentRouteKey() == "maximum"
    assert page.mode_description.text() == mode_description(Mode.MAXIMUM)


def test_mode_switch_updates_outcomes_and_description(page, qtbot, photos, settle):
    jpg = write(photos, "a.jpg", make_jpeg())
    page.add_paths([str(jpg)])
    settle(page, 1)
    (entry_id,) = page.model.entry_ids()

    assert page.mode_selector.currentRouteKey() == "privacy"
    assert page.mode_description.text() == mode_description(Mode.PRIVACY)
    assert _outcome(page, entry_id, Category.DEVICE) is Outcome.REMOVED
    assert _outcome(page, entry_id, Category.APPEARANCE) is Outcome.KEPT

    page.mode_selector.widget("photographer").click()  # as the user does it
    assert page.model.mode() is Mode.PHOTOGRAPHER
    assert page.mode_description.text() == mode_description(Mode.PHOTOGRAPHER)
    assert _outcome(page, entry_id, Category.DEVICE) is Outcome.KEPT

    page.set_mode(Mode.MAXIMUM)
    assert page.mode_selector.currentRouteKey() == "maximum"
    assert page.mode_description.text() == mode_description(Mode.MAXIMUM)
    assert _outcome(page, entry_id, Category.APPEARANCE) is Outcome.REMOVED
    assert page.model.entry(entry_id).state is EntryState.ANALYZED  # no new analysis


# -- summary, compact drop area, removing ---------------------------------------------


def test_summary_text_and_compact_drop_area(page, qtbot, photos, settle):
    assert not page.drop_area.is_compact()
    assert not page.summary_label.isVisibleTo(page)
    assert not page.view.isVisibleTo(page)
    assert not page.clean_button.isEnabled()

    heic = write(photos, "b.heic", HEIC_BYTES)
    plain = write(photos, "c.webp", make_webp("simple"))  # nothing hidden in it
    page.add_paths([str(write(photos, "a.jpg", make_jpeg())), str(heic), str(plain)])
    settle(page, 3)

    assert page.drop_area.is_compact()
    assert page.summary_label.isVisibleTo(page)
    assert page.view.isVisibleTo(page)
    assert page.summary_label.text() == "3 fotos · 1 com informação escondida · 1 ainda não suportada"
    assert page.clear_button.isVisibleTo(page) and page.clear_button.isEnabled()

    page.clear_list()
    assert not page.drop_area.is_compact()
    assert not page.summary_label.isVisibleTo(page)


def test_delete_key_removes_selected_row(page, qtbot, photos, settle):
    page.add_paths([str(write(photos, "a.jpg", make_jpeg())), str(write(photos, "b.png", make_png()))])
    settle(page, 2)
    first, second = page.model.entry_ids()

    child = page.model.index(0, 0, page.model.index_for(first))  # a detail row selects its whole file
    page.view.setCurrentIndex(child)
    qtbot.keyClick(page.view, Qt.Key.Key_Delete)

    assert page.model.entry_ids() == [second]
    assert page.summary_label.text() == summary_text(1, 1, 0)


def test_remove_selected_keeps_nothing_it_should_not(page, qtbot, photos, settle):
    page.add_paths([str(write(photos, "a.jpg", make_jpeg())), str(write(photos, "b.png", make_png()))])
    settle(page, 2)

    page.view.selectAll()
    page.remove_selected()

    assert page.model.entry_ids() == []
    assert not page.drop_area.is_compact()


def test_clear_list(page, qtbot, photos, settle):
    page.add_paths([str(write(photos, "a.jpg", make_jpeg())), str(write(photos, "b.png", make_png()))])
    settle(page, 2)

    page.clear_button.click()

    assert page.model.rowCount() == 0
    assert page.model.summary().total == 0


def test_clear_list_waits_while_photos_are_being_analyzed(page, qtbot, monkeypatch, photos, settle):
    release = threading.Event()
    real_inspect = siftool.inspect

    def slow_inspect(path):
        release.wait(TIMEOUT / 1000)
        return real_inspect(path)

    monkeypatch.setattr(siftool, "inspect", slow_inspect)
    page.add_paths([str(write(photos, "a.jpg", make_jpeg()))])
    assert page.model.summary().busy == 1

    assert not page.clear_button.isEnabled()
    page.clear_list()
    assert page.model.summary().total == 1  # nothing is cleared under a running analysis

    release.set()
    settle(page, 1)
    assert page.clear_button.isEnabled()
    page.clear_list()
    assert page.model.summary().total == 0


def test_an_entry_being_analyzed_can_be_removed(page, qtbot, monkeypatch, photos):
    # Spec 4.5: "Remover da lista" works in every state except Limpando.
    release = threading.Event()
    real_inspect = siftool.inspect

    def slow_inspect(path):
        release.wait(TIMEOUT / 1000)
        return real_inspect(path)

    monkeypatch.setattr(siftool, "inspect", slow_inspect)
    page.add_paths([str(write(photos, "a.jpg", make_jpeg()))])
    (entry_id,) = page.model.entry_ids()
    assert page.model.entry(entry_id).state is EntryState.ANALYZING

    page.view.setCurrentIndex(page.model.index_for(entry_id))
    page.remove_selected()
    assert page.model.entry_ids() == []

    release.set()  # the late report lands nowhere
    qtbot.waitUntil(lambda: not page.runner.busy(), timeout=TIMEOUT)
    assert page.model.entry_ids() == []


# -- the add buttons ---------------------------------------------------------------


def test_add_buttons_open_the_dialogs(page, qtbot, monkeypatch, photos, tmp_path, settle):
    jpg = write(photos, "a.jpg", make_jpeg())
    folder = tmp_path / "pasta"
    folder.mkdir()
    write(folder, "b.png", make_png())
    calls: list[tuple] = []

    def fake_open_files(parent, caption, directory, filters):
        calls.append(("files", caption, filters))
        return [str(jpg).replace(os.sep, "/")], filters.split(";;")[0]

    def fake_open_folder(parent, caption, directory):
        calls.append(("folder", caption))
        return str(folder).replace(os.sep, "/")

    monkeypatch.setattr(QFileDialog, "getOpenFileNames", fake_open_files)
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", fake_open_folder)

    page.drop_area.add_files_button.click()
    page.drop_area.add_folder_button.click()
    settle(page, 2)

    assert calls == [("files", "Adicionar fotos", FILE_FILTER), ("folder", "Adicionar pasta")]
    assert FILE_FILTER == "Imagens (*.jpg *.jpeg *.png *.webp *.heic *.heif);;Todos os arquivos (*.*)"
    assert sorted(_names(page)) == ["a.jpg", "b.png"]
    assert sorted(str(page.model.entry(i).path) for i in page.model.entry_ids()) == sorted(
        [str(jpg), str(folder / "b.png")]
    )


def test_cancelled_dialogs_add_nothing(page, monkeypatch):
    monkeypatch.setattr(QFileDialog, "getOpenFileNames", lambda *args: ([], ""))
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *args: "")

    page.drop_area.choose_files()
    page.drop_area.choose_folder()

    assert page.model.rowCount() == 0
    assert not page.runner.busy()


# -- the list ------------------------------------------------------------------------


def test_list_uses_the_file_delegate_and_hides_its_header(page):
    from siftool_gui.delegate import FileItemDelegate

    assert isinstance(page.view.itemDelegate(), FileItemDelegate)
    assert page.view.isHeaderHidden()
    assert page.view.isAnimated()
    assert not page.view.uniformRowHeights()


def test_click_or_enter_opens_the_details(page, qtbot, photos, settle):
    page.add_paths([str(write(photos, "a.jpg", make_jpeg()))])
    settle(page, 1)
    page.resize(900, 700)
    page.show()
    qtbot.waitExposed(page)
    (entry_id,) = page.model.entry_ids()
    index = page.model.index_for(entry_id)
    rect = page.view.visualRect(index)

    qtbot.mouseClick(page.view.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())
    assert page.view.isExpanded(index)
    qtbot.mouseClick(page.view.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())
    assert not page.view.isExpanded(index)

    # On the expand arrow (drawn 20-30 px into the row): one toggle, not two.
    arrow = QPoint(page.view.indentation() + 5, rect.center().y())
    qtbot.mouseClick(page.view.viewport(), Qt.MouseButton.LeftButton, pos=arrow)
    assert page.view.isExpanded(index)
    qtbot.mouseClick(page.view.viewport(), Qt.MouseButton.LeftButton, pos=arrow)
    assert not page.view.isExpanded(index)

    page.view.setCurrentIndex(index)
    qtbot.keyClick(page.view, Qt.Key.Key_Return)
    assert page.view.isExpanded(index)
    qtbot.keyClick(page.view, Qt.Key.Key_Return)
    assert not page.view.isExpanded(index)


def test_double_click_opens_the_details_once(page, qtbot, photos, settle):
    page.add_paths([str(write(photos, "a.jpg", make_jpeg()))])
    settle(page, 1)
    page.resize(900, 700)
    page.show()
    qtbot.waitExposed(page)
    (entry_id,) = page.model.entry_ids()
    index = page.model.index_for(entry_id)
    viewport, left = page.view.viewport(), Qt.MouseButton.LeftButton
    center = page.view.visualRect(index).center()

    # What Qt delivers for a double-click: press, release, double-click, release. The first
    # release opens the row; the rest of the double-click must not close it again.
    qtbot.mousePress(viewport, left, pos=center)
    qtbot.mouseRelease(viewport, left, pos=center)
    qtbot.mouseDClick(viewport, left, pos=center)
    qtbot.mouseRelease(viewport, left, pos=center)
    assert page.view.isExpanded(index)

    page.view.collapse(index)
    qtbot.wait(QApplication.doubleClickInterval() + 50)  # a new gesture, not the end of the last one
    qtbot.mouseDClick(viewport, left, pos=center)  # a double-click alone (as QTest sends it) toggles too
    assert page.view.isExpanded(index)


# -- staying responsive with many files -------------------------------------------------


def test_only_a_few_files_are_handed_to_the_runner_at_a_time(make_page, monkeypatch, photos, settle):
    release = threading.Event()
    real_inspect = siftool.inspect

    def slow_inspect(path):
        release.wait(TIMEOUT / 1000)
        return real_inspect(path)

    monkeypatch.setattr(siftool, "inspect", slow_inspect)
    page = make_page(runner=JobRunner(max_workers=1))
    paths = [write(photos, f"{number}.jpg", make_jpeg()) for number in range(5)]
    try:
        page.add_paths([str(path) for path in paths])

        ids = page.model.entry_ids()
        states = [page.model.entry(entry_id).state for entry_id in ids]
        # Twice the workers are in flight; the others wait in the page, truthfully "Na fila".
        assert states == [EntryState.ANALYZING] * 2 + [EntryState.QUEUED] * 3
        assert page.model.index_for(ids[-1]).data(VALUE_ROLE) == "Na fila"
        assert page.busy()
        assert not page.clear_button.isEnabled()
    finally:
        release.set()
    settle(page, 5)
    assert set(_states(page).values()) == {EntryState.ANALYZED}


def test_cancel_during_a_scan_stops_the_files_it_found(make_page, qtbot, tmp_path, monkeypatch):
    # Review of delivery C: "Cancelar" right after the first batch of a folder walk reached the
    # page used to list that batch and analyze all of it anyway.
    folder = tmp_path / "muitas fotos"
    folder.mkdir()
    jpeg = make_jpeg()
    for number in range(450):
        write(folder, f"{number:03d}.jpg", jpeg)
    inspected: list[str] = []
    real_inspect = siftool.inspect

    def counted_inspect(path):
        inspected.append(str(path))
        return real_inspect(path)

    monkeypatch.setattr(siftool, "inspect", counted_inspect)
    page = make_page(runner=JobRunner(max_workers=1))
    with qtbot.waitSignal(page.runner.pathsFound, timeout=TIMEOUT):  # the walk found files...
        page.add_paths([str(folder)])
    listed = len(page.model.entry_ids())

    page.cancel()  # ... and "Cancelar" before the page has listed and handed them out
    started = len(inspected)
    qtbot.waitUntil(lambda: not page.busy(), timeout=TIMEOUT)
    qtbot.wait(100)  # time for anything that would still be dispatched

    assert len(inspected) <= started + 1  # at most one analysis the pool had already picked up
    assert len(page.model.entry_ids()) == listed  # what was found but not listed yet went with the walk
    states = {page.model.entry(i).state for i in page.model.entry_ids()}
    assert EntryState.ANALYZING not in states
    assert not page.progress_bar.isVisibleTo(page)


def test_cancel_during_a_slow_folder_walk_ends_the_walk_at_once(make_page, slow_walk, qtbot, tmp_path, photos,
                                                                settle):
    # Final review: a walk over a whole user profile took 96.8 s, and "Cancelar" only flagged it to
    # stop: "Procurando fotos…" and "Cancelar" stayed on screen and "Limpar lista" stayed off until
    # the walk ended -- so the button seemed to do nothing.
    page = make_page()
    page.add_paths([str(write(photos, "a.jpg", make_jpeg()))])  # one row, so "Limpar lista" has a job
    settle(page, 1)
    folder = tmp_path / "perfil inteiro"
    folder.mkdir()
    write(folder, "b.jpg", make_jpeg())  # what the walk would find, if its result leaked through
    scan_finished: list[int] = []
    page.runner.scanFinished.connect(lambda: scan_finished.append(1))

    page.add_paths([str(folder)])
    assert slow_walk.started.wait(TIMEOUT / 1000)
    assert page.progress_label.text() == "Procurando fotos…"
    assert page.progress_bar.isVisibleTo(page) and page.cancel_button.isVisibleTo(page)
    assert not page.clear_button.isEnabled()

    page.cancel()  # "Cancelar" -- the walk is still blocked in its thread

    assert not page.progress_bar.isVisibleTo(page)
    assert not page.progress_label.isVisibleTo(page)
    assert not page.cancel_button.isVisibleTo(page)
    assert page.clear_button.isEnabled()
    assert scan_finished == [1]
    assert not page.busy()
    page.clear_button.click()
    assert page.model.entry_ids() == []

    slow_walk.release.set()  # the walk unwinds and finds b.jpg: nobody listens any more
    assert page.runner.wait_for_done(TIMEOUT)
    QApplication.processEvents()
    qtbot.waitUntil(lambda: not page.busy(), timeout=TIMEOUT)

    assert page.model.entry_ids() == []  # no row was added
    assert scan_finished == [1]  # and no second "scanFinished"
    assert not page.progress_bar.isVisibleTo(page)


def test_a_slow_folder_walk_cancelled_on_the_runner_ends_for_the_page_too(make_page, slow_walk, qtbot, tmp_path,
                                                                          photos, settle):
    # The window's runner is shared: a cancel that does not come through the page ends the walk for
    # it as well, one turn of the event loop later (the page applies what the runner reports in turns).
    page = make_page()
    page.add_paths([str(write(photos, "a.jpg", make_jpeg()))])
    settle(page, 1)
    folder = tmp_path / "perfil inteiro"
    folder.mkdir()
    write(folder, "b.jpg", make_jpeg())
    page.add_paths([str(folder)])
    assert slow_walk.started.wait(TIMEOUT / 1000)
    assert page.progress_bar.isVisibleTo(page) and not page.clear_button.isEnabled()

    page.runner.cancel()

    # A turn of the event loop -- not the seconds the blocked walk would take to give up by itself.
    qtbot.waitUntil(lambda: not page.progress_bar.isVisibleTo(page), timeout=2_000)
    assert not page.cancel_button.isVisibleTo(page)
    assert page.clear_button.isEnabled()
    slow_walk.release.set()
    assert page.runner.wait_for_done(TIMEOUT)
    QApplication.processEvents()
    qtbot.waitUntil(lambda: not page.busy(), timeout=TIMEOUT)
    assert len(page.model.entry_ids()) == 1  # a.jpg only: b.jpg, found late, was not listed


def test_a_failing_result_does_not_stall_the_queue(make_page, qtbot, photos, monkeypatch):
    # Review of delivery C: an exception while applying a result skipped handing out more work.
    page = make_page(runner=JobRunner(max_workers=1))  # two files go to the runner, the third waits
    monkeypatch.setattr(page.runner, "thumbnail", lambda *args: None)  # no later preview to unstick it
    paths = [write(photos, f"{name}.jpg", make_jpeg()) for name in "abc"]
    real_apply = page.model.apply_report

    def apply_report(entry_id, report):
        if Path(report.path).name != "c.jpg":
            raise RuntimeError("a bug while showing a result")
        real_apply(entry_id, report)

    monkeypatch.setattr(page.model, "apply_report", apply_report)
    with qtbot.capture_exceptions() as exceptions:
        page.add_paths([str(path) for path in paths])
        last = page.model.entry_ids()[-1]
        qtbot.waitUntil(lambda: page.model.entry(last).state is EntryState.ANALYZED, timeout=TIMEOUT)

    assert len(exceptions) == 2  # reported, not swallowed -- and the third photo still got its turn


def test_results_are_applied_a_few_per_turn_of_the_event_loop(page, qtbot, monkeypatch):
    # A thousand results must not be applied back to back: painting, timers and input get
    # their turn. With a clock that charges 5 ms per reading, 8 ms buys two results a turn.
    class Clock:
        now = 0.0

        def perf_counter(self) -> float:
            self.now += 0.005
            return self.now

    monkeypatch.setattr(clean_page_module, "time", Clock())
    applied: list[int] = []
    monkeypatch.setattr(page.model, "set_thumbnail", lambda entry_id, image: applied.append(entry_id))
    seen: list[int] = []
    probe = QTimer()  # any other user of the event loop
    probe.setInterval(0)
    probe.timeout.connect(lambda: seen.append(len(applied)))
    probe.start()

    for number in range(7):
        page.runner.thumbnailReady.emit(number, QImage())
    assert applied == []  # nothing is applied inside the runner's own signal
    assert page.busy()

    qtbot.waitUntil(lambda: len(applied) == 7, timeout=TIMEOUT)
    probe.stop()
    assert applied == list(range(7))  # in the order reported
    assert max(after - before for before, after in zip(seen, seen[1:])) <= 2
