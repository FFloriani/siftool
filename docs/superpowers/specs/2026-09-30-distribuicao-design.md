# Siftool v2 — Distribution: installer and portable version (design and plan)

- **Date:** 2026-09-30
- **Status:** approved by the user ("vamos investir tempo em construir um .exe do app então instalável etc … e uma versão portable"). The user authorized installing PyInstaller in the `.venv` and Inno Setup 6.7.3 for their user account, and both are already installed. The details below are the controller's decisions, taken under the user's standing delegation.
- **Subproject:** 3 of 4 of Siftool v2 (distribution).

## 1. Goal

A person without Python should be able to use the new program in one of two ways:
- **Installer:** `Siftool-2.0.0-beta.1-setup.exe`.
- **Portable:** `Siftool-2.0.0-beta.1-portable.zip`. Unzip it and run; nothing is installed.

Both use the same program folder built by PyInstaller.

## 2. Decisions

| Topic | Decision |
|---|---|
| Version | `2.0.0b1` (PEP 440) everywhere Python reads the version; `2.0.0-beta.1` in file names and in the installer; Windows version resource FileVersion `2.0.0.1` with ProductVersion `2.0.0 beta 1`. One source of truth: `siftool.__version__`. `pyproject.toml` switches to a dynamic version read from it, or both are updated and a test pins them equal. |
| Freezer | PyInstaller 6.22.3, **onedir** (fast start; the portable ZIP is this folder), windowed (no console). Output folder `dist/Siftool/` with `Siftool.exe`. Icon `siftool.ico` (already holds 16–256 px). |
| Size | Exclude Qt modules the program never uses (WebEngine, 3D, Multimedia, Charts, DataVisualization, Pdf, Bluetooth, Nfc, Positioning, Sensors, SerialPort, Quick, Qml, Designer, Help, …). The frozen self-test (§4) must still pass. Report the final folder and installer sizes. |
| Installer | Inno Setup 6.7.3 (`C:\Users\Floriani\AppData\Local\Programs\Inno Setup 6\ISCC.exe`; the build script also accepts an `ISCC` environment variable). |
| Installer: scope and location | Per-user, **no administrator prompt** (`PrivilegesRequired=lowest`), installed to `%LOCALAPPDATA%\Programs\Siftool`. |
| Installer: language | Brazilian Portuguese, using Inno's `BrazilianPortuguese.isl`. |
| Installer: shortcuts | Start menu shortcut; optional desktop icon, unchecked by default. |
| Installer: context menu | Optional task, checked by default: "Adicionar 'Limpar com Siftool' ao clique direito". It creates HKCU `Software\Classes\SystemFileAssociations\<ext>\shell\Siftool` for `.jpg .jpeg .png .webp`, plus `Software\Classes\Directory\shell\Siftool` ("Limpar fotos com Siftool") for folders. The command is `"{app}\Siftool.exe" "%1"` and the icon is the exe. The uninstaller removes all of these keys. |
| Installer: publisher and identity | Publisher Felipe Floriani; URL https://github.com/FFloriani/siftool; a fixed `AppId` GUID. |
| Installer: other | License text GPLv3 (QFluentWidgets). "Abrir o Siftool" on the finish page. Uninstall keeps the user's settings (`%APPDATA%\Siftool`), as is standard. |
| Portable | The same `Siftool\` folder zipped, plus a marker file `Siftool.portable` next to the exe and a short Portuguese `LEIA-ME.txt`. When the marker exists, settings live in `<exe folder>\dados\config.json` instead of `%APPDATA%`. |
| Opening files from Windows | `Siftool.exe <paths...>` adds those paths to the list on startup; this covers the context menu and "Abrir com". |
| Single window | A second launch sends its paths to the window already open (`QLocalServer` named per user) and exits. The open window adds them and comes to the front. A stale server name left after a crash is removed and recreated. |
| Window icon | `setWindowIcon` with `siftool.ico`, bundled as a data file. |
| Windowed mode | In the frozen exe `sys.stdout` is None. `--version` writes the version to a file only when given `--version-file <path>`; otherwise it only exits 0. Nothing may crash because stdout is missing. |
| Hidden self-test | `Siftool.exe --self-test <result.json>` builds the main window offscreen (`QT_QPA_PLATFORM=offscreen` set before `QApplication`), processes events, cleans one tiny generated PNG with the engine in a temp folder, writes `{"ok": true, "version": ..., "portable": ..., "config": ...}` and exits 0. On any exception it writes `{"ok": false, "error": ...}` and exits 1. This is how the build proves Qt plugins, QFluentWidgets resources and the engine are all inside the bundle. |
| Build | `packaging/build.py`, run as `.venv/Scripts/python.exe packaging/build.py`. Steps: clean `build/` and `dist/`; PyInstaller with `packaging/siftool.spec`; self-test of `dist/Siftool/Siftool.exe`; portable ZIP, then extract it to a temp folder and self-test there, checking that `config` points inside the portable folder; ISCC with `packaging/siftool.iss`, passing the version; print the artifact paths and sizes. |
| Legacy files | The legacy `siftool.py`, `build.bat` and `siftool.spec` at the root are not touched; retiring them is a later decision. |
| Code signing | Out of scope (no certificate). The README explains Windows' "O Windows protegeu o computador" → "Mais informações → Executar assim mesmo". |
| Publishing | Building is local. Creating a GitHub Release is a separate step and needs the user's OK. |

## 3. Plan: one implementer, two commits

**Commit 1 — program changes:** `feat(gui): version 2.0.0b1, window icon, open files from the command line, single window, portable settings`.
- **Version:** single source, with a test that pyproject and `siftool.__version__` agree (or that pyproject is dynamic).
- **Window icon.**
- **Command-line paths:** `app.main(argv)` accepts paths and hands them to the clean page after the window shows.
- **Single instance:**
  - a small `siftool_gui/single_instance.py` with a per-user server name and messages of UTF-8 JSON `{"paths": [...]}`;
  - the first instance raises and activates its window;
  - tests with two in-process sockets: send, receive, stale server name, non-ASCII paths.
- **Portable settings:** `settings.default_config_path()` picks `<exe dir>\dados\config.json` when frozen and the `Siftool.portable` marker exists. Tests monkeypatch `sys.frozen` / `sys.executable`.
- **`--version-file` and `--self-test`,** with tests that run them in a subprocess against the source tree using `-P`.
- **Rules:** TDD; the full suite stays green (768+).

**Commit 2 — packaging:** `build(dist): PyInstaller onedir, Inno Setup installer and portable ZIP`.
- **New files:** `packaging/siftool.spec`, `packaging/version_info.txt` (or generated by the build), `packaging/siftool.iss`, `packaging/build.py`, `packaging/LEIA-ME.txt`.
- **README:** a Portuguese "Instalar" section covering the installer, the portable ZIP and the SmartScreen note.
- **Verification**, all recorded in the report:
  1. Run the full build.
  2. Self-test the built folder and the extracted portable folder.
  3. Silent install to a temp folder: `setup.exe /VERYSILENT /SUPPRESSMSGBOXES /CURRENTUSER /DIR="<tmp>" /TASKS="contextmenu"`. Self-test the installed exe. Check the HKCU context-menu keys exist and point at it.
  4. Silent uninstall with `unins000.exe /VERYSILENT`. Check that the folder and the context-menu keys are gone.
  5. Afterwards, no Siftool keys and no leftover install may remain on the machine.
- **Constraint:** artifacts stay in `dist/`, which is git-ignored; nothing built is committed.

## 4. Success criteria

1. `.venv/Scripts/python.exe packaging/build.py` produces `dist/Siftool-2.0.0-beta.1-setup.exe` and `dist/Siftool-2.0.0-beta.1-portable.zip` from a clean checkout, with no manual step.
2. The frozen self-test passes for the built folder, the extracted portable folder and the silently installed copy.
3. The portable copy writes its settings inside its own folder.
4. Install and uninstall leave nothing behind (files and HKCU keys).
5. The full test suite still passes.
