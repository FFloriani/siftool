import importlib.metadata
import tomllib
from pathlib import Path

import pytest

PYPROJECT = Path(__file__).parents[1] / "pyproject.toml"


def _pyproject() -> dict:
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))


def test_package_imports_with_version():
    import siftool
    assert siftool.__version__ == "2.0.0b1"


def test_pyproject_reads_the_version_from_the_package():
    # Distribution spec 2: one source of truth for the version, `siftool.__version__`.
    config = _pyproject()
    assert "version" not in config["project"]
    assert "version" in config["project"]["dynamic"]
    assert config["tool"]["setuptools"]["dynamic"]["version"] == {"attr": "siftool.__version__"}


def test_setuptools_resolves_the_version_from_the_package():
    pyprojecttoml = pytest.importorskip("setuptools.config.pyprojecttoml")
    import siftool

    resolved = pyprojecttoml.read_configuration(PYPROJECT, expand=True)
    assert resolved["project"]["version"] == siftool.__version__


def test_window_icon_ships_inside_the_gui_package():
    # Distribution spec 2: the window icon is package data, so a wheel and the frozen program both carry it.
    import siftool_gui

    icon = Path(siftool_gui.__file__).parent / "assets" / "siftool.ico"
    assert icon.read_bytes()[:4] == b"\x00\x00\x01\x00"  # an ICO file
    assert _pyproject()["tool"]["setuptools"]["package-data"]["siftool_gui"] == ["assets/*.ico"]


def _gui_extra() -> list[str]:
    return _pyproject()["project"]["optional-dependencies"]["gui"]


def test_gui_extra_is_bounded_to_the_versions_the_code_relies_on():
    # Final review: the interface leans on QFluentWidgets 1.11 internals (the info bar's text
    # layout, the segmented widget's items, ...) and on PySide6 6.10+, so a looser lower bound
    # was a promise nobody tested, and a missing upper bound would let a major release break it.
    assert _gui_extra() == ["PySide6>=6.10,<7", "PySide6-Fluent-Widgets>=1.11,<2"]


def _check_installed(requirement: str, extra: str) -> None:
    from packaging.requirements import Requirement

    parsed = Requirement(requirement)
    try:
        installed = importlib.metadata.version(parsed.name)
    except importlib.metadata.PackageNotFoundError:
        pytest.skip(f"{parsed.name} is not installed (the `{extra}` extra is optional)")
    assert parsed.specifier.contains(installed), f"{parsed.name} {installed} is outside {parsed.specifier}"


@pytest.mark.parametrize("requirement", _gui_extra())
def test_installed_gui_packages_satisfy_the_extra(requirement):
    _check_installed(requirement, "gui")


def _build_extra() -> list[str]:
    return _pyproject()["project"]["optional-dependencies"].get("build", [])


def test_build_extra_installs_pyinstaller():
    # Release prep: `pip install -e ".[gui,build]"` is all packaging/build.py needs from pip (the Inno Setup
    # compiler, which makes the installer, is a separate install).
    assert _build_extra() == ["pyinstaller>=6.16"]


@pytest.mark.parametrize("requirement", _build_extra())
def test_installed_build_packages_satisfy_the_extra(requirement):
    _check_installed(requirement, "build")
