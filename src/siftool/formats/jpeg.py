"""Lossless JPEG handler: parse / write / essential digest / rewrite.

A JPEG is a sequence of marker segments starting with `SOI` (`0xFFD8`) and
ending with `EOI` (`0xFFD9`); anything after `EOI` is a `TRAILING` block
(Motion Photo video, an MPF secondary image, a Samsung SEFT trailer, ...).
Segments that define how pixels decode -- `DQT`/`DHT`, the file's one
`SOF`, `SOS` plus its entropy-coded data, `EOI`, `DRI`/`DNL`, `DAC` in an
arithmetic-coded frame, and the FIRST standard, exactly-12-byte Adobe
`APP14` color-transform marker -- are `ESSENTIAL`: `write` always copies
them byte-for-byte from the source, so `essential_digest` is unchanged by
cleaning -- the "bit-for-bit" proof behind the Verified Clean seal.
Everything else is metadata, classified into privacy categories per spec
table 7.1 and either kept, dropped, or (EXIF, and JFIF carrying a
thumbnail) rewritten by the planner.

Every one of those ESSENTIAL structures is copied byte-for-byte and never
gets `verify`'s full signature scan (spec section 8), so each is only
trusted at exactly the shape a decoder actually needs, never merely "close
enough" (rulings R15 and R19):

- The file has exactly one `SOF` -- a second frame header is
  `CorruptFileError("invalid SOF")`, which also rejects rare hierarchical
  JPEGs -- and at least one `SOS`; without both there is no image at all,
  `CorruptFileError("missing image data")` (`_frame_code`).
- `DAC` only means something in an arithmetic-coded frame (`SOF9`-`SOF11`,
  `SOF13`-`SOF15`), classified against the file's one `SOF` even when it
  comes first: in a Huffman-coded frame it is a removable `UNKNOWN` block
  ("Unused DAC segment", dropped); in an arithmetic one it must be 1-8
  two-byte entries with `Tc` 0/1, `Tb` 0-3 and no repeated table, else it
  is demoted to `"DAC?"` like the segments below (`_classify_dac`).
- `DRI`/`DNL` must be exactly 2 bytes (their one field), `CorruptFileError`
  otherwise.
- An `APP14` starting with `b"Adobe"` is ESSENTIAL only the FIRST time it
  appears, and only at that one fixed 12-byte payload. Repeating it --
  even at that same legal size, which is how a first pass at this task's
  fix could still be bypassed -- or any size mismatch, demotes it (and
  every later Adobe-signature `APP14`) to a non-removable `UNKNOWN` block
  tagged plainly `"APP14"`; it's kept (dropping it would shift a CMYK
  JPEG's colors) but now covered by `verify`'s full signature scan rather
  than trusted like the one real Adobe marker.
- `SOF0`-`SOF15`/`SOS`/`DQT`/`DHT`'s declared length must equal what their
  own content implies (`SOF`: `8 + 3*Nf`; `SOS`: `6 + 2*Ns`; `DQT`: the sum
  over its tables of 65 or 129 bytes each, 8-/16-bit precision; `DHT`: the
  sum over its tables of `17 + sum(16 code-length counts)`). Unlike a
  count/order or DRI/DNL/VP8X-family violation, this one does NOT raise --
  dropping or rejecting the segment would break decoding -- it demotes the
  segment (bytes unchanged) to a non-removable `UNKNOWN` block tagged
  `f"{NAME}?"` (e.g. `"DQT?"`), so it's kept but fully signature-scanned
  instead of trusted.

A file keeps at most one ICC profile (rulings R20, R26): its `ICC_PROFILE`
APP2 chunks are gathered across the whole file by their seq/count bytes,
and the first profile whose chunks 1..count are all present and whose
joined bytes are a structurally valid profile is kept as `appearance`
(`_select_icc_profile`), exposed as the first ICC block's `logical`, which
`verify` scans too. Every other ICC chunk is a removable `UNKNOWN` block,
dropped: "Extra ICC profile" beside a kept profile, "Invalid ICC profile"
when no valid profile completes. Likewise only the first JFIF `APP0` and
the first `APP1` Exif are classified as such; any later one is dropped as
"Extra JFIF segment" (ruling R25) or "Extra EXIF block" (ruling R24).

Fill bytes (extra `0xFF` before a marker) are tolerated on read and kept
inside a block's span -- the following segment's, or, after a scan, the
`SOS` segment's own -- so they are reproduced unchanged whenever that block
is kept; pixels are never re-encoded.
"""

from __future__ import annotations

import struct
from typing import BinaryIO

from ..detect import register
from ..errors import CorruptFileError
from ..metadata.c2pa import c2pa_findings
from ..metadata.exif import build_minimal_exif, parse_exif, strip_exif_header
from ..metadata.icc import icc_description, icc_profile_valid
from ..metadata.iptc import iptc_findings
from ..metadata.xmp import xmp_findings
from ..model import Action, Block, Category, Container, Finding, Kind, Plan, Span, TRAILING_TAG
from .base import copy_span, sha256_spans

# Marker codes with no length field or payload.
_SOI, _EOI = 0xD8, 0xD9
_RST_LO, _RST_HI = 0xD0, 0xD7

# SOF0..SOF15, minus the codes reused for DHT (0xC4), the reserved "JPG"
# extension marker (0xC8) and DAC (0xCC). The arithmetic-coded frames are
# SOF9-SOF11 and SOF13-SOF15; the others are Huffman-coded (ITU-T T.81 B.1).
_SOF_CODES = frozenset(range(0xC0, 0xD0)) - {0xC4, 0xC8, 0xCC}
_ARITHMETIC_SOF_CODES = frozenset({0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF})
_SOS, _DAC = 0xDA, 0xCC

# Application-data marker signatures (Task 6 brief's classification table).
_JFIF_SIG = b"JFIF\x00"
_JFXX_SIG = b"JFXX\x00"
_EXIF_SIG = b"Exif\x00\x00"
_XMP_SIG = b"http://ns.adobe.com/xap/1.0/\x00"
_XMP_EXT_SIG = b"http://ns.adobe.com/xmp/extension/\x00"
_ICC_SIG = b"ICC_PROFILE\x00"
_ICC_HEADER_LEN = len(_ICC_SIG) + 2  # + 1-byte chunk seq + 1-byte chunk count
_MPF_SIG = b"MPF\x00"
# ISO/IEC 19566-5 APP11 header: CI (b"JP") + En (2-byte box instance number,
# chosen by the writer -- NOT part of the signature) + Z (4-byte sequence
# number) + LBox/TBox. Match on CI only; the writer's own box instance
# number must never gate whether a C2PA/JUMBF manifest is recognized.
_JUMBF_CI = b"JP"
_IPTC_SIG = b"Photoshop 3.0\x00"
_ADOBE_SIG = b"Adobe"
_ADOBE_PAYLOAD_LEN = 12  # Adobe(5) + version(2) + flags0(2) + flags1(2) + transform(1)

_MAX_EXIF_PAYLOAD = 65533  # 2-byte length field max (65535) minus itself


class JpegHandler:
    """Recognizes, parses, rewrites and writes lossless JPEG files."""

    name = "jpeg"
    extensions = (".jpg", ".jpeg", ".jpe", ".jfif")

    def sniff(self, head: bytes) -> bool:
        return head[:3] == b"\xff\xd8\xff"

    def parse(self, buf: bytes) -> Container:
        segments, eoi_end = _read_segments(buf)
        arithmetic = _frame_code(segments) in _ARITHMETIC_SOF_CODES
        blocks = _classify_segments(segments, arithmetic)
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


def _frame_code(segments: list[tuple[Span, int, bytes]]) -> int:
    """Return the marker code of the file's one `SOF` (ruling R19).

    A second `SOF` is `CorruptFileError("invalid SOF")`: a decoder never
    uses it (hierarchical JPEGs, the one legitimate multi-frame case, are
    rejected with it), so it could only smuggle bytes through as
    `ESSENTIAL`. No `SOF` or no `SOS` at all is `CorruptFileError("missing
    image data")`: nothing in the file is an image. A demoted (`"SOF0?"`,
    `"SOS?"`) segment still counts: its marker is what a decoder acts on.
    """
    sof_codes = [code for _, code, _ in segments if code in _SOF_CODES]
    if len(sof_codes) > 1:
        raise CorruptFileError("invalid SOF")
    if not sof_codes or not any(code == _SOS for _, code, _ in segments):
        raise CorruptFileError("missing image data")
    return sof_codes[0]


def _classify_segments(segments: list[tuple[Span, int, bytes]], arithmetic: bool) -> list[Block]:
    """Convert raw segments into `Block`s, merging runs of adjacent ICC chunks.

    Keeps the pieces of cross-segment state `_classify`/`_classify_app`
    (otherwise stateless per segment) can't: only the FIRST Adobe-signature
    `APP14` can become the `ESSENTIAL` `"APP14:Adobe"` (ruling R15 -- a
    repeat, even at the same legal 12-byte size, is not trusted); `DAC` is
    classified against the whole file's frame type (`arithmetic`, ruling
    R19); only the FIRST JFIF `APP0` and the FIRST `APP1` Exif are
    classified as such, and every later one is a removable `UNKNOWN` block
    with a finding naming it (`_extra`: "Extra JFIF segment", rulings R25;
    "Extra EXIF block", R24); and the file's one ICC profile is chosen from
    every `ICC_PROFILE` chunk in the file before any of them is classified
    (`_select_icc_profile`, rulings R20, R26).
    """
    icc_profile, icc_chunks = _select_icc_profile(segments)
    blocks: list[Block] = []
    seen_standard_adobe = False
    seen_icc_profile = False
    seen_jfif = False
    seen_exif = False
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
            block, i = _classify_icc_run(segments, i, icc_profile, icc_chunks, first=not seen_icc_profile)
            seen_icc_profile = seen_icc_profile or block.kind is Kind.METADATA
            blocks.append(block)
        elif code == 0xE0 and payload.startswith(_JFIF_SIG):
            blocks.append(_extra(span, "APP0:JFIF", "Extra JFIF segment", payload) if seen_jfif
                          else _classify_jfif(span, payload))
            seen_jfif = True
            i += 1
        elif code == 0xE1 and payload.startswith(_EXIF_SIG):
            blocks.append(_extra(span, "APP1:Exif", "Extra EXIF block", payload) if seen_exif
                          else _classify_exif(span, payload))
            seen_exif = True
            i += 1
        elif code == 0xEE and payload.startswith(_ADOBE_SIG):
            block = _classify_adobe(span, payload, seen_standard_adobe)
            seen_standard_adobe = seen_standard_adobe or block.kind is Kind.ESSENTIAL
            blocks.append(block)
            i += 1
        elif code == _DAC:
            blocks.append(_classify_dac(span, payload, arithmetic))
            i += 1
        else:
            blocks.append(_classify(code, span, payload))
            i += 1
    return blocks


def _classify(code: int, span: Span, payload: bytes) -> Block:
    """Classify one non-SOI/EOI/RST/ICC/JFIF/Exif/Adobe-APP14/DAC segment into its `Block`.

    `SOF`/`SOS`/`DQT`/`DHT` additionally check that the declared length
    (`len(payload)`, i.e. the segment's own length field minus itself)
    matches what the payload's content implies (ruling R15); a mismatch
    demotes the segment to a scanned, non-removable `UNKNOWN` block
    instead of raising -- `_demoted`. `DRI`/`DNL` must be exactly 2 bytes,
    `CorruptFileError` otherwise -- same as any other fixed-size essential
    structure at the wrong size (Task 9b).
    """
    if code in _SOF_CODES:
        n = code - 0xC0
        name = f"SOF{n}"
        if not _sof_payload_len_ok(payload):
            return _demoted(span, name, payload)
        return Block(Kind.ESSENTIAL, span, f"Start of Frame {n}", name)
    if code == 0xC4:
        if not _dht_payload_len_ok(payload):
            return _demoted(span, "DHT", payload)
        return Block(Kind.ESSENTIAL, span, "Huffman Table", "DHT")
    if code == 0xDB:
        if not _dqt_payload_len_ok(payload):
            return _demoted(span, "DQT", payload)
        return Block(Kind.ESSENTIAL, span, "Quantization Table", "DQT")
    if code == 0xDD:
        if len(payload) != 2:
            raise CorruptFileError("invalid DRI")
        return Block(Kind.ESSENTIAL, span, "Restart Interval", "DRI")
    if code == 0xDC:
        if len(payload) != 2:
            raise CorruptFileError("invalid DNL")
        return Block(Kind.ESSENTIAL, span, "Number of Lines", "DNL")
    if code == 0xDA:
        if not _sos_payload_len_ok(payload):
            return _demoted(span, "SOS", payload)
        return Block(Kind.ESSENTIAL, span, "Start of Scan", "SOS")
    if 0xE0 <= code <= 0xEF:
        return _classify_app(code, span, payload)
    if code == 0xFE:
        text = payload.decode("utf-8", errors="replace")
        return Block(
            Kind.METADATA, span, "Comment", "COM", findings=[Finding(Category.TEXT, "Comment", text, "COM", len(payload))]
        )
    if code == 0xC8 or 0xF0 <= code <= 0xFD:
        label = f"Reserved marker 0x{code:02X}"
        tag = f"RESERVED:0x{code:02X}"
        return Block(
            Kind.UNKNOWN, span, label, tag,
            findings=[Finding(Category.UNKNOWN, label, None, tag, len(payload))], removable=False,
        )
    return Block(Kind.UNKNOWN, span, f"Unknown marker 0x{code:02X}", f"UNK:0x{code:02X}", removable=True)


def _sof_payload_len_ok(payload: bytes) -> bool:
    """`SOF`: precision(1) + height(2) + width(2) + Nf(1) + Nf*3 bytes."""
    if len(payload) < 6:
        return False
    nf = payload[5]
    return len(payload) == 6 + 3 * nf


def _sos_payload_len_ok(payload: bytes) -> bool:
    """`SOS` header: Ns(1) + Ns*2 bytes + Ss(1) + Se(1) + AhAl(1).

    Only the header is checked -- `payload` here never includes the
    entropy-coded scan data that follows (`_read_segments` keeps that in
    the same segment's span, but not in `payload`).
    """
    if len(payload) < 1:
        return False
    ns = payload[0]
    return len(payload) == 4 + 2 * ns


def _dqt_payload_len_ok(payload: bytes) -> bool:
    """`DQT`: one or more back-to-back tables, each Pq/Tq(1) + 64 values
    (1 byte each if Pq=0, 2 if Pq=1) -- 65 or 129 bytes."""
    pos, n = 0, len(payload)
    while pos < n:
        pq = payload[pos] >> 4
        table_size = 1 + (128 if pq else 64)
        if pos + table_size > n:
            return False
        pos += table_size
    return pos == n


def _dht_payload_len_ok(payload: bytes) -> bool:
    """`DHT`: one or more back-to-back tables, each Tc/Th(1) + 16 code-length
    counts + sum(counts) values -- `17 + sum(counts)` bytes."""
    pos, n = 0, len(payload)
    while pos < n:
        if pos + 17 > n:
            return False
        table_size = 17 + sum(payload[pos + 1 : pos + 17])
        if pos + table_size > n:
            return False
        pos += table_size
    return pos == n


def _demoted(span: Span, name: str, payload: bytes) -> Block:
    """A segment a decoder needs but that isn't in its standard form -- a
    `SOF`/`SOS`/`DQT`/`DHT` whose declared length disagrees with what its
    own content implies (ruling R15), or a non-standard `DAC` in an
    arithmetic-coded frame (ruling R19): kept, byte-for-byte, since
    dropping or rejecting it could break decoding -- but demoted to a
    non-removable `UNKNOWN` block tagged `f"{name}?"`, so `verify`'s full
    signature scan (spec section 8) covers it instead of trusting it like
    a real, internally-consistent one.
    """
    tag = f"{name}?"
    label = f"Non-standard {name} segment"
    return Block(
        Kind.UNKNOWN, span, label, tag,
        findings=[Finding(Category.UNKNOWN, label, None, tag, len(payload))],
        removable=False,
    )


def _classify_dac(span: Span, payload: bytes, arithmetic: bool) -> Block:
    """`DAC`, classified against the file's one frame (ruling R19).

    In a Huffman-coded frame no decoder reads a `DAC`: a removable
    `UNKNOWN` block ("Unused DAC segment"), dropped. In an arithmetic-coded
    frame it is `ESSENTIAL` in its standard form (`_dac_payload_ok`), and
    otherwise kept but demoted to `"DAC?"` (`_demoted`) -- dropping a
    conditioning table a decoder does read could break decoding.
    """
    if not arithmetic:
        return Block(Kind.UNKNOWN, span, "Unused DAC segment", "DAC", removable=True)
    if not _dac_payload_ok(payload):
        return _demoted(span, "DAC", payload)
    return Block(Kind.ESSENTIAL, span, "Arithmetic Coding Conditioning", "DAC")


def _dac_payload_ok(payload: bytes) -> bool:
    """`DAC`: 1-8 two-byte entries, each Tc/Tb(1) + Cs(1), with Tc in {0, 1},
    Tb <= 3 and no (Tc, Tb) table conditioned twice."""
    if len(payload) % 2 or not 2 <= len(payload) <= 16:
        return False
    tables = {(payload[k] >> 4, payload[k] & 0x0F) for k in range(0, len(payload), 2)}
    return len(tables) == len(payload) // 2 and all(tc <= 1 and tb <= 3 for tc, tb in tables)


def _classify_app(code: int, span: Span, payload: bytes) -> Block:
    """Classify an APP0-APP15 segment by its content signature.

    Any APPn whose payload doesn't match a known signature (including
    every APP3-APP10, and an APP14 that doesn't start with `b"Adobe"`)
    falls back to an `UNKNOWN`, removable block tagged `f"APP{n}"` --
    table 7.1's "Demais APPn -> unknown removivel". A JFIF `APP0`, an
    `APP1` Exif, an `ICC_PROFILE` `APP2` and an Adobe-signature `APP14`
    never reach here: `_classify_segments` intercepts them directly, since
    classifying each needs cross-segment state.
    """
    n = code - 0xE0
    if code == 0xE0 and payload.startswith(_JFXX_SIG):
        return Block(
            Kind.METADATA, span, "JFIF extension thumbnail", "APP0:JFXX",
            findings=[Finding(Category.EXTRAS, "JFXX thumbnail", None, "APP0:JFXX", len(payload))],
        )
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
            findings=[Finding(Category.EXTRAS, "MPF index", None, "APP2:MPF", len(payload))],
        )
    if code == 0xEB and payload.startswith(_JUMBF_CI):
        return Block(
            Kind.METADATA, span, "JUMBF/C2PA manifest", "APP11:JUMBF",
            findings=c2pa_findings(payload, "APP11/JUMBF"),
        )
    if code == 0xEC:
        return Block(
            Kind.METADATA, span, "APP12 data", "APP12",
            findings=[Finding(Category.SOFTWARE, "APP12 data", None, "APP12", len(payload))],
        )
    if code == 0xED and payload.startswith(_IPTC_SIG):
        return Block(
            Kind.METADATA, span, "Photoshop IRB", "APP13:IRB",
            findings=iptc_findings(payload, "APP13/IRB"),
        )
    return Block(Kind.UNKNOWN, span, f"Unknown APP{n}", f"APP{n}", removable=True)


def _classify_adobe(span: Span, payload: bytes, already_have_standard: bool) -> Block:
    """APP14 `Adobe`: `ESSENTIAL` only for the FIRST occurrence, and only at
    its one fixed 12-byte payload (ruling R15: a *repeated* Adobe APP14,
    even at that same legal size, is not trusted either -- only the first
    one is what any real decoder honors, and a reviewer showed that six
    12-byte Adobe segments could each carry a slice of a hidden secret
    while every one of them passed a size-only check).

    Any other size, or any Adobe APP14 after the first standard one, is
    kept -- dropping it would shift a CMYK JPEG's colors -- but demoted to
    a non-removable `UNKNOWN` block tagged plainly `"APP14"`, not
    `"APP14:Adobe"`, so it reads as the same non-standard structure either
    way and is covered by `verify`'s full signature scan (spec section 8)
    rather than trusted like the one real Adobe marker.
    """
    if not already_have_standard and len(payload) == _ADOBE_PAYLOAD_LEN:
        return Block(Kind.ESSENTIAL, span, "Adobe color transform", "APP14:Adobe")
    label = "Non-standard Adobe segment"
    return Block(
        Kind.UNKNOWN, span, label, "APP14",
        findings=[Finding(Category.UNKNOWN, label, None, "APP14", len(payload))],
        removable=False,
    )


def _classify_jfif(span: Span, payload: bytes) -> Block:
    """JFIF header: `appearance`; a thumbnail adds `extras` and makes it rewritable."""
    has_thumbnail = len(payload) > 14
    findings = [Finding(Category.APPEARANCE, "JFIF header", None, "APP0:JFIF", min(len(payload), 14))]
    if has_thumbnail:
        findings.append(Finding(Category.EXTRAS, "JFIF thumbnail", None, "APP0:JFIF", len(payload) - 14))
    return Block(
        Kind.METADATA, span, "JFIF header", "APP0:JFIF", findings=findings,
        rewritable=has_thumbnail, raw=payload if has_thumbnail else b"",
    )


def _classify_exif(span: Span, payload: bytes) -> Block:
    """APP1 Exif: always rewritable; `raw` holds the TIFF bytes (header stripped)."""
    tiff = strip_exif_header(payload)
    findings = parse_exif(tiff, "APP1/Exif")
    return Block(Kind.METADATA, span, "EXIF data", "APP1:Exif", findings=findings, rewritable=True, raw=tiff)


def _extra(span: Span, tag: str, label: str, payload: bytes) -> Block:
    """A segment a file may hold only once, after its first: a removable `UNKNOWN` block (rulings R24, R25).

    It keeps its tag and gets one finding naming it, so reports show it,
    and it is dropped in every mode.
    """
    return Block(
        Kind.UNKNOWN, span, label, tag, findings=[Finding(Category.UNKNOWN, label, None, tag, len(payload))],
        removable=True,
    )


def _select_icc_profile(segments: list[tuple[Span, int, bytes]]) -> tuple[bytes | None, frozenset[int]]:
    """The file's one ICC profile and the indices (into `segments`) of the chunks carrying it (ruling R26).

    Each `ICC_PROFILE` APP2 payload is the 12-byte signature, a 1-byte seq,
    a 1-byte count, then its slice of the profile. As libjpeg's own reader
    allows, a profile's chunks may sit anywhere in the file and in any
    order. Chunks are taken in file order: a chunk with 1 <= seq <= count
    joins the open profile of its count, unless that seq is already filled
    (a chunk without seq/count bytes or with a seq outside 1..count joins
    nothing); once seq 1..count are all filled, the slices joined in seq
    order must be a structurally valid profile (`icc_profile_valid`,
    ruling R20). The first profile completed that way which is valid is
    the file's profile; a complete but invalid one is discarded. Every
    other `ICC_PROFILE` chunk -- another profile, a repeat, a stray -- is
    not part of it. `(None, frozenset())` when no valid profile completes.
    """
    open_by_count: dict[int, dict[int, int]] = {}  # count -> {seq: segment index}
    for index, (_, code, payload) in enumerate(segments):
        if code != 0xE2 or not payload.startswith(_ICC_SIG) or len(payload) < _ICC_HEADER_LEN:
            continue
        seq, count = payload[_ICC_HEADER_LEN - 2], payload[_ICC_HEADER_LEN - 1]
        if not 1 <= seq <= count:
            continue
        chunks = open_by_count.setdefault(count, {})
        if seq in chunks:
            continue
        chunks[seq] = index
        if len(chunks) == count:
            del open_by_count[count]
            profile = b"".join(segments[chunks[s]][2][_ICC_HEADER_LEN:] for s in range(1, count + 1))
            if icc_profile_valid(profile):
                return profile, frozenset(chunks.values())
    return None, frozenset()


def _classify_icc_run(
    segments: list[tuple[Span, int, bytes]], i: int, profile: bytes | None, carriers: frozenset[int], first: bool
) -> tuple[Block, int]:
    """One Block for the run of adjacent `ICC_PROFILE` APP2 chunks starting at `i`
    that all do, or all don't, carry the file's profile (`carriers`, from
    `_select_icc_profile`).

    Real encoders write a large profile as back-to-back APP2 chunks (spec
    7.1: "inclusive multissegmento"), so a run is normally the whole
    profile. A run carrying it is `appearance`: the file's `first` such run
    carries the "ICC profile" finding and exposes the whole profile as
    `logical`; a later one -- chunks apart in the file -- carries "ICC
    profile (continued)". Any other run is a removable `UNKNOWN` block,
    dropped: "Extra ICC profile" (with a finding) when the file has a
    profile -- at most one is kept (ruling R26) -- else "Invalid ICC
    profile", the broken profile. Returns `(block, index after the run)`.
    """
    carries = i in carriers
    first_span = last_span = segments[i][0]
    data_size = 0
    j = i
    while j < len(segments):
        span, code, payload = segments[j]
        if code != 0xE2 or not payload.startswith(_ICC_SIG) or (j in carriers) != carries:
            break
        data_size += max(len(payload) - _ICC_HEADER_LEN, 0)
        last_span = span
        j += 1
    span = Span(first_span.start, last_span.end)

    if profile is None:
        return Block(Kind.UNKNOWN, span, "Invalid ICC profile", "APP2:ICC", removable=True), j
    if not carries:
        label = "Extra ICC profile"
        finding = Finding(Category.UNKNOWN, label, None, "APP2:ICC", data_size)
        return Block(Kind.UNKNOWN, span, label, "APP2:ICC", findings=[finding], removable=True), j
    if first:
        finding = Finding(Category.APPEARANCE, "ICC profile", icc_description(profile), "APP2:ICC", len(profile))
        return Block(Kind.METADATA, span, "ICC color profile", "APP2:ICC", findings=[finding], logical=profile), j
    finding = Finding(Category.APPEARANCE, "ICC profile (continued)", None, "APP2:ICC", data_size)
    return Block(Kind.METADATA, span, "ICC color profile (continued)", "APP2:ICC", findings=[finding]), j


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
        findings=[Finding(Category.EXTRAS, label, None, TRAILING_TAG, len(data))],
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
