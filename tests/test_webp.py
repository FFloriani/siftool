import io, struct, pytest
from siftool.model import Category as C, Kind, Mode
from siftool.planner import plan_container
from siftool.formats.webp import WebpHandler, VP8X_ICC, VP8X_EXIF, VP8X_XMP
from siftool.errors import CorruptFileError
from fixtures.make_fixtures import make_webp, riff_chunks, decode_pixels

H = WebpHandler()
def clean_bytes(data, mode=Mode.PRIVACY):
    c = H.parse(data); buf = io.BytesIO(); H.write(c, plan_container(c, mode, H.rewrite), buf); return buf.getvalue()
def flags(data): return dict(riff_chunks(data))[b"VP8X"][0]

def test_parse_extended_lossless():
    c = H.parse(make_webp("lossless", trailing=b"tail"))
    kinds = {b.tag: b.kind for b in c.blocks}
    assert kinds["VP8X"] is Kind.ESSENTIAL and kinds["VP8L"] is Kind.ESSENTIAL
    assert kinds["ABCD"] is Kind.UNKNOWN and c.blocks[-1].tag == "TRAILING"
    assert {f.category for b in c.blocks if b.tag == "C2PA" for f in b.findings} == {C.AI_PROVENANCE}

def test_privacy_updates_flags_and_riff_size():
    out = clean_bytes(make_webp("lossless", trailing=b"tail"))
    assert struct.unpack("<I", out[4:8])[0] == len(out) - 8
    assert flags(out) & VP8X_ICC and not flags(out) & (VP8X_EXIF | VP8X_XMP)
    assert {t for t, _ in riff_chunks(out)} <= {b"VP8X", b"VP8L", b"ICCP", b"ALPH"}

def test_maximum_clears_icc_and_photographer_keeps_exif():
    assert not flags(clean_bytes(make_webp("lossy"), Mode.MAXIMUM)) & VP8X_ICC
    ph = clean_bytes(make_webp("lossy"), Mode.PHOTOGRAPHER)
    assert flags(ph) & VP8X_EXIF and b"Felipe" not in ph and b"TestCam" in ph

def test_lossless_and_animation_are_bit_identical():
    for kind in ("lossless", "animated"):
        src = make_webp(kind); out = clean_bytes(src)
        pick = lambda d: [v for t, v in riff_chunks(d) if t in (b"VP8L", b"VP8 ", b"ANMF", b"ANIM", b"ALPH")]
        assert pick(out) == pick(src) and decode_pixels(out) == decode_pixels(src)

def test_simple_without_metadata_is_byte_identical():
    src = make_webp("simple"); assert clean_bytes(src) == src

def test_riff_size_beyond_file_is_corrupt():
    src = make_webp("lossy"); bad = src[:4] + struct.pack("<I", len(src) * 2) + src[8:]
    with pytest.raises(CorruptFileError): H.parse(bad)
