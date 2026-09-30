"""Photoshop IRB / IPTC-IIM reading for reporting.

`iptc_findings` walks a Photoshop "Image Resource Block" (the payload of
a JPEG APP13 `Photoshop 3.0\\x00` segment, a PNG/TIFF equivalent, ...): a
sequence of `8BIM` resources. The `0x0404` resource (the legacy IPTC-NAA
/ "IIM" record) is decoded dataset by dataset and each one categorized
by its record:dataset number; the `0x040C` resource (an embedded
thumbnail) becomes a single `extras` Finding. It never returns an empty
list: the last Finding is always the generic `Finding(Category.TEXT,
"Photoshop/IPTC data", ...)`, which guarantees this whole block is
dropped in every mode regardless of what else -- or nothing else -- was
found inside it (removal itself is decided elsewhere, by the planner).

Read-only and best-effort: resource/dataset scanning stops as soon as
it runs off the end of the buffer or hits a byte sequence it doesn't
recognize, rather than guessing -- so malformed or truncated input just
yields fewer findings, never an exception.
"""

from __future__ import annotations

from ..model import Category, Finding

_SIGNATURE = b"Photoshop 3.0\x00"
_IPTC_RESOURCE_ID = 0x0404
_THUMBNAIL_RESOURCE_ID = 0x040C
_IIM_MARKER = 0x1C

# IIM Application Record (record 2) dataset number -> (label, Category).
# Anything else -- a dataset not listed here, or any dataset of another
# record (e.g. 1:90, the Envelope Record's CodedCharacterSet, is not 2:90
# City) -- is "demais": Category.TEXT, per the design doc's dataset table.
_APPLICATION_RECORD = 2
_DATASETS: dict[int, tuple[str, Category]] = {
    80: ("By-line", Category.IDENTITY),
    85: ("By-line Title", Category.IDENTITY),
    116: ("Copyright", Category.IDENTITY),
    90: ("City", Category.LOCATION),
    92: ("Sublocation", Category.LOCATION),
    95: ("Province", Category.LOCATION),
    100: ("Country", Category.LOCATION),
    101: ("Country", Category.LOCATION),
    55: ("Date Created", Category.TIME),
    60: ("Time Created", Category.TIME),
    65: ("Originating Program", Category.SOFTWARE),
}


def _iim_datasets(data: bytes, where: str, findings: list[Finding]) -> None:
    """Append one Finding per IIM dataset in `data` (an 0x0404 resource's payload).

    Each dataset is `\\x1c` + record (1 byte) + dataset (1 byte) + a
    2-byte big-endian length + that many bytes of text, decoded as UTF-8
    with `errors="replace"` per the design doc. Stops at the first byte
    that isn't a `\\x1c` marker (including running past the end of
    `data`) instead of guessing at a different layout.
    """
    pos = 0
    while pos + 5 <= len(data) and data[pos] == _IIM_MARKER:
        record, dataset = data[pos + 1], data[pos + 2]
        length = (data[pos + 3] << 8) | data[pos + 4]
        pos += 5
        if length & 0x8000:  # extended dataset form: no real writer needs it for text
            break
        text = data[pos : pos + length].decode("utf-8", errors="replace")
        pos += length
        generic = (f"IPTC {record}:{dataset}", Category.TEXT)
        label, category = _DATASETS.get(dataset, generic) if record == _APPLICATION_RECORD else generic
        findings.append(Finding(category, label, text, where))


def _resources(irb: bytes, where: str) -> list[Finding]:
    findings: list[Finding] = []
    pos = len(_SIGNATURE) if irb.startswith(_SIGNATURE) else 0

    while pos + 4 <= len(irb) and irb[pos : pos + 4] == b"8BIM":
        pos += 4
        if pos + 2 > len(irb):
            break
        resource_id = (irb[pos] << 8) | irb[pos + 1]
        pos += 2
        if pos >= len(irb):
            break
        name_len = irb[pos]
        pos += 1 + name_len
        if (1 + name_len) % 2:  # Pascal string, padded so name+length is even
            pos += 1
        if pos + 4 > len(irb):
            break
        size = int.from_bytes(irb[pos : pos + 4], "big")
        pos += 4
        data = irb[pos : pos + size]
        pos += size + (size % 2)  # resource data is padded to an even length

        if resource_id == _THUMBNAIL_RESOURCE_ID:
            findings.append(Finding(Category.EXTRAS, "Thumbnail", where=where, size=len(data)))
        elif resource_id == _IPTC_RESOURCE_ID:
            _iim_datasets(data, where, findings)

    findings.append(Finding(Category.TEXT, "Photoshop/IPTC data", None, where, len(irb)))
    return findings


def iptc_findings(irb: bytes, where: str) -> list[Finding]:
    """Parse a Photoshop IRB into one `Finding` per recognized IIM dataset.

    Always ends with `Finding(Category.TEXT, "Photoshop/IPTC data",
    None, where, len(irb))`, even when nothing else was recognized (e.g.
    `irb` has no `8BIM` resources at all) -- this categorization is
    never allowed to survive any cleaning mode. Never raises: any
    scanning problem yields just that generic Finding.
    """
    try:
        return _resources(irb, where)
    except Exception:
        return [Finding(Category.TEXT, "Photoshop/IPTC data", None, where, len(irb))]
