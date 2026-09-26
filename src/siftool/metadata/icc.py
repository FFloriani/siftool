"""ICC color profile reading for reporting.

`icc_description` extracts the human-readable name of an embedded ICC
profile (e.g. "sRGB IEC61966-2.1", "Display P3") from its `desc` tag, so
callers can label the `appearance` Finding that represents the profile
itself (e.g. JPEG APP2 `ICC_PROFILE`, PNG `iCCP`). It does not classify
the profile -- an ICC profile is always `appearance` -- it only reads a
display string.

ICC profiles come in two tag encodings for the same "desc" tag, per the
version this module supports: ICC v2's `desc` (textDescriptionType, a
plain ASCII string) and ICC v4's `mluc` (multiLocalizedUnicodeType, a
table of language-tagged UTF-16BE strings). Both are read directly from
the profile's tag table; nothing here writes or validates a profile.
"""

from __future__ import annotations

import struct

_TAG_TABLE_OFFSET = 128
_MAX_TAGS = 1000  # sanity cap; a real profile has a handful of tags


def _read_tag_table(profile: bytes) -> dict[bytes, tuple[int, int]]:
    """Return `{tag_signature: (offset, size)}` for every entry in the tag table."""
    (count,) = struct.unpack_from(">I", profile, _TAG_TABLE_OFFSET)
    if count > _MAX_TAGS:
        raise ValueError(f"too many ICC tags: {count}")
    tags: dict[bytes, tuple[int, int]] = {}
    entries_start = _TAG_TABLE_OFFSET + 4
    for i in range(count):
        entry_off = entries_start + i * 12
        signature = profile[entry_off : entry_off + 4]
        offset, size = struct.unpack_from(">II", profile, entry_off + 4)
        tags[signature] = (offset, size)
    return tags


def _describe(profile: bytes) -> str | None:
    if len(profile) < _TAG_TABLE_OFFSET + 4 or profile[36:40] != b"acsp":
        return None
    tags = _read_tag_table(profile)
    if b"desc" not in tags:
        return None
    offset, size = tags[b"desc"]
    data = profile[offset : offset + size]
    if len(data) < 8:
        return None
    tag_type = data[0:4]

    if tag_type == b"desc":
        (length,) = struct.unpack_from(">I", data, 8)
        text = data[12 : 12 + length].rstrip(b"\x00")
        return text.decode("ascii", errors="replace") or None

    if tag_type == b"mluc":
        length, str_offset = struct.unpack_from(">II", data, 20)
        text = data[str_offset : str_offset + length]
        return text.decode("utf-16-be", errors="replace") or None

    return None


def icc_description(profile: bytes) -> str | None:
    """Return the profile's `desc` tag as text, or None if unreadable.

    Reads the 128-byte header's `acsp` signature, then the tag table at
    offset 128 (a 4-byte count followed by 12-byte entries: signature,
    offset, size) to find the `desc` tag, then decodes it as ASCII (ICC
    v2 `desc`) or UTF-16BE (ICC v4 `mluc`) depending on the tag's own
    type. Never raises: any structural problem (missing signature, tag
    table out of bounds, unrecognized tag type, ...) yields None.
    """
    try:
        return _describe(profile)
    except Exception:
        return None
