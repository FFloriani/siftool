"""Tests for `siftool_gui.single_instance`: one window per settings file (distribution spec 2).

Every instance here is named after a settings file under `tmp_path` and
keeps its lock file there, so a real Siftool window open on this machine is
never involved. The first instance lives in this process; the second one
sends its paths from a process of its own, exactly as a second launch does:
`send_paths` blocks in Qt holding the GIL, so from a thread of this process
the first instance's Python slots could never answer it.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from fixtures.make_fixtures import make_png, write
from siftool_gui.single_instance import (
    MAX_MESSAGE_BYTES,
    SingleInstance,
    decode_message,
    encode_message,
    send_paths,
    server_name,
)

TIMEOUT = 10_000  # ms
PROCESS_TIMEOUT = 60_000  # ms; a fresh Python process importing Qt

_SECOND_LAUNCH = textwrap.dedent(
    """
    import json, sys
    from siftool_gui.single_instance import send_paths
    sys.exit(0 if send_paths(sys.argv[1], json.loads(sys.argv[2]), int(sys.argv[3])) else 1)
    """
)


@pytest.fixture
def make_instance(tmp_path):
    instances = []

    def factory(config: str = "cfg.json") -> SingleInstance:
        instance = SingleInstance(tmp_path / config, lock_dir=tmp_path)
        instances.append(instance)
        return instance

    yield factory
    for instance in instances:
        instance.release()


def _send_from_another_process(qtbot, name: str, paths: list[str]) -> bool:
    """`send_paths` in a process of its own, while this one's event loop runs (and the first instance answers)."""
    second = subprocess.Popen([sys.executable, "-P", "-c", _SECOND_LAUNCH, name, json.dumps(paths), str(TIMEOUT)])
    try:
        qtbot.waitUntil(lambda: second.poll() is not None, timeout=PROCESS_TIMEOUT)
    finally:
        if second.poll() is None:
            second.kill()
            second.wait()
    return second.returncode == 0


def _received(qtbot, first: SingleInstance, name: str, paths: list[str]) -> tuple[list, bool]:
    with qtbot.waitSignal(first.pathsReceived, timeout=PROCESS_TIMEOUT) as signal:
        sent = _send_from_another_process(qtbot, name, paths)
    return signal.args[0], sent


def test_names_are_per_settings_file(tmp_path):
    name = server_name(tmp_path / "Siftool" / "config.json")

    assert re.fullmatch(r"siftool-[0-9a-f]{16}", name)
    assert server_name(tmp_path / "SIFTOOL" / "CONFIG.JSON") == name  # Windows paths ignore case
    assert server_name(tmp_path / "Portable" / "dados" / "config.json") != name


def test_messages_round_trip_non_ascii_paths():
    paths = [r"C:\Usuários\João\Fotos\ação 日本 ü.jpg", r"D:\Família"]
    data = encode_message(paths)

    assert data.endswith(b"\n") and data.count(b"\n") == 1
    assert decode_message(data[:-1]) == paths


@pytest.mark.parametrize(
    "data",
    [b"not json", b"[1, 2]", b'{"paths": "C:\\\\a.jpg"}', b'{"paths": [1]}', b'{"other": []}', b"\xff\xfe"],
)
def test_malformed_messages_are_refused(data):
    with pytest.raises(ValueError):
        decode_message(data)


def test_first_instance_receives_the_paths_of_the_second(qtbot, make_instance):
    first = make_instance()
    assert first.claim()
    assert first.listen()

    paths, sent = _received(qtbot, first, first.name, [r"C:\fotos\a.jpg", r"C:\fotos"])

    assert paths == [r"C:\fotos\a.jpg", r"C:\fotos"]
    assert sent is True


def test_non_ascii_paths_arrive_intact(qtbot, make_instance, tmp_path):
    first = make_instance()
    assert first.claim() and first.listen()
    paths = [str(tmp_path / "Família" / "ação 日本 ü.jpg"), str(tmp_path / "Fotos de São Paulo")]

    received, sent = _received(qtbot, first, first.name, paths)

    assert received == paths
    assert sent is True


def test_a_launch_without_paths_still_reaches_the_first(qtbot, make_instance):
    # Opening Siftool again from the Start menu: nothing to add, but the window must come forward.
    first = make_instance()
    assert first.claim() and first.listen()

    received, sent = _received(qtbot, first, first.name, [])

    assert received == []
    assert sent is True


def test_sending_fails_when_no_instance_listens(make_instance):
    nobody = make_instance()

    assert send_paths(nobody.name, [r"C:\a.jpg"], TIMEOUT) is False


def test_a_malformed_message_is_dropped_and_the_server_keeps_serving(qtbot, make_instance):
    from PySide6.QtNetwork import QLocalSocket

    first = make_instance()
    assert first.claim() and first.listen()
    received = []
    first.pathsReceived.connect(received.append)

    rogue = QLocalSocket()
    rogue.connectToServer(first.name)
    assert rogue.waitForConnected(TIMEOUT)
    rogue.write(b"{not json}\n")
    rogue.flush()
    qtbot.waitUntil(lambda: rogue.state() == QLocalSocket.LocalSocketState.UnconnectedState, timeout=TIMEOUT)

    paths, sent = _received(qtbot, first, first.name, [r"C:\b.jpg"])
    assert received == [[r"C:\b.jpg"]] and paths == [r"C:\b.jpg"] and sent


def test_an_oversized_message_is_cut_off(qtbot, make_instance):
    from PySide6.QtNetwork import QLocalSocket

    first = make_instance()
    assert first.claim() and first.listen()
    received = []
    first.pathsReceived.connect(received.append)

    rogue = QLocalSocket()
    rogue.connectToServer(first.name)
    assert rogue.waitForConnected(TIMEOUT)
    rogue.write(b"x" * (MAX_MESSAGE_BYTES + 1))  # never a line end: it would grow without bound
    qtbot.waitUntil(lambda: rogue.state() == QLocalSocket.LocalSocketState.UnconnectedState, timeout=TIMEOUT)

    assert received == []


def test_only_one_instance_claims(make_instance):
    first, second = make_instance(), make_instance()

    assert first.claim()
    assert not second.claim()
    first.release()
    assert second.claim()  # the first window closed: the next launch takes over


def test_instances_of_other_settings_files_do_not_meet(make_instance):
    installed, portable = make_instance("cfg.json"), make_instance("portable.json")

    assert installed.name != portable.name
    assert installed.claim()
    assert portable.claim()


def test_a_lock_left_by_a_crashed_instance_is_taken_over(qtbot, make_instance, tmp_path):
    # A Siftool that crashed leaves its lock file behind (spec 2's stale name, as it happens on Windows):
    # the next launch clears it and becomes the first instance.
    first = make_instance()
    lock = Path(first.lock_file)
    crash = textwrap.dedent(
        """
        import os, sys
        from PySide6.QtCore import QLockFile
        lock = QLockFile(sys.argv[1])
        assert lock.tryLock(0)
        os._exit(0)  # no unlock: as if it had crashed
        """
    )
    subprocess.run([sys.executable, "-P", "-c", crash, str(lock)], check=True, timeout=60)
    assert lock.exists()

    assert first.claim()
    assert first.listen()
    received, sent = _received(qtbot, first, first.name, [r"C:\c.jpg"])
    assert received == [r"C:\c.jpg"] and sent

    first.release()
    assert not lock.exists()


def test_second_launch_hands_its_paths_to_the_first_window(tmp_path):
    # The real `main()`, twice, in two processes (off-screen), sharing an APPDATA -- so one settings
    # file, one window. The first reports when it listens, and what reaches its window, then closes.
    photo = write(tmp_path, "ação.png", make_png())
    received_file = tmp_path / "recebido.json"
    first_code = textwrap.dedent(
        """
        import json, sys
        from siftool_gui import app, single_instance
        from siftool_gui.window import MainWindow

        listen = single_instance.SingleInstance.listen

        def listen_and_say_so(self):
            listening = listen(self)
            print("listening" if listening else "not listening", flush=True)
            return listening

        def record_and_close(self, paths):
            with open(sys.argv[1], "w", encoding="utf-8") as file:
                json.dump(paths, file, ensure_ascii=False)
            self.close()

        single_instance.SingleInstance.listen = listen_and_say_so
        MainWindow.open_paths = record_and_close
        raise SystemExit(app.main([]))
        """
    )
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", APPDATA=str(tmp_path / "appdata"),
               TEMP=str(tmp_path), TMP=str(tmp_path), TMPDIR=str(tmp_path))
    first_errors = tmp_path / "first.err"

    with first_errors.open("w", encoding="utf-8") as errors:
        first = subprocess.Popen([sys.executable, "-P", "-c", first_code, str(received_file)], cwd=tmp_path,
                                 env=env, stdout=subprocess.PIPE, stderr=errors, text=True)
        try:
            assert _first_word(first.stdout, ("listening", "not listening")) == "listening"
            second = subprocess.run([sys.executable, "-P", "-m", "siftool_gui", str(photo)], cwd=tmp_path,
                                    env=env, capture_output=True, text=True, timeout=60)
            assert second.returncode == 0, second.stderr
            assert first.wait(timeout=60) == 0
        finally:
            if first.poll() is None:
                first.kill()
                first.wait()
            first.stdout.close()

    assert json.loads(received_file.read_text(encoding="utf-8")) == [str(photo)], first_errors.read_text("utf-8")
    assert not list(tmp_path.glob("siftool-*.lock"))  # the first released its lock on the way out


def _first_word(stream, words: tuple[str, ...]) -> str | None:
    """The first line of `stream` that is one of `words` (QFluentWidgets prints a banner first); None at its end."""
    for line in stream:
        if line.strip() in words:
            return line.strip()
    return None
