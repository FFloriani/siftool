"""Tests for the pure helpers of `packaging/build.py` (distribution spec 2: the version in each place it goes).

The build itself -- PyInstaller, the self-tests, Inno Setup -- runs with
`.venv/Scripts/python.exe packaging/build.py`, not here. `packaging/` is not a
package (its name is taken by the `packaging` library), so the script is
loaded from its file.
"""

from __future__ import annotations

import importlib.util
import zipfile
from pathlib import Path

import pytest

import siftool

BUILD_SCRIPT = Path(__file__).parents[1] / "packaging" / "build.py"


@pytest.fixture(scope="module")
def build():
    spec = importlib.util.spec_from_file_location("siftool_build_script", BUILD_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_build_reads_the_version_from_the_package(build):
    assert build.read_version() == siftool.__version__


@pytest.mark.parametrize(
    ("version", "release", "product", "numbers"),
    [
        ("2.0.0b1", "2.0.0-beta.1", "2.0.0 beta 1", (2, 0, 0, 1)),
        ("2.0.0b12", "2.0.0-beta.12", "2.0.0 beta 12", (2, 0, 0, 12)),
        ("2.1.0a3", "2.1.0-alpha.3", "2.1.0 alpha 3", (2, 1, 0, 3)),
        ("2.0.0rc2", "2.0.0-rc.2", "2.0.0 rc 2", (2, 0, 0, 2)),
        ("2.0.0", "2.0.0", "2.0.0", (2, 0, 0, 0)),
    ],
)
def test_version_in_each_place_it_goes(build, version, release, product, numbers):
    assert build.release_name(version) == release  # file names, the installer
    assert build.product_version(version) == product  # ProductVersion of the version resource
    assert build.file_version(version) == numbers  # FileVersion: four numbers


@pytest.mark.parametrize("version", ["2.0", "2.0.0.dev0", "2.0.0b", "2.0.0-beta.1", "v2.0.0", "2.0.0b1+local"])
def test_versions_it_cannot_name_are_refused(build, version):
    with pytest.raises(ValueError):
        build.release_name(version)


def test_version_resource_says_the_versions(build):
    text = build.version_info("2.0.0b1")

    assert "filevers=(2, 0, 0, 1)" in text and "prodvers=(2, 0, 0, 1)" in text
    assert "StringStruct('FileVersion', '2.0.0.1')" in text
    assert "StringStruct('ProductVersion', '2.0.0 beta 1')" in text
    assert "StringStruct('OriginalFilename', 'Siftool.exe')" in text
    assert "flags=0x2" in text  # VS_FF_PRERELEASE
    assert "flags=0x0" in build.version_info("2.0.0")


def test_version_resource_is_what_pyinstaller_reads(build, tmp_path):
    versioninfo = pytest.importorskip("PyInstaller.utils.win32.versioninfo")
    path = tmp_path / "version_info.txt"
    path.write_text(build.version_info("2.0.0b1"), encoding="utf-8")

    info = versioninfo.load_version_info_from_text_file(str(path))

    assert (info.ffi.fileVersionMS, info.ffi.fileVersionLS) == ((2 << 16) | 0, (0 << 16) | 1)


def test_the_portable_marker_is_the_one_the_program_looks_for(build):
    settings = pytest.importorskip("siftool_gui.settings")  # the program's side needs the `gui` extra

    assert build.PORTABLE_MARKER == settings.PORTABLE_MARKER
    assert build.PORTABLE_DATA_FOLDER == settings.PORTABLE_DATA_FOLDER


def test_portable_zip_holds_one_folder_with_the_marker_and_the_readme(build, tmp_path):
    program = tmp_path / "Siftool"
    (program / "_internal" / "PySide6").mkdir(parents=True)
    (program / "Siftool.exe").write_bytes(b"MZ")
    (program / "_internal" / "PySide6" / "Qt6Core.dll").write_bytes(b"MZ")

    target = build.make_portable_zip(program, tmp_path / "Siftool-2.0.0-beta.1-portable.zip")

    with zipfile.ZipFile(target) as archive:
        names = sorted(archive.namelist())
        marker = archive.read("Siftool/Siftool.portable").decode("utf-8")
        readme = archive.read("Siftool/LEIA-ME.txt").decode("utf-8")
    assert names == [
        "Siftool/LEIA-ME.txt",
        "Siftool/Siftool.exe",
        "Siftool/Siftool.portable",
        "Siftool/_internal/PySide6/Qt6Core.dll",
    ]
    assert "dados" in marker
    assert "Siftool.exe" in readme and "dados" in readme
