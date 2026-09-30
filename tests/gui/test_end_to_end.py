"""End to end, through the real window (spec 3, criterion 1; Task 14).

`test_chatgpt_sample_end_to_end` uses the private ChatGPT sample in
`tests/samples-local/` (git-ignored; skipped when absent). The sample is
only ever read: the test copies it into `tmp_path` and works on the copy,
so nothing is ever written beside the private original.
"""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

import pytest
from PySide6.QtCore import QMimeData, QPointF, Qt, QUrl
from PySide6.QtGui import QDropEvent

import siftool
from fixtures.make_fixtures import make_jpeg, make_png, write
from siftool.model import Category, Mode, Status

from siftool_gui.app import create_window
from siftool_gui.entries import EntryState
from siftool_gui.model import CATEGORY_ROLE, KIND_ROLE, OUTCOME_ROLE, STATE_ROLE, VALUE_ROLE, RowKind
from siftool_gui.presentation import Outcome

TIMEOUT = 30_000  # ms; the real sample is a few MB
SAMPLE = Path(__file__).parents[1] / "samples-local" / "chatgpt_c2pa.png"


def _sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _drop(page, paths: list[Path]) -> None:
    """Drop `paths` on the drop area the way Windows Explorer does (it proposes a move)."""
    mime = QMimeData()  # kept alive for the whole event
    mime.setUrls([QUrl.fromLocalFile(str(path)) for path in paths])
    event = QDropEvent(QPointF(10, 10), Qt.DropAction.CopyAction | Qt.DropAction.MoveAction, mime,
                       Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    event.setDropAction(Qt.DropAction.MoveAction)
    page.drop_area.dropEvent(event)
    assert event.isAccepted() and event.dropAction() == Qt.DropAction.CopyAction  # never a move


def _children(model, parent) -> list:
    return [model.index(row, 0, parent) for row in range(model.rowCount(parent))]


def _category_row(model, entry_id: int, category: Category):
    (row,) = [index for index in _children(model, model.index_for(entry_id))
              if index.data(KIND_ROLE) == RowKind.CATEGORY and index.data(CATEGORY_ROLE) == category]
    return row


def _window(qtbot, tmp_path: Path):
    window = create_window(tmp_path / "cfg.json")
    qtbot.addWidget(window)
    window.resize(1100, 760)
    window.show()
    qtbot.waitExposed(window)
    return window


def _clean(qtbot, page) -> tuple[int, int, int]:
    """Click "Limpar N fotos", as the user does, and wait for the batch to end."""
    assert page.clean_button.isEnabled()
    with qtbot.waitSignal(page.batchFinished, timeout=TIMEOUT) as blocker:
        page.clean_button.click()
    return blocker.args[0]


@pytest.mark.skipif(not SAMPLE.exists(), reason="the private ChatGPT sample is not in tests/samples-local")
def test_chatgpt_sample_end_to_end(qtbot, tmp_path, settle):
    private_digest = _sha256(SAMPLE)
    private_folder = sorted(path.name for path in SAMPLE.parent.iterdir())
    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    photo = downloads / "26 de set. de 2026, 03_30_37.png"  # how a ChatGPT download is named
    shutil.copyfile(SAMPLE, photo)
    before = _sha256(photo)

    window = _window(qtbot, tmp_path)
    page = window.clean_page
    _drop(page, [photo])
    settle(page, 1)

    # Spec 3, criterion 1: the row says "Marca de IA: OpenAI".
    (entry_id,) = page.model.entry_ids()
    row = page.model.index_for(entry_id)
    assert row.data(STATE_ROLE) is EntryState.ANALYZED
    assert Category.AI_PROVENANCE in row.data(CATEGORY_ROLE)  # the "Marca de IA" tag
    ai = _category_row(page.model, entry_id, Category.AI_PROVENANCE)
    assert ai.data(Qt.ItemDataRole.DisplayRole) == "Marca de IA"
    assert "OpenAI" in ai.data(VALUE_ROLE)
    assert ai.data(Qt.ItemDataRole.ToolTipRole).startswith("Marca de IA: Gerada por IA (OpenAI")
    assert ai.data(OUTCOME_ROLE) == Outcome.REMOVED

    # "Limpar": the row turns "Verificado", the copy sits beside the original, the original is untouched.
    assert _clean(qtbot, page) == (1, 0, 0)
    assert row.data(STATE_ROLE) is EntryState.VERIFIED
    assert row.data(VALUE_ROLE) == "Verificado"
    assert page.model.entry(entry_id).result.mode is Mode.PRIVACY  # the default mode (spec 1)
    copy = downloads / "26 de set. de 2026, 03_30_37_clean.png"
    assert copy.exists()
    assert siftool.verify(copy).status is Status.VERIFIED
    assert {finding.category for finding in siftool.inspect(copy).findings} <= {Category.APPEARANCE}
    assert _sha256(photo) == before  # byte for byte the same
    assert sorted(path.name for path in downloads.iterdir()) == sorted([photo.name, copy.name])  # nothing else

    assert window.close()
    # The private sample was only read: same bytes, and nothing new beside it.
    assert _sha256(SAMPLE) == private_digest
    assert sorted(path.name for path in SAMPLE.parent.iterdir()) == private_folder


def test_window_smoke_full_flow(qtbot, tmp_path, settle):
    photos = tmp_path / "fotos"
    photos.mkdir()
    sources = [write(photos, "praia.jpg", make_jpeg()), write(photos, "print.png", make_png())]
    digests = {source: _sha256(source) for source in sources}

    window = _window(qtbot, tmp_path)
    page = window.clean_page
    _drop(page, sources)
    settle(page, 2)
    assert page.clean_button.text() == "Limpar 2 fotos"

    assert _clean(qtbot, page) == (2, 0, 0)

    states = [page.model.entry(entry_id).state for entry_id in page.model.entry_ids()]
    assert states == [EntryState.VERIFIED, EntryState.VERIFIED]
    for source in sources:
        copy = source.with_name(f"{source.stem}_clean{source.suffix}")
        assert siftool.verify(copy).status is Status.VERIFIED
        assert _sha256(source) == digests[source]
    listener = window.theme_listener

    assert window.close()  # without an error: pytest-qt fails the test on any exception inside Qt
    assert not page.busy() and not window.runner.busy()
    assert listener is None or not listener.isRunning()  # "Seguir o Windows" stops listening with the window
    assert window.config.get(window.config.windowGeometry)  # the size is remembered
