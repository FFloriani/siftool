"""One Siftool window per settings file: a second launch hands its paths to the first (distribution spec 2).

Windows starts Siftool once per file: "Limpar com Siftool" on ten selected
photos runs `Siftool.exe <photo>` ten times, nearly at once. The first
launch opens the window; every other one sends its paths to that window --
which adds them and comes to the front -- and exits.

- Who is first: a `QLockFile` in the user's temporary folder. On Windows a
  `QLocalServer` listens on a name another one already listens on without
  complaint, so the server alone cannot tell launches racing each other
  apart; the lock can. A lock left behind by a Siftool that crashed names a
  process that is gone: the next launch clears it and takes over (the
  "stale name" of spec 2). Outside Windows a crash also leaves the server's
  socket file behind: `listen` removes it and listens again.
- The messages: the first instance's `QLocalServer` (a named pipe on
  Windows, open to this user only) takes one line of UTF-8 JSON,
  `{"paths": [...]}`, and answers `ok`. `send_paths` waits for that answer
  with Qt's blocking calls, which keep Python's GIL: it must run in another
  process than the first instance (as a second launch does), never in a
  thread beside it -- the server's Python slots could not answer.
- The name (`server_name`) comes from the settings file: per user -- a pipe
  name is shared by the whole machine, every session -- and per copy of the
  program, since the installed program and a portable copy keep separate
  settings. Two windows never share one settings file, which they would
  otherwise both write.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import time
from collections.abc import Callable, Sequence
from pathlib import Path

from PySide6.QtCore import QLockFile, QObject, QThread, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

ACK = b"ok\n"
MAX_MESSAGE_BYTES = 4 * 1024 * 1024  # thousands of long paths; a client sending more is cut off
SEND_TIMEOUT_MS = 5_000  # each step of one attempt: connect, write, hear back
HAND_OVER_TIMEOUT_MS = 20_000  # how long a second launch waits for the first window to listen
RETRY_MS = 50  # between attempts, while the first window is still opening
_ASFW_ANY = 0xFFFFFFFF  # AllowSetForegroundWindow(ASFW_ANY): any process may


def server_name(config_path: str | os.PathLike[str]) -> str:
    """The name the first instance of the settings file `config_path` listens on: "siftool-" and 16 hex digits.

    How the path is written does not matter: it is made absolute and, on
    Windows, its case ignored.
    """
    key = os.path.normcase(os.path.abspath(config_path))
    return "siftool-" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def encode_message(paths: Sequence[str]) -> bytes:
    """One message: `{"paths": [...]}` as UTF-8 JSON on one line (JSON never writes a raw line end)."""
    return json.dumps({"paths": list(paths)}, ensure_ascii=False).encode("utf-8") + b"\n"


def decode_message(data: bytes) -> list[str]:
    """The paths in one message, without its line end; `ValueError` for anything that is not one."""
    message = json.loads(data.decode("utf-8"))  # UnicodeDecodeError and JSONDecodeError are ValueErrors
    paths = message.get("paths") if isinstance(message, dict) else None
    if not isinstance(paths, list) or not all(isinstance(path, str) for path in paths):
        raise ValueError("not a message: no list of paths")
    return paths


def send_paths(name: str, paths: Sequence[str], timeout_ms: int = SEND_TIMEOUT_MS) -> bool:
    """Hand `paths` to the first instance listening on `name`; True once it answered that it has them.

    Blocking -- a second launch has no event loop to wait in -- with each
    step bounded by `timeout_ms`. False at once when nobody listens on `name`.
    """
    socket = QLocalSocket()
    try:
        socket.connectToServer(name)
        if not socket.waitForConnected(timeout_ms):
            return False
        socket.write(encode_message(paths))
        while socket.bytesToWrite() > 0:
            if not socket.waitForBytesWritten(timeout_ms):
                return False
        answer = b""
        while len(answer) < len(ACK):
            if not socket.bytesAvailable() and not socket.waitForReadyRead(timeout_ms):
                return False
            answer += socket.read(len(ACK) - len(answer)).data()
        return answer == ACK
    finally:
        socket.abort()


def allow_foreground() -> None:
    """Windows: let the window already open come to the front when it gets this launch's paths.

    Windows lets the process the user just started -- this second launch --
    take the foreground, not one running in the background; this passes
    that right on.
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.AllowSetForegroundWindow.argtypes = (ctypes.c_uint32,)
        user32.AllowSetForegroundWindow.restype = ctypes.c_int
        user32.AllowSetForegroundWindow(_ASFW_ANY)
    except (AttributeError, OSError):  # nothing to hand on: the window then only flashes in the taskbar
        pass


class _Connection(QObject):
    """One second launch talking to the first instance: one message in, `ok` out, then it goes."""

    def __init__(self, socket: QLocalSocket, deliver: Callable[[list[str]], None]) -> None:
        super().__init__(socket)  # lives and dies with its socket
        self._socket = socket
        self._deliver = deliver
        self._buffer = bytearray()
        self._done = False
        socket.readyRead.connect(self._read)
        socket.disconnected.connect(socket.deleteLater)
        self._read()  # whatever arrived before the connections above

    def _read(self) -> None:
        data = self._socket.readAll().data()
        if self._done:
            return  # one message per connection: anything after it is dropped
        self._buffer += data
        end = self._buffer.find(b"\n")
        if end < 0:
            if len(self._buffer) > MAX_MESSAGE_BYTES:
                self._drop()
            return
        self._done = True
        try:
            paths = decode_message(bytes(self._buffer[:end]))
        except ValueError:
            self._drop()
            return
        self._socket.write(ACK)
        self._socket.flush()
        self._deliver(paths)

    def _drop(self) -> None:
        self._done = True
        self._socket.abort()


class SingleInstance(QObject):
    """This launch's place among the launches of one settings file (see the module docstring).

    `claim()` says whether this is the first; the first `listen()`s and
    receives the others' paths through `pathsReceived`; any other
    `hand_over()`s its own. `release()` gives the place up.
    """

    pathsReceived = Signal(list)  # list[str] from a second launch; empty when it had none

    def __init__(self, config_path: str | os.PathLike[str] | None = None,
                 lock_dir: str | os.PathLike[str] | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        if config_path is None:
            from siftool_gui.settings import default_config_path  # QFluentWidgets: only when needed

            config_path = default_config_path()
        self.name = server_name(config_path)
        folder = Path(tempfile.gettempdir() if lock_dir is None else lock_dir)
        self.lock_file = str(folder / f"{self.name}.lock")
        self._lock = QLockFile(self.lock_file)
        self._lock.setStaleLockTime(0)  # stale only when its process is gone: a window may stay open for days
        self._server: QLocalServer | None = None

    def claim(self) -> bool:
        """Whether this is the first instance (it already was, or it takes the lock now -- a crash's included)."""
        return self._lock.isLocked() or self._lock.tryLock(0)

    def another_is_first(self) -> bool:
        """After a `claim()` that failed: whether another live instance holds the place.

        False when the lock could not even be tried (an unwritable temporary
        folder): there is then nobody to hand anything to.
        """
        return not self._lock.isLocked() and self._lock.error() == QLockFile.LockError.LockFailedError

    def listen(self) -> bool:
        """Start taking the other launches' paths (`pathsReceived`); the first instance's job."""
        if self._server is not None:
            return True
        server = QLocalServer(self)
        server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)  # this user only
        if not server.listen(self.name):
            QLocalServer.removeServer(self.name)  # a crashed server's socket file, outside Windows
            if not server.listen(self.name):
                return False
        server.newConnection.connect(self._take_connections)
        self._server = server
        return True

    def hand_over(self, paths: Sequence[str], timeout_ms: int = HAND_OVER_TIMEOUT_MS) -> bool:
        """Send `paths` to the first instance; True once it has them.

        Waits up to `timeout_ms` for the first window to listen -- it may still
        be opening. False when it never answered, or when it went away
        meanwhile and this launch took its place (`claim()` then holds).
        """
        allow_foreground()
        deadline = time.monotonic() + timeout_ms / 1000
        while True:
            remaining_ms = int((deadline - time.monotonic()) * 1000)
            if send_paths(self.name, paths, max(1, min(SEND_TIMEOUT_MS, remaining_ms))):
                return True
            if self.claim() or not self.another_is_first() or time.monotonic() >= deadline:
                return False
            QThread.msleep(RETRY_MS)

    def release(self) -> None:
        """Stop listening and give the place up, so the next launch becomes the first (safe to repeat)."""
        server, self._server = self._server, None
        if server is not None:
            server.close()
        if self._lock.isLocked():
            self._lock.unlock()

    def _take_connections(self) -> None:
        while self._server is not None and self._server.hasPendingConnections():
            _Connection(self._server.nextPendingConnection(), self.pathsReceived.emit)
