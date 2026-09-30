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
import zlib

import pytest
from PIL import Image

import siftool
from siftool import Category as C, Finding, Mode, Status
from fixtures.make_fixtures import (
    base_jpeg,
    decode_pixels,
    make_icc,
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


# ---------------------------------------------------------------------------
# C2, C3, I2 / ruling R19 (WEBP) and I1 / ruling R20 (WEBP ICCP).
# ---------------------------------------------------------------------------

XMP_SECRET = (b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF><rdf:Description>'
              b"<dc:creator>Felipe Floriani</dc:creator></rdf:Description></rdf:RDF></x:xmpmeta>")


def _animated_with_extra_in_first_frame(extra: bytes) -> bytes:
    """The fixture animation with `extra` sub-chunks appended inside its first ANMF."""
    chunks = riff_chunks(make_webp("animated", meta=False, c2pa=False, unknown_chunk=False))
    first = next(i for i, (t, _) in enumerate(chunks) if t == b"ANMF")
    chunks[first] = (b"ANMF", chunks[first][1] + extra)
    return _riff(b"".join(_riff_chunk(t, p) for t, p in chunks))


@pytest.mark.parametrize("mode", list(Mode))
def test_webp_anmf_with_metadata_subchunks_fails_verification(tmp_path, mode):
    data = _animated_with_extra_in_first_frame(
        _riff_chunk(b"XMP ", XMP_SECRET) + _riff_chunk(b"EXIF", b"MM\x00*" + SECRET)
    )
    dst = tmp_path / "out.webp"
    with pytest.raises(siftool.VerificationFailedError) as e:
        siftool.clean(write(tmp_path, "a.webp", data), dst, mode)
    assert {(f.label, f.where) for f in e.value.verdict.violations} == {
        ("XMP packet", "ANMF?"), ("ISO 6709 coordinates", "ANMF?"), ("GPS marker", "ANMF?"),
    }
    assert "Unrecognized structure kept: ANMF?" in e.value.verdict.notes and not dst.exists()


@pytest.mark.parametrize("mode", list(Mode))
def test_webp_anmf_with_unknown_subchunk_is_kept_with_warnings(tmp_path, mode):
    data = _animated_with_extra_in_first_frame(_riff_chunk(b"ABCD", QUIET_SECRET))
    res, out = _clean(tmp_path, data, ".webp", mode)
    assert res.verdict.status is Status.WARNINGS
    assert res.verdict.notes == ["Unrecognized structure kept: ANMF?"]
    assert out.count(QUIET_SECRET) == 1  # kept inside the non-removable, signature-scanned ANMF?
    assert len(decode_pixels(out)) == 3


@pytest.mark.parametrize("mode", list(Mode))
def test_webp_second_top_level_vp8l_is_dropped(tmp_path, mode):
    data = _riff(_riff_chunk(b"VP8L", _vp8l()) + _riff_chunk(b"VP8L", QUIET_SECRET))
    res, out = _clean(tmp_path, data, ".webp", mode)
    assert res.verdict.status is Status.VERIFIED and QUIET_SECRET not in out


@pytest.mark.parametrize("mode", list(Mode))
def test_webp_alph_after_vp8l_is_dropped(tmp_path, mode):
    data = _riff(_riff_chunk(b"VP8X", _vp8x(0)) + _riff_chunk(b"VP8L", _vp8l()) + _riff_chunk(b"ALPH", QUIET_SECRET))
    res, out = _clean(tmp_path, data, ".webp", mode)
    assert res.verdict.status is Status.VERIFIED and QUIET_SECRET not in out


@pytest.mark.parametrize("declared", [0, 4, 22])
def test_webp_riff_size_that_hides_the_image_is_corrupt(tmp_path, declared):
    good = _riff(_riff_chunk(b"VP8X", _vp8x(0)) + _riff_chunk(b"VP8L", _vp8l()))
    src = write(tmp_path, "a.webp", good[:4] + struct.pack("<I", declared) + good[8:])
    with pytest.raises(siftool.CorruptFileError, match="^missing image data$"):
        siftool.clean(src, tmp_path / "out.webp", Mode.PRIVACY)
    assert not (tmp_path / "out.webp").exists()


def test_webp_with_only_an_unknown_chunk_is_corrupt(tmp_path):
    src = write(tmp_path, "a.webp", _riff(_riff_chunk(b"ABCD", b"junk")))
    with pytest.raises(siftool.CorruptFileError, match="^missing image data$"):
        siftool.clean(src, tmp_path / "out.webp", Mode.MAXIMUM)


@pytest.mark.parametrize("mode", [Mode.PRIVACY, Mode.PHOTOGRAPHER])
def test_webp_iccp_carrying_identity_text_is_dropped(tmp_path, mode):
    data = _riff(_riff_chunk(b"VP8X", _vp8x(0x20)) + _riff_chunk(b"ICCP", QUIET_SECRET) + _riff_chunk(b"VP8L", _vp8l()))
    res, out = _clean(tmp_path, data, ".webp", mode)
    assert res.verdict.status is Status.VERIFIED and QUIET_SECRET not in out
    assert dict(riff_chunks(out))[b"VP8X"][0] == 0  # ICC flag cleared with the chunk
    span = 8 + len(QUIET_SECRET) + len(QUIET_SECRET) % 2
    assert Finding(C.UNKNOWN, "Invalid ICC profile", None, "ICCP", span) in res.removed


# ---------------------------------------------------------------------------
# C3, I2 / ruling R19 (JPEG) and I1 / ruling R20 (JPEG APP2 ICC).
# ---------------------------------------------------------------------------


def _jpeg_after_soi(segment: bytes) -> bytes:
    base = base_jpeg()
    return base[:2] + segment + base[2:]


def _jpeg_before_eoi(extra: bytes) -> bytes:
    base = base_jpeg()
    assert base.endswith(b"\xff\xd9")
    return base[:-2] + extra + b"\xff\xd9"


@pytest.mark.parametrize("mode", list(Mode))
def test_jpeg_dac_carrying_text_is_dropped(tmp_path, mode):
    res, out = _clean(tmp_path, _jpeg_after_soi(_segment(0xCC, SECRET)), ".jpg", mode)
    assert res.verdict.status is Status.VERIFIED and SECRET not in out


@pytest.mark.parametrize("mode", list(Mode))
def test_jpeg_decodable_dac_pairs_are_dropped(tmp_path, mode):
    pairs = b"".join(b"\x10" + bytes([c]) for c in SECRET)  # libjpeg accepts these (Tc=1, Tb=0) entries
    res, out = _clean(tmp_path, _jpeg_after_soi(_segment(0xCC, pairs)), ".jpg", mode)
    assert res.verdict.status is Status.VERIFIED and pairs not in out


@pytest.mark.parametrize("mode", list(Mode))
def test_jpeg_second_sof_after_the_scan_is_corrupt(tmp_path, mode):
    nf = len(QUIET_SECRET) // 3
    sof = bytes([8]) + struct.pack(">HH", 48, 64) + bytes([nf]) + QUIET_SECRET[: 3 * nf]  # component bytes = text
    src = write(tmp_path, "a.jpg", _jpeg_before_eoi(_segment(0xC0, sof)))
    with pytest.raises(siftool.CorruptFileError, match="^invalid SOF$"):
        siftool.clean(src, tmp_path / "out.jpg", mode)
    assert not (tmp_path / "out.jpg").exists()


def test_jpeg_without_frame_or_scan_is_corrupt(tmp_path):
    exif = _segment(0xE1, b"Exif\x00\x00" + tiff_header() + tiff_ifd([]))
    src = write(tmp_path, "a.jpg", b"\xff\xd8" + exif + b"\xff\xd9")
    with pytest.raises(siftool.CorruptFileError, match="^missing image data$"):
        siftool.clean(src, tmp_path / "out.jpg", Mode.MAXIMUM)


@pytest.mark.parametrize("mode", [Mode.PRIVACY, Mode.PHOTOGRAPHER])
def test_jpeg_icc_chunk_carrying_identity_text_is_dropped(tmp_path, mode):
    data = _jpeg_after_soi(_segment(0xE2, b"ICC_PROFILE\x00\x01\x01" + QUIET_SECRET))
    res, out = _clean(tmp_path, data, ".jpg", mode)
    assert res.verdict.status is Status.VERIFIED and QUIET_SECRET not in out and out == base_jpeg()


@pytest.mark.parametrize("mode", [Mode.PRIVACY, Mode.PHOTOGRAPHER])
def test_jpeg_signature_split_across_three_icc_chunks_is_dropped(tmp_path, mode):
    parts = [b"G", b"PS +23.55", b"05-046.6333 owner=Felipe"]
    chunks = b"".join(_segment(0xE2, b"ICC_PROFILE\x00" + bytes([i + 1, 3]) + p) for i, p in enumerate(parts))
    res, out = _clean(tmp_path, _jpeg_after_soi(chunks), ".jpg", mode)
    assert res.verdict.status is Status.VERIFIED and b"05-046.6333" not in out and out == base_jpeg()


# ---------------------------------------------------------------------------
# C3, I2 / ruling R19 (PNG) and I1 / ruling R20 (PNG iCCP and the fixed-size
# appearance chunks).
# ---------------------------------------------------------------------------


def _bare_png(*chunks: bytes) -> bytes:
    """The fixture's 16x16 RGB PNG with only IHDR/IDAT/IEND, plus `chunks` right before its IDAT."""
    png = make_png(**_BARE_PNG)
    at = png.index(b"IDAT") - 4
    return png[:at] + b"".join(chunks) + png[at:]


_SUGGESTED_PALETTE = QUIET_SECRET + b"\x00" * (-len(QUIET_SECRET) % 3)


@pytest.mark.parametrize("mode", list(Mode))
def test_png_suggested_palette_carrying_text_is_dropped(tmp_path, mode):
    res, out = _clean(tmp_path, _bare_png(png_chunk(b"PLTE", _SUGGESTED_PALETTE)), ".png", mode)
    assert res.verdict.status is Status.VERIFIED and QUIET_SECRET not in out and out == make_png(**_BARE_PNG)


@pytest.mark.parametrize("mode", list(Mode))
def test_png_two_palettes_in_a_truecolor_image_are_dropped(tmp_path, mode):
    plte = png_chunk(b"PLTE", _SUGGESTED_PALETTE)
    res, out = _clean(tmp_path, _bare_png(plte, plte), ".png", mode)
    assert res.verdict.status is Status.VERIFIED and QUIET_SECRET not in out and out == make_png(**_BARE_PNG)


def test_png_without_idat_is_corrupt(tmp_path):
    png = b"\x89PNG\r\n\x1a\n" + png_chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)) + png_chunk(b"IEND", b"")
    src = write(tmp_path, "a.png", png)
    with pytest.raises(siftool.CorruptFileError, match="^missing image data$"):
        siftool.clean(src, tmp_path / "out.png", Mode.MAXIMUM)


@pytest.mark.parametrize("mode", [Mode.PRIVACY, Mode.PHOTOGRAPHER])
def test_png_iccp_hiding_coordinates_in_its_zlib_stream_is_dropped(tmp_path, mode):
    compressed = zlib.compress(b"desc " + SECRET)
    res, out = _clean(tmp_path, _bare_png(png_chunk(b"iCCP", b"icc\x00\x00" + compressed)), ".png", mode)
    assert res.verdict.status is Status.VERIFIED and compressed not in out and out == make_png(**_BARE_PNG)


@pytest.mark.parametrize("mode", [Mode.PRIVACY, Mode.PHOTOGRAPHER])
def test_png_iccp_keyword_carrying_identity_is_rewritten(tmp_path, mode):
    compressed = zlib.compress(make_icc("Display P3"))
    data = _bare_png(png_chunk(b"iCCP", b"Felipe Floriani calibrated\x00\x00" + compressed))
    res, out = _clean(tmp_path, data, ".png", mode)
    assert res.verdict.status is Status.VERIFIED and b"Felipe Floriani" not in out
    assert out == _bare_png(png_chunk(b"iCCP", b"ICC profile\x00\x00" + compressed))


@pytest.mark.parametrize("mode", [Mode.PRIVACY, Mode.PHOTOGRAPHER])
def test_png_oversize_gama_carrying_text_is_dropped(tmp_path, mode):
    data = _bare_png(png_chunk(b"gAMA", struct.pack(">I", 45455) + QUIET_SECRET))
    res, out = _clean(tmp_path, data, ".png", mode)
    assert res.verdict.status is Status.VERIFIED and QUIET_SECRET not in out and out == make_png(**_BARE_PNG)


# ---------------------------------------------------------------------------
# I5 / ruling R21: ESSENTIAL spans and kept ICC profiles are scanned for the
# long signatures only. I1 / ruling R20: a reassembled or inflated ICC
# profile (Block.logical) is scanned too.
# ---------------------------------------------------------------------------


def _clean_fails(tmp_path, data: bytes, ext: str, mode: Mode):
    dst = tmp_path / f"out_{mode.value}{ext}"
    with pytest.raises(siftool.VerificationFailedError) as e:
        siftool.clean(write(tmp_path, f"src_{mode.value}{ext}", data), dst, mode)
    assert not dst.exists()
    return e.value.verdict


def _png_with_idat_after_the_zlib_stream(extra: bytes) -> bytes:
    png = make_png(**_BARE_PNG)
    at = png.index(b"IEND") - 4
    return png[:at] + png_chunk(b"IDAT", extra) + png[at:]


ESSENTIAL_CARRIERS = {
    "jpeg-entropy-tail": (lambda s: _jpeg_before_eoi(s), ".jpg", "SOS"),
    "png-idat-after-zlib-end": (_png_with_idat_after_the_zlib_stream, ".png", "IDAT"),
    "webp-vp8l-tail": (lambda s: _riff(_riff_chunk(b"VP8L", _vp8l() + s)), ".webp", "VP8L"),
}


@pytest.mark.parametrize("mode", list(Mode))
@pytest.mark.parametrize("carrier", sorted(ESSENTIAL_CARRIERS))
def test_coordinates_hidden_in_image_data_fail_verification(tmp_path, carrier, mode):
    wrap, ext, tag = ESSENTIAL_CARRIERS[carrier]
    verdict = _clean_fails(tmp_path, wrap(SECRET), ext, mode)
    # Only the long signature fires: SECRET's bare b"GPS" is never searched in image data.
    assert verdict.violations == [Finding(C.LOCATION, "ISO 6709 coordinates", None, tag)]


LONG = {
    b"Exif\x00\x00": (C.UNKNOWN, "EXIF header"),
    b"<x:xmpmeta": (C.SOFTWARE, "XMP packet"),
    b"http://ns.adobe.com/xap/1.0/": (C.SOFTWARE, "XMP namespace"),
    b"jumdc2pa": (C.AI_PROVENANCE, "C2PA manifest box"),
    b"+23.5505-046.6333": (C.LOCATION, "ISO 6709 coordinates"),
}


@pytest.mark.parametrize("signature", sorted(LONG), ids=lambda s: s.decode("latin-1"))
def test_long_signatures_are_found_in_essential_spans(tmp_path, signature):
    category, label = LONG[signature]
    verdict = _clean_fails(tmp_path, _jpeg_before_eoi(b" " + signature + b" "), ".jpg", Mode.PRIVACY)
    assert verdict.violations == [Finding(category, label, None, "SOS")]


@pytest.mark.parametrize("signature", [b"GPS", b"jumb", b"c2pa", b"caBX"], ids=lambda s: s.decode())
def test_short_signatures_are_not_searched_in_essential_spans(tmp_path, signature):
    res, _ = _clean(tmp_path, _jpeg_before_eoi(b" " + signature + b" "), ".jpg", Mode.PRIVACY)
    assert res.verdict.status is Status.VERIFIED and res.verdict.violations == []


def _icc_desc(text: str) -> bytes:
    return make_icc(text, 2)  # v2 "desc": plain ASCII text inside the profile


def test_kept_icc_profile_is_scanned_for_long_signatures_only(tmp_path):
    profile = _icc_desc("GPS-calibrated display")  # a bare 3-byte "GPS" in a valid profile
    res, out = _clean(tmp_path, _jpeg_after_soi(_segment(0xE2, b"ICC_PROFILE\x00\x01\x01" + profile)), ".jpg", Mode.PRIVACY)
    assert res.verdict.status is Status.VERIFIED and profile in out


def test_coordinates_split_across_icc_chunks_are_found_in_the_reassembled_profile(tmp_path):
    profile = _icc_desc("Display GPS +23.5505-046.6333 office")
    start = profile.index(b"+23.5505")
    parts = [profile[: start + 3], profile[start + 3 : start + 9], profile[start + 9 :]]
    chunks = b"".join(_segment(0xE2, b"ICC_PROFILE\x00" + bytes([i + 1, 3]) + p) for i, p in enumerate(parts))
    data = _jpeg_after_soi(chunks)
    assert b"+23.5505-046.6333" not in data  # no raw-byte scan can see it
    verdict = _clean_fails(tmp_path, data, ".jpg", Mode.PRIVACY)
    assert verdict.violations == [Finding(C.LOCATION, "ISO 6709 coordinates", None, "APP2:ICC")]


def test_coordinates_inside_a_compressed_iccp_profile_are_found(tmp_path):
    compressed = zlib.compress(_icc_desc("Display GPS +23.5505-046.6333 office"))
    data = _bare_png(png_chunk(b"iCCP", b"ICC profile\x00\x00" + compressed))
    assert b"+23.5505" not in data
    verdict = _clean_fails(tmp_path, data, ".png", Mode.PRIVACY)
    assert verdict.violations == [Finding(C.LOCATION, "ISO 6709 coordinates", None, "iCCP")]


# ===========================================================================
# Residual round (rulings R24-R26, R28): variants of C1 and I1 from the fix
# wave's re-review (probes rr_attack.py and rr_attack2.py). A structure that
# may appear once is trusted once: repeats are dropped, and what the first
# instance keeps is bounded by its own standard size.
# ===========================================================================

# ---------------------------------------------------------------------------
# R24: one entry per (IFD, tag) -- repeats are reported as UNKNOWN and never
# written back -- and one EXIF block per file.
# ---------------------------------------------------------------------------

SECRET8 = [QUIET_SECRET[i:i + 8].ljust(8, b".") for i in range(0, len(QUIET_SECRET), 8)]  # one RATIONAL each


def _rational_detail(value: bytes) -> str:
    """How an 8-byte big-endian RATIONAL value reads back as a Finding detail."""
    return f"{int.from_bytes(value[:4], 'big')}/{int.from_bytes(value[4:], 'big')}"


def _tiff_repeated_rational(tag: int, pieces: list[bytes], *, orientation: bool = True) -> bytes:
    """IFD0 = [Orientation] + one RATIONAL `tag` entry per piece, the values laid out back to back."""
    first = [_orientation()] if orientation else []
    vals_off = 8 + _ifd_len(len(first) + len(pieces))
    entries = first + [(tag, 5, 1, struct.pack(">I", vals_off + 8 * k)) for k in range(len(pieces))]
    return tiff_header() + tiff_ifd(entries) + b"".join(pieces)


def _tiff_repeated_gamma_in_exif_ifd(pieces: list[bytes]) -> bytes:
    """IFD0 = Orientation + ExifIFD pointer; the ExifIFD holds one Gamma (RATIONAL) entry per piece."""
    exif_off = 8 + _ifd_len(2)
    vals_off = exif_off + _ifd_len(len(pieces))
    ifd0 = tiff_ifd([_orientation(), (0x8769, 4, 1, struct.pack(">I", exif_off))])
    exif = tiff_ifd([(0xA500, 5, 1, struct.pack(">I", vals_off + 8 * k)) for k in range(len(pieces))])
    return tiff_header() + ifd0 + exif + b"".join(pieces)


def _ifd_findings(path, ifd: str) -> list[tuple[str, str | None]]:
    """`(label, detail)` of every finding `inspect` reports inside the EXIF IFD named `ifd`."""
    return [(f.label, f.detail) for f in siftool.inspect(path).findings if f.where.endswith("/" + ifd)]


@pytest.mark.parametrize("mode", [Mode.PRIVACY, Mode.PHOTOGRAPHER])
@pytest.mark.parametrize("fmt", sorted(EXIF_CARRIERS))
def test_repeated_xresolution_keeps_only_its_first_entry(tmp_path, fmt, mode):  # rr_attack E1
    wrap, ext = EXIF_CARRIERS[fmt]
    res, out = _clean(tmp_path, wrap(_tiff_repeated_rational(0x011A, SECRET8)), ext, mode)
    assert res.verdict.status is Status.VERIFIED and not [p for p in SECRET8[1:] if p in out]
    assert _ifd_findings(tmp_path / f"out_{mode.value}{ext}", "IFD0") == [
        ("Orientation", "1"), ("XResolution", _rational_detail(SECRET8[0]))]


def test_kilobytes_in_repeated_xresolution_are_not_carried_over(tmp_path):  # rr_attack E1b
    blob = bytes(range(256)) * 28  # 7,168 bytes across 896 XResolution values
    pieces = [blob[i:i + 8] for i in range(0, len(blob), 8)]
    data = _exif_in_jpeg(_tiff_repeated_rational(0x011A, pieces, orientation=False))
    res, out = _clean(tmp_path, data, ".jpg", Mode.PRIVACY)
    assert res.verdict.status is Status.VERIFIED and blob[8:] not in out
    assert _ifd_findings(tmp_path / "out_privacy.jpg", "IFD0") == [("XResolution", _rational_detail(blob[:8]))]


@pytest.mark.parametrize("fmt", sorted(EXIF_CARRIERS))
def test_repeated_gamma_in_the_exif_ifd_keeps_only_its_first_entry(tmp_path, fmt):  # rr_attack E2
    wrap, ext = EXIF_CARRIERS[fmt]
    res, out = _clean(tmp_path, wrap(_tiff_repeated_gamma_in_exif_ifd(SECRET8)), ext, Mode.PRIVACY)
    assert res.verdict.status is Status.VERIFIED and not [p for p in SECRET8[1:] if p in out]
    assert _ifd_findings(tmp_path / f"out_privacy{ext}", "ExifIFD") == [("Gamma", _rational_detail(SECRET8[0]))]


def _tiff_repeated_xres_in_exif_ifd(pieces: list[bytes]) -> bytes:
    """IFD0 = Orientation + ExifIFD pointer; the ExifIFD holds one XResolution (RATIONAL) entry per piece.

    XResolution's home is IFD0 -- every copy here sits in the "wrong" IFD, the
    same layout as `_tiff_repeated_gamma_in_exif_ifd` but for a tag that does
    not belong there.
    """
    exif_off = 8 + _ifd_len(2)
    vals_off = exif_off + _ifd_len(len(pieces))
    ifd0 = tiff_ifd([_orientation(), (0x8769, 4, 1, struct.pack(">I", exif_off))])
    exif = tiff_ifd([(0x011A, 5, 1, struct.pack(">I", vals_off + 8 * k)) for k in range(len(pieces))])
    return tiff_header() + ifd0 + exif + b"".join(pieces)


@pytest.mark.parametrize("fmt", sorted(EXIF_CARRIERS))
def test_cross_ifd_duplicate_tag_does_not_survive_privacy_clean(tmp_path, fmt):  # re-review
    # Six XResolution entries in the ExifIFD -- its "wrong" IFD -- carrying
    # QUIET_SECRET's bytes (SECRET8). Before the fix, the FIRST entry (the
    # only one the old R24 "first entry per tag" rule didn't already drop)
    # would have been kept via _resolve's IFD0/ExifIFD fallback and leaked
    # into a VERIFIED output.
    wrap, ext = EXIF_CARRIERS[fmt]
    res, out = _clean(tmp_path, wrap(_tiff_repeated_xres_in_exif_ifd(SECRET8)), ext, Mode.PRIVACY)
    assert res.verdict.status is Status.VERIFIED
    assert QUIET_SECRET not in out and not [p for p in SECRET8 if p in out]


def test_thumbnail_split_across_repeated_xresolution_is_removed(tmp_path):  # rr_attack2
    buf = io.BytesIO()
    Image.new("RGB", (24, 16), (200, 30, 30)).save(buf, format="JPEG", quality=50)
    thumbnail = buf.getvalue() + b"\x00" * (-len(buf.getvalue()) % 8)
    pieces = [thumbnail[i:i + 8] for i in range(0, len(thumbnail), 8)]
    res, out = _clean(tmp_path, _exif_in_jpeg(_tiff_repeated_rational(0x011A, pieces)), ".jpg", Mode.PRIVACY)
    assert res.verdict.status is Status.VERIFIED and thumbnail[8:] not in out
    assert [f.label for f in res.removed] == ["Duplicate tag 0x011A"] * (len(pieces) - 1)
    again = siftool.clean(tmp_path / "out_privacy.jpg", tmp_path / "again.jpg", Mode.PRIVACY)
    assert again.verdict.status is Status.VERIFIED and (tmp_path / "again.jpg").read_bytes() == out


def test_repeated_tags_are_seen_by_inspect_and_verify(tmp_path):
    src = write(tmp_path, "third_party.jpg", _exif_in_jpeg(_tiff_repeated_rational(0x011A, SECRET8)))
    duplicates = [Finding(C.UNKNOWN, "Duplicate tag 0x011A", None, "APP1/Exif/IFD0", 8)] * (len(SECRET8) - 1)
    assert [f for f in siftool.inspect(src).findings if f.category is C.UNKNOWN] == duplicates
    verdict = siftool.verify(src, Mode.PRIVACY)
    assert verdict.status is Status.FAILED and verdict.violations == duplicates


def _single_tag_tiff(value: bytes) -> bytes:
    """A canonical big-endian TIFF: IFD0 = one XResolution whose RATIONAL is `value` (8 bytes)."""
    return tiff_header() + tiff_ifd([(0x011A, 5, 1, struct.pack(">I", 26))]) + value


def test_second_png_exif_chunk_is_dropped(tmp_path):  # rr_attack2
    first, second = _single_tag_tiff(QUIET_SECRET[:8]), _single_tag_tiff(QUIET_SECRET[8:16])
    res, out = _clean(tmp_path, _bare_png(png_chunk(b"eXIf", first), png_chunk(b"eXIf", second)), ".png", Mode.PRIVACY)
    assert res.verdict.status is Status.VERIFIED and out == _bare_png(png_chunk(b"eXIf", first))
    assert res.removed == [Finding(C.UNKNOWN, "Extra EXIF block", None, "eXIf", len(second))]


# ---------------------------------------------------------------------------
# R25 + R28: a PNG chunk the spec allows once keeps only its first instance in
# its legal position. That instance keeps its own value bytes -- the accepted
# limit of a few free bytes inside one valid value -- and every other copy is
# dropped.
# ---------------------------------------------------------------------------


def _pieces(secret: bytes, size: int) -> list[bytes]:
    return [secret[i:i + size].ljust(size, b".") for i in range(0, len(secret), size)]


def _bare_png_after_idat(*chunks: bytes) -> bytes:
    """The fixture's bare 16x16 RGB PNG with `chunks` right before its IEND, after its IDAT."""
    png = make_png(**_BARE_PNG)
    at = png.index(b"IEND") - 4
    return png[:at] + b"".join(chunks) + png[at:]


REPEATED_APPEARANCE = {  # rr_attack P1 (2x cHRM), P1b (12x gAMA); rr_attack2 (5x pHYs)
    "2x-cHRM": (b"cHRM", _pieces(QUIET_SECRET, 32)),
    "12x-gAMA": (b"gAMA", _pieces(QUIET_SECRET, 4)),
    "5x-pHYs": (b"pHYs", _pieces(QUIET_SECRET[:45], 9)),
}


@pytest.mark.parametrize("mode", [Mode.PRIVACY, Mode.PHOTOGRAPHER])
@pytest.mark.parametrize("case", sorted(REPEATED_APPEARANCE))
def test_repeated_appearance_chunk_keeps_only_its_first_instance(tmp_path, case, mode):
    ctype, pieces = REPEATED_APPEARANCE[case]
    chunks = [png_chunk(ctype, piece) for piece in pieces]
    res, out = _clean(tmp_path, _bare_png(*chunks), ".png", mode)
    assert res.verdict.status is Status.VERIFIED and out == _bare_png(chunks[0])


def test_five_hundred_mdcv_chunks_leave_one(tmp_path):  # rr_attack P1c
    chunks = [png_chunk(b"mDCV", (b"owner=Felipe Floriani %04d " % k)[:24]) for k in range(500)]
    res, out = _clean(tmp_path, _bare_png(*chunks), ".png", Mode.PRIVACY)
    assert res.verdict.status is Status.VERIFIED and out == _bare_png(chunks[0])
    assert out.count(b"owner=Felipe Floriani") == 1
    assert res.removed == [Finding(C.UNKNOWN, "Misplaced or repeated mDCV", None, "mDCV", 24)] * 499


def test_appearance_chunk_after_idat_is_dropped(tmp_path):  # rr_attack2
    res, out = _clean(tmp_path, _bare_png_after_idat(png_chunk(b"cHRM", QUIET_SECRET[:32])), ".png", Mode.PRIVACY)
    assert res.verdict.status is Status.VERIFIED and out == make_png(**_BARE_PNG)
    assert res.removed == [Finding(C.UNKNOWN, "Misplaced or repeated cHRM", None, "cHRM", 32)]


# ---------------------------------------------------------------------------
# JPEG: only the first JFIF APP0 (R25), the first APP1 Exif (R24) and the
# first complete, valid ICC profile (R26) are kept.
# ---------------------------------------------------------------------------


def test_repeated_jfif_segments_keep_only_the_first(tmp_path):  # rr_attack J1
    jfifs = [_segment(0xE0, b"JFIF\x00" + piece) for piece in _pieces(QUIET_SECRET, 9)]
    base = base_jpeg()
    app0_end = 4 + int.from_bytes(base[4:6], "big")  # Pillow's own JFIF APP0 comes seventh here
    res, out = _clean(tmp_path, base[:2] + b"".join(jfifs) + base[2:], ".jpg", Mode.PRIVACY)
    assert res.verdict.status is Status.VERIFIED and out == base[:2] + jfifs[0] + base[app0_end:]


def test_second_jpeg_exif_segment_is_dropped(tmp_path):
    first, second = (_segment(0xE1, b"Exif\x00\x00" + _single_tag_tiff(v)) for v in (QUIET_SECRET[:8], QUIET_SECRET[8:16]))
    res, out = _clean(tmp_path, _jpeg_after_soi(first + second), ".jpg", Mode.PRIVACY)
    assert res.verdict.status is Status.VERIFIED and out == _jpeg_after_soi(first)


def test_second_jpeg_icc_profile_is_dropped(tmp_path):
    first = _segment(0xE2, b"ICC_PROFILE\x00\x01\x01" + _icc_desc("Display P3"))
    second = _segment(0xE2, b"ICC_PROFILE\x00\x01\x01" + _icc_desc("owner=Felipe Floriani"))
    res, out = _clean(tmp_path, _jpeg_after_soi(first + second), ".jpg", Mode.PRIVACY)
    assert res.verdict.status is Status.VERIFIED and out == _jpeg_after_soi(first)


# ---------------------------------------------------------------------------
# WEBP: only the first EXIF (R24) and the first ICCP (R26) chunk are kept,
# and the VP8X flags follow.
# ---------------------------------------------------------------------------


def _with_size_field(profile: bytes) -> bytes:
    """`profile` with its ICC header size field set to its real length."""
    return struct.pack(">I", len(profile)) + profile[4:]


def test_second_webp_iccp_is_dropped(tmp_path):  # rr_attack W5
    good = _icc_desc("Display P3")
    slack = _with_size_field(good + QUIET_SECRET)
    data = _riff(_riff_chunk(b"VP8X", _vp8x(0x20)) + _riff_chunk(b"ICCP", good) + _riff_chunk(b"ICCP", slack)
                 + _riff_chunk(b"VP8L", _vp8l()))
    res, out = _clean(tmp_path, data, ".webp", Mode.PRIVACY)
    assert res.verdict.status is Status.VERIFIED
    assert out == _riff(_riff_chunk(b"VP8X", _vp8x(0x20)) + _riff_chunk(b"ICCP", good) + _riff_chunk(b"VP8L", _vp8l()))


def test_second_webp_exif_chunk_is_dropped(tmp_path):
    first, second = _single_tag_tiff(QUIET_SECRET[:8]), _single_tag_tiff(QUIET_SECRET[8:16])
    head = _riff_chunk(b"VP8X", _vp8x(0x08)) + _riff_chunk(b"VP8L", _vp8l())
    res, out = _clean(tmp_path, _riff(head + _riff_chunk(b"EXIF", first) + _riff_chunk(b"EXIF", second)), ".webp",
                      Mode.PRIVACY)
    assert res.verdict.status is Status.VERIFIED and out == _riff(head + _riff_chunk(b"EXIF", first))


# ---------------------------------------------------------------------------
# R26: an ICC profile carries no bytes outside its tags' data (bar 4-byte
# alignment padding), in every carrier (rr_attack J4/J4b/J4c/J5, rr_attack2).
# ---------------------------------------------------------------------------


def _zero_tag_wrapper(text: bytes) -> bytes:
    """A 128-byte header with `acsp` and a tag table of 0 tags, then `text`: R20 alone took it for a profile."""
    header = bytearray(128)
    header[36:40] = b"acsp"
    return _with_size_field(bytes(header) + struct.pack(">I", 0) + text)


ICC_WITH_FREE_BYTES = {
    "zero-tag-wrapper": _zero_tag_wrapper(QUIET_SECRET),
    "valid-profile-plus-slack": _with_size_field(_icc_desc("Display P3") + QUIET_SECRET),
}


@pytest.mark.parametrize("case", sorted(ICC_WITH_FREE_BYTES))
def test_jpeg_icc_profile_with_free_bytes_is_dropped(tmp_path, case):  # rr_attack J4, J5
    data = _jpeg_after_soi(_segment(0xE2, b"ICC_PROFILE\x00\x01\x01" + ICC_WITH_FREE_BYTES[case]))
    res, out = _clean(tmp_path, data, ".jpg", Mode.PRIVACY)
    assert res.verdict.status is Status.VERIFIED and out == base_jpeg()


@pytest.mark.parametrize("case", sorted(ICC_WITH_FREE_BYTES))
def test_webp_iccp_with_free_bytes_is_dropped(tmp_path, case):  # rr_attack J4b
    data = _riff(_riff_chunk(b"VP8X", _vp8x(0x20)) + _riff_chunk(b"ICCP", ICC_WITH_FREE_BYTES[case])
                 + _riff_chunk(b"VP8L", _vp8l()))
    res, out = _clean(tmp_path, data, ".webp", Mode.PRIVACY)
    assert res.verdict.status is Status.VERIFIED
    assert out == _riff(_riff_chunk(b"VP8X", _vp8x(0)) + _riff_chunk(b"VP8L", _vp8l()))


@pytest.mark.parametrize("case", sorted(ICC_WITH_FREE_BYTES))
def test_png_iccp_with_free_bytes_is_dropped(tmp_path, case):  # rr_attack J4c, rr_attack2
    data = _bare_png(png_chunk(b"iCCP", b"x\x00\x00" + zlib.compress(ICC_WITH_FREE_BYTES[case])))
    res, out = _clean(tmp_path, data, ".png", Mode.PRIVACY)
    assert res.verdict.status is Status.VERIFIED and out == make_png(**_BARE_PNG)
