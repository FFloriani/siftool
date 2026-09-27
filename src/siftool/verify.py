"""Fail-closed post-clean verification (spec section 8).

`verify_file` re-parses a file with the same handlers used to clean it and
never raises for a content problem: any read/parse failure degrades to
`Verdict(FAILED, ...)`. It backs both the public `api.verify` (a direct
check on any file) and `api.clean`'s "Verified Clean" seal, which calls it
on the temporary output before ever replacing the destination.

`block_findings` (controller ruling R11) is the single place that decides
what a `Block` "shows up as" for reporting/verification purposes: a format
handler never puts a `Finding` on an `UNKNOWN` block that carries none of
its own (`jpeg.py`'s bare "Unknown marker"/"Unknown APPn", `png.py`'s and
`webp.py`'s "Unknown chunk"), so without this helper such a block would be
invisible to `inspect`'s report, `clean`'s removed/kept split and, worst of
all, `verify`'s violation check -- a removable-but-unremoved unknown block
would otherwise silently pass as VERIFIED.
"""

from __future__ import annotations

import re

from .detect import detect
from .errors import SiftoolError
from .model import Block, Category, Container, Finding, Kind, Mode, Status, Verdict
from .modes import ALLOWED
from .safe_io import load

# spec section 8 step 4: byte signatures searched for outside of ESSENTIAL
# spans, regardless of what a handler did or didn't classify there.
SIGNATURES: list[tuple[bytes | re.Pattern[bytes], Category, str]] = [
    (b"Exif\x00\x00", Category.UNKNOWN, "EXIF header"),
    (b"<x:xmpmeta", Category.SOFTWARE, "XMP packet"),
    (b"jumb", Category.AI_PROVENANCE, "JUMBF box"),
    (b"c2pa", Category.AI_PROVENANCE, "C2PA label"),
    (b"caBX", Category.AI_PROVENANCE, "C2PA chunk"),
    (re.compile(rb"[+-]\d{2}\.\d+[+-]\d{3}\.\d+"), Category.LOCATION, "ISO 6709 coordinates"),
    (b"GPS", Category.LOCATION, "GPS marker"),
]

# Block tags whose own job is to carry the EXIF header -- `scan_signatures`
# never flags "EXIF header" found inside one of these, since that is
# exactly where a legitimately-kept EXIF block's own signature belongs.
EXIF_BLOCK_TAGS = frozenset({"APP1:Exif", "eXIf", "EXIF"})


def block_findings(block: Block) -> list[Finding]:
    """Return what `block` "shows up as": its own findings, or a synthesized one.

    A format handler never attaches a `Finding` to an `UNKNOWN` block that
    has none of its own; this returns a single
    `[Finding(Category.UNKNOWN, block.label, None, block.tag,
    block.span.length)]` for that case, so the block is never silently
    invisible to a report or a verification check (controller ruling R11).
    Every other block's own `findings` (possibly empty, for an `ESSENTIAL`
    block) is returned unchanged.
    """
    if block.findings or block.kind is not Kind.UNKNOWN:
        return block.findings
    return [Finding(Category.UNKNOWN, block.label, None, block.tag, block.span.length)]


def scan_signatures(container: Container) -> list[Finding]:
    """Search every non-`ESSENTIAL` block's own bytes for a leftover `SIGNATURES` match.

    A raw byte scan over `container.source[block.span.start:block.span.end]`
    for every block that isn't `Kind.ESSENTIAL` -- independent of, and in
    addition to, whatever a handler did or didn't classify there. The
    `"EXIF header"` signature is ignored inside a block tagged
    `EXIF_BLOCK_TAGS`, since that is a legitimately-kept EXIF block's own
    header, not a leftover trace. Each match becomes a
    `Finding(category, label, where=block.tag)`.
    """
    findings: list[Finding] = []
    for block in container.blocks:
        if block.kind is Kind.ESSENTIAL:
            continue
        data = container.source[block.span.start : block.span.end]
        for pattern, category, label in SIGNATURES:
            if label == "EXIF header" and block.tag in EXIF_BLOCK_TAGS:
                continue
            matched = pattern.search(data) is not None if isinstance(pattern, re.Pattern) else pattern in data
            if matched:
                findings.append(Finding(category, label, where=block.tag))
    return findings


def verify_file(path, mode: Mode = Mode.PRIVACY, original=None) -> Verdict:
    """Verify `path` per spec section 8; never raises for a content problem.

    Parses `path` with the same handler `clean` would use -- `SiftoolError`
    or `OSError` (unreadable, corrupt, unsupported, missing, ...) yields
    `Verdict(FAILED, [], [f"Cannot read file: {e}"])` rather than raising.
    Every remaining block's findings (via `block_findings`) whose category
    isn't allowed by `mode` are violations; a signature match outside
    `ESSENTIAL` spans (`scan_signatures`) is also a violation. A
    non-removable `UNKNOWN` block left in place is not a violation but
    produces a `f"Unrecognized structure kept: {tag}"` note instead. With
    `original`, the two files' `essential_digest` are compared when (and
    only when) they share the same format; a format mismatch is itself a
    violation. Status is `FAILED` if there is any violation or the digests
    differ, else `WARNINGS` if there is any note, else `VERIFIED`.
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
            return Verdict(Status.FAILED, [], [f"Cannot read file: {e}"])
        if container.format == ocontainer.format:
            essential_match = handler.essential_digest(container) == ohandler.essential_digest(ocontainer)
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
