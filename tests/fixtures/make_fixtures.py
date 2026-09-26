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
