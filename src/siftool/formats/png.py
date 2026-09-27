"""Lossless PNG handler: parse / write / essential digest / rewrite.

A PNG is a fixed 8-byte signature followed by a sequence of length-prefixed
chunks (4-byte big-endian length, 4-byte type, `length` bytes of data, then
a 4-byte CRC-32 of type+data), ending with `IEND`; anything after `IEND` is
a `TRAILING` block. Chunks that define how pixels decode (`IHDR`, `PLTE`,
`IDAT`, `IEND`, `tRNS`, `sBIT`, and the APNG animation chunks `acTL`/`fcTL`/
`fdAT`) are `ESSENTIAL`: `write` always copies them byte-for-byte from the
source, so `essential_digest` is unchanged by cleaning. Everything else is
metadata, classified into privacy categories per spec table 7.2 and either
kept, dropped, or (`eXIf`) rewritten by the planner.

`IHDR`/`IEND`/`acTL`/`fcTL` additionally have a payload size fixed by the
format itself (13/0/8/26 bytes), and `tRNS`/`sBIT`'s payload size is fixed
by `IHDR`'s own colour type (and, for `tRNS` on an indexed image, `PLTE`'s
entry count); any size outside what's allowed is `CorruptFileError` (Task
9b) -- an `ESSENTIAL` chunk is copied byte-for-byte and never
signature-scanned by `verify` (spec section 8), so trusting one at a size
it was never meant to have would let it smuggle anything. `PLTE`/`IDAT`/
`fdAT` are legitimately variable-length and keep no size check.

Repeating a structure at its own legal size is just as unscanned and
trusted as an oversize one, so count and order are enforced too (ruling
R15): `IHDR` must be the file's first chunk and appear exactly once; `acTL`
at most once and before the first `IDAT`; `fcTL`/`fdAT` only after an
`acTL`. Violating any of these is the same `CorruptFileError` as a wrong
size (`_check_structure`).

A chunk type's first letter's case is the PNG spec's own critical/ancillary
flag: an unrecognized chunk starting with an uppercase letter is critical
(the file cannot be safely processed without understanding it, so parsing
raises `UnsafeFileError`); a lowercase one is ancillary and safe to drop.

Real-world motivation: the user's own ChatGPT PNG hides its C2PA manifest
in a `caBX` chunk, and AI generators (Stable Diffusion, ComfyUI) hide
prompts in `tEXt`/`zTXt`/`iTXt` chunks -- both are classified
`ai_provenance` here.
"""

from __future__ import annotations

import struct
import zlib
from typing import BinaryIO

from ..detect import register
from ..errors import CorruptFileError, UnsafeFileError
from ..metadata.c2pa import c2pa_findings
from ..metadata.exif import build_minimal_exif, parse_exif, strip_exif_header
from ..metadata.icc import icc_description
from ..metadata.xmp import xmp_findings
from ..model import Action, Block, Category, Container, Finding, Kind, Plan, Span, TRAILING_TAG
from .base import copy_span, sha256_spans

_SIGNATURE = b"\x89PNG\r\n\x1a\n"

# AI-image-generator text keywords (Stable Diffusion "parameters", ComfyUI
# "workflow"/"prompt", InvokeAI "invokeai_metadata", Automatic1111-style
# "sd-metadata", DreamStudio "Dream") -- spec 7.2's ai_provenance keywords.
AI_TEXT_KEYWORDS = frozenset(
    {"parameters", "prompt", "workflow", "invokeai_metadata", "sd-metadata", "Dream"}
)

# Spec table 7.2's classification, by chunk type.
_ESSENTIAL_TYPES = frozenset(
    {b"IHDR", b"PLTE", b"IDAT", b"IEND", b"tRNS", b"sBIT", b"acTL", b"fcTL", b"fdAT"}
)

# Essential chunks whose payload size the format itself fixes (Task 9b):
# any other size is CorruptFileError. tRNS/sBIT's size instead depends on
# IHDR's colour type -- see _trns_size_ok / _SBIT_SIZE_BY_COLOUR_TYPE.
# PLTE/IDAT/fdAT are legitimately variable-length and are not size-checked.
_FIXED_ESSENTIAL_SIZES: dict[bytes, int] = {b"IHDR": 13, b"IEND": 0, b"acTL": 8, b"fcTL": 26}

# sBIT's payload size by IHDR colour type (0/2/3/4/6 -- 1/5 don't exist).
_SBIT_SIZE_BY_COLOUR_TYPE: dict[int, int] = {0: 1, 2: 3, 3: 3, 4: 2, 6: 4}

_APPEARANCE_TYPES = frozenset({b"iCCP", b"sRGB", b"gAMA", b"cHRM", b"cICP", b"mDCV", b"cLLI", b"pHYs"})
_AI_PROVENANCE_TYPES = frozenset({b"caBX", b"cPRV"})
_TEXT_TYPES = frozenset({b"tEXt", b"zTXt", b"iTXt"})

_ESSENTIAL_LABELS: dict[bytes, str] = {
    b"IHDR": "Image header",
    b"PLTE": "Palette",
    b"IDAT": "Image data",
    b"IEND": "Image trailer",
    b"tRNS": "Transparency",
    b"sBIT": "Significant bits",
    b"acTL": "Animation control",
    b"fcTL": "Frame control",
    b"fdAT": "Frame data",
}

_APPEARANCE_LABELS: dict[bytes, str] = {
    b"sRGB": "sRGB color space",
    b"gAMA": "Image gamma",
    b"cHRM": "Chromaticity",
    b"cICP": "Coding-independent code points",
    b"mDCV": "Mastering display color volume",
    b"cLLI": "Content light level information",
    b"pHYs": "Physical pixel dimensions",
}

_MAX_ICCP_PROFILE = 4 << 20  # 4 MiB, per the Task 7 brief
_MAX_TEXT_DECOMPRESSED = 1 << 20  # 1 MiB, per the Task 7 brief


class PngHandler:
    """Recognizes, parses, rewrites and writes lossless PNG files."""

    name = "png"
    extensions = (".png", ".apng")

    def sniff(self, head: bytes) -> bool:
        return head[:8] == _SIGNATURE

    def parse(self, buf: bytes) -> Container:
        blocks, iend_end = _read_chunks(buf)
        if iend_end < len(buf):
            blocks.append(_trailing_block(buf, iend_end, len(buf)))
        return Container(format="png", blocks=blocks, source=buf, positional=False)

    def write(self, container: Container, plan: Plan, out: BinaryIO) -> None:
        out.write(_SIGNATURE)
        for index, block in enumerate(container.blocks):
            action = plan.actions[index]
            if action == Action.KEEP:
                if block.corrupt:
                    raise CorruptFileError(f"cannot keep {block.tag} chunk: invalid CRC")
                copy_span(container.source, block.span, out)
            elif action == Action.REWRITE:
                if block.corrupt:
                    raise CorruptFileError(f"cannot rewrite {block.tag} chunk: invalid CRC")
                out.write(plan.replacements[index])
            # DROP: write nothing. ZERO never occurs for PNG (positional=False).

    def essential_digest(self, container: Container) -> str:
        spans = [block.span for block in container.blocks if block.kind is Kind.ESSENTIAL]
        return sha256_spans(container.source, spans)

    def rewrite(self, block: Block, allowed: frozenset[Category]) -> bytes | None:
        if block.tag == "eXIf":
            return _rewrite_exif(block, allowed)
        return None


# ---------------------------------------------------------------------------
# Reading: chunk stream -> Blocks
# ---------------------------------------------------------------------------


def _read_chunks(buf: bytes) -> tuple[list[Block], int]:
    """Tile `buf` into classified `Block`s, signature to `IEND`.

    Classifies each chunk as soon as it is read (rather than in a separate
    pass), so the first structural or safety problem encountered -- in
    file order -- is what gets raised. Returns `(blocks, iend_end)`; the
    caller treats anything from `iend_end` to `len(buf)` as trailing data.
    Never raises anything but `CorruptFileError`/`UnsafeFileError`.

    Tracks the handful of pieces of cross-chunk state `_check_structure`
    (count/order, ruling R15) and the `tRNS`/`sBIT` size check need:
    `index` (this chunk's position, for "`IHDR` must be first"), whether an
    `acTL`/`IDAT` has already been seen, `IHDR`'s own colour type, and
    `PLTE`'s entry count.
    """
    n = len(buf)
    if buf[:8] != _SIGNATURE:
        raise CorruptFileError("not a PNG file: bad signature")

    blocks: list[Block] = []
    p = 8
    index = 0
    seen_actl = False
    seen_idat = False
    colour_type: int | None = None
    plte_entries: int | None = None
    while True:
        if p + 8 > n:
            raise CorruptFileError("truncated PNG (missing IEND)")
        length = int.from_bytes(buf[p : p + 4], "big")
        ctype = buf[p + 4 : p + 8]
        data_start = p + 8
        data_end = data_start + length
        crc_end = data_end + 4
        if crc_end > n:
            raise CorruptFileError(f"chunk {ctype!r} exceeds buffer")
        data = buf[data_start:data_end]
        crc = int.from_bytes(buf[data_end:crc_end], "big")

        _check_structure(ctype, index, seen_actl, seen_idat)
        blocks.append(_classify_chunk(Span(p, crc_end), ctype, data, crc, colour_type, plte_entries))

        if ctype == b"IHDR":
            colour_type = data[9]
        elif ctype == b"PLTE":
            plte_entries = len(data) // 3
        elif ctype == b"acTL":
            seen_actl = True
        elif ctype == b"IDAT":
            seen_idat = True

        index += 1
        p = crc_end
        if ctype == b"IEND":
            return blocks, p


def _check_structure(ctype: bytes, index: int, seen_actl: bool, seen_idat: bool) -> None:
    """Count/order rules for `IHDR`/`acTL`/`fcTL`/`fdAT` (ruling R15).

    `IHDR` must be the very first chunk and appear exactly once (checking
    both "the first chunk is IHDR" and "IHDR is only ever the first chunk"
    together catches every way to violate either half); `acTL` at most
    once and before the first `IDAT`; `fcTL`/`fdAT` only once an `acTL` has
    already been seen. Each violation is `CorruptFileError("invalid
    <TYPE>")` -- the same message a wrong size would raise, since both are
    the same underlying problem: a structure trusted as `ESSENTIAL` (and so
    never signature-scanned) that isn't actually what it claims to be.
    """
    if index == 0:
        if ctype != b"IHDR":
            raise CorruptFileError("invalid IHDR")
        return
    if ctype == b"IHDR":
        raise CorruptFileError("invalid IHDR")
    if ctype == b"acTL" and (seen_actl or seen_idat):
        raise CorruptFileError("invalid acTL")
    if ctype in (b"fcTL", b"fdAT") and not seen_actl:
        raise CorruptFileError(f"invalid {ctype.decode('ascii')}")


def _classify_chunk(
    span: Span, ctype: bytes, data: bytes, crc: int, colour_type: int | None, plte_entries: int | None
) -> Block:
    """Classify one chunk into its `Block`, per spec table 7.2.

    A CRC mismatch on an `ESSENTIAL` chunk is `CorruptFileError` right
    away; on any other chunk it just marks `Block.corrupt = True` (the
    chunk is still classified normally -- CRC validity doesn't affect
    what it means, only whether it's safe for `write` to send its bytes
    to the output at all, verbatim via KEEP or rebuilt via REWRITE).

    An `IHDR`/`IEND`/`acTL`/`fcTL` at any size other than its one fixed
    length, or a `tRNS`/`sBIT` at a size `colour_type` (and, for `tRNS`,
    `plte_entries`) doesn't allow, is `CorruptFileError` too (Task 9b),
    regardless of a valid CRC -- `_FIXED_ESSENTIAL_SIZES` / `_trns_size_ok`
    / `_SBIT_SIZE_BY_COLOUR_TYPE`. `colour_type` is always already known
    here: `_check_structure` guarantees `IHDR` was the first chunk read,
    and classifying it (below) always sets it before any later chunk is
    reached.
    """
    tag = ctype.decode("ascii", errors="replace")
    valid_crc = (zlib.crc32(ctype + data) & 0xFFFFFFFF) == crc

    if ctype in _ESSENTIAL_TYPES:
        if not valid_crc:
            raise CorruptFileError(f"invalid CRC in essential chunk {tag}")
        fixed_size = _FIXED_ESSENTIAL_SIZES.get(ctype)
        if fixed_size is not None and len(data) != fixed_size:
            raise CorruptFileError(f"invalid {tag}")
        if ctype == b"tRNS" and not _trns_size_ok(colour_type, plte_entries, len(data)):
            raise CorruptFileError("invalid tRNS")
        if ctype == b"sBIT" and len(data) != _SBIT_SIZE_BY_COLOUR_TYPE.get(colour_type, -1):
            raise CorruptFileError("invalid sBIT")
        return Block(Kind.ESSENTIAL, span, _ESSENTIAL_LABELS.get(ctype, tag), tag)

    block = _classify_non_essential(span, tag, ctype, data)
    block.corrupt = not valid_crc
    return block


def _trns_size_ok(colour_type: int | None, plte_entries: int | None, size: int) -> bool:
    """`tRNS`'s payload size depends on `IHDR`'s colour type (Task 9b/R15):
    grayscale (0) is 2 bytes, truecolor (2) is 6, indexed (3) is 1 up to
    the palette's own entry count; grayscale+alpha (4) and RGBA (6) already
    carry alpha, so `tRNS` is never valid there at all.
    """
    if colour_type == 0:
        return size == 2
    if colour_type == 2:
        return size == 6
    if colour_type == 3:
        return 1 <= size <= (plte_entries or 0)
    return False


def _classify_non_essential(span: Span, tag: str, ctype: bytes, data: bytes) -> Block:
    """Classify every chunk not in `_ESSENTIAL_TYPES` -- metadata or unknown.

    An unrecognized type raises `UnsafeFileError` if critical (checked at
    the bottom, once every known metadata type has been ruled out), so
    this never actually returns a `Block` for those; only ancillary
    unknown chunks fall through to a `Kind.UNKNOWN` `Block`.
    """
    if ctype in _APPEARANCE_TYPES:
        finding = _iccp_finding(data) if ctype == b"iCCP" else Finding(
            Category.APPEARANCE, _APPEARANCE_LABELS.get(ctype, tag), where=tag, size=len(data)
        )
        return Block(Kind.METADATA, span, finding.label, tag, findings=[finding])

    if ctype == b"eXIf":
        tiff = strip_exif_header(data)
        return Block(
            Kind.METADATA, span, "EXIF data", tag,
            findings=parse_exif(tiff, tag), rewritable=True, raw=tiff,
        )

    if ctype == b"tIME":
        return Block(Kind.METADATA, span, "Creation Time", tag, findings=[_time_finding(data)])

    if ctype in _AI_PROVENANCE_TYPES:
        return Block(Kind.METADATA, span, "C2PA manifest", tag, findings=c2pa_findings(data, tag))

    if ctype in _TEXT_TYPES:
        return _classify_text(span, tag, data)

    if ctype[0:1].isupper():
        raise UnsafeFileError(f"unknown critical chunk: {tag}")
    return Block(Kind.UNKNOWN, span, f"Unknown chunk {tag}", tag, removable=True)


def _iccp_finding(data: bytes) -> Finding:
    """iCCP: `keyword\\0 compression_method(1) compressed_profile` -> `appearance`.

    Decompression is capped at 4 MiB (the brief's ceiling for the
    description). Never raises: any structural problem (no keyword
    terminator, bad zlib stream, unreadable profile) degrades to
    `detail=None` rather than propagating.
    """
    description = None
    size = len(data)
    try:
        i = data.index(b"\x00")
        compressed = data[i + 2 :]  # skip the NUL and the 1-byte compression method
        profile = zlib.decompressobj().decompress(compressed, _MAX_ICCP_PROFILE)
        description = icc_description(profile)
        size = len(profile)
    except Exception:
        pass
    return Finding(Category.APPEARANCE, "ICC profile", description, "iCCP", size=size)


def _time_finding(data: bytes) -> Finding:
    """tIME: year(2BE) month day hour minute second (1 byte each) -> `time`."""
    try:
        year, month, day, hour, minute, second = struct.unpack(">HBBBBB", data)
        detail = f"{year:04d}-{month:02d}-{day:02d} {hour:02d}:{minute:02d}:{second:02d}"
    except struct.error:
        detail = None
    return Finding(Category.TIME, "Creation Time", detail, "tIME", size=len(data))


# ---------------------------------------------------------------------------
# Text chunks: tEXt / zTXt / iTXt -> Blocks, by keyword
# ---------------------------------------------------------------------------


def _classify_text(span: Span, tag: str, data: bytes) -> Block:
    """tEXt/zTXt/iTXt -> a Block classified by keyword (spec 7.2's table).

    `XML:com.adobe.xmp` is the one exception: its text is an XMP packet,
    read with `xmp_findings` instead of the plain keyword/text Finding
    every other keyword gets.
    """
    keyword, raw = _read_text_chunk(tag, data)

    if keyword == "XML:com.adobe.xmp":
        return Block(Kind.METADATA, span, "XMP packet", tag, findings=xmp_findings(raw, tag))

    text = raw.decode("utf-8" if tag == "iTXt" else "latin-1", errors="replace")
    finding = Finding(_text_category(keyword, text), keyword, text, tag, size=len(raw))
    return Block(Kind.METADATA, span, keyword or tag, tag, findings=[finding])


def _inflate_bounded(compressed: bytes) -> bytes:
    """Decompress `compressed`, capped at `_MAX_TEXT_DECOMPRESSED` (1 MiB)."""
    try:
        return zlib.decompressobj().decompress(compressed, _MAX_TEXT_DECOMPRESSED)
    except zlib.error:
        return b""


def _read_text_chunk(tag: str, data: bytes) -> tuple[str, bytes]:
    """Return `(keyword, raw_text_bytes)` for a tEXt/zTXt/iTXt chunk.

    `raw_text_bytes` is already decompressed for zTXt/iTXt (bounded to 1
    MiB), but not yet decoded to `str` -- the caller decides the charset
    (Latin-1 for tEXt/zTXt, UTF-8 for iTXt) or, for the XMP keyword, keeps
    it as raw bytes for `xmp_findings`. Never raises: any malformed
    structure (missing NUL separators, truncated iTXt header, bad zlib
    stream) degrades to as much as could be read, down to `(text, b"")`.
    """
    try:
        i = data.index(b"\x00")
    except ValueError:
        return data.decode("latin-1", errors="replace"), b""
    keyword = data[:i].decode("latin-1", errors="replace")
    rest = data[i + 1 :]
    try:
        if tag == "tEXt":
            return keyword, rest
        if tag == "zTXt":
            return keyword, (_inflate_bounded(rest[1:]) if rest else b"")
        # iTXt: compression_flag(1) compression_method(1) lang\0 translated\0 text
        if len(rest) < 2:
            return keyword, b""
        compression_flag = rest[0]
        body = rest[2:]
        j = body.index(b"\x00")
        k = body.index(b"\x00", j + 1)
        text_bytes = body[k + 1 :]
        if compression_flag:
            text_bytes = _inflate_bounded(text_bytes)
        return keyword, text_bytes
    except Exception:
        return keyword, b""


def _text_category(keyword: str, text: str) -> Category:
    """Spec 7.2's tEXt/zTXt/iTXt keyword table."""
    if keyword in AI_TEXT_KEYWORDS:
        return Category.AI_PROVENANCE
    if keyword == "Comment" and text.startswith("{") and '"prompt"' in text:
        return Category.AI_PROVENANCE
    if keyword in ("Author", "Copyright"):
        return Category.IDENTITY
    if keyword == "Creation Time":
        return Category.TIME
    if keyword == "Software":
        return Category.SOFTWARE
    return Category.TEXT


def _trailing_block(source: bytes, start: int, end: int) -> Block:
    label = "Data after end of image"
    return Block(
        Kind.METADATA, Span(start, end), label, TRAILING_TAG,
        findings=[Finding(Category.EXTRAS, label, where=TRAILING_TAG, size=end - start)],
    )


# ---------------------------------------------------------------------------
# Rewriting: eXIf -> a reduced replacement chunk
# ---------------------------------------------------------------------------


def _rewrite_exif(block: Block, allowed: frozenset[Category]) -> bytes | None:
    """`png_chunk(b"eXIf", build_minimal_exif(...))`, or None.

    Same semantics as JPEG's EXIF rewrite (spec section 6: `appearance` --
    orientation, color -- is kept by every mode except MAXIMUM, for PNG
    exactly as for JPEG). Mode policy lives entirely in `modes.ALLOWED`
    and the planner; this always keeps whatever `allowed` permits.
    """
    minimal = build_minimal_exif(strip_exif_header(block.raw), allowed)
    if minimal is None:
        return None
    return _build_chunk(b"eXIf", minimal)


def _build_chunk(ctype: bytes, data: bytes) -> bytes:
    """Build one complete chunk: length(4BE) + type + data + CRC32(4BE)."""
    crc = zlib.crc32(ctype + data) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + ctype + data + struct.pack(">I", crc)


register(PngHandler())
