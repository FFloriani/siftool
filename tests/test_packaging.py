"""Tests for the pure helpers of `packaging/build.py` (distribution spec 2: the version in each place it goes)
and for the legal files and build notes of the release (license, third-party notices, `build.bat`).

The build itself -- PyInstaller, the self-tests, Inno Setup -- runs with
`.venv/Scripts/python.exe packaging/build.py`, not here. `packaging/` is not a
package (its name is taken by the `packaging` library), so the script is
loaded from its file.
"""

from __future__ import annotations

import importlib.util
import os
import re
import stat
import zipfile
from pathlib import Path

import pytest

import siftool

ROOT = Path(__file__).parents[1]
BUILD_SCRIPT = ROOT / "packaging" / "build.py"


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


@pytest.mark.parametrize("version", ["2.0.0b1", "2.0.0b12", "2.1.0a3", "2.0.0rc2", "2.0.0", siftool.__version__])
def test_the_program_shows_the_version_the_installer_and_the_exe_show(build, version):
    # About reads "2.0.0 beta 1", as the installer's and Siftool.exe's ProductVersion do: two implementations
    # (the program's has to survive any string; the build's refuses what it cannot name), one answer.
    from siftool_gui.presentation import display_version

    assert display_version(version) == build.product_version(version)


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


# -- what a build deletes: its own outputs, nothing else ------------------------------------------------


def _touch(root: Path, *names: str) -> None:
    for name in names:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")


def _files_under(root: Path) -> list[str]:
    return sorted(path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file())


def test_a_build_removes_only_what_a_build_makes(build, tmp_path):
    # `dist/legacy` is build.bat's (the old Tkinter program); `build/siftool` is that program's work folder.
    _touch(
        tmp_path,
        "build/pyinstaller/Analysis-00.toc",
        "build/version_info.txt",
        "build/siftool/siftool.exe.manifest",
        "build/notas.txt",
        "dist/Siftool/Siftool.exe",
        "dist/Siftool/_internal/PySide6/Qt6Core.dll",
        "dist/Siftool-2.0.0-beta.1-setup.exe",
        "dist/Siftool-2.0.0-beta.1-portable.zip",
        "dist/Siftool-1.9.0-setup.exe",  # an older version's installer: this build's kind of file too
        "dist/legacy/siftool/siftool.exe",
        "dist/notas.txt",
    )

    removed = build.remove_previous_outputs(tmp_path / "build", tmp_path / "dist")

    assert _files_under(tmp_path) == [
        "build/notas.txt",
        "build/siftool/siftool.exe.manifest",
        "dist/legacy/siftool/siftool.exe",
        "dist/notas.txt",
    ]
    assert sorted(path.relative_to(tmp_path).as_posix() for path in removed) == [
        "build/pyinstaller",
        "build/version_info.txt",
        "dist/Siftool",
        "dist/Siftool-1.9.0-setup.exe",
        "dist/Siftool-2.0.0-beta.1-portable.zip",
        "dist/Siftool-2.0.0-beta.1-setup.exe",
    ]


def test_a_build_with_nothing_before_it_removes_nothing(build, tmp_path):
    assert build.remove_previous_outputs(tmp_path / "build", tmp_path / "dist") == []
    assert list(tmp_path.iterdir()) == []  # and makes nothing


def test_read_only_leftovers_are_removed_too(build, tmp_path):
    _touch(tmp_path, "dist/Siftool/_internal/base_library.zip", "dist/Siftool-2.0.0-beta.1-setup.exe")
    for path in (tmp_path / "dist" / "Siftool" / "_internal" / "base_library.zip",
                 tmp_path / "dist" / "Siftool-2.0.0-beta.1-setup.exe"):
        os.chmod(path, stat.S_IREAD)

    build.remove_previous_outputs(tmp_path / "build", tmp_path / "dist")

    assert _files_under(tmp_path) == []


def test_the_build_keeps_its_folders_apart_from_the_legacy_ones(build):
    # Paths are case-insensitive on Windows: the old program's `dist\siftool` would BE `dist\Siftool`.
    assert build.WORK_DIR.parent == build.BUILD and build.WORK_DIR.name != "siftool"
    assert build.PROGRAM_DIR == build.DIST / "Siftool"


def test_the_legacy_build_writes_to_dist_legacy():
    text = (ROOT / "build.bat").read_text(encoding="utf-8")

    assert r"--distpath dist\legacy" in text
    assert r"dist\legacy\siftool\siftool.exe" in text
    assert not re.search(r"dist\\siftool\\", text, re.IGNORECASE)  # never the new program's folder


# -- the license and the notices the program ships with ---------------------------------------------------


def test_the_root_license_is_the_gplv3_the_program_ships(build):
    root = (ROOT / "LICENSE").read_text(encoding="utf-8")

    assert root.startswith("                    GNU GENERAL PUBLIC LICENSE\n                       Version 3, 29 June 2007")
    assert root.splitlines() == build.LICENSE.read_text(encoding="utf-8").splitlines()  # one text, two places


def test_the_notices_list_what_the_program_carries(build):
    text = build.NOTICES.read_text(encoding="utf-8")
    # One entry per component: its name at the start of a line, everything indented under it.
    entries = {entry.split("\n", 1)[0]: entry for entry in re.split(r"\n(?=\S)", text)}

    def entry(name: str) -> str:
        (found,) = [body for heading, body in entries.items() if heading.startswith(name)]
        return found

    qt = entry("PySide6 and Qt")
    assert "LGPLv3" in qt and "https://download.qt.io/" in qt  # dynamically linked: the license and the Qt source
    assert "GPLv3" in entry("PySide6-Fluent-Widgets")
    assert "LGPLv3" in entry("PySideSix-Frameless-Window")
    assert "BSD" in entry("darkdetect") and "Alberto Sottile" in entry("darkdetect")
    bootloader = entry("PyInstaller")
    assert "bootloader" in bootloader.lower() and "Bootloader Exception" in bootloader and "GNU General Public License" in bootloader
    assert "GPLv3" in text.split("\n", 6)[-1]  # and what the program itself is, before the list


def test_the_license_and_the_notices_go_beside_the_exe(build, tmp_path):
    program = tmp_path / "Siftool"
    program.mkdir()

    build.add_legal_files(program)

    for name, source in (("LICENSE.txt", build.LICENSE), ("THIRD-PARTY-NOTICES.txt", build.NOTICES)):
        data = (program / name).read_bytes()
        assert data.count(b"\r\n") == data.count(b"\n") > 10  # Windows line ends, for Notepad and the license page
        assert data.decode("utf-8").splitlines() == source.read_text(encoding="utf-8").splitlines()


def test_the_portable_zip_carries_them_next_to_the_exe(build, tmp_path):
    program = tmp_path / "Siftool"
    program.mkdir()
    (program / "Siftool.exe").write_bytes(b"MZ")
    build.add_legal_files(program)

    target = build.make_portable_zip(program, tmp_path / "Siftool-2.0.0-beta.1-portable.zip")

    with zipfile.ZipFile(target) as archive:
        assert {"Siftool/Siftool.exe", "Siftool/LICENSE.txt", "Siftool/THIRD-PARTY-NOTICES.txt"} <= set(archive.namelist())


# -- the docs: what people must know before they run it -----------------------------------------------------


def _section(text: str, heading: str) -> str:
    """The README section that starts at `heading`, up to the next "## " one."""
    return re.split(r"^## ", text.split(heading, 1)[1], maxsplit=1, flags=re.MULTILINE)[0]


def test_readme_says_gplv3_and_why():
    text = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "license-MIT" not in text and "MIT License" not in text
    assert "license-GPLv3" in text and "(LICENSE)" in text  # the badge links to a file that exists now
    license_section = _section(text, "## ✦ License")
    assert "GPLv3" in license_section and "QFluentWidgets" in license_section
    assert "THIRD-PARTY-NOTICES.txt" in license_section


def test_readme_install_section_warns_about_the_limits():
    install = _section((ROOT / "README.md").read_text(encoding="utf-8"), "## ✦ Instalar (Windows)")

    assert "200 caracteres" in install and "OneDrive" in install  # the portable folder's path must be short
    assert "15 arquivos" in install  # Windows hides "Limpar com Siftool" above that
    assert "Controle inteligente de aplicativos" in install and "Smart App Control" in install
    assert "assinatura" in install  # ... because nothing is signed yet


def test_readme_build_section_builds_the_new_program_with_build_py():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    install = _section(text, "## ✦ Instalar (Windows)")
    legacy = _section(text, "## ✦ Standalone Executable Build")

    assert r"packaging\build.py" in install and '".[gui,build]"' in install
    assert "build.bat" in install and "antigo" in install  # build.bat is the OLD program's
    assert r"dist\legacy" in legacy and "OLD" in legacy and r"packaging\build.py" in legacy


def test_portable_readme_warns_about_long_paths_and_smart_app_control():
    text = " ".join((ROOT / "packaging" / "LEIA-ME.txt").read_text(encoding="utf-8").split())  # it is hard-wrapped

    assert "200 caracteres" in text and "OneDrive" in text
    assert "Controle inteligente de aplicativos" in text and "Smart App Control" in text
    assert "THIRD-PARTY-NOTICES.txt" in text and "GPLv3" in text
