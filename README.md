# ◈ Siftool

[![Python Version](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12%20%7C%203.13-blue)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-Windows-lightgrey)](https://www.microsoft.com/windows)

**Siftool** is a premium, offline, lossless image metadata cleaner designed for maximum privacy and zero quality degradation. It strips EXIF, GPS coordinates, ICC profiles, XMP, IPTC, and **Content Credentials (C2PA)** from images without re-encoding the actual pixel data.

Every file processed by Siftool is automatically re-scanned, proving that it is mathematically clean through our built-in **Verified Clean Seal**.

---

## ✦ Key Features

- ⚡ **Lossless Stripping Engine:** Never degrades image quality. JPEGs are parsed segment-by-segment at the byte level, and PNGs are rewritten chunk-by-chunk, keeping pixel data completely untouched.
- 🛡️ **Content Credentials (C2PA) Detection:** The first open-source tool to detect, explain, and strip cryptographically signed provenance data (embedded by Google Pixel 10, Adobe Photoshop, DALL-E, etc.) that can track your identity, edits, and geolocation.
- ◈ **Sleek Dark Mode GUI:** A modern, premium violet-accented Tkinter dashboard with drag-and-drop support, multi-threaded batch processing, and a real-time **Metadata Inspector**.
- 💻 **CLI Dual-Mode:** Full command-line interface with subcommands (`scan`, `clean`, `verify`) and `--json` outputs, making it perfect for custom automation pipelines and developers.
- 🔒 **100% Offline & Private:** Zero network requests. Your photos never leave your machine.

---

## ✦ How It Works (Lossless vs. Re-encoding)

Unlike standard image editors that decode an image and re-encode it back (which introduces compression artifacts, changes hashes, and shifts colors), Siftool manipulates the container at the byte-level:

```
[Original JPEG]  ───>  [Segment Parser]  ───>  [Stripped JPEG]
  ├── SOI  (Keep)        ├── APP1  EXIF (Strip)   ├── SOI
  ├── APP1 EXIF          ├── APP2  ICC  (Strip)   ├── SOF0 (Unchanged Pixels)
  ├── SOF0 Pixels        ├── SOF0  Keep           └── EOI
  └── EOI  (Keep)        └── EOI   Keep
```

- **JPEG:** Strips `APP0` (JFIF headers), `APP1` (EXIF/GPS/XMP), `APP2` (ICC color profiles), `APP11` (C2PA/JUMBF), and `APP13` (IPTC) byte-by-byte.
- **PNG:** Parses chunk structures, dropping metadata chunks (`tEXt`, `iTXt`, `zTXt`, `eXIf`, `caBX`, `cPRV`, `iCCP`) while preserving core rendering chunks (`IHDR`, `IDAT`, `PLTE`, `tRNS`, `pHYs`, `IEND`).
- **WEBP & Others:** Decodes and saves without metadata, preserving WebP lossless mode if the source was lossless.

---

## ✦ CLI Usage

Siftool automatically runs in **CLI Mode** when launched with arguments.

### 1. Scan Metadata
Examine all metadata entries inside one or more images:
```bash
python siftool.py scan photo.jpg another.png
```

### 2. Clean Images
Strip metadata losslessly and output files with the `_clean` suffix in the same directory:
```bash
# Clean specific files
python siftool.py clean photo.jpg photo2.png

# Clean all supported files in a folder
python siftool.py clean --folder ./my-images
```

### 3. Verify Clean State
Check if images are truly clean of all tracking and camera metadata:
```bash
python siftool.py verify photo_clean.jpg
```

### 4. Integration & JSON Mode
Add the `--json` flag to any subcommand to get machine-readable outputs for your scripts and pipelines:
```bash
python siftool.py clean photo.jpg --json
```
**Output Example:**
```json
{
  "summary": {
    "total": 1,
    "cleaned": 1,
    "verified_clean": true,
    "errors": 0
  },
  "results": [
    {
      "file": "photo.jpg",
      "status": "ok",
      "cleaned_to": "photo_clean.jpg",
      "removed": [
        "EXIF data (camera, GPS, settings)",
        "C2PA / Content Credentials (APP11)"
      ],
      "verified_clean": true,
      "residuals": []
    }
  ]
}
```

---

## ✦ Installation & Development

### Setup
1. Clone the repository.
2. Run `iniciar.bat` (Windows) to automatically create a virtual environment `.venv` and install the required dependencies:
   - `Pillow` (Image scanning)
   - `piexif` (EXIF parser)
   - `tkinterdnd2` (Native drag-and-drop)
   - `pyinstaller` (Executable compiler)

### Running Siftool GUI
Simply launch `siftool.py` without any arguments to open the Tkinter GUI:
```bash
python siftool.py
```

---

## ✦ Compiling Standalone Executable (.exe)

You can package Siftool into a standalone, portable Windows application directory with zero Python requirements for end-users.

Simply run the build script:
```cmd
build.bat
```
This triggers PyInstaller to compile Siftool with the custom `siftool.spec`, incorporating native `tkinterdnd2/tkdnd` binaries and bundling our premium `siftool.ico` branding. 

Your distribution is generated at:
`dist\siftool\siftool.exe`

---

## ✦ License

This project is licensed under the MIT License.
