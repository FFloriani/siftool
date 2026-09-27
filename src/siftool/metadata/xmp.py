"""XMP packet reading for reporting.

`xmp_findings` scans a raw XMP packet (the payload of a JPEG APP1 XMP
segment, a PNG `XML:com.adobe.xmp` chunk, a WEBP `XMP ` chunk, ...) for a
fixed set of namespaced properties, each mapped to the `Category` a
privacy mode can whitelist. It never returns an empty list: the last
Finding is always the generic `Finding(Category.SOFTWARE, "XMP packet",
...)`, which guarantees XMP is dropped in every mode regardless of what
else -- or nothing else -- is found inside it (removal itself is decided
elsewhere, by the planner).

Read-only and best-effort: this module never parses XML structurally.
Each property is detected by a plain substring search (`<prop` for the
element form, ` prop=` for the attribute form -- exactly as the spec
describes it) and its value by a small pair of regexes, so malformed,
truncated or simply non-XML input degrades gracefully to "no properties
found" (i.e. just the generic Finding) instead of raising.
"""

from __future__ import annotations

import re

from ..model import Category, Finding

# category -> qualified property names, in the design doc's table order.
# `Iptc4xmpExt:DigitalSourceType` (ai_provenance) is handled separately
# below (_AI_PROPERTY/_AI_MARKERS): unlike every property here, it only
# becomes a Finding when its value names an AI/synthetic source, and its
# detail is derived from the value rather than being the value itself.
_PROPERTIES: dict[Category, tuple[str, ...]] = {
    Category.LOCATION: (
        "exif:GPSLatitude", "exif:GPSLongitude", "exif:GPSAltitude",
        "photoshop:City", "photoshop:State", "photoshop:Country",
        "Iptc4xmpCore:Location", "Iptc4xmpExt:LocationCreated", "Iptc4xmpExt:LocationShown",
    ),
    Category.IDENTITY: (
        "dc:creator", "dc:rights", "xmpRights:Owner", "aux:SerialNumber",
        "exifEX:BodySerialNumber", "exifEX:CameraOwnerName", "photoshop:Credit",
    ),
    Category.TIME: (
        "xmp:CreateDate", "xmp:ModifyDate", "xmp:MetadataDate",
        "photoshop:DateCreated", "exif:DateTimeOriginal",
    ),
    Category.SOFTWARE: (
        "xmp:CreatorTool", "xmpMM:History", "xmpMM:DocumentID",
        "xmpMM:InstanceID", "photoshop:History",
    ),
    Category.DEVICE: ("tiff:Make", "tiff:Model", "aux:Lens", "exifEX:LensModel"),
    Category.TEXT: ("dc:description", "dc:title", "dc:subject"),
}

_AI_PROPERTY = "Iptc4xmpExt:DigitalSourceType"
_AI_MARKERS = ("trainedAlgorithmicMedia", "compositeSynthetic", "algorithmicMedia")


def _present(text: str, prop: str) -> bool:
    """True if `prop` appears as an exact element or attribute name.

    Element form: `<` + `prop` immediately followed by whitespace, `/` or
    `>` -- so `<dc:creatorX>` does not count as `dc:creator` present.
    Attribute form: any whitespace character (space, tab, newline) before
    `prop`, then optional whitespace and `=` -- so attributes separated by
    a tab or newline are still found, while `dc:creatorX="..."` still does
    not count as `dc:creator`.
    """
    escaped = re.escape(prop)
    return bool(
        re.search(r"<" + escaped + r"[\s/>]", text)
        or re.search(r"\s" + escaped + r"\s*=", text)
    )


def _value(text: str, prop: str) -> str | None:
    """Best-effort extraction of `prop`'s value from raw XMP text.

    Tries the attribute form (`prop="..."`) first, then the element form
    (`<prop ...>...</prop>`), descending into a single nested `rdf:li`
    (an `rdf:Bag`/`Seq`/`Alt` wrapper, e.g. around `dc:creator`) if the
    element's own content is just more markup. None if `prop` isn't
    present in either form.
    """
    m = re.search(re.escape(prop) + r"""=["'](.*?)["']""", text)
    if m:
        return m.group(1)
    m = re.search(r"<" + re.escape(prop) + r"\b[^>]*>(.*?)</" + re.escape(prop) + r">", text, re.S)
    if not m:
        return None
    inner = m.group(1)
    li = re.search(r"<rdf:li\b[^>]*>(.*?)</rdf:li>", inner, re.S)
    if li:
        return li.group(1).strip()
    plain = re.sub(r"<[^>]+>", "", inner).strip()
    return plain or None


def _findings(packet: bytes, where: str) -> list[Finding]:
    text = packet.decode("utf-8", errors="replace")
    findings: list[Finding] = []

    for category, props in _PROPERTIES.items():
        for prop in props:
            if _present(text, prop):
                findings.append(Finding(category, prop, _value(text, prop), where))

    if _present(text, _AI_PROPERTY):
        value = _value(text, _AI_PROPERTY) or ""
        if any(marker in value for marker in _AI_MARKERS):
            detail = value.rsplit("/", 1)[-1]
            findings.append(Finding(Category.AI_PROVENANCE, _AI_PROPERTY, detail, where))

    findings.append(Finding(Category.SOFTWARE, "XMP packet", None, where, len(packet)))
    return findings


def xmp_findings(packet: bytes, where: str) -> list[Finding]:
    """Parse an XMP packet into one `Finding` per recognized property.

    Always ends with `Finding(Category.SOFTWARE, "XMP packet", None,
    where, len(packet))`, even when nothing else was recognized (e.g.
    `packet` isn't XML at all) -- this categorization is never allowed
    to survive any cleaning mode. Never raises: any decoding or matching
    problem yields just that generic Finding.
    """
    try:
        return _findings(packet, where)
    except Exception:
        return [Finding(Category.SOFTWARE, "XMP packet", None, where, len(packet))]
