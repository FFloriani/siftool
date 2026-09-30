import importlib.metadata
import tomllib
from pathlib import Path

import pytest

PYPROJECT = Path(__file__).parents[1] / "pyproject.toml"


def test_package_imports_with_version():
    import siftool
    assert siftool.__version__ == "2.0.0.dev0"


def _gui_extra() -> list[str]:
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]["optional-dependencies"]["gui"]


def test_gui_extra_is_bounded_to_the_versions_the_code_relies_on():
    # Final review: the interface leans on QFluentWidgets 1.11 internals (the info bar's text
    # layout, the segmented widget's items, ...) and on PySide6 6.10+, so a looser lower bound
    # was a promise nobody tested, and a missing upper bound would let a major release break it.
    assert _gui_extra() == ["PySide6>=6.10,<7", "PySide6-Fluent-Widgets>=1.11,<2"]


@pytest.mark.parametrize("requirement", _gui_extra())
def test_installed_gui_packages_satisfy_the_extra(requirement):
    from packaging.requirements import Requirement

    parsed = Requirement(requirement)
    try:
        installed = importlib.metadata.version(parsed.name)
    except importlib.metadata.PackageNotFoundError:
        pytest.skip(f"{parsed.name} is not installed (the `gui` extra is optional)")
    assert parsed.specifier.contains(installed), f"{parsed.name} {installed} is outside {parsed.specifier}"
