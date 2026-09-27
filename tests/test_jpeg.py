import pytest
from siftool.model import Category as C, Kind, Mode
from siftool.planner import plan_container
from siftool.formats.jpeg import JpegHandler
from siftool.errors import CorruptFileError
from fixtures.make_fixtures import make_jpeg, base_jpeg, decode_pixels, MOTION_PHOTO_TRAILER, make_c2pa_payload
import io
import struct
import warnings

H = JpegHandler()
def tags(c): return [b.tag for b in c.blocks]
def by_tag(c, t): return [b for b in c.blocks if b.tag == t]
def clean_bytes(data, mode=Mode.PRIVACY):
    c = H.parse(data); buf = io.BytesIO()
    H.write(c, plan_container(c, mode, H.rewrite), buf); return buf.getvalue()

def test_parse_classifies_segments():
    c = H.parse(make_jpeg())
    [exif] = by_tag(c, "APP1:Exif"); assert exif.kind is Kind.METADATA and exif.rewritable
    [icc] = by_tag(c, "APP2:ICC"); assert icc.findings[0].category is C.APPEARANCE and icc.findings[0].detail == "Display P3"
    [j] = by_tag(c, "APP11:JUMBF"); assert j.findings[0].detail == "generator: ChatGPT · AI-generated"
    assert {C.IDENTITY, C.LOCATION} <= {f.category for f in by_tag(c, "APP13:IRB")[0].findings}
    [u] = by_tag(c, "APP15"); assert u.kind is Kind.UNKNOWN and u.removable
    assert by_tag(c, "COM")[0].findings[0].category is C.TEXT
    assert {"SOI", "DQT", "DHT", "SOS", "EOI"} <= set(tags(c)) and tags(c)[-1] == "EOI"

def test_motion_photo_trailer_is_detected():
    c = H.parse(make_jpeg(trailing=MOTION_PHOTO_TRAILER))
    assert tags(c)[-1] == "TRAILING" and c.trailing is not None
    assert "Motion Photo" in c.blocks[-1].findings[0].label and c.blocks[-1].findings[0].category is C.EXTRAS

def test_progressive_has_multiple_scans():
    c = H.parse(make_jpeg(progressive=True))
    assert tags(c).count("SOS") > 1 and tags(c)[-1] == "EOI"

def test_fill_bytes_are_tolerated():
    assert decode_pixels(clean_bytes(make_jpeg(fill_bytes=True))) == decode_pixels(make_jpeg(fill_bytes=True))

@pytest.mark.parametrize("data", [b"\x00\x00not a jpeg", base_jpeg()[:-200]], ids=["no-soi", "truncated"])
def test_missing_soi_or_truncated_is_corrupt(data):
    with pytest.raises(CorruptFileError): H.parse(data)

def test_reserved_marker_is_unknown_not_removable():
    [r] = by_tag(H.parse(make_jpeg(reserved_marker=True)), "RESERVED:0xF0")
    assert r.kind is Kind.UNKNOWN and not r.removable

def test_privacy_output_keeps_only_appearance_and_minimal_exif():
    out = H.parse(clean_bytes(make_jpeg(mpf=True, trailing=MOTION_PHOTO_TRAILER)))
    meta = {b.tag for b in out.blocks if b.kind is not Kind.ESSENTIAL}
    assert meta <= {"APP0:JFIF", "APP1:Exif", "APP2:ICC"}
    assert {f.label for f in by_tag(out, "APP1:Exif")[0].findings} == {"Orientation", "ColorSpace"}

def test_digest_and_pixels_are_preserved():
    src = make_jpeg(trailing=MOTION_PHOTO_TRAILER); out = clean_bytes(src, Mode.MAXIMUM)
    assert H.essential_digest(H.parse(out)) == H.essential_digest(H.parse(src))
    assert decode_pixels(out) == decode_pixels(src)

def test_jfif_thumbnail_is_rewritten_without_thumbnail():
    [app0] = by_tag(H.parse(clean_bytes(make_jpeg(jfif_thumbnail=True))), "APP0:JFIF")
    assert app0.span.length == 18 and {f.category for f in app0.findings} == {C.APPEARANCE}

def test_app11_jumbf_with_nonzero_box_instance_is_c2pa():
    base = make_jpeg(c2pa=False)
    end = 4 + int.from_bytes(base[4:6], "big")  # right after Pillow's own APP0
    payload = b"JP" + b"\x02\x11" + b"\x00\x00\x00\x01" + make_c2pa_payload()  # CI + En=0x0211 + Z=1
    segment = b"\xff\xeb" + struct.pack(">H", len(payload) + 2) + payload
    c = H.parse(base[:end] + segment + base[end:])
    [app11] = by_tag(c, "APP11:JUMBF")
    [finding] = app11.findings
    assert finding.category is C.AI_PROVENANCE and finding.label == "C2PA manifest"
    assert finding.detail == "generator: ChatGPT · AI-generated"

def test_decode_pixels_of_mpf_fixture_is_silent():
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        frames = decode_pixels(make_jpeg(mpf=True))
    assert frames
