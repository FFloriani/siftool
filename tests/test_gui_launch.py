"""Tests for the program's command line (`siftool_gui.app`; distribution spec 2).

Qt-free, like `tests/test_gui_entries.py`: reading the command line and
answering `--version` never import Qt, so these run without the `gui` extra.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

import siftool
from siftool_gui.app import Launch, main, parse_args


def test_no_arguments_opens_an_empty_window():
    assert parse_args([]) == Launch()


def test_files_and_folders_become_absolute_paths(tmp_path, monkeypatch):
    # A second launch hands its paths to a window started elsewhere: relative ones would mean nothing there.
    monkeypatch.chdir(tmp_path)
    launch = parse_args(["foto.jpg", str(tmp_path / "pasta"), "Família/ação 日本.png"])

    assert launch.paths == (
        str(tmp_path / "foto.jpg"),
        str(tmp_path / "pasta"),
        str(tmp_path / "Família" / "ação 日本.png"),
    )


def test_qt_options_go_to_qt_with_their_values(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    launch = parse_args(["-style", "fusion", "foto.jpg", "-reverse", "--platform", "offscreen", "-stylesheet=x.qss"])

    assert launch.qt_args == ("-style", "fusion", "-reverse", "--platform", "offscreen", "-stylesheet=x.qss")
    assert launch.paths == (str(tmp_path / "foto.jpg"),)


def test_everything_after_a_double_dash_is_a_path(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    launch = parse_args(["--", "-foto.jpg", "--version"])

    assert launch.paths == (str(tmp_path / "-foto.jpg"), str(tmp_path / "--version"))
    assert not launch.version


def test_version_and_its_file():
    assert parse_args(["--version"]) == Launch(version=True)
    assert parse_args(["--version", "--version-file", "v.txt"]) == Launch(version=True, version_file="v.txt")
    assert parse_args(["--version-file=v.txt"]) == Launch(version=True, version_file="v.txt")  # the file alone asks too


def test_self_test_takes_its_result_file():
    assert parse_args(["--self-test", "r.json"]) == Launch(self_test="r.json")
    assert parse_args(["--self-test=r.json"]) == Launch(self_test="r.json")


@pytest.mark.parametrize("args", [["--self-test"], ["--version-file"], ["--version", "--version-file="]])
def test_an_option_without_its_value_is_refused(args):
    with pytest.raises(ValueError):
        parse_args(args)
    assert main(args) == 2  # nothing started


def test_version_prints_and_writes_its_file(tmp_path, capsys):
    target = tmp_path / "versão.txt"

    assert main(["--version", "--version-file", str(target)]) == 0

    line = f"siftool-gui {siftool.__version__}"
    assert capsys.readouterr().out.strip() == line
    assert target.read_text(encoding="utf-8") == line + "\n"


def test_version_without_a_console(tmp_path, monkeypatch):
    # Distribution spec 2: the frozen program is windowed, so `sys.stdout` and `sys.stderr` are None there.
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    target = tmp_path / "v.txt"

    assert main(["--version"]) == 0
    assert main(["--version-file", str(target)]) == 0
    assert main(["--self-test"]) == 2  # a refused command line says nothing either, and does not crash

    assert target.read_text(encoding="utf-8") == f"siftool-gui {siftool.__version__}\n"


def test_version_file_that_cannot_be_written_fails(tmp_path):
    assert main(["--version-file", str(tmp_path / "missing" / "v.txt")]) == 1


def test_version_file_from_the_entry_point(tmp_path):
    # As the build calls the frozen program: `-m siftool_gui`, `-P` so the legacy siftool.py never shadows it.
    result = subprocess.run(
        [sys.executable, "-P", "-m", "siftool_gui", "--version", "--version-file", "v.txt"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 0, result.stderr
    assert (tmp_path / "v.txt").read_text(encoding="utf-8") == f"siftool-gui {siftool.__version__}\n"
