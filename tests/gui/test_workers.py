"""Tests for `siftool_gui.workers.JobRunner` (spec sections 4.3-4.4; Task 7).

Every runner comes from the `make_runner` fixture, which cancels and waits
for it at teardown, so no pool thread outlives its test. Slow engine calls
are simulated by monkeypatching `siftool.inspect` (the runner looks it up
at call time); they block on `threading.Event`s, never on sleep loops.
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

import pytest
from PIL import Image
from PySide6.QtCore import QThread
from PySide6.QtWidgets import QApplication

import siftool
from fixtures.make_fixtures import PNG_SIGNATURE, make_jpeg, make_png, write
from siftool.errors import CorruptFileError, UnsupportedFormatError
from siftool.model import Mode, Status

from siftool_gui.workers import SCAN_BATCH_SIZE, JobRunner

TIMEOUT = 10_000  # ms; generous, the jobs themselves take milliseconds


@pytest.fixture
def make_runner():
    runners: list[JobRunner] = []

    def factory(**kwargs) -> JobRunner:
        runner = JobRunner(**kwargs)
        runners.append(runner)
        return runner

    yield factory
    for runner in runners:
        runner.cancel()
        assert runner.wait_for_done(TIMEOUT)


def _record(signal, log: list, name: str) -> None:
    signal.connect(lambda *args: log.append((name, *args)))


def test_scan_reports_supported_files_and_skips_outputs(make_runner, qtbot, tmp_path):
    a = write(tmp_path, "a.jpg", make_jpeg())
    b = write(tmp_path, "b.png", make_png())
    write(tmp_path, "a_clean.jpg", make_jpeg())
    write(tmp_path, "note.txt", b"not an image")

    runner = make_runner()
    log: list = []
    _record(runner.pathsFound, log, "paths")
    _record(runner.scanFinished, log, "finished")

    with qtbot.waitSignal(runner.scanFinished, timeout=TIMEOUT):
        runner.scan([str(tmp_path)])

    assert log[-1] == ("finished",)
    found = [path for name, *args in log if name == "paths" for path in args[0]]
    assert found == [str(a), str(b)]
    assert all(isinstance(path, str) for path in found)


def test_scan_emits_batches_of_at_most_200(make_runner, qtbot, tmp_path):
    for i in range(2 * SCAN_BATCH_SIZE + 50):
        write(tmp_path, f"img{i:04d}.jpg", b"")  # the scan only looks at names

    runner = make_runner()
    batches: list[list[str]] = []
    runner.pathsFound.connect(batches.append)
    with qtbot.waitSignal(runner.scanFinished, timeout=TIMEOUT):
        runner.scan([str(tmp_path)])

    assert SCAN_BATCH_SIZE == 200
    assert [len(batch) for batch in batches] == [200, 200, 50]


def test_scan_reports_a_walk_that_found_no_file(make_runner, monkeypatch, qtbot, tmp_path):
    # Final review: a dropped folder without a single supported file added nothing, in silence.
    def folder(name: str, files: dict[str, bytes] | None = None) -> str:
        path = tmp_path / name
        path.mkdir()
        for file_name, data in (files or {}).items():
            write(path, file_name, data)
        return str(path)

    nothing = folder("vazia")
    only_text = folder("textos", {"note.txt": b"not an image"})
    only_outputs = folder("saidas", {"a_clean.jpg": make_jpeg()})  # a previous run's own copies are skipped
    photos = folder("fotos", {"a.jpg": make_jpeg()})
    runner = make_runner()
    log: list = []
    _record(runner.scanEmpty, log, "empty")
    _record(runner.pathsFound, log, "paths")
    _record(runner.scanFinished, log, "finished")

    def walk(*paths: str) -> list:
        log.clear()
        with qtbot.waitSignal(runner.scanFinished, timeout=TIMEOUT):
            runner.scan(list(paths))
        return [entry[0] for entry in log]

    assert walk(nothing) == ["empty", "finished"]
    assert walk(only_text) == ["empty", "finished"]
    assert walk(only_outputs) == ["empty", "finished"]
    assert walk(nothing, only_text) == ["empty", "finished"]  # one walk, one report
    assert walk(photos) == ["paths", "finished"]  # found something: not empty
    assert walk(nothing, photos) == ["paths", "finished"]  # ... in any of its folders

    import siftool_gui.workers as workers

    def cannot_be_read(paths):
        raise PermissionError(13, "Permission denied", str(paths[0]))

    monkeypatch.setattr(workers, "iter_inputs", cannot_be_read)
    assert walk(photos) == ["empty", "finished"]  # a folder that cannot be read adds nothing either


def test_a_cancelled_walk_is_not_reported_empty(make_runner, slow_walk, qtbot, tmp_path):
    # It did not find nothing: it was stopped. "Nenhuma foto encontrada" would be a lie.
    runner = make_runner()
    log: list = []
    _record(runner.scanEmpty, log, "empty")
    _record(runner.scanFinished, log, "finished")
    runner.scan([str(tmp_path)])
    assert slow_walk.started.wait(TIMEOUT / 1000)

    runner.cancel()
    slow_walk.release.set()
    assert runner.wait_for_done(TIMEOUT)
    QApplication.processEvents()

    assert log == [("finished",)]


def test_analyze_success_unsupported_and_corrupt(make_runner, qtbot, tmp_path):
    png = write(tmp_path, "a.png", make_png())
    heic = write(tmp_path, "b.heic", b"\x00\x00\x00\x18ftypheic" + b"\x00" * 64)
    junk = write(tmp_path, "c.png", PNG_SIGNATURE + b"garbage" * 20)

    runner = make_runner()
    reports: dict = {}
    failures: dict = {}
    runner.analyzed.connect(reports.__setitem__)
    runner.analysisFailed.connect(failures.__setitem__)

    with qtbot.waitSignal(runner.idle, timeout=TIMEOUT):
        runner.analyze(1, png)
        runner.analyze(2, heic)
        runner.analyze(3, junk)

    assert set(reports) == {1}
    assert reports[1].format == "png"
    assert isinstance(failures[2], UnsupportedFormatError)
    assert failures[2].format_name == "HEIC"
    assert isinstance(failures[3], CorruptFileError)


def _recording_inspect(monkeypatch) -> list[str]:
    """Patch `siftool.inspect` with a wrapper that logs the name of every file handed to the engine."""
    handed_over: list[str] = []
    real_inspect = siftool.inspect

    def recording_inspect(path):
        handed_over.append(Path(path).name)
        return real_inspect(path)

    monkeypatch.setattr(siftool, "inspect", recording_inspect)
    return handed_over


def test_analyze_refuses_a_known_unsupported_format_from_its_first_bytes(make_runner, monkeypatch, qtbot, tmp_path):
    # Final review: the engine checks its 512 MB cap before it detects the format, so a big video said
    # "Grande demais" -- and every small one was read whole into memory just to be refused.
    handed_over = _recording_inspect(monkeypatch)
    files = {
        1: write(tmp_path, "clip.mp4", b"\x00\x00\x00\x18ftypisom" + b"\x00" * 64),
        2: write(tmp_path, "photo.heic", b"\x00\x00\x00\x18ftypheic" + b"\x00" * 64),
        3: write(tmp_path, "anim.gif", b"GIF89a" + b"\x00" * 64),
        4: write(tmp_path, "note.txt", b"just some text, not a photo"),
    }
    runner = make_runner()
    failures: dict = {}
    runner.analysisFailed.connect(failures.__setitem__)

    with qtbot.waitSignal(runner.idle, timeout=TIMEOUT):
        for entry_id, path in files.items():
            runner.analyze(entry_id, path)

    assert handed_over == []  # none of them was read whole by `siftool.inspect`
    assert all(isinstance(exc, UnsupportedFormatError) for exc in failures.values()), failures
    assert {entry_id: exc.format_name for entry_id, exc in failures.items()} == {
        1: "MP4/MOV", 2: "HEIC", 3: "GIF", 4: None,
    }
    assert all(exc.__traceback__ is None for exc in failures.values())


def test_analyze_still_hands_supported_empty_and_unreadable_files_to_the_engine(make_runner, monkeypatch, qtbot,
                                                                                tmp_path):
    handed_over = _recording_inspect(monkeypatch)
    png = write(tmp_path, "a.png", make_png())
    empty = write(tmp_path, "empty.jpg", b"")
    runner = make_runner()
    reports: dict = {}
    failures: dict = {}
    runner.analyzed.connect(reports.__setitem__)
    runner.analysisFailed.connect(failures.__setitem__)

    with qtbot.waitSignal(runner.idle, timeout=TIMEOUT):
        runner.analyze(1, png)
        runner.analyze(2, empty)  # nothing to recognize: the engine says "danificado", as before
        runner.analyze(3, tmp_path / "gone.jpg")  # cannot be opened: the engine's own OSError, as before

    assert sorted(handed_over) == ["a.png", "empty.jpg", "gone.jpg"]
    assert reports[1].format == "png"
    assert isinstance(failures[2], CorruptFileError)
    assert isinstance(failures[3], FileNotFoundError)


def test_unexpected_exceptions_become_failures_without_tracebacks(make_runner, monkeypatch, qtbot, tmp_path):
    raised = {"bug.png": RuntimeError("engine bug"), "huge.png": MemoryError()}

    def broken_inspect(path):
        raise raised[path.name]

    monkeypatch.setattr(siftool, "inspect", broken_inspect)
    runner = make_runner()
    failures: dict = {}
    runner.analysisFailed.connect(failures.__setitem__)

    with qtbot.waitSignal(runner.idle, timeout=TIMEOUT):
        runner.analyze(1, write(tmp_path, "bug.png", make_png()))
        runner.analyze(2, write(tmp_path, "huge.png", make_png()))

    assert failures == {1: raised["bug.png"], 2: raised["huge.png"]}
    # No frame (and so no file buffer) stays pinned by an entry's error.
    assert all(exc.__traceback__ is None for exc in failures.values())


def test_thumbnail_scaled_or_null(make_runner, qtbot, tmp_path):
    big = tmp_path / "big.jpg"
    Image.new("RGB", (400, 300), (200, 40, 40)).save(big, quality=90)
    rotated = tmp_path / "rotated.jpg"
    exif = Image.Exif()
    exif[0x0112] = 6  # Orientation: rotate 90 CW to display
    Image.new("RGB", (400, 300), (40, 40, 200)).save(rotated, quality=90, exif=exif)
    small = write(tmp_path, "small.png", make_png())  # 16x16: never upscaled
    broken = write(tmp_path, "broken.jpg", b"\xff\xd8\xff" + b"\x00garbage" * 20)

    runner = make_runner()
    images: dict = {}
    runner.thumbnailReady.connect(images.__setitem__)
    with qtbot.waitSignal(runner.idle, timeout=TIMEOUT):
        runner.thumbnail(1, big)
        runner.thumbnail(2, rotated)
        runner.thumbnail(3, small)
        runner.thumbnail(4, broken)
        runner.thumbnail(5, tmp_path / "missing.jpg")

    for entry_id in (1, 2, 3):
        assert not images[entry_id].isNull()
        assert max(images[entry_id].width(), images[entry_id].height()) <= 96
    assert (images[1].width(), images[1].height()) == (96, 72)
    assert (images[2].width(), images[2].height()) == (72, 96)  # EXIF orientation applied
    assert (images[3].width(), images[3].height()) == (16, 16)
    assert images[4].isNull()
    assert images[5].isNull()


def test_thumbnail_honours_requested_size(make_runner, qtbot, tmp_path):
    big = tmp_path / "big.png"
    Image.new("RGB", (300, 300), (10, 200, 10)).save(big)

    runner = make_runner()
    with qtbot.waitSignal(runner.thumbnailReady, timeout=TIMEOUT) as blocker:
        runner.thumbnail(9, big, size=48)

    entry_id, image = blocker.args
    assert entry_id == 9
    assert (image.width(), image.height()) == (48, 48)


def test_clean_writes_verified_copy(make_runner, qtbot, tmp_path):
    src = write(tmp_path, "a.jpg", make_jpeg())
    original = src.read_bytes()
    dst = tmp_path / "a_clean.jpg"

    runner = make_runner()
    with qtbot.waitSignal(runner.cleaned, timeout=TIMEOUT) as blocker:
        runner.clean(7, src, dst, Mode.PRIVACY)

    entry_id, result = blocker.args
    assert entry_id == 7
    assert result.verdict.status is Status.VERIFIED
    assert result.mode is Mode.PRIVACY
    assert dst.exists()
    assert src.read_bytes() == original


def test_clean_into_missing_folder_fails_with_oserror(make_runner, qtbot, tmp_path):
    src = write(tmp_path, "a.jpg", make_jpeg())
    dst = tmp_path / "missing" / "a_clean.jpg"

    runner = make_runner()
    with qtbot.waitSignal(runner.cleanFailed, timeout=TIMEOUT) as blocker:
        runner.clean(3, src, dst, Mode.PRIVACY)

    entry_id, exc = blocker.args
    assert entry_id == 3
    assert isinstance(exc, OSError)
    assert not dst.parent.exists()


def test_concurrency_is_bounded(make_runner, monkeypatch, qtbot, tmp_path):
    real_inspect = siftool.inspect
    lock = threading.Lock()
    active = 0
    peak = 0

    def slow_inspect(path):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        try:
            time.sleep(0.05)  # simulated work, inside the pool thread
            return real_inspect(path)
        finally:
            with lock:
                active -= 1

    monkeypatch.setattr(siftool, "inspect", slow_inspect)
    png = write(tmp_path, "a.png", make_png())
    runner = make_runner(max_workers=2)
    done: list[int] = []
    runner.analyzed.connect(lambda entry_id, report: done.append(entry_id))

    with qtbot.waitSignal(runner.idle, timeout=TIMEOUT):
        for entry_id in range(1, 13):
            runner.analyze(entry_id, png)

    assert sorted(done) == list(range(1, 13))
    assert peak == 2  # bounded by max_workers, and the bound is actually used


def test_default_worker_count_is_bounded(make_runner):
    assert make_runner().max_workers() == min(4, os.cpu_count() or 1)
    assert make_runner(max_workers=3).max_workers() == 3
    with pytest.raises(ValueError):
        JobRunner(max_workers=0)


def test_cancel_drops_pending_and_reports_them(make_runner, monkeypatch, qtbot, tmp_path):
    real_inspect = siftool.inspect
    started = threading.Event()
    release = threading.Event()

    def blocking_inspect(path):
        started.set()
        release.wait(TIMEOUT / 1000)
        return real_inspect(path)

    monkeypatch.setattr(siftool, "inspect", blocking_inspect)
    png = write(tmp_path, "a.png", make_png())
    runner = make_runner(max_workers=1)
    log: list = []
    _record(runner.analyzed, log, "analyzed")
    _record(runner.jobDropped, log, "dropped")
    _record(runner.idle, log, "idle")

    try:
        for entry_id in range(1, 6):
            runner.analyze(entry_id, png)
        assert started.wait(TIMEOUT / 1000)
        assert runner.busy()

        runner.cancel()
        assert [entry for entry in log if entry[0] == "dropped"] == [
            ("dropped", entry_id, "analyze") for entry_id in (2, 3, 4, 5)
        ]
        assert runner.busy()  # the running job still finishes
    finally:
        release.set()

    with qtbot.waitSignal(runner.idle, timeout=TIMEOUT):
        pass

    names = [entry[0] for entry in log]
    assert names.count("analyzed") == 1
    assert names.count("dropped") == 4
    assert log[-1] == ("idle",)
    assert [entry[1] for entry in log if entry[0] == "analyzed"] == [1]
    assert not runner.busy()


def test_cancel_drops_pending_clean_and_thumbnail_jobs(make_runner, monkeypatch, qtbot, tmp_path):
    real_inspect = siftool.inspect
    started = threading.Event()
    release = threading.Event()

    def blocking_inspect(path):  # occupies the only worker while work queues behind it
        started.set()
        release.wait(TIMEOUT / 1000)
        return real_inspect(path)

    monkeypatch.setattr(siftool, "inspect", blocking_inspect)
    src = write(tmp_path, "a.jpg", make_jpeg())
    runner = make_runner(max_workers=1)
    log: list = []
    _record(runner.jobDropped, log, "dropped")
    _record(runner.thumbnailReady, log, "thumbnail")
    _record(runner.cleaned, log, "cleaned")

    try:
        runner.analyze(1, src)
        assert started.wait(TIMEOUT / 1000)
        runner.clean(2, src, tmp_path / "a_clean.jpg", Mode.PRIVACY)
        runner.thumbnail(2, src)
        runner.cancel()
        assert log == [("dropped", 2, "clean")]  # a dropped thumbnail has nothing to revert
    finally:
        release.set()

    with qtbot.waitSignal(runner.idle, timeout=TIMEOUT):
        pass
    assert log == [("dropped", 2, "clean")]
    assert not (tmp_path / "a_clean.jpg").exists()


def test_cancel_ends_a_running_scan_at_once(make_runner, slow_walk, qtbot, tmp_path):
    # Final review: "Cancelar" during a long folder walk seemed to do nothing -- the walk was
    # reported finished only when it ended (96.8 s for a whole user profile).
    write(tmp_path, "a.jpg", make_jpeg())
    runner = make_runner()
    log: list = []
    _record(runner.pathsFound, log, "paths")
    _record(runner.scanFinished, log, "finished")
    _record(runner.idle, log, "idle")

    runner.scan([str(tmp_path)])
    assert slow_walk.started.wait(TIMEOUT / 1000)
    assert runner.busy()

    runner.cancel()

    # In this very call, with the walk still blocked in its thread: finished, then idle.
    assert log == [("finished",), ("idle",)]
    assert not runner.busy()

    slow_walk.release.set()  # the walk now unwinds and posts what it found...
    assert runner.wait_for_done(TIMEOUT)
    QApplication.processEvents()  # ... which is delivered, and ignored: no batch, no second finish
    assert log == [("finished",), ("idle",)]


def test_a_scan_that_ended_but_was_not_delivered_yet_is_reported_once(make_runner, qtbot, tmp_path):
    # The walk finished in its thread and its events wait for the event loop when "Cancelar" comes:
    # every scan() still ends with exactly one scanFinished, and its late batch is discarded.
    write(tmp_path, "a.jpg", make_jpeg())
    runner = make_runner()
    log: list = []
    _record(runner.pathsFound, log, "paths")
    _record(runner.scanFinished, log, "finished")
    _record(runner.idle, log, "idle")
    runner.scan([str(tmp_path)])
    assert runner.wait_for_done(TIMEOUT)  # over in its thread; nothing delivered yet

    runner.cancel()

    assert log == [("finished",), ("idle",)]
    QApplication.processEvents()  # the batch it posted and its own finish event
    assert log == [("finished",), ("idle",)]


def test_cancel_reports_a_walk_but_idle_waits_for_the_running_analysis(make_runner, slow_walk, monkeypatch, qtbot,
                                                                      tmp_path):
    real_inspect = siftool.inspect
    analysis_started = threading.Event()
    analysis_release = threading.Event()

    def blocking_inspect(path):
        analysis_started.set()
        analysis_release.wait(TIMEOUT / 1000)
        return real_inspect(path)

    monkeypatch.setattr(siftool, "inspect", blocking_inspect)
    png = write(tmp_path, "a.png", make_png())
    runner = make_runner(max_workers=2)  # the analysis and the walk run side by side
    log: list = []
    _record(runner.scanFinished, log, "finished")
    _record(runner.analyzed, log, "analyzed")
    _record(runner.idle, log, "idle")

    try:
        runner.analyze(1, png)
        runner.scan([str(tmp_path)])
        assert analysis_started.wait(TIMEOUT / 1000) and slow_walk.started.wait(TIMEOUT / 1000)

        runner.cancel()

        assert log == [("finished",)]  # the walk is over; the analysis still runs: not idle yet
        assert runner.busy()
    finally:
        slow_walk.release.set()
        analysis_release.set()

    with qtbot.waitSignal(runner.idle, timeout=TIMEOUT):
        pass
    assert runner.wait_for_done(TIMEOUT)
    QApplication.processEvents()
    assert [entry[0] for entry in log] == ["finished", "analyzed", "idle"]  # the walk said nothing more


def test_cancel_reports_each_scan_once_whether_it_ran_or_not(make_runner, slow_walk, qtbot, tmp_path):
    write(tmp_path, "a.jpg", make_jpeg())
    runner = make_runner(max_workers=1)
    finished: list = []
    runner.scanFinished.connect(lambda: finished.append(1))

    runner.scan([str(tmp_path)])  # runs, blocked in the only worker
    assert slow_walk.started.wait(TIMEOUT / 1000)
    runner.scan([str(tmp_path)])  # waits in the pool's queue behind it
    runner.cancel()

    assert finished == [1, 1]  # the one that never started and the one that was running
    slow_walk.release.set()
    assert runner.wait_for_done(TIMEOUT)
    QApplication.processEvents()
    assert finished == [1, 1]


def test_busy_and_wait_for_done(make_runner, qtbot, tmp_path):
    png = write(tmp_path, "a.png", make_png())
    runner = make_runner()
    assert not runner.busy()

    with qtbot.waitSignal(runner.idle, timeout=TIMEOUT):
        runner.analyze(1, png)
        assert runner.busy()
        assert runner.wait_for_done(TIMEOUT)

    assert not runner.busy()


def test_signals_arrive_on_gui_thread(make_runner, qtbot, tmp_path):
    gui_thread = QApplication.instance().thread()
    jpg = write(tmp_path, "a.jpg", make_jpeg())
    junk = write(tmp_path, "junk.png", PNG_SIGNATURE + b"garbage" * 20)

    runner = make_runner()
    seen: dict[str, list[bool]] = {}

    def recorder(name):
        return lambda *args: seen.setdefault(name, []).append(QThread.currentThread() is gui_thread)

    for name in ("pathsFound", "scanFinished", "analyzed", "analysisFailed", "thumbnailReady", "cleaned",
                 "cleanFailed", "idle"):
        getattr(runner, name).connect(recorder(name))

    with qtbot.waitSignal(runner.idle, timeout=TIMEOUT):
        runner.scan([str(tmp_path)])
        runner.analyze(1, jpg)
        runner.analyze(2, junk)
        runner.thumbnail(1, jpg)
        runner.clean(1, jpg, tmp_path / "out" / "a_clean.jpg", Mode.PRIVACY)  # missing folder: fails
        runner.clean(3, jpg, tmp_path / "a_clean.jpg", Mode.MAXIMUM)

    assert set(seen) == {"pathsFound", "scanFinished", "analyzed", "analysisFailed", "thumbnailReady", "cleaned",
                         "cleanFailed", "idle"}
    assert all(all(flags) for flags in seen.values()), seen
