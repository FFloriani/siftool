"""Tests for the "Limpar" page: dropping, analysis and the list (spec sections 5.2 and 6; Task 10).

Every page gets a config file under `tmp_path` (never `%APPDATA%`) and a real
`JobRunner` running the real engine over the synthetic fixtures. The
`make_page` fixture cancels and drains each page's runner at teardown, so no
pool thread outlives its test. Waiting is done with `qtbot.waitUntil` /
`waitSignal`; the only slow job is a monkeypatched `siftool.inspect` that
blocks on a `threading.Event`.
"""

from __future__ import annotations

import os
import threading

import pytest
from PySide6.QtCore import QMimeData, QPoint, QPointF, Qt, QUrl
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import QFileDialog

import siftool
from fixtures.make_fixtures import make_jpeg, make_png, make_webp, write
from siftool.model import Category, Mode

from siftool_gui.drop_area import FILE_FILTER
from siftool_gui.entries import EntryState
from siftool_gui.model import CATEGORY_ROLE, KIND_ROLE, OUTCOME_ROLE, STATE_ROLE, THUMBNAIL_ROLE, VALUE_ROLE, RowKind
from siftool_gui.pages.clean_page import CleanPage
from siftool_gui.presentation import Outcome, mode_description, summary_text
from siftool_gui.settings import load_config

TIMEOUT = 10_000  # ms

HEIC_BYTES = b"\x00\x00\x00\x18ftypheic" + b"\x00" * 64
MP4_BYTES = b"\x00\x00\x00\x18ftypisom" + b"\x00" * 64


@pytest.fixture
def config(tmp_path):
    return load_config(tmp_path / "cfg.json")


@pytest.fixture
def photos(tmp_path):
    folder = tmp_path / "fotos"
    folder.mkdir()
    return folder


@pytest.fixture
def make_page(qtbot, config):
    pages: list[CleanPage] = []

    def factory(cfg=None, runner=None) -> CleanPage:
        page = CleanPage(cfg if cfg is not None else config, runner)
        qtbot.addWidget(page)
        pages.append(page)
        return page

    yield factory
    for page in pages:
        page.runner.cancel()
        assert page.runner.wait_for_done(TIMEOUT)


@pytest.fixture
def page(make_page):
    return make_page()


def _names(page: CleanPage) -> list[str]:
    return [page.model.entry(entry_id).path.name for entry_id in page.model.entry_ids()]


def _states(page: CleanPage) -> dict[str, EntryState]:
    return {page.model.entry(i).path.name: page.model.entry(i).state for i in page.model.entry_ids()}


def _settle(qtbot, page: CleanPage, total: int) -> None:
    """Wait until `total` rows exist, none is being analyzed and the runner (thumbnails too) is idle."""

    def settled() -> bool:
        summary = page.model.summary()
        return summary.total == total and summary.busy == 0 and not page.runner.busy()

    qtbot.waitUntil(settled, timeout=TIMEOUT)


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


def test_drop_files_adds_rows_and_analyzes(page, qtbot, photos):
    jpg = write(photos, "a.jpg", make_jpeg())
    png = write(photos, "b.png", make_png())

    page.drop_area.pathsDropped.emit([str(jpg), str(png)])
    _settle(qtbot, page, 2)

    assert _states(page) == {"a.jpg": EntryState.ANALYZED, "b.png": EntryState.ANALYZED}
    assert page.model.summary().total == 2
    assert page.summary_label.text() == summary_text(2, 2, 0)
    for entry_id in page.model.entry_ids():  # the preview arrives too (decoded by Qt, in memory)
        assert page.model.index_for(entry_id).data(THUMBNAIL_ROLE) is not None


def test_real_drop_event_with_urls(page, qtbot, photos):
    jpg = write(photos, "a.jpg", make_jpeg())
    mime = _mime(QUrl.fromLocalFile(str(jpg)), QUrl("https://example.com/c.jpg"))
    actions = Qt.DropAction.CopyAction | Qt.DropAction.MoveAction

    enter = QDragEnterEvent(QPoint(5, 5), actions, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    page.drop_area.dragEnterEvent(enter)
    assert enter.isAccepted()
    assert enter.dropAction() == Qt.DropAction.CopyAction
    assert page.drop_area.is_highlighted()

    drop = QDropEvent(QPointF(5, 5), actions, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    with qtbot.waitSignal(page.drop_area.pathsDropped, timeout=TIMEOUT) as blocker:
        page.drop_area.dropEvent(drop)

    assert blocker.args == [[str(jpg)]]  # only the local file; the web link is left out
    # Never a move: Windows Explorer deletes the source of a move the target accepts.
    assert drop.isAccepted() and drop.dropAction() == Qt.DropAction.CopyAction
    assert not page.drop_area.is_highlighted()
    _settle(qtbot, page, 1)
    assert _states(page) == {"a.jpg": EntryState.ANALYZED}


def test_drag_without_local_files_is_refused(page):
    mime = _mime(QUrl("https://example.com/c.jpg"))  # the event only points at it: keep it alive
    enter = QDragEnterEvent(QPoint(5, 5), Qt.DropAction.CopyAction, mime, Qt.MouseButton.LeftButton,
                            Qt.KeyboardModifier.NoModifier)
    page.drop_area.dragEnterEvent(enter)

    assert not enter.isAccepted()
    assert not page.drop_area.is_highlighted()


def test_drop_on_the_list_area_is_accepted_too(page, qtbot, photos):
    jpg = write(photos, "a.jpg", make_jpeg())
    mime = _mime(QUrl.fromLocalFile(str(jpg)))
    drop = QDropEvent(QPointF(5, 5), Qt.DropAction.CopyAction | Qt.DropAction.MoveAction, mime,
                      Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)

    page.dropEvent(drop)

    assert drop.dropAction() == Qt.DropAction.CopyAction
    _settle(qtbot, page, 1)
    assert _names(page) == ["a.jpg"]


def test_drop_accented_names(page, qtbot, photos):
    name = "fotó 26 de set., ção.png"
    path = write(photos, name, make_png())

    # Through a real drop, so the name survives the QUrl round trip too.
    mime = _mime(QUrl.fromLocalFile(str(path)))
    drop = QDropEvent(QPointF(5, 5), Qt.DropAction.CopyAction, mime, Qt.MouseButton.LeftButton,
                      Qt.KeyboardModifier.NoModifier)
    page.drop_area.dropEvent(drop)
    _settle(qtbot, page, 1)

    (entry_id,) = page.model.entry_ids()
    index = page.model.index_for(entry_id)
    assert index.data(Qt.ItemDataRole.DisplayRole) == name
    assert index.data(Qt.ItemDataRole.ToolTipRole) == str(path)
    assert page.model.entry(entry_id).path == path
    assert index.data(STATE_ROLE) is EntryState.ANALYZED
    assert index.data(THUMBNAIL_ROLE) is not None


def test_drop_folder_skips_previous_outputs(page, qtbot, tmp_path):
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
    _settle(qtbot, page, 2)

    assert sorted(_names(page)) == ["a.jpg", "b.png"]
    assert set(_states(page).values()) == {EntryState.ANALYZED}


def test_heic_row_shows_not_supported_yet(page, qtbot, tmp_path):
    folder = tmp_path / "celular"
    folder.mkdir()
    write(folder, "IMG_0001.HEIC", HEIC_BYTES)
    write(folder, "VID_0002.mp4", MP4_BYTES)
    write(folder, "IMG_0003.jpg", make_jpeg())

    page.add_paths([str(folder)])
    _settle(qtbot, page, 3)

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


# -- mode ---------------------------------------------------------------------------


def test_initial_mode_comes_from_the_config(make_page, tmp_path):
    cfg = load_config(tmp_path / "other.json")
    cfg.set(cfg.defaultMode, "maximum")

    page = make_page(cfg)

    assert page.model.mode() is Mode.MAXIMUM
    assert page.mode_selector.currentRouteKey() == "maximum"
    assert page.mode_description.text() == mode_description(Mode.MAXIMUM)


def test_mode_switch_updates_outcomes_and_description(page, qtbot, photos):
    jpg = write(photos, "a.jpg", make_jpeg())
    page.add_paths([str(jpg)])
    _settle(qtbot, page, 1)
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


def test_summary_text_and_compact_drop_area(page, qtbot, photos):
    assert not page.drop_area.is_compact()
    assert not page.summary_label.isVisibleTo(page)
    assert not page.view.isVisibleTo(page)
    assert not page.clean_button.isEnabled()

    heic = write(photos, "b.heic", HEIC_BYTES)
    plain = write(photos, "c.webp", make_webp("simple"))  # nothing hidden in it
    page.add_paths([str(write(photos, "a.jpg", make_jpeg())), str(heic), str(plain)])
    _settle(qtbot, page, 3)

    assert page.drop_area.is_compact()
    assert page.summary_label.isVisibleTo(page)
    assert page.view.isVisibleTo(page)
    assert page.summary_label.text() == "3 fotos · 1 com informação escondida · 1 ainda não suportada"
    assert page.clear_button.isVisibleTo(page) and page.clear_button.isEnabled()

    page.clear_list()
    assert not page.drop_area.is_compact()
    assert not page.summary_label.isVisibleTo(page)


def test_delete_key_removes_selected_row(page, qtbot, photos):
    page.add_paths([str(write(photos, "a.jpg", make_jpeg())), str(write(photos, "b.png", make_png()))])
    _settle(qtbot, page, 2)
    first, second = page.model.entry_ids()

    child = page.model.index(0, 0, page.model.index_for(first))  # a detail row selects its whole file
    page.view.setCurrentIndex(child)
    qtbot.keyClick(page.view, Qt.Key.Key_Delete)

    assert page.model.entry_ids() == [second]
    assert page.summary_label.text() == summary_text(1, 1, 0)


def test_remove_selected_keeps_nothing_it_should_not(page, qtbot, photos):
    page.add_paths([str(write(photos, "a.jpg", make_jpeg())), str(write(photos, "b.png", make_png()))])
    _settle(qtbot, page, 2)

    page.view.selectAll()
    page.remove_selected()

    assert page.model.entry_ids() == []
    assert not page.drop_area.is_compact()


def test_clear_list(page, qtbot, photos):
    page.add_paths([str(write(photos, "a.jpg", make_jpeg())), str(write(photos, "b.png", make_png()))])
    _settle(qtbot, page, 2)

    page.clear_button.click()

    assert page.model.rowCount() == 0
    assert page.model.summary().total == 0


def test_clear_list_waits_while_photos_are_being_analyzed(page, qtbot, monkeypatch, photos):
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
    _settle(qtbot, page, 1)
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


def test_add_buttons_open_the_dialogs(page, qtbot, monkeypatch, photos, tmp_path):
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
    _settle(qtbot, page, 2)

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


def test_click_or_enter_opens_the_details(page, qtbot, photos):
    page.add_paths([str(write(photos, "a.jpg", make_jpeg()))])
    _settle(qtbot, page, 1)
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
