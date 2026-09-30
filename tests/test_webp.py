import io, struct, pytest
import siftool
from PIL import Image
from siftool import Finding, Status
from siftool.model import Category as C, Kind, Mode
from siftool.planner import plan_container
from siftool.formats.webp import WebpHandler, VP8X_ICC, VP8X_EXIF, VP8X_XMP, VP8X_ANIM
from siftool.errors import CorruptFileError
from fixtures.make_fixtures import make_webp, riff_chunks, decode_pixels, make_icc, tiff_header, tiff_ifd, write

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

# ---------------------------------------------------------------------------
# Final-review fix wave. Ruling R19 (C2, C3, I2, triage T8/T9b): an ANMF is
# trusted only as a 16-byte frame header + (ALPH? VP8 | VP8L); a still image
# has exactly one bitstream (ALPH only right before a VP8); stray bitstream
# chunks are dropped; a file with no image data is corrupt. Ruling R20 (I1):
# an ICCP must be a structurally valid ICC profile.
# ---------------------------------------------------------------------------

def _chunk(fourcc, payload):
    return fourcc + struct.pack("<I", len(payload)) + payload + (b"\x00" if len(payload) % 2 else b"")

def _riff(body):
    return b"RIFF" + struct.pack("<I", len(body) + 4) + b"WEBP" + body

def _vp8x(flags):
    side = (32 - 1).to_bytes(3, "little")
    return bytes([flags, 0, 0, 0]) + side + side

def _pillow_webp(img, **kw):
    buf = io.BytesIO(); img.save(buf, format="WEBP", **kw); return buf.getvalue()

def _rgba():
    return Image.new("RGBA", (32, 32), (255, 0, 0, 128))

VP8L = dict(riff_chunks(make_webp("lossless", meta=False, c2pa=False, unknown_chunk=False)))[b"VP8L"]
ALPH, VP8 = (dict(riff_chunks(_pillow_webp(_rgba(), lossless=False)))[t] for t in (b"ALPH", b"VP8 "))
ODD_VP8L = dict(riff_chunks(_pillow_webp(_rgba(), lossless=True)))[b"VP8L"]  # 15 bytes: needs a pad byte
ANIM_PAYLOAD = dict(riff_chunks(make_webp("animated", meta=False, c2pa=False, unknown_chunk=False)))[b"ANIM"]
FRAME_HEADER = bytes(6) + (31).to_bytes(3, "little") * 2 + (100).to_bytes(3, "little") + b"\x00"

def _animated(*anmf_payloads, extra=b""):
    body = _chunk(b"VP8X", _vp8x(VP8X_ANIM)) + _chunk(b"ANIM", ANIM_PAYLOAD)
    return _riff(body + b"".join(_chunk(b"ANMF", p) for p in anmf_payloads) + extra)

def layout(data):
    return [(b.tag, b.kind, b.removable) for b in H.parse(data).blocks]

E, U = Kind.ESSENTIAL, Kind.UNKNOWN

def test_anmf_frames_from_real_encoders_are_essential(tmp_path):
    frames = [Image.new("RGBA", (32, 32), c) for c in ((255, 0, 0, 128), (0, 255, 0, 255), (0, 0, 255, 60))]
    for lossless in (True, False):  # VP8L frames; ALPH+VP8 and bare VP8 frames
        buf = io.BytesIO()
        frames[0].save(buf, format="WEBP", save_all=True, append_images=frames[1:], lossless=lossless, duration=100)
        data = buf.getvalue()
        assert [t for t, k, _ in layout(data)] == ["VP8X", "ANIM", "ANMF", "ANMF", "ANMF"]
        assert all(k is E for _, k, _ in layout(data))
        for mode in Mode:
            res = siftool.clean(write(tmp_path, f"a{lossless}.webp", data), tmp_path / f"o{lossless}{mode.value}.webp", mode)
            assert res.verdict.status is Status.VERIFIED
            assert decode_pixels((tmp_path / f"o{lossless}{mode.value}.webp").read_bytes()) == decode_pixels(data)

def test_alpha_still_image_from_real_encoder_is_verified(tmp_path):
    data = _pillow_webp(_rgba(), lossless=False)
    assert layout(data) == [("VP8X", E, True), ("ALPH", E, True), ("VP8", E, True)]
    res = siftool.clean(write(tmp_path, "a.webp", data), tmp_path / "o.webp")
    assert res.verdict.status is Status.VERIFIED and (tmp_path / "o.webp").read_bytes() == data

def _frame(*subchunks):
    return FRAME_HEADER + b"".join(subchunks)

NON_STANDARD_ANMF = {
    "unknown-subchunk": _frame(_chunk(b"VP8L", VP8L), _chunk(b"ABCD", b"junk")),
    "xmp-subchunk": _frame(_chunk(b"VP8L", VP8L), _chunk(b"XMP ", b"<x:xmpmeta/>")),
    "vp8l-then-alph": _frame(_chunk(b"VP8L", VP8L), _chunk(b"ALPH", ALPH)),
    "alph-then-vp8l": _frame(_chunk(b"ALPH", ALPH), _chunk(b"VP8L", VP8L)),
    "two-vp8l": _frame(_chunk(b"VP8L", VP8L), _chunk(b"VP8L", VP8L)),
    "alph-only": _frame(_chunk(b"ALPH", ALPH)),
    "size-overrun": _frame(b"VP8L" + struct.pack("<I", len(VP8L) + 9) + VP8L),
    "missing-pad": _frame(b"VP8L" + struct.pack("<I", len(ODD_VP8L)) + ODD_VP8L),
    "trailing-byte": _frame(_chunk(b"VP8L", VP8L)) + b"\x00",
    "header-only": FRAME_HEADER,
    "short-header": FRAME_HEADER[:10],
}

@pytest.mark.parametrize("case", sorted(NON_STANDARD_ANMF))
def test_non_standard_anmf_is_demoted_not_trusted(case):
    assert len(ODD_VP8L) % 2  # "missing-pad" relies on an odd-sized VP8L
    payload = NON_STANDARD_ANMF[case]
    good = _frame(_chunk(b"VP8L", VP8L))
    c = H.parse(_animated(good, payload))
    [demoted] = [b for b in c.blocks if b.tag == "ANMF?"]
    assert demoted.kind is U and demoted.removable is False
    assert demoted.findings == [Finding(C.UNKNOWN, "Non-standard ANMF chunk", None, "ANMF?", len(payload))]
    assert [b.tag for b in c.blocks] == ["VP8X", "ANIM", "ANMF", "ANMF?"]

STRAY_LAYOUTS = {
    "second-vp8l": (_riff(_chunk(b"VP8L", VP8L) + _chunk(b"VP8L", VP8L)),
                    [("VP8L", E, True), ("VP8L", U, True)]),
    "alph-after-vp8l": (_riff(_chunk(b"VP8X", _vp8x(0x10)) + _chunk(b"VP8L", VP8L) + _chunk(b"ALPH", ALPH)),
                        [("VP8X", E, True), ("VP8L", E, True), ("ALPH", U, True)]),
    "alph-before-vp8l": (_riff(_chunk(b"VP8X", _vp8x(0x10)) + _chunk(b"ALPH", ALPH) + _chunk(b"VP8L", VP8L)),
                         [("VP8X", E, True), ("ALPH", U, True), ("VP8L", E, True)]),
    "two-alph-before-vp8": (_riff(_chunk(b"VP8X", _vp8x(0x10)) + _chunk(b"ALPH", ALPH) + _chunk(b"ALPH", ALPH)
                                  + _chunk(b"VP8 ", VP8)),
                            [("VP8X", E, True), ("ALPH", U, True), ("ALPH", E, True), ("VP8", E, True)]),
    "alph-not-adjacent": (_riff(_chunk(b"VP8X", _vp8x(0x30)) + _chunk(b"ALPH", ALPH)
                                + _chunk(b"ICCP", make_icc("Display P3")) + _chunk(b"VP8 ", VP8)),
                          [("VP8X", E, True), ("ALPH", U, True), ("ICCP", Kind.METADATA, True), ("VP8", E, True)]),
    "alph-after-vp8": (_riff(_chunk(b"VP8X", _vp8x(0x10)) + _chunk(b"ALPH", ALPH) + _chunk(b"VP8 ", VP8)
                             + _chunk(b"ALPH", ALPH)),
                       [("VP8X", E, True), ("ALPH", E, True), ("VP8", E, True), ("ALPH", U, True)]),
    "bitstream-in-animation": (_animated(_frame(_chunk(b"VP8L", VP8L)), extra=_chunk(b"VP8L", VP8L)),
                               [("VP8X", E, True), ("ANIM", E, True), ("ANMF", E, True), ("VP8L", U, True)]),
    "anmf-in-still-image": (_riff(_chunk(b"VP8X", _vp8x(0)) + _chunk(b"VP8L", VP8L)
                                  + _chunk(b"ANMF", _frame(_chunk(b"VP8L", VP8L)))),
                            [("VP8X", E, True), ("VP8L", E, True), ("ANMF", U, True)]),
}

@pytest.mark.parametrize("case", sorted(STRAY_LAYOUTS))
def test_stray_bitstream_chunks_are_removable_unknown(case):
    data, expected = STRAY_LAYOUTS[case]
    assert layout(data) == expected
    for block in H.parse(data).blocks:
        if block.kind is U:
            assert block.label == f"Unused {block.tag} chunk" and block.findings == []
    out = H.parse(clean_bytes(data, Mode.PRIVACY))
    assert [(b.tag, b.kind) for b in out.blocks] == [(t, k) for t, k, _ in expected if k is not U]

MISSING_IMAGE = {
    "riff-size-0": b"RIFF" + struct.pack("<I", 0) + b"WEBP" + _chunk(b"VP8L", VP8L),
    "riff-size-4": b"RIFF" + struct.pack("<I", 4) + b"WEBP" + _chunk(b"VP8L", VP8L),
    "riff-ends-after-vp8x": b"RIFF" + struct.pack("<I", 22) + b"WEBP" + _chunk(b"VP8X", _vp8x(0)) + _chunk(b"VP8L", VP8L),
    "only-unknown-chunk": _riff(_chunk(b"ABCD", b"junk")),
    "still-without-bitstream": _riff(_chunk(b"VP8X", _vp8x(0x20)) + _chunk(b"ICCP", make_icc("Display P3"))),
    "animation-without-frames": _animated(),
}

@pytest.mark.parametrize("case", sorted(MISSING_IMAGE))
def test_webp_without_image_data_is_corrupt(case):
    with pytest.raises(CorruptFileError, match="^missing image data$"):
        H.parse(MISSING_IMAGE[case])

def test_iccp_must_be_a_valid_icc_profile():
    good = make_icc("Display P3", 4)
    for profile, expected in ((good, (Kind.METADATA, "ICC color profile")),
                              (b"not a profile at all", (U, "Invalid ICC profile")),
                              (struct.pack(">I", len(good) + 2) + good[4:], (U, "Invalid ICC profile"))):
        data = _riff(_chunk(b"VP8X", _vp8x(0x20)) + _chunk(b"ICCP", profile) + _chunk(b"VP8L", VP8L))
        [iccp] = [b for b in H.parse(data).blocks if b.tag == "ICCP"]
        assert (iccp.kind, iccp.label) == expected and iccp.removable
    [iccp] = [b for b in H.parse(_riff(_chunk(b"VP8X", _vp8x(0x20)) + _chunk(b"ICCP", good) + _chunk(b"VP8L", VP8L))).blocks
              if b.tag == "ICCP"]
    assert iccp.findings == [Finding(C.APPEARANCE, "ICC profile", "Display P3", "ICCP", len(good))]

def test_every_webp_finding_says_where_it_is():  # review M2 ("check PNG/WEBP too")
    blocks = H.parse(make_webp("lossless", trailing=b"tail")).blocks
    assert {b.tag for b in blocks if b.findings} == {"ICCP", "EXIF", "XMP", "C2PA", "TRAILING"}
    assert all(f.where.startswith(b.tag) for b in blocks for f in b.findings)

# ---------------------------------------------------------------------------
# Residual round. Ruling R24: one EXIF block per file; ruling R26: one ICC
# profile per file. The first EXIF/ICCP chunk keeps its classification;
# every later one is a removable UNKNOWN block, dropped, and the VP8X flags
# follow what is left.
# ---------------------------------------------------------------------------

def _still(*chunks, flags=0):
    return _riff(_chunk(b"VP8X", _vp8x(flags)) + b"".join(chunks))

def test_only_the_first_exif_chunk_is_the_exif_block():
    tiffs = [tiff_header() + tiff_ifd([(0x0112, 3, 1, struct.pack(">HH", v, 0))]) for v in (6, 3)]
    data = _still(_chunk(b"VP8L", VP8L), _chunk(b"EXIF", tiffs[0]), _chunk(b"EXIF", tiffs[1]), flags=VP8X_EXIF)
    first, extra = [b for b in H.parse(data).blocks if b.tag == "EXIF"]
    assert (first.kind, first.rewritable, [f.detail for f in first.findings]) == (Kind.METADATA, True, ["6"])
    label = "Extra EXIF block"
    assert (extra.kind, extra.removable, extra.label, extra.findings) == (
        U, True, label, [Finding(C.UNKNOWN, label, None, "EXIF", len(tiffs[1]))])
    assert clean_bytes(data, Mode.PRIVACY) == _still(_chunk(b"VP8L", VP8L), _chunk(b"EXIF", tiffs[0]), flags=VP8X_EXIF)
    assert clean_bytes(data, Mode.MAXIMUM) == _still(_chunk(b"VP8L", VP8L))

def test_only_the_first_iccp_chunk_is_kept():
    first, other = make_icc("Display P3", 4), make_icc("Felipe Floriani", 4)
    data = _still(_chunk(b"ICCP", first), _chunk(b"ICCP", other), _chunk(b"VP8L", VP8L), flags=VP8X_ICC)
    kept, extra = [b for b in H.parse(data).blocks if b.tag == "ICCP"]
    assert (kept.kind, kept.label) == (Kind.METADATA, "ICC color profile")
    label = "Extra ICC profile"
    assert (extra.kind, extra.removable, extra.label, extra.findings) == (
        U, True, label, [Finding(C.UNKNOWN, label, None, "ICCP", len(other))])
    assert clean_bytes(data, Mode.PRIVACY) == _still(_chunk(b"ICCP", first), _chunk(b"VP8L", VP8L), flags=VP8X_ICC)
    assert clean_bytes(data, Mode.MAXIMUM) == _still(_chunk(b"VP8L", VP8L))

def test_a_later_iccp_chunk_never_stands_in_for_an_invalid_first_one():
    data = _still(_chunk(b"ICCP", b"not a profile"), _chunk(b"ICCP", make_icc("Display P3", 4)), _chunk(b"VP8L", VP8L),
                  flags=VP8X_ICC)
    assert [(b.kind, b.label) for b in H.parse(data).blocks if b.tag == "ICCP"] == [
        (U, "Invalid ICC profile"), (U, "Extra ICC profile")]
    assert clean_bytes(data, Mode.PRIVACY) == _still(_chunk(b"VP8L", VP8L))
