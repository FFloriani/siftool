<div align="center">

**English** · [Português (Brasil)](README.pt-BR.md)

<img src="./siftool_logo.png" width="96" height="96" alt="Siftool logo">

# Siftool — Remove EXIF, GPS & AI (C2PA) Metadata from Photos

**Free, offline Windows app that strips location, personal data and AI provenance marks from your photos — without losing quality.**<br>
The original is never modified · nothing is re-encoded · a copy is marked *Verified* only after it is re-checked

[![Latest release](https://img.shields.io/github/v/release/FFloriani/siftool?style=flat-square&label=release)](https://github.com/FFloriani/siftool/releases/latest)
[![Downloads](https://img.shields.io/github/downloads/FFloriani/siftool/total?style=flat-square)](https://github.com/FFloriani/siftool/releases)
[![Windows 10 | 11](https://img.shields.io/badge/Windows-10%20%7C%2011-0078D4?style=flat-square&logo=windows)](https://github.com/FFloriani/siftool/releases/latest)
[![License: GPLv3](https://img.shields.io/badge/license-GPLv3-blue?style=flat-square)](LICENSE)
[![Buy Me a Coffee](https://img.shields.io/badge/Buy%20Me%20a%20Coffee-support-ffdd00?style=flat-square&logo=buy-me-a-coffee&logoColor=black)](https://buymeacoffee.com/1b9hbqniv1)

**[⬇ Download the installer](https://github.com/FFloriani/siftool/releases/download/v2.0.0-beta.1/Siftool-2.0.0-beta.1-setup.exe)** (22 MB)
&nbsp;·&nbsp;
**[⬇ Download the portable ZIP](https://github.com/FFloriani/siftool/releases/download/v2.0.0-beta.1/Siftool-2.0.0-beta.1-portable.zip)** (27 MB)
&nbsp;·&nbsp;
[All releases](https://github.com/FFloriani/siftool/releases)

<img src="docs/images/janela-clara.png" width="820" alt="Siftool metadata remover for Windows listing the hidden data found in each photo: GPS location, personal data, date, camera and AI marks">

</div>

> [!NOTE]
> The interface is currently in **Brazilian Portuguese**. An English interface is on the [roadmap](#roadmap). The program is simple to use without reading the labels: drop photos, click the big button.

## Why remove metadata from photos?

Every photo carries hidden data besides the picture itself — the **EXIF**, **XMP** and **IPTC** metadata. A photo shared as a file can reveal:

- **where** it was taken: GPS coordinates, often precise to a few meters;
- **when**: the exact date and time;
- **who** took it and **with what**: your name, the phone or camera model, its serial number;
- the **software** that edited it;
- for AI images, a **C2PA / Content Credentials** manifest — the provenance record that ChatGPT (OpenAI), Adobe Firefly and other tools embed to say the image was AI-generated — plus the generation prompts some tools write into PNG files.

Siftool shows exactly what each photo hides, in plain language, and writes a **clean copy** next to the original.

## Features

- **Lossless metadata removal** — Siftool never decodes or re-compresses the image. It copies the image bytes exactly as they are and removes only the metadata blocks, so pixels come out identical and there is no JPEG quality loss.
- **Removes GPS location, EXIF, XMP, IPTC, comments and hidden thumbnails** in one click, for single photos or whole folders.
- **Removes AI provenance**: C2PA / Content Credentials manifests (such as ChatGPT's) and AI generation parameters stored in PNG text chunks.
- **A "Verified" seal you can trust**: after writing the copy, Siftool re-reads it from scratch and checks that nothing the chosen mode forbids is left. If anything remains, the copy is not saved and the reason is shown.
- **Your original is never touched** — the clean copy is saved as `photo_clean.jpg`; nothing is overwritten.
- **100% offline and private** — no internet, no telemetry, no account. Your photos never leave your computer.
- **Three cleaning modes**: Privacy, Photographer (keeps camera and lens settings) and Maximum.
- **Windows integration**: *Clean with Siftool* in the right-click menu, drag and drop, light and dark themes, installer with no admin rights, or a portable version.
- **Command-line interface** for scripts and automation, with JSON output.
- **Free and open source** (GPLv3).

## Download and install

Requires **Windows 10 or 11 (64-bit)**. No Python or other software needed.

| | |
|---|---|
| **Installer** · [`Siftool-2.0.0-beta.1-setup.exe`](https://github.com/FFloriani/siftool/releases/download/v2.0.0-beta.1/Siftool-2.0.0-beta.1-setup.exe) | Installs for your user only, **no administrator password**. Adds a Start menu shortcut and, optionally, **Limpar com Siftool** (Clean with Siftool) to the right-click menu of photos and folders. Uninstall from Settings → Apps. |
| **Portable** · [`Siftool-2.0.0-beta.1-portable.zip`](https://github.com/FFloriani/siftool/releases/download/v2.0.0-beta.1/Siftool-2.0.0-beta.1-portable.zip) | Nothing is installed. Extract the `Siftool` folder to a **short** path (such as `C:\Siftool` or a USB drive) and open `Siftool.exe`. Settings stay inside the folder. |

> [!NOTE]
> **"Windows protected your PC"**: Siftool is not code-signed yet, so Windows SmartScreen warns on first launch. Click **More info** → **Run anyway**. With Windows 11 *Smart App Control* turned on, unsigned programs may be blocked without that option.

<details>
<summary>Installation details</summary>

- The installer writes to `%LOCALAPPDATA%\Programs\Siftool`; settings live in `%APPDATA%\Siftool` and are kept on uninstall.
- On Windows 11, *Limpar com Siftool* is under **Show more options**. Windows hides it when more than 15 files are selected — for many photos, use the folder entry or drag them into the window.
- The portable folder's full path must be short: with about 200 characters or more (deep OneDrive folders), the program does not start.
- Opening several photos from the right-click menu sends them all to the same window.
- SHA-256 checksums are on the [release page](https://github.com/FFloriani/siftool/releases/tag/v2.0.0-beta.1).

</details>

## How to use

1. **Drop** photos or folders into the window (or use *Adicionar fotos* / *Adicionar pasta*, or the Windows right-click menu).
2. **See** what each photo hides. Click a photo to see what will be **removed** and what will be **kept** in the chosen mode.
3. Pick a **mode** and click **Limpar** (Clean). Each photo gets the **Verificado** (Verified) seal and the clean copy appears next to the original; *Abrir pasta* opens the folder.

<div align="center">
<img src="docs/images/limpeza-concluida.png" width="820" alt="Photos cleaned and verified by Siftool, with the AI (C2PA) mark removed from a ChatGPT image">
</div>

### Cleaning modes

| Mode | Removes | Keeps |
|---|---|---|
| **Privacy** (default) | Location, personal data, dates, device, camera settings, software, text and comments, hidden thumbnails, AI marks and any unknown data | Only what the photo needs to display correctly: orientation and color profile |
| **Photographer** | Same as Privacy, except device and camera settings | Orientation, color, camera, lens, ISO, aperture and shutter speed |
| **Maximum** | Everything, including orientation and color profile | Nothing (the photo may appear rotated or with slightly different colors) |

### Supported formats

| Format | Status |
|---|---|
| JPEG / JPG | ✅ Supported |
| PNG (including animated PNG) | ✅ Supported — including C2PA manifests and AI prompts |
| WEBP (lossy, lossless and animated) | ✅ Supported, without re-encoding |
| HEIC (iPhone photos), MP4 and MOV videos | ⏳ Shown as "not supported yet" — coming in a future release |

<details>
<summary>Dark theme</summary>

Siftool follows the Windows theme (light or dark), or the one you choose in Settings.

<img src="docs/images/janela-escura.png" width="820" alt="Siftool metadata remover in dark theme">
</details>

## How the "Verified" seal works

- **Byte-level parsing.** Siftool understands each format's structure (JPEG segments, PNG chunks, WEBP RIFF chunks) and classifies every block as image data or metadata. Image data is copied byte for byte; metadata is removed or rewritten from scratch with only the allowed fields.
- **Fail-closed verification.** The copy is re-read as an unknown file, scanned for metadata signatures (EXIF, XMP, C2PA, coordinates) and compared with the original to prove the image itself did not change. Any doubt becomes "not cleaned", never "Verified".
- **Validated structures.** Blocks treated as image data must have the size, order and count the format requires, which closes the tricks used to hide data inside valid-looking structures.
- **Tested.** 909 automated tests, including randomly corrupted files, files crafted to fool the verification, and a real ChatGPT image with a C2PA manifest.

Known limit: data hidden **inside the pixels themselves** (steganography or invisible watermarks such as SynthID) is not detected. Siftool removes metadata; it does not alter the image.

## Command line

The same engine runs in the terminal for scripts and automation (Python 3.11+):

```bash
pip install git+https://github.com/FFloriani/siftool.git

siftool scan photo.jpg                        # show what the photo contains, by category
siftool clean photos/ --mode photographer     # clean a whole folder (modes: privacy, photographer, maximum)
siftool clean photo.jpg --out cleaned/        # write the copies to another folder
siftool verify photo_clean.jpg --original photo.jpg
```

Add `--json` to any command for machine-readable output (schema 2). Exit code is `0` when everything is verified, `1` with warnings and `2` on failure.

## FAQ

<details>
<summary><b>How do I remove the GPS location from a photo on Windows?</b></summary>

Open Siftool, drop the photo into the window and click **Limpar** (Clean) in *Privacidade* (Privacy) mode. The clean copy (`photo_clean.jpg`) has no GPS coordinates, date, device or name. You can also right-click the photo in File Explorer and choose *Limpar com Siftool* (Clean with Siftool).
</details>

<details>
<summary><b>How do I remove the AI / C2PA metadata from a ChatGPT image?</b></summary>

Images generated by ChatGPT carry a C2PA (Content Credentials) manifest that identifies them as AI-generated. Siftool detects it — the row shows a *Marca de IA* (AI mark) tag — and removes it in any mode, without re-encoding the image.
</details>

<details>
<summary><b>Does removing metadata reduce image quality?</b></summary>

No. Siftool does not decode or re-compress the image: the image bytes are copied unchanged, so a JPEG keeps exactly the same quality. Re-saving a photo in an image editor, by contrast, re-compresses it every time.
</details>

<details>
<summary><b>Does it change or delete my original photo?</b></summary>

Never. Siftool writes a new file next to the original (`_clean` suffix) and never overwrites anything.
</details>

<details>
<summary><b>Is it safe? Does it upload my photos?</b></summary>

Siftool works fully offline: no internet connection, no telemetry, no account. The source code is public under the GPLv3.
</details>

<details>
<summary><b>Does it remove invisible AI watermarks (like SynthID)?</b></summary>

No. Invisible watermarks live in the pixels, not in the metadata. Siftool removes metadata only and never alters the image.
</details>

## Building from source

```bash
git clone https://github.com/FFloriani/siftool.git
cd siftool
python -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[gui,test]"
.venv\Scripts\python.exe -m pytest           # 909 tests, ~30 s
.venv\Scripts\siftool-gui.exe                # launch the app
```

- `src/siftool/` — the engine: Python standard library only, no dependencies.
- `src/siftool_gui/` — the desktop app, built with PySide6 and [QFluentWidgets](https://qfluentwidgets.com).
- `packaging/` — installer and portable build. Install [Inno Setup 6](https://jrsoftware.org/isinfo.php), run `pip install -e ".[gui,build]"`, then `.venv\Scripts\python.exe packaging\build.py`. Output goes to `dist\`.
- `docs/superpowers/` — design documents and implementation plans.

> [!WARNING]
> Do not run `python -m siftool_gui` from the repository root: the legacy `siftool.py` there shadows the new package.

<details>
<summary>The legacy program (Tkinter)</summary>

Siftool 1.x was a single file, `siftool.py`, with a Tkinter interface. It stays in the repository for now because it still has features the new program lacks (video cleaning with ffmpeg, a metadata editor), and it will be retired once HEIC and video reach the new program. Build it with `build.bat` (output in `dist\legacy\siftool`).
</details>

## Roadmap

- [ ] HEIC (iPhone) photos and MP4 / MOV videos
- [ ] Code signing (no Windows warning on first launch)
- [ ] English interface

## Support

If Siftool helped you, consider supporting its development:

<a href="https://buymeacoffee.com/1b9hbqniv1"><img src="https://img.shields.io/badge/Buy%20Me%20a%20Coffee-support-ffdd00?style=for-the-badge&logo=buy-me-a-coffee&logoColor=black" alt="Buy Me a Coffee"></a>

## License

Siftool is free software under the **GNU General Public License v3 (GPLv3)** — see [LICENSE](LICENSE). The interface uses [QFluentWidgets](https://qfluentwidgets.com) (Community edition), which is GPLv3, so the whole program is too. Licenses of the other bundled components (Qt for Python, PyInstaller and others) are in [packaging/THIRD-PARTY-NOTICES.txt](packaging/THIRD-PARTY-NOTICES.txt).

---

### 🇧🇷 Em português

O **Siftool** é um programa gratuito para Windows que **remove metadados de fotos**: tira a **localização (GPS)**, os dados pessoais, a data, o modelo do celular e as **marcas de IA (C2PA) do ChatGPT**, sem recomprimir a imagem e sem alterar o original. Funciona offline e só marca a cópia como *Verificado* depois de conferir. **[Leia a documentação completa em português →](README.pt-BR.md)**
