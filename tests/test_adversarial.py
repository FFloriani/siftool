"""Adversarial carriers from the final whole-branch review (rulings R17-R23).

Each test rebuilds one probe vector of the review from synthetic bytes and
pins its exact outcome -- the secret absent from a VERIFIED output, one
exact `CorruptFileError` message, WARNINGS with one exact kept tag, or a
FAILED verdict -- so that `clean` can never return VERIFIED while the output
still carries data a reader would see as forbidden metadata (the seal
invariant every ruling of the fix wave serves).
"""

from __future__ import annotations

import functools
import io
import struct

import pytest
from PIL import Image

import siftool
from siftool import Category as C, Finding, Mode, Status
from fixtures.make_fixtures import (
    base_jpeg,
    make_png,
    make_tiff_exif,
    make_webp,
    png_chunk,
    riff_chunks,
    tiff_header,
    tiff_ifd,
    write,
)

SECRET = b" GPS +23.5505-046.6333 owner=Felipe"
QUIET_SECRET = b"owner=Felipe Floriani; phone=+55 11 91234-5678"  # matches no verify signature


def _clean(tmp_path, data: bytes, ext: str, mode: Mode):
    src = write(tmp_path, f"src_{mode.value}{ext}", data)
    dst = tmp_path / f"out_{mode.value}{ext}"
    res = siftool.clean(src, dst, mode)
    return res, dst.read_bytes()


def _segment(marker: int, payload: bytes) -> bytes:
    return bytes([0xFF, marker]) + struct.pack(">H", len(payload) + 2) + payload


def _riff_chunk(fourcc: bytes, payload: bytes) -> bytes:
    return fourcc + struct.pack("<I", len(payload)) + payload + (b"\x00" if len(payload) % 2 else b"")


def _riff(body: bytes) -> bytes:
    return b"RIFF" + struct.pack("<I", len(body) + 4) + b"WEBP" + body


def _vp8x(flags: int) -> bytes:
    """A VP8X payload for the 32x32 fixture canvas."""
    side = (32 - 1).to_bytes(3, "little")
    return bytes([flags, 0, 0, 0]) + side + side


@functools.cache
def _vp8l() -> bytes:
    """The fixture's 32x32 lossless bitstream (a VP8L chunk payload)."""
    return dict(riff_chunks(make_webp("lossless", meta=False, c2pa=False, unknown_chunk=False)))[b"VP8L"]


_BARE_PNG = dict(icc=False, exif=False, xmp=False, ai_text=False, meta=False, c2pa=False, unknown_ancillary=False)


# ---------------------------------------------------------------------------
# C1 / ruling R18: EXIF is always re-serialized by the canonical writer, and
# parse_exif follows (or reports) every sub-IFD pointer form.
# Carriers: JPEG APP1, PNG eXIf, WEBP EXIF, each in every mode.
# ---------------------------------------------------------------------------


def _exif_in_jpeg(tiff: bytes) -> bytes:
    base = base_jpeg()
    return base[:2] + _segment(0xE1, b"Exif\x00\x00" + tiff) + base[2:]


def _exif_in_png(tiff: bytes) -> bytes:
    return make_png(**_BARE_PNG, extra_chunks=[png_chunk(b"eXIf", tiff)])


def _exif_in_webp(tiff: bytes) -> bytes:
    return _riff(_riff_chunk(b"VP8X", _vp8x(0x08)) + _riff_chunk(b"VP8L", _vp8l()) + _riff_chunk(b"EXIF", tiff))


EXIF_CARRIERS = {"jpeg": (_exif_in_jpeg, ".jpg"), "png": (_exif_in_png, ".png"), "webp": (_exif_in_webp, ".webp")}
LAT = struct.pack(">6I", 23, 1, 33, 1, 1818, 100)
LON = struct.pack(">6I", 46, 1, 37, 1, 5988, 100)
PREVIEW = b"\xff\xd8\xff\xfe\x00\x0fowner=Felipe!\xff\xd9"  # a stub JPEG "preview" carrying identity text


def _ifd_len(n: int) -> int:
    return 2 + 12 * n + 4


def _orientation(value: int = 1) -> tuple[int, int, int, bytes]:
    return (0x0112, 3, 1, struct.pack(">HH", value, 0))


def _tiff_slack(secret: bytes) -> bytes:
    """IFD0 = Orientation only, followed by bytes no entry references."""
    return tiff_header() + tiff_ifd([_orientation(6)]) + secret


def _tiff_gps_pointer(ptr_type: int) -> bytes:
    """IFD0 = Orientation + a GPSInfo pointer typed `ptr_type`, then a GPS IFD with a position."""
    gps_off = 8 + _ifd_len(2)
    vals_off = gps_off + _ifd_len(4)
    ifd0 = tiff_ifd([_orientation(), (0x8825, ptr_type, 1, struct.pack(">I", gps_off))])
    gps = tiff_ifd([(1, 2, 2, b"S\x00\x00\x00"), (2, 5, 3, struct.pack(">I", vals_off)),
                    (3, 2, 2, b"W\x00\x00\x00"), (4, 5, 3, struct.pack(">I", vals_off + 24))])
    return tiff_header() + ifd0 + gps + LAT + LON


def _tiff_exif_ifd_type13() -> bytes:
    """IFD0 = Orientation + an ExifIFD pointer typed IFD (13) to CameraOwnerName/BodySerialNumber."""
    exif_off = 8 + _ifd_len(2)
    vals_off = exif_off + _ifd_len(2)
    owner, serial = b"Felipe Floriani\x00", b"BODY-SN-998877\x00"
    ifd0 = tiff_ifd([_orientation(), (0x8769, 13, 1, struct.pack(">I", exif_off))])
    exif = tiff_ifd([(0xA430, 2, len(owner), struct.pack(">I", vals_off)),
                     (0xA431, 2, len(serial), struct.pack(">I", vals_off + len(owner)))])
    return tiff_header() + ifd0 + exif + owner + serial


def _tiff_ifd0_preview() -> bytes:
    """IFD0 = Orientation + JPEGInterchangeFormat/Length (0x0201/0x0202) pointing at PREVIEW."""
    body_off = 8 + _ifd_len(3)
    ifd0 = tiff_ifd([_orientation(), (0x0201, 4, 1, struct.pack(">I", body_off)),
                     (0x0202, 4, 1, struct.pack(">I", len(PREVIEW)))])
    return tiff_header() + ifd0 + PREVIEW


def _tiff_orientation_ascii(text: bytes) -> bytes:
    """Orientation (an allowed tag) declared as ASCII and carrying free text."""
    ifd0 = tiff_ifd([(0x0112, 2, len(text), struct.pack(">I", 8 + _ifd_len(1)))])
    return tiff_header() + ifd0 + text


def _tiff_artist_flipped_into_yresolution() -> bytes:
    """The fixture TIFF with one bit flipped: Artist (0x013B) becomes YResolution (0x011B).

    Reproduces the review's fuzz leak (seed 1001, mutation #96): the ASCII
    "Felipe" then reads as an allowed appearance tag.
    """
    tiff = make_tiff_exif(">")
    artist_entry = b"\x01\x3b\x00\x02"
    assert tiff.count(artist_entry) == 1
    return tiff.replace(artist_entry, b"\x01\x1b\x00\x02")


@pytest.mark.parametrize("mode", list(Mode))
@pytest.mark.parametrize("fmt", sorted(EXIF_CARRIERS))
def test_exif_slack_is_never_carried_over(tmp_path, fmt, mode):
    wrap, ext = EXIF_CARRIERS[fmt]
    res, out = _clean(tmp_path, wrap(_tiff_slack(QUIET_SECRET)), ext, mode)
    assert res.verdict.status is Status.VERIFIED and QUIET_SECRET not in out


@pytest.mark.parametrize("mode", list(Mode))
@pytest.mark.parametrize("fmt", sorted(EXIF_CARRIERS))
def test_exif_slack_with_coordinates_is_rewritten_away(tmp_path, fmt, mode):
    wrap, ext = EXIF_CARRIERS[fmt]
    res, out = _clean(tmp_path, wrap(_tiff_slack(SECRET)), ext, mode)
    assert res.verdict.status is Status.VERIFIED and SECRET not in out


@pytest.mark.parametrize("mode", list(Mode))
@pytest.mark.parametrize("fmt", sorted(EXIF_CARRIERS))
def test_gps_ifd_behind_type13_pointer_is_removed(tmp_path, fmt, mode):
    wrap, ext = EXIF_CARRIERS[fmt]
    res, out = _clean(tmp_path, wrap(_tiff_gps_pointer(13)), ext, mode)
    assert res.verdict.status is Status.VERIFIED and LAT not in out and LON not in out
    assert dict(Image.open(io.BytesIO(out)).getexif().get_ifd(0x8825)) == {}


@pytest.mark.parametrize("mode", list(Mode))
@pytest.mark.parametrize("fmt", sorted(EXIF_CARRIERS))
def test_exif_ifd_behind_type13_pointer_is_removed(tmp_path, fmt, mode):
    wrap, ext = EXIF_CARRIERS[fmt]
    res, out = _clean(tmp_path, wrap(_tiff_exif_ifd_type13()), ext, mode)
    assert res.verdict.status is Status.VERIFIED
    assert b"Felipe Floriani" not in out and b"BODY-SN-998877" not in out


@pytest.mark.parametrize("mode", list(Mode))
@pytest.mark.parametrize("fmt", sorted(EXIF_CARRIERS))
def test_ifd0_preview_is_removed(tmp_path, fmt, mode):
    wrap, ext = EXIF_CARRIERS[fmt]
    res, out = _clean(tmp_path, wrap(_tiff_ifd0_preview()), ext, mode)
    assert res.verdict.status is Status.VERIFIED and PREVIEW not in out


@pytest.mark.parametrize("mode", list(Mode))
@pytest.mark.parametrize("fmt", sorted(EXIF_CARRIERS))
def test_orientation_typed_as_text_is_removed(tmp_path, fmt, mode):
    wrap, ext = EXIF_CARRIERS[fmt]
    res, out = _clean(tmp_path, wrap(_tiff_orientation_ascii(QUIET_SECRET + b"\x00")), ext, mode)
    assert res.verdict.status is Status.VERIFIED and QUIET_SECRET not in out


@pytest.mark.parametrize("mode", list(Mode))
@pytest.mark.parametrize("fmt", sorted(EXIF_CARRIERS))
def test_artist_bitflipped_into_yresolution_is_removed(tmp_path, fmt, mode):
    wrap, ext = EXIF_CARRIERS[fmt]
    res, out = _clean(tmp_path, wrap(_tiff_artist_flipped_into_yresolution()), ext, mode)
    assert res.verdict.status is Status.VERIFIED and b"Felipe" not in out


def test_unfollowable_sub_ifd_pointer_is_seen_by_inspect_and_verify(tmp_path):
    tiff = _tiff_gps_pointer(16)  # LONG8: a BigTIFF-only type, not a classic TIFF pointer
    src = write(tmp_path, "third_party.jpg", _exif_in_jpeg(tiff))
    unfollowed = Finding(C.UNKNOWN, "Unfollowed sub-IFD pointer", None, "APP1/Exif/IFD0", len(tiff))
    assert unfollowed in siftool.inspect(src).findings
    verdict = siftool.verify(src, Mode.PRIVACY)
    assert verdict.status is Status.FAILED and unfollowed in verdict.violations
