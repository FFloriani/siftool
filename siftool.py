"""
Siftool v1.0 — Lossless Metadata Cleaner
=========================================
Removes EXIF, GPS, ICC, XMP, IPTC and other metadata from images
WITHOUT re-encoding the pixel data.

Architecture (single file, future-ready for lib/CLI extraction):
  [1] Engine  — pure functions, no GUI dependency
  [2] App     — SiftoolApp (Tkinter GUI with batch + threading)
  [3] Entry   — __main__ block

Lesson applied: NEVER re-encode JPEG. Strip APPn segments byte-by-byte.
                PNG: rewrite file keeping only essential chunks.
"""

import io
import os
import queue
import re
import struct
import threading
from pathlib import Path

import piexif
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from PIL import Image


# ─────────────────────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────

VERSION = "1.0.0"

SUPPORTED_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".gif", ".tiff", ".tif", ".webp"}

# Colour palette — deep dark with violet accent
BG      = "#0f0f17"
SURFACE = "#16161f"
CARD    = "#1c1c2a"
CARD2   = "#21212f"
ACCENT  = "#7c3aed"
ACCENT_H = "#6d28d9"
TEXT    = "#e2e8f0"
TEXT_M  = "#7878a0"
SUCCESS = "#22c55e"
WARNING = "#f59e0b"
DANGER  = "#ef4444"
SEAL    = "#10b981"  # Verified Clean seal

FONT = "Segoe UI"


# ─────────────────────────────────────────────────────────────────────────────
# ENGINE — Pure functions, zero GUI dependency
# ─────────────────────────────────────────────────────────────────────────────

class JpegCleaner:
    """
    Strips metadata segments from a JPEG file at the byte level.

    Segments REMOVED (metadata / privacy risk):
      APP0  0xE0  JFIF / JFXX header
      APP1  0xE1  EXIF + XMP
      APP2  0xE2  ICC color profile / FlashPix
      APP13 0xED  IPTC / Photoshop IRB
      APP14 0xEE  Adobe color transform info
      APP3..APP15 other application-specific segments
      COM   0xFE  Comment segments

    Segments KEPT (required for image decoding):
      SOI  0xD8  Start of Image
      SOF* 0xC0..0xCF  Frame headers
      DHT  0xC4  Huffman tables
      DQT  0xDB  Quantisation tables
      DRI  0xDD  Restart interval
      SOS  0xDA  Start of Scan + all compressed data that follows
      EOI  0xD9  End of Image
    """

    # APP0..APP15 (0xE0..0xEF) + COM (0xFE)
    _STRIP = set(range(0xE0, 0xF0)) | {0xFE}

    # Human-readable labels for the removal report
    _LABELS = {
        0xE0: "JFIF/JFXX header (APP0)",
        0xE2: "ICC color profile (APP2)",
        0xEB: "C2PA / Content Credentials (APP11)",
        0xED: "IPTC / Photoshop data (APP13)",
        0xEE: "Adobe color info (APP14)",
        0xFE: "Comment",
    }

    @classmethod
    def clean(cls, src: str, dst: str) -> list[str]:
        """
        Write a clean copy to *dst*. Returns list of removed items.
        Raises ValueError / IOError on bad input.
        """
        with open(src, "rb") as fh:
            data = fh.read()

        if data[:2] != b"\xff\xd8":
            raise ValueError("Not a valid JPEG (missing SOI marker)")

        removed: list[str] = []
        out = bytearray(b"\xff\xd8")  # SOI always goes through
        i = 2

        while i < len(data):
            # Seek next 0xFF (skip padding / raw bytes)
            if data[i] != 0xFF:
                # We're in compressed data territory — dump the remainder
                out += data[i:]
                break

            marker = data[i + 1]

            # --- Markers with no length field ---
            if marker == 0xD8:          # SOI (duplicate — skip)
                i += 2
                continue
            if marker == 0xD9:          # EOI
                out += b"\xff\xd9"
                break
            if 0xD0 <= marker <= 0xD7:  # RST0..RST7
                out += data[i:i + 2]
                i += 2
                continue
            if marker == 0x00:          # Stuffed byte (shouldn't appear here)
                out += data[i:i + 2]
                i += 2
                continue

            # --- SOS: header + all compressed data until EOI ---
            if marker == 0xDA:
                out += data[i:]
                break

            # --- Segments with a 2-byte length field ---
            if i + 3 >= len(data):
                break  # truncated file

            seg_len = struct.unpack(">H", data[i + 2: i + 4])[0]
            seg_end = i + 2 + seg_len   # length field includes itself

            if marker in cls._STRIP:
                # Identify what we stripped
                if marker == 0xE1:
                    seg_data = data[i + 4: seg_end]
                    if seg_data.startswith(b"Exif"):
                        removed.append("EXIF data (camera, GPS, settings)")
                    elif b"xpacket" in seg_data[:64] or seg_data.startswith(b"http://"):
                        removed.append("XMP metadata")
                    else:
                        removed.append("APP1 segment")
                else:
                    removed.append(cls._LABELS.get(marker, f"APP{marker - 0xE0} segment"))
            else:
                out += data[i:seg_end]

            i = seg_end

        with open(dst, "wb") as fh:
            fh.write(bytes(out))

        return removed


class PngCleaner:
    """
    Strips metadata chunks from a PNG file at the byte level.

    Chunks REMOVED (metadata):
      tEXt / iTXt / zTXt  — text metadata (author, software, comment…)
      eXIf                 — embedded EXIF
      iCCP                 — ICC color profile
      sRGB                 — sRGB rendering intent flag
      gAMA                 — gamma value
      cHRM                 — chromaticity data
      tIME                 — last modification timestamp
      bKGD                 — background colour hint
      hIST                 — colour histogram
      sPLT                 — suggested palette
      oFFs / pCAL / sCAL  — physical calibration

    Chunks KEPT (required for rendering):
      IHDR  Image header
      IDAT  Compressed pixel data
      IEND  End marker
      PLTE  Colour palette (required for indexed PNGs)
      tRNS  Transparency data
      pHYs  Physical pixel size (affects display; no PII)
    """

    PNG_SIG = b"\x89PNG\r\n\x1a\n"

    _STRIP: set[bytes] = {
        b"tEXt", b"iTXt", b"zTXt", b"eXIf",
        b"iCCP", b"sRGB", b"gAMA", b"cHRM",
        b"tIME", b"bKGD", b"hIST", b"sPLT",
        b"oFFs", b"pCAL", b"sCAL", b"caBX",
        b"cPRV",
    }

    _LABELS: dict[bytes, str] = {
        b"tEXt": "Text metadata (software, comment, author…)",
        b"iTXt": "International text metadata",
        b"zTXt": "Compressed text metadata",
        b"eXIf": "Embedded EXIF data",
        b"iCCP": "ICC color profile",
        b"sRGB": "sRGB rendering intent",
        b"gAMA": "Gamma value",
        b"cHRM": "Chromaticity data",
        b"tIME": "Modification timestamp",
        b"bKGD": "Background color hint",
        b"hIST": "Color histogram",
        b"sPLT": "Suggested palette",
        b"oFFs": "Image offset",
        b"pCAL": "Physical calibration",
        b"sCAL": "Physical scale",
        b"caBX": "C2PA / Content Credentials (caBX)",
        b"cPRV": "C2PA / Content Credentials (cPRV)",
    }

    @classmethod
    def clean(cls, src: str, dst: str) -> list[str]:
        """
        Write a clean copy to *dst*. Returns list of removed items.
        """
        with open(src, "rb") as fh:
            data = fh.read()

        if data[:8] != cls.PNG_SIG:
            raise ValueError("Not a valid PNG file (missing PNG signature)")

        removed_types: set[bytes] = set()
        out = bytearray(cls.PNG_SIG)
        i = 8

        while i + 12 <= len(data):
            chunk_len  = struct.unpack(">I", data[i:i + 4])[0]
            chunk_type = data[i + 4:i + 8]
            chunk_end  = i + 12 + chunk_len  # 4 len + 4 type + N data + 4 CRC

            if chunk_type in cls._STRIP:
                removed_types.add(chunk_type)
            else:
                out += data[i:chunk_end]

            i = chunk_end

        removed = [
            cls._LABELS.get(ct, ct.decode("ascii", errors="replace"))
            for ct in removed_types
        ]

        with open(dst, "wb") as fh:
            fh.write(bytes(out))

        return removed


# ── Public engine functions ────────────────────────────────────────────────

def detect_c2pa(image_path: str) -> tuple[bool, str]:
    """
    Detects if an image contains C2PA/Content Credentials metadata.
    Returns (detected: bool, details: str).
    """
    ext = Path(image_path).suffix.lower()
    try:
        if ext in (".jpg", ".jpeg"):
            with open(image_path, "rb") as f:
                data = f.read()
            if data[:2] != b"\xff\xd8":
                return False, ""
            i = 2
            while i < len(data):
                if data[i] != 0xFF:
                    next_ff = data.find(b"\xff", i)
                    if next_ff == -1:
                        break
                    i = next_ff
                if i + 1 >= len(data):
                    break
                marker = data[i + 1]
                if marker == 0xD9:
                    break
                if marker == 0xDA:
                    break
                if marker == 0xD8:
                    i += 2
                    continue
                if 0xD0 <= marker <= 0xD7 or marker == 0x00:
                    i += 2
                    continue
                if i + 3 >= len(data):
                    break
                seg_len = struct.unpack(">H", data[i + 2: i + 4])[0]
                seg_end = i + 2 + seg_len
                if marker == 0xEB: # APP11
                    seg_data = data[i + 4: seg_end]
                    if b"c2pa" in seg_data or b"jumb" in seg_data:
                        return True, "C2PA / Content Credentials (cryptographically signed provenance data)"
                i = seg_end
                
        elif ext == ".png":
            with open(image_path, "rb") as f:
                data = f.read()
            if data[:8] != b"\x89PNG\r\n\x1a\n":
                return False, ""
            i = 8
            while i + 12 <= len(data):
                chunk_len = struct.unpack(">I", data[i:i + 4])[0]
                chunk_type = data[i + 4:i + 8]
                if chunk_type in (b"caBX", b"cPRV"):
                    return True, f"C2PA / Content Credentials (PNG chunk: {chunk_type.decode('ascii', errors='ignore')})"
                i += 12 + chunk_len

        elif ext == ".webp":
            with open(image_path, "rb") as f:
                data = f.read()
            if data[:4] != b"RIFF" or data[8:12] != b"WEBP":
                return False, ""
            i = 12
            while i + 8 <= len(data):
                chunk_type = data[i:i + 4]
                chunk_len = struct.unpack("<I", data[i + 4:i + 8])[0]
                padded_len = chunk_len + (chunk_len % 2)
                if chunk_type == b"C2PA":
                    return True, "C2PA / Content Credentials (WEBP chunk: C2PA)"
                i += 8 + padded_len
    except Exception as e:
        return False, f"C2PA check error: {e}"
    return False, ""


def scan_metadata(image_path: str) -> dict[str, str]:
    """
    Read all metadata from *image_path* and return as an ordered dict.
    Pure function — no side effects, no GUI dependency.
    """
    meta: dict[str, str] = {}
    try:
        # Check C2PA presence
        c2pa_present, c2pa_desc = detect_c2pa(image_path)
        if c2pa_present:
            meta["C2PA / Content Credentials"] = f"[!] Present ({c2pa_desc})"

        img = Image.open(image_path)
        info = img.info or {}

        meta["Format"]     = img.format or "Unknown"
        meta["Color Mode"] = img.mode
        meta["Dimensions"] = f"{img.width} × {img.height} px"

        # --- EXIF via piexif ---
        if "exif" in info:
            try:
                exif_dict = piexif.load(info["exif"])
                for ifd_name, ifd_data in exif_dict.items():
                    if ifd_name == "thumbnail":
                        if ifd_data:
                            meta["EXIF Thumbnail"] = "Present"
                        continue
                    if not isinstance(ifd_data, dict):
                        continue
                    for tag_id, value in ifd_data.items():
                        tag_name = (
                            piexif.TAGS[ifd_name]
                            .get(tag_id, {})
                            .get("name", f"Tag_{tag_id}")
                        )
                        if isinstance(value, bytes):
                            display = value.decode("utf-8", errors="ignore").strip("\x00")
                        elif (
                            isinstance(value, tuple)
                            and len(value) == 2
                            and isinstance(value[0], int)
                        ):
                            display = f"{value[0]}/{value[1]}"
                        else:
                            display = str(value)
                        if display:
                            meta[tag_name] = display[:120]
            except Exception as exc:
                meta["EXIF (read error)"] = str(exc)

        # --- Other info keys (case-insensitive match) ---
        _INTERESTING_LOWER = {
            "dpi", "icc_profile", "photoshop", "xmp",
            "comment", "artist", "copyright", "software",
            "make", "model", "gps_ifd",
        }
        for key, val in info.items():
            if key.lower() in _INTERESTING_LOWER:
                if isinstance(val, bytes) and val:
                    meta[key.upper()] = f"<binary {len(val)} bytes>"
                elif val and not isinstance(val, bytes):
                    meta[key.upper()] = str(val)[:120]
            elif isinstance(val, str) and val and key.lower() not in ("n frames", "version"):
                # Catch-all: any remaining string-valued PNG text chunk
                meta[key.upper()] = val[:120]

        img.close()
    except Exception as exc:
        meta["Error"] = str(exc)

    return meta


def verify_clean(image_path: str) -> tuple[bool, list[str]]:
    """
    Re-scan a file that was supposedly cleaned.
    Returns (is_clean: bool, residuals: list[str]).
    An empty residuals list means the file is fully clean.
    """
    residuals: list[str] = []
    try:
        # Check C2PA
        c2pa_present, _ = detect_c2pa(image_path)
        if c2pa_present:
            residuals.append("C2PA / Content Credentials")

        img = Image.open(image_path)
        info = img.info or {}

        _SENSITIVE = {
            "exif": "EXIF data",
            "icc_profile": "ICC profile",
            "xmp": "XMP metadata",
            "photoshop": "Photoshop data",
            "comment": "Comment",
            "artist": "Artist field",
            "copyright": "Copyright field",
            "software": "Software field",
            "make": "Camera make",
            "model": "Camera model",
        }

        info_lower = {k.lower(): v for k, v in info.items()}
        for key, label in _SENSITIVE.items():
            val = info_lower.get(key)
            if val:
                residuals.append(label)

        # Deeper EXIF check
        exif_val = info_lower.get("exif")
        if exif_val:
            try:
                exif_dict = piexif.load(exif_val)
                if exif_dict.get("GPS"):
                    residuals.append("GPS coordinates")
            except Exception:
                pass

        img.close()
    except Exception:
        pass

    return (len(residuals) == 0, residuals)


def clean_lossless(src: str, dst: str) -> tuple[bool, list[str]]:
    """
    Dispatcher: routes to the appropriate lossless cleaner by file extension.
    Returns (success: bool, items: list[str]).
      On success:  items = list of what was removed.
      On failure:  items = [error message].
    Original file is NEVER modified.
    """
    ext = Path(src).suffix.lower()
    removed: list[str] = []

    try:
        if ext in (".jpg", ".jpeg"):
            removed = JpegCleaner.clean(src, dst)

        elif ext == ".png":
            removed = PngCleaner.clean(src, dst)

        elif ext == ".webp":
            img = Image.open(src)
            info = img.info or {}
            tracked: list[str] = []
            if "exif"        in info and info["exif"]:       tracked.append("EXIF data")
            if "icc_profile" in info and info["icc_profile"]: tracked.append("ICC color profile")
            if "xmp"         in info and info["xmp"]:         tracked.append("XMP metadata")

            c2pa_present, _ = detect_c2pa(src)
            if c2pa_present:
                tracked.append("C2PA / Content Credentials")

            # Detect if the source WEBP was lossless
            is_lossless = bool(info.get("lossless", False))
            save_kw: dict = {"format": "WEBP", "exif": b"", "icc_profile": b"", "xmp": b""}
            if is_lossless:
                save_kw["lossless"] = True
            else:
                save_kw["quality"] = 100  # minimise re-encode degradation

            img.save(dst, **save_kw)
            img.close()
            removed = tracked or ["No metadata found"]

        elif ext == ".gif":
            img = Image.open(src)
            info = img.info or {}
            if "comment" in info:
                removed.append("Comment")
            img.save(dst, format="GIF")
            img.close()

        elif ext in (".tiff", ".tif"):
            img = Image.open(src)
            info = img.info or {}
            if "exif" in info: removed.append("EXIF data")
            img.save(dst, format="TIFF")
            img.close()

        elif ext == ".bmp":
            img = Image.open(src)
            img.save(dst, format="BMP")
            img.close()

        else:
            img = Image.open(src)
            img.save(dst)
            img.close()

        if not removed:
            removed.append("No metadata found — file was already clean")

        return True, removed

    except Exception as exc:
        return False, [f"Error: {exc}"]


# ─────────────────────────────────────────────────────────────────────────────
# GUI
# ─────────────────────────────────────────────────────────────────────────────

class FileRow:
    """State model for a single file in the batch queue."""

    WAITING    = "waiting"
    PROCESSING = "processing"
    DONE       = "done"
    ERROR      = "error"

    def __init__(self, path: str) -> None:
        self.path      = path
        self.name      = Path(path).name
        self.status    = self.WAITING
        self.removed:  list[str] = []
        self.verified: bool      = False
        self.residuals: list[str] = []
        self.dst_path  = ""
        self.error_msg = ""


class SiftoolApp:

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(f"Siftool  v{VERSION}")
        try:
            self.root.iconbitmap("siftool.ico")
        except Exception:
            pass
        self.root.geometry("860x660")
        self.root.configure(bg=BG)
        self.root.resizable(True, True)
        self.root.minsize(680, 500)

        self.files: list[FileRow]          = []
        self._q: queue.Queue               = queue.Queue()
        self._worker: threading.Thread | None = None
        self._running = False

        self._dnd_available = False
        self._DND_FILES: str = ""
        self._try_enable_dnd()

        self._build_ui()
        self._poll()

    # ── DnD bootstrap ────────────────────────────────────────────────────────

    def _try_enable_dnd(self) -> None:
        try:
            from tkinterdnd2 import DND_FILES  # noqa: F401
            self._dnd_available = True
            self._DND_FILES = DND_FILES
        except ImportError:
            pass

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        self._build_header()
        self._build_dropzone()
        self._build_footer()       # footer FIRST so side="bottom" reserves space
        self._build_list_panel()   # list fills remaining space with expand=True

    def _build_header(self) -> None:
        hdr = tk.Frame(self.root, bg=SURFACE, pady=16)
        hdr.pack(fill="x")

        row = tk.Frame(hdr, bg=SURFACE)
        row.pack()

        tk.Label(row, text="◈", font=(FONT, 22, "bold"),
                 bg=SURFACE, fg=ACCENT).pack(side="left")
        tk.Label(row, text=" Siftool", font=(FONT, 20, "bold"),
                 bg=SURFACE, fg=TEXT).pack(side="left")
        tk.Label(row, text=f"  {VERSION}", font=(FONT, 10),
                 bg=SURFACE, fg=TEXT_M).pack(side="left", pady=(8, 0))

        tk.Label(hdr,
                 text="Lossless metadata removal · 100 % offline · Verified Clean",
                 font=(FONT, 9), bg=SURFACE, fg=TEXT_M).pack(pady=(2, 0))

    def _build_dropzone(self) -> None:
        outer = tk.Frame(self.root, bg=BG, padx=20, pady=10)
        outer.pack(fill="x")

        self.drop_frame = tk.Frame(outer, bg=CARD, relief="flat", bd=0,
                                   pady=22, padx=24, cursor="hand2")
        self.drop_frame.pack(fill="x")
        self.drop_frame.bind("<Button-1>", lambda _: self._add_files())

        tk.Label(self.drop_frame, text="⬇", font=(FONT, 26),
                 bg=CARD, fg=ACCENT, cursor="hand2").pack()

        hint = ("Drop images or folders here"
                if not self._dnd_available
                else "Drop images or folders here")
        self.drop_hint = tk.Label(self.drop_frame, text=hint,
                                   font=(FONT, 12), bg=CARD, fg=TEXT_M, cursor="hand2")
        self.drop_hint.pack(pady=(4, 2))
        self.drop_hint.bind("<Button-1>", lambda _: self._add_files())

        tk.Label(self.drop_frame,
                 text="JPEG · PNG · WEBP · GIF · TIFF · BMP",
                 font=(FONT, 9), bg=CARD, fg=TEXT_M).pack()

        if self._dnd_available:
            from tkinterdnd2 import DND_FILES
            self.drop_frame.drop_target_register(DND_FILES)
            self.drop_frame.dnd_bind("<<Drop>>", self._on_drop)

        # Button row
        btn_row = tk.Frame(self.root, bg=BG)
        btn_row.pack(pady=(0, 6))

        self._btn(btn_row, "＋  Add Files",   self._add_files,  ACCENT).pack(side="left", padx=5)
        self._btn(btn_row, "📁  Add Folder",  self._add_folder, CARD2).pack(side="left", padx=5)
        self._btn(btn_row, "✕  Clear Queue", self._clear_all,  CARD2).pack(side="left", padx=5)

    def _build_list_panel(self) -> None:
        panel = tk.Frame(self.root, bg=BG, padx=20)
        panel.pack(fill="both", expand=True)

        top = tk.Frame(panel, bg=BG)
        top.pack(fill="x", pady=(0, 6))

        self.count_lbl = tk.Label(top, text="No files added",
                                   font=(FONT, 9, "bold"), bg=BG, fg=TEXT_M, anchor="w")
        self.count_lbl.pack(side="left")

        # Split into Left (Treeview) and Right (Details Panel)
        main_split = tk.Frame(panel, bg=BG)
        main_split.pack(fill="both", expand=True)

        left_pane = tk.Frame(main_split, bg=BG)
        left_pane.pack(side="left", fill="both", expand=True)

        cols = ("name", "status", "details")
        self.tree = ttk.Treeview(left_pane, columns=cols, show="headings",
                                  selectmode="browse", height=13)
        self.tree.heading("name",    text="File")
        self.tree.heading("status",  text="Status")
        self.tree.heading("details", text="Details")
        self.tree.column("name",    width=200, anchor="w", stretch=True)
        self.tree.column("status",  width=120, anchor="center", stretch=False)
        self.tree.column("details", width=200, anchor="w",      stretch=True)

        sb = ttk.Scrollbar(left_pane, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        # Right pane: Details Panel (width=300)
        self.right_pane = tk.Frame(main_split, bg=CARD, width=300, bd=1, relief="flat")
        self.right_pane.pack(side="right", fill="both", padx=(14, 0))
        self.right_pane.pack_propagate(False) # Keep fixed width

        self._build_details_placeholder()

        self._style_tree()
        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)

    def _build_details_placeholder(self) -> None:
        for widget in self.right_pane.winfo_children():
            widget.destroy()
            
        container = tk.Frame(self.right_pane, bg=CARD)
        container.pack(fill="both", expand=True, pady=60)
        
        tk.Label(container, text="◈", font=(FONT, 32), bg=CARD, fg=TEXT_M).pack()
        tk.Label(container, text="Metadata Inspector", font=(FONT, 12, "bold"), bg=CARD, fg=TEXT).pack(pady=10)
        tk.Label(container, text="Select any file in the queue\nto inspect its EXIF tags & C2PA state.", 
                 font=(FONT, 9), bg=CARD, fg=TEXT_M, justify="center").pack()

    def _on_tree_select(self, event) -> None:
        selected = self.tree.selection()
        if not selected:
            self._build_details_placeholder()
            return
            
        path = selected[0]
        row = self._find(path)
        if not row:
            self._build_details_placeholder()
            return
            
        for widget in self.right_pane.winfo_children():
            widget.destroy()
            
        # Title
        hdr = tk.Frame(self.right_pane, bg=CARD, padx=12, pady=10)
        hdr.pack(fill="x")
        
        tk.Label(hdr, text=row.name, font=(FONT, 11, "bold"), bg=CARD, fg=TEXT, anchor="w").pack(fill="x")
        
        ext_desc = Path(path).suffix.upper()[1:] + " Image"
        tk.Label(hdr, text=ext_desc, font=(FONT, 9), bg=CARD, fg=TEXT_M, anchor="w").pack(fill="x")

        # C2PA Check
        c2pa_present, c2pa_desc = detect_c2pa(path)
        
        if c2pa_present:
            # High-impact warning banner
            c2pa_banner = tk.Frame(self.right_pane, bg="#451a03", bd=1, relief="solid", highlightbackground=WARNING, padx=10, pady=8)
            c2pa_banner.pack(fill="x", padx=12, pady=6)
            tk.Label(c2pa_banner, text="⚠  Content Credentials (C2PA)", font=(FONT, 9, "bold"), bg="#451a03", fg=WARNING, anchor="w").pack(fill="x")
            tk.Label(c2pa_banner, text="This image contains cryptographically signed metadata. It may leak: author identity, AI tool origin, exact timestamps, and edit history.\n\nSiftool will remove this on Sift.", 
                     font=(FONT, 8), bg="#451a03", fg=TEXT, justify="left", wraplength=250, anchor="w").pack(fill="x", pady=(4, 0))
        elif row.status == FileRow.DONE and row.verified:
            # Verified Clean seal banner
            seal_banner = tk.Frame(self.right_pane, bg="#064e3b", bd=1, relief="solid", highlightbackground=SEAL, padx=10, pady=8)
            seal_banner.pack(fill="x", padx=12, pady=6)
            tk.Label(seal_banner, text="✓  Verified Clean Seal", font=(FONT, 9, "bold"), bg="#064e3b", fg=SEAL, anchor="w").pack(fill="x")
            tk.Label(seal_banner, text="All privacy-sensitive metadata, including EXIF, ICC, GPS, and C2PA, have been successfully stripped.", 
                     font=(FONT, 8), bg="#064e3b", fg=TEXT, justify="left", wraplength=250, anchor="w").pack(fill="x", pady=(4, 0))
        
        # Details section (removed items or residuals)
        if row.status == FileRow.DONE:
            info_frame = tk.Frame(self.right_pane, bg=CARD, padx=12, pady=6)
            info_frame.pack(fill="x")
            if row.removed:
                removed_lbl = tk.Label(info_frame, text=f"Removed: {len(row.removed)} type(s)", font=(FONT, 9, "bold"), bg=CARD, fg=SUCCESS, anchor="w")
                removed_lbl.pack(fill="x")
                
                # bullet list of removed
                rem_text = "\n".join(f"• {item}" for item in row.removed[:4])
                if len(row.removed) > 4:
                    rem_text += f"\n• and {len(row.removed) - 4} more..."
                tk.Label(info_frame, text=rem_text, font=(FONT, 8), bg=CARD, fg=TEXT_M, justify="left", anchor="w").pack(fill="x", pady=(2, 6))

        # Metadata table title
        tk.Label(self.right_pane, text="Original Metadata Elements", font=(FONT, 9, "bold"), bg=CARD, fg=TEXT_M, anchor="w").pack(fill="x", padx=12, pady=(10, 4))

        # Treeview for metadata elements
        tree_frame = tk.Frame(self.right_pane, bg=CARD)
        tree_frame.pack(fill="both", expand=True, padx=12, pady=(0, 10))

        cols = ("key", "val")
        meta_tree = ttk.Treeview(tree_frame, columns=cols, show="headings", selectmode="none")
        meta_tree.heading("key", text="Element")
        meta_tree.heading("val", text="Value")
        meta_tree.column("key", width=100, anchor="w")
        meta_tree.column("val", width=150, anchor="w")

        msb = ttk.Scrollbar(tree_frame, orient="vertical", command=meta_tree.yview)
        meta_tree.configure(yscrollcommand=msb.set)
        meta_tree.pack(side="left", fill="both", expand=True)
        msb.pack(side="right", fill="y")

        # Load metadata
        meta = scan_metadata(path)
        for k, v in meta.items():
            meta_tree.insert("", "end", values=(k, v))

    def _build_footer(self) -> None:
        # Status bar (bottom-most)
        self.status_var = tk.StringVar(value="Ready — add files and press Sift All.")
        tk.Label(self.root, textvariable=self.status_var,
                 bg=CARD, fg=TEXT_M, font=(FONT, 9),
                 anchor="w", padx=14, pady=6).pack(fill="x", side="bottom")

        # Progress + action button
        foot = tk.Frame(self.root, bg=SURFACE, pady=12)
        foot.pack(fill="x", side="bottom")

        inner = tk.Frame(foot, bg=SURFACE)
        inner.pack()

        self.progress_var = tk.DoubleVar(value=0)
        self.progress = ttk.Progressbar(inner, variable=self.progress_var,
                                         maximum=100, length=360,
                                         mode="determinate")
        self.progress.pack(side="left", padx=(0, 14))

        self.sift_btn = self._btn(inner, "  ⟳  Sift All  ",
                                   self._start_batch, ACCENT, width=14)
        self.sift_btn.pack(side="left")

    def _btn(self, parent, text, cmd, color,
             state: str = "normal", width: int | None = None) -> tk.Button:
        kw: dict = dict(
            text=text, command=cmd,
            bg=color, fg=TEXT,
            font=(FONT, 10, "bold"),
            relief="flat", bd=0,
            padx=14, pady=8,
            cursor="hand2",
            activebackground=ACCENT_H,
            activeforeground=TEXT,
            state=state,
        )
        if width:
            kw["width"] = width
        return tk.Button(parent, **kw)

    def _style_tree(self) -> None:
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("Treeview",
                         background=CARD, foreground=TEXT,
                         rowheight=30, fieldbackground=CARD,
                         borderwidth=0, font=(FONT, 9))
        style.configure("Treeview.Heading",
                         background=SURFACE, foreground=TEXT_M,
                         relief="flat", font=(FONT, 9, "bold"))
        style.map("Treeview", background=[("selected", ACCENT)])

        self.tree.tag_configure("waiting",    foreground=TEXT_M)
        self.tree.tag_configure("processing", foreground=WARNING)
        self.tree.tag_configure("done",       foreground=SEAL)
        self.tree.tag_configure("residual",   foreground=WARNING)
        self.tree.tag_configure("error",      foreground=DANGER)

    # ── File management ───────────────────────────────────────────────────────

    def _add_files(self) -> None:
        paths = filedialog.askopenfilenames(
            title="Select images",
            filetypes=[
                ("Images", "*.jpg *.jpeg *.png *.bmp *.gif *.tiff *.tif *.webp"),
                ("All files", "*.*"),
            ],
        )
        self._enqueue(list(paths))

    def _add_folder(self) -> None:
        folder = filedialog.askdirectory(title="Select folder")
        if not folder:
            return
        paths: list[str] = []
        for root_dir, _, fnames in os.walk(folder):
            for fname in fnames:
                if Path(fname).suffix.lower() in SUPPORTED_EXTS:
                    paths.append(os.path.join(root_dir, fname))
        self._enqueue(paths)

    def _on_drop(self, event) -> None:
        """Handle tkinterdnd2 drop event."""
        raw: str = event.data
        # On Windows, paths with spaces are wrapped in {braces}
        tokens = re.findall(r"\{([^}]+)\}|(\S+)", raw)
        flat = [a or b for a, b in tokens]
        paths: list[str] = []
        for p in flat:
            if os.path.isdir(p):
                for rd, _, fnames in os.walk(p):
                    for fn in fnames:
                        if Path(fn).suffix.lower() in SUPPORTED_EXTS:
                            paths.append(os.path.join(rd, fn))
            elif os.path.isfile(p) and Path(p).suffix.lower() in SUPPORTED_EXTS:
                paths.append(p)
        self._enqueue(paths)

    def _enqueue(self, paths: list[str]) -> None:
        existing = {f.path for f in self.files}
        added = 0
        for p in paths:
            if p not in existing:
                row = FileRow(p)
                self.files.append(row)
                self.tree.insert(
                    "", "end", iid=p,
                    values=(row.name, "⧖  Waiting", ""),
                    tags=("waiting",),
                )
                added += 1
        self._refresh_count()
        if added:
            self.status_var.set(f"{added} file(s) added to queue.")

    def _clear_all(self) -> None:
        if self._running:
            return
        self.files.clear()
        for iid in self.tree.get_children():
            self.tree.delete(iid)
        self.progress_var.set(0)
        self._refresh_count()
        self.status_var.set("Queue cleared.")

    def _refresh_count(self) -> None:
        n = len(self.files)
        self.count_lbl.config(
            text="No files added" if n == 0 else f"{n} file(s) in queue"
        )

    # ── Batch processing ──────────────────────────────────────────────────────

    def _start_batch(self) -> None:
        pending = [f for f in self.files if f.status == FileRow.WAITING]
        if not pending:
            messagebox.showinfo("Siftool", "No files waiting to be processed.")
            return

        self._running = True
        self.sift_btn.config(state="disabled")
        self.status_var.set(f"Processing {len(pending)} file(s)…")
        self.progress_var.set(0)

        t = threading.Thread(target=self._worker_fn, args=(pending,), daemon=True)
        t.start()

    def _worker_fn(self, rows: list[FileRow]) -> None:
        """Background thread — communicates results via self._q."""
        total = len(rows)
        for idx, row in enumerate(rows):
            self._q.put(("progress", row.path, idx, total))

            p = Path(row.path)
            dst = str(p.parent / (p.stem + "_clean" + p.suffix))

            ok, items = clean_lossless(row.path, dst)
            if ok:
                is_clean, residuals = verify_clean(dst)
                self._q.put(("done", row.path, dst, items, is_clean, residuals))
            else:
                self._q.put(("error", row.path, items))

        self._q.put(("finished", total))

    def _poll(self) -> None:
        """Main-thread poller — drains the queue every 80 ms."""
        try:
            while True:
                msg = self._q.get_nowait()
                kind = msg[0]

                if kind == "progress":
                    _, path, idx, total = msg
                    row = self._find(path)
                    if row:
                        row.status = FileRow.PROCESSING
                        self.tree.item(path, values=(row.name, "⟳  Processing…", ""),
                                        tags=("processing",))
                    pct = int(idx / total * 100)
                    self.progress_var.set(pct)

                elif kind == "done":
                    _, path, dst, items, is_clean, residuals = msg
                    row = self._find(path)
                    if row:
                        row.status    = FileRow.DONE
                        row.dst_path  = dst
                        row.removed   = items
                        row.verified  = is_clean
                        row.residuals = residuals

                        if is_clean:
                            s_text = "✓  Verified Clean"
                            detail = f"{len(items)} item(s) removed"
                            tag    = "done"
                        else:
                            s_text = "⚠  Residuals found"
                            detail = ", ".join(residuals[:3])
                            tag    = "residual"

                        self.tree.item(path, values=(row.name, s_text, detail),
                                        tags=(tag,))

                elif kind == "error":
                    _, path, msgs = msg
                    row = self._find(path)
                    if row:
                        row.status    = FileRow.ERROR
                        row.error_msg = "; ".join(msgs)
                        self.tree.item(
                            path,
                            values=(row.name, "✕  Error", row.error_msg),
                            tags=("error",),
                        )

                elif kind == "finished":
                    total    = msg[1]
                    done     = sum(1 for f in self.files if f.status == FileRow.DONE)
                    verified = sum(1 for f in self.files if f.verified)
                    errors   = sum(1 for f in self.files if f.status == FileRow.ERROR)
                    self.progress_var.set(100)
                    self._running = False
                    self.sift_btn.config(state="normal")
                    self.status_var.set(
                        f"Done — {done} cleaned · {verified} Verified Clean "
                        f"· {errors} error(s) · saved as *_clean.ext"
                    )

        except queue.Empty:
            pass

        self.root.after(80, self._poll)

    def _find(self, path: str) -> FileRow | None:
        return next((f for f in self.files if f.path == path), None)


# ─────────────────────────────────────────────────────────────────────────────
# CLI MAIN
# ─────────────────────────────────────────────────────────────────────────────

def cli_main() -> None:
    import sys
    import argparse
    import json
    
    parser = argparse.ArgumentParser(
        description=f"Siftool v{VERSION} — Lossless Image Metadata Cleaner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python siftool.py scan photo.jpg
  python siftool.py clean photo.jpg
  python siftool.py clean --folder ./images
  python siftool.py verify photo_clean.jpg
  python siftool.py clean photo.jpg --json
"""
    )
    subparsers = parser.add_subparsers(dest="command", help="Subcommand to run")

    # scan
    scan_p = subparsers.add_parser("scan", help="Scan metadata of one or more files")
    scan_p.add_argument("files", nargs="+", help="File paths to scan")
    scan_p.add_argument("--json", action="store_true", help="Output results in JSON format")

    # clean
    clean_p = subparsers.add_parser("clean", help="Clean metadata from files losslessly")
    clean_p.add_argument("files", nargs="*", help="File paths to clean")
    clean_p.add_argument("--folder", help="Folder path to clean all supported images in")
    clean_p.add_argument("--json", action="store_true", help="Output results in JSON format")

    # verify
    verify_p = subparsers.add_parser("verify", help="Verify if files are metadata-clean")
    verify_p.add_argument("files", nargs="+", help="File paths to verify")
    verify_p.add_argument("--json", action="store_true", help="Output results in JSON format")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    if args.command == "scan":
        results = {}
        for path_str in args.files:
            p = Path(path_str)
            if not p.exists():
                results[path_str] = {"error": "File not found"}
                continue
            meta = scan_metadata(str(p))
            results[path_str] = meta

        if args.json:
            print(json.dumps(results, indent=2, ensure_ascii=False))
        else:
            for path_str, meta in results.items():
                print(f"\nMetadata for {path_str}:")
                print("=" * (13 + len(path_str)))
                if "Error" in meta:
                    print(f"  Error: {meta['Error']}")
                else:
                    for k, v in meta.items():
                        print(f"  {k:28} | {v}")

    elif args.command == "clean":
        # Gather all files
        paths_to_clean: list[Path] = []
        if args.folder:
            folder = Path(args.folder)
            if not folder.is_dir():
                if args.json:
                    print(json.dumps({"error": f"Folder {args.folder} does not exist or is not a directory"}, indent=2))
                else:
                    print(f"Error: Folder '{args.folder}' does not exist or is not a directory.")
                sys.exit(1)
            for root_dir, _, fnames in os.walk(folder):
                for fname in fnames:
                    if Path(fname).suffix.lower() in SUPPORTED_EXTS:
                        paths_to_clean.append(Path(root_dir) / fname)
        
        for f_str in args.files:
            p = Path(f_str)
            if p.is_dir():
                for root_dir, _, fnames in os.walk(p):
                    for fname in fnames:
                        if Path(fname).suffix.lower() in SUPPORTED_EXTS:
                            paths_to_clean.append(Path(root_dir) / fname)
            elif p.exists():
                paths_to_clean.append(p)
            else:
                if not args.json:
                    print(f"Warning: File or path not found: {f_str}")
        
        if not paths_to_clean:
            if args.json:
                print(json.dumps({"error": "No files found to clean"}, indent=2))
            else:
                print("Error: No files specified and no files found in folder.")
            sys.exit(1)

        results = []
        cleaned_count = 0
        verified_count = 0
        error_count = 0

        if not args.json:
            print(f"[siftool] Cleaning {len(paths_to_clean)} file(s)...")

        for p in paths_to_clean:
            dst = p.parent / (p.stem + "_clean" + p.suffix)
            ok, items = clean_lossless(str(p), str(dst))
            if ok:
                is_clean, residuals = verify_clean(str(dst))
                status_str = "Verified Clean" if is_clean else "Residuals"
                if is_clean:
                    verified_count += 1
                cleaned_count += 1
                
                results.append({
                    "file": str(p),
                    "status": "ok",
                    "cleaned_to": str(dst),
                    "removed": items,
                    "verified_clean": is_clean,
                    "residuals": residuals
                })
                if not args.json:
                    items_desc = ", ".join(items)
                    seal = "[Verified Clean]" if is_clean else "[Residuals Found]"
                    print(f"  [OK]  {p.name} -> {dst.name}  ({items_desc})  {seal}")
            else:
                error_count += 1
                results.append({
                    "file": str(p),
                    "status": "error",
                    "error": items[0]
                })
                if not args.json:
                    print(f"  [FAIL]  {p.name} -> ERROR: {items[0]}")

        if args.json:
            print(json.dumps({
                "summary": {
                    "total": len(paths_to_clean),
                    "cleaned": cleaned_count,
                    "verified_clean": verified_count,
                    "errors": error_count
                },
                "results": results
            }, indent=2, ensure_ascii=False))
        else:
            print(f"[siftool] Done. {cleaned_count} cleaned | {verified_count} Verified Clean | {error_count} error(s).")

    elif args.command == "verify":
        results = {}
        for path_str in args.files:
            p = Path(path_str)
            if not p.exists():
                results[path_str] = {"status": "error", "error": "File not found"}
                continue
            is_clean, residuals = verify_clean(str(p))
            results[path_str] = {
                "status": "ok",
                "verified_clean": is_clean,
                "residuals": residuals
            }

        if args.json:
            print(json.dumps(results, indent=2, ensure_ascii=False))
        else:
            print("\nVerification report:")
            print("====================")
            for path_str, res in results.items():
                if res["status"] == "error":
                    print(f"  {path_str:30} | [FAIL] ERROR: {res['error']}")
                elif res["verified_clean"]:
                    print(f"  {path_str:30} | [OK] Verified Clean")
                else:
                    res_str = ", ".join(res["residuals"])
                    print(f"  {path_str:30} | [WARN] Not Clean (residuals: {res_str})")


# ─────────────────────────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        cli_main()
    else:
        try:
            from tkinterdnd2 import TkinterDnD
            root: tk.Tk = TkinterDnD.Tk()
        except ImportError:
            root = tk.Tk()

        app = SiftoolApp(root)
        root.mainloop()
