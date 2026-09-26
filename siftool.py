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

import binascii
import io
import os
import queue
import re
import struct
import threading
from pathlib import Path

import piexif
import sys
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from PIL import Image

# Prevent crashes in pythonw.exe or PyInstaller console=False where sys.stdout/stderr are None
if sys.stdout is None:
    class NullWriter:
        def write(self, *args, **kwargs): pass
        def flush(self, *args, **kwargs): pass
    sys.stdout = NullWriter()
if sys.stderr is None:
    class NullWriter:
        def write(self, *args, **kwargs): pass
        def flush(self, *args, **kwargs): pass
    sys.stderr = NullWriter()



# ─────────────────────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────

VERSION = "1.0.0"

SUPPORTED_EXTS = {
    ".jpg", ".jpeg", ".png", ".bmp", ".gif", ".tiff", ".tif", ".webp",
    ".mp4", ".mov", ".mkv", ".avi", ".m4v"
}

# Colour palette — deep dark with violet accent (Espelho do Universo)
BG      = "#050508"
SURFACE = "#0b0b14"
CARD    = "#111122"
CARD2   = "#181832"
ACCENT  = "#8B00FF"
ACCENT_H = "#C026FF"
TEXT    = "#f1efff"
TEXT_M  = "#8a85a5"
SUCCESS = "#00FFC2"
WARNING = "#FFC107"
DANGER  = "#FF007F"
SEAL    = "#00F0FF"  # Verified Clean seal


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
    def _sanitise_exif(cls, exif_seg_data: bytes) -> bytes:
        exif_dict = piexif.load(exif_seg_data[6:])
        if "GPS" in exif_dict:
            exif_dict["GPS"] = {}
        if "0th" in exif_dict:
            exif_dict["0th"].pop(315, None)      # Artist
            exif_dict["0th"].pop(33432, None)    # Copyright
        if "Exif" in exif_dict:
            exif_dict["Exif"].pop(37500, None)   # MakerNote
            exif_dict["Exif"].pop(42033, None)   # BodySerialNumber
            exif_dict["Exif"].pop(42032, None)   # CameraOwnerName
        exif_bytes = piexif.dump(exif_dict)
        return exif_bytes

    @classmethod
    def clean(cls, src: str, dst: str, preset: str = "paranoia") -> list[str]:
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

        strip_markers = set(cls._STRIP)
        if preset in ("photographer", "social"):
            # Preserve ICC profile (APP2)
            strip_markers.discard(0xE2)

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

            if marker in strip_markers:
                # Identify what we stripped
                if marker == 0xE1:
                    seg_data = data[i + 4: seg_end]
                    if seg_data.startswith(b"Exif"):
                        if preset == "photographer":
                            try:
                                sanitized_exif = cls._sanitise_exif(seg_data)
                                out += b"\xff\xe1" + struct.pack(">H", len(sanitized_exif) + 2) + sanitized_exif
                                removed.append("GPS & personal tags removed (EXIF preserved)")
                            except Exception:
                                # Fallback to stripping if sanitisation fails
                                removed.append("EXIF data (camera, GPS, settings)")
                        else:
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
    def clean(cls, src: str, dst: str, preset: str = "paranoia") -> list[str]:
        """
        Write a clean copy to *dst*. Returns list of removed items.
        """
        with open(src, "rb") as fh:
            data = fh.read()

        if data[:8] != cls.PNG_SIG:
            raise ValueError("Not a valid PNG file (missing PNG signature)")

        removed_types: set[bytes] = set()
        exif_sanitised = False
        out = bytearray(cls.PNG_SIG)
        i = 8

        strip_chunks = set(cls._STRIP)
        if preset in ("photographer", "social"):
            strip_chunks.difference_update({b"iCCP", b"sRGB", b"gAMA", b"cHRM"})
        if preset == "photographer":
            strip_chunks.discard(b"eXIf")

        while i + 12 <= len(data):
            chunk_len  = struct.unpack(">I", data[i:i + 4])[0]
            chunk_type = data[i + 4:i + 8]
            chunk_data = data[i + 8:i + 8 + chunk_len]
            chunk_end  = i + 12 + chunk_len  # 4 len + 4 type + N data + 4 CRC

            if chunk_type in strip_chunks:
                removed_types.add(chunk_type)
            elif chunk_type == b"eXIf" and preset == "photographer":
                try:
                    # PNG eXIf chunk has raw EXIF bytes
                    exif_dict = piexif.load(chunk_data)
                    if "GPS" in exif_dict:
                        exif_dict["GPS"] = {}
                    if "0th" in exif_dict:
                        exif_dict["0th"].pop(315, None)      # Artist
                        exif_dict["0th"].pop(33432, None)    # Copyright
                    if "Exif" in exif_dict:
                        exif_dict["Exif"].pop(37500, None)   # MakerNote
                        exif_dict["Exif"].pop(42033, None)   # BodySerialNumber
                        exif_dict["Exif"].pop(42032, None)   # CameraOwnerName
                    sanitized_exif = piexif.dump(exif_dict)
                    out += make_png_chunk(b"eXIf", sanitized_exif)
                    exif_sanitised = True
                except Exception:
                    removed_types.add(b"eXIf")  # Fallback to stripping
            else:
                out += data[i:chunk_end]

            i = chunk_end

        with open(dst, "wb") as fh:
            fh.write(bytes(out))

        removed = [
            cls._LABELS.get(ct, ct.decode("ascii", errors="replace"))
            for ct in removed_types
        ]
        if exif_sanitised:
            removed.append("GPS & personal tags removed (EXIF preserved)")

        return removed


def _find_exif_tag(tag_name: str) -> tuple[str, int] | None:
    for ifd in ("0th", "Exif", "GPS", "1st"):
        for tag_id, tag_info in piexif.TAGS[ifd].items():
            if tag_info.get("name") == tag_name:
                return ifd, tag_id
    if tag_name.startswith("Tag_"):
        try:
            tag_id = int(tag_name[4:])
            for ifd in ("0th", "Exif", "GPS", "1st"):
                if tag_id in piexif.TAGS[ifd]:
                    return ifd, tag_id
        except ValueError:
            pass
    return None

def update_exif_tag(image_path: str, tag_name: str, new_val_str: str) -> bool:
    try:
        exif_dict = piexif.load(image_path)
        tag_loc = _find_exif_tag(tag_name)
        if not tag_loc:
            return False
        ifd, tag_id = tag_loc
        orig_val = exif_dict[ifd].get(tag_id)
        if isinstance(orig_val, bytes):
            exif_dict[ifd][tag_id] = new_val_str.encode("utf-8")
        elif isinstance(orig_val, tuple) and len(orig_val) == 2:
            if "/" in new_val_str:
                num, den = new_val_str.split("/", 1)
                exif_dict[ifd][tag_id] = (int(num.strip()), int(den.strip()))
            else:
                exif_dict[ifd][tag_id] = (int(new_val_str), 1)
        elif isinstance(orig_val, int):
            exif_dict[ifd][tag_id] = int(new_val_str)
        else:
            exif_dict[ifd][tag_id] = new_val_str.encode("utf-8")
        exif_bytes = piexif.dump(exif_dict)
        piexif.insert(exif_bytes, image_path)
        return True
    except Exception as e:
        print(f"Error EXIF update: {e}")
        return False

def delete_exif_tag(image_path: str, tag_name: str) -> bool:
    try:
        exif_dict = piexif.load(image_path)
        tag_loc = _find_exif_tag(tag_name)
        if not tag_loc:
            return False
        ifd, tag_id = tag_loc
        if tag_id in exif_dict[ifd]:
            exif_dict[ifd].pop(tag_id)
            exif_bytes = piexif.dump(exif_dict)
            piexif.insert(exif_bytes, image_path)
            return True
        return False
    except Exception as e:
        print(f"Error EXIF delete: {e}")
        return False

def make_png_chunk(chunk_type: bytes, chunk_data: bytes) -> bytes:
    length = len(chunk_data)
    len_bytes = struct.pack(">I", length)
    type_and_data = chunk_type + chunk_data
    crc = binascii.crc32(type_and_data) & 0xffffffff
    crc_bytes = struct.pack(">I", crc)
    return len_bytes + type_and_data + crc_bytes

def modify_png_chunk(image_path: str, keyword: str, new_value: str | None = None) -> bool:
    try:
        with open(image_path, "rb") as f:
            data = f.read()
        if data[:8] != b"\x89PNG\r\n\x1a\n":
            return False
        out = bytearray(b"\x89PNG\r\n\x1a\n")
        i = 8
        keyword_bytes = keyword.lower().encode("latin-1", errors="ignore")
        new_chunk_written = False
        while i + 12 <= len(data):
            chunk_len  = struct.unpack(">I", data[i:i + 4])[0]
            chunk_type = data[i + 4:i + 8]
            chunk_data = data[i + 8:i + 8 + chunk_len]
            chunk_end  = i + 12 + chunk_len
            is_target = False
            if chunk_type in (b"tEXt", b"zTXt", b"iTXt"):
                null_idx = chunk_data.find(b"\x00")
                if null_idx != -1:
                    chunk_key = chunk_data[:null_idx].lower()
                    if chunk_key == keyword_bytes:
                        is_target = True
            if is_target:
                pass
            else:
                if chunk_type == b"IDAT" and new_value is not None and not new_chunk_written:
                    new_data = keyword.encode("latin-1", errors="ignore") + b"\0" + new_value.encode("latin-1", errors="ignore")
                    out.extend(make_png_chunk(b"tEXt", new_data))
                    new_chunk_written = True
                out.extend(data[i:chunk_end])
            i = chunk_end
        if new_value is not None and not new_chunk_written:
            iend_idx = out.find(b"IEND")
            if iend_idx != -1:
                new_data = keyword.encode("latin-1", errors="ignore") + b"\0" + new_value.encode("latin-1", errors="ignore")
                chunk_bytes = make_png_chunk(b"tEXt", new_data)
                out.insert(iend_idx - 4, chunk_bytes)
        with open(image_path, "wb") as f:
            f.write(out)
        return True
    except Exception as e:
        print(f"Error PNG modify: {e}")
        return False

def modify_png_exif(image_path: str, tag_name: str, new_val_str: str | None = None) -> bool:
    try:
        with open(image_path, "rb") as f:
            data = f.read()
        if data[:8] != b"\x89PNG\r\n\x1a\n":
            return False
        out = bytearray(b"\x89PNG\r\n\x1a\n")
        i = 8
        exif_chunk_data = None
        while i + 12 <= len(data):
            chunk_len  = struct.unpack(">I", data[i:i + 4])[0]
            chunk_type = data[i + 4:i + 8]
            if chunk_type == b"eXIf":
                exif_chunk_data = data[i + 8:i + 8 + chunk_len]
                break
            i += 12 + chunk_len
        exif_dict = {"0th": {}, "Exif": {}, "GPS": {}, "1st": {}, "thumbnail": None}
        if exif_chunk_data is not None:
            try:
                exif_dict = piexif.load(exif_chunk_data)
            except Exception:
                pass
        tag_loc = _find_exif_tag(tag_name)
        if not tag_loc:
            return False
        ifd, tag_id = tag_loc
        if new_val_str is None:
            if tag_id in exif_dict[ifd]:
                exif_dict[ifd].pop(tag_id)
        else:
            orig_val = exif_dict[ifd].get(tag_id)
            if isinstance(orig_val, bytes):
                exif_dict[ifd][tag_id] = new_val_str.encode("utf-8")
            elif isinstance(orig_val, tuple) and len(orig_val) == 2:
                if "/" in new_val_str:
                    num, den = new_val_str.split("/", 1)
                    exif_dict[ifd][tag_id] = (int(num.strip()), int(den.strip()))
                else:
                    exif_dict[ifd][tag_id] = (int(new_val_str), 1)
            elif isinstance(orig_val, int):
                exif_dict[ifd][tag_id] = int(new_val_str)
            else:
                exif_dict[ifd][tag_id] = new_val_str.encode("utf-8")
        new_exif_bytes = piexif.dump(exif_dict)
        i = 8
        new_chunk_written = False
        while i + 12 <= len(data):
            chunk_len  = struct.unpack(">I", data[i:i + 4])[0]
            chunk_type = data[i + 4:i + 8]
            chunk_end  = i + 12 + chunk_len
            if chunk_type == b"eXIf":
                if new_exif_bytes and (exif_dict["0th"] or exif_dict["Exif"] or exif_dict["GPS"] or exif_dict["1st"]):
                    out.extend(make_png_chunk(b"eXIf", new_exif_bytes))
                new_chunk_written = True
            else:
                if chunk_type == b"IDAT" and not new_chunk_written and new_exif_bytes:
                    out.extend(make_png_chunk(b"eXIf", new_exif_bytes))
                    new_chunk_written = True
                out.extend(data[i:chunk_end])
            i = chunk_end
        with open(image_path, "wb") as f:
            f.write(out)
        return True
    except Exception as e:
        print(f"Error PNG EXIF modify: {e}")
        return False

def modify_webp_exif(image_path: str, tag_name: str, new_val_str: str | None = None) -> bool:
    try:
        with open(image_path, "rb") as f:
            data = f.read()
        if data[:4] != b"RIFF" or data[8:12] != b"WEBP":
            return False
        chunks = []
        i = 12
        exif_chunk_idx = -1
        while i + 8 <= len(data):
            chunk_type = data[i:i + 4]
            chunk_len = struct.unpack("<I", data[i + 4:i + 8])[0]
            padded_len = chunk_len + (chunk_len % 2)
            chunk_data = data[i + 8:i + 8 + chunk_len]
            if chunk_type == b"EXIF":
                exif_chunk_idx = len(chunks)
            chunks.append((chunk_type, chunk_data))
            i += 8 + padded_len
        exif_dict = {"0th": {}, "Exif": {}, "GPS": {}, "1st": {}, "thumbnail": None}
        if exif_chunk_idx != -1:
            try:
                exif_dict = piexif.load(chunks[exif_chunk_idx][1])
            except Exception:
                pass
        tag_loc = _find_exif_tag(tag_name)
        if not tag_loc:
            return False
        ifd, tag_id = tag_loc
        if new_val_str is None:
            if tag_id in exif_dict[ifd]:
                exif_dict[ifd].pop(tag_id)
        else:
            orig_val = exif_dict[ifd].get(tag_id)
            if isinstance(orig_val, bytes):
                exif_dict[ifd][tag_id] = new_val_str.encode("utf-8")
            elif isinstance(orig_val, tuple) and len(orig_val) == 2:
                if "/" in new_val_str:
                    num, den = new_val_str.split("/", 1)
                    exif_dict[ifd][tag_id] = (int(num.strip()), int(den.strip()))
                else:
                    exif_dict[ifd][tag_id] = (int(new_val_str), 1)
            elif isinstance(orig_val, int):
                exif_dict[ifd][tag_id] = int(new_val_str)
            else:
                exif_dict[ifd][tag_id] = new_val_str.encode("utf-8")
        new_exif_bytes = piexif.dump(exif_dict)
        if exif_chunk_idx != -1:
            if not exif_dict["0th"] and not exif_dict["Exif"] and not exif_dict["GPS"] and not exif_dict["1st"]:
                chunks.pop(exif_chunk_idx)
            else:
                chunks[exif_chunk_idx] = (b"EXIF", new_exif_bytes)
        else:
            if new_exif_bytes:
                chunks.append((b"EXIF", new_exif_bytes))
        out_body = bytearray()
        for c_type, c_data in chunks:
            out_body.extend(c_type)
            c_len = len(c_data)
            out_body.extend(struct.pack("<I", c_len))
            out_body.extend(c_data)
            if c_len % 2 != 0:
                out_body.extend(b"\x00")
        out_header = b"RIFF" + struct.pack("<I", len(out_body) + 4) + b"WEBP"
        with open(image_path, "wb") as f:
            f.write(out_header + out_body)
        return True
    except Exception as e:
        print(f"Error WebP EXIF modify: {e}")
        return False

def edit_image_metadata(image_path: str, tag_name: str, new_val_str: str | None = None) -> bool:
    """
    Modifies or deletes a specific metadata tag in the image file losslessly.
    If new_val_str is None, the tag is deleted.
    """
    ext = Path(image_path).suffix.lower()
    if ext in (".jpg", ".jpeg"):
        if new_val_str is None:
            return delete_exif_tag(image_path, tag_name)
        else:
            return update_exif_tag(image_path, tag_name, new_val_str)
    elif ext == ".png":
        if _find_exif_tag(tag_name) is not None:
            return modify_png_exif(image_path, tag_name, new_val_str)
        else:
            return modify_png_chunk(image_path, tag_name, new_val_str)
    elif ext == ".webp":
        return modify_webp_exif(image_path, tag_name, new_val_str)
    return False


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
    ext = Path(image_path).suffix.lower()
    if ext in (".mp4", ".mov", ".mkv", ".avi", ".m4v"):
        return scan_video_metadata(image_path)

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
    # Fail closed: a file we cannot read must never receive the Verified Clean seal
    if not os.path.isfile(image_path):
        return (False, ["File not found — nothing to verify"])

    ext = Path(image_path).suffix.lower()
    if ext in (".mp4", ".mov", ".mkv", ".avi", ".m4v"):
        residuals: list[str] = []
        try:
            meta = scan_video_metadata(image_path)
            if "Creation Date" in meta:
                residuals.append("Timestamps")
            if "GPS Location" in meta:
                residuals.append("GPS Coordinates")
            for key in ("Title", "Artist", "Comment", "Encoding Tool"):
                if key in meta:
                    residuals.append(f"Metadata Tag: {key}")
        except Exception as exc:
            residuals.append(f"Verification failed: {exc}")
        return (len(residuals) == 0, residuals)

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
    except Exception as exc:
        residuals.append(f"Verification failed: {exc}")

    return (len(residuals) == 0, residuals)


def clean_lossless(src: str, dst: str, preset: str = "paranoia") -> tuple[bool, list[str]]:
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
        if ext in (".mp4", ".mov", ".mkv", ".avi", ".m4v"):
            removed = VideoCleaner.clean(src, dst, preset)

        elif ext in (".jpg", ".jpeg"):
            removed = JpegCleaner.clean(src, dst, preset)

        elif ext == ".png":
            removed = PngCleaner.clean(src, dst, preset)

        elif ext == ".webp":
            img = Image.open(src)
            info = img.info or {}
            
            exif_bytes = info.get("exif", b"")
            icc_profile = info.get("icc_profile", b"")
            xmp_bytes = info.get("xmp", b"")
            
            save_kw: dict = {"format": "WEBP", "exif": b"", "icc_profile": b"", "xmp": b""}
            
            if preset == "paranoia":
                if exif_bytes: removed.append("EXIF data")
                if icc_profile: removed.append("ICC color profile")
                if xmp_bytes: removed.append("XMP metadata")
                c2pa_present, _ = detect_c2pa(src)
                if c2pa_present: removed.append("C2PA / Content Credentials")
                
            elif preset == "photographer":
                if icc_profile:
                    save_kw["icc_profile"] = icc_profile
                if xmp_bytes:
                    removed.append("XMP metadata")
                c2pa_present, _ = detect_c2pa(src)
                if c2pa_present:
                    removed.append("C2PA / Content Credentials")
                if exif_bytes:
                    try:
                        exif_dict = piexif.load(exif_bytes)
                        if "GPS" in exif_dict:
                            exif_dict["GPS"] = {}
                        if "0th" in exif_dict:
                            exif_dict["0th"].pop(315, None)      # Artist
                            exif_dict["0th"].pop(33432, None)    # Copyright
                        if "Exif" in exif_dict:
                            exif_dict["Exif"].pop(37500, None)   # MakerNote
                            exif_dict["Exif"].pop(42033, None)   # BodySerialNumber
                            exif_dict["Exif"].pop(42032, None)   # CameraOwnerName
                        sanitized_exif = piexif.dump(exif_dict)
                        save_kw["exif"] = sanitized_exif
                        removed.append("GPS & personal tags removed (EXIF preserved)")
                    except Exception:
                        removed.append("EXIF data")  # Fallback to stripping
                
            elif preset == "social":
                if icc_profile:
                    save_kw["icc_profile"] = icc_profile
                if exif_bytes:
                    removed.append("EXIF data")
                if xmp_bytes:
                    removed.append("XMP metadata")
                c2pa_present, _ = detect_c2pa(src)
                if c2pa_present:
                    removed.append("C2PA / Content Credentials")
            
            # Detect if the source WEBP was lossless
            is_lossless = bool(info.get("lossless", False))
            if is_lossless:
                save_kw["lossless"] = True
            else:
                save_kw["quality"] = 100  # minimise re-encode degradation

            img.save(dst, **save_kw)
            img.close()

        elif ext == ".gif":
            img = Image.open(src)
            info = img.info or {}
            if "comment" in info:
                removed.append("Comment")
            
            if getattr(img, "is_animated", False):
                frames = []
                durations = []
                disposals = []
                for frame_idx in range(img.n_frames):
                    img.seek(frame_idx)
                    frames.append(img.copy())
                    durations.append(img.info.get("duration", 100))
                    disposals.append(img.info.get("disposal", 2))
                
                frames[0].save(
                    dst,
                    format="GIF",
                    save_all=True,
                    append_images=frames[1:],
                    loop=info.get("loop", 0),
                    duration=durations,
                    disposal=disposals
                )
            else:
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

        if not os.path.isfile(dst):
            return False, ["Error: cleaned file was not written"]

        if not removed:
            removed.append("No metadata found — file was already clean")

        return True, removed

    except Exception as exc:
        return False, [f"Error: {exc}"]


def scan_native_mp4_metadata(path: str) -> dict[str, str]:
    meta = {}
    try:
        import datetime
        import struct
        with open(path, "rb") as f:
            f.seek(0, 2)
            file_size = f.tell()
            f.seek(0)
            
            def find_mvhd(start_offset, end_offset):
                f.seek(start_offset)
                while f.tell() < end_offset:
                    offset = f.tell()
                    header = f.read(8)
                    if not header or len(header) < 8:
                        break
                    box_size, box_type = struct.unpack(">I4s", header)
                    header_len = 8
                    if box_size == 1:
                        ext = f.read(8)
                        box_size = struct.unpack(">Q", ext)[0]
                        header_len = 16
                    if box_size <= 0:
                        break
                        
                    if box_type == b"mvhd":
                        f.seek(offset)
                        return f.read(box_size)
                    elif box_type in (b"moov", b"trak", b"mdia", b"minf", b"stbl"):
                        res = find_mvhd(offset + header_len, offset + box_size)
                        if res:
                            return res
                    f.seek(offset + box_size)
                return None

            mvhd_data = find_mvhd(0, file_size)
            if mvhd_data:
                header_len = 8
                box_size = struct.unpack_from(">I", mvhd_data, 0)[0]
                if box_size == 1:
                    header_len = 16
                
                version = mvhd_data[header_len]
                if version == 0:
                    creation_time = struct.unpack_from(">I", mvhd_data, header_len + 4)[0]
                    modification_time = struct.unpack_from(">I", mvhd_data, header_len + 8)[0]
                    time_scale = struct.unpack_from(">I", mvhd_data, header_len + 12)[0]
                    duration = struct.unpack_from(">I", mvhd_data, header_len + 16)[0]
                else:
                    creation_time = struct.unpack_from(">Q", mvhd_data, header_len + 4)[0]
                    modification_time = struct.unpack_from(">Q", mvhd_data, header_len + 12)[0]
                    time_scale = struct.unpack_from(">I", mvhd_data, header_len + 20)[0]
                    duration = struct.unpack_from(">Q", mvhd_data, header_len + 24)[0]
                
                if creation_time > 0:
                    try:
                        base = datetime.datetime(1904, 1, 1, tzinfo=datetime.timezone.utc)
                        dt = base + datetime.timedelta(seconds=creation_time)
                        meta["Creation Date"] = dt.strftime("%Y-%m-%d %H:%M:%S UTC")
                    except Exception:
                        meta["Creation Date"] = f"Raw: {creation_time}"
                
                if duration > 0 and time_scale > 0:
                    dur_secs = duration / time_scale
                    mins = int(dur_secs // 60)
                    secs = dur_secs % 60
                    meta["Duration"] = f"{mins}m {secs:.2f}s" if mins > 0 else f"{dur_secs:.2f}s"
                    
            def find_udta_tags(start_offset, end_offset):
                f.seek(start_offset)
                while f.tell() < end_offset:
                    offset = f.tell()
                    header = f.read(8)
                    if not header or len(header) < 8:
                        break
                    box_size, box_type = struct.unpack(">I4s", header)
                    header_len = 8
                    if box_size == 1:
                        ext = f.read(8)
                        box_size = struct.unpack(">Q", ext)[0]
                        header_len = 16
                    if box_size <= 0:
                        break
                        
                    if box_type == b"udta":
                        f.seek(offset)
                        udta_bytes = f.read(box_size)
                        udta_payload_start = header_len
                        while udta_payload_start + 8 < len(udta_bytes):
                            sub_size = struct.unpack_from(">I", udta_bytes, udta_payload_start)[0]
                            if sub_size <= 0 or udta_payload_start + sub_size > len(udta_bytes):
                                break
                            sub_type = udta_bytes[udta_payload_start+4:udta_payload_start+8]
                            if sub_type.startswith(b"\xa9") or sub_type in (b"xyz", b"meta"):
                                tag_name = sub_type.decode("latin1")
                                payload = udta_bytes[udta_payload_start+8:udta_payload_start+sub_size]
                                data_idx = payload.find(b"data")
                                if data_idx != -1 and data_idx + 12 < len(payload):
                                    text_bytes = payload[data_idx+12:]
                                    try:
                                        text = text_bytes.decode("utf-8", errors="ignore").strip("\x00\r\n\t ")
                                    except Exception:
                                        text = ""
                                else:
                                    try:
                                        text = payload.decode("utf-8", errors="ignore").strip("\x00\r\n\t ")
                                        text = "".join(c for c in text if c.isprintable())
                                    except Exception:
                                        text = ""

                                if text:
                                    friendly_names = {
                                        "\xa9nam": "Title",
                                        "nam": "Title",
                                        "\xa9ART": "Artist",
                                        "art": "Artist",
                                        "\xa9alb": "Album",
                                        "alb": "Album",
                                        "\xa9day": "Year",
                                        "day": "Year",
                                        "\xa9too": "Encoding Tool",
                                        "too": "Encoding Tool",
                                        "\xa9cmt": "Comment",
                                        "cmt": "Comment",
                                        "\xa9xyz": "GPS Location",
                                        "xyz": "GPS Location"
                                    }
                                    friendly = friendly_names.get(tag_name)
                                    if not friendly and tag_name.startswith("\xa9"):
                                        friendly = friendly_names.get(tag_name[1:])
                                    if not friendly:
                                        friendly = friendly_names.get(tag_name.lower())
                                        
                                    if friendly:
                                        meta[friendly] = text
                                    else:
                                        meta[tag_name.upper()] = text
                            udta_payload_start += sub_size
                        break
                    elif box_type in (b"moov", b"meta"):
                        find_udta_tags(offset + header_len, offset + box_size)
                    f.seek(offset + box_size)
            
            find_udta_tags(0, file_size)
    except Exception as e:
        print(f"[siftool] Native video scan error: {e}")
        
    return meta


def scan_video_metadata(path: str) -> dict[str, str]:
    meta: dict[str, str] = {}
    ext = Path(path).suffix.lower()
    
    if VideoCleaner.detect_ffmpeg():
        try:
            import subprocess
            import json
            cmd = ["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", path]
            result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, creationflags=0x08000000)
            if result.returncode == 0:
                data = json.loads(result.stdout)
                
                fmt = data.get("format", {})
                meta["Format"] = fmt.get("format_long_name", ext[1:].upper())
                
                v_streams = [s for s in data.get("streams", []) if s.get("codec_type") == "video"]
                a_streams = [s for s in data.get("streams", []) if s.get("codec_type") == "audio"]
                
                if v_streams:
                    v = v_streams[0]
                    meta["Video Codec"] = v.get("codec_name", "Unknown").upper()
                    meta["Resolution"] = f"{v.get('width')} \u00d7 {v.get('height')} px"
                    if "r_frame_rate" in v:
                        fps_parts = v["r_frame_rate"].split("/")
                        if len(fps_parts) == 2 and float(fps_parts[1]) > 0:
                            fps = float(fps_parts[0]) / float(fps_parts[1])
                            meta["Frame Rate"] = f"{fps:.2f} fps"
                            
                if a_streams:
                    meta["Audio Codec"] = a_streams[0].get("codec_name", "Unknown").upper()
                    
                dur_str = fmt.get("duration")
                if dur_str:
                    try:
                        dur_secs = float(dur_str)
                        mins = int(dur_secs // 60)
                        secs = dur_secs % 60
                        meta["Duration"] = f"{mins}m {secs:.2f}s" if mins > 0 else f"{dur_secs:.2f}s"
                    except Exception:
                        meta["Duration"] = f"{dur_str} s"
                        
                tags = fmt.get("tags", {})
                for s in data.get("streams", []):
                    tags.update(s.get("tags", {}))
                    
                friendly = {
                    "creation_time": "Creation Date",
                    "location": "GPS Location",
                    "location-eng": "GPS Location",
                    "encoder": "Encoding Tool",
                    "title": "Title",
                    "artist": "Artist",
                    "comment": "Comment"
                }
                for k, v in tags.items():
                    k_lower = k.lower()
                    if k_lower in friendly:
                        meta[friendly[k_lower]] = v
                    elif k_lower not in ("compatible_brands", "major_brand", "minor_version", "handler_name"):
                        meta[k.upper()] = v
                return meta
        except Exception as e:
            print(f"[siftool] ffprobe scan failed: {e}. Falling back to native...")

    if ext in (".mp4", ".mov", ".m4v"):
        meta.update(scan_native_mp4_metadata(path))
        meta["Format"] = ext[1:].upper() + " Video"
    else:
        meta["Format"] = ext[1:].upper() + " Video"
        meta["Duration"] = "Unknown (Install FFmpeg for details)"
        
    return meta


class VideoCleaner:
    @classmethod
    def clean(cls, src: str, dst: str, preset: str = "paranoia") -> list[str]:
        if cls.detect_ffmpeg():
            try:
                return cls.clean_with_ffmpeg(src, dst)
            except Exception as e:
                print(f"[siftool] FFmpeg cleaning failed: {e}. Falling back to native...")
                
        ext = Path(src).suffix.lower()
        if ext in (".mp4", ".mov", ".m4v"):
            return cls.clean_native_mp4(src, dst)
            
        raise Exception("FFmpeg is required to clean this video format. Please install FFmpeg.")

    @staticmethod
    def detect_ffmpeg() -> bool:
        import shutil
        return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None

    @staticmethod
    def clean_with_ffmpeg(src: str, dst: str) -> list[str]:
        import subprocess
        cmd = ["ffmpeg", "-i", src, "-map_metadata", "-1", "-c", "copy", "-y", dst]
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, creationflags=0x08000000)
        if result.returncode != 0:
            raise Exception(f"FFmpeg error: {result.stderr}")
        return ["All Metadata Streams", "GPS Coordinates", "Timestamps", "Camera profile"]

    @staticmethod
    def clean_native_mp4(src: str, dst: str) -> list[str]:
        import struct
        
        def sanitize_container_bytes(data: bytearray, start: int, end: int) -> None:
            i = start
            while i + 8 <= end:
                box_size = struct.unpack_from(">I", data, i)[0]
                box_type = data[i+4:i+8]
                
                header_len = 8
                real_size = box_size
                if box_size == 1:
                    real_size = struct.unpack_from(">Q", data, i + 8)[0]
                    header_len = 16
                    
                if real_size <= 0:
                    break
                    
                if box_type in (b"moov", b"trak", b"mdia", b"minf", b"stbl"):
                    sanitize_container_bytes(data, i + header_len, i + real_size)
                    
                elif box_type in (b"udta", b"meta"):
                    data[i+4:i+8] = b"free"
                    
                elif box_type == b"mvhd":
                    version = data[i + header_len]
                    if version == 0:
                        struct.pack_into(">I", data, i + header_len + 4, 0)
                        struct.pack_into(">I", data, i + header_len + 8, 0)
                    elif version == 1:
                        struct.pack_into(">Q", data, i + header_len + 4, 0)
                        struct.pack_into(">Q", data, i + header_len + 12, 0)
                        
                elif box_type == b"tkhd":
                    version = data[i + header_len]
                    if version == 0:
                        struct.pack_into(">I", data, i + header_len + 4, 0)
                        struct.pack_into(">I", data, i + header_len + 8, 0)
                    elif version == 1:
                        struct.pack_into(">Q", data, i + header_len + 4, 0)
                        struct.pack_into(">Q", data, i + header_len + 12, 0)
                        
                elif box_type == b"mdhd":
                    version = data[i + header_len]
                    if version == 0:
                        struct.pack_into(">I", data, i + header_len + 4, 0)
                        struct.pack_into(">I", data, i + header_len + 8, 0)
                    elif version == 1:
                        struct.pack_into(">Q", data, i + header_len + 4, 0)
                        struct.pack_into(">Q", data, i + header_len + 12, 0)
                        
                i += real_size

        removed = []
        with open(src, "rb") as f_in, open(dst, "wb") as f_out:
            f_in.seek(0, 2)
            file_size = f_in.tell()
            f_in.seek(0)
            
            while f_in.tell() < file_size:
                header_offset = f_in.tell()
                header = f_in.read(8)
                if not header or len(header) < 8:
                    break
                    
                box_size, box_type = struct.unpack(">I4s", header)
                header_len = 8
                if box_size == 1:
                    ext_header = f_in.read(8)
                    box_size = struct.unpack(">Q", ext_header)[0]
                    header_len = 16
                    
                if box_size <= 0:
                    break
                    
                if box_type == b"moov":
                    f_in.seek(header_offset)
                    moov_data = bytearray(f_in.read(box_size))
                    sanitize_container_bytes(moov_data, 0, len(moov_data))
                    f_out.write(moov_data)
                    removed.append("Metadata Atoms (moov/udta/meta)")
                    removed.append("Creation/Modification Timestamps")
                elif box_type == b"mdat":
                    f_out.write(header)
                    if header_len == 16:
                        f_out.write(ext_header)
                    
                    bytes_to_copy = box_size - header_len
                    chunk_size = 64 * 1024
                    while bytes_to_copy > 0:
                        read_size = min(bytes_to_copy, chunk_size)
                        chunk = f_in.read(read_size)
                        if not chunk:
                            break
                        f_out.write(chunk)
                        bytes_to_copy -= len(chunk)
                else:
                    f_in.seek(header_offset)
                    other_data = f_in.read(box_size)
                    if box_type in (b"udta", b"meta"):
                        other_data_ba = bytearray(other_data)
                        other_data_ba[4:8] = b"free"
                        other_data = bytes(other_data_ba)
                        removed.append("Metadata Atoms (udta/meta)")
                    f_out.write(other_data)
                    
        return removed


class ScrollableFrame(tk.Frame):
    def __init__(self, parent, bg):
        super().__init__(parent, bg=bg)
        self.canvas = tk.Canvas(self, bg=bg, bd=0, highlightthickness=0)
        self.scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.scrollable_frame = tk.Frame(self.canvas, bg=bg)
        
        self.scrollable_frame.bind(
            "<Configure>",
            lambda e: self.canvas.configure(
                scrollregion=self.canvas.bbox("all")
            )
        )
        
        self.canvas_window = self.canvas.create_window((0, 0), window=self.scrollable_frame, anchor="nw")
        
        self.canvas.bind('<Configure>', lambda event: self.canvas.itemconfig(self.canvas_window, width=event.width))
        
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        
        self.scrollbar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        
        # Smooth mousewheel binding only when hovered
        def _on_mousewheel(event):
            self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
            
        def _bind_mouse(event):
            self.canvas.bind_all("<MouseWheel>", _on_mousewheel)
            
        def _unbind_mouse(event):
            self.canvas.unbind_all("<MouseWheel>")
            
        self.canvas.bind("<Enter>", _bind_mouse)
        self.canvas.bind("<Leave>", _unbind_mouse)


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
        self.root.geometry("960x720")
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

        # Setup styles before building UI to ensure they apply to all widgets properly
        self._setup_styles()

        self._build_ui()
        self._poll()
        self._start_socket_server()

        # Responsive window resizing
        self.root.bind("<Configure>", self._on_window_resize)

        # Check initial arguments (if files/folders are passed)
        import sys
        is_cli_cmd = False
        if len(sys.argv) > 1:
            if sys.argv[1] in ("scan", "clean", "verify", "register-context", "unregister-context"):
                is_cli_cmd = True

        if len(sys.argv) > 1 and not is_cli_cmd:
            import os
            abs_paths = [os.path.abspath(p) for p in sys.argv[1:]]
            self.root.after(100, lambda: self._enqueue(abs_paths))

        # Auto-update context menu registration if needed (Windows only)
        if sys.platform == "win32":
            try:
                if is_context_menu_registered():
                    register_context_menu()
            except Exception:
                pass

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
        hdr = tk.Frame(self.root, bg=SURFACE, pady=6)
        hdr.pack(fill="x")

        center_frame = tk.Frame(hdr, bg=SURFACE)
        center_frame.pack()

        row = tk.Frame(center_frame, bg=SURFACE)
        row.pack()

        tk.Label(row, text="✦", font=(FONT, 18, "bold"),
                 bg=SURFACE, fg=ACCENT_H).pack(side="left")
        tk.Label(row, text=" Siftool", font=(FONT, 16, "bold"),
                 bg=SURFACE, fg=TEXT).pack(side="left")
        tk.Label(row, text=f"  {VERSION}", font=(FONT, 9),
                 bg=SURFACE, fg=TEXT_M).pack(side="left", pady=(6, 0))

        tk.Label(center_frame,
                 text="Lossless metadata removal · 100 % offline · Verified Clean",
                 font=(FONT, 8), bg=SURFACE, fg=TEXT_M).pack(pady=(2, 0))

        # Checkbox for Explorer Context Menu in Windows
        import sys
        if sys.platform == "win32":
            self.context_menu_var = tk.BooleanVar()
            try:
                self.context_menu_var.set(is_context_menu_registered())
            except Exception:
                self.context_menu_var.set(False)
            
            chk = tk.Checkbutton(
                hdr, text="Integrate with Windows Explorer",
                variable=self.context_menu_var,
                command=self._toggle_context_menu,
                bg=SURFACE, fg=TEXT_M, selectcolor=BG,
                activebackground=SURFACE, activeforeground=TEXT,
                font=(FONT, 8), bd=0, highlightthickness=0
            )
            chk.place(relx=1.0, rely=0.5, anchor="e", x=-20)

    def _build_dropzone(self) -> None:
        outer = tk.Frame(self.root, bg=BG, padx=20, pady=0)
        outer.pack(fill="x", pady=(6, 2))

        self.drop_frame = tk.Frame(outer, bg=CARD, relief="flat", bd=0,
                                   pady=8, padx=16, cursor="hand2",
                                   highlightthickness=1, highlightbackground=CARD2)
        self.drop_frame.pack(fill="x")
        self.drop_frame.bind("<Button-1>", lambda _: self._add_files())

        arrow_lbl = tk.Label(self.drop_frame, text="✦  ⬇  ✦", font=(FONT, 16, "bold"),
                             bg=CARD, fg=ACCENT, cursor="hand2")
        arrow_lbl.pack()
        arrow_lbl.bind("<Button-1>", lambda _: self._add_files())

        hint = "Drop images, videos or folders here"
        self.drop_hint = tk.Label(self.drop_frame, text=hint,
                                   font=(FONT, 11, "bold"), bg=CARD, fg=TEXT, cursor="hand2")
        self.drop_hint.pack(pady=(2, 1))
        self.drop_hint.bind("<Button-1>", lambda _: self._add_files())

        format_lbl = tk.Label(self.drop_frame,
                              text="✦ JPEG · PNG · WEBP · MP4 · MOV · GIF · TIFF ✦",
                              font=(FONT, 8), bg=CARD, fg=TEXT_M, cursor="hand2")
        format_lbl.pack()
        format_lbl.bind("<Button-1>", lambda _: self._add_files())

        # Portal/gravity well glow effects on hover
        def on_drop_enter(e):
            self.drop_frame.config(bg=CARD2, highlightbackground=ACCENT_H)
            for widget in self.drop_frame.winfo_children():
                try:
                    widget.config(bg=CARD2)
                except Exception:
                    pass
                if widget == arrow_lbl:
                    widget.config(fg=ACCENT_H)

        def on_drop_leave(e):
            x, y = self.drop_frame.winfo_pointerxy()
            gx = self.drop_frame.winfo_rootx()
            gy = self.drop_frame.winfo_rooty()
            gw = self.drop_frame.winfo_width()
            gh = self.drop_frame.winfo_height()
            if gx <= x <= gx + gw and gy <= y <= gy + gh:
                return  # Still inside
            self.drop_frame.config(bg=CARD, highlightbackground=CARD2)
            for widget in self.drop_frame.winfo_children():
                try:
                    widget.config(bg=CARD)
                except Exception:
                    pass
                if widget == arrow_lbl:
                    widget.config(fg=ACCENT)

        for w in (self.drop_frame, arrow_lbl, self.drop_hint, format_lbl):
            w.bind("<Enter>", on_drop_enter, add="+")
            w.bind("<Leave>", on_drop_leave, add="+")

        if self._dnd_available:
            from tkinterdnd2 import DND_FILES
            self.drop_frame.drop_target_register(DND_FILES)
            self.drop_frame.dnd_bind("<<Drop>>", self._on_drop)

        # Button row
        btn_row = tk.Frame(self.root, bg=BG)
        btn_row.pack(pady=(0, 2))

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

        self.left_pane = tk.Frame(main_split, bg=BG)
        # Packed via _apply_layout

        cols = ("name", "status", "details")
        self.tree = ttk.Treeview(self.left_pane, columns=cols, show="headings",
                                  selectmode="browse", height=13)
        self.tree.heading("name",    text="File")
        self.tree.heading("status",  text="Status")
        self.tree.heading("details", text="Details")
        self.tree.column("name",    width=200, anchor="w", stretch=True)
        self.tree.column("status",  width=120, anchor="center", stretch=False)
        self.tree.column("details", width=200, anchor="w",      stretch=True)

        sb = ttk.Scrollbar(self.left_pane, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        # Right pane: Details Panel (width=300)
        self.right_pane = tk.Frame(main_split, bg=CARD, width=300, bd=1, relief="flat")
        
        # Apply layout dynamically (split or stacked) based on window geometry
        self._current_layout_mode = None
        self._apply_layout("split")

        self._build_details_placeholder()

        # Configure Treeview tags locally
        self.tree.tag_configure("waiting",    foreground=TEXT_M)
        self.tree.tag_configure("processing", foreground=WARNING)
        self.tree.tag_configure("done",       foreground=SEAL)
        self.tree.tag_configure("residual",   foreground=WARNING)
        self.tree.tag_configure("error",      foreground=DANGER)

        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)

    def _on_window_resize(self, event) -> None:
        if event.widget == self.root:
            w = event.width
            # Check threshold: width >= 720 is split (desktop), else stacked (9:16 mobile)
            mode = "split" if w >= 720 else "stacked"
            self._apply_layout(mode)

    def _apply_layout(self, mode: str) -> None:
        if getattr(self, "_current_layout_mode", None) == mode:
            return  # Already in this layout
        
        self._current_layout_mode = mode
        
        # Safely unpack to re-layout
        try:
            self.left_pane.pack_forget()
            self.right_pane.pack_forget()
        except Exception:
            pass

        if mode == "split":
            # Desktop mode: side-by-side
            try:
                self.tree.config(height=10)
            except Exception:
                pass
            self.left_pane.pack(side="left", fill="both", expand=True)
            self.right_pane.config(width=320) # Wider sidebar
            self.right_pane.pack_propagate(False)
            self.right_pane.pack(side="right", fill="both", padx=(14, 0))
            
            # Adjust column widths for desktop view
            try:
                self.tree.column("name",    width=240, stretch=True)
                self.tree.column("status",  width=120, stretch=False)
                self.tree.column("details", width=160, stretch=True)
            except Exception:
                pass
        else:
            # Mobile Portrait (9:16) stacked mode: vertical
            try:
                self.tree.config(height=5) # Smaller file queue to prioritize inspector height
            except Exception:
                pass
            self.left_pane.pack(side="top", fill="both", expand=False)
            self.right_pane.pack_propagate(True)
            self.right_pane.pack(side="bottom", fill="both", expand=True, pady=(14, 0))
            
            # Adjust column widths for narrow mobile view
            try:
                self.tree.column("name",    width=140, stretch=True)
                self.tree.column("status",  width=95,  stretch=False)
                self.tree.column("details", width=105, stretch=True)
            except Exception:
                pass

    def _build_details_placeholder(self) -> None:
        for widget in self.right_pane.winfo_children():
            widget.destroy()
            
        container = tk.Frame(self.right_pane, bg=CARD)
        container.pack(fill="both", expand=True, pady=60)
        
        tk.Label(container, text="✦", font=(FONT, 32), bg=CARD, fg=TEXT_M).pack()
        tk.Label(container, text="Metadata Inspector", font=(FONT, 14, "bold"), bg=CARD, fg=TEXT).pack(pady=10)
        tk.Label(container, text="Select any file in the queue\nto inspect its EXIF tags & C2PA state.", 
                 font=(FONT, 10), bg=CARD, fg=TEXT_M, justify="center").pack()

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
            
        self.right_pane_active_path = path

        for widget in self.right_pane.winfo_children():
            widget.destroy()
            
        # Create scrollable frame for the entire details pane
        scroll_frame = ScrollableFrame(self.right_pane, bg=CARD)
        scroll_frame.pack(fill="both", expand=True, padx=0, pady=0)
        content_pane = scroll_frame.scrollable_frame

        # Determine wrapping widths based on current layout mode
        is_split = getattr(self, "_current_layout_mode", "split") == "split"
        name_wrap = 280 if is_split else 600
        banner_wrap = 260 if is_split else 580

        # Title
        hdr = tk.Frame(content_pane, bg=CARD, pady=10)
        hdr.pack(fill="x", padx=12)
        
        lbl_name = tk.Label(hdr, text=row.name, font=(FONT, 12, "bold"), bg=CARD, fg=TEXT, anchor="w", justify="left", wraplength=name_wrap)
        lbl_name.pack(fill="x")
        
        ext = Path(path).suffix.lower()
        if ext in (".mp4", ".mov", ".mkv", ".avi", ".m4v"):
            ext_desc = ext[1:].upper() + " Video"
        else:
            ext_desc = ext[1:].upper() + " Image"
        tk.Label(hdr, text=ext_desc, font=(FONT, 10), bg=CARD, fg=TEXT_M, anchor="w").pack(fill="x")

        # C2PA Check
        c2pa_present, c2pa_desc = detect_c2pa(path)
        
        if c2pa_present:
            # High-impact warning banner
            c2pa_banner = tk.Frame(content_pane, bg="#1a0b16", bd=1, relief="solid", highlightbackground=DANGER, highlightthickness=1, padx=10, pady=8)
            c2pa_banner.pack(fill="x", padx=12, pady=6)
            tk.Label(c2pa_banner, text="⚠  Content Credentials (C2PA)", font=(FONT, 10, "bold"), bg="#1a0b16", fg=DANGER, anchor="w").pack(fill="x")
            
            lbl_warning = tk.Label(c2pa_banner, text="This image contains cryptographically signed metadata. It may leak: author identity, AI tool origin, exact timestamps, and edit history.\n\nSiftool will remove this on Sift.", 
                     font=(FONT, 10), bg="#1a0b16", fg=TEXT, justify="left", anchor="w", wraplength=banner_wrap)
            lbl_warning.pack(fill="x", pady=(4, 0))
            
        elif row.status == FileRow.DONE and row.verified:
            # Verified Clean seal banner
            seal_banner = tk.Frame(content_pane, bg="#0b171a", bd=1, relief="solid", highlightbackground=SEAL, highlightthickness=1, padx=10, pady=8)
            seal_banner.pack(fill="x", padx=12, pady=6)
            tk.Label(seal_banner, text="✓  Verified Clean Seal", font=(FONT, 10, "bold"), bg="#0b171a", fg=SEAL, anchor="w").pack(fill="x")
            
            lbl_seal = tk.Label(seal_banner, text="All privacy-sensitive metadata, including EXIF, ICC, GPS, and C2PA, have been successfully stripped.", 
                     font=(FONT, 10), bg="#0b171a", fg=TEXT, justify="left", anchor="w", wraplength=banner_wrap)
            lbl_seal.pack(fill="x", pady=(4, 0))
        
        # Details section (removed items or residuals)
        if row.status == FileRow.DONE:
            info_frame = tk.Frame(content_pane, bg=CARD, pady=6)
            info_frame.pack(fill="x", padx=12)
            if row.removed:
                removed_lbl = tk.Label(info_frame, text=f"Removed: {len(row.removed)} type(s)", font=(FONT, 10, "bold"), bg=CARD, fg=SUCCESS, anchor="w")
                removed_lbl.pack(fill="x")
                
                # bullet list of removed
                rem_text = "\n".join(f"• {item}" for item in row.removed[:4])
                if len(row.removed) > 4:
                    rem_text += f"\n• and {len(row.removed) - 4} more..."
                tk.Label(info_frame, text=rem_text, font=(FONT, 10), bg=CARD, fg=TEXT_M, justify="left", anchor="w").pack(fill="x", pady=(2, 6))

        # Open folder button for processed files
        if row.status == FileRow.DONE and row.dst_path:
            folder_frame = tk.Frame(content_pane, bg=CARD, pady=4)
            folder_frame.pack(fill="x", padx=12, pady=4)
            btn_folder = self._btn(
                folder_frame,
                "📁 Abrir Pasta de Destino",
                lambda: self._open_file_folder(row.dst_path),
                ACCENT
            )
            btn_folder.pack(fill="x", pady=2)

        # Metadata title
        tk.Label(content_pane, text="Original Metadata Elements", font=(FONT, 10, "bold"), bg=CARD, fg=TEXT_M, anchor="w").pack(fill="x", padx=12, pady=(10, 4))

        # Metadata container frame for async population
        self.meta_container = tk.Frame(content_pane, bg=CARD)
        self.meta_container.pack(fill="both", expand=True)

        # Loading label
        loading_lbl = tk.Label(self.meta_container, text="⏳ Carregando metadados...", font=(FONT, 10, "italic"), bg=CARD, fg=TEXT_M, pady=20)
        loading_lbl.pack(fill="x", padx=12)

        # Start async loading thread
        threading.Thread(target=self._async_scan_metadata, args=(path,), daemon=True).start()

    def _async_scan_metadata(self, path: str) -> None:
        try:
            meta = scan_metadata(path)
        except Exception as e:
            print(f"[siftool] Async scan metadata error: {e}")
            meta = None
        
        self._q.put(("metadata_loaded", path, meta))

    def _async_scan_metadata_callback(self, path: str, meta: dict | None) -> None:
        # Prevent race conditions by checking active path
        if not hasattr(self, "right_pane_active_path") or path != self.right_pane_active_path:
            return
            
        # Ensure the container still exists
        if not hasattr(self, "meta_container") or not self.meta_container.winfo_exists():
            return
            
        # Clear loading label
        for widget in self.meta_container.winfo_children():
            widget.destroy()
            
        # Re-determine wrapping widths based on current layout mode
        is_split = getattr(self, "_current_layout_mode", "split") == "split"
        val_wrap = 200 if is_split else 480

        if not meta:
            empty_lbl = tk.Label(self.meta_container, text="No metadata tags found.", font=(FONT, 10, "italic"), bg=CARD, fg=TEXT_M, pady=20)
            empty_lbl.pack(fill="x", padx=12)
        else:
            for tag_name, val_str in meta.items():
                item_frame = tk.Frame(self.meta_container, bg=CARD, pady=8)
                item_frame.pack(fill="x", side="top", padx=12)
                
                # Bottom border separator
                sep = tk.Frame(self.meta_container, bg=CARD2, height=1)
                sep.pack(fill="x", side="top", pady=2, padx=12)
                
                lbl_key = tk.Label(item_frame, text=tag_name, font=(FONT, 9, "bold"), bg=CARD, fg=TEXT_M, anchor="w")
                lbl_key.pack(fill="x", side="top")
                
                val_row = tk.Frame(item_frame, bg=CARD)
                val_row.pack(fill="x", side="top", pady=(3, 0))
                
                # Actions packed first to align right
                actions = tk.Frame(val_row, bg=CARD)
                actions.pack(side="right")
                
                edit_btn = self._icon_btn(actions, "✎", lambda t=tag_name, v=val_str: self._edit_meta(path, t, v), CARD2, SUCCESS)
                edit_btn.pack(side="left", padx=2)
                
                del_btn = self._icon_btn(actions, "🗑", lambda t=tag_name: self._delete_meta(path, t), CARD2, DANGER)
                del_btn.pack(side="left", padx=2)
                
                # Value label packed second to expand left
                lbl_val = tk.Label(val_row, text=val_str, font=(FONT, 11), bg=CARD, fg=TEXT, anchor="w", justify="left", wraplength=val_wrap)
                lbl_val.pack(side="left", fill="x", expand=True)
                
                # Double click shortcuts to edit
                lbl_key.bind("<Double-1>", lambda _, t=tag_name, v=val_str: self._edit_meta(path, t, v))
                lbl_val.bind("<Double-1>", lambda _, t=tag_name, v=val_str: self._edit_meta(path, t, v))
                item_frame.bind("<Double-1>", lambda _, t=tag_name, v=val_str: self._edit_meta(path, t, v))

    def _open_file_folder(self, file_path: str) -> None:
        if not file_path or not os.path.exists(file_path):
            return
        
        file_path = os.path.abspath(file_path)
        import subprocess
        import sys
        
        try:
            if sys.platform == "win32":
                # explorer /select accepts Windows paths with backslashes
                subprocess.Popen(["explorer", "/select,", os.path.normpath(file_path)])
            elif sys.platform == "darwin":
                subprocess.Popen(["open", "-R", file_path])
            else:
                # Linux/Unix: Open containing folder using xdg-open
                parent_dir = os.path.dirname(file_path)
                subprocess.Popen(["xdg-open", parent_dir])
        except Exception as e:
            print(f"[siftool] Failed to open folder for {file_path}: {e}")


    def _edit_meta(self, image_path: str, tag_name: str, current_val: str) -> None:
        # Open edit dialog window
        dialog = tk.Toplevel(self.root)
        dialog.title(f"Edit {tag_name}")
        dialog.geometry("400x180")
        dialog.configure(bg=BG)
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.grab_set()
        
        # Center dialog on Siftool window
        x = self.root.winfo_x() + (self.root.winfo_width() // 2) - 200
        y = self.root.winfo_y() + (self.root.winfo_height() // 2) - 90
        dialog.geometry(f"+{x}+{y}")
        
        lbl = tk.Label(dialog, text=f"Edit value for '{tag_name}':", font=(FONT, 10, "bold"), bg=BG, fg=TEXT)
        lbl.pack(fill="x", padx=20, pady=(20, 10), anchor="w")
        
        entry = tk.Entry(dialog, font=(FONT, 10), bg=CARD, fg=TEXT, insertbackground=TEXT, relief="flat", bd=1, highlightthickness=1, highlightcolor=ACCENT, highlightbackground=CARD2)
        entry.pack(fill="x", padx=20, pady=(0, 20))
        entry.insert(0, current_val)
        entry.focus_set()
        
        def save_changes():
            new_val = entry.get()
            success = edit_image_metadata(image_path, tag_name, new_val)
            if success:
                row = self._find(image_path)
                if row:
                    row.status = FileRow.WAITING
                    row.error_msg = "Edited manually"
                    self._update_row_in_tree(row)
                
                dialog.destroy()
                self._on_tree_select(None)
            else:
                messagebox.showerror("Error", f"Failed to modify tag '{tag_name}'.")
        
        btn_frame = tk.Frame(dialog, bg=BG)
        btn_frame.pack(fill="x", padx=20, side="bottom", pady=15)
        
        save_btn = self._btn(btn_frame, " Save ", save_changes, ACCENT, width=10)
        save_btn.pack(side="right", padx=(10, 0))
        
        cancel_btn = self._btn(btn_frame, " Cancel ", dialog.destroy, CARD2, width=10)
        cancel_btn.pack(side="right")
        
        entry.bind("<Return>", lambda _: save_changes())

    def _delete_meta(self, image_path: str, tag_name: str) -> None:
        if not messagebox.askyesno("Confirm Delete", f"Are you sure you want to delete the tag '{tag_name}' losslessly from this image?"):
            return
            
        success = edit_image_metadata(image_path, tag_name, None)
        if success:
            row = self._find(image_path)
            if row:
                row.status = FileRow.WAITING
                row.error_msg = "Edited manually"
                self._update_row_in_tree(row)
            self._on_tree_select(None)
        else:
            messagebox.showerror("Error", f"Failed to delete tag '{tag_name}'.")

    def _update_row_in_tree(self, row: FileRow) -> None:
        if row.status == FileRow.WAITING:
            self.tree.item(row.path, values=(row.name, "⏳ Waiting", row.error_msg or "Edited manually"), tags=("waiting",))

    def _build_footer(self) -> None:
        # Status bar (bottom-most)
        self.status_var = tk.StringVar(value="Ready — add files and press Sift All.")
        tk.Label(self.root, textvariable=self.status_var,
                 bg=CARD, fg=TEXT_M, font=(FONT, 9),
                 anchor="w", padx=14, pady=4).pack(fill="x", side="bottom")

        # Progress + action button
        foot = tk.Frame(self.root, bg=SURFACE, pady=8)
        foot.pack(fill="x", side="bottom")

        inner = tk.Frame(foot, bg=SURFACE)
        inner.pack()

        # Preset selector dropdown
        self.preset_var = tk.StringVar(value="Paranoico (Max)")
        self.preset_cb = ttk.Combobox(
            inner, textvariable=self.preset_var,
            values=["Paranoico (Max)", "Fotógrafo", "Redes Sociais"],
            state="readonly", width=16, font=(FONT, 9, "bold")
        )
        self.preset_cb.pack(side="left", padx=(0, 14))

        self.progress_var = tk.DoubleVar(value=0)
        self.progress = ttk.Progressbar(inner, variable=self.progress_var,
                                         maximum=100, length=320,
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
        btn = tk.Button(parent, **kw)

        # Cosmic hover micro-interactions
        def on_enter(e):
            if btn["state"] == "normal":
                if color == ACCENT:
                    btn.config(bg=ACCENT_H)
                elif color == CARD2:
                    btn.config(bg="#25254c")
                else:
                    btn.config(bg=CARD2)

        def on_leave(e):
            if btn["state"] == "normal":
                btn.config(bg=color)

        btn.bind("<Enter>", on_enter)
        btn.bind("<Leave>", on_leave)
        return btn

    def _icon_btn(self, parent, text, cmd, bg_color, fg_color) -> tk.Button:
        btn = tk.Button(
            parent, text=text, command=cmd,
            bg=bg_color, fg=fg_color,
            font=(FONT, 10, "bold"),
            relief="flat", bd=0,
            padx=8, pady=4,
            cursor="hand2",
            activebackground=ACCENT_H,
            activeforeground=TEXT,
        )
        # Compact hover micro-interactions
        def on_enter(e):
            if btn["state"] == "normal":
                btn.config(bg="#25254c" if bg_color == CARD2 else ACCENT_H)
        def on_leave(e):
            if btn["state"] == "normal":
                btn.config(bg=bg_color)
        btn.bind("<Enter>", on_enter)
        btn.bind("<Leave>", on_leave)
        return btn

    def _setup_styles(self) -> None:
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("Treeview",
                         background=CARD, foreground=TEXT,
                         rowheight=30, fieldbackground=CARD,
                         borderwidth=0, font=(FONT, 10))
        style.configure("Treeview.Heading",
                         background=SURFACE, foreground=TEXT_M,
                         relief="flat", font=(FONT, 10, "bold"))
        style.map("Treeview", background=[("selected", "#25183a")], foreground=[("selected", "#ffffff")])
        style.map("Treeview.Heading", background=[("active", CARD2)])

        # Premium Dark Scrollbar styling
        style.configure("TScrollbar",
                        background=CARD2,
                        troughcolor=BG,
                        bordercolor=BG,
                        arrowcolor=TEXT_M,
                        relief="flat",
                        width=11,
                        arrowsize=10)
        style.map("TScrollbar",
                  background=[("active", ACCENT)])

        # Cosmic Progressbar styling
        style.configure("Horizontal.TProgressbar",
                        troughcolor=BG,
                        bordercolor=CARD2,
                        background=ACCENT,
                        lightcolor=ACCENT,
                        darkcolor=ACCENT)

        # Custom Dark Combobox styling
        style.configure("TCombobox",
                        fieldbackground=CARD,
                        background=CARD2,
                        foreground=TEXT,
                        bordercolor=CARD2,
                        arrowcolor=TEXT_M,
                        darkcolor=CARD,
                        lightcolor=CARD)
        style.map("TCombobox",
                  fieldbackground=[("readonly", CARD), ("disabled", CARD2)],
                  foreground=[("readonly", TEXT), ("disabled", TEXT_M)])

        # Option database styling for Combobox dropdown listbox
        self.root.option_add("*TCombobox*Listbox.background", CARD)
        self.root.option_add("*TCombobox*Listbox.foreground", TEXT)
        self.root.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
        self.root.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")
        self.root.option_add("*TCombobox*Listbox.font", (FONT, 10))
        self.root.option_add("*TCombobox*Listbox.relief", "flat")
        self.root.option_add("*TCombobox*Listbox.bd", "0")

    # ── File management ───────────────────────────────────────────────────────

    def _add_files(self) -> None:
        paths = filedialog.askopenfilenames(
            title="Select media files (Images & Videos)",
            filetypes=[
                ("All Supported Files", "*.jpg *.jpeg *.png *.bmp *.gif *.tiff *.tif *.webp *.mp4 *.mov *.mkv *.avi *.m4v"),
                ("Images", "*.jpg *.jpeg *.png *.bmp *.gif *.tiff *.tif *.webp"),
                ("Videos", "*.mp4 *.mov *.mkv *.avi *.m4v"),
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
        skipped_clean = 0
        for p in paths:
            path_obj = Path(p)
            if path_obj.stem.endswith("_clean"):
                skipped_clean += 1
                continue
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
        
        status_msg = ""
        if added:
            status_msg += f"{added} file(s) added to queue."
        if skipped_clean:
            if status_msg:
                status_msg += f" (Skipped {skipped_clean} already cleaned file(s))"
            else:
                status_msg = f"Skipped {skipped_clean} already cleaned file(s)."
        
        if status_msg:
            self.status_var.set(status_msg)

    def _toggle_context_menu(self) -> None:
        if self.context_menu_var.get():
            success, err = register_context_menu()
            if success:
                self.status_var.set("Successfully integrated with Windows Explorer.")
            else:
                self.status_var.set(f"Failed to integrate: {err}")
                self.context_menu_var.set(False)
        else:
            success, err = unregister_context_menu()
            if success:
                self.status_var.set("Successfully removed Windows Explorer integration.")
            else:
                self.status_var.set(f"Failed to remove integration: {err}")
                self.context_menu_var.set(True)

    def _start_socket_server(self) -> None:
        import sys
        if sys.platform == "win32":
            import threading
            t = threading.Thread(target=self._socket_listener_thread, daemon=True)
            t.start()

    def _socket_listener_thread(self) -> None:
        import socket
        
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", SINGLE_INSTANCE_PORT))
            s.listen(5)
        except Exception:
            return
            
        MAX_PAYLOAD_SIZE = 65536  # 64KB limit
        while True:
            try:
                conn, addr = s.accept()
                # Security: Verify connection comes from localhost client
                if addr[0] != "127.0.0.1":
                    conn.close()
                    continue

                data = bytearray()
                while True:
                    chunk = conn.recv(4096)
                    if not chunk:
                        break
                    data.extend(chunk)
                    # Protect against buffer overflow / memory exhaustion DoS
                    if len(data) > MAX_PAYLOAD_SIZE:
                        break
                conn.close()
                
                if len(data) <= MAX_PAYLOAD_SIZE:
                    paths = data.decode("utf-8", errors="ignore").split("\n")
                    # Basic path validation to prevent path traversal/non-existent files
                    sanitized_paths = [p.strip() for p in paths if p.strip() and os.path.exists(p.strip())]
                    if sanitized_paths:
                        self.root.after(0, lambda p=sanitized_paths: self._enqueue_from_socket(p))
            except Exception:
                pass

    def _enqueue_from_socket(self, paths: list[str]) -> None:
        paths = [p.strip() for p in paths if p.strip()]
        if paths:
            self._enqueue(paths)
            self.root.deiconify()
            self.root.lift()
            self.root.focus_force()

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

    def _get_active_preset(self) -> str:
        val = self.preset_var.get()
        if val == "Fotógrafo":
            return "photographer"
        elif val == "Redes Sociais":
            return "social"
        return "paranoia"

    def _start_batch(self) -> None:
        pending = [f for f in self.files if f.status == FileRow.WAITING]
        if not pending:
            messagebox.showinfo("Siftool", "No files waiting to be processed.")
            return

        self._running = True
        self.sift_btn.config(state="disabled")
        self.preset_cb.config(state="disabled")
        
        active_preset = self._get_active_preset()
        self.status_var.set(f"Processing {len(pending)} file(s) [{self.preset_var.get()}]…")
        self.progress_var.set(0)

        t = threading.Thread(target=self._worker_fn, args=(pending, active_preset), daemon=True)
        t.start()

    def _worker_fn(self, rows: list[FileRow], preset: str) -> None:
        """Background thread — communicates results via self._q."""
        total = len(rows)
        for idx, row in enumerate(rows):
            self._q.put(("progress", row.path, idx, total))

            p = Path(row.path)
            dst = str(p.parent / (p.stem + "_clean" + p.suffix))

            ok, items = clean_lossless(row.path, dst, preset)
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

                if kind == "metadata_loaded":
                    _, path, meta = msg
                    self._async_scan_metadata_callback(path, meta)

                elif kind == "progress":
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
                    self.preset_cb.config(state="readonly")
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
    clean_p.add_argument("--preset", choices=["paranoia", "photographer", "social"], default="paranoia", help="Cleaning preset: paranoia (default), photographer, social")
    clean_p.add_argument("--json", action="store_true", help="Output results in JSON format")

    # verify
    verify_p = subparsers.add_parser("verify", help="Verify if files are metadata-clean")
    verify_p.add_argument("files", nargs="+", help="File paths to verify")
    verify_p.add_argument("--json", action="store_true", help="Output results in JSON format")

    # register-context
    subparsers.add_parser("register-context", help="Register Siftool with Windows Explorer context menu")

    # unregister-context
    subparsers.add_parser("unregister-context", help="Remove Siftool from Windows Explorer context menu")

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
            ok, items = clean_lossless(str(p), str(dst), args.preset)
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

    elif args.command == "register-context":
        success, err = register_context_menu()
        if success:
            print("Successfully registered Siftool Windows Explorer integration.")
        else:
            print(f"Error registering Siftool integration: {err}")
            sys.exit(1)

    elif args.command == "unregister-context":
        success, err = unregister_context_menu()
        if success:
            print("Successfully removed Siftool Windows Explorer integration.")
        else:
            print(f"Error removing Siftool integration: {err}")
            sys.exit(1)



# ─────────────────────────────────────────────────────────────────────────────
# WINDOWS PLATFORM INTEGRATION
# ─────────────────────────────────────────────────────────────────────────────

SINGLE_INSTANCE_PORT = 49152

def send_to_existing_instance(paths: list[str]) -> bool:
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.5)
        s.connect(("127.0.0.1", SINGLE_INSTANCE_PORT))
        s.sendall("\n".join(paths).encode("utf-8"))
        s.close()
        return True
    except Exception:
        return False

def _delete_key_recursive(key, path):
    import winreg
    try:
        h_key = winreg.OpenKey(key, path, 0, winreg.KEY_ALL_ACCESS)
    except FileNotFoundError:
        return
    
    subkeys = []
    try:
        idx = 0
        while True:
            subkeys.append(winreg.EnumKey(h_key, idx))
            idx += 1
    except OSError:
        pass
    
    winreg.CloseKey(h_key)
    
    for subkey in subkeys:
        _delete_key_recursive(key, f"{path}\\{subkey}")
        
    try:
        winreg.DeleteKey(key, path)
    except FileNotFoundError:
        pass

def register_context_menu() -> tuple[bool, str]:
    import sys
    import os
    
    if sys.platform != "win32":
        return False, "Not supported on this platform"
        
    try:
        import winreg
        
        current_path = os.path.abspath(sys.argv[0])
        
        if current_path.lower().endswith(".exe"):
            cmd_str = f'"{current_path}" "%1"'
            ico_path = current_path
        else:
            pythonw_path = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
            if not os.path.exists(pythonw_path):
                pythonw_path = sys.executable
            cmd_str = f'"{pythonw_path}" "{current_path}" "%1"'
            ico_path = os.path.join(os.path.dirname(current_path), "siftool.ico")
            
        menu_title = "Sift with Siftool"
        
        for ext in SUPPORTED_EXTS:
            key_path = f"Software\\Classes\\SystemFileAssociations\\{ext}\\shell\\Siftool"
            
            key = winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_ALL_ACCESS)
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, menu_title)
            
            if os.path.exists(ico_path) or current_path.lower().endswith(".exe"):
                winreg.SetValueEx(key, "Icon", 0, winreg.REG_SZ, ico_path)
            winreg.CloseKey(key)
            
            cmd_key_path = f"{key_path}\\command"
            cmd_key = winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, cmd_key_path, 0, winreg.KEY_ALL_ACCESS)
            winreg.SetValueEx(cmd_key, "", 0, winreg.REG_SZ, cmd_str)
            winreg.CloseKey(cmd_key)
            
        return True, ""
    except Exception as e:
        return False, str(e)

def unregister_context_menu() -> tuple[bool, str]:
    import sys
    
    if sys.platform != "win32":
        return False, "Not supported on this platform"
        
    try:
        import winreg
        for ext in SUPPORTED_EXTS:
            key_path = f"Software\\Classes\\SystemFileAssociations\\{ext}\\shell\\Siftool"
            _delete_key_recursive(winreg.HKEY_CURRENT_USER, key_path)
        return True, ""
    except Exception as e:
        return False, str(e)

def is_context_menu_registered() -> bool:
    import sys
    
    if sys.platform != "win32":
        return False
        
    try:
        import winreg
        first_ext = list(SUPPORTED_EXTS)[0]
        key_path = f"Software\\Classes\\SystemFileAssociations\\{first_ext}\\shell\\Siftool"
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_READ)
        winreg.CloseKey(key)
        return True
    except FileNotFoundError:
        return False
    except Exception:
        return False


# ─────────────────────────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    import os
    
    is_cli_cmd = False
    if len(sys.argv) > 1:
        if sys.argv[1] in ("scan", "clean", "verify", "register-context", "unregister-context"):
            is_cli_cmd = True
            
    if len(sys.argv) > 1 and not is_cli_cmd:
        abs_paths = [os.path.abspath(p) for p in sys.argv[1:]]
        if send_to_existing_instance(abs_paths):
            sys.exit(0)

    if len(sys.argv) > 1 and is_cli_cmd:
        cli_main()
    else:
        try:
            from tkinterdnd2 import TkinterDnD
            root: tk.Tk = TkinterDnD.Tk()
        except ImportError:
            root = tk.Tk()

        app = SiftoolApp(root)
        root.mainloop()
