"""Shared setup for Qt-dependent tests (spec section 8, "Com Qt").

Sets the offscreen platform *before* anything in this process gets a
chance to import Qt, and skips this whole directory when PySide6 isn't
installed -- so the pure-logic suite (`tests/test_gui_*.py`,
`tests/test_engine_isolation.py`) stays runnable without the `gui` extra.

Also holds the fixtures shared by the "Limpar" page tests: every page gets
a config file under `tmp_path` (never `%APPDATA%`), and `make_page`
cancels and drains each page's `JobRunner` at teardown, so no pool thread
outlives its test.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

PAGE_TIMEOUT = 10_000  # ms; generous, the engine takes milliseconds on the fixtures


@pytest.fixture
def config(tmp_path):
    from siftool_gui.settings import load_config

    return load_config(tmp_path / "cfg.json")


@pytest.fixture
def photos(tmp_path):
    folder = tmp_path / "fotos"
    folder.mkdir()
    return folder


@pytest.fixture
def make_page(qtbot, config):
    from siftool_gui.pages.clean_page import CleanPage

    pages = []

    def factory(cfg=None, runner=None):
        page = CleanPage(cfg if cfg is not None else config, runner)
        qtbot.addWidget(page)
        pages.append(page)
        return page

    yield factory
    for page in pages:
        page.runner.cancel()
        assert page.runner.wait_for_done(PAGE_TIMEOUT)


@pytest.fixture
def page(make_page):
    return make_page()


@pytest.fixture
def settle(qtbot):
    """`settle(page, total)`: wait for `total` rows, none busy, and no work left (previews included)."""

    def wait(page, total: int) -> None:
        def settled() -> bool:
            summary = page.model.summary()
            return summary.total == total and summary.busy == 0 and not page.busy()

        qtbot.waitUntil(settled, timeout=PAGE_TIMEOUT)

    return wait
