"""Deterministic TIFF/EXIF byte builders shared by the test suite.

`make_tiff_exif` assembles a complete, valid TIFF (big- or little-endian)
with IFD0, ExifIFD, GPS and IFD1 (thumbnail) sections, using exactly the
tag/value set described in Task 4's brief. It is a small, independent
TIFF writer -- it does not import `siftool.metadata.exif` and must stay
deterministic byte-for-byte so tests can assert on exact `detail` strings.

`write` is a small helper for tests that need the bytes on disk.
"""

from __future__ import annotations

import io
import struct
import warnings
import zlib
from pathlib import Path
from typing import NamedTuple

from PIL import Image

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


# ---------------------------------------------------------------------------
# JPEG builders (Task 6)
# ---------------------------------------------------------------------------

GPS_TEXT = b"+37.7749-122.4194/"
MOTION_PHOTO_TRAILER = (
    b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom" + b"\x00" * 32 + GPS_TEXT
)


def base_jpeg(*, progressive: bool = False) -> bytes:
    """Build a deterministic 64x48 RGB gradient JPEG via Pillow (`quality=90`).

    A plain per-pixel gradient (R follows x, G follows y, B follows x+y),
    encoded baseline or, with `progressive=True`, as a multi-scan
    progressive JPEG (several `SOS` segments).
    """
    width, height = 64, 48
    pixels = bytes(
        value
        for y in range(height)
        for x in range(width)
        for value in (
            x * 255 // (width - 1),
            y * 255 // (height - 1),
            (x + y) * 255 // (width + height - 2),
        )
    )
    img = Image.frombytes("RGB", (width, height), pixels)
    buf = io.BytesIO()
    save_kwargs: dict = {"quality": 90}
    if progressive:
        save_kwargs["progressive"] = True
    img.save(buf, format="JPEG", **save_kwargs)
    return buf.getvalue()


def _after_app0(data: bytes) -> int:
    """Return the offset right after `data`'s first segment (Pillow's own APP0)."""
    length = int.from_bytes(data[4:6], "big")
    return 4 + length


def _segment(marker: int, payload: bytes) -> bytes:
    """Build one complete marker segment: `0xFF` + `marker` + length + `payload`."""
    return bytes([0xFF, marker]) + struct.pack(">H", len(payload) + 2) + payload


def make_jpeg(
    *,
    exif: bool = True,
    xmp: bool = True,
    icc: bool = True,
    mpf: bool = False,
    c2pa: bool = True,
    iptc: bool = True,
    comment: bool = True,
    unknown_app: bool = True,
    jfif_thumbnail: bool = False,
    trailing: bytes = b"",
    reserved_marker: bool = False,
    reserved_payload: bytes = b"harmless",
    fill_bytes: bool = False,
    progressive: bool = False,
) -> bytes:
    """Build a synthetic JPEG exercising every segment `JpegHandler` classifies.

    Starts from `base_jpeg()` and inserts, right after Pillow's own APP0,
    each of the following (in this order) when its flag is set: APP1 Exif
    (`make_tiff_exif("<")`), APP1 XMP, APP2 `ICC_PROFILE` (`make_icc("Display
    P3")`), APP2 `MPF`, APP11 JUMBF/C2PA (`make_c2pa_payload()`), APP13
    Photoshop IRB (`make_iptc_irb()`), an unrecognized APP15, a COM comment,
    and a reserved 0xF0 marker carrying `reserved_payload`. `jfif_thumbnail`
    replaces Pillow's plain APP0 with one carrying a 2x1 RGB thumbnail.
    `fill_bytes` inserts `b"\\xff\\xff"` right before the first `DQT`.
    `trailing` is appended after `EOI`.
    """
    base = base_jpeg(progressive=progressive)
    app0_end = _after_app0(base)
    head = base[:2]
    app0 = base[2:app0_end]
    rest = base[app0_end:]

    if jfif_thumbnail:
        thumbnail = b"\x00" * (3 * 2 * 1)  # 2x1 RGB, content irrelevant to tests
        payload = (
            b"JFIF\x00" + bytes([1, 1]) + b"\x00" + struct.pack(">HH", 1, 1) + bytes([2, 1]) + thumbnail
        )
        app0 = _segment(0xE0, payload)

    extra = bytearray()
    if exif:
        extra += _segment(0xE1, b"Exif\x00\x00" + make_tiff_exif("<"))
    if xmp:
        extra += _segment(0xE1, b"http://ns.adobe.com/xap/1.0/\x00" + make_xmp())
    if icc:
        extra += _segment(0xE2, b"ICC_PROFILE\x00" + bytes([1, 1]) + make_icc("Display P3"))
    if mpf:
        extra += _segment(0xE2, b"MPF\x00" + b"\x00" * 8)
    if c2pa:
        extra += _segment(0xEB, b"JP\x00\x00" + make_c2pa_payload())
    if iptc:
        extra += _segment(0xED, make_iptc_irb())
    if unknown_app:
        extra += _segment(0xEF, b"UNKNOWNDATA")
    if comment:
        extra += _segment(0xFE, b"secret comment")
    if reserved_marker:
        extra += _segment(0xF0, reserved_payload)

    if fill_bytes:
        extra += b"\xff\xff"

    return head + app0 + bytes(extra) + rest + trailing


# ---------------------------------------------------------------------------
# PNG builders (Task 7)
# ---------------------------------------------------------------------------

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_PNG_SIZE = 16


def png_chunk(ctype: bytes, data: bytes, *, bad_crc: bool = False) -> bytes:
    """Build one complete PNG chunk: length(4BE) + type(4) + data + CRC32(4BE).

    `bad_crc=True` flips every bit of the correct CRC (bitwise complement),
    which is always different from the correct value -- an easy,
    deterministic way to make the chunk fail its CRC check.
    """
    crc = zlib.crc32(ctype + data) & 0xFFFFFFFF
    if bad_crc:
        crc ^= 0xFFFFFFFF
    return struct.pack(">I", len(data)) + ctype + data + struct.pack(">I", crc)


def _png_rows(width: int, height: int) -> bytes:
    """One scanline per row, each prefixed with filter-type 0 (None): a
    plain per-pixel gradient (R follows x, G follows y, B follows x+y),
    same formula as `base_jpeg`'s, for a deterministic 8-bit RGB image."""
    rows = bytearray()
    for y in range(height):
        rows.append(0)
        for x in range(width):
            rows += bytes((
                x * 255 // (width - 1),
                y * 255 // (height - 1),
                (x + y) * 255 // (width + height - 2),
            ))
    return bytes(rows)


def _fctl(sequence_number: int, width: int, height: int) -> bytes:
    """APNG `fcTL` data: sequence number + a full-canvas frame at (0,0)."""
    return struct.pack(">IIIIIHHBB", sequence_number, width, height, 0, 0, 1, 1, 0, 0)


def _corrupt_first(chunks: list[bytes], ctype: bytes) -> list[bytes]:
    """Return `chunks` with the first chunk of type `ctype` CRC-corrupted."""
    out = list(chunks)
    for i, chunk in enumerate(out):
        if chunk[4:8] == ctype:
            crc = (int.from_bytes(chunk[-4:], "big") ^ 0xFFFFFFFF) & 0xFFFFFFFF
            out[i] = chunk[:-4] + struct.pack(">I", crc)
            break
    return out


def make_png(
    *,
    icc: bool = True,
    exif: bool = True,
    xmp: bool = True,
    ai_text: bool = True,
    meta: bool = True,
    c2pa: bool = True,
    unknown_ancillary: bool = True,
    trailing: bytes = b"",
    apng: bool = False,
    bad_crc: str | None = None,
    extra_chunks: list[bytes] = (),
) -> bytes:
    """Build a synthetic 16x16 RGB8 PNG exercising every chunk `PngHandler` classifies.

    Layout: `IHDR`, then (if `icc`) `iCCP`/`sRGB`/`gAMA`/`cHRM`/`pHYs` (must
    precede `IDAT`), then (if `apng`) `acTL` + a frame-0 `fcTL`, one `IDAT`
    (a plain gradient, filter 0), then (if `apng`) a frame-1 `fcTL` + `fdAT`
    (same pixels), then (if `exif`) `eXIf`, (if `xmp`) an iTXt `XML:com.
    adobe.xmp`, (if `ai_text`) a `parameters` tEXt and a `Comment` zTXt
    with a JSON `"prompt"`, (if `meta`) `Author`/`Software`/`Title` tEXt
    plus `tIME`, (if `c2pa`) `caBX`, (if `unknown_ancillary`) a 9-byte
    `vpAg`, then `extra_chunks` (already-complete chunks) and `IEND`.
    `bad_crc="<type>"` corrupts the CRC of the first chunk of that type
    (checked after `extra_chunks` are added, so it can target those too).
    `trailing` is appended after `IEND`.
    """
    chunks: list[bytes] = [
        png_chunk(b"IHDR", struct.pack(">IIBBBBB", _PNG_SIZE, _PNG_SIZE, 8, 2, 0, 0, 0))
    ]

    if icc:
        chunks.append(png_chunk(b"iCCP", b"icc\x00\x00" + zlib.compress(make_icc("Display P3"))))
        chunks.append(png_chunk(b"sRGB", bytes([0])))
        chunks.append(png_chunk(b"gAMA", struct.pack(">I", 45455)))
        chunks.append(png_chunk(b"cHRM", struct.pack(">8I", 31270, 32900, 64000, 33000, 30000, 60000, 15000, 6000)))
        chunks.append(png_chunk(b"pHYs", struct.pack(">IIB", 2835, 2835, 1)))

    if apng:
        chunks.append(png_chunk(b"acTL", struct.pack(">II", 2, 0)))
        chunks.append(png_chunk(b"fcTL", _fctl(0, _PNG_SIZE, _PNG_SIZE)))

    chunks.append(png_chunk(b"IDAT", zlib.compress(_png_rows(_PNG_SIZE, _PNG_SIZE))))

    if apng:
        chunks.append(png_chunk(b"fcTL", _fctl(1, _PNG_SIZE, _PNG_SIZE)))
        chunks.append(png_chunk(b"fdAT", struct.pack(">I", 2) + zlib.compress(_png_rows(_PNG_SIZE, _PNG_SIZE))))

    if exif:
        chunks.append(png_chunk(b"eXIf", make_tiff_exif(">")))
    if xmp:
        chunks.append(png_chunk(b"iTXt", b"XML:com.adobe.xmp\x00\x00\x00\x00\x00" + make_xmp()))
    if ai_text:
        chunks.append(png_chunk(b"tEXt", b"parameters\x00a cat, Steps: 20, Seed: 123456"))
        chunks.append(png_chunk(b"zTXt", b"Comment\x00\x00" + zlib.compress(b'{"prompt": "a cat"}')))
    if meta:
        chunks.append(png_chunk(b"tEXt", b"Author\x00Felipe"))
        chunks.append(png_chunk(b"tEXt", b"Software\x00TestSoftware"))
        chunks.append(png_chunk(b"tEXt", b"Title\x00My title"))
        chunks.append(png_chunk(b"tIME", struct.pack(">HBBBBB", 2026, 9, 26, 3, 30, 7)))
    if c2pa:
        chunks.append(png_chunk(b"caBX", make_c2pa_payload()))
    if unknown_ancillary:
        chunks.append(png_chunk(b"vpAg", b"\x00" * 9))

    chunks.extend(extra_chunks)
    chunks.append(png_chunk(b"IEND", b""))

    if bad_crc is not None:
        chunks = _corrupt_first(chunks, bad_crc.encode())

    return PNG_SIGNATURE + b"".join(chunks) + trailing


def decode_pixels(data: bytes) -> list[bytes]:
    """Decode every frame of an image (via Pillow) into `RGBA` bytes, one per frame.

    Our synthetic `MPF\\x00`-only APP2 (`make_jpeg(mpf=True)`) is a stub, not
    a real MP Index IFD, so Pillow's MPO probe warns before falling back to
    plain JPEG decoding; that warning is expected and silenced here rather
    than by changing the fixture bytes.
    """
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore", message="Image appears to be a malformed MPO file", category=UserWarning
        )
        img = Image.open(io.BytesIO(data))
        try:
            n_frames = img.n_frames
        except AttributeError:
            n_frames = 1
        frames = []
        for i in range(n_frames):
            img.seek(i)
            frames.append(img.convert("RGBA").tobytes())
        return frames
