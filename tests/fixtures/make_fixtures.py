"""Deterministic TIFF/EXIF byte builders shared by the test suite.

`make_tiff_exif` assembles a complete, valid TIFF (big- or little-endian)
with IFD0, ExifIFD, GPS and IFD1 (thumbnail) sections, using exactly the
tag/value set described in Task 4's brief. It is a small, independent
TIFF writer -- it does not import `siftool.metadata.exif` and must stay
deterministic byte-for-byte so tests can assert on exact `detail` strings.

`write` is a small helper for tests that need the bytes on disk.
"""

from __future__ import annotations

import struct
from pathlib import Path
from typing import NamedTuple

_TYPE_SIZES = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8, 11: 4, 12: 8}


class _Entry(NamedTuple):
    tag: int
    type: int
    count: int
    value: bytes = b""
    pointer_to: str | None = None


class _Ifd(NamedTuple):
    name: str
    entries: list[_Entry]
    next_name: str | None = None


def _ascii(s: str) -> tuple[int, bytes]:
    value = s.encode("ascii") + b"\x00"
    return len(value), value


def _short(order: str, *values: int) -> tuple[int, bytes]:
    return len(values), struct.pack(order + "H" * len(values), *values)


def _long(order: str, *values: int) -> tuple[int, bytes]:
    return len(values), struct.pack(order + "I" * len(values), *values)


def _rational(order: str, *pairs: tuple[int, int]) -> tuple[int, bytes]:
    flat = [v for pair in pairs for v in pair]
    return len(pairs), struct.pack(order + "I" * len(flat), *flat)


def _undefined(data: bytes) -> tuple[int, bytes]:
    return len(data), data


def _pad_even(data: bytes) -> bytes:
    return data if len(data) % 2 == 0 else data + b"\x00"


def _ifd_size(ifd: _Ifd) -> int:
    size = 2 + 12 * len(ifd.entries) + 4
    for entry in ifd.entries:
        if entry.pointer_to is not None:
            continue
        nbytes = entry.count * _TYPE_SIZES[entry.type]
        if nbytes > 4:
            size += len(_pad_even(entry.value))
    return size


def _emit_ifd(ifd: _Ifd, order: str, offsets: dict[str, int]) -> bytes:
    start = offsets[ifd.name]
    header_size = 2 + 12 * len(ifd.entries) + 4
    voffset = start + header_size
    head = bytearray(struct.pack(order + "H", len(ifd.entries)))
    tail = bytearray()
    for entry in ifd.entries:
        head += struct.pack(order + "HH", entry.tag, entry.type)
        head += struct.pack(order + "I", entry.count)
        if entry.pointer_to is not None:
            head += struct.pack(order + "I", offsets[entry.pointer_to])
            continue
        nbytes = entry.count * _TYPE_SIZES[entry.type]
        if nbytes <= 4:
            head += entry.value + b"\x00" * (4 - nbytes)
        else:
            head += struct.pack(order + "I", voffset)
            padded = _pad_even(entry.value)
            tail += padded
            voffset += len(padded)
    next_offset = offsets[ifd.next_name] if ifd.next_name else 0
    head += struct.pack(order + "I", next_offset)
    return bytes(head) + bytes(tail)


def _assemble(order: str, ifds: list[_Ifd], extra_blocks: dict[str, bytes]) -> bytes:
    """Lay out `ifds` (in order) followed by `extra_blocks`, patching every
    `pointer_to`/`next_name` reference to the resolved start offset of the
    named IFD or block. The first IFD always starts at offset 8."""
    offsets: dict[str, int] = {}
    offset = 8
    for ifd in ifds:
        offsets[ifd.name] = offset
        offset += _ifd_size(ifd)
    for name, data in extra_blocks.items():
        offsets[name] = offset
        offset += len(data)

    out = bytearray()
    for ifd in ifds:
        out += _emit_ifd(ifd, order, offsets)
    for data in extra_blocks.values():
        out += data
    return bytes(out)


def make_tiff_exif(byte_order: str = "<") -> bytes:
    """Build a deterministic TIFF/EXIF blob with IFD0, ExifIFD, GPS and IFD1.

    `byte_order` is `"<"` for little-endian (`II`) or `">"` for big-endian
    (`MM`), matching `struct`'s own convention.
    """
    order = byte_order
    bom = b"II" if order == "<" else b"MM"

    make_c, make_v = _ascii("TestCam")
    model_c, model_v = _ascii("X1")
    orient_c, orient_v = _short(order, 6)
    soft_c, soft_v = _ascii("TestSoftware")
    dt_c, dt_v = _ascii("2026:09:26 03:30:07")
    artist_c, artist_v = _ascii("Felipe")

    ifd0 = _Ifd(
        "IFD0",
        [
            _Entry(0x010F, 2, make_c, make_v),
            _Entry(0x0110, 2, model_c, model_v),
            _Entry(0x0112, 3, orient_c, orient_v),
            _Entry(0x0131, 2, soft_c, soft_v),
            _Entry(0x0132, 2, dt_c, dt_v),
            _Entry(0x013B, 2, artist_c, artist_v),
            _Entry(0x8769, 4, 1, pointer_to="ExifIFD"),
            _Entry(0x8825, 4, 1, pointer_to="GPS"),
        ],
        next_name="IFD1",
    )

    exp_c, exp_v = _rational(order, (1, 120))
    fnum_c, fnum_v = _rational(order, (28, 10))
    iso_c, iso_v = _short(order, 100)
    dto_c, dto_v = _ascii("2026:09:26 03:30:07")
    maker_c, maker_v = _undefined(b"NIKON\x00\x02\x00" + b"\x00" * 8)
    serial_c, serial_v = _ascii("SN123456")
    lens_c, lens_v = _ascii("50mm f/1.8")
    comment_c, comment_v = _undefined(b"ASCII\x00\x00\x00secret comment")
    cs_c, cs_v = _short(order, 1)
    unk_c, unk_v = _undefined(b"PrintIM\x00")

    exif_ifd = _Ifd(
        "ExifIFD",
        [
            _Entry(0x829A, 5, exp_c, exp_v),
            _Entry(0x829D, 5, fnum_c, fnum_v),
            _Entry(0x8827, 3, iso_c, iso_v),
            _Entry(0x9003, 2, dto_c, dto_v),
            _Entry(0x927C, 7, maker_c, maker_v),
            _Entry(0x9286, 7, comment_c, comment_v),
            _Entry(0xA001, 3, cs_c, cs_v),
            _Entry(0xA431, 2, serial_c, serial_v),
            _Entry(0xA434, 2, lens_c, lens_v),
            _Entry(0xC4A5, 7, unk_c, unk_v),
        ],
    )

    latref_c, latref_v = _ascii("N")
    lat_c, lat_v = _rational(order, (37, 1), (46, 1), (2964, 100))
    lonref_c, lonref_v = _ascii("W")
    lon_c, lon_v = _rational(order, (122, 1), (25, 1), (984, 100))

    gps = _Ifd(
        "GPS",
        [
            _Entry(1, 2, latref_c, latref_v),
            _Entry(2, 5, lat_c, lat_v),
            _Entry(3, 2, lonref_c, lonref_v),
            _Entry(4, 5, lon_c, lon_v),
        ],
    )

    comp_c, comp_v = _short(order, 6)
    jlen_c, jlen_v = _long(order, 4)
    ifd1 = _Ifd(
        "IFD1",
        [
            _Entry(0x0103, 3, comp_c, comp_v),
            _Entry(0x0201, 4, 1, pointer_to="thumbnail"),
            _Entry(0x0202, 4, jlen_c, jlen_v),
        ],
    )

    header = bom + struct.pack(order + "H", 0x2A) + struct.pack(order + "I", 8)
    body = _assemble(order, [ifd0, exif_ifd, gps, ifd1], {"thumbnail": b"\xff\xd8\xff\xd9"})
    return header + body


def write(tmp_path, name: str, data: bytes) -> Path:
    """Write `data` to `tmp_path/name` and return the resulting Path."""
    path = Path(tmp_path) / name
    path.write_bytes(data)
    return path


# ---------------------------------------------------------------------------
# XMP / C2PA / ICC / IPTC builders (Task 5)
# ---------------------------------------------------------------------------


def cbor_text(s: str) -> bytes:
    """Encode `s` as a CBOR major-type-3 (text string) item, `len(s) < 24`."""
    return bytes([0x60 + len(s)]) + s.encode()


def make_xmp(*, ai: bool = True, gps: bool = True, creator: str = "Felipe") -> bytes:
    """Build a deterministic, syntactically-valid XMP packet for tests.

    Always carries `xmp:CreatorTool="TestSoftware"` (software) and a
    `dc:creator` (identity) as a nested `rdf:Seq`/`rdf:li`, so the reader's
    element-form value extraction gets exercised too, not just attributes.
    `gps=True` adds `exif:GPSLatitude="37,46.494N"` (location). `ai=True`
    adds `Iptc4xmpExt:DigitalSourceType` set to the IPTC "trained
    algorithmic media" URI (ai_provenance).
    """
    attrs = ['xmp:CreatorTool="TestSoftware"']
    if ai:
        attrs.append(
            'Iptc4xmpExt:DigitalSourceType='
            '"http://cv.iptc.org/newscodes/digitalsourcetype/trainedAlgorithmicMedia"'
        )
    if gps:
        attrs.append('exif:GPSLatitude="37,46.494N"')

    description = (
        '<rdf:Description rdf:about="" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" '
        'xmlns:xmp="http://ns.adobe.com/xap/1.0/" '
        'xmlns:exif="http://ns.adobe.com/exif/1.0/" '
        'xmlns:Iptc4xmpExt="http://iptc.org/std/Iptc4xmpExt/2008-02-29/" '
        + " ".join(attrs) + ">"
        f"<dc:creator><rdf:Seq><rdf:li>{creator}</rdf:li></rdf:Seq></dc:creator>"
        "</rdf:Description>"
    )
    packet = (
        '<?xpacket begin="﻿" id="W5M0MpCehiHzreSzNTczkc9d"?>'
        '<x:xmpmeta xmlns:x="adobe:ns:meta/">'
        '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
        + description +
        '</rdf:RDF></x:xmpmeta>'
        '<?xpacket end="w"?>'
    )
    return packet.encode("utf-8")


def make_c2pa_payload(generator: str = "ChatGPT", ai: bool = True) -> bytes:
    """Build a synthetic JUMBF/C2PA payload with a `claim_generator_info` CBOR map.

    Layout: a 4-byte JUMBF box-size placeholder + `jumbc2pa` + the CBOR
    text key `claim_generator_info` + `\\x81\\xa2` (array/map headers, not
    semantically valid CBOR -- only `c2pa_findings`'s byte-search cares) +
    the CBOR text pairs `name`/`generator` and `version`/`"gpt-image"`.
    `ai=True` appends the literal marker `trainedAlgorithmicMedia`.
    """
    payload = (
        b"\x00\x00\x00\x40jumbc2pa"
        + cbor_text("claim_generator_info")
        + b"\x81\xa2"
        + cbor_text("name")
        + cbor_text(generator)
        + cbor_text("version")
        + cbor_text("gpt-image")
    )
    if ai:
        payload += b"trainedAlgorithmicMedia"
    return payload


def make_icc(description: str, version: int = 2) -> bytes:
    """Build a minimal ICC profile with a single `desc` tag.

    128-byte header (`acsp` at offset 36, `version` as a single byte at
    offset 8) + a 1-entry tag table at offset 128 + the tag's data. The
    tag's own value type follows the real ICC split: `version < 4` writes
    a v2 `desc` (textDescriptionType: 4-byte ASCII length at +8, ASCII
    text at +12); `version >= 4` writes a v4 `mluc`
    (multiLocalizedUnicodeType: one language/country record whose 3rd and
    4th fields are the length and offset of a UTF-16BE string).
    """
    header = bytearray(128)
    header[8] = version
    header[36:40] = b"acsp"

    if version >= 4:
        text = description.encode("utf-16-be")
        record = b"enUS" + struct.pack(">II", len(text), 28)
        tag_data = b"mluc" + b"\x00" * 4 + struct.pack(">II", 1, 12) + record + text
    else:
        text = description.encode("ascii") + b"\x00"
        tag_data = b"desc" + b"\x00" * 4 + struct.pack(">I", len(text)) + text

    tag_offset = 128 + 4 + 12  # header + tag count (4 bytes) + one 12-byte entry
    table = struct.pack(">I", 1) + b"desc" + struct.pack(">II", tag_offset, len(tag_data))
    return bytes(header) + table + tag_data


def _iim_dataset(record: int, dataset: int, value: str) -> bytes:
    data = value.encode("utf-8")
    return bytes([0x1C, record, dataset]) + struct.pack(">H", len(data)) + data


def _8bim_resource(resource_id: int, data: bytes) -> bytes:
    block = b"8BIM" + struct.pack(">H", resource_id) + b"\x00\x00" + struct.pack(">I", len(data)) + data
    if len(data) % 2:
        block += b"\x00"
    return block


def make_iptc_irb(byline: str = "Felipe", city: str = "Sao Paulo", caption: str = "secret caption") -> bytes:
    """Build a Photoshop IRB with one `8BIM` 0x0404 (IPTC-NAA) resource.

    The resource's data is three IIM datasets in the Application Record
    (record 2): 2:80 By-line, 2:90 City and 2:120 Caption/Abstract, each
    `\\x1c` + record + dataset + a 2-byte big-endian length + UTF-8 text.
    """
    iim = _iim_dataset(2, 80, byline) + _iim_dataset(2, 90, city) + _iim_dataset(2, 120, caption)
    return b"Photoshop 3.0\x00" + _8bim_resource(0x0404, iim)
