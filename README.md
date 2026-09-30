# ◈ Siftool

<div align="center">
  <img src="./siftool_logo.png" width="96" height="96" alt="Siftool Logo">
  <h3>Lossless Image Metadata & Content Credentials (C2PA) Cleaner</h3>
  <p>A premium, offline, byte-level privacy utility for Windows</p>

  [![Python Version](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12%20%7C%203.13-blue?style=flat-square)](https://www.python.org/)
  [![License](https://img.shields.io/badge/license-MIT-green?style=flat-square)](LICENSE)
  [![Platform](https://img.shields.io/badge/platform-Windows-lightgrey?style=flat-square)](https://www.microsoft.com/windows)
  [![Buy Me A Coffee](https://img.shields.io/badge/Buy%20Me%20a%20Coffee-Donate-ffdd00?style=flat-square&logo=buy-me-a-coffee&logoColor=black)](https://buymeacoffee.com/1b9hbqniv1)
</div>

---

## ✦ Overview

**Siftool** is a high-performance, 100% offline, lossless metadata cleaner built for Windows. It strips private tracking records—such as EXIF camera specs, precise GPS telemetry, ICC profiles, Photoshop IRB blocks, and **Content Credentials (C2PA)**—without re-encoding the actual pixel data. 

Every image processed is mathematically re-verified post-clean to guarantee zero remaining trackers, sealing the file with Siftool's signature **Verified Clean Seal**.

<div align="center">
  <a href="https://buymeacoffee.com/1b9hbqniv1">
    <img src="https://img.shields.io/badge/Buy%20Me%20a%20Coffee-Donate-ffdd00?style=for-the-badge&logo=buy-me-a-coffee&logoColor=black" alt="Buy Me A Coffee Button">
  </a>
</div>

---

## ✦ Key Features

*   ⚡ **True Lossless Engine:** Never decompresses or re-encodes pixel data. Avoids the generation loss (compression artifacts, color shifting) introduced by standard image editors.
*   🛡️ **Content Credentials (C2PA) Stripping:** Detects and strips cryptographically signed provenance data (embedded by modern devices like Google Pixel 10, Adobe Photoshop, and generative AI pipelines) that track edit history, tools, and creator IDs.
*   ◈ **Premium Dark Mode GUI:** A violet-accented Tkinter interface featuring multi-threaded batch operations, native Drag-and-Drop, and a built-in **Metadata Inspector**.
*   💻 **CLI Dual-Mode:** Full console interface featuring `scan`, `clean`, and `verify` commands with optional `--json` machine-readable outputs for pipeline automation.
*   🔒 **Zero-Trust Offline Operation:** Fully offline. No telemetry, no external API calls. Your photos never leave your computer.

---

## ✦ Core Architecture: Lossless vs. Re-encoding

Standard image cleaners load an image into memory, discard metadata, and write it back. This causes **re-encoding** which alters hashes and degrades image quality. 

Siftool works at the container byte level:

```
[Original JPEG File] ───> [Siftool Byte Parser] ───> [Cleaned JPEG File]
   ├── SOI (Start Marker)    ──> Preserve              ├── SOI
   ├── APP1 (EXIF / GPS)     ──> Strip                 ├── SOF0 (Raw Pixel Stream)
   ├── APP2 (ICC Color)      ──> Strip                 └── EOI
   ├── SOF0 (Uncompressed)   ──> Copy Untouched
   └── EOI (End Marker)      ──> Preserve
```

### Feature Comparison

| Feature | Siftool | Standard Python Scripts | Online Cleaners |
| :--- | :---: | :---: | :---: |
| **Pixel Preservation** | **100% Lossless (Byte-level)** | Re-encoded (Quality Loss) | Re-encoded (Quality Loss) |
| **C2PA Detection** | **Yes** | No | No |
| **Offline Privacy** | **Yes (100% Local)** | Yes | No (Uploads files) |
| **Verification Seal** | **Yes (Auto Post-Scan)** | No | No |
| **Dual GUI / CLI** | **Yes** | CLI Only | Web Page Only |

---

## ✦ Command Line Interface (CLI)

When arguments are passed to the script, Siftool automatically skips GUI launch and operates in CLI mode.

### 1. Scan Metadata
Read and list all metadata fields inside an image without starting a cleaning job:
```bash
python siftool.py scan photo.jpg
```

### 2. Clean Metadata
Remove all metadata from one or multiple files losslessly. Outputs files with a `_clean` suffix:
```bash
# Clean specific files
python siftool.py clean photo1.jpg photo2.png

# Clean an entire folder recursively
python siftool.py clean --folder ./my-gallery
```

### 3. Verify Cleanliness
Perform a deep, case-insensitive check to confirm a file contains no tracking metadata:
```bash
python siftool.py verify photo_clean.jpg
```

### 4. Developer / Pipeline Integration
Append `--json` to any subcommand to format output in structured JSON:
```bash
python siftool.py clean photo.jpg --json
```

**JSON Output Structure:**
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

## ✦ GUI Interface & Inspector

Launch Siftool with no arguments to open the visual client:
```bash
python siftool.py
```

*   **Drag & Drop:** Drag files or folders directly into the central drop zone.
*   **Metadata Inspector:** Click any file in the queue to preview its original metadata fields, C2PA warning indicators, and active seal status.
*   **Threaded Processing:** Run large batch files asynchronously without freezing the interface.

---

## ✦ Instalar (Windows)

O programa novo vem de duas formas. Nenhuma precisa de Python.

*   **Instalador** (`Siftool-<versão>-setup.exe`): instala só para o seu usuário, sem pedir senha de administrador, em `%LOCALAPPDATA%\Programs\Siftool`. Cria o atalho no menu Iniciar e, se você deixar a opção marcada, põe **Limpar com Siftool** no clique direito das fotos JPG, PNG e WEBP e das pastas (no Windows 11, em **Mostrar mais opções**). Para remover, vá em Configurações → Aplicativos → Siftool → Desinstalar. As suas configurações ficam guardadas.
*   **Portátil** (`Siftool-<versão>-portable.zip`): extraia a pasta `Siftool` para onde quiser (um pendrive, por exemplo) e abra o `Siftool.exe`. Nada é instalado no computador: as configurações ficam na pasta `dados`, dentro dela. Para remover, apague a pasta.

Na primeira vez, o Windows pode mostrar **O Windows protegeu o computador**, porque o Siftool ainda não tem assinatura digital. Clique em **Mais informações** e depois em **Executar assim mesmo**.

Para gerar os dois arquivos a partir do código, com o PyInstaller no `.venv` e o [Inno Setup 6](https://jrsoftware.org/isinfo.php) instalados:
```cmd
.venv\Scripts\python.exe packaging\build.py
```
Ele esvazia `build\` e `dist\`, gera a pasta `dist\Siftool`, testa o programa pronto, e deixa o instalador e o ZIP em `dist\`.

---

## ✦ Programa novo (prévia)

O Siftool v2 tem um programa novo, em português, sobre o motor novo. Ele ainda é uma prévia: o `siftool.py` antigo continua existindo até a versão final.

Para instalar e abrir (Python 3.11 ou mais novo), na pasta do repositório:
```bash
pip install -e ".[gui]"
siftool-gui
```

Também dá para abrir com `python -m siftool_gui`, mas **fora da pasta raiz do repositório**: ali o `siftool.py` antigo faz sombra ao pacote novo e o comando abriria a coisa errada.

*   Arraste fotos ou pastas para a janela: a lista mostra o que cada foto esconde antes de limpar.
*   Escolha o modo (Privacidade, Fotógrafo ou Máximo) e clique em **Limpar**. A cópia limpa fica ao lado do original, com `_clean` no nome, e o original nunca é alterado.
*   Limpa fotos JPG, PNG e WEBP, 100% offline. HEIC e vídeos aparecem como "Ainda não suportado".

---

## ✦ Standalone Executable Build (.exe)

You can package Siftool into a standalone directory containing a portable `.exe` binary that does not require Python on the host machine.

Double-click or run:
```cmd
build.bat
```

This runs PyInstaller with the custom configuration in `siftool.spec`, embedding the premium `siftool.ico` branding and extracting the native `tkinterdnd2` binary assets. The compiled program will be located in:

`dist\siftool\siftool.exe`

---

## ✦ Installation & Requirements

1. Clone the repository.
2. Run `iniciar.bat` to create a virtual environment (`.venv`) and install all required modules:
   *   `Pillow` (Image validation)
   *   `piexif` (EXIF parser)
   *   `tkinterdnd2` (Drag and drop)
   *   `pyinstaller` (Executable compilation)

---

## ✦ Donate & Support

If Siftool helped you protect your privacy or streamline your developer workflow, consider supporting its development:

<div align="center">
  <a href="https://buymeacoffee.com/1b9hbqniv1">
    <img src="https://img.shields.io/badge/Buy%20Me%20a%20Coffee-Donate-ffdd00?style=for-the-badge&logo=buy-me-a-coffee&logoColor=black" alt="Buy Me A Coffee">
  </a>
</div>

---

## ✦ License

This project is licensed under the MIT License.
