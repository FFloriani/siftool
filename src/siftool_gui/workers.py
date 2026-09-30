"""`JobRunner`: bounded background work for the GUI (spec sections 4.3-4.4).

Four kinds of job run on a private `QThreadPool` limited to
`min(4, os.cpu_count() or 1)` threads (spec 4.4, "Concorrência"): walking
dropped folders (`scan`), `siftool.inspect` (`analyze`), decoding a small
preview (`thumbnail`, at a lower priority) and `siftool.clean` (`clean`).

Threading contract:

- A job never touches the model or any widget. It only calls the engine
  (or `QImageReader`) and posts what happened, as plain Python objects,
  through a private relay signal whose connection to the runner is
  queued -- so the runner receives every job event in the thread it lives
  in (the GUI thread), keeps all of its bookkeeping there without locks,
  and re-emits the result as one of its own public signals. Every public
  signal is therefore emitted in the GUI thread, whatever kind of slot
  (method, lambda, plain function) is connected to it.
- Each job catches every `Exception` its work raises -- the engine's
  `SiftoolError`s, `OSError`, `MemoryError`, and anything unexpected --
  and reports it through the matching failure signal (a thumbnail that
  cannot be decoded is a null `QImage`). Nothing escapes `QRunnable.run()`.
  The reported exception has its traceback chain cleared first: an
  entry keeps its error for as long as it stays in the list, and the
  frames of a failed `inspect`/`clean` hold the whole file in memory.
- `cancel()` drops every job that has not started yet (spec 4.4: "Cancelar
  tira da fila o que ainda não começou; o que já começou termina"). A job
  decides atomically, under a lock shared with `cancel()`, whether it runs
  or was dropped, so each job either reports its result or is reported as
  dropped -- never both, never neither. `jobDropped` names the dropped
  `analyze`/`clean` jobs (their entries must go back to their previous
  state); a dropped thumbnail has nothing to revert and is dropped
  silently. Scans are ended too: a pending scan is dropped, and a running
  walk -- which cannot be interrupted, and on a big folder takes minutes --
  is told to stop and reported finished at once, its later batches and its
  own finish ignored; either way, every `scan()` call ends with exactly one
  `scanFinished`.

The runner must be created, used and destroyed in the GUI thread.
"""

from __future__ import annotations

import itertools
import os
import threading
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QSize, Qt, QThreadPool, Signal, Slot
from PySide6.QtGui import QImage, QImageReader

import siftool
from siftool.batch import iter_inputs
from siftool.detect import detect
from siftool.model import Mode

SCAN_BATCH_SIZE = 200
THUMBNAIL_SIZE = 96
HEAD_BYTES = 64  # how much of a file the analysis reads to tell its format apart (`detect` needs 12)

# QThreadPool runs higher priorities first. Scans only walk folders and feed
# the list, so they jump the queue; thumbnails wait for the real work.
_PRIORITY_SCAN = 2
_PRIORITY_WORK = 1
_PRIORITY_THUMBNAIL = 0

_PENDING = "pending"
_RUNNING = "running"
_DROPPED = "dropped"

# Job kinds; "analyze" and "clean" are also the names `jobDropped` reports.
_SCAN = "scan"
_ANALYZE = "analyze"
_THUMBNAIL = "thumbnail"
_CLEAN = "clean"


def default_max_workers() -> int:
    """Spec 4.4: at most `min(4, cores)` jobs at once -- the engine reads whole files (up to 512 MB)."""
    return min(4, os.cpu_count() or 1)


def _detached(exc: BaseException) -> BaseException:
    """Clear the traceback of `exc` and of every exception chained to it; return `exc`."""
    seen: set[int] = set()
    stack: list[BaseException | None] = [exc]
    while stack:
        current = stack.pop()
        if current is None or id(current) in seen:
            continue
        seen.add(id(current))
        current.__traceback__ = None
        stack.extend((current.__cause__, current.__context__))
    return exc


def read_thumbnail(path: Path, size: int = THUMBNAIL_SIZE) -> QImage:
    """Decode `path` into a `QImage` that fits a `size` x `size` box; a null `QImage` on failure.

    Uses only Qt's own decoders, applies the EXIF orientation
    (`setAutoTransform`), never upscales, and keeps everything in memory.
    Safe to call off the GUI thread (`QImage`, unlike `QPixmap`, is).
    """
    reader = QImageReader(str(path))
    reader.setAutoTransform(True)
    original = reader.size()
    if original.isValid() and (original.width() > size or original.height() > size):
        target = original.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio)
        reader.setScaledSize(QSize(max(1, target.width()), max(1, target.height())))
    image = reader.read()
    if image.isNull():
        return QImage()
    if image.width() > size or image.height() > size:  # a decoder that ignored the scaled size
        image = image.scaled(
            size, size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
        )
    return image


class _Relay(QObject):
    """Carries job events from pool threads to the runner.

    Created in the GUI thread and unparented, shared by the runner and its
    jobs: if the runner is destroyed while a job still runs, Qt drops the
    queued connection and the job's last events simply go nowhere.
    """

    event = Signal(object)  # (serial, event name, payload)


class _Job(QRunnable):
    """One unit of background work; `work(job)` returns the `(ok, value)` outcome."""

    def __init__(
        self,
        serial: int,
        kind: str,
        entry_id: int | None,
        work: Callable[["_Job"], tuple[bool, object]],
        lock: threading.Lock,
        relay: _Relay,
    ) -> None:
        super().__init__()
        self.serial = serial
        self.kind = kind
        self.entry_id = entry_id
        self.state = _PENDING  # guarded by `lock`, shared with `JobRunner.cancel`
        self.stop_requested = False  # set by `cancel()` for a scan that is already running
        self._work = work
        self._lock = lock
        self._relay = relay

    def post(self, name: str, payload: object = None) -> None:
        try:
            self._relay.event.emit((self.serial, name, payload))
        except Exception:  # pragma: no cover -- only when the whole GUI is already gone
            pass

    def run(self) -> None:
        with self._lock:
            if self.state != _PENDING:  # dropped by `cancel()` before a thread picked it up
                return
            self.state = _RUNNING
        outcome: tuple[bool, object] = (False, None)
        try:
            outcome = self._work(self)
        except BaseException as exc:  # pragma: no cover -- every work function catches Exception itself
            outcome = (False, _detached(exc))
        finally:
            self.post("finish", outcome)


def _scan_work(paths: list[str]) -> Callable[[_Job], tuple[bool, object]]:
    def work(job: _Job) -> tuple[bool, object]:
        total = 0  # how many files the walk found: what `JobRunner.scanEmpty` is about
        for raw in paths:
            if job.stop_requested:
                break
            try:
                found = [str(path) for path in iter_inputs([raw])]
            except Exception:  # an unreadable folder adds nothing; the other inputs still count
                continue
            total += len(found)
            for start in range(0, len(found), SCAN_BATCH_SIZE):
                if job.stop_requested:
                    break
                job.post("batch", found[start : start + SCAN_BATCH_SIZE])
        return True, total

    return work


def _read_head(path: Path) -> bytes:
    """The first `HEAD_BYTES` bytes of `path`; empty when the file is empty or cannot be opened.

    An unreadable file is not reported here: `siftool.inspect` meets the
    same error reading the file and reports it in its own words.
    """
    try:
        with open(path, "rb") as file:
            return file.read(HEAD_BYTES)
    except OSError:
        return b""


def _analyze_work(path: Path) -> Callable[[_Job], tuple[bool, object]]:
    def work(job: _Job) -> tuple[bool, object]:
        try:
            # The engine checks its 512 MB cap *before* it detects the format, and reads the whole
            # file even to refuse it: a big video would say "too big" (spec 3, criterion 3) and every
            # video would sit in memory first. So the format is named from the first bytes; a known
            # one the engine cannot clean raises `UnsupportedFormatError` here, and the file is never
            # read whole. Empty and unreadable files go through the engine, which has words for them.
            head = _read_head(path)
            if head:
                detect(head)
            return True, siftool.inspect(path)  # looked up at call time: tests slow it down
        except Exception as exc:
            return False, _detached(exc)

    return work


def _thumbnail_work(path: Path, size: int) -> Callable[[_Job], tuple[bool, object]]:
    def work(job: _Job) -> tuple[bool, object]:
        try:
            return True, read_thumbnail(path, size)
        except Exception:  # a preview that fails is just a generic icon (spec section 9)
            return True, QImage()

    return work


def _clean_work(src: Path, dst: Path, mode: Mode) -> Callable[[_Job], tuple[bool, object]]:
    def work(job: _Job) -> tuple[bool, object]:
        try:
            return True, siftool.clean(src, dst, mode)
        except Exception as exc:
            return False, _detached(exc)

    return work


class JobRunner(QObject):
    """Runs scans, analyses, thumbnails and cleans on a bounded thread pool (spec 4.4).

    All signals are emitted in the GUI thread (see the module docstring).
    `analyze`/`clean` results and failures carry the caller's `entry_id`.
    """

    pathsFound = Signal(list)  # list[str], batches of at most SCAN_BATCH_SIZE
    scanEmpty = Signal()  # a walk that reached its end without finding a file; right before its `scanFinished`
    scanFinished = Signal()
    analyzed = Signal(int, object)  # entry_id, Report
    analysisFailed = Signal(int, object)  # entry_id, exception
    thumbnailReady = Signal(int, QImage)  # a null QImage when decoding fails
    cleaned = Signal(int, object)  # entry_id, CleanResult
    cleanFailed = Signal(int, object)  # entry_id, exception
    jobDropped = Signal(int, str)  # entry_id, "analyze" | "clean" (cancelled before starting)
    idle = Signal()  # nothing pending or running any more (bar a cancelled walk still unwinding in its thread)

    def __init__(self, parent: QObject | None = None, max_workers: int | None = None) -> None:
        super().__init__(parent)
        if max_workers is not None and max_workers < 1:
            raise ValueError(f"max_workers must be at least 1, not {max_workers}")
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(max_workers if max_workers is not None else default_max_workers())
        self._lock = threading.Lock()
        self._jobs: dict[int, _Job] = {}  # submitted and neither finished nor dropped; GUI thread only
        self._serials = itertools.count(1)
        self._relay = _Relay()
        self._relay.event.connect(self._on_event, Qt.ConnectionType.QueuedConnection)

    # -- submitting ---------------------------------------------------------

    def scan(self, paths: list[str]) -> None:
        """Walk `paths` (`siftool.batch.iter_inputs`); report files via `pathsFound`, then `scanFinished`.

        A walk that finds no file at all -- and was not cancelled -- also
        reports `scanEmpty`, just before `scanFinished`.
        """
        self._submit(_SCAN, None, _scan_work([str(path) for path in paths]), _PRIORITY_SCAN)

    def analyze(self, entry_id: int, path: Path) -> None:
        """`siftool.inspect(path)` -> `analyzed(entry_id, report)` or `analysisFailed(entry_id, exc)`."""
        self._submit(_ANALYZE, entry_id, _analyze_work(Path(path)), _PRIORITY_WORK)

    def thumbnail(self, entry_id: int, path: Path, size: int = THUMBNAIL_SIZE) -> None:
        """Decode a preview at most `size` px on each side -> `thumbnailReady` (lower priority)."""
        self._submit(_THUMBNAIL, entry_id, _thumbnail_work(Path(path), size), _PRIORITY_THUMBNAIL)

    def clean(self, entry_id: int, src: Path, dst: Path, mode: Mode) -> None:
        """`siftool.clean(src, dst, mode)` -> `cleaned(entry_id, result)` or `cleanFailed(entry_id, exc)`."""
        self._submit(_CLEAN, entry_id, _clean_work(Path(src), Path(dst), Mode(mode)), _PRIORITY_WORK)

    def _submit(self, kind: str, entry_id: int | None, work, priority: int) -> None:
        job = _Job(next(self._serials), kind, entry_id, work, self._lock, self._relay)
        self._jobs[job.serial] = job
        self._pool.start(job, priority)

    # -- control ------------------------------------------------------------

    def cancel(self) -> None:
        """Drop every job not started yet and end every scan; other running jobs finish (and still report).

        Emits `jobDropped(entry_id, kind)` for each dropped `analyze`/`clean`
        job and `scanFinished` for each scan, in submission order -- a scan
        that never started and a walk that is running alike. Then `idle` if
        nothing else is left running.

        A running walk cannot be interrupted (listing a big folder takes
        minutes), so it is told to stop *and* reported finished right away,
        and forgotten: its thread may keep going for a while, and what it
        still posts -- batches, its own finish -- is ignored.
        """
        ended: list[_Job] = []  # dropped before they started, and the walks that are running
        with self._lock:
            for job in self._jobs.values():
                if job.state == _PENDING:
                    job.state = _DROPPED
                    ended.append(job)
                elif job.kind == _SCAN:
                    job.stop_requested = True
                    ended.append(job)
        self._pool.clear()  # frees the queue; a job a thread took meanwhile sees _DROPPED and returns

        was_busy = bool(self._jobs)
        for job in ended:
            del self._jobs[job.serial]
        for job in ended:
            if job.kind in (_ANALYZE, _CLEAN):
                self.jobDropped.emit(job.entry_id, job.kind)
            elif job.kind == _SCAN:
                self.scanFinished.emit()
        if was_busy and not self._jobs:
            self.idle.emit()

    def busy(self) -> bool:
        """Whether any job is pending or running (as last reported to the GUI thread).

        A walk that `cancel()` ended no longer counts; `wait_for_done` still waits for its thread.
        """
        return bool(self._jobs)

    def max_workers(self) -> int:
        """How many jobs may run at the same time."""
        return self._pool.maxThreadCount()

    def wait_for_done(self, msecs: int = -1) -> bool:
        """Block until no job is running or queued (or `msecs` elapse); True if all are done.

        The results of the jobs that finished are delivered as signals
        once control returns to the event loop, not during this call.
        """
        return self._pool.waitForDone(msecs)

    # -- job events (GUI thread) ------------------------------------------

    @Slot(object)
    def _on_event(self, event: tuple[int, str, object]) -> None:
        serial, name, payload = event
        job = self._jobs.get(serial)
        if job is None:  # a walk `cancel()` already reported finished: what it still posts goes nowhere
            return

        if name == "batch":
            self.pathsFound.emit(payload)
            return

        del self._jobs[serial]
        ok, value = payload
        if job.kind == _SCAN:
            if ok and value == 0:  # walked to the end and found nothing (a cancelled walk never gets here)
                self.scanEmpty.emit()
            self.scanFinished.emit()
        elif job.kind == _ANALYZE:
            (self.analyzed if ok else self.analysisFailed).emit(job.entry_id, value)
        elif job.kind == _THUMBNAIL:
            self.thumbnailReady.emit(job.entry_id, value if isinstance(value, QImage) else QImage())
        elif job.kind == _CLEAN:
            (self.cleaned if ok else self.cleanFailed).emit(job.entry_id, value)
        if not self._jobs:
            self.idle.emit()
