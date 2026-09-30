"""Shared setup for Qt-dependent tests (spec section 8, "Com Qt").

Sets the offscreen platform *before* anything in this process gets a
chance to import Qt, and skips this whole directory when PySide6 isn't
installed -- so the pure-logic suite (`tests/test_gui_*.py`,
`tests/test_engine_isolation.py`) stays runnable without the `gui` extra.

Also holds the fixtures shared by the "Limpar" page tests: every page gets
a config file under `tmp_path` (never `%APPDATA%`), and `make_page`
cancels and drains each page's `JobRunner` at teardown, so no pool thread
outlives its test. `slow_walk` stands in for a folder walk that takes long.
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


@pytest.fixture(params=["folder-is-a-file", "read-only-file"])
def unwritable_config(request, tmp_path):
    """A settings path nothing can be written to, as in a portable copy on read-only media (release prep).

    Either its folder is really a file, so `mkdir` fails (`QConfig.save`'s first step), or the settings file
    itself is read-only, so opening it for writing fails. It loads as the defaults; every save raises `OSError`.
    """
    import os
    import stat

    if request.param == "folder-is-a-file":
        (tmp_path / "dados").write_text("not a folder", encoding="utf-8")
        return tmp_path / "dados" / "config.json"
    path = tmp_path / "config.json"
    path.write_text("{}", encoding="utf-8")
    os.chmod(path, stat.S_IREAD)
    request.addfinalizer(lambda: os.chmod(path, stat.S_IREAD | stat.S_IWRITE))
    return path


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
def slow_walk(monkeypatch):
    """A folder walk that blocks, in its pool thread, until `slow_walk.release` is set -- then walks for real.

    `slow_walk.started` is set once the walk is running. Released at teardown
    too, so no pool thread is ever left waiting: request it *after* `page` /
    `make_page` / `make_runner`, so it is torn down first.
    """
    import threading

    import siftool_gui.workers as workers

    class SlowWalk:
        started = threading.Event()
        release = threading.Event()

    real_iter_inputs = workers.iter_inputs

    def blocking_iter_inputs(paths):
        SlowWalk.started.set()
        SlowWalk.release.wait(PAGE_TIMEOUT / 1000)
        return real_iter_inputs(paths)

    monkeypatch.setattr(workers, "iter_inputs", blocking_iter_inputs)
    yield SlowWalk
    SlowWalk.release.set()


@pytest.fixture
def settle(qtbot):
    """`settle(page, total)`: wait for `total` rows, none busy, and no work left (previews included)."""

    def wait(page, total: int) -> None:
        def settled() -> bool:
            summary = page.model.summary()
            return summary.total == total and summary.busy == 0 and not page.busy()

        qtbot.waitUntil(settled, timeout=PAGE_TIMEOUT)

    return wait
