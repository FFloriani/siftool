"""Tests for the "Limpar" page: cleaning, progress, results and row actions (spec 4.4-4.5, 5.2, 5.5; Task 11).

The real engine cleans the synthetic fixtures. A slow clean is simulated by
monkeypatching `siftool.clean` (the runner looks it up at call time) with a
wrapper that blocks on a `threading.Event` before calling the real one;
the `slow_clean` fixture always releases it at teardown -- request it
*after* `page`/`make_page`, so it is torn down first.
"""

from __future__ import annotations

import hashlib
import threading
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from qfluentwidgets import InfoBar, InfoBarIcon, PushButton

import siftool
from fixtures.make_fixtures import make_jpeg, make_png, make_webp, write
from siftool.model import Category, Mode, Status

from siftool_gui import shell
from siftool_gui.entries import EntryState
from siftool_gui.model import CATEGORY_ROLE, KIND_ROLE, OUTCOME_ROLE, VALUE_ROLE, RowKind
from siftool_gui.presentation import Outcome, clean_button_text, done_text
from siftool_gui.settings import load_config
from siftool_gui.window import MainWindow
from siftool_gui.workers import JobRunner

TIMEOUT = 10_000  # ms
FOLDER_MESSAGE = "A pasta escolhida para salvar as cópias não está disponível. Escolha outra em Configurações."


class _SlowClean:
    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()


@pytest.fixture
def slow_clean(monkeypatch):
    slow = _SlowClean()
    real_clean = siftool.clean

    def blocked_clean(src, dst, mode=Mode.PRIVACY, **kwargs):
        slow.started.set()
        slow.release.wait(TIMEOUT / 1000)
        return real_clean(src, dst, mode, **kwargs)

    monkeypatch.setattr(siftool, "clean", blocked_clean)
    yield slow
    slow.release.set()  # never leave a pool thread waiting


def _sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _analyze(page, settle, paths) -> None:
    page.add_paths([str(path) for path in paths])
    settle(page, len(paths))


def _clean(qtbot, page) -> tuple[int, int, int]:
    with qtbot.waitSignal(page.batchFinished, timeout=TIMEOUT) as blocker:
        page.start_cleaning()
    return blocker.args[0]


def _entries(page) -> dict:
    return {page.model.entry(i).path.name: page.model.entry(i) for i in page.model.entry_ids()}


def _id_of(page, name: str) -> int:
    (entry_id,) = [i for i in page.model.entry_ids() if page.model.entry(i).path.name == name]
    return entry_id


def _status(page, name: str) -> str:
    return page.model.index_for(_id_of(page, name)).data(VALUE_ROLE)


def _notes(page, name: str) -> list[str]:
    parent = page.model.index_for(_id_of(page, name))
    rows = [page.model.index(row, 0, parent) for row in range(page.model.rowCount(parent))]
    return [index.data(Qt.ItemDataRole.DisplayRole) for index in rows if index.data(KIND_ROLE) == RowKind.NOTE]


def _temp_files(folder: Path) -> list[Path]:
    return sorted(folder.rglob("*.siftool-tmp-*"))


def _bars(page, icon=None) -> list[InfoBar]:
    return [bar for bar in page.findChildren(InfoBar) if icon is None or bar.icon == icon]


def _clean_copy(src: Path, n: int = 1) -> Path:
    suffix = "_clean" if n == 1 else f"_clean ({n})"
    return src.with_name(f"{src.stem}{suffix}{src.suffix}")


# -- the main flow ------------------------------------------------------------------


def test_clean_all_writes_verified_copies_and_keeps_originals(page, qtbot, settle, photos):
    sources = [
        write(photos, "a.jpg", make_jpeg()),
        write(photos, "b.png", make_png()),
        write(photos, "c.webp", make_webp("lossless")),
    ]
    before = {src: _sha256(src) for src in sources}
    _analyze(page, settle, sources)

    assert _clean(qtbot, page) == (3, 0, 0)

    for src in sources:
        copy = _clean_copy(src)
        assert copy.exists()
        assert siftool.verify(copy).status is Status.VERIFIED
        assert _sha256(src) == before[src]  # the original is byte for byte the same
        assert _entries(page)[src.name].state is EntryState.VERIFIED
        assert _status(page, src.name) == "Verificado"
    assert _temp_files(photos) == []


def test_clean_button_text_and_enabled_state(page, qtbot, settle, photos, slow_clean):
    sources = [write(photos, f"{name}.jpg", make_jpeg()) for name in ("a", "b", "c")]
    assert page.clean_button.text() == "Limpar" and not page.clean_button.isEnabled()
    _analyze(page, settle, sources)
    assert page.clean_button.text() == "Limpar 3 fotos"
    assert page.clean_button.isEnabled()

    page.clean_button.click()
    assert not page.clean_button.isEnabled()  # while cleaning
    qtbot.waitUntil(slow_clean.started.is_set, timeout=TIMEOUT)
    assert not page.clean_button.isEnabled()

    with qtbot.waitSignal(page.batchFinished, timeout=TIMEOUT):
        slow_clean.release.set()
    assert page.clean_button.text() == clean_button_text(0) == "Limpar"
    assert not page.clean_button.isEnabled()  # and after: nothing left to clean


def test_output_folder_setting_is_used(make_page, qtbot, settle, photos, tmp_path):
    out = tmp_path / "cópias, limpas"
    out.mkdir()
    cfg = load_config(tmp_path / "folder.json")
    cfg.set(cfg.saveLocation, "folder")
    cfg.set(cfg.outputFolder, str(out))
    page = make_page(cfg)
    src = write(photos, "a.jpg", make_jpeg())
    _analyze(page, settle, [src])

    assert _clean(qtbot, page) == (1, 0, 0)

    assert (out / "a_clean.jpg").exists()
    assert not _clean_copy(src).exists()
    assert _entries(page)["a.jpg"].result.dst == str(out / "a_clean.jpg")


@pytest.mark.parametrize("problem", ["missing", "a file", "never chosen"])
def test_missing_output_folder_blocks_cleaning_with_message(make_page, settle, photos, tmp_path, problem):
    cfg = load_config(tmp_path / "folder.json")
    cfg.set(cfg.saveLocation, "folder")
    if problem == "missing":
        cfg.set(cfg.outputFolder, str(tmp_path / "pendrive que saiu"))
    elif problem == "a file":
        cfg.set(cfg.outputFolder, str(write(tmp_path, "not a folder.txt", b"x")))
    page = make_page(cfg)
    sources = [write(photos, "a.jpg", make_jpeg()), write(photos, "b.png", make_png())]
    _analyze(page, settle, sources)

    page.start_cleaning()

    # Nothing starts: no row says "Não encontrado" one by one, one message says why.
    assert {entry.state for entry in _entries(page).values()} == {EntryState.ANALYZED}
    assert not page.runner.busy()
    assert page.clean_button.isEnabled()
    (bar,) = _bars(page, InfoBarIcon.ERROR)
    assert bar.content == FOLDER_MESSAGE
    assert sorted(path.name for path in tmp_path.rglob("*_clean*")) == []


def test_same_name_sources_get_distinct_outputs(make_page, qtbot, settle, tmp_path):
    out = tmp_path / "saida"
    out.mkdir()
    (tmp_path / "trip1").mkdir()
    (tmp_path / "trip2").mkdir()
    cfg = load_config(tmp_path / "folder.json")
    cfg.set(cfg.saveLocation, "folder")
    cfg.set(cfg.outputFolder, str(out))
    page = make_page(cfg)
    first = write(tmp_path / "trip1", "IMG.jpg", make_jpeg())
    second = write(tmp_path / "trip2", "IMG.jpg", make_jpeg(comment=False))
    _analyze(page, settle, [first, second])

    assert _clean(qtbot, page) == (2, 0, 0)

    assert sorted(path.name for path in out.iterdir()) == ["IMG_clean (2).jpg", "IMG_clean.jpg"]
    outputs = {Path(page.model.entry(i).result.dst) for i in page.model.entry_ids()}
    assert outputs == {out / "IMG_clean.jpg", out / "IMG_clean (2).jpg"}


def test_file_removed_before_clean_shows_error_and_batch_continues(page, qtbot, settle, photos):
    sources = [write(photos, "a.jpg", make_jpeg()), write(photos, "b.png", make_png()),
               write(photos, "c.webp", make_webp("lossless"))]
    _analyze(page, settle, sources)
    sources[1].unlink()  # moved or deleted between the analysis and the clean

    assert _clean(qtbot, page) == (2, 0, 1)

    entries = _entries(page)
    assert entries["b.png"].state is EntryState.FAILED
    assert _status(page, "b.png") == "Não encontrado"
    assert "O arquivo foi movido ou apagado." in _notes(page, "b.png")
    assert entries["a.jpg"].state is EntryState.VERIFIED
    assert entries["c.webp"].state is EntryState.VERIFIED
    assert _temp_files(photos) == []


def test_verification_failure_shows_violations(page, qtbot, settle, photos):
    src = write(photos, "c2pa.jpg", make_jpeg(reserved_marker=True, reserved_payload=b"has c2pa inside"))
    _analyze(page, settle, [src])

    assert _clean(qtbot, page) == (0, 0, 1)

    assert _entries(page)["c2pa.jpg"].state is EntryState.FAILED
    assert _status(page, "c2pa.jpg") == "Não limpou"
    notes = _notes(page, "c2pa.jpg")
    assert "A verificação encontrou dados que não saíram, por isso nenhuma cópia foi salva." in notes
    assert "Marca de IA: C2PA label" in notes  # the violation, in Portuguese
    assert not _clean_copy(src).exists()
    assert _temp_files(photos) == []


def test_warnings_result(page, qtbot, settle, photos):
    src = write(photos, "reservado.jpg", make_jpeg(reserved_marker=True))
    _analyze(page, settle, [src])

    assert _clean(qtbot, page) == (0, 1, 0)

    assert _entries(page)["reservado.jpg"].state is EntryState.WARNINGS
    assert _status(page, "reservado.jpg") == "Com ressalvas"
    assert _notes(page, "reservado.jpg")[0].startswith("A cópia foi salva, mas havia uma estrutura")
    assert _clean_copy(src).exists()


def test_accented_names_clean_and_reveal(page, qtbot, settle, photos, monkeypatch):
    revealed: list[Path] = []
    monkeypatch.setattr(shell, "reveal_in_explorer", revealed.append)
    src = write(photos, "fotó 26 de set., ção.png", make_png())
    _analyze(page, settle, [src])

    assert _clean(qtbot, page) == (1, 0, 0)

    copy = photos / "fotó 26 de set., ção_clean.png"
    assert copy.exists()
    page.reveal(_id_of(page, src.name))
    assert revealed == [copy]
    assert shell.explorer_select_command(copy) == f'explorer /select,"{copy}"'


# -- progress and cancel ------------------------------------------------------------


def test_progress_counts_the_batch_and_hides_after(page, qtbot, settle, photos, slow_clean):
    sources = [write(photos, f"{name}.jpg", make_jpeg()) for name in ("a", "b")]
    _analyze(page, settle, sources)
    assert not page.progress_bar.isVisibleTo(page)
    assert not page.cancel_button.isVisibleTo(page)

    page.start_cleaning()
    qtbot.waitUntil(slow_clean.started.is_set, timeout=TIMEOUT)
    assert page.progress_bar.isVisibleTo(page) and page.cancel_button.isVisibleTo(page)
    assert (page.progress_bar.value(), page.progress_bar.maximum()) == (0, 2)
    assert page.progress_label.text() == "Limpando fotos… 0 de 2"

    with qtbot.waitSignal(page.batchFinished, timeout=TIMEOUT):
        slow_clean.release.set()
    assert not page.progress_bar.isVisibleTo(page)
    assert not page.cancel_button.isVisibleTo(page)


def test_cancel_during_clean_reverts_pending(make_page, qtbot, settle, photos, slow_clean):
    page = make_page(runner=JobRunner(max_workers=1))
    sources = [write(photos, f"{name}.jpg", make_jpeg()) for name in ("a", "b", "c")]
    _analyze(page, settle, sources)

    page.start_cleaning()
    qtbot.waitUntil(slow_clean.started.is_set, timeout=TIMEOUT)
    page.cancel_button.click()  # "Cancelar": the two waiting copies go back

    states = sorted(entry.state.name for entry in _entries(page).values())
    assert states == ["ANALYZED", "ANALYZED", "CLEANING"]  # the running one finishes

    with qtbot.waitSignal(page.batchFinished, timeout=TIMEOUT) as blocker:
        slow_clean.release.set()

    assert blocker.args == [(1, 0, 0)]
    states = sorted(entry.state.name for entry in _entries(page).values())
    assert states == ["ANALYZED", "ANALYZED", "VERIFIED"]
    assert len(list(photos.glob("*_clean*"))) == 1
    assert _temp_files(photos) == []
    assert page.clean_button.text() == "Limpar 2 fotos" and page.clean_button.isEnabled()
    assert not page.progress_bar.isVisibleTo(page)


def test_close_while_cleaning_leaves_no_partial_files(qtbot, monkeypatch, settle, photos, tmp_path):
    started = threading.Event()
    real_clean = siftool.clean

    def slow(src, dst, mode=Mode.PRIVACY, **kwargs):
        started.set()
        threading.Event().wait(0.5)  # a clean that is still writing when the window closes
        return real_clean(src, dst, mode, **kwargs)

    monkeypatch.setattr(siftool, "clean", slow)
    cfg = load_config(tmp_path / "cfg.json")
    runner = JobRunner(max_workers=1)
    window = MainWindow(cfg, runner)
    qtbot.addWidget(window)
    page = window.clean_page
    sources = [write(photos, f"{name}.jpg", make_jpeg()) for name in ("a", "b", "c")]
    before = {src: _sha256(src) for src in sources}
    _analyze(page, settle, sources)
    page.start_cleaning()
    qtbot.waitUntil(started.is_set, timeout=TIMEOUT)
    assert page.busy()

    assert window.close()  # waits for the running clean; the queued ones are dropped

    assert runner.wait_for_done(0)  # nothing runs any more
    assert _temp_files(photos) == []
    copies = sorted(photos.glob("*_clean*"))
    assert 1 <= len(copies) < len(sources)
    for copy in copies:  # whole files, never half-written ones
        assert siftool.verify(copy).status is Status.VERIFIED
    assert all(_sha256(src) == digest for src, digest in before.items())
    assert cfg.get(cfg.windowGeometry)  # the size is remembered


def test_clean_again_uses_current_mode_and_new_name(page, qtbot, settle, photos):
    src = write(photos, "a.jpg", make_jpeg())
    _analyze(page, settle, [src])
    assert _clean(qtbot, page) == (1, 0, 0)
    entry_id = _id_of(page, "a.jpg")
    assert page.model.entry(entry_id).result.mode is Mode.PRIVACY

    page.set_mode(Mode.MAXIMUM)
    with qtbot.waitSignal(page.batchFinished, timeout=TIMEOUT) as blocker:
        page.clean_again(entry_id)

    assert blocker.args == [(1, 0, 0)]
    result = page.model.entry(entry_id).result
    assert result.mode is Mode.MAXIMUM
    assert Path(result.dst) == _clean_copy(src, 2)
    assert _clean_copy(src).exists() and _clean_copy(src, 2).exists()
    parent = page.model.index_for(entry_id)
    outcomes = {
        Category(index.data(CATEGORY_ROLE)): Outcome(index.data(OUTCOME_ROLE))
        for index in (page.model.index(row, 0, parent) for row in range(page.model.rowCount(parent)))
        if index.data(KIND_ROLE) == RowKind.CATEGORY
    }
    assert outcomes[Category.APPEARANCE] is Outcome.REMOVED  # Máximo takes orientation and colors too


def test_batch_finished_counts_and_infobar(page, qtbot, settle, photos, monkeypatch):
    opened: list[Path] = []
    monkeypatch.setattr(shell, "open_folder", opened.append)
    sources = [
        write(photos, "a.jpg", make_jpeg()),
        write(photos, "reservado.jpg", make_jpeg(reserved_marker=True)),
        write(photos, "c2pa.jpg", make_jpeg(reserved_marker=True, reserved_payload=b"has c2pa inside")),
    ]
    _analyze(page, settle, sources)

    assert _clean(qtbot, page) == (1, 1, 1)

    (success,) = _bars(page, InfoBarIcon.SUCCESS)
    assert success.title == "Pronto"
    assert success.content == done_text(1, 1, 1)
    (open_folder,) = [button for button in success.findChildren(PushButton) if button.text() == "Abrir pasta"]
    open_folder.click()
    assert opened == [photos]  # beside the originals: the folder of the first cleaned file
    (warning,) = _bars(page, InfoBarIcon.WARNING)
    assert warning.content  # says how many were not cleaned

    # The next batch replaces the previous batch's messages.
    page.set_mode(Mode.PHOTOGRAPHER)
    with qtbot.waitSignal(page.batchFinished, timeout=TIMEOUT):
        page.clean_again(_id_of(page, "a.jpg"))
    assert len(_bars(page, InfoBarIcon.SUCCESS)) == 1
    assert _bars(page, InfoBarIcon.WARNING) == []


# -- the row menu and keyboard ----------------------------------------------------------


def test_row_menu_actions(page, qtbot, settle, photos, monkeypatch):
    revealed: list[Path] = []
    monkeypatch.setattr(shell, "reveal_in_explorer", revealed.append)
    src = write(photos, "a.jpg", make_jpeg())
    _analyze(page, settle, [src])
    entry_id = _id_of(page, "a.jpg")

    actions = {action.text(): action for action in page.row_menu(entry_id).menuActions()}
    assert list(actions) == ["Mostrar no Explorer", "Limpar de novo", "Remover da lista"]
    assert not actions["Limpar de novo"].isEnabled()  # only once a clean has finished
    actions["Mostrar no Explorer"].trigger()
    assert revealed == [src]  # no copy yet: the original

    assert _clean(qtbot, page) == (1, 0, 0)
    actions = {action.text(): action for action in page.row_menu(entry_id).menuActions()}
    assert actions["Limpar de novo"].isEnabled()
    actions["Mostrar no Explorer"].trigger()
    assert revealed[-1] == _clean_copy(src)  # the copy, now that it exists

    actions["Remover da lista"].trigger()
    assert page.model.entry_ids() == []


def test_a_file_being_cleaned_cannot_be_removed(page, qtbot, settle, photos, slow_clean):
    _analyze(page, settle, [write(photos, "a.jpg", make_jpeg())])
    entry_id = _id_of(page, "a.jpg")
    page.start_cleaning()
    qtbot.waitUntil(slow_clean.started.is_set, timeout=TIMEOUT)

    actions = {action.text(): action for action in page.row_menu(entry_id).menuActions()}
    assert not actions["Remover da lista"].isEnabled()
    page.view.setCurrentIndex(page.model.index_for(entry_id))
    qtbot.keyClick(page.view, Qt.Key.Key_Delete)
    page.clear_list()
    assert page.model.entry_ids() == [entry_id]
    assert not page.clear_button.isEnabled()

    with qtbot.waitSignal(page.batchFinished, timeout=TIMEOUT):
        slow_clean.release.set()


def test_ctrl_enter_starts_cleaning(page, qtbot, settle, photos):
    _analyze(page, settle, [write(photos, "a.jpg", make_jpeg())])
    page.resize(900, 700)
    page.show()
    qtbot.waitExposed(page)
    page.activateWindow()
    qtbot.waitUntil(page.isActiveWindow, timeout=TIMEOUT)
    page.view.setFocus()

    with qtbot.waitSignal(page.batchFinished, timeout=TIMEOUT) as blocker:
        qtbot.keyClick(page.view, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)

    assert blocker.args == [(1, 0, 0)]


# -- batches on the page's own queue ------------------------------------------------------


def test_mode_switch_during_a_batch_does_not_change_it(make_page, qtbot, settle, photos, slow_clean):
    page = make_page(runner=JobRunner(max_workers=1))
    sources = [write(photos, f"{name}.jpg", make_jpeg()) for name in ("a", "b", "c", "d")]
    _analyze(page, settle, sources)

    page.start_cleaning()  # in Privacidade; two copies are in flight, two wait in the page
    qtbot.waitUntil(slow_clean.started.is_set, timeout=TIMEOUT)
    page.set_mode(Mode.MAXIMUM)
    with qtbot.waitSignal(page.batchFinished, timeout=TIMEOUT) as blocker:
        slow_clean.release.set()

    assert blocker.args == [(4, 0, 0)]
    assert {entry.result.mode for entry in _entries(page).values()} == {Mode.PRIVACY}


def test_a_file_removed_while_waiting_for_its_clean_is_skipped(make_page, qtbot, settle, photos, slow_clean):
    page = make_page(runner=JobRunner(max_workers=1))
    sources = [write(photos, f"{name}.jpg", make_jpeg()) for name in ("a", "b", "c", "d")]
    _analyze(page, settle, sources)

    page.start_cleaning()
    qtbot.waitUntil(slow_clean.started.is_set, timeout=TIMEOUT)
    states = [entry.state.name for entry in _entries(page).values()]
    assert states == ["CLEANING", "CLEANING", "ANALYZED", "ANALYZED"]  # the last two wait their turn
    assert page.progress_label.text() == "Limpando fotos… 0 de 4"

    waiting = _id_of(page, "d.jpg")
    page.view.setCurrentIndex(page.model.index_for(waiting))
    page.remove_selected()  # not "Limpando" yet: it may go (spec 4.5)
    with qtbot.waitSignal(page.batchFinished, timeout=TIMEOUT) as blocker:
        slow_clean.release.set()

    assert blocker.args == [(3, 0, 0)]
    assert sorted(_entries(page)) == ["a.jpg", "b.jpg", "c.jpg"]
    assert not _clean_copy(sources[3]).exists()
    assert not page.progress_bar.isVisibleTo(page)
    assert not page.busy()


def test_cancel_empties_the_queue_even_when_nothing_is_left_to_drop(make_page, qtbot, settle, photos):
    page = make_page(runner=JobRunner(max_workers=1))
    sources = [write(photos, f"{name}.jpg", make_jpeg()) for name in ("a", "b", "c", "d")]
    _analyze(page, settle, sources)

    page.start_cleaning()  # a and b go to the runner; c and d wait in the page
    assert page.runner.wait_for_done(TIMEOUT)  # both finish; their results are not delivered yet
    with qtbot.waitSignal(page.batchFinished, timeout=TIMEOUT) as blocker:
        page.cancel()  # the pool has nothing to drop -- c and d must still not start

    assert blocker.args == [(2, 0, 0)]
    states = [entry.state.name for entry in _entries(page).values()]
    assert states == ["VERIFIED", "VERIFIED", "ANALYZED", "ANALYZED"]
    assert not page.busy()


def test_cancelling_the_runner_directly_stops_the_page_queue_too(make_page, qtbot, settle, photos, slow_clean):
    page = make_page(runner=JobRunner(max_workers=1))
    sources = [write(photos, f"{name}.jpg", make_jpeg()) for name in ("a", "b", "c", "d")]
    _analyze(page, settle, sources)
    page.start_cleaning()
    qtbot.waitUntil(slow_clean.started.is_set, timeout=TIMEOUT)

    page.runner.cancel()  # not through the page (the window's runner is shared)
    with qtbot.waitSignal(page.batchFinished, timeout=TIMEOUT) as blocker:
        slow_clean.release.set()

    assert blocker.args == [(1, 0, 0)]
    states = [entry.state.name for entry in _entries(page).values()]
    assert states == ["VERIFIED", "ANALYZED", "ANALYZED", "ANALYZED"]
