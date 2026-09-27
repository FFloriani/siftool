import io, struct, pytest
from siftool.model import Category as C, Kind, Mode
from siftool.planner import plan_container
from siftool.formats.webp import WebpHandler, VP8X_ICC, VP8X_EXIF, VP8X_XMP, VP8X_ANIM
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
    assert flags(out) & VP8X_ICC and flags(out) & VP8X_EXIF and not flags(out) & VP8X_XMP
    assert {t for t, _ in riff_chunks(out)} <= {b"VP8X", b"VP8L", b"ICCP", b"ALPH", b"EXIF"}
    [exif] = [b for b in H.parse(out).blocks if b.tag == "EXIF"]
    assert {f.label for f in exif.findings} == {"Orientation", "ColorSpace"}

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

# ---------------------------------------------------------------------------
# Task 9b: VP8X/ANIM's payload size is fixed by the format; write() copies
# an ESSENTIAL chunk byte-for-byte and verify() never signature-scans it, so
# an oversize VP8X/ANIM could otherwise smuggle a secret past both.
# ---------------------------------------------------------------------------

def _rebuild_with_chunk(data: bytes, fourcc: bytes, payload: bytes) -> bytes:
    """Rebuild a WEBP RIFF, replacing the first top-level chunk of type
    `fourcc` with one carrying `payload`, and fixing up the RIFF size field.
    Every other chunk is copied unchanged; trailing data (if any) is kept."""
    riff_size = int.from_bytes(data[4:8], "little")
    trailing = data[8 + riff_size :]
    body = bytearray()
    replaced = False
    for tag, chunk_payload in riff_chunks(data):
        if tag == fourcc and not replaced:
            chunk_payload = payload
            replaced = True
        body += tag + struct.pack("<I", len(chunk_payload)) + chunk_payload
        if len(chunk_payload) % 2:
            body += b"\x00"
    assert replaced
    body = bytes(body)
    return b"RIFF" + struct.pack("<I", len(body) + 4) + b"WEBP" + body + trailing

def test_oversize_vp8x_is_corrupt():
    src = make_webp("lossless")
    vp8x = dict(riff_chunks(src))[b"VP8X"]
    secret = b" GPS +23.5505-046.6333 owner=Felipe"
    bad = _rebuild_with_chunk(src, b"VP8X", vp8x + secret)
    with pytest.raises(CorruptFileError, match="invalid VP8X"): H.parse(bad)

def test_oversize_anim_is_corrupt():
    src = make_webp("animated")
    anim = dict(riff_chunks(src))[b"ANIM"]
    bad = _rebuild_with_chunk(src, b"ANIM", anim + b"\x00")
    with pytest.raises(CorruptFileError, match="invalid ANIM"): H.parse(bad)

# ---------------------------------------------------------------------------
# Controller ruling R15 (Task 9b fix round 1): the size rules alone are not
# enough -- a REPEATED VP8X/ANIM at its own legal size, a VP8X that isn't
# the first chunk, or an ANIM present without VP8X's own animation flag,
# are just as unscanned and byte-for-byte-trusted as an oversize one.
# ---------------------------------------------------------------------------

def _append_chunk(data: bytes, fourcc: bytes, payload: bytes) -> bytes:
    """Rebuild a WEBP RIFF with an extra top-level chunk of type `fourcc`
    appended after every existing one, fixing up the RIFF size field."""
    riff_size = int.from_bytes(data[4:8], "little")
    trailing = data[8 + riff_size :]
    pad = b"\x00" if len(payload) % 2 else b""
    body = data[12 : 8 + riff_size] + fourcc + struct.pack("<I", len(payload)) + payload + pad
    return b"RIFF" + struct.pack("<I", len(body) + 4) + b"WEBP" + body + trailing

def test_second_vp8x_is_corrupt():
    src = make_webp("lossless")
    vp8x = dict(riff_chunks(src))[b"VP8X"]
    bad = _append_chunk(src, b"VP8X", vp8x)
    with pytest.raises(CorruptFileError, match="invalid VP8X"): H.parse(bad)

def test_repeated_anim_is_corrupt():
    src = make_webp("animated")
    anim = dict(riff_chunks(src))[b"ANIM"]
    bad = _append_chunk(src, b"ANIM", anim)
    with pytest.raises(CorruptFileError, match="invalid ANIM"): H.parse(bad)

def test_anim_without_animation_flag_is_corrupt():
    src = make_webp("animated")
    vp8x = bytearray(dict(riff_chunks(src))[b"VP8X"])
    vp8x[0] &= ~VP8X_ANIM & 0xFF  # clear the flag; the ANIM chunk itself stays present
    bad = _rebuild_with_chunk(src, b"VP8X", bytes(vp8x))
    with pytest.raises(CorruptFileError, match="invalid ANIM"): H.parse(bad)
