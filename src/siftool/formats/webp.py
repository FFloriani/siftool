"""Lossless WEBP handler: parse / write / essential digest / rewrite.

A WEBP file is a RIFF container: `b"RIFF" + <u32 LE size> + b"WEBP"` followed
by a sequence of chunks, each `fourcc(4) + <u32 LE size> + data + pad(0-1)`
(a single zero pad byte when `size` is odd). The RIFF header's own `size`
counts everything from right after itself to the end of the container --
`b"WEBP"` plus every chunk, including their headers and padding; anything
beyond `8 + size` bytes into the file is a `TRAILING` block (spec 7.3).

Chunks that define how pixels decode -- `VP8 `/`VP8L` (the single-frame
bitstream), `VP8X` (the extended-features header), `ALPH` (alpha plane) and
`ANIM`/`ANMF` (animation parameters/frames) -- are `ESSENTIAL`: `write`
always copies them byte-for-byte from the source (except `VP8X`'s own
flags byte, below), so a lossless image and every frame of an animation
survive bit-identical -- the old engine's worst failure mode, since
re-encoding through Pillow turned lossless into lossy and dropped every
frame but the first. Everything else is metadata, classified per spec
table 7.3: `ICCP` is `appearance`; `EXIF` is rewritable, minimized the same
way as JPEG/PNG's; `"XMP "` and `C2PA` are always removed in every mode
(XMP's generic "software" finding and C2PA's `ai_provenance` are never in
any mode's whitelist); anything else is unknown and removable.

`EXIF` follows the same minimize-and-keep rule as JPEG/PNG (spec section 6:
`appearance` -- orientation, color -- is kept by every mode except MAXIMUM,
for WEBP exactly as for the other two formats): `rewrite` keeps whatever
`build_minimal_exif` returns for the mode's whitelist, dropping the chunk
only when nothing at all is left to keep. Mode policy lives entirely in
`modes.ALLOWED` and the planner, never inside this handler.

`write` recomputes the RIFF size and rewrites `VP8X`'s one flags byte
(the `ICC`/`EXIF`/`XMP` bits -- `VP8X_ICC`/`VP8X_EXIF`/`VP8X_XMP`) to match
whichever of ICCP/EXIF/XMP are still present in the output; every other
bit (alpha, animation, reserved) passes through unchanged. `essential_digest`
masks those same three bits out of `VP8X` before hashing, so cleaning --
which only ever touches metadata chunks and this one flags byte -- never
changes the digest.

An `ESSENTIAL` chunk is copied byte-for-byte and never gets `verify`'s
full signature scan (spec section 8), so a chunk is trusted as
`ESSENTIAL` only in exactly the shape a decoder uses (Task 9b, rulings R15
and R19):

- `VP8X`/`ANIM` have one fixed size (10/6 bytes); `VP8X` is optional but,
  if present, is the container's very first chunk (so at most once);
  `ANIM` appears at most once and only when `VP8X`'s animation flag
  (`VP8X_ANIM`, bit `0x02`) is set. Any violation is `CorruptFileError`
  (`_read_raw_chunks`, `_check_structure`).
- A still image has exactly one top-level bitstream (`VP8 `/`VP8L`, the
  first one); an `ALPH` is trusted only immediately before that bitstream,
  and only if it is a `VP8 `. Every other `VP8 `/`VP8L`/`ALPH`, any of them
  at the top level of an animation, and any `ANMF` in a still image are
  chunks decoders ignore: removable `UNKNOWN` blocks ("Unused <TAG>
  chunk"), dropped by `clean`.
- An `ANMF` is a 16-byte frame header followed by frame data that is
  exactly `ALPH` + `VP8 `, `VP8 ` or `VP8L`, with every sub-chunk size and
  pad byte inside the frame and nothing after (`_anmf_valid`). Any other
  frame is kept -- dropping it would break the animation -- but demoted to
  a non-removable `UNKNOWN` block tagged `"ANMF?"`: WARNINGS, and scanned
  for every signature instead of trusted.
- A file without image data -- a RIFF size under 12, no bitstream in a
  still image, no `ANMF` in an animation -- is `CorruptFileError("missing
  image data")`.

An `ICCP` is kept as `appearance` only if it is a structurally valid ICC
profile (`icc_profile_valid`, ruling R20); otherwise it is a removable
`UNKNOWN` block ("Invalid ICC profile"), dropped with the image's broken
profile.

Robustness is a hard requirement, same as every other handler: a
truncated chunk header, a chunk whose declared size runs past the RIFF's
own declared end, or a RIFF size that runs past the actual file are all
`CorruptFileError`, never an unguarded exception.
"""

from __future__ import annotations

import hashlib
import io
import struct
from typing import BinaryIO

from ..detect import register
from ..errors import CorruptFileError
from ..metadata.c2pa import c2pa_findings
from ..metadata.exif import build_minimal_exif, parse_exif, strip_exif_header
from ..metadata.icc import icc_description, icc_profile_valid
from ..metadata.xmp import xmp_findings
from ..model import Action, Block, Category, Container, Finding, Kind, Plan, Span, TRAILING_TAG
from .base import copy_span

# VP8X flags byte (spec 7.3 / WebP container spec), MSB to LSB:
# Rsv Rsv ICC Alpha EXIF XMP Anim Rsv.
VP8X_ICC = 0x20
VP8X_EXIF = 0x08
VP8X_XMP = 0x04
VP8X_ANIM = 0x02
_VP8X_META_MASK = VP8X_ICC | VP8X_EXIF | VP8X_XMP

# Chunks that decode pixels, per spec table 7.3 -- copied byte-for-byte by
# `write` (VP8X aside) when `_classify_chunks` trusts them. Block.tag/
# Finding.where for every chunk classified below is its fourcc with any
# padding space stripped ("fourcc sem espaços").
_ESSENTIAL_LABELS: dict[bytes, str] = {
    b"VP8X": "Extended features header",
    b"VP8L": "Lossless image data",
    b"VP8 ": "Lossy image data",
    b"ALPH": "Alpha channel data",
    b"ANIM": "Animation parameters",
    b"ANMF": "Animation frame",
}
_ESSENTIAL_TYPES = frozenset(_ESSENTIAL_LABELS)
_BITSTREAMS = frozenset({b"VP8 ", b"VP8L"})

# Essential chunks whose payload size the format itself fixes (Task 9b):
# any other size is CorruptFileError. The bitstreams and ALPH have no fixed
# size (their placement is what `_classify_chunks` checks), and an ANMF's
# frame header and sub-chunks are checked by `_anmf_valid`.
_FIXED_ESSENTIAL_SIZES: dict[bytes, int] = {b"VP8X": 10, b"ANIM": 6}

# ANMF payload: a 16-byte frame header (X/Y offset, width-1, height-1,
# duration: 3 bytes each; flags: 1 byte), then the frame data -- which a
# decoder only ever uses in one of these sub-chunk layouts (ruling R19).
_ANMF_HEADER_LEN = 16
_ANMF_FRAME_LAYOUTS = frozenset({(b"VP8 ",), (b"ALPH", b"VP8 "), (b"VP8L",)})

# tag (space-stripped) -> the VP8X bit it controls, for recomputing flags.
_FLAG_BY_TAG: dict[str, int] = {"ICCP": VP8X_ICC, "EXIF": VP8X_EXIF, "XMP": VP8X_XMP}


class WebpHandler:
    """Recognizes, parses, rewrites and writes lossless WEBP files."""

    name = "webp"
    extensions = (".webp",)

    def sniff(self, head: bytes) -> bool:
        return head[:4] == b"RIFF" and head[8:12] == b"WEBP"

    def parse(self, buf: bytes) -> Container:
        blocks, riff_end = _read_chunks(buf)
        if riff_end < len(buf):
            blocks.append(_trailing_block(buf, riff_end, len(buf)))
        return Container(format="webp", blocks=blocks, source=buf, positional=False)

    def write(self, container: Container, plan: Plan, out: BinaryIO) -> None:
        extra_flags = _surviving_metadata_flags(container, plan)
        body = io.BytesIO()
        for index, block in enumerate(container.blocks):
            action = plan.actions[index]
            if action == Action.KEEP:
                if block.tag == "VP8X":
                    body.write(_patched_vp8x(container.source, block, extra_flags))
                else:
                    copy_span(container.source, block.span, body)
            elif action == Action.REWRITE:
                body.write(plan.replacements[index])
            # DROP: write nothing. ZERO never occurs for WEBP (positional=False).
        payload = body.getvalue()
        out.write(b"RIFF" + struct.pack("<I", len(payload) + 4) + b"WEBP" + payload)

    def essential_digest(self, container: Container) -> str:
        digest = hashlib.sha256()
        for block in container.blocks:
            if block.kind is not Kind.ESSENTIAL:
                continue
            chunk = bytearray(container.source[block.span.start : block.span.end])
            if block.tag == "VP8X":
                chunk[8] &= ~_VP8X_META_MASK & 0xFF
            digest.update(bytes(chunk))
        return digest.hexdigest()

    def rewrite(self, block: Block, allowed: frozenset[Category]) -> bytes | None:
        if block.tag == "EXIF":
            return _rewrite_exif(block, allowed)
        return None


# ---------------------------------------------------------------------------
# Reading: RIFF chunk stream -> Blocks
# ---------------------------------------------------------------------------


def _read_chunks(buf: bytes) -> tuple[list[Block], int]:
    """Tile `buf` into classified `Block`s, from right after `WEBP` to the
    RIFF's own declared end. Returns `(blocks, riff_end)`; the caller
    treats anything from `riff_end` to `len(buf)` as trailing data. Never
    raises anything but `CorruptFileError` on a malformed structure.

    A RIFF size under 12 leaves no room for even one chunk header after
    `WEBP`: the image itself would be read as trailing data, so it is
    `CorruptFileError("missing image data")` (ruling R19). Chunks are read
    first (`_read_raw_chunks`), then classified with the whole file in view
    (`_classify_chunks`), since trusting an `ALPH` depends on the chunk
    after it.
    """
    n = len(buf)
    if buf[:4] != b"RIFF" or buf[8:12] != b"WEBP":
        raise CorruptFileError("not a WEBP file: bad RIFF/WEBP signature")

    riff_size = int.from_bytes(buf[4:8], "little")
    riff_end = 8 + riff_size
    if riff_end > n:
        raise CorruptFileError("RIFF declares a size larger than the file")
    if riff_size < 12:
        raise CorruptFileError("missing image data")

    chunks, vp8x_flags = _read_raw_chunks(buf, riff_end)
    animated = vp8x_flags is not None and bool(vp8x_flags & VP8X_ANIM)
    return _classify_chunks(chunks, animated), riff_end


def _read_raw_chunks(buf: bytes, riff_end: int) -> tuple[list[tuple[Span, bytes, bytes]], int | None]:
    """Return `([(span, fourcc, payload), ...], vp8x_flags)` for every top-level chunk.

    Enforces, in file order, the count/order rules (`_check_structure`,
    ruling R15) and the fixed sizes of `VP8X`/`ANIM`
    (`_FIXED_ESSENTIAL_SIZES`, Task 9b), so the first violation in the file
    is the one raised. `vp8x_flags` is `VP8X`'s flags byte, or None when
    the file has no `VP8X`.
    """
    chunks: list[tuple[Span, bytes, bytes]] = []
    p = 12
    vp8x_flags: int | None = None
    seen_anim = False
    while p < riff_end:
        if p + 8 > riff_end:
            raise CorruptFileError("truncated WEBP chunk header")
        fourcc = buf[p : p + 4]
        size = int.from_bytes(buf[p + 4 : p + 8], "little")
        data_start = p + 8
        data_end = data_start + size
        if data_end > riff_end:
            raise CorruptFileError(f"chunk {fourcc!r} exceeds RIFF end")
        pad = 1 if size % 2 and data_end + 1 <= riff_end else 0
        chunk_end = data_end + pad
        payload = buf[data_start:data_end]

        _check_structure(fourcc, len(chunks), vp8x_flags, seen_anim)
        fixed_size = _FIXED_ESSENTIAL_SIZES.get(fourcc)
        if fixed_size is not None and size != fixed_size:
            raise CorruptFileError(f"invalid {_tag(fourcc)}")
        if fourcc == b"VP8X":
            vp8x_flags = payload[0]
        elif fourcc == b"ANIM":
            seen_anim = True

        chunks.append((Span(p, chunk_end), fourcc, payload))
        p = chunk_end
    return chunks, vp8x_flags


def _check_structure(fourcc: bytes, index: int, vp8x_flags: int | None, seen_anim: bool) -> None:
    """Count/order rules for `VP8X`/`ANIM` (ruling R15).

    `VP8X` is optional, but if present must be the container's very first
    top-level chunk (so "at most once" follows for free: any later `VP8X`
    is already caught by not being first). `ANIM` may appear at most once,
    and only once a `VP8X` with its animation flag set has already been
    seen. `CorruptFileError("invalid <TYPE>")` on violation -- the same
    message a wrong size would raise, since both are the same underlying
    problem: a structure trusted as `ESSENTIAL` (and so never fully
    signature-scanned) that isn't actually what it claims to be.
    """
    if fourcc == b"VP8X" and index != 0:
        raise CorruptFileError("invalid VP8X")
    if fourcc == b"ANIM" and (seen_anim or vp8x_flags is None or not vp8x_flags & VP8X_ANIM):
        raise CorruptFileError("invalid ANIM")


def _tag(fourcc: bytes) -> str:
    return fourcc.decode("ascii", errors="replace").rstrip(" ")


def _classify_chunks(chunks: list[tuple[Span, bytes, bytes]], animated: bool) -> list[Block]:
    """Classify every top-level chunk, per spec table 7.3 and ruling R19.

    In a still image the first `VP8 `/`VP8L` is the image and an `ALPH` is
    its alpha plane only when it comes immediately before it and it is a
    `VP8 `; in an animation the image is the `ANMF` frames (`_anmf_valid`,
    else `_demoted_anmf`). Every other bitstream/`ALPH`/`ANMF` becomes a
    removable "Unused" block (`_unused`). Raises `CorruptFileError("missing
    image data")` when a still image has no bitstream or an animation no
    `ANMF` at all.
    """
    blocks: list[Block] = []
    have_bitstream = False
    for i, (span, fourcc, payload) in enumerate(chunks):
        if fourcc in _BITSTREAMS:
            trusted = not animated and not have_bitstream
            have_bitstream = have_bitstream or trusted
            blocks.append(_essential(span, fourcc) if trusted else _unused(span, fourcc))
        elif fourcc == b"ALPH":
            next_fourcc = chunks[i + 1][1] if i + 1 < len(chunks) else None
            trusted = not animated and not have_bitstream and next_fourcc == b"VP8 "
            blocks.append(_essential(span, fourcc) if trusted else _unused(span, fourcc))
        elif fourcc == b"ANMF":
            if not animated:
                blocks.append(_unused(span, fourcc))
            elif _anmf_valid(payload):
                blocks.append(_essential(span, fourcc))
            else:
                blocks.append(_demoted_anmf(span, payload))
        elif fourcc in _ESSENTIAL_TYPES:  # VP8X, ANIM: checked by _read_raw_chunks
            blocks.append(_essential(span, fourcc))
        else:
            blocks.append(_classify_metadata(span, fourcc, payload))

    has_image = any(fourcc == b"ANMF" for _, fourcc, _ in chunks) if animated else have_bitstream
    if not has_image:
        raise CorruptFileError("missing image data")
    return blocks


def _essential(span: Span, fourcc: bytes) -> Block:
    return Block(Kind.ESSENTIAL, span, _ESSENTIAL_LABELS[fourcc], _tag(fourcc))


def _unused(span: Span, fourcc: bytes) -> Block:
    """A bitstream/`ALPH`/`ANMF` chunk no decoder uses where it sits: removable, so dropped."""
    tag = _tag(fourcc)
    return Block(Kind.UNKNOWN, span, f"Unused {tag} chunk", tag, removable=True)


def _anmf_valid(payload: bytes) -> bool:
    """True if `payload` is a 16-byte frame header + exactly one `_ANMF_FRAME_LAYOUTS` layout.

    Every sub-chunk's header, data and pad byte (after an odd size) must
    lie inside the payload, and nothing may follow the last one.
    """
    pos, end = _ANMF_HEADER_LEN, len(payload)
    if end < pos:
        return False
    layout: list[bytes] = []
    while pos < end:
        if pos + 8 > end:
            return False
        size = int.from_bytes(payload[pos + 4 : pos + 8], "little")
        next_pos = pos + 8 + size + size % 2
        if next_pos > end:
            return False
        layout.append(payload[pos : pos + 4])
        pos = next_pos
    return tuple(layout) in _ANMF_FRAME_LAYOUTS


def _demoted_anmf(span: Span, payload: bytes) -> Block:
    """An `ANMF` whose frame data isn't one a decoder uses as-is (ruling R19).

    Kept byte-for-byte -- dropping a frame would break the animation --
    but as a non-removable `UNKNOWN` block tagged `"ANMF?"`, so `clean`
    reports WARNINGS and `verify` scans it for every signature instead of
    trusting it like a real frame.
    """
    label = "Non-standard ANMF chunk"
    return Block(
        Kind.UNKNOWN, span, label, "ANMF?",
        findings=[Finding(Category.UNKNOWN, label, None, "ANMF?", len(payload))],
        removable=False,
    )


def _classify_metadata(span: Span, fourcc: bytes, payload: bytes) -> Block:
    """Classify one top-level chunk that isn't an image chunk, per spec table 7.3.

    An `ICCP` that isn't a structurally valid ICC profile
    (`icc_profile_valid`, ruling R20) is a removable `UNKNOWN` block, so
    the broken profile is dropped rather than kept as `appearance`.
    """
    tag = _tag(fourcc)

    if fourcc == b"ICCP":
        if not icc_profile_valid(payload):
            return Block(Kind.UNKNOWN, span, "Invalid ICC profile", tag, removable=True)
        finding = Finding(Category.APPEARANCE, "ICC profile", icc_description(payload), tag, size=len(payload))
        return Block(Kind.METADATA, span, "ICC color profile", tag, findings=[finding])

    if fourcc == b"EXIF":
        tiff = strip_exif_header(payload)
        return Block(
            Kind.METADATA, span, "EXIF data", tag, findings=parse_exif(tiff, tag), rewritable=True, raw=tiff
        )

    if fourcc == b"XMP ":
        return Block(Kind.METADATA, span, "XMP packet", tag, findings=xmp_findings(payload, tag))

    if fourcc == b"C2PA":
        return Block(Kind.METADATA, span, "C2PA manifest", tag, findings=c2pa_findings(payload, tag))

    return Block(Kind.UNKNOWN, span, f"Unknown chunk {tag}", tag, removable=True)


def _trailing_block(source: bytes, start: int, end: int) -> Block:
    label = "Data after end of image"
    return Block(
        Kind.METADATA, Span(start, end), label, TRAILING_TAG,
        findings=[Finding(Category.EXTRAS, label, where=TRAILING_TAG, size=end - start)],
    )


# ---------------------------------------------------------------------------
# Writing: VP8X flags recompute
# ---------------------------------------------------------------------------


def _surviving_metadata_flags(container: Container, plan: Plan) -> int:
    """OR together the VP8X bit for every ICCP/EXIF/XMP block a KEEP or REWRITE kept."""
    flags = 0
    for index, block in enumerate(container.blocks):
        bit = _FLAG_BY_TAG.get(block.tag)
        if bit and plan.actions[index] in (Action.KEEP, Action.REWRITE):
            flags |= bit
    return flags


def _patched_vp8x(source: bytes, block: Block, extra_flags: int) -> bytes:
    """`block`'s full chunk bytes, with its flags byte reset per spec 7.3."""
    raw = bytearray(source[block.span.start : block.span.end])
    raw[8] = (raw[8] & ~_VP8X_META_MASK & 0xFF) | extra_flags
    return bytes(raw)


# ---------------------------------------------------------------------------
# Rewriting: EXIF -> a reduced replacement chunk
# ---------------------------------------------------------------------------


def _rewrite_exif(block: Block, allowed: frozenset[Category]) -> bytes | None:
    """`b"EXIF" + size(4LE) + build_minimal_exif(...) + pad`, or None.

    Same semantics as JPEG's/PNG's EXIF rewrite (spec section 6:
    `appearance` -- orientation, color -- is kept by every mode except
    MAXIMUM, for WEBP exactly as for JPEG/PNG). Mode policy lives entirely
    in `modes.ALLOWED` and the planner; this always keeps whatever
    `allowed` permits.
    """
    minimal = build_minimal_exif(strip_exif_header(block.raw), allowed)
    if minimal is None:
        return None
    pad = b"\x00" if len(minimal) % 2 else b""
    return b"EXIF" + struct.pack("<I", len(minimal)) + minimal + pad


register(WebpHandler())
