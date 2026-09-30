"""Tests for `siftool_gui.shell` (spec 4.4, item 5; Task 11). No Qt needed."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from siftool_gui import shell


def test_explorer_command_quotes_commas():
    # Explorer's /select, splits an unquoted path at the comma: the whole path is quoted.
    command = shell.explorer_select_command(Path(r"C:\a b, c\x.png"))

    assert command == 'explorer /select,"C:\\a b, c\\x.png"'


@pytest.mark.skipif(os.name != "nt", reason="a relative path resolves against a Windows working folder")
def test_explorer_command_uses_the_absolute_path(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    folder = tmp_path / "26 de set. de 2026, fotos"
    folder.mkdir()

    command = shell.explorer_select_command(Path("26 de set. de 2026, fotos") / "fotó ção.png")

    assert command == f'explorer /select,"{os.path.abspath(folder / "fotó ção.png")}"'


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
    photo = tmp_path / "a, b.png"

    shell.reveal_in_explorer(photo)
    shell.open_folder(tmp_path)

    assert launched == [f'explorer /select,"{photo}"', ("startfile", str(tmp_path))]
