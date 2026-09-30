import pytest
import siftool
from siftool.model import Category as C, Finding, Kind, Mode, Status
from siftool.planner import plan_container
from siftool.formats.jpeg import JpegHandler
from siftool.errors import CorruptFileError, VerificationFailedError
from fixtures.make_fixtures import (
    make_jpeg, base_jpeg, decode_pixels, MOTION_PHOTO_TRAILER, make_c2pa_payload, make_icc, tiff_header, tiff_ifd,
)
import io
import struct
import warnings
from PIL import Image

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

# ---------------------------------------------------------------------------
# Task 9b: APP14 "Adobe" is only ESSENTIAL at its one fixed 12-byte payload
# (write() copies an ESSENTIAL block byte-for-byte and verify() never
# signature-scans it, so an oversize one could otherwise smuggle a secret
# past both). Any other size is kept -- dropping it would shift CMYK
# colors -- but as a non-removable UNKNOWN block instead.
# ---------------------------------------------------------------------------

_ADOBE_STANDARD = b"Adobe" + b"\x00\x64\x00\x00\x00\x00\x01"
_SECRET = b" GPS +23.5505-046.6333 owner=Felipe"

def _insert_after_soi(data: bytes, marker: int, payload: bytes) -> bytes:
    """`data` with a raw length-prefixed segment (marker byte `marker`,
    `payload`) spliced in right after SOI."""
    segment = bytes([0xFF, marker]) + struct.pack(">H", len(payload) + 2) + payload
    return data[:2] + segment + data[2:]

def _with_app14(payload: bytes) -> bytes:
    """`base_jpeg()` with a raw APP14 segment carrying `payload` spliced in
    right after SOI."""
    return _insert_after_soi(base_jpeg(), 0xEE, payload)

def test_standard_adobe_segment_is_essential():
    [app14] = by_tag(H.parse(_with_app14(_ADOBE_STANDARD)), "APP14:Adobe")
    assert app14.kind is Kind.ESSENTIAL

def test_oversize_adobe_segment_is_kept_but_not_trusted():
    [app14] = by_tag(H.parse(_with_app14(_ADOBE_STANDARD + _SECRET)), "APP14")
    assert app14.kind is Kind.UNKNOWN and app14.removable is False
    [finding] = app14.findings
    assert (finding.category, finding.label, finding.where) == (C.UNKNOWN, "Non-standard Adobe segment", "APP14")

# ---------------------------------------------------------------------------
# Controller ruling R15 (Task 9b fix round 1):
# (a) only the FIRST standard-size Adobe APP14 is trusted as ESSENTIAL --
#     repeating it at its own legal 12-byte size, e.g. to smuggle a secret
#     split across several such markers, no longer works.
# (b) DRI/DNL's payload size is fixed by the format (2 bytes) and wasn't
#     checked at all before this round.
# (c) a SOF/SOS/DQT/DHT segment whose declared length disagrees with what
#     its own content (Nf/Ns/table sizes) implies is demoted to a scanned,
#     non-removable UNKNOWN block (tag "<NAME>?") instead of trusted as
#     ESSENTIAL -- the image still decodes (bytes are kept unchanged), but
#     the segment is no longer exempt from verify's signature scan.
# ---------------------------------------------------------------------------

def test_repeated_adobe_segments_are_not_essential(tmp_path):
    # Six Adobe-signature APP14 segments, each exactly 12 bytes (the
    # "legal" size): one real marker, and five carrying the brief's secret
    # split into 7-byte pieces after the "Adobe" signature (35 == 5 * 7).
    pieces = [_SECRET[i : i + 7] for i in range(0, len(_SECRET), 7)]
    assert len(pieces) == 5 and all(len(p) == 7 for p in pieces)
    segments = bytes([0xFF, 0xEE]) + struct.pack(">H", 14) + _ADOBE_STANDARD
    for piece in pieces:
        segments += bytes([0xFF, 0xEE]) + struct.pack(">H", 14) + b"Adobe" + piece
    base = base_jpeg()
    bad = base[:2] + segments + base[2:]

    c = H.parse(bad)
    standard = by_tag(c, "APP14:Adobe")
    assert len(standard) == 1 and standard[0].kind is Kind.ESSENTIAL
    demoted = by_tag(c, "APP14")
    assert len(demoted) == 5
    assert all(b.kind is Kind.UNKNOWN and b.removable is False for b in demoted)

    src = tmp_path / "bad.jpg"; src.write_bytes(bad)
    with pytest.raises(VerificationFailedError):
        siftool.clean(src, tmp_path / "clean.jpg")

def test_oversize_dri_is_corrupt():
    bad = _insert_after_soi(base_jpeg(), 0xDD, b"\x00\x00\x00\x00")
    with pytest.raises(CorruptFileError, match="invalid DRI"): H.parse(bad)

def test_oversize_dnl_is_corrupt():
    bad = _insert_after_soi(base_jpeg(), 0xDC, b"\x00\x00\x00\x00")
    with pytest.raises(CorruptFileError, match="invalid DNL"): H.parse(bad)

def _first_segment_span(data: bytes, marker: int) -> tuple[int, int]:
    """Return `(start, end)` of the first length-prefixed segment with
    marker byte `marker`, scanning from SOI. Assumes `marker` appears
    before SOS (true for APPn/DQT/DHT/SOF/DRI/DNL) and that no restart
    markers or fill bytes precede it."""
    p = 2
    while True:
        code = data[p + 1]
        length = int.from_bytes(data[p + 2 : p + 4], "big")
        if code == marker:
            return p, p + 2 + length
        p = p + 2 + length

def _rebuild_with_segment(data: bytes, marker: int, payload: bytes) -> bytes:
    """Rebuild a JPEG, replacing the first segment with marker byte
    `marker` with one carrying `payload`; everything else (including SOS
    and the entropy-coded scan) is copied unchanged."""
    start, end = _first_segment_span(data, marker)
    segment = bytes([0xFF, marker]) + struct.pack(">H", len(payload) + 2) + payload
    return data[:start] + segment + data[end:]

def test_dqt_with_trailing_bytes_is_demoted(tmp_path):
    src = base_jpeg()
    start, end = _first_segment_span(src, 0xDB)
    original_payload = src[start + 4 : end]
    bad = _rebuild_with_segment(src, 0xDB, original_payload + b"\x00\x00\x00\x00")

    [dqt] = by_tag(H.parse(bad), "DQT?")
    assert dqt.kind is Kind.UNKNOWN and dqt.removable is False
    [finding] = dqt.findings
    assert (finding.category, finding.label, finding.where) == (C.UNKNOWN, "Non-standard DQT segment", "DQT?")

    src_path = tmp_path / "bad.jpg"; src_path.write_bytes(bad)
    dst = tmp_path / "clean.jpg"
    res = siftool.clean(src_path, dst)
    assert res.verdict.status is Status.WARNINGS
    assert "Unrecognized structure kept: DQT?" in res.verdict.notes
    # base_jpeg() carries no metadata beyond APPEARANCE (which PRIVACY keeps
    # too), so a KEPT non-removable block makes clean() a byte-for-byte
    # no-op here: the malformed DQT survives exactly as in the input, so
    # whatever a decoder could (or couldn't) make of it is unchanged by
    # cleaning -- checked directly rather than via decode_pixels, since a
    # real decoder (confirmed with Pillow directly, independent of this
    # engine) can in fact choke on this exact fabricated corruption: its
    # own greedy per-table DQT loop reads past the declared length into
    # the next marker's bytes once it sees leftover bytes that don't form
    # another complete table, at which point NOT re-encoding is the only
    # thing keeping the file exactly as decodable (or not) as it started.
    assert dst.read_bytes() == bad

def _pillow_jpeg(mode: str, **save_kwargs) -> bytes:
    """A small Pillow-rendered JPEG using arbitrary `Image.save` kwargs
    (Task 9b/R15: confirm real encoder output -- optimize/progressive/
    restart markers, and CMYK -- still classifies and verifies cleanly)."""
    color = (128, 64, 32) if mode == "RGB" else (10, 20, 30, 40)
    img = Image.new(mode, (32, 32), color)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", **save_kwargs)
    return buf.getvalue()

def test_pillow_optimized_progressive_restart_and_cmyk_are_verified(tmp_path):
    variants = {
        "restart": _pillow_jpeg("RGB", optimize=True, progressive=True, restart_marker_blocks=1),
        "cmyk": _pillow_jpeg("CMYK", quality=90),
    }
    for name, data in variants.items():
        assert not any(b.tag.endswith("?") for b in H.parse(data).blocks)
        src = tmp_path / f"{name}.jpg"; src.write_bytes(data)
        res = siftool.clean(src, tmp_path / f"{name}_clean.jpg")
        assert res.verdict.status is Status.VERIFIED

# ---------------------------------------------------------------------------
# Final-review fix wave. Ruling R19 (C3, I2): one SOF, at least one SOF and
# one SOS, DAC trusted only in an arithmetic frame and only in its standard
# form. Ruling R20 (I1, triage T6): ICC_PROFILE chunks are reassembled across
# the whole file by seq/count and kept only as a valid profile. Review M2:
# every handler-built finding says where it is.
# ---------------------------------------------------------------------------

def _before_eoi(data: bytes, extra: bytes) -> bytes:
    assert data.endswith(b"\xff\xd9")
    return data[:-2] + extra + b"\xff\xd9"

def _segment(marker: int, payload: bytes) -> bytes:
    return bytes([0xFF, marker]) + struct.pack(">H", len(payload) + 2) + payload

def _arithmetic_jpeg() -> bytes:
    """base_jpeg() with its SOF0 relabelled SOF9 (extended sequential, arithmetic):
    structurally an arithmetic-coded frame, which is all DAC classification reads."""
    data = bytearray(base_jpeg())
    start, _ = _first_segment_span(bytes(data), 0xC0)
    data[start + 1] = 0xC9
    return bytes(data)

def test_second_sof_is_corrupt():
    sof = _first_segment_span(base_jpeg(), 0xC0)
    second = base_jpeg()[sof[0]:sof[1]]
    with pytest.raises(CorruptFileError, match="^invalid SOF$"): H.parse(_before_eoi(base_jpeg(), second))

@pytest.mark.parametrize("data", [
    b"\xff\xd8" + _segment(0xE1, b"Exif\x00\x00MM\x00*\x00\x00\x00\x08\x00\x00\x00\x00\x00\x00") + b"\xff\xd9",
    b"\xff\xd8\xff\xd9",
    b"\xff\xd8" + _segment(0xC0, bytes([8, 0, 1, 0, 1, 1, 1, 0x11, 0])) + b"\xff\xd9",
], ids=["app1-only", "soi-eoi", "sof-without-sos"])
def test_jpeg_without_image_data_is_corrupt(data):
    with pytest.raises(CorruptFileError, match="^missing image data$"): H.parse(data)

@pytest.mark.parametrize("payload", [
    b" GPS +23.5505-046.6333 owner=Felipe",
    b"".join(b"\x10" + bytes([c]) for c in b"owner=Felipe"),  # libjpeg-valid (Tc=1, Tb=0) pairs
    b"\x00\x10",
], ids=["raw-text", "valid-pairs", "one-entry"])
def test_dac_in_huffman_frame_is_unused(payload):
    c = H.parse(_insert_after_soi(base_jpeg(), 0xCC, payload))
    [dac] = by_tag(c, "DAC")
    assert (dac.kind, dac.removable, dac.label, dac.findings) == (Kind.UNKNOWN, True, "Unused DAC segment", [])
    assert "DAC" not in tags(H.parse(clean_bytes(_insert_after_soi(base_jpeg(), 0xCC, payload), Mode.MAXIMUM)))

@pytest.mark.parametrize("payload", [b"\x00\x10", b"\x00\x10\x10\x05", bytes(v for tb in range(4) for v in (tb, 0x10, 0x10 | tb, 5))],
                         ids=["dc0", "dc0-ac0", "all-eight"])
def test_standard_dac_in_arithmetic_frame_is_essential(payload):
    for where in ("after-soi", "before-eoi"):
        data = (_insert_after_soi(_arithmetic_jpeg(), 0xCC, payload) if where == "after-soi"
                else _before_eoi(_arithmetic_jpeg(), _segment(0xCC, payload)))
        [dac] = by_tag(H.parse(data), "DAC")
        assert dac.kind is Kind.ESSENTIAL

@pytest.mark.parametrize("payload", [
    b"", b"\x00", b"\x20\x10", b"\x04\x10", b"\x00\x10\x00\x11",
    bytes(v for tb in range(4) for v in (tb, 0x10, 0x10 | tb, 5)) + b"\x00\x10",
    b" GPS +23.5505-046.6333 owner=Felipe",
], ids=["empty", "odd", "tc-2", "tb-4", "duplicate", "nine-entries", "text"])
def test_non_standard_dac_in_arithmetic_frame_is_demoted(payload):
    [dac] = by_tag(H.parse(_insert_after_soi(_arithmetic_jpeg(), 0xCC, payload)), "DAC?")
    assert dac.kind is Kind.UNKNOWN and dac.removable is False
    assert dac.findings == [Finding(C.UNKNOWN, "Non-standard DAC segment", None, "DAC?", len(payload))]

PROFILE = make_icc("Display P3", 4)

def _icc_chunks(parts, seqs=None, count=None):
    seqs = seqs or range(1, len(parts) + 1)
    count = len(parts) if count is None else count
    return [_segment(0xE2, b"ICC_PROFILE\x00" + bytes([s, count]) + p) for s, p in zip(seqs, parts)]

def _thirds(data):
    k = len(data) // 3
    return [data[:k], data[k:2 * k], data[2 * k:]]

def test_icc_profile_split_across_chunks_is_reassembled():
    chunks = _icc_chunks(_thirds(PROFILE))
    for order in ([0, 1, 2], [2, 0, 1]):  # stream order is irrelevant: seq numbers decide
        data = base_jpeg()[:2] + b"".join(chunks[i] for i in order) + base_jpeg()[2:]
        [icc] = by_tag(H.parse(data), "APP2:ICC")
        assert icc.kind is Kind.METADATA and icc.logical == PROFILE
        assert icc.findings == [Finding(C.APPEARANCE, "ICC profile", "Display P3", "APP2:ICC", len(PROFILE))]

def test_icc_chunks_apart_in_the_file_still_form_one_profile():
    first, second = _icc_chunks([PROFILE[:100], PROFILE[100:]])
    data = base_jpeg()[:2] + first + _segment(0xFE, b"between") + second + base_jpeg()[2:]
    a, b = by_tag(H.parse(data), "APP2:ICC")
    assert (a.kind, b.kind) == (Kind.METADATA, Kind.METADATA) and a.logical == PROFILE and b.logical is None
    assert a.findings == [Finding(C.APPEARANCE, "ICC profile", "Display P3", "APP2:ICC", len(PROFILE))]
    assert b.findings == [Finding(C.APPEARANCE, "ICC profile (continued)", None, "APP2:ICC", len(PROFILE) - 100)]

INVALID_ICC = {
    "duplicate-seq": _icc_chunks(_thirds(PROFILE), seqs=[1, 2, 2]),
    "missing-seq": _icc_chunks(_thirds(PROFILE), seqs=[1, 2, 4], count=3),
    "count-mismatch": _icc_chunks(_thirds(PROFILE), count=4),
    "inconsistent-count": _icc_chunks(_thirds(PROFILE))[:2] + _icc_chunks([_thirds(PROFILE)[2]], seqs=[3], count=5),
    "seq-zero": _icc_chunks([PROFILE], seqs=[0], count=0),
    "no-seq-bytes": [_segment(0xE2, b"ICC_PROFILE\x00\x01")],
    "not-a-profile": _icc_chunks([b"G", b"PS +23.55", b"05-046.6333 owner=Felipe"]),
    "identity-text": _icc_chunks([b"owner=Felipe Floriani; phone=+55 11 91234-5678"]),
}

@pytest.mark.parametrize("case", sorted(INVALID_ICC))
def test_invalid_icc_profile_is_dropped(case):
    data = base_jpeg()[:2] + b"".join(INVALID_ICC[case]) + base_jpeg()[2:]
    blocks = by_tag(H.parse(data), "APP2:ICC")
    assert blocks and all((b.kind, b.removable, b.label, b.findings) == (Kind.UNKNOWN, True, "Invalid ICC profile", [])
                          for b in blocks)
    assert clean_bytes(data) == base_jpeg()

def test_every_jpeg_finding_says_where_it_is():  # review M2
    data = make_jpeg(mpf=True, jfif_thumbnail=True, reserved_marker=True, trailing=b"tail")
    data = _insert_after_soi(data, 0xEC, b"Ducky\x00\x01")  # APP12
    data = _insert_after_soi(data, 0xE0, b"JFXX\x00\x13" + bytes(3 * 4))  # JFXX, 2x2 RGB thumbnail
    readers = {"APP1:Exif": "APP1/Exif/", "APP1:XMP": "APP1/XMP", "APP11:JUMBF": "APP11/JUMBF", "APP13:IRB": "APP13/IRB"}
    seen = set()
    for block in H.parse(data).blocks:
        for finding in block.findings:
            expected = readers.get(block.tag, block.tag)
            assert finding.where.startswith(expected) if block.tag in readers else finding.where == expected, finding
            seen.add(block.tag)
    assert {"COM", "TRAILING", "APP2:ICC", "APP0:JFIF", "APP0:JFXX", "APP2:MPF", "APP12", "RESERVED:0xF0"} <= seen

# ---------------------------------------------------------------------------
# Residual round. Ruling R26 (I1): one ICC profile per file -- the first
# complete (seq 1..count), valid profile's ICC_PROFILE chunks are kept and
# every other ICC_PROFILE chunk is dropped. Ruling R25: only the first JFIF
# APP0 is kept. Ruling R24: only the first APP1 Exif is the EXIF block.
# ---------------------------------------------------------------------------

OTHER_PROFILE = make_icc("Felipe Floriani", 4)  # a second, equally valid profile

def _with_segments(*segments):
    base = base_jpeg()
    return base[:2] + b"".join(segments) + base[2:]

def _halves(data):
    return [data[: len(data) // 2], data[len(data) // 2 :]]

PROFILE_BLOCK = (Kind.METADATA, "ICC color profile")
CONTINUED_BLOCK = (Kind.METADATA, "ICC color profile (continued)")
EXTRA_BLOCK = (Kind.UNKNOWN, "Extra ICC profile")

KEEPS_FIRST_PROFILE = {  # case -> (ICC chunks in file order, indices of the chunks kept, APP2:ICC blocks)
    "two-profiles": (_icc_chunks([PROFILE]) + _icc_chunks([OTHER_PROFILE]), [0], [PROFILE_BLOCK, EXTRA_BLOCK]),
    "invalid-then-valid": (_icc_chunks([b"owner=Felipe Floriani"]) + _icc_chunks(_thirds(PROFILE)), [1, 2, 3],
                           [EXTRA_BLOCK, PROFILE_BLOCK]),
    "interleaved": ([c for pair in zip(_icc_chunks(_halves(PROFILE)), _icc_chunks(_halves(OTHER_PROFILE))) for c in pair],
                    [0, 2], [PROFILE_BLOCK, EXTRA_BLOCK, CONTINUED_BLOCK, EXTRA_BLOCK]),
    "stray-chunk-after": (_icc_chunks(_thirds(PROFILE)) + _icc_chunks([b"stray"], count=2), [0, 1, 2],
                          [PROFILE_BLOCK, EXTRA_BLOCK]),
}

@pytest.mark.parametrize("case", sorted(KEEPS_FIRST_PROFILE))
def test_only_the_first_complete_valid_icc_profile_is_kept(case):
    chunks, kept, layout = KEEPS_FIRST_PROFILE[case]
    data = _with_segments(*chunks)
    blocks = by_tag(H.parse(data), "APP2:ICC")
    assert [(b.kind, b.label) for b in blocks] == layout
    [profile_block] = [b for b in blocks if b.label == PROFILE_BLOCK[1]]
    assert profile_block.logical == PROFILE
    assert profile_block.findings == [Finding(C.APPEARANCE, "ICC profile", "Display P3", "APP2:ICC", len(PROFILE))]
    assert all((b.removable, [f.label for f in b.findings]) == (True, ["Extra ICC profile"])
               for b in blocks if b.kind is Kind.UNKNOWN)
    out = clean_bytes(data)
    assert out == _with_segments(*(chunks[i] for i in kept))
    assert clean_bytes(out) == out

@pytest.mark.parametrize("payload", [b"JFIF\x00owner=Fel", b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x02\x01" + bytes(6)],
                         ids=["plain", "with-thumbnail"])
def test_only_the_first_jfif_segment_is_kept(payload):
    base = base_jpeg()
    app0_end = 4 + int.from_bytes(base[4:6], "big")  # right after Pillow's own JFIF APP0
    data = base[:app0_end] + _segment(0xE0, payload) + base[app0_end:]
    first, extra = by_tag(H.parse(data), "APP0:JFIF")
    assert (first.kind, [f.category for f in first.findings]) == (Kind.METADATA, [C.APPEARANCE])
    label = "Extra JFIF segment"
    assert (extra.kind, extra.removable, extra.label, extra.findings) == (
        Kind.UNKNOWN, True, label, [Finding(C.UNKNOWN, label, None, "APP0:JFIF", len(payload))])
    assert clean_bytes(data, Mode.PRIVACY) == base

def test_only_the_first_app1_exif_is_the_exif_block():
    tiffs = [tiff_header() + tiff_ifd([(0x0112, 3, 1, struct.pack(">HH", v, 0))]) for v in (6, 3)]
    segments = [_segment(0xE1, b"Exif\x00\x00" + t) for t in tiffs]
    data = _with_segments(*segments)
    first, extra = by_tag(H.parse(data), "APP1:Exif")
    assert (first.kind, first.rewritable, [f.detail for f in first.findings]) == (Kind.METADATA, True, ["6"])
    label = "Extra EXIF block"
    assert (extra.kind, extra.removable, extra.label, extra.findings) == (
        Kind.UNKNOWN, True, label, [Finding(C.UNKNOWN, label, None, "APP1:Exif", 6 + len(tiffs[1]))])
    assert clean_bytes(data, Mode.PRIVACY) == _with_segments(segments[0])
    base = base_jpeg()
    assert clean_bytes(data, Mode.MAXIMUM) == base[:2] + base[4 + int.from_bytes(base[4:6], "big"):]  # its JFIF goes too
