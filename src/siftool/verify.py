"""Fail-closed post-clean verification (spec section 8).

`verify_file` re-parses a file with the same handlers used to clean it and
never raises for a content problem: any read/parse failure degrades to
`Verdict(FAILED, ...)` with a note naming what could not be read
(`"Cannot read file: ..."`, `"Cannot read original: ..."`). It backs both
the public `api.verify` (a direct check on any file) and `api.clean`'s
"Verified Clean" seal, which calls it on the temporary output before ever
replacing the destination.

`block_findings` (controller ruling R11, extended by R13) is the single
place that decides what a `Block` "shows up as" for reporting/verification
purposes: a format handler can leave a non-`ESSENTIAL` block with no
`Finding` of its own -- an `UNKNOWN` block with none of its own kind
(`jpeg.py`'s bare "Unknown marker"/"Unknown APPn", `png.py`'s and
`webp.py`'s "Unknown chunk"), or a `METADATA` block whose reader simply
found nothing to report (e.g. an EXIF/`eXIf`/`EXIF` block wrapping a
syntactically valid but tag-less TIFF -- an IFD0 with zero entries).
Without this helper, either case would be invisible to `inspect`'s report,
`clean`'s removed/kept split and, worst of all, `verify`'s violation check
-- a removable-but-unremoved leftover block would otherwise silently pass
as VERIFIED.

What the seal enforces (spec section 8, "Limites"; ruling R21):

- Structures. Every handler validates the structures it trusts as
  `ESSENTIAL` and rejects, drops or demotes to a scanned `UNKNOWN` block
  whatever isn't in its standard shape (rulings R14-R20), so each
  remaining non-`ESSENTIAL` block must show only findings the mode
  allows; a block kept with a bad CRC (`Block.corrupt`) is a violation too.
- Signatures (`scan_signatures`). Every non-`ESSENTIAL` block's bytes, and
  any content a handler reassembled or inflated for it (`Block.logical`),
  are searched for every entry of `SIGNATURES`. `ESSENTIAL` spans --
  headers, tables and the compressed image data -- and kept ICC profiles
  are searched for `LONG_SIGNATURES` only, where a 3- or 4-byte signature
  would match by chance.

Known limit: arbitrary text hidden in the values of a valid essential
structure (a flag bit, a quantization value, an unused table) or in the
compressed image data is not detected unless it contains one of the long
signatures, and pixel-level steganography is not detected at all: the
"Verified Clean" seal guarantees the absence of non-standard metadata
structures and of the long signatures, not the absence of every hidden bit
in the image itself. Follow-ups for M2: data after the end of a PNG's
zlib stream (`unused_data`) and unreferenced JPEG DHT/DQT tables.
"""

from __future__ import annotations

import re

from .detect import detect
from .errors import SiftoolError
from .model import Block, Category, Container, Finding, Kind, Mode, Status, Verdict
from .modes import ALLOWED
from .safe_io import load

_Signature = tuple["bytes | re.Pattern[bytes]", Category, str]

# spec section 8 step 4: byte signatures searched for regardless of what a
# handler did or didn't classify there. The long ones are specific enough
# never to match by chance in compressed image data (the review measured
# 0 hits in 60.9 MiB of clean images), so they are searched everywhere,
# ESSENTIAL spans and kept ICC profiles included (ruling R21).
LONG_SIGNATURES: list[_Signature] = [
    (b"Exif\x00\x00", Category.UNKNOWN, "EXIF header"),
    (b"<x:xmpmeta", Category.SOFTWARE, "XMP packet"),
    (b"http://ns.adobe.com/xap/1.0/", Category.SOFTWARE, "XMP namespace"),
    (b"jumdc2pa", Category.AI_PROVENANCE, "C2PA manifest box"),
    (re.compile(rb"[+-]\d{2}\.\d+[+-]\d{3}\.\d+"), Category.LOCATION, "ISO 6709 coordinates"),
]

# The short ones would match random bytes: searched only in the other,
# non-ESSENTIAL blocks.
SHORT_SIGNATURES: list[_Signature] = [
    (b"jumb", Category.AI_PROVENANCE, "JUMBF box"),
    (b"c2pa", Category.AI_PROVENANCE, "C2PA label"),
    (b"caBX", Category.AI_PROVENANCE, "C2PA chunk"),
    (b"GPS", Category.LOCATION, "GPS marker"),
]

SIGNATURES: list[_Signature] = LONG_SIGNATURES + SHORT_SIGNATURES

# Block tags whose own job is to carry the EXIF header -- `scan_signatures`
# never flags "EXIF header" found inside one of these, since that is
# exactly where a legitimately-kept EXIF block's own signature belongs.
EXIF_BLOCK_TAGS = frozenset({"APP1:Exif", "eXIf", "EXIF"})

# Block tags of ICC profiles: when kept (METADATA), scanned -- raw bytes and
# `Block.logical` alike -- for LONG_SIGNATURES only, since a large profile's
# binary tables can hold a bare "GPS" by chance (ruling R20).
ICC_BLOCK_TAGS = frozenset({"APP2:ICC", "iCCP", "ICCP"})


def block_findings(block: Block) -> list[Finding]:
    """Return what `block` "shows up as": its own findings, or a synthesized one.

    A format handler can leave a non-`ESSENTIAL` block -- `METADATA` or
    `UNKNOWN` -- with no `Finding` of its own; this returns a single
    `[Finding(Category.UNKNOWN, block.label, None, block.tag,
    block.span.length)]` for that case, so the block is never silently
    invisible to a report or a verification check (controller ruling R11,
    extended by R13 to cover a findings-less `METADATA` block too -- e.g.
    a leftover EXIF shell with a tag-less TIFF). An `ESSENTIAL` block's
    own (always-empty) `findings` is returned unchanged: it is never a
    "leftover", so it is never synthesized. Every other block's own,
    non-empty `findings` is likewise returned unchanged.
    """
    if block.findings or block.kind is Kind.ESSENTIAL:
        return block.findings
    return [Finding(Category.UNKNOWN, block.label, None, block.tag, block.span.length)]


def _found(pattern: bytes | re.Pattern[bytes], data: bytes, start: int, end: int) -> bool:
    """True if `pattern` occurs in `data[start:end]` (searched in place, never copied)."""
    if isinstance(pattern, re.Pattern):
        return pattern.search(data, start, end) is not None
    return data.find(pattern, start, end) >= 0


def scan_signatures(container: Container) -> list[Finding]:
    """Search every block for a leftover signature (spec section 8 step 4, ruling R21).

    A raw byte search, independent of, and in addition to, whatever a
    handler did or didn't classify there, over each block's span in
    `container.source` and over its `Block.logical` content when a handler
    reassembled or inflated one (ruling R20). `ESSENTIAL` blocks and kept
    ICC blocks (`ICC_BLOCK_TAGS`) are searched for `LONG_SIGNATURES` only;
    every other block for all of `SIGNATURES`. The `"EXIF header"`
    signature is ignored inside a block tagged `EXIF_BLOCK_TAGS`, since
    that is a legitimately-kept EXIF block's own header, not a leftover
    trace. Each signature found in a block becomes one
    `Finding(category, label, where=block.tag)`.
    """
    findings: list[Finding] = []
    for block in container.blocks:
        long_only = block.kind is Kind.ESSENTIAL or (block.kind is Kind.METADATA and block.tag in ICC_BLOCK_TAGS)
        regions = [(container.source, block.span.start, block.span.end)]
        if block.logical is not None:
            regions.append((block.logical, 0, len(block.logical)))
        for pattern, category, label in LONG_SIGNATURES if long_only else SIGNATURES:
            if label == "EXIF header" and block.tag in EXIF_BLOCK_TAGS:
                continue
            if any(_found(pattern, data, start, end) for data, start, end in regions):
                findings.append(Finding(category, label, where=block.tag))
    return findings


def verify_file(path, mode: Mode = Mode.PRIVACY, original=None) -> Verdict:
    """Verify `path` per spec section 8; never raises for a content problem.

    Parses `path` with the same handler `clean` would use -- `SiftoolError`
    or `OSError` (unreadable, corrupt, unsupported, missing, ...) yields
    `Verdict(FAILED, [], [f"Cannot read file: {e}"])` rather than raising.
    Every remaining block's findings (via `block_findings`) whose category
    isn't allowed by `mode` are violations; so is every block kept with a
    bad CRC (`Finding(UNKNOWN, "Corrupt structure", None, tag, size)`,
    review M3) and every signature `scan_signatures` finds. A non-removable
    `UNKNOWN` block left in place is not a violation but produces a
    `f"Unrecognized structure kept: {tag}"` note instead. With `original`,
    the two files' `essential_digest` are compared when (and only when)
    they share the same format -- a mismatch adds the note `"Image data
    differs from the original"`, and a format mismatch is itself a
    violation; an original that can't be read ends the check with the note
    `f"Cannot read original: {e}"` (ruling R23). Status is `FAILED` if
    there is any violation, the digests differ or the original can't be
    read, else `WARNINGS` if there is any note, else `VERIFIED`.
    """
    try:
        buf = load(path)
        handler = detect(buf)
        container = handler.parse(buf)
    except (SiftoolError, OSError) as e:
        return Verdict(Status.FAILED, [], [f"Cannot read file: {e}"])

    ok = ALLOWED[mode]
    violations: list[Finding] = []
    notes: list[str] = []

    # A remaining, removable UNKNOWN block is a violation via its (possibly
    # synthesized) Finding -- Category.UNKNOWN is never in any mode's
    # whitelist. A non-removable one is excluded from that check and gets
    # a WARNINGS note instead (spec 8.3). Trailing data needs no special
    # case: it is always Category.EXTRAS, which -- like UNKNOWN -- is never
    # allowed by any mode, so it is already caught by the same rule.
    for block in container.blocks:
        if block.corrupt:
            violations.append(Finding(Category.UNKNOWN, "Corrupt structure", None, block.tag, block.span.length))
        if block.kind is Kind.UNKNOWN and not block.removable:
            notes.append(f"Unrecognized structure kept: {block.tag}")
            continue
        for finding in block_findings(block):
            if finding.category not in ok:
                violations.append(finding)

    violations.extend(scan_signatures(container))

    essential_match: bool | None = None
    if original is not None:
        try:
            obuf = load(original)
            ohandler = detect(obuf)
            ocontainer = ohandler.parse(obuf)
        except (SiftoolError, OSError) as e:
            notes.append(f"Cannot read original: {e}")
            return Verdict(Status.FAILED, violations, notes)
        if container.format == ocontainer.format:
            essential_match = handler.essential_digest(container) == ohandler.essential_digest(ocontainer)
            if not essential_match:
                notes.append("Image data differs from the original")
        else:
            essential_match = False
            violations.append(
                Finding(
                    Category.UNKNOWN,
                    "Format mismatch against original",
                    f"{container.format} vs {ocontainer.format}",
                )
            )

    if violations or essential_match is False:
        status = Status.FAILED
    elif notes:
        status = Status.WARNINGS
    else:
        status = Status.VERIFIED

    return Verdict(status, violations, notes, essential_match)
