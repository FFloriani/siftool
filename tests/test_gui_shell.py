"""Tests for `siftool_gui.shell` (spec 4.4, item 5; Task 11). No Qt needed."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from siftool_gui import shell


def test_explorer_command_quotes_commas(monkeypatch):
    # Explorer's /select, splits an unquoted path at the comma: the whole path is quoted.
    monkeypatch.setenv("SystemRoot", r"C:\Windows")

    command = shell.explorer_select_command(Path(r"C:\a b, c\x.png"))

    assert command == '"C:\\Windows\\explorer.exe" /select,"C:\\a b, c\\x.png"'


def test_explorer_command_starts_explorer_by_its_full_path(monkeypatch):
    # Final review: a bare `explorer` makes Windows look in the current folder before anything else,
    # so a planted explorer.exe beside the photos would run. The path comes from %SystemRoot%, and is
    # quoted (a Windows folder can have a space in its name: an unquoted path would be cut there).
    monkeypatch.setenv("SystemRoot", r"D:\Windows 11")
    assert shell.explorer_select_command(Path(r"C:\fotos\x.png")) == (
        '"D:\\Windows 11\\explorer.exe" /select,"C:\\fotos\\x.png"'
    )

    monkeypatch.delenv("SystemRoot")
    command = shell.explorer_select_command(Path(r"C:\fotos\x.png"))
    assert command == '"C:\\Windows\\explorer.exe" /select,"C:\\fotos\\x.png"'
    assert not command.startswith("explorer")


@pytest.mark.skipif(os.name != "nt", reason="a relative path resolves against a Windows working folder")
def test_explorer_command_uses_the_absolute_path(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SystemRoot", r"C:\Windows")
    folder = tmp_path / "26 de set. de 2026, fotos"
    folder.mkdir()

    command = shell.explorer_select_command(Path("26 de set. de 2026, fotos") / "fotó ção.png")

    assert command == f'"C:\\Windows\\explorer.exe" /select,"{os.path.abspath(folder / "fotó ção.png")}"'


@pytest.mark.skipif(os.name != "nt", reason="Explorer only exists on Windows")
def test_the_real_system_root_holds_explorer():
    executable = shell.explorer_select_command(Path(r"C:\x.png")).split('"')[1]  # what the command starts

    assert os.path.isabs(executable)
    assert os.path.isfile(executable), executable


def test_shell_imports_no_qt():
    code = "import sys, siftool_gui.shell; print(any(m.startswith('PySide6') for m in sys.modules))"
    env = dict(os.environ, PYTHONPATH=os.pathsep.join([str(Path(__file__).parents[1] / "src")]))
    result = subprocess.run([sys.executable, "-P", "-c", code], capture_output=True, text=True, env=env,
                            timeout=60, cwd=Path(__file__).parent)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "False"


@pytest.mark.skipif(os.name != "nt", reason="Explorer and os.startfile are Windows-only")
def test_reveal_and_open_folder_on_windows(monkeypatch, tmp_path):
    launched: list = []
    monkeypatch.setattr(shell.subprocess, "Popen", lambda command: launched.append(command))
    monkeypatch.setattr(shell.os, "startfile", lambda path: launched.append(("startfile", path)), raising=False)
    monkeypatch.setenv("SystemRoot", r"C:\Windows")
    photo = tmp_path / "a, b.png"

    shell.reveal_in_explorer(photo)
    shell.open_folder(tmp_path)

    assert launched == [f'"C:\\Windows\\explorer.exe" /select,"{photo}"', ("startfile", str(tmp_path))]
