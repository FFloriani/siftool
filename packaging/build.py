"""Build Siftool for Windows: the program folder, the portable ZIP and the installer (distribution spec 2).

    .venv/Scripts/python.exe packaging/build.py

From a clean checkout, with no manual step (spec 4, criterion 1); it needs
`pip install -e ".[gui,build]"` and Inno Setup 6. Each step is checked before
the next one starts:

1. What an earlier run of this script left is deleted -- `build/pyinstaller`,
   `build/version_info.txt`, `dist/Siftool` and the ZIP and installer in
   `dist/` -- and nothing else: `dist/legacy`, where `build.bat` puts the OLD
   Tkinter program, and whatever else another tool made there stay.
2. PyInstaller freezes the program (`packaging/siftool.spec`) into
   `dist/Siftool/` -- `Siftool.exe`, windowed -- with a version resource
   written here from `siftool.__version__`; the license and the notices about
   the third-party components (GPLv3, LGPLv3...) go beside the exe.
3. The frozen program checks itself (`Siftool.exe --self-test`, off screen,
   with only Windows' own folders on PATH) and says its version.
4. The portable ZIP, `dist/Siftool-<version>-portable.zip`: that folder plus
   `Siftool.portable` (the settings stay in the folder) and `LEIA-ME.txt`.
   It is extracted to a temporary folder and checks itself there: portable,
   with its settings written inside it.
5. Inno Setup compiles `packaging/siftool.iss` into
   `dist/Siftool-<version>-setup.exe`. `ISCC.exe` comes from the `ISCC`
   environment variable, else from where Inno Setup 6 installs itself.
6. The artifacts and their sizes are printed.

`<version>` is `siftool.__version__` as people read it: "2.0.0b1" becomes
"2.0.0-beta.1" (`release_name`).
"""

from __future__ import annotations

import datetime
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

PACKAGING = Path(__file__).resolve().parent
ROOT = PACKAGING.parent
SRC = ROOT / "src"
BUILD = ROOT / "build"
DIST = ROOT / "dist"
# What this script makes in build/ -- the legacy build.bat's own work folder there is build/siftool, and its
# output dist/legacy/siftool (on Windows, paths ignore case: its old dist/siftool WAS dist/Siftool).
WORK_DIR = BUILD / "pyinstaller"
VERSION_FILE = BUILD / "version_info.txt"

APP_NAME = "Siftool"
EXE_NAME = "Siftool.exe"
PROGRAM_DIR = DIST / APP_NAME
SPEC = PACKAGING / "siftool.spec"
INSTALLER_SCRIPT = PACKAGING / "siftool.iss"
LICENSE = PACKAGING / "LICENSE.txt"
NOTICES = PACKAGING / "THIRD-PARTY-NOTICES.txt"
README_PORTABLE = PACKAGING / "LEIA-ME.txt"
ICON = SRC / "siftool_gui" / "assets" / "siftool.ico"
PUBLISHER = "Felipe Floriani"

PORTABLE_MARKER = "Siftool.portable"  # = siftool_gui.settings.PORTABLE_MARKER (tests/test_packaging.py checks)
PORTABLE_DATA_FOLDER = "dados"  # = siftool_gui.settings.PORTABLE_DATA_FOLDER
MARKER_TEXT = (
    "Este arquivo faz o Siftool guardar as configurações na pasta dados, ao lado do Siftool.exe.\n"
    "Sem ele, elas vão para a pasta do usuário no Windows.\n"
)
SELF_TEST_TIMEOUT_S = 300  # a cold start of the frozen program, a window and a clean

_VERSION = re.compile(r"(\d+)\.(\d+)\.(\d+)(?:(a|b|rc)(\d+))?")
_PRE_RELEASES = {"a": "alpha", "b": "beta", "rc": "rc"}


class BuildError(RuntimeError):
    """A step of the build failed; the message says which, and why."""


# -- the version, in each place it goes ------------------------------------------------------


def read_version() -> str:
    """`siftool.__version__`, read from this checkout's `src/siftool/__init__.py` -- the one source of truth.

    Read, not imported: never another installed copy, nor the legacy
    siftool.py at the root. The frozen program then has to say the same
    version (step 3).
    """
    text = (SRC / "siftool" / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'^__version__ = "([^"]+)"', text, re.MULTILINE)
    if match is None:
        raise BuildError("src/siftool/__init__.py has no __version__")
    return match.group(1)


def _parts(version: str) -> tuple[str, str | None, int]:
    """`("2.0.0", "b", 1)` for "2.0.0b1": the forms Siftool uses, X.Y.Z optionally followed by aN, bN or rcN."""
    match = _VERSION.fullmatch(version)
    if match is None:
        raise ValueError(f"version {version!r} is not X.Y.Z, optionally followed by aN, bN or rcN")
    major, minor, micro, kind, number = match.groups()
    return f"{int(major)}.{int(minor)}.{int(micro)}", kind, int(number or 0)


def release_name(version: str) -> str:
    """The version in file names and in the installer: "2.0.0b1" -> "2.0.0-beta.1"."""
    base, kind, number = _parts(version)
    return f"{base}-{_PRE_RELEASES[kind]}.{number}" if kind else base


def product_version(version: str) -> str:
    """The version for people, in the version resources: "2.0.0b1" -> "2.0.0 beta 1"."""
    base, kind, number = _parts(version)
    return f"{base} {_PRE_RELEASES[kind]} {number}" if kind else base


def file_version(version: str) -> tuple[int, int, int, int]:
    """Windows' four-number FileVersion: "2.0.0b1" -> (2, 0, 0, 1).

    The fourth number is the pre-release's, 0 for a final release. Windows
    only shows it: the installer replaces the program's files whatever
    their versions (`ignoreversion` in siftool.iss).
    """
    base, _kind, number = _parts(version)
    major, minor, micro = (int(part) for part in base.split("."))
    return major, minor, micro, number


def version_info(version: str) -> str:
    """PyInstaller's version resource for Siftool.exe: FileVersion 2.0.0.1, ProductVersion "2.0.0 beta 1"."""
    numbers = file_version(version)
    strings = {
        "CompanyName": PUBLISHER,
        "FileDescription": APP_NAME,  # the name Task Manager shows
        "FileVersion": ".".join(str(number) for number in numbers),
        "InternalName": APP_NAME,
        "LegalCopyright": f"© {datetime.date.today().year} {PUBLISHER}. Licença GPLv3.",
        "OriginalFilename": EXE_NAME,
        "ProductName": APP_NAME,
        "ProductVersion": product_version(version),
    }
    table = ",\n".join(f"        StringStruct({key!r}, {value!r})" for key, value in strings.items())
    flags = "0x2" if _parts(version)[1] else "0x0"  # VS_FF_PRERELEASE
    return f"""# UTF-8
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={numbers},
    prodvers={numbers},
    mask=0x3F,
    flags={flags},
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0),
  ),
  kids=[
    StringFileInfo([
      StringTable('041604B0', [
{table},
      ]),
    ]),
    VarFileInfo([VarStruct('Translation', [0x0416, 0x04B0])]),
  ],
)
"""


# -- the steps ----------------------------------------------------------------------------------


def _run(command: list[str], **kwargs) -> None:
    print("  $ " + subprocess.list2cmdline(command), flush=True)
    completed = subprocess.run(command, cwd=ROOT, **kwargs)
    if completed.returncode != 0:
        raise BuildError(f"{Path(command[0]).name} failed with exit code {completed.returncode}")


def _remove(path: Path) -> None:
    """Delete a file or a folder -- read-only files included -- if it is there."""
    def writable_again(function, target, _error) -> None:  # a read-only file: make it writable, try again
        os.chmod(target, stat.S_IWRITE)
        function(target)

    if path.is_dir() and not path.is_symlink():
        if sys.version_info >= (3, 12):
            shutil.rmtree(path, onexc=writable_again)
        else:
            shutil.rmtree(path, onerror=writable_again)
    elif path.exists() or path.is_symlink():
        try:
            path.unlink()
        except PermissionError:  # a read-only file: make it writable, try again
            os.chmod(path, stat.S_IWRITE)
            path.unlink()


def previous_outputs(build: Path = BUILD, dist: Path = DIST) -> list[Path]:
    """What an earlier run of this script left in `build/` and `dist/` -- its own outputs, and only those.

    In `build/`: PyInstaller's work folder and the version resource. In
    `dist/`: the program folder, and the ZIP and the installer of any version
    (an older release's are this script's too). Never `dist/legacy` -- where
    `build.bat` puts the old Tkinter program -- nor `build/siftool`, that
    program's work folder, nor anything else another tool or a person left in
    either folder.
    """
    found = [build / WORK_DIR.name, build / VERSION_FILE.name, dist / APP_NAME]
    found += sorted(dist.glob(f"{APP_NAME}-*-portable.zip")) + sorted(dist.glob(f"{APP_NAME}-*-setup.exe"))
    return [path for path in found if path.exists() or path.is_symlink()]


def remove_previous_outputs(build: Path = BUILD, dist: Path = DIST) -> list[Path]:
    """Step 1: delete `previous_outputs`; the paths removed."""
    removed = previous_outputs(build, dist)
    for path in removed:
        _remove(path)
    return removed


def _windows_text(path: Path) -> bytes:
    """A text file with Windows line ends, as Notepad and the installer's license page expect."""
    return path.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-8")


def add_legal_files(program: Path) -> None:
    """The GPLv3 license and the third-party notices go beside the exe, under their own names.

    Not through the spec's `datas`: PyInstaller 6 puts those in `_internal/`, and these are for the person who
    opens the folder -- the installer and the portable ZIP both carry the whole program folder.
    """
    for source in (LICENSE, NOTICES):
        (program / source.name).write_bytes(_windows_text(source))


def freeze(version: str) -> None:
    """Step 2: PyInstaller makes dist/Siftool/ (see siftool.spec); the license and the notices go beside the exe."""
    VERSION_FILE.parent.mkdir(parents=True, exist_ok=True)
    VERSION_FILE.write_text(version_info(version), encoding="utf-8")
    env = dict(os.environ, SIFTOOL_VERSION_INFO=str(VERSION_FILE))
    # A Python from conda keeps the DLLs of its own extension modules (ffi.dll for _ctypes, liblzma.dll,
    # libbz2.dll, libmpdec-4.dll, zstd.dll) in <base prefix>\Library\bin, which PyInstaller only searches
    # when it is on PATH. Last, so it never shadows a DLL found anywhere else.
    conda_dlls = Path(sys.base_prefix) / "Library" / "bin"
    if conda_dlls.is_dir():
        env["PATH"] = os.pathsep.join([env.get("PATH", ""), str(conda_dlls)])
    # -P: the repository root never goes on the module path, where the legacy siftool.py would shadow the engine.
    _run(
        [sys.executable, "-P", "-m", "PyInstaller", "--noconfirm", "--log-level", "WARN",
         "--distpath", str(DIST), "--workpath", str(WORK_DIR), str(SPEC)],
        env=env,
    )
    if not (PROGRAM_DIR / EXE_NAME).is_file():
        raise BuildError(f"PyInstaller did not make {PROGRAM_DIR / EXE_NAME}")
    add_legal_files(PROGRAM_DIR)


def _clean_environment() -> dict[str, str]:
    """A clean Windows' environment for the frozen program: only Windows' own folders on PATH, no Python or Qt
    variables -- so it can only use what its own folder holds."""
    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    ignored = ("PYTHON", "QT_", "QML", "CONDA", "VIRTUAL_ENV", "PYINSTALLER")
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith(ignored)}
    env["PATH"] = os.pathsep.join([rf"{system_root}\System32", system_root, rf"{system_root}\System32\Wbem"])
    return env


def self_test(exe: Path, version: str) -> dict:
    """`exe --self-test` and `exe --version --version-file`, in a clean environment; the self-test's report."""
    env = _clean_environment()
    with tempfile.TemporaryDirectory(prefix="siftool-build-") as folder:
        result = Path(folder) / "self-test.json"
        said = Path(folder) / "version.txt"
        code = subprocess.run([str(exe), "--self-test", str(result)], env=env, timeout=SELF_TEST_TIMEOUT_S).returncode
        report = json.loads(result.read_text(encoding="utf-8")) if result.is_file() else {}
        if code != 0 or report.get("ok") is not True:
            details = json.dumps(report, ensure_ascii=False, indent=2) if report else "no report"
            raise BuildError(f"the self-test of {exe} failed (exit code {code}): {details}")
        code = subprocess.run([str(exe), "--version", "--version-file", str(said)], env=env, timeout=60).returncode
        line = said.read_text(encoding="utf-8").strip() if said.is_file() else ""
    if code != 0 or line != f"siftool-gui {version}" or report.get("version") != version:
        raise BuildError(f"{exe} says version {line!r} / {report.get('version')!r}, not {version!r}")
    return report


def check_program(exe: Path, version: str) -> dict:
    """Step 3: the frozen program checks itself; it is not the portable copy."""
    report = self_test(exe, version)
    if report.get("portable") is not False:
        raise BuildError(f"{exe} takes itself for the portable copy: {report}")
    print(f"  self-test ok: version {report['version']}, settings would go to {report['config']}")
    return report


def make_portable_zip(program: Path, target: Path) -> Path:
    """Step 4: `program`'s folder zipped as the one folder "Siftool", with the portable marker and LEIA-ME.txt."""
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(program.rglob("*")):
            if path.is_file():
                archive.write(path, f"{APP_NAME}/{path.relative_to(program).as_posix()}")
        archive.writestr(f"{APP_NAME}/{PORTABLE_MARKER}", MARKER_TEXT.replace("\n", "\r\n").encode("utf-8"))
        archive.writestr(f"{APP_NAME}/LEIA-ME.txt", _windows_text(README_PORTABLE))
    return target


def check_portable_zip(target: Path, version: str) -> dict:
    """Step 4's check: extracted to a temporary folder, the copy is portable and writes its settings inside itself."""
    with tempfile.TemporaryDirectory(prefix="siftool-portable-") as folder:
        with zipfile.ZipFile(target) as archive:
            archive.extractall(folder)
        copy = Path(folder) / APP_NAME
        for legal in (LICENSE, NOTICES):  # next to the exe, in the ZIP as in the installer
            if not (copy / legal.name).is_file():
                raise BuildError(f"the portable ZIP does not carry {legal.name} next to {EXE_NAME}")
        report = self_test(copy / EXE_NAME, version)
        settings = copy / PORTABLE_DATA_FOLDER / "config.json"
        if report.get("portable") is not True or not settings.is_file() \
                or not os.path.samefile(report["config"], settings):
            raise BuildError(f"the extracted portable copy does not keep its settings in {settings}: {report}")
        print(f"  portable self-test ok: settings written to {report['config']}")
    return report


def find_iscc() -> Path:
    """Inno Setup 6's compiler: the `ISCC` environment variable, else where Inno Setup 6 installs itself."""
    candidates = [
        os.environ.get("ISCC"),
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Inno Setup 6" / "ISCC.exe",
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Inno Setup 6" / "ISCC.exe",
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Inno Setup 6" / "ISCC.exe",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    raise BuildError("Inno Setup 6's ISCC.exe was not found: install Inno Setup 6, or set ISCC to its path")


def make_installer(version: str) -> Path:
    """Step 5: Inno Setup compiles siftool.iss (per user, PT-BR) with the version and the folders."""
    release = release_name(version)
    _run([
        str(find_iscc()), "/Q",  # quiet: only errors
        f"/DAppVersion={release}",
        f"/DVersionInfo={'.'.join(str(number) for number in file_version(version))}",
        f"/DProductTextVersion={product_version(version)}",
        f"/DSourceDir={PROGRAM_DIR}",
        f"/DOutputDir={DIST}",
        f"/DIconFile={ICON}",
        f"/DLicenseFile={PROGRAM_DIR / 'LICENSE.txt'}",
        str(INSTALLER_SCRIPT),
    ])
    setup = DIST / f"{APP_NAME}-{release}-setup.exe"
    if not setup.is_file():
        raise BuildError(f"Inno Setup did not make {setup}")
    return setup


def _describe(path: Path) -> str:
    if path.is_dir():
        files = [item for item in path.rglob("*") if item.is_file()]
        return f"{len(files)} files, {sum(item.stat().st_size for item in files) / 2**20:.1f} MiB"
    return f"{path.stat().st_size / 2**20:.1f} MiB"


def main() -> int:
    version = read_version()
    release = release_name(version)
    print(f"Siftool {version} ({release})")
    print("1/6 removing what an earlier build left (build/pyinstaller, dist/Siftool, its ZIP and installer)", flush=True)
    for path in remove_previous_outputs():
        print(f"  removed {path.relative_to(ROOT)}")
    print("2/6 freezing the program (PyInstaller)", flush=True)
    freeze(version)
    print("3/6 self-test of dist/Siftool", flush=True)
    check_program(PROGRAM_DIR / EXE_NAME, version)
    print("4/6 portable ZIP", flush=True)
    portable = make_portable_zip(PROGRAM_DIR, DIST / f"{APP_NAME}-{release}-portable.zip")
    check_portable_zip(portable, version)
    print("5/6 installer (Inno Setup)", flush=True)
    setup = make_installer(version)
    print("6/6 artifacts:")
    for path in (PROGRAM_DIR, setup, portable):
        print(f"  {path}  ({_describe(path)})")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BuildError as error:
        print(f"build failed: {error}", file=sys.stderr)
        raise SystemExit(1) from None
