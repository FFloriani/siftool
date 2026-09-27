"""Lossless JPEG handler: parse / write / essential digest / rewrite.

A JPEG is a sequence of marker segments starting with `SOI` (`0xFFD8`) and
ending with `EOI` (`0xFFD9`); anything after `EOI` is a `TRAILING` block
(Motion Photo video, an MPF secondary image, a Samsung SEFT trailer, ...).
Segments that define how pixels decode (`DQT`/`DHT`/`SOF`/`SOS` plus its
entropy-coded data/`EOI`/the Adobe `APP14` color-transform marker) are
`ESSENTIAL`: `write` always copies them byte-for-byte from the source, so
`essential_digest` is unchanged by cleaning -- the "bit-for-bit" proof
behind the Verified Clean seal. Everything else is metadata, classified
into privacy categories per spec table 7.1 and either kept, dropped, or
(EXIF, and JFIF carrying a thumbnail) rewritten by the planner.

Fill bytes (extra `0xFF` before a marker) are tolerated on read and, since
they are folded into the following block's span, reproduced unchanged
whenever that block is kept -- pixels are never re-encoded.
"""

from __future__ import annotations

import struct
from typing import BinaryIO

from ..detect import register
from ..errors import CorruptFileError
from ..metadata.c2pa import c2pa_findings
from ..metadata.exif import build_minimal_exif, parse_exif, strip_exif_header
from ..metadata.icc import icc_description
from ..metadata.iptc import iptc_findings
from ..metadata.xmp import xmp_findings
from ..model import Action, Block, Category, Container, Finding, Kind, Plan, Span, TRAILING_TAG
from .base import copy_span, sha256_spans

# Marker codes with no length field or payload.
_SOI, _EOI = 0xD8, 0xD9
_RST_LO, _RST_HI = 0xD0, 0xD7

# SOF0..SOF15, minus the codes reused for DHT (0xC4), the reserved "JPG"
# extension marker (0xC8) and DAC (0xCC).
_SOF_CODES = frozenset(range(0xC0, 0xD0)) - {0xC4, 0xC8, 0xCC}

# Application-data marker signatures (Task 6 brief's classification table).
_JFIF_SIG = b"JFIF\x00"
_JFXX_SIG = b"JFXX\x00"
_EXIF_SIG = b"Exif\x00\x00"
_XMP_SIG = b"http://ns.adobe.com/xap/1.0/\x00"
_XMP_EXT_SIG = b"http://ns.adobe.com/xmp/extension/\x00"
_ICC_SIG = b"ICC_PROFILE\x00"
_ICC_HEADER_LEN = len(_ICC_SIG) + 2  # + 1-byte chunk seq + 1-byte chunk count
_MPF_SIG = b"MPF\x00"
_JUMBF_SIG = b"JP\x00\x00"
_IPTC_SIG = b"Photoshop 3.0\x00"
_ADOBE_SIG = b"Adobe"

_MAX_EXIF_PAYLOAD = 65533  # 2-byte length field max (65535) minus itself


class JpegHandler:
    """Recognizes, parses, rewrites and writes lossless JPEG files."""

    name = "jpeg"
    extensions = (".jpg", ".jpeg", ".jpe", ".jfif")

    def sniff(self, head: bytes) -> bool:
        return head[:3] == b"\xff\xd8\xff"

    def parse(self, buf: bytes) -> Container:
        segments, eoi_end = _read_segments(buf)
        blocks = _classify_segments(segments)
        if eoi_end < len(buf):
            blocks.append(_trailing_block(buf, eoi_end, len(buf)))
        return Container(format="jpeg", blocks=blocks, source=buf, positional=False)

    def write(self, container: Container, plan: Plan, out: BinaryIO) -> None:
        for index, block in enumerate(container.blocks):
            action = plan.actions[index]
            if action == Action.KEEP:
                copy_span(container.source, block.span, out)
            elif action == Action.REWRITE:
                out.write(plan.replacements[index])
            # DROP: write nothing. ZERO never occurs for JPEG (positional=False).

    def essential_digest(self, container: Container) -> str:
        spans = [block.span for block in container.blocks if block.kind is Kind.ESSENTIAL]
        return sha256_spans(container.source, spans)

    def rewrite(self, block: Block, allowed: frozenset[Category]) -> bytes | None:
        if block.tag == "APP1:Exif":
            return _rewrite_exif(block, allowed)
        if block.tag == "APP0:JFIF":
            return _rewrite_jfif(block, allowed)
        return None


# ---------------------------------------------------------------------------
# Reading: marker stream -> raw (span, code, payload) segments
# ---------------------------------------------------------------------------


def _read_segments(buf: bytes) -> tuple[list[tuple[Span, int, bytes]], int]:
    """Tile `buf` into `(span, marker_code, payload)` segments, SOI to EOI.

    Fill bytes (`0xFF`) preceding a marker are folded into the start of
    that marker's own segment. After each `SOS` header, the entropy-coded
    scan data is included in that segment too (`_scan_to_next_marker`).
    Returns `(segments, eoi_end)`; the caller treats anything from
    `eoi_end` to `len(buf)` as trailing data. Never raises anything but
    `CorruptFileError` on a malformed structure.
    """
    n = len(buf)
    if buf[:2] != b"\xff\xd8":
        raise CorruptFileError("missing SOI")

    segments: list[tuple[Span, int, bytes]] = [(Span(0, 2), _SOI, b"")]
    p = 2
    while True:
        start = p
        if p >= n or buf[p] != 0xFF:
            raise CorruptFileError("truncated JPEG")

        q = p
        while q + 1 < n and buf[q + 1] == 0xFF:
            q += 1
        if q + 1 >= n:
            raise CorruptFileError("truncated JPEG")
        code = buf[q + 1]
        marker_pos = q

        if code == _EOI:
            end = marker_pos + 2
            segments.append((Span(start, end), code, b""))
            return segments, end

        if _RST_LO <= code <= _RST_HI:
            end = marker_pos + 2
            segments.append((Span(start, end), code, b""))
            p = end
            continue

        if marker_pos + 4 > n:
            raise CorruptFileError("truncated JPEG")
        length = int.from_bytes(buf[marker_pos + 2 : marker_pos + 4], "big")
        seg_end = marker_pos + 2 + length
        if length < 2 or seg_end > n:
            raise CorruptFileError("truncated JPEG")
        payload = buf[marker_pos + 4 : seg_end]

        if code == 0xDA:  # SOS: header + entropy-coded data, up to the next marker
            scan_end = _scan_to_next_marker(buf, seg_end)
            segments.append((Span(start, scan_end), code, payload))
            p = scan_end
            continue

        segments.append((Span(start, seg_end), code, payload))
        p = seg_end


def _scan_to_next_marker(data: bytes, p: int) -> int:
    """Return the offset of the marker following a scan's entropy-coded data.

    Exactly the algorithm given in the Task 6 brief: `0xFF00` stuffing and
    `RST0-7` are part of the scan; any other `0xFF xx` is the next marker.
    """
    while True:
        p = data.find(b"\xff", p)
        if p < 0 or p + 1 >= len(data):
            raise CorruptFileError("truncated JPEG scan")
        n = data[p + 1]
        if n == 0x00 or 0xD0 <= n <= 0xD7:
            p += 2
            continue
        if n == 0xFF:
            p += 1
            continue
        break
    return p


# ---------------------------------------------------------------------------
# Classification: raw segments -> Blocks
# ---------------------------------------------------------------------------


def _classify_segments(segments: list[tuple[Span, int, bytes]]) -> list[Block]:
    """Convert raw segments into `Block`s, merging multi-segment ICC profiles."""
    blocks: list[Block] = []
    i = 0
    while i < len(segments):
        span, code, payload = segments[i]
        if code == _SOI:
            blocks.append(Block(Kind.ESSENTIAL, span, "Start of Image", "SOI"))
            i += 1
        elif code == _EOI:
            blocks.append(Block(Kind.ESSENTIAL, span, "End of Image", "EOI"))
            i += 1
        elif _RST_LO <= code <= _RST_HI:
            blocks.append(Block(Kind.ESSENTIAL, span, f"Restart marker {code - _RST_LO}", "RST"))
            i += 1
        elif code == 0xE2 and payload.startswith(_ICC_SIG):
            block, i = _classify_icc_run(segments, i)
            blocks.append(block)
        else:
            blocks.append(_classify(code, span, payload))
            i += 1
    return blocks


def _classify(code: int, span: Span, payload: bytes) -> Block:
    """Classify one non-SOI/EOI/RST/ICC segment into its `Block`."""
    if code in _SOF_CODES:
        n = code - 0xC0
        return Block(Kind.ESSENTIAL, span, f"Start of Frame {n}", f"SOF{n}")
    if code == 0xC4:
        return Block(Kind.ESSENTIAL, span, "Huffman Table", "DHT")
    if code == 0xCC:
        return Block(Kind.ESSENTIAL, span, "Arithmetic Coding Conditioning", "DAC")
    if code == 0xDB:
        return Block(Kind.ESSENTIAL, span, "Quantization Table", "DQT")
    if code == 0xDD:
        return Block(Kind.ESSENTIAL, span, "Restart Interval", "DRI")
    if code == 0xDC:
        return Block(Kind.ESSENTIAL, span, "Number of Lines", "DNL")
    if code == 0xDA:
        return Block(Kind.ESSENTIAL, span, "Start of Scan", "SOS")
    if 0xE0 <= code <= 0xEF:
        return _classify_app(code, span, payload)
    if code == 0xFE:
        text = payload.decode("utf-8", errors="replace")
        return Block(Kind.METADATA, span, "Comment", "COM", findings=[Finding(Category.TEXT, "Comment", text)])
    if code == 0xC8 or 0xF0 <= code <= 0xFD:
        label = f"Reserved marker 0x{code:02X}"
        return Block(
            Kind.UNKNOWN, span, label, f"RESERVED:0x{code:02X}",
            findings=[Finding(Category.UNKNOWN, label)], removable=False,
        )
    return Block(Kind.UNKNOWN, span, f"Unknown marker 0x{code:02X}", f"UNK:0x{code:02X}", removable=True)


def _classify_app(code: int, span: Span, payload: bytes) -> Block:
    """Classify an APP0-APP15 segment by its content signature.

    Any APPn whose payload doesn't match a known signature (including
    every APP3-APP10) falls back to an `UNKNOWN`, removable block tagged
    `f"APP{n}"` -- table 7.1's "Demais APPn -> unknown removivel".
    """
    n = code - 0xE0
    if code == 0xE0 and payload.startswith(_JFIF_SIG):
        return _classify_jfif(span, payload)
    if code == 0xE0 and payload.startswith(_JFXX_SIG):
        return Block(
            Kind.METADATA, span, "JFIF extension thumbnail", "APP0:JFXX",
            findings=[Finding(Category.EXTRAS, "JFXX thumbnail", size=len(payload))],
        )
    if code == 0xE1 and payload.startswith(_EXIF_SIG):
        return _classify_exif(span, payload)
    if code == 0xE1 and payload.startswith(_XMP_SIG):
        packet = payload[len(_XMP_SIG) :]
        return Block(
            Kind.METADATA, span, "XMP packet", "APP1:XMP", findings=xmp_findings(packet, "APP1/XMP")
        )
    if code == 0xE1 and payload.startswith(_XMP_EXT_SIG):
        chunk = payload[len(_XMP_EXT_SIG) :]
        return Block(
            Kind.METADATA, span, "Extended XMP", "APP1:XMPExt",
            findings=xmp_findings(chunk, "APP1/XMPExt"),
        )
    if code == 0xE2 and payload.startswith(_MPF_SIG):
        return Block(
            Kind.METADATA, span, "MPF index", "APP2:MPF",
            findings=[Finding(Category.EXTRAS, "MPF index", size=len(payload))],
        )
    if code == 0xEB and payload.startswith(_JUMBF_SIG):
        return Block(
            Kind.METADATA, span, "JUMBF/C2PA manifest", "APP11:JUMBF",
            findings=c2pa_findings(payload, "APP11/JUMBF"),
        )
    if code == 0xEC:
        return Block(
            Kind.METADATA, span, "APP12 data", "APP12",
            findings=[Finding(Category.SOFTWARE, "APP12 data", size=len(payload))],
        )
    if code == 0xED and payload.startswith(_IPTC_SIG):
        return Block(
            Kind.METADATA, span, "Photoshop IRB", "APP13:IRB",
            findings=iptc_findings(payload, "APP13/IRB"),
        )
    if code == 0xEE and payload.startswith(_ADOBE_SIG):
        return Block(Kind.ESSENTIAL, span, "Adobe color transform", "APP14:Adobe")
    return Block(Kind.UNKNOWN, span, f"Unknown APP{n}", f"APP{n}", removable=True)


def _classify_jfif(span: Span, payload: bytes) -> Block:
    """JFIF header: `appearance`; a thumbnail adds `extras` and makes it rewritable."""
    has_thumbnail = len(payload) > 14
    findings = [Finding(Category.APPEARANCE, "JFIF header")]
    if has_thumbnail:
        findings.append(Finding(Category.EXTRAS, "JFIF thumbnail", size=len(payload) - 14))
    return Block(
        Kind.METADATA, span, "JFIF header", "APP0:JFIF", findings=findings,
        rewritable=has_thumbnail, raw=payload if has_thumbnail else b"",
    )


def _classify_exif(span: Span, payload: bytes) -> Block:
    """APP1 Exif: always rewritable; `raw` holds the TIFF bytes (header stripped)."""
    tiff = strip_exif_header(payload)
    findings = parse_exif(tiff, "APP1/Exif")
    return Block(Kind.METADATA, span, "EXIF data", "APP1:Exif", findings=findings, rewritable=True, raw=tiff)


def _classify_icc_run(segments: list[tuple[Span, int, bytes]], i: int) -> tuple[Block, int]:
    """Merge one or more consecutive `ICC_PROFILE` APP2 chunks into one Block.

    Real encoders split a large ICC profile across several back-to-back
    APP2 segments (spec 7.1: "inclusive multissegmento"); their chunk data
    (each payload after the 12-byte signature + 1-byte seq + 1-byte count
    header) is concatenated in stream order before reading the profile's
    description, so `icc_description` always sees the complete profile.
    """
    first_span, _, first_payload = segments[i]
    parts = [first_payload[_ICC_HEADER_LEN:]]
    last_span = first_span
    j = i + 1
    while j < len(segments):
        span, code, payload = segments[j]
        if code != 0xE2 or not payload.startswith(_ICC_SIG):
            break
        parts.append(payload[_ICC_HEADER_LEN:])
        last_span = span
        j += 1

    profile = b"".join(parts)
    finding = Finding(Category.APPEARANCE, "ICC profile", icc_description(profile), size=len(profile))
    block = Block(
        Kind.METADATA, Span(first_span.start, last_span.end), "ICC color profile", "APP2:ICC",
        findings=[finding],
    )
    return block, j


def _trailing_block(source: bytes, start: int, end: int) -> Block:
    """Classify the data after EOI, per the Task 6 brief's label rules, in order."""
    data = source[start:end]
    head = data[:64]
    if b"ftyp" in head:
        label = "Embedded video after end of image (Motion Photo)"
    elif data[:2] == b"\xff\xd8":
        label = "Embedded image after end of image (MPF/gain map)"
    elif b"SEFH" in data:
        label = "Samsung trailer (SEFT)"
    else:
        label = "Data after end of image"
    return Block(
        Kind.METADATA, Span(start, end), label, TRAILING_TAG,
        findings=[Finding(Category.EXTRAS, label, size=len(data))],
    )


# ---------------------------------------------------------------------------
# Rewriting: mixed-category blocks -> a reduced replacement segment
# ---------------------------------------------------------------------------


def _rewrite_exif(block: Block, allowed: frozenset[Category]) -> bytes | None:
    """`FFE1 + len + b"Exif\\0\\0" + build_minimal_exif(...)`, or None."""
    minimal = build_minimal_exif(block.raw, allowed)
    if minimal is None:
        return None
    payload = b"Exif\x00\x00" + minimal
    if len(payload) > _MAX_EXIF_PAYLOAD:
        return None
    return b"\xff\xe1" + struct.pack(">H", len(payload) + 2) + payload


def _rewrite_jfif(block: Block, allowed: frozenset[Category]) -> bytes | None:
    """`FFE0 0010 + b"JFIF\\0" + version + units + densities + b"\\0\\0"`, or None."""
    if Category.APPEARANCE not in allowed:
        return None
    raw = block.raw
    payload = b"JFIF\x00" + raw[5:12] + b"\x00\x00"
    return b"\xff\xe0" + struct.pack(">H", len(payload) + 2) + payload


register(JpegHandler())
